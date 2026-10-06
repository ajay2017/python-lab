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
