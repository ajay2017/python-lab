"""Cash Activity chart helpers — categories, the unconfirmed-FEE note, and
zero-aligned dual axes. Real values from the owner's 2026 statement."""
import math

import pytest

from stock_analyzer import cash_activity as ca


def _ev(event_type, amount, raw_code=None, ticker=None, txn_id="live-1"):
    return {"event_type": event_type, "amount": amount, "raw_code": raw_code,
            "ticker": ticker, "snaptrade_txn_id": txn_id}


# ─── chart_category ──────────────────────────────────────────────────────

def test_margin_interest_charge_is_its_own_category():
    assert ca.chart_category(_ev("interest", -36.59, "MINT")) == "Margin interest"


def test_gold_credit_is_interest_earned_not_netted():
    assert ca.chart_category(_ev("interest", 29.45, "GMPC")) == "Interest earned"


def test_dividend_and_fee():
    assert ca.chart_category(_ev("dividend", 0.75, "CDIV")) == "Dividend"
    assert ca.chart_category(_ev("fee", -50.0, "GOLD")) == "Fee"


@pytest.mark.parametrize("amount", [0, 0.0, None, "x", float("nan"), float("inf")])
def test_unplaceable_amounts_return_none(amount):
    assert ca.chart_category(_ev("interest", amount)) is None


def test_unknown_event_type_returns_none():
    assert ca.chart_category(_ev("transfer", 10.0)) is None


def test_june_2026_no_longer_nets_to_seven_dollars():
    """The bug this module fixes: the charge and the credit stay separate."""
    june = [_ev("interest", -36.59, "MINT"), _ev("interest", 29.45, "GMPC"), _ev("interest", 0.06, "INT")]
    totals = {}
    for e in june:
        totals[ca.chart_category(e)] = round(totals.get(ca.chart_category(e), 0.0) + e["amount"], 2)
    assert totals == {"Margin interest": -36.59, "Interest earned": 29.51}


# ─── reclassified_broker_fees ────────────────────────────────────────────

def test_only_rows_marked_reclassified_are_listed():
    promoted = {**_ev("interest", -61.01, "FEE", txn_id="a39c851e"), "reclassified_from": "fee"}
    events = [
        promoted,
        _ev("fee", -50.0, "GOLD", txn_id="csv:2026-01-05:GOLD::-5000"),
        _ev("interest", -39.69, "MINT", txn_id="csv:2026-08-25:MINT::-3969"),
        {**_ev("dividend", 1.0, "CDIV"), "reclassified_from": float("nan")},  # pandas fill
    ]
    assert ca.reclassified_broker_fees(events) == [promoted]


def test_reclassified_fees_empty_input():
    assert ca.reclassified_broker_fees([]) == []
    assert ca.reclassified_broker_fees(None) == []


def test_a_reclassified_fee_charts_as_margin_interest():
    promoted = {**_ev("interest", -61.01, "FEE"), "reclassified_from": "fee"}
    assert ca.chart_category(promoted) == "Margin interest"


# ─── range_start / period_start / period_label ───────────────────────────

from datetime import date


def test_last_12_months_is_twelve_whole_month_buckets():
    assert ca.range_start("Last 12 months", date(2026, 10, 1)) == date(2025, 11, 1)
    assert ca.range_start("Last 12 months", date(2026, 12, 31)) == date(2026, 1, 1)
    assert ca.range_start("Last 12 months", date(2026, 11, 15)) == date(2025, 12, 1)


def test_range_start_snaps_to_a_whole_first_bucket():
    """Nov 2025 would make "Q4 2025" a 2-month bucket and "2025" an
    11-month-short year — snap back so every bucket shown is whole."""
    today = date(2026, 10, 1)
    assert ca.range_start("Last 12 months", today, "Quarterly") == date(2025, 10, 1)
    assert ca.range_start("Last 12 months", today, "Yearly") == date(2025, 1, 1)
    assert ca.range_start("This year", today, "Quarterly") == date(2026, 1, 1)


def test_this_year_and_all_time():
    assert ca.range_start("This year", date(2026, 10, 1)) == date(2026, 1, 1)
    assert ca.range_start("All time", date(2026, 10, 1)) is None


@pytest.mark.parametrize("d,grouping,start,label", [
    (date(2026, 9, 24), "Monthly", date(2026, 9, 1), "Sep 2026"),
    (date(2026, 9, 24), "Quarterly", date(2026, 7, 1), "Q3 2026"),
    (date(2026, 1, 5), "Quarterly", date(2026, 1, 1), "Q1 2026"),
    (date(2026, 12, 31), "Quarterly", date(2026, 10, 1), "Q4 2026"),
    (date(2026, 9, 24), "Yearly", date(2026, 1, 1), "2026"),
])
def test_period_bucketing(d, grouping, start, label):
    assert ca.period_start(d, grouping) == start
    assert ca.period_label(start, grouping) == label


# ─── zero_aligned_ranges ─────────────────────────────────────────────────

def _zero_frac(r):
    lo, hi = r
    return -lo / (hi - lo)


def test_zero_lines_align_and_no_data_is_clipped():
    bars, line = ca.zero_aligned_ranges(-61.01, 54.0, -1076.81, 2149.50)
    assert math.isclose(_zero_frac(bars), _zero_frac(line), rel_tol=1e-9)
    assert bars[0] <= -61.01 and bars[1] >= 54.0
    assert line[0] <= -1076.81 and line[1] >= 2149.50


def test_all_positive_bars_still_get_a_visible_zero():
    bars, line = ca.zero_aligned_ranges(0.0, 80.0, 0.0, 500.0)
    assert bars[0] < 0 < bars[1] and line[0] < 0 < line[1]
    assert math.isclose(_zero_frac(bars), _zero_frac(line), rel_tol=1e-9)


def test_all_negative_axis_does_not_crash():
    bars, line = ca.zero_aligned_ranges(-40.0, 0.0, -100.0, 200.0)
    assert bars[0] <= -40.0 and bars[1] > 0
    assert math.isclose(_zero_frac(bars), _zero_frac(line), rel_tol=1e-9)


def test_empty_axis_and_non_finite_inputs():
    bars, line = ca.zero_aligned_ranges(0, 0, float("nan"), None)
    assert bars[0] < 0 < bars[1] and line[0] < 0 < line[1]
    assert math.isclose(_zero_frac(bars), _zero_frac(line), rel_tol=1e-9)
