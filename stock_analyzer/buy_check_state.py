"""Gate module: "did a buy check actually run?" states for the entry checks
(docs/plans/entry-fail-closed.md §2; chunk C1).

A buy check that couldn't run must not read as a check that passed. This module
holds the pure state readers the entry surfaces will consult. Pure: no clock, no
streamlit, no db; inputs are never mutated; nothing here raises.

C1 shipped `earnings_state`. Nothing reads it for a decision yet (C3 wires
`entry_eligibility.check_earnings`); the cron scan log counts it so the real
failure rate is visible first. C2a (this edit) adds `fit_state` and
`beta_leg_could_bind`, read ONLY by the cron's `compute_watchlist_entries` and
`watchlist_advisor._portfolio_risk_gate` (when the caller passes a `fit_state`; the
app's Watchlist doesn't until C2b). `tone_unread` and the banner text builders
arrive in C4.

  earnings_state(bundle) -> {"state": "not_applicable" | "date" | "no_date" | "failed",
                             "date": "YYYY-MM-DD" | None}

THE FOUR STATES (the U2 owner decision, 2026-11-25):
  not_applicable  an ETF / fund (`asset_type` == the app's ETF constant): earnings
                  don't apply, even with a stray date on the bundle.
  date            a parseable earnings date is present (`str(x)[:10]` parsed as
                  %Y-%m-%d, the same read the app's surfaces use).
  no_date         the lookup succeeded and Yahoo announced no date (provider status
                  "no_date", e.g. a fresh IPO): passes.
  failed          the lookup failed, couldn't be told, or the bundle is a stale
                  last-known-good copy: a buy check that did not run.

Rule order: not a dict -> failed; ETF -> not_applicable; stale cached bundle
(`stale_as_of` set by `bundle_loader.load_bundle` on the Supabase-cache path) ->
failed, even if it carries a date; a truthy `earnings` value -> `date` when it
parses, else failed (a value we can't read is not "nothing announced"); provider
status "no_date" -> no_date; everything else (status "failed", no status key at all,
an unknown status, a falsy date with no status) -> failed. An absent
`earnings_status` key is "couldn't tell"; that is safe because `load_bundle` is the
only production bundle producer and always stamps the key (a test pins it).
"""
from __future__ import annotations

import math
from datetime import datetime

from stock_analyzer import asset_type
from stock_analyzer.constants import (
    PORTFOLIO_BETA_CEILING, TICKER_BETA_CRITICAL, UNCLASSIFIED_SECTOR,
)
from stock_analyzer.portfolio import resolve_sector

STATE_NOT_APPLICABLE = "not_applicable"
STATE_DATE = "date"
STATE_NO_DATE = "no_date"
STATE_FAILED = "failed"

_FAILED = {"state": STATE_FAILED, "date": None}


def _parse_date(raw) -> "str | None":
    """'YYYY-MM-DD' for a date-like value (a timestamped string is cut to its
    first 10 characters), else None. Never raises."""
    try:
        s = str(raw)[:10]
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except Exception:
        return None


def earnings_state(bundle) -> dict:
    """The earnings-read state of one loaded bundle (see the module docstring).
    Never raises: any bad input reads as `failed`."""
    try:
        if not isinstance(bundle, dict):
            return dict(_FAILED)
        if bundle.get("asset_type") == asset_type.ASSET_TYPE_ETF:
            return {"state": STATE_NOT_APPLICABLE, "date": None}
        if bundle.get("stale_as_of") is not None:
            return dict(_FAILED)
        raw = bundle.get("earnings")
        if raw:
            d = _parse_date(raw)
            if d is not None:
                return {"state": STATE_DATE, "date": d}
            return dict(_FAILED)
        if bundle.get("earnings_status") == STATE_NO_DATE:
            return {"state": STATE_NO_DATE, "date": None}
        return dict(_FAILED)
    except Exception:
        return dict(_FAILED)


# ── Portfolio-fit state (docs/plans/entry-fail-closed.md §2, chunk C2a) ──────────
FIT_READY = "ready"
FIT_FLAT = "flat"
FIT_UNKNOWN = "unknown"

# What `unpriced_sectors` holds when a no-price holding's sector can't be told: the
# partial book could be hiding ANY sector's weight (Q4 / C5 reads this).
UNPRICED_ALL = "__ALL__"


def _unpriced_sectors(port_df) -> set:
    """Sectors with a holding that dropped out of `port_df` for want of a price
    (`attrs["dropped_holdings"]` rows with reason "no_price_data", the shape
    `portfolio.build_portfolio_df` writes). The sector is the CURATED one
    (`portfolio.resolve_sector(ticker)`, no provider fallback: a name nobody could
    price has no usable provider label); a name the curated map can't place reads
    `UNPRICED_ALL`. A dropped list we cannot read is `{UNPRICED_ALL}` too, never an
    empty set (an unreadable list is not "nothing was dropped")."""
    attrs = getattr(port_df, "attrs", None)
    if not isinstance(attrs, dict) or "dropped_holdings" not in attrs:
        return set()
    dropped = attrs["dropped_holdings"]
    if dropped is None:
        return set()
    if not isinstance(dropped, (list, tuple)):
        return {UNPRICED_ALL}
    out: set = set()
    for row in dropped:
        if not isinstance(row, dict):
            return {UNPRICED_ALL}
        if row.get("reason") != "no_price_data":
            continue      # e.g. invalid_shares_or_cost: a data-entry problem, not a missing price
        tk = str(row.get("ticker") or "").strip()
        if not tk:
            return {UNPRICED_ALL}
        sector = resolve_sector(tk)
        out.add(UNPRICED_ALL if sector == UNCLASSIFIED_SECTOR else sector)
    return out


def fit_state(holdings_rows, port_df) -> dict:
    """{"state": "ready" | "flat" | "unknown", "unpriced_sectors": set | None}.

    flat     the book was read and is empty: a sector weight of 0 is true.
    unknown  holdings could not be read (None / not a list or frame), or holdings
             exist but there is no usable priced frame (None / empty / not a frame):
             the sector-concentration and beta checks could not run.
    ready    holdings exist and a non-empty frame was built; `unpriced_sectors` names
             the sectors of any holding that dropped out for want of a price (empty
             set = none dropped).
    `unpriced_sectors` is None unless the state is "ready". Mirrors
    `portfolio.refresh_outcome` (holdings empty = liquidated, holdings + empty frame
    = outage). Pure and never raises: any bad input reads as unknown."""
    try:
        if holdings_rows is None:
            return {"state": FIT_UNKNOWN, "unpriced_sectors": None}
        if hasattr(holdings_rows, "empty"):
            no_holdings = bool(holdings_rows.empty)
        elif isinstance(holdings_rows, (list, tuple)):
            no_holdings = len(holdings_rows) == 0
        else:
            return {"state": FIT_UNKNOWN, "unpriced_sectors": None}
        if no_holdings:
            return {"state": FIT_FLAT, "unpriced_sectors": None}
        if port_df is None or not hasattr(port_df, "empty") or bool(port_df.empty):
            return {"state": FIT_UNKNOWN, "unpriced_sectors": None}
        return {"state": FIT_READY, "unpriced_sectors": _unpriced_sectors(port_df)}
    except Exception:
        return {"state": FIT_UNKNOWN, "unpriced_sectors": None}


def beta_or_none(v) -> "float | None":
    """A finite float, else None (None / NaN / inf / non-numeric all read as
    "couldn't be read"). A bool is not a beta."""
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def beta_leg_could_bind(port_beta, ticker_beta) -> bool:
    """Could the G-06 beta gate (portfolio beta > PORTFOLIO_BETA_CEILING AND ticker
    beta > TICKER_BETA_CRITICAL, both strict, as in `_portfolio_risk_gate`) have
    fired if its unread leg were read? Plan Q3: hold only when it could.

    False when both legs are known (nothing unread: the gate itself decides), and
    when the known leg alone proves the gate can't fire (ticker beta at or under
    TICKER_BETA_CRITICAL, or portfolio beta at or under PORTFOLIO_BETA_CEILING).
    True when the unread leg could tip it (the known leg is over its line) or both
    are unread. Uses only the two existing constants; never raises."""
    try:
        pb = beta_or_none(port_beta)
        tb = beta_or_none(ticker_beta)
        if pb is not None and tb is not None:
            return False
        if pb is None and tb is None:
            return True
        if pb is None:
            return tb > TICKER_BETA_CRITICAL
        return pb > PORTFOLIO_BETA_CEILING
    except Exception:
        return True
