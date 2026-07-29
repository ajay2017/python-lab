"""Tests for stock_analyzer/decision_context.py — the passive decision-context
snapshot builder (`build_snapshot`, Concept E Phase 1). Pure / None-safe / no
I/O; every argument is optional. Previously zero test coverage.
"""
import json

import numpy as np
import pandas as pd

from stock_analyzer.decision_context import SCHEMA_VERSION, build_snapshot


# ─── all-defaults call: full documented shape, no exception ────────────────

def test_build_snapshot_all_defaults_returns_full_shape_with_none_fields():
    snap = build_snapshot(ticker=None, action=None)
    assert snap["v"] == SCHEMA_VERSION
    assert snap["ticker"] is None
    assert snap["action"] is None
    assert snap["signal"] == {"signal_seen": None}
    assert snap["market"] == {"macro_regime": None, "tone": None}
    assert snap["portfolio"] == {
        "value": None, "beta": None, "highbeta_share_pct": None,
        "n_positions": None, "top_sector": None,
    }
    assert snap["active_recs"] == {"act_today_n": None}
    assert "captured_at" in snap and isinstance(snap["captured_at"], str)


def test_build_snapshot_ticker_and_action_stringified():
    snap = build_snapshot(ticker="AAPL", action="BUY")
    assert snap["ticker"] == "AAPL"
    assert snap["action"] == "BUY"


# ─── SCHEMA_VERSION regression (imported, not a hardcoded literal) ─────────

def test_build_snapshot_schema_version_matches_imported_constant():
    snap = build_snapshot(ticker=None, action=None)
    assert snap["v"] == SCHEMA_VERSION


# ─── port_df enrichment — top_sector + n_positions ─────────────────────────

def test_build_snapshot_port_df_with_required_columns_computes_top_sector():
    port_df = pd.DataFrame({
        "Sector": ["Tech", "Tech", "Health"],
        "Market Value": [100.0, 200.0, 50.0],
    })
    snap = build_snapshot(ticker="AAA", action="BUY", port_df=port_df)
    assert snap["portfolio"]["n_positions"] == 3
    assert snap["portfolio"]["top_sector"]["sector"] == "Tech"
    assert snap["portfolio"]["top_sector"]["weight_pct"] == (300.0 / 350.0 * 100.0)


def test_build_snapshot_port_df_missing_required_column_leaves_top_sector_none_but_sets_n_positions():
    port_df = pd.DataFrame({"Ticker": ["AAA", "BBB"]})  # no Sector/Market Value
    snap = build_snapshot(ticker="AAA", action="BUY", port_df=port_df)
    assert snap["portfolio"]["top_sector"] is None
    assert snap["portfolio"]["n_positions"] == 2


def test_build_snapshot_empty_port_df_leaves_both_none():
    snap = build_snapshot(ticker="AAA", action="BUY", port_df=pd.DataFrame())
    assert snap["portfolio"]["top_sector"] is None
    assert snap["portfolio"]["n_positions"] is None


def test_build_snapshot_none_port_df_leaves_both_none():
    snap = build_snapshot(ticker="AAA", action="BUY", port_df=None)
    assert snap["portfolio"]["top_sector"] is None
    assert snap["portfolio"]["n_positions"] is None


# ─── macro_regime — dict / string / None branches ──────────────────────────

def test_build_snapshot_macro_regime_dict_extracts_only_documented_keys():
    regime = {"regime": "bull", "label": "Bull Market", "confidence": 0.8, "extra": "dropped"}
    snap = build_snapshot(ticker="AAA", action="BUY", macro_regime=regime)
    assert snap["market"]["macro_regime"] == {
        "regime": "bull", "label": "Bull Market", "confidence": 0.8,
    }


def test_build_snapshot_macro_regime_nonempty_string_becomes_label_dict():
    snap = build_snapshot(ticker="AAA", action="BUY", macro_regime="Neutral")
    assert snap["market"]["macro_regime"] == {"label": "Neutral"}


def test_build_snapshot_macro_regime_empty_string_stays_none():
    snap = build_snapshot(ticker="AAA", action="BUY", macro_regime="")
    assert snap["market"]["macro_regime"] is None


def test_build_snapshot_macro_regime_none_stays_none():
    snap = build_snapshot(ticker="AAA", action="BUY", macro_regime=None)
    assert snap["market"]["macro_regime"] is None


# ─── actions -> act_today_n ───────────────────────────────────────────────────

def test_build_snapshot_actions_list_sets_act_today_n_to_length():
    snap = build_snapshot(ticker="AAA", action="BUY", actions=[1, 2, 3])
    assert snap["active_recs"]["act_today_n"] == 3


def test_build_snapshot_actions_none_leaves_act_today_n_none():
    snap = build_snapshot(ticker="AAA", action="BUY", actions=None)
    assert snap["active_recs"]["act_today_n"] is None


def test_build_snapshot_actions_empty_list_sets_zero():
    snap = build_snapshot(ticker="AAA", action="BUY", actions=[])
    assert snap["active_recs"]["act_today_n"] == 0


# ─── JSON round-trip safety with non-native numeric types ──────────────────

def test_build_snapshot_is_plain_json_serializable_with_numpy_and_timestamp_inputs():
    snap = build_snapshot(
        ticker="AAA",
        action="BUY",
        portfolio_value=np.float64(123.45),
        portfolio_beta=np.float64(1.2),
        captured_at=pd.Timestamp("2026-01-01", tz="UTC"),
    )
    # Must already be a plain dict of JSON-native types (the function itself
    # round-trips through json.dumps/loads before returning).
    round_tripped = json.loads(json.dumps(snap))
    assert round_tripped == snap
    assert snap["portfolio"]["value"] == 123.45
    assert snap["captured_at"] == "2026-01-01T00:00:00+00:00"
