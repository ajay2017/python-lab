"""Exact (fractional) share counts in trim / exit text (owner 2026-10-28, narrow fix).

port_df "Shares" stays int-truncated (Today's-P&L snapshot baseline); sizing and
sell-all text read "Shares Exact" via trim_sizing.exact_shares / fmt_shares."""
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import macro_playbook as mp
from stock_analyzer import trim_sizing as ts
from stock_analyzer.daily_briefing import _act_today
from stock_analyzer.portfolio import build_portfolio_df, rebalance_actions
from tests.conftest import find_item, make_port_df

pytestmark = pytest.mark.fast

_TODAY = date(2026, 7, 27)


# ── exact_shares / fmt_shares ─────────────────────────────────────────────────

def test_exact_shares_prefers_shares_exact():
    assert ts.exact_shares({"Shares": 2, "Shares Exact": 2.95}) == 2.95


def test_exact_shares_falls_back_to_shares_when_exact_missing():
    assert ts.exact_shares({"Shares": 7}) == 7.0
    assert ts.exact_shares(pd.Series({"Shares": 7})) == 7.0


def test_exact_shares_works_on_series_and_bad_values():
    assert ts.exact_shares(pd.Series({"Shares": 2, "Shares Exact": 0.4})) == 0.4
    assert ts.exact_shares({"Shares": 3, "Shares Exact": float("nan")}) == 3.0
    assert ts.exact_shares({"Shares": -5}) == 0.0
    assert ts.exact_shares({"Shares Exact": -1.0, "Shares": 4}) == 0.0
    assert ts.exact_shares({}) == 0.0
    assert ts.exact_shares({"Shares": None}) == 0.0


@pytest.mark.parametrize("x, text", [
    (10, "10"), (10.0, "10"), (1200, "1,200"), (2.95, "2.95"), (0.4, "0.4"),
    (0.12345, "0.12345"), (1200.5, "1,200.5"), (None, "0"), (float("nan"), "0"), (0, "0"), (-3, "0"),
])
def test_fmt_shares(x, text):
    assert ts.fmt_shares(x) == text


# ── build_portfolio_df keeps Shares int and adds Shares Exact ─────────────────

def test_exact_sizing_2_95_shares_at_13pct_trims_one_share():
    assert ts.shares_to_trim(2, 13, 8) == 0          # the old truncated input
    assert ts.shares_to_trim(2.95, 13, 8) == 1       # floor(2.95 * 5 / 13)


def test_rebalance_trim_sizes_from_exact_shares():
    df = pd.DataFrame([{
        "Ticker": "ABC", "Weight (%)": 20.0, "P&L (%)": 30.0, "Price": 100.0,
        "Shares": 2, "Shares Exact": 2.95, "Signal": "Hold", "Score": 60.0,
        "Stop": 90.0, "Stop Type": "ATR Stop", "Gap to Stop (%)": 10.0,
        "Market Value": 295.0, "Avg Cost": 70.0,
    }])
    acts = rebalance_actions(df)
    trim = next(a for a in acts if a["type"] == "trim")
    assert trim["shares"] == 2.95
    # (20 - SINGLE_NAME_CEILING)/20 of 2.95 sh, floored
    assert trim["trim_shares"] == ts.shares_to_trim(2.95, 20.0, trim["target_weight"])


def test_rebalance_review_half_uses_exact_shares():
    df = pd.DataFrame([{
        "Ticker": "ABC", "Weight (%)": 5.0, "P&L (%)": 20.0, "Price": 100.0,
        "Shares": 1, "Shares Exact": 3.95, "Signal": "Sell", "Score": 20.0,
        "Stop": 90.0, "Stop Type": "ATR Stop", "Gap to Stop (%)": 10.0,
        "Market Value": 395.0, "Avg Cost": 70.0,
    }])
    review = next(a for a in rebalance_actions(df) if a["type"] == "review")
    assert review["half_shares"] == 1               # floor(3.95 / 2); truncated 1 sh -> 0
    assert review["shares"] == 3.95


def test_rebalance_actions_whole_share_holding_unchanged():
    base = {
        "Ticker": "ABC", "Weight (%)": 20.0, "P&L (%)": 30.0, "Price": 100.0,
        "Shares": 50, "Signal": "Hold", "Score": 60.0, "Stop": 90.0,
        "Stop Type": "ATR Stop", "Gap to Stop (%)": 10.0,
        "Market Value": 5000.0, "Avg Cost": 70.0,
    }
    without = rebalance_actions(pd.DataFrame([base]))
    with_exact = rebalance_actions(pd.DataFrame([{**base, "Shares Exact": 50.0}]))
    assert without == with_exact


# ── stop-breach text (Brief + cron alert) ─────────────────────────────────────

def _breach_df(shares, exact=None):
    df = make_port_df([{"ticker": "ZZZ", "gap_to_stop": -2.0, "shares": shares}])
    if exact is not None:
        df["Shares Exact"] = exact
    return df


def test_brief_stop_breach_reads_fractional_count():
    items = _act_today(_breach_df(2, 2.95), [], [], [], [], _TODAY)
    item = find_item(items, "ZZZ")
    assert "Sell all 2.95 shares at next open" in item["directive"]
    assert item["shares"] == 2.95


def test_brief_stop_breach_sub_one_share_lot():
    items = _act_today(_breach_df(0, 0.4), [], [], [], [], _TODAY)
    assert "Sell all 0.4 shares at next open" in find_item(items, "ZZZ")["directive"]


def test_brief_stop_breach_whole_share_text_unchanged():
    for df in (_breach_df(10), _breach_df(10, 10.0)):
        item = find_item(_act_today(df, [], [], [], [], _TODAY), "ZZZ")
        assert "Sell all 10 shares at next open — mechanical stop rule." in item["directive"]


def test_cron_stop_breach_reads_fractional_count():
    from unittest.mock import patch
    from stock_analyzer import headless_alert_engine as hae
    row = {"Ticker": "AAPL", "Gap to Stop (%)": -2.0, "Price": 100.0, "Shares": 2,
           "Shares Exact": 2.95, "Stop": 90.0, "Weight (%)": 10.0, "P&L (%)": 5.0,
           "Stop Type": "Trailing", "Score": 60.0}
    ctx = {"ok": True, "errors": [], "port_df": pd.DataFrame([row]), "held_data": {},
           "fragility": None, "spy_6mo": None, "spy_1y": None, "vix": None}
    with patch("stock_analyzer.headless_alert_engine._build_context", return_value=ctx), \
         patch("stock_analyzer.headless_alert_engine.deterioration_signals", return_value=[]), \
         patch("stock_analyzer.exit_advisor.assess_risk_off_derisk", return_value=[]):
        result = hae.compute_protective_alerts(date(2026, 7, 27))
    assert "Sell all 2.95 shares at next open" in result["alerts"][0]["directive"]


# ── deterioration clause with fractional counts ───────────────────────────────

def _det(tier, shares):
    return {
        "tier": tier, "ticker": "ABC", "shares": shares,
        "dd_from_peak_pct": -12.0, "peak": 10.0, "trend_ma": 50,
        "below_ma_count": 3, "rel_strength": -2.0,
        "trim_floor": 10.0, "exit_floor": 20.0,
        "pnl_pct": -5.0, "weight_pct": 2.0, "price": 95.5,
    }


def test_deterioration_fractional_count_text_stays_true():
    port_df = make_port_df([{"ticker": "ZZZ"}])
    exit_dir = _act_today(port_df, [], [], [], [], _TODAY,
                          deterioration=[_det("EXIT", 2.95)])[0]["directive"]
    assert "of 2.95 shares" in exit_dir
    trim_dir = _act_today(port_df, [], [], [], [], _TODAY,
                          deterioration=[_det("TRIM", 2.5)])[0]["directive"]
    # 25% of 2.5 sh is under one share; 1 share is 40% of the position
    assert "at 2.5 shares" in trim_dir and "40% of the position" in trim_dir


def test_deterioration_under_one_share_never_says_one_share_trim():
    port_df = make_port_df([{"ticker": "ZZZ"}])
    trim_dir = _act_today(port_df, [], [], [], [], _TODAY,
                          deterioration=[_det("TRIM", 0.4)])[0]["directive"]
    assert "at 0.4 shares the whole position is under one share, so no whole-share trim is sized" in trim_dir
    assert "smallest real trim is 1 share" not in trim_dir


# ── macro playbook ────────────────────────────────────────────────────────────

def _mp_row(**o):
    row = {"Ticker": "XYZ", "Sector": "Financials", "Weight (%)": 5.0, "Score": 60.0,
           "Signal": "Hold", "P&L (%)": 0.0, "Shares": 100, "Price": 50.0,
           "Market Value": 5000.0}
    row.update(o)
    return row


def test_macro_protect_fractional_position_text():
    detail = mp._action_detail(_mp_row(Shares=0, **{"Shares Exact": 0.4}, Price=50.0),
                               "Non-Farm Payrolls", "PROTECT")
    assert "0.4-share position" in detail and "0-share" not in detail


def test_macro_protect_overweight_trim_sizes_from_exact():
    detail = mp._action_detail(
        _mp_row(Shares=2, **{"Shares Exact": 2.95, "Weight (%)": 13.0, "Market Value": 295.0}),
        "Non-Farm Payrolls", "PROTECT")
    assert "sell 1 shares" in detail.lower()


# ── port_df column (Shares int baseline preserved, Shares Exact added) ────────

def test_build_portfolio_df_keeps_int_shares_and_adds_exact():
    import inspect
    src = inspect.getsource(build_portfolio_df)
    assert '"Shares": int(shares)' in src
    assert '"Shares Exact": float(shares)' in src


# ── float noise (DB share sums are unrounded) ─────────────────────────────────

def test_exact_shares_cleans_float_noise():
    assert ts.exact_shares({"Shares Exact": 2.3 - 0.3}) == 2.0


def test_review_half_shares_survives_float_noise():
    df = pd.DataFrame([{
        "Ticker": "ABC", "Weight (%)": 5.0, "P&L (%)": 20.0, "Price": 100.0,
        "Shares": 1, "Shares Exact": 2.3 - 0.3, "Signal": "Sell", "Score": 20.0,
        "Stop": 90.0, "Stop Type": "ATR Stop", "Gap to Stop (%)": 10.0,
        "Market Value": 200.0, "Avg Cost": 70.0,
    }])
    review = next(a for a in rebalance_actions(df) if a["type"] == "review")
    assert review["half_shares"] == 1


def test_deterioration_sub_one_branch_not_fired_for_noisy_one_share():
    port_df = make_port_df([{"ticker": "ZZZ"}])
    shares = ts.exact_shares({"Shares Exact": 1 - 1e-12})
    assert shares == 1.0
    trim_dir = _act_today(port_df, [], [], [], [], _TODAY,
                          deterioration=[_det("TRIM", shares)])[0]["directive"]
    assert "whole position is under one share" not in trim_dir
    assert "smallest real trim is 1 share" in trim_dir
