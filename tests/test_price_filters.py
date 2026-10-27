from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from goodprice.main import build_app
from goodprice.models import Listing
from goodprice.offers.service import OfferService
from goodprice.offers.types import OfferCandidate, PriceClaim, QuoteInput
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.task_service import TaskService


def test_price_filters_are_independent_of_budget_and_update_with_quotes(session_factory, base_settings):
    task = TaskService(session_factory).create_task({"keyword": "fixture", "max_price": 50})
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        exact = Listing(platform="x", external_id="one", task_id=task.id, title="exact listing", price=1, url="x")
        inquiry = Listing(platform="x", external_id="two", task_id=task.id, title="inquiry listing", price=1, url="x")
        session.add_all([exact, inquiry])
        session.commit()
    pipeline = OfferPipeline(session_factory)
    pipeline.evaluate_listing(exact.id, task.id, [OfferCandidate("default", {}, claims=(PriceClaim("dom", "exact", "100", now, "price", amount_cents=10000),))], now)
    pipeline.evaluate_listing(inquiry.id, task.id, [OfferCandidate("default", {}, claims=(PriceClaim("description", "inquiry", "价格请咨询", now, "description"),))], now)
    client = TestClient(build_app(base_settings, session_factory, with_scheduler=False))
    assert [r["id"] for r in client.get("/api/listings?price_state=exact").json()] == [exact.id]
    assert [r["id"] for r in client.get("/api/listings?price_state=pending").json()] == [inquiry.id]
    offer = pipeline.snapshot(inquiry.id, now)[0]
    quote = OfferService(session_factory).confirm_quote(offer["id"], QuoteInput("default", 4000, now,
        now + timedelta(days=7), offer["fingerprint"]))
    assert len(client.get("/api/listings?price_state=exact").json()) == 2
    OfferService(session_factory).revoke_quote(quote)
    assert len(client.get("/api/listings?price_state=exact").json()) == 1


def test_card_effective_price_belongs_to_current_target_not_cheapest_other_sku(session_factory):
    from goodprice.crawler.offers import parse_offer_candidates
    from goodprice.services.offer_pipeline import present_listings
    task = TaskService(session_factory).create_task({"keyword": "phone", "target_variant": "256GB"})
    with session_factory() as session:
        row = Listing(platform="x", external_id="sku", task_id=task.id, title="phone", price=99, url="x")
        session.add(row)
        session.commit()
    candidates = parse_offer_candidates('''<main><div data-sku-id="128" data-attributes='{"容量":"128GB"}' data-price="1200"></div>
        <div data-sku-id="256" data-attributes='{"容量":"256GB"}' data-price="1500"></div></main>''')
    OfferPipeline(session_factory).evaluate_listing(row.id, task.id, candidates, datetime.now(timezone.utc))
    assert present_listings([row], session_factory)[0].effective_price_cents == 150000
    TaskService(session_factory).update_task(task.id, {"target_variant": "512GB"})
    assert present_listings([row], session_factory)[0].effective_price_cents is None


def test_independent_media_is_visible_for_inquiry_price_without_parent_score(session_factory):
    from goodprice.services.offer_pipeline import present_listings
    task = TaskService(session_factory).create_task({"keyword": "fixture"})
    with session_factory() as session:
        listing = Listing(platform="x", external_id="inquiry", task_id=task.id, title="fixture", price=1, url="x")
        session.add(listing)
        session.commit()
    now = datetime.now(timezone.utc)
    candidate = OfferCandidate("default", {}, claims=(PriceClaim("description", "inquiry", "请询价", now, "description"),))
    OfferPipeline(session_factory).evaluate_listing(listing.id, task.id, [candidate], now,
        per_offer_results={"default": {"condition_score": 8, "visual_scope": "default"}})
    view = present_listings([listing], session_factory)[0]
    assert view.condition_score == 8 and not view.price_confirmed
