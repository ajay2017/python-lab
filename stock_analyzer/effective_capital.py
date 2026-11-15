"""
The ONE builder of the sizing capital inputs: F-255's net capital and the F-306
budget basis, each the LOWER of the journal figure and Robinhood's own positions
(U7 C3 / C4, `docs/plans/net-capital-broker-frame-c3-spec.md`).

Why a module of its own: the budget must be fed the RAW journal net capital and
state (`margin.resolve_net_capital`), never F-255's reconciled net. A BUY logged
after the broker sync is already subtracted by `daily_budget.budget_basis`'s lag
correction; feeding it the reconciled net would subtract it twice (G + C - 2K).
`resolve` builds the budget inputs from the raw pair and exposes the reconciled
pair only as `["net"]` / `["state"]`, so no call site can hand a net figure to
budget code. `budget_view` (app / Home / Watchlist / Analysis) and the cron's
`headless_alert_engine` both go through here, so the app and the emails size
from the same number.

Pure: no Streamlit, no DB, no clock reads (callers pass `now`). Deliberately does
NOT import `stock_analyzer.margin` (callers pass `resolve_net_capital`'s output;
margin's awareness-only import fence stays intact) and does NOT import
`budget_view` (which imports this module). Decision-bearing: a `_GATE_FILES`
member, and a `tests/test_margin.py` `_GATE_MODULES` member.

TIGHTENING-ONLY: every net / basis returned is <= the journal figure passed in
(`broker_frame`'s invariant); a missing, unpaired, implausible or unpriced
snapshot leaves the journal figure untouched and says so.
"""
from __future__ import annotations

import math

import numpy as np

from stock_analyzer import broker_frame, daily_budget, sync_timing
from stock_analyzer.constants import BROKER_FRAME_PAIR_MAX_MINUTES
from stock_analyzer.daily_pnl import _is_split


def budget_inputs(gross_book, net_capital, basis_state, acct_rec, frame=None) -> dict:
    """The dict `daily_budget.budget_basis` is fed from. `acct_rec` None /
    non-dict -> no cash record (-> basis unknown); a failed resolution passes
    basis_state "unknown". `net_capital` / `basis_state` MUST be the raw journal
    pair (see the module docstring). `frame` is `broker_frame.broker_frame`'s
    result (None = not computed): `reconciled_basis` tightens the basis with it."""
    ok = isinstance(acct_rec, dict)
    return {
        "gross_book":   gross_book,
        "net_capital":  net_capital,
        "basis_state":  basis_state or "unknown",
        "cash_balance": acct_rec.get("cash_balance") if ok else None,
        "cash_as_of":   acct_rec.get("updated_at") if ok else None,
        "broker_frame": frame,
    }


def reconciled_basis(inputs, trades_df, day, now) -> dict:
    """`daily_budget.budget_basis` fed the raw journal pair, then tightened to
    Robinhood's positions. The ONLY budget-basis entry point for `budget_view`
    (its three sites) and the cron's `_run_daily_budget`. Returns the
    `budget_basis` dict plus {source, gap, checked, journal_net, frame_state,
    frame_adjust} (`broker_frame.reconcile_budget_basis`)."""
    bi = inputs if isinstance(inputs, dict) else {}
    basis = daily_budget.budget_basis(
        bi.get("gross_book"), bi.get("net_capital"),
        bi.get("basis_state") or "unknown", bi.get("cash_balance"),
        bi.get("cash_as_of"), trades_df, day, now)
    return broker_frame.reconcile_budget_basis(
        basis, bi.get("gross_book"), bi.get("cash_balance"), bi.get("broker_frame"))


def split_tickers_since(trades_df, captured_at) -> frozenset:
    """Upper-cased tickers with a SPLIT row logged AFTER, or at an UNKNOWN time
    relative to, the snapshot at `captured_at`. Their journal share count and the
    snapshot's sit on different sides of the split, so `broker_frame` leaves them
    out of its adjust. Rows are looked up by POSITION (`sync_timing.row_codes`),
    as `broker_sync.drift_excuses` does, so duplicate index labels can't pull in
    a split from before the sync. frozenset() on None or any failure."""
    try:
        if trades_df is None or captured_at is None:
            return frozenset()
        if "ticker" not in trades_df.columns or "action" not in trades_df.columns:
            return frozenset()
        codes = sync_timing.row_codes(trades_df, captured_at)
        if codes is None or not len(codes):
            return frozenset()
        pos = np.flatnonzero(np.isin(codes, (sync_timing.AFTER, sync_timing.UNKNOWN)))
        sub = trades_df.iloc[pos]
        out = set()
        for tk, act in zip(sub["ticker"], sub["action"]):
            tk = str(tk).strip().upper()
            if tk and tk != "NAN" and _is_split(act):
                out.add(tk)
        return frozenset(out)
    except Exception:
        return frozenset()


def resolve(gross_book, cash_rec, journal_net, journal_state, snapshot,
            holdings_df, price_map, now, *, trades_df=None) -> dict:
    """Everything a sizing surface needs, in one never-raising call.

    gross_book : the portfolio value the journal's net capital was built from.
    cash_rec : `db.load_account_cash()`; journal_net / journal_state :
        `margin.resolve_net_capital`'s two values (the RAW journal pair).
    snapshot : `db.load_broker_position_snapshot()`; holdings_df / price_map :
        the journal's holdings and the prices the gross book was valued at.

    Returns {net, state, rec, frame, acct, cash_as_of, captured_at,
    budget_inputs}: `net` / `state` are F-255's (reconciled, tightening-only);
    `rec` is `broker_frame.reconcile_net_capital`'s result; `budget_inputs` is
    built from the RAW journal pair + the frame (`reconciled_basis` does the
    rest). `cash_as_of` / `captured_at` are the two freshness stamps the Home
    memo signature carries. A failure anywhere reads as "no frame" / "journal
    figure unchanged"."""
    cash = cash_rec if isinstance(cash_rec, dict) else None
    snap = snapshot if isinstance(snapshot, dict) else None
    captured_at = (snap.get("captured_at") or None) if snap else None
    try:
        frame = broker_frame.broker_frame(
            snap, cash, holdings_df, price_map, now,
            pair_max_minutes=BROKER_FRAME_PAIR_MAX_MINUTES,
            exclude_tickers=split_tickers_since(trades_df, captured_at))
    except Exception:
        frame = None
    try:
        rec = broker_frame.reconcile_net_capital(journal_net, journal_state, frame)
    except Exception:
        rec = {"net": journal_net, "state": journal_state, "source": "journal",
               "gap": None, "checked": False, "journal_net": journal_net,
               "frame_state": frame.get("state") if isinstance(frame, dict) else None}
    return {
        "net": rec["net"],
        "state": rec["state"],
        "rec": rec,
        "frame": frame,
        "acct": cash_rec,
        "cash_as_of": (cash.get("updated_at") or None) if cash else None,
        "captured_at": captured_at,
        "budget_inputs": budget_inputs(gross_book, journal_net, journal_state, cash,
                                       frame=frame),
    }


def broker_fields(basis) -> dict:
    """The three fields the budget cache / dict / plan summary carry, from a
    `reconciled_basis` result. Primitives only (the budget dict is persisted and
    emailed): no Timestamp, no frame."""
    b = basis if isinstance(basis, dict) else {}
    return {
        "basis_source": b.get("source") or "journal",
        "broker_gap": b.get("gap"),
        "broker_rec": {k: b.get(k) for k in (
            "state", "source", "gap", "checked", "journal_net", "frame_state",
            "frame_adjust")},
    }


def _fmt_gap(g) -> str:
    try:
        v = float(g)
    except (TypeError, ValueError):
        return "n/a"
    return f"{v:.2f}" if math.isfinite(v) else "n/a"


def log_line(eff) -> str:
    """One cron-log line for a `resolve` result, e.g.
    `net capital source=journal gap=0.00 frame=ok state=levered`."""
    e = eff if isinstance(eff, dict) else {}
    rec = e.get("rec")
    if not isinstance(rec, dict):
        rec = {}
    frame = e.get("frame")
    fstate = frame.get("state") if isinstance(frame, dict) else None
    return (f"net capital source={rec.get('source') or 'journal'} "
            f"gap={_fmt_gap(rec.get('gap'))} frame={fstate or 'none'} "
            f"state={e.get('state')}")


def budget_log_suffix(budget) -> str:
    """Suffix for the cron's budget log lines:
    ` · basis_source=journal broker_gap=0.00` (n/a when the budget has none)."""
    b = budget if isinstance(budget, dict) else {}
    return (f" · basis_source={b.get('basis_source') or 'n/a'} "
            f"broker_gap={_fmt_gap(b.get('broker_gap'))}")
