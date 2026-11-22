"""Tipos canônicos compartilhados entre os módulos do ATLAS.

Regras:
* Todo timestamp é tz-aware (America/Sao_Paulo ou UTC convertido).
* Campos indisponíveis ficam ``None`` — nunca são inventados.
* ``synthetic=True`` marca dados sintéticos (testes/demonstração), que jamais
  devem ser apresentados como reais.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from atlas.core.timeframe import TIMEFRAME


class Symbol(StrEnum):
    WIN = "WIN"
    WDO = "WDO"
    EURUSD = "EURUSD"  # forex: usado para validar o método em dados reais (sessão Londres/NY)
    USDJPY = "USDJPY"
    USDBRL = "USDBRL"  # dólar/real à vista (forex): substituto do WDO para validar o método

    @property
    def is_fx(self) -> bool:
        return self.value in ("EURUSD", "USDJPY", "USDBRL")


class SeriesKind(StrEnum):
    CONTRACT = "contract"  # contrato negociável específico (ex.: WINZ26)
    CONTINUOUS = "continuous"  # série contínua montada por política de rolagem


class BarStatus(StrEnum):
    FORMING = "forming"
    CLOSED = "closed"
    INVALID = "invalid"


class Quality(StrEnum):
    OK = "ok"
    SUSPECT = "suspect"
    INVALID = "invalid"


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True, slots=True)
class Bar:
    """Candle de 5 minutos. ``ts_open`` é a abertura; ``ts_close = ts_open + 5min``."""

    symbol: Symbol
    contract: str | None
    ts_open: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    trades: int | None = None
    status: BarStatus = BarStatus.CLOSED
    quality: Quality = Quality.OK
    quality_flags: tuple[str, ...] = ()
    source: str = "unknown"
    series_kind: SeriesKind = SeriesKind.CONTRACT
    synthetic: bool = False

    def __post_init__(self) -> None:
        if self.ts_open.tzinfo is None:
            raise ValueError("Bar.ts_open deve ser timezone-aware")

    @property
    def ts_close(self) -> datetime:
        return self.ts_open + TIMEFRAME

    def with_(self, **changes) -> "Bar":
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class DataIssue:
    """Problema de dados detectado por validação (importação ou live)."""

    kind: str
    message: str
    ts: datetime | None = None
    severity: str = "error"  # error | warning | info
    details: dict = field(default_factory=dict)
