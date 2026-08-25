"""
Tests for stock_analyzer/self_track_record.py — "is MY instinct good?" vs
the Engine Track Record's "is the ENGINE good?" A BUY trade classifies into
app_aligned / self_in_scope / self_out_of_scope / coverage_limited purely
from ticker scope + rec-match timing; `trigger_type` must have zero
influence. Pure logic, no I/O.

Also covers cron_runner._build_new_pick_rows — the pure row-shaping helper
that lets the headless scan log today's new_pick recommendations even when
no interactive session runs that day (closing the coverage gap
SELF_TRACK_RELIABLE_LOG_START exists to mark).
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import self_track_record as stv
from stock_analyzer.constants import (
    SELF_TRACK_MATCH_LOOKBACK_DAYS,
    SELF_TRACK_RELIABLE_LOG_START,
    SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    SELF_TRACK_SELL_RELIABLE_LOG_START,
    BEHAVIORAL_MIN_SAMPLE_N,
    REC_SCORE_MIN_DAYS,
)


# ─── builders ───────────────────────────────────────────────────────────────

def _trade_row(ticker="AAA", traded_at="2026-08-10", action="BUY", shares=10.0,
                price=100.0, id_=1, user_thesis="", trigger_type="MANUAL"):
    return {
        "id": id_, "ticker": ticker, "traded_at": traded_at, "action": action,
        "shares": shares, "price": price, "user_thesis": user_thesis,
        "trigger_type": trigger_type,
    }


def _trades_df(rows):
    return pd.DataFrame(rows)


def _rec_row(ticker="AAA", rec_date="2026-08-10", rec_type="new_pick"):
    return {"ticker": ticker, "rec_date": rec_date, "rec_type": rec_type}


def _recs_df(rows):
    return pd.DataFrame(rows)


def _flat_spy(start: date, end: date, price: float = 400.0) -> dict:
    """A constant-price SPY series so alpha_pct == outcome_pct exactly —
    isolates the bucket-averaging logic from the SPY-benchmarking math
    (already covered by test_recommendations_history.py)."""
    out = {}
    d = start
    while d <= end:
        out[d] = price
        d += timedelta(days=1)
    return out


# ─── classify_buys — SELF_TRACK_RELIABLE_LOG_START boundary ────────────────

def test_reliable_log_start_boundary_on_date_is_self_in_scope():
    trades = _trades_df([
        _trade_row(ticker="AAA", traded_at=SELF_TRACK_RELIABLE_LOG_START.isoformat(), id_=1),
    ])
    result = stv.classify_buys(
        trades, _recs_df([]), {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert result[0]["bucket"] == "self_in_scope"


def test_reliable_log_start_boundary_day_before_is_coverage_limited():
    day_before = SELF_TRACK_RELIABLE_LOG_START - timedelta(days=1)
    trades = _trades_df([
        _trade_row(ticker="AAA", traded_at=day_before.isoformat(), id_=1),
    ])
    result = stv.classify_buys(
        trades, _recs_df([]), {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert result[0]["bucket"] == "coverage_limited"


# ─── classify_buys — SELF_TRACK_MATCH_LOOKBACK_DAYS edge ───────────────────

def test_rec_exactly_lookback_days_before_matches():
    trade_date = date(2026, 8, 20)
    rec_date = trade_date - timedelta(days=SELF_TRACK_MATCH_LOOKBACK_DAYS)
    trades = _trades_df([_trade_row(ticker="AAA", traded_at=trade_date.isoformat())])
    recs = _recs_df([_rec_row(ticker="AAA", rec_date=rec_date.isoformat())])
    result = stv.classify_buys(
        trades, recs, {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert result[0]["bucket"] == "app_aligned"


def test_rec_one_day_beyond_lookback_does_not_match():
    trade_date = date(2026, 8, 20)
    rec_date = trade_date - timedelta(days=SELF_TRACK_MATCH_LOOKBACK_DAYS + 1)
    trades = _trades_df([_trade_row(ticker="AAA", traded_at=trade_date.isoformat())])
    recs = _recs_df([_rec_row(ticker="AAA", rec_date=rec_date.isoformat())])
    result = stv.classify_buys(
        trades, recs, {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert result[0]["bucket"] != "app_aligned"


def test_rec_dated_after_trade_never_matches():
    trade_date = date(2026, 8, 20)
    rec_date = trade_date + timedelta(days=1)
    trades = _trades_df([_trade_row(ticker="AAA", traded_at=trade_date.isoformat())])
    recs = _recs_df([_rec_row(ticker="AAA", rec_date=rec_date.isoformat())])
    result = stv.classify_buys(
        trades, recs, {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert result[0]["bucket"] != "app_aligned"


# ─── classify_buys — offline-sentinel safety ───────────────────────────────

def test_recs_none_returns_none():
    trades = _trades_df([_trade_row()])
    result = stv.classify_buys(
        trades, None, {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert result is None


def test_recs_empty_df_not_none_classifies_normally():
    trades = _trades_df([_trade_row(ticker="AAA", traded_at="2026-08-10")])
    result = stv.classify_buys(
        trades, _recs_df([]), {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert result is not None
    assert len(result) == 1
    assert result[0]["bucket"] == "self_in_scope"


# ─── classify_buys — out-of-scope purity ───────────────────────────────────

def test_out_of_scope_ticker_always_self_out_of_scope_regardless_of_date():
    old_date = SELF_TRACK_RELIABLE_LOG_START - timedelta(days=365)
    new_date = SELF_TRACK_RELIABLE_LOG_START + timedelta(days=10)
    trades = _trades_df([
        _trade_row(ticker="ZZZ", traded_at=old_date.isoformat(), id_=1),
        _trade_row(ticker="ZZZ", traded_at=new_date.isoformat(), id_=2),
    ])
    result = stv.classify_buys(
        trades, _recs_df([]), set(), set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert {r["bucket"] for r in result} == {"self_out_of_scope"}


# ─── classify_buys — trigger_type has zero effect ──────────────────────────

def test_trigger_type_does_not_affect_bucket():
    trades = _trades_df([
        _trade_row(ticker="AAA", traded_at="2026-08-10", trigger_type="MANUAL", id_=1),
        _trade_row(ticker="AAA", traded_at="2026-08-10", trigger_type="RECOMMENDATION", id_=2),
    ])
    result = stv.classify_buys(
        trades, _recs_df([]), {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    by_id = {r["id"]: r["bucket"] for r in result}
    assert by_id[1] == by_id[2]


# ─── classify_buys — per-trade granularity (never collapsed by ticker) ─────

def test_same_ticker_two_dates_classified_independently():
    trades = _trades_df([
        _trade_row(ticker="AAA", traded_at="2026-08-20", id_=1),   # matches the rec below
        _trade_row(ticker="AAA", traded_at="2026-08-05", id_=2),   # far from any rec
    ])
    recs = _recs_df([_rec_row(ticker="AAA", rec_date="2026-08-18", rec_type="new_pick")])
    result = stv.classify_buys(
        trades, recs, {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert len(result) == 2
    by_id = {r["id"]: r for r in result}
    assert by_id[1]["bucket"] == "app_aligned"
    assert by_id[2]["bucket"] != "app_aligned"


def test_only_buy_action_rows_are_classified():
    trades = _trades_df([
        _trade_row(ticker="AAA", traded_at="2026-08-10", action="BUY", id_=1),
        _trade_row(ticker="AAA", traded_at="2026-08-11", action="SELL", id_=2),
    ])
    result = stv.classify_buys(
        trades, _recs_df([]), {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert len(result) == 1
    assert result[0]["id"] == 1


def test_user_thesis_passed_through_as_is():
    trades = _trades_df([
        _trade_row(ticker="AAA", traded_at="2026-08-10", id_=1, user_thesis="my real thesis"),
    ])
    result = stv.classify_buys(
        trades, _recs_df([]), {"AAA"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert result[0]["user_thesis"] == "my real thesis"


# ─── self_vs_engine_summary ─────────────────────────────────────────────────

def test_summary_available_false_when_classified_none():
    summary = stv.self_vs_engine_summary(None, {}, {}, date(2026, 9, 1), BEHAVIORAL_MIN_SAMPLE_N)
    assert summary == {"available": False}


def test_summary_buckets_counts_and_gated_averages():
    today = date(2026, 9, 1)
    universe = {"AAA", "SSS"}
    rows, recs_rows = [], []

    # 8 app_aligned BUYs (AAA, matched rec same day) — 0% outcome (flat price)
    for i in range(8):
        d = today - timedelta(days=10 + i)
        rows.append(_trade_row(ticker="AAA", traded_at=d.isoformat(), id_=100 + i, price=100.0))
        recs_rows.append(_rec_row(ticker="AAA", rec_date=d.isoformat(), rec_type="new_pick"))

    # 8 self_in_scope BUYs (SSS, no rec, in scope, on/after reliable start) — +10% outcome
    for i in range(8):
        d = today - timedelta(days=10 + i)
        rows.append(_trade_row(ticker="SSS", traded_at=d.isoformat(), id_=200 + i, price=100.0))

    # 1 coverage_limited BUY (AAA, in scope, no matching rec, dated before reliable start)
    old_d = SELF_TRACK_RELIABLE_LOG_START - timedelta(days=5)
    rows.append(_trade_row(ticker="AAA", traded_at=old_d.isoformat(), id_=300, price=100.0))

    trades = _trades_df(rows)
    recs = _recs_df(recs_rows)
    classified = stv.classify_buys(
        trades, recs, universe, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    assert len(classified) == 17

    current_prices = {"AAA": 100.0, "SSS": 110.0}
    spy = _flat_spy(today - timedelta(days=40), today)

    summary = stv.self_vs_engine_summary(classified, current_prices, spy, today, BEHAVIORAL_MIN_SAMPLE_N)

    assert summary["available"] is True
    assert summary["n_app_aligned"] == 8
    assert summary["n_self_in_scope"] == 8
    assert summary["n_self_out_of_scope"] == 0
    assert summary["n_self_graded"] == 8
    assert summary["n_coverage_limited"] == 1

    # coverage_limited must never leak into either graded average's count
    assert summary["app_aligned"]["n"] + summary["self_graded"]["n"] == 16

    assert summary["app_aligned"]["sufficient"] is True
    assert summary["self_graded"]["sufficient"] is True
    assert summary["app_aligned"]["avg_alpha_pct"] == pytest.approx(0.0, abs=0.01)
    assert summary["self_graded"]["avg_alpha_pct"] == pytest.approx(10.0, abs=0.5)

    # All 8 self rows here are in-scope, matured and priced.
    assert summary["n_self_in_scope_graded"] == 8
    assert summary["n_self_out_of_scope_graded"] == 0


def test_summary_graded_split_sums_to_the_graded_n():
    """The split must describe exactly the rows behind self_graded's average.

    self_graded averages ACROSS self_in_scope + self_out_of_scope, and the two
    answer different questions — in-scope means the app had a view and stayed
    silent (a head-to-head), out-of-scope means it never looked. The panel
    captions the average with this split, so it has to reconcile with it.
    """
    today = date(2026, 9, 1)
    universe = {"SSS"}                      # ZZZ deliberately out of scope
    rows = []
    for i in range(5):
        d = today - timedelta(days=10 + i)
        rows.append(_trade_row(ticker="SSS", traded_at=d.isoformat(), id_=200 + i, price=100.0))
    for i in range(3):
        d = today - timedelta(days=10 + i)
        rows.append(_trade_row(ticker="ZZZ", traded_at=d.isoformat(), id_=400 + i, price=100.0))

    classified = stv.classify_buys(
        _trades_df(rows), _recs_df([]), universe, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    summary = stv.self_vs_engine_summary(
        classified, {"SSS": 110.0, "ZZZ": 110.0},
        _flat_spy(today - timedelta(days=40), today), today, BEHAVIORAL_MIN_SAMPLE_N,
    )

    assert summary["n_self_in_scope_graded"] == 5
    assert summary["n_self_out_of_scope_graded"] == 3
    assert (
        summary["n_self_in_scope_graded"] + summary["n_self_out_of_scope_graded"]
        == summary["self_graded"]["n"]
    )


def test_summary_graded_split_excludes_immature_rows_unlike_the_raw_counts():
    """THE reason the _graded variants exist rather than reusing the raw counts.

    `n_self_in_scope` counts every classified trade including immature and
    unpriced ones. Captioning the average with that number would describe a
    LARGER population than the average actually ran on — the same mistake F-246
    was built to fix, one panel over.
    """
    today = date(2026, 9, 1)
    rows = []
    for i in range(3):                      # matured: graded
        d = today - timedelta(days=10 + i)
        rows.append(_trade_row(ticker="SSS", traded_at=d.isoformat(), id_=200 + i, price=100.0))
    # Bought yesterday — inside REC_SCORE_MIN_DAYS, so classified but NOT graded.
    rows.append(_trade_row(
        ticker="SSS", traded_at=(today - timedelta(days=1)).isoformat(), id_=299, price=100.0,
    ))

    classified = stv.classify_buys(
        _trades_df(rows), _recs_df([]), {"SSS"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    summary = stv.self_vs_engine_summary(
        classified, {"SSS": 110.0},
        _flat_spy(today - timedelta(days=40), today), today, BEHAVIORAL_MIN_SAMPLE_N,
    )

    assert summary["n_self_in_scope"] == 4              # raw: includes the immature buy
    assert summary["n_self_in_scope_graded"] == 3       # graded: excludes it
    assert summary["self_graded"]["n"] == 3


def test_summary_all_out_of_scope_reports_zero_head_to_head():
    """Drives the amber "none of these is a head-to-head" branch.

    With nothing in-scope the side-by-side compares the user's own sourcing
    against the engine's picks — not their judgment against its silence — so the
    count that reveals it must be reachable and exactly zero.
    """
    today = date(2026, 9, 1)
    rows = [
        _trade_row(ticker="ZZZ", traded_at=(today - timedelta(days=10 + i)).isoformat(),
                   id_=400 + i, price=100.0)
        for i in range(4)
    ]
    classified = stv.classify_buys(
        _trades_df(rows), _recs_df([]), {"SSS"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    summary = stv.self_vs_engine_summary(
        classified, {"ZZZ": 110.0},
        _flat_spy(today - timedelta(days=40), today), today, BEHAVIORAL_MIN_SAMPLE_N,
    )

    assert summary["self_graded"]["n"] == 4
    assert summary["n_self_in_scope_graded"] == 0
    assert summary["n_self_out_of_scope_graded"] == 4


def test_summary_gates_each_average_independently():
    today = date(2026, 9, 1)
    universe = {"AAA", "SSS"}
    rows, recs_rows = [], []

    # Only 3 app_aligned BUYs — BELOW min_sample_n
    for i in range(3):
        d = today - timedelta(days=10 + i)
        rows.append(_trade_row(ticker="AAA", traded_at=d.isoformat(), id_=100 + i, price=100.0))
        recs_rows.append(_rec_row(ticker="AAA", rec_date=d.isoformat(), rec_type="new_pick"))

    # 8 self_in_scope BUYs — AT min_sample_n
    for i in range(8):
        d = today - timedelta(days=10 + i)
        rows.append(_trade_row(ticker="SSS", traded_at=d.isoformat(), id_=200 + i, price=100.0))

    trades = _trades_df(rows)
    recs = _recs_df(recs_rows)
    classified = stv.classify_buys(
        trades, recs, universe, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    current_prices = {"AAA": 100.0, "SSS": 105.0}
    spy = _flat_spy(today - timedelta(days=40), today)

    summary = stv.self_vs_engine_summary(classified, current_prices, spy, today, BEHAVIORAL_MIN_SAMPLE_N)

    assert summary["app_aligned"]["n"] == 3
    assert summary["app_aligned"]["sufficient"] is False
    assert summary["self_graded"]["n"] == 8
    assert summary["self_graded"]["sufficient"] is True


def test_summary_excludes_maturing_rows_from_average():
    """A BUY younger than REC_SCORE_MIN_DAYS must not inflate/deflate either
    average — same maturity contract compute_outcomes already enforces for
    recommendations_history."""
    today = date(2026, 9, 1)
    fresh_date = today - timedelta(days=max(REC_SCORE_MIN_DAYS - 1, 0))
    trades = _trades_df([
        _trade_row(ticker="SSS", traded_at=fresh_date.isoformat(), id_=1, price=100.0),
    ])
    classified = stv.classify_buys(
        trades, _recs_df([]), {"SSS"}, set(),
        SELF_TRACK_RELIABLE_LOG_START, SELF_TRACK_MATCH_LOOKBACK_DAYS,
    )
    summary = stv.self_vs_engine_summary(
        classified, {"SSS": 150.0}, _flat_spy(today - timedelta(days=10), today), today,
        min_sample_n=1,
    )
    assert summary["self_graded"]["n"] == 0
    assert summary["self_graded"]["avg_alpha_pct"] is None


# ─── cron_runner._build_new_pick_rows — row-shaping idempotency ────────────

def test_build_new_pick_rows_skips_picks_with_no_ticker():
    from cron_runner import _build_new_pick_rows

    picks = [
        {"ticker": "AAA", "price": 100.0, "composite_score": 70, "score": 60,
         "sector": "Tech", "conviction": "high",
         "xref": {"verdict": "confirmed"}, "thesis": "thesis text"},
        {"ticker": "", "price": 50.0},
    ]
    rows = _build_new_pick_rows(picks, date(2026, 8, 10))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAA"
    assert rows[0]["rec_type"] == "new_pick"
    assert rows[0]["rec_date"] == date(2026, 8, 10)
    assert rows[0]["verdict"] == "confirmed"


def test_build_new_pick_rows_deterministic_across_calls():
    """Calling the row-builder twice for the same picks/date produces
    identical rows — the idempotency guarantee against double-counting is
    db.save_recommendations' own upsert (on_conflict=ticker,rec_date,rec_type,
    ignore_duplicates=True), which this determinism is a precondition for."""
    from cron_runner import _build_new_pick_rows

    picks = [{"ticker": "AAA", "price": 100.0, "composite_score": 70, "score": 60,
              "sector": "Tech", "conviction": "high", "xref": {}, "thesis": ""}]
    rec_date = date(2026, 8, 10)
    rows1 = _build_new_pick_rows(picks, rec_date)
    rows2 = _build_new_pick_rows(picks, rec_date)
    assert rows1 == rows2


# ═════════════════════════════════════════════════════════════════════════════
# SELL-side sibling — classify_sells / self_vs_engine_sell_summary /
# detect_missed_exits (F-233's SELL extension)
# ═════════════════════════════════════════════════════════════════════════════

def _sell_row(ticker="AAA", traded_at="2026-08-10", shares=10.0, price=100.0,
              id_=1, user_thesis=""):
    return _trade_row(ticker=ticker, traded_at=traded_at, action="SELL",
                       shares=shares, price=price, id_=id_, user_thesis=user_thesis)


def _signal_row(ticker="AAA", signal_date="2026-08-10", signal_type="EXIT"):
    return {"ticker": ticker, "signal_date": signal_date, "signal_type": signal_type}


def _signals_df(rows):
    return pd.DataFrame(rows)


# ─── Offline-sentinel guards ────────────────────────────────────────────────

def test_classify_sells_returns_none_when_exit_signals_none():
    trades = _trades_df([_sell_row()])
    result = stv.classify_sells(
        trades, None, SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result is None


def test_sell_summary_available_false_when_classified_none():
    summary = stv.self_vs_engine_sell_summary(
        None, {}, {}, date(2026, 9, 1), BEHAVIORAL_MIN_SAMPLE_N,
    )
    assert summary == {"available": False}


def test_detect_missed_exits_returns_none_when_exit_signals_none():
    trades = _trades_df([_sell_row()])
    result = stv.detect_missed_exits(
        None, trades, {"AAA"}, date(2026, 9, 1), SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result is None


def test_classify_sells_empty_signals_df_not_none_classifies_normally():
    trades = _trades_df([_sell_row(ticker="AAA", traded_at="2026-08-10")])
    result = stv.classify_sells(
        trades, _signals_df([]),
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result is not None
    assert len(result) == 1
    assert result[0]["bucket"] == "self_initiated"


# ─── Alpha sign invariant ───────────────────────────────────────────────────

def test_alpha_negative_when_stock_underperforms_after_sale():
    """A good exit: the stock lags SPY after the sale -> negative avg alpha."""
    today = date(2026, 9, 1)
    sell_date = today - timedelta(days=30)
    trades = _trades_df([
        _sell_row(ticker="AAA", traded_at=sell_date.isoformat(), price=100.0, id_=1),
    ])
    classified = stv.classify_sells(
        trades, _signals_df([]),
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    # Stock DROPPED after the sale (good exit) while SPY held flat.
    current_prices = {"AAA": 90.0}
    spy = _flat_spy(sell_date - timedelta(days=5), today, price=400.0)
    summary = stv.self_vs_engine_sell_summary(
        classified, current_prices, spy, today, min_sample_n=1,
    )
    assert summary["self_graded"]["avg_alpha_pct"] is not None
    assert summary["self_graded"]["avg_alpha_pct"] < 0


def test_alpha_positive_when_stock_outperforms_after_sale():
    """A bad (early) exit: the stock beats SPY after the sale -> positive avg alpha."""
    today = date(2026, 9, 1)
    sell_date = today - timedelta(days=30)
    trades = _trades_df([
        _sell_row(ticker="AAA", traded_at=sell_date.isoformat(), price=100.0, id_=1),
    ])
    classified = stv.classify_sells(
        trades, _signals_df([]),
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    # Stock RALLIED after the sale (sold too early) while SPY held flat.
    current_prices = {"AAA": 130.0}
    spy = _flat_spy(sell_date - timedelta(days=5), today, price=400.0)
    summary = stv.self_vs_engine_sell_summary(
        classified, current_prices, spy, today, min_sample_n=1,
    )
    assert summary["self_graded"]["avg_alpha_pct"] is not None
    assert summary["self_graded"]["avg_alpha_pct"] > 0


# ─── Never-zeroed invariant (regression guard against the acted-SELL mistake) ─

def test_classify_sells_never_frames_as_acted_trade():
    trades = _trades_df([
        _sell_row(ticker="AAA", traded_at="2026-08-10", id_=1),
        _sell_row(ticker="BBB", traded_at="2026-08-11", id_=2),
    ])
    result = stv.classify_sells(
        trades, _signals_df([]),
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    for r in result:
        assert r["acted_on"] is False
        assert r["acted_trade"] is None
        assert r["price_at_surface"] == r["price"]
        assert r["rec_date"] == r["sell_date"]


# ─── coverage_limited boundary ──────────────────────────────────────────────

def test_sell_before_reliable_start_no_match_is_coverage_limited():
    day_before = SELF_TRACK_SELL_RELIABLE_LOG_START - timedelta(days=1)
    trades = _trades_df([_sell_row(ticker="AAA", traded_at=day_before.isoformat())])
    result = stv.classify_sells(
        trades, _signals_df([]),
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result[0]["bucket"] == "coverage_limited"


def test_sell_on_reliable_start_no_match_is_self_initiated():
    trades = _trades_df([
        _sell_row(ticker="AAA", traded_at=SELF_TRACK_SELL_RELIABLE_LOG_START.isoformat()),
    ])
    result = stv.classify_sells(
        trades, _signals_df([]),
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result[0]["bucket"] == "self_initiated"


def test_sell_before_reliable_start_with_matching_signal_is_engine_aligned():
    """A matched signal overrides the coverage boundary — date-independent."""
    sell_date = SELF_TRACK_SELL_RELIABLE_LOG_START - timedelta(days=30)
    signal_date = sell_date - timedelta(days=1)
    trades = _trades_df([_sell_row(ticker="AAA", traded_at=sell_date.isoformat())])
    signals = _signals_df([_signal_row(ticker="AAA", signal_date=signal_date.isoformat(),
                                        signal_type="EXIT")])
    result = stv.classify_sells(
        trades, signals,
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result[0]["bucket"] == "engine_aligned"


def test_watch_signal_never_counts_as_a_match():
    sell_date = date(2026, 8, 20)
    trades = _trades_df([_sell_row(ticker="AAA", traded_at=sell_date.isoformat())])
    signals = _signals_df([_signal_row(ticker="AAA", signal_date=sell_date.isoformat(),
                                        signal_type="WATCH")])
    result = stv.classify_sells(
        trades, signals,
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result[0]["bucket"] != "engine_aligned"


# ─── Signal-window boundary ─────────────────────────────────────────────────

def test_signal_exactly_window_days_before_sell_matches():
    sell_date = date(2026, 8, 20)
    signal_date = sell_date - timedelta(days=SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS)
    trades = _trades_df([_sell_row(ticker="AAA", traded_at=sell_date.isoformat())])
    signals = _signals_df([_signal_row(ticker="AAA", signal_date=signal_date.isoformat(),
                                        signal_type="TRIM")])
    result = stv.classify_sells(
        trades, signals,
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result[0]["bucket"] == "engine_aligned"


def test_signal_one_day_beyond_window_does_not_match():
    sell_date = date(2026, 8, 20)
    signal_date = sell_date - timedelta(days=SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS + 1)
    trades = _trades_df([_sell_row(ticker="AAA", traded_at=sell_date.isoformat())])
    signals = _signals_df([_signal_row(ticker="AAA", signal_date=signal_date.isoformat(),
                                        signal_type="EXIT")])
    result = stv.classify_sells(
        trades, signals,
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result[0]["bucket"] != "engine_aligned"


# ─── detect_missed_exits — calm-boundary behaviour ──────────────────────────

def test_missed_exits_recent_signal_not_yet_flagged():
    today = date(2026, 9, 1)
    signal_date = today - timedelta(days=SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS - 1)
    signals = _signals_df([_signal_row(ticker="AAA", signal_date=signal_date.isoformat(),
                                        signal_type="EXIT")])
    result = stv.detect_missed_exits(
        signals, _trades_df([]), {"AAA"}, today, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result == []


def test_missed_exits_older_signal_no_sell_since_is_flagged():
    today = date(2026, 9, 1)
    signal_date = today - timedelta(days=SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS + 5)
    signals = _signals_df([_signal_row(ticker="AAA", signal_date=signal_date.isoformat(),
                                        signal_type="EXIT")])
    result = stv.detect_missed_exits(
        signals, _trades_df([]), {"AAA"}, today, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert len(result) == 1
    assert result[0]["ticker"] == "AAA"
    assert result[0]["signal_type"] == "EXIT"


def test_missed_exits_ticker_sold_after_signal_not_flagged():
    today = date(2026, 9, 1)
    signal_date = today - timedelta(days=SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS + 5)
    sell_date = signal_date + timedelta(days=1)
    signals = _signals_df([_signal_row(ticker="AAA", signal_date=signal_date.isoformat(),
                                        signal_type="EXIT")])
    trades = _trades_df([_sell_row(ticker="AAA", traded_at=sell_date.isoformat())])
    result = stv.detect_missed_exits(
        signals, trades, {"AAA"}, today, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    assert result == []


# ─── No-cross-contamination with BUY-side bucket names ─────────────────────

def test_classify_sells_never_emits_buy_side_bucket_names():
    today = date(2026, 9, 1)
    rows = [
        _sell_row(ticker="AAA", traded_at=(today - timedelta(days=1)).isoformat(), id_=1),
        _sell_row(ticker="BBB",
                   traded_at=(SELF_TRACK_SELL_RELIABLE_LOG_START - timedelta(days=1)).isoformat(),
                   id_=2),
    ]
    signals = _signals_df([_signal_row(ticker="AAA",
                                        signal_date=(today - timedelta(days=1)).isoformat(),
                                        signal_type="EXIT")])
    result = stv.classify_sells(
        _trades_df(rows), signals,
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    buckets = {r["bucket"] for r in result}
    assert buckets <= {"engine_aligned", "self_initiated", "coverage_limited"}
    assert not buckets & {"app_aligned", "self_out_of_scope", "self_in_scope"}
