"""
Tests for stock_analyzer/recommendations_history.py — the retrospective
scorecard substrate: matches every Brief-surfaced recommendation to same-day
trades, prices the outcome (realized for acted SELLs, mark-to-market for
acted BUYs, "would-have-gained" for missed names), and rolls those outcomes
up by rec_type / verdict / composite band / distinct-ticker funnel.

Previously zero test coverage despite feeding THREE user-facing surfaces:
the Recommendations History page, the F-4 Monthly Intelligence Report
(`report_viz_snapshot`, frozen into `viz_json` as an immutable artifact), and
several AI Insights alpha/entry-quality readouts (`engine_trust_by_band`).
A silent bug here doesn't just mis-render a chart — it mis-states whether the
engine's own recommendations actually beat the market, which is the single
number this whole app exists to get right. Pure logic, no I/O.
"""
from datetime import date, datetime

import pandas as pd
import pytest

from stock_analyzer import recommendations_history as rh
from stock_analyzer.constants import COMPOSITE_STRONG_BUY, COMPOSITE_BUY, COMPOSITE_HOLD


# ─── builders ───────────────────────────────────────────────────────────────

def _trade_row(ticker="AAA", traded_at="2026-01-15", trigger_type="RECOMMENDATION",
                id_=1, action="BUY", shares=10.0, price=50.0, cost_basis=500.0,
                realized_pnl=0.0):
    return {
        "id": id_, "ticker": ticker, "traded_at": traded_at, "trigger_type": trigger_type,
        "action": action, "shares": shares, "price": price, "cost_basis": cost_basis,
        "realized_pnl": realized_pnl,
    }


def _trades_df(rows):
    return pd.DataFrame(rows)


def _rec_row(id_=1, ticker="AAA", rec_date="2026-01-15", rec_type="new_pick",
             surfaced_at="2026-01-15T09:30:00", price_at_surface=50.0,
             composite_score=70.0, momentum_score=60.0, sector="Tech",
             conviction="High", verdict="Confirmed", thesis="thesis text"):
    return {
        "id": id_, "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
        "surfaced_at": surfaced_at, "price_at_surface": price_at_surface,
        "composite_score": composite_score, "momentum_score": momentum_score,
        "sector": sector, "conviction": conviction, "verdict": verdict, "thesis": thesis,
    }


def _recs_df(rows):
    return pd.DataFrame(rows)


def _matched(ticker="AAA", rec_date=date(2026, 1, 1), rec_type="new_pick",
             price_at_surface=50.0, composite_score=70.0, verdict="Confirmed",
             acted_on=False, acted_trade=None):
    return {
        "id": 1, "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
        "surfaced_at": None, "price_at_surface": price_at_surface,
        "composite_score": composite_score, "momentum_score": None,
        "sector": "Tech", "conviction": "High", "verdict": verdict, "thesis": "",
        "acted_on": acted_on, "acted_trade": acted_trade,
    }


def _trade(action="BUY", price=50.0, shares=10.0, cost_basis=500.0, realized_pnl=0.0, id_=1):
    return {"id": id_, "action": action, "shares": shares, "price": price,
            "cost_basis": cost_basis, "realized_pnl": realized_pnl, "traded_at": None}


def _erow(ticker="AAA", rec_date=date(2026, 1, 1), acted_on=False, outcome_pct=None,
          outcome_label="unknown", alpha_pct=None, outcome_maturing=False,
          rec_type="new_pick", verdict="Confirmed", composite_score=70.0,
          outcome_dollars=None):
    return {
        "ticker": ticker, "rec_date": rec_date, "acted_on": acted_on,
        "outcome_pct": outcome_pct, "outcome_label": outcome_label, "alpha_pct": alpha_pct,
        "outcome_maturing": outcome_maturing, "rec_type": rec_type, "verdict": verdict,
        "composite_score": composite_score, "outcome_dollars": outcome_dollars,
    }


def _distinct_row(ticker="AAA", outcome_pct=1.0, outcome_label="win", alpha_pct=None,
                   outcome_dollars=None):
    return {"ticker": ticker, "outcome_pct": outcome_pct, "outcome_label": outcome_label,
            "alpha_pct": alpha_pct, "outcome_dollars": outcome_dollars, "verdict": "",
            "first_rec_date": date(2026, 1, 1)}


# ─── _f ─────────────────────────────────────────────────────────────────────

def test_f_none_returns_default():
    assert rh._f(None) == 0.0
    assert rh._f(None, default=5.0) == 5.0


def test_f_nan_returns_default():
    assert rh._f(float("nan"), default=-1.0) == -1.0


def test_f_unparseable_string_returns_default():
    assert rh._f("not-a-number", default=2.0) == 2.0


def test_f_valid_value_parses():
    assert rh._f("3.5") == 3.5
    assert rh._f(3.5) == 3.5


def test_f_default_none_confirms_default_itself_can_be_none():
    assert rh._f(None, default=None) is None
    assert rh._f(float("nan"), default=None) is None
    assert rh._f("bad", default=None) is None
    assert rh._f("3.5", default=None) == 3.5  # valid input still coerces despite None default


# ─── _to_date ───────────────────────────────────────────────────────────────

def test_to_date_none_returns_none():
    assert rh._to_date(None) is None


def test_to_date_datetime_uses_date_method():
    assert rh._to_date(datetime(2026, 1, 15, 9, 30)) == date(2026, 1, 15)


def test_to_date_pandas_timestamp_uses_date_method():
    assert rh._to_date(pd.Timestamp("2026-01-15")) == date(2026, 1, 15)


def test_to_date_iso_string_without_time():
    assert rh._to_date("2026-01-15") == date(2026, 1, 15)


def test_to_date_iso_string_with_time_suffix_sliced():
    assert rh._to_date("2026-01-15T09:30:00") == date(2026, 1, 15)


def test_to_date_unparseable_string_returns_none():
    assert rh._to_date("not-a-date") is None


# ─── _spy_return_pct ────────────────────────────────────────────────────────

def test_spy_return_pct_none_series_returns_none():
    assert rh._spy_return_pct(None, date(2026, 1, 1), date(2026, 1, 10)) is None


def test_spy_return_pct_none_start_returns_none():
    assert rh._spy_return_pct({date(2026, 1, 1): 400.0}, None, date(2026, 1, 10)) is None


def test_spy_return_pct_nearest_on_or_before_lookup_not_exact_match():
    series = {
        date(2025, 12, 30): 390.0,
        date(2026, 1, 5): 400.0,
        date(2026, 1, 8): 410.0,
    }
    # start_d=2026-01-02 has no exact key -> nearest earlier = 2025-12-30 (390.0)
    # today=2026-01-10 has no exact key -> nearest earlier = 2026-01-08 (410.0)
    result = rh._spy_return_pct(series, date(2026, 1, 2), date(2026, 1, 10))
    assert result == pytest.approx((410.0 - 390.0) / 390.0 * 100.0)


def test_spy_return_pct_no_key_on_or_before_start_returns_none():
    series = {date(2026, 2, 1): 400.0}
    assert rh._spy_return_pct(series, date(2026, 1, 1), date(2026, 2, 10)) is None


def test_spy_return_pct_p0_nonpositive_returns_none():
    series = {date(2026, 1, 1): 0.0, date(2026, 1, 10): 410.0}
    assert rh._spy_return_pct(series, date(2026, 1, 1), date(2026, 1, 10)) is None


def test_spy_return_pct_normal_case_not_rounded():
    series = {date(2026, 1, 1): 100.0, date(2026, 1, 10): 103.333}
    result = rh._spy_return_pct(series, date(2026, 1, 1), date(2026, 1, 10))
    assert result == pytest.approx(3.333)


# ─── match_recs_to_trades ───────────────────────────────────────────────────

def test_match_recs_to_trades_none_recs_df_returns_empty():
    assert rh.match_recs_to_trades(None, _trades_df([])) == []


def test_match_recs_to_trades_empty_recs_df_returns_empty():
    assert rh.match_recs_to_trades(pd.DataFrame(), _trades_df([])) == []


def test_match_recs_to_trades_non_recommendation_trigger_never_matched():
    recs = _recs_df([_rec_row(ticker="AAA", rec_date="2026-01-15")])
    trades = _trades_df([_trade_row(ticker="AAA", traded_at="2026-01-15", trigger_type="MANUAL")])
    matched = rh.match_recs_to_trades(recs, trades)
    assert matched[0]["acted_on"] is False
    assert matched[0]["acted_trade"] is None


def test_match_recs_to_trades_matching_recommendation_trade_sets_acted_on():
    recs = _recs_df([_rec_row(ticker="AAA", rec_date="2026-01-15")])
    trades = _trades_df([_trade_row(
        ticker="AAA", traded_at="2026-01-15", trigger_type="RECOMMENDATION",
        id_=99, action="BUY", shares=10.0, price=55.0, cost_basis=550.0, realized_pnl=0.0,
    )])
    matched = rh.match_recs_to_trades(recs, trades)
    assert matched[0]["acted_on"] is True
    t = matched[0]["acted_trade"]
    for key in ("id", "action", "shares", "price", "cost_basis", "realized_pnl"):
        assert key in t
    assert t["id"] == 99
    assert t["action"] == "BUY"
    assert t["shares"] == 10.0
    assert t["price"] == 55.0
    assert t["cost_basis"] == 550.0
    assert t["realized_pnl"] == 0.0


def test_match_recs_to_trades_duplicate_same_day_keeps_first_inserted():
    recs = _recs_df([_rec_row(ticker="AAA", rec_date="2026-01-15")])
    trades = _trades_df([
        _trade_row(ticker="AAA", traded_at="2026-01-15", trigger_type="RECOMMENDATION",
                   id_=1, price=50.0),
        _trade_row(ticker="AAA", traded_at="2026-01-15", trigger_type="RECOMMENDATION",
                   id_=2, price=60.0),
    ])
    matched = rh.match_recs_to_trades(recs, trades)
    assert matched[0]["acted_trade"]["id"] == 1
    assert matched[0]["acted_trade"]["price"] == 50.0


def test_match_recs_to_trades_no_rec_date_never_matched():
    recs = _recs_df([_rec_row(ticker="AAA", rec_date=None)])
    trades = _trades_df([_trade_row(ticker="AAA", traded_at="2026-01-15", trigger_type="RECOMMENDATION")])
    matched = rh.match_recs_to_trades(recs, trades)
    assert matched[0]["acted_on"] is False
    assert matched[0]["rec_date"] is None


def test_match_recs_to_trades_price_at_surface_nan_normalizes_to_none():
    recs = _recs_df([_rec_row(ticker="AAA", rec_date="2026-01-15", price_at_surface=float("nan"))])
    matched = rh.match_recs_to_trades(recs, _trades_df([]))
    assert matched[0]["price_at_surface"] is None


# ─── compute_outcomes ───────────────────────────────────────────────────────

def test_compute_outcomes_acted_buy_priced():
    m = [_matched(price_at_surface=50.0, acted_on=True,
                  acted_trade=_trade(action="BUY", price=50.0, shares=10.0))]
    out = rh.compute_outcomes(m, {"AAA": 60.0}, date(2026, 1, 15))
    r = out[0]
    assert r["outcome_pct"] == pytest.approx((60.0 - 50.0) / 50.0 * 100.0)
    assert r["outcome_dollars"] == pytest.approx((60.0 - 50.0) * 10.0)


def test_compute_outcomes_acted_buy_no_current_price_both_none():
    m = [_matched(acted_on=True, acted_trade=_trade(action="BUY", price=50.0))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] is None
    assert out[0]["outcome_dollars"] is None


def test_compute_outcomes_acted_buy_zero_trade_price_both_none():
    m = [_matched(acted_on=True, acted_trade=_trade(action="BUY", price=0.0))]
    out = rh.compute_outcomes(m, {"AAA": 60.0}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] is None
    assert out[0]["outcome_dollars"] is None


def test_compute_outcomes_acted_sell_cost_basis_present():
    m = [_matched(acted_on=True,
                  acted_trade=_trade(action="SELL", cost_basis=500.0, realized_pnl=100.0))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] == pytest.approx(20.0)
    assert out[0]["outcome_dollars"] == 100.0


def test_compute_outcomes_acted_sell_case_insensitive_action():
    m = [_matched(acted_on=True,
                  acted_trade=_trade(action="sell", cost_basis=500.0, realized_pnl=50.0))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] == pytest.approx(10.0)


def test_compute_outcomes_acted_sell_cost_basis_falsy_falls_back_to_price_times_shares():
    m = [_matched(acted_on=True, acted_trade=_trade(
        action="SELL", cost_basis=0.0, price=50.0, shares=10.0, realized_pnl=50.0))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] == pytest.approx(10.0)  # 50 / (50*10) * 100


def test_compute_outcomes_acted_sell_fallback_also_nonpositive_returns_none():
    m = [_matched(acted_on=True, acted_trade=_trade(
        action="SELL", cost_basis=0.0, price=0.0, shares=10.0, realized_pnl=50.0))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] is None


def test_compute_outcomes_missed_priced():
    m = [_matched(price_at_surface=50.0, acted_on=False)]
    out = rh.compute_outcomes(m, {"AAA": 55.0}, date(2026, 1, 15))
    r = out[0]
    pct = (55.0 - 50.0) / 50.0 * 100.0
    assert r["outcome_pct"] == pytest.approx(pct)
    assert r["outcome_dollars"] == pytest.approx(pct / 100.0 * 1000.0)  # normalized to $1k notional


def test_compute_outcomes_missed_no_price_at_surface_returns_none():
    m = [_matched(price_at_surface=None, acted_on=False)]
    out = rh.compute_outcomes(m, {"AAA": 55.0}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] is None
    assert out[0]["outcome_dollars"] is None


def test_compute_outcomes_missed_price_at_surface_nonpositive_returns_none():
    m = [_matched(price_at_surface=0.0, acted_on=False)]
    out = rh.compute_outcomes(m, {"AAA": 55.0}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] is None


def test_compute_outcomes_missed_no_current_price_returns_none():
    m = [_matched(price_at_surface=50.0, acted_on=False)]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15))
    assert out[0]["outcome_pct"] is None


def test_compute_outcomes_label_win_boundary_at_half_pct():
    m = [_matched(price_at_surface=100.0, acted_on=False)]
    out_at = rh.compute_outcomes(m, {"AAA": 100.5}, date(2026, 1, 15))
    assert out_at[0]["outcome_pct"] == pytest.approx(0.5)
    assert out_at[0]["outcome_label"] == "flat"  # exactly 0.5 is NOT a win

    out_above = rh.compute_outcomes(m, {"AAA": 100.50001}, date(2026, 1, 15))
    assert out_above[0]["outcome_label"] == "win"


def test_compute_outcomes_label_loss_boundary_at_negative_half_pct():
    m = [_matched(price_at_surface=100.0, acted_on=False)]
    out_at = rh.compute_outcomes(m, {"AAA": 99.5}, date(2026, 1, 15))
    assert out_at[0]["outcome_pct"] == pytest.approx(-0.5)
    assert out_at[0]["outcome_label"] == "flat"  # exactly -0.5 is NOT a loss

    out_below = rh.compute_outcomes(m, {"AAA": 99.49999}, date(2026, 1, 15))
    assert out_below[0]["outcome_label"] == "loss"


def test_compute_outcomes_label_unknown_when_outcome_pct_none():
    m = [_matched(price_at_surface=None, acted_on=False)]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15))
    assert out[0]["outcome_label"] == "unknown"


def test_compute_outcomes_days_since_computed_from_rec_date():
    m = [_matched(rec_date=date(2026, 1, 1))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 11))
    assert out[0]["days_since"] == 10


def test_compute_outcomes_days_since_none_when_rec_date_none():
    m = [_matched(rec_date=None)]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 11))
    assert out[0]["days_since"] is None


def test_compute_outcomes_sell_acted_forces_spy_and_alpha_none_even_with_valid_spy():
    spy = {date(2026, 1, 1): 100.0, date(2026, 1, 15): 110.0}
    m = [_matched(rec_date=date(2026, 1, 1), acted_on=True,
                  acted_trade=_trade(action="SELL", cost_basis=500.0, realized_pnl=100.0))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15), spy_close_by_date=spy)
    assert out[0]["spy_return_pct"] is None
    assert out[0]["alpha_pct"] is None


def test_compute_outcomes_non_sell_spy_return_and_alpha_computed():
    spy = {date(2026, 1, 1): 100.0, date(2026, 1, 15): 110.0}
    m = [_matched(rec_date=date(2026, 1, 1), price_at_surface=50.0, acted_on=False)]
    out = rh.compute_outcomes(m, {"AAA": 55.0}, date(2026, 1, 15), spy_close_by_date=spy)
    r = out[0]
    assert r["spy_return_pct"] == pytest.approx(10.0)
    assert r["alpha_pct"] == pytest.approx(round(r["outcome_pct"] - 10.0, 2))


def test_compute_outcomes_alpha_none_when_spy_series_missing():
    m = [_matched(rec_date=date(2026, 1, 1), price_at_surface=50.0, acted_on=False)]
    out = rh.compute_outcomes(m, {"AAA": 55.0}, date(2026, 1, 15), spy_close_by_date=None)
    assert out[0]["spy_return_pct"] is None
    assert out[0]["alpha_pct"] is None


def test_compute_outcomes_maturing_below_min_days_true():
    m = [_matched(rec_date=date(2026, 1, 10))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15), min_days=10)  # days_since=5 < 10
    assert out[0]["outcome_maturing"] is True


def test_compute_outcomes_maturing_exactly_at_min_days_false():
    m = [_matched(rec_date=date(2026, 1, 5))]
    out = rh.compute_outcomes(m, {}, date(2026, 1, 15), min_days=10)  # days_since=10 == min_days
    assert out[0]["outcome_maturing"] is False


def test_compute_outcomes_does_not_mutate_caller_input():
    original = _matched()
    assert "outcome_pct" not in original
    out = rh.compute_outcomes([original], {}, date(2026, 1, 15))
    assert out[0] is not original
    out[0]["outcome_pct"] = 999.0
    assert "outcome_pct" not in original


# ─── summary_stats ──────────────────────────────────────────────────────────

def test_summary_stats_counts_include_maturing_rows():
    rows = [
        _erow(acted_on=True, outcome_maturing=True),
        _erow(acted_on=False, outcome_maturing=False, outcome_pct=1.0, outcome_label="win"),
    ]
    stats = rh.summary_stats(rows)
    assert stats["n_total"] == 2
    assert stats["n_acted"] == 1
    assert stats["n_maturing"] == 1


def test_summary_stats_priced_excludes_maturing_even_if_priced():
    rows = [
        _erow(ticker="AAA", acted_on=True, outcome_maturing=True, outcome_pct=5.0, outcome_label="win"),
        _erow(ticker="BBB", acted_on=True, outcome_maturing=False, outcome_pct=10.0, outcome_label="win"),
    ]
    stats = rh.summary_stats(rows)
    assert stats["n_total"] == 2
    assert stats["n_priced"] == 1
    assert stats["n_wins"] == 1
    assert stats["avg_acted_pct"] == 10.0


def test_summary_stats_action_rate_none_when_zero_total():
    assert rh.summary_stats([])["action_rate"] is None


def test_summary_stats_averages_none_when_subset_empty():
    rows = [_erow(acted_on=False, outcome_pct=None, outcome_label="unknown")]
    stats = rh.summary_stats(rows)
    assert stats["avg_acted_pct"] is None
    assert stats["avg_missed_pct"] is None
    assert stats["avg_acted_alpha"] is None
    assert stats["avg_missed_alpha"] is None
    assert stats["avg_alpha"] is None
    assert stats["missed_alpha"] is None


def test_summary_stats_missed_alpha_only_when_both_averages_present():
    rows = [
        _erow(ticker="AAA", acted_on=True, outcome_pct=10.0, outcome_label="win", alpha_pct=2.0),
        _erow(ticker="BBB", acted_on=False, outcome_pct=20.0, outcome_label="win", alpha_pct=5.0),
    ]
    stats = rh.summary_stats(rows)
    assert stats["avg_acted_pct"] == 10.0
    assert stats["avg_missed_pct"] == 20.0
    assert stats["missed_alpha"] == 10.0  # positive = leaving money on the table


def test_summary_stats_best_worst_selected_by_outcome_pct():
    rows = [
        _erow(ticker="AAA", outcome_pct=5.0, outcome_label="win", alpha_pct=1.0, acted_on=True),
        _erow(ticker="BBB", outcome_pct=-10.0, outcome_label="loss", alpha_pct=-3.0, acted_on=False),
    ]
    stats = rh.summary_stats(rows)
    assert stats["best"]["ticker"] == "AAA"
    assert stats["best"]["outcome_pct"] == 5.0
    assert stats["worst"]["ticker"] == "BBB"
    assert stats["worst"]["outcome_pct"] == -10.0


def test_summary_stats_empty_input_no_crash_all_defaults():
    stats = rh.summary_stats([])
    assert stats["n_total"] == 0
    assert stats["n_acted"] == 0
    assert stats["n_maturing"] == 0
    assert stats["action_rate"] is None
    assert stats["n_priced"] == 0
    assert stats["best"] is None
    assert stats["worst"] is None


# ─── by_rec_type ────────────────────────────────────────────────────────────

def test_by_rec_type_groups_by_literal_string_value():
    rows = [
        _erow(ticker="AAA", rec_type="new_pick", acted_on=True, outcome_pct=5.0, outcome_label="win"),
        _erow(ticker="BBB", rec_type="buy_candidate", acted_on=False, outcome_pct=-5.0, outcome_label="loss"),
    ]
    out = rh.by_rec_type(rows)
    assert set(out.keys()) == {"new_pick", "buy_candidate"}
    assert out["new_pick"]["n_total"] == 1
    assert out["buy_candidate"]["n_total"] == 1


# ─── _verdict_bucket ────────────────────────────────────────────────────────

def test_verdict_bucket_confirm():
    assert rh._verdict_bucket("Confirmed") == "Engine-Confirmed"


def test_verdict_bucket_conflict():
    assert rh._verdict_bucket("Conflicted") == "Conflicted"


def test_verdict_bucket_caution():
    assert rh._verdict_bucket("Caution") == "Caution"


def test_verdict_bucket_mixed():
    assert rh._verdict_bucket("Mixed signal") == "Mixed"


def test_verdict_bucket_unverified_or_verify_substring():
    assert rh._verdict_bucket("Unverified") == "Unverified"
    assert rh._verdict_bucket("Needs to verify") == "Unverified"


def test_verdict_bucket_none_or_blank_falls_to_other():
    assert rh._verdict_bucket(None) == "Other / blank"
    assert rh._verdict_bucket("") == "Other / blank"
    assert rh._verdict_bucket("   ") == "Other / blank"


def test_verdict_bucket_priority_order_confirm_checked_before_caution():
    # A value matching multiple substrings resolves to whichever is checked first.
    assert rh._verdict_bucket("Confirmed but caution ahead") == "Engine-Confirmed"


# ─── by_verdict ─────────────────────────────────────────────────────────────

def test_by_verdict_orders_per_verdict_order_and_skips_empty_buckets():
    rows = [
        _erow(ticker="AAA", verdict="Mixed signal", outcome_pct=1.0, outcome_label="win", acted_on=True),
        _erow(ticker="BBB", verdict="Confirmed", outcome_pct=2.0, outcome_label="win", acted_on=True),
    ]
    out = rh.by_verdict(rows)
    assert [o["verdict"] for o in out] == ["Engine-Confirmed", "Mixed"]  # only 2 of 6 buckets populated


# ─── by_composite_band ──────────────────────────────────────────────────────

def test_by_composite_band_boundary_at_composite_strong_buy():
    rows = [
        _erow(ticker="AAA", composite_score=COMPOSITE_STRONG_BUY - 0.001),
        _erow(ticker="BBB", composite_score=float(COMPOSITE_STRONG_BUY)),
    ]
    out = rh.by_composite_band(rows)
    bands = {b["band"]: b["n_total"] for b in out}
    buy_label = next(b for b in bands if b.startswith("Buy ("))
    strong_label = next(b for b in bands if b.startswith("Strong Buy"))
    assert bands[buy_label] == 1
    assert bands[strong_label] == 1


def test_by_composite_band_boundary_at_composite_buy():
    rows = [
        _erow(ticker="AAA", composite_score=COMPOSITE_BUY - 0.001),   # Hold zone
        _erow(ticker="BBB", composite_score=float(COMPOSITE_BUY)),    # Buy
    ]
    out = rh.by_composite_band(rows)
    bands = {b["band"]: b["n_total"] for b in out}
    hold_label = next(b for b in bands if b.startswith("Hold zone"))
    buy_label = next(b for b in bands if b.startswith("Buy ("))
    assert bands[hold_label] == 1
    assert bands[buy_label] == 1


def test_by_composite_band_boundary_at_composite_hold():
    rows = [
        _erow(ticker="AAA", composite_score=COMPOSITE_HOLD - 0.001),  # Sell zone
        _erow(ticker="BBB", composite_score=float(COMPOSITE_HOLD)),   # Hold zone
    ]
    out = rh.by_composite_band(rows)
    bands = {b["band"]: b["n_total"] for b in out}
    sell_label = next(b for b in bands if b.startswith("Sell zone"))
    hold_label = next(b for b in bands if b.startswith("Hold zone"))
    assert bands[sell_label] == 1
    assert bands[hold_label] == 1


def test_by_composite_band_none_score_goes_to_unscored():
    rows = [_erow(ticker="AAA", composite_score=None)]
    out = rh.by_composite_band(rows)
    assert out[0]["band"] == "Unscored"


def test_by_composite_band_empty_matching_band_omitted():
    rows = [_erow(ticker="AAA", composite_score=80.0)]
    out = rh.by_composite_band(rows)
    assert len(out) == 1
    assert out[0]["band"].startswith("Strong Buy")


# ─── distinct_missed ────────────────────────────────────────────────────────

def test_distinct_missed_ticker_excluded_if_acted_via_any_surfacing():
    rows = [
        _erow(ticker="AAA", rec_type="buy_candidate", acted_on=True, outcome_pct=5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1)),
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 2)),
    ]
    out = rh.distinct_missed(rows, rec_types=("new_pick",))
    assert out == []


def test_distinct_missed_n_surfaced_counts_pool_not_gradable_subset():
    rows = [
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), alpha_pct=1.0),
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=None,
              outcome_maturing=False, rec_date=date(2026, 1, 5)),
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=6.0,
              outcome_maturing=True, rec_date=date(2026, 1, 8)),
    ]
    out = rh.distinct_missed(rows, rec_types=("new_pick",))
    assert len(out) == 1
    assert out[0]["n_surfaced"] == 3  # pool size, not the 1 gradable row


def test_distinct_missed_representative_is_earliest_gradable_not_highest_outcome():
    rows = [
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=1.0,
              outcome_maturing=False, rec_date=date(2026, 1, 10), alpha_pct=0.5),
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=20.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), alpha_pct=10.0),
    ]
    out = rh.distinct_missed(rows, rec_types=("new_pick",))
    assert out[0]["first_rec_date"] == date(2026, 1, 1)
    assert out[0]["outcome_pct"] == 20.0  # earliest row's outcome, not the highest one


def test_distinct_missed_sort_desc_alpha_or_per_row_outcome_fallback():
    rows = [
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), alpha_pct=None),
        _erow(ticker="BBB", rec_type="new_pick", acted_on=False, outcome_pct=1.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), alpha_pct=10.0),
        _erow(ticker="CCC", rec_type="new_pick", acted_on=False, outcome_pct=3.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), alpha_pct=None),
    ]
    out = rh.distinct_missed(rows, rec_types=("new_pick",))
    # BBB sorts by alpha=10 (highest); AAA and CCC have no alpha, fall back to outcome_pct (5, 3).
    assert [r["ticker"] for r in out] == ["BBB", "AAA", "CCC"]


def test_distinct_missed_no_gradable_surfacing_excluded():
    rows = [
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=None,
              outcome_maturing=False, rec_date=date(2026, 1, 1)),
    ]
    assert rh.distinct_missed(rows, rec_types=("new_pick",)) == []


# ─── missed_split ───────────────────────────────────────────────────────────

def test_missed_split_win_loss_flat_buckets():
    rows = [
        _distinct_row(ticker="AAA", outcome_pct=10.0, outcome_label="win"),
        _distinct_row(ticker="BBB", outcome_pct=-10.0, outcome_label="loss"),
        _distinct_row(ticker="CCC", outcome_pct=0.1, outcome_label="flat"),
    ]
    out = rh.missed_split(rows)
    assert out["n_winners"] == 1
    assert out["n_dodged"] == 1
    assert out["n_flat"] == 1
    assert out["n_distinct"] == 3


def test_missed_split_unknown_label_lands_in_no_bucket():
    rows = [_distinct_row(ticker="AAA", outcome_pct=5.0, outcome_label="unknown")]
    out = rh.missed_split(rows)
    assert out["n_winners"] == 0
    assert out["n_dodged"] == 0
    assert out["n_flat"] == 0
    assert out["n_distinct"] == 1


def test_missed_split_biggest_miss_and_dodge_span_all_rows():
    rows = [
        _distinct_row(ticker="AAA", outcome_pct=15.0, outcome_label="win"),
        _distinct_row(ticker="BBB", outcome_pct=-20.0, outcome_label="loss"),
        _distinct_row(ticker="CCC", outcome_pct=2.0, outcome_label="flat"),
    ]
    out = rh.missed_split(rows)
    assert out["biggest_miss"]["ticker"] == "AAA"
    assert out["biggest_dodge"]["ticker"] == "BBB"


def test_missed_split_empty_input_no_crash():
    out = rh.missed_split([])
    assert out["n_distinct"] == 0
    assert out["n_winners"] == 0
    assert out["biggest_miss"] is None
    assert out["biggest_dodge"] is None
    assert out["avg_winner_pct"] is None


# ─── signal_flow ────────────────────────────────────────────────────────────

def test_signal_flow_acted_tickers_built_from_unscoped_enriched():
    # AAA acted via an OUT-of-scope buy_candidate row; its IN-scope new_pick
    # row is acted_on=False. It must still count as acted, not missed.
    rows = [
        _erow(ticker="AAA", rec_type="buy_candidate", acted_on=True, outcome_pct=None,
              outcome_maturing=False, rec_date=date(2026, 1, 1)),
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 2), outcome_label="win"),
    ]
    out = rh.signal_flow(rows, rec_types=("new_pick",))
    assert out["n_total"] == 1
    assert out["n_acted"] == 1
    assert out["n_missed"] == 0


def test_signal_flow_bucket_falls_back_to_any_mature_row_when_none_acted():
    # Same cross-scope setup: the only in-scope row for AAA is acted_on=False,
    # so the acted-only pool is empty and _bucket must fall back to `mature`.
    rows = [
        _erow(ticker="AAA", rec_type="buy_candidate", acted_on=True, outcome_pct=None,
              outcome_maturing=False, rec_date=date(2026, 1, 1)),
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 2), outcome_label="win"),
    ]
    out = rh.signal_flow(rows, rec_types=("new_pick",))
    assert out["acted_win"] == 1


def test_signal_flow_basic_scoped_counts():
    rows = [
        _erow(ticker="AAA", rec_type="new_pick", acted_on=True, outcome_pct=5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), outcome_label="win"),
        _erow(ticker="BBB", rec_type="new_pick", acted_on=False, outcome_pct=-5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), outcome_label="loss"),
        _erow(ticker="CCC", rec_type="buy_candidate", acted_on=False, outcome_pct=1.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), outcome_label="flat"),
    ]
    out = rh.signal_flow(rows, rec_types=("new_pick",))
    assert out["n_total"] == 2  # CCC scoped out entirely
    assert out["n_acted"] == 1
    assert out["n_missed"] == 1
    assert out["acted_win"] == 1
    assert out["missed_loss"] == 1


# ─── report_viz_snapshot ────────────────────────────────────────────────────

def test_report_viz_snapshot_bands_excludes_none_avg_alpha():
    rows = [
        _erow(ticker="AAA", rec_type="new_pick", composite_score=80.0, acted_on=True,
              outcome_pct=5.0, alpha_pct=2.0, outcome_maturing=False, rec_date=date(2026, 1, 1),
              outcome_label="win"),
        _erow(ticker="BBB", rec_type="new_pick", composite_score=None, acted_on=False,
              outcome_pct=None, alpha_pct=None, outcome_maturing=False, rec_date=date(2026, 1, 2),
              outcome_label="unknown"),
    ]
    out = rh.report_viz_snapshot(rows, rec_types=("new_pick",))
    band_names = [b["band"] for b in out["bands"]]
    assert "Unscored" not in band_names  # avg_alpha is None for the Unscored bucket -> excluded
    assert any(b.startswith("Strong Buy") for b in band_names)


def test_report_viz_snapshot_missed_split_only_json_safe_scalar_keys():
    rows = [
        _erow(ticker="AAA", rec_type="new_pick", acted_on=False, outcome_pct=5.0,
              outcome_maturing=False, rec_date=date(2026, 1, 1), outcome_label="win", alpha_pct=1.0),
    ]
    out = rh.report_viz_snapshot(rows, rec_types=("new_pick",))
    assert set(out["missed_split"].keys()) == {"n_distinct", "n_winners", "n_dodged", "n_flat"}


# ─── engine_trust_by_band ───────────────────────────────────────────────────

def test_engine_trust_by_band_skips_maturing_rows_entirely():
    # Differs from summary_stats: maturing rows aren't even counted in n_recs.
    rows = [
        _erow(ticker="AAA", composite_score=80.0, acted_on=True, outcome_maturing=True,
              outcome_pct=5.0, alpha_pct=1.0),
        _erow(ticker="BBB", composite_score=80.0, acted_on=True, outcome_maturing=False,
              outcome_pct=5.0, alpha_pct=1.0),
    ]
    out = rh.engine_trust_by_band(rows)
    assert out[0]["n_recs"] == 1


def test_engine_trust_by_band_none_composite_skipped_not_bucketed():
    rows = [_erow(ticker="AAA", composite_score=None, outcome_maturing=False)]
    assert rh.engine_trust_by_band(rows) == []


def test_engine_trust_by_band_boundaries_at_composite_buy_and_strong_buy():
    rows = [
        _erow(ticker="AAA", composite_score=COMPOSITE_BUY - 0.001, outcome_maturing=False),
        _erow(ticker="BBB", composite_score=float(COMPOSITE_BUY), outcome_maturing=False),
        _erow(ticker="CCC", composite_score=COMPOSITE_STRONG_BUY - 0.001, outcome_maturing=False),
        _erow(ticker="DDD", composite_score=float(COMPOSITE_STRONG_BUY), outcome_maturing=False),
    ]
    out = rh.engine_trust_by_band(rows)
    labels = {b["band_label"]: b["n_recs"] for b in out}
    assert labels[f"Below {COMPOSITE_BUY} (sub-threshold)"] == 1
    assert labels[f"{COMPOSITE_BUY}–{COMPOSITE_STRONG_BUY - 1} (BUY)"] == 2
    assert labels[f"{COMPOSITE_STRONG_BUY}+ (Strong BUY)"] == 1


def test_engine_trust_by_band_edge_comment_acted_beat_passed():
    rows = [
        _erow(ticker="AAA", composite_score=80.0, acted_on=True, alpha_pct=5.0, outcome_maturing=False),
        _erow(ticker="BBB", composite_score=80.0, acted_on=False, alpha_pct=1.0, outcome_maturing=False),
    ]
    out = rh.engine_trust_by_band(rows)
    assert out[0]["avg_alpha_acted"] == 5.0
    assert out[0]["avg_alpha_passed"] == 1.0
    assert out[0]["edge_comment"].startswith("Acting on this band delivered")


def test_engine_trust_by_band_edge_comment_passed_beat_acted():
    rows = [
        _erow(ticker="AAA", composite_score=80.0, acted_on=True, alpha_pct=1.0, outcome_maturing=False),
        _erow(ticker="BBB", composite_score=80.0, acted_on=False, alpha_pct=5.0, outcome_maturing=False),
    ]
    out = rh.engine_trust_by_band(rows)
    assert out[0]["edge_comment"].startswith("Passing on this band outperformed acting")


def test_engine_trust_by_band_edge_comment_equal_alpha():
    rows = [
        _erow(ticker="AAA", composite_score=80.0, acted_on=True, alpha_pct=3.0, outcome_maturing=False),
        _erow(ticker="BBB", composite_score=80.0, acted_on=False, alpha_pct=3.0, outcome_maturing=False),
    ]
    out = rh.engine_trust_by_band(rows)
    assert out[0]["edge_comment"] == "Acting and passing produced similar alpha — no clear edge signal."


def test_engine_trust_by_band_edge_comment_only_acted_present():
    rows = [_erow(ticker="AAA", composite_score=80.0, acted_on=True, alpha_pct=3.0, outcome_maturing=False)]
    out = rh.engine_trust_by_band(rows)
    assert out[0]["edge_comment"] == "Acted rows: avg +3.0pp alpha. (No passed rows with outcomes to compare.)"


def test_engine_trust_by_band_edge_comment_only_passed_present():
    rows = [_erow(ticker="AAA", composite_score=80.0, acted_on=False, alpha_pct=3.0, outcome_maturing=False)]
    out = rh.engine_trust_by_band(rows)
    assert out[0]["edge_comment"] == "Passed rows: avg +3.0pp alpha. (No acted rows with outcomes to compare.)"


def test_engine_trust_by_band_edge_comment_insufficient_data():
    rows = [_erow(ticker="AAA", composite_score=80.0, acted_on=True, alpha_pct=None, outcome_maturing=False)]
    out = rh.engine_trust_by_band(rows)
    assert out[0]["edge_comment"] == "Insufficient outcome data to draw conclusions."


# ─── daily_volume ───────────────────────────────────────────────────────────

def test_daily_volume_groups_by_date_skips_none_and_sorts():
    rows = [
        _erow(ticker="AAA", rec_date=date(2026, 1, 1), acted_on=True),
        _erow(ticker="BBB", rec_date=date(2026, 1, 1), acted_on=False),
        _erow(ticker="CCC", rec_date=date(2026, 1, 2), acted_on=True),
        _erow(ticker="DDD", rec_date=None, acted_on=True),
    ]
    out = rh.daily_volume(rows)
    assert len(out) == 2
    assert [d["date"] for d in out] == [date(2026, 1, 1), date(2026, 1, 2)]
    day1 = out[0]
    assert day1["total"] == 2
    assert day1["acted"] == 1
    assert day1["missed"] == 1
    day2 = out[1]
    assert day2["total"] == 1
    assert day2["acted"] == 1
    assert day2["missed"] == 0


# ─── engine_trust_headline ───────────────────────────────────────────────────

def test_engine_trust_headline_empty_input_returns_building():
    out = rh.engine_trust_headline([], 8, 15)
    assert out["band"] == "building"
    assert out["acted_alpha"] is None
    assert out["missed_alpha"] is None
    assert out["n_acted_mature"] == 0
    assert out["since_date"] is None


def test_engine_trust_headline_no_new_pick_rows_returns_building():
    """buy_candidate and add_winner rows are excluded; with none left → building."""
    rows = [
        _erow(rec_type="buy_candidate", acted_on=True, outcome_maturing=False, alpha_pct=3.0),
        _erow(rec_type="add_winner",    acted_on=True, outcome_maturing=False, alpha_pct=2.0),
    ]
    out = rh.engine_trust_headline(rows, 8, 15)
    assert out["band"] == "building"
    assert out["n_acted_mature"] == 0


def test_engine_trust_headline_buy_candidate_add_winner_ignored_in_scoping():
    """new_pick rows are counted; buy_candidate/add_winner don't inflate n_acted_mature."""
    np_rows = [
        _erow(ticker="A", rec_type="new_pick",     acted_on=True,  outcome_maturing=False, alpha_pct=5.0,  outcome_pct=5.0, outcome_label="win", rec_date=date(2026, 1, 1)),
        _erow(ticker="B", rec_type="new_pick",     acted_on=True,  outcome_maturing=False, alpha_pct=3.0,  outcome_pct=3.0, outcome_label="win", rec_date=date(2026, 1, 2)),
        _erow(ticker="C", rec_type="buy_candidate",acted_on=True,  outcome_maturing=False, alpha_pct=10.0, rec_date=date(2026, 1, 3)),
        _erow(ticker="D", rec_type="add_winner",   acted_on=True,  outcome_maturing=False, alpha_pct=8.0,  rec_date=date(2026, 1, 4)),
    ]
    # Only 2 new_pick acted mature priced rows → below min_calls=8 → building
    out = rh.engine_trust_headline(np_rows, 8, 15)
    assert out["band"] == "building"
    assert out["n_acted_mature"] == 2


def test_engine_trust_headline_building_band_when_below_min_calls():
    rows = [
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, alpha_pct=5.0,
              outcome_pct=5.0, outcome_label="win", rec_date=date(2026, 1, i))
        for i in range(1, 8)   # 7 rows — below min_calls=8
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["band"] == "building"
    assert out["n_acted_mature"] == 7


def test_engine_trust_headline_early_band_at_min_calls_boundary():
    rows = [
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, alpha_pct=5.0,
              outcome_pct=5.0, outcome_label="win", rec_date=date(2026, 1, i))
        for i in range(1, 9)   # exactly 8 rows — at min_calls=8 → early
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["band"] == "early"
    assert out["n_acted_mature"] == 8


def test_engine_trust_headline_early_band_below_firm_calls():
    rows = [
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, alpha_pct=5.0,
              outcome_pct=5.0, outcome_label="win", rec_date=date(2026, 1, i))
        for i in range(1, 15)   # 14 rows — above min=8, below firm=15 → early
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["band"] == "early"
    assert out["n_acted_mature"] == 14


def test_engine_trust_headline_firm_band_at_firm_calls_boundary():
    rows = [
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, alpha_pct=5.0,
              outcome_pct=5.0, outcome_label="win", rec_date=date(2026, 1, i))
        for i in range(1, 16)   # exactly 15 rows — at firm_calls=15 → firm
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["band"] == "firm"
    assert out["n_acted_mature"] == 15


def test_engine_trust_headline_firm_band_above_firm_calls():
    rows = [
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, alpha_pct=5.0,
              outcome_pct=5.0, outcome_label="win", rec_date=date(2026, 1, i))
        for i in range(1, 20)   # 19 rows — above firm_calls=15 → firm
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["band"] == "firm"
    assert out["n_acted_mature"] == 19


def test_engine_trust_headline_alpha_values_from_summary_stats():
    """acted_alpha and missed_alpha are sourced from summary_stats, not reimplemented."""
    rows = [
        # 10 acted, mature, with computable alpha
        _erow(rec_type="new_pick", acted_on=True,  outcome_maturing=False,
              alpha_pct=6.0,  outcome_pct=8.0, outcome_label="win",
              rec_date=date(2026, 1, i))
        for i in range(1, 11)
    ] + [
        # 5 missed, mature, with computable alpha
        _erow(ticker=f"M{i}", rec_type="new_pick", acted_on=False, outcome_maturing=False,
              alpha_pct=-2.0, outcome_pct=-1.0, outcome_label="loss",
              rec_date=date(2026, 1, i))
        for i in range(1, 6)
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["band"] == "early"   # 10 acted mature (priced) → ≥8, <15
    assert out["acted_alpha"] == pytest.approx(6.0)
    assert out["missed_alpha"] == pytest.approx(-2.0)


def test_engine_trust_headline_maturing_rows_excluded_from_n_acted_mature():
    """Maturing rows (outcome_maturing=True) do not count toward n_acted_mature."""
    rows = [
        # 7 mature acted WITH priced outcomes
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, alpha_pct=5.0,
              outcome_pct=5.0, outcome_label="win", rec_date=date(2026, 1, i))
        for i in range(1, 8)
    ] + [
        # 5 still-maturing acted — should NOT count even if priced
        _erow(ticker=f"NEW{i}", rec_type="new_pick", acted_on=True, outcome_maturing=True,
              alpha_pct=None, outcome_pct=None, rec_date=date(2026, 1, i))
        for i in range(8, 13)
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["n_acted_mature"] == 7    # only the non-maturing+priced ones
    assert out["band"] == "building"     # 7 < min_calls=8


def test_engine_trust_headline_since_date_is_earliest_rec_date():
    rows = [
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, rec_date=date(2026, 3, 15)),
        _erow(ticker="B", rec_type="new_pick", acted_on=False, outcome_maturing=False, rec_date=date(2026, 1, 5)),
        _erow(ticker="C", rec_type="new_pick", acted_on=True, outcome_maturing=False, rec_date=date(2026, 2, 20)),
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["since_date"] == date(2026, 1, 5)


def test_engine_trust_headline_none_rec_date_handled_safely():
    """Rows with rec_date=None are excluded from since_date without crashing."""
    rows = [
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, rec_date=None),
        _erow(ticker="B", rec_type="new_pick", acted_on=True, outcome_maturing=False, rec_date=date(2026, 6, 1)),
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["since_date"] == date(2026, 6, 1)   # only the non-None date


def test_engine_trust_headline_all_rows_have_none_rec_date():
    rows = [
        _erow(rec_type="new_pick", acted_on=True, outcome_maturing=False, rec_date=None),
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    assert out["since_date"] is None


def test_engine_trust_headline_acted_unpriced_excluded_from_n_acted_mature():
    """Acted rows without a priced outcome (outcome_pct is None) do not count toward
    n_acted_mature, so they cannot inflate the band or the caption count beyond what
    the alpha is actually computed from (BLOCKING 3 alignment)."""
    rows = [
        # 10 acted + mature + PRICED → n_acted_mature=10, band=early (≥8, <15)
        _erow(ticker=f"P{i}", rec_type="new_pick", acted_on=True, outcome_maturing=False,
              alpha_pct=4.0, outcome_pct=5.0, outcome_label="win",
              rec_date=date(2026, 1, i))
        for i in range(1, 11)
    ] + [
        # 6 acted + mature but UNPRICED — must NOT push the count to 16 (firm territory)
        _erow(ticker=f"U{i}", rec_type="new_pick", acted_on=True, outcome_maturing=False,
              alpha_pct=None, outcome_pct=None, outcome_label="unknown",
              rec_date=date(2026, 1, i))
        for i in range(1, 7)
    ]
    out = rh.engine_trust_headline(rows, min_calls=8, firm_calls=15)
    # 10 priced ≥ min_calls=8 but < firm_calls=15 → early (not firm despite 16 total rows)
    assert out["band"] == "early"
    assert out["n_acted_mature"] == 10
    # Alpha computed only over the 10 priced rows
    assert out["acted_alpha"] == pytest.approx(4.0)
