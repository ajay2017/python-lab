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
    if score >= 72:
        return {
            "label": "Strong Buy",
            "color": "#00C851",
            "icon": "⬆⬆",
            "rationale": "Strong technical momentum, solid fundamentals, and positive sentiment all align.",
        }
    elif score >= 58:
        return {
            "label": "Buy",
            "color": "#00b300",
            "icon": "⬆",
            "rationale": "Positive signals across most dimensions. Favorable risk/reward entry.",
        }
    elif score >= 44:
        return {
            "label": "Hold",
            "color": "#ffbb33",
            "icon": "➡",
            "rationale": "Mixed signals. Current holders should maintain position; new entries may wait for clearer trend.",
        }
    elif score >= 30:
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
