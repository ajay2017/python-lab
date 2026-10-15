"""technical_coverage / t_score_or_none / bundle `t_available` flag (C1: additive,
nothing withholds on it yet). Counts derive from TECHNICAL_MIN_BARS, never literals."""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import bundle_loader
from stock_analyzer.constants import TECHNICAL_MIN_BARS
from stock_analyzer.technicals import (
    compute_indicators, technical_score, technical_coverage, _REQUIRED_LEGS,
)
from stock_analyzer.util import t_score_or_none

pytestmark = pytest.mark.fast


def _ohlcv(n: int, volume: float | None = 1_000_000.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-02", periods=n)
    closes = [100 + i * 0.3 + (i % 5) * 0.4 for i in range(n)]
    return pd.DataFrame({
        "Close": closes, "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes], "Open": closes,
        "Volume": [volume if volume is not None else np.nan] * n,
    }, index=idx)


def _cov(n, **k):
    df = compute_indicators(_ohlcv(n, **k))
    _, sig = technical_score(df)
    return technical_coverage(df, sig), sig


def test_empty_df():
    cov = technical_coverage(pd.DataFrame(), {})
    assert cov == {"bars": 0, "legs": 0, "available": False}


def test_no_close_column():
    assert technical_coverage(pd.DataFrame({"Volume": [1.0]}), {})["bars"] == 0


def test_below_floor_unavailable():
    cov, _ = _cov(TECHNICAL_MIN_BARS - 1)
    assert cov["bars"] == TECHNICAL_MIN_BARS - 1 and cov["available"] is False


def test_at_floor_available():
    cov, _ = _cov(TECHNICAL_MIN_BARS)
    assert cov["bars"] == TECHNICAL_MIN_BARS and cov["available"] is True


def test_long_history_available():
    cov, sig = _cov(120)
    assert cov["available"] is True and cov["legs"] == len(sig) == 5


def test_coherence_floor_matches_sma_lookback():
    _, at = _cov(TECHNICAL_MIN_BARS)
    _, below = _cov(TECHNICAL_MIN_BARS - 1)
    assert "MA Trend" in at
    assert "MA Trend" not in below


def test_volume_not_required():
    cov, sig = _cov(TECHNICAL_MIN_BARS + 10, volume=None)
    assert "Volume" not in sig
    assert cov["available"] is True and cov["legs"] == 4


def test_nan_closes_reduce_bars():
    df = compute_indicators(_ohlcv(TECHNICAL_MIN_BARS + 10))
    _, sig = technical_score(df)
    raw = df.copy()
    raw.iloc[:15, raw.columns.get_loc("Close")] = np.nan
    cov = technical_coverage(raw, sig)
    assert cov["bars"] == TECHNICAL_MIN_BARS - 5
    assert cov["available"] is False


def test_inf_close_not_counted():
    df = _ohlcv(TECHNICAL_MIN_BARS)
    _, sig = technical_score(compute_indicators(df))
    df.iloc[0, df.columns.get_loc("Close")] = np.inf
    assert technical_coverage(df, sig)["bars"] == TECHNICAL_MIN_BARS - 1


@pytest.mark.parametrize("leg", _REQUIRED_LEGS)
def test_missing_required_leg_unavailable(leg):
    df = compute_indicators(_ohlcv(120))
    _, sig = technical_score(df)
    sig = {k: v for k, v in sig.items() if k != leg}
    assert technical_coverage(df, sig)["available"] is False


def test_required_legs_are_the_four_non_volume():
    assert set(_REQUIRED_LEGS) == {"RSI", "MACD", "Bollinger", "MA Trend"}


def test_technical_score_unchanged():
    # Pinned from HEAD behaviour before the C1 edit.
    s5, sig5 = technical_score(compute_indicators(_ohlcv(5)))
    assert s5 == 53.3 and set(sig5) == {"RSI", "MACD", "Volume"}
    s120, sig120 = technical_score(compute_indicators(_ohlcv(120)))
    assert s120 == 54.0 and len(sig120) == 5
    assert technical_score(pd.DataFrame()) == (50.0, {})


# ── t_score_or_none ──────────────────────────────────────────────────────────
def test_t_score_or_none():
    assert t_score_or_none(61.0, {"t_available": True}) == 61.0
    assert t_score_or_none(61.0, {"t_available": False}) is None
    assert t_score_or_none(61.0, {}) == 61.0
    assert t_score_or_none(61.0, None) == 61.0


# ── bundle wiring ────────────────────────────────────────────────────────────
class _NoopDb:
    def __getattr__(self, name):
        if name.startswith("load_"):
            return lambda *a, **k: ("" if name == "load_sector_cache" else None)
        return lambda *a, **k: None


@pytest.mark.parametrize("n,expected", [(TECHNICAL_MIN_BARS - 1, False),
                                        (TECHNICAL_MIN_BARS, True), (120, True)])
def test_bundle_carries_flag(monkeypatch, n, expected):
    monkeypatch.setattr(bundle_loader, "db", _NoopDb())
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle", lambda t, p: {
        "history": _ohlcv(n), "info": {"sector": "Technology"}, "news": [],
        "earnings": {}, "revisions": {}, "_info_source": "test"})
    out = bundle_loader.load_bundle("TESTX")
    assert out["t_available"] is expected
    assert out["t_bars"] == n
