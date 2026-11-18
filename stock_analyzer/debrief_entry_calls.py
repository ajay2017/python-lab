"""
The weekly debrief's entry-signal block, on the shared entry-call rule.

Spec: docs/plans/acted-rule-c3-spec.md section 2A. Plan:
docs/plans/acted-rule-and-report-periods.md. The rule itself (what "acted"
means, the decision window) lives in `entry_call_action`; this module only
decides WHICH calls belong to a debrief week, rolls them up by name, counts the
follow-through share and words the bullets, so the debrief's text is fixed by
code and the AI never restates (or contradicts) whether a call was acted on.

  recs_load_start     earliest call date a week's recs load must reach.
  weekly_entry_calls  this-week + carried-over calls, judged as of the week's end.
  entry_signal_lines  the plain-text bullets (no markdown) for the debrief.
  behavioural_rows    all-time rows for the behavioural fingerprint, same rule.
  acted_rule_caption  the "older acted rule" note for an archived debrief.
  drop_ai_entry_bullets  strips AI-written entry bullets (the app writes them).

The window is always `constants.ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS` (a
`window_trading_days` argument exists only for tests). Pure / no Streamlit /
no DB / no clock: a regenerated debrief gives byte-identical output.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

import pandas as pd

from stock_analyzer import constants
from stock_analyzer import entry_call_action as eca

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Display order and wording of the call types (the labels are joined with " + ").
_TYPE_LABELS = (("new_pick", "New position"),
                ("add_winner", "Add to winner"),
                ("enter_now", "Enter Now"))

RECS_OK = "ok"
RECS_UNAVAILABLE = "unavailable"
JUDGE_OK = "ok"
JUDGE_TRADES_UNAVAILABLE = "trades_unavailable"
JUDGE_TRADES_UNREADABLE = "trades_unreadable"

_JUDGE_BY_JOURNAL_STATE = {
    "ok": JUDGE_OK,
    "unavailable": JUDGE_TRADES_UNAVAILABLE,
    "unreadable": JUDGE_TRADES_UNREADABLE,
}

_UNKNOWN_REASON_TEXT = {
    eca.REASON_TRADES_UNAVAILABLE: "the trade journal couldn't be read",
    eca.REASON_TRADES_UNREADABLE: "the trade journal couldn't be read",
    eca.REASON_TRADE_DATE_UNREADABLE:
        "a buy of this name has a date that couldn't be read",
    eca.REASON_NO_CALL_DATE: "the call has no readable date",
}

_UNKNOWN_REASON_FALLBACK = "the call couldn't be judged"


def unknown_reason_text(code) -> str:
    """Plain words for why a call is "not judged" (an `entry_call_action` reason
    code). The one source for the bullets AND the model prompt, so they can't drift."""
    return _UNKNOWN_REASON_TEXT.get(code, _UNKNOWN_REASON_FALLBACK)


# Merge precedence for a name that is both this week's and carried over
# (the same order `entry_call_action.per_name` uses).
_STATUS_RANK = {eca.STATUS_ACTED: 0, eca.STATUS_STILL_OPEN: 1,
                eca.STATUS_UNKNOWN: 2, eca.STATUS_NOT_ACTED: 3}

_REQUIRED_RECS_COLS = ("ticker", "rec_date", "rec_type")


# ── small helpers ───────────────────────────────────────────────────────────

def _window(window_trading_days: int | None) -> int:
    return (constants.ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS
            if window_trading_days is None else int(window_trading_days))


def _as_date(v) -> date | None:
    return eca._call_date(v)


def _days_phrase(n: int) -> str:
    return "1 trading day" if n == 1 else f"{n} trading days"


def _text(v) -> str:
    """Stripped text; None / NaN / NA -> ''."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _clean_id(v):
    """A JSON-safe id: None for None / NaN / pd.NA, a plain int / str otherwise."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    item = getattr(v, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            return v
    return v


def _iso(d) -> str | None:
    return d.isoformat() if isinstance(d, date) else None


def _fmt_date(iso) -> str:
    """"Fri Nov 6" from a date / ISO text, from fixed name tuples (never
    strftime, so it is locale-proof and has no leading zero)."""
    d = _as_date(iso)
    if d is None:
        return _text(iso) or "an unreadable date"
    return f"{_WEEKDAYS[d.weekday()]} {_MONTHS[d.month - 1]} {d.day}"


def _rows_of(v) -> list:
    """A list from a stored list field; an absent field (None) reads as no rows."""
    return [] if v is None else list(v)


def _finite(v) -> bool:
    if isinstance(v, bool):
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


# ── recs load window ────────────────────────────────────────────────────────

def recs_load_start(week_start, window_trading_days: int | None = None) -> date:
    """Earliest call date a week's recommendations load must reach.

    A call before `week_start` can still matter (it is carried over) when its
    decision window reaches `week_start`. Steps back from `week_start - 1` while
    the window of the day before that still reaches `week_start`
    (`advance_trading_days` never decreases, so the loop ends). Carry-over is
    decided by name against LAST week's calls, so the start is also never later
    than `week_start - 7`. At N = 2: Mon 2026-11-09 -> 2026-11-02; Mon
    2026-11-02 -> 2026-10-26; Mon 2026-11-30 -> 2026-11-23.
    """
    ws = _as_date(week_start)
    if ws is None:
        raise ValueError(f"week_start is not a date: {week_start!r}")
    n = _window(window_trading_days)
    one = timedelta(days=1)
    d = ws - one
    while eca.window_end(d - one, n) >= ws:
        d -= one
    return min(d, ws - timedelta(days=7))


# ── the weekly judgement ────────────────────────────────────────────────────

def _entry_type(rec_type) -> bool:
    return str(rec_type or "").strip() in eca.ENTRY_CALL_REC_TYPES


def _call_sort_key(call: dict):
    return (call["_rd"], _text(call.get("surfaced_at")),
            eca._id_key(call.get("id")))


def _build_rows(results: list[dict], calls: list[dict]) -> list[dict]:
    """One row per ticker (sorted) from index-aligned `classify_calls` results.

    Excluded calls are neither listed nor counted in `times_surfaced`.
    """
    pairs: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for res, call in zip(results, calls):
        if res["status"] == eca.STATUS_EXCLUDED:
            continue
        pairs[res["ticker"]].append((res, call))
    rolled = {r["ticker"]: r for r in eca.per_name(results)}

    rows: list[dict] = []
    for tk in sorted(pairs):
        rn = rolled.get(tk)
        if rn is None:
            continue
        group = sorted(pairs[tk], key=lambda p: _call_sort_key(p[1]))
        latest_call = group[-1][1]
        # most common verdict; a tie goes to the one seen latest
        seen: dict[str, list[int]] = {}
        for i, (_, c) in enumerate(group):
            v = _text(c.get("verdict"))
            if v:
                cnt = seen.setdefault(v, [0, 0])
                cnt[0] += 1
                cnt[1] = i
        verdict = max(seen, key=lambda k: (seen[k][0], seen[k][1])) if seen else ""
        reason = None
        if rn["status"] == eca.STATUS_UNKNOWN:
            unk = [r for r, _ in group if r["status"] == eca.STATUS_UNKNOWN]
            reason = unk[-1]["reason"] if unk else None
        rows.append({
            "ticker":             tk,
            "status":             rn["status"],
            "reason":             reason,
            "rec_types":          list(rn["rec_types"]),
            "conviction":         _text(latest_call.get("conviction")),
            "verdict":            verdict,
            "times_surfaced":     len(group),
            "call_dates":         sorted(_iso(r["rec_date"]) for r, _ in group
                                         if r.get("rec_date") is not None),
            "window_end":         _iso(rn["window_end"]),
            "matched_trade_date": _iso(rn["matched_trade_date"]),
            "matched_trade_id":   _clean_id(rn["matched_trade_id"]),
        })
    return rows


def _follow(this_week: list[dict], carried: list[dict]) -> dict:
    """The follow-through share, by name (UD-1, UD-2).

    A ticker in both blocks counts once with `per_name` precedence (acted >
    still_open > unknown > not_acted); a tie goes to this week's row.
    `n_carried` = counted (acted / not_acted) names whose status comes from the
    carried-over row.
    """
    merged: dict[str, tuple[str, str]] = {}
    for r in this_week:
        merged[r["ticker"]] = (r["status"], "this_week")
    for r in carried:
        cur = merged.get(r["ticker"])
        if cur is None or _STATUS_RANK[r["status"]] < _STATUS_RANK[cur[0]]:
            merged[r["ticker"]] = (r["status"], "carried")

    n_acted = sum(1 for s, _ in merged.values() if s == eca.STATUS_ACTED)
    n_not = sum(1 for s, _ in merged.values() if s == eca.STATUS_NOT_ACTED)
    n_open = sum(1 for s, _ in merged.values() if s == eca.STATUS_STILL_OPEN)
    n_unk = sum(1 for s, _ in merged.values() if s == eca.STATUS_UNKNOWN)
    n_carried = sum(1 for s, src in merged.values()
                    if src == "carried"
                    and s in (eca.STATUS_ACTED, eca.STATUS_NOT_ACTED))
    judged = n_acted + n_not
    share = int(math.floor(n_acted / judged * 100 + 0.5)) if judged else None
    return {"n_acted": n_acted, "n_not_acted": n_not, "n_still_open": n_open,
            "n_unknown": n_unk, "n_carried": n_carried, "share_pct": share}


def weekly_entry_calls(recs_df, trades_df, *, week_start, week_ending,
                       window_trading_days: int | None = None) -> dict:
    """Judge the entry calls of one debrief week as of `week_ending`.

    Returns
      {recs_status: "ok" | "unavailable",
       judge_state: "ok" | "trades_unavailable" | "trades_unreadable",
       this_week: [row], carried_over: [row], n_undated: int,
       follow: {n_acted, n_not_acted, n_still_open, n_unknown, n_carried,
                share_pct | None},
       matched_trade_ids: [...], window_trading_days: N,
       acted_rule_version: ACTED_RULE_VERSION}
      row = {ticker, status, reason, rec_types, conviction, verdict,
             times_surfaced, call_dates [ISO], window_end ISO | None,
             matched_trade_date ISO | None, matched_trade_id}
      a carried_over row also has carried_from: "still_open" | "unknown", the
      status its name had at week_start - 1
    (a caller may add `end_week_pct` to a this_week row for `entry_signal_lines`).

    `recs_df` None, not a frame, or a non-empty frame missing ticker / rec_date /
    rec_type reads "unavailable" (never a quiet week); an empty frame is "ok".
    Only entry-call types are judged. Calls dated after `week_ending` are never
    passed to the rule. THIS WEEK = calls dated week_start..week_ending, cutoff
    `week_ending`. CARRIED OVER = calls before `week_start` still open at
    `week_start - 1` (or unreadable then, with a window reaching `week_start`),
    re-judged at `week_ending`. Carry-over is by NAME: the call's ticker must
    read the same status (still_open / unknown) in the roll-up over last week's
    calls, so a name last week's debrief printed as acted or not acted is never
    carried (a call acted within its own week, or whose window closed by then,
    is never carried over).
    """
    ws, we = _as_date(week_start), _as_date(week_ending)
    if ws is None or we is None:
        raise ValueError("week_start / week_ending must be dates")
    n = _window(window_trading_days)
    judge_state = _JUDGE_BY_JOURNAL_STATE.get(
        eca._prepare_journal(trades_df)[0], JUDGE_TRADES_UNREADABLE)
    out = {
        "recs_status": RECS_UNAVAILABLE, "judge_state": judge_state,
        "this_week": [], "carried_over": [], "n_undated": 0,
        "follow": _follow([], []), "matched_trade_ids": [],
        "window_trading_days": n,
        "acted_rule_version": eca.ACTED_RULE_VERSION,
    }
    if recs_df is None or not isinstance(recs_df, pd.DataFrame):
        return out
    if len(recs_df) and any(c not in recs_df.columns for c in _REQUIRED_RECS_COLS):
        return out
    out["recs_status"] = RECS_OK
    if len(recs_df) == 0:
        return out

    this_calls: list[dict] = []
    prior_calls: list[dict] = []
    n_undated = 0
    for rec in recs_df.to_dict("records"):
        if not _entry_type(rec.get("rec_type")) or not _text(rec.get("ticker")):
            continue
        rd = _as_date(rec.get("rec_date"))
        if rd is None:
            n_undated += 1
            continue
        call = dict(rec)
        call["_rd"] = rd
        call["id"] = _clean_id(rec.get("id"))
        if rd > we:
            continue
        (this_calls if rd >= ws else prior_calls).append(call)
    out["n_undated"] = n_undated

    this_res = eca.classify_calls(this_calls, trades_df, cutoff=we,
                                  window_trading_days=n)
    this_rows = _build_rows(this_res, this_calls)

    carried_rows: list[dict] = []
    if prior_calls:
        before = eca.classify_calls(prior_calls, trades_df,
                                    cutoff=ws - timedelta(days=1),
                                    window_trading_days=n)
        # By NAME, as last week's debrief printed it: the roll-up over last
        # week's calls (week_start - 7 .. week_start - 1, cutoff week_start - 1).
        last_start = ws - timedelta(days=7)
        name_status = {
            r["ticker"]: r["status"] for r in eca.per_name(
                [res for res in before
                 if res["rec_date"] is not None and res["rec_date"] >= last_start])}
        still: list[dict] = []
        carried_from: dict[str, str] = {}
        # An unknown call on a name reading still_open cannot arise under one
        # journal: trades unavailable / unreadable makes every call unknown, and
        # trade_date_unreadable is decided per ticker before the still-open step.
        for c, r in zip(prior_calls, before):
            st = r["status"]
            if st == eca.STATUS_UNKNOWN:
                # carried only while its window reaches the new week
                if r["window_end"] is None or r["window_end"] < ws:
                    continue
            elif st != eca.STATUS_STILL_OPEN:
                continue
            ns = name_status.get(r["ticker"])
            # a call older than last week has no last-week roll-up: its own
            # status stands (never dropped silently)
            if (st if ns is None else ns) != st:
                continue
            still.append(c)
            prev = carried_from.get(r["ticker"])
            # one ticker's carried calls share one status; if they ever did
            # not, the neutral "unknown" wording stays true for every call
            carried_from[r["ticker"]] = (
                st if prev is None or prev == st else eca.STATUS_UNKNOWN)
        if still:
            after = eca.classify_calls(still, trades_df, cutoff=we,
                                       window_trading_days=n)
            carried_rows = _build_rows(after, still)
            for row in carried_rows:
                row["carried_from"] = carried_from.get(row["ticker"])

    out["this_week"] = this_rows
    out["carried_over"] = carried_rows
    out["follow"] = _follow(this_rows, carried_rows)
    ids: list = []
    for r in this_rows + carried_rows:
        if r["status"] == eca.STATUS_ACTED and r["matched_trade_id"] is not None \
                and r["matched_trade_id"] not in ids:
            ids.append(r["matched_trade_id"])
    out["matched_trade_ids"] = ids
    return out


# ── the bullets ─────────────────────────────────────────────────────────────

def _type_label(rec_types) -> str:
    have = {str(t) for t in (rec_types or [])}
    return " + ".join(lbl for key, lbl in _TYPE_LABELS if key in have)


def _entry_line(row: dict, n: int, *, carried: bool) -> str:
    label = _type_label(row.get("rec_types")) or "Entry"
    bits = [b for b in (_text(row.get("conviction")), _text(row.get("verdict"))) if b]
    paren = f" ({', '.join(bits)})" if bits else ""
    try:
        k = int(row.get("times_surfaced") or 1)
    except (TypeError, ValueError):
        k = 1
    dates = [d for d in _rows_of(row.get("call_dates")) if d]
    last = _fmt_date(max(dates)) if dates else None
    if k <= 1:
        when = f" on {last}" if last else ""
    else:
        scope = "" if carried else " this week"
        when = f" surfaced {k}×{scope}, last on {last}" if last else \
               f" surfaced {k}×{scope}"
    if not carried:
        tail = ""
    elif row.get("carried_from") == eca.STATUS_STILL_OPEN:
        tail = " (still open at last week's end)"
    else:       # unknown at last week's end (or not recorded): stay neutral
        tail = " (its decision window ran into this week)"
    head = f"• {row['ticker']}: {label} call{paren}{when}{tail}."

    status = row.get("status")
    wend = row.get("window_end")
    if status == eca.STATUS_ACTED:
        bought = row.get("matched_trade_date")
        text = (f"Acted on: you bought on {_fmt_date(bought)}." if bought
                else "Acted on: you bought inside the decision window.")
        pct = row.get("end_week_pct")
        if not carried and _finite(pct):
            text += f" Ended the week {float(pct):+.1f}%."
    elif status == eca.STATUS_NOT_ACTED:
        ended = f" (window ended {_fmt_date(wend)})" if wend else ""
        if k <= 1:
            after = "" if n <= 0 else f" or the {_days_phrase(n)} after it"
            text = f"Not acted on: no buy on the call day{after}{ended}."
        else:
            ended = f" (last window ended {_fmt_date(wend)})" if wend else ""
            text = (f"Not acted on: no buy within the decision window of any "
                    f"of its {k} calls{ended}.")
    elif status == eca.STATUS_STILL_OPEN:
        ends = f" ends {_fmt_date(wend)}" if wend else ""
        text = (f"Still open (decision window{ends}); next week's debrief says "
                f"whether you acted.")
    else:
        why = unknown_reason_text(row.get("reason"))
        text = f"Not judged: {why}."
    return f"{head} {text}"


def entry_signal_lines(entry) -> list[str]:
    """The debrief's entry-signal bullets, plain text (no markdown characters).

    `entry` is a `weekly_entry_calls` result (a caller may add `end_week_pct` to
    its this_week rows). Every bullet starts "• "; headers are bare lines.
    recs unavailable -> [] (the existing RECS_UNAVAILABLE_NOTE discloses it).
    """
    if not isinstance(entry, dict) or entry.get("recs_status") != RECS_OK:
        return []
    n = _window(entry.get("window_trading_days"))
    this_week = _rows_of(entry.get("this_week"))
    carried = _rows_of(entry.get("carried_over"))
    try:
        n_undated = int(entry.get("n_undated") or 0)
    except (TypeError, ValueError):
        n_undated = 0

    lines = ["Entry signals this week:"]
    for row in this_week:
        lines.append(_entry_line(row, n, carried=False))
    if not this_week and not n_undated:
        lines.append("• No entry signals (new positions, adds to winners, "
                     "Enter Now) were surfaced this week.")
    if n_undated:
        lines.append(f"• {n_undated} entry signal row(s) had no readable "
                     f"date and are not shown.")
    if carried:
        lines.append("Carried over from last week:")
        for row in carried:
            lines.append(_entry_line(row, n, carried=True))
    return lines


# ── behavioural fingerprint rows ────────────────────────────────────────────

def behavioural_rows(hist_df, trades_df, *, cutoff) -> "list[dict] | None":
    """Rows for `behavioral_fingerprint`, judged by the shared rule.

    None when `trades_df` or `hist_df` is None, or a non-empty `hist_df` lacks
    ticker / rec_date / rec_type (it cannot be judged). Otherwise one
    {momentum_score, composite_score, acted_on} per entry call dated on or
    before `cutoff`: acted (and not credit-deduped) -> True, not_acted ->
    False; still-open, unknown, credit-deduped and excluded calls are dropped
    (C4 contract of `entry_call_action.apply_to_matched`).
    """
    cut = _as_date(cutoff)
    if cut is None:
        raise ValueError(f"cutoff is not a date: {cutoff!r}")
    if trades_df is None or hist_df is None or not isinstance(hist_df, pd.DataFrame):
        return None
    if len(hist_df) == 0:
        return []
    if any(c not in hist_df.columns for c in _REQUIRED_RECS_COLS):
        return None

    rows: list[dict] = []
    for rec in hist_df.to_dict("records"):
        tk = _text(rec.get("ticker")).upper()
        rd = _as_date(rec.get("rec_date"))
        if not tk or rd is None or rd > cut:
            continue
        rows.append({
            "id": _clean_id(rec.get("id")), "ticker": tk,
            "rec_date": rd.isoformat(),
            "rec_type": str(rec.get("rec_type") or "").strip(),
            "surfaced_at": rec.get("surfaced_at"),
            "momentum_score": rec.get("momentum_score"),
            "composite_score": rec.get("composite_score"),
            "acted_on": False, "acted_trade": None,
        })
    matched = eca.apply_to_matched(rows, hist_df, trades_df, cutoff=cut)
    if matched is None:
        return None
    out: list[dict] = []
    for r in matched:
        status = r.get("entry_call_status")
        if status == eca.STATUS_ACTED and not r.get("credit_deduped"):
            acted = True
        elif status == eca.STATUS_NOT_ACTED:
            acted = False
        else:
            continue
        out.append({"momentum_score": r.get("momentum_score"),
                    "composite_score": r.get("composite_score"),
                    "acted_on": acted})
    return out


# ── archived-debrief caption ────────────────────────────────────────────────

def acted_rule_caption(v) -> "str | None":
    """A note for a debrief saved under an older acted rule; None for the
    current one. None / NaN / 1 / unreadable all read as the older rule."""
    try:
        if float(v) >= eca.ACTED_RULE_VERSION:
            return None
    except (TypeError, ValueError):
        pass
    n = _window(None)
    return ("Entry signals in this debrief used the older acted rule: any trade "
            "in the name, a buy or a sell, from the week's start counted as "
            "acted, and Enter Now calls weren't listed. Newer debriefs count "
            f"only a buy on the call day or within {_days_phrase(n)}.")


# ── strip AI-written entry bullets (UD-5) ───────────────────────────────────

def drop_ai_entry_bullets(text, entry_tickers: Iterable[str],
                          keep_tickers: Iterable[str] = ()) -> str:
    """Remove AI bullet lines that open with an entry-signal ticker.

    The app writes the entry bullets itself, so a model bullet for an entry name
    would repeat or contradict them. Tickers in `keep_tickers` (the protective
    and closed lists) keep their bullet. Lines that are not bullets, bullets
    for other names, and bullets whose ticker is not followed by a delimiter
    (":", "(", a dash) are untouched. None -> "".
    """
    if text is None:
        return ""
    text = str(text)
    keep = {_text(t).upper() for t in (keep_tickers or ())}
    drop = sorted({_text(t).upper() for t in (entry_tickers or ())} - keep - {""})
    if not drop:
        return text
    # the ticker must be followed by a delimiter, so prose that merely starts
    # with a short ticker ("• A TRIM signal on TSLA ...") is kept
    pat = re.compile(r"^\s*[•\-*]\s*\**\s*(?:%s)\**\s*(?=[:(—–]|-+\s)"
                     % "|".join(re.escape(t) for t in drop))
    return "\n".join(ln for ln in text.split("\n") if not pat.match(ln))
