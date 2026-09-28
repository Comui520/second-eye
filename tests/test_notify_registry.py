"""通知渠道注册表的一致性守卫。

渠道名/开关字段/构造逻辑集中在 goodprice.notify.registry；本测试保证注册表、
Settings 字段与设置页模板三者不脱节。
"""
from goodprice.config import PROJECT_ROOT, Settings
from goodprice.notify.registry import (
    CHANNELS,
    build_channel,
    build_enabled_notifiers,
    channel_names,
)

SETTINGS_HTML = PROJECT_ROOT / "goodprice" / "web" / "templates" / "settings.html"


def test_channel_names_are_unique_and_expected():
    names = [spec.name for spec in CHANNELS]
    assert len(names) == len(set(names))
    assert channel_names() == {"serverchan", "wecom_robot", "feishu", "gotify"}


def test_channel_specs_point_to_settings_fields():
    for spec in CHANNELS:
        assert spec.enabled_field in Settings.model_fields, spec.name
        notifier = build_channel(spec.name, Settings(_env_file=None))
        assert notifier is not None
        assert notifier.channel == spec.name


def test_unknown_channel_returns_none():
    assert build_channel("nope", Settings(_env_file=None)) is None


def test_disabled_channels_are_skipped():
    settings = Settings(
        _env_file=None,
        serverchan_enabled=False,
        wecom_robot_enabled=False,
        feishu_enabled=False,
        gotify_enabled=False,
    )
    assert build_enabled_notifiers(settings) == []


def test_template_exposes_every_channel():
    html = SETTINGS_HTML.read_text(encoding="utf-8")
    for spec in CHANNELS:
        assert spec.name in html, f"settings.html 缺少渠道 {spec.name} 的配置区"
