"""Pipeline safety regressions; use independent account data, never live DB."""
import datetime as dt
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import session_scope
from app.execution import EmergencyLayer
from app.models import PaperAccount, SignalQuality, Token, User, Wallet
from app.pipeline import AlphaHunterPipeline


@pytest.fixture
def isolated_account():
    with session_scope() as db:
        user = User(email=f"pipeline-{uuid4().hex}@example.test", password_hash="disabled")
        db.add(user)
        db.flush()
        account = PaperAccount(user_id=user.id, starting_equity=1000, equity=1000,
                               peak_equity=1500, day_start_equity=1000,
                               day_key=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
        db.add(account)
        db.flush()
        account_id = account.id
    EmergencyLayer().clear_emergency()
    EmergencyLayer().set_state("running", account_id)
    yield account_id
    EmergencyLayer().clear_emergency()
    EmergencyLayer().set_state("idle")


def test_forward_return_waits_for_actual_horizon():
    now = dt.datetime.now(dt.timezone.utc)
    with session_scope() as db:
        token = Token(chain="solana", address=uuid4().hex)
        wallet = Wallet(chain="solana", address=uuid4().hex)
        db.add_all([token, wallet])
        db.flush()
        fresh = SignalQuality(token_id=token.id, wallet_id=wallet.id, entry_price=10,
                              horizon_h=24, created_at=now)
        mature = SignalQuality(token_id=token.id, wallet_id=wallet.id, entry_price=10,
                               horizon_h=24, created_at=now - dt.timedelta(hours=24, minutes=1))
        db.add_all([fresh, mature])
        db.flush()
        tid, fresh_id, mature_id = token.id, fresh.id, mature.id
    AlphaHunterPipeline()._resolve_signal_quality(tid, 11)
    with session_scope() as db:
        assert db.get(SignalQuality, fresh_id).resolved is False
        assert db.get(SignalQuality, mature_id).resolved is True
        assert db.get(SignalQuality, mature_id).forward_return_pct == pytest.approx(10)


def test_account_snapshot_preserves_peak_and_remaining_exposure():
    account = SimpleNamespace(equity=1000, day_start_equity=1000, starting_equity=1000,
                              realized_pnl=0, peak_equity=1500)
    pos = SimpleNamespace(qty=10, qty_open=3, avg_entry=10, last_price=12, notional_usd=101)
    snapshot = AlphaHunterPipeline()._account_dict(account, [pos])
    assert snapshot["peak_equity"] == 1500
    assert snapshot["exposure_pct"] == pytest.approx(3.6)


def test_emergency_is_raised_before_candidate_evaluation(monkeypatch, isolated_account):
    pipeline = AlphaHunterPipeline()
    evaluations = []

    def inspect_brake(*args, **kwargs):
        evaluations.append(EmergencyLayer().get_state()["emergency"].get("active", False))
        return None

    monkeypatch.setattr(pipeline, "_evaluate", inspect_brake)
    pipeline.scan_cycle()
    assert evaluations, "fixture must exercise candidate evaluation"
    assert all(evaluations), "drawdown brake must precede all entry decisions"


def test_regime_provider_failure_is_unknown_not_sideways(monkeypatch):
    pipeline = AlphaHunterPipeline()
    def offline(*args):
        raise RuntimeError("offline")
    monkeypatch.setattr(pipeline.adapters.market, "candles", offline)
    regime, _, _ = pipeline.current_regime()
    assert regime == "unknown"


def test_account_equity_includes_unallocated_entry_costs(isolated_account):
    from app.models import Opportunity, PaperPosition
    from app.execution import PaperExecution
    with session_scope() as db:
        token = Token(chain="solana", address=uuid4().hex)
        db.add(token)
        db.flush()
        opportunity = Opportunity(token_id=token.id)
        db.add(opportunity)
        db.flush()
        oid, tid = opportunity.id, token.id
    pid = PaperExecution().open_position(account_id=isolated_account, opportunity_id=oid,
                                        token_id=tid, chain="solana", symbol="TEST",
                                        price=1.0, qty=100, notional=100, pool_depth=100000,
                                        risk_profile="balanced", strategy_version="test@1", regime="sideways")
    with session_scope() as db:
        pos = db.get(PaperPosition, pid)
        entry_cost = pos.notional_usd - pos.avg_entry * pos.qty
    AlphaHunterPipeline()._sync_account_equity(isolated_account, [pos])
    with session_scope() as db:
        acc = db.get(PaperAccount, isolated_account)
        assert acc.equity == pytest.approx(round(acc.starting_equity - entry_cost, 2))


def test_worker_paused_still_monitors_open_positions(monkeypatch, isolated_account):
    import asyncio
    from app.main import _worker_cycle
    pipeline = AlphaHunterPipeline()
    calls = []
    monkeypatch.setattr(pipeline, "scan_cycle", lambda: calls.append("scan"))
    monkeypatch.setattr(pipeline, "refresh_positions", lambda: calls.append("monitor"))
    EmergencyLayer().set_state("paused", isolated_account)
    asyncio.run(_worker_cycle(pipeline))
    assert calls == ["monitor"]


def test_worker_scan_failure_does_not_skip_monitoring(monkeypatch, isolated_account):
    import asyncio
    from app.main import _worker_cycle
    pipeline = AlphaHunterPipeline()
    calls = []

    def fail_scan():
        raise RuntimeError("provider down")

    monkeypatch.setattr(pipeline, "scan_cycle", fail_scan)
    monkeypatch.setattr(pipeline, "refresh_positions", lambda: calls.append("monitor") or {"positions": 0})
    EmergencyLayer().set_state("running", isolated_account)
    result = asyncio.run(_worker_cycle(pipeline))
    assert calls == ["monitor"]
    assert result["monitor"] == {"positions": 0}
    assert result["errors"] and result["errors"][0].startswith("scan: RuntimeError")


def test_worker_slow_scan_times_out_without_delaying_monitor(monkeypatch, isolated_account):
    import asyncio
    import threading
    import time
    import app.main as main_module
    from app.main import _worker_cycle

    pipeline = AlphaHunterPipeline()
    calls = []
    scan_started = threading.Event()
    original_timeout = main_module.s.scan_timeout_s

    def slow_scan():
        scan_started.set()
        time.sleep(0.2)
        calls.append("scan")

    monkeypatch.setattr(main_module.s, "scan_timeout_s", 0.05)
    monkeypatch.setattr(pipeline, "scan_cycle", slow_scan)
    monkeypatch.setattr(pipeline, "refresh_positions", lambda: calls.append("monitor") or {"positions": 0})
    EmergencyLayer().set_state("running", isolated_account)
    try:
        result = asyncio.run(_worker_cycle(pipeline))
    finally:
        main_module.s.scan_timeout_s = original_timeout
    assert result["monitor"] == {"positions": 0}
    assert any(err.startswith("scan: TimeoutError") for err in result["errors"])
    assert scan_started.is_set()
    assert calls[0] == "monitor"


def test_worker_monitor_still_runs_when_bot_pauses_during_scan(monkeypatch, isolated_account):
    import asyncio
    import threading
    from app.main import _worker_cycle

    pipeline = AlphaHunterPipeline()
    calls = []
    scan_started = threading.Event()

    def scan_then_pause():
        scan_started.set()
        EmergencyLayer().set_state("paused", isolated_account)
        return {"scanned": 0}

    monkeypatch.setattr(pipeline, "scan_cycle", scan_then_pause)
    monkeypatch.setattr(pipeline, "refresh_positions", lambda: calls.append("monitor") or {"positions": 0})
    EmergencyLayer().set_state("running", isolated_account)
    result = asyncio.run(_worker_cycle(pipeline))
    assert scan_started.is_set()
    assert result["scan"] == {"scanned": 0}
    assert result["monitor"] == {"positions": 0}
    assert calls == ["monitor"]


def test_worker_shutdown_cancels_and_clears_task(monkeypatch):
    import asyncio
    import app.main as main_module

    async def blocked_worker():
        await asyncio.Event().wait()

    monkeypatch.setattr(main_module, "_worker_loop", blocked_worker)

    async def exercise():
        await main_module._start_worker()
        await asyncio.sleep(0)
        task = main_module._worker_task
        await main_module._stop_worker()
        return task

    task = asyncio.run(exercise())
    assert task is not None and task.done()
    assert task.cancelled()
    assert main_module._worker_task is None


def test_worker_cycle_cancellation_cleans_inflight_thread_tasks(monkeypatch, isolated_account):
    import asyncio
    import time
    from app.main import _worker_cycle

    pipeline = AlphaHunterPipeline()
    started = asyncio.Event()

    def slow_scan():
        time.sleep(0.05)
        return {"scanned": 0}

    monkeypatch.setattr(pipeline, "scan_cycle", slow_scan)
    monkeypatch.setattr(pipeline, "refresh_positions", lambda: {"positions": 0})
    EmergencyLayer().set_state("running", isolated_account)

    async def exercise():
        task = asyncio.create_task(_worker_cycle(pipeline))
        await asyncio.sleep(0.01)
        started.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return task

    task = asyncio.run(exercise())
    assert started.is_set()
    assert task.done() and task.cancelled()


def test_stale_and_future_pool_snapshots_are_not_fresh():
    from app.pipeline import _fresh_pool
    from app.providers.base import PoolSnapshot
    now = dt.datetime.now(dt.timezone.utc)
    pool = PoolSnapshot(chain="solana", pool_address="P", token_address="T",
                        symbol="X", name="X", price_usd=1, liquidity_usd=100000,
                        fdv_usd=1000000, volume_24h_usd=10000, buys_24h=10,
                        sells_24h=10, buyers_24h=10, sellers_24h=10,
                        price_change_h1=0, price_change_h24=0, pool_created_at=now)
    pool.source_ts = now - dt.timedelta(days=10)
    assert not _fresh_pool(pool, 120)
    pool.source_ts = now + dt.timedelta(hours=1)
    assert not _fresh_pool(pool, 120)


def test_missing_quote_flags_stale_open_position_without_fabricated_exit(monkeypatch, isolated_account):
    from app.execution import PaperExecution
    from app.models import Opportunity, PaperPosition
    with session_scope() as db:
        token = Token(chain="solana", address=uuid4().hex)
        db.add(token)
        db.flush()
        opp = Opportunity(token_id=token.id)
        db.add(opp)
        db.flush()
        oid, tid = opp.id, token.id
    pid = PaperExecution().open_position(account_id=isolated_account, opportunity_id=oid,
                                        token_id=tid, chain="solana", symbol="STALE",
                                        price=1, qty=10, notional=10, pool_depth=100000,
                                        risk_profile="balanced", strategy_version="test", regime="sideways")
    with session_scope() as db:
        db.get(PaperPosition, pid).last_price_ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    pipeline = AlphaHunterPipeline()
    monkeypatch.setattr(pipeline.adapters.market, "pool_snapshot", lambda *_: None)
    outcome = pipeline.refresh_positions()
    assert outcome["stale_positions"] == [pid]
    assert EmergencyLayer().get_state()["emergency"]["active"] is True
    with session_scope() as db:
        assert db.get(PaperPosition, pid).status == "open"


def test_mature_watch_signal_resolves_without_open_position(monkeypatch, isolated_account):
    from app.providers.base import PoolSnapshot
    now = dt.datetime.now(dt.timezone.utc)
    with session_scope() as db:
        token = Token(chain="solana", address=uuid4().hex, metadata_json={"pool_address": "P"})
        wallet = Wallet(chain="solana", address=uuid4().hex)
        db.add_all([token, wallet])
        db.flush()
        signal = SignalQuality(token_id=token.id, wallet_id=wallet.id, entry_price=1,
                               horizon_h=24, created_at=now - dt.timedelta(hours=25))
        db.add(signal)
        db.flush()
        sid = signal.id
    pool = PoolSnapshot(chain="solana", pool_address="P", token_address=token.address,
                        symbol="X", name="X", price_usd=1.2, liquidity_usd=100000,
                        fdv_usd=1e6, volume_24h_usd=10000, buys_24h=10, sells_24h=10,
                        buyers_24h=10, sellers_24h=10, price_change_h1=0,
                        price_change_h24=0, pool_created_at=now)
    pipeline = AlphaHunterPipeline()
    monkeypatch.setattr(pipeline.adapters.market, "pool_snapshot", lambda *_: pool)
    pipeline.refresh_positions()
    with session_scope() as db:
        assert db.get(SignalQuality, sid).resolved is True
