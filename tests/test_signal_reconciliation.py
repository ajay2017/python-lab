"""Regression tests for stock_analyzer/signal_reconciliation.py — the "central
authority for resolving conflicts" between the momentum/technical scanner
score and the full composite score, per its own module docstring: "Every
surface that displays a buy/skip recommendation calls reconcile_signals() so
the resolution is consistent" (Daily Briefing, Grow Today, Market Scanner,
Watchlist all share this one function). A regression here silently ripples
across every one of those surfaces at once, which is exactly the kind of
blast radius this suite prioritizes.

reconcile_signals()'s four verdict tiers (skip/caution/verify/go) are checked
in a specific PRECEDENCE order in the source (skip-on-composite-conflict,
then skip-on-negative-news, then caution-on-earnings, then verify-on-missing-
composite, then go, then a verify fallback) -- several tests here exist
specifically to pin that ORDER, not just each condition in isolation, since a
reordering would silently change which tier wins when two conditions overlap.
"""
from stock_analyzer.constants import COMPOSITE_BUY, COMPOSITE_HOLD
from stock_analyzer.signal_reconciliation import (
    classify_composite_direction,
    classify_signal_change,
    effective_verdict_bucket,
    lookup_composite,
    reconcile_signals,
)


# ── classify_composite_direction / _composite_class ──────────────────────────

def test_composite_direction_label_match_beats_score():
    # A "Strong Sell" label with a high numeric score must still classify as
    # sell -- label match takes precedence over the score boundary.
    assert classify_composite_direction("Strong Sell", 90.0) == "sell"


def test_composite_direction_buy_label():
    assert classify_composite_direction("Buy", None) == "buy"


def test_composite_direction_hold_label():
    assert classify_composite_direction("Hold", None) == "hold"


def test_composite_direction_falls_back_to_score_when_no_label():
    assert classify_composite_direction(None, COMPOSITE_BUY) == "buy"
    assert classify_composite_direction(None, COMPOSITE_HOLD - 1) == "sell"
    assert classify_composite_direction(None, COMPOSITE_HOLD) == "hold"


def test_composite_direction_unknown_with_no_label_or_score():
    assert classify_composite_direction(None, None) == "unknown"


# ── reconcile_signals: verdict precedence ────────────────────────────────────

def test_reconcile_skip_when_composite_contradicts_high_momentum():
    result = reconcile_signals(
        "AAA", momentum_score=80.0, momentum_signal="Buy",
        composite_score=40.0, composite_signal="Sell",
    )
    assert result["verdict"] == "skip"


def test_reconcile_no_skip_when_momentum_below_buy_threshold():
    # The composite-contradicts-momentum skip is gated on momentum_score >=
    # COMPOSITE_BUY -- a low-momentum pick with a Hold/Sell composite doesn't
    # "skip", it falls through to the mixed-conviction verify fallback.
    result = reconcile_signals(
        "AAA", momentum_score=COMPOSITE_BUY - 1, momentum_signal="Buy",
        composite_score=40.0, composite_signal="Hold",
    )
    assert result["verdict"] == "verify"


def test_reconcile_skip_on_negative_news_even_without_composite_conflict():
    result = reconcile_signals(
        "AAA", momentum_score=80.0, momentum_signal="Buy",
        composite_score=90.0, composite_signal="Strong Buy",
        news_sentiment=-0.9,
    )
    assert result["verdict"] == "skip"


def test_reconcile_composite_conflict_skip_wins_over_earnings_caution():
    # Precedence: the composite-contradiction skip check runs BEFORE the
    # earnings-caution check in the source, so it must win when both apply.
    result = reconcile_signals(
        "AAA", momentum_score=80.0, momentum_signal="Buy",
        composite_score=40.0, composite_signal="Sell",
        earnings_days=1,
    )
    assert result["verdict"] == "skip"


def test_reconcile_caution_when_earnings_imminent_and_composite_missing():
    # Precedence: earnings-caution runs BEFORE the missing-composite verify
    # check, so it must win over "verify" when both apply.
    result = reconcile_signals(
        "AAA", momentum_score=80.0, momentum_signal="Buy",
        earnings_days=2,
    )
    assert result["verdict"] == "caution"


def test_reconcile_verify_when_composite_missing_regardless_of_momentum():
    # The missing-composite verify branch is unconditional on momentum score.
    low = reconcile_signals("AAA", momentum_score=10.0, momentum_signal="Hold")
    high = reconcile_signals("AAA", momentum_score=95.0, momentum_signal="Buy")
    assert low["verdict"] == "verify"
    assert high["verdict"] == "verify"


def test_reconcile_go_when_composite_confirms_buy():
    result = reconcile_signals(
        "AAA", momentum_score=80.0, momentum_signal="Buy",
        composite_score=90.0, composite_signal="Strong Buy",
    )
    assert result["verdict"] == "go"


def test_reconcile_fallback_verify_when_composite_hold_and_no_skip_triggered():
    result = reconcile_signals(
        "AAA", momentum_score=COMPOSITE_BUY - 1, momentum_signal="Hold",
        composite_score=50.0, composite_signal="Hold",
    )
    assert result["verdict"] == "verify"
    assert result["label"] == "🔍 Verify — Mixed Conviction"


def test_reconcile_mover_uses_breakout_phrasing_not_momentum_score():
    result = reconcile_signals(
        "AAA", momentum_score=80.0, momentum_signal="Buy", is_mover=True,
        composite_score=90.0, composite_signal="Strong Buy",
    )
    assert "Breakout today" in result["one_liner"]
    assert "Momentum 80" not in result["one_liner"]


# ── lookup_composite ──────────────────────────────────────────────────────────

def _port_df(rows):
    import pandas as pd
    return pd.DataFrame(rows)


def test_lookup_composite_prefers_port_df_over_composites_dict():
    port_df = _port_df([{"Ticker": "AAA", "Signal": "Hold", "Score": 55.0}])
    composites = {"AAA": {"rec": {"label": "Strong Buy"}, "total": 90.0}}
    sig, scr = lookup_composite("AAA", port_df, composites)
    assert (sig, scr) == ("Hold", 55.0)


def test_lookup_composite_falls_back_to_composites_when_not_held():
    port_df = _port_df([{"Ticker": "BBB", "Signal": "Hold", "Score": 55.0}])
    composites = {"AAA": {"rec": {"label": "Strong Buy"}, "total": 90.0}}
    sig, scr = lookup_composite("AAA", port_df, composites)
    assert (sig, scr) == ("Strong Buy", 90.0)


def test_lookup_composite_returns_none_when_neither_source_has_it():
    port_df = _port_df([{"Ticker": "BBB", "Signal": "Hold", "Score": 55.0}])
    assert lookup_composite("AAA", port_df, None) == (None, None)
    assert lookup_composite("AAA", port_df, {}) == (None, None)


def test_lookup_composite_empty_port_df_row_falls_through_to_composites():
    # A held row with no Signal/Score at all (both falsy) must not
    # false-positive as "found" -- it should fall through to composites.
    port_df = _port_df([{"Ticker": "AAA", "Signal": None, "Score": None}])
    composites = {"AAA": {"rec": {"label": "Buy"}, "total": 70.0}}
    sig, scr = lookup_composite("AAA", port_df, composites)
    assert (sig, scr) == ("Buy", 70.0)


# ── classify_signal_change ────────────────────────────────────────────────────

def test_classify_signal_change_degraded():
    result = classify_signal_change("Strong Buy", "Sell")
    assert result == {"degraded": True, "improved": False}


def test_classify_signal_change_improved():
    result = classify_signal_change("Sell", "Buy")
    assert result == {"degraded": False, "improved": True}


def test_classify_signal_change_neither_when_signal_unchanged_direction():
    assert classify_signal_change("Hold", "Hold") == {"degraded": False, "improved": False}
    assert classify_signal_change("Buy", "Strong Buy") == {"degraded": False, "improved": False}


# ── effective_verdict_bucket ──────────────────────────────────────────────────
#
# Closes the daily_briefing._cross_reference() divergence documented in memory
# project_verdict_divergence: a summary count built from the raw legacy xref
# `verdict` field could disagree with the reconciled color/label/one-liner
# each candidate's own card renders. Every caller that TALLIES candidates by
# confidence must route through this function instead of reading `verdict`
# directly, so the count can never drift from what the cards show.

def test_effective_bucket_prefers_reconciled_go_over_legacy_mixed():
    # The exact divergence case: legacy says "mixed" (an analyst-revisions
    # conflict reconcile_signals never sees), but reconciled says "go".
    xref = {"verdict": "mixed", "verdict_reconciled": {"verdict": "go"}}
    assert effective_verdict_bucket(xref) == "confirmed"


def test_effective_bucket_reconciled_verify_maps_to_unverified():
    xref = {"verdict": "confirmed", "verdict_reconciled": {"verdict": "verify"}}
    assert effective_verdict_bucket(xref) == "unverified"


def test_effective_bucket_reconciled_caution_and_skip_map_to_conflicted():
    assert effective_verdict_bucket({"verdict_reconciled": {"verdict": "caution"}}) == "conflicted"
    assert effective_verdict_bucket({"verdict_reconciled": {"verdict": "skip"}}) == "conflicted"


def test_effective_bucket_falls_back_to_legacy_when_reconciled_missing():
    assert effective_verdict_bucket({"verdict": "confirmed"}) == "confirmed"
    assert effective_verdict_bucket({"verdict": "unverified"}) == "unverified"
    assert effective_verdict_bucket({"verdict": "conflicted"}) == "conflicted"
    assert effective_verdict_bucket({"verdict": "caution"}) == "conflicted"
    assert effective_verdict_bucket({"verdict": "mixed"}) == "conflicted"


def test_effective_bucket_defaults_to_unverified_on_empty_xref():
    assert effective_verdict_bucket({}) == "unverified"
    assert effective_verdict_bucket(None) == "unverified"  # type: ignore[arg-type]  -- exercises the function's own None guard


# ── momentum_available display flag ──────────────────────────────────────────
#
# docs/plans/signal-reconciliation-momentum-available.md — the add-winner path
# calls reconcile_signals() with a synthetic "momentum_score" that is really
# the composite compared against itself (daily_briefing._cross_reference's
# scanner_row_is_synthetic=True call site), so the GO one-liner used to claim
# "technical momentum and full-score analysis agree" when there was only ever
# one source. momentum_available=False rewrites the COPY only -- it must never
# change verdict/label/color/icon/composite_available, and must NEVER be read
# inside a branch CONDITION (the protective negative-news skip in particular).

import pytest


_INVARIANCE_CASES = [
    pytest.param(
        dict(momentum_score=80.0, momentum_signal="Buy",
             composite_score=90.0, composite_signal="Strong Buy"),
        id="go",
    ),
    pytest.param(
        dict(momentum_score=90.0, momentum_signal="Buy",
             composite_score=90.0, composite_signal="Strong Buy",
             news_sentiment=-0.9),
        id="skip_negative_news",
    ),
    pytest.param(
        dict(momentum_score=80.0, momentum_signal="Buy",
             composite_score=90.0, composite_signal="Strong Buy",
             earnings_days=2),
        id="caution_earnings",
    ),
    pytest.param(
        dict(momentum_score=80.0, momentum_signal="Buy",
             composite_score=40.0, composite_signal="Sell"),
        id="skip_composite_contradicts",
    ),
    pytest.param(
        dict(momentum_score=COMPOSITE_BUY - 1, momentum_signal="Hold",
             composite_score=50.0, composite_signal="Hold"),
        id="fallback_mixed_conviction",
    ),
]


@pytest.mark.parametrize("kwargs", _INVARIANCE_CASES)
def test_momentum_available_never_changes_verdict_fields(kwargs):
    # Core safety proof: momentum_available may only change the one_liner
    # COPY. verdict/label/color/icon/composite_available must be identical
    # whether momentum_available is True or False, for every reachable and
    # defensive branch.
    result_true  = reconcile_signals("AAA", momentum_available=True, **kwargs)
    result_false = reconcile_signals("AAA", momentum_available=False, **kwargs)
    for key in ("verdict", "label", "color", "icon", "composite_available"):
        assert result_true[key] == result_false[key], (
            f"{key} differs across momentum_available for case {kwargs!r}"
        )


def test_momentum_available_false_does_not_defeat_protective_negative_news_skip():
    # THE TRAP (docs/plans/signal-reconciliation-momentum-available.md §2).
    # momentum_score keeps gating the protective negative-news skip
    # regardless of momentum_available -- a "clean up the fabricated value"
    # fix that nulled/zeroed momentum_score itself would silently flip this
    # verdict from skip to go for every add-winner with negative news.
    result = reconcile_signals(
        "AAA", momentum_score=90.0, composite_score=90.0,
        composite_signal="Strong Buy", news_sentiment=-0.9,
        momentum_available=False,
    )
    assert result["verdict"] == "skip"
    assert result["label"] == "❌ Skip — Negative News"


def test_momentum_available_false_copy_never_fabricates_corroboration():
    _false_forbidden = (
        "technical momentum and full-score analysis agree",
        "technical momentum",
        "Momentum ",
        "agree",
    )
    for kwargs in [c.values[0] for c in _INVARIANCE_CASES]:
        result = reconcile_signals("AAA", momentum_available=False, **kwargs)
        one_liner = result["one_liner"]
        for phrase in _false_forbidden:
            assert phrase not in one_liner, (
                f"forbidden phrase {phrase!r} leaked into False-copy: {one_liner!r}"
            )

    go_result = reconcile_signals(
        "AAA", momentum_score=80.0, momentum_signal="Buy",
        composite_score=90.0, composite_signal="Strong Buy",
        momentum_available=False,
    )
    assert "no separate scanner momentum reading" in go_result["one_liner"]


def test_momentum_available_true_go_one_liner_unchanged_default_behaviour():
    # Regression: default (momentum_available=True, i.e. every existing call
    # site that doesn't pass the new kwarg) must be byte-identical to
    # pre-change behaviour.
    result = reconcile_signals(
        "AAA", momentum_score=80.0, momentum_signal="Buy",
        composite_score=90.0, composite_signal="Strong Buy",
    )
    assert "technical momentum and full-score analysis agree" in result["one_liner"]
