"""Relational schema (PRD 23) + v1 additive tables for AI memory (PRD 21) and live-signal
quality checks (PRD 8.1 forward hit-rate)."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --- identity ---------------------------------------------------------------
class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active")


class UserSettings(Base, TimestampMixin):
    __tablename__ = "user_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    risk_profile: Mapped[str] = mapped_column(String(32), default="balanced")
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=5.0)
    max_positions: Mapped[int] = mapped_column(Integer, default=5)
    allowed_chains: Mapped[list[Any]] = mapped_column(JSON, default=lambda: ["solana"])
    acknowledged_disclaimer: Mapped[bool] = mapped_column(Boolean, default=False)


# --- smart money ------------------------------------------------------------
class Wallet(Base, TimestampMixin):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_wallet_chain_addr"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), index=True)
    address: Mapped[str] = mapped_column(String(128), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reputation: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[str] = mapped_column(String(4), default="C")
    archetype: Mapped[str] = mapped_column(String(32), default="unknown")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WalletScoreSnapshot(Base):
    __tablename__ = "wallet_score_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    components: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    total_score: Mapped[float] = mapped_column(Float)
    version: Mapped[str] = mapped_column(String(16), default="w1.0")
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WalletReputationHistory(Base):
    __tablename__ = "wallet_reputation_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    reputation: Mapped[float] = mapped_column(Float)
    drivers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    regime: Mapped[str] = mapped_column(String(24), default="unknown")
    version: Mapped[str] = mapped_column(String(16), default="r1.0")
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        UniqueConstraint("tx_hash", "wallet_id", "token_address", name="uq_wtx"),
        Index("ix_wtx_wallet_time", "wallet_id", "timestamp"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    chain: Mapped[str] = mapped_column(String(32))
    tx_hash: Mapped[str] = mapped_column(String(160), index=True)
    token_id: Mapped[int | None] = mapped_column(ForeignKey("tokens.id"), nullable=True)
    token_address: Mapped[str] = mapped_column(String(128), index=True)
    pool_address: Mapped[str] = mapped_column(String(128), default="")
    side: Mapped[str] = mapped_column(String(8))  # buy | sell
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    price_est: Mapped[float] = mapped_column(Float, default=0.0)
    value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    intent: Mapped[str] = mapped_column(String(32), default="unknown")
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)


class WalletCluster(Base):
    __tablename__ = "wallet_clusters"
    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), index=True)
    cluster_key: Mapped[str] = mapped_column(String(64), index=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    members: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- tokens / markets -------------------------------------------------------
class Token(Base, TimestampMixin):
    __tablename__ = "tokens"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_token_chain_addr"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(32), index=True)
    address: Mapped[str] = mapped_column(String(128), index=True)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(128), default="")
    age_hours: Mapped[float] = mapped_column(Float, default=0.0)
    listed: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TokenScore(Base):
    __tablename__ = "token_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), index=True)
    liquidity_score: Mapped[float] = mapped_column(Float, default=0.0)
    health_score: Mapped[float] = mapped_column(Float, default=0.0)
    security_score: Mapped[float] = mapped_column(Float, default=0.0)
    whale_score: Mapped[float] = mapped_column(Float, default=0.0)
    components: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManipulationFlag(Base):
    __tablename__ = "manipulation_flags"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), index=True)
    flag_type: Mapped[str] = mapped_column(String(40), index=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(16), default="warn")  # info|warn|reject
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("venue", "symbol", name="uq_market"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    venue: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(40))
    base_asset: Mapped[str] = mapped_column(String(24))
    quote_asset: Mapped[str] = mapped_column(String(24), default="USDT")
    market_type: Mapped[str] = mapped_column(String(16), default="spot")
    chain: Mapped[str] = mapped_column(String(32), default="")


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    volume_24h: Mapped[float] = mapped_column(Float, default=0.0)
    spread_bps: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity_usd: Mapped[float] = mapped_column(Float, default=0.0)
    source_ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ingested_ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketRegimeRow(Base):
    __tablename__ = "market_regimes"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    regime: Mapped[str] = mapped_column(String(24), index=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[str] = mapped_column(String(16), default="g1.0")


# --- opportunities ----------------------------------------------------------
class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), index=True)
    chain: Mapped[str] = mapped_column(String(32), default="")
    symbol: Mapped[str] = mapped_column(String(64), default="")
    scanner: Mapped[str] = mapped_column(String(24), default="listed")  # new_token|listed
    score: Mapped[float] = mapped_column(Float, default=0.0)
    decision: Mapped[str] = mapped_column(String(12), default="IGNORE", index=True)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    components: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    gate_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    feasibility: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    regime: Mapped[str] = mapped_column(String(24), default="unknown")
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(24), default="whale_copy@1.0.0")
    feature_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CopyEvaluation(Base):
    __tablename__ = "copy_evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    whale_wallet_id: Mapped[int | None] = mapped_column(ForeignKey("wallets.id"), nullable=True)
    feasible: Mapped[bool] = mapped_column(Boolean, default=False)
    expected_slippage_bps: Mapped[float] = mapped_column(Float, default=0.0)
    signal_age_s: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- strategies / learning --------------------------------------------------
class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_strategy_ver"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(24))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="champion")  # champion|challenger|shadow|rejected
    rationale: Mapped[str] = mapped_column(Text, default="")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StrategyRun(Base):
    __tablename__ = "strategy_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    regime: Mapped[str] = mapped_column(String(24), default="unknown")
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    expectation: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe: Mapped[float] = mapped_column(Float, default=0.0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TournamentRun(Base):
    __tablename__ = "tournament_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    window_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    window_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    entrants: Mapped[list[Any]] = mapped_column(JSON, default=list)
    ranking: Mapped[list[Any]] = mapped_column(JSON, default=list)
    winner_strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LearningEvent(Base):
    __tablename__ = "learning_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(64), default="")
    strategy_version: Mapped[str] = mapped_column(String(24), default="")
    regime: Mapped[str] = mapped_column(String(24), default="unknown")
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    outcome_label: Mapped[str] = mapped_column(String(24), default="")
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("model_type", "version", name="uq_model_ver"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    model_type: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(24))
    artifacts_ref: Mapped[str] = mapped_column(String(255), default="")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    gates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="candidate")
    promoted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- paper trading ----------------------------------------------------------
class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    starting_equity: Mapped[float] = mapped_column(Float, default=10000.0)
    equity: Mapped[float] = mapped_column(Float, default=10000.0)
    peak_equity: Mapped[float] = mapped_column(Float, default=10000.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    day_start_equity: Mapped[float] = mapped_column(Float, default=10000.0)
    day_key: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16), default="market")
    qty: Mapped[float] = mapped_column(Float)
    intended_price: Mapped[float] = mapped_column(Float)
    fill_price: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_bps: Mapped[float] = mapped_column(Float, default=0.0)
    partial_fill: Mapped[bool] = mapped_column(Boolean, default=False)
    mev_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="filled")
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"))
    chain: Mapped[str] = mapped_column(String(32), default="")
    symbol: Mapped[str] = mapped_column(String(64), default="")
    qty: Mapped[float] = mapped_column(Float)
    qty_open: Mapped[float] = mapped_column(Float, default=0.0)
    avg_entry: Mapped[float] = mapped_column(Float)
    notional_usd: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float] = mapped_column(Float, default=0.0)
    initial_stop: Mapped[float] = mapped_column(Float, default=0.0)
    risk_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tp_levels: Mapped[list[Any]] = mapped_column(JSON, default=list)
    trailing_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    peak_price: Mapped[float] = mapped_column(Float, default=0.0)
    strategy_version: Mapped[str] = mapped_column(String(24), default="")
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    exit_reason: Mapped[str] = mapped_column(String(48), default="")
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_price: Mapped[float] = mapped_column(Float, default=0.0)
    last_price_ts: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    events: Mapped[list[Any]] = mapped_column(JSON, default=list)
    trades: Mapped[list[Any]] = relationship("PaperTrade", back_populates="position")


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("paper_positions.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    exit_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_price: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_bps: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    r_multiple: Mapped[float] = mapped_column(Float, default=0.0)
    max_favorable: Mapped[float] = mapped_column(Float, default=0.0)
    max_adverse: Mapped[float] = mapped_column(Float, default=0.0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str] = mapped_column(String(48), default="")
    strategy_version: Mapped[str] = mapped_column(String(24), default="")
    regime: Mapped[str] = mapped_column(String(24), default="unknown")
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    position: Mapped[PaperPosition] = relationship(back_populates="trades")


# --- ops --------------------------------------------------------------------
class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_accounts.id"), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="warn")
    type: Mapped[str] = mapped_column(String(40), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="system")
    actor_id: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(48), index=True)
    entity: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BotState(Base):
    """Singleton (id=1) runtime state for the autonomous bot."""

    __tablename__ = "bot_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="idle")  # idle|running|paused|safe
    safe_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_accounts.id"), nullable=True)
    emergency: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProviderHealth(Base):
    __tablename__ = "provider_health"
    __table_args__ = (UniqueConstraint("provider", name="uq_provider"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    last_success: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    latency_ms_p95: Mapped[float] = mapped_column(Float, default=0.0)
    error_rate_5m: Mapped[float] = mapped_column(Float, default=0.0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd_day: Mapped[float] = mapped_column(Float, default=0.0)


class ProviderCall(Base):
    """Rolling call log used to derive error rate / latency percentiles."""

    __tablename__ = "provider_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    endpoint: Mapped[str] = mapped_column(String(160), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SignalQuality(Base):
    """Tracks forward hit-rate of followed wallets (PRD 8.1 / KPI)."""

    __tablename__ = "signal_quality"
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), index=True)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    horizon_h: Mapped[int] = mapped_column(Integer, default=24)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    forward_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiMemory(Base):
    """PRD 21 - wallet/token/market/strategy/pattern/incident memory."""

    __tablename__ = "ai_memory"
    __table_args__ = (Index("ix_mem_kind_key", "kind", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    key: Mapped[str] = mapped_column(String(160), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
