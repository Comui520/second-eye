import socket
import threading
import time
from datetime import datetime

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from goodprice.main import build_app
from goodprice.models import Listing
from goodprice.services.task_service import TaskService


@pytest.fixture
def live_app(base_settings, session_factory):
    app = build_app(base_settings, session_factory, with_scheduler=False)
    task = TaskService(session_factory).create_task({"keyword": "offline navigation", "enabled": False})
    with session_factory() as session:
        session.add_all([Listing(platform="fixture", external_id=str(i), task_id=task.id,
            title=f"fixture {i}", price=100+i, url="https://invalid.example/fixture",
            first_seen_at=datetime(2026, 1, 1)) for i in range(205)])
        session.commit()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    try:
        yield app, f"http://{host}:{port}", task.id
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


@pytest.fixture
def browser_page(live_app):
    _, url, _ = live_app
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1100, "height": 800})
        # Only the explicitly temporary loopback application is reachable.
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(url + "/") else route.abort())
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
