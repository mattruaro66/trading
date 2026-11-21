"""Modelos canônicos do Live Market Data.

Campos indisponíveis no provedor permanecem ``None`` — nunca são inventados
(quantidade, trade_id, sequence_number). Timestamps sempre com fuso.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from atlas.core.types import Bar


class EventType(StrEnum):
    TRADE = "trade"  # negócio realizado
    QUOTE = "quote"  # atualização de melhor oferta (bid/ask) — NUNCA usada para OHLCV
    STATUS = "status"


class DataQualityStatus(StrEnum):
    OK = "ok"
    LATE = "late"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    SUSPECT = "suspect"
    REJECTED = "rejected"


@dataclass(frozen=True)
class MarketEvent:
    provider: str
    symbol: str
    contract: str | None
    event_type: EventType
    exchange_timestamp: datetime
    received_timestamp: datetime
    price: float | None = None
    quantity: float | None = None
    trade_id: str | None = None
    sequence_number: int | None = None
    bid: float | None = None
    ask: float | None = None
    data_quality_status: DataQualityStatus = DataQualityStatus.OK
    synthetic: bool = False
    raw: dict | None = None  # evento original, apenas quando a licença permitir

    def __post_init__(self) -> None:
        if self.exchange_timestamp.tzinfo is None or self.received_timestamp.tzinfo is None:
            raise ValueError("MarketEvent exige timestamps com fuso explícito")

    @property
    def latency_ms(self) -> float:
        return (self.received_timestamp - self.exchange_timestamp).total_seconds() * 1000


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    FAILED = "failed"
    HISTORICAL_ONLY = "historical_only"  # provedor sem conectividade ao vivo (ex.: CSV)
    NOT_CONFIGURED = "not_configured"


@dataclass
class ProviderStatus:
    provider: str
    state: ConnectionState
    simulated: bool  # True para ReplayProvider (nunca apresentado como feed real)
    subscriptions: list[str] = field(default_factory=list)
    last_event_at: datetime | None = None
    last_exchange_ts: datetime | None = None
    latency_ms_last: float | None = None
    latency_ms_p50: float | None = None
    latency_ms_p95: float | None = None
    reconnect_attempts: int = 0
    message: str | None = None

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("last_event_at", "last_exchange_ts"):
            d[k] = d[k].isoformat() if d[k] else None
        d["state"] = self.state.value
        return d


# ---------------- eventos tipados publicados pelo Candle Engine / monitor ----------------
@dataclass(frozen=True)
class CandleUpdated:
    bar: Bar  # status=forming
    kind: str = "CandleUpdated"


@dataclass(frozen=True)
class CandleClosed:
    bar: Bar  # status=closed
    kind: str = "CandleClosed"


@dataclass(frozen=True)
class CandleInvalidated:
    bar: Bar
    reason: str
    kind: str = "CandleInvalidated"


@dataclass(frozen=True)
class DataGapDetected:
    symbol: str
    contract: str | None
    start: datetime
    end: datetime | None
    reason: str  # sequence_gap | no_trades | connection_lost | late_event
    details: dict = field(default_factory=dict)
    kind: str = "DataGapDetected"


@dataclass(frozen=True)
class ConnectionDegraded:
    provider: str
    ts: datetime
    reason: str
    kind: str = "ConnectionDegraded"


@dataclass(frozen=True)
class ConnectionRecovered:
    provider: str
    ts: datetime
    recovered_events: int
    complete: bool  # True se a recuperação cobriu todo o intervalo afetado
    kind: str = "ConnectionRecovered"


LiveEvent = CandleUpdated | CandleClosed | CandleInvalidated | DataGapDetected | ConnectionDegraded | ConnectionRecovered
