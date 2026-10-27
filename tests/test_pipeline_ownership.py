from datetime import datetime, timezone

import pytest

from goodprice.execution.pipeline import build_pipeline
from goodprice.execution.types import RunToken, Stage, WorkItemDTO
from goodprice.models import Listing, MediaAsset
from goodprice.services.offer_pipeline import StaleEvaluation
from goodprice.services.settings_service import SettingsService
from goodprice.services.task_service import TaskService


def test_deleting_listing_removes_its_media_records(session_factory):
    with session_factory() as session:
        listing = Listing(platform="x", external_id="one", title="one", price=1, url="x")
        session.add(listing)
        session.flush()
        session.add(MediaAsset(listing_id=listing.id, key="old-picture", kind="image"))
        session.commit()
        session.delete(listing)
        session.commit()
        assert session.query(MediaAsset).count() == 0


def test_worker_rejects_listing_reference_owned_by_another_task(session_factory, base_settings):
    from test_production_pipeline import Adapter, Provider
    service = TaskService(session_factory)
    first = service.create_task({"keyword": "first"})
    other = service.create_task({"keyword": "other"})
    with session_factory() as session:
        listing = Listing(platform="x", external_id="reused-id", title="other", price=1, url="x", task_id=other.id)
        session.add(listing)
        session.commit()
    adapter, provider = Adapter(), Provider()
    controller = build_pipeline(session_factory, SettingsService(session_factory, base_settings),
        adapter_factory=lambda: adapter.session(), provider=provider, notifier_factory=lambda: {})
    try:
        run = controller.submit(first.id)
        item = WorkItemDTO(1, RunToken(run, first.id, first.version, "owner"), Stage.ANALYZE,
            "fake", {"listing_id": listing.id, "eval_id": 1, "role": "requirement", "cache_key": "fake"},
            1, datetime.now(timezone.utc))
        with pytest.raises(StaleEvaluation):
            controller.pipeline.worker(item)
        assert provider.requirement_calls == 0
    finally:
        controller.stop(1)
