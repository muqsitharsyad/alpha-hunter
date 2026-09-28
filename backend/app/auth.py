"""Signed sessions and single-operator authorization shared by API and UI."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dig = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()
    return salt, dig


def verify_password(pw: str, salt: str, digest: str) -> bool:
    _, d2 = hash_password(pw, salt)
    return hmac.compare_digest(digest, d2)


def _sign(payload: dict[str, Any]) -> str:
    body = ".".join(f"{k}={payload[k]}" for k in sorted(payload))
    return hmac.new(get_settings().secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()


def create_token(user_id: int) -> str:
    payload = {"uid": user_id, "t": int(time.time())}
    return f"{payload['uid']}.{payload['t']}.{_sign(payload)}"


def decode_token(token: str) -> int | None:
    try:
        uid, t, sig = token.split(".")
        payload = {"uid": int(uid), "t": int(t)}
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        if not 0 <= int(time.time()) - int(t) <= 7 * 86400 or int(uid) <= 0:
            return None
        return int(uid)
    except (ValueError, AttributeError, TypeError):
        return None


def require_operator(uid: int) -> int:
    from .db import session_scope
    from .models import BotState, PaperAccount, User

    with session_scope() as db:
        user = db.get(User, uid)
        if user is None or user.status != "active":
            raise HTTPException(401, "invalid session")
        if get_settings().production and user.email == "demo@alphahunter.local":
            raise HTTPException(403, "demo account is disabled in production")
        state = db.get(BotState, 1)
        account = (db.get(PaperAccount, state.account_id) if state and state.account_id
                   else db.query(PaperAccount).order_by(PaperAccount.id).first())
        if account is None or account.user_id != uid:
            raise HTTPException(403, "single-operator access only")
    return uid


def user_id_from_request(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> int:
    uid = decode_token(cred.credentials) if cred else None
    if uid is None:
        raise HTTPException(401, "invalid or missing bearer token")
    return require_operator(uid)


def check_csrf(request: Request) -> None:
    """Reject unsafe browser requests without a verifiable same-origin source."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    expected = get_settings().public_origin or str(request.base_url).rstrip("/")
    source = request.headers.get("origin")
    if source is None:
        ref = urlsplit(request.headers.get("referer", ""))
        source = f"{ref.scheme}://{ref.netloc}" if ref.scheme and ref.netloc else ""
    if source != expected or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "same-origin request required")


# ponytail: one-process limiter; use an edge/shared limiter before adding workers.
_auth_attempts: dict[str, tuple[float, int]] = {}
_auth_lock = threading.Lock()


def limit_auth(request: Request) -> None:
    cfg = get_settings()
    now = time.monotonic()
    key = request.client.host if request.client else "unknown"
    with _auth_lock:
        for host, (started, _) in list(_auth_attempts.items()):
            if now - started >= cfg.auth_window_s:
                del _auth_attempts[host]
        started, count = _auth_attempts.get(key, (now, 0))
        if count >= cfg.auth_attempts or (key not in _auth_attempts and len(_auth_attempts) >= 4096):
            raise HTTPException(429, "too many authentication attempts", headers={"Retry-After": str(cfg.auth_window_s)})
        _auth_attempts[key] = (started, count + 1)


def authenticate(email: str, password: str) -> int | None:
    from .db import session_scope
    from .models import User

    with session_scope() as db:
        user = db.query(User).filter(User.email == email).first()
        stored = user.password_hash if user and "$" in user.password_hash else "invalid$invalid"
        salt, digest = stored.split("$", 1)
        valid = verify_password(password, salt, digest)
        if not valid or user is None or user.status != "active":
            return None
        try:
            return require_operator(user.id)
        except HTTPException:
            return None
