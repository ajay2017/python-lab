"""
Tests for stock_analyzer/missed_opportunity.py — O1 Missed-Opportunity
Pattern: price/composite band bucketing, corpus assembly from
distinct_missed(), prompt formatting, the two-layer-validated LLM pattern
call, and the pure-Python outcome-mix safeguard. Zero coverage before this
batch. `generate_missed_opportunity_patterns`'s real Anthropic call is
exercised via a fake `sys.modules["anthropic"]` module for one success and
one malformed-JSON round trip; the guard clauses return before `import
anthropic` runs and need no mocking.
"""
import sys
import types

import pytest

from stock_analyzer import missed_opportunity as mo
from stock_analyzer.constants import COMPOSITE_STRONG_BUY, COMPOSITE_BUY, COMPOSITE_HOLD


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


# ─── _price_band ──────────────────────────────────────────────────────────────

def test_price_band_none_returns_unknown():
    assert mo._price_band(None) == "unknown"


def test_price_band_unparseable_string_returns_unknown():
    assert mo._price_band("not-a-price") == "unknown"


def test_price_band_boundary_at_50():
    assert mo._price_band(49.99) == "under $50"
    assert mo._price_band(50.0) == "$50-150"


def test_price_band_boundary_at_150():
    assert mo._price_band(149.99) == "$50-150"
    assert mo._price_band(150.0) == "$150-300"


def test_price_band_boundary_at_300():
    assert mo._price_band(299.99) == "$150-300"
    assert mo._price_band(300.0) == "over $300"


def test_price_band_huge_value_over_300():
    assert mo._price_band(500) == "over $300"


# ─── _composite_band ──────────────────────────────────────────────────────────

def test_composite_band_none_returns_unscored():
    assert mo._composite_band(None) == "Unscored"


def test_composite_band_boundary_at_strong_buy():
    assert mo._composite_band(COMPOSITE_STRONG_BUY - 0.01) == f"Buy ({COMPOSITE_BUY}–{COMPOSITE_STRONG_BUY - 1})"
    assert mo._composite_band(COMPOSITE_STRONG_BUY) == f"Strong Buy (≥{COMPOSITE_STRONG_BUY})"


def test_composite_band_boundary_at_buy():
    assert mo._composite_band(COMPOSITE_BUY - 0.01) == f"Hold zone ({COMPOSITE_HOLD}–{COMPOSITE_BUY - 1})"
    assert mo._composite_band(COMPOSITE_BUY) == f"Buy ({COMPOSITE_BUY}–{COMPOSITE_STRONG_BUY - 1})"


def test_composite_band_boundary_at_hold():
    assert mo._composite_band(COMPOSITE_HOLD - 0.01) == f"Sell zone (<{COMPOSITE_HOLD})"
    assert mo._composite_band(COMPOSITE_HOLD) == f"Hold zone ({COMPOSITE_HOLD}–{COMPOSITE_BUY - 1})"


# ─── build_missed_opportunity_corpus ──────────────────────────────────────────

def test_build_corpus_none_input_returns_empty():
    assert mo.build_missed_opportunity_corpus(None) == []


def test_build_corpus_empty_list_returns_empty():
    assert mo.build_missed_opportunity_corpus([]) == []


def test_build_corpus_below_min_missed_tickers_returns_empty(monkeypatch):
    monkeypatch.setattr(mo, "distinct_missed", lambda *a, **k: [{"ticker": "A"}, {"ticker": "B"}])
    enriched_all = [{"ticker": "A", "rec_type": "new_pick", "rec_date": "2026-01-01"}]
    assert mo.build_missed_opportunity_corpus(enriched_all) == []


def test_build_corpus_derives_fields_from_pool_representative(monkeypatch):
    missed_rows = [
        {"ticker": "AAA", "first_rec_date": "2026-01-01", "verdict": "Confirmed",
         "outcome_label": "win", "outcome_pct": 10.0, "alpha_pct": 5.0},
        {"ticker": "BBB", "first_rec_date": "2026-01-02", "verdict": None,
         "outcome_label": None, "outcome_pct": None, "alpha_pct": None},
        {"ticker": "CCC", "first_rec_date": "2026-01-03", "verdict": "Unverified",
         "outcome_label": "loss", "outcome_pct": -5.0, "alpha_pct": -2.0},
    ]
    monkeypatch.setattr(mo, "distinct_missed", lambda *a, **k: missed_rows)
    enriched_all = [
        {"ticker": "AAA", "rec_type": "new_pick", "rec_date": "2026-01-01",
         "sector": "Technology", "price_at_surface": 100.0, "composite_score": 70.0},
        {"ticker": "BBB", "rec_type": "new_pick", "rec_date": "2026-01-02",
         "sector": "", "price_at_surface": None, "composite_score": None},
        {"ticker": "CCC", "rec_type": "new_pick", "rec_date": "2026-01-03",
         "sector": "Healthcare", "price_at_surface": 40.0, "composite_score": 50.0},
        # a same-day different-rec_type row that must NOT be picked as representative
        {"ticker": "AAA", "rec_type": "buy_candidate", "rec_date": "2026-01-01",
         "sector": "WRONG", "price_at_surface": 999.0, "composite_score": 1.0},
    ]
    corpus = mo.build_missed_opportunity_corpus(enriched_all)
    by_ticker = {c["ticker"]: c for c in corpus}

    assert by_ticker["AAA"]["sector"] == "Technology"
    assert by_ticker["AAA"]["price_band"] == "$50-150"
    assert by_ticker["AAA"]["composite_band"] == f"Buy ({COMPOSITE_BUY}–{COMPOSITE_STRONG_BUY - 1})"
    assert by_ticker["AAA"]["verdict"] == "Confirmed"
    assert by_ticker["AAA"]["outcome_label"] == "win"

    assert by_ticker["BBB"]["sector"] == "Other"
    assert by_ticker["BBB"]["verdict"] == "n/a"
    assert by_ticker["BBB"]["outcome_label"] == "unknown"
    assert by_ticker["BBB"]["price_band"] == "unknown"
    assert by_ticker["BBB"]["composite_band"] == "Unscored"


# ─── _format_corpus_for_prompt ────────────────────────────────────────────────

def test_format_corpus_for_prompt_none_values_render_as_na():
    corpus = [{
        "ticker": "AAA", "sector": "Technology", "price_band": "$50-150",
        "composite_band": "Buy", "verdict": "Confirmed", "outcome_label": "win",
        "outcome_pct": None, "alpha_pct": None,
    }]
    text = mo._format_corpus_for_prompt(corpus)
    assert "n/a" in text
    assert "(n/a) | alpha vs SPY: n/a" in text


def test_format_corpus_for_prompt_present_values_render_signed():
    corpus = [{
        "ticker": "AAA", "sector": "Technology", "price_band": "$50-150",
        "composite_band": "Buy", "verdict": "Confirmed", "outcome_label": "win",
        "outcome_pct": 12.345, "alpha_pct": 3.21,
    }]
    text = mo._format_corpus_for_prompt(corpus)
    assert "+12.3%" in text
    assert "+3.2pp" in text


# ─── generate_missed_opportunity_patterns — guards ────────────────────────────

def test_generate_patterns_no_api_key_returns_none():
    corpus = [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]
    assert mo.generate_missed_opportunity_patterns(corpus, api_key="") is None


def test_generate_patterns_too_few_corpus_returns_none():
    corpus = [{"ticker": "A"}, {"ticker": "B"}]
    assert mo.generate_missed_opportunity_patterns(corpus, api_key="fake-key") is None


def _corpus_3():
    return [
        {"ticker": "AAA", "sector": "Technology", "price_band": "$50-150",
         "composite_band": "Buy", "verdict": "Confirmed", "outcome_label": "win"},
        {"ticker": "BBB", "sector": "Technology", "price_band": "$50-150",
         "composite_band": "Buy", "verdict": "Confirmed", "outcome_label": "win"},
        {"ticker": "CCC", "sector": "Healthcare", "price_band": "under $50",
         "composite_band": "Hold zone", "verdict": "n/a", "outcome_label": "loss"},
    ]


def test_generate_patterns_valid_response_round_trip():
    raw = '{"patterns": [{"tickers": ["AAA", "BBB"], "shared_dimension": "sector", "shared_value": "Technology", "pattern_label": "Both Tech"}]}'
    _install_fake_anthropic(raw)
    result = mo.generate_missed_opportunity_patterns(_corpus_3(), api_key="fake-key")
    assert result is not None
    assert len(result["patterns"]) == 1
    assert result["patterns"][0]["tickers"] == ["AAA", "BBB"]


# ─── _parse_pattern_response ──────────────────────────────────────────────────

def test_parse_pattern_response_empty_or_none_returns_none():
    assert mo._parse_pattern_response("", _corpus_3()) is None
    assert mo._parse_pattern_response(None, _corpus_3()) is None


def test_parse_pattern_response_non_dict_json_returns_none():
    assert mo._parse_pattern_response("[1, 2, 3]", _corpus_3()) is None


def test_parse_pattern_response_patterns_not_list_returns_none():
    assert mo._parse_pattern_response('{"patterns": "nope"}', _corpus_3()) is None


def test_parse_pattern_response_unrecognized_dimension_drops_pattern():
    raw = '{"patterns": [{"tickers": ["AAA", "BBB"], "shared_dimension": "vibe", "shared_value": "x", "pattern_label": "l"}]}'
    result = mo._parse_pattern_response(raw, _corpus_3())
    assert result == []


def test_parse_pattern_response_mismatched_shared_value_drops_ticker():
    raw = '{"patterns": [{"tickers": ["AAA", "CCC"], "shared_dimension": "sector", "shared_value": "Technology", "pattern_label": "l"}]}'
    result = mo._parse_pattern_response(raw, _corpus_3())
    # CCC's real sector is Healthcare, not Technology -> dropped, leaving only AAA
    # which is below _MIN_PATTERN_TICKERS(2) -> whole pattern dropped
    assert result == []


def test_parse_pattern_response_below_min_tickers_after_validation_drops_pattern():
    raw = '{"patterns": [{"tickers": ["AAA", "UNKNOWN"], "shared_dimension": "sector", "shared_value": "Technology", "pattern_label": "l"}]}'
    result = mo._parse_pattern_response(raw, _corpus_3())
    assert result == []


def test_parse_pattern_response_dedup_repeated_ticker():
    raw = '{"patterns": [{"tickers": ["AAA", "AAA", "BBB"], "shared_dimension": "sector", "shared_value": "Technology", "pattern_label": "l"}]}'
    result = mo._parse_pattern_response(raw, _corpus_3())
    assert result[0]["tickers"] == ["AAA", "BBB"]


def test_parse_pattern_response_valid_two_ticker_pattern_survives_with_canonical_casing():
    raw = '{"patterns": [{"tickers": ["aaa", "bbb"], "shared_dimension": "sector", "shared_value": "technology", "pattern_label": "Both Tech"}]}'
    result = mo._parse_pattern_response(raw, _corpus_3())
    assert result[0]["tickers"] == ["AAA", "BBB"]


# ─── pattern_outcome_mix ──────────────────────────────────────────────────────

def test_pattern_outcome_mix_counts_correctly():
    corpus = _corpus_3()
    result = mo.pattern_outcome_mix(["AAA", "BBB", "CCC"], corpus)
    assert result == {"win": 2, "loss": 1, "flat": 0, "unknown": 0}


def test_pattern_outcome_mix_unknown_ticker_defaults_to_unknown():
    result = mo.pattern_outcome_mix(["NOTINCORPUS"], _corpus_3())
    assert result["unknown"] == 1


def test_pattern_outcome_mix_empty_tickers_all_zero():
    result = mo.pattern_outcome_mix([], _corpus_3())
    assert result == {"win": 0, "loss": 0, "flat": 0, "unknown": 0}
