"""Serial adapter for offer stages; the coordinator will call the same stages.

Keep page prices unchanged. Only explicit, fresh offer evidence can enter value
analysis and notifications. External fetches happen before observation writes.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from goodprice.models import (
    Listing,
    ListingOffer,
    Notification,
    OfferEvaluationRecord,
    OfferObservation,
    WatchTask,
)
from goodprice.notify.base import NotificationMessage
from goodprice.offers.pricing import decide_price, offer_fingerprint, utc
from goodprice.offers.service import decode_offer
from goodprice.offers.types import OfferCandidate, PriceClaim
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.satisfaction import compute_satisfaction

logger = logging.getLogger(__name__)


class OfferCrawlRunner:
    def __init__(self, crawl):
        self.crawl = crawl
        self.factory = crawl._session_factory
        self.pipeline = OfferPipeline(self.factory, vision_enabled=crawl.vision.enabled)

    def run(self, task_id: int) -> dict:
        now = datetime.now(timezone.utc)
        stats = dict.fromkeys(("found", "new", "notified", "backfilled", "reevaluated", "gone"), 0)
        with self.factory() as session:
            task = session.get(WatchTask, task_id)
            if task is None:
                raise RuntimeError(f"任务 {task_id} 不存在")
            if not task.enabled:
                return {**stats, "skipped": "disabled"}
            task.last_run_at = now
            task.last_run_count = (task.last_run_count or 0) + 1
            task.last_error = None
            session.commit()
        self.crawl._judger = self.crawl._build_judger(self.crawl.settings_service.get())
        self._retry_failed(task, now)
        try:
            items = self.crawl.adapter.search(task.keyword)
        except Exception as exc:
            self.crawl._record_error(task_id, f"抓取失败: {exc}")
            raise
        stats["found"] = len(items)
        pending, seen = [], set()
        for data in items[:30]:
            if self.crawl._excluded(task, data.title):
                continue
            with self.factory() as session:
                listing, is_new = self.crawl._upsert_listing(session, task, data)
                was_missing = listing.status != "active"
                listing.status, listing.missed_count = "active", 0
                seen.add(listing.id)
                blocked = self.crawl._is_blocked(session, listing)
                session.commit()
                listing_id = listing.id
            if blocked:
                continue
            stats["new"] += int(is_new)
            with self.factory() as session:
                previous = session.query(OfferObservation).join(ListingOffer).filter(
                    ListingOffer.listing_id == listing_id).order_by(OfferObservation.id.desc()).first()
                old_fingerprint = previous.fingerprint if previous else None
            previous_evaluations = {o["key"]: o["evaluation"] for o in self.pipeline.latest(listing_id)}
            candidates = self._fetch(listing_id, task)
            now = datetime.now(timezone.utc)
            changed = is_new or was_missing or len(candidates) > 1 or any(
                offer_fingerprint(c) != old_fingerprint for c in candidates)
            # Freshness renews each observation; content identity does not.
            ids = self.pipeline.evaluate_listing(listing_id, task_id, candidates, now)
            with self.factory() as session:
                records = [session.get(OfferEvaluationRecord, i) for i in ids]
                potential = any(r.payload["state"] == "exact" and r.payload["match"] == "matched"
                                and r.payload["amount_cents"] is not None
                                and r.payload["amount_cents"] >= round(task.min_price * 100)
                                and (not task.max_price or r.payload["amount_cents"] <= round(task.max_price * 100))
                                for r in records)
                analysis_price = next((r.payload["amount_cents"] / 100 for r in records
                    if r.payload["state"] == "exact" and r.payload["match"] == "matched"), None)
                for candidate, record in zip(candidates, records, strict=False):
                    old = previous_evaluations.get(candidate.key)
                    if old is None or any(old.get(k) != record.payload.get(k) for k in ("state", "source", "amount_cents", "task_version")):
                        changed = True
                listing = session.get(Listing, listing_id)
                settings = self.crawl.settings_service.get()
                inputs = {"description": listing.description, "images": listing.image_urls,
                          "requirement": task.condition_requirement, "target": task.target_variant,
                          "vision": self.crawl.vision.enabled, "model": settings.llm_model,
                          "vision_model": settings.vision_model, "prompt": "offer-v1",
                          "llm_format": settings.llm_api_format, "vision_format": settings.vision_api_format,
                          "llm_url": settings.llm_base_url, "vision_url": settings.vision_base_url}
                analysis_fp = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
                content_fp = hashlib.sha256(json.dumps(
                    {"description": listing.description, "images": listing.image_urls}, sort_keys=True).encode()).hexdigest()
                unchanged_content = all((previous_evaluations.get(c.key) or {}).get("analysis_content_fingerprint") == content_fp
                                        for c in candidates)
                needs_backfill = (not changed and unchanged_content and self.crawl.vision.enabled
                                  and listing.condition_score is None)
                if any((previous_evaluations.get(c.key) or {}).get("analysis_fingerprint") != analysis_fp
                       for c in candidates):
                    changed = True
                if needs_backfill:
                    changed = False
            if potential and (changed or needs_backfill):
                with self.factory() as session:
                    listing = session.get(Listing, listing_id)
                    if self.crawl._requirement_pass(session, listing, task):
                        if len(candidates) == 1:
                            self.crawl._condition_analysis(session, listing, task, price=analysis_price)
                        else:
                            listing.condition_score = None
                            listing.condition_detail = {"error": "多规格共享媒体，品相待核验"}
                        self.crawl._seller_check(session, listing, task)
                    session.commit()
                ids = self.pipeline.evaluate_listing(listing_id, task_id, candidates, now)
                stats["reevaluated"] += int(not is_new and changed)
                stats["backfilled"] += int(needs_backfill)
            for record_id in ids:
                with self.factory() as session:
                    row = session.get(OfferEvaluationRecord, record_id)
                    row.payload = {**row.payload, "analysis_fingerprint": analysis_fp,
                                   "analysis_content_fingerprint": content_fp}
                    offer = session.get(ListingOffer, row.offer_id)
                    listing = session.get(Listing, listing_id)
                    if row.payload["eligible"] and not changed:
                        old = previous_evaluations.get(offer.offer_key) or {}
                        row.payload = {**row.payload, **{key: old[key] for key in
                            ("value_score", "best_of_batch", "satisfaction") if key in old}}
                        listing.offer_price_cents = row.payload["amount_cents"]
                        listing.offer_verified_until = datetime.fromisoformat(row.payload["fresh_until"])
                        listing.offer_task_version = task.version
                        session.commit()
                    if row.payload["eligible"] and changed:
                        key = listing.external_id if len(candidates) == 1 else f"{listing.external_id}:{offer.offer_key}"
                        pending.append((record_id, {
                            **self.crawl._batch_row(listing, task.condition_requirement),
                            "external_id": key, "offer_id": offer.id,
                            "price": row.payload["amount_cents"] / 100,
                            "title": f"{listing.title} {offer.name} {dict(offer.attributes)}",
                        }))
                    elif not row.payload["eligible"]:
                        listing.value_score, listing.best_of_batch = None, False
                        listing.offer_verified_until = None
                        if task.notify_pending_price and row.payload["state"] != "exact" and row.payload["match"] != "mismatched":
                            self._notify_pending(session, task, listing, offer, row.payload)
                        session.commit()
                    session.commit()
        self._finish(task, pending, now, stats)
        with self.factory() as session:
            for listing in session.query(Listing).filter_by(task_id=task.id):
                if listing.id not in seen:
                    listing.missed_count += 1
                    if listing.missed_count >= 3:
                        listing.status = "unseen"  # A limited search is not proof of delisting.
            session.commit()
        stats["deferred"] = max(0, len(items) - 30)
        return stats

    def _retry_failed(self, task, now):
        notifiers = dict(self.crawl.notifiers)
        with self.factory() as session:
            rows = session.query(Notification).filter_by(task_id=task.id, status="failed").order_by(
                Notification.id.desc()).limit(100).all()
        seen = set()
        for row in rows:
            pair = (row.event_key, row.channel)
            ctx = row.event_context or {}
            if pair in seen or not row.event_key or row.channel not in notifiers or ctx.get("task_version") != task.version:
                continue
            seen.add(pair)
            with self.factory() as session:
                already_sent = session.query(Notification.id).filter_by(
                    event_key=row.event_key, channel=row.channel, status="sent").first()
                attempts = session.query(Notification).filter_by(event_key=row.event_key, channel=row.channel).count()
            if already_sent or attempts >= 3:
                continue
            snapshots = self.pipeline.snapshot(row.listing_id, now)
            current = next((s for s in snapshots if s["id"] == row.offer_id), None)
            if current is None or current["fingerprint"] != ctx.get("fingerprint"):
                continue
            if not ctx.get("pending") and (current["price_state"] != "exact"
                    or current["source"] != ctx.get("source")
                    or current["effective_price_cents"] != ctx.get("amount_cents")):
                continue
            status, detail = "sent", None
            try:
                with self.factory() as session:
                    listing = session.get(Listing, row.listing_id)
                    url = listing.url
                notifiers[row.channel].send(NotificationMessage(row.title, row.content, url))
            except Exception as exc:
                status = "uncertain" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "failed"
                detail = str(exc)[:500]
            with self.factory() as session:
                session.add(Notification(listing_id=row.listing_id, task_id=task.id, offer_id=row.offer_id,
                    event_key=row.event_key, event_context=ctx, channel=row.channel, status=status,
                    title=row.title, content=row.content, detail=detail))
                session.commit()

    def _notify_pending(self, session, task, listing, offer, payload):
        event_key = "pending:" + hashlib.sha256(
            f"{task.id}:{task.version}:{offer.id}:{payload['fingerprint']}".encode()).hexdigest()
        message = NotificationMessage(f"[待询价] {listing.title}",
            f"规格：{offer.name or dict(offer.attributes) or '待核验'}\n{payload['reason']}\n"
            "价格尚未确认，不参与正常性价比排名；请自行联系卖家核实。", listing.url)
        for channel, notifier in self.crawl.notifiers:
            if session.query(Notification).filter_by(event_key=event_key, channel=channel, status="sent").first():
                continue
            status, detail = "sent", None
            try:
                notifier.send(message)
            except Exception as exc:
                status, detail = "failed", str(exc)[:500]
            session.add(Notification(listing_id=listing.id, task_id=task.id, offer_id=offer.id,
                event_key=event_key, channel=channel, status=status, detail=detail,
                event_context={"task_version": task.version, "fingerprint": payload["fingerprint"], "pending": True},
                title=message.title, content=message.content))

    def _fetch(self, listing_id, task):
        detail = None
        if task.fetch_detail:
            with self.factory() as session:
                listing = session.get(Listing, listing_id)
                detail = self.crawl._fetch_detail(session, listing, force=True)
                session.commit()
        if detail and detail.offers:
            return detail.offers
        if detail is None:
            # Failure is not a new observation. Reuse original timestamps so a
            # transient fault neither revokes a fresh quote nor renews stale data.
            with self.factory() as session:
                rows = session.query(OfferObservation).join(ListingOffer).filter(
                    ListingOffer.listing_id == listing_id).order_by(OfferObservation.id.desc()).all()
                cached = {}
                for row in rows:
                    cached.setdefault(row.offer_id, decode_offer(row.payload))
                if cached:
                    return list(cached.values())
        return [OfferCandidate("default", claims=(PriceClaim(
            "dom", "unknown", "详情价格未核验", datetime.now(timezone.utc), "detail:missing"),))]

    def reanalyze(self, listing_id):
        with self.factory() as session:
            listing = session.get(Listing, listing_id)
            if listing is None:
                raise RuntimeError(f"商品 {listing_id} 不存在")
            task = session.get(WatchTask, listing.task_id) if listing.task_id else None
            if task is None:
                raise RuntimeError("原监控任务已删除，无法按旧条件重新分析")
        candidates = self._fetch(listing_id, task)
        now = datetime.now(timezone.utc)
        ids = self.pipeline.evaluate_listing(listing_id, task.id, candidates, now)
        with self.factory() as session:
            listing = session.get(Listing, listing_id)
            potential = any(session.get(OfferEvaluationRecord, i).payload["state"] == "exact" for i in ids)
            analysis_price = next((session.get(OfferEvaluationRecord, i).payload["amount_cents"] / 100
                                  for i in ids if session.get(OfferEvaluationRecord, i).payload["state"] == "exact"), None)
            if potential and self.crawl._requirement_pass(session, listing, task):
                if len(candidates) == 1:
                    self.crawl._condition_analysis(session, listing, task, price=analysis_price)
                else:
                    listing.condition_score = None
                    listing.condition_detail = {"error": "多规格共享媒体，品相待核验"}
                self.crawl._seller_check(session, listing, task)
            if not potential:
                listing.value_score, listing.best_of_batch = None, False
            session.commit()
        ids = self.pipeline.evaluate_listing(listing_id, task.id, candidates, datetime.now(timezone.utc))
        pending = []
        with self.factory() as session:
            listing = session.get(Listing, listing_id)
            for record_id in ids:
                row = session.get(OfferEvaluationRecord, record_id)
                if row.payload["eligible"]:
                    offer = session.get(ListingOffer, row.offer_id)
                    pending.append((record_id, {**self.crawl._batch_row(listing, task.condition_requirement),
                        "external_id": listing.external_id if len(candidates) == 1 else f"{listing.external_id}:{offer.offer_key}",
                        "offer_id": offer.id, "price": row.payload["amount_cents"] / 100}))
        self.crawl._judger = self.crawl._build_judger(self.crawl.settings_service.get())
        self._finish(task, pending, now, {"notified": 0}, notify=False)
        return {"updated": 1}

    def _finish(self, task, pending, now, stats, notify=True):
        result = {"scores": {}, "best": None, "reasons": {}}
        client = self.crawl._value_client()
        judger = self.crawl._judger
        typed = None
        if pending and judger is not None and judger.enabled:
            try:
                typed = judger.analyze_batch_value([r for _, r in pending])
            except Exception as exc:
                logger.warning("规格 Jev 性价比失败，回落原模型: %s", exc)
        if typed is not None:
            result = typed
        elif client and pending:
            try:
                result = client.analyze_batch_value([r for _, r in pending])
            except Exception as exc:
                logger.warning("规格性价比分析失败: %s", exc)
        for record_id, item in pending:
            with self.factory() as session:
                current_task = session.get(WatchTask, task.id)
                if current_task is None or not current_task.enabled or current_task.version != task.version:
                    continue
                record = session.get(OfferEvaluationRecord, record_id)
                listing = session.get(Listing, item["listing_id"])
                offer = session.get(ListingOffer, item["offer_id"])
                if record is None or listing is None or offer is None:
                    continue
                payload = dict(record.payload)
                if payload["fresh_until"] and datetime.fromisoformat(payload["fresh_until"]) <= datetime.now(timezone.utc):
                    continue
                value = result.get("scores", {}).get(item["external_id"])
                best = result.get("best") == item["external_id"]
                first = session.query(OfferObservation).filter_by(offer_id=offer.id).order_by(OfferObservation.id).first()
                first_price = decide_price(decode_offer(first.payload), None, utc(first.observed_at), task.interval_minutes * 60)
                drop = 0.0
                if first_price.source == payload["source"] and first_price.amount_cents:
                    drop = max(0.0, (first_price.amount_cents - payload["amount_cents"]) / first_price.amount_cents)
                score = compute_satisfaction(SimpleNamespace(requirement_match=listing.requirement_match,
                    condition_score=listing.condition_score, value_score=value, seller_risk=listing.seller_risk),
                    vision_enabled=self.crawl.vision.enabled, price_drop_pct=drop)
                payload.update(value_score=value, best_of_batch=best, satisfaction=score)
                record.payload = payload
                listing.value_score, listing.best_of_batch, listing.satisfaction = value, best, score
                listing.offer_price_cents = payload["amount_cents"]
                listing.offer_verified_until = datetime.fromisoformat(payload["fresh_until"])
                listing.offer_task_version = task.version
                if not notify:
                    session.commit()
                    continue
                if offer.last_notified_source == "legacy_baseline":
                    offer.last_notified_score, offer.last_notified_amount = score, payload["amount_cents"]
                    offer.last_notified_source = payload["source"]
                    session.commit()
                    continue
                if offer.last_notified_score is not None and score <= offer.last_notified_score:
                    session.commit()
                    continue
                event_key = hashlib.sha256(f"{task.id}:{task.version}:{offer.id}:{payload['fingerprint']}:{score}".encode()).hexdigest()
                previous_amount = offer.last_notified_amount
                renotify = (previous_amount is not None and offer.last_notified_source == payload["source"]
                            and previous_amount != payload["amount_cents"])
                lines = [f"规格：{offer.name or dict(offer.attributes) or '默认商品'}",
                         f"价格：{payload['amount_cents'] / 100:g} 元（{'人工报价' if payload['source'] == 'manual' else '页面确价'}）",
                         f"需求匹配：{'否' if listing.requirement_match is False else '是' if listing.requirement_match else '未核验'}",
                         f"品相分：{listing.condition_score if listing.condition_score is not None else '未评估'}"
                         + ("（视觉模型未启用）" if not self.crawl.vision.enabled else ""),
                         f"性价比：{str(value) + '/10' if value is not None else '未评估'}"]
                if renotify:
                    lines.append(f"价格更新重推：{previous_amount / 100:g} → {payload['amount_cents'] / 100:g} 元")
                if best:
                    lines.append(f"本批最优（本次已就绪 {len(pending)} 个规格）")
                if listing.seller_risk:
                    risk = listing.seller_risk
                    rate = risk.get("positive_rate")
                    lines.append(f"卖家：{risk.get('nickname', '卖家')} · 风险{risk.get('risk_level', '未知')} "
                                 + (f"好评率 {rate * 100:.0f}%" if isinstance(rate, (int, float)) else ""))
                message = NotificationMessage(f"[{task.keyword}] {listing.title}", "\n".join(lines), listing.url)
                success = False
                for channel, notifier in self.crawl.notifiers:
                    if session.query(Notification).filter_by(event_key=event_key, channel=channel, status="sent").first():
                        success = True
                        continue
                    status, error = "sent", None
                    try:
                        notifier.send(message)
                        success = True
                    except Exception as exc:
                        status = "uncertain" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "failed"
                        error = str(exc)[:500]
                    session.add(Notification(listing_id=listing.id, task_id=task.id, offer_id=offer.id,
                        event_key=event_key, channel=channel, status=status, detail=error,
                        event_context={"task_version": task.version, "fingerprint": payload["fingerprint"],
                                       "source": payload["source"], "amount_cents": payload["amount_cents"]},
                        title=message.title, content=message.content))
                if success:
                    offer.last_notified_score, offer.last_notified_amount = score, payload["amount_cents"]
                    offer.last_notified_source = payload["source"]
                    listing.notified_at, listing.last_notified_satisfaction = now, score
                    stats["notified"] += 1
                session.commit()
