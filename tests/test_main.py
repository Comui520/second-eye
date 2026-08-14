import logging

from fastapi.testclient import TestClient
from logging.handlers import RotatingFileHandler

from goodprice.main import build_app


def test_build_app_health(base_settings, session_factory):
    app = build_app(settings=base_settings, session_factory=session_factory, with_scheduler=False)
    with TestClient(app) as client:
        response = client.get("/api/stats")
    assert response.status_code == 200
    assert response.json() == {"tasks": 0, "enabled_tasks": 0, "listings": 0, "notified": 0}


def test_main_entry_importable():
    import goodprice.__main__  # noqa: F401


def test_logging_skips_file_handler_under_pytest():
    from goodprice.main import _setup_logging

    _setup_logging()
    handlers = logging.getLogger().handlers
    assert not any(isinstance(h, RotatingFileHandler) for h in handlers)
