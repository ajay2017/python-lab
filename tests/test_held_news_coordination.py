"""Held-news coverage chunk 4 (docs/plans/held-news-coverage.md D-D / D-F / D-I):
news-only cards stop swallowing risk-off TRIMs and Tune-up beta cards; Home
publishes the Brief's own news flags for the Rebalancer."""
import ast
from tests._ast_cache import parse_file_cached
import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import coord_freshness, decision_bucket, held_news_check as hnc
from stock_analyzer import daily_briefing as db
from stock_analyzer.constants import NEWS_CRITICAL_MAX_TIER, NEWS_SENTIMENT_CRITICAL

pytestmark = pytest.mark.fast

_APP = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


# -- all_flagged_tickers(ignore_news=) ---------------------------------------
def _cards():
    act = [
        {"ticker": "CN", "kind": "critical_news"},
        {"ticker": "SELL", "kind": "sell_signal"},
        {"ticker": None, "kind": "macro", "action": {"trim_ticker": "MAC"}},
    ]
    review = [
        {"ticker": "NW", "watch_kind": "news", "action": {"type": "WATCH"}},
        {"ticker": "EW", "action": {"type": "WATCH"}},          # earnings-style WATCH
        {"ticker": "TR", "action": {"type": "TRIM_TO_TARGET"}},
    ]
    return act, review


def test_ignore_news_skips_only_news_only_cards():
    act, review = _cards()
    assert decision_bucket.all_flagged_tickers(act, review, ignore_news=True) == {
        "SELL", "MAC", "EW", "TR"}


def test_default_unchanged_counts_news():
    act, review = _cards()
    assert decision_bucket.all_flagged_tickers(act, review) == {
        "CN", "SELL", "MAC", "NW", "EW", "TR"}


def test_ignore_news_none_safe():
    assert decision_bucket.all_flagged_tickers(None, None, ignore_news=True) == set()


# -- build_daily_briefing: D-D / D-I -----------------------------------------
def _port_df():
    rows = [("AAA", 20.0), ("BBB", 15.0), ("CCC", 10.0), ("DDD", 10.0)]
    return pd.DataFrame([
        {"Ticker": t, "Weight (%)": w, "Price": 100.0, "Shares": 10.0, "P&L (%)": 5.0}
        for t, w in rows])


_HELD = {t: {"risk_metrics": {"beta": b}} for t, b in
         [("AAA", 2.0), ("BBB", 1.5), ("CCC", 1.4), ("DDD", 1.3)]}


def _build(monkeypatch, act, review, risk_recs=None):
    monkeypatch.setattr(db, "_act_today", lambda *a, **k: [dict(x) for x in act])
    monkeypatch.setattr(db, "_review_list", lambda *a, **k: [dict(x) for x in review])
    monkeypatch.setattr(db, "_grow_today", lambda *a, **k: {})
    monkeypatch.setattr(db, "deterioration_signals", lambda *a, **k: {})
    return db.build_daily_briefing(
        _port_df(), [], risk_recs or [], [], [], _HELD, None, 10000.0,
        date(2026, 10, 16),
        fragility={"severity": "fragile"},
        spy_trend_df=pd.DataFrame({"Close": [200.0 - i * 0.5 for i in range(250)]}),
        vix_level=14.0,
    )


def _news_watch(t="AAA"):
    return {"ticker": t, "watch_kind": "news", "action": {"type": "WATCH"},
            "headline": "negative headline", "news_headline": "AAA recalls product",
            "news_compound": -0.2}


def _ro_tickers(brief):
    return [i["ticker"] for i in brief["act_today"] if i.get("kind") == "risk_off_derisk"]


def test_news_watch_on_top_contributor_does_not_move_risk_off(monkeypatch):
    brief = _build(monkeypatch, [], [_news_watch("AAA")])
    assert _ro_tickers(brief) == ["AAA", "BBB", "CCC"]


def test_news_watch_dropped_and_headline_on_risk_off_card(monkeypatch):
    brief = _build(monkeypatch, [], [_news_watch("AAA")])
    assert not [r for r in brief["review_list"] if r.get("watch_kind") == "news"]
    card = next(i for i in brief["act_today"] if i.get("ticker") == "AAA")
    assert "recent negative headlines" in card["why"]
    assert "AAA recalls product" in card["why"]


def test_news_watch_on_non_risk_off_name_is_kept(monkeypatch):
    brief = _build(monkeypatch, [], [_news_watch("ZZZ")])
    assert [r["ticker"] for r in brief["review_list"]] == ["ZZZ"]


def test_critical_news_act_card_on_non_risk_off_name_is_kept(monkeypatch):
    # Act-side twin of the news-WATCH test (chunk-4 review): only a risk-off
    # ticker's critical_news card is folded away; ZZZ is outside the pool.
    crit = {"ticker": "ZZZ", "kind": "critical_news", "action": "Watch - Critical News",
            "why": "2 headlines", "news_headline": "x", "news_compound": -0.6}
    brief = _build(monkeypatch, [crit], [])
    assert "ZZZ" not in _ro_tickers(brief)
    zzz = [i for i in brief["act_today"] if i.get("ticker") == "ZZZ"]
    assert [i.get("kind") for i in zzz] == ["critical_news"]


def test_earnings_watch_on_top_contributor_still_excludes(monkeypatch):
    earn = {"ticker": "AAA", "action": {"type": "WATCH"}, "headline": "earnings soon"}
    brief = _build(monkeypatch, [], [earn])
    assert _ro_tickers(brief) == ["BBB", "CCC", "DDD"]
    assert [r["ticker"] for r in brief["review_list"]] == ["AAA"]


def test_critical_news_act_card_folds_into_risk_off_reduce(monkeypatch):
    crit = {"ticker": "AAA", "kind": "critical_news", "action": "Watch - Critical News",
            "why": "2 headlines", "news_headline": "x", "news_compound": -0.6}
    brief = _build(monkeypatch, [crit], [])
    assert _ro_tickers(brief)[0] == "AAA"
    split = decision_bucket.split_defensive(brief["act_today"], brief["review_list"])
    aaa = [i for i in split["act"] + split["aware"] if i.get("ticker") == "AAA"]
    assert [i["kind"] for i in aaa] == ["risk_off_derisk"]
    assert "negative headline" in aaa[0]["why"]


def _beta_rec(ticker):
    return {"type": "beta", "priority": "HIGH", "title": "Beta", "problem": "p",
            "recommendation": "r", "root_tickers": [{"ticker": ticker}]}


def test_tuneup_beta_card_kept_with_only_news_watch(monkeypatch):
    # EEE is outside the risk-off pool, so only its news WATCH could drop the card.
    brief = _build(monkeypatch, [], [_news_watch("EEE")], risk_recs=[_beta_rec("EEE")])
    assert [t["tickers"] for t in brief["portfolio_tuneup"]] == [["EEE"]]


def test_tuneup_beta_card_dropped_with_real_trim(monkeypatch):
    trim = {"ticker": "EEE", "action": {"type": "TRIM_TO_TARGET"}, "headline": "h"}
    brief = _build(monkeypatch, [], [trim], risk_recs=[_beta_rec("EEE")])
    assert brief["portfolio_tuneup"] == []


# -- brief_news_flags ---------------------------------------------------------
def test_brief_news_flags_levels_and_fields():
    act = [{"ticker": "cn", "kind": "critical_news", "news_headline": "h1",
            "news_compound": -0.7}, {"ticker": "S", "kind": "sell_signal"}]
    review = [_news_watch("NW"), {"ticker": "EW", "action": {"type": "WATCH"}}]
    out = hnc.brief_news_flags(act, review)
    assert out == {
        "CN": {"level": "critical", "headline": "h1", "compound": -0.7},
        "NW": {"level": "warning", "headline": "AAA recalls product", "compound": -0.2},
    }


def test_brief_news_flags_critical_wins_and_missing_fields():
    act = [{"ticker": "X", "kind": "critical_news"}]
    review = [{"ticker": "X", "watch_kind": "news"}]
    assert hnc.brief_news_flags(act, review) == {
        "X": {"level": "critical", "headline": "", "compound": 0.0}}


def test_brief_news_flags_none_lists():
    assert hnc.brief_news_flags(None, None) == {}


def test_briefing_cards_carry_structured_news_fields():
    src = Path(db.__file__).read_text(encoding="utf-8")
    assert src.count('"news_headline"') >= 2 and src.count('"news_compound"') >= 2


# -- _tier NaN ------------------------------------------------------------------
def test_tier_nan_is_three():
    assert hnc._tier({"tier": float("nan")}) == 3


def test_nan_tier_not_critical_or_act_qualifying():
    item = {"ticker": "A", "ts": 2_000_000_000.0, "compound": NEWS_SENTIMENT_CRITICAL - 0.1,
            "tier": float("nan")}
    assert NEWS_CRITICAL_MAX_TIER < 3
    assert hnc.critical_news_by_ticker([item, dict(item)], {"A"}, 0.0) == {}
    w = hnc.warning_news_by_ticker([item], {"A"}, 0.0, exclude=set())
    assert w["A"]["n_act_qualifying"] == 0


# -- coord_freshness registration ----------------------------------------------
def test_registered_gate_tier_home_producer_pa_surface():
    e = coord_freshness.PORTFOLIO_DEPENDENT_KEYS["_brief_news_flags_cache"]
    assert e["producer"] == "home" and e["tier"] == coord_freshness.TIER_GATE
    assert "_brief_news_flags_cache" in coord_freshness.SURFACE_KEYS["pa"]


# -- app.py wiring (source text) -------------------------------------------------
def test_cache_published_via_helper_none_when_offline():
    none_m = re.search(r'_brief_news_flags_cache"\]\s*=\s*None', _APP)
    assert none_m
    off = _APP.index('st.session_state["_reduce_calls"] = {}')
    _else = 'else:' + chr(10) + '            st.session_state["_daily_brief_offline"] = False'
    assert off < none_m.start() < _APP.index(_else)
    # one helper publishes both caches from a built brief
    h = _APP.index("def _publish_brief_coord_caches(brief):")
    seg = _APP[h:h + 700]
    assert "reduce_call_items(" in seg and "published_news_flags(" in seg
    assert "_today_et().isoformat()" in seg


def test_miss_path_and_hit_rebuild_both_use_helper():
    assert _APP.count("_publish_brief_coord_caches(_daily_brief)") == 2
    hit = _APP.index("Composite freshness rebuild failed")
    rebuilt = _APP.rindex("_publish_brief_coord_caches(_daily_brief)", 0, hit)
    assert _APP.rindex("_daily_brief = build_daily_briefing(", 0, rebuilt) < rebuilt


def test_memo_hit_does_not_stamp_news_flags():
    assert re.search(r'k not in \("_reduce_calls", "_brief_news_flags_cache"\)', _APP)


def test_rebalancer_uses_dated_flags_and_fallback_caption():
    i = _APP.index('_rb_brief_news = usable_news_flags(')
    seg = _APP[i:i + 1600]
    assert "_today_et().isoformat()" in seg
    assert "if _rb_brief_news is not None:" in seg
    assert "News cross-check here uses the 20 most recent headlines across" in seg
    assert "open 🏠 Home today to check every holding." in seg


def test_card_renderers_escape_via_md_bold():
    a = _APP.index("def _render_act_card(")
    act = _APP[a:a + 4500]
    for v in ("_db_directive", "_db_why", "_db_trigger"):
        assert "{_md_bold(%s)}" % v in act
    r = _APP.index("def _render_review_card(")
    rev = _APP[r:r + 6000]
    for v in ("_headline", "_why", "_trigger"):
        assert "{_md_bold(%s)}" % v in rev


def test_rebalancer_labels_do_not_name_news_intelligence_as_source():
    assert "flagged by News Intelligence" not in _APP
    assert "News Intelligence Conflict" not in _APP


# -- published flags payload / usable gate ---------------------------------------
def test_published_news_flags_prefers_brief_news_flags():
    b = {"news_flags": {"A": {"level": "warning", "headline": "h", "compound": -0.5}},
         "act_today": [{"ticker": "X", "kind": "critical_news"}], "review_list": []}
    assert hnc.published_news_flags(b, "2026-10-17") == {
        "date": "2026-10-17", "flags": b["news_flags"]}


def test_published_news_flags_falls_back_to_cards_without_key():
    b = {"act_today": [{"ticker": "X", "kind": "critical_news"}], "review_list": []}
    assert list(hnc.published_news_flags(b, "d")["flags"]) == ["X"]


def test_usable_news_flags_date_and_shape():
    c = {"date": "2026-10-17", "flags": {"A": {}}}
    assert hnc.usable_news_flags(c, "2026-10-17") == {"A": {}}
    assert hnc.usable_news_flags(c, "2026-10-18") is None
    assert hnc.usable_news_flags(None, "2026-10-17") is None
    assert hnc.usable_news_flags({"date": "2026-10-17", "flags": None}, "2026-10-17") is None
    assert hnc.usable_news_flags({"date": "2026-10-17", "flags": {}}, "2026-10-17") == {}


# -- brief["news_flags"] comes from the detectors, not the cards (blocking fix) --
def _headline(t, c, tier=1):
    return {"ticker": t, "title": f"{t} headline", "compound": c, "tier": tier,
            "ts": 4_000_000_000.0}


def _build_with_news(monkeypatch, act, review, news):
    monkeypatch.setattr(db, "_act_today", lambda *a, **k: [dict(x) for x in act])
    monkeypatch.setattr(db, "_review_list", lambda *a, **k: [dict(x) for x in review])
    monkeypatch.setattr(db, "_grow_today", lambda *a, **k: {})
    monkeypatch.setattr(db, "deterioration_signals", lambda *a, **k: {})
    monkeypatch.setattr(hnc, "news_window_start",
                        lambda today, *a, **k: __import__("datetime").datetime(
                            2026, 10, 15, tzinfo=__import__("datetime").timezone.utc))
    return db.build_daily_briefing(
        _port_df(), [], [], news, [], _HELD, None, 10000.0, date(2026, 10, 16))


def test_news_flags_cover_names_whose_news_card_was_deduped(monkeypatch):
    det = {"ticker": "BBB", "action": {"type": "WATCH"}, "headline": "deterioration"}
    tight = {"ticker": "CCC", "action": {"type": "TIGHTEN_ONLY"}, "headline": "tighten"}
    pre = {"ticker": "DDD", "kind": "premortem_triggered", "action": "x"}
    news = [_headline("BBB", -0.5), _headline("CCC", -0.5),
            _headline("DDD", -0.6), _headline("DDD", -0.6)]
    brief = _build_with_news(monkeypatch, [pre], [det, tight], news)
    nf = brief["news_flags"]
    assert nf["BBB"]["level"] == "warning" and nf["CCC"]["level"] == "warning"
    assert nf["DDD"]["level"] == "critical"
    assert nf["BBB"]["headline"] == "BBB headline" and nf["BBB"]["compound"] == -0.5
    # and the card-derived flags (the old way) would have missed BBB/CCC
    old = hnc.brief_news_flags(brief["act_today"], brief["review_list"])
    assert "BBB" not in old and "CCC" not in old


def test_news_flags_empty_when_no_negative_headlines(monkeypatch):
    brief = _build_with_news(monkeypatch, [], [], [_headline("BBB", 0.4)])
    assert brief["news_flags"] == {}


def test_news_flags_from_detectors_critical_wins():
    crit = {"A": [{"title": "t" * 200, "compound": -0.7}]}
    warn = {"A": {"item": {"title": "w", "compound": -0.2}},
            "B": {"item": {"headline": "hb", "compound": -0.1}}}
    out = hnc.news_flags_from_detectors(crit, warn)
    assert out["A"]["level"] == "critical" and len(out["A"]["headline"]) == 140
    assert out["B"] == {"level": "warning", "headline": "hb", "compound": -0.1}


def test_act_critical_news_card_removed_for_risk_off_ticker(monkeypatch):
    crit = {"ticker": "AAA", "kind": "critical_news", "action": "Watch",
            "why": "2 headlines", "news_headline": "AAA bad", "news_compound": -0.6}
    brief = _build(monkeypatch, [crit], [])
    aaa = [i for i in brief["act_today"] if i.get("ticker") == "AAA"]
    assert [i["kind"] for i in aaa] == ["risk_off_derisk"]
    assert "AAA bad" in aaa[0]["why"]


def test_news_intelligence_caption_present():
    assert "Shows the 20 most recent headlines across your holdings" in _APP
    assert "Act Today and Review check every holding." in _APP
    parse_file_cached(Path(__file__).resolve().parent.parent / "app.py")
