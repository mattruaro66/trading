"""PriceActionEngine — processamento incremental de candles fechados.

Esta é a ÚNICA implementação das regras de Price Action. Backtest, replay e
monitoramento ao vivo chamam ``on_bar_closed`` na ordem cronológica.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.core.types import Bar, BarStatus, Quality, Side
from atlas.market_data.validation import is_contiguous, is_next_session_open
from atlas.price_action.bars import BarMetrics, BarParams, compute_metrics
from atlas.price_action.lateral import LateralGate, LateralSnapshot
from atlas.price_action.legs import LegCounter, LegParams, LegSnapshot
from atlas.price_action.regime import RegimeParams, RegimeState, RegimeTracker

ENGINE_VERSION = "1.0.0"


@dataclass(frozen=True)
class PriceActionParams:
    atr_period: int = 20
    bars: BarParams = field(default_factory=BarParams)
    regime: RegimeParams = field(default_factory=RegimeParams)
    legs: LegParams = field(default_factory=LegParams)
    reset_regime_on_new_session: bool = False
    # Tight trading range (Brooks + definição do usuário): suspende a contagem H/L até rompimento confirmado
    lateral_filter: bool = True
    lateral_min_bars: int = 3
    lateral_max_height_adr: float | None = 0.25


@dataclass(frozen=True)
class PriceActionSnapshot:
    bar: Bar
    metrics: BarMetrics
    regime: RegimeState
    bull_legs: LegSnapshot  # contagem H (pullbacks em alta)
    bear_legs: LegSnapshot  # contagem L (pullbacks em baixa)
    contiguous: bool  # candle imediatamente seguinte ao anterior (mesma sessão)
    session_start: bool  # primeiro candle de uma nova sessão (após candle final da sessão anterior)
    reset_reason: str | None  # motivo de reinício total (lacuna, troca de contrato)
    data_ok: bool  # janela de análise sem candles suspeitos/inválidos
    engine_version: str = ENGINE_VERSION
    lateral: LateralSnapshot | None = None  # presente quando o filtro de lateralidade está ligado

    def legs(self, side: Side) -> LegSnapshot:
        return self.bull_legs if side is Side.LONG else self.bear_legs


class PriceActionEngine:
    def __init__(self, params: PriceActionParams | None = None, calendar: TradingCalendar | None = None):
        self.params = params or PriceActionParams()
        self.cal = calendar or load_calendar()
        self.reset()

    def reset(self) -> None:
        p = self.params
        self.prev: Bar | None = None
        self._tr: deque[float] = deque(maxlen=p.atr_period)
        self._quality_window: deque[bool] = deque(maxlen=max(p.regime.lookback, p.atr_period) + 1)
        self.regime = RegimeTracker(p.regime)
        self.bull = LegCounter(Side.LONG, p.legs)
        self.bear = LegCounter(Side.SHORT, p.legs)
        old = getattr(self, "gate", None)
        self.gate = LateralGate(p.lateral_min_bars, p.lateral_max_height_adr) if p.lateral_filter else None
        if old is not None and self.gate is not None:
            self.gate.day_ranges = old.day_ranges  # o histórico de ranges diários sobrevive a lacunas

    def _atr(self) -> float | None:
        if len(self._tr) < self.params.atr_period:
            return None
        return sum(self._tr) / len(self._tr)

    def on_bar_closed(self, bar: Bar) -> PriceActionSnapshot:
        if bar.status is not BarStatus.CLOSED:
            raise ValueError("PriceActionEngine aceita apenas candles fechados (status=closed)")
        reset_reason = None
        contiguous = False
        session_start = False
        prev = self.prev
        if prev is not None:
            if bar.ts_open <= prev.ts_open:
                raise ValueError(f"Candle fora de ordem: {bar.ts_open} <= {prev.ts_open}")
            if bar.contract != prev.contract:
                reset_reason = "contract_change"
            elif is_contiguous(prev, bar):
                contiguous = True
            elif is_next_session_open(prev, bar, self.cal):
                session_start = True
            else:
                reset_reason = "data_gap"
        if reset_reason:
            self.reset()
            prev = None
        elif session_start:
            # pernas sempre reiniciam na nova sessão; regime opcionalmente
            self.bull.reset()
            self.bear.reset()
            if self.gate:
                self.gate.new_session()
            if self.params.reset_regime_on_new_session:
                self.regime.reset()
                self._tr.clear()

        atr_prev = self._atr()
        metrics = compute_metrics(bar, prev, atr_prev, self.params.bars)
        self._tr.append(metrics.true_range)
        regime = self.regime.update(bar.open, bar.close, metrics.overlap_prev, atr_prev)
        leg_prev = prev if not session_start else None
        lat = None
        if self.gate is not None:
            lat = self.gate.update(bar)
            if lat.event in ("breakout_confirmed", "range_expanded"):
                leg_prev = None  # contagem recomeça do zero na barra de follow-through
            elif not lat.counting_allowed:
                # lateralidade (ou rompimento ainda sem continuidade): não há contagem de H/L
                self.bull.reset()
                self.bear.reset()
        if lat is not None and not lat.counting_allowed:
            bull, bear = self.bull.snapshot(), self.bear.snapshot()
        else:
            bull = self.bull.update(bar, leg_prev)
            bear = self.bear.update(bar, leg_prev)
        self._quality_window.append(bar.quality is Quality.OK)
        data_ok = all(self._quality_window)
        self.prev = bar
        return PriceActionSnapshot(bar, metrics, regime, bull, bear, contiguous, session_start, reset_reason, data_ok,
                                   lateral=lat)
