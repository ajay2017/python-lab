"""Regression tests for the Act Today wiring of Pre-Commitment Enforcement
(docs/plans/premortem-enforcement.md): `_act_today()`'s new "2.6" section,
`_consolidate_act_today()`'s deliberate exemption of `premortem_triggered`
from same-ticker merging (user-confirmed Q4 — show both cards, never merge),
`_KIND_RANK`'s ordering, and `decision_bucket`'s classification/dedup
interactions. These pin the coexistence fix caught while wiring the feature:
without the exemption, `_consolidate_act_today` would silently drop or merge
away a premortem card sharing a ticker with an existing deterioration card.
"""
from datetime import date

from stock_analyzer import decision_bucket as db
from stock_analyzer.daily_briefing import _act_today, _consolidate_act_today
from tests.conftest import find_item, make_port_df

TODAY = date(2024, 1, 15)


def _premortem_trigger(ticker="AAA", direction="below", trigger_price=90.0,
                        first_breach_date=date(2024, 1, 10), days_since=5,
                        current_price=85.0):
    return {
        "ticker": ticker, "direction": direction, "trigger_price": trigger_price,
        "first_breach_date": first_breach_date, "days_since": days_since,
        "current_price": current_price,
    }


# ── _act_today: section 2.6 ─────────────────────────────────────────────────

def test_act_today_no_premortem_triggers_produces_no_card():
    port_df = make_port_df([{"ticker": "AAA"}])
    items = _act_today(port_df, [], [], [], [], TODAY)
    assert find_item(items, "AAA") is None


def test_act_today_premortem_trigger_produces_card():
    port_df = make_port_df([{"ticker": "AAA", "weight": 7.5, "pnl_pct": -12.0}])
    items = _act_today(port_df, [], [], [], [], TODAY,
                        premortem_triggers=[_premortem_trigger(ticker="AAA")])
    item = find_item(items, "AAA")
    assert item is not None
    assert item["kind"] == "premortem_triggered"
    assert item["priority"] == "high"
    assert item["weight"] == 7.5
    assert item["pnl_pct"] == -12.0
    assert "90.00" in item["directive"]
    assert "5 day" in item["directive"]
    assert "below" in item["why"]


def test_act_today_premortem_trigger_above_direction_wording():
    port_df = make_port_df([{"ticker": "BBB"}])
    items = _act_today(port_df, [], [], [], [], TODAY,
                        premortem_triggers=[_premortem_trigger(
                            ticker="BBB", direction="above", trigger_price=50.0,
                        )])
    item = find_item(items, "BBB")
    assert item is not None
    assert "above" in item["directive"]


def test_act_today_premortem_coexists_with_deterioration_card_same_ticker():
    """The card raw-build stage: both kinds present for the same ticker
    before consolidation ever runs."""
    port_df = make_port_df([{"ticker": "AAA"}])
    deterioration = [{
        "ticker": "AAA", "tier": "TRIM", "shares": 10, "dd_from_peak_pct": 15.0,
        "peak": 100.0, "trend_ma": 50, "below_ma_count": 2, "rel_strength": -3.0,
        "trim_floor": 12, "exit_floor": 20, "weight_pct": 5.0, "pnl_pct": -10.0,
        "dollar_risk": None,
    }]
    items = _act_today(port_df, [], [], [], [], TODAY,
                        deterioration=deterioration,
                        premortem_triggers=[_premortem_trigger(ticker="AAA")])
    kinds = {i["kind"] for i in items if i.get("ticker") == "AAA"}
    assert kinds == {"deterioration_trim", "premortem_triggered"}


# ── _consolidate_act_today: the coexistence fix ─────────────────────────────

def test_consolidate_preserves_both_cards_on_same_ticker():
    """Without the passthrough exemption, this would collapse to ONE card
    per ticker and silently drop/merge away the premortem card."""
    port_df = make_port_df([{"ticker": "AAA"}])
    items = [
        {"priority": "high", "icon": "✂️", "ticker": "AAA", "kind": "deterioration_trim",
         "action": "TRIM", "directive": "trim", "why": "why", "trigger": "trigger"},
        {"priority": "high", "icon": "🎯", "ticker": "AAA", "kind": "premortem_triggered",
         "action": "COMMIT FIRED", "directive": "directive", "why": "why", "trigger": "trigger"},
    ]
    out = _consolidate_act_today(items, port_df)
    kinds = {i["kind"] for i in out}
    assert kinds == {"deterioration_trim", "premortem_triggered"}
    assert len(out) == 2


def test_consolidate_preserves_premortem_alongside_mechanical_stop_breach():
    """Even a mechanical stop_breach (which normally wins outright and drops
    every other same-ticker card) must not swallow the premortem card."""
    port_df = make_port_df([{"ticker": "AAA"}])
    items = [
        {"priority": "critical", "icon": "🛑", "ticker": "AAA", "kind": "stop_breach",
         "action": "SELL", "directive": "sell", "why": "why", "trigger": "trigger"},
        {"priority": "high", "icon": "🎯", "ticker": "AAA", "kind": "premortem_triggered",
         "action": "COMMIT FIRED", "directive": "directive", "why": "why", "trigger": "trigger"},
    ]
    out = _consolidate_act_today(items, port_df)
    kinds = {i["kind"] for i in out}
    assert kinds == {"stop_breach", "premortem_triggered"}
    assert len(out) == 2


def test_consolidate_multiple_premortem_tickers_all_survive():
    port_df = make_port_df([{"ticker": "AAA"}, {"ticker": "BBB"}])
    items = [
        {"priority": "high", "icon": "🎯", "ticker": "AAA", "kind": "premortem_triggered",
         "action": "a", "directive": "d", "why": "w", "trigger": "t"},
        {"priority": "high", "icon": "🎯", "ticker": "BBB", "kind": "premortem_triggered",
         "action": "a", "directive": "d", "why": "w", "trigger": "t"},
    ]
    out = _consolidate_act_today(items, port_df)
    assert {i["ticker"] for i in out} == {"AAA", "BBB"}


# ── decision_bucket classification ──────────────────────────────────────────

def test_classify_bucket_premortem_triggered_is_act():
    item = {"_source": "act", "kind": "premortem_triggered"}
    assert db.classify_bucket(item) == "act"


def test_is_reduce_premortem_triggered_is_false():
    """A premortem card must NOT be treated as a reduce — it should never
    suppress a same-ticker 'hold' critical-news card the way a real reduce
    does (deliberately excluded from _REDUCE_ACT_KINDS)."""
    item = {"_source": "act", "kind": "premortem_triggered"}
    assert db._is_reduce(item) is False


def test_reconcile_act_does_not_drop_critical_news_for_premortem_only_ticker():
    items = [
        {"_source": "act", "kind": "premortem_triggered", "ticker": "AAA", "why": "w"},
        {"_source": "act", "kind": "critical_news", "ticker": "AAA", "why": "w"},
    ]
    out = db._reconcile_act(items)
    kinds = {i["kind"] for i in out}
    assert "critical_news" in kinds  # not folded — premortem alone isn't a reduce
