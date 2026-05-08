"""
Stock split detector.

Checks yfinance split history for each portfolio holding and identifies
positions where avg_cost and shares are out of sync due to an unaccounted
forward or reverse split.

Detection logic:
  1. If |current_price - avg_cost| / avg_cost < 35% → no split needed, skip.
  2. Fetch yfinance splits for the past 2 years.
  3. Calculate cumulative split ratio (product of all ratios in window).
  4. After applying ratio: adj_cost = avg_cost / ratio, adj_shares = shares * ratio.
  5. Validate: adj_cost must be within 60% of current_price (confirms it's a split,
     not a genuine price move).
  6. Return adjustment dict if validated, else None.
"""

import pandas as _pd
import yfinance as _yf
from datetime import datetime as _datetime
import pytz as _pytz

_LOOKBACK_DAYS    = 730    # 2 years of split history
_MIN_DISTORTION   = 0.35   # skip if cost vs price gap < 35%
_MAX_ADJ_DISTANCE = 0.60   # adj_cost must be within 60% of current price


def _today_et():
    return _datetime.now(_pytz.timezone("America/New_York")).date()


def fetch_splits(ticker: str, lookback_days: int = _LOOKBACK_DAYS) -> _pd.Series:
    """Return yfinance split history within the lookback window."""
    try:
        t      = _yf.Ticker(ticker)
        splits = t.splits
        if splits is None or splits.empty:
            return _pd.Series(dtype=float)
        cutoff = _pd.Timestamp.now(tz="UTC") - _pd.Timedelta(days=lookback_days)
        return splits[splits.index >= cutoff]
    except Exception:
        return _pd.Series(dtype=float)


def cumulative_ratio(splits: _pd.Series) -> float:
    """Product of all split ratios (>1 = forward split, <1 = reverse split)."""
    if splits.empty:
        return 1.0
    r = 1.0
    for v in splits:
        r *= float(v)
    return round(r, 6)


def detect_split_adjustment(
    ticker: str,
    stored_shares: float,
    stored_avg_cost: float,
    current_price: float,
    lookback_days: int = _LOOKBACK_DAYS,
) -> dict | None:
    """
    Returns a split adjustment dict if an unaccounted split is detected, else None.

    Keys in returned dict
    ---------------------
    ticker, split_ratio, split_date, split_type (Forward/Reverse),
    orig_shares, orig_avg_cost,
    adj_shares, adj_avg_cost,
    current_price, adj_pnl_pct, orig_pnl_pct
    """
    if current_price <= 0 or stored_avg_cost <= 0 or stored_shares <= 0:
        return None

    orig_pnl_pct = (current_price - stored_avg_cost) / stored_avg_cost * 100

    # Only investigate if cost vs price gap is large enough to suggest a split
    if abs(orig_pnl_pct) / 100 < _MIN_DISTORTION:
        return None

    splits = fetch_splits(ticker, lookback_days)
    if splits.empty:
        return None

    ratio = cumulative_ratio(splits)
    if abs(ratio - 1.0) < 0.01:   # no meaningful split
        return None

    adj_cost   = round(stored_avg_cost / ratio, 4)
    adj_shares = round(stored_shares * ratio, 2)

    # Validate: adjusted cost should be close to current price
    adj_dist = abs(current_price - adj_cost) / current_price
    if adj_dist > _MAX_ADJ_DISTANCE:
        return None

    adj_pnl_pct  = round((current_price - adj_cost) / adj_cost * 100, 1)
    split_type   = "Forward" if ratio > 1.0 else "Reverse"

    # Most recent split date for display
    try:
        last_dt      = splits.index[-1]
        split_date   = last_dt.tz_localize(None).date() if last_dt.tzinfo else last_dt.date()
    except Exception:
        split_date   = _today_et()

    # Human-readable ratio string  e.g. "5:1" or "1:10"
    if ratio >= 1.0:
        ratio_str = f"{int(round(ratio))}:1"
    else:
        ratio_str = f"1:{int(round(1/ratio))}"

    return {
        "ticker":        ticker,
        "split_ratio":   ratio,
        "ratio_str":     ratio_str,
        "split_date":    split_date,
        "split_type":    split_type,
        # Before adjustment
        "orig_shares":   stored_shares,
        "orig_avg_cost": stored_avg_cost,
        "orig_pnl_pct":  round(orig_pnl_pct, 1),
        # After adjustment
        "adj_shares":    adj_shares,
        "adj_avg_cost":  adj_cost,
        "adj_pnl_pct":   adj_pnl_pct,
        # Context
        "current_price": current_price,
    }


def detect_portfolio_splits(
    holdings_df: _pd.DataFrame,
    live_prices: dict,
    dismissed: set | None = None,
    lookback_days: int = _LOOKBACK_DAYS,
) -> list[dict]:
    """
    Scan all holdings for unaccounted splits.

    Parameters
    ----------
    holdings_df  : DataFrame with columns Ticker, Shares, Avg Cost ($)
    live_prices  : {ticker: {"price": float}}
    dismissed    : set of keys (f"{ticker}_{split_date}") to skip
    lookback_days: how far back to check yfinance splits

    Returns list of adjustment dicts, excluding already dismissed splits.
    """
    if holdings_df is None or holdings_df.empty:
        return []

    dismissed = dismissed or set()
    results   = []

    for _, row in holdings_df.iterrows():
        ticker     = str(row.get("Ticker", ""))
        shares     = float(row.get("Shares", 0) or 0)
        avg_cost   = float(row.get("Avg Cost ($)", 0) or 0)
        curr_price = float((live_prices.get(ticker) or {}).get("price", 0) or 0)

        if not ticker or shares <= 0 or avg_cost <= 0 or curr_price <= 0:
            continue

        adj = detect_split_adjustment(ticker, shares, avg_cost, curr_price, lookback_days)
        if adj is None:
            continue

        key = f"{ticker}_{adj['split_date']}"
        if key in dismissed:
            continue

        results.append(adj)

    return results
