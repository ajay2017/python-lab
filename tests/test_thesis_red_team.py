"""
Tests for stock_analyzer/thesis_red_team.py.

Phase 1 (compute_erosion_score) had zero test coverage before this file —
backfilled here, same pattern as tests/test_structural_scanner.py's
blast_radius() backfill. Phase 2 additions (build_counter_evidence_inputs,
parse_counter_evidence_response, generate_counter_evidence) are pinned with
regression tests for the None-vs-[] contract and the all-or-nothing
validation bar established across 6 Opus review rounds in
docs/plans/thesis-red-team-phase2.md.
"""
import json

import pytest

from stock_analyzer.thesis_red_team import (
    compute_erosion_score,
    build_counter_evidence_inputs,
    parse_counter_evidence_response,
    generate_counter_evidence,
    pt_points_from_signal,
)


# ─── Phase 1: compute_erosion_score ──────────────────────────────────────────

def test_compute_erosion_score_intact_no_signals():
    result = compute_erosion_score(None, 0.0, 0.0, 0.0)
    assert result["label"] == "Intact"
    assert result["score"] == 0.0


def test_compute_erosion_score_exit_tier_dominates():
    result = compute_erosion_score("EXIT", 0.0, 0.0, 0.0)
    assert result["components"]["tier_pts"] == 30
    assert result["score"] == 30.0
    assert result["label"] == "Softening"


def test_compute_erosion_score_watch_tier_lower_than_trim_and_exit():
    watch = compute_erosion_score("WATCH", 0.0, 0.0, 0.0)
    trim  = compute_erosion_score("TRIM", 0.0, 0.0, 0.0)
    exit_ = compute_erosion_score("EXIT", 0.0, 0.0, 0.0)
    assert watch["score"] < trim["score"] < exit_["score"]


def test_compute_erosion_score_negative_rs_increases_score():
    weak_underperform   = compute_erosion_score(None, -5.0, 0.0, 0.0)
    strong_underperform = compute_erosion_score(None, -20.0, 0.0, 0.0)
    assert strong_underperform["score"] > weak_underperform["score"]


def test_compute_erosion_score_rs_component_clamps_to_weight_max():
    result = compute_erosion_score(None, -1000.0, 0.0, 0.0)
    assert result["components"]["rs_pts"] == 30  # _WEIGHTS["rs"]


def test_compute_erosion_score_positive_rs_never_negative_component():
    result = compute_erosion_score(None, 50.0, 0.0, 0.0)
    assert result["components"]["rs_pts"] == 0.0


def test_compute_erosion_score_composite_delta_clamps_to_weight_max():
    result = compute_erosion_score(None, 0.0, -1000.0, 0.0)
    assert result["components"]["composite_pts"] == 25  # _WEIGHTS["composite"]


def test_compute_erosion_score_rising_composite_never_negative_component():
    result = compute_erosion_score(None, 0.0, 50.0, 0.0)
    assert result["components"]["composite_pts"] == 0.0


def test_compute_erosion_score_pt_revision_clamps_to_weight_max():
    result = compute_erosion_score(None, 0.0, 0.0, 999.0)
    assert result["components"]["pt_pts"] == 15  # _WEIGHTS["pt"]


def test_compute_erosion_score_breaking_label_at_high_score():
    result = compute_erosion_score("EXIT", -20.0, -20.0, 15.0)
    assert result["label"] == "Breaking"
    assert result["score"] >= 75


def test_compute_erosion_score_never_raises_on_none_tier():
    # None tier is the documented "intact" case, not a missing-data error
    result = compute_erosion_score(None, 0.0, 0.0, 0.0)
    assert isinstance(result["score"], float)


# ─── F-169 Phase 2: pt_points_from_signal ────────────────────────────────────

def test_pt_points_from_signal_none_input_falls_back_to_inert_flat():
    assert pt_points_from_signal(None) == 7.0


def test_pt_points_from_signal_withheld_signal_falls_back_to_inert_flat():
    withheld = {"direction": None, "pct_change": None}
    assert pt_points_from_signal(withheld) == 7.0


def test_pt_points_from_signal_flat_direction_is_inert_flat():
    flat = {"direction": "flat", "pct_change": 0.0}
    assert pt_points_from_signal(flat) == 7.0


def test_pt_points_from_signal_up_direction_is_inert_flat():
    up = {"direction": "up", "pct_change": 0.10}
    assert pt_points_from_signal(up) == 7.0


def test_pt_points_from_signal_warn_boundary_is_exactly_seven():
    warn = {"direction": "cut", "pct_change": -0.07}
    assert pt_points_from_signal(warn) == pytest.approx(7.0)


def test_pt_points_from_signal_danger_boundary_is_exactly_fifteen():
    danger = {"direction": "cut", "pct_change": -0.15}
    assert pt_points_from_signal(danger) == pytest.approx(15.0)


def test_pt_points_from_signal_midpoint_interpolates_exactly():
    midpoint = {"direction": "cut", "pct_change": -0.11}  # halfway between -7% and -15%
    assert pt_points_from_signal(midpoint) == pytest.approx(11.0)


# ─── Phase 2: build_counter_evidence_inputs ──────────────────────────────────

def test_build_counter_evidence_inputs_returns_system_and_user_prompt():
    system_prompt, user_prompt = build_counter_evidence_inputs(
        ticker="NVDA", price=120.0, entry_price=100.0, position_age_days=30,
        user_thesis="AI capex supercycle", premortem_commitment=None,
        tier="WATCH", rs_vs_spy=-5.0, composite_delta=-3.0,
    )
    assert "bear-case analyst" in system_prompt
    assert "NVDA" in user_prompt
    assert "AI capex supercycle" in user_prompt


def test_build_counter_evidence_inputs_omits_none_price():
    _, user_prompt = build_counter_evidence_inputs(
        ticker="NVDA", price=None, entry_price=100.0, position_age_days=30,
        user_thesis="thesis", premortem_commitment=None,
        tier=None, rs_vs_spy=1.0, composite_delta=None,
    )
    assert "Current price" not in user_prompt
    assert "Entry price" in user_prompt


def test_build_counter_evidence_inputs_omits_none_composite_delta():
    _, user_prompt = build_counter_evidence_inputs(
        ticker="NVDA", price=100.0, entry_price=90.0, position_age_days=10,
        user_thesis="thesis", premortem_commitment=None,
        tier=None, rs_vs_spy=1.0, composite_delta=None,
    )
    assert "not enough trading-day history" in user_prompt


def test_build_counter_evidence_inputs_omits_none_rs():
    _, user_prompt = build_counter_evidence_inputs(
        ticker="NVDA", price=100.0, entry_price=90.0, position_age_days=10,
        user_thesis="thesis", premortem_commitment=None,
        tier=None, rs_vs_spy=None, composite_delta=1.0,
    )
    assert "not available today" in user_prompt


def test_build_counter_evidence_inputs_includes_premortem_when_present():
    _, user_prompt = build_counter_evidence_inputs(
        ticker="NVDA", price=100.0, entry_price=90.0, position_age_days=10,
        user_thesis="thesis", premortem_commitment="I'd exit if margins compress",
        tier=None, rs_vs_spy=1.0, composite_delta=1.0,
    )
    assert "margins compress" in user_prompt


def test_build_counter_evidence_inputs_no_tier_says_none_active():
    _, user_prompt = build_counter_evidence_inputs(
        ticker="NVDA", price=100.0, entry_price=90.0, position_age_days=10,
        user_thesis="thesis", premortem_commitment=None,
        tier=None, rs_vs_spy=1.0, composite_delta=1.0,
    )
    assert "Deterioration tier: none active" in user_prompt


def test_build_counter_evidence_inputs_never_mentions_erosion_score():
    # Regression test for Round 2's blocking finding: the aggregate erosion
    # score/label must never reach the prompt (it launders the pt_pts
    # placeholder). This function's signature has no such parameter at all.
    _, user_prompt = build_counter_evidence_inputs(
        ticker="NVDA", price=100.0, entry_price=90.0, position_age_days=10,
        user_thesis="thesis", premortem_commitment=None,
        tier="EXIT", rs_vs_spy=-20.0, composite_delta=-10.0,
    )
    assert "erosion" not in user_prompt.lower()


# ─── Phase 2: parse_counter_evidence_response ────────────────────────────────

def test_parse_counter_evidence_response_valid_list():
    raw = json.dumps([
        {"claim": "RS down 17pp vs SPY", "severity": "high", "signal_basis": "rs_vs_spy = -17.0pp"},
    ])
    result = parse_counter_evidence_response(raw)
    assert result == [{"claim": "RS down 17pp vs SPY", "severity": "high", "signal_basis": "rs_vs_spy = -17.0pp"}]


def test_parse_counter_evidence_response_empty_list_is_valid_not_none():
    # The core Round 1 contract: [] is a valid "no grounded bear case" result,
    # distinct from None (call failure). Must not collapse into None.
    result = parse_counter_evidence_response("[]")
    assert result == []
    assert result is not None


def test_parse_counter_evidence_response_malformed_json_returns_none():
    assert parse_counter_evidence_response("not json at all") is None


def test_parse_counter_evidence_response_empty_string_returns_none():
    assert parse_counter_evidence_response("") is None


def test_parse_counter_evidence_response_bad_severity_drops_whole_response():
    raw = json.dumps([
        {"claim": "valid claim", "severity": "critical", "signal_basis": "some evidence"},
    ])
    assert parse_counter_evidence_response(raw) is None


def test_parse_counter_evidence_response_missing_field_drops_whole_response():
    raw = json.dumps([
        {"claim": "valid claim", "severity": "high"},  # missing signal_basis
    ])
    assert parse_counter_evidence_response(raw) is None


def test_parse_counter_evidence_response_one_bad_item_drops_all_all_or_nothing():
    raw = json.dumps([
        {"claim": "good claim", "severity": "high", "signal_basis": "real evidence"},
        {"claim": "", "severity": "low", "signal_basis": "evidence"},  # empty claim
    ])
    assert parse_counter_evidence_response(raw) is None


def test_parse_counter_evidence_response_too_many_items_returns_none():
    raw = json.dumps([
        {"claim": f"claim {i}", "severity": "low", "signal_basis": f"evidence {i}"}
        for i in range(4)
    ])
    assert parse_counter_evidence_response(raw) is None


def test_parse_counter_evidence_response_strips_code_fences():
    raw = "```json\n" + json.dumps([
        {"claim": "claim", "severity": "medium", "signal_basis": "evidence"},
    ]) + "\n```"
    result = parse_counter_evidence_response(raw)
    assert result == [{"claim": "claim", "severity": "medium", "signal_basis": "evidence"}]


def test_parse_counter_evidence_response_not_a_list_returns_none():
    assert parse_counter_evidence_response(json.dumps({"claim": "not a list"})) is None


# ─── Phase 2: generate_counter_evidence — fail-open contract ────────────────

def test_generate_counter_evidence_no_api_key_returns_none():
    inputs = ("system prompt", "user prompt")
    assert generate_counter_evidence("NVDA", inputs, "") is None


def test_generate_counter_evidence_none_api_key_returns_none():
    inputs = ("system prompt", "user prompt")
    assert generate_counter_evidence("NVDA", inputs, None) is None
