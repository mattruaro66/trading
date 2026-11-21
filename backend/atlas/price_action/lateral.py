"""Tight trading range (lateralidade) — suspende a contagem de H/L até um rompimento confirmado.

Fontes (Brooks Trading Course, glossário oficial: https://www.brookstradingcourse.com/price-action-trading-terms-glossary/):
* "tight trading range": barras com muita sobreposição, em que a maioria das reversões é pequena demais para
  ser operada com lucro usando ordens stop; touros e ursos em equilíbrio.
* "barbwire": tipo de tight trading range; iniciantes devem parar de operar até um rompimento claro.
* "breakout bar": barra que cria o rompimento, normalmente uma trend bar forte; o rompimento é mais forte quando
  o fechamento fica além do ponto rompido.
* "follow-through": barra(s) seguinte(s) que estendem o movimento; após um rompimento de alta os touros querem
  que a barra seguinte tenha corpo de alta.
* Exemplo do livro "Trading Price Action Trading Ranges": com range diário médio de 10–15 pontos no Emini, um
  range de até ~3 pontos é provavelmente tight (≈ 20–30% do range diário) — usado como limite de altura.

Regra operacional do ATLAS (definição do usuário + Brooks):
1. Barra lateral: fechamento dentro da barra anterior (mínima anterior ≤ fechamento ≤ máxima anterior).
2. Tight trading range: ``min_bars`` (3) ou mais barras laterais consecutivas cuja faixa total (máx.–mín., incluindo
   a barra de referência anterior) não passa de ``max_height_adr`` (25%) do range diário médio dos pregões anteriores.
   Sem histórico de pregões, o limite de altura não é aplicado.
3. Durante a lateralidade não há contagem de H/L. Se a faixa crescer além do limite, deixa de ser tight (vira um
   trading range comum) e a contagem recomeça do zero.
4. Rompimento: barra que FECHA além da faixa com corpo na direção do rompimento.
5. Follow-through: a barra seguinte tem corpo na mesma direção e fecha fora da faixa → rompimento confirmado;
   a contagem recomeça do zero. Sem follow-through o rompimento falha e a lateralidade continua (faixa ampliada).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from atlas.core.types import Bar


@dataclass(frozen=True)
class LateralSnapshot:
    state: str  # normal | lateral | breakout_pending
    range_high: float | None
    range_low: float | None
    bars_in_range: int
    breakout_dir: int  # +1 alta, -1 baixa, 0 nenhum
    event: str | None  # lateral_start | breakout | breakout_failed | breakout_confirmed | range_expanded
    counting_allowed: bool
    inside_run: int = 0
    adr: float | None = None  # range diário médio usado no limite de altura

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class LateralGate:
    def __init__(self, min_bars: int = 3, max_height_adr: float | None = 0.25, adr_sessions: int = 5):
        self.min_bars = min_bars
        self.max_height_adr = max_height_adr
        self.day_ranges: deque[float] = deque(maxlen=adr_sessions)
        self._day_hi: float | None = None
        self._day_lo: float | None = None
        self.reset()

    # ------------------------------------------------------------------ estado
    def reset(self) -> None:
        self.state = "normal"
        self.prev: Bar | None = None
        self.run: list[Bar] = []
        self.ref: Bar | None = None
        self.hi: float | None = None
        self.lo: float | None = None
        self.n = 0
        self.bo_dir = 0
        self.bo_bar: Bar | None = None

    def new_session(self) -> None:
        """Fecha o pregão anterior (alimenta o range diário médio) e reinicia o estado da lateralidade."""
        if self._day_hi is not None:
            self.day_ranges.append(self._day_hi - self._day_lo)
        self._day_hi = self._day_lo = None
        self.reset()

    @property
    def adr(self) -> float | None:
        return sum(self.day_ranges) / len(self.day_ranges) if self.day_ranges else None

    def _tight(self, hi: float, lo: float) -> bool:
        if self.max_height_adr is None or self.adr is None:
            return True
        return (hi - lo) <= self.max_height_adr * self.adr

    @staticmethod
    def is_lateral_bar(bar: Bar, prev: Bar) -> bool:
        return prev.low <= bar.close <= prev.high

    def _snap(self, event: str | None) -> LateralSnapshot:
        return LateralSnapshot(self.state, self.hi, self.lo, self.n, self.bo_dir, event, self.state == "normal",
                               len(self.run), self.adr)

    def _to_normal(self) -> None:
        self.state = "normal"
        self.run = []
        self.hi = self.lo = None
        self.n = 0
        self.bo_dir = 0
        self.bo_bar = None

    # ------------------------------------------------------------------ candle
    def update(self, bar: Bar) -> LateralSnapshot:
        self._day_hi = bar.high if self._day_hi is None else max(self._day_hi, bar.high)
        self._day_lo = bar.low if self._day_lo is None else min(self._day_lo, bar.low)
        prev, self.prev = self.prev, bar

        if self.state == "normal":
            if prev is not None and self.is_lateral_bar(bar, prev):
                if not self.run:
                    self.ref = prev
                self.run.append(bar)
                if len(self.run) >= self.min_bars:
                    group = [self.ref] + self.run
                    hi, lo = max(b.high for b in group), min(b.low for b in group)
                    if self._tight(hi, lo):
                        self.state, self.hi, self.lo, self.n = "lateral", hi, lo, len(self.run)
                        self.run = []
                        return self._snap("lateral_start")
            else:
                self.run = []
            return self._snap(None)

        if self.state == "lateral":
            up = bar.close > self.hi and bar.close > bar.open
            down = bar.close < self.lo and bar.close < bar.open
            if up or down:
                self.state, self.bo_dir, self.bo_bar = "breakout_pending", (1 if up else -1), bar
                return self._snap("breakout")
            self.hi, self.lo = max(self.hi, bar.high), min(self.lo, bar.low)
            self.n += 1
            if not self._tight(self.hi, self.lo):
                self._to_normal()
                return self._snap("range_expanded")
            return self._snap(None)

        # breakout_pending: esta barra é o possível follow-through
        d = self.bo_dir
        follow = (bar.close > bar.open and bar.close > self.hi) if d > 0 else (bar.close < bar.open and bar.close < self.lo)
        if follow:
            self._to_normal()
            self.bo_dir = d
            return self._snap("breakout_confirmed")
        hi, lo = max(self.hi, self.bo_bar.high), min(self.lo, self.bo_bar.low)
        up = bar.close > hi and bar.close > bar.open
        down = bar.close < lo and bar.close < bar.open
        self.hi, self.lo = hi, lo
        if up or down:  # novo rompimento (inclusive na direção oposta)
            self.n += 1
            self.bo_dir, self.bo_bar = (1 if up else -1), bar
            return self._snap("breakout")
        self.state = "lateral"
        self.hi, self.lo = max(hi, bar.high), min(lo, bar.low)
        self.n += 2
        self.bo_dir = 0
        if not self._tight(self.hi, self.lo):
            self._to_normal()
            return self._snap("range_expanded")
        return self._snap("breakout_failed")
