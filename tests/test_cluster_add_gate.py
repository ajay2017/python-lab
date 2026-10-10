"""Tests for stock_analyzer.cluster_add_gate (G-25, docs/plans/cluster-add-gate.md).

Three-state contract throughout — None=couldn't check, []/{}=checked clean,
populated=firing. Every test below is pinned against that contract directly
rather than inferring it from a single happy-path case.
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_analyzer.cluster_add_gate import (
    baseline_signature,
    resolve_new_clusters,
    add_block_map,
)
from stock_analyzer.portfolio import (
    correlation_unchecked,
    correlation_matrix,
    correlation_coverage,
    diversification_score,
    CORR_MIN_OBS_TRUSTED,
)
from stock_analyzer import portfolio as _portfolio_mod
from stock_analyzer.constants import CORR_HIGH_PAIRS_THRESHOLD

pytestmark = pytest.mark.fast


def _corr_df(tickers, pairs):
    df = pd.DataFrame(0.0, index=tickers, columns=tickers)
    for t in tickers:
        df.loc[t, t] = 1.0
    for (a, b), c in pairs.items():
        df.loc[a, b] = c
        df.loc[b, a] = c
    return df


def _cluster(tickers, new_pairs, combined_weight_pct=10.0, tier="warning"):
    return {
        "tickers": sorted(tickers),
        "size": len(tickers),
        "avg_internal_corr": 0.7,
        "combined_weight_pct": combined_weight_pct,
        "tier": tier,
        "new_pairs": new_pairs,
    }


# ── baseline_signature ────────────────────────────────────────────────────────

def test_baseline_signature_offline_on_none():
    assert baseline_signature(None) == ("offline",)


def test_baseline_signature_none_status():
    assert baseline_signature({"status": "none"}) == ("none",)


def test_baseline_signature_ok_keyed_on_scan_date():
    assert baseline_signature(
        {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []}
    ) == ("ok", "2026-10-01")
    # A different scan_date is a genuinely different signature — this is what
    # lets a fresh Structural Scan invalidate app.py's _synth_sig memo.
    assert (
        baseline_signature({"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []})
        != baseline_signature({"status": "ok", "scan_date": "2026-10-02", "cluster_snapshot": []})
    )


def test_baseline_signature_malformed_shape_fails_safe_to_offline():
    assert baseline_signature({"status": "weird"}) == ("offline",)
    assert baseline_signature({}) == ("offline",)


# ── resolve_new_clusters ──────────────────────────────────────────────────────

def test_resolve_new_clusters_none_when_corr_df_missing():
    assert resolve_new_clusters(None, {}, {"status": "none"}) is None
    assert resolve_new_clusters(pd.DataFrame(), {}, {"status": "none"}) is None


def test_resolve_new_clusters_none_when_baseline_state_none():
    # D4: baseline_state is None means "couldn't check" -- must flow through
    # as None, never silently become [].
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    assert resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, None) is None


def test_resolve_new_clusters_none_when_baseline_state_not_a_dict():
    # Reviewer round-1 non-blocking finding: a non-dict baseline_state must
    # return None (couldn't check), never fall through toward a [] collapse.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    assert resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, "not a dict") is None
    assert resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, []) is None


def test_resolve_new_clusters_none_when_status_is_unrecognized():
    # A dict with neither "none" nor "ok" as status is malformed -- treat as
    # couldn't-check, not as an implicit "ok" that reads a missing
    # cluster_snapshot as "nothing to diff against" (a false []).
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    assert resolve_new_clusters(
        df, {"AAPL": 10.0, "MSFT": 10.0}, {"status": "weird"}
    ) is None
    assert resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, {}) is None


def test_resolve_new_clusters_empty_list_when_baseline_status_none():
    # A real scan has never run -- nothing to diff against, but this is a
    # KNOWN state (checked), not an outage. Still returns [] for a clean book
    # or a real set of flagged clusters if the live book has new pairs.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    result = resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, {"status": "none"})
    assert result == []


def test_resolve_new_clusters_fires_on_real_new_pair():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    baseline = {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []}
    result = resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, baseline)
    assert len(result) == 1
    assert result[0]["new_pairs"] == [["AAPL", "MSFT"]]


def test_resolve_new_clusters_boundary_exactly_at_threshold_is_new():
    # Signed >=, matching detect_new_clusters_strict's own convention.
    # A real prior scan that found nothing ("ok" + empty snapshot), NOT
    # "status": "none" -- that status means no scan ever ran at all, which
    # resolve_new_clusters short-circuits to [] unconditionally (see the test
    # below) regardless of what corr_df shows, since there's nothing to diff.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): CORR_HIGH_PAIRS_THRESHOLD})
    baseline = {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []}
    result = resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, baseline)
    assert len(result) == 1
    assert result[0]["new_pairs"] == [["AAPL", "MSFT"]]


def test_resolve_new_clusters_just_below_threshold_not_new():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): CORR_HIGH_PAIRS_THRESHOLD - 0.0001})
    baseline = {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []}
    result = resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, baseline)
    assert result == []


def test_resolve_new_clusters_negative_correlation_never_fires():
    # A strongly NEGATIVE correlation must never be treated as "new" via an
    # abs()-style check -- this must be a SIGNED comparison throughout.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): -0.70})
    baseline = {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []}
    result = resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, baseline)
    assert result == []


def test_resolve_new_clusters_status_none_shortcircuits_regardless_of_corr_df():
    # "status": "none" means NO SCAN HAS EVER RUN -- nothing to diff against,
    # so this must return [] unconditionally, even when corr_df shows a pair
    # that would otherwise qualify as new. Same "first-ever comparison is not
    # everything is new" rule detect_new_clusters_strict itself applies when
    # handed prior_cluster_snapshot=None.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.95})
    result = resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, {"status": "none"})
    assert result == []


def test_resolve_new_clusters_already_acknowledged_pair_not_flagged():
    # The pair is already present in the baseline snapshot -- not new.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    baseline = {
        "status": "ok", "scan_date": "2026-10-01",
        "cluster_snapshot": [{"tickers": ["AAPL", "MSFT"]}],
    }
    result = resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, baseline)
    assert result == []


def test_resolve_new_clusters_none_on_malformed_baseline_snapshot():
    # A crash inside detect_new_clusters_strict must surface as "couldn't
    # check" (None), never as "checked clean" ([]).
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    baseline = {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": "not a list"}
    assert resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0}, baseline) is None


# ── add_block_map ─────────────────────────────────────────────────────────────

def test_add_block_map_none_straight_through():
    assert add_block_map(None, _corr_df(["AAPL"], {}), ["AAPL"], "2026-10-01") is None


def test_add_block_map_empty_when_no_new_clusters():
    assert add_block_map([], _corr_df(["AAPL"], {}), ["AAPL"], "2026-10-01") == {}


def test_add_block_map_blocks_both_held_endpoints():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    result = add_block_map(clusters, df, ["AAPL", "MSFT"], "2026-10-01")
    assert set(result.keys()) == {"AAPL", "MSFT"}
    assert result["AAPL"]["partners"] == [("MSFT", 0.9)]
    assert result["MSFT"]["partners"] == [("AAPL", 0.9)]
    assert result["AAPL"]["max_new_corr"] == 0.9
    assert result["AAPL"]["baseline_scan_date"] == "2026-10-01"
    assert result["AAPL"]["tier"] == "warning"


def test_add_block_map_d2_scope_excludes_transitive_only_member():
    # D2: only the tickers that are themselves endpoints of a verified new
    # pair are blocked -- GOOGL is a cluster member but not on the new_pairs
    # list, so it must NOT be blocked even though it's held.
    df = _corr_df(["AAPL", "MSFT", "GOOGL"], {("AAPL", "MSFT"): 0.9, ("MSFT", "GOOGL"): 0.5})
    clusters = [_cluster(["AAPL", "MSFT", "GOOGL"], [["AAPL", "MSFT"]])]
    result = add_block_map(clusters, df, ["AAPL", "MSFT", "GOOGL"], "2026-10-01")
    assert set(result.keys()) == {"AAPL", "MSFT"}
    assert "GOOGL" not in result


def test_add_block_map_excludes_not_held_endpoint():
    # A new-pair endpoint that is NOT currently held must never be blocked --
    # this gate only touches ADD suggestions on names already in the book.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    result = add_block_map(clusters, df, ["AAPL"], "2026-10-01")  # MSFT not held
    assert set(result.keys()) == {"AAPL"}
    assert "MSFT" not in result


def test_add_block_map_neither_held_yields_empty_dict():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    result = add_block_map(clusters, df, [], "2026-10-01")
    assert result == {}


def test_add_block_map_max_new_corr_across_multiple_partners():
    df = _corr_df(["AAPL", "MSFT", "GOOGL"], {("AAPL", "MSFT"): 0.70, ("AAPL", "GOOGL"): 0.90})
    clusters = [_cluster(["AAPL", "MSFT", "GOOGL"], [["AAPL", "MSFT"], ["AAPL", "GOOGL"]])]
    result = add_block_map(clusters, df, ["AAPL", "MSFT", "GOOGL"], "2026-10-01")
    assert result["AAPL"]["max_new_corr"] == 0.90
    assert set(p for p, _ in result["AAPL"]["partners"]) == {"MSFT", "GOOGL"}


def test_add_block_map_malformed_pair_skipped_not_raised():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL"]])]  # malformed pair (len 1)
    result = add_block_map(clusters, df, ["AAPL", "MSFT"], "2026-10-01")
    assert result == {}


def test_add_block_map_never_raises_on_bad_corr_df():
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    # corr_df missing the tickers entirely -- .loc[] raises internally, must
    # degrade to {} (not None -- new_clusters itself was not None).
    result = add_block_map(clusters, pd.DataFrame(), ["AAPL", "MSFT"], "2026-10-01")
    assert result == {}


# ── G-25 defect regression (disclosure-only follow-on) ───────────────────────
# docs/plans/pair-add-gate.md's fail-open blind spot: an unpriceable MSFT
# never enters corr_df, so the AAPL-MSFT new pairing can never be detected --
# add_block_map correctly degrades to {} (fail-open), and
# correlation_unchecked is what actually names the gap for the render-layer
# disclosure.

def test_g25_defect_regression_all_priced_fires_on_new_pair():
    df = _corr_df(["AAPL", "MSFT", "GOOGL"], {("AAPL", "MSFT"): 0.9})
    baseline = {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []}
    clusters = resolve_new_clusters(df, {"AAPL": 10.0, "MSFT": 10.0, "GOOGL": 10.0}, baseline)
    assert clusters is not None
    result = add_block_map(clusters, df, ["AAPL", "MSFT", "GOOGL"], "2026-10-01")
    assert set(result.keys()) == {"AAPL", "MSFT"}
    assert correlation_unchecked(df, ["AAPL", "MSFT", "GOOGL"]) == []


def test_g25_defect_regression_msft_unpriced_fails_open_and_is_disclosed():
    # MSFT never entered corr_df this run (unpriceable) -- the new pairing
    # with AAPL can never be detected, so add_block_map correctly degrades to
    # {} rather than fabricating a block.
    df = _corr_df(["AAPL", "GOOGL"], {})  # MSFT absent entirely
    baseline = {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []}
    clusters = resolve_new_clusters(df, {"AAPL": 10.0, "GOOGL": 10.0}, baseline)
    assert clusters is not None
    result = add_block_map(clusters, df, ["AAPL", "MSFT", "GOOGL"], "2026-10-01")
    assert result == {}, "contract unchanged -- fail-open, never a fabricated block"
    assert correlation_unchecked(df, ["AAPL", "MSFT", "GOOGL"]) == ["MSFT"]


# ── add_block_map: corr_coverage thin-sample guard (disclosure-only) ────────
# An entirely-NaN correlation matrix (a held ticker with a short/non-
# overlapping history collapses the listwise intersection) must not let this
# gate assert "no new cluster" from zero measured observations. Nothing below
# may change which tickers are suppressed -- only whether the gate returns
# None (couldn't check) instead of a fabricated {} -- an EMPTY map only.
# A map that FIRED is always returned untouched.

def _hist(n, start=100.0, step=1.0):
    import pandas as pd
    return pd.DataFrame({"Close": [start + step * i for i in range(n)]})


def test_corr_coverage_omitted_is_byte_identical_to_before():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    omitted = add_block_map(clusters, df, ["AAPL", "MSFT"], "2026-10-01")
    explicit_none = add_block_map(clusters, df, ["AAPL", "MSFT"], "2026-10-01", corr_coverage=None)
    assert set(omitted.keys()) == {"AAPL", "MSFT"}
    assert omitted == explicit_none


def test_corr_coverage_thin_sample_NEVER_discards_a_real_block():
    """THE regression guard — this asserted the OPPOSITE until an Opus
    review caught it (2026-10-09). See the pair_add_gate sibling's docstring
    for the full reasoning: a thin sample must never lift a live pause,
    because between 2 and 19 overlapping observations the matrix holds REAL
    correlations and a cluster can genuinely be detected.
    """
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    control = add_block_map(clusters, df, ["AAPL", "MSFT"], "2026-10-01")
    assert set(control.keys()) == {"AAPL", "MSFT"}  # control genuinely blocks

    thin = add_block_map(
        clusters, df, ["AAPL", "MSFT"], "2026-10-01",
        corr_coverage={"n_obs": CORR_MIN_OBS_TRUSTED - 1},
    )
    assert thin == control, (
        "a thin sample discarded a live suppression -- that is a gate being "
        "switched off by a short price history, not a disclosure change."
    )


def test_corr_coverage_thin_sample_downgrades_an_EMPTY_map_to_none():
    # Nothing held is on a new pair's endpoints, so the map is legitimately
    # empty -- the only case where "checked, clean" and "couldn't measure"
    # are indistinguishable.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    assert add_block_map(clusters, df, ["ZZZ"], "2026-10-01") == {}  # control
    thin = add_block_map(clusters, df, ["ZZZ"], "2026-10-01",
                         corr_coverage={"n_obs": CORR_MIN_OBS_TRUSTED - 1})
    assert thin is None


def test_corr_coverage_boundary_at_floor_is_normal_behaviour():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    result = add_block_map(clusters, df, ["ZZZ"], "2026-10-01",
                            corr_coverage={"n_obs": CORR_MIN_OBS_TRUSTED})
    assert result == {}


def test_corr_coverage_boundary_one_below_floor_is_none():
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    result = add_block_map(clusters, df, ["ZZZ"], "2026-10-01",
                            corr_coverage={"n_obs": CORR_MIN_OBS_TRUSTED - 1})
    assert result is None


def test_corr_coverage_n_obs_zero_is_none():
    # The real all-NaN-matrix case: n_obs == 0.
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    assert add_block_map(clusters, df, ["ZZZ"], "2026-10-01") == {}  # control
    result = add_block_map(clusters, df, ["ZZZ"], "2026-10-01",
                            corr_coverage={"n_obs": 0})
    assert result is None


def test_corr_coverage_type_guards_are_load_bearing():
    # At the REAL floor (CORR_MIN_OBS_TRUSTED == 20): True compares equal to
    # 1, which IS below 20, so if the explicit bool guard were ever deleted
    # and True silently treated as the int 1, this would wrongly degrade to
    # None (thin). The guard must reject it outright instead, leaving the
    # empty map as {} -- no floor manipulation needed to prove this,
    # unlike the inverted `>=` check this mirrors (correlation_claim_verified,
    # portfolio.py), where True==1 already fails that floor either way.
    # MUST use an EMPTY-map fixture (nothing held on a new pair's endpoint).
    # Against a firing fixture these assertions are vacuous -- since the fix
    # a non-empty map is never downgraded, so deleting either guard changes
    # nothing. An Opus review proved exactly that by mutation (2026-10-09).
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    assert add_block_map(clusters, df, ["ZZZ"], "2026-10-01") == {}   # control

    bool_result = add_block_map(clusters, df, ["ZZZ"], "2026-10-01",
                                 corr_coverage={"n_obs": True})
    assert bool_result == {}, (
        "a bool n_obs must never trigger the thin-sample guard -- True == 1, "
        "which IS below the floor of 20, so deleting the bool guard would "
        "turn this into None"
    )

    # A numeric-looking string would raise TypeError on a bare `<` against an
    # int if the int-type guard were deleted; the helper sits inside the try,
    # so that raise would surface as None rather than {}.
    str_result = add_block_map(clusters, df, ["ZZZ"], "2026-10-01",
                                corr_coverage={"n_obs": "125"})
    assert str_result == {}


def test_corr_coverage_floor_comparison_is_strict_less_than(monkeypatch):
    # Companion to the boundary tests above, using a monkeypatched floor of 1
    # so the comparison itself (not a type guard) is what's under test: a
    # real n_obs sitting EXACTLY at a low floor must clear it (strict `<`,
    # never `<=`) -- repeated here at the opposite extreme from the real
    # floor so a `<=` mutation can't hide at one particular floor value.
    monkeypatch.setattr(_portfolio_mod, "CORR_MIN_OBS_TRUSTED", 1)
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    result = add_block_map(clusters, df, ["AAPL", "MSFT"], "2026-10-01",
                            corr_coverage={"n_obs": 1})
    assert set(result.keys()) == {"AAPL", "MSFT"}, "n_obs==floor must clear it, not be thin"


@pytest.mark.parametrize("bad_coverage", [
    None, {}, [], "x", {"n_obs": None}, {"n_obs": float("nan")}, {"no_n_obs_key": 1},
])
def test_corr_coverage_malformed_never_disables_the_gate(bad_coverage):
    # Empty-map fixture, same reason as the type-guard test above: a firing
    # map can no longer be downgraded, so only an empty one can detect a
    # mishandled malformed value (it would surface as None, not {}).
    df = _corr_df(["AAPL", "MSFT"], {("AAPL", "MSFT"): 0.9})
    clusters = [_cluster(["AAPL", "MSFT"], [["AAPL", "MSFT"]])]
    assert add_block_map(clusters, df, ["ZZZ"], "2026-10-01") == {}   # control
    result = add_block_map(clusters, df, ["ZZZ"], "2026-10-01", corr_coverage=bad_coverage)
    assert result == {}


def test_corr_coverage_end_to_end_all_nan_matrix_degrades_to_none():
    """Reproduces the real hazard in the venv through the REAL
    correlation_matrix/correlation_coverage/diversification_score chain (not
    a hand-built corr_df): AAA/BBB at 60 bars, CCC at 1 bar. The listwise
    intersection collapses to zero shared observations, yet the matrix is
    non-empty -- a real production render could still feed it into
    resolve_new_clusters/add_block_map built from zero data.
    """
    held = {"AAA": {"df": _hist(60)}, "BBB": {"df": _hist(60)}, "CCC": {"df": _hist(1)}}
    held_tickers = ["AAA", "BBB", "CCC"]
    corr = correlation_matrix(held)
    cov = correlation_coverage(held)
    div = diversification_score(corr)

    # The hazard is real before asserting the fix.
    assert not corr.empty
    assert cov["n_obs"] == 0
    assert div["risk_pairs"] == []

    baseline = {"status": "ok", "scan_date": "2026-10-01", "cluster_snapshot": []}
    clusters = resolve_new_clusters(corr, {"AAA": 10.0, "BBB": 10.0, "CCC": 10.0}, baseline)
    # Every pairwise corr is NaN, so no cluster forms regardless -- but the
    # gate must ALSO independently refuse to assert "checked, clean" from
    # this coverage, which is what this test actually pins.
    result = add_block_map(clusters, corr, held_tickers, "2026-10-01", corr_coverage=cov)
    assert result is None

    # Control: a real, comfortably-above-floor, genuinely-firing sample is
    # NOT degraded to None by a healthy coverage reading.
    held_ok = {"AAA": {"df": _hist(60)}, "BBB": {"df": _hist(60)}}
    held_tickers_ok = ["AAA", "BBB"]
    corr_ok = correlation_matrix(held_ok)
    cov_ok = correlation_coverage(held_ok)
    assert cov_ok["n_obs"] >= CORR_MIN_OBS_TRUSTED
    clusters_ok = [_cluster(["AAA", "BBB"], [["AAA", "BBB"]])]
    result_ok = add_block_map(clusters_ok, corr_ok, held_tickers_ok, "2026-10-01", corr_coverage=cov_ok)
    assert set(result_ok.keys()) == {"AAA", "BBB"}
