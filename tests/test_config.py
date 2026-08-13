from goodprice.config import Settings


def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("XIANYU_COOKIE", "abc=1")
    settings = Settings(_env_file=None)
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.xianyu_cookie == "abc=1"
    assert settings.llm_base_url == ""


def test_settings_defaults():
    settings = Settings(_env_file=None)
    assert settings.app_name == "闲鱼盯价助手"
    assert settings.default_crawl_interval_minutes == 20
    assert settings.default_crawl_jitter_minutes == 10
