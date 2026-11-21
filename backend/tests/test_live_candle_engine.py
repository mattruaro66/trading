"""Testes do Candle Engine com eventos SINTÉTICOS."""
from datetime import timedelta

import pytest

from atlas.core.types import BarStatus, Quality
from atlas.live.candle_engine import CandleEngine
from atlas.live.models import (
    CandleClosed, CandleInvalidated, CandleUpdated, DataGapDetected, EventType, MarketEvent,
)
from tests.conftest import session_open

T0 = session_open()


def trade(sec: float, price: float, qty=1.0, tid=None, seq=None, contract="WINJ26", etype=EventType.TRADE):
    ts = T0 + timedelta(seconds=sec)
    return MarketEvent("test", "WIN", contract, etype, ts, ts + timedelta(milliseconds=30), price, qty,
                       tid, seq, synthetic=True)


def kinds(evs, cls):
    return [e for e in evs if isinstance(e, cls)]


def feed(eng, evs):
    out = []
    for e in evs:
        out += eng.ingest(e)
    return out


def test_builds_5min_ohlcv_and_closes_after_grace():
    eng = CandleEngine("WIN", "WINJ26", "test")
    out = feed(eng, [trade(1, 100000, 2, "a"), trade(60, 100050, 3, "b"), trade(120, 99950, 1, "c"),
                     trade(299, 100010, 4, "d")])
    assert kinds(out, CandleUpdated) and not kinds(out, CandleClosed)
    assert kinds(out, CandleUpdated)[-1].bar.status is BarStatus.FORMING
    out = eng.ingest(trade(301, 100015, 1, "e"))  # 09:05:01 < 09:05:00 + 2s de tolerância
    assert not kinds(out, CandleClosed)
    out = eng.ingest(trade(303, 100020, 1, "f"))
    c = kinds(out, CandleClosed)[0].bar
    assert (c.open, c.high, c.low, c.close) == (100000, 100050, 99950, 100010)
    assert c.volume == 10 and c.trades == 4
    assert c.ts_open == T0 and c.ts_close == T0 + timedelta(minutes=5)
    assert c.status is BarStatus.CLOSED and c.quality is Quality.OK


def test_advance_clock_closes_idle_candle():
    eng = CandleEngine("WIN", "WINJ26", "test")
    eng.ingest(trade(10, 100000, 1, "a"))
    assert not kinds(eng.advance_clock(T0 + timedelta(seconds=301)), CandleClosed)
    assert kinds(eng.advance_clock(T0 + timedelta(seconds=302)), CandleClosed)


def test_quotes_never_build_candles():
    eng = CandleEngine("WIN", "WINJ26", "test")
    q = trade(5, 100000, None, etype=EventType.QUOTE)
    assert eng.ingest(q) == [] and eng.stats.quotes_ignored == 1 and not eng.open_buckets


def test_duplicates_by_trade_id_and_sequence():
    eng = CandleEngine("WIN", "WINJ26", "test")
    feed(eng, [trade(1, 100000, 1, "a", 1), trade(1, 100000, 1, "a", 1), trade(2, 100005, 1, None, 2),
               trade(2, 100005, 1, None, 2)])
    assert eng.stats.duplicates == 2 and eng.stats.accepted == 2


def test_no_identity_trades_are_not_dropped():
    eng = CandleEngine("WIN", "WINJ26", "test")
    feed(eng, [trade(1, 100000), trade(1, 100000)])
    assert eng.stats.accepted == 2 and eng.no_identity_warning


def test_out_of_order_within_candle_sets_correct_open_close():
    eng = CandleEngine("WIN", "WINJ26", "test")
    feed(eng, [trade(100, 100050, 1, "b", 2), trade(10, 100000, 1, "a", 1), trade(200, 100020, 1, "c", 3)])
    c = kinds(eng.advance_clock(T0 + timedelta(minutes=6)), CandleClosed)[0].bar
    assert c.open == 100000 and c.close == 100020
    assert eng.stats.out_of_order == 1 and c.quality is Quality.OK


def test_volume_unavailable_kept_null():
    eng = CandleEngine("WIN", "WINJ26", "test")
    feed(eng, [trade(1, 100000, None, "a"), trade(2, 100005, 3, "b")])
    c = kinds(eng.advance_clock(T0 + timedelta(minutes=6)), CandleClosed)[0].bar
    assert c.volume is None and "volume_unavailable" in c.quality_flags and c.quality is Quality.OK


def test_session_hours_enforced():
    eng = CandleEngine("WIN", "WINJ26", "test")
    assert eng.ingest(trade(-60, 100000, 1, "x")) == []  # 08:59
    assert eng.stats.rejected_outside_session == 1


def test_other_contract_rejected():
    eng = CandleEngine("WIN", "WINJ26", "test")
    assert eng.ingest(trade(1, 100000, 1, "x", contract="WINM26")) == []
    assert eng.stats.rejected_other == 1


def test_late_event_invalidates_closed_candle():
    eng = CandleEngine("WIN", "WINJ26", "test")
    feed(eng, [trade(1, 100000, 1, "a"), trade(310, 100010, 1, "b")])
    assert T0 in eng.closed
    out = eng.ingest(trade(200, 100500, 1, "late"))
    inv = kinds(out, CandleInvalidated)
    assert inv and inv[0].reason == "late_event" and inv[0].bar.status is BarStatus.INVALID
    assert kinds(out, DataGapDetected)[0].reason == "late_event"
    assert T0 not in eng.closed and T0 in eng.invalid_buckets


def test_sequence_gap_flags_candle_suspect():
    eng = CandleEngine("WIN", "WINJ26", "test")
    out = feed(eng, [trade(1, 100000, 1, "a", 1), trade(2, 100000, 1, "b", 5)])
    gap = kinds(out, DataGapDetected)[0]
    assert gap.reason == "sequence_gap" and gap.details == {"expected": 2, "received": 5}
    c = kinds(eng.advance_clock(T0 + timedelta(minutes=6)), CandleClosed)[0].bar
    assert c.quality is Quality.SUSPECT and "sequence_gap" in c.quality_flags


def test_empty_bucket_reports_gap_without_fake_candle():
    eng = CandleEngine("WIN", "WINJ26", "test")
    eng.ingest(trade(1, 100000, 1, "a"))
    out = eng.ingest(trade(700, 100010, 1, "b"))  # 09:11:40 → candle 09:05 sem negócios
    gaps = [g for g in kinds(out, DataGapDetected) if g.reason == "no_trades"]
    assert gaps and gaps[0].start == T0 + timedelta(minutes=5)
    assert T0 + timedelta(minutes=5) not in eng.closed


def test_degraded_connection_invalidates_candle():
    eng = CandleEngine("WIN", "WINJ26", "test")
    eng.ingest(trade(1, 100000, 1, "a"))
    eng.mark_degraded(T0 + timedelta(seconds=2))
    out = eng.advance_clock(T0 + timedelta(minutes=6))
    inv = kinds(out, CandleInvalidated)
    assert inv and inv[0].reason == "connection_degraded"


def test_rebuild_from_recovered_events():
    eng = CandleEngine("WIN", "WINJ26", "test")
    eng.ingest(trade(1, 100000, 1, "a", 1))
    eng.mark_degraded(T0 + timedelta(seconds=1))
    eng.advance_clock(T0 + timedelta(minutes=6))  # candle invalidado durante a queda
    recovered = [trade(1, 100000, 1, "a", 1), trade(100, 100100, 2, "b", 2), trade(250, 99900, 1, "c", 3),
                 trade(330, 100010, 1, "d", 4)]
    eng.mark_recovered()
    out = eng.rebuild(recovered, T0, T0 + timedelta(seconds=400))
    c = kinds(out, CandleClosed)[0].bar
    assert (c.open, c.high, c.low, c.close, c.volume) == (100000, 100100, 99900, 99900, 4)
    assert "rebuilt" in c.quality_flags and c.quality is Quality.OK
    assert T0 + timedelta(minutes=5) in eng.open_buckets  # candle em formação continua acumulando
    # novos negócios após a recuperação não duplicam os recuperados
    assert eng.ingest(trade(330, 100010, 1, "d", 4)) == []
