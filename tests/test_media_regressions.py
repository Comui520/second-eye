import logging
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from goodprice.main import build_app
from goodprice.media.budget import MediaBudget
from goodprice.media.download import MediaDownloader
from goodprice.media.types import MediaCandidate
from goodprice.models import Listing, MediaAsset
from goodprice.services.task_service import TaskService


def test_signed_download_does_not_log_credentials(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    budget = MediaBudget(tmp_path, 1024, 60, safety_bytes=0)
    downloader = MediaDownloader(budget, httpx.MockTransport(lambda r: httpx.Response(200, content=b"file")), lambda h,p: ["93.184.216.34"])
    downloader.download(MediaCandidate("one", "image", "https://cdn.example/a?token=offline-secret"), 10, 10)
    assert "offline-secret" not in caplog.text


def test_failed_range_releases_cache_pin(session_factory, base_settings):
    import asyncio

    from starlette.requests import Request

    from goodprice.web.media_routes import media_file
    app = build_app(base_settings, session_factory, with_scheduler=False)
    root = base_settings.data_root / "media-cache"
    budget = MediaBudget(root, 1024, 60, safety_bytes=0)
    path = root / "a.jpg"
    path.write_bytes(b"jpeg")
    with session_factory() as session:
        listing = Listing(platform="x", external_id="one", title="x", url="x", price=1)
        session.add(listing)
        session.flush()
        asset = MediaAsset(listing_id=listing.id, key="one", kind="image", relative_path="a.jpg", status="ready")
        session.add(asset)
        session.commit()
    scope = {"type": "http", "method": "GET", "headers": [(b"range", b"bytes=999999-")], "app": app}
    response = media_file(asset.id, Request(scope))
    events = []
    async def send(event): events.append(event)
    async def receive(): return {"type": "http.request"}
    asyncio.run(response(scope, receive, send))
    assert events[0]["status"] == 416
    assert budget.state.pins.get(path.resolve(), 0) == 0


class Media:
    score = 8
    failure = None
    calls = 0
    force = False

    def record(self, *args): pass
    def analyze(self, *args, **kwargs):
        self.calls += 1
        self.force = kwargs.get("force", False)
        if self.failure:
            raise self.failure
        return {"condition_score": self.score, "media_cache_key": str(self.score)}


def test_provider_timeout_is_unverified_and_following_listing_processed(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service
    task = TaskService(session_factory).create_task({"keyword": "x"})
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item("one"), _item("two")]))
    media = Media()
    media.failure = httpx.ReadTimeout("private provider details")
    crawl.media_service = media
    crawl.run_task(task.id)
    with session_factory() as session:
        rows = session.query(Listing).all()
        assert len(rows) == 2
        assert all(row.condition_score is None for row in rows)


def test_same_url_new_media_score_invalidates_ranking_and_manual_reanalysis_forces(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service
    task = TaskService(session_factory).create_task({"keyword": "x"})
    crawl, _, _ = _service(session_factory, base_settings, adapter=FakeAdapter([_item()]))
    crawl.media_service = media = Media()
    crawl.run_task(task.id)
    with session_factory() as session:
        row = session.query(Listing).one()
        old = row.satisfaction
        listing_id = row.id
    media.score = 2
    crawl.run_task(task.id)
    with session_factory() as session:
        assert session.get(Listing, listing_id).satisfaction < old
    crawl.reanalyze_listing(listing_id)
    assert media.force


def test_pending_price_still_gets_independent_media_analysis(session_factory, base_settings):
    from test_crawl_service import FakeAdapter, _item, _service
    class Inquiry(FakeAdapter):
        def fetch_detail(self, url):
            from datetime import datetime, timezone

            from goodprice.offers.types import OfferCandidate, PriceClaim
            return replace(super().fetch_detail(url), offers=[OfferCandidate("default", {}, claims=(
                PriceClaim("description", "inquiry", "价格请咨询", datetime.now(timezone.utc), "description"),))])
    task = TaskService(session_factory).create_task({"keyword": "x"})
    crawl, _, _ = _service(session_factory, base_settings, adapter=Inquiry([_item()]))
    crawl.media_service = media = Media()
    crawl.run_task(task.id)
    assert media.calls == 1
    with session_factory() as session:
        assert session.query(Listing).one().value_score is None


def test_scoreless_evidence_and_pending_image_gallery_visible(session_factory, base_settings):
    app = build_app(base_settings, session_factory, with_scheduler=False)
    with session_factory() as session:
        row = Listing(platform="xianyu", external_id="one", title="x", price=1, url="x",
            image_urls=["https://cdn.example/a?token=private"],
            condition_detail={"observations": [{"text": "屏幕有划痕", "evidence_ids": ["frame1"]}],
                              "conflicts": [{"text": "描述与画面冲突", "evidence_ids": ["frame1"]}]})
        session.add(row)
        session.flush()
        asset = MediaAsset(listing_id=row.id, key="source", kind="image", source_url=row.image_urls[0], status="pending")
        session.add(asset)
        session.commit()
    html = TestClient(app).get(f"/listings/{row.id}").text
    assert f'src="/media/{asset.id}"' in html
    assert "token=private" not in html
    assert "屏幕有划痕" in html and "描述与画面冲突" in html and "frame1" in html
