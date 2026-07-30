"""
Tests for stock_analyzer/catalyst_stress.py — Catalyst-Specific Stress (D4):
weak-point ticker union, the two independently-ranked macro/earnings
candidate lists, evidence assembly, evidence formatting, and the narrative
LLM call. Zero coverage before this batch. `generate_catalyst_narrative`'s
real Anthropic call is exercised via a fake `sys.modules["anthropic"]`
module for one success and one malformed-JSON round trip; its TWO distinct
guards (no api_key; no top_macro/top_earnings at all) are both tested
without mocking since each returns before `import anthropic` runs.
"""
import sys
import types
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import catalyst_stress as cs


# ─── fake anthropic module helper ────────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    def create(self, **kwargs):
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text, **kwargs):
        self.messages = _FakeMessages(response_text)


def _install_fake_anthropic(response_text):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _FakeClient(response_text)
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


# ─── _weak_point_tickers ──────────────────────────────────────────────────────

def test_weak_point_tickers_both_none_returns_empty_set():
    assert cs._weak_point_tickers(None, None) == set()


def test_weak_point_tickers_both_empty_list_returns_empty_set():
    assert cs._weak_point_tickers([], []) == set()


def test_weak_point_tickers_union_of_all_sources_uppercased():
    blast = [{
        "shocked_ticker": "aapl",
        "contributing_tickers": [{"ticker": "msft"}, {"ticker": "goog"}],
    }]
    clusters = [{"tickers": ["nvda", "amzn"]}]
    result = cs._weak_point_tickers(blast, clusters)
    assert result == {"AAPL", "MSFT", "GOOG", "NVDA", "AMZN"}


def test_weak_point_tickers_malformed_input_degrades_to_empty_set():
    # a non-list passed where a list is expected -> iterating raises -> caught
    result = cs._weak_point_tickers("not-a-list", None)
    assert result == set()


# ─── rank_catalyst_threats ────────────────────────────────────────────────────

def _port_df():
    return pd.DataFrame({"Ticker": ["AAA", "BBB"], "Weight (%)": [10.0, 5.0]})


def test_rank_catalyst_threats_no_weak_points_returns_empty_both():
    result = cs.rank_catalyst_threats([], [], [], [], _port_df(), window_days=14)
    assert result == {"macro": [], "earnings": []}


def _blast_with_weak_points():
    return [{"shocked_ticker": "AAA", "contributing_tickers": []}]


def test_rank_catalyst_threats_macro_excludes_non_high_impact():
    today = date(2026, 7, 29)
    events = [{"event": "CPI", "impact": "MEDIUM", "category": "Inflation",
               "date": date(2026, 8, 1), "affected_tickers": ["AAA"]}]
    result = cs.rank_catalyst_threats(events, [], _blast_with_weak_points(), [], _port_df(),
                                       window_days=14, today=today)
    assert result["macro"] == []


def test_rank_catalyst_threats_macro_excludes_all_sector_category(monkeypatch):
    monkeypatch.setattr(cs, "is_all_sector_category", lambda cat: cat == "FAKE_ALL")
    today = date(2026, 7, 29)
    events = [{"event": "FOMC", "impact": "HIGH", "category": "FAKE_ALL",
               "date": date(2026, 8, 1), "affected_tickers": ["AAA"]}]
    result = cs.rank_catalyst_threats(events, [], _blast_with_weak_points(), [], _port_df(),
                                       window_days=14, today=today)
    assert result["macro"] == []


def test_rank_catalyst_threats_macro_excludes_outside_window():
    today = date(2026, 7, 29)
    events = [{"event": "CPI", "impact": "HIGH", "category": "Inflation",
               "date": date(2026, 9, 1), "affected_tickers": ["AAA"]}]
    result = cs.rank_catalyst_threats(events, [], _blast_with_weak_points(), [], _port_df(),
                                       window_days=14, today=today)
    assert result["macro"] == []


def test_rank_catalyst_threats_macro_excludes_no_overlap():
    today = date(2026, 7, 29)
    events = [{"event": "CPI", "impact": "HIGH", "category": "Inflation",
               "date": date(2026, 8, 1), "affected_tickers": ["ZZZ"]}]
    result = cs.rank_catalyst_threats(events, [], _blast_with_weak_points(), [], _port_df(),
                                       window_days=14, today=today)
    assert result["macro"] == []


def test_rank_catalyst_threats_macro_scores_by_weight_and_sorts_desc():
    today = date(2026, 7, 29)
    events = [
        {"event": "Weak", "impact": "HIGH", "category": "Inflation",
         "date": date(2026, 8, 1), "affected_tickers": ["BBB"]},
        {"event": "Strong", "impact": "HIGH", "category": "Inflation",
         "date": date(2026, 8, 2), "affected_tickers": ["AAA"]},
    ]
    blast = [{"shocked_ticker": "AAA", "contributing_tickers": [{"ticker": "BBB"}]}]
    result = cs.rank_catalyst_threats(events, [], blast, [], _port_df(),
                                       window_days=14, today=today)
    assert [c["event"] for c in result["macro"]] == ["Strong", "Weak"]
    assert result["macro"][0]["score"] == 10.0
    assert result["macro"][1]["score"] == 5.0


def test_rank_catalyst_threats_earnings_soonest_date_wins():
    today = date(2026, 7, 29)
    events = [
        {"ticker": "AAA", "date": "2026-08-10"},
        {"ticker": "AAA", "date": "2026-08-02"},
    ]
    blast = [{"shocked_ticker": "AAA", "contributing_tickers": []}]
    result = cs.rank_catalyst_threats([], events, blast, [], _port_df(),
                                       window_days=14, today=today)
    assert result["earnings"][0]["date"] == "2026-08-02"


def test_rank_catalyst_threats_earnings_excludes_non_weak_point_ticker():
    today = date(2026, 7, 29)
    events = [{"ticker": "ZZZ", "date": "2026-08-02"}]
    result = cs.rank_catalyst_threats([], events, _blast_with_weak_points(), [], _port_df(),
                                       window_days=14, today=today)
    assert result["earnings"] == []


def test_rank_catalyst_threats_earnings_excludes_outside_window():
    today = date(2026, 7, 29)
    events = [{"ticker": "AAA", "date": "2026-09-15"}]
    result = cs.rank_catalyst_threats([], events, _blast_with_weak_points(), [], _port_df(),
                                       window_days=14, today=today)
    assert result["earnings"] == []


def test_rank_catalyst_threats_earnings_score_uses_abs_of_negative_impact():
    today = date(2026, 7, 29)
    events = [{"ticker": "AAA", "date": "2026-08-02"}]
    blast = [{"shocked_ticker": "AAA", "portfolio_impact_pct": -8.0, "contributing_tickers": []}]
    result = cs.rank_catalyst_threats([], events, blast, [], _port_df(),
                                       window_days=14, today=today)
    assert result["earnings"][0]["score"] == pytest.approx(10.0 + 8.0)


def test_rank_catalyst_threats_earnings_score_zero_or_below_excluded():
    today = date(2026, 7, 29)
    events = [{"ticker": "CCC", "date": "2026-08-02"}]
    port_df = pd.DataFrame({"Ticker": ["CCC"], "Weight (%)": [0.0]})
    blast = [{"shocked_ticker": "CCC", "portfolio_impact_pct": 0.0, "contributing_tickers": []}]
    result = cs.rank_catalyst_threats([], events, blast, [], port_df,
                                       window_days=14, today=today)
    assert result["earnings"] == []


def test_rank_catalyst_threats_earnings_sorted_desc():
    today = date(2026, 7, 29)
    events = [{"ticker": "AAA", "date": "2026-08-02"}, {"ticker": "BBB", "date": "2026-08-03"}]
    blast = [{"shocked_ticker": "AAA", "contributing_tickers": []},
             {"shocked_ticker": "BBB", "contributing_tickers": []}]
    result = cs.rank_catalyst_threats([], events, blast, [], _port_df(),
                                       window_days=14, today=today)
    assert [e["ticker"] for e in result["earnings"]] == ["AAA", "BBB"]


# ─── build_catalyst_stress_inputs ─────────────────────────────────────────────

def test_build_catalyst_stress_inputs_empty_ranked_none_top():
    result = cs.build_catalyst_stress_inputs({"macro": [], "earnings": []}, [], [])
    assert result["top_macro"] is None
    assert result["top_earnings"] is None


def test_build_catalyst_stress_inputs_picks_first_item():
    ranked = {"macro": [{"event": "A"}, {"event": "B"}], "earnings": [{"ticker": "X"}]}
    result = cs.build_catalyst_stress_inputs(ranked, [], [])
    assert result["top_macro"] == {"event": "A"}
    assert result["top_earnings"] == {"ticker": "X"}


# ─── _format_evidence ──────────────────────────────────────────────────────────

def test_format_evidence_none_candidates_render_fallback_strings():
    evidence = {"top_macro": None, "top_earnings": None, "blast_radius": [], "clusters": []}
    text = cs._format_evidence(evidence)
    assert "Macro candidate: none within the window with weak-point overlap." in text
    assert "Earnings candidate: none within the window with weak-point overlap." in text


def test_format_evidence_blast_radius_and_clusters_only_render_when_nonempty():
    evidence = {"top_macro": None, "top_earnings": None, "blast_radius": [], "clusters": []}
    text = cs._format_evidence(evidence)
    assert "Blast radius" not in text
    assert "Correlation clusters" not in text

    evidence2 = {
        "top_macro": None, "top_earnings": None,
        "blast_radius": [{"shocked_ticker": "AAA", "shock_pct": -10, "portfolio_impact_pct": -3.5}],
        "clusters": [{"size": 2, "tickers": ["AAA", "BBB"], "avg_internal_corr": 0.8, "combined_weight_pct": 15.0}],
    }
    text2 = cs._format_evidence(evidence2)
    assert "Blast radius" in text2
    assert "Correlation clusters" in text2


# ─── generate_catalyst_narrative ──────────────────────────────────────────────

def test_generate_catalyst_narrative_no_api_key_returns_none():
    evidence = {"top_macro": {"event": "CPI"}, "top_earnings": None}
    assert cs.generate_catalyst_narrative(evidence, api_key="") is None


def test_generate_catalyst_narrative_no_candidates_returns_none_without_import():
    evidence = {"top_macro": None, "top_earnings": None}
    # no fake anthropic installed -- if the guard failed to short-circuit,
    # `import anthropic` would raise ModuleNotFoundError instead of a clean None
    assert cs.generate_catalyst_narrative(evidence, api_key="fake-key") is None


def test_generate_catalyst_narrative_valid_response_round_trip():
    evidence = {"top_macro": {"event": "CPI", "date": "2026-08-01", "days_out": 3,
                              "category": "Inflation", "overlap_tickers": ["AAA"], "score": 10.0},
                "top_earnings": None, "blast_radius": [], "clusters": []}
    _install_fake_anthropic('{"narrative": "CPI print threatens AAA exposure."}')
    result = cs.generate_catalyst_narrative(evidence, api_key="fake-key")
    assert result == {"narrative": "CPI print threatens AAA exposure."}


def test_generate_catalyst_narrative_malformed_json_returns_none():
    evidence = {"top_macro": {"event": "CPI", "date": "2026-08-01", "days_out": 3,
                              "category": "Inflation", "overlap_tickers": ["AAA"], "score": 10.0},
                "top_earnings": None, "blast_radius": [], "clusters": []}
    _install_fake_anthropic("not json at all")
    result = cs.generate_catalyst_narrative(evidence, api_key="fake-key")
    assert result is None


# ─── _parse_catalyst_narrative_response ───────────────────────────────────────

def test_parse_catalyst_narrative_response_empty_or_none_returns_none():
    assert cs._parse_catalyst_narrative_response("") is None
    assert cs._parse_catalyst_narrative_response(None) is None


def test_parse_catalyst_narrative_response_non_dict_returns_none():
    assert cs._parse_catalyst_narrative_response("[1, 2]") is None


def test_parse_catalyst_narrative_response_missing_narrative_returns_none():
    assert cs._parse_catalyst_narrative_response('{"other": "x"}') is None


def test_parse_catalyst_narrative_response_non_string_narrative_returns_none():
    assert cs._parse_catalyst_narrative_response('{"narrative": 123}') is None


def test_parse_catalyst_narrative_response_blank_narrative_returns_none():
    assert cs._parse_catalyst_narrative_response('{"narrative": "   "}') is None


def test_parse_catalyst_narrative_response_valid_strips_text():
    result = cs._parse_catalyst_narrative_response('{"narrative": "  Hello.  "}')
    assert result == {"narrative": "Hello."}
