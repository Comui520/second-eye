from fastapi.testclient import TestClient

from goodprice.main import build_app
from goodprice.models import SchemaVersion


def test_recommendation_is_readonly_and_profile_requires_explicit_application(session_factory, base_settings):
    app = build_app(base_settings, session_factory, with_scheduler=False)
    with session_factory() as session:
        session.get(SchemaVersion, 1).legacy_install = True
        session.commit()
    client = TestClient(app)
    service = app.state.settings_service
    before = service.get().model_dump()
    result = client.get("/api/resources/recommendation")
    assert result.status_code == 200
    assert result.json()["recommendation"]["name"] in {"low", "balanced"}
    assert service.get().model_dump() == before
    assert client.post("/settings/resources/apply-profile", data={"name": "balanced"}).status_code == 422
    response = client.post("/settings/resources/apply-profile", data={"name": "balanced", "confirm": "1"}, follow_redirects=False)
    assert response.status_code == 303
    assert service.get().execution_model_workers == 2
    assert service.get().media_total_limit == 4
    assert service.get().video_analysis_enabled is False
    assert service.get().execution_model_rpm == before["execution_model_rpm"]
