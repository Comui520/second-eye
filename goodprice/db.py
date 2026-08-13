from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


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
        "watch_tasks": [("fetch_detail", "fetch_detail BOOLEAN DEFAULT 1")],
        "listings": [
            ("description", "description TEXT"),
            ("requirement_match", "requirement_match BOOLEAN"),
            ("requirement_reason", "requirement_reason TEXT"),
            ("seller_uid", "seller_uid TEXT"),
            ("seller_name", "seller_name TEXT"),
            ("seller_risk", "seller_risk JSON"),
            ("blocked", "blocked BOOLEAN DEFAULT 0"),
            ("satisfaction", "satisfaction FLOAT DEFAULT 0"),
            ("task_id", "task_id INTEGER"),
        ],
        "sellers": [
            ("credit_label", "credit_label TEXT"),
            ("blocked", "blocked BOOLEAN DEFAULT 0"),
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
        session.commit()
