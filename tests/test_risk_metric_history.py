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

    # held_tickers must be supplied (and match held_data's keys) for the
    # correlation reading to be VERIFIED and therefore persisted — see
    # portfolio.correlation_claim_verified ("screens disclose, records
    # withhold"). Omitting it is covered separately below.
    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", port_df, port_risk, held, held_tickers=["AAA", "BBB"],
    )

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
        "2026-09-15", pd.DataFrame(), {"beta": 1.0}, _held_data(),
        held_tickers=["AAA", "BBB"],
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
    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), None, _held_data(), held_tickers=["AAA", "BBB"],
    )
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


# ── held_tickers / correlation-claim verification (2026-10-09) ────────────
#
# "screens disclose, records withhold" — portfolio.correlation_claim_verified
# is now consulted before a correlation reading is allowed to persist. Each
# test below varies EXACTLY ONE input relative to a verified baseline and
# asserts the control's REAL value (never just "differs from the other
# case") — this project has shipped vacuous tests three commits running,
# most recently one where deleting a type guard left all seven cases green.

def _ddl_columns_from_db_docstring() -> set:
    """The portfolio_risk_snapshots column set, PARSED from db.py's own DDL.

    Opus review 2026-10-09: a hand-written literal here duplicated the
    existing column test and would NOT fail if a column were added to the
    DDL. That matters more than it sounds -- `db.save_portfolio_risk_snapshot`
    upserts the whole row dict, so a DDL column the builder doesn't produce
    (or a builder key the DDL lacks) makes PostgREST reject the write, losing
    the entire day's snapshot and reddening the cron heartbeat. Parsing the
    real DDL means the two can't drift apart silently.
    """
    import re
    from stock_analyzer import db as _db
    doc = _db.__doc__ or ""
    m = re.search(
        r"create table if not exists public\.portfolio_risk_snapshots\s*\((.*?)\n\s*\);",
        doc, re.DOTALL,
    )
    assert m, "could not locate the portfolio_risk_snapshots DDL in db.py's docstring"
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        name = line.split()[0]
        if name and not name.startswith("-"):
            cols.add(name)
    cols.discard("created_at")   # DB-side default, never built in Python
    assert len(cols) >= 5, f"DDL parse looks wrong, got {cols}"
    return cols


_EXPECTED_ROW_KEYS = _ddl_columns_from_db_docstring()


def test_h1_held_ticker_missing_from_held_data_withholds_correlation_only():
    """A held ticker (CCC) never entered held_data at all (its bundle load
    failed upstream) -- correlation_unchecked(corr_df, held_tickers) must
    report it missing, so the reading is withheld. corr_coverage_n stays
    populated (a true fact about held_data's own inputs); beta/top_sector
    are computed from port_df/port_risk, untouched by the corr gate --
    proving per-metric isolation, not just "two results differ"."""
    port_df = _port_df()   # Ticker column includes AAA, BBB, CCC
    held = _held_data()    # only AAA, BBB actually loaded
    port_risk = {"beta": 1.23}

    row_missing = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", port_df, port_risk, held, held_tickers=["AAA", "BBB", "CCC"],
    )
    row_full = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", port_df, port_risk, held, held_tickers=["AAA", "BBB"],
    )

    assert row_missing["avg_pairwise_corr"] is None
    assert row_missing["diversification_score"] is None
    assert row_full["avg_pairwise_corr"] is not None
    assert row_full["diversification_score"] is not None

    # corr_coverage_n is non-None in BOTH -- it describes held_data's own
    # inputs, not the held_tickers-vs-matrix check.
    assert row_missing["corr_coverage_n"] is not None
    assert row_full["corr_coverage_n"] is not None

    # beta/top_sector are real, equal values in both -- proves the
    # correlation gate never touches unrelated metrics.
    assert row_missing["portfolio_beta"] == pytest.approx(1.23)
    assert row_full["portfolio_beta"] == pytest.approx(1.23)
    assert row_missing["top_sector"] == row_full["top_sector"] == "Tech"


def test_h2_held_tickers_omitted_withholds_correlation_but_keeps_beta():
    held = _held_data()
    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.1}, held,   # held_tickers NOT passed
    )
    assert row["avg_pairwise_corr"] is None
    assert row["diversification_score"] is None
    assert row["portfolio_beta"] == pytest.approx(1.1)


def test_h3_empty_held_tickers_list_withholds_correlation():
    held = _held_data()
    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.1}, held, held_tickers=[],
    )
    assert row["avg_pairwise_corr"] is None
    assert row["diversification_score"] is None
    assert row["portfolio_beta"] == pytest.approx(1.1)


def test_h4_all_nan_matrix_from_disjoint_thin_history_is_withheld():
    """The hazard correlation_claim_verified's own docstring names: AAA/BBB
    at 60 overlapping bars + CCC at a single, disjoint bar produces a
    NON-EMPTY 3x3 corr_df that is entirely NaN (every held ticker IS a
    column, so correlation_unchecked returns [] -- 'checked, clean' by that
    measure alone). diversification_score's `avg_corr ... else 0.0` fallback
    would score this 50.0 ("Well Diversified", clearing DIVERSIFY_WELL_PCT
    (42.0)) off ZERO real observations if nothing gated on n_obs too."""
    from stock_analyzer import portfolio as _portfolio

    held = dict(_held_data())   # AAA, BBB: 60 overlapping bars each
    held["CCC"] = _hist(1, start=75.0)   # single, disjoint bar

    # Prove the hazard is real BEFORE this function's fix would mask it:
    # the raw building blocks really do produce a non-empty, fully-NaN
    # matrix that scores a fabricated 50.0 if ungated.
    raw_corr_df = _portfolio.correlation_matrix(held)
    assert not raw_corr_df.empty
    raw_div = _portfolio.diversification_score(raw_corr_df)
    assert raw_div["score"] == pytest.approx(50.0)
    from stock_analyzer.constants import DIVERSIFY_WELL_PCT
    assert raw_div["score"] > DIVERSIFY_WELL_PCT

    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.0}, held,
        held_tickers=["AAA", "BBB", "CCC"],
    )
    assert row["avg_pairwise_corr"] is None
    assert row["diversification_score"] is None

    # Control: drop CCC from both the data and the expected set -- a clean,
    # fully-overlapping 2-ticker book populates normally.
    held_clean = {"AAA": held["AAA"], "BBB": held["BBB"]}
    row_clean = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.0}, held_clean,
        held_tickers=["AAA", "BBB"],
    )
    assert row_clean["avg_pairwise_corr"] is not None
    assert row_clean["diversification_score"] is not None


def test_h5_row_key_set_unchanged_with_and_without_diagnostics():
    """The returned row's key set must never gain/lose a key based on
    held_tickers/diagnostics usage -- db.save_portfolio_risk_snapshot upserts
    the whole row dict, and an unexpected key makes PostgREST reject the
    whole write (losing the entire day's row, turning the cron heartbeat
    red)."""
    held = _held_data()
    row_no_diag = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.0}, held, held_tickers=["AAA", "BBB"],
    )
    assert set(row_no_diag.keys()) == _EXPECTED_ROW_KEYS

    diag = {}
    row_with_diag = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.0}, held,
        held_tickers=["AAA", "BBB"], diagnostics=diag,
    )
    assert set(row_with_diag.keys()) == _EXPECTED_ROW_KEYS

    # Also pin the withheld/omitted paths -- same guarantee must hold there.
    row_withheld = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.0}, held,
    )
    assert set(row_withheld.keys()) == _EXPECTED_ROW_KEYS


def test_h6_diagnostics_populated_correctly_and_none_does_not_crash():
    held = _held_data()

    # Verified case.
    diag_ok = {}
    rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.0}, held,
        held_tickers=["AAA", "BBB"], diagnostics=diag_ok,
    )
    assert diag_ok["corr_unchecked"] == []
    assert isinstance(diag_ok["corr_n_obs"], int)
    assert diag_ok["corr_n_obs"] > 0
    assert diag_ok["corr_withheld"] is False

    # Withheld case (a held ticker missing from held_data).
    diag_bad = {}
    rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.0}, held,
        held_tickers=["AAA", "BBB", "CCC"], diagnostics=diag_bad,
    )
    assert diag_bad["corr_unchecked"] == ["CCC"]
    assert diag_bad["corr_withheld"] is True

    # diagnostics=None (the default) must never crash.
    row = rmh.build_portfolio_risk_snapshot(
        "2026-09-15", _port_df(), {"beta": 1.0}, held, held_tickers=["AAA", "BBB"],
    )
    assert row["avg_pairwise_corr"] is not None
