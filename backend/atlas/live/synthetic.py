"""Gerador de NEGÓCIOS SINTÉTICOS — exclusivamente para testes e demonstração do Live Market Data.

Todos os eventos têm ``synthetic=True`` e provider ``replay``.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from atlas.contracts.calendar import load_calendar
from atlas.contracts.specs import get_spec
from atlas.core.types import Symbol
from atlas.live.models import EventType, MarketEvent


def generate_trades(symbol: Symbol | str, contract: str, day: date, buckets: int, trades_per_bucket: int = 20,
                    seed: int = 3, base_price: float | None = None, latency_ms: float = 50.0) -> list[MarketEvent]:
    sym = Symbol(symbol)
    spec = get_spec(sym)
    sess = load_calendar().session(sym, day)
    if sess is None:
        raise ValueError(f"{day} não é dia de pregão")
    rng = np.random.default_rng(seed)
    price = base_price or (120000.0 if sym is Symbol.WIN else 5500.0)
    out: list[MarketEvent] = []
    seq = 0
    for b in sess.bucket_opens()[:buckets]:
        offsets = sorted(rng.uniform(0, 299.0, size=trades_per_bucket))
        for off in offsets:
            price = spec.round_to_tick(price + rng.choice([-1, 0, 1]) * spec.tick_size)
            seq += 1
            ts = b + timedelta(seconds=float(off))
            out.append(MarketEvent("replay", sym.value, contract, EventType.TRADE, ts,
                                   ts + timedelta(milliseconds=latency_ms), price, float(rng.integers(1, 20)),
                                   f"T{seq}", seq, synthetic=True))
    return out
