"""Tests for stock_analyzer/broker_sync.py's Phase 1 ETF-support additions
(F-279 §11): position_kinds (broker ground-truth ticker->asset_type map) and
resolve_trade_asset_type (the promotion-time precedence app.py's SnapTrade
bridge calls).

Mirrors tests/test_broker_sync.py's `_pos` fixture shape and its "offline
sentinel never collapses to an empty/real result" discipline.
"""
from stock_analyzer import broker_sync as bs
from stock_analyzer import asset_type
import pytest

pytestmark = pytest.mark.fast


def _pos(ticker, units, kind="stock"):
    return {"instrument": {"kind": kind, "symbol": ticker}, "units": units}


# ─── position_kinds — offline sentinel / real-empty ────────────────────────

def test_position_kinds_none_in_none_out():
    assert bs.position_kinds(None) is None


def test_position_kinds_empty_list_is_real_empty_map_not_none():
    out = bs.position_kinds([])
    assert out == {}
    assert out is not None


# ─── filtering: mirrors normalize_positions exactly ────────────────────────

def test_position_kinds_drops_non_equity_instrument():
    out = bs.position_kinds([_pos("BTC", 1.0, kind="crypto")])
    assert out == {}


def test_position_kinds_skips_zero_unit_position():
    out = bs.position_kinds([_pos("AAPL", 0.0, kind="stock")])
    assert out == {}


def test_position_kinds_skips_none_units():
    out = bs.position_kinds([{"instrument": {"kind": "stock", "symbol": "AAPL"}, "units": None}])
    assert out == {}


# ─── classification ─────────────────────────────────────────────────────────

def test_position_kinds_etf_kind_maps_to_etf():
    out = bs.position_kinds([_pos("SPY", 10, kind="etf")])
    assert out == {"SPY": asset_type.ASSET_TYPE_ETF}


def test_position_kinds_adr_kind_maps_to_stock():
    out = bs.position_kinds([_pos("BABA", 10, kind="adr")])
    assert out == {"BABA": asset_type.ASSET_TYPE_STOCK}


def test_position_kinds_stock_kind_maps_to_stock():
    out = bs.position_kinds([_pos("AAPL", 10, kind="stock")])
    assert out == {"AAPL": asset_type.ASSET_TYPE_STOCK}


def test_position_kinds_none_kind_defaults_to_stock():
    """A position whose `instrument.kind` is absent/None still gets an entry
    (defaulted to "stock"), unlike a fully-excluded non-equity instrument."""
    out = bs.position_kinds([{"instrument": {"symbol": "AAPL"}, "units": 10}])
    assert out == {"AAPL": asset_type.ASSET_TYPE_STOCK}


# ─── multi-account merge ────────────────────────────────────────────────────

def test_position_kinds_same_ticker_multiple_accounts_no_crash_no_duplicate():
    """Same ticker reported by two accounts — first non-None kind wins, one
    entry in the output, no crash."""
    positions = [_pos("SPY", 5, kind="etf"), _pos("SPY", 3, kind="etf")]
    out = bs.position_kinds(positions)
    assert out == {"SPY": asset_type.ASSET_TYPE_ETF}


def test_position_kinds_first_none_kind_then_real_kind_prefers_real():
    """The ticker's FIRST occurrence has no kind at all; a LATER occurrence
    resolves it — the later, more informative reading must win, not the
    earlier uninformative None."""
    positions = [
        {"instrument": {"symbol": "SPY"}, "units": 5},   # no kind at all
        _pos("SPY", 3, kind="etf"),
    ]
    out = bs.position_kinds(positions)
    assert out == {"SPY": asset_type.ASSET_TYPE_ETF}


def test_position_kinds_kind_never_resolved_across_any_account_defaults_stock():
    positions = [
        {"instrument": {"symbol": "AAPL"}, "units": 5},
        {"instrument": {"symbol": "AAPL"}, "units": 3},
    ]
    out = bs.position_kinds(positions)
    assert out == {"AAPL": asset_type.ASSET_TYPE_STOCK}


# ─── resolve_trade_asset_type — the app.py bridge's precedence ─────────────

def test_resolve_trade_asset_type_prefers_broker_snapshot():
    out = bs.resolve_trade_asset_type("SPY", {"SPY": "etf"}, bundle_asset_type="stock")
    assert out == asset_type.ASSET_TYPE_ETF


def test_resolve_trade_asset_type_falls_back_to_bundle_when_ticker_absent():
    out = bs.resolve_trade_asset_type("QQQ", {"SPY": "etf"}, bundle_asset_type="etf")
    assert out == asset_type.ASSET_TYPE_ETF


def test_resolve_trade_asset_type_falls_back_to_stock_when_neither_resolves():
    out = bs.resolve_trade_asset_type("AAPL", None, bundle_asset_type=None)
    assert out == asset_type.ASSET_TYPE_STOCK


def test_resolve_trade_asset_type_empty_kinds_map_falls_through():
    out = bs.resolve_trade_asset_type("AAPL", {}, bundle_asset_type=None)
    assert out == asset_type.ASSET_TYPE_STOCK


def test_resolve_trade_asset_type_normalizes_broker_value():
    """A raw broker-side value that isn't exactly "etf"/"stock" still
    normalizes safely rather than being stamped verbatim."""
    out = bs.resolve_trade_asset_type("XYZ", {"XYZ": "ETF"}, bundle_asset_type=None)
    assert out == asset_type.ASSET_TYPE_ETF
