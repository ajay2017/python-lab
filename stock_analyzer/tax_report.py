"""
Tax Report — Phase 1 of the 📄 Reports feature (docs/plans/reports.md).

Builds a realized-gains ledger via FIFO lot matching, for a given tax year.
This is deliberately a SEPARATE replay from `tax_advisor._build_open_lots`,
not a reuse of it: `_build_open_lots` only tracks OPEN lots as
`[shares, buy_date, split_ratio]` with no cost-basis field, because its
existing consumers (`build_tax_analysis`, `holding_period_status`) only need
holding period, not dollar gain — a matched SELL just discards the consumed
lot. A realized-gains report needs the CLOSED-lot history with a cost-per-
share on every lot, so this module mirrors `_build_open_lots`'s exact
BUY/SELL/SPLIT conventions (FIFO consumption, split pro-ration, the
no-prior-lots SPLIT seed) but additionally tracks `cost_per_share` and
records every closed match instead of discarding it.

One deliberate correction vs `_build_open_lots`: that function derives its
day-count from a bare UTC `.date()` on the trade timestamp — harmless for its
own holding-period day-count use, but not acceptable for a tax-YEAR boundary
(a late-evening Dec 31 ET trade could parse as UTC-Jan 1). Every date here is
converted to America/New_York (`tax_advisor._ET`) before `.date()` is taken.

Wash-sale detection is NOT reimplemented here — every closed lot at a loss is
checked via the existing `tax_advisor.wash_sale_violation_after_harvest()`,
verbatim.

Awareness/archival only — issues no recommendation, gates nothing, adds no
new constant (reuses `TAX_STCG_THRESHOLD_DAYS` / `TAX_WASH_SALE_DAYS`).
"""

import pandas as pd
from datetime import date as _date

from stock_analyzer.constants import TAX_STCG_THRESHOLD_DAYS
from stock_analyzer.tax_advisor import (
    _ET, _today_et, wash_sale_violation_after_harvest,
)


def _opt(val):
    """None-preserving float coercion. Returns None for None / NaN / non-numeric."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def _et_date(ts) -> _date:
    """Convert a UTC-aware pandas Timestamp to its America/New_York calendar date.

    Deliberately NOT `ts.date()` (that would take the UTC calendar date) —
    see module docstring: the tax-YEAR boundary must be ET, not UTC.
    """
    return ts.tz_convert(_ET).date()


def _empty_ledger(tax_year: int) -> dict:
    return {
        "tax_year":              tax_year,
        "rows":                  [],
        "st_gain":               0.0,
        "lt_gain":               0.0,
        "unknown_gain":          0.0,
        "total_proceeds":        0.0,
        "total_cost":            0.0,
        "stored_realized_total": 0.0,
        "reconciles":            True,
    }


def build_realized_lot_ledger(
    trades_df: pd.DataFrame | None,
    tax_year: int,
    today: _date | None = None,
) -> dict | None:
    """FIFO-replay the trade journal into closed-lot realized gains for `tax_year`.

    Returns `None` when `trades_df is None` (offline sentinel — never confuse
    with "loaded but empty"). Returns a shaped-empty dict (see `_empty_ledger`)
    for a genuinely empty year or an empty/columnless journal.

    Return shape: {tax_year, rows: [...], st_gain, lt_gain, unknown_gain,
    total_proceeds, total_cost, stored_realized_total, reconciles}. Each row:
    {ticker, buy_date, sell_date, shares, proceeds, cost, gain, days_held,
    term: "ST"|"LT"|"Unknown", wash_sale_status: dict | None}.
    """
    if trades_df is None:
        return None
    if today is None:
        today = _today_et()
    if trades_df.empty or "action" not in trades_df.columns or "traded_at" not in trades_df.columns:
        return _empty_ledger(tax_year)

    df = trades_df.copy()
    df["_ts"] = pd.to_datetime(df["traded_at"], errors="coerce", utc=True, format="ISO8601")
    df = df.dropna(subset=["_ts"])
    if df.empty:
        return _empty_ledger(tax_year)

    closed: list[dict] = []

    for ticker, tdf in df.groupby(df["ticker"].astype(str).str.upper()):
        tdf = tdf.sort_values(["_ts", "id"], ascending=True)
        # each lot: {shares, buy_date, cost_per_share, split_ratio, cost_known}
        lots: list[dict] = []

        for _, r in tdf.iterrows():
            action = str(r.get("action", "")).upper()
            try:
                sh = float(r.get("shares") or 0)
            except (TypeError, ValueError):
                continue
            if sh <= 0:
                continue
            d = _et_date(r["_ts"])

            if "SPLIT" in action:
                old_total = sum(l["shares"] for l in lots)
                if old_total > 1e-6:
                    ratio = sh / old_total
                    for l in lots:
                        l["shares"] *= ratio
                        l["split_ratio"] *= ratio
                        l["cost_per_share"] /= ratio
                else:
                    # No prior lots (rebuild from a SPLIT seed) — synthesize
                    # one, cost unknown: there's no real acquisition price to
                    # seed against, matching _build_open_lots' equivalent
                    # no-prior-lots branch.
                    lots = [{
                        "shares": sh, "buy_date": d, "cost_per_share": 0.0,
                        "split_ratio": 1.0, "cost_known": False,
                    }]

            elif "BUY" in action:
                price = _opt(r.get("price"))
                lots.append({
                    "shares":         sh,
                    "buy_date":       d,
                    "cost_per_share": price if price is not None else 0.0,
                    "split_ratio":    1.0,
                    "cost_known":     price is not None,
                })

            elif "SELL" in action:
                sell_price = _opt(r.get("price")) or 0.0
                sell_date = d
                total_sell_shares = sh
                remaining = sh
                while remaining > 1e-6 and lots:
                    lot = lots[0]
                    matched = min(lot["shares"], remaining)
                    proceeds = matched * sell_price
                    if lot["cost_known"]:
                        cost      = matched * lot["cost_per_share"]
                        gain      = proceeds - cost
                        days_held = (sell_date - lot["buy_date"]).days
                        term      = "LT" if days_held >= TAX_STCG_THRESHOLD_DAYS else "ST"
                    else:
                        cost, gain, days_held, term = None, None, None, "Unknown"
                    closed.append({
                        "ticker":    ticker,
                        "buy_date":  lot["buy_date"],
                        "sell_date": sell_date,
                        "shares":    matched,
                        "proceeds":  proceeds,
                        "cost":      cost,
                        "gain":      gain,
                        "days_held": days_held,
                        "term":      term,
                    })
                    lot["shares"] -= matched
                    remaining -= matched
                    if lot["shares"] <= 1e-6:
                        lots.pop(0)

                if remaining > 1e-6:
                    # No matching BUY history for the remainder (e.g. a
                    # broker-imported partial history) — never guess a term
                    # or fabricate a cost; fall back to the SELL row's own
                    # stored average-cost fields, prorated to the unmatched
                    # fraction of this sell.
                    frac = remaining / total_sell_shares if total_sell_shares > 1e-6 else 0.0
                    proceeds = remaining * sell_price
                    cb   = _opt(r.get("cost_basis"))
                    rpnl = _opt(r.get("realized_pnl"))
                    if cb is not None and cb > 0:
                        cost = remaining * cb
                        gain = proceeds - cost
                    elif rpnl is not None:
                        gain = rpnl * frac
                        cost = None
                    else:
                        gain, cost = None, None
                    closed.append({
                        "ticker":    ticker,
                        "buy_date":  None,
                        "sell_date": sell_date,
                        "shares":    remaining,
                        "proceeds":  proceeds,
                        "cost":      cost,
                        "gain":      gain,
                        "days_held": None,
                        "term":      "Unknown",
                    })

    year_rows = [r for r in closed if r["sell_date"].year == tax_year]

    st_gain      = sum(r["gain"] for r in year_rows if r["term"] == "ST" and r["gain"] is not None)
    lt_gain      = sum(r["gain"] for r in year_rows if r["term"] == "LT" and r["gain"] is not None)
    unknown_gain = sum(r["gain"] for r in year_rows if r["term"] == "Unknown" and r["gain"] is not None)
    total_proceeds = sum(r["proceeds"] for r in year_rows)
    total_cost     = sum(r["cost"] for r in year_rows if r["cost"] is not None)

    # Independent reconciliation total: the stored average-cost realized_pnl
    # for every real SELL (excluding SPLIT rows) whose ET sale-date year
    # matches, computed straight off the journal — not derived from the FIFO
    # replay above, so a divergence between the two methods is genuinely
    # observable rather than tautological.
    action_up = df["action"].astype(str).str.upper()
    sell_mask = action_up.str.contains("SELL", na=False) & ~action_up.str.contains("SPLIT", na=False)
    stored_realized_total = 0.0
    for _, r in df.loc[sell_mask].iterrows():
        if _et_date(r["_ts"]).year != tax_year:
            continue
        stored_realized_total += (_opt(r.get("realized_pnl")) or 0.0)

    # Wash-sale flags — only on realized LOSSES; the rule doesn't apply to
    # gains. Pass the FULL (unfiltered-by-year) trades_df: the AFTER-side
    # check needs to see BUYs after the sale regardless of tax year.
    for r in year_rows:
        if r["gain"] is not None and r["gain"] < 0:
            r["wash_sale_status"] = wash_sale_violation_after_harvest(
                r["ticker"], r["sell_date"], trades_df, today=today,
            )
        else:
            r["wash_sale_status"] = None

    reconciles = abs((st_gain + lt_gain + unknown_gain) - stored_realized_total) < 0.01

    return {
        "tax_year":              tax_year,
        "rows":                  year_rows,
        "st_gain":               st_gain,
        "lt_gain":               lt_gain,
        "unknown_gain":          unknown_gain,
        "total_proceeds":        total_proceeds,
        "total_cost":            total_cost,
        "stored_realized_total": stored_realized_total,
        "reconciles":            reconciles,
    }


def available_tax_years(trades_df: pd.DataFrame | None) -> list[int]:
    """Distinct ET-converted tax years among non-SPLIT SELL rows, descending.

    Empty list on `None`/empty/malformed input — this one only backs a
    selectbox, not a decision, so it doesn't need the offline/empty
    distinction `build_realized_lot_ledger` does.
    """
    if trades_df is None or trades_df.empty:
        return []
    if "action" not in trades_df.columns or "traded_at" not in trades_df.columns:
        return []
    action_up = trades_df["action"].astype(str).str.upper()
    mask = action_up.str.contains("SELL", na=False) & ~action_up.str.contains("SPLIT", na=False)
    if not mask.any():
        return []
    ts = pd.to_datetime(trades_df.loc[mask, "traded_at"], errors="coerce", utc=True, format="ISO8601").dropna()
    if ts.empty:
        return []
    years = sorted({_et_date(t).year for t in ts}, reverse=True)
    return years


def format_ledger_csv(ledger: dict) -> pd.DataFrame:
    """One row per closed lot, flattened for CSV export."""
    cols = ["ticker", "buy_date", "sell_date", "shares", "proceeds", "cost",
            "gain", "days_held", "term", "wash_sale_status"]
    rows = (ledger or {}).get("rows", [])
    if not rows:
        return pd.DataFrame(columns=cols)
    out = []
    for r in rows:
        ws = r.get("wash_sale_status")
        out.append({
            "ticker":           r.get("ticker"),
            "buy_date":         r.get("buy_date"),
            "sell_date":        r.get("sell_date"),
            "shares":           r.get("shares"),
            "proceeds":         r.get("proceeds"),
            "cost":             r.get("cost"),
            "gain":             r.get("gain"),
            "days_held":        r.get("days_held"),
            "term":             r.get("term"),
            "wash_sale_status": ws.get("status") if isinstance(ws, dict) else None,
        })
    return pd.DataFrame(out, columns=cols)


def format_ledger_markdown(ledger: dict) -> str:
    """Readable markdown tax report: summary + reconciliation + per-lot table."""
    ledger = ledger or {}
    ty = ledger.get("tax_year")
    lines = [
        f"# Tax Report — {ty}",
        "",
        "**Not tax advice** — informational reconciliation only; verify "
        "against your broker's 1099-B before filing.",
        "",
        "## Summary",
        f"- Short-term gain/loss: ${ledger.get('st_gain', 0.0):,.2f}",
        f"- Long-term gain/loss: ${ledger.get('lt_gain', 0.0):,.2f}",
        f"- Unknown-term gain/loss: ${ledger.get('unknown_gain', 0.0):,.2f}",
        f"- Total proceeds: ${ledger.get('total_proceeds', 0.0):,.2f}",
        f"- Total cost basis (FIFO): ${ledger.get('total_cost', 0.0):,.2f}",
        f"- Stored (average-cost) realized total: ${ledger.get('stored_realized_total', 0.0):,.2f}",
        "- Reconciles: " + (
            "Yes — FIFO and stored average-cost totals agree."
            if ledger.get("reconciles")
            else "No — FIFO (per-lot, ST/LT-aware) and the stored average-cost "
                 "total diverge. This is expected for partial sales across "
                 "lots bought at different prices; both methods are valid, "
                 "they allocate the same overall gain differently."
        ),
        "",
        "## Closed Lots",
        "",
        "| Ticker | Buy Date | Sell Date | Shares | Proceeds | Cost | Gain | Days Held | Term | Wash Sale |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = ledger.get("rows", [])
    if not rows:
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    for r in rows:
        ws = r.get("wash_sale_status")
        ws_label = ws.get("status") if isinstance(ws, dict) else "—"
        cost = r.get("cost")
        gain = r.get("gain")
        cost_str = f"${cost:,.2f}" if cost is not None else "—"
        gain_str = f"${gain:,.2f}" if gain is not None else "—"
        days_str = str(r.get("days_held")) if r.get("days_held") is not None else "—"
        lines.append(
            f"| {r.get('ticker')} | {r.get('buy_date') or '—'} | {r.get('sell_date')} | "
            f"{r.get('shares'):.4f} | ${r.get('proceeds', 0.0):,.2f} | {cost_str} | "
            f"{gain_str} | {days_str} | {r.get('term')} | {ws_label} |"
        )
    lines += [
        "",
        "---",
        "*This report is generated for informational purposes only and does "
        "not constitute tax advice. Consult a tax professional and verify "
        "against your broker's official 1099-B before filing.*",
    ]
    return "\n".join(lines)
