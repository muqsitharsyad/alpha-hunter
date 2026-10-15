"""Paper execution: order & fill simulation with realistic slippage/gas/MEV (PRD 16.3),
position sizing (16.4), TP/SL/trailing/smart exits (PRD 17), and emergency brakes (PRD 22)."""
from __future__ import annotations

import datetime as dt
import math
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm.attributes import set_committed_value

from .config import get_settings
from .db import session_scope
from .models import (
    AuditLog, BotState, LearningEvent, Opportunity, PaperAccount, PaperOrder, PaperPosition, PaperTrade, RiskEvent,
)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(v: dt.datetime | None) -> dt.datetime | None:
    """SQLite returns naive datetimes; normalize before arithmetic."""
    if v is None:
        return None
    return v.replace(tzinfo=dt.timezone.utc) if v.tzinfo is None else v


class PaperExecution:
    """Autonomous order simulator. Fill derives from pool depth (price impact of our size)."""

    def __init__(self) -> None:
        self.s = get_settings()

    def fill_price(self, side: str, price: float, notional: float, pool_depth: float,
                   price_impact_bps: float = 0.0) -> tuple[float, float]:
        depth = max(pool_depth, 1.0)
        impact_bps = (notional / depth) * 2.0 * 10_000.0 if depth > 0 else 10_000.0
        slip_bps = min(impact_bps + price_impact_bps, 2500.0)
        slip = slip_bps / 10_000.0
        fill = price * (1 + slip if side == "buy" else 1 - slip)
        return round(fill, 12), round(slip_bps, 2)

    def gas_fee_usd(self, chain: str) -> float:
        return {"solana": 0.0002, "ethereum": 3.5, "bsc": 0.15}.get(chain, 0.0002)

    def mev_cost_usd(self, notional: float, pool_depth: float) -> float:
        """Adverse-selection / worse-fill proxy for thin pools (PRD 16.3)."""
        if pool_depth <= 0:
            return 0.0
        thin = max(0.0, 1 - pool_depth / 100_000)
        return notional * thin * 0.003

    def expected_stop_pct(self, symbol: str) -> float:
        return self._vol_proxy(symbol)

    def _vol_proxy(self, symbol: str) -> float:
        return 0.05 if "PUMP" in symbol.upper() else 0.03

    def open_position(self, account_id: int, opportunity_id: int, token_id: int, chain: str,
                      symbol: str, price: float, qty: float, notional: float, pool_depth: float,
                      risk_profile: str, strategy_version: str, regime: str,
                      whale_swaps: list[Any] | None = None) -> int:
        """Simulate and persist an opening fill. Returns the position id."""
        for label, value in (("price", price), ("qty", qty), ("notional", notional),
                             ("pool depth", pool_depth)):
            _positive(label, value)
        if abs(price * qty - notional) > max(0.01, notional * 0.001):
            raise ValueError("quantity does not match requested notional")
        fill, slip = self.fill_price("buy", price, notional, pool_depth)
        fees = notional * 0.001 + self.gas_fee_usd(chain)
        mev = self.mev_cost_usd(notional, pool_depth)
        total_cost = fill * qty + fees + mev
        stop_pct = min(8.0, max(3.0, abs(self._vol_proxy(symbol)) * 0.5)) / 100.0
        stop = fill * (1 - stop_pct)
        risk_usd = qty * (fill - stop)
        with session_scope() as db:
            _begin_write(db)
            existing = db.query(PaperPosition).filter_by(opportunity_id=opportunity_id,
                                                        account_id=account_id).first()
            if existing:
                return existing.id
            existing_token = db.query(PaperPosition).filter_by(
                account_id=account_id, token_id=token_id, status="open").first()
            if existing_token:
                return existing_token.id
            # Retries above are read-only, including after a pause. Every NEW fill
            # requires current authorization, not the scan's stale running flag.
            # SQLite's writer reservation and PostgreSQL's singleton row lock
            # serialize this check with control changes until the fill commits.
            state = db.query(BotState).filter_by(id=1).with_for_update().first()
            if (state is None or state.status != "running" or state.safe_mode
                    or state.account_id != account_id):
                raise ValueError("bot is not running for this paper account")
            if (state.emergency or {}).get("active"):
                raise ValueError("bot emergency blocks new paper entries")
            account = db.query(PaperAccount).filter_by(id=account_id).with_for_update().first()
            if account is None or account.status != "active":
                raise ValueError("paper account unavailable")
            committed = sum(p.qty_open * p.avg_entry for p in db.query(PaperPosition).filter_by(
                account_id=account_id, status="open").all())
            if not math.isfinite(account.equity) or total_cost > account.equity - committed:
                raise ValueError("insufficient paper cash/equity")
            opportunity = db.get(Opportunity, opportunity_id)
            # Position JSON owns the immutable entry snapshot; never reconstruct
            # new trades from a subsequently re-evaluated opportunity.
            entry_context = {"regime": regime,
                             "features": deepcopy(opportunity.features or {}) if opportunity else {}}
            pos = PaperPosition(
                account_id=account_id, token_id=token_id, chain=chain, symbol=symbol,
                qty=qty, qty_open=qty, avg_entry=fill, notional_usd=total_cost, stop_loss=stop,
                initial_stop=stop, risk_usd=risk_usd, peak_price=fill,
                tp_levels=[{"level": 1, "price": round(fill * 1.08, 12), "alloc_pct": 0.30, "done": False},
                           {"level": 2, "price": round(fill * 1.18, 12), "alloc_pct": 0.40, "done": False}],
                trailing_state={"active": False, "trigger_pct": 0.06, "step_pct": 0.035,
                                "peak": fill, "stop": stop, "entry_context": entry_context},
                strategy_version=strategy_version, opportunity_id=opportunity_id,
                last_price=fill, last_price_ts=_now(),
                events=[{"ts": _now().isoformat(), "type": "open",
                         "note": f"fill {fill:.10g} slip {slip:.0f}bps mev ${mev:.4f}"}],
            )
            db.add(pos)
            db.flush()
            pos_id = pos.id
            db.add(PaperOrder(
                account_id=account_id, opportunity_id=opportunity_id, token_id=token_id,
                side="buy", order_type="market", qty=qty, intended_price=price, fill_price=fill,
                fees=fees, slippage_bps=slip, partial_fill=False, mev_cost_usd=mev,
                status="filled", idempotency_key=f"open-{account_id}-{opportunity_id}",
            ))
            db.add(AuditLog(actor_type="engine", actor_id="execution", action="position_open",
                            entity="position", entity_id=str(pos_id),
                            payload={"symbol": symbol, "qty": qty, "fill": fill, "slip_bps": slip,
                                     "mev_usd": mev, "fees_usd": fees}))
            sync_account(db, account_id)
        return pos_id


def _positive(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _begin_write(db) -> None:
    # SQLite has no row locks: reserve its single writer before reading the ledger.
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def sync_account(db, account_id: int) -> None:
    """Reconcile the paper ledger in the same transaction as each fill."""
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise ValueError("paper account unavailable")
    positions = db.query(PaperPosition).filter_by(account_id=account_id, status="open").all()
    trades = db.query(PaperTrade).filter_by(account_id=account_id).all()
    unrealized = sum(((p.last_price or p.avg_entry) - p.avg_entry) * p.qty_open for p in positions)
    pending_cost = sum(max(0.0, p.notional_usd - p.avg_entry * p.qty) * p.qty_open / p.qty
                       for p in positions if p.qty > 0)
    account.realized_pnl = round(sum(t.pnl for t in trades), 2)
    account.fees_paid = round(sum(t.fees for t in trades), 2)
    account.equity = round(account.starting_equity + account.realized_pnl + unrealized - pending_cost, 2)
    account.peak_equity = max(account.peak_equity, account.equity)
    today = _now().strftime("%Y-%m-%d")
    if account.day_key != today:
        account.day_key, account.day_start_equity = today, account.equity


@contextmanager
def _position_transaction(pos: PaperPosition):
    """Reload authoritative state; never merge a stale caller into the ledger."""
    if pos.id is None:
        # Transient objects support strategy previews, never execution or persistence.
        current = PaperPosition(**{c.key: deepcopy(getattr(pos, c.key))
                                  for c in PaperPosition.__table__.columns})
        yield None, current
    else:
        with session_scope() as db:
            _begin_write(db)
            owner = db.query(PaperPosition.account_id).filter_by(id=pos.id).scalar()
            if owner is None:
                raise ValueError("position does not exist")
            db.query(PaperAccount).filter_by(id=owner).with_for_update().one()
            current = db.query(PaperPosition).filter_by(id=pos.id).with_for_update().one()
            yield db, current
    # Only publish state after commit. Attached callers must not flush it a second time.
    for column in PaperPosition.__table__.columns:
        set_committed_value(pos, column.key, deepcopy(getattr(current, column.key)))


class PositionManager:
    """Atomic paper exits. qty is initial inventory; qty_open is unsold inventory.

    notional_usd stays the original all-in entry cost. PaperTrade.fees includes
    allocated entry costs (including MEV) plus exit costs; pnl is net of both.
    Event history is display-only and never determines inventory.
    """

    def __init__(self) -> None:
        self.s = get_settings()

    def _mark(self, pos: PaperPosition, price: float, ts: dt.datetime) -> None:
        pos.last_price = price
        pos.last_price_ts = ts
        pos.peak_price = max(price, pos.peak_price or price)
        pos.events = (list(pos.events or []) +
                      [{"ts": ts.isoformat(), "type": "mark", "price": price}])[-60:]

    def mark(self, pos: PaperPosition, price: float, ts: dt.datetime | None = None) -> PaperPosition:
        _positive("price", price)
        with _position_transaction(pos) as (db, current):
            if current.status == "open":
                self._mark(current, price, ts or _now())
                if db is not None:
                    sync_account(db, current.account_id)
        return pos

    def _record_exit(self, db, pos: PaperPosition, qty: float, intended_price: float,
                     fill_price: float, exit_slippage_bps: float, reason: str,
                     pnl: float, fees: float, exit_fees: float) -> None:
        opened = _aware(pos.opened_at) or _now()
        basis = pos.avg_entry * qty
        allocated_risk = (pos.risk_usd or 0.0) * qty / pos.qty
        # Legacy positions have no frozen context; keep unknown rather than
        # mislabeling a current opportunity evaluation as historical evidence.
        context = (pos.trailing_state or {}).get("entry_context") or {}
        features = deepcopy(context.get("features") or {})
        features.update(intended_exit_price=intended_price,
                        exit_slippage_bps=exit_slippage_bps, exit_fee_usd=exit_fees)
        trade = PaperTrade(position_id=pos.id, account_id=pos.account_id, qty=qty,
                          entry_time=opened, exit_time=_now(), entry_price=pos.avg_entry,
                          exit_price=fill_price, fees=fees, slippage_bps=exit_slippage_bps, pnl=pnl,
                          pnl_pct=pnl / basis * 100.0,
                          r_multiple=pnl / allocated_risk if allocated_risk > 0 else 0.0,
                          max_favorable=(pos.peak_price / pos.avg_entry - 1.0) * 100.0,
                          max_adverse=max(0.0, (pos.avg_entry - fill_price) / pos.avg_entry * 100.0),
                          duration_s=max(0.0, (_now() - opened).total_seconds()),
                          exit_reason=reason, strategy_version=pos.strategy_version,
                          regime=context.get("regime") or "unknown", features=features)
        db.add(trade)
        db.flush()
        # Actual fill identity (not position id): partial exits are distinct
        # samples. The locked position/inventory guard makes retries no-ops.
        db.add(LearningEvent(source_type="trade", source_id=str(trade.id),
                             strategy_version=trade.strategy_version, regime=trade.regime,
                             features=deepcopy(trade.features),
                             outcome_label="win" if pnl > 0 else "loss" if pnl < 0 else "flat",
                             pnl_pct=trade.pnl_pct, value=trade.pnl))
        db.add(PaperOrder(account_id=pos.account_id, opportunity_id=pos.opportunity_id,
                          token_id=pos.token_id, side="sell", order_type="market", qty=qty,
                          intended_price=intended_price, fill_price=fill_price, fees=exit_fees,
                          slippage_bps=exit_slippage_bps, status="filled",
                          idempotency_key=f"exit-{pos.account_id}-{trade.id}"))
        db.add(AuditLog(actor_type="engine", actor_id="positions",
                        action="position_closed" if pos.qty_open == 0 else "position_partial_exit",
                        entity="position", entity_id=str(pos.id),
                        payload={"reason": reason, "intended_exit_price": intended_price,
                                 "fill_price": fill_price, "exit_slippage_bps": exit_slippage_bps,
                                 "pnl": pnl, "qty": qty}))

    def _exit(self, db, pos: PaperPosition, qty: float, price: float, pool_depth: float,
              reason: str) -> float:
        if db is None:
            raise ValueError("cannot execute an unpersisted position")
        _positive("qty", qty)
        _positive("initial quantity", pos.qty)
        _positive("entry price", pos.avg_entry)
        _positive("pool depth", pool_depth)
        if qty > pos.qty_open or pos.qty_open > pos.qty:
            raise ValueError("exit quantity exceeds inventory")
        # Legacy manually-created positions may not have an entry cost basis.
        entry_costs = max(0.0, (pos.notional_usd or 0.0) - pos.avg_entry * pos.qty)
        fill_price, exit_slippage_bps = PaperExecution().fill_price("sell", price, price * qty, pool_depth)
        exit_fees = fill_price * qty * 0.001 + PaperExecution().gas_fee_usd(pos.chain)
        fees = exit_fees + entry_costs * qty / pos.qty
        pnl = (fill_price - pos.avg_entry) * qty - fees
        if not math.isfinite(pnl) or not math.isfinite(fees):
            raise ValueError("exit totals must be finite")
        pos.qty_open = max(0.0, pos.qty_open - qty)
        pos.last_price, pos.last_price_ts = fill_price, _now()
        if pos.qty_open == 0:
            pos.status, pos.closed_at, pos.exit_reason = "closed", _now(), reason
        self._record_exit(db, pos, qty, price, fill_price, exit_slippage_bps, reason, pnl, fees, exit_fees)
        db.flush()
        sync_account(db, pos.account_id)
        pos.events = (list(pos.events or []) + [{"ts": _now().isoformat(),
                      "type": reason.lower(), "intended_price": price, "fill_price": fill_price,
                      "slippage_bps": exit_slippage_bps, "qty": qty, "pnl": pnl}])[-60:]
        return pnl

    def _tp_exit(self, pos: PaperPosition, price: float, pool_depth: float, tp: dict[str, Any], qty: float) -> float:
        """Compatibility entry point; TP identity and allocation come from the DB."""
        _positive("price", price)
        _positive("qty", qty)
        pnl = 0.0
        with _position_transaction(pos) as (db, current):
            if current.status == "open" and current.qty_open > 0:
                levels = deepcopy(current.tp_levels or [])
                target = next((t for t in levels if t["level"] == tp["level"]), None)
                if target is None:
                    raise ValueError("unknown TP level")
                if not target.get("done") and price >= target["price"]:
                    allocation = _positive("TP allocation", target.get("alloc_pct", 0.30))
                    if allocation > 1:
                        raise ValueError("TP allocation must not exceed one")
                    qty = min(current.qty_open, current.qty * allocation)
                    pnl = self._exit(db, current, qty, price, pool_depth, f"TP{target['level']}")
                    target.update(done=True, closed_qty=qty)
                    current.tp_levels = levels
        return pnl

    def close_all(self, pos: PaperPosition, price: float, reason: str, pool_depth: float | None = None) -> float:
        """Atomically close persisted remaining inventory; retries return zero."""
        _positive("price", price)
        _positive("pool depth", pool_depth or 0.0)
        pnl = 0.0
        with _position_transaction(pos) as (db, current):
            if current.status == "open" and current.qty_open > 0:
                pnl = self._exit(db, current, current.qty_open, price, pool_depth, reason)
        return pnl

    _close_all = close_all

    def check_exits(self, pos: PaperPosition, price: float, regime: str = "sideways", pool_depth: float | None = None) -> dict[str, Any]:
        _positive("price", price)
        _positive("pool depth", pool_depth or 0.0)
        with _position_transaction(pos) as (db, current):
            result = self._check_exits(db, current, price, pool_depth)
        return result

    def _check_exits(self, db, pos: PaperPosition, price: float, pool_depth: float) -> dict[str, Any]:
        if pos.status != "open":
            return {"action": "none", "reason": "already_closed"}
        self._mark(pos, price, _now())
        if pos.qty_open <= 0:
            pos.status, pos.closed_at, pos.exit_reason = "closed", _now(), "tp_exhausted"
            return {"action": "close_all", "reason": "tp_exhausted"}
        if price <= pos.stop_loss:
            pnl = self._exit(db, pos, pos.qty_open, price, pool_depth, "SL")
            return {"action": "close_all", "reason": "SL", "pnl": pnl}

        state = dict(pos.trailing_state or {})
        if state.get("active", False):
            if price <= state.get("stop", pos.stop_loss):
                pnl = self._exit(db, pos, pos.qty_open, price, pool_depth, "trailing_stop")
                return {"action": "close_all", "reason": "trailing_stop", "pnl": pnl}
            state["stop"] = max(state.get("stop", pos.stop_loss), price * (1 - state.get("step_pct", 0.035)))
        elif price >= pos.avg_entry * (1 + state.get("trigger_pct", 0.06)):
            state["active"] = True
            state["stop"] = price * (1 - state.get("step_pct", 0.035))
        state["peak"] = max(state.get("peak", price), price)
        pos.trailing_state = state

        levels = deepcopy(pos.tp_levels or [])
        for tp in levels:
            if pos.qty_open <= 0:
                break
            if price >= tp["price"] and not tp.get("done"):
                allocation = _positive("TP allocation", tp.get("alloc_pct", 0.30))
                if allocation > 1:
                    raise ValueError("TP allocation must not exceed one")
                qty = min(pos.qty_open, pos.qty * allocation)
                self._exit(db, pos, qty, price, pool_depth, f"TP{tp['level']}")
                tp.update(done=True, closed_qty=qty)
        pos.tp_levels = levels
        if pos.qty_open == 0:
            return {"action": "close_all", "reason": "tp_exhausted"}
        if levels and all(t.get("done") for t in levels):
            return {"action": "hold", "reason": "runner_managed", "detail": "TPs hit, trailing manages remainder"}
        return {"action": "hold", "reason": "none"}

    def smart_exit_triggers(self, pos: PaperPosition, *, liquidity_collapse: bool = False,
                            security_downgrade: bool = False, stale_data: bool = False,
                            regime_crash: bool = False, cluster_distribution: bool = False) -> list[str]:
        return [t for t, cond in [
            ("liquidity_collapse", liquidity_collapse), ("security_downgrade", security_downgrade),
            ("stale_data_timeout", stale_data), ("regime_crash", regime_crash),
            ("cluster_distribution", cluster_distribution),
        ] if cond]


class EmergencyLayer:
    """PRD 22. Manual kill switches + auto brakes, persisted to BotState + risk_events."""

    STATUSES = ("idle", "running", "paused", "safe")

    def get_state(self) -> dict[str, Any]:
        with session_scope() as db:
            bs = db.query(BotState).filter(BotState.id == 1).first()
            if bs is None:
                return {"status": "idle", "safe_mode": False, "emergency": {},
                        "account_id": None, "stats": {}}
            return {"status": bs.status, "safe_mode": bs.safe_mode, "emergency": bs.emergency or {},
                    "account_id": bs.account_id, "stats": bs.stats or {}}

    def set_state(self, status: str, account_id: int | None = None,
                  emergency: dict[str, Any] | None = None) -> dict[str, Any]:
        with session_scope() as db:
            bs = db.query(BotState).filter(BotState.id == 1).first()
            if bs is None:
                bs = BotState(id=1, status="idle", emergency={}, stats={})
                db.add(bs)
                db.flush()
            if status in self.STATUSES:
                bs.status = status
            if account_id is not None:
                bs.account_id = account_id
            if emergency is not None:
                bs.emergency = {**(bs.emergency or {}), **emergency}
            if status == "running" and bs.started_at is None:
                bs.started_at = _now()
            bs.safe_mode = status == "safe"
            db.flush()
            return {"status": bs.status, "safe_mode": bs.safe_mode, "emergency": bs.emergency or {},
                    "account_id": bs.account_id, "stats": bs.stats or {}}

    def raise_emergency(self, brakes: list[dict[str, Any]], account_id: int | None = None) -> None:
        with session_scope() as db:
            for b in brakes:
                db.add(RiskEvent(account_id=account_id, severity=b.get("severity", "critical"),
                                 type=b.get("type", "unknown"),
                                 details={"detail": b.get("detail", "")}))
            bs = db.query(BotState).filter(BotState.id == 1).first()
            if bs is None:
                bs = BotState(id=1, status="paused", emergency={}, stats={})
                db.add(bs)
            bs.emergency = {**(bs.emergency or {}), "active": True, "brakes": brakes,
                            "raised_at": _now().isoformat()}
            if bs.status == "running":
                bs.status = "paused"

    def clear_emergency(self) -> dict[str, Any]:
        with session_scope() as db:
            bs = db.query(BotState).filter(BotState.id == 1).first()
            if bs:
                bs.emergency = {**(bs.emergency or {}), "active": False,
                                "cleared_at": _now().isoformat()}
        return {"cleared": True}