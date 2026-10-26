"""A durable per-channel outbox. External delivery cannot promise exactly-once."""
import time
import uuid
from contextlib import nullcontext
from datetime import timedelta

import httpx

from goodprice.execution.store import ExecutionStore, naive
from goodprice.models import (
    Listing,
    Notification,
    NotificationDelivery,
    NotificationEvent,
    WatchTask,
)
from goodprice.notify.base import NotificationMessage
from goodprice.services.offer_pipeline import OfferPipeline

SENDABLE = ("pending", "failed", "awaiting_revalidation", "retry_requested")


class DeliveryService:
    def __init__(self, session_factory, notifiers):
        self.factory = session_factory
        self.notifiers = notifiers
        self.lock = ExecutionStore(session_factory).lock

    def enqueue(self, task_id, task_version, listing_id, offer_id, business_key, payload, *, session=None):
        if not business_key or len(business_key) > 128:
            raise ValueError("Invalid notification event key")
        own = session is None
        with self.lock, self.factory() if own else nullcontext(session) as session:
            existing = session.query(NotificationEvent).filter_by(business_key=business_key).first()
            if existing:
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
            if task is None or not task.enabled or task.version != event.task_version or listing is None or listing.blocked:
                return "cancelled"
            score = listing.condition_score
            media_key = (listing.condition_detail or {}).get("media_cache_key")
        try:
            offers = OfferPipeline(self.factory).snapshot(event.listing_id, now)
        except LookupError:
            return "cancelled"
        current = next((o for o in offers if o["id"] == event.offer_id), None)
        if current is None:
            return "cancelled"
        payload = event.payload
        if (current["fingerprint"] != payload["fingerprint"] or score != payload.get("condition_score")
                or media_key != payload.get("media_cache_key")):
            return "superseded"
        if payload.get("pending"):
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

    def _invalidate(self, event_id, state):
        with self.lock, self.factory() as session:
            event = session.get(NotificationEvent, event_id)
            event.state = state
            for row in session.query(NotificationDelivery).filter(NotificationDelivery.event_id == event_id,
                    NotificationDelivery.state.in_(SENDABLE)):
                row.state = state
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
        for original in deliveries:
            if original.state not in SENDABLE or (original.not_before and original.not_before > naive(now)):
                continue
            check_time = now + timedelta(seconds=time.monotonic() - began)
            valid = self._validate(event, check_time)
            if valid != "valid":
                return self._invalidate(event.id, valid)
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
                    raise RuntimeError("channel unavailable")
                notifier.send(NotificationMessage(event.payload["title"], event.payload["content"], event.payload["url"]))
            except (TimeoutError, httpx.TimeoutException):
                status, detail = "uncertain", "投递结果未知，请核验后人工重试（可能重复）"
            except Exception:
                status, detail = "failed", "渠道不可用或投递失败，未记录响应中的敏感信息"
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
                session.commit()
        with self.lock, self.factory() as session:
            states = {r.state for r in session.query(NotificationDelivery).filter_by(event_id=event_id)}
            state = next((s for s in ["uncertain", "sending", "failed", "awaiting_revalidation", "pending", "cancelled", "superseded"] if s in states), "sent")
            session.get(NotificationEvent, event_id).state = state
            session.commit()
            return state

    def queue_retry(self, delivery_id, now):
        with self.factory() as session:
            row = session.get(NotificationDelivery, delivery_id)
            if row is None:
                return "cancelled"
            event = session.get(NotificationEvent, row.event_id)
            if row.state in {"sent", "sending"}:
                return row.state
        valid = self._validate(event, now)
        if valid != "valid":
            return self._invalidate(event.id, valid)
        with self.lock, self.factory() as session:
            row = session.get(NotificationDelivery, delivery_id)
            if row.state not in {"sent", "sending"}:
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
