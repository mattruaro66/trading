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
* Sem janela de horário, sem exigência de posição (zerado/posicionado), sem limite de contratos e sem prioridade:
  toda regra que disparar é executada. Único filtro adicional: qualidade dos dados (apenas no backtest simulado).
* MODELO EXPERIMENTAL para avaliar análise e execução; não faz parte da versão final.
"""
from __future__ import annotations

from collections import deque
from pydantic import BaseModel, Field

from atlas.contracts.specs import get_spec
from atlas.core.types import Side, Symbol
from atlas.price_action.engine import PriceActionSnapshot
from atlas.price_action.regime import Regime
from atlas.strategy.base import Criterion, SetupEvaluation, Signal, StrategySpec, params_hash

STRATEGY_ID = "h2l2_scale_momentum"
STRATEGY_VERSION = "1.1.0"

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
    # Outros mercados (forex): stop como fração do range diário médio; o alvo mantém a proporção alvo/stop (300/200)
    stop_adr_frac: float | None = Field(None, gt=0, le=1)
    lateral_filter: bool = True  # não conta H/L em lateralidade (3+ barras com fechamento dentro da barra anterior)
    lateral_min_bars: int = Field(3, ge=2, le=20)
    # Stop técnico (v1.1): 1 tick além do extremo oposto do candle de ativação; se maior que o stop estabelecido
    # (stop_points ou stop_adr_frac), a operação não é executada — exceto a continuidade de 3 candles expressivos.
    stop_mode: str = Field("technical", description="technical | fixed")
    momentum_min_range_atr: float = Field(1.0, ge=0, description="Candle expressivo: amplitude ≥ X × ATR")
    momentum_min_volume_mult: float = Field(1.5, ge=0, description="…ou volume ≥ X × média dos 20 candles anteriores")
    momentum_breakout_lookback: int = Field(20, ge=5, le=100, description="Rompimento: fecha além do extremo de N candles")
    min_stop_atr: float | None = Field(None, ge=0, description="Pesquisa: stop técnico mínimo em múltiplos do ATR")
    min_stop_action: str = Field("skip", description="skip = não executa | widen = alarga o stop até o mínimo")
    tech_stop_ref: str = Field("signal_bar", description="signal_bar | pullback (extremo do pullback, só H/L)")
    tech_target_r: float | None = Field(None, gt=0, description="Pesquisa: alvo em R no stop técnico (padrão 300/200)")
    momentum_require_expressive: bool = Field(True, description="v1.1: exige candles expressivos e tendência/rompimento")
    enable_momentum: bool = True
    enable_big_bar: bool = True
    flatten_minutes_before_close: int = Field(5, ge=0, le=60)
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
        self._hist: deque = deque(maxlen=max(self.params.momentum_breakout_lookback, 20) + 5)  # (bar, atr) anteriores

    def resolved_params(self) -> dict:
        return self.params.model_dump(mode="json")

    # ------------------------------------------------------------------
    def on_snapshot(self, snap: PriceActionSnapshot, sim=None) -> list[SetupEvaluation]:
        p, bar, m = self.params, snap.bar, snap.metrics
        if snap.reset_reason or snap.session_start:
            self._run_dir, self._run_len = 0, 0
        if snap.reset_reason:
            self._ranges.clear()
            self._hist.clear()
        hist = list(self._hist)
        self._hist.append((bar, m.atr))
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
        # continuidade (3 candles) também vale em rompimento, mesmo sem regime de tendência
        momentum_side, momentum_info = self._momentum(snap, hist, d)
        if trend is None and momentum_side is not None:
            trend = momentum_side
        if trend is None or (trend is Side.LONG and not p.allow_long) or (trend is Side.SHORT and not p.allow_short):
            return out
        in_trend = regime in (Regime.BULL_TREND, Regime.BEAR_TREND)
        legs = snap.legs(trend)
        stop_pts, tgt_pts = p.stop_points, p.target_points
        adr_ok = True
        if p.stop_adr_frac is not None:
            adr = snap.lateral.adr if snap.lateral is not None else None
            adr_ok = adr is not None
            if adr_ok:
                stop_pts = max(self.cs.tick_size, self.cs.round_to_tick(p.stop_adr_frac * adr))
                tgt_pts = self.cs.round_to_tick(stop_pts * p.target_points / p.stop_points)
        common = [
            Criterion("T1", "Sempre a favor da tendência (ou rompimento, na continuidade)", True, regime.value,
                      "BULL_TREND/BEAR_TREND"),
            # filtro de qualidade de dados: aplica-se somente ao backtest/simulação deste modelo
            Criterion("T3", "Dados válidos na janela de análise", snap.data_ok, snap.data_ok, True),
        ] + ([] if p.stop_adr_frac is None else [
            Criterion("T4", "Range diário médio disponível (stop proporcional)", adr_ok, adr_ok, True)])

        cand = legs.candidate_for
        if cand not in (2, 3) and legs.in_pullback:
            stage = "preparation" if legs.count >= 1 else "observation"
            out.append(SetupEvaluation(STRATEGY_ID, trend, legs.label(2), bar, stage, tuple(common[:1]), False, legs.pullback_start_ts))

        # Todas as regras do plano que dispararem neste candle são executadas (sem prioridade entre elas).
        triggered = []
        if cand == 2 and in_trend:
            triggered.append(("leg", legs.label(2), "entry", common + [Criterion("R1", f"Barra de sinal do {legs.label(2)}", True, legs.label(2), legs.label(2))]))
        elif cand == 3 and p.add_on_third and in_trend:
            triggered.append(("leg", legs.label(3), "add", common + [Criterion("R2", f"Barra de sinal do {legs.label(3)}: aumenta a mão x1", True, legs.label(3), legs.label(3))]))
        avg = sum(prior) / len(prior) if len(prior) == p.big_bar_lookback else None
        if p.enable_big_bar and in_trend and avg and d == trend.sign and m.range > p.big_bar_multiple * avg:
            triggered.append(("market", "BARRA 2x", "entry", common + [Criterion(
                "R3", f"Amplitude > {p.big_bar_multiple:g}× a média das {p.big_bar_lookback} barras anteriores",
                True, round(m.range / avg, 2), p.big_bar_multiple)]))
        if p.enable_momentum and momentum_side is trend:
            triggered.append(("market", f"{p.consecutive_bars} BARRAS", "entry", common + [
                Criterion("R4", f"{p.consecutive_bars} barras consecutivas a favor", True, self._run_len, p.consecutive_bars),
                Criterion("R5", "Candles expressivos (amplitude ≥ ATR ou volume ≥ 1,5× a média)", True,
                          momentum_info["expressive"], True),
                Criterion("R6", "Em tendência ou rompimento", True, momentum_info["context"], "tendência/rompimento")]))

        for kind, label, role, crit in triggered:
            sign = trend.sign
            tick = self.cs.tick_size
            if kind == "leg":
                otype = "stop"
                entry = self.cs.round_to_tick(bar.high + tick if trend is Side.LONG else bar.low - tick)
                pb = legs.pullback_start_ts
            else:
                otype = "market"
                entry = bar.close  # referência; executa na abertura do próximo candle
                pb = bar.ts_open
            sl_pts, tp_pts, fixed = stop_pts, tgt_pts, True
            if p.stop_mode == "technical":
                ref_lo, ref_hi = bar.low, bar.high
                if p.tech_stop_ref == "pullback" and kind == "leg" and legs.pullback_extreme is not None:
                    ref_lo, ref_hi = min(bar.low, legs.pullback_extreme), max(bar.high, legs.pullback_extreme)
                tech_stop = self.cs.round_to_tick(ref_lo - tick if trend is Side.LONG else ref_hi + tick)
                sl_pts = abs(entry - tech_stop)
                floor_ok = True
                if p.min_stop_atr is not None and m.atr:
                    floor = self.cs.round_to_tick(p.min_stop_atr * m.atr, "up")
                    if sl_pts < floor:
                        if p.min_stop_action == "widen":
                            sl_pts = floor
                        else:
                            floor_ok = False
                ratio = p.tech_target_r if p.tech_target_r is not None else p.target_points / p.stop_points
                tp_pts = self.cs.round_to_tick(sl_pts * ratio, "down")  # alvo conservador
                fixed = False
                continuity = label.endswith("BARRAS")
                crit = crit + [Criterion("S1", "Stop técnico até o máximo estabelecido" +
                                         (" (exceção: continuidade)" if continuity else ""),
                                         continuity or sl_pts <= stop_pts,
                                         self.cs.price_diff_to_ticks(sl_pts), self.cs.price_diff_to_ticks(stop_pts))]
                if p.min_stop_atr is not None:
                    crit = crit + [Criterion("S2", f"Stop técnico ≥ {p.min_stop_atr:g}× ATR", floor_ok,
                                             self.cs.price_diff_to_ticks(sl_pts), p.min_stop_atr)]
            passed = all(c.passed for c in crit) and sl_pts > 0
            signal = None
            if passed:
                signal = Signal(
                    strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, params_hash=self.params_hash, profile=p.profile,
                    symbol=self.symbol, contract=bar.contract, side=trend, signal_bar=bar, setup_label=label, entry_price=entry,
                    stop_price=self.cs.round_to_tick(entry - sign * sl_pts),
                    target_price=self.cs.round_to_tick(entry + sign * tp_pts), risk_points=sl_pts,
                    risk_ticks=self.cs.price_diff_to_ticks(sl_pts), target_r=p.target_r, time_exit_bars=0,
                    pullback_start_ts=pb, criteria=tuple(crit),
                    context={"regime": snap.regime.as_dict(), "bar": m.as_dict(), "legs": {"count": legs.count}},
                    order_type=otype, lot_role=role,
                    stop_points=sl_pts if fixed else None, target_points=tp_pts if fixed else None)
            out.append(SetupEvaluation(STRATEGY_ID, trend, label, bar, "candidate", tuple(crit), passed,
                                       signal.pullback_start_ts if signal else legs.pullback_start_ts, signal))
        return out

    def _momentum(self, snap: PriceActionSnapshot, hist: list, d: int):
        """Continuidade: 3 candles seguidos na mesma direção, todos expressivos, em tendência ou rompimento."""
        p, bar, m = self.params, snap.bar, snap.metrics
        if not p.enable_momentum or d == 0 or self._run_len != p.consecutive_bars or len(hist) < p.consecutive_bars - 1:
            return None, {}
        if not p.momentum_require_expressive:  # regra v1.0: só 3 candles a favor da tendência
            regime = snap.regime.regime
            ok = (regime is Regime.BULL_TREND and d > 0) or (regime is Regime.BEAR_TREND and d < 0)
            return ((Side.LONG if d > 0 else Side.SHORT), {"expressive": False, "context": "tendência"}) if ok else (None, {})
        seq = [(b, a) for b, a in hist[-(p.consecutive_bars - 1):]] + [(bar, m.atr)]
        vols = [b.volume for b, _ in hist[-20:] if b.volume]
        vavg = sum(vols) / len(vols) if len(vols) >= 10 else None

        def expressive(b, atr):
            size_ok = atr is not None and (b.high - b.low) >= p.momentum_min_range_atr * atr
            vol_ok = vavg is not None and b.volume is not None and b.volume >= p.momentum_min_volume_mult * vavg
            return size_ok or vol_ok

        if not all(expressive(b, a) for b, a in seq):
            return None, {}
        regime = snap.regime.regime
        side = Side.LONG if d > 0 else Side.SHORT
        trend_ok = (regime is Regime.BULL_TREND and d > 0) or (regime is Regime.BEAR_TREND and d < 0)
        ref = [b for b, _ in hist[-p.momentum_breakout_lookback - (p.consecutive_bars - 1):-(p.consecutive_bars - 1) or None]]
        breakout = len(ref) >= p.momentum_breakout_lookback and (
            bar.close > max(b.high for b in ref) if d > 0 else bar.close < min(b.low for b in ref))
        if not (trend_ok or breakout):
            return None, {}
        return side, {"expressive": True, "context": "tendência" if trend_ok else "rompimento"}
