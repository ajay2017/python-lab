"""FMP is the earnings-calendar provider only (owner decision 2026-10-27).

FMP's free plan serves just a sample symbol list, so it is out of both failover
orders; the market-wide calendar has its own order."""
import pytest

from stock_analyzer import constants as C
from stock_analyzer.providers import orchestrator as orch
from stock_analyzer.providers.base import (
    DataProvider, ProviderUnavailable, CAP_LIVE_PRICE, CAP_HISTORY,
)


@pytest.fixture(autouse=True)
def _fresh_chains():
    orch.reset()
    yield
    orch.reset()


def test_neither_failover_order_contains_fmp():
    assert "fmp" not in C.DATA_PROVIDER_ORDER
    assert "fmp" not in C.DATA_LIVE_PRICE_ORDER
    assert C.DATA_EARNINGS_CALENDAR_ORDER == ["fmp"]


def _registry(monkeypatch, built):
    class _Yahoo(DataProvider):
        name = "yahoo_finance"
        capabilities = frozenset({CAP_LIVE_PRICE, CAP_HISTORY})

        def is_configured(self):
            return True

        def live_prices(self, tickers):
            raise ProviderUnavailable("yahoo down")

        def price_history(self, ticker, period="6mo"):
            raise ProviderUnavailable("yahoo down")

    class _Finnhub(_Yahoo):
        name = "finnhub"
        capabilities = frozenset({CAP_LIVE_PRICE})

    class _Fmp(DataProvider):
        name = "fmp"
        capabilities = frozenset({CAP_LIVE_PRICE, CAP_HISTORY})

        def __init__(self):
            built.append("fmp")

        def is_configured(self):
            return True

        def live_prices(self, tickers):
            built.append("fmp.live_prices")
            return {}

        def price_history(self, ticker, period="6mo"):
            built.append("fmp.price_history")
            raise ProviderUnavailable("fmp")

    monkeypatch.setattr(orch, "PROVIDER_REGISTRY",
                        {"yahoo_finance": _Yahoo, "finnhub": _Finnhub, "fmp": _Fmp})


def test_failover_with_yahoo_failing_never_touches_fmp(monkeypatch):
    built: list = []
    _registry(monkeypatch, built)
    assert orch.get_live_prices(["A", "B"]) == {}
    with pytest.raises(ProviderUnavailable):
        orch.get_history("A")
    assert orch.crosscheck_batch(["A"]) == {}
    assert orch.crosscheck_price("A", 10.0, 9.0) is None
    assert built == []


def test_earnings_calendar_uses_its_own_order(monkeypatch):
    built: list = []

    class _Cal(DataProvider):
        name = "cal"

        def __init__(self):
            built.append("cal")

        def is_configured(self):
            return True

        def earnings_calendar(self, a, b):
            return [{"symbol": "X"}]

    class _Other(_Cal):
        name = "other"

        def earnings_calendar(self, a, b):
            raise AssertionError("general chain must not serve the calendar")

    monkeypatch.setattr(orch, "PROVIDER_REGISTRY", {"cal": _Cal, "other": _Other})
    monkeypatch.setattr(C, "DATA_PROVIDER_ORDER", ["other"])
    monkeypatch.setattr(C, "DATA_EARNINGS_CALENDAR_ORDER", ["cal"])
    assert orch.get_earnings_calendar("2026-10-27", "2026-11-03") == [{"symbol": "X"}]


def test_earnings_calendar_returns_empty_on_error_or_no_provider(monkeypatch):
    class _Boom(DataProvider):
        name = "cal"

        def is_configured(self):
            return True

        def earnings_calendar(self, a, b):
            raise RuntimeError("boom")

    monkeypatch.setattr(orch, "PROVIDER_REGISTRY", {"cal": _Boom})
    monkeypatch.setattr(C, "DATA_EARNINGS_CALENDAR_ORDER", ["cal"])
    assert orch.get_earnings_calendar("a", "b") == []
    orch.reset()
    monkeypatch.setattr(C, "DATA_EARNINGS_CALENDAR_ORDER", ["missing"])
    assert orch.get_earnings_calendar("a", "b") == []


# ── Profile-only .info refill (DATA_INFO_BACKFILL_ORDER) ─────────────────────
import pandas as pd

from stock_analyzer.providers.base import CAP_BUNDLE


def _bundle_registry(monkeypatch, primary_info, calls, fmp_info=None):
    hist = pd.DataFrame({"Close": [1.0, 2.0]})

    class _Yahoo(DataProvider):
        name = "yahoo_finance"
        capabilities = frozenset({CAP_BUNDLE})

        def is_configured(self):
            return True

        def bundle(self, ticker, period="6mo"):
            return {"history": hist, "info": primary_info, "news": [],
                    "earnings": "2026-11-01", "revisions": {"x": 1}}

    class _Fmp(DataProvider):
        name = "fmp"
        capabilities = frozenset({CAP_BUNDLE})

        def is_configured(self):
            return True

        def info(self, ticker):
            calls.append("fmp.info")
            return fmp_info if fmp_info is not None else {
                "sector": "Technology", "marketCap": 5e9}

        def earnings(self, ticker):
            calls.append("fmp.earnings")
            return "2026-12-01"

        def revisions(self, ticker):
            calls.append("fmp.revisions")
            return {"y": 2}

    monkeypatch.setattr(orch, "PROVIDER_REGISTRY",
                        {"yahoo_finance": _Yahoo, "fmp": _Fmp})
    monkeypatch.setattr(orch._ah, "in_cooldown", lambda *a, **k: False)
    monkeypatch.setattr(orch._ah, "get_fmp_daily_quota", lambda: 0)
    monkeypatch.setattr(orch._ah, "record", lambda *a, **k: None)


def test_sparse_info_is_refilled_from_fmp_profile_with_sector(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {}, calls)
    b = orch.get_bundle("ABC")
    assert calls == ["fmp.info"]
    assert b["info"]["sector"] == "Technology"
    assert b["_info_source"] == "fmp"


def test_refill_merges_keeping_sparse_primary_core_metrics(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {"earningsGrowth": 0.2, "debtToEquity": 55.0}, calls)
    b = orch.get_bundle("ABC")
    assert calls == ["fmp.info"]
    assert b["info"]["earningsGrowth"] == 0.2 and b["info"]["debtToEquity"] == 55.0
    assert b["info"]["marketCap"] == 5e9 and b["info"]["sector"] == "Technology"
    assert b["_info_source"] == "fmp"


def test_refill_merge_primary_value_wins_on_shared_key(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {"sector": "Energy", "forwardPE": 12.0}, calls)
    b = orch.get_bundle("ABC")
    assert b["info"]["sector"] == "Energy"
    assert b["info"]["forwardPE"] == 12.0 and b["info"]["marketCap"] == 5e9


def test_refill_merge_fills_key_that_is_none_in_primary(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {"sector": None, "earningsGrowth": 0.1}, calls)
    b = orch.get_bundle("ABC")
    assert b["info"]["sector"] == "Technology" and b["info"]["earningsGrowth"] == 0.1


def test_sparse_refill_leaves_primary_info_untouched(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {"earningsGrowth": 0.2}, calls, fmp_info={"beta": 1.1})
    b = orch.get_bundle("ABC")
    assert calls == ["fmp.info"]
    assert b["info"] == {"earningsGrowth": 0.2} and "_info_source" not in b


def test_refill_blank_string_and_whitespace_sector_are_missing(monkeypatch):
    for blank in ("", "   "):
        calls: list = []
        _bundle_registry(monkeypatch, {"sector": blank, "earningsGrowth": 0.1}, calls)
        b = orch.get_bundle("ABC")
        assert b["info"]["sector"] == "Technology" and b["info"]["earningsGrowth"] == 0.1


def test_refill_merge_keeps_zero_and_replaces_nan(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {"debtToEquity": 0.0, "beta": float("nan")}, calls,
                     fmp_info={"beta": 1.2, "marketCap": 5e9})
    b = orch.get_bundle("ABC")
    assert b["info"]["debtToEquity"] == 0.0 and b["info"]["beta"] == 1.2


def test_refill_merge_keeps_etf_identity_keys(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {"quoteType": "ETF", "netExpenseRatio": 0.03}, calls,
                     fmp_info={"marketCap": 5e9})
    b = orch.get_bundle("ABC")
    assert b["info"]["quoteType"] == "ETF" and b["info"]["netExpenseRatio"] == 0.03
    assert b["info"]["marketCap"] == 5e9


def test_refill_merge_does_not_alias_refill_dict_and_records_filled_keys(monkeypatch):
    calls: list = []
    fmp_info = {"sector": "Technology", "marketCap": 5e9, "beta": 1.2}
    _bundle_registry(monkeypatch, {"sector": "Energy", "earningsGrowth": 0.1}, calls,
                     fmp_info=fmp_info)
    b = orch.get_bundle("ABC")
    assert b["info"] is not fmp_info
    assert fmp_info == {"sector": "Technology", "marketCap": 5e9, "beta": 1.2}
    assert b["_info_filled_keys"] == ["beta", "marketCap"]


def test_pt_label_uses_primary_source_unless_refill_supplied_target(monkeypatch):
    from stock_analyzer import bundle_loader
    from tests.test_bundle_loader_asset_type import _fake_bundle, _NoopDb
    seen: list = []
    real = bundle_loader.choose_pt_target

    def spy(*a, **k):
        seen.append(a[2])
        return real(*a, **k)

    monkeypatch.setattr(bundle_loader, "db", _NoopDb)
    monkeypatch.setattr(bundle_loader, "choose_pt_target", spy)
    for filled, expect in ((["marketCap"], "yahoo_finance"), (["targetMeanPrice"], "fmp")):
        b = _fake_bundle({"targetMeanPrice": 100.0})
        b.update({"_source": "yahoo_finance", "_info_source": "fmp",
                  "_info_filled_keys": filled})
        monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle", lambda t, p, b=b: b)
        bundle_loader.load_bundle("TESTX")
        assert seen[-1] == expect


def test_non_sparse_info_makes_no_backfill_call(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {"marketCap": 1e9, "sector": "Energy"}, calls)
    b = orch.get_bundle("ABC")
    assert calls == []
    assert b["info"]["sector"] == "Energy" and "_info_source" not in b


def test_fmp_in_cooldown_gets_no_backfill_call(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {}, calls)
    monkeypatch.setattr(orch._ah, "in_cooldown", lambda name, *a, **k: name == "fmp")
    b = orch.get_bundle("ABC")
    assert calls == [] and b["info"] == {}


def test_fmp_over_soft_cap_gets_no_backfill_call(monkeypatch):
    calls: list = []
    _bundle_registry(monkeypatch, {}, calls)
    monkeypatch.setattr(orch._ah, "get_fmp_daily_quota",
                        lambda: C.FMP_DAILY_SOFT_CAP)
    b = orch.get_bundle("ABC")
    assert calls == [] and b["info"] == {}


def test_earnings_and_revisions_backfill_never_reaches_fmp(monkeypatch):
    calls: list = []
    hist = pd.DataFrame({"Close": [1.0, 2.0]})
    _bundle_registry(monkeypatch, {"marketCap": 1e9}, calls)

    class _YahooNoDates(orch.PROVIDER_REGISTRY["yahoo_finance"]):
        def bundle(self, ticker, period="6mo"):
            return {"history": hist, "info": {"marketCap": 1e9}, "news": [],
                    "earnings": None, "revisions": {}}
    monkeypatch.setattr(orch, "PROVIDER_REGISTRY",
                        {**orch.PROVIDER_REGISTRY, "yahoo_finance": _YahooNoDates})
    orch.reset()
    orch.get_bundle("ABC")
    assert calls == []


def test_build_info_makes_exactly_one_request_profile(monkeypatch):
    from stock_analyzer.providers import fmp_provider as fp
    urls: list = []

    def _fake_get(url, params=None, **kw):
        urls.append(url.rsplit("/", 1)[-1])
        return [{"companyName": "X", "sector": "Technology", "marketCap": 1e9}]

    monkeypatch.setattr(fp, "http_get_json", _fake_get)
    monkeypatch.setattr(fp._db, "increment_daily_quota", lambda *a, **k: None)
    monkeypatch.setattr(fp._ah, "record", lambda *a, **k: None)
    prov = fp.FMPProvider.__new__(fp.FMPProvider)
    prov._key = "k"
    info = prov._build_info("ABC")
    assert urls == ["profile"]
    assert info["sector"] == "Technology" and info["marketCap"] == 1e9
