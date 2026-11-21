"""Repositório de persistência (SQLAlchemy 2)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from atlas.alerts.engine import Alert
from atlas.core.timeframe import TZ
from atlas.core.types import Bar, BarStatus, Quality, SeriesKind, Symbol
from atlas.market_data.importers import ImportResult
from atlas.persistence.models import (
    AlertEventRow, AlertRow, BacktestRunRow, Base, CandleRow, ConnectionEventRow, DataImportRow, ProviderRow,
    StrategyVersionRow,
)


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:  # SQLite descarta o fuso; gravamos sempre em UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Repository:
    def __init__(self, url: str):
        kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        self.engine: Engine = create_engine(url, future=True, **kw)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    # ---------------- candles ----------------
    def save_import(self, res: ImportResult, filename: str, replace: bool = False) -> dict:
        with self.Session.begin() as s:
            imp = DataImportRow(symbol=res.report.symbol, contract=res.report.contract, filename=filename,
                                file_sha256=res.file_sha256, format=res.detected_format, synthetic=res.synthetic,
                                summary=res.summary(), created_at=_now())
            s.add(imp)
            s.flush()
            inserted = skipped = replaced = 0
            if res.bars:
                b0 = res.bars[0]
                key_contract = b0.contract or "continuous"
                existing = {_local(r.ts_open): r for r in s.scalars(select(CandleRow).where(
                    CandleRow.symbol == b0.symbol.value, CandleRow.contract == key_contract,
                    CandleRow.ts_open >= _utc(res.bars[0].ts_open), CandleRow.ts_open <= _utc(res.bars[-1].ts_open)))}
                for b in res.bars:
                    row = existing.get(b.ts_open)
                    if row is not None:
                        if not replace:
                            skipped += 1
                            continue
                        s.delete(row)
                        s.flush()
                        replaced += 1
                    s.add(self._bar_row(b, imp.id))
                    inserted += 1
            return {"import_id": imp.id, "inserted": inserted, "skipped_existing": skipped, "replaced": replaced}

    @staticmethod
    def _bar_row(b: Bar, import_id: int | None) -> CandleRow:
        return CandleRow(symbol=b.symbol.value, contract=b.contract or "continuous", series_kind=b.series_kind.value,
                         ts_open=_utc(b.ts_open), open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume,
                         trades=b.trades, status=b.status.value, quality=b.quality.value,
                         quality_flags=",".join(b.quality_flags), source=b.source, synthetic=b.synthetic, import_id=import_id)

    def save_live_bar(self, b: Bar) -> bool:
        """Persiste um candle fechado do live (ignora se já existir)."""
        with self.Session.begin() as s:
            exists = s.scalar(select(CandleRow.id).where(CandleRow.symbol == b.symbol.value,
                                                         CandleRow.contract == (b.contract or "continuous"),
                                                         CandleRow.ts_open == _utc(b.ts_open)))
            if exists:
                return False
            s.add(self._bar_row(b, None))
            return True

    def load_bars(self, symbol: str, contract: str, start: datetime | None = None, end: datetime | None = None,
                  limit: int | None = None) -> list[Bar]:
        with self.Session() as s:
            q = select(CandleRow).where(CandleRow.symbol == symbol, CandleRow.contract == contract)
            if start:
                q = q.where(CandleRow.ts_open >= _utc(start))
            if end:
                q = q.where(CandleRow.ts_open <= _utc(end))
            q = q.order_by(CandleRow.ts_open)
            rows = list(s.scalars(q))
        if limit:
            rows = rows[-limit:]
        return [Bar(Symbol(r.symbol), None if r.contract == "continuous" else r.contract, _local(r.ts_open), r.open,
                    r.high, r.low, r.close, r.volume, r.trades, BarStatus(r.status), Quality(r.quality),
                    tuple(x for x in r.quality_flags.split(",") if x), r.source, SeriesKind(r.series_kind), r.synthetic)
                for r in rows]

    def list_datasets(self) -> list[dict]:
        with self.Session() as s:
            q = select(CandleRow.symbol, CandleRow.contract, func.count(), func.min(CandleRow.ts_open),
                       func.max(CandleRow.ts_open), func.max(CandleRow.synthetic.cast(Integer))) \
                .group_by(CandleRow.symbol, CandleRow.contract)
            return [{"symbol": a, "contract": b, "bars": c, "first_ts": _local(d).isoformat(),
                     "last_ts": _local(e).isoformat(), "synthetic": bool(f)} for a, b, c, d, e, f in s.execute(q)]

    def list_imports(self, limit: int = 50) -> list[dict]:
        with self.Session() as s:
            rows = s.scalars(select(DataImportRow).order_by(DataImportRow.id.desc()).limit(limit))
            return [{"id": r.id, "symbol": r.symbol, "contract": r.contract, "filename": r.filename,
                     "format": r.format, "synthetic": r.synthetic, "summary": r.summary,
                     "created_at": _local(r.created_at).isoformat()} for r in rows]

    # ---------------- estratégias/backtests ----------------
    def register_strategy_version(self, strategy_id: str, version: str, params_hash: str, spec: dict, params: dict) -> None:
        with self.Session.begin() as s:
            exists = s.scalar(select(StrategyVersionRow.id).where(
                StrategyVersionRow.strategy_id == strategy_id, StrategyVersionRow.version == version,
                StrategyVersionRow.params_hash == params_hash))
            if not exists:
                s.add(StrategyVersionRow(strategy_id=strategy_id, version=version, params_hash=params_hash,
                                         spec=spec, params=params, created_at=_now()))

    def save_backtest(self, result: dict, params_hash: str) -> None:
        with self.Session.begin() as s:
            if s.get(BacktestRunRow, result["run_id"]):
                return
            c = result["config"]
            s.add(BacktestRunRow(run_id=result["run_id"], symbol=result["meta"]["symbol"], strategy_id=c["strategy_id"],
                                 strategy_version=c["strategy_version"], params_hash=params_hash,
                                 synthetic=result["meta"]["synthetic"], result=result, created_at=_now()))

    def get_backtest(self, run_id: str) -> dict | None:
        with self.Session() as s:
            r = s.get(BacktestRunRow, run_id)
            return r.result if r else None

    def list_backtests(self, limit: int = 50) -> list[dict]:
        with self.Session() as s:
            rows = s.scalars(select(BacktestRunRow).order_by(BacktestRunRow.created_at.desc()).limit(limit))
            return [{"run_id": r.run_id, "symbol": r.symbol, "strategy_id": r.strategy_id,
                     "strategy_version": r.strategy_version, "params_hash": r.params_hash, "synthetic": r.synthetic,
                     "created_at": _local(r.created_at).isoformat(), "n": r.result["stats"]["all"]["n"],
                     "net_brl": r.result["stats"]["all"]["net_brl"], "profile": r.result["config"]["params"]["profile"],
                     "first_ts": r.result["meta"]["first_ts"], "last_ts": r.result["meta"]["last_ts"]} for r in rows]

    def latest_backtest_for(self, symbol: str, strategy_id: str, version: str, params_hash: str) -> dict | None:
        with self.Session() as s:
            r = s.scalars(select(BacktestRunRow).where(
                BacktestRunRow.symbol == symbol, BacktestRunRow.strategy_id == strategy_id,
                BacktestRunRow.strategy_version == version, BacktestRunRow.params_hash == params_hash)
                .order_by(BacktestRunRow.created_at.desc()).limit(1)).first()
            return r.result if r else None

    # ---------------- alertas ----------------
    def save_alert(self, alert: Alert, origin: str) -> None:
        """Insere o alerta (se novo) e eventos ainda não persistidos. Nunca altera o payload original."""
        with self.Session.begin() as s:
            row = s.get(AlertRow, alert.alert_id)
            if row is None:
                s.add(AlertRow(alert_id=alert.alert_id, setup_key=alert.setup_key, origin=origin,
                               symbol=alert.payload.get("symbol", ""), created_ts=_utc(alert.created_ts),
                               payload=alert.payload, content_hash=alert.content_hash,
                               synthetic=bool(alert.payload.get("synthetic_data"))))
            elif row.content_hash != alert.content_hash:
                raise ValueError(f"Tentativa de alterar conteúdo imutável do alerta {alert.alert_id}")
            have = set(s.scalars(select(AlertEventRow.seq).where(AlertEventRow.alert_id == alert.alert_id)))
            for e in alert.events:
                if e.seq in have:
                    continue
                s.add(AlertEventRow(alert_id=e.alert_id, seq=e.seq, state=e.state.value, ts=_utc(e.ts), reason=e.reason,
                                    data=e.data, prev_hash=e.prev_hash, event_hash=e.event_hash))

    def list_alerts(self, symbol: str | None = None, origin: str | None = None, limit: int = 200) -> list[dict]:
        with self.Session() as s:
            q = select(AlertRow).order_by(AlertRow.created_ts.desc()).limit(limit)
            if symbol:
                q = q.where(AlertRow.symbol == symbol)
            if origin:
                q = q.where(AlertRow.origin == origin)
            out = []
            for r in s.scalars(q):
                evs = list(s.scalars(select(AlertEventRow).where(AlertEventRow.alert_id == r.alert_id).order_by(AlertEventRow.seq)))
                out.append({"alert_id": r.alert_id, "origin": r.origin, "symbol": r.symbol,
                            "created_ts": _local(r.created_ts).isoformat(), "payload": r.payload,
                            "content_hash": r.content_hash, "synthetic": r.synthetic,
                            "state": evs[-1].state if evs else None,
                            "events": [{"seq": e.seq, "state": e.state, "ts": _local(e.ts).isoformat(), "reason": e.reason,
                                        "data": e.data, "event_hash": e.event_hash, "prev_hash": e.prev_hash} for e in evs]})
            return out

    # ---------------- conexões ----------------
    def save_connection_event(self, provider: str, kind: str, ts: datetime, details: dict | None = None) -> None:
        with self.Session.begin() as s:
            s.add(ConnectionEventRow(provider=provider, kind=kind, ts=_utc(ts), details=details or {}))

    def upsert_provider(self, name: str, kind: str, status: str, details: dict) -> None:
        with self.Session.begin() as s:
            row = s.get(ProviderRow, name)
            if row is None:
                s.add(ProviderRow(name=name, kind=kind, status=status, details=details, updated_at=_now()))
            else:
                row.status, row.details, row.updated_at = status, details, _now()

    def connection_events(self, limit: int = 100) -> list[dict]:
        with self.Session() as s:
            rows = s.scalars(select(ConnectionEventRow).order_by(ConnectionEventRow.id.desc()).limit(limit))
            return [{"provider": r.provider, "kind": r.kind, "ts": _local(r.ts).isoformat(), "details": r.details} for r in rows]
