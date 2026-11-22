"""Importação de candles históricos (CSV/Parquet) para o formato canônico.

Formatos suportados (ver DATA_SOURCES.md): canônico ATLAS, MetaTrader 5, colunas em português.
Toda suposição (fuso, rótulo do timestamp, coluna de volume) é registrada em ``assumptions``.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, time
import io
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.core.timeframe import TIMEFRAME, TZ
from atlas.core.types import Bar, SeriesKind, Symbol
from atlas.market_data.validation import ValidationReport, validate_frame


@dataclass
class ImportResult:
    bars: list[Bar]
    report: ValidationReport
    detected_format: str
    assumptions: list[str] = field(default_factory=list)
    file_sha256: str = ""
    source: str = ""
    synthetic: bool = False

    def summary(self) -> dict:
        return {**self.report.summary(), "format": self.detected_format, "assumptions": self.assumptions,
                "file_sha256": self.file_sha256, "source": self.source, "synthetic": self.synthetic}


_PT_MAP = {"data": "date", "hora": "time", "abertura": "open", "maxima": "high", "máxima": "high",
           "minima": "low", "mínima": "low", "fechamento": "close", "volume": "volume",
           "negocios": "trades", "negócios": "trades", "contrato": "contract"}


def _read_text_table(raw: bytes) -> pd.DataFrame:
    text = raw.decode("utf-8-sig", errors="strict")
    first = text.splitlines()[0] if text else ""
    sep = "\t" if "\t" in first else (";" if first.count(";") > first.count(",") else ",")
    decimal = "," if sep == ";" else "."
    return pd.read_csv(io.StringIO(text), sep=sep, decimal=decimal)


def _normalize_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, str, list[str]]:
    assumptions: list[str] = []
    cols = [str(c).strip() for c in df.columns]
    df.columns = cols
    lower = [c.lower() for c in cols]

    if any(c.startswith("<") for c in cols):
        fmt = "metatrader5"
        ren = {c: c.strip("<>").lower() for c in cols}
        df = df.rename(columns=ren)
        df["timestamp"] = df["date"].astype(str).str.replace(".", "-", regex=False) + " " + df["time"].astype(str)
        if "vol" in df.columns and (pd.to_numeric(df["vol"], errors="coerce").fillna(0) > 0).any():
            df["volume"] = pd.to_numeric(df["vol"], errors="coerce")
            assumptions.append("Volume = coluna <VOL> (contratos negociados).")
        else:
            df["volume"] = pd.NA
            assumptions.append("<VOL> ausente/zero: volume deixado nulo (TICKVOL não é tratado como volume).")
        return df, fmt, assumptions

    if "data" in lower or "abertura" in lower:
        fmt = "portugues"
        df = df.rename(columns={c: _PT_MAP.get(c.lower(), c.lower()) for c in cols})
        if "time" in df.columns:
            df["timestamp"] = df["date"].astype(str) + " " + df["time"].astype(str)
        else:
            df["timestamp"] = df["date"].astype(str)
        assumptions.append("Datas em português interpretadas com dia primeiro (dd/mm/aaaa) quando ambíguas.")
        return df, fmt, assumptions

    fmt = "atlas"
    df = df.rename(columns={c: c.lower() for c in cols})
    if "timestamp" not in df.columns:
        for alt in ("datetime", "date", "time", "ts", "ts_open"):
            if alt in df.columns:
                df = df.rename(columns={alt: "timestamp"})
                break
    return df, fmt, assumptions


def fx_server_to_utc(ts: pd.Series, assumptions: list[str]) -> pd.Series:
    """Converte horários do servidor MT5 (forex) para UTC, detectando o deslocamento semana a semana.

    O mercado de forex abre no domingo às 17:00 de Nova York. A primeira barra de cada semana no arquivo
    revela o deslocamento do servidor em relação ao UTC (normalmente UTC+2 no inverno e UTC+3 no verão de NY).
    """
    ts = pd.to_datetime(ts)
    week = ts.dt.to_period("W-SAT")  # semanas de domingo a sábado
    first = ts.groupby(week).transform("min")
    offsets = {}
    for wk, f in ts.groupby(week).min().items():
        sunday = (wk.start_time).normalize()  # domingo
        ny_open = pd.Timestamp(datetime.combine(sunday.date(), time(17, 0))).tz_localize("America/New_York")
        ny_open_utc = ny_open.tz_convert("UTC").tz_localize(None)
        h = round((f - ny_open_utc).total_seconds() / 3600)
        offsets[wk] = h if -12 <= h <= 14 else None
    vals = [v for v in offsets.values() if v is not None]
    if not vals:
        raise ValueError("Não foi possível detectar o fuso do servidor MT5; informe-o manualmente.")
    mode = max(set(vals), key=vals.count)
    off = week.map(lambda w: offsets.get(w) if offsets.get(w) is not None else mode)
    _ = first
    assumptions.append(f"Fuso do servidor MT5 detectado pela abertura semanal do forex (17:00 de Nova York): "
                       f"UTC{'+' if mode >= 0 else ''}{mode} na maioria das semanas "
                       f"({sorted(set(vals))} ao longo do ano, horário de verão).")
    return (ts - pd.to_timedelta(off.astype(int), unit="h")).dt.tz_localize("UTC").dt.tz_convert(TZ)


def _parse_timestamps(series: pd.Series, fmt: str, label: str, assumptions: list[str]) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        ts = series
    else:
        ts = pd.to_datetime(series.astype(str), dayfirst=(fmt == "portugues"), utc=False, format="mixed")
    if getattr(ts.dt, "tz", None) is None:
        ts = ts.dt.tz_localize(TZ, ambiguous="raise", nonexistent="raise")
        assumptions.append("Timestamps sem fuso interpretados como America/Sao_Paulo.")
    else:
        ts = ts.dt.tz_convert(TZ)
    if label == "close":
        ts = ts - TIMEFRAME
        assumptions.append("Timestamps rotulados pelo FECHAMENTO do candle; convertidos para abertura (−5 min).")
    else:
        assumptions.append("Timestamps rotulados pela ABERTURA do candle.")
    return ts


def _resample_1m_to_5m(df: pd.DataFrame, symbol: Symbol, cal: TradingCalendar, assumptions: list[str]) -> pd.DataFrame:
    rows = []
    df = df.sort_values("ts_open")
    keys = []
    for ts in df["ts_open"]:
        s = cal.session_for_ts(symbol, ts.to_pydatetime())
        keys.append(s.bucket_open(ts.to_pydatetime()) if s else pd.NaT)
    df = df.assign(bucket=keys).dropna(subset=["bucket"])
    for bucket, g in df.groupby("bucket", sort=True):
        rows.append({
            "ts_open": bucket, "open": g["open"].iloc[0], "high": g["high"].max(), "low": g["low"].min(),
            "close": g["close"].iloc[-1],
            "volume": g["volume"].sum(min_count=1) if "volume" in g else pd.NA,
            "trades": g["trades"].sum(min_count=1) if "trades" in g else pd.NA,
            "n_1m": len(g),
        })
    assumptions.append("Candles de 1 min agregados para 5 min alinhados à abertura da sessão; candles 1m fora da sessão descartados.")
    out = pd.DataFrame(rows)
    partial = int((out["n_1m"] < 5).sum()) if len(out) else 0
    if partial:
        assumptions.append(f"{partial} candle(s) de 5 min formados com menos de 5 candles de 1 min (minutos sem negócio ou dados ausentes).")
    return out.drop(columns=["n_1m"])


def _detect_minutes(ts: pd.Series) -> int | None:
    """Intervalo predominante entre candles consecutivos do mesmo dia (empate favorece 5 e 1 min)."""
    t = ts.sort_values()
    d = t.diff()
    same_day = t.dt.normalize() == t.shift().dt.normalize()
    mins = (d[same_day].dropna().dt.total_seconds() // 60).astype(int)
    mins = mins[mins > 0]  # duplicidades são tratadas pela validação
    if len(mins) < 3:
        return None  # evidência insuficiente para inferir o intervalo
    counts = mins.value_counts()
    top = counts.max()
    for pref in (5, 1):
        if counts.get(pref, 0) == top:
            return pref
    return int(counts.idxmax())


def import_bars(
    data: bytes | str | Path,
    symbol: Symbol | str,
    contract: str | None,
    *,
    filename: str | None = None,
    timestamp_label: str = "open",
    resample_from_1m: bool = False,
    synthetic: bool = False,
    series_kind: SeriesKind = SeriesKind.CONTRACT,
    calendar: TradingCalendar | None = None,
) -> ImportResult:
    sym = Symbol(symbol)
    cal = calendar or load_calendar()
    if isinstance(data, (str, Path)):
        path = Path(data)
        raw = path.read_bytes()
        filename = filename or path.name
    else:
        raw = data
    filename = filename or "upload"
    sha = hashlib.sha256(raw).hexdigest()

    if filename.lower().endswith(".parquet"):
        df = pd.read_parquet(io.BytesIO(raw))
    else:
        df = _read_text_table(raw)
    df, fmt, assumptions = _normalize_columns(df)

    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes: {sorted(missing)}. Colunas encontradas: {list(df.columns)}")

    file_contract = None
    if "contract" in df.columns:
        vals = df["contract"].dropna().astype(str).str.upper().unique()
        if len(vals) > 1:
            raise ValueError(f"Arquivo contém múltiplos contratos {list(vals)}; importe um contrato por vez.")
        file_contract = vals[0] if len(vals) else None
        if contract and file_contract and contract.upper() != file_contract:
            raise ValueError(f"Contrato informado ({contract}) difere do contrato no arquivo ({file_contract}).")
    contract = (contract or file_contract)
    contract = contract.upper() if contract else None

    if sym.is_fx:
        raw_ts = pd.to_datetime(df["timestamp"].astype(str), format="mixed")
        ts_series = fx_server_to_utc(raw_ts, assumptions)
        if timestamp_label == "close":
            ts_series = ts_series - TIMEFRAME
        assumptions.append("Forex: mantidos apenas os candles da sessão Londres/Nova York (08:00–17:00 de Londres).")
    else:
        ts_series = _parse_timestamps(df["timestamp"], fmt, timestamp_label, assumptions)
    out = pd.DataFrame({
        "ts_open": ts_series,
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce") if "volume" in df else pd.NA,
        "trades": pd.to_numeric(df["trades"], errors="coerce") if "trades" in df else pd.NA,
    })

    if sym.is_fx:
        inside = [cal.session_for_ts(sym, t.to_pydatetime()) is not None and
                  cal.session_for_ts(sym, (t + TIMEFRAME - pd.Timedelta(seconds=1)).to_pydatetime()) is not None
                  for t in out["ts_open"]]
        n_out = len(out) - sum(inside)
        out = out[inside].reset_index(drop=True)
        assumptions.append(f"{n_out} candles fora da janela Londres/NY descartados.")
    minutes = _detect_minutes(out["ts_open"])
    if minutes is None:
        assumptions.append("Poucos candles para inferir o intervalo; assumido 5 min (a grade de 5 min é validada candle a candle).")
    if resample_from_1m:
        if minutes not in (1, None):
            raise ValueError(f"resample_from_1m requer dados de 1 min; detectado intervalo de {minutes} min.")
        out = _resample_1m_to_5m(out, sym, cal, assumptions)
    elif minutes is not None and minutes != 5:
        raise ValueError(
            f"Intervalo predominante detectado = {minutes} min. O ATLAS aceita apenas candles de 5 min "
            "(ou 1 min com resample_from_1m=True)."
        )

    source = f"file:{filename}#sha256:{sha[:12]}"
    if synthetic:
        source = "SYNTHETIC:" + source
    bars, report = validate_frame(out, sym, contract, source, synthetic=synthetic, series_kind=series_kind, calendar=cal)
    return ImportResult(bars, report, fmt, assumptions, sha, source, synthetic)
