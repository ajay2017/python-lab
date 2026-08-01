"""
Tests for stock_analyzer/monte_carlo.py — the historical block-bootstrap
Monte Carlo Outcome Range simulator: safe-float helpers, history alignment
+ exclusion-by-insufficient-history, the correlated block bootstrap itself
(shape, reproducibility with a fixed seed, and correlation preservation),
percentile summarization, and the top-level orchestrator. Zero coverage
before this batch. `fetch_long_history` calls `data.fetch_price_history` —
monkeypatched at the module-level name it's imported under
(`stock_analyzer.data.fetch_price_history` via the `_data` alias) so no
real network calls occur.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import monte_carlo as mc


# ─── _f / _weight_fraction ──────────────────────────────────────────────────

def test_f_none_returns_default():
    assert mc._f(None) == 0.0
    assert mc._f(None, default=5.0) == 5.0


def test_f_nan_returns_default():
    assert mc._f(float("nan"), default=3.0) == 3.0


def test_f_unparseable_string_returns_default():
    assert mc._f("not-a-number", default=1.5) == 1.5


def test_weight_fraction_converts_pct_to_fraction():
    assert mc._weight_fraction(12.5) == pytest.approx(0.125)


def test_weight_fraction_missing_is_zero():
    assert mc._weight_fraction(None) == 0.0


# ─── fetch_long_history ─────────────────────────────────────────────────────

def _fake_history(n_days=300, seed=1):
    idx = pd.date_range("2020-01-01", periods=n_days, freq="B")
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n_days))
    return pd.DataFrame({"Close": close}, index=idx)


def test_fetch_long_history_returns_df_per_ticker(monkeypatch):
    monkeypatch.setattr(mc._data, "fetch_price_history", lambda t, period: _fake_history())
    out = mc.fetch_long_history(["AAA", "BBB"], period="5y")
    assert set(out.keys()) == {"AAA", "BBB"}
    assert all(isinstance(df, pd.DataFrame) for df in out.values())


def test_fetch_long_history_none_on_fetch_exception(monkeypatch):
    def _raise(t, period):
        raise RuntimeError("provider down")
    monkeypatch.setattr(mc._data, "fetch_price_history", _raise)
    out = mc.fetch_long_history(["AAA"], period="5y")
    assert out["AAA"] is None


# ─── build_return_matrix ─────────────────────────────────────────────────────

def test_build_return_matrix_excludes_short_history():
    long_history = {
        "LONG":  _fake_history(300, seed=1),
        "SHORT": _fake_history(50, seed=2),
    }
    returns_df, excluded = mc.build_return_matrix(long_history, min_days=252)
    assert excluded == ["SHORT"]
    assert list(returns_df.columns) == ["LONG"]
    assert len(returns_df) == 299   # pct_change drops the first row


def test_build_return_matrix_excludes_none_and_empty():
    long_history = {
        "OK":    _fake_history(300, seed=1),
        "NONE":  None,
        "EMPTY": pd.DataFrame(),
    }
    returns_df, excluded = mc.build_return_matrix(long_history, min_days=252)
    assert set(excluded) == {"NONE", "EMPTY"}
    assert list(returns_df.columns) == ["OK"]


def test_build_return_matrix_no_qualifying_tickers_returns_empty():
    long_history = {"SHORT": _fake_history(50, seed=1)}
    returns_df, excluded = mc.build_return_matrix(long_history, min_days=252)
    assert returns_df.empty
    assert excluded == ["SHORT"]


# ─── block_bootstrap_paths ───────────────────────────────────────────────────

def _returns_df(n_days=300, tickers=("A", "B"), correlated=True, seed=1):
    idx = pd.date_range("2020-01-01", periods=n_days, freq="B")
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.01, n_days)
    data = {}
    for i, t in enumerate(tickers):
        if correlated:
            data[t] = base + rng.normal(0, 0.0001, n_days)   # near-identical to `base`
        else:
            data[t] = rng.normal(0, 0.01, n_days)
    return pd.DataFrame(data, index=idx)


def test_block_bootstrap_paths_shape():
    rdf = _returns_df()
    paths = mc.block_bootstrap_paths(
        rdf, {"A": 0.5, "B": 0.5}, n_trials=50, block_days=10, horizon_days=21, seed=42,
    )
    assert paths.shape == (50, 21)


def test_block_bootstrap_paths_reproducible_with_seed():
    rdf = _returns_df()
    p1 = mc.block_bootstrap_paths(rdf, {"A": 0.5, "B": 0.5}, n_trials=20, block_days=10, horizon_days=21, seed=7)
    p2 = mc.block_bootstrap_paths(rdf, {"A": 0.5, "B": 0.5}, n_trials=20, block_days=10, horizon_days=21, seed=7)
    np.testing.assert_array_equal(p1, p2)


def test_block_bootstrap_paths_different_seeds_differ():
    rdf = _returns_df()
    p1 = mc.block_bootstrap_paths(rdf, {"A": 0.5, "B": 0.5}, n_trials=20, block_days=10, horizon_days=21, seed=1)
    p2 = mc.block_bootstrap_paths(rdf, {"A": 0.5, "B": 0.5}, n_trials=20, block_days=10, horizon_days=21, seed=2)
    assert not np.array_equal(p1, p2)


def test_block_bootstrap_paths_preserves_correlation():
    # Two near-perfectly-correlated tickers: an equal-weight portfolio of both
    # should have roughly the same simulated volatility as either alone, since
    # the SAME sampled dates are applied to both -- if correlation were
    # destroyed (independent per-ticker resampling), the portfolio's simulated
    # vol at the horizon would be visibly damped by diversification instead.
    rdf = _returns_df(correlated=True, seed=3)
    paths = mc.block_bootstrap_paths(rdf, {"A": 0.5, "B": 0.5}, n_trials=500, block_days=15, horizon_days=63, seed=11)
    solo_paths = mc.block_bootstrap_paths(rdf, {"A": 1.0}, n_trials=500, block_days=15, horizon_days=63, seed=11)
    port_std = paths[:, -1].std()
    solo_std = solo_paths[:, -1].std()
    # correlated portfolio vol should be close to (not dramatically less than) solo vol.
    # An INDEPENDENT (uncorrelated) equal-weight 2-asset portfolio would land near
    # 1/sqrt(2) =~ 0.707x solo vol from diversification alone, so the bound must sit
    # above that or this test can't actually catch broken (independent) resampling.
    assert port_std > solo_std * 0.9


def test_block_bootstrap_paths_no_matching_tickers_returns_zeros():
    rdf = _returns_df()
    paths = mc.block_bootstrap_paths(rdf, {"ZZZ": 1.0}, n_trials=10, block_days=10, horizon_days=21)
    assert paths.shape == (10, 21)
    assert np.all(paths == 0.0)


def test_block_bootstrap_paths_zero_weight_sum_returns_zeros():
    rdf = _returns_df()
    paths = mc.block_bootstrap_paths(rdf, {"A": 0.0, "B": 0.0}, n_trials=10, block_days=10, horizon_days=21)
    assert np.all(paths == 0.0)


def test_block_bootstrap_paths_renormalizes_partial_weight_overlap():
    # Only "A" of the two tickers has a weight entry -- portfolio should
    # behave as 100% A (renormalized), not silently drop to near-zero.
    rdf = _returns_df(correlated=False)
    paths_a_only = mc.block_bootstrap_paths(rdf, {"A": 0.3}, n_trials=5, block_days=10, horizon_days=21, seed=5)
    paths_full_a = mc.block_bootstrap_paths(rdf, {"A": 1.0}, n_trials=5, block_days=10, horizon_days=21, seed=5)
    np.testing.assert_array_equal(paths_a_only, paths_full_a)


# ─── summarize_paths ─────────────────────────────────────────────────────────

def test_summarize_paths_empty_returns_empty_dict():
    assert mc.summarize_paths(np.array([])) == {}


def test_summarize_paths_known_distribution():
    # 5 trials, 2-day horizon, deterministic values so percentiles are exact.
    paths = np.array([
        [0.0, 0.00],
        [0.0, 0.01],
        [0.0, 0.02],
        [0.0, 0.03],
        [0.0, 0.04],
    ])
    summary = mc.summarize_paths(paths, percentiles=(0, 50, 100))
    assert summary["horizon_days"] == 2
    assert summary["n_trials"] == 5
    assert summary["endpoint_pct"][0] == pytest.approx(0.00)
    assert summary["endpoint_pct"][50] == pytest.approx(0.02)
    assert summary["endpoint_pct"][100] == pytest.approx(0.04)
    assert summary["bands"][50] == [pytest.approx(0.0), pytest.approx(0.02)]


# ─── run_monte_carlo ─────────────────────────────────────────────────────────

def _port_df(tickers=("AAA", "BBB"), weights=(60.0, 40.0), mvals=(6000.0, 4000.0)):
    return pd.DataFrame({
        "Ticker":       list(tickers),
        "Weight (%)":   list(weights),
        "Market Value": list(mvals),
    })


def test_run_monte_carlo_empty_port_df_returns_empty_dict():
    assert mc.run_monte_carlo(pd.DataFrame(), horizon_days=21) == {}
    assert mc.run_monte_carlo(None, horizon_days=21) == {}


def test_run_monte_carlo_end_to_end(monkeypatch):
    monkeypatch.setattr(mc, "fetch_long_history", lambda tickers, period: {
        "AAA": _fake_history(300, seed=1),
        "BBB": _fake_history(300, seed=2),
    })
    result = mc.run_monte_carlo(_port_df(), horizon_days=21, n_trials=25, seed=1)
    assert result["included"] == ["AAA", "BBB"]
    assert result["excluded"] == []
    assert result["portfolio_value"] == pytest.approx(10000.0)
    assert result["summary"]["horizon_days"] == 21
    assert result["summary"]["n_trials"] == 25


def test_run_monte_carlo_reports_excluded_short_history_ticker(monkeypatch):
    monkeypatch.setattr(mc, "fetch_long_history", lambda tickers, period: {
        "AAA": _fake_history(300, seed=1),
        "BBB": _fake_history(50, seed=2),   # too short
    })
    result = mc.run_monte_carlo(_port_df(), horizon_days=21, n_trials=10, min_days=252, seed=1)
    assert result["included"] == ["AAA"]
    assert result["excluded"] == ["BBB"]
    # $ basis must scale to the INCLUDED ticker only (AAA = $6000 of the
    # $10000 book) -- not the full portfolio value -- so the % and $ ranges
    # shown to the user describe the same thing (a book with an excluded
    # material-weight ticker must not have its $ range silently overstated).
    assert result["portfolio_value"] == pytest.approx(6000.0)


def test_run_monte_carlo_no_included_tickers_returns_shape_with_empty_summary(monkeypatch):
    monkeypatch.setattr(mc, "fetch_long_history", lambda tickers, period: {
        "AAA": _fake_history(50, seed=1),
        "BBB": _fake_history(50, seed=2),
    })
    result = mc.run_monte_carlo(_port_df(), horizon_days=21, min_days=252)
    assert result["included"] == []
    assert result["summary"] == {}
    # No included tickers -> no $ basis to show (avoids implying the full
    # portfolio moved like a simulation that actually includes none of it).
    assert result["portfolio_value"] == pytest.approx(0.0)
