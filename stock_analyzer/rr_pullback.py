"""The price a name would need to pull back to for its entry R:R to reach the minimum.

R:R = (target - price) / (price - stop). The stop trails the price at a roughly
constant distance d = price - stop (ATR based), so a pullback raises R:R. R:R
reaches `rr_min` at p* = target - rr_min * d.

Assumes the ATR stop trails the price at today's distance and the base target
and ATR stay as they are today. An estimate for a price alert, never a buy
level. Pure, no I/O, no UI wording.
"""
from __future__ import annotations

from stock_analyzer import entry_rr
from stock_analyzer.constants import RR_ENTRY_MIN


def _result(state: str, price=None, pct=None) -> dict:
    return {"state": state, "price": price, "pct": pct}


def pullback_to_rr(price, stop, target, base_source, rr_min: float = RR_ENTRY_MIN) -> dict:
    """Return {"state": "ok"|"beyond_stop"|"already_clears"|"target_moves"|"unknown",
    "price": float|None, "pct": float|None}.

    unknown: R:R can't be measured (missing input, stop not below price, or
    target at/below price). already_clears: measured R:R >= rr_min.
    target_moves: `base_source` is not "analyst" (resistance / momentum /
    flat fallbacks are all rebuilt from the price, so they fall on a pullback
    and R:R does not reliably rise): no pullback price is offered.
    beyond_stop: p* is at or below today's stop level, so no realistic
    pullback gets there. ok: `price` is p* rounded to cents, `pct` the
    (negative) move from today's price.
    """
    m = entry_rr.measure(price, stop, target)
    if m.get("state") != "ok":
        return _result("unknown")
    if m["rr"] >= rr_min:
        return _result("already_clears")
    if base_source != "analyst":
        return _result("target_moves")
    p, s, t = float(price), float(stop), float(target)
    d = p - s
    p_star = t - rr_min * d
    if p_star <= s:
        return _result("beyond_stop")
    return _result("ok", price=round(p_star, 2), pct=(p_star - p) / p * 100)
