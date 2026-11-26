"""Disclosure for a risk-off de-risk check that could not run (RiskOffUnchecked).

Why this exists (docs/plans/risk-off-unchecked-c4-spec.md, RiskListwiseYoung C4):
exit_advisor.assess_risk_off_derisk returns [] when the portfolio fragility is
None (portfolio beta could not be measured: a young holding above
PORTFOLIO_RISK_UNMEASURED_MAX_PCT, SPY missing, too few joined days, or an
exception). On a risk-off day that silence reads as "nothing to trim". This
module is the one decision: the check is UNCHECKED when fragility is not a dict
AND the risk-off regime is armed. If the regime is not armed the check could not
have fired anyway, and if fragility is a dict the check ran: nothing to say.

Disclosure only. It never changes a card, a size or a gate. Pure: no clock, no
streamlit, no db. Never raises.
"""
from __future__ import annotations

from stock_analyzer import exit_advisor
from stock_analyzer import risk_window
from stock_analyzer.constants import RISK_OFF_TREND_MA, RISK_OFF_VIX_LEVEL

_ADVICE = "Review your highest-beta holdings yourself before adding risk."

_REGIME_UNREADABLE_TEXT = (
    "Risk-off de-risk check couldn't run: your portfolio beta couldn't be "
    "measured, and the market regime couldn't be read either. " + _ADVICE
)


def derisk_unchecked(
    fragility,
    spy_trend_df,
    vix_level,
    *,
    coverage=None,
    trend_ma: int = RISK_OFF_TREND_MA,
    vix_threshold: float = RISK_OFF_VIX_LEVEL,
) -> dict | None:
    """None when the de-risk check ran (fragility is a dict) or could not have
    fired (the risk-off regime is not armed). Else
    {"reasons": [str], "text": plain text (no `$`, no markdown)}.

    A regime read that raises returns the generic dict with no reasons (fail
    visible: we cannot show the check was moot)."""
    if isinstance(fragility, dict):
        return None
    try:
        armed, reasons = exit_advisor.risk_off_regime(
            spy_trend_df, vix_level, trend_ma=trend_ma, vix_threshold=vix_threshold
        )
    except Exception:
        return {"reasons": [], "text": _REGIME_UNREADABLE_TEXT}
    if not armed:
        return None
    reasons = [str(r) for r in reasons]
    text = (
        "Risk-off de-risk check couldn't run: the market is in a risk-off regime ("
        + "; ".join(reasons)
        + "), but your portfolio beta couldn't be measured, so no de-risk trim "
        "was considered today. " + _ADVICE
    )
    # Only the "unmeasured" note explains why the beta is missing; a "partial"
    # note ("measured without X") would sit beside "beta couldn't be measured"
    # and mislead, so it is never appended here.
    note = None
    if isinstance(coverage, dict) and coverage.get("state") == "unmeasured":
        try:
            note = risk_window.display_note(coverage)
        except Exception:
            note = None
    if note is not None:
        text += " " + note
    return {"reasons": reasons, "text": text}
