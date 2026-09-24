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
  - `return_vs_spy` is the one new comparison: SPY's period return (via
            `benchmark_mirror.price_on_or_before`) shown next to realized
            trade P&L closed inside the window — explicitly labeled
            `basis="realized_only"`, since unrealized moves on positions
            still open during the window are excluded.
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

from datetime import date
from typing import Callable

import pandas as pd

from stock_analyzer import gate_ledger_readout, rec_events_readout
from stock_analyzer.benchmark_mirror import price_on_or_before
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
        # Realized return %, so the comparison to SPY's % is actually
        # apples-to-apples rather than a dollar figure next to a percentage
        # (a real gap the owner caught live 2026-09-18 — the two numbers
        # weren't on the same footing before this). Denominator is the total
        # cost basis of the shares actually closed this window — an "owned"
        # figure derived purely from the same trades the numerator covers,
        # not account equity (which would reopen the deposits/withdrawals
        # ambiguity this feature already declined to touch).
        if _tb_status == "ok":
            _total_cost_basis = float((_tb_windowed["cost_basis"] * _tb_windowed["shares"]).sum())
        else:
            _total_cost_basis = 0.0
        _realized_return_pct = (
            round(_realized_pnl_total / _total_cost_basis * 100, 2)
            if _total_cost_basis > 0 else None
        )
        _delta_vs_spy_pp = (
            round(_realized_return_pct - _spy_ret, 2)
            if _realized_return_pct is not None and _spy_ret is not None else None
        )
        return_vs_spy = {
            "status": "empty" if _n_realized == 0 else "ok",
            "basis": "realized_only",
            "spy_period_return_pct": _spy_ret,
            "realized_pnl_total": _realized_pnl_total,
            "realized_return_pct": _realized_return_pct,
            "total_cost_basis": round(_total_cost_basis, 2),
            "delta_vs_spy_pp": _delta_vs_spy_pp,
            "n_realized_trades": _n_realized,
            "caption": (
                "SPY's period return vs your REALIZED return on the capital "
                "actually deployed in trades closed inside this window only — "
                "unrealized moves on positions still open during the window are "
                "not included, and this is not your whole-account return."
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

    return {
        "period_start": period_start,
        "period_end": period_end,
        "return_vs_spy": return_vs_spy,
        "trade_behavior": trade_behavior,
        "recs": recs,
        "gates": gates,
        "leverage_drift": leverage_drift,
        "risk_drift": risk_drift,
    }


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

    # Return vs SPY
    rvs = review.get("return_vs_spy", {})
    lines.append("## Return vs SPY (realized only)")
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
        _delta = rvs.get("delta_vs_spy_pp")
        lines.append(f"- SPY period return: {_spy:+.2f}%" if _spy is not None else "- SPY period return: unavailable")
        lines.append(
            f"- Your realized return: {_rr:+.2f}% on ${rvs.get('total_cost_basis', 0.0):,.2f} deployed"
            if _rr is not None else "- Your realized return: unavailable (no cost-basis data)"
        )
        lines.append(f"- Realized trade P&L closed in period: ${rvs.get('realized_pnl_total', 0.0):,.2f} "
                     f"({rvs.get('n_realized_trades', 0)} trade(s))")
        if _delta is not None:
            lines.append(f"- Vs. SPY: {'+' if _delta >= 0 else ''}{_delta:.2f} percentage points")
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
