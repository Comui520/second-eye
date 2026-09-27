import json

import httpx
import pytest

from goodprice.analysis.judge import (
    JevJudger,
    build_judger,
    parse_requirement_typed,
    parse_value_typed,
)
from goodprice.analysis.jev_typesafe import TypeSafeJudger
from goodprice.analysis.llm import LLMClient
from goodprice.config import Settings
from goodprice.crawler.base import ListingData
from goodprice.models import Listing
from goodprice.services.crawl_service import CrawlService
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService


def _jev_client(handler, threshold=0.85):
    llm = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
        retry_delay=0,
    )
    return JevJudger(llm=llm, auto_threshold=threshold)


def _typed_response(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_parse_requirement_typed_confidence_inverts():
    parsed = parse_requirement_typed('{"matched": true, "probability": 0.93, "reason": "ok"}')
    assert parsed["matched"] is True
    assert parsed["probability"] == 0.93
    parsed = parse_requirement_typed('{"matched": false, "probability": 0.05, "reason": "x"}')
    assert parsed["matched"] is False
    assert parsed["probability"] == 0.05


def test_parse_requirement_typed_requires_bool_and_clamps():
    with pytest.raises(ValueError):
        parse_requirement_typed('{"matched": "yes", "probability": 1, "reason": "x"}')
    parsed = parse_requirement_typed('{"matched": true, "probability": 5, "reason": "x"}')
    assert parsed["probability"] == 1.0
    parsed = parse_requirement_typed('{"matched": true, "probability": -2, "reason": "x"}')
    assert parsed["probability"] == 0.0


def test_parse_value_typed_clamps_score():
    parsed = parse_value_typed('{"value_score": 99, "probability": 0.8, "reason": "x"}')
    assert parsed["value_score"] == 10
    with pytest.raises(ValueError):
        parse_value_typed('{"value_score": "高", "probability": 0.8, "reason": "x"}')


def test_judger_requirement_carries_confidence():
    def handler(request):
        body = json.loads(request.content)
        assert "probability" in body["messages"][0]["content"]
        return _typed_response('{"matched": true, "probability": 0.95, "reason": "符合"}')

    verdict = _jev_client(handler).analyze_requirement("t", "d", "屏幕完好")
    assert verdict.matched is True
    assert verdict.confidence == 0.95
    assert verdict.reason == "符合"


def test_judger_batch_value_single_failure_isolated():
    calls = []

    def handler(request):
        body = json.loads(request.content).get("messages")
        text = body[1]["content"][0]["text"]
        calls.append(text)
        if "1001" in text:
            return _typed_response("抱歉，无法判断")
        return _typed_response('{"value_score": 7, "probability": 0.9, "reason": "划算"}')

    result = _jev_client(handler).analyze_batch_value(
        [
            {"external_id": "1001", "title": "A", "price": 100, "condition_score": 8, "defects": [], "seller_risk": "低"},
            {"external_id": "1002", "title": "B", "price": 80, "condition_score": 8, "defects": [], "seller_risk": "低"},
        ]
    )
    assert result["scores"] == {"1002": 7}
    assert result["best"] == "1002"
    assert result["confidences"] == {"1002": 0.9}
    assert "1001" in calls[0] and "1002" in calls[1]


def test_judger_batch_best_breaks_tie_by_confidence():
    def handler(request):
        text = json.loads(request.content)["messages"][1]["content"][0]["text"]
        if "1001" in text:
            return _typed_response('{"value_score": 8, "probability": 0.6, "reason": "a"}')
        return _typed_response('{"value_score": 8, "probability": 0.9, "reason": "b"}')

    result = _jev_client(handler).analyze_batch_value(
        [
            {"external_id": "1001", "title": "A", "price": 100, "condition_score": 8, "defects": [], "seller_risk": "低"},
            {"external_id": "1002", "title": "B", "price": 100, "condition_score": 8, "defects": [], "seller_risk": "低"},
        ]
    )
    assert result["best"] == "1002"


def test_judger_batch_empty_items():
    result = _jev_client(lambda request: _typed_response("{}")).analyze_batch_value([])
    assert result == {"scores": {}, "best": None, "reasons": {}, "confidences": {}}


def test_build_judger_disabled_and_threshold_clamped():
    llm = LLMClient(base_url="", api_key="", model="")
    assert build_judger(llm) is None
    judger = build_judger(llm, jev_enabled=True, auto_threshold="abc")
    assert judger is not None
    assert judger.auto_threshold == 0.85
    judger = build_judger(llm, jev_enabled=True, auto_threshold=0.1)
    assert judger.auto_threshold == 0.5
    assert judger.enabled is False


def test_build_judger_typesafe_backend():
    judger = build_judger(
        LLMClient(base_url="", api_key="", model=""),
        jev_enabled=True,
        backend="typesafe",
        api_key="ts-key",
    )
    from goodprice.analysis.jev_typesafe import TypeSafeJudger

    assert isinstance(judger, TypeSafeJudger)
    assert judger.enabled is True
    assert build_judger(
        LLMClient(base_url="", api_key="", model=""),
        jev_enabled=True,
        backend="typesafe",
        api_key="",
    ).enabled is False


class _FakeNoulAnswer:
    def __init__(self, noul):
        self.noul = noul


class _FakeScoreAnswer:
    def __init__(self, score, confidence):
        self.score = score
        self.confidence = confidence


class _FakeSDKClient:
    def __init__(self, api_key, behaviors):
        self.api_key = api_key
        self.behaviors = behaviors  # callable(state, questions) -> dict
        self.states = []

    def system_one(self, state, questions):
        self.states.append(state)
        return type("R", (), {"answers": self.behaviors(state, questions)})()


def _typesafe_judger(behaviors):
    calls = []

    def factory(api_key):
        calls.append(api_key)
        return _FakeSDKClient(api_key, behaviors)

    judger = TypeSafeJudger(api_key="ts-key", client_factory=factory)
    return judger, calls


def test_typesafe_requirement_noul_semantics():
    def behaviors(state, questions):
        assert "商品标题" in state
        assert "verdict" in questions
        return {"verdict": _FakeNoulAnswer(0.93)}

    verdict = _typesafe_judger(behaviors)[0].analyze_requirement("t", "d", "屏幕完好")
    assert verdict.matched is True
    assert verdict.confidence == 0.93
    assert "Jev" in verdict.reason

    low = TypeSafeJudger(
        api_key="ts-key",
        client_factory=lambda key: _FakeSDKClient(
            key, lambda state, questions: {"verdict": _FakeNoulAnswer(0.07)}
        ),
    )
    verdict = low.analyze_requirement("t", "d", "屏幕完好")
    assert verdict.matched is False
    assert verdict.confidence == pytest.approx(0.93)  # 反向置信度


def test_typesafe_batch_value_score_index_offset():
    def behaviors(state, questions):
        if "1001" in state:
            return {"value": _FakeScoreAnswer(7.6, 0.9)}  # 索引空间 7.6 → 9 分
        return {"value": _FakeScoreAnswer(3.4, 0.8)}  # 索引空间 3.4 → 4 分

    result = _typesafe_judger(behaviors)[0].analyze_batch_value(
        [
            {"external_id": "1001", "title": "A", "price": 100, "condition_score": 8, "defects": [], "seller_risk": "低"},
            {"external_id": "1002", "title": "B", "price": 80, "condition_score": 8, "defects": [], "seller_risk": "低"},
        ]
    )
    assert result["scores"] == {"1001": 9, "1002": 4}
    assert result["best"] == "1001"
    assert result["confidences"] == {"1001": 0.9, "1002": 0.8}


def test_typesafe_batch_value_single_failure_isolated():
    def behaviors(state, questions):
        if "1001" in state:
            raise RuntimeError("429")
        return {"value": _FakeScoreAnswer(2.0, 0.8)}

    result = _typesafe_judger(behaviors)[0].analyze_batch_value(
        [
            {"external_id": "1001", "title": "A", "price": 100, "condition_score": 8, "defects": [], "seller_risk": "低"},
            {"external_id": "1002", "title": "B", "price": 80, "condition_score": 8, "defects": [], "seller_risk": "低"},
        ]
    )
    assert result["scores"] == {"1002": 3}
    assert result["best"] == "1002"


def test_typesafe_batch_best_tie_breaks_by_confidence():
    def behaviors(state, questions):
        if "1001" in state:
            return {"value": _FakeScoreAnswer(6.2, 0.5)}  # 索引 6 → 7 分
        return {"value": _FakeScoreAnswer(6.4, 0.95)}  # 索引 6 → 7 分，置信度更高

    result = _typesafe_judger(behaviors)[0].analyze_batch_value(
        [
            {"external_id": "1001", "title": "A", "price": 100, "condition_score": 8, "defects": [], "seller_risk": "低"},
            {"external_id": "1002", "title": "B", "price": 100, "condition_score": 8, "defects": [], "seller_risk": "低"},
        ]
    )
    assert result["scores"] == {"1001": 7, "1002": 7}
    assert result["best"] == "1002"


class FakeAdapter:
    def __init__(self, items=None):
        self.items = items or []

    def search(self, keyword):
        return self.items

    def fetch_detail(self, url):
        from goodprice.crawler.base import ListingDetail

        return ListingDetail(description="屏幕完好 电池健康", image_urls=["https://x/d.jpg"])


class FakeVision:
    def __init__(self, enabled=False):
        self.enabled = enabled


class FakeNotifier:
    def __init__(self):
        self.name = "log"
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


def _handler(typed_requirement, typed_value, legacy_requirement):
    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        if "value_score" in system:
            return typed_value(request)
        if "probability" in system:
            return typed_requirement(request)
        return legacy_requirement(request)

    return handler


def _crawl(session_factory, settings, handler):
    llm = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
        retry_delay=0,
    )
    notifier = FakeNotifier()
    crawl = CrawlService(
        session_factory=session_factory,
        adapter=FakeAdapter([_item()]),
        llm=llm,
        vision=FakeVision(),
        notifiers=[("log", notifier)],
        settings_service=SettingsService(session_factory, base=settings),
    )
    return crawl, notifier


def test_jev_enabled_routes_requirement_through_typed_layer(session_factory, tmp_db):
    settings = Settings(
        database_url=tmp_db, _env_file=None, jev_enabled=True, default_crawl_jitter_minutes=0
    )
    task = TaskService(session_factory).create_task({"keyword": "k", "condition_requirement": "屏幕完好"})

    def typed_req(request):
        return _typed_response('{"matched": true, "probability": 0.97, "reason": "符合要求"}')

    def typed_value(request):
        return _typed_response('{"value_score": 9, "probability": 0.9, "reason": "划算"}')

    def legacy(request):
        raise AssertionError("置信度足够时不应回落原模型")

    crawl, notifier = _crawl(session_factory, settings, _handler(typed_req, typed_value, legacy))
    crawl.run_task(task.id)
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.requirement_match is True
        assert "置信度" in (listing.requirement_reason or "")
        assert listing.value_score == 9
        assert listing.best_of_batch is True
    assert len(notifier.messages) == 1


def test_jev_low_confidence_falls_back_to_legacy(session_factory, tmp_db):
    settings = Settings(
        database_url=tmp_db, _env_file=None, jev_enabled=True, default_crawl_jitter_minutes=0
    )
    task = TaskService(session_factory).create_task({"keyword": "k", "condition_requirement": "屏幕完好"})

    def typed_req(request):
        return _typed_response('{"matched": false, "probability": 0.51, "reason": "不太确定"}')

    def typed_value(request):
        raise AssertionError("低置信回落时不应走到性价比判断")

    def legacy(request):
        return _typed_response('{"matched": true, "reason": "原模型判断"}')

    crawl, notifier = _crawl(session_factory, settings, _handler(typed_req, typed_value, legacy))
    crawl.run_task(task.id)
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.requirement_match is True
        assert listing.requirement_reason == "原模型判断"


def test_jev_disabled_keeps_legacy_path_only(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "k", "condition_requirement": "屏幕完好"})
    seen = []

    def legacy(request):
        seen.append(json.loads(request.content)["messages"][0]["content"])
        return _typed_response('{"matched": true, "reason": "原模型判断"}')

    crawl, _ = _crawl(
        session_factory, base_settings, _handler(lambda r: None, lambda r: None, legacy)
    )
    crawl.run_task(task.id)
    with session_factory() as session:
        listing = session.query(Listing).one()
        assert listing.requirement_match is True
    assert seen and all("probability" not in text for text in seen)
