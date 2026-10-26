from datetime import datetime, timezone

from goodprice.crawler.offers import parse_offer_candidates
from goodprice.models import Listing, OfferEvaluationRecord
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.task_service import TaskService


def test_target_price_not_search_teaser_controls_budget(session_factory):
    task = TaskService(session_factory).create_task({"keyword": "iphone", "target_variant": "256GB",
                                                   "min_price": 1000, "max_price": 1600})
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="e", title="iphone", price=99,
                          url="u", task_id=task.id)
        session.add(listing)
        session.commit()
        listing_id = listing.id
    candidates = parse_offer_candidates('''<main data-item-detail>
      <div data-sku-id="128" data-attributes='{"容量":"128GB"}' data-price="1200"></div>
      <div data-sku-id="256" data-attributes='{"容量":"256GB"}' data-price="1500"></div></main>''')
    pipeline = OfferPipeline(session_factory)
    ids = pipeline.evaluate_listing(listing_id, task.id, candidates, datetime.now(timezone.utc))
    with session_factory() as session:
        rows = [session.get(OfferEvaluationRecord, i) for i in ids]
        eligible = [row.payload for row in rows if row.payload["eligible"]]
        assert len(eligible) == 1
        assert eligible[0]["amount_cents"] == 150000
        assert session.get(Listing, listing_id).price == 99
    TaskService(session_factory).update_task(task.id, {"max_price": 1400})
    ids = pipeline.evaluate_listing(listing_id, task.id, candidates, datetime.now(timezone.utc))
    with session_factory() as session:
        assert not any(session.get(OfferEvaluationRecord, i).payload["eligible"] for i in ids)


def test_condition_threshold_and_unknown_prices_never_pass(session_factory):
    task = TaskService(session_factory).create_task({"keyword": "x", "min_condition_score": 7})
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="e", title="x", price=1,
                          url="u", task_id=task.id)
        session.add(listing)
        session.commit()
        listing_id = listing.id
    candidates = parse_offer_candidates('<main data-item-detail><span data-item-price>100</span></main>')
    ids = OfferPipeline(session_factory).evaluate_listing(listing_id, task.id, candidates,
                                                        datetime.now(timezone.utc))
    with session_factory() as session:
        payload = session.get(OfferEvaluationRecord, ids[0]).payload
        assert payload["eligible"] is False
        assert payload["reason"] == "品相未核验或低于门槛"


def test_live_crawl_does_not_filter_teaser_before_detail(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service

    from goodprice.crawler.parser import parse_detail_html

    class Adapter(FakeAdapter):
        def fetch_detail(self, url):
            self.fetch_calls.append(url)
            return parse_detail_html('''<main data-item-detail><div data-sku-id="256"
              data-attributes='{"容量":"256GB"}' data-price="1500"></div>
              <img class="ant-image-img" src="https://img.alicdn.com/bao/uploaded/sku.jpg"></main>''')

    task = TaskService(session_factory).create_task({"keyword": "iphone", "target_variant": "256GB",
                                                   "min_price": 1000, "max_price": 1600})
    adapter = Adapter([_item(price=99)])
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=adapter)
    stats = crawl.run_task(task.id)
    assert len(adapter.fetch_calls) == 1
    assert stats["notified"] == 1
    assert "1500" in notifier.messages[0].content
    assert "99 元" not in notifier.messages[0].content
    assert crawl.vision.calls[0]["price"] == 1500


def test_live_crawl_inquiry_does_not_call_value_model_or_send(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, FakeLLM, _item, _service

    from goodprice.crawler.parser import parse_detail_html

    class Adapter(FakeAdapter):
        def fetch_detail(self, url):
            return parse_detail_html('''<main data-item-detail><span data-item-price>1</span>
              <p data-item-description>标价非售价，咨询价格</p></main>''')

    task = TaskService(session_factory).create_task({"keyword": "iphone"})
    llm = FakeLLM()
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=Adapter([_item(price=1)]), llm=llm)
    assert crawl.run_task(task.id)["notified"] == 0
    assert not llm.value_calls
    assert not notifier.messages


def test_pending_price_notifications_require_opt_in_and_are_deduplicated(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service

    from goodprice.crawler.parser import parse_detail_html

    class Adapter(FakeAdapter):
        def fetch_detail(self, url):
            return parse_detail_html('<main data-item-detail><p data-item-description>价格私聊</p></main>')

    task = TaskService(session_factory).create_task({"keyword": "iphone", "notify_pending_price": True})
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=Adapter([_item(price=1)]))
    crawl.run_task(task.id)
    crawl.run_task(task.id)
    assert len(notifier.messages) == 1
    assert "待询价" in notifier.messages[0].title
    assert "本批最优" not in notifier.messages[0].content


def test_reanalysis_does_not_rank_unknown_price(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, FakeLLM, _item, _service

    task = TaskService(session_factory).create_task({"keyword": "x", "fetch_detail": False})
    llm = FakeLLM()
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=llm)
    crawl.run_task(task.id)
    with session_factory() as session:
        listing_id = session.query(Listing).one().id
    crawl.reanalyze_listing(listing_id)
    assert not llm.value_calls


def test_disabled_vision_cannot_reuse_old_score_to_pass_threshold(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, FakeVision, _item, _service

    task = TaskService(session_factory).create_task({"keyword": "x", "min_condition_score": 7})
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="1001", title="t", price=100,
                          url="https://x/1001", task_id=task.id, condition_score=10)
        session.add(listing)
        session.commit()
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]),
                                  vision=FakeVision(enabled=False))
    assert crawl.run_task(task.id)["notified"] == 0
    assert not notifier.messages
