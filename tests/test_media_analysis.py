import base64
import json

import httpx
import pytest

from goodprice.analysis.llm import LLMClient
from goodprice.analysis.media_analysis import analyze_media, validate_evidence_references
from goodprice.media.types import MediaEvidence, MediaSelection
from goodprice.offers.types import OfferCandidate


@pytest.mark.parametrize("api_format", ["chat_completions", "responses"])
def test_data_urls_and_evidence_ids_in_both_api_formats(tmp_path, api_format):
    from test_media_processing import image_bytes
    path = tmp_path / "private-host-path.jpg"
    path.write_bytes(image_bytes())
    evidence = MediaEvidence("e1", "frame", "hash", path, "sku:one", "v1", 1500)
    verdict = {"condition_score": 8, "observations": [{"text": "轻微划痕", "evidence_ids": ["e1"]}],
               "seller_claims": [], "conflicts": [], "unverified": []}
    calls = []
    def handler(request):
        data = json.loads(request.content)
        calls.append(data)
        content = data["input"][0]["content"] if api_format == "responses" else data["messages"][1]["content"]
        image = content[1]["image_url"]
        url = image if isinstance(image, str) else image["url"]
        assert base64.b64decode(url.split(",", 1)[1]) == path.read_bytes()
        assert "e1" in content[0]["text"] and "1500" in content[0]["text"]
        assert str(tmp_path) not in request.content.decode()
        if api_format == "responses":
            return httpx.Response(200, text="data: " + json.dumps({"type": "response.output_text.delta", "delta": json.dumps(verdict)}) + "\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(verdict)}}]})
    client = LLMClient("https://model.example/v1", "fake", "vision", transport=httpx.MockTransport(handler), api_format=api_format)
    result = analyze_media(OfferCandidate("sku:one", {}), MediaSelection((evidence,)), client, "屏幕完好", 16 * 1024**2)
    assert result["condition_score"] == 8 and len(calls) == 1


def test_hallucinated_reference_and_shared_sku_score_rejected(tmp_path):
    with pytest.raises(ValueError):
        validate_evidence_references({"observations": [{"evidence_ids": ["invented"]}]}, {"real"})
    path = tmp_path / "image.jpg"
    path.write_bytes(b"jpg")
    class Client:
        def payload(self, content, system): return {"messages": content}
        def complete(self, payload, parser):
            return parser(json.dumps({"condition_score": 9, "observations": [{"text": "样品", "evidence_ids": ["one"]}]}))
    result = analyze_media(OfferCandidate("sku:one", {}), MediaSelection((MediaEvidence("one", "image", "h", path),)), Client(), "", 10000)
    assert result["condition_score"] is None
    assert "shared_media" in result["limitations"]


def test_empty_or_oversized_input_does_not_send(tmp_path):
    class Client:
        def payload(self, content, system): return {"messages": content}
        def complete(self, *args, **kwargs): raise AssertionError("model request sent")
    offer = OfferCandidate("default", {})
    assert analyze_media(offer, MediaSelection(()), Client(), "", 100)["condition_score"] is None
    path = tmp_path / "image.jpg"
    path.write_bytes(b"x" * 1000)
    with pytest.raises(ValueError, match="预算"):
        analyze_media(offer, MediaSelection((MediaEvidence("one", "image", "h", path),)), Client(), "", 100)
