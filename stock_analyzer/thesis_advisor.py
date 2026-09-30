"""
Thesis Advisor — F-1 AI Intelligence Layer.

Periodically reviews a user's written investment thesis against current
evidence (news, fundamentals, technical trend) and returns a structured
verdict: INTACT, WEAKENING, or BROKEN.

Design principles:
- LLM narrates only what it is given — it cannot invent news or events.
- Returns None on any failure so callers surface an explicit offline state.
- Conservative by default: BROKEN requires clear contradicting evidence.
- All thresholds and gates remain with the rule-based engine; this module
  only produces awareness text.

Entry points:
  review_thesis()     — single-position review (on-demand or batch).
  run_batch_review()  — weekly batch across all open positions with a thesis.
  build_review_inputs() — assembles the structured evidence package.
"""

import hashlib
import json
from datetime import date, datetime, timezone

from stock_analyzer.constants import (
    LLM_REQUEST_TIMEOUT_SEC,
    COMPOSITE_STRONG_BUY,
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    COMPOSITE_SELL,
    PT_TARGET_CUT_WARN_PCT,
    THESIS_DELTA_COMPOSITE_PTS,
    THESIS_DELTA_EROSION_PTS,
)
# thesis_card.py does not import this module (verified), so this direction
# is safe -- no import cycle.
from stock_analyzer.thesis_card import TAG_DERIVED, TAG_ANALYST, TAG_AI


# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a disciplined portfolio analyst helping an individual investor check whether their original investment thesis still holds.

Your job: given the investor's original thesis and current evidence, assess whether the thesis is INTACT, WEAKENING, or BROKEN. Write a concise 2-3 sentence explanation.

Rules:
- Only use facts from the evidence provided. Do not invent events, price targets, or analyst opinions. If an 'External analyst coverage' line is provided in the evidence, that consensus is REAL and you MAY cite it as supporting or contradicting context — but it is CONTEXT ONLY: analyst agreement never makes a broken thesis intact. Grade the user's specific thesis claim against the evidence; a thesis whose stated rationale is contradicted stays WEAKENING or BROKEN even if analysts remain bullish.
- INTACT: evidence is broadly consistent with the original conviction.
- WEAKENING: some evidence contradicts; not yet decisive. Use this when signals are mixed.
- BROKEN: evidence materially contradicts the key condition the investor stated, or the core premise has clearly reversed.
- Be conservative: default to WEAKENING when uncertain. BROKEN requires a clear, specific contradiction.
- Do not recommend buying, selling, or any portfolio action. Observation only.
- Do not add disclaimers. Plain language. No bullet points. Prose only.
- End your response with exactly one verdict line on its own:
    Verdict: INTACT
    Verdict: WEAKENING
    Verdict: BROKEN"""


def _format_prompt(ticker: str, user_thesis: str, inputs: dict) -> str:
    lines = [
        f"Ticker: {ticker}",
        f"\nOriginal investment thesis:\n\"{user_thesis}\"",
        "\nCurrent evidence:",
    ]

    tech = inputs.get("technical", {})
    if tech:
        trend = "above" if tech.get("above_sma50") else "below"
        rsi   = tech.get("rsi")
        mom   = tech.get("momentum_1m_pct")
        parts = [f"Price is {trend} the 50-day moving average."]
        if rsi is not None:
            zone = "overbought (>70)" if rsi > 70 else ("oversold (<30)" if rsi < 30 else "neutral")
            parts.append(f"RSI {rsi:.0f} ({zone}).")
        if mom is not None:
            parts.append(f"1-month price change: {mom:+.1f}%.")
        lines.append("Technical: " + " ".join(parts))

    fund = inputs.get("fundamentals", {})
    if fund:
        parts = []
        if fund.get("revenue_growth") is not None:
            parts.append(f"Revenue growth: {fund['revenue_growth']:+.1f}%.")
        if fund.get("profit_margin") is not None:
            parts.append(f"Profit margin: {fund['profit_margin']:.1f}%.")
        if fund.get("earnings_trend"):
            parts.append(f"Earnings trend: {fund['earnings_trend']}.")
        if parts:
            lines.append("Fundamentals: " + " ".join(parts))

    headlines = inputs.get("news_headlines", [])
    if headlines:
        lines.append(f"Recent news ({len(headlines)} headlines):")
        for h in headlines[:12]:
            lines.append(f"  - {h}")

    earnings = inputs.get("last_earnings", {})
    if earnings:
        parts = []
        if earnings.get("result"):
            parts.append(f"Last earnings: {earnings['result']}.")
        if earnings.get("guidance"):
            parts.append(f"Guidance: {earnings['guidance']}.")
        if parts:
            lines.append("Earnings: " + " ".join(parts))

    analyst = inputs.get("analyst_consensus", {})
    if analyst:
        parts = []
        if analyst.get("consensus_rating"):
            parts.append(f"Consensus rating: {analyst['consensus_rating']}.")
        if analyst.get("avg_pt") is not None:
            try:
                _apt = float(analyst["avg_pt"])
                parts.append(f"Avg price target ${_apt:.2f} across {analyst.get('n_firms', '?')} firm(s).")
            except (TypeError, ValueError):
                pass
        _asof = analyst.get("as_of")
        if _asof and str(_asof).lower() != "none":
            parts.append(f"(coverage as of {_asof})")
        if analyst.get("thesis"):
            parts.append("Analyst thesis points: " + "; ".join(analyst['thesis'][:2]) + ".")
        if parts:
            lines.append("External analyst coverage: " + " ".join(parts))

    return "\n".join(lines)


def _parse_response(text: str) -> dict:
    """Extract status and summary from LLM response."""
    status  = "WEAKENING"  # safe default
    summary = text.strip()

    lines = [ln.strip() for ln in text.strip().splitlines()]
    # Use the LAST "verdict:" line for BOTH the status and the summary boundary, so
    # a stray "verdict:" in the body prose can't truncate the summary at an earlier
    # line than the one the status was read from (the two indices used to disagree).
    verdict_idx = next(
        (i for i in range(len(lines) - 1, -1, -1) if "verdict:" in lines[i].lower()),
        -1,
    )
    if verdict_idx >= 0:
        low = lines[verdict_idx].lower()
        if "intact" in low:
            status = "INTACT"
        elif "broken" in low:
            status = "BROKEN"
        else:
            status = "WEAKENING"
        if verdict_idx > 0:
            summary = " ".join(lines[:verdict_idx]).strip()

    return {"status": status, "summary": summary}


# ── Public API ────────────────────────────────────────────────────────────────

def build_review_inputs(
    technical: dict | None = None,
    fundamentals: dict | None = None,
    news_headlines: list[str] | None = None,
    last_earnings: dict | None = None,
    analyst_consensus: dict | None = None,
) -> dict:
    """
    Assemble the structured evidence package passed to review_thesis().

    technical keys (all optional):
        above_sma50 (bool), rsi (float), momentum_1m_pct (float)
    fundamentals keys (all optional):
        revenue_growth (float, %), profit_margin (float, %), earnings_trend (str)
    news_headlines: list of plain-text headline strings (last 30 days)
    last_earnings keys (all optional):
        result (str e.g. "beat EPS by 8%"), guidance (str e.g. "raised FY guidance")
    analyst_consensus keys (all optional):
        consensus_rating (str), avg_pt (float), n_firms (int), as_of (str),
        thesis (list[str]) — newest saved coverage row from analyst_coverage table
    """
    return {
        "technical":         technical         or {},
        "fundamentals":      fundamentals       or {},
        "news_headlines":    news_headlines     or [],
        "last_earnings":     last_earnings      or {},
        "analyst_consensus": analyst_consensus  or {},
    }


def inputs_hash(inputs: dict) -> str:
    """Stable hash of review inputs — used to detect staleness."""
    serialized = json.dumps(inputs, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def review_thesis(
    ticker: str,
    user_thesis: str,
    inputs: dict,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 300,
) -> dict | None:
    """
    Call the LLM to review a single investment thesis against current evidence.

    Returns a dict with keys:
        status   — 'INTACT' | 'WEAKENING' | 'BROKEN'
        summary  — 2-3 sentence explanation (~100 words)
        raw      — original LLM text
        model    — model used
        reviewed_at — ISO timestamp (UTC)

    Returns None on any failure — caller must surface an explicit offline state.
    """
    if not api_key or not user_thesis or not user_thesis.strip():
        return None
    try:
        import anthropic
        client      = anthropic.Anthropic(api_key=api_key)
        user_prompt = _format_prompt(ticker, user_thesis, inputs)
        response    = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text   = response.content[0].text if response.content else ""
        parsed = _parse_response(text)
        return {
            **parsed,
            "raw":         text,
            "model":       model,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def run_batch_review(
    positions: list[dict],
    api_key: str,
    model: str = "claude-sonnet-4-6",
) -> list[dict]:
    """
    Run thesis reviews for a list of open positions.

    Each item in `positions` must have:
        ticker       (str)
        trade_date   (str | date)  — earliest BUY date for this position
        user_thesis  (str)
        inputs       (dict)        — from build_review_inputs()

    Returns a list of save-ready records for db.save_thesis_review(), one per
    position that was reviewed. Positions with no thesis or failed LLM calls
    are silently skipped (caller checks returned list length).
    """
    results = []
    for pos in positions:
        ticker      = pos.get("ticker", "")
        user_thesis = pos.get("user_thesis", "")
        trade_date  = pos.get("trade_date", date.today())
        ev_inputs   = pos.get("inputs", {})

        if not ticker or not user_thesis or not user_thesis.strip():
            continue

        result = review_thesis(ticker, user_thesis, ev_inputs, api_key, model)
        if result is None:
            continue

        results.append({
            "ticker":      ticker,
            "trade_date":  str(trade_date),
            "reviewed_at": result["reviewed_at"],
            "status":      result["status"],
            "summary":     result["summary"],
            "inputs_hash": inputs_hash(ev_inputs),
        })

    return results


def already_reviewed_today(ticker: str, reviews_df, today_et: date) -> bool:
    """True if `reviews_df` (as returned by db.load_thesis_reviews()) already
    holds a row for `ticker` whose `reviewed_at` (a UTC ISO timestamp) falls
    on `today_et`'s ET calendar date.

    Makes the weekly thesis cron lane idempotent against a duplicate firing.
    Live production data confirmed 2026-09-29 that this lane can fire more
    than once on the same Sunday (24 tickers found with a near-duplicate
    review roughly an hour apart, across 5 separate weeks) with nothing to
    stop it — every extra firing silently paid for a second LLM call and
    wrote a second near-identical row per open position. This check is the
    fix: skip a ticker already reviewed today, whatever the duplicate
    firing's actual cause turns out to be.

    A row with a missing or unparseable `reviewed_at` is never treated as a
    match — fails toward "not yet reviewed today" so a malformed historical
    row can never accidentally suppress a legitimate review.
    """
    import pytz

    if reviews_df is None or getattr(reviews_df, "empty", True):
        return False
    t = str(ticker or "").strip().upper()
    if not t:
        return False
    et = pytz.timezone("America/New_York")
    same_ticker = reviews_df[reviews_df["ticker"].astype(str).str.upper() == t]
    for raw in same_ticker.get("reviewed_at", []):
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts.astimezone(et).date() == today_et:
                return True
        except (TypeError, ValueError):
            continue
    return False


# ── Thesis authoring (F-5) ──────────────────────────────────────────────────
#
# Generative complement to the reviewer above. Given the engine's evidence for a
# candidate the user is about to buy, draft a CANDIDATE investment thesis the
# user then edits and owns. Advisory only — this never gates and never decides.
#
# Two invariants (see docs/plans/thesis-authoring-analyst-desk.md):
#   - The user is always the author of record. The draft is offered into an
#     editable field and is NEVER persisted without the user accepting it.
#   - The author (this module) reads entry-time evidence; the reviewer
#     (review_thesis) weights post-entry evidence — they are not the same call.

_DRAFT_SYSTEM_PROMPT = """You are helping an individual investor write the investment thesis for a stock they are about to buy. You draft a CANDIDATE thesis; the investor will edit it and own the final words.

Write the thesis as flowing prose (not labelled sections), covering three things:
1. The durable claim — why this company wins over the medium term: competitive position, demand, a catalyst. This is the conviction, not the entry timing.
2. The supporting evidence — drawn ONLY from the evidence provided below. Do not invent a number, an order book, an analyst opinion, or an event.
3. A falsifiable condition — end with one sentence beginning "Breaks if " that names the specific developments which would invalidate the thesis.

Rules:
- Ground every claim in the evidence given. If the evidence is thin, write a shorter, honest thesis — never pad it with invented facts.
- Price levels, moving averages, RSI and momentum are ENTRY TIMING, not the thesis. Do not build the thesis on them.
- No price targets. No probabilities or odds of success. No buy/sell/hold language. No gate or score values.
- Plain language. No preamble, no disclaimer, no bullet points. Prose only.
- Keep it under 500 characters.
- End with exactly one sentence starting "Breaks if "."""


def _format_authoring_prompt(ticker: str, inputs: dict) -> str:
    lines = [f"Ticker: {ticker}"]
    if inputs.get("company_name"):
        lines.append(f"Company: {inputs['company_name']}")
    if inputs.get("sector"):
        lines.append(f"Sector: {inputs['sector']}")

    lines.append("\nEvidence available:")

    eng = inputs.get("engine", {})
    if eng:
        parts = []
        if eng.get("composite") is not None:
            parts.append(f"Composite score {eng['composite']:.0f}/100")
        if eng.get("band"):
            parts.append(f"({eng['band']})")
        if eng.get("conviction"):
            parts.append(f"conviction {eng['conviction']}")
        if parts:
            lines.append(
                "Engine read (context only — do not restate as a recommendation): "
                + " ".join(parts) + "."
            )
        gates = eng.get("gates_cleared") or []
        if gates:
            lines.append("Cleared entry checks: " + ", ".join(gates) + ".")

    fund = inputs.get("fundamentals", {})
    if fund:
        parts = []
        if fund.get("revenue_growth") is not None:
            parts.append(f"Revenue growth {fund['revenue_growth']:+.1f}%.")
        if fund.get("profit_margin") is not None:
            parts.append(f"Profit margin {fund['profit_margin']:.1f}%.")
        if fund.get("earnings_trend"):
            parts.append(f"Earnings trend: {fund['earnings_trend']}.")
        if parts:
            lines.append("Fundamentals: " + " ".join(parts))

    cat = inputs.get("catalyst", {})
    if cat:
        parts = []
        if cat.get("next_earnings_date"):
            parts.append(f"Next earnings {cat['next_earnings_date']}.")
        if cat.get("note"):
            parts.append(str(cat["note"]))
        if parts:
            lines.append("Catalyst: " + " ".join(parts))

    headlines = inputs.get("news_headlines", [])
    if headlines:
        lines.append(f"Recent news ({len(headlines)} headlines):")
        for h in headlines[:12]:
            lines.append(f"  - {h}")

    tech = inputs.get("technical", {})
    if tech:
        trend = "above" if tech.get("above_sma50") else "below"
        parts = [f"Price is {trend} the 50-day moving average."]
        if tech.get("rsi") is not None:
            parts.append(f"RSI {tech['rsi']:.0f}.")
        if tech.get("momentum_1m_pct") is not None:
            parts.append(f"1-month change {tech['momentum_1m_pct']:+.1f}%.")
        lines.append("Entry timing (NOT the thesis): " + " ".join(parts))

    if inputs.get("regime"):
        lines.append(f"Market regime: {inputs['regime']}.")

    lines.append("\nWrite the candidate thesis now.")
    return "\n".join(lines)


def build_authoring_inputs(
    company_name: str | None = None,
    sector: str | None = None,
    engine: dict | None = None,
    fundamentals: dict | None = None,
    catalyst: dict | None = None,
    news_headlines: list[str] | None = None,
    technical: dict | None = None,
    regime: str | None = None,
) -> dict:
    """
    Assemble the structured evidence package passed to draft_thesis().

    engine keys (all optional):
        composite (float 0-100), band (str e.g. "Strong Buy"),
        conviction (str), gates_cleared (list[str])
    fundamentals keys (all optional):
        revenue_growth (float, %), profit_margin (float, %), earnings_trend (str)
    catalyst keys (all optional):
        next_earnings_date (str), note (str)
    news_headlines: list of plain-text headline strings (last ~30 days)
    technical keys (all optional, labelled to the LLM as entry timing only):
        above_sma50 (bool), rsi (float), momentum_1m_pct (float)
    regime: short market-regime tag string
    """
    return {
        "company_name":   company_name,
        "sector":         sector,
        "engine":         engine         or {},
        "fundamentals":   fundamentals   or {},
        "catalyst":       catalyst       or {},
        "news_headlines": news_headlines or [],
        "technical":      technical      or {},
        "regime":         regime,
    }


def draft_thesis(
    ticker: str,
    inputs: dict,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 300,
) -> dict | None:
    """
    Draft a CANDIDATE investment thesis for `ticker` from the engine's evidence.

    Returns a dict with keys:
        draft        — the candidate thesis text (prose; ends with "Breaks if ...")
        model        — model used
        generated_at — ISO timestamp (UTC)

    Returns None on any failure — the caller must surface an explicit offline
    state and fall back to a plain manual text field. The returned draft is a
    CANDIDATE only; the user edits and owns the final text (never auto-saved).
    """
    if not api_key:
        return None
    try:
        import anthropic
        client      = anthropic.Anthropic(api_key=api_key)
        user_prompt = _format_authoring_prompt(ticker, inputs)
        response    = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_DRAFT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text = response.content[0].text.strip() if response.content else ""
        if not text:
            return None
        return {
            "draft":        text,
            "model":        model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def bundle_evidence(bundle: dict) -> dict:
    """Extract the thesis-relevant evidence — technical trend, fundamentals, and
    recent news headlines — from a load_bundle() result.

    Shared by F-1 review and F-5 authoring so the two paths can NEVER drift on
    the bundle's key names. The bundle stores technicals in the `df` DataFrame
    (Close / SMA_50 / RSI columns), fundamentals under nested `financials`
    (yfinance FRACTIONS → converted to percent here), and processed news under
    `headlines` — NOT under `indicators` / `revenue_growth` / `news` (reading
    those silently fed the LLM empty evidence: the bug this helper closes).

    Pure (no Streamlit; duck-typed DataFrame access, no pandas import). Every
    field degrades to None / empty on absence, so a thin/empty bundle yields a
    thin — but honest — package rather than an error.

    Returns {"technical": {...}, "fundamentals": {...}, "news_headlines": [...]}
    shaped to drop straight into build_review_inputs() / build_authoring_inputs().
    """
    fin = bundle.get("financials") or {}

    def _pct(x):
        try:
            return float(x) * 100
        except Exception:
            return None

    technical = {}
    df = bundle.get("df")
    try:
        if df is not None and not df.empty:
            close = df["Close"].dropna()
            sma50 = df["SMA_50"].iloc[-1] if "SMA_50" in df.columns else None
            technical = {
                "above_sma50": (bool(close.iloc[-1] > sma50)
                                if sma50 is not None and not close.empty else None),
                "rsi": (float(df["RSI"].iloc[-1])
                        if "RSI" in df.columns and not df["RSI"].dropna().empty else None),
                "momentum_1m_pct": (float((close.iloc[-1] / close.iloc[-21] - 1) * 100)
                                    if len(close) > 21 else None),
            }
    except Exception:
        technical = {}

    eg = fin.get("earnings_growth")
    earnings_trend = None
    try:
        if eg is not None:
            earnings_trend = (f"{'growing' if float(eg) >= 0 else 'contracting'} "
                              f"~{abs(float(eg)) * 100:.0f}% YoY")
    except Exception:
        earnings_trend = None

    fundamentals = {
        "revenue_growth": _pct(fin.get("revenue_growth")),
        "profit_margin":  _pct(fin.get("profit_margins")),
        "earnings_trend": earnings_trend,
    }

    news_headlines = [
        h.get("headline", "") for h in (bundle.get("headlines") or [])
        if isinstance(h, dict) and h.get("headline")
    ][:15]

    return {
        "technical":      technical,
        "fundamentals":   fundamentals,
        "news_headlines": news_headlines,
    }


def build_snapshot(
    evidence: "dict | None",
    composite: "float | None",
    erosion_score: "float | None",
    erosion_label: "str | None",
    pt_signal: "dict | None",
    analyst_latest: "dict | None",
    regime: "str | None",
) -> dict:
    """Assemble the evidence-snapshot dict persisted alongside a
    thesis_reviews row (Chunk B — capture only). Chunk C, built and
    reviewed separately, is what later reads this back and diffs it against
    a newer snapshot to answer "what changed since last review" on the 🧵
    Thesis page — nothing here compares, ranks, or interprets anything.

    Pure, no I/O, never raises. Every field is independently None-safe, so a
    thin/all-None call still returns a valid schema_v=1 dict rather than an
    error — a position with no erosion score today, no PT-cut signal, no
    saved analyst coverage, and no known regime is a normal, expected state,
    not a partial failure.

    `schema_v` lets Chunk C (or any future reader) branch on shape without
    guessing from field presence alone — bump it if this shape ever changes
    in a way a reader must know about.
    """
    return {
        "schema_v": 1,
        "evidence": evidence or {"technical": {}, "fundamentals": {}, "news_headlines": []},
        "composite": composite,
        "erosion_score": erosion_score,
        "erosion_label": erosion_label,
        "pt_signal": pt_signal,
        "analyst": analyst_latest,
        "regime": regime,
    }


def _composite_band(value: "float | None") -> "str | None":
    """Classify a composite score into the same Strong Buy / Buy / Hold /
    Sell / Strong Sell bands scoring.recommendation() uses -- read-only
    against the shared constants.py thresholds, never a private copy of the
    numbers. Returns None only when `value` is None; any other malformed
    input (e.g. a non-numeric string) is the caller's problem, not something
    this classifier should swallow, since it's only ever called from
    diff_snapshots() on values already read out of a persisted snapshot."""
    if value is None:
        return None
    v = float(value)
    if v >= COMPOSITE_STRONG_BUY:
        return "strong_buy"
    if v >= COMPOSITE_BUY:
        return "buy"
    if v >= COMPOSITE_HOLD:
        return "hold"
    if v >= COMPOSITE_SELL:
        return "sell"
    return "strong_sell"


_BAND_LABEL = {
    "strong_buy":  "Strong Buy",
    "buy":         "Buy",
    "hold":        "Hold",
    "sell":        "Sell",
    "strong_sell": "Strong Sell",
}


def _finite_float(v) -> "float | None":
    """float(v), but NaN/inf and anything unparseable both collapse to None
    -- a NaN composite/erosion score is not a valid measurement to diff
    against, and should be treated exactly like a missing one (not-
    comparable), not fed into a band/threshold comparison that would
    silently produce a fabricated 'Strong Sell' or similar reading."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # f != f is the NaN check
        return None
    return f


def _parse_date_safe(s: "str | None"):
    """Best-effort ISO-date parse (first 10 chars). Returns None on any
    malformed/missing input -- callers must treat None as "not comparable,"
    never as a sentinel date."""
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def diff_snapshots(
    prev_snapshot: "dict | None",
    curr_snapshot: dict,
    prev_status: "str | None" = None,
    curr_status: "str | None" = None,
) -> list:
    """Chunk C -- compare two thesis_reviews.evidence_snapshot dicts (Chunk B)
    and return every MATERIAL change since the prior review, as a list of
    render-ready items: {"field": str, "tag": str, "message": str}.

    Pure, no I/O, never raises -- any missing/malformed field is treated as
    "not comparable" (no delta for that field), never as a crash or a
    fabricated change. Returns ALL applicable deltas, not just the first
    match -- this is a different shape from thesis_card.detect_tension(),
    which deliberately returns only its single highest-priority hit. Do not
    assume the two functions behave the same way.

    `prev_snapshot is None` (no snapshot on the prior review -- the very
    first review ever, or a legacy pre-Chunk-B row with no capture) always
    returns [] immediately. This must NEVER be read as "everything changed."

    `prev_status`/`curr_status` are the two reviews' own `status` column
    values (INTACT/WEAKENING/BROKEN) -- read from the caller's review rows,
    NOT from inside either snapshot dict (the snapshot schema never carried
    verdict/status).
    """
    if prev_snapshot is None:
        return []

    changes: list = []
    prev = prev_snapshot
    curr = curr_snapshot or {}

    # ── 1. Composite ─────────────────────────────────────────────────────
    p_comp = prev.get("composite")
    c_comp = curr.get("composite")
    if p_comp is not None and c_comp is not None:
        p_comp_f = _finite_float(p_comp)
        c_comp_f = _finite_float(c_comp)
        if p_comp_f is not None and c_comp_f is not None:
            p_band = _composite_band(p_comp_f)
            c_band = _composite_band(c_comp_f)
            band_crossed = p_band != c_band
            moved_enough = abs(c_comp_f - p_comp_f) >= THESIS_DELTA_COMPOSITE_PTS
            if band_crossed or moved_enough:
                if band_crossed:
                    msg = (
                        f"Composite moved from {p_comp_f:.0f} to {c_comp_f:.0f} "
                        f"({_BAND_LABEL.get(p_band, p_band)} → {_BAND_LABEL.get(c_band, c_band)})."
                    )
                else:
                    msg = (
                        f"Composite moved from {p_comp_f:.0f} to {c_comp_f:.0f} "
                        f"({c_comp_f - p_comp_f:+.0f} pts)."
                    )
                changes.append({"field": "composite", "tag": TAG_DERIVED, "message": msg})

    # ── 2. Erosion ───────────────────────────────────────────────────────
    p_ero_score = prev.get("erosion_score")
    c_ero_score = curr.get("erosion_score")
    p_ero_label = prev.get("erosion_label")
    c_ero_label = curr.get("erosion_label")
    if p_ero_score is not None and c_ero_score is not None and p_ero_label is not None and c_ero_label is not None:
        p_ero_f = _finite_float(p_ero_score)
        c_ero_f = _finite_float(c_ero_score)
        if p_ero_f is not None and c_ero_f is not None:
            label_changed = p_ero_label != c_ero_label
            moved_enough = (not label_changed) and abs(c_ero_f - p_ero_f) >= THESIS_DELTA_EROSION_PTS
            if label_changed or moved_enough:
                msg = (
                    f"Erosion moved from {p_ero_f:.0f} ('{p_ero_label}') to "
                    f"{c_ero_f:.0f} ('{c_ero_label}')."
                )
                changes.append({"field": "erosion", "tag": TAG_DERIVED, "message": msg})

    # ── 3. PT-cut signal -- newly crossed only, not a sustained cut ───────
    # Explicit `is None` checks rather than `... or {}` -- a real-but-empty
    # pt_signal dict and a missing one both correctly degrade to
    # "no pct_change to read," so the OFFLINE_SENTINEL_COLLAPSE shape does
    # not apply here in practice, but the antipattern gate's static check
    # can't see that, so write it the guarded way anyway.
    c_pt = curr.get("pt_signal")
    c_pct = c_pt.get("pct_change") if c_pt is not None else None
    if c_pct is not None:
        c_pct_f = _finite_float(c_pct)
        c_pct_pts = c_pct_f * 100.0 if c_pct_f is not None else None
        if c_pct_pts is not None and c_pct_pts <= PT_TARGET_CUT_WARN_PCT:
            p_pt = prev.get("pt_signal")
            p_pct = p_pt.get("pct_change") if p_pt is not None else None
            p_pct_f = _finite_float(p_pct) if p_pct is not None else None
            p_pct_pts = p_pct_f * 100.0 if p_pct_f is not None else None
            was_already_cut = p_pct_pts is not None and p_pct_pts <= PT_TARGET_CUT_WARN_PCT
            if not was_already_cut:
                changes.append({
                    "field": "pt_signal",
                    "tag": TAG_ANALYST,
                    "message": (
                        "A new analyst price-target cut crossed the warning "
                        f"threshold ({c_pct_pts:.1f}%)."
                    ),
                })

    # ── 4. New analyst coverage ────────────────────────────────────────
    c_an = curr.get("analyst")
    if c_an is not None:
        c_an_date = _parse_date_safe(c_an.get("latest_article_date"))
        if c_an_date is not None:
            p_an = prev.get("analyst")
            p_an_date = _parse_date_safe(p_an.get("latest_article_date")) if p_an else None
            if p_an is None or (p_an_date is not None and c_an_date > p_an_date):
                changes.append({
                    "field": "analyst",
                    "tag": TAG_ANALYST,
                    "message": f"New analyst coverage saved ({c_an_date.isoformat()}).",
                })

    # ── 5. Regime ────────────────────────────────────────────────────────
    p_regime = prev.get("regime")
    c_regime = curr.get("regime")
    if p_regime is not None and c_regime is not None and p_regime != c_regime:
        changes.append({
            "field": "regime",
            "tag": TAG_DERIVED,
            "message": f"Market tone shifted from {p_regime} to {c_regime}.",
        })

    # ── 6. F-1 verdict change (reads the two review rows' own status column,
    #      never anything inside the snapshot dicts) ──────────────────────
    if prev_status is not None and curr_status is not None and prev_status != curr_status:
        changes.append({
            "field": "status",
            "tag": TAG_AI,
            "message": (
                f"Thesis verdict changed from {prev_status} "
                f"to {curr_status}."
            ),
        })

    return changes


# ── Phase 2 — Earnings Thesis Checkpoint ─────────────────────────────────────

_EARNINGS_CHECKPOINT_PROMPT = """You are a disciplined portfolio analyst helping an investor check whether their investment thesis still holds after a quarterly earnings report.

Your job: given the investor's original thesis and the actual earnings results, assess whether the thesis is INTACT, WEAKENING, or BROKEN. Write a concise 2-3 sentence explanation.

Rules:
- Only use facts from the earnings results provided. Do not invent events or outcomes.
- INTACT: the earnings result is broadly consistent with the thesis and does not raise new doubts.
- WEAKENING: the result introduces some doubt but does not decisively contradict the core thesis premise.
- BROKEN: the result materially contradicts the key condition the investor stated, or the core premise has clearly reversed.
- Be conservative: default to WEAKENING when uncertain. BROKEN requires a clear, specific contradiction.
- Do not recommend buying, selling, or any portfolio action. Observation only.
- End your response with exactly one verdict line on its own:
    Verdict: INTACT
    Verdict: WEAKENING
    Verdict: BROKEN"""


def generate_earnings_thesis_update(
    ticker: str,
    user_thesis: str,
    earnings_result: dict,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 300,
) -> dict | None:
    """
    Suggest a thesis status update based on actual earnings results.

    earnings_result: a row dict from earnings_results table.

    Returns:
        suggested_status  — 'INTACT' | 'WEAKENING' | 'BROKEN'
        rationale         — 2-3 sentence explanation
        earnings_signal   — 'beat' | 'miss' | 'mixed' | 'unknown'

    Returns None on any failure — caller degrades gracefully (no checkpoint
    CTA shown). Does NOT write to thesis_reviews; that is the user's action.
    """
    if not api_key or not user_thesis or not earnings_result:
        return None
    try:
        import anthropic
        r = earnings_result
        # Derive a simple earnings_signal label from the extracted facts
        eps_beat = r.get("eps_beat")
        rev_beat = r.get("rev_beat")
        guidance = r.get("guidance_direction") or "unknown"
        if eps_beat is True and rev_beat is True and guidance in ("raised", "maintained"):
            earnings_signal = "beat"
        elif eps_beat is False and rev_beat is False:
            earnings_signal = "miss"
        elif eps_beat is None and rev_beat is None:
            earnings_signal = "unknown"
        else:
            earnings_signal = "mixed"

        # Build the user prompt
        result_parts = [f"Ticker: {ticker}"]
        result_parts.append(f"Original thesis: {user_thesis.strip()}")
        result_parts.append("")
        result_parts.append("Earnings results:")
        if r.get("actual_eps") is not None and r.get("estimated_eps") is not None:
            beat_miss = "beat" if r.get("eps_beat") else "missed"
            surprise  = (
                f" (surprise: {r['eps_surprise_pct']:+.1f}%)" if r.get("eps_surprise_pct") is not None else ""
            )
            result_parts.append(
                f"- EPS: actual ${r['actual_eps']:.2f} vs estimate ${r['estimated_eps']:.2f}"
                f" — {beat_miss}{surprise}"
            )
        if r.get("actual_revenue") is not None and r.get("estimated_revenue") is not None:
            rev_beat_miss = "beat" if r.get("rev_beat") else "missed"
            result_parts.append(
                f"- Revenue: actual ${r['actual_revenue']:.2f}B vs estimate ${r['estimated_revenue']:.2f}B"
                f" — {rev_beat_miss}"
            )
        if guidance != "unknown":
            result_parts.append(f"- Guidance: {guidance}")
        if r.get("key_narrative"):
            result_parts.append(f"- Management commentary: {r['key_narrative']}")

        user_msg = "\n".join(result_parts)

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_EARNINGS_CHECKPOINT_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text = response.content[0].text if response.content else ""
        parsed = _parse_response(text)
        return {
            "suggested_status": parsed["status"],
            "rationale":        parsed["summary"],
            "earnings_signal":  earnings_signal,
        }
    except Exception:
        return None
