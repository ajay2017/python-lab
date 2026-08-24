"""
stock_analyzer/broker_sync.py — SnapTrade broker-sync transform/decision logic (pure)

No Streamlit, DB, or network imports — takes already-fetched SnapTrade payloads
(from `stock_analyzer/snaptrade_client.py`) and the app's own DataFrames, and
returns plain dicts/lists for the caller (the `broker` cron lane / app.py) to
persist or render. See docs/plans/snaptrade-broker-integration.md for the
full design and the reasoning behind each split below.

Three pure functions:
    diff_positions          — 3-bucket drift vs the live portfolio (never gates,
                               never writes; awareness only)
    map_balances_to_cash    — SnapTrade balance payload -> account_cash shape
    classify_transactions   — SnapTrade activity payload -> pending-import /
                               income-event / cash-flow buckets, with the
                               two-tier (exact broker_txn_id, then content-match)
                               dedup against existing trades

Modified Dietz integrity: only CONTRIBUTION/WITHDRAWAL activities are ever
routed to `flows` (account_flows feeds net_contributed_capital in
stock_analyzer/account.py — a dividend/interest credit is performance, not a
contribution, and must never land there). Dividends/interest/fees go to
`income_events`, a display/trend-only bucket nothing else reads.
"""

from __future__ import annotations

import collections

import pandas as pd

from stock_analyzer.constants import BROKER_DRIFT_SHARE_TOL

# SnapTrade `type` values that add/remove cash-basis capital — the ONLY types
# allowed to become an account_flows row. Everything else that touches cash
# (dividend/interest/fee) is performance, not contribution, and goes to
# income_events instead. TRANSFER is deliberately excluded: it can mean an
# internal move between the user's own accounts rather than external cash in/
# out, and misclassifying it as a deposit/withdrawal would corrupt NCC.
_FLOW_TYPES = {"CONTRIBUTION": "deposit", "WITHDRAWAL": "withdrawal"}

# SnapTrade `type` values that are performance/cash-events, not trades or flows.
_INCOME_TYPES = {
    "DIVIDEND": "dividend",
    "SUBSTITUTE_DIVIDEND": "dividend",
    "REI": "dividend",
    "STOCK_DIVIDEND": "dividend",
    "INTEREST": "interest",
    "FEE": "fee",
    "TAX": "fee",
}

_TRADE_TYPES = {"BUY", "SELL"}

# Position instrument kinds treated as ordinary equity/ETF holdings for drift
# purposes. Options/crypto/other instrument kinds are excluded — the app has
# no options or crypto tracking anywhere else (project_today_pnl_scope: "user
# holds no options"), and their `instrument` shape is a different schema than
# the equity case this function is built for. "adr" added for foreign stocks
# that trade as American Depositary Receipts (e.g. SAP, ASML).
_EQUITY_INSTRUMENT_KINDS = {"stock", "etf", "adr"}


# ── Position drift (capability 1) ───────────────────────────────────────────

def _position_ticker(pos: dict) -> str | None:
    """Extract the ticker from a raw SnapTrade position dict, or None if this
    isn't an equity/ETF position (options/crypto/other) or the shape is
    unrecognised. Defensive `.get()` chain — SnapTrade's `instrument` field is
    a discriminated union keyed by `instrument.kind`."""
    instrument = pos.get("instrument", {})
    if instrument is None:
        instrument = {}
    kind = instrument.get("kind")
    if kind is not None and kind not in _EQUITY_INSTRUMENT_KINDS:
        return None
    symbol = instrument.get("symbol")
    if symbol is None:
        symbol = pos.get("symbol")
    if isinstance(symbol, dict):
        ticker = symbol.get("symbol")
    else:
        ticker = symbol
    return str(ticker).strip().upper() if ticker else None


def diff_positions(rh_positions: list[dict] | None, port_df: pd.DataFrame) -> dict | None:
    """Diff live Robinhood (via SnapTrade) positions against the app's
    trades-derived holdings.

    Parameters
    ----------
    rh_positions : list[dict] | None
        Raw output of `snaptrade_client.get_account_positions()`. None means
        the broker read failed this call — propagated as None (offline
        sentinel), NEVER treated as "no positions" / "no drift".
    port_df : pd.DataFrame
        The app's enriched portfolio frame (`Ticker`, `Shares` columns).

    Returns
    -------
    None when `rh_positions` is None (broker unreachable this call).
    Otherwise: {"rh_only": [...], "app_only": [...], "qty_mismatch": [...]}
    Each bucket entry is a plain dict; empty lists mean "checked, no drift in
    that bucket" — a real, positive result, not an unknown.
    """
    return diff_position_map(normalize_positions(rh_positions), port_df)


def normalize_positions(rh_positions: list[dict] | None) -> dict | None:
    """Raw SnapTrade position dicts → `{TICKER: shares}`, or None if unreadable.

    Split out of `diff_positions` so the broker side can be PERSISTED (the cron
    already fetches these payloads and discarded them) and re-diffed later
    against a live book. Keeps every filter the drift check depends on:
    non-equity instruments dropped, zero-unit (closed) positions skipped, and
    the same ticker summed across the user's multiple linked accounts.

    None in → None out. An empty dict is a REAL result (broker holds nothing),
    never conflated with "could not read".
    """
    if rh_positions is None:
        return None
    rh_shares: dict[str, float] = {}
    for pos in rh_positions:
        ticker = _position_ticker(pos)
        if ticker is None:
            continue
        units = pos.get("units")
        if units is None or float(units) == 0.0:
            continue  # a closed (zero-unit) position is not a real holding
        rh_shares[ticker] = rh_shares.get(ticker, 0.0) + float(units)
    return rh_shares


def diff_position_map(rh_shares: dict | None, port_df) -> dict | None:
    """Diff an already-normalized `{TICKER: shares}` broker map against the book.

    The half of `diff_positions` that does not need a live broker call, so a
    persisted snapshot can be re-diffed against CURRENT holdings on every
    render. That ordering matters: the side the user actively edits (the book)
    is always live, so a fix clears the warning immediately instead of waiting
    for the next cron.
    """
    if rh_shares is None:
        return None

    app_shares: dict[str, float] = {}
    if port_df is not None and not port_df.empty:
        for _, row in port_df.iterrows():
            ticker = str(row.get("Ticker", "")).strip().upper()
            if not ticker:
                continue
            app_shares[ticker] = app_shares.get(ticker, 0.0) + float(row.get("Shares") or 0)

    rh_only: list[dict] = []
    app_only: list[dict] = []
    qty_mismatch: list[dict] = []

    for ticker in sorted(set(rh_shares) | set(app_shares)):
        rh_qty = rh_shares.get(ticker)
        app_qty = app_shares.get(ticker)
        if rh_qty is not None and app_qty is None:
            rh_only.append({"ticker": ticker, "shares": rh_qty})
        elif app_qty is not None and rh_qty is None:
            app_only.append({"ticker": ticker, "shares": app_qty})
        else:
            diff = abs(rh_qty - app_qty)
            if diff > BROKER_DRIFT_SHARE_TOL:
                qty_mismatch.append({
                    "ticker": ticker,
                    "rh_shares": rh_qty,
                    "app_shares": app_qty,
                    "diff": rh_qty - app_qty,
                })

    return {"rh_only": rh_only, "app_only": app_only, "qty_mismatch": qty_mismatch}


# ── Balance sync (capability 2) ─────────────────────────────────────────────

def map_balances_to_cash(rh_balance) -> dict | None:
    """Map a raw SnapTrade balance payload to the `account_cash` shape.

    Parameters
    ----------
    rh_balance : the raw body from `snaptrade_client.get_account_balance()` —
        either a single balance dict or a list of per-currency balance
        dicts. None means the broker read failed this call.

    Returns
    -------
    None when `rh_balance` is None, or no usable cash figure could be found
    (e.g. an empty list, or every entry missing `cash`) — callers must treat
    this as "unknown this call", never as a zero balance.
    Otherwise: {"cash_balance": float, "note": str} — `cash_balance` is
    SIGNED (negative = margin debit), matching the account-baseline v4
    convention already used by `db.save_account_cash`.
    """
    if rh_balance is None:
        return None

    entries = rh_balance if isinstance(rh_balance, list) else [rh_balance]
    if not entries:
        return None

    def _currency_code(e: dict) -> str | None:
        cur = e.get("currency")
        if cur is None:
            cur = {}
        return cur.get("code")

    # Prefer a USD entry (Robinhood is single-currency); otherwise take the
    # first entry with a usable cash figure.
    usd = next((e for e in entries if _currency_code(e) == "USD"), None)
    chosen = usd if usd is not None else entries[0]

    cash = chosen.get("cash")
    if cash is None:
        return None

    return {"cash_balance": float(cash), "note": "Synced via SnapTrade (Robinhood)"}


# ── Transaction import (capability 3) ───────────────────────────────────────

def _activity_ticker(txn: dict) -> str:
    symbol = txn.get("symbol")
    ticker = symbol.get("symbol") if isinstance(symbol, dict) else symbol
    return str(ticker).strip().upper() if ticker else ""


def _activity_date(txn: dict):
    ts = pd.to_datetime(txn.get("trade_date"), utc=True, errors="coerce")
    return None if pd.isnull(ts) else ts.date()


def classify_transactions(rh_txns: list[dict] | None, existing_trades: pd.DataFrame) -> dict | None:
    """Classify raw SnapTrade activities into pending-import / income-event /
    cash-flow buckets, applying the two-tier dedup against `existing_trades`.

    Parameters
    ----------
    rh_txns : list[dict] | None
        Raw output of `snaptrade_client.get_account_activities()`. None means
        the broker read failed this call.
    existing_trades : pd.DataFrame
        The app's current trades (db.load_trades()), used only for BUY/SELL
        dedup — column `broker_txn_id` may or may not be present yet
        (backward-compatible: absence is treated as "no exact matches").

    Returns
    -------
    None when `rh_txns` is None. Otherwise a dict:
        new_pending             : [{snaptrade_txn_id, ticker, action, shares,
                                     price, trade_date, raw_json}, ...]
                                   BUY/SELL activities with no existing match —
                                   candidates for snaptrade_pending_imports.
        backfill_broker_txn_id  : [{trade_id, broker_txn_id}, ...]
                                   BUY/SELL activities that content-match an
                                   existing trades row lacking broker_txn_id
                                   (e.g. previously CSV-imported) — the caller
                                   should backfill the id onto that row rather
                                   than creating a duplicate pending import.
        income_events           : [{event_type, ticker, amount, event_date}, ...]
                                   dividend/interest/fee — display/trend only,
                                   NEVER read by account.py's return math.
        flows                   : [{flow_type, amount, flow_date}, ...]
                                   CONTRIBUTION/WITHDRAWAL only — the sole
                                   category allowed to touch net_contributed_capital.
        ignored                 : {type: count} — activity types not handled
                                   above (OPTIONEXPIRATION, TRANSFER, SPLIT,
                                   etc.), surfaced for transparency rather than
                                   silently dropped.
    """
    if rh_txns is None:
        return None

    has_broker_col = (
        existing_trades is not None
        and isinstance(existing_trades, pd.DataFrame)
        and "broker_txn_id" in existing_trades.columns
    )
    existing_broker_ids: set[str] = set()
    if has_broker_col:
        existing_broker_ids = set(
            existing_trades.loc[
                existing_trades["broker_txn_id"].notna(), "broker_txn_id"
            ].astype(str)
        )

    # Content-match counters (Tier 2), same shape as broker_import's
    # count-based match: (date, ticker, action, round(shares,4), round(price,2))
    # -> how many existing trades already occupy that key (only rows still
    # lacking a broker_txn_id are eligible — an already-linked row can't also
    # be a Tier-2 target).
    content_counts: collections.Counter = collections.Counter()
    content_trade_ids: dict[tuple, list] = collections.defaultdict(list)
    if existing_trades is not None and isinstance(existing_trades, pd.DataFrame) and not existing_trades.empty:
        for _, erow in existing_trades.iterrows():
            if has_broker_col and pd.notna(erow.get("broker_txn_id")):
                continue
            eaction = str(erow.get("action", "")).upper().strip()
            if eaction not in _TRADE_TYPES:
                continue
            eticker = str(erow.get("ticker", "")).upper().strip()
            try:
                eshares = round(float(erow.get("shares") or 0), 4)
                eprice = round(float(erow.get("price") or 0), 2)
            except (TypeError, ValueError):
                continue
            edt = pd.to_datetime(erow.get("traded_at"), utc=True, errors="coerce", format="ISO8601")
            if pd.isnull(edt):
                continue
            key = (edt.date(), eticker, eaction, eshares, eprice)
            content_counts[key] += 1
            content_trade_ids[key].append(erow.get("id"))

    content_seen: collections.Counter = collections.Counter()

    new_pending: list[dict] = []
    backfill: list[dict] = []
    income_events: list[dict] = []
    flows: list[dict] = []
    ignored: dict[str, int] = {}
    seen_txn_ids: set[str] = set()

    for txn in rh_txns:
        ttype = str(txn.get("type", "")).upper().strip()
        txn_id = txn.get("id")

        if ttype in _TRADE_TYPES:
            if txn_id is not None and str(txn_id) in existing_broker_ids:
                continue  # Tier 1: exact match already logged

            ticker = _activity_ticker(txn)
            trade_date = _activity_date(txn)
            units = txn.get("units")
            price = txn.get("price")
            if not ticker or trade_date is None or units is None or price is None:
                ignored_key = f"{ttype} (malformed)"
                ignored[ignored_key] = ignored.get(ignored_key, 0) + 1
                continue

            try:
                shares_r = round(abs(float(units)), 4)
                price_r = round(float(price), 2)
            except (TypeError, ValueError):
                ignored_key = f"{ttype} (malformed)"
                ignored[ignored_key] = ignored.get(ignored_key, 0) + 1
                continue

            if txn_id is not None and str(txn_id) in seen_txn_ids:
                continue  # duplicate id within this same fetch batch
            if txn_id is not None:
                seen_txn_ids.add(str(txn_id))

            key = (trade_date, ticker, ttype, shares_r, price_r)
            if content_seen[key] < content_counts[key]:
                # Tier 2: content-match against a pre-existing (e.g. CSV
                # imported) row that has no broker_txn_id yet — backfill it
                # rather than creating a duplicate pending import.
                idx = content_seen[key]
                content_seen[key] += 1
                trade_id = content_trade_ids[key][idx]
                if txn_id is not None:
                    backfill.append({"trade_id": trade_id, "broker_txn_id": str(txn_id)})
                continue

            new_pending.append({
                "snaptrade_txn_id": str(txn_id) if txn_id is not None else None,
                "ticker": ticker,
                "action": ttype,
                "shares": shares_r,
                "price": price_r,
                "trade_date": trade_date.isoformat(),
                "raw_json": txn,
            })

        elif ttype in _INCOME_TYPES:
            amount = txn.get("amount")
            event_date = _activity_date(txn)
            if amount is None or event_date is None:
                ignored[ttype] = ignored.get(ttype, 0) + 1
                continue
            income_events.append({
                "snaptrade_txn_id": str(txn_id) if txn_id is not None else None,
                "event_type": _INCOME_TYPES[ttype],
                "ticker": _activity_ticker(txn) or None,
                "amount": float(amount),
                "event_date": event_date.isoformat(),
            })

        elif ttype in _FLOW_TYPES:
            amount = txn.get("amount")
            flow_date = _activity_date(txn)
            if amount is None or flow_date is None:
                ignored[ttype] = ignored.get(ttype, 0) + 1
                continue
            flows.append({
                "flow_type": _FLOW_TYPES[ttype],
                "amount": abs(float(amount)),
                "flow_date": flow_date.isoformat(),
            })

        else:
            ignored[ttype] = ignored.get(ttype, 0) + 1

    return {
        "new_pending": new_pending,
        "backfill_broker_txn_id": backfill,
        "income_events": income_events,
        "flows": flows,
        "ignored": ignored,
    }


# ── Drift banner decision (pure) ────────────────────────────────────────────

def drift_dollar_impact(diff: dict | None, price_map: dict | None) -> dict:
    """Signed dollar impact of a drift diff on Portfolio Value.

    Positive `overstated` = the app's book counts value the broker does not, so
    every weight reads SMALLER than it is and `SINGLE_NAME_CEILING` /
    `SECTOR_CEILING` LOOSEN. That direction is why this matters: the 2026-08-23
    DELL case (+4.46%) made a true 15.0% position read 14.36% — a fail-open on
    a concentration gate, not a cosmetic display error.

    `rh_only` names are held at the broker but absent from the book, so they
    have no price here and are reported as SHARES only. Fetching a price would
    put a network call back on Home, which is the whole thing this design
    avoids; printing $0 would read as "no impact", which is worse than silence.
    """
    out = {"overstated": 0.0, "priced": [], "unpriced": [], "rh_only_shares": []}
    if not diff:
        return out
    prices = price_map or {}

    for row in diff.get("app_only", []):
        tk, sh = row["ticker"], float(row.get("shares") or 0.0)
        px = prices.get(tk)
        if px:
            out["overstated"] += sh * float(px)
            out["priced"].append({"ticker": tk, "shares": sh, "dollars": sh * float(px)})
        else:
            out["unpriced"].append({"ticker": tk, "shares": sh})

    for row in diff.get("qty_mismatch", []):
        tk = row["ticker"]
        # `diff` is rh - app, so app-minus-rh is the overstatement.
        excess = -float(row.get("diff") or 0.0)
        px = prices.get(tk)
        if px:
            out["overstated"] += excess * float(px)
            out["priced"].append({"ticker": tk, "shares": excess, "dollars": excess * float(px)})
        else:
            out["unpriced"].append({"ticker": tk, "shares": excess})

    for row in diff.get("rh_only", []):
        out["rh_only_shares"].append(
            {"ticker": row["ticker"], "shares": float(row.get("shares") or 0.0)}
        )

    out["overstated"] = round(out["overstated"], 2)
    return out


def split_awaiting_sync(diff: dict | None, recent_trade_tickers) -> tuple[dict, list]:
    """Separate drift the user has ALREADY EXPLAINED from drift that is real.

    The broker snapshot refreshes once a day; the book is diffed live. That
    asymmetry is deliberate — it means correcting a mis-logged trade clears the
    warning immediately — but it has a mirror problem that is easy to miss: the
    moment you log a PERFECTLY CORRECT trade, the book moves ahead of the
    snapshot and the ticker looks like drift. Without this split, the app's most
    common daily workflow produces a confident "Portfolio Value overstated by
    ~$5,400 — fix a missing trade" the same morning you did everything right.

    A ticker traded after `captured_at` is therefore reported as AWAITING SYNC,
    not as an error. Deliberately NOT dropped: a trade on a ticker does not
    prove the resulting share count is right, so it still has to be visible —
    just not as an accusation.
    """
    known = {str(t).strip().upper() for t in (recent_trade_tickers or [])}
    if not diff:
        return {"rh_only": [], "app_only": [], "qty_mismatch": []}, []
    if not known:
        return diff, []
    real: dict = {}
    awaiting: list = []
    for bucket in ("rh_only", "app_only", "qty_mismatch"):
        keep = []
        for row in diff.get(bucket, []):
            if row["ticker"] in known:
                awaiting.append(row["ticker"])
            else:
                keep.append(row)
        real[bucket] = keep
    return real, sorted(set(awaiting))


def tickers_traded_since(trades_df, captured_at) -> list:
    """Tickers with a trade logged AFTER `captured_at`.

    The broker snapshot refreshes once daily while the book is diffed live, so
    any ticker traded since the capture will differ for a perfectly good reason.
    Without this, logging a correct trade at 10:00 makes the drift check
    announce "Portfolio Value overstated — fix a missing trade" the same
    morning the user did everything right, which is how a real warning gets
    ignored.

    Relocated here from app.py 2026-08-24 so the headless cron path (which has
    no Streamlit, no st.session_state) can compute the same drift verdict
    app.py does for the interactive Home render, without importing the
    Streamlit-laden app.py module. app.py now calls this via
    `broker_sync.tickers_traded_since`.

    Returns [] (not None) on any failure — an empty set means "explain nothing
    away", i.e. the conservative direction that keeps drift visible.
    """
    if trades_df is None or captured_at is None:
        return []
    try:
        if getattr(trades_df, "empty", True) or "traded_at" not in trades_df.columns:
            return []
        _cap = pd.to_datetime(captured_at, utc=True, errors="coerce", format="ISO8601")
        if _cap is None or pd.isna(_cap):
            return []
        _ts = pd.to_datetime(trades_df["traded_at"], utc=True, errors="coerce",
                             format="ISO8601")
        _hit = trades_df.loc[_ts.notna() & (_ts > _cap), "ticker"]
        return sorted({str(t).strip().upper() for t in _hit if str(t).strip()})
    except (KeyError, TypeError, ValueError):
        return []


def decide_drift_banner(snapshot, holdings_df, now_et, stale_hours,
                        price_map=None, recent_trade_tickers=None) -> dict:
    """What 🏠 Home should say about app-vs-broker drift. Pure, no Streamlit.

    Extracted as a decision function rather than render-layer `if`s because
    every branch below is an offline/partial state that must be ASSERTABLE —
    the queued "extract the outage gate's decision logic into testable pure
    functions" lesson, applied before the incident rather than after it.

    `state` is one of:
      "none"        — nothing to say (no broker configured, or a clean, fresh
                      check). Silent by design: a green tick on every render is
                      the noise that trains a user past the amber one.
      "unknown"     — NOT CHECKED. No snapshot, or the book isn't loaded. Must
                      render; this is the branch that would otherwise fail open
                      into looking clean.
      "stale_clean" — checked, no mismatch, but the snapshot is older than
                      `stale_hours`. Renders as "no mismatch as of <date>, not
                      re-checked since" and NEVER as a clean bill of health.
      "drift"       — a real mismatch. Renders with the dollar impact.
    """
    if holdings_df is None or (hasattr(holdings_df, "empty") and holdings_df.empty):
        # Diffing against an unloaded book turns every broker position into a
        # fabricated `rh_only`. The Account panel refuses for the same reason.
        return {"state": "none", "reason": "no_holdings"}

    if snapshot is None:
        return {"state": "unknown", "reason": "no_snapshot"}

    positions = snapshot.get("positions")
    if positions is None:
        return {"state": "unknown", "reason": "no_positions"}

    diff = diff_position_map(positions, holdings_df)
    if diff is None:
        return {"state": "unknown", "reason": "no_positions"}

    captured_at = snapshot.get("captured_at")
    is_stale = _is_stale(captured_at, now_et, stale_hours)
    # NOTE: no writer passes False today — the cron's invariant is to skip
    # entirely when any account is unreadable, so a persisted row always has
    # all_accounts_ok=True. The column and this branch are kept because the
    # DDL default is `false` (the fail-safe reading for a legacy or
    # hand-inserted row) and because a future per-account-scoped write could
    # legitimately set it. Don't try to trigger the False path from the cron.
    all_ok = bool(snapshot.get("all_accounts_ok", False))

    # Drift on a ticker traded since the snapshot is EXPECTED, not an error —
    # the book moved ahead of a once-daily broker capture. Reported separately
    # so a correct trade never renders as a missing one.
    diff, awaiting = split_awaiting_sync(diff, recent_trade_tickers)
    has_drift = any(diff[k] for k in ("rh_only", "app_only", "qty_mismatch"))

    base = {
        "diff": diff,
        "awaiting_sync": awaiting,
        "captured_at": captured_at,
        "is_stale": is_stale,
        "all_accounts_ok": all_ok,
        "impact": drift_dollar_impact(diff, price_map),
    }

    if has_drift:
        # A stale POSITIVE is still true of its date — report it, dated.
        return {"state": "drift", **base}
    if awaiting:
        # Everything that differed is explained by a trade logged since the
        # snapshot. Informational, NOT a warning — the user did nothing wrong.
        return {"state": "awaiting_sync", **base}
    if is_stale:
        return {"state": "stale_clean", **base}
    if not all_ok:
        # Clean, fresh, but some account didn't respond when captured — a clean
        # result cannot rule out drift in the account we never read.
        return {"state": "stale_clean", **base}
    return {"state": "none", "reason": "clean", **base}


def _is_stale(captured_at, now_et, stale_hours) -> bool:
    """True when `captured_at` is older than `stale_hours`, or unreadable.

    Unreadable counts as STALE, not fresh — an unparseable timestamp is an
    unknown age, and treating an unknown as fresh is the fail-open direction.
    """
    if captured_at is None:
        return True
    try:
        ts = pd.to_datetime(captured_at, utc=True, errors="coerce", format="ISO8601")
        if ts is None or pd.isna(ts):
            return True
        now = pd.to_datetime(now_et, utc=True, errors="coerce")
        if now is None or pd.isna(now):
            return True
        return (now - ts).total_seconds() > float(stale_hours) * 3600.0
    except (TypeError, ValueError):
        return True
