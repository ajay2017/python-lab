"""Gate module: where the 📋 Watchlist's portfolio-fit frame comes from, and what the
page tells the user about it (docs/plans/entry-fail-closed.md §2 "Watchlist
self-build (U3)", chunk C2b; spec docs/plans/entry-fail-closed-c2b-spec.md).

C2a taught `watchlist_advisor._portfolio_risk_gate` to HOLD a name when the sector /
beta fit check couldn't run, but only when the caller passes a `fit_state`. The cron
does; the app's Watchlist used to read Home's published frame (weight 0.0 when Home
hadn't built one: the G-05 check failed OPEN). C2b makes the page build its own frame
from the current holdings when Home's isn't trustworthy, or hold the name.

Pure: no streamlit, no db, no clock (the caller passes the "built at" time string);
inputs are never mutated; nothing here raises.

  decide_source(...) -> "home" | "self_build" | "flat"
  banner(...)        -> (tier, markdown) | None   (warning when a check is weakened)
  unpriced_tickers(frame) -> sorted list | None   (holdings the self-built frame left out)
  beta_note(...)     -> markdown | None

THE SOURCE RULE. An empty book is "flat" (a weight of 0 is true; nothing to build).
Holdings we couldn't read (None) never trust Home's frame: "self_build", which then
fails to build and reads unknown, so the name is held. Otherwise Home's frame is used
only when ALL of: its coordination cache is ready, the frame is non-empty, the
portfolio-risk cache is ready AND fresh at the current coordination epoch (weights
and beta must come from the same book: "one freshness per number"; a trade logged
off Home republishes the frame and bumps the epoch but leaves the risk cache on the
pre-trade book, so a stale risk cache rebuilds both rather than mixing post-trade
weights with a pre-trade beta), and the holdings signature hasn't changed since
Home built the frame. Anything else self-builds.
"""
from __future__ import annotations

import re

from stock_analyzer import buy_check_state

SOURCE_HOME = "home"
SOURCE_SELF = "self_build"
SOURCE_FLAT = "flat"

_STATE_READY = "ready"   # the app's `_coord_cache_state` "ready" value

_FIT_UNKNOWN_TEXT = (
    "⚠️ **Enter Now is held on this page today:** your holdings couldn't be loaded "
    "or priced here, so how each name would fit your book (sector concentration and "
    "portfolio beta) couldn't be checked. That's a reason to pause, not a green "
    "light. It re-runs the next time this page loads."
)

_BETA_NOTE_TEXT = (
    "⚠️ **Portfolio beta couldn't be computed** — a name whose own beta is above "
    "the limit or couldn't be read is held (the portfolio-beta check couldn't run "
    "for it); other names are unaffected."
)


def decide_source(*, holdings_empty, home_frame_state, home_frame_empty,
                  home_risk_state, home_risk_fresh, home_stale) -> str:
    """Which frame the Watchlist's sector / beta checks read (see the module
    docstring). `holdings_empty`: True = the book was read and is empty, False = it
    has rows, None = it couldn't be read. `home_risk_fresh`: True only when the
    risk cache is stamped at the current epoch (REQUIRED; stale / absent = False).
    Never raises: any odd input self-builds
    (the safe side: the build fails closed, the name is held)."""
    try:
        if holdings_empty is True:
            return SOURCE_FLAT
        if holdings_empty is None:
            return SOURCE_SELF
        if (home_frame_state == _STATE_READY
                and home_frame_empty is False
                and home_risk_state == _STATE_READY
                and home_risk_fresh is True
                and home_stale is False):
            return SOURCE_HOME
        return SOURCE_SELF
    except Exception:
        return SOURCE_SELF


def _fit_name(fit_state):
    return fit_state.get("state") if isinstance(fit_state, dict) else None


_TICKER_OK = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")


def unpriced_tickers(port_df):
    """Tickers that dropped out of a self-built frame for want of a price
    (`attrs["dropped_holdings"]` rows with reason "no_price_data", the shape
    `portfolio.build_portfolio_df` writes), sorted and de-duplicated. `[]` = none
    dropped (an absent or None list, as for a fully priced book); None = the list
    couldn't be read (not a list, a non-dict row, or a ticker that isn't a plain
    symbol), which the banner says rather than reading as "all priced". Rows with
    another reason (invalid_shares_or_cost: a data-entry problem) are skipped.
    Never raises."""
    try:
        attrs = getattr(port_df, "attrs", None)
        if not isinstance(attrs, dict) or "dropped_holdings" not in attrs:
            return []
        dropped = attrs["dropped_holdings"]
        if dropped is None:
            return []
        if not isinstance(dropped, (list, tuple)):
            return None
        out = set()
        for row in dropped:
            if not isinstance(row, dict):
                return None
            if row.get("reason") != "no_price_data":
                continue
            tk = str(row.get("ticker") or "").strip().upper()
            if not _TICKER_OK.match(tk):
                return None
            out.add(tk)
        return sorted(out)
    except Exception:
        return None


def banner(*, source, fit_state, built_at_et, n_holdings, n_priced, unpriced):
    """(tier, markdown) for the one page banner, or None. Tier is "warning" or
    "info". The fit state decides first: anything but ready / flat reads "held".

    A self-built frame whose every holding was priced is an info line; one that left
    holdings out (`unpriced` non-empty, or unreadable = None) is a warning, because a
    sector's weight reads low and the G-05 check is weakened for it. `n_holdings` =
    every holding row, `n_priced` = rows in the built frame, `unpriced` = tickers
    from `unpriced_tickers`."""
    try:
        name = _fit_name(fit_state)
        if name not in (buy_check_state.FIT_READY, buy_check_state.FIT_FLAT):
            return ("warning", _FIT_UNKNOWN_TEXT)
        if source != SOURCE_SELF or name != buy_check_state.FIT_READY:
            return None
        when = f" at {built_at_et}" if built_at_et else ""
        known_n = (isinstance(n_holdings, int) and not isinstance(n_holdings, bool))
        known_p = (isinstance(n_priced, int) and not isinstance(n_priced, bool))
        tail = ("at the last loaded prices (refreshed every 30 minutes), not from "
                "🏠 Home's live prices.")
        head = "Sector weights and portfolio beta for the checks below were built on this page"
        if isinstance(unpriced, (list, tuple)) and len(unpriced) == 0:
            if known_n:
                what = "1 holding" if n_holdings == 1 else f"{n_holdings} holdings"
                what = f"your {what}"
            else:
                what = "your holdings"
            return ("info", f"ℹ️ {head}{when} from {what} {tail}")
        if not isinstance(unpriced, (list, tuple)):
            return ("warning", (
                f"⚠️ {head}{when} {tail} Whether every holding was priced couldn't "
                "be read, so a sector's weight may read low until a later load."
            ))
        names = ", ".join(str(t) for t in unpriced)
        if known_n and known_p:
            of = f"{n_priced} of your {n_holdings} holdings"
        else:
            of = "the holdings that could be priced"
        if len(unpriced) == 1:
            tail2 = (f"{names} couldn't be priced, so it isn't in the sector weights "
                     "and its sector reads low until a later load.")
        else:
            tail2 = (f"{names} couldn't be priced, so they aren't in the sector weights "
                     "and their sectors read low until a later load.")
        return ("warning", f"⚠️ {head}{when} from {of} {tail} {tail2}")
    except Exception:
        return ("warning", _FIT_UNKNOWN_TEXT)


def beta_note(*, fit_state, portfolio_beta):
    """The warning for a fit that built but whose portfolio beta couldn't be
    computed (the beta check then holds only names whose own beta could bind it),
    else None. Reflects the beta actually used (Home's or the self-built one)."""
    try:
        if (_fit_name(fit_state) == buy_check_state.FIT_READY
                and buy_check_state.beta_or_none(portfolio_beta) is None):
            return _BETA_NOTE_TEXT
        return None
    except Exception:
        return None
