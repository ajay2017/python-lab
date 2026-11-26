"""
The one G-06 portfolio-beta predicate and its couldn't-check decision.

THE SHAPE
    G-06 blocks a new buy when the portfolio's beta is over
    `PORTFOLIO_BETA_CEILING` AND the candidate's own beta is over
    `TICKER_BETA_CRITICAL` (both strict). `breached` is that comparison and
    nothing else; `decide` wraps it with the "a leg or the book couldn't be
    read" outcome so every surface that applies G-06 reads one answer.
    Pure: no clock, no I/O, never raises. Callers: the 📋 Watchlist's
    `watchlist_advisor._portfolio_risk_gate` (`breached`, C1); `decide` is the
    shared decision for 🏠 Grow Today and the cron emails from C2.

THE FAIL DIRECTIONS
    - An unread beta leg (None / NaN / inf / bool / non-numeric, see
      `buy_check_state.beta_or_none`) is never itself a breach.
    - `decide`: a book that couldn't be loaded or priced (fit_state not ready
      or flat, or unreadable) is UNKNOWN (`fit_unknown`): hold, never pass.
    - `decide`: an unread leg that could have tipped the gate (the read leg is
      already over its line, or both are unread) is UNKNOWN (`beta_unknown`).
    - `decide`: an unread leg that could not have tipped it (the read leg is
      at or under its line) passes: the gate provably could not have fired.
    - Any exception inside `decide` fails closed to UNKNOWN (`fit_unknown`).
"""

from stock_analyzer.buy_check_state import (
    FIT_FLAT,
    FIT_READY,
    beta_leg_could_bind,
    beta_or_none,
)
from stock_analyzer.constants import PORTFOLIO_BETA_CEILING, TICKER_BETA_CRITICAL

STATE_PASS, STATE_BLOCKED, STATE_UNKNOWN = "pass", "blocked", "unknown"


def breached(port_beta, ticker_beta) -> bool:
    """Both legs readable (`beta_or_none`) AND port_beta > PORTFOLIO_BETA_CEILING
    AND ticker_beta > TICKER_BETA_CRITICAL (both strict). An unread leg is never
    a breach."""
    try:
        pb = beta_or_none(port_beta)
        tb = beta_or_none(ticker_beta)
        if pb is None or tb is None:
            return False
        return pb > PORTFOLIO_BETA_CEILING and tb > TICKER_BETA_CRITICAL
    except Exception:
        return False


def _result(state, kind, pb, tb) -> dict:
    blocked = state == STATE_BLOCKED
    return {
        "state": state,
        "kind": kind,
        "port_beta": pb,
        "ticker_beta": tb,
        "gate_value": tb if blocked else None,
        "gate_threshold": TICKER_BETA_CRITICAL if blocked else None,
    }


def decide(*, port_beta, ticker_beta, fit_state) -> dict:
    """{"state": pass|blocked|unknown, "kind": None|"beta"|"beta_unknown"|"fit_unknown",
        "port_beta": float|None, "ticker_beta": float|None,
        "gate_value": ticker_beta if blocked else None,
        "gate_threshold": TICKER_BETA_CRITICAL if blocked else None}

    Order, the same as `watchlist_advisor._portfolio_risk_gate`: (1) a readable
    breach -> blocked / beta (a real breach binds first and is still ledgered, even
    when the fit check is unknown); (2) fit_state unreadable or not ready/flat ->
    unknown / fit_unknown; (3) flat book -> pass; (4) both legs read -> pass;
    (5) an unread leg that could bind -> unknown / beta_unknown, else pass.
    Never raises."""
    try:
        pb = beta_or_none(port_beta)
        tb = beta_or_none(ticker_beta)
        if breached(port_beta, ticker_beta):
            return _result(STATE_BLOCKED, "beta", pb, tb)
        fs_state = fit_state.get("state") if isinstance(fit_state, dict) else None
        if fs_state not in (FIT_READY, FIT_FLAT):
            return _result(STATE_UNKNOWN, "fit_unknown", pb, tb)
        if fs_state == FIT_FLAT:
            return _result(STATE_PASS, None, pb, tb)
        if pb is not None and tb is not None:
            return _result(STATE_PASS, None, pb, tb)
        if beta_leg_could_bind(port_beta, ticker_beta):
            return _result(STATE_UNKNOWN, "beta_unknown", pb, tb)
        return _result(STATE_PASS, None, pb, tb)
    except Exception:
        return _result(STATE_UNKNOWN, "fit_unknown", None, None)
