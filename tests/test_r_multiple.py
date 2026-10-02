"""
Tests for stock_analyzer/r_multiple.py — R-multiple measurement (closed
episode realized P&L vs. its planned risk). Pure logic — no I/O, no
Streamlit. Covers chunks 1/2 only: the engine lens (ATR-reconstructed,
no-lookahead) and the declared-lens stub (always "no plan" until a future
capture chunk ships).
"""
from datetime import date

import pandas as pd
import pytest

from stock_analyzer.constants import ATR_STOP_MULT
from stock_analyzer.ticker_history import build_ticker_history
from stock_analyzer.r_multiple import (
    engine_reference_risk, build_risk_plan, episode_r_multiple, add_flags,
    _safe_dict,
)

pytestmark = pytest.mark.fast


# ─── builders ────────────────────────────────────────────────────────────────

def _flat_ohlc(start, n_days, close=50.0, half_range=0.5, freq="D"):
    """Constant-range OHLC fixture: True Range is exactly `2*half_range`
    every single day (Close constant => prev_close == close, so
    |High-prev_close| == |Low-prev_close| == half_range, and High-Low is the
    max). EWM-based ATR(14) of a constant series is that same constant from
    the very first bar on, so the resulting ATR is deterministic and exact —
    no approximation needed to make these tests precise.
    """
    idx = pd.date_range(start=start, periods=n_days, freq=freq)
    return pd.DataFrame({
        "High":  [close + half_range] * n_days,
        "Low":   [close - half_range] * n_days,
        "Close": [close] * n_days,
    }, index=idx)


def _trade_row(ticker="AAA", action="BUY", shares=10.0, price=100.0,
                traded_at="2026-01-05T09:30:00Z", id_=1, realized_pnl=None,
                trigger_type="MANUAL"):
    return {
        "id": id_, "ticker": ticker, "action": action, "shares": shares,
        "price": price, "traded_at": traded_at, "realized_pnl": realized_pnl,
        "trigger_type": trigger_type,
    }


def _df(rows):
    return pd.DataFrame(rows)


def _closed_episode(fills, realized_pnl):
    """Minimal episode dict matching ticker_history's `_build_episode` shape
    — only the fields `r_multiple` actually reads."""
    return {"status": "closed", "fills": fills, "realized_pnl": realized_pnl}


# ─── 1. No lookahead ──────────────────────────────────────────────────────────

def test_no_lookahead_huge_bar_on_trade_date_does_not_affect_result():
    as_of = date(2026, 2, 19)
    prior_only = _flat_ohlc(date(2026, 1, 30), 20)   # 20 days strictly before as_of

    # Full frame = the same prior bars PLUS a huge-range bar dated as_of itself.
    huge_day = pd.DataFrame({"High": [5000.0], "Low": [1.0], "Close": [50.0]},
                             index=pd.DatetimeIndex([pd.Timestamp(as_of)]))
    full = pd.concat([prior_only, huge_day])

    r_prior_only = engine_reference_risk(prior_only, as_of)
    r_full = engine_reference_risk(full, as_of)

    assert r_prior_only is not None
    assert r_full is not None
    assert r_full["risk_per_share"] == pytest.approx(r_prior_only["risk_per_share"])
    assert r_full["atr_value"] == pytest.approx(r_prior_only["atr_value"])
    assert r_full["bars_used"] == r_prior_only["bars_used"]


# ─── 2. Fewer than 15 prior bars → None, fallback never reached ─────────────

def test_fewer_than_15_prior_bars_returns_none():
    as_of = date(2026, 1, 20)
    # Only 5 prior bars, with a HUGE range — if the mean(High-Low) fallback
    # were ever reached it would produce a large non-None risk. It must not.
    thin = _flat_ohlc(date(2026, 1, 10), 5, close=50.0, half_range=100.0)
    assert engine_reference_risk(thin, as_of) is None


# ─── 3. Stop at/above entry price → build_risk_plan returns None ────────────

def test_declared_stop_at_or_above_entry_returns_none():
    assert build_risk_plan(100.0, 10.0, None, date(2026, 1, 5),
                            declared_stop_price=100.0) is None   # equal
    assert build_risk_plan(100.0, 10.0, None, date(2026, 1, 5),
                            declared_stop_price=110.0) is None   # above


def test_engine_stop_at_or_above_entry_returns_none():
    # Entry price set BELOW the reconstructed stop (ref_price - risk_per_share)
    # so the resolved engine stop sits at/above the entry.
    as_of = date(2026, 2, 1)
    ohlc = _flat_ohlc(date(2026, 1, 1), 20, close=50.0, half_range=0.5)  # range=1.0
    # engine risk_per_share = ATR_STOP_MULT * 1.0; use a tiny entry price so
    # entry - risk_per_share <= 0, which build_risk_plan must also reject.
    tiny_entry = ATR_STOP_MULT * 1.0 * 0.5   # strictly less than the risk itself
    assert build_risk_plan(tiny_entry, 10.0, ohlc, as_of) is None


# ─── 4. Episode missing a plan on any leg → None, never partial ────────────

def test_episode_with_one_unplannable_leg_returns_none_not_partial():
    ohlc = _flat_ohlc(date(2025, 12, 1), 90, close=50.0, half_range=0.5)
    fills = [
        # leg 1: plenty of prior bars (resolvable)
        {"date": date(2026, 2, 1), "action": "BUY", "shares": 10.0, "price": 50.0},
        # leg 2: only ~4 days of history exist before this date (<15, unresolvable)
        {"date": date(2025, 12, 5), "action": "BUY", "shares": 10.0, "price": 50.0},
    ]
    ep = _closed_episode(fills, realized_pnl=100.0)
    res = episode_r_multiple(ep, "engine", ohlc)
    assert res["r_multiple"] is None
    assert res["legs_total"] == 2
    assert res["legs_planned"] == 1
    assert "reason" in res


# ─── 5. Open episode → None ─────────────────────────────────────────────────

def test_open_episode_returns_none():
    ohlc = _flat_ohlc(date(2026, 1, 1), 30, close=50.0, half_range=0.5)
    fills = [{"date": date(2026, 2, 1), "action": "BUY", "shares": 10.0, "price": 50.0}]
    ep = {"status": "open", "fills": fills, "realized_pnl": 0.0}
    res = episode_r_multiple(ep, "engine", ohlc)
    assert res["r_multiple"] is None
    assert res["reason"] == "episode still open"


# ─── 6. Winning trade positive R; losing trade exactly -1.0R ───────────────

def test_winning_trade_gives_positive_r():
    as_of = date(2026, 2, 1)
    ohlc = _flat_ohlc(date(2026, 1, 1), 25, close=50.0, half_range=0.5)  # range=1.0
    # risk_per_share = ATR_STOP_MULT * 1.0; risk_dollars = that * 10 shares
    fills = [{"date": as_of, "action": "BUY", "shares": 10.0, "price": 50.0}]
    risk_dollars = ATR_STOP_MULT * 1.0 * 10.0
    ep = _closed_episode(fills, realized_pnl=risk_dollars * 1.5)   # a clear win
    res = episode_r_multiple(ep, "engine", ohlc)
    assert res["r_multiple"] is not None
    assert res["r_multiple"] > 0
    assert res["r_multiple"] == pytest.approx(1.5)


def test_losing_trade_exactly_at_planned_risk_gives_minus_one_r():
    as_of = date(2026, 2, 1)
    ohlc = _flat_ohlc(date(2026, 1, 1), 25, close=50.0, half_range=0.5)  # range=1.0
    fills = [{"date": as_of, "action": "BUY", "shares": 10.0, "price": 50.0}]
    risk_dollars = ATR_STOP_MULT * 1.0 * 10.0
    ep = _closed_episode(fills, realized_pnl=-risk_dollars)
    res = episode_r_multiple(ep, "engine", ohlc)
    assert res["r_multiple"] == pytest.approx(-1.0)


# ─── 7. Split mid-episode gives the same R as the equivalent unsplit case ──

def test_split_mid_episode_gives_same_r_as_unsplit_equivalent():
    # Real split-handling fixture (mirrors
    # test_split_rescales_episode_so_realized_pct_is_correct in
    # test_ticker_history.py): BUY 10 @ $100 -> SPLIT(20, $50) -> SELL 20 @ $60.
    # After rescale the BUY leg becomes 20 sh @ $50, same date.
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SPLIT", shares=20.0, price=50.0,
                   traded_at="2026-02-05T09:30:00Z"),
        _trade_row(id_=3, action="SELL", shares=20.0, price=60.0,
                   traded_at="2026-03-05T09:30:00Z", realized_pnl=200.0),
    ]
    result = build_ticker_history(_df(rows), "AAA", today=date(2026, 4, 1))
    ep_split = result["episodes"][0]
    assert ep_split["fills"][0]["price"] == pytest.approx(50.0)   # rescaled

    # Post-split-scale OHLC spanning well before the (unchanged) BUY date,
    # consistent with yfinance's auto_adjust=True applying the split-adjusted
    # scale across all history, including pre-split dates.
    ohlc = _flat_ohlc(date(2025, 11, 1), 150, close=50.0, half_range=0.5)

    ep_unsplit = _closed_episode(
        fills=[
            {"date": date(2026, 1, 5), "action": "BUY", "shares": 20.0, "price": 50.0},
            {"date": date(2026, 3, 5), "action": "SELL", "shares": 20.0, "price": 60.0},
        ],
        realized_pnl=200.0,
    )

    res_split = episode_r_multiple(ep_split, "engine", ohlc)
    res_unsplit = episode_r_multiple(ep_unsplit, "engine", ohlc)

    assert res_split["r_multiple"] is not None
    assert res_unsplit["r_multiple"] is not None
    assert res_split["r_multiple"] == pytest.approx(res_unsplit["r_multiple"])


# ─── 8. add_flags ────────────────────────────────────────────────────────────

def test_add_flags_flags_an_add_priced_below_running_average():
    fills = [
        {"date": date(2026, 1, 1), "action": "BUY", "shares": 10.0, "price": 100.0},
        {"date": date(2026, 1, 10), "action": "BUY", "shares": 10.0, "price": 90.0},   # below avg 100
    ]
    ep = {"fills": fills}
    assert add_flags(ep)["added_while_losing"] is True


def test_add_flags_does_not_flag_an_add_priced_above_running_average():
    fills = [
        {"date": date(2026, 1, 1), "action": "BUY", "shares": 10.0, "price": 100.0},
        {"date": date(2026, 1, 10), "action": "BUY", "shares": 10.0, "price": 110.0},  # above avg 100
    ]
    ep = {"fills": fills}
    assert add_flags(ep)["added_while_losing"] is False


# ─── 9. JSON-safety: NaN coerced to None ────────────────────────────────────

def test_safe_dict_coerces_nan_to_none():
    out = _safe_dict({"a": float("nan"), "b": 1.0, "c": "text", "d": None})
    assert out["a"] is None
    assert out["b"] == 1.0
    assert out["c"] == "text"
    assert out["d"] is None


def test_degenerate_ohlc_with_nan_close_returns_none_not_nan():
    as_of = date(2026, 2, 1)
    idx = pd.date_range(start=date(2026, 1, 1), periods=20, freq="D")
    ohlc = pd.DataFrame({
        "High":  [50.5] * 20,
        "Low":   [49.5] * 20,
        "Close": [50.0] * 19 + [float("nan")],   # degenerate last bar
    }, index=idx)
    result = engine_reference_risk(ohlc, as_of)
    assert result is None   # never a dict with a NaN ref_price inside it


# ─── bonus: declared lens is a stub until the capture chunk ships ─────────

def test_declared_lens_reports_no_plan_when_nothing_attached():
    ohlc = _flat_ohlc(date(2026, 1, 1), 30, close=50.0, half_range=0.5)
    fills = [{"date": date(2026, 2, 1), "action": "BUY", "shares": 10.0, "price": 50.0}]
    ep = _closed_episode(fills, realized_pnl=100.0)
    res = episode_r_multiple(ep, "declared", ohlc)
    assert res["r_multiple"] is None
    assert res["legs_planned"] == 0
    assert res["legs_total"] == 1
