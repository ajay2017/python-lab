"""
Multi-Agent Debate — Phase 1 (entry debate from Grow Today).

Runs a structured 4-round Bull vs Bear debate on an entry candidate, then
issues a Judge verdict. All LLM calls use claude-haiku-4-5-20251001 for cost
efficiency (~5 Haiku calls per debate run).

Design principles (mirrors premortem_advisor.py):
- No Streamlit imports — pure logic only.
- api_key passed in; never read from st.secrets.
- Every LLM-calling body wrapped in bare except Exception so a rate-limit or
  outage degrades gracefully (partial result returned, never raises).
- Scoped to Phase 1: entry debate only. Exit debate corpus builder is a stub.
"""

import json

from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC

# ── Module-level ceilings (UI / quality knobs, not investment-policy thresholds) ─
DEBATE_WIN_MARGIN    = 20   # bull_case_score - bear_case_score threshold for declared winner
DEBATE_SESSION_CEILING = 3  # max new debates per session (UI ceiling, not a policy threshold)

# ── System prompts ────────────────────────────────────────────────────────────

_BULL_SYSTEM = """You are a disciplined long-only equity analyst in a structured 4-round Bull vs Bear debate. Present the strongest SPECIFIC case FOR the investment position using ONLY the evidence supplied. Rules:
- Cite specific numbers or facts from the evidence (scores, percentages, sector, momentum)
- 2–3 sentences maximum. Be direct and confident — do not hedge.
- Do NOT fabricate data not in the evidence."""

_BEAR_SYSTEM = """You are a disciplined short-seller in a structured 4-round Bull vs Bear debate. Present the strongest SPECIFIC case AGAINST the investment position using ONLY the evidence supplied. Rules:
- Cite specific numbers or facts from the evidence (scores, percentages, sector, momentum)
- 2–3 sentences maximum. Be direct — do not hedge.
- Do NOT fabricate data not in the evidence. Address Bull's specific claim if one is given."""

_JUDGE_SYSTEM = f"""You are an impartial debate judge. Evaluate the 4-round Bull vs Bear debate below.
Verdict rules: "bull_wins" when bull_case_score minus bear_case_score >= {DEBATE_WIN_MARGIN}; "bear_wins" when the reverse; otherwise "contested".
grounded = true if BOTH agents cited specific evidence (numbers, scores, named facts); false if either was generic.
Output ONLY valid JSON, no other text:
{{"verdict": "bull_wins"|"bear_wins"|"contested", "key_dispute": "one sentence or null", "bull_case_score": 0-100, "bear_case_score": 0-100, "grounded": true|false}}"""

_VALID_VERDICTS = {"bull_wins", "bear_wins", "contested"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _call_haiku(client, model, system, user_text, max_tokens=200):
    """Single Haiku call. Returns text or None on any failure. Never raises."""
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user_text}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        return response.content[0].text.strip() if response.content else None
    except Exception:
        return None


def _format_corpus(corpus: dict, debate_type: str) -> str:
    """Convert corpus dict to a clean multi-line prompt block. Never raises."""
    try:
        _ctx = "New-entry analysis" if debate_type == "entry" else "Hold-vs-exit decision"
        lines = [f"Debate context: {_ctx}"]
        if corpus.get("ticker"):
            lines.append(f"Ticker: {corpus['ticker']}")
        if corpus.get("current_price") is not None:
            lines.append(f"Current price: ${corpus['current_price']:.2f}")
        comp = corpus.get("composite_score")
        lbl  = corpus.get("composite_label", "")
        if comp is not None:
            _comp_line = f"Composite score: {round(comp, 1)}/100"
            if lbl:
                _comp_line += f" ({lbl})"
            lines.append(_comp_line)
        _pillar_parts = []
        for key, name in (("t_score", "Technical"), ("bq_score", "Fundamentals"),
                          ("val_score", "Valuation"), ("s_score", "Sentiment")):
            v = corpus.get(key)
            if v is not None:
                _pillar_parts.append(f"{name}: {round(v, 1)}")
        if _pillar_parts:
            lines.append("Pillar scores — " + ", ".join(_pillar_parts))
        if corpus.get("sector"):
            lines.append(f"Sector: {corpus['sector']}")
        m5 = corpus.get("momentum_5d_pct")
        m20 = corpus.get("momentum_20d_pct")
        _mom_parts = []
        if m5 is not None:
            _mom_parts.append(f"Momentum (5d): {m5:+.1f}%")
        if m20 is not None:
            _mom_parts.append(f"Momentum (20d): {m20:+.1f}%")
        if _mom_parts:
            lines.append(" | ".join(_mom_parts))
        rs = corpus.get("rs_vs_spy_20d_pp")
        if rs is not None:
            lines.append(f"Relative strength vs SPY (20d): {rs:+.1f} pp")
        conv = corpus.get("conviction")
        if conv:
            lines.append(f"Conviction tier: {conv}")
        return "\n".join(lines)
    except Exception:
        return ""


def _parse_judge(text: str) -> dict | None:
    """Parse judge JSON. Returns validated dict or None on any parse/validation error."""
    if not text:
        return None
    cleaned = text.strip()
    # Strip markdown fences (same pattern as premortem_advisor._parse_case_against)
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
        cleaned = cleaned.strip()
    # Try direct parse first
    try:
        parsed = json.loads(cleaned)
    except Exception:
        # Fall back: slice from first { to last }
        start = cleaned.find("{")
        end   = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start: end + 1])
        except Exception:
            return None
    if not isinstance(parsed, dict):
        return None
    verdict = parsed.get("verdict")
    if verdict not in _VALID_VERDICTS:
        return None
    return {
        "verdict":         verdict,
        "key_dispute":     parsed.get("key_dispute"),
        "bull_case_score": parsed.get("bull_case_score"),
        "bear_case_score": parsed.get("bear_case_score"),
        "grounded":        parsed.get("grounded"),
    }


# ── Public corpus builders ────────────────────────────────────────────────────

def build_entry_corpus(ticker, grow_candidate_row, grow_bundle, spy_close_series) -> dict:
    """
    Assemble the evidence package for an entry debate.

    Parameters:
        ticker            — str, the candidate ticker
        grow_candidate_row — the _gp dict from the Grow Today loop
                             (has composite_score, conviction, sector, etc.)
        grow_bundle       — _grow_composites[ticker] raw load_all() bundle
                            (keys: t_score, bq_score/f_score, val_score, s_score,
                             history DataFrame, info dict, headlines)
        spy_close_series  — pd.Series of SPY Close prices

    Returns a flat dict. Each field individually try/except-ed — missing fields
    excluded silently. Never raises overall.
    """
    corpus: dict = {"ticker": str(ticker).upper().strip(), "debate_type": "entry"}

    try:
        corpus["current_price"] = round(
            float(grow_bundle["history"]["Close"].iloc[-1]), 2
        )
    except Exception:
        pass

    try:
        corpus["composite_score"] = grow_candidate_row.get("composite_score")
    except Exception:
        pass

    try:
        corpus["composite_label"] = grow_candidate_row.get("composite_label", "")
    except Exception:
        pass

    try:
        corpus["t_score"] = grow_bundle.get("t_score")
    except Exception:
        pass

    try:
        _bq = grow_bundle.get("bq_score")
        if _bq is None:
            _bq = grow_bundle.get("f_score")
        corpus["bq_score"] = _bq
    except Exception:
        pass

    try:
        corpus["val_score"] = grow_bundle.get("val_score")
    except Exception:
        pass

    try:
        corpus["s_score"] = grow_bundle.get("s_score")
    except Exception:
        pass

    try:
        _sec = grow_candidate_row.get("sector")
        if not _sec:
            _sec = (grow_bundle.get("info") or {}).get("sector", "")
        corpus["sector"] = _sec
    except Exception:
        pass

    try:
        corpus["momentum_5d_pct"] = round(
            float(grow_bundle["history"]["Close"].pct_change(5).iloc[-1] * 100), 1
        )
    except Exception:
        pass

    try:
        corpus["momentum_20d_pct"] = round(
            float(grow_bundle["history"]["Close"].pct_change(20).iloc[-1] * 100), 1
        )
    except Exception:
        pass

    try:
        from stock_analyzer.exit_advisor import compute_relative_strength
        corpus["rs_vs_spy_20d_pp"] = round(
            float(compute_relative_strength(
                grow_bundle["history"]["Close"], spy_close_series
            )), 1
        )
    except Exception:
        pass

    try:
        corpus["conviction"] = grow_candidate_row.get("conviction", "")
    except Exception:
        pass

    return corpus


def build_exit_corpus(ticker, _port_df_row, _held_data_bundle, _erosion_cache_row, _trade_row) -> dict:
    # Phase 2 — exit corpus builder (stub; parameters populated in Phase 2)
    return {"ticker": str(ticker).upper().strip(), "debate_type": "exit"}


# ── Main debate runner ────────────────────────────────────────────────────────

def run_debate(corpus: dict, debate_type: str, api_key: str,
               model: str = "claude-haiku-4-5-20251001") -> dict:
    """
    Run a 4-round Bull vs Bear debate + Judge verdict (5 Haiku calls total).

    Returns a result dict with keys:
        transcript      — list of {round, agent, text} (up to 4 items)
        verdict         — "bull_wins" | "bear_wins" | "contested" | None
        key_dispute     — str | None
        bull_case_score — int | None
        bear_case_score — int | None
        grounded        — bool | None
        partial         — True if any debate round failed; False on full completion
        error           — str | None
    """
    if not api_key:
        return {
            "transcript": [], "verdict": None, "partial": True,
            "error": "no_api_key",
        }

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except Exception:
        return {
            "transcript": [], "verdict": None, "partial": True,
            "error": "anthropic_import_failed",
        }

    ticker      = corpus.get("ticker", "?")
    corpus_text = _format_corpus(corpus, debate_type)
    transcript  = []

    # Round 1 — Bull opens
    r1 = _call_haiku(
        client, model, _BULL_SYSTEM,
        f"Evidence:\n{corpus_text}\n\nPresent the strongest specific case FOR entering {ticker}.",
    )
    if not r1:
        return {"transcript": transcript, "verdict": None, "partial": True, "error": "round1_failed"}
    transcript.append({"round": 1, "agent": "bull", "text": r1})

    # Round 2 — Bear responds
    r2 = _call_haiku(
        client, model, _BEAR_SYSTEM,
        f"Evidence:\n{corpus_text}\n\n---\nBull's case:\n{r1}\n\n---\n"
        f"Counter Bull's argument with the strongest specific evidence AGAINST this position.",
    )
    if not r2:
        return {"transcript": transcript, "verdict": None, "partial": True, "error": "round2_failed"}
    transcript.append({"round": 2, "agent": "bear", "text": r2})

    # Round 3 — Bull rebuts
    r3 = _call_haiku(
        client, model, _BULL_SYSTEM,
        f"Evidence:\n{corpus_text}\n\n---\nBull Round 1:\n{r1}\n\nBear's counter:\n{r2}\n\n---\n"
        f"Rebut Bear's specific objection with evidence.",
    )
    if not r3:
        return {"transcript": transcript, "verdict": None, "partial": True, "error": "round3_failed"}
    transcript.append({"round": 3, "agent": "bull", "text": r3})

    # Round 4 — Bear closes
    r4 = _call_haiku(
        client, model, _BEAR_SYSTEM,
        f"Evidence:\n{corpus_text}\n\n---\nBull:\n{r1}\n\nBear:\n{r2}\n\nBull rebuttal:\n{r3}\n\n---\n"
        f"State the ONE remaining concern that Bull's rebuttal did not adequately address.",
    )
    if not r4:
        return {"transcript": transcript, "verdict": None, "partial": True, "error": "round4_failed"}
    transcript.append({"round": 4, "agent": "bear", "text": r4})

    # Round 5 — Judge verdict
    full_transcript_text = "\n\n".join(
        f"Round {t['round']} ({t['agent'].title()}): {t['text']}"
        for t in transcript
    )
    judge_raw = _call_haiku(
        client, model, _JUDGE_SYSTEM,
        f"Debate transcript:\n{full_transcript_text}\n\nIssue your verdict.",
        max_tokens=300,
    )
    judge = _parse_judge(judge_raw) if judge_raw else None

    if judge is None:
        # Judge call failed or unparseable — return transcript with contested default
        return {
            "transcript":      transcript,
            "verdict":         "contested",
            "key_dispute":     None,
            "bull_case_score": None,
            "bear_case_score": None,
            "grounded":        None,
            "partial":         False,
            "error":           None,
        }

    return {
        "transcript":      transcript,
        "verdict":         judge["verdict"],
        "key_dispute":     judge.get("key_dispute"),
        "bull_case_score": judge.get("bull_case_score"),
        "bear_case_score": judge.get("bear_case_score"),
        "grounded":        judge.get("grounded"),
        "partial":         False,
        "error":           None,
    }
