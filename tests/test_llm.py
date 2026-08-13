import json

import httpx
import pytest

from goodprice.analysis.llm import (
    LLMClient,
    parse_analysis_json,
    parse_batch_value_json,
    parse_requirement_json,
)


def _client(handler):
    transport = httpx.MockTransport(handler)
    return LLMClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="qwen-vl-max",
        transport=transport,
        retry_delay=0,
    )


def _batch_response(items, best):
    return {
        "items": [
            {"id": item_id, "value_score": score, "reason": "横向对比"}
            for item_id, score in items
        ],
        "best": best,
    }


def test_http_error_message_includes_response_body():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "Model Not Exist: glm-xxx"}})

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        _client(handler).analyze_requirement("t", "d", "r")
    assert "Model Not Exist: glm-xxx" in str(exc_info.value)


def test_429_retries_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"error": {"code": "1305", "message": "访问量过大"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"condition_score": 7, "defects": [], "recommended": true, "reason": "ok"}'
                        }
                    }
                ]
            },
        )

    verdict = _client(handler).analyze_condition("t", 1, image_urls=["https://x/1.jpg"])
    assert verdict["condition_score"] == 7
    assert len(calls) == 3


def test_429_exhausted_raises_with_body():
    def handler(request):
        return httpx.Response(429, json={"error": {"code": "1305", "message": "访问量过大"}})

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        _client(handler).analyze_condition("t", 1, image_urls=["https://x/1.jpg"])
    assert "访问量过大" in str(exc_info.value)


def test_analyze_batch_value_returns_scores_and_best():
    def handler(request):
        body = json.loads(request.content)
        assert "1001" in body["messages"][1]["content"][0]["text"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            _batch_response([("1001", 8), ("1002", 5)], best="1001")
        )}}]})

    items = [
        {"external_id": "1001", "title": "A", "price": 100, "condition_score": 8, "defects": [], "seller_risk": "低"},
        {"external_id": "1002", "title": "B", "price": 200, "condition_score": 6, "defects": ["划痕"], "seller_risk": "中"},
    ]
    result = _client(handler).analyze_batch_value(items)
    assert result["scores"] == {"1001": 8, "1002": 5}
    assert result["best"] == "1001"
    assert result["reasons"]["1001"] == "横向对比"


def test_parse_batch_value_clamps_and_requires_best():
    parsed = parse_batch_value_json(
        '{"items": [{"id": "a", "value_score": 99, "reason": "x"}], "best": "a"}'
    )
    assert parsed["scores"]["a"] == 10
    assert parsed["best"] == "a"
    with pytest.raises(ValueError):
        parse_batch_value_json('{"items": [{"id": "a", "value_score": 5}]}')


def test_analyze_batch_value_raises_on_bad_json():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "抱歉，无法判断"}}]})

    with pytest.raises(ValueError):
        _client(handler).analyze_batch_value(
            [{"external_id": "1001", "title": "A", "price": 1, "condition_score": 5, "defects": [], "seller_risk": "低"}]
        )


def test_analyze_condition_returns_verdict():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "qwen-vl-max"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"condition_score": 8, "defects": ["轻微划痕"], "recommended": true, "reason": "成色不错"}'
                        }
                    }
                ]
            },
        )

    verdict = _client(handler).analyze_condition("iPhone 13", 2999, image_urls=["https://x/1.jpg"])
    assert verdict["condition_score"] == 8
    assert verdict["defects"] == ["轻微划痕"]
    assert verdict["recommended"] is True
    assert verdict["reason"] == "成色不错"


def test_analyze_condition_disabled_without_config():
    client = LLMClient(base_url="", api_key="", model="")
    assert client.enabled is False
    with pytest.raises(RuntimeError):
        client.analyze_condition("t", 1)


def test_parse_analysis_json_tolerates_fence_and_clamps():
    raw = '```json\n{"condition_score": 99, "defects": [], "recommended": false, "reason": "x"}\n```'
    verdict = parse_analysis_json(raw)
    assert verdict["condition_score"] == 10
    assert verdict["recommended"] is False


def test_parse_analysis_json_raises_on_no_json():
    with pytest.raises(ValueError):
        parse_analysis_json("抱歉，我无法判断")


def test_image_unsupported_falls_back_to_text_only():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        has_image = any(item.get("type") == "image_url" for item in body["messages"][1]["content"])
        calls.append(has_image)
        if has_image:
            return httpx.Response(400, json={"error": {"message": "image_url not supported"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"condition_score": 6, "defects": [], "recommended": true, "reason": "文本判断"}'
                        }
                    }
                ]
            },
        )

    verdict = _client(handler).analyze_condition("t", 1, image_urls=["https://x/1.jpg"])
    assert verdict["condition_score"] == 6
    assert calls == [True, False]  # 先带图失败，再纯文本成功


def test_other_400_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "Model Not Exist"}})

    client = _client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.analyze_condition("t", 1, image_urls=["https://x/1.jpg"])
    assert len(calls) == 1


def test_analyze_requirement_returns_verdict():
    def handler(request):
        body = json.loads(request.content)
        content = body["messages"][1]["content"]
        assert all(item.get("type") == "text" for item in content)  # 纯文本
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"matched": true, "reason": "屏幕完好，符合要求"}'
                        }
                    }
                ]
            },
        )

    verdict = _client(handler).analyze_requirement("iPhone 13", "屏幕完好", "屏幕完好")
    assert verdict == {"matched": True, "reason": "屏幕完好，符合要求"}


def test_parse_requirement_requires_bool():
    with pytest.raises(ValueError):
        parse_requirement_json('{"matched": "yes", "reason": "x"}')


def test_vision_no_text_fallback_when_disabled():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "unknown variant `image_url`"}})

    client = LLMClient(
        base_url="https://api.example.com/v1",
        api_key="k",
        model="m",
        transport=httpx.MockTransport(handler),
        allow_image_fallback=False,
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.analyze_condition("t", 1, image_urls=["https://x/1.jpg"])
    assert len(calls) == 1  # 视觉强依赖：不做纯文本降级
