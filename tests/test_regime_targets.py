"""Tests for stock_analyzer/regime_targets.py — Concept D regime-conditional
beta/cash gap diagnostic (regime_position_gap). Previously zero test coverage
despite being real (if awareness-only, never-gating) decision-support logic.
Pure, no I/O — fully None-safe by design (never raises on missing beta, cash,
an empty portfolio, or an unrecognized regime id).
"""
import pandas as pd
import pytest

from stock_analyzer import regime_targets as rt
from stock_analyzer.constants import REGIME_BETA_CEILING, REGIME_CASH_FLOOR_PCT


# ─── regime id lookup / fallback ────────────────────────────────────────────

def test_unrecognized_regime_id_falls_back_to_neutral_ceiling_and_floor():
    result = rt.regime_position_gap("not_a_real_regime", 1.0, 5.0, pd.DataFrame(), {})
    assert result["beta_ceiling"] == REGIME_BETA_CEILING["neutral"]
    assert result["cash_floor_pct"] == REGIME_CASH_FLOOR_PCT["neutral"]


def test_all_five_regime_ids_resolve_to_their_own_ceiling_and_floor():
    for regime_id, ceiling in REGIME_BETA_CEILING.items():
        result = rt.regime_position_gap(regime_id, 1.0, 5.0, pd.DataFrame(), {})
        assert result["beta_ceiling"] == ceiling
        assert result["cash_floor_pct"] == REGIME_CASH_FLOOR_PCT[regime_id]
        assert result["regime_id"] == regime_id


# ─── None-safety on beta / cash ─────────────────────────────────────────────

def test_none_port_beta_gives_none_beta_gap_and_no_breach():
    result = rt.regime_position_gap("neutral", None, 5.0, pd.DataFrame(), {})
    assert result["beta_gap"] is None
    assert result["beta_breach"] is False
    assert result["port_beta"] is None


def test_none_cash_pct_gives_none_cash_gap_and_no_breach():
    result = rt.regime_position_gap("neutral", 1.0, None, pd.DataFrame(), {})
    assert result["cash_gap"] is None
    assert result["cash_breach"] is False
    assert result["cash_pct"] is None


def test_both_none_never_raises_and_no_breaches():
    result = rt.regime_position_gap("stagflation_risk", None, None, pd.DataFrame(), {})
    assert result["beta_gap"] is None
    assert result["cash_gap"] is None
    assert result["beta_breach"] is False
    assert result["cash_breach"] is False


def test_held_data_none_does_not_raise():
    # Regression guard: `held_data = held_data or {}` at the top.
    port_df = pd.DataFrame([{"Ticker": "AAA", "Weight (%)": 50.0}])
    result = rt.regime_position_gap("neutral", 1.5, 0.0, port_df, None)
    assert isinstance(result, dict)
    assert result["top_contributors"] == []  # no held_data -> every beta lookup is None -> excluded


# ─── beta_gap / cash_gap sign, rounding, and breach boundary ────────────────

def test_beta_gap_positive_and_breach_when_above_ceiling():
    # neutral ceiling = 1.10
    result = rt.regime_position_gap("neutral", 1.35, 5.0, pd.DataFrame(), {})
    assert result["beta_gap"] == 0.25
    assert result["beta_breach"] is True


def test_beta_gap_negative_and_no_breach_when_below_ceiling():
    result = rt.regime_position_gap("neutral", 0.90, 5.0, pd.DataFrame(), {})
    assert result["beta_gap"] == round(0.90 - 1.10, 2)
    assert result["beta_breach"] is False


def test_beta_gap_exactly_zero_at_ceiling_is_not_a_breach():
    # gap == 0 must NOT breach -- source uses `> 0`, not `>= 0`.
    result = rt.regime_position_gap("neutral", 1.10, 5.0, pd.DataFrame(), {})
    assert result["beta_gap"] == 0.0
    assert result["beta_breach"] is False


def test_cash_gap_positive_and_breach_when_below_floor():
    # neutral cash floor = 5.0
    result = rt.regime_position_gap("neutral", 1.0, 2.0, pd.DataFrame(), {})
    assert result["cash_gap"] == 3.0
    assert result["cash_breach"] is True


def test_cash_gap_negative_and_no_breach_when_above_floor():
    result = rt.regime_position_gap("neutral", 1.0, 10.0, pd.DataFrame(), {})
    assert result["cash_gap"] == round(5.0 - 10.0, 1)
    assert result["cash_breach"] is False


def test_cash_gap_exactly_zero_at_floor_is_not_a_breach():
    result = rt.regime_position_gap("neutral", 1.0, 5.0, pd.DataFrame(), {})
    assert result["cash_gap"] == 0.0
    assert result["cash_breach"] is False


# ─── top_contributors ────────────────────────────────────────────────────────

def _held(beta):
    return {"risk_metrics": {"beta": beta}}


def test_top_contributors_empty_when_no_beta_breach():
    port_df = pd.DataFrame([{"Ticker": "AAA", "Weight (%)": 50.0}])
    held_data = {"AAA": _held(2.0)}
    # beta below ceiling -> no breach -> top_contributors stays empty
    # regardless of a real port_df/held_data.
    result = rt.regime_position_gap("neutral", 0.5, 5.0, port_df, held_data)
    assert result["beta_breach"] is False
    assert result["top_contributors"] == []


def test_top_contributors_empty_when_port_df_empty_even_if_breach():
    result = rt.regime_position_gap("neutral", 1.5, 5.0, pd.DataFrame(), {})
    assert result["beta_breach"] is True
    assert result["top_contributors"] == []


def test_top_contributors_excludes_rows_with_missing_or_nonpositive_beta_or_weight():
    port_df = pd.DataFrame([
        {"Ticker": "AAA", "Weight (%)": 30.0},   # no held_data entry -> beta None
        {"Ticker": "BBB", "Weight (%)": 0.0},    # weight <= 0
        {"Ticker": "CCC", "Weight (%)": 20.0},   # beta <= 0
        {"Ticker": "DDD", "Weight (%)": 25.0},   # valid
    ])
    held_data = {
        "BBB": _held(1.5),
        "CCC": _held(-0.5),
        "DDD": _held(1.8),
    }
    result = rt.regime_position_gap("neutral", 1.5, 5.0, port_df, held_data)
    tickers = [c["ticker"] for c in result["top_contributors"]]
    assert tickers == ["DDD"]


def test_top_contributors_sorted_descending_and_capped_at_3():
    port_df = pd.DataFrame([
        {"Ticker": "A", "Weight (%)": 10.0},
        {"Ticker": "B", "Weight (%)": 20.0},
        {"Ticker": "C", "Weight (%)": 30.0},
        {"Ticker": "D", "Weight (%)": 40.0},
    ])
    held_data = {
        "A": _held(1.0),  # contrib = 1.0*10/100 = 0.10
        "B": _held(1.0),  # contrib = 0.20
        "C": _held(1.0),  # contrib = 0.30
        "D": _held(1.0),  # contrib = 0.40
    }
    result = rt.regime_position_gap("neutral", 1.5, 5.0, port_df, held_data)
    tickers = [c["ticker"] for c in result["top_contributors"]]
    assert tickers == ["D", "C", "B"]  # descending, capped at 3
    assert result["top_contributors"][0]["contrib"] == pytest.approx(0.40)
