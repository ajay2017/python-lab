"""Per-leg read of the risk-off market regime: which leg was read, which was not.

Why this exists (RiskOffUncheckedRegimeBlind / RegimeOneLegMeasured, plan
`docs/plans/sell-check-unread-3a-3b.md` §2): `exit_advisor.risk_off_regime` degrades
each leg (SPY trend, VIX) to "not tripped" on missing data, so an unread leg is
indistinguishable from a calm one, and `exit_advisor.risk_off_state` calls the regime
"measured" when only ONE leg was usable. This module reports each leg as TRIPPED,
CLEAR or UNREAD so a display or a de-risk check can say "only partly known" instead
of "calm".

Arming never changes: a leg is TRIPPED exactly when `exit_advisor.risk_off_regime`
arms on that leg's input alone, and `state == "risk_off"` exactly when the combined
`risk_off_regime` arms. The SMA / threshold arithmetic is never re-implemented here.

`exit_advisor.risk_off_state` is deliberately left unchanged: `enter_now_readout.py`
(the pre-registered EnterNowInProtect readout) reads its "measured" verdict, and
changing it would alter a pre-registered measurement.

Pure: no clock, no streamlit, no db, no network. Never raises: a bad input makes that
leg UNREAD. Plain text only (no `$`, no `**`, no HTML).
"""
from __future__ import annotations

import math

from stock_analyzer import exit_advisor
from stock_analyzer.constants import RISK_OFF_TREND_MA, RISK_OFF_VIX_LEVEL

TRIPPED, CLEAR, UNREAD = "tripped", "clear", "unread"
_LEGS = (TRIPPED, CLEAR, UNREAD)


def _trend_leg(spy_trend_df, trend_ma, vix_threshold):
    """(leg, why) for the SPY trend leg; `why` is set only when the leg is unread."""
    try:
        close = exit_advisor._series_close(spy_trend_df)
        if close is None:
            return UNREAD, "no_data"
        if len(close) < trend_ma:
            return UNREAD, "short_history"
        armed, _ = exit_advisor.risk_off_regime(
            spy_trend_df, None, trend_ma=trend_ma, vix_threshold=vix_threshold)
        return (TRIPPED if armed else CLEAR), None
    except Exception:
        return UNREAD, "no_data"


def _vix_leg(vix_level, trend_ma, vix_threshold):
    """(leg, value) for the VIX leg; `value` is the float only when the leg is read."""
    try:
        if vix_level is None:
            return UNREAD, None
        value = float(vix_level)
        if math.isnan(value):
            return UNREAD, None
        armed, _ = exit_advisor.risk_off_regime(
            None, vix_level, trend_ma=trend_ma, vix_threshold=vix_threshold)
        return (TRIPPED if armed else CLEAR), value
    except Exception:
        return UNREAD, None


def read_regime(spy_trend_df, vix_level, *, trend_ma=RISK_OFF_TREND_MA,
                vix_threshold=RISK_OFF_VIX_LEVEL) -> dict:
    """Read both regime legs. Never raises.

    Returns {"trend": leg, "vix": leg,
             "state": "risk_off" | "calm" | "partial" | "unread",
             "reasons": [...],   # identical to exit_advisor.risk_off_regime(...)[1]
             "unread": ["trend"?, "vix"?],
             "trend_unread_why": None | "no_data" | "short_history",
             "vix_value": float | None}
    """
    trend, why = _trend_leg(spy_trend_df, trend_ma, vix_threshold)
    vix, vix_value = _vix_leg(vix_level, trend_ma, vix_threshold)
    try:
        _, reasons = exit_advisor.risk_off_regime(
            spy_trend_df, vix_level, trend_ma=trend_ma, vix_threshold=vix_threshold)
        reasons = list(reasons)
    except Exception:
        reasons = []
    unread = [name for name, leg in (("trend", trend), ("vix", vix)) if leg == UNREAD]
    if TRIPPED in (trend, vix):
        state = "risk_off"
    else:
        state = ("calm", "partial", "unread")[len(unread)]
    return {
        "trend": trend,
        "vix": vix,
        "state": state,
        "reasons": reasons,
        "unread": unread,
        "trend_unread_why": why if trend == UNREAD else None,
        "vix_value": vix_value if vix != UNREAD else None,
    }


def _leg(read, key):
    """A leg value from `read`, UNREAD when `read` is not a usable dict."""
    try:
        value = read.get(key) if isinstance(read, dict) else None
    except Exception:
        value = None
    return value if value in _LEGS else UNREAD


def _unread_count(read) -> int:
    return sum(1 for key in ("trend", "vix") if _leg(read, key) == UNREAD)


def posture_regime_state(read) -> str:
    """"measured" (no leg unread) | "partial" (one) | "not_measured" (two or a bad read)."""
    if not isinstance(read, dict):
        return "not_measured"
    return ("measured", "partial", "not_measured")[_unread_count(read)]


def signature(read) -> tuple:
    """(trend leg, vix leg); ("unread", "unread") for a non-dict."""
    return (_leg(read, "trend"), _leg(read, "vix"))


def log_text(read) -> str:
    """"risk-off regime: state=<state> trend=<leg> vix=<leg>" (caller appends the book part)."""
    state = UNREAD
    try:
        if isinstance(read, dict) and isinstance(read.get("state"), str):
            state = read["state"]
    except Exception:
        state = UNREAD
    trend, vix = signature(read)
    return f"risk-off regime: state={state} trend={trend} vix={vix}"


def unread_phrase(read):
    """Short noun phrase for the unread leg(s), None when nothing is unread."""
    trend_unread = _leg(read, "trend") == UNREAD
    vix_unread = _leg(read, "vix") == UNREAD
    if trend_unread and vix_unread:
        return "neither VIX nor the S&P 500's 1-year history"
    if vix_unread:
        return "VIX"
    if trend_unread:
        return "the S&P 500's 1-year history"
    return None


def _floor_1dp(value: float) -> float:
    """Floor to one decimal so a below-threshold VIX (24.96) never prints as the threshold."""
    return math.floor(value * 10) / 10


def display_line(read, *, trend_ma=RISK_OFF_TREND_MA, vix_threshold=RISK_OFF_VIX_LEVEL):
    """Caption for a regime that is only partly known, or None when nothing is hidden.

    None when no leg is unread, and also when a leg is tripped (the regime is armed,
    so nothing is hidden). Never raises.
    """
    try:
        trend = _leg(read, "trend")
        vix = _leg(read, "vix")
        if TRIPPED in (trend, vix):
            return None
        if trend != UNREAD and vix != UNREAD:
            return None
        avg = f"{trend_ma}-day"
        threshold = f"{vix_threshold:g}"
        tail = "Treat the regime as unknown, not calm."
        if trend == UNREAD and vix == UNREAD:
            return (
                "⚠ Neither VIX nor the S&P 500's 1-year history could be read this "
                "session, so the market regime is unknown. The book half of this "
                "posture is shown; no risk-off action is armed on missing data."
            )
        if vix == UNREAD:
            return (
                "⚠ VIX couldn't be read this session, so the market regime is only "
                f"partly known: the S&P 500 is not below its {avg} average, but a VIX "
                f"reading of {threshold} or more would not show here. {tail}"
            )
        why = read.get("trend_unread_why") if isinstance(read, dict) else None
        if why == "short_history":
            opening = f"⚠ The S&P 500's history was too short for the {avg} check this session"
        else:
            opening = "⚠ The S&P 500's 1-year history couldn't be read this session"
        value = read.get("vix_value") if isinstance(read, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            vix_text = f"VIX is {_floor_1dp(float(value)):.1f} (below {threshold})"
        else:
            vix_text = f"VIX is below {threshold}"
        return (
            f"{opening}, so the market regime is only partly known: {vix_text}, but a "
            f"close below the {avg} average would not show here. {tail}"
        )
    except Exception:
        return None
