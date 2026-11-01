"""Tests for stock_analyzer/daily_pnl.py — Tier-B positions-scope day P&L
(`compute_positions_day_pnl`), the today's-trades cash delta it consumes
(`today_trade_cash_delta`), and the NaN-safe float coercion helper (`_num`).
Pure dict/list math, no I/O. Previously zero test coverage.
"""
import math

from stock_analyzer import daily_pnl as dp
import pytest

pytestmark = pytest.mark.fast


# ─── _num — NaN/None/junk coercion ────────────────────────────────────────────

def test_num_none_returns_default():
    assert dp._num(None) == 0.0
    assert dp._num(None, default=5.0) == 5.0


def test_num_nan_returns_default():
    assert dp._num(float("nan")) == 0.0
    assert dp._num(float("nan"), default=-1.0) == -1.0


def test_num_junk_string_returns_default():
    assert dp._num("not-a-number") == 0.0


def test_num_valid_value_coerces_to_float():
    assert dp._num("3.5") == 3.5
    assert dp._num(3) == 3.0


# ─── today_trade_cash_delta ───────────────────────────────────────────────────

def test_today_trade_cash_delta_empty_list_is_zero():
    assert dp.today_trade_cash_delta([]) == 0.0


def test_today_trade_cash_delta_sell_adds_cash():
    trades = [{"action": "SELL", "price": 10.0, "shares": 5}]
    assert dp.today_trade_cash_delta(trades) == 50.0


def test_today_trade_cash_delta_buy_subtracts_cash():
    trades = [{"action": "BUY", "price": 10.0, "shares": 5}]
    assert dp.today_trade_cash_delta(trades) == -50.0


def test_today_trade_cash_delta_split_and_other_actions_ignored():
    trades = [
        {"action": "SPLIT", "price": 100.0, "shares": 5},
        {"action": "DIVIDEND", "price": 100.0, "shares": 5},
    ]
    assert dp.today_trade_cash_delta(trades) == 0.0


def test_today_trade_cash_delta_mixed_buy_and_sell_nets_correctly():
    trades = [
        {"action": "SELL", "price": 20.0, "shares": 3},   # +60
        {"action": "BUY", "price": 10.0, "shares": 4},    # -40
        {"action": "SPLIT", "price": 5.0, "shares": 100},  # ignored
    ]
    assert dp.today_trade_cash_delta(trades) == 20.0


# ─── compute_positions_day_pnl — baseline guard ──────────────────────────────

def test_compute_positions_day_pnl_empty_baseline_returns_none():
    assert dp.compute_positions_day_pnl([], {}, [], 1000.0) is None


def test_compute_positions_day_pnl_none_baseline_returns_none():
    assert dp.compute_positions_day_pnl([], None, [], 1000.0) is None


# ─── compute_positions_day_pnl — normal case formula ────────────────────────

def test_compute_positions_day_pnl_normal_case_matches_formula():
    held = [{"ticker": "AAA", "shares": 10, "price": 110.0}]
    baseline = {"AAA": {"shares": 10, "close": 100.0}}
    today_trades = [{"ticker": "AAA", "action": "SELL", "price": 50.0, "shares": 1}]
    total_value = 1100.0

    result = dp.compute_positions_day_pnl(held, baseline, today_trades, total_value)

    current_val = 10 * 110.0
    baseline_val = 10 * 100.0
    cash_delta = 50.0 * 1  # SELL adds
    expected_pnl = current_val - baseline_val + cash_delta
    expected_pct = expected_pnl / total_value * 100.0

    assert result["day_pnl"] == round(expected_pnl, 2)
    assert result["day_pnl_pct"] == round(expected_pct, 2)
    assert result["trade_cash_delta"] == round(cash_delta, 2)
    assert result["current_value"] == round(current_val, 2)
    assert result["baseline_value"] == round(baseline_val, 2)
    assert result["n_baseline"] == 1


def test_compute_positions_day_pnl_total_value_zero_guard_returns_zero_pct():
    held = [{"ticker": "AAA", "shares": 10, "price": 110.0}]
    baseline = {"AAA": {"shares": 10, "close": 100.0}}
    result = dp.compute_positions_day_pnl(held, baseline, [], 0.0)
    assert result["day_pnl_pct"] == 0.0
    assert not math.isnan(result["day_pnl_pct"])


# ─── compute_positions_day_pnl — orphan detection ───────────────────────────

def test_compute_positions_day_pnl_orphan_ticker_neither_held_nor_traded_flagged():
    held = [{"ticker": "AAA", "shares": 10, "price": 110.0}]
    baseline = {
        "AAA": {"shares": 10, "close": 100.0},
        "BBB": {"shares": 5, "close": 50.0},   # vanished with no exit recorded
    }
    result = dp.compute_positions_day_pnl(held, baseline, [], 1000.0)
    assert [o["ticker"] for o in result["orphans"]] == ["BBB"]
    # The error is the FULL prior-close value (5 x $50), not a day move.
    assert result["orphans"][0]["value_impact"] == -250.0


def test_compute_positions_day_pnl_still_held_ticker_is_not_an_orphan():
    held = [{"ticker": "AAA", "shares": 10, "price": 110.0}]
    baseline = {"AAA": {"shares": 10, "close": 100.0}}
    result = dp.compute_positions_day_pnl(held, baseline, [], 1000.0)
    assert result["orphans"] == []


def test_compute_positions_day_pnl_ticker_sold_today_is_not_an_orphan():
    held = []  # fully exited
    baseline = {"AAA": {"shares": 10, "close": 100.0}}
    today_trades = [{"ticker": "AAA", "action": "SELL", "price": 105.0, "shares": 10}]
    result = dp.compute_positions_day_pnl(held, baseline, today_trades, 1000.0)
    assert result["orphans"] == []


def test_compute_positions_day_pnl_orphans_sorted_alphabetically():
    held = []
    baseline = {
        "ZZZ": {"shares": 1, "close": 10.0},
        "AAA": {"shares": 1, "close": 10.0},
        "MMM": {"shares": 1, "close": 10.0},
    }
    result = dp.compute_positions_day_pnl(held, baseline, [], 1000.0)
    assert [o["ticker"] for o in result["orphans"]] == ["AAA", "MMM", "ZZZ"]


# ─── compute_positions_day_pnl — rounding ───────────────────────────────────

def test_compute_positions_day_pnl_rounds_all_numeric_outputs():
    held = [{"ticker": "AAA", "shares": 3, "price": 100.123456}]
    baseline = {"AAA": {"shares": 3, "close": 99.987654}}
    result = dp.compute_positions_day_pnl(held, baseline, [], 300.0)
    for key in ("day_pnl", "day_pnl_pct", "trade_cash_delta", "current_value", "baseline_value"):
        value = result[key]
        assert round(value, 2) == value


# ─── today_trade_share_delta ────────────────────────────────────────────────

def test_share_delta_buy_is_positive_and_sell_is_negative():
    trades = [
        {"ticker": "AAA", "action": "BUY",  "shares": 5,  "price": 10.0},
        {"ticker": "BBB", "action": "SELL", "shares": 3,  "price": 10.0},
    ]
    assert dp.today_trade_share_delta(trades) == {"AAA": 5.0, "BBB": -3.0}


def test_share_delta_nets_multiple_rows_for_the_same_ticker():
    trades = [
        {"ticker": "AAA", "action": "BUY",  "shares": 10, "price": 10.0},
        {"ticker": "AAA", "action": "SELL", "shares": 4,  "price": 11.0},
        {"ticker": "AAA", "action": "BUY",  "shares": 1,  "price": 12.0},
    ]
    assert dp.today_trade_share_delta(trades) == {"AAA": 7.0}


def test_share_delta_is_case_insensitive_on_ticker_and_action():
    trades = [{"ticker": "aaa", "action": "buy", "shares": 2, "price": 10.0}]
    assert dp.today_trade_share_delta(trades) == {"AAA": 2.0}


def test_share_delta_excludes_split_rows_entirely():
    """SPLIT stores the adjusted TOTAL, not a delta - summing it would corrupt
    the expected share count far more than leaving the ticker unchecked."""
    trades = [{"ticker": "AAA", "action": "SPLIT", "shares": 40, "price": 0.0}]
    assert dp.today_trade_share_delta(trades) == {}


def test_share_delta_excludes_decorated_split_actions():
    """Every other SPLIT check in the codebase matches by substring, so a
    decorated action like SPLIT 4:1 must be excluded here too."""
    trades = [{"ticker": "AAA", "action": "SPLIT 4:1", "shares": 40, "price": 0.0}]
    assert dp.today_trade_share_delta(trades) == {}


def test_share_delta_ignores_rows_with_no_ticker():
    trades = [{"ticker": "", "action": "BUY", "shares": 5, "price": 10.0}]
    assert dp.today_trade_share_delta(trades) == {}


def test_share_delta_coerces_junk_shares_to_zero_without_raising():
    trades = [{"ticker": "AAA", "action": "BUY", "shares": None, "price": 10.0}]
    assert dp.today_trade_share_delta(trades) == {"AAA": 0.0}


# ─── reconcile_baseline — quantity drift (the 2026-08-23 DELL class) ────────

def test_qty_drift_flags_a_share_count_no_trade_explains():
    """The defect that made a $1,091.62 error silent: still held, so the orphan
    check cannot see it, but at a share count nothing accounts for."""
    held = [{"ticker": "DELL", "shares": 6.0, "price": 272.90}]
    baseline = {"DELL": {"shares": 2.0, "close": 270.0}}
    out = dp.reconcile_baseline(held, baseline, [])
    assert out["orphans"] == []          # invisible to the old check, by construction
    assert len(out["qty_drift"]) == 1
    row = out["qty_drift"][0]
    assert row["ticker"] == "DELL"
    assert row["baseline_shares"] == 2.0
    assert row["expected_shares"] == 2.0
    assert row["current_shares"] == 6.0
    assert row["drift_shares"] == 4.0
    assert row["value_impact"] == round(4.0 * 272.90, 2)


def test_qty_drift_silent_when_todays_buy_explains_the_change():
    held = [{"ticker": "AAA", "shares": 15.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    trades = [{"ticker": "AAA", "action": "BUY", "shares": 5, "price": 100.0}]
    assert dp.reconcile_baseline(held, baseline, trades)["qty_drift"] == []


def test_qty_drift_silent_when_todays_partial_sell_explains_the_change():
    held = [{"ticker": "AAA", "shares": 4.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    trades = [{"ticker": "AAA", "action": "SELL", "shares": 6, "price": 100.0}]
    assert dp.reconcile_baseline(held, baseline, trades)["qty_drift"] == []


def test_qty_drift_fires_when_a_trade_only_partly_explains_the_change():
    """A logged buy of 5 but 8 more shares present - the residual 3 is the gap."""
    held = [{"ticker": "AAA", "shares": 18.0, "price": 50.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 49.0}}
    trades = [{"ticker": "AAA", "action": "BUY", "shares": 5, "price": 50.0}]
    row = dp.reconcile_baseline(held, baseline, trades)["qty_drift"][0]
    assert row["expected_shares"] == 15.0
    assert row["drift_shares"] == 3.0
    assert row["value_impact"] == 150.0


def test_qty_drift_negative_when_fewer_shares_held_than_expected():
    """Signed: fewer shares than expected UNDERSTATES the day P&L."""
    held = [{"ticker": "AAA", "shares": 6.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    row = dp.reconcile_baseline(held, baseline, [])["qty_drift"][0]
    assert row["drift_shares"] == -4.0
    assert row["value_impact"] == -400.0


def test_qty_drift_ignores_sub_tolerance_float_noise():
    held = [{"ticker": "AAA", "shares": 10.0000001, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    assert dp.reconcile_baseline(held, baseline, [])["qty_drift"] == []


def test_qty_drift_skips_a_ticker_split_today_rather_than_crying_wolf():
    """A split rewrites the count by a ratio this function cannot recover from
    the row, so the honest answer is to not report a drift it cannot compute."""
    held = [{"ticker": "AAA", "shares": 40.0, "price": 25.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 100.0}}
    trades = [{"ticker": "AAA", "action": "SPLIT", "shares": 40, "price": 0.0}]
    assert dp.reconcile_baseline(held, baseline, trades)["qty_drift"] == []


def test_qty_drift_matches_tickers_case_insensitively():
    held = [{"ticker": "aaa", "shares": 12.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    assert len(dp.reconcile_baseline(held, baseline, [])["qty_drift"]) == 1


# ─── reconcile_baseline — held but unbaselined ──────────────────────────────

def test_unbaselined_flags_a_held_position_missing_from_the_baseline():
    """The worst shape: the FULL position value lands in the delta as gain."""
    held = [
        {"ticker": "AAA", "shares": 10.0, "price": 100.0},
        {"ticker": "BBB", "shares": 4.0,  "price": 250.0},   # no baseline row
    ]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    out = dp.reconcile_baseline(held, baseline, [])
    assert out["qty_drift"] == []
    assert out["unbaselined"] == [
        {"ticker": "BBB", "current_shares": 4.0,
         "unexplained_shares": 4.0, "value_impact": 1000.0}
    ]


def test_unbaselined_silent_for_a_position_opened_today():
    """Bought today: the cash leg already offsets it, so it is not a gap."""
    held = [{"ticker": "BBB", "shares": 4.0, "price": 250.0}]
    baseline = {}
    trades = [{"ticker": "BBB", "action": "BUY", "shares": 4, "price": 248.0}]
    assert dp.reconcile_baseline(held, baseline, trades)["unbaselined"] == []


def test_unbaselined_silent_for_a_zero_share_row():
    held = [{"ticker": "BBB", "shares": 0.0, "price": 250.0}]
    assert dp.reconcile_baseline(held, {}, [])["unbaselined"] == []


# ─── reconcile_baseline — the three shapes are independent ──────────────────

def test_all_four_disagreement_shapes_reported_together():
    held = [
        {"ticker": "AAA", "shares": 12.0, "price": 100.0},   # drift (+2)
        {"ticker": "CCC", "shares": 3.0,  "price": 200.0},   # unbaselined
    ]
    baseline = {
        "AAA": {"shares": 10.0, "close": 99.0},
        "BBB": {"shares": 5.0,  "close": 50.0},              # orphan
    }
    trades = [                                               # unbaselined_sells
        {"ticker": "DDD", "action": "SELL", "shares": 4.0, "price": 40.0},
    ]
    out = dp.reconcile_baseline(held, baseline, trades)
    assert [o["ticker"] for o in out["orphans"]] == ["BBB"]
    assert [r["ticker"] for r in out["qty_drift"]] == ["AAA"]
    assert [r["ticker"] for r in out["unbaselined"]] == ["CCC"]
    assert [r["ticker"] for r in out["unbaselined_sells"]] == ["DDD"]


def test_reconcile_baseline_returns_empty_lists_never_none_when_clean():
    """No-disagreement and not-checked are both knowable here, so the
    offline-sentinel convention does not apply - these are always lists."""
    held = [{"ticker": "AAA", "shares": 10.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    out = dp.reconcile_baseline(held, baseline, [])
    assert out == {"orphans": [], "qty_drift": [], "unbaselined": [], "unbaselined_sells": []}


def test_reconcile_results_are_sorted_by_ticker():
    held = [
        {"ticker": "ZZZ", "shares": 2.0, "price": 10.0},
        {"ticker": "AAA", "shares": 2.0, "price": 10.0},
        {"ticker": "MMM", "shares": 2.0, "price": 10.0},
    ]
    out = dp.reconcile_baseline(held, {}, [])
    assert [r["ticker"] for r in out["unbaselined"]] == ["AAA", "MMM", "ZZZ"]


# ─── compute_positions_day_pnl exposes the new keys ─────────────────────────

def test_compute_positions_day_pnl_surfaces_qty_drift_and_unbaselined():
    held = [
        {"ticker": "AAA", "shares": 12.0, "price": 100.0},
        {"ticker": "CCC", "shares": 3.0,  "price": 200.0},
    ]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    result = dp.compute_positions_day_pnl(held, baseline, [], 1800.0)
    assert [r["ticker"] for r in result["qty_drift"]] == ["AAA"]
    assert [r["ticker"] for r in result["unbaselined"]] == ["CCC"]


def test_qty_drift_value_impact_equals_the_day_pnl_error():
    """The dollar figure in the banner must be the ACTUAL distortion, not an
    approximation - that is what makes it actionable."""
    held_wrong = [{"ticker": "AAA", "shares": 14.0, "price": 100.0}]
    held_right = [{"ticker": "AAA", "shares": 10.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    wrong = dp.compute_positions_day_pnl(held_wrong, baseline, [], 1400.0)
    right = dp.compute_positions_day_pnl(held_right, baseline, [], 1000.0)
    assert wrong["qty_drift"][0]["value_impact"] == round(
        wrong["day_pnl"] - right["day_pnl"], 2)


# ─── reconcile_baseline — the truncated-vs-raw unit guard ───────────────────
# portfolio.build_portfolio_df stores int(shares) for display, while trade
# deltas are raw to 4dp. Comparing them reports truncation as "drift", which
# would fire on every fractional-quantity day. See the UNIT GUARD comment.

def test_qty_drift_skips_when_a_fractional_delta_meets_truncated_share_counts():
    """The live false-positive shape: 10 held (truncated from 10.5), baseline 10,
    a real 0.5-share buy today. Expected 10.5 vs current 10 is truncation, not
    drift, and must stay silent."""
    held = [{"ticker": "AAA", "shares": 10.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    trades = [{"ticker": "AAA", "action": "BUY", "shares": 0.5, "price": 100.0}]
    assert dp.reconcile_baseline(held, baseline, trades)["qty_drift"] == []


def test_qty_drift_still_fires_on_whole_share_drift_with_no_fractional_trade():
    """The guard must not swallow the case it was built for."""
    held = [{"ticker": "DELL", "shares": 6.0, "price": 272.90}]
    baseline = {"DELL": {"shares": 2.0, "close": 270.0}}
    trades = [{"ticker": "DELL", "action": "BUY", "shares": 1, "price": 272.0}]
    row = dp.reconcile_baseline(held, baseline, trades)["qty_drift"][0]
    assert row["expected_shares"] == 3.0
    assert row["drift_shares"] == 3.0


def test_qty_drift_compares_normally_when_both_sides_are_genuinely_fractional():
    """The guard keys off an integral value on EITHER side (a whole number might
    be a truncated fractional), so a book that really does carry fractional
    shares on both sides is still checked."""
    held = [{"ticker": "AAA", "shares": 12.5, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.25, "close": 99.0}}
    row = dp.reconcile_baseline(held, baseline, [])["qty_drift"][0]
    assert row["drift_shares"] == 2.25


def test_qty_drift_guard_catches_a_large_drift_despite_a_same_day_fractional_fill():
    """2026-08-24 regression test: this exact shape used to be silently
    swallowed (an all-or-nothing guard skipped the whole check once a
    fractional fill appeared, regardless of the drift's size — a 9.5-share
    drift reported as zero). QTY_DRIFT_TRUNCATION_AMBIGUITY_SHARES bounds the
    guard to genuinely-ambiguous sub-1.0-share residuals, so a drift this
    large (far above the 1.0-share truncation-ambiguity ceiling) is now
    reported regardless of the same-day fractional fill."""
    held = [{"ticker": "AAA", "shares": 20.0, "price": 100.0}]     # truncated
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    trades = [{"ticker": "AAA", "action": "BUY", "shares": 0.5, "price": 100.0}]
    row = dp.reconcile_baseline(held, baseline, trades)["qty_drift"][0]
    assert row["drift_shares"] == 9.5


def test_qty_drift_guard_still_suppresses_a_genuinely_ambiguous_sub_bound_residual():
    """The narrowed guard's remaining blind spot, by design: a residual BELOW
    QTY_DRIFT_TRUNCATION_AMBIGUITY_SHARES on a fractional-fill day cannot be
    distinguished from truncation noise, so it is still suppressed. This is
    the mathematical floor (truncation ambiguity is provably < 1.0 share),
    not a policy choice to revisit."""
    held = [{"ticker": "AAA", "shares": 10.0, "price": 100.0}]     # truncated
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    trades = [{"ticker": "AAA", "action": "BUY", "shares": 0.5, "price": 100.0}]
    # expected = 10 + 0.5 = 10.5 (fractional); unexplained = 10 - 10.5 = -0.5,
    # under the 1.0-share bound → still suppressed.
    assert dp.reconcile_baseline(held, baseline, trades)["qty_drift"] == []


def test_qty_drift_guard_boundary_just_under_vs_just_over_the_bound():
    """Sanity-check the boundary itself: a residual just under
    QTY_DRIFT_TRUNCATION_AMBIGUITY_SHARES (1.0) stays suppressed, and a
    residual just over it is reported -- confirming the fix changed the
    guard's SIZE threshold, not just the two extreme cases above."""
    held = [{"ticker": "AAA", "shares": 10.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}

    just_under = [{"ticker": "AAA", "action": "SELL", "shares": 0.99, "price": 100.0}]
    assert dp.reconcile_baseline(held, baseline, just_under)["qty_drift"] == []

    just_over = [{"ticker": "AAA", "action": "SELL", "shares": 1.01, "price": 100.0}]
    row = dp.reconcile_baseline(held, baseline, just_over)["qty_drift"][0]
    assert row["drift_shares"] == 1.01


# ─── reconcile_baseline — PARTIALLY explained discrepancies ─────────────────
# An early version exempted any ticker with a trade today, which hid the case
# where a trade explains only part of the change. "Was there a trade" is not
# the question; the unexplained residual is.

def test_unbaselined_fires_when_todays_trade_explains_only_part_of_the_position():
    """Stale baseline, position opened after it, added to today: the 5 bought
    today are explained, the other 10 shares are pure unbacked gain."""
    held = [{"ticker": "AAA", "shares": 15.0, "price": 100.0}]
    trades = [{"ticker": "AAA", "action": "BUY", "shares": 5, "price": 100.0}]
    out = dp.reconcile_baseline(held, {}, trades)
    assert len(out["unbaselined"]) == 1
    row = out["unbaselined"][0]
    assert row["unexplained_shares"] == 10.0
    assert row["value_impact"] == 1000.0


def test_partially_sold_baseline_ticker_now_unheld_is_flagged_not_silent():
    """Baseline 10, sold 4 today, holding nothing: 6 shares vanished. Not an
    orphan (it WAS traded), so the old check couldn't see it either."""
    baseline = {"AAA": {"shares": 10.0, "close": 50.0}}
    trades = [{"ticker": "AAA", "action": "SELL", "shares": 4, "price": 52.0}]
    out = dp.reconcile_baseline([], baseline, trades)
    assert out["orphans"] == []
    row = out["qty_drift"][0]
    assert row["current_shares"] == 0.0
    assert row["expected_shares"] == 6.0
    assert row["drift_shares"] == -6.0
    # Unheld names have no live price, so the residual is valued at the
    # baseline close — the same basis the day-P&L subtracted it at.
    assert row["value_impact"] == -300.0


def test_fully_sold_baseline_ticker_stays_silent():
    """The legitimate case the above must not swallow: sold the lot today."""
    baseline = {"AAA": {"shares": 10.0, "close": 50.0}}
    trades = [{"ticker": "AAA", "action": "SELL", "shares": 10, "price": 52.0}]
    out = dp.reconcile_baseline([], baseline, trades)
    assert out == {"orphans": [], "qty_drift": [], "unbaselined": [], "unbaselined_sells": []}


def test_orphan_is_not_also_reported_as_qty_drift():
    """The two keys must not double-count the same ticker."""
    baseline = {"BBB": {"shares": 5.0, "close": 50.0}}
    out = dp.reconcile_baseline([], baseline, [])
    assert [o["ticker"] for o in out["orphans"]] == ["BBB"]
    assert out["qty_drift"] == []


# ─── unit guard — a fractional value on EITHER side blocks the compare ──────

def test_unit_guard_skips_a_fractional_baseline_against_a_truncated_holding():
    """No writer produces a fractional baseline today, but if one ever does,
    this must fail closed rather than newly cry wolf on every position."""
    held = [{"ticker": "AAA", "shares": 10.0, "price": 100.0}]   # truncated
    baseline = {"AAA": {"shares": 10.5, "close": 99.0}}
    assert dp.reconcile_baseline(held, baseline, [])["qty_drift"] == []


def test_unit_guard_allows_fractional_trades_that_net_to_a_whole_number():
    """floor(x + n) == floor(x) + n for integer n, so an integral NET delta
    compares exactly even when the individual fills were fractional."""
    held = [{"ticker": "AAA", "shares": 11.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    trades = [
        {"ticker": "AAA", "action": "BUY", "shares": 0.5, "price": 100.0},
        {"ticker": "AAA", "action": "BUY", "shares": 0.5, "price": 100.0},
    ]
    assert dp.reconcile_baseline(held, baseline, trades)["qty_drift"] == []
    # The above has a ZERO residual, so it would pass via the tolerance branch
    # whether or not the guard behaves as titled. Same fills, plus a real
    # 3-share drift: the guard must let this THROUGH, not suppress it.
    held_drifted = [{"ticker": "AAA", "shares": 14.0, "price": 100.0}]
    row = dp.reconcile_baseline(held_drifted, baseline, trades)["qty_drift"][0]
    assert row["drift_shares"] == 3.0


# ─── reconcile_baseline — unbaselined_sells (the fourth shape) ──────────────
# A ticker sold today that is in NEITHER baseline NOR held: bought on some day
# after the last baseline snapshot (stale-baseline gap), held uncaptured, then
# sold today. Only the sell appears in today_trades. The sell-side twin of
# unbaselined.

def test_unbaselined_sell_flags_a_sale_with_no_baseline_or_holding():
    """The core case: sold today, never baselined, not held now. The full
    sale is unbacked -- nothing in the function's inputs explains where the
    shares came from."""
    trades = [{"ticker": "DDD", "action": "SELL", "shares": 5.0, "price": 40.0}]
    out = dp.reconcile_baseline([], {}, trades)
    assert len(out["unbaselined_sells"]) == 1
    row = out["unbaselined_sells"][0]
    assert row["unbacked_shares"] == 5.0
    assert row["value_impact"] == 200.0   # positive = day-P&L overstated


def test_unbaselined_sell_not_flagged_when_same_day_round_trip_is_flat():
    """Bought and sold the same quantity today, never held/baselined: both
    legs enter cash_delta and the identity is exact (current_val and
    baseline_val both contribute zero for a name never held at prior close
    and not held now) -- this must NOT be flagged."""
    trades = [
        {"ticker": "DDD", "action": "BUY",  "shares": 5.0, "price": 38.0},
        {"ticker": "DDD", "action": "SELL", "shares": 5.0, "price": 40.0},
    ]
    assert dp.reconcile_baseline([], {}, trades)["unbaselined_sells"] == []


def test_unbaselined_sell_not_flagged_when_bought_more_than_sold():
    """Net BUY today (even though not currently held is impossible in a
    consistent book) must not be flagged -- only an EXCESS of sold-over-bought
    is unexplained. Guards the '>' direction, not just the '==' case above."""
    trades = [
        {"ticker": "DDD", "action": "BUY",  "shares": 6.0, "price": 38.0},
        {"ticker": "DDD", "action": "SELL", "shares": 5.0, "price": 40.0},
    ]
    assert dp.reconcile_baseline([], {}, trades)["unbaselined_sells"] == []


def test_unbaselined_sell_flags_only_the_excess_over_a_partial_same_day_buy():
    """Bought 2, sold 5, same day, never baselined/held: only the 3-share
    excess is unbacked -- the other 2 are a legitimate same-day round trip
    already accounted for in cash_delta."""
    trades = [
        {"ticker": "DDD", "action": "BUY",  "shares": 2.0, "price": 38.0},
        {"ticker": "DDD", "action": "SELL", "shares": 5.0, "price": 40.0},
    ]
    row = dp.reconcile_baseline([], {}, trades)["unbaselined_sells"][0]
    assert row["unbacked_shares"] == 3.0


def test_unbaselined_sell_values_the_excess_at_the_sell_vwap():
    """Multiple sell fills at different prices: the excess is valued at the
    volume-weighted average sell price, not the first/last/highest fill."""
    trades = [
        {"ticker": "DDD", "action": "SELL", "shares": 3.0, "price": 30.0},
        {"ticker": "DDD", "action": "SELL", "shares": 2.0, "price": 40.0},
    ]
    # VWAP = (3*30 + 2*40) / 5 = 170/5 = 34.0; excess = 5.0 (no buys)
    row = dp.reconcile_baseline([], {}, trades)["unbaselined_sells"][0]
    assert row["unbacked_shares"] == 5.0
    assert row["value_impact"] == 170.0   # 5.0 * 34.0


def test_unbaselined_sell_skipped_when_ticker_is_held():
    """A ticker still held is handled by the qty_drift/unbaselined branch
    above, not this one -- must not double-report."""
    held = [{"ticker": "DDD", "shares": 2.0, "price": 40.0}]
    trades = [{"ticker": "DDD", "action": "SELL", "shares": 5.0, "price": 40.0}]
    out = dp.reconcile_baseline(held, {}, trades)
    assert out["unbaselined_sells"] == []


def test_unbaselined_sell_skipped_when_ticker_has_a_baseline_row():
    """A ticker with a baseline row is judged by the main loop above (even if
    not currently held), not this branch -- must not double-report. Sold only
    PART of the baselined lot, so the main loop's own qty_drift fires; the
    point here is that unbaselined_sells stays empty regardless."""
    baseline = {"DDD": {"shares": 5.0, "close": 39.0}}
    trades = [{"ticker": "DDD", "action": "SELL", "shares": 3.0, "price": 40.0}]
    out = dp.reconcile_baseline([], baseline, trades)
    assert out["unbaselined_sells"] == []
    assert len(out["qty_drift"]) == 1


def test_unbaselined_sell_skipped_on_a_split_day():
    """Same treatment as the main loop: a split rewrites the share count by a
    ratio this function cannot recover from the row, and there is no error to
    report on a split day."""
    trades = [{"ticker": "DDD", "action": "SPLIT", "shares": 10.0, "price": 0.0}]
    assert dp.reconcile_baseline([], {}, trades)["unbaselined_sells"] == []


def test_unbaselined_sell_ignores_sub_tolerance_excess():
    """A sub-tolerance excess (float noise, not a real unbacked sale) stays
    silent -- same BROKER_DRIFT_SHARE_TOL used everywhere else in this
    function, not a second policy number."""
    trades = [
        {"ticker": "DDD", "action": "BUY",  "shares": 5.0,     "price": 38.0},
        {"ticker": "DDD", "action": "SELL", "shares": 5.0005, "price": 40.0},
    ]
    assert dp.reconcile_baseline([], {}, trades)["unbaselined_sells"] == []


def test_compute_positions_day_pnl_surfaces_unbaselined_sells():
    """compute_positions_day_pnl must not silently drop the fourth shape --
    the same regression class as F-250's Summary-coordination review finding."""
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}   # need a baseline for compute_ to run at all
    held = [{"ticker": "AAA", "shares": 10.0, "price": 100.0}]
    trades = [{"ticker": "DDD", "action": "SELL", "shares": 5.0, "price": 40.0}]
    result = dp.compute_positions_day_pnl(held, baseline, trades, 1000.0)
    assert [r["ticker"] for r in result["unbaselined_sells"]] == ["DDD"]


# ─── fill_late_logged_baseline ────────────────────────────────────────────────

import copy
import datetime as _dtm

_BD = _dtm.date(2026, 10, 30)      # baseline date (Fri)
_TODAY = _dtm.date(2026, 11, 2)    # today (Mon)
_MID = _dtm.date(2026, 10, 31)     # logged Saturday


def _jb(tk, d, sh, px, action="BUY"):
    return {"ticker": tk, "action": action, "shares": sh, "price": px, "date": d}


def _fill(held, baseline, today_trades, journal, closes):
    return dp.fill_late_logged_baseline(held, baseline, today_trades, journal,
                                        _BD, _TODAY, closes)


def test_fill_observed_case_baseline_close():
    held = [{"ticker": "MRVL", "shares": 8, "price": 110.0}]
    base = {"AAPL": {"shares": 1, "close": 100.0}}
    held2 = held + [{"ticker": "AAPL", "shares": 1, "price": 100.0}]
    nb, filled = _fill(held2, base, [], [_jb("MRVL", _BD, 8, 100.0)], {"MRVL": 105.0})
    assert nb["MRVL"] == {"shares": 8, "close": 105.0}
    assert filled == [{"ticker": "MRVL", "shares": 8, "close": 105.0, "basis": "baseline_close"}]
    res = dp.compute_positions_day_pnl(held2, nb, [], 1000.0)
    assert res["unbaselined"] == []
    assert res["day_pnl"] == pytest.approx(8 * (110.0 - 105.0))


def test_fill_buy_between_uses_fill_price():
    held = [{"ticker": "MU", "shares": 5, "price": 90.0}]
    nb, filled = _fill(held, {"X": {"shares": 1, "close": 1.0}}, [],
                       [_jb("MU", _MID, 5, 88.0)], {})
    assert nb["MU"] == {"shares": 5, "close": 88.0}
    assert filled[0]["basis"] == "fill"


def test_fill_mixed_weighted_close():
    held = [{"ticker": "CSCO", "shares": 10, "price": 60.0}]
    journal = [_jb("CSCO", _BD, 6, 50.0), _jb("CSCO", _MID, 4, 56.0)]
    nb, filled = _fill(held, {"X": {"shares": 1, "close": 1.0}}, [], journal, {"CSCO": 52.0})
    assert nb["CSCO"]["close"] == pytest.approx((6 * 52.0 + 4 * 56.0) / 10)
    assert filled[0]["basis"] == "mixed"


def test_fill_bought_today_only_not_filled():
    held = [{"ticker": "NEW", "shares": 3, "price": 10.0}]
    today = [{"ticker": "NEW", "action": "BUY", "shares": 3, "price": 9.5}]
    nb, filled = _fill(held, {"X": {"shares": 1, "close": 1.0}}, today,
                       [_jb("NEW", _TODAY, 3, 9.5)], {"NEW": 9.0})
    assert "NEW" not in nb and filled == []


def test_fill_no_journal_buy_not_filled_and_still_unbaselined():
    held = [{"ticker": "GHOST", "shares": 4, "price": 10.0}]
    base = {"X": {"shares": 1, "close": 1.0}}
    nb, filled = _fill(held, base, [], [], {"GHOST": 9.0})
    assert filled == [] and "GHOST" not in nb
    res = dp.compute_positions_day_pnl(held, nb, [], 100.0)
    assert [u["ticker"] for u in res["unbaselined"]] == ["GHOST"]


@pytest.mark.parametrize("close", [None, 0, 0.0, float("nan")])
def test_fill_bad_close_not_filled(close):
    held = [{"ticker": "MRVL", "shares": 8, "price": 110.0}]
    closes = {} if close is None else {"MRVL": close}
    nb, filled = _fill(held, {"X": {"shares": 1, "close": 1.0}}, [],
                       [_jb("MRVL", _BD, 8, 100.0)], closes)
    assert filled == [] and "MRVL" not in nb


def test_fill_sell_between_not_filled():
    held = [{"ticker": "MRVL", "shares": 5, "price": 110.0}]
    journal = [_jb("MRVL", _BD, 8, 100.0), _jb("MRVL", _MID, 3, 105.0, "SELL")]
    nb, filled = _fill(held, {"X": {"shares": 1, "close": 1.0}}, [], journal, {"MRVL": 100.0})
    assert filled == [] and "MRVL" not in nb


def test_fill_ticker_case_insensitive():
    held = [{"ticker": "AAPL", "shares": 2, "price": 10.0}]
    base = {"aapl": {"shares": 2, "close": 9.0}}
    nb, filled = _fill(held, base, [], [_jb("AAPL", _BD, 2, 9.0)], {"AAPL": 9.0})
    assert filled == [] and nb == base


def test_fill_does_not_mutate_inputs():
    held = [{"ticker": "MRVL", "shares": 8, "price": 110.0}]
    base = {"X": {"shares": 1, "close": 1.0}}
    journal = [_jb("MRVL", _BD, 8, 100.0)]
    closes = {"MRVL": 105.0}
    snap = copy.deepcopy((held, base, journal, closes))
    nb, _ = _fill(held, base, [], journal, closes)
    assert (held, base, journal, closes) == snap
    assert "MRVL" not in base and "MRVL" in nb


def test_fill_today_split_skipped():
    held = [{"ticker": "NVDA", "shares": 40, "price": 10.0}]
    today = [{"ticker": "NVDA", "action": "SPLIT 4:1", "shares": 40, "price": 0}]
    nb, filled = _fill(held, {"X": {"shares": 1, "close": 1.0}}, today,
                       [_jb("NVDA", _BD, 10, 40.0)], {"NVDA": 40.0})
    assert filled == [] and "NVDA" not in nb


# ─── journal replay check (review FIX-FIRST) ─────────────────────────────────

_OLD = _dtm.date(2026, 9, 1)
_OTHER = {"X": {"shares": 1, "close": 1.0}}


def test_fill_fully_exited_old_position_not_filled_and_still_unbaselined():
    held = [{"ticker": "OLD", "shares": 5, "price": 10.0}]
    journal = [_jb("OLD", _OLD, 5, 9.0), _jb("OLD", _dtm.date(2026, 9, 10), 5, 9.5, "SELL")]
    nb, filled = _fill(held, _OTHER, [], journal, {"OLD": 10.0})
    assert filled == [] and "OLD" not in nb
    res = dp.compute_positions_day_pnl(held, nb, [], 100.0)
    assert [u["ticker"] for u in res["unbaselined"]] == ["OLD"]


def test_fill_journal_net_short_of_pre_not_filled():
    held = [{"ticker": "MRVL", "shares": 8, "price": 110.0}]
    nb, filled = _fill(held, _OTHER, [], [_jb("MRVL", _BD, 5, 100.0)], {"MRVL": 105.0})
    assert filled == [] and "MRVL" not in nb


def test_fill_fractional_journal_with_int_truncated_held():
    held = [{"ticker": "FRC", "shares": 2, "price": 10.0}]
    nb, filled = _fill(held, _OTHER, [], [_jb("FRC", _BD, 2.5, 9.0)], {"FRC": 9.5})
    assert filled and filled[0]["ticker"] == "FRC"


def test_fill_split_before_baseline_sets_total():
    held = [{"ticker": "NVDA", "shares": 40, "price": 10.0}]
    journal = [_jb("NVDA", _OLD, 10, 40.0),
               _jb("NVDA", _dtm.date(2026, 10, 1), 40, 0, "SPLIT 4:1")]
    nb, filled = _fill(held, _OTHER, [], journal, {"NVDA": 10.0})
    assert filled and filled[0]["shares"] == 40


def test_fill_split_same_day_as_buy_skipped():
    held = [{"ticker": "NVDA", "shares": 50, "price": 10.0}]
    d = _dtm.date(2026, 10, 1)
    journal = [_jb("NVDA", d, 10, 40.0), _jb("NVDA", d, 40, 0, "SPLIT 4:1"),
               _jb("NVDA", d, 10, 10.0)]
    nb, filled = _fill(held, _OTHER, [], journal, {"NVDA": 10.0})
    assert filled == [] and "NVDA" not in nb


def test_fill_oversold_history_skipped():
    held = [{"ticker": "ODD", "shares": 3, "price": 10.0}]
    journal = [_jb("ODD", _OLD, 2, 9.0), _jb("ODD", _dtm.date(2026, 9, 5), 5, 9.0, "SELL"),
               _jb("ODD", _dtm.date(2026, 9, 6), 6, 9.0)]
    nb, filled = _fill(held, _OTHER, [], journal, {"ODD": 10.0})
    assert filled == []


# ─── close_on_date ───────────────────────────────────────────────────────────

import pandas as _pd

_D = _dtm.date(2026, 10, 30)


def _frame(idx, closes):
    return _pd.DataFrame({"Close": closes}, index=idx)


def test_close_on_date_tz_naive():
    df = _frame(_pd.to_datetime(["2026-10-29", "2026-10-30"]), [1.0, 2.0])
    assert dp.close_on_date(df, _D) == 2.0


def test_close_on_date_et_aware():
    idx = _pd.DatetimeIndex(["2026-10-29 00:00", "2026-10-30 00:00"]).tz_localize("America/New_York")
    assert dp.close_on_date(_frame(idx, [1.0, 2.0]), _D) == 2.0


def test_close_on_date_utc_midnight_maps_to_previous_et_day():
    # A bar stamped 00:00 UTC on 10-30 is 20:00 ET on 10-29, so the function
    # (which converts to ET) files it under 10-29, not 10-30.
    idx = _pd.DatetimeIndex(["2026-10-30 00:00"]).tz_localize("UTC")
    df = _frame(idx, [5.0])
    assert dp.close_on_date(df, _D) is None
    assert dp.close_on_date(df, _dtm.date(2026, 10, 29)) == 5.0


def test_close_on_date_missing_and_bad_closes():
    df = _frame(_pd.to_datetime(["2026-10-29"]), [1.0])
    assert dp.close_on_date(df, _D) is None
    for bad in (float("nan"), 0.0, -1.0):
        assert dp.close_on_date(_frame(_pd.to_datetime(["2026-10-30"]), [bad]), _D) is None
    assert dp.close_on_date(None, _D) is None


def test_close_on_date_multiple_bars_same_date_takes_last():
    idx = _pd.to_datetime(["2026-10-30 09:30", "2026-10-30 15:59"])
    assert dp.close_on_date(_frame(idx, [1.0, 2.0]), _D) == 2.0


def test_replay_same_day_round_trip_newest_first_is_filled():
    # db.load_trades order is newest-first: SELL listed before its BUY.
    d = _dtm.date(2026, 9, 10)
    held = [{"ticker": "RT", "shares": 4, "price": 10.0}]
    journal = [_jb("RT", _BD, 4, 9.0), _jb("RT", d, 10, 9.5, "SELL"), _jb("RT", d, 10, 9.0)]
    nb, filled = _fill(held, _OTHER, [], journal, {"RT": 9.8})
    assert filled and filled[0]["ticker"] == "RT" and filled[0]["shares"] == 4


def test_replay_same_day_net_reduction_gives_correct_total():
    d = _dtm.date(2026, 9, 10)
    rows = [(_BD, "BUY", 3.0, 1.0), (d, "SELL", 6.0, 1.0), (d, "BUY", 2.0, 1.0),
            (_OLD, "BUY", 10.0, 1.0)]   # newest-first-ish, unsorted
    assert dp._journal_shares_at(rows, _BD) == 9.0


def test_replay_oversold_still_none():
    rows = [(_OLD, "BUY", 2.0, 1.0), (_dtm.date(2026, 9, 5), "SELL", 5.0, 1.0)]
    assert dp._journal_shares_at(rows, _BD) is None
