import sys
from pathlib import Path

import pytest

from goodprice.config import Settings
from goodprice.db import Base, make_session_factory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tmp_db(tmp_path):
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def session_factory(tmp_db):
    factory = make_session_factory(tmp_db)
    Base.metadata.create_all(factory().get_bind())
    return factory


@pytest.fixture
def base_settings(tmp_db):
    return Settings(
        database_url=tmp_db,
        _env_file=None,
        default_crawl_interval_minutes=20,
        default_crawl_jitter_minutes=0,
    )
