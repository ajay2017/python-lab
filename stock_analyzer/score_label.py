"""Label a Grow Today suppression row's score truthfully (pure, no Streamlit).

A NEW pick's ``score`` is the scanner's momentum score, not the composite, so
printing it as "Score 100" reads as a perfect composite. A HELD add's ``score``
is the position's composite, so "Score N" is right for it. Pick rows carry
``momentum_score`` (and ``composite_score`` when trustworthy, else None); add
rows carry neither. The G-20 pre-pass rows carry ``momentum_score: None`` on
purpose (daily_briefing.py), so they keep the plain "Score" label.
"""
from __future__ import annotations

import math


def _real(v):
    """Return v as float if it is a finite int/float (not bool), else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def score_phrase(row) -> str:
    m = _real(row.get("momentum_score"))
    if m is not None:
        out = f"momentum {m:.0f}"
        c = _real(row.get("composite_score"))
        if c is not None:
            out += f" · composite {c:.0f}"
        return out
    s = _real(row.get("score"))
    return f"Score {(s if s is not None else 0):.0f}"
