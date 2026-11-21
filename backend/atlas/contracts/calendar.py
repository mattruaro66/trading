"""Calendário de pregões e sessões (America/Sao_Paulo).

Feriados e horários vêm de arquivos versionados em ``data/reference``.
Datas fora dos anos cobertos pelo arquivo de feriados são reportadas como
``coverage=False`` — o sistema não presume que um dia é pregão sem cobertura.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

from atlas.contracts.specs import REFERENCE_DIR
from atlas.core.timeframe import TIMEFRAME, TZ
from atlas.core.types import Symbol


@dataclass(frozen=True)
class SessionWindow:
    symbol: Symbol
    day: date
    open: datetime
    close: datetime
    special_note: str | None = None

    def contains(self, ts: datetime) -> bool:
        return self.open <= ts < self.close

    def bucket_open(self, ts: datetime) -> datetime:
        """Abertura do candle de 5 min (alinhado à abertura da sessão) que contém ``ts``."""
        if not self.contains(ts):
            raise ValueError(f"{ts} fora da sessão {self.open}–{self.close}")
        n = (ts - self.open) // TIMEFRAME
        return self.open + n * TIMEFRAME

    def bucket_opens(self) -> list[datetime]:
        out, t = [], self.open
        while t + TIMEFRAME <= self.close:
            out.append(t)
            t += TIMEFRAME
        return out


class TradingCalendar:
    def __init__(self, holidays: dict[int, set[date]], special: dict[date, dict], sessions: list[dict],
                 holidays_validated: bool, sessions_validated: bool):
        self._holidays = holidays
        self._special = special
        self._sessions = sorted(sessions, key=lambda s: s["effective_from"])
        self.holidays_validated = holidays_validated
        self.sessions_validated = sessions_validated

    @property
    def covered_years(self) -> list[int]:
        return sorted(self._holidays)

    def has_coverage(self, d: date) -> bool:
        return d.year in self._holidays

    def is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        if not self.has_coverage(d):
            # Sem cobertura: considera apenas fim de semana e marca a incerteza via has_coverage().
            return True
        return d not in self._holidays[d.year]

    def next_trading_day(self, d: date) -> date:
        d = d + timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def prev_trading_day(self, d: date) -> date:
        d = d - timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def session(self, symbol: Symbol | str, d: date) -> SessionWindow | None:
        sym = Symbol(symbol)
        if not self.is_trading_day(d):
            return None
        versions = [s for s in self._sessions if s["symbol"] == sym.value and s["effective_from"] <= d]
        if not versions:
            return None
        v = versions[-1]
        open_t, close_t = v["open"], v["close"]
        note = None
        if d in self._special:
            sp = self._special[d]
            if "open" in sp:
                open_t = time.fromisoformat(sp["open"])
            if "close" in sp:
                close_t = time.fromisoformat(sp["close"])
            note = sp.get("note")
        return SessionWindow(sym, d, datetime.combine(d, open_t, TZ), datetime.combine(d, close_t, TZ), note)

    def session_for_ts(self, symbol: Symbol | str, ts: datetime) -> SessionWindow | None:
        local = ts.astimezone(TZ)
        s = self.session(symbol, local.date())
        if s and s.contains(local):
            return s
        return None


@lru_cache(maxsize=2)
def load_calendar(reference_dir: str | None = None) -> TradingCalendar:
    base = Path(reference_dir) if reference_dir else REFERENCE_DIR
    h = json.loads((base / "b3_holidays.json").read_text(encoding="utf-8"))
    s = json.loads((base / "sessions.json").read_text(encoding="utf-8"))
    holidays = {int(y): {date.fromisoformat(x) for x in days} for y, days in h["holidays"].items()}
    special = {date.fromisoformat(k): v for k, v in h.get("special_sessions", {}).items()}
    sessions = [
        {
            "symbol": x["symbol"],
            "effective_from": date.fromisoformat(x["effective_from"]),
            "open": time.fromisoformat(x["open"]),
            "close": time.fromisoformat(x["close"]),
        }
        for x in s["sessions"]
    ]
    return TradingCalendar(holidays, special, sessions, bool(h.get("validated")), bool(s.get("validated")))
