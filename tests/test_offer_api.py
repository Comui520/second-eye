from datetime import datetime, timezone

from fastapi.testclient import TestClient

from goodprice.crawler.offers import parse_offer_candidates
from goodprice.main import build_app
from goodprice.models import Listing, ManualQuote
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.task_service import TaskService


def setup_client(base_settings, session_factory):
    task = TaskService(session_factory).create_task({"keyword": "x"})
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="e", title="t", price=1,
                          url="https://invalid.test", task_id=task.id)
        session.add(listing)
        session.commit()
        listing_id = listing.id
    OfferPipeline(session_factory).evaluate_listing(listing_id, task.id,
        parse_offer_candidates('<main data-item-detail><span data-item-price>1500</span></main>'),
        datetime.now(timezone.utc))
    return TestClient(build_app(base_settings, session_factory, with_scheduler=False)), listing_id


def test_offers_api_and_manual_quote_flow(base_settings, session_factory):
    client, listing_id = setup_client(base_settings, session_factory)
    response = client.get(f"/api/listings/{listing_id}/offers")
    assert response.status_code == 200
    offer = response.json()[0]
    assert offer["effective_price_cents"] == 150000
    saved = client.post(f"/offers/{offer['id']}/quotes", json={
        "amount": "1400", "fingerprint": offer["fingerprint"], "valid_days": 7})
    assert saved.status_code == 200
    quote_id = saved.json()["id"]
    new = client.get(f"/api/listings/{listing_id}/offers").json()[0]
    assert new["effective_price_cents"] == 140000
    assert new["source"] == "manual"
    assert client.post(f"/quotes/{quote_id}/revoke", json={}).status_code == 200
    assert client.get(f"/api/listings/{listing_id}/offers").json()[0]["effective_price_cents"] == 150000
    page = client.get(f"/listings/{listing_id}")
    assert "页面标价" in page.text
    assert "确认报价" in page.text


def test_bad_quote_does_not_commit_and_missing_offer_is_404(base_settings, session_factory):
    client, listing_id = setup_client(base_settings, session_factory)
    offer = client.get(f"/api/listings/{listing_id}/offers").json()[0]
    for payload in ({"amount": "NaN"}, {"amount": "1400", "fingerprint": "stale"},
                    {"amount": "1400", "fingerprint": offer["fingerprint"], "valid_days": 0}):
        assert client.post(f"/offers/{offer['id']}/quotes", json=payload).status_code == 422
    with session_factory() as session:
        assert session.query(ManualQuote).count() == 0
    assert client.post("/offers/999/quotes", json={"amount": "100", "fingerprint": "x"}).status_code == 404
    assert client.get("/api/listings/999/offers").status_code == 404
