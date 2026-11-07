"""N1 C7: the pure per-ticker daily_snapshots planner for repaired account rows
(stock_analyzer/snapshot_row_repair.py)."""
import json
from datetime import date

import pytest

from stock_analyzer import snapshot_row_repair as S

pytestmark = pytest.mark.fast

TODAY = date(2026, 11, 7)
D = "2026-11-05"


def bk(**kw):
    """bk(AAA=(shares, close), ...)"""
    return {t: {"shares": s, "close": c} for t, (s, c) in kw.items()}


def srow(tk, sh, px=10.0):
    return {"ticker": tk, "shares": sh, "close_price": px}


def plan(book, rows, d=D, today=TODAY):
    return S.plan_snapshot_rows(d, book, rows, today)


def acts(p):
    return ([(u["ticker"], u["shares"], u["close_price"]) for u in p["upserts"]], p["deletes"])


# -- the planner -------------------------------------------------------------------

def test_missing_ticker_is_upserted_with_int_shares():
    p = plan(bk(AAA=(3.0, 10.0)), [])
    assert p["refusal"] is None and acts(p) == ([("AAA", 3, 10.0)], [])
    assert isinstance(p["upserts"][0]["shares"], int)


def test_short_ticker_is_topped_up():
    p = plan(bk(CSCO=(15.0, 60.0)), [srow("CSCO", 13, 60.0)])
    assert acts(p) == ([("CSCO", 15, 60.0)], [])


def test_equal_row_plans_nothing():
    p = plan(bk(AAA=(10.0, 10.0)), [srow("AAA", 10, 10.0)])
    assert p["refusal"] is None and acts(p) == ([], [])


def test_stored_shares_compare_as_numbers_not_types():
    p = plan(bk(AAA=(10.0, 10.0)), [{"ticker": "AAA", "shares": "10.0", "close_price": "10"}])
    assert acts(p) == ([], [])


def test_close_off_by_a_cent_is_upserted_but_within_half_a_cent_is_not():
    assert acts(plan(bk(AAA=(10.0, 10.01)), [srow("AAA", 10, 10.0)])) == ([("AAA", 10, 10.01)], [])
    assert acts(plan(bk(AAA=(10.0, 10.004)), [srow("AAA", 10, 10.0)])) == ([], [])


def test_stored_ticker_absent_from_the_book_is_deleted():
    p = plan(bk(AAA=(10.0, 10.0)), [srow("AAA", 10), srow("GONE", 4)])
    assert acts(p) == ([], ["GONE"])


def test_fractional_book_truncates_to_int_and_matches_a_stored_int():
    assert acts(plan(bk(AAA=(2.6, 10.0)), [srow("AAA", 2)])) == ([], [])
    assert acts(plan(bk(AAA=(2.6, 10.0)), [])) == ([("AAA", 2, 10.0)], [])


def test_sub_share_book_has_no_expected_row_and_deletes_a_stored_one():
    p = plan(bk(AAA=(10.0, 10.0), TINY=(0.5, 10.0)), [srow("AAA", 10), srow("TINY", 1)])
    assert p["refusal"] is None and acts(p) == ([], ["TINY"])
    assert acts(plan(bk(AAA=(10.0, 10.0), TINY=(0.5, 10.0)), [srow("AAA", 10)])) == ([], [])


def test_non_positive_close_has_no_expected_row():
    assert acts(plan(bk(AAA=(10.0, 10.0), BAD=(5.0, 0.0)), [srow("AAA", 10)])) == ([], [])


@pytest.mark.parametrize("d", ["2026-11-07", "2026-11-08", date(2026, 11, 7)])
def test_today_and_future_are_refused_with_no_actions(d):
    p = plan(bk(AAA=(3.0, 10.0)), [srow("GONE", 1)], d=d)
    assert p["refusal"]["kind"] == "today_or_future"
    assert p["upserts"] == [] and p["deletes"] == []


LATER = date(2026, 12, 1)       # a "today" after every date below, so only the calendar decides


@pytest.mark.parametrize("d", [
    "2026-10-31",      # Saturday
    "2026-11-01",      # Sunday (the fall-back DST day)
    "2026-03-08",      # Sunday (the spring-forward DST day)
    "2026-11-26",      # Thanksgiving (NYSE holiday, a Thursday)
    "2026-09-07",      # Labor Day (holiday, a Monday)
])
def test_weekend_and_holiday_dates_are_refused_non_trading_day(d):
    p = plan(bk(AAA=(3.0, 10.0)), [srow("GONE", 1)], d=d, today=LATER)
    assert p["refusal"]["kind"] == "non_trading_day" and p["date"] == d
    assert p["upserts"] == [] and p["deletes"] == []


@pytest.mark.parametrize("d", [
    "2026-11-02",      # the Monday right after the fall-back change
    "2026-03-09",      # the Monday right after the spring-forward change
    "2026-11-27",      # early close (1pm) is still a trading day
])
def test_dst_monday_and_early_close_are_planned_normally(d):
    p = plan(bk(AAA=(3.0, 10.0)), [], d=d, today=LATER)
    assert p["refusal"] is None and p["date"] == d and acts(p) == ([("AAA", 3, 10.0)], [])


def test_today_check_runs_before_the_calendar_check():
    # a future weekend date reads today_or_future, not non_trading_day
    assert plan(bk(AAA=(3.0, 10.0)), [], d="2026-11-08")["refusal"]["kind"] == "today_or_future"


def test_day_before_today_is_planned():
    assert plan(bk(AAA=(3.0, 10.0)), [], d="2026-11-06")["refusal"] is None


def test_unreadable_date_is_refused():
    p = plan(bk(AAA=(3.0, 10.0)), [], d="not a date")
    assert p["refusal"]["kind"] == "bad_date" and p["date"] is None


@pytest.mark.parametrize("book", [None, "{not json", "[1, 2]", 7, float("nan")])
def test_no_readable_book_is_a_no_book_refusal_and_never_a_delete_all(book):
    p = plan(book, [srow("AAA", 10), srow("BBB", 5)])
    assert p["refusal"]["kind"] == "no_book"
    assert p["upserts"] == [] and p["deletes"] == []


@pytest.mark.parametrize("book", [
    {"AAA": "garbage"}, {"AAA": {"shares": None, "close": 10.0}},
    {"AAA": {"shares": 3, "close": None}}, {"AAA": {"shares": "x", "close": 10.0}},
    {"AAA": {"shares": 3, "close": 10.0}, " aaa ": {"shares": 1, "close": 10.0}},
])
def test_a_malformed_book_entry_refuses_the_whole_date(book):
    p = plan(book, [srow("AAA", 10), srow("ZZZ", 5)])
    assert p["refusal"]["kind"] == "no_book" and p["deletes"] == []


def test_json_string_book_parses():
    p = plan(json.dumps(bk(AAA=(3.0, 10.0))), [])
    assert p["refusal"] is None and acts(p) == ([("AAA", 3, 10.0)], [])


def test_empty_book_with_rows_is_refused_not_a_delete_all():
    p = plan({}, [srow("AAA", 10)])
    assert p["refusal"]["kind"] == "empty_book" and p["deletes"] == []
    assert plan("{}", [srow("AAA", 10)])["refusal"]["kind"] == "empty_book"


@pytest.mark.parametrize("book", [
    bk(TINY=(0.5, 10.0)),                          # every entry truncates to 0 whole shares
    bk(A=(0.9, 10.0), B=(0.2, 5.0)),
    bk(BAD=(5.0, 0.0)),                            # close not > 0
    bk(NEG=(-3.0, 10.0), BAD=(5.0, -1.0)),
])
def test_a_book_implying_no_rows_while_rows_exist_is_refused_not_a_delete_all(book):
    p = plan(book, [srow("AAA", 10), srow("TINY", 1)])
    assert p["refusal"]["kind"] == "empty_book"
    assert p["upserts"] == [] and p["deletes"] == []


def test_a_book_implying_no_rows_with_no_stored_rows_is_a_clean_noop():
    p = plan(bk(TINY=(0.5, 10.0)), [])
    assert p["refusal"] is None and acts(p) == ([], [])


def test_some_sub_share_entries_still_delete_just_those_rows():
    p = plan(bk(AAA=(10.0, 10.0), TINY=(0.5, 10.0)), [srow("AAA", 10), srow("TINY", 1)])
    assert p["refusal"] is None and acts(p) == ([], ["TINY"])


def test_empty_book_with_no_rows_is_a_clean_noop():
    p = plan({}, [])
    assert p["refusal"] is None and acts(p) == ([], [])


def test_tickers_are_normalised_and_output_is_sorted():
    p = plan({" zzz ": {"shares": 1, "close": 5.0}, "aaa": {"shares": 2, "close": 5.0}},
             [{"ticker": " bbb ", "shares": 1, "close_price": 5.0}])
    assert acts(p) == ([("AAA", 2, 5.0), ("ZZZ", 1, 5.0)], ["BBB"])


def test_plan_is_idempotent_after_applying_it():
    book = bk(AAA=(10.0, 10.0), CRWD=(3.0, 400.0), FRAC=(2.6, 50.0), TINY=(0.4, 9.0))
    rows = [srow("AAA", 7, 10.0), srow("GONE", 4), srow("TINY", 1, 9.0), srow("FRAC", 2, 49.0)]
    p = plan(book, rows)
    assert p["upserts"] and p["deletes"]
    again = plan(book, S.apply_plan_to_rows(rows, p))
    assert again["refusal"] is None and acts(again) == ([], [])


def test_apply_plan_to_rows_ignores_a_refused_plan():
    rows = [srow("AAA", 1)]
    assert S.apply_plan_to_rows(rows, plan(None, rows)) == rows


def test_prod_fixture_2026_11_05():
    """Book CRWD 3, CSCO 15, DASH 10, EQ 4 vs stored CSCO 13, DASH 7, EQ 4:
    +CRWD 3, CSCO 13->15, DASH 7->10, nothing for the equal ticker."""
    book = bk(CRWD=(3.0, 480.0), CSCO=(15.0, 75.5), DASH=(10.0, 210.0), EQ=(4.0, 30.0))
    rows = [srow("CSCO", 13, 75.5), srow("DASH", 7, 210.0), srow("EQ", 4, 30.0)]
    p = plan(book, rows)
    assert acts(p) == ([("CRWD", 3, 480.0), ("CSCO", 15, 75.5), ("DASH", 10, 210.0)], [])
    assert p["previous"] == {"CRWD": None, "CSCO": 13.0, "DASH": 7.0}
    assert S.summarize([p]) == "snapshot rows: 2026-11-05 +CRWD 3, CSCO 13→15, DASH 7→10"


# -- summarize ---------------------------------------------------------------------

def test_summarize_skips_clean_and_refused_plans_and_names_deletes():
    clean = plan(bk(AAA=(1.0, 10.0)), [srow("AAA", 1)])
    refused = plan(None, [])
    gone = plan(bk(AAA=(1.0, 10.0)), [srow("AAA", 1), srow("XYZ", 2)], d="2026-10-28")
    assert S.summarize([clean, refused, gone]) == "snapshot rows: 2026-10-28 -XYZ"
    assert S.summarize([clean, refused]) == "snapshot rows: no change"
    assert S.summarize([]) == "snapshot rows: no change"


def test_summarize_caps_at_eight_dates():
    days = [1, 2, 5, 6, 7, 8, 9, 12, 13, 14, 15]          # 11 weekdays in Oct 2026
    plans = [plan(bk(AAA=(1.0, 10.0)), [], d=f"2026-10-{i:02d}") for i in days]
    line = S.summarize(plans)
    assert line.count("+AAA 1") == 8 and line.endswith("+3 more")
    assert "2026-10-12" in line and "2026-10-13" not in line
