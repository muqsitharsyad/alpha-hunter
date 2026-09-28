"""Engine/session helpers. Works on Postgres (prod) and SQLite (tests/dev fallback)."""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine = None
_SessionLocal = None


def engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = os.environ.get("ALPHAHUNTER_DATABASE_URL") or get_settings().database_url
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}

        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _fk_on(dbapi_conn, _rec):  # pragma: no cover - trivial
                c = dbapi_conn
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA busy_timeout=15000")
                c.execute("PRAGMA foreign_keys=ON")

        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def session_factory():
    if _SessionLocal is None:
        engine()
    return _SessionLocal


@contextmanager
def session_scope():
    s: Session = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db(drop: bool = False) -> None:
    from . import models  # noqa: F401

    if drop:
        models.Base.metadata.drop_all(engine())
    models.Base.metadata.create_all(engine())