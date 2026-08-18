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
        "quotas":             0,
        "auth_errors":        0,
        "parse_errors":       0,
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
    "finnhub":       _blank(),
    "fred":          _blank(),
    "supabase":      _blank(),
    "snaptrade":     _blank(),
}


def record(source: str, event: str, msg: str = "") -> None:
    """
    Record an API event for a data source.

    Parameters
    ----------
    source : "yahoo_finance" | "fmp" | "finnhub" | "fred" | "supabase" (or any string)
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

    elif event == "quota":
        s["calls"]              += 1
        s["quotas"]             += 1
        s["last_error_ts"]      = now
        s["last_error_msg"]     = str(msg)[:120] if msg else "402 — Payment Required (plan limit)"
        s["consecutive_errors"] += 1

    elif event == "auth":
        s["calls"]              += 1
        s["auth_errors"]        += 1
        s["last_error_ts"]      = now
        s["last_error_msg"]     = str(msg)[:120] if msg else "401/403 — Auth error (check API key)"
        s["consecutive_errors"] += 1

    elif event == "parse":
        s["calls"]              += 1
        s["parse_errors"]       += 1
        s["last_error_ts"]      = now
        s["last_error_msg"]     = str(msg)[:120] if msg else "Parse error (bad response body)"
        s["consecutive_errors"] += 1


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
    if s["auth_errors"] >= 1 or s["rate_limits"] >= 3 or s["consecutive_errors"] >= 5:
        level, icon = "red",    "🔴"
    elif (
        s["quotas"] >= 1 or
        s["rate_limits"] >= 1 or
        s["consecutive_errors"] >= 2 or
        s["parse_errors"] >= 3 or
        (s["calls"] > 0 and (s["errors"] + s["parse_errors"]) / s["calls"] > 0.20)
    ):
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
        "quotas":      s["quotas"],
        "auth_errors": s["auth_errors"],
        "parse_errors": s["parse_errors"],
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


def in_cooldown(source: str, cooldown_sec: float) -> bool:
    """True when `source` is currently 'tripped' (rate-limited / erroring hard)
    AND its last error was within `cooldown_sec`. Used by the orchestrator
    circuit-breaker to skip a provider that's actively 429-ing instead of
    re-calling it on every ticker. Reuses the same 'red' thresholds as
    get_health (rate_limits >= 3 or consecutive_errors >= 5). Auto-recovers:
    once cooldown_sec elapses since the last error, returns False again."""
    s = _stats.get(source)
    if not s:
        return False
    tripped = s["auth_errors"] >= 3 or s["rate_limits"] >= 3 or s["consecutive_errors"] >= 5
    if not tripped:
        return False
    last = s["last_error_ts"]
    if last is None:
        return False
    return (_now() - last) < cooldown_sec


# ── FMP daily quota cache (Option 2) ─────────────────────────────────────────
_fmp_quota: dict = {"count": None, "fetched_at": None}
_FMP_QUOTA_CACHE_TTL_SEC = 300  # refresh Supabase read at most every 5 min


def get_fmp_daily_quota() -> int | None:
    """Return FMP's today call count from Supabase, cached for 5 min.
    Returns None when Supabase is unavailable (chip hides the field then)."""
    from stock_analyzer import db as _db
    now = _now()
    if (
        _fmp_quota["fetched_at"] is None
        or now - _fmp_quota["fetched_at"] > _FMP_QUOTA_CACHE_TTL_SEC
    ):
        _fmp_quota["count"] = _db.get_daily_quota("fmp")
        _fmp_quota["fetched_at"] = now
    return _fmp_quota["count"]


def invalidate_fmp_quota_cache() -> None:
    """Force a fresh Supabase read on the next get_fmp_daily_quota() call."""
    _fmp_quota["fetched_at"] = None


def reset() -> None:
    """Reset all counters — call when user clicks Refresh."""
    for src in list(_stats.keys()):
        _stats[src] = _blank()
