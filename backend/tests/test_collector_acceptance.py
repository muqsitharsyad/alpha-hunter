"""Offline acceptance for paused collection, provenance and fair quota scheduling."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections import deque
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.db import session_scope
from app.execution import EmergencyLayer
from app.models import Market, MarketSnapshot, PaperOrder, Token
from app.pipeline import AlphaHunterPipeline, _aware
from app.providers.base import PoolSnapshot, ProviderError, SwapEvent
from app.providers.free import _HTTP


def pool(address: str = "P", chain: str = "solana") -> PoolSnapshot:
    """Create explicit offline observations, not provider responses."""
    return PoolSnapshot(
        chain=chain,
        pool_address=address,
        token_address=f"T-{address}",
        symbol="TEST",
        name="Acceptance fixture",
        price_usd=1,
        liquidity_usd=100000,
        fdv_usd=1e6,
        volume_24h_usd=10000,
        buys_24h=10,
        sells_24h=10,
        buyers_24h=10,
        sellers_24h=10,
        price_change_h1=0,
        price_change_h24=0,
        pool_created_at=None,
        source_ts=dt.datetime.now(dt.timezone.utc),
    )


@pytest.fixture
def collector(monkeypatch: pytest.MonkeyPatch) -> AlphaHunterPipeline:
    pipeline = AlphaHunterPipeline()
    pipeline.s = pipeline.s.model_copy(update={"collector_enabled": True})
    state = {"status": "paused", "account_id": None, "emergency": {"active": False}}
    monkeypatch.setattr(EmergencyLayer, "get_state", lambda _: state)
    monkeypatch.setattr(
        pipeline,
        "current_regime",
        lambda: ("sideways", {}, dt.datetime.now(dt.timezone.utc)),
    )
    monkeypatch.setattr(_HTTP, "_requests", {})
    monkeypatch.setattr(_HTTP, "_cooldown", {})
    monkeypatch.setattr(
        pipeline.scanner,
        "candidates_from_trending",
        lambda pools, chain: [{"pool": p, "kind": "listed"} for p in pools],
    )
    return pipeline


def fake_market(
    pipeline: AlphaHunterPipeline, pools: list[PoolSnapshot]
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def discover(chain: str, limit: int, kind: str) -> list[PoolSnapshot]:
        calls.append((chain, kind))
        return [p for p in pools if p.chain == chain][:limit]

    pipeline.adapters = SimpleNamespace(
        market=SimpleNamespace(
            name="geckoterminal",
            trending_pools=lambda c, n: discover(c, n, "trending"),
            new_pools=lambda c, n: discover(c, n, "new"),
        ),
        chain=SimpleNamespace(recent_swaps_for_chain=lambda *a, **kw: []),
    )
    return calls


def result() -> dict:
    return {"snapshots_persisted": 0, "deferred": 0, "errors": []}


def test_collector_opt_in_is_conservative() -> None:
    settings = Settings(_env_file=None, collector_enabled=False)
    assert not settings.collector_enabled
    assert settings.collector_gt_calls_per_min == 8
    assert settings.collector_monitor_reserve == 2
    with pytest.raises(ValueError):
        Settings(_env_file=None, collector_gt_calls_per_min=9)


def test_paused_worker_opt_in_collects_after_monitor_without_resuming(
    collector: AlphaHunterPipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import _worker_cycle

    calls = []
    monkeypatch.setattr(
        EmergencyLayer,
        "get_state",
        lambda _: {"status": "paused", "account_id": 1, "emergency": {"active": False}},
    )
    monkeypatch.setattr(collector, "refresh_positions", lambda: calls.append("monitor"))
    monkeypatch.setattr(collector, "scan_cycle", lambda: calls.append("collect"))
    asyncio.run(_worker_cycle(collector))
    assert calls == ["monitor", "collect"]
    assert EmergencyLayer().get_state()["status"] == "paused"
    collector.s.collector_enabled = False
    calls.clear()
    asyncio.run(_worker_cycle(collector))
    assert calls == ["monitor"]


def test_paused_collection_persists_without_swaps_or_orders(
    collector: AlphaHunterPipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = pool(uuid4().hex)
    fake_market(collector, [observation])
    monkeypatch.setattr(
        collector, "_evaluate", lambda *a: pytest.fail("paused evaluation")
    )
    monkeypatch.setattr(
        collector.exec, "open_position", lambda **kw: pytest.fail("paused entry")
    )
    with session_scope() as db:
        before = db.query(PaperOrder).count()
    actual = collector.scan_cycle()
    assert actual["collector_only"] is True
    assert actual["snapshots_persisted"] == 1
    assert actual["buys"] == 0
    assert actual["errors"] == []
    with session_scope() as db:
        assert db.query(PaperOrder).count() == before
        market = db.query(Market).filter_by(symbol=observation.pool_address).one()
        snapshot = db.query(MarketSnapshot).filter_by(market_id=market.id).one()
        assert _aware(snapshot.source_ts) == observation.source_ts
        assert _aware(snapshot.ingested_ts) >= observation.source_ts
        token = db.query(Token).filter_by(address=observation.token_address).one()
        assert token.metadata_json["market_provenance"]["provider"] == "geckoterminal"


def test_paused_collection_can_ingest_whales_but_never_evaluate(
    collector: AlphaHunterPipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = pool(uuid4().hex)
    fake_market(collector, [observation])
    event = SwapEvent(
        chain="solana",
        tx_hash="fixture-tx",
        wallet="fixture-wallet",
        pool_address=observation.pool_address,
        token_address=observation.token_address,
        symbol="TEST",
        side="buy",
        base_amount=2000,
        quote_amount=2000,
        price_usd=1,
        value_usd=2000,
        block_time=observation.source_ts,
    )
    collector.adapters.chain.recent_swaps_for_chain = lambda *a, **kw: [event]
    recorded = []
    monkeypatch.setattr(
        collector.wallets, "record_swaps", lambda *a: recorded.append(a)
    )
    monkeypatch.setattr(collector.wallets, "compute_for", lambda *a: {"tier": "C"})
    monkeypatch.setattr(
        collector, "_evaluate", lambda *a: pytest.fail("paused evaluation")
    )
    actual = collector.scan_cycle()
    assert actual["wallet_events_ingested"] == 1
    assert len(recorded) == 1
    assert actual["opportunities_evaluated"] == actual["buys"] == 0


def test_cached_observation_does_not_gain_a_new_source_timestamp(
    collector: AlphaHunterPipeline,
) -> None:
    observation = pool(uuid4().hex)
    fake_market(collector, [observation])
    assert collector._persist_pool(observation)
    assert not collector._persist_pool(observation)
    assert not collector._persist_pool(replace(observation, source_ts=None))
    with session_scope() as db:
        market = db.query(Market).filter_by(symbol=observation.pool_address).one()
        snapshots = db.query(MarketSnapshot).filter_by(market_id=market.id).all()
        assert len(snapshots) == 1
        assert _aware(snapshots[0].source_ts) == observation.source_ts


def test_scheduler_bounds_calls_reserves_monitoring_and_defers(
    collector: AlphaHunterPipeline,
) -> None:
    fake_market(collector, [pool(str(i)) for i in range(12)])
    actual = result()
    selected = collector._scheduled_candidates(["solana"], 0, actual)
    assert len(selected) == 2  # discovery + two swap calls, each including one retry
    assert len(collector._scan_requests) == 6
    assert actual["deferred"] == 10
    assert actual["errors"] == []
    assert not collector._reserve_scan_request()
    collector._scan_requests.clear()
    actual = result()
    assert collector._scheduled_candidates(["solana"], 4, actual) == []
    assert actual["deferred"] > 0


def test_round_robin_refresh_does_not_starve_lower_ranked_pools(
    collector: AlphaHunterPipeline,
) -> None:
    fake_market(collector, [pool(str(i)) for i in range(12)])
    collector.s.trending_scan_interval_s = 0
    seen = []
    for _ in range(6):
        collector._scan_requests.clear()  # next quota window, no sleeping/network
        selected = collector._scheduled_candidates(["solana"], 0, result())
        seen.extend(c["pool"].pool_address for c in selected)
    assert seen == [str(i) for i in range(12)]


def test_discovery_rotates_across_chains_and_feeds(
    collector: AlphaHunterPipeline,
) -> None:
    calls = fake_market(collector, [])
    for _ in range(4):
        collector._scan_requests.clear()
        collector._scheduled_candidates(["solana", "base"], 0, result())
    assert calls == [
        ("solana", "trending"),
        ("solana", "new"),
        ("base", "trending"),
        ("base", "new"),
    ]


def test_shared_http_usage_and_cooldown_defer_without_calls(
    collector: AlphaHunterPipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    calls = fake_market(collector, [])
    monkeypatch.setattr(
        _HTTP, "_requests", {"geckoterminal": deque([time.monotonic()] * 5)}
    )
    assert collector._scheduled_candidates(["solana"], 0, result()) == []
    assert calls == []
    monkeypatch.setattr(_HTTP, "_requests", {})
    monkeypatch.setattr(_HTTP, "_cooldown", {"geckoterminal": time.monotonic() + 60})
    assert collector._scheduled_candidates(["solana"], 0, result()) == []
    assert calls == []


def test_quota_exception_is_deferred_not_scan_error(
    collector: AlphaHunterPipeline,
) -> None:
    fake_market(collector, [])

    def exhausted(*args: object) -> list:
        raise ProviderError("geckoterminal: free request budget exhausted")

    collector.adapters.market.trending_pools = exhausted
    actual = result()
    assert collector._scheduled_candidates(["solana"], 0, actual) == []
    assert actual["deferred"] == 1
    assert actual["errors"] == []
    assert not collector._quota_deferred(ProviderError("network offline"))


def test_discovery_budget_deferral_does_not_advance_feed_cursor(
    collector: AlphaHunterPipeline,
) -> None:
    calls = fake_market(collector, [])
    collector._scheduled_candidates(["solana", "base"], 0, result())
    assert calls == [("solana", "trending")]
    cursor = collector._discovery_cursor
    collector._scan_requests.extend([__import__("time").monotonic()] * 6)
    for _ in range(3):
        collector._scheduled_candidates(["solana", "base"], 0, result())
    assert collector._discovery_cursor == cursor
    collector._scan_requests.clear()
    collector._scheduled_candidates(["solana", "base"], 0, result())
    assert calls[-1] == ("solana", "new")


@pytest.mark.parametrize(
    "entry_depth,depth,expected",
    [(100000, 30000, True), (100000, 31000, False), (0, 1000, False)],
)
def test_fresh_monitor_passes_only_proven_liquidity_collapse(
    collector: AlphaHunterPipeline,
    monkeypatch: pytest.MonkeyPatch,
    entry_depth: float,
    depth: float,
    expected: bool,
) -> None:
    from app.models import PaperAccount, PaperPosition, User

    with session_scope() as db:
        user = User(
            email=f"collector-{uuid4().hex}@example.test", password_hash="disabled"
        )
        db.add(user)
        db.flush()
        account = PaperAccount(
            user_id=user.id,
            starting_equity=1000,
            equity=1000,
            peak_equity=1000,
            day_start_equity=1000,
        )
        token = Token(chain="solana", address=uuid4().hex)
        db.add_all([account, token])
        db.flush()
        position = PaperPosition(
            account_id=account.id,
            token_id=token.id,
            chain="solana",
            symbol="TEST",
            qty=10,
            qty_open=10,
            avg_entry=1,
            notional_usd=10,
            risk_usd=1,
            stop_loss=0.9,
            trailing_state={"entry_context": {"pool_depth": entry_depth}},
        )
        db.add(position)
        account_id = account.id
    monkeypatch.setattr(
        EmergencyLayer,
        "get_state",
        lambda _: {
            "status": "paused",
            "account_id": account_id,
            "emergency": {"active": False},
        },
    )
    quote = replace(pool(), liquidity_usd=depth)
    monkeypatch.setattr(collector, "executable_quote", lambda p: quote)
    monkeypatch.setattr(collector, "_resolve_pending_signals", lambda *a: None)
    calls = []

    def check(*args: object, **kwargs: object) -> dict:
        calls.append(kwargs)
        return {"action": "hold"}

    monkeypatch.setattr(collector.pos_mgr, "check_exits", check)
    collector.refresh_positions()
    assert calls == [{"liquidity_collapse": True} if expected else {}]


def test_position_quotes_use_immutable_entry_pool_not_latest_token_pool(
    collector: AlphaHunterPipeline,
) -> None:
    position = SimpleNamespace(
        trailing_state={"entry_context": {"features": {"pool": "ENTRY-POOL"}}}
    )
    assert collector._pool_addr(position) == "ENTRY-POOL"


def test_expired_local_reservations_allow_new_window(
    collector: AlphaHunterPipeline,
) -> None:
    import time

    fake_market(collector, [])
    collector._scan_requests.extend([time.monotonic() - 61] * 8)
    assert collector._reserve_scan_request()
    assert len(collector._scan_requests) == 2


def test_stale_pool_keeps_queue_order_but_cannot_be_selected(
    collector: AlphaHunterPipeline,
) -> None:
    observation = pool(uuid4().hex)
    observation.source_ts -= dt.timedelta(days=1)
    fake_market(collector, [observation])
    assert collector._scheduled_candidates(["solana"], 0, result()) == []
    assert ("solana", observation.pool_address) in collector._pool_queue
