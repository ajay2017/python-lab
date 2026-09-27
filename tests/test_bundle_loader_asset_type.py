"""stock_analyzer/bundle_loader.py::load_bundle() — Phase 1 ETF support
(F-279 §11) additive observability keys (quote_type/asset_type/etf_facts).

Mocks every I/O boundary (fetch_ticker_bundle + all db.* calls) so this runs
pure-pandas, no network/Supabase — same convention as
tests/test_headless_alert_engine.py's load_bundle mocking, just one level
deeper (this IS load_bundle's own internals, not a caller that mocks it away).

Two things this file must prove:
  1. A bundle whose .info lacks quoteType entirely -> asset_type == "stock",
     etf_facts is None (the fail-safe default — Phase 1 does not change what
     an ordinary stock bundle looks like).
  2. The scoring pipeline (total/rec/bq_available/val_available) is BYTE-
     IDENTICAL to independently recomputing the same public functions
     (technical_score/business_quality_score/valuation_score/combined_score/
     recommendation) on the same inputs — pinning that this purely-additive
     change did not perturb the existing return dict.
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
