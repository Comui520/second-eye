import sys
from pathlib import Path

import pytest

import goodprice.models  # noqa: F401 -- register tables before fixture creation
from goodprice.config import Settings
from goodprice.db import Base, make_session_factory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    # A test app must never inherit the developer's real cookies or API keys.
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    from datetime import datetime, timezone

    from goodprice.resources import ResourceSnapshot
    monkeypatch.setattr("goodprice.execution.protection.detect_resources", lambda:
        ResourceSnapshot(8, 16 * 1024**3, 8 * 1024**3, "host", (), datetime.now(timezone.utc)))


@pytest.fixture
def tmp_db(tmp_path):
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def session_factory(tmp_db):
    factory = make_session_factory(tmp_db)
    Base.metadata.create_all(factory().get_bind())
    return factory


@pytest.fixture
def base_settings(tmp_db, tmp_path):
    return Settings(
        database_url=tmp_db,
        data_root=tmp_path / "data",
        _env_file=None,
        default_crawl_interval_minutes=20,
        default_crawl_jitter_minutes=0,
    )
