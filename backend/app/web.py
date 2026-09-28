"""Server-rendered dashboard (PRD 25). FastAPI + Jinja2 + one CSS file + tiny vanilla JS.

No Node, no build step, no CDN: single Python process serves API and UI, so the whole app
fits the constrained home server.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .cache import CACHE
from .config import get_settings
from .db import session_scope
from .execution import EmergencyLayer
from .models import (
    AiMemory, AuditLog, BotState, CopyEvaluation, LearningEvent, ManipulationFlag, ModelVersion,
    Opportunity, PaperAccount, PaperPosition, PaperTrade, ProviderHealth, RiskEvent, StrategyRun,
    TournamentRun, User, Wallet, WalletReputationHistory, WalletScoreSnapshot, WalletTransaction,
)
from .notify import KINDS, NOTIFIER
from .regime import regime_label

BASE_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# The human UI lives under /app so the PRD 24 JSON paths (/opportunities, /positions, /trades)
# stay exactly as specified and are owned by the API routes in main.py.
router = APIRouter(prefix="/app")
# Auth and the site root stay at the top level: sign in and sign out must be reachable
# before any /app page exists for the session.
auth_router = APIRouter()
COOKIE = "ah_token"


def _uid(token: str | None) -> int | None:
    from fastapi import HTTPException
    from .auth import decode_token, require_operator
    uid = decode_token(token) if token else None
    if uid is None:
        return None
    try:
        return require_operator(uid)
    except HTTPException:
        return None


def _need_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def _money(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}"


def _pct(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v:+.{digits}f}%"


templates.env.filters["money"] = _money
templates.env.filters["pct"] = _pct
templates.env.globals["regime_label"] = regime_label


def _bot_block() -> dict[str, Any]:
    state = EmergencyLayer().get_state()
    return {
        "status": state["status"], "safe_mode": state["safe_mode"],
        "emergency_active": bool((state.get("emergency") or {}).get("active")),
        "brakes": (state.get("emergency") or {}).get("brakes", []),
        "account_id": state.get("account_id"),
    }


def _account(uid: int) -> PaperAccount | None:
    with session_scope() as db:
        return db.query(PaperAccount).filter(PaperAccount.user_id == uid).first()


def _settings_row(uid: int):
    with session_scope() as db:
        from .models import UserSettings

        return db.query(UserSettings).filter(UserSettings.user_id == uid).first()


# ---------------------------------------------------------------- auth pages
@auth_router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html",
                                      {"error": error, "demo": get_settings().demo_mode})


@auth_router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    from .auth import authenticate, create_token, limit_auth
    limit_auth(request)
    uid = authenticate(email, password)
    if uid is None:
        return RedirectResponse("/login?error=1", status_code=303)
    token = create_token(uid)
    resp = RedirectResponse("/app/dashboard", status_code=303)
    resp.set_cookie(COOKIE, token, httponly=True, secure=get_settings().production,
                    samesite="lax", max_age=7 * 86400)
    return resp


@auth_router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@auth_router.get("/")
def root(ah_token: str | None = Cookie(default=None)):
    return RedirectResponse("/app/dashboard" if _uid(ah_token) else "/login", status_code=303)


# ------------------------------------------------- cookie-authenticated controls
# The JSON API keeps its Bearer requirement (PRD 27.1). The UI posts here instead, so the
# browser never has to hold a token in JavaScript.
def _require(ah_token: str | None) -> int | None:
    return _uid(ah_token)


@router.post("/ui/bot/start")
def ui_bot_start(ah_token: str | None = Cookie(default=None)):
    from .models import UserSettings

    uid = _require(ah_token)
    if not uid:
        return _need_login()
    state = EmergencyLayer().get_state()
    with session_scope() as db:
        acc = db.query(PaperAccount).filter(PaperAccount.user_id == uid).first()
        if acc is None:
            return RedirectResponse("/app/settings?error=no+account", status_code=303)
        us = db.query(UserSettings).filter(UserSettings.user_id == uid).first()
        if us is None:
            db.add(UserSettings(user_id=uid, acknowledged_disclaimer=True))
        else:
            us.acknowledged_disclaimer = True
        db.add(AuditLog(actor_type="user", actor_id=str(uid), action="bot_start",
                        entity="account", entity_id=str(acc.id), payload={"via": "ui"}))
        account_id = acc.id
    EmergencyLayer().set_state("running", account_id=account_id)
    from .notify import notify

    notify("entry", "Bot started in paper mode by operator.", force=True)
    return RedirectResponse("/app/dashboard", status_code=303)


@router.post("/ui/bot/pause")
def ui_bot_pause(ah_token: str | None = Cookie(default=None)):
    uid = _require(ah_token)
    if not uid:
        return _need_login()
    with session_scope() as db:
        db.add(AuditLog(actor_type="user", actor_id=str(uid), action="bot_pause",
                        entity="bot_state", entity_id="1", payload={"via": "ui"}))
    EmergencyLayer().set_state("paused")
    return RedirectResponse("/app/dashboard", status_code=303)


@router.post("/ui/bot/safe-mode")
def ui_bot_safe(ah_token: str | None = Cookie(default=None)):
    uid = _require(ah_token)
    if not uid:
        return _need_login()
    with session_scope() as db:
        db.add(AuditLog(actor_type="user", actor_id=str(uid), action="bot_safe_mode",
                        entity="bot_state", entity_id="1", payload={"via": "ui"}))
    EmergencyLayer().set_state("safe")
    return RedirectResponse("/app/dashboard", status_code=303)


@router.post("/ui/bot/close-all")
def ui_bot_close_all(ah_token: str | None = Cookie(default=None)):
    uid = _require(ah_token)
    if not uid:
        return _need_login()
    from .main import bot_close_all

    try:
        result = bot_close_all(uid=uid)
    except HTTPException as exc:
        if exc.status_code == 409:
            return RedirectResponse("/app/settings?error=paper+close+blocked%3A+stale+or+missing+price%3B+bot+paused", status_code=303)
        raise
    with session_scope() as db:
        db.add(AuditLog(actor_type="user", actor_id=str(uid), action="bot_close_all",
                        entity="bot_state", entity_id="1", payload={"closed": result["closed"], "via": "ui"}))
    return RedirectResponse("/app/dashboard", status_code=303)


@router.post("/ui/bot/clear-emergency")
def ui_clear_emergency(ah_token: str | None = Cookie(default=None)):
    uid = _require(ah_token)
    if not uid:
        return _need_login()
    with session_scope() as db:
        db.add(AuditLog(actor_type="user", actor_id=str(uid), action="emergency_cleared",
                        entity="bot_state", entity_id="1", payload={"via": "ui"}))
    EmergencyLayer().clear_emergency()
    return RedirectResponse("/app/dashboard", status_code=303)


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, ah_token: str | None = Cookie(default=None)):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    acc = _account(uid)
    bot = _bot_block()
    with session_scope() as db:
        positions = db.query(PaperPosition).filter(PaperPosition.account_id == (acc.id if acc else -1),
                                                   PaperPosition.status == "open").all()
        trades = (db.query(PaperTrade).filter(PaperTrade.account_id == (acc.id if acc else -1))
                  .order_by(PaperTrade.exit_time.desc()).limit(10).all())
        opps = db.query(Opportunity).order_by(Opportunity.timestamp.desc()).limit(8).all()
        flows = (db.query(WalletTransaction).order_by(WalletTransaction.timestamp.desc())
                 .limit(8).all())
        open_notional = sum(p.notional_usd for p in positions)
        unreal = sum(((p.last_price or p.avg_entry) - p.avg_entry) * p.qty_open for p in positions)
        daily = ((acc.equity - acc.day_start_equity) / acc.day_start_equity * 100.0) if acc and acc.day_start_equity else 0.0
        peak = (acc.peak_equity if acc else 0.0) or 1.0
        drawdown = max(0.0, (peak - (acc.equity if acc else 0.0)) / peak * 100.0)
        ctx = {
            "nav": "dashboard", "bot": bot, "acc": acc,
            "equity": acc.equity if acc else 0.0,
            "starting_equity": acc.starting_equity if acc else 0.0,
            "realized": acc.realized_pnl if acc else 0.0,
            "unrealized": unreal, "daily_pnl_pct": daily, "drawdown_pct": drawdown,
            "open_notional": open_notional,
            "open_count": len(positions), "max_positions": get_settings().max_positions,
            "positions": positions, "trades": trades, "opps": opps, "flows": flows,
            "next_scan_s": get_settings().scan_interval_s,
        }
    return templates.TemplateResponse(request, "dashboard.html", ctx)


# ---------------------------------------------------------------- smart money
@router.get("/smart-money", response_class=HTMLResponse)
def smart_money(request: Request, ah_token: str | None = Cookie(default=None),
                wallet: int | None = None):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    with session_scope() as db:
        wallets = db.query(Wallet).order_by(Wallet.score.desc()).limit(50).all()
        flows = (db.query(WalletTransaction).order_by(WalletTransaction.timestamp.desc())
                 .limit(40).all())
        detail = None
        if wallet:
            w = db.query(Wallet).filter(Wallet.id == wallet).first()
            if w:
                snaps = (db.query(WalletScoreSnapshot).filter(WalletScoreSnapshot.wallet_id == w.id)
                         .order_by(WalletScoreSnapshot.timestamp.desc()).limit(20).all())
                reps = (db.query(WalletReputationHistory)
                        .filter(WalletReputationHistory.wallet_id == w.id)
                        .order_by(WalletReputationHistory.timestamp.desc()).limit(20).all())
                detail = {"wallet": w, "snaps": snaps, "reps": reps}
        ctx = {"nav": "smart", "bot": _bot_block(), "wallets": wallets, "flows": flows,
               "detail": detail}
    return templates.TemplateResponse(request, "smart_money.html", ctx)


# ---------------------------------------------------------------- opportunities
@router.get("/opportunities", response_class=HTMLResponse)
def opportunities(request: Request, ah_token: str | None = Cookie(default=None),
                  opp: int | None = None, decision: str | None = None):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    with session_scope() as db:
        q = db.query(Opportunity)
        if decision in ("BUY", "WATCH", "IGNORE"):
            q = q.filter(Opportunity.decision == decision)
        rows = q.order_by(Opportunity.timestamp.desc()).limit(60).all()
        detail = None
        if opp:
            o = db.query(Opportunity).filter(Opportunity.id == opp).first()
            if o:
                evals = db.query(CopyEvaluation).filter_by(opportunity_id=o.id).all()
                flags = db.query(ManipulationFlag).filter(ManipulationFlag.token_id == o.token_id).all()
                detail = {"opp": o, "evals": evals, "flags": flags}
        counts = {d: db.query(Opportunity).filter(Opportunity.decision == d).count()
                  for d in ("BUY", "WATCH", "IGNORE")}
        ctx = {"nav": "opps", "bot": _bot_block(), "rows": rows, "detail": detail,
               "decision": decision, "counts": counts}
    return templates.TemplateResponse(request, "opportunities.html", ctx)


# ---------------------------------------------------------------- positions
@router.get("/positions", response_class=HTMLResponse)
def positions_page(request: Request, ah_token: str | None = Cookie(default=None)):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    acc = _account(uid)
    with session_scope() as db:
        rows = (db.query(PaperPosition).filter(PaperPosition.account_id == (acc.id if acc else -1),
                                               PaperPosition.status == "open")
                .order_by(PaperPosition.opened_at.desc()).all())
        ctx = {"nav": "positions", "bot": _bot_block(), "rows": rows}
    return templates.TemplateResponse(request, "positions.html", ctx)


# ---------------------------------------------------------------- trades
@router.get("/trades", response_class=HTMLResponse)
def trades_page(request: Request, ah_token: str | None = Cookie(default=None)):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    acc = _account(uid)
    with session_scope() as db:
        rows = (db.query(PaperTrade).filter(PaperTrade.account_id == (acc.id if acc else -1))
                .order_by(PaperTrade.exit_time.desc()).limit(200).all())
        wins = [t for t in rows if t.pnl > 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = sum(t.pnl for t in rows if t.pnl <= 0)
        stats = {
            "n": len(rows), "wins": len(wins),
            "win_rate": (len(wins) / len(rows) * 100.0) if rows else 0.0,
            "expectancy": (sum(t.pnl for t in rows) / len(rows)) if rows else 0.0,
            "profit_factor": (gross_win / abs(gross_loss)) if gross_loss else None,
            "total": sum(t.pnl for t in rows),
        }
        ctx = {"nav": "trades", "bot": _bot_block(), "rows": rows, "stats": stats}
    return templates.TemplateResponse(request, "trades.html", ctx)


# ---------------------------------------------------------------- learning
@router.get("/learning", response_class=HTMLResponse)
def learning_page(request: Request, ah_token: str | None = Cookie(default=None)):
    return _learning_page(request, ah_token, None)


@router.post("/learning/run", response_class=HTMLResponse)
def learning_run(request: Request, ah_token: str | None = Cookie(default=None)):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    from .learning import Tournament
    from .models import Strategy
    with session_scope() as db:
        rows = db.query(Strategy).all()
        entrants = [{"strategy_id": row.id, "name": row.name,
                     "pnls": [r.expectation for r in db.query(StrategyRun).filter_by(strategy_id=row.id).all()]}
                    for row in rows]
    return _learning_page(request, ah_token, Tournament().run_tournament(entrants))


def _learning_page(request: Request, ah_token: str | None, result: dict | None):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    from .models import SignalQuality, Strategy

    with session_scope() as db:
        strategies = db.query(Strategy).all()
        runs = db.query(StrategyRun).order_by(StrategyRun.timestamp.desc()).limit(30).all()
        tours = db.query(TournamentRun).order_by(TournamentRun.timestamp.desc()).limit(10).all()
        models = db.query(ModelVersion).all()
        events = db.query(LearningEvent).order_by(LearningEvent.timestamp.desc()).limit(25).all()
        memory = db.query(AiMemory).order_by(AiMemory.updated_at.desc()).limit(12).all()
        resolved = db.query(SignalQuality).filter(SignalQuality.resolved == True).all()  # noqa: E712
        hits = [r for r in resolved if r.hit]
        ctx = {
            "nav": "learning", "bot": _bot_block(), "strategies": strategies, "runs": runs,
            "tours": tours, "models": models, "events": events, "memory": memory,
            "tournament": result,
            "signal": {"resolved": len(resolved), "hits": len(hits),
                       "hit_rate": (len(hits) / len(resolved) * 100.0) if resolved else None,
                       "pending": db.query(SignalQuality).filter(SignalQuality.resolved == False).count()},  # noqa: E712
            "gates": {"min_sample": 200, "min_per_regime": 30, "fdr_q": 0.1},
        }
    return templates.TemplateResponse(request, "learning.html", ctx)


# ---------------------------------------------------------------- settings
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, ah_token: str | None = Cookie(default=None),
                  saved: int = 0, error: str | None = None):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    s = get_settings()
    row = _settings_row(uid)
    acc = _account(uid)
    ctx = {"nav": "settings", "bot": _bot_block(), "cfg": s, "row": row, "acc": acc,
           "saved": bool(saved), "error": error, "notify_kinds": KINDS,
           "notifier": {"enabled": NOTIFIER.enabled, "sent": NOTIFIER.sent[-10:],
                        "failures": NOTIFIER.failures}}
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.post("/settings")
def settings_save(ah_token: str | None = Cookie(default=None),
                  risk_profile: str = Form("balanced"),
                  max_positions: int = Form(5),
                  max_daily_loss_pct: float = Form(5.0),
                  allowed_chains: str = Form("solana")):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    from .models import AuditLog, UserSettings

    chains = [c.strip() for c in allowed_chains.split(",") if c.strip()] or ["solana"]
    with session_scope() as db:
        row = db.query(UserSettings).filter(UserSettings.user_id == uid).first()
        if row is None:
            row = UserSettings(user_id=uid)
            db.add(row)
        row.risk_profile = risk_profile if risk_profile in ("conservative", "balanced", "aggressive") else "balanced"
        row.max_positions = max(1, min(int(max_positions), 20))
        row.max_daily_loss_pct = max(0.5, min(float(max_daily_loss_pct), 50.0))
        row.allowed_chains = chains
        db.add(AuditLog(actor_type="user", actor_id=str(uid), action="settings_updated",
                        entity="user_settings", entity_id=str(uid),
                        payload={"risk_profile": row.risk_profile, "max_positions": row.max_positions,
                                 "max_daily_loss_pct": row.max_daily_loss_pct,
                                 "allowed_chains": chains}))
    return RedirectResponse("/app/settings?saved=1", status_code=303)


# ---------------------------------------------------------------- health
@router.get("/health", response_class=HTMLResponse)
def health_page(request: Request, ah_token: str | None = Cookie(default=None)):
    uid = _uid(ah_token)
    if not uid:
        return _need_login()
    with session_scope() as db:
        providers = db.query(ProviderHealth).all()
        risk = db.query(RiskEvent).order_by(RiskEvent.timestamp.desc()).limit(20).all()
        audit = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(20).all()
        bots = db.query(BotState).all()
        stale = [p for p in providers if p.status != "healthy"]
        cache = CACHE.stats()
        ctx = {"nav": "health", "bot": _bot_block(), "providers": providers, "risk": risk,
               "audit": audit, "bots": bots, "stale": stale,
               "cost_day": sum(p.cost_usd_day for p in providers),
               "cost_budget": get_settings().data_cost_budget_usd_per_day,
               "cache": cache, "ttl_market": get_settings().ttl_market_s,
               "ttl_pool": get_settings().ttl_pool_s,
               "scan_interval": get_settings().scan_interval_s}
    return templates.TemplateResponse(request, "health.html", ctx)