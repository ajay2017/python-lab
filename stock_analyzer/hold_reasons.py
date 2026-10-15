"""Plain-English reasons a name that cleared every Grow Today check was held
back by PACING only (grow-today-owns-buys P3, OP-07 "gate the call, never the
candidate").

Pure text, no I/O, no Streamlit. Every number is interpolated from
constants.py -- never a literal. The daily new-position budget build adds its
own codes here later so both lanes speak with one voice.

Pacing text must never say "sized": "not sized" is reserved for the budget.
"""
from __future__ import annotations

from stock_analyzer.constants import (
    COMPOSITE_BUY_FLAT_DAY,
    GROW_MAX_PICKS_BULL,
    GROW_MAX_PICKS_DEFAULT,
    MOVER_MAX_PICKS,
)

PICK_CAP = "pick_cap"
SECTOR_SLOT = "sector_slot"
MOVER_CAP = "mover_cap"

REASON_CODES = (PICK_CAP, SECTOR_SLOT, MOVER_CAP)

FOOTER = ("Same checks as the picks above; held back only by pacing. "
          "No share size — not a second buy list.")


def reason_text(code, ctx=None) -> str:
    """Reason sentence for a hold `code`; "" for an unknown/None code (callers
    render nothing rather than inventing a reason). `ctx` may carry `sector`
    for the sector_slot code; a missing sector reads "that sector"."""
    ctx = ctx if isinstance(ctx, dict) else {}
    if code == PICK_CAP:
        return (f"Daily pick limit reached ({GROW_MAX_PICKS_BULL} on an up day / "
                f"{GROW_MAX_PICKS_DEFAULT} on a flat day).")
    if code == SECTOR_SLOT:
        sector = str(ctx.get("sector") or "").strip() or "that sector"
        return f"Another {sector} pick took today's one-per-sector slot."
    if code == MOVER_CAP:
        return f"Breakout limit reached ({MOVER_MAX_PICKS} per day)."
    return ""


OVERFLOW_HEADER = "✔ Also cleared every check — not one of today's picks"
WATCHLIST_TAG = "📋 On your Watchlist — check Ready to Enter there"
FLAT_HELD_HEADER = "⏸ Flat market — only fully confirmed setups today"


def leads_note(tone) -> str:
    """Explainer under 'Momentum leads — not checked today'. Always says the
    names carry no verdict, so a lead is never read as a buy call."""
    if tone == "bear":
        return ("New entries are deferred while the market is risk-off; these "
                "weren't checked and carry no verdict. Resumes when the market "
                "tone recovers.")
    if tone == "flat":
        return (f"On a flat day Grow Today only checks its top few names with "
                f"momentum ≥ {COMPOSITE_BUY_FLAT_DAY}; these weren't checked, so "
                "they carry no verdict — not buy calls.")
    return ("Grow Today didn't check these today, so they carry no verdict — "
            "not buy calls.")


def adds_paused_note(names) -> str:
    """Explainer under 'Adds paused on a flat/down day'. No size, by design."""
    who = ", ".join(str(n) for n in names) if names else "These holdings"
    return (f"{who} would qualify for an add-to-winner. Adds resume on the "
            "next up day — no size today.")


def adds_paused_label(tone, n) -> str:
    kind = "down" if tone == "bear" else "flat"
    return f"Adds paused on a {kind} day ({n})"
