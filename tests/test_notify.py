import json

import httpx
import pytest

from goodprice.notify.base import NotificationMessage
from goodprice.notify.log import LogNotifier
from goodprice.notify.serverchan import ServerChanNotifier
from goodprice.notify.wecom import WeComNotifier
from goodprice.notify.wecom_robot import WeComRobotNotifier


def test_log_notifier_logs(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        LogNotifier().send(NotificationMessage(title="t", content="c", url="u"))
    assert "通知[log]" in caplog.text


def test_serverchan_sends_form():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"code": 0, "message": "ok"})

    notifier = ServerChanNotifier(sendkey="KEY123", transport=httpx.MockTransport(handler))
    notifier.send(NotificationMessage(title="标题", content="内容", url="https://x"))
    assert captured["url"].endswith("/KEY123.send")
    assert "title=%E6%A0%87%E9%A2%98" in captured["body"]


def test_serverchan_raises_on_error_response():
    def handler(request):
        return httpx.Response(200, json={"code": 400, "message": "bad"})

    notifier = ServerChanNotifier(sendkey="KEY", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        notifier.send(NotificationMessage(title="t", content="c"))


def test_serverchan_disabled_without_key():
    assert ServerChanNotifier(sendkey="").enabled is False


def _wecom(handler, **kwargs):
    return WeComNotifier(
        corpid="ww123",
        agentid="1000002",
        secret="sec",
        touser="@all",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_wecom_send_success():
    captured = {}

    def handler(request):
        if "/gettoken" in str(request.url):
            return httpx.Response(200, json={"errcode": 0, "access_token": "TOK", "expires_in": 7200})
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0})

    notifier = _wecom(handler)
    notifier.send(NotificationMessage(title="标题", content="内容", url="https://x"))
    assert captured["body"]["touser"] == "@all"
    assert captured["body"]["agentid"] == 1000002
    assert captured["body"]["msgtype"] == "text"
    assert "标题" in captured["body"]["text"]["content"]


def test_wecom_refreshes_token_on_40014():
    token_calls = []
    send_calls = []

    def handler(request):
        if "/gettoken" in str(request.url):
            token_calls.append(1)
            return httpx.Response(200, json={"errcode": 0, "access_token": "TOK", "expires_in": 7200})
        send_calls.append(1)
        if len(send_calls) == 1:
            return httpx.Response(200, json={"errcode": 40014, "errmsg": "invalid token"})
        return httpx.Response(200, json={"errcode": 0})

    _wecom(handler).send(NotificationMessage(title="t", content="c"))
    assert len(token_calls) == 2
    assert len(send_calls) == 2


def test_wecom_60020_raises_clear_error():
    def handler(request):
        if "/gettoken" in str(request.url):
            return httpx.Response(200, json={"errcode": 0, "access_token": "TOK", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 60020, "errmsg": "not allow to access from your ip"})

    with pytest.raises(RuntimeError, match="可信 IP"):
        _wecom(handler).send(NotificationMessage(title="t", content="c"))


def test_wecom_disabled_without_config():
    assert WeComNotifier(corpid="", agentid="", secret="").enabled is False


def test_wecom_robot_send_success():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0})

    notifier = WeComRobotNotifier(
        webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
        transport=httpx.MockTransport(handler),
    )
    notifier.send(NotificationMessage(title="标题", content="内容", url="https://x"))
    assert captured["url"].startswith("https://qyapi.weixin.qq.com/cgi-bin/webhook/send")
    assert captured["body"]["msgtype"] == "text"
    assert "标题" in captured["body"]["text"]["content"]


def test_wecom_robot_93000_raises():
    def handler(request):
        return httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook"})

    notifier = WeComRobotNotifier(webhook="https://x/send?key=abc", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="移除"):
        notifier.send(NotificationMessage(title="t", content="c"))


def test_wecom_robot_93004_raises():
    def handler(request):
        return httpx.Response(200, json={"errcode": 93004, "errmsg": "frequent"})

    notifier = WeComRobotNotifier(webhook="https://x/send?key=abc", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="频繁"):
        notifier.send(NotificationMessage(title="t", content="c"))


def test_wecom_robot_disabled_without_webhook():
    assert WeComRobotNotifier(webhook="").enabled is False
