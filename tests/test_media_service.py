import json

import httpx

from goodprice.analysis.llm import LLMClient
from goodprice.media.budget import MediaBudget
from goodprice.media.download import MediaDownloader
from goodprice.media.processing import MediaProcessor
from goodprice.models import Listing
from goodprice.offers.types import OfferCandidate
from goodprice.services.media_service import MediaService


def test_stored_media_cache_and_unknown_score_after_removed_media(session_factory, base_settings, tmp_path):
    from test_media_processing import image_bytes

    from goodprice.media.types import MediaCandidate
    budget = MediaBudget(tmp_path / "cache", 256 * 1024**2, 86400, safety_bytes=0)
    processor = MediaProcessor(budget, MediaDownloader(budget, httpx.MockTransport(lambda r: httpx.Response(200, content=image_bytes())), lambda h,p: ["93.184.216.34"]))
    service = MediaService(session_factory, processor)
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="a", title="x", price=1, url="https://item.example/a")
        session.add(listing)
        session.commit()
    service.record(listing.id, [MediaCandidate("one", "image", "https://cdn.example/a?sig=secret")])
    calls = []
    def handler(request):
        data = json.loads(request.content)
        descriptors = json.loads(data["messages"][1]["content"][0]["text"])["evidence"]
        calls.append(1)
        result = {"condition_score": 8, "observations": [{"text": "完好", "evidence_ids": [descriptors[0]["id"]]}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
    client = LLMClient("https://model.example/v1", "fake", "vision", transport=httpx.MockTransport(handler))
    offer = OfferCandidate("default", {})
    first = service.analyze(listing.id, offer, client, "", base_settings)
    service.save_result(listing.id, first)
    second = service.analyze(listing.id, offer, client, "", base_settings)
    assert first["condition_score"] == second["condition_score"] == 8
    assert len(calls) == 1
    service.analyze(listing.id, offer, client, "", base_settings, force=True)
    assert len(calls) == 2
    with session_factory() as session:
        session.get(Listing, listing.id).description = "屏幕已碎裂"
        session.commit()
    service.analyze(listing.id, offer, client, "", base_settings)
    assert len(calls) == 3
    service.record(listing.id, [])
    assert service.analyze(listing.id, offer, client, "", base_settings)["condition_score"] is None
    assert "secret" not in json.dumps(service.public_assets(listing.id))
