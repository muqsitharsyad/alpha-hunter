"""Provider adapters. Business logic never talks to a vendor directly (PRD 7.1):

* ``ChainActivityProvider`` - on-chain swaps/transfers (who traded what, how big)
* ``MarketDataProvider``   - prices/candles/liquidity for pools and listed markets
* ``TokenMetadataProvider``- token/pool metadata + holder distribution
* ``SocialDataProvider``   - narrative/sentiment (optional, off by default)
* ``NewsProvider``         - news headlines (optional, off by default)

v1 ships free implementations (GeckoTerminal + DexScreener + Binance/Bybit/OKX public
endpoints). Paid vendors are drop-in replacements: same interface, one registry line.
"""
from .base import (  # noqa: F401
    AdapterRegistry,
    ChainActivityProvider,
    MarketDataProvider,
    NewsProvider,
    ProviderError,
    SocialDataProvider,
    TokenMetadataProvider,
    HEALTH,
    get_adapters,
    record_call,
)
