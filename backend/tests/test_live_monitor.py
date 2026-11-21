"""Testes do LiveMonitor / ReplayProvider / CSVProvider / CedroProvider com eventos SINTÉTICOS."""
import asyncio
from datetime import date, timedelta

import pytest

from atlas.live.models import ConnectionState
from atlas.live.monitor import LiveMonitor, ReconnectPolicy
from atlas.live.provider import AuthenticationError, NotSupported, ProviderNotConfigured
from atlas.live.providers import CedroProvider, CSVProvider, ReplayProvider
from atlas.live.synthetic import generate_trades
from atlas.persistence.repository import Repository

DAY = date(2026, 3, 2)


async def no_sleep(_):
    return None


def monitor(prov, **kw):
    msgs = []
    mon = LiveMonitor(prov, "WIN", "WINJ26", publish=msgs.append, clock=lambda: prov.clock(), sleep=no_sleep,
                      reconnect=ReconnectPolicy(max_attempts=3), **kw)
    return mon, msgs


async def run_to_end(prov, mon, final_tick=True):
    await mon.start()
    await asyncio.wait_for(prov.done.wait(), 10)
    enabled_before_tick = mon.alerts.data_ok
    if final_tick:  # fecha o último candle (e registra corretamente o candle seguinte, sem negócios, como lacuna)
        await mon.tick(prov.clock() + timedelta(minutes=6))
    return enabled_before_tick


async def test_connect_disconnect_and_simulated_flag():
    prov = ReplayProvider(generate_trades("WIN", "WINJ26", DAY, 3))
    mon, msgs = monitor(prov)
    await mon.start()
    assert mon.state is ConnectionState.CONNECTED and prov.get_status().simulated
    assert "SIMULAÇÃO" in prov.get_status().message
    await mon.stop()
    assert prov.get_status().state is ConnectionState.DISCONNECTED


async def test_auth_failure():
    prov = ReplayProvider([], reject_auth=True)
    mon, msgs = monitor(prov)
    with pytest.raises(AuthenticationError):
        await mon.start()
    assert mon.state is ConnectionState.FAILED
    assert msgs[-1]["type"] == "status"


async def test_candles_published_and_fed_to_price_action():
    prov = ReplayProvider(generate_trades("WIN", "WINJ26", DAY, 50, seed=4))
    mon, msgs = monitor(prov)
    await run_to_end(prov, mon)
    closed = [m for m in msgs if m["type"] == "CandleClosed"]
    assert len(closed) == 50
    assert all("analysis" in m for m in closed)  # Price Action consumiu cada candle fechado
    assert closed[-1]["analysis"]["regime"]["bars_available"] == 50
    assert any(m["type"] == "CandleUpdated" for m in msgs)
    assert all(m["bar"]["synthetic"] for m in closed)
    st = mon.status()
    assert st["simulated"] and st["latency_ms_p50"] == pytest.approx(50.0)


async def test_reconnect_with_recovery_rebuilds_and_resumes_alerts():
    evs = generate_trades("WIN", "WINJ26", DAY, 12, trades_per_bucket=20, seed=8)
    prov = ReplayProvider(evs, fail_after=60, drop_on_fail=30)  # queda no 4º candle e 30 negócios perdidos
    mon, msgs = monitor(prov)
    enabled = await run_to_end(prov, mon)
    types = [m["type"] for m in msgs]
    assert "ConnectionDegraded" in types and "ConnectionRecovered" in types
    rec = next(m for m in msgs if m["type"] == "ConnectionRecovered")
    assert rec["complete"] and rec["recovered_events"] >= 30
    closed = [m["bar"] for m in msgs if m["type"] == "CandleClosed"]
    assert len({b["ts_open"] for b in closed}) == 12  # nenhum candle perdido após recuperação
    assert any("rebuilt" in b["quality_flags"] for b in closed)
    # cada candle reconstruído é idêntico ao construído a partir do fluxo completo
    prov2 = ReplayProvider(evs)
    mon2, msgs2 = monitor(prov2)
    await run_to_end(prov2, mon2)
    ref = {m["bar"]["ts_open"]: m["bar"] for m in msgs2 if m["type"] == "CandleClosed"}
    for b in closed:
        r = ref[b["ts_open"]]
        assert (b["open"], b["high"], b["low"], b["close"], b["volume"], b["trades"]) == \
               (r["open"], r["high"], r["low"], r["close"], r["volume"], r["trades"])
    assert enabled is True  # alertas retomados após recuperação completa + candle limpo
    assert mon.status()["alerts_block_reason"] == "data_gap:no_trades"  # após o fim do fluxo sintético
    assert mon.reconnect_attempts == 1


async def test_alerts_suspended_during_outage():
    evs = generate_trades("WIN", "WINJ26", DAY, 6, seed=9)
    prov = ReplayProvider(evs, fail_after=50, drop_on_fail=10)
    mon, msgs = monitor(prov)
    seen_block = []
    orig = mon.alerts.set_data_status

    def spy(ok, reason=None):
        seen_block.append((ok, reason))
        orig(ok, reason)

    mon.alerts.set_data_status = spy
    await run_to_end(prov, mon, final_tick=False)
    assert seen_block[0] == (False, "connection_degraded")
    assert seen_block[-1][0] is True  # retomado após recuperação e candle limpo


async def test_heartbeat_timeout_degrades():
    prov = ReplayProvider(generate_trades("WIN", "WINJ26", DAY, 1))
    now = {"t": None}
    mon = LiveMonitor(prov, "WIN", "WINJ26", clock=lambda: now["t"] or prov.clock(), sleep=no_sleep,
                      heartbeat_timeout=timedelta(seconds=30))
    await mon.start()
    await asyncio.wait_for(prov.done.wait(), 5)
    now["t"] = prov.clock() + timedelta(seconds=31)
    await mon.tick(now["t"])
    assert mon.state is ConnectionState.DEGRADED and not mon.alerts.data_ok


async def test_reconnect_exhaustion_marks_failed():
    class Flaky(ReplayProvider):
        async def connect(self):
            if self.reconnects > 0:
                raise ConnectionError("down")
            self.reconnects += 1
            await super().connect()

    prov = Flaky(generate_trades("WIN", "WINJ26", DAY, 4), fail_after=10)
    mon, msgs = monitor(prov)
    await mon.start()
    await asyncio.sleep(0.05)
    assert mon.state is ConnectionState.FAILED and mon.reconnect_attempts == 3


def test_backoff_policy_exponential_with_jitter():
    d = ReconnectPolicy(base_delay=1, max_delay=10, max_attempts=6, jitter=0.0).delays()
    assert d == [1, 2, 4, 8, 10, 10]
    j = ReconnectPolicy(jitter=0.2, seed=3).delays()
    assert j == ReconnectPolicy(jitter=0.2, seed=3).delays()  # reproduzível


async def test_live_candles_and_events_persisted(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path / 'l.db'}")
    repo.create_all()
    prov = ReplayProvider(generate_trades("WIN", "WINJ26", DAY, 5))
    mon, _ = monitor(prov, repo=repo)
    await run_to_end(prov, mon)
    bars = repo.load_bars("WIN", "WINJ26")
    assert len(bars) == 5 and all(b.synthetic for b in bars)
    kinds = {e["kind"] for e in repo.connection_events()}
    assert "connected" in kinds


async def test_csv_provider_is_historical_only(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("timestamp,open,high,low,close,volume\n2026-03-02 09:00,100000,100050,99950,100020,10\n")
    prov = CSVProvider(p, "WIN", "WINJ26")
    with pytest.raises(NotSupported):
        await prov.connect()
    with pytest.raises(NotSupported):
        await prov.subscribe("WINJ26")
    from atlas.contracts.calendar import load_calendar
    s = load_calendar().session("WIN", DAY)
    bars = await prov.get_historical_data("WINJ26", s.open, s.close)
    assert len(bars) == 1 and prov.get_status().state is ConnectionState.HISTORICAL_ONLY


async def test_cedro_pending_never_fakes_connection(tmp_settings):
    prov = CedroProvider(tmp_settings)
    with pytest.raises(ProviderNotConfigured):
        await prov.connect()
    with pytest.raises(ProviderNotConfigured):
        await prov.subscribe("WINJ26")
    st = prov.get_status()
    assert st.state is ConnectionState.NOT_CONFIGURED and not st.simulated


async def test_replay_from_csv_file():
    csv = ("exchange_timestamp,price,quantity,trade_id,sequence_number\n"
           "2026-03-02 09:00:01,100000,1,a,1\n2026-03-02 09:01:00,100010,2,b,2\n2026-03-02 09:05:10,100020,1,c,3\n")
    prov = ReplayProvider.from_csv(csv.encode(), "WIN", "WINJ26")
    mon, msgs = monitor(prov)
    await run_to_end(prov, mon)
    closed = [m["bar"] for m in msgs if m["type"] == "CandleClosed"]
    assert closed[0]["open"] == 100000 and closed[0]["close"] == 100010 and closed[0]["volume"] == 3


def test_ws_publication(tmp_settings):
    from fastapi.testclient import TestClient
    from atlas.api.app import create_app
    client = TestClient(create_app(tmp_settings))
    with client.websocket_connect("/ws/market") as ws:
        assert ws.receive_json()["type"] == "hello"
        r = client.post("/api/live/start", json={"provider": "replay", "symbol": "WIN", "contract": "WINJ26",
                                                 "synthetic_day": "2026-03-02", "synthetic_buckets": 3})
        assert r.status_code == 200, r.text
        assert r.json()["status"]["simulated"] is True
        types = set()
        for _ in range(400):
            m = ws.receive_json()
            types.add(m["type"])
            if m["type"] == "status" and m.get("note") == "replay concluído":
                break
        assert {"CandleUpdated", "CandleClosed"} <= types
    st = client.get("/api/market/status").json()
    assert st["live"]["contract"] == "WINJ26_SINTETICO"
