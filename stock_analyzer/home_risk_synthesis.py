"""Home's correlation/diversification producer — extracted from app.py.

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
"""
from __future__ import annotations

import pandas as pd

from stock_analyzer.portfolio import (
    correlation_matrix,
    correlation_coverage,
    diversification_score,
    diversification_recommendations,
)


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
