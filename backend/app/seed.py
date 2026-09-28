"""Demo/user seeds + PRD fixture data (Phase 0 deliverable: seeded fixtures)."""
from __future__ import annotations

import datetime as dt

from .auth import hash_password
from .config import get_settings
from .db import session_scope
from .execution import EmergencyLayer
from .models import BotState, PaperAccount, User, UserSettings


def seed_demo_user() -> int:
    if get_settings().production:
        raise RuntimeError("demo accounts cannot be seeded in production")
    with session_scope() as db:
        u = db.query(User).filter(User.email == "demo@alphahunter.local").first()
        if u:
            return u.id
        salt, dig = hash_password("demo1234")
        u = User(email="demo@alphahunter.local", password_hash=f"{salt}${dig}", status="active")
        db.add(u)
        db.flush()
        db.add(UserSettings(user_id=u.id, risk_profile="balanced", max_daily_loss_pct=5.0,
                            max_positions=5, allowed_chains=["solana"], acknowledged_disclaimer=True))
        acc = PaperAccount(user_id=u.id, starting_equity=10_000.0, equity=10_000.0,
                           peak_equity=10_000.0, day_start_equity=10_000.0,
                           day_key=__import__("datetime").datetime.now().strftime("%Y-%m-%d"), status="active")
        db.add(acc)
        db.flush()
        account_id = acc.id
    EmergencyLayer().set_state("idle", account_id=account_id)
    return u.id


def bootstrap_operator(email: str, password: str) -> int:
    """One-time local bootstrap; never rotates credentials on subsequent restarts."""
    if not email or not password or email == "demo@alphahunter.local" or len(password) < 16:
        raise ValueError("bootstrap requires a non-demo operator and strong password")
    with session_scope() as db:
        if db.query(User).filter(User.email == "demo@alphahunter.local").first():
            raise RuntimeError("demo account exists; production requires a fresh database")
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            return existing.id
        if db.query(User).first():
            raise RuntimeError("operator already exists; refusing to create a second")
        salt, dig = hash_password(password)
        user = User(email=email, password_hash=f"{salt}${dig}", status="active")
        db.add(user)
        db.flush()
        account = PaperAccount(user_id=user.id, starting_equity=10_000.0, equity=10_000.0,
                               peak_equity=10_000.0, day_start_equity=10_000.0,
                               day_key=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
        db.add_all([account, UserSettings(user_id=user.id, acknowledged_disclaimer=False)])
        db.flush()
        user_id, account_id = user.id, account.id
    EmergencyLayer().set_state("idle", account_id=account_id)
    return user_id


def validate_operator() -> None:
    """Fail production startup if an orphaned/demo account masks an unusable UI."""
    with session_scope() as db:
        users = db.query(User).all()
        if len(users) != 1 or users[0].status != "active" or users[0].email == "demo@alphahunter.local":
            raise RuntimeError("production requires exactly one active non-demo operator")
        account = db.query(PaperAccount).filter_by(user_id=users[0].id, status="active").first()
        state = db.get(BotState, 1)
        if account is None or state is None or state.account_id != account.id:
            raise RuntimeError("production operator has no linked active paper account")


__all__ = ["seed_demo_user", "bootstrap_operator", "validate_operator"]