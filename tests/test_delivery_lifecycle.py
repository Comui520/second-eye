from datetime import datetime, timedelta, timezone

import httpx

from goodprice.execution.delivery import DeliveryService
from goodprice.models import Listing, NotificationDelivery
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.task_service import TaskService


class Notifier:
    def __init__(self, failure=None):
        self.calls = 0
        self.failure = failure
    def send(self, message):
        self.calls += 1
        if self.failure:
            raise self.failure


def prepare(factory, settings):
    from test_crawl_service import FakeAdapter, _item, _service
    task = TaskService(factory).create_task({"keyword": "offline"})
    crawl, _, _ = _service(factory, settings, adapter=FakeAdapter([_item()]))
    crawl.run_task(task.id)
    with factory() as session:
        listing = session.query(Listing).one()
    offer = OfferPipeline(factory).snapshot(listing.id, datetime.now(timezone.utc))[0]
    payload = {"title": "fixture", "content": "fixture", "url": listing.url,
        "fingerprint": offer["fingerprint"], "source": offer["source"],
        "amount_cents": offer["effective_price_cents"], "condition_score": listing.condition_score,
        "media_cache_key": (listing.condition_detail or {}).get("media_cache_key")}
    return task, listing.id, offer["id"], payload


def test_event_survives_run_completion_and_successful_channels_never_resend(session_factory, base_settings):
    task, listing, offer, payload = prepare(session_factory, base_settings)
    ok, flaky = Notifier(), Notifier(httpx.ConnectError("connection not established"))
    service = DeliveryService(session_factory, {"ok": ok, "flaky": flaky})
    event = service.enqueue(task.id, task.version, listing, offer, "same-business-event", payload)
    assert event == service.enqueue(task.id, task.version, listing, offer, "same-business-event", payload)
    now = datetime.now(timezone.utc)
    assert service.deliver(event, now) == "failed"
    assert ok.calls == flaky.calls == 1
    flaky.failure = None
    assert service.deliver(event, now + timedelta(seconds=20)) == "sent"
    assert ok.calls == 1 and flaky.calls == 2


def test_uncertain_timeout_never_automatically_resends(session_factory, base_settings):
    task, listing, offer, payload = prepare(session_factory, base_settings)
    notifier = Notifier(httpx.ReadTimeout("private-provider-url"))
    service = DeliveryService(session_factory, {"offline": notifier})
    event = service.enqueue(task.id, task.version, listing, offer, "timeout", payload)
    now = datetime.now(timezone.utc)
    assert service.deliver(event, now) == "uncertain"
    notifier.failure = None
    assert service.deliver(event, now + timedelta(seconds=60)) == "uncertain"
    assert notifier.calls == 1
    with session_factory() as session:
        delivery = session.query(NotificationDelivery).filter_by(event_id=event).one()
    assert service.retry(delivery.id, now + timedelta(seconds=61)) == "sent"
    assert notifier.calls == 2


def test_pause_or_condition_change_cancels_unsent_event(session_factory, base_settings):
    task, listing, offer, payload = prepare(session_factory, base_settings)
    notifier = Notifier()
    service = DeliveryService(session_factory, {"offline": notifier})
    event = service.enqueue(task.id, task.version, listing, offer, "cancel", payload)
    TaskService(session_factory).toggle_task(task.id)
    assert service.deliver(event, datetime.now(timezone.utc)) == "cancelled"
    assert notifier.calls == 0


def test_expired_evidence_waits_and_changed_media_supersedes(session_factory, base_settings):
    task, listing, offer, payload = prepare(session_factory, base_settings)
    notifier = Notifier()
    service = DeliveryService(session_factory, {"offline": notifier})
    event = service.enqueue(task.id, task.version, listing, offer, "expiry", payload)
    now = datetime.now(timezone.utc)
    assert service.deliver(event, now + timedelta(hours=1)) == "awaiting_revalidation"
    assert notifier.calls == 0
    with session_factory() as session:
        session.get(Listing, listing).condition_score = 2
        session.commit()
    assert service.deliver(event, now) == "superseded"
    assert notifier.calls == 0


def test_recover_interrupted_send_is_uncertain_not_pending(session_factory, base_settings):
    task, listing, offer, payload = prepare(session_factory, base_settings)
    notifier = Notifier()
    service = DeliveryService(session_factory, {"offline": notifier})
    event = service.enqueue(task.id, task.version, listing, offer, "crash", payload)
    with session_factory() as session:
        row = session.query(NotificationDelivery).filter_by(event_id=event).one()
        row.state = "sending"
        row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    service.recover(datetime.now(timezone.utc))
    assert service.deliver(event, datetime.now(timezone.utc)) == "uncertain"
    assert notifier.calls == 0
