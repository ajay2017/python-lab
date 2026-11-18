"""Pure period + regenerate-guard decisions (stock_analyzer/report_periods.py).

docs/plans/acted-rule-and-report-periods.md, chunk C1 (owner U12-U14).
"""
import calendar
from datetime import date, datetime, timedelta

import pytest

from stock_analyzer import report_periods as rp
from stock_analyzer.report_labels import period_label


# ── monthly_period: the previous calendar month ───────────────────────────────

def test_first_sunday_of_december_covers_november():
    assert rp.monthly_period(date(2026, 12, 6)) == (date(2026, 11, 1), date(2026, 11, 30))


def test_the_day_before_the_first_sunday_is_still_the_previous_month():
    # Sat 12-05: any day of December reports November
    assert rp.monthly_period(date(2026, 12, 5)) == (date(2026, 11, 1), date(2026, 11, 30))


def test_mid_month_covers_the_previous_month():
    assert rp.monthly_period(date(2026, 8, 16)) == (date(2026, 7, 1), date(2026, 7, 31))


def test_jan_1_looks_back_into_the_previous_years_december():
    assert rp.monthly_period(date(2027, 1, 1)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_dec_31_covers_november():
    assert rp.monthly_period(date(2026, 12, 31)) == (date(2026, 11, 1), date(2026, 11, 30))


def test_leap_february_end_is_the_29th():
    assert rp.monthly_period(date(2028, 3, 4)) == (date(2028, 2, 1), date(2028, 2, 29))


def test_every_day_of_a_month_gives_the_same_window():
    # a forced retry on any day lands on the same period_end (the upsert key)
    got = {rp.monthly_period(date(2026, 11, d)) for d in range(1, 31)}
    assert got == {(date(2026, 10, 1), date(2026, 10, 31))}


def test_24_consecutive_months_have_no_gap_no_overlap_and_are_whole_months():
    prev_end = None
    y, m = 2025, 1
    for _ in range(24):
        # the first Sunday of month (y, m)
        d = date(y, m, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)
        assert d.weekday() == 6 and d.day <= 7
        start, end = rp.monthly_period(d)
        assert start.day == 1
        assert end.day == calendar.monthrange(end.year, end.month)[1]
        assert (start.year, start.month) == (end.year, end.month)
        if prev_end is not None:
            assert start == prev_end + timedelta(days=1), "gap or overlap"
        prev_end = end
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


# ── monthly_period_for_row ────────────────────────────────────────────────────

def test_row_window_from_strings_and_dates():
    assert rp.monthly_period_for_row(
        {"period_start": "2026-10-04", "period_end": "2026-11-01"}
    ) == (date(2026, 10, 4), date(2026, 11, 1))
    assert rp.monthly_period_for_row(
        {"period_start": date(2026, 11, 1), "period_end": datetime(2026, 11, 30, 12, 0)}
    ) == (date(2026, 11, 1), date(2026, 11, 30))


@pytest.mark.parametrize("row", [
    {}, {"period_start": None, "period_end": "2026-11-30"},
    {"period_start": "2026-11-01", "period_end": float("nan")},
    {"period_start": "garbage", "period_end": "2026-11-30"},
    {"period_start": "2026-11-30", "period_end": "2026-11-01"},
])
def test_row_window_unreadable_raises(row):
    with pytest.raises(ValueError):
        rp.monthly_period_for_row(row)


# ── regenerate_action ─────────────────────────────────────────────────────────

def test_viewer_is_always_refused():
    assert rp.regenerate_action(None, True) == "refused"
    assert rp.regenerate_action({"email_sent": True}, True) == "refused"


def test_an_emailed_row_is_preview_only():
    assert rp.regenerate_action({"email_sent": True}, False) == "preview_emailed"


@pytest.mark.parametrize("flag", [None, False, float("nan")])
def test_a_row_that_was_not_emailed_saves(flag):
    # NaN is what a DataFrame hands back for a missing cell; it must not read as sent
    assert rp.regenerate_action({"email_sent": flag}, False) == "save"
    assert rp.regenerate_action({}, False) == "save"


def test_no_row_saves():
    assert rp.regenerate_action(None, False) == "save"


def test_a_replaced_row_stays_protected_through_its_email_sent_at_stamp():
    # Replace rewrites email_sent to False but leaves the stamp: still "emailed once"
    row = {"email_sent": False, "email_sent_at": "2026-12-06T18:05:00-05:00"}
    assert rp.regenerate_action(row, False) == "preview_emailed"


@pytest.mark.parametrize("stamp", [None, float("nan"), "", "  "])
def test_a_blank_email_sent_at_does_not_protect(stamp):
    assert rp.regenerate_action({"email_sent": False, "email_sent_at": stamp}, False) == "save"


def test_pandas_nat_stamp_does_not_protect():
    import pandas as pd
    assert rp.regenerate_action({"email_sent": None, "email_sent_at": pd.NaT}, False) == "save"


def test_unreadable_saved_rows_give_a_preview_not_a_write():
    assert rp.regenerate_action(None, False, rows_checked=False) == "preview_unchecked"


def test_the_viewer_is_refused_even_when_the_rows_were_not_checked():
    assert rp.regenerate_action(None, True, rows_checked=False) == "refused"


def test_emailed_suffix_names_a_replaced_version():
    assert rp.emailed_suffix({"email_sent": True, "email_sent_at": "x"}) == " · ✉ emailed"
    assert rp.emailed_suffix({"email_sent": False, "email_sent_at": "2026-12-06T18:05:00"}) == \
        " · ✉ emailed version replaced"
    assert rp.emailed_suffix({"email_sent": None, "email_sent_at": None}) == ""
    assert rp.emailed_suffix({"email_sent": float("nan"), "email_sent_at": float("nan")}) == ""
    assert rp.emailed_suffix({}) == ""


# ── generated_et_text ─────────────────────────────────────────────────────────

def test_generated_et_text_converts_utc_to_eastern_across_dst():
    assert rp.generated_et_text("2026-07-01T18:05:00+00:00") == "2026-07-01 14:05 ET"   # EDT
    assert rp.generated_et_text("2026-12-06T18:05:00+00:00") == "2026-12-06 13:05 ET"   # EST
    assert rp.generated_et_text("2026-12-06T18:05:00") == "2026-12-06 13:05 ET"         # naive = UTC
    assert rp.generated_et_text("2026-12-07T01:30:00+00:00") == "2026-12-06 20:30 ET"   # prior ET day


def test_generated_et_text_unreadable_is_blank():
    assert rp.generated_et_text(None) == "" and rp.generated_et_text("garbage") == ""


# ── weekly_rows_for_period: the majority-of-week rule ─────────────────────────

def _wk(d):
    return {"week_ending": d}


def test_november_keeps_the_four_november_weeks_only():
    rows = [_wk(d) for d in ("2026-12-06", "2026-11-29", "2026-11-22", "2026-11-15",
                             "2026-11-08", "2026-11-01", "2026-10-25")]
    kept = [r["week_ending"] for r in rp.weekly_rows_for_period(
        rows, date(2026, 11, 1), date(2026, 11, 30))]
    # 12-06's week is Nov 30 - Dec 6 (December); 11-01's week is Oct 26 - Nov 1 (October)
    assert kept == ["2026-11-29", "2026-11-22", "2026-11-15", "2026-11-08"]


def test_each_week_lands_in_exactly_one_month_over_a_year():
    sundays = []
    d = date(2026, 1, 4)
    while d.year == 2026:
        sundays.append(_wk(d.isoformat()))
        d += timedelta(days=7)
    seen = 0
    for m in range(1, 13):
        start = date(2026, m, 1)
        end = (date(2026, m + 1, 1) if m < 12 else date(2027, 1, 1)) - timedelta(days=1)
        seen += len(rp.weekly_rows_for_period(sundays, start, end))
    assert seen == len(sundays)


def test_an_archived_month_uses_only_its_own_weeks():
    rows = [_wk(d) for d in ("2026-11-08", "2026-10-25", "2026-10-18", "2026-10-11",
                             "2026-10-04", "2026-09-27", "2026-09-20")]
    kept = [r["week_ending"] for r in rp.weekly_rows_for_period(
        rows, date(2026, 10, 1), date(2026, 10, 31))]
    assert kept == ["2026-10-25", "2026-10-18", "2026-10-11", "2026-10-04"]


def test_weekly_rows_accept_dates_and_drop_unreadable_rows():
    rows = [{"week_ending": date(2026, 11, 8)}, {"week_ending": None}, {"week_ending": "x"}, {}]
    assert len(rp.weekly_rows_for_period(rows, date(2026, 11, 1), date(2026, 11, 30))) == 1


def test_find_period_row_matches_str_and_date_values():
    rows = [{"period_end": "2026-10-31", "id": 1}, {"period_end": date(2026, 11, 30), "id": 2}]
    assert rp.find_period_row(rows, "period_end", date(2026, 11, 30))["id"] == 2
    assert rp.find_period_row(rows, "period_end", date(2026, 10, 31))["id"] == 1
    assert rp.find_period_row(rows, "period_end", date(2026, 9, 30)) is None
    assert rp.find_period_row([], "period_end", date(2026, 9, 30)) is None


# ── monthly_archive_options ───────────────────────────────────────────────────

def _row(start, end, **kw):
    return {"period_start": start, "period_end": end, **kw}


def test_legacy_october_and_new_november_do_not_share_an_entry():
    rows = [_row("2026-11-01", "2026-11-30"), _row("2026-10-04", "2026-11-01")]
    opts = rp.monthly_archive_options(rows)
    assert [lbl for lbl, _ in opts] == ["November 2026", "October 2026 (Oct 04 – Nov 01)"]
    assert [i for _, i in opts] == [0, 1]


def test_a_new_october_row_is_not_buried_by_a_legacy_row_ending_in_november():
    # Generate in November builds October 1-31; the legacy 10-04 -> 11-01 and
    # 10-11 -> 11-08 rows share its majority month but have later period_ends.
    rows = [_row("2026-10-11", "2026-11-08"), _row("2026-10-04", "2026-11-01"),
            _row("2026-10-01", "2026-10-31")]
    opts = rp.monthly_archive_options(rows)
    labels = [lbl for lbl, _ in opts]
    assert labels[0] == "October 2026" and opts[0][1] == 2, "calendar month listed first"
    assert len(opts) == 2, "one legacy October entry (newest period_end) plus the new one"
    assert opts[1] == ("October 2026 (Oct 11 – Nov 08)", 0)


def test_newest_row_per_month_wins_regardless_of_input_order():
    older = _row("2026-09-06", "2026-10-04", tag="older")
    newer = _row("2026-09-13", "2026-10-11", tag="newer")
    for rows in ([older, newer], [newer, older]):
        opts = rp.monthly_archive_options(rows)
        assert len(opts) == 1
        assert rows[opts[0][1]]["tag"] == "newer"


def test_label_and_content_index_come_from_the_same_row():
    rows = [
        _row("2026-11-01", "2026-11-30"),
        _row("2026-10-11", "2026-11-08"),       # legacy, stale
        _row("2026-10-04", "2026-11-01"),
        _row("2026-09-06", "2026-10-04"),
        _row("2026-08-09", "2026-09-06"),
    ]
    for lbl, idx in rp.monthly_archive_options(rows):
        r = rows[idx]
        # the label is report_labels' own for this row's window (a single-month,
        # non-calendar window gets its dates appended; see the next test)
        assert lbl.startswith(period_label(r["period_start"], r["period_end"]))


def test_every_non_calendar_label_spells_out_its_window():
    # two legacy windows inside ONE month: period_label alone reads both "October 2026"
    rows = [_row("2026-10-04", "2026-10-31"), _row("2026-09-27", "2026-10-24"),
            _row("2026-10-01", "2026-10-31")]
    labels = {rows[i]["period_start"]: lbl for lbl, i in rp.monthly_archive_options(rows)}
    assert labels["2026-10-01"] == "October 2026"                    # whole calendar month: bare
    assert labels["2026-10-04"] == "October 2026 (Oct 04 – Oct 31)"  # single-month legacy window
    # a legacy window spanning two months keeps the single window suffix (not doubled)
    two = rp.monthly_archive_options([_row("2026-10-04", "2026-11-01")])
    assert two == [("October 2026 (Oct 04 – Nov 01)", 0)]
    # two legacy windows in one month that both survive (different majority months here)
    # can never read identically
    all_labels = [lbl for lbl, _ in rp.monthly_archive_options(rows)]
    assert len(all_labels) == len(set(all_labels))


def test_entries_are_newest_month_first():
    rows = [_row("2026-11-01", "2026-11-30"), _row("2026-10-01", "2026-10-31"),
            _row("2026-09-01", "2026-09-30")]
    assert [lbl for lbl, _ in rp.monthly_archive_options(rows)] == [
        "November 2026", "October 2026", "September 2026"]


def test_an_unreadable_row_still_gets_its_own_entry():
    rows = [_row("2026-11-01", "2026-11-30"), _row(None, None), _row("x", float("nan"))]
    opts = rp.monthly_archive_options(rows)
    assert len(opts) == 3
    assert {lbl for lbl, _ in opts} >= {"November 2026", "Report 2", "Report 3"}


def test_empty_archive_has_no_entries():
    assert rp.monthly_archive_options([]) == []


# ── monthly_generate_window / selected_archive_row (picker keyed by period_end) ──

def _keys(rows):
    opts = rp.monthly_archive_options(rows)
    return opts, rp.archive_option_keys(rows, opts)


def test_archive_option_keys_are_the_rows_period_end():
    rows = [_row("2026-11-01", "2026-11-30"), _row("2026-10-04", "2026-11-01")]
    assert _keys(rows)[1] == ["2026-11-30", "2026-11-01"]


def test_default_target_is_the_last_completed_month_even_when_the_newest_row_is_stale():
    rows = [_row("2026-10-11", "2026-11-08"), _row("2026-10-04", "2026-11-01")]
    opts, keys = _keys(rows)
    # the newest entry is the picker default; it must not pin the legacy window
    assert rp.monthly_generate_window(date(2026, 12, 6), rows, opts, keys[0]) == (
        date(2026, 11, 1), date(2026, 11, 30))
    assert rp.monthly_generate_window(date(2026, 12, 6), rows, opts, None) == (
        date(2026, 11, 1), date(2026, 11, 30))


def test_an_archived_month_rebuilds_that_rows_own_window():
    rows = [_row("2026-11-01", "2026-11-30"), _row("2026-10-04", "2026-11-01")]
    opts, keys = _keys(rows)
    assert rp.monthly_generate_window(date(2026, 12, 6), rows, opts, keys[1]) == (
        date(2026, 10, 4), date(2026, 11, 1))


def test_a_row_inserted_while_the_page_is_open_cannot_change_the_window_built():
    # the owner picked the September row; the cron then inserts a newer row, which
    # would have shifted a list-POSITION selection onto a different row
    before = [_row("2026-10-01", "2026-10-31"), _row("2026-09-06", "2026-10-04")]
    opts, keys = _keys(before)
    picked = keys[1]
    after = [_row("2026-11-01", "2026-11-30")] + before
    opts2, keys2 = _keys(after)
    assert picked in keys2 and keys2.index(picked) == 2    # moved from position 1 to 2
    assert rp.monthly_generate_window(date(2026, 12, 6), after, opts2, picked) == (
        date(2026, 9, 6), date(2026, 10, 4))


def test_a_stale_or_unknown_selection_falls_back_to_the_default():
    rows = [_row("2026-11-01", "2026-11-30")]
    opts, _ = _keys(rows)
    for key in ("2026-01-31", 1, "", None):
        assert rp.monthly_generate_window(date(2026, 12, 6), rows, opts, key) == (
            date(2026, 11, 1), date(2026, 11, 30))


def test_an_archived_row_with_unreadable_dates_refuses_rather_than_guessing():
    rows = [_row("2026-11-01", "2026-11-30"), _row(None, None)]
    opts, keys = _keys(rows)
    assert len(keys) == 2 and keys[0] == "2026-11-30" and keys[1].startswith("row-")
    with pytest.raises(ValueError):
        rp.monthly_generate_window(date(2026, 12, 6), rows, opts, keys[1])
