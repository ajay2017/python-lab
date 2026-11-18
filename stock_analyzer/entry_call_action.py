"""
One rule for "did the owner act on an entry call?", shared by every report.

An ENTRY CALL is a `new_pick`, `add_winner` or `enter_now` recommendation. The
call is ACTED when the owner BOUGHT the same ticker on the call's ET date or
within `constants.ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS` NYSE sessions after it.
Plan: docs/plans/acted-rule-and-report-periods.md (owner decisions U1-U15).

THE RULE, in the order `classify_call` applies it:
  1. Only entry-call types are judged; `buy_candidate` / `overflow_pick` and an
     `enter_now` whose `already_held` is truthy are EXCLUDED (not an entry call).
  2. A call with no readable date, or dated after the cut-off, or judged against
     a journal that could not be read, is UNKNOWN. Never "not acted".
  3. A qualifying BUY (same ticker, action BUY, ET date in
     [rec_date, min(window_end, cutoff)]) makes the call ACTED, the earliest by
     ET timestamp then lowest id. `trigger_type` is confirmation only.
  4. No qualifying BUY but a same-ticker BUY whose time cannot be parsed:
     UNKNOWN (`trade_date_unreadable`), because it might have been in the window.
  5. Window end after the cut-off: STILL_OPEN. Otherwise NOT_ACTED.
A SELL, or a BUY dated before the call, is never looked at.
Known limit: when one same-ticker BUY has an unreadable date and another BUY in
the window is readable, the call is ACTED but the matched fill (and so the fill
price behind engine alpha) may not be the true earliest one.

AS-OF CUT-OFF. `cutoff` is a required keyword and the module never reads the
clock, so a regenerated report gives byte-identical output. Trades after the
cutoff are ignored; a call whose window runs past it reads STILL_OPEN, never
"missed".

DATES. A trade's date is the ET date of its re-anchored `traded_at`
(`trade_time.normalize_traded_at`, idempotent and provenance-gated, so a raw
frame and a `db.load_trades_or_none` frame agree). A tz-naive value is read as
UTC, the same contract as `trade_time`. `rec_date` is a plain ET calendar date
and is never tz-converted; a legacy weekend / holiday `rec_date` counts
sessions from the date as stored (the same as `rec_events`).

NONE vs EMPTY. `trades_df is None` means the journal could not be read: every
entry call is UNKNOWN and `apply_to_matched` returns None. An empty frame is a
genuinely empty journal and is judged normally.

This module does not replace `recommendations_history.match_recs_to_trades`
(about 20 callers; its same-day rule feeds engine alpha). `apply_to_matched`
only rewrites its output for the reports that adopt this rule.

Pure / no Streamlit / no DB / no clock.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Iterable, Mapping

import pandas as pd

from stock_analyzer import constants
from stock_analyzer.predictive_analytics import advance_trading_days
from stock_analyzer.recommendations_history import _dedup_acted_credit, _f
from stock_analyzer.trade_time import normalize_traded_at

_ET = "America/New_York"

# The call types this rule judges. Defined here on purpose, NOT imported from
# `predictive_analytics.ACTIONABLE_REC_TYPES`: that tuple answers a different
# question (which recs enter the outcome statistics), and tying the two would
# let an analytics change silently move what the reports call "acted".
ENTRY_CALL_REC_TYPES = ("new_pick", "add_winner", "enter_now")

# 1 = the older rules (same-day UTC match, weekly any-trade match, ...).
ACTED_RULE_VERSION = 2

# A matched trade whose `trigger_type` is this was taken as the app suggested.
CONFIRMING_TRIGGER = "RECOMMENDATION"

STATUS_ACTED = "acted"
STATUS_NOT_ACTED = "not_acted"
STATUS_STILL_OPEN = "still_open"
STATUS_UNKNOWN = "unknown"
STATUS_EXCLUDED = "excluded"

REASON_TRADES_UNAVAILABLE = "trades_unavailable"
REASON_TRADES_UNREADABLE = "trades_unreadable"
REASON_NO_CALL_DATE = "no_call_date"
REASON_CALL_AFTER_CUTOFF = "call_after_cutoff"
REASON_TRADE_DATE_UNREADABLE = "trade_date_unreadable"
REASON_NOT_ENTRY_CALL_TYPE = "not_entry_call_type"
REASON_ALREADY_HELD = "already_held"

_REQUIRED_TRADE_COLS = ("ticker", "action", "traded_at")

_JOURNAL_OK = "ok"
_JOURNAL_UNAVAILABLE = "unavailable"
_JOURNAL_UNREADABLE = "unreadable"


# ── small normalisers ───────────────────────────────────────────────────────

def _call_date(v) -> date | None:
    """A call's `rec_date` as a plain date, or None.

    date, datetime / Timestamp (its own calendar date, never tz-converted) and
    ISO text (first 10 chars). None / NaN / NaT / unparseable -> None.
    `pd.NaT` is a `datetime` whose `.date()` is NaT, so existing `_to_date`
    helpers do not catch it; hence this dedicated reader.
    """
    if v is None:
        return None
    try:
        if isinstance(v, datetime):
            return None if pd.isna(v) else v.date()
        if isinstance(v, date):
            return v
        if isinstance(v, float) and v != v:
            return None
        s = str(v).strip()[:10]
        return date.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def _truthy_flag(v) -> bool:
    """True only for a real affirmative (True / 1 / "true"); None / NaN / absent
    is NOT affirmative. Mirrors `self_track_record._truthy_flag`, so a legacy
    `enter_now` with a NULL `already_held` is judged as not held."""
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "t", "1", "yes")
    try:
        return bool(v) and v == v
    except (TypeError, ValueError):
        return False


def _norm_text(v) -> str:
    """Stripped upper-case text; None / NaN -> ''."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip().upper()


def _id_key(v) -> float:
    """Sort key for a trade / call id: numeric ascending, unreadable last."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float("inf")
    return float("inf") if x != x else x


def _window(window_trading_days: int | None) -> int:
    return (constants.ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS
            if window_trading_days is None else int(window_trading_days))


def window_end(rec_date: date, window_trading_days: int | None = None) -> date:
    """Last ET date on which a BUY still counts for a call dated `rec_date`.

    `rec_date` plus N NYSE sessions, N defaulting to
    `constants.ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS`. A weekend / holiday
    `rec_date` counts from the date as stored (no roll-forward first).
    """
    return advance_trading_days(rec_date, _window(window_trading_days))


# ── journal preparation (once per batch) ────────────────────────────────────

def _et_stamps(col: "pd.Series") -> list:
    """Every `traded_at` as an ET Timestamp (None when unparseable). Naive
    values are UTC. Whole-column parse; a per-value fallback if pandas refuses
    the mixed column."""
    try:
        parsed = pd.to_datetime(col, utc=True, format="ISO8601",
                                errors="coerce").dt.tz_convert(_ET)
        return [None if pd.isna(t) else t for t in parsed]
    except (TypeError, ValueError, AttributeError):
        out = []
        for v in col:
            try:
                t = pd.to_datetime(v, utc=True, errors="coerce", format="ISO8601")
            except (TypeError, ValueError):
                out.append(None)
                continue
            out.append(None if t is None or pd.isna(t) else t.tz_convert(_ET))
        return out


def _prepare_journal(trades_df) -> tuple[str, dict[str, list[dict]]]:
    """(state, {TICKER: [BUY entries]}) built once for a whole batch.

    state is "unavailable" for None, "unreadable" for a non-empty frame without
    ticker / action / traded_at, else "ok" (an empty frame is an empty journal).
    Each entry: {"id", "ts" (ET Timestamp or None), "date" (ET date or None),
    "rec" (the normalised row as a dict)}. Only BUYs are kept.
    """
    if trades_df is None:
        return _JOURNAL_UNAVAILABLE, {}
    if not isinstance(trades_df, pd.DataFrame):
        return _JOURNAL_UNREADABLE, {}
    if len(trades_df) == 0:
        return _JOURNAL_OK, {}
    if any(c not in trades_df.columns for c in _REQUIRED_TRADE_COLS):
        return _JOURNAL_UNREADABLE, {}

    df = normalize_traded_at(trades_df)
    stamps = _et_stamps(df["traded_at"])
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for rec, ts in zip(df.to_dict("records"), stamps):
        if _norm_text(rec.get("action")) != "BUY":
            continue
        tk = _norm_text(rec.get("ticker"))
        if not tk:
            continue
        by_ticker[tk].append({
            "id":   rec.get("id"),
            "ts":   ts,
            "date": None if ts is None else ts.date(),
            "rec":  rec,
        })
    return _JOURNAL_OK, dict(by_ticker)


def _matched_trade(rec: Mapping) -> dict:
    """A trade row in EXACTLY the shape of `match_recs_to_trades`' `acted_trade`."""
    return {
        "id":           rec.get("id"),
        "action":       str(rec.get("action", "") or ""),
        "shares":       _f(rec.get("shares")),
        "price":        _f(rec.get("price")),
        "cost_basis":   _f(rec.get("cost_basis")),
        "realized_pnl": _f(rec.get("realized_pnl")),
        "traded_at":    rec.get("traded_at"),
    }


# ── classification ──────────────────────────────────────────────────────────

def _classify(call: Mapping, state: str, by_ticker: dict[str, list[dict]],
              cutoff: date, n: int) -> dict:
    """The decision order from the module docstring, against a prepared journal."""
    rec_type = str(call.get("rec_type", "") or "").strip()
    ticker = _norm_text(call.get("ticker"))
    rd = _call_date(call.get("rec_date"))
    wend = None if rd is None else window_end(rd, n)

    out = {
        "id": call.get("id"), "ticker": ticker, "rec_type": rec_type,
        "rec_date": rd, "status": None, "reason": None, "window_end": wend,
        "cutoff": cutoff, "matched_trade": None, "matched_trade_date": None,
        "confirmed": False, "rule_version": ACTED_RULE_VERSION,
    }

    def _done(status: str, reason: str | None = None) -> dict:
        out["status"], out["reason"] = status, reason
        return out

    if rec_type not in ENTRY_CALL_REC_TYPES:
        return _done(STATUS_EXCLUDED, REASON_NOT_ENTRY_CALL_TYPE)
    if rec_type == "enter_now" and _truthy_flag(call.get("already_held")):
        return _done(STATUS_EXCLUDED, REASON_ALREADY_HELD)
    if rd is None:
        return _done(STATUS_UNKNOWN, REASON_NO_CALL_DATE)
    if rd > cutoff:
        return _done(STATUS_UNKNOWN, REASON_CALL_AFTER_CUTOFF)
    if state == _JOURNAL_UNAVAILABLE:
        return _done(STATUS_UNKNOWN, REASON_TRADES_UNAVAILABLE)
    if state == _JOURNAL_UNREADABLE:
        return _done(STATUS_UNKNOWN, REASON_TRADES_UNREADABLE)

    buys = by_ticker.get(ticker, [])
    upper = min(wend, cutoff)
    qualifying = [b for b in buys
                  if b["date"] is not None and rd <= b["date"] <= upper]
    if qualifying:
        win = min(qualifying, key=lambda b: (b["ts"], _id_key(b["id"])))
        out["matched_trade"] = _matched_trade(win["rec"])
        out["matched_trade_date"] = win["date"]
        out["confirmed"] = (_norm_text(win["rec"].get("trigger_type"))
                            == CONFIRMING_TRIGGER)
        return _done(STATUS_ACTED)
    if any(b["date"] is None for b in buys):
        return _done(STATUS_UNKNOWN, REASON_TRADE_DATE_UNREADABLE)
    if wend > cutoff:
        return _done(STATUS_STILL_OPEN)
    return _done(STATUS_NOT_ACTED)


def classify_call(call: Mapping, trades_df: "pd.DataFrame | None", *, cutoff: date,
                  window_trading_days: int | None = None) -> dict:
    """Judge ONE call against the journal as of `cutoff`.

    Returns {id, ticker, rec_type, rec_date, status, reason, window_end, cutoff,
    matched_trade, matched_trade_date, confirmed, rule_version}. For many calls
    use `classify_calls`, which normalises the journal once.
    """
    state, by_ticker = _prepare_journal(trades_df)
    return _classify(call, state, by_ticker, cutoff, _window(window_trading_days))


def classify_calls(calls: "pd.DataFrame | Iterable[Mapping]",
                   trades_df: "pd.DataFrame | None", *, cutoff: date,
                   window_trading_days: int | None = None) -> list[dict]:
    """`classify_call` for every row, input order kept, journal prepared once."""
    rows = calls.to_dict("records") if isinstance(calls, pd.DataFrame) else list(calls)
    state, by_ticker = _prepare_journal(trades_df)
    n = _window(window_trading_days)
    return [_classify(c, state, by_ticker, cutoff, n) for c in rows]


# ── per-name roll-up ────────────────────────────────────────────────────────

def per_name(results: list[dict]) -> list[dict]:
    """One row per ticker from `classify_calls` results, sorted by ticker.

    Excluded calls are ignored. Precedence: acted > still_open > unknown >
    not_acted, so a name reads not_acted only when every call on it was judged.
    Keys: ticker, status, n_calls, rec_types (sorted unique), first_call_date,
    last_call_date, credited_call_date / matched_trade_id / matched_trade_date /
    confirmed (from the earliest acted call, else None / False), window_end
    (the credited call's for acted; the latest still_open call's for still_open;
    otherwise the latest of all).
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results or []:
        if r.get("status") != STATUS_EXCLUDED:
            groups[r["ticker"]].append(r)

    out: list[dict] = []
    for tk in sorted(groups):
        rows = groups[tk]
        statuses = {r["status"] for r in rows}
        dates = [r["rec_date"] for r in rows if r.get("rec_date") is not None]
        credited = None
        if STATUS_ACTED in statuses:
            status = STATUS_ACTED
            credited = min((r for r in rows if r["status"] == STATUS_ACTED),
                           key=lambda r: (r["rec_date"] or date.max,
                                          _id_key(r.get("id"))))
            wend = credited["window_end"]
        elif STATUS_STILL_OPEN in statuses:
            status = STATUS_STILL_OPEN
            ends = [r["window_end"] for r in rows
                    if r["status"] == STATUS_STILL_OPEN and r["window_end"] is not None]
            wend = max(ends) if ends else None
        else:
            status = (STATUS_UNKNOWN if STATUS_UNKNOWN in statuses
                      else STATUS_NOT_ACTED)
            ends = [r["window_end"] for r in rows if r["window_end"] is not None]
            wend = max(ends) if ends else None
        mt_id = credited["matched_trade"]["id"] if credited else None
        out.append({
            "ticker":             tk,
            "status":             status,
            "n_calls":            len(rows),
            "rec_types":          sorted({r["rec_type"] for r in rows}),
            "first_call_date":    min(dates) if dates else None,
            "last_call_date":     max(dates) if dates else None,
            "credited_call_date": credited["rec_date"] if credited else None,
            "matched_trade_id":   mt_id,
            "matched_trade_date": credited["matched_trade_date"] if credited else None,
            "confirmed":          bool(credited["confirmed"]) if credited else False,
            "window_end":         wend,
        })
    return out


# ── rewrite of match_recs_to_trades output ──────────────────────────────────

def apply_to_matched(matched: list[dict], recs_df, trades_df, *, cutoff: date,
                     window_trading_days: int | None = None) -> "list[dict] | None":
    """`match_recs_to_trades` output re-judged by this rule, for `compute_outcomes`.

    None when `trades_df` or `recs_df` is None (`compute_outcomes` cannot
    represent "unknown"; the caller must show the offline state). Otherwise a
    NEW list of NEW dicts, same length and order; the input is not mutated.

    Entry-call rows that are not excluded are classified from their own
    ticker / rec_date / rec_type (`already_held` is read from `recs_df` by id;
    a missing column or id reads as NULL = judged). They get `acted_on`
    (status == acted), `acted_trade` (the matched trade or None),
    `entry_call_status`, `entry_call_reason`, `entry_call_window_end`,
    `acted_rule_version` and `credit_deduped`.

    The locked A4 same-(ticker, rec_date) cross-type dedup is applied by
    reusing `recommendations_history._dedup_acted_credit` on the rewritten
    entry rows only: the loser keeps `entry_call_status == "acted"` but loses
    `acted_on` / `acted_trade` and gets `credit_deduped = True`. One BUY may
    still credit several calls with different dates (their windows overlap).

    Every other row (buy_candidate, overflow_pick, already-held enter_now) is
    copied unchanged except `entry_call_status = "excluded"` and its reason;
    filtering those out is the caller's job.

    ⚠️ CALLER CONTRACT (C4). Split on `entry_call_status`, NEVER on `acted_on` or
    `rec_type`: `acted_on` is False on still_open, unknown and credit_deduped rows
    too (so "not acted_on" is NOT "missed"), and an excluded row keeps the OLD
    same-day rule's `acted_on` / `acted_trade` (any direction, so a SELL can
    credit it) and still carries an entry-call `rec_type` (an already-held
    `enter_now`). The arms are: acted with `credit_deduped` False = acted;
    not_acted = passed; every other status = out of both arms. Pass only calls
    dated on or before `cutoff` (a later call reads unknown and would turn a
    not_acted name unknown in `per_name`).
    """
    if trades_df is None or recs_df is None:
        return None

    held_by_id: dict = {}
    try:
        if "already_held" in recs_df.columns and "id" in recs_df.columns:
            for rid, h in zip(recs_df["id"], recs_df["already_held"]):
                # pd.isna, not `rid == rid`: a pd.NA (Int64) id makes the
                # comparison raise; a missing id reads "not found" instead
                try:
                    if rid is None or pd.isna(rid):
                        continue
                except (TypeError, ValueError):
                    continue
                held_by_id[rid] = h
    except AttributeError:
        pass

    state, by_ticker = _prepare_journal(trades_df)
    n = _window(window_trading_days)

    rewritten: list[dict] = []
    new_rows: list[dict] = []
    for row in matched:
        new = dict(row)
        rec_type = str(row.get("rec_type", "") or "").strip()
        row_id = row.get("id")
        try:
            held = None if pd.isna(row_id) else held_by_id.get(row_id)
        except (TypeError, ValueError):
            held = None
        call = {"id": row_id, "ticker": row.get("ticker"),
                "rec_date": row.get("rec_date"), "rec_type": rec_type,
                "already_held": held}
        res = _classify(call, state, by_ticker, cutoff, n)
        if res["status"] == STATUS_EXCLUDED:
            new["entry_call_status"] = STATUS_EXCLUDED
            new["entry_call_reason"] = res["reason"]
        else:
            new["acted_on"] = res["status"] == STATUS_ACTED
            new["acted_trade"] = res["matched_trade"]
            new["entry_call_status"] = res["status"]
            new["entry_call_reason"] = res["reason"]
            new["entry_call_window_end"] = res["window_end"]
            new["acted_rule_version"] = ACTED_RULE_VERSION
            new["credit_deduped"] = False
            rewritten.append(new)
        new_rows.append(new)

    _dedup_acted_credit(rewritten)
    for r in rewritten:
        if r["entry_call_status"] == STATUS_ACTED and not r["acted_on"]:
            r["credit_deduped"] = True
    return new_rows
