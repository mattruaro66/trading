"""Livro de ordens SIMULADO: o replay/backtest executa as ordens do método (Estratégia A) — nunca ordens reais."""
from atlas.backtest.costs import CostModel
from atlas.backtest.execution import TradeSimulator
from atlas.backtest.engine import run_backtest
from atlas.pipeline import AnalysisPipeline
from atlas.replay.session import ReplaySession
from atlas.strategy.second_entry import SecondEntryParams
from tests.conftest import mk
from tests.helpers import make_signal, trend_then_h2


def orders_by_role(sim):
    return {o.role: o for o in sim.orders}


def test_method_order_flow_target_cancels_stop():
    """Sinal H2 → stop de compra; execução → stop + alvo (OCO); alvo executa → stop cancelado."""
    pipe = AnalysisPipeline("WIN", SecondEntryParams())
    outs = [pipe.on_bar_closed(b) for b in trend_then_h2([(102180, 102290, 102170, 102285)])]
    placed = outs[-2].exec_events[-1]
    assert placed.kind == "order_placed"
    o = placed.orders[0]
    assert (o["role"], o["action"], o["type"], o["price"], o["status"], o["simulated"]) == \
           ("entry", "buy", "stop", 102190, "working", True)
    sim = pipe.sim
    r = orders_by_role(sim)
    assert r["entry"].status == "filled" and r["entry"].fill_price == 102195  # +1 tick de slippage
    assert r["stop_loss"].type == "stop" and r["stop_loss"].action == "sell" and r["stop_loss"].price == 102145
    assert r["take_profit"].type == "limit" and r["take_profit"].price == 102280
    assert r["take_profit"].status == "filled" and r["stop_loss"].status == "cancelled"
    assert len({x.trade_group for x in sim.orders}) == 1
    assert sim.position()["state"] == "flat" and sim.position()["realized_brl"] == sim.closed[0].net_brl


def test_stop_executes_and_cancels_target():
    bars = mk([(100000, 100050, 99950, 100020), (100020, 100060, 100000, 100040), (100040, 100045, 99800, 99850)])
    sim = TradeSimulator("WIN", CostModel(entry_slippage_ticks=0, exit_slippage_ticks=0))
    sim.on_bar(bars[0])
    sim.submit(make_signal(bars[0]), bars[0])
    sim.on_bar(bars[1])
    pos = sim.position(bars[1])
    assert pos["state"] == "long" and pos["unrealized_points"] == 100040 - 100055
    assert {o["role"] for o in pos["working_orders"]} == {"stop_loss", "take_profit"}
    evs = sim.on_bar(bars[2])
    roles = {o["role"]: o["status"] for o in evs[0].orders}
    assert roles == {"stop_loss": "filled", "take_profit": "cancelled"}


def test_entry_order_expires_when_not_triggered():
    pipe = AnalysisPipeline("WIN", SecondEntryParams())
    outs = [pipe.on_bar_closed(b) for b in trend_then_h2([(102180, 102185, 102130, 102140)])]
    ev = outs[-1].exec_events[0]
    assert ev.kind == "order_expired" and ev.orders[0]["status"] == "expired"
    assert pipe.sim.position()["state"] == "flat" and not pipe.sim._working


def test_time_exit_is_market_order_cancelling_oco():
    rows = [(100000, 100050, 99950, 100020)] + [(100040, 100060, 100000, 100040)] * 3
    bars = mk(rows)
    sim = TradeSimulator("WIN")
    sim.on_bar(bars[0])
    sim.submit(make_signal(bars[0], time_exit=2), bars[0])
    evs = [e for b in bars[1:] for e in sim.on_bar(b)]
    closed = [e for e in evs if e.kind == "closed"][0]
    st = {o["role"]: o["status"] for o in closed.orders}
    assert st == {"stop_loss": "cancelled", "take_profit": "cancelled", "market_exit": "filled"}
    assert all(o.status != "working" for o in sim.orders)


def test_order_book_consistent_with_trades(synthetic_win):
    """Toda operação tem exatamente uma entrada executada e uma saída executada; nenhuma ordem fica pendente sem posição."""
    params = SecondEntryParams.for_profile("scalp")
    s = ReplaySession(synthetic_win[0].symbol, synthetic_win[:2000], params)
    while not s.finished:
        s.step(200)
    sim = s.pipeline.sim
    filled_entries = [o for o in sim.orders if o.role == "entry" and o.status == "filled"]
    exits = [o for o in sim.orders if o.role != "entry" and o.status == "filled"]
    n_closed = len(sim.closed)
    assert len(filled_entries) - n_closed in (0, 1)
    assert len(exits) == n_closed
    for t, e, x in zip(sim.closed, filled_entries, exits):
        assert t.entry_price == e.fill_price and t.exit_price == x.fill_price
    bt = run_backtest(synthetic_win[:2000], "WIN", params)
    assert [t["entry_price"] for t in bt["trades"]] == [o.fill_price for o in filled_entries[: len(bt["trades"])]]
    assert sum(t.net_brl for t in sim.closed) == round(sim.realized_brl, 2) or abs(sum(t.net_brl for t in sim.closed) - sim.realized_brl) < 1e-6
    state = s.state()
    assert state["orders"] and all(o["simulated"] for o in state["orders"])
