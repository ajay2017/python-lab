"""News-feed-restore chunk 3b: the Brief discloses a news-feed outage."""
from datetime import date

from stock_analyzer import news_feed as nf
from stock_analyzer.daily_briefing import build_daily_briefing
from tests.conftest import make_port_df

_TODAY = date(2026, 7, 27)


def _outage(checked=12):
    return {"state": "outage", "checked": checked, "with_news": 0,
            "unavailable": [], "none": [], "partial": False}


def _partial(names):
    return {"state": "ok", "checked": 3, "with_news": 3,
            "unavailable": names, "none": [], "partial": True}


def test_none_and_malformed_return_none():
    assert nf.brief_news_disclosure(None) is None
    assert nf.brief_news_disclosure("outage") is None
    assert nf.brief_news_disclosure({}) is None
    # malformed counts degrade to None, never raise
    assert nf.brief_news_disclosure({"state": "outage", "checked": "x"}) is None
    assert nf.brief_news_disclosure({"state": "ok", "partial": True,
                                     "unavailable": 5}) is None


def test_outage_text():
    d = nf.brief_news_disclosure(_outage(12))
    assert d == {"level": "outage", "text": (
        "📰 News feed unavailable this run — the critical-news and warning-news "
        "checks on your holdings and Grow Today's negative-news Skip did not run "
        "(0 of 12 names returned headlines). Scores count news sentiment as neutral meanwhile.")}


def test_partial_text_lists_up_to_five():
    d = nf.brief_news_disclosure(_partial(["A", "B", "C", "D", "E"]))
    assert d["level"] == "partial"
    assert d["text"] == ("📰 News feed partly unavailable — news couldn't be "
                         "checked for 5 names (A, B, C, D, E); for those names the "
                         "critical-news / warning-news checks and Grow Today's "
                         "negative-news Skip did not run.")
    d7 = nf.brief_news_disclosure(_partial(list("ABCDEFG")))
    assert "(A, B, C, D, E, …)" in d7["text"]
    assert "for 7 names" in d7["text"]
    d5 = nf.brief_news_disclosure(_partial(list("ABCDE")))
    assert "(A, B, C, D, E)" in d5["text"]


def test_ok_not_partial_and_thin_return_none():
    assert nf.brief_news_disclosure(
        {"state": "ok", "checked": 5, "with_news": 5, "unavailable": [],
         "none": [], "partial": False}) is None
    assert nf.brief_news_disclosure(
        {"state": "thin", "checked": 1, "with_news": 0, "unavailable": [],
         "none": [], "partial": False}) is None


def test_all_cached_run_discloses_partial():
    cached = {t: {"news_status": "cached"} for t in "ABCDEF"}
    st = nf.feed_state(cached)
    assert st["state"] == "thin" and st["partial"] is False
    d = nf.brief_news_disclosure(st)
    assert d["level"] == "partial" and "(A, B, C, D, E, …)" in d["text"]
    few = nf.feed_state({t: {"news_status": "cached"} for t in "ABCD"})
    assert nf.brief_news_disclosure(few) is None


def test_home_feed_state_merges_and_never_raises():
    assert nf.home_feed_state(None, None) is None
    assert nf.home_feed_state({}, {}) is None
    s = nf.home_feed_state({"A": {"news_status": "ok"}}, {"B": {"news_status": "error"}})
    assert s["checked"] == 2 and s["state"] == "ok"


def _kwargs():
    return dict(
        port_df=make_port_df([{"ticker": "HELD", "weight": 10.0, "sector": "Tech"}]),
        alert_list=[], risk_recs=[], news_items=[], macro_events=[],
        held_data={}, scanner_results=None, portfolio_value=100_000.0,
        today=_TODAY, market_context={"tone": "bull"},
    )


def test_build_adds_key_only_on_outage_or_partial():
    base = build_daily_briefing(**_kwargs())
    assert "news_feed_disclosure" not in base
    out = build_daily_briefing(**_kwargs(), news_feed=_outage())
    assert out["news_feed_disclosure"] == nf.brief_news_disclosure(_outage())
    part = build_daily_briefing(**_kwargs(), news_feed=_partial(list("VWXYZ")))
    assert part["news_feed_disclosure"]["level"] == "partial"
    # Disclosure only: nothing else changes.
    for k in base:
        assert out[k] == base[k] and part[k] == base[k]


def test_build_unchanged_for_none_ok_thin():
    base = build_daily_briefing(**_kwargs())
    ok = {"state": "ok", "checked": 5, "with_news": 5, "unavailable": [],
          "none": [], "partial": False}
    thin = {"state": "thin", "checked": 1, "with_news": 0, "unavailable": [],
            "none": [], "partial": False}
    for st in (None, ok, thin):
        assert build_daily_briefing(**_kwargs(), news_feed=st) == base
