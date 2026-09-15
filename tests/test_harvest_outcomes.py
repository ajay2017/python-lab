"""Tests for the F-13-adjacent tax-harvest outcome measurement pieces added to
stock_analyzer/tax_advisor.py: the TAX_HARVEST running-total
(`harvest_outcomes_summary`) and the wash-sale AFTER-side compliance check
(`wash_sale_violation_after_harvest`). See
docs/plans/recommendation-outcomes-measurement.md §10 item 3 / §11.

Pure computation (date math + pandas), no I/O. Awareness-only — neither
function may raise or gate anything.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer import tax_advisor as ta
from stock_analyzer.constants import (
    TAX_RATE_LONG_TERM,
    TAX_RATE_SHORT_TERM,
    TAX_STCG_THRESHOLD_DAYS,
    TAX_WASH_SALE_DAYS,
)

pytestmark = pytest.mark.fast


def _row(ticker, traded_at, action, shares=10.0, realized_pnl=None,
         trigger_type=None, idx=0):
    return {
        "id": idx, "ticker": ticker, "traded_at": traded_at.isoformat(),
        "action": action, "shares": shares, "realized_pnl": realized_pnl,
        "trigger_type": trigger_type,
    }


def _df(rows):
    return pd.DataFrame(rows)


# ── harvest_outcomes_summary ────────────────────────────────────────────

def test_harvest_no_qualifying_rows_returns_zero_dict():
    trades = _df([_row("AAA", date(2026, 1, 1), "BUY", idx=0)])
    result = ta.harvest_outcomes_summary(trades)
    assert result == {
        "total_harvested_loss": 0.0,
        "estimated_tax_saved": 0.0,
        "n_events": 0,
        "since_date": None,
    }


def test_harvest_none_trades_df_is_zero_safe():
    result = ta.harvest_outcomes_summary(None)
    assert result["n_events"] == 0
    assert result["since_date"] is None


def test_harvest_empty_trades_df_is_zero_safe():
    result = ta.harvest_outcomes_summary(pd.DataFrame())
    assert result["n_events"] == 0


def test_harvest_missing_columns_is_zero_safe():
    # No trigger_type / action columns at all — must not crash.
    trades = pd.DataFrame([{"ticker": "AAA", "shares": 10}])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 0


def test_harvest_short_term_loss_applies_short_term_rate():
    buy_date  = date(2026, 1, 1)
    sale_date = buy_date + timedelta(days=50)  # well under STCG threshold
    trades = _df([
        _row("AAA", buy_date, "BUY", idx=0),
        _row("AAA", sale_date, "SELL", realized_pnl=-1000.0,
             trigger_type="TAX_HARVEST", idx=1),
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 1
    assert result["total_harvested_loss"] == 1000.0
    assert result["estimated_tax_saved"] == round(1000.0 * TAX_RATE_SHORT_TERM, 2)
    assert result["since_date"] == str(sale_date)


def test_harvest_long_term_loss_applies_long_term_rate():
    buy_date  = date(2024, 1, 1)
    sale_date = buy_date + timedelta(days=TAX_STCG_THRESHOLD_DAYS + 50)
    trades = _df([
        _row("BBB", buy_date, "BUY", idx=0),
        _row("BBB", sale_date, "SELL", realized_pnl=-2000.0,
             trigger_type="TAX_HARVEST", idx=1),
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 1
    assert result["total_harvested_loss"] == 2000.0
    assert result["estimated_tax_saved"] == round(2000.0 * TAX_RATE_LONG_TERM, 2)


def test_harvest_gains_excluded():
    buy_date  = date(2026, 1, 1)
    sale_date = buy_date + timedelta(days=50)
    trades = _df([
        _row("CCC", buy_date, "BUY", idx=0),
        _row("CCC", sale_date, "SELL", realized_pnl=500.0,   # a GAIN, tagged harvest anyway
             trigger_type="TAX_HARVEST", idx=1),
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 0
    assert result["total_harvested_loss"] == 0.0


def test_harvest_zero_pnl_excluded():
    buy_date  = date(2026, 1, 1)
    sale_date = buy_date + timedelta(days=50)
    trades = _df([
        _row("CCC", buy_date, "BUY", idx=0),
        _row("CCC", sale_date, "SELL", realized_pnl=0.0,
             trigger_type="TAX_HARVEST", idx=1),
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 0


def test_harvest_non_tax_harvest_trigger_excluded_even_if_loss():
    buy_date  = date(2026, 1, 1)
    sale_date = buy_date + timedelta(days=50)
    trades = _df([
        _row("DDD", buy_date, "BUY", idx=0),
        _row("DDD", sale_date, "SELL", realized_pnl=-800.0,
             trigger_type="STOP_HIT", idx=1),   # a loss, but NOT tax-harvest-tagged
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 0
    assert result["total_harvested_loss"] == 0.0


def test_harvest_mix_of_qualifying_and_disqualifying_rows():
    trades = _df([
        _row("AAA", date(2026, 1, 1), "BUY", idx=0),
        _row("AAA", date(2026, 2, 20), "SELL", realized_pnl=-1000.0,
             trigger_type="TAX_HARVEST", idx=1),                      # qualifies (loss)
        _row("BBB", date(2026, 1, 1), "BUY", idx=2),
        _row("BBB", date(2026, 2, 20), "SELL", realized_pnl=500.0,
             trigger_type="TAX_HARVEST", idx=3),                      # excluded (gain)
        _row("CCC", date(2026, 1, 1), "BUY", idx=4),
        _row("CCC", date(2026, 2, 20), "SELL", realized_pnl=-300.0,
             trigger_type="STOP_HIT", idx=5),                         # excluded (wrong trigger)
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 1
    assert result["total_harvested_loss"] == 1000.0


def test_harvest_since_date_is_earliest_qualifying_sale():
    trades = _df([
        _row("AAA", date(2026, 1, 1), "BUY", idx=0),
        _row("AAA", date(2026, 3, 1), "SELL", realized_pnl=-500.0,
             trigger_type="TAX_HARVEST", idx=1),
        _row("BBB", date(2026, 1, 1), "BUY", idx=2),
        _row("BBB", date(2026, 2, 1), "SELL", realized_pnl=-500.0,   # earlier than AAA's sale
             trigger_type="TAX_HARVEST", idx=3),
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 2
    assert result["since_date"] == str(date(2026, 2, 1))


def test_harvest_malformed_realized_pnl_skips_row_without_crashing():
    trades = _df([
        _row("AAA", date(2026, 1, 1), "BUY", idx=0),
        _row("AAA", date(2026, 2, 1), "SELL", realized_pnl="not-a-number",
             trigger_type="TAX_HARVEST", idx=1),
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 0


def test_harvest_unparseable_date_skips_row_without_crashing():
    trades = pd.DataFrame([
        {"id": 0, "ticker": "AAA", "traded_at": "not-a-date", "action": "SELL",
         "shares": 10, "realized_pnl": -100.0, "trigger_type": "TAX_HARVEST"},
    ])
    result = ta.harvest_outcomes_summary(trades)
    assert result["n_events"] == 0


# ── wash_sale_violation_after_harvest ───────────────────────────────────

SALE_DATE = date(2026, 6, 1)


def _wash_trades(rows):
    """rows: list of (idx, ticker, days_after_sale, action)."""
    return pd.DataFrame([
        {"id": i, "ticker": t, "traded_at": (SALE_DATE + timedelta(days=d)).isoformat(),
         "action": a, "shares": 5}
        for i, t, d, a in rows
    ])


def test_wash_after_violation_at_exact_boundary_day30():
    trades = _wash_trades([(0, "AAA", TAX_WASH_SALE_DAYS, "BUY")])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45),
    )
    assert result["status"] == "violation"
    assert result["days_after"] == TAX_WASH_SALE_DAYS


def test_wash_after_day31_is_not_a_violation():
    trades = _wash_trades([(0, "AAA", TAX_WASH_SALE_DAYS + 1, "BUY")])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45),
    )
    assert result["status"] == "clean"


def test_wash_after_window_still_open_with_no_rebuy_is_pending():
    trades = _wash_trades([])  # no BUY rows at all
    elapsed = 10
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=elapsed),
    )
    assert result["status"] == "pending"
    assert result["days_remaining"] == TAX_WASH_SALE_DAYS - elapsed


def test_wash_after_window_fully_elapsed_no_rebuy_is_clean():
    trades = _wash_trades([])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=TAX_WASH_SALE_DAYS + 5),
    )
    assert result["status"] == "clean"


def test_wash_after_violation_detected_even_while_window_still_open():
    # A rebuy on day 5 is already a confirmed violation — no need to wait
    # for the full window to elapse before reporting it.
    trades = _wash_trades([(0, "AAA", 5, "BUY")])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=10),
    )
    assert result["status"] == "violation"
    assert result["days_after"] == 5


def test_wash_after_same_day_rebuy_is_violation():
    trades = _wash_trades([(0, "AAA", 0, "BUY")])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45),
    )
    assert result["status"] == "violation"
    assert result["days_after"] == 0


def test_wash_after_ignores_sell_rows():
    trades = _wash_trades([(0, "AAA", 10, "SELL")])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45),
    )
    assert result["status"] == "clean"


def test_wash_after_uses_earliest_violating_rebuy():
    trades = _wash_trades([(0, "AAA", 20, "BUY"), (1, "AAA", 5, "BUY")])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45),
    )
    assert result["status"] == "violation"
    assert result["days_after"] == 5


def test_wash_after_case_insensitive_ticker():
    trades = _wash_trades([(0, "aaa", 10, "BUY")])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45),
    )
    assert result["status"] == "violation"


def test_wash_after_none_trades_df_is_safe():
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, None, today=SALE_DATE + timedelta(days=45),
    )
    assert result["status"] == "clean"


def test_wash_after_empty_trades_df_is_safe():
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, pd.DataFrame(), today=SALE_DATE + timedelta(days=10),
    )
    assert result["status"] == "pending"


def test_wash_after_different_ticker_not_counted():
    trades = _wash_trades([(0, "ZZZ", 5, "BUY")])
    result = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45),
    )
    assert result["status"] == "clean"


def test_wash_after_custom_window():
    trades = _wash_trades([(0, "AAA", 40, "BUY")])
    # Past the default 30-day window...
    result_default = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45),
    )
    assert result_default["status"] == "clean"
    # ...but within a wider custom window.
    result_wide = ta.wash_sale_violation_after_harvest(
        "AAA", SALE_DATE, trades, today=SALE_DATE + timedelta(days=45), window_days=45,
    )
    assert result_wide["status"] == "violation"
