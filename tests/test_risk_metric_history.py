"""
stock_analyzer/risk_metric_history.py — Recommendation-Outcomes-Measurement
Phase 1a (docs/plans/recommendation-outcomes-measurement.md §10/§11).

Every failure/insufficient-data path must return None for the affected
field(s), NEVER a fabricated 0/neutral value — the exact bug class this
project has already been bitten by twice (feedback_sentinel_is_present /
feedback_overloaded_producer_state). Tests below assert `is None` explicitly,
never a falsy check, so a future regression to 0/50 cannot slip past a
`not x` assertion that a real 0 would also satisfy.
"""

import pandas as pd
import pytest

from stock_analyzer import risk_metric_history as rmh


def _hist(n, start=100.0, step=1.0):
    return {"df": pd.DataFrame({"Close": [start + step * i for i in range(n)]})}


def _held_data():
    # Two real-shaped, non-degenerate price histories so correlation_matrix /
    # diversification_score actually compute something (not just empty-guard
    # paths — a genuine happy path needs >=2 usable histories).
    return {
        "AAA": _hist(60, start=100.0, step=1.0),
        "BBB": _hist(60, start=50.0, step=-0.3),
    }


def _port_df():
    return pd.DataFrame({
        "Ticker": ["AAA", "BBB", "CCC"],
        "Sector": ["Tech", "Tech", "Healthcare"],
        "Gate Weight (%)": [20.0, 15.0, 10.0],
    })


# ── Full-data happy path ───────────────────────────────────────────────────

def test_happy_path_populates_every_field():
    port_df = _port_df()
    held = _held_data()
    port_risk = {"beta": 1.23}

    row = rmh.build_portfolio_risk_snapshot("2026-09-15", port_df, port_risk, held)

    assert row["snapshot_date"] == "2026-09-15"
    assert row["portfolio_beta"] == pytest.approx(1.23)
    assert row["top_sector"] == "Tech"
    assert row["top_sector_pct"] == pytest.approx(35.0)   # 20 + 15
    assert row["max_single_name_pct"] == pytest.approx(20.0)
    assert row["avg_pairwise_corr"] is not None
    assert row["diversification_score"] is not None
    assert row["corr_coverage_n"] is not None
    assert row["corr_coverage_n"] > 0


def test_snapshot_date_coerced_to_iso_date_string():
    import datetime
    row = rmh.build_portfolio_risk_snapshot(
        datetime.date(2026, 9, 15), pd.DataFrame(), None, {}
    )
    assert row["snapshot_date"] == "2026-09-15"


# ── Empty port_df -> sector/single-name fields None ────────────────────────

def test_empty_port_df_leaves_sector_and_single_name_fields_none():
    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", pd.DataFrame(), {"beta": 1.0}, _held_data()
    )
    assert row["top_sector"] is None
    assert row["top_sector_pct"] is None
    assert row["max_single_name_pct"] is None
    # Unrelated fields must still populate — one metric's failure never
    # blanks the others.
    assert row["portfolio_beta"] == pytest.approx(1.0)
    assert row["avg_pairwise_corr"] is not None


def test_port_df_missing_sector_column_leaves_sector_fields_none():
    port_df = pd.DataFrame({"Ticker": ["AAA"], "Gate Weight (%)": [20.0]})
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", port_df, None, {})
    assert row["top_sector"] is None
    assert row["top_sector_pct"] is None
    # Ticker/weight columns ARE present, so single-name should still compute.
    assert row["max_single_name_pct"] == pytest.approx(20.0)


def test_port_df_missing_ticker_column_leaves_single_name_field_none():
    port_df = pd.DataFrame({"Sector": ["Tech"], "Gate Weight (%)": [20.0]})
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", port_df, None, {})
    assert row["max_single_name_pct"] is None
    # Sector fields ARE resolvable independently.
    assert row["top_sector"] == "Tech"


def test_none_port_df_leaves_sector_and_single_name_fields_none():
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", None, None, {})
    assert row["top_sector"] is None
    assert row["top_sector_pct"] is None
    assert row["max_single_name_pct"] is None


def test_gate_weight_column_falls_back_to_weight_pct():
    port_df = pd.DataFrame({
        "Ticker": ["AAA", "BBB"],
        "Sector": ["Tech", "Energy"],
        "Weight (%)": [30.0, 10.0],
    })
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", port_df, None, {})
    assert row["top_sector"] == "Tech"
    assert row["top_sector_pct"] == pytest.approx(30.0)
    assert row["max_single_name_pct"] == pytest.approx(30.0)


# ── <2-ticker held_data -> corr fields None ────────────────────────────────

def test_single_ticker_held_data_leaves_corr_fields_none():
    held = {"AAA": _hist(60)}
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", _port_df(), {"beta": 1.0}, held)
    assert row["avg_pairwise_corr"] is None
    assert row["diversification_score"] is None
    assert row["corr_coverage_n"] is None
    # Unrelated fields must still populate.
    assert row["portfolio_beta"] == pytest.approx(1.0)
    assert row["top_sector"] == "Tech"


def test_empty_held_data_leaves_corr_fields_none():
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", _port_df(), None, {})
    assert row["avg_pairwise_corr"] is None
    assert row["diversification_score"] is None
    assert row["corr_coverage_n"] is None


def test_none_held_data_leaves_corr_fields_none():
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", _port_df(), None, None)
    assert row["avg_pairwise_corr"] is None
    assert row["diversification_score"] is None
    assert row["corr_coverage_n"] is None


# ── port_risk=None -> portfolio_beta None ───────────────────────────────────

def test_none_port_risk_leaves_beta_none():
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", _port_df(), None, _held_data())
    assert row["portfolio_beta"] is None
    # Unrelated fields still populate.
    assert row["top_sector"] == "Tech"
    assert row["avg_pairwise_corr"] is not None


def test_port_risk_with_none_beta_leaves_beta_none():
    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": None}, _held_data()
    )
    assert row["portfolio_beta"] is None


def test_port_risk_missing_beta_key_leaves_beta_none():
    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"some_other_field": 1.0}, _held_data()
    )
    assert row["portfolio_beta"] is None


# ── Never a fabricated 0/50 on a failure path ──────────────────────────────

def test_no_field_is_ever_a_fabricated_zero_on_full_failure():
    """Every producer input missing/None at once — every metric field must be
    an explicit None, never a fabricated 0 (beta) or 50 (diversification
    score, the exact 'measured, nothing notable' trap this codebase's
    util.factor_tilt_state class of bug guards against elsewhere)."""
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", pd.DataFrame(), None, {})
    for key in ("portfolio_beta", "top_sector", "top_sector_pct",
                "max_single_name_pct", "avg_pairwise_corr",
                "diversification_score", "corr_coverage_n"):
        assert row[key] is None, f"{key} must be None on full failure, got {row[key]!r}"


def test_returns_exactly_the_expected_columns():
    row = rmh.build_portfolio_risk_snapshot("2026-09-15", _port_df(), {"beta": 1.0}, _held_data())
    assert set(row.keys()) == {
        "snapshot_date", "portfolio_beta", "top_sector", "top_sector_pct",
        "max_single_name_pct", "avg_pairwise_corr", "diversification_score",
        "corr_coverage_n",
    }
