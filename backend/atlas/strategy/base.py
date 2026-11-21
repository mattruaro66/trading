"""Tipos do Strategy Engine: especificação declarativa, critérios e sinais."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from atlas.core.timeframe import TIMEFRAME_LABEL
from atlas.core.types import Bar, Side, Symbol


@dataclass(frozen=True)
class Criterion:
    code: str
    description: str
    passed: bool
    value: object = None
    threshold: object = None

    def as_dict(self) -> dict:
        return {"code": self.code, "description": self.description, "passed": self.passed,
                "value": self.value, "threshold": self.threshold}


@dataclass(frozen=True)
class StrategySpec:
    """Descrição declarativa e versionada de uma estratégia (serializável)."""

    strategy_id: str
    version: str
    name: str
    description: str
    timeframe: str = TIMEFRAME_LABEL
    context: str = ""
    pattern: str = ""
    confirmations: tuple[str, ...] = ()
    entry: str = ""
    stop: str = ""
    target: str = ""
    horizon: str = ""
    cancel_conditions: tuple[str, ...] = ()
    exit_conditions: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def params_hash(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Signal:
    """Configuração confirmada no fechamento da barra de sinal: ordem stop hipotética para a próxima barra."""

    strategy_id: str
    strategy_version: str
    params_hash: str
    profile: str
    symbol: Symbol
    contract: str | None
    side: Side
    signal_bar: Bar
    setup_label: str  # ex.: "H2"
    entry_price: float
    stop_price: float
    target_price: float
    risk_points: float
    risk_ticks: float
    target_r: float
    time_exit_bars: int
    pullback_start_ts: datetime | None
    criteria: tuple[Criterion, ...]
    context: dict = field(default_factory=dict)

    @property
    def signal_ts(self) -> datetime:
        return self.signal_bar.ts_open

    @property
    def setup_key(self) -> str:
        pb = self.pullback_start_ts.isoformat() if self.pullback_start_ts else "na"
        return f"{self.strategy_id}:{self.symbol.value}:{self.contract}:{self.side.value}:{pb}"

    @property
    def reward_risk(self) -> float:
        return abs(self.target_price - self.entry_price) / self.risk_points if self.risk_points else 0.0


@dataclass(frozen=True)
class SetupEvaluation:
    """Avaliação (aprovada ou não) de uma barra candidata — usada por alertas e explicações."""

    strategy_id: str
    side: Side
    setup_label: str
    bar: Bar
    stage: str  # observation | preparation | candidate
    criteria: tuple[Criterion, ...]
    passed: bool
    pullback_start_ts: datetime | None
    signal: Signal | None = None
