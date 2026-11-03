"""Small pure text formatters shared by display code (no Streamlit, no I/O).

Kept out of the gate modules so display wording fixes stay cheap to change.
"""
from __future__ import annotations

import math

# SumGap (owner 2026-11-02, no backfill): one line shown with the Tax Report and
# the trade-behaviour statistics, defined once so every surface says the same thing.
TRADE_HISTORY_GAP_NOTE = (
    "Trade history before mid-August 2026 is incomplete (about 25 short round trips "
    "were never logged), so realized P&L and trade statistics for that period "
    "understate activity. Your Robinhood 1099-B is the tax figure."
)


def share_word(n) -> str:
    """"share" for exactly one, else "shares". Accepts numbers or numeric strings;
    anything unreadable gets the plural."""
    try:
        return "share" if float(n) == 1.0 else "shares"
    except (TypeError, ValueError):
        return "shares"


def signed_dollars(v, nd: int = 0) -> str:
    """Signed money with the sign BEFORE the dollar sign: "+$80", "-$198", "$0".
    A value that rounds to zero prints "$0" (never "+$0" / "-$0"). A missing or
    non-finite value prints an em dash rather than "$+nan"."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(f):
        return "—"
    r = round(f, nd)
    if r == 0:
        return "$0" if nd <= 0 else f"$0.{'0' * nd}"
    sign = "+" if r > 0 else "-"
    return f"{sign}${abs(r):,.{nd}f}"


def signed_count(n, word: str) -> str:
    """"N word" for a count that is shown with a minus when negative, never "-0":
    signed_count(-3, "downgrades") -> "-3 downgrades"; 0 -> "0 downgrades"."""
    try:
        i = int(round(float(n)))
    except (TypeError, ValueError):
        return f"— {word}"
    return f"{i} {word}"


def plural(n, noun: str) -> str:
    """"1 downgrade" / "3 downgrades" / "0 downgrades". An unreadable count
    prints "— downgrades"."""
    try:
        i = int(round(float(n)))
    except (TypeError, ValueError):
        return f"— {noun}s"
    return f"{i} {noun}" if i == 1 else f"{i} {noun}s"
