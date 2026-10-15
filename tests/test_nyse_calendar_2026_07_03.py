"""2026-07-03 was a FULL NYSE closure ("Independence Day observed" for
Saturday July 4, per nyse.com, re-checked 2026-10-14). constants.py had
listed it as a 1pm early close, so the app treated it as a trading day and
wrote 8 daily_snapshots rows for a session that never happened."""
from datetime import date

import pytest

from stock_analyzer import constants as C
from stock_analyzer.data import is_trading_day

pytestmark = pytest.mark.fast


def test_2026_07_03_is_a_full_closure_not_an_early_close():
    assert "2026-07-03" in C.NYSE_HOLIDAYS
    assert "2026-07-03" not in C.NYSE_EARLY_CLOSES
    assert is_trading_day(date(2026, 7, 3)) is False


def test_2028_07_03_is_still_the_july_early_close():
    # The only July early close on NYSE's 2026-2028 calendar.
    assert C.NYSE_EARLY_CLOSES.get("2028-07-03") == 13.0
    assert is_trading_day(date(2028, 7, 3)) is True


def test_no_date_is_both_a_holiday_and_an_early_close():
    assert not (set(C.NYSE_HOLIDAYS) & set(C.NYSE_EARLY_CLOSES))


def test_2026_has_ten_full_closures():
    # nyse.com 2026: New Year's, MLK, Washington's Birthday, Good Friday,
    # Memorial, Juneteenth, Independence (obs.), Labor, Thanksgiving, Christmas.
    assert len([d for d in C.NYSE_HOLIDAYS if d.startswith("2026")]) == 10
