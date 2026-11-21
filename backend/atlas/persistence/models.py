"""Modelos SQLAlchemy. Timestamps persistidos em UTC; convertidos para America/Sao_Paulo na leitura."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ContractRow(Base):
    __tablename__ = "contracts"
    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(8), index=True)
    expiry_date: Mapped[str] = mapped_column(String(10))
    last_trading_day: Mapped[str] = mapped_column(String(10))
    rule: Mapped[str] = mapped_column(String(64))
    spec_version: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DataImportRow(Base):
    __tablename__ = "data_imports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(8), index=True)
    contract: Mapped[str | None] = mapped_column(String(12), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    format: Mapped[str] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CandleRow(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "contract", "ts_open", name="uq_candle"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(8), index=True)
    contract: Mapped[str] = mapped_column(String(12), index=True)  # "continuous" para séries contínuas
    series_kind: Mapped[str] = mapped_column(String(16), default="contract")
    ts_open: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="closed")
    quality: Mapped[str] = mapped_column(String(12), default="ok")
    quality_flags: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(String(255))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("data_imports.id"), nullable=True)


class ProviderRow(Base):
    __tablename__ = "providers"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConnectionEventRow(Base):
    __tablename__ = "connection_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategyVersionRow(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", "params_hash", name="uq_strategy_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16))
    params_hash: Mapped[str] = mapped_column(String(32))
    spec: Mapped[dict] = mapped_column(JSON)
    params: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AlertRow(Base):
    """Conteúdo imutável do alerta: nunca é atualizado após inserção (verificado por content_hash)."""

    __tablename__ = "alerts"
    alert_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    setup_key: Mapped[str] = mapped_column(String(255), index=True)
    origin: Mapped[str] = mapped_column(String(16))  # replay | live | backtest
    symbol: Mapped[str] = mapped_column(String(8), index=True)
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class AlertEventRow(Base):
    __tablename__ = "alert_events"
    __table_args__ = (UniqueConstraint("alert_id", "seq", name="uq_alert_event"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.alert_id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))


class BacktestRunRow(Base):
    __tablename__ = "backtest_runs"
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(8), index=True)
    strategy_id: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(16))
    params_hash: Mapped[str] = mapped_column(String(32), index=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
