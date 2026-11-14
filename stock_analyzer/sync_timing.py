"""
Was a journal trade logged AFTER a broker sync, so the sync can't include it?
(RecTime, `docs/plans/fill-time-typed.md` S3.)

THE QUESTION. Capital vs Margin's late-trade anchor, the F-306 budget basis and
the broker-drift excuse each ask "is this trade already reflected in the broker
snapshot that was synced at `sync_at`?". They used to compare the stored
`traded_at` with `sync_at` directly. That is only sound when `traded_at` is a
real fill time. A blank-time hand log stores the moment Record was pressed (an
UPPER BOUND on the fill), and an import / broker-prefill row is stored at the
16:00 ET date anchor, so a raw comparison counted a trade the sync already
contained a second time (live 2026-11-13: #494, filled 10:21 ET, saved 14:30 ET,
already in the 11:19 ET sync).

ONE HELPER, three answers. `after_sync` returns True (certainly after the
sync, so the sync can't include it), False (certainly at or before it, so the
sync already reflects it) or None (can't tell). Each consumer resolves None in
its own protective direction; this module only says what is known. The kind
comes from `trade_time.normalize_traded_at`'s `traded_at_time_kind`.

Pure / no I/O / no Streamlit. No thresholds: every comparison is an exact
instant or an ET calendar date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from stock_analyzer.daily_pnl import today_trade_cash_delta, today_trade_share_delta
from stock_analyzer.market_time import ET
from stock_analyzer.trade_time import (
    KIND_FILL, KIND_PLACEHOLDER, KIND_RECORDED, et_trade_date,
)


def _instant(value) -> "pd.Timestamp | None":
    """`value` as a tz-aware UTC Timestamp, or None (None / NaT / unparseable).

    Naive values are read as UTC, the same as `trade_time._et_ts` and the
    `timestamptz` column. `format="ISO8601"` so mixed precision never NaTs a
    valid value.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce", format="ISO8601")
    except (TypeError, ValueError):
        return None
    if ts is None or pd.isna(ts):
        return None
    return ts


def _kind_or_fill(kind) -> "str | None":
    """A missing kind (None / NaN / absent column) reads as "fill", today's
    behaviour; a recognised kind passes through; anything else is None."""
    if kind is None:
        return KIND_FILL
    try:
        if pd.isna(kind):
            return KIND_FILL
    except (TypeError, ValueError):
        pass
    k = str(kind).strip().lower()
    return k if k in (KIND_FILL, KIND_RECORDED, KIND_PLACEHOLDER) else None


def _after_sync_ts(ts: "pd.Timestamp | None", kind, sync: "pd.Timestamp | None"):
    """`after_sync` on already-parsed instants (the frame path parses once)."""
    if ts is None or sync is None:
        return None
    k = _kind_or_fill(kind)
    if k is None:
        return None
    if k == KIND_FILL:
        # Strict: a tie is "already reflected", the existing convention.
        return bool(ts > sync)
    t_date, s_date = et_trade_date(ts), et_trade_date(sync)
    if t_date is None or s_date is None:
        return None
    if k == KIND_RECORDED:
        # The record time is an UPPER BOUND on the fill. Recorded at or before
        # the sync -> the fill was certainly before it. Recorded after it on
        # the SAME ET date -> the fill may be either side of the sync (None).
        # Recorded on a LATER ET date -> True (S3): the blank time is only
        # offered while the market is open that day, so the fill was that
        # later date's.
        if ts <= sync:
            return False
        return True if t_date > s_date else None
    # KIND_PLACEHOLDER: only the DATE is real.
    if t_date > s_date:
        return True
    if t_date < s_date:
        return False
    return None


def after_sync(traded_at, kind, sync_at) -> "bool | None":
    """Is a trade stored at `traded_at` certainly AFTER the sync at `sync_at`?

    `True`  the trade is after the sync: the sync can't include it.
    `False` the trade is at or before the sync: the sync already reflects it.
    `None`  can't tell (also: `traded_at` or `sync_at` unreadable, or an
            unrecognised `kind`).

    Instants are compared in UTC; ET calendar dates come from
    `trade_time.et_trade_date`. By `kind`
    (`trade_time.normalize_traded_at`'s `traded_at_time_kind`; a missing /
    NaN kind reads as "fill"):
      "fill"        `traded_at > sync_at` strict (a tie is already reflected).
      "recorded"    `traded_at <= sync_at` -> False; else its ET date later than
                    the sync's ET date -> True; else None.
      "placeholder" ET date later than the sync's -> True; earlier -> False;
                    the same date -> None.
    """
    return _after_sync_ts(_instant(traded_at), kind, _instant(sync_at))


def _bucket_summary(rows: "list[dict]") -> dict:
    """Per-ticker `{net_shares, cash_delta, rows}` for one set of trade rows.

    `net_shares` is signed (BUY +, SELL -) via `daily_pnl.today_trade_share_delta`
    (SPLIT excluded: it carries the adjusted TOTAL, not a delta, so a SPLIT-only
    ticker reads 0.0 here; look the rows up by index if a caller must see it).
    `cash_delta` is `daily_pnl.today_trade_cash_delta` (SELL +, BUY -).
    """
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        t = str(r.get("ticker", "")).strip().upper()
        if t:
            by_ticker.setdefault(t, []).append(r)
    out: dict[str, dict] = {}
    for t, rs in by_ticker.items():
        out[t] = {
            "net_shares": float(today_trade_share_delta(rs).get(t, 0.0)),
            "cash_delta": float(today_trade_cash_delta(rs)),
            "rows": len(rs),
        }
    return out


def classify_vs_sync(trades_df, sync_at) -> "dict | None":
    """Sort every journal row into after / before / unknown against `sync_at`.

    Returns `None` when the answer can't be computed: `trades_df` is None or
    not a DataFrame, a non-empty frame has no `traded_at` column, or `sync_at`
    is unparseable (the three-state offline sentinel: consumers must not read
    it as "nothing is after the sync"). An EMPTY frame is a real answer: all
    four row lists empty.

    Otherwise a plain dict:
      "after"    list of frame INDEX LABELS whose `after_sync` is True
      "before"   ... False
      "unknown"  ... None (can't tell; the row has a readable time)
      "unreadable"  rows whose `traded_at` can't be parsed at all. They have no
                 date, so they would sit in "unknown" forever, and an unreadable
                 row must never excuse a drift gap (as
                 `broker_sync.tickers_traded_since` already treats a NaT row).
                 They are in NO other list and in no ticker / cash summary.
      "after_by_ticker" / "unknown_by_ticker"
                 `{TICKER: {"net_shares": float, "cash_delta": float,
                 "rows": int}}` over the after / unknown rows; see
                 `_bucket_summary` for sign and SPLIT conventions
      "after_cash_delta" / "unknown_cash_delta"
                 the set's total cash delta (SELL +, BUY -)
    The `before` set has no ticker summary: nothing needs it. The `kind` for
    each row is the frame's `traded_at_time_kind` column; a missing column
    reads every row as "fill" (the status quo). Rows are not filtered by date:
    anything older than the sync's day lands in `before` by construction.

    Cost: a vectorized pre-pass sends every row that is certainly BEFORE the
    sync (a "fill" / "recorded" row at or before it; a "placeholder" row on an
    earlier ET date) straight to `before`, so the per-row Python work, and the
    row-to-dict conversion, run only on the rows left over (typically today's).
    The result is identical to the all-rows loop (`_classify(..., fast=False)`;
    a test pins it against `after_sync` row by row).
    """
    return _classify(trades_df, sync_at, fast=True)


# Per-row verdict codes (`row_codes`).
BEFORE, AFTER, UNKNOWN, UNREADABLE = 0, 1, 2, 3


def _normalized_kinds(trades_df) -> "pd.Series":
    """The frame's kind column as lower-case strings, a missing / NaN kind as
    "fill" (the same reading as `_kind_or_fill`, vectorized)."""
    if "traded_at_time_kind" not in trades_df.columns:
        return pd.Series(KIND_FILL, index=trades_df.index, dtype=object)
    k = trades_df["traded_at_time_kind"].astype("object")
    k = k.where(k.notna(), KIND_FILL)
    return k.astype(str).str.strip().str.lower()


def _codes(trades_df, sync: "pd.Timestamp", ts_all, fast: bool) -> "np.ndarray":
    """int8 verdict per row position (`BEFORE` / `AFTER` / `UNKNOWN` /
    `UNREADABLE`) for a non-empty frame with a parsed `ts_all`. `fast=False`
    skips the vectorized pre-pass and runs `_after_sync_ts` on every readable
    row (the reference path the tests compare against)."""
    n = len(trades_df)
    unreadable = ts_all.isna().to_numpy()
    before_fast = np.zeros(n, dtype=bool)
    if fast:
        kinds = _normalized_kinds(trades_df)
        # "fill" / "recorded" at or before the sync -> certainly before it
        # (`_after_sync_ts`: fill is `ts > sync`; recorded is `ts <= sync`).
        fr = kinds.isin([KIND_FILL, KIND_RECORDED]).to_numpy()
        before_fast |= fr & ~unreadable & (ts_all <= sync).to_numpy()
        # "placeholder" on an earlier ET date than the sync's -> before.
        ph = kinds.eq(KIND_PLACEHOLDER).to_numpy() & ~unreadable
        if ph.any():
            s_date = et_trade_date(sync)
            pos = np.flatnonzero(ph)
            dates = ts_all.iloc[pos].dt.tz_convert(ET).dt.date
            before_fast[pos[(dates < s_date).to_numpy()]] = True

    codes = np.full(n, BEFORE, dtype=np.int8)
    codes[unreadable] = UNREADABLE
    todo = np.flatnonzero(~unreadable & ~before_fast)
    if len(todo):
        raw_kinds = (trades_df["traded_at_time_kind"]
                     if "traded_at_time_kind" in trades_df.columns else None)
        for pos in todo:
            verdict = _after_sync_ts(
                ts_all.iloc[pos],
                raw_kinds.iloc[pos] if raw_kinds is not None else None, sync)
            codes[pos] = (AFTER if verdict is True
                          else BEFORE if verdict is False else UNKNOWN)
    return codes


def row_codes(trades_df, sync_at) -> "np.ndarray | None":
    """`after_sync` for every row of `trades_df`, as an int8 array aligned to the
    frame's row POSITIONS: `BEFORE` (0), `AFTER` (1), `UNKNOWN` (2, can't tell)
    or `UNREADABLE` (3, `traded_at` can't be parsed). An empty frame gives an
    empty array. `None` when the answer can't be computed (not a DataFrame, a
    non-empty frame with no `traded_at` column, or an unparseable `sync_at`),
    which a consumer must not read as "nothing is after the sync". Same
    vectorized pre-pass as `classify_vs_sync`."""
    if trades_df is None or not isinstance(trades_df, pd.DataFrame):
        return None
    sync = _instant(sync_at)
    if sync is None:
        return None
    if trades_df.empty:
        return np.zeros(0, dtype=np.int8)
    if "traded_at" not in trades_df.columns:
        return None
    ts_all = pd.to_datetime(trades_df["traded_at"], utc=True, errors="coerce",
                            format="ISO8601")
    return _codes(trades_df, sync, ts_all, fast=True)


def _classify(trades_df, sync_at, fast: bool) -> "dict | None":
    """`classify_vs_sync`; `fast=False` is the reference path the tests use."""
    if trades_df is None or not isinstance(trades_df, pd.DataFrame):
        return None
    sync = _instant(sync_at)
    if sync is None:
        return None
    empty = {
        "after": [], "before": [], "unknown": [], "unreadable": [],
        "after_by_ticker": {}, "unknown_by_ticker": {},
        "after_cash_delta": 0.0, "unknown_cash_delta": 0.0,
    }
    if trades_df.empty:
        return empty
    if "traded_at" not in trades_df.columns:
        return None

    # One vectorized parse, not a per-row `pd.to_datetime`.
    ts_all = pd.to_datetime(trades_df["traded_at"], utc=True, errors="coerce",
                            format="ISO8601")
    codes = _codes(trades_df, sync, ts_all, fast)
    index = trades_df.index
    after_rows = trades_df.iloc[np.flatnonzero(codes == AFTER)].to_dict("records")
    unknown_rows = trades_df.iloc[np.flatnonzero(codes == UNKNOWN)].to_dict("records")
    out = dict(empty)
    out["after"] = index[codes == AFTER].tolist()
    out["before"] = index[codes == BEFORE].tolist()
    out["unknown"] = index[codes == UNKNOWN].tolist()
    out["unreadable"] = index[codes == UNREADABLE].tolist()
    out["after_by_ticker"] = _bucket_summary(after_rows)
    out["unknown_by_ticker"] = _bucket_summary(unknown_rows)
    out["after_cash_delta"] = float(today_trade_cash_delta(after_rows))
    out["unknown_cash_delta"] = float(today_trade_cash_delta(unknown_rows))
    return out


# ─── 💰 Account cash caption (RecTime C3) ───────────────────────────────────

def account_cash_sets(trades_df, sync_at) -> "dict | None":
    """What 💰 Account's "traded since this sync" caption should say.

    `{"certain_tickers": sorted [str], "certain_delta": float,
      "unknown_tickers": sorted [str], "unknown_delta": float}`, or `None` when
    the log or the sync time can't be read (the caption then says nothing, as it
    did before when `broker_sync.tickers_traded_since` returned []).

    The CERTAIN set is the tickers of rows certainly AFTER the sync and their
    net cash effect (SELL +, BUY -): the sync can't contain them, so the cash
    figure above the caption is that much off. The UNKNOWN set is the tickers of
    rows with no fill time the app can place against the sync (left blank,
    or an import; the sync may or may not have counted them) and their cash
    effect. The two are NEVER summed: adding
    an unknown row to the certain figure would claim a correction that may not
    exist. A row whose time can't be read is in neither set.
    """
    cls = classify_vs_sync(trades_df, sync_at)
    if cls is None:
        return None
    return {
        "certain_tickers": sorted(cls["after_by_ticker"]),
        "certain_delta": float(cls["after_cash_delta"]),
        "unknown_tickers": sorted(cls["unknown_by_ticker"]),
        "unknown_delta": float(cls["unknown_cash_delta"]),
    }


def account_cash_unknown_sentence(tickers, delta, fmt_usd=None) -> "str | None":
    """The 💰 Account caption sentence for the UNKNOWN set, or None when there
    are no tickers. These rows have no fill time the app can place against the
    sync (left blank, or an import), so the sync may already include them.
    `fmt_usd(v)` formats the dollar amount (the page passes its privacy mask);
    the default is a raw `$` figure, which the caller must escape for Streamlit
    markdown. The "(about $X)" part is left out when the set nets to under a
    dollar."""
    names = [str(t) for t in (tickers or []) if str(t).strip()]
    if not names:
        return None
    many = len(names) > 1
    amount = ""
    if delta is not None and round(abs(float(delta))) >= 1:
        usd = abs(float(delta))
        amount = f" (about {fmt_usd(usd) if fmt_usd else f'${usd:,.0f}'})"
    return (f"{', '.join(names)}{amount} {'have' if many else 'has'} no fill "
            f"time the app can place against this sync (left blank, or an "
            f"import), so the figures above may already include "
            f"{'them' if many else 'it'}.")
