"""Operator daily-loss limits must agree across entry and emergency gates."""
from __future__ import annotations

from typing import Any

import pytest

from app.risk import EmergencySafeties, RiskEngine


@pytest.fixture
def gates(monkeypatch: pytest.MonkeyPatch) -> tuple[RiskEngine, EmergencySafeties]:
    """Fix the global fallback independently of local environment settings."""
    risk, safeties = RiskEngine(), EmergencySafeties()
    monkeypatch.setattr(risk.s, "daily_loss_cap_pct", 5.0)
    return risk, safeties


def check_gates(
    gates: tuple[RiskEngine, EmergencySafeties],
    loss_pct: float,
    **kwargs: Any,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Evaluate identical daily loss through both enforcement paths."""
    risk, safeties = gates
    equity = 10_000.0 - loss_pct * 100.0
    allowed, reasons = risk.check_entry_limits(
        equity=equity,
        day_start_equity=10_000.0,
        open_notional=0.0,
        max_positions=5,
        open_positions=0,
        **kwargs,
    )
    emergency = safeties.evaluate(
        daily_pnl_pct=risk.daily_pnl_pct(
            {"equity": equity, "day_start_equity": 10_000.0}
        ),
        portfolio_equity=equity,
        drawdown_pct=0.0,
        provider_error_rate=0.0,
        feed_stale=False,
        regime="sideways",
        **kwargs,
    )
    return allowed, reasons, emergency


@pytest.mark.parametrize("cap", [2.0, 8.0])
@pytest.mark.parametrize("offset", [-0.01, 0.0, 0.01])
def test_operator_cap_boundary_matches_both_gates(
    gates: tuple[RiskEngine, EmergencySafeties], cap: float, offset: float
) -> None:
    allowed, reasons, emergency = check_gates(
        gates, cap + offset, max_daily_loss_pct=cap
    )
    blocked = offset >= 0.0
    assert allowed is not blocked
    assert bool(reasons) is blocked
    assert emergency["emergency"] is blocked
    assert emergency["action"] == ("block_new_entries" if blocked else "none")
    if blocked:
        assert reasons == [f"daily loss {-cap - offset:.2f}% <= -cap {cap:.1f}%"]
        assert emergency["brakes"] == [{
            "type": "daily_loss_limit",
            "severity": "critical",
            "detail": f"daily PnL {-cap - offset:.2f}% <= -{cap:.1f}%",
        }]


@pytest.mark.parametrize("loss_pct, cap, blocked", [(3.0, 2.0, True), (6.0, 8.0, False)])
def test_operator_override_can_be_stricter_or_looser_than_global(
    gates: tuple[RiskEngine, EmergencySafeties],
    loss_pct: float,
    cap: float,
    blocked: bool,
) -> None:
    allowed, _, emergency = check_gates(gates, loss_pct, max_daily_loss_pct=cap)
    assert allowed is not blocked
    assert emergency["emergency"] is blocked


@pytest.mark.parametrize(
    "invalid_cap", [None, 0.0, -2.0, float("nan"), float("inf"), float("-inf"), "bad", True, False]
)
@pytest.mark.parametrize("loss_pct, blocked", [(4.99, False), (5.0, True), (5.01, True)])
def test_invalid_operator_cap_falls_back_to_global(
    gates: tuple[RiskEngine, EmergencySafeties],
    invalid_cap: Any,
    loss_pct: float,
    blocked: bool,
) -> None:
    allowed, reasons, emergency = check_gates(
        gates, loss_pct, max_daily_loss_pct=invalid_cap
    )
    assert allowed is not blocked
    assert emergency["emergency"] is blocked
    if blocked:
        assert "-cap 5.0%" in reasons[0]
        assert "<= -5.0%" in emergency["brakes"][0]["detail"]


@pytest.mark.parametrize("loss_pct, blocked", [(4.99, False), (5.0, True), (5.01, True)])
def test_omitted_operator_cap_preserves_global_behavior(
    gates: tuple[RiskEngine, EmergencySafeties], loss_pct: float, blocked: bool
) -> None:
    allowed, _, emergency = check_gates(gates, loss_pct)
    assert allowed is not blocked
    assert emergency["emergency"] is blocked


def test_operator_cap_does_not_remove_other_emergency_brakes(
    gates: tuple[RiskEngine, EmergencySafeties],
) -> None:
    _, safeties = gates
    emergency = safeties.evaluate(
        daily_pnl_pct=None,
        portfolio_equity=10_000.0,
        drawdown_pct=safeties.s.max_drawdown_cap_pct,
        provider_error_rate=0.0,
        feed_stale=False,
        regime="sideways",
        max_daily_loss_pct=8.0,
    )
    assert emergency["emergency"] is True
    assert [brake["type"] for brake in emergency["brakes"]] == ["max_drawdown"]
