"""Date-keyed FRED changes (CpiYoyGap C1, docs/plans/cpi-yoy-gap-spec.md).

Pure: no Streamlit, no network, no DB. Unwired until C2.

Why this exists: `macro_calendar._fred_obs` drops FRED's "." rows, so the
observation dates are lost and every change was computed BY POSITION
(`vals[0]` vs `vals[12]`).  FRED CPIAUCNS and UNRATE have no 2025-10 value, so
every CPI "YoY" from the 2025-11 print to the 2026-10 print was a 13-month
change.  Here every lookback is by DATE: the base of a 12-month change is the
month exactly 12 months before the reading, and a base that is blank (".") or
simply absent from the series means "not computable", never "use the next
older value".

Data shape ("obs"): a list of ``(date, float | None)`` tuples, NEWEST FIRST.
A date is the first of the month (or of the quarter) the reading describes.
``None`` = FRED row with value "." (or an unreadable / non-finite value).  A
month that is ABSENT from the list counts exactly like a "." month.

A change result ("change") is a plain dict:

    status       "ok" | "base_missing" | "ref_missing" | "no_data"
    value        float | None   the change (percent for yoy / mom_pct, difference
                                for mom_diff, the reading itself for level);
                                None unless status == "ok"
    period       date | None    the reading month asked for (first of month)
    base_period  date | None    the month it is compared with (None for level)
    ref_value    float | None   the reading's own value (None if it has none)
    base_value   float | None   the base month's value (None if it has none)
    reason       str | None     "base_zero" when the base exists but is 0 for a
                                percentage change (reported as base_missing);
                                None otherwise

``status`` meaning: ``no_data`` = the series has no rows at all; ``ref_missing``
= the reading month itself has no value; ``base_missing`` = the base month has
no value (or is 0 for a percentage change).
"""
from __future__ import annotations

import math
from datetime import date, datetime

# Transform names match macro_calendar._FRED_MAP / _apply_transform ("yoy_pct");
# "yoy" is accepted as an alias.
YOY = "yoy_pct"
MOM_PCT = "mom_pct"
MOM_DIFF = "mom_diff"
LEVEL = "level"

_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ── parsing ───────────────────────────────────────────────────────────────────

def _to_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _to_float(v) -> float | None:
    """FRED "." (or anything unreadable / non-finite) -> None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_dated_obs(fred_json_or_rows) -> list[tuple[date, float | None]]:
    """FRED observations -> ``[(date, float | None), ...]`` NEWEST FIRST.

    Accepts the whole FRED response dict (``{"observations": [...]}``) or the
    bare list of ``{"date": "YYYY-MM-DD", "value": "123.4" | "."}`` rows.
    A "." value (or an unreadable one) becomes ``None`` and the row is KEPT, so
    the gap stays visible.  A row whose date cannot be read is dropped.  An
    unusable input gives ``[]``.
    """
    rows = fred_json_or_rows
    if isinstance(rows, dict):
        rows = rows.get("observations")
    if not isinstance(rows, (list, tuple)):
        return []
    out: list[tuple[date, float | None]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = _to_date(r.get("date"))
        if d is None:
            continue
        out.append((d, _to_float(r.get("value"))))
    out.sort(key=lambda t: t[0], reverse=True)
    return out


# ── month arithmetic ──────────────────────────────────────────────────────────

def month_start(d: date) -> date:
    """The first of ``d``'s month."""
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    """First-of-month date ``n`` months after ``d`` (negative n = before).
    Always returns day 1, whatever day ``d`` was."""
    idx = d.year * 12 + (d.month - 1) + int(n)
    return date(idx // 12, idx % 12 + 1, 1)


def sub_months(d: date, n: int) -> date:
    """First-of-month date ``n`` months before ``d``."""
    return add_months(d, -int(n))


def format_period(d: date | None, step_months: int = 1) -> str:
    """"Oct 2026" for a monthly series, "Q3 2026" for a quarterly one
    (``step_months == 3``).  ``None`` -> "" (nothing to label)."""
    if d is None:
        return ""
    if step_months == 3:
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    return f"{_MONTH_ABBR[d.month - 1]} {d.year}"


# ── date-keyed changes ────────────────────────────────────────────────────────

def _by_month(obs) -> dict[date, float | None]:
    """First-of-month -> value; the NEWEST row wins on a duplicate month."""
    out: dict[date, float | None] = {}
    for d, v in obs or []:
        k = month_start(d)
        if k not in out:
            out[k] = _to_float(v)
    return out


def _result(status, period, base_period=None, ref_value=None, base_value=None,
            value=None, reason=None) -> dict:
    return {"status": status, "value": value, "period": period,
            "base_period": base_period, "ref_value": ref_value,
            "base_value": base_value, "reason": reason}


def change_by_period(obs, transform: str, ref: date, step_months: int = 1) -> dict:
    """The ``transform`` change for the reading month ``ref``, by DATE.

    ``transform``: ``yoy_pct`` (alias ``yoy``; base = 12 months before ``ref``),
    ``mom_pct`` / ``mom_diff`` (base = ``step_months`` before ``ref``; a
    quarterly series passes 3, a 3-month level difference such as UNRATE passes
    ``mom_diff`` with 3), ``level`` (no base).  Same numeric formulas as
    ``macro_calendar._apply_transform``.  Returns the change dict documented in
    the module docstring.  An unknown transform raises ValueError (a coding
    error, never a data condition).
    """
    t = YOY if transform == "yoy" else transform
    if t not in (YOY, MOM_PCT, MOM_DIFF, LEVEL):
        raise ValueError(f"unknown transform: {transform!r}")
    period = month_start(ref)
    series = _by_month(obs)
    if not series:
        return _result("no_data", period)
    ref_v = series.get(period)
    if ref_v is None:
        return _result("ref_missing", period)
    if t == LEVEL:
        return _result("ok", period, ref_value=ref_v, value=ref_v)
    base_period = sub_months(period, 12 if t == YOY else step_months)
    base_v = series.get(base_period)
    if base_v is None:
        return _result("base_missing", period, base_period, ref_v)
    if t == MOM_DIFF:
        return _result("ok", period, base_period, ref_v, base_v, value=ref_v - base_v)
    if base_v == 0:
        return _result("base_missing", period, base_period, ref_v, base_v,
                       reason="base_zero")
    return _result("ok", period, base_period, ref_v, base_v,
                   value=(ref_v - base_v) / base_v * 100)


def current_and_previous(obs, transform: str, step_months: int = 1) -> dict:
    """``{"current": change, "previous": change}`` for a series.

    The current reading is the NEWEST row that has a value (a trailing "." row
    is a period FRED has not posted a number for yet).  The previous reading is
    the current period minus ONE STEP (``step_months``: 1 monthly, 3 quarterly),
    by date, so a gap before it makes ``previous`` ``base_missing`` /
    ``ref_missing`` instead of silently borrowing an older month.  With no
    valued row both come back ``no_data`` with ``period`` None.
    """
    valued = [d for d, v in (obs or []) if _to_float(v) is not None]
    if not valued:
        empty = _result("no_data", None)
        return {"current": dict(empty), "previous": dict(empty)}
    cur = month_start(max(valued))
    return {"current": change_by_period(obs, transform, cur, step_months),
            "previous": change_by_period(obs, transform,
                                         sub_months(cur, step_months), step_months)}


def cpi_yoy_for_regime(obs, max_fallback: int) -> dict:
    """The regime's CPI 12-month change, with a bounded fallback (spec §2).

    Reading month = the newest row of the series INCLUDING a "." row.  For
    k = 0..``max_fallback`` the first month (reading month minus k) that has
    both its own value and the value 12 months earlier wins; never a 13-month
    change.  Returns a dict:

        value          float | None   the true 12-month % change used
        month          date | None    the month ``value`` describes
        basis          "latest"      k == 0 (the newest month itself)
                       "fallback"    k >= 1
                       "unavailable" nothing within the bound (value / month None)
        reading_month  date | None    the newest month FRED lists (None = no rows)
        skipped_month  date | None    the reading month when it has no 12-month
                                      figure (basis fallback / unavailable);
                                      None when basis == "latest"
        missing_month  date | None    the data month that was absent for the
                                      reading month: its base a year earlier
                                      (status base_missing) or the reading month
                                      itself (status ref_missing / "." row);
                                      None when basis == "latest" or no rows

    ``max_fallback`` below 0 is treated as 0.  The caller maps "unavailable"
    to "Rate-Cut Optimism can't be selected" (missing data only tightens).
    """
    series = _by_month(obs)
    if not series:
        return {"value": None, "month": None, "basis": "unavailable",
                "reading_month": None, "skipped_month": None, "missing_month": None}
    reading = max(series)
    first_missing: date | None = None
    for k in range(0, max(0, int(max_fallback)) + 1):
        m = sub_months(reading, k)
        c = change_by_period(obs, YOY, m)
        if c["status"] == "ok":
            return {"value": c["value"], "month": m,
                    "basis": "latest" if k == 0 else "fallback",
                    "reading_month": reading,
                    "skipped_month": None if k == 0 else reading,
                    "missing_month": first_missing}
        if k == 0:
            first_missing = (c["base_period"] if c["status"] == "base_missing"
                             else c["period"])
    return {"value": None, "month": None, "basis": "unavailable",
            "reading_month": reading, "skipped_month": reading,
            "missing_month": first_missing}
