"""Regression tests for stock_analyzer/premortem_monitor.py — Pre-Commitment
Enforcement (docs/plans/premortem-enforcement.md). Covers the LLM extraction
helper (_parse_trigger, extract_trigger — with a fake anthropic module, same
pattern as test_news_intelligence.py's _install_fake_anthropic, since the dev
venv has no anthropic installed) and the pure, zero-LLM-cost daily check
(detect_premortem_triggers), including the three false-trigger classes the
2026-08-03 Opus design review caught: stock-split staleness, sell-then-rebuy
lot mis-attribution, and "ever crossed since BUY" stale nags.
"""
import sys
import types
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import premortem_monitor as pm


# ── fake anthropic module helper (mirrors test_news_intelligence.py) ────────

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)] if text is not None else []


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


def _hist(prices, start="2024-01-01"):
    idx = pd.date_range(start=start, periods=len(prices), freq="D")
    return pd.DataFrame({"Close": prices}, index=idx)


# ── _parse_trigger ───────────────────────────────────────────────────────────

def test_parse_trigger_checkable_below():
    result = pm._parse_trigger('{"checkable": true, "direction": "below", "price_level": 150.0}')
    assert result == {"checkable": True, "direction": "below", "price_level": 150.0}


def test_parse_trigger_checkable_above():
    result = pm._parse_trigger('{"checkable": true, "direction": "above", "price_level": 42.5}')
    assert result == {"checkable": True, "direction": "above", "price_level": 42.5}


def test_parse_trigger_not_checkable():
    result = pm._parse_trigger('{"checkable": false, "direction": null, "price_level": null}')
    assert result == {"checkable": False, "direction": None, "price_level": None}


def test_parse_trigger_strips_markdown_fence():
    result = pm._parse_trigger('```json\n{"checkable": true, "direction": "below", "price_level": 10}\n```')
    assert result == {"checkable": True, "direction": "below", "price_level": 10.0}


def test_parse_trigger_garbage_text_returns_none():
    assert pm._parse_trigger("not json at all") is None


def test_parse_trigger_empty_returns_none():
    assert pm._parse_trigger("") is None
    assert pm._parse_trigger(None) is None


def test_parse_trigger_bad_direction_returns_none():
    assert pm._parse_trigger('{"checkable": true, "direction": "sideways", "price_level": 10}') is None


def test_parse_trigger_negative_price_returns_none():
    assert pm._parse_trigger('{"checkable": true, "direction": "below", "price_level": -5}') is None


def test_parse_trigger_missing_checkable_key_returns_none():
    assert pm._parse_trigger('{"direction": "below", "price_level": 10}') is None


def test_parse_trigger_non_bool_checkable_returns_none():
    assert pm._parse_trigger('{"checkable": "yes", "direction": "below", "price_level": 10}') is None


# ── extract_trigger ──────────────────────────────────────────────────────────

def test_extract_trigger_no_api_key_returns_none():
    assert pm.extract_trigger("if it breaks $150", api_key="") is None


def test_extract_trigger_empty_commitment_returns_none():
    assert pm.extract_trigger("   ", api_key="fake-key") is None


def test_extract_trigger_success_checkable():
    _install_fake_anthropic('{"checkable": true, "direction": "below", "price_level": 150.0}')
    result = pm.extract_trigger("I'll exit if it breaks $150", api_key="fake-key")
    assert result == {"checkable": True, "direction": "below", "price_level": 150.0}


def test_extract_trigger_success_not_checkable():
    _install_fake_anthropic('{"checkable": false, "direction": null, "price_level": null}')
    result = pm.extract_trigger("If Q3 guidance disappoints", api_key="fake-key")
    assert result == {"checkable": False, "direction": None, "price_level": None}


def test_extract_trigger_api_exception_returns_none():
    _install_fake_anthropic(raise_exc=RuntimeError("API down"))
    assert pm.extract_trigger("if it breaks $150", api_key="fake-key") is None


def test_extract_trigger_malformed_response_returns_none():
    _install_fake_anthropic("this is not JSON")
    assert pm.extract_trigger("if it breaks $150", api_key="fake-key") is None


# ── detect_premortem_triggers ───────────────────────────────────────────────

def _buy_row(ticker, trigger_price, direction, traded_at="2024-01-01T10:00:00Z"):
    return {
        "id": 1, "ticker": ticker, "action": "BUY", "shares": 10,
        "traded_at": traded_at,
        "premortem_trigger_price": trigger_price,
        "premortem_trigger_direction": direction,
    }


def test_detect_active_below_trigger():
    trades = pd.DataFrame([_buy_row("AAA", 150.0, "below")])
    held_data = {"AAA": {"df": _hist([160, 155, 140, 130, 125])}}
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5))
    assert len(result) == 1
    r = result[0]
    assert r["ticker"] == "AAA"
    assert r["trigger_price"] == 150.0
    assert r["current_price"] == 125.0
    assert r["first_breach_date"] == date(2024, 1, 3)
    assert r["days_since"] == 2


def test_detect_recovered_trigger_is_self_resolving():
    """Blocking finding #3: a dip that recovers must stop firing, not nag forever."""
    trades = pd.DataFrame([_buy_row("AAA", 150.0, "below")])
    held_data = {"AAA": {"df": _hist([160, 155, 140, 130, 200])}}  # recovered on day 5
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5))
    assert result == []


def test_detect_active_above_trigger():
    trades = pd.DataFrame([_buy_row("BBB", 50.0, "above")])
    held_data = {"BBB": {"df": _hist([40, 45, 55, 60, 65])}}
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5))
    assert len(result) == 1
    assert result[0]["direction"] == "above"


def test_detect_not_checkable_is_skipped():
    trades = pd.DataFrame([_buy_row("CCC", None, "not_checkable")])
    held_data = {"CCC": {"df": _hist([10, 5, 3, 2, 1])}}
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5))
    assert result == []


def test_detect_not_yet_extracted_is_skipped():
    """direction=None (both columns NULL) means extraction hasn't run / failed
    — must never be confused with 'not_checkable'."""
    trades = pd.DataFrame([_buy_row("CCC", None, None)])
    held_data = {"CCC": {"df": _hist([10, 5, 3, 2, 1])}}
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5))
    assert result == []


def test_detect_split_adjusts_trigger_price():
    """Blocking finding #1: a 2:1 split must halve the stored trigger price
    before comparison, or a stale pre-split price permanently misfires."""
    trades = pd.DataFrame([
        _buy_row("DDD", 150.0, "below"),
        {"id": 2, "ticker": "DDD", "action": "SPLIT", "shares": 20,
         "traded_at": "2024-01-03T10:00:00Z"},
    ])
    held_data = {"DDD": {"df": _hist([150, 148, 76, 74, 70])}}  # post-split prices
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5))
    assert len(result) == 1
    assert result[0]["trigger_price"] == pytest.approx(75.0)


def test_detect_sell_then_rebuy_uses_new_lot_not_old_commitment():
    """Blocking finding #2: a closed lot's commitment must never be
    resurfaced against a brand-new position on the same ticker."""
    trades = pd.DataFrame([
        _buy_row("EEE", 100.0, "below", traded_at="2024-01-01T10:00:00Z"),
        {"id": 2, "ticker": "EEE", "action": "SELL", "shares": 10,
         "traded_at": "2024-01-05T10:00:00Z"},
        _buy_row("EEE", 300.0, "below", traded_at="2024-01-10T10:00:00Z"),
    ])
    # New lot's trigger (300) is active; old lot's trigger (100) must not apply.
    held_data = {"EEE": {"df": _hist([310, 305, 295, 290, 280], start="2024-01-10")}}
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 14))
    assert len(result) == 1
    assert result[0]["trigger_price"] == 300.0


def test_detect_no_open_lots_returns_empty():
    trades = pd.DataFrame([
        _buy_row("FFF", 100.0, "below"),
        {"id": 2, "ticker": "FFF", "action": "SELL", "shares": 10,
         "traded_at": "2024-01-05T10:00:00Z"},
    ])
    held_data = {"FFF": {"df": _hist([90, 85, 80])}}
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 10))
    assert result == []


def test_detect_no_ddl_applied_returns_empty_not_error():
    """The DDL-not-yet-applied case: columns simply don't exist on trades_df."""
    trades = pd.DataFrame([{"id": 1, "ticker": "GGG", "action": "BUY", "shares": 10,
                             "traded_at": "2024-01-01T10:00:00Z"}])
    held_data = {"GGG": {"df": _hist([10, 9, 8])}}
    result = pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5))
    assert result == []


def test_detect_no_trades_df_returns_empty():
    assert pm.detect_premortem_triggers(None, {"AAA": {"df": _hist([10])}}, date(2024, 1, 1)) == []
    assert pm.detect_premortem_triggers(pd.DataFrame(), {"AAA": {}}, date(2024, 1, 1)) == []


def test_detect_no_held_data_returns_empty():
    trades = pd.DataFrame([_buy_row("AAA", 150.0, "below")])
    assert pm.detect_premortem_triggers(trades, None, date(2024, 1, 5)) == []
    assert pm.detect_premortem_triggers(trades, {}, date(2024, 1, 5)) == []


def test_detect_ticker_missing_from_held_data_is_skipped():
    trades = pd.DataFrame([_buy_row("AAA", 150.0, "below")])
    held_data = {"ZZZ": {"df": _hist([10, 9, 8])}}  # AAA not present
    assert pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5)) == []


def test_detect_empty_price_history_is_skipped():
    trades = pd.DataFrame([_buy_row("AAA", 150.0, "below")])
    held_data = {"AAA": {"df": pd.DataFrame({"Close": []})}}
    assert pm.detect_premortem_triggers(trades, held_data, date(2024, 1, 5)) == []
