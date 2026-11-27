"""
Intraday pullback entry window: fire when a morning go-verdict pick has dipped
from its open by PULLBACK_ENTRY_DIP_PCT while SPY is not in freefall AND the
pick is still above the stop this morning's pick set (G-35, beta-gate C4,
IntradayBelowStop).

Rule order in compute_intraday_entries (the first that applies decides):
  1. SPY guard: SPY missing / unreadable -> no entries ("unavailable");
     SPY down by spy_max_down or more -> no entries ("freefall").
  2. Per pick (blank ticker skipped silently):
     a. no live price -> excluded "no_price" (never a silent skip);
     b. not a dip (drop > -dip_pct) -> neither an entry nor excluded;
     c. the stop: a stock uses the pick's sizing.stop (or sizing.stop_at, the
        stop a "stop_infeasible" sizing marker carries). A sizing with neither
        key reads "no_size" (a sized-out marker); an empty / missing sizing, or
        an unreadable stop, reads "stop_unknown" (stocks and ETFs alike). An ETF
        pick re-anchors its stop at the 11:30 price (its bundle is fetched
        fresh), so its stop is max(pick stop, today's open - ATR_STOP_MULT x
        ATR); no readable ATR -> "stop_unknown". stop_basis records which side
        won ("pick" / "open");
     d. current <= stop (strict: stop + 0.01 passes) -> excluded "below_stop";
     e. otherwise an entry, carrying stop, stop_basis and stop_distance_pct.
  3. Entries sort composite first, then the bigger dip.
A pick that throws is excluded "unreadable".
`drop_atr` (the drop from the open in ATRs) is MEASUREMENT ONLY: it is logged
so a cap can be set from data later (owner U9 = a) and decides nothing here.
The only caller is cron_runner._run_intraday.

Pure module — takes pre-fetched price data (no direct network calls here);
caller (headless_alert_engine or cron_runner) provides the fast_info price
data. This keeps the computation testable and the I/O boundary explicit.
"""

from __future__ import annotations

from stock_analyzer.asset_type import ASSET_TYPE_ETF
from stock_analyzer.constants import ATR_STOP_MULT

def _safe_float(value, default=None):
    try:
        v = float(value)
        return default if v != v else v   # NaN != NaN
    except (TypeError, ValueError):
        return default


def _as_dict(v) -> dict:
    """A dict stays; anything else (None, junk) reads as empty. Used where an
    unreadable value fails SAFE (no stop -> excluded, no price -> excluded)."""
    return v if isinstance(v, dict) else {}


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


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
) -> dict:
    """Return the qualifying intraday pullback entries and the names left out.

    Args:
        go_picks:      go-verdict morning picks with ticker + composite_score
                       (+ sizing.stop, + atr when known)
        price_data:    output of fetch_intraday_prices() — {TICKER: {current, open}}
        spy_data:      {current, open} for SPY (None = not available, be conservative)
        dip_pct:       positive threshold — e.g. 1.5 means a 1.5% drop qualifies
        spy_max_down:  positive ceiling — e.g. 1.0 means suppress if SPY is down >1%

    Returns:
        {"entries":   qualifying pick dicts enriched with intraday_drop_pct,
                      current_price, open_price, stop, stop_basis,
                      stop_distance_pct, drop_atr,
         "excluded":  [{ticker, why: below_stop | stop_unknown | no_size |
                                     no_price | unreadable,
                        current_price, open_price, stop, stop_basis,
                        intraday_drop_pct, drop_atr}],
         "spy_guard": None | "unavailable" | "freefall"}
    """
    # SPY guard: fail safe — if SPY data is missing or unreadable (yfinance from
    # GH Actions datacenter is a known recurring miss), suppress ALL entries.
    # Provider outages correlate with volatility, so an unverifiable SPY on a rout
    # day is the dangerous case; "when in doubt, recommend nothing" wins here.
    if spy_data is None:
        return {"entries": [], "excluded": [], "spy_guard": "unavailable"}
    spy_cur  = _safe_float(spy_data.get("current"))
    spy_open = _safe_float(spy_data.get("open"))
    if spy_cur is None or spy_open is None or spy_open <= 0:
        return {"entries": [], "excluded": [], "spy_guard": "unavailable"}
    spy_drop = (spy_cur - spy_open) / spy_open * 100
    if spy_drop <= -abs(spy_max_down):
        # market in freefall — suppress all entry signals
        return {"entries": [], "excluded": [], "spy_guard": "freefall"}

    entries: list[dict] = []
    excluded: list[dict] = []

    def _out(ticker, why, **kw):
        excluded.append({"ticker": ticker, "why": why, "current_price": None,
                         "open_price": None, "stop": None, "stop_basis": None,
                         "intraday_drop_pct": None, "drop_atr": None, **kw})

    for pick in go_picks:
        ticker = ""
        try:
            ticker = str(pick.get("ticker") or "").upper()
            if not ticker:
                continue
            pr = _as_dict(price_data.get(ticker))
            current = _safe_float(pr.get("current"))
            open_p  = _safe_float(pr.get("open"))
            if current is None or open_p is None or open_p <= 0:
                _out(ticker, "no_price")
                continue
            drop = (current - open_p) / open_p * 100
            if drop > -abs(dip_pct):
                continue   # not a dip: most picks simply didn't pull back
            atr = _safe_float(pick.get("atr"))
            atr_ok = atr is not None and atr > 0
            drop_atr = round((open_p - current) / atr, 2) if atr_ok else None
            sz = pick.get("sizing")
            sz = sz if isinstance(sz, dict) else {}
            pick_stop = _safe_float(sz.get("stop"))
            if pick_stop is None or pick_stop <= 0:
                # a stop_infeasible sizing marker carries the stop as stop_at
                _at = _safe_float(sz.get("stop_at"))
                pick_stop = _at if _at is not None and _at > 0 else None
            stop, basis, why = pick_stop, "pick", None
            if pick_stop is None:
                # Stocks and ETFs alike: a sized-out marker is no_size, no sizing
                # or an unreadable stop is stop_unknown (an ETF never becomes an
                # entry on the open-based stop alone: it had no morning size).
                has_key = "stop" in sz or "stop_at" in sz
                why = "no_size" if (sz and not has_key) else "stop_unknown"
            elif pick.get("asset_type") == ASSET_TYPE_ETF:
                # The ETF pick's stop was re-derived from a bundle fetched at this
                # run, so it sits below TODAY's price and can never be breached:
                # measure it from the open too and keep the HIGHER of the two
                # (it can only tighten).
                if not atr_ok:
                    why = "stop_unknown"
                else:
                    open_stop = open_p - ATR_STOP_MULT * atr
                    if open_stop > pick_stop:
                        stop, basis = open_stop, "open"
            if why is None and (stop is None or stop <= 0):
                why = "stop_unknown"
            base = {"ticker": ticker, "current_price": round(current, 2),
                    "open_price": round(open_p, 2),
                    "stop": None if why else round(stop, 2),
                    "stop_basis": None if why else basis,
                    "intraday_drop_pct": round(drop, 2),
                    "drop_atr": drop_atr}
            if why:
                excluded.append({**base, "why": why})
            elif current <= stop:
                excluded.append({**base, "why": "below_stop"})
            else:
                entries.append({
                    **pick,
                    "intraday_drop_pct": round(drop, 2),
                    "current_price":     round(current, 2),
                    "open_price":        round(open_p, 2),
                    "stop":              round(stop, 2),
                    "stop_basis":        basis,
                    "stop_distance_pct": round((current - stop) / current * 100, 1),
                    "drop_atr":          drop_atr,
                })
        except Exception:
            # A pick that can't be read is never emailed, and never silent.
            if ticker:
                _out(ticker, "unreadable")

    # Highest composite first (U11), then the bigger dip.
    entries.sort(key=lambda x: (-(_safe_float(x.get("composite_score"), 0.0) or 0.0),
                                x["intraday_drop_pct"]))
    return {"entries": entries, "excluded": excluded, "spy_guard": None}


# ─── log lines (pure text; the cron lane prints them) ───────────────────────

def _fmt_num(v, spec: str = ".2f") -> str:
    f = _safe_float(v)
    return "n/a" if f is None else format(f, spec)


def candidates_log_line(result: dict) -> str:
    """One line naming every name that dipped past the trigger, entries and
    excluded alike, with its drop in ATRs (measurement only) and its stop."""
    rows = []
    for e in _as_list(result.get("entries")):
        stop = e.get("stop")
        rows.append((e.get("ticker"), e.get("intraday_drop_pct"), e.get("drop_atr"),
                     stop, e.get("current_price")))
    for x in _as_list(result.get("excluded")):
        if x.get("intraday_drop_pct") is None:
            continue   # no_price: never got a drop to report
        rows.append((x.get("ticker"), x.get("intraday_drop_pct"), x.get("drop_atr"),
                     x.get("stop"), x.get("current_price")))
    if not rows:
        return "intraday candidates: none"
    return "intraday candidates: " + " · ".join(
        f"{t} (drop {_fmt_num(d, '+.1f')}%, {_fmt_num(a)} ATR, "
        f"stop {_fmt_num(s)} vs now {_fmt_num(c)})"
        for t, d, a, s, c in rows)


def excluded_log_line(result: dict) -> str:
    """One line naming every dip / priced name that is NOT an entry, and why."""
    parts = []
    for x in _as_list(result.get("excluded")):
        why = x.get("why")
        t = x.get("ticker")
        if why == "below_stop":
            _from = ", from today's open" if x.get("stop_basis") == "open" else ""
            parts.append(f"{t} below_stop (now {_fmt_num(x.get('current_price'))} "
                         f"≤ stop {_fmt_num(x.get('stop'))}{_from})")
        else:
            parts.append(f"{t} {why}")
    return "intraday excluded: " + ("; ".join(parts) if parts else "none")


def no_entry_reason(result: dict) -> str:
    """Why the entry list is empty (for the 'no tickers qualify' log line)."""
    guard = result.get("spy_guard")
    if guard == "unavailable":
        return "SPY unavailable"
    if guard == "freefall":
        return "SPY in freefall"
    n = len(_as_list(result.get("excluded")))
    return "none pulled back enough" + (f" ({n} excluded)" if n else "")
