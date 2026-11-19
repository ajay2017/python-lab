"""
The monthly Decision Review's entry-call judgement, on the shared entry-call rule.

Spec: docs/plans/acted-rule-c4-spec.md section 2B. The rule itself (what "acted"
means, the decision window) lives in `entry_call_action`; this module decides
WHICH calls belong to a calendar month, rolls them up by name, builds the two
views the report needs, and words the app-written lines, so the report's counts
and its still-open / not-judged names are fixed by code and the AI never
restates (or contradicts) whether a call was acted on.

  build_month          judge one period's entry calls; counts, names, viz.
  band_rows / verdict_rows  engine rows for the prompt (call-day price).
  signal_lines         plain-text bullets appended to Signal discipline.
  rule_note            the one-paragraph "what acted means" footnote.
  acted_rule_caption   the "older acted rule" / "recomputed today" caption.
  scope_words          version-aware UI phrases for the tiles and charts.

NAME ROLL-UP (owner decision 2026-11-18, replaces spec UD-5). A name is judged
on its CLOSED calls: acted > unknown > not_acted (skipped) > still_open. A name
reads still_open only when EVERY one of its calls was still open at `period_end`;
a name with any closed not-acted call and no buy is skipped (its open call stays
out of the judged view). This is local to the monthly: `entry_call_action.per_name`
(acted > still_open > unknown > not_acted) is the weekly debrief's carry-over rule
and is not changed.

TWO VIEWS (spec UD-1 B). "judged" = the owner's view: only names that read
acted or not_acted, priced from the FILL for a bought call. "engine" = every
entry call of the period (still-open, unknown and credit-deduped included),
priced from its call-day `price_at_surface` whether or not the owner bought,
so the band chart never carries the owner's timing. SPY is measured from the
call date in both. The judging cut-off is `period_end`, never `as_of` (U6), so a
regenerated report reads the same; `as_of` only dates the outcome prices.

Pure / no Streamlit / no DB / no clock.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Mapping

import pandas as pd

from stock_analyzer import debrief_entry_calls as dec
from stock_analyzer import entry_call_action as eca
from stock_analyzer import recommendations_history as rh

JUDGE_OK = dec.JUDGE_OK
JUDGE_RECS_UNAVAILABLE = "recs_unavailable"
JUDGE_RECS_UNREADABLE = "recs_unreadable"
JUDGE_TRADES_UNAVAILABLE = dec.JUDGE_TRADES_UNAVAILABLE
JUDGE_TRADES_UNREADABLE = dec.JUDGE_TRADES_UNREADABLE

PRICE_BASIS_CALL_DAY = "call_day"

# Display wording of the call types in the monthly report: one capitalisation
# ("New Position" / "Add to Winner" / "Enter Now") across the bullets, the rule
# note and `scope_words`. Local on purpose: `debrief_entry_calls._TYPE_LABELS`
# is shared with the weekly email, whose wording must not change.
_MONTHLY_TYPE_LABELS = (("new_pick", "New Position"),
                        ("add_winner", "Add to Winner"),
                        ("enter_now", "Enter Now"))

_REQUIRED_RECS_COLS = ("ticker", "rec_date", "rec_type")


# ── small helpers ───────────────────────────────────────────────────────────

def _as_date(v) -> date | None:
    return eca._call_date(v)


def _need_date(v, name: str) -> date:
    d = _as_date(v)
    if d is None:
        raise ValueError(f"{name} is not a date: {v!r}")
    return d


def _is_v2(viz) -> bool:
    """True when a stored viz was built under the current acted rule. None, a
    missing key, NaN, 1 and anything unreadable all read as the older rule."""
    if not isinstance(viz, Mapping):
        return False
    try:
        return float(viz.get("acted_rule_version")) >= eca.ACTED_RULE_VERSION
    except (TypeError, ValueError):
        return False


def _entry_block(viz) -> Mapping:
    e = viz.get("entry") if isinstance(viz, Mapping) else None
    return e if isinstance(e, Mapping) else {}


def _int_of(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _type_label(rec_types) -> str:
    """The call types joined with " + ", in the monthly report's wording."""
    have = set(rec_types or ())
    return " + ".join(lbl for key, lbl in _MONTHLY_TYPE_LABELS if key in have)


def _refused(state: str, n: int) -> dict:
    """A non-ok result: no counts and no viz, never "all missed"."""
    return {"judge_state": state, "counts": None, "names": [],
            "judged": [], "engine": [], "viz": None,
            "window_trading_days": n}


# ── the monthly name roll-up ────────────────────────────────────────────────

def _roll_up(rows: list[dict]) -> list[dict]:
    """One row per ticker, sorted by ticker, from adapter rows
    {ticker, status, rec_date, window_end, rec_type, id} (no excluded and no
    credit-deduped rows).

    Precedence: acted > unknown > not_acted > still_open (see the module note).
    Keys: ticker, status, n_calls (every call), rec_types (sorted unique, every
    call), last_call_date (latest of every call), window_end (acted: the
    credited call's; still_open: the latest still-open call's; unknown /
    not_acted: the latest of the unknown / closed not-acted calls)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["ticker"]].append(r)

    def _latest_end(rs):
        ends = [r["window_end"] for r in rs if r.get("window_end") is not None]
        return max(ends) if ends else None

    out: list[dict] = []
    for tk in sorted(groups):
        rs = groups[tk]
        by_status: dict[str, list[dict]] = defaultdict(list)
        for r in rs:
            by_status[r["status"]].append(r)
        if eca.STATUS_ACTED in by_status:
            status = eca.STATUS_ACTED
            credited = min(by_status[eca.STATUS_ACTED],
                           key=lambda r: (r["rec_date"] or date.max,
                                          eca._id_key(r.get("id"))))
            wend = credited["window_end"]
        elif eca.STATUS_UNKNOWN in by_status:
            status = eca.STATUS_UNKNOWN
            wend = _latest_end(by_status[eca.STATUS_UNKNOWN])
        elif eca.STATUS_NOT_ACTED in by_status:
            status = eca.STATUS_NOT_ACTED
            wend = _latest_end(by_status[eca.STATUS_NOT_ACTED])
        else:
            status = eca.STATUS_STILL_OPEN
            wend = _latest_end(by_status[eca.STATUS_STILL_OPEN])
        dates = [r["rec_date"] for r in rs if r.get("rec_date") is not None]
        out.append({
            "ticker": tk, "status": status, "n_calls": len(rs),
            "rec_types": sorted({r["rec_type"] for r in rs}),
            "last_call_date": max(dates) if dates else None,
            "window_end": wend,
        })
    return out


def _call_windows(recs_df, n: int) -> dict[str, list[tuple[date, date]]]:
    """{TICKER: [(call date, decision-window end)]} for EVERY entry-type call in
    `recs_df`, earlier months' calls included (the caller loads back to
    `recs_load_start(period_start)`), an already-held Enter Now excluded exactly
    as the judged rule does. Used only to tell a BUY a call already credited from
    one that no call's window covers."""
    out: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for row in recs_df.to_dict("records"):
        rec_type = str(row.get("rec_type", "") or "").strip()
        if rec_type not in eca.ENTRY_CALL_REC_TYPES:
            continue
        tk = eca._norm_text(row.get("ticker"))
        rd = _as_date(row.get("rec_date"))
        if not tk or rd is None:
            continue
        if rec_type == "enter_now" and eca._truthy_flag(row.get("already_held")):
            continue
        out[tk].append((rd, eca.window_end(rd, n)))
    return dict(out)


def _bought_outside(by_ticker: dict, ticker: str, ps: date, pe: date,
                    windows: "list[tuple[date, date]] | None" = None) -> list[str]:
    """ISO ET dates (sorted, unique) of the ticker's BUYs dated `ps..pe`, from the
    journal `entry_call_action._prepare_journal` builds, that NO entry call's
    decision window covers (`windows` from `_call_windows`; a BUY on or inside
    any call's window, an earlier month's call included, is that call's credit,
    not "outside"). A SELL is not in the journal; a BUY with no readable date is
    skipped."""
    ds = {b["date"] for b in by_ticker.get(ticker, [])
          if b.get("date") is not None and ps <= b["date"] <= pe
          and not any(rd <= b["date"] <= we for rd, we in (windows or ()))}
    return [d.isoformat() for d in sorted(ds)]


# ── the monthly judgement ───────────────────────────────────────────────────

def build_month(recs_df, trades_df, *, period_start, period_end, as_of,
                current_prices, spy_close_by_date, min_days,
                window_trading_days: int | None = None) -> dict:
    """Judge one period's entry calls as of `period_end`.

    Returns
      {judge_state: "ok" | "recs_unavailable" | "recs_unreadable" |
                    "trades_unavailable" | "trades_unreadable",
       counts: {n_surfaced, n_acted, n_not_acted, n_open, n_unknown,
                n_undated} | None,
       names: [{ticker, status, n_calls, rec_types, last_call_date ISO,
                window_end ISO | None, reason,
                bought_outside [ISO ET dates; only a skipped name has any]}],
       judged: [enriched rows, the owner's view],
       engine: [enriched rows, call-day price],
       viz: JSON-safe dict | None,
       window_trading_days: N}
    Any non-ok state carries no counts, empty row lists and viz None.

    Raises ValueError when `as_of < period_end` (the outcomes would be measured
    to a date before the period's own calls were judged) or when
    `period_start > period_end`: both are caller bugs, never a data state.

    recs None -> recs_unavailable; a non-frame or a non-empty frame without
    ticker / rec_date / rec_type -> recs_unreadable; the journal state decides
    the trades_* states. An empty recs frame is ok with zero counts.

    Only entry-call types dated `period_start..period_end` are judged (a call
    after `period_end` is never passed on). A call with no readable date is
    counted in `n_undated` and dropped. Already-held Enter Now calls are
    excluded (U8). The period's names roll up on their CLOSED calls by
    `_roll_up` (acted > unknown > not_acted > still_open): a name is "still open"
    only when every call was, and a still-open or unknown name is in neither the
    bought nor the skipped count (UD-2 A; replaces UD-5, owner 2026-11-18). A
    skipped name's judged rows are its not-acted rows only. A skipped name that
    was bought this month outside every call's window (an earlier month's call
    included: `recs_df` may reach back to `recs_load_start(period_start)`, and
    only `period_start..period_end` calls are judged) carries `bought_outside`
    (and is listed in `viz["entry"]["bought_outside_names"]`); it still counts
    as skipped.
    """
    ps = _need_date(period_start, "period_start")
    pe = _need_date(period_end, "period_end")
    ao = _need_date(as_of, "as_of")
    if ao < pe:
        raise ValueError(f"as_of {ao} is before period_end {pe}")
    if ps > pe:
        raise ValueError(f"period_start {ps} is after period_end {pe}")
    n = eca._window(window_trading_days)

    if recs_df is None:
        return _refused(JUDGE_RECS_UNAVAILABLE, n)
    if not isinstance(recs_df, pd.DataFrame):
        return _refused(JUDGE_RECS_UNREADABLE, n)
    if len(recs_df) and any(c not in recs_df.columns for c in _REQUIRED_RECS_COLS):
        return _refused(JUDGE_RECS_UNREADABLE, n)
    journal_state = eca._prepare_journal(trades_df)[0]
    if journal_state != "ok":
        return _refused(dec._JUDGE_BY_JOURNAL_STATE.get(
            journal_state, JUDGE_TRADES_UNREADABLE), n)

    # The matcher is used only for the row shape (its same-day acted flag is
    # overwritten by `apply_to_matched` below).
    matched = rh.match_recs_to_trades(recs_df, trades_df)
    kept: list[dict] = []
    n_undated = 0
    for row in matched:
        if row.get("rec_type") not in eca.ENTRY_CALL_REC_TYPES or not row.get("ticker"):
            continue
        rd = _as_date(row.get("rec_date"))
        if rd is None:
            # An already-held Enter Now is not an entry call at all (U8), so it
            # is not an "undated entry call" either: let the shared rule say.
            held = eca.apply_to_matched([row], recs_df, trades_df, cutoff=pe,
                                        window_trading_days=n)
            if held is not None and held[0].get(
                    "entry_call_status") == eca.STATUS_EXCLUDED:
                continue
            n_undated += 1
            continue
        if rd < ps or rd > pe:
            continue
        new = dict(row)
        new["id"] = dec._clean_id(row.get("id"))      # pd.NA -> None
        kept.append(new)

    rows = eca.apply_to_matched(kept, recs_df, trades_df, cutoff=pe,
                                window_trading_days=n)
    if rows is None:        # cannot happen: both frames were checked above
        return _refused(JUDGE_TRADES_UNAVAILABLE, n)
    rows = [r for r in rows if r.get("entry_call_status") != eca.STATUS_EXCLUDED]

    # Names. A credit-deduped row is dropped: its (ticker, date) winner is acted,
    # so the roll-up never has to read a deduped row's (None) matched trade.
    adapter = [{
        "ticker": r["ticker"], "status": r["entry_call_status"],
        "rec_date": r["rec_date"], "window_end": r.get("entry_call_window_end"),
        "rec_type": r["rec_type"], "id": r.get("id"),
    } for r in rows if not r.get("credit_deduped")]
    rolled = _roll_up(adapter)

    unknown_reason: dict[str, str | None] = {}
    for r in sorted(rows, key=lambda r: (r["rec_date"], eca._id_key(r.get("id")))):
        if r["entry_call_status"] == eca.STATUS_UNKNOWN:
            unknown_reason[r["ticker"]] = r.get("entry_call_reason")

    # A skipped name's BUYs dated inside the period but outside every call's
    # decision window ("bought outside the window"): a label only, the counts
    # and the rule are unchanged.
    by_ticker = eca._prepare_journal(trades_df)[1]
    windows = _call_windows(recs_df, n)

    names: list[dict] = []
    status_of: dict[str, str] = {}
    for rn in rolled:
        status_of[rn["ticker"]] = rn["status"]
        names.append({
            "ticker": rn["ticker"], "status": rn["status"],
            "n_calls": rn["n_calls"], "rec_types": list(rn["rec_types"]),
            "last_call_date": dec._iso(rn["last_call_date"]),
            "window_end": dec._iso(rn["window_end"]),
            "reason": (unknown_reason.get(rn["ticker"])
                       if rn["status"] == eca.STATUS_UNKNOWN else None),
            "bought_outside": (
                _bought_outside(by_ticker, eca._norm_text(rn["ticker"]), ps, pe,
                                windows.get(eca._norm_text(rn["ticker"])))
                if rn["status"] == eca.STATUS_NOT_ACTED else []),
        })

    # The owner's view: rows of an acted / not_acted NAME that read an arm.
    judged_in: list[dict] = []
    for r in rows:
        side = eca.arm(r)
        if side is None or status_of.get(r["ticker"]) not in (
                eca.STATUS_ACTED, eca.STATUS_NOT_ACTED):
            continue
        judged_in.append(dict(r, acted_on=(side == "acted")))
    judged = rh.compute_outcomes(judged_in, current_prices, today=ao,
                                 spy_close_by_date=spy_close_by_date,
                                 min_days=min_days)

    # The engine view (UD-1 B): every call at its call-day price.
    engine = rh.compute_outcomes(
        [dict(r, acted_on=False, acted_trade=None) for r in rows],
        current_prices, today=ao, spy_close_by_date=spy_close_by_date,
        min_days=min_days)

    def _count(status: str) -> int:
        return sum(1 for s in status_of.values() if s == status)

    counts = {
        "n_surfaced": len(names),
        "n_acted": _count(eca.STATUS_ACTED),
        "n_not_acted": _count(eca.STATUS_NOT_ACTED),
        "n_open": _count(eca.STATUS_STILL_OPEN),
        "n_unknown": _count(eca.STATUS_UNKNOWN),
        "n_undated": n_undated,
    }

    viz = rh.report_viz_snapshot(judged, rec_types=eca.ENTRY_CALL_REC_TYPES)
    viz["bands"] = [{"band": b["band"], "avg_alpha": b["avg_alpha"]}
                    for b in rh.by_composite_band(engine)
                    if b.get("avg_alpha") is not None]
    viz["avg_skipped_names_alpha"] = rh.skipped_names_avg_alpha(
        judged, eca.ENTRY_CALL_REC_TYPES)
    viz["as_of"] = ao.isoformat()
    viz["acted_rule_version"] = eca.ACTED_RULE_VERSION
    viz["window_trading_days"] = n
    viz["entry"] = {
        "rec_types": list(eca.ENTRY_CALL_REC_TYPES),
        "n_surfaced": counts["n_surfaced"],
        "n_open": counts["n_open"],
        "n_unknown": counts["n_unknown"],
        "n_undated": counts["n_undated"],
        "price_basis": PRICE_BASIS_CALL_DAY,
        "open_names": [
            {"ticker": x["ticker"], "rec_types": x["rec_types"],
             "n_calls": x["n_calls"], "last_call_date": x["last_call_date"],
             "window_end": x["window_end"]}
            for x in names if x["status"] == eca.STATUS_STILL_OPEN],
        "unknown_names": [
            {"ticker": x["ticker"], "rec_types": x["rec_types"],
             "n_calls": x["n_calls"], "last_call_date": x["last_call_date"],
             "reason": x["reason"]}
            for x in names if x["status"] == eca.STATUS_UNKNOWN],
        "bought_outside_names": [
            {"ticker": x["ticker"], "buy_dates": list(x["bought_outside"]),
             "rec_types": x["rec_types"], "last_call_date": x["last_call_date"]}
            for x in names if x["bought_outside"]],
    }
    return {"judge_state": JUDGE_OK, "counts": counts, "names": names,
            "judged": judged, "engine": engine, "viz": viz,
            "window_trading_days": n}


# ── engine rows for the prompt ──────────────────────────────────────────────

def _acted_rate_by_label(buckets: list[dict], label_key: str) -> dict:
    return {b[label_key]: b.get("action_rate") for b in buckets}


def band_rows(engine: list[dict], judged: list[dict]) -> list[dict]:
    """Composite-band rows for the prompt: n_total / n_priced / avg_alpha from the
    ENGINE view (call-day price), `action_rate` from the same-label bucket of the
    JUDGED view (None when that bucket has no judged row), so "acted X%" never
    counts a still-open or not-judged call."""
    rates = _acted_rate_by_label(rh.by_composite_band(judged), "band")
    return [{"band": b["band"], "n_total": b["n_total"], "n_priced": b["n_priced"],
             "avg_alpha": b["avg_alpha"], "action_rate": rates.get(b["band"])}
            for b in rh.by_composite_band(engine)]


def verdict_rows(engine: list[dict], judged: list[dict]) -> list[dict]:
    """`band_rows` for the verdict buckets (`recommendations_history.by_verdict`)."""
    rates = _acted_rate_by_label(rh.by_verdict(judged), "verdict")
    return [{"verdict": b["verdict"], "n_total": b["n_total"],
             "n_priced": b["n_priced"], "avg_alpha": b["avg_alpha"],
             "action_rate": rates.get(b["verdict"])}
            for b in rh.by_verdict(engine)]


# ── the app-written lines ───────────────────────────────────────────────────

def _name_line(item: Mapping, tail: str) -> str:
    label = _type_label(item.get("rec_types")) or "Entry"
    k = _int_of(item.get("n_calls")) or 1
    last = item.get("last_call_date")
    if k <= 1:
        when = f" on {dec._fmt_date(last)}" if last else ""
    else:
        when = (f" surfaced {k}× this month, last on {dec._fmt_date(last)}"
                if last else f" surfaced {k}× this month")
    return f"• {item.get('ticker')}: {label} call{when}. {tail}"


# At most this many buy dates are spelled out in a bullet; the rest are counted.
_MAX_BUY_DATES_SHOWN = 4


def _buy_dates_text(dates) -> str:
    """"Tue Oct 13, Wed Oct 14 and Thu Oct 15"; past the display cap the rest are
    counted: "A, B, C, D and 2 more"."""
    ds = [dec._fmt_date(d) for d in dec._rows_of(dates)]
    if not ds:
        return "an unreadable date"
    shown, more = ds[:_MAX_BUY_DATES_SHOWN], len(ds) - _MAX_BUY_DATES_SHOWN
    if more > 0:
        return ", ".join(shown) + f" and {more} more"
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + f" and {shown[-1]}"


def _bought_outside_line(item: Mapping) -> str:
    last = item.get("last_call_date")
    tail = f"; its last call was {dec._fmt_date(last)}" if last else ""
    return (f"• {item.get('ticker')}: bought "
            f"{_buy_dates_text(item.get('buy_dates'))}{tail}.")


def signal_lines(viz) -> list[str]:
    """The bullets appended to the end of Signal discipline (UD-3), plain text
    (no markdown characters). [] for a non-v2 viz or when there is nothing to
    list. Every bullet starts "• "; headers are bare lines."""
    if not _is_v2(viz):
        return []
    entry = _entry_block(viz)
    lines: list[str] = []

    open_names = dec._rows_of(entry.get("open_names"))
    if open_names:
        lines.append("Still open at the month's end "
                     "(not counted as bought or skipped):")
        for it in open_names:
            wend = it.get("window_end")
            tail = f"Decision window ends {dec._fmt_date(wend)}." if wend else ""
            lines.append(_name_line(it, tail).rstrip())

    unknown_names = dec._rows_of(entry.get("unknown_names"))
    if unknown_names:
        lines.append("Not judged (not counted as bought or skipped):")
        for it in unknown_names:
            why = dec.unknown_reason_text(it.get("reason"))
            lines.append(_name_line(it, f"Not judged: {why}."))

    outside = dec._rows_of(entry.get("bought_outside_names"))
    if outside:
        lines.append("Bought this month, but not within a call's decision window "
                     "(counted as skipped):")
        for it in outside:
            lines.append(_bought_outside_line(it))

    n_undated = _int_of(entry.get("n_undated"))
    if n_undated > 0:
        lines.append(f"• {n_undated} entry call row(s) had no readable date "
                     f"and are not counted.")
    return lines


def rule_note(viz, *, live: bool = False) -> "str | None":
    """The footnote explaining the acted rule on a current-rule report; None for
    an older one. N is read from the constant at call time.

    live=True (the app recomputed the counts today for a report saved before
    freezing, so its Bought tile is the stored older-rule figure) drops the
    clause about the bought average's fill price."""
    if not _is_v2(viz):
        return None
    n = eca._window(None)
    days = dec._days_phrase(n)
    entry = _entry_block(viz)
    o, u = _int_of(entry.get("n_open")), _int_of(entry.get("n_unknown"))
    fill = ("" if live else
            f"; the bought average uses your fill price, up to {days} after the call")
    return (
        f"Acted on = a buy of that name on the call day or within {days} after "
        f"it (New Position, Add to Winner and Enter Now calls; an Enter Now on a "
        f"name you already held is left out). {o} name(s) whose every call was "
        f"still open at the month's end and {u} not judged are in neither count; "
        f"a name with any closed call is judged on its closed calls. "
        f"The band chart prices every call from its call-day price{fill}. "
        f"📜 Recommendations History and the other pages that count acted calls "
        f"still use the older rule (a same-day trade tagged as a recommendation), "
        f"so their counts can differ."
    )


def band_price_note(viz) -> str:
    """The sentence the band chart's caption adds under UD-1 B: every call is
    measured from its call-day price. "" for a viz that doesn't say so (an older
    report's bands were priced from the fill for a bought call)."""
    if _entry_block(viz).get("price_basis") == PRICE_BASIS_CALL_DAY:
        return " Each call is measured from its call-day price, whether or not you bought it."
    return ""


def acted_rule_caption(viz, *, live: bool) -> "str | None":
    """A note on which acted rule a report's counts used.

    live=True (the app recomputed the counts today for a report saved before
    freezing): says so. Otherwise None for a current-rule frozen viz, and the
    older-rule note for None / missing / NaN / 1 / unreadable."""
    n = eca._window(None)
    days = dec._days_phrase(n)
    if live:
        return ("Counts and charts were recomputed today with the current acted "
                f"rule (a buy on the call day or within {days}). The text below "
                "and the bought average were saved when the report was generated, "
                "under the older rule, so they can differ.")
    if _is_v2(viz):
        return None
    return ("This report used the older acted rule: only a same-day trade tagged "
            "as a recommendation counted as acted, and only New Position calls "
            "were counted. Newer reports count a buy on the call day or within "
            f"{days}, and include Add to Winner and Enter Now calls.")


# The nine Sankey node labels, in the order `app._signal_flow_sankey` draws them
# (surfaced, acted, not acted, win / loss / flat of acted, rose / fell / flat of
# not acted). v1 is the older rule's wording, exactly as the shared builder (and
# 📜 Recommendations History) has always drawn it.
_SANKEY_LABELS_V1 = ("New Positions Surfaced", "Acted", "Not acted",
                     "Win (acted)", "Loss (acted)", "Flat / Open (acted)",
                     "Rose (missed)", "Fell — dodged", "Flat / Open (missed)")
_SANKEY_LABELS_V2 = ("Entry calls judged", "Bought", "Skipped",
                     "Win (bought)", "Loss (bought)", "Flat / Open (bought)",
                     "Rose (skipped)", "Fell (skipped)", "Flat / Open (skipped)")


def scope_words(viz) -> dict:
    """Version-aware UI phrases: `names`, `flow_title`, `missed_scope`,
    `sankey_labels` (the nine Sankey node labels), `skipped_avg_scope` (the tile
    caption's description of the skipped names), `rose_phrase` / `fell_phrase`
    (the missed-bar caption), plus `current_rule` (True when the viz was built
    under the current acted rule), so the renderer can pick the matching wording
    without re-deciding the version."""
    if _is_v2(viz):
        return {
            "current_rule": True,
            "sankey_labels": _SANKEY_LABELS_V2,
            "skipped_avg_scope": "the names you didn't buy within a call's "
                                 "decision window",
            "rose_phrase": "rose (skipped)",
            "fell_phrase": "fell (skipped)",
            "names": "distinct entry-call tickers "
                     "(New Position, Add to Winner, Enter Now)",
            "flow_title": "This month's signal flow — entry calls → bought / "
                          "skipped → outcome",
            "missed_scope": "entry calls (New Position, Add to Winner, Enter Now)",
        }
    return {
        "current_rule": False,
        "sankey_labels": _SANKEY_LABELS_V1,
        "skipped_avg_scope": "the names you never bought",
        "rose_phrase": "rose (missed)",
        "fell_phrase": "fell (dodged)",
        "names": "distinct New-Position tickers",
        "flow_title": "This month's signal flow — New Positions → acted / "
                      "missed → outcome",
        "missed_scope": "New Positions to Initiate",
    }
