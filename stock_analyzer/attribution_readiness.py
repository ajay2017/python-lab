"""
Data-readiness measurement for alpha attribution (E2) — AUDIT ONLY.

Answers "can the history actually support an attribution decomposition yet?"
before anything is built on it. Same precedent as the F-192 Behavioral
Fingerprint readiness audit, which found 12% Buy-side completeness and changed
the build plan — measuring first is cheaper than discovering mid-build that the
data can't carry the feature.

Why this module exists rather than just reading the existing panel: the shipped
Alpha Attribution placeholder measured coverage as `(latest - earliest).days + 1`
— a CALENDAR SPAN. Two ways that overstates readiness:

  1. Only ~69% of calendar days are NYSE sessions, so a 180-calendar-day span
     can hold at most ~124 observations.
  2. More seriously, a gap is invisible. Snapshots for 5 sessions in March plus
     1 in August report 168 days of "coverage" backed by 6 real dates — the case
     pinned in tests/test_attribution_readiness.py. `daily_snapshots` is
     cron-written and cron gaps are demonstrated in this app, not hypothetical
     (F-239 found a lane outage on 2026-08-16 that read green until 08-21).

So this counts DISTINCT SNAPSHOT DATES against the NYSE sessions that should
have produced them, and reports the gaps.

Pure logic — no Streamlit, no DB, no network. Introduces NO thresholds: it
reports numbers and lets the caller (and the user) judge.

ONE HONEST CAVEAT about the gate. The `ALPHA_ATTRIBUTION_MIN_SNAPSHOT_DAYS = 180`
literal is untouched, but the quantity it is compared against changed from a
calendar span to distinct captured dates — so the effective bar moved materially
STRICTER (~180 sessions is ~8.6 months of unbroken capture, more with any gap).
"The gate is unchanged" is true of the literal and misleading about the bar; the
panel copy states the change, and so should anything else that describes this.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from stock_analyzer.constants import MARKET_CALENDAR_LAST_YEAR


def _is_session(d: date) -> bool:
    """`data.is_trading_day`, imported at call time rather than module scope.

    `data.is_trading_day` is the codebase's stated single source of truth for "is
    the market supposed to be open", so this module must use it — but `data.py`
    imports yfinance and the whole provider layer at module scope, and dragging
    that into an otherwise-pure module (and its test module) is exactly the
    import fan-out the 2026-08-17 test-collection audit named as the cause of
    slow collection.

    Deliberately NOT cached in a module global. The import-graph win comes from
    not importing at module scope, not from executing the statement once: after
    the first import this is a `sys.modules` hit plus an attribute fetch, so ~0.2
    ms across a 1,100-day walk. Caching would bind whichever object happened to
    be present on the FIRST call — and several tests `monkeypatch.setattr` a
    weekday-only lambda onto `data.is_trading_day`, which a module global would
    latch permanently past teardown, silently counting untabled holidays as
    sessions for every later caller in the process. Late binding is worth more
    than the 0.2 ms.
    """
    from stock_analyzer.data import is_trading_day
    return is_trading_day(d)


def _snapshot_dates(snaps_df) -> list[date]:
    """Sorted distinct snapshot dates, or []. Never raises."""
    if snaps_df is None or getattr(snaps_df, "empty", True):
        return []
    if "snapshot_date" not in getattr(snaps_df, "columns", []):
        return []
    try:
        parsed = pd.to_datetime(snaps_df["snapshot_date"], errors="coerce", utc=True)
    except (TypeError, ValueError):
        return []
    days = {d.date() for d in parsed.dropna()}
    return sorted(days)


def _expected_sessions(first: date, last: date) -> list[date]:
    """NYSE sessions from `first` to `last` inclusive.

    Uses `data.is_trading_day`, which its own docstring names the single source
    of truth for "is the market supposed to be open" — so this skips holidays,
    not just weekends. A naive weekday count would overstate the denominator and
    make completeness look worse than it is.
    """
    out, cur = [], first
    while cur <= last:
        if _is_session(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def snapshot_coverage(snaps_df) -> dict | None:
    """How much `daily_snapshots` history actually exists.

    Returns None when there are no usable snapshot dates at all — distinct from
    "some history, badly gapped", which returns a dict the caller must read.

    `n_dates` is the real observation count. `completeness_pct` is that against
    the NYSE sessions in the same span, so a gapped history cannot present as
    continuous. `largest_gap_sessions` is the longest unbroken run of missing
    sessions — the figure that matters most for attribution, since one long
    outage in the middle is far worse than the same number of scattered misses.
    """
    days = _snapshot_dates(snaps_df)
    if not days:
        return None
    first, last = days[0], days[-1]
    sessions = _expected_sessions(first, last)
    have = set(days)
    missing = [s for s in sessions if s not in have]

    # Longest consecutive run of missing SESSIONS (weekends/holidays are not
    # gaps — they were never expected).
    largest_gap, run = 0, 0
    for s in sessions:
        if s in have:
            run = 0
        else:
            run += 1
            largest_gap = max(largest_gap, run)

    n_sessions = len(sessions)
    return {
        "n_dates": len(days),
        "earliest": first,
        "latest": last,
        # Kept so the honest number can be shown BESIDE the old one rather than
        # silently replacing it — the gap between them is the finding.
        "span_days": (last - first).days + 1,
        "expected_sessions": n_sessions,
        "missing_sessions": len(missing),
        "largest_gap_sessions": largest_gap,
        "completeness_pct": round(len(days) / n_sessions * 100, 1) if n_sessions else None,
        # Snapshot dates that are NOT sessions (holiday/weekend writes). Not a
        # defect — recorded because it means n_dates can exceed expected.
        # Reuses the session list already built rather than re-walking the span.
        "non_session_dates": len(have - set(sessions)),
        # NYSE_HOLIDAYS is tabulated only through MARKET_CALENDAR_LAST_YEAR, and
        # is_trading_day returns True for an untabled holiday beyond it — so the
        # session denominator would be OVERstated and completeness UNDERstated.
        # That is the safe direction for an audit, but it must not fail silently.
        "calendar_stale": last.year > MARKET_CALENDAR_LAST_YEAR,
        "calendar_last_year": MARKET_CALENDAR_LAST_YEAR,
    }


def concentration(snaps_df) -> dict | None:
    """Herfindahl concentration of the LATEST snapshot.

    Attribution is noisier the fewer independent bets a book really holds, so
    `effective_positions` (1/H) is the honest count: 18 names at equal weight is
    18 effective, 18 names where three carry half the book is far fewer. Reported
    because Pass #1's warning about E2 was specifically about a *concentrated*
    high-turnover book.
    """
    days = _snapshot_dates(snaps_df)
    if not days or snaps_df is None:
        return None
    cols = getattr(snaps_df, "columns", [])
    if "shares" not in cols or "close_price" not in cols:
        return None
    try:
        parsed = pd.to_datetime(snaps_df["snapshot_date"], errors="coerce", utc=True)
        latest = snaps_df[parsed.dt.date == days[-1]]
        values = (
            pd.to_numeric(latest["shares"], errors="coerce")
            * pd.to_numeric(latest["close_price"], errors="coerce")
        ).dropna()
        values = values[values > 0]
    except (TypeError, ValueError, KeyError):
        return None
    total = float(values.sum())
    if total <= 0 or values.empty:
        return None
    weights = values / total
    herfindahl = float((weights ** 2).sum())
    return {
        "as_of": days[-1],
        "n_positions": int(values.shape[0]),
        "herfindahl": round(herfindahl, 4),
        "effective_positions": round(1.0 / herfindahl, 1) if herfindahl > 0 else None,
        "top_weight_pct": round(float(weights.max()) * 100, 1),
    }


def turnover(trades_df, snaps_df, *, lookback_days: int = 180) -> dict | None:
    """Two-way turnover over the trailing `lookback_days`.

    Definition, stated because turnover has several: traded notional
    (`shares × price`) divided by the mean daily portfolio market value over the
    same window. BUY and SELL legs are returned separately as well as summed,
    because a pure accumulation phase is not "churn" — the conventional
    `min(buys, sells)` answers that by stripping net build-out, and reporting
    both lets the reader see which they are looking at.

    `annualised_turnover_pct` is **withheld (None) until the window is actually
    as long as `lookback_days`.** Annualising a 10-day history multiplies by ~36
    and would print a spectacular, meaningless number — and the mean book value
    behind it would rest on 10 observations. Scaling a short, possibly gapped
    window up to a year is the same measurement sin this module exists to fix,
    so it is not committed one function further down. `window_turnover_pct` is
    always returned and is the honest figure.

    Returns None when either leg is unavailable — never a fabricated 0%, which
    would read as "no churn" and is the opposite of "unknown".

    Note the *unrounded* identity `traded == buys + sells` holds exactly, but the
    rounded output fields can differ by $1 when both legs land on a half-dollar
    (`round(a) + round(b)` vs `round(a + b)`). Display artifact only.
    """
    if trades_df is None or getattr(trades_df, "empty", True):
        return None
    if snaps_df is None or getattr(snaps_df, "empty", True):
        return None
    days = _snapshot_dates(snaps_df)
    if not days:
        return None

    window_end = days[-1]
    window_start = window_end - timedelta(days=lookback_days)

    cols = set(getattr(trades_df, "columns", []))
    # `action` is REQUIRED, not optional. SPLIT rows are synthetic: the
    # Apply-Split handler on the Portfolio page (app.py) writes a `db.save_trade`
    # row with action='SPLIT', shares = adjusted TOTAL shares and price =
    # adjusted avg cost (special-cased in db.recalculate_from_trades to overwrite
    # rather than accumulate). So shares × price on such a row is the position's
    # whole cost basis, not a trade — counting one would inject a
    # full-position-sized fake notional.
    # trades.py / portfolio_qa.py / evening_debrief.py all filter this already;
    # a missing column means we cannot tell trades from splits, which is
    # "unknown", not "assume they're all trades".
    if not {"shares", "price", "action", "traded_at"} <= cols:
        return None
    try:
        # utc=True per the mixed-offset convention — traded_at has been written
        # by several paths and pandas coerces mixed ISO offsets to NaT without it.
        tdates = pd.to_datetime(trades_df["traded_at"], errors="coerce", utc=True)
        action = trades_df["action"].astype(str).str.upper()
        is_real_trade = ~action.str.contains("SPLIT", na=False)
        notional = (
            pd.to_numeric(trades_df["shares"], errors="coerce")
            * pd.to_numeric(trades_df["price"], errors="coerce")
        )
        in_window = (
            (tdates.dt.date >= window_start)
            & (tdates.dt.date <= window_end)
            & is_real_trade
        )
        # Legs first, then `traded = buys + sells` so the identity holds BY
        # CONSTRUCTION rather than by external convention. The live `action`
        # vocabulary is exactly {BUY, SELL, SPLIT} across all five write paths,
        # but a legacy row whose action was backfilled None (load_trades does
        # that) stringifies to "NONE" — summing the legs excludes it from the
        # total too, instead of leaving it in `traded` and in neither leg.
        priced = in_window & notional.notna()
        buys = float(notional[priced & action.str.startswith("BUY")].sum())
        sells = float(notional[priced & action.str.startswith("SELL")].sum())
        traded = buys + sells
        # Count only rows that actually contributed notional, so an unparseable
        # shares/price row can't inflate the trade count against a total it
        # didn't move.
        n_trades = int((priced & (
            action.str.startswith("BUY") | action.str.startswith("SELL")
        )).sum())
    except (TypeError, ValueError, KeyError):
        return None

    # Mean daily book value across the snapshots inside the window. The grouper
    # is derived from `rows` itself rather than index-aligned from the parent
    # frame — a non-unique index (this comes from a DB read) would otherwise
    # raise on reindex.
    try:
        sdates = pd.to_datetime(snaps_df["snapshot_date"], errors="coerce", utc=True).dt.date
        rows = snaps_df[(sdates >= window_start) & (sdates <= window_end)].copy()
        rows["_mv"] = (
            pd.to_numeric(rows["shares"], errors="coerce")
            * pd.to_numeric(rows["close_price"], errors="coerce")
        )
        rows["_d"] = pd.to_datetime(
            rows["snapshot_date"], errors="coerce", utc=True
        ).dt.date
        per_day = rows.groupby("_d")["_mv"].sum()
        mean_value = float(per_day.mean()) if not per_day.empty else 0.0
        n_days_in_window = int(per_day.shape[0])
    except (TypeError, ValueError, KeyError):
        return None
    if mean_value <= 0:
        return None

    window_actual = max(1, (window_end - max(window_start, days[0])).days)
    window_pct = traded / mean_value * 100.0
    return {
        "window_days": window_actual,
        "lookback_days": lookback_days,
        # How many snapshot dates the mean book value rests on — the F-246
        # lesson applied to this module's own figure.
        "n_snapshot_dates_in_window": n_days_in_window,
        "n_trades": n_trades,
        "traded_notional": round(traded, 0),
        "buy_notional": round(buys, 0),
        "sell_notional": round(sells, 0),
        "mean_book_value": round(mean_value, 0),
        "window_turnover_pct": round(window_pct, 1),
        "annualised_turnover_pct": (
            round(window_pct * (365.0 / window_actual), 1)
            if window_actual >= lookback_days else None
        ),
    }
