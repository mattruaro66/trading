"""Estratégia D (sempre dentro). Cenários SINTÉTICOS."""
from datetime import date

from atlas.core.types import Side
from atlas.market_data.synthetic import generate_bars
from atlas.pipeline import AnalysisPipeline
from atlas.strategy.always_in import AlwaysInParams
from tests.conftest import mk


def _run(rows, **kw):
    pipe = AnalysisPipeline("WIN", AlwaysInParams(**kw))
    return [pipe.on_bar_closed(b) for b in mk(rows)]


def flat(n=30, p=100000):
    return [(p, p + 40, p - 40, p + (10 if i % 2 else -10)) for i in range(n)]


def test_only_trades_after_confirmed_ema_break_and_only_that_direction():
    rows = flat()
    rows += [(100000, 100010, 99800, 99810),   # barra de força de baixa que fecha abaixo da média
             (99810, 99820, 99700, 99720),     # continuidade: fecha abaixo da média → dia de venda
             (99720, 99730, 99560, 99570)]     # nova barra de força de baixa → venda stop abaixo dela
    outs = _run(rows, ttr_max_range_atr=0.1)
    sig = [s for o in outs for s in o.signals]
    assert sig and all(s.side is Side.SHORT for s in sig)
    last = outs[-1].signals[0]
    assert last.order_type == "stop" and last.entry_price == 99560 - 5 and last.stop_price == 99730 + 5
    assert not any(o.signals for o in outs[:30])  # sem direção confirmada, nada


def test_no_trades_inside_lateral_range():
    rows = flat() + [(100000, 100010, 99900, 99905), (99905, 99910, 99860, 99870), (99870, 99880, 99800, 99805)]
    outs = _run(rows, ttr_max_range_atr=50)  # tudo é "lateral" com esse limite
    assert not any(o.signals for o in outs)
    rejected = [c for o in outs for e in o.evaluations for c in e.criteria if c.code == "D3" and not c.passed]
    assert rejected


def test_no_lookahead_truncation():
    bars = generate_bars("WIN", "WINK26", date(2026, 1, 5), 15, seed=5)
    full = AnalysisPipeline("WIN", AlwaysInParams())
    a = [(o.bar.ts_open, s.entry_price) for o in (full.on_bar_closed(b) for b in bars) for s in o.signals]
    part = AnalysisPipeline("WIN", AlwaysInParams())
    k = len(bars) // 2
    b_ = [(o.bar.ts_open, s.entry_price) for o in (part.on_bar_closed(b) for b in bars[:k]) for s in o.signals]
    assert b_ == [x for x in a if x[0] <= bars[k - 1].ts_open]
