"""
Shared input loading for the account scorecard ("Is this account earning extra?").

One helper that the Summary card (app.py `_scorecard_compute`) and the Sunday
weekly-debrief email (cron_runner `_run_debrief`) BOTH call, so the two surfaces
can never read different inputs.  docs/plans/account-scorecard.md sections 8 / 9.

Pure plumbing apart from the injected `db`: no streamlit and no
`import stock_analyzer.db` (the db module is passed in, like
`period_inputs.load_engine_inputs`).  Every read is None-on-failure: a loader that
raises (or returns None) yields None for that input, never an empty container, so
`scorecard` reads it as "offline" instead of "nothing happened".  The lenient
loaders (load_account_flows, load_trades, load_daily_snapshots,
load_snaptrade_income_events) are deliberately never used here.

The leverage line's engine inputs are loaded over `scorecard.leverage_window` (whole
months), NOT the week the debrief itself reports on.
"""
from __future__ import annotations

from stock_analyzer import period_inputs
from stock_analyzer import scorecard
from stock_analyzer.constants import (
    LEVERAGE_REFERENCE_TARGET,
    TAX_RATE_LONG_TERM,
    TAX_RATE_SHORT_TERM,
)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def compute(db, today, spy_closes) -> "tuple[dict, dict, dict]":
    """(since, lev, tax) for the scorecard.  Never raises on a failed read: it becomes a
    None input, which the scorecard turns into an "offline" state.

    today       the America/New_York date (market_time.today_et / now_et.date()).
    spy_closes  {date: close} from period_inputs.spy_closes_by_date, or None."""
    flows = _safe(db.load_account_flows_for_dedup_check)
    acct = _safe(db.load_account_daily_snapshots)
    trades = _safe(db.load_trades_or_none)
    win = scorecard.leverage_window(acct, today)
    if win:
        inputs = period_inputs.load_engine_inputs(
            db, *win, trades_df=trades, account_snapshots_df=acct, flows=flows,
        )
    else:
        # No complete month yet: leverage_verdict still needs the account frame in
        # a dict to say "no_complete_month" (a bare None would read as "offline").
        inputs = {"account_snapshots_df": acct}
    since = scorecard.since_start(flows, acct, trades, spy_closes, today=today)
    lev = scorecard.leverage_verdict(
        inputs, spy_closes, today=today, reference_leverage=LEVERAGE_REFERENCE_TARGET,
    )
    tax = scorecard.tax_triggered(
        trades, tax_year=today.year, st_rate=TAX_RATE_SHORT_TERM,
        lt_rate=TAX_RATE_LONG_TERM, today=today,
    )
    return since, lev, tax
