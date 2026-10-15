"""FastAPI app: auth, bot controls, dashboard, wallet/opportunity/trade/learning/health endpoints,
SSE live feed, demo-user seeding."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import pathlib
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import authenticate, check_csrf, create_token, limit_auth, user_id_from_request
from .config import get_settings
from .db import init_db, session_scope
from .execution import EmergencyLayer, PaperPosition
from .models import (
    AuditLog, BotState, CopyEvaluation, LearningEvent, Market, Opportunity, PaperAccount,
    PaperOrder, PaperPosition, PaperTrade, ProviderHealth, RiskEvent, Strategy, StrategyRun,
    TournamentRun, User, UserSettings, Wallet, WalletScoreSnapshot,
)
from .pipeline import AlphaHunterPipeline
from .regime import RegimeEngine
from .seed import seed_demo_user

app = FastAPI(title="AlphaHunter AI", version="1.0.0", description="Smart Money Crypto Intelligence & Paper Trading Platform")
s = get_settings()
if s.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
                       allow_credentials=False, allow_methods=["GET"], allow_headers=["Authorization"])


@app.middleware("http")
async def protect_browser_forms(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and (
        request.url.path.startswith("/app/") or request.url.path == "/logout"
    ):
        try:
            check_csrf(request)
        except HTTPException as error:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": error.detail}, status_code=error.status_code)
    return await call_next(request)

_BASE = pathlib.Path(__file__).resolve().parents[1]
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")

from .web import auth_router, router as web_router  # noqa: E402  (needs app-level static mount first)

app.include_router(auth_router)
app.include_router(web_router)

s = get_settings()


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if s.demo_mode or __import__("os").environ.get("ALPHAHUNTER_FORCE_DEMO") == "1":
        if s.production:
            raise RuntimeError("demo mode is forbidden in production")
        seed_demo_user()
    elif s.bootstrap_email:
        from .seed import bootstrap_operator
        bootstrap_operator(s.bootstrap_email, s.bootstrap_password)
    if s.production:
        from .seed import validate_operator
        validate_operator()



# ---------------------------------------------------------------- auth
class RegisterIn(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=128)


def _valid_email(value: str) -> bool:
    """Cheap structural check; no email-validator dependency needed for a local paper tool."""
    return "@" in value and "." in value.split("@")[-1] and " " not in value


@app.post("/auth/register")
def register(body: RegisterIn):
    # Single operator: account creation is an explicit local bootstrap, never an HTTP endpoint.
    raise HTTPException(403, "public registration is disabled")


@app.post("/auth/login")
def login(body: LoginIn, request: Request):
    limit_auth(request)
    uid = authenticate(body.email, body.password)
    if uid is None:
        raise HTTPException(401, "invalid credentials")
    return {"token": create_token(uid)}


# ---------------------------------------------------------------- bot controls
class StartBotIn(BaseModel):
    risk_profile: str = "balanced"
    paper_capital: float = Field(default=10_000.0, gt=0, le=1_000_000)
    max_positions: int = Field(default=5, ge=1, le=20)
    max_daily_loss_pct: float = Field(default=5.0, gt=0, le=20)
    acknowledged: bool = False


@app.post("/bot/start")
def bot_start(body: StartBotIn, uid: int = Depends(user_id_from_request)):
    if not body.acknowledged:
        raise HTTPException(400, "must acknowledge paper-only disclaimer")
    with session_scope() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.user_id == uid).first()
        if not acc:
            raise HTTPException(404, "account not found")
        has_history = db.query(PaperOrder.id).filter(PaperOrder.account_id == acc.id).first() is not None
        if has_history and body.paper_capital != acc.starting_equity:
            raise HTTPException(409, "paper capital cannot be reset after an order; create a new ledger")
        if not has_history:
            acc.starting_equity = body.paper_capital
            acc.equity = body.paper_capital
            acc.peak_equity = body.paper_capital
            acc.day_start_equity = body.paper_capital
        us = db.query(UserSettings).filter(UserSettings.user_id == uid).first()
        if us:
            us.risk_profile = body.risk_profile
            us.max_positions = body.max_positions
            us.max_daily_loss_pct = body.max_daily_loss_pct
            us.acknowledged_disclaimer = True
        db.add(AuditLog(actor_type="user", actor_id=str(uid), action="bot_start",
                        entity="account", entity_id=str(acc.id),
                        payload={"risk_profile": body.risk_profile, "capital": body.paper_capital}))
    EmergencyLayer().set_state("running", acc.id)
    # return a running mask so the frontend can kick the poller
    return {"status": "running", "mode": "paper", "message": "Autonomous paper mode started"}


@app.post("/bot/pause")
def bot_pause(uid: int = Depends(user_id_from_request)):
    EmergencyLayer().set_state("paused")
    return {"status": "paused"}


@app.post("/bot/safe-mode")
def bot_safe(uid: int = Depends(user_id_from_request)):
    EmergencyLayer().set_state("safe")
    return {"status": "safe"}


@app.post("/bot/close-all")
def bot_close_all(uid: int = Depends(user_id_from_request)):
    EmergencyLayer().set_state("paused")
    with session_scope() as db:
        account = db.query(PaperAccount).filter_by(user_id=uid).first()
        if account is None:
            raise HTTPException(404, "account not found")
        pos = db.query(PaperPosition).filter(PaperPosition.account_id == account.id,
                                             PaperPosition.status == "open").all()
    pipeline = AlphaHunterPipeline()
    quotes = [(p, pipeline.executable_quote(p)) for p in pos]
    unavailable = [p.id for p, quote in quotes if quote is None]
    if unavailable:
        raise HTTPException(409, f"paper close blocked: stale or missing executable depth quote for position IDs {unavailable}; bot paused")
    # Validate the full batch before submitting any close; each close is atomic
    # and idempotent against monitoring or a repeated operator request.
    from .pipeline import _fresh_pool
    for p, quote in quotes:
        if not _fresh_pool(quote, s.ttl_pool_s):
            raise HTTPException(409, "paper close blocked: quote expired during batch; bot paused")
    for p, quote in quotes:
        pipeline.pos_mgr.close_all(p, quote.price_usd, "manual", pool_depth=quote.liquidity_usd)
    return {"status": "paused", "closed": len(pos)}


# ---------------------------------------------------------------- dashboard
def _dashboard(uid: int) -> dict[str, Any]:
    with session_scope() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.user_id == uid).first()
        if not acc:
            raise HTTPException(404, "no account")
        positions = db.query(PaperPosition).filter(PaperPosition.account_id == acc.id,
                                                   PaperPosition.status == "open").all()
        trades = db.query(PaperTrade).filter(PaperTrade.account_id == acc.id).order_by(PaperTrade.exit_time.desc()).limit(20).all()
        opps = db.query(Opportunity).order_by(Opportunity.timestamp.desc()).limit(15).all()
        wallets = db.query(Wallet).order_by(Wallet.score.desc()).limit(12).all()
        state = EmergencyLayer().get_state()
        open_notional = sum(p.notional_usd for p in positions)
        daily_pnl = (acc.equity - acc.day_start_equity) / acc.day_start_equity * 100.0 if acc.day_start_equity else 0.0
        price_map = {p.token_id: (p.last_price or p.avg_entry) for p in positions}
        return {
            "ai_status": state["status"], "mode": "PAPER", "safe_mode": state["safe_mode"],
            "emergency": state["emergency"],
            "equity": round(acc.equity, 2), "starting_equity": round(acc.starting_equity, 2),
            "realized_pnl": round(acc.realized_pnl, 2), "daily_pnl_pct": round(daily_pnl, 2),
            "open_positions": len(positions), "open_notional": round(open_notional, 2),
            "max_positions": s.max_positions,
            "positions": [{
                "id": p.id, "symbol": p.symbol, "qty": p.qty_open, "avg_entry": p.avg_entry,
                "last_price": price_map.get(p.token_id), "stop_loss": p.stop_loss,
                "tp_levels": p.tp_levels, "status": p.status, "strategy_version": p.strategy_version,
                "unrealized_pnl_pct": ((price_map.get(p.token_id, p.avg_entry) / p.avg_entry - 1.0) * 100.0) if p.avg_entry else 0.0,
            } for p in positions],
            "trades": [{
                "id": t.id, "symbol": t.position.symbol if t.position else "", "pnl": t.pnl,
                "pnl_pct": t.pnl_pct, "exit_price": t.exit_price, "exit_reason": t.exit_reason,
                "strategy_version": t.strategy_version, "exit_time": t.exit_time,
            } for t in trades],
            "recent_wallets": [{
                "address": w.address, "chain": w.chain, "score": w.score, "reputation": w.reputation,
                "tier": w.tier, "archetype": w.archetype, "confidence": w.confidence,
                "metrics": w.metrics,
            } for w in wallets],
            "recent_opportunities": [{
                "id": o.id, "symbol": o.symbol, "chain": o.chain, "scanner": o.scanner,
                "score": o.score, "decision": o.decision, "reasons": o.reasons[:3],
                "regime": o.regime, "timestamp": o.timestamp, "strategy_version": o.strategy_version,
            } for o in opps],
        }


@app.get("/dashboard/summary")
def dashboard_summary(uid: int = Depends(user_id_from_request)):
    return _dashboard(uid)


# ---------------------------------------------------------------- smart money
@app.get("/smart-money/wallets")
def wallets_list(uid: int = Depends(user_id_from_request), limit: int = Query(50, ge=1, le=500)):
    with session_scope() as db:
        rows = db.query(Wallet).order_by(Wallet.score.desc()).limit(limit).all()
        return {"wallets": [{
            "id": w.id, "address": w.address, "chain": w.chain, "score": w.score,
            "reputation": w.reputation, "tier": w.tier, "archetype": w.archetype,
            "confidence": w.confidence, "first_seen": w.first_seen, "last_seen": w.last_seen,
            "metrics": w.metrics,
        } for w in rows]}


@app.get("/smart-money/wallets/{wallet_id}")
def wallet_detail(wallet_id: int, uid: int = Depends(user_id_from_request)):
    with session_scope() as db:
        w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
        if not w:
            raise HTTPException(404, "wallet not found")
        snaps = db.query(WalletScoreSnapshot).filter(WalletScoreSnapshot.wallet_id == w.id).order_by(WalletScoreSnapshot.timestamp.desc()).limit(30).all()
        reps = []
        from .models import WalletReputationHistory
        reps = db.query(WalletReputationHistory).filter(WalletReputationHistory.wallet_id == w.id).order_by(WalletReputationHistory.timestamp.desc()).limit(30).all()
        return {
            "wallet": {"id": w.id, "address": w.address, "chain": w.chain, "score": w.score,
                       "reputation": w.reputation, "tier": w.tier, "archetype": w.archetype,
                       "confidence": w.confidence, "metrics": w.metrics, "labels": w.labels},
            "score_history": [{"components": s.components, "total": s.total_score, "ts": s.timestamp} for s in snaps],
            "reputation_history": [{"reputation": r.reputation, "drivers": r.drivers, "ts": r.timestamp} for r in reps],
        }


@app.get("/smart-money/flows")
def wallet_flows(uid: int = Depends(user_id_from_request), limit: int = Query(30, ge=1, le=200)):
    from .models import WalletTransaction
    with session_scope() as db:
        rows = db.query(WalletTransaction).order_by(WalletTransaction.timestamp.desc()).limit(limit).all()
        return {"flows": [{
            "wallet": r.wallet_id, "side": r.side, "value_usd": r.value_usd,
            "price_est": r.price_est, "token_address": r.token_address, "ts": r.timestamp,
        } for r in rows]}


# ---------------------------------------------------------------- opportunities
@app.get("/opportunities")
def opportunities(uid: int = Depends(user_id_from_request), limit: int = Query(50, ge=1, le=500)):
    with session_scope() as db:
        rows = db.query(Opportunity).order_by(Opportunity.timestamp.desc()).limit(limit).all()
        return {"opportunities": [{
            "id": o.id, "symbol": o.symbol, "chain": o.chain, "scanner": o.scanner,
            "score": o.score, "decision": o.decision, "reasons": o.reasons,
            "explanation": o.explanation, "components": o.components, "gates": o.gate_results,
            "feasibility": o.feasibility, "regime": o.regime, "strategy_version": o.strategy_version,
            "timestamp": o.timestamp,
        } for o in rows]}


@app.get("/opportunities/{opp_id}")
def opportunity_detail(opp_id: int, uid: int = Depends(user_id_from_request)):
    with session_scope() as db:
        o = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
        if not o:
            raise HTTPException(404, "opportunity not found")
        evals = db.query(CopyEvaluation).filter(CopyEvaluation.opportunity_id == o.id).all()
        flags = []
        from .models import ManipulationFlag
        flags = db.query(ManipulationFlag).filter(ManipulationFlag.token_id == o.token_id).all()
        return {
            "opportunity": {
                "id": o.id, "symbol": o.symbol, "chain": o.chain, "scanner": o.scanner,
                "score": o.score, "decision": o.decision,
                "reasons": o.reasons, "explanation": o.explanation,
                "components": o.components, "gates": o.gate_results, "feasibility": o.feasibility,
                "features": o.features, "regime": o.regime, "strategy_version": o.strategy_version,
                "feature_hash": o.feature_hash, "timestamp": o.timestamp,
            },
            "copy_evaluations": [{"feasible": c.feasible, "slippage_bps": c.expected_slippage_bps,
                                  "signal_age_s": c.signal_age_s, "reason": c.reason} for c in evals],
            "manipulation_flags": [{"flag_type": f.flag_type, "severity": f.severity,
                                    "evidence": f.evidence} for f in flags],
        }


# ---------------------------------------------------------------- positions / trades
@app.get("/positions")
def positions(uid: int = Depends(user_id_from_request)):
    with session_scope() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.user_id == uid).first()
        if not acc:
            raise HTTPException(404, "no account")
        rows = db.query(PaperPosition).filter(PaperPosition.account_id == acc.id, PaperPosition.status == "open").order_by(PaperPosition.opened_at.desc()).all()
        return {"positions": [{
            "id": p.id, "symbol": p.symbol, "chain": p.chain, "qty": p.qty_open, "avg_entry": p.avg_entry,
            "last_price": p.last_price, "stop_loss": p.stop_loss, "tp_levels": p.tp_levels,
            "trailing_state": p.trailing_state, "notional_usd": p.notional_usd, "status": p.status,
            "strategy_version": p.strategy_version, "opened_at": p.opened_at, "events": p.events,
            "unrealized_pnl_pct": ((p.last_price or p.avg_entry) / p.avg_entry - 1.0) * 100.0 if p.avg_entry else 0.0,
        } for p in rows]}


@app.get("/trades")
def trades(uid: int = Depends(user_id_from_request), limit: int = Query(100, ge=1, le=1000)):
    with session_scope() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.user_id == uid).first()
        if not acc:
            raise HTTPException(404, "no account")
        rows = db.query(PaperTrade).filter(PaperTrade.account_id == acc.id).order_by(PaperTrade.exit_time.desc()).limit(limit).all()
        return {"trades": [{
            "id": t.id, "symbol": getattr(t.position, "symbol", "") if t.position else "",
            "entry_time": t.entry_time, "exit_time": t.exit_time, "qty": t.qty,
            "entry_price": t.entry_price, "exit_price": t.exit_price, "pnl": t.pnl,
            "pnl_pct": t.pnl_pct, "r_multiple": t.r_multiple, "max_favorable": t.max_favorable,
            "max_adverse": t.max_adverse, "duration_s": t.duration_s, "exit_reason": t.exit_reason,
            "strategy_version": t.strategy_version, "regime": t.regime,
        } for t in rows]}


# ---------------------------------------------------------------- learning
@app.get("/learning/summary")
def learning_summary(uid: int = Depends(user_id_from_request)):
    with session_scope() as db:
        events = db.query(LearningEvent).all()
        strategies = db.query(Strategy).all()
        runs = db.query(StrategyRun).all()
        tours = db.query(TournamentRun).order_by(TournamentRun.timestamp.desc()).limit(5).all()
        models = []
        from .models import ModelVersion
        models = db.query(ModelVersion).all()
        return {
            "events": len(events),
            "strategies": [{"id": s.id, "name": s.name, "version": s.version, "status": s.status,
                            "rationale": s.rationale, "metrics": s.metrics} for s in strategies],
            "strategy_runs": [{"strategy_id": r.strategy_id, "regime": r.regime, "n": r.sample_count,
                               "pnl": r.pnl, "expectancy": r.expectation, "sharpe": r.sharpe} for r in runs],
            "tournaments": [{"id": t.id, "winner_strategy_id": t.winner_strategy_id, "promoted": t.promoted,
                             "notes": t.notes, "ranking": t.ranking[:5], "timestamp": t.timestamp} for t in tours],
            "model_versions": [{"model_type": m.model_type, "version": m.version, "status": m.status,
                                "metrics": m.metrics, "gates": m.gates, "promoted_at": m.promoted_at} for m in models],
            "statistical_gates": {"min_sample": 200, "min_per_regime": 30, "fdr_q": 0.1},
        }


@app.get("/strategies")
def strategies(uid: int = Depends(user_id_from_request)):
    with session_scope() as db:
        rows = db.query(Strategy).order_by(Strategy.created_at.desc()).all()
        return {"strategies": [{
            "id": s.id, "name": s.name, "version": s.version, "status": s.status,
            "config": s.config, "rationale": s.rationale, "metrics": s.metrics,
        } for s in rows]}


@app.get("/research/forward")
def forward_research(uid: int = Depends(user_id_from_request)):
    from .forward_study import summary
    return summary()


@app.get("/system/health")
def system_health(uid: int = Depends(user_id_from_request)):
    with session_scope() as db:
        prov = db.query(ProviderHealth).all()
        risk = db.query(RiskEvent).order_by(RiskEvent.timestamp.desc()).limit(20).all()
        audit = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(10).all()
        bot = EmergencyLayer().get_state()
        return {
            "bot_state": bot,
            "providers": [{"provider": p.provider, "status": p.status, "error_rate_5m": p.error_rate_5m,
                           "latency_ms_p95": p.latency_ms_p95, "calls": p.calls, "errors": p.errors,
                           "last_success": p.last_success, "cost_usd_day": p.cost_usd_day} for p in prov],
            "risk_events": [{"type": r.type, "severity": r.severity, "details": r.details, "ts": r.timestamp} for r in risk],
            "audit": [{"action": a.action, "entity": a.entity, "entity_id": a.entity_id,
                       "actor": a.actor_id, "ts": a.timestamp} for a in audit],
        }


# ---------------------------------------------------------------- SSE live
@app.get("/live")
def live_feed(uid: int = Depends(user_id_from_request)):
    async def gen():
        last = 0
        while True:
            with session_scope() as db:
                row = db.query(Opportunity).order_by(Opportunity.timestamp.desc()).first()
                pos = db.query(PaperPosition).filter(PaperPosition.status == "open").count()
                bs = db.query(BotState).filter(BotState.id == 1).first()
                payload = {
                    "latest_opportunity_id": row.id if row else None,
                    "open_positions": pos,
                    "bot_status": bs.status if bs else "idle",
                    "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            if payload["latest_opportunity_id"] != last:
                yield f"data: {json.dumps(payload)}\n\n"
                last = payload["latest_opportunity_id"]
            await asyncio.sleep(5)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- worker hook
_worker_task: asyncio.Task | None = None
_worker_stop_requested: asyncio.Event | None = None
_log = logging.getLogger(__name__)


async def _worker_cycle(pipeline: AlphaHunterPipeline) -> dict[str, Any]:
    """Run scan and position monitoring independently.

    A provider/scan failure must not skip position monitoring. The worker loop owns
    logging; callers receive a compact result for tests and diagnostics.
    """
    result: dict[str, Any] = {"scan": None, "monitor": None, "errors": []}
    state = await asyncio.to_thread(EmergencyLayer().get_state)
    async def run_scan():
        if (state["status"] not in ("running", "safe") or state["emergency"].get("active")) and not pipeline.s.collector_enabled:
            return
        try:
            # Do not wrap synchronous work in wait_for(): cancellation only stops
            # the await, leaving its thread able to write after this cycle returns.
            # Provider calls have bounded HTTP timeouts; this cycle waits for the
            # scan to finish so there is never an abandoned, overlapping writer.
            return await asyncio.to_thread(pipeline.scan_cycle)
        except Exception as exc:  # noqa: BLE001 - isolate scan from monitoring
            result["errors"].append(f"scan: {type(exc).__name__}: {exc}")
            _log.exception("paper worker scan failed")

    async def run_monitor():
        if state["status"] not in ("running", "safe", "paused") or not state.get("account_id"):
            return
        try:
            return await asyncio.to_thread(pipeline.refresh_positions)
        except Exception as exc:  # noqa: BLE001 - keep worker alive for next cycle
            result["errors"].append(f"monitor: {type(exc).__name__}: {exc}")
            _log.exception("paper worker monitoring failed")

    # Monitoring owns first use of the shared quota and SQLite writer. A scan
    # failure is isolated below and cannot undo this already-completed pass.
    result["monitor"] = await run_monitor()
    result["scan"] = await run_scan()
    return result


async def _worker_loop():
    pipeline = AlphaHunterPipeline()
    # inherit bot state (paused until user starts)
    while _worker_stop_requested is None or not _worker_stop_requested.is_set():
        try:
            await _worker_cycle(pipeline)
            if _worker_stop_requested is not None:
                try:
                    await asyncio.wait_for(_worker_stop_requested.wait(), timeout=s.scan_interval_s)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            _log.info("paper worker stopped")
            raise
        except Exception:  # noqa: BLE001 - last-resort loop guard
            _log.exception("paper worker cycle failed")
            if _worker_stop_requested is not None:
                try:
                    await asyncio.wait_for(_worker_stop_requested.wait(), timeout=s.scan_interval_s)
                except asyncio.TimeoutError:
                    pass


@app.on_event("startup")
async def _start_worker():
    global _worker_task, _worker_stop_requested
    _worker_stop_requested = asyncio.Event()
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop(), name="alphahunter-paper-worker")


@app.on_event("shutdown")
async def _stop_worker():
    global _worker_task, _worker_stop_requested
    task = _worker_task
    _worker_task = None
    if _worker_stop_requested is not None:
        _worker_stop_requested.set()
    if task is not None and not task.done():
        # Drain rather than cancel. asyncio.to_thread cancellation does not stop the
        # underlying synchronous work, so cancelling here would permit writes after
        # the ASGI shutdown hook returned.
        await task
    _worker_stop_requested = None