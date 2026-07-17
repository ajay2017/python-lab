"""
Portfolio Intelligence — Concept B ("Portfolio-as-One Positioning Intelligence").

Holds the panels for the 🧩 Portfolio Intelligence page: a unified view of what
the portfolio's ownership MEANS in aggregate (risk budget, factor tilt,
correlation clustering, regime fit) rather than position-by-position. This
module will grow across future tasks — each panel gets its own public
function here.

Awareness-only: nothing in this module gates, resizes, or reorders a
recommendation. The composite score remains the sole ranker everywhere else
in the app; these functions only add a display-level lens on top of data the
app already computes.

Panel 1 — correlation clustering (this task): the app already flags
correlated PAIRS above CORR_HIGH_PAIRS_THRESHOLD / CORR_DANGER_PAIRS_THRESHOLD
(see stock_analyzer.portfolio.diversification_score). This module groups
those pairwise flags TRANSITIVELY via connected components over a simple
adjacency graph — plain Python graph traversal, no new data source and no
new library dependency.
"""

import numpy as np
import pandas as pd

from stock_analyzer.constants import CORR_HIGH_PAIRS_THRESHOLD, CORR_DANGER_PAIRS_THRESHOLD


def correlation_clusters(
    corr_df: pd.DataFrame,
    weights: dict | None = None,
    threshold: float = CORR_HIGH_PAIRS_THRESHOLD,
    danger_threshold: float = CORR_DANGER_PAIRS_THRESHOLD,
) -> list[dict]:
    """
    Group tickers into transitive correlation clusters.

    A cluster is a maximal set of tickers connected via a chain of
    >=threshold pairwise correlations (A-B and B-C connected implies A, B, C
    are one cluster even when A-C isn't itself flagged). Singletons (no
    correlated pair) are excluded — only clusters of 2+ members are returned.

    corr_df: square DataFrame, index == columns == tickers (from
             portfolio.correlation_matrix()).
    weights: optional {ticker: weight_pct} for combined_weight_pct; treated
             as all-zero when None.

    Returns a list of dicts, each:
        {
            "tickers": [sorted tickers],
            "size": int,
            "avg_internal_corr": float (2dp),
            "combined_weight_pct": float (1dp),
            "tier": "danger" | "warning",
        }
    Sorted by combined_weight_pct descending (or size descending when
    weights is None), avg_internal_corr descending as tiebreak. Never
    raises — returns [] on any unusable input.
    """
    try:
        if corr_df is None or corr_df.empty or len(corr_df) < 2:
            return []

        tickers = corr_df.index.tolist()

        # ── Build adjacency (mirrors diversification_score's iteration) ────
        adjacency: dict[str, set[str]] = {t: set() for t in tickers}
        for i, t1 in enumerate(tickers):
            for j, t2 in enumerate(tickers):
                if i >= j:
                    continue
                if t1 not in corr_df.columns or t2 not in corr_df.columns:
                    continue
                c = float(corr_df.loc[t1, t2])
                if np.isnan(c):
                    continue
                if c >= threshold:
                    adjacency[t1].add(t2)
                    adjacency[t2].add(t1)

        # ── Connected components via BFS ────────────────────────────────────
        visited: set[str] = set()
        components: list[list[str]] = []
        for t in tickers:
            if t in visited or not adjacency[t]:
                continue
            comp: list[str] = []
            queue = [t]
            visited.add(t)
            while queue:
                cur = queue.pop()
                comp.append(cur)
                for nbr in adjacency[cur]:
                    if nbr not in visited:
                        visited.add(nbr)
                        queue.append(nbr)
            if len(comp) >= 2:
                components.append(comp)

        if not components:
            return []

        has_weights = weights is not None

        clusters: list[dict] = []
        for comp in components:
            comp_sorted = sorted(comp)
            pair_corrs: list[float] = []
            is_danger = False
            for i, t1 in enumerate(comp_sorted):
                for t2 in comp_sorted[i + 1:]:
                    try:
                        c = float(corr_df.loc[t1, t2])
                    except Exception:
                        continue
                    if np.isnan(c):
                        continue
                    pair_corrs.append(c)
                    if c >= danger_threshold:
                        is_danger = True

            avg_internal = round(float(np.mean(pair_corrs)), 2) if pair_corrs else 0.0
            combined_weight = round(
                sum(float((weights or {}).get(t, 0.0)) for t in comp_sorted), 1
            )

            clusters.append({
                "tickers": comp_sorted,
                "size": len(comp_sorted),
                "avg_internal_corr": avg_internal,
                "combined_weight_pct": combined_weight,
                "tier": "danger" if is_danger else "warning",
            })

        if has_weights:
            clusters.sort(key=lambda c: (-c["combined_weight_pct"], -c["avg_internal_corr"]))
        else:
            clusters.sort(key=lambda c: (-c["size"], -c["avg_internal_corr"]))

        return clusters
    except Exception:
        return []
