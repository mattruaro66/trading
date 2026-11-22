"""Contagem de pernas High 1/High 2 e Low 1/Low 2 (ver STRATEGIES.md §4).

Implementação incremental: ``update(bar, prev)`` é chamado uma vez por candle
fechado, na ordem cronológica, e nunca acessa candles futuros.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from atlas.core.types import Bar, Side


@dataclass(frozen=True)
class LegParams:
    max_count: int = 4
    max_pullback_bars: int = 20  # Brooks: após ~20 barras o pullback vira trading range
    two_legs: bool = False  # True: depois do H1 só rearma com nova mínima do pullback (2ª perna), não só com máxima menor


@dataclass(frozen=True)
class LegSnapshot:
    side: Side  # LONG → contagem H (pullback em alta); SHORT → contagem L
    in_pullback: bool
    count: int
    armed: bool
    swing_extreme: float | None
    pullback_extreme: float | None  # mínima do pullback (LONG) / máxima (SHORT)
    pullback_start_ts: datetime | None
    bars_in_pullback: int
    event: str | None  # "H1", "H2", "L1"... registrado NESTA barra
    reset_reason: str | None

    @property
    def candidate_for(self) -> int | None:
        """Se a barra atual é candidata a barra de sinal, retorna o número da contagem que ela acionaria."""
        if self.in_pullback and self.armed:
            return self.count + 1
        return None

    def label(self, n: int) -> str:
        return ("H" if self.side is Side.LONG else "L") + str(n)


@dataclass
class LegCounter:
    side: Side
    params: LegParams = field(default_factory=LegParams)
    swing: float | None = None
    in_pullback: bool = False
    count: int = 0
    armed: bool = False
    pb_extreme: float | None = None
    pb_start: datetime | None = None
    bars_in_pb: int = 0
    lo_at_count: float | None = None  # extremo do pullback quando a última contagem disparou

    def reset(self, swing: float | None = None) -> None:
        self.swing = swing
        self.in_pullback = False
        self.count = 0
        self.armed = False
        self.pb_extreme = None
        self.pb_start = None
        self.bars_in_pb = 0
        self.lo_at_count = None

    # Para LONG: "acima" = maior máxima. Para SHORT: espelho com mínimas.
    def _hi(self, b: Bar) -> float:
        return b.high if self.side is Side.LONG else -b.low

    def _lo(self, b: Bar) -> float:
        return b.low if self.side is Side.LONG else -b.high

    def update(self, bar: Bar, prev: Bar | None) -> LegSnapshot:
        event: str | None = None
        reset_reason: str | None = None
        hi, lo = self._hi(bar), self._lo(bar)
        if prev is None:
            self.reset(hi)
        elif not self.in_pullback:
            self.swing = hi if self.swing is None else max(self.swing, hi)
            # Brooks: pullback bar (mínima abaixo da anterior) ou pause bar (máxima igual ou abaixo da anterior)
            if hi <= self._hi(prev) or lo < self._lo(prev):
                self.in_pullback = True
                self.count = 0
                self.armed = True
                self.pb_extreme = lo
                self.pb_start = bar.ts_open
                self.bars_in_pb = 1
        else:
            self.bars_in_pb += 1
            if self.armed and hi > self._hi(prev):
                self.count += 1
                self.armed = False
                self.lo_at_count = min(self.pb_extreme, lo) if self.pb_extreme is not None else lo
                event = ("H" if self.side is Side.LONG else "L") + str(self.count)
            elif self.params.two_legs:
                # Brooks: o H2 é o fim da SEGUNDA perna — exige nova mínima do pullback depois do H1
                if self.lo_at_count is not None and lo < self.lo_at_count:
                    self.armed = True
            elif hi < self._hi(prev):  # Brooks: "bar with a lower high" rearma para o próximo H
                self.armed = True
            self.pb_extreme = lo if self.pb_extreme is None else min(self.pb_extreme, lo)
            if self.swing is not None and hi > self.swing:
                reset_reason = "trend_resumed"
                self.reset(hi)
            elif self.count > self.params.max_count:
                reset_reason = "max_count"
                self.reset(hi)
            elif self.bars_in_pb > self.params.max_pullback_bars:
                reset_reason = "max_pullback_bars"
                self.reset(hi)
        return self.snapshot(event, reset_reason)

    def snapshot(self, event: str | None = None, reset_reason: str | None = None) -> LegSnapshot:
        sign = 1 if self.side is Side.LONG else -1
        conv = (lambda x: None if x is None else sign * x)
        return LegSnapshot(self.side, self.in_pullback, self.count, self.armed, conv(self.swing), conv(self.pb_extreme),
                           self.pb_start, self.bars_in_pb, event, reset_reason)
