"""Risk engine: portfolio risk, position sizing (PRD 16.4), daily-loss/exposure/concentration caps,
emergency brakes (PRD 22), and position sizing with a `SizingInput`-friendly interface."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .config import get_settings


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _effective_daily_loss_cap(max_daily_loss_pct: float | None, fallback: float) -> float:
    """Use a valid operator override, otherwise retain the global safety limit."""
    if max_daily_loss_pct is None or isinstance(max_daily_loss_pct, bool):
        return fallback
    try:
        cap = float(max_daily_loss_pct)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return cap if math.isfinite(cap) and cap > 0.0 else fallback


class RiskEngine:
    def __init__(self) -> None:
        self.s = get_settings()

    def daily_pnl_pct(self, account: dict[str, Any]) -> float:
        """account dict with keys: equity, day_start_equity (all positive cash-backed)."""
        start = account.get("day_start_equity") or account.get("starting_equity") or 0.0
        if start <= 0:
            return 0.0
        return (account.get("equity", 0.0) - start) / start * 100.0

    def exposure_pct(self, open_notional: float, equity: float) -> float:
        return (open_notional / equity * 100.0) if equity else 0.0

    def max_position_pct(self, risk_profile: str = "balanced") -> float:
        return {"conservative": 0.10, "balanced": 0.20, "aggressive": 0.35}.get(risk_profile, 0.20)

    def position_size(self, equity: float, entry_price: float, stop_price: float,
                      max_risk_per_trade_pct: float, risk_profile: str = "balanced",
                      pool_depth: float = 0.0, exposure_used_pct: float = 0.0,
                      regime_cop: float = 1.0) -> dict[str, Any]:
        s = self.s
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return {"qty": 0.0, "notional": 0.0, "risk_usd": 0.0, "reason": "zero stop distance"}
        risk_budget = equity * (max_risk_per_trade_pct / 100.0) * regime_cop
        raw = risk_budget / stop_distance
        max_pct = self.max_position_pct(risk_profile)
        max_notional = equity * max_pct * regime_cop
        liq_cap = pool_depth if pool_depth > 0 else float("inf")
        exposure_remaining = equity * (1.0 - exposure_used_pct / 100.0)
        max_notional = min(max_notional, exposure_remaining * regime_cop)
        notional = min(raw * entry_price, max_notional, liq_cap * 0.2)  # never >20% of pool depth as our size
        qty = notional / entry_price if entry_price else 0.0
        risk_usd = qty * stop_distance
        return dict(qty=round(qty, 8), notional=round(notional, 2), risk_usd=round(risk_usd, 2),
                    reason="ok", max_notional=round(max_notional, 2), liq_cap_usd=round(liq_cap if liq_cap != float("inf") else 0.0, 2),
                    exposure_used_pct=round(exposure_used_pct, 2), regime_cop=regime_cop)

    def check_entry_limits(self, equity: float, day_start_equity: float, open_notional: float,
                           max_positions: int, open_positions: int, new_notional: float = 0.0,
                           max_daily_loss_pct: float | None = None) -> tuple[bool, list[str]]:
        fails: list[str] = []
        cap = _effective_daily_loss_cap(max_daily_loss_pct, self.s.daily_loss_cap_pct)
        daily = self.daily_pnl_pct({"equity": equity, "day_start_equity": day_start_equity})
        if daily <= -cap:
            fails.append(f"daily loss {daily:.2f}% <= -cap {cap:.1f}%")
        if open_positions >= max_positions:
            fails.append(f"position count {open_positions} >= max {max_positions}")
        expo = self.exposure_pct(open_notional + new_notional, equity)
        if expo > 100.0:
            fails.append(f"exposure {expo:.1f}% > 100%")
        return (len(fails) == 0, fails)


class EmergencySafeties:
    """PRD 22.1 automatic brakes. State lives on the BotState row + audit/risk event."""

    def __init__(self) -> None:
        self.s = get_settings()

    def evaluate(self, *, daily_pnl_pct: float | None, portfolio_equity: float, drawdown_pct: float,
                 provider_error_rate: float, feed_stale: bool, regime: str,
                 max_daily_loss_pct: float | None = None) -> dict[str, Any]:
        brakes: list[dict[str, Any]] = []
        cap = _effective_daily_loss_cap(max_daily_loss_pct, self.s.daily_loss_cap_pct)
        if daily_pnl_pct is not None and daily_pnl_pct <= -cap:
            brakes.append({"type": "daily_loss_limit", "severity": "critical",
                           "detail": f"daily PnL {daily_pnl_pct:.2f}% <= -{cap:.1f}%"})
        if drawdown_pct >= self.s.max_drawdown_cap_pct:
            brakes.append({"type": "max_drawdown", "severity": "critical",
                           "detail": f"drawdown {drawdown_pct:.2f}% >= cap {self.s.max_drawdown_cap_pct:.1f}%"})
        if provider_error_rate > 0.5:
            brakes.append({"type": "provider_failure", "severity": "critical",
                           "detail": f"provider error rate {provider_error_rate*100:.0f}% > 50%"})
        if feed_stale:
            brakes.append({"type": "stale_data", "severity": "critical", "detail": "decision-critical feed stale"})
        if regime == "crash":
            brakes.append({"type": "market_crash", "severity": "critical", "detail": "regime = crash"})
        return {
            "emergency": bool(brakes),
            "brakes": brakes,
            "action": "block_new_entries" if brakes else "none",
        }