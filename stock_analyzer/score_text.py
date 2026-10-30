"""Composite score as display text that never rounds across a band edge.

A composite printed with ``:.0f`` reads "65" for a live 64.6 while its label
says Hold (Hold = below COMPOSITE_BUY). Pure; no I/O.
"""

import math

from stock_analyzer.constants import (
    COMPOSITE_SELL,
    COMPOSITE_HOLD,
    COMPOSITE_BUY,
    COMPOSITE_STRONG_BUY,
)

_EDGES = (COMPOSITE_SELL, COMPOSITE_HOLD, COMPOSITE_BUY, COMPOSITE_STRONG_BUY)


def composite_score_text(score) -> str:
    """Whole-number score, unless rounding would cross a composite band edge;
    then the score floored to one decimal. Unusable input -> "n/a"."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(s):
        return "n/a"
    rounded = float(f"{s:.0f}")
    for edge in _EDGES:
        if (s < edge) != (rounded < edge):
            return f"{math.floor(s * 10) / 10:.1f}"
    return f"{s:.0f}"
