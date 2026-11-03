"""Wording for where a price sits relative to an entry zone (display only).

``watchlist_advisor`` treats a price as "near" the zone when it is no more than
``WATCHLIST_NEAR_ZONE_PCT`` above the zone top, and that test is also true for a
price BELOW the zone. The words must say which side the price is on: "within N%
of the zone" is only true for a price above ``entry_hi``. Pure; no I/O. This
module chooses words only, never which action a card gets.
"""
from __future__ import annotations


def zone_side(price, entry_lo, in_zone: bool) -> str:
    """"inside" | "below" | "above" (above the zone top). A price below
    ``entry_lo`` is "below" even when the caller counted it as near the zone."""
    if in_zone:
        return "inside"
    try:
        if price is not None and entry_lo is not None and float(price) < float(entry_lo):
            return "below"
    except (TypeError, ValueError):
        pass
    return "above"


def zone_word(price, entry_lo, in_zone: bool) -> str:
    """Short word for "price is {word} the entry zone": in / below / near."""
    return {"inside": "in", "below": "below", "above": "near"}[zone_side(price, entry_lo, in_zone)]


def zone_phrase(price, entry_lo, in_zone: bool, near_pct: float, zone_txt: str = "") -> str:
    """Predicate for "Price ___": "is inside the entry zone", "is within N% of
    the entry zone" (price above the zone top, within the band) or "is below the
    entry zone (X% under $entry_lo)". ``zone_txt`` (e.g. " ($1.00–$2.00)") is placed
    right after "the entry zone"."""
    side = zone_side(price, entry_lo, in_zone)
    if side == "inside":
        return f"is inside the entry zone{zone_txt}"
    if side == "below":
        try:
            under = (float(entry_lo) - float(price)) / float(entry_lo) * 100
            return f"is below the entry zone{zone_txt} ({under:.1f}% under ${float(entry_lo):.2f})"
        except (TypeError, ValueError, ZeroDivisionError):
            return f"is below the entry zone{zone_txt}"
    return f"is within {near_pct:g}% of the entry zone{zone_txt}"
