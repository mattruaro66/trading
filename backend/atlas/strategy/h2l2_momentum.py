"""Estratégia B — H2/L2 com aumento de mão no H3/L3 + entradas de momento (definida pelo usuário).

Regras (texto do usuário) e a tradução implementada:
* "compra/vende em H2/L2 e aumenta a mão x1 no H3/L3" → ordem stop de 1 contrato 1 tick além da barra de sinal
  do H2/L2; se esse lote ainda estiver aberto, nova ordem stop de +1 contrato na barra de sinal do H3/L3
  do MESMO pullback.
* "Alvo 300 pontos. Stop 200 pontos." → cada lote tem stop e alvo próprios, fixos em pontos a partir do seu
  preço de entrada (antes do slippage).
* "Sempre siga a tendência" → toda entrada exige regime BULL_TREND (compras) ou BEAR_TREND (vendas).
* "Após 3 barras consecutivas, entre a favor do movimento" → no fechamento da 3ª barra seguida na mesma direção
  (C>O para alta, C<O para baixa), a favor da tendência e sem posição: ordem a mercado na abertura seguinte.
* "Barra 2x maior que a média das últimas 7 barras, entre a favor do movimento" → amplitude > 2 × média das
  amplitudes das 7 barras anteriores, na direção da tendência e sem posição: ordem a mercado na abertura seguinte.
* Prioridade no mesmo candle: H2/L2 > barra grande > 3 barras consecutivas. Day trade: tudo zerado no fim da sessão.
"""
from __future__ import annotations

from collections import deque
from datetime import time

from pydantic import BaseModel, Field

from atlas.contracts.specs import get_spec
from atlas.core.types import Side, Symbol
from atlas.price_action.engine import PriceActionSnapshot
from atlas.price_action.regime import Regime
from atlas.strategy.base import Criterion, SetupEvaluation, Signal, StrategySpec, params_hash

STRATEGY_ID = "h2l2_scale_momentum"
STRATEGY_VERSION = "1.0.0"

SPEC = StrategySpec(
    strategy_id=STRATEGY_ID, version=STRATEGY_VERSION, name="H2/L2 + aumento no H3/L3 + momento",
    description="Compra no H2 / vende no L2 a favor da tendência, aumenta 1 contrato no H3/L3; entradas de momento após "
                "3 barras consecutivas ou barra 2x maior que a média das 7 anteriores.",
    context="Sempre a favor da tendência (regime BULL_TREND para compras, BEAR_TREND para vendas).",
    pattern="H2/L2 (entrada), H3/L3 (aumento), 3 barras consecutivas, barra grande.",
    entry="H2/L2/H3/L3: stop 1 tick além da barra de sinal, válida 1 candle. Momento: a mercado na abertura seguinte.",
    stop="200 pontos a partir da entrada de cada lote.", target="300 pontos a partir da entrada de cada lote.",
    horizon="Intradiário; zera no fim da sessão.",
    cancel_conditions=("Ordem stop não acionada no candle seguinte", "Candle seguinte não contíguo"),
    exit_conditions=("Stop do lote", "Alvo do lote", "Fim da sessão", "Lacuna de dados"),
    references=("Regras definidas pelo usuário; H2/L2/H3/L3 conforme a contagem de pernas de Al Brooks (STRATEGIES.md §4).",),
)


class H2L2MomentumParams(BaseModel):
    profile: str = "usuario"
    stop_points: float = Field(200, gt=0)
    target_points: float = Field(300, gt=0)
    add_on_third: bool = True
    consecutive_bars: int = Field(3, ge=2, le=10)
    big_bar_multiple: float = Field(2.0, gt=1)
    big_bar_lookback: int = Field(7, ge=2, le=50)
    enable_momentum: bool = True
    enable_big_bar: bool = True
    first_entry_time: time = time(9, 15)
    last_entry_time: time = time(17, 30)
    flatten_minutes_before_close: int = Field(5, ge=0, le=60)
    max_lots: int = Field(2, ge=1, le=5)
    allow_long: bool = True
    allow_short: bool = True

    @property
    def target_r(self) -> float:
        return self.target_points / self.stop_points

    time_exit_bars: int = 0  # sem saída por tempo (somente stop, alvo ou fim da sessão)


class H2L2MomentumStrategy:
    spec = SPEC
    needs_position = True

    def __init__(self, symbol: Symbol | str, params: H2L2MomentumParams | None = None):
        self.symbol = Symbol(symbol)
        self.params = params or H2L2MomentumParams()
        self.cs = get_spec(self.symbol)
        self.params_hash = params_hash({**self.params.model_dump(mode="json"), "symbol": self.symbol.value})
        self._ranges: deque[float] = deque(maxlen=self.params.big_bar_lookback)
        self._run_dir = 0
        self._run_len = 0

    def resolved_params(self) -> dict:
        return self.params.model_dump(mode="json")

    # ------------------------------------------------------------------
    def on_snapshot(self, snap: PriceActionSnapshot, sim=None) -> list[SetupEvaluation]:
        p, bar, m = self.params, snap.bar, snap.metrics
        if snap.reset_reason or snap.session_start:
            self._run_dir, self._run_len = 0, 0
        if snap.reset_reason:
            self._ranges.clear()
        prior = list(self._ranges)
        self._ranges.append(m.range)
        d = 1 if bar.close > bar.open else -1 if bar.close < bar.open else 0
        if d != 0 and d == self._run_dir:
            self._run_len += 1
        else:
            self._run_dir, self._run_len = d, (1 if d else 0)

        regime = snap.regime.regime
        trend = Side.LONG if regime is Regime.BULL_TREND else Side.SHORT if regime is Regime.BEAR_TREND else None
        out: list[SetupEvaluation] = []
        if trend is None or (trend is Side.LONG and not p.allow_long) or (trend is Side.SHORT and not p.allow_short):
            return out
        legs = snap.legs(trend)
        t_local = bar.ts_close.timetz().replace(tzinfo=None)
        in_time = p.first_entry_time <= t_local <= p.last_entry_time
        flat = sim is None or sim.exposure() == 0
        same_side = sim is not None and sim.side is trend
        common = [
            Criterion("T1", "Sempre a favor da tendência", True, regime.value, "BULL_TREND/BEAR_TREND"),
            Criterion("T2", "Horário dentro da janela de entradas", in_time, t_local.strftime("%H:%M"),
                      [p.first_entry_time.strftime("%H:%M"), p.last_entry_time.strftime("%H:%M")]),
            Criterion("T3", "Dados válidos na janela de análise", snap.data_ok, snap.data_ok, True),
        ]

        cand = legs.candidate_for
        # informação para alertas de observação/preparação
        if cand not in (2, 3) and legs.in_pullback:
            stage = "preparation" if legs.count >= 1 else "observation"
            out.append(SetupEvaluation(STRATEGY_ID, trend, legs.label(2), bar, stage, tuple(common[:1]), False, legs.pullback_start_ts))

        chosen = None
        if cand == 2:
            crit = common + [Criterion("P1", "Sem posição aberta (entrada inicial)", flat, "zerado" if flat else "posicionado", "zerado")]
            chosen = ("leg", legs.label(2), "entry", crit)
        elif cand == 3 and p.add_on_third:
            has_lot = same_side and any(t.signal.pullback_start_ts == legs.pullback_start_ts
                                        and t.signal.setup_label == legs.label(2) for t in sim.lots)
            crit = common + [Criterion("P2", f"Lote do {legs.label(2)} aberto neste pullback (aumento de mão)", has_lot, has_lot, True),
                             Criterion("P3", "Limite de contratos", sim is None or sim.exposure() < p.max_lots,
                                       None if sim is None else sim.exposure(), p.max_lots)]
            chosen = ("leg", legs.label(3), "add", crit)
        if chosen is None or not all(c.passed for c in chosen[3]):
            alt = None
            avg = sum(prior) / len(prior) if len(prior) == p.big_bar_lookback else None
            if p.enable_big_bar and avg and d == trend.sign and m.range > p.big_bar_multiple * avg:
                crit = common + [Criterion("B1", f"Amplitude > {p.big_bar_multiple:g}× a média das {p.big_bar_lookback} barras anteriores",
                                           True, round(m.range / avg, 2), p.big_bar_multiple),
                                 Criterion("P1", "Sem posição aberta", flat, "zerado" if flat else "posicionado", "zerado")]
                alt = ("market", "BARRA 2x", "entry", crit)
            elif p.enable_momentum and self._run_len == p.consecutive_bars and self._run_dir == trend.sign:
                crit = common + [Criterion("M1", f"{p.consecutive_bars} barras consecutivas a favor", True, self._run_len, p.consecutive_bars),
                                 Criterion("P1", "Sem posição aberta", flat, "zerado" if flat else "posicionado", "zerado")]
                alt = ("market", f"{p.consecutive_bars} BARRAS", "entry", crit)
            if alt is not None and (chosen is None or all(c.passed for c in alt[3])):
                chosen = alt
        if chosen is None:
            return out

        kind, label, role, crit = chosen
        passed = all(c.passed for c in crit)
        signal = None
        if passed:
            if kind == "leg":
                otype = "stop"
                entry = self.cs.round_to_tick(bar.high + self.cs.tick_size if trend is Side.LONG else bar.low - self.cs.tick_size)
                pb = legs.pullback_start_ts
            else:
                otype = "market"
                entry = bar.close  # referência; executa na abertura do próximo candle
                pb = bar.ts_open
            sign = trend.sign
            stop = self.cs.round_to_tick(entry - sign * p.stop_points)
            target = self.cs.round_to_tick(entry + sign * p.target_points)
            signal = Signal(
                strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, params_hash=self.params_hash, profile=p.profile,
                symbol=self.symbol, contract=bar.contract, side=trend, signal_bar=bar, setup_label=label, entry_price=entry,
                stop_price=stop, target_price=target, risk_points=p.stop_points,
                risk_ticks=self.cs.price_diff_to_ticks(p.stop_points), target_r=p.target_r, time_exit_bars=0,
                pullback_start_ts=pb, criteria=tuple(crit),
                context={"regime": snap.regime.as_dict(), "bar": m.as_dict(), "legs": {"count": legs.count}},
                order_type=otype, lot_role=role, stop_points=p.stop_points, target_points=p.target_points)
        out.append(SetupEvaluation(STRATEGY_ID, trend, label, bar, "candidate", tuple(crit), passed,
                                   signal.pullback_start_ts if signal else legs.pullback_start_ts, signal))
        return out
