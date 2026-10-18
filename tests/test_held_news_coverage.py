"""Held-news coverage chunk 2 (docs/plans/held-news-coverage.md D-A, D-B, D-C,
D-E): Act Today's critical-news card and Review's warning-news WATCH check
EVERY holding inside a recency window, via pure functions in held_news_check.
Fixed dates only -- never the clock."""
from datetime import date

import pytest

from stock_analyzer import held_news_check as hnc
from stock_analyzer.constants import (
    NEWS_CRITICAL_MIN_HEADLINES,
    NEWS_SENTIMENT_CRITICAL,
    NEWS_SENTIMENT_WARN,
)
from stock_analyzer.daily_briefing import (
    _act_today, _review_list, _cross_reference, build_daily_briefing,
)
from stock_analyzer.data import curate_brief_news, curate_news_items
from tests.conftest import find_item, make_port_df

pytestmark = pytest.mark.fast

_TODAY = date(2026, 10, 19)                       # Monday -> window starts Fri 10-16
_START = hnc.news_window_start(_TODAY)
_S = _START.timestamp()
_HELD = {"AAA", "BBB"}


def _it(t="AAA", c=-0.5, tier=1, ts=None, title=None):
    return {"ticker": t, "compound": c, "tier": tier,
            "ts": _S + 100 if ts is None else ts,
            "title": title or f"{t} headline {c} {tier} {ts}"}


# ── critical_news_by_ticker ──────────────────────────────────────────────────
def _crit(items, held=_HELD, ts=_S):
    return hnc.critical_news_by_ticker(items, held, ts)


def _pair(**kw):
    return [_it(**kw) for _ in range(NEWS_CRITICAL_MIN_HEADLINES)]


def test_critical_threshold_boundary():
    assert "AAA" in _crit(_pair(c=NEWS_SENTIMENT_CRITICAL))          # -0.25 counts
    assert _crit(_pair(c=NEWS_SENTIMENT_CRITICAL + 0.001)) == {}     # -0.249 does not


def test_critical_tier_boundary():
    assert "AAA" in _crit(_pair(tier=2))
    assert _crit(_pair(tier=3)) == {}
    no_tier = [{k: v for k, v in i.items() if k != "tier"} for i in _pair()]
    assert _crit(no_tier) == {}                                       # missing tier reads as 3


def test_critical_min_headlines():
    assert "AAA" in _crit(_pair())
    assert _crit(_pair()[:-1]) == {}


def test_critical_window_boundary_inclusive_and_one_outside():
    one_in = _it(ts=_S + 5)
    assert _crit([one_in, _it(ts=_S - 1)]) == {}                      # 1 in + 1 just before: no
    assert "AAA" in _crit([_it(ts=_S), _it(ts=_S + 1)])               # ts == start counts


@pytest.mark.parametrize("bad", [0, None, -5, "x"])
def test_critical_undated_never_counts(bad):
    assert _crit([_it(ts=_S + 5), {**_it(), "ts": bad}]) == {}


def test_critical_non_held_ignored_and_case_insensitive():
    assert _crit(_pair(t="ZZZ")) == {}
    assert "AAA" in _crit(_pair(t="aaa"))


def test_critical_order_and_worst_first():
    items = [_it("BBB", -0.30, title="b1"), _it("AAA", -0.40, title="a1"),
             _it("BBB", -0.90, title="b2"), _it("AAA", -0.60, title="a2")]
    out = _crit(items)
    assert list(out) == ["BBB", "AAA"]                                # first appearance
    assert [i["title"] for i in out["BBB"]] == ["b2", "b1"]
    assert [i["title"] for i in out["AAA"]] == ["a2", "a1"]


# ── warning_news_by_ticker ───────────────────────────────────────────────────
def _warn(items, exclude=frozenset(), held=_HELD):
    return hnc.warning_news_by_ticker(items, held, _S, exclude=set(exclude))


def test_warning_threshold_boundary():
    assert "AAA" in _warn([_it(c=NEWS_SENTIMENT_WARN, tier=3)])       # -0.05 counts
    assert _warn([_it(c=NEWS_SENTIMENT_WARN + 0.001, tier=3)]) == {}  # -0.049 does not


def test_warning_single_tier1_critical_headline_gets_a_card_D_C():
    w = _warn([_it(c=-0.60, tier=1)])
    assert w["AAA"]["n_negative"] == 1 and w["AAA"]["n_act_qualifying"] == 1


def test_warning_two_tier3_critical_headlines_do_not_qualify_for_act():
    w = _warn([_it(c=-0.70, tier=3, ts=_S + 1), _it(c=-0.70, tier=3, ts=_S + 2)])
    assert w["AAA"]["n_negative"] == 2 and w["AAA"]["n_act_qualifying"] == 0


def test_warning_exclude_window_undated_and_worst_item():
    items = [_it("AAA", -0.10, 3, title="mild"), _it("AAA", -0.60, 1, title="bad"),
             _it("AAA", -0.90, 1, ts=_S - 1), {**_it("AAA", -0.95, 1), "ts": 0},
             _it("BBB", -0.10, 3)]
    w = _warn(items, exclude={"BBB"})
    assert list(w) == ["AAA"]
    assert w["AAA"]["item"]["title"] == "bad"
    assert (w["AAA"]["n_negative"], w["AAA"]["n_act_qualifying"]) == (2, 1)


def test_window_label_has_no_leading_zero():
    assert hnc.window_label(hnc.news_window_start(date(2026, 10, 19))) == "Fri Oct 16"
    assert hnc.window_label(hnc.news_window_start(date(2026, 11, 3))) == "Mon Nov 2"


# ── _act_today / _review_list wording and coordination ───────────────────────
def _pdf():
    return make_port_df([{"ticker": "AAA", "weight": 4.0, "score": 70.0},
                         {"ticker": "BBB", "weight": 4.0, "score": 70.0}])


def _act(news):
    return _act_today(_pdf(), [], [], news, [], _TODAY)


def _rev(news, act=None):
    return _review_list(_pdf(), news, [], {}, _TODAY, act_today=act or [])


def test_act_card_wording_names_window_count_and_worst():
    items = [_it(c=-0.40, tier=2, ts=_S + 1, title="mid"),
             _it(c=-0.70, tier=1, ts=_S + 2, title="worst one"),
             _it(c=-0.30, tier=1, ts=_S + 3, title="third")]
    card = find_item(_act(items), "AAA")
    assert card["kind"] == "critical_news" and card["action"] == "Watch — Critical News"
    assert card["why"] == ('3 headlines from tier-1/2 sources since Fri Oct 16 (worst -0.70): '
                           '"worst one" — material, but not a mechanical sell.')
    assert card["priority"] == "high" and card["icon"] == "🚨"


def test_review_watch_wording_plural_and_singular():
    two = _rev([_it(c=-0.10, tier=3, ts=_S + 1, title="t1"),
                _it(c=-0.20, tier=3, ts=_S + 2, title="t2")])
    w = find_item(two, "AAA")
    assert w["watch_kind"] == "news" and w["action"] == {"type": "WATCH"} and w["weight"] == 0
    assert w["priority"] == "low" and w["icon"] == "📰"
    assert w["headline"] == 'negative headlines since Fri Oct 16 (worst -0.20): "t2"'
    assert w["why"] == ("2 negative headlines since Fri Oct 16 (worst -0.20). "
                        "Not enough alone for an Act Today card — wait for confirmation.")
    assert w["trigger"] == (f"{NEWS_CRITICAL_MIN_HEADLINES} more tier-1/2 headlines at or below "
                            f"{NEWS_SENTIMENT_CRITICAL:+.2f} since Fri Oct 16 "
                            "→ Act Today critical news (tighten your stop).")
    one = find_item(_rev([_it(c=-0.60, tier=1, title="solo")]), "AAA")
    assert one["headline"] == 'negative headline since Fri Oct 16 (worst -0.60): "solo"'
    assert one["why"] == ("One negative headline since Fri Oct 16 (sentiment -0.60). "
                          "Not enough alone for an Act Today card — wait for confirmation.")
    need = NEWS_CRITICAL_MIN_HEADLINES - 1
    assert one["trigger"].startswith(f"{need} more tier-1/2 headline{'s' if need != 1 else ''} at or below")


def test_review_news_watch_excluded_for_act_card_and_actioned_tickers():
    items = _pair(ts=_S + 1) + [_it("BBB", -0.10, 3)]
    act = _act(items)
    assert find_item(act, "AAA")["kind"] == "critical_news"
    rev = _rev(items, act=act)
    assert find_item(rev, "AAA") is None            # Act card owns AAA
    assert find_item(rev, "BBB") is not None        # BBB is not actioned
    rev2 = _review_list(_pdf(), items, [], {}, _TODAY,
                        act_today=[{"ticker": "BBB", "kind": "x"}] + act)
    assert find_item(rev2, "BBB") is None           # an Act-listed ticker is excluded


def test_macro_trim_final_pass_still_removes_the_news_watch():
    pdf = make_port_df([
        {"ticker": "AAA", "weight": 35.0, "sector": "Semiconductors", "score": 60.0},
        {"ticker": "BBB", "weight": 20.0, "sector": "Healthcare"},
    ])
    ev = {"date": "2026-10-20", "impact": "HIGH", "category": "Employment",
          "event": "Non-Farm Payrolls", "affected_tickers": ["AAA"]}
    news = [_it("AAA", -0.10, 3)]
    rev = _review_list(pdf, news, [ev], {}, _TODAY, portfolio_value=100_000.0, act_today=[])
    trims = [i for i in rev if (i.get("action") or {}).get("type") == "PROTECTIVE_TRIM"]
    assert trims and trims[0]["action"]["trim_ticker"] == "AAA", "precondition: macro trim lands on AAA"
    assert not [i for i in rev if i.get("watch_kind") == "news"]
    # Control: without the macro event the same news produces the WATCH.
    assert find_item(_review_list(pdf, news, [], {}, _TODAY, act_today=[]), "AAA")["watch_kind"] == "news"


def test_old_direct_callers_without_window_kwargs_use_todays_window():
    # _act_today / _review_list with no window params compute it from `today`.
    assert find_item(_act(_pair(ts=_S + 1)), "AAA") is not None
    assert find_item(_act(_pair(ts=_S - 1)), "AAA") is None


# ── end-to-end: the motivating case (a quiet holding behind 19 busy ones) ────
_NEG = ["QUIET plunges as fraud probe deepens and lawsuit filed",
        "QUIET crashes on terrible losses, bankruptcy fears grow"]
_MILD = "QUIET reports a slight miss"


def _bundle(headlines):
    return {"news_raw": [{"title": t, "publisher": "Reuters", "link": f"https://x/{i}",
                          "providerPublishTime": ts}
                         for i, (t, ts) in enumerate(headlines)]}


def _book(quiet_headlines):
    held = {}
    for k in range(19):
        held[f"B{k:02d}"] = _bundle([(f"B{k:02d} routine update {i}", int(_S) + 50_000 + k * 100 + i)
                                     for i in range(8)])
    held["QUIET"] = _bundle(quiet_headlines)
    return held


def _brief(held, news):
    port = make_port_df([{"ticker": t, "weight": 5.0} for t in held])
    return build_daily_briefing(
        port_df=port, alert_list=[], risk_recs=[], news_items=news, macro_events=[],
        held_data=held, scanner_results=None, portfolio_value=100_000.0, today=_TODAY,
        market_context={"tone": "flat"},
    )


def _quiet_neg(ts0):
    return [(t, int(ts0) + i) for i, t in enumerate(_NEG)]


def test_end_to_end_quiet_holding_behind_busy_ones_now_fires():
    held = _book(_quiet_neg(_S + 100))
    top20 = curate_news_items(held)
    assert not any(i["ticker"] == "QUIET" for i in top20), "precondition: top-20 crowds QUIET out"
    full = curate_news_items(held, max_items=None)
    q = [i for i in full if i["ticker"] == "QUIET"]
    assert len(q) == 2 and all(i["compound"] <= NEWS_SENTIMENT_CRITICAL and i["tier"] <= 2 for i in q)
    brief = _brief(held, curate_brief_news(held, {}))
    card = find_item(brief["act_today"], "QUIET")
    assert card is not None and card["kind"] == "critical_news"
    assert "2 headlines from tier-1/2 sources since Fri Oct 16" in card["why"]


def test_end_to_end_headlines_just_before_the_window_do_not_fire():
    held = _book(_quiet_neg(_S - 10))          # both items at start-10 / start-9: outside
    brief = _brief(held, curate_brief_news(held, {}))
    assert find_item(brief["act_today"], "QUIET") is None
    assert not [i for i in brief["review_list"] if i.get("ticker") == "QUIET"]


def test_end_to_end_single_mild_headline_gets_a_review_watch():
    held = _book([(_MILD, int(_S) + 100)])
    full = curate_news_items(held, max_items=None)
    q = [i for i in full if i["ticker"] == "QUIET"]
    assert len(q) == 1 and NEWS_SENTIMENT_CRITICAL < q[0]["compound"] <= NEWS_SENTIMENT_WARN
    assert not any(i["ticker"] == "QUIET" for i in curate_news_items(held))
    brief = _brief(held, curate_brief_news(held, {}))
    w = find_item(brief["review_list"], "QUIET")
    assert w is not None and w["watch_kind"] == "news"
    held_old = _book([(_MILD, int(_S) - 10)])
    assert find_item(_brief(held_old, curate_brief_news(held_old, {}))["review_list"], "QUIET") is None


# ── curate_brief_news: D-E (no held/candidate de-dup) ────────────────────────
def test_candidate_keeps_a_negative_headline_shared_with_a_held_name():
    shared = "Sector shock: fraud probe deepens and lawsuit filed, shares plunge"
    held = {"HLD": _bundle([(shared, 5_000)])}
    cand = {"CAND": _bundle([(shared, 5_001)])}
    out = curate_brief_news(held, cand)
    assert sorted(i["ticker"] for i in out if i["title"] == shared) == ["CAND", "HLD"]
    cand_item = next(i for i in out if i["ticker"] == "CAND")
    assert cand_item["compound"] <= NEWS_SENTIMENT_CRITICAL
    port = make_port_df([{"ticker": "HLD", "weight": 100.0}])
    row = {"Ticker": "CAND", "Signal": "BUY", "Score": 80}
    x = _cross_reference("CAND", row, port, out, held, date(2026, 10, 19))
    assert [c for c in x["conflicts"] if c.startswith("News sentiment: Negative")]


def test_gate_list_contains_held_news_check():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "pre_tool_checks.py"
    spec = importlib.util.spec_from_file_location("_ptc_held", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert "stock_analyzer/held_news_check.py" in m._GATE_FILES


def test_review_news_watch_carries_structured_count_and_window():
    # 7c reads these instead of parsing the headline prose (chunk 7a review).
    two = _rev([_it(c=-0.10, tier=3, ts=_S + 1, title="t1"),
                _it(c=-0.20, tier=3, ts=_S + 2, title="t2")])
    w = find_item(two, "AAA")
    assert w["news_n_negative"] == 2 and w["news_window_label"] == "Fri Oct 16"
    one = find_item(_rev([_it(c=-0.10, tier=3, ts=_S + 1, title="t1")]), "AAA")
    assert one["news_n_negative"] == 1
