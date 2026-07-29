"""Tests for stock_analyzer/indicators.py — pure pandas/numpy technical
indicator formulas (sma, ema, rsi, macd, bbands, obv, atr). Previously zero
test coverage despite these feeding technical_score and the composite scoring
pipeline downstream. All pure math, no I/O — testable against small,
hand-computable synthetic series.
"""
import pandas as pd
import pytest

from stock_analyzer import indicators as ind


# ─── builders ───────────────────────────────────────────────────────────────

def _series(vals):
    idx = pd.date_range("2020-01-01", periods=len(vals), freq="D")
    return pd.Series(vals, index=idx, dtype=float)


# ─── sma ─────────────────────────────────────────────────────────────────────

def test_sma_hand_computable_last_value():
    s = _series([1, 2, 3, 4, 5])
    result = ind.sma(s, 3)
    assert result.iloc[-1] == 4.0  # mean(3,4,5)


def test_sma_leading_window_is_nan():
    s = _series([1, 2, 3, 4, 5])
    result = ind.sma(s, 3)
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert not pd.isna(result.iloc[2])


# ─── ema ─────────────────────────────────────────────────────────────────────

def test_ema_first_value_equals_first_close():
    # adjust=False EWM: the seed value equals the first observation.
    s = _series([10, 20, 30])
    result = ind.ema(s, 5)
    assert result.iloc[0] == 10.0


def test_ema_differs_from_sma_when_values_vary():
    s = _series([1, 2, 3, 4, 5, 6, 7, 8])
    sma_val = ind.sma(s, 5).iloc[-1]
    ema_val = ind.ema(s, 5).iloc[-1]
    assert sma_val != pytest.approx(ema_val)


# ─── rsi ─────────────────────────────────────────────────────────────────────

def test_rsi_pure_uptrend_avg_loss_zero_approaches_100():
    # Every day gains -> avg_loss stays exactly 0 the whole window -> the
    # raw rs=avg_gain/0 formula would be inf/NaN; the explicit fallback
    # (avg_loss > 0 ? raw : (avg_gain > 0 ? 100 : 50)) must yield 100.
    s = _series([100 + i for i in range(20)])
    result = ind.rsi(s, length=14)
    assert result.iloc[-1] == 100.0
    assert not result.isna().any()


def test_rsi_flat_price_avg_gain_and_loss_zero_reads_50():
    # A perfectly flat series: avg_gain == avg_loss == 0 -> explicit 50.0.
    s = _series([100.0] * 20)
    result = ind.rsi(s, length=14)
    assert result.iloc[-1] == 50.0


def test_rsi_pure_downtrend_approaches_0():
    s = _series([100 - i for i in range(20)])
    result = ind.rsi(s, length=14)
    assert result.iloc[-1] == pytest.approx(0.0, abs=1e-9)


# ─── macd ────────────────────────────────────────────────────────────────────

def test_macd_histogram_equals_line_minus_signal():
    s = _series([100 + i * 0.5 + (i % 3) for i in range(60)])
    macd_line, signal_line, hist = ind.macd(s)
    diff = (macd_line - signal_line) - hist
    assert (diff.abs() < 1e-9).all()


def test_macd_line_equals_ema_fast_minus_ema_slow():
    s = _series([100 + i for i in range(40)])
    macd_line, _, _ = ind.macd(s, fast=12, slow=26)
    expected = ind.ema(s, 12) - ind.ema(s, 26)
    assert (macd_line - expected).abs().max() < 1e-9


# ─── bbands ──────────────────────────────────────────────────────────────────

def test_bbands_upper_and_lower_are_symmetric_around_mid():
    s = _series([100 + (i % 5) * 2 for i in range(30)])
    upper, mid, lower = ind.bbands(s, length=20, std=2.0)
    diff_up = (upper - mid).dropna()
    diff_down = (mid - lower).dropna()
    assert (diff_up - diff_down).abs().max() < 1e-9


def test_bbands_zero_std_collapses_to_mid():
    s = _series([100 + (i % 5) for i in range(25)])
    upper, mid, lower = ind.bbands(s, length=20, std=0.0)
    assert (upper.dropna() == mid.dropna()).all()
    assert (lower.dropna() == mid.dropna()).all()


# ─── obv ─────────────────────────────────────────────────────────────────────

def test_obv_up_day_adds_volume():
    close = _series([100, 101])
    vol = _series([1000, 2000])
    result = ind.obv(close, vol)
    assert result.iloc[-1] == 2000  # 0 (first day, diff NaN->fillna 0) + +2000


def test_obv_down_day_subtracts_volume():
    close = _series([100, 99])
    vol = _series([1000, 2000])
    result = ind.obv(close, vol)
    assert result.iloc[-1] == -2000


def test_obv_flat_day_contributes_zero():
    close = _series([100, 100])
    vol = _series([1000, 2000])
    result = ind.obv(close, vol)
    assert result.iloc[-1] == 0  # np.sign(0) == 0 -> contributes nothing


def test_obv_cumulative_across_mixed_days():
    close = _series([100, 101, 100, 100, 102])
    vol = _series([1000, 1000, 1000, 1000, 1000])
    result = ind.obv(close, vol)
    # day0: 0 (diff NaN->0); day1: up +1000; day2: down -1000; day3: flat 0; day4: up +1000
    assert result.tolist() == [0, 1000, 0, 0, 1000]


# ─── atr ─────────────────────────────────────────────────────────────────────

def test_atr_true_range_uses_prev_close_when_wider_than_high_low():
    # Day 2's High/Low range is narrow (101-99=2), but prev close (110) is far
    # outside that range, so true range should be |low - prev_close| = |99-110|=11,
    # not the narrow high-low=2.
    high = _series([100, 101])
    low = _series([90, 99])
    close = _series([110, 100])
    result = ind.atr(high, low, close, length=1)
    # span=1 EWM with adjust=False: first value seeds at tr[0], second value
    # heavily weights tr[1]. tr[0] = high[0]-low[0] = 10 (no prev close on day0).
    # tr[1] = max(101-99, |101-110|, |99-110|) = max(2, 9, 11) = 11.
    tr0 = 100 - 90
    tr1 = max(101 - 99, abs(101 - 110), abs(99 - 110))
    assert tr1 == 11
    expected = pd.Series([tr0, tr1]).ewm(span=1, adjust=False).mean()
    assert result.iloc[-1] == pytest.approx(expected.iloc[-1])


def test_atr_differs_from_plain_average_when_values_vary():
    high = _series([100 + (i % 4) for i in range(20)])
    low = _series([95 + (i % 3) for i in range(20)])
    close = _series([98 + (i % 5) for i in range(20)])
    result = ind.atr(high, low, close, length=5)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    plain_avg = tr.mean()
    assert result.iloc[-1] != pytest.approx(plain_avg)
