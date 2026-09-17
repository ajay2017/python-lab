"""
stock_analyzer/rec_events_readout.py — Recommendation-Outcomes-Measurement
Phase 1b readout half (docs/plans/recommendation-outcomes-measurement.md
§10/§11).

Covers every item in the implementer's own "Tests required" list: attribution
invariants (named-ticker/direction/window/candidate-membership),
action-window + maturity boundaries, trigger_type non-collision, collapse,
cross-system overlap, band boundaries, and the db offline contract (see
tests/test_db_rec_events.py for that last one).
"""
from __future__ import annotations

import datetime

import pytest

from stock_analyzer import rec_events_readout as ro

pytestmark = pytest.mark.fast

TODAY = datetime.date(2026, 9, 17)


def _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-01", **kw):
    row = {
        "rec_type": rec_type, "ticker": ticker, "sector": None,
        "fired_date": fired_date, "source": "cron",
        "metric_name": "portfolio_beta", "metric_before": 1.5,
        "metric_predicted_after": 1.3, "rec_dollars": 1000.0,
        "price_at_rec": None, "candidates": None, "corr_coverage_n": None,
    }
    row.update(kw)
    return row


def _trade(ticker="AAA", action="SELL", traded_at="2026-08-03", trigger_type=None):
    # traded_at is a timestamptz in real rows — always carries a mid-day UTC
    # time here so _traded_date_et's UTC->ET conversion never rolls the
    # calendar date backward the way a bare midnight-UTC date string would
    # (a bare "YYYY-MM-DD" is parsed as 00:00 UTC, which is still the
    # PREVIOUS day in America/New_York).
    ta = traded_at if "T" in str(traded_at) else f"{traded_at}T15:00:00+00:00"
    t = {"ticker": ticker, "action": action, "traded_at": ta}
    if trigger_type is not None:
        t["trigger_type"] = trigger_type
    return t


# ── Attribution ──────────────────────────────────────────────────────────────

def test_named_ticker_correct_direction_in_window_credits_acted():
    rec = _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-01")
    out = ro.match_attribution(rec, [_trade(ticker="AAA", action="SELL", traded_at="2026-08-03")], 10)
    assert out["acted"] is True
    assert out["matched_trade"]["ticker"] == "AAA"


def test_different_ticker_never_credits_the_specific_rec():
    rec = _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-01")
    out = ro.match_attribution(rec, [_trade(ticker="BBB", action="SELL", traded_at="2026-08-03")], 10)
    assert out["acted"] is False


def test_same_ticker_wrong_direction_not_acted():
    rec = _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-01")
    out = ro.match_attribution(rec, [_trade(ticker="AAA", action="BUY", traded_at="2026-08-03")], 10)
    assert out["acted"] is False


def test_diversify_add_requires_buy_direction():
    rec = _rec(rec_type="diversify_add", ticker="UNH", fired_date="2026-08-01",
                candidates=["UNH", "JNJ"])
    out_sell = ro.match_attribution(rec, [_trade(ticker="UNH", action="SELL", traded_at="2026-08-03")], 10)
    assert out_sell["acted"] is False
    out_buy = ro.match_attribution(rec, [_trade(ticker="UNH", action="BUY", traded_at="2026-08-03")], 10)
    assert out_buy["acted"] is True


def test_diversify_add_candidate_membership_required():
    """A BUY of the named ticker whose OWN candidates jsonb doesn't list it
    (a malformed/corrupted row) must NOT be credited — defense in depth."""
    rec = _rec(rec_type="diversify_add", ticker="UNH", fired_date="2026-08-01",
                candidates=["JNJ", "ABT"])  # UNH deliberately absent
    out = ro.match_attribution(rec, [_trade(ticker="UNH", action="BUY", traded_at="2026-08-03")], 10)
    assert out["acted"] is False


# ── Action-window boundary ───────────────────────────────────────────────────

def test_action_window_exact_boundary_counts():
    fired = datetime.date(2026, 8, 3)   # Monday
    window_end = ro._advance_trading_days(fired, 10)
    rec = _rec(rec_type="beta_trim", ticker="AAA", fired_date=fired.isoformat())
    out = ro.match_attribution(
        rec, [_trade(ticker="AAA", action="SELL", traded_at=window_end.isoformat())], 10,
    )
    assert out["acted"] is True


def test_action_window_one_day_later_does_not_count():
    fired = datetime.date(2026, 8, 3)
    window_end = ro._advance_trading_days(fired, 10)
    one_day_later = ro._advance_trading_days(window_end, 1)
    rec = _rec(rec_type="beta_trim", ticker="AAA", fired_date=fired.isoformat())
    out = ro.match_attribution(
        rec, [_trade(ticker="AAA", action="SELL", traded_at=one_day_later.isoformat())], 10,
    )
    assert out["acted"] is False


def test_trade_before_fired_date_does_not_count():
    fired = datetime.date(2026, 8, 3)
    before = datetime.date(2026, 8, 1)
    rec = _rec(rec_type="beta_trim", ticker="AAA", fired_date=fired.isoformat())
    out = ro.match_attribution(
        rec, [_trade(ticker="AAA", action="SELL", traded_at=before.isoformat())], 10,
    )
    assert out["acted"] is False


# ── trigger_type non-collision / confirming boost ────────────────────────────

def test_rebalance_trigger_type_never_reads_as_reb_trim_confirmation():
    """A REBALANCE-tagged SELL (the doubly-polluted legacy value, used by the
    SPLIT synthetic-row writer AND the plain user-selectable journal option)
    must never be treated as a confirming REBAL_TRIM signal — but the
    ticker/direction/window match alone must still credit `acted`."""
    rec = _rec(rec_type="rebal_trim", ticker="AAA", fired_date="2026-08-01",
                metric_name="single_name_pct")
    out = ro.match_attribution(
        rec, [_trade(ticker="AAA", action="SELL", traded_at="2026-08-03", trigger_type="REBALANCE")], 10,
    )
    assert out["acted"] is True
    assert out["attribution_confirmed"] is False


def test_absent_trigger_type_still_allows_attribution():
    rec = _rec(rec_type="rebal_trim", ticker="AAA", fired_date="2026-08-01")
    out = ro.match_attribution(
        rec, [_trade(ticker="AAA", action="SELL", traded_at="2026-08-03")], 10,  # no trigger_type key
    )
    assert out["acted"] is True
    assert out["attribution_confirmed"] is False


def test_matching_trigger_type_confirms():
    rec = _rec(rec_type="rebal_trim", ticker="AAA", fired_date="2026-08-01")
    out = ro.match_attribution(
        rec, [_trade(ticker="AAA", action="SELL", traded_at="2026-08-03", trigger_type="REBAL_TRIM")], 10,
    )
    assert out["acted"] is True
    assert out["attribution_confirmed"] is True


# ── Maturity boundary ────────────────────────────────────────────────────────

def test_maturity_boundary_matures_at_exactly_horizon():
    fired = datetime.date(2026, 8, 1)
    target = ro._advance_trading_days(fired, 30)
    rows = [_rec(fired_date=fired.isoformat())]
    out = ro.enrich_and_grade(
        rows, today=target, trades=[], horizon_trading_days=30, action_window_trading_days=10,
    )
    assert out[0]["status"] == ro.STATUS_MATURED


def test_maturity_boundary_not_matured_one_day_before():
    fired = datetime.date(2026, 8, 1)
    target = ro._advance_trading_days(fired, 30)
    one_day_before = target - datetime.timedelta(days=1)
    rows = [_rec(fired_date=fired.isoformat())]
    out = ro.enrich_and_grade(
        rows, today=one_day_before, trades=[], horizon_trading_days=30, action_window_trading_days=10,
    )
    assert out[0]["status"] == ro.STATUS_NOT_MATURED


def test_unparseable_fired_date_is_not_matured_never_evaluable():
    rows = [_rec(fired_date="not-a-date")]
    out = ro.enrich_and_grade(
        rows, today=TODAY, trades=[], horizon_trading_days=30, action_window_trading_days=10,
    )
    assert out[0]["status"] == ro.STATUS_NOT_MATURED
    assert out[0]["outcome"] is None


# ── Collapse ─────────────────────────────────────────────────────────────────

def test_collapse_n_consecutive_refires_to_one_row_earliest_anchored():
    rows = [
        _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-01"),
        _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-02"),
        _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-03"),
        _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-04"),
    ]
    out = ro.collapse_by_rec_ticker(rows)
    assert len(out) == 1
    assert out[0]["fired_date"] == "2026-08-01"


def test_collapse_keys_on_rec_type_and_ticker_pair():
    rows = [
        _rec(rec_type="beta_trim", ticker="AAA", fired_date="2026-08-01"),
        _rec(rec_type="rebal_trim", ticker="AAA", fired_date="2026-08-02"),
    ]
    out = ro.collapse_by_rec_ticker(rows)
    assert len(out) == 2
    keys = {(r["rec_type"], r["ticker"]) for r in out}
    assert keys == {("beta_trim", "AAA"), ("rebal_trim", "AAA")}


def test_collapse_drops_rows_missing_rec_type_or_ticker():
    rows = [{"ticker": "AAA", "fired_date": "2026-08-01"}, {"rec_type": "beta_trim", "fired_date": "2026-08-01"}]
    assert ro.collapse_by_rec_ticker(rows) == []


# ── Cross-system overlap ─────────────────────────────────────────────────────

def test_sell_matching_beta_trim_and_active_exit_credits_both():
    fired = datetime.date(2026, 8, 1)
    rows = [_rec(rec_type="beta_trim", ticker="AAA", fired_date=fired.isoformat())]
    trades = [_trade(ticker="AAA", action="SELL", traded_at="2026-08-03")]
    out = ro.enrich_and_grade(
        rows, today=TODAY, trades=trades, horizon_trading_days=30,
        action_window_trading_days=10, protective_call_tickers={"AAA"},
    )
    assert out[0]["acted"] is True
    assert out[0]["overlap_with_exit_advisor"] is True


def test_no_overlap_when_ticker_not_in_protective_call_set():
    fired = datetime.date(2026, 8, 1)
    rows = [_rec(rec_type="beta_trim", ticker="AAA", fired_date=fired.isoformat())]
    trades = [_trade(ticker="AAA", action="SELL", traded_at="2026-08-03")]
    out = ro.enrich_and_grade(
        rows, today=TODAY, trades=trades, horizon_trading_days=30,
        action_window_trading_days=10, protective_call_tickers={"ZZZ"},
    )
    assert out[0]["acted"] is True
    assert out[0]["overlap_with_exit_advisor"] is False


def test_overlap_never_set_when_not_acted():
    fired = datetime.date(2026, 8, 1)
    rows = [_rec(rec_type="beta_trim", ticker="AAA", fired_date=fired.isoformat())]
    out = ro.enrich_and_grade(
        rows, today=TODAY, trades=[], horizon_trading_days=30,
        action_window_trading_days=10, protective_call_tickers={"AAA"},
    )
    assert out[0]["acted"] is False
    assert out[0]["overlap_with_exit_advisor"] is False


# ── Band boundaries ──────────────────────────────────────────────────────────

def _matured_rows(n, n_tickers, rec_type="beta_trim", acted=True):
    """n matured rows, spread across n_tickers distinct tickers."""
    out = []
    for i in range(n):
        ticker = f"T{i % n_tickers}"
        out.append({
            "rec_type": rec_type, "ticker": ticker, "status": ro.STATUS_MATURED,
            "acted": acted,
        })
    return out


def test_band_seven_calls_is_building():
    rows = _matured_rows(7, 5)
    g = ro.grade_by_rec_type(rows, rec_type="beta_trim", arm="acted",
                              min_calls=8, firm_calls=15, min_tickers=5)
    assert g["band"] == "building"


def test_band_eight_calls_but_only_four_tickers_is_building():
    rows = _matured_rows(8, 4)
    g = ro.grade_by_rec_type(rows, rec_type="beta_trim", arm="acted",
                              min_calls=8, firm_calls=15, min_tickers=5)
    assert g["band"] == "building"


def test_band_eight_calls_five_tickers_is_early():
    rows = _matured_rows(8, 5)
    g = ro.grade_by_rec_type(rows, rec_type="beta_trim", arm="acted",
                              min_calls=8, firm_calls=15, min_tickers=5)
    assert g["band"] == "early"


def test_band_fifteen_calls_five_tickers_is_firm():
    rows = _matured_rows(15, 5)
    g = ro.grade_by_rec_type(rows, rec_type="beta_trim", arm="acted",
                              min_calls=8, firm_calls=15, min_tickers=5)
    assert g["band"] == "firm"


def test_band_scopes_to_rec_type_and_arm_only():
    rows = (
        _matured_rows(15, 5, rec_type="beta_trim", acted=True)
        + _matured_rows(15, 5, rec_type="rebal_trim", acted=True)
        + _matured_rows(15, 5, rec_type="beta_trim", acted=False)
    )
    g = ro.grade_by_rec_type(rows, rec_type="beta_trim", arm="acted",
                              min_calls=8, firm_calls=15, min_tickers=5)
    assert g["n_calls"] == 15


# ── NULL-preserving outcome computation ──────────────────────────────────────

def test_beta_trim_outcome_realized_none_when_snapshot_missing():
    fired = datetime.date(2026, 8, 1)
    rows = [_rec(rec_type="beta_trim", ticker="AAA", fired_date=fired.isoformat())]
    out = ro.enrich_and_grade(
        rows, today=TODAY, trades=[], horizon_trading_days=30,
        action_window_trading_days=10, risk_snapshot_by_date={},
    )
    assert out[0]["outcome"]["realized_portfolio_beta"] is None


def test_beta_trim_outcome_realized_from_snapshot():
    fired = datetime.date(2026, 8, 1)
    target = ro._advance_trading_days(fired, 30)
    rows = [_rec(rec_type="beta_trim", ticker="AAA", fired_date=fired.isoformat(),
                  metric_predicted_after=1.3)]
    snap = {target.isoformat(): {"portfolio_beta": 1.28}}
    out = ro.enrich_and_grade(
        rows, today=TODAY, trades=[], horizon_trading_days=30,
        action_window_trading_days=10, risk_snapshot_by_date=snap,
    )
    assert out[0]["outcome"]["predicted"] == pytest.approx(1.3)
    assert out[0]["outcome"]["realized_portfolio_beta"] == pytest.approx(1.28)


def test_diversify_add_outcome_leg_a_none_without_price_at_rec():
    fired = datetime.date(2026, 8, 1)
    rows = [_rec(rec_type="diversify_add", ticker="UNH", fired_date=fired.isoformat(),
                  price_at_rec=None, candidates=["UNH"])]
    out = ro.enrich_and_grade(
        rows, today=TODAY, trades=[], horizon_trading_days=30, action_window_trading_days=10,
    )
    assert out[0]["outcome"]["leg_a_candidate_alpha_pct"] is None


# ── schedule_disclosure ───────────────────────────────────────────────────────

def test_schedule_disclosure_none_when_no_fired_dates():
    out = ro.schedule_disclosure(None, TODAY)
    assert out["sanity_check_date"] is None
    assert out["retirement_date"] is None
    assert out["sanity_check_due"] is False
    assert out["retirement_due"] is False


def test_schedule_disclosure_dates_and_due_flags():
    min_fired = datetime.date(2026, 1, 1)
    out = ro.schedule_disclosure(min_fired, datetime.date(2026, 6, 1))
    assert out["sanity_check_date"] == datetime.date(2026, 4, 1)
    assert out["retirement_date"] == datetime.date(2027, 1, 1)
    assert out["sanity_check_due"] is True
    assert out["retirement_due"] is False


def test_earliest_fired_date_ignores_unparseable():
    rows = [_rec(fired_date="2026-08-05"), _rec(fired_date="bogus"), _rec(fired_date="2026-08-01")]
    assert ro.earliest_fired_date(rows) == datetime.date(2026, 8, 1)


def test_earliest_fired_date_empty_input_is_none():
    assert ro.earliest_fired_date([]) is None
