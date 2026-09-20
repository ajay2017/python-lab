"""
Tests for scripts/exit_early_cost_analysis.py's pure helpers -- everything
that composes protective_track_record / self_track_record / trade_review
outputs into a per-ticker dollar figure. No DB/network dependency, matching
tests/test_investigator_eval.py's own synthetic-data convention for a
`scripts/` module; `scripts/exit_ladder_replay.py` (this script's sibling)
has no test file at all, but its own logic is a thin CLI wrapper around
`exit_advisor`/`portfolio` functions already covered elsewhere. This script
is different: it introduces several NEW pure composition helpers
(direction classification, FIFO share/price attribution, the four-bucket
dollar arithmetic) that exist nowhere else in the codebase, so unlike its
sibling they get real unit coverage here.
"""

from datetime import date

import pandas as pd
import pytest

import scripts.exit_early_cost_analysis as eeca

pytestmark = pytest.mark.fast


# ─── classify_direction ──────────────────────────────────────────────────────

def test_classify_direction_negative_is_ran_early():
    assert eeca.classify_direction(-5.2) == "ran_early"


def test_classify_direction_positive_is_validated():
    assert eeca.classify_direction(3.1) == "validated"


def test_classify_direction_zero_tie_is_validated():
    """Disclosed judgment call -- protective_track_record.py itself only
    defines strict positive/negative, not a tie rule."""
    assert eeca.classify_direction(0.0) == "validated"


# ─── trade_rows_for_ticker ───────────────────────────────────────────────────

def _trades_df(rows):
    return pd.DataFrame(rows)


def test_trade_rows_for_ticker_filters_and_uppercases():
    df = _trades_df([
        {"id": 1, "ticker": "mu", "action": "BUY", "shares": 10, "price": 50.0, "traded_at": "2026-01-01"},
        {"id": 2, "ticker": "AAPL", "action": "BUY", "shares": 5, "price": 150.0, "traded_at": "2026-01-02"},
        {"id": 3, "ticker": "MU", "action": "SELL", "shares": 4, "price": 55.0, "traded_at": "2026-01-10"},
        {"id": 4, "ticker": "MU", "action": "DIVIDEND", "shares": 0, "price": 0, "traded_at": "2026-01-11"},
    ])
    rows = eeca.trade_rows_for_ticker(df, "mu")
    assert [r["id"] for r in rows] == [1, 3]
    assert all(r["ticker"] == "MU" for r in rows)


def test_trade_rows_for_ticker_empty_when_none():
    assert eeca.trade_rows_for_ticker(None, "MU") == []


# ─── shares_open_asof ────────────────────────────────────────────────────────

def _rows(*specs):
    """specs: (id, action, shares, price, date_str)"""
    return [
        {"id": i, "ticker": "MU", "action": a, "shares": s, "price": p,
         "_trade_date": date.fromisoformat(d)}
        for (i, a, s, p, d) in specs
    ]


def test_shares_open_asof_full_history_no_sells():
    rows = _rows((1, "BUY", 10, 50.0, "2026-01-01"))
    assert eeca.shares_open_asof(rows, None) == 10


def test_shares_open_asof_after_partial_sell():
    rows = _rows(
        (1, "BUY", 10, 50.0, "2026-01-01"),
        (2, "SELL", 4, 55.0, "2026-01-10"),
    )
    assert eeca.shares_open_asof(rows, None) == 6


def test_shares_open_asof_truncates_to_date():
    """Shares open ON the signal date must not see a LATER sell."""
    rows = _rows(
        (1, "BUY", 10, 50.0, "2026-01-01"),
        (2, "SELL", 4, 55.0, "2026-01-10"),
    )
    assert eeca.shares_open_asof(rows, date(2026, 1, 5)) == 10
    assert eeca.shares_open_asof(rows, date(2026, 1, 10)) == 6


def test_shares_open_asof_empty_rows_is_zero():
    assert eeca.shares_open_asof([], None) == 0.0


# ─── aggregate_engine_aligned_sale ───────────────────────────────────────────

def test_aggregate_engine_aligned_sale_none_when_no_match():
    rows = _rows(
        (1, "BUY", 10, 50.0, "2026-01-01"),
        (2, "SELL", 10, 55.0, "2026-01-10"),
    )
    assert eeca.aggregate_engine_aligned_sale(rows, engine_aligned_sell_ids=set()) is None


def test_aggregate_engine_aligned_sale_single_full_sell():
    rows = _rows(
        (1, "BUY", 10, 50.0, "2026-01-01"),
        (2, "SELL", 10, 55.0, "2026-01-10"),
    )
    out = eeca.aggregate_engine_aligned_sale(rows, engine_aligned_sell_ids={2})
    assert out["shares"] == 10
    assert out["avg_price"] == 55.0
    assert out["earliest_sell_date"] == date(2026, 1, 10)


def test_aggregate_engine_aligned_sale_weights_partial_fills_across_two_sells():
    """One sell IS engine_aligned, a second later sell of the remainder is
    NOT -- only the engine_aligned portion should be counted, weighted."""
    rows = _rows(
        (1, "BUY", 10, 50.0, "2026-01-01"),
        (2, "SELL", 4, 60.0, "2026-01-10"),   # engine_aligned
        (3, "SELL", 6, 40.0, "2026-02-01"),   # NOT engine_aligned
    )
    out = eeca.aggregate_engine_aligned_sale(rows, engine_aligned_sell_ids={2})
    assert out["shares"] == 4
    assert out["avg_price"] == 60.0
    assert out["earliest_sell_date"] == date(2026, 1, 10)


def test_aggregate_engine_aligned_sale_weights_across_two_buy_lots():
    """A single SELL that spans two BUY lots is still ONE engine_aligned
    sell -- both matched portions must be aggregated together."""
    rows = _rows(
        (1, "BUY", 5, 50.0, "2026-01-01"),
        (2, "BUY", 5, 60.0, "2026-01-05"),
        (3, "SELL", 8, 70.0, "2026-01-10"),   # eats all of lot 1, 3 of lot 2
    )
    out = eeca.aggregate_engine_aligned_sale(rows, engine_aligned_sell_ids={3})
    assert out["shares"] == 8
    assert out["avg_price"] == pytest.approx(70.0)  # same sell price both portions


# ─── bucket_and_dollar_figure ────────────────────────────────────────────────

_SALE = {"shares": 10.0, "avg_price": 50.0, "earliest_sell_date": date(2026, 1, 10)}


def test_bucket_cut_short_positive_cost_when_price_recovered():
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="ran_early", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=65.0,
        sale=_SALE, shares_at_signal=10.0, shares_held_today=0.0,
    )
    assert r["bucket"] == "cut_short"
    assert r["dollar"] == pytest.approx((65.0 - 50.0) * 10.0)
    assert r["note"] is None


def test_bucket_cut_short_flags_leftover_unsold_shares():
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="ran_early", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=65.0,
        sale=_SALE, shares_at_signal=15.0, shares_held_today=5.0,
    )
    assert r["bucket"] == "cut_short"
    assert "5.0" in r["note"] or "5" in r["note"]


def test_bucket_self_corrected_still_held():
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="ran_early", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=65.0,
        sale=None, shares_at_signal=10.0, shares_held_today=10.0,
    )
    assert r["bucket"] == "self_corrected"
    assert r["dollar"] == pytest.approx((65.0 - 48.0) * 10.0)
    assert r["note"] is None


def test_bucket_self_corrected_since_exited_is_disclosed_not_hidden():
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="ran_early", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=65.0,
        sale=None, shares_at_signal=10.0, shares_held_today=0.0,
    )
    assert r["bucket"] == "self_corrected"
    assert r["dollar"] is not None
    assert "unrelated" in r["note"]


def test_bucket_avoided_loss_positive_benefit():
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="validated", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=20.0,
        sale=_SALE, shares_at_signal=10.0, shares_held_today=0.0,
    )
    assert r["bucket"] == "avoided_loss"
    assert r["dollar"] == pytest.approx((50.0 - 20.0) * 10.0)


def test_bucket_ignored_call_reports_drag_when_still_held():
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="validated", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=20.0,
        sale=None, shares_at_signal=10.0, shares_held_today=10.0,
    )
    assert r["bucket"] == "ignored_call"
    assert r["dollar"] == pytest.approx((20.0 - 48.0) * 10.0)
    assert r["dollar"] < 0


def test_bucket_ignored_call_no_longer_held_reports_no_figure():
    """Per spec: if no longer held (sold later on other terms), note it --
    don't guess why, don't compute a figure."""
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="validated", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=20.0,
        sale=None, shares_at_signal=10.0, shares_held_today=0.0,
    )
    assert r["bucket"] == "ignored_call"
    assert r["dollar"] is None
    assert r["note"] is not None


def test_bucket_missing_current_price_never_fabricates_a_figure():
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="validated", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=None,
        sale=_SALE, shares_at_signal=10.0, shares_held_today=0.0,
    )
    assert r["dollar"] is None
    assert "no current price" in r["note"]


def test_bucket_data_gap_zero_shares_at_signal_reports_no_figure():
    r = eeca.bucket_and_dollar_figure(
        ticker="MU", direction="ran_early", signal_date=date(2026, 1, 5),
        price_at_signal=48.0, current_price=65.0,
        sale=None, shares_at_signal=0.0, shares_held_today=0.0,
    )
    assert r["bucket"] == "self_corrected"
    assert r["dollar"] is None
    assert "data gap" in r["note"]
