"""
Tests for stock_analyzer/debate_agent.py — Multi-Agent Debate Phase 1 (entry)
+ Phase 3 (exit) corpus builders, prompt formatting, judge-response parsing,
and the 4-round Bull vs Bear + Judge orchestrator. Zero coverage before this
batch. `run_debate`'s own orchestration logic (transcript assembly, per-round
early return, entry-vs-exit prompt branching) is tested by monkeypatching
`_call_haiku` directly with a small stateful fake -- `_call_haiku` itself
(and the real Anthropic wiring) is covered separately via a fake
`sys.modules["anthropic"]` module (see tests/test_news_intelligence.py for
the pattern). The dev venv has no `anthropic` installed (see CLAUDE.md
"never run locally"), so the no-fake-installed path also doubles as a real
test of the `anthropic_import_failed` branch.
"""
import math
import sys
import types

import pandas as pd
import pytest

from stock_analyzer import debate_agent as da


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


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


def _history(n=25, start=100.0, step=1.0):
    """Synthetic Close-price history long enough for pct_change(20) to be non-NaN."""
    return pd.DataFrame({"Close": [start + i * step for i in range(n)]})


# ─── _call_haiku ──────────────────────────────────────────────────────────────

def test_call_haiku_success_returns_stripped_text():
    client = _FakeClient(response_text="  hello there  ")
    result = da._call_haiku(client, "model", "system", "user text")
    assert result == "hello there"


def test_call_haiku_raises_returns_none():
    client = _FakeClient(raise_exc=RuntimeError("boom"))
    result = da._call_haiku(client, "model", "system", "user text")
    assert result is None


# ─── _format_corpus ───────────────────────────────────────────────────────────

def test_format_corpus_entry_type_header():
    text = da._format_corpus({"ticker": "AAPL"}, "entry")
    assert text.startswith("Debate context: New-entry analysis")


def test_format_corpus_exit_type_header():
    text = da._format_corpus({"ticker": "AAPL"}, "exit")
    assert text.startswith("Debate context: Hold-vs-exit decision")


def test_format_corpus_current_price_nan_and_inf_excluded():
    nan_text = da._format_corpus({"current_price": float("nan")}, "entry")
    inf_text = da._format_corpus({"current_price": float("inf")}, "entry")
    assert "Current price" not in nan_text
    assert "Current price" not in inf_text


def test_format_corpus_current_price_finite_rendered():
    text = da._format_corpus({"current_price": 123.456}, "entry")
    assert "Current price: $123.46" in text


def test_format_corpus_composite_score_nan_excluded():
    text = da._format_corpus({"composite_score": float("nan"), "composite_label": "Buy"}, "entry")
    assert "Composite score" not in text


def test_format_corpus_composite_score_with_label_rendered():
    text = da._format_corpus({"composite_score": 78.0, "composite_label": "Strong Buy"}, "entry")
    assert "Composite score: 78.0/100 (Strong Buy)" in text


def test_format_corpus_pillar_line_absent_when_all_missing():
    text = da._format_corpus({"ticker": "AAPL"}, "entry")
    assert "Pillar scores" not in text


def test_format_corpus_pillar_line_joined_when_multiple_present():
    corpus = {"t_score": 70.0, "bq_score": 55.0, "val_score": float("nan"), "s_score": 40.0}
    text = da._format_corpus(corpus, "entry")
    assert "Pillar scores — Technical: 70.0, Fundamentals: 55.0, Sentiment: 40.0" in text


def test_format_corpus_momentum_finite_rendered_and_nan_excluded():
    both = da._format_corpus({"momentum_5d_pct": 4.2, "momentum_20d_pct": 19.2}, "entry")
    assert "Momentum (5d): +4.2%" in both
    assert "Momentum (20d): +19.2%" in both

    nan_5d = da._format_corpus({"momentum_5d_pct": float("nan"), "momentum_20d_pct": 19.2}, "entry")
    assert "Momentum (5d)" not in nan_5d
    assert "Momentum (20d): +19.2%" in nan_5d


def test_format_corpus_exit_only_fields_render_even_in_entry_typed_corpus():
    corpus = {
        "debate_type": "entry",  # entry-typed, but exit-only fields present anyway
        "unrealized_pnl_pct": -12.5,
        "deterioration_tier": "TRIM",
        "deterioration_signals": "down 18.4% from peak",
        "thesis_erosion_score": 62.4,
        "days_held": 30,
        "stop_distance_pct": 4.8,
        "user_thesis": "Long-term compounder",
    }
    text = da._format_corpus(corpus, "entry")
    assert "Unrealized P&L: -12.5%" in text
    assert "Deterioration tier: TRIM" in text
    assert "Deterioration signals — down 18.4% from peak" in text
    assert "Thesis erosion score: 62/100" in text
    assert "Days held: 30" in text
    assert "Distance above protective stop: +4.8%" in text
    assert "Original buy thesis: Long-term compounder" in text


def test_format_corpus_never_raises_on_malformed_corpus():
    # A non-numeric value where a numeric one is expected should be swallowed
    # by the outer try/except, returning "" rather than raising.
    text = da._format_corpus({"current_price": "not-a-number"}, "entry")
    assert isinstance(text, str)


# ─── _parse_judge ─────────────────────────────────────────────────────────────

def test_parse_judge_none_or_empty_returns_none():
    assert da._parse_judge(None) is None
    assert da._parse_judge("") is None


def test_parse_judge_markdown_fenced_json_stripped():
    text = '```json\n{"verdict": "bull_wins", "key_dispute": "x", "bull_case_score": 80, "bear_case_score": 50, "grounded": true}\n```'
    result = da._parse_judge(text)
    assert result["verdict"] == "bull_wins"


def test_parse_judge_invalid_verdict_returns_none():
    text = '{"verdict": "draw"}'
    assert da._parse_judge(text) is None


def test_parse_judge_well_formed_all_keys_missing_optional_default_none():
    text = '{"verdict": "contested"}'
    result = da._parse_judge(text)
    assert result == {
        "verdict": "contested", "key_dispute": None,
        "bull_case_score": None, "bear_case_score": None, "grounded": None,
    }


# ─── build_entry_corpus ────────────────────────────────────────────────────────

def test_build_entry_corpus_happy_path_full_fields(monkeypatch):
    grow_bundle = {
        "history": _history(),
        "t_score": 65,
        "bq_score": None,
        "f_score": 55,
        "val_score": 40,
        "s_score": 70,
        "info": {"sector": "Technology"},
    }
    grow_candidate_row = {
        "composite_score": 72.0, "composite_label": "Buy",
        "sector": "", "conviction": "High",
    }
    monkeypatch.setattr(
        "stock_analyzer.exit_advisor.compute_relative_strength",
        lambda close, spy: 8.34,
    )
    corpus = da.build_entry_corpus("aapl", grow_candidate_row, grow_bundle, pd.Series([1.0]))

    assert corpus["ticker"] == "AAPL"
    assert corpus["debate_type"] == "entry"
    assert corpus["current_price"] == 124.0
    assert corpus["composite_score"] == 72.0
    assert corpus["composite_label"] == "Buy"
    assert corpus["t_score"] == 65
    assert corpus["bq_score"] == 55  # fallback from f_score since bq_score is None
    assert corpus["val_score"] == 40
    assert corpus["s_score"] == 70
    assert corpus["sector"] == "Technology"  # fallback from grow_bundle["info"]
    assert corpus["momentum_5d_pct"] == pytest.approx(4.2)
    assert corpus["momentum_20d_pct"] == pytest.approx(19.2)
    assert corpus["rs_vs_spy_20d_pp"] == pytest.approx(8.3)
    assert corpus["conviction"] == "High"


def test_build_entry_corpus_missing_history_never_raises_and_omits_price_fields():
    corpus = da.build_entry_corpus("AAPL", {}, {}, None)
    assert corpus["ticker"] == "AAPL"
    assert corpus["debate_type"] == "entry"
    assert "current_price" not in corpus
    assert "momentum_5d_pct" not in corpus
    assert "momentum_20d_pct" not in corpus
    assert "rs_vs_spy_20d_pp" not in corpus


# ─── build_exit_corpus ─────────────────────────────────────────────────────────

def test_build_exit_corpus_happy_path_full_fields():
    port_df_row = {
        "Price": 105.0, "P&L (%)": -12.5, "Score": 45.0,
        "Sector": "Technology", "Stop": 100.0, "Signal": "TRIM",
    }
    held_data_bundle = {"df": _history(), "position_age_days": 30}
    erosion_cache_row = {"erosion_score": 62}
    trade_row = {"user_thesis": "Long-term compounder"}
    deterioration_payload = {
        "tier": "TRIM", "dd_from_peak_pct": 18.4, "trim_floor": 15, "exit_floor": 25,
        "below_ma_count": 2, "trend_ma": 50, "rel_strength": -3.2, "sma": 102.5,
    }
    corpus = da.build_exit_corpus(
        "aapl", port_df_row, held_data_bundle, erosion_cache_row, trade_row, deterioration_payload
    )

    assert corpus["ticker"] == "AAPL"
    assert corpus["debate_type"] == "exit"
    assert corpus["current_price"] == 105.0
    assert corpus["unrealized_pnl_pct"] == -12.5
    assert corpus["deterioration_tier"] == "TRIM"
    assert corpus["deterioration_signals"] == (
        "down 18.4% from peak; trigger 15%/25%; 2/3 sessions below SMA50; "
        "rel-strength -3.2pp vs SPY; SMA50 $102.50"
    )
    assert corpus["rs_vs_spy_20d_pp"] == pytest.approx(-3.2)
    assert corpus["thesis_erosion_score"] == 62
    assert corpus["composite_score"] == 45.0
    assert corpus["composite_label"] == "TRIM"
    assert corpus["momentum_5d_pct"] == pytest.approx(4.2)
    assert corpus["momentum_20d_pct"] == pytest.approx(19.2)
    assert corpus["days_held"] == 30
    assert corpus["stop_distance_pct"] == pytest.approx(4.8)
    assert corpus["sector"] == "Technology"
    assert corpus["user_thesis"] == "Long-term compounder"


def test_build_exit_corpus_partial_deterioration_payload_omits_missing_clause():
    deterioration_payload = {
        "tier": "WATCH", "dd_from_peak_pct": 10.0,
        "below_ma_count": 1, "trend_ma": 50, "rel_strength": 1.5, "sma": 95.0,
        # trim_floor / exit_floor deliberately absent
    }
    corpus = da.build_exit_corpus("AAPL", {}, {}, None, None, deterioration_payload)
    assert corpus["deterioration_signals"] == (
        "down 10.0% from peak; 1/3 sessions below SMA50; "
        "rel-strength +1.5pp vs SPY; SMA50 $95.00"
    )
    assert "trigger" not in corpus["deterioration_signals"]


def test_build_exit_corpus_current_price_falls_back_to_held_data_close():
    held_data_bundle = {"df": _history()}
    corpus = da.build_exit_corpus("AAPL", {}, held_data_bundle, None, None, {})
    assert corpus["current_price"] == 124.0


def test_build_exit_corpus_stop_distance_requires_both_price_and_stop():
    # Stop present but no current_price available anywhere -> no stop_distance_pct.
    corpus = da.build_exit_corpus("AAPL", {"Stop": 100.0}, {}, None, None, {})
    assert "stop_distance_pct" not in corpus


def test_build_exit_corpus_all_none_inputs_only_ticker_and_debate_type():
    corpus = da.build_exit_corpus("AAPL", None, None, None, None, None)
    assert corpus == {"ticker": "AAPL", "debate_type": "exit"}


# ─── run_debate ────────────────────────────────────────────────────────────────

def _stateful_haiku(responses):
    """Returns responses[i] for the i-th call (0-indexed), None once exhausted.
    Records every call's kwargs on `.calls` for prompt-content assertions."""
    calls = []

    def _fake(client, model, system, user_text, max_tokens=200):
        idx = len(calls)
        calls.append({"system": system, "user_text": user_text, "max_tokens": max_tokens})
        return responses[idx] if idx < len(responses) else None

    _fake.calls = calls
    return _fake


_JUDGE_JSON = (
    '{"verdict": "bull_wins", "key_dispute": "growth vs valuation", '
    '"bull_case_score": 80, "bear_case_score": 50, "grounded": true}'
)


def test_run_debate_no_api_key_no_anthropic_import_attempted():
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="")
    assert result == {
        "transcript": [], "verdict": None, "partial": True, "error": "no_api_key",
    }


def test_run_debate_anthropic_import_failure_returns_partial(monkeypatch):
    # Force the import to fail rather than relying on `anthropic` being absent
    # from the dev venv. It IS installed here now (requirements.txt pins
    # anthropic>=0.40.0), so the old "no fake installed" assumption silently
    # stopped exercising this branch and the test failed on error="round1_failed"
    # instead. Binding sys.modules["anthropic"] to None makes `import anthropic`
    # raise ImportError deterministically, in any venv.
    monkeypatch.setitem(sys.modules, "anthropic", None)
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")
    assert result["transcript"] == []
    assert result["verdict"] is None
    assert result["partial"] is True
    assert result["error"] == "anthropic_import_failed"


def test_run_debate_round1_failure(monkeypatch):
    _install_fake_anthropic()
    monkeypatch.setattr(da, "_call_haiku", _stateful_haiku([None]))
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")
    assert result == {
        "transcript": [], "verdict": None, "partial": True, "error": "round1_failed",
    }


def test_run_debate_round2_failure(monkeypatch):
    _install_fake_anthropic()
    monkeypatch.setattr(da, "_call_haiku", _stateful_haiku(["bull1", None]))
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")
    assert result["error"] == "round2_failed"
    assert result["partial"] is True
    assert result["transcript"] == [{"round": 1, "agent": "bull", "text": "bull1"}]


def test_run_debate_round3_failure(monkeypatch):
    _install_fake_anthropic()
    monkeypatch.setattr(da, "_call_haiku", _stateful_haiku(["bull1", "bear1", None]))
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")
    assert result["error"] == "round3_failed"
    assert len(result["transcript"]) == 2


def test_run_debate_round4_failure(monkeypatch):
    _install_fake_anthropic()
    monkeypatch.setattr(da, "_call_haiku", _stateful_haiku(["bull1", "bear1", "bull2", None]))
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")
    assert result["error"] == "round4_failed"
    assert len(result["transcript"]) == 3


def test_run_debate_full_success_populates_judge_fields(monkeypatch):
    _install_fake_anthropic()
    fake = _stateful_haiku(["bull1", "bear1", "bull2", "bear2", _JUDGE_JSON])
    monkeypatch.setattr(da, "_call_haiku", fake)
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")

    assert result["partial"] is False
    assert result["error"] is None
    assert result["verdict"] == "bull_wins"
    assert result["key_dispute"] == "growth vs valuation"
    assert result["bull_case_score"] == 80
    assert result["bear_case_score"] == 50
    assert result["grounded"] is True
    assert result["transcript"] == [
        {"round": 1, "agent": "bull", "text": "bull1"},
        {"round": 2, "agent": "bear", "text": "bear1"},
        {"round": 3, "agent": "bull", "text": "bull2"},
        {"round": 4, "agent": "bear", "text": "bear2"},
    ]


def test_run_debate_judge_unparseable_returns_error_sentinel_not_contested(monkeypatch):
    """Judge call succeeds but returns unparseable output → error sentinel, not "contested".
    A genuine 50/50 contested verdict comes from _parse_judge returning verdict="contested",
    not from a parse failure — the two must be distinguishable.
    """
    _install_fake_anthropic()
    fake = _stateful_haiku(["bull1", "bear1", "bull2", "bear2", "not valid json"])
    monkeypatch.setattr(da, "_call_haiku", fake)
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")

    assert result["verdict"] is None           # NOT "contested"
    assert result["error"] == "judge_failed"   # explicit infra-failure sentinel
    assert result["partial"] is True           # debate ran but verdict unavailable
    assert result["key_dispute"] is None
    assert result["bull_case_score"] is None
    assert result["bear_case_score"] is None
    assert result["grounded"] is None
    assert len(result["transcript"]) == 4      # rounds 1-4 are present


_JUDGE_JSON_CONTESTED = (
    '{"verdict": "contested", "key_dispute": "bulls and bears equally matched", '
    '"bull_case_score": 55, "bear_case_score": 55, "grounded": true}'
)


def test_run_debate_genuine_contested_verdict_distinct_from_judge_failure(monkeypatch):
    """A 50/50 judge result returns verdict='contested' (non-None) with no error."""
    _install_fake_anthropic()
    fake = _stateful_haiku(["bull1", "bear1", "bull2", "bear2", _JUDGE_JSON_CONTESTED])
    monkeypatch.setattr(da, "_call_haiku", fake)
    result = da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")

    assert result["verdict"] == "contested"    # genuine 50/50, not a failure
    assert result["error"] is None             # no error — judge succeeded
    assert result["partial"] is False
    assert result["key_dispute"] == "bulls and bears equally matched"


def test_run_debate_exit_vs_entry_round1_prompt_wording_differs(monkeypatch):
    _install_fake_anthropic()

    fake_exit = _stateful_haiku(["bull1", "bear1", "bull2", "bear2", _JUDGE_JSON])
    monkeypatch.setattr(da, "_call_haiku", fake_exit)
    da.run_debate({"ticker": "AAPL"}, "exit", api_key="fake-key")
    exit_round1_prompt = fake_exit.calls[0]["user_text"]
    assert "CONTINUING TO HOLD" in exit_round1_prompt

    fake_entry = _stateful_haiku(["bull1", "bear1", "bull2", "bear2", _JUDGE_JSON])
    monkeypatch.setattr(da, "_call_haiku", fake_entry)
    da.run_debate({"ticker": "AAPL"}, "entry", api_key="fake-key")
    entry_round1_prompt = fake_entry.calls[0]["user_text"]
    assert "entering" in entry_round1_prompt
    assert "CONTINUING TO HOLD" not in entry_round1_prompt
