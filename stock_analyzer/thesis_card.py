"""🧵 Thesis page — Chunk A: pure assembly logic for the read-only
consolidation card.

This module computes NOTHING new. It arranges and labels data that five
already-shipped features already compute/store elsewhere (Thesis Authoring/
Review — F-1/F-5, Thesis Red Team, Multi-Agent Debate, Analyst Coverage,
Pre-Mortem). `app.py` fetches the raw rows from `db.py`/`session_state` and
hands them to `assemble_thesis_card()` here as plain dicts — this module has
no Streamlit imports, no DB imports, no LLM calls, and never raises on
missing/None input (every section degrades to an explicit disclosed
"unavailable" shape instead).

Mirrors the pure-module convention already established by
`thesis_red_team.py` / `outage_gate.py`: no I/O, no gate, no threshold — the
one constant this module reads (`COMPOSITE_BUY`) is imported read-only from
`constants.py`, never modified or duplicated.

See docs/mockups/2026-09-29-thesis-card-mockup.html for the approved layout
this data structure feeds.
"""
from __future__ import annotations

from stock_analyzer.constants import COMPOSITE_BUY

# ── Provenance tags ──────────────────────────────────────────────────────────
# Five tags applied to every evidence line on the card so the reader always
# knows what KIND of claim they're looking at — a raw fact, something the app
# computed, saved external opinion, an LLM's own inference/narration, or the
# owner's own words. Module-level (not constants.py) — these are display
# labels, not a policy/threshold value, same precedent as
# thesis_red_team.EROSION_LABELS.
TAG_FACT     = "fact"            # 📊 raw provider data
TAG_DERIVED  = "derived"         # 🧮 a Python-computed value
TAG_ANALYST  = "analyst_opinion" # 🏦 saved external research
TAG_AI       = "ai_inference"    # 🤖 LLM narration/judgment
TAG_USER     = "user_authored"   # 🖊 the owner's own words

TAG_EMOJI = {
    TAG_FACT:    "📊",
    TAG_DERIVED: "🧮",
    TAG_ANALYST: "🏦",
    TAG_AI:      "🤖",
    TAG_USER:    "🖊",
}

TAG_LABEL = {
    TAG_FACT:    "fact",
    TAG_DERIVED: "derived",
    TAG_ANALYST: "analyst opinion",
    TAG_AI:      "AI inference",
    TAG_USER:    "your words",
}

# Field -> tag map, one sub-dict per card section. Anything not listed here
# (a typo'd field name, a field added later and not wired in) falls through
# to TAG_AI, the CONSERVATIVE default per the design spec — never silently
# default to TAG_FACT, which would let an unrecognized/unvetted field masquerade
# as raw provider data.
_FIELD_TAGS: dict[str, dict[str, str]] = {
    "header": {
        "composite":      TAG_DERIVED,
        "shares":         TAG_FACT,
        "unrealized_pct": TAG_FACT,
    },
    "thesis": {
        "text": TAG_USER,
    },
    "f1_review": {
        "status":  TAG_AI,
        "summary": TAG_AI,
    },
    "red_team": {
        "erosion_score":    TAG_DERIVED,
        "erosion_label":    TAG_DERIVED,
        "components":       TAG_DERIVED,
        "counter_evidence": TAG_AI,
    },
    "debate": {
        "verdict":         TAG_AI,
        "key_dispute":     TAG_AI,
        "bull_case_score": TAG_AI,
        "bear_case_score": TAG_AI,
    },
    "analyst_coverage": {
        "consensus_rating":      TAG_ANALYST,
        "avg_pt":                TAG_ANALYST,
        "analysts":              TAG_ANALYST,
        "thesis":                TAG_ANALYST,
        "catalysts":             TAG_ANALYST,
        "risks":                 TAG_ANALYST,
        "price_at_article_date": TAG_FACT,
    },
    "pre_mortem": {
        "case_against":      TAG_AI,
        "commitment":        TAG_USER,
        "trigger_price":     TAG_USER,
        "trigger_direction": TAG_USER,
    },
}


def tag_for(section: str, field: str) -> str:
    """Provenance tag for a known (section, field) pair. Defaults to
    TAG_AI (the conservative default) for any unrecognized section or field —
    never TAG_FACT."""
    return _FIELD_TAGS.get(section, {}).get(field, TAG_AI)


# ── Disclosed "not available" reasons ────────────────────────────────────────
# Exact wording lives here (not scattered across app.py render calls) so the
# tests can assert against it and a future re-word touches one place.
REASON_NO_PORTFOLIO = (
    "Portfolio not loaded — open 🏠 Home to see your position and the "
    "engine's live composite"
)
REASON_NOT_HELD = (
    "Not currently held — this ticker has no live position or composite "
    "to show here (only held tickers get a position header)."
)
REASON_NO_THESIS = "No thesis saved for this ticker's most recent buy."
REASON_NO_REVIEW = "No thesis review on record for this ticker."
REASON_REVIEW_OFFLINE = "Thesis review data unavailable — data offline"
REASON_EROSION_NOT_SCORED = (
    "Erosion not computed today — open the Red Team tab (🧠 AI Insights) "
    "to score it."
)
REASON_NO_DEBATE = "No debate on record for this ticker."
REASON_ANALYST_OFFLINE = "Analyst coverage unavailable — data offline"
REASON_ANALYST_NONE = "No saved analyst coverage"
REASON_NO_PREMORTEM = (
    "No pre-mortem logged at entry (pre-mortem applies to live Buys only)."
)

# The F-1 section renders the review's verdict + summary only. An itemized
# evidence breakdown was never built (snapshots are captured since Chunk B,
# but only the diff reads them) and nothing is queued for it — so this note
# states what the summary IS rather than promising a future breakdown.
EVIDENCE_NOTE_F1 = (
    "The summary is the AI's own reading of the evidence at review time — "
    "the underlying figures aren't itemized here."
)

# Shown when the diff can't run: fewer than 2 reviews for this ticker, or
# either of the two newest lacks a snapshot (pre-Chunk-B rows, or the
# earnings-checkpoint path, which never captures one). Watchlist-only tickers
# never get F-1 reviews, so for them this is permanent — the parenthetical
# says why rather than implying it will fill in on its own.
CHANGE_TRACKING_NOTE = (
    "Not enough history yet — a change report needs two reviews of this "
    "ticker with captured evidence (the weekly Sunday review adds one for "
    "held positions that have a thesis)."
)


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


# ── Section assembly ─────────────────────────────────────────────────────────

def _assemble_header(ticker: str, position_row: "dict | None",
                      portfolio_loaded: bool) -> dict:
    if not position_row:
        reason = REASON_NO_PORTFOLIO if not portfolio_loaded else REASON_NOT_HELD
        return {"available": False, "reason": reason, "ticker": ticker}
    return {
        "available":       True,
        "ticker":          ticker,
        "composite":       position_row.get("composite"),
        "composite_tag":   tag_for("header", "composite"),
        "shares":          position_row.get("shares"),
        "shares_tag":      tag_for("header", "shares"),
        "unrealized_pct":  position_row.get("unrealized_pct"),
        "unrealized_tag":  tag_for("header", "unrealized_pct"),
    }


def _assemble_thesis(trade_row: "dict | None") -> dict:
    if not trade_row:
        return _unavailable(REASON_NO_THESIS)
    text = trade_row.get("user_thesis")
    if not text or not str(text).strip():
        return _unavailable(REASON_NO_THESIS)
    return {
        "available":     True,
        "text":          str(text).strip(),
        "tag":           tag_for("thesis", "text"),
        "thesis_source": trade_row.get("thesis_source"),
        "trade_date":    trade_row.get("trade_date"),
    }


def _assemble_f1_review(thesis_review_row: "dict | None") -> dict:
    # Three-state contract, deliberately NOT collapsed (CLAUDE.md offline-
    # sentinel rule), mirroring _assemble_analyst_coverage exactly: None =
    # load failed/offline, {} = checked, zero rows (no review yet for this
    # ticker), populated dict = a real saved thesis_reviews row.
    if thesis_review_row is None:
        return _unavailable(REASON_REVIEW_OFFLINE)
    if not thesis_review_row:
        return _unavailable(REASON_NO_REVIEW)
    return {
        "available":    True,
        "status":       thesis_review_row.get("status"),
        "status_tag":   tag_for("f1_review", "status"),
        "summary":      thesis_review_row.get("summary"),
        "summary_tag":  tag_for("f1_review", "summary"),
        "reviewed_at":  thesis_review_row.get("reviewed_at"),
        "note":         EVIDENCE_NOTE_F1,
    }


def _assemble_red_team(erosion: "dict | None") -> dict:
    if not erosion:
        return _unavailable(REASON_EROSION_NOT_SCORED)
    snapshot = erosion.get("signals_snapshot")
    components = None
    if snapshot is not None:
        components = {
            "tier":       snapshot.get("tier"),
            "rs_vs_spy":  snapshot.get("rs_vs_spy"),
            "comp_delta": snapshot.get("comp_delta"),
            "pt_pts":     snapshot.get("pt_pts"),
        }
    return {
        "available":            True,
        "erosion_score":        erosion.get("erosion_score"),
        "erosion_label":        erosion.get("erosion_label"),
        "score_tag":            tag_for("red_team", "erosion_score"),
        "components":           components,
        "components_tag":       tag_for("red_team", "components"),
        # counter_evidence: None = not attempted/failed (never render a bear
        # case), [] = attempted, genuinely no grounded counter-evidence
        # (still a valid, disclosable result) — preserved as-is, never
        # coerced, per thesis_red_team.py's own documented contract.
        "counter_evidence":     erosion.get("counter_evidence"),
        "counter_evidence_tag": tag_for("red_team", "counter_evidence"),
    }


def _assemble_debate(debate_row: "dict | None") -> dict:
    if not debate_row:
        return _unavailable(REASON_NO_DEBATE)
    return {
        "available":        True,
        "verdict":          debate_row.get("verdict"),
        "verdict_tag":      tag_for("debate", "verdict"),
        "debate_type":      debate_row.get("debate_type"),
        "debate_date":      debate_row.get("debate_date"),
        "key_dispute":      debate_row.get("key_dispute"),
        "key_dispute_tag":  tag_for("debate", "key_dispute"),
        "bull_case_score":  debate_row.get("bull_case_score"),
        "bear_case_score":  debate_row.get("bear_case_score"),
    }


def _count_analysts(raw) -> int:
    """`analyst_coverage.analysts` is a jsonb list of one dict per firm — count
    the firms, never render the raw list. Defensive against a JSON-string
    payload (some Supabase client paths return jsonb as a string rather than
    an already-deserialized list), mirroring the `_pj`/`_pj_cron` parse
    helpers already used for this exact same column elsewhere in this
    codebase (app.py's on-demand thesis review site, cron_runner.py's
    `_run_thesis`)."""
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            return 0
    return len(raw) if isinstance(raw, list) else 0


def _assemble_analyst_coverage(analyst_row: "dict | None") -> dict:
    # Three-state contract, deliberately NOT collapsed (CLAUDE.md offline-
    # sentinel rule): None = load failed/offline, {} = checked, zero rows,
    # populated dict = a real saved row.
    if analyst_row is None:
        return _unavailable(REASON_ANALYST_OFFLINE)
    if not analyst_row:
        return _unavailable(REASON_ANALYST_NONE)
    return {
        "available":              True,
        "consensus_rating":       analyst_row.get("consensus_rating"),
        "consensus_tag":          tag_for("analyst_coverage", "consensus_rating"),
        "avg_pt":                 analyst_row.get("avg_pt"),
        "n_firms":                _count_analysts(analyst_row.get("analysts")),
        "thesis":                 analyst_row.get("thesis"),
        "article_date":           analyst_row.get("article_date"),
        "price_at_article_date":  analyst_row.get("price_at_article_date"),
        "price_tag":              tag_for("analyst_coverage", "price_at_article_date"),
    }


def _assemble_pre_mortem(trade_row: "dict | None") -> dict:
    if not trade_row:
        return _unavailable(REASON_NO_PREMORTEM)
    case_against = trade_row.get("premortem_case_against")
    commitment = trade_row.get("premortem_commitment")
    has_case = isinstance(case_against, list) and len(case_against) > 0
    has_commitment = bool(commitment and str(commitment).strip())
    if not has_case and not has_commitment:
        return _unavailable(REASON_NO_PREMORTEM)
    return {
        "available":          True,
        "case_against":       case_against if has_case else None,
        "case_against_tag":   tag_for("pre_mortem", "case_against"),
        "commitment":         str(commitment).strip() if has_commitment else None,
        "commitment_tag":     tag_for("pre_mortem", "commitment"),
        "trigger_price":      trade_row.get("premortem_trigger_price"),
        "trigger_direction":  trade_row.get("premortem_trigger_direction"),
        "trigger_tag":        tag_for("pre_mortem", "trigger_price"),
        "trade_date":         trade_row.get("trade_date"),
    }


def assemble_thesis_card(
    ticker: str,
    position_row: "dict | None" = None,
    trade_row: "dict | None" = None,
    thesis_review_row: "dict | None" = None,
    erosion: "dict | None" = None,
    debate_row: "dict | None" = None,
    analyst_row: "dict | None" = None,
    portfolio_loaded: bool = True,
) -> dict:
    """Pure function of already-fetched data -> a render-ready structure for
    the 🧵 Thesis card. Never raises, never recomputes a score/verdict.

    Every arg is a plain dict (or None) the caller has already fetched from
    db.py / session_state — this function does no I/O.

    `position_row`: {"composite", "shares", "unrealized_pct"} for a HELD
        ticker's row in port_df, or None if the ticker isn't held (or the
        portfolio hasn't loaded this session — disambiguated by
        `portfolio_loaded`).
    `trade_row`: the most recent BUY trade row for this ticker (thesis +
        pre-mortem fields live on the same row) or None.
    `thesis_review_row`: None (load failed/offline), {} (checked, zero rows
        for this ticker), or the most recent thesis_reviews row (a populated
        dict) — three distinct states, never collapsed (same contract as
        `analyst_row` below).
    `erosion`: today's thesis_erosion_cache row for this ticker, or None if
        not scored today (a normal, expected state — not an error).
    `debate_row`: the most recent debate_cache row (across both debate
        types) for this ticker, or None.
    `analyst_row`: None (load failed/offline), {} (checked, zero rows), or a
        populated dict (a real saved analyst_coverage row) — three distinct
        states, never collapsed.
    `portfolio_loaded`: whether _port_df_enriched has been built this
        session at all (True even if this specific ticker isn't in it) —
        lets the header distinguish "cold session" from "not held".

    Returns a dict with 7 keys — "header" plus the 6 body sections — each
    either `{"available": True, ...tagged content...}` or
    `{"available": False, "reason": "..."}`.
    """
    return {
        "ticker":           ticker,
        "header":           _assemble_header(ticker, position_row, portfolio_loaded),
        "thesis":           _assemble_thesis(trade_row),
        "f1_review":        _assemble_f1_review(thesis_review_row),
        "red_team":         _assemble_red_team(erosion),
        "debate":           _assemble_debate(debate_row),
        "analyst_coverage": _assemble_analyst_coverage(analyst_row),
        "pre_mortem":       _assemble_pre_mortem(trade_row),
    }


# ── Tension detection ────────────────────────────────────────────────────────

def analyst_side_detailed(consensus_rating: "str | None") -> "str | None":
    """Finer-grained than `analyst_intel.consensus_side()` — distinguishes a
    "strong buy" rating from a plain "buy" rating.

    `analyst_intel.consensus_side()` deliberately collapses both onto the
    same `"buy"` bucket for ITS consumer (the calibration matrix, which only
    needs a Buy/Sell/neutral axis — see its own docstring). `detect_tension`'s
    condition 2 specifically needs the stronger "strong buy" read, so that
    distinction lives here rather than being added to analyst_intel.py
    (out of scope for this chunk — analyst_intel.py is unmodified).

    Returns "strong_buy" / "buy" / "sell" / "neutral", or None when there is
    no rating to classify (mirrors consensus_side()'s own None contract).
    """
    label = (consensus_rating or "").strip().lower()
    if not label:
        return None
    if label.startswith("strong buy"):
        return "strong_buy"
    if label.startswith("buy"):
        return "buy"
    if label.startswith("sell"):
        return "sell"
    return "neutral"


def detect_tension(
    composite: "float | None",
    analyst_consensus_side: "str | None",
    f1_verdict: "str | None",
    debate_verdict: "str | None",
) -> "str | None":
    """Pure, read-only disagreement check across three independent readings.
    Returns a plain descriptive sentence (never a new score/verdict) or None.
    Never raises on None/missing input.

    `analyst_consensus_side` must distinguish "strong_buy" from "buy" — pass
    `analyst_side_detailed()`'s output, NOT `analyst_intel.consensus_side()`,
    which deliberately collapses the two (see that function's docstring).

    Checked in this priority order, first match wins:
      1. Analyst side is bullish ("buy"/"strong_buy") while composite is
         below COMPOSITE_BUY.
      2. F-1 verdict is WEAKENING/BROKEN while analyst side is "strong_buy".
      3. Debate verdict is "bear_wins" while composite is at/above
         COMPOSITE_BUY.
    """
    # ── condition 1 ──
    if analyst_consensus_side in ("buy", "strong_buy") and composite is not None:
        try:
            comp = float(composite)
        except (TypeError, ValueError):
            comp = None
        if comp is not None and comp < COMPOSITE_BUY:
            side_label = "Strong Buy" if analyst_consensus_side == "strong_buy" else "Buy"
            return (
                f"Wall St. consensus leans {side_label} while the engine "
                f"composite sits at {comp:.0f} — below the Buy band. "
                "Neither reading overrides the other."
            )

    # ── condition 2 ──
    if f1_verdict in ("WEAKENING", "BROKEN") and analyst_consensus_side == "strong_buy":
        return (
            f"Thesis Review reads {str(f1_verdict).title()} for this position "
            "while Wall St. consensus remains Strong Buy — a live "
            "disagreement between the two readings, disclosed here rather "
            "than resolved."
        )

    # ── condition 3 ──
    if debate_verdict == "bear_wins" and composite is not None:
        try:
            comp = float(composite)
        except (TypeError, ValueError):
            comp = None
        if comp is not None and comp >= COMPOSITE_BUY:
            return (
                "The debate agents' verdict favors the bear case while the "
                f"engine composite sits at {comp:.0f} (Buy band or higher). "
                "Neither reading overrides the other."
            )

    return None
