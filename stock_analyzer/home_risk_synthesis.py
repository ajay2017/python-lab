"""Home's correlation/diversification + risk-metrics producers — extracted
from app.py.

WHY THIS EXISTS (F-260 Phase 3, Unit A — docs/plans/surface-proprioception.md,
"Still unbuilt: Phase 3")
-------------------------------------------------------------------------------
`app.py` is 38k+ lines and no test imports it, so a conditional living there is
verifiable only by reading it or by screenshot. This block computed the
correlation matrix, the diversification score/label, and the diversification
ADD/REDUCE recommendations inline in Home's memo-miss branch, publishing all
six coordination caches (`_corr_df_cache`, `_div_score_cache`, `_avg_corr_cache`,
`_risk_pairs_cache`, `_div_label_cache`, `_corr_coverage_cache`) plus
`_div_recs_cache` straight from that scope. The offline-sentinel discipline was
already correct — a failure returns `None`/empty shapes, never a fabricated
value — but that correctness was only ever confirmed by inline reading. Lifting
it here, byte-identical, means the sentinel behaviour can finally be asserted
by a test instead of re-verified by eye every time the surrounding code shifts.

`app.py` calls `build_correlation_bundle()` and republishes the returned dict
into session_state; all decision logic — including the exact except-branch
sentinel shapes and the 42/30 diversification-label bands — lives here now,
unchanged from what it read the day this file was created.

One invariant worth calling out explicitly: `div_recs`'s failure sentinel is
`None`, NOT `[]` — deliberately, matching every sibling cache's "`None` means
offline, not means genuinely nothing" contract (see CLAUDE.md's coordination
cache registry). Do not "simplify" that to `[]`.

UNIT B — `build_risk_bundle()`
-------------------------------------------------------------------------------
Same motivation, same byte-identical-lift discipline, applied to the second
half of the same memo-miss branch: portfolio-level risk metrics (beta, Sharpe,
Sortino, VaR, CVaR, max drawdown), the fragility gauge, the high-beta cluster
share, and the Risk Advisor recommendation list. Four separate try/except
blocks, kept separate here exactly as they were inline — they fail
independently except for one REAL dependency the extraction must preserve:
fragility reads `_port_risk`'s beta, so a `port_risk` failure must cascade
into a `fragility` failure too, in the same call, in the same order.

The single most important invariant this extraction must never regress is the
2026-08-04 safety branch inside the risk-advisor block: when `port_risk` is
`None` (offline), the advisor is not merely discarded after being called — it
is **never called at all**. Calling it on a `None`/insufficient `port_risk`
can itself return a falsy `[]` (a real, distinguishable "checked, found
nothing"), which would then get cached as "checked, no risk" instead of the
honest "we never checked" `None` sentinel. The `if _port_risk is None:` guard
exists specifically to short-circuit before that call happens.
"""
from __future__ import annotations

import pandas as pd

from stock_analyzer.portfolio import (
    correlation_matrix,
    correlation_coverage,
    diversification_score,
    diversification_recommendations,
)
from stock_analyzer.risk import compute_portfolio_risk_metrics
from stock_analyzer.stress_test import SCENARIOS, run_scenario, assess_fragility
from stock_analyzer.concentration import high_beta_share
from stock_analyzer.risk_advisor import build_risk_advisor_recommendations


def _f(v, default=0.0):
    """Best-effort float coerce — returns `default` on None / non-numeric.

    Local mirror of the `_f` helper used at this call site in app.py (and in
    most `stock_analyzer` modules) rather than importing app.py's copy, which
    would create a backwards dependency from this pure module onto the UI.
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_correlation_bundle(port_df, held_data, portfolio_value) -> dict:
    """Correlation matrix + diversification score/label/recs for Home.

    Returns a dict with keys `corr_df`, `div`, `div_score`, `avg_corr`,
    `risk_pairs`, `div_label`, `corr_coverage`, `div_recs` — the caller
    republishes these into the matching `_*_cache` session_state keys.
    """
    try:
        corr_df      = correlation_matrix(held_data)
        # F-246: computed HERE, beside the matrix and from the same
        # held_data, then published/memoized alongside it. Computing it at
        # render time instead would describe a DIFFERENT held_data whenever
        # the synthesis memo serves a corr_df built on an earlier run (the
        # memo signature keys on holdings/date, not on price histories), so
        # a thin matrix whose histories have since recovered would render as
        # "sample is fine" — the exact inverse of what this figure is for.
        # Same producer-threaded shape F-230's `n_window_days` uses.
        _corr_cov    = correlation_coverage(held_data)
        _weights_map = dict(zip(port_df["Ticker"], port_df["Weight (%)"])) if not corr_df.empty else None
        div          = diversification_score(corr_df, _weights_map)
        div_score    = div["score"]
        avg_corr     = div["avg_correlation"]
        risk_pairs   = div["risk_pairs"]
        _div_label   = ("Well Diversified" if div_score >= 42
                        else "Moderate" if div_score >= 30 else "High Correlation Risk")
    except Exception:
        corr_df    = pd.DataFrame()
        div        = {"score": None, "avg_correlation": None, "risk_pairs": []}
        div_score  = avg_corr = None
        risk_pairs = []
        _div_label = "Unavailable"
        _corr_cov  = None    # offline sentinel — never a fabricated count

    try:
        div_recs = diversification_recommendations(port_df, corr_df, div, portfolio_value)
    except Exception:
        div_recs = None  # offline sentinel, not [] — matches sibling cache contract

    return {
        "corr_df": corr_df,
        "div": div,
        "div_score": div_score,
        "avg_corr": avg_corr,
        "risk_pairs": risk_pairs,
        "div_label": _div_label,
        "corr_coverage": _corr_cov,
        "div_recs": div_recs,
    }


def build_risk_bundle(
    port_df, held_data, h_rets, total_val, gate_denom, trades_df,
    spy_df, rfr, beta_elevated, beta_ceiling, fragility_pullback_pct,
) -> dict:
    """Portfolio risk metrics + fragility + high-beta share + Risk Advisor
    recs for Home.

    `spy_df`/`rfr` and the three policy constants (`beta_elevated`,
    `beta_ceiling`, `fragility_pullback_pct`) are taken as plain arguments —
    this module has no Streamlit/session_state access and no import surface
    onto `constants.py` of its own; the caller resolves those and controls
    the I/O, exactly as `build_correlation_bundle` takes `portfolio_value`
    as a plain argument rather than reaching into session_state.

    Returns a dict with keys `port_risk`, `fragility`, `highbeta_share`,
    `risk_advisor_recs`, `risk_high_alerts`, `h_rets` — the caller republishes
    the first five into the matching `_*_cache` session_state keys. `h_rets`
    is passed straight through (it is also a parameter) so the bundle stays
    the single source of truth for what this call computed, matching the
    `_home_synth_cache` memo bundle's own shape.

    Real dependency preserved across the first two try blocks: fragility
    reads `port_risk`'s beta, computed via THIS call, in THIS order — a
    `port_risk` failure must cascade into a `fragility` failure too.
    """
    # Portfolio-level risk metrics (Beta, Sharpe, Sortino, VaR, CVaR, Max Drawdown)
    try:
        _port_risk = compute_portfolio_risk_metrics(port_df, held_data, spy_df, rfr)
    except Exception:
        _port_risk = None  # offline sentinel, not {} — matches sibling cache contract

    # Fragility gauge — how a ROUTINE pullback would hit THIS book. Pre-emptive
    # exposure, NOT a forecast of when a pullback comes. Reuses the stress-test
    # "Mild Correction" engine + cached portfolio beta; severity reuses the
    # PORTFOLIO_BETA_ELEVATED / _CEILING policy bands. None on failure (not {})
    # so the render can show "offline" rather than fabricate a calm reading.
    try:
        _frag_beta = _port_risk.get("beta") if _port_risk else None
        if _frag_beta is not None and not port_df.empty:
            _mild_sc  = next((s for s in SCENARIOS if s["id"] == "mild_correction"), None)
            _mild_res = (
                run_scenario(_mild_sc, port_df, held_data, _frag_beta,
                             custom_spy_move=fragility_pullback_pct)
                if _mild_sc else {}
            )
            _fragility = assess_fragility(
                _mild_res, _frag_beta,
                beta_elevated, beta_ceiling, fragility_pullback_pct,
            )
        else:
            _fragility = None
    except Exception:
        _fragility = None

    # High-beta cluster share (Part 2b) — standing "correlated exposure" read:
    # what % of the book sits in high-beta (β ≥ PORTFOLIO_BETA_ELEVATED) names.
    # A cheap, honest proxy for the "ten tech names that all fall together"
    # risk that per-name diversification hides. Computed here where port_df +
    # per-name betas are both in scope; rendered under the fragility gauge.
    try:
        _hb_positions = [
            (_f(_r.get("Weight (%)")),
             (held_data.get(_r["Ticker"]) or {}).get("risk_metrics", {}).get("beta"))
            for _, _r in port_df.iterrows()
        ]
        _highbeta_share = high_beta_share(_hb_positions, beta_elevated)
    except Exception:
        _highbeta_share = None

    # Risk Advisor recommendations — generated from portfolio risk metrics.
    # Cache HIGH-priority alert titles so other pages (e.g. Watchlist) can gate
    # ENTER_NOW recommendations against active portfolio risk state. On a build
    # failure publish None (offline sentinel), NOT [] — an empty list reads as
    # "no active HIGH risks" and the Watchlist's risk-alert caution silently
    # vanishes (fail-open). None trips the Watchlist's existing offline banner so
    # the disabled gate is visible (house contract: producers fail to None).
    try:
        if _port_risk is None:
            # _port_risk offline (insufficient data or a build failure) —
            # propagate the offline sentinel rather than calling the
            # advisor, which would otherwise return [] on a falsy
            # port_risk and get cached as a false "checked, no risk"
            # (2026-08-04 audit finding).
            _risk_advisor_recs = None
            _risk_high_alerts = None
        else:
            _risk_advisor_recs = build_risk_advisor_recommendations(
                port_df, held_data, _port_risk, h_rets, total_val,
                gate_denom=gate_denom,
                trades_df=trades_df,
            )
            # build_risk_advisor_recommendations can itself return the
            # offline sentinel (empty port_df / invalid portfolio_value,
            # separate from the _port_risk-is-None case handled above) —
            # must check before iterating, or a None result crashes here.
            if _risk_advisor_recs is None:
                _risk_high_alerts = None
            else:
                _risk_high_alerts = [
                    r.get("title", "") for r in _risk_advisor_recs if r.get("priority") == "HIGH"
                ]
    except Exception:
        _risk_advisor_recs = None
        _risk_high_alerts = None

    return {
        "port_risk": _port_risk,
        "fragility": _fragility,
        "highbeta_share": _highbeta_share,
        "risk_advisor_recs": _risk_advisor_recs,
        "risk_high_alerts": _risk_high_alerts,
        "h_rets": h_rets,
    }
