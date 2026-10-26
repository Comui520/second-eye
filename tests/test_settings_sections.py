import pytest
from fastapi.testclient import TestClient

from goodprice.main import build_app
from goodprice.services.settings_service import SettingsService
from goodprice.web.settings_sections import save_section


def test_section_only_changes_its_own_checkboxes_and_secrets(session_factory, base_settings):
    service = SettingsService(session_factory, base_settings)
    service.set_many({"feishu_enabled": "1", "vision_enabled": "1", "llm_api_key": "private"})
    save_section(service, "models", {"llm_model": "model", "llm_api_key": ""})
    assert service.get().feishu_enabled is True
    assert service.get().vision_enabled is False
    assert service.get().llm_api_key == "private"
    with pytest.raises(ValueError):
        save_section(service, "models", {"database_url": "sqlite:///evil"})
    with pytest.raises(ValueError):
        save_section(service, "models", {"clear_llm_api_key": "1"})
    save_section(service, "models", {"clear_llm_api_key": "1", "confirm_clear": "1"})
    assert service.get().llm_api_key == ""


def test_invalid_category_save_is_atomic_and_reports_error(session_factory, base_settings):
    app = build_app(base_settings, session_factory, with_scheduler=False)
    client = TestClient(app)
    response = client.post("/settings/sections/notifications", data={"gotify_priority": "invalid", "gotify_token": "do-not-echo"})
    assert response.status_code == 422
    assert "do-not-echo" not in response.text
    assert app.state.settings_service.get().gotify_token == ""
    html = client.get("/settings").text
    assert '/settings/sections/account' in html
    assert '/settings/sections/models' in html
    assert '/settings/sections/notifications' in html


def test_explicit_clear_overrides_environment_secret(session_factory, base_settings):
    service = SettingsService(session_factory, base_settings.model_copy(update={"llm_api_key": "env-secret"}))
    service.set_many({"llm_api_key": "override"})
    returned = service.set_many({"llm_api_key": ""})
    assert returned.llm_api_key == service.get().llm_api_key == "env-secret"
    save_section(service, "models", {"clear_llm_api_key": "1", "confirm_clear": "1"})
    assert service.get().llm_api_key == ""
