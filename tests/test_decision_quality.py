"""Regression tests for stock_analyzer/decision_quality.py — retrospective
investor-improvement analytics: monthly/quarterly Decision Quality grades
(Feature B) and per-trade prep-tier classification for Workflow ROI
(Feature C). Pure computation (pandas only, no I/O). Dates use plain
datetime.date objects throughout so decision_quality.py's own ET-localizing
_parse_dt short-circuits to a direct return (isinstance date-not-datetime),
avoiding any UTC->ET conversion day-shift in the fixtures.
See docs/plans/test-automation.md for scope.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import decision_quality as dq
from stock_analyzer.constants import (
    DECISION_QUALITY_ALPHA_SCALE,
    DECISION_QUALITY_GRADE_A,
    DECISION_QUALITY_GRADE_B,
    DECISION_QUALITY_GRADE_C,
    DECISION_QUALITY_GRADE_D,
    DECISION_QUALITY_MIN_TRADES,
    WORKFLOW_ANALYST_LOOKBACK_DAYS,
    WORKFLOW_EARNINGS_WINDOW_DAYS,
    WORKFLOW_MIN_THESIS_LENGTH,
)

TODAY = date(2026, 7, 28)


def _sell(ticker, d, realized_pnl, cost_basis=10.0, shares=10.0, price=110.0, id_=0):
    return {"id": id_, "ticker": ticker, "traded_at": d, "action": "SELL",
            "realized_pnl": realized_pnl, "cost_basis": cost_basis,
            "shares": shares, "price": price}


def _buy(ticker, d, shares=10.0, user_thesis=None, id_=0):
    return {"id": id_, "ticker": ticker, "traded_at": d, "action": "BUY",
            "shares": shares, "user_thesis": user_thesis}


def _trades(rows):
    return pd.DataFrame(rows)


# ── _grade_letter / _grade_label / _grade_color ───────────────────────────

@pytest.mark.parametrize("score,letter", [
    (100.0, "A"), (DECISION_QUALITY_GRADE_A, "A"), (DECISION_QUALITY_GRADE_A - 0.1, "B"),
    (DECISION_QUALITY_GRADE_B, "B"), (DECISION_QUALITY_GRADE_B - 0.1, "C"),
    (DECISION_QUALITY_GRADE_C, "C"), (DECISION_QUALITY_GRADE_C - 0.1, "D"),
    (DECISION_QUALITY_GRADE_D, "D"), (DECISION_QUALITY_GRADE_D - 0.1, "F"),
    (0.0, "F"),
])
def test_grade_letter_boundaries(score, letter):
    assert dq._grade_letter(score) == letter


def test_grade_label_mapping():
    assert dq._grade_label("A") == "Elite"
    assert dq._grade_label("F") == "Critical"
    assert dq._grade_label("Z") == "—"


def test_grade_color_mapping():
    assert dq._grade_color("A") == "#16a34a"
    assert dq._grade_color("Z") == "#6b7280"


# ── _parse_dt ─────────────────────────────────────────────────────────────

def test_parse_dt_none_returns_none():
    assert dq._parse_dt(None) is None


def test_parse_dt_plain_date_passthrough_no_tz_conversion():
    d = date(2026, 3, 15)
    assert dq._parse_dt(d) == d


def test_parse_dt_unparseable_returns_none():
    assert dq._parse_dt("not-a-date") is None


def test_parse_dt_iso_string_parses_and_localizes():
    result = dq._parse_dt("2026-03-15T12:00:00-04:00")
    assert result == date(2026, 3, 15)


# ── _profit_factor ─────────────────────────────────────────────────────────

def test_profit_factor_mixed():
    grp = pd.DataFrame({"realized_pnl": [100.0, -50.0, -50.0]})
    assert dq._profit_factor(grp) == 1.0


def test_profit_factor_all_winners_is_none():
    grp = pd.DataFrame({"realized_pnl": [100.0, 50.0]})
    assert dq._profit_factor(grp) is None


def test_profit_factor_all_losers_is_zero_not_none():
    grp = pd.DataFrame({"realized_pnl": [-100.0]})
    assert dq._profit_factor(grp) == 0.0


# ── _monthly_overtrading ────────────────────────────────────────────────────

def test_monthly_overtrading_empty_after_filter():
    df = _trades([{"ticker": "AAPL", "traded_at": TODAY, "action": "SPLIT"}])
    assert dq._monthly_overtrading(df) == {}


def test_monthly_overtrading_first_two_months_have_no_baseline():
    df = _trades([
        _buy("AAPL", date(2026, 1, 15)),
        _buy("AAPL", date(2026, 2, 15)),
    ])
    result = dq._monthly_overtrading(df)
    assert result["2026-01"] is None
    assert result["2026-02"] is None


def test_monthly_overtrading_third_month_gets_a_multiplier():
    rows = (
        [_buy("AAPL", date(2026, 1, 15), id_=i) for i in range(2)] +
        [_buy("AAPL", date(2026, 2, 15), id_=i) for i in range(2, 6)] +
        [_buy("AAPL", date(2026, 3, 15), id_=i) for i in range(6, 15)]
    )
    df = _trades(rows)
    result = dq._monthly_overtrading(df)
    # prior avg of months with count 2 and 4 = 3.0; month 3 has 9 -> 9/3 = 3.0
    assert result["2026-03"] == 3.0


def test_monthly_overtrading_only_counts_buy_and_sell_actions():
    rows = (
        [_buy("AAPL", date(2026, 1, 15), id_=0)] +
        [_buy("AAPL", date(2026, 2, 15), id_=1)] +
        [_buy("AAPL", date(2026, 3, 15), id_=2),
         {"id": 3, "ticker": "AAPL", "traded_at": date(2026, 3, 16), "action": "SPLIT"}]
    )
    df = _trades(rows)
    result = dq._monthly_overtrading(df)
    assert result["2026-03"] == 1.0  # SPLIT row not counted, only the 1 BUY


# ── _alpha_subscore ─────────────────────────────────────────────────────────

def test_alpha_subscore_zero_alpha_is_midpoint():
    assert dq._alpha_subscore(0.0) == 50.0


def test_alpha_subscore_at_positive_scale_is_100():
    assert dq._alpha_subscore(DECISION_QUALITY_ALPHA_SCALE) == 100.0


def test_alpha_subscore_at_negative_scale_is_0():
    assert dq._alpha_subscore(-DECISION_QUALITY_ALPHA_SCALE) == 0.0


def test_alpha_subscore_clamped_beyond_scale():
    assert dq._alpha_subscore(DECISION_QUALITY_ALPHA_SCALE * 10) == 100.0
    assert dq._alpha_subscore(-DECISION_QUALITY_ALPHA_SCALE * 10) == 0.0


# ── build_monthly_grades ────────────────────────────────────────────────────

def test_build_monthly_grades_empty_trades_df():
    assert dq.build_monthly_grades(pd.DataFrame()) == []


def test_build_monthly_grades_none_trades_df():
    assert dq.build_monthly_grades(None) == []


def test_build_monthly_grades_excludes_month_below_min_trades():
    df = _trades([_sell("AAPL", date(2026, 3, 10), 100.0, id_=0)])  # only 1 trade
    assert dq.build_monthly_grades(df) == []


def test_build_monthly_grades_excludes_unknown_month():
    df = _trades([
        {"id": 0, "ticker": "AAPL", "traded_at": "garbage", "action": "SELL",
         "realized_pnl": 100.0, "cost_basis": 10.0, "shares": 10.0, "price": 110.0},
        {"id": 1, "ticker": "AAPL", "traded_at": "garbage2", "action": "SELL",
         "realized_pnl": 50.0, "cost_basis": 10.0, "shares": 10.0, "price": 110.0},
    ])
    assert dq.build_monthly_grades(df) == []


def test_build_monthly_grades_win_rate_and_trade_count():
    df = _trades([
        _sell("AAPL", date(2026, 3, 5), 100.0, id_=0),
        _sell("AAPL", date(2026, 3, 10), 100.0, id_=1),
        _sell("AAPL", date(2026, 3, 15), -50.0, id_=2),
        _sell("AAPL", date(2026, 3, 20), -50.0, id_=3),
    ])
    grades = dq.build_monthly_grades(df)
    assert len(grades) == 1
    g = grades[0]
    assert g["month_str"] == "2026-03"
    assert g["year"] == 2026
    assert g["trade_count"] == 4
    assert g["win_rate"] == 50.0
    assert g["profit_factor"] == 2.0  # wins 200 / losses 100


def test_build_monthly_grades_all_winners_pf_subscore_full():
    df = _trades([
        _sell("AAPL", date(2026, 3, 5), 100.0, id_=0),
        _sell("AAPL", date(2026, 3, 10), 100.0, id_=1),
    ])
    grades = dq.build_monthly_grades(df)
    assert grades[0]["profit_factor"] is None
    # win_rate=100 -> wr_sub=100; pf_sub=100 (all-winner); no alpha -> composite = avg(100,100)=100
    assert grades[0]["composite_score"] == 100.0
    assert grades[0]["has_alpha"] is False


def test_build_monthly_grades_has_alpha_when_spy_return_provided():
    df = _trades([
        _sell("AAPL", date(2026, 3, 5), 100.0, id_=0),
        _sell("AAPL", date(2026, 3, 10), 100.0, id_=1),
    ])
    grades = dq.build_monthly_grades(df, spy_monthly_returns={"2026-03": 2.0})
    assert grades[0]["has_alpha"] is True
    assert grades[0]["alpha_vs_spy"] is not None


def test_build_monthly_grades_overtrading_penalty_high_tier():
    # Build 3 months so the 3rd has an overtrading multiplier >= 2.0 while
    # also having >=2 closed (SELL) trades to qualify for a grade.
    rows = (
        [_buy("AAPL", date(2026, 1, 15), id_=100)] +
        [_buy("AAPL", date(2026, 2, 15), id_=101)] +
        [_sell("AAPL", date(2026, 3, d), 100.0, id_=d) for d in range(1, 10)]
    )
    df = _trades(rows)
    overtrade = dq._monthly_overtrading(df)
    assert overtrade["2026-03"] >= 2.0
    grades = dq.build_monthly_grades(df)
    g = next(x for x in grades if x["month_str"] == "2026-03")
    # win_rate=100 -> wr_sub 100; all-winner pf_sub 100; composite pre-penalty
    # = 100, minus 25 for the >=2.0 overtrading tier -> 75.
    assert g["composite_score"] == 75.0
    assert g["overtrading_mult"] == overtrade["2026-03"]


def test_build_monthly_grades_sorted_by_month():
    df = _trades([
        _sell("AAPL", date(2026, 5, 1), 100.0, id_=0),
        _sell("AAPL", date(2026, 5, 2), 100.0, id_=1),
        _sell("AAPL", date(2026, 1, 1), 100.0, id_=2),
        _sell("AAPL", date(2026, 1, 2), 100.0, id_=3),
    ])
    grades = dq.build_monthly_grades(df)
    assert [g["month_str"] for g in grades] == ["2026-01", "2026-05"]


def test_build_monthly_grades_grade_letter_matches_composite():
    df = _trades([
        _sell("AAPL", date(2026, 3, 5), 100.0, id_=0),
        _sell("AAPL", date(2026, 3, 10), -50.0, id_=1),
    ])
    grades = dq.build_monthly_grades(df)
    g = grades[0]
    assert g["grade_letter"] == dq._grade_letter(g["composite_score"])
    assert g["grade_label"] == dq._grade_label(g["grade_letter"])
    assert g["grade_color"] == dq._grade_color(g["grade_letter"])


# ── build_quarterly_grades ──────────────────────────────────────────────────

def _mg(month_str, year, trade_count, win_rate, pf, alpha, ot, composite, has_alpha=True):
    return {
        "month_str": month_str, "year": year, "trade_count": trade_count,
        "win_rate": win_rate, "profit_factor": pf, "alpha_vs_spy": alpha,
        "overtrading_mult": ot, "composite_score": composite, "has_alpha": has_alpha,
    }


def test_build_quarterly_grades_empty_input():
    assert dq.build_quarterly_grades([]) == []


def test_build_quarterly_grades_buckets_into_quarters():
    monthly = [
        _mg("2026-01", 2026, 2, 50.0, 1.0, 0.0, None, 60.0),
        _mg("2026-02", 2026, 2, 50.0, 1.0, 0.0, None, 60.0),
        _mg("2026-04", 2026, 2, 50.0, 1.0, 0.0, None, 60.0),
    ]
    result = dq.build_quarterly_grades(monthly)
    periods = [r["period_str"] for r in result]
    assert periods == ["2026-Q1", "2026-Q2"]


def test_build_quarterly_grades_trade_count_weighted_composite():
    monthly = [
        _mg("2026-01", 2026, 1, 50.0, 1.0, 0.0, None, 100.0),
        _mg("2026-02", 2026, 3, 50.0, 1.0, 0.0, None, 0.0),
    ]
    result = dq.build_quarterly_grades(monthly)
    q1 = result[0]
    assert q1["trade_count"] == 4
    # weighted: (1*100 + 3*0) / 4 = 25.0
    assert q1["composite_score"] == 25.0


def test_build_quarterly_grades_averages_ignore_none_values():
    monthly = [
        _mg("2026-01", 2026, 2, 50.0, None, None, None, 60.0),
        _mg("2026-02", 2026, 2, 70.0, 2.0, 1.0, 1.2, 60.0),
    ]
    result = dq.build_quarterly_grades(monthly)
    q1 = result[0]
    assert q1["win_rate"] == 60.0  # avg of 50, 70
    assert q1["profit_factor"] == 2.0  # only one non-None value
    assert q1["overtrading_mult"] == 1.2


def test_build_quarterly_grades_has_alpha_true_if_any_month_has_it():
    monthly = [
        _mg("2026-01", 2026, 2, 50.0, 1.0, 0.0, None, 60.0, has_alpha=False),
        _mg("2026-02", 2026, 2, 50.0, 1.0, 0.0, None, 60.0, has_alpha=True),
    ]
    result = dq.build_quarterly_grades(monthly)
    assert result[0]["has_alpha"] is True


def test_build_quarterly_grades_skips_malformed_month_str():
    monthly = [
        {"month_str": "bad", "year": None, "trade_count": 2, "win_rate": 50.0,
         "profit_factor": 1.0, "alpha_vs_spy": None, "overtrading_mult": None,
         "composite_score": 60.0, "has_alpha": False},
    ]
    assert dq.build_quarterly_grades(monthly) == []


def test_build_quarterly_grades_zero_total_trades_skipped():
    monthly = [_mg("2026-01", 2026, 0, 50.0, 1.0, 0.0, None, 60.0)]
    assert dq.build_quarterly_grades(monthly) == []


# ── build_spy_monthly_returns ────────────────────────────────────────────────

def test_build_spy_monthly_returns_computes_first_to_last():
    prices = {"2026-03-01": 100.0, "2026-03-15": 110.0, "2026-03-31": 105.0}
    result = dq.build_spy_monthly_returns(prices)
    assert result["2026-03"] == 5.0  # (105/100 - 1) * 100


def test_build_spy_monthly_returns_handles_unsorted_entries():
    prices = {"2026-03-31": 105.0, "2026-03-01": 100.0}
    result = dq.build_spy_monthly_returns(prices)
    assert result["2026-03"] == 5.0


def test_build_spy_monthly_returns_skips_non_positive_start_price():
    prices = {"2026-03-01": 0.0, "2026-03-31": 105.0}
    assert "2026-03" not in dq.build_spy_monthly_returns(prices)


def test_build_spy_monthly_returns_splits_multiple_months():
    prices = {"2026-01-01": 100.0, "2026-01-31": 110.0,
              "2026-02-01": 200.0, "2026-02-28": 190.0}
    result = dq.build_spy_monthly_returns(prices)
    assert result["2026-01"] == 10.0
    assert result["2026-02"] == -5.0


# ── classify_trade_prep ──────────────────────────────────────────────────────

def _analyst_row(ticker, article_date):
    return {"ticker": ticker, "article_date": article_date}


def test_classify_trade_prep_full_prep_all_three_signals():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY,
                        "user_thesis": "x" * WORKFLOW_MIN_THESIS_LENGTH})
    analyst_df = pd.DataFrame([_analyst_row("AAPL", TODAY - timedelta(days=10))])
    earnings = {"AAPL": [{"article_date": TODAY - timedelta(days=5)}]}
    result = dq.classify_trade_prep(trade, analyst_df, earnings)
    assert result == {
        "thesis_flag": True, "analyst_flag": True, "earnings_flag": True,
        "tier_int": 3, "tier_label": "Full Prep", "tier_color": "#22d3ee",
    }


def test_classify_trade_prep_thorough_thesis_plus_analyst():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY,
                        "user_thesis": "x" * WORKFLOW_MIN_THESIS_LENGTH})
    analyst_df = pd.DataFrame([_analyst_row("AAPL", TODAY - timedelta(days=10))])
    result = dq.classify_trade_prep(trade, analyst_df, {})
    assert result["tier_int"] == 2
    assert result["tier_label"] == "Thorough"


def test_classify_trade_prep_basic_thesis_only():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY,
                        "user_thesis": "x" * WORKFLOW_MIN_THESIS_LENGTH})
    result = dq.classify_trade_prep(trade, None, {})
    assert result["tier_int"] == 1
    assert result["tier_label"] == "Basic"


def test_classify_trade_prep_cold_entry_no_thesis():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY, "user_thesis": ""})
    result = dq.classify_trade_prep(trade, None, {})
    assert result["tier_int"] == 0
    assert result["tier_label"] == "Cold Entry"


def test_classify_trade_prep_thesis_gates_the_ladder_analyst_and_earnings_alone_dont_count():
    # Non-obvious rule: 2 signals (analyst+earnings) WITHOUT a thesis still
    # falls all the way to "Cold Entry" -- thesis is a gatekeeper, not just
    # one of three equally-weighted signals.
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY, "user_thesis": ""})
    analyst_df = pd.DataFrame([_analyst_row("AAPL", TODAY - timedelta(days=10))])
    earnings = {"AAPL": [{"article_date": TODAY - timedelta(days=5)}]}
    result = dq.classify_trade_prep(trade, analyst_df, earnings)
    assert result["analyst_flag"] is True
    assert result["earnings_flag"] is True
    assert result["tier_int"] == 0
    assert result["tier_label"] == "Cold Entry"


def test_classify_trade_prep_thesis_length_boundary():
    short = pd.Series({"ticker": "AAPL", "traded_at": TODAY,
                        "user_thesis": "x" * (WORKFLOW_MIN_THESIS_LENGTH - 1)})
    exact = pd.Series({"ticker": "AAPL", "traded_at": TODAY,
                        "user_thesis": "x" * WORKFLOW_MIN_THESIS_LENGTH})
    assert dq.classify_trade_prep(short, None, {})["thesis_flag"] is False
    assert dq.classify_trade_prep(exact, None, {})["thesis_flag"] is True


def test_classify_trade_prep_analyst_lookback_boundary():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY, "user_thesis": ""})
    at_boundary = pd.DataFrame([_analyst_row(
        "AAPL", TODAY - timedelta(days=WORKFLOW_ANALYST_LOOKBACK_DAYS))])
    past_boundary = pd.DataFrame([_analyst_row(
        "AAPL", TODAY - timedelta(days=WORKFLOW_ANALYST_LOOKBACK_DAYS + 1))])
    assert dq.classify_trade_prep(trade, at_boundary, {})["analyst_flag"] is True
    assert dq.classify_trade_prep(trade, past_boundary, {})["analyst_flag"] is False


def test_classify_trade_prep_analyst_article_after_trade_date_excluded():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY, "user_thesis": ""})
    analyst_df = pd.DataFrame([_analyst_row("AAPL", TODAY + timedelta(days=1))])
    assert dq.classify_trade_prep(trade, analyst_df, {})["analyst_flag"] is False


def test_classify_trade_prep_analyst_ticker_mismatch_excluded():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY, "user_thesis": ""})
    analyst_df = pd.DataFrame([_analyst_row("MSFT", TODAY - timedelta(days=10))])
    assert dq.classify_trade_prep(trade, analyst_df, {})["analyst_flag"] is False


def test_classify_trade_prep_earnings_window_boundary():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY, "user_thesis": ""})
    at_boundary = {"AAPL": [{"article_date": TODAY - timedelta(days=WORKFLOW_EARNINGS_WINDOW_DAYS)}]}
    past_boundary = {"AAPL": [{"article_date": TODAY - timedelta(days=WORKFLOW_EARNINGS_WINDOW_DAYS + 1)}]}
    assert dq.classify_trade_prep(trade, None, at_boundary)["earnings_flag"] is True
    assert dq.classify_trade_prep(trade, None, past_boundary)["earnings_flag"] is False


def test_classify_trade_prep_earnings_article_after_trade_excluded():
    trade = pd.Series({"ticker": "AAPL", "traded_at": TODAY, "user_thesis": ""})
    earnings = {"AAPL": [{"article_date": TODAY + timedelta(days=1)}]}
    assert dq.classify_trade_prep(trade, None, earnings)["earnings_flag"] is False


def test_classify_trade_prep_unparseable_trade_date_leaves_flags_false():
    trade = pd.Series({"ticker": "AAPL", "traded_at": "garbage",
                        "user_thesis": "x" * WORKFLOW_MIN_THESIS_LENGTH})
    analyst_df = pd.DataFrame([_analyst_row("AAPL", TODAY - timedelta(days=10))])
    earnings = {"AAPL": [{"article_date": TODAY - timedelta(days=5)}]}
    result = dq.classify_trade_prep(trade, analyst_df, earnings)
    assert result["analyst_flag"] is False
    assert result["earnings_flag"] is False
    assert result["thesis_flag"] is True  # thesis check doesn't need trade_date


# ── classify_all_buys ────────────────────────────────────────────────────────

def test_classify_all_buys_filters_to_buy_action_only():
    df = _trades([
        _buy("AAPL", TODAY, user_thesis="x" * WORKFLOW_MIN_THESIS_LENGTH, id_=0),
        _sell("AAPL", TODAY, 100.0, id_=1),
    ])
    result = dq.classify_all_buys(df, None, {})
    assert len(result) == 1
    assert result.iloc[0]["action"] == "BUY"
    assert "tier_int" in result.columns


def test_classify_all_buys_empty_when_no_buys():
    df = _trades([_sell("AAPL", TODAY, 100.0, id_=0)])
    result = dq.classify_all_buys(df, None, {})
    assert result.empty


# ── build_workflow_roi ───────────────────────────────────────────────────────

def test_build_workflow_roi_empty_classified_buys():
    assert dq.build_workflow_roi(pd.DataFrame(), pd.DataFrame()).empty


def test_build_workflow_roi_no_closed_trades():
    classified = dq.classify_all_buys(
        _trades([_buy("AAPL", TODAY, user_thesis="x" * WORKFLOW_MIN_THESIS_LENGTH)]),
        None, {},
    )
    result = dq.build_workflow_roi(classified, _trades([_buy("AAPL", TODAY)]))
    assert result.empty


def test_build_workflow_roi_matches_sell_to_preceding_buy_tier():
    trades = _trades([
        _buy("AAPL", TODAY - timedelta(days=30),
             user_thesis="x" * WORKFLOW_MIN_THESIS_LENGTH, id_=0),
        _sell("AAPL", TODAY, 100.0, id_=1),
    ])
    classified = dq.classify_all_buys(trades, None, {})
    result = dq.build_workflow_roi(classified, trades)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["ticker"] == "AAPL"
    assert row["tier_int"] == 1  # Basic — thesis only
    assert bool(row["is_closed"]) is True
    assert row["hold_days"] == 30


def test_build_workflow_roi_no_matching_ticker_defaults_cold_entry():
    trades = _trades([
        _buy("MSFT", TODAY - timedelta(days=30),
             user_thesis="x" * WORKFLOW_MIN_THESIS_LENGTH, id_=0),
        _sell("AAPL", TODAY, 100.0, id_=1),
    ])
    classified = dq.classify_all_buys(trades, None, {})
    result = dq.build_workflow_roi(classified, trades)
    row = result.iloc[0]
    assert row["ticker"] == "AAPL"
    assert row["tier_int"] == 0
    assert row["tier_label"] == "Cold Entry"


def test_build_workflow_roi_sell_before_any_buy_uses_earliest_known_buy():
    trades = _trades([
        _buy("AAPL", TODAY, user_thesis="x" * WORKFLOW_MIN_THESIS_LENGTH, id_=0),
        _sell("AAPL", TODAY - timedelta(days=100), 100.0, id_=1),  # sell predates the buy
    ])
    classified = dq.classify_all_buys(trades, None, {})
    result = dq.build_workflow_roi(classified, trades)
    assert result.iloc[0]["tier_int"] == 1  # falls back to the only known buy's tier


def test_build_workflow_roi_pnl_pct_and_alpha_computed():
    trades = _trades([
        _buy("AAPL", TODAY - timedelta(days=30),
             user_thesis="x" * WORKFLOW_MIN_THESIS_LENGTH, id_=0),
        _sell("AAPL", TODAY, 100.0, cost_basis=10.0, shares=10.0, id_=1),
    ])
    classified = dq.classify_all_buys(trades, None, {})
    spy_prices = {
        str(TODAY - timedelta(days=30)): 100.0,
        str(TODAY): 105.0,
    }
    result = dq.build_workflow_roi(classified, trades, spy_prices=spy_prices)
    row = result.iloc[0]
    assert row["pnl_pct"] == 100.0  # 100 / (10*10) * 100
    assert row["alpha_vs_spy"] == pytest.approx(100.0 - 5.0, abs=0.01)
