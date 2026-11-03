"""Pure report labels: period naming and ET generated date."""
from datetime import date, datetime, timezone

from stock_analyzer.report_labels import (
    generated_et_date, period_label, period_month_key,
)


def test_period_crossing_a_month_names_the_majority_month_with_range():
    assert period_label("2026-10-04", "2026-11-01") == "October 2026 (Oct 04 – Nov 01)"


def test_period_inside_a_month_is_the_bare_month():
    assert period_label("2026-10-01", "2026-10-31") == "October 2026"
    assert period_label(date(2026, 3, 2), date(2026, 3, 29)) == "March 2026"


def test_even_split_has_no_majority_so_shows_the_range():
    # Oct 18 - Nov 14 = 14 days each side
    assert period_label("2026-10-18", "2026-11-14") == "Oct 18 – Nov 14"


def test_key_groups_by_majority_month_not_end_month():
    assert period_month_key("2026-10-04", "2026-11-01") == (2026, 10)
    assert period_month_key("2026-10-01", "2026-10-31") == (2026, 10)
    assert period_month_key("2026-11-02", "2026-11-29") == (2026, 11)


def test_unreadable_dates_give_none():
    assert period_label(None, "2026-11-01") is None
    assert period_label("nope", "2026-11-01") is None
    assert period_label("2026-11-02", "2026-11-01") is None
    assert period_month_key(None, None) is None


def test_generated_at_0100_utc_is_the_previous_et_day():
    assert generated_et_date("2026-11-02T01:00:00+00:00") == "2026-11-01"
    assert generated_et_date(datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)) == "2026-11-01"


def test_naive_generated_at_is_treated_as_utc():
    assert generated_et_date("2026-11-02 01:00:00") == "2026-11-01"


def test_generated_at_midday_keeps_its_date_and_garbage_is_empty():
    assert generated_et_date("2026-11-02T15:00:00+00:00") == "2026-11-02"
    assert generated_et_date(None) == ""
    assert generated_et_date("not a date") == ""
