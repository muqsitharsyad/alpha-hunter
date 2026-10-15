"""Execution acceptance: authoritative bot controls and atomic exit learning."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.db import session_scope
from app.execution import EmergencyLayer, PaperExecution, PositionManager
from app.models import (
    BotState, LearningEvent, Opportunity, PaperAccount, PaperOrder, PaperPosition,
    PaperTrade, Token, User,
)


@pytest.fixture
def entry():
    """Bind an explicitly running bot; restore shared singleton state afterward."""
    with session_scope() as db:
        previous = db.get(BotState, 1)
        saved = {c.key: deepcopy(getattr(previous, c.key))
                 for c in BotState.__table__.columns} if previous else None
        user = User(email=f"acceptance-{uuid4()}@test.invalid", password_hash="unused")
        token = Token(chain="solana", address=str(uuid4()), symbol="ACCEPT")
        db.add_all([user, token])
        db.flush()
        account = PaperAccount(user_id=user.id, starting_equity=10000, equity=10000)
        opportunity = Opportunity(token_id=token.id, chain="solana", symbol="ACCEPT",
                                  regime="bull", features={"entry_score": 91, "flow": {"buyers": 3}})
        db.add_all([account, opportunity])
        db.flush()
        if previous is None:
            previous = BotState(id=1)
            db.add(previous)
        previous.status, previous.account_id = "running", account.id
        previous.safe_mode, previous.emergency = False, {}
        args = dict(account_id=account.id, opportunity_id=opportunity.id, token_id=token.id,
                    chain="solana", symbol="ACCEPT", price=10.0, qty=100.0, notional=1000.0,
                    pool_depth=100000.0, risk_profile="balanced", strategy_version="acceptance@1",
                    regime="bull")
    yield args
    with session_scope() as db:
        state = db.get(BotState, 1)
        if saved is None:
            if state:
                db.delete(state)
        else:
            if state is None:
                state = BotState(id=1)
                db.add(state)
            for key, value in saved.items():
                setattr(state, key, value)


def load_position(pid):
    with session_scope() as db:
        return db.get(PaperPosition, pid)


@pytest.mark.parametrize("change", ["paused", "emergency", "safe", "account", "missing"])
def test_entry_rechecks_bot_control_after_fill_calculation(entry, monkeypatch, change):
    executor = PaperExecution()
    original = executor.fill_price

    def change_while_scan_in_flight(*args, **kwargs):
        result = original(*args, **kwargs)
        with session_scope() as db:
            state = db.get(BotState, 1)
            if change == "paused":
                state.status = "paused"
            elif change == "emergency":
                state.emergency = {"active": True}
            elif change == "safe":
                state.safe_mode = True
            elif change == "account":
                state.account_id = None
            else:
                db.delete(state)
        return result

    monkeypatch.setattr(executor, "fill_price", change_while_scan_in_flight)
    with pytest.raises(ValueError, match="bot|emergency"):
        executor.open_position(**entry)
    with session_scope() as db:
        assert db.query(PaperPosition).filter_by(account_id=entry["account_id"]).count() == 0
        assert db.query(PaperOrder).filter_by(account_id=entry["account_id"]).count() == 0
        assert db.get(PaperAccount, entry["account_id"]).equity == 10000


def test_running_entry_retry_after_pause_does_not_create_another_fill(entry):
    executor = PaperExecution()
    pid = executor.open_position(**entry)
    EmergencyLayer().set_state("paused")
    assert executor.open_position(**entry) == pid
    with session_scope() as db:
        assert db.query(PaperOrder).filter_by(account_id=entry["account_id"]).count() == 1


def test_each_exit_learns_once_from_trade_id_and_frozen_entry_context(entry):
    pos = load_position(PaperExecution().open_position(**entry))
    stale = load_position(pos.id)
    with session_scope() as db:
        opportunity = db.get(Opportunity, entry["opportunity_id"])
        opportunity.features, opportunity.regime = {"entry_score": 0}, "crash"
    pm = PositionManager()
    for _ in range(65):
        pm.mark(pos, pos.avg_entry)
    pm.check_exits(pos, pos.avg_entry * 1.09, pool_depth=entry["pool_depth"])
    pm.check_exits(stale, pos.avg_entry * 1.09, pool_depth=entry["pool_depth"])
    pm.close_all(pos, pos.avg_entry * 0.8, "manual", pool_depth=entry["pool_depth"])
    assert pm.close_all(stale, pos.avg_entry, "retry", pool_depth=entry["pool_depth"]) == 0
    with session_scope() as db:
        trades = db.query(PaperTrade).filter_by(position_id=pos.id).order_by(PaperTrade.id).all()
        assert len(trades) == 2
        for trade, label in zip(trades, ["win", "loss"]):
            events = db.query(LearningEvent).filter_by(source_type="trade", source_id=str(trade.id)).all()
            assert len(events) == 1
            event = events[0]
            assert event.strategy_version == trade.strategy_version == entry["strategy_version"]
            assert event.regime == trade.regime == "bull"
            assert event.features == trade.features
            assert event.features["entry_score"] == 91
            assert event.features["flow"] == {"buyers": 3}
            assert event.outcome_label == label
            assert event.pnl_pct == pytest.approx(trade.pnl_pct)
            assert event.value == pytest.approx(trade.pnl)


def test_learning_failure_rolls_back_entire_exit(entry, monkeypatch):
    from sqlalchemy import event
    pos = load_position(PaperExecution().open_position(**entry))
    with session_scope() as db:
        before_events = db.query(LearningEvent).count()

    def fail_learning(*args):
        raise RuntimeError("learning insert failed")

    event.listen(LearningEvent, "before_insert", fail_learning)
    try:
        with pytest.raises(RuntimeError, match="learning insert failed"):
            PositionManager().close_all(pos, pos.avg_entry, "manual", pool_depth=entry["pool_depth"])
    finally:
        event.remove(LearningEvent, "before_insert", fail_learning)
    with session_scope() as db:
        assert db.get(PaperPosition, pos.id).qty_open == pos.qty
        assert db.get(PaperPosition, pos.id).status == "open"
        assert db.query(PaperTrade).filter_by(position_id=pos.id).count() == 0
        assert db.query(PaperOrder).filter_by(account_id=entry["account_id"], side="sell").count() == 0
    assert pos.status == "open"
    with session_scope() as db:
        assert db.query(LearningEvent).count() == before_events


def test_legacy_position_can_exit_during_emergency_without_fabricated_context(entry):
    pos = load_position(PaperExecution().open_position(**entry))
    with session_scope() as db:
        legacy = db.get(PaperPosition, pos.id)
        legacy.trailing_state = {}
        legacy.opportunity_id = None
    EmergencyLayer().raise_emergency([{"type": "manual"}], entry["account_id"])
    PositionManager().close_all(pos, pos.avg_entry, "manual", pool_depth=entry["pool_depth"])
    with session_scope() as db:
        trade = db.query(PaperTrade).filter_by(position_id=pos.id).one()
        learned = db.query(LearningEvent).filter_by(source_type="trade", source_id=str(trade.id)).one()
        assert trade.regime == learned.regime == "unknown"
        assert "entry_score" not in learned.features
        assert db.get(PaperPosition, pos.id).status == "closed"
