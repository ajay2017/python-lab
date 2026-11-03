"""Decision + wording for the Diversification Advisor's sector REDUCE card
(bug SecTrig).

The card fires above `SECTOR_REDUCE_TRIGGER` (decided in
`portfolio.diversification_recommendations`). It is an EARLY AWARENESS signal,
not a cap and not a "reduce by" call (owner decision 2026-11-03, option a), and
it must never contradict the detector the Brief uses: the Risk Advisor's
`sector_concentration` rec (top sector only, on the basis `gate_denom` sets, equity today (G-19: `_acct_f` = 1.0)). `card_state`
decides which band the card is in from that rec, so a risk rec that is HIGH
while the card's own pct is under `SECTOR_CEILING` still defers (only reachable
if `gate_denom` ever moves off equity).
The app's real sector levels: `SECTOR_ELEVATED` (Risk Analysis lists the largest
sector as elevated; Grow Today warns before adding) and `SECTOR_CEILING`
(Today's Brief Act Today trim; new buys blocked, G-16).

This module DECIDES the band (so it is a `_GATE_FILES` member) and words it.
Pure. Plain text safe for markdown: no `$` pair (dollar signs are escaped) and
no `**` (the title is rendered inside an HTML span).
"""
from __future__ import annotations

import math

from stock_analyzer.constants import SECTOR_CEILING, SECTOR_ELEVATED, SECTOR_REDUCE_TRIGGER
from stock_analyzer.score_text import composite_score_text


def _finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def title(rec: dict) -> str:
    """e.g. "Healthcare 23.5%: above the 20% diversification trigger"."""
    return (f"{rec['sector']} {float(rec['current_pct']):.1f}%: above the "
            f"{SECTOR_REDUCE_TRIGGER:.0f}% diversification trigger")


def size_label() -> str:
    return f"Trim to get under {SECTOR_REDUCE_TRIGGER:.0f}%"


def size_value(rec: dict, *, escape: bool = False) -> str:
    """The trim that brings the sector back under the trigger, "≈$540".
    `escape=True` for markdown prose (Streamlit renders a `$…$` pair as LaTeX);
    the st.metric value uses the plain form."""
    d = rec.get("reduce_dollars")
    if not _finite(d):
        return "n/a"
    sign = "\\$" if escape else "$"
    return f"≈{sign}{float(d):,.0f}"


def card_state(rec: dict, risk_recs) -> dict:
    """Which band the REDUCE card is in, from the Risk Advisor's own rec.

    `risk_recs` is the published `_risk_advisor_recs_cache` read with
    `util.get_or_offline` (None = couldn't check). Returns
    {"band": "early"|"elevated"|"hard_cap"|"unchecked", "show_size": bool,
     "pointer": short text for the right-hand slot or None}.
    Only the "early" band (no matching risk rec) shows a size of its own.
    """
    if risk_recs is None:
        return {"band": "unchecked", "show_size": False, "pointer": None}
    for r in risk_recs:
        if not isinstance(r, dict) or r.get("type") != "sector_concentration":
            continue
        rs = r.get("sector")
        if rs is None:
            # A concentration rec that doesn't name its sector can't be matched;
            # say so rather than assume it is about another sector.
            return {"band": "unchecked", "show_size": False, "pointer": None}
        if rs != rec.get("sector"):
            continue
        if r.get("priority") == "HIGH":
            return {"band": "hard_cap", "show_size": False,
                    "pointer": "See 🏠 Today's Brief"}
        return {"band": "elevated", "show_size": False,
                "pointer": "See 🔗 Risk Analysis"}
    return {"band": "early", "show_size": True, "pointer": None}


def body(rec: dict, state: dict) -> str:
    """The card's explanation for the band `card_state` returned."""
    band = state.get("band")
    if band == "hard_cap":
        return ("Today's Brief has an Act Today item for this sector, which "
                "takes precedence over this card.")
    if band == "elevated":
        return ("🔗 Risk Analysis lists this sector as elevated, with its own "
                "trim plan (Risk Action Plan).")
    if band == "unchecked":
        return ("The app's sector check couldn't be read this session, so this "
                "card can't say how urgent it is.")
    parts = [
        f"From {SECTOR_ELEVATED:.0f}%, 🔗 Risk Analysis lists your largest sector "
        "as elevated and Grow Today warns before adding to it; at "
        f"{SECTOR_CEILING:.0f}% Today's Brief makes it an Act Today trim and new "
        "buys in it are blocked. This is an early heads-up. Awareness only, no "
        "action required.",
    ]
    n = rec.get("n_holdings")
    if isinstance(n, int) and n >= 2:
        parts.append("Names in one sector often move together on the same news.")
    weakest = rec.get("weakest_tickers")
    if weakest is None:
        weakest = []
    if _finite(rec.get("reduce_dollars")):
        line = (f"If you trim, {size_value(rec, escape=True)} brings it back under "
                f"{SECTOR_REDUCE_TRIGGER:.0f}%")
        if weakest:
            w = weakest[0]
            line += f"; {w['ticker']} is the weakest name"
            if _finite(w.get("score")):
                sig = (w.get("signal") or "").split()
                line += f" ({sig[-1] if sig else 'score'} {composite_score_text(w['score'])})"
            if w.get("near_stop") and _finite(w.get("gap_to_stop")):
                line += f", and its stop is {float(w['gap_to_stop']):.1f}% away"
                line += (", so a stop exit would roughly resolve this"
                         if w.get("exit_resolves")
                         else ", so a stop exit would also cut this")
        parts.append(line + ".")
    return " ".join(parts)
