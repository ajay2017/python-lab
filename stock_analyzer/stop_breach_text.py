"""Wording for a stop-breach card's "why" line.

A breach fires when the stored `Gap to Stop (%)` (rounded to
`GAP_TO_STOP_ROUND_DECIMALS` in portfolio.build_portfolio_df) is <= 0, so a
price a hair ABOVE the stop (gap rounds to +0.0%) still counts as breached.
"closed below the stop" is false for that case; this module picks the sentence
that matches the price/stop relation without changing the breach test itself.
Pure; shared by daily_briefing and headless_alert_engine so they can't drift.
"""
from __future__ import annotations

import math

from stock_analyzer.constants import GAP_TO_STOP_ROUND_DECIMALS


def _finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _money(x) -> str:
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def breach_why(price, stop, stop_type, gap) -> str:
    """The "why" sentence for a stop breach (see module docstring)."""
    try:
        gap_txt = f"{float(gap):+.{GAP_TO_STOP_ROUND_DECIMALS}f}"
    except (TypeError, ValueError):
        gap_txt = "n/a"
    if _finite(price) and _finite(stop) and float(price) >= float(stop):
        return (
            f"Price ${_money(price)} closed at the {stop_type} stop "
            f"${_money(stop)} (gap {gap_txt}%): a gap that rounds to 0 "
            f"counts as a breach."
        )
    return (
        f"Price ${_money(price)} closed below the {stop_type} stop "
        f"${_money(stop)} (gap {gap_txt}%)."
    )
