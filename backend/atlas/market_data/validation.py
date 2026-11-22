"""Validação de candles OHLCV de 5 minutos.

Nada é corrigido silenciosamente: linhas problemáticas são excluídas ou
marcadas, e cada decisão é registrada em ``ValidationReport``. Lacunas são
reportadas e **nunca** preenchidas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.contracts.specs import get_spec
from atlas.core.timeframe import TIMEFRAME, TZ
from atlas.core.types import Bar, DataIssue, Quality, SeriesKind, Symbol


@dataclass
class ValidationReport:
    symbol: str
    contract: str | None
    rows_in: int = 0
    rows_valid: int = 0
    rows_rejected: int = 0
    rows_suspect: int = 0
    issues: list[DataIssue] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    sessions_covered: int = 0
    calendar_coverage_ok: bool = True

    def add(self, kind: str, msg: str, ts=None, severity="error", **details):
        self.issues.append(DataIssue(kind, msg, ts, severity, details))

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for i in self.issues:
            kinds[i.kind] = kinds.get(i.kind, 0) + 1
        return {
            "symbol": self.symbol, "contract": self.contract, "rows_in": self.rows_in,
            "rows_valid": self.rows_valid, "rows_rejected": self.rows_rejected,
            "rows_suspect": self.rows_suspect, "issue_counts": kinds, "gaps": len(self.gaps),
            "first_ts": self.first_ts.isoformat() if self.first_ts else None,
            "last_ts": self.last_ts.isoformat() if self.last_ts else None,
            "sessions_covered": self.sessions_covered,
            "calendar_coverage_ok": self.calendar_coverage_ok,
        }


def validate_frame(
    df: pd.DataFrame,
    symbol: Symbol | str,
    contract: str | None,
    source: str,
    *,
    synthetic: bool = False,
    series_kind: SeriesKind = SeriesKind.CONTRACT,
    calendar: TradingCalendar | None = None,
) -> tuple[list[Bar], ValidationReport]:
    """Valida um DataFrame canônico (colunas: ts_open[tz-aware], open, high, low, close, volume, trades)."""
    sym = Symbol(symbol)
    cal = calendar or load_calendar()
    spec = get_spec(sym)
    rep = ValidationReport(sym.value, contract, rows_in=len(df))
    if series_kind is SeriesKind.CONTRACT and not contract:
        raise ValueError("Contrato obrigatório para série de contrato (ex.: WINZ26). Use series_kind=continuous para séries contínuas.")

    df = df.copy()
    # ordenação
    if not df["ts_open"].is_monotonic_increasing:
        rep.add("unordered", "Timestamps fora de ordem; registros reordenados cronologicamente.", severity="warning")
        df = df.sort_values("ts_open", kind="stable")

    # duplicidades
    dup_mask = df.duplicated("ts_open", keep=False)
    if dup_mask.any():
        price_cols = ["open", "high", "low", "close"]
        keep = []
        for ts, grp in df[dup_mask].groupby("ts_open"):
            identical = (grp[price_cols].nunique() == 1).all()
            if identical:
                rep.add("duplicate_identical", "Candle duplicado idêntico; mantida uma ocorrência.", ts, "warning")
                keep.append(grp.index[0])
            else:
                rep.add("duplicate_conflict", "Candles duplicados com valores diferentes; todos rejeitados.", ts)
        drop_idx = df[dup_mask].index.difference(keep)
        rep.rows_rejected += len(drop_idx)
        df = df.drop(index=drop_idx)

    bars: list[Bar] = []
    sessions_seen: dict = {}
    for row in df.itertuples(index=False):
        ts: datetime = row.ts_open.to_pydatetime() if hasattr(row.ts_open, "to_pydatetime") else row.ts_open
        ts = ts.astimezone(TZ)
        flags: list[str] = []
        o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
        vol = None if pd.isna(row.volume) else float(row.volume)
        trades = None if (not hasattr(row, "trades") or pd.isna(row.trades)) else int(row.trades)

        if not cal.has_coverage(ts.date()):
            rep.calendar_coverage_ok = False
        sess = cal.session(sym, ts.date())
        if sess is None:
            rep.add("non_trading_day", "Candle em dia sem pregão segundo o calendário.", ts)
            rep.rows_rejected += 1
            continue
        if not (sess.open <= ts and ts + TIMEFRAME <= sess.close):
            rep.add("outside_session", f"Candle fora da sessão {sess.open:%H:%M}–{sess.close:%H:%M}.", ts)
            rep.rows_rejected += 1
            continue
        if (ts - sess.open) % TIMEFRAME:
            rep.add("misaligned", "Timestamp não alinhado à grade de 5 min da sessão.", ts)
            rep.rows_rejected += 1
            continue
        if any(pd.isna(x) for x in (o, h, l, c)) or min(o, h, l, c) <= 0:
            rep.add("invalid_price", "Preço ausente ou não positivo.", ts)
            rep.rows_rejected += 1
            continue
        if h < max(o, c) or l > min(o, c) or h < l:
            rep.add("ohlc_inconsistent", f"OHLC inconsistente (O={o} H={h} L={l} C={c}).", ts)
            rep.rows_rejected += 1
            continue
        if vol is not None and vol < 0:
            rep.add("negative_volume", "Volume negativo.", ts)
            rep.rows_rejected += 1
            continue
        if not all(spec.is_on_tick(x) for x in (o, h, l, c)):
            flags.append("off_tick")
            rep.add("off_tick", f"Preço fora do tick mínimo ({spec.tick_size}).", ts, "warning")

        quality = Quality.SUSPECT if flags else Quality.OK
        if quality is Quality.SUSPECT:
            rep.rows_suspect += 1
        sessions_seen.setdefault(sess.day, (sess, []))[1].append(ts)
        bars.append(Bar(sym, contract, ts, o, h, l, c, vol, trades, quality=quality,
                        quality_flags=tuple(flags), source=source, series_kind=series_kind, synthetic=synthetic))

    # lacunas dentro de cada sessão (entre o primeiro e o último candle presentes)
    for day, (sess, stamps) in sessions_seen.items():
        present = set(stamps)
        expected = [t for t in sess.bucket_opens() if min(stamps) <= t <= max(stamps)]
        missing = [t for t in expected if t not in present]
        if missing:
            rep.gaps.append({"day": day.isoformat(), "missing": len(missing),
                             "first_missing": missing[0].isoformat(), "last_missing": missing[-1].isoformat()})
            rep.add("missing_candles", f"{len(missing)} candle(s) ausente(s) na sessão de {day:%d/%m/%Y}.",
                    missing[0], "warning", count=len(missing))

    rep.rows_valid = len(bars)
    rep.sessions_covered = len(sessions_seen)
    if bars:
        rep.first_ts, rep.last_ts = bars[0].ts_open, bars[-1].ts_open
    if not rep.calendar_coverage_ok:
        rep.add("calendar_coverage", "Há datas fora dos anos cobertos pelo arquivo de feriados; pregões presumidos.", severity="warning")
    return bars, rep


def is_contiguous(prev: Bar, cur: Bar, calendar: TradingCalendar | None = None) -> bool:
    """True se ``cur`` é o candle imediatamente seguinte a ``prev`` dentro da mesma sessão e contrato."""
    if prev.contract != cur.contract or prev.symbol != cur.symbol:
        return False
    return cur.ts_open == prev.ts_open + TIMEFRAME


def is_next_session_open(prev: Bar, cur: Bar, calendar: TradingCalendar | None = None) -> bool:
    """True se ``prev`` foi o último candle de uma sessão e ``cur`` o primeiro da próxima sessão de pregão."""
    cal = calendar or load_calendar()
    if prev.contract != cur.contract:
        return False
    s_prev = cal.session_for_ts(prev.symbol, prev.ts_open)
    s_cur = cal.session_for_ts(cur.symbol, cur.ts_open)
    if not s_prev or not s_cur:
        return False
    consecutive_days = s_cur.day == cal.next_trading_day(s_prev.day, prev.symbol)
    if prev.symbol.is_fx:
        # forex: o horário efetivo de negociação pode ser menor que a janela da sessão (ex.: USDBRL offshore);
        # a virada para o dia útil seguinte é uma troca normal de pregão, não uma lacuna de dados.
        return consecutive_days
    last_prev = s_prev.bucket_opens()[-1]
    return prev.ts_open == last_prev and cur.ts_open == s_cur.open and consecutive_days
