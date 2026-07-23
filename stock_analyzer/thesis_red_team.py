# Thesis Red Team Agent — Phase 1: quantitative erosion score
# Phase 2 (Haiku counter-evidence) is added separately after Phase 1 validates in production.
#
# Pure logic — no Streamlit imports, no app.py imports.
# All inputs have safe defaults so partial data never raises.
# See docs/plans/thesis-red-team-agent.md for the full design.

_TIER_SCORES = {"WATCH": 10, "TRIM": 22, "EXIT": 30}

_WEIGHTS = {
    "tier":       30,
    "rs":         30,
    "composite":  25,
    "pt":         15,
}

# Display-band thresholds (score → label). Checked in descending order;
# first threshold the score meets wins.  These are display-copy tuning
# values, NOT gate/recommendation inputs — tune against live production data
# for ~1 week, then adjust without Opus review. Escalate to constants.py
# only if any band ever feeds a gate.
_LABELS = [
    (75, "Breaking"),
    (50, "Eroding"),
    (25, "Softening"),
    (0,  "Intact"),
]


def compute_erosion_score(
    tier,               # str | None  ("WATCH"/"TRIM"/"EXIT"/None)
    rs_vs_spy,          # float, pct-pts; negative = underperforming SPY
    composite_delta,    # float; negative = composite falling; 0 if unknown
    pt_revision_pts,    # float: 0=upward/flat-up, 7=flat, 15=cut
):
    """
    Return {"score": float 0-100, "label": str, "components": dict}.
    All inputs have safe defaults so partial data never raises.

    Component weights (30/30/25/15) are module-level calibration values, NOT
    in constants.py — they drive a display label only with no gate or
    recommendation path. Escalate to constants.py if that ever changes.
    """
    tier_pts      = _TIER_SCORES.get(tier or "", 0)
    rs_pts        = max(0.0, min(float(_WEIGHTS["rs"]),   -float(rs_vs_spy)        * 1.5))
    comp_pts      = max(0.0, min(float(_WEIGHTS["composite"]), -float(composite_delta) * 2.5))
    pt_pts        = max(0.0, min(float(_WEIGHTS["pt"]),   float(pt_revision_pts)))

    score = tier_pts + rs_pts + comp_pts + pt_pts

    label = "Intact"
    for threshold, lbl in _LABELS:
        if score >= threshold:
            label = lbl
            break

    return {
        "score": round(score, 1),
        "label": label,
        "components": {
            "tier_pts":          tier_pts,
            "rs_pts":            round(rs_pts, 1),
            "composite_pts":     round(comp_pts, 1),
            "pt_pts":            round(pt_pts, 1),
            "tier":              tier,
            "rs_vs_spy":         round(float(rs_vs_spy), 2),
            "composite_delta":   round(float(composite_delta), 2),
            "pt_revision_pts":   float(pt_revision_pts),
        },
    }
