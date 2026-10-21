"""Headline relevance (docs/plans/headline-relevance.md): a headline counts toward a
ticker only if its TITLE names the company or the ticker.

Pure module tests, load_bundle wiring, the shared entry check's wording, and the
held-news integration.  No network; db.* is stubbed.  Title fixtures are real live
Yahoo headlines (2026-10-17)."""
import pandas as pd
import pytest

from stock_analyzer import bundle_loader
from stock_analyzer import entry_eligibility as ee
from stock_analyzer import news_feed as nf
from stock_analyzer import news_relevance as nr
from stock_analyzer.data import curate_news_items
from stock_analyzer.held_news_check import warning_news_by_ticker

pytestmark = pytest.mark.fast

NAMES = {
    "ABBV": ["AbbVie Inc."], "MRVL": ["Marvell Technology, Inc."],
    "XOM": ["ExxonMobil Holdings Corporation"], "META": ["Meta Platforms, Inc."],
    "HOOD": ["Robinhood Markets, Inc."], "MU": ["Micron Technology, Inc."],
    "AMD": ["Advanced Micro Devices, Inc."], "NVDA": ["NVIDIA Corporation"],
    "INTC": ["Intel Corporation"], "ON": ["ON Semiconductor Corporation"],
    "GOOGL": ["Alphabet Inc."], "GOOG": ["Alphabet Inc."],
    "JPM": ["JPMorgan Chase & Co."], "COF": ["Capital One Financial Corporation"],
    "AAPL": ["Apple Inc."], "BRK-B": ["Berkshire Hathaway Inc. New"],
    "T": ["AT&T Inc."], "A": ["Agilent Technologies, Inc."], "F": ["Ford Motor Company"],
    "NOW": ["ServiceNow, Inc."], "IT": ["Gartner, Inc."], "KEY": ["KeyCorp"],
    "ARM": ["Arm Holdings plc"], "SAP": ["SAP SE"],
}


def rel(title, ticker, names=None):
    names = NAMES[ticker] if names is None else names
    return nr.is_relevant(title, ticker, nr.name_terms(ticker, names))


# ── dropped: the five live fixtures + extras ─────────────────────────────────
@pytest.mark.parametrize("ticker,title", [
    ("ABBV", "Is Amgen Stock Increasing Your Market Risk?"),
    ("ABBV", "When Amgen's Forecast And Chart Say The Same Thing, Pay Attention"),
    ("MRVL", "Dell Stock Closes Its Worst Week in Years"),
    ("XOM", "Oil Topped $110 as Tech Stocks Lost $1 Trillion in a Day"),
    ("META", "BofA Warns AI Investors Could Lose Even If the Technology Works"),
    ("HOOD", "AMC's CEO warns major exchange to stop selling 'fake' versions of his stock"),
    ("NVDA", "Entegris, Micron, AMD, Marvell Technology Fall"),
    ("AMD", "Dell, HP and MRVL fall"),
    ("AAPL", "iPhone 18 Sales Slump in China"),                      # pins R4: no product names
])
def test_unrelated_headline_is_dropped(ticker, title):
    assert rel(title, ticker) is False


# ── kept ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ticker,title", [
    ("XOM", "Exxon Stocks Rise Higher as Crude Jumps"),
    ("XOM", "ExxonMobil’s Refining Profits Surge"),                   # curly possessive
    ("XOM", "XOM Stock Climbs After Earnings"),
    ("META", "Meta is 'back on offense,' BofA says"),
    ("ABBV", "What AbbVie (ABBV)'s Phase 3 Results Mean"),
    ("MRVL", "Broadcom Falls 4% on Guidance; Marvell Drops 4%"),
    ("MRVL", "Entegris, Micron, AMD, Marvell Technology Fall"),
    ("MU", "Entegris, Micron, AMD, Marvell Technology Fall"),
    ("AMD", "Entegris, Micron, AMD, Marvell Technology Fall"),
    ("MRVL", "Dell, HP and MRVL fall"),
    ("MU", "Jim Cramer On Micron (MU) & Intel (INTC)"),
    ("INTC", "Jim Cramer On Micron (MU) & Intel (INTC)"),
    ("MU", "Micron Is Down 30% From Its High"),
    ("ON", "ON Stock Draws Bullish Initiation From Analyst"),
    ("ON", "onsemi to Announce Quarterly Results"),
    ("ON", "What to Expect From ON Semiconductor's Earnings"),
    ("GOOGL", "Google Puts Gemini Into Every Product"),
    ("GOOG", "Google Puts Gemini Into Every Product"),
    ("JPM", "Goldman vs. JPMorgan: Which Bank Wins"),
    ("JPM", "JPMorganChase Declares Preferred Stock Dividends"),
    ("COF", "Capital One (COF) Stock Sees Unusual Volume"),
    ("AMD", "1 Red Flag That AMD Investors Should Watch"),
    ("AAPL", "Apple’s $1,999 Foldable iPhone Is Coming"),
    ("BRK-B", "Berkshire Hathaway Buys Back Shares"),
    ("BRK-B", "BRK.B Hits a Record High"),
    ("T", "AT&T Raises Guidance"),
    ("A", "$A jumps on guidance"),
    ("A", "Agilent (A) Beats Estimates"),
    ("F", "Ford Recalls 200,000 Trucks"),
    ("IT", "Gartner (IT) Cuts Outlook"),
    ("KEY", "KeyCorp Profit Falls"),
    ("ARM", "Arm Unveils New Chip"),
    ("HOOD", "HOOD Stock Jumps"),
    ("HOOD", "Robinhood Markets Reports Record Volume"),
])
def test_relevant_headline_is_kept(ticker, title):
    assert rel(title, ticker) is True


# ── short / common-word tickers and names ────────────────────────────────────
@pytest.mark.parametrize("ticker,title", [
    ("ON", "Cirrus Logic no Longer Pursuing a Deal"),
    ("ON", "Stocks Rally On Strong Jobs Data"),
    ("COF", "Venture Capital Funding Dries Up"),
    ("AMD", "Advanced Chip Packaging Demand Soars"),
    ("T", "T-Mobile Gains"),
    ("A", "A Stock to Buy"),
    ("F", "F Stock Falls"),                     # single letter: no "T Stock" form
    ("NOW", "Buy These 3 Stocks NOW"),
    ("IT", "IT Spending Slows"),
    ("KEY", "3 KEY Takeaways"),
    ("ARM", "AI arms race heats up"),
    ("ARM", "Strong-arm tactics at the bank"),
    ("HOOD", "Robin Hood Tax Proposal"),
    ("META", "meta-analysis finds no effect"),
    ("SAP", "Tree Sap Prices Climb"),
])
def test_short_or_common_word_does_not_false_match(ticker, title):
    assert rel(title, ticker) is False


def test_all_caps_title_disables_the_bare_ticker():
    assert rel("AMD EARNINGS BEAT ESTIMATES", "AMD") is False
    assert rel("Amd earnings beat; AMD jumps", "AMD") is True     # mixed case: bare ok
    assert rel("$AMD SURGES", "AMD") is True                      # $T always counts


def test_ticker_stock_form_needs_two_chars():
    assert rel("ON Stock Falls", "ON") is True
    assert rel("T Stock Falls", "T") is False


def test_exchange_prefixed_forms_always_count():
    assert rel("Chip Rally (NASDAQ: ZZZ) Continues", "ZZZ", ["Zed Zed Zed"]) is True
    assert rel("Chip Rally NYSE: ZZZ Continues", "ZZZ", ["Zed Zed Zed"]) is True


def test_substring_is_not_a_match():
    assert rel("Marvellous Day For Everyone", "MRVL") is False
    assert rel("Pineapple Prices Rise", "AAPL", ["Apple Inc."]) is False


# ── name derivation ──────────────────────────────────────────────────────────
def _t(ticker, name):
    return {t for t, _ in nr.name_terms(ticker, [name])}


def test_truncated_name_still_yields_core():
    t = _t("COF", "Capital One Financial Corporati")
    assert "Capital One Financial" in t and "Capital One" in t
    assert "Capital" not in t and not any(x.endswith("Corporati") for x in t)


def test_sap_se_and_berkshire():
    assert _t("SAP", "SAP  SE") == {"SAP"}
    assert dict(nr.name_terms("SAP", ["SAP SE"]))["SAP"] is True        # case-sensitive
    t = _t("BRK-B", "Berkshire Hathaway Inc. New")
    assert {"Berkshire Hathaway", "Berkshire"} <= t and not any("New" in x or "Inc" in x for x in t)


def test_on_semiconductor_never_yields_bare_on():
    t = _t("ON", "ON Semiconductor Corporation")
    assert "ON" not in t and "ON Semiconductor" in t and "onsemi" in t


def test_advanced_micro_devices_never_yields_bare_advanced():
    t = _t("AMD", "Advanced Micro Devices")
    assert "Advanced" not in t and "Advanced Micro Devices" in t


def test_industry_trim_and_short_terms_case_sensitivity():
    cs = dict(nr.name_terms("META", ["Meta Platforms, Inc."]))
    assert cs["Meta"] is True and cs["Meta Platforms"] is False and cs["Facebook"] is False
    assert "Robinhood" in _t("HOOD", "Robinhood Markets, Inc.")


def test_class_suffix_and_share_class_aliases():
    assert "Alphabet" in _t("GOOGL", "Alphabet Inc. Class A")
    assert {"Google", "Alphabet"} <= {t for t, _ in nr.name_terms("GOOG", [])}
    assert "Berkshire" in {t for t, _ in nr.name_terms("BRK-A", [])}


def test_item_title_flat_nested_missing():
    assert nr.item_title({"title": " a "}) == "a"
    assert nr.item_title({"content": {"title": "b"}}) == "b"
    assert nr.item_title({"content": None}) == "" and nr.item_title(None) == ""


# ── split_relevant ───────────────────────────────────────────────────────────
def _it(title, ts=1):
    return {"title": title, "publisher": "P", "providerPublishTime": ts, "link": "u"}


def test_split_preserves_order_and_drops_untitled():
    items = [_it("Marvell up"), _it("Dell down"), {"publisher": "x"}, _it("MRVL (MRVL) again")]
    s = nr.split_relevant(items, "MRVL", NAMES["MRVL"])
    assert s["checked"] is True
    assert [i["title"] for i in s["kept"]] == ["Marvell up", "MRVL (MRVL) again"]
    assert [i["title"] for i in s["unrelated"]] == ["Dell down"]


def test_split_fails_open_without_a_name():
    items = [_it("Dell down"), _it("Anything")]
    s = nr.split_relevant(items, "MRVL", [])
    assert s == {"kept": items, "unrelated": [], "checked": False}
    assert nr.split_relevant(items, "MRVL", None)["checked"] is False
    # a ticker form alone is NOT enough to call it checked
    assert nr.split_relevant([_it("(ZZZ) up")], "ZZZ", [])["checked"] is False


def test_only_stoplisted_name_fails_open():
    s = nr.split_relevant([_it("Global (ZZZ) misses")], "ZZZ", ["Global Inc."])
    assert s["checked"] is False and len(s["kept"]) == 1


# ── nf.search_quote_names / keep ─────────────────────────────────────────────
def test_search_quote_names_matches_symbol_or_sibling_only():
    resp = {"quotes": [{"symbol": "ZZ", "shortname": "Other Co"},
                       {"symbol": "goog", "shortname": "Alphabet Inc.", "longname": "Alphabet Inc.",
                        "name": "ALPHABET"}]}
    assert nf.search_quote_names(resp, "GOOGL") == ["Alphabet Inc.", "ALPHABET"]
    assert nf.search_quote_names(resp, "MSFT") == []          # never quotes[0] blindly
    for bad in (None, {}, {"quotes": None}, {"quotes": ["x", 3, None]}, "s"):
        assert nf.search_quote_names(bad, "GOOG") == []


def test_filter_search_news_keep_none_is_uncapped_default_unchanged():
    now = 1_800_000_000.0
    items = [{"title": f"t{i}", "relatedTickers": ["ACME"], "providerPublishTime": now - i}
             for i in range(25)]
    assert len(nf.filter_search_news(items, "ACME", now)) == nf.NEWS_KEEP_PER_TICKER
    assert len(nf.filter_search_news(items, "ACME", now, keep=None)) == 25
    assert len(nf.filter_search_news(items, "ACME", now, keep=3)) == 3


# ── load_bundle wiring ───────────────────────────────────────────────────────
def _price_df(n=60):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = [100 + i * 0.3 for i in range(n)]
    return pd.DataFrame({"Close": c, "High": [x + 1 for x in c], "Low": [x - 1 for x in c],
                         "Open": c, "Volume": [1e6] * n}, index=idx)


class _Db:
    cache_row = None
    bundle_cache = None
    saved = []

    @classmethod
    def reset(cls, cache_row=None, bundle_cache=None):
        cls.cache_row, cls.bundle_cache, cls.saved = cache_row, bundle_cache, []

    save_bundle_cache = staticmethod(lambda *a, **k: None)
    load_bundle_cache = classmethod(lambda cls, *a, **k: cls.bundle_cache)
    save_fundamentals_cache = staticmethod(lambda *a, **k: None)
    load_fundamentals_cache = staticmethod(lambda *a, **k: None)
    load_analyst_coverage = staticmethod(lambda *a, **k: None)
    load_analyst_coverage_or_none = staticmethod(lambda *a, **k: None)
    save_sector_cache = staticmethod(lambda *a, **k: None)
    load_sector_cache = staticmethod(lambda *a, **k: "")
    save_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
    load_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
    load_sentiment_llm_cache = classmethod(lambda cls, *a, **k: cls.cache_row)
    save_sentiment_llm_cache = classmethod(
        lambda cls, t, d, h, a: cls.saved.append((t, h, a)))


def _bundle(news, info=None, names=None, status="ok"):
    b = {"history": _price_df(), "info": info if info is not None else {"sector": ""},
         "news": news, "news_status": status, "earnings": {}, "revisions": {},
         "_info_source": "t"}
    if names is not None:
        b["news_names"] = names
    return b


@pytest.fixture
def loader(monkeypatch):
    monkeypatch.setattr(bundle_loader, "db", _Db)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _Db.reset()
    return bundle_loader


MRVL_INFO = {"sector": "", "shortName": "Marvell Technology, Inc."}


def test_cap_applies_after_the_split_not_before(loader, monkeypatch):
    # 25 newest are about Dell; the 5 relevant ones are older. Cap-before-split would
    # leave zero relevant headlines in the top 10.
    news = [_it(f"Dell Stock Slides Again {i}", ts=1000 - i) for i in range(25)] + \
           [_it(f"Marvell lands design win {i}", ts=500 - i) for i in range(5)]
    assert len(news) == 30
    monkeypatch.setattr(loader, "fetch_ticker_bundle",
                        lambda t, p: _bundle(news, MRVL_INFO))
    out = loader.load_bundle("MRVL")
    assert [i["title"] for i in out["news_raw"]] == [f"Marvell lands design win {i}" for i in range(5)]
    assert out["news_unrelated_n"] == 25 and len(out["news_unrelated"]) == 10
    assert set(out["news_unrelated"][0]) == {"title", "publisher", "ts", "url"}
    assert out["news_relevance_checked"] is True
    assert len(out["headlines"]) == 5
    assert all("Dell" not in h["headline"] for h in out["headlines"])
    assert not any("Dell" in i["title"] for i in out["news_raw"])


def test_s_score_reads_kept_headlines_only(loader, monkeypatch):
    from stock_analyzer.sentiment import analyze_news, sentiment_score_0_100
    good = [_it("Marvell soars on record profit and strong growth")]
    bad = [_it("Dell stock crashes, terrible awful disaster")]
    monkeypatch.setattr(loader, "fetch_ticker_bundle",
                        lambda t, p: _bundle(bad * 5 + good, MRVL_INFO))
    out = loader.load_bundle("MRVL")
    avg, _ = analyze_news(good)
    assert out["avg_sent"] == avg and out["s_score"] == sentiment_score_0_100(avg)


def test_news_names_from_the_search_response_are_used(loader, monkeypatch):
    news = [_it("Marvell up"), _it("Dell down")]
    monkeypatch.setattr(loader, "fetch_ticker_bundle",
                        lambda t, p: _bundle(news, {"sector": ""}, names=["Marvell Technology Inc"]))
    out = loader.load_bundle("MRVL")
    assert [i["title"] for i in out["news_raw"]] == ["Marvell up"]


def test_no_names_keeps_everything_and_is_unchecked(loader, monkeypatch):
    news = [_it("Dell down"), _it("Marvell up")]
    monkeypatch.setattr(loader, "fetch_ticker_bundle", lambda t, p: _bundle(news, {"sector": ""}))
    out = loader.load_bundle("MRVL")
    assert len(out["news_raw"]) == 2
    assert out["news_relevance_checked"] is False and out["news_unrelated_n"] is None
    assert out["news_unrelated"] == []


def test_unrelated_never_in_news_raw_and_cached_bundle_has_no_count(loader, monkeypatch):
    def _boom(t, p):
        raise RuntimeError("providers down")
    monkeypatch.setattr(loader, "fetch_ticker_bundle", _boom)
    _Db.reset(bundle_cache={"bundle": _bundle([], MRVL_INFO),
                            "fetched_at": "2026-10-15T00:00:00+00:00"})
    out = loader.load_bundle("MRVL")
    assert out["news_status"] == "cached" and out["news_unrelated_n"] is None


def test_news_status_stays_ok_when_nothing_is_relevant(loader, monkeypatch):
    news = [_it("Dell stock falls"), _it("Amgen up")]
    monkeypatch.setattr(loader, "fetch_ticker_bundle",
                        lambda t, p: _bundle(news, MRVL_INFO, status="ok"))
    out = loader.load_bundle("MRVL")
    assert out["news_raw"] == [] and out["news_unrelated_n"] == 2
    assert out["news_status"] == "ok"
    assert nf.feed_state({"MRVL": out})["state"] == "ok"      # not an outage


def test_llm_cache_hit_headlines_are_relevance_filtered(loader, monkeypatch):
    from stock_analyzer import news_intelligence as ni
    row = {"headlines": [{"headline": "Marvell wins", "score": 0.5},
                         {"headline": "Dell collapses", "score": -0.9}],
           "avg_sent": -0.2}
    _Db.reset(cache_row=row)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(ni, "rescore_headlines_llm", lambda *a, **k: pytest.fail("must not rescore"))
    monkeypatch.setattr(loader, "fetch_ticker_bundle",
                        lambda t, p: _bundle([_it("Marvell wins")], MRVL_INFO))
    out = loader.load_bundle("MRVL")
    assert [h["headline"] for h in out["headlines"]] == ["Marvell wins"]
    assert out["avg_sent"] == 0.5


def test_cache_hit_filter_skipped_when_unchecked(loader, monkeypatch):
    from stock_analyzer import news_intelligence as ni
    row = {"headlines": [{"headline": "Dell collapses", "score": -0.9}], "avg_sent": -0.5}
    # stored avg deliberately != mean of its scores (-0.9): an unguarded recompute
    # would change it, so this pins that the unchecked path serves the row as-is.
    _Db.reset(cache_row=row)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(ni, "rescore_headlines_llm", lambda *a, **k: pytest.fail("must not rescore"))
    monkeypatch.setattr(loader, "fetch_ticker_bundle",
                        lambda t, p: _bundle([_it("Dell collapses")], {"sector": ""}))
    out = loader.load_bundle("MRVL")
    assert out["headlines"] == row["headlines"] and out["avg_sent"] == -0.5


# ── check_news wording ───────────────────────────────────────────────────────
def _ctx(news=None, status="ok", n=None, who="MRVL"):
    b = {"news_status": status}
    if n is not None:
        b["news_unrelated_n"] = n
    return {"news": news, "bundle": b, "ticker": who}


def test_check_news_unrelated_wording_all_states():
    one = ee.check_news(_ctx(n=1))
    assert one == {"kind": "unchecked", "check": "news", "why":
                   "no headlines naming MRVL — 1 headline tagged to MRVL didn't name it "
                   "in the title and wasn't counted"}
    many = ee.check_news(_ctx(n=4))
    assert many["why"] == ("no headlines naming MRVL — 4 headlines tagged to MRVL didn't name it "
                           "in the title and weren't counted")
    # precedence: error / cached / none outrank the unrelated branch
    assert "couldn't be read" in ee.check_news(_ctx(status="error", n=3))["why"]
    assert "couldn't be read" in ee.check_news(_ctx(status="cached", n=3))["why"]
    assert ee.check_news(_ctx(status="none", n=3))["why"] == "no headlines tagged to MRVL"
    # zero / None / bool -> the generic line
    for n in (0, None, True):
        assert ee.check_news(_ctx(n=n))["why"] == "no recent headlines to check"
    # passed
    p = ee.check_news(_ctx(news={"compound": 0.1, "n": 3}, n=2))
    assert p["kind"] == "passed" and p["detail"].endswith(" · 2 that didn't name MRVL not counted")
    base = ee.check_news(_ctx(news={"compound": 0.1, "n": 3}))
    assert p["detail"].startswith(base["detail"]) and "didn't name" not in base["detail"]
    # a real negative still holds
    assert ee.check_news(_ctx(news={"compound": -0.6, "n": 2}, n=5))["kind"] == "hold"


def test_quiet_never_appears():
    states = [_ctx(n=n, status=s) for n in (None, 0, 1, 7) for s in ("ok", "none", "error", "cached")]
    states.append(_ctx(news={"compound": 0.1, "n": 3}, n=2))
    for c in states:
        r = ee.check_news(c)
        assert "quiet" not in " ".join(str(v) for v in r.values()).lower()


# ── held-news integration: MRVL's only negative headline is the Dell story ───
def test_held_news_warning_not_raised_off_an_unrelated_headline(loader, monkeypatch):
    dell = _it("Dell Stock Closes Its Worst Week in Years", ts=1_800_000_000)
    mrvl_ok = _it("Marvell Technology Announces New Custom Silicon Partnership", ts=1_799_999_000)

    def run(info):
        monkeypatch.setattr(loader, "fetch_ticker_bundle",
                            lambda t, p: _bundle([dell, mrvl_ok], info))
        res = loader.load_bundle("MRVL")
        items = curate_news_items({"MRVL": res}, max_items=None)
        return warning_news_by_ticker(items, {"MRVL"}, 0, exclude=set())

    assert run({"sector": ""}).keys() == {"MRVL"}, "control: unfiltered, Dell story warns"
    assert run(MRVL_INFO) == {}


# ── review round: SpaceX, lost short forms, alias/stoplist, app.py pins ──────
SPCX_NAMES = ["Space Exploration Technologies Corp.", "Space Exploration"]


@pytest.mark.parametrize("title", [
    "SpaceX Is Changing: Here's What You Need to Know",
    "SpaceX Stocks Fall 1.94% Despite Evercore's $275 Price Target",
    "SpaceX's Mobile Ambition Comes With an $80 Billion Catch",
    "35 Analysts Cover SpaceX",
])
def test_spcx_live_titles_kept(title):
    assert rel(title, "SPCX", SPCX_NAMES) is True


def test_spcx_generic_space_is_dropped():
    assert rel("Stock Market Today: Nasdaq Tanks 2%; Palo Alto Leads Slide In "
               "Cybersecurity Space", "SPCX", SPCX_NAMES) is False
    assert "Space" not in {t for t, _ in nr.name_terms("SPCX", SPCX_NAMES)}


@pytest.mark.parametrize("ticker,names,title", [
    ("SO", ["Southern Company (The)", "The Southern Company"],
     "Is Southern Company's 3.5% Dividend Yield Worth the Price?"),
    ("SO", ["Southern Company (The)"], "Southern Company vs. AEP"),
    ("DIS", ["Walt Disney Company (The)"],
     "Disney seeks to buy Character AI assets in Chapter 11 bankruptcy"),
    ("DIS", ["The Walt Disney Company"], "Are You Overpaying For Disney Stock"),
    ("SCHW", ["Charles Schwab Corporation (The)"], "Schwab Reports Record Client Assets"),
    ("LLY", ["Eli Lilly and Company"], "Lilly's Obesity Drug Wins Approval"),
    ("BMY", ["Bristol-Myers Squibb Company"], "Bristol Myers Cuts Guidance"),
    ("SMCI", ["Super Micro Computer, Inc."], "Supermicro Shares Jump on AI Server Demand"),
    ("OXY", ["Occidental Petroleum Corporation"], "Oxy Raises Dividend"),
    ("MMM", ["3M Company"], "3M Settles Lawsuit"),
    ("JD", ["JD.com, Inc."], "JD.com Posts Strong Singles Day"),
    ("ONON", ["On Holding AG"], "On Holding Raises Outlook"),
])
def test_common_short_forms_kept(ticker, names, title):
    assert rel(title, ticker, names) is True


def test_on_alias_is_multi_token_so_it_does_not_match_the_word_on():
    assert rel("Stocks Rally on Strong Jobs Data", "ONON", ["On Holding AG"]) is False


def test_leading_the_and_trailing_and_stripped():
    assert _t("BA", "The Boeing Company") == {"Boeing"}
    t = _t("XX", "Johnson and")
    assert not any(x.endswith(" and") for x in t)


def test_new_stoplist_words_are_first_token_only():
    t = _t("HD", "Home Depot, Inc.")
    assert "Home Depot" in t and "Home" not in t
    t = _t("MS", "Morgan Stanley")
    assert "Morgan Stanley" in t and "Morgan" not in t


def test_unclosed_parenthetical_and_dash_tail_dropped():
    assert "(REI" not in " ".join(_t("AMT", "American Tower Corporation (REI"))
    assert not any("York" in x for x in _t("ASML", "ASML Holding N.V. - New York Re"))


def test_targeted_fail_open_names_now_have_alias_terms():
    assert nr.split_relevant([_it("Target Corporation misses")], "TGT",
                             ["Target Corporation"])["checked"] is True


def test_app_renders_unrelated_via_st_text_and_discloses_fail_open():
    import pathlib
    app = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    i = app.index('_da_unrel_rows = r.get("news_unrelated")')
    block = app[i:i + 2600]
    assert "st.text(" in block and "for _u in _da_unrel_rows" in block
    loop = block[block.index("for _u in _da_unrel_rows"):][:200]
    assert "st.markdown" not in loop and "unsafe_allow_html" not in loop
    assert 'r.get("news_relevance_checked") is False' in block
    assert "Couldn't check which headlines name" in block
    assert "didn't name {ticker} in the title" not in block      # wording sanity
    assert "didn't name it in the title and aren't counted" in block
    assert "of {_da_unrel_n} shown" in block


# ── round 3: curated aliases bypass the stoplist and are case-sensitive ──────
@pytest.mark.parametrize("ticker,names,title", [
    ("TGT", ["Target Corporation"], "Has Target Stock Become A Different Bet?"),
    ("TGT", ["Target Corporation"],
     "Should You Buy, Hold or Sell Target Stock at its Current Valuation?"),
    ("TGT", ["Target Corporation"], "Walmart, Target face a new price problem as costs surge"),
    ("XYZ", ["Block, Inc."], "FTC investigating Block over frozen Square, Cash App accounts"),
    ("XYZ", ["Block, Inc."], "Block Stock Trades More Than 10% Below Its 52-Week High"),
    ("U", ["Unity Software Inc."], "Unity stock jumps 5% on Google AI gaming partnership"),
    ("U", ["Unity Software Inc."],
     "Unity Announces Date of Third Quarter 2026 Financial Results Webcast"),
    ("PANW", ["Palo Alto Networks, Inc."],
     "Palo Alto Falls 4% as Report of OpenAI Cybersecurity Deal Hits Sector"),
    ("PANW", ["Palo Alto Networks, Inc."],
     "Sell-Off In Cybersecurity Stocks Intensifies Amid Palo Alto Vulnerability"),
    ("SE", ["Sea Limited"],
     "DBS cuts Sea target as Shopee spending weighs on profit growth"),
    ("SO", ["Southern Company (The)"],
     "KeyBanc Adjusts Price Target on Southern to $74, Keeps Underweight"),
    ("MPC", ["Marathon Petroleum Corporation"], "Marathon vs. Delek US: Which Refining Stock"),
    ("WBD", ["Warner Bros. Discovery, Inc."], "Warner Bros Posts Strong Box Office"),
    ("USB", ["U.S. Bancorp"], "US Bancorp Raises Dividend"),
])
def test_live_titles_kept_with_curated_aliases(ticker, names, title):
    assert rel(title, ticker, names) is True


@pytest.mark.parametrize("ticker,names,title", [
    ("TGT", ["Target Corporation"], "Analysts raise price target on Apple"),
    ("U", ["Unity Software Inc."], "Shareholders call for unity"),
    ("SE", ["Sea Limited"], "Deep sea mining stocks rally"),
    ("XYZ", ["Block, Inc."], "Roads block traffic for hours"),
])
def test_curated_single_token_alias_is_case_sensitive(ticker, names, title):
    assert rel(title, ticker, names) is False


def test_known_false_keep_from_a_capitalised_alias_is_deliberate():
    # Accepted trade-off (no worse than fail-open): a capitalised "Block"/"Target"
    # in an unrelated title still counts.
    assert rel("Adjusts Price Target on Block", "TGT", ["Target Corporation"]) is True


def test_curated_aliases_skip_the_stoplist_but_derived_terms_do_not():
    terms = dict(nr.name_terms("TGT", ["Target Corporation"]))
    assert terms == {"Target": True}
    assert "Target" not in {t for t, _ in nr.name_terms("ZZZ", ["Target Corporation"])}


def test_truncated_junk_in_names_gives_clean_cores():
    assert _t("BKNG", "Booking Holdings Inc. Common St") == {"Booking"}
    assert _t("PNC", "PNC Financial Services Group I") == {"PNC Financial Services"}
    assert _t("BKNG", "Booking Holdings Inc. Common Stock") == {"Booking"}


def test_fail_open_caption_wording_pinned():
    import pathlib
    app = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    assert "(no usable company name)" in app and "(no company name" not in app


@pytest.mark.parametrize("ticker,names,title", [
    ("ON", ["ON Semiconductor Corporation"],
     "Onsemi to Deploy EliteSiC Technology at Sungrow's PowerTitan 3.0 Energy Storage Platform"),
    ("ON", ["ON Semiconductor Corporation"], "ONSEMI CUTS GUIDANCE"),
    ("SPCX", SPCX_NAMES, "Spacex Delays IPO"),
    ("META", ["Meta Platforms, Inc."], "facebook outage hits millions"),
])
def test_distinctive_curated_aliases_match_any_case(ticker, names, title):
    assert rel(title, ticker, names) is True
