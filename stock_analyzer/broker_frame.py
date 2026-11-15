"""
Net capital checked against Robinhood's own positions (bug register U7; plan
`docs/plans/net-capital-broker-frame.md`, owner U1-U10 accepted 2026-11-14).

Pure functions only: no Streamlit, no DB, no clock reads (callers pass `now`).
Deliberately does NOT import `stock_analyzer.margin` (callers pass
`resolve_net_capital`'s output; tests/test_broker_frame.py pins this): margin.py's
awareness-only import fence (tests/test_margin.py `_GATE_MODULES`) stays intact.

The defect: `margin.resolve_net_capital` is journal gross book + `account_cash`,
true only when both describe the same moment. A sell filled before a broker
sync but not yet logged leaves the shares in the gross book while the proceeds
are already in cash, so net capital reads too HIGH and the F-255 cap and the
F-306 daily budget allow too much. The identity this module uses:

    broker frame = sum(broker shares x price) + broker cash
                 = journal gross + cash - adjust
    adjust       = sum over priced tickers of (journal shares - broker shares) x price

It is exact when the books agree and needs no lag correction (a fill after the
sync moves shares and cash equally).

TIGHTENING-ONLY by construction: every net / basis returned is <= the one passed
in. A broker figure that reads HIGHER than the journal's (a BUY logged after the
sync, an unlogged BUY) is never used to loosen anything.

Not wired into any decision yet (chunks C3 / C4 of the plan).
"""
from __future__ import annotations

import math

import pandas as pd

from stock_analyzer.constants import BROKER_DRIFT_SHARE_TOL
from stock_analyzer.daily_budget import CALLED_REASON

# Frame states. Only "ok" carries an `adjust`; every other state means "not
# checked" and `adjust` is None (never 0.0, which would read as "books agree").
STATE_OK = "ok"
STATE_NO_SNAPSHOT = "no_snapshot"   # no broker_position_snapshot row / DB offline
STATE_UNREADABLE = "unreadable"     # a snapshot or cash timestamp / payload is malformed
STATE_NO_CASH = "no_cash"           # no account-cash record (or no cash figure)
STATE_NO_HOLDINGS = "no_holdings"   # the journal holdings couldn't be read
STATE_UNPAIRED = "unpaired"         # cash not from the same broker sync as the positions
STATE_IMPLAUSIBLE = "implausible"   # empty snapshot while the journal holds positions

# The note `broker_sync.map_balances_to_cash` writes on a broker-synced cash row.
# A hand-entered cash balance carries a different (or no) note. Pinned equal to
# the real mapper's output in tests/test_broker_frame.py.
BROKER_CASH_NOTE = "Synced via SnapTrade (Robinhood)"

# Plain-words reason an unchecked frame gives in the disclosure suffix.
_WHY = {
    STATE_NO_SNAPSHOT: "no Robinhood positions snapshot yet",
    STATE_UNREADABLE: "the Robinhood positions snapshot or cash record was unreadable",
    STATE_NO_CASH: "no cash record",
    STATE_NO_HOLDINGS: "your holdings couldn't be read",
    STATE_UNPAIRED: "Robinhood's positions and cash weren't read together",
    STATE_IMPLAUSIBLE: "Robinhood's snapshot showed no positions while your journal holds some",
}

# net / basis states a disclosure applies to (a status line exists for them).
_DISCLOSED_STATES = ("ok", "levered", "called")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ts(x):
    """tz-aware UTC Timestamp, or None when missing / unparseable. A naive string
    is read as UTC (every stamp the app writes is tz-aware)."""
    if x is None or (isinstance(x, str) and not x.strip()):
        return None
    try:
        t = pd.to_datetime(x, utc=True)
    except Exception:
        return None
    return None if pd.isna(t) else t


def _norm(t) -> str:
    """Ticker key, the same normalisation broker_sync.diff_position_map uses
    (strip + upper). Symbol aliases (BRK.B vs BRK-B) are deliberately NOT merged:
    a standing alias must show up as a difference, not be silently hidden."""
    if t is None:
        return ""
    try:
        if pd.isna(t):
            return ""
    except (TypeError, ValueError):
        pass
    return str(t).strip().upper()


def _journal_shares(holdings_df) -> "dict | None":
    """{TICKER: shares} summed over the journal's holdings; None = unreadable.
    An empty frame is a real answer ({}: the journal holds nothing)."""
    if holdings_df is None or not hasattr(holdings_df, "iterrows"):
        return None
    try:
        if len(holdings_df) == 0:
            return {}
        cols = set(holdings_df.columns)
        if "Ticker" not in cols or "Shares" not in cols:
            return None
        out: dict = {}
        for _, row in holdings_df.iterrows():
            tk = _norm(row.get("Ticker"))
            if not tk:
                continue
            sh = _num(row.get("Shares"))
            if sh is None:
                return None
            out[tk] = out.get(tk, 0.0) + sh
        return out
    except Exception:
        return None


def _broker_shares(positions) -> "dict | None":
    if not hasattr(positions, "items"):
        return None
    out: dict = {}
    try:
        for k, v in positions.items():
            tk = _norm(k)
            if not tk:
                continue
            sh = _num(v)
            if sh is None:
                return None
            out[tk] = out.get(tk, 0.0) + sh
    except Exception:
        return None
    return out


def _prices(price_map) -> dict:
    """{TICKER: price} keeping only finite positive prices."""
    out: dict = {}
    if not hasattr(price_map, "items"):
        return out
    for k, v in price_map.items():
        tk, px = _norm(k), _num(v)
        if tk and px is not None and px > 0:
            out[tk] = px
    return out


def _result(state, adjust=None, rows=None, unpriced=None, captured_at=None,
            age_hours=None) -> dict:
    return {"state": state, "adjust": adjust, "rows": list(rows or []),
            "unpriced": list(unpriced or []), "captured_at": captured_at,
            "age_hours": age_hours}


def broker_frame(snapshot, cash_rec, holdings_df, price_map, now, *,
                 pair_max_minutes) -> dict:
    """Compare Robinhood's own positions to the journal's.

    snapshot : `db.load_broker_position_snapshot()` (positions map, captured_at),
        or None.
    cash_rec : `db.load_account_cash()` ({cash_balance, note, updated_at}), or None.
    holdings_df : the journal's holdings (Ticker / Shares columns), or None.
    price_map : {TICKER: live price}.
    now : tz-aware "now" from the caller (used only for `age_hours`).
    pair_max_minutes : pass `BROKER_FRAME_PAIR_MAX_MINUTES`. The snapshot and the
        cash record are one broker sync only when their stamps are within this
        many minutes of each other, either direction, inclusive.

    Returns {state, adjust, rows, unpriced, captured_at, age_hours}.
    `state` "ok" carries `adjust` (signed dollars: positive = the journal holds
    more than Robinhood, so net capital is overstated). Every other state
    ("no_snapshot", "unreadable", "no_cash", "no_holdings", "unpaired",
    "implausible") has `adjust` None. `rows` lists each ticker the books differ
    on beyond BROKER_DRIFT_SHARE_TOL (priced ones only); `unpriced` names the
    differing tickers with no usable price (an unlogged BUY, or an alias symbol):
    they are left out of `adjust`. `captured_at` is a UTC Timestamp (None when
    unknown); `age_hours` is now - captured_at, None when either is unknown.
    """
    if not isinstance(snapshot, dict):
        return _result(STATE_NO_SNAPSHOT)
    cap = _ts(snapshot.get("captured_at"))
    broker = _broker_shares(snapshot.get("positions"))
    now_t = _ts(now)
    age = ((now_t - cap).total_seconds() / 3600.0
           if cap is not None and now_t is not None else None)

    def _res(state, **kw):
        return _result(state, captured_at=cap, age_hours=age, **kw)

    if cap is None or broker is None:
        return _res(STATE_UNREADABLE)

    if not isinstance(cash_rec, dict) or _num(cash_rec.get("cash_balance")) is None \
            or cash_rec.get("updated_at") in (None, ""):
        return _res(STATE_NO_CASH)
    upd = _ts(cash_rec.get("updated_at"))
    if upd is None:
        return _res(STATE_UNREADABLE)

    journal = _journal_shares(holdings_df)
    if journal is None:
        return _res(STATE_NO_HOLDINGS)

    # Pairing: the same broker sync wrote both. A hand-entered cash balance has a
    # different note; a hand edit that kept the synced note still moves
    # `updated_at`, so the window catches it unless it was saved within minutes
    # of the sync.
    pm = _num(pair_max_minutes)
    if str(cash_rec.get("note") or "").strip() != BROKER_CASH_NOTE \
            or pm is None or pm < 0 \
            or abs((cap - upd).total_seconds()) > pm * 60.0:
        return _res(STATE_UNPAIRED)

    # An empty snapshot while the journal holds positions is far likelier an
    # unread / failed capture than a real all-cash account: never act on it.
    if not broker and any(v > BROKER_DRIFT_SHARE_TOL for v in journal.values()):
        return _res(STATE_IMPLAUSIBLE)

    px = _prices(price_map)
    rows: list = []
    unpriced: list = []
    adjust = 0.0
    for tk in sorted(set(journal) | set(broker)):
        js, bs = journal.get(tk, 0.0), broker.get(tk, 0.0)
        diff = js - bs
        if abs(diff) <= BROKER_DRIFT_SHARE_TOL:
            continue
        price = px.get(tk)
        if price is None:
            unpriced.append(tk)
            continue
        dollars = diff * price
        adjust += dollars
        rows.append({"ticker": tk, "journal_shares": js, "broker_shares": bs,
                     "diff": diff, "price": price, "dollars": dollars})
    return _res(STATE_OK, adjust=adjust, rows=rows, unpriced=unpriced)


def _frame_adjust(frame) -> "float | None":
    """The frame's `adjust` when it is a usable "ok" frame, else None."""
    if not isinstance(frame, dict) or frame.get("state") != STATE_OK:
        return None
    return _num(frame.get("adjust"))


def _frame_state(frame):
    return frame.get("state") if isinstance(frame, dict) else None


def reconcile_net_capital(net, state, frame) -> dict:
    """The F-255 net capital, tightened to Robinhood's positions when they say less.

    net, state : `margin.resolve_net_capital`'s two return values.
    frame : `broker_frame(...)`'s result (or None).

    levered / called: net_eff = net - max(0, adjust); "called" when net_eff <= 0;
    source "broker" only when that actually lowered the figure. unlevered /
    stale / unknown (or a frame that isn't "ok"): `net` and `state` unchanged.
    Returns {net, state, source: "journal"|"broker", gap, checked, journal_net,
    frame_state}: `gap` = dollars the broker figure is below the journal's (0.0
    when it didn't bind, None when unchecked); `checked` = the frame was usable
    for a state it applies to; `journal_net` = `net` as passed.
    """
    adj = _frame_adjust(frame)
    n = _num(net)
    out = {"net": net, "state": state, "source": "journal", "gap": None,
           "checked": False, "journal_net": net, "frame_state": _frame_state(frame)}
    if state in ("levered", "called") and n is not None:
        if adj is None:
            return out
        gap = max(0.0, adj)
        net_eff = n - gap
        out.update(net=net_eff, checked=True, gap=gap)
        if net_eff < n:
            out["source"] = "broker"
        if net_eff <= 0:
            out["state"] = "called"
        return out
    if state == "unlevered" and adj is not None:
        out.update(checked=True, gap=0.0)     # nothing to net: no cap applies
    return out


def reconcile_budget_basis(basis_result, gross_book, cash_balance, frame) -> dict:
    """The F-306 budget basis, tightened to Robinhood's positions when they say less.

    basis_result : `daily_budget.budget_basis(...)`'s dict (state, basis, reason,
        lag_adjustment, lag_uncertain, lag_uncertain_tickers). Every key is kept
        as is, except `basis` / `state` / `reason` when the broker figure binds.
    gross_book, cash_balance : the same two figures `budget_basis` was given.
    frame : `broker_frame(...)`'s result (or None).

    basis_eff = min(lag-corrected journal basis, gross + cash - adjust): NOT
    "lagged basis - adjust", which would subtract a post-sync logged BUY twice.
    "called" when basis_eff <= 0. Only state "ok" is tightened; "unknown" and
    "called" pass through. Adds {source: "journal"|"broker", gap, checked,
    journal_net, frame_state}: `gap` = the dollars the basis was lowered by (0.0
    when it didn't bind, None when unchecked), `journal_net` = the basis as passed.
    """
    res = dict(basis_result) if isinstance(basis_result, dict) else {}
    basis = _num(res.get("basis"))
    adj = _frame_adjust(frame)
    gb, cash = _num(gross_book), _num(cash_balance)
    res.update({"source": "journal", "gap": None, "checked": False,
                "journal_net": res.get("basis"), "frame_state": _frame_state(frame)})
    if res.get("state") not in ("ok", "called") or adj is None or gb is None \
            or cash is None:
        return res
    res.update(checked=True, gap=0.0)
    if res.get("state") != "ok" or basis is None:
        return res
    frame_basis = gb + cash - adj
    if frame_basis < basis:
        res.update(basis=frame_basis, source="broker", gap=basis - frame_basis)
        if frame_basis <= 0:
            res.update(state="called", reason=CALLED_REASON)
    return res


def _usd(x) -> str:
    v = _num(x)
    return f"${v:,.0f}" if v is not None else "$0"


def disclosure_text(rec, note_pct) -> str:
    """Suffix for the budget status line, or "".

    rec : `reconcile_net_capital`'s or `reconcile_budget_basis`'s result.
    note_pct : pass `NET_CAPITAL_BROKER_GAP_NOTE_PCT` (percent of the journal's
        figure; inclusive). It only decides whether the note SHOWS; the lower
        figure always applies.

    - checked and the broker figure is lower by >= note_pct of the journal's:
      " · net capital from Robinhood's positions; your journal shows ~$X more
      (see the broker check on Home)".
    - applicable but not checked: " · sizes not cross-checked against Robinhood's
      positions (<why>)".
    - anything else (not a status-line state, or checked and under the note
      threshold): "".
    Dollar amounts are written as plain "$1,234": the callers mask them with the
    same `\\$[\\d,]+` pattern budget_view uses and escape `$` for markdown.
    """
    if not isinstance(rec, dict) or rec.get("state") not in _DISCLOSED_STATES:
        return ""
    if not rec.get("checked"):
        why = _WHY.get(rec.get("frame_state"),
                       "no Robinhood check was available" if rec.get("frame_state") is None
                       else "its inputs were unavailable")
        return f" · sizes not cross-checked against Robinhood's positions ({why})"
    gap, jnet, pct = _num(rec.get("gap")), _num(rec.get("journal_net")), _num(note_pct)
    if gap is None or gap <= 0 or pct is None or jnet is None:
        return ""
    if jnet > 0 and gap / jnet * 100.0 < pct:
        return ""
    return (f" · net capital from Robinhood's positions; your journal shows "
            f"~{_usd(gap)} more (see the broker check on Home)")
