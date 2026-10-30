"""Wording for a stop-breach card's "why" line.

A breach fires when the stored `Gap to Stop (%)` (rounded to
`GAP_TO_STOP_ROUND_DECIMALS` in portfolio.build_portfolio_df) is <= 0, so a
price a hair ABOVE the stop (gap rounds to +0.0%) still counts as breached.
"below the stop" is false for that case; this module picks the sentence
that matches the price/stop relation without changing the breach test itself.
The sentence avoids "closed" because the in-app Brief passes a LIVE intraday
price (only the premarket cron and after-close runs use a close). The stop
label is trimmed of a trailing "stop" because portfolio.py's stop types can
already end in it ("ATR Stop"), which would read "the ATR Stop stop".
Pure; shared by daily_briefing and headless_alert_engine so they can't drift.
"""
from __future__ import annotations

import math

from stock_analyzer.constants import GAP_TO_STOP_ROUND_DECIMALS, STOP_RECOVERY_MARGIN_PCT


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


def _label(stop_type) -> str:
    """Stop-type label without a trailing word "stop" (case-insensitive)."""
    t = "" if stop_type is None else str(stop_type).strip()
    if t.lower() == "stop":
        return ""
    if t.lower().endswith(" stop"):
        t = t[:-5].rstrip()
    return t


def breach_why(price, stop, stop_type, gap) -> str:
    """The "why" sentence for a stop breach (see module docstring)."""
    try:
        gap_txt = f"{float(gap):+.{GAP_TO_STOP_ROUND_DECIMALS}f}"
    except (TypeError, ValueError):
        gap_txt = "n/a"
    label = _label(stop_type)
    stop_phrase = f"the {label} stop" if label else "the stop"
    if _finite(price) and _finite(stop) and float(price) >= float(stop):
        return (
            f"Price ${_money(price)} is at or just above {stop_phrase} "
            f"${_money(stop)} (gap {gap_txt}%): a gap that rounds to 0 "
            f"counts as a breach."
        )
    return (
        f"Price ${_money(price)} is below {stop_phrase} "
        f"${_money(stop)} (gap {gap_txt}%)."
    )


def live_breach_caption(live_px, stop) -> str | None:
    """Caption for a confirmed (active) stop breach at the live price.

    `util.stop_recovery_state` keeps a breach active until the live price is
    more than STOP_RECOVERY_MARGIN_PCT above the stop, so the live price can be
    at or slightly above the stop; "still below" is false there.
    """
    if not (_finite(live_px) and _finite(stop)):
        return None
    px, st_ = float(live_px), float(stop)
    if px <= 0 or st_ <= 0:
        return None
    if px < st_:
        return f"⚠️ Breach confirmed at live price ${px:.2f} — still below stop ${st_:.2f}."
    return (
        f"⚠️ Live price ${px:.2f} is at or just above the stop ${st_:.2f}, "
        f"within the {STOP_RECOVERY_MARGIN_PCT:g}% recovery margin, so the breach still stands."
    )
