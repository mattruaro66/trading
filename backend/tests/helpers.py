"""Construtores de cenários SINTÉTICOS para testes."""
from __future__ import annotations

from atlas.core.types import Bar, Side, Symbol
from atlas.strategy.base import Criterion, Signal
from tests.conftest import mk, uptrend


def trend_then_h2(extra: list[tuple] | None = None, symbol="WIN", contract="WINJ26") -> list[Bar]:
    """45 barras de alta + pullback com H1 e barra de sinal de H2 + barras extras (O,H,L,C)."""
    base = uptrend(45, symbol=symbol, contract=contract)
    last = base[-1]
    pb = [
        (102250, 102250, 102160, 102170),  # início do pullback (máxima < anterior)
        (102170, 102180, 102080, 102090),  # continua (rearma)
        (102090, 102190, 102080, 102180),  # H1
        (102170, 102185, 102150, 102180),  # rearma → barra de sinal do H2
    ]
    rows = pb + (extra or [])
    tail = mk(rows, symbol, contract, start=last.ts_close)
    return base + tail


def make_signal(bar: Bar, side: Side = Side.LONG, entry=None, stop=None, target=None, time_exit=24) -> Signal:
    entry = entry if entry is not None else (bar.high + 5 if side is Side.LONG else bar.low - 5)
    stop = stop if stop is not None else (bar.low - 5 if side is Side.LONG else bar.high + 5)
    risk = abs(entry - stop)
    target = target if target is not None else entry + side.sign * 2 * risk
    return Signal("test", "0", "h", "daytrade", Symbol(bar.symbol), bar.contract, side, bar, "H2", entry, stop, target,
                  risk, risk / 5, 2.0, time_exit, bar.ts_open, (Criterion("X", "x", True),), {"regime": {"regime": "BULL_TREND"}})
