"""Tests for stock_analyzer/technicals.py — compute_indicators (the
indicator-column builder feeding every downstream scoring surface) and
technical_score (real scoring logic, treated with the same rigor as
scanner._quick_score since it feeds a composite). Previously zero test
coverage. Pure pandas, no I/O.
"""
import pandas as pd

from stock_analyzer.technicals import compute_indicators, technical_score
from stock_analyzer.indicators import rsi as _rsi_fn


# ─── compute_indicators — NaN-Close drop-before-compute ─────────────────────

def test_compute_indicators_drops_nan_close_row_before_computing():
    n = 40
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [100 + i * 0.3 for i in range(n)]
    closes[20] = float("nan")  # a degraded mid-series bar
    df = pd.DataFrame({
        "Close":  closes,
        "High":   [c + 1 if pd.notna(c) else float("nan") for c in closes],
        "Low":    [c - 1 if pd.notna(c) else float("nan") for c in closes],
        "Volume": [1_000_000.0] * n,
    }, index=idx)

    result = compute_indicators(df)

    assert len(result) == n - 1
    assert idx[20] not in result.index
    assert not result["Close"].isna().any()

    # The NaN-Close bar must be gone BEFORE indicators are computed, not
    # merely NaN'd out afterward -- confirmed by comparing against RSI
    # computed directly on the close series with that row physically
    # removed (a real gap), not left in as NaN (which would poison the
    # EWM window around it).
    clean_idx = [i for i in idx if i != idx[20]]
    clean_close = pd.Series(
        [c for c in closes if pd.notna(c)], index=clean_idx
    )
    expected_rsi = _rsi_fn(clean_close, 14)
    pd.testing.assert_series_equal(result["RSI"], expected_rsi, check_names=False)


def test_compute_indicators_all_expected_columns_present():
    n = 60
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [100 + i * 0.2 for i in range(n)]
    df = pd.DataFrame({
        "Close":  closes,
        "High":   [c + 1 for c in closes],
        "Low":    [c - 1 for c in closes],
        "Volume": [1_000_000.0] * n,
    }, index=idx)

    result = compute_indicators(df)

    expected_cols = [
        "SMA_20", "SMA_50", "EMA_20", "RSI",
        "MACD", "MACD_signal", "MACD_hist",
        "BB_upper", "BB_mid", "BB_lower", "OBV",
    ]
    for col in expected_cols:
        assert col in result.columns, f"missing column {col}"


# ─── technical_score — builders ──────────────────────────────────────────────

_SIGNAL_COLS = ["RSI", "MACD_hist", "SMA_20", "SMA_50", "BB_upper", "BB_lower"]


def _sig_df(latest, prev=None, close=100.0, n=2):
    """Minimal df for isolating one technical_score signal branch at a time.
    All signal columns default to NaN (excluded from both points and
    max_pts) except those explicitly overridden. No 'Volume' column is
    added unless the caller passes volumes separately via _vol_df below --
    technical_score's volume gate is bare column presence, not value
    validity."""
    prev = prev or {}
    rows = []
    for _ in range(n):
        row = {c: float("nan") for c in _SIGNAL_COLS}
        row["Close"] = close
        rows.append(row)
    rows[-1].update(latest)
    if n > 1:
        rows[-2].update(prev)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(rows, index=idx)


def _vol_df(volumes, close=100.0):
    """Minimal df for isolating the Volume branch -- all other signal
    columns NaN, plus a real Volume column."""
    n = len(volumes)
    rows = []
    for v in volumes:
        row = {c: float("nan") for c in _SIGNAL_COLS}
        row["Close"] = close
        row["Volume"] = v
        rows.append(row)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(rows, index=idx)


# ─── technical_score — RSI bucket boundaries (source: 30 / 45 / 55 / 70) ────

def test_technical_score_rsi_deep_oversold_below_30():
    df = _sig_df({"RSI": 29.9})
    score, signals = technical_score(df)
    assert score == 90.0  # 18/20 * 100
    assert "Oversold" in signals["RSI"]


def test_technical_score_rsi_at_30_is_not_deep_oversold():
    df = _sig_df({"RSI": 30.0})
    score, _ = technical_score(df)
    assert score == 70.0  # 14/20 * 100


def test_technical_score_rsi_just_below_45():
    df = _sig_df({"RSI": 44.9})
    score, _ = technical_score(df)
    assert score == 70.0  # 14/20 * 100


def test_technical_score_rsi_at_45_is_neutral():
    df = _sig_df({"RSI": 45.0})
    score, signals = technical_score(df)
    assert score == 50.0  # 10/20 * 100
    assert "Neutral" in signals["RSI"]


def test_technical_score_rsi_just_below_55():
    df = _sig_df({"RSI": 54.9})
    score, _ = technical_score(df)
    assert score == 50.0  # 10/20 * 100


def test_technical_score_rsi_at_55():
    df = _sig_df({"RSI": 55.0})
    score, _ = technical_score(df)
    assert score == 30.0  # 6/20 * 100


def test_technical_score_rsi_just_below_70():
    df = _sig_df({"RSI": 69.9})
    score, _ = technical_score(df)
    assert score == 30.0  # 6/20 * 100


def test_technical_score_rsi_at_70_is_overbought():
    df = _sig_df({"RSI": 70.0})
    score, signals = technical_score(df)
    assert score == 10.0  # 2/20 * 100
    assert "Overbought" in signals["RSI"]


# ─── technical_score — MACD histogram branches ──────────────────────────────

def test_technical_score_macd_positive_and_rising():
    df = _sig_df({"MACD_hist": 5.0}, prev={"MACD_hist": 3.0})
    score, signals = technical_score(df)
    assert score == 100.0  # 20/20 * 100
    assert "rising" in signals["MACD"]


def test_technical_score_macd_positive_but_declining():
    df = _sig_df({"MACD_hist": 5.0}, prev={"MACD_hist": 10.0})
    score, signals = technical_score(df)
    assert score == 70.0  # 14/20 * 100
    assert "declining" in signals["MACD"]


def test_technical_score_macd_negative_but_improving():
    df = _sig_df({"MACD_hist": -5.0}, prev={"MACD_hist": -10.0})
    score, signals = technical_score(df)
    assert score == 40.0  # 8/20 * 100
    assert "improving" in signals["MACD"]


def test_technical_score_macd_negative_and_falling():
    df = _sig_df({"MACD_hist": -5.0}, prev={"MACD_hist": -2.0})
    score, signals = technical_score(df)
    assert score == 10.0  # 2/20 * 100
    assert "falling" in signals["MACD"]


def test_technical_score_macd_single_row_fallback_prev_equals_latest():
    # len(df) == 1 -> prev = latest, so delta is always 0. A positive hist
    # with hist == prev_hist fails "hist > prev_hist" (equal, not greater)
    # and lands in the "positive but declining" branch even though nothing
    # actually declined -- documenting the real fallback behavior.
    df = _sig_df({"MACD_hist": 5.0}, n=1)
    score, signals = technical_score(df)
    assert score == 70.0  # 14/20 * 100
    assert "declining" in signals["MACD"]


# ─── technical_score — MA trend orderings ───────────────────────────────────

def test_technical_score_ma_trend_strong_uptrend():
    df = _sig_df({"SMA_20": 105.0, "SMA_50": 100.0}, close=110.0)
    score, signals = technical_score(df)
    assert score == 100.0  # 20/20 * 100
    assert "strong uptrend" in signals["MA Trend"]


def test_technical_score_ma_trend_price_above_sma20_only():
    # sma20 (105) is NOT above sma50 (115), but price (110) is still above
    # sma20 -- second branch.
    df = _sig_df({"SMA_20": 105.0, "SMA_50": 115.0}, close=110.0)
    score, signals = technical_score(df)
    assert score == 70.0  # 14/20 * 100
    assert "short-term bullish" in signals["MA Trend"]


def test_technical_score_ma_trend_price_above_sma50_only():
    df = _sig_df({"SMA_20": 105.0, "SMA_50": 95.0}, close=100.0)
    score, signals = technical_score(df)
    assert score == 40.0  # 8/20 * 100
    assert "weakening" in signals["MA Trend"]


def test_technical_score_ma_trend_downtrend_below_both():
    df = _sig_df({"SMA_20": 95.0, "SMA_50": 100.0}, close=90.0)
    score, signals = technical_score(df)
    assert score == 10.0  # 2/20 * 100
    assert "downtrend" in signals["MA Trend"]


# ─── technical_score — Bollinger position buckets ───────────────────────────
# band_range fixed at 100 (bb_lower=0, bb_upper=100) so pos == close/100
# exactly, letting boundary values (0.2/0.4/0.6/0.8) be hit in closed form.

def test_technical_score_bollinger_band_range_zero_guard():
    # bb_upper == bb_lower -> band_range <= 0 -> the guard skips scoring
    # entirely, but max_pts was already incremented -> score 0, not 50.
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 100.0}, close=100.0)
    score, signals = technical_score(df)
    assert score == 0.0
    assert "Bollinger" not in signals


def test_technical_score_bollinger_just_below_02_near_lower_band():
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 0.0}, close=19.99)
    score, _ = technical_score(df)
    assert score == 90.0  # 18/20 * 100


def test_technical_score_bollinger_at_02_is_lower_half_not_near_lower():
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 0.0}, close=20.0)
    score, _ = technical_score(df)
    assert score == 70.0  # 14/20 * 100


def test_technical_score_bollinger_just_below_04():
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 0.0}, close=39.99)
    score, _ = technical_score(df)
    assert score == 70.0  # 14/20 * 100


def test_technical_score_bollinger_at_04_is_mid_band():
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 0.0}, close=40.0)
    score, _ = technical_score(df)
    assert score == 50.0  # 10/20 * 100


def test_technical_score_bollinger_just_below_06():
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 0.0}, close=59.99)
    score, _ = technical_score(df)
    assert score == 50.0  # 10/20 * 100


def test_technical_score_bollinger_at_06_is_upper_half():
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 0.0}, close=60.0)
    score, _ = technical_score(df)
    assert score == 30.0  # 6/20 * 100


def test_technical_score_bollinger_just_below_08():
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 0.0}, close=79.99)
    score, _ = technical_score(df)
    assert score == 30.0  # 6/20 * 100


def test_technical_score_bollinger_at_08_is_near_upper_band():
    df = _sig_df({"BB_upper": 100.0, "BB_lower": 0.0}, close=80.0)
    score, signals = technical_score(df)
    assert score == 10.0  # 2/20 * 100
    assert "potential reversal" in signals["Bollinger"]


# ─── technical_score — Volume ratio buckets ─────────────────────────────────
# 15 rows @ base volume V, last 5 rows @ Vlast; vol_avg = mean(last 20),
# vol_recent = mean(last 5) = Vlast. Round numbers chosen so ratio is exact.

def test_technical_score_volume_strong_interest_above_1_5():
    volumes = [1_000_000.0] * 15 + [3_000_000.0] * 5  # ratio = 2.0
    df = _vol_df(volumes)
    score, signals = technical_score(df)
    assert score == 100.0  # 20/20 * 100
    assert "strong interest" in signals["Volume"]


def test_technical_score_volume_at_1_5_boundary_is_above_average_not_strong():
    volumes = [1_000_000.0] * 15 + [1_800_000.0] * 5  # ratio = 1.5 exactly
    df = _vol_df(volumes)
    score, signals = technical_score(df)
    assert score == 70.0  # 14/20 * 100
    assert "above average" in signals["Volume"]


def test_technical_score_volume_normal_ratio_around_1():
    volumes = [1_000_000.0] * 20  # ratio = 1.0
    df = _vol_df(volumes)
    score, signals = technical_score(df)
    assert score == 50.0  # 10/20 * 100
    assert "normal" in signals["Volume"]


def test_technical_score_volume_low_interest_below_0_9():
    volumes = [1_000_000.0] * 15 + [500_000.0] * 5  # ratio ~= 0.571
    df = _vol_df(volumes)
    score, signals = technical_score(df)
    assert score == 25.0  # 5/20 * 100
    assert "low interest" in signals["Volume"]


# ─── technical_score — max_pts == 0 fallback ────────────────────────────────

def test_technical_score_all_signals_nan_returns_50():
    df = _sig_df({})  # every signal column stays NaN, no Volume column
    score, signals = technical_score(df)
    assert score == 50.0
    assert signals == {}


# ─── technical_score — combined happy path (max-scoring signals) ───────────

def test_technical_score_max_pts_signals_combine_to_exactly_100():
    # RSI (max bucket +18/20) and Bollinger (max bucket +18/20) each cap
    # below full marks, so an exact 100 requires only signals whose top
    # bucket awards the full 20 pts: MACD (positive+rising), MA trend
    # (strong uptrend), and Volume (>1.5x ratio) all hit 20/20 -> 60/60.
    df = _sig_df(
        {"MACD_hist": 5.0, "SMA_20": 105.0, "SMA_50": 100.0},
        prev={"MACD_hist": 3.0},
        close=110.0,
    )
    score, _ = technical_score(df)
    assert score == 100.0
