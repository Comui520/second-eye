"""A durable per-channel outbox. External delivery cannot promise exactly-once."""
import time
import uuid
from contextlib import nullcontext
from datetime import timedelta

import httpx

from goodprice.execution.store import ExecutionStore, naive
from goodprice.models import (
    Listing,
    ListingOffer,
    Notification,
    NotificationDelivery,
    NotificationEvent,
    WatchTask,
)
from goodprice.notify.base import DeliveryRejected, NotificationMessage
from goodprice.services.offer_pipeline import OfferPipeline

SENDABLE = ("pending", "failed", "awaiting_revalidation", "retry_requested")


class DeliveryService:
    def __init__(self, session_factory, notifiers, vision_enabled=True):
        self.factory = session_factory
        self.notifiers = notifiers
        self.lock = ExecutionStore(session_factory).lock
        self.vision_enabled = vision_enabled

    def enqueue(self, task_id, task_version, listing_id, offer_id, business_key, payload, *, session=None):
        if not business_key or len(business_key) > 128:
            raise ValueError("Invalid notification event key")
        own = session is None
        with self.lock, self.factory() if own else nullcontext(session) as session:
            listing = session.get(Listing, listing_id)
            offer = session.get(ListingOffer, offer_id)
            if listing and offer:
                payload = {**payload, "product_identity": [listing.platform, listing.external_id, offer.offer_key]}
            existing = session.query(NotificationEvent).filter_by(business_key=business_key).first()
            if existing:
                rows = session.query(NotificationDelivery).filter_by(event_id=existing.id).all()
                resumable = [r for r in rows if r.state in {"cancelled", "superseded", "awaiting_revalidation", "pending", "failed"}]
                if resumable:
                    existing.task_version, existing.payload = task_version, payload
                    existing.state = "pending"
                    for row in resumable:
                        if row.state in {"cancelled", "superseded", "awaiting_revalidation"} and row.attempt < 3:
                            row.state, row.not_before = "pending", None
                            self._history(session, existing, row, "pending")
                if own:
                    session.commit()
                return existing.id
            event = NotificationEvent(task_id=task_id, task_version=task_version, listing_id=listing_id,
                offer_id=offer_id, business_key=business_key, payload=payload)
            session.add(event)
            session.flush()
            notifiers = self.notifiers() if callable(self.notifiers) else self.notifiers
            for channel in notifiers:
                prior_sent = session.query(Notification).filter_by(event_key=business_key, channel=channel, status="sent").first()
                row = NotificationDelivery(event_id=event.id, channel=channel,
                                            state="sent" if prior_sent else "pending")
                session.add(row)
                session.flush()
                if not prior_sent:
                    session.add(Notification(task_id=task_id, listing_id=listing_id, offer_id=offer_id,
                        event_key=business_key, channel=channel, status="pending", title=payload["title"],
                        content=payload["content"], event_context={"delivery_id": row.id, "task_version": task_version}))
            if own:
                session.commit()
            return event.id

    def _validate(self, event, now):
        with self.factory() as session:
            task = session.get(WatchTask, event.task_id)
            listing = session.get(Listing, event.listing_id)
            if task is None or not task.enabled or task.version != event.task_version or listing is None or listing.blocked or listing.task_id != event.task_id:
                return "cancelled"
            identity = event.payload.get("product_identity")
            if identity and identity[:2] != [listing.platform, listing.external_id]:
                return "cancelled"
            score = listing.condition_score
            media_key = (listing.condition_detail or {}).get("media_cache_key")
        try:
            vision = self.vision_enabled() if callable(self.vision_enabled) else self.vision_enabled
            offers = OfferPipeline(self.factory, vision_enabled=vision).snapshot(event.listing_id, now)
        except LookupError:
            return "cancelled"
        current = next((o for o in offers if o["id"] == event.offer_id), None)
        if current is None:
            return "cancelled"
        if identity and identity[2] != current["key"]:
            return "cancelled"
        payload = event.payload
        if not current.get("scope_current", True):
            return "awaiting_revalidation"
        if payload.get("offer_evaluation"):
            evaluation = current.get("evaluation") or {}
            if evaluation.get("status", "ready") not in {"ready", "completed"}:
                return "awaiting_revalidation"
            score = evaluation.get("condition_score")
            media_key = evaluation.get("media_cache_key")
        if (current["fingerprint"] != payload["fingerprint"] or score != payload.get("condition_score")
                or media_key != payload.get("media_cache_key")):
            return "superseded"
        if payload.get("pending"):
            if not current.get("observation_fresh"):
                return "awaiting_revalidation"
            return "valid" if current["price_state"] != "exact" else "superseded"
        if current["price_state"] != "exact":
            return "awaiting_revalidation"
        if current["source"] != payload["source"] or current["effective_price_cents"] != payload["amount_cents"]:
            return "superseded"
        if not current.get("evaluation", {}).get("eligible"):
            return "superseded"
        return "valid"

    def _history(self, session, event, row, state, detail=None):
        history = session.query(Notification).filter_by(event_key=event.business_key,
            channel=row.channel).order_by(Notification.id.desc()).first()
        if history:
            history.status, history.detail = state, detail

    def _invalidate(self, event_id, state, now=None):
        with self.lock, self.factory() as session:
            event = session.get(NotificationEvent, event_id)
            event.state = state
            for row in session.query(NotificationDelivery).filter(NotificationDelivery.event_id == event_id,
                    NotificationDelivery.state.in_(SENDABLE)):
                row.state = state
                if state == "awaiting_revalidation" and now:
                    row.not_before = naive(now) + timedelta(seconds=30)
                self._history(session, event, row, state)
            session.commit()
        return state

    def deliver(self, event_id, now):
        began = time.monotonic()
        with self.factory() as session:
            event = session.get(NotificationEvent, event_id)
            if event is None:
                return "cancelled"
            deliveries = session.query(NotificationDelivery).filter_by(event_id=event_id).order_by(NotificationDelivery.id).all()
        if any(row.state in SENDABLE for row in deliveries):
            valid = self._validate(event, now)
            if valid != "valid":
                return self._invalidate(event.id, valid, now)
        for original in deliveries:
            if original.state not in SENDABLE or (original.not_before and original.not_before > naive(now)):
                continue
            check_time = now + timedelta(seconds=time.monotonic() - began)
            valid = self._validate(event, check_time)
            if valid != "valid":
                return self._invalidate(event.id, valid, check_time)
            with self.lock, self.factory() as session:
                row = session.get(NotificationDelivery, original.id)
                # Last short write-gate before crossing the external boundary.
                task = session.get(WatchTask, event.task_id)
                if task is None or not task.enabled or task.version != event.task_version:
                    return self._invalidate(event.id, "cancelled")
                if row.state not in SENDABLE or (row.attempt >= 3 and row.state != "retry_requested"):
                    continue
                lease = uuid.uuid4().hex
                row.state, row.lease_id = "sending", lease
                row.lease_until = naive(check_time) + timedelta(seconds=120)
                row.attempt += 1
                session.commit()
            status, detail = "sent", None
            notifiers = self.notifiers() if callable(self.notifiers) else self.notifiers
            notifier = notifiers.get(original.channel)
            try:
                if notifier is None:
                    raise DeliveryRejected("channel unavailable")
                notifier.send(NotificationMessage(event.payload["title"], event.payload["content"], event.payload["url"]))
            except (DeliveryRejected, httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                status, detail = "failed", "渠道不可用或投递失败，未记录响应中的敏感信息"
            except httpx.HTTPStatusError as exc:
                status = "failed" if 400 <= exc.response.status_code < 500 and exc.response.status_code != 408 else "uncertain"
                detail = "渠道明确拒绝请求" if status == "failed" else "服务端投递结果未知，请核验后人工重试（可能重复）"
            except Exception:
                status, detail = "uncertain", "投递结果未知，请核验后人工重试（可能重复）"
            with self.lock, self.factory() as session:
                row = session.get(NotificationDelivery, original.id)
                if row.lease_id != lease:
                    # Recovery has already classified this interrupted send.
                    continue
                row.state, row.lease_id, row.lease_until = status, None, None
                row.not_before = naive(now) + timedelta(seconds=min(300, 5 * 2**row.attempt))
                self._history(session, event, row, status, detail)
                if status == "sent":
                    listing = session.get(Listing, event.listing_id)
                    if listing:
                        listing.notified_at = naive(check_time)
                    offer = session.get(ListingOffer, event.offer_id)
                    if offer and not event.payload.get("pending"):
                        offer.last_notified_score = event.payload.get("satisfaction", offer.last_notified_score)
                        offer.last_notified_amount, offer.last_notified_source = event.payload["amount_cents"], event.payload["source"]
                        offer.last_notified_task_version = event.task_version
                session.commit()
        with self.lock, self.factory() as session:
            states = {r.state for r in session.query(NotificationDelivery).filter_by(event_id=event_id)}
            state = next((s for s in ["uncertain", "sending", "failed", "awaiting_revalidation", "pending", "cancelled_manual", "cancelled", "superseded"] if s in states), "sent")
            session.get(NotificationEvent, event_id).state = state
            session.commit()
            return state

    def queue_retry(self, delivery_id, now):
        with self.factory() as session:
            row = session.get(NotificationDelivery, delivery_id)
            if row is None:
                return "cancelled"
            event = session.get(NotificationEvent, row.event_id)
            if row.state in {"sent", "sending", "cancelled_manual"}:
                return row.state
        valid = self._validate(event, now)
        if valid != "valid":
            return self._invalidate(event.id, valid, now)
        with self.lock, self.factory() as session:
            row = session.get(NotificationDelivery, delivery_id)
            if row.state not in {"sent", "sending", "cancelled_manual"}:
                row.state, row.not_before = "retry_requested", None
                session.commit()
        return "pending"

    def retry(self, delivery_id, now):
        status = self.queue_retry(delivery_id, now)
        if status != "pending":
            return status
        with self.factory() as session:
            event_id = session.get(NotificationDelivery, delivery_id).event_id
        return self.deliver(event_id, now)

    def recover(self, now, *, startup=False):
        with self.lock, self.factory() as session:
            rows = session.query(NotificationDelivery).filter_by(state="sending")
            if not startup:
                rows = rows.filter(NotificationDelivery.lease_until <= naive(now))
            for row in rows:
                row.state, row.lease_id = "uncertain", None
                event = session.get(NotificationEvent, row.event_id)
                self._history(session, event, row, "uncertain", "进程中断，投递结果未知；不会自动重发")
            session.commit()
