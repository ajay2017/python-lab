# Thesis Red Team Agent — Phase 1: quantitative erosion score.
# Phase 2: Haiku counter-evidence narrative + Pre-Mortem loop (below).
#
# Pure logic — no Streamlit imports, no app.py imports.
# All inputs have safe defaults so partial data never raises.
# See docs/plans/thesis-red-team-agent.md (Phase 1) and
# docs/plans/thesis-red-team-phase2.md (Phase 2, 6 Opus review rounds) for
# the full design.

import json

from stock_analyzer.constants import (
    LLM_REQUEST_TIMEOUT_SEC,
    PT_TARGET_CUT_WARN_PCT,
    PT_TARGET_CUT_DANGER_PCT,
)

_TIER_SCORES = {"WATCH": 10, "TRIM": 22, "EXIT": 30}

_WEIGHTS = {
    "tier":       30,
    "rs":         30,
    "composite":  25,
    "pt":         15,
}


def pt_points_from_signal(signal: dict | None) -> float:
    """Map an analyst_targets.detect_pt_cut() result onto
    compute_erosion_score()'s 0-15 pt_revision_pts scale (7=flat/up/no-
    signal, 15=severe cut). An upward revision deliberately still returns 7.0,
    not a lower "good news" value — a raised PT shouldn't pull a genuinely
    eroding position (e.g. EXIT tier + underperforming) back toward "Intact,"
    and it preserves the pre-Phase-2 inert-7.0 floor so nothing scores lower
    than before. Withheld/missing -> 7.0 likewise, so behaviour is UNCHANGED
    for any ticker without enough snapshot history yet (F-169 Phase 2 —
    docs/architecture.md §6.23)."""
    if not signal or signal.get("direction") is None:
        return 7.0
    pct = signal.get("pct_change")
    if pct is None or pct >= 0:
        return 7.0
    pct_pts = pct * 100
    if pct_pts <= PT_TARGET_CUT_DANGER_PCT:
        return float(_WEIGHTS["pt"])
    if pct_pts <= PT_TARGET_CUT_WARN_PCT:
        span = PT_TARGET_CUT_WARN_PCT - PT_TARGET_CUT_DANGER_PCT  # 8.0
        frac = (PT_TARGET_CUT_WARN_PCT - pct_pts) / span  # 0 at -7%, 1 at -15%
        return 7.0 + frac * (float(_WEIGHTS["pt"]) - 7.0)
    return 7.0  # real but sub-warning cut — not alarming enough to move the needle

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


# ─── Phase 2: Haiku counter-evidence narrative ──────────────────────────────
# Mirrors premortem_advisor.py's fail-open, strictly-validated shape. Every
# value handed to the prompt must already be None-safe AND finite (NaN/inf
# coerced to None by the caller before calling build_counter_evidence_inputs)
# — see docs/plans/thesis-red-team-phase2.md for why (6 review rounds, most
# finding exactly this class of bug on a different value each time).
# Deliberately excludes erosion_score/erosion_label/pt_pts from the prompt —
# they either don't exist yet (PT) or are aggregates that can launder a
# placeholder/synthetic component back in; the bear case is built only from
# primitive, individually-real signals.

_COUNTER_EVIDENCE_SYSTEM_PROMPT = """You are a bear-case analyst reviewing a position the investor already holds. Using ONLY the signals given below, identify the 2-3 strongest SPECIFIC counter-arguments the data currently supports against the original thesis.

Every counter-argument MUST reference a specific value from the evidence given (a number, a date, a signal name) — a generic statement that could apply to any stock ("markets are unpredictable", "risk exists") is a FAILURE of this task.

If the investor's pre-mortem commitment is given and current evidence supports it, say so explicitly, quoting the commitment.

If the signals are too weak to support any grounded counter-argument, return an empty array — do not invent one.

Do not recommend selling and do not hedge with disclaimers; describe what the data currently shows, not what to do about it.

Respond with ONLY a JSON array, no other text before or after:
[{"claim": "...", "severity": "low"|"medium"|"high", "signal_basis": "..."}]
— max 3 items, min 0."""


def build_counter_evidence_inputs(
    ticker,
    price,                  # float | None — omit line if None
    entry_price,            # float | None — omit line if None
    position_age_days,      # int | None — omit line if None
    user_thesis,            # str, non-empty (caller guarantees via trigger)
    premortem_commitment,   # str | None — omit if None/empty
    tier,                   # str | None ("WATCH"/"TRIM"/"EXIT"/None)
    rs_vs_spy,              # float | None — omit line if None
    composite_delta,        # float | None — omit line if None (bootstrap or non-finite)
):
    """
    Assemble the (system_prompt, user_prompt) pair for generate_counter_evidence().

    Every optional value must already be caller-coerced to None when missing
    or non-finite (NaN/inf) — this function only decides whether to include
    a line, it does not itself validate finiteness. See the Phase 2 plan's
    "Round 4/5 fix" for exactly which caller-side values need this.
    """
    lines = [f"Ticker: {ticker}", f"\nOriginal thesis: {user_thesis}"]

    if premortem_commitment and str(premortem_commitment).strip():
        lines.append(
            f'\nPre-mortem commitment (written at buy time): '
            f'"{str(premortem_commitment).strip()}"'
        )

    lines.append("\nCurrent signals:")
    if price is not None:
        lines.append(f"- Current price: ${price:.2f}")
    if entry_price is not None:
        lines.append(f"- Entry price: ${entry_price:.2f}")
    if position_age_days is not None:
        lines.append(f"- Position age: {position_age_days} days")
    if tier:
        lines.append(f"- Deterioration tier: {tier} (an active exit-discipline signal)")
    else:
        lines.append("- Deterioration tier: none active")
    if rs_vs_spy is not None:
        lines.append(f"- Relative strength vs SPY (20-session): {rs_vs_spy:+.1f} percentage points")
    else:
        lines.append("- Relative strength vs SPY: not available today")
    if composite_delta is not None:
        lines.append(f"- Composite score trend (5-session): {composite_delta:+.1f} points")
    else:
        lines.append("- Composite score trend: not enough trading-day history yet to compute")

    lines.append("\nWrite the counter-evidence now, as the JSON array specified.")
    return _COUNTER_EVIDENCE_SYSTEM_PROMPT, "\n".join(lines)


_SEVERITIES = ("low", "medium", "high")


def parse_counter_evidence_response(text):
    """
    Validate a raw Haiku response into [{"claim","severity","signal_basis"}]
    (0-3 items) or None on any failure.

    An empty list IS a valid result ("no grounded bear case found today") —
    distinct from None (the call itself failed or returned garbage). Callers
    must test `is None` / `is not None`, never truthiness. All-or-nothing:
    any single malformed item drops the whole response (tightens the
    original Phase 1 plan's per-item-drop spec to match
    premortem_advisor._parse_case_against's stricter bar).
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
        cleaned = cleaned.strip()
    if not cleaned.startswith("["):
        start = cleaned.find("[")
        end   = cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    if not isinstance(parsed, list) or len(parsed) > 3:
        return None
    out = []
    for item in parsed:
        if not isinstance(item, dict):
            return None
        claim        = str(item.get("claim", "")).strip()
        severity     = str(item.get("severity", "")).strip().lower()
        signal_basis = str(item.get("signal_basis", "")).strip()
        if not claim or severity not in _SEVERITIES or not signal_basis:
            return None
        out.append({"claim": claim, "severity": severity, "signal_basis": signal_basis})
    return out


def generate_counter_evidence(
    ticker,
    inputs,
    api_key,
    model="claude-haiku-4-5-20251001",
    max_tokens=600,
):
    """
    Generate the Haiku bear-case narrative for `ticker`. Returns validated
    [{"claim","severity","signal_basis"}] (0-3 items, possibly empty — a
    valid result) or None on ANY failure (no key, API error, malformed
    response). Fail-open: the entire API call + parse is wrapped in one
    try/except, mirroring premortem_advisor.generate_case_against() exactly
    — the sole call site sits inside a per-ticker compute loop with no
    surrounding try/except of its own, so this function must never let an
    exception escape.
    """
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        system_prompt, user_prompt = inputs
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text = response.content[0].text.strip() if response.content else ""
        return parse_counter_evidence_response(text)
    except Exception:
        return None
