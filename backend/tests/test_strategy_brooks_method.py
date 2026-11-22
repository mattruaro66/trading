"""Estratégia C (Método Brooks). Cenários SINTÉTICOS."""
from datetime import date, timedelta

import pytest

from atlas.backtest.bracket_sim import BracketSimulator
from atlas.backtest.costs import CostModel
from atlas.contracts.calendar import load_calendar
from atlas.core.types import Side
from atlas.market_data.synthetic import generate_bars
from atlas.pipeline import AnalysisPipeline
from atlas.strategy.brooks_method import REVERSAL_SETUPS, SETUP_NAMES, BrooksMethodParams
from tests.conftest import mk, uptrend
from tests.helpers import make_signal


@pytest.fixture(scope="module")
def run40():
    bars = generate_bars("WIN", "WINK26", date(2026, 1, 5), 40, seed=33)
    pipe = AnalysisPipeline("WIN", BrooksMethodParams(user_rules=False))
    outs = [pipe.on_bar_closed(b) for b in bars]
    return bars, pipe, outs


def test_two_lots_scalp_and_swing_with_breakeven():
    """Brooks: lote scalp em 1R e lote swing em 2R; atingido o scalp, o stop do swing vai para o zero a zero."""
    bars = mk([(100000, 100050, 99950, 100020),
               (100020, 100170, 100010, 100160),   # entra em 100055 e atinge o scalp (1R = 110 → 100165)
               (100160, 100170, 100040, 100050)])  # volta: o swing sai no zero a zero
    sim = BracketSimulator("WIN", CostModel(entry_slippage_ticks=0, exit_slippage_ticks=0))
    sim.on_bar(bars[0])
    base = make_signal(bars[0], entry=100055, stop=99945, target=100165)
    from dataclasses import replace
    sig = replace(base, extra_targets=(100275,), breakeven_after_first=True)
    sim.submit(sig, bars[0])
    sim.on_bar(bars[1])
    sim.on_bar(bars[2])
    by = {t.lot: t for t in sim.closed}
    assert by["scalp"].exit_reason == "target" and by["scalp"].points == 110
    assert by["swing"].exit_reason == "breakeven" and by["swing"].points == 0
    moved = [o for o in sim.orders if o.role == "stop_loss" and "zero a zero" in o.note]
    assert moved and moved[0].price == 100055


def test_every_setup_has_a_name_and_reversal_set_is_known():
    assert len(SETUP_NAMES) == 20
    assert REVERSAL_SETUPS <= set(SETUP_NAMES)


def test_no_lookahead_truncation(run40):
    bars, _, outs = run40
    k = 2500
    pipe = AnalysisPipeline("WIN", BrooksMethodParams(user_rules=False))
    outs2 = [pipe.on_bar_closed(b) for b in bars[:k]]
    sig = lambda os: [(o.bar.ts_open, s.setup_label, s.side, s.entry_price, s.stop_price) for o in os for s in o.signals]
    assert sig(outs2) == sig(outs[:k])


def test_context_rules_respected(run40):
    """Sem entradas em tight range; setups a favor só com tendência alinhada; contra a tendência só reversões/range."""
    _, _, outs = run40
    n = 0
    for o in outs:
        for s in o.signals:
            n += 1
            assert o.snapshot.lateral.counting_allowed
            reg = o.snapshot.regime.regime.value
            d = 1 if s.side is Side.LONG else -1
            if s.setup_label in ("S1", "S2", "S3", "S4", "S6", "S7", "S11", "S12", "S15", "S18", "S19", "S20"):
                assert reg == ("BULL_TREND" if d > 0 else "BEAR_TREND")
            if s.setup_label in ("S5", "S10"):
                assert reg == "TRADING_RANGE"
            risk_atr = next(c.value for c in s.criteria if c.code == "B4")
            assert risk_atr is None or risk_atr <= 2.5
            assert s.extra_targets and s.breakeven_after_first
    assert n > 100


def test_one_signal_per_direction_per_bar(run40):
    _, _, outs = run40
    for o in outs:
        sides = [s.side for s in o.signals]
        assert len(sides) == len(set(sides))


def test_brackets_scalp_1r_swing_2r(run40):
    _, pipe, _ = run40
    for t in pipe.sim.closed[:200]:
        s = t.signal
        r = s.risk_points
        assert abs(abs(s.target_price - s.entry_price) - r) <= 5
        assert abs(abs(s.extra_targets[0] - s.entry_price)) >= 2 * r - 5


def test_gap_reversal_setup():
    """Abertura com gap acima da última barra do pregão anterior → venda stop abaixo da 1ª barra (reversão do gap)."""
    day1 = uptrend(113)  # pregão completo (09:00–18:25)
    cal = load_calendar()
    s2 = cal.session("WIN", cal.next_trading_day(day1[0].ts_open.date()))
    hi = day1[-1].high
    day2 = mk([(hi + 150, hi + 200, hi + 100, hi + 110), (hi + 110, hi + 120, hi - 50, hi - 30)], start=s2.open)
    pipe = AnalysisPipeline("WIN", BrooksMethodParams(user_rules=False))
    outs = [pipe.on_bar_closed(b) for b in day1 + day2]
    assert outs[-2].snapshot.session_start
    sig = [s for s in outs[-2].signals if s.setup_label == "S16"]
    assert sig and sig[0].side is Side.SHORT and sig[0].entry_price == hi + 95


def test_trend_from_the_open_market_entry():
    day1 = uptrend(113)
    cal = load_calendar()
    s2 = cal.session("WIN", cal.next_trading_day(day1[0].ts_open.date()))
    last = day1[-1].close
    day2 = mk([(last, last + 65, last - 5, last + 60), (last + 60, last + 125, last + 55, last + 120),
               (last + 120, last + 160, last + 115, last + 150)], start=s2.open)
    for always_stop, otype in ((False, "market"), (True, "stop")):
        pipe = AnalysisPipeline("WIN", BrooksMethodParams(always_stop_entry=always_stop, user_rules=False))
        outs = [pipe.on_bar_closed(b) for b in day1 + day2]
        ev = [e for e in outs[-2].evaluations if e.setup_label == "S17"]
        assert ev  # tendência desde a abertura reconhecida no 2º candle do pregão
        sig = next(s for s in outs[-2].signals if s.setup_label in ("S17", "S18"))  # um sinal por direção
        assert sig.order_type == otype and sig.side is Side.LONG
        if otype == "stop":  # ativação 1 tick acima da barra de sinal, stop 1 tick abaixo dela
            sb = sig.signal_bar
            assert sig.entry_price == sb.high + 5 and sig.stop_price == sb.low - 5



# ----------------------------------------------------------------------- v1.2: regras da revisão do usuário
def test_user_rules_no_lookahead_and_criteria_enforced():
    bars = generate_bars("WIN", "WINK26", date(2026, 1, 5), 40, seed=33)
    full = AnalysisPipeline("WIN", BrooksMethodParams())
    outs = [full.on_bar_closed(b) for b in bars]
    k = 2500
    part = AnalysisPipeline("WIN", BrooksMethodParams())
    outs2 = [part.on_bar_closed(b) for b in bars[:k]]
    sig = lambda os: [(o.bar.ts_open, s.setup_label, s.side, s.entry_price, s.stop_price) for o in os for s in o.signals]
    assert sig(outs2) == sig(outs[:k])
    signals = [s for o in outs for s in o.signals]
    assert signals, "as regras não devem eliminar todos os sinais"
    for s in signals:
        crit = {c.code: c for c in s.criteria}
        assert all(crit[c].passed for c in ("U1", "U2", "U3", "U4", "U5", "U6"))
        b = s.signal_bar
        assert (b.close - b.open) * s.side.sign > 0  # nunca compra acima de barra negativa / vende abaixo de positiva
    rejected = {c.code for o in outs for e in o.evaluations for c in e.criteria if c.code.startswith("U") and not c.passed}
    assert {"U1", "U2"} <= rejected  # as regras de fato barram entradas
