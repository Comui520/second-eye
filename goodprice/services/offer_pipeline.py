"""Offer decisions shared by serial execution and the future stage coordinator."""
from dataclasses import asdict
from datetime import datetime

from goodprice.models import (
    Listing,
    ListingOffer,
    ManualQuote,
    OfferEvaluationRecord,
    OfferObservation,
    WatchTask,
)
from goodprice.offers.matching import match_variant
from goodprice.offers.pricing import decide_price, utc
from goodprice.offers.service import OfferService
from goodprice.offers.types import OfferCandidate, QuoteInput


class StaleEvaluation(RuntimeError):
    pass


class OfferPipeline:
    def __init__(self, session_factory, vision_enabled: bool = True):
        self._session_factory = session_factory
        self.vision_enabled = vision_enabled
        self.offers = OfferService(session_factory)

    def evaluate_listing(self, listing_id: int, task_id: int,
                         candidates: list[OfferCandidate], now: datetime, *, expected_version=None) -> list[int]:
        # Network/AI callers must finish their work before this short write phase.
        result = []
        with self._session_factory() as session:
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
            ids = [self.offers.record_observation(listing_id, o, session=session) for o in candidates[:20]]
            for candidate, offer_id in zip(candidates, ids, strict=False):
                quote = session.query(ManualQuote).filter_by(offer_id=offer_id, revoked=False).order_by(
                    ManualQuote.id.desc()).first()
                value = None if quote is None else QuoteInput(candidate.key, quote.amount_cents,
                    utc(quote.confirmed_at), utc(quote.expires_at), quote.evidence_fingerprint, quote.revoked)
                decision = decide_price(candidate, value, now, task.interval_minutes * 60)
                match = match_variant(task.target_variant, candidate.attributes, listing.title)
                eligible = (match == "matched" and decision.state == "exact"
                            and candidate.availability != "unavailable")
                reason = decision.reason if not eligible else ""
                if decision.amount_cents is not None:
                    amount = decision.amount_cents
                    if amount < round(task.min_price * 100) or (task.max_price and amount > round(task.max_price * 100)):
                        eligible, reason = False, "该规格价格不在预算内"
                if match != "matched":
                    eligible, reason = False, "目标规格不匹配" if match == "mismatched" else "规格待核验"
                if task.min_condition_score and (not self.vision_enabled or len(candidates) != 1 or listing.condition_score is None
                                                 or listing.condition_score < task.min_condition_score):
                    eligible, reason = False, "品相未核验或低于门槛"
                if listing.requirement_match is False or listing.blocked:
                    eligible, reason = False, "需求不匹配或商品已拉黑"
                payload = {**asdict(decision), "match": match, "eligible": eligible, "reason": reason}
                payload["fresh_until"] = decision.fresh_until.isoformat() if decision.fresh_until else None
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
            session.commit()
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
                item["target_match"] = match_variant(task.target_variant, candidate.attributes, listing.title) if task else "uncertain"
                value = None if quote is None else QuoteInput(candidate.key, quote.amount_cents,
                    utc(quote.confirmed_at), utc(quote.expires_at), quote.evidence_fingerprint, quote.revoked)
                ttl = (task.interval_minutes if task else 20) * 60
                decision = decide_price(candidate, value, now, 0 if task and not task.fetch_detail else ttl)
                item.update(fingerprint=observation.fingerprint, price_state=decision.state,
                    effective_price_cents=decision.amount_cents, source=decision.source,
                    fresh_until=decision.fresh_until.isoformat() if decision.fresh_until else None,
                    reason=decision.reason, quote_id=quote.id if quote else None)
                evaluation = dict(record.payload or {}) if record else {}
                valid = (task is not None and task.enabled and not listing.blocked
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
        if not eligible:
            view.value_score, view.best_of_batch, view.satisfaction = None, False, 0.0
        result.append(view)
    return result
