from stock_analyzer.constants import (
    COMPOSITE_STRONG_BUY,
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    COMPOSITE_SELL,
)

WEIGHTS = {
    "technical": 0.45,
    "fundamental": 0.40,
    "sentiment": 0.15,
}


def combined_score(
    technical: float,
    fundamental: float,
    sentiment: float,
) -> float:
    return round(
        technical * WEIGHTS["technical"]
        + fundamental * WEIGHTS["fundamental"]
        + sentiment * WEIGHTS["sentiment"],
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
