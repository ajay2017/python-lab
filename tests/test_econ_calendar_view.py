"""Tests for stock_analyzer/econ_calendar_view.py (📅 Economic Calendar decisions).

Time-context matrix: same-day release before / at / after the clock, an unknown
release time, DST change days (UTC-aware "now"), a Sat / Sun visit, the
week-boundary tags (incl. the year end), and the month-after-value text.
"""
import re
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.econ_calendar_view import (
    card_value_lines, card_value_row, completed_day_tag, fred_enriched_count,
    is_done, kpis, split_events, tag_markdown, upcoming_day_tag,
)
from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast

HIGH = "HIGH"
MED = "MEDIUM"


def _et(y, mo, d, h=0, mi=0):
    return ET.localize(datetime(y, mo, d, h, mi))


def _ev(d, time_et="08:30", name="CPI Inflation", impact=HIGH, **kw):
    ev = {
        "date": d, "time_et": time_et, "event": name, "impact": impact,
        "category": "Inflation", "days_label": "x", "released": False,
        "previous": None, "estimate": None, "actual": None,
    }
    ev.update(kw)
    return ev


CPI_DAY = date(2026, 11, 10)          # a Tuesday


# ── is_done ──────────────────────────────────────────────────────────────────
def test_same_day_before_release_not_done():
    assert is_done(_ev(CPI_DAY), _et(2026, 11, 10, 8, 29)) is False


def test_same_day_at_release_minute_done():
    assert is_done(_ev(CPI_DAY), _et(2026, 11, 10, 8, 30)) is True


def test_same_day_unknown_time_not_done():
    assert is_done(_ev(CPI_DAY, time_et=None), _et(2026, 11, 10, 12, 0)) is False
    assert is_done(_ev(CPI_DAY, time_et="TBD"), _et(2026, 11, 10, 12, 0)) is False


def test_same_day_unknown_time_but_fred_released_is_done():
    assert is_done(_ev(CPI_DAY, time_et=None, released=True), _et(2026, 11, 10, 7, 0)) is True


def test_same_day_evening_done():
    assert is_done(_ev(CPI_DAY), _et(2026, 11, 10, 20, 0)) is True


def test_past_day_done_future_day_not():
    assert is_done(_ev(date(2026, 11, 9)), _et(2026, 11, 10, 6, 0)) is True
    assert is_done(_ev(date(2026, 11, 11)), _et(2026, 11, 10, 23, 59)) is False


def test_future_event_flagged_released_is_still_not_done():
    assert is_done(_ev(date(2026, 11, 11), released=True), _et(2026, 11, 10, 12)) is False


def test_bad_input_never_raises():
    assert is_done({}, _et(2026, 11, 10, 12)) is False
    assert is_done(_ev("2026-11-10"), _et(2026, 11, 10, 12)) is False


def test_dst_edt_utc_now():
    # 2026-03-09 is after the 03-08 spring-forward: 08:30 ET = 12:30Z
    ev = _ev(date(2026, 3, 9))
    assert is_done(ev, datetime(2026, 3, 9, 12, 29, tzinfo=timezone.utc)) is False
    assert is_done(ev, datetime(2026, 3, 9, 12, 31, tzinfo=timezone.utc)) is True


def test_dst_est_utc_now():
    # 2026-11-02 is after the 11-01 fall-back: 08:30 ET = 13:30Z
    ev = _ev(date(2026, 11, 2))
    assert is_done(ev, datetime(2026, 11, 2, 13, 29, tzinfo=timezone.utc)) is False
    assert is_done(ev, datetime(2026, 11, 2, 13, 31, tzinfo=timezone.utc)) is True


def test_utc_now_after_et_midnight_boundary():
    # 2026-11-11 01:00Z is still Tue 11-10 20:00 ET: today's event is done,
    # and a 11-11 event is still upcoming (UTC date would say it is today).
    now = datetime(2026, 11, 11, 1, 0, tzinfo=timezone.utc)
    assert is_done(_ev(date(2026, 11, 10)), now) is True
    assert is_done(_ev(date(2026, 11, 11)), now) is False


def test_naive_now_taken_as_et():
    assert is_done(_ev(CPI_DAY), datetime(2026, 11, 10, 8, 29)) is False
    assert is_done(_ev(CPI_DAY), datetime(2026, 11, 10, 8, 30)) is True


# ── split_events ─────────────────────────────────────────────────────────────
def test_split_orders_and_boundary():
    old = _ev(date(2026, 11, 6), name="NFP")
    older = _ev(date(2026, 11, 4), name="ISM")
    cpi = _ev(CPI_DAY)
    nxt = _ev(date(2026, 11, 12), name="PPI")
    later = _ev(date(2026, 11, 13), name="Retail")
    events = [older, old, cpi, nxt, later]
    up, done = split_events(events, _et(2026, 11, 10, 8, 29))
    assert up == [cpi, nxt, later]                  # input (ascending) order kept
    assert done == [old, older]                     # most recent first
    up, done = split_events(events, _et(2026, 11, 10, 8, 31))
    assert up == [nxt, later]
    assert done == [cpi, old, older]


def test_split_same_day_events_keep_time_order_in_completed():
    a = _ev(date(2026, 11, 9), time_et="08:30", name="A")
    b = _ev(date(2026, 11, 9), time_et="10:00", name="B")
    _, done = split_events([a, b], _et(2026, 11, 10, 9, 0))
    assert done == [a, b]


def test_split_empty():
    assert split_events(None, _et(2026, 11, 10)) == ([], [])


# ── kpis ─────────────────────────────────────────────────────────────────────
def test_kpis_next_major_skips_released_today_cpi():
    cpi = _ev(CPI_DAY, name="CPI Inflation", released=True, days_label="Today")
    ppi = _ev(date(2026, 11, 12), name="PPI", impact=HIGH, days_label="In 2d")
    med = _ev(date(2026, 11, 11), name="Jobless", impact=MED, days_label="Tomorrow")
    k = kpis([cpi, med, ppi], _et(2026, 11, 10, 9, 0))
    assert k["next_event"] is ppi
    assert k["next_name"] == "PPI"
    assert k["next_days_label"] == "In 2d"
    assert k["upcoming_count"] == 2          # the CPI is excluded
    assert k["high_count"] == 1
    assert k["week_count"] == 2


def test_kpis_before_release_cpi_is_next_and_counted():
    cpi = _ev(CPI_DAY, days_label="Today")
    ppi = _ev(date(2026, 11, 12), name="PPI")
    k = kpis([cpi, ppi], _et(2026, 11, 10, 8, 29))
    assert k["next_event"] is cpi
    assert (k["upcoming_count"], k["high_count"], k["week_count"]) == (2, 2, 2)


def test_kpis_falls_back_to_first_not_done_when_no_high():
    med = _ev(date(2026, 11, 11), name="Jobless", impact=MED, days_label="Tomorrow")
    k = kpis([med], _et(2026, 11, 10, 9, 0))
    assert k["next_event"] is med and k["high_count"] == 0


def test_kpis_empty_and_all_done():
    k = kpis([], _et(2026, 11, 10))
    assert k["next_event"] is None and k["next_name"] is None
    assert k["next_days_label"] == "" and k["upcoming_count"] == 0
    k = kpis([_ev(date(2026, 11, 9))], _et(2026, 11, 10))
    assert k["next_event"] is None and k["upcoming_count"] == 0


def test_kpis_week_count_is_the_trading_week_not_seven_days():
    # Tue 11-10: Fri 11-13 is this week; Mon 11-16 (6 days out) is next week.
    fri = _ev(date(2026, 11, 13), name="F")
    mon = _ev(date(2026, 11, 16), name="M")
    k = kpis([fri, mon], _et(2026, 11, 10, 9, 0))
    assert k["upcoming_count"] == 2 and k["week_count"] == 1


# ── upcoming_day_tag ─────────────────────────────────────────────────────────
def test_tag_today_and_tomorrow():
    assert upcoming_day_tag(date(2026, 11, 10), date(2026, 11, 10)) == "TODAY"
    assert upcoming_day_tag(date(2026, 11, 11), date(2026, 11, 10)) == "TOMORROW"


def test_tag_saturday_visit_monday_is_this_week():
    assert date(2026, 11, 14).weekday() == 5
    assert upcoming_day_tag(date(2026, 11, 16), date(2026, 11, 14)) == "this week"
    assert upcoming_day_tag(date(2026, 11, 20), date(2026, 11, 14)) == "this week"
    assert upcoming_day_tag(date(2026, 11, 23), date(2026, 11, 14)) == "next week"


def test_tag_sunday_visit_monday_is_tomorrow():
    assert date(2026, 11, 15).weekday() == 6
    assert upcoming_day_tag(date(2026, 11, 16), date(2026, 11, 15)) == "TOMORROW"
    assert upcoming_day_tag(date(2026, 11, 18), date(2026, 11, 15)) == "this week"


def test_tag_tuesday_to_next_tuesday_is_next_week():
    assert upcoming_day_tag(date(2026, 11, 17), date(2026, 11, 10)) == "next week"


def test_tag_same_week_later_day():
    assert upcoming_day_tag(date(2026, 11, 13), date(2026, 11, 10)) == "this week"


def test_tag_friday_to_monday_is_next_week():
    assert date(2026, 11, 13).weekday() == 4
    assert upcoming_day_tag(date(2026, 11, 16), date(2026, 11, 13)) == "next week"


def test_tag_year_end_uses_monday_dates_not_iso_week():
    wed = date(2026, 12, 30)
    assert wed.weekday() == 2
    assert upcoming_day_tag(date(2027, 1, 1), wed) == "this week"
    assert upcoming_day_tag(date(2027, 1, 4), wed) == "next week"
    assert upcoming_day_tag(date(2027, 1, 11), wed) == ""


def test_tag_beyond_next_week_and_past_and_bad():
    assert upcoming_day_tag(date(2026, 12, 1), date(2026, 11, 10)) == ""
    assert upcoming_day_tag(date(2026, 11, 9), date(2026, 11, 10)) == ""
    assert upcoming_day_tag(None, date(2026, 11, 10)) == ""


# ── completed_day_tag / tag_markdown ─────────────────────────────────────────
def test_completed_tag():
    t = date(2026, 11, 10)
    assert completed_day_tag(t, t) == "today"
    assert completed_day_tag(date(2026, 11, 9), t) == "yesterday"
    assert completed_day_tag(date(2026, 11, 7), t) == "3d ago"
    assert "0d" not in completed_day_tag(t, t)
    assert completed_day_tag(date(2026, 11, 11), t) == ""


def test_tag_markdown_matches_the_page_suffixes():
    assert tag_markdown("TODAY") == " — **TODAY**"
    assert tag_markdown("TOMORROW") == " — **TOMORROW**"
    assert tag_markdown("this week") == " — *this week*"
    assert tag_markdown("3d ago") == " — *3d ago*"
    assert tag_markdown("") == ""


# ── Post-Event filter (= is_done) ────────────────────────────────────────────
def test_post_event_filter_excludes_cpi_before_release_includes_after():
    cpi = _ev(CPI_DAY)
    assert [e for e in [cpi] if is_done(e, _et(2026, 11, 10, 8, 29))] == []
    assert [e for e in [cpi] if is_done(e, _et(2026, 11, 10, 8, 31))] == [cpi]


# ── card_value_lines ─────────────────────────────────────────────────────────
def test_released_same_day_card_shows_actual():
    ev = _ev(CPI_DAY, released=True, previous="CPI YoY: +3.74%",
             previous_period="Sep 2026", actual="CPI YoY: +3.90%",
             actual_period="Oct 2026")
    done = is_done(ev, _et(2026, 11, 10, 9, 0))
    assert done is True
    lines = card_value_lines(ev, done)
    assert lines == [
        "Prev: **CPI YoY: +3.74%** (Sep 2026)",
        "Actual: **CPI YoY: +3.90%** (Oct 2026)",
    ]
    assert card_value_row(ev, done) == "Prev: **CPI YoY: +3.74%** (Sep 2026)  ·  Actual: **CPI YoY: +3.90%** (Oct 2026)"


def test_actual_falls_back_to_released_flag_when_done_omitted():
    ev = _ev(CPI_DAY, released=True, actual="CPI YoY: +3.90%")
    assert card_value_lines(ev) == ["Actual: **CPI YoY: +3.90%**"]


def test_upcoming_card_shows_prev_and_est_not_actual():
    ev = _ev(date(2026, 11, 12), previous="NFP Chg: +228K", estimate="+200K",
             actual="NFP Chg: +1K")        # an actual can't exist pre-release; never shown
    assert card_value_lines(ev, False) == ["Prev: **NFP Chg: +228K**", "Est: **+200K**"]


def test_completed_card_hides_est():
    ev = _ev(date(2026, 11, 9), previous="P", estimate="E", actual="A")
    assert card_value_lines(ev, True) == ["Prev: **P**", "Actual: **A**"]


def test_no_values_gives_empty():
    assert card_value_lines(_ev(date(2026, 11, 12)), False) == []
    assert card_value_row(_ev(date(2026, 11, 12)), False) == ""


def test_not_computable_prev_line():
    # the 12-10 shape: a computable actual, a previous with no 12-month figure
    ev = _ev(date(2026, 12, 10), released=True,
             actual="CPI YoY: +3.80%", actual_period="Nov 2026",
             previous=None, previous_period="Oct 2026",
             previous_note="no 12-month figure: FRED has no Oct 2025 value")
    lines = card_value_lines(ev, True)
    assert lines[0] == ("Prev: **CPI YoY (Oct 2026): no 12-month figure**"
                        ": FRED has no Oct 2025 value")
    assert lines[1] == "Actual: **CPI YoY: +3.80%** (Nov 2026)"


def test_not_computable_actual_line_takes_label_from_previous():
    ev = _ev(date(2026, 12, 10), released=True,
             actual=None, actual_period="Oct 2026",
             actual_note="no 12-month figure: FRED has no Oct 2025 value",
             previous="CPI YoY: +3.74%", previous_period="Sep 2026")
    lines = card_value_lines(ev, True)
    assert lines[1] == ("Actual: **CPI YoY (Oct 2026): no 12-month figure**"
                        ": FRED has no Oct 2025 value")


def test_not_computable_label_falls_back_to_fred_map_then_event_name():
    ev = _ev(date(2026, 12, 10), released=True, actual=None,
             actual_period="Oct 2026", actual_note="no 12-month figure: gap")
    assert card_value_lines(ev, True)[0] == (
        "Actual: **CPI YoY (Oct 2026): no 12-month figure**: gap")
    other = _ev(date(2026, 12, 10), name="Some Other Event", released=True,
                actual=None, actual_note="n/a")
    assert card_value_lines(other, True) == ["Actual: **Some Other Event: n/a**"]


def test_month_goes_after_value_first_number_is_the_value():
    ev = _ev(date(2026, 11, 9), previous="CPI YoY: +3.74%", previous_period="Sep 2026",
             actual="CPI YoY: +3.90%", actual_period="Oct 2026", released=True)
    for line in card_value_lines(ev, True):
        bold = re.search(r"\*\*(.+?)\*\*", line).group(1)
        first = re.search(r"[-+]?\d+(?:\.\d+)?", bold).group(0)
        assert first in ("+3.74", "+3.90"), line         # never "2026"
        assert line.rstrip().endswith(")")               # month trails the value


def test_dollar_in_value_is_escaped():
    ev = _ev(date(2026, 11, 9), previous="Gap: $5 to $6", actual="A: $1", released=True)
    out = card_value_row(ev, True)
    assert "$" not in out.replace("\\$", "")


def test_missing_period_keys_tolerated():
    ev = _ev(date(2026, 11, 9), previous="P", actual="A", released=True)
    assert "actual_period" not in ev
    assert card_value_lines(ev, True) == ["Prev: **P**", "Actual: **A**"]


# ── fred_enriched_count ──────────────────────────────────────────────────────
def test_fred_enriched_count_counts_notes_too():
    evs = [
        _ev(date(2026, 11, 9)),                                   # nothing
        _ev(date(2026, 11, 9), previous="P"),
        _ev(date(2026, 11, 9), actual="A"),
        _ev(date(2026, 11, 9), previous_note="no 12-month figure: x"),
        _ev(date(2026, 11, 9), actual_note="no 12-month figure: x"),
    ]
    assert fred_enriched_count(evs) == 4
    assert fred_enriched_count([]) == 0
    assert fred_enriched_count(None) == 0
