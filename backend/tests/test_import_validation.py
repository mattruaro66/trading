import pandas as pd
import pytest

from atlas.market_data.importers import import_bars

HDR = "timestamp,open,high,low,close,volume\n"


def rows(times_prices):
    return "".join(f"2026-03-02 {t},{o},{h},{l},{c},{v}\n" for t, o, h, l, c, v in times_prices)


def test_canonical_csv_import():
    csv = HDR + rows([("09:00", 100000, 100050, 99950, 100020, 10), ("09:05", 100020, 100060, 100000, 100040, 5)])
    res = import_bars(csv.encode(), "WIN", "WINJ26", filename="x.csv")
    assert res.report.rows_valid == 2
    assert res.bars[0].ts_open.tzinfo is not None
    assert res.bars[0].contract == "WINJ26"
    assert any("America/Sao_Paulo" in a for a in res.assumptions)
    assert res.file_sha256 and not res.synthetic


def test_timestamp_label_close_shifts():
    csv = HDR + rows([("09:05", 100000, 100050, 99950, 100020, 10)])
    res = import_bars(csv.encode(), "WIN", "WINJ26", timestamp_label="close")
    assert res.bars[0].ts_open.strftime("%H:%M") == "09:00"


def test_rejects_inconsistent_ohlc_and_outside_session():
    csv = HDR + rows([
        ("09:00", 100000, 99000, 99950, 100020, 10),  # high < open
        ("08:00", 100000, 100050, 99950, 100020, 10),  # fora da sessão
        ("09:07", 100000, 100050, 99950, 100020, 10),  # desalinhado
        ("09:10", 100000, 100050, 99950, 100020, -1),  # volume negativo
        ("09:15", 100000, 100050, 99950, 100020, 1),
    ])
    res = import_bars(csv.encode(), "WIN", "WINJ26")
    kinds = {i.kind for i in res.report.issues}
    assert {"ohlc_inconsistent", "outside_session", "misaligned", "negative_volume"} <= kinds
    assert res.report.rows_valid == 1


def test_duplicates_identical_kept_once_conflicting_rejected():
    csv = HDR + rows([
        ("09:00", 100000, 100050, 99950, 100020, 10), ("09:00", 100000, 100050, 99950, 100020, 10),
        ("09:05", 100000, 100050, 99950, 100020, 10), ("09:05", 100005, 100050, 99950, 100020, 10),
    ])
    res = import_bars(csv.encode(), "WIN", "WINJ26")
    kinds = [i.kind for i in res.report.issues]
    assert "duplicate_identical" in kinds and "duplicate_conflict" in kinds
    assert [b.ts_open.strftime("%H:%M") for b in res.bars] == ["09:00"]


def test_gap_detected_not_filled():
    csv = HDR + rows([("09:00", 100000, 100050, 99950, 100020, 10), ("09:15", 100020, 100060, 100000, 100040, 5)])
    res = import_bars(csv.encode(), "WIN", "WINJ26")
    assert len(res.bars) == 2  # nada inventado
    assert res.report.gaps and res.report.gaps[0]["missing"] == 2


def test_off_tick_marked_suspect():
    csv = HDR + rows([("09:00", 100001, 100050, 99950, 100020, 10)])
    res = import_bars(csv.encode(), "WIN", "WINJ26")
    assert res.bars[0].quality.value == "suspect"


def test_rejects_other_timeframes():
    csv = HDR + rows([("09:00", 1e5, 1e5 + 50, 1e5 - 50, 1e5, 1), ("09:15", 1e5, 1e5 + 50, 1e5 - 50, 1e5, 1),
                      ("09:30", 1e5, 1e5 + 50, 1e5 - 50, 1e5, 1), ("09:45", 1e5, 1e5 + 50, 1e5 - 50, 1e5, 1)])
    with pytest.raises(ValueError, match="5 min"):
        import_bars(csv.encode(), "WIN", "WINJ26")


def test_resample_1m_to_5m():
    lines = []
    p = 100000
    for m in range(10):
        lines.append((f"09:{m:02d}", p, p + 10, p - 10, p + 5, 2))
        p += 5
    res = import_bars((HDR + rows(lines)).encode(), "WIN", "WINJ26", resample_from_1m=True)
    assert len(res.bars) == 2
    b = res.bars[0]
    assert b.open == 100000 and b.close == 100025 and b.high == 100030 and b.low == 99990 and b.volume == 10


def test_metatrader_format():
    txt = ("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
           "2026.03.02\t09:00:00\t100000\t100050\t99950\t100020\t500\t0\t0\n"
           "2026.03.02\t09:05:00\t100020\t100060\t100000\t100040\t400\t0\t0\n")
    res = import_bars(txt.encode(), "WIN", "WINJ26")
    assert res.detected_format == "metatrader5"
    assert res.bars[0].volume is None  # TICKVOL não vira volume
    assert len(res.bars) == 2


def test_portuguese_format():
    txt = ("data;hora;abertura;maxima;minima;fechamento;volume\n"
           "02/03/2026;09:00;5000,5;5003,0;4999,0;5001,5;100\n")
    res = import_bars(txt.encode(), "WDO", "WDOJ26")
    assert res.detected_format == "portugues"
    assert res.bars[0].open == 5000.5 and res.bars[0].ts_open.day == 2 and res.bars[0].ts_open.month == 3


def test_contract_mismatch_and_required():
    csv = "timestamp,open,high,low,close,contract\n2026-03-02 09:00,1,1,1,1,WINM26\n"
    with pytest.raises(ValueError, match="difere"):
        import_bars(csv.encode(), "WIN", "WINJ26")
    with pytest.raises(ValueError, match="Contrato obrigatório"):
        import_bars((HDR + rows([("09:00", 1e5, 1e5, 1e5, 1e5, 1)])).encode(), "WIN", None)


def test_parquet_import(tmp_path):
    df = pd.DataFrame({"timestamp": pd.to_datetime(["2026-03-02 09:00", "2026-03-02 09:05"]).tz_localize("America/Sao_Paulo"),
                       "open": [1e5, 1e5], "high": [1e5 + 50] * 2, "low": [1e5 - 50] * 2, "close": [1e5] * 2,
                       "volume": [1, 2]})
    p = tmp_path / "d.parquet"
    df.to_parquet(p)
    res = import_bars(p, "WIN", "WINJ26")
    assert res.report.rows_valid == 2


def test_parquet_store_roundtrip(tmp_path, synthetic_win):
    from atlas.market_data.parquet_store import load_bars, save_bars
    save_bars(tmp_path, synthetic_win[:50])
    back = load_bars(tmp_path, "WIN", "WINJ26")
    assert back == synthetic_win[:50]
