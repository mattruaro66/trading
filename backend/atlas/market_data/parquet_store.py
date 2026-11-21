"""Armazenamento de candles em Parquet: ``{store}/{symbol}/{contract}.parquet``."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from atlas.core.timeframe import TZ
from atlas.core.types import Bar, BarStatus, Quality, SeriesKind, Symbol


def bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame([{
        "symbol": b.symbol.value, "contract": b.contract, "ts_open": b.ts_open, "open": b.open,
        "high": b.high, "low": b.low, "close": b.close, "volume": b.volume, "trades": b.trades,
        "status": b.status.value, "quality": b.quality.value, "quality_flags": ",".join(b.quality_flags),
        "source": b.source, "series_kind": b.series_kind.value, "synthetic": b.synthetic,
    } for b in bars])


def frame_to_bars(df: pd.DataFrame) -> list[Bar]:
    out = []
    for r in df.itertuples(index=False):
        out.append(Bar(
            Symbol(r.symbol), r.contract, r.ts_open.to_pydatetime().astimezone(TZ), float(r.open), float(r.high),
            float(r.low), float(r.close), None if pd.isna(r.volume) else float(r.volume),
            None if pd.isna(r.trades) else int(r.trades), BarStatus(r.status), Quality(r.quality),
            tuple(x for x in str(r.quality_flags).split(",") if x), r.source, SeriesKind(r.series_kind), bool(r.synthetic),
        ))
    return out


def save_bars(store_dir: Path, bars: list[Bar]) -> Path:
    if not bars:
        raise ValueError("Nada a salvar")
    b0 = bars[0]
    path = Path(store_dir) / b0.symbol.value / f"{b0.contract or 'continuous'}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = bars_to_frame(bars)
    if path.exists():
        old = pd.read_parquet(path)
        new = pd.concat([old, new]).drop_duplicates("ts_open", keep="last").sort_values("ts_open")
    new.to_parquet(path, index=False)
    return path


def load_bars(store_dir: Path, symbol: str, contract: str | None) -> list[Bar]:
    path = Path(store_dir) / symbol / f"{contract or 'continuous'}.parquet"
    if not path.exists():
        return []
    return frame_to_bars(pd.read_parquet(path))
