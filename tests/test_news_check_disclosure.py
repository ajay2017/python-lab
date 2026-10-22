"""News chunk 3a: the shared news check says WHY it could not run and says so
when it PASSED (docs/plans/news-feed-restore.md).  Pure logic + structural
pins on app.py (no test imports app.py)."""
import datetime
import pathlib

import pytest

from stock_analyzer import entry_eligibility as ee
from stock_analyzer.news_feed import (
    NEWS_STATUS_CACHED, NEWS_STATUS_ERROR, NEWS_STATUS_NONE, NEWS_STATUS_OK,
)
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation

pytestmark = pytest.mark.fast

APP = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
TODAY = datetime.date(2026, 10, 16)
FLAT = {"tone": "flat", "sp500_pct": -0.1, "available": True, "source": "live", "as_of": None}
CLEAR_MAP = {"sectors": set(), "reasons": {}}
GOOD = {"compound": 0.12, "n": 4, "headline": "h"}
ONE = {"compound": 0.30, "n": 1, "headline": "h"}
NEG = {"compound": -0.6, "n": 2, "headline": "bad"}
BAR = f"above the negative-news bar ({ee.NEWS_SENTIMENT_NEGATIVE:+.2f})"


def unavailable(who="this ticker"):
    return f"headlines for {who} couldn't be read — the news check didn't run"


def _b(status):
    return {} if status is None else {"news_status": status}


# ── check_news: four branches ───────────────────────────────────────────────
def test_headlines_pass_with_exact_detail_plural_and_singular():
    r = ee.check_news({"news": GOOD, "bundle": _b(NEWS_STATUS_OK)})
    assert r["kind"] == "passed" and r["check"] == "news"
    assert r["detail"] == f"4 headlines, avg +0.12 — {BAR}"
    r1 = ee.check_news({"news": ONE, "bundle": _b(NEWS_STATUS_OK)})
    assert r1["detail"] == f"1 headline, avg +0.30 — {BAR}"


def test_real_headlines_win_over_a_bad_status():
    assert ee.check_news({"news": GOOD, "bundle": _b(NEWS_STATUS_ERROR)})["kind"] == "passed"
    r = ee.check_news({"news": NEG, "bundle": _b(NEWS_STATUS_ERROR)})
    assert r["kind"] == "hold" and r["check"] == "news"


@pytest.mark.parametrize("status", [NEWS_STATUS_ERROR, NEWS_STATUS_CACHED])
def test_error_or_cached_without_headlines_is_feed_unavailable(status):
    r = ee.check_news({"news": None, "bundle": _b(status)})
    assert r == {"kind": "unchecked", "check": "news", "why": unavailable()}


def test_read_failed_flag_is_unavailable_even_with_status_ok():
    r = ee.check_news({"news": None, "bundle": _b(NEWS_STATUS_OK), "news_read_failed": True})
    assert r["why"] == unavailable()


def test_none_status_names_the_ticker_and_never_says_quiet():
    r = ee.check_news({"news": None, "bundle": _b(NEWS_STATUS_NONE), "ticker": "bkng"})
    assert r["why"] == "no headlines tagged to BKNG"
    r2 = ee.check_news({"news": None, "bundle": _b(NEWS_STATUS_NONE)})
    assert r2["why"] == "no headlines tagged to this ticker"
    assert "quiet" not in r["why"] + r2["why"]
    assert r["why"] != ee.check_news({"news": None, "bundle": _b(NEWS_STATUS_ERROR)})["why"]


@pytest.mark.parametrize("bad", [["x"], "x", 3])
def test_a_malformed_news_input_raises_so_the_check_fails_closed(bad):
    with pytest.raises(TypeError):
        ee.check_news({"news": bad, "bundle": _b(NEWS_STATUS_OK)})
    # watchlist: a crashing entry check is a visible check_failed hold, not ENTER_NOW
    r = build_watchlist_recommendation("XYZ", _data(), entry_ctx=_ctx(news=bad))
    assert r["action"] == "ENTRY_HELD" and r["hold_kind"] == "check_failed"


def test_pass_wording_never_claims_no_negative_news():
    r = ee.check_news({"news": GOOD, "bundle": _b(NEWS_STATUS_OK)})
    assert "no negative news" not in r["detail"].lower()
    assert str(ee.NEWS_SENTIMENT_NEGATIVE) in r["detail"]


def test_missing_status_or_non_dict_bundle_keeps_the_old_wording():
    for ctx in ({"news": None, "bundle": {}}, {"news": None, "bundle": None},
                {"news": None}, {"news": {"compound": None, "n": 0}, "bundle": {}}):
        assert ee.check_news(ctx)["why"] == "no recent headlines to check"


# ── entry_check ─────────────────────────────────────────────────────────────
def _chk(**kw):
    base = dict(bundle={"news_status": NEWS_STATUS_OK}, tone_state=FLAT, sector="Tech",
                macro_map=CLEAR_MAP, news=GOOD, earnings_days=None, ticker="AAA")
    base.update(kw)
    return ee.entry_check(**base)


def test_a_passed_check_lands_in_passed_not_holds():
    r = _chk()
    assert r["eligible"] is True and r["holds"] == []
    assert [p["check"] for p in r["passed"]] == ["news"]
    assert r["passed"][0]["detail"] == f"4 headlines, avg +0.12 — {BAR}"
    assert r["unchecked"] == []


def test_a_hold_still_blocks_and_news_failure_is_unchecked_not_passed():
    r = _chk(news=NEG)
    assert r["eligible"] is False and r["passed"] == []
    r2 = _chk(news=None, bundle={"news_status": NEWS_STATUS_ERROR})
    assert r2["eligible"] is True and r2["passed"] == []
    assert r2["unchecked"] == [{"check": "news", "why": unavailable("AAA")}]
    r3 = _chk(news=None, news_read_failed=True)
    assert r3["unchecked"][0]["why"] == unavailable("AAA")


def test_watchlist_ticker_ctx_news_read_failed():
    run = {"tone_state": FLAT, "macro_map": CLEAR_MAP}
    kw = dict(sector="Tech", classified=True)
    assert ee.watchlist_ticker_ctx(run, news_items=None, **kw)["news_read_failed"] is True
    assert ee.watchlist_ticker_ctx(run, news_items=[], **kw)["news_read_failed"] is False


# ── Analysis tiles ──────────────────────────────────────────────────────────
def _decide(news_items, bundle):
    return ee.analysis_entry_decision(
        bundle=bundle, basis="equity", label="Strong Buy", held=False,
        run_ctx={"tone_state": FLAT, "macro_map": CLEAR_MAP}, sector="Tech",
        classified=True, news_items=news_items, earnings=None, today=TODAY, ticker="aaa")


def _news_tile(decision):
    return next(t for t in ee.analysis_gate_tiles(decision) if t["label"] == "News")


def test_tile_for_passed_news_shows_count_and_average():
    d = _decide([{"title": "x", "compound": 0.2}, {"title": "y", "compound": 0.04}],
                {"news_status": NEWS_STATUS_OK})
    assert d["passed"] and _news_tile(d) == {
        "label": "News", "state": True, "detail": "Above the negative-news bar (2 headlines, avg +0.12)"}


def test_tile_for_unchecked_news_shows_the_reason():
    t = _news_tile(_decide([], {"news_status": NEWS_STATUS_NONE}))
    assert t["state"] is None and t["detail"] == "no headlines tagged to AAA"
    t2 = _news_tile(_decide(None, {"news_status": NEWS_STATUS_OK}))
    assert t2["detail"] == unavailable("AAA")


# ── watchlist cards ─────────────────────────────────────────────────────────
def _data(**ov):
    d = {"total": 70.0, "rec": {"label": "Buy"}, "current_price": 100.0,
         "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
         "targets": {"base": 130.0}, "earnings": None, "news_status": NEWS_STATUS_OK}
    d.update(ov)
    return d


def _ctx(**ov):
    c = {"tone_state": FLAT, "macro_map": CLEAR_MAP, "sector": "Technology",
         "news": {"compound": 0.1, "n": 3, "headline": "fine"}}
    c.update(ov)
    return c


def test_enter_now_card_carries_entry_passed():
    r = build_watchlist_recommendation("XYZ", _data(), entry_ctx=_ctx())
    assert r["action"] == "ENTER_NOW"
    assert [p["detail"] for p in r["entry_passed"]] == [
        f"3 headlines, avg +0.10 — {BAR}"]


def test_entry_held_card_carries_entry_passed():
    # tone bear holds; the news check still ran and passed
    bear = {"tone": "bear", "sp500_pct": -0.8, "available": True, "source": "home",
            "as_of": "09:42"}
    r = build_watchlist_recommendation("XYZ", _data(), entry_ctx=_ctx(tone_state=bear))
    assert r["action"] == "ENTRY_HELD"
    assert [p["detail"] for p in r["entry_passed"]] == [
        f"3 headlines, avg +0.10 — {BAR}"]


def test_card_unchecked_reason_distinguishes_feed_down_from_untagged():
    down = build_watchlist_recommendation(
        "XYZ", _data(news_status=NEWS_STATUS_ERROR), entry_ctx=_ctx(news=None))
    assert down["entry_unchecked"] == [{"check": "news", "why": unavailable("XYZ")}]
    untagged = build_watchlist_recommendation(
        "XYZ", _data(news_status=NEWS_STATUS_NONE), entry_ctx=_ctx(news=None))
    assert untagged["entry_unchecked"] == [{"check": "news", "why": "no headlines tagged to XYZ"}]
    failed = build_watchlist_recommendation(
        "XYZ", _data(), entry_ctx=_ctx(news=None, news_read_failed=True))
    assert failed["entry_unchecked"][0]["why"] == unavailable("XYZ")


# ── email ───────────────────────────────────────────────────────────────────
def _card(**kw):
    c = {"ticker": "AAA", "action": "ENTER_NOW", "score": 80.0, "price": 100.0,
         "entry_lo": 95.0, "entry_hi": 105.0, "stop": 90.0, "rr": 2.5, "summary": "s"}
    c.update(kw)
    return c


def test_email_shows_news_checked_line_only_when_passed():
    from stock_analyzer.notify import render_watchlist_entries_email
    passed = [{"check": "news", "detail": f"3 headlines, avg +0.10 — {BAR}"}]
    _s, html = render_watchlist_entries_email([_card(entry_passed=passed)], built_at="x")
    assert f"News checked: 3 headlines, avg +0.10 — {BAR}" in html
    for none in (None, [], [{"check": "macro", "detail": "x"}]):
        _s, h = render_watchlist_entries_email([_card(entry_passed=none)], built_at="x")
        assert "News checked" not in h
    _s, h = render_watchlist_entries_email([_card()], built_at="x")
    assert "News checked" not in h


# ── app.py wiring (structural) ──────────────────────────────────────────────
def test_app_wiring():
    i = APP.index("_entry_elig.analysis_entry_decision(")
    assert "ticker=_t" in APP[i:i + 600]
    assert 'st.caption(f"📰 News checked:' in APP
    assert '_wr.get("entry_passed")' in APP
    j = APP.index("_ap_btn_shown = False")
    k = APP.index("Opens Trade Journal pre-filled", j)
    chain = APP[j:k]
    assert chain.count("_ap_btn_shown = True") == chain.count("_render_trade_button(") == 5
    assert "if not _da_withheld and _ap_btn_shown:" in APP[k - 200:k]
    # each flag-set sits directly before its button call
    for m in range(len(chain)):
        if chain.startswith("_render_trade_button(", m):
            assert chain[:m].rstrip().endswith("_ap_btn_shown = True")


def test_email_shows_base_target_note_escaped_only_when_present():
    from stock_analyzer.notify import render_watchlist_entries_email
    _s, h = render_watchlist_entries_email(
        [_card(rr_target_note="Base target: <b>x</b> & y")], built_at="x")
    assert "Base target: &lt;b&gt;x&lt;/b&gt; &amp; y" in h
    assert "<b>x</b>" not in h
    for none in (None, "", 5):
        _s, h = render_watchlist_entries_email([_card(rr_target_note=none)], built_at="x")
        assert "Base target" not in h
