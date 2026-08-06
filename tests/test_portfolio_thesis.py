"""Tests for stock_analyzer/portfolio_thesis.py — the weekly "State of the
Portfolio" standing thesis (see docs/plans/state-of-portfolio-standing-thesis.md).

Locked invariants under test:
  - §5.8 no-point-forecast: claims/prose carry only portfolio-scoped enums/
    counts, never a per-ticker price/return/target field.
  - "AI narrates, never originates": no LLM import anywhere in the module.
  - Offline discipline: compose_thesis(None/falsy, ...) -> None; each claim
    degrades to "unavailable" independently on its OWN missing source.
  - Grading is a stability ledger, never a right/wrong score: grade_prior
    returns None with no prior; otherwise per-claim held/shifted/not_comparable
    only (never a single aggregate pass/fail).
  - Never raises on malformed/wrong-type input.
"""
from datetime import date

import pytest

from stock_analyzer import portfolio_thesis as pth
from stock_analyzer.constants import (
    COMPOSITE_BUY,
    PORTFOLIO_THESIS_BASELINE_LOOKBACK_DAYS,
    SECTOR_CEILING,
    SINGLE_NAME_CEILING,
)

TODAY = date(2026, 8, 6)  # a Thursday — ISO week 32 of 2026


def _full_bundle(**overrides):
    bundle = {
        "rag_label": "Monitor",
        "div_label": "Moderate",
        "structural_new_clusters": [],
        "holdings_scores": [70, 80, 40, 55, 90, 30, 60, 68],
        "buy_candidates": [],
    }
    bundle.update(overrides)
    return bundle


def _full_acct_gate(**overrides):
    g = {"max_name_wt": 10.0, "max_sector_wt": 20.0}
    g.update(overrides)
    return g


# ── PORTFOLIO_THESIS_BASELINE_LOOKBACK_DAYS sanity (constant exists, locked value) ──

def test_baseline_lookback_constant_is_14_days():
    assert PORTFOLIO_THESIS_BASELINE_LOOKBACK_DAYS == 14


# ── No-LLM invariant — enforced by import-absence ───────────────────────────

def test_module_source_has_no_llm_client_import():
    """AST-based check (not a bare substring match — the module's own docstring
    legitimately *mentions* "anthropic" in prose explaining the invariant, so
    a naive `"anthropic" not in src` would false-fail on its own documentation).
    Walks the actual import statements only. Also robust to sys.modules-based
    checks giving a false negative because some OTHER module in the same test
    session already imported anthropic/openai."""
    import ast
    import inspect

    src = inspect.getsource(pth)
    tree = ast.parse(src)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])
    assert "anthropic" not in imported_names
    assert "openai" not in imported_names


def test_module_import_does_not_pull_in_anthropic():
    """Belt-and-suspenders: importing this module alone must not require the
    anthropic package to be installed (it has zero LLM dependency)."""
    import importlib
    import sys
    # Fresh reload to prove nothing anthropic-related sneaks in transitively.
    if "stock_analyzer.portfolio_thesis" in sys.modules:
        importlib.reload(sys.modules["stock_analyzer.portfolio_thesis"])
    assert "anthropic" not in sys.modules


# ── compose_thesis offline discipline ───────────────────────────────────────

def test_compose_thesis_none_bundle_returns_none():
    assert pth.compose_thesis(None, _full_acct_gate(), {}, today=TODAY) is None


def test_compose_thesis_falsy_empty_dict_bundle_returns_none():
    assert pth.compose_thesis({}, _full_acct_gate(), {}, today=TODAY) is None


def test_compose_thesis_wrong_type_bundle_returns_none_not_raise():
    for bad in ["not a dict", 123, [1, 2, 3], object()]:
        assert pth.compose_thesis(bad, _full_acct_gate(), {}, today=TODAY) is None


def test_compose_thesis_valid_bundle_returns_full_shape():
    out = pth.compose_thesis(_full_bundle(), _full_acct_gate(), {}, today=TODAY)
    assert out is not None
    assert out["v"] == 1
    assert out["thesis_date"] == TODAY.isoformat()
    assert set(pth.CLAIM_KEYS) <= set(out["claims"].keys())
    assert isinstance(out["prose"], str) and out["prose"]
    iso_year, iso_week, _ = TODAY.isocalendar()
    assert out["iso_year"] == iso_year
    assert out["iso_week"] == iso_week


# ── Per-claim independence — one missing source degrades ONLY its own claim ──

def test_concentration_unavailable_when_acct_gate_none_others_still_resolve():
    out = pth.compose_thesis(_full_bundle(), None, {}, today=TODAY)
    assert out["claims"]["concentration"] == "unavailable"
    assert out["claims"]["risk_posture"] == "Monitor"
    assert out["claims"]["correlation_structure"] != "unavailable"
    assert out["claims"]["action_posture"] != "unavailable"
    assert isinstance(out["claims"]["holdings_health"], dict)


def test_risk_posture_unavailable_when_rag_label_missing_others_still_resolve():
    out = pth.compose_thesis(
        _full_bundle(rag_label=None), _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["risk_posture"] == "unavailable"
    assert out["claims"]["concentration"] != "unavailable"


def test_risk_posture_unavailable_on_unrecognized_label():
    out = pth.compose_thesis(
        _full_bundle(rag_label="Something Else"), _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["risk_posture"] == "unavailable"


def test_correlation_unavailable_when_div_label_missing():
    out = pth.compose_thesis(
        _full_bundle(div_label=None), _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["correlation_structure"] == "unavailable"
    assert out["claims"]["risk_posture"] != "unavailable"


def test_holdings_health_unavailable_when_scores_missing():
    out = pth.compose_thesis(
        _full_bundle(holdings_scores=None), _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["holdings_health"] == "unavailable"
    assert out["claims"]["risk_posture"] != "unavailable"


def test_holdings_health_unavailable_when_scores_empty_list():
    out = pth.compose_thesis(
        _full_bundle(holdings_scores=[]), _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["holdings_health"] == "unavailable"


def test_action_posture_unavailable_when_buy_candidates_missing():
    out = pth.compose_thesis(
        _full_bundle(buy_candidates=None), _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["action_posture"] == "unavailable"
    assert out["claims"]["risk_posture"] != "unavailable"


def test_action_posture_unavailable_when_reduce_calls_none():
    out = pth.compose_thesis(_full_bundle(), _full_acct_gate(), None, today=TODAY)
    assert out["claims"]["action_posture"] == "unavailable"
    assert out["claims"]["risk_posture"] != "unavailable"


# ── Claim classification correctness ────────────────────────────────────────

def test_concentration_within_when_below_both_ceilings():
    out = pth.compose_thesis(
        _full_bundle(), {"max_name_wt": 5.0, "max_sector_wt": 10.0}, {}, today=TODAY,
    )
    assert out["claims"]["concentration"] == "within"


def test_concentration_single_name_elevated_at_ceiling_boundary():
    out = pth.compose_thesis(
        _full_bundle(),
        {"max_name_wt": SINGLE_NAME_CEILING, "max_sector_wt": 5.0},
        {}, today=TODAY,
    )
    assert out["claims"]["concentration"] == "single_name_elevated"


def test_concentration_sector_elevated_at_ceiling_boundary():
    out = pth.compose_thesis(
        _full_bundle(),
        {"max_name_wt": 5.0, "max_sector_wt": SECTOR_CEILING},
        {}, today=TODAY,
    )
    assert out["claims"]["concentration"] == "sector_elevated"


def test_correlation_diversified_for_well_diversified_label():
    out = pth.compose_thesis(
        _full_bundle(div_label="Well Diversified"), _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["correlation_structure"] == "diversified"


def test_correlation_elevated_for_moderate_or_high_correlation_risk():
    for label in ("Moderate", "High Correlation Risk"):
        out = pth.compose_thesis(
            _full_bundle(div_label=label), _full_acct_gate(), {}, today=TODAY,
        )
        assert out["claims"]["correlation_structure"] == "elevated"


def test_correlation_concentrated_cluster_overrides_when_new_cluster_formed():
    out = pth.compose_thesis(
        _full_bundle(div_label="Well Diversified", structural_new_clusters=[{"tickers": ["A", "B"]}]),
        _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["correlation_structure"] == "concentrated_cluster"


def test_holdings_health_counts_are_correct():
    scores = [COMPOSITE_BUY, COMPOSITE_BUY - 1, COMPOSITE_BUY + 10, 10]
    out = pth.compose_thesis(
        _full_bundle(holdings_scores=scores), _full_acct_gate(), {}, today=TODAY,
    )
    health = out["claims"]["holdings_health"]
    assert health == {"n_buy_plus": 2, "n_below": 2, "n_total": 4}


def test_action_posture_de_risking_when_reduce_calls_present():
    out = pth.compose_thesis(
        _full_bundle(buy_candidates=[{"ticker": "AAA"}]),
        _full_acct_gate(), {"AAA": {"reason": "trim"}}, today=TODAY,
    )
    assert out["claims"]["action_posture"] == "de_risking"


def test_action_posture_deploying_when_buy_candidates_present_no_reduce():
    out = pth.compose_thesis(
        _full_bundle(buy_candidates=[{"ticker": "AAA"}]),
        _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["action_posture"] == "deploying"


def test_action_posture_holding_when_neither():
    out = pth.compose_thesis(
        _full_bundle(buy_candidates=[]), _full_acct_gate(), {}, today=TODAY,
    )
    assert out["claims"]["action_posture"] == "holding"


# ── §5.8 invariant — no per-ticker price/return field anywhere in output ────

def test_no_per_ticker_price_or_return_field_in_claims_or_prose():
    out = pth.compose_thesis(
        _full_bundle(buy_candidates=[{"ticker": "AAA", "price": 123.45}]),
        _full_acct_gate(), {"BBB": {"reason": "trim", "price": 55.0}}, today=TODAY,
    )
    claims_str = str(out["claims"])
    # No dollar-figure-looking price leaked into the claims dict.
    assert "123.45" not in claims_str
    assert "55.0" not in claims_str
    assert "AAA" not in claims_str
    assert "BBB" not in claims_str
    assert "AAA" not in out["prose"]
    assert "BBB" not in out["prose"]
    assert "123.45" not in out["prose"]


# ── prose composition sanity — deterministic, no hedging "will" language ───

def test_prose_contains_no_forward_forecast_language():
    out = pth.compose_thesis(_full_bundle(), _full_acct_gate(), {}, today=TODAY)
    assert " will " not in out["prose"].lower()


def test_prose_cites_engine_trust_only_when_firm_band_provided():
    out_with = pth.compose_thesis(
        _full_bundle(), _full_acct_gate(), {},
        engine_trust={"band": "firm", "acted_alpha": 3.2}, today=TODAY,
    )
    assert "Engine Track Record" in out_with["prose"]

    out_building = pth.compose_thesis(
        _full_bundle(), _full_acct_gate(), {},
        engine_trust={"band": "building", "acted_alpha": None}, today=TODAY,
    )
    assert "Engine Track Record" not in out_building["prose"]

    out_none = pth.compose_thesis(
        _full_bundle(), _full_acct_gate(), {}, engine_trust=None, today=TODAY,
    )
    assert "Engine Track Record" not in out_none["prose"]


# ── grade_prior — no prior -> None (never fabricate a grade) ────────────────

def test_grade_prior_none_when_no_prior():
    this_week = pth.compose_thesis(_full_bundle(), _full_acct_gate(), {}, today=TODAY)
    assert pth.grade_prior(this_week, None) is None


def test_grade_prior_none_when_prior_is_empty_dict():
    this_week = pth.compose_thesis(_full_bundle(), _full_acct_gate(), {}, today=TODAY)
    assert pth.grade_prior(this_week, {}) is None


def test_grade_prior_none_when_this_week_missing():
    prior = pth.compose_thesis(_full_bundle(), _full_acct_gate(), {}, today=TODAY)
    assert pth.grade_prior(None, prior) is None


# ── grade_prior — per-claim held/shifted/not_comparable, never aggregate ────

def test_grade_prior_held_on_identical_claims():
    this_week = pth.compose_thesis(_full_bundle(), _full_acct_gate(), {}, today=TODAY)
    prior = pth.compose_thesis(_full_bundle(), _full_acct_gate(), {}, today=TODAY)
    ledger = pth.grade_prior(this_week, prior)
    for key in pth.CLAIM_KEYS:
        assert ledger[key]["status"] == "held"
        assert ledger[key]["from"] == ledger[key]["to"]


def test_grade_prior_shifted_with_correct_from_to():
    prior = pth.compose_thesis(
        _full_bundle(rag_label="All Clear"), _full_acct_gate(), {}, today=TODAY,
    )
    this_week = pth.compose_thesis(
        _full_bundle(rag_label="Monitor"), _full_acct_gate(), {}, today=TODAY,
    )
    ledger = pth.grade_prior(this_week, prior)
    assert ledger["risk_posture"]["status"] == "shifted"
    assert ledger["risk_posture"]["from"] == "All Clear"
    assert ledger["risk_posture"]["to"] == "Monitor"


def test_grade_prior_not_comparable_when_either_side_unavailable_never_held_or_shifted():
    prior = pth.compose_thesis(
        _full_bundle(rag_label=None), _full_acct_gate(), {}, today=TODAY,
    )
    this_week = pth.compose_thesis(
        _full_bundle(rag_label="Monitor"), _full_acct_gate(), {}, today=TODAY,
    )
    ledger = pth.grade_prior(this_week, prior)
    assert ledger["risk_posture"]["status"] == "not_comparable"
    assert ledger["risk_posture"]["status"] not in ("held", "shifted")


def test_grade_prior_not_comparable_when_both_sides_unavailable():
    prior = pth.compose_thesis(
        _full_bundle(rag_label=None), _full_acct_gate(), {}, today=TODAY,
    )
    this_week = pth.compose_thesis(
        _full_bundle(rag_label=None), _full_acct_gate(), {}, today=TODAY,
    )
    ledger = pth.grade_prior(this_week, prior)
    assert ledger["risk_posture"]["status"] == "not_comparable"


def test_grade_prior_never_a_single_aggregate_pass_fail():
    """The ledger must be a per-claim dict keyed by CLAIM_KEYS — never a
    single bool/score summarizing all 5 claims at once."""
    this_week = pth.compose_thesis(_full_bundle(), _full_acct_gate(), {}, today=TODAY)
    prior = pth.compose_thesis(_full_bundle(rag_label="All Clear"), _full_acct_gate(), {}, today=TODAY)
    ledger = pth.grade_prior(this_week, prior)
    assert isinstance(ledger, dict)
    assert set(ledger.keys()) == set(pth.CLAIM_KEYS)
    for v in ledger.values():
        assert isinstance(v, dict)
        assert v["status"] in ("held", "shifted", "not_comparable")


def test_grade_prior_holdings_health_dict_equality_held():
    scores = [COMPOSITE_BUY + 5, COMPOSITE_BUY - 5]
    this_week = pth.compose_thesis(
        _full_bundle(holdings_scores=scores), _full_acct_gate(), {}, today=TODAY,
    )
    prior = pth.compose_thesis(
        _full_bundle(holdings_scores=scores), _full_acct_gate(), {}, today=TODAY,
    )
    ledger = pth.grade_prior(this_week, prior)
    assert ledger["holdings_health"]["status"] == "held"


def test_grade_prior_holdings_health_dict_shifted():
    this_week = pth.compose_thesis(
        _full_bundle(holdings_scores=[COMPOSITE_BUY + 5]), _full_acct_gate(), {}, today=TODAY,
    )
    prior = pth.compose_thesis(
        _full_bundle(holdings_scores=[COMPOSITE_BUY - 5]), _full_acct_gate(), {}, today=TODAY,
    )
    ledger = pth.grade_prior(this_week, prior)
    assert ledger["holdings_health"]["status"] == "shifted"


# ── never-raise on malformed/wrong-type input ───────────────────────────────

def test_grade_prior_never_raises_on_malformed_input():
    for bad_prior in ["not a dict", 123, [1, 2], object()]:
        assert pth.grade_prior({"claims": {}}, bad_prior) is None
    for bad_this in ["not a dict", 123, [1, 2], object()]:
        assert pth.grade_prior(bad_this, {"claims": {}}) is None


def test_grade_prior_never_raises_when_claims_key_missing_or_malformed():
    assert pth.grade_prior({}, {"claims": {}}) is None  # falsy this_week
    ledger = pth.grade_prior({"claims": "not a dict"}, {"claims": {"risk_posture": "Monitor"}})
    assert ledger is not None
    for v in ledger.values():
        assert v["status"] == "not_comparable"


def test_compose_thesis_never_raises_on_malformed_acct_gate_or_reduce_calls():
    for bad in ["nope", 123, [1, 2, 3], object()]:
        out = pth.compose_thesis(_full_bundle(), bad, bad, today=TODAY)
        assert out is not None  # bundle itself is valid, so compose still returns a dict
        assert out["claims"]["concentration"] == "unavailable"
        assert out["claims"]["action_posture"] == "unavailable"
