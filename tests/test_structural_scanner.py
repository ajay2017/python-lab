"""Regression tests for stock_analyzer/structural_scanner.py.

blast_radius() shipped 2026-07-24 (Phase 1) with zero test coverage -- its own
Opus pre-ship review caught a real 100x weight-scaling bug (weight_pct and
comove_pct are both percent-scaled; the sum must normalize weight to a
fraction first) before ship, which is exactly the class of regression a test
should pin so it can't silently return. detect_new_clusters() is new (Phase
2, not yet built as of this test file) -- its own design went through 3 Opus
plan-review rounds, two of which caught real fabrication-risk bugs in the
"new_pairs" citation logic (a transitive pair cited as if directly correlated,
then a signed-vs-abs() correlation bug in the fix for that): see
docs/plans/structural-scanner-phase2.md's Review log. These tests pin both
bugs' *fixed* behavior directly so neither class of bug can silently return.
"""
import numpy as np
import pandas as pd

from stock_analyzer import structural_scanner
from stock_analyzer.structural_scanner import blast_radius, detect_new_clusters


def _corr_df(tickers, pairs):
    """Square, symmetric correlation DataFrame. `pairs` is {(a, b): corr};
    the diagonal is always 1.0, unspecified off-diagonal pairs default to 0.0.
    """
    df = pd.DataFrame(0.0, index=tickers, columns=tickers)
    for t in tickers:
        df.loc[t, t] = 1.0
    for (a, b), c in pairs.items():
        df.loc[a, b] = c
        df.loc[b, a] = c
    return df


def _rb_position(ticker, weight_pct, vol_annualized_pct, risk_pct=0.0):
    return {"ticker": ticker, "weight_pct": weight_pct, "vol_annualized_pct": vol_annualized_pct, "risk_pct": risk_pct}


def _cluster(tickers, combined_weight_pct=10.0, avg_internal_corr=0.7, tier="warning"):
    return {
        "tickers": sorted(tickers),
        "size": len(tickers),
        "avg_internal_corr": avg_internal_corr,
        "combined_weight_pct": combined_weight_pct,
        "tier": tier,
    }


# ── blast_radius ──────────────────────────────────────────────────────────────

def test_blast_radius_empty_corr_df_returns_empty():
    assert blast_radius(pd.DataFrame(), [_rb_position("AAPL", 10.0, 20.0)]) == []
    assert blast_radius(None, [_rb_position("AAPL", 10.0, 20.0)]) == []


def test_blast_radius_no_positions_returns_empty():
    df = _corr_df(["AAPL"], {})
    assert blast_radius(df, []) == []
    assert blast_radius(df, None) == []


def test_blast_radius_weight_normalized_not_100x_overstated():
    # Single shocked ticker, no correlated peers -> portfolio_impact_pct is
    # purely its own weight fraction * shock. An 8%-weight name shocked -20%
    # must contribute -1.6%, NOT -160% (the exact bug Phase 1's pre-ship
    # review caught before ship).
    df = _corr_df(["AAPL"], {})
    positions = [_rb_position("AAPL", weight_pct=8.0, vol_annualized_pct=20.0, risk_pct=50.0)]
    result = blast_radius(df, positions, shock_pct=-20.0)
    assert len(result) == 1
    assert result[0]["portfolio_impact_pct"] == -1.6
    assert result[0]["contributing_tickers"] == []


def test_blast_radius_cascades_via_correlated_peer_signed_comove():
    # AAPL shocked -20%; MSFT is strongly positively correlated (0.9 >= 0.65)
    # and has double AAPL's volatility -> beta = 0.9 * (30/15) = 1.8,
    # comove = -20 * 1.8 = -36%, contributing (10/100)*-36 = -3.6 to the -1.6
    # from AAPL itself.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    positions = [
        _rb_position("AAPL", weight_pct=8.0, vol_annualized_pct=15.0, risk_pct=50.0),
        _rb_position("MSFT", weight_pct=10.0, vol_annualized_pct=30.0, risk_pct=20.0),
    ]
    result = blast_radius(df, positions, shock_pct=-20.0, top_n=1)  # only shock AAPL
    assert len(result) == 1
    b = result[0]
    assert b["shocked_ticker"] == "AAPL"
    assert b["portfolio_impact_pct"] == -5.2  # -1.6 (AAPL) + -3.6 (MSFT cascade)
    assert b["contributing_tickers"] == [{"ticker": "MSFT", "corr": 0.9, "comove_pct": -36.0}]


def test_blast_radius_uses_signed_correlation_negative_is_hedge_offset():
    # A strong NEGATIVE correlation is a real hedge -- comove_pct is negative
    # * negative shock = a POSITIVE offset, deliberately different from
    # detect_new_clusters' cluster-formation gate (which is positive-only).
    df = _corr_df(["AAPL", "GLD"], {("AAPL", "GLD"): -0.9})
    positions = [
        _rb_position("AAPL", weight_pct=8.0, vol_annualized_pct=15.0, risk_pct=50.0),
        _rb_position("GLD", weight_pct=10.0, vol_annualized_pct=15.0, risk_pct=20.0),
    ]
    result = blast_radius(df, positions, shock_pct=-20.0)
    b = result[0]
    # beta = -0.9 * (15/15) = -0.9; comove = -20 * -0.9 = +18 (an offsetting move)
    assert b["contributing_tickers"] == [{"ticker": "GLD", "corr": -0.9, "comove_pct": 18.0}]
    assert b["portfolio_impact_pct"] == 0.2  # -1.6 (AAPL) + 1.8 (GLD offset, (10/100)*18)


def test_blast_radius_below_threshold_peer_does_not_cascade():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.5})  # below 0.65
    positions = [
        _rb_position("AAPL", weight_pct=8.0, vol_annualized_pct=15.0, risk_pct=50.0),
        _rb_position("MSFT", weight_pct=10.0, vol_annualized_pct=30.0, risk_pct=20.0),
    ]
    result = blast_radius(df, positions, shock_pct=-20.0)
    assert result[0]["contributing_tickers"] == []
    assert result[0]["portfolio_impact_pct"] == -1.6  # only AAPL's own weight


def test_blast_radius_only_shocks_top_n_by_risk_pct_order():
    # risk_budget_positions is pre-sorted by risk_pct descending (house
    # convention) -- blast_radius shocks the first top_n as given, doesn't
    # re-sort.
    df = _corr_df(["A", "B", "C", "D"], {})
    positions = [
        _rb_position("A", 10.0, 20.0, risk_pct=40.0),
        _rb_position("B", 10.0, 20.0, risk_pct=30.0),
        _rb_position("C", 10.0, 20.0, risk_pct=20.0),
        _rb_position("D", 10.0, 20.0, risk_pct=10.0),
    ]
    result = blast_radius(df, positions, top_n=3)
    assert [b["shocked_ticker"] for b in result] == ["A", "B", "C"]


def test_blast_radius_missing_or_zero_vol_skips_cascade_not_whole_result():
    # vol_T missing/zero/NaN -> still returns the shocked ticker's own-weight
    # impact, just with no cascade (never crashes, never silently drops it).
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    positions = [
        _rb_position("AAPL", weight_pct=8.0, vol_annualized_pct=0.0, risk_pct=50.0),
        _rb_position("MSFT", weight_pct=10.0, vol_annualized_pct=30.0, risk_pct=20.0),
    ]
    result = blast_radius(df, positions, shock_pct=-20.0)
    assert result[0]["portfolio_impact_pct"] == -1.6
    assert result[0]["contributing_tickers"] == []


def test_blast_radius_never_raises_on_malformed_input():
    assert blast_radius("not a df", [_rb_position("AAPL", 10.0, 20.0)]) == []
    assert blast_radius(_corr_df(["AAPL"], {}), [{"ticker": None}]) == []
    assert blast_radius(_corr_df(["AAPL"], {}), [{"weight_pct": 10.0}]) == []  # no ticker key


# ── detect_new_clusters ────────────────────────────────────────────────────────

def test_detect_new_clusters_no_prior_scan_suppresses_everything():
    # prior_cluster_snapshot is None (no scan has EVER run) -- nothing to
    # diff against, so nothing is flagged. A first-ever comparison treating
    # everything as "new" would be a false-positive flood, not a real finding.
    today = [_cluster(["AAPL", "MSFT"])]
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    assert detect_new_clusters(today, None, df) == []


def test_detect_new_clusters_empty_prior_snapshot_flags_all_real_clusters():
    # prior_cluster_snapshot == [] means a REAL scan ran and found zero
    # clusters that day -- this is NOT the same as None, and must flag
    # today's genuinely new clusters (the cleanest possible new-formation
    # signal: zero clusters -> some clusters).
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    today = [_cluster(["AAPL", "MSFT"])]
    result = detect_new_clusters(today, [], df)
    assert len(result) == 1
    assert result[0]["new_pairs"] == [["AAPL", "MSFT"]]


def test_detect_new_clusters_no_today_clusters_returns_empty():
    assert detect_new_clusters([], [_cluster(["AAPL", "MSFT"])], _corr_df(["AAPL", "MSFT"], {})) == []
    assert detect_new_clusters(None, [_cluster(["AAPL", "MSFT"])], _corr_df(["AAPL", "MSFT"], {})) == []


def test_detect_new_clusters_no_corr_df_returns_empty():
    today = [_cluster(["AAPL", "MSFT"])]
    assert detect_new_clusters(today, [], None) == []
    assert detect_new_clusters(today, [], pd.DataFrame()) == []


def test_detect_new_clusters_already_clustered_pair_is_not_new():
    # Same pair, same membership as the prior snapshot -- no new_pairs, not
    # flagged at all, even though it's still >= threshold today.
    prior = [_cluster(["AAPL", "MSFT"])]
    today = [_cluster(["AAPL", "MSFT"])]
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    assert detect_new_clusters(today, prior, df) == []


def test_detect_new_clusters_new_member_joining_existing_cluster_cites_real_edge():
    # AAPL-MSFT already clustered before. Today GOOGL joins via a real direct
    # edge to MSFT (0.7 >= 0.65). AAPL-GOOGL is only a transitive link (never
    # itself checked/correlated in this corr_df -- defaults to 0.0) and must
    # NOT be cited; only the real MSFT-GOOGL edge should appear.
    prior = [_cluster(["AAPL", "MSFT"])]
    today = [_cluster(["AAPL", "MSFT", "GOOGL"])]
    df = _corr_df(["AAPL", "MSFT", "GOOGL"], {("AAPL", "MSFT"): 0.9, ("MSFT", "GOOGL"): 0.7})
    result = detect_new_clusters(today, prior, df)
    assert len(result) == 1
    assert result[0]["new_pairs"] == [["GOOGL", "MSFT"]]  # NOT AAPL-GOOGL


def test_detect_new_clusters_never_cites_a_pair_below_threshold_today():
    # GOOGL is grouped with AAPL/MSFT in today's cluster input (as the caller
    # would only do if correlation_clusters() itself found a real edge
    # somewhere), but if this function is ever handed a GOOGL-MSFT pair that
    # doesn't actually clear the threshold in corr_df, it must not fabricate
    # a citation for it.
    prior = [_cluster(["AAPL", "MSFT"])]
    today = [_cluster(["AAPL", "MSFT", "GOOGL"])]
    df = _corr_df(["AAPL", "MSFT", "GOOGL"], {("AAPL", "MSFT"): 0.9, ("MSFT", "GOOGL"): 0.5})  # below threshold
    result = detect_new_clusters(today, prior, df)
    assert result == []  # no verifiable new direct edge -> not flagged at all


def test_detect_new_clusters_never_cites_anti_correlated_pair_as_new_pairing():
    # Round 2 Opus review finding: A-C only linked transitively (via B), and
    # is STRONGLY NEGATIVELY correlated (-0.9). abs(-0.9) >= threshold would
    # wrongly pass an abs()-based check -- the fixed, signed check must
    # exclude it, since two names moving OPPOSITE each other is not a
    # co-movement "pairing" to cite as new risk formation.
    prior = [_cluster(["A", "B"])]
    today = [_cluster(["A", "B", "C"])]
    df = _corr_df(["A", "B", "C"], {("A", "B"): 0.9, ("B", "C"): 0.7, ("A", "C"): -0.9})
    result = detect_new_clusters(today, prior, df)
    assert len(result) == 1
    assert result[0]["new_pairs"] == [["B", "C"]]  # real positive edge only, never A-C


def test_detect_new_clusters_nan_correlation_skipped_not_raised():
    prior = []
    today = [_cluster(["AAPL", "MSFT"])]
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): np.nan})
    assert detect_new_clusters(today, prior, df) == []


def test_detect_new_clusters_never_raises_on_malformed_input():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    assert detect_new_clusters([{"tickers": None}], [], df) == []
    assert detect_new_clusters("not a list", [], df) == []
    assert detect_new_clusters([_cluster(["AAPL", "MSFT"])], "not a list", df) == []


from stock_analyzer.util import factor_tilt_evidence_line  # noqa: E402


class TestFactorTiltAlwaysDisclosedInNarrativeInputs:
    """F-260 (2026-08-28) — twin of the regime-scenario case. This narrative is
    also persisted (structural_scan_cache, one row per scan_date). Factor Tilt
    lives on the SAME page as this tab but behind its own button, so it is
    still commonly unloaded when the narrative is generated.
    """

    def _evidence(self, factor):
        return structural_scanner.build_narrative_inputs([], [], [], factor)

    def test_factor_line_present_in_all_three_states(self):
        for factor in (None,
                       {"positions": [], "portfolio_tilt": {}, "n_included": 0},
                       {"portfolio_tilt": {"QUAL": -0.66}}):
            text = structural_scanner._format_evidence(self._evidence(factor))
            assert "Factor tilt:" in text, f"no factor line for {factor!r}"
            # Pin the CONTRACT, not the substring: the two consumers must emit
            # the shared helper's exact line. A re-inlined near-copy that
            # dropped the forbid-inference clause would pass the check above.
            assert text.endswith(factor_tilt_evidence_line(factor))

    def test_absent_factor_data_is_stated_not_omitted(self):
        assert "NOT MEASURED" in structural_scanner._format_evidence(self._evidence(None))

    def test_system_prompt_no_longer_assumes_the_line_can_be_absent(self):
        """The old instruction was 'do not mention factor exposure if it isn't
        supplied' — now it is ALWAYS supplied, so that wording would have been
        unreachable guidance pointing at a state that no longer occurs."""
        sys_prompt = structural_scanner._NARRATIVE_SYSTEM
        assert "do not mention factor exposure if it isn't supplied" not in sys_prompt
        assert "NOT MEASURED" in sys_prompt
        assert "never treat its absence" in sys_prompt
