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
    DAILY_NEW_POSITION_BUDGET_PCT,
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


# -- Daily new-position budget wording (docs/plans/daily-new-position-budget.md) --
# "not sized" belongs to the budget only; none of these says "not one of today's
# picks" (that phrase is the PACING header's, OVERFLOW_HEADER).
BUDGET_PARTIAL = "budget_partial"
BUDGET_EXHAUSTED = "budget_exhausted"
BUDGET_RESERVED = "budget_reserved"
BUDGET_UNKNOWN = "budget_unknown"

BUDGET_CODES = (BUDGET_PARTIAL, BUDGET_EXHAUSTED, BUDGET_RESERVED, BUDGET_UNKNOWN)


def _usd(x) -> str:
    try:
        return f"${float(x):,.0f}"
    except (TypeError, ValueError):
        return "$0"


def _pct(x, fallback="0%") -> str:
    try:
        return f"{float(x):.1f}%"
    except (TypeError, ValueError):
        return fallback


def budget_status_line(pct_used, logged, allocated, listed, left,
                       list_label="this list") -> str:
    """Status line shown on every sizing surface. `list_label` names what
    `listed` covers on that surface (the emails say "this morning's emails",
    since their joint total includes entries the morning email does not show)."""
    return (f"New positions today: {_pct(pct_used)} of net capital "
            f"(logged {_usd(logged)} · allocated {_usd(allocated)} · "
            f"{list_label} {_usd(listed)}) — budget "
            f"{DAILY_NEW_POSITION_BUDGET_PCT:g}%, {_usd(left)} left")


def budget_note(status, ctx=None) -> str:
    """Per-item note for a budget status; "" for any other status."""
    ctx = ctx if isinstance(ctx, dict) else {}
    if status == BUDGET_PARTIAL:
        return (f"Sized from what's left of today's budget — "
                f"{ctx.get('n', '?')} of {ctx.get('m', '?')} shares.")
    if status == BUDGET_EXHAUSTED and ctx.get("called"):
        return ("Not sized: net capital is at or below zero, so there is no "
                "new-position budget today.")
    if status == BUDGET_EXHAUSTED and ctx.get("is_pick") is False:
        return (f"Not sized: today's new-position budget "
                f"({DAILY_NEW_POSITION_BUDGET_PCT:g}% of net capital) is already "
                f"used. Resets tomorrow.")
    if status == BUDGET_EXHAUSTED:
        return (f"One of today's picks — not sized: today's new-position budget "
                f"({DAILY_NEW_POSITION_BUDGET_PCT:g}% of net capital) is already "
                f"used. Resets tomorrow if it still qualifies.")
    if status == BUDGET_RESERVED:
        return "Reserved by today's email run"
    if status == BUDGET_UNKNOWN:
        why = str(ctx.get("why") or "").strip() or "a required input was unavailable"
        return f"Size withheld — today's new-position budget couldn't be checked ({why})."
    return ""


def budget_over_line(pct_used) -> str:
    """Logged buys alone are already above the budget."""
    return (f"Today's new buys are {_pct(pct_used, '?%')} of net capital — above the "
            f"{DAILY_NEW_POSITION_BUDGET_PCT:g}% daily budget; nothing new "
            f"sized today.")
