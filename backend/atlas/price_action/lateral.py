"""Filtro de lateralidade para a contagem de pernas H/L (regra definida pelo usuário).

* Lateralidade: ``min_bars`` (3) ou mais barras consecutivas sobrepostas, isto é, com uma faixa de preço
  comum a todas elas (maior mínima < menor máxima).
* Durante a lateralidade NÃO há contagem de H1/H2/L1/L2: a contagem fica zerada.
* Faixa da lateralidade: maior máxima e menor mínima das barras laterais (cresce enquanto não houver rompimento).
* Rompimento: barra que FECHA acima da máxima (alta) ou abaixo da mínima (baixa) da faixa.
* Confirmação: a barra seguinte (follow-through) fecha além do fechamento da barra de rompimento, na mesma
  direção, e fora da faixa. Sem continuidade o rompimento falha e a lateralidade continua (faixa ampliada).
* Após a confirmação, a contagem recomeça do zero a partir da barra de follow-through.

Incremental, sem look-ahead: cada barra é avaliada no seu fechamento, usando só barras anteriores.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from atlas.core.types import Bar


@dataclass(frozen=True)
class LateralSnapshot:
    state: str  # normal | lateral | breakout_pending
    range_high: float | None
    range_low: float | None
    bars_in_range: int
    breakout_dir: int  # +1 alta, -1 baixa, 0 nenhum (rompimento pendente)
    event: str | None  # "lateral_start" | "breakout" | "breakout_failed" | "breakout_confirmed"
    counting_allowed: bool

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class LateralGate:
    min_bars: int = 3
    min_overlap: float = 0.0  # faixa comum mínima, em fração da amplitude média das barras (0 = qualquer sobreposição)
    window: deque = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.window = deque(maxlen=self.min_bars)
        self.state = "normal"
        self.hi: float | None = None
        self.lo: float | None = None
        self.n = 0
        self.bo_dir = 0
        self.bo_close: float | None = None
        self.bo_bar: Bar | None = None

    def _snap(self, event: str | None) -> LateralSnapshot:
        return LateralSnapshot(self.state, self.hi, self.lo, self.n, self.bo_dir, event, self.state == "normal")

    def update(self, bar: Bar) -> LateralSnapshot:
        event = None
        if self.state == "normal":
            self.window.append(bar)
            if len(self.window) == self.min_bars:
                max_low = max(b.low for b in self.window)
                min_high = min(b.high for b in self.window)
                avg_rng = sum(b.high - b.low for b in self.window) / self.min_bars
                if min_high - max_low > self.min_overlap * avg_rng and max_low < min_high:  # faixa comum → sobrepostas
                    self.state = "lateral"
                    self.hi = max(b.high for b in self.window)
                    self.lo = min(b.low for b in self.window)
                    self.n = self.min_bars
                    event = "lateral_start"
            return self._snap(event)

        if self.state == "lateral":
            if bar.close > self.hi or bar.close < self.lo:
                self.state = "breakout_pending"
                self.bo_dir = 1 if bar.close > self.hi else -1
                self.bo_close = bar.close
                self.bo_bar = bar
                return self._snap("breakout")
            self.hi, self.lo = max(self.hi, bar.high), min(self.lo, bar.low)
            self.n += 1
            return self._snap(None)

        # breakout_pending: esta barra decide se há follow-through
        d = self.bo_dir
        confirmed = (bar.close > self.bo_close and bar.close > self.hi) if d > 0 else \
                    (bar.close < self.bo_close and bar.close < self.lo)
        if confirmed:
            self.state = "normal"
            self.window = deque([bar], maxlen=self.min_bars)
            self.hi = self.lo = None
            self.n = 0
            self.bo_dir = d
            return self._snap("breakout_confirmed")
        # falhou: rompimento na direção oposta nesta barra?
        hi = max(self.hi, self.bo_bar.high)
        lo = min(self.lo, self.bo_bar.low)
        if bar.close > hi or bar.close < lo:
            self.hi, self.lo = hi, lo
            self.n += 1
            self.bo_dir = 1 if bar.close > hi else -1
            self.bo_close = bar.close
            self.bo_bar = bar
            return self._snap("breakout")
        self.state = "lateral"
        self.hi, self.lo = max(hi, bar.high), min(lo, bar.low)
        self.n += 2
        self.bo_dir = 0
        return self._snap("breakout_failed")
