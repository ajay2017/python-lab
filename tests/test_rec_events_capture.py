"""
stock_analyzer/rec_events_capture.py — Recommendation-Outcomes-Measurement
Phase 1b capture half (docs/plans/recommendation-outcomes-measurement.md
§10/§11).

Every numeric field must be None on a missing/unmeasured input, NEVER a
fabricated 0/neutral — the exact bug class this project has already been
bitten by twice (feedback_sentinel_is_present / feedback_overloaded_
producer_state). `rebalance_actions`/`diversification_recommendations` are
monkeypatched directly (they're imported names in this module's own
namespace, same pattern test_home_risk_synthesis.py uses) so these tests
control exactly what each generator sees without depending on portfolio.py's
real sector roster.
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from stock_analyzer import rec_events_capture as rec
from stock_analyzer.constants import PORTFOLIO_BETA_ELEVATED, SINGLE_NAME_CEILING

pytestmark = pytest.mark.fast

FIRED_DATE = datetime.date(2026, 9, 15)


# ── rebal_trim ───────────────────────────────────────────────────────────────

def test_rebal_trim_row_built_from_rebalance_actions_trim_action(monkeypatch):
    monkeypatch.setattr(rec, "rebalance_actions", lambda port_df: [
        {"type": "trim", "ticker": "AAA", "weight": 22.0, "trim_val": 3500.0},
    ])
    rows = rec._build_rebal_trim_rows(pd.DataFrame({"Ticker": ["AAA"]}), "2026-09-15")
    assert len(rows) == 1
    r = rows[0]
    assert r["rec_type"] == "rebal_trim"
    assert r["ticker"] == "AAA"
    assert r["fired_date"] == "2026-09-15"
    assert r["source"] == "cron"
    assert r["metric_name"] == "single_name_pct"
    assert r["metric_before"] == pytest.approx(22.0)
    assert r["metric_predicted_after"] == pytest.approx(SINGLE_NAME_CEILING)
    assert r["rec_dollars"] == pytest.approx(3500.0)
    # ADD-only fields stay None for this type.
    assert r["price_at_rec"] is None
    assert r["candidates"] is None
    assert r["corr_coverage_n"] is None


def test_rebal_trim_ignores_non_trim_actions(monkeypatch):
    monkeypatch.setattr(rec, "rebalance_actions", lambda port_df: [
        {"type": "add", "ticker": "BBB", "weight": 2.0},
        {"type": "review", "ticker": "CCC"},
    ])
    rows = rec._build_rebal_trim_rows(pd.DataFrame({"Ticker": ["BBB"]}), "2026-09-15")
    assert rows == []


def test_rebal_trim_empty_port_df_returns_empty():
    assert rec._build_rebal_trim_rows(pd.DataFrame(), "2026-09-15") == []
    assert rec._build_rebal_trim_rows(None, "2026-09-15") == []


def test_rebal_trim_generator_failure_returns_empty_not_raise(monkeypatch):
    monkeypatch.setattr(rec, "rebalance_actions", lambda port_df: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rec._build_rebal_trim_rows(pd.DataFrame({"Ticker": ["AAA"]}), "2026-09-15") == []


# ── beta_trim ────────────────────────────────────────────────────────────────

def _beta_port_df():
    return pd.DataFrame({
        "Ticker":         ["AAA", "BBB", "CCC"],
        "Weight (%)":     [20.0, 15.0, 10.0],
        "Market Value":   [20000.0, 15000.0, 10000.0],
    })


def _beta_held_data():
    return {
        "AAA": {"risk_metrics": {"beta": 2.0}},
        "BBB": {"risk_metrics": {"beta": 1.5}},
        "CCC": {"risk_metrics": {"beta": 1.1}},
    }


def test_beta_trim_fires_above_elevated_and_picks_top_contributor():
    port_risk = {"beta": PORTFOLIO_BETA_ELEVATED + 0.5}
    row = rec._build_beta_trim_row(
        _beta_port_df(), port_risk, _beta_held_data(), None, FIRED_DATE,
    )
    assert row is not None
    assert row["rec_type"] == "beta_trim"
    # AAA has the highest beta*weight contribution (2.0*20 > 1.5*15 > 1.1*10).
    assert row["ticker"] == "AAA"
    assert row["metric_name"] == "portfolio_beta"
    assert row["metric_before"] == pytest.approx(port_risk["beta"])
    assert row["metric_predicted_after"] is not None
    assert row["metric_predicted_after"] < row["metric_before"]
    assert row["rec_dollars"] == pytest.approx(20000.0 * 0.5)
    assert row["fired_date"] == FIRED_DATE.isoformat()
    assert row["source"] == "cron"


def test_beta_trim_does_not_fire_at_or_below_elevated():
    port_risk = {"beta": PORTFOLIO_BETA_ELEVATED}
    assert rec._build_beta_trim_row(
        _beta_port_df(), port_risk, _beta_held_data(), None, FIRED_DATE,
    ) is None

    port_risk_low = {"beta": PORTFOLIO_BETA_ELEVATED - 0.2}
    assert rec._build_beta_trim_row(
        _beta_port_df(), port_risk_low, _beta_held_data(), None, FIRED_DATE,
    ) is None


def test_beta_trim_excludes_same_day_bought_top_contributor():
    """AAA is the top contributor but was bought today — the pick should
    fall through to BBB, mirroring risk_advisor.py's own exclusion."""
    port_risk = {"beta": PORTFOLIO_BETA_ELEVATED + 0.5}
    trades_df = pd.DataFrame({
        "ticker":     ["AAA"],
        "action":     ["BUY"],
        "traded_at":  [pd.Timestamp(FIRED_DATE, tz="America/New_York")],
    })
    row = rec._build_beta_trim_row(
        _beta_port_df(), port_risk, _beta_held_data(), trades_df, FIRED_DATE,
    )
    assert row is not None
    assert row["ticker"] == "BBB"


def test_beta_trim_none_when_no_positive_beta_contributors():
    port_risk = {"beta": PORTFOLIO_BETA_ELEVATED + 0.5}
    held = {"AAA": {"risk_metrics": {"beta": None}}}
    port_df = pd.DataFrame({"Ticker": ["AAA"], "Weight (%)": [20.0], "Market Value": [20000.0]})
    assert rec._build_beta_trim_row(port_df, port_risk, held, None, FIRED_DATE) is None


def test_beta_trim_none_when_port_risk_missing():
    assert rec._build_beta_trim_row(_beta_port_df(), None, _beta_held_data(), None, FIRED_DATE) is None
    assert rec._build_beta_trim_row(_beta_port_df(), {}, _beta_held_data(), None, FIRED_DATE) is None


# ── diversify_add ────────────────────────────────────────────────────────────

def test_diversify_add_row_uses_first_candidate_and_injected_price(monkeypatch):
    monkeypatch.setattr(rec, "diversification_recommendations", lambda *a, **kw: [
        {"type": "ADD", "sector": "Healthcare", "candidates": ["UNH", "JNJ"]},
        {"type": "REDUCE", "sector": "Tech"},  # non-ADD entries must be ignored
    ])
    rows = rec._build_diversify_add_rows(
        pd.DataFrame({"Ticker": ["AAA"]}), {}, {}, 50_000.0,
        avg_pairwise_corr=0.42, corr_coverage_n=120,
        fired_date_str="2026-09-15", price_fn=lambda t: 310.5,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["rec_type"] == "diversify_add"
    assert r["ticker"] == "UNH"
    assert r["sector"] == "Healthcare"
    assert r["candidates"] == ["UNH", "JNJ"]
    assert r["metric_name"] == "avg_pairwise_corr"
    assert r["metric_before"] == pytest.approx(0.42)
    assert r["metric_predicted_after"] is None   # no formula exists yet
    assert r["rec_dollars"] is None
    assert r["price_at_rec"] == pytest.approx(310.5)
    assert r["corr_coverage_n"] == 120


def test_diversify_add_skips_empty_candidates(monkeypatch):
    monkeypatch.setattr(rec, "diversification_recommendations", lambda *a, **kw: [
        {"type": "ADD", "sector": "Energy", "candidates": []},
    ])
    rows = rec._build_diversify_add_rows(
        pd.DataFrame({"Ticker": ["AAA"]}), {}, {}, 50_000.0,
        avg_pairwise_corr=None, corr_coverage_n=None,
        fired_date_str="2026-09-15",
    )
    assert rows == []


def test_diversify_add_price_fn_none_leaves_price_at_rec_none(monkeypatch):
    monkeypatch.setattr(rec, "diversification_recommendations", lambda *a, **kw: [
        {"type": "ADD", "sector": "Energy", "candidates": ["XOM"]},
    ])
    rows = rec._build_diversify_add_rows(
        pd.DataFrame({"Ticker": ["AAA"]}), {}, {}, 50_000.0,
        avg_pairwise_corr=None, corr_coverage_n=None,
        fired_date_str="2026-09-15", price_fn=None,
    )
    assert rows[0]["price_at_rec"] is None


def test_diversify_add_price_fn_raising_leaves_price_at_rec_none(monkeypatch):
    monkeypatch.setattr(rec, "diversification_recommendations", lambda *a, **kw: [
        {"type": "ADD", "sector": "Energy", "candidates": ["XOM"]},
    ])
    rows = rec._build_diversify_add_rows(
        pd.DataFrame({"Ticker": ["AAA"]}), {}, {}, 50_000.0,
        avg_pairwise_corr=None, corr_coverage_n=None,
        fired_date_str="2026-09-15",
        price_fn=lambda t: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert rows[0]["price_at_rec"] is None


def test_diversify_add_generator_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(rec, "diversification_recommendations",
                         lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    rows = rec._build_diversify_add_rows(
        pd.DataFrame({"Ticker": ["AAA"]}), {}, {}, 50_000.0,
        avg_pairwise_corr=None, corr_coverage_n=None, fired_date_str="2026-09-15",
    )
    assert rows == []


# ── orchestrator: isolation + NULL-preserving ────────────────────────────────

def test_build_rec_event_rows_combines_all_three_generators(monkeypatch):
    monkeypatch.setattr(rec, "rebalance_actions", lambda port_df: [
        {"type": "trim", "ticker": "AAA", "weight": 22.0, "trim_val": 1000.0},
    ])
    monkeypatch.setattr(rec, "diversification_recommendations", lambda *a, **kw: [
        {"type": "ADD", "sector": "Energy", "candidates": ["XOM"]},
    ])
    port_risk = {"beta": PORTFOLIO_BETA_ELEVATED + 0.5}
    rows = rec.build_rec_event_rows(
        FIRED_DATE, _beta_port_df(), port_risk, _beta_held_data(), None,
        {"avg_pairwise_corr": 0.3, "corr_coverage_n": 100},
        {}, {}, 50_000.0,
    )
    types = {r["rec_type"] for r in rows}
    assert types == {"rebal_trim", "beta_trim", "diversify_add"}


def test_one_generator_failure_never_blanks_the_others(monkeypatch):
    """The load-bearing isolation contract: rebal_trim generator raises,
    beta_trim and diversify_add must still produce their rows."""
    monkeypatch.setattr(rec, "rebalance_actions",
                         lambda port_df: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(rec, "diversification_recommendations", lambda *a, **kw: [
        {"type": "ADD", "sector": "Energy", "candidates": ["XOM"]},
    ])
    port_risk = {"beta": PORTFOLIO_BETA_ELEVATED + 0.5}
    rows = rec.build_rec_event_rows(
        FIRED_DATE, _beta_port_df(), port_risk, _beta_held_data(), None,
        {"avg_pairwise_corr": 0.3, "corr_coverage_n": 100},
        {}, {}, 50_000.0,
    )
    types = {r["rec_type"] for r in rows}
    assert "rebal_trim" not in types
    assert "beta_trim" in types
    assert "diversify_add" in types


def test_beta_trim_failure_never_blanks_rebal_trim_or_diversify_add(monkeypatch):
    monkeypatch.setattr(rec, "rebalance_actions", lambda port_df: [
        {"type": "trim", "ticker": "AAA", "weight": 22.0, "trim_val": 1000.0},
    ])
    monkeypatch.setattr(rec, "diversification_recommendations", lambda *a, **kw: [
        {"type": "ADD", "sector": "Energy", "candidates": ["XOM"]},
    ])
    monkeypatch.setattr(rec, "expected_beta_after_trim",
                         lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    port_risk = {"beta": PORTFOLIO_BETA_ELEVATED + 0.5}
    rows = rec.build_rec_event_rows(
        FIRED_DATE, _beta_port_df(), port_risk, _beta_held_data(), None,
        {"avg_pairwise_corr": 0.3, "corr_coverage_n": 100},
        {}, {}, 50_000.0,
    )
    types = {r["rec_type"] for r in rows}
    assert "rebal_trim" in types
    assert "diversify_add" in types


def test_no_qualifying_rec_returns_empty_list_not_none(monkeypatch):
    monkeypatch.setattr(rec, "rebalance_actions", lambda port_df: [])
    monkeypatch.setattr(rec, "diversification_recommendations", lambda *a, **kw: [])
    rows = rec.build_rec_event_rows(
        FIRED_DATE, pd.DataFrame({"Ticker": ["AAA"]}), {"beta": 1.0}, {}, None,
        None, {}, {}, 50_000.0,
    )
    assert rows == []
