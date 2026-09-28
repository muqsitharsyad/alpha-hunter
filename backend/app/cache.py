"""In-process TTL cache + data-freshness gate (PRD 6.3, 15.2).

Decision-critical feeds must be within TTL or trading is blocked. One process, one cache:
no Redis needed for the single-node paper deployment.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self) -> None:
        self._d: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> tuple[Any, float] | None:
        """Returns (value, age_seconds) or None when missing/expired."""
        with self._lock:
            row = self._d.get(key)
            if row is None:
                self.misses += 1
                return None
            value, inserted = row
            age = time.time() - inserted
            if age > 0 and value is not None:
                self.hits += 1
            return value, age

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._d[key] = (value, time.time())

    def age(self, key: str) -> float | None:
        with self._lock:
            row = self._d.get(key)
            return (time.time() - row[1]) if row else None

    def invalidate(self, prefix: str = "") -> int:
        with self._lock:
            keys = [k for k in self._d if k.startswith(prefix)]
            for k in keys:
                self._d.pop(k, None)
            return len(keys)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {"entries": len(self._d), "hits": self.hits, "misses": self.misses,
                    "hit_rate": round(self.hits / total, 3) if total else 0.0}


CACHE = TTLCache()


def cached(key: str, ttl_s: float, producer: Callable[[], Any]) -> tuple[Any, float, bool]:
    """Return (value, age_seconds, from_cache). Serves stale-so-far entries only within ttl_s."""
    hit = CACHE.get(key)
    if hit is not None:
        value, age = hit
        if age <= ttl_s:
            return value, age, True
    value = producer()
    CACHE.set(key, value)
    return value, 0.0, False


def is_fresh(key: str, ttl_s: float) -> bool:
    age = CACHE.age(key)
    return age is not None and age <= ttl_s


class FreshnessGate:
    """PRD 15.2: 'Data freshness: all decision-critical feeds within TTL'."""

    def __init__(self) -> None:
        self.violations: list[dict[str, Any]] = []

    def check(self, label: str, key: str, ttl_s: float) -> bool:
        age = CACHE.age(key)
        ok = age is not None and age <= ttl_s
        if not ok:
            self.violations.append({"feed": label, "key": key, "age_s": age, "ttl_s": ttl_s})
        return ok

    def reset(self) -> None:
        self.violations = []

    @property
    def ok(self) -> bool:
        return not self.violations