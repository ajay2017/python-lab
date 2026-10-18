"""Chunk 7a of docs/plans/shared-entry-check.md (owner decisions N-A Option 2 /
N-C, 2026-10-18): an add-to-winner on a held name is paused by

  CARD  -- an Act Today card (incl. critical news), a Review card EXCEPT a news
           WATCH, or a sale today (`decision_bucket.add_blocking_cards`), or
  ENTRY -- the shared entry check with exactly stale + news + earnings,
           checked LAST (after G-20 ... G-25).

Every pairing below is a boundary or a precedence the build must not blur.
"""
import ast
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from stock_analyzer import daily_briefing, decision_bucket
from stock_analyzer.constants import (
    ADD_WINNER_MIN_GAP_PCT,
    COMPOSITE_BUY,
    EARNINGS_IMMINENT_DAYS,
    GROW_TODAY_MAX_FUND_AGE_DAYS,
    NEWS_SENTIMENT_NEGATIVE,
)
from stock_analyzer.daily_briefing import _grow_today
from tests.conftest import make_port_df

pytestmark = pytest.mark.fast

_TODAY = date(2026, 10, 15)
_T = "ZT"


def _iso(d_off):
    return (_TODAY + timedelta(days=d_off)).isoformat()


def _news(compound, n=1, ticker=_T):
    return [{"ticker": ticker, "title": f"{ticker} headline {i}", "compound": compound,
             "label": "x"} for i in range(n)]


class _FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        naive = datetime(2026, 10, 15, 7, 0)
        return tz.localize(naive) if tz is not None else naive


def _run(*, tone="bull", news=None, earnings=None, held_data=None, act=None, review=None,
         sold=None, deterioration=None, tickers=(_T,)):
    pct = {"bull": 0.8, "flat": 0.0, "bear": -0.8}[tone]
    rows = [{"ticker": t, "weight": 5.0, "sector": f"Sec{i}", "score": COMPOSITE_BUY + 15,
             "signal": "Strong Buy", "gap_to_stop": ADD_WINNER_MIN_GAP_PCT + 2,
             "price": 100.0} for i, t in enumerate(tickers)]
    ctx = {"tone": tone, "sp500_pct": pct, "nasdaq_pct": pct * 1.2, "leading_sectors": []}
    with mock.patch.object(daily_briefing, "_dt", _FixedDT):
        return _grow_today(
            make_port_df(rows), None, news or [], held_data or {}, _TODAY, 100_000.0, ctx,
            act_today=act, review_list=review, composites={}, earnings_lookup=earnings,
            deterioration=deterioration, sold_today=sold)


def _t(items):
    return [i["ticker"] for i in items]


def _only(r):
    """Where the ticker landed: exactly one of added / card / entry / (other)."""
    return {"added": _T in _t(r["add_positions"]),
            "card": _T in _t(r["card_blocked_adds"]),
            "entry": _T in _t(r["entry_held_adds"])}


# ── news bar ────────────────────────────────────────────────────────────────

def test_news_exactly_at_bar_is_held_just_above_is_added():
    at = _run(news=_news(NEWS_SENTIMENT_NEGATIVE))
    assert _only(at) == {"added": False, "card": False, "entry": True}
    h = at["entry_held_adds"][0]
    assert h["check"] == "news" and h["gate_id"] == "G-30" and h["counterfactual"] is True
    assert h["gate_threshold"] == NEWS_SENTIMENT_NEGATIVE and h["clears_when"]
    above = _run(news=_news(NEWS_SENTIMENT_NEGATIVE + 0.001))
    assert _only(above) == {"added": True, "card": False, "entry": False}
    assert above["add_positions"][0]["entry_passed"][0]["check"] == "news"


def test_no_headlines_is_added_with_an_unchecked_entry():
    r = _run(news=[])
    assert _only(r)["added"] is True
    a = r["add_positions"][0]
    assert any(u["check"] == "news" for u in a["entry_unchecked"])
    assert a["review_news_note"] is None


def test_bundle_absent_is_added_with_an_unchecked_note():
    r = _run(held_data={})
    a = r["add_positions"][0]
    assert any(u["check"] == "stale" for u in a["entry_unchecked"])


# ── stale data ──────────────────────────────────────────────────────────────

def test_stale_as_of_holds():
    r = _run(held_data={_T: {"stale_as_of": "2026-10-10"}})
    assert _only(r)["entry"] is True
    assert r["entry_held_adds"][0]["check"] == "stale"


def test_fund_age_at_max_is_added_plus_one_is_held():
    ok = _run(held_data={_T: {"fund_cache_age_days": GROW_TODAY_MAX_FUND_AGE_DAYS}})
    assert _only(ok)["added"] is True
    bad = _run(held_data={_T: {"fund_cache_age_days": GROW_TODAY_MAX_FUND_AGE_DAYS + 1}})
    assert _only(bad)["entry"] is True
    assert bad["entry_held_adds"][0]["check"] == "stale"


# ── earnings ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("off,held", [(0, True), (EARNINGS_IMMINENT_DAYS, True),
                                      (EARNINGS_IMMINENT_DAYS + 1, False), (None, False)])
def test_earnings_window(off, held):
    r = _run(earnings=None if off is None else {_T: _iso(off)})
    assert _only(r)["entry"] is held and _only(r)["added"] is (not held)
    if held:
        assert r["entry_held_adds"][0]["check"] == "earnings"


def test_earnings_read_from_held_data_when_lookup_absent():
    r = _run(held_data={_T: {"earnings": _iso(1)}})
    assert _only(r)["entry"] is True


# ── cards ───────────────────────────────────────────────────────────────────

_NEWS_WATCH = {"ticker": _T, "watch_kind": "news", "action": {"type": "WATCH"},
               "headline": 'negative headline since Mon (worst -0.10): "x"',
               "news_headline": "ZT headline 0", "news_compound": -0.10,
               "news_n_negative": 2, "news_window_label": "Mon"}


def test_review_news_watch_alone_does_not_pause_the_add_and_carries_a_note():
    r = _run(news=_news(-0.10), review=[dict(_NEWS_WATCH)])
    assert _only(r) == {"added": True, "card": False, "entry": False}
    note = r["add_positions"][0]["review_news_note"]
    assert note["worst_compound"] == -0.10 and note["news_avg"] == pytest.approx(-0.10)
    assert note["news_headline"] == "ZT headline 0" and note["news_n"] == 1
    # structured fields, so the render never parses prose
    assert note["n_negative"] == 2 and note["window_label"] == "Mon"
    assert note["headline"] == _NEWS_WATCH["headline"]


def test_act_critical_news_card_pauses_the_add():
    r = _run(news=_news(-0.10), review=[dict(_NEWS_WATCH)],
             act=[{"ticker": _T, "kind": "critical_news", "action": "MONITOR — Critical News"}])
    assert _only(r) == {"added": False, "card": True, "entry": False}
    c = r["card_blocked_adds"][0]
    assert c["where"] == "Act Today" and c["card_label"] == "Act Today · critical news"


def test_review_earnings_card_pauses_as_a_card_with_its_label():
    r = _run(review=[{"ticker": _T, "action": {"type": "WATCH"},
                      "headline": "earnings in 2d (2026-10-17), weight 5.0%"}])
    c = r["card_blocked_adds"][0]
    assert c["where"] == "Monitoring" and c["card_label"] == "Monitoring · earnings in 2d"


def test_sold_today_is_a_visible_card_block():
    r = _run(sold={_T})
    c = r["card_blocked_adds"][0]
    assert c["where"] == "sold" and c["card_label"] == "sold today"
    assert _only(r) == {"added": False, "card": True, "entry": False}


def test_any_act_card_means_never_added_even_with_clean_entry():
    r = _run(act=[{"ticker": _T, "kind": "stop_breach", "action": "SELL — Stop Breached"}])
    assert _only(r)["added"] is False and _only(r)["card"] is True


# ── precedence + failure mode ───────────────────────────────────────────────

def test_g20_name_with_negative_news_stays_in_its_g20_bucket():
    det = [{"ticker": _T, "tier": "WATCH", "dd_from_peak_pct": 12.0, "peak": 120.0,
            "trend_ma": 50}]
    r = _run(news=_news(-0.40), deterioration=det)
    assert _T in _t(r["deterioration_blocked_adds"])
    assert _only(r) == {"added": False, "card": False, "entry": False}


def test_card_and_g20_name_is_listed_once_under_g20():
    det = [{"ticker": _T, "tier": "WATCH", "dd_from_peak_pct": 12.0, "peak": 120.0,
            "trend_ma": 50}]
    r = _run(news=_news(-0.40), deterioration=det, sold={_T})
    assert _T in _t(r["deterioration_blocked_adds"])
    assert _only(r) == {"added": False, "card": False, "entry": False}


def test_entry_check_crash_fails_closed(monkeypatch):
    def boom(**_kw):
        raise RuntimeError("x")
    monkeypatch.setattr(daily_briefing.entry_eligibility, "entry_check", boom)
    r = _run()
    assert _only(r) == {"added": False, "card": False, "entry": True}
    h = r["entry_held_adds"][0]
    assert h["check"] == "check_failed" and h["reason"] == "entry check couldn't run"
    assert "reload the page" in h["clears_when"]


_ADD_BUCKETS = ("add_positions", "entry_held_adds", "card_blocked_adds",
                "deterioration_blocked_adds", "cooldown_adds", "sector_blocked_adds",
                "risk_blocked_adds", "concentration_blocked_adds", "pair_blocked_adds",
                "cluster_blocked_adds")


def _placements(r):
    out = []
    for k in _ADD_BUCKETS:
        out += [(k, t) for t in _t(r[k])]
    return out


def test_invariant_one_place_per_ticker_across_every_add_bucket():
    det = [{"ticker": "A5", "tier": "WATCH", "dd_from_peak_pct": 12.0, "peak": 120.0,
            "trend_ma": 50}]
    r = _run(tickers=("A1", "A2", "A3", "A4", "A5"),
             news=_news(-0.40, ticker="A2"),
             act=[{"ticker": "A3", "kind": "sell_signal", "action": "SELL"},
                  {"ticker": "A5", "kind": "sell_signal", "action": "SELL"}],
             sold={"A4"}, deterioration=det)
    placed = _placements(r)
    tickers = [t for _, t in placed]
    assert sorted(tickers) == ["A1", "A2", "A3", "A4", "A5"] and len(tickers) == len(set(tickers))
    assert dict((t, k) for k, t in placed)["A5"] == "deterioration_blocked_adds"
    assert "A3" not in _t(r["add_positions"])


# ── non-bull ────────────────────────────────────────────────────────────────

def test_bear_return_carries_both_keys_empty():
    r = _run(tone="bear", news=_news(-0.40))
    assert r["card_blocked_adds"] == [] and r["entry_held_adds"] == []
    assert r["adds_paused"] == []        # the entry-held name is NOT "resume on an up day"


def test_flat_adds_paused_excludes_entry_and_card_held_names():
    r = _run(tone="flat", tickers=("A1", "A2", "A3"), news=_news(-0.40, ticker="A2"),
             sold={"A3"})
    assert _t(r["adds_paused"]) == ["A1"]
    assert r["card_blocked_adds"] == [] and r["entry_held_adds"] == []


# ── pure helper ─────────────────────────────────────────────────────────────

def test_add_blocking_cards_helper():
    act = [{"ticker": "a", "kind": "deterioration_trim", "action": "x"},
           {"ticker": "b", "kind": "critical_news", "action": "x"},
           {"ticker": None, "action": {"trim_ticker": "m"}, "kind": "macro"}]
    rev = [dict(_NEWS_WATCH, ticker="c"),
           {"ticker": "d", "action": {"type": "TRIM_TO_TARGET"}},
           {"ticker": "a", "action": {"type": "WATCH"}}]
    out = decision_bucket.add_blocking_cards(act, rev, {"e"})
    crit = decision_bucket.classify_bucket({"kind": "critical_news", "_source": "act"})
    crit_prefix = {"act": "Act Today", "aware": "Monitoring"}[crit]
    assert out == {"A": "Act Today · TRIM", "B": f"{crit_prefix} · critical news",
                   "M": "Monitoring · macro event", "D": "Act Today · TRIM", "E": "sold today"}
    assert decision_bucket.add_blocking_cards(None, None) == {}
    assert list(decision_bucket.review_news_watch_by_ticker(rev)) == ["C"]


# ── wiring pins (text/AST) ──────────────────────────────────────────────────

def _grow_fn():
    src = Path(daily_briefing.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_grow_today")


def test_grow_today_calls_entry_check_with_exactly_stale_news_earnings():
    calls = [n for n in ast.walk(_grow_fn()) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", getattr(n.func, "id", None)) == "entry_check"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw["checks"], ast.Tuple)
    assert [e.attr for e in kw["checks"].elts] == ["check_stale", "check_news", "check_earnings"]


def test_add_card_set_comes_from_add_blocking_cards_not_all_flagged_tickers():
    fn = _grow_fn()
    assert any(isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "add_blocking_cards"
               for n in ast.walk(fn))
    gate = next(n for n in ast.walk(fn)
                if isinstance(n, ast.FunctionDef) and n.name == "_add_block_gate")
    names = {n.id for n in ast.walk(gate) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(gate) if isinstance(n, ast.Attribute)}
    assert "_act_blocked" not in names and "all_flagged_tickers" not in attrs


@pytest.mark.parametrize("card,expected", [
    # a Review-list trim renders in Home's Act Today section -> label says so
    ({"ticker": None, "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "ZT"}},
     "Act Today · TRIM"),
    ({"ticker": _T, "action": {"type": "TRIM_TO_TARGET"}}, "Act Today · TRIM"),
    ({"ticker": _T, "action": {"type": "WATCH"},
      "headline": "earnings in 2d (2026-10-17), weight 5.0%"}, "Monitoring · earnings in 2d"),
    ({"ticker": _T, "action": {"type": "WATCH"}}, "Monitoring · WATCH"),
])
def test_review_card_label_follows_the_section_home_renders_it_in(card, expected):
    out = decision_bucket.add_blocking_cards(None, [card])
    assert out == {_T: expected}
    r = _run(review=[card])
    where = r["card_blocked_adds"][0]["where"]
    assert where == expected.split(" · ")[0]


def test_act_critical_news_label_is_whatever_classify_bucket_says():
    card = {"ticker": _T, "kind": "critical_news", "action": "MONITOR"}
    prefix = {"act": "Act Today", "aware": "Monitoring"}[
        decision_bucket.classify_bucket({**card, "_source": "act"})]
    assert decision_bucket.add_blocking_cards([card], None) == {_T: f"{prefix} · critical news"}


# ── new picks unchanged: a Review news WATCH still blocks via _act_blocked ──

def test_etf_new_pick_still_blocked_by_a_review_news_watch():
    from tests.test_daily_briefing import _etf_candidate
    port = make_port_df([{"ticker": "HELD", "weight": 10.0}])
    ctx = {"tone": "bull"}
    watch = dict(_NEWS_WATCH, ticker="SPY")
    ctl = _grow_today(port, None, [], {}, _TODAY, 100_000.0, ctx,
                      etf_candidates=[_etf_candidate()])
    assert "SPY" in _t(ctl["new_picks"])                       # control: eligible
    r = _grow_today(port, None, [], {}, _TODAY, 100_000.0, ctx, review_list=[watch],
                    etf_candidates=[_etf_candidate()])
    assert "SPY" not in _t(r["new_picks"])
    r2 = _grow_today(port, None, [], {}, _TODAY, 100_000.0, ctx, review_list=[watch],
                     etf_candidates=[_etf_candidate(), _etf_candidate("VOO")])
    assert "VOO" not in _t(r2["new_picks"])                    # group sibling screened out
