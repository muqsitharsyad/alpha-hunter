"""Notifications (PRD 26). Telegram Bot API over plain httpx; optional, no-op when unconfigured.

Live inside the relayed config only, never logged.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx

from .cache import is_fresh
from .config import get_settings

log = logging.getLogger("alphahunter.notify")

# PRD 26 event kinds
KINDS = (
    "convergence",        # smart-money convergence detected
    "tier_s_action",      # tier-S wallet action
    "entry",              # auto paper entry executed
    "exit",               # TP1/TP2/SL/trailing
    "distribution",       # major whale distribution
    "risk_limit",         # daily risk limit / emergency brake
    "stale_feed",         # data source stale/outage
    "learning",           # model promoted/rolled back / tournament winner
    "manipulation",       # manipulation or MEV flag
)

_DEDUPE_TTL_S = 300


class Notifier:
    def __init__(self) -> None:
        s = get_settings()
        self.token = s.telegram_bot_token
        self.chat_id = s.telegram_chat_id
        self.topic_id = s.telegram_topic_id
        self.enabled = bool(self.token and self.chat_id)
        self.sent: list[dict[str, Any]] = []
        self.failures = 0

    def _dedupe_key(self, kind: str, text: str) -> str:
        return f"notify:{kind}:{hash(text) & 0xFFFFFFFF}"

    def notify(self, kind: str, text: str, force: bool = False) -> bool:
        """Send once per kind+text per 5 min. Returns True when actually delivered."""
        if kind not in KINDS:
            kind = "entry"
        if not force and is_fresh(self._dedupe_key(kind, text), _DEDUPE_TTL_S):
            return False
        from .cache import CACHE

        CACHE.set(self._dedupe_key(kind, text), True)
        stamped = f"[{kind}] {text}"
        self.sent.append({"kind": kind, "text": text, "ts": dt.datetime.now(dt.timezone.utc).isoformat()})
        self.sent = self.sent[-200:]
        if not self.enabled:
            log.info("notify (no telegram configured): %s", stamped)
            return False
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": stamped,
                                   "disable_web_page_preview": True}
        if self.topic_id:
            payload["message_thread_id"] = self.topic_id
        try:
            r = httpx.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                           json=payload, timeout=10.0)
            r.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001 - notification must never break trading
            self.failures += 1
            log.warning("telegram notify failed: %s", type(e).__name__)
            return False


NOTIFIER = Notifier()


def notify(kind: str, text: str, force: bool = False) -> bool:
    return NOTIFIER.notify(kind, text, force=force)