from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from test_production_pipeline import Adapter, Provider, pump

from goodprice.execution.pipeline import build_pipeline
from goodprice.execution.types import Stage
from goodprice.models import Listing, OfferObservation
from goodprice.offers.service import OfferService
from goodprice.offers.types import QuoteInput
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService


def test_quote_changed_after_value_application_before_aggregation_recomputes(session_factory, base_settings):
    class Pricing(Provider):
        def value(self, items, settings, fallback=False):
            self.value_calls += 1
            return {"scores": {i["external_id"]: 9 if i["price"] == 100 else 2 for i in items}, "best": items[0]["external_id"]}
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    adapter, provider, current = Adapter(), Pricing(), [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    changed = []
    original = controller.workers.handlers[Stage.AGGREGATE]
    def between_value_and_aggregate(item):
        if not changed:
            with session_factory() as session:
                obs = session.query(OfferObservation).order_by(OfferObservation.id.desc()).first()
            now = datetime.now(timezone.utc)
            OfferService(session_factory).confirm_quote(obs.offer_id, QuoteInput("default", 20000, now,
                now+timedelta(days=7), obs.fingerprint))
            changed.append(True)
        return original(item)
    controller.workers.handlers[Stage.AGGREGATE] = between_value_and_aggregate
    try:
        controller.submit(task.id)
        pump(controller, current)
        with session_factory() as session:
            listing = session.query(Listing).one()
            assert listing.offer_price_cents == 20000 and listing.value_score == 2
        assert provider.value_calls == 2
    finally:
        controller.stop(2)


def test_title_changed_with_failed_detail_cannot_keep_old_recommendation(session_factory, base_settings):
    class Failing(Adapter):
        failed = False
        def fetch_detail(self, url):
            if self.failed:
                raise OSError("synthetic detail failure")
            return super().fetch_detail(url)
    task = TaskService(session_factory).create_task({"keyword": "fixture", "condition_requirement": "screen intact"})
    adapter, provider, current = Failing(), Provider(), [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        adapter.items = [replace(adapter.items[0], title="BROKEN screen")]
        adapter.failed = True
        controller.submit(task.id)
        pump(controller, current)
        with session_factory() as session:
            listing = session.query(Listing).one()
            assert listing.requirement_match is None and listing.value_score is None
        snapshot = OfferPipeline(session_factory).snapshot(listing.id, datetime.fromtimestamp(current[0], timezone.utc))[0]
        assert not snapshot["evaluation"]["eligible"]
        assert snapshot["evaluation"].get("value_score") is None
    finally:
        controller.stop(2)


def test_read_error_after_provider_accepts_message_never_automatically_resends(session_factory, base_settings):
    from test_delivery_lifecycle import Notifier, prepare

    from goodprice.execution.delivery import DeliveryService
    task, listing, offer, payload = prepare(session_factory, base_settings)
    notifier = Notifier(httpx.ReadError("accepted but response disconnected"))
    delivery = DeliveryService(session_factory, {"fixture": notifier})
    event = delivery.enqueue(task.id, task.version, listing, offer, "read-disconnect", payload)
    now = datetime.now(timezone.utc)
    assert delivery.deliver(event, now) == "uncertain"
    notifier.failure = None
    assert delivery.deliver(event, now+timedelta(seconds=60)) == "uncertain"
    assert notifier.calls == 1


@pytest.mark.parametrize("prior_sent", [True, False])
def test_reused_sqlite_ids_do_not_suppress_a_different_product_alert(session_factory, base_settings, prior_sent):
    from test_delivery_lifecycle import Notifier
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    adapter, notifier, current = Adapter(), Notifier(), [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {"fixture": notifier}, clock=lambda: current[0])
    try:
        if not prior_sent:
            controller.workers.capacities["delivery"] = 0
        controller.submit(task.id)
        pump(controller, current)
        assert notifier.calls == int(prior_sent)
        with session_factory() as session:
            listing = session.query(Listing).one()
            original_id = listing.id
            session.delete(listing)
            session.commit()
        adapter.items = [replace(adapter.items[0], external_id="different-product", url="https://www.goofish.com/item?id=other")]
        controller.submit(task.id)
        pump(controller, current)
        controller.workers.capacities["delivery"] = 1
        pump(controller, current)
        with session_factory() as session:
            assert session.query(Listing).one().id == original_id
        assert notifier.calls == int(prior_sent)+1
    finally:
        controller.stop(2)
