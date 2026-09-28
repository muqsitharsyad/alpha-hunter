"""Decision engine: composite score (PRD 15.1), hard gates (15.2), decision thresholds (15.3),
explainable layer (PRD 18), audit trail (PRD 16)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from .config import get_settings
from .copy_feasibility import CopyFeasibility, SizingInput, decidability, hard_gates
from .db import session_scope
from .models import (
    AiMemory, CopyEvaluation, ManipulationFlag, Opportunity, Token, TokenScore, WalletCluster,
)
from .providers.base import PoolSnapshot
from .regime import regime_label
from .risk import RiskEngine
from .token_intel import TokenSafety, token_health_score
from .wallet_intel import consensus_strength, detect_consensus, tier_for


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def composite_score(*, smart_money_quality: float, whale_consensus: float, token_health: float,
                    market_regime: float, entry_quality: float, narrative_momentum: float) -> float:
    """PRD 15.1 weights."""
    return round(
        smart_money_quality * 0.30 + whale_consensus * 0.20 + token_health * 0.15 +
        market_regime * 0.15 + entry_quality * 0.10 + narrative_momentum * 0.10, 2
    )


def build_explanation(candidate: dict[str, Any], decision: str, gates: dict[str, Any],
                      components: dict[str, float]) -> str:
    """PRD 18: plain-language WHY. All values come from the same structured signals."""
    p = candidate["pool"]
    s = candidate.get("safety", {})
    fails = gates.get("failures", [])
    lines = [
        f"{p.symbol} ({p.name}) on {candidate['chain']}",
        f"Scanned as {candidate['kind']} opportunity.",
        f"Score {components.get('composite', 0):.0f}/100 -> decision {decision}.",
    ]
    if components.get("smart_money") is not None:
        lines.append(f"Smart money quality {components['smart_money']:.0f} (wallet tier-based), "
                     f"consensus {components['consensus']:.0f}.")
    if components.get("token_health") is not None:
        lines.append(f"Token health {components['token_health']:.0f} (liquidity/security/holders).")
    if components.get("regime") is not None:
        lines.append(f"Regime {regime_label(candidate.get('regime', 'unknown'))} ({components['regime']:.0f}).")
    if fails:
        lines.append("Hard gates blocked this entry: " + "; ".join(fails) + ".")
    else:
        lines.append("All hard gates passed.")
    sc = s.get("summary", "unknown") if isinstance(s, dict) else "unknown"
    if s and sc != "PASS":
        lines.append(f"Token safety: {sc}.")
    return " ".join(lines)


class DecisionEngine:
    """Pipeline: candidate (pool + consensus + meta) -> composite -> hard gates -> decision ->
    store opportunity + gates + explanation + audit."""

    def __init__(self) -> None:
        self.s = get_settings()
        self.risk = RiskEngine()
        self.safety = TokenSafety()
        self.feas = CopyFeasibility()

    def evaluate(self, candidate: dict[str, Any], *, account: dict[str, Any] | None = None,
                 regime: str = "sideways", regime_cop: float = 1.0,
                 open_positions: int = 0, open_notional: float = 0.0) -> dict[str, Any]:
        p: PoolSnapshot = candidate["pool"]
        meta = candidate.get("meta")
        consensus = candidate.get("consensus", {"buyers": 0, "sellers": 0, "net_flow_usd": 0.0})
        safety = candidate.get("safety") or self.safety.check(meta, p) if meta else {"summary": "UNKNOWN", "flags": {}, "severe": [], "warn": []}
        health = candidate.get("token_health") or token_health_score(p, meta, consensus)

        sm_quality = self._smart_money_quality(candidate, consensus)
        wh_cons = consensus_strength(consensus)
        regime_score = {"bull_expansion": 90.0, "early_bull": 85.0, "sideways": 55.0,
                        "bear": 30.0, "crash": 0.0}.get(regime, 50.0)
        entry = min(100.0, (1 - min(p.price_change_h1, 40) / 100) * 80 + 10)
        narrative = candidate.get("narrative", 50.0)
        comp = composite_score(smart_money_quality=sm_quality, whale_consensus=wh_cons * 100,
                               token_health=health["total"], market_regime=regime_score,
                               entry_quality=entry, narrative_momentum=narrative)

        # feasibility: estimate our size from account risk sizing, then *resize* until feasible
        # (PRD 16.1 lifecycle: SIGNAL -> RISK CHECK -> COPY FEASIBILITY -> POSITION SIZE).
        equity = (account or {}).get("equity", 10_000.0)
        whale_size = max(candidate.get("whale_swaps", []), key=lambda x: x.value_usd, default=None)
        whale_val = whale_size.value_usd if whale_size else 0.0
        our_size, feas, sized = 0.0, None, False
        if whale_val:
            risk_pct = 1.5 if (account or {}).get("risk_profile", "balanced") == "conservative" else 2.0
            stop_pct = min(8.0, max(3.0, p.price_change_h24 * 0.5)) / 100.0
            stop_price = p.price_usd * (1 - stop_pct)
            pos = self.risk.position_size(equity=equity, entry_price=p.price_usd,
                                          stop_price=stop_price, max_risk_per_trade_pct=risk_pct,
                                          risk_profile=(account or {}).get("risk_profile", "balanced"),
                                          pool_depth=p.liquidity_usd,
                                          exposure_used_pct=(account or {}).get("exposure_pct", 0.0),
                                          regime_cop=regime_cop)
            desired = pos["notional"]
            vol = max(150.0, abs(p.price_change_h24) * 80 + 100)
            for scale in (1.0, 0.6, 0.4, 0.25, 0.1):
                cand_size = desired * scale
                if whale_val > 0 and cand_size > self.s.max_pct_of_whale_size * whale_val:
                    cand_size = self.s.max_pct_of_whale_size * whale_val * scale
                feas = self.feas.evaluate(SizingInput(
                    equity=equity, our_size=cand_size, whale_trade_size=whale_val,
                    pool_depth=p.liquidity_usd, volatility_bps=vol,
                    signal_age_s=candidate.get("signal_age_s", 0.0),
                    price_move_since=candidate.get("price_move_since_pct", 0.0) / 100.0,
                ))
                if feas.feasible:
                    our_size = cand_size
                    sized = True
                    break
            if not sized:
                final_size = self.feas.max_feasible_size(p.liquidity_usd, vol)
                if whale_val > 0:
                    final_size = min(final_size, self.s.max_pct_of_whale_size * whale_val)
                feas = self.feas.evaluate(SizingInput(
                    equity=equity, our_size=final_size, whale_trade_size=whale_val,
                    pool_depth=p.liquidity_usd, volatility_bps=vol,
                    signal_age_s=candidate.get("signal_age_s", 0.0),
                    price_move_since=candidate.get("price_move_since_pct", 0.0) / 100.0,
                ))
                if feas.feasible:
                    our_size = final_size
        else:
            feas = self.feas.evaluate(SizingInput(equity=equity, our_size=0.0, whale_trade_size=0.0,
                                                  pool_depth=p.liquidity_usd))

        # hard gates
        risk_ok, risk_fails = (True, [])
        if account is not None:
            risk_ok, risk_fails = self.risk.check_entry_limits(
                equity=equity, day_start_equity=(account or {}).get("day_start_equity", equity),
                open_notional=open_notional, max_positions=(account or {}).get("max_positions", 5),
                open_positions=open_positions, new_notional=our_size)
        consensus_ok = bool(consensus.get("min_wallets_met"))
        divergence = (consensus.get("sellers", 0) >= 2 and consensus.get("net_flow_usd", 0.0) < 0)
        safety_summary = safety.get("summary", "UNKNOWN")
        fresh = candidate.get("data_fresh", False)
        liq_ok = p.liquidity_usd >= self.s.min_pool_liquidity_usd
        ok, failures = hard_gates(
            comp, safety_summary=safety_summary, feasibility_ok=feas.feasible,
            regime=regime, liquidity_ok=liq_ok, risk_ok=risk_ok, consensus_ok=consensus_ok,
            divergence_ok=not divergence, data_fresh=fresh,
            safety_severe=safety.get("severe", []),
        )

        decision = decidability(comp) if ok else "IGNORE"
        if not ok:
            decision = "IGNORE"
        elif comp < self.s.composite_buy_threshold:
            decision = "WATCH" if comp >= self.s.composite_watch_threshold else "IGNORE"
        elif candidate.get("kind") == "new_token" and comp < self.s.composite_buy_threshold + 5 and wh_cons < 0.8:
            decision = "WATCH"  # new tokens need extra confirmation unless consensus is strong

        components = {
            "composite": comp, "smart_money": round(sm_quality, 1), "consensus": round(wh_cons * 100, 1),
            "token_health": health["total"], "regime": regime_score, "entry": entry,
            "narrative": narrative,
        }
        reason = build_explanation(candidate, decision, {"failures": failures}, components)
        gates = {"passed": ok, "failures": failures,
                 "safety": safety_summary, "feasibility": feas.feasible,
                 "risk_failures": risk_fails, "regime": regime}

        feature_hash = self._feature_hash(candidate, components)
        opp = self._persist(candidate, decision, components, reason, gates, feas, regime, feature_hash)
        self._audit(candidate, decision, components, opp.id)

        return {
            "opportunity_id": opp.id, "decision": decision, "composite": comp,
            "components": components, "explanation": reason, "gates": gates, "feasibility": feas,
            "feature_hash": feature_hash, "our_size_notional": our_size,
            "qty": round(our_size / p.price_usd, 8) if p.price_usd else 0.0,
            "notional": round(our_size, 2), "price": p.price_usd, "symbol": p.symbol,
        }

    def _smart_money_quality(self, candidate: dict[str, Any], consensus: dict[str, Any]) -> float:
        tiers = candidate.get("wallet_tiers", [])
        if not tiers:
            return 20.0
        sm = {"S": 100.0, "A": 85.0, "B": 65.0, "C": 40.0}
        return sum(sm.get(t, 40.0) for t in tiers) / len(tiers)

    def _feature_hash(self, candidate: dict[str, Any], components: dict[str, float]) -> str:
        p = candidate["pool"]
        raw = json.dumps({
            "chain": candidate["chain"], "pool": p.pool_address, "symbol": p.symbol,
            "price": p.price_usd, "liq": p.liquidity_usd, "vol24": p.volume_24h_usd,
            "buys24": p.buys_24h, "sells24": p.sells_24h, "buyers24": p.buyers_24h,
            "sellers24": p.sellers_24h, "change_h1": p.price_change_h1, "change_h24": p.price_change_h24,
            "age": p.age_hours, "composite": components.get("composite"),
            "decision_inputs": candidate.get("decision_inputs", {}),
        }, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _persist(self, candidate: dict[str, Any], decision: str, components: dict[str, float],
                 reason: str, gates: dict[str, Any], feas: Any, regime: str, feature_hash: str) -> Opportunity:
        p: PoolSnapshot = candidate["pool"]
        meta = candidate.get("meta")
        with session_scope() as db:
            tok = db.query(Token).filter(Token.chain == candidate["chain"],
                                         Token.address == p.token_address).first()
            if tok is None:
                tok = Token(chain=candidate["chain"], address=p.token_address, symbol=p.symbol,
                            name=p.name, age_hours=p.age_hours, listed=(candidate["kind"] == "listed"),
                            metadata_json={"pool_address": p.pool_address, "dex": p.dex,
                                           "fdv": p.fdv_usd, "liq": p.liquidity_usd})
                db.add(tok)
                db.flush()
            else:
                tok.age_hours = p.age_hours
                tok.listed = candidate["kind"] == "listed"
                tok.metadata_json = {**(tok.metadata_json or {}), "pool_address": p.pool_address}
            db.add(TokenScore(token_id=tok.id, liquidity_score=round(p.liquidity_usd, 2),
                              health_score=components.get("token_health", 0),
                              security_score=80.0, whale_score=components.get("smart_money", 0),
                              components=candidate.get("token_health_components", {}),
                              timestamp=_now()))
            strategy = candidate.get("strategy") or "whale_copy@1.0.0"
            opp = Opportunity(
                token_id=tok.id, chain=candidate["chain"], symbol=p.symbol,
                scanner=candidate["kind"], score=components["composite"], decision=decision,
                reasons=[reason], explanation=reason, components=components,
                gate_results=gates, feasibility=getattr(feas, "details", {}),
                features={"pool": p.pool_address, "price": p.price_usd, "liq": p.liquidity_usd,
                          "vol24": p.volume_24h_usd, "age_hours": p.age_hours,
                          "price_change_h24": p.price_change_h24, "whale_value_usd": feas.details.get("our_size", 0)},
                regime=regime, strategy_version=strategy, feature_hash=feature_hash,
            )
            db.add(opp)
            db.flush()
            if meta:
                db.add(ManipulationFlag(token_id=tok.id, flag_type="copy_feasibility",
                                        evidence={"safety_summary": gates["safety"],
                                                  "feasible": feas.feasible,
                                                  "slippage_bps": feas.slippage_bps},
                                        severity="info", timestamp=_now()))
            # feasibility row
            if candidate.get("whale_swaps"):
                for sw in candidate["whale_swaps"][:3]:
                    db.add(CopyEvaluation(opportunity_id=opp.id, feasible=feas.feasible,
                                          expected_slippage_bps=feas.slippage_bps,
                                          signal_age_s=candidate.get("signal_age_s", 0),
                                          reason=feas.reasons[0] if feas.reasons else "",
                                          details=feas.details))
            db.flush()
            self._ensure_cluster(candidate, db, opp.id)
            return opp

    def _ensure_cluster(self, candidate: dict[str, Any], db, opp_id: int) -> None:
        p: PoolSnapshot = candidate["pool"]
        sw = candidate.get("whale_swaps", [])
        if not sw:
            return
        key = p.token_address
        cluster = db.query(WalletCluster).filter(WalletCluster.cluster_key == key).first()
        if cluster is None:
            cluster = WalletCluster(chain=candidate["chain"], cluster_key=key,
                                    member_count=len({s.wallet for s in sw}), confidence=0.5,
                                    members=[s.wallet for s in sw])
            db.add(cluster)
        else:
            cluster.member_count = len({s.wallet for s in sw})
            cluster.confidence = min(1.0, cluster.confidence + 0.1)
            cluster.members = list(dict.fromkeys([*cluster.members, *[s.wallet for s in sw]]))
        db.flush()

    def _audit(self, candidate: dict[str, Any], decision: str, components: dict[str, float], opp_id: int) -> None:
        from .models import AuditLog

        with session_scope() as db:
            db.add(AuditLog(actor_type="engine", actor_id="decision", action="opportunity_evaluated",
                            entity="opportunity", entity_id=str(opp_id),
                            payload={"decision": decision, "score": components["composite"],
                                     "symbol": candidate["pool"].symbol}))
            db.add(AiMemory(kind="opportunity", key=f"{candidate['chain']}:{candidate['pool'].symbol}",
                            summary=f"{candidate['pool'].symbol} -> {decision} ({components['composite']:.0f})",
                            payload={"decision": decision, "score": components["composite"],
                                     "safety": candidate.get("safety", {}).get("summary", "UNKNOWN")},
                            importance=0.5))