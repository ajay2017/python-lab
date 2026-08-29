"""Held-position banner precedence for the "add to an existing position" flow.

Extracted from app.py's 📈 Analysis "already held" render block (Part 2 #3 of
the 2026-08-26 app review, `docs/reviews/2026-08-26-app-review.md`, mirroring
the `outage_gate.py` extraction pattern of commit `1b12779`): a held-position,
considering-an-add decision reads THREE independently-computed signals -- is
the mechanical stop breached, is there an active Brief Reduce/Exit call, is
the position merely held with neither -- and picks exactly one banner.
Getting the PRECEDENCE wrong (stop-breach must win over a reduce call, which
must win over the plain "already held" note) silently reorders which warning
a user sees on a name they're about to add to. That belongs in a pure,
unit-tested function, not an app.py if/elif chain nobody runs a test against.

Pure: no Streamlit, no I/O, no DB. app.py stays render-only -- it calls these,
then renders the markdown/HTML for whichever state comes back. Byte-identical
refactor: no banner text, no threshold, no rendering condition changed.
"""
from __future__ import annotations

from stock_analyzer.constants import GAP_TO_STOP_ROUND_DECIMALS

# Mutually exclusive banner states for the "already held, considering an add"
# render block. Order here IS the precedence order -- see `held_position_state`.
STOP_BREACH = "stop_breach"
REDUCE_CALL = "reduce_call"
HELD_PLAIN = "held_plain"
NOT_HELD = "not_held"


def gap_to_stop_pct(price: float | None, stop: float | None) -> float | None:
    """% distance from `price` to `stop` (negative/zero = at or past the stop).

    Same formula and rounding `portfolio.py`'s "Gap to Stop (%)" column and
    `daily_briefing`'s Act Today build use. app.py deliberately recomputes
    this against a fresher live-merged price rather than reading the
    already-published column (see its own comment on that) -- this is the
    one place that recompute's arithmetic lives, so it can't independently
    drift from the formula app.py's own comments say it must match.
    """
    if not price or not stop:
        return None
    return round((price - stop) / price * 100, GAP_TO_STOP_ROUND_DECIMALS)


def is_stop_breached(price: float | None, stop: float | None, *, gap_missing: bool = False) -> bool:
    """True when the mechanical stop is at or past the current price.

    `gap_missing` mirrors the caller's own data-integrity guard: a position
    whose stored "Gap to Stop (%)" is NaN is a data issue, not a decision --
    an absent gap must never read as a genuine breach.
    """
    if gap_missing or not price or not stop:
        return False
    gap = gap_to_stop_pct(price, stop)
    return gap is not None and gap <= 0


def held_position_state(
    *, is_holding: bool, stop_breached: bool, has_reduce_call: bool,
) -> str:
    """Which single banner applies to a held position being considered for an add.

    Precedence, highest first: STOP_BREACH (mechanical, recomputed live) >
    REDUCE_CALL (the Brief's published protective call) > HELD_PLAIN (neither
    -- just an existing position) > NOT_HELD (no position at all).

    Stop-breach outranks a reduce call deliberately: a stop is a hard,
    mechanical trigger the position itself defines, while a reduce call is
    the Brief's read of the same underlying deterioration -- when both are
    true they describe one event, and the more actionable of the two (sell
    at next open) is what the user should see, not a softer "under review"
    framing layered on top of it.
    """
    if not is_holding:
        return NOT_HELD
    if stop_breached:
        return STOP_BREACH
    if has_reduce_call:
        return REDUCE_CALL
    return HELD_PLAIN
