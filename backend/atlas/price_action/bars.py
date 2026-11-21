"""Métricas e classificação de barras (ver STRATEGIES.md §1–2)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from atlas.core.types import Bar


class BarClass(StrEnum):
    BULL_TREND = "bull_trend_bar"
    BEAR_TREND = "bear_trend_bar"
    DOJI = "doji"
    WEAK_BULL = "weak_bull"
    WEAK_BEAR = "weak_bear"


@dataclass(frozen=True)
class BarParams:
    doji_max_body: float = 0.30
    trend_min_body: float = 0.50
    strong_close: float = 0.75  # close_pos >= → fechamento na máxima; <= 1-x → na mínima
    relevant_tail: float = 0.33


@dataclass(frozen=True)
class BarMetrics:
    range: float
    body: float
    body_ratio: float
    upper_tail: float
    lower_tail: float
    close_pos: float
    true_range: float
    atr: float | None  # ATR das barras ANTERIORES
    rel_range: float | None
    overlap_prev: float | None
    bar_class: BarClass
    close_near_high: bool
    close_near_low: bool
    inside: bool
    outside: bool
    big_upper_tail: bool
    big_lower_tail: bool

    def as_dict(self) -> dict:
        return {k: (v.value if isinstance(v, StrEnum) else v) for k, v in self.__dict__.items()}


def classify(o: float, h: float, l: float, c: float, p: BarParams = BarParams()) -> BarClass:
    rng = h - l
    body_ratio = abs(c - o) / rng if rng > 0 else 0.0
    if rng <= 0 or body_ratio <= p.doji_max_body:
        return BarClass.DOJI
    if c > o:
        return BarClass.BULL_TREND if body_ratio >= p.trend_min_body else BarClass.WEAK_BULL
    if c < o:
        return BarClass.BEAR_TREND if body_ratio >= p.trend_min_body else BarClass.WEAK_BEAR
    return BarClass.DOJI


def compute_metrics(bar: Bar, prev: Bar | None, atr_prev: float | None, p: BarParams = BarParams()) -> BarMetrics:
    o, h, l, c = bar.open, bar.high, bar.low, bar.close
    rng = h - l
    body = abs(c - o)
    if rng > 0:
        body_ratio = body / rng
        upper = (h - max(o, c)) / rng
        lower = (min(o, c) - l) / rng
        close_pos = (c - l) / rng
    else:
        body_ratio = upper = lower = 0.0
        close_pos = 0.5
    if prev is not None:
        tr = max(h, prev.close) - min(l, prev.close)
        ov = max(0.0, min(h, prev.high) - max(l, prev.low))
        overlap = ov / rng if rng > 0 else 1.0
        inside = h <= prev.high and l >= prev.low
        outside = h > prev.high and l < prev.low
    else:
        tr, overlap, inside, outside = rng, None, False, False
    rel = (rng / atr_prev) if (atr_prev and atr_prev > 0) else None
    return BarMetrics(
        range=rng, body=body, body_ratio=body_ratio, upper_tail=upper, lower_tail=lower, close_pos=close_pos,
        true_range=tr, atr=atr_prev, rel_range=rel, overlap_prev=overlap, bar_class=classify(o, h, l, c, p),
        close_near_high=close_pos >= p.strong_close, close_near_low=close_pos <= 1 - p.strong_close,
        inside=inside, outside=outside, big_upper_tail=upper >= p.relevant_tail, big_lower_tail=lower >= p.relevant_tail,
    )
