"""AnalysisPipeline — caminho único das regras para backtest, replay e live.

Ordem por candle fechado ``t``:
1. ``TradeSimulator.on_bar(t)`` resolve ordens/posições originadas em candles anteriores.
2. ``PriceActionEngine.on_bar_closed(t)`` atualiza métricas/regime/pernas.
3. A estratégia avalia ``t``; sinais aprovados viram ordens hipotéticas para ``t+1``.

Assim nenhum componente vê o candle ``t+1`` ao decidir sobre ``t``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from atlas.backtest.costs import CostModel
from atlas.backtest.execution import ExecEvent, TradeSimulator
from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.core.types import Bar, Symbol
from atlas.price_action.engine import PriceActionEngine, PriceActionParams, PriceActionSnapshot
from atlas.strategy.base import SetupEvaluation, Signal
from atlas.backtest.bracket_sim import BracketSimulator
from atlas.strategy.h2l2_momentum import H2L2MomentumParams, H2L2MomentumStrategy
from atlas.strategy.second_entry import SecondEntryParams, SecondEntryStrategy


def make_strategy(symbol: Symbol, params):
    """Instancia a estratégia correspondente ao tipo dos parâmetros (registro simples de estratégias)."""
    if isinstance(params, H2L2MomentumParams):
        return H2L2MomentumStrategy(symbol, params)
    return SecondEntryStrategy(symbol, params)


@dataclass
class PipelineOutput:
    bar: Bar
    snapshot: PriceActionSnapshot
    evaluations: list[SetupEvaluation]
    signals: list[Signal]
    exec_events: list[ExecEvent] = field(default_factory=list)


class AnalysisPipeline:
    def __init__(self, symbol: Symbol | str, strategy_params: SecondEntryParams | H2L2MomentumParams | None = None,
                 pa_params: PriceActionParams | None = None, costs: CostModel | None = None,
                 calendar: TradingCalendar | None = None, simulate: bool = True):
        self.symbol = Symbol(symbol)
        self.cal = calendar or load_calendar()
        if pa_params is None and isinstance(strategy_params, H2L2MomentumParams) and strategy_params.lateral_filter:
            pa_params = PriceActionParams(lateral_filter=True, lateral_min_bars=strategy_params.lateral_min_bars)
        self.engine = PriceActionEngine(pa_params, self.cal)
        self.strategy = make_strategy(self.symbol, strategy_params)
        self.simulate = simulate
        if getattr(self.strategy, "needs_position", False):
            self.sim = BracketSimulator(self.symbol.value, costs or CostModel(),
                                        self.strategy.params.flatten_minutes_before_close, self.cal,
                                        max_lots=None)
        else:
            self.sim = TradeSimulator(self.symbol.value, costs or CostModel(),
                                      self.strategy.params.flatten_minutes_before_close, self.cal)
        self.last_ts = None

    def on_bar_closed(self, bar: Bar) -> PipelineOutput:
        if bar.symbol != self.symbol:
            raise ValueError(f"Pipeline de {self.symbol} recebeu candle de {bar.symbol}")
        if self.last_ts is not None and bar.ts_open <= self.last_ts:
            raise ValueError("Candles devem chegar em ordem cronológica estrita (proteção contra look-ahead)")
        self.last_ts = bar.ts_open
        exec_events = self.sim.on_bar(bar) if self.simulate else []
        snap = self.engine.on_bar_closed(bar)
        if getattr(self.strategy, "needs_position", False):
            evals = self.strategy.on_snapshot(snap, self.sim)
        else:
            evals = self.strategy.on_snapshot(snap)
        signals = [e.signal for e in evals if e.passed and e.signal is not None]
        if self.simulate:
            for s in signals:
                exec_events.append(self.sim.submit(s, bar))
        return PipelineOutput(bar, snap, evals, signals, exec_events)
