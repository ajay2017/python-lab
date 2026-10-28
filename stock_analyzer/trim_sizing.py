"""Shared trim share-count helpers and the earnings-week overweight trim rule.

Pure logic — no Streamlit, no I/O. Owner decisions:
  * 2026-10-23: no surface proposes a trim that sizes 0 shares (floor, never
    round a sub-share excess UP to 1). Migrated 2026-10-25: the Brief's
    earnings / profit-lock / deterioration items, the earnings playbook, macro
    PROTECT text and Signals' concentration trim. Migrated 2026-10-28: the
    Brief's weak-large TRIM_TO_TARGET and PROTECTIVE_TRIM, and Signals'
    "review" half_shares.
  * 2026-10-25 (Option A): in earnings week (0 <= days <= EARNINGS_IMMINENT_DAYS)
    ONE rule sizes the overweight trim — the Brief's: fires above the
    position-count-aware floor and trims to EARNINGS_OVERWEIGHT_TRIM_TO_PCT.
"""
from __future__ import annotations

import math
from datetime import date

from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_OVERWEIGHT_TOLERANCE_PP,
    EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT,
    EARNINGS_OVERWEIGHT_TRIM_PCT,
    EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
)


def _num(v) -> float | None:
    """Finite float or None (None / NaN / inf / non-numeric)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def shares_to_trim(shares: float, weight_pct: float, target_pct: float) -> int:
    """Whole shares to sell to take a position from weight_pct down to target_pct.

    Floored (never rounded up); 0 when there is no excess or the input is bad.
    """
    s, w, t = _num(shares), _num(weight_pct), _num(target_pct)
    if s is None or w is None or t is None:
        return 0
    if s <= 0 or t < 0 or w <= t:
        return 0
    return max(0, math.floor(s * (w - t) / w))


def shares_for_dollars(dollars: float, price: float) -> int:
    """Whole shares worth at most `dollars` at `price`, floored; 0 for bad input."""
    d, p = _num(dollars), _num(price)
    if d is None or p is None or d <= 0 or p <= 0:
        return 0
    return max(0, math.floor(d / p))


def fraction_trim_shares(shares: float, pct: float) -> int:
    """Whole shares in `pct`% of `shares`, floored; 0 for bad input."""
    s, p = _num(shares), _num(pct)
    if s is None or p is None or s <= 0 or p <= 0:
        return 0
    return max(0, math.floor(s * p / 100))


def earnings_overweight_floor(n_positions: int) -> float:
    """Equal-weight + EARNINGS_OVERWEIGHT_TOLERANCE_PP buffer, clamped to
    [EARNINGS_OVERWEIGHT_TRIM_PCT, EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT]. A flat
    threshold assumes a fixed portfolio size; this scales with however many
    positions are actually held, while still capping binary earnings-event
    exposure even for a deliberately concentrated book.
    """
    if n_positions <= 0:
        return EARNINGS_OVERWEIGHT_TRIM_PCT
    eq_target = 100.0 / n_positions
    return max(EARNINGS_OVERWEIGHT_TRIM_PCT,
               min(eq_target + EARNINGS_OVERWEIGHT_TOLERANCE_PP, EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT))


def days_to_earnings(earn_str, today) -> int | None:
    """Days from `today` (a date) to a "%Y-%m-%d" earnings string; None if missing/bad."""
    if not earn_str:
        return None
    # Same parse as daily_briefing._days_until (date.fromisoformat on the first
    # 10 chars), so a datetime-suffixed string can't put a ticker inside the
    # window on one surface and outside it on another.
    try:
        d = date.fromisoformat(str(earn_str)[:10])
        return (d - today).days
    except (TypeError, ValueError):
        return None


def earnings_days_by_ticker(held_data: dict | None, today) -> dict[str, int]:
    """Upper-cased ticker -> days to earnings, skipping missing / unparseable dates."""
    out: dict[str, int] = {}
    for t, data in (held_data or {}).items():
        days = days_to_earnings((data or {}).get("earnings"), today)
        if days is not None:
            out[str(t).upper()] = days
    return out


def in_earnings_window(days: int | None) -> bool:
    """Earnings week: 0 <= days <= EARNINGS_IMMINENT_DAYS."""
    return days is not None and 0 <= days <= EARNINGS_IMMINENT_DAYS


def earnings_week_trim(weight_pct: float, n_positions: int, shares: float) -> dict | None:
    """The earnings-week overweight rule. None when weight is not above the floor;
    otherwise the floor, the target and the whole-share trim (which may be 0)."""
    w = _num(weight_pct)
    if w is None:
        return None
    floor = earnings_overweight_floor(n_positions)
    if w <= floor:
        return None
    return {
        "floor_pct": floor,
        "target_pct": EARNINGS_OVERWEIGHT_TRIM_TO_PCT,
        "trim_shares": shares_to_trim(shares, w, EARNINGS_OVERWEIGHT_TRIM_TO_PCT),
    }
