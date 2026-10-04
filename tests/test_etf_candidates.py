"""Unit tests for stock_analyzer/etf_candidates.py — ETF-support Phase 2b
(docs/plans/etf-multi-asset-support.md "Phase 2b" section).

Covers the three pure functions in isolation:
  - resolve_etf_candidates: registry payload + held tickers -> candidate list,
    including the D-G same-index-group screen-out.
  - etf_newpick_eligible: the 9-step ordered eligibility check (D-A AUM fail
    closed, bull-days-only, composite bar).
  - etf_macro_block_reason: D-M set-membership macro gate, fails CLOSED even
    when the recorded reason text is empty.
"""
from __future__ import annotations

import math

import pytest

from stock_analyzer.constants import COMPOSITE_STRONG_BUY, ETF_AUM_THIN_FLOOR_USD
from stock_analyzer.etf_candidates import (
    resolve_etf_candidates,
    etf_newpick_eligible,
    etf_macro_block_reason,
)

pytestmark = pytest.mark.fast


# ── resolve_etf_candidates ───────────────────────────────────────────────────

def test_resolve_returns_none_when_payload_is_none():
    assert resolve_etf_candidates(None, held_tickers=[]) is None


def test_resolve_returns_empty_list_for_empty_payload():
    assert resolve_etf_candidates({}, held_tickers=[]) == []


def test_resolve_normal_candidates_when_nothing_held():
    out = resolve_etf_candidates({"Broad Market": ["SPY", "VOO", "IVV"]}, held_tickers=[])
    tickers = {d["ticker"] for d in out}
    assert tickers == {"SPY", "VOO", "IVV"}
    assert all(d.get("kind") is None for d in out)
    assert all(d["group"] == "Broad Market" for d in out)


def test_resolve_dg_holding_spy_screens_out_voo_and_ivv():
    out = resolve_etf_candidates({"Broad Market": ["SPY", "VOO", "IVV"]}, held_tickers=["SPY"])
    by_ticker = {d["ticker"]: d for d in out}
    assert "SPY" not in by_ticker  # held ticker is not a candidate, not a screen-out
    assert by_ticker["VOO"]["kind"] == "held_group"
    assert by_ticker["IVV"]["kind"] == "held_group"
    assert "SPY" in by_ticker["VOO"]["reason"]
    assert "SPY" in by_ticker["IVV"]["reason"]


def test_resolve_dg_case_insensitive_held_match():
    out = resolve_etf_candidates({"Broad Market": ["SPY", "VOO", "IVV"]}, held_tickers=["spy"])
    by_ticker = {d["ticker"]: d for d in out}
    assert by_ticker["VOO"]["kind"] == "held_group"
    assert by_ticker["IVV"]["kind"] == "held_group"


def test_resolve_dg_no_group_member_held_applies_normal_rules():
    out = resolve_etf_candidates({"Broad Market": ["SPY", "VOO", "IVV"]}, held_tickers=["AAPL"])
    assert all(d.get("kind") is None for d in out)
    assert {d["ticker"] for d in out} == {"SPY", "VOO", "IVV"}


def test_resolve_dedupes_ticker_across_groups():
    out = resolve_etf_candidates(
        {"Group A": ["SPY"], "Group B": ["SPY", "VOO"]}, held_tickers=[],
    )
    tickers = [d["ticker"] for d in out]
    assert tickers.count("SPY") == 1


def test_resolve_skips_empty_group():
    out = resolve_etf_candidates({"Empty Group": []}, held_tickers=[])
    assert out == []


# ── etf_newpick_eligible ─────────────────────────────────────────────────────

def _good_bundle(**overrides):
    base = {
        "asset_type":    "etf",
        "stale_as_of":   None,
        "etf_available": True,
        "etf_total":     COMPOSITE_STRONG_BUY + 5.0,
        "etf_facts":     {"total_assets": ETF_AUM_THIN_FLOOR_USD + 1.0},
    }
    base.update(overrides)
    return base


def test_eligible_true_on_a_clean_bull_day_bundle():
    ok, why = etf_newpick_eligible(_good_bundle(), "bull")
    assert ok is True
    assert why is None


def test_falsy_bundle_is_ineligible():
    ok, why = etf_newpick_eligible(None, "bull")
    assert ok is False
    assert why == "ETF data unavailable"
    ok2, why2 = etf_newpick_eligible({}, "bull")
    assert ok2 is False
    assert why2 == "ETF data unavailable"


def test_non_etf_asset_type_is_ineligible():
    ok, why = etf_newpick_eligible(_good_bundle(asset_type="stock"), "bull")
    assert ok is False
    assert why == "not classified as an ETF"


def test_stale_cache_ineligible_even_at_high_composite():
    ok, why = etf_newpick_eligible(
        _good_bundle(stale_as_of="2026-09-01T00:00:00+00:00", etf_total=90.0), "bull",
    )
    assert ok is False
    assert why == "prices served from stale cache"


def test_etf_available_false_is_ineligible():
    ok, why = etf_newpick_eligible(_good_bundle(etf_available=False), "bull")
    assert ok is False
    assert why == "expense ratio unknown"


def test_etf_total_none_is_ineligible():
    ok, why = etf_newpick_eligible(_good_bundle(etf_total=None), "bull")
    assert ok is False
    assert why == "ETF composite unavailable"


def test_etf_total_nan_is_ineligible():
    ok, why = etf_newpick_eligible(_good_bundle(etf_total=float("nan")), "bull")
    assert ok is False
    assert why == "ETF composite unavailable"


def test_total_assets_none_is_ineligible_fail_closed_D_A():
    ok, why = etf_newpick_eligible(
        _good_bundle(etf_facts={"total_assets": None}), "bull",
    )
    assert ok is False
    assert why == "fund size unknown"


def test_total_assets_nan_is_ineligible_fail_closed_D_A():
    ok, why = etf_newpick_eligible(
        _good_bundle(etf_facts={"total_assets": float("nan")}), "bull",
    )
    assert ok is False
    assert why == "fund size unknown"


def test_total_assets_exactly_at_floor_is_eligible():
    ok, why = etf_newpick_eligible(
        _good_bundle(etf_facts={"total_assets": ETF_AUM_THIN_FLOOR_USD}), "bull",
    )
    assert ok is True
    assert why is None


def test_total_assets_just_below_floor_is_ineligible():
    ok, why = etf_newpick_eligible(
        _good_bundle(etf_facts={"total_assets": ETF_AUM_THIN_FLOOR_USD - 1.0}), "bull",
    )
    assert ok is False
    assert why == "AUM below floor"


def test_flat_day_is_ineligible():
    ok, why = etf_newpick_eligible(_good_bundle(), "flat")
    assert ok is False
    assert why == "bull days only"


def test_down_day_is_ineligible():
    ok, why = etf_newpick_eligible(_good_bundle(), "bear")
    assert ok is False
    assert why == "bull days only"


def test_composite_just_below_strong_buy_bar_is_ineligible():
    ok, why = etf_newpick_eligible(
        _good_bundle(etf_total=COMPOSITE_STRONG_BUY - 0.1), "bull",
    )
    assert ok is False
    assert "below" in why


def test_composite_exactly_at_strong_buy_bar_is_eligible():
    ok, why = etf_newpick_eligible(_good_bundle(etf_total=COMPOSITE_STRONG_BUY), "bull")
    assert ok is True
    assert why is None


def test_calibration_boundary_cheap_fund_technical_just_below_and_above():
    # Calls the REAL etf_composite() rather than hand-duplicating
    # 0.70*technical + 0.30*cost inline -- a hardcoded copy of the formula
    # can silently drift from the real one if ETF_COMPOSITE_WEIGHTS ever
    # changes, while this version would simply start failing (the honest
    # outcome). At cost=100 (cheap fund), composite == 75 at technical ~= 64.286.
    from stock_analyzer.etf_scoring import etf_composite
    _cost = 100.0
    _just_below = etf_composite(64.2, _cost)
    _just_above = etf_composite(64.4, _cost)
    assert _just_below < COMPOSITE_STRONG_BUY
    assert _just_above >= COMPOSITE_STRONG_BUY
    ok_below, _ = etf_newpick_eligible(_good_bundle(etf_total=_just_below), "bull")
    ok_above, _ = etf_newpick_eligible(_good_bundle(etf_total=_just_above), "bull")
    assert ok_below is False
    assert ok_above is True


# ── etf_macro_block_reason ───────────────────────────────────────────────────

def test_macro_empty_set_passes():
    assert etf_macro_block_reason(set(), {}) is None


def test_macro_all_sentinel_blocks_with_recorded_reason():
    reason = etf_macro_block_reason({"__ALL__"}, {"__ALL__": "FOMC meeting tomorrow"})
    assert reason == "FOMC meeting tomorrow"


def test_macro_all_sentinel_blocks_even_with_no_recorded_reason():
    reason = etf_macro_block_reason({"__ALL__"}, {})
    assert reason is not None
    assert "all-sector" in reason


def test_macro_keyed_sector_set_blocks_D_M_option_b():
    reason = etf_macro_block_reason({"Financials"}, {"Financials": "CPI release in 1d"})
    assert reason == "CPI release in 1d"


def test_macro_keyed_sector_set_with_empty_reason_text_still_blocks_fail_closed():
    # This is the exact bug the stock path's own `if _macro_block:` check has
    # (an empty-string reason reads as falsy and fails OPEN) -- this function
    # must not copy it: set membership alone decides, not reason truthiness.
    reason = etf_macro_block_reason({"Financials"}, {"Financials": ""})
    assert reason is not None
    assert reason != ""


def test_macro_keyed_sector_set_with_no_reason_entry_at_all_still_blocks():
    reason = etf_macro_block_reason({"Financials", "Energy"}, {})
    assert reason is not None
    assert "Energy" in reason or "Financials" in reason
