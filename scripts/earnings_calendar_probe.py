"""Read-only probe of the raw shape of Yahoo's `yf.Ticker(t).calendar`.

Purpose (docs/plans/entry-fail-closed.md section 6, P0): decide whether an
EMPTY `{}` calendar means "no earnings date announced" or "the lookup failed"
before the earnings hold goes live. The app swallows every exception and any
non-dict (yfinance_provider.py ~286-294 `bundle`, ~346-359 `next_earnings`), so
both a failure and a genuine "no date" read as `None` today; this script shows
the RAW outcome per ticker so the two can be told apart.

Read-only: no DB writes, no app import (the parse below is REPLICATED from
yfinance_provider.py on purpose, so the probe sees what the provider sees even
if the provider changes). The only app call is behind the optional
`--app-provider` flag, which asks `stock_analyzer.data.fetch_next_earnings`
what the app itself gets (None vs a date).

Run (owner or implementer, one network pass; never needs secrets):

    python scripts/earnings_calendar_probe.py
    python scripts/earnings_calendar_probe.py AAPL ZZZZQX --app-provider

Default tickers: AAPL, MSFT, ANT, VOO, SPY, QQQ, ZZZZQX (a bogus symbol).
Summary buckets: date / empty-dict-or-no-key / raised / non-dict.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_TICKERS = ["AAPL", "MSFT", "ANT", "VOO", "SPY", "QQQ", "ZZZZQX"]


def _short(msg: object, n: int = 80) -> str:
    s = " ".join(str(msg).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _replicated_parse(cal: dict):
    """Same few lines as yfinance_provider.bundle / next_earnings (replicated,
    nothing imported from the app). Returns (parsed_str_or_None, raised_or_None).
    A truthy value that can't be indexed raises here; the provider's own
    try/except would swallow it into None, so it is reported separately."""
    try:
        dates = cal.get("Earnings Date") or cal.get("earningsDate")
        if dates:
            return str(dates[0])[:10], None
        return None, None
    except Exception as e:  # noqa: BLE001 - mirrors the provider's swallow
        return None, e


def probe_one(ticker: str, app_provider: bool) -> str:
    """Print one line for `ticker`; return its summary bucket."""
    import yfinance as yf

    try:
        cal = yf.Ticker(ticker).calendar
    except Exception as e:  # noqa: BLE001 - the point is to see every failure
        line = f"{ticker:8} RAISED {type(e).__name__}: {_short(e)}"
        bucket = "raised"
    else:
        if not isinstance(cal, dict):
            line = f"{ticker:8} NON-DICT returned {type(cal).__name__} value={_short(repr(cal))}"
            bucket = "non-dict"
        else:
            keys = sorted(str(k) for k in cal.keys())
            raw_key = "Earnings Date" if "Earnings Date" in cal else (
                "earningsDate" if "earningsDate" in cal else None)
            raw = cal.get(raw_key) if raw_key else None
            parsed, parse_err = _replicated_parse(cal)
            if parsed:
                bucket = "date"
            else:
                bucket = "empty-dict-or-no-key"
            line = (f"{ticker:8} DICT keys={keys} "
                    f"raw[{raw_key}]={_short(repr(raw))} type={type(raw).__name__} "
                    f"-> app parse={'DATE ' + parsed if parsed else 'None'}"
                    + (f" (parse raised {type(parse_err).__name__}: {_short(parse_err)})"
                       if parse_err else ""))
    if app_provider:
        try:
            from stock_analyzer.data import fetch_next_earnings
            line += f" | app fetch_next_earnings={fetch_next_earnings(ticker)!r}"
        except Exception as e:  # noqa: BLE001
            line += f" | app fetch_next_earnings RAISED {type(e).__name__}: {_short(e)}"
    print(line)
    return bucket


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="*", help="symbols (default: a fixed mixed list)")
    ap.add_argument("--app-provider", action="store_true",
                    help="also call the app's stock_analyzer.data.fetch_next_earnings")
    args = ap.parse_args(argv)
    tickers = [t.strip().upper() for t in (args.tickers or DEFAULT_TICKERS) if t.strip()]
    if args.app_provider:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    counts = {"date": 0, "empty-dict-or-no-key": 0, "raised": 0, "non-dict": 0}
    for t in tickers:
        counts[probe_one(t, args.app_provider)] += 1
    print("SUMMARY " + " | ".join(f"{k}={v}" for k, v in counts.items())
          + f" | total={len(tickers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
