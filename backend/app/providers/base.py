"""Provider interfaces, health/telemetry, cost guardrail, demo-mode routing."""
from __future__ import annotations

import abc
import datetime as dt
import os
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings


class ProviderError(RuntimeError):
    pass


class ProviderDeferred(ProviderError):
    """Local quota/cooldown prevented a request; not an upstream failure."""


class StaleDataError(ProviderError):
    """Raised when a decision-critical feed is older than its TTL (PRD 15.2 hard gate)."""


@dataclass
class PriceBar:
    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class PoolSnapshot:
    chain: str
    pool_address: str
    token_address: str
    symbol: str
    name: str
    price_usd: float
    liquidity_usd: float
    fdv_usd: float
    volume_24h_usd: float
    buys_24h: int
    sells_24h: int
    buyers_24h: int
    sellers_24h: int
    price_change_h1: float
    price_change_h24: float
    pool_created_at: dt.datetime | None
    dex: str = ""
    source_ts: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def age_hours(self) -> float:
        if not self.pool_created_at:
            return 0.0
        delta = dt.datetime.now(dt.timezone.utc) - self.pool_created_at
        return max(delta.total_seconds() / 3600.0, 0.0)


@dataclass
class SwapEvent:
    """Normalized on-chain swap. ``wallet`` is the human/signer, not the pool."""

    chain: str
    tx_hash: str
    wallet: str
    pool_address: str
    token_address: str
    symbol: str
    side: str  # buy|sell
    base_amount: float
    quote_amount: float
    price_usd: float
    value_usd: float
    block_time: dt.datetime
    source_ts: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


@dataclass
class TokenMeta:
    chain: str
    address: str
    symbol: str
    name: str
    decimals: int = 9
    total_supply: float = 0.0
    holder_count: int | None = None
    top10_holder_pct: float | None = None
    deployer_address: str = ""
    deployer_holder_pct: float | None = None
    lp_locked: bool | None = None
    mint_authority_revoked: bool | None = None
    freeze_authority_revoked: bool | None = None
    transfer_tax_bps: float | None = None
    sell_simulation_ok: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# --- health / telemetry -----------------------------------------------------
class _HealthRegistry:
    """In-process provider health (also mirrored to provider_health table)."""

    def __init__(self) -> None:
        self.calls: dict[str, list[tuple[float, bool, float]]] = {}

    def stats_all(self) -> dict[str, dict[str, Any]]:
        return {p: self.stats(p) for p in self.calls}

    def record(self, provider: str, ok: bool, latency_ms: float) -> None:
        bucket = self.calls.setdefault(provider, [])
        bucket.append((time.time(), ok, latency_ms))
        cutoff = time.time() - 900
        self.calls[provider] = [c for c in bucket if c[0] >= cutoff]

    def stats(self, provider: str) -> dict[str, Any]:
        rows = self.calls.get(provider, [])
        if not rows:
            return {"calls": 0, "errors": 0, "error_rate_5m": 0.0, "latency_ms_p95": 0.0,
                    "last_success": None, "status": "unknown"}
        recent5 = [r for r in rows if r[0] >= time.time() - 300]
        errs5 = [r for r in recent5 if not r[1]]
        lat = sorted(r[2] for r in rows)
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
        succ = [r[0] for r in rows if r[1]]
        return {
            "calls": len(rows),
            "errors": sum(1 for r in rows if not r[1]),
            "error_rate_5m": (len(errs5) / len(recent5)) if recent5 else 0.0,
            "latency_ms_p95": p95,
            "last_success": dt.datetime.fromtimestamp(max(succ), dt.timezone.utc) if succ else None,
            "status": "healthy" if (not recent5 or len(errs5) / len(recent5) < 0.5) else "degraded",
        }


HEALTH = _HealthRegistry()


def record_call(provider: str, ok: bool, latency_ms: float) -> None:
    HEALTH.record(provider, ok, latency_ms)
    from ..db import session_scope
    from ..models import ProviderCall
    with session_scope() as db:
        db.add(ProviderCall(provider=provider, ok=ok, latency_ms=latency_ms))


# --- interfaces -------------------------------------------------------------
class ChainActivityProvider(abc.ABC):
    name: str = "chain"

    @abc.abstractmethod
    def recent_swaps(self, pool_address: str, min_usd: float = 0.0, limit: int = 100) -> list[SwapEvent]:
        ...


class MarketDataProvider(abc.ABC):
    name: str = "market"

    @abc.abstractmethod
    def trending_pools(self, chain: str, limit: int = 12) -> list[PoolSnapshot]:
        ...

    @abc.abstractmethod
    def new_pools(self, chain: str, limit: int = 6) -> list[PoolSnapshot]:
        ...

    @abc.abstractmethod
    def pool_snapshot(self, chain: str, pool_address: str) -> PoolSnapshot | None:
        ...

    @abc.abstractmethod
    def candles(self, symbol: str, interval: str = "1d", limit: int = 200) -> list[PriceBar]:
        ...

    @abc.abstractmethod
    def ticker(self, symbol: str) -> dict[str, Any]:
        ...


class TokenMetadataProvider(abc.ABC):
    name: str = "token"

    @abc.abstractmethod
    def token_meta(self, chain: str, token_address: str, pool_address: str = "") -> TokenMeta:
        ...


class SocialDataProvider(abc.ABC):
    name: str = "social"

    @abc.abstractmethod
    def narrative_score(self, chain: str, token_address: str, symbol: str) -> float | None:
        ...


class NewsProvider(abc.ABC):
    """Placeholder interface: v1 ships no news adapter (optional domain, PRD 7.2)."""

    name: str = "news"

    @abc.abstractmethod
    def headlines(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        ...


class AdapterRegistry:
    """Holds the active adapter set. ``demo_mode`` swaps in deterministic fixtures."""

    def __init__(self) -> None:
        self.chain: ChainActivityProvider | None = None
        self.market: MarketDataProvider | None = None
        self.token: TokenMetadataProvider | None = None
        self.social: SocialDataProvider | None = None

    def load(self, demo: bool | None = None) -> "AdapterRegistry":
        demo = get_settings().demo_mode if demo is None else demo
        if demo or os.environ.get("ALPHAHUNTER_FORCE_DEMO") == "1":
            from .demo import DemoChainActivity, DemoMarketData, DemoTokenMetadata

            self.chain = DemoChainActivity()
            self.market = DemoMarketData()
            self.token = DemoTokenMetadata()
            self.social = None
            return self
        from .free import GeckoTerminalMarket, GeckoTerminalSwaps
        from .security import GoPlusTokenSecurity

        self.chain = GeckoTerminalSwaps()
        self.market = GeckoTerminalMarket()
        self.token = GoPlusTokenSecurity()
        self.social = None
        return self

    def reload(self) -> None:
        self.load()


_REGISTRY = AdapterRegistry()


def get_adapters() -> AdapterRegistry:
    if _REGISTRY.market is None:
        _REGISTRY.load()
    return _REGISTRY


def estimate_data_cost_usd_per_day(calls_per_day: int, provider_class: str = "free") -> float:
    """PRD 7.2 cost guardrail. Free providers cost 0; kept explicit for paid swaps."""
    rates = {"free": 0.0, "standard": 0.00002, "premium": 0.0005}
    return calls_per_day * rates.get(provider_class, 0.0)
