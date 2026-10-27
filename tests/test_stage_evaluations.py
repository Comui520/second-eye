from datetime import datetime, timezone

from goodprice.crawler.offers import parse_offer_candidates
from goodprice.models import Listing, OfferEvaluationRecord, OfferObservation
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.services.task_service import TaskService


def fixture(factory):
    task = TaskService(factory).create_task({"keyword": "phone", "target_variant": "256GB", "min_condition_score": 7})
    with factory() as session:
        listing = Listing(platform="xianyu", external_id="fixture", title="phone", price=99, url="x", task_id=task.id)
        session.add(listing)
        session.commit()
    candidates = parse_offer_candidates('''<main><div data-sku-id="128" data-attributes='{"容量":"128GB"}' data-price="1200"></div>
        <div data-sku-id="256" data-attributes='{"容量":"256GB"}' data-price="1500"></div></main>''')
    return task, listing, candidates


def test_evaluation_joins_coordinator_transaction_without_partial_commits(session_factory):
    task, listing, candidates = fixture(session_factory)
    with session_factory() as session:
        ids = OfferPipeline(session_factory).evaluate_listing(listing.id, task.id, candidates,
            datetime.now(timezone.utc), session=session, expected_version=task.version, ready=False)
        assert ids and not any(session.get(OfferEvaluationRecord, i).payload["eligible"] for i in ids)
        session.rollback()
    with session_factory() as session:
        assert session.query(OfferObservation).count() == 0
        assert session.query(OfferEvaluationRecord).count() == 0


def test_scoped_offer_score_does_not_borrow_another_sku_or_shared_sample(session_factory):
    task, listing, candidates = fixture(session_factory)
    pipeline = OfferPipeline(session_factory)
    result = {"sku:256": {"condition_score": 8, "visual_scope": "sku:256", "requirement_match": True}}
    ids = pipeline.evaluate_listing(listing.id, task.id, candidates, datetime.now(timezone.utc), per_offer_results=result)
    with session_factory() as session:
        assert [session.get(OfferEvaluationRecord, i).payload["eligible"] for i in ids] == [False, True]
    result["sku:256"]["visual_scope"] = "shared"
    ids = pipeline.evaluate_listing(listing.id, task.id, candidates, datetime.now(timezone.utc), per_offer_results=result)
    with session_factory() as session:
        assert not any(session.get(OfferEvaluationRecord, i).payload["eligible"] for i in ids)

def test_shared_numeric_score_is_hidden_even_without_minimum_threshold(session_factory):
    task, listing, candidates = fixture(session_factory)
    TaskService(session_factory).update_task(task.id, {"min_condition_score": 0})
    ids = OfferPipeline(session_factory).evaluate_listing(listing.id, task.id, candidates, datetime.now(timezone.utc),
        per_offer_results={"sku:256": {"condition_score": 9, "visual_scope": "shared"}})
    with session_factory() as session:
        assert all(session.get(OfferEvaluationRecord, i).payload["condition_score"] is None for i in ids)
