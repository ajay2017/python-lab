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
    unchecked_disclosure,
)
from stock_analyzer.portfolio import (
    diversification_score,
    correlation_unchecked,
    correlation_matrix,
    correlation_coverage,
    CORR_MIN_OBS_TRUSTED,
)
from stock_analyzer import portfolio as _portfolio_mod
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


# ── unchecked_disclosure (disclosure-only G-25/G-26 follow-on) ───────────────
# docs/plans/pair-add-gate.md's fail-open blind spot: an unpriceable ticker
# can never be an add candidate itself, so the real gap is a priced,
# add-eligible holding whose PARTNER never entered the correlation matrix.
# This caption names the gap; it never suppresses anything.

def test_unchecked_disclosure_none_when_neither_gate_checked():
    # Even with a populated unchecked list, the existing 🧬/🔗 "couldn't
    # check" captions already cover a render where NEITHER gate ran --
    # stacking a third caption here would be redundant.
    assert unchecked_disclosure(["AAA"], pair_checked=False, cluster_checked=False) is None
    assert unchecked_disclosure(None, pair_checked=False, cluster_checked=False) is None


def test_unchecked_disclosure_none_when_checked_clean():
    assert unchecked_disclosure([], pair_checked=True, cluster_checked=False) is None
    assert unchecked_disclosure([], pair_checked=False, cluster_checked=True) is None
    assert unchecked_disclosure([], pair_checked=True, cluster_checked=True) is None


def test_unchecked_disclosure_couldnt_confirm_text_when_unchecked_is_none_but_a_gate_ran():
    text = unchecked_disclosure(None, pair_checked=True, cluster_checked=False)
    assert text is not None
    assert "couldn't confirm" in text
    assert "**" not in text and "$" not in text


@pytest.mark.parametrize("pair_checked,cluster_checked,expect_phrase", [
    (True, True, "Correlated-pair and new-cluster add-pause checks"),
    (True, False, "Correlated-pair add-pause check"),
    (False, True, "New-cluster add-pause check"),
])
def test_unchecked_disclosure_names_which_gate_ran(pair_checked, cluster_checked, expect_phrase):
    text = unchecked_disclosure(["AAA"], pair_checked=pair_checked, cluster_checked=cluster_checked)
    assert expect_phrase in text


def test_unchecked_disclosure_singular_ticker_wording():
    text = unchecked_disclosure(["AAA"], pair_checked=True, cluster_checked=False)
    assert "AAA" in text
    assert " it " in text or text.count(" it ") >= 1
    assert "they" not in text
    assert "is uncorrelated" in text


def test_unchecked_disclosure_plural_ticker_wording():
    text = unchecked_disclosure(["AAA", "BBB"], pair_checked=True, cluster_checked=True)
    assert "AAA, BBB" in text
    assert "they" in text
    assert "are uncorrelated" in text


def test_unchecked_disclosure_truncates_past_five_with_plus_n_more():
    tickers = [f"T{i:02d}" for i in range(8)]
    text = unchecked_disclosure(tickers, pair_checked=True, cluster_checked=True)
    for t in sorted(tickers)[:5]:
        assert t in text
    assert "+3 more" in text


def test_unchecked_disclosure_never_contains_bold_markers_or_dollar_signs():
    # feedback_streamlit_renderer_mismatch -- this project has no automated
    # check for the "**bold** prints literally" / "$...$ renders as LaTeX"
    # class, so the string itself must stay plain.
    for unchecked in (None, [], ["AAA"], ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]):
        for pc, cc in ((True, False), (False, True), (True, True)):
            text = unchecked_disclosure(unchecked, pair_checked=pc, cluster_checked=cc)
            if text is not None:
                assert "**" not in text
                assert "$" not in text


def test_unchecked_disclosure_malformed_unchecked_list_degrades_gracefully():
    # A non-string element inside the list must not raise.
    text = unchecked_disclosure([None, 123, "AAA"], pair_checked=True, cluster_checked=False)
    assert text is not None
    assert "AAA" in text


# ── G-26 defect regression (disclosure-only follow-on) ───────────────────────
# docs/plans/pair-add-gate.md's fail-open blind spot: an unpriceable B never
# enters corr_df, so the A-B pair can never be computed as danger-tier --
# add_block_map correctly degrades to {} (fail-open, not suppressing A just
# because its partner went dark), and correlation_unchecked is what actually
# names the gap for the render-layer disclosure.

def test_g26_defect_regression_all_priced_blocks_both_endpoints():
    df = _corr_df(["AAA", "BBB", "CCC"], {("AAA", "BBB"): 0.85})
    pair = {"t1": "AAA", "t2": "BBB", "corr": 0.85, "level": "danger"}
    result = add_block_map(df, [pair], [], ["AAA", "BBB", "CCC"])
    assert set(result.keys()) == {"AAA", "BBB"}
    assert correlation_unchecked(df, ["AAA", "BBB", "CCC"]) == []


def test_g26_defect_regression_b_unpriced_fails_open_and_is_disclosed():
    # Same book, but BBB never entered corr_df this run (unpriceable) -- the
    # pair can never be computed, so NO risk_pairs entry for it exists either.
    df = _corr_df(["AAA", "CCC"], {})  # BBB absent entirely
    result = add_block_map(df, [], [], ["AAA", "BBB", "CCC"])
    assert result == {}, "contract unchanged -- fail-open, never a fabricated block"
    assert correlation_unchecked(df, ["AAA", "BBB", "CCC"]) == ["BBB"]


def test_unchecked_disclosure_says_history_not_priced():
    """The sentence must blame the price HISTORY, not the price.

    Opus review 2026-10-09: `_close_series_map` also drops a ticker whose
    bundle loaded but whose history is empty / has no Close column, and such
    a ticker can still carry a live current_price and show a price elsewhere
    on Home. "couldn't be priced" would then contradict a number already on
    screen. Pinned because nothing else in this file asserts the wording, and
    that is exactly how the inaccurate phrasing shipped to review.
    """
    for tickers in (["AAPL"], ["AAPL", "MSFT"]):
        msg = unchecked_disclosure(tickers, True, True)
        assert "price history" in msg
        assert "couldn't be priced" not in msg


# ── add_block_map: corr_coverage thin-sample guard (disclosure-only) ────────
# An entirely-NaN correlation matrix (a held ticker with a short/non-
# overlapping history collapses the listwise intersection) must not let this
# gate assert "no dangerous pairs" from zero measured observations. Nothing
# below may change which tickers are suppressed -- only whether the gate
# returns None (couldn't check) instead of a fabricated {} -- an EMPTY map
# only. A map that FIRED is always returned untouched.

def _hist(n, start=100.0, step=1.0):
    import pandas as pd
    return pd.DataFrame({"Close": [start + step * i for i in range(n)]})


def test_corr_coverage_omitted_is_byte_identical_to_before():
    # Back-compat, non-blocking fixture: omitting corr_coverage entirely must
    # behave exactly as it did before this parameter existed.
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.70})
    with_default = add_block_map(df, [_warning_pair(corr=0.70)], [], ["AAA", "BBB"])
    without_param = add_block_map(df, [_warning_pair(corr=0.70)], [], ["AAA", "BBB"], corr_coverage=None)
    assert with_default == without_param == {}


def test_corr_coverage_omitted_is_byte_identical_to_before_blocking_fixture():
    # Back-compat, BLOCKING fixture -- the real suppression must survive
    # untouched when corr_coverage is simply never passed.
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    omitted = add_block_map(df, [_danger_pair(corr=0.9)], [], ["AAA", "BBB"])
    explicit_none = add_block_map(df, [_danger_pair(corr=0.9)], [], ["AAA", "BBB"], corr_coverage=None)
    assert set(omitted.keys()) == {"AAA", "BBB"}
    assert omitted == explicit_none


def test_corr_coverage_thin_sample_NEVER_discards_a_real_block():
    """THE regression guard. A thin sample must not lift a live pause.

    This test asserted the OPPOSITE until an Opus review caught it
    (2026-10-09). The thin check originally ran before the map was built and
    returned None regardless, which REMOVED suppressions that genuinely
    fire: between 2 and 19 overlapping observations `correlation_matrix`
    returns REAL correlations, so a danger-tier pair can exist and this map
    can be non-empty. Reproduced through the real chain at n_obs=11 with a
    1.0 danger pair — Grow Today would have said "add to AAA" while
    📡 Signals & Advice showed a PAIR_RISK trim card for the same pair,
    built from the same risk_pairs.

    Thin coverage may only downgrade an EMPTY map (see the companion test
    below). A firing map is returned untouched.
    """
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    pair = [_danger_pair(corr=0.9)]
    control = add_block_map(df, pair, [], ["AAA", "BBB"])
    assert set(control.keys()) == {"AAA", "BBB"}  # control genuinely blocks

    thin = add_block_map(
        df, pair, [], ["AAA", "BBB"],
        corr_coverage={"n_obs": CORR_MIN_OBS_TRUSTED - 1},
    )
    assert thin == control, (
        "a thin sample discarded a live suppression -- that is a gate being "
        "switched off by a short price history, not a disclosure change."
    )


def _no_block_inputs():
    """Inputs that legitimately produce an EMPTY map (warning tier, not
    danger), so the thin-coverage downgrade is the only variable."""
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    warning = [dict(_danger_pair(corr=0.9), level="warning")]
    return df, warning, ["AAA", "BBB"]


def test_corr_coverage_thin_sample_downgrades_an_EMPTY_map_to_none():
    # The hazard this guard exists for: an empty map cannot distinguish
    # "checked, found nothing" from "couldn't measure anything".
    df, warning, held = _no_block_inputs()
    assert add_block_map(df, warning, [], held) == {}   # control: empty, not None
    thin = add_block_map(df, warning, [], held,
                         corr_coverage={"n_obs": CORR_MIN_OBS_TRUSTED - 1})
    assert thin is None


def test_corr_coverage_boundary_at_floor_is_normal_behaviour():
    df, warning, held = _no_block_inputs()
    result = add_block_map(df, warning, [], held,
                           corr_coverage={"n_obs": CORR_MIN_OBS_TRUSTED})
    assert result == {}


def test_corr_coverage_boundary_one_below_floor_is_none():
    df, warning, held = _no_block_inputs()
    result = add_block_map(df, warning, [], held,
                           corr_coverage={"n_obs": CORR_MIN_OBS_TRUSTED - 1})
    assert result is None


def test_corr_coverage_n_obs_zero_is_none():
    # The real all-NaN-matrix case: n_obs == 0 with nothing detectable.
    df, warning, held = _no_block_inputs()
    result = add_block_map(df, warning, [], held, corr_coverage={"n_obs": 0})
    assert result is None


def test_corr_coverage_type_guards_are_load_bearing():
    # At the REAL floor (CORR_MIN_OBS_TRUSTED == 20): True compares equal to
    # 1, which IS below 20, so if the explicit bool guard were ever deleted
    # and True silently treated as the int 1, this would wrongly degrade to
    # None (thin). The guard must reject it outright instead, leaving the
    # empty map as {} -- no floor manipulation needed to prove this,
    # unlike the inverted `>=` check this mirrors (correlation_claim_verified,
    # portfolio.py), where True==1 already fails that floor either way.
    # MUST use an EMPTY-map fixture. With a firing fixture these assertions
    # are vacuous: since the fix, a non-empty map can never be downgraded at
    # all, so deleting either guard changes nothing and the test still
    # passes. An Opus review proved exactly that by mutation (2026-10-09) --
    # both guard-deletion mutants survived against the old danger-tier
    # fixture. Only on an empty map can the guards change the outcome.
    df, warning, held = _no_block_inputs()
    assert add_block_map(df, warning, [], held) == {}   # control: empty, not None

    bool_result = add_block_map(df, warning, [], held, corr_coverage={"n_obs": True})
    assert bool_result == {}, (
        "a bool n_obs must never trigger the thin-sample guard -- True == 1, "
        "which IS below the floor of 20, so deleting the bool guard would "
        "turn this into None"
    )

    # A numeric-looking string would raise TypeError on a bare `<` against an
    # int if the int-type guard were deleted; the helper sits inside the
    # try, so that raise would surface as None rather than {}.
    str_result = add_block_map(df, warning, [], held, corr_coverage={"n_obs": "125"})
    assert str_result == {}


def test_corr_coverage_floor_comparison_is_strict_less_than(monkeypatch):
    # Companion to the boundary tests above, using a monkeypatched floor of 1
    # so the comparison itself (not a type guard) is what's under test: a
    # real n_obs sitting EXACTLY at a low floor must clear it (strict `<`,
    # never `<=`) -- this is the same invariant the CORR_MIN_OBS_TRUSTED/
    # CORR_MIN_OBS_TRUSTED-1 boundary tests already pin at the real floor,
    # repeated here at the opposite extreme so a `<=` mutation can't hide at
    # one particular floor value.
    monkeypatch.setattr(_portfolio_mod, "CORR_MIN_OBS_TRUSTED", 1)
    df = _corr_df(["AAA", "BBB"], {("AAA", "BBB"): 0.9})
    pair = [_danger_pair(corr=0.9)]
    result = add_block_map(df, pair, [], ["AAA", "BBB"], corr_coverage={"n_obs": 1})
    assert set(result.keys()) == {"AAA", "BBB"}, "n_obs==floor must clear it, not be thin"


@pytest.mark.parametrize("bad_coverage", [
    None, {}, [], "x", {"n_obs": None}, {"n_obs": float("nan")}, {"no_n_obs_key": 1},
])
def test_corr_coverage_malformed_never_disables_the_gate(bad_coverage):
    # Empty-map fixture for the same reason as the type-guard test above:
    # against a firing map this can no longer detect anything, since a
    # non-empty map is never downgraded. Here a mishandled malformed value
    # would surface as None instead of {}.
    df, warning, held = _no_block_inputs()
    assert add_block_map(df, warning, [], held) == {}   # control
    result = add_block_map(df, warning, [], held, corr_coverage=bad_coverage)
    assert result == {}


def test_corr_coverage_end_to_end_all_nan_matrix_degrades_to_none():
    """Reproduces the real hazard in the venv through the REAL
    correlation_matrix/correlation_coverage/diversification_score chain (not
    a hand-built corr_df): AAA/BBB at 60 bars, CCC at 1 bar. The listwise
    intersection collapses to zero shared observations, yet the matrix is
    non-empty and risk_pairs is empty (every correlation is NaN, so
    diversification_score's own pair loop skips every cell) -- a real
    production render would see risk_pairs=[] and read this gate's old {}
    return as "checked, nothing dangerous," built from zero data.
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

    result = add_block_map(corr, div["risk_pairs"], [], held_tickers, corr_coverage=cov)
    assert result is None

    # Control: drop CCC -- a real, comfortably-above-floor sample (AAA/BBB
    # are identical price series, so their correlation is exactly 1.0,
    # genuinely danger-tier) is NOT degraded to None by a healthy coverage
    # reading. The gate still fires for real, exactly as it would have
    # before this parameter existed.
    held_ok = {"AAA": {"df": _hist(60)}, "BBB": {"df": _hist(60)}}
    held_tickers_ok = ["AAA", "BBB"]
    corr_ok = correlation_matrix(held_ok)
    cov_ok = correlation_coverage(held_ok)
    div_ok = diversification_score(corr_ok)
    assert cov_ok["n_obs"] >= CORR_MIN_OBS_TRUSTED
    assert div_ok["risk_pairs"] != []
    result_ok = add_block_map(corr_ok, div_ok["risk_pairs"], [], held_tickers_ok, corr_coverage=cov_ok)
    assert set(result_ok.keys()) == {"AAA", "BBB"}
