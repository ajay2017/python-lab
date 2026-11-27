"""Disclosure for a risk-off de-risk check that could not run (RiskOffUnchecked).

Why this exists (docs/plans/risk-off-unchecked-c4-spec.md, RiskListwiseYoung C4):
exit_advisor.assess_risk_off_derisk returns [] when the portfolio fragility is
None (portfolio beta could not be measured: a young holding above
PORTFOLIO_RISK_UNMEASURED_MAX_PCT, SPY missing, too few joined days, or an
exception). On a risk-off day that silence reads as "nothing to trim". This
module is the one decision: the check is UNCHECKED when the book half or the
market half of its two AND-gates could not be read.

Three kinds (the dict's "kind" key):
- beta_unmeasured: the book beta is unmeasured and the regime IS risk-off (the
  original C4 case; its text is unchanged).
- regime_unread: the book is caution / fragile (it could arm) and the regime is
  only partly read or unread, so the market half of the gate was never known.
- both_unread: the book beta is unmeasured and the regime is only partly read or
  unread, so the check had nothing to go on.

RiskOffUncheckedRegimeBlind (plan docs/plans/sell-check-unread-3a-3b.md §2, C2):
`exit_advisor.risk_off_regime` degrades each leg (SPY trend, VIX) to "not
tripped" on missing data, so a regime with an unread leg looked calm and a
caution / fragile book on a day VIX or SPY could not be fetched read "nothing to
trim". The per-leg read lives in `regime_read`. Arming NEVER changes: this module
only discloses, it never creates or removes a card, a size or a gate.
`assess_risk_off_derisk` is untouched and is the sole thing that arms a trim.

This DELIBERATELY reverses the C4 spec's pinned "regime inputs missing -> None"
(docs/plans/risk-off-unchecked-c4-spec.md ~20) by owner decision U-C, 2026-11-27.

Pure: no clock, no streamlit, no db. Never raises.
"""
from __future__ import annotations

import math

from stock_analyzer import regime_read
from stock_analyzer import risk_window
from stock_analyzer.constants import RISK_OFF_TREND_MA, RISK_OFF_VIX_LEVEL

_ADVICE = "Review your highest-beta holdings yourself before adding risk."

# The book half, in the words the Risk Posture dial already uses for these bands.
_BOOK_PHRASE = {
    "caution": "more volatile than the market",
    "fragile": "would fall outsized in a routine pullback",
}
_ARMABLE = tuple(_BOOK_PHRASE)


def _floor_1dp(value: float) -> float:
    """Floor to one decimal so a below-threshold VIX (24.96) never prints as the threshold."""
    return math.floor(value * 10) / 10


def _coverage_note(coverage):
    """The risk_window note, only when coverage says the beta is unmeasured.

    A "partial" note ("measured without X") would sit beside "beta couldn't be
    measured" and mislead, so it is never appended here."""
    if isinstance(coverage, dict) and coverage.get("state") == "unmeasured":
        try:
            return risk_window.display_note(coverage)
        except Exception:
            return None
    return None


def _both_unread_text(phrase: str) -> str:
    # "neither VIX nor ..." already carries the negative: "could be read", never a
    # double negative ("neither ... couldn't be read either").
    verb = "could be read" if phrase.startswith("neither ") else "couldn't be read either"
    return (
        "Risk-off de-risk check couldn't run: your portfolio beta couldn't be "
        f"measured, and {phrase} {verb}, so the check had "
        "nothing to go on today. " + _ADVICE
    )


def _regime_unread_text(read, book_phrase, trend_ma, vix_threshold) -> str:
    avg = f"{trend_ma}-day"
    threshold = f"{vix_threshold:g}"
    trend_unread = "trend" in read["unread"]
    vix_unread = "vix" in read["unread"]
    book = f"Your book is {book_phrase}, so "
    if trend_unread and vix_unread:
        return (
            "Risk-off de-risk check couldn't run: neither VIX nor the S&P 500's "
            "1-year history could be read today, so the market regime is unknown. "
            + book + f"either signal (VIX at {threshold} or more, or the S&P 500 "
            f"below its {avg} average) would have armed a de-risk trim. " + _ADVICE
        )
    if vix_unread:
        return (
            "Risk-off de-risk check couldn't fully run: VIX couldn't be read today, "
            "so the market regime is only partly known (the S&P 500 is not below "
            f"its {avg} average). " + book + f"a VIX reading of {threshold} or more "
            "would have armed a de-risk trim. " + _ADVICE
        )
    if read.get("trend_unread_why") == "short_history":
        opening = f"the S&P 500's history was too short for the {avg} check today"
    else:
        opening = "the S&P 500's 1-year history couldn't be read today"
    value = read.get("vix_value")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        vix_text = f"VIX is {_floor_1dp(float(value)):.1f}, below {threshold}"
    else:
        vix_text = f"VIX is below {threshold}"
    return (
        f"Risk-off de-risk check couldn't fully run: {opening}, so the market "
        f"regime is only partly known ({vix_text}). " + book + "the S&P 500 "
        f"closing below its {avg} average would have armed a de-risk trim. " + _ADVICE
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
    """None when the de-risk check ran or could not have fired. Else
    {"kind": "beta_unmeasured" | "regime_unread" | "both_unread",
     "reasons": [str], "text": plain text (no `$`, no markdown)}.

    Book severity: "caution" / "fragile" = a book that could arm; "calm" = can't
    arm (None); anything else (None, {}, {"severity": "ok"}, a non-dict) = the book
    beta is unmeasured, because assess_risk_off_derisk can't arm on it either.

        book            regime state          result
        calm            any                   None
        caution/fragile risk_off or calm      None (the check ran)
        caution/fragile partial / unread      regime_unread
        unmeasured      risk_off              beta_unmeasured
        unmeasured      calm                  None (moot)
        unmeasured      partial / unread      both_unread

    A regime read that raises returns a both_unread dict with no reasons (fail
    visible: we cannot show the check was moot)."""
    try:
        severity = fragility.get("severity") if isinstance(fragility, dict) else None
        if severity == "calm":
            return None
        armable = severity in _ARMABLE
        read = regime_read.read_regime(
            spy_trend_df, vix_level, trend_ma=trend_ma, vix_threshold=vix_threshold)
        state = read["state"]
        if state == "calm" or (armable and state == "risk_off"):
            return None
        if armable:
            return {
                "kind": "regime_unread",
                "reasons": [str(r) for r in read["reasons"]],
                "text": _regime_unread_text(
                    read, _BOOK_PHRASE[severity], trend_ma, vix_threshold),
            }
        reasons = [str(r) for r in read["reasons"]]
        if state == "risk_off":
            kind = "beta_unmeasured"
            text = (
                "Risk-off de-risk check couldn't run: the market is in a risk-off regime ("
                + "; ".join(reasons)
                + "), but your portfolio beta couldn't be measured, so no de-risk trim "
                "was considered today. " + _ADVICE
            )
        else:
            kind = "both_unread"
            text = _both_unread_text(regime_read.unread_phrase(read))
        note = _coverage_note(coverage)
        if note is not None:
            text += " " + note
        return {"kind": kind, "reasons": reasons, "text": text}
    except Exception:
        return {"kind": "both_unread", "reasons": [],
                "text": _both_unread_text("the market regime")}
