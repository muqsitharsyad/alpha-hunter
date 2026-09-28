"""Deterministic fixtures for DEMO_MODE=true (no network). Exercise every engine:
listed-token scanners, new-token scanner, wallet ledger, consensus, hard gates,
execution, TP/SL/trailing, emergency brakes, learning events.

Whale wallets carry a multi-token history (A tier via PRD 8.2 scoring) so the
autonomous path can produce a real BUY deterministically. Genes are synthetic."""
from __future__ import annotations

import datetime as dt
from typing import Any

from .base import PoolSnapshot, PriceBar, SwapEvent, TokenMeta


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ago(**kw: float) -> dt.datetime:
    return _now() - dt.timedelta(**kw)


PUMPD = dict(
    chain="solana", pool_address="demo_pool_pump", token_address="DEMO_PUMP",
    symbol="PUMPD", name="PUMPD / SOL", price_usd=0.0042, liquidity_usd=250_000.0,
    fdv_usd=1_400_000.0, volume_24h_usd=2_400_000.0, buys_24h=4800, sells_24h=2100,
    buyers_24h=2200, sellers_24h=730, price_change_h1=3.1, price_change_h24=145.0,
    pool_created_at=_ago(hours=26),
)
MEME2 = dict(
    chain="solana", pool_address="demo_pool_meme2", token_address="DEMO_MEME2",
    symbol="MEME2", name="MEME2 / SOL", price_usd=0.0008, liquidity_usd=52_000.0,
    fdv_usd=300_000.0, volume_24h_usd=700_000.0, buys_24h=1450, sells_24h=1390,
    buyers_24h=610, sellers_24h=590, price_change_h1=-2.4, price_change_h24=41.0,
    pool_created_at=_ago(hours=6),
)
SOLP = dict(
    chain="solana", pool_address="demo_pool_sol", token_address="DEMO_SOL",
    symbol="SOLUSDT", name="SOL / USDT", price_usd=102.0, liquidity_usd=450_000.0,
    fdv_usd=60_000_000.0, volume_24h_usd=8_000_000.0, buys_24h=5000, sells_24h=4800,
    buyers_24h=2200, sellers_24h=2100, price_change_h1=0.8, price_change_h24=4.2,
    pool_created_at=_ago(days=400),
)

DEMO_POOLS = [PUMPD, MEME2, SOLP]

# Each whale: 6x PUMPD buys (rising entries), 5x SOL round-trips, 4x MEME2 buys.
# Entries far below current -> strong realized. Timestamps inside window -> consensus.
def _history(prefix: str, pump_now, sol_now, meme_now):
    out = []
    # PUMPD accumulation (15 legs over 30 days -> n>=30 with sol+meme round-trips)
    pump_entries = [(30, 0.00030), (27, 0.00045), (24, 0.00060), (21, 0.00080),
                    (18, 0.00100), (15, 0.00125), (12, 0.00150), (9, 0.00175),
                    (7, 0.00200), (5, 0.00230), (3.5, 0.00260), (2, 0.00300),
                    (1.2, 0.00340), (0.7, 0.00370), (0.4, 0.00390)]
    for i, (days, px) in enumerate(pump_entries):
        out.append(dict(chain="solana", tx_hash=f"tx_{prefix}_p{i}", wallet=f"whale_sol_{prefix}",
                        pool_address="demo_pool_pump", token_address="DEMO_PUMP",
                        side="buy", value_usd=2600.0 + (30 - days) * 30, price_usd=px,
                        block_time=_ago(days=days) if days >= 1 else _ago(hours=days * 24)))
    # SOL round-trips (balanced buy/sell -> exposure ~0.6, positive realized)
    sol_legs = [("buy", 29, 72.0, 1600.0), ("sell", 28, 74.5, 1600.0),
                ("buy", 26, 76.0, 1600.0), ("sell", 25, 78.5, 1600.0),
                ("buy", 23, 80.0, 1600.0), ("sell", 22, 82.5, 1600.0),
                ("buy", 20, 83.5, 1600.0), ("sell", 19, 85.0, 1600.0),
                ("buy", 17, 86.0, 1600.0), ("sell", 16, 87.5, 1600.0),
                ("buy", 14, 89.0, 1600.0), ("sell", 13, 90.5, 1600.0),
                ("buy", 11, 91.0, 1600.0), ("sell", 10, 92.5, 1600.0),
                ("buy", 8, 94.0, 1600.0), ("sell", 7, 95.0, 1600.0),
                ("buy", 6, 95.5, 1600.0), ("sell", 5, 96.5, 1600.0),
                ("buy", 3, 98.0, 1600.0), ("sell", 2, 99.0, 1600.0),
                ("buy", 1, 100.0, 1600.0)]
    for j, (side, days, px, val) in enumerate(sol_legs):
        out.append(dict(chain="solana", tx_hash=f"tx_{prefix}_sol{j}", wallet=f"whale_sol_{prefix}",
                        pool_address="demo_pool_sol", token_address="DEMO_SOL",
                        side=side, value_usd=val, price_usd=px, block_time=_ago(days=days)))
    # MEME2 early buys
    for k, (days, px) in enumerate([(24, 0.00024), (20, 0.00030), (16, 0.00036), (12, 0.00042),
                                    (8, 0.00048), (4, 0.00055), (1.2, 0.00062)]):
        out.append(dict(chain="solana", tx_hash=f"tx_{prefix}_m{k}", wallet=f"whale_sol_{prefix}",
                        pool_address="demo_pool_meme2", token_address="DEMO_MEME2",
                        side="buy", value_usd=1300.0, price_usd=px, block_time=_ago(days=days)))
    return out


W1 = _history("alpha", 0, 0, 0)
W2 = _history("beta", 0, 0, 0)
W3 = _history("gamma", 0, 0, 0)
# current-window buys for PUMPD consensus (3 whales, 30 min window)
W1 += [dict(chain="solana", tx_hash="tx_whale1_pump_now", wallet="whale_sol_alpha",
            pool_address="demo_pool_pump", token_address="DEMO_PUMP", side="buy",
            value_usd=4500.0, price_usd=0.0039, block_time=_ago(minutes=26))]
W2 += [dict(chain="solana", tx_hash="tx_whale2_pump_now", wallet="whale_sol_beta",
            pool_address="demo_pool_pump", token_address="DEMO_PUMP", side="buy",
            value_usd=6200.0, price_usd=0.0040, block_time=_ago(minutes=9))]
W3 += [dict(chain="solana", tx_hash="tx_whale3_pump_now", wallet="whale_sol_gamma",
            pool_address="demo_pool_pump", token_address="DEMO_PUMP", side="buy",
            value_usd=5300.0, price_usd=0.00405, block_time=_ago(minutes=3))]
# MEME2: gamma distributes (negative flow, divergence veto)
W3 += [dict(chain="solana", tx_hash="tx_whale3_meme2_sell", wallet="whale_sol_gamma",
            pool_address="demo_pool_meme2", token_address="DEMO_MEME2", side="sell",
            value_usd=2800.0, price_usd=0.00082, block_time=_ago(minutes=11))]

DEMO_SWAPS = W1 + W2 + W3

DEMO_BARS = []
_base = 62000.0
for i in range(60):
    _base = _base * (1.0 + (0.008 if i % 9 < 6 else 0.001))
    DEMO_BARS.append(PriceBar(ts=_now() - dt.timedelta(days=60 - i), open=_base,
                              high=_base * 1.02, low=_base * 0.98, close=_base * (1.002),
                              volume=1.4e9))


class DemoMarketData:
    name = "demo_market"

    def trending_pools(self, chain: str, limit: int = 12) -> list[PoolSnapshot]:
        return [PoolSnapshot(**p) for p in DEMO_POOLS if p["chain"] == chain][:limit]

    def new_pools(self, chain: str, limit: int = 6) -> list[PoolSnapshot]:
        return [PoolSnapshot(**p) for p in DEMO_POOLS
                if p.get("pool_created_at") and (_now() - p["pool_created_at"]).total_seconds() < 3600 * 30][:limit]

    def pool_snapshot(self, chain: str, pool_address: str) -> PoolSnapshot | None:
        for p in DEMO_POOLS:
            if p["pool_address"] == pool_address and p["chain"] == chain:
                return PoolSnapshot(**p)
        return None

    def candles(self, symbol: str, interval: str = "1d", limit: int = 200) -> list[PriceBar]:
        return DEMO_BARS

    def ticker(self, symbol: str) -> dict[str, Any]:
        s = symbol.replace("/", "").upper()
        price = next((p["price_usd"] for p in DEMO_POOLS if p["symbol"] == s), 100.0)
        return {"price": price, "volume": 1e9, "change_24h_pct": 2.1, "source": "demo"}


class DemoChainActivity:
    name = "demo_chain"

    def recent_swaps(self, pool_address: str, min_usd: float = 0.0, limit: int = 100) -> list[SwapEvent]:
        return self.recent_swaps_for_chain("solana", pool_address, min_usd, limit)

    def recent_swaps_for_chain(self, chain: str, pool_address: str, min_usd: float = 0.0,
                               limit: int = 100) -> list[SwapEvent]:
        rows = [s for s in DEMO_SWAPS if s["pool_address"] == pool_address and s["chain"] == chain]
        evts = []
        for s in rows:
            if s["value_usd"] < min_usd:
                continue
            evts.append(SwapEvent(
                chain=chain, tx_hash=s["tx_hash"], wallet=s["wallet"], pool_address=s["pool_address"],
                token_address=s["token_address"], symbol="", side=s["side"], base_amount=0.0,
                quote_amount=0.0, price_usd=s["price_usd"], value_usd=s["value_usd"],
                block_time=s["block_time"], source_ts=dt.datetime.now(dt.timezone.utc),
            ))
        return evts[:limit]


class DemoTokenMetadata:
    name = "demo_token"

    def token_meta(self, chain: str, token_address: str, pool_address: str = "") -> TokenMeta:
        extra = {}
        if token_address == "DEMO_PUMP":
            extra = {"symbol": "PUMPD", "name": "Pump Demo"}
        elif token_address == "DEMO_MEME2":
            extra = {"symbol": "MEME2", "name": "Meme Two"}
        elif token_address == "DEMO_SOL":
            extra = {"symbol": "SOL", "name": "Solana"}
        return TokenMeta(chain=chain, address=token_address,
                         **{**extra, "holder_count": 420, "top10_holder_pct": 18.0,
                            "deployer_address": "deployer_demo", "deployer_holder_pct": 6.0,
                            "lp_locked": True, "mint_authority_revoked": True,
                            "freeze_authority_revoked": True, "transfer_tax_bps": 0.0,
                            "sell_simulation_ok": True})


class DemoSocial:
    name = "demo_social"

    def narrative_score(self, chain: str, token_address: str, symbol: str) -> float | None:
        return 0.6