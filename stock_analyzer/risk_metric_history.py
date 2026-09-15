"""
Portfolio risk-metric history — Recommendation-Outcomes-Measurement Phase 1a
(docs/plans/recommendation-outcomes-measurement.md §10/§11).

Pure, no I/O. `build_portfolio_risk_snapshot` turns the EOD cron's
already-computed `port_df` / `port_risk` / `held_data` into one
`portfolio_risk_snapshots`-shaped row (see the DDL comment block at the top of
db.py). Answers the owner's own live question — "is beta actually coming down
over time" — as a chart, independent of attributing any change to a specific
recommendation (that's a later, separate phase; see the plan's Phase 1b/2).

NULL-preserving contract (this project has been bitten by the
fabricated-neutral bug class twice — see feedback_sentinel_is_present /
feedback_overloaded_producer_state): every metric field is None when its
input is unavailable, NEVER a fabricated 0/neutral value. Each metric block is
wrapped in its own try/except so one failure can never blank the others.
"""

from __future__ import annotations


def _gate_weight_col(port_df) -> str:
    """Same column-resolution convention used across the codebase (e.g.
    headless_alert_engine.py:809, app.py, risk_advisor.py-adjacent call
    sites) — "Gate Weight (%)" is the equity-basis weight when present,
    "Weight (%)" otherwise."""
    return "Gate Weight (%)" if "Gate Weight (%)" in port_df.columns else "Weight (%)"


def _sector_concentration(port_df) -> tuple[str | None, float | None]:
    """(top_sector, top_sector_pct) — the sector with the largest summed
    gate-weight, or (None, None) when port_df is empty or lacks the needed
    columns."""
    if port_df is None or port_df.empty or "Sector" not in port_df.columns:
        return None, None
    gcol = _gate_weight_col(port_df)
    if gcol not in port_df.columns:
        return None, None
    sector_sums = port_df.groupby("Sector")[gcol].sum()
    if sector_sums.empty:
        return None, None
    top_sector = sector_sums.idxmax()
    return str(top_sector), float(sector_sums.max())


def _max_single_name_pct(port_df) -> float | None:
    """Largest single position's % of gate-weight basis, or None when
    port_df is empty or lacks the needed columns."""
    if port_df is None or port_df.empty or "Ticker" not in port_df.columns:
        return None
    gcol = _gate_weight_col(port_df)
    if gcol not in port_df.columns:
        return None
    by_ticker = port_df.groupby("Ticker")[gcol].sum()
    if by_ticker.empty:
        return None
    return float(by_ticker.max())


def build_portfolio_risk_snapshot(snapshot_date, port_df, port_risk, held_data) -> dict:
    """One day's portfolio risk-metric snapshot — pure, no I/O.

    `snapshot_date` is coerced to an ISO date string (matching how
    save_account_daily_snapshot's caller formats its date). `port_df` is the
    EOD cron's already-built portfolio frame (headless_alert_engine.compute_eod's
    "port_df" key). `port_risk` is compute_portfolio_risk_metrics's return dict
    (or None if that computation itself failed upstream — treated the same as
    "beta unavailable", never as beta=0). `held_data` is the same bundle map
    the EOD cron already loaded — reused here for correlation_matrix /
    correlation_coverage, never a second fetch.

    Every field is None on its own failure/insufficient-data path — each
    metric block is independently try/excepted so one metric's failure never
    blanks the others.
    """
    row: dict = {
        "snapshot_date":         str(snapshot_date)[:10],
        "portfolio_beta":        None,
        "top_sector":            None,
        "top_sector_pct":        None,
        "max_single_name_pct":   None,
        "avg_pairwise_corr":     None,
        "diversification_score": None,
        "corr_coverage_n":       None,
    }

    try:
        row["portfolio_beta"] = float(port_risk.get("beta")) if port_risk and port_risk.get("beta") is not None else None
    except Exception:
        row["portfolio_beta"] = None

    try:
        top_sector, top_sector_pct = _sector_concentration(port_df)
        row["top_sector"] = top_sector
        row["top_sector_pct"] = top_sector_pct
    except Exception:
        row["top_sector"] = None
        row["top_sector_pct"] = None

    try:
        row["max_single_name_pct"] = _max_single_name_pct(port_df)
    except Exception:
        row["max_single_name_pct"] = None

    try:
        # Equal-weight simplification — diversification_score's `weights` arg
        # is deliberately omitted (None), matching this feature's
        # awareness-only scope. A weighted version would need the same
        # gate-weight resolution as the sector/single-name blocks above; not
        # done here so a failure in that mapping can never blank the corr
        # metrics too. Noted in the DDL comment and the Account-page caption
        # as a known simplification, not silently presented as weighted.
        from . import portfolio as _portfolio
        corr_df = _portfolio.correlation_matrix(held_data or {})
        div = _portfolio.diversification_score(corr_df)
        row["avg_pairwise_corr"] = div.get("avg_correlation")
        row["diversification_score"] = div.get("score")
    except Exception:
        row["avg_pairwise_corr"] = None
        row["diversification_score"] = None

    try:
        from . import portfolio as _portfolio
        coverage = _portfolio.correlation_coverage(held_data or {})
        row["corr_coverage_n"] = coverage.get("n_obs") if coverage else None
    except Exception:
        row["corr_coverage_n"] = None

    return row
