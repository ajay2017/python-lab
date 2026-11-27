"""Which held names did the cron protective lanes NOT really check? (pure)

Why this exists (docs/plans/sell-check-unread-3a-3b.md, chunk C3; bug-register
rows CronStaleHeld and the cron leg of DetSpyFail): the premarket scan and the
EOD lane both read "all clear" when a holding was never priced, was priced from
the Supabase last-known-good cache, or came from a Yahoo bar series that ends
before the session being judged -- and when the S&P 500 history the
deterioration engine needs (or an engine itself) failed.  A clean scan that did
not look at a held name must not read as clean.  This module only DECIDES what
wasn't checked and words it; the cron (chunk C4 / C5) turns it into a red
heartbeat, a failure email, a footer line and a per-alert note.

It changes no alert and no threshold: a protective alert that was computed is
still sent (U-H), with a note.  There is no new constant: any one affected name
counts, no tolerance (U-A).

Three-state rule: `price_coverage` returns None when it could not verify
coverage (the held-ticker list is not a list / tuple, or anything inside raised)
-- None means "couldn't check", never "clean".  `{"state": "full"}` = checked
and every held name is freshly priced; `{"state": "gaps"}` = something wasn't.

Pure: no clock (today / expected session are parameters), no streamlit, no db,
no network.  Never raises: each public function wraps its body and returns the
safest loud value (price_coverage -> None, deterioration_basis -> "depth_only",
lane_failures -> a generic "held_coverage_unverified" line, text functions ->
[] / None).  Dates in user text read "Wed Nov 25" (no `%-d`, which fails on
Windows).  All text is plain: no `$`, no `**`, no HTML.
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd

from stock_analyzer.constants import REL_STRENGTH_LOOKBACK_DAYS
from stock_analyzer.data import (
    is_trading_day,
    previous_trading_day,
    trading_sessions_between,
)
from stock_analyzer.exit_advisor import _pct_return, _series_close
from stock_analyzer.held_stop import _et_dates

# Plain US symbols only (the finnhub_provider rule): a foreign-listed holding
# (SHOP.TO, 000660.KS) keeps its own exchange holidays, so a "behind" read on it
# would be wrong and must never turn the lane red.
_US_SYMBOL = re.compile(r"[A-Z]{1,5}(-[A-Z]{1,2})?")

_MSG_MAX = 80

# Also covers a bad `expected_session` (price_coverage -> None), which the cron can't
# produce: `expected_session()` of a real date is always a date.
_UNVERIFIED_LINE = ("held_coverage_unverified -- the held-ticker list was "
                    "unavailable, so it can't be confirmed every holding was checked")

# Unpriced reason -> phrase used in the failure line.
_NO_PRICE = "no price from any provider"
_BAD_LOT = "its shares or cost on record can't be used"


# ---------------------------------------------------------------- small helpers

def _fmt_date(d) -> str:
    """'Wed Nov 25' from a date or an ISO string; never %-d."""
    try:
        if isinstance(d, str):
            d = date.fromisoformat(d)
        return f"{d:%a %b} {d.day}"
    except Exception:
        return str(d)


def _sessions(n) -> str:
    try:
        n = int(n)
    except Exception:
        return "an unknown number of sessions"
    return "1 session" if n == 1 else f"{n} sessions"


def _short(msg) -> str:
    """One-line error text, truncated to 80 chars; never empty."""
    try:
        s = " ".join(str(msg).split())
    except Exception:
        s = ""
    s = s[:_MSG_MAX]
    return s or "unknown error"


def _norm(t):
    try:
        return str(t).strip().upper()
    except Exception:
        return ""


def _items(coverage: dict, key: str) -> list:
    """A coverage list field; a missing / non-list field reads as empty (the
    coverage dict itself is always built by price_coverage, so this is only a
    guard against a malformed hand-built dict)."""
    v = coverage.get(key)
    return v if isinstance(v, list) else []


def _reason_phrase(reason) -> str:
    return _BAD_LOT if reason == "invalid_shares_or_cost" else _NO_PRICE


def _priced_set(port_df) -> set:
    """Tickers port_df has a row for with numeric Price > 0 (the EOD guard's rule)."""
    if port_df is None or getattr(port_df, "empty", True):
        return set()
    cols = getattr(port_df, "columns", [])
    if "Ticker" not in cols or "Price" not in cols:
        return set()
    px = pd.to_numeric(port_df["Price"], errors="coerce")
    return {_norm(t) for t in port_df.loc[px > 0, "Ticker"]} - {""}


def _dropped_reasons(port_df) -> dict:
    out: dict = {}
    try:
        attrs = getattr(port_df, "attrs", None)
        drops = attrs.get("dropped_holdings") if isinstance(attrs, dict) else None
        for d in drops if isinstance(drops, (list, tuple)) else ():
            if isinstance(d, dict):
                t = _norm(d.get("ticker"))
                if t and d.get("reason"):
                    out[t] = str(d.get("reason"))
    except Exception:
        return {}
    return out


def _last_bar(df):
    """ET session date of the frame's last bar WITH a Close, or None.

    A trailing NaN-Close bar is skipped: the engines price from the last clean
    close, so that is the bar the check really used (YahooNaN)."""
    try:
        if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
            return None
        # A frame without dates (RangeIndex) would read as 1970-01-01, i.e. thousands
        # of sessions behind: undated, never "behind".
        if not isinstance(df.index, pd.DatetimeIndex):
            return None
        sub = df.loc[df["Close"].notna()]
        if sub.empty:
            return None
        d = _et_dates(sub.index).max()
        if pd.isna(d):
            return None
        return d.date()
    except Exception:
        return None


# ---------------------------------------------------------------------- public

def expected_session(today: date, *, lane: str):
    """The session whose close a fresh price read should reach.

    premarket (and any unknown lane): the last session strictly before `today`.
    eod: `today` if it is a trading day, else the last session before it.
    None on a bad `today` (price_coverage then returns None)."""
    try:
        if lane == "eod":
            return today if is_trading_day(today) else previous_trading_day(today)
        return previous_trading_day(today)
    except Exception:
        return None


def price_coverage(held_tickers, held_data, port_df, *, expected_session: date):
    """Coverage of the held book for one lane run, or None (couldn't verify).

    {"state": "full" | "gaps", "expected_session": iso,
     "held_count": int, "fresh_count": int,
     "unpriced":  [{"ticker", "reason"}],
     "cached":    [{"ticker", "last_bar": iso | None, "sessions_behind": int | None}],
     "behind":    [{"ticker", "last_bar": iso, "sessions_behind": int}],
     "not_dated": [ticker]}
    Lists are sorted by ticker.  A cached name is never also in `behind`."""
    try:
        if not isinstance(held_tickers, (list, tuple)):
            return None
        if not isinstance(expected_session, date):
            return None
        exp = expected_session
        held = sorted({_norm(t) for t in held_tickers} - {""})
        hd = {}
        if isinstance(held_data, dict):
            hd = {_norm(k): v for k, v in held_data.items()}
        priced = _priced_set(port_df)
        dropped = _dropped_reasons(port_df)

        unpriced, cached, behind, not_dated = [], [], [], []
        for t in held:
            bundle = hd.get(t)
            bundle = bundle if isinstance(bundle, dict) else None
            if t not in priced:
                if bundle is None:
                    reason = "bundle_failed"
                else:
                    reason = dropped.get(t) or "no_price_data"
                unpriced.append({"ticker": t, "reason": reason})
                continue
            if bundle is None:
                not_dated.append(t)         # priced elsewhere; nothing to date it by
                continue
            lb = _last_bar(bundle.get("df"))
            sb = None
            if lb is not None:
                sb = trading_sessions_between(lb, exp) if lb < exp else 0
            if bundle.get("stale_as_of") is not None:
                cached.append({"ticker": t,
                               "last_bar": lb.isoformat() if lb else None,
                               "sessions_behind": sb})
                continue
            if lb is None or lb > exp:
                not_dated.append(t)
            elif lb < exp:
                if _US_SYMBOL.fullmatch(t):
                    behind.append({"ticker": t, "last_bar": lb.isoformat(),
                                   "sessions_behind": sb})
                else:
                    not_dated.append(t)
        gaps = bool(unpriced or cached or behind)
        return {
            "state": "gaps" if gaps else "full",
            "expected_session": exp.isoformat(),
            "held_count": len(held),
            "fresh_count": len(held) - len(unpriced) - len(cached) - len(behind),
            "unpriced": unpriced,
            "cached": cached,
            "behind": behind,
            "not_dated": not_dated,
        }
    except Exception:
        return None


def deterioration_basis(spy_df, *, lookback: int = REL_STRENGTH_LOOKBACK_DAYS) -> str:
    """"full" when the S&P 500 relative-strength window is readable, else
    "depth_only" (the engine can only judge drawdown depth).  Uses the engine's
    own `_pct_return` so the boundary can't drift from it."""
    try:
        ret = _pct_return(_series_close(spy_df), lookback)
        return "full" if ret is not None else "depth_only"
    except Exception:
        return "depth_only"


def _names(items, key="ticker") -> list:
    return [i[key] for i in items]


def _cached_item_phrase(c) -> str:
    """'ABC (last price Wed Nov 25)' / 'DEF (Mon Nov 23, 2 sessions old)'."""
    lb, sb = c.get("last_bar"), c.get("sessions_behind")
    if lb is None:
        return f"{c['ticker']} (no dated price)"
    if not sb:
        return f"{c['ticker']} (last price {_fmt_date(lb)})"
    return f"{c['ticker']} ({_fmt_date(lb)}, {_sessions(sb)} old)"


def _group_unpriced(unpriced) -> dict:
    groups: dict = {}
    for u in unpriced:
        groups.setdefault(_reason_phrase(u.get("reason")), []).append(u["ticker"])
    return groups


def lane_failures(coverage, *, lane, deterioration_basis, deterioration_error,
                  risk_off_error) -> list:
    """Premarket failure lines (each makes the lane red), in this order:
    held_coverage_unverified / held_unchecked / held_cached / held_price_behind /
    deterioration_depth_only / deterioration_failed / risk_off_failed.

    EOD returns [] always: its failures come through `eod_withheld` (the EOD
    guard already handles an unverified list), and a behind name there is log
    only.  An unknown lane follows the premarket rule."""
    try:
        if lane == "eod":
            return []
        out: list = []
        if coverage is None:
            out.append(_UNVERIFIED_LINE)
        else:
            unpriced = _items(coverage, "unpriced")
            cached = _items(coverage, "cached")
            behind = _items(coverage, "behind")
            if unpriced:
                parts = "; ".join(f"{', '.join(names)} ({phrase})"
                                  for phrase, names in _group_unpriced(unpriced).items())
                out.append(
                    f"held_unchecked -- not checked this morning: {parts}; "
                    f"{'their' if len(unpriced) > 1 else 'its'} stop, "
                    "deterioration and risk-off checks did not run (protective alerts "
                    "that were computed were still sent)")
            if cached:
                out.append(
                    "held_cached -- checked against a saved price copy, not a fresh "
                    f"read: {', '.join(_cached_item_phrase(c) for c in cached)}; stop "
                    "and exit calls for these names may be wrong (protective alerts "
                    "that were computed were still sent)")
            if behind:
                exp = _fmt_date(coverage.get("expected_session"))
                items = ", ".join(
                    f"{b['ticker']} (ends {_fmt_date(b['last_bar'])}, "
                    f"{_sessions(b.get('sessions_behind'))} behind)" for b in behind)
                out.append(
                    f"held_price_behind -- price history ends before the {exp} close: "
                    f"{items}; "
                    f"{'their stops were' if len(behind) > 1 else 'its stop was'} "
                    "checked against an old close (protective "
                    "alerts that were computed were still sent)")
        if deterioration_basis == "depth_only":
            out.append(
                "deterioration_depth_only -- the S&P 500 6-month history couldn't be "
                "read, so deterioration exits were checked on drawdown depth alone; an "
                "exit that needs the stock to lag the market couldn't fire (stops were "
                "checked normally)")
        if deterioration_error is not None:
            out.append(
                f"deterioration_failed -- the deterioration check raised an error "
                f"({_short(deterioration_error)}); no deterioration exit was checked today")
        if risk_off_error is not None:
            out.append(
                f"risk_off_failed -- the risk-off de-risk check raised an error "
                f"({_short(risk_off_error)}); no de-risk trim was considered today")
        return out
    except Exception:
        return [_UNVERIFIED_LINE]


def email_lines(coverage, *, lane, deterioration_basis) -> list:
    """Alert-email footer lines (premarket wording).  One line per unpriced
    group, per cached name and per behind name, plus the depth-only line.
    [] for the eod lane (no alert email there) and on any error."""
    try:
        if lane == "eod":
            return []
        out: list = []
        if coverage is not None:
            for phrase, names in _group_unpriced(_items(coverage, "unpriced")).items():
                many = len(names) > 1
                why = ("No provider returned a price" if phrase == _NO_PRICE
                       else "Its share count or cost on record couldn't be used")
                if phrase != _NO_PRICE and many:
                    why = "Their share count or cost on record couldn't be used"
                if phrase == _NO_PRICE and many:
                    why = "No provider returned a price for them"
                out.append(
                    f"Not checked this morning: {', '.join(names)}. {why}, so "
                    f"{'their' if many else 'its'} stop and exit checks did not run. "
                    f"Look at {'them' if many else 'it'} in your broker before the open.")
            for c in _items(coverage, "cached"):
                t, lb, sb = c["ticker"], c.get("last_bar"), c.get("sessions_behind")
                if lb is None:
                    out.append(
                        f"{t} was checked against a saved copy of its price (date "
                        f"unknown), not a fresh read. Its stop and exit checks may be "
                        "wrong: check the live price before acting.")
                elif not sb:
                    out.append(
                        f"{t} was checked against a saved copy of its {_fmt_date(lb)} "
                        "price, not a fresh read. The copy may be from before that "
                        f"day's close, so check the live price before acting on any "
                        f"call for {t}.")
                else:
                    out.append(
                        f"{t} was checked against a saved copy of its {_fmt_date(lb)} "
                        f"price ({_sessions(sb)} old), not a fresh read. Its stop and "
                        "exit checks may be wrong: check the live price before acting.")
            exp = coverage.get("expected_session")
            for b in _items(coverage, "behind"):
                out.append(
                    f"{b['ticker']}'s price history ends {_fmt_date(b['last_bar'])}, "
                    f"{_sessions(b.get('sessions_behind'))} before the {_fmt_date(exp)} "
                    "close this check needs, so its stop was checked against an old "
                    "close. Check the live price before acting.")
        if deterioration_basis == "depth_only":
            out.append(
                "Deterioration exits were checked on drawdown depth only today: the "
                "S&P 500 history couldn't be read, so an exit that needs the stock to "
                "be lagging the market couldn't fire. Stop checks are unaffected.")
        return out
    except Exception:
        return []


def alert_note(coverage, ticker):
    """Sentence appended to an alert's `why` for a cached or behind ticker, else None."""
    try:
        if coverage is None:
            return None
        t = _norm(ticker)
        for c in _items(coverage, "cached"):
            if c["ticker"] == t:
                lb, sb = c.get("last_bar"), c.get("sessions_behind")
                if lb is None:
                    when = "date unknown"
                elif not sb:
                    when = f"last price {_fmt_date(lb)}"
                else:
                    when = f"last price {_fmt_date(lb)}, {_sessions(sb)} old"
                return (f"Priced from a saved copy ({when}), not a fresh read: "
                        "check the live price before acting.")
        for b in _items(coverage, "behind"):
            if b["ticker"] == t:
                return (f"Its price history ends {_fmt_date(b['last_bar'])}, not at the "
                        f"{_fmt_date(coverage.get('expected_session'))} close: check the "
                        "live price before acting.")
        return None
    except Exception:
        return None


def eod_withheld(coverage) -> list:
    """Cached tickers the EOD lane treats as unpriced (U-E).  [] when unknown."""
    try:
        if coverage is None:
            return []
        return sorted(c["ticker"] for c in _items(coverage, "cached"))
    except Exception:
        return []


def cached_tickers(coverage) -> list:
    """Sorted tickers served from the saved price copy (the capture writers skip
    them: a saved copy is never recorded as the day's own reading).  [] when the
    coverage is unknown, so an unverified run keeps recording."""
    try:
        if coverage is None:
            return []
        return sorted(c["ticker"] for c in _items(coverage, "cached"))
    except Exception:
        return []


def eod_shadow_lines(coverage) -> list:
    """EOD cron log lines for a fresh read whose last bar is before the session
    being judged (U-D shadow: log only at the EOD, never a failure).  One line per
    `behind` name, ISO dates; [] when none, unknown or malformed."""
    out: list = []
    try:
        if coverage is None:
            return out
        exp = coverage.get("expected_session")
        for b in _items(coverage, "behind"):
            try:
                out.append(f"eod price basis: {b['ticker']} last bar {b['last_bar']}, "
                           f"expected {exp} (shadow, not a failure)")
            except Exception:
                continue
        return out
    except Exception:
        return []


def gap_tickers(coverage) -> list:
    """Sorted tickers that are unpriced, cache-served or behind (the cron's dedup
    fingerprint reads it, so a later slot that re-prices a name changes the
    fingerprint and still sends).  [] when unknown.  `not_dated` is log only."""
    try:
        if coverage is None:
            return []
        names = set()
        for key in ("unpriced", "cached", "behind"):
            names.update(i["ticker"] for i in _items(coverage, key))
        return sorted(names)
    except Exception:
        return []


def log_lines(coverage) -> list:
    """The every-run cron log line; empty parts are omitted."""
    try:
        if coverage is None:
            return ["held coverage: unverified (held-ticker list unavailable)"]
        head = f"held coverage: {coverage['fresh_count']} of {coverage['held_count']} not flagged"
        parts = [head]
        cached = _items(coverage, "cached")
        if cached:
            items = []
            for c in cached:
                if c.get("last_bar") is None:
                    items.append(f"{c['ticker']}(last unknown)")
                else:
                    items.append(f"{c['ticker']}(last {c['last_bar']}, "
                                 f"{c.get('sessions_behind')} behind)")
            parts.append("cached=" + ",".join(items))
        behind = _items(coverage, "behind")
        if behind:
            parts.append("behind=" + ",".join(
                f"{b['ticker']}(last {b['last_bar']}, {b.get('sessions_behind')} behind)"
                for b in behind))
        unpriced = _items(coverage, "unpriced")
        if unpriced:
            parts.append("unpriced=" + ",".join(
                f"{u['ticker']}({u.get('reason')})" for u in unpriced))
        nd = _items(coverage, "not_dated")
        if nd:
            parts.append("not_dated=" + ",".join(nd))
        return ["; ".join(parts)]
    except Exception:
        return ["held coverage: unverified (held-ticker list unavailable)"]
