"""
Tax Efficiency Advisor.

For each open position, computes:
- Holding period (from earliest BUY in trade journal, or unknown)
- STCG vs LTCG classification (≤365 days = short-term, >365 = long-term)
- Days until LTCG threshold (if still short-term)
- Estimated tax bill if sold today vs after LTCG threshold
- Dollar savings from waiting
- Harvestable losses (unrealized losses that can offset gains)
- Wash sale rule warnings

Tax rates are configurable; defaults to US high-bracket (37% STCG / 20% LTCG).
"""

import pandas as pd
import pytz as _pytz
from datetime import date as _date, datetime as _dt

from stock_analyzer.constants import (
    TAX_RATE_SHORT_TERM, TAX_RATE_LONG_TERM, TAX_STCG_THRESHOLD_DAYS,
    TAX_HARVEST_MIN_LOSS, TAX_LTCG_WAIT_WINDOW_DAYS, TAX_LONGTERM_WINDOW_DAYS,
    TAX_WASH_SALE_DAYS,
)

_ET = _pytz.timezone("America/New_York")


def _today_et() -> _date:
    """ET-localized "today" — Streamlit Cloud runs UTC so plain date.today()
    is up to ~5 hours ahead of the user's calendar after 7 PM ET, which would
    classify positions in the wrong tax-year window."""
    return _dt.now(_ET).date()


# Single-sourced from constants.py (display-only tax policy). Kept as a module
# alias so the existing internal references read unchanged.
_STCG_THRESHOLD_DAYS = TAX_STCG_THRESHOLD_DAYS   # IRS: ≥ 366 days = long-term


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def _opt(val):
    """None-preserving float coercion. Returns None for None / NaN / non-numeric."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def _earliest_buy(ticker: str, trades_df: pd.DataFrame) -> _date | None:
    """Return the earliest BUY date for a ticker from the trade journal.

    Kept for callers that only need the oldest acquisition date. New tax
    logic should prefer _build_open_lots, which is tax-lot aware.
    """
    if trades_df is None or trades_df.empty:
        return None
    buys = trades_df[
        (trades_df["ticker"].astype(str).str.upper() == ticker.upper()) &
        (trades_df["action"] == "BUY")
    ]
    if buys.empty:
        return None
    dates = pd.to_datetime(buys["traded_at"], errors="coerce", utc=True, format="ISO8601").dropna()
    if dates.empty:
        return None
    return dates.min().date()


def _build_open_lots(ticker: str, trades_df: pd.DataFrame, today: _date) -> list[dict]:
    """FIFO-replay the trade journal and return the currently-open tax lots.

    Each returned dict: {shares, buy_date, days_held}. SELLs consume from
    the oldest open lot first (FIFO). SPLIT rows pro-rata adjust each lot's
    share count so the post-split total matches the SPLIT row's shares,
    preserving each lot's original acquisition date (IRS rule: a split
    inherits the holding period of the pre-split shares).

    Without this lot-level reconstruction, a multi-lot position is
    incorrectly classified by `_earliest_buy` — recently-added shares get
    treated as LTCG once the oldest lot matures.
    """
    if trades_df is None or trades_df.empty:
        return []
    rows = trades_df[trades_df["ticker"].astype(str).str.upper() == ticker.upper()].copy()
    if rows.empty:
        return []
    rows["_ts"] = pd.to_datetime(rows["traded_at"], errors="coerce", utc=True, format="ISO8601")
    rows = rows.dropna(subset=["_ts"]).sort_values(["_ts", "id"], ascending=True)

    lots: list[list] = []  # each entry: mutable [shares, buy_date]
    for _, r in rows.iterrows():
        action = str(r.get("action", "")).upper()
        try:
            sh = float(r.get("shares") or 0)
        except (TypeError, ValueError):
            continue
        if sh <= 0:
            continue
        d = r["_ts"].date()
        if "SPLIT" in action:
            old_total = sum(lot[0] for lot in lots)
            if old_total > 1e-6 and sh > 0:
                ratio = sh / old_total
                for lot in lots:
                    lot[0] *= ratio
            else:
                # No prior lots (rebuild from a SPLIT seed) — synthesize one.
                lots = [[sh, d]]
        elif "BUY" in action:
            lots.append([sh, d])
        elif "SELL" in action:
            remaining = sh
            while remaining > 1e-6 and lots:
                if lots[0][0] <= remaining + 1e-6:
                    remaining -= lots[0][0]
                    lots.pop(0)
                else:
                    lots[0][0] -= remaining
                    remaining = 0.0

    return [
        {"shares": s, "buy_date": d, "days_held": (today - d).days}
        for s, d in lots if s > 1e-6
    ]


def build_tax_analysis(
    port_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    stcg_rate: float = TAX_RATE_SHORT_TERM,
    ltcg_rate: float = TAX_RATE_LONG_TERM,
    today: _date | None = None,
) -> dict:
    """
    Main entry point.

    Returns dict with:
      rows:              list of per-position tax dicts
      total_stcg_gain:   sum of unrealized gains in STCG positions
      total_ltcg_gain:   sum of unrealized gains in LTCG positions
      total_harvestable: sum of unrealized losses (absolute value)
      tax_today:         estimated total tax if all positions sold today
      tax_ltcg:          estimated total tax if all STCG positions wait for LTCG
      tax_savings:       tax_today - tax_ltcg ($ saved by waiting)
      stcg_rate, ltcg_rate
    """
    if today is None:
        today = _today_et()

    rows = []
    total_stcg_gain   = 0.0
    total_ltcg_gain   = 0.0
    total_harvestable = 0.0
    tax_today_total   = 0.0
    tax_ltcg_total    = 0.0

    for _, row in port_df.iterrows():
        ticker      = row["Ticker"]
        # None-preserving coercion on the decision-gating fields. The legacy
        # _f(default=0.0) silently turned a missing Price into pnl = -avg_cost
        # * shares (huge fake loss -> harvestable), and a missing Shares into
        # cost_total = 0 -> position "fully disposed". Skip the row entirely
        # so the user sees the position omitted (visible) rather than as $0
        # tax math (invisible).
        shares_v    = _opt(row.get("Shares"))
        avg_cost_v  = _opt(row.get("Avg Cost"))
        price_v     = _opt(row.get("Price"))
        pnl_v       = _opt(row.get("P&L ($)"))
        if shares_v is None or avg_cost_v is None or price_v is None or pnl_v is None:
            continue
        shares      = shares_v
        avg_cost    = avg_cost_v
        price       = price_v
        pnl         = pnl_v
        cost_total  = round(avg_cost * shares, 2)

        # Tax-lot-aware holding period reconstruction. A multi-lot position
        # (added shares to an existing one) has a mix of STCG and LTCG-aged
        # shares; the legacy logic classified the whole position by the
        # oldest lot's date, mis-rating the recently-added shares.
        lots = _build_open_lots(ticker, trades_df, today)
        total_lot_shares = sum(l["shares"] for l in lots)

        if not lots or total_lot_shares <= 1e-6:
            acq_date     = _earliest_buy(ticker, trades_df)
            days_held    = (today - acq_date).days if acq_date else None
            gain_type    = "Unknown"
            days_to_ltcg = None
            ltcg_frac    = 0.0
            stcg_frac    = 0.0
        else:
            ltcg_shares = sum(l["shares"] for l in lots if l["days_held"] >= _STCG_THRESHOLD_DAYS)
            stcg_shares = total_lot_shares - ltcg_shares
            ltcg_frac   = ltcg_shares / total_lot_shares
            stcg_frac   = 1.0 - ltcg_frac
            acq_date    = min(l["buy_date"] for l in lots)
            # Display "days held" as the share-weighted average across lots
            # so a 90/10 fresh/old split doesn't claim a 2-year hold time.
            days_held   = int(round(
                sum(l["days_held"] * l["shares"] for l in lots) / total_lot_shares
            ))
            if stcg_shares <= 1e-6:
                gain_type    = "LTCG"
                days_to_ltcg = 0
            elif ltcg_shares <= 1e-6:
                gain_type    = "STCG"
                # Wait time = earliest STCG lot's days to maturity
                days_to_ltcg = max(
                    0,
                    min(
                        _STCG_THRESHOLD_DAYS - l["days_held"]
                        for l in lots
                        if l["days_held"] < _STCG_THRESHOLD_DAYS
                    ),
                )
            else:
                gain_type    = "MIXED"
                days_to_ltcg = max(
                    0,
                    min(
                        _STCG_THRESHOLD_DAYS - l["days_held"]
                        for l in lots
                        if l["days_held"] < _STCG_THRESHOLD_DAYS
                    ),
                )

        # Tax estimates — apportion PnL by the share fractions actually
        # eligible for each rate today vs. after waiting STCG lots out.
        if pnl > 0:
            if gain_type == "Unknown":
                tax_if_sold_today = round(pnl * stcg_rate, 0)   # worst case
                tax_if_ltcg       = round(pnl * ltcg_rate, 0)
                tax_savings       = round(tax_if_sold_today - tax_if_ltcg, 0)
            else:
                stcg_pnl = pnl * stcg_frac
                ltcg_pnl = pnl * ltcg_frac
                tax_if_sold_today = round(stcg_pnl * stcg_rate + ltcg_pnl * ltcg_rate, 0)
                # Waited-out case: every share that's still STCG eventually
                # becomes LTCG-rated. Upper bound on savings; see M-14.
                tax_if_ltcg       = round(pnl * ltcg_rate, 0)
                tax_savings       = round(tax_if_sold_today - tax_if_ltcg, 0)
                if stcg_pnl > 0:
                    total_stcg_gain += stcg_pnl
                if ltcg_pnl > 0:
                    total_ltcg_gain += ltcg_pnl
        else:
            tax_if_sold_today = 0.0
            tax_if_ltcg       = 0.0
            tax_savings       = 0.0

        # Harvestable loss
        harvestable = round(abs(pnl), 0) if pnl < 0 else 0.0
        if pnl < 0:
            total_harvestable += abs(pnl)

        tax_today_total += tax_if_sold_today
        if gain_type == "LTCG":
            tax_ltcg_total += tax_if_ltcg
        else:
            # If STCG and would wait: pay LTCG rate
            tax_ltcg_total += round(max(pnl, 0) * ltcg_rate, 0)

        # Action flag
        # Tax tail does not wag the investment dog. A position currently rated
        # Buy/Strong Buy is NOT eligible for HARVEST regardless of the unrealized
        # loss — exiting a high-conviction view to capture a tax loss trades a
        # known tax benefit for an unknown opportunity cost the investment view
        # explicitly says is unfavourable.
        _sig            = str(row.get("Signal", ""))
        _is_conviction  = any(w in _sig for w in ("Strong Buy", "Buy"))
        harvest_blocked = False
        if pnl < 0 and abs(pnl) > TAX_HARVEST_MIN_LOSS:
            if _is_conviction:
                action          = "HOLD_FOR_SIGNAL"
                harvest_blocked = True
            else:
                action = "HARVEST"
        elif gain_type in ("STCG", "MIXED") and pnl > 0 and days_to_ltcg is not None and days_to_ltcg <= TAX_LTCG_WAIT_WINDOW_DAYS:
            action = "WAIT"
        elif gain_type in ("STCG", "MIXED") and pnl > 0 and days_to_ltcg is not None and days_to_ltcg > TAX_LTCG_WAIT_WINDOW_DAYS:
            action = "HOLD_FOR_LTCG"
        elif gain_type == "LTCG" and pnl > 0:
            action = "LTCG_ELIGIBLE"
        else:
            action = "MONITOR"

        rows.append({
            "ticker":             ticker,
            "shares":             int(shares),
            "avg_cost":           avg_cost,
            "price":              price,
            "cost_total":         cost_total,
            "pnl":                pnl,
            "acq_date":           str(acq_date) if acq_date else None,
            "days_held":          days_held,
            "gain_type":          gain_type,
            "days_to_ltcg":       days_to_ltcg,
            "ltcg_frac":          round(ltcg_frac, 4),
            "stcg_frac":          round(stcg_frac, 4),
            "tax_if_sold_today":  tax_if_sold_today,
            "tax_if_ltcg":        tax_if_ltcg,
            "tax_savings":        tax_savings,
            "harvestable":        harvestable,
            "action":             action,
            "signal":             _sig,
            "harvest_blocked":    harvest_blocked,
        })

    # Sort: HARVEST first, then HOLD_FOR_SIGNAL, then WAIT, then HOLD_FOR_LTCG, then rest
    _order = {"HARVEST": 0, "HOLD_FOR_SIGNAL": 1, "WAIT": 2, "HOLD_FOR_LTCG": 3,
              "LTCG_ELIGIBLE": 4, "MONITOR": 5}
    rows.sort(key=lambda x: _order.get(x["action"], 5))

    return {
        "rows":              rows,
        "total_stcg_gain":   round(total_stcg_gain, 0),
        "total_ltcg_gain":   round(total_ltcg_gain, 0),
        "total_harvestable": round(total_harvestable, 0),
        "tax_today":         round(tax_today_total, 0),
        "tax_ltcg":          round(tax_ltcg_total, 0),
        "tax_savings":       round(tax_today_total - tax_ltcg_total, 0),
        "stcg_rate":         stcg_rate,
        "ltcg_rate":         ltcg_rate,
    }


def holding_period_status(
    ticker: str,
    trades_df: pd.DataFrame,
    today: _date | None = None,
    lt_window_days: int = TAX_LONGTERM_WINDOW_DAYS,
) -> dict | None:
    """Lightweight per-ticker holding-period read for the EXIT-signal tax lens.

    Reuses the same tax-lot reconstruction as build_tax_analysis but needs no
    price/PnL, so an exit card can cheaply annotate a position with its
    holding-period status without running the full portfolio tax analysis.

    Returns None when there are no open lots (nothing to annotate). Otherwise:
      {"gain_type": "LTCG"|"STCG"|"MIXED",
       "days_held": int,            # share-weighted average across open lots
       "days_to_ltcg": int,         # 0 if already fully long-term
       "acq_date": "YYYY-MM-DD",
       "near_ltcg": bool,           # STCG/MIXED and 0 < days_to_ltcg <= lt_window_days
       "lt_window_days": int}

    Awareness-only — the caller must NEVER gate, suppress, or reorder a
    recommendation on this. It is a visible context note layered on the
    unchanged investment signal.
    """
    if today is None:
        today = _today_et()
    lots = _build_open_lots(ticker, trades_df, today)
    total = sum(l["shares"] for l in lots)
    if not lots or total <= 1e-6:
        return None

    ltcg_shares = sum(l["shares"] for l in lots if l["days_held"] >= _STCG_THRESHOLD_DAYS)
    stcg_shares = total - ltcg_shares
    acq_date    = min(l["buy_date"] for l in lots)
    days_held   = int(round(sum(l["days_held"] * l["shares"] for l in lots) / total))

    if stcg_shares <= 1e-6:
        gain_type, days_to_ltcg = "LTCG", 0
    else:
        days_to_ltcg = max(0, min(
            _STCG_THRESHOLD_DAYS - l["days_held"]
            for l in lots if l["days_held"] < _STCG_THRESHOLD_DAYS
        ))
        gain_type = "STCG" if ltcg_shares <= 1e-6 else "MIXED"

    near_ltcg = gain_type in ("STCG", "MIXED") and 0 < days_to_ltcg <= lt_window_days
    return {
        "gain_type":      gain_type,
        "days_held":      days_held,
        "days_to_ltcg":   days_to_ltcg,
        "acq_date":       str(acq_date),
        "near_ltcg":      near_ltcg,
        "lt_window_days": lt_window_days,
    }


def wash_sale_risk(
    ticker: str,
    trades_df: pd.DataFrame,
    today: _date | None = None,
    window_days: int = TAX_WASH_SALE_DAYS,
) -> dict | None:
    """Wash-sale awareness for a prospective SELL of ``ticker``.

    IRS wash-sale rule: a LOSS is disallowed if you bought (or added to) the
    same security within 30 days before OR after the sale. This checks the
    BEFORE side — a recent same-ticker BUY within ``window_days`` — which is the
    only side the app can see at SELL time. Awareness-only; NEVER blocks a sale.

    The caller should surface this only when the sale is at a loss (the rule
    does not apply to gains). Returns None when there is no recent same-ticker
    BUY. Otherwise: {"recent_buy_date": "YYYY-MM-DD", "days_ago": int,
    "window_days": int}.
    """
    if trades_df is None or trades_df.empty:
        return None
    if today is None:
        today = _today_et()
    buys = trades_df[
        (trades_df["ticker"].astype(str).str.upper() == ticker.upper()) &
        (trades_df["action"].astype(str).str.upper().str.contains("BUY"))
    ]
    if buys.empty:
        return None
    dates = pd.to_datetime(
        buys["traded_at"], errors="coerce", utc=True, format="ISO8601"
    ).dropna()
    if dates.empty:
        return None
    most_recent = dates.max().date()
    days_ago = (today - most_recent).days
    if 0 <= days_ago <= window_days:
        return {
            "recent_buy_date": str(most_recent),
            "days_ago":        days_ago,
            "window_days":     window_days,
        }
    return None
