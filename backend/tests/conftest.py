"""Fixtures de teste. TODOS os dados aqui são SINTÉTICOS e marcados como tal."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from atlas.contracts.calendar import load_calendar
from atlas.core.timeframe import TIMEFRAME, TZ
from atlas.core.types import Bar, Symbol
from atlas.market_data.synthetic import generate_bars

DAY = date(2026, 3, 2)  # segunda-feira, dia de pregão no arquivo de referência


def session_open(day: date = DAY, symbol: str = "WIN") -> datetime:
    return load_calendar().session(symbol, day).open


def mk(ohlc: list[tuple[float, float, float, float]], symbol: str = "WIN", contract: str = "WINJ26",
       start: datetime | None = None, **kw) -> list[Bar]:
    """Cria candles sintéticos contíguos a partir de tuplas (O, H, L, C)."""
    t = start or session_open(DAY, symbol)
    out = []
    for o, h, l, c in ohlc:
        out.append(Bar(Symbol(symbol), contract, t, o, h, l, c, 100.0, source="SYNTHETIC:test", synthetic=True, **kw))
        t += TIMEFRAME
    return out


def uptrend(n: int, start_price: float = 100000.0, step: float = 50.0, symbol="WIN", contract="WINJ26", start=None):
    """Tendência de alta sintética simples (cada barra sobe ``step``)."""
    rows, p = [], start_price
    for _ in range(n):
        o, c = p, p + step
        rows.append((o, c + 10, o - 10, c))
        p = c
    return mk(rows, symbol, contract, start)


@pytest.fixture(scope="session")
def synthetic_win() -> list[Bar]:
    return generate_bars("WIN", "WINJ26", date(2026, 1, 2), 40, seed=11)


@pytest.fixture(scope="session")
def synthetic_wdo() -> list[Bar]:
    return generate_bars("WDO", "WDOJ26", date(2026, 1, 2), 30, seed=5)


@pytest.fixture
def tmp_settings(tmp_path):
    from atlas.config import Settings
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", data_dir=tmp_path)
