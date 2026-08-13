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


def test_round2_settings_defaults():
    settings = Settings(_env_file=None)
    assert settings.vision_model == "qwen-vl-max"
    assert settings.vision_base_url == ""
    assert settings.wecom_touser == "@all"
    assert settings.wecom_corpid == ""


def test_round2_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("WECOM_CORPID", "ww123")
    monkeypatch.setenv("VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    settings = Settings(_env_file=None)
    assert settings.wecom_corpid == "ww123"
    assert settings.vision_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_wecom_webhook_setting(monkeypatch):
    monkeypatch.setenv("WECOM_WEBHOOK", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc")
    assert Settings(_env_file=None).wecom_webhook == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc"
