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

from stock_analyzer.gate_ledger import build_suppression_rows
from stock_analyzer.constants import MARKET_TONE_BEAR_PCT


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
