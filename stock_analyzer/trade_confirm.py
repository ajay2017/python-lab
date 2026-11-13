"""
Duplicate-entry guard for the Trade Journal's confirm card (bug TJDup).

THE INCIDENT (2026-11-06). CSCO BUY 3 @ $102.00 was logged once as a
RECOMMENDATION row (stored 2026-11-05T00:14:00+00:00, i.e. 19:14 ET on Nov 4) and
later logged AGAIN as a MANUAL row dated Nov 4 (stored at the 16:00 ET date-only
anchor, 2026-11-04T21:00:00+00:00). Robinhood had ONE fill. The journal read 16
shares against the broker's 15 and the stop card said "Sell all 16". Nothing
compared a new entry with the rows already in the journal.

THE FIX IS A WARNING, NOT A BLOCK (owner, 2026-11-06). Genuine split fills
repeat a size on the same day (the P0 probe found about one a month), so a
duplicate-looking entry shows a prominent panel naming the matching row and asks
for ONE explicit tick ("this is a separate fill"). The single exception is exact
identity: on the broker-prefill path a `broker_txn_id` already on a journal row
is the same Robinhood fill, so Confirm stays disabled (HARD).

WHICH DATE. Every comparison uses the ET trade date (`trade_time.et_trade_date`)
of a journal row that `trade_time.normalize_traded_at` has already re-anchored.
The UTC date would not have caught the incident: the existing row's UTC date was
Nov 5, the new row's Nov 4. Session distance is counted in NYSE trading days
(`data.trading_sessions_between`), so Fri -> Mon is 1 and a holiday adds none.

Pure / no I/O / no Streamlit. `find_duplicate_entries` is the finding;
`confirm_gate` turns findings into what the card renders. The C2 entry checks
(docs/plans/trade-entry-guard.md, `trade_entry_checks`) arrive as the optional
`entry_findings` argument of `confirm_gate` and are appended AFTER the duplicate
blocks, so one card, one tick box and one disabled rule stay the only confirm UI.
C2b adds the price and broker findings to the same list; a tick finding with
`group` "price" or "broker" changes the tick-box wording (see `confirm_gate`).
Plain text only: the caller escapes it.
"""

from __future__ import annotations

import pandas as pd

from stock_analyzer import data as _data
from stock_analyzer import trade_entry_checks as _tec
from stock_analyzer import trade_time
from stock_analyzer.constants import (
    BROKER_DRIFT_SHARE_TOL,
    JOURNAL_MATCH_PRICE_TOL_USD,
    TRADE_LOG_DUP_WINDOW_SESSIONS,
)

KIND_SAME_PRICE = "same_price"
KIND_SAME_SIZE_DIFF_PRICE = "same_size_diff_price"
KIND_ADJACENT = "adjacent_session_same_price"
KIND_BROKER_TXN = "broker_txn_present"

_KIND_ORDER = {KIND_SAME_PRICE: 0, KIND_SAME_SIZE_DIFF_PRICE: 1, KIND_ADJACENT: 2}

TITLE_UNCHECKED = "Couldn't check for a duplicate: the trade journal couldn't be read."
TITLE_HARD = "Robinhood fill already in the journal"
TITLE_POSSIBLE = "Possible duplicate — this looks like a trade already in your journal"
LINE_HARD_NEXT = "Use 💰 Account → Broker Sync → Pending Imports → Already logged instead."
LINE_TICK_HINT = "Tick the box below only if this is a separate fill."
TITLE_ENTRY_HARD = "This entry can't be recorded as dated"
TITLE_ENTRY_TICK = "Check the date and time"
LINE_ENTRY_TICK_HINT = ("Tick the box below if the date and time are right "
                        "(for example an extended-hours fill).")
LABEL_ENTRY_TICK = "The date and time are right"
# The same single tick-box once a price or broker finding (TJDate C2b) is among
# the ticks: the box then vouches for the whole entry, not only its date and time.
TITLE_ENTRY_TICK_WIDE = "Check this entry"
LINE_ENTRY_TICK_HINT_WIDE = ("Tick the box below if the entry is right as typed "
                             "(for example an extended-hours fill).")
LABEL_ENTRY_TICK_WIDE = "The entry is right as typed"
# A weekend or holiday date has no extended-hours session to point at, so when a tick
# finding is NON_TRADING_DATE the hint's example is the broker's own record instead.
LINE_ENTRY_TICK_HINT_NO_SESSION = ("Tick the box below if the date and time are right "
                                   "(for example Robinhood shows the fill on this date).")
LINE_ENTRY_TICK_HINT_WIDE_NO_SESSION = ("Tick the box below if the entry is right as typed "
                                        "(for example Robinhood shows the fill on this date).")
_ENTRY_WIDE_GROUPS = ("price", "broker")


# ── small coercions ──────────────────────────────────────────────────────────

def _norm(v) -> str:
    return str(v or "").strip().upper()


def _num(v) -> "float | None":
    """float, or None for None / NaN / unparseable."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _txn(v) -> "str | None":
    """A non-empty broker_txn_id as text, else None (NaN / None / '' / 'None')."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return None if s in ("", "None", "nan", "NaN") else s


def _row_id(v):
    """Journal id as an int when it is integer-valued (pandas floats a column
    that holds a None), else the raw value, else None."""
    f = _num(v)
    if f is None:
        return None
    return int(f) if f == int(f) else v


def _same_price(p_new: float, p_row: float) -> bool:
    return round(abs(p_new - p_row), 6) <= JOURNAL_MATCH_PRICE_TOL_USD


def _et_date(value):
    return trade_time.et_trade_date(value)


# ── the finding ──────────────────────────────────────────────────────────────

def _summarise(row, kind: str, row_after_new: "bool | None") -> dict:
    ta = row.get("traded_at")
    tk = row.get("traded_at_time_known")
    return {
        "kind": kind,
        "id": _row_id(row.get("id")),
        "ticker": _norm(row.get("ticker")),
        "action": _norm(row.get("action")),
        "shares": _num(row.get("shares")),
        "price": _num(row.get("price")),
        "et_display": trade_time.et_display(ta, tk),
        "traded_at": ta,
        "time_known": tk,
        "trigger_type": (str(row.get("trigger_type")).strip()
                         if row.get("trigger_type") is not None
                         and str(row.get("trigger_type")).strip() not in ("", "None", "nan")
                         else None),
        "linked": _txn(row.get("broker_txn_id")) is not None,
        "row_after_new": row_after_new,
    }


def find_duplicate_entries(new: dict, journal: "pd.DataFrame | None", *,
                           broker_path: bool) -> dict:
    """Compare a staged trade entry with the journal.

    `new`: ticker, action, shares, price, traded_at (ISO; may be the 16:00 ET
    date-only anchor; absent = a manual entry stamped "now", read as now in ET),
    optional broker_txn_id. `journal`: a STRICT read (`db.load_trades_or_none()`,
    or the session frame in no-DB mode); None = could not read.

    Returns {"state": "unchecked" | "checked", "hard": [...], "matches": [...]}.
    `hard` holds exact-identity findings (broker path only: the staged
    broker_txn_id is already on a journal row). `matches` are the soft findings,
    closest first. On the broker path a row that already carries a
    broker_txn_id is a different fill and never soft-matches; on the manual
    path it can (that is how the CSCO duplicate was a linked row's twin).
    """
    if journal is None:
        return {"state": "unchecked", "hard": [], "matches": []}
    if not isinstance(journal, pd.DataFrame) or journal.empty:
        return {"state": "checked", "hard": [], "matches": []}

    df = journal
    if "traded_at_time_known" not in df.columns:
        # Same re-anchoring the strict loader already applied; skipped when the
        # frame carries its flag so the "time was invented" marks survive.
        df = trade_time.normalize_traded_at(df)

    traded_at_new = new.get("traded_at")
    if traded_at_new is None or str(traded_at_new).strip() == "":
        from stock_analyzer.market_time import now_et
        traded_at_new = now_et().isoformat()
    d_new = _et_date(traded_at_new)
    t_new, a_new = _norm(new.get("ticker")), _norm(new.get("action"))
    sh_new, p_new = _num(new.get("shares")), _num(new.get("price"))
    txn_new = _txn(new.get("broker_txn_id")) if broker_path else None

    hard: list = []
    matches: list = []
    for rec in df.to_dict("records"):
        txn_row = _txn(rec.get("broker_txn_id"))
        if txn_new is not None and txn_row == txn_new:
            hard.append({"kind": KIND_BROKER_TXN,
                         "row": _summarise(rec, KIND_BROKER_TXN, None)})
            continue
        if broker_path and txn_row is not None:
            continue                    # a linked row is a different fill
        if _norm(rec.get("ticker")) != t_new or _norm(rec.get("action")) != a_new:
            continue
        sh_row, p_row = _num(rec.get("shares")), _num(rec.get("price"))
        if sh_new is None or sh_row is None or p_new is None or p_row is None:
            continue
        if round(abs(sh_new - sh_row), 9) > BROKER_DRIFT_SHARE_TOL:
            continue
        d_row = _et_date(rec.get("traded_at"))
        if d_new is None or d_row is None:
            continue
        n = _data.trading_sessions_between(d_new, d_row)
        price_same = _same_price(p_new, p_row)
        if n == 0:
            kind = KIND_SAME_PRICE if price_same else KIND_SAME_SIZE_DIFF_PRICE
        elif n <= TRADE_LOG_DUP_WINDOW_SESSIONS and price_same:
            kind = KIND_ADJACENT
        else:
            continue
        matches.append(_summarise(rec, kind, d_row > d_new))

    matches.sort(key=lambda m: (_KIND_ORDER[m["kind"]],
                                -(m["id"] if isinstance(m["id"], int) else 0)))
    return {"state": "checked", "hard": hard, "matches": matches}


# ── the card ─────────────────────────────────────────────────────────────────

def _money(v) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _when(m: dict) -> str:
    """"Wed Nov 4, 19:14 ET", or "Wed Nov 4 (no time)" for a date-only placeholder."""
    ts = trade_time._et_ts(m.get("traded_at"))
    if ts is None:
        return "date unknown"
    day = f"{ts.strftime('%a %b')} {ts.day}"
    if trade_time.is_placeholder_time(m.get("traded_at"), m.get("time_known")):
        return f"{day} (no time)"
    return f"{day}, {ts.strftime('%H:%M')} ET"


def _reason(m: dict) -> str:
    t = m.get("trigger_type")
    return "reason not recorded" if not t else t.replace("_", " ").title()


def _ref(i) -> str:
    """"#12", or a plain phrase for a row with no id (no-DB session mode)."""
    return f"#{i}" if i is not None else "the entry logged earlier this session"


def _head(m: dict) -> str:
    sh = m.get("shares")
    sh_txt = "?" if sh is None else f"{sh:g}"
    return (f"{_ref(m.get('id'))} · {_when(m)} · {m.get('action')} {sh_txt} "
            f"@ {_money(m.get('price'))} · {_reason(m)}")


def _kind_phrase(m: dict) -> str:
    k = m["kind"]
    if k == KIND_SAME_PRICE:
        return "same price, same day"
    if k == KIND_SAME_SIZE_DIFF_PRICE:
        return "same size, different price, same day"
    return ("same price, the next trading day" if m.get("row_after_new")
            else "same price, the previous trading day")


def _findings(dup: "dict | None") -> list:
    """Every finding as {"hard", "ids", "block"}. C2 extends this list."""
    if dup is None or dup.get("state") != "checked":
        return [{"hard": False, "ids": [], "block": {
            "level": "info", "title": TITLE_UNCHECKED, "lines": []}}]
    out: list = []
    for h in dup.get("hard", []):
        out.append({"hard": True, "ids": [], "block": {
            "level": "error", "title": TITLE_HARD,
            "lines": [_head(h["row"]), LINE_HARD_NEXT]}})
    if out:
        return out                      # an exact identity needs no tick-box
    matches = dup.get("matches", [])
    if matches:
        lines = []
        for m in matches:
            tail = " · linked to Robinhood" if m.get("linked") else ""
            lines.append(f"{_head(m)}{tail} · {_kind_phrase(m)}")
        lines.append(LINE_TICK_HINT)
        out.append({"hard": False, "ids": [m.get("id") for m in matches],
                    "block": {"level": "warning", "title": TITLE_POSSIBLE,
                              "lines": lines}})
    return out


def _tick_label(ids: list) -> str:
    names = [_ref(i) for i in ids]
    if len(names) <= 1:
        return f"This is a separate fill, not {''.join(names)}"
    return f"This is a separate fill, not {', '.join(names[:-1])} or {names[-1]}"


def _entry_split(entry_findings) -> tuple:
    """(hard texts, tick texts, info texts) of `trade_entry_checks` findings
    `[{"kind", "severity", "text"}]`. None / [] -> three empty lists. A finding
    with an unrecognised severity is treated as a tick (acknowledge it)."""
    hard, tick, info = [], [], []
    for f in entry_findings or []:
        if not isinstance(f, dict):
            continue
        text = str(f.get("text") or "")
        sev = f.get("severity")
        (hard if sev == "hard" else info if sev == "info" else tick).append(text)
    return hard, tick, info


def pending_import_to_flip(stash, saved_record):
    """The pending-import id a Confirm may flip to 'logged', or None.

    `stash` is what staging left under `_tj_pending_broker_id`:
    `{"pending_id", "txn_id"}` (a broker prefill, or a manual entry that matched
    one Robinhood fill). The flip is allowed ONLY when the stash is a dict with a
    `pending_id` and its `txn_id` is the very `broker_txn_id` of the record just
    saved. The key is shared by every staged card, so without this check
    confirming one card could mark ANOTHER card's fill logged (and the cron would
    never re-queue it). A legacy bare-int stash (staged before this shipped), a
    missing key, a None / mismatched txn id: None, and nothing is flipped. The
    cron's `close_linked_pending_imports` closes a linked fill later, the safe
    direction.
    """
    if not isinstance(stash, dict) or not isinstance(saved_record, dict):
        return None
    pid = stash.get("pending_id")
    txn, saved = _txn(stash.get("txn_id")), _txn(saved_record.get("broker_txn_id"))
    if pid is None or txn is None or saved is None or txn != saved:
        return None
    return pid


def _entry_ticks_are_wide(entry_findings) -> bool:
    """True when a tick finding (same rule as `_entry_split`: any severity other
    than "hard" / "info") has a `group` of "price" or "broker". Time findings,
    and findings with no group, keep the date-and-time wording."""
    for f in entry_findings or []:
        if (isinstance(f, dict) and f.get("severity") not in ("hard", "info")
                and f.get("group") in _ENTRY_WIDE_GROUPS):
            return True
    return False


def _entry_ticks_have_no_session(entry_findings) -> bool:
    """True when a tick finding (same rule as `_entry_split`: any severity other
    than "hard" / "info") is NON_TRADING_DATE: the date has no session, so the tick
    hint must not suggest an extended-hours fill."""
    for f in entry_findings or []:
        if (isinstance(f, dict) and f.get("severity") not in ("hard", "info")
                and f.get("kind") == _tec.KIND_NON_TRADING_DATE):
            return True
    return False


def confirm_gate(dup: "dict | None", entry_findings: "list | None" = None) -> dict:
    """What the confirm card renders and whether Confirm may be pressed.

    -> {"hard": bool, "needs_tick": bool, "tick_label": str,
        "blocks": [{"level": "error"|"warning"|"info", "title", "lines"}]}
    `hard` = Confirm stays disabled whatever is ticked. `needs_tick` = Confirm
    stays disabled until the one checkbox labelled `tick_label` is ticked.
    A None / unchecked finding is an `info` block only: never blocks, says so.

    `entry_findings` (optional): `trade_entry_checks` findings. With None / []
    the result is exactly what `dup` alone gives. Otherwise, AFTER the duplicate
    blocks: hard findings make one "error" block ("This entry can't be recorded
    as dated") and set `hard`; tick findings make one "warning" block ("Check
    the date and time", with the tick hint line when a box is shown) and need
    the same single checkbox, whose label then names both reasons when a
    duplicate tick is also due; info findings are one "info" block each. An
    exact-identity duplicate (hard, no tick) still shows no checkbox, but the
    entry findings are rendered next to it. When any tick finding is in group
    "price" or "broker" (TJDate C2b) the block title, hint and label read "Check
    this entry" / "...the entry is right as typed" instead, because the one box
    then vouches for more than the date and time. When a tick finding is
    NON_TRADING_DATE the hint's example reads "Robinhood shows the fill on this
    date" (`LINE_ENTRY_TICK_HINT_NO_SESSION` / `..._WIDE_NO_SESSION`), because a
    weekend or holiday has no extended-hours session.
    """
    fs = _findings(dup)
    ids = [i for f in fs for i in f["ids"]]
    e_hard, e_tick, e_info = _entry_split(entry_findings)
    wide = _entry_ticks_are_wide(entry_findings)
    dup_hard = any(f["hard"] for f in fs)
    needs_tick = (bool(ids) or bool(e_tick)) and not dup_hard
    blocks = [f["block"] for f in fs]
    if e_hard:
        blocks.append({"level": "error", "title": TITLE_ENTRY_HARD,
                       "lines": list(e_hard)})
    if e_tick:
        lines = list(e_tick)
        if needs_tick:
            no_session = _entry_ticks_have_no_session(entry_findings)
            if wide:
                lines.append(LINE_ENTRY_TICK_HINT_WIDE_NO_SESSION if no_session
                             else LINE_ENTRY_TICK_HINT_WIDE)
            else:
                lines.append(LINE_ENTRY_TICK_HINT_NO_SESSION if no_session
                             else LINE_ENTRY_TICK_HINT)
        blocks.append({"level": "warning",
                       "title": TITLE_ENTRY_TICK_WIDE if wide else TITLE_ENTRY_TICK,
                       "lines": lines})
    for text in e_info:
        blocks.append({"level": "info", "title": text, "lines": []})
    if not needs_tick:
        label = ""
    elif ids and e_tick:
        tail = "the entry is right as typed" if wide else "the date and time are right"
        label = f"{_tick_label(ids)}, and {tail}"
    elif ids:
        label = _tick_label(ids)
    else:
        label = LABEL_ENTRY_TICK_WIDE if wide else LABEL_ENTRY_TICK
    return {
        "hard": dup_hard or bool(e_hard),
        "needs_tick": needs_tick,
        "tick_label": label,
        "blocks": blocks,
    }
