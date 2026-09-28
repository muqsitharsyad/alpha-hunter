"""Unverified public metadata never becomes a token safety approval."""
import datetime as dt
import httpx
import pytest
from app.providers.base import PoolSnapshot, TokenMeta
from app.providers.base import ProviderError
from app.token_intel import TokenSafety


def test_unknown_security_fields_do_not_pass():
    meta = TokenMeta(chain="solana", address="A", symbol="A", name="A")
    pool = PoolSnapshot(chain="solana", pool_address="P", token_address="A",
                        symbol="A", name="A", price_usd=1, liquidity_usd=100000,
                        fdv_usd=100000, volume_24h_usd=1000, buys_24h=3,
                        sells_24h=1, buyers_24h=3, sellers_24h=1,
                        price_change_h1=0, price_change_h24=0,
                        pool_created_at=dt.datetime.now(dt.timezone.utc))
    result = TokenSafety().check(meta, pool)
    assert result["summary"] != "PASS"


def test_provider_429_enters_cooldown_without_retry_storm(monkeypatch):
    from app.providers.free import _Http

    class FakeClient:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return httpx.Response(429, headers={"Retry-After": "1"})

    client = FakeClient()
    http = _Http()
    monkeypatch.setattr(http, "_c", lambda: client)
    with pytest.raises(ProviderError, match="HTTP 429"):
        http.get_json("test-provider", "https://example.test")
    with pytest.raises(ProviderError, match="cooldown active"):
        http.get_json("test-provider", "https://example.test")
    assert client.calls == 1


def test_provider_retries_transient_5xx_once(monkeypatch):
    from app.providers.free import _Http

    class FakeClient:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return httpx.Response(503, request=httpx.Request("GET", "https://example.test"))
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", "https://example.test"))

    client = FakeClient()
    http = _Http()
    monkeypatch.setattr(http, "_c", lambda: client)
    assert http.get_json("test-provider", "https://example.test") == {"ok": True}
    assert client.calls == 2
