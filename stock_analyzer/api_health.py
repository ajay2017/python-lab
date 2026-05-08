"""
API health tracker.

Lightweight module-level stats accumulator that persists across Streamlit
reruns within the same worker process. Tracks yfinance, FMP and Supabase
call counts, error rates, rate-limit hits and data freshness.

Usage
-----
    from stock_analyzer import api_health
    api_health.record("yahoo_finance", "success")
    api_health.record("yahoo_finance", "rate_limit")
    health = api_health.get_health("yahoo_finance")
"""

import time as _t
from datetime import datetime as _dt
import pytz as _pytz

_ET = _pytz.timezone("America/New_York")


def _now() -> float:
    return _t.time()


def _blank():
    return {
        "calls":              0,
        "successes":          0,
        "errors":             0,
        "rate_limits":        0,
        "empty_returns":      0,
        "consecutive_errors": 0,
        "last_success_ts":    None,
        "last_error_ts":      None,
        "last_error_msg":     "",
        "session_start_ts":   _now(),
    }


_stats: dict = {
    "yahoo_finance": _blank(),
    "fmp":           _blank(),
    "supabase":      _blank(),
}


def record(source: str, event: str, msg: str = "") -> None:
    """
    Record an API event for a data source.

    Parameters
    ----------
    source : "yahoo_finance" | "fmp" | "supabase" (or any string)
    event  : "success" | "error" | "rate_limit" | "empty"
    msg    : optional error message (truncated to 120 chars)
    """
    if source not in _stats:
        _stats[source] = _blank()

    s   = _stats[source]
    now = _now()

    if event == "success":
        s["calls"]              += 1
        s["successes"]          += 1
        s["last_success_ts"]    = now
        s["consecutive_errors"] = 0

    elif event == "error":
        s["calls"]              += 1
        s["errors"]             += 1
        s["last_error_ts"]      = now
        s["last_error_msg"]     = str(msg)[:120]
        s["consecutive_errors"] += 1

    elif event == "rate_limit":
        s["rate_limits"]        += 1
        s["last_error_ts"]      = now
        s["last_error_msg"]     = "429 — Too Many Requests (rate limited)"
        s["consecutive_errors"] += 1

    elif event == "empty":
        s["empty_returns"]      += 1
        s["calls"]              += 1


def _age_str(ts: float | None) -> str:
    if ts is None:
        return "—"
    age = _now() - ts
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age / 60)}m ago"
    return f"{int(age / 3600)}h ago"


def get_health(source: str) -> dict:
    """
    Return a health summary dict for one source.

    Keys: calls, successes, errors, rate_limits, empty, freshness,
          level ("green"|"yellow"|"red"|"gray"), icon, last_error, consec_err
    """
    if source not in _stats:
        return {
            "calls": 0, "successes": 0, "errors": 0,
            "rate_limits": 0, "empty": 0, "freshness": "—",
            "level": "gray", "icon": "⚪", "last_error": "", "consec_err": 0,
        }

    s = _stats[source]

    # Health level logic
    if s["rate_limits"] >= 3 or s["consecutive_errors"] >= 5:
        level, icon = "red",    "🔴"
    elif (s["rate_limits"] >= 1 or
          s["consecutive_errors"] >= 2 or
          (s["calls"] > 0 and s["errors"] / s["calls"] > 0.20)):
        level, icon = "yellow", "🟡"
    elif s["calls"] == 0:
        level, icon = "gray",   "⚪"
    else:
        level, icon = "green",  "🟢"

    return {
        "calls":       s["calls"],
        "successes":   s["successes"],
        "errors":      s["errors"],
        "rate_limits": s["rate_limits"],
        "empty":       s["empty_returns"],
        "freshness":   _age_str(s["last_success_ts"]),
        "level":       level,
        "icon":        icon,
        "last_error":  s["last_error_msg"],
        "consec_err":  s["consecutive_errors"],
        "last_success_ts": s["last_success_ts"],
    }


def overall_level() -> tuple[str, str]:
    """
    Returns (level, icon) for the worst source.
    Used to colour the sidebar expander title.
    """
    levels = [get_health(src)["level"] for src in _stats]
    if "red"    in levels: return "red",    "🔴"
    if "yellow" in levels: return "yellow", "🟡"
    if "green"  in levels: return "green",  "🟢"
    return "gray", "⚪"


def reset() -> None:
    """Reset all counters — call when user clicks Refresh."""
    for src in list(_stats.keys()):
        _stats[src] = _blank()
