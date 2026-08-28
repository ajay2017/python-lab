"""
Structural Vulnerability Scanner — Phase 1.

Composes three already-shipped portfolio_intelligence.py panels
(correlation_clusters, risk_budget, factor_tilt) with one genuinely new
calculation — a Blast Radius Map estimating cascade drawdown from a
single-position shock — and ties it together with a daily Haiku-generated
narrative. Full design in docs/plans/structural-vulnerability-scanner.md.

Design principles (mirrors debate_agent.py):
- No Streamlit imports — pure logic only.
- api_key passed in; never read from st.secrets.
- Every LLM-calling body wrapped in bare except Exception so a rate-limit or
  outage degrades gracefully (returns None, never raises).
- Reuses CORR_HIGH_PAIRS_THRESHOLD (constants.py) to gate which correlations
  contribute a cascade — no new correlation cutoff invented here.
"""

from itertools import combinations

from stock_analyzer.constants import CORR_HIGH_PAIRS_THRESHOLD, LLM_REQUEST_TIMEOUT_SEC
from stock_analyzer.util import factor_tilt_evidence_line

# ── Module-level defaults (what-if scenario inputs, NOT investment-policy
# thresholds — same class of value as stress_test.py's _SECTOR_SHOCKS) ───────
BLAST_RADIUS_SHOCK_PCT = -20.0   # default single-position shock magnitude
BLAST_RADIUS_TOP_N     = 3       # number of top risk-contributors to shock

_NARRATIVE_SYSTEM = """You are a portfolio structural-risk analyst. Given the clustering, risk concentration, and cascade-shock evidence below, write a 2-4 sentence explanation of the SINGLE most dangerous structural pattern in this portfolio — name the specific tickers and numbers involved. A "Factor tilt:" line is ALWAYS present in the evidence and says one of three things: a measured tilt, that it was measured but unusable, or that it was NOT MEASURED. If it reports a measured tilt, connect it to the cluster/blast-radius evidence only if they reinforce each other. If it says NOT MEASURED or unknown, do not mention factor exposure at all, and never treat its absence as evidence that factor concentration is low or balanced. Do not invent a real-world catalyst (news, sector story) not present in the evidence — describe the co-movement mechanics only. If the evidence shows no meaningful clustering or cascade risk, say so plainly instead of manufacturing a concern."""


# ── Blast Radius Map ──────────────────────────────────────────────────────

def blast_radius(corr_df, risk_budget_positions, shock_pct=BLAST_RADIUS_SHOCK_PCT,
                  top_n=BLAST_RADIUS_TOP_N):
    """
    Estimate cascade portfolio impact from shocking each of the top-N risk
    contributors by -shock_pct%, using a single-factor beta approximation:
    beta_H_to_T = corr(T,H) * (vol_H/vol_T); comove_H = shock_pct * beta_H_to_T.
    Only tickers with abs(corr) >= CORR_HIGH_PAIRS_THRESHOLD contribute a
    non-zero comove (treated as independent/0 otherwise) — reuses the
    existing, already-reviewed correlation threshold rather than inventing a
    new cutoff. Uses SIGNED correlation for comove_pct (a strong negative
    correlation is a real hedge/offset).

    weight_pct and vol_annualized_pct are read directly from
    risk_budget_positions (risk_budget()["positions"]) — no separate weights
    dict, so there is exactly one source of truth for a ticker's weight.

    CRITICAL: weight_pct is 0-100 scale; comove_pct is also percent-scale.
    portfolio_impact_pct = sum((weight_i / 100.0) * comove_i for all i) —
    MUST normalize weight to a fraction before multiplying, or the result
    overstates by 100x (an 8%-weight name shocked -20% must contribute -1.6%,
    not -160%).

    Returns one dict per shocked ticker (the top_n by risk_pct, already
    sorted that way in risk_budget_positions):
    {
        "shocked_ticker": str,
        "shock_pct": float,
        "portfolio_impact_pct": float,
        "contributing_tickers": [
            {"ticker": str, "corr": float, "comove_pct": float}, ...
        ],  # only names with abs(corr) >= CORR_HIGH_PAIRS_THRESHOLD, sorted by
            # abs(comove_pct) descending
    }
    Never raises — degrades to [] on any missing/malformed input (empty
    corr_df, empty risk_budget_positions, ticker not in corr_df, etc.).
    """
    try:
        if corr_df is None or getattr(corr_df, "empty", True):
            return []
        if not risk_budget_positions:
            return []

        lookup = {}
        for p in risk_budget_positions:
            t = p.get("ticker")
            if not t:
                continue
            lookup[t] = {
                "weight_pct": p.get("weight_pct"),
                "vol_annualized_pct": p.get("vol_annualized_pct"),
            }

        shocked = risk_budget_positions[:top_n]

        results = []
        for shock_pos in shocked:
            T = shock_pos.get("ticker")
            if not T or T not in lookup:
                continue

            weight_T = lookup[T]["weight_pct"]
            vol_T = lookup[T]["vol_annualized_pct"]
            if weight_T is None:
                continue

            portfolio_impact_pct = (weight_T / 100.0) * shock_pct
            contributing_tickers = []

            has_valid_vol_T = (
                vol_T is not None and vol_T == vol_T and vol_T != 0  # not None/NaN/0
            )

            if T in corr_df.index and T in corr_df.columns and has_valid_vol_T:
                for H, hinfo in lookup.items():
                    if H == T:
                        continue
                    if H not in corr_df.index or H not in corr_df.columns:
                        continue
                    vol_H = hinfo["vol_annualized_pct"]
                    weight_H = hinfo["weight_pct"]
                    if vol_H is None or weight_H is None:
                        continue
                    try:
                        corr_TH = float(corr_df.loc[T, H])
                    except Exception:
                        continue
                    if corr_TH != corr_TH:  # NaN
                        continue
                    if abs(corr_TH) < CORR_HIGH_PAIRS_THRESHOLD:
                        continue

                    beta = corr_TH * (vol_H / vol_T)
                    comove = shock_pct * beta
                    portfolio_impact_pct += (weight_H / 100.0) * comove
                    contributing_tickers.append({
                        "ticker": H,
                        "corr": round(corr_TH, 3),
                        "comove_pct": round(comove, 2),
                    })

            contributing_tickers.sort(key=lambda c: abs(c["comove_pct"]), reverse=True)

            results.append({
                "shocked_ticker": T,
                "shock_pct": shock_pct,
                "portfolio_impact_pct": round(portfolio_impact_pct, 2),
                "contributing_tickers": contributing_tickers,
            })

        return results
    except Exception:
        return []


# ── Narrative evidence assembly ──────────────────────────────────────────

def build_narrative_inputs(clusters, risk_budget_positions, blast_radius_results, factor_tilt=None):
    """
    Assembles the evidence dict passed into generate_structural_narrative()'s
    prompt. Never raises — degrades gracefully, omitting any section that's
    empty/None rather than fabricating placeholder text.

    Returns a dict with keys: "clusters" (list, possibly empty), "weakest_links"
    (top 3 risk_budget_positions by risk_pct — reslice defensively even though
    input is expected pre-sorted), "blast_radius" (the blast_radius_results list),
    "factor_tilt" (the factor_tilt dict if given and non-empty, else None).

    NOTE: a None here does NOT mean the prompt omits factor exposure.
    _format_evidence emits a "Factor tilt:" line UNCONDITIONALLY via
    util.factor_tilt_evidence_line, stating explicitly that the data was
    not measured. Silently omitting it was the F-260 defect.
    """
    try:
        weakest_links = list(risk_budget_positions or [])
        weakest_links = sorted(
            weakest_links, key=lambda p: p.get("risk_pct") or 0, reverse=True
        )[:3]

        _factor = factor_tilt if factor_tilt else None

        return {
            "clusters": list(clusters or []),
            "weakest_links": weakest_links,
            "blast_radius": list(blast_radius_results or []),
            "factor_tilt": _factor,
        }
    except Exception:
        return {"clusters": [], "weakest_links": [], "blast_radius": [], "factor_tilt": None}


def _format_evidence(evidence: dict) -> str:
    """Render the evidence dict into a readable text block. Never raises."""
    lines = []
    try:
        clusters = evidence.get("clusters") or []
        if clusters:
            lines.append("Correlation clusters:")
            for c in clusters:
                names = ", ".join(c.get("tickers") or [])
                lines.append(
                    f"  - {c.get('size')} positions ({names}): avg internal "
                    f"correlation {round(c.get('avg_internal_corr', 0), 2)}, "
                    f"combined weight {round(c.get('combined_weight_pct', 0), 1)}%, "
                    f"tier={c.get('tier')}"
                )
        else:
            lines.append("Correlation clusters: none detected.")

        weakest_links = evidence.get("weakest_links") or []
        if weakest_links:
            lines.append("Weakest links (top risk contributors):")
            for p in weakest_links:
                lines.append(
                    f"  - {p.get('ticker')}: risk contribution "
                    f"{round(p.get('risk_pct', 0), 1)}%, capital weight "
                    f"{round(p.get('weight_pct', 0), 1)}%"
                )

        blast = evidence.get("blast_radius") or []
        if blast:
            lines.append("Blast radius (single-position shock cascade):")
            for b in blast:
                lines.append(
                    f"  - Shocking {b.get('shocked_ticker')} by "
                    f"{b.get('shock_pct')}% → estimated portfolio impact "
                    f"{b.get('portfolio_impact_pct'):+.2f}%"
                )
                for c in (b.get("contributing_tickers") or [])[:3]:
                    lines.append(
                        f"      cascades via {c.get('ticker')} "
                        f"(corr {c.get('corr'):+.2f}, comove {c.get('comove_pct'):+.2f}%)"
                    )

        # Unconditional by design — see util.factor_tilt_evidence_line. This
        # used to be `if factor:` + `if valid:`, which silently emitted NOTHING
        # both when factor data was never loaded and when it was loaded but
        # unusable, so the model could not distinguish either from "measured,
        # nothing notable" and wrote as if its evidence were complete (F-260).
        lines.append(factor_tilt_evidence_line(evidence.get("factor_tilt")))

        return "\n".join(lines)
    except Exception:
        return ""


# ── Narrative generation ─────────────────────────────────────────────────

def generate_structural_narrative(evidence: dict, api_key: str,
                                   model: str = "claude-haiku-4-5-20251001") -> str | None:
    """
    Single Haiku call synthesizing the evidence dict into a 2-4 sentence
    structural narrative. Returns None on any failure (no key, timeout, empty
    response, exception) — caller shows the "unavailable this session"
    caption and does not cache. Never raises.
    """
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        evidence_text = _format_evidence(evidence)
        response = client.messages.create(
            model=model,
            max_tokens=300,
            temperature=0.3,
            system=_NARRATIVE_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Evidence:\n{evidence_text}\n\nWrite the structural narrative.",
            }],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        if not response.content:
            return None
        text = response.content[0].text.strip()
        return text or None
    except Exception:
        return None


# ── Phase 2: newly-formed cluster detection (Home "Structural alert" banner) ─

def detect_new_clusters(today_clusters, prior_cluster_snapshot, corr_df,
                         threshold=CORR_HIGH_PAIRS_THRESHOLD):
    """
    Compare today's live correlation_clusters() output against the most
    recent structural_scan_cache snapshot's cluster_snapshot (see
    db.load_structural_scan_baseline() -- "most recent scan on or before
    today", not strictly "prior"). Returns the subset of today's clusters
    containing at least one ticker PAIR that (a) was NOT already co-clustered
    in the baseline snapshot and (b) IS a direct positive edge today
    (corr_df.loc[a,b] >= threshold, SIGNED not abs() -- matches
    correlation_clusters()'s own edge condition, portfolio_intelligence.py:78)
    -- pair-level comparison restricted to verifiable direct positive edges,
    so a cluster losing a member (decorrelation) is never flagged, an
    anti-correlated transitive pair is never cited as if it were a real
    co-movement pairing, and a pair that only co-occurs transitively (never
    itself directly correlated >= threshold) is never cited as if it were.

    prior_cluster_snapshot: the "cluster_snapshot" field of the row returned
    by db.load_structural_scan_baseline() -- or None if NO scan has ever been
    generated (returns [] in that case; a first-ever comparison with nothing
    to diff against is not "everything is new"). An empty list [] (a real
    scan ran and found zero clusters that day) is a DIFFERENT, meaningful
    state and is NOT treated the same as None -- every one of today's real
    clusters then flags as new, the cleanest possible new-formation signal.

    Returns a list of dicts, each a copy of the matching today_clusters entry
    plus one new key:
        "new_pairs": [[tickerA, tickerB], ...]  -- the specific, verified-
                     direct-positive-edge pair(s) driving the "new" flag,
                     sorted, for citation in the banner (never a bare "new
                     cluster" claim with no basis, and never citing an
                     unverified or anti-correlated pair).
    Never raises -- degrades to [] on any missing/malformed input.
    """
    try:
        if prior_cluster_snapshot is None or not today_clusters:
            return []
        if corr_df is None or getattr(corr_df, "empty", True):
            return []

        prior_pairs = set()
        for c in prior_cluster_snapshot:
            members = sorted(c.get("tickers") or [])
            for a, b in combinations(members, 2):
                prior_pairs.add(frozenset((a, b)))

        flagged = []
        for c in today_clusters:
            members = sorted(c.get("tickers") or [])
            new_pairs = []
            for a, b in combinations(members, 2):
                if frozenset((a, b)) in prior_pairs:
                    continue  # already co-clustered as of the baseline
                if a not in corr_df.index or b not in corr_df.columns:
                    continue
                try:
                    corr_ab = float(corr_df.loc[a, b])
                except Exception:
                    continue
                if corr_ab != corr_ab:  # NaN
                    continue
                if corr_ab >= threshold:  # SIGNED, not abs()
                    new_pairs.append(sorted((a, b)))
            if new_pairs:
                flagged.append({**c, "new_pairs": new_pairs})

        return flagged
    except Exception:
        return []
