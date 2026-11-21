"""Estratégia A — Second Entry em tendência (H2 em alta / L2 em baixa). Ver STRATEGIES.md §5."""
from __future__ import annotations

from datetime import time

from pydantic import BaseModel, Field, field_validator, model_validator

from atlas.contracts.specs import get_spec
from atlas.core.types import Side, Symbol
from atlas.price_action.bars import BarClass
from atlas.price_action.engine import PriceActionSnapshot
from atlas.price_action.regime import Regime
from atlas.strategy.base import Criterion, SetupEvaluation, Signal, StrategySpec, params_hash

STRATEGY_ID = "second_entry_trend"
STRATEGY_VERSION = "1.0.0"

SPEC = StrategySpec(
    strategy_id=STRATEGY_ID,
    version=STRATEGY_VERSION,
    name="Second Entry em tendência",
    description="Segunda tentativa de retomada (H2/L2) após pullback em tendência, com barra de sinal de qualidade.",
    context="Regime BULL_TREND (compra) ou BEAR_TREND (venda) no fechamento da barra de sinal.",
    pattern="Barra candidata a sinal do H2 (compra) ou L2 (venda) segundo a máquina de contagem de pernas.",
    confirmations=("Fechamento da barra de sinal no terço favorável", "Barra de sinal não é trend bar contrária",
                   "Amplitude da barra de sinal entre limites relativos ao ATR", "Risco em ticks dentro dos limites",
                   "Horário dentro da janela de entradas", "Dados válidos na janela de análise"),
    entry="Ordem stop 1 tick além da extremidade da barra de sinal, válida apenas no candle seguinte.",
    stop="1 tick além da extremidade oposta da barra de sinal (ou do pullback, conforme parâmetro).",
    target="target_r × risco a partir da entrada, arredondado ao tick de forma conservadora.",
    horizon="time_exit_bars candles de 5 min após a entrada, ou fim da sessão.",
    cancel_conditions=("Ordem não acionada no candle seguinte", "Candle seguinte não contíguo (lacuna/sessão/contrato)"),
    exit_conditions=("Stop", "Alvo", "Tempo", "Fim da sessão", "Lacuna de dados com posição aberta"),
    references=("Brooks, A. Trading Price Action Trends. Wiley, 2012.",
                "Brooks, A. Reading Price Charts Bar by Bar. Wiley, 2009."),
)

PROFILES = {
    "scalp": {"target_r": 1.0, "time_exit_bars": 6},
    "daytrade": {"target_r": 2.0, "time_exit_bars": 24},
}

DEFAULT_RISK_TICKS = {Symbol.WIN: (4, 80), Symbol.WDO: (2, 40)}


class SecondEntryParams(BaseModel):
    profile: str = Field("daytrade", description="scalp | daytrade (ambos em 5 min)")
    entry_count: int = Field(2, ge=1, le=4, description="2 = segunda entrada (H2/L2)")
    target_r: float = Field(2.0, gt=0, le=10)
    time_exit_bars: int = Field(24, ge=1, le=120)
    stop_mode: str = Field("signal_bar", description="signal_bar | pullback_low")
    min_signal_close_pos: float = Field(0.50, ge=0, le=1)
    reject_opposite_trend_bar: bool = True
    min_signal_rel_range: float = Field(0.3, ge=0)
    max_signal_rel_range: float = Field(2.0, gt=0)
    min_risk_ticks: int | None = None
    max_risk_ticks: int | None = None
    first_entry_time: time = time(9, 15)
    last_entry_time: time = time(17, 30)
    flatten_minutes_before_close: int = Field(5, ge=0, le=60)
    allow_long: bool = True
    allow_short: bool = True

    @field_validator("profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"perfil inválido: {v}")
        return v

    @field_validator("stop_mode")
    @classmethod
    def _stop_mode(cls, v: str) -> str:
        if v not in ("signal_bar", "pullback_low"):
            raise ValueError("stop_mode deve ser signal_bar ou pullback_low")
        return v

    @model_validator(mode="after")
    def _apply_profile_defaults(self) -> "SecondEntryParams":
        # target_r/time_exit_bars não informados explicitamente herdam o perfil
        for k, v in PROFILES[self.profile].items():
            if k not in self.model_fields_set:
                object.__setattr__(self, k, v)
        return self

    @classmethod
    def for_profile(cls, profile: str, **overrides) -> "SecondEntryParams":
        return cls(profile=profile, **{**PROFILES[profile], **overrides})


class SecondEntryStrategy:
    spec = SPEC

    def __init__(self, symbol: Symbol | str, params: SecondEntryParams | None = None):
        self.symbol = Symbol(symbol)
        self.params = params or SecondEntryParams.for_profile("daytrade")
        self.spec_contract = get_spec(self.symbol)
        lo, hi = DEFAULT_RISK_TICKS[self.symbol]
        self.min_risk = self.params.min_risk_ticks if self.params.min_risk_ticks is not None else lo
        self.max_risk = self.params.max_risk_ticks if self.params.max_risk_ticks is not None else hi
        self.params_hash = params_hash({**self.params.model_dump(mode="json"), "min_risk": self.min_risk,
                                        "max_risk": self.max_risk, "symbol": self.symbol.value})

    def resolved_params(self) -> dict:
        return {**self.params.model_dump(mode="json"), "min_risk_ticks": self.min_risk, "max_risk_ticks": self.max_risk}

    # ------------------------------------------------------------------
    def on_snapshot(self, snap: PriceActionSnapshot) -> list[SetupEvaluation]:
        out: list[SetupEvaluation] = []
        for side, regime_ok, allowed in ((Side.LONG, Regime.BULL_TREND, self.params.allow_long),
                                         (Side.SHORT, Regime.BEAR_TREND, self.params.allow_short)):
            if not allowed:
                continue
            ev = self._evaluate_side(snap, side, regime_ok)
            if ev:
                out.append(ev)
        return out

    def _evaluate_side(self, snap: PriceActionSnapshot, side: Side, trend_regime: Regime) -> SetupEvaluation | None:
        p = self.params
        legs = snap.legs(side)
        bar, m = snap.bar, snap.metrics
        in_trend = snap.regime.regime is trend_regime
        n = p.entry_count
        label = legs.label(n)

        # Estágios informativos (para alertas): observação = pullback em tendência; preparação = H1/L1 ocorreu.
        if legs.candidate_for != n:
            if in_trend and legs.in_pullback:
                stage = "preparation" if legs.count >= n - 1 and legs.count >= 1 else "observation"
                crit = (Criterion("C1", "Regime de tendência a favor", True, snap.regime.regime.value, trend_regime.value),)
                return SetupEvaluation(STRATEGY_ID, side, label, bar, stage, crit, False, legs.pullback_start_ts)
            return None

        tick = self.spec_contract.tick_size
        sign = side.sign
        local_t = bar.ts_close.timetz().replace(tzinfo=None)
        if side is Side.LONG:
            entry = self.spec_contract.round_to_tick(bar.high + tick)
            ref = bar.low if p.stop_mode == "signal_bar" else min(bar.low, legs.pullback_extreme or bar.low)
            stop = self.spec_contract.round_to_tick(ref - tick)
            close_quality = m.close_pos
            opposite = m.bar_class is BarClass.BEAR_TREND
        else:
            entry = self.spec_contract.round_to_tick(bar.low - tick)
            ref = bar.high if p.stop_mode == "signal_bar" else max(bar.high, legs.pullback_extreme or bar.high)
            stop = self.spec_contract.round_to_tick(ref + tick)
            close_quality = 1 - m.close_pos
            opposite = m.bar_class is BarClass.BULL_TREND
        risk = abs(entry - stop)
        risk_ticks = self.spec_contract.price_diff_to_ticks(risk)
        target_raw = entry + sign * p.target_r * risk
        target = self.spec_contract.round_to_tick(target_raw, "down" if side is Side.LONG else "up")

        rel = m.rel_range
        criteria = (
            Criterion("C1", "Regime de tendência a favor", in_trend, snap.regime.regime.value, trend_regime.value),
            Criterion("C2", f"Barra candidata a sinal do {label}", True, label, label),
            Criterion("C3", "Fechamento da barra de sinal no lado favorável", close_quality >= p.min_signal_close_pos,
                      round(close_quality, 3), p.min_signal_close_pos),
            Criterion("C4", "Barra de sinal não é trend bar contrária",
                      not (p.reject_opposite_trend_bar and opposite), m.bar_class.value, "não contrária"),
            Criterion("C5", "Amplitude relativa ao ATR dentro dos limites",
                      rel is not None and p.min_signal_rel_range <= rel <= p.max_signal_rel_range,
                      None if rel is None else round(rel, 3), [p.min_signal_rel_range, p.max_signal_rel_range]),
            Criterion("C6", "Risco em ticks dentro dos limites", self.min_risk <= risk_ticks <= self.max_risk,
                      risk_ticks, [self.min_risk, self.max_risk]),
            Criterion("C7", "Horário dentro da janela de entradas",
                      p.first_entry_time <= local_t <= p.last_entry_time, local_t.strftime("%H:%M"),
                      [p.first_entry_time.strftime("%H:%M"), p.last_entry_time.strftime("%H:%M")]),
            Criterion("C8", "Dados válidos na janela de análise", snap.data_ok, snap.data_ok, True),
        )
        passed = all(c.passed for c in criteria)
        signal = None
        if passed:
            signal = Signal(
                strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, params_hash=self.params_hash,
                profile=p.profile, symbol=self.symbol, contract=bar.contract, side=side, signal_bar=bar,
                setup_label=label, entry_price=entry, stop_price=stop, target_price=target, risk_points=risk,
                risk_ticks=risk_ticks, target_r=p.target_r, time_exit_bars=p.time_exit_bars,
                pullback_start_ts=legs.pullback_start_ts, criteria=criteria,
                context={"regime": snap.regime.as_dict(), "bar": m.as_dict(), "legs": {
                    "count": legs.count, "swing_extreme": legs.swing_extreme,
                    "pullback_extreme": legs.pullback_extreme, "bars_in_pullback": legs.bars_in_pullback}},
            )
        stage = "candidate"
        return SetupEvaluation(STRATEGY_ID, side, label, bar, stage, criteria, passed, legs.pullback_start_ts, signal)
