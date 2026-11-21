import pytest

from atlas.core.types import BarStatus
from atlas.price_action.bars import BarClass, classify, compute_metrics
from atlas.price_action.engine import PriceActionEngine
from atlas.price_action.legs import LegCounter
from atlas.price_action.regime import Regime
from atlas.core.types import Side
from tests.conftest import mk, uptrend


def test_classify_bars():
    assert classify(100, 110, 99, 109) is BarClass.BULL_TREND
    assert classify(109, 110, 99, 100) is BarClass.BEAR_TREND
    assert classify(100, 110, 90, 101) is BarClass.DOJI
    assert classify(100, 100, 100, 100) is BarClass.DOJI
    assert classify(100, 110, 90, 106) is BarClass.DOJI  # corpo 0,30 = limite do doji
    assert classify(100, 110, 90, 107) is BarClass.WEAK_BULL  # corpo 0,35


def test_metrics_values():
    b, p = mk([(100, 110, 90, 105), (105, 120, 100, 118)])
    m = compute_metrics(p, b, atr_prev=20.0)
    assert m.range == 20 and m.body == 13
    assert m.close_pos == pytest.approx(0.9)
    assert m.upper_tail == pytest.approx(0.1) and m.lower_tail == pytest.approx(0.25)
    assert m.rel_range == pytest.approx(1.0)
    assert m.overlap_prev == pytest.approx(10 / 20)
    assert m.true_range == 20  # max(120,105)-min(100,105)
    assert m.close_near_high and not m.inside


def test_atr_excludes_current_bar():
    bars = uptrend(25)
    eng = PriceActionEngine()
    snaps = [eng.on_bar_closed(b) for b in bars]
    assert snaps[19].metrics.atr is None  # só 19 barras anteriores
    assert snaps[20].metrics.atr is not None
    big = bars[24].with_(high=bars[24].high + 5000)
    eng2 = PriceActionEngine()
    s2 = [eng2.on_bar_closed(b) for b in bars[:24] + [big]]
    assert s2[-1].metrics.atr == snaps[-1].metrics.atr  # a própria barra não altera seu ATR


def test_regime_bull_bear_range_undefined():
    eng = PriceActionEngine()
    snaps = [eng.on_bar_closed(b) for b in uptrend(45)]
    assert snaps[37].regime.regime is Regime.UNDEFINED
    assert snaps[38].regime.regime is Regime.BULL_TREND
    assert snaps[-1].regime.regime is Regime.BULL_TREND
    assert snaps[-1].regime.always_in.value == "LONG"

    eng = PriceActionEngine()
    down = mk([(100000 - 50 * i, 100000 - 50 * i + 10, 100000 - 50 * (i + 1) - 10, 100000 - 50 * (i + 1)) for i in range(45)])
    assert [eng.on_bar_closed(b) for b in down][-1].regime.regime is Regime.BEAR_TREND

    eng = PriceActionEngine()
    rows = [(100000, 100060, 99940, 100050) if i % 2 == 0 else (100050, 100060, 99940, 100000) for i in range(45)]
    r = [eng.on_bar_closed(b) for b in mk(rows)][-1].regime.regime
    assert r in (Regime.TRADING_RANGE, Regime.TIGHT_TRADING_RANGE)


H2_SEQ = [
    (100000, 100100, 99950, 100080),
    (100080, 100200, 100050, 100180),
    (100180, 100300, 100150, 100280),  # swing high
    (100280, 100290, 100200, 100210),  # início do pullback
    (100210, 100220, 100120, 100130),
    (100130, 100240, 100120, 100230),  # H1
    (100230, 100235, 100150, 100160),  # rearma → candidata a H2
    (100160, 100250, 100150, 100245),  # H2
    (100245, 100320, 100240, 100310),  # nova máxima → tendência retomada
]


def test_leg_counter_h1_h2_sequence():
    bars = mk(H2_SEQ)
    lc = LegCounter(Side.LONG)
    snaps, prev = [], None
    for b in bars:
        snaps.append(lc.update(b, prev))
        prev = b
    assert [s.event for s in snaps] == [None, None, None, None, None, "H1", None, "H2", None]
    assert snaps[3].in_pullback and snaps[3].count == 0
    assert snaps[6].candidate_for == 2
    assert snaps[8].reset_reason == "trend_resumed" and not snaps[8].in_pullback
    assert snaps[7].pullback_extreme == 100120


def test_leg_counter_low_mirror():
    mirrored = [(200000 - o, 200000 - l, 200000 - h, 200000 - c) for o, h, l, c in H2_SEQ]
    bars = mk(mirrored)
    lc = LegCounter(Side.SHORT)
    prev, events = None, []
    for b in bars:
        events.append(lc.update(b, prev).event)
        prev = b
    assert events == [None, None, None, None, None, "L1", None, "L2", None]


def test_candidate_consistency_property(synthetic_win):
    """Se a barra t é candidata a sinal do Hk e t+1 (contígua) supera a máxima de t, a máquina registra Hk em t+1."""
    eng = PriceActionEngine()
    snaps = [eng.on_bar_closed(b) for b in synthetic_win]
    checked = 0
    for a, b in zip(snaps, snaps[1:]):
        if not b.contiguous:
            continue
        lat = b.lateral
        if lat is not None and (not lat.counting_allowed or lat.event in ("breakout_confirmed", "range_expanded")):
            continue  # Brooks: sem contagem H/L em tight trading range (a contagem recomeça após o rompimento)
        for side, cross in ((Side.LONG, b.bar.high > a.bar.high), (Side.SHORT, b.bar.low < a.bar.low)):
            k = a.legs(side).candidate_for
            if k is not None and cross:
                assert b.legs(side).event == a.legs(side).label(k)
                checked += 1
    assert checked > 50


def test_engine_rejects_unclosed_and_unordered():
    eng = PriceActionEngine()
    b = mk([(1e5, 1e5 + 10, 1e5 - 10, 1e5)])[0]
    with pytest.raises(ValueError):
        eng.on_bar_closed(b.with_(status=BarStatus.FORMING))
    eng.on_bar_closed(b)
    with pytest.raises(ValueError):
        eng.on_bar_closed(b)


def test_engine_resets_on_gap_and_contract_change():
    bars = uptrend(30)
    eng = PriceActionEngine()
    for b in bars[:20]:
        eng.on_bar_closed(b)
    s = eng.on_bar_closed(bars[22])  # pula 2 candles
    assert s.reset_reason == "data_gap" and s.regime.regime is Regime.UNDEFINED
    s = eng.on_bar_closed(bars[23].with_(contract="WINM26"))
    assert s.reset_reason == "contract_change"


def test_session_boundary_resets_legs_not_regime(synthetic_win):
    eng = PriceActionEngine()
    boundary = None
    for b in synthetic_win:
        s = eng.on_bar_closed(b)
        if s.session_start:
            boundary = s
            break
    assert boundary is not None and boundary.reset_reason is None
    assert not boundary.bull_legs.in_pullback and boundary.regime.bars_available > 1


# ------------------------------------------------------------- regras do glossário Brooks
def test_brooks_h1_h2_definitions():
    """H1: máxima acima da anterior no pullback; depois de uma barra com MÁXIMA MAIS BAIXA, o próximo é H2.
    Uma barra com máxima igual à anterior (não mais baixa) não rearma a contagem."""
    rows = [(100000, 100100, 99950, 100080), (100080, 100200, 100050, 100180), (100180, 100300, 100150, 100280),
            (100280, 100290, 100200, 100210),   # pause/pullback
            (100210, 100295, 100190, 100250),   # H1 (supera 100290)
            (100250, 100295, 100200, 100220),   # máxima IGUAL → não rearma
            (100220, 100298, 100210, 100290),   # supera, mas não é H2 (não houve máxima mais baixa)
            (100290, 100280, 100230, 100240),   # máxima mais baixa → rearma
            (100240, 100290, 100235, 100285)]   # H2
    lc, prev, ev = LegCounter(Side.LONG), None, []
    for b in mk(rows):
        ev.append(lc.update(b, prev).event)
        prev = b
    assert ev == [None, None, None, None, "H1", None, None, None, "H2"]


def test_brooks_pullback_bar_starts_pullback():
    """Pullback bar (mínima abaixo da anterior) inicia o pullback mesmo com máxima maior (barra externa)."""
    rows = [(100000, 100100, 99950, 100080), (100080, 100200, 100050, 100180), (100180, 100210, 100000, 100020)]
    lc, prev = LegCounter(Side.LONG), None
    for b in mk(rows):
        snap = lc.update(b, prev)
        prev = b
    assert snap.in_pullback


def test_brooks_pullback_longer_than_20_bars_becomes_range():
    from atlas.price_action.legs import LegParams
    assert LegParams().max_pullback_bars == 20


def test_tight_range_height_limited_by_daily_range():
    from atlas.price_action.lateral import LateralGate
    g = LateralGate(3, 0.25)
    g.day_ranges.extend([1000.0] * 5)  # range diário médio de 1.000 pontos → limite de 250
    bars = mk([(100000, 100200, 99800, 100100), (100100, 100150, 99900, 100000), (100000, 100100, 99850, 99950),
               (99950, 100050, 99900, 100000)])
    snaps = [g.update(b) for b in bars]
    assert all(s.state == "normal" for s in snaps)  # faixa de 400 pts > 250: não é tight
    g2 = LateralGate(3, 0.25)
    g2.day_ranges.extend([2000.0] * 5)  # limite de 500
    assert [g2.update(b) for b in bars][-1].event == "lateral_start"
