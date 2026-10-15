"""Keyless public market adapters. Market metadata is NOT a token safety audit."""
from __future__ import annotations

import datetime as dt
import math
import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from ..config import get_settings
from .base import PriceBar, PoolSnapshot, ProviderError, ProviderDeferred, SwapEvent, TokenMeta, TokenMetadataProvider, record_call

_BASE_URLS = {"binance": "https://api.binance.com"}
GT_CHAIN_TAG = {"solana": "solana", "ethereum": "eth", "bsc": "bsc", "base": "base"}


def _chain(chain: str) -> str:
    if chain not in GT_CHAIN_TAG:
        raise ProviderError(f"unsupported chain: {chain}")
    return GT_CHAIN_TAG[chain]


def _address(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderError("missing address")
    return quote(value, safe="")


def _same_address(chain: str, left: str, right: str) -> bool:
    # Solana base58 addresses are case sensitive; EVM hex addresses are not.
    return left == right if chain == "solana" else left.lower() == right.lower()


class _Http:
    """Process-wide shared budget; exhausted budgets fail fast, never queue scans.

    GT interactive docs advertise ~10/min (variable), while FAQ says 30/min.
    Use 8/min, including retries. Multi-process deployments need a shared limiter.
    """

    LIMITS = {"goplus": 8, "geckoterminal": 8, "dexscreener": 60, "binance": 60}

    def __init__(self) -> None:
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._requests: dict[str, deque] = {}
        self._cooldown: dict[str, float] = {}

    def _c(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(8.0, connect=3.0),
                headers={"User-Agent": "AlphaHunterAI/1.0 (paper research)"},
                follow_redirects=False,
            )
        return self._client

    def _reserve(self, provider: str) -> None:
        with self._lock:
            now = time.monotonic()
            calls = self._requests.setdefault(provider, deque())
            while calls and calls[0] <= now - 60:
                calls.popleft()
            if now < self._cooldown.get(provider, 0):
                raise ProviderDeferred(f"{provider}: rate-limit cooldown active")
            if len(calls) >= self.LIMITS.get(provider, 8):
                raise ProviderDeferred(f"{provider}: free request budget exhausted")
            calls.append(now)

    def get_json(self, provider: str, url: str, **kw: Any) -> Any:
        t0 = time.monotonic()
        try:
            for attempt in range(2):
                self._reserve(provider)
                try:
                    response = self._c().get(url, **kw)
                    if response.status_code == 429:
                        retry = response.headers.get("Retry-After", "60")
                        try:
                            delay = float(retry)
                        except ValueError:
                            try:
                                delay = (parsedate_to_datetime(retry) - dt.datetime.now(dt.timezone.utc)).total_seconds()
                            except (ValueError, TypeError, OverflowError):
                                delay = 60.0
                        if not math.isfinite(delay):
                            delay = 60.0
                        with self._lock:
                            self._cooldown[provider] = time.monotonic() + max(60.0, delay)
                        raise ProviderError(f"{provider}: HTTP 429; retry deferred by cooldown")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, (dict, list)):
                        raise ValueError("expected JSON object or array")
                    record_call(provider, True, (time.monotonic() - t0) * 1000)
                    return payload
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    transient = isinstance(exc, httpx.TransportError) or exc.response.status_code >= 500
                    if not transient or attempt == 1:
                        raise ProviderError(f"{provider}: {type(exc).__name__}") from exc
                    time.sleep(0.25)
        except ProviderDeferred:
            raise
        except (ProviderError, ValueError) as exc:
            record_call(provider, False, (time.monotonic() - t0) * 1000)
            raise ProviderError(str(exc)) from exc
        raise ProviderError(f"{provider}: request failed")


_HTTP = _Http()


def _f(x: Any, default: float = 0.0) -> float:
    try:
        value = float(x)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _positive(x: Any, field: str, *, zero: bool = False) -> float:
    value = _f(x, float("nan"))
    if isinstance(x, bool) or not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ProviderError(f"missing or invalid {field}")
    return value


def _ts(value: Any) -> dt.datetime | None:
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(dt.timezone.utc) if result.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def _pool_from_attrs(p: dict[str, Any], chain: str, dex_id: str = "") -> PoolSnapshot:
    _chain(chain)
    if not p.get("address") or not p.get("base_token_address"):
        raise ProviderError("missing pool/token identity")
    h24 = (p.get("transactions") or {}).get("h24") or {}
    changes = p.get("price_change_percentage") or {}
    return PoolSnapshot(
        chain=chain, pool_address=p["address"], token_address=p["base_token_address"],
        symbol=p.get("base_token_symbol", ""), name=p.get("base_token_name") or p.get("name", ""),
        price_usd=_positive(p.get("base_token_price_usd"), "pool price"),
        liquidity_usd=_positive(p.get("reserve_in_usd"), "pool liquidity", zero=True),
        fdv_usd=_f(p.get("fdv_usd")),
        volume_24h_usd=_f((p.get("volume_usd") or {}).get("h24")),
        buys_24h=int(_f(h24.get("buys"))), sells_24h=int(_f(h24.get("sells"))),
        buyers_24h=int(_f(h24.get("buyers"))), sellers_24h=int(_f(h24.get("sellers"))),
        price_change_h1=_f(changes.get("h1")), price_change_h24=_f(changes.get("h24")),
        pool_created_at=_ts(p.get("pool_created_at")), dex=dex_id,
    )


def _parse_pool(row: dict, included: list, chain: str) -> PoolSnapshot:
    relationships = row.get("relationships") or {}
    base_id = ((relationships.get("base_token") or {}).get("data") or {}).get("id")
    dex_id = ((relationships.get("dex") or {}).get("data") or {}).get("id", "")
    token = next((r.get("attributes", {}) for r in included if r.get("id") == base_id), {})
    attrs = {**row.get("attributes", {}), "base_token_address": token.get("address", ""),
             "base_token_symbol": token.get("symbol", ""), "base_token_name": token.get("name", "")}
    return _pool_from_attrs(attrs, chain, dex_id)


class GeckoTerminalMarket:
    """Public GT market data, conservatively budgeted at 8 requests/minute."""

    name = "geckoterminal"

    def __init__(self) -> None:
        self.s = get_settings()
        self.client = _HTTP
        self.base = self.s.gt_base

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _pools(self, path: str, chain: str, limit: int) -> list[PoolSnapshot]:
        tag = _chain(chain)
        if limit <= 0:
            return []
        from ..cache import cached

        def fetch() -> list[PoolSnapshot]:
            payload = self.client.get_json(self.name, self._url(path.format(chain=tag)),
                                           params={"page": 1, "include": "base_token,dex"})
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ProviderError("invalid pool collection")
            return [_parse_pool(row, payload.get("included") or [], chain) for row in payload["data"]]

        # Cache parsed snapshots so fetching from cache cannot rejuvenate source_ts.
        rows, _, _ = cached(f"gt:pools:{self.base}:{path}:{tag}", self.s.ttl_pool_s, fetch)
        return rows[:limit]

    def trending_pools(self, chain: str, limit: int = 12) -> list[PoolSnapshot]:
        return self._pools("/networks/{chain}/trending_pools", chain, limit)

    def new_pools(self, chain: str, limit: int = 6) -> list[PoolSnapshot]:
        return self._pools("/networks/{chain}/new_pools", chain, limit)

    def pool_snapshot(self, chain: str, pool_address: str) -> PoolSnapshot | None:
        tag = _chain(chain)
        payload = self.client.get_json(self.name, self._url(f"/networks/{tag}/pools/{_address(pool_address)}"),
                                       params={"include": "base_token,dex"})
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ProviderError("missing pool snapshot")
        snap = _parse_pool(payload["data"], payload.get("included") or [], chain)
        if not _same_address(chain, snap.pool_address, pool_address):
            raise ProviderError("pool identity mismatch")
        return snap

    def candles(self, symbol: str, interval: str = "1d", limit: int = 200) -> list[PriceBar]:
        payload = self.client.get_json("binance", _BASE_URLS["binance"] + "/api/v3/klines",
                                       params={"symbol": symbol.replace("/", "").upper(),
                                               "interval": interval, "limit": min(limit, 1000)})
        out = []
        now = dt.datetime.now(dt.timezone.utc)
        for row in payload:
            if not isinstance(row, list) or len(row) < 7:
                raise ProviderError("invalid candle row")
            # Only closed candles: current in-progress bar must not enter historical regime estimates.
            if _positive(row[6], "candle close timestamp") > now.timestamp() * 1000:
                continue
            out.append(PriceBar(ts=dt.datetime.fromtimestamp(row[0] / 1000, dt.timezone.utc),
                                open=_positive(row[1], "open"), high=_positive(row[2], "high"),
                                low=_positive(row[3], "low"), close=_positive(row[4], "close"),
                                volume=_positive(row[5], "volume", zero=True)))
        if not out:
            raise ProviderError("no closed candles")
        return out

    def ticker(self, symbol: str) -> dict[str, Any]:
        payload = self.client.get_json("binance", _BASE_URLS["binance"] + "/api/v3/ticker/24hr",
                                       params={"symbol": symbol.replace("/", "").upper()})
        stamp = _positive(payload.get("closeTime"), "ticker timestamp") / 1000
        age = time.time() - stamp
        if not 0 <= age <= self.s.ttl_market_s:
            raise ProviderError("stale or future ticker timestamp")
        return {"price": _positive(payload.get("lastPrice"), "ticker price"),
                "volume": _f(payload.get("quoteVolume")),
                "change_24h_pct": _f(payload.get("priceChangePercent")), "source": "binance",
                "source_ts": dt.datetime.fromtimestamp(stamp, dt.timezone.utc)}


class GeckoTerminalSwaps:
    name = "geckoterminal"

    def __init__(self) -> None:
        self.client = _HTTP
        self.base = get_settings().gt_base

    def recent_swaps(self, pool_address: str, min_usd: float = 0.0, limit: int = 100) -> list[SwapEvent]:
        raise NotImplementedError("Use GeckoTerminalSwaps.recent_swaps_for_chain()")

    def recent_swaps_for_chain(self, chain: str, pool_address: str, min_usd: float = 0.0,
                               limit: int = 100) -> list[SwapEvent]:
        tag = _chain(chain)
        if limit <= 0:
            return []
        payload = self.client.get_json(self.name,
                                       f"{self.base}/networks/{tag}/pools/{_address(pool_address)}/trades",
                                       params={"trade_volume_in_usd_greater_than": max(0, min_usd)})
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderError("invalid swap collection")
        out = []
        for row in payload["data"]:
            a = row.get("attributes") or {}
            kind = a.get("kind")
            if kind not in ("buy", "sell"):
                continue
            value = _positive(a.get("volume_in_usd"), "swap value")
            if value < min_usd:
                continue
            stamp = _ts(a.get("block_timestamp"))
            if stamp is None or stamp > dt.datetime.now(dt.timezone.utc):
                raise ProviderError("missing or invalid swap timestamp")
            base, quote_side = ("to", "from") if kind == "buy" else ("from", "to")
            if not a.get("tx_hash") or not a.get("tx_from_address") or not a.get(f"{base}_token_address"):
                raise ProviderError("missing swap identity")
            out.append(SwapEvent(
                chain=chain, tx_hash=a["tx_hash"], wallet=a["tx_from_address"], pool_address=pool_address,
                token_address=a[f"{base}_token_address"], symbol="", side=kind,
                base_amount=_positive(a.get(f"{base}_token_amount"), "base amount"),
                quote_amount=_positive(a.get(f"{quote_side}_token_amount"), "quote amount"),
                price_usd=_positive(a.get(f"price_{base}_in_usd"), "swap price"),
                value_usd=value, block_time=stamp,
            ))
            if len(out) >= min(limit, 100):
                break
        return out


class DexScreenerTokenMeta(TokenMetadataProvider):
    """Identity/marketing metadata only; holder/security fields are unavailable."""

    name = "dexscreener"

    def __init__(self) -> None:
        self.client = _HTTP
        self.base = get_settings().dexscreener_base

    def token_meta(self, chain: str, token_address: str, pool_address: str = "") -> TokenMeta:
        _chain(chain)
        payload = self.client.get_json(self.name, f"{self.base}/token-pairs/v1/{chain}/{_address(token_address)}")
        if not isinstance(payload, list):
            raise ProviderError("invalid token pair collection")
        pairs = [p for p in payload if p.get("chainId") == chain
                 and _same_address(chain, (p.get("baseToken") or {}).get("address", ""), token_address)
                 and (not pool_address or _same_address(chain, p.get("pairAddress", ""), pool_address))]
        if not pairs:
            raise ProviderError("missing matching chain/token/pool metadata")
        pair = max(pairs, key=lambda p: _f((p.get("liquidity") or {}).get("usd")))
        token = pair["baseToken"]
        if not token.get("symbol") or not token.get("name"):
            raise ProviderError("missing token identity metadata")
        return TokenMeta(chain=chain, address=token_address, symbol=token["symbol"], name=token["name"],
                         metadata={"source": self.name, "info": pair.get("info") or {},
                                   "safety_status": "unavailable",
                                   "safety_reason": "Public pair API supplies no holder, LP lock, authority or sell-simulation evidence"})
