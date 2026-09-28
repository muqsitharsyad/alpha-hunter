"""Copy feasibility risk model (PRD 11.2) + hard gates (PRD 15.2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import get_settings


@dataclass
class SizingInput:
    equity: float
    our_size: float
    whale_trade_size: float
    pool_depth: float
    volatility_bps: float = 300.0
    signal_age_s: float = 0.0
    price_move_since: float = 0.0  # pct (0.03 = +3%)


def expected_slippage_bps(our_size: float, pool_depth: float, volatility_bps: float = 300.0) -> float:
    """Conservative ceiling consistent with PaperExecution.fill_price, plus volatility buffer."""
    if pool_depth <= 0:
        return 10_000.0  # infeasible
    impact = min((our_size / pool_depth) * 2.0 * 10_000.0, 2500.0)
    vol_term = min(max(volatility_bps, 0.0), 1200.0) * 0.05
    return max(5.0, impact + vol_term)


@dataclass
class FeasibilityResult:
    feasible: bool
    slippage_bps: float
    reasons: list[str]
    details: dict[str, Any]


class CopyFeasibility:
    """PRD 11.2. Hard gate: infeasible => reject (or resize, handled by caller)."""

    def __init__(self) -> None:
        self.s = get_settings()

    def evaluate(self, inp: SizingInput) -> FeasibilityResult:
        s = self.s
        vol = inp.volatility_bps if inp.volatility_bps > 0 else 300.0
        slip = expected_slippage_bps(inp.our_size, inp.pool_depth, vol)
        reasons: list[str] = []
        ok = True

        if slip > s.max_slippage_bps:
            ok = False
            reasons.append(f"slippage {slip:.0f} bps > max {s.max_slippage_bps:.0f}")
        if inp.pool_depth < s.depth_multiple * inp.our_size:
            ok = False
            reasons.append(f"pool depth ${inp.pool_depth:,.0f} < {s.depth_multiple:.0f}x our size ${inp.our_size:,.0f}")
        if inp.signal_age_s > s.max_signal_age_s:
            ok = False
            reasons.append(f"signal age {inp.signal_age_s:.0f}s > max {s.max_signal_age_s}s")
        if inp.price_move_since > s.max_chase_pct / 100.0:
            ok = False
            reasons.append(f"price moved {inp.price_move_since*100:.2f}% > max chase {s.max_chase_pct:.1f}%")
        if inp.whale_trade_size > 0 and inp.our_size > s.max_pct_of_whale_size * inp.whale_trade_size:
            ok = False
            reasons.append(f"our size ${inp.our_size:,.0f} > {s.max_pct_of_whale_size*100:.0f}% of whale ${inp.whale_trade_size:,.0f}")

        return FeasibilityResult(
            feasible=ok, slippage_bps=round(slip, 1), reasons=reasons,
            details={"volatility_bps": vol, "pool_depth": inp.pool_depth,
                     "our_size": inp.our_size, "signal_age_s": inp.signal_age_s,
                     "price_move_since_pct": round(inp.price_move_since * 100, 2),
                     "slippage_bps": round(slip, 1), "feasible": ok,
                     "reasons": list(reasons)},
        )

    def max_feasible_size(self, pool_depth: float, volatility_bps: float = 300.0) -> float:
        """Largest size whose modeled impact stays under max_slippage_bps."""
        s = self.s
        if pool_depth <= 0:
            return 0.0
        vol_term = min(max(volatility_bps, 0.0), 1200.0) * 0.05
        budget = s.max_slippage_bps - vol_term
        if budget <= 0:
            return 0.0
        return max(0.0, budget / (2.0 * 10_000.0) * pool_depth)


def hard_gates(composite: float, *, safety_summary: str, feasibility_ok: bool,
               regime: str, liquidity_ok: bool, risk_ok: bool, consensus_ok: bool,
               divergence_ok: bool, data_fresh: bool,
               safety_severe: list[str] | None = None) -> tuple[bool, list[str]]:
    """PRD 15.2. All gates must pass regardless of composite. Returns (ok, failures).

    Failures are human-readable and name the exact check (PRD 18: surface rejection reasons).
    """
    failures: list[str] = []
    if not data_fresh:
        failures.append("stale market data (TTL exceeded)")
    if safety_summary != "PASS":
        detail = f" ({', '.join(sorted(safety_severe))})" if safety_severe else ""
        failures.append(f"token safety: {safety_summary}{detail}")
    if not liquidity_ok:
        failures.append("liquidity below minimum")
    if not feasibility_ok:
        failures.append("copy feasibility failed")
    if not risk_ok:
        failures.append("risk limits breached")
    if not consensus_ok:
        failures.append("consensus minimum not met")
    if regime == "crash":
        failures.append("regime = crash (entries blocked)")
    if not divergence_ok:
        failures.append("qualified wallets net-distributing (divergence veto)")
    return (len(failures) == 0, failures)


def decidability(composite: float) -> str:
    """PRD 15.3 baselines."""
    if composite >= get_settings().composite_buy_threshold:
        return "BUY"
    if composite >= get_settings().composite_watch_threshold:
        return "WATCH"
    return "IGNORE"