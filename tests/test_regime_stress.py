"""Tests for stock_analyzer/regime_stress.py — composes blast-radius,
correlation-cluster, macro-regime, and cross-asset evidence into a Haiku
prompt, and parses the resulting compound-scenario JSON. `generate_regime_
scenario`'s real Anthropic call is never exercised -- only its `not api_key`
early return, which is reached (and returns) before `import anthropic` runs
(confirmed by reading the source: the import sits inside the try block,
after the guard clause).
"""
import json

from stock_analyzer import regime_stress as rs


# ─── build_regime_scenario_inputs — all-empty defaults ──────────────────────

def test_build_inputs_all_none_produces_empty_but_valid_shape():
    result = rs.build_regime_scenario_inputs(None, None, None, None, None)
    assert result == {
        "blast_radius": [],
        "clusters": [],
        "regime": {"label": None, "fed_trend": None, "cpi_yoy": None, "confidence": 0, "signals": []},
        "cross_asset": {"label": None, "score": None},
        "factor_tilt": None,
    }


# ─── build_regime_scenario_inputs — regime extraction ───────────────────────

def test_build_inputs_extracts_exact_regime_fields_and_lists_signals():
    regime_data = {
        "label": "Rising Rates / Tightening",
        "fed_trend": "hawkish",
        "cpi_yoy": 3.2,
        "signals": (("VIX Level", 20), ("Yield Curve", -30)),  # tuple, not list
    }
    result = rs.build_regime_scenario_inputs(None, None, regime_data, None, None)
    regime = result["regime"]
    assert regime["label"] == "Rising Rates / Tightening"
    assert regime["fed_trend"] == "hawkish"
    assert regime["cpi_yoy"] == 3.2
    assert regime["confidence"] == 0  # missing key defaults to 0
    assert regime["signals"] == [("VIX Level", 20), ("Yield Curve", -30)]
    assert isinstance(regime["signals"], list)


# ─── build_regime_scenario_inputs — cross_asset per-signal filtering ────────

def test_build_inputs_cross_asset_only_includes_available_sub_signals():
    cross_asset_data = {
        "label": "Stress",
        "score": 3,
        "summary": "should be excluded",
        "dollar": {"available": True, "label": "USD strength"},
        "credit": {"available": False, "label": "HYG"},
    }
    result = rs.build_regime_scenario_inputs(None, None, None, cross_asset_data, None)
    ca_evidence = result["cross_asset"]
    assert ca_evidence["label"] == "Stress"
    assert ca_evidence["score"] == 3
    assert "summary" not in ca_evidence
    assert "dollar" in ca_evidence
    assert "credit" not in ca_evidence


# ─── build_regime_scenario_inputs — factor_tilt passthrough ─────────────────

def test_build_inputs_factor_tilt_empty_dict_becomes_none():
    result = rs.build_regime_scenario_inputs(None, None, None, None, {})
    assert result["factor_tilt"] is None


def test_build_inputs_factor_tilt_truthy_passes_through():
    tilt = {"portfolio_tilt": {"growth": 0.5}}
    result = rs.build_regime_scenario_inputs(None, None, None, None, tilt)
    assert result["factor_tilt"] == tilt


# ─── build_regime_scenario_inputs — outer try/except fallback ───────────────

def test_build_inputs_malformed_regime_data_falls_back_gracefully():
    # regime_data=42 is truthy (so `regime_data or {}` keeps it as 42), and
    # `42.get(...)` raises AttributeError -- caught by the outer try/except.
    result = rs.build_regime_scenario_inputs(None, None, 42, None, None)
    assert result == {
        "blast_radius": [], "clusters": [], "regime": {},
        "cross_asset": {}, "factor_tilt": None,
    }


# ─── _format_evidence ────────────────────────────────────────────────────────

def test_format_evidence_empty_dict_does_not_raise():
    text = rs._format_evidence({})
    assert isinstance(text, str)
    assert "Blast radius: none computed." in text
    assert "Correlation clusters: none detected." in text


def test_format_evidence_blast_radius_caps_contributing_tickers_at_3():
    evidence = {
        "blast_radius": [{
            "shocked_ticker": "AAPL", "shock_pct": -10, "portfolio_impact_pct": -3.5,
            "contributing_tickers": [
                {"ticker": "MSFT", "corr": 0.8, "comove_pct": -2.0},
                {"ticker": "GOOG", "corr": 0.7, "comove_pct": -1.5},
                {"ticker": "AMZN", "corr": 0.6, "comove_pct": -1.0},
                {"ticker": "META", "corr": 0.5, "comove_pct": -0.5},
            ],
        }],
    }
    text = rs._format_evidence(evidence)
    assert "MSFT" in text and "GOOG" in text and "AMZN" in text
    assert "META" not in text


def test_format_evidence_renders_cluster_details():
    evidence = {
        "clusters": [{
            "size": 3, "tickers": ["AAPL", "MSFT", "GOOG"],
            "avg_internal_corr": 0.812, "combined_weight_pct": 24.567, "tier": "high",
        }],
    }
    text = rs._format_evidence(evidence)
    assert "3 positions (AAPL, MSFT, GOOG)" in text
    assert "0.81" in text
    assert "24.6" in text
    assert "tier=high" in text


def test_format_evidence_skips_malformed_signal_tuple():
    evidence = {
        "regime": {
            "label": "Neutral", "fed_trend": "neutral", "cpi_yoy": 3.0, "confidence": 50,
            "signals": [("VIX Level", 20), ["OnlyOneElement"]],
        },
    }
    text = rs._format_evidence(evidence)
    assert "VIX Level: 20" in text
    assert "OnlyOneElement" not in text


def test_format_evidence_cross_asset_only_renders_dicts_with_label():
    evidence = {
        "cross_asset": {
            "label": "Stress", "score": 2,
            "dollar": {"label": "USD strength", "detail": "5-day ROC 2.1%"},
            "credit": {"available": True},  # no "label" key -- skipped
        },
    }
    text = rs._format_evidence(evidence)
    assert "USD strength" in text
    assert "5-day ROC 2.1%" in text


def test_format_evidence_factor_tilt_dominant_by_absolute_value():
    evidence = {
        "factor_tilt": {
            "portfolio_tilt": {"growth": 0.3, "value": -0.6, "quality": None},
        },
    }
    text = rs._format_evidence(evidence)
    assert "leans value-tilted" in text
    assert "-0.60" in text


# ─── _parse_regime_scenario_response ─────────────────────────────────────────

def _evidence_with_signals():
    return {"regime": {"signals": [("VIX Level", 20), ("Credit Spreads", 350)]}}


def test_parse_response_empty_or_none_returns_none():
    assert rs._parse_regime_scenario_response("", {}) is None
    assert rs._parse_regime_scenario_response(None, {}) is None


def test_parse_response_valid_json():
    raw = json.dumps({"scenario_narrative": "A compound scenario.", "indicator_watchlist": ["VIX Level"]})
    result = rs._parse_regime_scenario_response(raw, _evidence_with_signals())
    assert result == {"scenario_narrative": "A compound scenario.", "indicator_watchlist": ["VIX Level"]}


def test_parse_response_markdown_fenced_json_is_stripped():
    raw = "```json\n" + json.dumps({"scenario_narrative": "Fenced.", "indicator_watchlist": []}) + "\n```"
    result = rs._parse_regime_scenario_response(raw, _evidence_with_signals())
    assert result["scenario_narrative"] == "Fenced."


def test_parse_response_garbage_wrapped_json_extracted_via_find_rfind():
    raw = "Here is my answer: " + json.dumps({"scenario_narrative": "Extracted.", "indicator_watchlist": []}) + " Thanks!"
    result = rs._parse_regime_scenario_response(raw, _evidence_with_signals())
    assert result["scenario_narrative"] == "Extracted."


def test_parse_response_missing_narrative_returns_none():
    raw = json.dumps({"indicator_watchlist": []})
    assert rs._parse_regime_scenario_response(raw, {}) is None


def test_parse_response_empty_narrative_returns_none():
    raw = json.dumps({"scenario_narrative": "   ", "indicator_watchlist": []})
    assert rs._parse_regime_scenario_response(raw, {}) is None


def test_parse_response_non_string_narrative_returns_none():
    raw = json.dumps({"scenario_narrative": 12345, "indicator_watchlist": []})
    assert rs._parse_regime_scenario_response(raw, {}) is None


def test_parse_response_indicator_watchlist_canonicalized_case_insensitive():
    raw = json.dumps({
        "scenario_narrative": "Watch these.",
        "indicator_watchlist": ["  vix level  ", "CREDIT SPREADS", "Unknown Indicator"],
    })
    result = rs._parse_regime_scenario_response(raw, _evidence_with_signals())
    assert result["indicator_watchlist"] == ["VIX Level", "Credit Spreads"]


def test_parse_response_non_list_indicator_watchlist_falls_back_to_empty():
    raw = json.dumps({"scenario_narrative": "Fine.", "indicator_watchlist": "not a list"})
    result = rs._parse_regime_scenario_response(raw, _evidence_with_signals())
    assert result["indicator_watchlist"] == []


# ─── generate_regime_scenario — api_key guard only ──────────────────────────

def test_generate_regime_scenario_no_api_key_returns_none():
    assert rs.generate_regime_scenario({"some": "evidence"}, api_key=None) is None
    assert rs.generate_regime_scenario({"some": "evidence"}, api_key="") is None


from stock_analyzer.util import factor_tilt_evidence_line  # noqa: E402


class TestFactorTiltAlwaysDisclosedInEvidence:
    """F-260 (2026-08-28) — the regime scenario is PERSISTED to
    regime_scenario_cache and served for the rest of the ET day, so a narrative
    written on silently-incomplete evidence becomes the day's reading. The
    producer of `_pi_factor_tilt_cache` is 🧩 Intelligence's Factor Tilt button;
    this consumer lives on 🔗 Risk Analysis, so absent is the COMMON case.
    """

    def _evidence(self, factor):
        return rs.build_regime_scenario_inputs(
            [], [], {"label": "Neutral"}, {}, factor
        )

    def test_factor_line_present_in_all_three_states(self):
        for factor in (None,
                       {"positions": [], "portfolio_tilt": {}, "n_included": 0},
                       {"portfolio_tilt": {"MTUM": 0.81}}):
            text = rs._format_evidence(self._evidence(factor))
            assert "Factor tilt:" in text, f"no factor line for {factor!r}"
            # Pin the CONTRACT, not the substring: the two consumers must emit
            # the shared helper's exact line. A re-inlined near-copy that
            # dropped the forbid-inference clause would pass the check above.
            assert text.endswith(factor_tilt_evidence_line(factor))

    def test_absent_factor_data_is_stated_not_omitted(self):
        text = rs._format_evidence(self._evidence(None))
        assert "NOT MEASURED" in text

    def test_not_measured_differs_from_measured_with_a_real_tilt(self):
        absent = rs._format_evidence(self._evidence(None))
        present = rs._format_evidence(
            self._evidence({"portfolio_tilt": {"MTUM": 0.81}})
        )
        assert absent != present
        assert "MTUM-tilted" in present and "MTUM-tilted" not in absent

    def test_system_prompt_instructs_the_model_on_the_absent_case(self):
        """The evidence line is only half the fix — the model must be told not
        to read absence as balance."""
        assert "NOT MEASURED" in rs._SCENARIO_SYSTEM
        assert "never treat its absence" in rs._SCENARIO_SYSTEM
