"""The G-32 entry reward:risk measure (unrounded) and its `clears` predicate.

One definition of "R:R to the base target at today's price and ATR stop" for
every gate that compares it to `RR_ENTRY_MIN`. `targets.risk_reward` stays
display-only (it rounds to 1 decimal, so a true 1.96 reads 2.0); gate
comparisons use this module. Pure, no I/O, no UI wording (copy lives with the
surfaces that render it).
"""
from __future__ import annotations

import math

from stock_analyzer.constants import RR_ENTRY_MIN

# Float-noise cleanup only, not a threshold: prices are cents, and e.g.
# (384.25, 381.21, 390.33) computes 1.9999999999999813, so a true 2:1 would fail
# an exact >= comparison without it.
_RR_CANON_DIGITS = 6


def _finite(x) -> bool:
    """A real finite number. bool / np.bool_ / str rejected; numpy numbers accepted."""
    if isinstance(x, bool):
        return False
    try:
        import numpy as np
        if isinstance(x, np.bool_):
            return False
        if not isinstance(x, (int, float, np.integer, np.floating)):
            return False
    except ImportError:  # pragma: no cover
        if not isinstance(x, (int, float)):
            return False
    return math.isfinite(float(x))


def _unknown(why: str) -> dict:
    return {"rr": None, "state": "unknown", "why": why}


def measure(entry, stop, target) -> dict:
    """R:R = (target - entry) / (entry - stop), canonicalised to 6 decimals.

    Returns {"rr": float|None, "state": "ok"|"no_upside"|"unknown", "why": str}.
    unknown: any input missing / non-numeric / non-finite / <= 0
    ("missing_input"), or entry - stop <= 0 ("stop_not_below_entry").
    no_upside: rr <= 0 (target at or below entry).
    """
    if not (_finite(entry) and _finite(stop) and _finite(target)):
        return _unknown("missing_input")
    e, s, t = float(entry), float(stop), float(target)
    if e <= 0 or s <= 0 or t <= 0:
        return _unknown("missing_input")
    risk = e - s
    if risk <= 0:
        return _unknown("stop_not_below_entry")
    rr = round((t - e) / risk, _RR_CANON_DIGITS)
    if rr <= 0:
        return {"rr": rr, "state": "no_upside", "why": "no_upside"}
    return {"rr": rr, "state": "ok", "why": "ok"}


def from_bundle(bundle) -> dict:
    """`measure` on the bundle triple (current_price, stop, targets.base), the
    same triple the Watchlist and the redeploy headline read. Adds `base_source`."""
    if not isinstance(bundle, dict):
        out = _unknown("no_bundle")
        out["base_source"] = None
        return out
    targets = bundle.get("targets")
    if not isinstance(targets, dict):
        targets = {}
    out = measure(bundle.get("current_price"), bundle.get("stop"), targets.get("base"))
    out["base_source"] = targets.get("base_source")
    return out


def clears(m: dict, rr_min: float = RR_ENTRY_MIN) -> bool:
    """True only for a measured, positive R:R at or above `rr_min`."""
    return m.get("state") == "ok" and m.get("rr") is not None and m["rr"] >= rr_min


def clears_rr(rr, rr_min: float = RR_ENTRY_MIN) -> bool:
    """`clears` for a bare, already-measured R:R (the Watchlist result's `rr`):
    True only for a finite, positive value at or above `rr_min`."""
    return _finite(rr) and float(rr) > 0 and float(rr) >= rr_min


def gate_state(m: dict, rr_min: float = RR_ENTRY_MIN):
    """Tri-state for a gate tile: True (clears), False (measured but below, or
    no upside), None (unknown: couldn't be measured)."""
    state = m.get("state")
    if state == "ok":
        return clears(m, rr_min)
    if state == "no_upside":
        return False
    return None


def classify(m: dict, rr_min: float = RR_ENTRY_MIN) -> str:
    """"pass" | "below" (measured, under rr_min) | "no_upside" | "unknown"."""
    state = m.get("state")
    if state == "ok":
        return "pass" if clears(m, rr_min) else "below"
    if state == "no_upside":
        return "no_upside"
    return "unknown"
