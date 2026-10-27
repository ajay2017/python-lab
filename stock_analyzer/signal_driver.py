"""Primary-driver diagnosis for the Signals "Score Breakdown" card.

Pure logic, no Streamlit. Sign convention: ``gap = t_score - bq_score``.
A positive gap means Technical is HIGHER than Business Quality, so Business
Quality is the weak side; a negative gap means Technical is the weak side.

Before 2026-10-27 app.py had the two outcomes swapped (a gap above the line
printed "Technical weakness only"; seen live on TSLA, Technical 62 / BQ 32).
"""
from __future__ import annotations

import math

from stock_analyzer import constants


def _is_real(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def bq_weak(bq) -> bool:
    """True when Business Quality is below the Hold floor (the Recommended Action rule)."""
    return bq < constants.COMPOSITE_HOLD


def show_driver_box(action_type) -> bool:
    """The driver box belongs on "review" cards only (not "trim" / "add")."""
    return action_type == "review"


def driver_diagnosis(t_score, bq_score, *, action_type="review", t_available=True) -> str | None:
    """Return "bq", "technical" or "both"; None when no diagnosis applies.

    None when the card isn't a "review" card, the technical score is an
    unmeasured placeholder (``t_available`` False), or either score isn't a
    finite real. Uses the same absolute rule as the Recommended Action text
    (``bq_weak``): "bq" needs a positive gap AND weak BQ, "technical" needs a
    negative gap AND BQ not weak; anything else is "both".
    """
    if not show_driver_box(action_type) or not t_available:
        return None
    if not _is_real(t_score) or not _is_real(bq_score):
        return None
    gap = t_score - bq_score
    weak = bq_weak(bq_score)
    if gap > constants.SIGNAL_DRIVER_GAP_PTS and weak:
        return "bq"
    if gap < -constants.SIGNAL_DRIVER_GAP_PTS and not weak:
        return "technical"
    return "both"


DRIVER_TEXT = {
    "bq": "🔴 **Business quality weakness** is the primary driver — this is a thesis-change signal, act with more urgency.",
    "technical": "🟡 **Technical weakness only** — business quality remains solid. Likely a timing/momentum signal; the ratchet stop may handle it.",
    # A gap inside the band can mean both pillars are weak OR both strong (the
    # Sell then comes from Valuation / Sentiment), so this says only what the
    # gap shows (2026-10-27; the old text claimed both were weak).
    "both": (f"⚠️ **No single driver** — Technical and Business Quality are within "
             f"{constants.SIGNAL_DRIVER_GAP_PTS:.0f} points of each other, so neither pillar "
             "alone explains the signal; check every pillar above before acting."),
}
