"""
Missed-Opportunity Pattern — O1 of the Agentic Intelligence Roadmap v2.

Finds a DESCRIPTIVE pattern across engine "new_pick" buy recommendations the
user never acted on, grounded entirely in real, already-computed outcome
data (recommendations_history.distinct_missed()). One Haiku call/day; a
pure-Python two-layer guard validates every cited ticker AND verifies the
claimed shared trait actually holds for it (predicate verification against
a closed set of categorical fields) before rendering.

Full design in docs/plans/missed-opportunity-pattern.md.

Design principles (mirrors thesis_cluster.py):
- No Streamlit imports — pure logic only.
- api_key passed in; never read from st.secrets.
- Every LLM-calling body wrapped in bare except Exception so a rate-limit
  or outage degrades gracefully (returns None, never raises).
- Never forces a pattern — "no coherent pattern" is a valid, expected
  answer when the misses are genuinely unrelated.
- TWO-LAYER fabrication guard: (1) every returned ticker must
  normalized-match a ticker in the supplied corpus; (2) the claimed
  shared_dimension must be one of a CLOSED set of categorical fields
  (sector/price_band/composite_band/verdict/outcome_label), and every
  remaining ticker's real pre-computed value for that dimension must
  normalized-match the claimed shared_value. A ticker failing either layer
  is dropped from the pattern (not the whole pattern discarded); a pattern
  below the minimum member floor after validation is discarded entirely.
  This is STRICTER grounding than a free-text quote match would be, since
  every field here is a closed, mechanically checkable category.
- Descriptive framing only ("what these skipped names have in common"),
  never causal ("why you skipped them") — a causal claim invites an
  unverifiable psychology inference that reads as forward-looking advice.
- MUST be built from the UNFILTERED enriched snapshot (e.g. app.py's
  _rh_enriched_all), never a status-filtered view, or
  distinct_missed()'s "acted via ANY surfacing" safeguard silently breaks.
"""

import json

from stock_analyzer.constants import (
    LLM_REQUEST_TIMEOUT_SEC,
    COMPOSITE_STRONG_BUY, COMPOSITE_BUY, COMPOSITE_HOLD,
)
from stock_analyzer.recommendations_history import distinct_missed

_MIN_MISSED_TICKERS  = 3   # corpus floor — matches predictive_analytics.py's min_n=3
_MIN_PATTERN_TICKERS = 2   # per-pattern floor — safe now that predicate verification exists

_PRICE_BANDS = [
    ("under $50", 0.0,   50.0),
    ("$50-150",   50.0,  150.0),
    ("$150-300",  150.0, 300.0),
    ("over $300", 300.0, float("inf")),
]

_RECOGNIZED_DIMENSIONS = {"sector", "price_band", "composite_band", "verdict", "outcome_label"}

_PATTERN_SYSTEM = """You are a portfolio behavioral analyst. Given a list of buy recommendations the investor never acted on, along with how each one actually performed, identify any group of 2 or more that share a real common trait — even if their sectors differ. Describe what these skipped names have in common, not why the investor might have skipped them (never speculate about psychology or motive). For each group, name the shared trait using ONLY one of these five dimensions: sector, price_band, composite_band, verdict, or outcome_label — and state the EXACT value as supplied in the data (do not invent a new category or value, do not paraphrase the value). Cite the specific tickers. If no group shares a genuine common trait, say so plainly — do not force a connection between misses that merely happen to sit near each other in the list. Output ONLY valid JSON: {"patterns": [{"tickers": ["TICK1","TICK2"], "shared_dimension": "sector"|"price_band"|"composite_band"|"verdict"|"outcome_label", "shared_value": "<exact value from the data>", "pattern_label": "one plain descriptive sentence"}, ...]}"""


# ── Corpus assembly ───────────────────────────────────────────────────────

def _price_band(price) -> str:
    """Bucket a price into a fixed, closed band. Never raises."""
    if price is None:
        return "unknown"
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "unknown"
    for label, lo, hi in _PRICE_BANDS:
        if lo <= p < hi:
            return label
    return "unknown"


def _composite_band(score) -> str:
    """Bucket a composite score using the SAME band boundaries/labels as
    recommendations_history.by_composite_band() — no new breakpoints
    invented. Never raises."""
    if score is None:
        return "Unscored"
    try:
        c = float(score)
    except (TypeError, ValueError):
        return "Unscored"
    if c >= COMPOSITE_STRONG_BUY:
        return f"Strong Buy (≥{COMPOSITE_STRONG_BUY})"
    if c >= COMPOSITE_BUY:
        return f"Buy ({COMPOSITE_BUY}–{COMPOSITE_STRONG_BUY - 1})"
    if c >= COMPOSITE_HOLD:
        return f"Hold zone ({COMPOSITE_HOLD}–{COMPOSITE_BUY - 1})"
    return f"Sell zone (<{COMPOSITE_HOLD})"


def build_missed_opportunity_corpus(enriched_all, rec_types=("new_pick",)) -> list[dict]:
    """
    Real missed-opportunity records enriched with closed-category fields for
    pattern grouping.

    MUST be called with the UNFILTERED enriched snapshot (app.py's
    `_rh_enriched_all`) — never a status-filtered view (`_rh_enriched` after
    the Acted-only/Missed-only dropdown mutates it) — or
    distinct_missed()'s "acted via ANY surfacing" safeguard silently breaks
    (a name bought on a buy_candidate day but skipped as new_pick would be
    wrongly counted missed under a "Missed only" filter).

    Never raises. Returns [] on any failure, or if fewer than
    _MIN_MISSED_TICKERS graded missed tickers qualify.
    """
    try:
        if not enriched_all:
            return []

        missed_rows = distinct_missed(enriched_all, rec_types=rec_types)
        if len(missed_rows) < _MIN_MISSED_TICKERS:
            return []

        # Index the SAME rec_type-scoped pool distinct_missed() draws its
        # representative row from, keyed (ticker, rec_date) -> row, so this
        # enrichment lookup can't accidentally pick a different same-day
        # surfacing (e.g. a buy_candidate row) than the one distinct_missed
        # actually used to compute the outcome.
        pool_by_key = {}
        for r in enriched_all:
            if rec_types is not None and r.get("rec_type") not in rec_types:
                continue
            key = (r.get("ticker"), r.get("rec_date"))
            if key not in pool_by_key:
                pool_by_key[key] = r

        corpus = []
        for m in missed_rows:
            rep = pool_by_key.get((m["ticker"], m["first_rec_date"])) or {}
            sector = str(rep.get("sector") or "").strip() or "Other"

            corpus.append({
                "ticker":         m["ticker"],
                "sector":         sector,
                "price_band":     _price_band(rep.get("price_at_surface")),
                "composite_band": _composite_band(rep.get("composite_score")),
                "verdict":        str(m.get("verdict") or "").strip() or "n/a",
                "outcome_label":  m.get("outcome_label") or "unknown",
                "outcome_pct":    m.get("outcome_pct"),
                "alpha_pct":      m.get("alpha_pct"),
                "first_rec_date": m.get("first_rec_date"),
            })

        return corpus
    except Exception:
        return []


def _format_corpus_for_prompt(corpus: list[dict]) -> str:
    """Render the corpus into a prompt block. Never raises."""
    try:
        lines = []
        for item in corpus:
            alpha_str = (
                f"{item['alpha_pct']:+.1f}pp" if item.get("alpha_pct") is not None else "n/a"
            )
            outcome_pct = item.get("outcome_pct")
            outcome_str = f"{outcome_pct:+.1f}%" if outcome_pct is not None else "n/a"
            lines.append(
                f"Ticker: {item['ticker']} | sector: {item['sector']} | "
                f"price_band: {item['price_band']} | composite_band: {item['composite_band']} | "
                f"verdict: {item['verdict']} | outcome_label: {item['outcome_label']} "
                f"({outcome_str}) | alpha vs SPY: {alpha_str}"
            )
        return "\n".join(lines)
    except Exception:
        return ""


# ── Pattern generation ─────────────────────────────────────────────────────

def generate_missed_opportunity_patterns(corpus: list[dict], api_key: str,
                                         model: str = "claude-haiku-4-5-20251001") -> dict | None:
    """
    Single Haiku call. Returns {"patterns": list[dict]} or None on any
    failure (no key, too few positions, timeout, malformed response).

    An empty patterns list is a VALID result (no coherent pattern found —
    never treated as a failure, still cacheable). Each returned pattern is
    validated two ways before being included — see module docstring.

    Never raises.
    """
    if not api_key or len(corpus) < _MIN_MISSED_TICKERS:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt_text = _format_corpus_for_prompt(corpus)
        response = client.messages.create(
            model=model,
            max_tokens=600,
            temperature=0.2,
            system=_PATTERN_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Skipped recommendations:\n{prompt_text}\n\nIdentify shared-trait patterns now.",
            }],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        if not response.content:
            return None
        raw_text = response.content[0].text.strip()
        patterns = _parse_pattern_response(raw_text, corpus)
        if patterns is None:
            return None
        return {"patterns": patterns}
    except Exception:
        return None


def _parse_pattern_response(raw_json: str, corpus: list[dict]):
    """
    Parse + two-layer-validate the Haiku JSON response. Returns a
    (possibly empty) list of validated patterns, or None only on a
    structural parse failure — NOT on "no patterns found", which is a
    valid [] result. Never raises.
    """
    if not raw_json:
        return None
    try:
        cleaned = raw_json.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
        except Exception:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            try:
                parsed = json.loads(cleaned[start: end + 1])
            except Exception:
                return None

        if not isinstance(parsed, dict):
            return None

        raw_patterns = parsed.get("patterns")
        if not isinstance(raw_patterns, list):
            return None  # malformed shape — structural failure, not "none found"

        by_norm_ticker = {c["ticker"].strip().casefold(): c for c in corpus}

        validated = []
        for raw_p in raw_patterns:
            if not isinstance(raw_p, dict):
                continue
            raw_tickers = raw_p.get("tickers") or []
            dimension   = raw_p.get("shared_dimension")
            value       = raw_p.get("shared_value")
            label       = raw_p.get("pattern_label")
            if not isinstance(raw_tickers, list) or not label:
                continue
            if dimension not in _RECOGNIZED_DIMENSIONS or value is None:
                continue  # unrecognized dimension — nothing to verify against, discard whole pattern

            value_norm = str(value).strip().casefold()

            verified_tickers = []
            seen = set()
            for raw_t in raw_tickers:
                norm = str(raw_t).strip().casefold()
                match = by_norm_ticker.get(norm)
                if match is None:
                    continue  # layer 1: unknown ticker — drop

                real_value = str(match.get(dimension, "")).strip().casefold()
                if not real_value or real_value != value_norm:
                    continue  # layer 2: claimed predicate doesn't hold — drop

                canonical = match["ticker"]
                if canonical in seen:
                    continue  # dedup
                seen.add(canonical)
                verified_tickers.append(canonical)

            if len(verified_tickers) < _MIN_PATTERN_TICKERS:
                continue  # pattern collapsed below minimum after validation

            validated.append({
                "tickers":          verified_tickers,
                "shared_dimension": dimension,
                "shared_value":     str(value).strip(),
                "pattern_label":    str(label).strip(),
            })

        return validated
    except Exception:
        return None


# ── Outcome mix (pure Python — the posture safeguard) ──────────────────────

def pattern_outcome_mix(tickers, corpus) -> dict:
    """
    Count win/loss/flat/unknown among the given tickers, using each
    ticker's real outcome_label from the corpus. Pure Python, no LLM — lets
    the render show a pattern's real win/dodge/flat mix so it can never
    read as "these were all winners, buy the next one like it."

    Never raises.
    """
    try:
        by_ticker = {c["ticker"]: c for c in corpus}
        counts = {"win": 0, "loss": 0, "flat": 0, "unknown": 0}
        for t in tickers:
            label = (by_ticker.get(t) or {}).get("outcome_label") or "unknown"
            counts[label] = counts.get(label, 0) + 1
        return counts
    except Exception:
        return {"win": 0, "loss": 0, "flat": 0, "unknown": 0}
