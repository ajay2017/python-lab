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
import math

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
        if corpus.get("current_price") is not None and math.isfinite(corpus["current_price"]):
            lines.append(f"Current price: ${corpus['current_price']:.2f}")
        comp = corpus.get("composite_score")
        lbl  = corpus.get("composite_label", "")
        if comp is not None and math.isfinite(comp):
            _comp_line = f"Composite score: {round(comp, 1)}/100"
            if lbl:
                _comp_line += f" ({lbl})"
            lines.append(_comp_line)
        _pillar_parts = []
        for key, name in (("t_score", "Technical"), ("bq_score", "Fundamentals"),
                          ("val_score", "Valuation"), ("s_score", "Sentiment")):
            v = corpus.get(key)
            if v is not None and math.isfinite(v):
                _pillar_parts.append(f"{name}: {round(v, 1)}")
        if _pillar_parts:
            lines.append("Pillar scores — " + ", ".join(_pillar_parts))
        if corpus.get("sector"):
            lines.append(f"Sector: {corpus['sector']}")
        m5 = corpus.get("momentum_5d_pct")
        m20 = corpus.get("momentum_20d_pct")
        _mom_parts = []
        if m5 is not None and math.isfinite(m5):
            _mom_parts.append(f"Momentum (5d): {m5:+.1f}%")
        if m20 is not None and math.isfinite(m20):
            _mom_parts.append(f"Momentum (20d): {m20:+.1f}%")
        if _mom_parts:
            lines.append(" | ".join(_mom_parts))
        rs = corpus.get("rs_vs_spy_20d_pp")
        if rs is not None and math.isfinite(rs):
            lines.append(f"Relative strength vs SPY (20d): {rs:+.1f} pp")
        conv = corpus.get("conviction")
        if conv:
            lines.append(f"Conviction tier: {conv}")
        # Exit-debate evidence (rendered only when present; entry corpus omits
        # these keys, so this block is a no-op for entry debates).
        pnl = corpus.get("unrealized_pnl_pct")
        if pnl is not None and math.isfinite(pnl):
            lines.append(f"Unrealized P&L: {pnl:+.1f}%")
        tier = corpus.get("deterioration_tier")
        if tier:
            lines.append(f"Deterioration tier: {tier}")
        dsig = corpus.get("deterioration_signals")
        if dsig:
            lines.append(f"Deterioration signals — {dsig}")
        ero = corpus.get("thesis_erosion_score")
        if ero is not None and math.isfinite(float(ero)):
            lines.append(f"Thesis erosion score: {round(float(ero))}/100")
        dh = corpus.get("days_held")
        if dh is not None:
            lines.append(f"Days held: {dh}")
        sd = corpus.get("stop_distance_pct")
        if sd is not None and math.isfinite(sd):
            lines.append(f"Distance above protective stop: {sd:+.1f}%")
        thesis = corpus.get("user_thesis")
        if thesis:
            lines.append(f"Original buy thesis: {thesis}")
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
        _cur_price = float(grow_bundle["history"]["Close"].iloc[-1])
        if math.isfinite(_cur_price):
            corpus["current_price"] = round(_cur_price, 2)
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
        _m5 = float(grow_bundle["history"]["Close"].pct_change(5).iloc[-1] * 100)
        if math.isfinite(_m5):
            corpus["momentum_5d_pct"] = round(_m5, 1)
    except Exception:
        pass

    try:
        _m20 = float(grow_bundle["history"]["Close"].pct_change(20).iloc[-1] * 100)
        if math.isfinite(_m20):
            corpus["momentum_20d_pct"] = round(_m20, 1)
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


def build_exit_corpus(ticker, port_df_row, held_data_bundle, erosion_cache_row,
                      trade_row, deterioration_payload) -> dict:
    """
    Assemble the evidence package for an exit ("Challenge This Exit") debate.

    Parameters:
        ticker                — str, the held ticker under a TRIM/EXIT signal
        port_df_row           — dict from the enriched port_df row (keys:
                                Price, Score, Sector, "P&L (%)", Stop, …)
        held_data_bundle      — held_data[ticker] bundle (keys: df, atr,
                                position_age_days, …)
        erosion_cache_row     — db.load_thesis_erosion_cache row or None
                                (key: erosion_score)
        trade_row             — most-recent BUY trade row (dict) or None
                                (key: user_thesis)
        deterioration_payload — exit_advisor.assess_holding output for this
                                ticker (keys: tier, dd_from_peak_pct,
                                rel_strength, below_ma_count, trend_ma, sma,
                                trim_floor, exit_floor). The Bear's real
                                ammunition — cited, never invented.

    Returns a flat dict. Each field individually try/except-ed — missing fields
    excluded silently. Never raises overall.
    """
    corpus: dict = {"ticker": str(ticker).upper().strip(), "debate_type": "exit"}

    _row = port_df_row or {}
    _bd  = held_data_bundle or {}
    _det = deterioration_payload or {}
    _tr  = trade_row or {}

    # Price history — held_data bundles use "df"; fall back to "history".
    _hist = _bd.get("df")
    if _hist is None:
        _hist = _bd.get("history")

    try:
        _px = _row.get("Price")
        if _px is not None:
            corpus["current_price"] = round(float(_px), 2)
        elif _hist is not None and not _hist.empty and "Close" in _hist.columns:
            corpus["current_price"] = round(float(_hist["Close"].iloc[-1]), 2)
    except Exception:
        pass

    try:
        _pnl = _row.get("P&L (%)")
        if _pnl is not None:
            corpus["unrealized_pnl_pct"] = round(float(_pnl), 1)
    except Exception:
        pass

    try:
        if _det.get("tier"):
            corpus["deterioration_tier"] = _det.get("tier")
    except Exception:
        pass

    # Deterioration signals — the Bear's factual ammunition, assembled from the
    # real numbers behind the tier (never fabricated).
    try:
        _sig = []
        if _det.get("dd_from_peak_pct") is not None:
            _sig.append(f"down {_det['dd_from_peak_pct']:.1f}% from peak")
        if _det.get("trim_floor") is not None and _det.get("exit_floor") is not None:
            _sig.append(f"trigger {_det['trim_floor']:.0f}%/{_det['exit_floor']:.0f}%")
        if _det.get("below_ma_count") is not None:
            _sig.append(f"{_det['below_ma_count']}/3 sessions below SMA{_det.get('trend_ma', '')}")
        if _det.get("rel_strength") is not None:
            _sig.append(f"rel-strength {_det['rel_strength']:+.1f}pp vs SPY")
        if _det.get("sma") is not None:
            _sig.append(f"SMA{_det.get('trend_ma', '')} ${_det['sma']:.2f}")
        if _sig:
            corpus["deterioration_signals"] = "; ".join(_sig)
    except Exception:
        pass

    # Relative strength — reuse the payload's already-computed value (no re-fetch).
    try:
        if _det.get("rel_strength") is not None:
            corpus["rs_vs_spy_20d_pp"] = round(float(_det["rel_strength"]), 1)
    except Exception:
        pass

    try:
        if erosion_cache_row and erosion_cache_row.get("erosion_score") is not None:
            corpus["thesis_erosion_score"] = erosion_cache_row.get("erosion_score")
    except Exception:
        pass

    try:
        if _row.get("Score") is not None:
            corpus["composite_score"] = _row.get("Score")
    except Exception:
        pass

    try:
        _lbl = _row.get("Signal") or _row.get("Verdict") or _row.get("composite_label")
        if _lbl:
            corpus["composite_label"] = _lbl
    except Exception:
        pass

    try:
        if _hist is not None and not _hist.empty and "Close" in _hist.columns:
            _m5 = float(_hist["Close"].pct_change(5).iloc[-1] * 100)
            if math.isfinite(_m5):
                corpus["momentum_5d_pct"] = round(_m5, 1)
    except Exception:
        pass

    try:
        if _hist is not None and not _hist.empty and "Close" in _hist.columns:
            _m20 = float(_hist["Close"].pct_change(20).iloc[-1] * 100)
            if math.isfinite(_m20):
                corpus["momentum_20d_pct"] = round(_m20, 1)
    except Exception:
        pass

    try:
        _age = _bd.get("position_age_days")
        if _age is not None:
            corpus["days_held"] = int(_age)
    except Exception:
        pass

    try:
        _stop = _row.get("Stop")
        _px2  = corpus.get("current_price")
        if _stop is not None and _px2:
            corpus["stop_distance_pct"] = round(
                (float(_px2) - float(_stop)) / float(_px2) * 100, 1
            )
    except Exception:
        pass

    try:
        if _row.get("Sector"):
            corpus["sector"] = _row.get("Sector")
    except Exception:
        pass

    try:
        if _tr.get("user_thesis"):
            corpus["user_thesis"] = str(_tr.get("user_thesis"))
    except Exception:
        pass

    return corpus


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

    _is_exit = debate_type == "exit"

    # Round 1 — Bull opens (entry: defend the buy; exit: defend the hold)
    if _is_exit:
        _r1 = (f"Evidence:\n{corpus_text}\n\nPresent the strongest specific case for "
               f"CONTINUING TO HOLD {ticker} despite the exit signal. Anchor on the original "
               f"thesis if one is supplied. Do NOT argue to hold merely because the position "
               f"is underwater — argue only from the thesis and current data.")
    else:
        _r1 = f"Evidence:\n{corpus_text}\n\nPresent the strongest specific case FOR entering {ticker}."
    r1 = _call_haiku(client, model, _BULL_SYSTEM, _r1)
    if not r1:
        return {"transcript": transcript, "verdict": None, "partial": True, "error": "round1_failed"}
    transcript.append({"round": 1, "agent": "bull", "text": r1})

    # Round 2 — Bear responds (entry: case against; exit: case for exiting now)
    if _is_exit:
        _r2 = (f"Evidence:\n{corpus_text}\n\n---\nBull's case for holding:\n{r1}\n\n---\n"
               f"The exit signal has fired. Make the strongest specific case for EXITING NOW, "
               f"citing the specific deterioration signals in the evidence. This is closing an "
               f"existing long, not opening a short.")
    else:
        _r2 = (f"Evidence:\n{corpus_text}\n\n---\nBull's case:\n{r1}\n\n---\n"
               f"Counter Bull's argument with the strongest specific evidence AGAINST this position.")
    r2 = _call_haiku(client, model, _BEAR_SYSTEM, _r2)
    if not r2:
        return {"transcript": transcript, "verdict": None, "partial": True, "error": "round2_failed"}
    transcript.append({"round": 2, "agent": "bear", "text": r2})

    # Round 3 — Bull rebuts
    if _is_exit:
        _r3 = (f"Evidence:\n{corpus_text}\n\n---\nBull Round 1:\n{r1}\n\nBear's exit case:\n{r2}\n\n---\n"
               f"Rebut the exit case — is the deterioration temporary or noise, or does it break the thesis?")
    else:
        _r3 = (f"Evidence:\n{corpus_text}\n\n---\nBull Round 1:\n{r1}\n\nBear's counter:\n{r2}\n\n---\n"
               f"Rebut Bear's specific objection with evidence.")
    r3 = _call_haiku(client, model, _BULL_SYSTEM, _r3)
    if not r3:
        return {"transcript": transcript, "verdict": None, "partial": True, "error": "round3_failed"}
    transcript.append({"round": 3, "agent": "bull", "text": r3})

    # Round 4 — Bear closes
    if _is_exit:
        _r4 = (f"Evidence:\n{corpus_text}\n\n---\nBull:\n{r1}\n\nBear:\n{r2}\n\nBull rebuttal:\n{r3}\n\n---\n"
               f"State the ONE reason the exit should still stand despite the Bull's defense.")
    else:
        _r4 = (f"Evidence:\n{corpus_text}\n\n---\nBull:\n{r1}\n\nBear:\n{r2}\n\nBull rebuttal:\n{r3}\n\n---\n"
               f"State the ONE remaining concern that Bull's rebuttal did not adequately address.")
    r4 = _call_haiku(client, model, _BEAR_SYSTEM, _r4)
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
