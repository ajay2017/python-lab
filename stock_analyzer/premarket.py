"""
Pre-market intelligence: US futures, global indices, stock movers, economic events.

Designed to run during 4:00–9:29 AM ET on weekdays.  All fetches use yfinance
fast_info (single HTTP request per ticker) to stay within free-tier rate limits.
Results are meant to be cached by the caller for ~5 minutes (ttl=300).
"""
import pytz
from datetime import datetime

import pandas as pd
import yfinance as yf

from stock_analyzer import api_health as _ah

_ET = pytz.timezone("America/New_York")

# ── US index futures ─────────────────────────────────────────────────────────
_US_FUTURES = {
    "ES=F":  {"name": "S&P 500",      "icon": "🇺🇸"},
    "NQ=F":  {"name": "Nasdaq 100",   "icon": "💻"},
    "YM=F":  {"name": "Dow Jones",    "icon": "🏭"},
    "RTY=F": {"name": "Russell 2000", "icon": "📊"},
}

# ── Global indices (overnight read) ─────────────────────────────────────────
_GLOBAL_INDICES = {
    "^N225":  {"name": "Nikkei 225",     "flag": "🇯🇵"},
    "^HSI":   {"name": "Hang Seng",      "flag": "🇭🇰"},
    "^GDAXI": {"name": "DAX",            "flag": "🇩🇪"},
    "^FTSE":  {"name": "FTSE 100",       "flag": "🇬🇧"},
    "^FCHI":  {"name": "CAC 40",         "flag": "🇫🇷"},
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def is_premarket() -> bool:
    """True on weekdays between 4:00 AM and 9:29 AM ET."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:
        return False
    h = now.hour + now.minute / 60
    return 4.0 <= h < 9.5


def _pct(price, prev) -> float | None:
    if price and prev and float(prev) > 0:
        return round((float(price) - float(prev)) / float(prev) * 100, 2)
    return None


def _fast(sym: str) -> tuple[float | None, float | None]:
    """Return (last_price, previous_close) via fast_info."""
    try:
        fi = yf.Ticker(sym).fast_info
        return getattr(fi, "last_price", None), getattr(fi, "previous_close", None)
    except Exception as exc:
        _ah.record("yahoo_finance", "error", msg=f"pm fast_info {sym}: {str(exc)[:80]}")
        return None, None


# ── Fetch functions ───────────────────────────────────────────────────────────

def fetch_futures() -> list[dict]:
    """S&P 500 / Nasdaq / Dow / Russell futures with % change."""
    rows = []
    for sym, meta in _US_FUTURES.items():
        price, prev = _fast(sym)
        chg = _pct(price, prev)
        if price is not None:
            rows.append({
                "symbol":  sym,
                "name":    meta["name"],
                "icon":    meta["icon"],
                "price":   round(price, 2),
                "chg_pct": chg if chg is not None else 0.0,
            })
    return rows


def futures_tone(futures: list[dict]) -> str:
    """Derive expected open direction from ES=F change."""
    es = next((f for f in futures if f["symbol"] == "ES=F"), None)
    if es is None:
        return "flat"
    chg = es["chg_pct"]
    if chg >= 0.4:
        return "bull"
    if chg <= -0.4:
        return "bear"
    return "flat"


def fetch_global_markets() -> list[dict]:
    """Overnight % change for major global indices using 2-day history."""
    rows = []
    for sym, meta in _GLOBAL_INDICES.items():
        try:
            hist = yf.Ticker(sym).history(period="5d", auto_adjust=True)
            hist = hist.dropna(subset=["Close"])
            if len(hist) >= 2:
                prev  = float(hist["Close"].iloc[-2])
                close = float(hist["Close"].iloc[-1])
                chg   = _pct(close, prev)
                if chg is not None:
                    rows.append({
                        "symbol":  sym,
                        "name":    meta["name"],
                        "flag":    meta["flag"],
                        "price":   round(close, 2),
                        "chg_pct": chg,
                    })
        except Exception as exc:
            _ah.record("yahoo_finance", "error", msg=f"pm global {sym}: {str(exc)[:80]}")
    return sorted(rows, key=lambda x: x["chg_pct"], reverse=True)


def fetch_premarket_movers(
    tickers: list[str],
    held_data: dict,
) -> list[dict]:
    """
    Pre-market % change for held + watchlist stocks.
    Uses the last Close from held_data as the prior close baseline (most accurate),
    falls back to fast_info.previous_close.
    Only returns movers with |chg| >= 0.5%.
    """
    movers = []
    for sym in tickers:
        price, fi_prev = _fast(sym)
        if price is None:
            continue
        # Prefer the known close from already-loaded history
        prev = None
        hd = held_data.get(sym, {})
        df = hd.get("df") or hd.get("history")
        if df is not None and not df.empty and "Close" in df.columns:
            prev = float(df["Close"].iloc[-1])
        if prev is None:
            prev = fi_prev
        chg = _pct(price, prev)
        if chg is not None and abs(chg) >= 0.5:
            movers.append({
                "ticker":     sym,
                "pre_price":  round(price, 2),
                "prev_close": round(float(prev), 2) if prev else None,
                "chg_pct":    chg,
                "is_held":    sym in held_data,
            })
    return sorted(movers, key=lambda x: abs(x["chg_pct"]), reverse=True)[:12]


# ── Main entry point ──────────────────────────────────────────────────────────

def build_premarket_brief(
    held_tickers: list[str],
    watchlist: list[str],
    held_data: dict,
    macro_events: list[dict],
    today,
) -> dict:
    """
    Orchestrate all pre-market data into a single dict.

    Returns:
        tone          : 'bull' | 'bear' | 'flat'
        futures       : list[dict]  — US index futures
        global_markets: list[dict]  — overnight global index moves
        movers        : list[dict]  — pre-market movers from portfolio + watchlist
        events        : list[dict]  — today's HIGH/MEDIUM macro events
        as_of         : str         — "HH:MM AM/PM ET"
    """
    futures       = fetch_futures()
    tone          = futures_tone(futures)
    global_mkts   = fetch_global_markets()
    all_tickers   = list(dict.fromkeys(held_tickers + watchlist))
    movers        = fetch_premarket_movers(all_tickers, held_data)
    today_events  = [
        e for e in (macro_events or [])
        if e.get("date") == today and e.get("impact") in ("HIGH", "MEDIUM")
    ]
    return {
        "tone":            tone,
        "futures":         futures,
        "global_markets":  global_mkts,
        "movers":          movers,
        "events":          today_events,
        "as_of":           datetime.now(_ET).strftime("%I:%M %p ET"),
    }
