"""Regression tests for stock_analyzer/tax_report.py — the Tax Report (Phase 1
of the 📄 Reports feature, docs/plans/reports.md). FIFO closed-lot realized-
gains ledger: share conservation, ST/LT boundary, the ET tax-year boundary,
reconciliation vs the stored average-cost total, wash-sale pass-through, the
offline/empty contract, and SPLIT holding-period inheritance.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import tax_advisor as ta
from stock_analyzer import tax_report as tr
from stock_analyzer.constants import TAX_STCG_THRESHOLD_DAYS, TAX_WASH_SALE_DAYS

pytestmark = pytest.mark.fast

_COLS = ["id", "ticker", "action", "shares", "price", "cost_basis",
         "realized_pnl", "trigger_type", "traded_at"]


def _row(id_, ticker, action, shares, price, when: date, cost_basis=None,
         realized_pnl=None, hhmmss_utc="15:00:00"):
    """A single trade row. `when` is an America/New_York-safe UTC timestamp
    (15:00 UTC sits mid-day ET regardless of DST), unless the caller overrides
    `hhmmss_utc` to probe a tax-year boundary explicitly."""
    return {
        "id": id_, "ticker": ticker, "action": action, "shares": shares,
        "price": price, "cost_basis": cost_basis, "realized_pnl": realized_pnl,
        "trigger_type": None,
        "traded_at": f"{when.isoformat()}T{hhmmss_utc}Z",
    }


def _df(rows):
    return pd.DataFrame(rows, columns=_COLS)


# ── offline / empty contract ────────────────────────────────────────────────

def test_none_trades_df_returns_none():
    assert tr.build_realized_lot_ledger(None, 2025) is None


def test_empty_dataframe_returns_shaped_empty():
    ledger = tr.build_realized_lot_ledger(pd.DataFrame(columns=_COLS), 2025)
    assert ledger is not None
    assert ledger["rows"] == []
    assert ledger["st_gain"] == 0.0 and ledger["lt_gain"] == 0.0
    assert ledger["reconciles"] is True


def test_empty_year_with_real_trades_elsewhere_is_shaped_empty_not_none():
    rows = [
        _row(1, "AAPL", "BUY", 10, 100.0, when=date(2024, 1, 1)),
        _row(2, "AAPL", "SELL", 10, 120.0, cost_basis=100.0, realized_pnl=200.0,
             when=date(2024, 6, 1)),
    ]
    ledger = tr.build_realized_lot_ledger(_df(rows), 2025, today=date(2026, 1, 1))
    assert ledger is not None
    assert ledger["rows"] == []
    assert ledger["st_gain"] == 0.0
    assert ledger["reconciles"] is True


def test_available_tax_years_none_and_empty():
    assert tr.available_tax_years(None) == []
    assert tr.available_tax_years(pd.DataFrame(columns=_COLS)) == []


def test_available_tax_years_distinct_descending():
    rows = [
        _row(1, "AAPL", "BUY", 10, 100.0, when=date(2023, 1, 1)),
        _row(2, "AAPL", "SELL", 5, 110.0, cost_basis=100.0, realized_pnl=50.0,
             when=date(2023, 6, 1)),
        _row(3, "AAPL", "SELL", 5, 120.0, cost_basis=100.0, realized_pnl=100.0,
             when=date(2024, 6, 1)),
        _row(4, "MSFT", "BUY", 20, 50.0, when=date(2025, 1, 1)),
        _row(5, "MSFT", "SPLIT", 40, 25.0, when=date(2025, 2, 1)),
        _row(6, "MSFT", "SELL", 40, 30.0, cost_basis=25.0, realized_pnl=200.0,
             when=date(2025, 6, 1)),
    ]
    assert tr.available_tax_years(_df(rows)) == [2025, 2024, 2023]


# ── FIFO share conservation ─────────────────────────────────────────────────

def test_fifo_matches_multiple_lots_in_order_and_conserves_shares():
    rows = [
        _row(1, "ABC", "BUY", 5, 10.0, when=date(2024, 1, 1)),
        _row(2, "ABC", "BUY", 5, 20.0, when=date(2024, 1, 10)),
        _row(3, "ABC", "SELL", 8, 30.0, cost_basis=15.0, realized_pnl=120.0,
             when=date(2024, 1, 20)),
    ]
    ledger = tr.build_realized_lot_ledger(_df(rows), 2024, today=date(2025, 6, 1))
    matched = [r for r in ledger["rows"]]
    assert sum(r["shares"] for r in matched) == pytest.approx(8.0)
    # FIFO: 5 shares from the $10 lot first, then 3 from the $20 lot.
    assert matched[0]["shares"] == pytest.approx(5.0)
    assert matched[0]["cost"] == pytest.approx(50.0)
    assert matched[1]["shares"] == pytest.approx(3.0)
    assert matched[1]["cost"] == pytest.approx(60.0)
    assert all(r["term"] != "Unknown" for r in matched)


def test_sell_exceeding_lot_history_surfaces_unmatched_as_unknown_never_dropped():
    rows = [
        _row(1, "ABC", "BUY", 5, 10.0, when=date(2024, 1, 1)),
        _row(2, "ABC", "SELL", 8, 30.0, cost_basis=10.5, realized_pnl=None,
             when=date(2024, 6, 1)),
    ]
    ledger = tr.build_realized_lot_ledger(_df(rows), 2024, today=date(2025, 6, 1))
    matched_rows = [r for r in ledger["rows"] if r["term"] != "Unknown"]
    unknown_rows = [r for r in ledger["rows"] if r["term"] == "Unknown"]
    assert len(matched_rows) == 1 and matched_rows[0]["shares"] == pytest.approx(5.0)
    assert len(unknown_rows) == 1
    assert unknown_rows[0]["shares"] == pytest.approx(3.0)
    # Fallback proration off the SELL row's own stored cost_basis (per share).
    assert unknown_rows[0]["cost"] == pytest.approx(3 * 10.5)
    assert unknown_rows[0]["proceeds"] == pytest.approx(3 * 30.0)
    # Total matched+unknown shares must equal the full sell — never dropped.
    assert sum(r["shares"] for r in ledger["rows"]) == pytest.approx(8.0)


# ── ST/LT boundary — pinned exact days, not assumed ─────────────────────────

def test_st_lt_boundary_exact_days():
    buy_date = date(2024, 1, 1)
    # Held exactly TAX_STCG_THRESHOLD_DAYS -> long-term.
    sell_lt = buy_date + timedelta(days=TAX_STCG_THRESHOLD_DAYS)
    rows_lt = [
        _row(1, "LTX", "BUY", 10, 10.0, when=buy_date),
        _row(2, "LTX", "SELL", 10, 20.0, cost_basis=10.0, realized_pnl=100.0, when=sell_lt),
    ]
    ledger_lt = tr.build_realized_lot_ledger(_df(rows_lt), sell_lt.year, today=sell_lt + timedelta(days=60))
    assert ledger_lt["rows"][0]["term"] == "LT"
    assert ledger_lt["rows"][0]["days_held"] == TAX_STCG_THRESHOLD_DAYS

    # One day short -> short-term.
    sell_st = buy_date + timedelta(days=TAX_STCG_THRESHOLD_DAYS - 1)
    rows_st = [
        _row(1, "STX", "BUY", 10, 10.0, when=buy_date),
        _row(2, "STX", "SELL", 10, 20.0, cost_basis=10.0, realized_pnl=100.0, when=sell_st),
    ]
    ledger_st = tr.build_realized_lot_ledger(_df(rows_st), sell_st.year, today=sell_st + timedelta(days=60))
    assert ledger_st["rows"][0]["term"] == "ST"
    assert ledger_st["rows"][0]["days_held"] == TAX_STCG_THRESHOLD_DAYS - 1


# ── ET tax-year boundary ────────────────────────────────────────────────────

def test_et_tax_year_boundary_utc_jan1_is_et_dec31():
    rows = [
        _row(1, "XYZ", "BUY", 5, 10.0, when=date(2025, 1, 1)),
        # 2026-01-01 04:30 UTC == 2025-12-31 23:30 ET (EST, UTC-5).
        _row(2, "XYZ", "SELL", 5, 12.0, cost_basis=10.0, realized_pnl=10.0,
             when=date(2026, 1, 1), hhmmss_utc="04:30:00"),
    ]
    df = _df(rows)
    assert tr.available_tax_years(df) == [2025]

    ledger_2025 = tr.build_realized_lot_ledger(df, 2025, today=date(2026, 2, 1))
    assert len(ledger_2025["rows"]) == 1
    assert ledger_2025["rows"][0]["sell_date"] == date(2025, 12, 31)

    ledger_2026 = tr.build_realized_lot_ledger(df, 2026, today=date(2026, 2, 1))
    assert ledger_2026["rows"] == []


def test_et_tax_year_boundary_utc_dec31_evening_is_et_dec31_not_jan1():
    # A late-evening UTC timestamp on Dec 31 is STILL Dec 31 in ET (ET is
    # always behind UTC) — this direction should never misfile into next year.
    rows = [
        _row(1, "QRS", "BUY", 5, 10.0, when=date(2025, 1, 1)),
        _row(2, "QRS", "SELL", 5, 12.0, cost_basis=10.0, realized_pnl=10.0,
             when=date(2025, 12, 31), hhmmss_utc="23:00:00"),
    ]
    df = _df(rows)
    ledger_2025 = tr.build_realized_lot_ledger(df, 2025, today=date(2026, 2, 1))
    assert len(ledger_2025["rows"]) == 1
    assert ledger_2025["rows"][0]["sell_date"] == date(2025, 12, 31)


# ── Reconciliation ───────────────────────────────────────────────────────────

def test_reconciliation_agrees_on_single_lot_full_exit():
    rows = [
        _row(1, "AGR", "BUY", 10, 10.0, when=date(2024, 1, 1)),
        _row(2, "AGR", "SELL", 10, 15.0, cost_basis=10.0, realized_pnl=50.0,
             when=date(2024, 6, 1)),
    ]
    ledger = tr.build_realized_lot_ledger(_df(rows), 2024, today=date(2025, 6, 1))
    assert ledger["reconciles"] is True
    assert ledger["st_gain"] == pytest.approx(50.0)
    assert ledger["stored_realized_total"] == pytest.approx(50.0)


def test_reconciliation_diverges_on_partial_sale_across_different_cost_lots():
    # Two lots at $10 and $20; a partial sale of 5 (all from the $10 lot under
    # FIFO) vs the stored average-cost row, which used average cost $15/sh.
    rows = [
        _row(1, "DIV", "BUY", 5, 10.0, when=date(2024, 1, 1)),
        _row(2, "DIV", "BUY", 5, 20.0, when=date(2024, 1, 2)),
        # Average-cost realized_pnl as the app's own engine would compute it:
        # (30 - 15) * 5 = 75, cost_basis stored per-share = 15.
        _row(3, "DIV", "SELL", 5, 30.0, cost_basis=15.0, realized_pnl=75.0,
             when=date(2024, 1, 3)),
    ]
    ledger = tr.build_realized_lot_ledger(_df(rows), 2024, today=date(2025, 6, 1))
    assert ledger["reconciles"] is False
    # FIFO matches the $10 lot: gain = (30-10)*5 = 100.
    assert ledger["st_gain"] == pytest.approx(100.0)
    assert ledger["stored_realized_total"] == pytest.approx(75.0)


# ── Wash-sale pass-through (no invented logic) ──────────────────────────────

def test_wash_sale_status_mirrors_detector_violation():
    sale_date = date(2024, 6, 1)
    rows = [
        _row(1, "WSV", "BUY", 10, 50.0, when=date(2024, 1, 1)),
        _row(2, "WSV", "SELL", 10, 40.0, cost_basis=50.0, realized_pnl=-100.0, when=sale_date),
        _row(3, "WSV", "BUY", 10, 41.0, when=sale_date + timedelta(days=10)),
    ]
    df = _df(rows)
    today = sale_date + timedelta(days=20)
    ledger = tr.build_realized_lot_ledger(df, 2024, today=today)
    expected = ta.wash_sale_violation_after_harvest("WSV", sale_date, df, today=today)
    assert expected["status"] == "violation"
    assert ledger["rows"][0]["wash_sale_status"] == expected


def test_wash_sale_status_mirrors_detector_pending():
    sale_date = date(2024, 6, 1)
    rows = [
        _row(1, "WSP", "BUY", 10, 50.0, when=date(2024, 1, 1)),
        _row(2, "WSP", "SELL", 10, 40.0, cost_basis=50.0, realized_pnl=-100.0, when=sale_date),
    ]
    df = _df(rows)
    today = sale_date + timedelta(days=5)  # inside the wash-sale window still
    ledger = tr.build_realized_lot_ledger(df, 2024, today=today)
    expected = ta.wash_sale_violation_after_harvest("WSP", sale_date, df, today=today)
    assert expected["status"] == "pending"
    assert ledger["rows"][0]["wash_sale_status"] == expected


def test_wash_sale_status_mirrors_detector_clean():
    sale_date = date(2024, 6, 1)
    rows = [
        _row(1, "WSC", "BUY", 10, 50.0, when=date(2024, 1, 1)),
        _row(2, "WSC", "SELL", 10, 40.0, cost_basis=50.0, realized_pnl=-100.0, when=sale_date),
    ]
    df = _df(rows)
    today = sale_date + timedelta(days=TAX_WASH_SALE_DAYS + 5)
    ledger = tr.build_realized_lot_ledger(df, 2024, today=today)
    expected = ta.wash_sale_violation_after_harvest("WSC", sale_date, df, today=today)
    assert expected["status"] == "clean"
    assert ledger["rows"][0]["wash_sale_status"] == expected


def test_wash_sale_status_is_none_for_a_gain():
    sale_date = date(2024, 6, 1)
    rows = [
        _row(1, "WSG", "BUY", 10, 50.0, when=date(2024, 1, 1)),
        _row(2, "WSG", "SELL", 10, 60.0, cost_basis=50.0, realized_pnl=100.0, when=sale_date),
    ]
    ledger = tr.build_realized_lot_ledger(_df(rows), 2024, today=sale_date + timedelta(days=60))
    assert ledger["rows"][0]["wash_sale_status"] is None


# ── SPLIT holding-period inheritance ─────────────────────────────────────────

def test_split_inherits_original_buy_date_and_pro_rates_cost():
    buy_date = date(2024, 1, 1)
    split_date = date(2024, 6, 1)
    sell_date = date(2025, 1, 5)  # 370 days after buy_date -> long-term
    rows = [
        _row(1, "SPL", "BUY", 10, 10.0, when=buy_date),
        _row(2, "SPL", "SPLIT", 20, 0.0, when=split_date),  # 2-for-1
        _row(3, "SPL", "SELL", 20, 8.0, cost_basis=5.0, realized_pnl=60.0, when=sell_date),
    ]
    ledger = tr.build_realized_lot_ledger(_df(rows), 2025, today=sell_date + timedelta(days=30))
    assert len(ledger["rows"]) == 1
    row = ledger["rows"][0]
    assert row["buy_date"] == buy_date
    assert row["days_held"] == (sell_date - buy_date).days
    assert row["term"] == "LT"
    # cost_per_share halved by the split (10 -> 5); total dollar cost on 20
    # shares == the original 10-share cost basis of $100.
    assert row["cost"] == pytest.approx(100.0)
    assert row["gain"] == pytest.approx(160.0 - 100.0)
    assert ledger["reconciles"] is True  # 60 (FIFO) == 60 (stored)


def test_split_with_no_prior_lots_is_unknown_term():
    rows = [
        _row(1, "SEED", "SPLIT", 20, 0.0, when=date(2024, 1, 1)),
        _row(2, "SEED", "SELL", 20, 8.0, cost_basis=5.0, realized_pnl=60.0,
             when=date(2024, 6, 1)),
    ]
    ledger = tr.build_realized_lot_ledger(_df(rows), 2024, today=date(2025, 6, 1))
    assert len(ledger["rows"]) == 1
    assert ledger["rows"][0]["term"] == "Unknown"
    assert ledger["rows"][0]["gain"] is None
