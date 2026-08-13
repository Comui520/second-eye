import json

import httpx
import pytest

from goodprice.analysis.llm import LLMClient, parse_analysis_json


def _client(handler):
    transport = httpx.MockTransport(handler)
    return LLMClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="qwen-vl-max",
        transport=transport,
    )


def test_analyze_listing_returns_verdict():
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

    verdict = _client(handler).analyze_listing("iPhone 13", 2999, image_urls=["https://x/1.jpg"])
    assert verdict["condition_score"] == 8
    assert verdict["defects"] == ["轻微划痕"]
    assert verdict["recommended"] is True
    assert verdict["reason"] == "成色不错"


def test_analyze_disabled_without_config():
    client = LLMClient(base_url="", api_key="", model="")
    assert client.enabled is False
    with pytest.raises(RuntimeError):
        client.analyze_listing("t", 1)


def test_parse_analysis_json_tolerates_fence_and_clamps():
    raw = '```json\n{"condition_score": 99, "defects": [], "recommended": false, "reason": "x"}\n```'
    verdict = parse_analysis_json(raw)
    assert verdict["condition_score"] == 10
    assert verdict["recommended"] is False


def test_parse_analysis_json_raises_on_no_json():
    with pytest.raises(ValueError):
        parse_analysis_json("抱歉，我无法判断")
