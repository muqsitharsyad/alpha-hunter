"""Unit tests for the scoring / gating engines (PRD 8, 9, 11, 12, 13, 14, 15, 16, 17, 19, 20, 22)."""
from __future__ import annotations

import datetime as dt

import pytest

from app.copy_feasibility import (
    CopyFeasibility,
    SizingInput,
    decidability,
    expected_slippage_bps,
    hard_gates,
)
from app.learning import Tournament, bh_fdr, deflated_sharpe, _stat
from app.providers.base import PoolSnapshot, SwapEvent, TokenMeta
from app.regime import RegimeEngine
from app.risk import EmergencySafeties, RiskEngine
from app.token_intel import TokenSafety, token_health_score
from app.wallet_intel import (
    WalletScorer,
    classify_wallet_intent,
    compute_reputation,
    consensus_strength,
    detect_consensus,
    reputation_decay_weight,
    tier_for,
)


def _swap(wallet: str, side: str, value: float, price: float = 1.0, minutes_ago: int = 1,
          token: str = "T", pool: str = "P") -> SwapEvent:
    return SwapEvent(chain="solana", tx_hash=f"tx{wallet}{side}{value}", wallet=wallet,
                     pool_address=pool, token_address=token, symbol="X", side=side,
                     base_amount=value, quote_amount=value, price_usd=price, value_usd=value,
                     block_time=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago))


def _pool(**kw) -> PoolSnapshot:
    base = dict(chain="solana", pool_address="P", token_address="T", symbol="X", name="X/Y",
                price_usd=1.0, liquidity_usd=250_000.0, fdv_usd=1e6, volume_24h_usd=5e5,
                buys_24h=500, sells_24h=400, buyers_24h=300, sellers_24h=250,
                price_change_h1=1.0, price_change_h24=5.0,
                pool_created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10))
    base.update(kw)
    return PoolSnapshot(**base)


# --- PRD 8.2 wallet score ---------------------------------------------------
def test_scorer_weights_sum_to_one():
    s = WalletScorer()
    assert pytest.approx(sum(s.weights.values()), abs=1e-9) == 1.0


def test_scorer_bounds_and_components_present():
    out = WalletScorer().score(realized_pnl_pct=50.0, win_rate=0.8, n_trades=20,
                               early_entry_ratio=0.8, avg_exit_improvement_pct=1.5,
                               concentration=20.0, max_drawdown_pct=5.0, exposure_ratio=0.4,
                               avg_trade_size_usd=3000.0, edge_single_trade_ratio=0.2)
    assert 0 <= out["total"] <= 100
    assert set(out["components"]) == {"profitability", "early_entry", "exit_timing",
                                      "risk_behavior", "consistency", "influence"}
    assert out["version"] == "w1.0"


def test_scorer_penalises_single_trade_edge():
    good = WalletScorer().score(realized_pnl_pct=40.0, win_rate=0.7, n_trades=20,
                                early_entry_ratio=0.7, avg_exit_improvement_pct=1.0,
                                concentration=20.0, max_drawdown_pct=5.0, exposure_ratio=0.4,
                                avg_trade_size_usd=2000.0, edge_single_trade_ratio=0.0)
    one_lucky = WalletScorer().score(realized_pnl_pct=40.0, win_rate=0.7, n_trades=20,
                                     early_entry_ratio=0.7, avg_exit_improvement_pct=1.0,
                                     concentration=20.0, max_drawdown_pct=5.0, exposure_ratio=0.4,
                                     avg_trade_size_usd=2000.0, edge_single_trade_ratio=1.0)
    assert one_lucky["total"] < good["total"]


def test_thin_history_is_penalised():
    thin = WalletScorer().score(realized_pnl_pct=100.0, win_rate=1.0, n_trades=2,
                                early_entry_ratio=0.9, avg_exit_improvement_pct=0.0,
                                concentration=10.0, max_drawdown_pct=1.0, exposure_ratio=0.4,
                                avg_trade_size_usd=2000.0, edge_single_trade_ratio=0.1)
    full = WalletScorer().score(realized_pnl_pct=100.0, win_rate=1.0, n_trades=30,
                                early_entry_ratio=0.9, avg_exit_improvement_pct=0.0,
                                concentration=10.0, max_drawdown_pct=1.0, exposure_ratio=0.4,
                                avg_trade_size_usd=2000.0, edge_single_trade_ratio=0.1)
    assert full["total"] > thin["total"]


# --- PRD 8.3 tiering -------------------------------------------------------
@pytest.mark.parametrize("score,tier", [(95, "S"), (90, "S"), (85, "A"), (80, "A"),
                                        (75, "B"), (70, "B"), (69, "C"), (0, "C")])
def test_tier_boundaries(score, tier):
    assert tier_for(score) == tier


# --- PRD 9 reputation ------------------------------------------------------
def test_reputation_decay_half_life_is_half():
    assert reputation_decay_weight(30.0, 30.0) == pytest.approx(0.5)
    assert reputation_decay_weight(60.0, 30.0) == pytest.approx(0.25)
    assert reputation_decay_weight(0.0, 30.0) == pytest.approx(1.0)


def test_reputation_clamped_and_penalised_by_inactivity():
    fresh = compute_reputation(80.0, [(1.0, 1.0), (1.0, 2.0)], age_days=0.0)
    stale = compute_reputation(80.0, [(1.0, 1.0), (1.0, 2.0)], age_days=90.0)
    assert 0.0 <= stale["reputation"] <= 100.0
    assert stale["reputation"] < fresh["reputation"]
    over = compute_reputation(100.0, [(100.0, 1.0)], age_days=0.0)
    assert over["reputation"] <= 100.0


def test_reputation_recent_performance_moves_result():
    strong = compute_reputation(60.0, [(3.0, 1.0), (2.5, 2.0)], age_days=1.0)
    weak = compute_reputation(60.0, [(-3.0, 1.0), (-2.5, 2.0)], age_days=1.0)
    assert strong["reputation"] > weak["reputation"]


# --- PRD 8.4 intent --------------------------------------------------------
def test_intent_accumulator_vs_distributor():
    buys = [_swap(f"w{i}", "buy", 100.0) for i in range(5)]
    sells = [_swap(f"w{i}", "sell", 100.0) for i in range(5)]
    assert classify_wallet_intent(buys) == "accumulator"
    assert classify_wallet_intent(sells) == "distributor"
    assert classify_wallet_intent([]) == "unknown"


# --- PRD 10 consensus ------------------------------------------------------
def test_consensus_requires_min_wallets_in_window():
    swaps = [_swap("a", "buy", 5000.0, minutes_ago=2), _swap("b", "buy", 4000.0, minutes_ago=3)]
    cons = detect_consensus(swaps, min_wallets=2, window_minutes=30)
    assert cons["buyers"] == 2 and cons["min_wallets_met"] is True
    late = detect_consensus([_swap("a", "buy", 5000.0, minutes_ago=200)], window_minutes=30)
    assert late["buyers"] == 0 and late["min_wallets_met"] is False


def test_consensus_strength_zero_without_buyers_and_rises_with_flow():
    assert consensus_strength({"buyers": 0}) == 0.0
    weak = consensus_strength({"buyers": 1, "sellers": 1, "buy_value_usd": 100.0, "sell_value_usd": 1000.0})
    strong = consensus_strength({"buyers": 3, "sellers": 0, "buy_value_usd": 20000.0, "sell_value_usd": 0.0})
    assert strong > weak


# --- PRD 11.2/11.3 copy feasibility ---------------------------------------
def test_slippage_grows_with_size_and_is_bounded_by_zero_depth():
    assert expected_slippage_bps(100.0, 1_000_000.0) < expected_slippage_bps(100_000.0, 1_000_000.0)
    assert expected_slippage_bps(100.0, 0.0) == 10_000.0


def test_infeasible_copy_is_rejected():
    r = CopyFeasibility().evaluate(SizingInput(equity=10_000, our_size=500_000,
                                               whale_trade_size=1e9, pool_depth=200_000,
                                               volatility_bps=300))
    assert r.feasible is False and r.reasons


def test_feasible_copy_passes_and_reports_slippage():
    r = CopyFeasibility().evaluate(SizingInput(equity=10_000, our_size=500.0,
                                               whale_trade_size=1e6, pool_depth=500_000,
                                               volatility_bps=200, signal_age_s=10,
                                               price_move_since=0.001))
    assert r.feasible is True and r.slippage_bps > 0 and r.reasons == []
    assert r.details["slippage_bps"] == r.slippage_bps
    assert r.details["feasible"] is True


def test_infeasible_copy_details_include_contract_fields():
    r = CopyFeasibility().evaluate(SizingInput(
        equity=10_000, our_size=100_000, whale_trade_size=1_000_000,
        pool_depth=500_000, volatility_bps=200, signal_age_s=10,
        price_move_since=0.001,
    ))
    assert r.feasible is False
    assert r.details["slippage_bps"] == r.slippage_bps
    assert r.details["feasible"] is False
    assert r.details["reasons"] == r.reasons


def test_whale_size_cap_and_aging_and_chasing_block_copy():
    f = CopyFeasibility()
    big = f.evaluate(SizingInput(equity=10_000, our_size=9_000, whale_trade_size=10_000,
                                 pool_depth=5_000_000, volatility_bps=100))
    assert big.feasible is False and any("whale" in x for x in big.reasons)
    old = f.evaluate(SizingInput(equity=10_000, our_size=100, whale_trade_size=1e6,
                                 pool_depth=5_000_000, signal_age_s=999_999))
    assert old.feasible is False and any("signal age" in x for x in old.reasons)
    chased = f.evaluate(SizingInput(equity=10_000, our_size=100, whale_trade_size=1e6,
                                    pool_depth=5_000_000, price_move_since=0.9))
    assert chased.feasible is False and any("price moved" in x for x in chased.reasons)


def test_max_feasible_size_is_actually_feasible():
    f = CopyFeasibility()
    size = f.max_feasible_size(200_000.0, volatility_bps=300.0)
    assert size > 0
    assert f.evaluate(SizingInput(equity=10_000, our_size=size * 0.99, whale_trade_size=1e9,
                                  pool_depth=200_000.0, volatility_bps=300.0)).feasible is True


# --- PRD 15.2/15.3 hard gates ---------------------------------------------
def test_every_hard_gate_can_block():
    kw = dict(safety_summary="PASS", feasibility_ok=True, regime="bull_expansion",
              liquidity_ok=True, risk_ok=True, consensus_ok=True, divergence_ok=True,
              data_fresh=True)
    ok, fails = hard_gates(95.0, **kw)
    assert ok and fails == []
    assert hard_gates(95.0, **{**kw, "data_fresh": False})[1][0].startswith("stale")
    assert hard_gates(95.0, **{**kw, "safety_summary": "REJECT"})[0] is False
    assert hard_gates(95.0, **{**kw, "feasibility_ok": False})[0] is False
    assert hard_gates(95.0, **{**kw, "liquidity_ok": False})[0] is False
    assert hard_gates(95.0, **{**kw, "risk_ok": False})[0] is False
    assert hard_gates(95.0, **{**kw, "consensus_ok": False})[0] is False
    assert hard_gates(95.0, **{**kw, "divergence_ok": False})[0] is False
    crash_ok, crash_fails = hard_gates(99.0, **{**kw, "regime": "crash"})
    assert crash_ok is False and any("crash" in f for f in crash_fails)


def test_decision_thresholds():
    assert decidability(95.0) == "BUY"
    assert decidability(85.0) == "BUY"
    assert decidability(80.0) == "WATCH"
    assert decidability(70.0) == "WATCH"
    assert decidability(69.9) == "IGNORE"


# --- PRD 13 anti-manipulation ---------------------------------------------
def _meta(**kw) -> TokenMeta:
    base = dict(chain="solana", address="T", symbol="X", name="X", holder_count=800,
                top10_holder_pct=18.0, deployer_holder_pct=4.0, lp_locked=True,
                mint_authority_revoked=True, freeze_authority_revoked=True,
                transfer_tax_bps=0.0, sell_simulation_ok=True)
    base.update(kw)
    return TokenMeta(**base)


def test_honeypot_is_hard_rejected():
    g = TokenSafety().check(_meta(sell_simulation_ok=False), _pool())
    assert g["summary"] == "REJECT" and "honeypot" in g["severe"]


def test_unlocked_liquidity_and_mint_authority_rejected():
    assert TokenSafety().check(_meta(lp_locked=False), _pool())["summary"] == "REJECT"
    assert TokenSafety().check(_meta(mint_authority_revoked=False), _pool())["summary"] == "REJECT"


def test_concentration_and_deployer_dump_rejected():
    assert "top10_concentration" in TokenSafety().check(_meta(top10_holder_pct=75.0), _pool())["severe"]
    assert "deployer_dump_risk" in TokenSafety().check(_meta(deployer_holder_pct=30.0), _pool())["severe"]


def test_fake_volume_rejected_and_clean_token_passes():
    bad = TokenSafety().check(_meta(holder_count=50), _pool(liquidity_usd=1_000.0, volume_24h_usd=500_000.0))
    assert bad["summary"] == "REJECT"
    assert TokenSafety().check(_meta(), _pool())["summary"] == "PASS"


def test_thin_pool_flagged_as_mev_exposure():
    g = TokenSafety().check(_meta(), _pool(liquidity_usd=20_000.0, volume_24h_usd=10_000.0))
    assert "mev_sandwich_exposure" in g["warn"]


# --- PRD 12.3 token health ------------------------------------------------
def test_health_score_bounds_and_components():
    h = token_health_score(_pool(), _meta(), {"buyers": 3, "sellers": 0, "net_flow_usd": 9000.0})
    assert 0 <= h["total"] <= 100
    assert "liquidity" in h["components"] and "security" in h["components"]
    empty = token_health_score(None, None, None)
    assert empty["total"] == 0.0


def test_health_score_drops_for_unsafe_token():
    safe = token_health_score(_pool(), _meta(), None)["total"]
    unsafe = token_health_score(_pool(), _meta(top10_holder_pct=80.0, lp_locked=False), None)["total"]
    assert unsafe < safe


# --- PRD 16.4 risk / sizing ----------------------------------------------
def test_position_sizing_respects_risk_budget_and_caps():
    r = RiskEngine().position_size(equity=10_000, entry_price=100.0, stop_price=95.0,
                                   max_risk_per_trade_pct=1.0, risk_profile="balanced",
                                   pool_depth=1_000_000.0)
    assert r["risk_usd"] <= 10_000 * 0.01 + 1e-6
    assert r["notional"] <= 10_000 * 0.20 + 1e-6
    assert r["qty"] > 0


def test_position_size_zero_without_stop_distance():
    assert RiskEngine().position_size(10_000, 100.0, 100.0, 1.0)["qty"] == 0.0


def test_risk_profile_changes_max_position_pct():
    rk = RiskEngine()
    assert rk.max_position_pct("conservative") < rk.max_position_pct("balanced") < rk.max_position_pct("aggressive")


def test_crash_regime_blocks_sizing():
    r = RiskEngine().position_size(10_000, 100.0, 95.0, 2.0, regime_cop=0.0)
    assert r["qty"] == 0.0


def test_daily_loss_and_position_count_limits_block_entries():
    rk = RiskEngine()
    blocked, why = rk.check_entry_limits(equity=9_400, day_start_equity=10_000, open_notional=0,
                                         max_positions=5, open_positions=0)
    assert blocked is False and any("daily loss" in w for w in why)
    blocked2, why2 = rk.check_entry_limits(equity=10_000, day_start_equity=10_000, open_notional=0,
                                           max_positions=2, open_positions=2)
    assert blocked2 is False and any("position count" in w for w in why2)
    ok, _ = rk.check_entry_limits(10_000, 10_000, 0, 5, 0)
    assert ok is True


# --- PRD 22 emergency brakes ---------------------------------------------
def test_emergency_brakes_fire_per_trigger():
    eb = EmergencySafeties()
    assert eb.evaluate(daily_pnl_pct=-6.0, portfolio_equity=9_400, drawdown_pct=0.0,
                       provider_error_rate=0.0, feed_stale=False, regime="sideways")["emergency"] is True
    assert eb.evaluate(daily_pnl_pct=0.0, portfolio_equity=7_900, drawdown_pct=21.0,
                       provider_error_rate=0.0, feed_stale=False, regime="sideways")["emergency"] is True
    assert eb.evaluate(daily_pnl_pct=0.0, portfolio_equity=10_000, drawdown_pct=0.0,
                       provider_error_rate=0.9, feed_stale=False, regime="sideways")["emergency"] is True
    assert eb.evaluate(daily_pnl_pct=0.0, portfolio_equity=10_000, drawdown_pct=0.0,
                       provider_error_rate=0.0, feed_stale=True, regime="sideways")["emergency"] is True
    assert eb.evaluate(daily_pnl_pct=0.0, portfolio_equity=10_000, drawdown_pct=0.0,
                       provider_error_rate=0.0, feed_stale=False, regime="crash")["emergency"] is True
    calm = eb.evaluate(daily_pnl_pct=0.5, portfolio_equity=10_100, drawdown_pct=1.0,
                       provider_error_rate=0.0, feed_stale=False, regime="bull_expansion")
    assert calm["emergency"] is False and calm["action"] == "none"


# --- PRD 14 regime --------------------------------------------------------
def test_regime_classifies_crash_on_sharp_drawdown():
    rising = [100.0 * (1.004 ** i) for i in range(80)]
    regime, feats, _ = RegimeEngine().classify(rising)
    assert regime in ("bull_expansion", "early_bull")
    crashing = [100.0 * (1.004 ** i) for i in range(70)] + [100.0, 85.0, 70.0, 55.0, 45.0, 40.0, 38.0, 36.0, 35.0, 34.0]
    regime2, _, _ = RegimeEngine().classify(crashing)
    assert regime2 == "crash"
    assert RegimeEngine().cop_level("crash") == 0.0


def test_regime_handles_insufficient_history():
    regime, _, _ = RegimeEngine().classify([1.0, 2.0])
    assert regime == "sideways"


# --- PRD 19.3 / 20.2 statistics ------------------------------------------
def test_bh_fdr_rejects_clear_and_keeps_noise():
    assert bh_fdr([0.001, 0.02, 0.5, 0.9]) == [True, True, False, False]
    assert bh_fdr([]) == []
    assert bh_fdr([0.9, 0.8]) == [False, False]


def test_deflated_sharpe_penalises_more_trials():
    few = deflated_sharpe(1.5, n_trials=2, n_obs=300)
    many = deflated_sharpe(1.5, n_trials=200, n_obs=300)
    assert many < few


def test_stat_helper_and_tournament_sample_gate():
    s = _stat([1.0, -0.5, 2.0])
    assert s["n"] == 3 and s["sum"] == pytest.approx(2.5)
    t = Tournament()
    result = t.run_tournament([{"strategy_id": 1, "name": "a", "pnls": [0.1] * 5, "regime": "bull"}])
    assert result["ranking"][0]["stat"]["n"] == 5


def test_bh_fdr_is_monotonic_step_up():
    # smallest p-value must be rejected whenever a larger one is
    decisions = bh_fdr([0.04, 0.01, 0.5])
    assert decisions[1] is True and decisions[0] is True
