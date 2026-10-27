import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from goodprice.crawler.base import ListingData, ListingDetail
from goodprice.execution.model_provider import ModelProvider
from goodprice.execution.pipeline import build_pipeline
from goodprice.models import Listing, TaskRun
from goodprice.offers.types import OfferCandidate, PriceClaim
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService


class Adapter:
    def __init__(self, count=1):
        self.items = [ListingData(str(i), f"fixture {i}", 99, f"https://www.goofish.com/item?id={i}") for i in range(count)]
        self.details = []

    def search(self, *args, **kwargs):
        return self.items

    def fetch_detail(self, url):
        self.details.append(url)
        return ListingDetail(description="offline fixture", offers=[OfferCandidate("default", {}, claims=(
            PriceClaim("dom", "exact", "100", datetime.now(timezone.utc), "fixture:price", amount_cents=10000),))])

    @contextmanager
    def session(self):
        yield self


def pump(controller, current, limit=150):
    for _ in range(limit):
        controller.tick()
        time.sleep(.01)
        current[0] += 5
        with controller.store.factory() as session:
            active = session.query(TaskRun.id).filter(TaskRun.state.in_(("queued", "active", "waiting_capacity"))).first()
        if not active and not controller.store.has_open_work() and not controller._futures and not getattr(controller, "_delivery_futures", {}):
            return
    raise AssertionError("pipeline did not settle within bounded fake time")


def test_real_stage_graph_reaches_verified_price_without_network_or_models(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    adapter = Adapter()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        run_id = controller.submit(task.id)
        pump(controller, current)
        with session_factory() as session:
            listing = session.query(Listing).one()
            assert session.get(TaskRun, run_id).state == "completed"
        snapshot = OfferPipeline(session_factory).snapshot(listing.id, datetime.now(timezone.utc))[0]
        assert snapshot["effective_price_cents"] == 10000
        assert snapshot["evaluation"]["eligible"]
        assert len(adapter.details) == 1
    finally:
        controller.stop(2)


def test_overflow_candidates_resume_instead_of_repeating_first_slice(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    settings = base_settings.model_copy(update={"execution_run_details": 2})
    adapter = Adapter(5)
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        assert len(set(adapter.details)) == 2
        controller.submit(task.id)
        pump(controller, current)
        assert len(set(adapter.details)) == 4
    finally:
        controller.stop(2)


class Provider(ModelProvider):
    def __init__(self):
        self.requirement_calls = 0
        self.value_calls = 0

    def enabled(self, role, settings):
        return role != "visual"

    def requirement(self, data, settings, fallback=False):
        self.requirement_calls += 1
        return {"matched": True, "reason": "offline verified"}

    def value(self, items, settings, fallback=False):
        self.value_calls += 1
        return {"scores": {item["external_id"]: 8 for item in items}, "best": items[0]["external_id"]}


def test_completed_model_steps_reuse_content_cache_on_next_run(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "fixture", "condition_requirement": "屏幕完好"})
    adapter, provider = Adapter(), Provider()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        for _ in range(2):
            controller.submit(task.id)
            pump(controller, current)
        assert provider.requirement_calls == provider.value_calls == 1
        with session_factory() as session:
            listing = session.query(Listing).one()
            assert listing.value_score == 8 and listing.requirement_match is True
    finally:
        controller.stop(2)


def test_slow_model_allows_next_task_search_and_pause_rejects_late_result(session_factory, base_settings):
    import threading
    started, release, second_searched = threading.Event(), threading.Event(), threading.Event()
    class Slow(Provider):
        def requirement(self, data, settings, fallback=False):
            if data["requirement"] == "hold":
                started.set()
                assert release.wait(5)
            return super().requirement(data, settings, fallback)
    class Observed(Adapter):
        def search(self, keyword, **kwargs):
            if keyword == "second":
                second_searched.set()
            return super().search(keyword, **kwargs)
    service = TaskService(session_factory)
    first = service.create_task({"keyword": "first", "condition_requirement": "hold"})
    second = service.create_task({"keyword": "second", "condition_requirement": "other"})
    adapter, provider = Observed(), Slow()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        run = controller.submit(first.id)
        for _ in range(30):
            controller.tick()
            current[0] += 5
            time.sleep(.01)
            if started.is_set():
                break
        assert started.is_set()
        controller.submit(second.id)
        for _ in range(15):
            controller.tick()
            current[0] += 5
            time.sleep(.01)
            if second_searched.is_set():
                break
        assert second_searched.is_set()
        service.toggle_task(first.id)
        release.set()
        pump(controller, current)
        with session_factory() as session:
            assert session.get(TaskRun, run).state == "cancelled"
            assert session.query(Listing).filter_by(task_id=first.id).one().requirement_match is None
    finally:
        release.set()
        controller.stop(2)


def test_http_budget_defers_unfinished_analysis_without_false_recommendation(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "fixture", "condition_requirement": "屏幕完好"})
    settings = base_settings.model_copy(update={"execution_run_http_attempts": 1})
    adapter, provider = Adapter(), Provider()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        run_id = controller.submit(task.id)
        pump(controller, current)
        assert provider.requirement_calls + provider.value_calls == 1
        with session_factory() as session:
            run = session.get(TaskRun, run_id)
            assert run.state == "deferred" and run.cursor["remaining_ids"]
            listing = session.query(Listing).one()
        assert not OfferPipeline(session_factory).snapshot(listing.id, datetime.now(timezone.utc))[0]["evaluation"]["eligible"]
    finally:
        controller.stop(2)


def test_provider_retry_after_is_respected_without_holding_model_worker(session_factory, base_settings):
    import httpx
    task = TaskService(session_factory).create_task({"keyword": "fixture", "condition_requirement": "test"})
    current = [datetime.now(timezone.utc).timestamp()]
    attempted = []
    class Limited(Provider):
        def enabled(self, role, settings): return role == "requirement"
        def requirement(self, data, settings, fallback=False):
            attempted.append(current[0])
            if len(attempted) == 1:
                request = httpx.Request("POST", "https://model.example/test")
                raise httpx.HTTPStatusError("limited", request=request,
                    response=httpx.Response(429, headers={"Retry-After": "30"}, request=request))
            return {"matched": True, "reason": "offline"}
    adapter = Adapter()
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=Limited(), notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        assert len(attempted) == 2 and attempted[1] - attempted[0] >= 30
    finally:
        controller.stop(2)


@pytest.mark.parametrize("explicit_media", [True, False])
def test_visual_stage_keeps_target_scope_and_reuses_signed_url_content(session_factory, base_settings, tmp_path, explicit_media):
    from dataclasses import replace

    import httpx
    from test_media_processing import image_bytes

    from goodprice.media.budget import MediaBudget
    from goodprice.media.download import MediaDownloader
    from goodprice.media.processing import MediaProcessor
    from goodprice.media.types import MediaCandidate
    from goodprice.models import MediaAsset
    class Images(Adapter):
        signature = "one"
        def fetch_detail(self, url):
            candidates = [MediaCandidate(str(i) + self.signature,
                "image", f"https://cdn.example/a{i}?sig={self.signature}") for i in range(2)]
            return replace(super().fetch_detail(url), media=candidates if explicit_media else [],
                image_urls=[c.url for c in candidates])
    class Visual(Provider):
        visual_calls = 0
        def enabled(self, role, settings): return role == "visual"
        def visual(self, candidate, selection, data, settings):
            self.visual_calls += 1
            return {"condition_score": 8, "observations": [{"text": "offline image", "evidence_ids": [selection.items[0].id]}],
                    "limitations": list(selection.limitations), "evidence_ids": [e.id for e in selection.items]}
    def processor(settings):
        budget = MediaBudget(settings.data_root / "media-cache", settings.media_cache_mib * 1024**2, 86400, safety_bytes=0)
        downloader = MediaDownloader(budget, httpx.MockTransport(lambda r: httpx.Response(200, content=image_bytes())), lambda h,p: ["93.184.216.34"])
        return MediaProcessor(budget, downloader)
    task = TaskService(session_factory).create_task({"keyword": "fixture", "min_condition_score": 7})
    adapter, provider = Images(), Visual()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, processor_factory=processor,
        notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        adapter.signature = "two"
        controller.submit(task.id)
        pump(controller, current)
        assert provider.visual_calls == 1
        with session_factory() as session:
            listing = session.query(Listing).one()
            assert listing.condition_score == 8
            assert session.query(MediaAsset).filter(MediaAsset.key.like("evidence:%")).count() == 1
        assert OfferPipeline(session_factory).snapshot(listing.id, datetime.now(timezone.utc))[0]["evaluation"]["eligible"]
    finally:
        controller.stop(2)


def test_outbox_delivers_after_run_finishes_without_repeating_success(session_factory, base_settings):
    from test_crawl_service import FakeNotifier
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    adapter, notifier = Adapter(), FakeNotifier()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {"offline": notifier}, clock=lambda: current[0])
    try:
        for _ in range(2):
            controller.submit(task.id)
            pump(controller, current)
        assert len(notifier.messages) == 1
    finally:
        controller.stop(2)


def test_forced_reanalysis_uses_pool_bypasses_cache_and_does_not_notify(session_factory, base_settings):
    from test_crawl_service import FakeNotifier
    task = TaskService(session_factory).create_task({"keyword": "fixture", "condition_requirement": "screen"})
    class Improved(Provider):
        score = 8
        def value(self, items, settings, fallback=False):
            self.value_calls += 1
            return {"scores": {item["external_id"]: self.score for item in items}, "best": items[0]["external_id"]}
    adapter, notifier, provider = Adapter(), FakeNotifier(), Improved()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider,
        notifier_factory=lambda: {"offline": notifier}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        assert len(notifier.messages) == 1
        with session_factory() as session:
            listing_id = session.query(Listing).one().id
        provider.score = 9
        controller.submit_reanalysis(listing_id)
        pump(controller, current)
        assert provider.requirement_calls == provider.value_calls == 2
        assert len(notifier.messages) == 1
    finally:
        controller.stop(2)


def test_quote_changed_during_value_call_recomputes_value_for_current_price(session_factory, base_settings):
    from datetime import timedelta

    from goodprice.models import OfferObservation
    from goodprice.offers.service import OfferService
    from goodprice.offers.types import QuoteInput
    class Quoted(Provider):
        def value(self, items, settings, fallback=False):
            self.value_calls += 1
            if self.value_calls == 1:
                offer_id = int(items[0]["external_id"].split(":")[1])
                with session_factory() as session:
                    observation = session.query(OfferObservation).filter_by(offer_id=offer_id).order_by(OfferObservation.id.desc()).first()
                now = datetime.now(timezone.utc)
                OfferService(session_factory).confirm_quote(offer_id, QuoteInput("default", 20000,
                    now, now + timedelta(days=7), observation.fingerprint))
            return {"scores": {item["external_id"]: 9 if item["price"] == 100 else 2 for item in items},
                    "best": items[0]["external_id"]}
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    adapter, provider = Adapter(), Quoted()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        with session_factory() as session:
            listing = session.query(Listing).one()
            assert listing.offer_price_cents == 20000 and listing.value_score == 2
        assert provider.value_calls == 2
    finally:
        controller.stop(2)


def test_slow_value_result_refreshes_price_and_reuses_completed_analysis(session_factory, base_settings):
    from dataclasses import replace
    current = [datetime.now(timezone.utc).timestamp()]
    class Fresh(Adapter):
        def fetch_detail(self, url):
            detail = super().fetch_detail(url)
            claim = replace(detail.offers[0].claims[0], observed_at=datetime.fromtimestamp(current[0], timezone.utc))
            return replace(detail, offers=[OfferCandidate("default", {}, claims=(claim,))])
    class SlowValue(Provider):
        def enabled(self, role, settings): return role == "value"
        def value(self, items, settings, fallback=False):
            self.value_calls += 1
            current[0] += 75
            return {"scores": {item["external_id"]: 8 for item in items}, "best": items[0]["external_id"]}
    task = TaskService(session_factory).create_task({"keyword": "fixture", "interval_minutes": 1})
    adapter, provider = Fresh(), SlowValue()
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        run_id = controller.submit(task.id)
        pump(controller, current)
        with session_factory() as session:
            assert session.get(TaskRun, run_id).state == "completed"
            assert session.query(Listing).one().value_score == 8
        assert provider.value_calls == 1
        assert len(adapter.details) == 2
    finally:
        controller.stop(2)


def test_pause_resume_does_not_repeat_a_successful_business_event(session_factory, base_settings):
    from test_crawl_service import FakeNotifier
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "fixture"})
    adapter, notifier = Adapter(), FakeNotifier()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {"offline": notifier}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        service.toggle_task(task.id)
        service.toggle_task(task.id)
        controller.submit(task.id)
        pump(controller, current)
        assert len(notifier.messages) == 1
    finally:
        controller.stop(2)


def test_changed_model_configuration_does_not_submit_old_queued_request(session_factory, base_settings):
    from goodprice.models import WorkItem
    task = TaskService(session_factory).create_task({"keyword": "fixture", "condition_requirement": "test"})
    settings = SettingsService(session_factory, base_settings)
    adapter, provider = Adapter(), Provider()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, settings, adapter_factory=lambda: adapter.session(),
        provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        run_id = controller.submit(task.id)
        controller.store.postpone_gate("model", current[0] + 120)
        for _ in range(30):
            controller.tick()
            current[0] += 2
            time.sleep(.01)
            with session_factory() as session:
                waiting = session.query(WorkItem).filter_by(stage="analyze").first()
            if waiting:
                break
        assert waiting and provider.requirement_calls == 0
        settings.set_many({"llm_model": "changed-model"})
        pump(controller, current)
        assert provider.requirement_calls == 0
        with session_factory() as session:
            assert session.get(TaskRun, run_id).state == "deferred"
    finally:
        controller.stop(2)


@pytest.mark.parametrize("limits", [{"max_price": 50}, {"min_condition_score": 7}])
def test_unmet_hard_gates_do_not_spend_value_model_calls(session_factory, base_settings, limits):
    task = TaskService(session_factory).create_task({"keyword": "fixture", **limits})
    adapter, provider = Adapter(), Provider()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        assert provider.value_calls == 0
    finally:
        controller.stop(2)


def test_pending_price_alert_is_opt_in_and_deduplicated(session_factory, base_settings):
    from dataclasses import replace

    from test_crawl_service import FakeNotifier
    class Inquiry(Adapter):
        def fetch_detail(self, url):
            return replace(super().fetch_detail(url), offers=[OfferCandidate("default", {}, claims=(
                PriceClaim("description", "inquiry", "请联系卖家询价", datetime.now(timezone.utc), "description"),))])
    task = TaskService(session_factory).create_task({"keyword": "fixture", "notify_pending_price": True})
    adapter, notifier = Inquiry(), FakeNotifier()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {"offline": notifier}, clock=lambda: current[0])
    try:
        for _ in range(2):
            controller.submit(task.id)
            pump(controller, current)
        assert len(notifier.messages) == 1 and "待询价" in notifier.messages[0].title
        with session_factory() as session:
            assert session.query(Listing).one().value_score is None
    finally:
        controller.stop(2)


def test_fresh_revalidation_rebinds_unsent_event_after_pause_resume(session_factory, base_settings):
    from test_crawl_service import FakeNotifier
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "fixture"})
    adapter, notifier = Adapter(), FakeNotifier()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {"offline": notifier}, clock=lambda: current[0])
    try:
        controller.workers.capacities["delivery"] = 0
        controller.submit(task.id)
        pump(controller, current)
        assert not notifier.messages
        service.toggle_task(task.id)
        service.toggle_task(task.id)
        controller.workers.capacities["delivery"] = 1
        controller.submit(task.id)
        pump(controller, current)
        assert len(notifier.messages) == 1
    finally:
        controller.stop(2)


def test_sku_missing_from_new_capture_keeps_history_but_pauses_current_price(session_factory, base_settings):
    from dataclasses import replace

    from goodprice.crawler.offers import parse_offer_candidates
    class Options(Adapter):
        missing = False
        def fetch_detail(self, url):
            detail = super().fetch_detail(url)
            html = '''<main><div data-sku-id="128" data-attributes='{"容量":"128GB"}' data-price="1000"></div>'''
            if not self.missing:
                html += '''<div data-sku-id="256" data-attributes='{"容量":"256GB"}' data-price="1500"></div>'''
            return replace(detail, offers=parse_offer_candidates(html + "</main>"))
    task = TaskService(session_factory).create_task({"keyword": "fixture", "target_variant": "256GB"})
    adapter = Options()
    current = [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        adapter.missing = True
        controller.submit(task.id)
        pump(controller, current)
        with session_factory() as session:
            listing_id = session.query(Listing).one().id
        missing = next(o for o in OfferPipeline(session_factory).snapshot(listing_id, datetime.now(timezone.utc)) if o["key"] == "sku:256")
        assert missing["price_state"] == "unknown" and not missing["evaluation"]["eligible"]
    finally:
        controller.stop(2)

@pytest.mark.parametrize("blocked", [False, True])
def test_stage_graph_preserves_cached_seller_risk(session_factory, base_settings, blocked):
    from dataclasses import replace

    from goodprice.crawler.base import SellerData
    from goodprice.models import Seller
    class Merchant(Adapter):
        seller_calls = 0
        def fetch_detail(self, url):
            return replace(super().fetch_detail(url), seller_uid="seller-fixture", positive_rate=.8)
        def fetch_seller(self, uid):
            self.seller_calls += 1
            return SellerData(uid, "offline seller", 100, 100, ["fixture"])
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    if blocked:
        with session_factory() as session:
            session.add(Seller(platform="xianyu", seller_uid="seller-fixture", blocked=True))
            session.commit()
    adapter, current = Merchant(), [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        for _ in range(2):
            controller.submit(task.id)
            pump(controller, current)
        assert adapter.seller_calls == 1
        with session_factory() as session:
            assert session.query(Seller).one().positive_rate == 1
            from goodprice.constants import RISK_HIGH
            assert session.query(Listing).one().seller_risk["risk_level"] == RISK_HIGH
            assert session.query(Listing).one().blocked is blocked
    finally:
        controller.stop(2)

def test_prompt_changes_invalidate_semantic_model_cache(session_factory, base_settings, monkeypatch):
    import goodprice.analysis.prompts as prompts
    task = TaskService(session_factory).create_task({"keyword": "fixture", "condition_requirement": "fixture"})
    adapter, provider, current = Adapter(), Provider(), [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        monkeypatch.setattr(prompts, "REQUIREMENT_SYSTEM_PROMPT", prompts.REQUIREMENT_SYSTEM_PROMPT + " fixture version")
        controller.submit(task.id)
        pump(controller, current)
        assert provider.requirement_calls == 2
    finally:
        controller.stop(2)


def test_value_provider_cannot_rate_unrequested_ids(session_factory, base_settings):
    class Wrong(Provider):
        def value(self, items, settings, fallback=False):
            return {"scores": {"foreign": 10}, "best": "foreign"}
    from goodprice.models import WorkItem
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    adapter, current = Adapter(), [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=Wrong(), notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        controller.submit(task.id)
        pump(controller, current)
        with session_factory() as session:
            assert session.query(WorkItem).filter_by(stage="analyze", state="failed").count() == 1
            assert session.query(Listing).one().value_score is None
    finally:
        controller.stop(2)

def test_independent_value_cache_survives_changed_batch_membership(session_factory, base_settings):
    class Recorded(Provider):
        def __init__(self):
            super().__init__()
            self.groups = []
        def value(self, items, settings, fallback=False):
            self.groups.append([i["external_id"] for i in items])
            return super().value(items, settings, fallback)
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    adapter, provider, current = Adapter(2), Recorded(), [datetime.now(timezone.utc).timestamp()]
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {}, clock=lambda: current[0])
    try:
        seal = controller.pipeline._value_batch
        expected = [2]
        def sealed(session, store, run, rows, runtime):
            if len(rows) >= expected[0]:
                return seal(session, store, run, rows, runtime)
        controller.pipeline._value_batch = sealed
        controller.submit(task.id)
        pump(controller, current)
        adapter.items = Adapter(3).items
        expected[0] = 3
        controller.submit(task.id)
        pump(controller, current)
        assert provider.groups == [["offer:1", "offer:2"], ["offer:3"]]
    finally:
        controller.stop(2)
