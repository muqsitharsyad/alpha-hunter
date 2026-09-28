"""Autonomous scan pipeline: providers -> ingestion -> wallet intel -> consensus -> safety ->
decision -> paper execution -> position management -> learning. One scan cycle per call."""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Any

from .config import get_settings
from .db import session_scope
from .decision_engine import DecisionEngine
from .execution import EmergencyLayer, PaperExecution, PositionManager, sync_account
from .models import (
    PaperAccount, PaperPosition, PaperTrade, ProviderHealth, SignalQuality, Token,
)
from .providers.base import PoolSnapshot, SwapEvent, get_adapters
from .regime import RegimeEngine
from .risk import EmergencySafeties, RiskEngine
from .token_intel import OpportunityScanner, TokenSafety, token_health_score
from .wallet_intel import WalletIntelligence, detect_consensus


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _fresh_pool(pool: PoolSnapshot, ttl_s: int) -> bool:
    if pool.source_ts is None:
        return False
    age = (_now() - _aware(pool.source_ts)).total_seconds()
    return 0 <= age <= ttl_s and math.isfinite(pool.price_usd) and pool.price_usd > 0


class AlphaHunterPipeline:
    def __init__(self) -> None:
        self.s = get_settings()
        self.adapters = get_adapters()
        self.regime_engine = RegimeEngine()
        self.wallets = WalletIntelligence()
        self.safety = TokenSafety()
        self.scanner = OpportunityScanner()
        self.decider = DecisionEngine()
        self.exec = PaperExecution()
        self.pos_mgr = PositionManager()
        self.risk = RiskEngine()
        self.emergency = EmergencyLayer()
        self.safeties = EmergencySafeties()

    # ------------------------------------------------------------------ regime
    def current_regime(self) -> tuple[str, dict[str, float], dt.datetime]:
        try:
            bars = self.adapters.market.candles("BTCUSDT", "1d", 90)
            closes = [b.close for b in bars]
            return self.regime_engine.classify(closes)
        except Exception:
            return "unknown", {"confidence": 0.0}, _now()

    # ------------------------------------------------------- provider health
    def persist_provider_health(self) -> None:
        from .providers.base import HEALTH

        for provider, stats in HEALTH.stats_all().items():
            with session_scope() as db:
                ph = db.query(ProviderHealth).filter_by(provider=provider).first()
                if ph is None:
                    ph = ProviderHealth(provider=provider)
                    db.add(ph)
                ph.status = stats["status"]
                ph.last_success = stats["last_success"]
                ph.error_rate_5m = stats["error_rate_5m"]
                ph.latency_ms_p95 = stats["latency_ms_p95"]
                ph.calls = stats["calls"]
                ph.errors = stats["errors"]

    # --------------------------------------------------------------- account
    def _account_dict(self, account: PaperAccount, open_positions: list[PaperPosition],
                      user_settings: Any = None) -> dict[str, Any]:
        open_notional = sum(p.qty_open * (p.last_price or p.avg_entry) for p in open_positions)
        profile = getattr(user_settings, "risk_profile", None) or "balanced"
        max_pos = getattr(user_settings, "max_positions", None) or self.s.max_positions
        return {
            "equity": account.equity, "day_start_equity": account.day_start_equity,
            "starting_equity": account.starting_equity, "realized_pnl": account.realized_pnl,
            "peak_equity": account.peak_equity,
            "risk_profile": profile, "max_positions": max_pos,
            "max_daily_loss_pct": getattr(user_settings, "max_daily_loss_pct", None) or self.s.daily_loss_cap_pct,
            "exposure_pct": (open_notional / account.equity * 100.0) if account.equity else 0.0,
        }

    def _user_settings(self, uid: int | None):
        if not uid:
            return None
        from .models import UserSettings

        with session_scope() as db:
            return db.query(UserSettings).filter(UserSettings.user_id == uid).first()

    # ------------------------------------------------------------- main cycle
    def scan_cycle(self) -> dict[str, Any]:
        s = self.s
        state = self.emergency.get_state()
        running = state["status"] in ("running", "safe") and not state["emergency"].get("active")

        regime, features, ts = self.current_regime()
        result = {
            "regime": regime, "features": features, "pools_scanned": 0,
            "wallet_events_ingested": 0, "opportunities_evaluated": 0,
            "buys": 0, "watches": 0, "skipped_held": 0, "errors": [],
            "new_positions": [], "emergency": state["emergency"],
        }

        account_row, open_positions = self._load_account_and_positions(state.get("account_id"))
        user_settings = self._user_settings(account_row.user_id if account_row else None)
        account = self._account_dict(account_row, open_positions, user_settings) if account_row else None
        self._check_emergency(account, regime, state, account_row)
        state = self.emergency.get_state()
        running = state["status"] in ("running", "safe") and not state["emergency"].get("active")
        regime_cop = self.regime_engine.cop_level(regime)
        chains = list(getattr(user_settings, "allowed_chains", None) or s.chain_list()) or s.chain_list()

        # ---- pass 1: scan pools, collect torn candidates + per-wallet swaps
        collected: list[dict[str, Any]] = []
        wallet_swaps: dict[tuple[str, str], list[SwapEvent]] = defaultdict(list)
        seen_pools: set[tuple[str, str]] = set()
        for chain in chains:
            try:
                pools = self.adapters.market.trending_pools(chain, s.max_pools_per_scan)
                if s.new_pools_per_scan:
                    pools += self.adapters.market.new_pools(chain, s.new_pools_per_scan)
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"market scan {chain}: {e}")
                continue

            for cand in self.scanner.candidates_from_trending(pools, chain):
                pool = cand["pool"]
                if not pool or not pool.token_address or (chain, pool.pool_address) in seen_pools:
                    continue
                seen_pools.add((chain, pool.pool_address))
                result["pools_scanned"] += 1
                try:
                    swaps = self.adapters.chain.recent_swaps_for_chain(
                        chain, pool.pool_address,
                        min_usd=max(50.0, s.min_whale_trade_usd / 5), limit=120)
                    if not swaps:
                        continue
                    whale_swaps = [x for x in swaps if x.value_usd >= s.min_whale_trade_usd
                                   and x.chain == chain and x.pool_address == pool.pool_address
                                   and x.token_address == pool.token_address]
                    if not whale_swaps:
                        continue
                    self.wallets.record_swaps(chain, pool, whale_swaps)
                    for w in whale_swaps:
                        wallet_swaps[(chain, w.wallet)].append(w)
                    collected.append({**cand, "chain": chain, "whale_swaps": whale_swaps})
                except Exception as e:  # noqa: BLE001
                    result["errors"].append(f"ingest {cand['pool'].symbol}: {type(e).__name__}: {e}")

        # ---- pass 2: wallet intelligence (global ledger across pools)
        wallet_tiers: dict[tuple[str, str], str] = {}
        pool_prices: dict[str, float] = {}
        for cand in collected:
            pool_prices[cand["pool"].token_address] = cand["pool"].price_usd
        for (chain, wallet_addr), swaps in wallet_swaps.items():
            if not swaps:
                continue
            info = self.wallets.compute_for(chain, wallet_addr, swaps, pool_prices)
            wallet_tiers[(chain, wallet_addr)] = info["tier"]
        result["wallet_events_ingested"] = sum(len(v) for v in wallet_swaps.values())

        # ---- pass 3: evaluate + execute
        for cand in collected:
            pool = cand["pool"]
            try:
                evt = self._evaluate(cand, regime, regime_cop, account,
                                     len(open_positions), sum(p.qty_open * (p.last_price or p.avg_entry) for p in open_positions),
                                     wallet_tiers)
                if evt is None:
                    continue
                result["opportunities_evaluated"] += 1
                held = {p.token_id for p in open_positions}
                if evt["decision"] == "BUY" and evt["token_id"] in held:
                    # never stack a second position in a token we already hold
                    # (risk concentration guard, PRD 16.4 / 12.2)
                    result["skipped_held"] += 1
                    continue
                if evt["decision"] == "BUY" and running and account_row:
                    pos_id = self.exec.open_position(
                        account_id=account_row.id, opportunity_id=evt["opportunity_id"],
                        token_id=evt["token_id"], chain=cand["chain"], symbol=pool.symbol,
                        price=evt["price"], qty=evt["qty"], notional=evt["notional"],
                        pool_depth=pool.liquidity_usd, risk_profile="balanced",
                        strategy_version=evt["strategy_version"], regime=regime,
                        whale_swaps=evt.get("whale_swaps"))
                    result["buys"] += 1
                    result["new_positions"].append({"id": pos_id, "symbol": pool.symbol})
                    account_row = self._reload_account(account_row.id)
                    open_positions = self._load_positions(account_row.id)
                    account = self._account_dict(account_row, open_positions, user_settings)
                elif evt["decision"] == "WATCH":
                    result["watches"] += 1
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"evaluate {pool.symbol}: {type(e).__name__}: {e}")

        self._check_emergency(account, regime, state, account_row)
        if account_row:
            self.persist_provider_health()
        return result

    def _load_account_and_positions(self, account_id: int | None) -> tuple[PaperAccount | None, list[PaperPosition]]:
        if not account_id:
            return None, []
        with session_scope() as db:
            acc = db.query(PaperAccount).filter(PaperAccount.id == account_id).first()
            pos = db.query(PaperPosition).filter(PaperPosition.account_id == account_id,
                                                 PaperPosition.status == "open").all()
            return acc, pos

    def _reload_account(self, account_id: int) -> PaperAccount:
        with session_scope() as db:
            return db.query(PaperAccount).filter(PaperAccount.id == account_id).first()

    def _load_positions(self, account_id: int) -> list[PaperPosition]:
        with session_scope() as db:
            return db.query(PaperPosition).filter(PaperPosition.account_id == account_id,
                                                  PaperPosition.status == "open").all()

    def _token_id(self, chain: str, token_address: str) -> int:
        with session_scope() as db:
            tok = db.query(Token).filter(Token.chain == chain, Token.address == token_address).first()
            return tok.id if tok else 0

    def _evaluate(self, cand: dict[str, Any], regime: str, regime_cop: float,
                  account: dict[str, Any] | None, open_positions: int, open_notional: float,
                  wallet_tiers: dict[tuple[str, str], str]) -> dict[str, Any] | None:
        pool = cand["pool"]
        whale_swaps = cand["whale_swaps"]
        tiers = [wallet_tiers.get((cand["chain"], w.wallet), "C") for w in whale_swaps]
        buys = [x for x in whale_swaps if x.side == "buy"]
        signal_age = 0.0
        price_move = 0.0
        if buys:
            latest = max(buys, key=lambda x: x.block_time)
            signal_age = (_now() - latest.block_time).total_seconds()
            if latest.price_usd and pool.price_usd:
                price_move = (pool.price_usd / latest.price_usd - 1.0) * 100.0
        consensus = detect_consensus(whale_swaps, min_wallets=2, window_minutes=30)

        meta = None
        try:
            meta = self.adapters.token.token_meta(cand["chain"], pool.token_address, pool.pool_address)
        except Exception:  # noqa: BLE001 - metadata optional
            pass
        safety = self.safety.check(meta, pool) if meta else {"summary": "UNKNOWN", "flags": {}, "severe": [], "warn": []}
        health = token_health_score(pool, meta, consensus)

        candidate = {
            "chain": cand["chain"], "pool": pool, "kind": cand["kind"], "meta": meta,
            "safety": safety, "token_health": health, "token_health_components": health["components"],
            "consensus": consensus, "whale_swaps": whale_swaps, "wallet_tiers": tiers,
            "signal_age_s": signal_age, "price_move_since_pct": price_move,
            "data_fresh": _fresh_pool(pool, self.s.ttl_pool_s), "narrative": 50.0,
        }
        evt = self.decider.evaluate(candidate, account=account, regime=regime, regime_cop=regime_cop,
                                    open_positions=open_positions, open_notional=open_notional)
        evt["token_id"] = self._token_id(cand["chain"], pool.token_address)
        evt["whale_swaps"] = whale_swaps
        evt["strategy_version"] = "whale_copy@1.0.0"

        # PRD 8.1 forward hit-rate: record every followed-wallet signal, traded or not
        if consensus.get("min_wallets_met"):
            self._record_signal_quality(cand["chain"], pool.token_address, pool.price_usd,
                                        whale_swaps, wallet_tiers)
        # PRD 26 notifications
        if "S" in tiers and buys:
            from .notify import notify

            notify("tier_s_action", f"{pool.symbol}: tier-S wallet {buys[-1].wallet[:8]} bought "
                                    f"${buys[-1].value_usd:,.0f}")
        if consensus.get("buyers", 0) >= 2:
            from .notify import notify

            notify("convergence", f"{pool.symbol}: {consensus['buyers']} wallets buying within "
                                  f"{consensus['window_minutes']}m (net ${consensus['net_flow_usd']:,.0f})")
        if evt["decision"] == "BUY":
            from .notify import notify

            notify("entry", f"{pool.symbol} BUY score {evt['composite']:.0f}, "
                            f"size ${evt.get('notional', 0):,.0f}, slip "
                            f"{getattr(evt['feasibility'], 'slippage_bps', 0):.0f}bps")
        return evt

    def _record_signal_quality(self, chain: str, token_address: str, entry_price: float,
                               whale_swaps: list[SwapEvent], wallet_tiers: dict[tuple[str, str], str]) -> None:
        token_id = self._token_id(chain, token_address)
        if not token_id or entry_price <= 0:
            return
        with session_scope() as db:
            for sw in whale_swaps:
                if sw.side != "buy":
                    continue
                wid = self.wallets.wallet_id(chain, sw.wallet)
                if not wid:
                    continue
                exists = db.query(SignalQuality).filter(SignalQuality.wallet_id == wid,
                                                        SignalQuality.token_id == token_id,
                                                        SignalQuality.resolved == False).first()  # noqa: E712
                if exists:
                    continue
                db.add(SignalQuality(wallet_id=wid, token_id=token_id, entry_price=sw.price_usd or entry_price,
                                     horizon_h=24, resolved=False))

    # ------------------------------------------------------------ position mgmt
    def refresh_positions(self) -> dict[str, Any]:
        state = self.emergency.get_state()
        if not state.get("account_id"):
            return {"positions": 0, "closes": [], "stale_positions": []}
        regime, _, _ = self.current_regime()
        closes: list[dict[str, Any]] = []
        stale_positions: list[int] = []
        stale_risk = False
        with session_scope() as db:
            positions = db.query(PaperPosition).filter(PaperPosition.account_id == state["account_id"],
                                                       PaperPosition.status == "open").all()
            for pos in positions:
                pool = None
                try:
                    pool = self.adapters.market.pool_snapshot(pos.chain, self._pool_addr(pos))
                except Exception:
                    pool = None
                if pool is None or not _fresh_pool(pool, self.s.ttl_pool_s):
                    stale_positions.append(pos.id)
                    last = _aware(pos.last_price_ts) if pos.last_price_ts else None
                    if last is None or (_now() - last).total_seconds() > self.s.ttl_pool_s:
                        stale_risk = True
                    continue
                price = pool.price_usd
                self._resolve_signal_quality(pos.token_id, price)
                out = self.pos_mgr.check_exits(pos, price, regime)
                if out["action"] not in ("hold", "none"):
                    closes.append({"position_id": pos.id, "symbol": pos.symbol,
                                   "reason": out["reason"], "pnl": out.get("pnl", 0.0)})
            self._sync_account_equity(state["account_id"], positions)
        self._resolve_pending_signals()
        if stale_risk and not state["emergency"].get("active"):
            self.emergency.raise_emergency([{"type": "stale_position_quote", "severity": "critical",
                                             "detail": "Open paper position cannot be priced; entries halted, no synthetic exit"}],
                                           state["account_id"])
        return {"positions": len(positions), "closes": closes, "regime": regime,
                "stale_positions": stale_positions}

    def _pool_addr(self, pos: PaperPosition) -> str:
        with session_scope() as db:
            tok = db.query(Token).filter(Token.id == pos.token_id).first()
            if tok and tok.metadata_json:
                return tok.metadata_json.get("pool_address", "")
        return ""

    def _resolve_signal_quality(self, token_id: int, price: float) -> None:
        now = _now()
        with session_scope() as db:
            rows = db.query(SignalQuality).filter(SignalQuality.token_id == token_id,
                                                  SignalQuality.resolved == False).all()  # noqa: E712
            for r in rows:
                if now < _aware(r.created_at) + dt.timedelta(hours=r.horizon_h):
                    continue
                if not math.isfinite(price) or price <= 0:
                    continue
                if r.entry_price <= 0:
                    r.resolved = True
                    r.resolved_at = now
                    continue
                ret = (price / r.entry_price - 1.0) * 100.0
                r.forward_return_pct = round(ret, 2)
                r.hit = ret > 0
                r.resolved = True
                r.resolved_at = now

    def _resolve_pending_signals(self) -> None:
        """Price mature paper signals even when they never became open positions."""
        with session_scope() as db:
            pending = db.query(SignalQuality).filter_by(resolved=False).order_by(
                SignalQuality.created_at).limit(100).all()
            due_ids = {r.token_id for r in pending if _now() >= _aware(r.created_at) +
                       dt.timedelta(hours=r.horizon_h)}
            tokens = [(t.id, t.chain, t.metadata_json or {}) for t in db.query(Token).filter(
                Token.id.in_(due_ids)).all()] if due_ids else []
        for token_id, chain, metadata in tokens[:2]:
            pool_address = metadata.get("pool_address")
            if not pool_address:
                continue
            try:
                pool = self.adapters.market.pool_snapshot(chain, pool_address)
                if pool is not None and _fresh_pool(pool, self.s.ttl_pool_s):
                    self._resolve_signal_quality(token_id, pool.price_usd)
            except Exception:
                continue

    def _sync_account_equity(self, account_id: int, positions: list[PaperPosition]) -> None:
        with session_scope() as db:
            sync_account(db, account_id)

    # ----------------------------------------------------------- emergency
    def _check_emergency(self, account: dict[str, Any] | None, regime: str, state: dict[str, Any],
                         account_row: PaperAccount | None) -> None:
        if not account:
            return
        from .providers.base import HEALTH

        rates = [s["error_rate_5m"] for s in HEALTH.stats_all().values()]
        err_rate = max(rates) if rates else 0.0
        peak = account.get("peak_equity") or account["equity"]
        drawdown = max(0.0, (peak - account["equity"]) / peak * 100.0) if peak else 0.0
        res = self.safeties.evaluate(daily_pnl_pct=self.risk.daily_pnl_pct(account),
                                     portfolio_equity=account["equity"], drawdown_pct=drawdown,
                                     provider_error_rate=err_rate, feed_stale=regime == "unknown", regime=regime)
        if res["emergency"]:
            self.emergency.raise_emergency(res["brakes"], (account_row.id if account_row else None))