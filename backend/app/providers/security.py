"""Keyless GoPlus Solana evidence, never a complete safety audit.

Contract verified against the official generated SDK and a read-only USDC probe:
https://github.com/GoPlusSecurity/goplus-sdk-python/blob/main/swagger_client/api/token_security_api_for_solana__beta_api.py
https://github.com/GoPlusSecurity/goplus-sdk-python/blob/main/swagger_client/models/response_wrapper_solana_token_security_result.py
Related *_mintable, *_holders and *_transfer_fee_current_fee_rate models document
status strings, top-ten holders and fee ratios. holder_count/account/total_supply
also occur in the live response (the generated SDK lags those additive fields).

The endpoint has NO upstream observation timestamp or sell simulation. Retrieval
TTL bounds our cached observation, not the age of the vendor's underlying scan.
LP holders/burn percentages do not prove a requested pool's lock; metadata creators
are not proven deployers. Those gates deliberately remain UNKNOWN. RugCheck's
public reports were also probed: detectedAt was months old and holder/market
collections were null, so they are not used to manufacture fresh audit evidence.
"""

from __future__ import annotations

import copy
import datetime as dt
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, TypeGuard

from .base import ProviderError, TokenMeta, TokenMetadataProvider
from .free import _HTTP, DexScreenerTokenMeta

_ENDPOINT = "https://api.gopluslabs.io/api/v1/solana/token_security"
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_FIELDS = (
    "holder_count",
    "top10_holder_pct",
    "deployer_holder_pct",
    "lp_locked",
    "mint_authority_revoked",
    "freeze_authority_revoked",
    "transfer_tax_bps",
    "sell_simulation_ok",
)
_GAPS = {
    "deployer_holder_pct": "Metadata creators are not verified deployers; absence from top ten is not zero ownership",
    "lp_locked": "No verified pool-scoped lock amount, expiry and coverage evidence",
    "sell_simulation_ok": "Token security GET supplies no route/amount-specific sell simulation",
    "transfer_tax_bps": "Missing explicit current transfer fee; empty transfer_fee is not zero tax",
    "holder_count": "Missing or invalid positive holder_count",
    "top10_holder_pct": "Missing or incomplete distinct top-ten holder ratios",
    "mint_authority_revoked": "Missing, invalid or contradictory mintable status/authority",
    "freeze_authority_revoked": "Missing, invalid or contradictory freezable status/authority",
}


def _mint(value: object) -> TypeGuard[str]:
    """Validate an exact, canonical 32-byte base58 public key without dependencies."""
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return False
    number = 0
    for char in value:
        if char not in _BASE58:
            return False
        number = number * 58 + _BASE58.index(char)
    leading = len(value) - len(value.lstrip("1"))
    return leading + (number.bit_length() + 7) // 8 == 32


def _ratio(value: Any) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() and 0 <= result <= 1 else None


def _revoked(value: Any) -> bool | None:
    if not isinstance(value, dict):
        return None
    status, authorities = value.get("status"), value.get("authority")
    if status == "0" and authorities == []:
        return True
    if (
        status == "1"
        and isinstance(authorities, list)
        and authorities
        and all(isinstance(a, dict) and _mint(a.get("address")) for a in authorities)
    ):
        return False
    return None


def _concentration(holders: Any, count: int | None) -> float | None:
    if count is None or not isinstance(holders, list) or len(holders) != min(10, count):
        return None
    identities: set[str] = set()
    total = Decimal(0)
    for holder in holders:
        if not isinstance(holder, dict):
            return None
        # New API includes owner account; older SDK describes a single token_account.
        identity = holder.get("account", holder.get("token_account"))
        ratio = _ratio(holder.get("percent"))
        if not _mint(identity) or identity in identities or ratio is None:
            return None
        identities.add(identity)
        total += ratio
    return float(total * 100) if total <= 1 else None


class GoPlusTokenSecurity(TokenMetadataProvider):
    """Compose public market identity with explicitly partial Solana security data.

    Non-Solana chains retain the metadata-only adapter. HTTP/rate-limit/schema
    failures erase evidence for this call, never serve stale security as safe.
    """

    name = "goplus"

    def __init__(
        self,
        *,
        metadata_provider: TokenMetadataProvider | None = None,
        ttl_s: float = 120.0,
    ) -> None:
        if isinstance(ttl_s, bool) or not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("security TTL must be finite and positive")
        self.market = (
            metadata_provider
            if metadata_provider is not None
            else DexScreenerTokenMeta()
        )
        self.client = _HTTP
        self.ttl_s = ttl_s

    def _fetch(self, token_address: str) -> TokenMeta:
        payload = self.client.get_json(
            self.name, _ENDPOINT, params={"contract_addresses": token_address}
        )
        if (
            not isinstance(payload, dict)
            or type(payload.get("code")) is not int
            or payload["code"] != 1
        ):
            raise ProviderError("goplus: invalid success envelope")
        results = payload.get("result")
        row = results.get(token_address) if isinstance(results, dict) else None
        if not isinstance(row, dict) or not row:
            raise ProviderError("goplus: missing exact case-sensitive token result")
        identity = row.get("metadata")
        identity = identity if isinstance(identity, dict) else {}
        symbol, name = identity.get("symbol"), identity.get("name")
        meta = TokenMeta(
            chain="solana",
            address=token_address,
            symbol=symbol if isinstance(symbol, str) else "",
            name=name if isinstance(name, str) else "",
        )
        count = row.get("holder_count")
        if (
            isinstance(count, str)
            and re.fullmatch(r"[0-9]{1,20}", count)
            and int(count) > 0
        ):
            meta.holder_count = int(count)
        meta.top10_holder_pct = _concentration(row.get("holders"), meta.holder_count)
        meta.mint_authority_revoked = _revoked(row.get("mintable"))
        meta.freeze_authority_revoked = _revoked(row.get("freezable"))
        fee = row.get("transfer_fee")
        current = fee.get("current_fee_rate") if isinstance(fee, dict) else None
        ratio = _ratio(current.get("fee_rate")) if isinstance(current, dict) else None
        if ratio is not None:
            meta.transfer_tax_bps = float(ratio * 10000)
        observed = dt.datetime.now(dt.UTC)
        paths = {
            "holder_count": "holder_count",
            "top10_holder_pct": "holders[*].percent",
            "mint_authority_revoked": "mintable",
            "freeze_authority_revoked": "freezable",
            "transfer_tax_bps": "transfer_fee.current_fee_rate.fee_rate",
        }
        meta.metadata = {
            "source": self.name,
            "safety_status": "partial",
            "safety_source": self.name,
            "safety_observed_at": observed.isoformat(),
            "safety_expires_at": (
                observed + dt.timedelta(seconds=self.ttl_s)
            ).isoformat(),
            "safety_ttl_s": self.ttl_s,
            "upstream_source_ts": None,
            "freshness_basis": "retrieval_only; upstream scan age unavailable",
            "security_endpoint": _ENDPOINT,
            "security_chain": "solana",
            "security_token_address": token_address,
            "evidence": {
                key: {
                    "source": self.name,
                    "path": path,
                    "value": getattr(meta, key),
                    "observed_at": observed.isoformat(),
                }
                for key, path in paths.items()
                if getattr(meta, key) is not None
            },
            "unknown_reasons": {
                key: _GAPS[key] for key in _FIELDS if getattr(meta, key) is None
            },
        }
        return meta

    def token_meta(
        self, chain: str, token_address: str, pool_address: str = ""
    ) -> TokenMeta:
        if chain != "solana":
            return self.market.token_meta(chain, token_address, pool_address)
        if not _mint(token_address) or (pool_address and not _mint(pool_address)):
            raise ProviderError("invalid Solana token/pool public key")
        from ..cache import cached

        try:
            # Cache evidence objects, not raw JSON, so hits cannot rejuvenate timestamps.
            evidence, _, _ = cached(
                f"goplus:security:{token_address}:{self.ttl_s}",
                self.ttl_s,
                lambda: self._fetch(token_address),
            )
            meta: TokenMeta = copy.deepcopy(evidence)
            now = dt.datetime.now(dt.UTC)
            observed = dt.datetime.fromisoformat(meta.metadata["safety_observed_at"])
            if not 0 <= (now - observed).total_seconds() <= self.ttl_s:
                raise ProviderError("goplus: stale or future security observation")
        except ProviderError:
            meta = TokenMeta(
                chain=chain,
                address=token_address,
                symbol="",
                name="",
                metadata={
                    "source": self.name,
                    "safety_source": self.name,
                    "safety_status": "unavailable",
                    "safety_reason": "Security request unavailable, stale or invalid; no stale fallback",
                    "unknown_reasons": dict(_GAPS),
                    "evidence": {},
                },
            )
        try:
            market = self.market.token_meta(chain, token_address, pool_address)
        except ProviderError:
            market = None
        if market is not None:
            if market.chain != chain or market.address != token_address:
                raise ProviderError("market metadata identity mismatch")
            meta.symbol, meta.name = market.symbol, market.name
            meta.metadata["market_metadata"] = market.metadata
        # No market, trust flag, empty risk list or burn percentage can fill unknown gates.
        meta.metadata["security_pool_address"] = pool_address
        return meta
