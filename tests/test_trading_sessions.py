"""data.previous_trading_day / data.trading_sessions_between (TJDup D0)."""
from datetime import date

from stock_analyzer.data import previous_trading_day, trading_sessions_between

FRI = date(2026, 11, 6)
SAT = date(2026, 11, 7)
SUN = date(2026, 11, 8)
MON = date(2026, 11, 9)


def test_same_date_is_zero():
    assert trading_sessions_between(FRI, FRI) == 0
    assert trading_sessions_between(SAT, SAT) == 0


def test_weekend_boundaries():
    assert trading_sessions_between(FRI, SAT) == 0
    assert trading_sessions_between(FRI, SUN) == 0
    assert trading_sessions_between(FRI, MON) == 1
    assert trading_sessions_between(SAT, MON) == 1


def test_order_independent():
    assert trading_sessions_between(MON, FRI) == trading_sessions_between(FRI, MON) == 1


def test_thanksgiving_2026():
    assert trading_sessions_between(date(2026, 11, 25), date(2026, 11, 27)) == 1
    assert trading_sessions_between(date(2026, 11, 25), date(2026, 11, 26)) == 0
    assert trading_sessions_between(date(2026, 11, 24), date(2026, 11, 27)) == 2


def test_full_week():
    assert trading_sessions_between(date(2026, 11, 2), date(2026, 11, 6)) == 4


def test_previous_trading_day():
    assert previous_trading_day(MON) == FRI
    assert previous_trading_day(SAT) == FRI
    assert previous_trading_day(FRI) == date(2026, 11, 5)
    assert previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)   # skips Thanksgiving
