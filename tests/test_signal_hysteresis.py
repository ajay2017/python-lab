"""Regression tests for stock_analyzer/signal_hysteresis.py — the calm-advisor
"steady vs yesterday" annotator (Tier 2, Phase 2C). Purely additive/cosmetic:
NEVER suppresses, reorders, or adds a pick, only attaches a `_hysteresis`
marker. Pure logic, no I/O. See docs/plans/test-automation.md for scope.
"""
import pytest

from stock_analyzer import signal_hysteresis as sh
from stock_analyzer.constants import HYSTERESIS_COMPOSITE_DELTA


# ── _pick_composite ────────────────────────────────────────────────────────

def test_pick_composite_prefers_composite_score():
    assert sh._pick_composite({"composite_score": 70.0, "score": 50.0}) == 70.0


def test_pick_composite_falls_back_to_score():
    assert sh._pick_composite({"score": 55.0}) == 55.0


def test_pick_composite_falls_back_to_total():
    assert sh._pick_composite({"total": 42.0}) == 42.0


def test_pick_composite_none_when_no_fields():
    assert sh._pick_composite({}) is None


def test_pick_composite_skips_non_positive_values():
    assert sh._pick_composite({"composite_score": 0.0, "score": 60.0}) == 60.0
    assert sh._pick_composite({"composite_score": -5.0, "score": 60.0}) == 60.0


def test_pick_composite_skips_unparseable_value():
    assert sh._pick_composite({"composite_score": "n/a", "score": 60.0}) == 60.0


def test_pick_composite_skips_none_value_for_a_key():
    assert sh._pick_composite({"composite_score": None, "score": 60.0}) == 60.0


# ── _pick_verdict ──────────────────────────────────────────────────────────

def test_pick_verdict_from_xref():
    assert sh._pick_verdict({"xref": {"verdict": "Confirmed"}}) == "confirmed"


def test_pick_verdict_from_bare_verdict_key():
    assert sh._pick_verdict({"verdict": "Reject"}) == "reject"


def test_pick_verdict_xref_takes_priority_over_bare_verdict():
    assert sh._pick_verdict({"xref": {"verdict": "Confirmed"}, "verdict": "Reject"}) == "confirmed"


def test_pick_verdict_empty_string_when_unknown():
    assert sh._pick_verdict({}) == ""
    assert sh._pick_verdict({"xref": {}}) == ""


# ── apply_hysteresis ───────────────────────────────────────────────────────

def test_apply_hysteresis_empty_today_picks_returns_as_is():
    result = sh.apply_hysteresis([], {"AAPL": {"composite": 70.0, "verdict": "confirmed"}})
    assert result == []


def test_apply_hysteresis_unparseable_delta_returns_untouched_not_raises():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": 70.0, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior, delta="not-a-number")
    assert "_hysteresis" not in result[0]


def test_apply_hysteresis_empty_prior_snapshot_returns_untouched():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    result = sh.apply_hysteresis(picks, {})
    assert "_hysteresis" not in result[0]


def test_apply_hysteresis_marks_steady_pick_within_delta():
    picks = [{"ticker": "AAPL", "composite_score": 70.0, "xref": {"verdict": "confirmed"}}]
    prior = {"AAPL": {"composite": 68.0, "verdict": "confirmed"}}
    result = sh.apply_hysteresis(picks, prior)
    assert result[0]["_hysteresis"] == {"stable": True, "note": "Steady vs yesterday"}


def test_apply_hysteresis_no_mark_when_ticker_not_in_prior_snapshot():
    picks = [{"ticker": "MSFT", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": 68.0, "verdict": "confirmed"}}
    result = sh.apply_hysteresis(picks, prior)
    assert "_hysteresis" not in result[0]


def test_apply_hysteresis_ticker_matching_is_case_insensitive():
    picks = [{"ticker": "aapl", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": 68.0, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert result[0]["_hysteresis"]["stable"] is True


def test_apply_hysteresis_no_mark_when_ticker_missing_or_blank():
    picks = [{"composite_score": 70.0}, {"ticker": "  ", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": 68.0, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert all("_hysteresis" not in p for p in result)


def test_apply_hysteresis_no_mark_when_today_composite_unavailable():
    picks = [{"ticker": "AAPL"}]  # no composite_score/score/total
    prior = {"AAPL": {"composite": 68.0, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert "_hysteresis" not in result[0]


def test_apply_hysteresis_no_mark_when_prior_composite_is_none():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": None, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert "_hysteresis" not in result[0]


def test_apply_hysteresis_delta_boundary_exactly_at_band_is_steady():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": 70.0 - HYSTERESIS_COMPOSITE_DELTA, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert result[0]["_hysteresis"]["stable"] is True


def test_apply_hysteresis_delta_just_beyond_band_not_steady():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": 70.0 - HYSTERESIS_COMPOSITE_DELTA - 0.01, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert "_hysteresis" not in result[0]


def test_apply_hysteresis_custom_delta_overrides_default():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": 65.0, "verdict": ""}}
    # 5-point gap: not steady under the default 4.0 delta...
    assert "_hysteresis" not in sh.apply_hysteresis(list(picks), prior)[0]
    # ...but is steady under a wider custom delta.
    result = sh.apply_hysteresis(picks, prior, delta=10.0)
    assert result[0]["_hysteresis"]["stable"] is True


def test_apply_hysteresis_prior_composite_as_string_is_coerced():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": "68.0", "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert result[0]["_hysteresis"]["stable"] is True


def test_apply_hysteresis_prior_composite_unparseable_skips_pick():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": "not-a-number", "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert "_hysteresis" not in result[0]


def test_apply_hysteresis_verdict_mismatch_blocks_even_within_delta():
    picks = [{"ticker": "AAPL", "composite_score": 70.0, "xref": {"verdict": "confirmed"}}]
    prior = {"AAPL": {"composite": 69.0, "verdict": "watch"}}
    result = sh.apply_hysteresis(picks, prior)
    assert "_hysteresis" not in result[0]


def test_apply_hysteresis_unknown_verdict_on_either_side_does_not_block():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]  # no verdict at all
    prior = {"AAPL": {"composite": 69.0, "verdict": "watch"}}
    result = sh.apply_hysteresis(picks, prior)
    assert result[0]["_hysteresis"]["stable"] is True


def test_apply_hysteresis_both_verdicts_unknown_does_not_block():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    prior = {"AAPL": {"composite": 69.0, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert result[0]["_hysteresis"]["stable"] is True


def test_apply_hysteresis_verdict_match_is_case_insensitive():
    picks = [{"ticker": "AAPL", "composite_score": 70.0, "xref": {"verdict": "CONFIRMED"}}]
    prior = {"AAPL": {"composite": 69.0, "verdict": "Confirmed"}}
    result = sh.apply_hysteresis(picks, prior)
    assert result[0]["_hysteresis"]["stable"] is True


def test_apply_hysteresis_mixed_batch_only_qualifying_pick_marked():
    picks = [
        {"ticker": "AAPL", "composite_score": 70.0},     # steady
        {"ticker": "MSFT", "composite_score": 90.0},     # big jump vs prior 50 -> not steady
        {"ticker": "XOM", "composite_score": 60.0},      # not in prior snapshot
    ]
    prior = {
        "AAPL": {"composite": 68.0, "verdict": ""},
        "MSFT": {"composite": 50.0, "verdict": ""},
    }
    result = sh.apply_hysteresis(picks, prior)
    assert result[0].get("_hysteresis", {}).get("stable") is True
    assert "_hysteresis" not in result[1]
    assert "_hysteresis" not in result[2]


def test_apply_hysteresis_returns_same_list_object_for_chaining():
    picks = [{"ticker": "AAPL", "composite_score": 70.0}]
    result = sh.apply_hysteresis(picks, {"AAPL": {"composite": 70.0, "verdict": ""}})
    assert result is picks


def test_apply_hysteresis_uses_score_field_for_add_to_winner_picks():
    # add-to-winner picks carry composite under "score", not "composite_score".
    picks = [{"ticker": "AAPL", "score": 70.0}]
    prior = {"AAPL": {"composite": 69.0, "verdict": ""}}
    result = sh.apply_hysteresis(picks, prior)
    assert result[0]["_hysteresis"]["stable"] is True
