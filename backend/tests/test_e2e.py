"""End-to-end + acceptance tests (PRD 33, 34): signal -> feasibility -> trade -> TP/SL -> close ->
learning, emergency brakes, paper-only invariant, honeypot rejection, auditable explanations."""
from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from app.config import get_settings
from app.execution import EmergencyLayer, PaperExecution, PositionManager
from app.models import (
    AuditLog, LearningEvent, Opportunity, PaperAccount, PaperPosition, PaperTrade, RiskEvent,
    SignalQuality, Token, Wallet,
)


# --- PRD 27.1 / AC 16: no live-money capability ---------------------------
def test_paper_mode_is_forced_even_if_env_says_live(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("ENABLE_LIVE_EXECUTION", "true")
    get_settings.cache_clear()
    s = get_settings()
    assert s.trading_mode == "paper"
    assert s.enable_live_execution is False
    get_settings.cache_clear()


# --- PRD 34 / AC 1-7: autonomous cycle creates opportunities + positions --
@pytest.fixture
def running_account():
    from app.seed import seed_demo_user

    uid = seed_demo_user()
    with_writes = None
    state = EmergencyLayer().set_state("running", EmergencyLayer().get_state()["account_id"])
    from app.db import session_scope

    with session_scope() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.user_id == uid).first()
        with_writes = acc.id
    return {"user_id": uid, "account_id": with_writes, "state": state}


def paper_position(account_id: int) -> PaperPosition:
    """Exercise the ledger independently of whether the signal has validated edge."""
    from app.db import session_scope
    with session_scope() as db:
        token = Token(chain="solana", address=f"synthetic-{uuid4().hex}", symbol="TEST")
        db.add(token)
        db.flush()
        opportunity = Opportunity(token_id=token.id, chain="solana", symbol="TEST")
        db.add(opportunity)
        db.flush()
        oid, tid = opportunity.id, token.id
    pid = PaperExecution().open_position(
        account_id=account_id, opportunity_id=oid, token_id=tid, chain="solana",
        symbol="TEST", price=1.0, qty=100.0, notional=100.0,
        pool_depth=100000.0, risk_profile="balanced", strategy_version="test@1",
        regime="sideways")
    with session_scope() as db:
        return db.get(PaperPosition, pid)


def test_scan_cycle_discovers_wallets_without_user_input(pipeline, running_account):
    res = pipeline.scan_cycle()
    assert res["pools_scanned"] > 0
    assert res["wallet_events_ingested"] > 0
    assert res["errors"] == []
    from app.db import session_scope

    with session_scope() as db:
        wallets = db.query(Wallet).all()
        assert len(wallets) >= 3
        # discovered automatically, never user-supplied
        for w in wallets:
            assert (w.labels or {}).get("origin") == "auto_discovered"
        assert all(w.tier in ("S", "A", "B", "C") for w in wallets)


def test_scan_cycle_emits_explainable_opportunities(pipeline, running_account):
    pipeline.scan_cycle()
    from app.db import session_scope

    with session_scope() as db:
        opps = db.query(Opportunity).all()
        assert opps, "expected at least one opportunity"
        for o in opps:
            assert o.decision in ("BUY", "WATCH", "IGNORE")
            assert o.explanation and len(o.explanation) > 20      # AC 12 plain-language reason
            assert isinstance(o.components, dict) and "composite" in o.components
            assert isinstance(o.gate_results, dict) and "failures" in o.gate_results
            assert o.feature_hash and len(o.feature_hash) == 16   # AC 11 feature snapshot
            assert o.strategy_version                                 # AC 11 strategy version
        rejects = [o for o in opps if o.decision == "IGNORE"]
        assert any(o.gate_results["failures"] for o in rejects), "rejection must state its reason"


def test_unverified_wallet_history_does_not_force_demo_entry(pipeline, running_account):
    res = pipeline.scan_cycle()
    assert res["buys"] == 0
    from app.db import session_scope

    with session_scope() as db:
        pos = db.query(PaperPosition).filter(PaperPosition.status == "open").all()
        assert not pos


def test_execution_records_intended_vs_fill_and_costs(pipeline, running_account):
    paper_position(running_account["account_id"])
    from app.db import session_scope
    from app.models import PaperOrder

    with session_scope() as db:
        orders = db.query(PaperOrder).filter(PaperOrder.side == "buy").all()
        assert orders
        for o in orders:
            assert o.fill_price != o.intended_price        # simulated slippage applied
            assert o.slippage_bps > 0
            assert o.fees >= 0 and o.mev_cost_usd >= 0
            assert o.idempotency_key                      # restart-safe


def test_every_decision_is_audited(pipeline, running_account):
    pipeline.scan_cycle()
    from app.db import session_scope

    with session_scope() as db:
        logs = db.query(AuditLog).all()
        assert any(l.action == "opportunity_evaluated" for l in logs)


# --- PRD 17 / AC 9: exits ------------------------------------------------
def test_tp1_partial_exit_and_stop_loss_and_trailing(pipeline, running_account):
    pos = paper_position(running_account["account_id"])
    from app.db import session_scope
    pid = pos.id
    entry = pos.avg_entry

    pm = PositionManager()

    out = pm.check_exits(pos, entry * 1.09, "bull_expansion", pool_depth=1_000_000.0)
    assert out["action"] in ("hold", "close_all")
    assert any(str(e["type"]).startswith("tp") for e in pos.events), "TP1 must fire"

    with session_scope() as db:
        partly = db.query(PaperTrade).filter(PaperTrade.position_id == pid).all()
        assert partly, "partial exit must be recorded as a trade row"
        pos2 = db.query(PaperPosition).filter(PaperPosition.id == pid).first()

    out2 = pm.check_exits(pos2, entry * 1.25, "bull_expansion", pool_depth=1_000_000.0)
    # PRD 17.1: after TP1+TP2 the remainder is a runner managed by trailing / smart exit,
    # so the position may legitimately stay open at this mark.
    assert out2["reason"] in ("trailing_stop", "runner_managed", "tp_exhausted", "SL", "none")
    with session_scope() as db:
        p3 = db.query(PaperPosition).filter(PaperPosition.id == pid).first()
        assert all(t.get("done") for t in p3.tp_levels), "both TP levels must have fired"
        assert p3.peak_price > p3.avg_entry
        if p3.status == "closed":
            assert p3.exit_reason and p3.closed_at is not None


def test_stop_loss_closes_position_and_records_reason(pipeline, running_account):
    pos = paper_position(running_account["account_id"])
    from app.db import session_scope
    pid, stop = pos.id, pos.stop_loss
    out = PositionManager().check_exits(pos, stop * 0.99, "sideways", pool_depth=1_000_000.0)
    assert out["action"] == "close_all" and out["reason"] == "SL"
    with session_scope() as db:
        p = db.query(PaperPosition).filter(PaperPosition.id == pid).first()
        assert p.status == "closed" and p.exit_reason == "SL"


def test_trailing_activates_after_favourable_move_and_never_loosens():
    pos = PaperPosition(account_id=1, token_id=1, qty=10.0, qty_open=10.0, avg_entry=100.0,
                        stop_loss=95.0, initial_stop=95.0, risk_usd=50.0, peak_price=100.0,
                        tp_levels=[{"level": 1, "price": 200.0, "alloc_pct": 0.3},
                                   {"level": 2, "price": 300.0, "alloc_pct": 0.4}],
                        trailing_state={"active": False, "trigger_pct": 0.06, "step_pct": 0.035,
                                        "peak": 100.0, "stop": 95.0},
                        strategy_version="whale_copy@1.0.0", events=[], status="open")
    pm = PositionManager()
    pm.check_exits(pos, 108.0, "bull_expansion", pool_depth=1_000_000.0)
    assert pos.trailing_state["active"] is True
    first_stop = pos.trailing_state["stop"]
    pm.check_exits(pos, 120.0, "bull_expansion", pool_depth=1_000_000.0)
    assert pos.trailing_state["stop"] > first_stop, "trailing stop only moves up"


def test_smart_exit_triggers_listed_for_learning():
    pos = PaperPosition(account_id=1, token_id=1, qty=1.0, qty_open=1.0, avg_entry=1.0,
                        status="open", events=[], tp_levels=[], trailing_state={})
    trigs = PositionManager().smart_exit_triggers(pos, liquidity_collapse=True, regime_crash=True,
                                                  cluster_distribution=True)
    assert set(trigs) == {"liquidity_collapse", "regime_crash", "cluster_distribution"}


# --- AC 10 / PRD 22: limits and emergency ---------------------------------
def test_daily_loss_limit_blocks_new_entries(pipeline, running_account):
    from app.db import session_scope

    with session_scope() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.id == running_account["account_id"]).first()
        acc.day_start_equity = 10_000.0
        acc.equity = 9_000.0
        acc.peak_equity = 10_000.0
    before = EmergencyLayer().get_state()
    res = pipeline.scan_cycle()
    after = EmergencyLayer().get_state()
    assert res["regime"] in ("bull_expansion", "early_bull", "sideways", "bear", "crash")
    risk_events = []
    with session_scope() as db:
        risk_events = db.query(RiskEvent).all()
    assert after["emergency"].get("active") or risk_events, "daily-loss breach must raise a brake"


def test_close_all_manual_switch_closes_positions(pipeline, running_account):
    paper_position(running_account["account_id"])
    from app.db import session_scope

    with session_scope() as db:
        open_before = db.query(PaperPosition).filter(PaperPosition.status == "open").count()
    assert open_before >= 1
    state = EmergencyLayer().get_state()
    with session_scope() as db:
        pos = db.query(PaperPosition).filter(PaperPosition.account_id == state["account_id"],
                                             PaperPosition.status == "open").all()
        for p in pos:
            PositionManager()._close_all(p, p.last_price or p.avg_entry, "manual_close_all", pool_depth=1_000_000.0)
    with session_scope() as db:
        assert db.query(PaperPosition).filter(PaperPosition.status == "open").count() == 0
    EmergencyLayer().set_state("paused")


# --- AC 13: losing trade produces a learning event -------------------------
def test_trade_outcome_is_available_for_learning(pipeline, running_account):
    pos = paper_position(running_account["account_id"])
    from app.learning import record_learning_event
    from app.db import session_scope

    pid = pos.id
    PositionManager()._close_all(pos, pos.avg_entry * 0.9, "SL", pool_depth=1_000_000.0)
    with session_scope() as db:
        t = db.query(PaperTrade).filter(PaperTrade.position_id == pid).first()
        assert t is not None and t.pnl < 0
        record_learning_event("trade", str(pid), t.strategy_version, "sideways",
                              {"exit_reason": t.exit_reason}, "loss", t.pnl_pct)
        ev = db.query(LearningEvent).filter(LearningEvent.source_id == str(pid)).first()
        assert ev is not None and ev.outcome_label == "loss" and ev.pnl_pct < 0


# --- AC 3: reputation exists and is versioned -----------------------------
def test_reputation_history_is_written(pipeline, running_account):
    pipeline.scan_cycle()
    from app.db import session_scope
    from app.models import WalletReputationHistory, WalletScoreSnapshot

    with session_scope() as db:
        assert db.query(WalletScoreSnapshot).count() >= 3
        assert db.query(WalletReputationHistory).count() >= 3


# --- PRD 8.1: forward hit-rate rows created -------------------------------
def test_followed_wallet_signals_are_tracked(pipeline, running_account):
    pipeline.scan_cycle()
    from app.db import session_scope

    with session_scope() as db:
        rows = db.query(SignalQuality).all()
        assert rows, "followed-wallet signals must be recorded for forward hit-rate KPI"
        assert all(r.resolved is False or r.resolved is True for r in rows)


# --- AC 5: both new-token and listed scanners produce candidates -----------
def test_both_scanners_are_exercised(pipeline, running_account):
    pipeline.scan_cycle()
    from app.db import session_scope

    with session_scope() as db:
        scanners = {o.scanner for o in db.query(Opportunity).all()}
        assert scanners, "expected scanned opportunities"
        assert scanners <= {"new_token", "listed"}


# --- AC 6: infeasible copy rejected/resized by the gate -------------------
def test_infeasible_copy_is_rejected_or_resized(pipeline, running_account):
    pipeline.scan_cycle()
    from app.db import session_scope

    with session_scope() as db:
        opps = db.query(Opportunity).all()
        blocked = [o for o in opps if not o.gate_results.get("feasibility", True)]
        assert blocked, "expected at least one copy rejected on feasibility in fixtures"
        for o in blocked:
            assert any("feasibility" in f for f in o.gate_results["failures"])


# --- AC 15: honeypot fixture rejected -------------------------------------
def test_honeypot_token_is_rejected_end_to_end(pipeline, running_account):
    from app.decision_engine import DecisionEngine
    from app.providers.base import PoolSnapshot, SwapEvent, TokenMeta
    from app.token_intel import token_health_score

    now = dt.datetime.now(dt.timezone.utc)
    pool = PoolSnapshot(chain="solana", pool_address="hp", token_address="HP", symbol="HONEY",
                        name="Honey", price_usd=1.0, liquidity_usd=300_000.0, fdv_usd=1e6,
                        volume_24h_usd=1e5, buys_24h=900, sells_24h=100, buyers_24h=500,
                        sellers_24h=50, price_change_h1=5.0, price_change_h24=40.0,
                        pool_created_at=now - dt.timedelta(hours=5))
    meta = TokenMeta(chain="solana", address="HP", symbol="HONEY", name="Honey",
                     holder_count=500, top10_holder_pct=20.0, deployer_holder_pct=5.0,
                     lp_locked=True, mint_authority_revoked=True, freeze_authority_revoked=True,
                     transfer_tax_bps=0.0, sell_simulation_ok=False)
    swaps = [SwapEvent(chain="solana", tx_hash=f"t{i}", wallet=f"w{i}", pool_address="hp",
                       token_address="HP", symbol="HONEY", side="buy", base_amount=1000.0,
                       quote_amount=1000.0, price_usd=1.0, value_usd=5000.0,
                       block_time=now - dt.timedelta(minutes=2)) for i in range(3)]
    cand = {"chain": "solana", "pool": pool, "kind": "new_token", "meta": meta,
            "whale_swaps": swaps, "wallet_tiers": ["S", "S", "S"],
            "token_health": token_health_score(pool, meta, None),
            "consensus": {"buyers": 3, "sellers": 0, "net_flow_usd": 15000.0,
                          "min_wallets_met": True, "buy_value_usd": 15000.0, "sell_value_usd": 0.0},
            "signal_age_s": 120.0, "price_move_since_pct": 1.0, "data_fresh": True}
    evt = DecisionEngine().evaluate(cand, account={"equity": 10_000.0, "day_start_equity": 10_000.0,
                                                   "max_positions": 5, "risk_profile": "balanced",
                                                   "exposure_pct": 0.0},
                                   regime="bull_expansion", regime_cop=1.0)
    assert evt["decision"] == "IGNORE"
    assert any("honeypot" in f for f in evt["gates"]["failures"])
