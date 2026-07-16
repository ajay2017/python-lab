"""
Decision Quality — retrospective investor-improvement analytics.

Two responsibilities:
  1. Monthly / quarterly grade computation (Feature B — Decision Quality Timeline)
  2. Per-trade prep-tier classification (Feature C — Workflow ROI)

All functions are pure computation (no Streamlit, no DB calls, no yfinance).
Import cost is negligible — only pandas.
"""

import pandas as pd
import pytz as _pytz
from datetime import date as _date, datetime as _dt, timedelta as _td
from typing import Optional

from stock_analyzer.constants import (
    DECISION_QUALITY_GRADE_A,
    DECISION_QUALITY_GRADE_B,
    DECISION_QUALITY_GRADE_C,
    DECISION_QUALITY_GRADE_D,
    DECISION_QUALITY_MIN_TRADES,
    DECISION_QUALITY_ALPHA_SCALE,
    WORKFLOW_ANALYST_LOOKBACK_DAYS,
    WORKFLOW_EARNINGS_WINDOW_DAYS,
    WORKFLOW_MIN_THESIS_LENGTH,
)

_ET = _pytz.timezone("America/New_York")

# ── Grade helpers ─────────────────────────────────────────────────────────────

def _grade_letter(score: float) -> str:
    if score >= DECISION_QUALITY_GRADE_A:
        return "A"
    if score >= DECISION_QUALITY_GRADE_B:
        return "B"
    if score >= DECISION_QUALITY_GRADE_C:
        return "C"
    if score >= DECISION_QUALITY_GRADE_D:
        return "D"
    return "F"


def _grade_label(letter: str) -> str:
    return {
        "A": "Elite",
        "B": "Disciplined",
        "C": "Learning",
        "D": "Struggling",
        "F": "Critical",
    }.get(letter, "—")


def _grade_color(letter: str) -> str:
    return {
        "A": "#16a34a",
        "B": "#2563eb",
        "C": "#b45309",
        "D": "#c2410c",
        "F": "#b91c1c",
    }.get(letter, "#6b7280")


def _parse_dt(val) -> _date | None:
    if val is None:
        return None
    try:
        if isinstance(val, _date) and not isinstance(val, _dt):
            return val
        ts = pd.to_datetime(val, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.astimezone(_ET).date()
    except Exception:
        return None


# ── Monthly profit-factor helper ──────────────────────────────────────────────

def _profit_factor(group: pd.DataFrame) -> float | None:
    """Gross gains / abs(gross losses) for a set of trades. None when no losers."""
    wins   = group.loc[group["realized_pnl"] > 0, "realized_pnl"].sum()
    losses = abs(group.loc[group["realized_pnl"] < 0, "realized_pnl"].sum())
    if losses < 0.01:
        return None   # all winners — meaningful but infinite; caller shows "∞"
    return round(float(wins) / float(losses), 2)


# ── Per-month overtrading multiplier (historical, not just current month) ─────

def _monthly_overtrading(trades_df: pd.DataFrame) -> dict[str, float | None]:
    """
    For each calendar month in trades_df, compute the trade count vs the
    rolling 12-month average of PRIOR months. Returns {month_str: multiplier}.
    Months with fewer than 2 prior reference months return None (insufficient
    baseline — not overtrading by definition, just unknown).
    """
    df = trades_df[trades_df["action"].isin(["BUY", "SELL"])].copy()
    if df.empty:
        return {}

    df["_dt"]       = df["traded_at"].apply(_parse_dt)
    df              = df.dropna(subset=["_dt"])
    df["month_str"] = df["_dt"].apply(lambda d: d.strftime("%Y-%m"))
    monthly_counts  = df.groupby("month_str").size().sort_index()
    months          = list(monthly_counts.index)

    result: dict[str, float | None] = {}
    for i, m in enumerate(months):
        prior = monthly_counts.iloc[max(0, i - 12):i]
        if len(prior) < 2:
            result[m] = None
            continue
        avg = float(prior.mean())
        result[m] = round(monthly_counts[m] / avg, 2) if avg > 0 else None
    return result


# ── Alpha subscore (0–100 scale) ──────────────────────────────────────────────

def _alpha_subscore(alpha_pct: float) -> float:
    """Map realized alpha vs SPY to a 0–100 subscore. ±ALPHA_SCALE = 100/0."""
    clamped = max(-DECISION_QUALITY_ALPHA_SCALE, min(DECISION_QUALITY_ALPHA_SCALE, alpha_pct))
    return round((clamped + DECISION_QUALITY_ALPHA_SCALE) / (2 * DECISION_QUALITY_ALPHA_SCALE) * 100, 1)


# ── Main grade builder ────────────────────────────────────────────────────────

def build_monthly_grades(
    trades_df: pd.DataFrame,
    spy_monthly_returns: dict[str, float] | None = None,
) -> list[dict]:
    """
    Compute per-calendar-month decision quality grades.

    `spy_monthly_returns` is an optional {month_str: return_pct} dict.
    When provided (benchmark data loaded), the grade includes an alpha
    subscore; otherwise the grade uses win_rate + profit_factor only.

    Returns list of dicts:
        month_str, year, win_rate, profit_factor, trade_count,
        overtrading_mult, alpha_vs_spy, composite_score, grade_letter,
        grade_label, grade_color, has_alpha
    Months with < DECISION_QUALITY_MIN_TRADES closed trades are excluded.
    """
    from stock_analyzer.trade_analytics import compute_extended_stats

    ext_df = compute_extended_stats(trades_df)
    if ext_df is None or (hasattr(ext_df, "empty") and ext_df.empty):
        return []

    overtrade_map = _monthly_overtrading(trades_df)
    spy_map = spy_monthly_returns or {}

    results: list[dict] = []
    for month_str, grp in ext_df.groupby("month_str"):
        if month_str == "Unknown":
            continue
        n = len(grp)
        if n < DECISION_QUALITY_MIN_TRADES:
            continue

        winners  = grp[grp["realized_pnl"] > 0]
        win_rate = round(len(winners) / n * 100, 1)
        pf       = _profit_factor(grp)

        # Win-rate subscore: linear 0 → 100 mapped 30% → 70%
        wr_sub = max(0.0, min(100.0, (win_rate - 30.0) / 40.0 * 100.0))

        # Profit-factor subscore: log-linear 0.5 → 2.0 → 0..100
        if pf is None:
            pf_sub = 100.0   # all-winner month — full score
        else:
            pf_sub = max(0.0, min(100.0, (pf - 0.5) / 1.5 * 100.0))

        # Alpha subscore
        spy_ret = spy_map.get(month_str)
        if spy_ret is not None:
            # Approximate realized-trade P&L % for the month
            month_pnl = float(grp["realized_pnl"].sum())
            # per-row cost basis is per-share; multiply before summing (not after)
            month_cost = abs(float((grp["cost_basis"].fillna(0) * grp["shares"].fillna(0)).sum()))
            if month_cost > 1:
                realized_pct = month_pnl / month_cost * 100
            else:
                realized_pct = 0.0
            alpha = round(realized_pct - spy_ret, 2)
            alpha_sub = _alpha_subscore(alpha)
            has_alpha = True
            # Composite: equal weights across 3 subscores
            composite  = round((wr_sub + pf_sub + alpha_sub) / 3.0, 1)
        else:
            alpha     = None
            has_alpha = False
            composite = round((wr_sub + pf_sub) / 2.0, 1)

        # Overtrading penalty
        ot_mult = overtrade_map.get(month_str)
        if ot_mult is not None and ot_mult >= 2.0:
            composite = max(0.0, composite - 25.0)
        elif ot_mult is not None and ot_mult >= 1.5:
            composite = max(0.0, composite - 10.0)

        composite = round(composite, 1)
        letter = _grade_letter(composite)

        try:
            year = int(month_str[:4])
        except ValueError:
            year = None

        results.append({
            "month_str":        month_str,
            "year":             year,
            "trade_count":      n,
            "win_rate":         win_rate,
            "profit_factor":    pf,
            "overtrading_mult": ot_mult,
            "alpha_vs_spy":     alpha,
            "composite_score":  composite,
            "grade_letter":     letter,
            "grade_label":      _grade_label(letter),
            "grade_color":      _grade_color(letter),
            "has_alpha":        has_alpha,
        })

    return sorted(results, key=lambda x: x["month_str"])


def build_quarterly_grades(monthly_grades: list[dict]) -> list[dict]:
    """
    Aggregate monthly grades into calendar quarters (Q1–Q4 per year).
    Trade-count-weighted average for composite score; sum for trade counts.
    """
    from collections import defaultdict
    buckets: dict[str, list[dict]] = defaultdict(list)
    for mg in monthly_grades:
        try:
            m = int(mg["month_str"][5:7])
            q = (m - 1) // 3 + 1
            key = f"{mg['year']}-Q{q}"
        except (ValueError, TypeError, KeyError):
            continue
        buckets[key].append(mg)

    results: list[dict] = []
    for qkey in sorted(buckets):
        rows = buckets[qkey]
        total_trades = sum(r["trade_count"] for r in rows)
        if total_trades == 0:
            continue
        # Weighted composite
        composite = sum(r["composite_score"] * r["trade_count"] for r in rows) / total_trades
        composite = round(composite, 1)
        win_rates = [r["win_rate"] for r in rows if r["win_rate"] is not None]
        pfs = [r["profit_factor"] for r in rows if r["profit_factor"] is not None]
        alphas = [r["alpha_vs_spy"] for r in rows if r["alpha_vs_spy"] is not None]
        ots = [r["overtrading_mult"] for r in rows if r["overtrading_mult"] is not None]
        letter = _grade_letter(composite)
        results.append({
            "period_str":       qkey,
            "trade_count":      total_trades,
            "win_rate":         round(sum(win_rates) / len(win_rates), 1) if win_rates else None,
            "profit_factor":    round(sum(pfs) / len(pfs), 2) if pfs else None,
            "overtrading_mult": round(sum(ots) / len(ots), 2) if ots else None,
            "alpha_vs_spy":     round(sum(alphas) / len(alphas), 2) if alphas else None,
            "composite_score":  composite,
            "grade_letter":     letter,
            "grade_label":      _grade_label(letter),
            "grade_color":      _grade_color(letter),
            "has_alpha":        any(r["has_alpha"] for r in rows),
        })
    return results


# ── SPY monthly return builder (from daily price dict) ───────────────────────

def build_spy_monthly_returns(prices: dict[str, float]) -> dict[str, float]:
    """
    Convert a {date_str: close} dict from fetch_benchmark_prices into
    {month_str: return_pct}. Uses first and last trading day per month.
    """
    by_month: dict[str, list[tuple[str, float]]] = {}
    for d_str, p in prices.items():
        month = d_str[:7]
        by_month.setdefault(month, []).append((d_str, p))

    result: dict[str, float] = {}
    for month, entries in by_month.items():
        entries_sorted = sorted(entries, key=lambda x: x[0])
        p_start = entries_sorted[0][1]
        p_end   = entries_sorted[-1][1]
        if p_start > 0:
            result[month] = round((p_end / p_start - 1) * 100, 4)
    return result


# ── Prep tier classification (Feature C — Workflow ROI) ──────────────────────

def classify_trade_prep(
    trade_row: pd.Series,
    analyst_df: pd.DataFrame | None,
    earnings_context_all: dict[str, list[dict]],
) -> dict:
    """
    Classify a single BUY trade row by prep tier.

    Signal 1 — thesis: user_thesis not null and len > WORKFLOW_MIN_THESIS_LENGTH
    Signal 2 — analyst: analyst_coverage row exists for ticker within
                WORKFLOW_ANALYST_LOOKBACK_DAYS before trade date
    Signal 3 — earnings: earnings_context row exists for ticker within
                WORKFLOW_EARNINGS_WINDOW_DAYS of the trade date (before or after)

    Returns {thesis_flag, analyst_flag, earnings_flag, tier_int, tier_label, tier_color}
    """
    ticker = str(trade_row.get("ticker") or "").strip().upper()

    trade_dt = _parse_dt(trade_row.get("traded_at"))
    if trade_dt is None:
        trade_date = None
    elif isinstance(trade_dt, _dt):
        trade_date = trade_dt.date()
    else:
        trade_date = trade_dt

    # Signal 1 — thesis
    thesis = str(trade_row.get("user_thesis") or "").strip()
    thesis_flag = len(thesis) >= WORKFLOW_MIN_THESIS_LENGTH

    # Signal 2 — analyst research saved within lookback window before trade
    analyst_flag = False
    if analyst_df is not None and not analyst_df.empty and trade_date is not None:
        ticker_rows = analyst_df[
            analyst_df["ticker"].astype(str).str.upper() == ticker
        ] if "ticker" in analyst_df.columns else pd.DataFrame()
        if not ticker_rows.empty:
            cutoff = trade_date - _td(days=WORKFLOW_ANALYST_LOOKBACK_DAYS)
            for _, ar in ticker_rows.iterrows():
                ad = _parse_dt(ar.get("article_date"))
                if ad is not None and cutoff <= ad <= trade_date:
                    analyst_flag = True
                    break

    # Signal 3 — earnings context within window of trade date
    earnings_flag = False
    if trade_date is not None:
        ec_rows = earnings_context_all.get(ticker, [])
        for ec in ec_rows:
            ad = _parse_dt(ec.get("article_date"))
            # before-only: research saved after the trade date is not pre-entry prep
            if ad is not None and -WORKFLOW_EARNINGS_WINDOW_DAYS <= (ad - trade_date).days <= 0:
                earnings_flag = True
                break

    signals = sum([thesis_flag, analyst_flag, earnings_flag])
    if signals == 3:
        tier_int, tier_label, tier_color = 3, "Full Prep",      "#22d3ee"
    elif thesis_flag and (analyst_flag or earnings_flag):
        tier_int, tier_label, tier_color = 2, "Thorough",       "#a78bfa"
    elif thesis_flag:
        tier_int, tier_label, tier_color = 1, "Basic",          "#94a3b8"
    else:
        tier_int, tier_label, tier_color = 0, "Cold Entry",     "#f59e0b"

    return {
        "thesis_flag":   thesis_flag,
        "analyst_flag":  analyst_flag,
        "earnings_flag": earnings_flag,
        "tier_int":      tier_int,
        "tier_label":    tier_label,
        "tier_color":    tier_color,
    }


def classify_all_buys(
    trades_df: pd.DataFrame,
    analyst_df: pd.DataFrame | None,
    earnings_context_all: dict[str, list[dict]],
) -> pd.DataFrame:
    """
    Apply classify_trade_prep to every BUY row in trades_df.
    Returns the BUY subset with added columns:
        thesis_flag, analyst_flag, earnings_flag, tier_int, tier_label, tier_color
    """
    buys = trades_df[trades_df["action"] == "BUY"].copy()
    if buys.empty:
        return buys

    tiers = buys.apply(
        lambda row: classify_trade_prep(row, analyst_df, earnings_context_all),
        axis=1,
    )
    tier_df = pd.DataFrame(list(tiers))
    for col in tier_df.columns:
        buys[col] = tier_df[col].values
    return buys.reset_index(drop=True)


def build_workflow_roi(
    classified_buys: pd.DataFrame,
    trades_df: pd.DataFrame,
    spy_prices: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Join classified BUY tiers to realized outcomes (SELL rows) and open
    position P&L for a per-trade ROI comparison by prep tier.

    Returns a DataFrame with columns:
        ticker, trade_date, tier_int, tier_label, tier_color,
        pnl_pct, realized_pnl, hold_days, is_closed, alpha_vs_spy
    """
    from stock_analyzer.benchmark_mirror import price_on_or_before
    from stock_analyzer.trade_analytics import compute_extended_stats

    if classified_buys.empty:
        return pd.DataFrame()

    ext_df = compute_extended_stats(trades_df)
    rows: list[dict] = []

    # Closed trades: join SELL rows back to the nearest preceding BUY tier
    if not (ext_df is None or (hasattr(ext_df, "empty") and ext_df.empty)):
        ext_df["_trade_date"] = ext_df["traded_at"].apply(_parse_dt)
        for _, sell in ext_df.iterrows():
            t = str(sell.get("ticker") or "").upper()
            sd = sell.get("_trade_date")
            if sd is None:
                continue
            # Match to nearest preceding BUY in classified_buys
            cands = classified_buys[
                (classified_buys["ticker"].astype(str).str.upper() == t)
            ].copy()
            if cands.empty:
                tier_int, tier_label, tier_color = 0, "Cold Entry", "#f59e0b"
                trade_date = sd
            else:
                cands["_dt"] = cands["traded_at"].apply(_parse_dt)
                cands = cands.dropna(subset=["_dt"]).sort_values("_dt")
                prior = cands[cands["_dt"] <= sd]
                if prior.empty:
                    # No buy before this sell — use the earliest known buy
                    matched = cands.iloc[0]
                else:
                    # Nearest preceding BUY — last entry after ascending sort
                    matched = prior.iloc[-1]
                tier_int   = int(matched.get("tier_int", 0))
                tier_label = str(matched.get("tier_label", "Cold Entry"))
                tier_color = str(matched.get("tier_color", "#f59e0b"))
                trade_date = matched.get("_dt") or sd

            pnl_pct = sell.get("pnl_pct")
            hold_d  = sell.get("hold_days")

            # SPY alpha for closed trade
            alpha = None
            if spy_prices and isinstance(trade_date, _date) and isinstance(sd, _date):
                spy_entry = price_on_or_before(spy_prices, trade_date)
                spy_exit  = price_on_or_before(spy_prices, sd)
                if spy_entry and spy_exit and spy_entry > 0:
                    spy_ret = (spy_exit / spy_entry - 1) * 100
                    alpha   = round(float(pnl_pct or 0) - spy_ret, 2)

            rows.append({
                "ticker":       t,
                "trade_date":   trade_date,
                "sell_date":    sd,
                "tier_int":     tier_int,
                "tier_label":   tier_label,
                "tier_color":   tier_color,
                "pnl_pct":      round(float(pnl_pct), 2) if pnl_pct is not None else None,
                "realized_pnl": sell.get("realized_pnl"),
                "hold_days":    int(hold_d) if hold_d is not None else None,
                "is_closed":    True,
                "alpha_vs_spy": alpha,
            })

    return pd.DataFrame(rows)
