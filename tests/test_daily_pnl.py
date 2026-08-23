"""Tests for stock_analyzer/daily_pnl.py — Tier-B positions-scope day P&L
(`compute_positions_day_pnl`), the today's-trades cash delta it consumes
(`today_trade_cash_delta`), and the NaN-safe float coercion helper (`_num`).
Pure dict/list math, no I/O. Previously zero test coverage.
"""
import math

from stock_analyzer import daily_pnl as dp


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

def test_all_three_disagreement_shapes_reported_together():
    held = [
        {"ticker": "AAA", "shares": 12.0, "price": 100.0},   # drift (+2)
        {"ticker": "CCC", "shares": 3.0,  "price": 200.0},   # unbaselined
    ]
    baseline = {
        "AAA": {"shares": 10.0, "close": 99.0},
        "BBB": {"shares": 5.0,  "close": 50.0},              # orphan
    }
    out = dp.reconcile_baseline(held, baseline, [])
    assert [o["ticker"] for o in out["orphans"]] == ["BBB"]
    assert [r["ticker"] for r in out["qty_drift"]] == ["AAA"]
    assert [r["ticker"] for r in out["unbaselined"]] == ["CCC"]


def test_reconcile_baseline_returns_empty_lists_never_none_when_clean():
    """No-disagreement and not-checked are both knowable here, so the
    offline-sentinel convention does not apply - these are always lists."""
    held = [{"ticker": "AAA", "shares": 10.0, "price": 100.0}]
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    out = dp.reconcile_baseline(held, baseline, [])
    assert out == {"orphans": [], "qty_drift": [], "unbaselined": []}


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


def test_qty_drift_guard_documented_false_negative_is_deliberate():
    """A genuine drift IS missed when the same ticker also had a fractional fill
    that day. Pinned so the trade-off is a decision, not a surprise."""
    held = [{"ticker": "AAA", "shares": 20.0, "price": 100.0}]     # truncated
    baseline = {"AAA": {"shares": 10.0, "close": 99.0}}
    trades = [{"ticker": "AAA", "action": "BUY", "shares": 0.5, "price": 100.0}]
    assert dp.reconcile_baseline(held, baseline, trades)["qty_drift"] == []


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
    assert out == {"orphans": [], "qty_drift": [], "unbaselined": []}


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
