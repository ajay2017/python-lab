"""Does the stop cover the expected earnings move?  (finding #23, 2026-10-30)

One shared decision for the 🔔 Catalyst Watch Pre-Earnings Playbook
(`earnings_advisor`) and the 🏠 Brief earnings-week card (`daily_briefing`).
Before this module the Playbook compared the stop gap with the expected move
while the Brief printed fixed text ("existing stop protects") that never
looked at the stop, so AAPL (stop 3.5% below vs a +-6% move) read "may not
protect" on one surface and "protects" on the other.

Wording only: nothing here changes an action, a size or a priority.
"""

from stock_analyzer.constants import EARNINGS_STOP_COVER_FRAC

# Fallback expected earnings move (%) by sector, used when the VaR-based
# estimate is too small to mean anything. Moved here unchanged from
# earnings_advisor (which re-exports it as `_SECTOR_DEFAULTS`).
SECTOR_DEFAULTS = {
    "Semiconductors":  10.0,
    "AI & Data":        9.0,
    "AI & Cloud":       8.0,
    "Cybersecurity":    9.0,
    "Healthcare":       8.0,
    "Energy":           6.0,
    "Defense":          5.0,
    "Financials":       6.0,
    "Clean Energy":     8.0,
    "Consumer Tech":    8.0,
    "EV & Auto":       10.0,
    "Enterprise Tech":  7.0,
    # Added 2026-09-15 (F-274 follow-on, user-confirmed) — regulated,
    # rate-case-driven utility earnings are famously small and predictable;
    # real post-earnings moves for DUK/SO/D/AEP/EXC are typically 1-3%,
    # well below the previous floor here (Defense, 5.0).
    "Utilities":        3.0,
}


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def estimate_earnings_move(risk_metrics: dict, sector: str) -> float:
    """Estimate typical earnings-day move from daily VaR × 3."""
    var95 = abs(_f(risk_metrics.get("var_95")))
    est = max(3.0, min(25.0, var95 * 3.0))
    if est <= 3.1:
        est = SECTOR_DEFAULTS.get(sector, 7.0)
    return round(est, 1)


def stop_cover_state(gap_to_stop, est_move) -> str:
    """'unknown' | 'at_risk' | 'covered'.

    'unknown' when the stop gap or the expected move isn't usable (never a
    fabricated 0). 'at_risk' when the stop sits closer than
    EARNINGS_STOP_COVER_FRAC of the expected move."""
    if gap_to_stop is None or est_move is None or est_move <= 0:
        return "unknown"
    if gap_to_stop < est_move * EARNINGS_STOP_COVER_FRAC:
        return "at_risk"
    return "covered"


def brief_earnings_trigger(state, gap_to_stop, est_move, *, trimmed: bool) -> str:
    """The Brief's earnings-week Trigger sentence. Plain text: no `$`, no `**`.

    'covered' only means gap >= EARNINGS_STOP_COVER_FRAC x move, so the wording
    splits on gap >= move ("beyond") vs. in between ("just inside"); it never
    claims the stop "covers" or "protects"."""
    _beat = "Beat → re-enter post-print if composite holds; Miss → "
    if state == "covered":
        if gap_to_stop >= est_move:
            if trimmed:
                return (
                    f"{_beat}the stop ({gap_to_stop:.1f}% below) sits beyond the "
                    f"±{est_move:.1f}% expected move on the rest."
                )
            return (
                f"Surprise miss + price gap-down → the stop ({gap_to_stop:.1f}% below) "
                f"sits beyond the ±{est_move:.1f}% expected move; no pre-event action needed."
            )
        # In between: a miss of about the expected size reaches the stop, but
        # the gap clears the at-risk line, so no pre-event action is called.
        if trimmed:
            return (
                f"{_beat}the stop ({gap_to_stop:.1f}% below) is just inside the "
                f"±{est_move:.1f}% expected move on the rest, so a miss of about the "
                "usual size could reach it."
            )
        return (
            f"The stop ({gap_to_stop:.1f}% below) is just inside the ±{est_move:.1f}% "
            "expected move, so a miss of about the usual size could reach it; it "
            f"clears the app's at-risk line ({EARNINGS_STOP_COVER_FRAC:.0%} of the "
            "move), so no pre-event action is called."
        )
    if state == "at_risk":
        if trimmed:
            return (
                f"{_beat}the stop is only {gap_to_stop:.1f}% below against a "
                f"±{est_move:.1f}% expected move, so a gap-down could go through it: "
                "be ready to act at the open on the rest."
            )
        return (
            f"The stop is only {gap_to_stop:.1f}% below against a ±{est_move:.1f}% "
            "expected move, so a gap-down on a miss could go through it: no pre-event "
            "trim, but be ready to act at the open if the report is negative "
            "(see 🔔 Catalyst Watch)."
        )
    _unk = (
        "Stop data unavailable, so the app can't say whether it covers the "
        "expected move: set a stop before the report."
    )
    if trimmed:
        return "Beat → re-enter post-print if composite holds; s" + _unk[1:]
    return _unk
