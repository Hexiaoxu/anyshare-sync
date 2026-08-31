"""Database engine — SQLite (dev) or DaMeng (prod), configured via config.yaml.

Both backends go through a real SQLAlchemy engine + sqlmodel.Session (same
approach as BISHENG's core/database/connection.py), so every caller elsewhere
in the app (SyncPipeline, LogEventHandler, ScanEngine, ...) uses the same
``session.exec(select(...))`` / ``session.add()`` / ``session.commit()`` calls
regardless of which database is configured — no per-backend query shim.
"""

import logging
import os
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlmodel import Session, SQLModel

logger = logging.getLogger("models.base")

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.yaml"


def _load_db_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("database", {})


_db = _load_db_config()
_db_type = os.environ.get("SYNC_DB_TYPE", _db.get("type", "sqlite")).lower()

# ── Build engine ─────────────────────────────────────────────

if _db_type == "dameng":
    from . import dm_dialect  # noqa: F401 — registers dm+dmPython, patches DDL compiler

    # DaMeng selects the active schema via the `schema` connect kwarg (both
    # dmPython and dmAsync accept it) rather than the URL path; local_code=1
    # forces UTF-8 to avoid GBK encoding errors on non-ASCII names.
    _query = {"local_code": "1"}
    if _db.get("schema"):
        _query["schema"] = _db["schema"]

    _url = URL.create(
        "dm+dmPython",
        username=_db.get("user", "SYSDBA"),
        password=_db.get("password", "SYSDBA"),
        host=_db.get("host", "127.0.0.1"),
        port=int(_db.get("port", 5236)),
        query=_query,
    )
    engine = create_engine(
        _url,
        pool_size=int(_db.get("pool_size", 5)),
        max_overflow=10,
        pool_timeout=int(_db.get("pool_timeout", 30)),
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )
    logger.info(
        "DB engine: dameng host=%s port=%s schema=%s user=%s pool_size=%s",
        _db.get("host", "127.0.0.1"), _db.get("port", 5236),
        _db.get("schema") or "(default)", _db.get("user", "SYSDBA"),
        _db.get("pool_size", 5))
else:
    sqlite_path = os.environ.get(
        "SYNC_SQLITE_PATH", _db.get("sqlite_path", "data/sync_state.db"))
    db_path = Path(__file__).parent.parent.parent / sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}", echo=False,
        connect_args={"check_same_thread": False})
    logger.info("DB engine: sqlite path=%s", db_path)


# ── Public API ───────────────────────────────────────────────

_tables_ready = False


def init_db():
    """Create any tables that don't exist yet. Idempotent and cheap after the
    first call — safe to call from hot paths (e.g. persist_event_mapping)."""
    global _tables_ready
    if _tables_ready:
        return
    SQLModel.metadata.create_all(engine)
    _tables_ready = True
    logger.info("init_db: %d table(s) ensured (create_all)",
                len(SQLModel.metadata.tables))


def get_session() -> Session:
    return Session(engine)


class Base:
    """Stub base class."""
    pass
