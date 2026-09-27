"""stock_analyzer/bundle_loader.py::load_bundle() — Phase 1 ETF support
(F-279 §11) additive observability keys (quote_type/asset_type/etf_facts),
plus Phase 2 (ETF-support etf_scoring wiring) additive scoring keys
(etf_available/etf_cost_score/etf_total/etf_rec/etf_aum_thin), plus Phase 3a
(sector look-through) additive key (etf_lookthrough).

Mocks every I/O boundary (fetch_ticker_bundle + all db.* calls) so this runs
pure-pandas, no network/Supabase — same convention as
tests/test_headless_alert_engine.py's load_bundle mocking, just one level
deeper (this IS load_bundle's own internals, not a caller that mocks it away).

Things this file must prove:
  1. A bundle whose .info lacks quoteType entirely -> asset_type == "stock",
     etf_facts is None (the fail-safe default — Phase 1 does not change what
     an ordinary stock bundle looks like).
  2. The scoring pipeline (total/rec/bq_available/val_available) is BYTE-
     IDENTICAL to independently recomputing the same public functions
     (technical_score/business_quality_score/valuation_score/combined_score/
     recommendation) on the same inputs — pinning that this purely-additive
     change did not perturb the existing return dict.
  3. (Phase 2) A stock bundle's 5 new etf_* keys are all None/False —
     "not applicable", never fabricated.
  4. (Phase 2) An ETF bundle with a known net_expense_ratio gets a real
     etf_total/etf_rec surfaced through the bundle.
  5. (Phase 2) An ETF bundle WITHOUT a known net_expense_ratio (etf_available
     False) gets etf_total/etf_rec == None, same as a stock.
"""
import pandas as pd
import pytest

from stock_analyzer import bundle_loader
from stock_analyzer import asset_type as asset_type_mod
from stock_analyzer.technicals import compute_indicators, technical_score
from stock_analyzer.fundamentals import business_quality_score, count_core_metrics
from stock_analyzer.valuation import valuation_score
from stock_analyzer.sentiment import analyze_news, sentiment_score_0_100
from stock_analyzer.scoring import combined_score, recommendation
from stock_analyzer.constants import FUNDAMENTALS_GATE_MIN_METRICS

pytestmark = pytest.mark.fast


def _price_df(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    closes = [100 + i * 0.3 for i in range(n)]
    return pd.DataFrame({
        "Close":  closes,
        "High":   [c + 1 for c in closes],
        "Low":    [c - 1 for c in closes],
        "Open":   closes,
        "Volume": [1_000_000.0] * n,
    }, index=idx)


class _NoopDb:
    """Stand-in for stock_analyzer.db inside bundle_loader — every call is a
    safe no-op / cache-miss, matching what a no-credentials session sees."""

    @staticmethod
    def save_bundle_cache(*a, **k): pass

    @staticmethod
    def load_bundle_cache(*a, **k): return None

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

    @staticmethod
    def load_sentiment_llm_cache(*a, **k): return None

    @staticmethod
    def save_sentiment_llm_cache(*a, **k): pass


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    monkeypatch.setattr(bundle_loader, "db", _NoopDb)
    yield


def _fake_bundle(info: dict, n: int = 60) -> dict:
    return {
        "history": _price_df(n),
        "info": info,
        "news": [],
        "earnings": {},
        "revisions": {},
        "_info_source": "test",
    }


def test_missing_quote_type_defaults_to_stock_with_no_etf_facts(monkeypatch):
    """No quoteType at all in .info -- the exact shape of an ordinary
    stock's .info from a provider that doesn't expose it, or a partial/
    degraded fetch. Must fail SAFE to "stock", never "etf"."""
    info = {"sector": "Technology"}
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(info))

    out = bundle_loader.load_bundle("TESTX")

    assert out["quote_type"] is None
    assert out["asset_type"] == asset_type_mod.ASSET_TYPE_STOCK
    assert out["etf_facts"] is None
    assert out["etf_lookthrough"] is None


def test_etf_quote_type_classified_and_facts_populated(monkeypatch):
    info = {"quoteType": "ETF", "sector": "", "category": "Large Blend",
            "netExpenseRatio": 0.03, "longName": "Test ETF"}
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(info))

    out = bundle_loader.load_bundle("TESTETF")

    assert out["quote_type"] == "ETF"
    assert out["asset_type"] == asset_type_mod.ASSET_TYPE_ETF
    assert out["etf_facts"] is not None
    assert out["etf_facts"]["category"] == "Large Blend"
    assert out["etf_facts"]["net_expense_ratio"] == 0.03
    assert out["etf_facts"]["name"] == "Test ETF"


def test_stock_bundle_scoring_pipeline_is_unperturbed(monkeypatch):
    """Regression pin (Phase 1 must not touch scoring): total/rec/
    bq_available/val_available match an INDEPENDENT recomputation of the
    same public functions on the same inputs."""
    info = {"sector": ""}  # deliberately no fundamentals fields at all
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(info))

    out = bundle_loader.load_bundle("TESTY")

    df = compute_indicators(_price_df())
    t_score, _ = technical_score(df)
    from stock_analyzer.data import fetch_financials_from_info
    financials = fetch_financials_from_info(info)
    bq_score, _ = business_quality_score(financials, "")
    expected_bq_available = count_core_metrics(financials) >= FUNDAMENTALS_GATE_MIN_METRICS
    val_score, _, expected_val_available = valuation_score(
        financials, {"avg_pt": None, "consensus_label": None, "has_coverage": False},
        out["current_price"], "",
    )
    avg_sent, _ = analyze_news([])
    s_score = sentiment_score_0_100(avg_sent)
    expected_total = combined_score(t_score, bq_score, val_score, s_score)
    expected_rec = recommendation(expected_total)

    assert out["bq_available"] == expected_bq_available
    assert out["val_available"] == expected_val_available
    assert out["total"] == expected_total
    assert out["rec"] == expected_rec
    # Sanity: with zero fundamentals fields, the gate must be closed either way.
    assert expected_bq_available is False


# ── Phase 2 (ETF-support etf_scoring wiring) ─────────────────────────────────

def test_stock_bundle_etf_scoring_keys_are_all_none_or_false(monkeypatch):
    """A stock bundle's 5 new etf_* keys must read as "not applicable" —
    None/False — never a fabricated value."""
    info = {"sector": "Technology"}  # no quoteType -> asset_type "stock"
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(info))

    out = bundle_loader.load_bundle("TESTX")

    assert out["asset_type"] == asset_type_mod.ASSET_TYPE_STOCK
    assert out["etf_available"] is False
    assert out["etf_cost_score"] is None
    assert out["etf_total"] is None
    assert out["etf_rec"] is None
    assert out["etf_aum_thin"] is False
    assert out["etf_lookthrough"] is None


def test_etf_bundle_with_known_expense_ratio_surfaces_real_composite(monkeypatch):
    info = {
        "quoteType": "ETF", "sector": "", "category": "Large Blend",
        "netExpenseRatio": 0.03, "longName": "Test ETF", "totalAssets": 1_000_000_000,
    }
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(info))

    out = bundle_loader.load_bundle("TESTETF")

    assert out["asset_type"] == asset_type_mod.ASSET_TYPE_ETF
    assert out["etf_available"] is True
    assert out["etf_cost_score"] == 100.0  # 0.03% is well below ETF_EXPENSE_RATIO_CHEAP_PCT
    assert out["etf_total"] is not None
    assert out["etf_rec"] is not None
    assert out["etf_rec"]["label"] in ("Strong Buy", "Buy", "Hold", "Sell", "Strong Sell")
    assert out["etf_aum_thin"] is False

    # Cross-check against an independent recomputation of the pure function.
    from stock_analyzer import etf_scoring
    from stock_analyzer.technicals import compute_indicators, technical_score
    df = compute_indicators(_price_df())
    t_score, _ = technical_score(df)
    expected_total = etf_scoring.etf_composite(t_score, 100.0)
    assert out["etf_total"] == expected_total


def test_etf_bundle_without_expense_ratio_stays_unavailable(monkeypatch):
    """An ETF quoteType with NO netExpenseRatio data (etf_available False)
    must behave exactly like a stock for the 4 downstream etf_* keys —
    never fabricate a composite off technicals alone."""
    info = {"quoteType": "ETF", "sector": "", "category": "Large Blend"}
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(info))

    out = bundle_loader.load_bundle("TESTETF2")

    assert out["asset_type"] == asset_type_mod.ASSET_TYPE_ETF
    assert out["etf_available"] is False
    assert out["etf_cost_score"] is None
    assert out["etf_total"] is None
    assert out["etf_rec"] is None


# ── Phase 3a (ETF-support: sector look-through) ──────────────────────────────
# Cache-FIRST resolve/write-through: check the persistent cache before a live
# funds_data fetch, only fetching live on a miss or a stale entry.

def _etf_info(net_expense_ratio=0.03):
    return {"quoteType": "ETF", "sector": "", "category": "Large Blend",
            "netExpenseRatio": net_expense_ratio, "longName": "Test ETF"}


def test_etf_bundle_fresh_cache_hit_uses_cache_not_live_fetch(monkeypatch):
    """A fresh (within ETF_LOOKTHROUGH_CACHE_MAX_AGE_DAYS) cache entry is used
    verbatim -- the live fetch function must NOT be called at all."""
    from datetime import datetime, timezone
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(_etf_info()))

    cached_payload = {"sector_weightings": {"technology": 1.0}, "top_holdings": [],
                       "fetched_at": "2026-09-01T00:00:00+00:00"}
    cached_row = {"payload": cached_payload, "fetched_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(bundle_loader.db, "load_etf_lookthrough_cache", lambda t: cached_row)

    live_calls = []
    def _fake_live_fetch(t):
        live_calls.append(t)
        return {"sector_weightings": {"energy": 1.0}, "top_holdings": [], "fetched_at": "live"}
    monkeypatch.setattr(bundle_loader, "fetch_etf_lookthrough", _fake_live_fetch)

    out = bundle_loader.load_bundle("TESTETF3")

    assert out["etf_lookthrough"] == cached_payload
    assert live_calls == []


def test_etf_bundle_no_cache_live_fetch_success_populates_and_writes_through(monkeypatch):
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(_etf_info()))
    monkeypatch.setattr(bundle_loader.db, "load_etf_lookthrough_cache", lambda t: None)

    live_payload = {"sector_weightings": {"energy": 1.0}, "top_holdings": [], "fetched_at": "live"}
    monkeypatch.setattr(bundle_loader, "fetch_etf_lookthrough", lambda t: live_payload)

    save_calls = []
    monkeypatch.setattr(bundle_loader.db, "save_etf_lookthrough_cache",
                         lambda t, p: save_calls.append((t, p)))

    out = bundle_loader.load_bundle("TESTETF4")

    assert out["etf_lookthrough"] == live_payload
    assert save_calls == [("TESTETF4", live_payload)]


def test_etf_bundle_stale_cache_falls_through_to_live_fetch(monkeypatch):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(_etf_info()))

    stale_ts = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
    stale_row = {"payload": {"sector_weightings": {"technology": 1.0}, "top_holdings": [],
                              "fetched_at": "old"},
                 "fetched_at": stale_ts}
    monkeypatch.setattr(bundle_loader.db, "load_etf_lookthrough_cache", lambda t: stale_row)

    live_calls = []
    live_payload = {"sector_weightings": {"energy": 1.0}, "top_holdings": [], "fetched_at": "live"}
    def _fake_live_fetch(t):
        live_calls.append(t)
        return live_payload
    monkeypatch.setattr(bundle_loader, "fetch_etf_lookthrough", _fake_live_fetch)
    monkeypatch.setattr(bundle_loader.db, "save_etf_lookthrough_cache", lambda t, p: None)

    out = bundle_loader.load_bundle("TESTETF5")

    assert live_calls == ["TESTETF5"]
    assert out["etf_lookthrough"] == live_payload


def test_etf_bundle_live_fetch_failure_and_no_cache_leaves_lookthrough_none(monkeypatch):
    """A live fetch failure (None) with no cache to fall back on must leave
    etf_lookthrough None -- never write-through a failed fetch."""
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle",
                         lambda ticker, period: _fake_bundle(_etf_info()))
    monkeypatch.setattr(bundle_loader.db, "load_etf_lookthrough_cache", lambda t: None)
    monkeypatch.setattr(bundle_loader, "fetch_etf_lookthrough", lambda t: None)

    save_calls = []
    monkeypatch.setattr(bundle_loader.db, "save_etf_lookthrough_cache",
                         lambda t, p: save_calls.append((t, p)))

    out = bundle_loader.load_bundle("TESTETF6")

    assert out["etf_lookthrough"] is None
    assert save_calls == []
