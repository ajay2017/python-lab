"""session_label: which session a live change_pct describes."""
import datetime as dt

import pytest
import pytz

from stock_analyzer.session_label import move_when_phrase, price_move_session

pytestmark = pytest.mark.fast
ET = pytz.timezone("America/New_York")


def _et(y, m, d, h=12, mi=0):
    return ET.localize(dt.datetime(y, m, d, h, mi))


def test_saturday_points_at_friday():
    assert price_move_session(_et(2026, 10, 31)) == (False, dt.date(2026, 10, 30))
    assert move_when_phrase(_et(2026, 10, 31)) == "in the last session (Fri Oct 30)"


def test_sunday_points_at_friday():
    assert price_move_session(_et(2026, 11, 1)) == (False, dt.date(2026, 10, 30))


def test_monday_before_open_after_dst_points_at_friday():
    # DST ended Sun 2026-11-01; Mon 08:00 EST is before the open.
    assert price_move_session(_et(2026, 11, 2, 8)) == (False, dt.date(2026, 10, 30))
    assert move_when_phrase(_et(2026, 11, 2, 8)).endswith("(Fri Oct 30)")


def test_weekday_holiday_points_at_prior_session():
    # Thanksgiving 2026-11-26 is in constants.NYSE_HOLIDAYS.
    assert price_move_session(_et(2026, 11, 26, 11)) == (False, dt.date(2026, 11, 25))


def test_open_boundary():
    assert price_move_session(_et(2026, 11, 3, 9, 29)) == (False, dt.date(2026, 11, 2))
    assert price_move_session(_et(2026, 11, 3, 9, 30)) == (True, dt.date(2026, 11, 3))
    assert move_when_phrase(_et(2026, 11, 3, 10)) == "today"


def test_walk_back_over_long_weekend():
    # Mon 2026-09-07 (Labor Day) -> last session is Fri 2026-09-04.
    assert price_move_session(_et(2026, 9, 7, 11)) == (False, dt.date(2026, 9, 4))
    # Tue after the long weekend, before the open -> still Fri (Mon was a holiday).
    assert price_move_session(_et(2026, 9, 8, 8)) == (False, dt.date(2026, 9, 4))
    assert move_when_phrase(_et(2026, 9, 7, 11)) == "in the last session (Fri Sep 4)"
