"""
Tests for stock_analyzer/trade_review.py — the Trade Journal behavioural
retrospective: FIFO share-matching between BUY/SELL rows, per-trade
outcome pricing (realized for SELLs, mark-to-market for open/partial BUYs,
vs-SPY benchmarking for closed BUYs), bucket rollups (app_followed /
deviated / discretionary / panic_window / overall), and the 7 independent
Lens-3 rule-based diagnostics that drive the user-facing "course-correction"
recommendations plus the orchestrating build_insights()/build_trade_review().

Previously zero test coverage despite this being the substrate for a
user-facing behavioural-coaching surface: a silent bug in the FIFO matcher
or the diagnostic severity ladders doesn't just mis-render a number, it
tells the user the wrong lesson about their own trading behaviour. Pure
logic, no I/O, no Streamlit.
"""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from stock_analyzer import trade_review as tr
from stock_analyzer.constants import SECTOR_ELEVATED, SINGLE_NAME_CEILING


# ─── builders ───────────────────────────────────────────────────────────────

def _raw(id_=1, ticker="AAA", action="BUY", shares=10.0, price=50.0,
         trade_date=date(2026, 1, 1)):
    """Minimal trade dict shape consumed by _pair_sells_to_buys."""
    return {"id": id_, "ticker": ticker, "action": action, "shares": shares,
            "price": price, "_trade_date": trade_date}


def _buy_row(id_=1, ticker="AAA", price=50.0, shares=10.0, trade_date=date(2026, 1, 1)):
    return {"id": id_, "ticker": ticker, "action": "BUY", "price": price,
            "shares": shares, "_trade_date": trade_date}


def _sell_row(id_=1, ticker="AAA", price=60.0, shares=10.0, trade_date=date(2026, 1, 10),
              realized_pnl=100.0, cost_basis=500.0):
    return {"id": id_, "ticker": ticker, "action": "SELL", "price": price,
            "shares": shares, "_trade_date": trade_date,
            "realized_pnl": realized_pnl, "cost_basis": cost_basis}


def _match_info(matched=None, shares_remaining=0.0):
    return {"matched": matched or [], "shares_remaining": shares_remaining}


def _sell_match(sell_id, sell_price, sell_date, shares):
    return {"sell_id": sell_id, "sell_price": sell_price, "sell_date": sell_date,
            "shares": shares}


def _spy_df(pairs):
    dates, closes = zip(*pairs)
    return pd.DataFrame({"Close": list(closes)}, index=pd.to_datetime(list(dates)))


def _t(id_=1, ticker="AAA", action="BUY", shares=10.0, price=50.0,
       trade_date=date(2026, 1, 1), is_win=None, outcome_pnl=0.0, outcome_pct=0.0,
       hold_days=None, outcome_status="open", vs_spy_pct=None, signal_seen="",
       trigger_type="", lesson="", category="discretionary", panic_window=False,
       sell_dedup=False, exit_price=None, exit_date=None, realized_pnl=0.0,
       shares_sold=0.0, shares_remaining=0.0):
    """General-purpose "trade_with_outcome" row — the shape every bucket/
    diagnostic function downstream of _per_trade_outcome() consumes."""
    return {
        "id": id_, "ticker": ticker, "action": action, "shares": shares, "price": price,
        "_trade_date": trade_date, "is_win": is_win, "outcome_pnl": outcome_pnl,
        "outcome_pct": outcome_pct, "hold_days": hold_days, "outcome_status": outcome_status,
        "vs_spy_pct": vs_spy_pct, "signal_seen": signal_seen, "trigger_type": trigger_type,
        "lesson": lesson, "category": category, "panic_window": panic_window,
        "_sell_dedup": sell_dedup, "exit_price": exit_price, "exit_date": exit_date,
        "realized_pnl": realized_pnl, "shares_sold": shares_sold,
        "shares_remaining": shares_remaining,
    }


def _journal_row(id_=1, ticker="AAA", traded_at="2026-01-05", action="BUY", shares=10.0,
                  price=50.0, cost_basis=500.0, realized_pnl=0.0, trigger_type="MANUAL",
                  signal_seen="", followed_signal=None, deviation_reason="", lesson="",
                  notes=""):
    return {
        "id": id_, "ticker": ticker, "traded_at": traded_at, "action": action,
        "shares": shares, "price": price, "cost_basis": cost_basis,
        "realized_pnl": realized_pnl, "trigger_type": trigger_type,
        "signal_seen": signal_seen, "followed_signal": followed_signal,
        "deviation_reason": deviation_reason, "lesson": lesson, "notes": notes,
    }


def _journal_df(rows):
    return pd.DataFrame(rows)


def _metrics(af=None, dv=None, pw=None, ov=None):
    return {"app_followed": af or {}, "deviated": dv or {}, "panic_window": pw or {},
            "overall": ov or {}}


def _trigger_group(trigger, n, n_wins, start_id, pnl_per_win=10.0, pnl_per_loss=-10.0):
    trades = []
    for i in range(n_wins):
        trades.append(_t(id_=start_id + i, trigger_type=trigger, is_win=True,
                          outcome_pnl=pnl_per_win))
    for i in range(n - n_wins):
        trades.append(_t(id_=start_id + n_wins + i, trigger_type=trigger, is_win=False,
                          outcome_pnl=pnl_per_loss))
    return trades


def _weekday_group(trade_date, n, n_wins, start_id, pnl_per_win=10.0, pnl_per_loss=-10.0):
    trades = []
    for i in range(n_wins):
        trades.append(_t(id_=start_id + i, is_win=True, outcome_pnl=pnl_per_win,
                          trade_date=trade_date))
    for i in range(n - n_wins):
        trades.append(_t(id_=start_id + n_wins + i, is_win=False, outcome_pnl=pnl_per_loss,
                          trade_date=trade_date))
    return trades


def _closed_trades(n_beat, n_lose, price=10.0, shares=1.0):
    trades = []
    for i in range(n_beat):
        trades.append(_t(id_=i + 1, vs_spy_pct=1.0, outcome_status="closed",
                          price=price, shares=shares))
    for i in range(n_lose):
        trades.append(_t(id_=1000 + i, vs_spy_pct=-1.0, outcome_status="closed",
                          price=price, shares=shares))
    return trades


# Real weekdays confirmed via .weekday(): 2026-01-05 = Monday (0), 2026-01-09 = Friday (4).
MONDAY = date(2026, 1, 5)
FRIDAY = date(2026, 1, 9)


# ─── _f / _to_date ──────────────────────────────────────────────────────────

def test_f_none_returns_default():
    assert tr._f(None) == 0.0
    assert tr._f(None, default=5.0) == 5.0


def test_f_nan_returns_default():
    assert tr._f(float("nan"), default=-1.0) == -1.0


def test_f_unparseable_returns_default():
    assert tr._f("not-a-number", default=2.0) == 2.0


def test_f_valid_value_parses():
    assert tr._f("3.5") == 3.5
    assert tr._f(3.5) == 3.5


def test_to_date_none_returns_none():
    assert tr._to_date(None) is None


def test_to_date_datetime_uses_date_method():
    assert tr._to_date(datetime(2026, 1, 15, 9, 30)) == date(2026, 1, 15)


def test_to_date_iso_string():
    assert tr._to_date("2026-01-15") == date(2026, 1, 15)


def test_to_date_unparseable_returns_none():
    assert tr._to_date("not-a-date") is None


# ─── _classify ──────────────────────────────────────────────────────────────

def test_classify_true_app_followed():
    assert tr._classify(True) == "app_followed"


def test_classify_false_deviated():
    assert tr._classify(False) == "deviated"


def test_classify_none_discretionary():
    assert tr._classify(None) == "discretionary"


def test_classify_non_bool_truthy_value_discretionary():
    # Only literal True/False match — a stray truthy non-bool falls to discretionary.
    assert tr._classify("yes") == "discretionary"


# ─── _build_spy_returns ─────────────────────────────────────────────────────

def test_build_spy_returns_none_or_empty_returns_empty_dict():
    assert tr._build_spy_returns(None) == {}
    assert tr._build_spy_returns(pd.DataFrame()) == {}


def test_build_spy_returns_missing_close_column_does_not_fall_back_to_open():
    df = pd.DataFrame({"Open": [100.0, 110.0]},
                       index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    assert tr._build_spy_returns(df) == {}


def test_build_spy_returns_normal_series_first_row_excluded():
    df = pd.DataFrame({"Close": [100.0, 110.0, 121.0]},
                       index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]))
    out = tr._build_spy_returns(df)
    assert date(2026, 1, 1) not in out
    assert out[date(2026, 1, 2)] == pytest.approx(10.0)
    assert out[date(2026, 1, 3)] == pytest.approx(10.0)


def test_build_spy_returns_malformed_values_caught_by_outer_except():
    df = pd.DataFrame({"Close": ["abc", "def"]},
                       index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    assert tr._build_spy_returns(df) == {}


# ─── _spy_return_between ────────────────────────────────────────────────────

def test_spy_return_between_none_history_or_dates_returns_none():
    assert tr._spy_return_between(None, date(2026, 1, 1), date(2026, 1, 10)) is None
    df = _spy_df([("2026-01-01", 100.0)])
    assert tr._spy_return_between(df, None, date(2026, 1, 10)) is None
    assert tr._spy_return_between(df, date(2026, 1, 1), None) is None


def test_spy_return_between_missing_close_column_returns_none():
    df = pd.DataFrame({"Open": [100.0, 110.0]},
                       index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    assert tr._spy_return_between(df, date(2026, 1, 1), date(2026, 1, 2)) is None


def test_spy_return_between_nearest_in_window_selection():
    df = _spy_df([
        ("2026-01-02", 100.0),
        ("2026-01-05", 110.0),
        ("2026-01-09", 120.0),
        ("2026-01-12", 130.0),
    ])
    # start_d=01-03 (no exact key) -> first close ON OR AFTER = 01-05 (110.0)
    # end_d=01-10 (no exact key) -> last close ON OR BEFORE = 01-09 (120.0)
    result = tr._spy_return_between(df, date(2026, 1, 3), date(2026, 1, 10))
    assert result == pytest.approx((120.0 - 110.0) / 110.0 * 100.0)


def test_spy_return_between_no_close_on_or_after_start_returns_none():
    df = _spy_df([("2026-01-02", 100.0), ("2026-01-05", 110.0)])
    assert tr._spy_return_between(df, date(2026, 2, 1), date(2026, 3, 1)) is None


def test_spy_return_between_cstart_nonpositive_returns_none():
    df = _spy_df([("2026-01-01", 0.0), ("2026-01-10", 110.0)])
    assert tr._spy_return_between(df, date(2026, 1, 1), date(2026, 1, 10)) is None


# ─── _pair_sells_to_buys (FIFO matcher) ─────────────────────────────────────

def test_pair_sells_to_buys_simple_full_match():
    buy = _raw(id_=1, action="BUY", shares=10.0)
    sell = _raw(id_=2, action="SELL", shares=10.0, price=60.0, trade_date=date(2026, 1, 10))
    out = tr._pair_sells_to_buys([buy, sell])
    m = out["matches"][1]
    assert m["shares_remaining"] == 0
    assert len(m["matched"]) == 1
    assert out["matched_sell_ids"] == {2}


def test_pair_sells_to_buys_partial_sell_leaves_remainder():
    buy = _raw(id_=1, shares=10.0)
    sell = _raw(id_=2, action="SELL", shares=4.0, price=60.0, trade_date=date(2026, 1, 5))
    out = tr._pair_sells_to_buys([buy, sell])
    m = out["matches"][1]
    assert m["shares_remaining"] == 6.0
    assert len(m["matched"]) == 1
    assert m["matched"][0]["shares"] == 4.0


def test_pair_sells_to_buys_multi_buy_spillover_oldest_first():
    buy1 = _raw(id_=1, shares=5.0, trade_date=date(2026, 1, 1))
    buy2 = _raw(id_=2, shares=5.0, trade_date=date(2026, 1, 2))
    sell = _raw(id_=3, action="SELL", shares=8.0, price=60.0, trade_date=date(2026, 1, 3))
    out = tr._pair_sells_to_buys([buy1, buy2, sell])
    m1, m2 = out["matches"][1], out["matches"][2]
    assert len(m1["matched"]) == 1 and m1["matched"][0]["shares"] == 5.0
    assert len(m2["matched"]) == 1 and m2["matched"][0]["shares"] == 3.0
    assert m1["shares_remaining"] == 0.0
    assert m2["shares_remaining"] == 2.0


def test_pair_sells_to_buys_multi_sell_against_one_buy():
    buy = _raw(id_=1, shares=10.0, trade_date=date(2026, 1, 1))
    sell1 = _raw(id_=2, action="SELL", shares=3.0, price=55.0, trade_date=date(2026, 1, 2))
    sell2 = _raw(id_=3, action="SELL", shares=4.0, price=65.0, trade_date=date(2026, 1, 3))
    out = tr._pair_sells_to_buys([buy, sell1, sell2])
    m = out["matches"][1]
    assert len(m["matched"]) == 2
    assert m["shares_remaining"] == 3.0


def test_pair_sells_to_buys_sell_exceeding_inventory_silently_dropped():
    buy = _raw(id_=1, shares=5.0, trade_date=date(2026, 1, 1))
    sell = _raw(id_=2, action="SELL", shares=10.0, price=60.0, trade_date=date(2026, 1, 2))
    out = tr._pair_sells_to_buys([buy, sell])
    m = out["matches"][1]
    assert m["shares_remaining"] == 0.0
    assert len(m["matched"]) == 1
    assert m["matched"][0]["shares"] == 5.0  # only what was available; no crash, no negative


def test_pair_sells_to_buys_different_tickers_never_cross_match():
    buy_a = _raw(id_=1, ticker="AAA", shares=5.0)
    buy_b = _raw(id_=2, ticker="BBB", shares=5.0)
    sell_a = _raw(id_=3, ticker="AAA", action="SELL", shares=5.0, price=60.0,
                  trade_date=date(2026, 1, 2))
    out = tr._pair_sells_to_buys([buy_a, buy_b, sell_a])
    assert out["matches"][2]["matched"] == []
    assert out["matches"][2]["shares_remaining"] == 5.0


def test_pair_sells_to_buys_chronological_order_independent_of_input_order():
    buy1 = _raw(id_=1, shares=5.0, trade_date=date(2026, 1, 1))
    buy2 = _raw(id_=2, shares=5.0, trade_date=date(2026, 1, 2))
    sell = _raw(id_=3, action="SELL", shares=8.0, price=60.0, trade_date=date(2026, 1, 3))
    shuffled = [sell, buy2, buy1]
    out = tr._pair_sells_to_buys(shuffled)
    assert out["matches"][1]["matched"][0]["shares"] == 5.0
    assert out["matches"][2]["matched"][0]["shares"] == 3.0


def test_pair_sells_to_buys_float_epsilon_fully_closed_not_dangling():
    buy = _raw(id_=1, shares=1.0, trade_date=date(2026, 1, 1))
    sells = [
        _raw(id_=2, action="SELL", shares=1 / 3, price=60.0, trade_date=date(2026, 1, 2)),
        _raw(id_=3, action="SELL", shares=1 / 3, price=60.0, trade_date=date(2026, 1, 3)),
        _raw(id_=4, action="SELL", shares=1 / 3, price=60.0, trade_date=date(2026, 1, 4)),
    ]
    out = tr._pair_sells_to_buys([buy] + sells)
    m = out["matches"][1]
    assert len(m["matched"]) == 3
    # Residual is a tiny positive float (~1.1e-16), not exactly zero — the epsilon
    # check is what keeps this treated as fully closed rather than a phantom open lot.
    assert 0 <= m["shares_remaining"] < 1e-9


# ─── _per_trade_outcome ─────────────────────────────────────────────────────

def test_per_trade_outcome_sell_uses_cost_basis_when_present():
    t = _sell_row(price=60.0, shares=10.0, realized_pnl=100.0, cost_basis=500.0,
                  trade_date=date(2026, 1, 10))
    out = tr._per_trade_outcome(t, None, None, date(2026, 1, 15), None)
    assert out["outcome_status"] == "closed"
    assert out["outcome_pct"] == pytest.approx(20.0)
    assert out["exit_price"] == 60.0
    assert out["exit_date"] == date(2026, 1, 10)
    assert out["hold_days"] is None
    assert out["vs_spy_pct"] is None


def test_per_trade_outcome_sell_falls_back_to_price_times_shares_when_cost_basis_falsy():
    t = _sell_row(price=50.0, shares=10.0, realized_pnl=50.0, cost_basis=0.0)
    out = tr._per_trade_outcome(t, None, None, date(2026, 1, 15), None)
    assert out["outcome_pct"] == pytest.approx(10.0)  # 50 / (50*10) * 100


def test_per_trade_outcome_sell_cost_basis_zero_and_price_zero_pct_is_zero_not_none():
    t = _sell_row(price=0.0, shares=10.0, realized_pnl=50.0, cost_basis=0.0)
    out = tr._per_trade_outcome(t, None, None, date(2026, 1, 15), None)
    assert out["outcome_pct"] == 0.0
    assert out["outcome_pct"] is not None


def test_per_trade_outcome_sell_is_win_strict_greater_than_zero():
    win = tr._per_trade_outcome(_sell_row(realized_pnl=50.0), None, None, date(2026, 1, 15), None)
    zero = tr._per_trade_outcome(_sell_row(realized_pnl=0.0), None, None, date(2026, 1, 15), None)
    assert win["is_win"] is True
    assert zero["is_win"] is False


def test_per_trade_outcome_buy_open_no_match_info_priced():
    t = _buy_row(price=50.0, shares=10.0, trade_date=date(2026, 1, 1))
    out = tr._per_trade_outcome(t, None, 60.0, date(2026, 1, 15), None)
    assert out["outcome_status"] == "open"
    assert out["shares_sold"] == 0
    assert out["is_win"] is True
    assert out["outcome_pnl"] == pytest.approx((60.0 - 50.0) * 10.0)
    assert out["hold_days"] == 14
    assert out["exit_price"] == 60.0
    assert out["exit_date"] is None
    assert out["vs_spy_pct"] is None


def test_per_trade_outcome_buy_open_current_price_none_is_win_none():
    out = tr._per_trade_outcome(_buy_row(), None, None, date(2026, 1, 15), None)
    assert out["is_win"] is None
    assert out["exit_price"] is None


def test_per_trade_outcome_buy_open_current_price_nonpositive_is_win_none():
    out = tr._per_trade_outcome(_buy_row(), None, 0.0, date(2026, 1, 15), None)
    assert out["is_win"] is None


def test_per_trade_outcome_buy_closed_weighted_exit_price_not_simple_mean():
    t = _buy_row(price=50.0, shares=10.0, trade_date=date(2026, 1, 1))
    matched = [
        _sell_match(2, 55.0, date(2026, 1, 5), 4.0),
        _sell_match(3, 65.0, date(2026, 1, 10), 6.0),
    ]
    match_info = _match_info(matched=matched, shares_remaining=0.0)
    out = tr._per_trade_outcome(t, match_info, None, date(2026, 1, 15), None)
    weighted = (55.0 * 4 + 65.0 * 6) / 10.0
    simple_mean = (55.0 + 65.0) / 2.0
    assert out["outcome_status"] == "closed"
    assert out["exit_price"] == pytest.approx(round(weighted, 2))
    assert out["exit_price"] != pytest.approx(simple_mean)
    assert out["exit_date"] == date(2026, 1, 10)
    assert out["hold_days"] == 9
    realized = (55.0 - 50.0) * 4 + (65.0 - 50.0) * 6
    assert out["realized_pnl"] == pytest.approx(round(realized, 2))
    assert out["outcome_pnl"] == pytest.approx(round(realized, 2))
    assert out["is_win"] is True
    assert out["vs_spy_pct"] is None  # no spy history supplied


def test_per_trade_outcome_buy_closed_vs_spy_pct_computed_with_spy_data():
    t = _buy_row(price=50.0, shares=10.0, trade_date=date(2026, 1, 1))
    matched = [_sell_match(2, 60.0, date(2026, 1, 10), 10.0)]
    match_info = _match_info(matched=matched, shares_remaining=0.0)
    spy_df = _spy_df([("2026-01-01", 100.0), ("2026-01-10", 110.0)])
    out = tr._per_trade_outcome(t, match_info, None, date(2026, 1, 15), spy_df)
    # pct = ((60-50)*10)/(50*10)*100 = 20.0 ; spy_ret = (110-100)/100*100 = 10.0
    assert out["vs_spy_pct"] == pytest.approx(round(20.0 - 10.0, 2))


def test_per_trade_outcome_buy_partial_exit_price_is_most_recent_sell_not_weighted():
    t = _buy_row(price=50.0, shares=10.0, trade_date=date(2026, 1, 1))
    matched = [
        _sell_match(2, 55.0, date(2026, 1, 5), 3.0),
        _sell_match(3, 65.0, date(2026, 1, 10), 3.0),
    ]
    match_info = _match_info(matched=matched, shares_remaining=4.0)
    out = tr._per_trade_outcome(t, match_info, 70.0, date(2026, 1, 20), None)
    assert out["outcome_status"] == "partial"
    assert out["exit_price"] == 65.0            # most recent sell price only
    assert out["exit_date"] == date(2026, 1, 10)
    assert out["hold_days"] == 19                # today - entry_date, NOT to the sell date
    assert out["vs_spy_pct"] is None              # remainder still open
    realized = (55.0 - 50.0) * 3 + (65.0 - 50.0) * 3
    unrealized = (70.0 - 50.0) * 4.0
    assert out["realized_pnl"] == pytest.approx(round(realized, 2))
    assert out["outcome_pnl"] == pytest.approx(round(realized + unrealized, 2))


# ─── _bucket_metrics ────────────────────────────────────────────────────────

def test_bucket_metrics_sell_dedup_excluded_even_if_filter_matches():
    rows = [_t(sell_dedup=True, is_win=True, outcome_pnl=100.0)]
    out = tr._bucket_metrics(rows, lambda t: True)
    assert out["n_trades"] == 0


def test_bucket_metrics_n_judged_subset_of_bucket():
    rows = [_t(id_=1, is_win=None), _t(id_=2, is_win=True, outcome_pnl=50.0)]
    out = tr._bucket_metrics(rows, lambda t: True)
    assert out["n_trades"] == 2
    assert out["n_judged"] == 1


def test_bucket_metrics_win_rate_none_when_no_judged():
    out = tr._bucket_metrics([_t(is_win=None)], lambda t: True)
    assert out["win_rate"] is None


def test_bucket_metrics_avg_gain_avg_loss_default_zero_not_none():
    only_wins = tr._bucket_metrics([_t(is_win=True, outcome_pnl=50.0)], lambda t: True)
    assert only_wins["avg_gain"] == 50.0
    assert only_wins["avg_loss"] == 0.0

    only_losses = tr._bucket_metrics([_t(is_win=False, outcome_pnl=-30.0)], lambda t: True)
    assert only_losses["avg_gain"] == 0.0
    assert only_losses["avg_loss"] == -30.0


def test_bucket_metrics_net_pnl_sums_wins_and_losses_together():
    rows = [_t(id_=1, is_win=True, outcome_pnl=50.0), _t(id_=2, is_win=False, outcome_pnl=-20.0)]
    out = tr._bucket_metrics(rows, lambda t: True)
    assert out["net_pnl"] == 30.0


# ─── cumulative_pnl_series ───────────────────────────────────────────────────

def test_cumulative_pnl_series_excludes_unjudged_and_dedup():
    rows = [
        _t(id_=1, is_win=None, outcome_pnl=10.0, trade_date=date(2026, 1, 1)),
        _t(id_=2, is_win=True, outcome_pnl=100.0, trade_date=date(2026, 1, 2), sell_dedup=True),
        _t(id_=3, is_win=True, outcome_pnl=50.0, trade_date=date(2026, 1, 3)),
    ]
    out = tr.cumulative_pnl_series(rows)
    assert len(out) == 1
    assert out[0]["cumulative_pnl"] == 50.0


def test_cumulative_pnl_series_running_total_chronological_order():
    rows = [
        _t(id_=1, is_win=True, outcome_pnl=100.0, trade_date=date(2026, 1, 3)),
        _t(id_=2, is_win=False, outcome_pnl=-30.0, trade_date=date(2026, 1, 1)),
        _t(id_=3, is_win=True, outcome_pnl=50.0, trade_date=date(2026, 1, 2)),
    ]
    out = tr.cumulative_pnl_series(rows)
    assert [r["cumulative_pnl"] for r in out] == [-30.0, 20.0, 120.0]


# ─── rolling_win_rate ────────────────────────────────────────────────────────

def test_rolling_win_rate_below_window_returns_empty():
    rows = [_t(id_=i, is_win=True, trade_date=date(2026, 1, i)) for i in range(1, 5)]  # 4 < 5
    assert tr.rolling_win_rate(rows) == []


def test_rolling_win_rate_exactly_at_window_one_point():
    rows = [_t(id_=i, is_win=True, trade_date=date(2026, 1, i)) for i in range(1, 6)]  # 5
    out = tr.rolling_win_rate(rows)
    assert len(out) == 1
    assert out[0]["win_rate"] == 100.0


def test_rolling_win_rate_hand_verified_consecutive_windows():
    pattern = [True, True, False, False, True, True, False]  # 7 judged trades
    rows = [_t(id_=i + 1, is_win=w, trade_date=date(2026, 1, i + 1))
            for i, w in enumerate(pattern)]
    out = tr.rolling_win_rate(rows, window=5)
    # idx4 window [0:5]=T,T,F,F,T -> 3/5=60% ; idx5 [1:6]=T,F,F,T,T -> 3/5=60%
    # idx6 [2:7]=F,F,T,T,F -> 2/5=40%
    assert [r["win_rate"] for r in out] == [60.0, 60.0, 40.0]


# ─── position_size_discipline ───────────────────────────────────────────────

def test_position_size_discipline_only_buy_rows_scored():
    rows = [_t(action="SELL", price=100.0, shares=10.0)]
    out = tr.position_size_discipline(rows, 10000.0)
    assert out["n_trades"] == 0


def test_position_size_discipline_nonpositive_cost_or_portfolio_excluded():
    rows = [_t(id_=1, action="BUY", price=0.0, shares=10.0),
            _t(id_=2, action="BUY", price=10.0, shares=0.0)]
    out = tr.position_size_discipline(rows, 10000.0)
    assert out["n_trades"] == 0

    out2 = tr.position_size_discipline([_t(action="BUY", price=10.0, shares=10.0)], 0.0)
    assert out2["n_trades"] == 0


def test_position_size_discipline_over_ceiling_boundary_at_exactly_15_pct():
    at = tr.position_size_discipline([_t(action="BUY", price=150.0, shares=10.0)], 10000.0)
    assert at["trades"][0]["size_pct"] == pytest.approx(15.0)
    assert at["trades"][0]["over_ceiling"] is False

    over = tr.position_size_discipline([_t(action="BUY", price=150.1, shares=10.0)], 10000.0)
    assert over["trades"][0]["over_ceiling"] is True


def test_position_size_discipline_sorted_descending_by_size_pct():
    rows = [_t(id_=1, action="BUY", price=10.0, shares=10.0),    # 1%
            _t(id_=2, action="BUY", price=50.0, shares=10.0)]    # 5%
    out = tr.position_size_discipline(rows, 10000.0)
    pcts = [r["size_pct"] for r in out["trades"]]
    assert pcts == sorted(pcts, reverse=True)


def test_position_size_discipline_empty_result_averages_none_not_zero():
    out = tr.position_size_discipline([], 10000.0)
    assert out["avg_size_pct"] is None
    assert out["max_size_pct"] is None
    assert out["ceiling_threshold"] == SINGLE_NAME_CEILING


# ─── sector_mix ──────────────────────────────────────────────────────────────

def test_sector_mix_only_buy_rows_counted():
    out = tr.sector_mix([_t(action="SELL", ticker="AAA")], {"AAA": "Tech"})
    assert out["n_sectors"] == 0


def test_sector_mix_missing_or_blank_ticker_bucketed_other():
    rows = [_t(id_=1, action="BUY", ticker="ZZZ", price=10.0, shares=1.0),
            _t(id_=2, action="BUY", ticker="YYY", price=10.0, shares=1.0)]
    out = tr.sector_mix(rows, {"YYY": ""})
    sectors = {s["sector"]: s["n_trades"] for s in out["sectors"]}
    assert sectors["Other"] == 2


def test_sector_mix_zero_buy_rows_early_return_all_defaults():
    out = tr.sector_mix([], {})
    assert out["sectors"] == []
    assert out["n_sectors"] == 0
    assert out["top_sector"] is None
    assert out["top_sector_pct"] is None
    assert out["elevated_threshold"] == SECTOR_ELEVATED


def test_sector_mix_over_elevated_boundary_at_exactly_25_pct():
    rows = [_t(id_=1, action="BUY", ticker="AAA", price=1.0, shares=1.0),
            _t(id_=2, action="BUY", ticker="BBB", price=1.0, shares=1.0),
            _t(id_=3, action="BUY", ticker="CCC", price=1.0, shares=1.0),
            _t(id_=4, action="BUY", ticker="DDD", price=1.0, shares=1.0)]
    sector_map = {"AAA": "Tech", "BBB": "Health", "CCC": "Health", "DDD": "Health"}
    out = tr.sector_mix(rows, sector_map)
    tech = next(s for s in out["sectors"] if s["sector"] == "Tech")
    assert tech["pct_of_trades"] == 25.0
    assert tech["over_elevated"] is False


def test_sector_mix_top_sector_is_largest_share():
    rows = [_t(id_=1, action="BUY", ticker="AAA", price=1.0, shares=1.0),
            _t(id_=2, action="BUY", ticker="BBB", price=1.0, shares=1.0),
            _t(id_=3, action="BUY", ticker="CCC", price=1.0, shares=1.0)]
    sector_map = {"AAA": "Tech", "BBB": "Tech", "CCC": "Health"}
    out = tr.sector_mix(rows, sector_map)
    assert out["top_sector"] == "Tech"
    assert out["top_sector_pct"] == pytest.approx(66.7, abs=0.1)


def test_sector_mix_default_available_marks_normal_result_not_unavailable():
    rows = [_t(id_=1, action="BUY", ticker="AAA", price=1.0, shares=1.0)]
    out = tr.sector_mix(rows, {"AAA": "Tech"})
    assert out["data_unavailable"] is False


def test_sector_mix_zero_rows_default_available_not_flagged_unavailable():
    # No BUY trades is a genuinely different state from "couldn't check" —
    # both yield n_sectors == 0 but only the latter should read as an outage.
    out = tr.sector_mix([], {})
    assert out["data_unavailable"] is False


def test_sector_mix_data_unavailable_abstains_instead_of_fabricating_other():
    # Surface-proprioception F-260 finding #4: with neither holdings nor
    # scanner cache published, every ticker would otherwise default to
    # "Other" via the fallback below, fabricating a 100%-concentration
    # finding for data that was never actually classified.
    rows = [_t(id_=1, action="BUY", ticker="AAA", price=1.0, shares=1.0),
            _t(id_=2, action="BUY", ticker="BBB", price=1.0, shares=1.0),
            _t(id_=3, action="BUY", ticker="CCC", price=1.0, shares=1.0),
            _t(id_=4, action="BUY", ticker="DDD", price=1.0, shares=1.0)]
    out = tr.sector_mix(rows, {}, data_available=False)
    assert out["sectors"] == []
    assert out["n_sectors"] == 0
    assert out["top_sector"] is None
    assert out["top_sector_pct"] is None
    assert out["data_unavailable"] is True


def test_build_insights_sector_concentration_silent_when_data_unavailable():
    # The unavailable result's n_sectors == 0 must suppress Finding 5 the same
    # way the "no BUY trades" path does -- pinned so a future refactor can't
    # reintroduce a fabricated concentration claim through this path.
    sm = tr.sector_mix(
        [_t(id_=1, action="BUY", ticker="AAA", price=1.0, shares=1.0),
         _t(id_=2, action="BUY", ticker="BBB", price=1.0, shares=1.0),
         _t(id_=3, action="BUY", ticker="CCC", price=1.0, shares=1.0),
         _t(id_=4, action="BUY", ticker="DDD", price=1.0, shares=1.0)],
        {}, data_available=False,
    )
    metrics = _metrics(ov={"n_judged": 0, "win_rate": None, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [], sector_mix_data=sm)
    assert not any("concentration warn level" in f for f in out["findings"])


# ─── Lens 3 diagnostics ───────────────────────────────────────────────────────

# -- _diag_holding_period_imbalance --

def test_diag_holding_period_imbalance_below_floor_returns_none():
    trades = ([_t(id_=i, is_win=True, hold_days=5) for i in range(1, 3)] +
              [_t(id_=i, is_win=False, hold_days=15) for i in range(10, 13)])  # only 2 wins
    assert tr._diag_holding_period_imbalance(trades) is None


def test_diag_holding_period_imbalance_gap_exactly_5_critical_vs_just_below_watch():
    wins = [_t(id_=i, is_win=True, hold_days=5) for i in range(1, 4)]
    at_losses = [_t(id_=i, is_win=False, hold_days=10) for i in range(10, 13)]  # gap=5
    rec_at = tr._diag_holding_period_imbalance(wins + at_losses)
    assert rec_at["severity"] == "concern"

    below_losses = [_t(id_=i, is_win=False, hold_days=9.9) for i in range(10, 13)]  # gap=4.9
    rec_below = tr._diag_holding_period_imbalance(wins + below_losses)
    assert rec_below["severity"] == "note"


def test_diag_holding_period_imbalance_gap_exactly_2_watch_vs_just_below_dead_zone():
    wins = [_t(id_=i, is_win=True, hold_days=5) for i in range(1, 4)]
    at_losses = [_t(id_=i, is_win=False, hold_days=7) for i in range(10, 13)]  # gap=2
    rec_at = tr._diag_holding_period_imbalance(wins + at_losses)
    assert rec_at["severity"] == "note"

    below_losses = [_t(id_=i, is_win=False, hold_days=6.9) for i in range(10, 13)]  # gap=1.9
    assert tr._diag_holding_period_imbalance(wins + below_losses) is None


def test_diag_holding_period_imbalance_gap_exactly_neg2_good_vs_just_above_dead_zone():
    at_wins = [_t(id_=i, is_win=True, hold_days=7) for i in range(1, 4)]
    losses = [_t(id_=i, is_win=False, hold_days=5) for i in range(10, 13)]  # gap=-2
    rec_at = tr._diag_holding_period_imbalance(at_wins + losses)
    assert rec_at["severity"] == "good"
    assert rec_at["action"] is None

    above_wins = [_t(id_=i, is_win=True, hold_days=6.9) for i in range(1, 4)]  # gap=-1.9
    assert tr._diag_holding_period_imbalance(above_wins + losses) is None


def test_diag_holding_period_imbalance_gap_exactly_zero_dead_zone_none():
    wins = [_t(id_=i, is_win=True, hold_days=7) for i in range(1, 4)]
    losses = [_t(id_=i, is_win=False, hold_days=7) for i in range(10, 13)]
    assert tr._diag_holding_period_imbalance(wins + losses) is None


# -- _diag_signal_defying_bias --

def test_diag_signal_defying_bias_below_floor_returns_none():
    trades = [_t(id_=i, is_win=True, action="BUY", signal_seen="sell") for i in range(1, 3)]
    assert tr._diag_signal_defying_bias(trades) is None


def test_diag_signal_defying_bias_mixed_signal_classified_bearish_not_bullish():
    # signal_seen contains BOTH "buy" and a bearish word -> classified bearish
    # (signal_bullish requires `not signal_bearish`), so a BUY against it is "defying".
    defying = [_t(id_=i, is_win=False, action="BUY", signal_seen="buy but hold for now",
                  outcome_pnl=-50.0) for i in range(1, 4)]
    rec = tr._diag_signal_defying_bias(defying)
    assert rec is not None
    assert rec["evidence"]["n_defying"] == 3


def test_diag_signal_defying_bias_spread_exactly_100_critical_vs_just_below():
    defying = [_t(id_=i, is_win=False, action="BUY", signal_seen="sell", outcome_pnl=-50.0)
               for i in range(1, 4)]
    compliant_at = [_t(id_=i, is_win=True, action="BUY", signal_seen="buy", outcome_pnl=50.0)
                    for i in range(10, 13)]
    rec_at = tr._diag_signal_defying_bias(defying + compliant_at)  # spread=100
    assert rec_at["severity"] == "concern"

    defying_below = [_t(id_=i, is_win=False, action="BUY", signal_seen="sell", outcome_pnl=-49.0)
                      for i in range(1, 4)]
    rec_below = tr._diag_signal_defying_bias(defying_below + compliant_at)  # spread=99
    assert rec_below is None


def test_diag_signal_defying_bias_spread_exactly_neg100_watch_vs_just_above():
    defying_at = [_t(id_=i, is_win=True, action="BUY", signal_seen="sell", outcome_pnl=50.0)
                  for i in range(1, 4)]
    compliant = [_t(id_=i, is_win=False, action="BUY", signal_seen="buy", outcome_pnl=-50.0)
                 for i in range(10, 13)]
    rec_at = tr._diag_signal_defying_bias(defying_at + compliant)  # spread=-100
    assert rec_at["severity"] == "note"

    defying_above = [_t(id_=i, is_win=True, action="BUY", signal_seen="sell", outcome_pnl=49.0)
                      for i in range(1, 4)]
    rec_above = tr._diag_signal_defying_bias(defying_above + compliant)  # spread=-99
    assert rec_above is None


def test_diag_signal_defying_bias_no_compliant_avg_def_negative_watch_positive_none():
    negative = [_t(id_=i, is_win=False, action="BUY", signal_seen="sell", outcome_pnl=-10.0)
                for i in range(1, 4)]
    rec = tr._diag_signal_defying_bias(negative)
    assert rec["severity"] == "note"

    positive = [_t(id_=i, is_win=True, action="BUY", signal_seen="sell", outcome_pnl=10.0)
                for i in range(1, 4)]
    assert tr._diag_signal_defying_bias(positive) is None


# -- _diag_vs_spy_drag --

def test_diag_vs_spy_drag_below_floor_returns_none():
    trades = [_t(id_=i, vs_spy_pct=5.0, outcome_status="closed") for i in range(1, 3)]
    assert tr._diag_vs_spy_drag(trades) is None


def test_diag_vs_spy_drag_partial_or_open_status_never_qualifies():
    trades = [_t(id_=i, vs_spy_pct=5.0, outcome_status="partial") for i in range(1, 4)]
    assert tr._diag_vs_spy_drag(trades) is None
    trades_open = [_t(id_=i, vs_spy_pct=5.0, outcome_status="open") for i in range(1, 4)]
    assert tr._diag_vs_spy_drag(trades_open) is None


def test_diag_vs_spy_drag_boundary_at_65_pct_good_vs_just_below_watch():
    good = tr._diag_vs_spy_drag(_closed_trades(65, 35))       # exactly 65%
    assert good["severity"] == "good"
    watch = tr._diag_vs_spy_drag(_closed_trades(64, 36))       # just below
    assert watch["severity"] == "note"


def test_diag_vs_spy_drag_boundary_at_50_pct_watch_vs_just_below_critical():
    watch = tr._diag_vs_spy_drag(_closed_trades(50, 50))       # exactly 50%
    assert watch["severity"] == "note"
    critical = tr._diag_vs_spy_drag(_closed_trades(49, 51))    # just below
    assert critical["severity"] == "concern"


def test_diag_vs_spy_drag_cumulative_alpha_dollar_math():
    trades = [
        _t(id_=1, vs_spy_pct=10.0, outcome_status="closed", price=10.0, shares=10.0),  # cost=100
        _t(id_=2, vs_spy_pct=-5.0, outcome_status="closed", price=20.0, shares=5.0),   # cost=100
        _t(id_=3, vs_spy_pct=2.0, outcome_status="closed", price=50.0, shares=2.0),    # cost=100
    ]
    rec = tr._diag_vs_spy_drag(trades)
    expected_alpha = 100 * 0.10 + 100 * (-0.05) + 100 * 0.02
    assert rec["evidence"]["cumulative_alpha"] == pytest.approx(expected_alpha, abs=0.01)


# -- _diag_re_entered_tickers --

def test_diag_re_entered_tickers_single_entry_excluded():
    trades = [_t(id_=1, action="BUY", ticker="AAA", is_win=True, outcome_pnl=10.0)]
    assert tr._diag_re_entered_tickers(trades) is None


def test_diag_re_entered_tickers_negative_branch_takes_priority_over_positive():
    negative = [_t(id_=1, action="BUY", ticker="AAA", is_win=False, outcome_pnl=-50.0),
                _t(id_=2, action="BUY", ticker="AAA", is_win=False, outcome_pnl=-60.0)]
    positive = [_t(id_=3, action="BUY", ticker="BBB", is_win=True, outcome_pnl=50.0),
                _t(id_=4, action="BUY", ticker="BBB", is_win=True, outcome_pnl=60.0)]
    rec = tr._diag_re_entered_tickers(negative + positive)
    assert rec["evidence"]["n_repeated_negative"] == 1
    assert rec["related_trades"]["negative_groups"][0]["ticker"] == "AAA"
    assert rec["related_trades"]["positive_groups"][0]["ticker"] == "BBB"


def test_diag_re_entered_tickers_boundary_at_neg200_critical_vs_just_above_watch():
    at = [_t(id_=1, action="BUY", ticker="AAA", is_win=False, outcome_pnl=-100.0),
          _t(id_=2, action="BUY", ticker="AAA", is_win=False, outcome_pnl=-100.0)]  # -200
    rec_at = tr._diag_re_entered_tickers(at)
    assert rec_at["severity"] == "concern"

    above = [_t(id_=1, action="BUY", ticker="AAA", is_win=False, outcome_pnl=-99.0),
             _t(id_=2, action="BUY", ticker="AAA", is_win=False, outcome_pnl=-100.0)]  # -199
    rec_above = tr._diag_re_entered_tickers(above)
    assert rec_above["severity"] == "note"


def test_diag_re_entered_tickers_all_positive_is_good_severity():
    positive = [_t(id_=1, action="BUY", ticker="BBB", is_win=True, outcome_pnl=50.0),
                _t(id_=2, action="BUY", ticker="BBB", is_win=True, outcome_pnl=60.0)]
    rec = tr._diag_re_entered_tickers(positive)
    assert rec["severity"] == "good"
    assert rec["action"] is None


# -- _diag_trigger_type_effectiveness --

def test_diag_trigger_type_effectiveness_below_floor_returns_none():
    trades = [_t(id_=i, trigger_type="MANUAL", is_win=True) for i in range(1, 3)]
    assert tr._diag_trigger_type_effectiveness(trades) is None


def test_diag_trigger_type_effectiveness_spread_at_20_fires_vs_just_below_none():
    a = _trigger_group("A", 100, 60, start_id=1)
    b_at = _trigger_group("B", 100, 40, start_id=1000)     # spread=20
    rec_at = tr._diag_trigger_type_effectiveness(a + b_at)
    assert rec_at is not None
    assert rec_at["evidence"]["spread_pp"] == pytest.approx(20.0)

    b_below = _trigger_group("B", 100, 41, start_id=1000)  # spread=19
    assert tr._diag_trigger_type_effectiveness(a + b_below) is None


def test_diag_trigger_type_effectiveness_severity_boundary_at_35_critical_vs_below_watch():
    a = _trigger_group("A", 100, 85, start_id=1)
    b_at = _trigger_group("B", 100, 50, pnl_per_loss=-30.0, start_id=1000)  # spread=35, net<0
    rec_at = tr._diag_trigger_type_effectiveness(a + b_at)
    assert rec_at["evidence"]["spread_pp"] == pytest.approx(35.0)
    assert rec_at["severity"] == "concern"

    a2 = _trigger_group("A", 100, 84, start_id=1)
    b2 = _trigger_group("B", 100, 50, pnl_per_loss=-30.0, start_id=1000)    # spread=34
    rec2 = tr._diag_trigger_type_effectiveness(a2 + b2)
    assert rec2["severity"] == "note"


def test_diag_trigger_type_effectiveness_two_distinct_watch_text_templates():
    a = _trigger_group("A", 100, 80, start_id=1)
    b = _trigger_group("B", 100, 48, pnl_per_loss=-1.0, start_id=1000)  # spread=32, net>=0
    rec_lean = tr._diag_trigger_type_effectiveness(a + b)
    assert rec_lean["severity"] == "note"
    assert "Lean toward" in rec_lean["action"]

    a2 = _trigger_group("A", 100, 70, start_id=1)
    b2 = _trigger_group("B", 100, 45, start_id=1000)  # spread=25, in [20,30)
    rec_short = tr._diag_trigger_type_effectiveness(a2 + b2)
    assert rec_short["severity"] == "note"
    assert "Lean toward" not in rec_short["action"]
    assert "outperforming" in rec_short["action"]


# -- _diag_lesson_capture_rate --

def test_diag_lesson_capture_rate_below_floor_returns_none():
    trades = [_t(id_=i, is_win=True) for i in range(1, 6)]  # 5 judged < 6
    assert tr._diag_lesson_capture_rate(trades) is None


def test_diag_lesson_capture_rate_boundary_at_25_pct_dead_zone_vs_just_below_watch():
    at = [_t(id_=i, is_win=True, lesson=("x" if i <= 25 else "")) for i in range(1, 101)]
    assert tr._diag_lesson_capture_rate(at) is None  # exactly 25% -> dead zone

    below = [_t(id_=i, is_win=True, lesson=("x" if i <= 24 else "")) for i in range(1, 101)]
    rec = tr._diag_lesson_capture_rate(below)
    assert rec["severity"] == "note"


def test_diag_lesson_capture_rate_exactly_40_pct_middling_dead_zone_none():
    trades = [_t(id_=i, is_win=True, lesson=("x" if i <= 4 else "")) for i in range(1, 11)]
    assert tr._diag_lesson_capture_rate(trades) is None


def test_diag_lesson_capture_rate_boundary_at_60_pct_good_vs_just_below_none():
    below = [_t(id_=i, is_win=True, lesson=("x" if i <= 59 else "")) for i in range(1, 101)]
    assert tr._diag_lesson_capture_rate(below) is None

    at = [_t(id_=i, is_win=True, lesson=("x" if i <= 60 else "")) for i in range(1, 101)]
    rec = tr._diag_lesson_capture_rate(at)
    assert rec["severity"] == "good"


def test_diag_lesson_capture_rate_good_both_sides_gap_gte15_detailed_template():
    with_lesson = [_t(id_=i, is_win=True, lesson="x") for i in range(1, 7)]      # 6, all win
    without_lesson = [_t(id_=i, is_win=False, lesson="") for i in range(10, 14)]  # 4, all loss
    rec = tr._diag_lesson_capture_rate(with_lesson + without_lesson)  # capture=60%
    assert rec["severity"] == "good"
    assert "outperformance" in rec["detection"]


def test_diag_lesson_capture_rate_good_both_sides_gap_lt15_strong_discipline_template():
    with_lesson = [_t(id_=i, is_win=True, lesson="x") for i in range(1, 7)]      # 6, wl_wr=100
    without_lesson = [_t(id_=i, is_win=True, lesson="") for i in range(10, 14)]  # 4, wol_wr=100
    rec = tr._diag_lesson_capture_rate(with_lesson + without_lesson)  # gap=0
    assert rec["severity"] == "good"
    assert "gap with/without is" in rec["detection"]
    assert "outperformance" not in rec["detection"]


def test_diag_lesson_capture_rate_good_one_side_below_3_short_template():
    trades = [_t(id_=i, is_win=True, lesson=("x" if i <= 4 else "")) for i in range(1, 7)]
    # n=6 (meets the >=6 floor), 4 with lesson (66.7%), 2 without (<3) -> short template
    rec = tr._diag_lesson_capture_rate(trades)
    assert rec["severity"] == "good"
    assert "gap with/without is" not in rec["detection"]
    assert "outperformance" not in rec["detection"]


# -- _diag_day_of_week_timing --

def test_diag_day_of_week_timing_below_total_floor_returns_none():
    trades = _weekday_group(MONDAY, 5, 3, 1) + _weekday_group(FRIDAY, 4, 2, 1000)  # total=9<10
    assert tr._diag_day_of_week_timing(trades) is None


def test_diag_day_of_week_timing_single_weekday_returns_none():
    trades = _weekday_group(MONDAY, 12, 8, 1)  # total>=10 but only 1 distinct weekday
    assert tr._diag_day_of_week_timing(trades) is None


def test_diag_day_of_week_timing_spread_just_below_30_none_vs_at_30_fires():
    below = _weekday_group(MONDAY, 100, 65, 1) + _weekday_group(FRIDAY, 100, 36, 1000)  # spread=29
    assert tr._diag_day_of_week_timing(below) is None

    at = _weekday_group(MONDAY, 100, 65, 1) + _weekday_group(FRIDAY, 100, 35, 1000)  # spread=30
    rec = tr._diag_day_of_week_timing(at)
    assert rec is not None
    assert rec["evidence"]["spread_pp"] == pytest.approx(30.0)


def test_diag_day_of_week_timing_critical_when_worst_net_negative():
    trades = _weekday_group(MONDAY, 100, 65, 1) + _weekday_group(FRIDAY, 100, 35, 1000)
    rec = tr._diag_day_of_week_timing(trades)
    assert rec["severity"] == "concern"


def test_diag_day_of_week_timing_watch_when_worst_net_nonnegative():
    trades = (_weekday_group(MONDAY, 100, 65, 1) +
              _weekday_group(FRIDAY, 100, 35, 1000, pnl_per_loss=-1.0))
    rec = tr._diag_day_of_week_timing(trades)
    assert rec["severity"] == "note"


# ─── build_recommendations ───────────────────────────────────────────────────

def test_build_recommendations_diagnostic_exception_excluded_others_still_returned(monkeypatch):
    def _raiser(trades):
        raise RuntimeError("boom")
    monkeypatch.setattr(tr, "_diag_holding_period_imbalance", _raiser)

    wins = [_t(id_=i, is_win=True, hold_days=5, lesson="x") for i in range(1, 4)]
    losses = [_t(id_=i, is_win=False, hold_days=15, lesson="x") for i in range(10, 13)]
    recs = tr.build_recommendations(wins + losses)
    assert not any(r["pattern_key"] == "holding_period_imbalance" for r in recs)
    assert any(r["pattern_key"] == "lesson_capture_rate" for r in recs)


def test_build_recommendations_sorted_critical_before_watch_before_good(monkeypatch):
    fixed = {
        "_diag_holding_period_imbalance": "good",
        "_diag_signal_defying_bias": "concern",
        "_diag_vs_spy_drag": "note",
        "_diag_re_entered_tickers": None,
        "_diag_trigger_type_effectiveness": None,
        "_diag_lesson_capture_rate": None,
        "_diag_day_of_week_timing": None,
    }
    for name, sev in fixed.items():
        if sev is None:
            monkeypatch.setattr(tr, name, lambda trades: None)
        else:
            monkeypatch.setattr(
                tr, name,
                (lambda s: (lambda trades: {"pattern_key": s, "severity": s}))(sev),
            )
    recs = tr.build_recommendations([])
    assert [r["severity"] for r in recs] == ["concern", "note", "good"]


# ─── build_insights ─────────────────────────────────────────────────────────

# -- Finding 1: signal-compliance --

def test_build_insights_signal_compliance_app_followed_wins():
    metrics = _metrics(af={"n_judged": 5, "win_rate": 80.0}, dv={"n_judged": 2, "win_rate": 50.0},
                        ov={"n_judged": 7, "win_rate": 70.0, "net_pnl": 100.0})
    out = tr.build_insights(metrics, [])
    assert any("outperforming your external" in f for f in out["findings"])


def test_build_insights_signal_compliance_deviated_wins():
    metrics = _metrics(af={"n_judged": 5, "win_rate": 40.0}, dv={"n_judged": 2, "win_rate": 60.0},
                        ov={"n_judged": 7, "win_rate": 45.0, "net_pnl": 100.0})
    out = tr.build_insights(metrics, [])
    assert any("discretionary calls are doing better" in f for f in out["findings"])


def test_build_insights_signal_compliance_similar_no_action_appended():
    metrics = _metrics(af={"n_judged": 5, "win_rate": 55.0}, dv={"n_judged": 2, "win_rate": 50.0},
                        ov={"n_judged": 7, "win_rate": 53.0, "net_pnl": 100.0})
    out = tr.build_insights(metrics, [])
    assert any("similar" in f for f in out["findings"])
    # No action from this finding, no other findings fire -> falls to the generic fallback.
    assert out["next_move"] == (
        "Keep the current cadence — sample size is still building. "
        "Stay disciplined on the signal-compliance log."
    )


def test_build_insights_signal_compliance_guard_below_sample_floor():
    metrics = _metrics(af={"n_judged": 2, "win_rate": 90.0}, dv={"n_judged": 5, "win_rate": 10.0},
                        ov={"n_judged": 7, "win_rate": 50.0, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [])
    assert not any("outperforming your external" in f or "discretionary calls" in f
                   or "win rates are similar" in f for f in out["findings"])


# -- Finding 2: panic-window --

def test_build_insights_panic_window_cost_finding():
    metrics = _metrics(pw={"n_trades": 3, "net_pnl": -100.0, "win_rate": 30.0},
                        ov={"n_judged": 5, "win_rate": 50.0, "net_pnl": -100.0})
    out = tr.build_insights(metrics, [])
    assert any("panic" in f.lower() and "cost" in f.lower() for f in out["findings"])


def test_build_insights_panic_window_benefit_finding():
    metrics = _metrics(pw={"n_trades": 3, "net_pnl": 100.0, "win_rate": 70.0},
                        ov={"n_judged": 5, "win_rate": 50.0, "net_pnl": 100.0})
    out = tr.build_insights(metrics, [])
    assert any("bought" in f.lower() for f in out["findings"])


def test_build_insights_panic_window_dead_zone_no_finding():
    metrics = _metrics(pw={"n_trades": 3, "net_pnl": 20.0, "win_rate": 50.0},
                        ov={"n_judged": 5, "win_rate": 50.0, "net_pnl": 20.0})
    out = tr.build_insights(metrics, [])
    assert not any("panic" in f.lower() for f in out["findings"])


# -- Finding 3: best/worst highlight --

def test_build_insights_best_worst_fires_when_gain_or_loss_present():
    trades = [_t(id_=1, is_win=True, outcome_pnl=100.0), _t(id_=2, is_win=False, outcome_pnl=-50.0)]
    metrics = _metrics(ov={"n_judged": 2, "win_rate": 50.0, "net_pnl": 50.0})
    out = tr.build_insights(metrics, trades)
    assert any("Best:" in f for f in out["findings"])


def test_build_insights_best_worst_all_flat_no_finding():
    trades = [_t(id_=1, is_win=True, outcome_pnl=0.0), _t(id_=2, is_win=False, outcome_pnl=0.0)]
    metrics = _metrics(ov={"n_judged": 2, "win_rate": 50.0, "net_pnl": 0.0})
    out = tr.build_insights(metrics, trades)
    assert not any("Best:" in f for f in out["findings"])


# -- Finding 4: position-size discipline --

def test_build_insights_position_size_finding_fires():
    pdisc = {"n_trades": 3, "n_over_ceiling": 1, "max_size_pct": 20.0, "ceiling_threshold": 15.0}
    metrics = _metrics(ov={"n_judged": 0, "win_rate": None, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [], position_discipline=pdisc)
    assert any("single-name ceiling" in f for f in out["findings"])


def test_build_insights_position_size_guard_below_3_trades():
    pdisc = {"n_trades": 2, "n_over_ceiling": 1, "max_size_pct": 20.0, "ceiling_threshold": 15.0}
    metrics = _metrics(ov={"n_judged": 0, "win_rate": None, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [], position_discipline=pdisc)
    assert not any("single-name ceiling" in f for f in out["findings"])


# -- Finding 5: sector concentration --

def test_build_insights_sector_concentration_finding_fires():
    sm = {"n_sectors": 2, "top_sector_pct": 50.0, "top_sector": "Tech",
          "elevated_threshold": 25.0, "sectors": [{"n_trades": 3}, {"n_trades": 1}]}
    metrics = _metrics(ov={"n_judged": 0, "win_rate": None, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [], sector_mix_data=sm)
    assert any("concentration warn level" in f for f in out["findings"])


def test_build_insights_sector_concentration_guard_below_4_total_trades():
    sm = {"n_sectors": 1, "top_sector_pct": 100.0, "top_sector": "Tech",
          "elevated_threshold": 25.0, "sectors": [{"n_trades": 3}]}
    metrics = _metrics(ov={"n_judged": 0, "win_rate": None, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [], sector_mix_data=sm)
    assert not any("concentration warn level" in f for f in out["findings"])


# -- Finding 6: win-rate trend --

def test_build_insights_trend_up_no_action_appended():
    rolling = [{"win_rate": 80.0, "window": 5}]
    metrics = _metrics(ov={"n_judged": 6, "win_rate": 50.0, "net_pnl": 10.0})
    out = tr.build_insights(metrics, [], rolling_wr_series=rolling)
    assert any("trending" in f and "up" in f for f in out["findings"])
    assert "Recent decision quality" not in (out["next_move"] or "")


def test_build_insights_trend_down_has_action():
    rolling = [{"win_rate": 20.0, "window": 5}]
    metrics = _metrics(ov={"n_judged": 6, "win_rate": 50.0, "net_pnl": 10.0})
    out = tr.build_insights(metrics, [], rolling_wr_series=rolling)
    assert any("trending" in f and "down" in f for f in out["findings"])
    assert "Recent decision quality" in out["next_move"]


def test_build_insights_trend_guard_below_6_judged():
    rolling = [{"win_rate": 80.0, "window": 5}]
    metrics = _metrics(ov={"n_judged": 5, "win_rate": 50.0, "net_pnl": 10.0})
    out = tr.build_insights(metrics, [], rolling_wr_series=rolling)
    assert not any("trending" in f for f in out["findings"])


# -- Verdict tier --

def test_build_insights_verdict_thin_below_3_judged():
    metrics = _metrics(ov={"n_judged": 2, "win_rate": None, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [])
    assert out["verdict"] == "thin"
    assert out["data_thin"] is True


def test_build_insights_verdict_on_track():
    metrics = _metrics(af={"win_rate": 70.0}, ov={"n_judged": 3, "win_rate": 70.0, "net_pnl": 100.0})
    out = tr.build_insights(metrics, [])
    assert out["verdict"] == "on_track"


def test_build_insights_verdict_correct_negative_pnl():
    metrics = _metrics(ov={"n_judged": 3, "win_rate": 50.0, "net_pnl": -100.0})
    out = tr.build_insights(metrics, [])
    assert out["verdict"] == "correct"


def test_build_insights_verdict_correct_weak_signal_compliance():
    metrics = _metrics(af={"n_judged": 3, "win_rate": 30.0},
                        ov={"n_judged": 3, "win_rate": 50.0, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [])
    assert out["verdict"] == "correct"


def test_build_insights_verdict_mixed():
    metrics = _metrics(af={"n_judged": 3, "win_rate": 50.0},
                        ov={"n_judged": 3, "win_rate": 50.0, "net_pnl": 10.0})
    out = tr.build_insights(metrics, [])
    assert out["verdict"] == "mixed"


# -- next_move ordering & fallback --

def test_build_insights_next_move_is_first_appended_action_not_strongest():
    metrics = _metrics(
        af={"n_judged": 5, "win_rate": 80.0}, dv={"n_judged": 2, "win_rate": 50.0},
        pw={"n_trades": 3, "net_pnl": -100.0, "win_rate": 30.0},
        ov={"n_judged": 7, "win_rate": 70.0, "net_pnl": 50.0},
    )
    out = tr.build_insights(metrics, [])
    # Finding 1 (signal-compliance) is checked before Finding 2 (panic-window) —
    # both fire actions here, so next_move must be Finding 1's, not Finding 2's.
    assert out["next_move"] == (
        "Trust app signals on the next entry; revisit the deviation_reason "
        "before acting on external info next time."
    )


def test_build_insights_default_next_move_negative_pnl():
    metrics = _metrics(ov={"n_judged": 5, "win_rate": 50.0, "net_pnl": -10.0})
    out = tr.build_insights(metrics, [])
    assert out["next_move"] == (
        "Pause new entries for one session; review the losing trades' "
        "deviation_reason / lesson columns for the common thread."
    )


def test_build_insights_default_next_move_nonnegative_pnl():
    metrics = _metrics(ov={"n_judged": 5, "win_rate": 50.0, "net_pnl": 0.0})
    out = tr.build_insights(metrics, [])
    assert out["next_move"] == (
        "Keep the current cadence — sample size is still building. "
        "Stay disciplined on the signal-compliance log."
    )


# ─── build_trade_review (orchestration) ─────────────────────────────────────

def test_build_trade_review_lookback_zero_or_negative_window_start_is_all_time_literal():
    df = _journal_df([_journal_row(traded_at="2020-01-01")])
    out_zero = tr.build_trade_review(df, {}, None, date(2026, 1, 15), lookback_days=0)
    assert out_zero["window_start"] == "all-time"
    assert len(out_zero["trades"]) == 1  # far-past row not excluded

    out_neg = tr.build_trade_review(df, {}, None, date(2026, 1, 15), lookback_days=-5)
    assert out_neg["window_start"] == "all-time"


def test_build_trade_review_rows_before_window_start_excluded():
    df = _journal_df([
        _journal_row(id_=1, traded_at="2025-01-01"),  # outside 14-day window
        _journal_row(id_=2, traded_at="2026-01-14"),  # inside
    ])
    out = tr.build_trade_review(df, {}, None, date(2026, 1, 15), lookback_days=14)
    assert [t["id"] for t in out["trades"]] == [2]


def test_build_trade_review_split_action_rows_filtered_entirely():
    df = _journal_df([
        _journal_row(id_=1, action="SPLIT", traded_at="2026-01-10"),
        _journal_row(id_=2, action="BUY", traded_at="2026-01-10"),
    ])
    out = tr.build_trade_review(df, {}, None, date(2026, 1, 15), lookback_days=14)
    assert len(out["trades"]) == 1
    assert out["trades"][0]["id"] == 2
    assert out["metrics"]["overall"]["n_trades"] == 1


def test_build_trade_review_sorted_newest_first():
    df = _journal_df([
        _journal_row(id_=1, action="BUY", traded_at="2026-01-05"),
        _journal_row(id_=2, action="BUY", traded_at="2026-01-10"),
    ])
    out = tr.build_trade_review(df, {}, None, date(2026, 1, 15), lookback_days=14)
    assert [t["id"] for t in out["trades"]] == [2, 1]


def test_build_trade_review_spy_available_false_when_no_history():
    df = _journal_df([_journal_row()])
    out = tr.build_trade_review(df, {}, None, date(2026, 1, 15), lookback_days=14)
    assert out["spy_available"] is False


def test_build_trade_review_spy_available_true_when_history_yields_returns():
    df = _journal_df([_journal_row()])
    spy_df = _spy_df([("2026-01-01", 400.0), ("2026-01-02", 410.0)])
    out = tr.build_trade_review(df, {}, spy_df, date(2026, 1, 15), lookback_days=14)
    assert out["spy_available"] is True


def test_build_trade_review_overall_bucket_includes_everything():
    df = _journal_df([
        _journal_row(id_=1, action="BUY", traded_at="2026-01-05", followed_signal=True),
        _journal_row(id_=2, action="BUY", traded_at="2026-01-06", followed_signal=False),
        _journal_row(id_=3, action="BUY", traded_at="2026-01-07", followed_signal=None),
    ])
    out = tr.build_trade_review(df, {}, None, date(2026, 1, 15), lookback_days=14)
    assert out["metrics"]["overall"]["n_trades"] == 3
    assert out["metrics"]["app_followed"]["n_trades"] == 1
    assert out["metrics"]["deviated"]["n_trades"] == 1
    assert out["metrics"]["discretionary"]["n_trades"] == 1


def test_build_trade_review_window_end_and_lookback_days_echoed():
    out = tr.build_trade_review(_journal_df([]), {}, None, date(2026, 1, 15), lookback_days=14)
    assert out["window_end"] == "2026-01-15"
    assert out["lookback_days"] == 14
    assert out["window_start"] == (date(2026, 1, 15) - timedelta(days=14)).isoformat()


def test_build_trade_review_none_trades_df_handled_gracefully():
    out = tr.build_trade_review(None, {}, None, date(2026, 1, 15), lookback_days=14)
    assert out["trades"] == []
    assert out["metrics"]["overall"]["n_trades"] == 0
