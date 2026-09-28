"""Market regime: scalar trend/vol/stress -> PRD 14 regimes + versioning + transitions."""
from __future__ import annotations

import datetime as dt

import numpy as np

from .config import get_settings


class RegimeEngine:
    """Computes regime from BTC/ETH trend (MA relationships), realized vol, and a stress proxy.

    v1 stress proxy = short-window realized vol percentile + cross-asset drawdown of the
    listed universe. No *fundamental* liquidation/liquidity feed required (PRD 14 fits free data).
    """

    def __init__(self) -> None:
        self.version = "g1.0"

    def classify(self, closes: list[float], dates: list[dt.datetime] | None = None) -> tuple[str, dict[str, float], dt.datetime]:
        if not closes or len(closes) < 30:
            return "sideways", {"confidence": 0.0, "trend_strength": 0.0}, dt.datetime.now(dt.timezone.utc)

        arr = np.asarray(closes, dtype=float)
        ma20 = float(np.mean(arr[-20:]))
        ma50 = float(np.mean(arr[-50:])) if len(arr) >= 50 else float(np.mean(arr))
        last = float(arr[-1])
        rets = np.diff(np.log(np.maximum(arr, 1e-12)))
        vol = float(np.std(rets[-30:])) * np.sqrt(365) if len(rets) >= 2 else 0.2
        vol_pct = float(np.percentile(np.std(rets[max(0, len(rets) - 120):]) if len(rets) > 5 else rets, 80))

        # stress proxy: max drawdown from recent rolling peak (10%+ window = stress)
        window = arr[-60:] if len(arr) >= 60 else arr
        roll_peak = float(np.max(window))
        drawdown = float((window[-1] / roll_peak - 1.0) * 100.0) if roll_peak else 0.0
        stress = 0.0
        if drawdown < -15:
            stress = 0.9
        elif drawdown < -8:
            stress = 0.5
        elif vol > 0.9:
            stress = 0.6

        trend = last > ma20 > ma50
        early = last > ma20 and last < ma50 * 1.02
        bear = last < ma20 and last < ma50
        strength = min(abs((last / ma50 - 1.0) * 100.0) / 5.0, 1.0)

        if stress >= 0.6:
            regime = "crash"
        elif trend and strength >= 0.3:
            regime = "bull_expansion"
        elif early:
            regime = "early_bull"
        elif bear:
            regime = "bear"
        else:
            regime = "sideways"

        features = {
            "last": last, "ma20": ma20, "ma50": ma50, "vol_annual": vol,
            "vol_pct": vol_pct, "drawdown_pct": drawdown, "stress": stress, "trend_strength": strength,
        }
        return regime, features, dt.datetime.now(dt.timezone.utc)

    def cop_level(self, regime: str) -> float:
        """Multiplier applied to position sizing / gates (PRD 14 effects). Crash = 0 (hard block)."""
        return {"bull_expansion": 1.0, "early_bull": 1.1, "sideways": 0.75, "bear": 0.5, "crash": 0.0}.get(regime, 1.0)

    def sanity_bound_opportunities_per_hour(self, regime: str) -> int:
        return {"bull_expansion": 150, "early_bull": 180, "sideways": 60, "bear": 40, "crash": 0}.get(regime, 60)


def format_regime(regime: str) -> str:
    return regime


def regime_label(regime: str) -> str:
    return {
        "bull_expansion": "Bull expansion",
        "early_bull": "Early bull",
        "sideways": "Sideways",
        "bear": "Bear",
        "crash": "Crash",
    }.get(regime, regime)