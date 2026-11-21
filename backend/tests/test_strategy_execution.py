from datetime import time

import pytest

from atlas.backtest.costs import CostModel
from atlas.backtest.execution import TradeSimulator
from atlas.core.types import Side
from atlas.pipeline import AnalysisPipeline
from atlas.strategy.second_entry import SecondEntryParams
from tests.conftest import mk
from tests.helpers import make_signal, trend_then_h2


def run(bars, **params):
    pipe = AnalysisPipeline("WIN", SecondEntryParams(**params))
    return pipe, [pipe.on_bar_closed(b) for b in bars]


def test_h2_signal_levels_and_criteria():
    bars = trend_then_h2()
    pipe, outs = run(bars)
    sig = outs[-1].signals
    assert len(sig) == 1
    s = sig[0]
    assert s.side is Side.LONG and s.setup_label == "H2"
    assert s.entry_price == 102190  # máxima da barra de sinal + 1 tick
    assert s.stop_price == 102145  # mínima − 1 tick
    assert s.risk_points == 45 and s.risk_ticks == 9
    assert s.target_price == 102280  # 2R (perfil daytrade)
    assert all(c.passed for c in s.criteria)
    assert outs[-1].exec_events[-1].kind == "order_placed"


def test_profile_scalp_target():
    pipe, outs = run(trend_then_h2(), profile="scalp")
    s = outs[-1].signals[0]
    assert s.target_price == 102235 and s.time_exit_bars == 6


def test_time_filter_and_disable_long_block_signal():
    _, outs = run(trend_then_h2(), last_entry_time=time(9, 30))
    assert not outs[-1].signals
    ev = [e for e in outs[-1].evaluations if e.stage == "candidate"][0]
    assert not next(c for c in ev.criteria if c.code == "C7").passed
    _, outs = run(trend_then_h2(), allow_long=False)
    assert not outs[-1].signals


def test_risk_limit_blocks():
    _, outs = run(trend_then_h2(), max_risk_ticks=5)
    assert not outs[-1].signals


def test_full_trade_target_hit():
    bars = trend_then_h2([(102180, 102290, 102170, 102285)])
    pipe, outs = run(bars)
    closed = [e for e in outs[-1].exec_events if e.kind == "closed"]
    assert closed and closed[0].reason == "target"
    t = closed[0].trade
    assert t.entry_price == 102195  # entrada + 1 tick de slippage
    assert t.exit_price == 102280
    assert t.points == 85 and t.gross_brl == pytest.approx(17.0)  # 85 pts × R$0,20


def test_order_expires_if_not_triggered():
    bars = trend_then_h2([(102180, 102185, 102130, 102140)])
    _, outs = run(bars)
    assert [e.kind for e in outs[-1].exec_events][0] == "order_expired"


# --------- simulador isolado ---------
def sim_with(signal_bar, next_bars, costs=None, **sig_kw):
    sim = TradeSimulator("WIN", costs or CostModel(entry_slippage_ticks=0, exit_slippage_ticks=0))
    sim.on_bar(signal_bar)
    sim.submit(make_signal(signal_bar, **sig_kw), signal_bar)
    evs = []
    for b in next_bars:
        evs += sim.on_bar(b)
    return sim, evs


def test_entry_bar_touching_stop_is_conservative_loss():
    bars = mk([(100000, 100050, 99950, 100020), (100020, 100200, 99900, 100100)])
    sim, evs = sim_with(bars[0], bars[1:])
    t = sim.closed[0]
    assert t.exit_reason == "stop" and t.points < 0  # stop tocado na própria barra de entrada


def test_same_bar_stop_and_target_resolves_stop():
    bars = mk([(100000, 100050, 99950, 100020), (100020, 100060, 100000, 100040), (100040, 100400, 99800, 100100)])
    sim, _ = sim_with(bars[0], bars[1:])
    t = sim.closed[0]
    assert t.exit_reason == "stop" and t.ambiguous_bar and t.points < 0


def test_gap_entry_and_gap_stop():
    bars = mk([(100000, 100050, 99950, 100020), (100100, 100120, 100090, 100110), (99800, 99850, 99700, 99750)])
    sim, _ = sim_with(bars[0], bars[1:])
    t = sim.closed[0]
    assert t.entry_gap and t.entry_price == 100100  # abertura acima da ordem
    assert t.exit_reason == "stop_gap" and t.exit_price == 99800  # pior que o stop


def test_gap_beyond_target_fills_at_target_not_open():
    bars = mk([(100000, 100050, 99950, 100020), (100020, 100060, 100000, 100040), (100500, 100600, 100450, 100550)])
    sim, _ = sim_with(bars[0], bars[1:])
    t = sim.closed[0]
    assert t.exit_reason == "target_gap" and t.exit_price == t.target_price


def test_time_exit():
    rows = [(100000, 100050, 99950, 100020)] + [(100040, 100060, 100000, 100040)] * 4
    bars = mk(rows)
    sim, _ = sim_with(bars[0], bars[1:], time_exit=3)
    t = sim.closed[0]
    assert t.exit_reason == "time_exit" and t.bars_held == 3


def test_session_end_flatten(synthetic_win):
    from atlas.contracts.calendar import load_calendar
    s = load_calendar().session("WIN", synthetic_win[0].ts_open.date())
    from datetime import timedelta
    start = s.close - timedelta(minutes=15)
    bars = mk([(100000, 100050, 99950, 100020), (100020, 100060, 100000, 100040), (100040, 100060, 100000, 100040)],
              start=start)
    sim, _ = sim_with(bars[0], bars[1:])
    assert sim.closed[0].exit_reason == "session_end"


def test_data_gap_exit_and_cancel():
    from atlas.core.timeframe import TIMEFRAME
    bars = mk([(100000, 100050, 99950, 100020), (100020, 100060, 100000, 100040)])
    later = mk([(100040, 100060, 100000, 100040)], start=bars[-1].ts_close + TIMEFRAME)
    sim, evs = sim_with(bars[0], bars[1:] + later)
    assert sim.closed[0].exit_reason == "data_gap_exit"
    # ordem pendente com candle seguinte ausente → cancelada
    sim2 = TradeSimulator("WIN")
    sim2.on_bar(bars[0])
    sim2.submit(make_signal(bars[0]), bars[0])
    evs = sim2.on_bar(later[0])
    assert evs[0].kind == "order_cancelled"


def test_costs_and_slippage_wdo():
    bars = mk([(5000.0, 5002.0, 4999.0, 5001.5), (5001.5, 5020.0, 5001.0, 5019.0)], symbol="WDO", contract="WDOJ26")
    costs = CostModel(contracts=2, brokerage_per_contract_side=1.0, exchange_fees_per_contract_side=0.5,
                      entry_slippage_ticks=1, exit_slippage_ticks=1)
    sim = TradeSimulator("WDO", costs)
    sim.on_bar(bars[0])
    sim.submit(make_signal(bars[0], entry=5002.5, stop=4998.5, target=5010.5), bars[0])
    sim.on_bar(bars[1])
    t = sim.closed[0]
    assert t.entry_price == 5003.0 and t.exit_price == 5010.5
    assert t.points == pytest.approx(7.5)
    assert t.gross_brl == pytest.approx(150.0)  # 7,5 pts × R$10 × 2
    assert t.costs_brl == pytest.approx(6.0)  # (1,0+0,5) × 2 lados × 2 contratos
    assert t.net_brl == pytest.approx(144.0)
    assert t.net_r == pytest.approx(144.0 / (4.0 * 10 * 2))


def test_one_position_at_a_time():
    bars = mk([(100000, 100050, 99950, 100020), (100020, 100060, 100000, 100040)])
    sim = TradeSimulator("WIN")
    sim.on_bar(bars[0])
    assert sim.submit(make_signal(bars[0]), bars[0]).kind == "order_placed"
    assert sim.submit(make_signal(bars[0]), bars[0]).kind == "signal_skipped"


def test_cost_warnings():
    assert CostModel().warnings()
    assert not CostModel(brokerage_per_contract_side=0.1, spread_ticks=0.5).warnings()
