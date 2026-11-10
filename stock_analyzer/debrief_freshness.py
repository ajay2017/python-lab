"""Is a STORED Weekly Debrief still the same figure Reports shows today?

THE INCIDENT (2026-11-10). The debrief for the week ending 2026-11-08 was
generated 11-08 and read -9.7% / -$673. The 11-09 EOD run then repaired the
11-06 account row (gross +$141.20), so 📄 Reports → Performance Review → Last
week reads -7.67% / -$532. The stored debrief's caption still claimed it was the
"same figure as" Performance Review. A saved debrief is an immutable dated
artifact, so the honest fix is to say when the account record it was built from
has changed since, and to point the reader at Generate Now.

WHAT THIS MODULE IS. Pure decisions only: no streamlit, no database, no network,
no clock read. A stored debrief is "same" only when nothing that feeds its
figure has changed AND nothing could be left unchecked:

  * `account_daily_snapshots.repaired_at` (docs/sql/account-snapshot-
    provenance.sql) is stamped when a row is repaired: a window row repaired
    AFTER `generated_at` makes the debrief "changed".
  * "Account %" is money-weighted with `account_flows` dated in (d0, d1]
    (performance_review.py): a flow dated in the window whose `created_at` is
    later than `generated_at` also makes it "changed".
  * Rows Performance Review LEAVES OUT (`account_row_trust.exclude_stale_rows`:
    a late-logged fill before that night's repair, a refused repair, an
    unreadable journal) never get a `repaired_at`, so the caller passes their
    dates in; one inside the window makes the state "unknown" (claim dropped).
  * "other_week": Performance Review -> Last week resolves to the week ending
    the most recent Sunday ON OR BEFORE today (`resolve_period`), so before the
    Sunday cron or after a missed one the newest stored debrief is the PRIOR
    week. It is never "same" as a different week.

  * `stored_debrief_freshness`: the decision (same / changed / unknown /
    other_week).
  * `account_caption`, `stale_warning_text`: the wording for each state, so
    app.py is render-only wiring.

LIMIT (same-day cache). The caller reads the day-scoped session caches
(`_account_daily_snapshots_full_cache_{date}`, `_account_flows_full_cache_{date}`),
so a repair or flow recorded later the SAME day shows from the next load.
Performance Review reads the same caches, so the two agree within a session.

THE WINDOW. The debrief measures the account from the session BEFORE the week to
its last record, so the rows it was built from are those dated from the last
NYSE trading day before the week's Monday through `week_ending`. The trading
calendar is provider-free on purpose (weekday and not in
`constants.NYSE_HOLIDAYS`, the same rule `trade_entry_checks._previous_trading_day`
applies), so this module stays importable from a pure-logic test.

TIME. `generated_at` is stored in UTC and `repaired_at` / `created_at` are
timestamptz, so they are compared as INSTANTS (both parsed tz-aware), never as
wall-clock text or calendar dates: an evening-ET generation is the next UTC day,
and a repair on a DST change day has an ambiguous wall time but an exact
instant. The row dates (`snapshot_date`, `flow_date`) and every date shown are
America/New_York.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from stock_analyzer.constants import NYSE_HOLIDAYS

_ET = "America/New_York"


# -- calendar (provider-free; same rule as trade_entry_checks._is_trading_day) --

def _is_trading_day(d: date) -> bool:
    """Weekday and not an NYSE full-day closure. Early closes are trading days."""
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def _previous_trading_day(d: date) -> date:
    """The last session strictly before `d`; `d` itself need not be one."""
    p = d - timedelta(days=1)
    while not _is_trading_day(p):
        p -= timedelta(days=1)
    return p


# -- coercions ------------------------------------------------------------------

def _as_date(v) -> "date | None":
    """A date from a date / datetime / Timestamp / ISO string; None if unreadable.
    A datetime is taken at its own calendar date (a `week_ending` or a
    `snapshot_date` is a plain date, never an instant)."""
    if v is None:
        return None
    try:
        if isinstance(v, pd.Timestamp):
            return None if pd.isna(v) else v.date()
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        t = pd.to_datetime(str(v).strip()[:10], errors="coerce")
        return None if pd.isna(t) else t.date()
    except (TypeError, ValueError, OverflowError):
        return None


def _as_instant(v) -> "pd.Timestamp | None":
    """A tz-aware (UTC) instant; a naive value is read as UTC (the storage zone).
    None for a missing / unreadable value."""
    if v is None:
        return None
    try:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            t = pd.to_datetime(v, utc=True, errors="coerce", format="ISO8601")
        else:
            t = pd.to_datetime(v, utc=True, errors="coerce")
        return None if pd.isna(t) else t
    except (TypeError, ValueError, OverflowError):
        return None


def _is_null(v) -> bool:
    """A NULL timestamp: None, NaN, NaT or an empty / "None" / "NaT" string."""
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(v, str) and v.strip().lower() in ("", "none", "nan", "nat")


def _et_date_str(t: "pd.Timestamp") -> str:
    return str(t.tz_convert(_ET).date())


def _result(state: str, *, repaired_dates=None, last_repaired_et=None,
            flow_dates=None, reasons=None, week_ending=None,
            current_week_ending=None) -> dict:
    return {
        "state": state,
        "repaired_dates": list(repaired_dates or []),
        "last_repaired_et": last_repaired_et,
        "flow_dates": list(flow_dates or []),
        "reasons": list(reasons or []),
        "week_ending": week_ending,
        "current_week_ending": current_week_ending,
    }


def _unknown() -> dict:
    return _result("unknown")


# -- the decision -----------------------------------------------------------------

def window_start(week_ending) -> "date | None":
    """First account-row date a debrief for `week_ending` is built from: the last
    NYSE trading day strictly before that week's Monday. None if unreadable."""
    we = _as_date(week_ending)
    if we is None:
        return None
    monday = we - timedelta(days=we.weekday())
    return _previous_trading_day(monday)


def _excluded_in_window(excluded_dates, start: date, end: date) -> bool:
    """True when any excluded account-row date falls in [start, end]. Items may be
    dates, ISO strings or `exclude_stale_rows` dicts ({"snapshot_date": ...})."""
    for x in excluded_dates:
        d = _as_date(x.get("snapshot_date") if isinstance(x, dict) else x)
        if d is not None and start <= d <= end:
            return True
    return False


def stored_debrief_freshness(generated_at, week_ending, snapshots_df, *,
                             current_week_ending, excluded_dates, flows_rows) -> dict:
    """Whether a stored debrief still matches what Performance Review shows.

    Returns {"state": "same" | "changed" | "unknown" | "other_week",
             "repaired_dates": [snapshot dates (ET) repaired after generation],
             "last_repaired_et": ET date of the latest such repair | None,
             "flow_dates": [flow dates recorded after generation],
             "reasons": ["repair", "flows"] subset (for "changed"),
             "week_ending": ISO str | None, "current_week_ending": ISO str | None}.

    The three keyword inputs are REQUIRED so a caller can never skip a check
    silently: pass `None` for one that could not be read.
      * `current_week_ending`: the end date of `resolve_period("last_week",
        today_et)`. A stored `week_ending` different from it is "other_week".
      * `excluded_dates`: the dates `exclude_stale_rows` leaves out (a list,
        possibly empty); `None` = the exclusion call failed -> "unknown".
      * `flows_rows`: `account_flows` rows (dicts with `flow_date`,
        `created_at`); `None` = the read failed -> "unknown".

      * "changed": a window row has `repaired_at` later than `generated_at`
        (instants, both tz-aware), and/or a flow in (window start, week_ending]
        has `created_at` later than `generated_at`. A provable change wins over
        anything unchecked.
      * "unknown": `snapshots_df` None or missing `repaired_at` / `snapshot_date`;
        `generated_at` / `week_ending` / `current_week_ending` unreadable;
        `excluded_dates` None or one inside the window; `flows_rows` None, a
        flow in the window with no readable `created_at`, or no row carrying
        `created_at` at all; a window row with an unreadable `repaired_at`.
        Never "same" for something that could not be checked.
      * "same": otherwise, including NULL `repaired_at`, a repair before the
        debrief was generated, and a repair dated outside the window.
    """
    gen = _as_instant(generated_at)
    start = window_start(week_ending)
    end = _as_date(week_ending)
    cur = _as_date(current_week_ending)
    if gen is None or start is None or end is None or cur is None:
        return _unknown()
    if end != cur:
        return _result("other_week", week_ending=end.isoformat(),
                       current_week_ending=cur.isoformat())

    unchecked = False
    repaired: "list[tuple[str, pd.Timestamp]]" = []

    # -- account rows repaired after generation
    if (snapshots_df is None or not hasattr(snapshots_df, "columns")
            or "repaired_at" not in snapshots_df.columns
            or "snapshot_date" not in snapshots_df.columns):
        unchecked = True
    else:
        for _, row in snapshots_df.iterrows():
            d = _as_date(row.get("snapshot_date"))
            if d is None or d < start or d > end:
                continue
            raw = row.get("repaired_at")
            if _is_null(raw):
                continue
            rep = _as_instant(raw)
            if rep is None:
                unchecked = True
                continue
            if rep > gen:
                repaired.append((d.isoformat(), rep))

    # -- rows Performance Review leaves out never carry a repaired_at
    if excluded_dates is None:
        unchecked = True
    elif _excluded_in_window(excluded_dates, start, end):
        unchecked = True

    # -- flows recorded after generation (Account % is money-weighted on them)
    flow_dates: "list[str]" = []
    if flows_rows is None or not isinstance(flows_rows, (list, tuple)):
        unchecked = True
    else:
        dict_rows = [f for f in flows_rows if isinstance(f, dict)]
        if dict_rows and not any("created_at" in f for f in dict_rows):
            unchecked = True
        for f in dict_rows:
            fd = _as_date(f.get("flow_date"))
            if fd is None or fd <= start or fd > end:     # Performance Review: d0 < flow <= d1
                continue
            raw = f.get("created_at")
            made = None if _is_null(raw) else _as_instant(raw)
            if made is None:
                unchecked = True
                continue
            if made > gen:
                flow_dates.append(fd.isoformat())

    reasons = (["repair"] if repaired else []) + (["flows"] if flow_dates else [])
    if reasons:
        last = max((r for _, r in repaired), default=None)
        return _result(
            "changed",
            repaired_dates=sorted({d for d, _ in repaired}),
            last_repaired_et=_et_date_str(last) if last is not None else None,
            flow_dates=sorted(set(flow_dates)),
            reasons=reasons,
        )
    if unchecked:
        return _unknown()
    return _result("same")


# -- wording (the caption text choice; app.py only renders it) ---------------------

_CAPTION_HEAD = (
    "Account change measured from the session before the week to its last "
    "account record"
)

_CAPTION_TAIL = (
    " A leveraged account moves roughly its leverage times the holdings' own move; "
    "SPY is unlevered."
)


def account_caption(freshness: dict) -> str:
    """The caption under a current-method debrief's metrics, by freshness state.
    Only "same" may claim the figure matches Performance Review today."""
    f = freshness if isinstance(freshness, dict) else {}
    state = f.get("state")
    if state == "same":
        return (_CAPTION_HEAD + " — same figure as 📄 Reports → Performance Review → "
                "Last week." + _CAPTION_TAIL)
    if state == "changed":
        return _CAPTION_HEAD + ", as recorded when this debrief was generated." + _CAPTION_TAIL
    if state == "other_week":
        return (
            _CAPTION_HEAD + ". This is the debrief for the week ending "
            f"{f.get('week_ending') or '?'}; 📄 Reports → Performance Review → Last week "
            f"now shows the week ending {f.get('current_week_ending') or '?'}."
            + _CAPTION_TAIL
        )
    return (
        _CAPTION_HEAD + ", measured when this debrief was generated (whether it still "
        "matches the account record could not be checked)." + _CAPTION_TAIL
    )


def stale_warning_text(freshness: dict, generated_et_date: str) -> "str | None":
    """The warning for a "changed" debrief; None for any other state. `$` is
    backslash-escaped for Streamlit markdown (the text carries none today; the
    escape keeps a future edit from rendering as LaTeX)."""
    f = freshness or {}
    if f.get("state") != "changed":
        return None
    reasons = f.get("reasons")
    reasons = reasons if isinstance(reasons, (list, tuple)) and reasons else ["repair"]
    rd = f.get("repaired_dates")
    dates = ", ".join(rd) if isinstance(rd, (list, tuple)) and rd else "an earlier day"
    last = f.get("last_repaired_et")
    last = last if isinstance(last, str) and last else "later"
    fd = f.get("flow_dates")
    fdates = ", ".join(fd) if isinstance(fd, (list, tuple)) and fd else "an earlier day"
    repair_txt = f"a later repair of the account record for {dates} (repaired {last})"
    flows_txt = f"a deposit/withdrawal for {fdates} was recorded after this debrief"
    if "repair" in reasons and "flows" in reasons:
        head = f"Recorded {generated_et_date}, before {repair_txt}; also, {flows_txt}."
    elif "flows" in reasons:
        head = f"Recorded {generated_et_date}; {flows_txt}."
    else:
        head = f"Recorded {generated_et_date}, before {repair_txt}."
    txt = (
        head + " These figures can differ from "
        "📄 Reports → Performance Review → Last week; press **Generate Now** to "
        "refresh this week."
    )
    return txt.replace("$", "\\$")
