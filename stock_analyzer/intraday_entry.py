"""
Intraday pullback entry window: fire when a morning go-verdict pick has dipped
from its open by PULLBACK_ENTRY_DIP_PCT while SPY is not in freefall.

Pure module — takes pre-fetched price data (no direct network calls here);
caller (headless_alert_engine or cron_runner) provides the fast_info price
data. This keeps the computation testable and the I/O boundary explicit.
"""

from __future__ import annotations

def _safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_intraday_prices(tickers: list[str]) -> dict[str, dict]:
    """Fetch current price and today's open for each ticker via yf.Ticker.fast_info.

    Returns a dict keyed by uppercase ticker:
        {
            "AAPL": {"current": 192.5, "open": 195.1},
            ...
        }
    Missing/errored tickers are absent from the result.
    """
    import yfinance as yf  # lazy import — only needed at cron runtime

    result: dict[str, dict] = {}
    for t in tickers:
        try:
            fi = yf.Ticker(t).fast_info
            current = _safe_float(getattr(fi, "last_price", None))
            open_p  = _safe_float(getattr(fi, "open", None))
            if current is not None and open_p is not None and open_p > 0:
                result[t.upper()] = {"current": current, "open": open_p}
        except Exception:
            pass
    return result


def compute_intraday_entries(
    go_picks: list[dict],
    price_data: dict[str, dict],
    spy_data: dict | None,
    dip_pct: float,
    spy_max_down: float,
) -> list[dict]:
    """Return picks that qualify as intraday pullback entry windows.

    Args:
        go_picks:      go-verdict morning picks with ticker + composite_score
        price_data:    output of fetch_intraday_prices() — {TICKER: {current, open}}
        spy_data:      {current, open} for SPY (None = not available, be conservative)
        dip_pct:       positive threshold — e.g. 1.5 means a 1.5% drop qualifies
        spy_max_down:  positive ceiling — e.g. 1.0 means suppress if SPY is down >1%

    Returns:
        List of qualifying pick dicts enriched with intraday_drop_pct, current_price, open_price.
    """
    # SPY guard: fail safe — if SPY data is missing or unreadable (yfinance from
    # GH Actions datacenter is a known recurring miss), suppress ALL entries.
    # Provider outages correlate with volatility, so an unverifiable SPY on a rout
    # day is the dangerous case; "when in doubt, recommend nothing" wins here.
    if spy_data is None:
        return []
    spy_cur  = _safe_float(spy_data.get("current"))
    spy_open = _safe_float(spy_data.get("open"))
    if spy_cur is None or spy_open is None or spy_open <= 0:
        return []
    spy_drop = (spy_cur - spy_open) / spy_open * 100
    if spy_drop <= -abs(spy_max_down):
        return []   # market in freefall — suppress all entry signals

    entries = []
    for pick in go_picks:
        ticker = str(pick.get("ticker") or "").upper()
        if not ticker:
            continue
        pr = price_data.get(ticker)
        if pr is None:
            continue
        current = pr["current"]
        open_p  = pr["open"]
        drop    = (current - open_p) / open_p * 100
        if drop <= -abs(dip_pct):
            entries.append({
                **pick,
                "intraday_drop_pct": round(drop, 2),
                "current_price":     round(current, 2),
                "open_price":        round(open_p, 2),
            })

    # Most pulled-back first (biggest opportunity)
    entries.sort(key=lambda x: x["intraday_drop_pct"])
    return entries
