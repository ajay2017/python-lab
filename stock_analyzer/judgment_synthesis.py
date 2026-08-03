"""
Judgment-layer synthesis — Phase 1 ("The Judge", read-only, no authority).

Reconciles a list of opinion dicts (stock_analyzer.judgment_opinion.build_opinion)
into one decomposable posture per ticker plus a portfolio-wide posture. Pure
logic, no I/O, no Streamlit imports — see docs/plans/judgment-layer.md for the
full design and Opus review history.

Evidence-based weighting (Phase 3): the blend below is a *confidence-weighted*
average, and each opinion's confidence is further scaled by a track-record
weight multiplier once its (source, dimension) pair clears the min-sample gate
(track_record_summary(), same BEHAVIORAL_MIN_SAMPLE_N floor Phase 2 uses) — see
_weight_multiplier(). Until then, or when no track record is supplied at all
(track_record=None, the Phase 1/2 behavior), every multiplier is 1.0 and the
blend is identical to plain confidence-weighting. The multiplier is ONLY ever
applied inside the blend — the protective veto and the contradiction-audit
magnitude floor stay hard gates, never softened by a source's track record
(that would silently re-litigate an existing hard suppression into a weighted
vote, the exact structural hole the Q1 design review caught for veto-vs-
acquisitive routing). synthesize() itself still changes NOTHING to any
recommendation: it renders a read for the user to compare against the Daily
Brief.

Coherence audit (Phase 4, `audit_coherence()`): the one piece of real
"authority" granted so far — but authority to AUDIT, never to gate. A
research pass before building Phase 4 found that 3 of the 4 protective
dimensions are either already enforced elsewhere (position_health via
`_reduce_calls`, concentration via the entry-gate pipeline) or explicitly
documented as "never gates" (leverage, per CLAUDE.md's coordination pattern)
— so a literal "Judge gets veto authority" would either duplicate existing
enforcement (a drift risk) or silently reverse a deliberate house policy.
The user chose the audit-only scope instead: `audit_coherence()` cross-checks
every ticker under an active Judge veto against `_reduce_calls` (the app's
one other already-published cross-feature risk surface) and reports which
vetoed tickers are already "covered" by that mechanism vs "uncovered" — a
genuine coherence gap where the Judge is the only thing flagging risk on
that name. This operationalizes the design's own "Job 3: audit for
cross-feature contradiction systematically" — it never suppresses or
modifies any recommendation, purely surfaces a finding loudly for the user
to act on.

Routing (three outcomes per ticker, never a plain average of everything):
  1. PROTECTIVE VETO — the MOST SEVERE protective-dimension opinion
     (position_health, concentration, structural_risk, leverage) at/below
     JUDGMENT_VETO_PROTECTIVE_THRESHOLD hard-suppresses EVERY same-ticker
     positive acquisitive-dimension opinion (quality, momentum) at once — not
     just the first one found. All suppressed opinions are shown as dissent,
     never blended in. Track record plays no part in this decision.
  2. CONTRADICTION AUDIT — two different sources on the SAME dimension with
     opposite-sign signals, both at/above JUDGMENT_CONTRADICTION_MIN_MAGNITUDE,
     are flagged (not silently averaged away). Track record plays no part here
     either — a contradiction is a magnitude/sign fact, not a vote.
  3. WEIGHTING — everything else blends via a track-record-weighted average
     (Phase 3) — equal-weight confidence average until a witness earns history.

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
    JUDGMENT_TRACK_RECORD_NEUTRAL_ACCURACY,
    JUDGMENT_TRACK_RECORD_WEIGHT_FLOOR,
    JUDGMENT_TRACK_RECORD_WEIGHT_CEILING,
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


def _weight_multiplier(o: dict, track_record: dict | None) -> float:
    """Track-record weight multiplier for one opinion's (source, dimension)
    pair, applied on top of its own `confidence` inside the blend only.

    Returns 1.0 (neutral, identical to pre-Phase-3 behavior) when no track
    record was supplied, the pair has no track record yet, or it hasn't
    cleared the min-sample gate (`track_record_summary`'s `sufficient_sample`)
    — a thin sample is noise, not evidence, so it must not move weight.
    Otherwise scales linearly off 50%-accuracy-is-neutral (a coin-flip witness
    is worth exactly what it always was) and clamps to the user-set
    [JUDGMENT_TRACK_RECORD_WEIGHT_FLOOR, …_CEILING] band so one witness can
    never be fully silenced or allowed to dominate the blend alone.
    """
    if not track_record:
        return 1.0
    tr = track_record.get((o["source"], o["dimension"]))
    if not tr or not tr.get("sufficient_sample"):
        return 1.0
    if JUDGMENT_TRACK_RECORD_NEUTRAL_ACCURACY <= 0:
        return 1.0  # guards a future constants.py misconfiguration, not a real code path today
    raw = tr["accuracy"] / JUDGMENT_TRACK_RECORD_NEUTRAL_ACCURACY
    return max(JUDGMENT_TRACK_RECORD_WEIGHT_FLOOR, min(JUDGMENT_TRACK_RECORD_WEIGHT_CEILING, raw))


def _confidence_weighted_average(ops: list[dict], track_record: dict | None = None) -> float:
    if not ops:
        return 0.0
    weights = [o["confidence"] * _weight_multiplier(o, track_record) for o in ops]
    total_weight = sum(weights)
    if total_weight <= 0:
        return 0.0
    return sum(o["signal"] * w for o, w in zip(ops, weights)) / total_weight


def _synthesize_group(ops: list[dict], track_record: dict | None = None) -> dict:
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
    # Keyed by natural identity (source, dimension, ticker) rather than id() —
    # the returned "opinions" list below is now a set of shallow copies (each
    # carries its own weight_multiplier annotation), so identity comparison
    # against the original veto["suppressed"] objects would silently break.
    # INVARIANT: this assumes at most one opinion per (source, dimension,
    # ticker) within a group — true for every witness wired today. If a future
    # witness ever emits two opinions on the same dimension+ticker in one run,
    # a sibling opinion sharing that key would be mislabeled "suppressed" too
    # (display-only — posture_signal is unaffected either way). Reviewed and
    # accepted 2026-08-03 Opus review of Judge Phase 3; dedupe upstream in
    # build_opinion() call sites if this ever stops holding.
    _suppressed_keys = {
        (s["source"], s["dimension"], s.get("ticker")) for s in (veto["suppressed"] if veto else [])
    }

    if veto:
        posture_signal = veto["protective"]["signal"]
        posture_source = "veto"
        # Every opinion in veto["suppressed"] is excluded from the blend by
        # design — shown (via `veto`) but never averaged in. NOTE: other
        # scored_ops on this ticker (additional protective/"other"-dimension
        # opinions beyond the one that won) are currently also NOT folded in
        # once a veto fires — posture_signal is the winning protective
        # opinion's own value alone. Harmless today (posture_signal is
        # computed but not rendered anywhere yet) but must be revisited before
        # any Phase 4 consumer reads it, so a third opinion isn't silently
        # dropped from an authoritative call.
    else:
        posture_signal = _confidence_weighted_average(scored_ops, track_record)
        posture_source = "blend"

    # Annotate each returned opinion with its blend weight_multiplier (None
    # for advisory opinions — never weighted — and for every opinion when a
    # veto fired, since the blend didn't run this ticker) and whether it was
    # veto-suppressed, so the UI can show both without touching this module's
    # internals or relying on object identity.
    annotated_ops = []
    for o in ops:
        d = dict(o)
        if o.get("advisory") or veto:
            d["weight_multiplier"] = None
        else:
            d["weight_multiplier"] = _weight_multiplier(o, track_record)
        d["suppressed"] = (o["source"], o["dimension"], o.get("ticker")) in _suppressed_keys
        annotated_ops.append(d)

    return {
        "opinions": annotated_ops,
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


def synthesize(opinions: list[dict], track_record: dict | None = None) -> dict:
    """Reconcile today's in-memory opinions into a decomposable read.

    `opinions` is the raw list built by judgment_opinion.build_opinion() across
    today's witness call sites (ticker=None for portfolio-wide opinions — NOT
    yet coerced to the '_PORTFOLIO' storage sentinel, that coercion only
    happens at db.py write time). Returns a dict a Streamlit page can render
    directly without further business logic — see the "🧑‍⚖️ The Judge" page.

    `track_record` (Phase 3, optional) is a `{(source, dimension): row}` map
    built from judgment_grading.track_record_summary() — each row needs
    `accuracy` and `sufficient_sample`. Omit it (or pass None/{}) to get the
    Phase 1/2 equal-weight behavior exactly. It only ever influences the blend
    inside _synthesize_group — never the veto or contradiction-audit routing.

    Advisory opinions stay attached to their ticker's "opinions" list (so the
    decomposition still shows them as context) but are excluded from veto,
    blend, and contradiction-audit inside _synthesize_group, and excluded from
    the reduced-visibility scan below.
    """
    by_ticker: dict = defaultdict(list)
    for o in opinions:
        by_ticker[o.get("ticker")].append(o)

    portfolio_ops = by_ticker.pop(_PORTFOLIO_KEY, [])
    ticker_results = {
        ticker: _synthesize_group(ops, track_record) for ticker, ops in by_ticker.items()
    }
    portfolio_result = _synthesize_group(portfolio_ops, track_record)

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


def audit_coherence(judge_result: dict, reduce_call_tickers: set) -> dict:
    """Phase 4 — the Judge's one piece of real authority: an AUDIT, never a
    new gate. Cross-checks every ticker under an active protective veto (from
    `synthesize()`'s output) against `_reduce_calls` — the app's one other
    already-published cross-feature risk surface (`app.py` publishes it every
    Home render; see CLAUDE.md's coordination pattern) — to find coherence
    gaps: a ticker the Judge independently flags as at-risk with no active
    reduce call. **Scope note:** this checks ONLY `_reduce_calls`, not every
    enforcement mechanism in the app (concentration's own entry-gate and
    leverage's documented never-gates policy sit outside it) — "uncovered"
    means "not covered by `_reduce_calls` specifically," not "nothing else in
    the app is watching this ticker." Today every dimension that can actually
    fire this veto (`position_health` — the only per-ticker protective
    dimension below the veto threshold; `concentration`/`structural_risk` are
    portfolio-scoped and cannot veto a ticker) is sourced from the exact same
    act-kinds `_reduce_calls` itself buckets from, so a fireable veto is
    always "covered" today and "uncovered" is genuine future-proofing, not a
    live false-positive. Re-check this scope note if a future phase wires a
    NEW per-ticker protective witness that doesn't also feed `_reduce_calls`.

    Why audit rather than gate: a pre-build research pass found 3 of the 4
    protective dimensions are either already enforced elsewhere
    (`position_health` via `_reduce_calls` itself across 5+ surfaces;
    `concentration` via the entry-gate pipeline's own `SINGLE_NAME_CEILING`/
    `SECTOR_CEILING` checks, fully decoupled from the Judge) or explicitly
    documented as never gating (`leverage`, per CLAUDE.md's coordination
    pattern — `_leverage_cache` is "read-only, never gates" by deliberate
    house policy). A literal veto-enforces-everything Phase 4 would therefore
    either duplicate mature, more nuanced existing enforcement (a drift risk
    the app's own single-surface-priority principle warns against) or
    silently reverse a standing policy decision — so the user chose this
    audit-only scope instead. It operationalizes the design's own "Job 3:
    audit for cross-feature contradiction systematically."

    Returns `{"covered": [...], "uncovered": [...]}`:
      - "covered" — a Judge veto exists AND the ticker has an active reduce
        call: validating (the Judge agrees with what's already flagged).
      - "uncovered" — a Judge veto exists with NO active reduce call: a real
        coherence gap the Judge alone caught this run.
    Each finding: {ticker, source, dimension, signal, evidence} from the
    veto's winning protective opinion. Includes the portfolio-wide bucket
    under the sentinel ticker key "_PORTFOLIO_WIDE" (a portfolio veto cannot
    currently fire — no portfolio-wide acquisitive opinion is emitted yet —
    so this is future-proofing, not dead code invoked today).

    Pure — no I/O, no Streamlit. Never suppresses or modifies any
    recommendation; purely reports a finding for the page to render loudly.
    """
    groups = dict(judge_result["tickers"])
    groups["_PORTFOLIO_WIDE"] = judge_result["portfolio"]

    covered, uncovered = [], []
    for ticker, result in groups.items():
        veto = result.get("veto")
        if not veto:
            continue
        protective = veto["protective"]
        finding = {
            "ticker": ticker,
            "source": protective["source"],
            "dimension": protective["dimension"],
            "signal": protective["signal"],
            "evidence": protective["evidence"],
        }
        (covered if ticker in reduce_call_tickers else uncovered).append(finding)
    return {"covered": covered, "uncovered": uncovered}
