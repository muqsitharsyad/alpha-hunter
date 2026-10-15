"""Operator close requires fresh executable depth, not a cached mark."""
import datetime as dt
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.db import session_scope
from app.models import PaperAccount, PaperPosition, PaperTrade, Token, User
from app.main import bot_close_all
from app.pipeline import AlphaHunterPipeline


@pytest.fixture
def holding():
    with session_scope() as db:
        user = User(email=f"close-{uuid4()}@test.invalid", password_hash="unused")
        token = Token(chain="solana", address=str(uuid4()), symbol="CLOSE")
        db.add_all([user, token]); db.flush()
        account = PaperAccount(user_id=user.id)
        db.add(account); db.flush()
        pos = PaperPosition(account_id=account.id, token_id=token.id, chain="solana",
                            symbol="CLOSE", qty=10, qty_open=10, avg_entry=1,
                            notional_usd=10, initial_stop=0.9, risk_usd=1)
        db.add(pos); db.flush()
        return user.id, pos.id


def test_manual_close_uses_fresh_quote_even_if_cached_mark_missing(holding, monkeypatch):
    monkeypatch.setattr(AlphaHunterPipeline, "executable_quote", lambda self, pos:
        SimpleNamespace(price_usd=1.2, liquidity_usd=100000,
                        source_ts=dt.datetime.now(dt.timezone.utc)))
    uid, pid = holding
    assert bot_close_all(uid=uid)["closed"] == 1
    assert bot_close_all(uid=uid)["closed"] == 0
    with session_scope() as db:
        assert db.get(PaperPosition, pid).status == "closed"
        assert db.query(PaperTrade).filter_by(position_id=pid).count() == 1


def test_manual_close_missing_quote_never_synthesizes_exit(holding, monkeypatch):
    monkeypatch.setattr(AlphaHunterPipeline, "executable_quote", lambda self, pos: None)
    uid, pid = holding
    with pytest.raises(HTTPException) as exc:
        bot_close_all(uid=uid)
    assert exc.value.status_code == 409
    with session_scope() as db:
        assert db.get(PaperPosition, pid).status == "open"
        assert db.query(PaperTrade).filter_by(position_id=pid).count() == 0
