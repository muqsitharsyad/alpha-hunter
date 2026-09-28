"""Token intelligence: token health score (PRD 12.3), anti-manipulation / MEV hard gates (PRD 13),
safety checks and opportunity scanners (PRD 12.1 new token / 12.2 listed coin)."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .config import get_settings
from .providers.base import PoolSnapshot, TokenMeta


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TokenSafety:
    """PRD 13 hard gates. Failures are severe (reject) or warn (down-weight)."""

    def check(self, meta: TokenMeta, pool: PoolSnapshot | None) -> dict[str, Any]:
        gates: dict[str, Any] = {}
        required = ("sell_simulation_ok", "lp_locked", "mint_authority_revoked",
                    "freeze_authority_revoked", "top10_holder_pct", "deployer_holder_pct",
                    "transfer_tax_bps", "holder_count")
        unknown = [key for key in required if getattr(meta, key) is None]
        # Unknown evidence is not proof of safety.
        gates["honeypot"] = meta.sell_simulation_ok is False
        gates["rug_liquidity"] = meta.lp_locked is False
        gates["rug_mint_authority"] = meta.mint_authority_revoked is False
        gates["rug_freeze_authority"] = meta.freeze_authority_revoked is False
        gates["transfer_tax_bps"] = meta.transfer_tax_bps > 50 if meta.transfer_tax_bps else False
        # deployer / top-holder concentration
        top = meta.top10_holder_pct
        deployer = meta.deployer_holder_pct
        gates["top10_concentration"] = top is not None and top >= 60.0
        gates["deployer_dump_risk"] = deployer is not None and deployer >= 15.0
        # liquidity floor (feasibility) — checked by copy engine too, but keep the gate here
        liq = pool.liquidity_usd if pool else 0.0
        gates["liquidity_below_min"] = liq < get_settings().min_pool_liquidity_usd
        # wash trading heuristic: extreme buy/sell symmetry with low holder base
        if pool and pool.buys_24h > 0 and pool.sells_24h > 0:
            ratio = max(pool.buys_24h, pool.sells_24h) / max(min(pool.buys_24h, pool.sells_24h), 1)
            gates["wash_trade_shape"] = ratio < 1.3 and pool.buyers_24h + pool.sellers_24h < 200
        else:
            gates["wash_trade_shape"] = False
        # fake volume: volume far above liquidity with few holders -> suspicious
        if pool and pool.volume_24h_usd > 0:
            gates["fake_volume"] = (pool.volume_24h_usd > liq * 20 and
                                    meta.holder_count is not None and meta.holder_count < 300)
        else:
            gates["fake_volume"] = False
        if pool:
            gates["mev_sandwich_exposure"] = pool.liquidity_usd < get_settings().min_pool_liquidity_usd * 3
        else:
            gates["mev_sandwich_exposure"] = False

        def severity(flag: bool) -> str:
            return "reject" if flag else "pass"

        return {
            "flags": gates,
            "unknown": unknown,
            "severe": [k for k, v in gates.items() if v and k in (
                "honeypot", "rug_liquidity", "rug_mint_authority", "rug_freeze_authority",
                "transfer_tax_bps", "top10_concentration", "deployer_dump_risk", "fake_volume")],
            "warn": [k for k, v in gates.items() if v and k not in (
                "honeypot", "rug_liquidity", "rug_mint_authority", "rug_freeze_authority",
                "transfer_tax_bps", "top10_concentration", "deployer_dump_risk", "fake_volume")],
            "summary": ("UNKNOWN" if unknown else "PASS") if not any(gates.get(k) for k in (
                "honeypot", "rug_liquidity", "rug_mint_authority", "rug_freeze_authority",
                "transfer_tax_bps", "top10_concentration", "deployer_dump_risk", "fake_volume")) else "REJECT",
        }


def token_health_score(pool: PoolSnapshot | None, meta: TokenMeta | None,
                       consensus: dict[str, Any] | None = None) -> dict[str, Any]:
    """PRD 12.3 heuristic: liquidity quality, holder distribution, security, whale quality,
    usage growth, narrative default (no live social feed => 0.5)."""
    if pool is None and meta is None:
        return {"total": 0.0, "components": {}}
    liq = pool.liquidity_usd if pool else (meta.fdv_usd if meta else 0.0) * 0.3

    def liq_score(v: float) -> float:
        if v >= 200_000:
            return 90.0
        if v >= 80_000:
            return 70.0
        if v >= 30_000:
            return 50.0
        if v >= 15_000:
            return 30.0
        return 10.0

    def holder_score(meta: TokenMeta | None) -> float:
        if not meta or meta.top10_holder_pct is None:
            return 50.0
        top = min(meta.top10_holder_pct, 100.0)
        if top <= 20:
            return 85.0
        if top <= 35:
            return 65.0
        if top <= 60:
            return 40.0
        return 15.0

    def security_score(meta: TokenMeta | None) -> float:
        if not meta or meta.sell_simulation_ok is None:
            return 0.0
        s = 100.0
        if meta.transfer_tax_bps is not None and meta.transfer_tax_bps > 50:
            s -= 40
        if not meta.lp_locked:
            s -= 40
        if not meta.mint_authority_revoked:
            s -= 30
        if meta.deployer_holder_pct is not None and meta.deployer_holder_pct >= 15:
            s -= 25
        if not meta.sell_simulation_ok:
            s -= 60
        if meta.top10_holder_pct is not None and meta.top10_holder_pct >= 60:
            s -= 20
        return max(0.0, s)

    def whale_score(cons: dict[str, Any] | None) -> float:
        if not cons:
            return 50.0
        buyers = cons.get("buyers", 0)
        net = cons.get("net_flow_usd", 0.0)
        flow = cons.get("buy_value_usd", 0.0) / max(cons.get("buy_value_usd", 0) + cons.get("sell_value_usd", 0), 1)
        base = 50.0 + min(buyers, 6) * 8.0 + (10.0 if net > 0 else 0.0) + flow * 8.0
        return max(0.0, min(100.0, base))

    def growth_score(pool: PoolSnapshot | None) -> float:
        if not pool:
            return 50.0
        gr = pool.price_change_h24
        if gr >= 0:
            s = 50 + min(gr, 100) * 0.5
        else:
            s = 50 + max(gr, -50) * 0.8
        return max(0.0, min(100.0, s))

    parts = {
        "liquidity": liq_score(liq),
        "holder_distribution": holder_score(meta),
        "security": security_score(meta),
        "whale_participation": whale_score(consensus),
        "usage_growth": growth_score(pool),
        "narrative": 50.0,  # no live social feed; neutral default
    }
    weights = {"liquidity": 0.20, "holder_distribution": 0.15, "security": 0.20,
               "whale_participation": 0.20, "usage_growth": 0.10, "narrative": 0.15}
    total = sum(weights[k] * parts[k] for k in parts)
    return {"total": round(total, 2), "components": {k: round(v, 1) for k, v in parts.items()}}


class OpportunityScanner:
    """Produces candidate candidates for the decision engine from market-provider pool lists.

    v1: new pools (recent creation) + established pools with strong 24h flow. The scanner itself
    does not trade; it hands candidates to the DecisionEngine which applies hard gates."""

    def __init__(self) -> None:
        self.s = get_settings()

    def candidates_from_trending(self, pools: list[PoolSnapshot], chain: str) -> list[dict[str, Any]]:
        out = []
        for p in pools:
            if not p or not p.token_address:
                continue
            age_h = p.age_hours
            if age_h < 48:
                kind = "new_token"
            else:
                kind = "listed"
            out.append({
                "chain": chain, "pool": p, "kind": kind,
                "recency_bonus": max(0.0, 20.0 - age_h) / 40.0,
                "flow_score": (p.buys_24h / max(p.buys_24h + p.sells_24h, 1)) if (p.buys_24h + p.sells_24h) else 0.5,
            })
        return out

    def candidates_from_new_pools(self, pools: list[PoolSnapshot], chain: str) -> list[dict[str, Any]]:
        return [{"chain": chain, "pool": p, "kind": "new_token", "recency_bonus": 0.5,
                 "flow_score": (p.buys_24h / max(p.buys_24h + p.sells_24h, 1)) if (p.buys_24h + p.sells_24h) else 0.5}
                for p in pools if p and p.token_address]

    def apply_safety_prefilter(self, candidates: list[dict[str, Any]], safety: TokenSafety,
                               metas: dict[str, TokenMeta]) -> list[dict[str, Any]]:
        """Only forward candidates passing minimum safety thresholds (PRD 12.1)."""
        out = []
        for c in candidates:
            p = c["pool"]
            meta = metas.get(p.token_address)
            if meta is None:
                # No metadata = worst case for new memecoins; still let the decision engine hard-gate,
                # but mark unknown security.
                c["security_unknown"] = True
                out.append(c)
                continue
            gate = safety.check(meta, p)
            if gate["summary"] == "PASS" or gate["severe"]:
                # Always surface for evidence, but flag severes so Engine can reject.
                c["severe_gates"] = gate["severe"]
                c["security_gates"] = gate
                out.append(c)
        return out