"""
Tests for stock_analyzer.beta_repair -- the beta-lever arithmetic that
replaced the false "add 8-10% to reach target beta" claim in risk_advisor.py.

Fixture used throughout, matching the real book that motivated this feature:
    current_beta = 1.88, current_value (portfolio_value) = 24,500
    high-beta position: beta 4.15, weight 10% of the book (i.e. $2,450)
    candidate defensive name: beta 0.60
    target = 1.3 (PORTFOLIO_BETA_ELEVATED)
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer.beta_repair import (
    aligned_beta,
    dollars_to_target_add,
    dollars_to_target_swap,
    dollars_to_target_trim,
    expected_beta_after_swap,
    expected_beta_after_trim,
    leverage_side_effect,
    rank_defensive_candidates,
)
from stock_analyzer.portfolio import expected_beta_after_add

pytestmark = pytest.mark.fast

_B = 1.88      # current_beta
_V = 24_500.0  # current_value / portfolio_value
_T = 1.3       # target (PORTFOLIO_BETA_ELEVATED)
_FROM_BETA = 4.15   # the high-beta position being trimmed/swapped out of
_TO_BETA = 0.60     # the defensive candidate being swapped/added into
_DOLLARS = 2_450.0  # 10% of _V


# ── pinned closed forms ──────────────────────────────────────────────────────

def test_add_pinned_figure():
    # Cash-funded add: existing portfolio.expected_beta_after_add, re-verified
    # here as the baseline the other two levers are compared against.
    assert expected_beta_after_add(_B, _V, _DOLLARS, _TO_BETA) == 1.76


def test_swap_pinned_figure():
    result = expected_beta_after_swap(
        current_beta=_B, current_value=_V, swap_dollars=_DOLLARS,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    )
    # Mathematically 1.525 exactly; float repr places it fractionally below
    # 1.525 so round() (round-half-to-even on the nearest representable float)
    # lands on 1.52, not 1.53 -- pinned to what the code actually returns.
    assert result == 1.52


def test_trim_pinned_figure():
    result = expected_beta_after_trim(
        current_beta=_B, book_fraction_sold=0.10, position_beta=_FROM_BETA,
    )
    assert result == 1.63


def test_dollars_to_target_add_pinned_figure():
    assert dollars_to_target_add(
        current_beta=_B, current_value=_V, target=_T, candidate_beta=_TO_BETA,
    ) == 20_300.0


def test_dollars_to_target_swap_pinned_figure():
    result = dollars_to_target_swap(
        current_beta=_B, current_value=_V, target=_T,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    )
    assert result == pytest.approx(4_002.82, abs=0.01)


def test_dollars_to_target_trim_pinned_figure():
    result = dollars_to_target_trim(
        current_beta=_B, current_value=_V, target=_T, position_beta=_FROM_BETA,
    )
    assert result == pytest.approx(4_985.96, abs=0.01)


# ── invariants, at exact boundaries ──────────────────────────────────────────

def test_swap_constant_notional_when_betas_equal():
    # to_beta == from_beta -> the swap changes nothing; current_beta returned
    # EXACTLY, proving the denominator never grows (unlike a cash add).
    result = expected_beta_after_swap(
        current_beta=_B, current_value=_V, swap_dollars=_DOLLARS,
        from_beta=3.0, to_beta=3.0,
    )
    assert result == _B


def test_composition_swap_equals_trim_then_add():
    # swap == trim(book_fraction) then add(same dollars). Verified with
    # UNROUNDED intermediate values -- each function rounds to 2dp internally,
    # so composing the ROUNDED trim output through add compounds a small
    # (~0.01) rounding error; abs=0.02 tolerance absorbs exactly that, no more.
    direct_swap = expected_beta_after_swap(
        current_beta=_B, current_value=_V, swap_dollars=_DOLLARS,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    )
    trimmed = expected_beta_after_trim(
        current_beta=_B, book_fraction_sold=_DOLLARS / _V, position_beta=_FROM_BETA,
    )
    remaining_value = _V - _DOLLARS
    composed = expected_beta_after_add(trimmed, remaining_value, _DOLLARS, _TO_BETA)
    assert composed == pytest.approx(direct_swap, abs=0.02)


def test_div_by_zero_boundary_add_candidate_beta_at_target():
    # candidate_beta == target exactly -> denominator (target - candidate_beta)
    # is 0 -> None, never inf, never a huge float.
    assert dollars_to_target_add(
        current_beta=_B, current_value=_V, target=_T, candidate_beta=_T,
    ) is None


def test_div_by_zero_boundary_trim_position_beta_at_target():
    assert dollars_to_target_trim(
        current_beta=_B, current_value=_V, target=_T, position_beta=_T,
    ) is None


def test_div_by_zero_boundary_swap_from_beta_equals_to_beta():
    assert dollars_to_target_swap(
        current_beta=_B, current_value=_V, target=_T, from_beta=2.0, to_beta=2.0,
    ) is None


def test_unreachable_add_when_candidate_beta_above_target():
    # A candidate at/above target can never dilute the book down to target,
    # at ANY dollar amount -- None, not a fabricated (negative/huge) number.
    assert dollars_to_target_add(
        current_beta=_B, current_value=_V, target=_T, candidate_beta=_T + 0.5,
    ) is None


def test_unreachable_trim_when_position_beta_below_target():
    assert dollars_to_target_trim(
        current_beta=_B, current_value=_V, target=_T, position_beta=_T - 0.5,
    ) is None


def test_unreachable_swap_when_direction_is_backwards():
    # from_beta < to_beta -- this "swap" would RAISE beta, not lower it.
    assert dollars_to_target_swap(
        current_beta=_B, current_value=_V, target=_T, from_beta=0.5, to_beta=2.0,
    ) is None


def test_nothing_to_fix_when_already_at_or_below_target():
    assert dollars_to_target_add(
        current_beta=_T, current_value=_V, target=_T, candidate_beta=_TO_BETA,
    ) is None
    assert dollars_to_target_trim(
        current_beta=1.0, current_value=_V, target=_T, position_beta=_FROM_BETA,
    ) is None
    assert dollars_to_target_swap(
        current_beta=1.0, current_value=_V, target=_T,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    ) is None


def test_monotonic_relief_per_dollar_swap_beats_trim_beats_add():
    # Relief per $1,000 moved: swap > trim > add, on the production fixture.
    add_dollars = dollars_to_target_add(
        current_beta=_B, current_value=_V, target=_T, candidate_beta=_TO_BETA,
    )
    swap_dollars = dollars_to_target_swap(
        current_beta=_B, current_value=_V, target=_T,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    )
    trim_dollars = dollars_to_target_trim(
        current_beta=_B, current_value=_V, target=_T, position_beta=_FROM_BETA,
    )
    relief = _B - _T
    add_relief_per_1k = relief / add_dollars * 1000
    swap_relief_per_1k = relief / swap_dollars * 1000
    trim_relief_per_1k = relief / trim_dollars * 1000
    assert swap_relief_per_1k > trim_relief_per_1k > add_relief_per_1k


# ── sentinels ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fn,kwargs", [
    (expected_beta_after_swap, dict(current_beta=None, current_value=_V,
     swap_dollars=_DOLLARS, from_beta=_FROM_BETA, to_beta=_TO_BETA)),
    (expected_beta_after_trim, dict(current_beta=None, book_fraction_sold=0.1,
     position_beta=_FROM_BETA)),
    (dollars_to_target_add, dict(current_beta=None, current_value=_V, target=_T,
     candidate_beta=_TO_BETA)),
    (dollars_to_target_swap, dict(current_beta=None, current_value=_V, target=_T,
     from_beta=_FROM_BETA, to_beta=_TO_BETA)),
    (dollars_to_target_trim, dict(current_beta=None, current_value=_V, target=_T,
     position_beta=_FROM_BETA)),
])
def test_none_current_beta_returns_none(fn, kwargs):
    assert fn(**kwargs) is None


def test_current_value_zero_or_negative_returns_none():
    assert expected_beta_after_swap(
        current_beta=_B, current_value=0, swap_dollars=_DOLLARS,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    ) is None
    assert expected_beta_after_swap(
        current_beta=_B, current_value=-100, swap_dollars=_DOLLARS,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    ) is None
    assert dollars_to_target_add(
        current_beta=_B, current_value=0, target=_T, candidate_beta=_TO_BETA,
    ) is None


def test_swap_dollars_out_of_range_returns_none():
    # negative or larger than the whole book -> None
    assert expected_beta_after_swap(
        current_beta=_B, current_value=_V, swap_dollars=-1,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    ) is None
    assert expected_beta_after_swap(
        current_beta=_B, current_value=_V, swap_dollars=_V + 1,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    ) is None


def test_trim_fraction_out_of_range_returns_none():
    assert expected_beta_after_trim(
        current_beta=_B, book_fraction_sold=-0.1, position_beta=_FROM_BETA,
    ) is None
    assert expected_beta_after_trim(
        current_beta=_B, book_fraction_sold=1.0, position_beta=_FROM_BETA,
    ) is None


@pytest.mark.parametrize("nan_value", [float("nan"), np.float64("nan")])
def test_nan_current_beta_returns_none_python_and_numpy(nan_value):
    # feedback_none_sentinel_meets_pandas -- both a bare Python float("nan")
    # and a numpy NaN (what a pandas cell actually yields) must be caught.
    assert expected_beta_after_swap(
        current_beta=nan_value, current_value=_V, swap_dollars=_DOLLARS,
        from_beta=_FROM_BETA, to_beta=_TO_BETA,
    ) is None
    assert dollars_to_target_trim(
        current_beta=nan_value, current_value=_V, target=_T, position_beta=_FROM_BETA,
    ) is None


# ── aligned_beta / window alignment ──────────────────────────────────────────

def _make_close_series(n, start="2026-01-01", seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n)
    prices = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.Series(prices, index=dates)


def test_aligned_beta_returns_none_and_zero_on_missing_inputs():
    assert aligned_beta(candidate_close=None, spy_close=None, on_index=None) == (None, 0)


def test_aligned_beta_exact_overlap_count():
    # 26 business days -> pct_change() drops the first -> 25 return observations.
    cand = _make_close_series(26, seed=3)
    spy = _make_close_series(26, seed=4)
    beta, n_obs = aligned_beta(candidate_close=cand, spy_close=spy, on_index=cand.index, min_obs=20)
    assert n_obs == 25
    assert beta is not None


def test_aligned_beta_below_floor_returns_none_beta_but_reports_n_obs():
    # 20 business days -> 19 return observations, below min_obs=20 -> (None, 19),
    # NOT (None, 0) -- callers must be able to tell "some overlap, still short"
    # from "no overlap at all".
    cand = _make_close_series(20, seed=5)
    spy = _make_close_series(20, seed=6)
    beta, n_obs = aligned_beta(candidate_close=cand, spy_close=spy, on_index=cand.index, min_obs=20)
    assert beta is None
    assert n_obs == 19


def test_aligned_beta_restricts_to_book_index_not_full_candidate_overlap():
    # Candidate/SPY have a long shared history, but the book's own return-series
    # index (on_index) only covers a short recent window -- aligned_beta must
    # regress on the SHORT window, not the candidate's full overlap with SPY.
    n = 60
    cand = _make_close_series(n, seed=7)
    spy = _make_close_series(n, seed=8)
    short_idx = cand.index[-15:]   # last 15 dates only -> ~14 return obs, below floor
    beta_short, n_short = aligned_beta(
        candidate_close=cand, spy_close=spy, on_index=short_idx, min_obs=20,
    )
    beta_full, n_full = aligned_beta(
        candidate_close=cand, spy_close=spy, on_index=cand.index, min_obs=20,
    )
    assert n_short < n_full
    assert beta_short is None   # short window doesn't meet the floor
    assert beta_full is not None


# ── import isolation ──────────────────────────────────────────────────────────

def test_module_does_not_import_gate_files():
    # beta_repair.py deliberately stays out of the decision-engine import
    # graph -- it duplicates _to_tz_naive rather than importing
    # portfolio._to_tz_naive, and independently re-derives dollars_to_target_add
    # rather than importing portfolio.expected_beta_after_add, specifically so
    # this module (not yet in _GATE_FILES as of Phase 1) never drags a
    # _GATE_FILES member into its own import graph. Same shape as the F-259b
    # import-isolation test that caught risk.py being pulled into gate_ledger's
    # graph. Parses the module's own AST (not a substring grep, which a
    # comment mentioning "portfolio.py" would falsely trip) so a future edit
    # that adds a real `import`/`from ... import` of a forbidden module fails
    # this test rather than silently drifting.
    import ast
    from pathlib import Path

    from stock_analyzer import beta_repair

    forbidden = {"stock_analyzer.portfolio", "stock_analyzer.stress_test",
                 "stock_analyzer.forward_sim", "portfolio", "stress_test",
                 "forward_sim"}
    source = Path(beta_repair.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    hit = imported & forbidden
    assert not hit, f"beta_repair.py must not import: {hit}"


# ── leverage_side_effect ──────────────────────────────────────────────────────

_LEV_FIXTURE = dict(
    dollars=2_450.0, current_beta=1.88, new_beta=1.76, gross_book=24_500.0,
    net_capital=7_802.0, basis="levered",
)


def test_leverage_add_worsens_ratio_even_though_beta_improved():
    # The counterintuitive claim this feature exists to surface: an ADD
    # improves the beta number (1.88 -> 1.76) while the exposure/equity
    # ratio gets WORSE, because gross_book grows against a fixed equity.
    r = leverage_side_effect(kind="add", **_LEV_FIXTURE)
    assert r["state"] == "measured"
    assert r["direction"] == "worse"
    assert r["ratio_after"] > r["ratio_before"]
    assert r["beta_dollars_before"] == 46_060.0
    assert r["ratio_before"] == 5.9


def test_leverage_trim_always_improves_ratio():
    r = leverage_side_effect(
        kind="trim", dollars=7_350.0, current_beta=1.88, new_beta=0.91,
        gross_book=24_500.0, net_capital=7_802.0, basis="levered",
    )
    assert r["state"] == "measured"
    assert r["direction"] == "better"
    assert r["ratio_after"] < r["ratio_before"]


def test_leverage_swap_holds_gross_book_constant_and_improves_ratio():
    r = leverage_side_effect(
        kind="swap", dollars=4_003.0, current_beta=1.88, new_beta=1.3,
        gross_book=24_500.0, net_capital=7_802.0, basis="levered",
    )
    assert r["state"] == "measured"
    assert r["direction"] == "better"
    # gross_book unchanged for a swap -> beta_dollars_after == new_beta * gross_book
    assert r["beta_dollars_after"] == round(1.3 * 24_500.0, 2)


@pytest.mark.parametrize("basis,expected_state", [
    ("unlevered", "not_levered"),
    ("stale", "stale"),
    ("called", "called"),
])
def test_leverage_four_states_distinct_and_no_direction_outside_measured(basis, expected_state):
    r = leverage_side_effect(
        kind="trim", dollars=100, current_beta=1.88, new_beta=1.5,
        gross_book=24_500.0, net_capital=None, basis=basis,
    )
    assert r["state"] == expected_state
    assert "direction" not in r


def test_leverage_unlevered_and_stale_are_not_the_same_state():
    # Both return net_capital=None from resolve_net_capital, but they are NOT
    # interchangeable -- "no debt" vs "can't measure" must stay distinguishable.
    unlevered = leverage_side_effect(
        kind="trim", dollars=100, current_beta=1.88, new_beta=1.5,
        gross_book=24_500.0, net_capital=None, basis="unlevered",
    )
    stale = leverage_side_effect(
        kind="trim", dollars=100, current_beta=1.88, new_beta=1.5,
        gross_book=24_500.0, net_capital=None, basis="stale",
    )
    assert unlevered["state"] != stale["state"]


def test_leverage_called_does_not_withhold_the_trim_lever():
    # A margin call is exactly when trimming is MOST warranted -- called must
    # not suppress a trim the way it would an add/swap (caller's job for
    # those, not this function's -- but "called" for kind="trim" must still
    # be a distinct, actionable state, not silently degraded to "stale").
    r = leverage_side_effect(
        kind="trim", dollars=7_350.0, current_beta=1.88, new_beta=0.91,
        gross_book=24_500.0, net_capital=None, basis="called",
    )
    assert r["state"] == "called"
    assert r["kind"] == "trim"


def test_leverage_missing_numeric_input_while_levered_degrades_to_stale():
    # basis claims measurable data exists, but a specific number is missing --
    # must not raise, must not compute, must not fabricate a ratio.
    r = leverage_side_effect(
        kind="add", dollars=None, current_beta=1.88, new_beta=1.76,
        gross_book=24_500.0, net_capital=7_802.0, basis="levered",
    )
    assert r["state"] == "stale"
    assert "direction" not in r


def test_leverage_nan_input_while_levered_degrades_to_stale():
    r = leverage_side_effect(
        kind="add", dollars=float("nan"), current_beta=1.88, new_beta=1.76,
        gross_book=24_500.0, net_capital=7_802.0, basis="levered",
    )
    assert r["state"] == "stale"


def test_leverage_unknown_kind_degrades_to_stale_not_a_crash():
    r = leverage_side_effect(
        kind="bogus", dollars=100, current_beta=1.88, new_beta=1.5,
        gross_book=24_500.0, net_capital=7_802.0, basis="levered",
    )
    assert r["state"] == "stale"


def test_leverage_gross_book_going_negative_degrades_to_stale():
    # A trim larger than the entire book (should never happen upstream, but
    # this function must not compute a negative gross_book_after silently).
    r = leverage_side_effect(
        kind="trim", dollars=50_000.0, current_beta=1.88, new_beta=0.5,
        gross_book=24_500.0, net_capital=7_802.0, basis="levered",
    )
    assert r["state"] == "stale"


# ── rank_defensive_candidates ─────────────────────────────────────────────────

_HOLD_FLOOR = 44.0   # COMPOSITE_HOLD
_BUY_GATE   = 65.0   # COMPOSITE_BUY
_TARGET     = 1.3    # PORTFOLIO_BETA_ELEVATED


def test_rank_defensive_candidates_none_when_candidates_none():
    assert rank_defensive_candidates(
        candidates=None, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    ) is None


def test_rank_defensive_candidates_none_when_target_none():
    assert rank_defensive_candidates(
        candidates=[{"ticker": "AAA", "beta": 0.5, "corr": 0.1, "composite": 70}],
        target=None, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    ) is None


def test_rank_defensive_candidates_empty_list_when_measured_and_empty():
    result = rank_defensive_candidates(
        candidates=[], target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )
    assert result == []


def test_rank_defensive_candidates_never_drops_a_row():
    # 4 candidates: one actionable, one below-floor, one structurally
    # unreachable, one unscored -- ALL FOUR must appear in the output.
    candidates = [
        {"ticker": "ACTIONABLE", "beta": 0.5, "corr": 0.1, "composite": 70},
        {"ticker": "BELOWFLOOR", "beta": 0.5, "corr": 0.1, "composite": 30},
        {"ticker": "UNREACHABLE", "beta": 2.0, "corr": 0.1, "composite": 70},
        {"ticker": "UNSCORED", "beta": 0.5, "corr": 0.1, "composite": None},
    ]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )
    assert {r["ticker"] for r in result} == {
        "ACTIONABLE", "BELOWFLOOR", "UNREACHABLE", "UNSCORED",
    }


def test_rank_defensive_candidates_actionable_true_only_when_reachable_and_above_buy_gate():
    candidates = [
        {"ticker": "A", "beta": 0.5, "corr": 0.1, "composite": 70},   # actionable
        {"ticker": "B", "beta": 2.0, "corr": 0.1, "composite": 90},   # unreachable, never actionable
    ]
    result = {r["ticker"]: r for r in rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )}
    assert result["A"]["actionable"] is True
    assert result["B"]["actionable"] is False
    assert result["B"]["reachable"] is False


def test_rank_defensive_candidates_disclosed_cost_between_floor_and_buy_gate():
    candidates = [{"ticker": "MID", "beta": 0.5, "corr": 0.1, "composite": 55}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["meets_floor"] is True
    assert result["actionable"] is False
    assert result["cost_note"] is not None
    assert "risk trade" in result["cost_note"]


def test_rank_defensive_candidates_below_floor_has_no_cost_note():
    candidates = [{"ticker": "LOW", "beta": 0.5, "corr": 0.1, "composite": 30}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["meets_floor"] is False
    assert result["cost_note"] is None


def test_rank_defensive_candidates_unscored_composite_is_none_not_false():
    # Distinct from "measured and below the floor" -- couldn't load a score
    # at all must not masquerade as a measured failure.
    candidates = [{"ticker": "NOLOAD", "beta": 0.5, "corr": 0.1, "composite": None}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["meets_floor"] is None
    assert result["actionable"] is False


def test_rank_defensive_candidates_unreachable_never_gets_a_floor_classification():
    # A structurally unreachable candidate's composite is irrelevant to the
    # beta objective -- meets_floor must stay None (not evaluated), never
    # True/False, since the composite was never the reason it's excluded.
    candidates = [{"ticker": "HIBETA", "beta": 5.0, "corr": 0.1, "composite": 90}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["reachable"] is False
    assert result["meets_floor"] is None
    assert result["actionable"] is False


def test_rank_defensive_candidates_beta_exactly_at_target_is_unreachable():
    # Structural boundary: beta == target means dilution can never pull the
    # book strictly below target -- >= is unreachable, not just >.
    candidates = [{"ticker": "ATTARGET", "beta": _TARGET, "corr": 0.1, "composite": 90}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["reachable"] is False


def test_rank_defensive_candidates_unmeasured_beta_is_none_not_false():
    candidates = [{"ticker": "NOBETA", "beta": None, "corr": 0.1, "composite": 90}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["reachable"] is None
    assert result["actionable"] is False


def test_rank_defensive_candidates_sort_order_reachable_before_unreachable():
    candidates = [
        {"ticker": "UNREACHABLE", "beta": 5.0, "corr": 0.1, "composite": 90},
        {"ticker": "REACHABLE", "beta": 0.5, "corr": 0.1, "composite": 20},
    ]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )
    assert [r["ticker"] for r in result] == ["REACHABLE", "UNREACHABLE"]


def test_rank_defensive_candidates_sort_order_actionable_before_cost_disclosed():
    candidates = [
        {"ticker": "COST", "beta": 0.5, "corr": 0.1, "composite": 55},
        {"ticker": "CLEAN", "beta": 0.5, "corr": 0.1, "composite": 70},
    ]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )
    assert [r["ticker"] for r in result] == ["CLEAN", "COST"]


def test_rank_defensive_candidates_sort_order_within_tier_by_beta_then_corr_then_composite():
    candidates = [
        {"ticker": "HIGHER_BETA", "beta": 0.7, "corr": 0.1, "composite": 70},
        {"ticker": "LOWER_BETA", "beta": 0.3, "corr": 0.1, "composite": 70},
    ]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )
    assert [r["ticker"] for r in result] == ["LOWER_BETA", "HIGHER_BETA"]


def test_rank_defensive_candidates_never_returns_actionable_for_below_floor():
    # No output field can read as a buy call for a sub-floor name.
    candidates = [{"ticker": "LOW", "beta": 0.5, "corr": 0.1, "composite": 10}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["actionable"] is False
    assert result["cost_note"] is None


def test_rank_defensive_candidates_composite_exactly_at_hold_floor():
    # composite == hold_floor exactly -> NOT below the floor (< is the gate,
    # not <=) -> meets_floor True, with a disclosed cost note since it's
    # still below buy_gate.
    candidates = [{"ticker": "ATFLOOR", "beta": 0.5, "corr": 0.1, "composite": _HOLD_FLOOR}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["meets_floor"] is True
    assert result["actionable"] is False
    assert result["cost_note"] is not None


def test_rank_defensive_candidates_composite_exactly_at_buy_gate():
    # composite == buy_gate exactly -> actionable True (>= is the gate).
    candidates = [{"ticker": "ATGATE", "beta": 0.5, "corr": 0.1, "composite": _BUY_GATE}]
    result = rank_defensive_candidates(
        candidates=candidates, target=_TARGET, hold_floor=_HOLD_FLOOR, buy_gate=_BUY_GATE,
    )[0]
    assert result["meets_floor"] is True
    assert result["actionable"] is True
    assert result["cost_note"] is None
