"""Tests for stock_analyzer.pair_add_gate (G-26, owner decisions 2026-10-08).

Three-state contract throughout — None=couldn't check, {}=checked clean,
populated=firing. Every test below is pinned against that contract directly
rather than inferring it from a single happy-path case.
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_analyzer.pair_add_gate import (
    add_block_map,
    describe_reason,
    split_scan_liftable,
    buy_lane_only,
    partners_of,
)
from stock_analyzer.portfolio import diversification_score
from stock_analyzer.constants import CORR_DANGER_PAIRS_THRESHOLD

pytestmark = pytest.mark.fast


def _corr_df(tickers, pairs):
    df = pd.DataFrame(0.0, index=tickers, columns=tickers)
    for t in tickers:
        df.loc[t, t] = 1.0
    for (a, b), c in pairs.items():
        df.loc[a, b] = c
        df.loc[b, a] = c
    return df


def _danger_pair(t1="AAA", t2="BBB", corr=0.85):
    return {"t1": t1, "t2": t2, "corr": round(corr, 2), "level": "danger"}


def _warning_pair(t1="AAA", t2="BBB", corr=0.70):
    return {"t1": t1, "t2": t2, "corr": round(corr, 2), "level": "warning"}


def _pair_risk_rec(t1="AAA", t2="BBB", weaker="AAA"):
    return {"type": "PAIR_RISK", "t1": t1, "t2": t2, "weaker": weaker}


# ── add_block_map: offline / malformed input ─────────────────────────────────

def test_none_corr_df_is_couldnt_check():
    assert add_block_map(None, [], None, ["AAA", "BBB"]) is None


def test_empty_corr_df_is_couldnt_check():
    assert add_block_map(pd.DataFrame(), [], None, ["AAA", "BBB"]) is None


def test_collapsed_failure_empty_corr_df_and_empty_risk_pairs_is_none():
    # home_risk_synthesis's except branch sets BOTH corr_df=pd.DataFrame() and
    # risk_pairs=[] on a real failure -- must read as None (couldn't check),
    # never {} (checked, clean). This is the fact #2 regression this gate's
    # spec explicitly calls out.
    assert add_block_map(pd.DataFrame(), [], None, ["AAA", "BBB"]) is None


def test_risk_pairs_not_a_list_is_couldnt_check():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    assert add_block_map(df, None, None, ["AAA", "BBB"]) is None
    assert add_block_map(df, "not a list", None, ["AAA", "BBB"]) is None


def test_checked_but_nothing_danger_tier_is_empty_dict():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.70})
    result = add_block_map(df, [_warning_pair(corr=0.70)], [], ["AAA", "BBB"])
    assert result == {}


def test_never_raises_on_malformed_pair():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    result = add_block_map(df, [{"level": "danger"}], [], ["AAA", "BBB"])  # no t1/t2
    assert result == {}


# ── add_block_map: level, not rounded corr value ─────────────────────────────

def test_warning_level_with_rounded_080_display_does_not_fire():
    # A pair stored as corr=0.80 (rounded) but labelled "warning" tier must
    # NOT fire -- the gate reads "level", never a numeric corr comparison.
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.799})
    pair = {"t1": "AAA", "t2": "BBB", "corr": 0.80, "level": "warning"}
    result = add_block_map(df, [pair], [], ["AAA", "BBB"])
    assert result == {}


def test_danger_level_fires_regardless_of_rounded_display():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.80})
    pair = {"t1": "AAA", "t2": "BBB", "corr": 0.80, "level": "danger"}
    result = add_block_map(df, [pair], [], ["AAA", "BBB"])
    assert set(result.keys()) == {"AAA", "BBB"}


def test_end_to_end_via_real_diversification_score_0799_vs_0800():
    # Build the risk_pairs list via the REAL portfolio.diversification_score()
    # (not hand-crafted) so this also proves the two modules agree: 0.799
    # rounds to a displayed 0.80 but is "warning" tier (must not fire); an
    # exact 0.800 is "danger" tier (must fire).
    df_799 = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): CORR_DANGER_PAIRS_THRESHOLD - 0.001})
    div_799 = diversification_score(df_799, {"AAA": 50.0, "BBB": 50.0})
    result_799 = add_block_map(df_799, div_799["risk_pairs"], [], ["AAA", "BBB"])
    assert result_799 == {}

    df_800 = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): CORR_DANGER_PAIRS_THRESHOLD})
    div_800 = diversification_score(df_800, {"AAA": 50.0, "BBB": 50.0})
    result_800 = add_block_map(df_800, div_800["risk_pairs"], [], ["AAA", "BBB"])
    assert set(result_800.keys()) == {"AAA", "BBB"}


# ── add_block_map: held-status scoping ───────────────────────────────────────

def test_partner_not_held_pauses_neither():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    result = add_block_map(df, [_danger_pair()], [], ["AAA"])  # BBB not held
    assert result == {}


def test_both_held_blocks_both_endpoints():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    result = add_block_map(df, [_danger_pair(corr=0.9)], [], ["AAA", "BBB"])
    assert set(result.keys()) == {"AAA", "BBB"}
    assert result["AAA"]["max_corr"] == 0.9
    assert result["AAA"]["partners"][0]["partner"] == "BBB"
    assert result["BBB"]["partners"][0]["partner"] == "AAA"


# ── add_block_map: trim_call classification ──────────────────────────────────

def test_missing_div_recs_list_does_not_disable_the_gate_tags_unknown():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    result = add_block_map(df, [_danger_pair()], None, ["AAA", "BBB"])
    assert set(result.keys()) == {"AAA", "BBB"}
    assert result["AAA"]["partners"][0]["trim_call"] == "unknown"
    assert result["BBB"]["partners"][0]["trim_call"] == "unknown"


def test_named_trim_call_when_pair_risk_rec_exists():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    result = add_block_map(df, [_danger_pair()], [_pair_risk_rec(weaker="AAA")], ["AAA", "BBB"])
    assert result["AAA"]["partners"][0]["trim_call"] == "named"
    assert result["AAA"]["partners"][0]["weaker"] == "AAA"
    assert result["BBB"]["partners"][0]["trim_call"] == "named"


def test_not_named_when_div_recs_checked_but_no_matching_pair_risk():
    # A partner with no score yields no PAIR_RISK rec (portfolio.py skips the
    # pair when either member lacks "Score Available"), but the scored name
    # is still paused here -- tagged "not_named", never dropped.
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    result = add_block_map(df, [_danger_pair()], [], ["AAA", "BBB"])
    assert set(result.keys()) == {"AAA", "BBB"}
    assert result["AAA"]["partners"][0]["trim_call"] == "not_named"
    assert result["AAA"]["partners"][0]["weaker"] is None


def test_trim_call_matches_pair_in_either_ticker_order():
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    # Rec stores t1/t2 in the OPPOSITE order from risk_pairs.
    rec = {"type": "PAIR_RISK", "t1": "BBB", "t2": "AAA", "weaker": "BBB"}
    result = add_block_map(df, [_danger_pair(t1="AAA", t2="BBB")], [rec], ["AAA", "BBB"])
    assert result["AAA"]["partners"][0]["trim_call"] == "named"
    assert result["BBB"]["partners"][0]["trim_call"] == "named"


def test_nan_corr_skipped_not_raised():
    df = _corr_df(["AAA", "BBB"], {})
    pair = {"t1": "AAA", "t2": "BBB", "corr": float("nan"), "level": "danger"}
    assert add_block_map(df, [pair], [], ["AAA", "BBB"]) == {}


def test_max_corr_across_multiple_partners():
    df = _corr_df(["AAA", "BBB", "CCC"], {("AAA", "BBB"): 0.82, ("AAA", "CCC"): 0.95})
    pairs = [_danger_pair("AAA", "BBB", 0.82), _danger_pair("AAA", "CCC", 0.95)]
    result = add_block_map(df, pairs, [], ["AAA", "BBB", "CCC"])
    assert result["AAA"]["max_corr"] == 0.95
    assert {p["partner"] for p in result["AAA"]["partners"]} == {"BBB", "CCC"}


# ── describe_reason ───────────────────────────────────────────────────────────

def test_describe_reason_named():
    entry = {"partners": [{"partner": "BBB", "corr": 0.9, "trim_call": "named", "weaker": "AAA"}]}
    text = describe_reason(entry)
    assert "the pair's trim call is on 📡 Signals & Advice" in text
    assert text.endswith("This pause adds no recommendation of its own.")
    assert len(text) <= 300


def test_describe_reason_not_named_names_partner():
    entry = {"partners": [{"partner": "BBB", "corr": 0.9, "trim_call": "not_named", "weaker": None}]}
    text = describe_reason(entry)
    assert "BBB" in text
    assert "conviction isn't" in text
    assert text.endswith("This pause adds no recommendation of its own.")


def test_describe_reason_unknown():
    entry = {"partners": [{"partner": "BBB", "corr": 0.9, "trim_call": "unknown", "weaker": None}]}
    text = describe_reason(entry)
    assert "trim-call status couldn't be read this run" in text


def test_describe_reason_malformed_entry_never_raises():
    assert describe_reason(None) != ""
    assert describe_reason({}) != ""
    assert describe_reason({"partners": "not a list"}) != ""


def test_describe_reason_length_capped_at_300():
    entry = {
        "partners": [
            {"partner": f"T{i:03d}", "corr": 0.9, "trim_call": "not_named", "weaker": None}
            for i in range(20)
        ]
    }
    assert len(describe_reason(entry)) <= 300


# ── split_scan_liftable ───────────────────────────────────────────────────────

def test_split_scan_liftable_pair_blocks_none_fail_open_everything_liftable():
    cluster_blocks = {"AAA": {}, "BBB": {}}
    liftable, also_pair = split_scan_liftable(cluster_blocks, None)
    assert liftable == ["AAA", "BBB"]
    assert also_pair == []


def test_split_scan_liftable_splits_overlap():
    cluster_blocks = {"AAA": {}, "BBB": {}, "CCC": {}}
    pair_blocks = {"BBB": {}}
    liftable, also_pair = split_scan_liftable(cluster_blocks, pair_blocks)
    assert liftable == ["AAA", "CCC"]
    assert also_pair == ["BBB"]


def test_split_scan_liftable_empty_cluster_blocks():
    assert split_scan_liftable({}, {"AAA": {}}) == ([], [])
    assert split_scan_liftable(None, {"AAA": {}}) == ([], [])


# ── buy_lane_only ─────────────────────────────────────────────────────────────

def test_buy_lane_only_none_passes_through():
    assert buy_lane_only([], None) is None
    assert buy_lane_only(None, None) is None


def test_buy_lane_only_excludes_already_disclosed():
    pair_blocked_adds = [{"ticker": "AAA"}]
    result = buy_lane_only(pair_blocked_adds, ["AAA", "BBB"])
    assert result == ["BBB"]


def test_buy_lane_only_empty_skip_list_is_empty_not_none():
    assert buy_lane_only([], []) == []


# ── partners_of ───────────────────────────────────────────────────────────────

def test_partners_of_returns_the_real_list():
    entry = {"partners": [{"partner": "BBB", "corr": 0.84}], "max_corr": 0.84}
    assert partners_of(entry) == [{"partner": "BBB", "corr": 0.84}]


def test_partners_of_degrades_to_empty_list_never_raises():
    # Each of these is a malformed entry a render site could receive; none
    # may raise, and every one must degrade to "no partners to name".
    for bad in (None, {}, {"partners": None}, {"partners": "AAA"},
                {"partners": 7}, "not-a-dict", 42):
        assert partners_of(bad) == []


def test_partners_of_preserves_an_empty_list_distinctly():
    # An entry that genuinely has no partners and a malformed one both
    # render the same way, but the accessor must not invent entries for
    # either -- this pins that it returns a list in both cases.
    assert partners_of({"partners": []}) == []
    assert isinstance(partners_of({"partners": []}), list)
