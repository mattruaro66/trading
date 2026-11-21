"""Estratégia C — Método Brooks: conceitos do glossário do Brooks Trading Course transformados em regras de execução.

Cada setup abaixo é uma tradução objetiva (e portanto aproximada) de um conceito; a fonte de cada um está em
BROOKS_RULES.md. MODELO EXPERIMENTAL para validar o método em simulação — não executa ordens reais.

Setups (signal bar = barra fechada t; ordem stop 1 tick além dela, válida no candle t+1, salvo indicação):
 S1  H2/L2 a favor da tendência                         (second entry)
 S2  H1/L1 em tendência forte (após spike/micro canal)   (strong trend, small pullback)
 S3  H3/L3 cunha a favor (wedge flag, 1º sinal)          S4  H4/L4 a favor
 S5  H1/H2 no fundo / L1/L2 no topo do trading range    (buy low, sell high)
 S6  Fundo duplo bull flag / topo duplo bear flag
 S7  Micro fundo duplo / micro topo duplo (ledge)
 S8  Breakout pullback (H1/L1 até 5 barras após rompimento confirmado)
 S9  Falha da falha (rompimento falha e retoma em até 3 barras)
 S10 Rompimento falho no extremo do trading range (fade; inclui five-tick failure)
 S11 1ª barra de gap da média móvel em tendência forte
 S12 20+ barras sem tocar a média → 1º toque
 S13 Cunha de reversão — 2º sinal (L2 após três impulsos de alta; espelho)
 S14 Reversão maior (MTR): rompe a média com força e testa o extremo
 S15 ii / ioi (breakout mode) a favor do always in
 S16 Reversão do gap de abertura
 S17 Tendência desde a abertura (compra/venda no fechamento a mercado)
 S18 Spike — compra/venda no fechamento (buy/sell the close) a mercado
 S19 2ª perna após barra-surpresa
 S20 H1/L1 após micro canal

Filtros de contexto (Brooks): sem entradas em tight trading range; setups a favor exigem tendência e always in
alinhados; contra a tendência só os setups de reversão/range (S10, S13, S14, S16); nada contra micro canal ativo;
após clímax (barra ≥ 3 ATR em tendência madura) sem novas entradas a favor por 10 barras; equação do trader:
risco entre 2 ticks e 2,5 ATR.

Gestão (Brooks): stop 1 tick além da barra de sinal; 2 lotes — scalp em 1R e swing em 2R (ou medida do
movimento, se maior); ao atingir o alvo do scalp, o stop do swing vai para o zero a zero; posição zerada no fim
do pregão. Cada sinal é executado (sem limite de posição); um sinal por direção por candle.
"""
from __future__ import annotations

from collections import deque

from pydantic import BaseModel, Field

from atlas.contracts.specs import get_spec
from atlas.core.types import Side, Symbol
from atlas.price_action.bars import BarClass
from atlas.price_action.engine import PriceActionSnapshot
from atlas.price_action.regime import Regime
from atlas.strategy.base import Criterion, SetupEvaluation, Signal, StrategySpec, params_hash

STRATEGY_ID = "brooks_method"
STRATEGY_VERSION = "1.0.0"

SETUP_NAMES = {
    "S1": "H2/L2 tendência", "S2": "H1/L1 tendência forte", "S3": "H3/L3 cunha", "S4": "H4/L4",
    "S5": "Range: fundo/topo", "S6": "Fundo/topo duplo flag", "S7": "Micro fundo/topo duplo",
    "S8": "Breakout pullback", "S9": "Falha da falha", "S10": "Rompimento falho (range)",
    "S11": "Gap bar da média", "S12": "20 barras sem tocar a média", "S13": "Cunha reversão 2º sinal",
    "S14": "Reversão maior (MTR)", "S15": "ii / ioi", "S16": "Reversão do gap", "S17": "Tendência desde a abertura",
    "S18": "Spike (no fechamento)", "S19": "2ª perna pós-surpresa", "S20": "H1/L1 pós micro canal",
}
REVERSAL_SETUPS = {"S10", "S13", "S14", "S16"}

SPEC = StrategySpec(
    strategy_id=STRATEGY_ID, version=STRATEGY_VERSION, name="Método Brooks (20 setups)",
    description="Conceitos do glossário do Brooks Trading Course transformados em regras de execução (experimental).",
    context="Tendência/always in para setups a favor; trading range para buy low/sell high; reversões com 2º sinal.",
    pattern="20 setups (ver docstring do módulo e BROOKS_RULES.md).",
    entry="Stop 1 tick além da barra de sinal (válida 1 candle); spike/tendência desde a abertura a mercado.",
    stop="1 tick além da barra de sinal (ou do spike).", target="Scalp 1R + swing 2R ou medida do movimento.",
    horizon="Intradiário; zera no fim do pregão.",
    references=("https://www.brookstradingcourse.com/price-action-trading-terms-glossary/",),
)


class BrooksMethodParams(BaseModel):
    profile: str = "brooks"
    scalp_r: float = Field(1.0, gt=0)
    swing_r: float = Field(2.0, gt=0)
    max_risk_atr: float = Field(2.5, gt=0)
    min_risk_ticks: int = Field(2, ge=1)
    range_lookback: int = Field(40, ge=10)
    flatten_minutes_before_close: int = Field(5, ge=0, le=60)
    enabled_setups: list[str] | None = None  # None = todos
    time_exit_bars: int = 0

    @property
    def target_r(self) -> float:
        return self.swing_r


class BrooksMethodStrategy:
    spec = SPEC
    needs_position = True

    def __init__(self, symbol: Symbol | str, params: BrooksMethodParams | None = None):
        self.symbol = Symbol(symbol)
        self.params = params or BrooksMethodParams()
        self.cs = get_spec(self.symbol)
        self.params_hash = params_hash({**self.params.model_dump(mode="json"), "symbol": self.symbol.value})
        self.reset()

    def resolved_params(self) -> dict:
        return self.params.model_dump(mode="json")

    def reset(self) -> None:
        self.h: deque = deque(maxlen=80)  # snapshots recentes (contíguos)
        self.i = 0
        self.swing_highs: deque = deque(maxlen=10)  # (índice, preço)
        self.swing_lows: deque = deque(maxlen=10)
        self.trend_dir_prev = 0
        self.trend_age = 0
        self.extreme = None  # extremo da tendência atual
        self.gap_used = {1: False, -1: False}
        self.no_touch = {1: 0, -1: 0}
        self.climax_until = {1: -1, -1: -1}
        self.last_breakout = None  # (dir, índice, altura da faixa)
        self.last_failed = None  # (dir, índice)
        self.surprise = {1: -99, -1: -99}
        self.micro = {1: 0, -1: 0}  # barras consecutivas sem pullback bar
        self.micro_end = {1: -99, -1: -99}
        self.mtr_watch = {1: None, -1: None}  # dir da reversão → (extremo, índice)
        self.session_bar = 0
        self.prev_session_last = None

    # ------------------------------------------------------------------ utilitários
    def _strong(self, snap, d: int) -> bool:
        m = snap.metrics
        if d > 0:
            return m.bar_class is BarClass.BULL_TREND and m.close_pos >= 0.7
        return m.bar_class is BarClass.BEAR_TREND and m.close_pos <= 0.3

    @staticmethod
    def _good_signal(snap, d: int, strict: bool = False) -> bool:
        m = snap.metrics
        cp = m.close_pos if d > 0 else 1 - m.close_pos
        opposite = m.bar_class is (BarClass.BEAR_TREND if d > 0 else BarClass.BULL_TREND)
        return cp >= (0.6 if strict else 0.5) and not opposite and (not strict or (snap.bar.close - snap.bar.open) * d > 0)

    # ------------------------------------------------------------------ estado por candle
    def _update_state(self, snap: PriceActionSnapshot) -> None:
        if snap.reset_reason:
            self.reset()
        if snap.session_start:
            self.prev_session_last = self.h[-1].bar if self.h else None
            self.session_bar = 0
            self.last_breakout = self.last_failed = None
            self.micro = {1: 0, -1: 0}
        self.session_bar += 1
        self.i += 1
        bar, m, rg = snap.bar, snap.metrics, snap.regime
        prev = self.h[-1] if self.h else None
        # swings (fractal de 3 barras confirmado nesta barra, sem olhar adiante)
        if len(self.h) >= 2:
            a, b = self.h[-2].bar, self.h[-1].bar
            if b.high >= a.high and b.high >= bar.high:
                self.swing_highs.append((self.i - 1, b.high))
            if b.low <= a.low and b.low <= bar.low:
                self.swing_lows.append((self.i - 1, b.low))
        # tendência e idade
        td = 1 if rg.regime is Regime.BULL_TREND else -1 if rg.regime is Regime.BEAR_TREND else 0
        if td != self.trend_dir_prev:
            self.trend_age = 0
            self.gap_used = {1: False, -1: False}
            self.extreme = None
        self.trend_age += 1 if td else 0
        if td:
            self.extreme = (bar.high if td > 0 else bar.low) if self.extreme is None else \
                (max(self.extreme, bar.high) if td > 0 else min(self.extreme, bar.low))
        self.trend_dir_prev = td
        # clímax: barra enorme numa tendência madura
        if td and m.atr and m.range >= 3 * m.atr and self.trend_age >= 20 and (bar.close - bar.open) * td > 0:
            self.climax_until[td] = self.i + 10
        # média móvel: barras sem tocar
        ema = rg.ema
        if ema is not None:
            self.no_touch[1] = self.no_touch[1] + 1 if bar.low > ema else 0
            self.no_touch[-1] = self.no_touch[-1] + 1 if bar.high < ema else 0
        # micro canal (sem pullback bar)
        if prev is not None:
            for d in (1, -1):
                no_pb = (bar.low >= prev.bar.low) if d > 0 else (bar.high <= prev.bar.high)
                if no_pb:
                    self.micro[d] += 1
                else:
                    if self.micro[d] >= 5:
                        self.micro_end[d] = self.i
                    self.micro[d] = 0
        # barra-surpresa
        if m.atr and m.range >= 2 * m.atr:
            for d in (1, -1):
                if self._strong(snap, d):
                    self.surprise[d] = self.i
        # rompimentos do tight trading range
        lat = snap.lateral
        if lat is not None and lat.event == "breakout_confirmed":
            height = 0.0
            for s in reversed(self.h):
                if s.lateral is not None and s.lateral.range_high is not None:
                    height = s.lateral.range_high - s.lateral.range_low
                    break
            self.last_breakout = (lat.breakout_dir, self.i, height)
        if lat is not None and lat.event == "breakout_failed" and prev is not None and prev.lateral is not None:
            self.last_failed = (prev.lateral.breakout_dir, self.i)
        # MTR: rompimento forte da média contra a tendência
        if ema is not None and td and self.extreme is not None:
            if td > 0 and bar.close < ema and self._strong(snap, -1):
                self.mtr_watch[-1] = (self.extreme, self.i)
            if td < 0 and bar.close > ema and self._strong(snap, 1):
                self.mtr_watch[1] = (self.extreme, self.i)

    # ------------------------------------------------------------------ setups
    def _candidates(self, snap: PriceActionSnapshot) -> list[tuple[str, int, str, float | None]]:
        """Retorna (setup, direção, tipo de ordem, medida do movimento opcional)."""
        bar, m, rg = snap.bar, snap.metrics, snap.regime
        out = []
        td = self.trend_dir_prev
        ai = {"LONG": 1, "SHORT": -1}.get(rg.always_in.value, 0)
        prev = self.h[-1] if self.h else None
        prev2 = self.h[-2] if len(self.h) >= 2 else None
        atr = m.atr
        ema = rg.ema
        for d in (1, -1):
            legs = snap.bull_legs if d > 0 else snap.bear_legs
            cand = legs.candidate_for
            with_trend = td == d and ai != -d
            if with_trend:
                if cand == 2 and self._good_signal(snap, d):
                    out.append(("S1", d, "stop", None))
                if cand == 1 and self._good_signal(snap, d) and (self.i - self.surprise[d] <= 10 or rg.frac_above_ema is not None and
                                                                  ((rg.frac_above_ema >= 0.9) if d > 0 else (rg.frac_above_ema <= 0.1))):
                    out.append(("S2", d, "stop", None))
                if cand == 3 and self._good_signal(snap, d):
                    out.append(("S3", d, "stop", None))
                if cand == 4 and self._good_signal(snap, d):
                    out.append(("S4", d, "stop", None))
                # fundo duplo bull flag / topo duplo bear flag
                if atr and self._good_signal(snap, d, strict=True):
                    pts = self.swing_lows if d > 0 else self.swing_highs
                    ext = bar.low if d > 0 else bar.high
                    if any(3 <= self.i - k <= 20 and abs(ext - p) <= 0.15 * atr for k, p in pts):
                        out.append(("S6", d, "stop", None))
                # micro fundo/topo duplo
                if prev is not None and self._good_signal(snap, d, strict=True):
                    e1, e0 = (bar.low, prev.bar.low) if d > 0 else (bar.high, prev.bar.high)
                    if abs(e1 - e0) <= self.cs.tick_size:
                        out.append(("S7", d, "stop", None))
                # 1ª barra de gap da média
                if ema is not None and not self.gap_used[d] and self.trend_age >= 10 and \
                        ((bar.high < ema) if d > 0 else (bar.low > ema)):
                    self.gap_used[d] = True
                    out.append(("S11", d, "stop", None))
                # 20 barras sem tocar a média → 1º toque
                if ema is not None and prev is not None and self.no_touch[d] == 0 and \
                        self._prev_no_touch(d) >= 20 and self._good_signal(snap, d):
                    out.append(("S12", d, "stop", None))
                # ii / ioi
                if prev is not None and prev2 is not None:
                    ii = m.inside and prev.metrics.inside
                    ioi = m.inside and prev.metrics.outside and prev2.metrics.inside
                    if (ii or ioi) and ai == d:
                        out.append(("S15", d, "stop", None))
                # spike: 2+ barras fortes rompendo a máxima/mínima das 10 anteriores → a mercado
                if prev is not None and self._strong(snap, d) and self._strong(prev, d) and len(self.h) >= 12:
                    ref = [s.bar for s in list(self.h)[-12:-1]]
                    brk = bar.close > max(b.high for b in ref) if d > 0 else bar.close < min(b.low for b in ref)
                    if brk:
                        out.append(("S18", d, "market", None))
                # 2ª perna após barra-surpresa
                if cand == 1 and 1 <= self.i - self.surprise[d] <= 10:
                    out.append(("S19", d, "stop", None))
                # H1/L1 após micro canal
                if cand == 1 and 1 <= self.i - self.micro_end[d] <= 3:
                    out.append(("S20", d, "stop", None))
            # breakout pullback (após rompimento confirmado do tight range)
            if self.last_breakout and self.last_breakout[0] == d and 1 <= self.i - self.last_breakout[1] <= 5 and \
                    cand == 1 and ai != -d:
                out.append(("S8", d, "stop", self.last_breakout[2]))
            # falha da falha
            if self.last_failed and self.last_failed[0] == d and 1 <= self.i - self.last_failed[1] <= 3 and \
                    self._strong(snap, d) and prev is not None and \
                    ((bar.close > prev.bar.high) if d > 0 else (bar.close < prev.bar.low)):
                out.append(("S9", d, "stop", None))
            # trading range: buy low / sell high e rompimento falho nos extremos
            if rg.regime is Regime.TRADING_RANGE and len(self.h) >= self.params.range_lookback:
                ref = [s.bar for s in list(self.h)[-self.params.range_lookback:]]
                hi, lo = max(b.high for b in ref), min(b.low for b in ref)
                third = (hi - lo) / 3
                pos_ok = (bar.close <= lo + third) if d > 0 else (bar.close >= hi - third)
                if cand in (1, 2) and pos_ok and self._good_signal(snap, d, strict=True):
                    out.append(("S5", d, "stop", None))
                # rompimento falho: excede o extremo e fecha de volta com barra de reversão
                if d < 0 and bar.high > hi and bar.close < hi and m.close_pos <= 0.4:
                    out.append(("S10", d, "stop", None))
                if d > 0 and bar.low < lo and bar.close > lo and m.close_pos >= 0.6:
                    out.append(("S10", d, "stop", None))
            # cunha de reversão: três impulsos contra d e 2º sinal (L2/H2) na direção d
            pushes = self.swing_highs if d < 0 else self.swing_lows
            if cand == 2 and len(pushes) >= 3 and td == -d:
                p3 = list(pushes)[-3:]
                rising = all((p3[k + 1][1] > p3[k][1]) if d < 0 else (p3[k + 1][1] < p3[k][1]) for k in range(2))
                if rising and self.i - p3[-1][0] <= 15 and p3[-1][0] - p3[0][0] <= 40 and self._good_signal(snap, d):
                    out.append(("S13", d, "stop", None))
            # reversão maior: após romper a média com força, teste do extremo e barra de reversão
            w = self.mtr_watch[d]
            if w is not None and atr and 3 <= self.i - w[1] <= 40:
                ext = w[0]
                test = (bar.high >= ext - 0.5 * atr) if d < 0 else (bar.low <= ext + 0.5 * atr)
                if test and self._good_signal(snap, d, strict=True):
                    out.append(("S14", d, "stop", None))
                    self.mtr_watch[d] = None
        # abertura: reversão do gap e tendência desde a abertura
        if self.session_bar == 1 and self.prev_session_last is not None:
            pl = self.prev_session_last
            if bar.open > pl.high:
                out.append(("S16", -1, "stop", None))
            elif bar.open < pl.low:
                out.append(("S16", 1, "stop", None))
        if self.session_bar == 2 and prev is not None:
            for d in (1, -1):
                if self._strong(snap, d) and self._strong(prev, d):
                    out.append(("S17", d, "market", None))
        return out

    def _prev_no_touch(self, d: int) -> int:
        # barras consecutivas sem tocar a média até a barra anterior (estado já atualizado nesta barra)
        n = 0
        for s in reversed(list(self.h)):
            ema = s.regime.ema
            if ema is None:
                break
            if (s.bar.low > ema) if d > 0 else (s.bar.high < ema):
                n += 1
            else:
                break
        return n

    # ------------------------------------------------------------------ avaliação
    def on_snapshot(self, snap: PriceActionSnapshot, sim=None) -> list[SetupEvaluation]:
        self._update_state(snap)
        cands = self._candidates(snap) if snap.data_ok else []
        self.h.append(snap)  # a partir daqui self.h[-1] é a barra atual
        p = self.params
        if p.enabled_setups is not None:
            cands = [c for c in cands if c[0] in p.enabled_setups]
        out: list[SetupEvaluation] = []
        tight = snap.lateral is not None and not snap.lateral.counting_allowed
        bar, m = snap.bar, snap.metrics
        done = set()
        for setup, d, otype, mm in cands:
            side = Side.LONG if d > 0 else Side.SHORT
            # filtros de contexto (Brooks)
            micro_against = self.micro[-d] >= 5
            climax = self.i <= self.climax_until[d] and setup not in REVERSAL_SETUPS
            tick = self.cs.tick_size
            if otype == "market":
                entry = bar.close
                pb = self.h[-2].bar if len(self.h) >= 2 else bar  # barra anterior (h já inclui a atual)
                lo_ref = min(bar.low, pb.low) if d > 0 else max(bar.high, pb.high)
                stop = self.cs.round_to_tick(lo_ref - d * tick)
            else:
                entry = self.cs.round_to_tick(bar.high + tick if d > 0 else bar.low - tick)
                stop = self.cs.round_to_tick(bar.low - tick if d > 0 else bar.high + tick)
            risk = abs(entry - stop)
            risk_ok = risk >= p.min_risk_ticks * tick and (m.atr is None or risk <= p.max_risk_atr * m.atr)
            crit = (
                Criterion("B1", "Fora de tight trading range", not tight, not tight, True),
                Criterion("B2", "Não opera contra micro canal ativo", not micro_against, self.micro[-d], "< 5"),
                Criterion("B3", "Sem clímax recente a favor (10 barras)", not climax, climax, False),
                Criterion("B4", "Equação do trader: risco entre 2 ticks e 2,5 ATR", risk_ok,
                          round(risk / m.atr, 2) if m.atr else None, p.max_risk_atr),
                Criterion("B5", "Dados válidos", snap.data_ok, snap.data_ok, True),
                Criterion(setup, SETUP_NAMES[setup], True, setup, setup),
            )
            passed = all(c.passed for c in crit) and d not in done
            signal = None
            if passed:
                done.add(d)
                t1 = entry + d * p.scalp_r * risk
                t2 = entry + d * max(p.swing_r * risk, mm or 0.0)
                signal = Signal(
                    strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, params_hash=self.params_hash,
                    profile=p.profile, symbol=self.symbol, contract=bar.contract, side=side, signal_bar=bar,
                    setup_label=setup, entry_price=entry, stop_price=stop,
                    target_price=self.cs.round_to_tick(t1, "down" if d > 0 else "up"), risk_points=risk,
                    risk_ticks=self.cs.price_diff_to_ticks(risk), target_r=p.swing_r, time_exit_bars=0,
                    pullback_start_ts=bar.ts_open, criteria=crit,
                    context={"regime": snap.regime.as_dict(), "bar": m.as_dict(), "setup_name": SETUP_NAMES[setup]},
                    order_type=otype, lot_role="entry",
                    extra_targets=(self.cs.round_to_tick(t2, "down" if d > 0 else "up"),), breakeven_after_first=True)
            out.append(SetupEvaluation(STRATEGY_ID, side, setup, bar, "candidate", crit, passed, bar.ts_open, signal))
        return out
