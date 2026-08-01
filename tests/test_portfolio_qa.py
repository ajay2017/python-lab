"""
Tests for stock_analyzer/portfolio_qa.py (💬 Ask tab, 🧠 AI Insights).

Same pattern as tests/test_thesis_red_team.py: pin the None-vs-empty and
fail-open contracts, and the "never invent a value" discipline that backs
the parse/narrate prompts. The fake-anthropic-module helper (below) mirrors
tests/test_news_intelligence.py's `_install_fake_anthropic` so the real
success/parsing path (not just the except-fallback path) is exercised.
"""
import json
import sys
import types

import pandas as pd
import pytest

from stock_analyzer.portfolio_qa import (
    parse_parsed_query,
    build_parse_prompt,
    trades_in_range,
    trades_for_ticker,
    recommendation_outcome,
    facts_to_text,
    parse_question,
    narrate_answer,
)


# ── fake anthropic module helper (mirrors test_news_intelligence.py) ────────

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


# ─── parse_parsed_query ──────────────────────────────────────────────────────

def test_parse_parsed_query_valid_trades_in_range():
    raw = json.dumps({
        "intent": "trades_in_range", "ticker": None,
        "start_date": "2026-07-20", "end_date": "2026-07-27", "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["intent"] == "trades_in_range"
    assert result["start_date"] == "2026-07-20"
    assert result["end_date"] == "2026-07-27"


def test_parse_parsed_query_valid_rec_outcome():
    raw = json.dumps({
        "intent": "rec_outcome", "ticker": "aapl",
        "start_date": "2026-07-20", "end_date": None, "horizon_days": 5,
    })
    result = parse_parsed_query(raw)
    assert result["intent"] == "rec_outcome"
    assert result["ticker"] == "AAPL"  # uppercased
    assert result["horizon_days"] == 5


def test_parse_parsed_query_trades_in_range_missing_dates_becomes_unsupported():
    raw = json.dumps({
        "intent": "trades_in_range", "ticker": None,
        "start_date": None, "end_date": None, "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["intent"] == "unsupported"
    assert "date" in result["reason"].lower()


def test_parse_parsed_query_rec_outcome_missing_ticker_becomes_unsupported():
    raw = json.dumps({
        "intent": "rec_outcome", "ticker": None,
        "start_date": "2026-07-20", "end_date": None, "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["intent"] == "unsupported"
    assert "ticker" in result["reason"].lower()


def test_parse_parsed_query_valid_trade_lookup():
    raw = json.dumps({
        "intent": "trade_lookup", "ticker": "crwd",
        "start_date": None, "end_date": None, "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["intent"] == "trade_lookup"
    assert result["ticker"] == "CRWD"
    assert result["reason"] is None


def test_parse_parsed_query_trade_lookup_missing_ticker_becomes_unsupported():
    raw = json.dumps({
        "intent": "trade_lookup", "ticker": None,
        "start_date": None, "end_date": None, "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["intent"] == "unsupported"
    assert "ticker" in result["reason"].lower()


def test_parse_parsed_query_model_supplied_unsupported_reason_is_kept():
    raw = json.dumps({
        "intent": "unsupported", "ticker": None, "start_date": None,
        "end_date": None, "horizon_days": None,
        "reason": "This asks for a portfolio-wide return, which isn't supported yet.",
    })
    result = parse_parsed_query(raw)
    assert result["intent"] == "unsupported"
    assert result["reason"] == "This asks for a portfolio-wide return, which isn't supported yet."


def test_parse_parsed_query_unsupported_with_no_reason_gets_generic_fallback():
    raw = json.dumps({
        "intent": "unsupported", "ticker": None, "start_date": None,
        "end_date": None, "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["intent"] == "unsupported"
    assert result["reason"]  # non-empty fallback, never None/blank


def test_parse_parsed_query_valid_intents_have_no_reason():
    raw = json.dumps({
        "intent": "trade_lookup", "ticker": "AAPL",
        "start_date": None, "end_date": None, "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["reason"] is None


def test_parse_parsed_query_malformed_json_returns_none():
    assert parse_parsed_query("not json") is None


def test_parse_parsed_query_empty_string_returns_none():
    assert parse_parsed_query("") is None


def test_parse_parsed_query_invalid_intent_returns_none():
    raw = json.dumps({"intent": "delete_my_trades", "ticker": None,
                       "start_date": None, "end_date": None, "horizon_days": None})
    assert parse_parsed_query(raw) is None


def test_parse_parsed_query_not_a_dict_returns_none():
    assert parse_parsed_query(json.dumps(["not", "a", "dict"])) is None


def test_parse_parsed_query_strips_code_fences():
    raw = "```json\n" + json.dumps({
        "intent": "unsupported", "ticker": None,
        "start_date": None, "end_date": None, "horizon_days": None,
    }) + "\n```"
    result = parse_parsed_query(raw)
    assert result["intent"] == "unsupported"


def test_parse_parsed_query_trailing_prose_after_fence_no_closing_fence():
    # Regression test for a live production failure (2026-08-01): Haiku
    # opened a ```json fence, emitted valid JSON, then kept writing an
    # explanation afterward with NO closing fence — the old parser only
    # stripped trailing garbage when the cleaned string did NOT already
    # start with "{", so this well-formed-looking case slipped through
    # and broke json.loads on the extra prose.
    raw = (
        '```json\n{"intent": "unsupported", "ticker": null, "start_date": null, '
        '"end_date": null, "horizon_days": null}\n \n\nThe question asks about a '
        "trade on CrowdStrike (ticker: CRWD) but does not specify a date range."
    )
    result = parse_parsed_query(raw)
    assert result is not None
    assert result["intent"] == "unsupported"


def test_parse_parsed_query_trailing_prose_no_fence_at_all():
    raw = (
        '{"intent": "rec_outcome", "ticker": "CRWD", "start_date": null, '
        '"end_date": null, "horizon_days": null} — this assumes the most '
        "recent recommendation for CRWD."
    )
    result = parse_parsed_query(raw)
    assert result is not None
    assert result["intent"] == "rec_outcome"
    assert result["ticker"] == "CRWD"


def test_parse_parsed_query_invalid_date_string_treated_as_none():
    raw = json.dumps({
        "intent": "trades_in_range", "ticker": None,
        "start_date": "not-a-date", "end_date": "2026-07-27", "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    # start_date failed validation -> None -> trades_in_range requires both -> unsupported
    assert result["intent"] == "unsupported"


def test_build_parse_prompt_embeds_today():
    prompt = build_parse_prompt("2026-08-01")
    assert "2026-08-01" in prompt


def test_parse_parsed_query_range_within_max_not_clamped():
    raw = json.dumps({
        "intent": "trades_in_range", "ticker": None,
        "start_date": "2026-07-01", "end_date": "2026-07-31", "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["range_clamped"] is False
    assert result["start_date"] == "2026-07-01"


def test_parse_parsed_query_over_wide_range_gets_clamped():
    raw = json.dumps({
        "intent": "trades_in_range", "ticker": None,
        "start_date": "2020-01-01", "end_date": "2026-07-31", "horizon_days": None,
    })
    result = parse_parsed_query(raw)
    assert result["range_clamped"] is True
    # start_date walked forward to end_date - QA_MAX_RANGE_DAYS
    assert result["start_date"] > "2020-01-01"
    assert result["end_date"] == "2026-07-31"


# ─── trades_in_range ─────────────────────────────────────────────────────────

def _trades_df(rows):
    return pd.DataFrame(rows)


def test_trades_in_range_empty_df_returns_empty_list():
    assert trades_in_range(pd.DataFrame(), "2026-07-01", "2026-07-31") == []


def test_trades_in_range_none_df_returns_empty_list():
    assert trades_in_range(None, "2026-07-01", "2026-07-31") == []


def test_trades_in_range_sell_uses_stored_realized_pnl():
    df = _trades_df([{
        "ticker": "AAPL", "action": "SELL", "shares": 10, "price": 220.0,
        "realized_pnl": 150.0, "traded_at": "2026-07-15T10:00:00Z",
    }])
    result = trades_in_range(df, "2026-07-01", "2026-07-31")
    assert len(result) == 1
    assert result[0]["pnl"] == 150.0
    assert result[0]["pnl_label"] == "realized"


def test_trades_in_range_buy_still_held_computes_unrealized():
    df = _trades_df([{
        "ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
        "realized_pnl": None, "traded_at": "2026-07-15T10:00:00Z",
    }])
    result = trades_in_range(df, "2026-07-01", "2026-07-31", current_prices={"AAPL": 220.0})
    assert result[0]["pnl"] == pytest.approx(200.0)  # (220-200)*10
    assert result[0]["pnl_label"] == "unrealized"


def test_trades_in_range_buy_position_closed_when_no_current_price():
    df = _trades_df([{
        "ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
        "realized_pnl": None, "traded_at": "2026-07-15T10:00:00Z",
    }])
    result = trades_in_range(df, "2026-07-01", "2026-07-31", current_prices={})
    assert result[0]["pnl"] is None
    assert result[0]["pnl_label"] == "position_closed"


def test_trades_in_range_excludes_split_rows():
    df = _trades_df([
        {"ticker": "AAPL", "action": "SPLIT", "shares": 20, "price": 0,
         "realized_pnl": None, "traded_at": "2026-07-15T10:00:00Z"},
        {"ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
         "realized_pnl": None, "traded_at": "2026-07-16T10:00:00Z"},
    ])
    result = trades_in_range(df, "2026-07-01", "2026-07-31")
    assert len(result) == 1
    assert result[0]["action"] == "BUY"


def test_trades_in_range_filters_outside_date_range():
    df = _trades_df([
        {"ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
         "realized_pnl": None, "traded_at": "2026-06-01T10:00:00Z"},
        {"ticker": "AAPL", "action": "BUY", "shares": 5, "price": 210.0,
         "realized_pnl": None, "traded_at": "2026-07-15T10:00:00Z"},
    ])
    result = trades_in_range(df, "2026-07-01", "2026-07-31")
    assert len(result) == 1
    assert result[0]["shares"] == 5


def test_trades_in_range_end_date_is_inclusive():
    df = _trades_df([{
        "ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
        "realized_pnl": None, "traded_at": "2026-07-31T23:00:00Z",
    }])
    result = trades_in_range(df, "2026-07-01", "2026-07-31")
    assert len(result) == 1


# ─── trades_for_ticker ───────────────────────────────────────────────────────

def test_trades_for_ticker_empty_df_returns_empty_list():
    assert trades_for_ticker(pd.DataFrame(), "AAPL") == []


def test_trades_for_ticker_none_df_returns_empty_list():
    assert trades_for_ticker(None, "AAPL") == []


def test_trades_for_ticker_no_ticker_returns_empty_list():
    df = _trades_df([{"ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
                       "realized_pnl": None, "traded_at": "2026-07-15T10:00:00Z"}])
    assert trades_for_ticker(df, "") == []


def test_trades_for_ticker_filters_to_matching_ticker_only():
    df = _trades_df([
        {"ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
         "realized_pnl": None, "traded_at": "2026-06-01T10:00:00Z"},
        {"ticker": "CRWD", "action": "BUY", "shares": 5, "price": 300.0,
         "realized_pnl": None, "traded_at": "2026-07-01T10:00:00Z"},
    ])
    result = trades_for_ticker(df, "crwd")
    assert len(result) == 1
    assert result[0]["ticker"] == "CRWD"


def test_trades_for_ticker_no_date_filter_spans_all_history():
    df = _trades_df([
        {"ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
         "realized_pnl": None, "traded_at": "2020-01-01T10:00:00Z"},
        {"ticker": "AAPL", "action": "SELL", "shares": 10, "price": 250.0,
         "realized_pnl": 500.0, "traded_at": "2026-07-01T10:00:00Z"},
    ])
    result = trades_for_ticker(df, "AAPL")
    assert len(result) == 2
    assert result[0]["traded_at"] == "2020-01-01"  # oldest first
    assert result[1]["pnl"] == 500.0


def test_trades_for_ticker_excludes_split_rows():
    df = _trades_df([
        {"ticker": "AAPL", "action": "SPLIT", "shares": 20, "price": 0,
         "realized_pnl": None, "traded_at": "2026-07-15T10:00:00Z"},
        {"ticker": "AAPL", "action": "BUY", "shares": 10, "price": 200.0,
         "realized_pnl": None, "traded_at": "2026-07-16T10:00:00Z"},
    ])
    result = trades_for_ticker(df, "AAPL")
    assert len(result) == 1
    assert result[0]["action"] == "BUY"


# ─── recommendation_outcome ──────────────────────────────────────────────────

def _recs_df(rows):
    return pd.DataFrame(rows)


def test_recommendation_outcome_no_match_returns_found_false():
    recs = _recs_df([{"ticker": "MSFT", "rec_date": "2026-07-20", "rec_type": "new_pick"}])
    result = recommendation_outcome("AAPL", "2026-07-20", recs)
    assert result["found"] is False
    assert "no recommendation on record" in result["reason"]


def test_recommendation_outcome_empty_recs_df_returns_found_false():
    result = recommendation_outcome("AAPL", "2026-07-20", pd.DataFrame())
    assert result["found"] is False


def test_recommendation_outcome_match_returns_pillar_scores():
    recs = _recs_df([{
        "ticker": "AAPL", "rec_date": "2026-07-20", "rec_type": "new_pick",
        "composite_score": 75, "conviction": "high", "thesis": "uptrend",
        "t_score": 80, "bq_score": 70, "val_score": 58,
        "price_at_surface": 200.0,
    }])
    result = recommendation_outcome("aapl", "2026-07-20", recs)
    assert result["found"] is True
    assert result["composite_score"] == 75
    assert result["t_score"] == 80
    assert result["val_score"] == 58


def test_recommendation_outcome_pre_pillar_persistence_row_has_none_pillars():
    # Simulates a row saved before the 2026-08-01 pillar-persistence change —
    # columns are simply absent from the loaded frame.
    recs = _recs_df([{
        "ticker": "AAPL", "rec_date": "2026-07-20", "rec_type": "new_pick",
        "composite_score": 75, "conviction": "high", "thesis": "uptrend",
        "price_at_surface": 200.0,
    }])
    result = recommendation_outcome("AAPL", "2026-07-20", recs)
    assert result["found"] is True
    assert result["t_score"] is None
    assert result["bq_score"] is None
    assert result["val_score"] is None


def test_recommendation_outcome_computes_price_move_when_history_given():
    recs = _recs_df([{
        "ticker": "AAPL", "rec_date": "2026-07-20", "rec_type": "new_pick",
        "composite_score": 75, "price_at_surface": 200.0,
    }])
    dates = pd.date_range("2026-07-20", periods=10, freq="D")
    hist = pd.DataFrame({"Close": [200, 198, 195, 190, 185, 180, 178, 176, 174, 172]}, index=dates)
    result = recommendation_outcome("AAPL", "2026-07-20", recs, price_history_df=hist, horizon_days=5)
    assert result["price_at_horizon"] == 180.0
    assert result["pct_move"] == pytest.approx(-10.0)


def test_recommendation_outcome_not_enough_forward_history_leaves_move_none():
    recs = _recs_df([{
        "ticker": "AAPL", "rec_date": "2026-07-20", "rec_type": "new_pick",
        "composite_score": 75, "price_at_surface": 200.0,
    }])
    dates = pd.date_range("2026-07-20", periods=3, freq="D")
    hist = pd.DataFrame({"Close": [200, 198, 195]}, index=dates)
    result = recommendation_outcome("AAPL", "2026-07-20", recs, price_history_df=hist, horizon_days=5)
    assert result["found"] is True
    assert result["price_at_horizon"] is None
    assert result["pct_move"] is None


# ─── facts_to_text — the "never invent a value" discipline ──────────────────

def test_facts_to_text_rec_outcome_states_missing_pillars_plainly():
    facts = {
        "found": True, "ticker": "AAPL", "rec_date": "2026-07-20", "rec_type": "new_pick",
        "composite_score": 75, "conviction": "high", "thesis": None,
        "t_score": None, "bq_score": None, "val_score": None,
        "price_at_surface": 200.0, "horizon_days": 5,
        "price_at_horizon": None, "pct_move": None,
    }
    text = facts_to_text("rec_outcome", facts)
    assert "not recorded for this recommendation" in text


def test_facts_to_text_rec_outcome_not_found_states_reason():
    text = facts_to_text("rec_outcome", {"found": False, "reason": "no recommendation on record for that ticker/date"})
    assert "no recommendation on record" in text


def test_facts_to_text_trades_in_range_empty_says_no_trades():
    text = facts_to_text("trades_in_range", [])
    assert "No trades" in text


def test_facts_to_text_trade_lookup_empty_says_no_trades_on_record():
    text = facts_to_text("trade_lookup", [])
    assert "No trades on record" in text


def test_facts_to_text_trade_lookup_renders_trade_list():
    facts = [{"ticker": "CRWD", "action": "BUY", "shares": 5, "price": 300.0,
              "traded_at": "2026-07-01", "pnl": 100.0, "pnl_label": "unrealized"}]
    text = facts_to_text("trade_lookup", facts)
    assert "CRWD" in text
    assert "100.00" in text


# ─── generate_* — fail-open contract ─────────────────────────────────────────

def test_parse_question_no_api_key_returns_none():
    assert parse_question("how many trades last week", "", "2026-08-01") is None


def test_parse_question_empty_question_returns_none():
    assert parse_question("", "fake-key", "2026-08-01") is None


def test_narrate_answer_no_api_key_returns_none():
    assert narrate_answer("trades_in_range", [], "") is None


# ─── LAST_*_ERROR diagnostics — mirrors analyst_intel.LAST_EXTRACT_ERROR ────

def test_parse_question_no_api_key_sets_last_parse_error():
    import stock_analyzer.portfolio_qa as qa_mod
    qa_mod.parse_question("how many trades last week", "", "2026-08-01")
    assert qa_mod.LAST_PARSE_ERROR is not None


def test_narrate_answer_no_api_key_sets_last_narrate_error():
    import stock_analyzer.portfolio_qa as qa_mod
    qa_mod.narrate_answer("trades_in_range", [], "")
    assert qa_mod.LAST_NARRATE_ERROR is not None


# ─── narrate_answer — dollar-sign escaping (live production bug, 2026-08-02) ─
#
# Streamlit's st.markdown renders a $...$ pair as inline LaTeX math. A real
# answer mentioning two (or more) dollar amounts — routine for this feature —
# had the prose BETWEEN the first and second "$" silently swallowed into a
# garbled math span in production. The fix escapes every literal "$" to "\$"
# before the text is returned to the caller (which renders it via
# st.markdown), independent of what the caller does.

def test_narrate_answer_escapes_dollar_signs_in_response():
    _install_fake_anthropic(
        "You bought 5 shares at $117.77 and sold at $95.77 for a loss."
    )
    answer = narrate_answer("trade_lookup", [], "fake-key")
    assert answer == (
        "You bought 5 shares at \\$117.77 and sold at \\$95.77 for a loss."
    )
    assert "95.77" in answer


def test_narrate_answer_single_dollar_sign_also_escaped():
    _install_fake_anthropic("The realized loss was $181.15.")
    answer = narrate_answer("trade_lookup", [], "fake-key")
    assert answer == "The realized loss was \\$181.15."


def test_narrate_answer_no_dollar_signs_unaffected():
    _install_fake_anthropic("No dollar amounts mentioned here at all.")
    answer = narrate_answer("trade_lookup", [], "fake-key")
    assert answer == "No dollar amounts mentioned here at all."


def test_narrate_answer_real_exception_sets_last_narrate_error_message():
    import stock_analyzer.portfolio_qa as qa_mod
    _install_fake_anthropic(raise_exc=RuntimeError("rate limit exceeded"))
    answer = qa_mod.narrate_answer("trade_lookup", [], "fake-key")
    assert answer is None
    assert "rate limit exceeded" in qa_mod.LAST_NARRATE_ERROR


def test_narrate_answer_empty_response_returns_none_with_reason():
    import stock_analyzer.portfolio_qa as qa_mod
    _install_fake_anthropic("")
    answer = qa_mod.narrate_answer("trade_lookup", [], "fake-key")
    assert answer is None
    assert qa_mod.LAST_NARRATE_ERROR == "model returned an empty response"


# ─── parse_question — real success path via the fake client ────────────────

def test_parse_question_real_success_path_via_fake_client():
    _install_fake_anthropic(json.dumps({
        "intent": "trade_lookup", "ticker": "HOOD",
        "start_date": None, "end_date": None, "horizon_days": None,
    }))
    result = parse_question("how much did I lose on HOOD trade?", "fake-key", "2026-08-02")
    assert result is not None
    assert result["intent"] == "trade_lookup"
    assert result["ticker"] == "HOOD"


def test_parse_question_real_exception_sets_last_parse_error_message():
    import stock_analyzer.portfolio_qa as qa_mod
    _install_fake_anthropic(raise_exc=RuntimeError("connection reset"))
    result = qa_mod.parse_question("how much did I lose on HOOD trade?", "fake-key", "2026-08-02")
    assert result is None
    assert "connection reset" in qa_mod.LAST_PARSE_ERROR
