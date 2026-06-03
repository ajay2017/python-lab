"""
Position lifecycle — a held position's current state, used by the calm-advisor
layer to decide which management nudges are worth surfacing.

A position moves through: settling → established → winning, with at_risk / exit
overriding whenever the position is in danger. The point is *cadence*: a
freshly-opened position ("settling") shouldn't be micromanaged with routine
stop-tighten nudges (it sits 3–8% above its own ATR stop by construction), but
a genuine exit or an at-risk position is NEVER silenced by age.

Pure logic — no Streamlit / no I/O. The caller supplies age/pnl/gap (already in
port_df + held_data) so this stays trivially unit-testable.
"""

from stock_analyzer.constants import (
    POSITION_SETTLING_DAYS,
    POSITION_AT_RISK_GAP_PCT,
    POSITION_WINNING_PNL_PCT,
)


def classify_position_state(
    age_days: int | None,
    pnl_pct: float,
    gap_to_stop_pct: float | None,
    has_exit_signal: bool = False,
) -> str:
    """Return the lifecycle state of a held position.

    Parameters
    ----------
    age_days        : days since the OLDEST still-held lot was opened (position
                      age). None when there's no trade journal for the ticker.
    pnl_pct         : unrealised P&L %.
    gap_to_stop_pct : (price − stop) / price × 100. ≤ 0 means the stop is
                      breached. None when no stop is available.
    has_exit_signal : True when an explicit Sell/Strong-Sell signal is active.

    Returns one of: "exit" | "at_risk" | "winning" | "settling" | "established".

    Strict precedence — danger ALWAYS beats age, so a freshly-opened position
    that is already breaching its stop is "exit", not "settling":
      1. exit         — breach or sell signal
      2. at_risk      — within the critical gap band
      3. settling     — younger than POSITION_SETTLING_DAYS (only if age known)
      4. winning      — meaningful unrealised gain
      5. established   — the default steady state

    Critical rule: age_days is None NEVER yields "settling". No trade-journal
    history must not silence management — calm, not blind.
    """
    if has_exit_signal or (gap_to_stop_pct is not None and gap_to_stop_pct <= 0):
        return "exit"
    if gap_to_stop_pct is not None and gap_to_stop_pct <= POSITION_AT_RISK_GAP_PCT:
        return "at_risk"
    if age_days is not None and age_days < POSITION_SETTLING_DAYS:
        return "settling"
    if pnl_pct is not None and pnl_pct >= POSITION_WINNING_PNL_PCT:
        return "winning"
    return "established"


# Display metadata per state. "established" is intentionally un-badged (the
# steady state needs no chip). Colours are hex strings for inline HTML; no
# Streamlit import here.
_BADGES: dict[str, dict] = {
    "settling":  {"emoji": "🌱", "label": "Settling",  "color": "#22c55e",
                  "tip": "Recently opened — giving it room before routine stop nudges."},
    "winning":   {"emoji": "📈", "label": "Winning",   "color": "#3b82f6",
                  "tip": "Sitting on a meaningful unrealised gain."},
    "at_risk":   {"emoji": "⚠️", "label": "At Risk",   "color": "#f59e0b",
                  "tip": "Close to its stop — watch for a breach."},
    "exit":      {"emoji": "⛔", "label": "Exit",       "color": "#ef4444",
                  "tip": "Stop breached or sell signal — exit per your rule."},
}


def lifecycle_badge(state: str) -> dict | None:
    """Return {emoji, label, color, tip} for a state, or None when un-badged
    ("established" — the steady state shows no chip)."""
    return _BADGES.get(state)
