"""Tests for stock_analyzer/portfolio_intelligence.py — the 🧩 Portfolio
Intelligence page's correlation-clustering, risk-budget (Euler decomposition),
and factor-tilt panels. Pure pandas/numpy logic, no I/O. Constants used (from
stock_analyzer/constants.py): CORR_HIGH_PAIRS_THRESHOLD=0.65,
CORR_DANGER_PAIRS_THRESHOLD=0.80. Previously zero test coverage.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import portfolio_intelligence as pi
from stock_analyzer.constants import CORR_HIGH_PAIRS_THRESHOLD, CORR_DANGER_PAIRS_THRESHOLD


# ─── correlation_clusters — builders ─────────────────────────────────────────

def _corr_df(tickers, pairs, default=0.10):
    """Square, symmetric, diagonal=1.0 correlation matrix. `pairs` is a list
    of (t1, t2, corr) overrides; every other off-diagonal cell gets
    `default` (kept below CORR_HIGH_PAIRS_THRESHOLD unless overridden)."""
    n = len(tickers)
    df = pd.DataFrame(np.eye(n), index=tickers, columns=tickers)
    for t1 in tickers:
        for t2 in tickers:
            if t1 != t2:
                df.loc[t1, t2] = default
    for t1, t2, c in pairs:
        df.loc[t1, t2] = c
        df.loc[t2, t1] = c
    return df


# ─── correlation_clusters — empty / too-small input ──────────────────────────

def test_correlation_clusters_none_returns_empty_list():
    assert pi.correlation_clusters(None) == []


def test_correlation_clusters_empty_df_returns_empty_list():
    assert pi.correlation_clusters(pd.DataFrame()) == []


def test_correlation_clusters_single_ticker_returns_empty_list():
    df = _corr_df(["A"], [])
    assert pi.correlation_clusters(df) == []


# ─── correlation_clusters — simple pair + boundary ───────────────────────────

def test_correlation_clusters_simple_pair_above_threshold_forms_cluster():
    df = _corr_df(["A", "B"], [("A", "B", 0.70)])
    clusters = pi.correlation_clusters(df)
    assert len(clusters) == 1
    assert clusters[0]["tickers"] == ["A", "B"]
    assert clusters[0]["size"] == 2


def test_correlation_clusters_pair_at_exact_threshold_forms_edge():
    df = _corr_df(["A", "B"], [("A", "B", CORR_HIGH_PAIRS_THRESHOLD)])
    clusters = pi.correlation_clusters(df)
    assert len(clusters) == 1


def test_correlation_clusters_pair_just_below_threshold_no_cluster():
    df = _corr_df(["A", "B"], [("A", "B", CORR_HIGH_PAIRS_THRESHOLD - 0.01)])
    assert pi.correlation_clusters(df) == []


# ─── correlation_clusters — transitivity ─────────────────────────────────────

def test_correlation_clusters_transitive_chain_forms_one_cluster():
    # A-B and B-C flagged, A-C is NOT (0.3, below threshold) -- still one
    # 3-member cluster via connected components, not two separate pairs.
    df = _corr_df(["A", "B", "C"], [("A", "B", 0.70), ("B", "C", 0.70), ("A", "C", 0.30)])
    clusters = pi.correlation_clusters(df)
    assert len(clusters) == 1
    assert clusters[0]["tickers"] == ["A", "B", "C"]
    assert clusters[0]["size"] == 3


# ─── correlation_clusters — singleton exclusion ──────────────────────────────

def test_correlation_clusters_singleton_with_no_correlated_pair_excluded():
    df = _corr_df(["A", "B", "C"], [("A", "B", 0.70)])  # C isolated
    clusters = pi.correlation_clusters(df)
    assert len(clusters) == 1
    all_tickers = clusters[0]["tickers"]
    assert "C" not in all_tickers


# ─── correlation_clusters — danger vs warning tier ───────────────────────────

def test_correlation_clusters_danger_tier_when_pair_at_or_above_danger_threshold():
    df = _corr_df(["A", "B"], [("A", "B", CORR_DANGER_PAIRS_THRESHOLD)])
    clusters = pi.correlation_clusters(df)
    assert clusters[0]["tier"] == "danger"


def test_correlation_clusters_warning_tier_when_below_danger_threshold():
    df = _corr_df(["A", "B"], [("A", "B", CORR_DANGER_PAIRS_THRESHOLD - 0.01)])
    clusters = pi.correlation_clusters(df)
    assert clusters[0]["tier"] == "warning"


def test_correlation_clusters_cluster_flagged_danger_if_any_internal_pair_qualifies():
    # 3-member cluster where only ONE of the 3 internal pairs hits danger --
    # the whole cluster is still tagged danger.
    df = _corr_df(
        ["A", "B", "C"],
        [("A", "B", 0.70), ("B", "C", 0.70), ("A", "C", CORR_DANGER_PAIRS_THRESHOLD)],
    )
    clusters = pi.correlation_clusters(df)
    assert clusters[0]["tier"] == "danger"


# ─── correlation_clusters — avg_internal_corr across ALL internal pairs ──────

def test_correlation_clusters_avg_internal_corr_across_all_pairs_not_just_edges():
    # A-C is NaN (no edge, adjacency skips it), A-B and B-C form the edges.
    # avg_internal_corr should be mean(0.70, 0.70) = 0.70 -- the NaN pair is
    # excluded from the average too, not treated as 0.
    df = _corr_df(["A", "B", "C"], [("A", "B", 0.70), ("B", "C", 0.70)])
    df.loc["A", "C"] = float("nan")
    df.loc["C", "A"] = float("nan")
    clusters = pi.correlation_clusters(df)
    assert len(clusters) == 1
    assert clusters[0]["avg_internal_corr"] == pytest.approx(0.70)


def test_correlation_clusters_nan_cells_do_not_crash_or_form_edge():
    df = _corr_df(["A", "B", "C"], [])
    df.loc["A", "B"] = float("nan")
    df.loc["B", "A"] = float("nan")
    # No usable pairs above threshold anywhere -> no clusters, no crash.
    assert pi.correlation_clusters(df) == []


# ─── correlation_clusters — sort order: with weights vs without ─────────────

def test_correlation_clusters_sort_without_weights_by_size_desc():
    # Cluster1 = {A,B,C} fully connected (avg 0.66), cluster2 = {D,E} pair
    # (avg 0.75). weights=None -> sort by size desc: cluster1 (3) first.
    tickers = ["A", "B", "C", "D", "E"]
    pairs = [("A", "B", 0.66), ("B", "C", 0.66), ("A", "C", 0.66), ("D", "E", 0.75)]
    df = _corr_df(tickers, pairs)
    clusters = pi.correlation_clusters(df, weights=None)
    assert len(clusters) == 2
    assert clusters[0]["size"] == 3
    assert clusters[1]["size"] == 2


def test_correlation_clusters_empty_dict_weights_uses_weights_branch_not_none_branch():
    # Same clusters as above, but weights={} (NOT None) -- combined_weight_pct
    # is 0 for both (tie), so the tiebreak (avg_internal_corr desc) decides:
    # cluster2 (0.75) sorts BEFORE cluster1 (0.66), the opposite order from
    # the weights=None (size-desc) test above.
    tickers = ["A", "B", "C", "D", "E"]
    pairs = [("A", "B", 0.66), ("B", "C", 0.66), ("A", "C", 0.66), ("D", "E", 0.75)]
    df = _corr_df(tickers, pairs)
    clusters = pi.correlation_clusters(df, weights={})
    assert len(clusters) == 2
    assert clusters[0]["tickers"] == ["D", "E"]
    assert clusters[1]["tickers"] == ["A", "B", "C"]


def test_correlation_clusters_sort_with_real_weights_by_combined_weight_desc():
    tickers = ["A", "B", "C", "D", "E"]
    pairs = [("A", "B", 0.66), ("B", "C", 0.66), ("A", "C", 0.66), ("D", "E", 0.75)]
    df = _corr_df(tickers, pairs)
    weights = {"A": 5.0, "B": 5.0, "C": 5.0, "D": 50.0, "E": 50.0}
    clusters = pi.correlation_clusters(df, weights=weights)
    assert clusters[0]["tickers"] == ["D", "E"]
    assert clusters[0]["combined_weight_pct"] == pytest.approx(100.0)
    assert clusters[1]["tickers"] == ["A", "B", "C"]
    assert clusters[1]["combined_weight_pct"] == pytest.approx(15.0)


# ─── correlation_clusters — malformed input never raises ────────────────────

def test_correlation_clusters_malformed_duplicate_index_returns_empty_list():
    # Duplicate "A" index/column labels make corr_df.loc["A","A"] return a
    # DataFrame (not a scalar); float() on it raises -- caught by the outer
    # try/except, returns [] rather than propagating.
    df = pd.DataFrame(
        [[1.0, 1.0, 0.7], [1.0, 1.0, 0.7], [0.7, 0.7, 1.0]],
        index=["A", "A", "B"], columns=["A", "A", "B"],
    )
    assert pi.correlation_clusters(df) == []


# ─── risk_budget — builders ──────────────────────────────────────────────────

def _price_series(seed, n=60, start=100.0, drift=0.0005, vol=0.02):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    prices = start * np.cumprod(1 + rets)
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.Series(prices, index=idx)


def _held_data(tickers_vols):
    """tickers_vols: {ticker: (seed, vol)}"""
    return {
        t: {"df": pd.DataFrame({"Close": _price_series(seed, vol=vol)})}
        for t, (seed, vol) in tickers_vols.items()
    }


# ─── risk_budget — empty / undersized input ──────────────────────────────────

def test_risk_budget_empty_held_data_returns_empty_shape():
    result = pi.risk_budget({}, {})
    assert result == {"positions": [], "portfolio_vol_annualized_pct": None, "n_included": 0}


def test_risk_budget_single_usable_ticker_returns_empty_shape():
    held = _held_data({"A": (1, 0.02)})
    result = pi.risk_budget(held, {"A": 100.0})
    assert result["positions"] == []
    assert result["n_included"] == 0


# ─── risk_budget — Euler decomposition math ──────────────────────────────────

def test_risk_budget_risk_pct_sums_to_approximately_100():
    held = _held_data({"A": (1, 0.02), "B": (2, 0.02), "C": (3, 0.04)})
    weights = {"A": 40.0, "B": 30.0, "C": 30.0}
    result = pi.risk_budget(held, weights, trading_days=252)
    assert result["n_included"] == 3
    total = sum(p["risk_pct"] for p in result["positions"])
    assert total == pytest.approx(100.0, abs=1.0)


def test_risk_budget_vol_annualized_uses_sqrt_trading_days_scaling():
    held = _held_data({"A": (1, 0.02), "B": (2, 0.02)})
    weights = {"A": 50.0, "B": 50.0}
    result = pi.risk_budget(held, weights, trading_days=252)

    # Recompute the aligned per-ticker vol the same way the function does,
    # to confirm the sqrt(trading_days) annualization is actually applied.
    prices = pd.DataFrame({
        "A": held["A"]["df"]["Close"], "B": held["B"]["df"]["Close"],
    }).dropna()
    returns = prices.pct_change().dropna()
    expected_vol_a = round(float(returns["A"].std() * (252 ** 0.5) * 100), 1)

    posA = next(p for p in result["positions"] if p["ticker"] == "A")
    assert posA["vol_annualized_pct"] == pytest.approx(expected_vol_a)


def test_risk_budget_risk_to_weight_ratio_none_when_weight_exactly_zero():
    held = _held_data({"A": (1, 0.02), "B": (2, 0.02), "D": (4, 0.03)})
    weights = {"A": 60.0, "B": 40.0, "D": 0.0}
    result = pi.risk_budget(held, weights, trading_days=252)
    posD = next(p for p in result["positions"] if p["ticker"] == "D")
    assert posD["weight_pct"] == 0.0
    assert posD["risk_to_weight_ratio"] is None


def test_risk_budget_sort_order_descending_by_risk_pct():
    held = _held_data({"A": (1, 0.01), "B": (2, 0.05), "C": (3, 0.03)})
    weights = {"A": 33.0, "B": 34.0, "C": 33.0}
    result = pi.risk_budget(held, weights, trading_days=252)
    risk_pcts = [p["risk_pct"] for p in result["positions"]]
    assert risk_pcts == sorted(risk_pcts, reverse=True)


def test_risk_budget_ticker_absent_from_weights_defaults_to_zero_no_crash():
    held = _held_data({"A": (1, 0.02), "B": (2, 0.02), "C": (3, 0.03)})
    weights = {"A": 50.0, "B": 50.0}  # C is absent
    result = pi.risk_budget(held, weights, trading_days=252)
    assert result["n_included"] == 3
    posC = next(p for p in result["positions"] if p["ticker"] == "C")
    assert posC["weight_pct"] == 0.0


# ─── factor_tilt — builders ──────────────────────────────────────────────────

def _close_series_from_returns(returns, first_date):
    """Build a Close price Series whose pct_change() reproduces `returns`
    exactly, with the return-row index starting at `first_date` (i.e. Close
    has one extra leading row one business day earlier)."""
    idx = pd.bdate_range(first_date, periods=len(returns))
    prepend = idx[0] - pd.tseries.offsets.BDay(1)
    full_idx = pd.DatetimeIndex([prepend]).append(idx)
    prices = 100.0 * np.cumprod(1 + np.concatenate([[0.0], returns]))
    return pd.Series(prices, index=full_idx)


# ─── factor_tilt — empty input ────────────────────────────────────────────────

def test_factor_tilt_empty_held_data_returns_empty_shape():
    result = pi.factor_tilt({}, {}, {"F": pd.Series([0.01, 0.02])})
    assert result == {"positions": [], "portfolio_tilt": {}, "n_included": 0}


def test_factor_tilt_empty_factor_returns_returns_empty_shape():
    held = {"A": {"df": pd.DataFrame({"Close": [100.0, 101.0, 102.0]})}}
    result = pi.factor_tilt(held, {"A": 100.0}, {})
    assert result == {"positions": [], "portfolio_tilt": {}, "n_included": 0}


# ─── factor_tilt — min_overlap_days boundary ─────────────────────────────────

def test_factor_tilt_overlap_one_below_minimum_excluded():
    rA = np.linspace(0.001, 0.02, 25)
    close_a = _close_series_from_returns(rA, "2024-01-02")
    close_b = _close_series_from_returns(rA, "2024-01-02")  # filler, kept in output
    factor_idx = pd.bdate_range("2024-01-02", periods=19)  # 19 < 20 min_overlap
    factor_f = pd.Series(np.linspace(-0.01, 0.01, 19), index=factor_idx)
    # A second factor with FULL overlap so position A isn't dropped entirely
    # (a position with zero usable correlations across every factor gets
    # dropped from the output, which would make this boundary unobservable).
    factor_g = pd.Series(rA, index=pd.bdate_range("2024-01-02", periods=25))

    held = {"A": {"df": pd.DataFrame({"Close": close_a})},
            "B": {"df": pd.DataFrame({"Close": close_b})}}
    result = pi.factor_tilt(
        held, {"A": 60.0, "B": 40.0}, {"F": factor_f, "G": factor_g}, min_overlap_days=20
    )
    posA = next(p for p in result["positions"] if p["ticker"] == "A")
    assert posA["correlations"]["F"] is None
    assert posA["correlations"]["G"] is not None


def test_factor_tilt_overlap_exactly_at_minimum_included():
    rA = np.linspace(0.001, 0.02, 25)
    close_a = _close_series_from_returns(rA, "2024-01-02")
    close_b = _close_series_from_returns(rA, "2024-01-02")
    factor_idx = pd.bdate_range("2024-01-02", periods=20)  # exactly 20
    factor_f = pd.Series(np.linspace(-0.01, 0.01, 20), index=factor_idx)

    held = {"A": {"df": pd.DataFrame({"Close": close_a})},
            "B": {"df": pd.DataFrame({"Close": close_b})}}
    result = pi.factor_tilt(held, {"A": 60.0, "B": 40.0}, {"F": factor_f}, min_overlap_days=20)
    posA = next(p for p in result["positions"] if p["ticker"] == "A")
    assert posA["correlations"]["F"] is not None


# ─── factor_tilt — dominant_factor picks largest ABSOLUTE correlation ────────
# — plus portfolio_tilt renormalization among only the valid-correlation subset

def _dominant_and_renorm_fixture():
    idx_a = pd.bdate_range("2024-01-02", periods=25)
    idx_b = pd.bdate_range("2024-04-01", periods=25)  # disjoint from idx_a

    rA = np.linspace(0.001, 0.02, 25)
    rB = np.linspace(-0.01, 0.015, 25)
    factor_a = pd.Series(rA, index=idx_a)
    factor_b = pd.Series(rB, index=idx_b)

    # POS1: only overlaps factor A's dates. returns = 2*rA -> corr(A) = +1.0.
    # No data at all on idx_b -> corr(B) = None (zero overlap).
    close1 = _close_series_from_returns(2 * rA, "2024-01-02")

    # POS2: overlaps BOTH factor date ranges (idx_a then idx_b back-to-back).
    # returns over idx_a = -1*rA -> corr(A) = -1.0 (strong negative).
    # returns over idx_b = a distinct, non-scalar-multiple pattern -> some
    # correlation with |corr| < 1.0 (weaker than A's).
    r2b = np.sin(np.linspace(0, 3.0, 25)) * 0.01
    returns2 = np.concatenate([-1 * rA, r2b])
    idx2 = idx_a.append(idx_b)
    prepend = idx2[0] - pd.tseries.offsets.BDay(1)
    full_idx2 = pd.DatetimeIndex([prepend]).append(idx2)
    prices2 = 100.0 * np.cumprod(1 + np.concatenate([[0.0], returns2]))
    close2 = pd.Series(prices2, index=full_idx2)

    held = {
        "POS1": {"df": pd.DataFrame({"Close": close1})},
        "POS2": {"df": pd.DataFrame({"Close": close2})},
    }
    weights = {"POS1": 60.0, "POS2": 40.0}
    factor_returns = {"A": factor_a, "B": factor_b}
    return held, weights, factor_returns


def test_factor_tilt_dominant_factor_picks_strong_negative_over_weak_positive():
    held, weights, factor_returns = _dominant_and_renorm_fixture()
    result = pi.factor_tilt(held, weights, factor_returns, min_overlap_days=20)

    pos2 = next(p for p in result["positions"] if p["ticker"] == "POS2")
    assert pos2["correlations"]["A"] == pytest.approx(-1.0, abs=0.01)
    assert abs(pos2["correlations"]["B"]) < 0.99
    assert pos2["dominant_factor"] == "A"
    assert pos2["dominant_corr"] == pytest.approx(-1.0, abs=0.01)


def test_factor_tilt_position_lacking_one_factor_dropped_from_that_factors_tilt_math():
    held, weights, factor_returns = _dominant_and_renorm_fixture()
    result = pi.factor_tilt(held, weights, factor_returns, min_overlap_days=20)

    pos1 = next(p for p in result["positions"] if p["ticker"] == "POS1")
    pos2 = next(p for p in result["positions"] if p["ticker"] == "POS2")
    assert pos1["correlations"]["B"] is None  # zero overlap -> dropped for B

    # Factor A: both positions contribute -> weighted avg of +1.0 (w=60) and
    # -1.0 (w=40) == (60 - 40) / 100 == 0.2.
    assert result["portfolio_tilt"]["A"] == pytest.approx(0.2, abs=0.02)

    # Factor B: ONLY POS2 has a valid correlation -- portfolio_tilt["B"] must
    # equal POS2's own correlation exactly (not diluted by POS1's implicit
    # absence, which would otherwise drag it toward 0).
    assert result["portfolio_tilt"]["B"] == pytest.approx(pos2["correlations"]["B"], abs=1e-9)


def test_factor_tilt_sort_order_by_weight_pct_desc():
    held, weights, factor_returns = _dominant_and_renorm_fixture()
    result = pi.factor_tilt(held, weights, factor_returns, min_overlap_days=20)
    assert result["positions"][0]["ticker"] == "POS1"  # weight 60
    assert result["positions"][1]["ticker"] == "POS2"  # weight 40


# ─── factor_tilt — position with ALL-None correlations dropped ──────────────

def test_factor_tilt_position_with_zero_overlap_on_every_factor_is_dropped():
    held, weights, factor_returns = _dominant_and_renorm_fixture()
    # POS3 lives entirely outside both factors' date ranges.
    idx3 = pd.bdate_range("2025-01-02", periods=25)
    r3 = np.linspace(0.002, 0.01, 25)
    close3 = _close_series_from_returns(r3, "2025-01-02")
    held["POS3"] = {"df": pd.DataFrame({"Close": close3})}
    weights["POS3"] = 10.0

    result = pi.factor_tilt(held, weights, factor_returns, min_overlap_days=20)
    tickers_out = [p["ticker"] for p in result["positions"]]
    assert "POS3" not in tickers_out
    assert result["n_included"] == 2


def test_factor_tilt_all_positions_dropped_returns_empty_shape():
    idx_a = pd.bdate_range("2024-01-02", periods=25)
    factor_a = pd.Series(np.linspace(0.001, 0.02, 25), index=idx_a)

    # Both tickers live entirely outside factor A's date range.
    idx_x = pd.bdate_range("2025-06-01", periods=25)
    close_x = _close_series_from_returns(np.linspace(0.001, 0.01, 25), "2025-06-01")
    close_y = _close_series_from_returns(np.linspace(0.002, 0.015, 25), "2025-06-01")

    held = {"X": {"df": pd.DataFrame({"Close": close_x})},
            "Y": {"df": pd.DataFrame({"Close": close_y})}}
    result = pi.factor_tilt(held, {"X": 50.0, "Y": 50.0}, {"A": factor_a}, min_overlap_days=20)
    assert result == {"positions": [], "portfolio_tilt": {}, "n_included": 0}
