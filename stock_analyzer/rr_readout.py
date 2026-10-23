"""Pure R:R readout helpers: number formatting, display quality band, and the
redeploy/diversification headline decision, and the display ranking of add
candidates (entry-ready names first, applied before the top-N cut), so app.py
stays render-only.
"""
from __future__ import annotations

import math

from stock_analyzer.constants import (
    RR_ENTRY_MIN,
    RR_FAVOURABLE_MIN,
    RR_MARGINAL_MIN,
)


def _finite(x) -> bool:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x)


def fmt_ratio_vs(value: float, *thresholds: float) -> str:
    """Format a ratio to 1 decimal, unless rounding would land on the other
    side of a threshold from the true value (1.97 vs 2.0 must not read "2.0").
    In that case show 2 decimals, floored if the true value is below the
    threshold, ceiled if at/above it. Non-finite / non-numeric input returns
    "n/a"."""
    if not _finite(value):
        return "n/a"
    short = f"{value:.1f}"
    rounded = float(short)
    for t in thresholds:
        if (value < t) != (rounded < t):
            if value < t:
                return f"{math.floor(round(value * 100, 6)) / 100:.2f}"
            return f"{math.ceil(round(value * 100, 6)) / 100:.2f}"
    return short


def rr_quality(rr) -> str | None:
    """Display band for an R:R: favourable / marginal / poor; None when rr is
    not a finite positive number."""
    if not _finite(rr) or rr <= 0:
        return None
    if rr >= RR_FAVOURABLE_MIN:
        return "favourable"
    if rr >= RR_MARGINAL_MIN:
        return "marginal"
    return "poor"


def is_entry_ready(c: dict, rr_min: float = RR_ENTRY_MIN) -> bool:
    """A candidate is entry-ready when it clears the composite Buy gate
    (passes is True) AND its R:R is a finite number >= rr_min."""
    rr = c.get("rr")
    return c.get("passes") is True and _finite(rr) and rr >= rr_min


def rank_add_candidates(candidates: list[dict], rr_min: float = RR_ENTRY_MIN) -> list[dict]:
    """Display order for add/redeploy candidates, applied BEFORE the top-N cut:
    entry-ready (Buy gate AND R:R >= rr_min) -> other gate-passers -> scored
    but below the gate -> unscored; higher composite first within a tier.
    Stable, so ties keep the incoming order. Returns a new list; the input is
    not mutated."""
    def _key(c: dict) -> tuple:
        if is_entry_ready(c, rr_min):
            tier = 0
        elif c.get("passes") is True:
            tier = 1
        elif c.get("passes") is False:
            tier = 2
        else:
            tier = 3
        score = c.get("score")
        num = score if isinstance(score, (int, float)) and not isinstance(score, bool) else 0
        return (tier, -num)

    return sorted(candidates or [], key=_key)


def redeploy_headline(candidates: list[dict], rr_min: float = RR_ENTRY_MIN) -> dict:
    """Decide the headline for an add-candidate list. Callers pass the
    DISPLAYED rows (in display order), so a named ticker always has a card.

    states: entry (a passer whose R:R clears rr_min), rr_fails (first passer's
    R:R is below rr_min), no_upside (first passer's R:R is finite but <= 0),
    rr_unknown (first passer's R:R is None / non-finite),
    none_pass (scored, none passes), none_scored (nothing scored).
    """
    cands = candidates or []

    def _pack(state: str, c: dict) -> dict:
        return {
            "state": state,
            "ticker": c.get("ticker"),
            "score": c.get("score"),
            "rr": c.get("rr"),
            "sector": c.get("sector"),
        }

    for c in cands:
        if is_entry_ready(c, rr_min):
            return _pack("entry", c)
    for c in cands:
        if c.get("passes") is True:
            rr = c.get("rr")
            if not _finite(rr):
                return _pack("rr_unknown", c)
            return _pack("rr_fails" if rr > 0 else "no_upside", c)
    if any(c.get("passes") is False for c in cands):
        return {"state": "none_pass"}
    return {"state": "none_scored"}
