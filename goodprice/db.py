from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_LISTINGS_COLUMNS = (
    "id, platform, external_id, title, price, url, image_urls, seller, location, "
    "published_at, first_seen_at, last_seen_at, condition_score, condition_detail, "
    "notified_at, description, requirement_match, requirement_reason, seller_uid, "
    "seller_name, seller_risk, blocked, satisfaction, status, missed_count, variants, "
    "value_score, value_batch_at, best_of_batch, last_notified_satisfaction, task_id"
)

_LISTINGS_DDL = """
    id INTEGER PRIMARY KEY,
    platform VARCHAR(50) NOT NULL,
    external_id VARCHAR(200) NOT NULL,
    title VARCHAR(500) NOT NULL,
    price FLOAT NOT NULL,
    url TEXT NOT NULL,
    image_urls JSON,
    seller VARCHAR(200),
    location VARCHAR(200),
    published_at DATETIME,
    first_seen_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    condition_score INTEGER,
    condition_detail JSON,
    notified_at DATETIME,
    description TEXT,
    requirement_match BOOLEAN,
    requirement_reason TEXT,
    seller_uid VARCHAR(100),
    seller_name VARCHAR(200),
    seller_risk JSON,
    blocked BOOLEAN NOT NULL DEFAULT 0,
    satisfaction FLOAT NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    missed_count INTEGER NOT NULL DEFAULT 0,
    variants JSON,
    value_score INTEGER,
    value_batch_at DATETIME,
    best_of_batch BOOLEAN NOT NULL DEFAULT 0,
    last_notified_satisfaction FLOAT,
    task_id INTEGER,
    CONSTRAINT uq_listing_task_external UNIQUE (platform, external_id, task_id),
    FOREIGN KEY (task_id) REFERENCES watch_tasks (id) ON DELETE SET NULL
"""

_NEW_LISTING_UNIQUE = ("external_id", "platform", "task_id")


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    path = database_url.removeprefix("sqlite:///")
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def make_session_factory(database_url: str) -> sessionmaker:
    _ensure_sqlite_dir(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(database_url: str) -> None:
    """建表（幂等）。"""
    from goodprice import models  # noqa: F401  确保模型注册

    factory = make_session_factory(database_url)
    Base.metadata.create_all(factory().get_bind())


def migrate_schema(session_factory) -> None:
    """幂等迁移：为已有数据库补齐新列。"""
    columns = {
        "watch_tasks": [
            ("fetch_detail", "fetch_detail BOOLEAN DEFAULT 1"),
            ("min_price", "min_price FLOAT DEFAULT 0"),
            ("exclude_words", "exclude_words TEXT"),
        ],
        "listings": [
            ("description", "description TEXT"),
            ("requirement_match", "requirement_match BOOLEAN"),
            ("requirement_reason", "requirement_reason TEXT"),
            ("seller_uid", "seller_uid TEXT"),
            ("seller_name", "seller_name TEXT"),
            ("seller_risk", "seller_risk JSON"),
            ("blocked", "blocked BOOLEAN DEFAULT 0"),
            ("satisfaction", "satisfaction FLOAT DEFAULT 0"),
            ("status", "status VARCHAR(20) DEFAULT 'active'"),
            ("missed_count", "missed_count INTEGER DEFAULT 0"),
            ("variants", "variants JSON"),
            ("value_score", "value_score INTEGER"),
            ("value_batch_at", "value_batch_at DATETIME"),
            ("best_of_batch", "best_of_batch BOOLEAN DEFAULT 0"),
            ("last_notified_satisfaction", "last_notified_satisfaction FLOAT"),
            ("task_id", "task_id INTEGER"),
        ],
        "sellers": [
            ("credit_label", "credit_label TEXT"),
            ("blocked", "blocked BOOLEAN DEFAULT 0"),
        ],
        "notifications": [
            ("title", "title TEXT"),
            ("content", "content TEXT"),
        ],
    }
    with session_factory() as session:
        existing_tables = {
            row[0]
            for row in session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        for table, cols in columns.items():
            if table not in existing_tables:
                continue
            existing = {row[1] for row in session.execute(text(f"PRAGMA table_info({table})"))}
            for col, ddl in cols:
                if col not in existing:
                    session.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        if "listings" in existing_tables and _listing_unique_columns(session) != _NEW_LISTING_UNIQUE:
            _rebuild_listings(session)
        session.commit()


def _listing_unique_columns(session):
    for row in session.execute(text("PRAGMA index_list('listings')")):
        if row[2] == 1:  # 唯一索引
            name = row[1]
            cols = [
                r[1]
                for r in session.execute(text(f"PRAGMA index_info('{name}')"))
                if r[1] is not None
            ]
            if cols:
                return tuple(sorted(cols))
    return None


def _rebuild_listings(session) -> None:
    """重建 listings 表：把全局唯一(platform, external_id)换成按任务唯一(platform, external_id, task_id)。"""
    old_cols = [r[1] for r in session.execute(text("PRAGMA table_info('listings')"))]
    new_cols = [c.strip() for c in _LISTINGS_COLUMNS.split(",")]
    # 老表缺的列先以可空形式补上，保证拷贝时列齐全
    for col in new_cols:
        if col == "id" or col in old_cols:
            continue
        ddl = _column_ddl(col)
        session.execute(text(f"ALTER TABLE listings ADD COLUMN {ddl}"))
    old_cols = [r[1] for r in session.execute(text("PRAGMA table_info('listings')"))]
    common = [c for c in new_cols if c in old_cols]
    cols_sql = ", ".join(common)

    def select_expr(col: str) -> str:
        if col in ("first_seen_at", "last_seen_at"):
            return f"COALESCE({col}, datetime('now'))"
        return col

    select_sql = ", ".join(select_expr(c) for c in common)
    session.execute(text(f"CREATE TABLE listings_new (\n{_LISTINGS_DDL}\n)"))
    session.execute(
        text(f"INSERT INTO listings_new ({cols_sql}) SELECT {select_sql} FROM listings")
    )
    session.execute(text("DROP TABLE listings"))
    session.execute(text("ALTER TABLE listings_new RENAME TO listings"))


_COLUMN_DDL: dict[str, str] = {}
for _line in _LISTINGS_DDL.strip().splitlines():
    _line = _line.strip().rstrip(",")
    if not _line or _line.startswith("CONSTRAINT") or _line.startswith("FOREIGN"):
        continue
    _name, _, _rest = _line.partition(" ")
    if _name:
        _COLUMN_DDL[_name] = _rest.strip()


def _column_ddl(col: str) -> str:
    ddl = _COLUMN_DDL.get(col, "TEXT")
    ddl = ddl.replace("PRIMARY KEY", "").replace("NOT NULL", "").strip()
    return f"{col} {ddl}".strip()
