"""
Tests for stock_analyzer/thesis_advisor.py — F-1 Thesis Advisor (reviewer) and
F-5 Thesis Authoring (drafter). Zero coverage before this batch. Covers the
two prompt-formatting functions (each field independently gated, several
exact-boundary quirks: RSI zone strictness, the `as_of == "none"` string
quirk, the verdict-line-uses-LAST-match parsing rule with its
verdict_idx==0 no-truncation edge case), the LLM-calling functions via a fake
`sys.modules["anthropic"]` module (mirrors test_analyst_intel.py's/
test_news_intelligence.py's `_install_fake_anthropic()` pattern, extended
with a capturing variant to inspect the exact prompt text sent), the pure
`bundle_evidence()` extractor (the bug it was built to close: reading the
bundle's real key names, not `indicators`/`revenue_growth`/`news`), and
`run_batch_review`'s skip-before-call guard (proven via a review_thesis stub
that raises if invoked with a bad position).
"""
import sys
import types
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import thesis_advisor as ta


# ─── fake anthropic module helper ────────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text=None, raise_exc=None):
        self._response_text = response_text
        self._raise_exc = raise_exc

    def create(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text=None, raise_exc=None, **kwargs):
        self.messages = _FakeMessages(response_text, raise_exc)


def _install_fake_anthropic(response_text=None, raise_exc=None):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _FakeClient(response_text, raise_exc)
    sys.modules["anthropic"] = fake_mod


def _install_fake_anthropic_capturing(response_text, captured):
    """Like _install_fake_anthropic but records the create() kwargs so the
    exact prompt text sent to the LLM can be inspected."""
    class _CapturingMessages:
        def create(self, **kwargs):
            captured.append(kwargs)
            return _FakeResponse(response_text)

    class _CapturingClient:
        def __init__(self, **kwargs):
            self.messages = _CapturingMessages()

    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _CapturingClient(**kwargs)
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


# ─── _format_prompt — technical branch ───────────────────────────────────────

def test_format_prompt_technical_above_below_moving_average():
    above = ta._format_prompt("AAPL", "t", {"technical": {"above_sma50": True}})
    below = ta._format_prompt("AAPL", "t", {"technical": {"above_sma50": False}})
    assert "above the 50-day moving average" in above
    assert "below the 50-day moving average" in below


def test_format_prompt_rsi_overbought_boundary_strictly_above_70():
    at_70 = ta._format_prompt("AAPL", "t", {"technical": {"rsi": 70}})
    above_70 = ta._format_prompt("AAPL", "t", {"technical": {"rsi": 70.01}})
    assert "overbought (>70)" not in at_70
    assert "neutral" in at_70
    assert "overbought (>70)" in above_70


def test_format_prompt_rsi_oversold_boundary_strictly_below_30():
    at_30 = ta._format_prompt("AAPL", "t", {"technical": {"rsi": 30}})
    below_30 = ta._format_prompt("AAPL", "t", {"technical": {"rsi": 29.99}})
    assert "oversold (<30)" not in at_30
    assert "neutral" in at_30
    assert "oversold (<30)" in below_30


def test_format_prompt_momentum_rendered_with_sign():
    out_pos = ta._format_prompt("AAPL", "t", {"technical": {"momentum_1m_pct": 3.2}})
    out_neg = ta._format_prompt("AAPL", "t", {"technical": {"momentum_1m_pct": -3.2}})
    assert "+3.2%" in out_pos
    assert "-3.2%" in out_neg


# ─── _format_prompt — fundamentals branch ────────────────────────────────────

def test_format_prompt_fundamentals_fields_rendered_independently():
    out = ta._format_prompt("AAPL", "t", {"fundamentals": {"revenue_growth": 10.0}})
    assert "Revenue growth: +10.0%." in out
    assert "Profit margin" not in out
    assert "Earnings trend" not in out

    out2 = ta._format_prompt("AAPL", "t", {"fundamentals": {"profit_margin": 22.5}})
    assert "Profit margin: 22.5%." in out2
    assert "Revenue growth" not in out2

    out3 = ta._format_prompt("AAPL", "t", {"fundamentals": {"earnings_trend": "growing"}})
    assert "Earnings trend: growing." in out3


def test_format_prompt_fundamentals_omitted_when_dict_empty():
    out = ta._format_prompt("AAPL", "t", {"fundamentals": {}})
    assert "Fundamentals:" not in out


# ─── _format_prompt — news_headlines ─────────────────────────────────────────

def test_format_prompt_news_headlines_capped_at_12():
    headlines = [f"h{i}" for i in range(15)]
    out = ta._format_prompt("AAPL", "t", {"news_headlines": headlines})
    assert "Recent news (15 headlines):" in out
    rendered = [ln for ln in out.splitlines() if ln.strip().startswith("- h")]
    assert len(rendered) == 12


# ─── _format_prompt — last_earnings ──────────────────────────────────────────

def test_format_prompt_last_earnings_result_and_guidance_independent():
    out = ta._format_prompt("AAPL", "t", {"last_earnings": {"result": "beat EPS"}})
    assert "Last earnings: beat EPS." in out
    assert "Guidance" not in out


def test_format_prompt_last_earnings_omitted_when_neither_key_present():
    out = ta._format_prompt("AAPL", "t", {"last_earnings": {"unrelated": "x"}})
    assert "Earnings:" not in out


# ─── _format_prompt — analyst_consensus ──────────────────────────────────────

def test_format_prompt_analyst_avg_pt_and_n_firms_fallback():
    out = ta._format_prompt("AAPL", "t", {"analyst_consensus": {"avg_pt": 123.456}})
    assert "Avg price target $123.46 across ? firm(s)." in out


def test_format_prompt_analyst_as_of_skipped_when_literal_none_string():
    out = ta._format_prompt(
        "AAPL", "t",
        {"analyst_consensus": {"consensus_rating": "Buy", "as_of": "None"}},
    )
    assert "coverage as of" not in out


def test_format_prompt_analyst_as_of_shown_when_real_value():
    out = ta._format_prompt(
        "AAPL", "t",
        {"analyst_consensus": {"consensus_rating": "Buy", "as_of": "2026-01-01"}},
    )
    assert "coverage as of 2026-01-01" in out


def test_format_prompt_analyst_thesis_capped_at_2_joined_with_semicolon():
    out = ta._format_prompt(
        "AAPL", "t",
        {"analyst_consensus": {"consensus_rating": "Buy", "thesis": ["a", "b", "c"]}},
    )
    assert "Analyst thesis points: a; b." in out
    assert "; c" not in out


# ─── _parse_response ─────────────────────────────────────────────────────────

def test_parse_response_no_verdict_line_defaults_weakening_full_summary():
    text = "Some prose without any verdict line."
    result = ta._parse_response(text)
    assert result["status"] == "WEAKENING"
    assert result["summary"] == text.strip()


def test_parse_response_uses_last_verdict_line_for_status_and_truncation():
    text = "Verdict: INTACT is mentioned early.\nMore prose.\nVerdict: broken"
    result = ta._parse_response(text)
    assert result["status"] == "BROKEN"
    assert result["summary"] == "Verdict: INTACT is mentioned early. More prose."


def test_parse_response_intact_and_broken_substrings_win():
    assert ta._parse_response("stuff\nVerdict: INTACT")["status"] == "INTACT"
    assert ta._parse_response("stuff\nVerdict: BROKEN")["status"] == "BROKEN"


def test_parse_response_unrecognized_verdict_text_defaults_weakening():
    assert ta._parse_response("stuff\nVerdict: unclear")["status"] == "WEAKENING"


def test_parse_response_verdict_as_first_line_summary_not_truncated():
    text = "Verdict: INTACT"
    result = ta._parse_response(text)
    assert result["status"] == "INTACT"
    assert result["summary"] == text.strip()


# ─── build_review_inputs ─────────────────────────────────────────────────────

def test_build_review_inputs_all_none_defaults():
    result = ta.build_review_inputs()
    assert result == {
        "technical": {}, "fundamentals": {}, "news_headlines": [],
        "last_earnings": {}, "analyst_consensus": {},
    }


# ─── inputs_hash ──────────────────────────────────────────────────────────────

def test_inputs_hash_key_order_independent():
    a = {"x": 1, "y": 2}
    b = {"y": 2, "x": 1}
    assert ta.inputs_hash(a) == ta.inputs_hash(b)


def test_inputs_hash_different_dict_gives_different_hash():
    assert ta.inputs_hash({"x": 1}) != ta.inputs_hash({"x": 2})


def test_inputs_hash_is_16_char_lowercase_hex():
    h = ta.inputs_hash({"a": 1})
    assert len(h) == 16
    assert h == h.lower()
    int(h, 16)  # raises ValueError if not hex


# ─── review_thesis ────────────────────────────────────────────────────────────

def test_review_thesis_no_api_key_returns_none():
    assert ta.review_thesis("AAPL", "thesis text", {}, api_key="") is None


def test_review_thesis_blank_user_thesis_returns_none():
    assert ta.review_thesis("AAPL", "   ", {}, api_key="fake-key") is None


def test_review_thesis_full_round_trip_intact():
    _install_fake_anthropic("Some supporting prose.\nVerdict: INTACT")
    result = ta.review_thesis("AAPL", "my thesis", {}, api_key="fake-key")
    assert result["status"] == "INTACT"
    assert result["raw"] == "Some supporting prose.\nVerdict: INTACT"
    assert result["model"] == "claude-sonnet-4-6"
    assert "reviewed_at" in result


def test_review_thesis_exception_returns_none():
    _install_fake_anthropic(raise_exc=RuntimeError("boom"))
    assert ta.review_thesis("AAPL", "my thesis", {}, api_key="fake-key") is None


# ─── run_batch_review ────────────────────────────────────────────────────────

def test_run_batch_review_skips_missing_ticker_or_blank_thesis_before_any_call(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("review_thesis should not be called for a skippable position")
    monkeypatch.setattr(ta, "review_thesis", _boom)

    positions = [
        {"ticker": "", "user_thesis": "something"},
        {"ticker": "AAPL", "user_thesis": "   "},
        {"user_thesis": "something"},  # missing ticker key entirely
    ]
    result = ta.run_batch_review(positions, api_key="fake-key")
    assert result == []


def test_run_batch_review_none_result_excluded_success_included(monkeypatch):
    def fake_review(ticker, user_thesis, inputs, api_key, model="claude-sonnet-4-6"):
        if ticker == "FAIL":
            return None
        return {
            "status": "INTACT", "summary": "looks fine", "raw": "raw text",
            "model": model, "reviewed_at": "2026-01-01T00:00:00+00:00",
        }
    monkeypatch.setattr(ta, "review_thesis", fake_review)

    positions = [
        {"ticker": "FAIL", "user_thesis": "t1", "inputs": {"a": 1}},
        {"ticker": "OK", "user_thesis": "t2", "inputs": {"b": 2}, "trade_date": date(2026, 1, 1)},
    ]
    results = ta.run_batch_review(positions, api_key="fake-key")
    assert len(results) == 1
    r = results[0]
    assert r["ticker"] == "OK"
    assert r["trade_date"] == "2026-01-01"
    assert r["status"] == "INTACT"
    assert r["summary"] == "looks fine"
    assert r["inputs_hash"] == ta.inputs_hash({"b": 2})


def test_run_batch_review_trade_date_defaults_to_today(monkeypatch):
    def fake_review(*a, **k):
        return {"status": "INTACT", "summary": "s", "raw": "r", "model": "m", "reviewed_at": "x"}
    monkeypatch.setattr(ta, "review_thesis", fake_review)

    positions = [{"ticker": "OK", "user_thesis": "t", "inputs": {}}]
    results = ta.run_batch_review(positions, api_key="fake-key")
    assert results[0]["trade_date"] == str(date.today())


# ─── _format_authoring_prompt ─────────────────────────────────────────────────

def test_format_authoring_prompt_company_and_sector_only_when_present():
    out = ta._format_authoring_prompt("AAPL", {"company_name": "Apple Inc", "sector": "Tech"})
    assert "Company: Apple Inc" in out
    assert "Sector: Tech" in out

    out2 = ta._format_authoring_prompt("AAPL", {})
    assert "Company:" not in out2
    assert "Sector:" not in out2


def test_format_authoring_prompt_engine_gates_cleared_only_when_nonempty():
    out = ta._format_authoring_prompt(
        "AAPL",
        {"engine": {"composite": 80, "band": "Strong Buy", "conviction": "High",
                     "gates_cleared": ["gate1", "gate2"]}},
    )
    assert "Composite score 80/100" in out
    assert "(Strong Buy)" in out
    assert "conviction High" in out
    assert "Cleared entry checks: gate1, gate2." in out

    out2 = ta._format_authoring_prompt("AAPL", {"engine": {"composite": 80}})
    assert "Cleared entry checks" not in out2


def test_format_authoring_prompt_fundamentals_and_catalyst_rendered():
    out = ta._format_authoring_prompt(
        "AAPL",
        {"fundamentals": {"revenue_growth": 5.0},
         "catalyst": {"next_earnings_date": "2026-08-01", "note": "note text"}},
    )
    assert "Revenue growth +5.0%." in out
    assert "Next earnings 2026-08-01." in out
    assert "note text" in out


def test_format_authoring_prompt_news_headlines_capped_at_12():
    inputs = {"news_headlines": [f"h{i}" for i in range(15)]}
    out = ta._format_authoring_prompt("AAPL", inputs)
    rendered = [ln for ln in out.splitlines() if ln.strip().startswith("- h")]
    assert len(rendered) == 12


def test_format_authoring_prompt_technical_labelled_entry_timing_not_thesis():
    out = ta._format_authoring_prompt("AAPL", {"technical": {"above_sma50": True, "rsi": 60}})
    assert "Entry timing (NOT the thesis):" in out
    assert "Technical:" not in out  # distinguishes from the reviewer prompt's plain label


def test_format_authoring_prompt_regime_rendered_as_own_line():
    out = ta._format_authoring_prompt("AAPL", {"regime": "Rate-Cut Optimism"})
    assert "Market regime: Rate-Cut Optimism." in out

    out_absent = ta._format_authoring_prompt("AAPL", {})
    assert "Market regime:" not in out_absent


# ─── build_authoring_inputs ───────────────────────────────────────────────────

def test_build_authoring_inputs_all_none_defaults():
    result = ta.build_authoring_inputs()
    assert result == {
        "company_name": None, "sector": None, "engine": {}, "fundamentals": {},
        "catalyst": {}, "news_headlines": [], "technical": {}, "regime": None,
    }


# ─── draft_thesis ─────────────────────────────────────────────────────────────

def test_draft_thesis_no_api_key_returns_none():
    assert ta.draft_thesis("AAPL", {}, api_key="") is None


def test_draft_thesis_empty_response_text_returns_none():
    _install_fake_anthropic("   ")
    assert ta.draft_thesis("AAPL", {}, api_key="fake-key") is None


def test_draft_thesis_full_round_trip():
    _install_fake_anthropic("A candidate thesis. Breaks if margins compress.")
    result = ta.draft_thesis("AAPL", {}, api_key="fake-key")
    assert result["draft"] == "A candidate thesis. Breaks if margins compress."
    assert result["model"] == "claude-sonnet-4-6"
    assert "generated_at" in result


def test_draft_thesis_exception_returns_none():
    _install_fake_anthropic(raise_exc=RuntimeError("boom"))
    assert ta.draft_thesis("AAPL", {}, api_key="fake-key") is None


# ─── bundle_evidence ──────────────────────────────────────────────────────────

def test_bundle_evidence_none_or_empty_df_technical_empty():
    assert ta.bundle_evidence({"df": None})["technical"] == {}
    assert ta.bundle_evidence({"df": pd.DataFrame()})["technical"] == {}


def test_bundle_evidence_missing_sma50_column_above_sma50_none():
    df = pd.DataFrame({"Close": [100.0] * 25})
    result = ta.bundle_evidence({"df": df})
    assert result["technical"]["above_sma50"] is None


def test_bundle_evidence_rsi_all_nan_returns_none_not_crash():
    df = pd.DataFrame({"Close": [100.0] * 25, "RSI": [float("nan")] * 25})
    result = ta.bundle_evidence({"df": df})
    assert result["technical"]["rsi"] is None


def test_bundle_evidence_momentum_requires_more_than_21_rows_strictly():
    df21 = pd.DataFrame({"Close": [float(i) for i in range(1, 22)]})  # len == 21
    result21 = ta.bundle_evidence({"df": df21})
    assert result21["technical"]["momentum_1m_pct"] is None

    df22 = pd.DataFrame({"Close": [float(i) for i in range(1, 23)]})  # len == 22
    result22 = ta.bundle_evidence({"df": df22})
    close = df22["Close"]
    expected = (close.iloc[-1] / close.iloc[-21] - 1) * 100
    assert result22["technical"]["momentum_1m_pct"] == pytest.approx(expected)


def test_bundle_evidence_earnings_growth_growing_and_contracting_sign():
    growing = ta.bundle_evidence({"financials": {"earnings_growth": 0.15}})
    assert growing["fundamentals"]["earnings_trend"] == "growing ~15% YoY"

    zero = ta.bundle_evidence({"financials": {"earnings_growth": 0.0}})
    assert zero["fundamentals"]["earnings_trend"] == "growing ~0% YoY"

    contracting = ta.bundle_evidence({"financials": {"earnings_growth": -0.08}})
    assert contracting["fundamentals"]["earnings_trend"] == "contracting ~8% YoY"


def test_bundle_evidence_earnings_growth_none_trend_none():
    result = ta.bundle_evidence({"financials": {"earnings_growth": None}})
    assert result["fundamentals"]["earnings_trend"] is None


def test_bundle_evidence_news_headlines_filters_and_caps_at_15():
    headlines = (
        [{"headline": f"h{i}"} for i in range(17)]
        + [{"headline": ""}, "not a dict", {"other": "x"}]
    )
    result = ta.bundle_evidence({"headlines": headlines})
    assert len(result["news_headlines"]) == 15
    assert all(h.startswith("h") for h in result["news_headlines"])


def test_bundle_evidence_malformed_empty_bundle_degrades_gracefully():
    result = ta.bundle_evidence({})
    assert result == {
        "technical": {},
        "fundamentals": {"revenue_growth": None, "profit_margin": None, "earnings_trend": None},
        "news_headlines": [],
    }


# ─── generate_earnings_thesis_update ─────────────────────────────────────────

def test_generate_earnings_thesis_update_guards_no_anthropic_import():
    assert ta.generate_earnings_thesis_update("AAPL", "t", {"eps_beat": True}, api_key="") is None
    assert ta.generate_earnings_thesis_update("AAPL", "   ", {"eps_beat": True}, api_key="fake") is None
    assert ta.generate_earnings_thesis_update("AAPL", "t", {}, api_key="fake") is None
    assert ta.generate_earnings_thesis_update("AAPL", "t", None, api_key="fake") is None


def test_generate_earnings_thesis_update_signal_beat():
    _install_fake_anthropic("prose\nVerdict: INTACT")
    result = ta.generate_earnings_thesis_update(
        "AAPL", "t", {"eps_beat": True, "rev_beat": True, "guidance_direction": "raised"},
        api_key="fake",
    )
    assert result["earnings_signal"] == "beat"


def test_generate_earnings_thesis_update_signal_beat_with_maintained_guidance():
    _install_fake_anthropic("prose\nVerdict: INTACT")
    result = ta.generate_earnings_thesis_update(
        "AAPL", "t", {"eps_beat": True, "rev_beat": True, "guidance_direction": "maintained"},
        api_key="fake",
    )
    assert result["earnings_signal"] == "beat"


def test_generate_earnings_thesis_update_signal_miss():
    _install_fake_anthropic("prose\nVerdict: BROKEN")
    result = ta.generate_earnings_thesis_update(
        "AAPL", "t", {"eps_beat": False, "rev_beat": False}, api_key="fake",
    )
    assert result["earnings_signal"] == "miss"


def test_generate_earnings_thesis_update_signal_unknown():
    _install_fake_anthropic("prose\nVerdict: WEAKENING")
    result = ta.generate_earnings_thesis_update(
        "AAPL", "t", {"eps_beat": None, "rev_beat": None}, api_key="fake",
    )
    assert result["earnings_signal"] == "unknown"


def test_generate_earnings_thesis_update_signal_mixed():
    _install_fake_anthropic("prose\nVerdict: WEAKENING")
    result = ta.generate_earnings_thesis_update(
        "AAPL", "t", {"eps_beat": True, "rev_beat": False}, api_key="fake",
    )
    assert result["earnings_signal"] == "mixed"


def test_generate_earnings_thesis_update_eps_line_with_surprise_pct():
    captured = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured)
    ta.generate_earnings_thesis_update(
        "AAPL", "t",
        {"actual_eps": 1.5, "estimated_eps": 1.2, "eps_beat": True, "eps_surprise_pct": 25.0},
        api_key="fake",
    )
    prompt = captured[0]["messages"][0]["content"]
    assert "EPS: actual $1.50 vs estimate $1.20" in prompt
    assert "surprise: +25.0%" in prompt


def test_generate_earnings_thesis_update_eps_line_without_surprise_pct():
    captured = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured)
    ta.generate_earnings_thesis_update(
        "AAPL", "t", {"actual_eps": 1.5, "estimated_eps": 1.2, "eps_beat": True}, api_key="fake",
    )
    prompt = captured[0]["messages"][0]["content"]
    assert "EPS: actual $1.50 vs estimate $1.20" in prompt
    assert "surprise:" not in prompt


def test_generate_earnings_thesis_update_eps_line_omitted_when_either_missing():
    captured = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured)
    ta.generate_earnings_thesis_update("AAPL", "t", {"actual_eps": 1.5}, api_key="fake")
    prompt = captured[0]["messages"][0]["content"]
    assert "EPS:" not in prompt


def test_generate_earnings_thesis_update_revenue_line_gated_similarly():
    captured = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured)
    ta.generate_earnings_thesis_update(
        "AAPL", "t", {"actual_revenue": 5.0, "estimated_revenue": 4.5, "rev_beat": True}, api_key="fake",
    )
    prompt = captured[0]["messages"][0]["content"]
    assert "Revenue: actual $5.00B vs estimate $4.50B" in prompt

    captured2 = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured2)
    ta.generate_earnings_thesis_update("AAPL", "t", {"actual_revenue": 5.0}, api_key="fake")
    prompt2 = captured2[0]["messages"][0]["content"]
    assert "Revenue:" not in prompt2


def test_generate_earnings_thesis_update_guidance_omitted_when_unknown():
    captured = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured)
    ta.generate_earnings_thesis_update("AAPL", "t", {"eps_beat": True}, api_key="fake")
    prompt = captured[0]["messages"][0]["content"]
    assert "Guidance: unknown" not in prompt


def test_generate_earnings_thesis_update_guidance_rendered_when_known():
    captured = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured)
    ta.generate_earnings_thesis_update("AAPL", "t", {"guidance_direction": "raised"}, api_key="fake")
    prompt = captured[0]["messages"][0]["content"]
    assert "Guidance: raised" in prompt


def test_generate_earnings_thesis_update_key_narrative_only_when_present():
    captured = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured)
    ta.generate_earnings_thesis_update("AAPL", "t", {"key_narrative": "strong demand"}, api_key="fake")
    prompt = captured[0]["messages"][0]["content"]
    assert "Management commentary: strong demand" in prompt

    captured2 = []
    _install_fake_anthropic_capturing("prose\nVerdict: INTACT", captured2)
    ta.generate_earnings_thesis_update("AAPL", "t", {"eps_beat": True}, api_key="fake")
    prompt2 = captured2[0]["messages"][0]["content"]
    assert "Management commentary" not in prompt2


def test_generate_earnings_thesis_update_full_round_trip():
    _install_fake_anthropic("Solid quarter overall.\nVerdict: INTACT")
    result = ta.generate_earnings_thesis_update(
        "AAPL", "t", {"eps_beat": True, "rev_beat": True, "guidance_direction": "raised"},
        api_key="fake",
    )
    assert result == {
        "suggested_status": "INTACT",
        "rationale": "Solid quarter overall.",
        "earnings_signal": "beat",
    }


def test_generate_earnings_thesis_update_exception_returns_none():
    _install_fake_anthropic(raise_exc=RuntimeError("boom"))
    result = ta.generate_earnings_thesis_update("AAPL", "t", {"eps_beat": True}, api_key="fake")
    assert result is None
