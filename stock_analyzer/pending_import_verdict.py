"""TJDate U9a: what 📥 Pending Imports tells the owner about each pending broker
fill (docs/plans/pending-imports-verdicts-u9.md).

PURE: no streamlit, no db, no `stock_analyzer.data` import and no clock read
(`now_et` is passed in). It turns the strict pending list plus one run of the C5
matcher (`journal_broker_match.match_journal_to_broker`) into one verdict per
pending row and decides which of the two buttons may act:

  * "Log This Trade" is DISABLED when the fill is already in the journal (a hand
    log would count it twice), needs a tick when a journal row could already be
    this fill, and is the primary action when the fill is not in the journal.
  * "Already logged" is the primary action when the fill is already in the
    journal, and needs a tick when the matcher says the fill is NOT in the
    journal (the 172-dismissal failure: advice from a share-count match).

Three-state, like every cache here: a pending list that couldn't be read is
"offline" (never "No pending imports"), an empty list is "empty" (the matcher is
not run), and when the journal, the fills or the comparison can't be had the rows
read "Not compared" with plain buttons and no verdict wording. An exception in the
matcher never becomes a verdict.

Twin rule (the 2026-08-30 DCA-twin class): two identical fills (same ticker,
action, session, shares and price) with one journal row between them read, by the
matcher, as one LINK and one UNLOGGED. When the UNLOGGED copy was already
dismissed, the pending LINK copy would read "already in your journal" while one of
the two isn't, so such a row flips to "not in your journal" (at most one flip per
dismissed unlogged twin; never for a pending twin, whose own row is visible).
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from stock_analyzer import journal_broker_match as _jbm
from stock_analyzer import trade_entry_checks as _tec
from stock_analyzer.constants import JOURNAL_MATCH_LOOKBACK_DAYS

# ── verdicts ─────────────────────────────────────────────────────────────────
V_IN_JOURNAL = "IN_JOURNAL"
V_DIFFERS = "DIFFERS"
V_NOT_IN_JOURNAL = "NOT_IN_JOURNAL"
V_UNCLEAR = "UNCLEAR"
V_NOT_YET = "NOT_YET"
V_UNCHECKED = "UNCHECKED"
V_OPTION = "OPTION"
V_OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
V_NOT_COMPARED = "NOT_COMPARED"

# Every matcher kind has an explicit verdict (a test pins the mapping over
# `journal_broker_match._SEVERITY`, so a new matcher kind fails until it is
# placed here). JOURNAL_ONLY has no Robinhood fill, so it can't reach a pending
# row; it is listed so the mapping is total, and reads as UNCLEAR if it ever does.
# A kind missing from this table is treated like UNCLEAR with both ticks.
_VERDICT_OF_KIND = {
    _jbm.KIND_LINK: V_IN_JOURNAL,
    _jbm.KIND_RECONCILED: V_IN_JOURNAL,
    _jbm.KIND_REDATE: V_DIFFERS,
    _jbm.KIND_SPLIT: V_DIFFERS,
    _jbm.KIND_FORWARD: V_DIFFERS,
    _jbm.KIND_PRICE_FIX: V_DIFFERS,
    _jbm.KIND_MERGED_FILLS: V_DIFFERS,
    _jbm.KIND_QTY_MISMATCH: V_DIFFERS,
    _jbm.KIND_LINKED_DIFFERS: V_DIFFERS,
    _jbm.KIND_UNLOGGED: V_NOT_IN_JOURNAL,
    _jbm.KIND_AMBIGUOUS: V_UNCLEAR,
    _jbm.KIND_TOO_MANY: V_UNCLEAR,
    _jbm.KIND_JOURNAL_ONLY: V_UNCLEAR,
    _jbm.KIND_NOT_YET_COMPARED: V_NOT_YET,
    _jbm.KIND_UNCHECKED: V_UNCHECKED,
    _jbm.KIND_SKIPPED_NON_EQUITY: V_OPTION,
}

# UNCLEAR kinds whose "Already logged" stays plain; any other kind that lands in
# UNCLEAR (JOURNAL_ONLY, an unknown kind) needs the dismiss tick as well.
_UNCLEAR_PLAIN_DISMISS = (_jbm.KIND_AMBIGUOUS, _jbm.KIND_TOO_MANY)

_TONE = {
    V_IN_JOURNAL: "ok", V_DIFFERS: "warning", V_NOT_IN_JOURNAL: "warning",
    V_UNCLEAR: "neutral", V_NOT_YET: "neutral", V_UNCHECKED: "neutral",
    V_OPTION: "neutral", V_OUTSIDE_WINDOW: "neutral", V_NOT_COMPARED: "neutral",
}

_LABEL = {
    V_IN_JOURNAL: "✅ Already in your journal",
    V_DIFFERS: "⚠️ In your journal, but the details differ",
    V_NOT_IN_JOURNAL: "🆕 Not in your journal",
    V_UNCLEAR: "❓ Can't tell if it's in your journal",
    V_NOT_YET: "⏳ May be in your journal (not compared yet)",
    V_UNCHECKED: "❔ Couldn't be compared",
    V_OPTION: "ℹ️ Option trade, not tracked",
    V_OUTSIDE_WINDOW: "ℹ️ Older than the comparison window",
    V_NOT_COMPARED: "❔ Not compared",
}

_SUMMARY_WORDS = {
    V_IN_JOURNAL: "already in your journal",
    V_DIFFERS: "in your journal with differences",
    V_NOT_IN_JOURNAL: "not in your journal",
    V_UNCLEAR: "unclear",
    V_NOT_YET: "not compared yet",
    V_UNCHECKED: "couldn't be compared",
    V_OPTION: "option trade",
    V_OUTSIDE_WINDOW: "older than the comparison window",
    V_NOT_COMPARED: "not compared",
}
_SUMMARY_ORDER = (V_NOT_IN_JOURNAL, V_DIFFERS, V_UNCLEAR, V_NOT_YET, V_UNCHECKED,
                  V_IN_JOURNAL, V_OPTION, V_OUTSIDE_WINDOW, V_NOT_COMPARED)

LIST_OFFLINE_TEXT = "⚠️ Pending imports couldn't be read this render."
DISMISS_NOTE_DIFFERS = "clears this row only; it doesn't change your journal"
DISMISS_TICK_NOT_IN_JOURNAL = ("I've checked: this fill is already in my journal under a row "
                               "the comparison didn't match")
OPTION_DISABLED_REASON = "the journal tracks stocks and ETFs"
TWIN_TEXT = ("Robinhood has another identical fill (same day, shares and price) that no "
             "journal row matches, so one of the two isn't in your journal: logging this "
             "one covers it.")
NOT_YET_TAIL = " It may be this fill: check it before logging this one."
UNCHECKED_TAIL = " Check it in Robinhood and 📋 History before using either button."
OFFLINE_ROW_TEXT = "the comparison with your journal didn't run this render; see the note above."
OPTION_TEXT = ("This looks like an option trade, which the journal doesn't track (it tracks "
               "stocks and ETFs).")
NOT_COMPARED_TEXT = ("This fill wasn't part of this render's comparison with your journal, "
                     "so no verdict is shown.")
UNREADABLE_DATE_TEXT = ("This fill's date couldn't be read, so it wasn't compared with your "
                        "journal.")


# ── small helpers ────────────────────────────────────────────────────────────

def _ids_text(ids) -> str:
    return ", ".join(f"#{i}" for i in ids)


def _list(v) -> list:
    return list(v) if isinstance(v, (list, tuple)) else []


def _upper(v) -> str:
    return (_tec._txn_text(v) or "").upper()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _check_label(ids) -> str:
    if ids:
        return f"I've checked {_ids_text(ids)}: this fill is a separate trade"
    return "I've checked my journal: this fill is a separate trade"


def _in_journal_reason(ids) -> str:
    who = f" ({_ids_text(ids)})" if ids else ""
    return f"this fill is already in your journal{who}, so logging it would count it twice"


def _facts(rec) -> "dict | None":
    """Ticker, action, ET session, shares and price of one fill record (a pending
    row or a fills-read row), or None when it isn't a dict. The price is the exact
    one in `raw_json` (the column is rounded to 2 dp), like the matcher reads it;
    any field may be None when unreadable."""
    if not isinstance(rec, dict):
        return None
    placed = _tec._as_date(rec.get("trade_date"))
    session = _jbm._session_on_or_after(placed) if placed is not None else None
    sh = _tec._num(rec.get("shares"))
    sh = sh if sh is not None and sh > 0 else None
    px = _tec._raw_price(rec.get("raw_json"))
    if px is None:
        px = _tec._num(rec.get("price"))
        px = px if px is not None and px > 0 else None
    return {"ticker": _upper(rec.get("ticker")), "act": _upper(rec.get("action")),
            "placed": placed, "session": session, "shares": sh, "price": px}


def _headline(facts: dict) -> str:
    """'BUY 3 CSCO at 102.00, placed Wed Nov 4' (no currency symbol); parts that
    can't be read are left out."""
    head = " ".join(p for p in (
        facts["act"], _tec._fmt_shares(facts["shares"]) if facts["shares"] is not None else "",
        facts["ticker"]) if p)
    if not head:
        head = "Robinhood fill"
    if facts["price"] is not None:
        head += f" at {_tec._fmt_price(facts['price'])}"
    if facts["placed"] is not None:
        head += f", placed {_tec._fmt_date(facts['placed'])}"
    return head


def _same_fill(a: dict, b: dict) -> bool:
    """Interchangeable fills: same ticker, action and session, and the same shares
    and price within the matcher's own tolerances."""
    if any(x is None for x in (a["session"], a["shares"], a["price"],
                               b["session"], b["shares"], b["price"])):
        return False
    return (a["ticker"] == b["ticker"] and a["act"] == b["act"] and a["session"] == b["session"]
            and _tec._same_shares(a["shares"], b["shares"])
            and _tec._same_price(a["price"], b["price"]))


# ── one verdict mapping ──────────────────────────────────────────────────────

def _mapping(verdict, kind, reason, text, **over) -> dict:
    m = {
        "verdict": verdict, "kind": kind, "reason": reason, "tone": _TONE[verdict],
        "label": _LABEL[verdict], "text": text, "journal_ids": [], "nearby_journal_ids": [],
        "log_enabled": True, "log_primary": False, "log_needs_tick": False,
        "log_tick_label": "", "log_disabled_reason": "",
        "dismiss_primary": False, "dismiss_needs_tick": False, "dismiss_tick_label": "",
        "dismiss_note": "",
    }
    m.update(over)
    return m


def _not_in_journal(kind, reason, text, jids, nearby) -> dict:
    return _mapping(
        V_NOT_IN_JOURNAL, kind, reason, text, journal_ids=jids, nearby_journal_ids=nearby,
        log_primary=True, log_needs_tick=bool(nearby),
        log_tick_label=_check_label(nearby) if nearby else "",
        dismiss_needs_tick=True, dismiss_tick_label=DISMISS_TICK_NOT_IN_JOURNAL)


def _from_finding(f: dict) -> dict:
    """The mapping for one matcher finding that names this fill."""
    kind = f.get("kind")
    verdict = _VERDICT_OF_KIND.get(kind, V_UNCLEAR)
    known = kind in _VERDICT_OF_KIND
    reason = f"matcher:{kind}" if known else f"unknown_kind:{kind}"
    text = str(f.get("text") or "").strip()
    jids = _list(f.get("journal_ids"))
    nearby = _list(f.get("nearby_journal_ids"))
    if verdict == V_IN_JOURNAL:
        return _mapping(verdict, kind, reason, text, journal_ids=jids,
                        log_enabled=False, log_disabled_reason=_in_journal_reason(jids),
                        dismiss_primary=True)
    if verdict == V_DIFFERS:
        why = _in_journal_reason(jids)
        if kind == _jbm.KIND_MERGED_FILLS:
            row = _ids_text(jids) if jids else "the journal row"
            why += f". If these were separate trades, {row} needs correcting first."
        return _mapping(verdict, kind, reason, text, journal_ids=jids,
                        log_enabled=False, log_disabled_reason=why,
                        dismiss_primary=True, dismiss_note=DISMISS_NOTE_DIFFERS)
    if verdict == V_NOT_IN_JOURNAL:
        return _not_in_journal(kind, reason, text, jids, nearby)
    if verdict == V_UNCLEAR:
        strict = kind not in _UNCLEAR_PLAIN_DISMISS
        return _mapping(verdict, kind, reason, text, journal_ids=jids,
                        log_needs_tick=True, log_tick_label=_check_label(jids),
                        dismiss_needs_tick=strict,
                        dismiss_tick_label=DISMISS_TICK_NOT_IN_JOURNAL if strict else "")
    if verdict == V_NOT_YET:
        return _mapping(verdict, kind, reason, text + NOT_YET_TAIL, journal_ids=jids,
                        log_needs_tick=True, log_tick_label=_check_label(jids))
    if verdict == V_UNCHECKED:
        return _mapping(verdict, kind, reason, text + UNCHECKED_TAIL, journal_ids=jids)
    # V_OPTION
    return _mapping(verdict, kind, reason, OPTION_TEXT,
                    log_enabled=False, log_disabled_reason=OPTION_DISABLED_REASON,
                    dismiss_primary=True)


def _restrictiveness(m: dict) -> tuple:
    return (not m["log_enabled"], m["log_needs_tick"], m["dismiss_needs_tick"])


def _merge(maps: list) -> dict:
    """One mapping from several. Only reachable if the matcher's one-finding-per-
    fill invariant is ever broken: take the most restrictive mapping, then make
    each flag at least as restrictive as every mapping's (Log disabled if any
    disables it, a tick if any asks for one)."""
    if len(maps) == 1:
        return maps[0]
    base = dict(max(maps, key=_restrictiveness))
    base["log_enabled"] = all(m["log_enabled"] for m in maps)
    base["log_needs_tick"] = any(m["log_needs_tick"] for m in maps)
    base["dismiss_needs_tick"] = any(m["dismiss_needs_tick"] for m in maps)
    if base["log_needs_tick"] and not base["log_tick_label"]:
        base["log_tick_label"] = next((m["log_tick_label"] for m in maps if m["log_tick_label"]),
                                      _check_label([]))
    if base["dismiss_needs_tick"] and not base["dismiss_tick_label"]:
        base["dismiss_tick_label"] = DISMISS_TICK_NOT_IN_JOURNAL
    if not base["log_enabled"] and not base["log_disabled_reason"]:
        base["log_disabled_reason"] = next(
            (m["log_disabled_reason"] for m in maps if m["log_disabled_reason"]),
            _in_journal_reason([]))
    return base


# ── the decision ─────────────────────────────────────────────────────────────

def _result(list_state, list_text, compare_state="skipped", compare_text="",
            coverage_text="", summary_text="", rows=None) -> dict:
    return {"list_state": list_state, "list_text": list_text, "compare_state": compare_state,
            "compare_text": compare_text, "coverage_text": coverage_text,
            "summary_text": summary_text, "rows": [] if rows is None else rows}


def _offline_banner(journal_bad: bool, fills_bad: bool, ran: bool) -> str:
    tail = " Check each fill in Robinhood and 📋 History before using either button."
    if journal_bad or fills_bad:
        parts = []
        if journal_bad:
            parts.append("your journal")
        if fills_bad:
            parts.append("Robinhood's records")
        return f"⚠️ No verdict this render: {' and '.join(parts)} couldn't be read." + tail
    if not ran:
        return "⚠️ No verdict this render: the comparison couldn't run." + tail
    return ("⚠️ No verdict this render: the comparison couldn't be completed (an input was "
            "unreadable)." + tail)


def _txn_to_journal_id(journal) -> dict:
    """txn text -> the lowest journal row id carrying it (from the same journal read
    the matcher used)."""
    out: dict = {}
    if (not isinstance(journal, pd.DataFrame) or "broker_txn_id" not in journal.columns
            or "id" not in journal.columns):
        return out
    for rid, txn in zip(journal["id"], journal["broker_txn_id"]):
        t = _tec._txn_text(txn)
        n = _tec._num(rid)
        if t is None or n is None:
            continue
        n = int(n)
        if t not in out or n < out[t]:
            out[t] = n
    return out


def _index_findings(findings: list) -> tuple:
    by_txn: dict = {}
    by_pid: dict = {}
    for f in findings:
        for t in _list(f.get("txn_ids")):
            t = _tec._txn_text(t)
            if t is not None:
                by_txn.setdefault(t, []).append(f)
        for p in _list(f.get("pending_ids")):
            n = _tec._num(p)
            if n is not None:
                by_pid.setdefault(n, []).append(f)
    return by_txn, by_pid


def _findings_for(txn, pid, by_txn, by_pid) -> list:
    got = list(by_txn.get(txn, [])) if txn is not None else []
    if not got:
        n = _tec._num(pid)
        got = list(by_pid.get(n, [])) if n is not None else []
    seen: set = set()
    out = []
    for f in got:
        if id(f) not in seen:
            seen.add(id(f))
            out.append(f)
    return out


def _fill_lookup(fills) -> tuple:
    by_txn: dict = {}
    by_pid: dict = {}
    for r in fills:
        if not isinstance(r, dict):
            continue
        t = _tec._txn_text(r.get("snaptrade_txn_id"))
        if t is not None:
            by_txn.setdefault(t, r)
        n = _tec._num(r.get("id"))
        if n is not None:
            by_pid.setdefault(n, r)
    return by_txn, by_pid


def _unlogged_twins(findings: list, fill_by_txn: dict, fill_by_pid: dict,
                    pending_txns: set, pending_pids: set) -> list:
    """(txn sort key, facts, finding) for every UNLOGGED finding whose fill is NOT
    pending (dismissed / logged), facts read from the fills read (fall back to the
    finding's own values). A fill is also pending when its txn or id is in the
    pending list of this render, whatever the fills read says (the two reads can
    disagree after a concurrent write)."""
    out = []
    for f in findings:
        if f.get("kind") != _jbm.KIND_UNLOGGED:
            continue
        st = _list(f.get("fill_statuses"))
        txns = _list(f.get("txn_ids"))
        if not st or st[0] == "pending" or len(txns) != 1:
            continue
        txn = _tec._txn_text(txns[0])
        pids = _list(f.get("pending_ids"))
        if txn is not None and txn in pending_txns:
            continue
        if any(_tec._num(p) in pending_pids for p in pids if _tec._num(p) is not None):
            continue
        rec = fill_by_txn.get(txn) if txn is not None else None
        if rec is None and pids:
            n = _tec._num(pids[0])
            rec = fill_by_pid.get(n) if n is not None else None
        facts = _facts(rec)
        if facts is None:
            dates = _list(f.get("fill_dates"))
            facts = {"ticker": _upper(f.get("ticker")), "act": _upper(f.get("action")),
                     "placed": None, "session": dates[0] if dates else None,
                     "shares": f.get("broker_shares"), "price": f.get("broker_price")}
        out.append((txn or "", facts, f))
    out.sort(key=lambda t: t[0])
    return out


def _apply_twin_rule(items: list, findings: list, fill_by_txn: dict, fill_by_pid: dict,
                     pending_txns: set, pending_pids: set, linked: dict) -> None:
    """Flip pending rows read as IN_JOURNAL (a LINK / RECONCILED finding) or DIFFERS
    to NOT_IN_JOURNAL when a dismissed / logged-but-unjournaled UNLOGGED fill is
    interchangeable with them: one flip per such twin, lowest txn text first.
    A row whose own txn is already on a journal row (`linked`: "already linked" and
    LINKED_DIFFERS alike) never flips: logging it again would be refused by the
    trade-confirm hard block, which sends the owner back to "Already logged". The
    flipped row keeps the matcher's own text after the twin sentence. Mutates `items`
    (each `{"row", "facts", "map", "from_finding"}`)."""
    twins = _unlogged_twins(findings, fill_by_txn, fill_by_pid, pending_txns, pending_pids)
    if not twins:
        return
    cands = [it for it in items
             if it["from_finding"] and it["map"]["verdict"] in (V_IN_JOURNAL, V_DIFFERS)
             and it["facts"] is not None
             and not (it["txn"] is not None and it["txn"] in linked)]
    cands.sort(key=lambda it: (it["txn"] or "", str(it["row"].get("id"))))
    flipped: set = set()
    for _txn, tfacts, tfinding in twins:
        for it in cands:
            if id(it) in flipped or not _same_fill(it["facts"], tfacts):
                continue
            flipped.add(id(it))
            old = it["map"]
            text = f"{TWIN_TEXT} {old['text']}" if old["text"] else TWIN_TEXT
            it["map"] = _not_in_journal(old["kind"], "twin", text, old["journal_ids"],
                                        _list(tfinding.get("nearby_journal_ids")))
            break


def _row_map(it: dict, by_txn: dict, by_pid: dict, linked: dict, start) -> dict:
    """The mapping for one pending row (compare ok)."""
    txn, pid, facts = it["txn"], it["row"].get("id"), it["facts"]
    found = _findings_for(txn, pid, by_txn, by_pid)
    if found:
        it["from_finding"] = True
        return _merge([_from_finding(f) for f in found])
    if txn is not None and txn in linked:
        n = linked[txn]
        return _mapping(
            V_IN_JOURNAL, None, "already_linked",
            f"Journal row #{n} is already linked to this Robinhood record.", journal_ids=[n],
            log_enabled=False, log_disabled_reason=_in_journal_reason([n]),
            dismiss_primary=True)
    if facts["session"] is None:
        return _mapping(V_NOT_COMPARED, None, "unreadable_date", UNREADABLE_DATE_TEXT)
    if facts["session"] < start:
        n = int(JOURNAL_MATCH_LOOKBACK_DAYS)
        return _mapping(
            V_OUTSIDE_WINDOW, None, "outside_window",
            f"This fill is older than the {n}-day comparison window, so it wasn't compared "
            "with your journal.")
    return _mapping(V_NOT_COMPARED, None, "no_finding_in_window", NOT_COMPARED_TEXT)


def _finish(it: dict) -> dict:
    m, row = it["map"], it["row"]
    return {
        "pending_id": row.get("id"), "txn_id": it["txn"], "headline": _headline(it["facts"]),
        "verdict": m["verdict"], "kind": m["kind"], "reason": m["reason"], "tone": m["tone"],
        "label": m["label"], "text": m["text"],
        "journal_ids": list(m["journal_ids"]),
        "nearby_journal_ids": list(m["nearby_journal_ids"]),
        "log_enabled": bool(m["log_enabled"]), "log_primary": bool(m["log_primary"]),
        "log_needs_tick": bool(m["log_needs_tick"]), "log_tick_label": m["log_tick_label"],
        "log_disabled_reason": m["log_disabled_reason"],
        "dismiss_primary": bool(m["dismiss_primary"]),
        "dismiss_needs_tick": bool(m["dismiss_needs_tick"]),
        "dismiss_tick_label": m["dismiss_tick_label"], "dismiss_note": m["dismiss_note"],
    }


def _summary(rows: list, compared: bool) -> str:
    n = len(rows)
    if not compared:
        return f"{_plural(n, 'pending import')}, not compared with your journal this render."
    counts: dict = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    parts = [f"{counts[v]} {_SUMMARY_WORDS[v]}" + ("s" if v == V_OPTION and counts[v] != 1 else "")
             for v in _SUMMARY_ORDER if v in counts]
    return f"{_plural(n, 'pending import')}: {', '.join(parts)}."


def decide_pending_imports(pending, journal, fills, synced_at, now_et) -> dict:
    """One verdict per pending Robinhood fill, and what each button may do.

    `pending` = the strict pending list (`db.load_snaptrade_pending_imports_or_none()`)
    or None; `journal` = `db.load_trades_or_none()`; `fills` =
    `db.load_snaptrade_fills_or_none(journal_broker_match.fills_read_since(now).isoformat())`;
    `synced_at` = `db.load_snaptrade_txn_synced_at_or_none()`; `now_et` a tz-aware
    datetime (ValueError otherwise, even when nothing is pending).

    Returns `{"list_state": "offline" | "empty" | "rows", "list_text",
    "compare_state": "ok" | "offline" | "skipped", "compare_text", "coverage_text",
    "summary_text", "rows": [...]}`. Each row: `pending_id`, `txn_id`, `headline`,
    `verdict`, `kind` (the matcher kind or None), `reason` (a short token: why this
    verdict), `tone` ("ok" | "warning" | "neutral"), `label`, `text`, `journal_ids`,
    `nearby_journal_ids`, `log_enabled`, `log_primary`, `log_needs_tick`,
    `log_tick_label`, `log_disabled_reason`, `dismiss_primary`, `dismiss_needs_tick`,
    `dismiss_tick_label`, `dismiss_note`. Use `log_allowed` / `dismiss_allowed` for the
    button state. Every string is plain (the caller escapes any HTML it builds).
    """
    now = _tec._aware_et(now_et)
    if pending is None or not isinstance(pending, (list, tuple)) \
            or any(not isinstance(r, dict) for r in pending):
        return _result("offline", LIST_OFFLINE_TEXT)
    if len(pending) == 0:
        st = _jbm._sync_et(synced_at)
        if st is None:
            text = f"✓ No pending imports. {_jbm.match_coverage(synced_at)['text']}"
        else:
            text = ("✓ No pending imports as of the last completed transaction sync "
                    f"({_jbm._sync_when(st)}).")
        return _result("empty", text)

    journal_bad, fills_bad = journal is None, fills is None
    findings, ran = None, False
    if not journal_bad and not fills_bad:
        try:
            findings = _jbm.match_journal_to_broker(journal, fills, synced_at, now)
            ran = True
        except Exception:
            findings = None
        if findings is not None and not isinstance(findings, list):
            findings = None

    items = []
    for row in pending:
        facts = _facts(row)
        items.append({"row": row, "facts": facts, "txn": _tec._txn_text(row.get("snaptrade_txn_id")),
                      "map": None, "from_finding": False})

    if findings is None:
        banner = _offline_banner(journal_bad, fills_bad, ran)
        for it in items:
            it["map"] = _mapping(V_NOT_COMPARED, None, "compare_offline", OFFLINE_ROW_TEXT)
        rows = [_finish(it) for it in items]
        return _result("rows", "", "offline", banner, "", _summary(rows, False), rows)

    start = now.date() - timedelta(days=int(JOURNAL_MATCH_LOOKBACK_DAYS))
    by_txn, by_pid = _index_findings(findings)
    linked = _txn_to_journal_id(journal)
    for it in items:
        it["map"] = _row_map(it, by_txn, by_pid, linked, start)
    fill_by_txn, fill_by_pid = _fill_lookup(fills)
    # A pending row's facts for the twin test come from the fills read when it is there.
    for it in items:
        rec = fill_by_txn.get(it["txn"]) if it["txn"] is not None else None
        if rec is None:
            n = _tec._num(it["row"].get("id"))
            rec = fill_by_pid.get(n) if n is not None else None
        better = _facts(rec)
        if better is not None:
            it["facts"] = better
    pending_txns = {it["txn"] for it in items if it["txn"] is not None}
    pending_pids = {n for n in (_tec._num(it["row"].get("id")) for it in items) if n is not None}
    _apply_twin_rule(items, findings, fill_by_txn, fill_by_pid, pending_txns, pending_pids, linked)
    rows = [_finish(it) for it in items]
    return _result("rows", "", "ok", "", _jbm.match_coverage(synced_at)["text"],
                   _summary(rows, True), rows)


def log_allowed(row: dict, ticked: bool) -> bool:
    """May "Log This Trade" act on this row: it is enabled, and any tick it asks
    for has been given."""
    return bool(row.get("log_enabled")) and (not row.get("log_needs_tick") or bool(ticked))


def dismiss_allowed(row: dict, ticked: bool) -> bool:
    """May "Already logged" act on this row: any tick it asks for has been given."""
    return not row.get("dismiss_needs_tick") or bool(ticked)
