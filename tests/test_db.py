from sqlalchemy import create_engine, text

from goodprice.db import make_session_factory, migrate_schema


def test_migrate_adds_new_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE watch_tasks (id INTEGER PRIMARY KEY, keyword TEXT)"))
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY, external_id TEXT, title TEXT)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        task_cols = {row[1] for row in session.execute(text("PRAGMA table_info(watch_tasks)"))}
        listing_cols = {row[1] for row in session.execute(text("PRAGMA table_info(listings)"))}
    assert "fetch_detail" in task_cols
    assert {"description", "requirement_match", "requirement_reason"} <= listing_cols


def test_migrate_is_idempotent(session_factory):
    migrate_schema(session_factory)
    migrate_schema(session_factory)
