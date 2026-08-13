import httpx
import pytest

from goodprice.notify.base import NotificationMessage
from goodprice.notify.log import LogNotifier
from goodprice.notify.serverchan import ServerChanNotifier


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
