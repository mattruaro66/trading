"""Classificação de regime (ver STRATEGIES.md §3). Incremental e sem look-ahead."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum


class Regime(StrEnum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    TRADING_RANGE = "TRADING_RANGE"
    TIGHT_TRADING_RANGE = "TIGHT_TRADING_RANGE"
    UNDEFINED = "UNDEFINED"


class AlwaysIn(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass(frozen=True)
class RegimeParams:
    ema_period: int = 20
    lookback: int = 20
    slope_bars: int = 5
    trend_frac_above: float = 0.70
    trend_min_slope_atr: float = 0.10
    trend_min_efficiency: float = 0.30
    tight_min_overlap: float = 0.60
    tight_max_efficiency: float = 0.15

    @property
    def warmup(self) -> int:
        # EMA semeada em ema_period barras + lookback barras com EMA disponível para frac_above_ema
        return self.ema_period + self.lookback - 1


@dataclass(frozen=True)
class RegimeState:
    regime: Regime
    always_in: AlwaysIn
    ema: float | None
    ema_slope_atr: float | None
    frac_above_ema: float | None
    efficiency: float | None
    overlap_mean: float | None
    buying_pressure: float | None
    selling_pressure: float | None
    bars_available: int

    def as_dict(self) -> dict:
        return {k: (v.value if isinstance(v, StrEnum) else v) for k, v in self.__dict__.items()}


@dataclass
class RegimeTracker:
    params: RegimeParams = field(default_factory=RegimeParams)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        p = self.params
        n = max(p.lookback, p.ema_period) + p.slope_bars + 2
        self._closes: deque[float] = deque(maxlen=n)
        self._emas: deque[float | None] = deque(maxlen=n)
        self._above: deque[bool] = deque(maxlen=p.lookback)
        self._overlaps: deque[float] = deque(maxlen=p.lookback)
        self._bodies: deque[tuple[float, float]] = deque(maxlen=p.lookback)  # (up_body, abs_body)
        self._ema: float | None = None
        self._seed: list[float] = []
        self.count = 0

    def update(self, o: float, c: float, overlap_prev: float | None, atr: float | None) -> RegimeState:
        p = self.params
        self.count += 1
        prev_close = self._closes[-1] if self._closes else None
        self._closes.append(c)
        # EMA semeada pela média simples das primeiras ema_period barras
        if self._ema is None:
            self._seed.append(c)
            if len(self._seed) == p.ema_period:
                self._ema = sum(self._seed) / p.ema_period
        else:
            alpha = 2.0 / (p.ema_period + 1)
            self._ema = alpha * c + (1 - alpha) * self._ema
        self._emas.append(self._ema)
        if self._ema is not None:
            self._above.append(c > self._ema)
        if overlap_prev is not None:
            self._overlaps.append(overlap_prev)
        self._bodies.append((max(c - o, 0.0), abs(c - o)))

        ema = self._ema
        slope = None
        if ema is not None and len(self._emas) > p.slope_bars and self._emas[-1 - p.slope_bars] is not None and atr:
            slope = (ema - self._emas[-1 - p.slope_bars]) / atr
        frac = (sum(self._above) / len(self._above)) if len(self._above) == p.lookback else None
        eff = None
        if len(self._closes) > p.lookback:
            closes = list(self._closes)[-(p.lookback + 1):]
            path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
            eff = abs(closes[-1] - closes[0]) / path if path > 0 else 0.0
        ovl = (sum(self._overlaps) / len(self._overlaps)) if len(self._overlaps) == p.lookback else None
        tot = sum(b[1] for b in self._bodies)
        bp = (sum(b[0] for b in self._bodies) / tot) if tot > 0 and len(self._bodies) == p.lookback else None
        sp = (1 - bp) if bp is not None else None

        regime = Regime.UNDEFINED
        ready = self.count >= p.warmup and None not in (slope, frac, eff, ovl)
        if ready:
            if frac >= p.trend_frac_above and slope >= p.trend_min_slope_atr and eff >= p.trend_min_efficiency:
                regime = Regime.BULL_TREND
            elif frac <= 1 - p.trend_frac_above and slope <= -p.trend_min_slope_atr and eff >= p.trend_min_efficiency:
                regime = Regime.BEAR_TREND
            elif ovl >= p.tight_min_overlap and eff < p.tight_max_efficiency:
                regime = Regime.TIGHT_TRADING_RANGE
            else:
                regime = Regime.TRADING_RANGE
        ai = AlwaysIn.NONE
        if ema is not None and slope is not None:
            if c > ema and slope > 0:
                ai = AlwaysIn.LONG
            elif c < ema and slope < 0:
                ai = AlwaysIn.SHORT
        _ = prev_close
        return RegimeState(regime, ai, ema, slope, frac, eff, ovl, bp, sp, self.count)
