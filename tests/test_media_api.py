from fastapi.testclient import TestClient

from goodprice.main import build_app
from goodprice.models import Listing, MediaAsset


def test_media_api_confined_to_ids_and_cache_root(session_factory, base_settings, tmp_path):
    app = build_app(base_settings, session_factory, with_scheduler=False)
    root = base_settings.data_root / "media-cache"
    root.mkdir(parents=True, exist_ok=True)
    image = root / "sample.jpg"
    image.write_bytes(b"jpeg")
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"secret")
    with session_factory() as session:
        row = Listing(platform="xianyu", external_id="one", title="x", url="x", price=1)
        session.add(row)
        session.flush()
        valid = MediaAsset(listing_id=row.id, key="one", kind="image", relative_path="sample.jpg", status="ready")
        invalid = MediaAsset(listing_id=row.id, key="two", kind="image", relative_path=str(outside), status="ready")
        session.add_all([valid, invalid])
        session.commit()
    client = TestClient(app)
    assert client.get(f"/media/{valid.id}").content == b"jpeg"
    assert client.get(f"/media/{invalid.id}").status_code == 404
    assert client.get("/media/9999").status_code == 404
    image.unlink()
    assert client.get(f"/media/{valid.id}").status_code == 404
    capabilities = client.get("/api/media/capabilities")
    assert capabilities.status_code == 200
    assert str(base_settings.data_root) not in capabilities.text


def test_increasing_inputs_requires_confirmation_and_keeps_other_settings(session_factory, base_settings):
    app = build_app(base_settings, session_factory, with_scheduler=False)
    client = TestClient(app)
    service = app.state.settings_service
    service.set_many({"media_total_limit": "4", "feishu_enabled": "1"})
    assert client.post("/settings/media", data={"media_total_limit": "6"}).status_code == 422
    assert service.get().media_total_limit == 4
    assert client.post("/settings/media", data={"media_total_limit": "6", "confirm_cost": "1"}, follow_redirects=False).status_code == 303
    assert service.get().media_total_limit == 6
    assert service.get().feishu_enabled is True
    assert client.post("/settings/media", data={"database_url": "sqlite:///evil"}).status_code == 422
    assert "费用" in client.get("/settings").text


def test_general_settings_cannot_override_media_or_deployment(session_factory, base_settings):
    app = build_app(base_settings, session_factory, with_scheduler=False)
    client = TestClient(app)
    for field, value in [("media_total_limit", "16"), ("data_root", "outside"), ("database_url", "sqlite:///outside")]:
        assert client.post("/settings", data={field: value}).status_code == 422


def test_busy_media_returns_retryable_response_without_model_or_network(session_factory, base_settings, monkeypatch):
    from contextlib import contextmanager

    from goodprice.media.budget import BudgetExceeded
    @contextmanager
    def busy(*args, **kwargs):
        raise BudgetExceeded("busy")
        yield
    monkeypatch.setattr("goodprice.media.processing.MediaProcessor.session", busy)
    app = build_app(base_settings, session_factory, with_scheduler=False)
    with session_factory() as session:
        row = Listing(platform="x", external_id="busy", title="busy", price=1, url="x")
        session.add(row)
        session.flush()
        asset = MediaAsset(listing_id=row.id, key="busy", kind="image", source_url="https://cdn.example/a.jpg")
        session.add(asset)
        session.commit()
    response = TestClient(app, raise_server_exceptions=False).get(f"/media/{asset.id}")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
