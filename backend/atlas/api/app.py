"""API FastAPI do ATLAS B3 (REST + WebSocket). Nenhum endpoint envia ordens."""
from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from atlas import __version__
from atlas.api.state import AppState
from atlas.backtest.costs import CostModel
from atlas.backtest.engine import run_backtest, walk_forward
from atlas.config import Settings, get_settings
from atlas.contracts.calendar import load_calendar
from atlas.contracts.resolver import ContractResolver, RollPolicy
from atlas.contracts.specs import get_spec, load_specs
from atlas.core.logging import configure_logging
from atlas.core.timeframe import TIMEFRAME_LABEL, TZ
from atlas.core.types import Symbol
from atlas.knowledge.catalog import CONCEPTS, REFERENCES
from atlas.live.models import ConnectionState
from atlas.live.monitor import LiveMonitor
from atlas.live.providers import CedroProvider, ReplayProvider
from atlas.live.synthetic import generate_trades
from atlas.market_data.importers import import_bars
from atlas.market_data.synthetic import generate_bars
from atlas.persistence.repository import Repository
from atlas.price_action.engine import ENGINE_VERSION
from atlas.replay.session import ReplaySession
from atlas.reporting.serialize import bar_dict, json_safe
from atlas.strategy import h2l2_momentum as h2l2
from atlas.strategy.second_entry import PROFILES, SPEC, STRATEGY_VERSION, SecondEntryParams, SecondEntryStrategy
from atlas.pipeline import make_strategy

SYNTHETIC_SUFFIX = "_SINTETICO"
NO_ORDERS = "O ATLAS B3 não executa ordens: plataforma de pesquisa, monitoramento e simulação."


def _sym(symbol: str) -> Symbol:
    try:
        return Symbol(symbol.upper())
    except ValueError:
        raise HTTPException(422, f"Símbolo inválido: {symbol}. Use WIN ou WDO.")


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


# ----------------------------------------------------------------------------------------------
class StrategyRequest(BaseModel):
    strategy_id: str = "second_entry_trend"
    symbol: str
    contract: str
    start: str | None = None
    end: str | None = None
    params: dict = Field(default_factory=dict)
    costs: dict = Field(default_factory=dict)
    train_fraction: float = Field(0.7, gt=0.1, lt=0.95)
    min_sample: int = Field(30, ge=5, le=10000)


class WalkForwardRequest(StrategyRequest):
    grid: dict[str, list] = Field(default_factory=lambda: {"target_r": [1.0, 1.5, 2.0]})
    folds: int = Field(4, ge=2, le=12)


class ReplayRequest(BaseModel):
    strategy_id: str = "second_entry_trend"
    symbol: str
    contract: str
    start: str | None = None
    end: str | None = None
    params: dict = Field(default_factory=dict)
    costs: dict = Field(default_factory=dict)
    warmup_bars: int = Field(0, ge=0)
    evidence_run_id: str | None = None


class StepRequest(BaseModel):
    n: int = Field(1, ge=1, le=500)


class SyntheticRequest(BaseModel):
    symbol: str = "WIN"
    contract: str = "WINZ26"
    start: date = date(2026, 1, 2)
    sessions: int = Field(40, ge=1, le=500)
    seed: int = 7


class LiveStartRequest(BaseModel):
    provider: str = "replay"
    symbol: str = "WIN"
    contract: str = "WINZ26"
    synthetic_day: date | None = date(2026, 3, 2)
    synthetic_buckets: int = Field(60, ge=1, le=120)
    speed: float = Field(0.0, ge=0)


def _params(symbol: Symbol, raw: dict, strategy_id: str = "second_entry_trend"):
    cls = {"second_entry_trend": SecondEntryParams, h2l2.STRATEGY_ID: h2l2.H2L2MomentumParams}.get(strategy_id)
    if cls is None:
        raise HTTPException(422, f"Estratégia desconhecida: {strategy_id}")
    try:
        return cls(**raw)
    except Exception as e:
        raise HTTPException(422, f"Parâmetros inválidos: {e}")


def _costs(raw: dict) -> CostModel:
    try:
        return CostModel(**raw)
    except Exception as e:
        raise HTTPException(422, f"Custos inválidos: {e}")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    repo = Repository(settings.database_url)
    repo.create_all()
    state = AppState(settings, repo)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if state.live:
            await state.live.stop()
        if state.live_task:
            state.live_task.cancel()

    app = FastAPI(title="ATLAS B3", version=__version__, description=NO_ORDERS, lifespan=lifespan)
    app.state.atlas = state
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                       allow_methods=["*"], allow_headers=["*"])

    def J(obj):
        return JSONResponse(json_safe(obj))

    def _load(symbol: Symbol, contract: str, start: str | None, end: str | None):
        bars = repo.load_bars(symbol.value, contract.upper(), _dt(start), _dt(end))
        if not bars:
            raise HTTPException(404, f"Nenhum candle armazenado para {symbol.value} {contract} no período.")
        return bars

    def _evidence(symbol: Symbol, params, run_id: str | None = None) -> dict | None:
        strat = make_strategy(symbol, params)
        ph = strat.params_hash
        r = repo.get_backtest(run_id) if run_id else repo.latest_backtest_for(symbol.value, strat.spec.strategy_id, strat.spec.version, ph)
        if not r:
            return None
        return {"run_id": r["run_id"], "period": f"{r['meta']['first_ts'][:10]} a {r['meta']['last_ts'][:10]}",
                "stats": r["stats"]["all"], "stats_test": r["stats"]["test"], "probability": r["probability"],
                "synthetic": r["meta"]["synthetic"], "params_match": r["config"]["params"] == params.model_dump(mode="json")}

    # ------------------------------------------------------------------ sistema
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/system/info")
    def info():
        cal = load_calendar()
        return {"version": __version__, "timeframe": TIMEFRAME_LABEL, "engine_version": ENGINE_VERSION,
                "strategies": [{"id": SPEC.strategy_id, "version": STRATEGY_VERSION}], "order_execution": False,
                "notice": NO_ORDERS,
                "reference_validation": {"contract_specs": all(s.validated for s in load_specs()),
                                         "holidays": cal.holidays_validated, "sessions": cal.sessions_validated,
                                         "holiday_years": cal.covered_years}}

    # ------------------------------------------------------------------ mercado / contratos
    @app.get("/api/market/contracts")
    def contracts(symbol: str = "WIN", on: date | None = None, roll_days: int = 2):
        sym = _sym(symbol)
        on = on or datetime.now(TZ).date()
        res = ContractResolver()
        spec = get_spec(sym, on)
        sug = res.suggest_front(sym, on, RollPolicy(roll_days))
        listed = res.listed_after(sym, on, 4)
        return {"symbol": sym.value, "on": on.isoformat(),
                "spec": {"name": spec.name, "version": spec.version, "tick_size": spec.tick_size,
                         "point_value_brl": spec.point_value_brl, "tick_value_brl": spec.tick_value_brl,
                         "expiry_rule": spec.expiry_rule, "validated": spec.validated, "source": spec.source},
                "listed": [{"code": c.code, "expiry_date": c.expiry_date.isoformat(),
                            "last_trading_day": c.last_trading_day.isoformat(), "calendar_coverage": c.calendar_coverage}
                           for c in listed],
                "suggestion": {"code": sug.contract.code, "reason": sug.reason,
                               "note": "Sugestão auditável; a seleção do contrato é sempre explícita."}}

    @app.get("/api/market/candles")
    def candles(symbol: str, contract: str, start: str | None = None, end: str | None = None,
                limit: int = Query(5000, ge=1, le=100000)):
        sym = _sym(symbol)
        bars = repo.load_bars(sym.value, contract.upper(), _dt(start), _dt(end), limit=limit)
        return {"symbol": sym.value, "contract": contract.upper(), "timeframe": TIMEFRAME_LABEL,
                "synthetic": any(b.synthetic for b in bars), "bars": [bar_dict(b) for b in bars]}

    @app.get("/api/market/providers")
    def providers():
        return {"providers": [
            {"name": "csv", "kind": "historical", "status": "available", "simulated": False,
             "description": "Importação de candles históricos (CSV/Parquet)."},
            {"name": "replay", "kind": "replay", "status": "available", "simulated": True,
             "description": "Reprodução de negócios históricos/sintéticos com a mesma interface de um feed. Sempre rotulado como SIMULAÇÃO."},
            {"name": "cedro", "kind": "live", **CedroProvider(settings).get_status().as_dict()},
        ]}

    @app.get("/api/market/status")
    def market_status():
        live = state.live.status() if state.live else None
        return J({"live": live, "connection_events": repo.connection_events(50)})

    # ------------------------------------------------------------------ dados
    @app.get("/api/data/datasets")
    def datasets():
        return {"datasets": repo.list_datasets()}

    @app.get("/api/data/imports")
    def imports():
        return J({"imports": repo.list_imports()})

    @app.post("/api/data/import")
    async def import_file(file: UploadFile = File(...), symbol: str = Form(...), contract: str = Form(...),
                          timestamp_label: str = Form("open"), resample_from_1m: bool = Form(False),
                          replace: bool = Form(False)):
        sym = _sym(symbol)
        if contract.upper().endswith(SYNTHETIC_SUFFIX):
            raise HTTPException(422, "O sufixo _SINTETICO é reservado a dados sintéticos gerados pelo ATLAS.")
        if timestamp_label not in ("open", "close"):
            raise HTTPException(422, "timestamp_label deve ser 'open' ou 'close'")
        try:
            ContractResolver().parse(contract)
        except ValueError as e:
            raise HTTPException(422, str(e))
        raw = await file.read()
        if len(raw) > 200 * 1024 * 1024:
            raise HTTPException(413, "Arquivo maior que 200 MB")
        try:
            res = import_bars(raw, sym, contract, filename=file.filename, timestamp_label=timestamp_label,
                              resample_from_1m=resample_from_1m)
        except ValueError as e:
            raise HTTPException(422, str(e))
        saved = repo.save_import(res, file.filename or "upload", replace=replace) if res.bars else {"inserted": 0}
        return J({"summary": res.summary(), "saved": saved,
                  "issues": [{"kind": i.kind, "message": i.message, "ts": i.ts.isoformat() if i.ts else None,
                              "severity": i.severity} for i in res.report.issues[:500]],
                  "gaps": res.report.gaps[:200]})

    @app.post("/api/data/synthetic")
    def synthetic(req: SyntheticRequest):
        sym = _sym(req.symbol)
        label = req.contract.upper().removesuffix(SYNTHETIC_SUFFIX) + SYNTHETIC_SUFFIX
        bars = [b.with_(contract=label) for b in generate_bars(sym, label, req.start, req.sessions, req.seed)]
        from atlas.market_data.validation import validate_frame
        import pandas as pd
        df = pd.DataFrame([{"ts_open": b.ts_open, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                            "volume": b.volume, "trades": None} for b in bars])
        vbars, rep = validate_frame(df, sym, label, f"SYNTHETIC:seed={req.seed}", synthetic=True)
        from atlas.market_data.importers import ImportResult
        res = ImportResult(vbars, rep, "synthetic", ["Dados SINTÉTICOS gerados pelo ATLAS para teste do software."],
                           f"synthetic-seed-{req.seed}", f"SYNTHETIC:seed={req.seed}", True)
        saved = repo.save_import(res, f"synthetic_seed{req.seed}", replace=True)
        return J({"contract": label, "summary": res.summary(), "saved": saved,
                  "warning": "DADOS SINTÉTICOS — não representam o mercado real."})

    # ------------------------------------------------------------------ estratégias / backtest
    @app.get("/api/strategies")
    def strategies():
        return {"strategies": [{"spec": SPEC.as_dict(), "profiles": PROFILES,
                                "params_schema": SecondEntryParams.model_json_schema(),
                                "defaults": SecondEntryParams().model_dump(mode="json"),
                                "costs_schema": CostModel.model_json_schema(), "costs_defaults": CostModel().model_dump()},
                               {"spec": h2l2.SPEC.as_dict(), "profiles": {},
                                "params_schema": h2l2.H2L2MomentumParams.model_json_schema(),
                                "defaults": h2l2.H2L2MomentumParams().model_dump(mode="json"),
                                "costs_schema": CostModel.model_json_schema(), "costs_defaults": CostModel().model_dump()}]}

    @app.post("/api/backtest/run")
    def backtest(req: StrategyRequest):
        sym = _sym(req.symbol)
        params, costs = _params(sym, req.params, req.strategy_id), _costs(req.costs)
        bars = _load(sym, req.contract, req.start, req.end)
        res = run_backtest(bars, sym, params, costs, train_fraction=req.train_fraction, min_sample=req.min_sample)
        strat = make_strategy(sym, params)
        repo.register_strategy_version(strat.spec.strategy_id, strat.spec.version, strat.params_hash, strat.spec.as_dict(),
                                       strat.resolved_params())
        res = json_safe(res)
        repo.save_backtest(res, strat.params_hash)
        return JSONResponse(res)

    @app.post("/api/backtest/walk-forward")
    def wf(req: WalkForwardRequest):
        sym = _sym(req.symbol)
        params, costs = _params(sym, req.params, req.strategy_id), _costs(req.costs)
        allowed = set(type(params).model_fields)
        bad = set(req.grid) - allowed
        if bad:
            raise HTTPException(422, f"Parâmetros desconhecidos na grade: {sorted(bad)}")
        n_combos = 1
        for v in req.grid.values():
            n_combos *= max(1, len(v))
        if n_combos > 64:
            raise HTTPException(422, "Grade grande demais (máximo 64 combinações)")
        bars = _load(sym, req.contract, req.start, req.end)
        return J(walk_forward(bars, sym, req.grid, params, costs, folds=req.folds, min_sample=req.min_sample))

    @app.get("/api/backtest/runs")
    def runs():
        return J({"runs": repo.list_backtests()})

    @app.get("/api/backtest/runs/{run_id}")
    def run_detail(run_id: str):
        r = repo.get_backtest(run_id)
        if not r:
            raise HTTPException(404, "Backtest não encontrado")
        return J(r)

    @app.get("/api/backtest/runs/{run_id}/trades.csv")
    def run_trades_csv(run_id: str):
        r = repo.get_backtest(run_id)
        if not r:
            raise HTTPException(404, "Backtest não encontrado")
        trades = r.get("trades", [])
        buf = io.StringIO()
        if trades:
            w = csv.DictWriter(buf, fieldnames=list(trades[0].keys()))
            w.writeheader()
            w.writerows(trades)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="atlas_{run_id}_trades.csv"'})

    # ------------------------------------------------------------------ replay
    @app.post("/api/replay/sessions")
    def replay_create(req: ReplayRequest):
        sym = _sym(req.symbol)
        params, costs = _params(sym, req.params, req.strategy_id), _costs(req.costs)
        bars = _load(sym, req.contract, req.start, req.end)
        if len(state.replays) >= 20:
            state.replays.pop(next(iter(state.replays)))
        sess = ReplaySession(sym, bars, params, costs, _evidence(sym, params, req.evidence_run_id), start_index=req.warmup_bars)
        state.replays[sess.session_id] = sess
        return J({**sess.state(), "bars": sess.revealed_bars()})

    def _replay(sid: str) -> ReplaySession:
        s = state.replays.get(sid)
        if not s:
            raise HTTPException(404, "Sessão de replay não encontrada")
        return s

    @app.get("/api/replay/sessions/{sid}")
    def replay_get(sid: str):
        s = _replay(sid)
        return J({**s.state(), "bars": s.revealed_bars()})

    @app.post("/api/replay/sessions/{sid}/step")
    def replay_step(sid: str, req: StepRequest):
        s = _replay(sid)
        steps = s.step(req.n)
        for aid in {e["alert_id"] for st in steps for e in st["alert_events"]}:
            repo.save_alert(s.alerts.alerts[aid], "replay")
        return J({"steps": steps, "cursor": s.cursor, "total": s.total, "finished": s.finished})

    @app.delete("/api/replay/sessions/{sid}")
    def replay_delete(sid: str):
        state.replays.pop(sid, None)
        return {"deleted": sid}

    # ------------------------------------------------------------------ alertas / conhecimento
    @app.get("/api/alerts")
    def alerts(symbol: str | None = None, origin: str | None = None, limit: int = Query(200, ge=1, le=2000)):
        return J({"alerts": repo.list_alerts(symbol, origin, limit)})

    @app.get("/api/knowledge/concepts")
    def concepts():
        return {"concepts": [{**c.__dict__, "references": list(c.references), "limitations": list(c.limitations)} for c in CONCEPTS],
                "references": {k: {**v.__dict__, "added_on": v.added_on.isoformat()} for k, v in REFERENCES.items()},
                "note": "Resumos do ATLAS (não são citações literais). RAG com materiais licenciados: Fase 2."}

    # ------------------------------------------------------------------ live
    @app.post("/api/live/start")
    async def live_start(req: LiveStartRequest):
        sym = _sym(req.symbol)
        if state.live and state.live.state not in (ConnectionState.DISCONNECTED, ConnectionState.FAILED):
            raise HTTPException(409, "Já existe um monitor ativo; pare-o antes.")
        if req.provider == "cedro":
            raise HTTPException(501, CedroProvider.PENDING_MSG)
        if req.provider != "replay":
            raise HTTPException(422, "Provedor desconhecido")
        if req.synthetic_day is None:
            raise HTTPException(422, "Informe synthetic_day (o replay de arquivos de negócios é feito via /api/live/start-file).")
        label = req.contract.upper().removesuffix(SYNTHETIC_SUFFIX) + SYNTHETIC_SUFFIX
        try:
            evs = generate_trades(sym, label, req.synthetic_day, req.synthetic_buckets)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return await _start_monitor(ReplayProvider(evs, speed=req.speed), sym, label)

    @app.post("/api/live/start-file")
    async def live_start_file(file: UploadFile = File(...), symbol: str = Form(...), contract: str = Form(...),
                              speed: float = Form(0.0)):
        sym = _sym(symbol)
        if state.live and state.live.state not in (ConnectionState.DISCONNECTED, ConnectionState.FAILED):
            raise HTTPException(409, "Já existe um monitor ativo; pare-o antes.")
        try:
            prov = ReplayProvider.from_csv(await file.read(), sym.value, contract.upper(), speed=speed)
        except Exception as e:
            raise HTTPException(422, f"Arquivo de negócios inválido: {e}")
        return await _start_monitor(prov, sym, contract.upper())

    async def _start_monitor(prov: ReplayProvider, sym: Symbol, contract: str):
        mon = LiveMonitor(prov, sym, contract, publish=state.hub.publish, repo=repo, clock=lambda: prov.clock() or datetime.now(TZ))
        state.live = mon
        await mon.start()

        async def _finish():
            await prov.done.wait()
            end = prov.clock()
            if end:
                from datetime import timedelta
                await mon.tick(end + timedelta(minutes=6))  # fecha o último candle no fim do arquivo
            await state.hub.publish({"type": "status", "status": mon.status(), "note": "replay concluído"})

        state.live_task = asyncio.create_task(_finish())
        return J({"status": mon.status()})

    @app.post("/api/live/stop")
    async def live_stop():
        if not state.live:
            return {"stopped": False}
        await state.live.stop()
        return J({"stopped": True, "status": state.live.status()})

    @app.get("/api/live/events")
    def live_events(limit: int = Query(200, ge=1, le=1000)):
        if not state.live:
            return {"events": [], "status": None}
        return J({"events": list(state.live.events_log)[-limit:], "status": state.live.status()})

    @app.websocket("/ws/market")
    async def ws_market(ws: WebSocket):
        await ws.accept()
        q = state.hub.subscribe()
        try:
            await ws.send_json(json_safe({"type": "hello", "status": state.live.status() if state.live else None}))
            while True:
                msg = await q.get()
                await ws.send_json(json_safe(msg))
        except WebSocketDisconnect:
            pass
        finally:
            state.hub.unsubscribe(q)

    return app


app = None


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app
