"""
stock_analyzer/broker_import.py — Robinhood CSV statement importer (pure)

No Streamlit or database imports.  Pure parsing + classification only.

Parsing approach:
    parse_robinhood_csv  reads a Robinhood activity-export CSV, normalises
    Buy/Sell rows, skips non-trade rows (CDIV/ACH/INT/fees/etc.), and moves
    rows with invalid shares or price into the `invalid` bucket where they
    are surfaced to the user rather than silently dropped.

Dedup approach (classify_against_existing):
    Content-match key = (date, ticker_upper, action_upper, round(shares,4),
    round(price,2)).  For each key, `existing_count` rows already in the DB
    are allowed before the same CSV key is treated as "new".

    This correctly handles:
    - Identical multi-fills (two separate 1-share executions at the same
      price on the same day — legitimately distinct, counted separately).
    - Re-downloads of overlapping date ranges (already-imported rows are
      flagged as dupes; only net-new rows get is_new=True).

    The human-in-the-loop preview (in app.py) is the final safety net —
    this module never touches the trades source-of-truth.
"""

from __future__ import annotations

import collections
import datetime

import pandas as pd

# Columns that must be present in a valid Robinhood activity CSV
_REQUIRED_COLS = {"Activity Date", "Instrument", "Trans Code", "Quantity", "Price"}

# Trans Codes recognised as importable trades (v1 — cash events deferred)
_TRADE_CODES = {"BUY", "SELL"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _money_to_float(s) -> float | None:
    """Convert a Robinhood price/amount string to a positive float.

    Handles: leading/trailing whitespace, dollar signs ($), thousands commas,
    and parentheses (which Robinhood uses for outflow Amounts, e.g. '($518.00)').
    Price column entries should always be positive; parentheses shouldn't appear
    on Price, but we absorb them defensively via abs().

    Returns None on any parse failure.
    """
    if s is None:
        return None
    try:
        s = str(s).strip()
        # Parentheses = negative in Robinhood Amount column
        paren = s.startswith("(") and s.endswith(")")
        s = s.strip("()").replace("$", "").replace(",", "").strip()
        v = float(s)
        # For Price, parens shouldn't appear, but if they do take abs()
        if paren:
            v = abs(v)
        return v
    except (ValueError, AttributeError):
        return None


def _parse_date(d) -> datetime.date | None:
    """Parse a Robinhood activity date string (M/D/YYYY) to datetime.date.

    Returns None when the date cannot be parsed (triggers invalid classification).
    """
    try:
        ts = pd.to_datetime(d, format="%m/%d/%Y", errors="coerce")
        if pd.isnull(ts):
            return None
        return ts.date()
    except Exception:
        return None


def _parse_company(desc) -> str:
    """Extract company name from the multi-line Description field.

    Robinhood Description format:
        "Apple Inc\nCUSIP: 037833100"
    The company name is the first line, before any "CUSIP" text.
    """
    if not isinstance(desc, str):
        return ""
    first_line = desc.split("\n")[0].strip()
    if "CUSIP" in first_line:
        first_line = first_line.split("CUSIP")[0].strip().rstrip(",").strip()
    return first_line


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_robinhood_csv(file) -> dict:
    """Parse a Robinhood activity CSV file into normalised trade rows.

    Parameters
    ----------
    file : file-like object or path
        A Streamlit UploadedFile or any path/str/Path accepted by pd.read_csv.

    Returns
    -------
    dict with keys:
        trades    : pd.DataFrame with columns
                      ticker, action, shares, price, activity_date, company
                    Valid Buy/Sell rows ready for the preview / import flow.
        skipped   : dict[str, int]  Trans Code → count of non-trade rows.
        invalid   : pd.DataFrame (same columns + reason) — rows with shares or
                    price ≤ 0, surfaced to the user, never silently dropped.
                    (DB has CHECK constraints: shares > 0, price > 0.)
        error     : str | None — set when the file cannot be parsed at all
                    (wrong file type, missing required columns, read error).
    """
    _cols_trade   = ["ticker", "action", "shares", "price", "activity_date", "company"]
    _cols_invalid = _cols_trade + ["reason"]
    _empty_trades   = pd.DataFrame(columns=_cols_trade)
    _empty_invalid  = pd.DataFrame(columns=_cols_invalid)

    # ── Read raw CSV ──────────────────────────────────────────────────────────
    # on_bad_lines='skip' is required because Robinhood activity CSVs include a
    # trailing tax-disclaimer line whose column count doesn't match the header —
    # without this, pandas raises a C tokenizer error on that row.
    try:
        raw = pd.read_csv(file, on_bad_lines="skip")
    except Exception as exc:
        return {
            "trades":  _empty_trades,
            "skipped": {},
            "invalid": _empty_invalid,
            "error":   f"Could not read CSV: {exc}",
        }

    # ── Validate expected headers ─────────────────────────────────────────────
    missing = _REQUIRED_COLS - set(raw.columns)
    if missing:
        return {
            "trades":  _empty_trades,
            "skipped": {},
            "invalid": _empty_invalid,
            "error":   (
                "This doesn't look like a Robinhood activity export "
                "(missing expected columns)."
            ),
        }

    # ── Drop blank junk + disclaimer rows ────────────────────────────────────
    raw = raw.dropna(subset=["Trans Code", "Instrument"])
    raw = raw[
        raw["Trans Code"].astype(str).str.strip().ne("") &
        raw["Instrument"].astype(str).str.strip().ne("")
    ]

    if raw.empty:
        return {
            "trades":  _empty_trades,
            "skipped": {},
            "invalid": _empty_invalid,
            "error":   None,
        }

    # ── Split by Trans Code: trade rows vs everything else ────────────────────
    raw = raw.copy()
    raw["_tc_norm"] = raw["Trans Code"].astype(str).str.strip().str.upper()
    trade_mask = raw["_tc_norm"].isin(_TRADE_CODES)
    skip_rows  = raw[~trade_mask]
    trade_rows = raw[trade_mask]

    # Count non-trade rows (keep original case for transparency)
    skipped: dict[str, int] = {}
    for tc in skip_rows["Trans Code"].astype(str).str.strip():
        skipped[tc] = skipped.get(tc, 0) + 1

    if trade_rows.empty:
        return {
            "trades":  _empty_trades,
            "skipped": skipped,
            "invalid": _empty_invalid,
            "error":   None,
        }

    # ── Normalise trade rows ──────────────────────────────────────────────────
    records: list[dict]         = []
    invalid_records: list[dict] = []

    for _, row in trade_rows.iterrows():
        ticker    = str(row["Instrument"]).strip().upper()
        action    = str(row["_tc_norm"])                          # BUY or SELL
        shares_v  = _money_to_float(
            str(row.get("Quantity", "")).replace(",", "").strip()
        )
        price_v   = _money_to_float(row.get("Price"))
        act_date  = _parse_date(row.get("Activity Date"))
        company   = _parse_company(row.get("Description", ""))

        base = {
            "ticker":        ticker,
            "action":        action,
            "shares":        shares_v,
            "price":         price_v,
            "activity_date": act_date,
            "company":       company,
        }

        # Classify as valid or invalid
        reasons: list[str] = []
        if act_date is None:
            reasons.append("unparseable date")
        if shares_v is None or shares_v <= 0:
            reasons.append("shares ≤ 0 or unparseable")
        if price_v is None or price_v <= 0:
            reasons.append("price ≤ 0 or unparseable")

        if reasons:
            invalid_records.append({**base, "reason": "; ".join(reasons)})
        else:
            records.append(base)

    trades_df  = pd.DataFrame(records,         columns=_cols_trade)
    invalid_df = pd.DataFrame(invalid_records, columns=_cols_invalid)

    return {
        "trades":  trades_df,
        "skipped": skipped,
        "invalid": invalid_df,
        "error":   None,
    }


def classify_against_existing(
    candidates: pd.DataFrame,
    trades_df,
) -> pd.DataFrame:
    """Count-based content match between CSV candidates and existing trades.

    For each candidate row, checks how many times the same match key already
    exists in `trades_df`.  The first `existing_count` occurrences from the
    CSV are flagged is_new=False; the remainder are is_new=True.

    Match key = (date, ticker_upper, action_upper, round(shares,4), round(price,2))

    Parameters
    ----------
    candidates : pd.DataFrame
        Output of parse_robinhood_csv["trades"].
    trades_df : pd.DataFrame | None
        The app's current trades_df from db.load_trades(), or None / empty.
        Parsed with utc=True on traded_at (mixed-tz convention per the project
        note in feedback_pandas_mixed_tz_parsing.md).

    Returns
    -------
    candidates with two additional columns appended:
        is_new       (bool) — True when this row should be pre-selected
        match_reason (str)  — short human label for the Status column
    """
    result = candidates.copy()
    result["is_new"]       = True
    result["match_reason"] = "new"

    if result.empty:
        return result

    # ── Build counters of existing trade keys ──────────────────────────────────
    # exact  = (date, ticker, action, shares, price) — a same-day content match.
    # agnos  = (ticker, action, shares, price)        — the same trade on ANY date.
    #   The date-agnostic counter catches trades entered via the interactive
    #   Log-a-Trade form: that form does NOT set traded_at, so the DB stamps the
    #   logging timestamp (now()) rather than the real trade date — such a row
    #   won't exact-match a statement line and would otherwise re-import as a
    #   duplicate. We flag those as "possible duplicate (different date)" and
    #   leave them UNCHECKED so a mixed manual+import workflow can't double-count.
    existing_exact: collections.Counter = collections.Counter()
    existing_agnos: collections.Counter = collections.Counter()

    if (
        trades_df is not None
        and isinstance(trades_df, pd.DataFrame)
        and not trades_df.empty
    ):
        for _, erow in trades_df.iterrows():
            eaction = str(erow.get("action", "")).upper().strip()
            if eaction not in _TRADE_CODES:
                continue
            eticker = str(erow.get("ticker", "")).upper().strip()
            try:
                eshares = round(float(erow.get("shares") or 0), 4)
                eprice  = round(float(erow.get("price")  or 0), 2)
            except (TypeError, ValueError):
                continue
            agkey = (eticker, eaction, eshares, eprice)
            existing_agnos[agkey] += 1
            # utc=True handles mixed ISO 8601 offset formats in traded_at
            edt = pd.to_datetime(erow.get("traded_at"), utc=True, errors="coerce")
            if not pd.isnull(edt):
                existing_exact[(edt.date(), *agkey)] += 1

    # ── Walk candidates in stable order ───────────────────────────────────────
    # An exact (same-day) match consumes one slot from BOTH counters so the
    # date-agnostic budget is never double-spent; the remaining agnostic budget
    # then flags different-date matches; anything beyond that is genuinely new.
    exact_seen: collections.Counter = collections.Counter()
    agnos_seen: collections.Counter = collections.Counter()

    is_new_col:      list[bool] = []
    match_reason_col: list[str] = []

    for _, crow in result.iterrows():
        caction = str(crow.get("action", "")).upper().strip()
        cticker = str(crow.get("ticker", "")).upper().strip()
        cdate   = crow.get("activity_date")   # already a datetime.date or None
        try:
            cshares = round(float(crow.get("shares") or 0), 4)
            cprice  = round(float(crow.get("price")  or 0), 2)
        except (TypeError, ValueError):
            # Can't form a key — treat as new
            is_new_col.append(True)
            match_reason_col.append("new")
            continue

        agkey   = (cticker, caction, cshares, cprice)
        fullkey = (cdate, *agkey)

        if exact_seen[fullkey] < existing_exact[fullkey]:
            is_new_col.append(False)
            match_reason_col.append("matches an existing trade on this date")
            exact_seen[fullkey] += 1
            agnos_seen[agkey]   += 1
        elif agnos_seen[agkey] < existing_agnos[agkey]:
            is_new_col.append(False)
            match_reason_col.append(
                "possible duplicate — an existing trade matches on a different "
                "date (e.g. one you logged by hand)"
            )
            agnos_seen[agkey] += 1
        else:
            is_new_col.append(True)
            match_reason_col.append("new")
            agnos_seen[agkey] += 1

    result["is_new"]       = is_new_col
    result["match_reason"] = match_reason_col
    return result
