"""
Pre-Mortem Protocol — Concept C (next-evolution roadmap, Wave 2, Phase 1).

Before a prospective LIVE Buy decision, generates a position-specific
counterargument citing the composite pillar driving the score, the
portfolio's current concentration/tilt, and macro/earnings context — then
the investor must write a falsifiable pre-commitment ("what would make me
wrong about this") before the trade writes. Not a warning label; a structured
thinking protocol built into the transaction workflow.

Design principles (mirrors thesis_advisor.py / analyst_intel.py):
- LLM narrates only what it is given — it cannot invent numbers or events.
- Returns None on ANY failure so the caller falls back to a plain generic
  prompt. The HARD gate — a non-empty pre-commitment — is enforced in pure
  Python/UI, never by the LLM, so a rate-limit or outage can never block a
  legitimate Buy.
- Awareness/friction only: never re-scores, re-gates, reorders, sizes, or
  vetoes the recommendation. It stress-tests the buy; it does not oppose it.
- Scoped to prospective LIVE Buy decisions only (the caller's responsibility —
  never wired to broker/screenshot/split imports or the recalculate_from_trades
  replay, which have no live decision to stress-test).

Entry points:
  build_premortem_inputs()  — assembles the structured evidence package.
  generate_case_against()   — the LLM call; returns exactly 3 counterarguments
                               (one per angle) or None on any failure.
"""

import json
from datetime import datetime, timezone

from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC

_ANGLES = ("pillar", "portfolio", "macro")

_CASE_AGAINST_SYSTEM_PROMPT = """You are a disciplined portfolio risk analyst. An investor is about to buy a stock the app's engine rates favorably. Your job is to build the strongest SPECIFIC case for why this could be a mistake, using ONLY the evidence given below.

Produce exactly 3 counterarguments, one for each angle:
1. PILLAR CONCERN — name the specific composite pillar (fundamentals, valuation, momentum, or sentiment) driving the score, and explain a concrete way that pillar's read could be wrong, fragile, or about to reverse. Reference the specific evidence given for that pillar.
2. PORTFOLIO IMPACT — using the investor's current sector/position concentration given below, explain specifically how adding this position changes their portfolio's risk profile (e.g. concentrates a sector further, adds correlated exposure to an existing tilt). If they have no other positions yet, say so explicitly as the counterargument (this trade sets 100% of the book's initial concentration/correlation profile) — do not skip the angle.
3. MACRO/EARNINGS CONTEXT — using the regime and/or earnings-calendar context given below, name a specific near-term event or backdrop that could hurt this specific position. If no regime or earnings context is given, say so explicitly as the counterargument (no macro read is available to weigh against this entry) — do not skip the angle or invent one.

CRITICAL RULES:
- Every counterargument MUST reference specific evidence given below — a name, a number, a date, a sector, a score. NEVER write a generic statement that could apply to any stock ("stocks can go down", "markets are unpredictable", "consider your risk tolerance"). A generic counterargument is a FAILURE of this task — find the specific angle in THIS evidence, or explicitly note the evidence is missing for that angle per the rules above.
- Do not invent facts, numbers, or events not given in the evidence.
- Do not recommend against the buy and do not hedge with disclaimers — you are stress-testing the decision, not vetoing it. Frame each as what would have to be true for this specific trade to go wrong.
- Each counterargument: 1-2 sentences, plain language, no bullet symbols or markdown inside the text.

Respond with ONLY a JSON array of exactly 3 objects, no other text before or after:
[{"angle": "pillar", "argument": "..."}, {"angle": "portfolio", "argument": "..."}, {"angle": "macro", "argument": "..."}]"""


def driving_pillar_from_bundle(bundle: dict) -> dict:
    """
    Identify which composite pillar is driving the score from a load_all()-
    shaped bundle, and pull the specific signals behind it for a position-
    specific counterargument (§1 of the case-against prompt).

    Pure; None-safe. Bundle keys per bundle_loader.load_all(): t_score/
    t_signals (momentum), bq_score/bq_signals (fundamentals, aliased f_score/
    f_signals), val_score/val_signals (valuation), s_score (sentiment — no
    signals list; the top headlines stand in for it).

    Returns {"driving_pillar": str|None, "driving_signals": list[str]} —
    degrades to (None, []) if no pillar scores are present in the bundle.
    """
    if not bundle:
        return {"driving_pillar": None, "driving_signals": []}
    _headline_signals = [
        h.get("headline", "") for h in (bundle.get("headlines") or [])
        if isinstance(h, dict) and h.get("headline")
    ][:3]
    _pillars = {
        "momentum":     (bundle.get("t_score"), bundle.get("t_signals") or []),
        "fundamentals": (
            bundle.get("bq_score") if bundle.get("bq_score") is not None else bundle.get("f_score"),
            bundle.get("bq_signals") or bundle.get("f_signals") or [],
        ),
        "valuation":    (bundle.get("val_score"), bundle.get("val_signals") or []),
        "sentiment":    (bundle.get("s_score"), _headline_signals),
    }
    scored = {k: v for k, (v, _sig) in _pillars.items() if v is not None}
    if not scored:
        return {"driving_pillar": None, "driving_signals": []}
    top = max(scored, key=scored.get)
    return {"driving_pillar": top, "driving_signals": _pillars[top][1]}


def build_premortem_inputs(
    ticker: str,
    engine: dict | None = None,
    portfolio: dict | None = None,
    macro: dict | None = None,
    earnings: dict | None = None,
) -> dict:
    """
    Assemble the structured evidence package passed to generate_case_against().

    engine keys (all optional):
        composite (float 0-100), band (str e.g. "Strong Buy"),
        driving_pillar (str: "fundamentals"|"valuation"|"momentum"|"sentiment"),
        driving_signals (list[str] — the specific factors behind that pillar)
    portfolio keys (all optional):
        n_positions (int), top_sector (str), top_sector_weight_pct (float),
        this_sector (str — the sector of the ticker being bought)
    macro keys (all optional):
        label (str — e.g. "Inflation Fight"), confidence (int, 0-100)
    earnings keys (all optional):
        next_earnings_date (str), note (str)
    """
    return {
        "ticker":    ticker,
        "engine":    engine or {},
        "portfolio": portfolio or {},
        "macro":     macro or {},
        "earnings":  earnings or {},
    }


def _format_case_against_prompt(ticker: str, inputs: dict) -> str:
    lines = [f"Ticker: {ticker}", "\nEvidence available:"]

    eng = inputs.get("engine") or {}
    if eng:
        parts = []
        if eng.get("composite") is not None:
            parts.append(f"Composite score {eng['composite']:.0f}/100")
        if eng.get("band"):
            parts.append(f"({eng['band']})")
        if parts:
            lines.append("Engine read: " + " ".join(parts) + ".")
        if eng.get("driving_pillar"):
            _sig = eng.get("driving_signals") or []
            _sig_txt = f" Specific factors: {'; '.join(_sig[:5])}." if _sig else ""
            lines.append(
                f"Driving pillar: {eng['driving_pillar']}.{_sig_txt}"
            )

    pf = inputs.get("portfolio") or {}
    if pf:
        parts = []
        if pf.get("n_positions") is not None:
            parts.append(f"{pf['n_positions']} existing position(s) in the portfolio.")
        if pf.get("top_sector") and pf.get("top_sector_weight_pct") is not None:
            parts.append(
                f"Largest current sector concentration: {pf['top_sector']} at "
                f"{pf['top_sector_weight_pct']:.1f}% of the book."
            )
        if pf.get("this_sector"):
            parts.append(f"This new position's sector: {pf['this_sector']}.")
        if parts:
            lines.append("Portfolio: " + " ".join(parts))
        else:
            lines.append("Portfolio: no existing-position data available.")
    else:
        lines.append("Portfolio: no existing-position data available (e.g. first trade this session).")

    mc = inputs.get("macro") or {}
    if mc.get("label"):
        _conf = f" ({mc['confidence']}% confidence)" if mc.get("confidence") is not None else ""
        lines.append(f"Macro regime: {mc['label']}{_conf}.")
    else:
        lines.append("Macro regime: not available this session.")

    ec = inputs.get("earnings") or {}
    if ec.get("next_earnings_date") or ec.get("note"):
        parts = []
        if ec.get("next_earnings_date"):
            parts.append(f"Next earnings {ec['next_earnings_date']}.")
        if ec.get("note"):
            parts.append(str(ec["note"]))
        lines.append("Earnings context: " + " ".join(parts))
    else:
        lines.append("Earnings context: not available.")

    lines.append("\nWrite the 3 counterarguments now, as the JSON array specified.")
    return "\n".join(lines)


def _parse_case_against(text: str) -> list[dict] | None:
    """Robust JSON-array parse: strip markdown fences, then slice to the
    first '[' .. last ']'. Validates exactly 3 well-formed items. Returns
    None on any malformed shape — the quality bar for this feature is that a
    broken/short/generic response never reaches the UI silently disguised as
    a real counterargument."""
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
    if not isinstance(parsed, list) or len(parsed) != 3:
        return None
    out = []
    seen_angles = set()
    for item in parsed:
        if not isinstance(item, dict):
            return None
        angle = str(item.get("angle", "")).strip().lower()
        arg   = str(item.get("argument", "")).strip()
        if angle not in _ANGLES or not arg:
            return None
        seen_angles.add(angle)
        out.append({"angle": angle, "argument": arg})
    if seen_angles != set(_ANGLES):
        return None
    return out


def generate_case_against(
    ticker: str,
    inputs: dict,
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 700,
) -> dict | None:
    """
    Generate the app-side case against buying `ticker`, from the engine's own
    evidence. Returns a dict with keys:
        case_against — list of exactly 3 {"angle", "argument"} dicts
        model        — model used
        generated_at — ISO timestamp (UTC)

    Returns None on ANY failure (no key, API error, malformed/short/generic
    response) — the caller must fall back to a plain manual prompt. This is
    the aid, not the gate: the pre-commitment text field is the hard
    requirement, and it is enforced independently of this call succeeding.
    """
    if not api_key:
        return None
    try:
        import anthropic
        client      = anthropic.Anthropic(api_key=api_key)
        user_prompt = _format_case_against_prompt(ticker, inputs)
        response    = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,  # structured JSON output — deterministic, not prose
            system=_CASE_AGAINST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text = response.content[0].text.strip() if response.content else ""
        case_against = _parse_case_against(text)
        if not case_against:
            return None
        return {
            "case_against": case_against,
            "model":        model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None
