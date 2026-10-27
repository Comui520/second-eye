"""Offer decisions shared by serial execution and the future stage coordinator."""
from contextlib import nullcontext
from datetime import datetime, timedelta

from goodprice.models import (
    Listing,
    ListingOffer,
    ManualQuote,
    OfferEvaluationRecord,
    OfferObservation,
    WatchTask,
)
from goodprice.offers.evaluation import assess_offer
from goodprice.offers.matching import match_variant
from goodprice.offers.pricing import decide_price, utc
from goodprice.offers.service import OfferService
from goodprice.offers.types import OfferCandidate, PriceDecision, QuoteInput


class StaleEvaluation(RuntimeError):
    pass


class OfferPipeline:
    def __init__(self, session_factory, vision_enabled: bool = True):
        self._session_factory = session_factory
        self.vision_enabled = vision_enabled
        self.offers = OfferService(session_factory)

    def evaluate_listing(self, listing_id: int, task_id: int,
                         candidates: list[OfferCandidate], now: datetime, *, expected_version=None,
                         session=None, per_offer_results=None, ready=True,
                         record_observations=True) -> list[int]:
        # Network/AI callers must finish their work before this short write phase.
        result = []
        own = session is None
        with self._session_factory() if own else nullcontext(session) as session:
            listing, task = session.get(Listing, listing_id), session.get(WatchTask, task_id)
            if listing is None or task is None or listing.task_id != task.id:
                raise LookupError("任务或商品不存在／关联不一致")
            if expected_version is not None:
                from sqlalchemy import update
                gate = session.execute(update(WatchTask).where(WatchTask.id == task_id,
                    WatchTask.version == expected_version, WatchTask.enabled.is_(True)).values(
                        version=WatchTask.version).execution_options(synchronize_session=False))
                if gate.rowcount != 1:
                    raise StaleEvaluation("任务已暂停或条件已变化")
            if record_observations:
                ids = [self.offers.record_observation(listing_id, o, session=session, now=now) for o in candidates[:20]]
            else:
                existing = {o.offer_key: o.id for o in session.query(ListingOffer).filter_by(listing_id=listing_id)}
                ids = [existing[o.key] for o in candidates[:20]]
            for candidate, offer_id in zip(candidates, ids, strict=False):
                quote = session.query(ManualQuote).filter_by(offer_id=offer_id, revoked=False).order_by(
                    ManualQuote.id.desc()).first()
                value = None if quote is None else QuoteInput(candidate.key, quote.amount_cents,
                    utc(quote.confirmed_at), utc(quote.expires_at), quote.evidence_fingerprint, quote.revoked)
                scoped = (per_offer_results or {}).get(candidate.key, {})
                condition_score = scoped.get("condition_score") if per_offer_results is not None else listing.condition_score
                visual_valid = (scoped.get("visual_scope") == candidate.key if per_offer_results is not None else len(candidates) == 1)
                requirement_match = scoped.get("requirement_match") if per_offer_results is not None else listing.requirement_match
                payload = assess_offer(task, listing, candidate, value, now, vision_enabled=self.vision_enabled,
                    condition_score=condition_score, requirement_match=requirement_match,
                    visual_valid=visual_valid, ready=ready)
                latest = session.query(OfferObservation).filter_by(offer_id=offer_id).order_by(
                    OfferObservation.id.desc()).first()
                payload["fingerprint"] = latest.fingerprint
                payload["observation_id"] = latest.id
                payload["task_version"] = task.version
                row = OfferEvaluationRecord(offer_id=offer_id, task_version=task.version,
                                            payload=payload, evaluated_at=now)
                session.add(row)
                session.flush()
                result.append(row.id)
            if own:
                session.commit()
            else:
                session.flush()
        return result

    def latest(self, listing_id: int) -> list[dict]:
        with self._session_factory() as session:
            result = []
            for offer in session.query(ListingOffer).filter_by(listing_id=listing_id).order_by(ListingOffer.id):
                row = session.query(OfferEvaluationRecord).filter_by(offer_id=offer.id).order_by(
                    OfferEvaluationRecord.id.desc()).first()
                result.append({"id": offer.id, "key": offer.offer_key, "name": offer.name,
                               "attributes": offer.attributes, "availability": offer.availability,
                               "evaluation": row.payload if row else None})
            return result

    def snapshot(self, listing_id: int, now: datetime) -> list[dict]:
        result = self.snapshot_many([listing_id], now)
        if listing_id not in result:
            raise LookupError("商品不存在")
        return result[listing_id]

    def snapshot_many(self, listing_ids, now: datetime) -> dict[int, list[dict]]:
        """One bounded set of latest-row queries for the whole visible page."""
        from sqlalchemy import func

        from goodprice.offers.service import decode_offer

        if not listing_ids:
            return {}
        with self._session_factory() as session:
            listings = {row.id: row for row in session.query(Listing).filter(Listing.id.in_(listing_ids))}
            task_ids = {row.task_id for row in listings.values() if row.task_id}
            tasks = {row.id: row for row in session.query(WatchTask).filter(WatchTask.id.in_(task_ids))} if task_ids else {}
            offers = session.query(ListingOffer).filter(ListingOffer.listing_id.in_(listing_ids)).order_by(ListingOffer.id).all()
            offer_ids = [row.id for row in offers]
            result = {key: [] for key in listings}
            if not offer_ids:
                return result
            def latest(model, active=False):
                ids = session.query(func.max(model.id)).filter(model.offer_id.in_(offer_ids))
                if active:
                    ids = ids.filter(model.revoked.is_(False))
                ids = ids.group_by(model.offer_id)
                return {row.offer_id: row for row in session.query(model).filter(model.id.in_(ids))}
            observations = latest(OfferObservation)
            evaluations = latest(OfferEvaluationRecord)
            quotes = latest(ManualQuote, active=True)
            for offer in offers:
                listing = listings[offer.listing_id]
                task = tasks.get(listing.task_id)
                observation, quote, record = observations.get(offer.id), quotes.get(offer.id), evaluations.get(offer.id)
                item = {"id": offer.id, "key": offer.offer_key, "name": offer.name,
                        "attributes": offer.attributes, "availability": offer.availability}
                if observation is None:
                    item.update(price_state="unknown", effective_price_cents=None, source=None,
                                fingerprint="", reason="缺少价格证据", quote_id=None,
                                evaluation={"eligible": False})
                    result[listing.id].append(item)
                    continue
                candidate = decode_offer(observation.payload)
                scoped_claims = [c for c in candidate.claims if c.scope_key in (None, "", candidate.key)]
                expiry = min((utc(c.observed_at) for c in scoped_claims), default=utc(now)) + timedelta(minutes=task.interval_minutes if task else 20)
                item["observation_fresh"] = bool(scoped_claims and expiry > utc(now) and
                    all(utc(c.observed_at) <= utc(now) + timedelta(seconds=5) for c in scoped_claims))
                item["target_match"] = match_variant(task.target_variant, candidate.attributes, listing.title) if task else "uncertain"
                value = None if quote is None else QuoteInput(candidate.key, quote.amount_cents,
                    utc(quote.confirmed_at), utc(quote.expires_at), quote.evidence_fingerprint, quote.revoked)
                ttl = (task.interval_minutes if task else 20) * 60
                decision = decide_price(candidate, value, now, 0 if task and not task.fetch_detail else ttl)
                item["scope_current"] = offer.seen_in_latest_detail
                item["observed_price_cents"] = decision.amount_cents
                if not offer.seen_in_latest_detail:
                    decision = PriceDecision("unknown", None, None, (), "本次详情未核验此规格，旧观察仅供参考", None)
                item.update(fingerprint=observation.fingerprint, price_state=decision.state,
                    effective_price_cents=decision.amount_cents, source=decision.source,
                    fresh_until=decision.fresh_until.isoformat() if decision.fresh_until else None,
                    reason=decision.reason, quote_id=quote.id if quote else None)
                item.update(quote_confirmed_at=utc(quote.confirmed_at).isoformat() if quote else None,
                            quote_expires_at=utc(quote.expires_at).isoformat() if quote else None)
                evaluation = dict(record.payload or {}) if record else {}
                item["evaluation_current"] = (offer.seen_in_latest_detail and task is not None and evaluation.get("task_version") == task.version
                    and evaluation.get("fingerprint") == observation.fingerprint)
                valid = (offer.seen_in_latest_detail and task is not None and task.enabled and not listing.blocked
                    and decision.state == "exact" and evaluation.get("task_version") == task.version
                    and evaluation.get("fingerprint") == observation.fingerprint
                    and evaluation.get("source") == decision.source
                    and evaluation.get("amount_cents") == decision.amount_cents
                    and (not task.min_condition_score or self.vision_enabled))
                if not valid:
                    evaluation.update(eligible=False, value_score=None, best_of_batch=False, satisfaction=None)
                item["evaluation"] = evaluation
                result[listing.id].append(item)
            return result


def present_listings(listings, factory, vision_enabled=True):
    from types import SimpleNamespace

    pipeline = OfferPipeline(factory, vision_enabled=vision_enabled)
    result = []
    snapshots = pipeline.snapshot_many([listing.id for listing in listings], datetime.now().astimezone())
    for listing in listings:
        view = SimpleNamespace(**{col.name: getattr(listing, col.name) for col in Listing.__table__.columns})
        view.image_urls = [f"/media/legacy-listing/{listing.id}/image/{i}" for i, _ in enumerate((listing.image_urls or [])[:32])]
        offers = snapshots.get(listing.id, [])
        eligible = [o for o in offers if (o.get("evaluation") or {}).get("eligible")]
        exact = [o for o in offers if o["price_state"] == "exact"]
        matched_exact = [o for o in exact if o.get("target_match") == "matched"]
        cheapest = min(matched_exact, key=lambda o: o["effective_price_cents"], default=None)
        view.price_confirmed = bool(eligible)
        view.price_state = "exact" if exact else "pending"
        view.exact_offer_count = len(matched_exact)
        view.effective_price_cents = cheapest["effective_price_cents"] if cheapest else None
        view.effective_price_source = cheapest["source"] if cheapest else None
        current_media = [o for o in offers if o.get("evaluation_current") and o.get("target_match") == "matched"]
        if len(current_media) == 1:
            assessment = current_media[0].get("evaluation") or {}
            view.condition_score = assessment.get("condition_score")
            view.requirement_match = assessment.get("requirement_match")
            if assessment.get("media") is not None:
                view.condition_detail = assessment["media"]
        elif offers:
            view.condition_score = None
            view.condition_detail = {"reason": "请查看当前规格的媒体结果，历史分析不代表新条件"}
        if not eligible:
            view.value_score, view.best_of_batch, view.satisfaction = None, False, 0.0
        elif any((o.get("evaluation") or {}).get("status") == "ready" for o in eligible):
            chosen = max((o["evaluation"] for o in eligible), key=lambda e: e.get("satisfaction") or 0)
            view.value_score, view.satisfaction = chosen.get("value_score"), chosen.get("satisfaction") or 0
            view.best_of_batch = any(o["evaluation"].get("best_of_batch", False) for o in eligible)
        result.append(view)
    return result
