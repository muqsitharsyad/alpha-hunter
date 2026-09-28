"""Test bootstrap: isolated SQLite DB + demo adapters, no network."""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="alphahunter-test-")
os.environ["ALPHAHUNTER_DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["ALPHAHUNTER_FORCE_DEMO"] = "1"
os.environ["DEMO_MODE"] = "true"
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

import pytest  # noqa: E402

from app.db import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    init_db()
    yield


@pytest.fixture
def db_session():
    from app.db import session_scope

    with session_scope() as s:
        yield s


@pytest.fixture(autouse=True)
def _reset_open_positions():
    """The suite shares one database. Close open positions before each test so a test that
    expects a fresh entry is not defeated by the duplicate-position guard."""
    from app.db import session_scope
    from app.models import PaperPosition

    with session_scope() as db:
        db.query(PaperPosition).filter(PaperPosition.status == "open").update(
            {"status": "closed", "qty_open": 0.0, "exit_reason": "test_reset"},
            synchronize_session=False,
        )
    yield


@pytest.fixture
def pipeline():
    from app.pipeline import AlphaHunterPipeline

    return AlphaHunterPipeline()
