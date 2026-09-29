"""通知渠道的唯一注册表。

渠道名、启用开关字段与构造逻辑集中在此处：main.py（运行时装配）与
web/routes.py（测试发送）都从这里取，避免渠道名在多个文件里平行维护而漂移。
新增渠道只需在此追加一条 ChannelSpec，并同步 settings.html 与 Settings 字段。
"""
from dataclasses import dataclass
from typing import Callable

from goodprice.config import Settings
from goodprice.notify.base import Notifier
from goodprice.notify.feishu import FeishuNotifier
from goodprice.notify.gotify import GotifyNotifier
from goodprice.notify.serverchan import ServerChanNotifier
from goodprice.notify.wecom_robot import WeComRobotNotifier


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    enabled_field: str
    build: Callable[[Settings], Notifier]


CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec(
        "serverchan",
        "serverchan_enabled",
        lambda s: ServerChanNotifier(sendkey=s.serverchan_sendkey),
    ),
    ChannelSpec(
        "wecom_robot",
        "wecom_robot_enabled",
        lambda s: WeComRobotNotifier(webhook=s.wecom_webhook),
    ),
    ChannelSpec(
        "feishu",
        "feishu_enabled",
        lambda s: FeishuNotifier(webhook=s.feishu_webhook, secret=s.feishu_secret),
    ),
    ChannelSpec(
        "gotify",
        "gotify_enabled",
        lambda s: GotifyNotifier(
            url=s.gotify_url, token=s.gotify_token, priority=s.gotify_priority
        ),
    ),
)


def channel_names() -> set[str]:
    return {spec.name for spec in CHANNELS}


def build_channel(name: str, settings: Settings) -> Notifier | None:
    """按渠道名构造通知器；未知渠道返回 None。"""
    for spec in CHANNELS:
        if spec.name == name:
            return spec.build(settings)
    return None


def build_enabled_notifiers(settings: Settings) -> list[tuple[str, Notifier]]:
    """返回所有已启用且配置完整的 (渠道名, 通知器)，供运行时装配使用。"""
    notifiers: list[tuple[str, Notifier]] = []
    for spec in CHANNELS:
        if not getattr(settings, spec.enabled_field):
            continue
        notifier = spec.build(settings)
        if notifier.enabled:
            notifiers.append((spec.name, notifier))
    return notifiers
