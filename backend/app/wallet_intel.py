"""Wallet intelligence: discovery, trade ledger, scoring (PRD 8), dynamic reputation (PRD 9),
whale intent classification (PRD 8.4), consensus/clusters (PRD 10)."""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Any

from .config import get_settings
from .db import session_scope
from .models import AiMemory, Token, Wallet, WalletScoreSnapshot, WalletTransaction
from .providers.base import PoolSnapshot, SwapEvent


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def _valid_swap(sw: SwapEvent) -> bool:
    return (sw.side in ("buy", "sell") and bool(sw.tx_hash and sw.token_address and sw.wallet)
            and all(math.isfinite(v) and v > 0 for v in (sw.base_amount, sw.price_usd, sw.value_usd)))


def _weighted_avg(rows: list[tuple[float, float]]) -> float:
    num = sum(v * w for v, w in rows)
    den = sum(w for _, w in rows)
    return num / den if den else 0.0


def reputation_decay_weight(age_days: float, half_life_days: float) -> float:
    """PRD 9.1: weight_i = 0.5 ** (age / half_life)"""
    return 0.5 ** (age_days / half_life_days)


def penalty_for_inactivity(age_days: float) -> float:
    """Grace 7d, then linear decay to -5 at 90d."""
    if age_days <= 7:
        return 0.0
    return min(5.0, (age_days - 7) / 90.0 * 5.0)


def compute_reputation(base_score: float, recent_pnls: list[tuple[float, float]],
                       age_days: float, w_base: float = 0.5, w_recent: float = 0.5,
                       half_life_days: float = 30.0) -> dict[str, float]:
    """PRD 9.1: reputation = w_base*base + w_recent*time_decayed_ewma(outcomes) - inactivity_penalty"""
    weights = [reputation_decay_weight(max(a, 0.0), half_life_days) for a, _ in recent_pnls]
    recent = _weighted_avg([(p, w) for (_, p), w in zip(recent_pnls, weights)]) if recent_pnls else 0.0
    recent_score = min(100.0, max(0.0, 50.0 + recent * 20.0))
    rep = w_base * base_score + w_recent * recent_score - penalty_for_inactivity(age_days)
    return {"reputation": round(min(max(rep, 0.0), 100.0), 2), "base_score": round(base_score, 2),
            "recent_score": round(recent_score, 2), "recent_pnls": len(recent_pnls), "age_days": round(age_days, 2)}


def tier_for(score: float) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    return "C"


def classify_wallet_intent(swaps: list[SwapEvent]) -> str:
    """PRD 8.4 heuristic classifier."""
    if not swaps:
        return "unknown"
    buys = [s for s in swaps if s.side == "buy"]
    sells = [s for s in swaps if s.side == "sell"]
    if len(sells) > 2 * len(buys) and len(sells) >= 3:
        return "distributor"
    if len(buys) > 2 * len(sells) and len(buys) >= 3:
        bv = sum(s.value_usd for s in buys)
        sv = sum(s.value_usd for s in sells)
        if sv <= bv * 0.05:
            return "accumulator"
        return "conviction_buyer"
    if len(buys) + len(sells) >= 20:
        return "mm_like"
    return "internal_transfer"


class WalletScorer:
    """PRD 8.2 component weights. All inputs point-in-time scalars from the normalized ledger."""

    def __init__(self) -> None:
        self.weights = {
            "profitability": 0.25, "early_entry": 0.25, "exit_timing": 0.15,
            "risk_behavior": 0.15, "consistency": 0.10, "influence": 0.10,
        }
        self.version = "w1.0"

    def score(self, *, realized_pnl_pct: float, win_rate: float, n_trades: int,
              early_entry_ratio: float, avg_exit_improvement_pct: float,
              concentration: float, max_drawdown_pct: float, exposure_ratio: float,
              avg_trade_size_usd: float, edge_single_trade_ratio: float) -> dict[str, Any]:
        if n_trades >= 5:
            risk_adj = realized_pnl_pct / max(max_drawdown_pct, 0.01)
            cons = min(1.0, n_trades / 30.0)
            profit = 50 + 50 * math.tanh(risk_adj * 0.3) * cons
            profit *= (1 - 0.2 * min(1.0, edge_single_trade_ratio))
        else:
            profit = 20.0 + n_trades * 4.0
        profit = float(min(max(profit, 0.0), 100.0))
        early = float(min(max(early_entry_ratio * 100.0, 0.0), 100.0))
        exit_ = float(min(max(50.0 + min(avg_exit_improvement_pct, 5.0) * 10.0, 0.0), 100.0))
        risk = float(min(max(100.0 - concentration * 0.25 - max_drawdown_pct * 0.3 - max(0.0, (exposure_ratio - 0.35)) * 15.0, 0.0), 100.0))
        consist = float(min(max(win_rate * 80.0 + min(1.0, n_trades / 15.0) * 20.0, 0.0), 100.0))
        influence = float(min(max(math.log10(max(avg_trade_size_usd, 1.0)) / 3.0 * 100.0, 0.0), 100.0))
        parts = {
            "profitability": profit, "early_entry": early, "exit_timing": exit_,
            "risk_behavior": risk, "consistency": consist, "influence": influence,
        }
        total = sum(self.weights[k] * v for k, v in parts.items())
        return {"total": round(total, 2),
                "components": {k: round(v, 1) for k, v in parts.items()},
                "version": self.version}


def _ledger_stats(swaps: list[SwapEvent], pool_prices: dict[str, float]) -> dict[str, Any]:
    """Weighted-average inventory accounting, gross of unavailable fees.

    Unmatched sales have unknown basis. NAV drawdown requires historical marks.
    """
    inventory = defaultdict(lambda: [0.0, 0.0])
    total_in = total_out = realized = matched_basis = 0.0
    outcomes = []
    position_outcomes = []
    position_totals = defaultdict(lambda: [0.0, 0.0])
    unmatched = invalid = 0
    valid = []
    for s in sorted(swaps, key=lambda x: x.block_time):
        if (s.side not in ("buy", "sell") or
                not all(math.isfinite(v) and v > 0 for v in (s.base_amount, s.price_usd, s.value_usd))):
            invalid += 1
            continue
        valid.append(s)
        qty, basis = inventory[s.token_address]
        if s.side == "buy":
            inventory[s.token_address] = [qty + s.base_amount, basis + s.value_usd]
            total_in += s.value_usd
            continue
        total_out += s.value_usd
        matched = min(qty, s.base_amount)
        if matched < s.base_amount:
            unmatched += 1
        if matched <= 0:
            continue
        cost = basis * matched / qty
        proceeds = s.value_usd * matched / s.base_amount
        pnl = proceeds - cost
        realized += pnl
        matched_basis += cost
        outcomes.append((s.block_time, pnl / cost * 100.0, pnl))
        position_totals[s.token_address][0] += cost
        position_totals[s.token_address][1] += pnl
        inventory[s.token_address] = [max(0.0, qty - matched), max(0.0, basis - cost)]
        if matched == qty:
            cycle_cost, cycle_pnl = position_totals.pop(s.token_address)
            position_outcomes.append((s.block_time, cycle_pnl / cycle_cost * 100.0, cycle_pnl))
    unrealized = marked_basis = 0.0
    for token, (qty, basis) in inventory.items():
        mark = pool_prices.get(token)
        if qty > 0 and mark is not None and math.isfinite(mark) and mark >= 0:
            unrealized += qty * mark - basis
            marked_basis += basis
    remaining = sum(basis for qty, basis in inventory.values())
    concentration = max((basis for qty, basis in inventory.values()), default=0.0) / remaining * 100 if remaining else 0.0
    profits = [max(0.0, p) for _, _, p in position_outcomes]
    return dict(realized_pnl_pct=realized / matched_basis * 100 if matched_basis else 0.0,
                realized_pnl_usd=realized, unrealized_pnl_usd=unrealized,
                unrealized_marked_basis_usd=marked_basis, realized_cost_basis_usd=matched_basis,
                win_rate=sum(p > 0 for _, _, p in outcomes) / len(outcomes) if outcomes else 0.0,
                position_win_rate=sum(p > 0 for _, _, p in position_outcomes) / len(position_outcomes) if position_outcomes else 0.0,
                n_trades=len(valid), resolved_trades=len(outcomes), unmatched_sells=unmatched,
                invalid_events=invalid, concentration=concentration, max_drawdown_pct=None,
                exposure_ratio=remaining / total_in if total_in else 0.0,
                avg_trade_size_usd=(total_in + total_out) / len(valid) if valid else 0.0,
                edge_single_trade_ratio=max(profits, default=0.0) / sum(profits) if sum(profits) else 0.0,
                avg_exit_improvement_pct=None, total_in=total_in, total_out=total_out,
                resolved_outcomes=outcomes, position_outcomes=position_outcomes,
                resolved_positions=len(position_outcomes))


class WalletIntelligence:
    def __init__(self) -> None:
        self.s = get_settings()
        self.scorer = WalletScorer()

    def find_or_create(self, chain: str, address: str) -> int:
        with session_scope() as db:
            w = db.query(Wallet).filter(Wallet.chain == chain, Wallet.address == address).first()
            if w is None:
                w = Wallet(chain=chain, address=address, labels={"origin": "auto_discovered"}, metrics={})
                db.add(w)
                db.flush()
            return w.id

    def wallet_id(self, chain: str, address: str) -> int:
        with session_scope() as db:
            w = db.query(Wallet).filter(Wallet.chain == chain, Wallet.address == address).first()
            return w.id if w else 0

    def record_swaps(self, chain: str, pool: PoolSnapshot, swaps: list[SwapEvent]) -> int:
        """Persist only distinct observations without rolling back earlier inserts."""
        inserted = 0
        with session_scope() as db:
            for sw in swaps:
                if (sw.chain != chain or pool.chain != chain or sw.pool_address != pool.pool_address
                        or sw.token_address != pool.token_address or not _valid_swap(sw)
                        or _utc(sw.block_time) > _now()):
                    continue
                wallet = db.query(Wallet).filter_by(chain=chain, address=sw.wallet).first()
                if wallet is None:
                    wallet = Wallet(chain=chain, address=sw.wallet,
                                    labels={"origin": "auto_discovered"}, metrics={})
                    db.add(wallet)
                    db.flush()
                # Persist pool-specific creation evidence; never infer it from first-seen time.
                created = pool.pool_created_at
                if created and _utc(created) <= _utc(pool.source_ts) <= _now():
                    metrics = dict(wallet.metrics or {})
                    creation_times = dict(metrics.get("pool_creation_times", {}))
                    creation_times.setdefault(sw.pool_address, _utc(created).isoformat())
                    metrics["pool_creation_times"] = creation_times
                    wallet.metrics = metrics
                exists = db.query(WalletTransaction.id).filter_by(
                    tx_hash=sw.tx_hash, wallet_id=wallet.id,
                    token_address=sw.token_address).first()
                if exists:
                    continue
                tok = db.query(Token).filter_by(chain=chain, address=sw.token_address).first()
                db.add(WalletTransaction(
                    wallet_id=wallet.id, chain=chain, tx_hash=sw.tx_hash,
                    token_id=tok.id if tok else None, token_address=sw.token_address,
                    pool_address=sw.pool_address, side=sw.side, amount=sw.base_amount,
                    price_est=sw.price_usd, value_usd=sw.value_usd,
                    intent="unknown", timestamp=sw.block_time))
                inserted += 1
            return inserted

    def compute_for(self, chain: str, wallet_address: str, swaps: list[SwapEvent],
                    pool_prices: dict[str, float] | None = None) -> dict[str, Any]:
        pool_prices = pool_prices or {}
        now = _now()
        wid = self.find_or_create(chain, wallet_address)
        # Persist scan input too: callers may compute without a preceding ingest.
        with session_scope() as db:
            for sw in swaps:
                if (sw.chain != chain or sw.wallet != wallet_address or not _valid_swap(sw)
                        or _utc(sw.block_time) > now):
                    continue
                if not db.query(WalletTransaction.id).filter_by(
                        wallet_id=wid, tx_hash=sw.tx_hash, token_address=sw.token_address).first():
                    db.add(WalletTransaction(wallet_id=wid, chain=chain, tx_hash=sw.tx_hash,
                                             token_address=sw.token_address, pool_address=sw.pool_address,
                                             side=sw.side, amount=sw.base_amount, price_est=sw.price_usd,
                                             value_usd=sw.value_usd, timestamp=sw.block_time))
            db.flush()
            rows = db.query(WalletTransaction).filter_by(wallet_id=wid, chain=chain).order_by(
                WalletTransaction.timestamp, WalletTransaction.id).all()
            wallet = db.query(Wallet).filter_by(id=wid).one()
            creation_times = (wallet.metrics or {}).get("pool_creation_times", {})
            swaps = [SwapEvent(chain=chain, tx_hash=r.tx_hash, wallet=wallet_address,
                               token_address=r.token_address, pool_address=r.pool_address,
                               symbol="", side=r.side, base_amount=r.amount, quote_amount=0.0,
                               price_usd=r.price_est, value_usd=r.value_usd,
                               block_time=r.timestamp.replace(tzinfo=dt.timezone.utc)
                               if r.timestamp.tzinfo is None else r.timestamp) for r in rows]
        swaps = [sw for sw in swaps if sw.block_time <= now]
        stats = _ledger_stats(swaps, pool_prices)
        intent = classify_wallet_intent(swaps)
        buys = [sw for sw in swaps if sw.side == "buy" and _valid_swap(sw)]
        early_count = known_entries = 0
        for sw in buys:
            raw = creation_times.get(sw.pool_address)
            if raw:
                try:
                    elapsed = (sw.block_time - _utc(dt.datetime.fromisoformat(raw))).total_seconds()
                except (ValueError, TypeError):
                    continue
                if elapsed >= 0:
                    known_entries += 1
                    early_count += elapsed <= 3600
        # Unknown entries receive no credit; known subsets cannot imply 100% early.
        early_ratio = early_count / len(buys) if known_entries and buys else None
        stats["early_entry_ratio"] = early_ratio
        score = self.scorer.score(
            realized_pnl_pct=stats["realized_pnl_pct"], win_rate=stats["position_win_rate"],
            n_trades=stats["resolved_positions"], early_entry_ratio=early_ratio or 0.0,
            avg_exit_improvement_pct=-5.0,  # no post-exit benchmark: no timing credit
            concentration=stats["concentration"], max_drawdown_pct=100.0 if stats["max_drawdown_pct"] is None else stats["max_drawdown_pct"],
            exposure_ratio=stats["exposure_ratio"], avg_trade_size_usd=stats["avg_trade_size_usd"],
            edge_single_trade_ratio=stats["edge_single_trade_ratio"],
        )
        score["version"] = "w1.1"  # historical qualification input policy; scalar scorer is unchanged
        outcomes = stats["position_outcomes"]
        last_activity = max((ts for ts, _, _ in outcomes), default=None)
        inactivity_days = (now - last_activity).total_seconds() / 86400 if last_activity else None
        rep = compute_reputation(score["total"],
                                 [((now - ts).total_seconds() / 86400, pct / 100)
                                  for ts, pct, _ in outcomes], inactivity_days or 0.0)
        # A few visible swaps are not evidence of a profitable smart-money wallet.
        history_qualified = len(outcomes) >= 5 and stats["unmatched_sells"] == 0
        tier = tier_for(rep["reputation"]) if history_qualified else "C"
        # PRD 9.2: every reputation change is versioned for audit/replay
        try:
            from .models import WalletReputationHistory

            with session_scope() as db:
                db.add(WalletReputationHistory(wallet_id=wid, reputation=rep["reputation"],
                                               drivers={"base_score": score["total"],
                                                        "intent": intent,
                                                        "n_trades": stats["n_trades"],
                                                        "win_rate": round(stats["win_rate"], 3),
                                                        "resolved_positions": len(outcomes),
                                                        "history_qualified": history_qualified,
                                                        "last_qualifying_activity": last_activity.isoformat() if last_activity else None,
                                                        "inactivity_days": inactivity_days,
                                                        "recent_score": rep["recent_score"],
                                                        "early_entry_evidence_count": known_entries,
                                                        "half_life_days": 30.0},
                                               regime="unknown", version="r1.1"))
        except Exception:  # noqa: BLE001 - history is best-effort
            pass
        w = None
        with session_scope() as db:
            w = db.query(Wallet).filter(Wallet.id == wid).first()
            if w:
                w.score = score["total"]
                w.reputation = rep["reputation"]
                w.tier = tier
                w.archetype = intent
                w.confidence = round(min(0.95, 0.3 + stats["n_trades"] * 0.05), 3)
                w.metrics = {
                    "pool_creation_times": creation_times,
                    "early_entry_ratio": early_ratio,
                    "early_entry_evidence_count": known_entries,
                    "avg_exit_improvement_pct": stats["avg_exit_improvement_pct"],
                    "realized_pnl_pct": round(stats["realized_pnl_pct"], 2),
                    "win_rate": round(stats["win_rate"], 3), "n_trades": stats["n_trades"],
                    "resolved_trades": stats["resolved_trades"],
                    "resolved_positions": stats["resolved_positions"],
                    "position_win_rate": round(stats["position_win_rate"], 3),
                    "history_qualified": history_qualified,
                    "inactivity_days": inactivity_days,
                    "last_qualifying_activity": last_activity.isoformat() if last_activity else None,
                    "avg_size_usd": round(stats["avg_trade_size_usd"], 2),
                    "total_in_usd": round(stats["total_in"], 2), "total_out_usd": round(stats["total_out"], 2),
                    "max_drawdown_pct": stats["max_drawdown_pct"], "last_intent": intent,
                }
            db.add(WalletScoreSnapshot(wallet_id=wid, components=score["components"],
                                       total_score=score["total"], version=score["version"]))
            db.add(AiMemory(kind="wallet", key=f"{chain}:{wallet_address}",
                            summary=f"{intent} tier {tier}",
                            payload={"score": score["total"], "reputation": rep["reputation"], "intent": intent},
                            importance=0.6))
        return {"wallet_id": wid, "score": score["total"], "reputation": rep["reputation"],
                "tier": tier, "intent": intent, "components": score["components"],
                "metrics": w.metrics if w else {}}


def detect_consensus(swaps: list[SwapEvent], min_wallets: int = 2,
                     window_minutes: int = 30, min_whale_value_usd: float = 0.0) -> dict[str, Any]:
    """PRD 10: number of distinct wallets buying/selling in the window, net flow."""
    now = _now()

    def _inside(s: SwapEvent) -> bool:
        return (now - s.block_time).total_seconds() <= window_minutes * 60

    buy_wallets: set[str] = set()
    sell_wallets: set[str] = set()
    buy_value = 0.0
    sell_value = 0.0
    for s in swaps:
        if not _inside(s):
            continue
        if s.value_usd < min_whale_value_usd:
            continue
        if s.side == "buy":
            buy_wallets.add(s.wallet)
            buy_value += s.value_usd
        else:
            sell_wallets.add(s.wallet)
            sell_value += s.value_usd
    return {
        "buyers": len(buy_wallets), "sellers": len(sell_wallets),
        "min_wallets_met": len(buy_wallets) >= min_wallets,
        "window_minutes": window_minutes, "net_flow_usd": round(buy_value - sell_value, 2),
        "buy_value_usd": round(buy_value, 2), "sell_value_usd": round(sell_value, 2),
    }


def consensus_strength(cons: dict[str, Any]) -> float:
    buyers = cons.get("buyers", 0)
    sellers = cons.get("sellers", 0)
    buy_v = cons.get("buy_value_usd", 0.0)
    sell_v = cons.get("sell_value_usd", 0.0)
    if buyers <= 0:
        return 0.0
    b = min(buyers / 3.0, 1.0)
    flow_mag = buy_v / (buy_v + sell_v) if (buy_v + sell_v) > 0 else 0.5
    flow = 0.5 + 0.5 * flow_mag  # strong net inflow => up to 1.0
    against = min(sellers / 4.0, 0.5)
    return round(max(0.0, min(1.0, 0.4 * b + 0.35 * flow - against + 0.25)), 3)