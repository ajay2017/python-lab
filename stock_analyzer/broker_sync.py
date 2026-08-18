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
# the equity case this function is built for.
_EQUITY_INSTRUMENT_KINDS = {"stock", "etf"}


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
            edt = pd.to_datetime(erow.get("traded_at"), utc=True, errors="coerce")
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
