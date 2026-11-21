"""Estratégia B (definida pelo usuário): H2/L2 + aumento no H3/L3 + momento. Cenários SINTÉTICOS."""
from datetime import timedelta

from atlas.core.types import Side
from atlas.pipeline import AnalysisPipeline
from atlas.strategy.h2l2_momentum import H2L2MomentumParams
from tests.conftest import mk, uptrend
from tests.helpers import trend_then_h2


def clean_h2(extra=None):
    """45 barras de alta + pullback em degraus (sem 3 barras sobrepostas): H1 e barra de sinal do H2."""
    base = uptrend(45)
    pb = [(102250, 102250, 102215, 102220),   # início do pullback
          (102210, 102210, 102120, 102130),   # continua
          (102130, 102215, 102125, 102210),   # H1
          (102115, 102115, 102030, 102100)]   # rearma → barra de sinal do H2 (entrada 102120)
    return base + mk(pb + (extra or []), start=base[-1].ts_close)


H2_FILL = (102100, 102125, 102100, 102120)  # aciona o H2 (102120 + 1 tick de slippage = 102125)


def run(bars, **kw):
    pipe = AnalysisPipeline("WIN", H2L2MomentumParams(**kw))
    return pipe, [pipe.on_bar_closed(b) for b in bars]


def after_trend(rows):
    base = uptrend(45)
    return base + mk(rows, start=base[-1].ts_close)


def test_h2_entry_with_fixed_200_300_bracket():
    pipe, outs = run(clean_h2([H2_FILL]))
    sig = next(s for s in outs[-2].signals if s.setup_label == "H2")
    assert (sig.side, sig.order_type, sig.entry_price) == (Side.LONG, "stop", 102120)
    lot = next(t for t in pipe.sim.lots if t.signal.setup_label == "H2")
    assert lot.entry_price == 102125
    assert lot.stop_price == 101920 and lot.target_price == 102420  # 200 / 300 pontos a partir do preço da ordem
    g = next(o.trade_group for o in pipe.sim.orders if o.role == "entry" and o.setup == "H2")
    roles = {o.role: o.status for o in pipe.sim.orders if o.trade_group == g}
    assert roles == {"entry": "filled", "stop_loss": "working", "take_profit": "working"}


def test_scale_in_on_h3_adds_one_contract():
    pipe, outs = run(clean_h2([H2_FILL,
                               (102100, 102100, 102010, 102020),     # rearma → barra de sinal do H3 (entrada 102105)
                               (102020, 102130, 102015, 102125)]))   # H3 executa
    h3 = [s for s in outs[-2].signals if s.setup_label == "H3"]
    assert h3 and h3[0].lot_role == "add" and h3[0].entry_price == 102105
    b = next(t for t in pipe.sim.lots if t.signal.setup_label == "H3")
    assert b.entry_price == 102110 and b.stop_price == 101905 and b.target_price == 102405
    assert any(t.signal.setup_label == "H2" for t in pipe.sim.lots)
    pos = pipe.sim.position()
    assert pos["qty"] == len(pipe.sim.lots) >= 2 and pos["state"] == "long"


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


def test_big_bar_entry():
    rows = [(102250, 102255, 102200, 102210), (102210, 102420, 102205, 102415), (102415, 102430, 102400, 102425)]
    pipe, outs = run(after_trend(rows))
    sig = next(s for s in outs[-2].signals if s.setup_label == "BARRA 2x")
    assert sig.order_type == "market"
    crit = {c.code: c for c in sig.criteria}
    assert crit["R3"].value > 2


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
    bars = clean_h2([H2_FILL, (102100, 102100, 102010, 102020), (102020, 102130, 102015, 102125),
                     (102125, 102140, 102115, 102130)])
    s = load_calendar().session("WIN", bars[0].ts_open.date())
    shift = (s.close - timedelta(minutes=5)) - bars[-1].ts_open
    bars = [b.with_(ts_open=b.ts_open + shift) for b in bars]
    pipe, outs = run(bars)
    assert not pipe.sim.lots
    assert sum(1 for t in pipe.sim.closed if t.exit_reason == "session_end") >= 2


def test_no_time_window_position_limit_or_priority():
    """O plano não tem janela de horário, exigência de posição, limite de contratos nem prioridade de sinais."""
    p = H2L2MomentumParams()
    for f in ("first_entry_time", "last_entry_time", "max_lots"):
        assert f not in H2L2MomentumParams.model_fields
    rows = [(102250, 102255, 102200, 102210),
            (102210, 102270, 102205, 102265), (102265, 102320, 102260, 102315), (102315, 102370, 102310, 102365),
            (102370, 102400, 102360, 102395), (102395, 102400, 102380, 102385),
            (102385, 102420, 102380, 102415), (102415, 102440, 102410, 102435), (102435, 102460, 102430, 102455),
            (102455, 102470, 102450, 102465)]
    pipe, outs = run(after_trend(rows))
    labels = [s.setup_label for o in outs for s in o.signals]
    assert labels.count("3 BARRAS") == 2  # segunda sequência executa mesmo com posição aberta
    assert len(pipe.sim.lots) == 2 and pipe.sim.position()["qty"] == 2
    assert p.stop_points == 200 and p.target_points == 300


def test_all_rules_triggered_on_same_bar_are_executed():
    # barra grande de alta que também é a 3ª barra seguida de alta: as duas regras executam
    rows = [(102250, 102255, 102200, 102210), (102210, 102240, 102205, 102235), (102235, 102260, 102230, 102255),
            (102255, 102470, 102250, 102465), (102465, 102480, 102455, 102475)]
    pipe, outs = run(after_trend(rows))
    assert {s.setup_label for s in outs[-2].signals} == {"BARRA 2x", "3 BARRAS"}
    assert len(pipe.sim.lots) == 2


def test_h3_executes_even_without_h2_lot():
    """O H2 é estopado; o H3 do mesmo pullback é executado mesmo assim (o plano não exige posição aberta)."""
    # stop menor só neste cenário, para a queda até o stop não tirar o mercado da tendência
    pipe, outs = run(clean_h2([H2_FILL,
                               (102095, 102100, 102010, 102020),    # stop do H2 (102020) + rearma → sinal do H3
                               (102020, 102130, 102015, 102125)]), stop_points=100)  # H3 executa (102105)
    h2 = next(t for t in pipe.sim.closed if t.signal.setup_label == "H2")
    assert h2.exit_reason == "stop"
    assert any(t.signal.setup_label == "H3" for t in pipe.sim.lots)


def test_data_quality_filter_blocks_signal():
    bars = clean_h2()
    bars[-1] = bars[-1].with_(quality=__import__("atlas.core.types", fromlist=["Quality"]).Quality.SUSPECT)
    _, outs = run(bars)
    ev = [e for e in outs[-1].evaluations if e.stage == "candidate"]
    assert ev and not ev[0].passed and not outs[-1].signals
    assert next(c for c in ev[0].criteria if c.code == "T3").passed is False


# ----------------------------------------------------------------------- lateralidade (regra do usuário)
def test_lateral_pullback_is_not_counted():
    """Pullback com 3 barras sobrepostas = lateralidade: nenhum H1/H2 é contado nem executado."""
    pipe, outs = run(trend_then_h2([(102180, 102200, 102170, 102195)]))
    lat = [o.snapshot.lateral for o in outs[-5:]]
    assert any(l.state != "normal" for l in lat)
    assert not any(o.snapshot.bull_legs.event for o in outs[-5:])
    assert not any(s.setup_label in ("H2", "H3") for o in outs for s in o.signals)


def test_counting_resumes_after_confirmed_breakout_with_follow_through():
    rows = [(102250, 102255, 102200, 102210), (102210, 102240, 102195, 102230), (102230, 102245, 102200, 102215),  # 3 sobrepostas
            (102215, 102240, 102205, 102225),   # ainda lateral
            (102225, 102300, 102220, 102295),   # rompimento: fecha acima da faixa (102255)
            (102295, 102340, 102290, 102335)]   # follow-through: fecha acima do fechamento do rompimento
    _, outs = run(after_trend(rows))
    ev = [o.snapshot.lateral.event for o in outs[-7:]]
    assert "lateral_start" in ev[:4] and ev[-2] == "breakout" and ev[-1] == "breakout_confirmed"
    assert all(not o.snapshot.lateral.counting_allowed for o in outs[-5:-1])
    assert outs[-1].snapshot.lateral.counting_allowed
    assert outs[-1].snapshot.bull_legs.count == 0 and not outs[-1].snapshot.bull_legs.in_pullback  # contagem recomeça do zero


def test_breakout_without_follow_through_fails_and_stays_lateral():
    rows = [(102250, 102255, 102200, 102210), (102210, 102240, 102195, 102230), (102230, 102245, 102200, 102215),
            (102215, 102300, 102210, 102290),   # rompimento
            (102290, 102295, 102230, 102240)]   # sem continuidade → falha
    _, outs = run(after_trend(rows))
    last = outs[-1].snapshot.lateral
    assert last.event == "breakout_failed" and last.state == "lateral" and not last.counting_allowed
    assert last.range_high == 102300  # faixa ampliada com a barra de rompimento


def test_strategy_a_unaffected_by_lateral_filter():
    from atlas.strategy.second_entry import SecondEntryParams
    pipe = AnalysisPipeline("WIN", SecondEntryParams())
    out = [pipe.on_bar_closed(b) for b in trend_then_h2()][-1]
    assert out.snapshot.lateral is None and out.signals  # estratégia A continua sem o filtro
