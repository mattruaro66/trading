"""Gerador de candles SINTÉTICOS — exclusivamente para testes e demonstração do software.

Todo candle gerado tem ``synthetic=True`` e fonte ``SYNTHETIC:...``. Esses dados
não representam o mercado e não devem ser usados para conclusões sobre WIN/WDO.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.contracts.specs import get_spec
from atlas.core.types import Bar, Symbol

_BASE = {Symbol.WIN: 120000.0, Symbol.WDO: 5500.0, Symbol.EURUSD: 1.08, Symbol.USDJPY: 150.0}
_VOL = {Symbol.WIN: 0.0012, Symbol.WDO: 0.0007, Symbol.EURUSD: 0.0004, Symbol.USDJPY: 0.0005}  # desvio por candle (fração do preço), arbitrário


def generate_bars(symbol: Symbol | str, contract: str, start: date, sessions: int, seed: int = 7,
                  calendar: TradingCalendar | None = None, base_price: float | None = None) -> list[Bar]:
    sym = Symbol(symbol)
    cal = calendar or load_calendar()
    spec = get_spec(sym)
    rng = np.random.default_rng(seed)
    price = base_price or _BASE[sym]
    vol = _VOL[sym]
    drift = 0.0
    bars: list[Bar] = []
    d = start
    produced = 0
    while produced < sessions:
        s = cal.session(sym, d)
        d += timedelta(days=1)
        if s is None:
            continue
        produced += 1
        for ts in s.bucket_opens():
            if rng.random() < 0.04:  # troca de regime: tendência de alta/baixa ou lateral
                drift = rng.choice([-1.0, 0.0, 0.0, 1.0]) * vol * rng.uniform(0.15, 0.45)
            o = price
            path = o * (1 + np.cumsum(rng.normal(drift / 4, vol / 2, size=4)))
            c = float(path[-1])
            h = max(o, c, float(path.max())) + abs(rng.normal(0, vol / 3)) * o
            l = min(o, c, float(path.min())) - abs(rng.normal(0, vol / 3)) * o
            o_, h_, l_, c_ = (spec.round_to_tick(x) for x in (o, h, l, c))
            h_ = max(h_, o_, c_)
            l_ = min(l_, o_, c_)
            bars.append(Bar(sym, contract, ts, o_, h_, l_, c_, float(rng.integers(500, 5000)), None,
                            source=f"SYNTHETIC:seed={seed}", synthetic=True))
            price = c_
    return bars
