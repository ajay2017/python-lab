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
app's Watchlist passes one since C2b, `watchlist_fit_source`). `tone_unread` and the banner text builders
arrive in C4. C5 (chunk A) adds `unpriced_holdings` (holdings the priced frame leaves
out, from the holdings rows rather than the frame's attrs) and the additive
`fit_state["unpriced"]`, which `sector_unpriced` turns into the sector hold.

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

Buy-check C3a: a bundle that carries `earnings_resolved` (every `load_bundle` bundle;
`earnings_date.resolve`, the date resolved once in the producer) is read from it instead:
the answer adds `reason` and two more pass states, `reported` (FMP shows a recent report
with an actual EPS) and `reported_unconfirmed` (Yahoo's date is older than
EARNINGS_PAST_DATE_HOLD_DAYS, no next date anywhere). A past Yahoo date within that hold
window reads `failed` (reason `past_unconfirmed` / `past_unchecked`). Buy-check C3b
(this edit) adds the hold itself: `earnings_unknown_hold` (consumed by
`entry_eligibility.check_earnings` and Grow Today) and `earnings_pass_note`.
"""
from __future__ import annotations

import math
from datetime import date as _date, datetime, timedelta

from stock_analyzer import asset_type
from stock_analyzer.constants import (
    EARNINGS_PAST_DATE_HOLD_DAYS, PORTFOLIO_BETA_CEILING, TICKER_BETA_CRITICAL, UNCLASSIFIED_SECTOR,
)
from stock_analyzer.portfolio import resolve_sector

STATE_NOT_APPLICABLE = "not_applicable"
STATE_DATE = "date"
STATE_NO_DATE = "no_date"
STATE_FAILED = "failed"
# Buy-check C3a: the two "it reported and the next date isn't on a calendar" passes,
# read from `earnings_resolved` (`earnings_date.resolve`). Both pass; the second is a
# Yahoo date older than EARNINGS_PAST_DATE_HOLD_DAYS with no future date anywhere.
STATE_REPORTED = "reported"
STATE_REPORTED_UNCONFIRMED = "reported_unconfirmed"

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


def _state_from_resolved(er: dict) -> dict:
    """earnings_state for a bundle that carries `earnings_resolved` (buy-check C3a): the
    producer already resolved the date, so this only maps its state. `reason` is the
    resolver's (None unless failed). An unknown state, or a `date` that doesn't parse,
    reads failed. `earnings_unknown_hold` turns a failed read into a buy hold."""
    st = er.get("state")
    reason = er.get("reason")
    if st == STATE_DATE:
        d = _parse_date(er.get("date"))
        if d is not None:
            return {"state": STATE_DATE, "date": d, "reason": None}
        return {"state": STATE_FAILED, "date": None, "reason": "read_failed"}
    if st in (STATE_NOT_APPLICABLE, STATE_NO_DATE, STATE_REPORTED,
              STATE_REPORTED_UNCONFIRMED):
        return {"state": st, "date": None, "reason": None}
    if st == STATE_FAILED:
        return {"state": STATE_FAILED, "date": None, "reason": reason or "read_failed"}
    return {"state": STATE_FAILED, "date": None, "reason": "read_failed"}


def earnings_state(bundle) -> dict:
    """The earnings-read state of one loaded bundle (see the module docstring).
    Never raises: any bad input reads as `failed`. A bundle carrying `earnings_resolved`
    (every `bundle_loader.load_bundle` bundle since C3a) is read from it and the answer
    also carries `reason`; a bundle without one reads by the C1 rules below."""
    try:
        if not isinstance(bundle, dict):
            return dict(_FAILED)
        if bundle.get("asset_type") == asset_type.ASSET_TYPE_ETF:
            return {"state": STATE_NOT_APPLICABLE, "date": None}
        er = bundle.get("earnings_resolved")
        if isinstance(er, dict):
            return _state_from_resolved(er)
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


# ── The earnings-unconfirmed buy hold (buy-check C3b) ───────────────────────────
CHECK_EARNINGS_UNKNOWN = "earnings_unknown"

_EARN_CLEARS_WHEN = ("the next earnings date can be read (re-checked each time the "
                     "data reloads)")


def _short_date(iso) -> "str | None":
    """'Mon Nov 23' for an ISO date string (no zero-padded day), else None."""
    s = _parse_date(iso)
    if s is None:
        return None
    d = _date.fromisoformat(s)
    return f"{d:%a %b} {d.day}"


def _past_date_clears_when(yahoo_iso) -> str:
    """The clears-when for a held past Yahoo date: confirmed, or the day the app stops
    treating the date on file as a recent report (that date + EARNINGS_PAST_DATE_HOLD_DAYS
    + 1). A name outside FMP's sample is never "confirmed", so the age is the honest
    second exit."""
    s = _parse_date(yahoo_iso)
    if s is None:
        return _EARN_CLEARS_WHEN
    end = _date.fromisoformat(s) + timedelta(days=EARNINGS_PAST_DATE_HOLD_DAYS + 1)
    return (f"the next earnings date is confirmed, or on {end:%a %b} {end.day} "
            f"(more than {EARNINGS_PAST_DATE_HOLD_DAYS} days after the date on file, when the app "
            "stops treating it as a recent report)")


def _earn_hold(reason: str, clears_when: "str | None" = None) -> dict:
    return {"kind": "hold", "check": CHECK_EARNINGS_UNKNOWN, "gate_id": None,
            "reason": reason, "clears_when": clears_when or _EARN_CLEARS_WHEN,
            "gate_value": None, "gate_threshold": None}


def earnings_unknown_hold(bundle, earnings_days) -> "dict | None":
    """The entry hold for a name whose next earnings date couldn't be confirmed, else
    None (buy-check C3b; docs/plans/entry-fail-closed-c3-spec.md "The buy hold").

    Reads `bundle["earnings_resolved"]` (`earnings_date.resolve`, stamped once by
    `bundle_loader.load_bundle`). A bundle without one (a legacy / test bundle, or not a
    dict), an ETF / fund, `no_date` (a fresh listing), `reported` and
    `reported_unconfirmed` all return None (they pass). A `date` state returns None for
    `earnings_days` >= 0 (an imminent date is the existing earnings hold, checked first
    by `check_earnings`; a later one is a real date); `earnings_days` None holds ("the
    earnings date couldn't be placed on the calendar") and a negative one holds ("has
    passed since the data loaded"). A `failed` state holds by its reason.
    The hold is an abstention: gate_id None, no gate-ledger row. Plain text, no `$`, no
    `**`. Never raises (a malformed input reads as no hold only when it carries no
    resolved stamp; a malformed stamp holds)."""
    try:
        if not isinstance(bundle, dict):
            return None
        if bundle.get("asset_type") == asset_type.ASSET_TYPE_ETF:
            return None
        er = bundle.get("earnings_resolved")
        if not isinstance(er, dict):
            return None
        st = er.get("state")
        if st in (STATE_NOT_APPLICABLE, STATE_NO_DATE, STATE_REPORTED,
                  STATE_REPORTED_UNCONFIRMED):
            return None
        if st == STATE_DATE:
            on_file = _short_date(er.get("date"))
            days = earnings_days
            if (isinstance(days, bool) or not isinstance(days, (int, float))
                    or not math.isfinite(days)):
                return _earn_hold("the earnings date couldn't be placed on the calendar")
            if days < 0:
                return _earn_hold(
                    (f"the earnings date on file ({on_file}) has passed since the data loaded"
                     if on_file else
                     "the earnings date on file has passed since the data loaded"))
            return None
        if st == STATE_FAILED:
            reason = er.get("reason")
            on_file = _short_date(er.get("yahoo_date"))
            if reason == "past_unconfirmed" and on_file:
                return _earn_hold(f"the earnings date on file ({on_file}) has passed and "
                                  "the next one couldn't be confirmed",
                                  _past_date_clears_when(er.get("yahoo_date")))
            if reason == "past_unchecked" and on_file:
                return _earn_hold(f"the earnings date on file ({on_file}) has passed and "
                                  "the earnings calendar couldn't be read to confirm the "
                                  "next one",
                                  _past_date_clears_when(er.get("yahoo_date")))
            if reason == "stale_copy":
                return _earn_hold("a saved copy of the data can't show the earnings date")
            return _earn_hold("the next earnings date couldn't be read")
        # An unknown / malformed state: the date was not resolved -> hold.
        return _earn_hold("the next earnings date couldn't be read")
    except Exception:
        return _earn_hold("the next earnings date couldn't be read")


def earnings_pass_note(bundle) -> "str | None":
    """A short plain-text note for an earnings check that PASSED with something worth
    saying (buy-check C3b), else None: `no_date` "no earnings date announced yet";
    `reported` "reported on Wed Nov 19; the next date isn't on the calendar yet";
    `reported_unconfirmed` "...; the next date isn't announced yet"; a `date` whose two
    sources disagree "Yahoo shows Thu Dec 3, the FMP calendar Tue Dec 1; the earlier date
    is used." A plain date, a fund and a bundle without a resolved stamp return None.
    Never raises."""
    try:
        if not isinstance(bundle, dict):
            return None
        if bundle.get("asset_type") == asset_type.ASSET_TYPE_ETF:
            return None
        er = bundle.get("earnings_resolved")
        if not isinstance(er, dict):
            return None
        st = er.get("state")
        if st == STATE_NO_DATE:
            return "no earnings date announced yet"
        if st in (STATE_REPORTED, STATE_REPORTED_UNCONFIRMED):
            when = _short_date(er.get("last_reported"))
            head = f"reported on {when}" if when else "reported recently"
            tail = ("the next date isn't on the calendar yet" if st == STATE_REPORTED
                    else "the next date isn't announced yet")
            return f"{head}; {tail}"
        if st == STATE_DATE:
            dis = er.get("disagree")
            if isinstance(dis, dict):
                y, f = _short_date(dis.get("yahoo")), _short_date(dis.get("fmp"))
                if y and f:
                    return (f"Yahoo shows {y}, the FMP calendar {f}; the earlier date "
                            "is used.")
        return None
    except Exception:
        return None


# ── Portfolio-fit state (docs/plans/entry-fail-closed.md §2, chunk C2a) ──────────
FIT_READY = "ready"
FIT_FLAT = "flat"
FIT_UNKNOWN = "unknown"

# What `unpriced` / `unpriced_sectors` hold when a no-price holding's sector can't be
# told: the partial book could be hiding ANY sector's weight (C5, `sector_unpriced`).
UNPRICED_ALL = "__ALL__"


def _num_or_none(v) -> "float | None":
    """A finite float, else None (None / NaN / inf / bool / non-numeric)."""
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _holding_rows(holdings_rows) -> "list | None":
    """The holdings as a list of rows (dicts or ticker strings), else None."""
    if holdings_rows is None:
        return None
    if hasattr(holdings_rows, "to_dict") and hasattr(holdings_rows, "columns"):
        return holdings_rows.to_dict("records")
    if isinstance(holdings_rows, (list, tuple)):
        return list(holdings_rows)
    return None


def _priced_tickers(port_df) -> "set | None":
    """Upper-cased tickers of the frame rows that carry a usable price: a finite
    `Price` > 0 (a frame with no `Price` column counts every ticker present). None
    when the frame has no `Ticker` column (it cannot be read)."""
    if port_df is None or not hasattr(port_df, "columns") or "Ticker" not in port_df.columns:
        return None
    has_price = "Price" in port_df.columns
    out: set = set()
    for tk, px in zip(port_df["Ticker"].tolist(),
                      port_df["Price"].tolist() if has_price else [None] * len(port_df)):
        t = str(tk).strip().upper() if tk is not None else ""
        if not t or t == "NAN":
            continue
        if not has_price:
            out.add(t)
            continue
        f = _num_or_none(px)
        if f is not None and f > 0:
            out.add(t)
    return out


def unpriced_holdings(holdings_rows, port_df) -> "dict | None":
    """{TICKER: sector | UNPRICED_ALL} for every holding the priced frame leaves out
    (docs/plans/entry-fail-closed-c5-spec.md, chunk A); `{}` = none; None = could not
    be read. A partly priced book reads every sector's weight low, so the sector
    cap (G-05 / G-16) cannot be trusted for the sector that holding sits in.

    A holding is a row with shares > 0, or shares that can't be read (counted: a
    lot we can't size is not a lot we can ignore); shares <= 0 are skipped, as are
    blank tickers. It is unpriced when its ticker has no frame row with a finite
    `Price` > 0 (no `Price` column: no row at all). A holding with shares > 0 but a
    zero / invalid average cost never reaches the frame
    (`portfolio.build_portfolio_df` drops it), so it counts here too (owner QA4).
    A 0.5-share lot with a real price is PRICED. The sector is the CURATED one
    (`portfolio.resolve_sector(ticker)`, no provider fallback: a name nobody could
    price has no usable provider label); a name the curated map can't place (every
    ETF, any uncurated stock) reads `UNPRICED_ALL`. `holdings_rows` may be a list
    of dicts (`Ticker` / `Shares` or `ticker` / `shares`), a DataFrame of them, or a
    list of ticker strings. Pure; inputs are never mutated; never raises."""
    try:
        rows = _holding_rows(holdings_rows)
        if rows is None:
            return None
        priced = _priced_tickers(port_df)
        if priced is None:
            return None
        out: dict = {}
        for row in rows:
            if isinstance(row, str):
                tk, shares = row.strip().upper(), None
            elif isinstance(row, dict):
                raw_tk = row["Ticker"] if "Ticker" in row else row.get("ticker")
                tk = str(raw_tk).strip().upper() if raw_tk is not None else ""
                shares = _num_or_none(row["Shares"] if "Shares" in row else row.get("shares"))
            else:
                return None
            if not tk or tk == "NAN":
                continue
            if shares is not None and shares <= 0:
                continue
            if tk in priced:
                continue
            sector = resolve_sector(tk)
            out[tk] = UNPRICED_ALL if sector == UNCLASSIFIED_SECTOR else sector
        return out
    except Exception:
        return None


def fit_state(holdings_rows, port_df) -> dict:
    """{"state": "ready" | "flat" | "unknown", "unpriced_sectors": set | None,
    "unpriced": {TICKER: sector | UNPRICED_ALL} | None}.

    flat     the book was read and is empty: a sector weight of 0 is true.
    unknown  holdings could not be read (None / not a list or frame), or holdings
             exist but there is no usable priced frame (None / empty / not a frame):
             the sector-concentration and beta checks could not run.
    ready    holdings exist and a non-empty frame was built; `unpriced` maps each
             holding the frame left out for want of a price to its curated sector
             (`unpriced_holdings`; empty = none left out) and `unpriced_sectors` is
             the set of those sectors (`UNPRICED_ALL` among them when a sector
             couldn't be told). Holdings rows that can't be read as rows read
             unknown, never an empty map.
    Both are None unless the state is "ready". Mirrors `portfolio.refresh_outcome`
    (holdings empty = liquidated, holdings + empty frame = outage). Pure and never
    raises: any bad input reads as unknown."""
    _unknown = {"state": FIT_UNKNOWN, "unpriced_sectors": None, "unpriced": None}
    try:
        if holdings_rows is None:
            return dict(_unknown)
        if hasattr(holdings_rows, "empty"):
            no_holdings = bool(holdings_rows.empty)
        elif isinstance(holdings_rows, (list, tuple)):
            no_holdings = len(holdings_rows) == 0
        else:
            return dict(_unknown)
        if no_holdings:
            return {"state": FIT_FLAT, "unpriced_sectors": None, "unpriced": None}
        if port_df is None or not hasattr(port_df, "empty") or bool(port_df.empty):
            return dict(_unknown)
        unpriced = unpriced_holdings(holdings_rows, port_df)
        if unpriced is None:
            return dict(_unknown)
        return {"state": FIT_READY, "unpriced_sectors": set(unpriced.values()),
                "unpriced": unpriced}
    except Exception:
        return dict(_unknown)


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
