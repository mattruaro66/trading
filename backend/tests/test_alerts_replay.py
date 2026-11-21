import re

from atlas.alerts.engine import AlertConfig, AlertEngine, AlertState
from atlas.backtest.engine import run_backtest
from atlas.explanation.engine import explain_alert
from atlas.pipeline import AnalysisPipeline
from atlas.replay.session import ReplaySession
from atlas.strategy.second_entry import SecondEntryParams
from tests.helpers import trend_then_h2


def run_alerts(bars, config=None, data_ok_fn=None):
    pipe = AnalysisPipeline("WIN", SecondEntryParams())
    eng = AlertEngine(config or AlertConfig(), explain=explain_alert)
    for i, b in enumerate(bars):
        if data_ok_fn:
            eng.set_data_status(data_ok_fn(i), "teste")
        eng.process(pipe.on_bar_closed(b))
    return eng


def test_alert_lifecycle_confirmed_to_closed():
    eng = run_alerts(trend_then_h2([(102180, 102290, 102170, 102285)]))
    confirmed = eng.list("confirmed_setup")
    assert len(confirmed) == 1
    a = confirmed[0]
    states = [e.state for e in a.events]
    assert states[0] is AlertState.CONFIRMED and states[-1] is AlertState.CLOSED
    assert a.events[-1].data["exit_reason"] == "target"
    p = a.payload
    assert p["timeframe"] == "5min" and p["contract"] == "WINJ26" and p["signal"]["entry_price"] == 102190
    assert p["evidence"] is None  # sem backtest associado → nada inventado
    tracking = eng.list("setup_tracking")
    assert tracking and tracking[0].events[0].state is AlertState.OBSERVATION
    assert any(e.state is AlertState.PREPARATION for e in tracking[0].events)


def test_alert_invalidated_when_not_triggered():
    eng = run_alerts(trend_then_h2([(102180, 102185, 102130, 102140)]))
    a = eng.list("confirmed_setup")[0]
    assert a.state is AlertState.INVALIDATED


def test_alert_immutability_and_tamper_detection():
    eng = run_alerts(trend_then_h2([(102180, 102290, 102170, 102285)]))
    a = eng.list("confirmed_setup")[0]
    assert a.verify()
    a.payload["signal"]["entry_price"] = 1  # adulteração
    assert not a.verify()


def test_event_chain_tamper_detection():
    eng = run_alerts(trend_then_h2([(102180, 102290, 102170, 102285)]))
    a = eng.list("confirmed_setup")[0]
    a.events[-1].data["net_brl"] = 999999
    assert not a.verify()


def test_alerts_suppressed_when_data_not_ok():
    bars = trend_then_h2()
    eng = run_alerts(bars, data_ok_fn=lambda i: i < len(bars) - 1)
    assert not eng.list("confirmed_setup")
    assert eng.suppressed and eng.suppressed[0]["reason"] == "teste"


def test_dedup_and_cooldown():
    bars = trend_then_h2()
    pipe = AnalysisPipeline("WIN", SecondEntryParams())
    eng = AlertEngine(AlertConfig(cooldown_bars=3))
    outs = [pipe.on_bar_closed(b) for b in bars]
    for o in outs:
        eng.process(o)
    n = len(eng.list("confirmed_setup"))
    eng.process(outs[-1])  # mesmo sinal reprocessado → bloqueado (cooldown/dedup)
    assert len(eng.list("confirmed_setup")) == n == 1
    assert eng.suppressed[-1]["reason"] == "cooldown"


def test_explanation_sections_and_numbers_from_payload():
    eng = run_alerts(trend_then_h2())
    a = eng.list("confirmed_setup")[0]
    ex = a.payload["explanation"]
    titles = [s["title"][0] for s in ex["sections"]]
    assert titles == list("ABCDEFG")
    text = " ".join(s.get("text", "") for s in ex["sections"])
    assert "Evidência estatística insuficiente" in text
    # números de preço citados no cenário F vêm do payload
    f = ex["sections"][5]["text"]
    assert "102.190" in f and "102.145" in f and "102.280" in f
    assert "SINTÉTICOS" in " ".join(ex["sections"][6]["items"])


def test_explanation_with_evidence(synthetic_win):
    r = run_backtest(synthetic_win, "WIN", SecondEntryParams(), min_sample=5)
    ev = {"run_id": r["run_id"], "period": "x", "stats": r["stats"]["all"], "probability": r["probability"], "synthetic": True}
    eng = AlertEngine(explain=explain_alert, evidence_provider=lambda s: ev)
    pipe = AnalysisPipeline("WIN", SecondEntryParams())
    for b in trend_then_h2():
        eng.process(pipe.on_bar_closed(b))
    text = eng.list("confirmed_setup")[0].payload["explanation"]["sections"][4]["text"]
    assert r["run_id"] in text and f"{r['stats']['all']['n']} operações" in text and "SINTÉTICOS" in text


def test_replay_never_reveals_future(synthetic_win):
    s = ReplaySession(symbol=synthetic_win[0].symbol, bars=synthetic_win[:300], params=SecondEntryParams())
    assert s.revealed_bars() == []
    s.step(10)
    rb = s.revealed_bars()
    assert len(rb) == 10 and rb[-1]["ts_open"] == synthetic_win[9].ts_open.isoformat()
    st = s.state()
    assert st["cursor"] == 10 and "bars" not in st


def test_replay_matches_backtest(synthetic_win):
    bars = synthetic_win[:1500]
    params = SecondEntryParams.for_profile("scalp")
    s = ReplaySession(symbol=bars[0].symbol, bars=bars, params=params)
    while not s.finished:
        s.step(97)
    bt = run_backtest(bars, "WIN", params)
    assert [t["entry_ts"] for t in s.state()["closed_trades"]] == [t["entry_ts"] for t in bt["trades"]]
    assert [t["net_brl"] for t in s.state()["closed_trades"]] == [t["net_brl"] for t in bt["trades"]]
    confirmed = [a for a in s.alerts.list("confirmed_setup")]
    assert len(confirmed) >= len(bt["trades"])
    assert all(re.match(r"[0-9a-f-]{36}", a.alert_id) for a in confirmed)
