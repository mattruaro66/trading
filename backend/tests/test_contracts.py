from datetime import date

import pytest

from atlas.contracts.calendar import load_calendar
from atlas.contracts.resolver import ContractResolver, RollPolicy
from atlas.contracts.specs import get_spec
from atlas.core.timeframe import assert_operational_timeframe


def test_win_spec_values():
    s = get_spec("WIN")
    assert s.tick_size == 5
    assert s.point_value_brl == 0.20
    assert s.tick_value_brl == pytest.approx(1.00)


def test_wdo_spec_values():
    s = get_spec("WDO")
    assert s.tick_size == 0.5
    assert s.point_value_brl == 10.0
    assert s.tick_value_brl == pytest.approx(5.00)


def test_specs_flagged_unvalidated():
    assert get_spec("WIN").validated is False  # até conferência com a B3


@pytest.mark.parametrize("sym,points,contracts,expected", [
    ("WIN", 100, 1, 20.00), ("WIN", -55, 3, -33.00), ("WIN", 5, 1, 1.00),
    ("WDO", 1.0, 1, 10.00), ("WDO", 0.5, 2, 10.00), ("WDO", -3.5, 1, -35.00),
])
def test_pnl_brl(sym, points, contracts, expected):
    assert get_spec(sym).pnl_brl(points, contracts) == pytest.approx(expected)


def test_round_to_tick():
    win, wdo = get_spec("WIN"), get_spec("WDO")
    assert win.round_to_tick(100002) == 100000
    assert win.round_to_tick(100003) == 100005
    assert win.round_to_tick(100004.9, "down") == 100000
    assert win.round_to_tick(100000.1, "up") == 100005
    assert wdo.round_to_tick(5000.26) == 5000.5
    assert wdo.round_to_tick(5000.74, "down") == 5000.5
    assert wdo.is_on_tick(5000.5) and not wdo.is_on_tick(5000.3)
    assert win.price_diff_to_ticks(25) == 5
    assert wdo.price_diff_to_ticks(2.0) == 4


def test_only_5min_timeframe():
    assert_operational_timeframe(5)
    with pytest.raises(ValueError):
        assert_operational_timeframe(15)


def test_calendar_holidays_and_sessions():
    cal = load_calendar()
    assert not cal.is_trading_day(date(2026, 2, 16))  # carnaval
    assert not cal.is_trading_day(date(2026, 2, 21))  # sábado
    assert cal.is_trading_day(date(2026, 3, 2))
    s = cal.session("WIN", date(2026, 3, 2))
    assert (s.open.hour, s.open.minute) == (9, 0)
    assert s.open.tzinfo is not None
    ash = cal.session("WIN", date(2026, 2, 18))
    assert ash.open.hour == 13  # quarta-feira de cinzas (a validar)
    assert cal.session("WIN", date(2026, 2, 17)) is None
    assert not cal.has_coverage(date(2031, 1, 6))


def test_bucket_alignment():
    s = load_calendar().session("WIN", date(2026, 3, 2))
    from datetime import timedelta
    assert s.bucket_open(s.open + timedelta(minutes=7, seconds=30)) == s.open + timedelta(minutes=5)
    with pytest.raises(ValueError):
        s.bucket_open(s.close)
    opens = s.bucket_opens()
    assert opens[0] == s.open and opens[-1] + timedelta(minutes=5) <= s.close


def test_parse_contract_codes():
    r = ContractResolver()
    c = r.parse("WINJ26")
    assert (c.symbol.value, c.month, c.year) == ("WIN", 4, 2026)
    with pytest.raises(ValueError):
        r.parse("WIN")  # genérico não identifica vencimento
    with pytest.raises(ValueError):
        r.parse("WINF26")  # WIN não vence em janeiro
    assert r.parse("WDOF26").month == 1


def test_win_expiry_wednesday_nearest_15th():
    r = ContractResolver()
    c = r.parse("WINJ26")  # 15/04/2026 é quarta
    assert c.expiry_date == date(2026, 4, 15)
    c = r.parse("WINM26")  # 15/06/2026 é segunda → quarta mais próxima 17/06
    assert c.expiry_date == date(2026, 6, 17)
    assert c.expiry_date.weekday() == 2


def test_wdo_expiry_first_business_day():
    r = ContractResolver()
    c = r.parse("WDOF26")  # 01/01/2026 feriado → 02/01
    assert c.expiry_date == date(2026, 1, 2)
    assert c.last_trading_day == date(2025, 12, 30)  # 31/12/2025 sem pregão
    c = r.parse("WDOJ26")
    assert c.expiry_date == date(2026, 4, 1)


def test_suggest_front_is_explicit_and_rolls():
    r = ContractResolver()
    s = r.suggest_front("WIN", date(2026, 3, 2))
    assert s.contract.code == "WINJ26" and "WINJ26" in s.reason
    s2 = r.suggest_front("WIN", date(2026, 4, 14), RollPolicy(days_before=2))
    assert s2.contract.code == "WINM26"
    assert s2.alternatives
