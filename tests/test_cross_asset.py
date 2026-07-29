"""Tests for stock_analyzer/cross_asset.py — the macro stress-pulse signal
feeding the Risk tab (`compute_cross_asset_signals`). `fetch_cross_asset_data`
(real `yf.download` + threading) is out of scope, matching the house
convention of skipping live-network fetchers.

Real constants (from stock_analyzer/constants.py, not invented):
CROSS_ASSET_HYG_TREND_DAYS=20, CROSS_ASSET_COPPER_TREND_DAYS=20,
CROSS_ASSET_DXY_TREND_DAYS=20, CROSS_ASSET_DXY_ROC_DAYS=5,
CROSS_ASSET_DXY_ROC_THRESHOLD=1.5, CROSS_ASSET_VIX_TERM_RATIO=1.0,
CROSS_ASSET_CURVE_STRESS_BP=-50.
"""
import pandas as pd
import pytest

from stock_analyzer import cross_asset as ca
from stock_analyzer.constants import (
    CROSS_ASSET_HYG_TREND_DAYS,
    CROSS_ASSET_COPPER_TREND_DAYS,
    CROSS_ASSET_DXY_TREND_DAYS,
    CROSS_ASSET_DXY_ROC_DAYS,
)

_DXY_N = max(CROSS_ASSET_DXY_TREND_DAYS, CROSS_ASSET_DXY_ROC_DAYS + 1)


# ─── builders ────────────────────────────────────────────────────────────────

def _mkdf(closes):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


def _hyg_df(n=CROSS_ASSET_HYG_TREND_DAYS, rising=True):
    closes = [80 + i * 0.2 for i in range(n)] if rising else [80 - i * 0.2 for i in range(n)]
    return _mkdf(closes)


def _copper_df(n=CROSS_ASSET_COPPER_TREND_DAYS, rising=True):
    closes = [4.0 + i * 0.01 for i in range(n)] if rising else [4.0 - i * 0.01 for i in range(n)]
    return _mkdf(closes)


def _dxy_df(n=_DXY_N, stressed=False):
    if stressed:
        base = [100 + i * 0.05 for i in range(n - 6)]
        last6_start = base[-1]
        tail = [last6_start + i * 0.5 for i in range(1, 7)]
        closes = base + tail
    else:
        closes = [100 + i * 0.02 for i in range(n)]
    return _mkdf(closes)


def _vix_dfs(inverted=False):
    vix, vix3m = (25.0, 20.0) if inverted else (15.0, 20.0)
    return _mkdf([vix]), _mkdf([vix3m])


def _curve_dfs(inverted=False):
    tnx, irx = (3.0, 4.0) if inverted else (4.0, 3.0)
    return _mkdf([tnx]), _mkdf([irx])


def _full_data(hyg=False, vix=False, dxy=False, copper=False, curve=False):
    """A dataset with all 5 signals available; each flag toggles that
    signal's `stressed` state (True = stressed)."""
    vix_df, vix3m_df = _vix_dfs(inverted=vix)
    tnx_df, irx_df = _curve_dfs(inverted=curve)
    return {
        "HYG":      _hyg_df(rising=not hyg),
        "^VIX":     vix_df,
        "^VIX3M":   vix3m_df,
        "DX-Y.NYB": _dxy_df(stressed=dxy),
        "HG=F":     _copper_df(rising=not copper),
        "^IRX":     irx_df,
        "^TNX":     tnx_df,
    }


# ─── HYG credit signal ────────────────────────────────────────────────────────

def test_hyg_declining_is_stressed():
    result = ca.compute_cross_asset_signals({"HYG": _hyg_df(rising=False)})
    assert result["credit"]["stressed"] is True
    assert result["credit"]["available"] is True


def test_hyg_rising_is_not_stressed():
    result = ca.compute_cross_asset_signals({"HYG": _hyg_df(rising=True)})
    assert result["credit"]["stressed"] is False


def test_hyg_below_lookback_length_is_unavailable():
    short = _hyg_df(n=CROSS_ASSET_HYG_TREND_DAYS - 1, rising=False)
    result = ca.compute_cross_asset_signals({"HYG": short})
    assert result["credit"]["available"] is False


# ─── VIX term structure signal ────────────────────────────────────────────────

def test_vix_ratio_exactly_at_one_is_not_stressed():
    vix_df, vix3m_df = _mkdf([20.0]), _mkdf([20.0])
    result = ca.compute_cross_asset_signals({"^VIX": vix_df, "^VIX3M": vix3m_df})
    assert result["vix_term"]["stressed"] is False
    assert result["vix_term"]["available"] is True


def test_vix_ratio_just_above_one_is_stressed():
    vix_df, vix3m_df = _mkdf([20.01]), _mkdf([20.0])
    result = ca.compute_cross_asset_signals({"^VIX": vix_df, "^VIX3M": vix3m_df})
    assert result["vix_term"]["stressed"] is True


def test_vix3m_zero_is_unavailable():
    vix_df, vix3m_df = _mkdf([20.0]), _mkdf([0.0])
    result = ca.compute_cross_asset_signals({"^VIX": vix_df, "^VIX3M": vix3m_df})
    assert result["vix_term"]["available"] is False


def test_vix_missing_one_side_is_unavailable():
    result = ca.compute_cross_asset_signals({"^VIX": _mkdf([20.0])})
    assert result["vix_term"]["available"] is False


# ─── DXY compound condition ───────────────────────────────────────────────────

def test_dxy_rising_but_roc_small_is_not_stressed():
    result = ca.compute_cross_asset_signals({"DX-Y.NYB": _dxy_df(stressed=False)})
    assert result["dollar"]["available"] is True
    assert result["dollar"]["stressed"] is False


def test_dxy_high_roc_but_declining_trend_is_not_stressed():
    # Steep decline over most of the window, mild recovery in the last 6
    # points -- 5-day ROC clears the threshold but the overall slope stays
    # negative, so the AND condition fails.
    base = [200 - i * 7.0 for i in range(_DXY_N - 6)]
    tail = [base[-1] + i * 1.0 for i in range(1, 7)]
    dxy = _mkdf(base + tail)
    result = ca.compute_cross_asset_signals({"DX-Y.NYB": dxy})
    assert result["dollar"]["stressed"] is False


def test_dxy_rising_and_high_roc_is_stressed():
    result = ca.compute_cross_asset_signals({"DX-Y.NYB": _dxy_df(stressed=True)})
    assert result["dollar"]["stressed"] is True


def test_dxy_below_required_length_is_unavailable():
    short = _dxy_df(n=_DXY_N - 1, stressed=True)
    result = ca.compute_cross_asset_signals({"DX-Y.NYB": short})
    assert result["dollar"]["available"] is False


# ─── Copper signal ────────────────────────────────────────────────────────────

def test_copper_declining_is_stressed():
    result = ca.compute_cross_asset_signals({"HG=F": _copper_df(rising=False)})
    assert result["copper"]["stressed"] is True


def test_copper_rising_is_not_stressed():
    result = ca.compute_cross_asset_signals({"HG=F": _copper_df(rising=True)})
    assert result["copper"]["stressed"] is False


def test_copper_below_lookback_length_is_unavailable():
    short = _copper_df(n=CROSS_ASSET_COPPER_TREND_DAYS - 1, rising=False)
    result = ca.compute_cross_asset_signals({"HG=F": short})
    assert result["copper"]["available"] is False


# ─── Yield curve signal ────────────────────────────────────────────────────────

def test_curve_spread_exactly_at_negative_50bp_is_not_stressed():
    tnx_df, irx_df = _mkdf([3.0]), _mkdf([3.5])  # spread = -50bp exactly
    result = ca.compute_cross_asset_signals({"^TNX": tnx_df, "^IRX": irx_df})
    assert result["curve"]["stressed"] is False
    assert result["curve"]["available"] is True


def test_curve_spread_just_below_negative_50bp_is_stressed():
    tnx_df, irx_df = _mkdf([2.99]), _mkdf([3.5])  # spread = -51bp
    result = ca.compute_cross_asset_signals({"^TNX": tnx_df, "^IRX": irx_df})
    assert result["curve"]["stressed"] is True


# ─── Aggregate score / label / summary ───────────────────────────────────────

def test_all_signals_unavailable_gives_offline_summary_not_calm():
    result = ca.compute_cross_asset_signals({})
    assert result["score"] == 0
    assert result["label"] == "—"
    assert "unavailable" in result["summary"].lower()
    assert "offline" in result["summary"].lower()


def test_zero_stressed_but_available_gives_calm_summary_distinct_from_offline():
    data = _full_data()  # all 5 calm
    result = ca.compute_cross_asset_signals(data)
    assert result["score"] == 0
    assert result["summary"] == "All available cross-asset signals calm."
    assert result["label"] == "Calm"


@pytest.mark.parametrize("score,expected_label", [
    (0, "Calm"),
    (1, "Calm"),
    (2, "Caution"),
    (3, "Stress"),
    (4, "Stress"),
    (5, "Alarm"),
])
def test_score_label_boundaries(score, expected_label):
    flags = [False] * 5
    for i in range(score):
        flags[i] = True
    hyg, vix, dxy, copper, curve = flags
    data = _full_data(hyg=hyg, vix=vix, dxy=dxy, copper=copper, curve=curve)
    result = ca.compute_cross_asset_signals(data)
    assert result["score"] == score
    assert result["label"] == expected_label


def test_summary_lists_stressed_signal_names():
    data = _full_data(hyg=True, curve=True)
    result = ca.compute_cross_asset_signals(data)
    assert result["score"] == 2
    assert "credit spreads (HYG)" in result["summary"]
    assert "3m10y spread inverted" in result["summary"]
    assert "2 of 5 cross-asset signals showing stress" in result["summary"]
