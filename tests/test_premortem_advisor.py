"""
Tests for stock_analyzer/premortem_advisor.py — Pre-Mortem Protocol (Concept
C): pillar-attribution from a load_all() bundle, evidence-package assembly,
prompt formatting, response parsing, and the LLM entry point. Zero coverage
before this batch. `generate_case_against`'s real Anthropic call is exercised
via a fake `sys.modules["anthropic"]` module (see tests/test_news_intelligence.py
for the pattern) for one success and one malformed-JSON round trip; the
api_key guard is tested without any mocking since it returns before the
`import anthropic` line runs.
"""
import sys
import types

import pytest

from stock_analyzer import premortem_advisor as pa


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


# ─── _signals_dict_to_strings ────────────────────────────────────────────────

def test_signals_dict_to_strings_none_input_returns_empty():
    assert pa._signals_dict_to_strings(None) == []


def test_signals_dict_to_strings_list_input_returns_empty():
    assert pa._signals_dict_to_strings(["a", "b"]) == []


def test_signals_dict_to_strings_str_input_returns_empty():
    assert pa._signals_dict_to_strings("not a dict") == []


def test_signals_dict_to_strings_dict_preserves_order():
    signals = {"RSI": "62.3 — mildly bearish", "MACD": "positive crossover"}
    result = pa._signals_dict_to_strings(signals)
    assert result == ["RSI: 62.3 — mildly bearish", "MACD: positive crossover"]


# ─── driving_pillar_from_bundle ───────────────────────────────────────────────

def test_driving_pillar_from_bundle_empty_bundle():
    assert pa.driving_pillar_from_bundle({}) == {"driving_pillar": None, "driving_signals": []}


def test_driving_pillar_from_bundle_none_bundle():
    assert pa.driving_pillar_from_bundle(None) == {"driving_pillar": None, "driving_signals": []}


def test_driving_pillar_from_bundle_only_technical_set():
    bundle = {"t_score": 70, "t_signals": {"RSI": "62.3 — mildly bearish"}}
    result = pa.driving_pillar_from_bundle(bundle)
    assert result["driving_pillar"] == "technical"
    assert result["driving_signals"] == ["RSI: 62.3 — mildly bearish"]


def test_driving_pillar_from_bundle_bq_score_beats_others():
    bundle = {
        "t_score": 40, "val_score": 30, "s_score": 20,
        "bq_score": 90, "bq_signals": {"ROE": "22% — strong"},
    }
    result = pa.driving_pillar_from_bundle(bundle)
    assert result["driving_pillar"] == "fundamentals"
    assert result["driving_signals"] == ["ROE: 22% — strong"]


def test_driving_pillar_from_bundle_f_score_fallback_when_bq_absent():
    bundle = {"f_score": 80, "f_signals": {"ROE": "18% — solid"}}
    result = pa.driving_pillar_from_bundle(bundle)
    assert result["driving_pillar"] == "fundamentals"
    assert result["driving_signals"] == ["ROE: 18% — solid"]


def test_driving_pillar_from_bundle_bq_score_present_ignores_f_score():
    bundle = {
        "bq_score": 60, "bq_signals": {"ROE": "bq value"},
        "f_score": 95, "f_signals": {"ROE": "f value"},
    }
    result = pa.driving_pillar_from_bundle(bundle)
    # bq_score (60) used over f_score (95) even though f_score is higher —
    # bq_score/bq_signals are preferred whenever present, never blended.
    assert result["driving_pillar"] == "fundamentals"
    assert result["driving_signals"] == ["ROE: bq value"]


def test_driving_pillar_from_bundle_sentiment_uses_first_3_headlines():
    bundle = {
        "s_score": 90,
        "headlines": [
            {"headline": "H1"},
            {"headline": ""},
            "not a dict",
            {"headline": "H2"},
            {"headline": "H3"},
            {"headline": "H4"},
        ],
    }
    result = pa.driving_pillar_from_bundle(bundle)
    assert result["driving_pillar"] == "sentiment"
    assert result["driving_signals"] == ["H1", "H2", "H3"]


def test_driving_pillar_from_bundle_ties_resolved_first_seen_wins():
    # technical/fundamentals tie at 50 — iteration order is technical,
    # fundamentals, valuation, sentiment, and max() keeps the FIRST maximal.
    bundle = {
        "t_score": 50, "t_signals": {},
        "bq_score": 50, "bq_signals": {},
    }
    result = pa.driving_pillar_from_bundle(bundle)
    assert result["driving_pillar"] == "technical"


def test_driving_pillar_from_bundle_valuation_picked_when_highest():
    bundle = {"t_score": 10, "val_score": 99, "val_signals": {"P/E": "12x — cheap"}}
    result = pa.driving_pillar_from_bundle(bundle)
    assert result["driving_pillar"] == "valuation"
    assert result["driving_signals"] == ["P/E: 12x — cheap"]


# ─── build_premortem_inputs ───────────────────────────────────────────────────

def test_build_premortem_inputs_all_none_defaults():
    result = pa.build_premortem_inputs("AAPL")
    assert result == {
        "ticker": "AAPL", "engine": {}, "portfolio": {}, "macro": {},
        "earnings": {}, "lessons": [],
    }


def test_build_premortem_inputs_explicit_values_pass_through():
    engine = {"composite": 80.0}
    portfolio = {"n_positions": 3}
    macro = {"label": "Risk-On"}
    earnings = {"note": "reports Friday"}
    lessons = ["sold too early"]
    result = pa.build_premortem_inputs(
        "AAPL", engine=engine, portfolio=portfolio, macro=macro,
        earnings=earnings, recent_lessons=lessons,
    )
    assert result["engine"] is engine
    assert result["portfolio"] is portfolio
    assert result["macro"] is macro
    assert result["earnings"] is earnings
    assert result["lessons"] is lessons


# ─── _format_case_against_prompt ──────────────────────────────────────────────

def test_format_case_against_prompt_engine_full_dict():
    inputs = pa.build_premortem_inputs(
        "AAPL",
        engine={
            "composite": 78.0, "band": "Strong Buy",
            "driving_pillar": "fundamentals", "driving_signals": ["ROE: 22%"],
        },
    )
    text = pa._format_case_against_prompt("AAPL", inputs)
    assert "78" in text
    assert "Strong Buy" in text
    assert "fundamentals" in text


def test_format_case_against_prompt_portfolio_empty_dict_first_trade_line():
    inputs = pa.build_premortem_inputs("AAPL")
    text = pa._format_case_against_prompt("AAPL", inputs)
    assert "no existing-position data available (e.g. first trade this session)." in text


def test_format_case_against_prompt_portfolio_truthy_no_recognized_keys():
    inputs = pa.build_premortem_inputs("AAPL", portfolio={"unused_key": 1})
    text = pa._format_case_against_prompt("AAPL", inputs)
    assert "Portfolio: no existing-position data available." in text
    assert "(e.g. first trade this session)" not in text


def test_format_case_against_prompt_portfolio_populated_renders_parts():
    inputs = pa.build_premortem_inputs(
        "AAPL",
        portfolio={
            "n_positions": 5, "top_sector": "Technology",
            "top_sector_weight_pct": 40.0, "this_sector": "Technology",
        },
    )
    text = pa._format_case_against_prompt("AAPL", inputs)
    assert "Portfolio: 5 existing position(s)" in text
    assert "Technology at 40.0%" in text
    assert "This new position's sector: Technology." in text


def test_format_case_against_prompt_macro_present_vs_absent():
    present = pa.build_premortem_inputs("AAPL", macro={"label": "Risk-On", "confidence": 70})
    absent  = pa.build_premortem_inputs("AAPL")
    assert "Macro regime: Risk-On (70% confidence)." in pa._format_case_against_prompt("AAPL", present)
    assert "Macro regime: not available this session." in pa._format_case_against_prompt("AAPL", absent)


def test_format_case_against_prompt_earnings_either_key_alone_vs_absent():
    date_only = pa.build_premortem_inputs("AAPL", earnings={"next_earnings_date": "2026-08-01"})
    note_only = pa.build_premortem_inputs("AAPL", earnings={"note": "guidance cut expected"})
    absent    = pa.build_premortem_inputs("AAPL")
    assert "Next earnings 2026-08-01." in pa._format_case_against_prompt("AAPL", date_only)
    assert "guidance cut expected" in pa._format_case_against_prompt("AAPL", note_only)
    assert "Earnings context: not available." in pa._format_case_against_prompt("AAPL", absent)


def test_format_case_against_prompt_lessons_capped_at_5_and_joined():
    lessons = [f"lesson {i}" for i in range(7)]
    inputs = pa.build_premortem_inputs("AAPL", recent_lessons=lessons)
    text = pa._format_case_against_prompt("AAPL", inputs)
    assert "lesson 0; lesson 1; lesson 2; lesson 3; lesson 4" in text
    assert "lesson 5" not in text


def test_format_case_against_prompt_lessons_empty_omits_line():
    inputs = pa.build_premortem_inputs("AAPL")
    text = pa._format_case_against_prompt("AAPL", inputs)
    assert "recorded exit lessons" not in text


# ─── _parse_case_against ──────────────────────────────────────────────────────

def _valid_3():
    return (
        '[{"angle": "pillar", "argument": "a"}, '
        '{"angle": "portfolio", "argument": "b"}, '
        '{"angle": "macro", "argument": "c"}]'
    )


def test_parse_case_against_none_or_empty_returns_none():
    assert pa._parse_case_against(None) is None
    assert pa._parse_case_against("") is None


def test_parse_case_against_markdown_fenced_json_is_stripped():
    text = "```json\n" + _valid_3() + "\n```"
    result = pa._parse_case_against(text)
    assert len(result) == 3
    assert {r["angle"] for r in result} == {"pillar", "portfolio", "macro"}


def test_parse_case_against_non_json_text_returns_none():
    assert pa._parse_case_against("not json at all") is None


def test_parse_case_against_wrong_item_count_returns_none():
    text = '[{"angle": "pillar", "argument": "a"}, {"angle": "portfolio", "argument": "b"}]'
    assert pa._parse_case_against(text) is None


def test_parse_case_against_missing_angle_returns_none():
    text = (
        '[{"argument": "a"}, '
        '{"angle": "portfolio", "argument": "b"}, '
        '{"angle": "macro", "argument": "c"}]'
    )
    assert pa._parse_case_against(text) is None


def test_parse_case_against_unrecognized_angle_returns_none():
    text = (
        '[{"angle": "weather", "argument": "a"}, '
        '{"angle": "portfolio", "argument": "b"}, '
        '{"angle": "macro", "argument": "c"}]'
    )
    assert pa._parse_case_against(text) is None


def test_parse_case_against_duplicate_angle_returns_none():
    text = (
        '[{"angle": "pillar", "argument": "a"}, '
        '{"angle": "pillar", "argument": "b"}, '
        '{"angle": "portfolio", "argument": "c"}]'
    )
    assert pa._parse_case_against(text) is None


def test_parse_case_against_well_formed_parses_and_lowercases_angle():
    text = (
        '[{"angle": " Pillar ", "argument": "a"}, '
        '{"angle": "portfolio", "argument": "b"}, '
        '{"angle": "macro", "argument": "c"}]'
    )
    result = pa._parse_case_against(text)
    assert result[0] == {"angle": "pillar", "argument": "a"}


# ─── generate_case_against ────────────────────────────────────────────────────

def test_generate_case_against_no_api_key_returns_none():
    inputs = pa.build_premortem_inputs("AAPL")
    assert pa.generate_case_against("AAPL", inputs, api_key="") is None


def test_generate_case_against_valid_response_round_trip():
    inputs = pa.build_premortem_inputs("AAPL", engine={"composite": 70.0})
    _install_fake_anthropic(_valid_3())
    result = pa.generate_case_against("AAPL", inputs, api_key="fake-key")
    assert result is not None
    assert len(result["case_against"]) == 3
    assert result["model"] == "claude-haiku-4-5-20251001"
    assert "generated_at" in result


def test_generate_case_against_malformed_json_returns_none():
    inputs = pa.build_premortem_inputs("AAPL")
    _install_fake_anthropic("not json at all")
    result = pa.generate_case_against("AAPL", inputs, api_key="fake-key")
    assert result is None
