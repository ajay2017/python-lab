"""entry-fail-closed C1 (docs/plans/entry-fail-closed.md §2 "Earnings plumbing", §7):
`earnings_status` flows provider -> orchestrator fallback -> load_bundle, with NO
behaviour change at C1 (nothing holds a buy on it yet). Since buy-check C3a the
bundle's `earnings` value is the resolved next date (`earnings_date.resolve`), so a
past Yahoo date no longer survives as `earnings`.

No network: yf.Ticker is faked and db.* is stubbed."""
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import bundle_loader
from stock_analyzer import buy_check_state as bcs
from stock_analyzer.providers import orchestrator as orch
from stock_analyzer.providers import yfinance_provider as yp
from stock_analyzer.providers.base import CAP_BUNDLE, DataProvider

pytestmark = pytest.mark.fast


# ── provider mapping (yfinance_provider.bundle) ──────────────────────────────

class _Raises:
    """A `calendar` attribute that raises on access."""


def _fake_ticker(cal):
    class _T:
        def __init__(self, t):
            pass

        def history(self, period="6mo"):
            idx = pd.date_range("2024-01-01", periods=30, freq="D")
            c = [100.0 + i for i in range(30)]
            return pd.DataFrame({"Close": c, "High": [x + 1 for x in c],
                                 "Low": [x - 1 for x in c], "Open": c,
                                 "Volume": [1_000_000.0] * 30}, index=idx)

        info = {"sector": "Technology", "longBusinessSummary": "x"}
        upgrades_downgrades = None

        @property
        def calendar(self):
            if cal is _Raises:
                raise RuntimeError("calendar endpoint down")
            return cal
    return _T


def _bundle_for(monkeypatch, cal):
    monkeypatch.setattr(yp.yf, "Ticker", _fake_ticker(cal))
    monkeypatch.setattr(yp.YFinanceProvider, "search_news_with_names",
                        lambda self, t, keep=None: ([], "ok", []))
    return yp.YFinanceProvider().bundle("ACME")


@pytest.mark.parametrize("cal, earnings, status", [
    # a dated stock (P0 probe: AAPL / MSFT)
    ({"Earnings Date": ["2026-12-02"]}, "2026-12-02", "date"),
    # the lowerCamel key the provider has always accepted
    ({"earningsDate": ["2026-12-02"]}, "2026-12-02", "date"),
    # a timestamped first date keeps its first 10 characters, as today
    ({"Earnings Date": ["2026-12-02 16:00:00"]}, "2026-12-02", "date"),
    # a recent IPO (P0 probe: ANT): the key holds [] -> Yahoo answered, nothing announced
    ({"Earnings Date": []}, None, "no_date"),
    ({"Earnings Date": None}, None, "no_date"),
    ({"earningsDate": []}, None, "no_date"),
    # an ETF / bogus symbol (P0 probe: VOO / SPY / ZZZZQX): a bare {} -> failed
    # (earnings_state turns an ETF's into not_applicable from asset_type)
    ({}, None, "failed"),
    ({"Dividend Date": "2026-12-01"}, None, "failed"),
    # not a dict
    (None, None, "failed"),
    ([], None, "failed"),
    ("n/a", None, "failed"),
    # unparseable first date: the old `earnings` value is kept, the status is failed
    ({"Earnings Date": ["soon"]}, "soon", "failed"),
    ({"Earnings Date": [None]}, "None", "failed"),
    # a raise inside the read (a scalar, not a list) -> earnings None, failed
    ({"Earnings Date": 5}, None, "failed"),
    # the calendar endpoint raising
    (_Raises, None, "failed"),
])
def test_provider_maps_the_calendar_shape_and_keeps_earnings_as_today(
        monkeypatch, cal, earnings, status):
    b = _bundle_for(monkeypatch, cal)
    assert b["earnings"] == earnings
    assert b["earnings_status"] == status


def test_provider_keeps_the_first_of_two_keys_exactly_as_before(monkeypatch):
    # `.get("Earnings Date") or .get("earningsDate")`: an empty first key falls to the second.
    b = _bundle_for(monkeypatch, {"Earnings Date": [], "earningsDate": ["2026-12-09"]})
    assert b["earnings"] == "2026-12-09" and b["earnings_status"] == "date"


def test_provider_status_feeds_earnings_state(monkeypatch):
    b = _bundle_for(monkeypatch, {"Earnings Date": []})
    assert bcs.earnings_state(b)["state"] == "no_date"
    b = _bundle_for(monkeypatch, {})
    assert bcs.earnings_state(b)["state"] == "failed"
    b = _bundle_for(monkeypatch, {"Earnings Date": ["2026-12-02"]})
    assert bcs.earnings_state(b) == {"state": "date", "date": "2026-12-02"}


# ── orchestrator fallback (get_bundle) ───────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_chains():
    orch.reset()
    yield
    orch.reset()


def _fallback_registry(monkeypatch, primary_bundle, fallback_earnings):
    hist = pd.DataFrame({"Close": [1.0, 2.0]})

    class _Yahoo(DataProvider):
        name = "yahoo_finance"
        capabilities = frozenset({CAP_BUNDLE})

        def is_configured(self):
            return True

        def bundle(self, ticker, period="6mo"):
            return dict({"history": hist, "info": {"marketCap": 1e9}, "news": [],
                         "revisions": {"x": 1}}, **primary_bundle)

    class _Other(DataProvider):
        name = "finnhub"
        capabilities = frozenset({CAP_BUNDLE})

        def is_configured(self):
            return True

        def earnings(self, ticker):
            if isinstance(fallback_earnings, Exception):
                raise fallback_earnings
            return fallback_earnings

    monkeypatch.setattr(orch, "PROVIDER_REGISTRY",
                        {"yahoo_finance": _Yahoo, "finnhub": _Other})
    monkeypatch.setattr(orch._ah, "in_cooldown", lambda *a, **k: False)
    monkeypatch.setattr(orch._ah, "get_fmp_daily_quota", lambda: 0)
    monkeypatch.setattr(orch._ah, "record", lambda *a, **k: None)
    orch.reset()


@pytest.mark.parametrize("primary_status", ["no_date", "failed"])
def test_fallback_that_returns_a_date_sets_status_date(monkeypatch, primary_status):
    _fallback_registry(monkeypatch, {"earnings": None, "earnings_status": primary_status},
                       "2026-12-01")
    b = orch.get_bundle("ABC")
    assert b["earnings"] == "2026-12-01"
    assert b["earnings_status"] == "date"


def test_fallback_that_raises_keeps_the_primary_status(monkeypatch):
    _fallback_registry(monkeypatch, {"earnings": None, "earnings_status": "failed"},
                       RuntimeError("fallback down"))
    b = orch.get_bundle("ABC")
    assert b["earnings"] is None and b["earnings_status"] == "failed"


def test_fallback_that_returns_nothing_keeps_the_primary_status(monkeypatch):
    _fallback_registry(monkeypatch, {"earnings": None, "earnings_status": "no_date"}, None)
    b = orch.get_bundle("ABC")
    assert b["earnings"] is None and b["earnings_status"] == "no_date"


def test_fallback_is_not_consulted_when_the_primary_has_a_date(monkeypatch):
    _fallback_registry(monkeypatch, {"earnings": "2026-12-02", "earnings_status": "date"},
                       "2099-01-01")
    b = orch.get_bundle("ABC")
    assert b["earnings"] == "2026-12-02" and b["earnings_status"] == "date"


def test_primary_without_status_support_leaves_the_key_absent(monkeypatch):
    _fallback_registry(monkeypatch, {"earnings": None}, "2026-12-01")
    b = orch.get_bundle("ABC")
    assert b["earnings"] == "2026-12-01"          # the existing fill is unchanged
    assert "earnings_status" not in b             # and no status is invented


# ── producer pin: load_bundle always stamps earnings_status ──────────────────

def _price_df(n=60):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = [100 + i * 0.3 for i in range(n)]
    return pd.DataFrame({"Close": c, "High": [x + 1 for x in c],
                         "Low": [x - 1 for x in c], "Open": c,
                         "Volume": [1_000_000.0] * n}, index=idx)


def _db(bundle_cache=None):
    class _Db:
        save_bundle_cache = staticmethod(lambda *a, **k: None)
        load_bundle_cache = staticmethod(lambda *a, **k: bundle_cache)
        save_fundamentals_cache = staticmethod(lambda *a, **k: None)
        load_fundamentals_cache = staticmethod(lambda *a, **k: None)
        load_analyst_coverage = staticmethod(lambda *a, **k: None)
        load_analyst_coverage_or_none = staticmethod(lambda *a, **k: None)
        save_sector_cache = staticmethod(lambda *a, **k: None)
        load_sector_cache = staticmethod(lambda *a, **k: "")
        save_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
        load_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
        load_sentiment_llm_cache = staticmethod(lambda *a, **k: None)
        save_sentiment_llm_cache = staticmethod(lambda *a, **k: None)
    return _Db


def _fetch_with(**extra):
    def _f(ticker, period):
        return dict({"history": _price_df(), "info": {"sector": "Technology"}, "news": [],
                     "earnings": None, "revisions": {}, "_info_source": "t"}, **extra)
    return _f


@pytest.mark.parametrize("extra, earnings, status", [
    ({"earnings": "2026-12-02", "earnings_status": "date"}, "2026-12-02", "date"),
    ({"earnings": None, "earnings_status": "no_date"}, None, "no_date"),
    ({"earnings": None, "earnings_status": "failed"}, None, "failed"),
    # a provider without status support: the key is still stamped (None = couldn't tell)
    ({"earnings": None}, None, None),
])
def test_load_bundle_passes_the_status_through(monkeypatch, extra, earnings, status):
    # buy-check C3a: `earnings` is now the RESOLVED next date (never a past one), so
    # "today" is pinned: 2026-12-02 is a future date here, whenever the suite runs.
    monkeypatch.setattr(bundle_loader, "today_et", lambda: date(2026, 11, 27))
    monkeypatch.setattr(bundle_loader, "db", _db())
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle", _fetch_with(**extra))
    out = bundle_loader.load_bundle("ACME")
    assert "earnings_status" in out
    assert out["earnings"] == earnings and out["earnings_status"] == status


def test_load_bundle_stamps_failed_when_the_calendar_raised_end_to_end(monkeypatch):
    """Provider (calendar raises) -> load_bundle: the key is present and 'failed',
    and earnings_state reads failed for a stock."""
    monkeypatch.setattr(bundle_loader, "db", _db())
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                        lambda t, p: _bundle_for(monkeypatch, _Raises))
    out = bundle_loader.load_bundle("ACME")
    assert out["earnings_status"] == "failed" and out["earnings"] is None
    assert bcs.earnings_state(out)["state"] == "failed"


def test_load_bundle_supabase_cache_fallback_forces_failed(monkeypatch):
    # The cached copy carries no earnings (db.load_bundle_cache returns {}); even a
    # stored "date" status must not survive: a last-known-good copy proves nothing.
    cached = {"bundle": {"history": _price_df(), "info": {"sector": "Technology"},
                         "news": [], "earnings": {}, "revisions": {},
                         "earnings_status": "date"},
              "fetched_at": "2026-11-20"}
    monkeypatch.setattr(bundle_loader, "db", _db(bundle_cache=cached))

    def _boom(t, p):
        raise RuntimeError("providers down")
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle", _boom)
    out = bundle_loader.load_bundle("ACME")
    assert out["stale_as_of"] == "2026-11-20"
    assert out["earnings_status"] == "failed"
    # C3a: the resolver names the reason (a saved copy), and `earnings` is never carried
    assert bcs.earnings_state(out) == {"state": "failed", "date": None,
                                       "reason": "stale_copy"}
    assert out["earnings"] is None


def test_load_bundle_has_exactly_one_bundle_return_that_stamps_the_key():
    """Producer pin, source side: every `return {` that builds the bundle dict in
    load_bundle carries the key (there is one; a second path must stamp it too)."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "stock_analyzer"
           / "bundle_loader.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "load_bundle")
    dict_returns = [n for n in ast.walk(fn)
                    if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
    assert dict_returns, "load_bundle no longer returns a dict literal"
    for r in dict_returns:
        keys = [k.value for k in r.value.keys if isinstance(k, ast.Constant)]
        assert "earnings_status" in keys
