"""C4 / C5 of docs/plans/macro-sector-gate.md (MacroAff, Macro latent gap)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import stock_analyzer.daily_briefing as _db
from stock_analyzer.daily_briefing import (
    _left_out_clause, _rewrite_macro_affected, build_daily_briefing,
)
from stock_analyzer.macro_calendar import HIGH as _MACRO_HIGH
from stock_analyzer.macro_release import refresh_macro_card
from tests.conftest import find_item, make_port_df

_TODAY = date(2026, 7, 27)
_ET = ZoneInfo("America/New_York")


def _review_macro(tickers, **extra):
    it = {"icon": "🌐", "ticker": None, "event": "NFP",
          "headline": "stale", "action": {"type": "WATCH"},
          "why": "w", "trigger": "t", "weight": None,
          "_affected_all": list(tickers),
          "_headline_head": "NFP in 3d (2026-07-30)"}
    it.update(extra)
    return it


def _build(names, affected, days=3, category="Economic", weight=5.0, **kw):
    port_df = make_port_df([{"ticker": t, "weight": weight, **kw.get(t, {})}
                            for t in names])
    ev = {"impact": _MACRO_HIGH, "date": _TODAY + timedelta(days=days),
          "category": "Economic", "event": "NFP",
          "affected_tickers": affected}
    return build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=[], news_items=[],
        macro_events=[ev], held_data={}, scanner_results=None,
        portfolio_value=100_000.0, today=_TODAY, market_context={"tone": "bull"},
    )


def _macro_review(brief):
    return next(i for i in brief["review_list"] if "_affected_all" in i)


# ── C4: review headline ────────────────────────────────────────────────────

def test_stop_breach_name_dropped_from_review_headline_with_clause():
    brief = _build(["AAPL", "BBB"], ["AAPL", "BBB"],
                   AAPL={"gap_to_stop": -1.0})
    assert find_item(brief["act_today"], "AAPL")["kind"] == "stop_breach"
    h = _macro_review(brief)["headline"]
    assert "AAPL" not in h
    assert "affected: BBB (1 affected name with its own Act Today call not listed)" in h


def test_risk_off_trim_name_dropped(monkeypatch):
    monkeypatch.setattr(_db, "deterioration_signals", lambda *a, **k: [])
    ro = {"kind": "risk_off_derisk", "ticker": "CCC", "priority": "high",
          "icon": "🛡️", "title": "CCC — Trim - Risk-Off", "why": "fragile",
          "directive": "Trim", "weight": 5.0}
    monkeypatch.setattr(_db.exit_advisor, "assess_risk_off_derisk",
                        lambda *a, **k: [dict(ro)])
    brief = _build(["BBB", "CCC"], ["BBB", "CCC"])
    h = _macro_review(brief)["headline"]
    assert "CCC" not in h and "BBB" in h and "not listed" in h


def test_own_trim_ticker_is_kept():
    it = _review_macro(["AAA", "BBB"], action={"type": "PROTECTIVE_TRIM",
                                               "trim_ticker": "AAA"})
    _rewrite_macro_affected([], {"AAA", "BBB"}, [it])
    assert "AAA" in it["headline"]
    assert "BBB" not in it["headline"].split("affected:")[1].split("(")[0]
    assert "(1 affected name with its own" in it["headline"]


def test_review_headline_plus_n_more_past_five():
    it = _review_macro(list("ABCDEFG"))
    _rewrite_macro_affected([], set(), [it])
    assert it["headline"] == "NFP in 3d (2026-07-30) · affected: A, B, C, D, E +2 more"


def test_review_headline_all_dropped_is_truthful():
    it = _review_macro(["AAA"])
    _rewrite_macro_affected([], {"AAA"}, [it])
    assert "names already carrying their own Act Today calls" in it["headline"]
    assert "AAA" not in it["headline"]


def test_left_out_clause_grammar():
    assert _left_out_clause(1) == "(1 affected name with its own Act Today call not listed)"
    assert _left_out_clause(2) == "(2 affected names with their own Act Today calls not listed)"


def test_act_card_gets_clause_too():
    act = [{"kind": "macro", "why": "x", "_affected_all": ["A", "B", "C"],
            "_event_category": "Economic"}]
    _rewrite_macro_affected(act, {"A", "B"})
    assert act[0]["why"] == ("Economic release today. Affected: C. "
                             "(2 affected names with their own Act Today calls not listed)")


# ── U6 wording ─────────────────────────────────────────────────────────────

_U6 = ("Hawkish surprise → your stops are the exit plan, though a gap can open "
       "below one; dovish → reconsider adds.")


def test_u6_wording_and_retired_phrases_gone(monkeypatch):
    monkeypatch.setattr("stock_analyzer.macro_calendar.affected_sectors",
                        lambda c: ["__ALL__"])
    brief = _build(["AAA", "BBB"], ["AAA", "BBB"], weight=50.0)
    rev = _macro_review(brief)
    assert rev["trigger"] == _U6
    assert "no pre-event trim is sized; your stops stay in place" in rev["why"]
    act_brief = _build(["AAA"], ["AAA"], days=0)
    macro = next(i for i in act_brief["act_today"] if i.get("kind") == "macro")
    assert macro["trigger"] == _U6
    blob = repr(brief) + repr(act_brief)
    assert "existing stops protect" not in blob
    assert "let your existing stops do the work" not in blob


# ── C5 ─────────────────────────────────────────────────────────────────────

def _card(**kw):
    c = {"kind": "macro", "directive": "Hold through the print.", "why": "W.",
         "reason": "Hold through the print. W.", "trigger": "x",
         "_event_date": date(2026, 7, 27), "_event_time_et": "08:30"}
    c.update(kw)
    return c


def test_refresh_rewrites_reason_after_release_only():
    c = _card()
    out = refresh_macro_card(c, datetime(2026, 7, 27, 12, 0, tzinfo=_ET))
    assert out["reason"] == f"{out['directive']} W."
    assert out["reason"].startswith("Released at 08:30 ET")
    assert c["reason"] == "Hold through the print. W."
    before = datetime(2026, 7, 27, 8, 0, tzinfo=_ET)
    assert refresh_macro_card(c, before) is c
