"""Canonical severity DISPLAY vocabulary (2026-08-04 UX audit CA1).
Display-only -- producers keep their internal literals, sort dicts, gates,
and DB tokens untouched (same discipline as the CA4 verdict-bucket fix).
No global raw-token->tier alias on purpose: "high" is a top rung in some
producers and a middle rung in others, so each render site maps ITS OWN
tokens to a tier explicitly rather than importing a shared alias.
"""

ACT_NOW, ELEVATED, WATCH, STEADY = "ACT_NOW", "ELEVATED", "WATCH", "STEADY"

SEVERITY_RANK = {ACT_NOW: 0, ELEVATED: 1, WATCH: 2, STEADY: 3}

SEVERITY_STYLE = {
    ACT_NOW:  {"icon": "🔴", "color": "#ff4444", "label": "Act Now"},
    ELEVATED: {"icon": "🟠", "color": "#ff8800", "label": "Elevated"},
    WATCH:    {"icon": "🟡", "color": "#ffbb33", "label": "Watch"},
    STEADY:   {"icon": "✅", "color": "#2e9e5b", "label": "Steady"},
}


def style(tier: str) -> dict:
    """icon/color/label for a tier. Raises KeyError on an unknown tier --
    fail loud, not a silent default, since this is a display contract."""
    return SEVERITY_STYLE[tier]
