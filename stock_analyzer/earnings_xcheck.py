"""Gate module: the FMP earnings-calendar cross-check behind the resolved earnings
date (buy-check C3a, docs/plans/entry-fail-closed-c3-spec.md; owner amendments
2026-11-27 after the P1 probe).

`earnings_date.resolve` needs a second opinion on Yahoo's date. This module reads it
from FMP's market-wide earnings calendar with exactly TWO calls (the free plan accepts
a `from` no older than about 30 days and a window up to 90 days ahead):
  * past   [today - EARNINGS_REPORTED_CLEAR_DAYS, today - 1]  -> rows with an actual EPS
  * future [today, today + EARNINGS_XCHECK_HORIZON_DAYS]      -> upcoming report dates

`calendar_index(today)` returns, never raising:
  {"state": "ok" | "failed", "as_of_day": iso, "fetched_at": ET iso, "error": str | None,
   "counts": {"upcoming": n_rows, "reported": n_rows_with_actual},
   "by_ticker": {SYMBOL: {"upcoming": [iso, ...], "reported": iso | None}}}
ANY failed call (an exception, or no provider answering) makes the WHOLE index `failed`:
a partial index must never read ok, because "no row" would then pass for a name whose
half of the window was never fetched. A failed index is never an empty one.
`reported` is set only from a row with a finite actual EPS.

FMP's free plan covers a fixed sample of about 70 large names, so an ok index with no
row for a symbol says nothing about that symbol (see `earnings_date`).

The index is cached per process: keyed by the ET day (it refetches at ET midnight), kept
EARNINGS_XCHECK_CACHE_TTL_SEC after a good read and EARNINGS_XCHECK_FAIL_RETRY_SEC after
a failed one; a lock makes `load_all`'s worker threads share ONE fetch. `_fetch_rows` is
the single network seam (tests replace it; tests/conftest.py makes the default raise).
The returned dict is shared: callers must treat it as read-only.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime, timedelta

from stock_analyzer.constants import (
    EARNINGS_PAST_DATE_HOLD_DAYS,
    EARNINGS_REPORTED_CLEAR_DAYS, EARNINGS_XCHECK_CACHE_TTL_SEC,
    EARNINGS_XCHECK_FAIL_RETRY_SEC, EARNINGS_XCHECK_HORIZON_DAYS,
)
from stock_analyzer.market_time import now_et, today_et

_LOCK = threading.Lock()
_CACHE: dict = {}          # {"day": iso, "index": dict, "expires": monotonic seconds}
_LAST: "dict | None" = None


def _clock() -> float:
    """Monotonic seconds (a seam so tests can age the cache)."""
    return time.monotonic()


def _fetch_rows(from_date: str, to_date: str):
    """The one network seam: FMP calendar rows [{ticker, date, when, eps_actual}] for the
    inclusive window, or None when no provider could answer. Raises on a provider
    error. Never called without the lock held."""
    from stock_analyzer.providers import orchestrator
    rows = orchestrator.get_earnings_calendar_rows_or_none(from_date, to_date)
    if rows is None:
        raise RuntimeError(
            "the FMP earnings calendar couldn't be read (FMP_API_KEY not set, or the call failed)")
    return rows


def reset_cache() -> None:
    """Forget the cached index and the last one (tests; never needed in production)."""
    global _LAST
    with _LOCK:
        _CACHE.clear()
        _LAST = None


def _parse_iso(raw) -> "date | None":
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _finite(v) -> bool:
    try:
        if v is None or isinstance(v, bool):
            return False
        return math.isfinite(float(v))
    except Exception:
        return False


def _failed_index(day_iso: str, error: str) -> dict:
    return {"state": "failed", "as_of_day": day_iso, "fetched_at": now_et().isoformat(),
            "error": str(error)[:200], "counts": {"upcoming": 0, "reported": 0},
            "by_ticker": {}}


def _build(today: date) -> dict:
    """Fetch both windows and build the index. Never raises."""
    day_iso = today.isoformat()
    try:
        past_from = today - timedelta(days=EARNINGS_REPORTED_CLEAR_DAYS)
        past_to = today - timedelta(days=1)
        fut_to = today + timedelta(days=EARNINGS_XCHECK_HORIZON_DAYS)
        past_rows = _fetch_rows(past_from.isoformat(), past_to.isoformat())
        fut_rows = _fetch_rows(today.isoformat(), fut_to.isoformat())
        if not isinstance(past_rows, list) or not isinstance(fut_rows, list):
            return _failed_index(day_iso, "an earnings-calendar call returned nothing usable")
        by: dict = {}
        n_up = n_rep = 0
        for r in fut_rows:
            if not isinstance(r, dict):
                continue
            d = _parse_iso(r.get("date"))
            sym = str(r.get("ticker") or "").strip().upper()
            if d is None or not sym or not (today <= d <= fut_to):
                continue
            ent = by.setdefault(sym, {"upcoming": [], "reported": None})
            if d.isoformat() not in ent["upcoming"]:
                ent["upcoming"].append(d.isoformat())
            n_up += 1
        for r in past_rows:
            if not isinstance(r, dict) or not _finite(r.get("eps_actual")):
                continue
            d = _parse_iso(r.get("date"))
            sym = str(r.get("ticker") or "").strip().upper()
            if d is None or not sym or not (past_from <= d <= past_to):
                continue
            ent = by.setdefault(sym, {"upcoming": [], "reported": None})
            if ent["reported"] is None or d.isoformat() > ent["reported"]:
                ent["reported"] = d.isoformat()
            n_rep += 1
        for ent in by.values():
            ent["upcoming"].sort()
        return {"state": "ok", "as_of_day": day_iso, "fetched_at": now_et().isoformat(),
                "error": None, "counts": {"upcoming": n_up, "reported": n_rep},
                "by_ticker": by}
    except Exception as exc:  # noqa: BLE001 - a failed read is a failed index, never raised
        return _failed_index(day_iso, f"{type(exc).__name__}: {exc}")


def calendar_index(today=None) -> dict:
    """The cached cross-check index for the ET day `today` (default `today_et()`); see
    the module docstring. Never raises."""
    global _LAST
    try:
        d = today if isinstance(today, date) and not isinstance(today, datetime) else today_et()
    except Exception:
        d = today_et()
    day_iso = d.isoformat()
    try:
        with _LOCK:
            now = _clock()
            if _CACHE.get("day") == day_iso and now < _CACHE.get("expires", 0.0):
                return _CACHE["index"]
            idx = _build(d)
            ttl = (EARNINGS_XCHECK_CACHE_TTL_SEC if idx.get("state") == "ok"
                   else EARNINGS_XCHECK_FAIL_RETRY_SEC)
            _CACHE.update({"day": day_iso, "index": idx, "expires": now + ttl})
            _LAST = idx
            return idx
    except Exception as exc:  # noqa: BLE001
        return _failed_index(day_iso, f"{type(exc).__name__}: {exc}")


def last_index() -> "dict | None":
    """The most recent index this process built (None before the first). Never fetches."""
    return _LAST


def _time_text(iso) -> str:
    try:
        dt = datetime.fromisoformat(str(iso))
        return f"{dt:%I:%M %p}".lstrip("0") + " ET"
    except Exception:
        return "unknown time"


def log_line(index=None) -> str:
    """The cron log line for an index (default `last_index()`):
    "earnings cross-check: ok, N upcoming / M reported rows (fetched 9:44 AM ET)" or
    "earnings cross-check: couldn't be read (...); names whose date on file passed in the
    last N days read unconfirmed" (a label only; the buy hold is C3b) (or "not run" when there is no index). Never raises."""
    try:
        idx = index if index is not None else _LAST
        if not isinstance(idx, dict):
            return "earnings cross-check: not run in this process"
        if idx.get("state") == "ok":
            c = idx.get("counts")
            c = c if isinstance(c, dict) else {}
            return (f"earnings cross-check: ok, {c.get('upcoming', 0)} upcoming / "
                    f"{c.get('reported', 0)} reported rows "
                    f"(fetched {_time_text(idx.get('fetched_at'))})")
        err = str(idx.get("error") or "no detail")[:120]
        # C3a review: label only (the hold ships in C3b), so say what the read means.
        return (f"earnings cross-check: couldn't be read ({err}); names whose date on "
                f"file passed in the last {EARNINGS_PAST_DATE_HOLD_DAYS} days read unconfirmed")
    except Exception:
        return "earnings cross-check: log line failed"
