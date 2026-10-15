"""Paper ledger regressions: real SQLite transactions, no providers or live orders."""
from uuid import uuid4

import pytest

from app.db import session_scope
from app.execution import EmergencyLayer, PaperExecution, PositionManager
from app.models import AuditLog, Opportunity, PaperAccount, PaperOrder, PaperPosition, PaperTrade, Token, User


@pytest.fixture
def entry():
    with session_scope() as db:
        user = User(email=f"ledger-{uuid4()}@test.invalid", password_hash="unused")
        token = Token(chain="solana", address=str(uuid4()), symbol="LEDGER")
        db.add_all([user, token])
        db.flush()
        account = PaperAccount(user_id=user.id, starting_equity=10000, equity=10000)
        opportunity = Opportunity(token_id=token.id, chain="solana", symbol="LEDGER")
        db.add_all([account, opportunity])
        db.flush()
        args = dict(account_id=account.id, opportunity_id=opportunity.id, token_id=token.id,
                    chain="solana", symbol="LEDGER", price=10.0, qty=100.0, notional=1000.0,
                    pool_depth=100000.0, risk_profile="balanced", strategy_version="test@1", regime="sideways")
    EmergencyLayer().clear_emergency()
    EmergencyLayer().set_state("running", args["account_id"])
    return args


def load_position(pid):
    with session_scope() as db:
        return db.get(PaperPosition, pid)


def trades(pid):
    with session_scope() as db:
        return db.query(PaperTrade).filter_by(position_id=pid).order_by(PaperTrade.id).all()


def test_tp_allocates_initial_quantity_and_final_exit_only_sells_runner(entry):
    pos = load_position(PaperExecution().open_position(**entry))
    pm = PositionManager()
    pm.check_exits(pos, pos.avg_entry * 1.09, pool_depth=entry["pool_depth"])
    assert pos.qty_open == pytest.approx(70)
    pm.check_exits(pos, pos.avg_entry * 1.20, pool_depth=entry["pool_depth"])
    assert pos.qty_open == pytest.approx(30)
    pm.close_all(pos, pos.avg_entry * 1.15, "manual", pool_depth=entry["pool_depth"])
    fills = trades(pos.id)
    assert [t.qty for t in fills] == pytest.approx([30, 40, 30])
    assert load_position(pos.id).qty_open == 0
    with session_scope() as db:
        sells = db.query(PaperOrder).filter_by(account_id=entry["account_id"],
                                                token_id=entry["token_id"], side="sell").all()
        assert [order.qty for order in sells] == pytest.approx([30, 40, 30])
        assert len({order.idempotency_key for order in sells}) == 3
    exit_fees = sum(t.exit_price * t.qty * 0.001 + PaperExecution().gas_fee_usd(entry["chain"]) for t in fills)
    assert sum(t.pnl for t in fills) == pytest.approx(sum(t.exit_price * t.qty for t in fills) - pos.notional_usd - exit_fees)


def test_detached_repeat_close_and_mark_cannot_resurrect_position(entry):
    pid = PaperExecution().open_position(**entry)
    first, stale = load_position(pid), load_position(pid)
    pm = PositionManager()
    pm.close_all(first, first.avg_entry, "manual", pool_depth=entry["pool_depth"])
    assert pm.close_all(stale, stale.avg_entry, "manual", pool_depth=entry["pool_depth"]) == 0
    pm.mark(stale, stale.avg_entry * 2)
    assert load_position(pid).status == "closed"
    assert len(trades(pid)) == 1


def test_tp_survives_event_truncation(entry):
    pos = load_position(PaperExecution().open_position(**entry))
    pm = PositionManager()
    pm.check_exits(pos, pos.avg_entry * 1.09, pool_depth=entry["pool_depth"])
    for _ in range(65):
        pm.mark(pos, pos.avg_entry * 1.09)
    pm.check_exits(pos, pos.avg_entry * 1.20, pool_depth=entry["pool_depth"])
    pm.close_all(pos, pos.avg_entry, "manual", pool_depth=entry["pool_depth"])
    assert sum(t.qty for t in trades(pos.id)) == pytest.approx(pos.qty)


def test_close_failure_rolls_back_trade_and_position(entry, monkeypatch):
    pos = load_position(PaperExecution().open_position(**entry))
    pm = PositionManager()
    original = pm._record_exit

    def fail_after_record(*args, **kwargs):
        original(*args, **kwargs)
        args[0].flush()
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(pm, "_record_exit", fail_after_record)
    with pytest.raises(RuntimeError, match="injected"):
        pm.close_all(pos, pos.avg_entry, "manual", pool_depth=entry["pool_depth"])
    assert trades(pos.id) == []
    assert load_position(pos.id).qty_open == pos.qty
    assert pos.status == "open"


def test_entry_is_idempotent_and_cannot_spend_more_than_available(entry):
    executor = PaperExecution()
    pid = executor.open_position(**entry)
    assert executor.open_position(**entry) == pid
    with session_scope() as db:
        assert db.query(PaperOrder).filter_by(opportunity_id=entry["opportunity_id"]).count() == 1
        acc = db.get(PaperAccount, entry["account_id"])
        acc.equity = 1.0
        other_token = Token(chain="solana", address=str(uuid4()), symbol="OTHER")
        db.add(other_token)
        db.flush()
        other = Opportunity(token_id=other_token.id, chain="solana", symbol="OTHER")
        db.add(other)
        db.flush()
        oid, tid = other.id, other_token.id
    modified = dict(entry, opportunity_id=oid, token_id=tid)
    with pytest.raises(ValueError, match="cash|equity"):
        executor.open_position(**modified)


def test_close_updates_equity_before_next_entry(entry):
    executor = PaperExecution()
    pos = load_position(executor.open_position(**entry))
    PositionManager().close_all(pos, pos.avg_entry * 0.1, "manual", pool_depth=entry["pool_depth"])
    with session_scope() as db:
        account = db.get(PaperAccount, entry["account_id"])
        assert account.equity < account.starting_equity
        assert account.realized_pnl < 0
        other_token = Token(chain="solana", address=str(uuid4()), symbol="OTHER")
        db.add(other_token)
        db.flush()
        other = Opportunity(token_id=other_token.id, chain="solana", symbol="OTHER")
        db.add(other)
        db.flush()
        oid, tid = other.id, other_token.id
    expensive = dict(entry, opportunity_id=oid, token_id=tid, qty=900, notional=9000)
    with pytest.raises(ValueError, match="cash|equity"):
        executor.open_position(**expensive)


def test_exit_uses_adverse_depth_slippage_and_persists_execution_evidence(entry):
    pos = load_position(PaperExecution().open_position(**entry))
    intended_price = pos.avg_entry * 1.10
    PositionManager().close_all(pos, intended_price, "manual", pool_depth=10_000.0)
    with session_scope() as db:
        trade = db.query(PaperTrade).filter_by(position_id=pos.id).one()
        order = db.query(PaperOrder).filter_by(account_id=entry["account_id"], token_id=entry["token_id"], side="sell").one()
        assert trade.exit_price < intended_price
        assert trade.slippage_bps > 0
        assert trade.features["intended_exit_price"] == pytest.approx(intended_price)
        assert order.intended_price == pytest.approx(intended_price)
        assert order.fill_price == pytest.approx(trade.exit_price)
        assert order.slippage_bps == pytest.approx(trade.slippage_bps)


def test_cannot_stack_open_position_for_same_account_and_token(entry):
    executor = PaperExecution()
    pid = executor.open_position(**entry)
    with session_scope() as db:
        other = Opportunity(token_id=entry["token_id"], chain="solana", symbol="LEDGER")
        db.add(other)
        db.flush()
        oid = other.id
    assert executor.open_position(**dict(entry, opportunity_id=oid)) == pid
    with session_scope() as db:
        assert db.query(PaperPosition).filter_by(account_id=entry["account_id"], token_id=entry["token_id"], status="open").count() == 1
