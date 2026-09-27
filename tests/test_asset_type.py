"""Boundary tests for stock_analyzer/asset_type.py — the ONE vocabulary
definition every consumer reads (Phase 1 ETF support, F-279 §11).

The load-bearing invariant across every function here: an unrecognized input
is NEVER classified as "etf" — only an exact, known ETF/fund signal earns
that label. Everything else (None, empty, unknown, malformed) falls back to
"stock", the fail-safe default.
"""
import pytest

from stock_analyzer import asset_type

pytestmark = pytest.mark.fast


# ── from_quote_type ──────────────────────────────────────────────────────────

def test_from_quote_type_etf():
    assert asset_type.from_quote_type("ETF") == asset_type.ASSET_TYPE_ETF


def test_from_quote_type_etf_lowercase():
    assert asset_type.from_quote_type("etf") == asset_type.ASSET_TYPE_ETF


def test_from_quote_type_etf_mixed_case():
    assert asset_type.from_quote_type("Etf") == asset_type.ASSET_TYPE_ETF


def test_from_quote_type_mutualfund():
    assert asset_type.from_quote_type("MUTUALFUND") == asset_type.ASSET_TYPE_ETF


def test_from_quote_type_mutualfund_lowercase():
    assert asset_type.from_quote_type("mutualfund") == asset_type.ASSET_TYPE_ETF


def test_from_quote_type_equity_is_stock():
    assert asset_type.from_quote_type("EQUITY") == asset_type.ASSET_TYPE_STOCK


def test_from_quote_type_none_is_stock():
    assert asset_type.from_quote_type(None) == asset_type.ASSET_TYPE_STOCK


def test_from_quote_type_empty_string_is_stock():
    assert asset_type.from_quote_type("") == asset_type.ASSET_TYPE_STOCK


def test_from_quote_type_unknown_is_stock():
    assert asset_type.from_quote_type("CRYPTOCURRENCY") == asset_type.ASSET_TYPE_STOCK
    assert asset_type.from_quote_type("INDEX") == asset_type.ASSET_TYPE_STOCK
    assert asset_type.from_quote_type("CURRENCY") == asset_type.ASSET_TYPE_STOCK


def test_from_quote_type_whitespace_padded():
    assert asset_type.from_quote_type("  ETF  ") == asset_type.ASSET_TYPE_ETF


# ── from_broker_kind ──────────────────────────────────────────────────────────

def test_from_broker_kind_etf():
    assert asset_type.from_broker_kind("etf") == asset_type.ASSET_TYPE_ETF


def test_from_broker_kind_etf_uppercase():
    assert asset_type.from_broker_kind("ETF") == asset_type.ASSET_TYPE_ETF


def test_from_broker_kind_etf_mixed_case():
    assert asset_type.from_broker_kind("Etf") == asset_type.ASSET_TYPE_ETF


def test_from_broker_kind_stock():
    assert asset_type.from_broker_kind("stock") == asset_type.ASSET_TYPE_STOCK


def test_from_broker_kind_adr_is_stock():
    """Owner-confirmed mapping: an ADR is a single company's shares, not a
    fund — routes through the equity strategy, not the ETF one."""
    assert asset_type.from_broker_kind("adr") == asset_type.ASSET_TYPE_STOCK
    assert asset_type.from_broker_kind("ADR") == asset_type.ASSET_TYPE_STOCK


def test_from_broker_kind_none_is_stock():
    assert asset_type.from_broker_kind(None) == asset_type.ASSET_TYPE_STOCK


def test_from_broker_kind_empty_string_is_stock():
    assert asset_type.from_broker_kind("") == asset_type.ASSET_TYPE_STOCK


def test_from_broker_kind_unknown_is_stock():
    assert asset_type.from_broker_kind("crypto") == asset_type.ASSET_TYPE_STOCK
    assert asset_type.from_broker_kind("option") == asset_type.ASSET_TYPE_STOCK


# ── normalize ─────────────────────────────────────────────────────────────────

def test_normalize_etf():
    assert asset_type.normalize("etf") == asset_type.ASSET_TYPE_ETF


def test_normalize_etf_uppercase():
    assert asset_type.normalize("ETF") == asset_type.ASSET_TYPE_ETF


def test_normalize_etf_mixed_case_whitespace():
    assert asset_type.normalize("  Etf  ") == asset_type.ASSET_TYPE_ETF


def test_normalize_stock():
    assert asset_type.normalize("stock") == asset_type.ASSET_TYPE_STOCK


def test_normalize_none_is_stock():
    """The NULL-backfill case — a legacy row predating this column, or one
    whose DDL simply hasn't been applied yet."""
    assert asset_type.normalize(None) == asset_type.ASSET_TYPE_STOCK


def test_normalize_empty_string_is_stock():
    assert asset_type.normalize("") == asset_type.ASSET_TYPE_STOCK


def test_normalize_unknown_string_is_stock():
    assert asset_type.normalize("bond") == asset_type.ASSET_TYPE_STOCK
    assert asset_type.normalize("garbage") == asset_type.ASSET_TYPE_STOCK


def test_normalize_never_returns_etf_unless_exact():
    """Pinned invariant: only the exact string "etf" (any case/whitespace)
    ever normalizes to ASSET_TYPE_ETF -- a near-miss must not."""
    for bad in ("etfs", " et f", "e.t.f", "fund", "mutualfund"):
        assert asset_type.normalize(bad) == asset_type.ASSET_TYPE_STOCK
