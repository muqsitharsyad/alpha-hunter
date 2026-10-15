"""Frozen research cohorts preserve missing outcomes and reject hindsight prices."""
import datetime as dt
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.db import session_scope
from app.models import ForwardObservation, Opportunity, Token, Wallet
from app.forward_study import resolve_due, summary

UTC = dt.timezone.utc


@pytest.fixture
def observation():
    now = dt.datetime.now(UTC)
    with session_scope() as db:
        token = Token(chain="solana", address=str(uuid4()), symbol="STUDY")
        wallet = Wallet(chain="solana", address=str(uuid4()))
        db.add_all([token, wallet]); db.flush()
        opportunity = Opportunity(token_id=token.id)
        db.add(opportunity); db.flush()
        row = ForwardObservation(opportunity_id=opportunity.id, wallet_id=wallet.id, token_id=token.id,
                                 detected_at=now-dt.timedelta(hours=24), due_at=now,
                                 next_attempt_at=now,
                                 snapshot={"observed_price": 1.0, "whale_entry_price": 0.5,
                                           "pool": {"chain": "solana", "pool_address": "pool"}})
        db.add(row); db.flush()
        ids = row.id, opportunity.id, wallet.id, token.id
    yield ids[0], now
    with session_scope() as db:
        db.query(ForwardObservation).filter_by(id=ids[0]).delete()
        db.query(Opportunity).filter_by(id=ids[1]).delete()
        db.query(Wallet).filter_by(id=ids[2]).delete()
        db.query(Token).filter_by(id=ids[3]).delete()


def test_outcome_uses_frozen_detection_price_and_is_idempotent(observation):
    oid, now = observation
    quote = SimpleNamespace(price_usd=1.1, source_ts=now)
    assert resolve_due(lambda *args: quote, now=now)["resolved"] == 1
    assert resolve_due(lambda *args: quote, now=now)["resolved"] == 0
    with session_scope() as db:
        row = db.get(ForwardObservation, oid)
        assert row.outcome["gross_return_pct"] == pytest.approx(10)
        assert row.snapshot["observed_price"] == 1.0


def test_late_price_is_missing_and_stays_in_denominator(observation):
    oid, now = observation
    def forbidden(*args):
        raise AssertionError("late outcome must not fetch a hindsight price")
    assert resolve_due(forbidden, now=now+dt.timedelta(minutes=16))["missing"] == 1
    with session_scope() as db:
        assert db.get(ForwardObservation, oid).status == "missing"
    report = summary()
    assert report["missing"] >= 1
    assert report["decision"] == "INSUFFICIENT EVIDENCE"


def test_failed_price_is_deferred_not_zero_return(observation):
    oid, now = observation
    assert resolve_due(lambda *args: None, now=now)["deferred"] == 1
    with session_scope() as db:
        row = db.get(ForwardObservation, oid)
        assert row.status == "pending"
        assert row.outcome == {}
        assert row.next_attempt_at.replace(tzinfo=UTC) > now
