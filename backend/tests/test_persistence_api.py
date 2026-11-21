import pytest
from fastapi.testclient import TestClient

from atlas.alerts.engine import AlertEngine
from atlas.api.app import create_app
from atlas.market_data.importers import import_bars
from atlas.persistence.repository import Repository
from atlas.pipeline import AnalysisPipeline
from tests.helpers import trend_then_h2

CSV = ("timestamp,open,high,low,close,volume\n"
       "2026-03-02 09:00,100000,100050,99950,100020,10\n"
       "2026-03-02 09:05,100020,100060,100000,100040,5\n"
       "2026-03-02 09:10,100040,100080,100020,100060,7\n")


@pytest.fixture
def repo(tmp_path):
    r = Repository(f"sqlite:///{tmp_path / 'p.db'}")
    r.create_all()
    return r


def test_persist_and_load_bars_roundtrip(repo):
    res = import_bars(CSV.encode(), "WIN", "WINJ26")
    saved = repo.save_import(res, "a.csv")
    assert saved["inserted"] == 3
    again = repo.save_import(res, "a.csv")
    assert again["inserted"] == 0 and again["skipped_existing"] == 3
    bars = repo.load_bars("WIN", "WINJ26")
    assert bars == res.bars  # inclusive fuso horário
    ds = repo.list_datasets()
    assert ds[0]["bars"] == 3 and ds[0]["synthetic"] is False


def test_alert_persistence_is_append_only(repo):
    pipe = AnalysisPipeline("WIN")
    eng = AlertEngine()
    for b in trend_then_h2([(102180, 102290, 102170, 102285)]):
        eng.process(pipe.on_bar_closed(b))
    a = eng.list("confirmed_setup")[0]
    repo.save_alert(a, "replay")
    repo.save_alert(a, "replay")  # idempotente
    stored = [x for x in repo.list_alerts() if x["alert_id"] == a.alert_id][0]
    assert stored["content_hash"] == a.content_hash and len(stored["events"]) == len(a.events)
    a.content_hash = "0" * 64  # simula tentativa de reescrever conteúdo
    with pytest.raises(ValueError, match="imutável"):
        repo.save_alert(a, "replay")


@pytest.fixture
def client(tmp_settings):
    return TestClient(create_app(tmp_settings))


def test_api_end_to_end_synthetic(client):
    r = client.get("/api/system/info").json()
    assert r["order_execution"] is False and r["timeframe"] == "5min"
    r = client.post("/api/data/synthetic", json={"symbol": "WIN", "contract": "WINJ26", "sessions": 25, "seed": 3}).json()
    contract = r["contract"]
    assert contract == "WINJ26_SINTETICO" and r["summary"]["synthetic"]
    c = client.get("/api/market/candles", params={"symbol": "WIN", "contract": contract, "limit": 50}).json()
    assert len(c["bars"]) == 50 and c["synthetic"] is True

    bt = client.post("/api/backtest/run", json={"symbol": "WIN", "contract": contract, "params": {"profile": "scalp"},
                                                "min_sample": 5}).json()
    assert bt["meta"]["synthetic"] and bt["run_id"]
    assert client.get(f"/api/backtest/runs/{bt['run_id']}").json()["run_id"] == bt["run_id"]
    csv = client.get(f"/api/backtest/runs/{bt['run_id']}/trades.csv")
    assert csv.status_code == 200 and "entry_price" in csv.text.splitlines()[0]
    assert client.get("/api/backtest/runs").json()["runs"][0]["run_id"] == bt["run_id"]

    rp = client.post("/api/replay/sessions", json={"symbol": "WIN", "contract": contract, "params": {"profile": "scalp"}}).json()
    assert rp["cursor"] == 0 and rp["bars"] == []
    st = client.post(f"/api/replay/sessions/{rp['session_id']}/step", json={"n": 500}).json()
    assert st["cursor"] == 500 and len(st["steps"]) == 500
    full = client.get(f"/api/replay/sessions/{rp['session_id']}").json()
    assert len(full["bars"]) == 500
    alerts = client.get("/api/alerts", params={"origin": "replay"}).json()["alerts"]
    if full["alerts"]:
        assert alerts and alerts[0]["payload"]["synthetic_data"] is True

    wf = client.post("/api/backtest/walk-forward", json={"symbol": "WIN", "contract": contract, "grid": {"target_r": [1, 2]},
                                                         "folds": 2, "min_sample": 5}).json()
    assert wf["configurations_tested"] == 2


def test_api_import_csv_and_validation(client):
    r = client.post("/api/data/import", files={"file": ("w.csv", CSV.encode(), "text/csv")},
                    data={"symbol": "WIN", "contract": "WINJ26"})
    assert r.status_code == 200, r.text
    assert r.json()["saved"]["inserted"] == 3
    bad = client.post("/api/data/import", files={"file": ("w.csv", CSV.encode(), "text/csv")},
                      data={"symbol": "WIN", "contract": "WIN"})
    assert bad.status_code == 422
    reserved = client.post("/api/data/import", files={"file": ("w.csv", CSV.encode(), "text/csv")},
                           data={"symbol": "WIN", "contract": "WINJ26_SINTETICO"})
    assert reserved.status_code == 422
    assert client.get("/api/market/candles", params={"symbol": "XYZ", "contract": "A"}).status_code == 422


def test_api_contracts_providers_knowledge(client):
    c = client.get("/api/market/contracts", params={"symbol": "WDO", "on": "2026-03-02"}).json()
    assert c["spec"]["tick_value_brl"] == 5.0 and c["suggestion"]["code"].startswith("WDO")
    p = client.get("/api/market/providers").json()["providers"]
    cedro = next(x for x in p if x["name"] == "cedro")
    assert cedro["state"] == "not_configured"
    assert client.post("/api/live/start", json={"provider": "cedro"}).status_code == 501
    k = client.get("/api/knowledge/concepts").json()
    assert any(x["concept_id"] == "high1_high2" for x in k["concepts"])
    s = client.get("/api/strategies").json()["strategies"][0]
    assert s["spec"]["timeframe"] == "5min"


def test_api_no_order_endpoints(client):
    paths = [r.path for r in client.app.routes]
    assert not any(w in p.lower() for p in paths for w in ("order", "ordem", "execute", "broker"))


def test_api_user_strategy_backtest_and_replay_orders(client):
    contract = client.post("/api/data/synthetic", json={"symbol": "WIN", "contract": "WINK26", "sessions": 15, "seed": 33}).json()["contract"]
    sids = [s["spec"]["strategy_id"] for s in client.get("/api/strategies").json()["strategies"]]
    assert "h2l2_scale_momentum" in sids
    bt = client.post("/api/backtest/run", json={"strategy_id": "h2l2_scale_momentum", "symbol": "WIN", "contract": contract,
                                                "min_sample": 5}).json()
    assert bt["config"]["strategy_id"] == "h2l2_scale_momentum" and bt["trades"]
    assert {t["setup"] for t in bt["trades"]} & {"H2", "L2", "3 BARRAS", "BARRA 2x"}
    rp = client.post("/api/replay/sessions", json={"strategy_id": "h2l2_scale_momentum", "symbol": "WIN", "contract": contract}).json()
    st = client.post(f"/api/replay/sessions/{rp['session_id']}/step", json={"n": 500}).json()
    orders = [o for s in st["steps"] for e in s["exec_events"] for o in e["orders"]]
    assert orders and all(o["simulated"] for o in orders)
    assert client.post("/api/backtest/run", json={"strategy_id": "xyz", "symbol": "WIN", "contract": contract}).status_code == 422
