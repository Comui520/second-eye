from goodprice.models import Listing
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.task_service import TaskService


def test_title_change_invalidates_requirement_analysis(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, FakeLLM, _item, _service
    task = TaskService(session_factory).create_task({"keyword": "phone", "condition_requirement": "整机"})
    item = _item()
    item.title = "整机"
    adapter = FakeAdapter([item])
    llm = FakeLLM()
    crawl, _, _ = _service(session_factory, base_settings, adapter=adapter, llm=llm)
    crawl.run_task(task.id)
    item.title = "只有配件"
    llm.verdict = {"matched": False, "reason": "配件不满足整机"}
    crawl.run_task(task.id)
    with session_factory() as session:
        row = session.query(Listing).one()
        assert row.title == "只有配件" and row.requirement_match is False
    assert len(llm.calls) == 2


def test_disabling_detail_never_reuses_previous_exact_price(session_factory, base_settings):
    from datetime import datetime, timezone

    from test_crawl_service import FakeAdapter, _item, _service
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "test"})
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    crawl.run_task(task.id)
    service.update_task(task.id, {"fetch_detail": False})
    crawl.run_task(task.id)
    with session_factory() as session:
        row = session.query(Listing).one()
    assert OfferPipeline(session_factory).snapshot(row.id, datetime.now(timezone.utc))[0]["price_state"] != "exact"


def test_paused_during_model_request_does_not_publish_late_result(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, FakeLLM, _item, _service
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "test", "condition_requirement": "屏幕完好"})
    class PauseDuringModel(FakeLLM):
        def analyze_requirement(self, *args, **kwargs):
            service.toggle_task(task.id)
            return {"matched": True, "reason": "late"}
    crawl, notifier, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]), llm=PauseDuringModel())
    result = crawl.run_task(task.id)
    assert result.get("skipped") == "superseded"
    with session_factory() as session:
        assert session.query(Listing).one().requirement_match is None
    assert not notifier.messages
