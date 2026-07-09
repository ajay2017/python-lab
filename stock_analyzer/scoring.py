from stock_analyzer.constants import (
    COMPOSITE_STRONG_BUY,
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    COMPOSITE_SELL,
    COMPOSITE_WEIGHTS,
)

# Backwards-compat alias — external imports `from stock_analyzer.scoring
# import WEIGHTS` keep working. Source of truth lives in constants.py so
# tuning the layer weighting is a policy decision, not a hidden module knob.
WEIGHTS = COMPOSITE_WEIGHTS


def combined_score(
    technical: float,
    business_quality: float,
    valuation: float,
    sentiment: float,
) -> float:
    w = WEIGHTS
    return round(
        technical        * w["technical"]
        + business_quality * w["business_quality"]
        + valuation        * w["valuation"]
        + sentiment        * w["sentiment"],
        1,
    )


def recommendation(score: float) -> dict:
    # Boundaries are imported from constants.py so the label the user sees
    # here matches the gate threshold every downstream feature uses (Grow
    # Today, Brief verdict, add-to-winner). Previously 72/58 here vs 75/65
    # in constants meant a stock labelled "Buy" at score 60 was silently
    # filtered out of Grow Today as "Composite Says No (Hold)."
    if score >= COMPOSITE_STRONG_BUY:
        return {
            "label": "Strong Buy",
            "color": "#00C851",
            "icon": "⬆⬆",
            "rationale": "Strong technical momentum, solid fundamentals, and positive sentiment all align.",
        }
    elif score >= COMPOSITE_BUY:
        return {
            "label": "Buy",
            "color": "#00b300",
            "icon": "⬆",
            "rationale": "Positive signals across most dimensions. Favorable risk/reward entry.",
        }
    elif score >= COMPOSITE_HOLD:
        return {
            "label": "Hold",
            "color": "#ffbb33",
            "icon": "➡",
            "rationale": "Mixed signals. Current holders should maintain position; new entries may wait for clearer trend.",
        }
    elif score >= COMPOSITE_SELL:
        return {
            "label": "Sell",
            "color": "#ff4444",
            "icon": "⬇",
            "rationale": "Weakening technicals or fundamentals. Consider reducing exposure.",
        }
    else:
        return {
            "label": "Strong Sell",
            "color": "#CC0000",
            "icon": "⬇⬇",
            "rationale": "Multiple bearish signals. High risk of continued decline.",
        }
