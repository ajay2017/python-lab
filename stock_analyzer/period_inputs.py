"""
Shared input loading + core computation for the period report engine.

One helper that 💰 Account -> Reports (app.py), the in-app Weekly Debrief
"Generate Now" button (app.py) and the Sunday cron's `_run_debrief`
(cron_runner.py) ALL call, so the three can never read different inputs or
run the engine with different arguments.

Pure plumbing: no streamlit and no `import stock_analyzer.db` -- the db module
is passed in (`load_engine_inputs(db, ...)`).  Every read is None-on-failure:
a loader that raises (or returns None) yields None for that key, never an empty
container -- `period_attribution` / `performance_review` treat None as
"offline" and withhold the figure instead of printing a fabricated zero.
"""
from __future__ import annotations

from datetime import timedelta

from stock_analyzer import period_attribution as pa
from stock_analyzer import performance_review as _pr

# Sentinel meaning "load this input yourself".  A value passed explicitly --
# including None -- is used as-is and never reloaded.
_LOAD = object()
LOAD = _LOAD   # public alias for callers (app.py) that load some inputs themselves

# Plumbing, not policy: how far before period_start the daily-snapshot read
# reaches so the d0 session (the trading day before the period) has closes.
# Same window the Reports page has always used.
DAILY_SNAPSHOT_LOOKBACK_DAYS = 14


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def load_engine_inputs(db, period_start, period_end, *, trades_df=_LOAD,
                       account_snapshots_df=_LOAD, flows=_LOAD) -> dict:
    """The five engine inputs, each None when its read failed.  Never raises.

    trades_df           db.load_trades_or_none()
    account_snapshots_df  db.load_account_daily_snapshots()  (UNSCOPED: the
                        session before period_start must be visible)
    daily_snapshots_df  db.load_daily_snapshots_or_none(start - 14d, end)
    flows               db.load_account_flows_for_dedup_check()  (None on
                        failure -- never db.load_account_flows(), which
                        collapses a failed read to [])
    income_events       db.load_snaptrade_income_events_or_none()
    """
    return {
        "trades_df": (_safe(db.load_trades_or_none) if trades_df is _LOAD else trades_df),
        "account_snapshots_df": (_safe(db.load_account_daily_snapshots)
                                 if account_snapshots_df is _LOAD else account_snapshots_df),
        "daily_snapshots_df": _safe(
            db.load_daily_snapshots_or_none,
            period_start - timedelta(days=DAILY_SNAPSHOT_LOOKBACK_DAYS), period_end),
        "flows": (_safe(db.load_account_flows_for_dedup_check) if flows is _LOAD else flows),
        "income_events": _safe(db.load_snaptrade_income_events_or_none),
    }


def spy_closes_by_date(hist) -> "dict | None":
    """{date: close > 0} from a SPY price-history frame, or None when the frame
    is None/empty/has no usable row.  Handles a MultiIndex `Close` column;
    NaN / non-positive closes are dropped.  Keys are `date` objects (the type
    performance_review's SPY lookups expect)."""
    try:
        if hist is None or getattr(hist, "empty", True):
            return None
        close = hist["Close"]
        if hasattr(close, "columns"):   # MultiIndex / multi-ticker frame -> first column
            close = close.iloc[:, 0]
        out: dict = {}
        for idx, val in close.items():
            d = idx.date() if hasattr(idx, "date") else None
            try:
                c = float(val)
            except (TypeError, ValueError):
                continue
            if d is not None and c == c and c > 0:
                out[d] = c
        return out or None
    except Exception:
        return None


def compute_period_core(inputs, period_start, period_end, *, spy_prices_by_date,
                        reference_leverage) -> dict:
    """{period_start, period_end, recon, leverage, account_return}.

    `inputs` None -> recon/leverage/account_return all None (offline).  The
    arguments match what the Reports page has always passed to
    `reconcile_period` / `leverage_section` / `build_review`'s account return,
    so every surface prints the same numbers for the same period."""
    core = {"period_start": period_start, "period_end": period_end,
            "recon": None, "leverage": None, "account_return": None}
    if inputs is None:
        return core
    acct = inputs.get("account_snapshots_df")
    flows = inputs.get("flows")
    core["recon"] = pa.reconcile_period(
        period_start=period_start, period_end=period_end,
        trades_df=inputs.get("trades_df"),
        account_snapshots_df=acct,
        daily_snapshots_df=inputs.get("daily_snapshots_df"),
        flows=flows, income_events=inputs.get("income_events"),
        historical_closes=None,
    )
    core["leverage"] = pa.leverage_section(acct, period_start, period_end, reference_leverage)
    core["account_return"] = _pr.account_return(
        acct, flows, period_start, period_end, spy_prices_by_date)
    return core
