"""Tests for stock_analyzer/daily_briefing.py::deterioration_signals() — in
particular its `split_flagged` parameter (data-integrity finding D1).

`classify_deterioration_tier`'s `escalate` leg reads `price < avg_cost`
directly. A held ticker's stored `avg_cost` is a static dollar figure entered
at BUY time; it is never auto-adjusted for a stock split, while the live
`price` this app fetches IS split-adjusted (yfinance's `auto_adjust=True`).
So an unaccounted forward split can make `price < avg_cost` trivially true —
turning an otherwise-legitimate, price-history-real TRIM into an unwarranted
EXIT with no actual deterioration behind it. `deterioration_signals` is the
one shared decision path BOTH the interactive Home render (app.py:5349) and
the headless cron lane (headless_alert_engine.py) call — so a fix here
protects both, and a regression here endangers both.

`split_flagged` closes this by withholding EVERY tier (not only EXIT) for a
ticker whose split is unconfirmed: every dollar figure a directive would
render for that ticker is built on the same uncorrected avg_cost, so a
"safe" WATCH/TRIM would still print wrong numbers.
"""
import pandas as pd
import pytest

from stock_analyzer.constants import DETERIORATION_TREND_MA
from stock_analyzer.daily_briefing import deterioration_signals
from stock_analyzer.exit_advisor import EXIT

pytestmark = pytest.mark.fast

_MA = f"SMA_{DETERIORATION_TREND_MA}"


def _declining_frame(n=80, peak=150.0, trough=137.0):
    """Flat at `peak`, then a steady decline to `trough` — a genuine,
    moderate downtrend (~8-9% off peak, tuned to clear the TRIM floor
    without reaching the EXIT floor on its own — see module docstring).
    Ends below the trend MA for the last several sessions.
    """
    head = [peak] * 20
    decline = [peak - (peak - trough) * i / (n - 21) for i in range(n - 20)]
    close = pd.Series([float(c) for c in head + decline])
    return pd.DataFrame({"Close": close, _MA: close.rolling(DETERIORATION_TREND_MA).mean()})


def _rising_spy_frame(n=80, start=100.0, end=110.0):
    """A mildly RISING benchmark, so the declining ticker's relative
    strength is negative (idiosyncratic weakness, not a market-wide day) —
    required for `trim_active`.
    """
    close = pd.Series([start + (end - start) * i / (n - 1) for i in range(n)])
    return pd.DataFrame({"Close": close})


def _port_df_row(ticker="AAA", price=None, avg_cost=100.0, shares=10.0):
    return pd.DataFrame([{
        "Ticker": ticker, "Price": price, "Avg Cost": avg_cost, "Shares": shares,
        "P&L (%)": None, "Weight (%)": 10.0,
    }])


def test_unconfirmed_split_would_manufacture_a_false_exit_without_the_guard():
    """Proves the bug is real: a genuine, moderate TRIM-worthy decline (NOT a
    deep EXIT-worthy one on its own) escalates to EXIT purely because
    avg_cost is 10x the live price — the signature of an uncorrected 10:1
    forward split, not real deterioration.
    """
    df = _declining_frame()
    spy = _rising_spy_frame()
    price = float(df["Close"].iloc[-1])
    avg_cost = price * 10.0   # uncorrected 10:1 forward split
    port_df = _port_df_row(price=price, avg_cost=avg_cost)
    held_data = {"AAA": {"df": df, "atr": 1.0, "position_age_days": 200}}

    out = deterioration_signals(port_df, held_data, spy)
    assert len(out) == 1
    assert out[0]["ticker"] == "AAA"
    assert out[0]["tier"] == EXIT   # the false EXIT this finding exists to close


def test_split_flagged_withholds_the_ticker_entirely():
    """The fix: the SAME scenario, with the ticker in `split_flagged`, is
    absent from the output entirely — not just tier-downgraded. Every dollar
    figure the payload would carry is built on the same corrupted avg_cost,
    so a partial disclosure (e.g. still showing TRIM) would still be wrong.
    """
    df = _declining_frame()
    spy = _rising_spy_frame()
    price = float(df["Close"].iloc[-1])
    avg_cost = price * 10.0
    port_df = _port_df_row(price=price, avg_cost=avg_cost)
    held_data = {"AAA": {"df": df, "atr": 1.0, "position_age_days": 200}}

    out = deterioration_signals(port_df, held_data, spy, split_flagged={"AAA"})
    assert out == []


def test_a_genuine_deep_drawdown_still_fires_exit_when_not_split_flagged():
    """No over-suppression: a ticker NOT in split_flagged, with a real deep
    drawdown (avg_cost consistent with the live price — no split signature),
    must still return EXIT. Proves the fix doesn't blunt real protective
    calls for unrelated tickers.
    """
    df = _declining_frame(peak=150.0, trough=110.0)   # ~27% off peak: real EXIT-depth
    spy = _rising_spy_frame()
    price = float(df["Close"].iloc[-1])
    port_df = _port_df_row(price=price, avg_cost=price * 1.05)   # ordinary, unsplit cost basis
    held_data = {"AAA": {"df": df, "atr": 1.0, "position_age_days": 200}}

    out = deterioration_signals(port_df, held_data, spy, split_flagged=set())
    assert len(out) == 1
    assert out[0]["tier"] == EXIT


def test_split_flagged_ticker_with_a_genuine_deep_exit_is_still_withheld():
    """Pins the deliberate, owner-confirmed tradeoff: EVERY tier is withheld
    for a flagged ticker, even a deep_exit that would have fired purely on
    price history (split-safe on its own). A future "helpful" change that
    tries to let deep_exit through for a flagged ticker must fail this test.
    """
    df = _declining_frame(peak=150.0, trough=110.0)  # real EXIT-depth on its own
    spy = _rising_spy_frame()
    price = float(df["Close"].iloc[-1])
    avg_cost = price * 10.0   # ALSO split-flagged
    port_df = _port_df_row(price=price, avg_cost=avg_cost)
    held_data = {"AAA": {"df": df, "atr": 1.0, "position_age_days": 200}}

    out = deterioration_signals(port_df, held_data, spy, split_flagged={"AAA"})
    assert out == []


def test_default_split_flagged_is_none_and_reproduces_old_behaviour():
    # Every OTHER existing caller passes no split_flagged at all -- confirm
    # the default is a true no-op, not merely "usually empty".
    df = _declining_frame()
    spy = _rising_spy_frame()
    price = float(df["Close"].iloc[-1])
    avg_cost = price * 10.0
    port_df = _port_df_row(price=price, avg_cost=avg_cost)
    held_data = {"AAA": {"df": df, "atr": 1.0, "position_age_days": 200}}

    out_default = deterioration_signals(port_df, held_data, spy)
    out_explicit_none = deterioration_signals(port_df, held_data, spy, split_flagged=None)
    assert out_default == out_explicit_none
    assert len(out_default) == 1


def test_deep_exit_dd_from_peak_is_unaffected_by_the_avg_cost_scale():
    """Locks in the fact-check the design relied on: dd_from_peak_pct comes
    purely from the (already split-adjusted) Close series, never from
    avg_cost -- so the SAME price history produces the SAME tier regardless
    of whether avg_cost happens to be on a pre- or post-split scale, as long
    as escalate's price-vs-avg_cost leg isn't what's firing. Uses a decline
    deep enough that deep_exit alone decides the tier (not escalate).
    """
    df = _declining_frame(peak=150.0, trough=110.0)
    spy = _rising_spy_frame()
    price = float(df["Close"].iloc[-1])
    held_data = {"AAA": {"df": df, "atr": 1.0, "position_age_days": 200}}

    out_normal_cost = deterioration_signals(
        _port_df_row(price=price, avg_cost=price * 1.05), held_data, spy,
    )
    out_below_cost = deterioration_signals(
        _port_df_row(price=price, avg_cost=price * 0.5), held_data, spy,
    )
    assert out_normal_cost[0]["tier"] == out_below_cost[0]["tier"] == EXIT
