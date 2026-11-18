"""Pure period + regenerate-guard decisions for the saved periodic reports.

No Streamlit, no DB. Exists so the decisions that determine WHICH window a
report covers, and WHETHER a regenerate may overwrite a saved row, are
unit-tested instead of living as conditionals in `app.py` / `cron_runner.py`
(docs/plans/acted-rule-and-report-periods.md, chunk C1).

Why calendar months (owner U12, 2026-11-17): the old monthly window was "the
most recent Sunday minus 28 days". First Sundays are 4 or 5 weeks apart, so
the windows left gaps (2026-11-02 -> 11-07 sat in no report) and an on-demand
click on a weekday produced a rolling window that hid the real month's row in
the archive. A report generated in month M now covers the whole of month M-1,
so `monthly_reports.period_end` (the upsert key) is always a month's last day
and a forced re-run on any day of M lands on the same row.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from stock_analyzer.market_time import ET
from stock_analyzer.report_labels import period_label, period_month_key


def _to_date(v) -> date | None:
    """A stored date as a `date`: accepts date / datetime / Timestamp / ISO text."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def monthly_period(today: date) -> tuple[date, date]:
    """The previous calendar month as (first day, last day).

    January looks back into the previous year's December. Pass the ET date
    (`market_time.today_et()` / `now_et.date()`), never a UTC one.
    """
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def monthly_period_for_row(row) -> tuple[date, date]:
    """The row's own stored (period_start, period_end), so regenerating an
    archived month rebuilds THAT window (U13), not the latest one.

    Raises ValueError when either date is missing or unreadable: the caller
    must say so and generate nothing, never guess a window.
    """
    s = _to_date(row.get("period_start"))
    e = _to_date(row.get("period_end"))
    if s is None or e is None or e < s:
        raise ValueError("the saved report has no readable period dates")
    return s, e


def _truthy(v) -> bool:
    """Truthiness of a stored flag. A DataFrame turns a missing cell into NaN,
    and bool(NaN) is True, so NaN must read as 'not set'."""
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return bool(v)


def _is_set(v) -> bool:
    """True when a stored value is present: not None, NaN / NaT, or blank text."""
    if v is None:
        return False
    try:
        if v != v:              # NaN and pandas NaT are the only values unequal to themselves
            return False
    except Exception:
        pass
    if isinstance(v, str) and not v.strip():
        return False
    return True


def _was_emailed(row) -> bool:
    """A report counts as emailed once `email_sent` is true OR an `email_sent_at`
    stamp exists. A regenerate-and-replace rewrites `email_sent` to false (the new
    record carries it) but leaves the stamp, so the period stays protected."""
    return _truthy(row.get("email_sent")) or _is_set(row.get("email_sent_at"))


def regenerate_action(existing_row, is_viewer: bool, rows_checked: bool = True) -> str:
    """What Generate may do with a freshly built report (owner U14).

    "refused"            read-only viewer: nothing is generated or saved.
    "preview_unchecked"  the saved rows could not be read (`rows_checked` False),
                         so "was this period already emailed?" is unknown: show the
                         result as an unsaved preview, never write blind.
    "preview_emailed"    a row for this period already exists AND was emailed:
                         show the new result as an unsaved preview, keep the
                         emailed version, offer an explicit Replace.
    "save"               no row, or a row that was never emailed: save as before.
    """
    if is_viewer:
        return "refused"
    if not rows_checked:
        return "preview_unchecked"
    if existing_row is not None and _was_emailed(existing_row):
        return "preview_emailed"
    return "save"


def emailed_suffix(row) -> str:
    """The archive caption's email marker. A replaced report (`email_sent` false
    but `email_sent_at` set) says so, rather than claiming the stored text is what
    was emailed."""
    if _truthy(row.get("email_sent")):
        return " · ✉ emailed"
    if _is_set(row.get("email_sent_at")):
        return " · ✉ emailed version replaced"
    return ""


def generated_et_text(ts) -> str:
    """'2026-11-17 14:05 ET' for a stored / fresh `generated_at` (a tz-naive value
    is UTC, how the DB stores it). '' when unreadable."""
    if ts is None:
        return ""
    try:
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%Y-%m-%d %H:%M") + " ET"
    except Exception:
        return ""


def find_period_row(rows, column: str, target: date):
    """The row whose `column` date equals `target` (str or date), else None."""
    for r in rows:
        if _to_date(r.get(column)) == target:
            return r
    return None


def weekly_rows_for_period(rows, start: date, end: date) -> list:
    """The weekly debriefs that belong to a monthly report's period, order kept.

    A week belongs to the month holding the majority of its days. Weeks end on
    Sunday, so a week_ending day W has days W-6 .. W and its majority month is
    the one containing W-3 (Thursday). So a week is in [start, end] exactly when
    W-3 is, i.e. W falls in [start + 3 days, end + 3 days]. Same majority rule as
    `report_labels`. For November 1-30 that keeps week_endings 11-04 .. 12-03
    (11-08, 11-15, 11-22, 11-29) and drops 11-01 (an October week) and 12-06.
    Rows with no readable week_ending are dropped.
    """
    lo, hi = start + timedelta(days=3), end + timedelta(days=3)
    out = []
    for r in rows:
        w = _to_date(r.get("week_ending"))
        if w is not None and lo <= w <= hi:
            out.append(r)
    return out


def _is_calendar_month(s: date, e: date) -> bool:
    """True for a window that is exactly one whole calendar month."""
    return (s.day == 1 and (s.year, s.month) == (e.year, e.month)
            and (e + timedelta(days=1)).month != e.month)


def _window_label(s: date, e: date, is_cal: bool) -> str | None:
    """`report_labels.period_label`, with the window ALSO spelled out on every
    non-calendar row. period_label alone names a window inside one month by the
    bare month, so two legacy windows in the same month would read identically."""
    lbl = period_label(s, e)
    if lbl is None or is_cal or "–" in lbl:
        return lbl
    return f"{lbl} ({s.strftime('%b %d')} – {e.strftime('%b %d')})"


def monthly_archive_options(rows) -> list[tuple[str, int]]:
    """(label, row index) per archive entry for the month picker, newest first.

    One entry per month: the row with the latest period_end wins (an on-demand
    re-run inside a month would otherwise duplicate). The label comes from
    `report_labels.period_label` and the index from the SAME row, so the name
    on screen is always the content shown.

    Two window shapes exist. Legacy rows are rolling 28-day windows that
    `report_labels` files under the month holding most of their days
    (2026-10-04 -> 11-01 = October). New rows are whole calendar months
    (2026-11-01 -> 11-30 = November). The majority-month key already keeps a
    legacy October and a new November apart, but a NEW October row
    (10-01 -> 10-31, from a Generate click in November) shares October's
    majority key with the legacy row, and "latest period_end wins" would let the
    legacy 11-01 / 11-08 row bury it. So calendar-month rows are keyed apart
    ("cal", year, month): both stay visible, the calendar row is listed first
    within its month, and nothing is hidden. Every NON-calendar row's label
    carries its window, "October 2026 (Oct 04 – Nov 01)", while a calendar
    row reads as the bare month, "October 2026", so the labels always tell the
    shapes apart and two legacy windows in one month never read identically.
    """
    entries: dict = {}   # key -> (period_end, -position, label, index, sort_month, is_cal)
    for ri, row in enumerate(rows):
        s = _to_date(row.get("period_start"))
        e = _to_date(row.get("period_end"))
        is_cal = bool(s is not None and e is not None and e >= s
                      and _is_calendar_month(s, e))
        key = period_month_key(s, e) if (s is not None and e is not None) else None
        label = _window_label(s, e, is_cal) if (s is not None and e is not None) else None
        if key is None or label is None:
            if e is not None:
                key, label = (e.year, e.month), e.strftime("%B %Y")
            else:
                key, label = (ri,), f"Report {ri + 1}"
        sort_month = (e.year, e.month) if e is not None else (0, 0)
        if len(key) == 2 and isinstance(key[0], int):    # a (year, month) majority key
            sort_month = key
        if is_cal:
            key = ("cal", s.year, s.month)
            sort_month = (s.year, s.month)
        rank = (e or date.min, -ri)          # latest period_end; earlier position breaks a tie
        cur = entries.get(key)
        if cur is None or rank > (cur[0], cur[1]):
            entries[key] = (rank[0], rank[1], label, ri, sort_month, is_cal)
    ordered = sorted(entries.values(),
                     key=lambda v: (v[4], v[5], v[0], v[1]), reverse=True)
    return [(v[2], v[3]) for v in ordered]


def archive_option_keys(rows, options) -> list[str]:
    """One stable string per archive entry, for the picker widget's value: the
    row's period_end (ISO). Keying the selection by period_end rather than list
    position means a row the cron inserts while the page is open cannot shift
    which window a click builds. An unreadable period_end falls back to its
    row position, which is the best a row with no date can offer."""
    keys = []
    for _lbl, ri in options:
        e = _to_date(rows[ri].get("period_end"))
        keys.append(e.isoformat() if e is not None else f"row-{ri}")
    return keys


def selected_archive_row(rows, options, selected_key):
    """The archived row the owner picked, or None for 'the last completed month'.

    The first entry is the newest, which is also the picker's default, and
    Streamlit stores a widget's default value, so choosing it cannot mean
    'rebuild this one'. Any other entry (an older month) is an explicit pick
    (U13). An unknown key (a stale selection) is no pick.
    """
    keys = archive_option_keys(rows, options)
    if selected_key not in keys:
        return None
    pos = keys.index(selected_key)
    if pos == 0:
        return None
    return rows[options[pos][1]]


def monthly_generate_window(today: date, rows, options, selected_key) -> tuple[date, date]:
    """The window Generate builds: the last completed month, or the picked
    archived row's own stored window (U13). Raises ValueError (see
    `monthly_period_for_row`) when that row's dates can't be read."""
    picked = selected_archive_row(rows, options, selected_key)
    if picked is None:
        return monthly_period(today)
    return monthly_period_for_row(picked)
