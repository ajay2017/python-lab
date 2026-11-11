"""TJDate C5: the journal vs Robinhood matcher (docs/plans/journal-broker-match-c5.md).

PURE and READ-ONLY: no streamlit, no db, no network, no `stock_analyzer.data`
import and no clock read (`now_et` is passed in). It computes one verdict per
unlinked journal row and per Robinhood fill and writes nothing. The Pending
Imports verdicts (U9), the Home line (U8) and the correction planner (Phase 2)
are later chunks that consume these findings.

Why a second matcher next to `trade_entry_checks.pending_fill_findings`: that one
answers "one staged entry vs the pending fills" at log time; this one answers
"the whole journal vs every fill" after the fact. The two share their primitives
(the helpers imported from `trade_entry_checks`) and a cross-consistency test
keeps them from drifting.

Facts the rules rest on:
  * SnapTrade's `trade_date` is the ORDER PLACEMENT time (P0 probe), so a
    journal row dated after a fill is "dates differ", not "the journal is wrong":
    the REDATE / SPLIT text says so, and its later one-click needs the owner to
    confirm Robinhood's 'Filled' date. A fill placed on a weekend or holiday is
    read as the next trading session; an evening placement is never shifted.
  * Fills of EVERY status take part (owner D3): the status is the owner's
    workflow state, not a fact about the fill. "Linked" is decided only by
    `trades.broker_txn_id`.
  * A journal row with no Robinhood record is judged only once the last
    completed transaction sync is on a later ET date AND at least
    `JOURNAL_MATCH_BROKER_LAG_SESSIONS` sessions after the row (owner D1);
    before that it reads "not compared yet". An empty result is therefore never
    worded "matches Robinhood": show `match_coverage(...)["text"]` beside it.

Explanation tiers (the weaker a story, the later it is tried, and a stronger
story on the same fills always wins): 1 RECONCILED (a whole day's fills),
2 REDATE / SPLIT / FORWARD / PRICE_FIX (exact shares AND price, or same-day
shares), 3 MERGED_FILLS (fills from more than one day whose shares add up to
the row at a price that is NOT their average: a row that merged fills from two
days, owner's 2026-11-11 dry run, MRVL #419 5 @ 260.00 = 2 + 1 @ 260.00 on 10-14
and 2 @ 235.00 on 10-15; ANCHORED: at least one of the fills is at the row's own
price, review of 2026-11-11, because a bare share-count match could explain away a
real missing trade), 4 QTY_MISMATCH (one same-price fill, or a whole day's
fills, with a different share count).

Three-state output, like every cache here: `None` = couldn't check (an
unreadable journal or fills input), `[]` = checked and nothing to say,
otherwise a list of findings (see `match_journal_to_broker`).

C5 judges ET DATES only, never the time of day.
"""

from __future__ import annotations

import itertools
import json
import math
from datetime import date, datetime, timedelta

import pandas as pd

from stock_analyzer import trade_entry_checks as _tec, trade_time
from stock_analyzer.constants import (
    ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS,
    BROKER_DRIFT_SHARE_TOL,
    JOURNAL_MATCH_BROKER_LAG_SESSIONS,
    JOURNAL_MATCH_LOOKBACK_DAYS,
    JOURNAL_MATCH_MAX_CANDIDATES,
    JOURNAL_MATCH_MAX_FILLS,
    JOURNAL_MATCH_WINDOW_SESSIONS,
)

# ── finding kinds ────────────────────────────────────────────────────────────
KIND_UNLOGGED = "UNLOGGED"
KIND_JOURNAL_ONLY = "JOURNAL_ONLY"
KIND_QTY_MISMATCH = "QTY_MISMATCH"
KIND_PRICE_FIX = "PRICE_FIX"
KIND_REDATE = "REDATE"
KIND_SPLIT = "SPLIT"
KIND_MERGED_FILLS = "MERGED_FILLS"
KIND_FORWARD = "FORWARD"
KIND_LINKED_DIFFERS = "LINKED_DIFFERS"
KIND_AMBIGUOUS = "AMBIGUOUS"
KIND_TOO_MANY = "TOO_MANY"
KIND_UNCHECKED = "UNCHECKED"
KIND_NOT_YET_COMPARED = "NOT_YET_COMPARED"
KIND_LINK = "LINK"
KIND_RECONCILED = "RECONCILED"
KIND_SKIPPED_NON_EQUITY = "SKIPPED_NON_EQUITY"

SEV_DIFFERS = "differs"
SEV_UNRESOLVED = "unresolved"
SEV_UNCHECKED = "unchecked"
SEV_INFO = "info"

_SEVERITY = {
    KIND_UNLOGGED: SEV_DIFFERS, KIND_JOURNAL_ONLY: SEV_DIFFERS,
    KIND_QTY_MISMATCH: SEV_DIFFERS, KIND_PRICE_FIX: SEV_DIFFERS,
    KIND_REDATE: SEV_DIFFERS, KIND_SPLIT: SEV_DIFFERS, KIND_FORWARD: SEV_DIFFERS,
    KIND_MERGED_FILLS: SEV_DIFFERS,
    KIND_LINKED_DIFFERS: SEV_DIFFERS,
    KIND_AMBIGUOUS: SEV_UNRESOLVED, KIND_TOO_MANY: SEV_UNRESOLVED,
    KIND_UNCHECKED: SEV_UNCHECKED, KIND_NOT_YET_COMPARED: SEV_UNCHECKED,
    KIND_LINK: SEV_INFO, KIND_RECONCILED: SEV_INFO, KIND_SKIPPED_NON_EQUITY: SEV_INFO,
}

# Rank (owner D5). UNLOGGED / QTY_MISMATCH / JOURNAL_ONLY depend on the action or
# the sign of `holdings_effect` and are resolved in `_rank`.
_RANK = {
    KIND_PRICE_FIX: 70, KIND_REDATE: 80, KIND_SPLIT: 90, KIND_MERGED_FILLS: 95,
    KIND_FORWARD: 100,
    KIND_LINKED_DIFFERS: 110, KIND_AMBIGUOUS: 120, KIND_TOO_MANY: 130,
    KIND_UNCHECKED: 140, KIND_NOT_YET_COMPARED: 150, KIND_LINK: 160,
    KIND_RECONCILED: 170, KIND_SKIPPED_NON_EQUITY: 180,
}

BLOCK_NO_TXN_ID = "no_txn_id"
BLOCK_OUTSIDE_REPAIR = "outside_repair_lookback"
BLOCK_FORWARD = "forward"
BLOCK_DISPLAY_ONLY = "display_only"
BLOCK_MULTI_FILL = "needs_multi_fill_link"

_JOURNAL_COLUMNS = ("id", "ticker", "action", "shares", "price", "traded_at",
                    "broker_txn_id")


# ── calendar and coverage ────────────────────────────────────────────────────

def _session_on_or_after(d: date) -> date:
    """`d` if it is a trading session, else the next one (a weekend or holiday
    placement is read as the next session; no shift for evening times)."""
    while not _tec._is_trading_day(d):
        d += timedelta(days=1)
    return d


def _judged(d: date, s) -> bool:
    """True when a journal row dated `d` may be called "not in Robinhood": the
    last completed sync (ET date `s`) is on a LATER date and at least
    `JOURNAL_MATCH_BROKER_LAG_SESSIONS` sessions after `d`."""
    return (s is not None and s > d
            and _tec._sessions_between(d, s) >= JOURNAL_MATCH_BROKER_LAG_SESSIONS)


def _sync_et(synced_at):
    """The last completed sync as an ET datetime, or None. Parsed exactly as
    `trade_entry_checks.broker_check_coverage` does (a naive value is read as
    UTC; unreadable is None)."""
    if synced_at is None:
        return None
    try:
        return _tec._to_et_datetime(
            synced_at.isoformat() if isinstance(synced_at, datetime) else synced_at)
    except (ValueError, AttributeError, TypeError):
        return None


def _sync_when(st) -> str:
    return f"{_tec._fmt_date(st.date())}, {_tec._fmt_dt_clock(st)} ET"


def fills_read_since(now_et) -> date:
    """The earliest fill PLACEMENT date the matcher needs: the lower bound for the
    C5b loader (`db.load_snaptrade_fills_or_none(fills_read_since(now).isoformat())`).
    The pool starts at session `pool_start = fills_since(today - LOOKBACK)` (the
    window start stepped back `JOURNAL_MATCH_WINDOW_SESSIONS` sessions) and a fill
    belongs to it by its SESSION, so a Saturday or Sunday placement that rolls to
    `pool_start` must be read too: the bound is the day AFTER the trading day before
    `pool_start`."""
    start = _tec._aware_et(now_et).date() - timedelta(days=int(JOURNAL_MATCH_LOOKBACK_DAYS))
    pool_start = _tec.fills_since(start)
    return _tec._previous_trading_day(pool_start) + timedelta(days=1)


def match_coverage(synced_at) -> dict:
    """How far the journal-vs-Robinhood comparison reaches, given the last
    completed transaction sync.

    -> `{"state": "known" | "unknown", "synced_at_et": datetime | None,
    "judged_through": date | None, "text": str}` (`judged_through` is None only
    when the state is "unknown"). It is the latest date a journal row can carry and still be judged against Robinhood
    (`_judged`); rows dated after it read "not compared yet". Show `text` beside
    every "no differences".
    """
    st = _sync_et(synced_at)
    if st is None:
        return {"state": "unknown", "synced_at_et": None, "judged_through": None,
                "text": ("The time of the last completed Robinhood sync couldn't be read, "
                         "so journal rows with no Robinhood record aren't flagged.")}
    sd = st.date()
    # The session count between d and the sync date only grows going back, so this
    # always ends (after about LAG sessions' worth of days).
    through = sd - timedelta(days=1)
    while not _judged(through, sd):
        through -= timedelta(days=1)
    text = (f"Journal rows dated after {_tec._fmt_date(through)} aren't compared "
            f"with Robinhood yet (last completed sync {_sync_when(st)}).")
    return {"state": "known", "synced_at_et": st, "judged_through": through, "text": text}


# ── input parsing ────────────────────────────────────────────────────────────

def _s(v) -> str:
    """Stripped text; None / NaN / '' / 'None' read as ''."""
    return _tec._txn_text(v) or ""


def _ts_key(v):
    """A UTC Timestamp for ordering, the earliest possible when unreadable."""
    try:
        ts = pd.to_datetime(v, utc=True, errors="coerce", format="ISO8601")
    except (TypeError, ValueError):
        ts = None
    if ts is None or pd.isna(ts):
        return pd.Timestamp.min.tz_localize("UTC")
    return ts


def _parse_journal(journal):
    """-> (linked_ids, rows). Each row: id, ticker, act, date, ts, shares, price,
    txn (text or None), linked, ok. Only BUY / SELL rows with a ticker are kept.
    The frame must be valid (columns checked by the caller)."""
    if len(journal) == 0:
        return set(), []
    df = trade_time.normalize_traded_at(journal)
    linked_ids: set = set()
    for v in df["broker_txn_id"]:
        t = _tec._txn_text(v)
        if t is not None:
            linked_ids.add(t)
    rows: list = []
    for r in df.to_dict("records"):
        act = _s(r.get("action")).upper()
        if act not in ("BUY", "SELL"):
            continue
        tk = _s(r.get("ticker")).upper()
        if not tk:
            continue
        rid = _tec._num(r.get("id"))
        rid = int(rid) if rid is not None and rid > 0 else None
        sh, px = _tec._num(r.get("shares")), _tec._num(r.get("price"))
        sh = sh if sh is not None and sh > 0 else None
        px = px if px is not None and px > 0 else None
        d = trade_time.et_trade_date(r.get("traded_at"))
        txn = _tec._txn_text(r.get("broker_txn_id"))
        rows.append({"id": rid, "ticker": tk, "act": act, "date": d,
                     "ts": _ts_key(r.get("traded_at")), "shares": sh, "price": px,
                     "txn": txn, "linked": txn is not None,
                     "ok": rid is not None and d is not None and sh is not None and px is not None})
    return linked_ids, rows


def _parse_raw(raw_json):
    raw = raw_json
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    return raw if isinstance(raw, dict) else None


def _parse_fills(fills):
    """Validated, deduplicated fills as records, or None when the input is not
    usable (a non-dict element, a blank ticker, an action not BUY / SELL)."""
    for f in fills:
        if not isinstance(f, dict):
            return None
        if not _s(f.get("ticker")):
            return None
        if _s(f.get("action")).upper() not in ("BUY", "SELL"):
            return None
    ordered = sorted(
        fills,
        key=lambda f: (_tec._num(f.get("id")) if _tec._num(f.get("id")) is not None else math.inf,
                       _s(f.get("snaptrade_txn_id")),
                       _tec._num(f.get("shares")) if _tec._num(f.get("shares")) is not None else math.inf,
                       _tec._num(f.get("price")) if _tec._num(f.get("price")) is not None else math.inf))
    out: list = []
    seen: set = set()
    for f in ordered:
        txn = _tec._txn_text(f.get("snaptrade_txn_id"))
        if txn is not None:
            if txn in seen:
                continue
            seen.add(txn)
        raw = _parse_raw(f.get("raw_json"))
        nonequity = raw is not None and _tec._txn_text(raw.get("option_symbol")) is not None
        placed = _tec._as_date(f.get("trade_date"))
        session = _session_on_or_after(placed) if placed is not None else None
        placed_at = None
        if raw is not None and raw.get("trade_date") is not None:
            try:
                placed_at = _tec._to_et_datetime(raw["trade_date"])
            except (ValueError, TypeError):
                placed_at = None
        sh = _tec._num(f.get("shares"))
        sh = sh if sh is not None and sh > 0 else None
        px = _tec._raw_price(f.get("raw_json"))
        if px is None:
            px = _tec._num(f.get("price"))
            px = px if px is not None and px > 0 else None
        status = str(f.get("status") or "").strip().lower() or None
        pid = f.get("id")
        pnum = _tec._num(pid)
        out.append({
            "pid": pid, "txn": txn, "ticker": _s(f.get("ticker")).upper(),
            "act": _s(f.get("action")).upper(), "placed": placed, "session": session,
            "placed_at": placed_at, "shares": sh, "price": px, "status": status,
            "nonequity": nonequity,
            "ok": placed is not None and sh is not None and px is not None,
            "sk": (session.toordinal() if session is not None else 0, txn or "",
                   pnum if pnum is not None else math.inf,
                   sh if sh is not None else math.inf, px if px is not None else math.inf),
        })
    for i, f in enumerate(sorted(out, key=lambda f: f["sk"])):
        f["k"] = i
    return out


# ── formatting and field helpers ─────────────────────────────────────────────

def _fd(d) -> str:
    return _tec._fmt_date(d)


def _sh(x) -> str:
    return _tec._fmt_shares(x)


def _shares_text(x) -> str:
    """'1 share' / '5 shares' / '0.5 shares'."""
    t = _tec._fmt_shares(x)
    return f"{t} {'share' if t == '1' else 'shares'}"


def _px(x) -> str:
    return _tec._fmt_price(x)


def _vwap(items):
    tot = sum(i["shares"] for i in items)
    return sum(i["shares"] * i["price"] for i in items) / tot if tot else None


def _holdings(act: str, jsh: float, bsh: float) -> float:
    d = (jsh - bsh) if act == "BUY" else (bsh - jsh)
    return round(d, 9) + 0.0


def _rank(kind: str, act: str, holdings) -> int:
    if kind == KIND_UNLOGGED:
        return 10 if act == "SELL" else 30
    if kind == KIND_QTY_MISMATCH:
        return 15 if (holdings or 0) > 0 else 35
    if kind == KIND_JOURNAL_ONLY:
        return 20 if act == "BUY" else 40
    return _RANK[kind]


def _one_click(ctx, kind, jrows, ffills):
    """(correction_shape, eligible, block, requires_fill_date_check). A
    pre-filter only: the Phase-2 correction planner re-validates."""
    has_txn = all(f["txn"] for f in ffills) and bool(ffills)
    if kind == KIND_LINK:
        return ("link", has_txn, None if has_txn else BLOCK_NO_TXN_ID, False)
    if kind in (KIND_REDATE, KIND_SPLIT):
        shape = "redate" if kind == KIND_REDATE else "split"
        earliest = min(f["session"] for f in ffills)
        if not has_txn:
            return (shape, False, BLOCK_NO_TXN_ID, True)
        if earliest < ctx.repair_cutoff:
            return (shape, False, BLOCK_OUTSIDE_REPAIR, True)
        return (shape, True, None, True)
    if kind == KIND_PRICE_FIX:
        shape = "price" if len(ffills) == 1 else "split_same_day"
        return (shape, has_txn, None if has_txn else BLOCK_NO_TXN_ID, False)
    if kind == KIND_RECONCILED:
        return (None, False, BLOCK_MULTI_FILL, False)
    if kind == KIND_FORWARD:
        return (None, False, BLOCK_FORWARD, False)
    if kind == KIND_MERGED_FILLS:
        later = any(f["session"] > jrows[0]["date"] for f in ffills)
        return ("split", False, BLOCK_FORWARD if later else BLOCK_DISPLAY_ONLY, True)
    return (None, False, BLOCK_DISPLAY_ONLY, False)


def _finding(ctx, kind, tk, act, jrows, ffills, text, *, holdings=None,
             nearby=(), dup=None):
    jrows = sorted(jrows, key=lambda j: (j["id"] if j["id"] is not None else math.inf))
    ffills = sorted(ffills, key=lambda f: f["sk"])
    shape, eligible, block, need_check = _one_click(ctx, kind, jrows, ffills)
    jok = [j for j in jrows if j["ok"]]
    fok = [f for f in ffills if f["ok"]]
    jsh = sum(j["shares"] for j in jok) if jok else None
    jpx = _vwap(jok) if jok else None
    bsh = sum(f["shares"] for f in fok) if fok else None
    bpx = _vwap(fok) if fok else None
    return {
        "kind": kind, "severity": _SEVERITY[kind], "rank": _rank(kind, act, holdings),
        "ticker": tk, "action": act,
        "journal_ids": [j["id"] for j in jrows if j["id"] is not None],
        "journal_dates": [j["date"] for j in jrows if j["id"] is not None],
        "txn_ids": [f["txn"] or "" for f in ffills],
        "pending_ids": [f["pid"] for f in ffills],
        "fill_dates": [f["session"] for f in ffills],
        "placed_dates": [f["placed"] for f in ffills],
        "fill_statuses": [f["status"] for f in ffills],
        "journal_shares": jsh, "journal_price": jpx,
        "broker_shares": bsh, "broker_price": bpx,
        "holdings_effect": holdings,
        "correction_shape": shape, "one_click_eligible": bool(eligible),
        "one_click_block": block, "requires_fill_date_check": bool(need_check),
        "nearby_journal_ids": sorted(nearby), "possible_duplicate_of": dup,
        "text": text,
    }


def _sort_key(f):
    ds = [d for d in f["journal_dates"] + f["fill_dates"] if d is not None]
    newest = max(ds).toordinal() if ds else 0
    return (f["rank"], -newest, f["ticker"] or "", f["action"] or "",
            f["journal_ids"][0] if f["journal_ids"] else math.inf,
            f["txn_ids"][0] if f["txn_ids"] else "")


def _in_window(f, start) -> bool:
    ds = [d for d in f["journal_dates"] + f["fill_dates"] if d is not None]
    return not ds or max(ds) >= start


# ── per-group matching ───────────────────────────────────────────────────────

class _Ctx:
    """The fixed inputs of one run (windows and the sync date)."""

    def __init__(self, today, sync_et):
        self.today = today
        self.start = today - timedelta(days=int(JOURNAL_MATCH_LOOKBACK_DAYS))
        self.pool_start = _tec.fills_since(self.start)
        self.repair_cutoff = today - timedelta(days=int(ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS))
        self.sync_et = sync_et
        self.sync_date = sync_et.date() if sync_et is not None else None


def _dist(j, f) -> int:
    return _tec._sessions_between(j["date"], f["session"])


def _classify(j, S, whole_day: bool):
    """(tier, kind) for the fill set S read as ONE journal row `j`, or None."""
    sh = sum(f["shares"] for f in S)
    v = _vwap(S)
    sh_ok = _tec._same_shares(sh, j["shares"])
    px_ok = _tec._same_price(v, j["price"])
    same_day = all(f["session"] == j["date"] for f in S)
    if sh_ok and px_ok:
        if same_day:
            return (1, KIND_RECONCILED) if len(S) >= 2 else None
        if any(f["session"] > j["date"] for f in S):
            return (2, KIND_FORWARD)
        return (2, KIND_REDATE if len(S) == 1 else KIND_SPLIT)
    if sh_ok:
        if same_day:
            return (2, KIND_PRICE_FIX)      # the price is what differs
        # Other days: the row merged fills of several days at a price that isn't their
        # average. A share-count match alone is a coincidence that could explain away a
        # real missing trade, so the set must be ANCHORED: at least one fill at the
        # row's own price (which also rules out a single fill: one anchored fill with
        # the row's shares is already REDATE / FORWARD).
        anchored = any(_tec._same_price(f["price"], j["price"]) for f in S)
        return (3, KIND_MERGED_FILLS) if anchored else None
    if same_day and px_ok and (len(S) == 1 or whole_day):
        return (4, KIND_QTY_MISMATCH)
    return None


def _signature(kind, S):
    return (kind, tuple(sorted((f["session"], round(f["shares"], 6), round(f["price"], 6))
                               for f in S)))


def _explain(j, F):
    """Everything the remaining fills `F` could say about journal row `j`.
    -> {"C": candidates, "E": [(tier, kind, fills, signature)], "too_many": bool}."""
    C = [f for f in F if _dist(j, f) <= JOURNAL_MATCH_WINDOW_SESSIONS]
    Cd = [f for f in C if f["session"] == j["date"]]
    cd_keys = {f["k"] for f in Cd}
    E: list = []
    seen: set = set()

    def add(S, whole_day):
        key = tuple(f["k"] for f in S)
        if key in seen:
            return
        seen.add(key)
        c = _classify(j, S, whole_day)
        if c is not None:
            E.append((c[0], c[1], tuple(S), _signature(c[1], S)))

    if len(Cd) >= 2:
        add(Cd, True)       # a whole day's fills are always tried, whatever their number
    searched = len(C) <= JOURNAL_MATCH_MAX_CANDIDATES
    if searched:
        top = min(len(C), int(JOURNAL_MATCH_MAX_FILLS))
        for n in range(1, top + 1):
            for S in itertools.combinations(C, n):
                add(S, len(S) >= 2 and {f["k"] for f in S} == cd_keys)
    has_whole_t1 = any(e[0] == 1 for e in E)
    too_many = False
    if len(C) > JOURNAL_MATCH_MAX_CANDIDATES and not has_whole_t1:
        too_many = True
    elif (not any(e[0] <= 2 for e in E) and len(C) > JOURNAL_MATCH_MAX_FILLS
          and sum(f["shares"] for f in C) >= j["shares"] - BROKER_DRIFT_SHARE_TOL):
        too_many = True
    return {"C": C, "E": E, "too_many": too_many}


def _best_tier(inf) -> "int | None":
    if inf["too_many"]:
        return 1
    return min((e[0] for e in inf["E"]), default=None)


def _claims(inf, t) -> set:
    if inf["too_many"]:
        return {f["k"] for f in inf["C"]}
    return {f["k"] for e in inf["E"] if e[0] <= t for f in e[2]}


def _group_findings(ctx, tk, act, jrows, fills, linked_rows, diff_rows=()):
    """Findings for one (ticker, action) group: `jrows` = readable unlinked pool
    journal rows, `fills` = readable unlinked pool equity fills, `linked_rows` =
    the group's readable linked journal rows (duplicate hints only), `diff_rows` =
    those linked rows whose LINKED_DIFFERS differs on shares (a "check before
    logging" reference on a nearby UNLOGGED, never a verdict here)."""
    out: list = []
    matched: list = []              # rows that got a LINK / RECONCILED verdict
    F = sorted(fills, key=lambda f: f["sk"])
    rows = sorted(jrows, key=lambda j: (j["ts"], j["id"]), reverse=True)

    # Step 2: LINK, greedy, newest journal row first (the Tier-2 key of
    # broker_sync.classify_transactions).
    left: list = []
    for j in rows:
        cands = [f for f in F if f["session"] == j["date"]
                 and _tec._same_shares(f["shares"], j["shares"])
                 and _tec._same_price(f["price"], j["price"])]
        if not cands:
            left.append(j)
            continue
        f = min(cands, key=lambda f: (abs(f["price"] - j["price"]), f["txn"] or "",
                                      f["sk"][2]))
        F = [x for x in F if x is not f]
        matched.append(j)
        out.append(_finding(
            ctx, KIND_LINK, tk, act, [j], [f],
            f"Your journal's {act} {_sh(j['shares'])} {tk} on {_fd(j['date'])} "
            f"(#{j['id']}) matches Robinhood's record of this order but isn't linked "
            "to it yet."))
    rows = left

    # Step 3: the explanation loop.
    while rows:
        info = {j["id"]: _explain(j, F) for j in rows}
        progressed = False
        for t in (1, 2, 3, 4):
            res = []
            for j in rows:
                inf = info[j["id"]]
                if inf["too_many"] or _best_tier(inf) != t:
                    continue
                Et = [e for e in inf["E"] if e[0] == t]
                if len({e[3] for e in Et}) != 1:
                    continue
                chosen = min(Et, key=lambda e: tuple(f["sk"] for f in sorted(e[2], key=lambda f: f["sk"])))
                mine = {f["k"] for f in chosen[2]}
                if any(mine & _claims(info[k["id"]], t) for k in rows if k is not j):
                    continue
                res.append((j, chosen))
            if res:
                for j, chosen in res:
                    out.append(_resolved(ctx, tk, act, j, chosen))
                    if chosen[1] == KIND_RECONCILED:
                        matched.append(j)
                done_rows = {j["id"] for j, _ in res}
                done_fills = {f["k"] for _, c in res for f in c[2]}
                rows = [j for j in rows if j["id"] not in done_rows]
                F = [f for f in F if f["k"] not in done_fills]
                progressed = True
                break
        if not progressed:
            break

    def _net(f, extra):
        return [(j, only) for j, only in extra + [(d, False) for d in diff_rows]
                if _dist(j, f) <= JOURNAL_MATCH_WINDOW_SESSIONS]

    if not rows:
        for f in F:
            out.append(_unlogged(ctx, tk, act, f, _net(f, [])))
        return out

    # Step 4: leftovers.
    info = {j["id"]: _explain(j, F) for j in rows}
    stuck = [j for j in rows if info[j["id"]]["E"] or info[j["id"]]["too_many"]]
    plain = [j for j in rows if j not in stuck]
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for j in stuck:
        inf = info[j["id"]]
        find(("r", j["id"]))
        for k in (({f["k"] for f in inf["C"]}) if inf["too_many"] else _claims(inf, 4)):
            union(("r", j["id"]), ("f", k))
    comps: dict = {}
    for node in list(parent):
        comps.setdefault(find(node), []).append(node)
    claimed: set = set()
    fill_by_k = {f["k"]: f for f in F}
    for root in sorted(comps, key=lambda r: min((n[1] for n in comps[r] if n[0] == "r"),
                                                default=math.inf)):
        nodes = set(comps[root])
        crow = [j for j in stuck if ("r", j["id"]) in nodes]
        cfill = [fill_by_k[n[1]] for n in nodes if n[0] == "f"]
        if not crow:
            continue
        claimed |= {f["k"] for f in cfill}
        out.append(_component(ctx, tk, act, crow, cfill, info))

    unclaimed = [f for f in F if f["k"] not in claimed]
    judged_rows = [j for j in plain if _judged(j["date"], ctx.sync_date)]
    not_yet = [j for j in plain if j not in judged_rows]
    absorbed: dict = {j["id"]: [] for j in not_yet}
    still: list = []
    for f in unclaimed:
        near = [j for j in not_yet if _dist(j, f) <= JOURNAL_MATCH_WINDOW_SESSIONS]
        if near:
            best = min(near, key=lambda j: (_dist(j, f), j["id"]))
            absorbed[best["id"]].append(f)
        else:
            still.append(f)
    for j in not_yet:
        out.append(_not_yet(ctx, tk, act, j, absorbed[j["id"]]))
    match_pool = linked_rows + matched
    for j in judged_rows:
        dups = [m["id"] for m in match_pool
                if m["id"] != j["id"] and m["date"] == j["date"]
                and _tec._same_shares(m["shares"], j["shares"])
                and _tec._same_price(m["price"], j["price"])]
        dup = min(dups) if dups else None
        text = (f"Your journal's {act} {_sh(j['shares'])} {tk} at {_px(j['price'])} on "
                f"{_fd(j['date'])} (#{j['id']}) matches no Robinhood record.")
        if dup is not None:
            text += (f" #{dup} has the same trade that day and does match Robinhood: "
                     "this row may have been logged twice.")
        jsh = j["shares"]
        out.append(_finding(ctx, KIND_JOURNAL_ONLY, tk, act, [j], [], text,
                            holdings=_holdings(act, jsh, 0.0), dup=dup))
    # Rows whose verdict leaves them not fully matched. NOT_YET_COMPARED rows are not
    # listed: they absorb every unclaimed fill within the window, so no UNLOGGED fill
    # is ever near one.
    refs = [(j, True) for j in judged_rows] + [(j, False) for j in stuck]
    for f in still:
        out.append(_unlogged(ctx, tk, act, f, _net(f, refs)))
    return out


def _resolved(ctx, tk, act, j, chosen):
    tier, kind, S, _sig = chosen
    S = sorted(S, key=lambda f: f["sk"])
    jsh, jpx = j["shares"], j["price"]
    bsh, bpx = sum(f["shares"] for f in S), _vwap(S)
    desc = f"{act} {_sh(jsh)} {tk}"
    jid = f"#{j['id']}"
    dj = _fd(j["date"])
    holdings = 0.0
    if kind == KIND_RECONCILED:
        text = (f"Your journal's {desc} on {dj} ({jid}) is Robinhood's {len(S)} fills "
                f"that day combined (average {_px(bpx)}).")
    elif kind == KIND_REDATE:
        f = S[0]
        text = (f"Robinhood's record of {act} {_sh(bsh)} {tk} at {_px(f['price'])} is dated "
                f"{_fd(f['placed'])}")
        if f["placed_at"] is not None:
            text += f", placed {_tec._fmt_dt_clock(f['placed_at'])} ET"
        text += (f", the day the order was placed; your journal ({jid}) has it on {dj}. "
                 f"Check the 'Filled' date in Robinhood: if it filled on {_fd(f['session'])}, "
                 f"the journal date is wrong; if the order waited and filled on {dj}, the "
                 "journal is right.")
        if f["placed"] != f["session"]:
            text += (f" (placed {_fd(f['placed'])}, so it filled {_fd(f['session'])} or "
                     "later)")
    elif kind == KIND_FORWARD:
        g = max(S, key=lambda x: x["sk"])
        text = (f"Your journal ({jid}) has {desc} at {_px(jpx)} on {dj}, but Robinhood's "
                f"record of this order is dated {_fd(g['placed'])}, the day it was placed, "
                f"so it can't have filled on {dj}.")
        if g["placed"] != g["session"]:
            text += (f" (placed {_fd(g['placed'])}, so it filled {_fd(g['session'])} or "
                     "later)")
        text += " Check the 'Filled' date in Robinhood."
    elif kind == KIND_SPLIT:
        dates = ", ".join(_fd(d) for d in sorted({f["session"] for f in S}))
        text = (f"Your journal's {desc} at {_px(jpx)} on {dj} ({jid}) matches {len(S)} "
                f"Robinhood fills dated {dates}, average {_px(bpx)}. Robinhood dates each "
                "fill by when its order was placed; check the 'Filled' dates.")
    elif kind == KIND_MERGED_FILLS:
        dates = ", ".join(_fd(d) for d in sorted({f["placed"] for f in S}))
        n = len(S)
        text = (f"Your journal's {desc} at {_px(jpx)} on {dj} ({jid}) matches {n} Robinhood "
                f"{'fill' if n == 1 else 'fills'} dated {dates} totalling {_shares_text(bsh)} "
                f"at an average of {_px(bpx)}, not {_px(jpx)}.")
        for pl, se in sorted({(f["placed"], f["session"]) for f in S if f["placed"] != f["session"]}):
            text += f" (placed {_fd(pl)}, so it filled {_fd(se)} or later)"
        text += (" Robinhood dates each fill by when its order was placed; check the "
                 "'Filled' dates. If these were separate trades, a Robinhood fill is "
                 "missing from your journal.")
    elif kind == KIND_PRICE_FIX:
        if len(S) == 1:
            text = (f"Robinhood's record of {desc} on {dj} is at {_px(bpx)}, not "
                    f"{_px(jpx)} ({jid}).")
        else:
            text = (f"Robinhood's {len(S)} fills of {desc} on {dj} average {_px(bpx)}, "
                    f"not {_px(jpx)} ({jid}).")
    else:   # QTY_MISMATCH
        holdings = _holdings(act, jsh, bsh)
        more = "more" if holdings > 0 else "fewer"
        if len(S) == 1:
            lead = (f"Robinhood's record of {tk} {act} on {dj} at {_px(bpx)} is "
                    f"{_shares_text(bsh)}")
        else:
            lead = (f"Robinhood's {len(S)} fills of {tk} {act} on {dj} (average "
                    f"{_px(bpx)}) total {_shares_text(bsh)}")
        gap = _sh(abs(holdings))
        text = (f"{lead}; your journal ({jid}) has {_shares_text(jsh)}, so it counts "
                f"{gap} {more} {tk} {'share' if gap == '1' else 'shares'}.")
    return _finding(ctx, kind, tk, act, [j], S, text,
                    holdings=holdings if kind != KIND_RECONCILED else None)


def _near_sentence(rows, act, f, tail_one, tail_many):
    if not rows:
        return ""
    if len(rows) == 1:
        j = rows[0]
        return (f" Journal row #{j['id']} ({act} {_sh(j['shares'])} on {_fd(j['date'])}) "
                f"{tail_one}: check it before logging this one.")
    parts = ", ".join(f"#{j['id']} ({act} {_sh(j['shares'])} on {_fd(j['date'])})" for j in rows)
    return f" Journal rows {parts} {tail_many}: check them before logging this one."


def _unlogged(ctx, tk, act, f, near_rows):
    """`near_rows` = [(journal row, matches_no_fill)] within the window of this
    fill's session whose own verdict leaves them not fully matched: a JOURNAL_ONLY
    row (True), or a row in an AMBIGUOUS / TOO_MANY component or a linked row that
    differs on shares (False). References only: never a second verdict for the row."""
    near = sorted(near_rows, key=lambda t: (_dist(t[0], f), t[0]["id"]))
    verb = "bought" if act == "BUY" else "sold"
    text = (f"Robinhood {verb} {_sh(f['shares'])} {tk} on {_fd(f['session'])} at "
            f"{_px(f['price'])}, and no journal row matches it.")
    if f["status"] == "dismissed":
        text += " You marked it 'Already logged' in Pending Imports."
    elif f["status"] == "logged":
        text += (" Pending Imports shows it as logged, but no journal row carries it "
                 "(the row may have been deleted).")
    if act == "SELL":
        text += " Your journal still counts these shares."
    text += _near_sentence([j for j, only in near if only], act, f,
                           "is nearby and matches no fill", "are nearby and match no fill")
    text += _near_sentence([j for j, only in near if not only], act, f,
                           "is nearby and isn't fully matched to Robinhood",
                           "are nearby and aren't fully matched to Robinhood")
    return _finding(ctx, KIND_UNLOGGED, tk, act, [], [f], text,
                    holdings=_holdings(act, 0.0, f["shares"]),
                    nearby=sorted({j["id"] for j, _ in near}))


def _not_yet(ctx, tk, act, j, absorbed):
    if ctx.sync_et is None:
        why = "the time of the last completed broker sync couldn't be read."
    else:
        why = f"the last completed sync was {_sync_when(ctx.sync_et)}."
    text = (f"Your journal's {act} {_sh(j['shares'])} {tk} on {_fd(j['date'])} "
            f"(#{j['id']}) isn't compared with Robinhood yet: {why}")
    return _finding(ctx, KIND_NOT_YET_COMPARED, tk, act, [j], absorbed, text)


def _component(ctx, tk, act, crow, cfill, info):
    crow = sorted(crow, key=lambda j: j["id"])
    many = [j for j in crow if info[j["id"]]["too_many"]]
    if many:
        j = many[0]
        n = len(info[j["id"]]["C"])
        text = (f"Too many Robinhood {tk} {act} fills near {_fd(j['date'])} ({n}) to "
                f"compare with journal row #{j['id']} automatically.")
        return _finding(ctx, KIND_TOO_MANY, tk, act, crow, cfill, text)
    ids = ", ".join(f"#{j['id']}" for j in crow)
    dates = ", ".join(_fd(d) for d in sorted({f["session"] for f in cfill}))
    text = (f"Your journal's {tk} {act} rows {ids} and Robinhood's records dated {dates} "
            "could match in more than one way; check them in Robinhood.")
    return _finding(ctx, KIND_AMBIGUOUS, tk, act, crow, cfill, text)


def _unchecked(ctx, tk, act, jrows, fills):
    bad_j = [j for j in jrows if not j["ok"]]
    bad_f = [f for f in fills if not f["ok"]]
    if bad_j:
        j = bad_j[0]
        who = f"journal row #{j['id']}" if j["id"] is not None else "a journal row"
    else:
        f = bad_f[0]
        who = f"Robinhood record {f['txn']}" if f["txn"] else "a Robinhood record"
    text = (f"Couldn't compare {tk} {act} with Robinhood: {who} has an unreadable "
            "date, share count or price.")
    return _finding(ctx, KIND_UNCHECKED, tk, act, jrows, fills, text)


# ── public matcher ───────────────────────────────────────────────────────────

def match_journal_to_broker(journal, fills, synced_at, now_et) -> "list | None":
    """The whole journal against every Robinhood fill: one verdict per unlinked
    journal row and per fill. Read-only; see the module docstring.

    `journal` = `db.load_trades_or_none()`, `fills` = `db.load_snaptrade_fills_or_none(
    fills_read_since(now_et).isoformat())` (any status), `synced_at` =
    `db.load_snaptrade_txn_synced_at_or_none()`, `now_et` a tz-aware datetime
    (ValueError otherwise). Returns `None` (couldn't check: an unreadable journal
    or fills input), `[]` (checked, nothing to say) or findings, each a dict with
    `kind`, `severity` ("differs" | "unresolved" | "unchecked" | "info"), `rank`
    (lower = more urgent), `ticker`, `action`, `journal_ids` + `journal_dates`,
    `txn_ids` + `pending_ids` / `fill_dates` (the ET session) / `placed_dates` /
    `fill_statuses` (parallel), `journal_shares`, `journal_price`, `broker_shares`,
    `broker_price`, `holdings_effect` (journal-over shares; positive = the journal
    overstates holdings; None for unresolved / unchecked / info), `correction_shape`,
    `one_click_eligible`, `one_click_block`, `requires_fill_date_check`,
    `nearby_journal_ids`, `possible_duplicate_of`, `text` (plain; the caller escapes).

    Sorted by (rank, newest first, ticker, ...). Every unlinked in-window journal
    row and every in-window equity fill appears in exactly one finding.
    """
    today = _tec._aware_et(now_et).date()
    if journal is None or not isinstance(journal, pd.DataFrame):
        return None
    if fills is None or not isinstance(fills, (list, tuple)):
        return None
    if len(journal) > 0 and any(c not in journal.columns for c in _JOURNAL_COLUMNS):
        return None
    fl = _parse_fills(fills)
    if fl is None:
        return None
    ctx = _Ctx(today, _sync_et(synced_at))
    linked_ids, jrecs = _parse_journal(journal)

    findings: list = []

    # Journal rows: pool (unlinked, in the pool window) and the linked index.
    by_txn: dict = {}
    pool_j: dict = {}
    linked_rows: dict = {}
    for j in sorted(jrecs, key=lambda j: (j["id"] if j["id"] is not None else math.inf)):
        if j["linked"]:
            if j["ok"]:
                by_txn.setdefault(j["txn"], j)
                linked_rows.setdefault((j["ticker"], j["act"]), []).append(j)
            continue
        if j["date"] is None or j["date"] >= ctx.pool_start:
            pool_j.setdefault((j["ticker"], j["act"]), []).append(j)

    # Fills: non-equity aside, linked ones for Step 0, the rest pooled.
    pool_f: dict = {}
    nonequity: list = []
    linked_f: list = []
    for f in fl:
        if f["nonequity"]:
            if f["session"] is not None and f["session"] >= ctx.start:
                nonequity.append(f)
            continue
        if f["txn"] is not None and f["txn"] in linked_ids:
            linked_f.append(f)
            continue
        if f["session"] is None or f["session"] >= ctx.pool_start:
            pool_f.setdefault((f["ticker"], f["act"]), []).append(f)

    # Step 0: LINKED_DIFFERS.
    share_diff: dict = {}
    for f in linked_f:
        if not f["ok"] or f["session"] < ctx.start:
            continue
        j = by_txn.get(f["txn"])
        if j is None:
            continue
        diffs = []
        if j["ticker"] != f["ticker"]:
            diffs.append(f"ticker {j['ticker']} in the journal, {f['ticker']} in Robinhood")
        if j["act"] != f["act"]:
            diffs.append(f"action {j['act']} in the journal, {f['act']} in Robinhood")
        if not _tec._same_shares(j["shares"], f["shares"]):
            diffs.append(f"share count {_sh(j['shares'])} in the journal, {_sh(f['shares'])} "
                         "in Robinhood")
        if not _tec._same_price(j["price"], f["price"]):
            diffs.append(f"price {_px(j['price'])} in the journal, {_px(f['price'])} "
                         "in Robinhood")
        if j["date"] not in (f["placed"], f["session"]):
            diffs.append(f"date {_fd(j['date'])} in the journal, {_fd(f['session'])} in "
                         "Robinhood")
        if not diffs:
            continue
        if not _tec._same_shares(j["shares"], f["shares"]):
            share_diff.setdefault((j["ticker"], j["act"]), []).append(j)
        text = (f"Journal row #{j['id']} is linked to Robinhood's record of this order, "
                f"but they differ: {'; '.join(diffs)}.")
        findings.append(_finding(
            ctx, KIND_LINKED_DIFFERS, j["ticker"], j["act"], [j], [f], text,
            holdings=_holdings(j["act"], j["shares"], f["shares"])))

    # Steps 1 to 4, per group.
    for key in sorted(set(pool_j) | set(pool_f)):
        tk, act = key
        jrows = pool_j.get(key, [])
        frows = pool_f.get(key, [])
        if any(not j["ok"] for j in jrows) or any(not f["ok"] for f in frows):
            findings.append(_unchecked(ctx, tk, act, jrows, frows))
            continue
        findings.extend(_group_findings(ctx, tk, act, jrows, frows,
                                        linked_rows.get(key, []), share_diff.get(key, [])))

    # Step 5: non-equity records.
    if nonequity:
        n = len(nonequity)
        text = (f"Robinhood records {n} look like option trades and weren't compared "
                "(the journal tracks stocks and ETFs).")
        findings.append(_finding(ctx, KIND_SKIPPED_NON_EQUITY, "", "", [], nonequity, text))

    findings = [f for f in findings if _in_window(f, ctx.start)]
    findings.sort(key=_sort_key)
    return findings
