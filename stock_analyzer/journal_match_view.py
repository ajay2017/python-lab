"""TJDate U8a: what the journal-vs-Robinhood comparison tells the owner on 🏠 Home
and on 💰 Account (docs/plans/journal-vs-robinhood-u8.md).

PURE: no streamlit, no db, no `stock_analyzer.data` import and no clock read
(`now_et` / `today_et` are passed in). It turns the C5 matcher's findings
(`journal_broker_match.match_journal_to_broker`) into

  * `home_line`: the one calm line Home may print (a "warning" only when a counted
    difference changes share counts, otherwise a "caption"), with the broker-drift
    notice's own tickers deferred to it;
  * `account_list`: the grouped list on 💰 Account (Differs / Dates differ / Couldn't
    match automatically / Not compared yet / Matches), each item with a headline,
    the date it leaves the comparison window, a "what to check" line and, for a
    fill that is in no journal row, the one "Log This Trade" action (UE-4);
  * `sync_status_text`, `journal_signature` and `memo_key`, the small helpers the
    wiring needs (the sync wording, and a memo key that never reads the clock).

Three-state, like every cache here: `findings is None` = couldn't check (the Home
line says "not checked", Account says "offline" and names the failed input),
`[]` = checked and clean (Home prints nothing, Account says "No differences found"
beside the coverage text, never "matches Robinhood"), otherwise a list. An
unreadable sync time is never "offline": it reads through the coverage text.

This is NEVER a gate and never feeds Act Today, the Brief or any sizing path: it
only states what differs. Holdings, weights and sizes keep coming from the journal.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

import pandas as pd

from stock_analyzer import journal_broker_match as _jbm
from stock_analyzer import trade_entry_checks as _tec
from stock_analyzer.constants import JOURNAL_MATCH_BROKER_LAG_SESSIONS, JOURNAL_MATCH_LOOKBACK_DAYS

# ── what Home counts (UE-1) ──────────────────────────────────────────────────
# severity "differs" minus REDATE and SPLIT (both read as "an order placed one day
# and filled on the journal's date"; listed on Account under "dates differ"). A test
# classifies every key of `journal_broker_match._SEVERITY` into exactly one Account
# group, so a new matcher kind fails until it is placed here.
HOME_COUNTED_KINDS = frozenset({
    _jbm.KIND_UNLOGGED, _jbm.KIND_JOURNAL_ONLY, _jbm.KIND_QTY_MISMATCH,
    _jbm.KIND_PRICE_FIX, _jbm.KIND_FORWARD, _jbm.KIND_MERGED_FILLS,
    _jbm.KIND_LINKED_DIFFERS,
})

# (key, title, kinds) in display order. A kind in no group (an unknown future one)
# reads as "unmatched": visible, never counted.
ACCOUNT_GROUPS = (
    ("differs", "Differs", HOME_COUNTED_KINDS),
    ("dates", "Dates differ; the journal may be right",
     frozenset({_jbm.KIND_REDATE, _jbm.KIND_SPLIT})),
    ("unmatched", "Couldn't match automatically",
     frozenset({_jbm.KIND_AMBIGUOUS, _jbm.KIND_TOO_MANY})),
    ("not_compared", "Not compared yet",
     frozenset({_jbm.KIND_NOT_YET_COMPARED, _jbm.KIND_UNCHECKED})),
    ("matches", "Matches, nothing to fix",
     frozenset({_jbm.KIND_LINK, _jbm.KIND_RECONCILED, _jbm.KIND_SKIPPED_NON_EQUITY})),
)
_UNKNOWN_KIND_GROUP = "unmatched"
_GROUP_OF_KIND = {k: key for key, _title, kinds in ACCOUNT_GROUPS for k in kinds}

# ── texts (plain: no "$", no "**", no line starting with "#") ────────────────
HOME_UNCHECKED_TEXT = ("⚠️ Journal vs Robinhood not checked: your journal or Robinhood's fills "
                       "couldn't be read this run, so a missing or wrong journal row isn't flagged.")
HOME_WARNING_TAIL = (" → 💰 Account → Broker Sync. Holdings, weights and sizes are computed from "
                     "your journal.")
LOG_TICK_LABEL_DISMISSED = ("I've checked: this fill isn't in my journal (I marked it Already "
                            "logged earlier)")
LOG_TICK_LABEL_LOGGED = ("I've checked: this fill isn't in my journal (it was marked logged, but "
                         "no journal row is linked to it)")
_LOG_TICK_LABEL_OF_STATUS = {"dismissed": LOG_TICK_LABEL_DISMISSED, "logged": LOG_TICK_LABEL_LOGGED}
NOT_BUILT = "A correction that also repairs past account figures isn't built yet."
CHECK_FILLED = "Check the Filled date in Robinhood."
WAITING_FOR_SYNC_TEXT = "Waiting for the first `broker` cron sync."
NO_TXN_SYNC_TEXT = ("No completed transaction import on record (or it couldn't be read), so "
                    "journal rows aren't compared with Robinhood's fills.")
CLEAN_TEXT = "No differences found."

_OFFLINE_PART_WORDS = {"journal": "your journal", "fills": "Robinhood's fills"}

_FIX_TEXT = {
    _jbm.KIND_UNLOGGED: ("If this trade is yours and missing from the journal, log it, after "
                         "checking the journal so it isn't counted twice."),
    _jbm.KIND_JOURNAL_ONLY: ("Check this trade in Robinhood. If it never happened or was logged "
                             f"twice, the journal row is wrong. {NOT_BUILT}"),
    _jbm.KIND_QTY_MISMATCH: ("Check the share count in Robinhood against the journal row. "
                             f"{NOT_BUILT}"),
    _jbm.KIND_PRICE_FIX: f"Check the price in Robinhood. {NOT_BUILT}",
    _jbm.KIND_FORWARD: f"{CHECK_FILLED} {NOT_BUILT}",
    _jbm.KIND_MERGED_FILLS: ("Check in Robinhood whether these fills were one order or separate "
                             f"trades. {NOT_BUILT}"),
    _jbm.KIND_LINKED_DIFFERS: ("Compare this journal row with Robinhood's record to see which "
                               "one is wrong."),
    _jbm.KIND_REDATE: (f"{CHECK_FILLED} If it matches the journal's date, nothing needs fixing."),
    _jbm.KIND_SPLIT: (f"{CHECK_FILLED} If the dates match the journal's, nothing needs fixing."),
    _jbm.KIND_AMBIGUOUS: ("Check these in Robinhood: the app can't tell which journal row goes "
                          "with which Robinhood record."),
    _jbm.KIND_TOO_MANY: ("Check these in Robinhood: there are too many similar fills to match "
                         "them to the journal row automatically."),
    _jbm.KIND_NOT_YET_COMPARED: (
        "Nothing to do yet: a journal row is compared once a completed transaction sync runs "
        f"on a later date, at least {JOURNAL_MATCH_BROKER_LAG_SESSIONS} "
        f"{'session' if JOURNAL_MATCH_BROKER_LAG_SESSIONS == 1 else 'sessions'} after the "
        "row's date."),
    _jbm.KIND_UNCHECKED: ("Check this trade in Robinhood and 📋 History: a date, share count or "
                          "price couldn't be read."),
    _jbm.KIND_LINK: "Nothing to fix: the date, shares and price agree.",
    _jbm.KIND_RECONCILED: "Nothing to fix: the journal row equals Robinhood's fills that day combined.",
    _jbm.KIND_SKIPPED_NON_EQUITY: "Nothing to fix: the journal tracks stocks and ETFs, not options.",
}
_FIX_TEXT_UNKNOWN = "Check this in Robinhood."


# ── small helpers ────────────────────────────────────────────────────────────

def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _list(v) -> list:
    return list(v) if isinstance(v, (list, tuple)) else []


def _upper(v) -> str:
    return (_tec._txn_text(v) or "").upper()


def _status(v) -> str:
    return (_tec._txn_text(v) or "").lower()


def _findings_list(findings) -> "list | None":
    """The findings as a list of dicts, or None when they aren't usable (None, not a
    list). Non-dict elements are dropped."""
    if not isinstance(findings, (list, tuple)):
        return None
    return [f for f in findings if isinstance(f, dict)]


def _ids_text(ids) -> str:
    return ", ".join(f"#{i}" for i in ids)


_SHARE_TOKENS = ("ticker", "action", "shares")
_NOT_A_COUNT_TOKENS = ("ticker", "action")


def _differs_on(f: dict):
    """A LINKED_DIFFERS finding's `differs_on` as a list, or None when it is missing or
    not a list (callers fail closed on None)."""
    on = f.get("differs_on")
    return list(on) if isinstance(on, (list, tuple)) else None


def _linked_names_ticker_or_action(f: dict) -> bool:
    """A LINKED_DIFFERS finding whose ticker or action differs from Robinhood's record
    (or whose `differs_on` is missing: fail closed). The broker-drift notice can't
    explain a wrong ticker or action."""
    if f.get("kind") != _jbm.KIND_LINKED_DIFFERS:
        return False
    on = _differs_on(f)
    return on is None or any(t in on for t in _NOT_A_COUNT_TOKENS)


def changes_holdings(f: dict) -> bool:
    """True when the finding moves share counts: its `holdings_effect` is present and
    not the same as zero within `BROKER_DRIFT_SHARE_TOL` (`_tec._same_shares`), or it is
    a LINKED_DIFFERS whose ticker, action or shares differ (`differs_on`; a missing or
    non-list `differs_on` counts: fail closed). A linked row with the right share count
    on the wrong ticker or action has a zero `holdings_effect` and still moves holdings."""
    if f.get("kind") == _jbm.KIND_LINKED_DIFFERS:
        on = _differs_on(f)
        if on is None or any(t in on for t in _SHARE_TOKENS):
            return True
    he = _tec._num(f.get("holdings_effect"))
    return he is not None and not _tec._same_shares(he, 0.0)


def diff_bucket(f: dict) -> str:
    """"price" or "date": which bucket a counted finding that doesn't change holdings
    reads in on Home's caption ("{k} price, {d} date"). PRICE_FIX is price; FORWARD and
    MERGED_FILLS are date; LINKED_DIFFERS is price when "price" is in its `differs_on`,
    else date (the price comparison below only when `differs_on` is absent); anything else
    (a share-changing kind whose effect is inside the tolerance) is price when both prices
    are known and differ, else date. Exactly one bucket for every finding."""
    kind = f.get("kind")
    if kind == _jbm.KIND_PRICE_FIX:
        return "price"
    if kind in (_jbm.KIND_FORWARD, _jbm.KIND_MERGED_FILLS):
        return "date"
    if kind == _jbm.KIND_LINKED_DIFFERS:
        on = _differs_on(f)
        if on is not None:
            return "price" if "price" in on else "date"
    jp, bp = _tec._num(f.get("journal_price")), _tec._num(f.get("broker_price"))
    if jp is not None and bp is not None and not _tec._same_price(jp, bp):
        return "price"
    return "date"


def _tickers_in_drift(drift_verdict) -> set:
    """Upper-case tickers of the broker-drift notice's `diff` (rh_only / app_only /
    qty_mismatch), only when its state is "drift"; empty for any other state, a missing
    `diff` or a non-dict."""
    if not isinstance(drift_verdict, dict) or drift_verdict.get("state") != "drift":
        return set()
    diff = drift_verdict.get("diff")
    if not isinstance(diff, dict):
        return set()
    out: set = set()
    for key in ("rh_only", "app_only", "qty_mismatch"):
        for rec in _list(diff.get(key)):
            tk = _upper(rec.get("ticker")) if isinstance(rec, dict) else ""
            if tk:
                out.add(tk)
    return out


def _example(f: dict) -> str:
    """The share-changing example clause for the warning line, from one finding."""
    kind = f.get("kind")
    tk = _upper(f.get("ticker")) or "a ticker"
    act = (_tec._txn_text(f.get("action")) or "trade").lower()
    if kind == _jbm.KIND_UNLOGGED:
        return f"1 Robinhood {act} of {tk} not in your journal"
    if kind == _jbm.KIND_JOURNAL_ONLY:
        return f"a journal {act} of {tk} with no Robinhood fill"
    if kind == _jbm.KIND_QTY_MISMATCH:
        return f"{tk} {act} shares differ between your journal and Robinhood"
    if kind == _jbm.KIND_LINKED_DIFFERS:
        if _linked_names_ticker_or_action(f):
            return f"a linked {tk} {act} differs in ticker or action from Robinhood's record"
        return f"a linked {tk} {act} differs in shares"
    return f"a {tk} {act} differs in shares"


def _deferred_sentence(tickers: list) -> str:
    rows = "it is" if len(tickers) == 1 else "they are"
    return (f"{', '.join(tickers)}: shown in the broker-drift notice above; {rows} listed "
            "on 💰 Account.")


def _lag_text(txn_et, full_et) -> str:
    """UE-8: the sentence when the last sync run's transaction import didn't finish
    (the completed-import stamp is older than the run's own stamp), else ''. A clean run
    stamps both from the same time, so equal stamps are no lag."""
    if txn_et is None or full_et is None or not txn_et < full_et:
        return ""
    return (f"The last Robinhood sync run didn't finish importing transactions, so fills after "
            f"{_jbm._sync_when(txn_et)} may not be imported yet, so they may not be compared "
            "with your journal.")


# ── Home ─────────────────────────────────────────────────────────────────────

def home_line(findings, drift_verdict, txn_synced_at, full_synced_at,
              broker_linked) -> "dict | None":
    """The line 🏠 Home may print, or None (nothing to say, never an all-clear).

    `findings` = the matcher's findings (None = couldn't check, [] = clean);
    `drift_verdict` = `broker_sync.decide_drift_banner`'s dict (or None); the two sync
    stamps = `db.load_snaptrade_txn_synced_at_or_none()` and the config's
    `last_full_sync_at`; `broker_linked` False -> None.

    Returns `{"tier": "warning" | "caption", "text", "counted", "deferred",
    "deferred_tickers"}` where `counted` is the number of counted findings left after
    the broker-drift dedupe, `deferred` the number left to the broker-drift notice
    (counted + deferred = every finding of a counted kind) and `deferred_tickers` their
    sorted tickers.

    Counted = `HOME_COUNTED_KINDS`. "warning" only when a counted finding changes share
    counts (`changes_holdings`); a price- or date-only difference is a caption. A counted
    share-changing finding on a ticker in the drift notice (state "drift" only) is not
    counted: the drift notice shows it and the line cross-references it.
    """
    if not broker_linked:
        return None
    fl = _findings_list(findings)
    if fl is None:
        return {"tier": "caption", "text": HOME_UNCHECKED_TEXT, "counted": 0, "deferred": 0,
                "deferred_tickers": []}
    drift_tickers = _tickers_in_drift(drift_verdict)
    counted, deferred = [], []
    for f in fl:
        if f.get("kind") not in HOME_COUNTED_KINDS:
            continue
        # A linked row on the wrong ticker or action is never deferred: the drift
        # notice doesn't explain it.
        if (changes_holdings(f) and not _linked_names_ticker_or_action(f)
                and _upper(f.get("ticker")) in drift_tickers):
            deferred.append(f)
        else:
            counted.append(f)
    d_tickers = sorted({_upper(f.get("ticker")) for f in deferred if _upper(f.get("ticker"))})
    d_text = _deferred_sentence(d_tickers) if d_tickers else ""
    shares = [f for f in counted if changes_holdings(f)]
    others = [f for f in counted if not changes_holdings(f)]

    def out(tier, text):
        return {"tier": tier, "text": text, "counted": len(counted), "deferred": len(deferred),
                "deferred_tickers": d_tickers}

    if shares:
        ex = _example(shares[0])
        ex = f"e.g. {ex}" if len(shares) > 1 else ex
        more = f"; plus {len(others)} in price or date" if others else ""
        word = "changes" if len(shares) == 1 else "change"
        text = (f"⚠️ Journal vs Robinhood: {_plural(len(shares), 'difference')} that {word} "
                f"share counts ({ex}){more}{HOME_WARNING_TAIL}")
        if d_text:
            text += f" {d_text}"
        return out("warning", text)
    if others:
        price = sum(1 for f in others if diff_bucket(f) == "price")
        date_n = len(others) - price
        # "share counts agree" only where agreement is known: nothing left unresolved or
        # unchecked (a missing or unknown severity counts as unchecked), the coverage
        # known, no UE-8 lag and nothing deferred to the broker-drift notice.
        known = (all(f.get("severity") in (_jbm.SEV_DIFFERS, _jbm.SEV_INFO) for f in fl)
                 and _jbm.match_coverage(txn_synced_at)["state"] == "known"
                 and not _lag_text(_jbm._sync_et(txn_synced_at), _jbm._sync_et(full_synced_at))
                 and not d_text)
        agree = "; share counts agree" if known else ""
        text = (f"ℹ️ Journal vs Robinhood: {_plural(len(others), 'difference')} in price or date "
                f"({price} price, {date_n} date){agree} → 💰 Account → Broker Sync.")
        if d_text:
            text += f" {d_text}"
        return out("caption", text)
    if d_text:
        return out("caption", f"ℹ️ Journal vs Robinhood: {d_text}")
    txn_et, full_et = _jbm._sync_et(txn_synced_at), _jbm._sync_et(full_synced_at)
    cov = _jbm.match_coverage(txn_synced_at)
    if cov["state"] == "unknown":
        return out("caption", f"ℹ️ Journal vs Robinhood: {cov['text']}")
    lag = _lag_text(txn_et, full_et)
    if lag:
        return out("caption", f"ℹ️ Journal vs Robinhood: {lag}")
    return None


# ── 💰 Account ───────────────────────────────────────────────────────────────

def _dates(f: dict, key: str) -> list:
    out = []
    for d in _list(f.get(key)):
        d = _tec._as_date(d) if d is not None else None
        if d is not None:
            out.append(d)
    return out


def _leaves_window_on(f: dict) -> "date | None":
    """The first day this finding is no longer compared: its newest date plus the
    comparison window (`JOURNAL_MATCH_LOOKBACK_DAYS`) plus one day (the matcher keeps a
    finding while its newest date is `today - LOOKBACK` or later). None without dates."""
    ds = _dates(f, "journal_dates") + _dates(f, "fill_dates")
    if not ds:
        return None
    return max(ds) + timedelta(days=int(JOURNAL_MATCH_LOOKBACK_DAYS) + 1)


def _statuses(f: dict) -> list:
    return [_status(s) for s in _list(f.get("fill_statuses"))]


def _all_pending(f: dict) -> bool:
    """True when the finding has at least one fill and every fill is still pending, so
    it is already shown in 📥 Pending Imports (a JOURNAL_ONLY finding has no fill)."""
    st = _statuses(f)
    return len(st) >= 1 and all(s == "pending" for s in st)


def _headline(f: dict) -> str:
    """'SELL 5 TSLA, journal #420, Robinhood fill Tue Oct 14' (no currency symbol);
    parts that aren't there are left out."""
    if f.get("kind") == _jbm.KIND_SKIPPED_NON_EQUITY:
        return "Robinhood option records, not compared"
    jsh, bsh = _tec._num(f.get("journal_shares")), _tec._num(f.get("broker_shares"))
    sh = jsh if jsh is not None else bsh
    head = " ".join(p for p in (
        _upper(f.get("action")), _tec._fmt_shares(sh) if sh is not None else "",
        _upper(f.get("ticker"))) if p) or "Journal vs Robinhood"
    if jsh is not None and bsh is not None and not _tec._same_shares(jsh, bsh):
        head += f" (Robinhood {_tec._fmt_shares(bsh)})"
    jids = [i for i in _list(f.get("journal_ids")) if i is not None]
    if jids:
        head += f", journal {_ids_text(jids)}"
    fills = sorted(set(_dates(f, "fill_dates")))
    if fills:
        head += f", Robinhood {'fill' if len(fills) == 1 else 'fills'} " \
                + ", ".join(_tec._fmt_date(d) for d in fills)
    elif _dates(f, "journal_dates"):
        head += f", dated {_tec._fmt_date(max(_dates(f, 'journal_dates')))}"
    return head


def _status_note(f: dict) -> str:
    st = [s for s in _statuses(f) if s]
    if not st:
        return ""
    if len(st) == 1:
        return {"pending": "This fill is in 📥 Pending Imports.",
                "dismissed": "You marked this fill 'Already logged' in Pending Imports.",
                "logged": "Pending Imports shows this fill as logged."}.get(
            st[0], f"Pending Imports status of this fill: {st[0]}.")
    counts: dict = {}
    for s in st:
        counts[s] = counts.get(s, 0) + 1
    return "Pending Imports status of the fills: " + ", ".join(
        f"{n} {s}" for s, n in sorted(counts.items())) + "."


def _log_fields(f: dict) -> dict:
    """UE-4: "Log This Trade" is offered only for an UNLOGGED finding with exactly one
    fill that has a transaction id and a Pending Imports status of dismissed or logged
    (any other status fails closed); it always needs the tick, worded by that status.
    Every other item has it off. The
    fields are shaped like `pending_import_verdict` rows so `log_allowed` works on them."""
    off = {"log_enabled": False, "log_primary": False, "log_needs_tick": False,
           "log_tick_label": "", "log_disabled_reason": "", "log_txn_id": None,
           "log_pending_id": None}
    if f.get("kind") != _jbm.KIND_UNLOGGED:
        return off
    txns, sts = _list(f.get("txn_ids")), _statuses(f)
    if len(txns) != 1 or len(sts) != 1:
        return off
    txn = _tec._txn_text(txns[0])
    label = _LOG_TICK_LABEL_OF_STATUS.get(sts[0])
    if txn is None or label is None:
        return off
    pids = _list(f.get("pending_ids"))
    return dict(off, log_enabled=True, log_needs_tick=True, log_tick_label=label,
                log_txn_id=txn, log_pending_id=pids[0] if pids else None)


def _item(f: dict) -> dict:
    kind = f.get("kind")
    raw = f.get("text")
    text = raw.strip() if isinstance(raw, str) else ""
    head = _headline(f)
    leaves = _leaves_window_on(f)
    n = int(JOURNAL_MATCH_LOOKBACK_DAYS)
    jids = []
    for i in _list(f.get("journal_ids")):
        num = _tec._num(i)
        if num is not None:
            jids.append(int(num))
    item = {
        "kind": kind, "ticker": _upper(f.get("ticker")), "action": _upper(f.get("action")),
        "headline": head, "text": text or head,
        "fix_text": _FIX_TEXT.get(kind, _FIX_TEXT_UNKNOWN), "status_note": _status_note(f),
        "leaves_window_on": leaves,
        "leaves_text": ("" if leaves is None else
                        f"Leaves this comparison on {_tec._fmt_date(leaves)} "
                        f"(it covers the last {n} days)."),
        "journal_ids": jids,
        "txn_ids": [t for t in (_tec._txn_text(x) for x in _list(f.get("txn_ids")))
                    if t is not None],
    }
    item.update(_log_fields(f))
    return item


def _offline_text(offline_parts) -> str:
    parts = [offline_parts] if isinstance(offline_parts, str) else _list(offline_parts)
    names = [_OFFLINE_PART_WORDS[p] for p in ("journal", "fills") if p in parts]
    if names:
        return (f"⚠️ Journal vs Robinhood not compared: {' and '.join(names)} couldn't be read "
                "this render, so a missing or wrong journal row isn't flagged here.")
    return ("⚠️ Journal vs Robinhood not compared: the comparison didn't run this render, so a "
            "missing or wrong journal row isn't flagged here.")


def _summary(groups: list, in_pending: int) -> str:
    n = {g["key"]: len(g["items"]) for g in groups}
    parts = []
    if n.get("differs"):
        parts.append(f"{n['differs']} {'differs' if n['differs'] == 1 else 'differ'}")
    if n.get("dates"):
        parts.append(f"{n['dates']} dated differently")
    if n.get("unmatched"):
        parts.append(f"{n['unmatched']} couldn't be matched automatically")
    if n.get("not_compared"):
        parts.append(f"{n['not_compared']} not compared yet")
    agree = ""
    if n.get("matches"):
        agree = f"{n['matches']} {'agrees' if n['matches'] == 1 else 'agree'}, nothing to fix."
    pend = (f"{in_pending} more {'is' if in_pending == 1 else 'are'} in 📥 Pending Imports above."
            if in_pending else "")
    if parts:
        text = f"Found: {', '.join(parts)}." + (f" {agree}" if agree else "")
    elif agree:
        text = agree if in_pending else f"{CLEAN_TEXT} {agree}"
    elif in_pending:
        return f"Nothing else to list here; {pend}"
    else:
        text = CLEAN_TEXT
    return f"{text} {pend}" if pend else text


def account_list(findings, txn_synced_at, now_et, pending_visible, offline_parts) -> dict:
    """The grouped list for 💰 Account.

    `findings` = the matcher's findings (None = couldn't check), `txn_synced_at` =
    `db.load_snaptrade_txn_synced_at_or_none()`, `now_et` a tz-aware datetime (ValueError
    otherwise, even when offline), `pending_visible` True when 📥 Pending Imports is
    rendered on the page (a finding whose fills are all pending is then left out and
    counted in `in_pending_count`; with False everything is listed), `offline_parts` the
    inputs that couldn't be read, names from {"journal", "fills"} (empty when the matcher
    itself failed).

    "clean" means the findings list is empty; findings that were all left out as pending
    are "rows" with no groups (the summary names them).

    Returns `{"state": "offline" | "clean" | "rows", "offline_text", "summary_text",
    "window_text", "coverage_text", "in_pending_count", "groups"}`. `groups` is a list of
    `{"key", "title", "items"}` in `ACCOUNT_GROUPS` order (empty groups left out; items
    in the matcher's order). Each item: `kind`, `ticker`, `action`, `headline`, `text`
    (the matcher's own), `fix_text`, `status_note`, `leaves_window_on` (a date or None),
    `leaves_text`, `journal_ids`, `txn_ids` and the `log_*` fields (`log_enabled`,
    `log_primary`, `log_needs_tick`, `log_tick_label`, `log_disabled_reason`, `log_txn_id`,
    `log_pending_id`; use `pending_import_verdict.log_allowed(item, ticked)`).
    """
    now = _tec._aware_et(now_et)
    fl = _findings_list(findings)
    if fl is None:
        return {"state": "offline", "offline_text": _offline_text(offline_parts),
                "summary_text": "", "window_text": "", "coverage_text": "",
                "in_pending_count": 0, "groups": []}
    start = now.date() - timedelta(days=int(JOURNAL_MATCH_LOOKBACK_DAYS))
    window = f"Compared: journal rows and Robinhood fills dated {_tec._fmt_date(start)} or later."
    coverage = _jbm.match_coverage(txn_synced_at)["text"]
    in_pending = 0
    buckets = {key: [] for key, _t, _k in ACCOUNT_GROUPS}
    for f in fl:
        if pending_visible is True and _all_pending(f):
            in_pending += 1
            continue
        buckets[_GROUP_OF_KIND.get(f.get("kind"), _UNKNOWN_KIND_GROUP)].append(_item(f))
    groups = [{"key": key, "title": title, "items": buckets[key]}
              for key, title, _k in ACCOUNT_GROUPS if buckets[key]]
    # "clean" only for an empty findings list. Findings that were all left out as pending
    # are "rows" with no groups; the summary names them (never "No differences found").
    clean = len(fl) == 0
    return {"state": "clean" if clean else "rows", "offline_text": "",
            "summary_text": CLEAN_TEXT if clean else _summary(groups, in_pending),
            "window_text": window,
            "coverage_text": coverage, "in_pending_count": in_pending, "groups": groups}


def sync_status_text(full_synced_at, txn_synced_at) -> str:
    """The Broker Sync banner's sync wording (ET): when the last sync run was, and
    whether its transaction import finished. "Last sync run" uses the later of the two
    readable stamps. Never claims a comparison it can't back."""
    full_et, txn_et = _jbm._sync_et(full_synced_at), _jbm._sync_et(txn_synced_at)
    readable = [s for s in (full_et, txn_et) if s is not None]
    if not readable:
        if full_synced_at is None and txn_synced_at is None:
            return WAITING_FOR_SYNC_TEXT
        return f"Last sync run: time couldn't be read. {NO_TXN_SYNC_TEXT}"
    last = max(readable)
    text = f"Last sync run: {_jbm._sync_when(last)}."
    if txn_et is None:
        return f"{text} {NO_TXN_SYNC_TEXT}"
    if full_et is not None and txn_et < full_et:
        return (f"{text} Its transaction import didn't finish; fills are compared through the "
                f"{_jbm._sync_when(txn_et)} sync.")
    return f"{text} Transactions imported in that run."


# ── memo support (Home) ──────────────────────────────────────────────────────

def _tok(v) -> str:
    t = _tec._txn_text(v)
    return "" if t is None else t


def _tok_num(v, dp: int) -> str:
    n = _tec._num(v)
    return "" if n is None else f"{round(n, dp) + 0.0:.{dp}f}"


def _tok_id(v) -> str:
    n = _tec._num(v)
    if n is None:
        return ""
    return str(int(n)) if n == int(n) else f"{n:.9f}"


def _tok_when(v) -> str:
    if _tec._txn_text(v) is None:
        return ""
    try:
        ts = pd.to_datetime(v, utc=True, errors="coerce", format="ISO8601")
    except (TypeError, ValueError):
        ts = None
    if ts is None or pd.isna(ts):
        return str(v).strip()
    return ts.isoformat()


def journal_signature(trades_df) -> "str | None":
    """An order-independent hash of what the matcher reads from the journal: per row
    (id, ticker, action, shares to 9 dp, price to 6 dp, `traded_at` as aware-UTC ISO,
    `broker_txn_id`). None / NaN / '' / 'None' read the same, a missing column reads as
    None, and an empty frame has one fixed signature. None for a non-DataFrame."""
    if not isinstance(trades_df, pd.DataFrame):
        return None
    lines = []
    for r in trades_df.to_dict("records"):
        lines.append("\x1f".join((
            _tok_id(r.get("id")), _tok(r.get("ticker")), _tok(r.get("action")),
            _tok_num(r.get("shares"), 9), _tok_num(r.get("price"), 6),
            _tok_when(r.get("traded_at")), _tok(r.get("broker_txn_id")))))
    lines.sort()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _stamp_key(v) -> "str | None":
    st = _jbm._sync_et(v)
    return None if st is None else st.isoformat()


def memo_key(today_et, journal_sig, txn_synced_at, full_synced_at, nonce) -> tuple:
    """The key Home's memo compares: `(today_et ISO date, journal signature, txn sync
    ET ISO or None, full sync ET ISO or None, nonce)`. `today_et` must be a `date` (the
    matcher reads `now_et` only for its date; a datetime raises ValueError). Stamps are
    normalised through `journal_broker_match._sync_et`, so two stamps naming the same
    instant in different offsets give the same key."""
    if not isinstance(today_et, date) or isinstance(today_et, datetime):
        raise ValueError("today_et must be a date (not a datetime)")
    return (today_et.isoformat(), journal_sig, _stamp_key(txn_synced_at),
            _stamp_key(full_synced_at), nonce)
