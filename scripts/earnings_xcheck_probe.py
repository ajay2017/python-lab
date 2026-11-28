"""Read-only probe of the FMP earnings calendar (buy-check C3, P1).

Purpose (docs/plans/entry-fail-closed-c3-spec.md, chunk 0): before the earnings
cross-check goes live, see what FMP's /stable/earnings-calendar actually returns
on this plan:
  * row counts and the min / max date for the future window [today, today+90]
    and the past window [today-45, today-1] (a round count or a last date short
    of the window's end suggests a row cap -> smaller chunks);
  * the keys of one row (is the actual-EPS field called `epsActual`?);
  * whether past rows carry an actual EPS (needed for the "just reported" rule);
  * per ticker: FMP upcoming / reported dates beside Yahoo's raw "Earnings Date".

Read-only: no DB, no app provider (the provider increments a DB quota counter),
no writes anywhere. Two FMP calls per run plus one Yahoo call per ticker.

Run (owner; the key is read from the environment only):

    $env:FMP_API_KEY = "<your key>"          # PowerShell
    python scripts/earnings_xcheck_probe.py
    python scripts/earnings_xcheck_probe.py CRWD NVDA MU ANT 000660.KS AAPL

Default tickers: CRWD NVDA MU ANT 000660.KS.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

DEFAULT_TICKERS = ["CRWD", "NVDA", "MU", "ANT", "000660.KS", "AAPL", "SNOW", "AMZN", "TGT", "AVGO"]
_BASE = "https://financialmodelingprep.com/stable/earnings-calendar"


def _fetch(key: str, frm: dt.date, to: dt.date):
    q = urllib.parse.urlencode({"from": frm.isoformat(), "to": to.isoformat(), "apikey": key})
    with urllib.request.urlopen(f"{_BASE}?{q}", timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _try(key: str, frm: dt.date, to: dt.date) -> str:
    """One window; returns a one-line outcome incl. FMP's own error message."""
    try:
        rows = _fetch(key, frm, to)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        return f"HTTP {e.code}: {' '.join(body.split())[:160]}"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:120]}"
    if isinstance(rows, list):
        dates = sorted(str(r.get("date"))[:10] for r in rows if isinstance(r, dict) and r.get("date"))
        act = sum(1 for r in rows if isinstance(r, dict) and r.get("epsActual") is not None)
        return (f"OK {len(rows)} rows, {dates[0] if dates else '-'} .. "
                f"{dates[-1] if dates else '-'}, epsActual on {act}")
    return f"non-list: {str(rows)[:160]}"


def _window_scan(key: str, today: dt.date) -> None:
    """Which window sizes does this plan accept? (the 2026-10-27 probe used 14 days)."""
    print("window scan (future from today, past ending yesterday):")
    for days in (7, 14, 30, 60, 90):
        print(f"  future {days:3}d: {_try(key, today, today + dt.timedelta(days=days))}")
    for days in (7, 14, 30, 45):
        y = today - dt.timedelta(days=1)
        print(f"  past   {days:3}d: {_try(key, y - dt.timedelta(days=days - 1), y)}")


def _summary(label: str, rows, frm: dt.date, to: dt.date) -> None:
    if not isinstance(rows, list):
        print(f"{label}: NOT A LIST -> {str(rows)[:200]}")
        return
    dates = sorted(str(r.get("date"))[:10] for r in rows if isinstance(r, dict) and r.get("date"))
    print(f"{label} [{frm} .. {to}]: {len(rows)} rows; "
          f"min {dates[0] if dates else '-'} max {dates[-1] if dates else '-'}")
    if rows and isinstance(rows[0], dict):
        print(f"  keys of one row: {sorted(rows[0].keys())}")
    with_actual = sum(1 for r in rows if isinstance(r, dict) and r.get("epsActual") is not None)
    print(f"  rows with epsActual not null: {with_actual}")
    if dates and dates[-1] < (to - dt.timedelta(days=3)).isoformat():
        print("  NOTE: last date is well short of the window end -> possible row cap")


def _yahoo_raw(t: str):
    try:
        import yfinance as yf
        cal = yf.Ticker(t).calendar
        if isinstance(cal, dict):
            return [str(d)[:10] for d in (cal.get("Earnings Date") or [])]
        return f"non-dict {type(cal).__name__}"
    except Exception as e:  # noqa: BLE001 - the probe reports every failure
        return f"raised {type(e).__name__}"


def main(argv: list[str]) -> int:
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        print("FMP_API_KEY is not set in the environment; set it and re-run.")
        return 2
    tickers = [a.upper() for a in argv] or DEFAULT_TICKERS
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    fut_from, fut_to = today, today + dt.timedelta(days=90)
    # 2026-11-27 first run: a 45-day past window is refused on the free plan
    # ("value set for 'from' is not available"); 30 days works.
    past_from, past_to = today - dt.timedelta(days=30), today - dt.timedelta(days=1)
    if "--scan" in argv:
        argv = [a for a in argv if a != "--scan"]
        _window_scan(key, today)
        print()
    tickers = [a.upper() for a in argv] or DEFAULT_TICKERS
    print()
    try:
        fut = _fetch(key, fut_from, fut_to)
        past = _fetch(key, past_from, past_to)
    except Exception as e:  # noqa: BLE001
        print(f"FMP call failed: {type(e).__name__}: {str(e)[:200]}")
        return 1
    print(f"today (ET) {today}")
    _summary("FUTURE", fut, fut_from, fut_to)
    _summary("PAST", past, past_from, past_to)

    def _rows_for(rows, t):
        alt = t.replace(".", "-") if "." in t else t.replace("-", ".")
        return [r for r in rows if isinstance(r, dict)
                and str(r.get("symbol", "")).upper() in (t, alt)]

    def _syms(rows):
        return sorted({str(r.get("symbol", "")).upper() for r in rows
                       if isinstance(r, dict) and r.get("symbol")})
    _fs, _ps = _syms(fut if isinstance(fut, list) else []), _syms(past if isinstance(past, list) else [])
    print(f"FUTURE distinct symbols ({len(_fs)}): {', '.join(_fs)}")
    print(f"PAST distinct symbols ({len(_ps)}): {', '.join(_ps)}")
    print()
    for t in tickers:
        up = sorted(str(r.get("date"))[:10] for r in _rows_for(fut if isinstance(fut, list) else [], t))
        rep = [(str(r.get("date"))[:10], r.get("epsActual"))
               for r in _rows_for(past if isinstance(past, list) else [], t)]
        print(f"{t:10} FMP upcoming {up or '-'} | FMP past (date, epsActual) {rep or '-'} "
              f"| Yahoo raw {_yahoo_raw(t)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
