"""
Hidden Same-Bet Detector — D1 of the Agentic Intelligence Roadmap v2.

Finds positions that LOOK diversified (different sectors, low price
correlation) but secretly share the same underlying investment thesis/
assumption. One Haiku call/day semantically clusters held positions'
saved buy theses; a pure-Python cross-reference against
portfolio_intelligence.correlation_clusters() classifies each validated
cluster as unverified / possible / confirmed.

Full design in docs/plans/hidden-same-bet-detector.md.

Design principles (mirrors regime_stress.py / debate_agent.py):
- No Streamlit imports — pure logic only.
- api_key passed in; never read from st.secrets.
- Every LLM-calling body wrapped in bare except Exception so a rate-limit
  or outage degrades gracefully (returns None, never raises).
- Never forces a grouping — "no shared assumption" is a valid, expected
  answer when theses are genuinely independent.
- TWO-LAYER fabrication guard (semantic clustering over free prose is
  fuzzier than citing structured data, so ticker validation alone isn't
  enough): (1) every returned ticker must normalized-match a ticker in
  the supplied corpus; (2) every returned quote must verifiably appear
  (case-insensitive substring) in that ticker's own thesis text. A
  cluster member failing either layer is dropped; if a group falls below
  2 verified members it is discarded entirely.
- The unverified/possible/confirmed classification is a pure-Python
  check against correlation_clusters()'s real output (or its absence) —
  never an LLM judgment call, so it cannot hallucinate a false verdict.
"""

import json

from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC

_THESIS_TRUNCATE_CHARS = 1500   # prompt-sizing tuning, not a policy threshold
_MIN_THESIS_POSITIONS  = 2      # minimum thesis-bearing positions to attempt clustering

_CLUSTER_SYSTEM = """You are a portfolio analyst looking for hidden concentration risk. Given a list of held positions with their tickers, sectors, and the investor's own stated investment thesis for each, identify any groups of 2 or more positions whose theses rest on the SAME underlying market/macro/sector assumption — even if their sectors differ. Do not force a connection between theses that merely sound superficially similar; two positions are only a genuine match if breaking ONE assumption would hurt both. For each group found: name the shared assumption in one plain sentence, and for EACH member ticker quote the exact short span (a few words, verbatim, do not paraphrase) from THAT ticker's own thesis text that expresses the assumption. If no group shares a genuine underlying assumption, say so plainly — an empty result is a valid, expected answer. Output ONLY valid JSON: {"clusters": [{"tickers": ["TICK1","TICK2"], "shared_assumption": "one sentence", "quotes": {"TICK1": "exact quoted span from TICK1's thesis", "TICK2": "exact quoted span from TICK2's thesis"}}, ...]}"""


# ── Corpus assembly ───────────────────────────────────────────────────────

def build_thesis_corpus(port_df, trades_df) -> list[dict]:
    """
    Assemble {ticker, sector, thesis_text} for every currently-held ticker
    with a non-empty saved user_thesis (most recent BUY row per ticker).

    Never raises. Returns [] on any failure, or if fewer than
    _MIN_THESIS_POSITIONS tickers qualify (nothing to cluster).
    """
    try:
        if port_df is None or getattr(port_df, "empty", True):
            return []
        if trades_df is None or getattr(trades_df, "empty", True):
            return []
        if "Ticker" not in port_df.columns or "ticker" not in trades_df.columns:
            return []

        held_tickers = {str(t).upper() for t in port_df["Ticker"].dropna().tolist()}
        sector_by_ticker = {}
        if "Sector" in port_df.columns:
            for _, row in port_df.iterrows():
                sector_by_ticker[str(row.get("Ticker", "")).upper()] = row.get("Sector", "") or ""

        buys = trades_df[trades_df["action"].astype(str).str.upper() == "BUY"]
        if buys.empty:
            return []

        corpus = []
        for ticker in sorted(held_tickers):
            t_buys = buys[buys["ticker"].astype(str).str.upper() == ticker]
            if t_buys.empty:
                continue
            # trades_df loads newest-first (db.load_trades() orders
            # traded_at desc, preserved through session_state) — take the
            # most recent BUY that actually HAS a non-empty thesis, so a
            # later add without a re-entered thesis doesn't shadow an
            # earlier BUY that was recorded with a real one.
            thesis = None
            for _, row in t_buys.iterrows():
                _thesis_val = row.get("user_thesis")
                if _thesis_val and str(_thesis_val).strip():
                    thesis = str(_thesis_val).strip()
                    break
            if not thesis:
                continue
            corpus.append({
                "ticker": ticker,
                "sector": sector_by_ticker.get(ticker, ""),
                "thesis_text": thesis,
            })

        return corpus if len(corpus) >= _MIN_THESIS_POSITIONS else []
    except Exception:
        return []


def _truncate(text: str, max_len: int = _THESIS_TRUNCATE_CHARS):
    """Returns (possibly-truncated text, was_truncated). Never raises."""
    try:
        if len(text) <= max_len:
            return text, False
        return text[:max_len], True
    except Exception:
        return text, False


def _format_corpus_for_prompt(corpus: list[dict]):
    """Render the corpus into a prompt block. Returns (text, any_truncated).
    Never raises."""
    try:
        lines = []
        any_truncated = False
        for item in corpus:
            text, truncated = _truncate(item["thesis_text"])
            any_truncated = any_truncated or truncated
            lines.append(
                f"Ticker: {item['ticker']} (sector: {item.get('sector') or 'unknown'})\n"
                f"Thesis: {text}"
            )
        return "\n\n".join(lines), any_truncated
    except Exception:
        return "", False


# ── Cluster generation ────────────────────────────────────────────────────

def generate_thesis_clusters(corpus: list[dict], api_key: str,
                             model: str = "claude-haiku-4-5-20251001") -> dict | None:
    """
    Single Haiku call. Returns {"clusters": list[dict], "truncated": bool}
    or None on any failure (no key, too few positions, timeout, malformed
    response).

    An empty clusters list is a VALID result (no shared assumption found —
    never treated as a failure, still returned as a dict, still cacheable).
    Each returned cluster is validated two ways before being included:
      1. every ticker must normalized-match a ticker in the supplied corpus
      2. every quote must verifiably appear (case-insensitive substring) in
         that ticker's own thesis_text
    A member failing either check is dropped; a cluster with fewer than 2
    verified members after validation is discarded entirely.

    Never raises.
    """
    if not api_key or len(corpus) < _MIN_THESIS_POSITIONS:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt_text, truncated = _format_corpus_for_prompt(corpus)
        response = client.messages.create(
            model=model,
            max_tokens=600,
            temperature=0.2,
            system=_CLUSTER_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Positions:\n{prompt_text}\n\nIdentify shared-assumption clusters now.",
            }],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        if not response.content:
            return None
        raw_text = response.content[0].text.strip()
        clusters = _parse_cluster_response(raw_text, corpus)
        if clusters is None:
            return None
        return {"clusters": clusters, "truncated": truncated}
    except Exception:
        return None


def _parse_cluster_response(raw_json: str, corpus: list[dict]):
    """
    Parse + two-layer-validate the Haiku JSON response. Returns a
    (possibly empty) list of validated clusters, or None only on a
    structural parse failure (malformed JSON / wrong shape) — NOT on "no
    clusters found", which is a valid [] result. Never raises.
    """
    if not raw_json:
        return None
    try:
        cleaned = raw_json.strip()
        # Strip markdown fences (same pattern as debate_agent._parse_judge).
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

        raw_clusters = parsed.get("clusters")
        if not isinstance(raw_clusters, list):
            return None  # malformed shape — structural failure, not "none found"

        # Canonical ticker + thesis-text lookup, normalized-match keyed.
        by_norm_ticker = {c["ticker"].strip().casefold(): c for c in corpus}

        validated = []
        for raw_c in raw_clusters:
            if not isinstance(raw_c, dict):
                continue
            raw_tickers = raw_c.get("tickers") or []
            raw_quotes  = raw_c.get("quotes") or {}
            assumption  = raw_c.get("shared_assumption")
            if not isinstance(raw_tickers, list) or not assumption:
                continue
            if not isinstance(raw_quotes, dict):
                raw_quotes = {}

            # Case-insensitive lookup into the quotes dict (LLM may echo
            # ticker casing inconsistently between "tickers" and "quotes").
            quotes_by_norm = {str(k).strip().casefold(): v for k, v in raw_quotes.items()}

            verified_tickers = []
            _seen_tickers = set()
            for raw_t in raw_tickers:
                norm = str(raw_t).strip().casefold()
                match = by_norm_ticker.get(norm)
                if match is None:
                    continue  # layer 1: unknown ticker — drop

                quote = quotes_by_norm.get(norm)
                if not quote:
                    continue  # no quote supplied for this member — drop

                quote_norm  = str(quote).strip().casefold()
                thesis_norm = match["thesis_text"].strip().casefold()
                if not quote_norm or quote_norm not in thesis_norm:
                    continue  # layer 2: unverifiable quote — drop

                canonical = match["ticker"]
                if canonical in _seen_tickers:
                    continue  # dedup — a repeated ticker can't count twice toward the min-2 floor
                _seen_tickers.add(canonical)
                verified_tickers.append(canonical)

            if len(verified_tickers) < _MIN_THESIS_POSITIONS:
                continue  # group collapsed below minimum after validation

            validated.append({
                "tickers": verified_tickers,
                "shared_assumption": str(assumption).strip(),
            })

        return validated
    except Exception:
        return None


# ── Classification (pure Python, zero LLM — cannot hallucinate) ───────────

def classify_clusters(validated_clusters, correlation_clusters_result) -> list[dict]:
    """
    Classify each validated thesis-cluster as 'unverified' / 'possible' /
    'confirmed' against portfolio_intelligence.correlation_clusters()'s
    output. Pure Python — cannot hallucinate a classification.

      - correlation_clusters_result is None (price-correlation data
        unavailable this session) -> every cluster is 'unverified'
        ("no numbers to check" is never coerced into 'possible'/'hidden').
      - Otherwise: 'confirmed' if some existing price-correlation
        cluster's tickers is a superset of this group's tickers;
        'possible' otherwise. Also attaches 'corr_subpairs' — any
        already-price-correlated 2+ ticker subsets within the group, so a
        partial overlap isn't silently overstated as fully 'possible'.

    Never raises. Returns [] if validated_clusters is empty/None.
    """
    try:
        if not validated_clusters:
            return []

        result = []
        for cluster in validated_clusters:
            tickers = set(cluster["tickers"])

            if correlation_clusters_result is None:
                state    = "unverified"
                subpairs = []
            else:
                price_ticker_sets = [set(pc.get("tickers") or []) for pc in correlation_clusters_result]
                confirmed = any(tickers.issubset(pset) for pset in price_ticker_sets)
                state = "confirmed" if confirmed else "possible"

                subpairs = []
                if not confirmed:
                    for pset in price_ticker_sets:
                        overlap = tickers & pset
                        if len(overlap) >= 2:
                            subpairs.append(sorted(overlap))

            result.append({
                "tickers":           cluster["tickers"],
                "shared_assumption": cluster["shared_assumption"],
                "state":             state,
                "corr_subpairs":     subpairs,
            })

        return result
    except Exception:
        return []
