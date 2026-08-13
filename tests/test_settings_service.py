from goodprice.config import Settings
from goodprice.services.settings_service import RuntimeSettings, SettingsService


def test_defaults_without_overrides(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    settings = service.get()
    assert isinstance(settings, RuntimeSettings)
    assert settings.default_crawl_interval_minutes == 20
    assert settings.xianyu_cookie == ""


def test_set_many_persists_and_merges(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    service.set_many({"xianyu_cookie": "a=1", "llm_model": "gpt-4o-mini"})
    settings = service.get()
    assert settings.xianyu_cookie == "a=1"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.default_crawl_jitter_minutes == 0  # env 默认值仍在


def test_empty_value_clears_override(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    service.set_many({"xianyu_cookie": "a=1"})
    service.set_many({"xianyu_cookie": ""})
    assert service.get().xianyu_cookie == ""


def test_round2_settings_persist(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    service.set_many({"wecom_webhook": "https://x/send?key=abc", "vision_model": "glm-4v-flash"})
    settings = service.get()
    assert settings.wecom_webhook == "https://x/send?key=abc"
    assert settings.vision_model == "glm-4v-flash"
    assert settings.serverchan_enabled is True


def test_round5_toggle_persist(session_factory, base_settings):
    service = SettingsService(session_factory, base=base_settings)
    service.set_many({"serverchan_enabled": "0", "vision_enabled": "0"})
    settings = service.get()
    assert settings.serverchan_enabled is False
    assert settings.vision_enabled is False
    assert settings.wecom_robot_enabled is True
