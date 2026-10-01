"""Pure helpers for 💰 Account → Cash Activity (display only).

The chart used to plot every income event as a positive bar, grouped only by
`event_type`. So a margin-interest charge looked like income, and a Gold Plan
credit was netted into that month's interest: June 2026 showed $7.08 when the
owner had actually paid $36.59 against a $29.45 credit. Found from a live
screenshot on 2026-10-01. These helpers keep charges and credits apart and
signed, and line up the zero of the bar axis with the zero of the realized-P&L
axis so "below the line" means the same thing on both.

Awareness only: nothing here feeds account_flows, Modified Dietz, or any gate.
"""
from __future__ import annotations

import math
from datetime import date as _date

# Display order, bottom-to-top within a month's stack.
CATEGORIES = ("Dividend", "Interest earned", "Margin interest", "Fee")

# Chart controls (owner choice, 2026-10-01). Weekly is deliberately absent:
# interest and dividends post monthly, so weekly bars would be mostly empty.
GROUPINGS = ("Monthly", "Quarterly", "Yearly")
RANGES = ("Last 12 months", "This year", "All time")


def range_start(range_label: str, today: _date, grouping: str = "Monthly") -> "_date | None":
    """First date included by a range choice; None means no lower bound.
    "Last 12 months" starts on the 1st of the month 11 months back, so the
    current month plus the 11 before it are whole buckets. The start is then
    snapped back to its `grouping` bucket, so the first quarter or year shown
    is never a partial one labelled as if it were whole."""
    if range_label == "This year":
        start = _date(today.year, 1, 1)
    elif range_label == "Last 12 months":
        y, m = today.year, today.month - 11
        if m < 1:
            y, m = y - 1, m + 12
        start = _date(y, m, 1)
    else:
        return None
    return period_start(start, grouping)


def period_start(d: _date, grouping: str) -> _date:
    """Bucket start for `d`: the 1st of its month, quarter, or year."""
    if grouping == "Yearly":
        return _date(d.year, 1, 1)
    if grouping == "Quarterly":
        return _date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    return _date(d.year, d.month, 1)


def period_label(p: _date, grouping: str) -> str:
    """Axis label for a bucket start from `period_start`."""
    if grouping == "Yearly":
        return str(p.year)
    if grouping == "Quarterly":
        return f"Q{(p.month - 1) // 3 + 1} {p.year}"
    return p.strftime("%b %Y")


def chart_category(ev: dict) -> "str | None":
    """Bar category for one income event, or None when it can't be placed.

    Interest is split by SIGN, using the same convention capital_vs_margin
    confirmed against the real statement (2026-09-11): negative = margin
    interest charged (MINT), positive = earned (GMPC / INT). Zero/NaN amounts
    return None because they are neither a charge nor a credit."""
    try:
        amt = float(ev.get("amount"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amt) or amt == 0:
        return None
    et = str(ev.get("event_type") or "").strip().lower()
    if et == "dividend":
        return "Dividend"
    if et == "interest":
        return "Margin interest" if amt < 0 else "Interest earned"
    if et == "fee":
        return "Fee"
    return None


def reclassified_broker_fees(events: "list[dict]") -> "list[dict]":
    """Rows that broker_sync.canonical_income_events counted as margin
    interest even though the broker sync labelled them 'FEE' (owner
    decision 2026-10-01; the only genuine fee on this account is the annual
    Gold fee). The page lists them so the relabel is visible, not silent.
    Pure."""
    return [ev for ev in (events or []) if ev.get("reclassified_from") == "fee"]


def _finite(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def zero_aligned_ranges(a_lo, a_hi, b_lo, b_hi, pad: float = 0.08) -> "tuple[tuple[float, float], tuple[float, float]]":
    """Axis ranges for two y-axes (primary a, secondary b) whose zero lines sit
    at the same height. Each axis keeps its own data extent, padded by `pad`
    of its span, and only ever EXTENDS downward to match the other axis's
    zero position, so no data is clipped. An axis with no data gets (-1, 1).
    Non-finite inputs are treated as 0. Pure."""
    def _padded(lo, hi):
        lo, hi = min(_finite(lo), 0.0), max(_finite(hi), 0.0)
        span = hi - lo
        if span <= 0:
            return -1.0, 1.0
        return lo - pad * span, hi + pad * span

    (alo, ahi), (blo, bhi) = _padded(a_lo, a_hi), _padded(b_lo, b_hi)
    # Fraction of the axis height that sits below zero; strictly in (0, 1)
    # because padding guarantees lo < 0 < hi.
    z = max(-alo / (ahi - alo), -blo / (bhi - blo))
    k = z / (1.0 - z)
    return (-k * ahi, ahi), (-k * bhi, bhi)
