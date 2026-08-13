import pytest

from goodprice.crawler.base import CrawlerAuthError, ListingData
from goodprice.models import Listing
from goodprice.services.crawl_service import CrawlService
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService


class FakeAdapter:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error

    def search(self, keyword):
        if self.error:
            raise self.error
        return self.items


class FakeLLM:
    def __init__(self, enabled=True, verdict=None, error=None):
        self.enabled = enabled
        self.verdict = verdict or {
            "condition_score": 8,
            "defects": [],
            "recommended": True,
            "reason": "ok",
        }
        self.error = error

    def analyze_listing(self, **kwargs):
        if self.error:
            raise self.error
        return self.verdict


class FakeNotifier:
    def __init__(self, name="log"):
        self.name = name
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def _item(external_id="1001", price=100.0):
    return ListingData(
        external_id=external_id,
        title=f"商品{external_id}",
        price=price,
        url=f"https://x/{external_id}",
        image_urls=[f"https://x/{external_id}.jpg"],
    )


def _service(session_factory, base_settings, adapter=None, llm=None, notifier=None):
    settings_service = SettingsService(session_factory, base=base_settings)
    notifier = notifier or FakeNotifier()
    crawl = CrawlService(
        session_factory=session_factory,
        adapter=adapter or FakeAdapter(),
        llm=llm or FakeLLM(),
        notifiers=[(notifier.name, notifier)],
        settings_service=settings_service,
    )
    return crawl, notifier, settings_service


def test_happy_path_and_dedup(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "iPhone", "min_condition_score": "6"})
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    stats = crawl.run_task(task.id)
    assert stats["new"] == 1
    assert stats["notified"] == 1
    assert len(notifier.messages) == 1

    stats2 = crawl.run_task(task.id)
    assert stats2["new"] == 0
    assert stats2["notified"] == 0
    assert len(notifier.messages) == 1  # 同一商品只通知一次

    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score == 8
        assert listing.notified_at is not None
        assert len(listing.snapshots) == 1


def test_price_filter(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "max_price": "50"})
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item(price=100.0)]))
    stats = crawl.run_task(task.id)
    assert stats["new"] == 0
    assert stats["notified"] == 0
    with session_factory() as session:
        assert session.query(Listing).count() == 0


def test_condition_gate_blocks_low_score(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "min_condition_score": "6"})
    llm = FakeLLM(
        verdict={"condition_score": 3, "defects": ["碎屏"], "recommended": False, "reason": "太差"}
    )
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 0
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score == 3
        assert listing.notified_at is None


def test_llm_failure_falls_back_to_price_only(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    llm = FakeLLM(error=RuntimeError("网络错误"))
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score is None


def test_llm_disabled_skips_analysis(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    llm = FakeLLM(enabled=False)
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 1


def test_adapter_error_records_last_error(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    crawl, _, _ = _service(
        session_factory,
        base_settings,
        adapter=FakeAdapter(error=CrawlerAuthError("Cookie 失效")),
    )
    with pytest.raises(CrawlerAuthError):
        crawl.run_task(task.id)
    with session_factory() as session:
        loaded = session.get(type(task), task.id)
        assert "Cookie 失效" in loaded.last_error


def test_price_change_creates_snapshot(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = FakeAdapter([_item(price=100.0)])
    crawl, _, _ = _service(session_factory, base_settings, adapter=adapter)
    crawl.run_task(task.id)
    adapter.items = [_item(price=90.0)]
    crawl.run_task(task.id)
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.price == 90.0
        assert len(listing.snapshots) == 2
