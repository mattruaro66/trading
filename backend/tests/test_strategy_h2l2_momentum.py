"""Estratégia B (definida pelo usuário): H2/L2 + aumento no H3/L3 + momento. Cenários SINTÉTICOS."""
from datetime import timedelta

from atlas.core.types import Side
from atlas.pipeline import AnalysisPipeline
from atlas.strategy.h2l2_momentum import H2L2MomentumParams
from tests.conftest import mk, uptrend
from tests.helpers import trend_then_h2


def run(bars, **kw):
    pipe = AnalysisPipeline("WIN", H2L2MomentumParams(**kw))
    return pipe, [pipe.on_bar_closed(b) for b in bars]


def after_trend(rows):
    base = uptrend(45)
    return base + mk(rows, start=base[-1].ts_close)


def test_h2_entry_with_fixed_200_300_bracket():
    pipe, outs = run(trend_then_h2([(102180, 102200, 102170, 102195)]))
    sig = outs[-2].signals[0]
    assert (sig.setup_label, sig.side, sig.order_type, sig.entry_price) == ("H2", Side.LONG, "stop", 102190)
    lot = pipe.sim.lots[0]
    assert lot.entry_price == 102195  # 102190 + 1 tick de slippage
    assert lot.stop_price == 101990 and lot.target_price == 102490  # 200 / 300 pontos a partir do preço da ordem
    roles = {o.role: o.status for o in pipe.sim.orders}
    assert roles == {"entry": "filled", "stop_loss": "working", "take_profit": "working"}


def test_scale_in_on_h3_adds_one_contract():
    pipe, outs = run(trend_then_h2([(102180, 102200, 102170, 102195),   # H2 executa
                                    (102195, 102195, 102150, 102190),   # rearma → barra de sinal do H3
                                    (102190, 102230, 102185, 102225)]))  # H3 executa
    h3 = outs[-2].signals
    assert h3 and h3[0].setup_label == "H3" and h3[0].lot_role == "add"
    assert len(pipe.sim.lots) == 2
    a, b = pipe.sim.lots
    assert (a.signal.setup_label, b.signal.setup_label) == ("H2", "H3")
    assert h3[0].entry_price == 102200 and b.entry_price == 102205  # 102195 + 1 tick; + 1 tick de slippage
    assert b.stop_price == 102000 and b.target_price == 102500  # bracket próprio do lote adicionado
    pos = pipe.sim.position()
    assert pos["qty"] == 2 and pos["state"] == "long"


def test_three_consecutive_bars_market_entry():
    rows = [(102250, 102255, 102200, 102210),  # barra de baixa interrompe a sequência
            (102210, 102270, 102205, 102265), (102265, 102320, 102260, 102315), (102315, 102370, 102310, 102365),
            (102370, 102400, 102360, 102395)]
    pipe, outs = run(after_trend(rows))
    sig = outs[-2].signals[0]
    assert sig.setup_label == "3 BARRAS" and sig.order_type == "market" and sig.side is Side.LONG
    lot = pipe.sim.lots[0]
    assert lot.entry_price == 102375  # abertura 102370 + slippage
    assert lot.stop_price == 102170 and lot.target_price == 102670


def test_big_bar_entry_has_priority_over_momentum():
    rows = [(102250, 102255, 102200, 102210), (102210, 102420, 102205, 102415), (102415, 102430, 102400, 102425)]
    pipe, outs = run(after_trend(rows))
    sig = outs[-2].signals[0]
    assert sig.setup_label == "BARRA 2x" and sig.order_type == "market"
    crit = {c.code: c for c in sig.criteria}
    assert crit["B1"].value > 2


def test_always_follows_trend_no_counter_trend_entries():
    rows = [(102250, 102255, 102230, 102235), (102235, 102240, 102215, 102220), (102220, 102225, 102200, 102205),
            (102205, 102210, 102190, 102195)]
    _, outs = run(after_trend(rows))
    sigs = [s for o in outs for s in o.signals]
    assert all(s.side is Side.LONG for s in sigs)  # nenhuma venda em tendência de alta


def test_no_entries_outside_trend(synthetic_win):
    pipe = AnalysisPipeline("WIN", H2L2MomentumParams())
    for b in synthetic_win[:1500]:
        out = pipe.on_bar_closed(b)
        for s in out.signals:
            reg = out.snapshot.regime.regime.value
            assert (s.side is Side.LONG and reg == "BULL_TREND") or (s.side is Side.SHORT and reg == "BEAR_TREND")


def test_brackets_and_exits_consistent(synthetic_win):
    pipe = AnalysisPipeline("WIN", H2L2MomentumParams())
    for b in synthetic_win[:3000]:
        pipe.on_bar_closed(b)
    sim = pipe.sim
    assert sim.closed
    for t in sim.closed:
        ref_stop = abs(t.entry_price - t.stop_price)
        assert ref_stop in (200 + 5, 200 + 10, 200 + 0) or t.entry_gap  # 200 pts + slippage de entrada
        if t.exit_reason == "target":
            assert t.points == 300 - 5
        if t.exit_reason == "stop":
            assert t.points == -(200 + 5 + 5)
        assert t.exit_reason in ("stop", "target", "stop_gap", "target_gap", "session_end", "data_gap_exit")
    assert len({o.trade_group for o in sim.orders if o.role == "entry"}) == len([o for o in sim.orders if o.role == "entry"])
    assert max(len([o for o in sim.orders if o.role == "entry" and o.status == "filled"]) - len(sim.closed), 0) <= 2


def test_session_end_flattens_all_lots():
    from atlas.contracts.calendar import load_calendar
    s = load_calendar().session("WIN", trend_then_h2()[0].ts_open.date())
    # desloca o cenário de H2 + H3 para terminar no fim da sessão
    bars = trend_then_h2([(102180, 102200, 102170, 102195), (102195, 102195, 102150, 102190),
                          (102190, 102230, 102185, 102225), (102225, 102240, 102215, 102230)])
    shift = (s.close - timedelta(minutes=5)) - bars[-1].ts_open
    bars = [b.with_(ts_open=b.ts_open + shift) for b in bars]
    pipe, outs = run(bars, last_entry_time=__import__("datetime").time(18, 30))
    assert not pipe.sim.lots
    assert sum(1 for t in pipe.sim.closed if t.exit_reason == "session_end") == 2
