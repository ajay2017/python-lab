"""News-feed restore, chunk 1 (docs/plans/news-feed-restore.md).

yfinance's Ticker.news hit a retired Yahoo endpoint (silent HTTP 404 parsed as
`[]`), so the feed now comes from yf.Search filtered on relatedTickers, with a
per-ticker `news_status` so a dead feed can never read as a quiet one.

No network: yf.Search / yf.Ticker are faked; db.* is stubbed.
"""
import pandas as pd
import pytest

from stock_analyzer import bundle_loader
from stock_analyzer import news_feed as nf
from stock_analyzer.constants import NEWS_MAX_AGE_DAYS, NEWS_SEARCH_FETCH_COUNT
from stock_analyzer.data import curate_news_items, _parse_news_item
from stock_analyzer.providers import yfinance_provider as yp
from stock_analyzer.sentiment import analyze_news

pytestmark = pytest.mark.fast

NOW = 1_800_000_000.0
DAY = 86400


def _item(title="Acme beats earnings", pub="Barrons.com", rel=("ACME",), ts=NOW - 3600,
          link="https://x/y"):
    d = {"uuid": title, "title": title, "publisher": pub, "link": link,
         "providerPublishTime": int(ts)}
    if rel is not None:
        d["relatedTickers"] = list(rel)
    return d


class _FakeResp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, ""

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeSearch:
    """Stands in for yfinance's UNCACHED YfData().get (the news GET). `response` is a
    dict body, or a list of bodies served in order (last one repeats)."""
    response = {"news": []}
    status = 200
    calls = []
    boom = None

    _memo = {}

    def cache_get(self, url, params=None, timeout=30):
        """Mimics yfinance's lru_cache'd YfData.cache_get (process-wide memo)."""
        key = (url, tuple(sorted(params.items())), timeout)
        if key not in type(self)._memo:
            type(self)._memo[key] = self.get(url, params, timeout)
        return type(self)._memo[key]

    def get(self, url, params=None, timeout=30):
        cls = type(self)
        cls.calls.append((params["q"], {"url": url, "params": params, "timeout": timeout}))
        if cls.boom:
            raise cls.boom
        body = cls.response
        if isinstance(body, list):
            body = body[min(len(cls.calls) - 1, len(body) - 1)]
        return _FakeResp(body, cls.status)


@pytest.fixture
def fake_search(monkeypatch):
    _FakeSearch.response = {"news": []}
    _FakeSearch.status = 200
    _FakeSearch.calls = []
    _FakeSearch.boom = None
    _FakeSearch._memo = {}
    monkeypatch.setattr(yp, "_YfData", _FakeSearch)
    return _FakeSearch


# -- (a) status: error vs none ------------------------------------------------
def test_response_without_news_key_is_error(fake_search):
    fake_search.response = {"message": "Not Found"}      # the silent-404 shape
    items, status = yp.YFinanceProvider().search_news("ACME", NOW)
    assert (items, status) == ([], "error")


def test_empty_news_list_is_none_not_error(fake_search):
    fake_search.response = {"news": []}
    items, status = yp.YFinanceProvider().search_news("ACME", NOW)
    assert (items, status) == ([], "none")


def test_news_present_but_all_filtered_is_none(fake_search):
    fake_search.response = {"news": [_item(rel=("OTHER",))]}
    assert yp.YFinanceProvider().search_news("ACME", NOW)[1] == "none"


def test_ok_status_and_search_kwargs(fake_search):
    fake_search.response = {"news": [_item()]}
    items, status = yp.YFinanceProvider().search_news("ACME", NOW)
    assert status == "ok" and len(items) == 1
    q, kw = fake_search.calls[0]
    p = kw["params"]
    assert q == "ACME" and kw["url"].endswith("/v1/finance/search")
    assert p["q"] == "ACME" and p["newsCount"] == NEWS_SEARCH_FETCH_COUNT
    assert p["quotesCount"] == 1 and p["listsCount"] == 0
    assert p["enableResearchReports"] is False and p["enableCb"] is False


def test_raising_search_is_error(fake_search):
    fake_search.boom = RuntimeError("yahoo down")
    assert yp.YFinanceProvider().search_news("ACME", NOW) == ([], "error")


def test_search_gets_explicit_timeout_under_bundle_budget(fake_search):
    from stock_analyzer.constants import DATA_YF_REQUEST_TIMEOUT_SEC
    yp.YFinanceProvider().search_news("ACME", NOW)
    assert 0 < fake_search.calls[0][1]["timeout"] < DATA_YF_REQUEST_TIMEOUT_SEC


def test_timeout_in_search_is_error_and_bundle_survives(fake_search, monkeypatch):
    monkeypatch.setattr(yp.yf, "Ticker", _FakeTicker)
    fake_search.boom = TimeoutError("read timed out")
    assert yp.YFinanceProvider().search_news("ACME", NOW) == ([], "error")
    b = yp.YFinanceProvider().bundle("ACME")
    assert b["news_status"] == "error" and len(b["history"]) == 30 and b["info"]["sector"]


def test_non_200_and_bad_json_are_error(fake_search):
    fake_search.status = 500
    fake_search.response = {"news": [_item()]}
    assert yp.YFinanceProvider().search_news("ACME", NOW) == ([], "error")
    fake_search.status = 200
    fake_search.response = ValueError("not json")
    assert yp.YFinanceProvider().search_news("ACME", NOW) == ([], "error")


def test_two_consecutive_calls_make_two_requests_not_served_from_cache(fake_search):
    fake_search.response = [{"message": "Not Found"}, {"news": [_item()]}]
    p = yp.YFinanceProvider()
    assert p.search_news("ACME", NOW)[1] == "error"
    items, status = p.search_news("ACME", NOW)
    assert len(fake_search.calls) == 2
    assert status == "ok" and len(items) == 1     # the error shape was NOT replayed


def test_params_match_yfinance_search_for_our_kwargs():
    """Our hand-built GET must send what yf.Search would for the same kwargs."""
    import yfinance as yf
    from yfinance.data import YfData
    seen = {}

    class _Spy:
        def get(self, url, params=None, timeout=30):
            seen.setdefault("yf", (url, dict(params), timeout))
            return _FakeResp({"news": []})
    real = YfData.cache_get
    try:
        YfData.cache_get = lambda self, url, params=None, timeout=30: _Spy().get(url, params, timeout)
        yf.Search("ACME", max_results=1, news_count=NEWS_SEARCH_FETCH_COUNT, lists_count=0,
                  recommended=0, include_cb=False, include_research=False)
    finally:
        YfData.cache_get = real
    _FakeSearch.calls = []
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(yp, "_YfData", _FakeSearch)
    try:
        yp.YFinanceProvider().search_news("ACME", NOW)
    finally:
        mp.undo()
    url, params, _ = seen["yf"]
    ours = _FakeSearch.calls[0][1]
    assert ours["url"] == url and ours["params"] == params


def test_share_class_sibling_counts_but_unrelated_does_not():
    goog = [_item("g", rel=("GOOG",))]
    assert [i["title"] for i in nf.filter_search_news(goog, "GOOGL", NOW)] == ["g"]
    assert [i["title"] for i in nf.filter_search_news([_item("b", rel=("BRK-A",))], "BRK-B", NOW)] == ["b"]
    assert nf.filter_search_news(goog, "MSFT", NOW) == []
    assert nf.filter_search_news([_item("x", rel=("GOOGLE",))], "GOOGL", NOW) == []


def test_search_news_status_pure():
    assert nf.search_news_status({"message": "Not Found"}, []) == "error"
    assert nf.search_news_status(None, []) == "error"
    assert nf.search_news_status({"news": []}, []) == "none"
    assert nf.search_news_status({"news": [1]}, [1]) == "ok"


# -- (b) relatedTickers filter / sort / cap -----------------------------------
def test_filter_drops_wrong_and_missing_related_tickers():
    items = [_item("keep"), _item("wrong", rel=("ZZZ",)), _item("missing", rel=None),
             _item("empty", rel=())]
    out = nf.filter_search_news(items, "ACME", NOW)
    assert [i["title"] for i in out] == ["keep"]


def test_filter_is_case_insensitive_exact_match():
    out = nf.filter_search_news([_item("a", rel=("acme",)), _item("b", rel=("ACMEX",))],
                                "Acme", NOW)
    assert [i["title"] for i in out] == ["a"]


def test_filter_sorts_newest_first_and_caps_at_10():
    items = [_item(f"h{i}", ts=NOW - 100 - i * 10) for i in range(25)]
    items.reverse()   # scrambled input order
    out = nf.filter_search_news(items, "ACME", NOW)
    assert len(out) == 10
    assert [i["title"] for i in out] == [f"h{i}" for i in range(10)]


def test_filter_handles_non_list_input():
    assert nf.filter_search_news(None, "ACME", NOW) == []
    assert nf.filter_search_news([None, "x", 3], "ACME", NOW) == []


# -- (c) age boundary ---------------------------------------------------------
def test_age_boundary_exact_kept_one_second_older_dropped():
    edge = NOW - NEWS_MAX_AGE_DAYS * DAY
    out = nf.filter_search_news([_item("edge", ts=edge), _item("old", ts=edge - 1)],
                                "ACME", NOW)
    assert [i["title"] for i in out] == ["edge"]


def test_item_without_publish_time_is_dropped():
    it = _item("nots")
    del it["providerPublishTime"]
    assert nf.filter_search_news([it], "ACME", NOW) == []


# -- (d) shape / tier parity with the downstream consumers --------------------
def test_search_item_flows_through_curate_and_analyze():
    pubs = {"Barrons.com": 1, "Zacks": 2, "MT Newswires": 3}
    items = [_item(f"Acme story {i}", pub=p, ts=NOW - i * 60) for i, p in enumerate(pubs)]
    title, publisher, url, ts = _parse_news_item(items[0])
    assert (title, publisher, url, ts) == ("Acme story 0", "Barrons.com", "https://x/y", int(NOW))
    curated = curate_news_items({"ACME": {"news_raw": items}})
    assert len(curated) == 3
    for c in curated:
        assert set(c) == {"ticker", "title", "url", "publisher", "ts", "compound", "label", "tier"}
    assert {c["publisher"]: c["tier"] for c in curated} == pubs
    avg, heads = analyze_news(items)
    assert len(heads) == 3 and isinstance(avg, float)


# -- (e) a news failure never fails the bundle --------------------------------
class _FakeTicker:
    def __init__(self, t):
        pass

    def history(self, period="6mo"):
        idx = pd.date_range("2024-01-01", periods=30, freq="D")
        return pd.DataFrame({"Close": [100.0 + i for i in range(30)]}, index=idx)

    info = {"sector": "Technology", "longBusinessSummary": "x"}
    calendar = None
    upgrades_downgrades = None

    @property
    def news(self):
        raise AssertionError("Ticker.news must not be used any more")


def test_news_failure_does_not_fail_the_bundle(fake_search, monkeypatch):
    monkeypatch.setattr(yp.yf, "Ticker", _FakeTicker)
    fake_search.boom = RuntimeError("search 500")
    b = yp.YFinanceProvider().bundle("ACME")
    assert b["news"] == [] and b["news_status"] == "error"
    assert len(b["history"]) == 30 and b["info"]["sector"] == "Technology"


def test_bundle_carries_ok_status(fake_search, monkeypatch):
    monkeypatch.setattr(yp.yf, "Ticker", _FakeTicker)
    real = nf.filter_search_news
    monkeypatch.setattr(nf, "filter_search_news",
                        lambda items, t, now_ts=None, **kw: real(items, t, NOW, **kw))
    fake_search.response = {"news": [_item(ts=NOW)]}
    b = yp.YFinanceProvider().bundle("ACME")
    assert b["news_status"] == "ok" and len(b["news"]) == 1


# -- (f) LLM day-cache rules + cached status, through load_bundle -------------
def _price_df(n=60):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = [100 + i * 0.3 for i in range(n)]
    return pd.DataFrame({"Close": c, "High": [x + 1 for x in c], "Low": [x - 1 for x in c],
                         "Open": c, "Volume": [1e6] * n}, index=idx)


class _Db:
    cache_row = None
    saved = []
    bundle_cache = None

    @classmethod
    def reset(cls, cache_row=None, bundle_cache=None):
        cls.cache_row, cls.saved, cls.bundle_cache = cache_row, [], bundle_cache

    @staticmethod
    def save_bundle_cache(*a, **k): pass

    @classmethod
    def load_bundle_cache(cls, *a, **k): return cls.bundle_cache

    @staticmethod
    def save_fundamentals_cache(*a, **k): pass

    @staticmethod
    def load_fundamentals_cache(*a, **k): return None

    @staticmethod
    def load_analyst_coverage(*a, **k): return None

    @staticmethod
    def save_sector_cache(*a, **k): pass

    @staticmethod
    def load_sector_cache(*a, **k): return ""

    @staticmethod
    def save_etf_lookthrough_cache(*a, **k): pass

    @staticmethod
    def load_etf_lookthrough_cache(*a, **k): return None

    @classmethod
    def load_sentiment_llm_cache(cls, *a, **k): return cls.cache_row

    @classmethod
    def save_sentiment_llm_cache(cls, ticker, day, headlines, avg):
        cls.saved.append((ticker, headlines, avg))


def _bundle(news, status="ok"):
    return {"history": _price_df(), "info": {"sector": ""}, "news": news,
            "news_status": status, "earnings": {}, "revisions": {}, "_info_source": "t"}


@pytest.fixture
def loader(monkeypatch):
    monkeypatch.setattr(bundle_loader, "db", _Db)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _Db.reset()
    return bundle_loader


def _patch_llm(monkeypatch, fn):
    from stock_analyzer import news_intelligence as ni
    monkeypatch.setattr(ni, "rescore_headlines_llm", fn)


def test_empty_rescore_is_never_saved(loader, monkeypatch):
    monkeypatch.setattr(loader, "fetch_ticker_bundle", lambda t, p: _bundle([]))
    _patch_llm(monkeypatch, lambda heads, key, ticker="": heads)   # real fn echoes [] back
    out = loader.load_bundle("ACME")
    assert _Db.saved == []
    assert out["news_status"] == "ok"      # status passed through from the bundle


def test_nonempty_rescore_is_saved(loader, monkeypatch):
    monkeypatch.setattr(loader, "fetch_ticker_bundle", lambda t, p: _bundle([_item(ts=NOW)]))
    _patch_llm(monkeypatch, lambda heads, key, ticker="": [dict(h, score=0.4) for h in heads])
    loader.load_bundle("ACME")
    assert len(_Db.saved) == 1 and len(_Db.saved[0][1]) == 1


def test_cached_empty_row_with_live_headlines_is_a_miss(loader, monkeypatch):
    _Db.reset(cache_row={"headlines": [], "avg_sent": 0.0})
    monkeypatch.setattr(loader, "fetch_ticker_bundle", lambda t, p: _bundle([_item(ts=NOW)]))
    calls = []

    def _llm(heads, key, ticker=""):
        calls.append(len(heads))
        return [dict(h, score=0.4) for h in heads]

    _patch_llm(monkeypatch, _llm)
    out = loader.load_bundle("ACME")
    assert calls == [1] and len(out["headlines"]) == 1 and out["avg_sent"] == 0.4


def test_cached_empty_row_with_no_live_headlines_is_a_hit(loader, monkeypatch):
    _Db.reset(cache_row={"headlines": [], "avg_sent": 0.0})
    monkeypatch.setattr(loader, "fetch_ticker_bundle", lambda t, p: _bundle([]))
    _patch_llm(monkeypatch, lambda *a, **k: pytest.fail("must not rescore"))
    out = loader.load_bundle("ACME")
    assert out["headlines"] == [] and _Db.saved == []


def test_cached_nonempty_row_is_a_hit(loader, monkeypatch):
    row = {"headlines": [{"headline": "h", "score": 0.1}], "avg_sent": 0.1}
    _Db.reset(cache_row=row)
    monkeypatch.setattr(loader, "fetch_ticker_bundle", lambda t, p: _bundle([_item(ts=NOW)]))
    _patch_llm(monkeypatch, lambda *a, **k: pytest.fail("must not rescore"))
    assert loader.load_bundle("ACME")["headlines"] == row["headlines"]


def test_bundle_from_bundle_cache_is_status_cached(loader, monkeypatch):
    def _boom(t, p):
        raise RuntimeError("providers down")

    monkeypatch.setattr(loader, "fetch_ticker_bundle", _boom)
    cached = _bundle([], status="ok")      # even a stored "ok" must not leak through
    _Db.reset(bundle_cache={"bundle": cached, "fetched_at": "2026-10-15T00:00:00+00:00"})
    out = loader.load_bundle("ACME")
    assert out["news_status"] == "cached"


def test_bundle_without_provider_status_infers_from_news(loader, monkeypatch):
    b = _bundle([_item(ts=NOW)])
    del b["news_status"]
    monkeypatch.setattr(loader, "fetch_ticker_bundle", lambda t, p: b)
    _patch_llm(monkeypatch, lambda heads, key, ticker="": heads)
    assert loader.load_bundle("ACME")["news_status"] == "ok"
    b2 = _bundle([])
    del b2["news_status"]
    monkeypatch.setattr(loader, "fetch_ticker_bundle", lambda t, p: b2)
    assert loader.load_bundle("ACME")["news_status"] == "error"


def test_llm_cache_helpers_pure():
    assert nf.should_save_llm_rescore([]) is False
    assert nf.should_save_llm_rescore(None) is False
    assert nf.should_save_llm_rescore([{"a": 1}]) is True
    assert nf.llm_cache_usable(None, [1]) is False
    assert nf.llm_cache_usable({"headlines": []}, [1]) is False
    assert nf.llm_cache_usable({"headlines": []}, []) is True
    assert nf.llm_cache_usable({"headlines": [1]}, [2]) is True


def test_fmp_age_window_reads_the_constant():
    import inspect
    from stock_analyzer.providers import fmp_provider
    src = inspect.getsource(fmp_provider.FMPProvider._fetch_news)
    assert "NEWS_MAX_AGE_DAYS" in src and "days=21" not in src
