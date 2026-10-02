"""
Performance Review — Phase 2 of the 📄 Reports feature (docs/plans/reports.md).

A point-in-time, archivable snapshot for an arbitrary date range: return vs
SPY, recommendation calls acted/skipped, gates that fired, trade-behavior
trend, and leverage/risk drift — all assembled by SLICING/FILTERING what
already-shipped readout modules produce, never by recomputing a number a
live page already owns (docs/plans/reports.md Phase 2 "Risks" section, the
double-decide hazard).

This module's ONLY new logic is: date-slicing an input to a window, handing
the slice to an existing pure delegate, tagging a below-floor period, and the
one genuinely new comparison (SPY period return vs realized trade P&L closed
in the window). It never reimplements alpha, banding, or FIFO/hold-day
matching:
  - `recs`  delegates to `rec_events_readout.enrich_and_grade`/
            `grade_by_rec_type` on `rec_events_rows` filtered to
            `fired_date ∈ [period_start, period_end]`.
  - `gates` delegates to `gate_ledger_readout.enrich_and_grade`/
            `grade_by_gate` on `gate_rows` filtered to
            `rec_date ∈ [period_start, period_end]`.
  - `trade_behavior` delegates to `trade_analytics.build_monthly_trend`/
            `build_trigger_breakdown`, called on `compute_extended_stats`'s
            OUTPUT frame filtered by its own `_dt` column — never on a
            pre-filtered input `trades` frame, or a SELL's nearest-preceding-
            BUY hold-day match (which may sit before the window) breaks.
  - `return_vs_spy` shows SPY's period return (via
            `benchmark_mirror.price_on_or_before`) alongside the return on
            capital CYCLED through trades closed inside the window —
            explicitly labeled `basis="realized_only"`, since unrealized
            moves on positions still open during the window are excluded.
            **2026-10-01 reframe: these two figures are deliberately NOT
            presented as a comparison anymore** (no `delta_vs_spy_pp` is
            computed). The cycled-capital figure's denominator sums cost
            basis across every closed lot, so capital reused across several
            round trips is counted once per trade — on real data this made
            the figure read closer to an average per-trade return than a
            period return, and a vs-SPY delta badge on it gave the OPPOSITE
            verdict from the account's real return (see memory
            `project_q3_2026_return_review`). A real account-level return
            vs SPY is designed, not built (memory
            `project_performance_review_return_tile_redesign`).
  - `leverage_drift`/`risk_drift` are a first-vs-last-in-window read of the
            already-recorded `account_daily_snapshots`/`portfolio_risk_
            snapshots` columns — no recomputation, and specifically NOT
            `capital_vs_margin.py` (a heavy backward-reconstruction engine
            the owner ruled out for this feature).

`fired_date`/`rec_date` are already America/New_York calendar dates at write
time (`rec_events_capture._fired_date_str`/`gate_ledger.py`'s equivalent both
stamp `_today_et()`-derived dates, never a raw UTC timestamp) — so no further
timezone conversion is applied to those two columns, only to `trades.
traded_at` (a genuine `timestamptz`), mirroring `tax_report.py`'s own ET-vs-
UTC judgment call for the identical column.

Below-floor framing (owner decision, 2026-09-18): a period under the
standalone pages' 8-call/5-ticker floors NEVER renders an independent
"building"/"early"/"firm" band here — that banding vocabulary is reserved for
the all-time 🛑 The Road Not Taken / 🎖️ Recommendation Outcomes pages, and a
quarter-scoped review would almost always sit below those floors. Instead,
every `recs`/`gates` entry carries a `below_floor: bool` tag alongside its RAW
`n_calls`/`n_distinct_tickers` (and, for `gates`, the same descriptive
`mean_alpha_pct` `grade_by_gate` already computes regardless of band). The
underlying `band` string is still carried through for anyone inspecting the
dict, but callers (the `app.py` render layer and the two format functions
below) must never surface it as a headline.

Self/Protective track records (`self_track_record.py`/
`protective_track_record.py`) are deliberately EXCLUDED — both are all-time
behavioral measures with their own per-trade maturity floors, not sub-window
measures; period-scoping them would either mislead or just re-show a
permanent "building" band.

Pure — no Streamlit, no DB, no network I/O. Every dependency (trades, rec
events, gate suppressions, snapshot frames, SPY history, a historical-close
fetcher, the protective-call ticker set) is injected by the `app.py` caller.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

import pandas as pd

from stock_analyzer import gate_ledger_readout, rec_events_readout
from stock_analyzer.account import money_weighted_return
from stock_analyzer.benchmark_mirror import price_on_or_before
from stock_analyzer.constants import ALERT_EOD_HOUR_ET, NYSE_HOLIDAYS
from stock_analyzer.trade_analytics import (
    build_monthly_trend, build_trigger_breakdown, compute_extended_stats,
)

_REC_TYPES = (
    "rebal_trim", "beta_trim", "diversify_add",
    # 2026-09-21 app-review Part 2 #1 additions.
    "single_name_concentration", "sector_concentration",
)
_ARMS = ("acted", "skipped")


# ── small shared helpers (mirror tax_report.py's own style) ─────────────────

def _opt(val):
    """None-preserving float coercion. Returns None for None / NaN / non-numeric."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def _to_date(v) -> "date | None":
    """Best-effort date coerce — mirrors rec_events_readout._to_date /
    gate_ledger_readout._to_date exactly (each readout module keeps its own
    trivial copy rather than sharing a private cross-module import; this
    module follows the same established convention). Unparseable input ->
    None, never a fabricated evaluable date."""
    if v is None:
        return None
    try:
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _et_date_from_parsed(dt) -> "date | None":
    """Convert a `trade_analytics.compute_extended_stats` `_dt` value (a
    parsed, possibly tz-aware pandas Timestamp) to its America/New_York
    calendar date. `trades.traded_at` is a genuine UTC `timestamptz`, so a
    late-evening ET trade must not roll into "tomorrow" by staying in UTC —
    same reasoning as `tax_report._et_date`. A naive (no-tzinfo) timestamp is
    treated as already-UTC before converting, matching `rec_events_readout.
    _traded_date_et`'s convention for the same column."""
    if dt is None or pd.isna(dt):
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        return dt.tz_convert("America/New_York").date()
    except Exception:
        return None


def _cash_as_of_et_timestamp(v) -> "pd.Timestamp | None":
    """Full America/New_York timestamp of a `cash_as_of` timestamptz value
    (DB rows arrive as ISO strings; may already be a Timestamp in a test
    fixture)."""
    if v is None:
        return None
    try:
        ts = pd.Timestamp(v)
        if pd.isna(ts):
            return None
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert("America/New_York")
    except Exception:
        return None


def _is_trading_day(d: date) -> bool:
    """Weekday AND not an NYSE holiday. Re-implements `data.is_trading_day`'s
    exact logic locally rather than importing `stock_analyzer.data` (which
    pulls in yfinance/providers at module level) — this module stays
    pure/I-O-free per its own docstring."""
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def _prior_trading_day(d: date) -> date:
    """The NYSE session immediately before `d`."""
    prev = d - timedelta(days=1)
    while not _is_trading_day(prev):
        prev -= timedelta(days=1)
    return prev


def _last_trading_day_on_or_before(d: date) -> date:
    cur = d
    while not _is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def _next_trading_day(d: date) -> date:
    """The NYSE session immediately after `d`."""
    nxt = d + timedelta(days=1)
    while not _is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def _valid_snapshot_row(row) -> bool:
    """A usable account_daily_snapshots anchor for a real return calculation:
    `net_equity` present, and `cash_as_of` dated the SAME ET calendar date as
    `snapshot_date` — never a stale cash balance carried forward from an
    earlier sync (e.g. 2026-09-23's cash_balance, which the live data showed
    was correctly unchanged from 9/22 because no trade happened, not because
    the read was stale; this check still catches a genuinely stale read on a
    day that SHOULD have moved).

    Does NOT reject a day merely because a trade happened on it. The
    original design worried the EOD cron pairs end-of-day holdings with a
    POSSIBLY-midday cash balance, so any same-day trade could skew
    net_equity. A live 2026-10-01 audit disproved that for a cash read taken
    AFTER close: chaining a real Robinhood statement (8/31 close) forward
    through 10 real trading days of real transactions landed on the recorded
    9/10 cash_balance to the exact penny, and the full September activity
    log matched the recorded day-over-day cash changes to the cent on 11 of
    14 days (the other 3 explained by one dividend posting a day late) — see
    memory `project_performance_review_return_tile_redesign`. **The audit
    proved "a post-close read reflects that day's trades," not "any read
    sharing the calendar date does"** — the broker-sync lane fires roughly
    twice a day (noon and evening ET); if the evening sync fails and the
    noon one succeeded, `cash_as_of` still shares `snapshot_date`'s ET
    calendar date while missing the day's afternoon trades entirely, on a
    ~3x-levered book where one unreflected trade can be a large fraction of
    net_equity. So the rule below ALSO requires `cash_as_of` to be at or
    after market close (`ALERT_EOD_HOUR_ET`) in ET on `snapshot_date` —
    enforcing the premise the evidence actually supports, not assuming a
    3-week sample covers a failed-evening-sync day too. Module-local
    data-integrity check, not an investment-policy threshold (same
    precedent as `account._ANNUALIZE_CAVEAT_MAX_DAYS`)."""
    if _opt(row.get("net_equity")) is None:
        return False
    snap_d = _to_date(row.get("snapshot_date"))
    cash_ts = _cash_as_of_et_timestamp(row.get("cash_as_of"))
    if snap_d is None or cash_ts is None:
        return False
    return cash_ts.date() == snap_d and cash_ts.hour >= ALERT_EOD_HOUR_ET


def _spy_period_return(spy_prices_by_date: "dict | None", start: date, end: date) -> "float | None":
    """SPY % return from `start` to `end`, using the nearest trading-day
    close on/before each date (`benchmark_mirror.price_on_or_before`).
    `spy_prices_by_date` here uses the SAME {date: close} shape the
    rec_events_readout/gate_ledger_readout call sites already build (date-
    object keys) — converted to the {iso_date_str: close} shape
    `price_on_or_before` actually expects, since the two existing readout
    modules and `benchmark_mirror` were each built independently and never
    needed to agree on a key type until now. None when the series is missing
    or doesn't cover the window."""
    if not spy_prices_by_date:
        return None
    str_prices = {
        (d.isoformat() if hasattr(d, "isoformat") else str(d)): px
        for d, px in spy_prices_by_date.items()
    }
    p0 = price_on_or_before(str_prices, start)
    p1 = price_on_or_before(str_prices, end)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return round((p1 / p0 - 1) * 100, 2)


def _windowed_ext_df(trades: "pd.DataFrame | None", start: date, end: date):
    """Returns (status, windowed_ext_df). `status` in {"offline","empty","ok"}.

    Computes `compute_extended_stats` on the FULL `trades` frame first, THEN
    filters the OUTPUT by its own `_dt` column converted to an ET date —
    never filters the input `trades` frame first, which would destroy a
    SELL's nearest-preceding-BUY `hold_days` match whenever that BUY sits
    before `start` (docs/plans/reports.md Phase 2 risk section)."""
    if trades is None:
        return "offline", pd.DataFrame()
    ext_df = compute_extended_stats(trades)
    if ext_df.empty:
        return "empty", ext_df
    ext_df = ext_df.copy()
    ext_df["_et_date"] = ext_df["_dt"].apply(_et_date_from_parsed)
    windowed = ext_df[
        ext_df["_et_date"].apply(lambda d: d is not None and start <= d <= end)
    ]
    if windowed.empty:
        return "empty", windowed
    return "ok", windowed


# ── recs / gates period-scoping ──────────────────────────────────────────────

def _build_recs_section(
    rec_events_rows: "list[dict] | None",
    *,
    period_start: date,
    period_end: date,
    today: date,
    trades_records: "list[dict]",
    protective_call_tickers: "set[str]",
    rec_risk_snapshot_by_date: "dict | None",
    spy_prices_by_date: "dict | None",
    historical_close_fn,
    rec_min_calls: int,
    rec_firm_calls: int,
    rec_min_tickers: int,
    rec_horizon_days: int,
    rec_action_window_days: int,
) -> dict:
    if rec_events_rows is None:
        return {"status": "offline", "by_type": [], "enriched_rows": []}

    collapsed = rec_events_readout.collapse_by_rec_ticker(rec_events_rows)
    windowed = []
    for r in collapsed:
        d = _to_date(r.get("fired_date"))
        if d is not None and period_start <= d <= period_end:
            windowed.append(r)
    if not windowed:
        return {"status": "empty", "by_type": [], "enriched_rows": []}

    enriched = rec_events_readout.enrich_and_grade(
        windowed,
        today=today,
        trades=trades_records,
        horizon_trading_days=rec_horizon_days,
        action_window_trading_days=rec_action_window_days,
        protective_call_tickers=protective_call_tickers,
        risk_snapshot_by_date=rec_risk_snapshot_by_date,
        spy_close_by_date=spy_prices_by_date,
        historical_close_fn=historical_close_fn,
    )

    by_type: "list[dict]" = []
    for rt in _REC_TYPES:
        for arm in _ARMS:
            g = rec_events_readout.grade_by_rec_type(
                enriched, rec_type=rt, arm=arm,
                min_calls=rec_min_calls, firm_calls=rec_firm_calls,
                min_tickers=rec_min_tickers,
            )
            g["below_floor"] = g["n_calls"] < rec_min_calls or g["n_distinct_tickers"] < rec_min_tickers
            g["footnotes"] = rec_events_readout.readout_footnotes(rt)
            if rt == "diversify_add":
                # The only rec_type with a genuine alpha concept (§11's hard
                # redline forbids porting this shape onto rebal_trim/
                # beta_trim's portfolio-metric-delta grading) — a descriptive
                # mean over this arm's own matured rows, shown regardless of
                # `below_floor` per the owner's decision 2.
                _alphas = [
                    r["outcome"]["leg_a_candidate_alpha_pct"]
                    for r in enriched
                    if r.get("rec_type") == rt
                    and r.get("status") == rec_events_readout.STATUS_MATURED
                    and bool(r.get("acted")) == (arm == "acted")
                    and isinstance(r.get("outcome"), dict)
                    and r["outcome"].get("leg_a_candidate_alpha_pct") is not None
                ]
                g["mean_leg_a_alpha_pct"] = round(sum(_alphas) / len(_alphas), 2) if _alphas else None
            else:
                g["mean_leg_a_alpha_pct"] = None
            by_type.append(g)

    return {"status": "ok", "by_type": by_type, "enriched_rows": enriched}


def _build_gates_section(
    gate_rows: "list[dict] | None",
    *,
    period_start: date,
    period_end: date,
    today: date,
    spy_prices_by_date: "dict | None",
    historical_close_fn,
    gate_min_calls: int,
    gate_firm_calls: int,
    gate_min_tickers: int,
    gate_horizon_days: int,
    composite_buy: float,
    gate_ids: "tuple[str, ...]",
) -> dict:
    if gate_rows is None:
        return {"status": "offline", "by_gate": [], "enriched_rows": []}

    windowed = []
    for r in gate_rows:
        d = _to_date(r.get("rec_date"))
        if d is not None and period_start <= d <= period_end:
            windowed.append(r)
    if not windowed:
        return {"status": "empty", "by_gate": [], "enriched_rows": []}

    enriched = gate_ledger_readout.enrich_and_grade(
        windowed,
        today=today,
        spy_close_by_date=spy_prices_by_date or {},
        historical_close_fn=historical_close_fn,
        horizon_trading_days=gate_horizon_days,
        composite_buy=composite_buy,
    )
    graded = gate_ledger_readout.grade_by_gate(
        enriched, gate_ids=gate_ids,
        min_calls=gate_min_calls, firm_calls=gate_firm_calls,
        min_tickers=gate_min_tickers,
    )
    for g in graded:
        if g.get("market_wide"):
            g["below_floor"] = False
        else:
            g["below_floor"] = (
                g["n_matured_evaluable"] < gate_min_calls
                or g["n_distinct_tickers_evaluable"] < gate_min_tickers
            )
        g["footnotes"] = gate_ledger_readout.readout_footnotes(g["gate_id"])

    return {"status": "ok", "by_gate": graded, "enriched_rows": enriched}


# ── main entry point ─────────────────────────────────────────────────────────

def build_review(
    *,
    period_start: date,
    period_end: date,
    today: date,
    trades: "pd.DataFrame | None",
    rec_events_rows: "list[dict] | None",
    gate_rows: "list[dict] | None",
    account_snapshots_df: "pd.DataFrame | None",
    account_return_snapshots_df: "pd.DataFrame | None",
    account_flows_rows: "list[dict] | None",
    risk_snapshots_df: "pd.DataFrame | None",
    spy_prices_by_date: "dict | None",
    historical_close_fn: "Callable[[str, date, date], float | None] | None",
    rec_risk_snapshot_by_date: "dict | None",
    protective_call_tickers: "set[str] | None",
    rec_min_calls: int,
    rec_firm_calls: int,
    rec_min_tickers: int,
    rec_horizon_days: int,
    rec_action_window_days: int,
    gate_min_calls: int,
    gate_firm_calls: int,
    gate_min_tickers: int,
    gate_horizon_days: int,
    composite_buy: float,
    gate_ids: "tuple[str, ...]",
) -> "dict | None":
    """Assemble a point-in-time Performance Review for `[period_start,
    period_end]` (both inclusive, ET-based where the underlying data is a
    genuine timestamp).

    Returns `None` only when `period_start > period_end` (an invalid range).
    Otherwise always returns a dict with `period_start`, `period_end`, and
    six sections — `return_vs_spy`, `trade_behavior`, `recs`, `gates`,
    `leverage_drift`, `risk_drift` — each independently carrying its own
    `status` in `{"offline","empty","ok"}`. One section reading `"offline"`
    (its underlying loader arg was `None`) never forces another section
    offline — every section is graded from its OWN inputs only.

    All I/O is caller-injected; this function does no loading of its own.
    """
    if period_start > period_end:
        return None

    protective_call_tickers = protective_call_tickers or set()
    trades_records = (
        trades.to_dict("records") if trades is not None and not trades.empty else []
    )

    # ── trade_behavior + return_vs_spy share one windowed ext_df ────────────
    _tb_status, _tb_windowed = _windowed_ext_df(trades, period_start, period_end)

    if _tb_status == "offline":
        trade_behavior = {"status": "offline"}
    elif _tb_status == "empty":
        trade_behavior = {
            "status": "empty", "n_trades": 0, "total_realized_pnl": 0.0,
            "win_rate_pct": None, "trigger_breakdown": [], "monthly_trend": [],
            "trades": [],
        }
    else:
        _n = len(_tb_windowed)
        _wins = int((_tb_windowed["realized_pnl"] > 0).sum())
        # Per-trade rows (ticker/sell_date/realized_pnl/pnl_pct/hold_days/
        # trigger_type) straight off the already-windowed ext_df — purely an
        # export/audit convenience, no new computation. Also what makes
        # `hold_days` preservation (a SELL inside the window whose matching
        # BUY sits before it) directly testable through the public API,
        # rather than reaching into the private `_windowed_ext_df` helper.
        _trade_rows = _tb_windowed.rename(columns={"_et_date": "sell_date"})[
            ["ticker", "sell_date", "realized_pnl", "pnl_pct", "hold_days", "trigger_type"]
        ].to_dict("records")
        trade_behavior = {
            "status": "ok",
            "n_trades": _n,
            "total_realized_pnl": round(float(_tb_windowed["realized_pnl"].sum()), 2),
            "win_rate_pct": round(_wins / _n * 100, 1) if _n else None,
            "trigger_breakdown": build_trigger_breakdown(_tb_windowed).to_dict("records"),
            "monthly_trend": build_monthly_trend(_tb_windowed).to_dict("records"),
            "trades": _trade_rows,
        }

    # ── return_vs_spy ────────────────────────────────────────────────────────
    if trades is None or spy_prices_by_date is None:
        return_vs_spy = {"status": "offline", "basis": "realized_only"}
    else:
        _spy_ret = _spy_period_return(spy_prices_by_date, period_start, period_end)
        _n_realized = 0 if _tb_status != "ok" else len(_tb_windowed)
        _realized_pnl_total = (
            0.0 if _tb_status != "ok" else round(float(_tb_windowed["realized_pnl"].sum()), 2)
        )
        # Return on capital CYCLED through closed trades, not a quarterly
        # account return. 2026-10-01 reframe: the owner's real Q3 account
        # return (confirmed against a Robinhood statement) was +19.44%,
        # while this figure showed +0.49% "vs SPY -2.03pp" — because the
        # denominator is the SUMMED cost basis of every closed lot, which
        # double/triple/N-counts the same recycled capital on each round
        # trip (84 trades on ~$5-8k of capital summed to ~$95k of "deployed"
        # cost basis that quarter). So this is closer to an average
        # per-trade return than a period return, and is NOT directly
        # comparable to SPY's own period return — a vs-SPY delta badge
        # (the field used to be called delta_vs_spy_pp) is deliberately NOT
        # computed here anymore; a real account-level figure, where one is
        # available, is a separate piece of work (see memory
        # project_performance_review_return_tile_redesign), not this module.
        if _tb_status == "ok":
            _total_cost_basis = float((_tb_windowed["cost_basis"] * _tb_windowed["shares"]).sum())
        else:
            _total_cost_basis = 0.0
        _realized_return_pct = (
            round(_realized_pnl_total / _total_cost_basis * 100, 2)
            if _total_cost_basis > 0 else None
        )
        return_vs_spy = {
            "status": "empty" if _n_realized == 0 else "ok",
            "basis": "realized_only",
            "spy_period_return_pct": _spy_ret,
            "realized_pnl_total": _realized_pnl_total,
            "realized_return_pct": _realized_return_pct,
            "total_cost_basis": round(_total_cost_basis, 2),
            "n_realized_trades": _n_realized,
            "caption": (
                "This is your average return on the capital cycled through "
                "closed trades this period — not your account's return. "
                "Capital reused across several round trips this period is "
                "counted once per trade, so it is NOT directly comparable to "
                "SPY's own period return above; unrealized moves on positions "
                "still open during the window are excluded entirely."
            ),
        }

    # ── recs / gates ─────────────────────────────────────────────────────────
    recs = _build_recs_section(
        rec_events_rows,
        period_start=period_start, period_end=period_end, today=today,
        trades_records=trades_records,
        protective_call_tickers=protective_call_tickers,
        rec_risk_snapshot_by_date=rec_risk_snapshot_by_date,
        spy_prices_by_date=spy_prices_by_date,
        historical_close_fn=historical_close_fn,
        rec_min_calls=rec_min_calls, rec_firm_calls=rec_firm_calls,
        rec_min_tickers=rec_min_tickers, rec_horizon_days=rec_horizon_days,
        rec_action_window_days=rec_action_window_days,
    )
    gates = _build_gates_section(
        gate_rows,
        period_start=period_start, period_end=period_end, today=today,
        spy_prices_by_date=spy_prices_by_date,
        historical_close_fn=historical_close_fn,
        gate_min_calls=gate_min_calls, gate_firm_calls=gate_firm_calls,
        gate_min_tickers=gate_min_tickers, gate_horizon_days=gate_horizon_days,
        composite_buy=composite_buy, gate_ids=gate_ids,
    )

    # ── leverage_drift / risk_drift ──────────────────────────────────────────
    leverage_drift = _drift_section(
        account_snapshots_df, period_start, period_end,
        ("leverage", "cushion", "call_distance_pct"),
    )
    risk_drift = _drift_section(
        risk_snapshots_df, period_start, period_end,
        ("portfolio_beta", "top_sector_pct", "max_single_name_pct",
         "avg_pairwise_corr", "corr_coverage_n"),
    )

    # ── account_return (2026-10-01) ───────────────────────────────────────────
    account_return = _account_return_section(
        account_return_snapshots_df, account_flows_rows,
        period_start, period_end, spy_prices_by_date,
    )
    account_return_monthly = monthly_account_returns(
        account_return_snapshots_df, account_flows_rows,
        period_start, period_end, spy_prices_by_date,
    )

    return {
        "period_start": period_start,
        "period_end": period_end,
        "account_return": account_return,
        "account_return_monthly": account_return_monthly,
        "return_vs_spy": return_vs_spy,
        "trade_behavior": trade_behavior,
        "recs": recs,
        "gates": gates,
        "leverage_drift": leverage_drift,
        "risk_drift": risk_drift,
    }


# ── account_return — real account-level return vs SPY (2026-10-01) ─────────
#
# A first-vs-last account_daily_snapshots lookup plus account.
# money_weighted_return over account_flows — reusing the SAME formula
# 💰 Account and 🎯 My Edge already use, NEVER the capital_vs_margin backward-
# reconstruction engine (ruled out for this feature, see module docstring).
# Owner-approved design, gated on a live-data endpoint-integrity audit that
# PASSED (see `_valid_snapshot_row`'s docstring and memory
# `project_performance_review_return_tile_redesign`).
#
# d0 is the SPECIFIC NYSE session immediately before the period starts — if
# its snapshot is missing or invalid, the headline is withheld (never
# silently substituted with an earlier date, which would silently redefine
# what period is being measured). d1 is the LATEST valid snapshot on or
# before the period ends — searching backward here is correct: it answers
# "as of the most recent close we have good data for" (e.g. today's EOD row
# not written yet), not a data-quality substitution.

def _valid_snapshots(account_snapshots_df: "pd.DataFrame | None") -> "pd.DataFrame | None":
    """`account_snapshots_df` filtered to `_valid_snapshot_row` rows, with a
    parsed `_d` date column, sorted — or None if the input itself is None
    (offline) or lacks a `snapshot_date` column at all."""
    if account_snapshots_df is None:
        return None
    df = account_snapshots_df.copy()
    if "snapshot_date" not in df.columns:
        return df.iloc[0:0]
    df["_d"] = df["snapshot_date"].apply(_to_date)
    df = df.dropna(subset=["_d"])
    if df.empty:
        return df
    return df[df.apply(_valid_snapshot_row, axis=1)].sort_values("_d")


def earliest_valid_account_date(account_snapshots_df: "pd.DataFrame | None") -> "date | None":
    """First date with a usable account_daily_snapshots anchor, or None when
    offline/empty. Exposed so app.py can offer a "Since tracking began"
    period preset without duplicating the validity rule."""
    valid = _valid_snapshots(account_snapshots_df)
    if valid is None or valid.empty:
        return None
    return valid["_d"].min()


def first_measurable_period_start(account_snapshots_df: "pd.DataFrame | None") -> "date | None":
    """First `period_start` for which the account-return headline CAN
    actually compute, or None when no valid snapshot exists. This is the
    trading day AFTER `earliest_valid_account_date`, never that date
    itself — the earliest valid day can only ever serve as a d1 anchor
    (nothing valid exists before it to serve as its own d0). Using the
    earliest date itself as a period start would always resolve to
    "pre_coverage" (found in Opus review, 2026-10-01: the "Since Tracking
    Began" preset could never produce its own headline without this)."""
    earliest = earliest_valid_account_date(account_snapshots_df)
    return None if earliest is None else _next_trading_day(earliest)


def _two_point_account_return(
    valid_df: "pd.DataFrame", d0: date, d1: date,
    account_flows_rows: "list[dict] | None", spy_prices_by_date: "dict | None",
) -> "dict | None":
    """MWR + SPY between two dates ALREADY confirmed present in `valid_df`.
    None if the denominator/MWR isn't computable (e.g. non-positive BMV +
    weighted flows). Flows are NOT deduped here against a possible manual-
    entry/broker-sync duplicate of the same real deposit/withdrawal
    (`data_maintenance.check_account_flows_duplicates` detects that class
    for manual rows) — a known, shared exposure 💰 Account's own
    money_weighted_return call already carries, so the two pages at least
    can't disagree with each other over it; not fixed here as its own,
    separate change (Opus review, 2026-10-01). Flows are filtered to (d0,
    d1] — strictly after d0, because d0's own EOD snapshot already reflects
    a flow dated d0 itself; passing it to money_weighted_return too would
    double-count it (that
    function's own `fd < d0` check only excludes strictly-before dates)."""
    d0_rows = valid_df[valid_df["_d"] == d0]
    d1_rows = valid_df[valid_df["_d"] == d1]
    if d0_rows.empty or d1_rows.empty:
        return None
    d0_row, d1_row = d0_rows.iloc[0], d1_rows.iloc[-1]
    bmv, emv = _opt(d0_row.get("net_equity")), _opt(d1_row.get("net_equity"))
    if bmv is None or emv is None:
        return None
    flows_in_window = []
    for f in (account_flows_rows or []):
        fd = _to_date(f.get("flow_date"))
        if fd is not None and d0 < fd <= d1:
            flows_in_window.append(f)
    mwr = money_weighted_return(bmv, d0, emv, d1, flows_in_window)
    if mwr is None:
        return None
    levs = [v for v in (_opt(d0_row.get("leverage")), _opt(d1_row.get("leverage"))) if v is not None]
    return {
        "d0": d0, "d1": d1,
        "bmv": round(bmv, 2), "emv": round(emv, 2),
        "net_flow": mwr["net_flow"], "gain": mwr["gain"],
        "return_pct": mwr["period_return_pct"],
        "spy_return_pct": _spy_period_return(spy_prices_by_date, d0, d1),
        "n_flows": len(flows_in_window),
        "max_leverage": round(max(levs), 2) if levs else None,
    }


def _account_return_section(
    account_snapshots_df: "pd.DataFrame | None",
    account_flows_rows: "list[dict] | None",
    period_start: date, period_end: date,
    spy_prices_by_date: "dict | None",
) -> dict:
    """Real account-level return vs SPY for `[period_start, period_end]`.

    Status in `{"offline", "pre_coverage", "ok"}`. `"offline"` only when a
    required loader itself failed (`None`); `"pre_coverage"` covers every
    other reason the headline can't be shown (no snapshot before
    period_start, an invalid endpoint, a non-computable MWR) — D2's single
    withhold-the-headline state, not several visually-distinct ones. When
    withheld, a `secondary` sub-dict (or `None`) gives the same two-point
    figure over whatever sub-range of the period IS covered — e.g. a
    quarter starting before tracking began still shows the tracked portion
    — labeled with its own dates, never the period's name (owner decision,
    D2 option b).

    SPY data is NOT required for an "offline"/"ok" distinction — it only
    affects `spy_return_pct` (None when unavailable, same as the existing
    cycled-capital tile's own tolerance), so a temporary SPY outage never
    withholds the account's own real return the way it's entitled to stay
    independent of other sections per this module's design."""
    if account_snapshots_df is None or account_flows_rows is None:
        return {"status": "offline"}
    if spy_prices_by_date is None:
        spy_prices_by_date = {}

    valid_df = _valid_snapshots(account_snapshots_df)
    if valid_df is None or valid_df.empty:
        return {"status": "pre_coverage", "earliest_valid_date": None, "secondary": None}

    earliest_valid = valid_df["_d"].min()
    d0_target = _prior_trading_day(period_start)
    d1_target = _last_trading_day_on_or_before(period_end)
    d1_candidates = valid_df[valid_df["_d"] <= d1_target]
    d1 = d1_candidates["_d"].iloc[-1] if not d1_candidates.empty else None

    result = None
    # Strictly GREATER, not >= -- d1 == d0_target is a ZERO-length window
    # (e.g. the first day of "This Quarter", before today's own EOD row has
    # been written, falls back to d1 == yesterday == d0_target). Accepting
    # it would report a fabricated 0.00% "return" for a period that hasn't
    # actually closed a single session yet, indistinguishable on screen
    # from a genuine flat measurement (found in Opus review, 2026-10-01).
    if d1 is not None and d1 > d0_target and (valid_df["_d"] == d0_target).any():
        result = _two_point_account_return(valid_df, d0_target, d1, account_flows_rows, spy_prices_by_date)
    if result is not None:
        result["status"] = "ok"
        result["caption"] = (
            "Your account's actual return, read from your recorded account "
            f"value on {d0_target.isoformat()} and {d1.isoformat()} (the NYSE "
            "sessions bracketing this period) and adjusted for any deposits "
            "or withdrawals in between — this is the real answer to \"did I "
            "beat the market\", unlike the cycled-capital figure below."
        )
        return result

    secondary = None
    if d1 is not None and earliest_valid < d1:
        secondary = _two_point_account_return(valid_df, earliest_valid, d1, account_flows_rows, spy_prices_by_date)
    return {"status": "pre_coverage", "earliest_valid_date": earliest_valid, "secondary": secondary}


def monthly_account_returns(
    account_snapshots_df: "pd.DataFrame | None",
    account_flows_rows: "list[dict] | None",
    period_start: date, period_end: date,
    spy_prices_by_date: "dict | None",
) -> "list[dict]":
    """One row per calendar month FULLY contained in `[period_start,
    period_end]` — a partial month at either edge is skipped, never
    clipped (a clipped month's return would be a different, confusing
    quantity). Each row is an independent call back into
    `_account_return_section`, so a month's figure always equals what a
    standalone call over just that month would produce — never a second,
    divergent computation. Always a monthly grain regardless of how much
    history has accumulated (3 years later this is just 36 rows, not a new
    threshold to invent). `[]` only when a REQUIRED loader is offline
    (account_snapshots_df/account_flows_rows) — SPY unavailable doesn't
    empty this list, matching `_account_return_section`'s own tolerance
    (an SPY outage never withholds the account's own real return)."""
    out: "list[dict]" = []
    if account_snapshots_df is None or account_flows_rows is None:
        return out
    y, m = period_start.year, period_start.month
    while True:
        m_start = date(y, m, 1)
        m_end = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1) - timedelta(days=1)
        if m_start > period_end:
            break
        if m_start >= period_start and m_end <= period_end:
            section = _account_return_section(
                account_snapshots_df, account_flows_rows, m_start, m_end, spy_prices_by_date,
            )
            # A month row shows no dates on screen (unlike the headline,
            # which prints d0/d1 explicitly), so "ok" must mean the WHOLE
            # month, never a silently narrower sub-range hidden behind the
            # month's own label. d1 is allowed to search backward past a
            # missing/invalid session (that's its whole purpose), but for a
            # COMPLETED month that search must still land exactly on the
            # month's own last trading day — if it had to reach further
            # back than that, the month isn't actually fully measurable
            # (found in Opus review, 2026-10-01).
            if section.get("status") == "ok" and section.get("d1") != _last_trading_day_on_or_before(m_end):
                # Not section["d0"] -- that's this MONTH's own anchor, not
                # the account's earliest valid date; nothing reads this key
                # on a monthly row today, but None keeps it honest for
                # whatever future consumer does.
                section = {"status": "pre_coverage", "earliest_valid_date": None, "secondary": None}
            out.append({"month": m_start.strftime("%Y-%m"), **section})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _drift_section(df: "pd.DataFrame | None", start: date, end: date, cols: "tuple[str, ...]") -> dict:
    """First-vs-last-in-window read of an already-recorded snapshot table's
    columns — no recomputation. `None` -> offline; loaded-but-nothing-in-
    window -> empty; otherwise the first/last row's values for each of
    `cols`, plus a delta (None-safe when either side is missing)."""
    if df is None:
        return {"status": "offline"}
    if df.empty or "snapshot_date" not in df.columns:
        return {"status": "empty"}
    d = df.copy()
    d["_d"] = pd.to_datetime(d["snapshot_date"], errors="coerce").dt.date
    d = d.dropna(subset=["_d"])
    d = d[(d["_d"] >= start) & (d["_d"] <= end)].sort_values("_d")
    if d.empty:
        return {"status": "empty"}

    first, last = d.iloc[0], d.iloc[-1]
    out = {"status": "ok", "start_date": first["_d"], "end_date": last["_d"]}
    for c in cols:
        v0 = _opt(first.get(c))
        v1 = _opt(last.get(c))
        out[f"{c}_start"] = v0
        out[f"{c}_end"] = v1
        out[f"{c}_delta"] = round(v1 - v0, 4) if (v0 is not None and v1 is not None) else None
    return out


# ── export formatters ────────────────────────────────────────────────────────

def format_review_csv(review: "dict | None") -> pd.DataFrame:
    """One row per fired call — recs + gates combined, flattened for CSV
    export. Empty/offline sections contribute no rows (never a crash)."""
    cols = ["section", "type_id", "ticker", "date", "status", "acted", "alpha_pct", "note"]
    review = review or {}
    rows: "list[dict]" = []

    recs = review.get("recs", {})
    for r in recs.get("enriched_rows", []):
        outcome = r.get("outcome")
        if not isinstance(outcome, dict):
            outcome = {}
        alpha = outcome.get("leg_a_candidate_alpha_pct")
        note = outcome.get("caption") or outcome.get("leg_a_caption") or ""
        rows.append({
            "section":  "rec",
            "type_id":  r.get("rec_type"),
            "ticker":   r.get("ticker"),
            "date":     r.get("fired_date"),
            "status":   r.get("status"),
            "acted":    r.get("acted"),
            "alpha_pct": alpha,
            "note":     note,
        })

    gates = review.get("gates", {})
    for r in gates.get("enriched_rows", []):
        rows.append({
            "section":  "gate",
            "type_id":  r.get("gate_id"),
            "ticker":   r.get("ticker"),
            "date":     r.get("rec_date"),
            "status":   r.get("status"),
            "acted":    None,
            "alpha_pct": r.get("alpha_pct"),
            "note":     "",
        })

    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def format_review_markdown(review: "dict | None") -> str:
    """Readable markdown performance review: period header, one section per
    key, below-floor disclosure inline, "informational, not advice" footer.
    Never renders a "building"/"early"/"firm" band as a headline (owner
    decision 2026-09-18) and never emits a literal `**` outside real markdown
    bold (this module never wraps a value in double-asterisks and lets it
    reach the page raw — every bold marker below is genuine intended
    markdown, consumed by a markdown renderer, not by Streamlit's HTML path)."""
    review = review or {}
    ps, pe = review.get("period_start"), review.get("period_end")
    lines = [
        f"# Performance Review — {ps} to {pe}",
        "",
        "**Informational only — not investment advice.** Archival snapshot; "
        "changes no gate, no recommendation, no composite.",
        "",
    ]

    # Account return vs SPY — the real headline, when a valid anchor exists
    ar = review.get("account_return", {})
    lines.append("## Account Return vs SPY")
    if ar.get("status") == "offline":
        lines.append("_Offline — account history or cash-flow history could not be loaded._")
    elif ar.get("status") == "ok":
        _ar_spy = ar.get("spy_return_pct")
        lines.append(f"- Your account's return, {ar['d0']} to {ar['d1']}: {ar['return_pct']:+.2f}%")
        lines.append(f"- SPY over the same dates: {_ar_spy:+.2f}%" if _ar_spy is not None else "- SPY over the same dates: unavailable")
        lines.append(f"- Net gain: ${ar['gain']:,.2f} (after {ar['n_flows']} deposit/withdrawal(s) in the ledger)")
        if ar.get("max_leverage") is not None and ar["max_leverage"] > 1:
            lines.append(
                f"- At up to {ar['max_leverage']:.1f}x leverage this period, your return moves "
                "roughly that many times the book's own move; SPY above is unlevered."
            )
        lines.append(f"- {ar.get('caption', '')}")
    else:
        lines.append(
            "_Your daily account history doesn't cover the start of this period, "
            "so an account-level return can't be shown for the full period._"
        )
        _sec = ar.get("secondary")
        if _sec is not None:
            _sec_spy = _sec.get("spy_return_pct")
            lines.append(
                f"- Tracked history covers {_sec['d0']} to {_sec['d1']}: your account "
                f"{_sec['return_pct']:+.2f}%"
                + (f" vs SPY {_sec_spy:+.2f}%" if _sec_spy is not None else "")
                + " over those specific dates — not the full period."
            )
    lines.append("")

    # Return vs SPY
    rvs = review.get("return_vs_spy", {})
    lines.append("## Return on capital cycled through closed trades")
    if rvs.get("status") == "offline":
        lines.append("_Offline — trade history or SPY history could not be loaded._")
    elif rvs.get("status") == "empty":
        lines.append("_No trades closed in this period._")
        _spy = rvs.get("spy_period_return_pct")
        if _spy is not None:
            lines.append(f"- SPY period return: {_spy:+.2f}%")
    else:
        _spy = rvs.get("spy_period_return_pct")
        _rr = rvs.get("realized_return_pct")
        # Suppress this section's OWN (differently-anchored) SPY line
        # whenever Account Return above already printed one — otherwise
        # the export disagrees with the screen, which deliberately shows
        # only one SPY figure per the same rule (2026-10-01 Opus review).
        if ar.get("status") == "ok":
            lines.append("- (SPY already shown above, over the same dates as your account return.)")
        else:
            lines.append(f"- SPY period return: {_spy:+.2f}%" if _spy is not None else "- SPY period return: unavailable")
        lines.append(
            f"- Return on capital cycled through closed trades: {_rr:+.2f}% "
            f"on ${rvs.get('total_cost_basis', 0.0):,.2f} cycled"
            if _rr is not None else "- Return on capital cycled: unavailable (no cost-basis data)"
        )
        lines.append(f"- Realized trade P&L closed in period: ${rvs.get('realized_pnl_total', 0.0):,.2f} "
                     f"({rvs.get('n_realized_trades', 0)} trade(s))")
        lines.append(f"- {rvs.get('caption', '')}")
    lines.append("")

    # Trade behavior
    tb = review.get("trade_behavior", {})
    lines.append("## Trade Behavior")
    if tb.get("status") == "offline":
        lines.append("_Offline — trade history could not be loaded._")
    elif tb.get("status") == "empty":
        lines.append("_No closed trades in this period._")
    else:
        lines.append(f"- Trades closed: {tb.get('n_trades', 0)}")
        lines.append(f"- Total realized P&L: ${tb.get('total_realized_pnl', 0.0):,.2f}")
        _wr = tb.get("win_rate_pct")
        lines.append(f"- Win rate: {_wr:.1f}%" if _wr is not None else "- Win rate: —")
    lines.append("")

    # Recommendations
    recs = review.get("recs", {})
    lines.append("## Recommendations Acted vs Skipped")
    if recs.get("status") == "offline":
        lines.append("_Offline — the recommendation-outcomes ledger could not be loaded._")
    elif recs.get("status") == "empty":
        lines.append("_No qualifying recommendation calls fired in this period._")
    else:
        for g in recs.get("by_type", []):
            lines.append(f"- **{g['rec_type']} / {g['arm']}** — {g['n_calls']} call(s), "
                         f"{g['n_distinct_tickers']} distinct ticker(s)")
            if g.get("below_floor"):
                lines.append("  - Below the standalone page's evaluable floor — descriptive "
                             "counts only, no verdict implied.")
            if g.get("mean_leg_a_alpha_pct") is not None:
                lines.append(f"  - Mean candidate alpha vs SPY: {g['mean_leg_a_alpha_pct']:+.2f}%")
            for fn in g.get("footnotes", []):
                lines.append(f"  - ℹ️ {fn}")
    lines.append("")

    # Gates
    gates = review.get("gates", {})
    lines.append("## Gates Fired")
    if gates.get("status") == "offline":
        lines.append("_Offline — the gate suppression ledger could not be loaded._")
    elif gates.get("status") == "empty":
        lines.append("_No gate suppressions logged in this period._")
    else:
        for g in gates.get("by_gate", []):
            if g.get("market_wide"):
                lines.append(f"- **{g['gate_id']}** — market-wide, no single ticker to evaluate.")
                continue
            lines.append(f"- **{g['gate_id']}** — {g.get('n_matured_evaluable', 0)} matured "
                         f"evaluable, {g.get('n_distinct_tickers_evaluable', 0)} distinct ticker(s)")
            if g.get("below_floor"):
                lines.append("  - Below the standalone page's evaluable floor — descriptive "
                             "counts only, no verdict implied.")
            _ma = g.get("mean_alpha_pct")
            if _ma is not None:
                lines.append(f"  - Mean forward alpha: {_ma:+.2f}%")
            for fn in g.get("footnotes", []):
                lines.append(f"  - ℹ️ {fn}")
    lines.append("")

    # Leverage drift
    ld = review.get("leverage_drift", {})
    lines.append("## Leverage & Margin Cushion Drift")
    if ld.get("status") == "offline":
        lines.append("_Offline — account snapshot history could not be loaded._")
    elif ld.get("status") == "empty":
        lines.append("_No account snapshots recorded in this period._")
    else:
        lines.append(f"- Leverage: {ld.get('leverage_start')} → {ld.get('leverage_end')} "
                     f"(Δ {ld.get('leverage_delta')})")
        lines.append(f"- Cushion: {ld.get('cushion_start')} → {ld.get('cushion_end')} "
                     f"(Δ {ld.get('cushion_delta')})")
        lines.append(f"- Call distance %: {ld.get('call_distance_pct_start')} → "
                     f"{ld.get('call_distance_pct_end')} (Δ {ld.get('call_distance_pct_delta')})")
    lines.append("")

    # Risk drift
    rd = review.get("risk_drift", {})
    lines.append("## Portfolio Risk Drift")
    if rd.get("status") == "offline":
        lines.append("_Offline — portfolio risk snapshot history could not be loaded._")
    elif rd.get("status") == "empty":
        lines.append("_No risk snapshots recorded in this period._")
    else:
        lines.append(f"- Portfolio beta: {rd.get('portfolio_beta_start')} → {rd.get('portfolio_beta_end')} "
                     f"(Δ {rd.get('portfolio_beta_delta')})")
        lines.append(f"- Top sector %: {rd.get('top_sector_pct_start')} → {rd.get('top_sector_pct_end')} "
                     f"(Δ {rd.get('top_sector_pct_delta')})")
        lines.append(f"- Max single-name %: {rd.get('max_single_name_pct_start')} → "
                     f"{rd.get('max_single_name_pct_end')} (Δ {rd.get('max_single_name_pct_delta')})")
        lines.append(f"- Avg pairwise correlation: {rd.get('avg_pairwise_corr_start')} → "
                     f"{rd.get('avg_pairwise_corr_end')} (Δ {rd.get('avg_pairwise_corr_delta')}) "
                     f"— sample size {rd.get('corr_coverage_n_start')} → {rd.get('corr_coverage_n_end')} "
                     "observations; a shift here can reflect sample size, not just a real change.")
    lines += [
        "",
        "---",
        "*This report is generated for informational purposes only and does "
        "not constitute investment advice.*",
    ]
    return "\n".join(lines)
