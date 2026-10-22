"""Caption under the Analysis trade buttons: says what the Trade Journal
pre-fill carries, and flags a Sell button shown against a Buy verdict.

Pure. No Streamlit, no DB. See docs/plans/one-sizing-path.md (C4).
"""
from __future__ import annotations

import math


def _score_text(score) -> str:
    try:
        if isinstance(score, bool) or score is None:
            return "—"
        f = float(score)
        return f"{f:.0f}" if math.isfinite(f) else "—"
    except (TypeError, ValueError):
        return "—"


def context_caption(label, score, shown) -> "str | None":
    """Caption for the Analysis trade-button row, or None when no button rendered.

    `shown`: set of the suggested actions whose buttons actually rendered
    ("BUY" / "SELL"). A Sell-only button on a Buy / Strong Buy verdict gets the
    mismatch wording; everything else keeps the standard pre-fill text.
    """
    try:
        if not shown:
            return None
        lab = str(label if label is not None else "")
        sc = _score_text(score)
        if ("Sell" not in lab and "Hold" not in lab and "Buy" in lab
                and set(shown) == {"SELL"}):
            return (f"Selling goes against today's {lab} call ({sc}/100). "
                    "Trade Journal records that call as context; you choose "
                    "whether it counts as following the signal.")
        return f"Opens Trade Journal pre-filled with **{lab} · {sc}/100** as the signal context."
    except Exception:
        return None
