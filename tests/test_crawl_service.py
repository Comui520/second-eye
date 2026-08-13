import pytest

from goodprice.crawler.base import CrawlerAuthError, ListingData, SellerData
from goodprice.models import Listing
from goodprice.services.crawl_service import CrawlService, TaskRunGuard
from goodprice.services.seller_service import SellerService
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService


class FakeAdapter:
    def __init__(self, items=None, error=None):
        self.items = items or []
        self.error = error
        self.fetch_calls = []

    def search(self, keyword):
        if self.error:
            raise self.error
        return self.items

    def fetch_detail(self, url):
        self.fetch_calls.append(url)
        from goodprice.crawler.base import ListingDetail

        return ListingDetail(description="屏幕完好 电池健康", image_urls=["https://x/d.jpg"])


class FakeLLM:
    def __init__(self, enabled=True, verdict=None, error=None):
        self.enabled = enabled
        self.verdict = verdict or {"matched": True, "reason": "符合需求"}
        self.error = error
        self.calls = []

    def analyze_requirement(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.verdict


class FakeVision:
    def __init__(self, enabled=True, verdict=None, error=None):
        self.enabled = enabled
        self.verdict = verdict or {
            "condition_score": 8,
            "defects": [],
            "recommended": True,
            "reason": "ok",
        }
        self.error = error
        self.calls = []

    def analyze_condition(self, **kwargs):
        self.calls.append(kwargs)
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
        image_urls=[f"https://img.alicdn.com/bao/uploaded/{external_id}.jpg"],
    )


def _service(session_factory, base_settings, adapter=None, llm=None, vision=None, notifier=None):
    settings_service = SettingsService(session_factory, base=base_settings)
    notifier = notifier or FakeNotifier()
    crawl = CrawlService(
        session_factory=session_factory,
        adapter=adapter or FakeAdapter(),
        llm=llm or FakeLLM(),
        vision=vision if vision is not None else FakeVision(),
        notifiers=[(notifier.name, notifier)],
        settings_service=settings_service,
    )
    return crawl, notifier, settings_service


class SellerFakeAdapter(FakeAdapter):
    def __init__(self, items=None, seller_data=None, seller_error=None, credit_label="卖家信用极好", positive_rate=1.0):
        super().__init__(items=items)
        self.seller_data = seller_data or SellerData(
            seller_uid="2672367114", positive_count=133, total_count=194, tags=["沟通愉快 13"]
        )
        self.seller_error = seller_error
        self.seller_calls = 0
        self.credit_label = credit_label
        self.positive_rate = positive_rate

    def fetch_detail(self, url):
        from goodprice.crawler.base import ListingDetail

        return ListingDetail(
            description="屏幕完好",
            image_urls=["https://img.alicdn.com/bao/uploaded/d.jpg"],
            seller_uid="2672367114",
            seller_name="饼住呼吸",
            credit_label=self.credit_label,
            positive_rate=self.positive_rate,
            sold_count=264,
        )

    def fetch_seller(self, user_id):
        self.seller_calls += 1
        if self.seller_error:
            raise self.seller_error
        return self.seller_data


def _service_with_seller(session_factory, base_settings, adapter, **kwargs):
    settings_service = SettingsService(session_factory, base=base_settings)
    notifier = FakeNotifier()
    seller_service = SellerService(session_factory, adapter=adapter)
    crawl = CrawlService(
        session_factory=session_factory,
        adapter=adapter,
        llm=kwargs.get("llm") or FakeLLM(),
        vision=kwargs.get("vision") if "vision" in kwargs else FakeVision(),
        notifiers=[("log", notifier)],
        settings_service=settings_service,
        seller_service=seller_service,
    )
    return crawl, notifier


def test_seller_advisory_in_notification_and_cache(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "condition_requirement": "屏幕完好"})
    adapter = SellerFakeAdapter([_item()])
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    assert "卖家" in notifier.messages[0].content
    assert "低" in notifier.messages[0].content
    assert "好评率 100%" in notifier.messages[0].content
    crawl.run_task(task.id)
    assert adapter.seller_calls == 1  # 缓存命中
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.seller_uid == "2672367114"
        assert listing.seller_risk["risk_level"] == "低"
        assert listing.task_id == task.id
        assert listing.satisfaction == 92.0


def test_seller_fetch_failure_does_not_block(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = SellerFakeAdapter([_item()], seller_error=RuntimeError("主页超时"))
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.seller_risk["risk_level"] == "低"  # 详情页好评率 100% 仍可用


def test_no_seller_uid_skips_seller_stage(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = FakeAdapter([_item()])
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.seller_risk is None


def test_condition_analysis_retries_once_and_records_error(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    vision = FakeVision(error=RuntimeError("模型超时"))
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=vision)
    crawl.run_task(task.id)
    assert len(vision.calls) == 2  # 重试一次
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_detail["error"] == "模型超时"
        assert listing.condition_score is None


def test_no_valid_image_skips_vision(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    vision = FakeVision()

    class NoImgAdapter(FakeAdapter):
        def __init__(self):
            from goodprice.crawler.base import ListingData

            item = ListingData(
                external_id="1001",
                title="商品1001",
                price=100.0,
                url="https://x/1001",
                image_urls=["https://img.alicdn.com/imgextra/xx-2-tps-2-2.png"],
            )
            super().__init__(items=[item])

        def fetch_detail(self, url):
            from goodprice.crawler.base import ListingDetail

            return ListingDetail(
                description="无图",
                image_urls=["https://img.alicdn.com/imgextra/xx-2-tps-2-2.png"],
            )

    crawl, _, _ = _service(session_factory, base_settings, adapter=NoImgAdapter(), vision=vision)
    crawl.run_task(task.id)
    assert vision.calls == []
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_detail["error"] == "无有效商品图"


def test_seller_without_credit_label_still_works(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = SellerFakeAdapter([_item()], credit_label=None, positive_rate=None)
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.seller_risk["risk_level"] == "高"  # 按卖家主页好评 133/194 判定


def test_seller_stage_crash_records_last_error(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = SellerFakeAdapter([_item()])
    crawl, _ = _service_with_seller(session_factory, base_settings, adapter)

    def boom(platform, seller_uid, nickname=None, credit_label=None, session=None):
        raise RuntimeError("卖家服务崩溃")

    crawl.seller_service.ensure_fresh = boom
    with pytest.raises(RuntimeError, match="卖家服务崩溃"):
        crawl.run_task(task.id)
    with session_factory() as session:
        loaded = session.get(type(task), task.id)
        assert "卖家服务崩溃" in loaded.last_error


def test_blocked_listing_and_seller_skipped(session_factory, base_settings):
    from goodprice.models import Seller

    task = TaskService(session_factory).create_task({"keyword": "k"})
    adapter = SellerFakeAdapter([_item()])
    crawl, notifier = _service_with_seller(session_factory, base_settings, adapter)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1

    with session_factory() as session:
        listing = session.query(Listing).one()
        listing.blocked = True
        session.commit()
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1  # 已拉黑不再处理

    with session_factory() as session:
        session.query(Listing).update({Listing.blocked: False})
        seller = session.query(Seller).one()
        seller.blocked = True
        session.commit()
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1  # 卖家已拉黑不再通知


def test_happy_path_and_dedup(session_factory, base_settings):
    task = TaskService(session_factory).create_task(
        {"keyword": "iPhone", "min_condition_score": "6", "condition_requirement": "屏幕完好"}
    )
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    stats = crawl.run_task(task.id)
    assert stats["new"] == 1
    assert stats["notified"] == 1
    assert len(notifier.messages) == 1

    stats2 = crawl.run_task(task.id)
    assert stats2["new"] == 0
    assert stats2["notified"] == 0
    assert len(notifier.messages) == 1

    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score == 8
        assert listing.requirement_match is True
        assert listing.description == "屏幕完好 电池健康"
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


def test_requirement_mismatch_blocks_and_skips_vision(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "condition_requirement": "屏幕完好"})
    llm = FakeLLM(verdict={"matched": False, "reason": "描述说后盖碎了"})
    vision = FakeVision()
    crawl, notifier, _ = _service(
        session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm, vision=vision
    )
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 0
    assert vision.calls == []
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.requirement_match is False
        assert listing.condition_score is None


def test_requirement_empty_skips_stage1(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    llm = FakeLLM()
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    crawl.run_task(task.id)
    assert llm.calls == []
    assert len(notifier.messages) == 1


def test_vision_disabled_skips_stage2(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    crawl, notifier, _ = _service(
        session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=FakeVision(enabled=False)
    )
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score is None


def test_condition_gate_blocks_low_score(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "min_condition_score": "6"})
    vision = FakeVision(
        verdict={"condition_score": 3, "defects": ["碎屏"], "recommended": False, "reason": "太差"}
    )
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=vision)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 0


def test_requirement_failure_fails_open(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "condition_requirement": "屏幕完好"})
    llm = FakeLLM(error=RuntimeError("网络错误"))
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 1
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.requirement_match is None


def test_fetch_detail_off_skips_call(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "fetch_detail": False})
    adapter = FakeAdapter([_item()])
    crawl, _, _ = _service(session_factory, base_settings, adapter=adapter)
    crawl.run_task(task.id)
    assert adapter.fetch_calls == []


def test_fetch_detail_failure_falls_back(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})

    class BrokenAdapter(FakeAdapter):
        def fetch_detail(self, url):
            raise RuntimeError("详情页超时")

    crawl, notifier, _ = _service(session_factory, base_settings, adapter=BrokenAdapter([_item()]))
    stats = crawl.run_task(task.id)
    assert stats["notified"] == 1


def test_backfill_fills_missing_analysis_without_renotify(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    crawl, notifier, _ = _service(
        session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=FakeVision(enabled=False)
    )
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1

    crawl2, notifier2, _ = _service(
        session_factory, base_settings, adapter=FakeAdapter([_item()]), vision=FakeVision()
    )
    stats = crawl2.run_task(task.id)
    assert stats["backfilled"] == 1
    assert len(notifier2.messages) == 0
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.condition_score == 8


def test_guard_prevents_concurrent_run(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k"})
    guard = TaskRunGuard()
    assert guard.try_start(task.id) is True
    assert guard.try_start(task.id) is False
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    crawl.guard = guard
    stats = crawl.run_task(task.id)
    assert stats.get("skipped") == "already_running"
    guard.finish(task.id)


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
