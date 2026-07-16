"""
Benchmark Mirror — shadow portfolio comparison.

Computes a "shadow portfolio" that invested the same cash flows (baseline +
deposits/withdrawals from account_flows) into a benchmark ticker (SPY, QQQ)
instead of individual stocks. Enables a fair money-weighted comparison
between active stock-picking and passive investing.

All functions are pure computation (no Streamlit, no DB calls). The caller
is responsible for fetching data and caching results.
"""

import pandas as pd
import pytz as _pytz
from datetime import date as _date, timedelta as _td
from typing import Optional

_ET = _pytz.timezone("America/New_York")


def fetch_benchmark_prices(ticker: str, start: _date, end: _date) -> dict[str, float]:
    """
    Fetch daily adjusted closing prices for a benchmark ticker via yfinance.
    Returns {date_str: close_price}. Returns {} on any failure — callers must
    gate on an empty dict before rendering.
    """
    try:
        import yfinance as yf
        raw = yf.download(
            ticker,
            start=str(start),
            end=str(end + _td(days=1)),   # yfinance end is exclusive
            progress=False,
            auto_adjust=True,
            multi_level_index=False,
        )
        if raw.empty or "Close" not in raw.columns:
            return {}
        return {
            str(idx.date()): float(p)
            for idx, p in raw["Close"].items()
            if pd.notna(p)
        }
    except Exception:
        return {}


def price_on_or_before(prices: dict[str, float], target: _date) -> float | None:
    """Return the benchmark close on target or the nearest prior trading day (up to 5 days back)."""
    for delta in range(6):
        p = prices.get(str(target - _td(days=delta)))
        if p is not None:
            return p
    return None


def build_shadow_portfolio(
    flows: list[dict],
    prices: dict[str, float],
    today: _date,
) -> dict:
    """
    Compute what the user's cash flows would be worth today if every dollar
    had been invested in the benchmark instead of individual stocks.

    `flows` is the raw list from db.load_account_flows() — rows with
    flow_type in ('baseline', 'deposit', 'withdrawal').

    Returns:
        shadow_ending_value  float | None  — current shadow portfolio value
        flow_attribution     list[dict]    — per-flow breakdown
        total_invested       float         — sum of all positive cash flows
    """
    today_price = price_on_or_before(prices, today)
    if today_price is None or not flows:
        return {"shadow_ending_value": None, "flow_attribution": [], "total_invested": 0.0}

    attribution: list[dict] = []
    total_units  = 0.0
    total_invested = 0.0

    for f in flows:
        ftype = str(f.get("flow_type") or "").strip().lower()
        if ftype not in ("baseline", "deposit", "withdrawal"):
            continue
        try:
            amount = float(f.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        signed = amount if ftype != "withdrawal" else -amount

        fd_raw = f.get("flow_date")
        try:
            fd = _date.fromisoformat(str(fd_raw)) if fd_raw else None
        except ValueError:
            fd = None
        if fd is None:
            continue

        fp = price_on_or_before(prices, fd)
        if fp is None or fp <= 0:
            continue

        units  = signed / fp
        total_units += units
        if signed > 0:
            total_invested += signed

        current_value = round(units * today_price, 2)
        attribution.append({
            "date":           fd,
            "flow_type":      ftype,
            "amount":         signed,
            "price_at_entry": round(fp, 2),
            "units":          round(units, 6),
            "current_value":  current_value,
            "gain_loss":      round(current_value - signed, 2),
            "return_pct":     round((current_value / signed - 1) * 100, 2) if signed != 0 else None,
        })

    shadow_ending_value = round(total_units * today_price, 2)
    return {
        "shadow_ending_value": shadow_ending_value,
        "flow_attribution":    attribution,
        "total_invested":      round(total_invested, 2),
    }


def build_benchmark_curve(
    prices: dict[str, float],
    flows: list[dict],
    start: _date,
    end: _date,
) -> dict[str, dict]:
    """
    Build a daily series indexed to 100 at `start` for the benchmark AND the
    shadow portfolio value. Returns {date_str: {benchmark_idx, shadow_value}}.

    Used for the Cumulative Growth chart. The shadow_value series starts at
    zero before the first cash flow and grows as units accumulate.
    """
    dates_in_range = sorted(d for d in prices if str(start) <= d <= str(end))
    if not dates_in_range:
        return {}

    base_price = price_on_or_before(prices, start)
    if not base_price:
        return {}

    # Pre-process flows into (date, signed_amount) pairs
    valid_flows: list[tuple[_date, float]] = []
    for f in flows:
        ftype = str(f.get("flow_type") or "").strip().lower()
        if ftype not in ("baseline", "deposit", "withdrawal"):
            continue
        try:
            amount = float(f.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        fd_raw = f.get("flow_date")
        try:
            fd = _date.fromisoformat(str(fd_raw)) if fd_raw else None
        except ValueError:
            fd = None
        if fd is None:
            continue
        signed = amount if ftype != "withdrawal" else -amount
        valid_flows.append((fd, signed))
    valid_flows.sort(key=lambda x: x[0])

    result: dict[str, dict] = {}
    cum_units = 0.0
    flow_idx  = 0

    for d_str in dates_in_range:
        d = _date.fromisoformat(d_str)
        # Apply flows whose date falls on or before this trading day
        while flow_idx < len(valid_flows) and valid_flows[flow_idx][0] <= d:
            fd, signed = valid_flows[flow_idx]
            fp = price_on_or_before(prices, fd)
            if fp and fp > 0:
                cum_units += signed / fp
            flow_idx += 1

        price = prices[d_str]
        result[d_str] = {
            "benchmark_idx": round(price / base_price * 100, 4),
            "shadow_value":  round(cum_units * price, 2),
        }

    return result


def build_drawdown_series(prices: dict[str, float], start: _date, end: _date) -> dict[str, float]:
    """
    Benchmark drawdown from peak (%) over the date range.
    Returns {date_str: drawdown_pct} where values are ≤ 0.
    """
    dates_in_range = sorted(d for d in prices if str(start) <= d <= str(end))
    if not dates_in_range:
        return {}

    result: dict[str, float] = {}
    peak = prices[dates_in_range[0]]
    for d_str in dates_in_range:
        p = prices[d_str]
        if p > peak:
            peak = p
        dd = round((p / peak - 1) * 100, 2)
        result[d_str] = dd
    return result


def compute_shadow_mwr(
    baseline_value: float,
    baseline_date: _date,
    shadow_ending_value: float,
    today: _date,
    flows: list[dict],
) -> dict | None:
    """
    Modified Dietz money-weighted return for the shadow portfolio,
    using the same formula as account.money_weighted_return so the
    two returns are directly comparable.

    Returns {period_return_pct, annualized_pct, days} or None.
    """
    days = (today - baseline_date).days
    if days <= 0 or shadow_ending_value is None:
        return None

    net_flow  = 0.0
    weighted  = 0.0
    for f in flows:
        ftype = str(f.get("flow_type") or "").strip().lower()
        if ftype not in ("deposit", "withdrawal"):
            continue
        fd_raw = f.get("flow_date")
        try:
            fd = _date.fromisoformat(str(fd_raw)) if fd_raw else None
        except ValueError:
            fd = None
        if fd is None or fd < baseline_date or fd > today:
            continue
        try:
            amt = float(f.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        signed  = amt if ftype == "deposit" else -amt
        net_flow += signed
        w = ((today - fd).days / days) if days > 0 else 0.0
        weighted += signed * w

    denom = float(baseline_value) + weighted
    if denom <= 0:
        return None

    gain           = float(shadow_ending_value) - float(baseline_value) - net_flow
    period_return  = gain / denom
    annualized     = ((1 + period_return) ** (365.0 / days) - 1) if days >= 30 and (1 + period_return) > 0 else None

    return {
        "days":              days,
        "period_return_pct": round(period_return * 100, 2),
        "annualized_pct":    round(annualized * 100, 2) if annualized is not None else None,
    }
