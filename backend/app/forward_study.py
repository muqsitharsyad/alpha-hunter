"""Point-in-time paper research; missing prices are outcomes, never silently dropped."""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import Counter
from dataclasses import asdict
from typing import Any, Callable
from sqlalchemy import func

from .db import session_scope
from .models import ForwardObservation, Opportunity, Wallet

UTC = dt.timezone.utc
HORIZON = dt.timedelta(hours=24)
MAX_LATENESS = dt.timedelta(minutes=15)
RETRY_DELAY = dt.timedelta(minutes=2)
STUDY_VERSION = "forward-observed-price@1"


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def freeze(value):
    return json.loads(json.dumps(value, default=lambda x: x.isoformat() if isinstance(x, dt.datetime) else str(x)))


def record(candidate: dict[str, Any], event: dict[str, Any], tiers: dict, provider: str) -> int:
    """Record eligible cohort once, including rejected/nontraded opportunities."""
    pool = candidate["pool"]
    price = pool.price_usd
    if not math.isfinite(price) or price <= 0:
        return 0
    now = dt.datetime.now(UTC)
    count = 0
    with session_scope() as db:
        opportunity = db.get(Opportunity, event["opportunity_id"])
        if opportunity is None:
            return 0
        for swap in candidate["whale_swaps"]:
            tier = tiers.get((candidate["chain"], swap.wallet))
            if swap.side != "buy" or tier not in {"S", "A"}:
                continue
            wallet = db.query(Wallet).filter_by(chain=candidate["chain"], address=swap.wallet).first()
            if wallet is None:
                continue
            if db.query(ForwardObservation).filter_by(opportunity_id=opportunity.id, wallet_id=wallet.id).first():
                continue
            db.add(ForwardObservation(
                opportunity_id=opportunity.id, wallet_id=wallet.id, token_id=opportunity.token_id,
                detected_at=now, due_at=now + HORIZON, next_attempt_at=now + HORIZON,
                snapshot=freeze({"study_version": STUDY_VERSION, "tier_at_detection": tier,
                    "wallet_score": wallet.score, "wallet_reputation": wallet.reputation,
                    "observed_price": price, "whale_entry_price": swap.price_usd,
                    "price_kind": "observed_mark_not_executable_fill", "provider": provider,
                    "pool": asdict(pool), "swap": asdict(swap), "horizon_hours": 24,
                    "maximum_lateness_seconds": int(MAX_LATENESS.total_seconds()),
                    "source_timestamp_verified": False,
                    "timestamp_note": "adapter timestamp may be retrieval time; no historical/source-time guarantee",
                    "decision": opportunity.decision, "gates": opportunity.gate_results,
                    "features": opportunity.features, "strategy_version": opportunity.strategy_version,
                    "cost_model": "execution.depth-adverse@1", "performance_kind": "gross_mark_return_only"})))
            db.flush()
            count += 1
    return count


def resolve_due(fetch: Callable, *, limit: int = 2, now: dt.datetime | None = None) -> dict:
    """Fair retries and a frozen late-price cutoff; caller controls provider quota."""
    now = now or dt.datetime.now(UTC)
    resolved = missing = deferred = 0
    with session_scope() as db:
        rows = db.query(ForwardObservation).filter(
            ForwardObservation.status == "pending", ForwardObservation.next_attempt_at <= now
        ).order_by(ForwardObservation.next_attempt_at, ForwardObservation.id).limit(limit).all()
        for row in rows:
            due = aware(row.due_at)
            if now > due + MAX_LATENESS:
                row.status, row.resolved_at = "missing", now
                row.outcome = {"reason": "outcome_window_missed", "performance_eligible": False}
                missing += 1
                continue
            snap = row.snapshot
            pool_info = snap["pool"]
            try:
                quote = fetch(pool_info["chain"], pool_info["pool_address"])
                stamp = aware(quote.source_ts) if quote and quote.source_ts else None
                fresh = stamp is not None and 0 <= (now - stamp).total_seconds() <= 300
                if not quote or not fresh or not math.isfinite(quote.price_usd) or quote.price_usd <= 0:
                    raise ValueError("missing_or_stale_price")
            except Exception:
                row.next_attempt_at = now + RETRY_DELAY
                deferred += 1
                continue
            row.status, row.resolved_at = "resolved", now
            row.outcome = {"observed_price": quote.price_usd, "observed_at": now.isoformat(),
                           "quote_timestamp": stamp.isoformat(), "performance_kind": "gross_mark_return_only",
                           "gross_return_pct": (quote.price_usd / snap["observed_price"] - 1) * 100,
                           "lateness_seconds": (now - due).total_seconds(),
                           "source_timestamp_verified": False}
            resolved += 1
    return {"resolved": resolved, "missing": missing, "deferred": deferred}


def summary() -> dict:
    with session_scope() as db:
        counts = Counter(dict(db.query(ForwardObservation.status, func.count(ForwardObservation.id))
                              .group_by(ForwardObservation.status).all()))
    total = sum(counts.values())
    return {"study_version": STUDY_VERSION, "observations": total,
            "pending": counts["pending"], "resolved": counts["resolved"], "missing": counts["missing"],
            "coverage": counts["resolved"] / total if total else None,
            "decision": "INSUFFICIENT EVIDENCE",
            "limitations": ["observed gross mark returns are not executable net fills",
                            "source timestamps not independently verified",
                            "no strategy-edge or statistical promotion acceptance"]}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=2))
