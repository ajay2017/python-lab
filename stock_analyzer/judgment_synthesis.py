"""
Judgment-layer synthesis — Phase 1 ("The Judge", read-only, no authority).

Reconciles a list of opinion dicts (stock_analyzer.judgment_opinion.build_opinion)
into one decomposable posture per ticker plus a portfolio-wide posture. Pure
logic, no I/O, no Streamlit imports — see docs/plans/judgment-layer.md for the
full design and Opus review history.

Equal-weight era (Phase 1-2): every opinion is weighted by its own `confidence`
only, never by a source's historical track record (that's Phase 3 — no track
record exists yet to weight by). This module changes NOTHING: it renders a read
for the user to compare against the Daily Brief, and has no override/gating
authority. It does not decide; it only reconciles what witnesses already said.

Routing (three outcomes per ticker, never a plain average of everything):
  1. PROTECTIVE VETO — the MOST SEVERE protective-dimension opinion
     (position_health, concentration, structural_risk, leverage) at/below
     JUDGMENT_VETO_PROTECTIVE_THRESHOLD hard-suppresses EVERY same-ticker
     positive acquisitive-dimension opinion (quality, momentum) at once — not
     just the first one found. All suppressed opinions are shown as dissent,
     never blended in.
  2. CONTRADICTION AUDIT — two different sources on the SAME dimension with
     opposite-sign signals, both at/above JUDGMENT_CONTRADICTION_MIN_MAGNITUDE,
     are flagged (not silently averaged away).
  3. WEIGHTING — everything else blends via a confidence-weighted average.

Advisory opinions (build_opinion(advisory=True), e.g. verdict_reconciliation —
a META-witness that already synthesizes momentum+composite+news+earnings into
one verdict, not a peer vote on `quality`) are kept attached to their ticker
for DISPLAY, but excluded from the veto/blend/contradiction machinery above —
otherwise a meta-witness would double-count its own inputs and get flagged as
"contradicting" the very witness it already reconciled (caught in the
2026-08-03 Opus review of this module).
"""

from __future__ import annotations

from collections import defaultdict

from stock_analyzer.constants import (
    JUDGMENT_VETO_PROTECTIVE_THRESHOLD,
    JUDGMENT_CONTRADICTION_MIN_MAGNITUDE,
)
from stock_analyzer.judgment_opinion import (
    PROTECTIVE_DIMENSIONS,
    is_protective,
    is_acquisitive,
)

_PORTFOLIO_KEY = None  # ticker=None on an in-memory opinion means portfolio-wide


def _partition(ops: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    protective, acquisitive, other = [], [], []
    for o in ops:
        d = o["dimension"]
        if is_protective(d):
            protective.append(o)
        elif is_acquisitive(d):
            acquisitive.append(o)
        else:
            other.append(o)
    return protective, acquisitive, other


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _find_contradictions(ops: list[dict]) -> list[dict]:
    """Same dimension, different sources, opposite sign, both above the
    minimum-magnitude floor (so near-neutral noise never counts as a conflict).
    Callers must pass only non-advisory opinions — see synthesize()."""
    by_dim: dict[str, list[dict]] = defaultdict(list)
    for o in ops:
        by_dim[o["dimension"]].append(o)
    contradictions = []
    for dim, dim_ops in by_dim.items():
        if len(dim_ops) < 2:
            continue
        for i in range(len(dim_ops)):
            for j in range(i + 1, len(dim_ops)):
                a, b = dim_ops[i], dim_ops[j]
                if a["source"] == b["source"]:
                    continue
                if (abs(a["signal"]) >= JUDGMENT_CONTRADICTION_MIN_MAGNITUDE
                        and abs(b["signal"]) >= JUDGMENT_CONTRADICTION_MIN_MAGNITUDE
                        and _sign(a["signal"]) * _sign(b["signal"]) < 0):
                    contradictions.append({"dimension": dim, "opinions": [a, b]})
    return contradictions


def _confidence_weighted_average(ops: list[dict]) -> float:
    if not ops:
        return 0.0
    total_weight = sum(o["confidence"] for o in ops)
    if total_weight <= 0:
        return 0.0
    return sum(o["signal"] * o["confidence"] for o in ops) / total_weight


def _synthesize_group(ops: list[dict]) -> dict:
    """Reconcile one ticker's (or the portfolio's) opinions into one posture.
    Shared by both grains — the routing logic doesn't care whether it's a real
    ticker or the portfolio-wide bucket.

    `ops` may include advisory opinions (kept for display in the returned
    "opinions" list) — they are excluded from `scored_ops`, which is all the
    veto/blend/contradiction machinery below ever sees.
    """
    scored_ops = [o for o in ops if not o.get("advisory")]
    protective, acquisitive, _ = _partition(scored_ops)

    veto = None
    protective_candidates = [p for p in protective if p["signal"] <= JUDGMENT_VETO_PROTECTIVE_THRESHOLD]
    positive_acquisitive = [a for a in acquisitive if a["signal"] > 0]
    if protective_candidates and positive_acquisitive:
        # Most severe (lowest signal) protective opinion wins — not simply the
        # first one found — and it suppresses EVERY positive acquisitive
        # opinion on this ticker at once, not just one.
        _most_severe = min(protective_candidates, key=lambda p: p["signal"])
        veto = {"protective": _most_severe, "suppressed": positive_acquisitive}

    contradictions = _find_contradictions(scored_ops)

    if veto:
        posture_signal = veto["protective"]["signal"]
        posture_source = "veto"
        # Every opinion in veto["suppressed"] is excluded from the blend by
        # design — shown (via `veto`) but never averaged in. NOTE: other
        # scored_ops on this ticker (additional protective/"other"-dimension
        # opinions beyond the one that won) are currently also NOT folded in
        # once a veto fires — posture_signal is the winning protective
        # opinion's own value alone. Harmless in Phase 1 (posture_signal is
        # computed but not rendered anywhere yet) but must be revisited before
        # any Phase 3/4 consumer reads it, so a third opinion isn't silently
        # dropped from an authoritative call.
    else:
        posture_signal = _confidence_weighted_average(scored_ops)
        posture_source = "blend"

    return {
        "opinions": ops,
        "veto": veto,
        "contradictions": contradictions,
        "posture_signal": posture_signal,
        "posture_source": posture_source,
    }


def _build_overall_line(ticker_results: dict, portfolio_result: dict) -> str:
    vetoed_tickers = sorted(t for t, r in ticker_results.items() if r["veto"])
    n_contradictions = (
        sum(len(r["contradictions"]) for r in ticker_results.values())
        + len(portfolio_result["contradictions"])
    )
    if vetoed_tickers:
        return (
            f"{len(vetoed_tickers)} ticker(s) under a protective override: "
            f"{', '.join(vetoed_tickers)}."
        )
    if n_contradictions:
        return (
            f"{n_contradictions} same-dimension contradiction(s) detected — "
            f"see decomposition below."
        )
    all_dims = {o["dimension"] for r in ticker_results.values() for o in r["opinions"]}
    all_dims |= {o["dimension"] for o in portfolio_result["opinions"]}
    return (
        f"No contradictions or protective overrides detected among today's "
        f"{len(all_dims)} wired dimension(s)."
    )


def synthesize(opinions: list[dict]) -> dict:
    """Reconcile today's in-memory opinions into a decomposable read.

    `opinions` is the raw list built by judgment_opinion.build_opinion() across
    today's witness call sites (ticker=None for portfolio-wide opinions — NOT
    yet coerced to the '_PORTFOLIO' storage sentinel, that coercion only
    happens at db.py write time). Returns a dict a Streamlit page can render
    directly without further business logic — see the "🧑‍⚖️ The Judge" page.

    Advisory opinions stay attached to their ticker's "opinions" list (so the
    decomposition still shows them as context) but are excluded from veto,
    blend, and contradiction-audit inside _synthesize_group, and excluded from
    the reduced-visibility scan below.
    """
    by_ticker: dict = defaultdict(list)
    for o in opinions:
        by_ticker[o.get("ticker")].append(o)

    portfolio_ops = by_ticker.pop(_PORTFOLIO_KEY, [])
    ticker_results = {ticker: _synthesize_group(ops) for ticker, ops in by_ticker.items()}
    portfolio_result = _synthesize_group(portfolio_ops)

    live_protective_dims_seen = {
        o["dimension"] for o in opinions
        if not o.get("advisory") and o.get("is_live", True) and is_protective(o["dimension"])
    }
    reduced_visibility_dims = sorted(PROTECTIVE_DIMENSIONS - live_protective_dims_seen)

    return {
        "overall_line": _build_overall_line(ticker_results, portfolio_result),
        "tickers": ticker_results,
        "portfolio": portfolio_result,
        "reduced_visibility_dims": reduced_visibility_dims,
        "n_opinions": len(opinions),
        "n_sources": len({o["source"] for o in opinions}),
    }
