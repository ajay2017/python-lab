"""Chunk 2 of docs/plans/shared-entry-check.md: the shared entry-eligibility module."""
import ast
import pathlib
from datetime import date, datetime

import pytest

from stock_analyzer import entry_eligibility as ee
from stock_analyzer.constants import (
    COMPOSITE_BUY, EARNINGS_IMMINENT_DAYS, GROW_TODAY_MAX_FUND_AGE_DAYS, MACRO_IMMINENT_DAYS,
    MARKET_TONE_BEAR_PCT, MARKET_TONE_BULL_PCT, NEWS_SENTIMENT_NEGATIVE,
)
from stock_analyzer.macro_calendar import HIGH
from stock_analyzer.signal_reconciliation import reconcile_signals

ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 14)


# -- classify_tone ------------------------------------------------------------
@pytest.mark.parametrize("pct,want", [
    (MARKET_TONE_BEAR_PCT, "bear"),
    (MARKET_TONE_BEAR_PCT + 0.01, "flat"),
    (MARKET_TONE_BULL_PCT, "bull"),
    (MARKET_TONE_BULL_PCT - 0.01, "flat"),
])
def test_classify_tone_boundaries(pct, want):
    assert ee.classify_tone(pct) == want


# -- resolve_tone_state -------------------------------------------------------
def test_resolver_order():
    home = {"tone": "bear", "sp500_pct": -0.8, "tone_available": True}
    r = ee.resolve_tone_state(home, True, "09:42", 0.9, True)
    assert (r["source"], r["tone"], r["available"], r["as_of"]) == ("home", "bear", True, "09:42")
    r = ee.resolve_tone_state(home, False, "09:42", 0.9, True)
    assert (r["source"], r["tone"]) == ("live", "bull")
    r = ee.resolve_tone_state({**home, "tone_available": False}, True, "x", -0.8, True)
    assert (r["source"], r["tone"]) == ("live", "bear")
    r = ee.resolve_tone_state(None, False, None, None, False)
    assert r == {"tone": None, "sp500_pct": None, "available": False, "source": None, "as_of": None}
    r = ee.resolve_tone_state(home, False, None, 0.0, False)
    assert r["available"] is False and r["source"] is None


# -- macro --------------------------------------------------------------------
def _ev(days, category="Inflation", **kw):
    d = date.fromordinal(TODAY.toordinal() + days).isoformat()
    return {"impact": HIGH, "date": d, "category": category, "event": "CPI", **kw}


def _sector_of(category):
    from stock_analyzer.macro_calendar import affected_sectors
    s = sorted(affected_sectors(category) - {"__ALL__"})
    assert s
    return s[0]


NOW = datetime(2026, 10, 14, 12, 0)


def test_macro_window_boundary():
    sec = _sector_of("Inflation")
    on = ee.macro_block_map([_ev(MACRO_IMMINENT_DAYS)], TODAY, NOW)
    off = ee.macro_block_map([_ev(MACRO_IMMINENT_DAYS + 1)], TODAY, NOW)
    assert ee.macro_hold(sec, on)
    assert ee.macro_hold(sec, off) is None


def test_macro_none_vs_empty_distinguishable():
    assert ee.macro_block_map(None, TODAY, NOW) is None
    m = ee.macro_block_map([], TODAY, NOW)
    assert m is not None and m["sectors"] == set()
    assert ee.macro_hold("Tech", None) is None and ee.macro_hold("Tech", m) is None


def test_macro_same_day_release_and_fomc():
    sec = _sector_of("Inflation")
    before = datetime(2026, 10, 14, 7, 0)
    after = datetime(2026, 10, 14, 9, 0)
    ev = _ev(0, time_et="08:30")
    assert ee.macro_hold(sec, ee.macro_block_map([ev], TODAY, before))
    assert ee.macro_hold(sec, ee.macro_block_map([ev], TODAY, after)) is None
    assert ee.macro_hold(sec, ee.macro_block_map([_ev(0, released=True)], TODAY, before)) is None
    # clock unavailable keeps the gate on
    assert ee.macro_hold(sec, ee.macro_block_map([ev], TODAY, None))


# -- data trust ---------------------------------------------------------------
def test_data_trust_boundaries():
    assert ee.data_trust_hold({"fund_cache_age_days": GROW_TODAY_MAX_FUND_AGE_DAYS}) is None
    h = ee.data_trust_hold({"fund_cache_age_days": GROW_TODAY_MAX_FUND_AGE_DAYS + 1})
    assert h and h["check"] == "stale" and h["gate_id"] is None
    assert ee.data_trust_hold({"stale_as_of": "2026-10-10"})
    assert ee.data_trust_hold({}) is None
    assert ee.data_trust_hold({"stale_as_of": None, "fund_cache_age_days": None}) is None


# -- news ---------------------------------------------------------------------
@pytest.mark.parametrize("c", [
    NEWS_SENTIMENT_NEGATIVE, NEWS_SENTIMENT_NEGATIVE - 0.001, NEWS_SENTIMENT_NEGATIVE + 0.001,
    -0.4, 0.0, 0.5, None,
])
@pytest.mark.parametrize("mom", [0, COMPOSITE_BUY - 1, 90])
def test_is_negative_news_matches_reconcile_signals(c, mom):
    r = reconcile_signals("X", mom, "Strong Buy", composite_score=85,
                          composite_signal="Strong Buy", news_sentiment=c)
    skipped = r["verdict"] == "skip" and "Negative News" in r["label"]
    assert ee.is_negative_news(c) == skipped


def test_reconcile_signals_uses_the_one_shared_predicate():
    from stock_analyzer import signal_reconciliation
    assert signal_reconciliation.is_negative_news is ee.is_negative_news


def test_news_compound():
    assert ee.news_compound([]) is None and ee.news_compound(None) is None
    assert ee.news_compound([{"compound": -0.2}, {"compound": -0.4}]) == pytest.approx(-0.3)


# -- entry_check --------------------------------------------------------------
BEAR = {"tone": "bear", "sp500_pct": -0.8, "available": True, "source": "live", "as_of": None}
FLAT = {"tone": "flat", "sp500_pct": 0.1, "available": True, "source": "live", "as_of": None}
NEWS_OK = {"compound": 0.2, "n": 3, "headline": "h"}


def _clean(**kw):
    base = dict(bundle={}, tone_state=FLAT, sector="Tech",
                macro_map=ee.macro_block_map([], TODAY, NOW), news=NEWS_OK, earnings_days=None)
    base.update(kw)
    return ee.entry_check(**base)


def test_clean_is_eligible():
    r = _clean()
    assert r["eligible"] and r["holds"] == [] and r["binding"] is None and r["unchecked"] == []


def test_precedence_all_holds_listed_binding_first():
    sec = _sector_of("Inflation")
    r = _clean(
        bundle={"stale_as_of": "2026-10-10"}, tone_state=BEAR, sector=sec,
        macro_map=ee.macro_block_map([_ev(1)], TODAY, NOW),
        news={"compound": -0.4, "n": 2, "headline": "h"}, earnings_days=1,
        gate_ids={"tone": "G-29", "macro": "G-28"},
    )
    assert [h["check"] for h in r["holds"]] == ["stale", "tone", "macro", "news", "earnings"]
    assert r["binding"] is r["holds"][0] and not r["eligible"]
    ids = {h["check"]: h["gate_id"] for h in r["holds"]}
    assert ids == {"stale": None, "tone": "G-29", "macro": "G-28", "news": None, "earnings": None}
    for h in r["holds"]:
        assert set(h) >= {"check", "gate_id", "reason", "clears_when", "gate_value", "gate_threshold"}


def test_gate_ids_default_none():
    r = _clean(tone_state=BEAR)
    assert r["holds"][0]["gate_id"] is None


def test_unchecked_entries_fail_open():
    r = _clean(tone_state={"available": False}, macro_map=None, news=None)
    assert r["eligible"] and r["holds"] == []
    assert {u["check"] for u in r["unchecked"]} == {"tone", "macro", "news"}
    assert any("no recent headlines" in u["why"] for u in r["unchecked"])
    assert _clean(tone_state=None)["unchecked"][0]["check"] == "tone"


def test_earnings_boundary_and_unknown():
    assert _clean(earnings_days=EARNINGS_IMMINENT_DAYS)["holds"][0]["check"] == "earnings"
    assert _clean(earnings_days=EARNINGS_IMMINENT_DAYS + 1)["eligible"]
    assert _clean(earnings_days=-1)["eligible"]
    assert _clean(earnings_days=None)["eligible"]


def test_news_boundary():
    assert not _clean(news={"compound": NEWS_SENTIMENT_NEGATIVE, "n": 1, "headline": ""})["eligible"]
    assert _clean(news={"compound": NEWS_SENTIMENT_NEGATIVE + 0.01, "n": 1, "headline": ""})["eligible"]


def test_extra_check_is_applied_A4():
    def check_margin(ctx):
        return {"kind": "hold", "check": "margin", "gate_id": None, "reason": "r",
                "clears_when": "c", "gate_value": 1, "gate_threshold": 2}
    r = ee.entry_check(bundle={}, tone_state=FLAT, macro_map=None, news=NEWS_OK,
                       checks=ee.DEFAULT_CHECKS + (check_margin,))
    assert r["holds"][-1]["check"] == "margin" and not r["eligible"]

    def check_u(ctx):
        return {"kind": "unchecked", "check": "z", "why": "w"}
    r = ee.entry_check(checks=(check_u,))
    assert r["unchecked"] == [{"check": "z", "why": "w"}] and r["eligible"]


# -- the tone rule has one copy -----------------------------------------------
@pytest.mark.parametrize("rel", ["app.py", "stock_analyzer/headless_alert_engine.py"])
def test_no_local_tone_rule(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("MARKET_TONE_BEAR_PCT", "MARKET_TONE_BULL_PCT"):
            pytest.fail(f"{rel} references {node.id} outside entry_eligibility")
    assert "classify_tone" in src
    assert '"tone_available"' in src


# -- chunk 3 follow-ups ----------------------------------------------------------
def test_stale_check_without_a_bundle_is_unchecked_not_a_silent_pass():
    r = ee.entry_check(bundle=None)
    assert r["eligible"]
    assert {"check": "stale", "why": "no data bundle — the stale-data check couldn't run"} in r["unchecked"]
    # a real (empty) bundle is a checked pass
    assert not [u for u in ee.entry_check(bundle={})["unchecked"] if u["check"] == "stale"]


def test_resolver_requires_tone_available_true():
    base = {"tone": "bear", "sp500_pct": -0.8}
    # flag missing -> falls through to the live read
    r = ee.resolve_tone_state(base, True, "x", 0.9, True)
    assert (r["source"], r["tone"]) == ("live", "bull")
    # missing flag and no live read -> unavailable, never the Home value
    r = ee.resolve_tone_state(base, True, "x", None, False)
    assert r["available"] is False and r["tone"] is None
    # truthy-but-not-True is also rejected
    r = ee.resolve_tone_state({**base, "tone_available": 1}, True, "x", None, False)
    assert r["available"] is False
    r = ee.resolve_tone_state({**base, "tone_available": True}, True, "x", None, False)
    assert r["source"] == "home"


def test_compute_daily_brief_fallback_ctx_marks_tone_unavailable():
    tree = ast.parse((ROOT / "stock_analyzer" / "daily_briefing.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.BoolOp)
                and isinstance(node.value.op, ast.Or)
                and any(isinstance(t, ast.Name) and t.id == "ctx" for t in node.targets)
                and isinstance(node.value.values[0], ast.Name)
                and node.value.values[0].id == "market_context"):
            d = node.value.values[-1]
            assert isinstance(d, ast.Dict)
            found.append({k.value: v for k, v in zip(d.keys, d.values)
                          if isinstance(k, ast.Constant)})
    assert len(found) == 1
    assert isinstance(found[0]["tone_available"], ast.Constant) and found[0]["tone_available"].value is False


def test_macro_hold_decided_on_membership_not_reason_truthiness():
    for reasons in ({}, {"Technology": ""}, {"Technology": None}):
        bm = {"sectors": {"Technology"}, "reasons": reasons}
        r = ee.entry_check(sector="Technology", macro_map=bm, bundle={})
        assert not r["eligible"] and r["binding"]["check"] == "macro"
        assert "imminent HIGH-impact macro event" in r["binding"]["reason"]
    allm = {"sectors": {"__ALL__"}, "reasons": {}}
    assert not ee.entry_check(sector="Whatever", macro_map=allm, bundle={})["eligible"]
    # not in the set -> no hold even though a reason exists for another sector
    assert ee.entry_check(sector="Energy", bundle={},
                          macro_map={"sectors": {"Technology"}, "reasons": {"Energy": "x"}})["eligible"]


def test_entry_eligibility_is_pure_and_margin_isolated():
    tree = ast.parse((ROOT / "stock_analyzer" / "entry_eligibility.py").read_text(encoding="utf-8"))
    banned = ("streamlit", "stock_analyzer.db", "app", "stock_analyzer.margin")
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = [mod] + [f"{mod}.{a.name}" for a in node.names]
        for n in names:
            for b in banned:
                assert n != b and not n.startswith(b + "."), f"imports {n}"
        if isinstance(node, ast.Call):
            f = node.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            assert nm not in ("now", "today", "utcnow"), f"clock read {nm}()"
