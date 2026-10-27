from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from goodprice.crawler.offers import parse_offer_candidates
from goodprice.main import build_app
from goodprice.models import Listing, ListingOffer, Notification
from goodprice.offers.pricing import decide_price, offer_fingerprint
from goodprice.offers.service import OfferService
from goodprice.offers.types import QuoteInput
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.task_service import TaskService


def test_manual_price_cannot_resolve_ambiguous_identity():
    offer = parse_offer_candidates('''<main data-item-detail>
      <div data-sku-id="1" data-attributes='{"容量":"256GB"}' data-price="100"></div>
      <div data-sku-id="1" data-attributes='{"容量":"128GB"}' data-price="200"></div></main>''')[0]
    now = datetime.now(timezone.utc)
    quote = QuoteInput(offer.key, 10000, now, now + timedelta(days=7), offer_fingerprint(offer))
    assert decide_price(offer, quote, now, 1200).state != "exact"


def test_description_and_media_are_analysis_dependencies(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, FakeLLM, _item, _service

    class Adapter(FakeAdapter):
        description = "屏幕完好"

        def fetch_detail(self, url):
            return replace(super().fetch_detail(url), description=self.description)

    task = TaskService(session_factory).create_task({"keyword": "x", "condition_requirement": "屏幕完好"})
    adapter, llm = Adapter([_item()]), FakeLLM()
    crawl, _, _ = _service(session_factory, base_settings, adapter=adapter, llm=llm)
    crawl.run_task(task.id)
    adapter.description = "屏幕碎裂，无法显示"
    llm.verdict = {"matched": False, "reason": "碎屏"}
    crawl.run_task(task.id)
    assert len(llm.calls) == 2
    with session_factory() as session:
        assert session.query(Listing).one().requirement_match is False


def test_removed_images_clear_old_condition_score(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service

    class Adapter(FakeAdapter):
        empty_images = False

        def fetch_detail(self, url):
            detail = super().fetch_detail(url)
            return replace(detail, image_urls=[]) if self.empty_images else detail

    task = TaskService(session_factory).create_task({"keyword": "x", "min_condition_score": 7})
    adapter = Adapter([_item()])
    crawl, _, _ = _service(session_factory, base_settings, adapter=adapter)
    crawl.run_task(task.id)
    adapter.empty_images = True
    crawl.run_task(task.id)
    with session_factory() as session:
        assert session.query(Listing).one().condition_score is None


def test_expired_snapshot_and_list_do_not_keep_recommendations(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service

    from goodprice.models import OfferObservation

    task = TaskService(session_factory).create_task({"keyword": "x"})
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    crawl.run_task(task.id)
    with session_factory() as session:
        listing_id = session.query(Listing).one().id
        for observation in session.query(OfferObservation):
            payload = deepcopy(observation.payload)
            for claim in payload["claims"]:
                claim["observed_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            observation.payload = payload
        session.commit()
    snapshot = OfferPipeline(session_factory).snapshot(listing_id, datetime.now(timezone.utc))[0]
    assert snapshot["price_state"] == "unknown"
    assert not snapshot["evaluation"]["eligible"]
    client = TestClient(build_app(base_settings, session_factory, with_scheduler=False))
    assert "本批最优" not in client.get("/listings").text
    data = client.get("/api/listings").json()[0]
    assert data.get("value_score") is None


def test_failed_channel_not_hidden_by_successful_channel(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, FakeNotifier, _item, _service

    class Flaky(FakeNotifier):
        attempts = 0

        def send(self, message):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("temporary")
            super().send(message)

    task = TaskService(session_factory).create_task({"keyword": "x"})
    crawl, success, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    flaky = Flaky("flaky")
    crawl.notifiers = [("log", success), ("flaky", flaky)]
    crawl.run_task(task.id)
    crawl.run_task(task.id)
    assert flaky.attempts == 2
    assert len(success.messages) == 1


def test_transient_fetch_failure_does_not_overwrite_fresh_quote_evidence(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service

    class Adapter(FakeAdapter):
        fail = False

        def fetch_detail(self, url):
            if self.fail:
                raise RuntimeError("timeout")
            return super().fetch_detail(url)

    task = TaskService(session_factory).create_task({"keyword": "x"})
    adapter = Adapter([_item()])
    crawl, _, _ = _service(session_factory, base_settings, adapter=adapter)
    crawl.run_task(task.id)
    with session_factory() as session:
        listing_id = session.query(Listing).one().id
    snapshot = OfferPipeline(session_factory).snapshot(listing_id, datetime.now(timezone.utc))[0]
    now = datetime.now(timezone.utc)
    OfferService(session_factory).confirm_quote(snapshot["id"], QuoteInput(
        snapshot["key"], 9000, now, now + timedelta(days=7), snapshot["fingerprint"]))
    adapter.fail = True
    crawl.run_task(task.id)
    snapshot = OfferPipeline(session_factory).snapshot(listing_id, datetime.now(timezone.utc))[0]
    assert snapshot["source"] == "manual"
    assert snapshot["effective_price_cents"] == 9000


def test_legacy_successful_notice_not_resent_after_migration(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service

    task = TaskService(session_factory).create_task({"keyword": "x"})
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="1001", title="t", price=100,
                          url="https://x/1001", task_id=task.id, notified_at=datetime.now(),
                          last_notified_satisfaction=99)
        session.add(listing)
        session.flush()
        session.add(Notification(listing_id=listing.id, task_id=task.id, channel="log", status="sent"))
        session.commit()
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    crawl.run_task(task.id)
    assert not notifier.messages
    with session_factory() as session:
        assert session.query(ListingOffer).count() == 1
