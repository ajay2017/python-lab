"""Tests for stock_analyzer.gate_ledger.build_suppression_rows.

Coverage:
- None grow (offline) → []
- {} grow (online, no buckets) → []
- Missing bucket key vs [] bucket (semantically distinct, both → no rows)
- Bear-day synthetic row (G-23)
- G-04 / G-09 split from the same concentration_blocked_adds bucket
- Item with no gate_id → skipped (never infer from bucket name)
- 300-char truncation on reason
- Both counterfactual values (True / False) survive
- Non-positive price → None
"""
from __future__ import annotations

import datetime
import pytest

from stock_analyzer.gate_ledger import (
    build_suppression_rows,
    build_watchlist_suppression_rows,
    build_rebalance_suppression_rows,
    build_analysis_stop_suppression_row,
)
from stock_analyzer.constants import MARKET_TONE_BEAR_PCT, RR_ENTRY_MIN

pytestmark = pytest.mark.fast


REC_DATE = datetime.date(2026, 8, 27)
SOURCE = "app"

# ── helpers ──────────────────────────────────────────────────────────────────

def _rows(grow, tone=None, sp500_pct=None):
    return build_suppression_rows(
        grow, rec_date=REC_DATE, source=SOURCE, tone=tone, sp500_pct=sp500_pct
    )


def _make_item(gate_id, ticker="AAPL", counterfactual=True, **extra):
    return {
        "ticker": ticker,
        "gate_id": gate_id,
        "counterfactual": counterfactual,
        "gate_value": extra.pop("gate_value", 10.0),
        "gate_threshold": extra.pop("gate_threshold", 15.0),
        "score": extra.pop("score", 70.0),
        "reason": extra.pop("reason", "test reason"),
        **extra,
    }


# ── offline sentinel ──────────────────────────────────────────────────────────

def test_none_grow_returns_empty():
    """grow=None means offline; must return [] without recording anything."""
    assert _rows(None) == []


def test_empty_dict_grow_returns_empty():
    """grow={} (online, no buckets populated) → no rows, but NOT the same path
    as None — {} is reachable separately and means 'checked, nothing suppressed'."""
    result = _rows({})
    assert result == []


# ── missing key vs [] — both reachable but semantically distinct ──────────────

def test_missing_bucket_key_yields_no_rows():
    """grow has no 'macro_blocked_picks' key → no rows for that bucket."""
    grow = {}
    result = _rows(grow)
    assert result == []


def test_empty_bucket_list_yields_no_rows():
    """grow has the key but the bucket is [] → no rows for that bucket."""
    grow = {"macro_blocked_picks": []}
    result = _rows(grow)
    assert result == []


def test_missing_key_and_empty_list_are_both_reachable():
    """Confirm the two paths are reachable separately without raising."""
    # Missing key → no rows
    assert _rows({}) == []
    # Empty list → no rows
    assert _rows({"macro_blocked_picks": []}) == []


# ── sentinel ordering: None must precede tone check ─────────────────────────

def test_offline_never_fabricates_a_bear_day_row():
    """grow=None must return [] even when tone="bear" and sp500_pct is set.

    This pins that the `is None` early return PRECEDES the `tone == "bear"` branch.
    Reorder them and an offline day with a stale bear tone would write a synthetic
    G-23 row asserting the engine ran restraint on a day it never ran at all.

    Mutation check (performed 2026-08-27, then reverted): inserting
    `grow = grow or {}` above the `is None` return makes the FIRST assertion
    below fail — grow=None collapses to {}, the bear branch then fires, and the
    call returns 1 synthetic G-23 row where [] is required. The second
    assertion is the control: it must keep returning exactly 1 row, so the test
    cannot be satisfied by a function that simply never emits a tone row.
    """
    # grow=None with bear tone → must return [], never a synthetic row
    assert build_suppression_rows(
        None, rec_date=REC_DATE, source="app", tone="bear", sp500_pct=-0.8
    ) == []
    # grow={} with bear tone → must return 1 synthetic row (gates ran, bear blocked all)
    assert len(build_suppression_rows(
        {}, rec_date=REC_DATE, source="app", tone="bear", sp500_pct=-0.8
    )) == 1


# ── bear-day synthetic row ────────────────────────────────────────────────────

def test_bear_day_emits_one_synthetic_row():
    """On a bear day, exactly one G-23 row is emitted regardless of grow content."""
    grow = {}   # buckets absent — that is the bear-day state
    result = _rows(grow, tone="bear", sp500_pct=-0.8)
    assert len(result) == 1
    row = result[0]
    assert row["ticker"] == "__MARKET__"
    assert row["gate_id"] == "G-23"
    assert row["lane"] == "tone"
    assert row["counterfactual"] is True
    assert row["gate_value"] == pytest.approx(-0.8)
    assert row["gate_threshold"] == pytest.approx(MARKET_TONE_BEAR_PCT)
    assert row["tone"] == "bear"
    assert row["source"] == SOURCE
    assert row["rec_date"] == REC_DATE.isoformat()


def test_bear_day_ignores_bucket_contents():
    """Bear-day returns exactly 1 synthetic row even if grow has bucket data."""
    grow = {
        "macro_blocked_picks": [_make_item("G-07")],
    }
    result = _rows(grow, tone="bear", sp500_pct=-1.2)
    assert len(result) == 1
    assert result[0]["gate_id"] == "G-23"


# ── G-04 / G-09 split from concentration_blocked_adds ────────────────────────

def test_g04_and_g09_split_from_same_bucket():
    """Two items in concentration_blocked_adds with different gate_ids produce
    two rows with correct ids (F4 — producer emits id, consumer reads it)."""
    grow = {
        "concentration_blocked_adds": [
            _make_item("G-04", ticker="AAPL", gate_value=16.0, gate_threshold=15.0),
            _make_item("G-09", ticker="MSFT", gate_value=12.0, gate_threshold=None),
        ]
    }
    result = _rows(grow, tone="bull")
    gate_ids = {r["gate_id"] for r in result}
    assert "G-04" in gate_ids
    assert "G-09" in gate_ids
    assert len(result) == 2


# ── item with no gate_id → skipped ───────────────────────────────────────────

def test_item_without_gate_id_is_skipped():
    """An item with no gate_id key must be silently skipped — never infer from
    the bucket name (plan finding F4)."""
    grow = {
        "macro_blocked_picks": [
            {"ticker": "AAPL", "score": 70.0, "reason": "no id"},   # no gate_id
            _make_item("G-07", ticker="MSFT"),
        ]
    }
    result = _rows(grow, tone="bull")
    assert len(result) == 1
    assert result[0]["ticker"] == "MSFT"
    assert result[0]["gate_id"] == "G-07"


# ── 300-char truncation ───────────────────────────────────────────────────────

def test_reason_truncated_to_300_chars():
    """reason is capped at 300 characters."""
    long_reason = "x" * 500
    grow = {
        "macro_blocked_picks": [
            _make_item("G-07", reason=long_reason),
        ]
    }
    result = _rows(grow, tone="bull")
    assert len(result) == 1
    assert len(result[0]["reason"]) == 300


def test_reason_shorter_than_300_preserved():
    """reason shorter than 300 characters is not padded or altered."""
    grow = {
        "macro_blocked_picks": [
            _make_item("G-07", reason="short"),
        ]
    }
    result = _rows(grow, tone="bull")
    assert result[0]["reason"] == "short"


# ── counterfactual values ─────────────────────────────────────────────────────

def test_counterfactual_true_survives():
    grow = {
        "cooldown_adds": [
            _make_item("G-24", counterfactual=True),
        ]
    }
    result = _rows(grow, tone="bull")
    assert result[0]["counterfactual"] is True


def test_counterfactual_false_survives():
    grow = {
        "deterioration_blocked_adds": [
            _make_item("G-20", counterfactual=False),
        ]
    }
    result = _rows(grow, tone="bull")
    assert result[0]["counterfactual"] is False


# ── non-positive price → None ─────────────────────────────────────────────────

def test_zero_price_becomes_none():
    item = _make_item("G-07")
    item["price"] = 0.0
    grow = {"macro_blocked_picks": [item]}
    result = _rows(grow, tone="bull")
    assert result[0]["price_at_suppress"] is None


def test_negative_price_becomes_none():
    item = _make_item("G-07")
    item["price"] = -10.0
    grow = {"macro_blocked_picks": [item]}
    result = _rows(grow, tone="bull")
    assert result[0]["price_at_suppress"] is None


def test_positive_price_preserved():
    item = _make_item("G-07")
    item["price"] = 150.0
    grow = {"macro_blocked_picks": [item]}
    result = _rows(grow, tone="bull")
    assert result[0]["price_at_suppress"] == pytest.approx(150.0)


def test_missing_price_is_none():
    item = _make_item("G-07")
    # no "price" key at all
    grow = {"macro_blocked_picks": [item]}
    result = _rows(grow, tone="bull")
    assert result[0]["price_at_suppress"] is None


# ── rec_date and source on every row ─────────────────────────────────────────

def test_rec_date_and_source_on_every_row():
    grow = {
        "macro_blocked_picks": [_make_item("G-07", ticker="AAA")],
        "sector_blocked_adds": [_make_item("G-16", ticker="BBB")],
    }
    result = _rows(grow, tone="bull")
    assert len(result) == 2
    for row in result:
        assert row["rec_date"] == REC_DATE.isoformat()
        assert row["source"] == SOURCE


# ── tickers uppercased/stripped ───────────────────────────────────────────────

def test_ticker_uppercased_and_stripped():
    grow = {
        "macro_blocked_picks": [_make_item("G-07", ticker=" aapl ")],
    }
    result = _rows(grow, tone="bull")
    assert result[0]["ticker"] == "AAPL"


# ── explicit score fields (producer sets them; ledger reads directly) ─────────

def test_explicit_momentum_score_read_from_item():
    """momentum_score is read directly from the item dict (no lane inference)."""
    grow = {
        "macro_blocked_picks": [
            {
                "ticker": "AAPL",
                "gate_id": "G-07",
                "counterfactual": True,
                "gate_value": None,
                "gate_threshold": 3,
                "score": 72.0,
                "momentum_score": 72.0,
                "composite_score": None,
                "reason": "macro",
                "price": 150.0,
            }
        ]
    }
    result = _rows(grow, tone="bull")
    assert result[0]["momentum_score"] == pytest.approx(72.0)
    assert result[0]["composite_score"] is None


def test_explicit_composite_score_read_from_item():
    """composite_score is read directly from the item dict (no lane inference)."""
    grow = {
        "cooldown_adds": [
            {
                "ticker": "MSFT",
                "gate_id": "G-24",
                "counterfactual": True,
                "gate_value": 5,
                "gate_threshold": 10,
                "score": 80.0,
                "composite_score": 80.0,
                "momentum_score": None,
                "reason": "cooldown",
                "price": 200.0,
            }
        ]
    }
    result = _rows(grow, tone="bull")
    assert result[0]["composite_score"] == pytest.approx(80.0)
    assert result[0]["momentum_score"] is None


def test_both_scores_none_when_absent():
    """If neither score key is present in the item, both output fields are None."""
    grow = {
        "macro_blocked_picks": [
            {
                "ticker": "AAPL",
                "gate_id": "G-07",
                "counterfactual": True,
                "gate_value": None,
                "gate_threshold": 3,
                "score": 72.0,
                # no composite_score or momentum_score keys
                "reason": "macro",
            }
        ]
    }
    result = _rows(grow, tone="bull")
    assert result[0]["composite_score"] is None
    assert result[0]["momentum_score"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Roadmap B2 (2026-09-13): build_watchlist_suppression_rows,
# build_rebalance_suppression_rows, build_analysis_stop_suppression_row.
# None of these 5 gates (G-02/G-05/G-06/G-13/G-18) flow through grow_today,
# so build_suppression_rows above is untouched by any of this.
# ═══════════════════════════════════════════════════════════════════════════

# ── build_watchlist_suppression_rows ─────────────────────────────────────────

def _wl_card(action, ticker="AAPL", score=70.0, price=100.0, suppression_kind=None,
             gate_value=None, gate_threshold=None):
    return {
        "ticker": ticker,
        "action": action,
        "score": score,
        "price": price,
        "suppression_kind": suppression_kind,
        "gate_value": gate_value,
        "gate_threshold": gate_threshold,
    }


def test_watchlist_none_recs_returns_empty():
    assert build_watchlist_suppression_rows(None, rec_date=REC_DATE, source="app") == []


def test_watchlist_empty_recs_returns_empty():
    assert build_watchlist_suppression_rows([], rec_date=REC_DATE, source="app") == []


def test_watchlist_ordinary_near_entry_without_suppression_kind_yields_no_row():
    """LOAD-BEARING: a NEAR_ENTRY card with no suppression_kind (the
    'approaching zone' branch, watchlist_advisor.py:530) must produce NO row
    — only cards that ALSO carry suppression_kind (the hard-breach and
    in-zone-R:R branches) do."""
    recs = [_wl_card("NEAR_ENTRY", suppression_kind=None)]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result == []


def test_watchlist_enter_now_card_yields_no_row_even_with_stray_kind():
    """Belt-and-braces: action must ALSO be NEAR_ENTRY — an ENTER_NOW card
    can never legitimately carry suppression_kind, but if one somehow did,
    the builder must not emit a row for it."""
    recs = [_wl_card("ENTER_NOW", suppression_kind="sector")]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result == []


def test_watchlist_sector_downgrade_yields_g05_row():
    recs = [_wl_card(
        "NEAR_ENTRY", ticker="XOM", score=72.0, price=110.0,
        suppression_kind="sector", gate_value=40.0, gate_threshold=35.0,
    )]
    result = build_watchlist_suppression_rows(
        recs, rec_date=REC_DATE, source="app", sector_by_ticker={"XOM": "Energy"}
    )
    assert len(result) == 1
    row = result[0]
    assert row["gate_id"] == "G-05"
    assert row["lane"] == "downgrade"
    assert row["ticker"] == "XOM"
    assert row["sector"] == "Energy"
    assert row["gate_value"] == pytest.approx(40.0)
    assert row["gate_threshold"] == pytest.approx(35.0)
    assert row["composite_score"] == pytest.approx(72.0)


def test_watchlist_beta_downgrade_yields_g06_row():
    recs = [_wl_card(
        "NEAR_ENTRY", suppression_kind="beta", gate_value=2.0, gate_threshold=1.8,
    )]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result[0]["gate_id"] == "G-06"
    assert result[0]["lane"] == "downgrade"


def test_watchlist_rr_downgrade_yields_g13_row():
    recs = [_wl_card(
        "NEAR_ENTRY", suppression_kind="rr", gate_value=1.5, gate_threshold=RR_ENTRY_MIN,
    )]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result[0]["gate_id"] == "G-13"
    assert result[0]["lane"] == "downgrade"


def test_watchlist_unknown_suppression_kind_is_skipped():
    """Never guess a gate id for an unrecognized suppression_kind (F4)."""
    recs = [_wl_card("NEAR_ENTRY", suppression_kind="something_new")]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result == []


def test_watchlist_counterfactual_always_true():
    recs = [
        _wl_card("NEAR_ENTRY", ticker="AAA", suppression_kind="sector"),
        _wl_card("NEAR_ENTRY", ticker="BBB", suppression_kind="beta"),
        _wl_card("NEAR_ENTRY", ticker="CCC", suppression_kind="rr"),
    ]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert len(result) == 3
    assert all(r["counterfactual"] is True for r in result)


def test_watchlist_price_zero_becomes_none():
    recs = [_wl_card("NEAR_ENTRY", suppression_kind="sector", price=0.0)]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result[0]["price_at_suppress"] is None


def test_watchlist_price_negative_becomes_none():
    recs = [_wl_card("NEAR_ENTRY", suppression_kind="sector", price=-5.0)]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result[0]["price_at_suppress"] is None


def test_watchlist_price_positive_preserved():
    recs = [_wl_card("NEAR_ENTRY", suppression_kind="sector", price=88.5)]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result[0]["price_at_suppress"] == pytest.approx(88.5)


def test_watchlist_blank_ticker_skipped():
    recs = [_wl_card("NEAR_ENTRY", ticker="  ", suppression_kind="sector")]
    result = build_watchlist_suppression_rows(recs, rec_date=REC_DATE, source="app")
    assert result == []


def test_watchlist_sector_lookup_missing_ticker_is_none():
    recs = [_wl_card("NEAR_ENTRY", ticker="ZZZ", suppression_kind="sector")]
    result = build_watchlist_suppression_rows(
        recs, rec_date=REC_DATE, source="app", sector_by_ticker={}
    )
    assert result[0]["sector"] is None


# ── build_rebalance_suppression_rows ─────────────────────────────────────────

def _rb_item(ticker="AAPL", price=100.0, composite_score=70.0, sector="Tech", reason="test"):
    return {
        "ticker": ticker, "price": price, "composite_score": composite_score,
        "sector": sector, "reason": reason,
    }


def test_rebalance_none_input_returns_empty():
    assert build_rebalance_suppression_rows(None, rec_date=REC_DATE, source="app") == []


def test_rebalance_empty_input_returns_empty():
    assert build_rebalance_suppression_rows([], rec_date=REC_DATE, source="app") == []


def test_rebalance_yields_g02_row_add_suppressed_lane():
    items = [_rb_item(ticker="MSFT")]
    result = build_rebalance_suppression_rows(items, rec_date=REC_DATE, source="app")
    assert len(result) == 1
    row = result[0]
    assert row["gate_id"] == "G-02"
    assert row["lane"] == "add_suppressed"
    assert row["ticker"] == "MSFT"
    assert row["counterfactual"] is True
    # Set-membership gate, like G-01 — no scalar threshold.
    assert row["gate_value"] is None
    assert row["gate_threshold"] is None


def test_rebalance_reads_price_composite_sector_from_item():
    items = [_rb_item(price=142.5, composite_score=68.0, sector="Technology")]
    result = build_rebalance_suppression_rows(items, rec_date=REC_DATE, source="app")
    assert result[0]["price_at_suppress"] == pytest.approx(142.5)
    assert result[0]["composite_score"] == pytest.approx(68.0)
    assert result[0]["sector"] == "Technology"


def test_rebalance_price_non_positive_becomes_none():
    items = [_rb_item(price=0.0)]
    result = build_rebalance_suppression_rows(items, rec_date=REC_DATE, source="app")
    assert result[0]["price_at_suppress"] is None


def test_rebalance_blank_ticker_skipped():
    items = [_rb_item(ticker="")]
    result = build_rebalance_suppression_rows(items, rec_date=REC_DATE, source="app")
    assert result == []


def test_rebalance_reason_truncated_to_300_chars():
    items = [_rb_item(reason="x" * 500)]
    result = build_rebalance_suppression_rows(items, rec_date=REC_DATE, source="app")
    assert len(result[0]["reason"]) == 300


# ── build_analysis_stop_suppression_row ──────────────────────────────────────

def test_analysis_stop_row_none_on_blank_ticker():
    row = build_analysis_stop_suppression_row(
        ticker="", price=90.0, composite_score=70.0, stop=95.0, gap_pct=-5.0,
        sector="Tech", rec_date=REC_DATE, source="app",
    )
    assert row is None


def test_analysis_stop_row_basic_fields():
    row = build_analysis_stop_suppression_row(
        ticker="aapl", price=90.0, composite_score=70.0, stop=95.0, gap_pct=-5.0,
        sector="Technology", rec_date=REC_DATE, source="app",
    )
    assert row is not None
    assert row["ticker"] == "AAPL"
    assert row["gate_id"] == "G-18"
    assert row["lane"] == "add_suppressed"
    assert row["counterfactual"] is True
    assert row["gate_value"] == pytest.approx(90.0)     # price
    assert row["gate_threshold"] == pytest.approx(95.0)  # stop
    assert row["composite_score"] == pytest.approx(70.0)
    assert row["sector"] == "Technology"
    assert row["rec_date"] == REC_DATE.isoformat()
    assert row["source"] == "app"


def test_analysis_stop_row_price_non_positive_becomes_none():
    row = build_analysis_stop_suppression_row(
        ticker="AAPL", price=0.0, composite_score=70.0, stop=95.0, gap_pct=-5.0,
        sector="Technology", rec_date=REC_DATE, source="app",
    )
    assert row["price_at_suppress"] is None


def test_analysis_stop_row_price_positive_preserved():
    row = build_analysis_stop_suppression_row(
        ticker="AAPL", price=90.0, composite_score=70.0, stop=95.0, gap_pct=-5.0,
        sector="Technology", rec_date=REC_DATE, source="app",
    )
    assert row["price_at_suppress"] == pytest.approx(90.0)
