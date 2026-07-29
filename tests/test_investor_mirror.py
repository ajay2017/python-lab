"""Tests for stock_analyzer/investor_mirror.py — conviction alignment and
behavioral bias analytics (F-194): the FIFO closed-lot builder plus 6
downstream bias/alignment functions. Pure computation, no Streamlit/DB/
yfinance. Constants used (from stock_analyzer/constants.py):
COMPOSITE_STRONG_BUY=75, CONVICTION_WEAK_SCORE=50, CONVICTION_FADED_SCORE=60,
CONVICTION_LEGACY_TOP_N=3, BREAKEVEN_ANCHOR_DWELL_RATIO=1.3,
PREMATURE_EXIT_RATIO=0.5. Previously zero test coverage.
"""
import pandas as pd
import pytest

from stock_analyzer import investor_mirror as im


# ─── build_closed_lots — builders ────────────────────────────────────────────

def _trade(id_, ticker, action, shares, price, traded_at):
    return {"id": id_, "ticker": ticker, "action": action, "shares": shares,
            "price": price, "traded_at": traded_at}


def _trades_df(rows):
    return pd.DataFrame(rows)


# ─── build_closed_lots ────────────────────────────────────────────────────────

def test_build_closed_lots_none_returns_empty_df():
    assert im.build_closed_lots(None).empty


def test_build_closed_lots_empty_df_returns_empty_df():
    assert im.build_closed_lots(pd.DataFrame()).empty


def test_build_closed_lots_simple_full_exit_one_lot_row():
    trades = _trades_df([
        _trade(1, "AAA", "BUY", 10.0, 100.0, "2024-01-01"),
        _trade(2, "AAA", "SELL", 10.0, 120.0, "2024-01-11"),
    ])
    lots = im.build_closed_lots(trades)
    assert len(lots) == 1
    row = lots.iloc[0]
    assert row["shares"] == 10.0
    assert row["pnl_pct"] == pytest.approx(20.0)
    assert row["pnl_abs"] == pytest.approx(200.0)
    assert row["days_held"] == 10


def test_build_closed_lots_buy_split_across_two_partial_sells():
    trades = _trades_df([
        _trade(1, "AAA", "BUY", 10.0, 100.0, "2024-01-01"),
        _trade(2, "AAA", "SELL", 4.0, 110.0, "2024-01-05"),
        _trade(3, "AAA", "SELL", 6.0, 120.0, "2024-01-10"),
    ])
    lots = im.build_closed_lots(trades)
    assert len(lots) == 2
    assert lots.iloc[0]["shares"] == 4.0
    assert lots.iloc[0]["sell_price"] == 110.0
    assert lots.iloc[1]["shares"] == 6.0
    assert lots.iloc[1]["sell_price"] == 120.0
    # Both fragments came from the SAME original buy lot -> same buy_price.
    assert lots.iloc[0]["buy_price"] == 100.0
    assert lots.iloc[1]["buy_price"] == 100.0


def test_build_closed_lots_two_buy_lots_spanned_by_one_sell_fifo_order():
    trades = _trades_df([
        _trade(1, "AAA", "BUY", 5.0, 100.0, "2024-01-01"),
        _trade(2, "AAA", "BUY", 5.0, 110.0, "2024-01-03"),
        _trade(3, "AAA", "SELL", 8.0, 130.0, "2024-01-10"),
    ])
    lots = im.build_closed_lots(trades)
    assert len(lots) == 2
    # FIFO -- oldest (cheapest, $100) lot consumed first, for its OWN price.
    row0, row1 = lots.iloc[0], lots.iloc[1]
    assert row0["buy_price"] == 100.0
    assert row0["shares"] == 5.0
    assert row0["pnl_pct"] == pytest.approx(30.0)
    assert row1["buy_price"] == 110.0
    assert row1["shares"] == 3.0
    assert row1["pnl_pct"] == pytest.approx((130 - 110) / 110 * 100)


def test_build_closed_lots_split_scales_shares_and_buy_price():
    trades = _trades_df([
        _trade(1, "AAA", "BUY", 10.0, 100.0, "2024-01-01"),
        _trade(2, "AAA", "SPLIT", 20.0, 0.0, "2024-01-05"),  # 2:1 forward split
        _trade(3, "AAA", "SELL", 20.0, 60.0, "2024-01-10"),
    ])
    lots = im.build_closed_lots(trades)
    assert len(lots) == 1
    row = lots.iloc[0]
    assert row["shares"] == pytest.approx(20.0)
    assert row["buy_price"] == pytest.approx(50.0)  # 100 / 2
    assert row["pnl_pct"] == pytest.approx(20.0)     # (60-50)/50*100


def test_build_closed_lots_sell_more_than_available_drains_without_crash():
    trades = _trades_df([
        _trade(1, "AAA", "BUY", 5.0, 100.0, "2024-01-01"),
        _trade(2, "AAA", "SELL", 10.0, 120.0, "2024-01-05"),  # only 5 available
    ])
    lots = im.build_closed_lots(trades)
    assert len(lots) == 1
    assert lots.iloc[0]["shares"] == 5.0


def test_build_closed_lots_grouping_is_per_ticker_no_cross_contamination():
    trades = _trades_df([
        _trade(1, "AAA", "BUY", 5.0, 100.0, "2024-01-01"),
        _trade(2, "BBB", "BUY", 5.0, 50.0, "2024-01-01"),
        _trade(3, "AAA", "SELL", 5.0, 110.0, "2024-01-05"),
        _trade(4, "BBB", "SELL", 5.0, 60.0, "2024-01-05"),
    ])
    lots = im.build_closed_lots(trades)
    assert len(lots) == 2
    aaa_row = lots[lots["ticker"] == "AAA"].iloc[0]
    bbb_row = lots[lots["ticker"] == "BBB"].iloc[0]
    assert aaa_row["buy_price"] == 100.0
    assert bbb_row["buy_price"] == 50.0


# ─── disposition_effect ───────────────────────────────────────────────────────

def _lots(rows):
    return pd.DataFrame(rows)


def test_disposition_effect_share_weighted_avg_and_ratio():
    rows = [
        {"pnl_abs": 10.0, "days_held": 5, "shares": 10.0},
        {"pnl_abs": 20.0, "days_held": 15, "shares": 10.0},
        {"pnl_abs": -5.0, "days_held": 30, "shares": 10.0},
        {"pnl_abs": -10.0, "days_held": 40, "shares": 10.0},
    ]
    result = im.disposition_effect(_lots(rows), min_n=2)
    assert result["winner_avg_days"] == pytest.approx(10.0)
    assert result["loser_avg_days"] == pytest.approx(35.0)
    assert result["ratio"] == pytest.approx(3.5)


def test_disposition_effect_winner_pnl_abs_boundary_zero_is_winner():
    # pnl_abs>=0 is winner per the source -- NOT pnl_pct.
    rows = [
        {"pnl_abs": 0.0, "days_held": 5, "shares": 10.0},
        {"pnl_abs": 0.0, "days_held": 5, "shares": 10.0},
        {"pnl_abs": -5.0, "days_held": 20, "shares": 10.0},
        {"pnl_abs": -5.0, "days_held": 20, "shares": 10.0},
    ]
    result = im.disposition_effect(_lots(rows), min_n=2)
    assert result["n_winners"] == 2


def test_disposition_effect_below_min_n_in_either_bucket_returns_none():
    rows = [
        {"pnl_abs": 10.0, "days_held": 5, "shares": 10.0},
        {"pnl_abs": -5.0, "days_held": 20, "shares": 10.0},
    ]
    assert im.disposition_effect(_lots(rows), min_n=2) is None


def test_disposition_effect_empty_returns_none():
    assert im.disposition_effect(pd.DataFrame(), min_n=1) is None


# ─── win_loss_closure_ratio ───────────────────────────────────────────────────

def test_win_loss_closure_ratio_nets_fragments_before_classifying():
    rows = [
        {"ticker": "AAA", "sell_date": "2024-01-05", "pnl_abs": 50.0},
        {"ticker": "AAA", "sell_date": "2024-01-05", "pnl_abs": -20.0},  # net +30 -> gain tx
        {"ticker": "BBB", "sell_date": "2024-01-06", "pnl_abs": -10.0},  # loss tx
    ]
    result = im.win_loss_closure_ratio(_lots(rows), min_n=1)
    assert result["n_gain_tx"] == 1
    assert result["n_loss_tx"] == 1
    assert result["ratio"] == pytest.approx(1.0)


def test_win_loss_closure_ratio_gates_on_n_loss_tx_only():
    rows = [
        {"ticker": "AAA", "sell_date": "2024-01-05", "pnl_abs": 50.0},
        {"ticker": "BBB", "sell_date": "2024-01-06", "pnl_abs": 60.0},
        {"ticker": "CCC", "sell_date": "2024-01-07", "pnl_abs": -10.0},
    ]
    # n_gain_tx=2 (well above min_n), n_loss_tx=1 -- gate is on n_loss_tx only.
    assert im.win_loss_closure_ratio(_lots(rows), min_n=2) is None
    result = im.win_loss_closure_ratio(_lots(rows), min_n=1)
    assert result["n_gain_tx"] == 2


def test_win_loss_closure_ratio_empty_returns_none():
    assert im.win_loss_closure_ratio(pd.DataFrame(), min_n=1) is None


# ─── breakeven_anchoring ──────────────────────────────────────────────────────

def test_breakeven_anchoring_below_min_n_returns_none():
    rows = [{"pnl_pct": -3.0, "days_held": 10, "shares": 10.0}]
    assert im.breakeven_anchoring(_lots(rows), min_n=3) is None


def test_breakeven_anchoring_boundary_exactly_minus10_lands_in_minus10_to_minus5_bracket():
    rows = [{"pnl_pct": -10.0, "days_held": 5, "shares": 10.0} for _ in range(3)]
    result = im.breakeven_anchoring(_lots(rows), min_n=3)
    b_10_5 = next(b for b in result["brackets"] if b["bracket_label"] == "-10 to -5%")
    b_20_10 = next(b for b in result["brackets"] if b["bracket_label"] == "-20 to -10%")
    assert b_10_5["n_lots"] == 3
    assert b_20_10["n_lots"] == 0


def test_breakeven_anchoring_flagged_at_dwell_ratio_boundary():
    rows = [
        {"pnl_pct": -7.0, "days_held": 10.0, "shares": 10.0},   # -10 to -5% bracket
        {"pnl_pct": -3.0, "days_held": 10.0, "shares": 10.0},   # -5 to -2% bracket
        {"pnl_pct": -1.0, "days_held": 13.0, "shares": 10.0},   # -2 to 0% (breakeven)
    ]
    # adj_mean = 10.0; breakeven avg_days = 13.0 == 1.3 * 10.0 exactly -> flagged.
    result = im.breakeven_anchoring(_lots(rows), min_n=3)
    assert result["anchoring_flagged"] is True


def test_breakeven_anchoring_not_flagged_just_below_dwell_ratio_boundary():
    rows = [
        {"pnl_pct": -7.0, "days_held": 10.0, "shares": 10.0},
        {"pnl_pct": -3.0, "days_held": 10.0, "shares": 10.0},
        {"pnl_pct": -1.0, "days_held": 12.9, "shares": 10.0},  # just below 13.0
    ]
    result = im.breakeven_anchoring(_lots(rows), min_n=3)
    assert result["anchoring_flagged"] is False


# ─── conviction_alignment ─────────────────────────────────────────────────────

def _port_df(rows):
    return pd.DataFrame(rows)


def test_conviction_alignment_missing_columns_returns_none():
    df = _port_df([{"Ticker": "A", "Score": 80}])  # no Weight (%)
    assert im.conviction_alignment(df, min_positions=1) is None


def test_conviction_alignment_below_min_positions_returns_none():
    df = _port_df([
        {"Ticker": "A", "Score": 80, "Weight (%)": 10.0},
        {"Ticker": "B", "Score": 40, "Weight (%)": 5.0},
    ])
    assert im.conviction_alignment(df, min_positions=3) is None


def _alignment_fixture():
    return _port_df([
        {"Ticker": "A", "Score": 80, "Weight (%)": 2.0},   # orphan conviction
        {"Ticker": "B", "Score": 40, "Weight (%)": 30.0},  # accidental overexposure + legacy
        {"Ticker": "C", "Score": 55, "Weight (%)": 25.0},  # legacy overhang
        {"Ticker": "D", "Score": 65, "Weight (%)": 20.0},
        {"Ticker": "E", "Score": 70, "Weight (%)": 23.0},  # in top-3 by weight, score not faded
    ])


def test_conviction_alignment_orphan_conviction_triggers_independently():
    result = im.conviction_alignment(_alignment_fixture(), min_positions=3)
    tickers = [o["Ticker"] for o in result["orphan_convictions"]]
    assert tickers == ["A"]


def test_conviction_alignment_accidental_overexposure_triggers_independently():
    result = im.conviction_alignment(_alignment_fixture(), min_positions=3)
    tickers = [o["Ticker"] for o in result["accidental_overexposures"]]
    assert tickers == ["B"]


def test_conviction_alignment_legacy_overhang_triggers_independently():
    result = im.conviction_alignment(_alignment_fixture(), min_positions=3)
    tickers = sorted(o["Ticker"] for o in result["legacy_overhangs"])
    assert tickers == ["B", "C"]


def test_conviction_alignment_spearman_perfectly_monotonic_increasing():
    df = _port_df([
        {"Ticker": "A", "Score": 10, "Weight (%)": 1.0},
        {"Ticker": "B", "Score": 20, "Weight (%)": 2.0},
        {"Ticker": "C", "Score": 30, "Weight (%)": 3.0},
        {"Ticker": "D", "Score": 40, "Weight (%)": 4.0},
    ])
    result = im.conviction_alignment(df, min_positions=3)
    assert result["spearman_rho"] == pytest.approx(1.0, abs=0.01)


def test_conviction_alignment_spearman_perfectly_monotonic_decreasing():
    df = _port_df([
        {"Ticker": "A", "Score": 10, "Weight (%)": 4.0},
        {"Ticker": "B", "Score": 20, "Weight (%)": 3.0},
        {"Ticker": "C", "Score": 30, "Weight (%)": 2.0},
        {"Ticker": "D", "Score": 40, "Weight (%)": 1.0},
    ])
    result = im.conviction_alignment(df, min_positions=3)
    assert result["spearman_rho"] == pytest.approx(-1.0, abs=0.01)


# ─── sizing_alpha ─────────────────────────────────────────────────────────────

def test_sizing_alpha_groups_by_originating_buy_lot_not_sell_fragment():
    rows = [
        # ONE buy lot (AAA, 2024-01-01, $100) sold in 2 pieces -- must be
        # treated as a single lot for tercile purposes.
        {"ticker": "AAA", "buy_date": "2024-01-01", "buy_price": 100.0, "shares": 5.0, "pnl_pct": 10.0},
        {"ticker": "AAA", "buy_date": "2024-01-01", "buy_price": 100.0, "shares": 5.0, "pnl_pct": 20.0},
        {"ticker": "BBB", "buy_date": "2024-01-02", "buy_price": 50.0, "shares": 10.0, "pnl_pct": 5.0},
        {"ticker": "CCC", "buy_date": "2024-01-03", "buy_price": 200.0, "shares": 10.0, "pnl_pct": -5.0},
    ]
    result = im.sizing_alpha(_lots(rows), min_n=1)
    total_lots = sum(t["n_lots"] for t in result["terciles"])
    assert total_lots == 3  # AAA's 2 fragments collapse to 1 lot


def test_sizing_alpha_fewer_than_3_distinct_sizes_returns_none():
    rows = [
        {"ticker": "AAA", "buy_date": "2024-01-01", "buy_price": 100.0, "shares": 5.0, "pnl_pct": 10.0},
        {"ticker": "BBB", "buy_date": "2024-01-02", "buy_price": 50.0, "shares": 10.0, "pnl_pct": 5.0},
    ]
    assert im.sizing_alpha(_lots(rows), min_n=1) is None


def test_sizing_alpha_tercile_below_min_n_returns_none():
    rows = [
        {"ticker": "AAA", "buy_date": "2024-01-01", "buy_price": 100.0, "shares": 5.0, "pnl_pct": 10.0},
        {"ticker": "BBB", "buy_date": "2024-01-02", "buy_price": 50.0, "shares": 10.0, "pnl_pct": 5.0},
        {"ticker": "CCC", "buy_date": "2024-01-03", "buy_price": 200.0, "shares": 10.0, "pnl_pct": -5.0},
    ]
    # Only 1 lot per tercile -- min_n=2 fails every tercile.
    assert im.sizing_alpha(_lots(rows), min_n=2) is None


def test_sizing_alpha_empty_returns_none():
    assert im.sizing_alpha(pd.DataFrame(), min_n=1) is None


# ─── premature_exit_cost ──────────────────────────────────────────────────────

def test_premature_exit_cost_quick_vs_patient_split():
    rows = [
        {"days_held": 2, "shares": 10.0, "pnl_pct": 5.0, "pnl_abs": 50.0, "is_gain": True},
        {"days_held": 5, "shares": 10.0, "pnl_pct": 10.0, "pnl_abs": 100.0, "is_gain": True},
        {"days_held": 15, "shares": 10.0, "pnl_pct": 20.0, "pnl_abs": 200.0, "is_gain": True},
    ]
    # avg_winner_days = (2+5+15)*10 / 30 = 7.333; split_point = 0.5*7.333 = 3.667
    result = im.premature_exit_cost(_lots(rows), min_n=1)
    assert result["avg_winner_days"] == pytest.approx(7.3, abs=0.05)
    assert result["quick"]["n_lots"] == 1
    assert result["patient"]["n_lots"] == 2
    assert result["quick"]["avg_pnl_pct"] == pytest.approx(5.0)


def test_premature_exit_cost_below_min_n_in_either_bucket_returns_none():
    rows = [
        {"days_held": 2, "shares": 10.0, "pnl_pct": 5.0, "pnl_abs": 50.0, "is_gain": True},
        {"days_held": 15, "shares": 10.0, "pnl_pct": 20.0, "pnl_abs": 200.0, "is_gain": True},
    ]
    assert im.premature_exit_cost(_lots(rows), min_n=2) is None


def test_premature_exit_cost_no_winners_returns_none():
    rows = [
        {"days_held": 5, "shares": 10.0, "pnl_pct": -10.0, "pnl_abs": -100.0, "is_gain": False},
    ]
    assert im.premature_exit_cost(_lots(rows), min_n=1) is None


def test_premature_exit_cost_excludes_is_gain_true_with_none_pnl_abs():
    # The documented build_closed_lots quirk: is_gain = (pnl_abs or 0.0) >= 0
    # evaluates True even when pnl_abs is None. This function's own dropna
    # (subset includes "pnl_abs") must exclude such a row regardless -- confirm
    # by comparing against the same dataset WITHOUT the bad row: identical
    # avg_winner_days proves the bad row never entered the average.
    good_rows = [
        {"days_held": 2, "shares": 10.0, "pnl_pct": 5.0, "pnl_abs": 50.0, "is_gain": True},
        {"days_held": 10, "shares": 10.0, "pnl_pct": 10.0, "pnl_abs": 100.0, "is_gain": True},
    ]
    bad_row = {"days_held": 10, "shares": 10.0, "pnl_pct": 10.0, "pnl_abs": None, "is_gain": True}

    result_clean = im.premature_exit_cost(_lots(good_rows), min_n=1)
    result_with_bad = im.premature_exit_cost(_lots(good_rows + [bad_row]), min_n=1)

    assert result_with_bad["avg_winner_days"] == pytest.approx(result_clean["avg_winner_days"])
