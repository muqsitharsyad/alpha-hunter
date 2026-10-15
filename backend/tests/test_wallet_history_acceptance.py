"""Historical wallet acceptance; synthetic accounting fixtures, not trading evidence."""
import datetime as dt
import uuid

import pytest

from app.db import session_scope
from app.models import WalletTransaction
from app.providers.base import SwapEvent
from app.wallet_intel import WalletIntelligence

UTC = dt.timezone.utc


def event(wallet, side, minute, price=10.0, qty=10.0, token="HISTORY"):
    return SwapEvent(chain="solana", tx_hash=f"{wallet}-{minute}-{side}", wallet=wallet,
                     pool_address="history-pool", token_address=token, symbol="H",
                     side=side, base_amount=qty, quote_amount=qty * price,
                     price_usd=price, value_usd=qty * price,
                     block_time=dt.datetime(2026, 1, 1, tzinfo=UTC) + dt.timedelta(minutes=minute))


def test_compute_reloads_deduplicated_history_across_instances(monkeypatch):
    monkeypatch.setattr("app.wallet_intel._now", lambda: dt.datetime(2026, 4, 1, tzinfo=UTC))
    wallet = f"history-{uuid.uuid4()}"
    buy, sell = event(wallet, "buy", 1), event(wallet, "sell", 2, 20)
    WalletIntelligence().compute_for("solana", wallet, [buy, buy])
    result = WalletIntelligence().compute_for("solana", wallet, [sell, sell])
    assert result["metrics"]["realized_pnl_pct"] == 100.0
    assert result["metrics"]["n_trades"] == 2
    assert result["metrics"]["resolved_trades"] == 1
    with session_scope() as db:
        assert db.query(WalletTransaction).filter_by(wallet_id=result["wallet_id"]).count() == 2
    replay = WalletIntelligence().compute_for("solana", wallet, [])
    assert replay["metrics"] == result["metrics"]


def test_tier_uses_reputation_and_inactivity_uses_resolved_activity(monkeypatch):
    import app.wallet_intel as module
    wallet = f"decay-{uuid.uuid4()}"
    now = dt.datetime(2026, 4, 1, tzinfo=UTC)
    monkeypatch.setattr(module, "_now", lambda: now)
    intel = WalletIntelligence()
    monkeypatch.setattr(intel.scorer, "score", lambda **kw: {
        "total": 95.0, "components": {}, "version": "test"})
    swaps = [event(wallet, side, i * 2 + j, price)
             for i in range(5) for j, side, price in [(0, "buy", 10), (1, "sell", 5)]]
    result = intel.compute_for("solana", wallet, swaps)
    assert result["tier"] == "C"  # high static base cannot override losing reputation
    metrics = result["metrics"]
    expected_age = (now - swaps[-1].block_time).total_seconds() / 86400
    assert metrics["inactivity_days"] == pytest.approx(expected_age)
    assert metrics["last_qualifying_activity"] == swaps[-1].block_time.isoformat()
    assert metrics["resolved_positions"] == 5
    assert metrics["history_qualified"] is True
    # Unmatched sale and invalid activity must not refresh the qualifying clock.
    noise = event(wallet, "sell", 90 * 1440, 10, token="UNKNOWN")
    result2 = intel.compute_for("solana", wallet, [noise])
    assert result2["metrics"]["inactivity_days"] == pytest.approx(expected_age)


def test_pool_creation_evidence_survives_scans_and_can_qualify_real_history(monkeypatch):
    from types import SimpleNamespace
    from app.wallet_intel import tier_for
    wallet = f"qualified-{uuid.uuid4()}"
    now = dt.datetime(2026, 1, 2, tzinfo=UTC)
    monkeypatch.setattr("app.wallet_intel._now", lambda: now)
    intel = WalletIntelligence()
    # Distinct fully closed positions; no mocked scores/reputation or seeded DB outcomes.
    for i in range(30):
        buy = event(wallet, "buy", i * 2, qty=1000)
        sell = event(wallet, "sell", i * 2 + 1, price=50, qty=1000)
        pool = SimpleNamespace(chain="solana", pool_address="history-pool",
                               token_address="HISTORY", pool_created_at=buy.block_time.replace(hour=0, minute=0),
                               source_ts=now)
        intel.record_swaps("solana", pool, [buy])
        intel.compute_for("solana", wallet, [sell])
    result = WalletIntelligence().compute_for("solana", wallet, [])
    metrics = result["metrics"]
    assert metrics["early_entry_ratio"] == 1.0
    assert metrics["early_entry_evidence_count"] == 30
    assert metrics["max_drawdown_pct"] is None  # no historical NAV marks
    assert metrics["avg_exit_improvement_pct"] is None  # no post-exit benchmark
    assert metrics["history_qualified"] is True
    assert result["tier"] == tier_for(result["reputation"])
    assert result["tier"] in {"A", "S"}


def test_unknown_features_and_fragmented_sales_stay_unqualified():
    wallet = f"fragmented-{uuid.uuid4()}"
    swaps = [event(wallet, "buy", 0, qty=10)]
    swaps += [event(wallet, "sell", i + 1, price=50, qty=1) for i in range(10)]
    result = WalletIntelligence().compute_for("solana", wallet, swaps)
    assert result["metrics"]["resolved_positions"] == 1
    assert result["metrics"]["history_qualified"] is False
    assert result["metrics"]["early_entry_ratio"] is None
    assert result["metrics"]["avg_exit_improvement_pct"] is None
    assert result["tier"] == "C"


def test_recent_outcomes_decay_and_cannot_overwhelm_missing_evidence():
    from app.wallet_intel import compute_reputation
    old_win = compute_reputation(50, [(180, 2.0), (0, -0.5)], 0)
    recent_win = compute_reputation(50, [(0, 2.0), (180, -0.5)], 0)
    assert recent_win["reputation"] > old_win["reputation"]
    extreme = compute_reputation(20, [(0, 1000.0)], 0)
    assert extreme["recent_score"] == 100.0
    assert extreme["reputation"] == 60.0


def test_cold_start_and_foreign_invalid_future_input_do_not_qualify(monkeypatch):
    from dataclasses import replace
    now = dt.datetime(2026, 1, 2, tzinfo=UTC)
    monkeypatch.setattr("app.wallet_intel._now", lambda: now)
    wallet = f"cold-{uuid.uuid4()}"
    buy = event(wallet, "buy", 0)
    foreign = event("different-wallet", "sell", 1, price=100)
    wrong_chain = replace(event(wallet, "sell", 2), chain="ethereum")
    invalid = event(wallet, "sell", 3, price=float("inf"))
    future = event(wallet, "sell", 10000, price=100)
    intel = WalletIntelligence()
    result = intel.compute_for("solana", wallet, [buy, foreign, wrong_chain, invalid, future])
    metrics = result["metrics"]
    assert metrics["n_trades"] == 1
    assert metrics["resolved_positions"] == 0
    assert metrics["history_qualified"] is False
    assert metrics["last_qualifying_activity"] is None
    assert metrics["inactivity_days"] is None
    assert result["tier"] == "C"
    with session_scope() as db:
        assert db.query(WalletTransaction).filter_by(wallet_id=result["wallet_id"]).count() == 1


def test_reputation_inactivity_penalty_survives_empty_rescan(monkeypatch):
    wallet = f"inactive-{uuid.uuid4()}"
    intel = WalletIntelligence()
    now = dt.datetime(2026, 1, 2, tzinfo=UTC)
    monkeypatch.setattr("app.wallet_intel._now", lambda: now)
    events = [event(wallet, side, 2 * i + j, price)
              for i in range(5) for j, side, price in [(0, "buy", 10), (1, "sell", 20)]]
    active = intel.compute_for("solana", wallet, events)
    monkeypatch.setattr("app.wallet_intel._now", lambda: now + dt.timedelta(days=100))
    inactive = WalletIntelligence().compute_for("solana", wallet, [])
    assert inactive["reputation"] == pytest.approx(active["reputation"] - 5.0)
    assert inactive["metrics"]["last_qualifying_activity"] == active["metrics"]["last_qualifying_activity"]


def test_dust_partial_sales_are_not_completed_positions():
    from app.wallet_intel import _ledger_stats
    wallet = f"dust-{uuid.uuid4()}"
    swaps = [event(wallet, "buy", 0, qty=1e-13)]
    swaps += [event(wallet, "sell", i + 1, qty=1e-14) for i in range(5)]
    assert _ledger_stats(swaps, {})["resolved_positions"] == 0


def test_reputation_history_records_qualification_and_decay_drivers():
    from app.models import WalletReputationHistory
    wallet = f"audit-{uuid.uuid4()}"
    result = WalletIntelligence().compute_for("solana", wallet, [
        event(wallet, "buy", 1), event(wallet, "sell", 2, price=20)])
    with session_scope() as db:
        history = db.query(WalletReputationHistory).filter_by(wallet_id=result["wallet_id"]).one()
        assert history.version == "r1.1"
        assert history.reputation == result["reputation"]
        assert history.drivers["resolved_positions"] == 1
        assert history.drivers["history_qualified"] is False
        assert history.drivers["inactivity_days"] == result["metrics"]["inactivity_days"]
        assert history.drivers["last_qualifying_activity"] == result["metrics"]["last_qualifying_activity"]


def test_partial_sale_wins_do_not_hide_a_losing_closed_position():
    wallet = f"loss-fragments-{uuid.uuid4()}"
    events = [event(wallet, "buy", 0, qty=10)]
    events += [event(wallet, "sell", i + 1, price=11, qty=1) for i in range(9)]
    events += [event(wallet, "sell", 10, price=0.1, qty=1)]
    result = WalletIntelligence().compute_for("solana", wallet, events)
    assert result["components"]["consistency"] == round(20 / 15, 1)
    assert result["metrics"]["position_win_rate"] == 0.0
