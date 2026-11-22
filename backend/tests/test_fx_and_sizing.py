"""Forex (EURUSD/USDJPY, sessão Londres/NY) e dimensionamento por risco. Dados SINTÉTICOS."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from atlas.backtest.costs import CostModel
from atlas.backtest.engine import run_backtest
from atlas.contracts.calendar import load_calendar
from atlas.contracts.specs import get_spec
from atlas.market_data.importers import fx_server_to_utc, import_bars
from atlas.market_data.synthetic import generate_bars
from atlas.strategy.brooks_method import BrooksMethodParams
from atlas.strategy.h2l2_momentum import H2L2MomentumParams


def test_fx_specs_and_money():
    e, j = get_spec("EURUSD"), get_spec("USDJPY")
    assert e.kind == "fx" and e.account_currency == "USD" and e.lot_step == 0.01
    assert e.pnl_money(0.0020, 0.2) == 40.0           # 20 pips × 0,2 lote × US$ 10/pip
    assert j.pnl_money(0.20, 0.3, 150.0) == 40.0      # 20 pips de iene convertidos a 150
    assert get_spec("WIN").pnl_money(200, 5) == 200.0  # 200 pontos × 5 contratos × R$ 0,20


def test_sizing_respects_loss_limit():
    win, eur, jpy = get_spec("WIN"), get_spec("EURUSD"), get_spec("USDJPY")
    no_slip = CostModel(risk_per_trade=200, entry_slippage_ticks=0, exit_slippage_ticks=0)
    assert no_slip.quantity(win, 200, None) == 5  # 5 contratos = R$ 200 no stop de 200 pontos
    assert CostModel(risk_per_trade=200).quantity(win, 200, None) == 4  # com 1 tick de slippage por lado
    fx = CostModel(risk_per_trade=40, entry_slippage_ticks=0, exit_slippage_ticks=0, spread_ticks=0)
    q = fx.quantity(eur, 0.0020, 1.08)
    assert q == 0.19  # 0,20 lote estouraria US$ 40 com a margem de arredondamento
    assert eur.pnl_money(0.0020 + 0.00002, q) <= 40
    assert fx.quantity(jpy, 0.20, 150.0) > 0
    assert fx.quantity(eur, 0.5, 1.08) == 0  # stop enorme: nem o lote mínimo cabe → não opera
    assert fx.quantity(eur, 0.0020, 1.08, n_lots=2) == pytest.approx(0.09)  # orçamento dividido entre 2 lotes


def test_london_ny_session_follows_uk_dst():
    cal = load_calendar()
    summer = cal.session("EURUSD", date(2026, 7, 6))
    winter = cal.session("EURUSD", date(2026, 1, 5))
    assert (summer.open.hour, summer.close.hour) == (4, 13)  # 08:00–17:00 BST = 04:00–13:00 em Brasília
    assert (winter.open.hour, winter.close.hour) == (5, 14)  # 08:00–17:00 GMT = 05:00–14:00 em Brasília
    assert len(summer.bucket_opens()) == 108
    assert cal.is_trading_day(date(2026, 2, 16), "EURUSD")  # carnaval da B3 não fecha o forex
    assert not cal.is_trading_day(date(2026, 2, 16))


@pytest.mark.parametrize("monday,offset", [(date(2026, 1, 5), 2), (date(2026, 7, 6), 3)])
def test_mt5_server_timezone_detection(monday, offset):
    """Servidor UTC+2 no inverno / UTC+3 no verão de NY: a 1ª barra (segunda 00:00 do servidor) = domingo 17:00 NY."""
    ts = pd.Series(pd.date_range(datetime.combine(monday, datetime.min.time()), periods=12 * 24 * 5, freq="5min"))
    notes = []
    utc = fx_server_to_utc(ts, notes)
    first_ny = utc.iloc[0].tz_convert("America/New_York")
    assert (first_ny.weekday(), first_ny.hour) == (6, 17)
    assert str(offset) in notes[0]


def test_import_mt5_fx_csv_keeps_only_london_ny():
    start = datetime(2026, 1, 5)  # segunda, horário do servidor UTC+2
    idx = pd.date_range(start, periods=12 * 24 * 5, freq="5min")
    p = 1.08
    rows = []
    for t in idx:
        rows.append((t.strftime("%Y-%m-%d %H:%M:%S"), p, p + 0.0003, p - 0.0003, p + 0.0001, "", 100, 8))
        p = round(p + 0.0001, 5)
    csv = "timestamp,open,high,low,close,volume,tick_volume,spread\n" + "\n".join(",".join(map(str, r)) for r in rows)
    res = import_bars(csv.encode(), "EURUSD", "EURUSD", filename="EURUSD_M5.csv")
    per_day = pd.Series([b.ts_open.date() for b in res.bars]).value_counts()
    assert set(per_day.values) == {108}  # 08:00–17:00 de Londres em cada dia
    assert res.bars[0].ts_open.hour == 5  # 08:00 GMT = 05:00 em Brasília
    assert any("Londres/NY" in a for a in res.assumptions)


def test_fx_backtests_respect_loss_limit():
    bars = generate_bars("EURUSD", "EURUSD", date(2025, 6, 2), 30, seed=8)
    costs = CostModel(risk_per_trade=40, entry_slippage_ticks=0, exit_slippage_ticks=0, spread_ticks=5)
    for params, limit in ((H2L2MomentumParams(stop_adr_frac=0.10), 40), (BrooksMethodParams(), 20)):
        r = run_backtest(bars, "EURUSD", params, costs, min_sample=10)
        assert r["trades"]
        for t in r["trades"]:
            if t["exit_reason"] != "stop_gap":
                assert t["net_brl"] >= -limit - 1e-6


def test_usdbrl_as_wdo_proxy():
    s = get_spec("USDBRL")
    assert s.kind == "fx" and s.quote_currency == "BRL" and s.account_currency == "USD"
    assert s.pnl_money(0.0100, 1.0, 5.5) == pytest.approx(181.82)  # 100 pips de real convertidos a 5,50
    w = load_calendar().session("USDBRL", date(2026, 2, 16))  # carnaval: forex abre; horário do WDO
    assert (w.open.hour, w.close.hour, w.close.minute) == (9, 18, 30) and len(w.bucket_opens()) == 114
