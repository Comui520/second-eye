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


def test_migrate_adds_seller_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY, external_id TEXT)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        cols = {row[1] for row in session.execute(text("PRAGMA table_info(listings)"))}
    assert {"seller_uid", "seller_name", "seller_risk"} <= cols


def test_migrate_adds_block_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE sellers (id INTEGER PRIMARY KEY)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        lc = {r[1] for r in session.execute(text("PRAGMA table_info(listings)"))}
        sc = {r[1] for r in session.execute(text("PRAGMA table_info(sellers)"))}
    assert "blocked" in lc and "blocked" in sc
    assert "task_id" in lc


def test_migrate_adds_notification_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE notifications (id INTEGER PRIMARY KEY)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        cols = {r[1] for r in session.execute(text("PRAGMA table_info(notifications)"))}
    assert {"title", "content"} <= cols


def test_migrate_adds_round7_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY, external_id TEXT)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        cols = {r[1] for r in session.execute(text("PRAGMA table_info(listings)"))}
    assert {
        "status",
        "missed_count",
        "variants",
        "value_score",
        "value_batch_at",
        "best_of_batch",
        "last_notified_satisfaction",
    } <= cols


def test_migrate_adds_round8_task_columns(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE watch_tasks (id INTEGER PRIMARY KEY, keyword TEXT)"))
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        cols = {r[1] for r in session.execute(text("PRAGMA table_info(watch_tasks)"))}
    assert {"min_price", "exclude_words"} <= cols


def test_migrate_rebuilds_listings_for_per_task_unique(tmp_db):
    engine = create_engine(tmp_db)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE listings ("
                "id INTEGER PRIMARY KEY, platform TEXT, external_id TEXT, "
                "title TEXT, price FLOAT, url TEXT, "
                "CONSTRAINT uq_old UNIQUE (platform, external_id))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO listings (id, platform, external_id, title, price, url) "
                "VALUES (1, 'xianyu', '1001', 'a', 1, 'u')"
            )
        )
    factory = make_session_factory(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        session.execute(
            text(
                "INSERT INTO listings (platform, external_id, title, price, url, task_id, first_seen_at, last_seen_at) "
                "VALUES ('xianyu', '1001', 'b', 2, 'v', 7, datetime('now'), datetime('now'))"
            )
        )
        session.commit()
        assert session.execute(text("SELECT COUNT(*) FROM listings")).scalar() == 2
        cols = {r[1] for r in session.execute(text("PRAGMA table_info(listings)"))}
        assert "seller_risk" in cols  # 重建后其它列也齐全
