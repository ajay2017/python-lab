"""
Tests for stock_analyzer/broker_screenshot.py — Robinhood "History" plain-text
paste parser. Pure text/regex parsing + a Claude text-API ticker-resolution
fallback, no Streamlit/DB. Zero coverage before this batch. The LLM-calling
`_resolve_unknown_tickers` is exercised via a fake `sys.modules["anthropic"]`
module (see tests/test_news_intelligence.py for the pattern) for a success
round trip, a null-mapped-ticker fallback, and an exception path; the
empty-unknowns / empty-api_key guards return before `import anthropic` runs
and need no mocking.
"""
import sys
import types
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import broker_screenshot as bs


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


# ─── text-block builders (Robinhood History paste shape) ─────────────────────

def _executed_block(company, order_type, action, date_str, total, shares, price):
    return (
        f"{company} {order_type} {action}\n"
        f"Individual · {date_str}\n"
        f"${total}\n"
        f"{shares} shares at ${price}\n"
    )


def _canceled_block(company, order_type, action, date_str):
    return (
        f"{company} {order_type} {action}\n"
        f"Individual · {date_str}\n"
        f"Canceled\n"
    )


# ─── _lookup_ticker ───────────────────────────────────────────────────────────

def test_lookup_ticker_known_company_case_insensitive():
    assert bs._lookup_ticker("apple inc") == ("AAPL", "high")
    assert bs._lookup_ticker("APPLE INC") == ("AAPL", "high")


def test_lookup_ticker_low_conf_name():
    assert bs._lookup_ticker("SpaceX") == ("SPACEX", "low")
    assert bs._lookup_ticker("spacex") == ("SPACEX", "low")


def test_lookup_ticker_unknown_returns_empty_tuple():
    assert bs._lookup_ticker("Some Obscure Company") == ("", "")


# ─── _infer_year ──────────────────────────────────────────────────────────────

def test_infer_year_same_month_no_rollback():
    reference = date(2026, 7, 15)
    assert bs._infer_year("Jul 9", reference) == date(2026, 7, 9)


def test_infer_year_exactly_one_day_ahead_no_rollback():
    reference = date(2026, 1, 5)
    # candidate == reference + 1 day exactly -- boundary, must NOT roll back.
    assert bs._infer_year("Jan 6", reference) == date(2026, 1, 6)


def test_infer_year_two_days_ahead_rolls_back_to_prior_year():
    reference = date(2026, 1, 5)
    # candidate == reference + 2 days -- strictly more than 1 day ahead.
    assert bs._infer_year("Jan 7", reference) == date(2025, 1, 7)


def test_infer_year_far_future_month_rolls_back_to_prior_year():
    reference = date(2026, 1, 5)
    assert bs._infer_year("Dec 31", reference) == date(2025, 12, 31)


def test_infer_year_explicit_year_parsed_as_is_no_rollback_logic():
    reference = date(2026, 1, 5)
    assert bs._infer_year("Jul 9, 2024", reference) == date(2024, 7, 9)


def test_infer_year_unparseable_returns_none():
    assert bs._infer_year("Not A Date", date(2026, 1, 5)) is None


# ─── _parse_text_blocks ───────────────────────────────────────────────────────

def test_parse_text_blocks_two_executed_orders_with_blank_line_between():
    text = (
        _executed_block("Apple Inc", "market", "buy", "Jul 9", "1,502.30", "10", "150.23")
        + "\n"
        + _executed_block("Tesla", "stop", "sell", "Jul 10", "1,000.00", "5", "200.00")
    )
    trades, canceled = bs._parse_text_blocks(text)
    assert canceled == 0
    assert len(trades) == 2
    t0, t1 = trades
    assert t0["company"] == "Apple Inc"
    assert t0["action"] == "BUY"
    assert t0["date_str"] == "Jul 9"
    assert t0["shares"] == 10.0
    assert t0["price"] == 150.23
    assert t1["company"] == "Tesla"
    assert t1["action"] == "SELL"
    assert t1["shares"] == 5.0
    assert t1["price"] == 200.0


def test_parse_text_blocks_canceled_order_not_added_to_trades():
    text = _canceled_block("Microsoft Corporation", "limit", "buy", "Jul 11")
    trades, canceled = bs._parse_text_blocks(text)
    assert trades == []
    assert canceled == 1


def test_parse_text_blocks_malformed_action_line_skipped():
    text = (
        "NotAValidActionLine\n"
        "Individual · Jul 12\n"
        "$100.00\n"
        "5 shares at $20.00\n"
    )
    trades, canceled = bs._parse_text_blocks(text)
    assert trades == []
    assert canceled == 0


def test_parse_text_blocks_malformed_status_line_skipped():
    text = (
        "Apple Inc market buy\n"
        "Individual · Jul 13\n"
        "Not a valid line\n"
        "5 shares at $20.00\n"
    )
    trades, canceled = bs._parse_text_blocks(text)
    assert trades == []
    assert canceled == 0


def test_parse_text_blocks_malformed_shares_line_skipped():
    text = (
        "Apple Inc market buy\n"
        "Individual · Jul 14\n"
        "$100.00\n"
        "not a valid shares line\n"
    )
    trades, canceled = bs._parse_text_blocks(text)
    assert trades == []
    assert canceled == 0


# ─── _resolve_unknown_tickers ─────────────────────────────────────────────────

def test_resolve_unknown_tickers_empty_unknowns_returns_empty_dict_no_call():
    assert bs._resolve_unknown_tickers([], "fake-key", "model") == {}


def test_resolve_unknown_tickers_no_api_key_returns_empty_dict_no_call():
    assert bs._resolve_unknown_tickers(["Some Company"], "", "model") == {}


def test_resolve_unknown_tickers_markdown_fenced_success_round_trip():
    _install_fake_anthropic('```json\n{"Some Company": "TICK"}\n```')
    result = bs._resolve_unknown_tickers(["Some Company"], "fake-key", "model")
    assert result == {"some company": ("TICK", "high")}


def test_resolve_unknown_tickers_null_ticker_falls_back_to_low_conf_name():
    _install_fake_anthropic('{"Some Company": null}')
    result = bs._resolve_unknown_tickers(["Some Company"], "fake-key", "model")
    assert result == {"some company": ("SOME COMPANY", "low")}


def test_resolve_unknown_tickers_client_exception_returns_empty_dict():
    _install_fake_anthropic(raise_exc=RuntimeError("boom"))
    result = bs._resolve_unknown_tickers(["Some Company"], "fake-key", "model")
    assert result == {}


# ─── parse_robinhood_text ─────────────────────────────────────────────────────

def test_parse_robinhood_text_empty_text_error():
    result = bs.parse_robinhood_text("")
    assert result["error"] == "No text provided."
    assert result["trades"].empty
    assert result["invalid"].empty
    assert result["skipped"] == {}
    assert result["low_confidence_tickers"] == []
    assert result["parse_warnings"] == []


def test_parse_robinhood_text_whitespace_only_text_error():
    result = bs.parse_robinhood_text("   \n  \n ")
    assert result["error"] == "No text provided."


def test_parse_robinhood_text_no_parseable_trades_no_cancellations_error():
    result = bs.parse_robinhood_text("garbage\nmore garbage\n")
    assert result["error"] is not None
    assert "No trades found" in result["error"]


def test_parse_robinhood_text_happy_path_known_ticker():
    text = _executed_block("Apple Inc", "market", "buy", "Jul 9", "1,502.30", "10", "150.23")
    result = bs.parse_robinhood_text(text, reference_date=date(2026, 7, 15))
    assert result["error"] is None
    assert len(result["trades"]) == 1
    row = result["trades"].iloc[0]
    assert row["ticker"] == "AAPL"
    assert row["action"] == "BUY"
    assert row["activity_date"] == date(2026, 7, 9)


def test_parse_robinhood_text_invalid_unparseable_date():
    text = _executed_block("Apple Inc", "market", "buy", "Xyz 9", "100.00", "5", "20.00")
    result = bs.parse_robinhood_text(text, reference_date=date(2026, 7, 15))
    assert len(result["invalid"]) == 1
    assert "unparseable date" in result["invalid"].iloc[0]["reason"]


def test_parse_robinhood_text_invalid_zero_shares():
    text = _executed_block("Apple Inc", "market", "buy", "Jul 9", "100.00", "0", "20.00")
    result = bs.parse_robinhood_text(text, reference_date=date(2026, 7, 15))
    assert len(result["invalid"]) == 1
    assert "shares" in result["invalid"].iloc[0]["reason"]


def test_parse_robinhood_text_invalid_zero_price():
    text = _executed_block("Apple Inc", "market", "buy", "Jul 9", "0.00", "5", "0.00")
    result = bs.parse_robinhood_text(text, reference_date=date(2026, 7, 15))
    assert len(result["invalid"]) == 1
    assert "price" in result["invalid"].iloc[0]["reason"]


# NOTE: no test for the "no ticker resolved" invalid reason -- it is
# unreachable via the public parse_robinhood_text function. _parse_text_blocks
# strips every line (`ln.strip()`) *before* _ACTION_RE ever sees it, so a
# company group can never collapse to whitespace-only; _ACTION_RE also
# requires 1+ non-whitespace-collapsing char in the company group, and the
# ticker-resolution fallback (`company.upper()`) is therefore always
# non-empty for any company string _parse_text_blocks can actually produce.
# Confirmed by direct experimentation: a doubled-leading-space line that
# would make the *raw* regex capture a lone space as the company still fails
# once passed through the real pipeline, because the leading padding is
# stripped away first. See final report.
# Similarly, "unknown action '{action}'" is dead code: _ACTION_RE's action
# group is the literal alternation (buy|sell), so `action.upper()` is always
# "BUY" or "SELL" -- both already in `_TRADE_ACTIONS`. Also unreachable via
# the public function; not tested for the same reason.


def test_parse_robinhood_text_duplicate_pasted_block_deduplicated():
    block = _executed_block("Apple Inc", "market", "buy", "Jul 9", "1,502.30", "10", "150.23")
    text = block + "\n" + block
    result = bs.parse_robinhood_text(text, reference_date=date(2026, 7, 15))
    assert len(result["trades"]) == 1


def test_parse_robinhood_text_low_confidence_tickers_populated():
    text = _executed_block("SpaceX", "market", "buy", "Jul 9", "100.00", "5", "20.00")
    result = bs.parse_robinhood_text(text, reference_date=date(2026, 7, 15))
    assert result["low_confidence_tickers"] == ["SPACEX"]


def test_parse_robinhood_text_parse_warnings_populated_when_resolver_returns_empty(monkeypatch):
    monkeypatch.setattr(bs, "_resolve_unknown_tickers", lambda unknowns, api_key, model: {})
    text = _executed_block("Some Unknown Co", "market", "buy", "Jul 9", "100.00", "5", "20.00")
    result = bs.parse_robinhood_text(text, api_key="fake-key", reference_date=date(2026, 7, 15))
    assert len(result["parse_warnings"]) == 1
    assert "Some Unknown Co" in result["parse_warnings"][0]


# ─── find_app_only_in_range ───────────────────────────────────────────────────

def _screenshot_trades(rows):
    cols = ["ticker", "action", "shares", "price", "activity_date", "company"]
    return pd.DataFrame(rows, columns=cols)


def test_find_app_only_in_range_none_trades_df_empty_with_columns():
    result = bs.find_app_only_in_range(_screenshot_trades([]), None, date(2026, 1, 1), date(2026, 1, 31))
    assert result.empty
    assert list(result.columns) == ["ticker", "action", "shares", "price", "traded_at"]


def test_find_app_only_in_range_empty_trades_df_empty_result():
    empty = pd.DataFrame(columns=["ticker", "action", "shares", "price", "traded_at"])
    result = bs.find_app_only_in_range(_screenshot_trades([]), empty, date(2026, 1, 1), date(2026, 1, 31))
    assert result.empty


def test_find_app_only_in_range_non_dataframe_trades_df_empty_result():
    result = bs.find_app_only_in_range(_screenshot_trades([]), "not a dataframe", date(2026, 1, 1), date(2026, 1, 31))
    assert result.empty


def test_find_app_only_in_range_row_outside_date_range_excluded():
    trades_df = pd.DataFrame([
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "traded_at": "2026-02-15T09:30:00Z"},
    ])
    result = bs.find_app_only_in_range(_screenshot_trades([]), trades_df, date(2026, 1, 1), date(2026, 1, 31))
    assert result.empty


def test_find_app_only_in_range_non_buy_sell_action_excluded():
    trades_df = pd.DataFrame([
        {"ticker": "AAPL", "action": "DIVIDEND", "shares": 10.0, "price": 150.0,
         "traded_at": "2026-01-15T09:30:00Z"},
    ])
    result = bs.find_app_only_in_range(_screenshot_trades([]), trades_df, date(2026, 1, 1), date(2026, 1, 31))
    assert result.empty


def test_find_app_only_in_range_multiplicity_second_occurrence_unmatched():
    # 1 screenshot row matches the key; 2 app rows share the same key -- the
    # first consumes the screenshot match budget, the second has none left
    # and appears in the "app only" output.
    screenshot = _screenshot_trades([
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "activity_date": date(2026, 1, 15), "company": "Apple Inc"},
    ])
    trades_df = pd.DataFrame([
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "traded_at": "2026-01-15T09:30:00Z"},
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "traded_at": "2026-01-16T09:30:00Z"},
    ])
    result = bs.find_app_only_in_range(screenshot, trades_df, date(2026, 1, 1), date(2026, 1, 31))
    assert len(result) == 1
    assert result.iloc[0]["traded_at"] == "2026-01-16"


# ─── last_screenshot_sync_date ────────────────────────────────────────────────

def test_last_screenshot_sync_date_none_or_empty_or_non_dataframe_returns_none():
    assert bs.last_screenshot_sync_date(None) is None
    assert bs.last_screenshot_sync_date(pd.DataFrame()) is None
    assert bs.last_screenshot_sync_date("not a dataframe") is None


def test_last_screenshot_sync_date_missing_notes_column_returns_none():
    trades_df = pd.DataFrame([{"traded_at": "2026-01-15T09:30:00Z"}])
    assert bs.last_screenshot_sync_date(trades_df) is None


def test_last_screenshot_sync_date_missing_traded_at_column_returns_none():
    trades_df = pd.DataFrame([{"notes": "RH screenshot import"}])
    assert bs.last_screenshot_sync_date(trades_df) is None


def test_last_screenshot_sync_date_returns_max_matching_row_date():
    trades_df = pd.DataFrame([
        {"notes": "RH screenshot import", "traded_at": "2026-01-10T09:30:00Z"},
        {"notes": "manual entry", "traded_at": "2026-01-25T09:30:00Z"},
        {"notes": "RH text import batch", "traded_at": "2026-01-20T09:30:00Z"},
    ])
    result = bs.last_screenshot_sync_date(trades_df)
    assert result == date(2026, 1, 20)
