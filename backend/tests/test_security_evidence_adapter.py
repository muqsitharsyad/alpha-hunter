"""Wire fixtures projected from a keyless GoPlus USDC GET on 2026-10-15.

Endpoint and model documentation: official GoPlusSecurity/goplus-sdk-python,
swagger_client/api/token_security_api_for_solana__beta_api.py and
swagger_client/models/response_wrapper_solana_token_security_*.py.
Tests modifying fields below are deliberately malformed/edge-case variants,
not claims that those variants were observed live.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import httpx
import pytest

from app.cache import CACHE
from app.providers.base import (
    AdapterRegistry,
    ProviderError,
    TokenMeta,
    TokenMetadataProvider,
)
from app.providers.free import _Http
from app.providers.security import GoPlusTokenSecurity, _mint
from app.token_intel import TokenSafety

MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
AUTH = "BJE5MMbqXjVwjAF7oxwPYXnTXDyspzZyt4vwenNw5ruG"
FREEZE = "7dGbd2QZcCKcTndnHcTL8q7SMVXAkp688NTQYwrRCrar"
POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"
LIVE_HOLDERS = [
    ("7VHUFJHWu2CuExkJcJrzhQPJ2oygupTWkL2A2For4BmE", "0.1214"),
    ("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9", "0.1071"),
    ("6LY1JzAFVZsP2a2xKrtU6znQMQ5h4i7tocWdgrkZzkzF", "0.0163"),
    ("DBD8hAwLDRQkTsu6EqviaYNGKPnsAMmQonxf7AH8ZcFY", "0.0159"),
    ("AVzP2GeRmqGphJsMxWoqjpUifPpCret7LqWhD8NWQK49", "0.0141"),
    ("8Vkgsarud8mSc1gkzayGA7XhNQfq4wDJRxTWDgq8RaJD", "0.0129"),
    ("3csuXZKah5rgpb8RiwX9XfjrMxcp3u1K9mBdCwL51spj", "0.0099"),
    ("F6ZjiBm1WgVXzez5vxHeBDgaVPQRfLyFb7GFwXvyZVxD", "0.0090"),
    ("2GAPBYrsxR8pUaDaWGKkAJUf6DcBZrxMjpZ423kLDia1", "0.0065"),
    ("E1bQJ8eMMn3zmeSewW3HQ8zmJr7KR75JonbwAtWx2bux", "0.0065"),
]


class Market(TokenMetadataProvider):
    def token_meta(
        self, chain: str, token_address: str, pool_address: str = ""
    ) -> TokenMeta:
        return TokenMeta(
            chain,
            token_address,
            "USDC",
            "USD Coin",
            metadata={"source": "dexscreener", "safety_status": "unavailable"},
        )


@pytest.fixture(autouse=True)
def clean_cache() -> Any:
    CACHE.invalidate("goplus:security:")
    yield
    CACHE.invalidate("goplus:security:")


@pytest.fixture
def payload() -> dict[str, Any]:
    return {
        "code": 1,
        "message": "ok",
        "result": {
            MINT: {
                "metadata": {"name": "USD Coin", "symbol": "USDC"},
                "holder_count": "9095715",
                "total_supply": "7757717865.306653",
                "holders": [
                    {"account": account, "percent": ratio}
                    for account, ratio in LIVE_HOLDERS
                ],
                "mintable": {
                    "authority": [{"address": AUTH, "malicious_address": 0}],
                    "status": "1",
                },
                "freezable": {
                    "authority": [{"address": FREEZE, "malicious_address": 0}],
                    "status": "1",
                },
                "creators": [],
                "lp_holders": [],
                "transfer_fee": {},
                "trusted_token": 1,
                "dex": [{"id": POOL, "burn_percent": 0, "type": "Standard"}],
            }
        },
    }


def adapter(
    payload: Any, *, status: int = 200
) -> tuple[GoPlusTokenSecurity, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=payload)

    http = _Http()
    http._client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = GoPlusTokenSecurity(metadata_provider=Market())
    provider.client = http
    return provider, requests


def test_verified_wire_contract_and_live_projection(payload: dict[str, Any]) -> None:
    provider, calls = adapter(payload)
    meta = provider.token_meta("solana", MINT, POOL)
    request = calls[0]
    assert request.method == "GET"
    assert (
        str(request.url).split("?")[0]
        == "https://api.gopluslabs.io/api/v1/solana/token_security"
    )
    assert dict(request.url.params) == {"contract_addresses": MINT}
    assert "authorization" not in request.headers
    assert meta.holder_count == 9095715
    assert meta.top10_holder_pct == pytest.approx(31.96)
    assert meta.mint_authority_revoked is False
    assert meta.freeze_authority_revoked is False
    assert meta.sell_simulation_ok is meta.lp_locked is meta.deployer_holder_pct is None
    assert meta.transfer_tax_bps is None
    assert meta.metadata["safety_status"] == "partial"
    assert meta.metadata["upstream_source_ts"] is None
    assert "retrieval_only" in meta.metadata["freshness_basis"]
    assert meta.metadata["market_metadata"]["source"] == "dexscreener"
    assert meta.metadata["evidence"]["top10_holder_pct"]["path"] == "holders[*].percent"
    safety = TokenSafety().check(meta, None)
    assert safety["summary"] == "REJECT"  # real USDC has active mint/freeze authorities
    assert {"sell_simulation_ok", "lp_locked", "deployer_holder_pct"} <= set(
        safety["unknown"]
    )


def test_positive_authority_evidence_is_still_unknown_overall(
    payload: dict[str, Any],
) -> None:
    row = payload["result"][MINT]
    for field in ("mintable", "freezable"):
        row[field] = {"status": "0", "authority": []}
    row["transfer_fee"] = {"current_fee_rate": {"fee_rate": "0", "maximum_fee": "0"}}
    row["dex"][0]["burn_percent"] = 100
    provider, _ = adapter(payload)
    meta = provider.token_meta("solana", MINT, POOL)
    assert meta.mint_authority_revoked is True
    assert meta.freeze_authority_revoked is True
    assert meta.transfer_tax_bps == 0
    assert meta.lp_locked is None  # even full burn is not a verified scoped lock
    assert TokenSafety().check(meta, None)["summary"] == "UNKNOWN"


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"status": "0"},
        {"status": 0, "authority": []},
        {"status": "false", "authority": []},
        {"status": "0", "authority": [{"address": AUTH}]},
        {"status": "1", "authority": []},
        {"status": "1", "authority": [{"address": "bad"}]},
    ],
)
def test_missing_or_contradictory_authority_is_unknown(
    payload: dict[str, Any], value: Any
) -> None:
    payload["result"][MINT]["mintable"] = value
    provider, _ = adapter(payload)
    assert provider.token_meta("solana", MINT).mint_authority_revoked is None


@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", "-0.1", "1.1", 0, True])
def test_bad_holder_ratio_never_defaults_zero(
    payload: dict[str, Any], value: Any
) -> None:
    payload["result"][MINT]["holders"][0]["percent"] = value
    provider, _ = adapter(payload)
    assert provider.token_meta("solana", MINT).top10_holder_pct is None


@pytest.mark.parametrize(
    "variant", ["missing", "short", "duplicate", "nonobject", "over100", "noidentity"]
)
def test_incomplete_concentration_is_unknown(
    payload: dict[str, Any], variant: str
) -> None:
    row = payload["result"][MINT]
    if variant == "missing":
        row.pop("holders")
    elif variant == "short":
        row["holders"].pop()
    elif variant == "duplicate":
        row["holders"][1] = copy.deepcopy(row["holders"][0])
    elif variant == "nonobject":
        row["holders"][1] = None
    elif variant == "over100":
        row["holders"][0]["percent"] = "1"
    else:
        row["holders"][0].pop("account")
    provider, _ = adapter(payload)
    assert provider.token_meta("solana", MINT).top10_holder_pct is None


@pytest.mark.parametrize("value", [None, "0", "-1", "1.0", True, 10, "NaN", "9" * 30])
def test_unknown_count_is_not_inferred_from_top_ten(
    payload: dict[str, Any], value: Any
) -> None:
    payload["result"][MINT]["holder_count"] = value
    provider, _ = adapter(payload)
    meta = provider.token_meta("solana", MINT)
    assert meta.holder_count is meta.top10_holder_pct is None


def test_small_complete_holder_set_and_old_sdk_identity(
    payload: dict[str, Any],
) -> None:
    row = payload["result"][MINT]
    row["holder_count"] = "1"
    row["holders"] = [{"token_account": LIVE_HOLDERS[0][0], "percent": "1"}]
    provider, _ = adapter(payload)
    assert provider.token_meta("solana", MINT).top10_holder_pct == 100


@pytest.mark.parametrize(
    "value, expected",
    [("0.02", 200), ("0", 0), ("1", 10000), (None, None), ("NaN", None), (True, None)],
)
def test_only_explicit_current_fee_ratio_is_used(
    payload: dict[str, Any], value: Any, expected: float | None
) -> None:
    payload["result"][MINT]["transfer_fee"] = {"current_fee_rate": {"fee_rate": value}}
    provider, _ = adapter(payload)
    assert provider.token_meta("solana", MINT).transfer_tax_bps == expected


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {},
        {"code": True, "result": {}},
        {"code": 0},
        {"code": 1, "result": {MINT.lower(): {"trusted_token": 1}}},
        {"code": 1, "result": {MINT: {}}},
    ],
)
def test_bad_envelope_or_identity_is_unavailable(bad: Any) -> None:
    provider, _ = adapter(bad)
    meta = provider.token_meta("solana", MINT)
    assert meta.metadata["safety_status"] == "unavailable"
    assert meta.holder_count is meta.mint_authority_revoked is None
    assert meta.symbol == "USDC"
    assert TokenSafety().check(meta, None)["summary"] == "UNKNOWN"


@pytest.mark.parametrize("status", [401, 404, 429, 503])
def test_http_failure_stays_unknown(
    payload: dict[str, Any], status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.providers.free.time.sleep", lambda _: None)
    provider, calls = adapter(payload, status=status)
    meta = provider.token_meta("solana", MINT)
    assert meta.metadata["safety_status"] == "unavailable"
    assert meta.metadata["evidence"] == {}
    assert len(calls) <= 2


def test_cache_does_not_rejuvenate_or_allow_mutation(payload: dict[str, Any]) -> None:
    provider, calls = adapter(payload)
    first = provider.token_meta("solana", MINT)
    stamp = first.metadata["safety_observed_at"]
    first.metadata["evidence"].clear()
    first.mint_authority_revoked = True
    second = provider.token_meta("solana", MINT)
    assert len(calls) == 1
    assert second.metadata["safety_observed_at"] == stamp
    assert second.metadata["evidence"]
    assert second.mint_authority_revoked is False
    expires = dt.datetime.fromisoformat(second.metadata["safety_expires_at"])
    observed = dt.datetime.fromisoformat(stamp)
    assert (expires - observed).total_seconds() == 120


def test_stale_cache_refresh_failure_never_serves_old_evidence(
    payload: dict[str, Any],
) -> None:
    provider, calls = adapter(payload)
    provider.token_meta("solana", MINT)
    key = f"goplus:security:{MINT}:120.0"
    evidence, inserted = CACHE._d[key]
    CACHE._d[key] = (evidence, inserted - 121)
    payload["code"] = 0
    meta = provider.token_meta("solana", MINT)
    assert len(calls) == 2
    assert meta.mint_authority_revoked is None
    assert meta.metadata["safety_status"] == "unavailable"


@pytest.mark.parametrize("offset", [-121, 60])
def test_stale_or_future_observation_is_not_rejuvenated(
    payload: dict[str, Any], offset: int
) -> None:
    provider, _ = adapter(payload)
    provider.token_meta("solana", MINT)
    evidence, _ = CACHE._d[f"goplus:security:{MINT}:120.0"]
    evidence.metadata["safety_observed_at"] = (
        dt.datetime.now(dt.UTC) + dt.timedelta(seconds=offset)
    ).isoformat()
    meta = provider.token_meta("solana", MINT)
    assert meta.metadata["safety_status"] == "unavailable"
    assert meta.mint_authority_revoked is None


def test_security_does_not_depend_on_market_availability(
    payload: dict[str, Any],
) -> None:
    provider, _ = adapter(payload)

    class MissingMarket(Market):
        def token_meta(
            self, chain: str, token_address: str, pool_address: str = ""
        ) -> TokenMeta:
            raise ProviderError("market offline")

    provider.market = MissingMarket()
    meta = provider.token_meta("solana", MINT)
    assert meta.symbol == "USDC"
    assert meta.mint_authority_revoked is False


def test_wrong_market_identity_is_rejected(payload: dict[str, Any]) -> None:
    provider, _ = adapter(payload)

    class WrongMarket(Market):
        def token_meta(
            self, chain: str, token_address: str, pool_address: str = ""
        ) -> TokenMeta:
            return TokenMeta(chain, AUTH, "OTHER", "Other")

    provider.market = WrongMarket()
    with pytest.raises(ProviderError, match="identity"):
        provider.token_meta("solana", MINT)


def test_non_solana_is_metadata_only(payload: dict[str, Any]) -> None:
    provider, calls = adapter(payload)
    meta = provider.token_meta("base", "0x123")
    assert calls == []
    assert meta.metadata["source"] == "dexscreener"
    assert meta.mint_authority_revoked is None


@pytest.mark.parametrize(
    "value", ["", " invalid ", MINT + "x", "0" * 44, "1" * 31, "1" * 33]
)
def test_bad_address_rejected_before_network(
    payload: dict[str, Any], value: str
) -> None:
    provider, calls = adapter(payload)
    with pytest.raises(ProviderError):
        provider.token_meta("solana", value)
    with pytest.raises(ProviderError):
        provider.token_meta("solana", MINT, value or "bad")
    assert calls == []


@pytest.mark.parametrize("ttl", [0, -1, float("nan"), float("inf"), True])
def test_invalid_ttl_rejected(ttl: float) -> None:
    with pytest.raises(ValueError):
        GoPlusTokenSecurity(ttl_s=ttl)


def test_registry_uses_security_only_outside_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALPHAHUNTER_FORCE_DEMO", raising=False)
    assert isinstance(AdapterRegistry().load(demo=False).token, GoPlusTokenSecurity)
    assert AdapterRegistry().load(demo=True).token.name != "goplus"
    assert _mint(MINT)
