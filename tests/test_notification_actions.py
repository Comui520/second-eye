from datetime import datetime, timezone

from fastapi.testclient import TestClient

from goodprice.execution.delivery import DeliveryService
from goodprice.main import build_app
from goodprice.models import Notification, NotificationDelivery


def prepare_app(factory, settings, state="pending"):
    from test_delivery_lifecycle import Notifier, prepare
    task, listing, offer, payload = prepare(factory, settings)
    notifier = Notifier()
    delivery = DeliveryService(factory, {"offline": notifier})
    event = delivery.enqueue(task.id, task.version, listing, offer, "web-fixture", payload)
    with factory() as session:
        row = session.query(NotificationDelivery).filter_by(event_id=event).one()
        row.state = state
        history = session.query(Notification).filter_by(event_key="web-fixture").one()
        history.status = state
        session.commit()
    app = build_app(settings, factory, with_scheduler=False)
    app.state.coordinator.pipeline.delivery = delivery
    return TestClient(app), delivery, notifier, row.id, history.id


def test_retry_uncertain_requires_explicit_duplicate_ack_and_only_enqueues(session_factory, base_settings):
    client, delivery, notifier, row, history = prepare_app(session_factory, base_settings, "uncertain")
    url = f"/notifications/deliveries/{row}/retry"
    assert client.post(url, follow_redirects=False).status_code == 422
    assert client.post(url, data={"confirm": "1"}, follow_redirects=False).status_code == 303
    assert notifier.calls == 0
    with session_factory() as session:
        assert session.get(NotificationDelivery, row).state == "retry_requested"
    assert "可能重复" in client.get("/notifications").text or "等待重试" in client.get("/notifications").text


def test_deleting_unsent_history_cancels_delivery_and_does_not_rebind(session_factory, base_settings):
    client, delivery, notifier, row, history = prepare_app(session_factory, base_settings)
    assert client.post(f"/notifications/{history}/delete", follow_redirects=False).status_code == 303
    with session_factory() as session:
        assert session.get(NotificationDelivery, row).state == "cancelled_manual"
        event_id = session.get(NotificationDelivery, row).event_id
    assert delivery.deliver(event_id, datetime.now(timezone.utc)) == "cancelled_manual"
    assert notifier.calls == 0


def test_sending_notification_cannot_be_deleted_as_if_retracted(session_factory, base_settings):
    client, _, _, _, history = prepare_app(session_factory, base_settings, "sending")
    assert client.post(f"/notifications/{history}/delete", follow_redirects=False).status_code == 409
    with session_factory() as session:
        assert session.get(Notification, history)
