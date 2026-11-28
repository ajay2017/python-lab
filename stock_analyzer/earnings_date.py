"""Gate module: the ONE resolved next-earnings date (buy-check C3a,
docs/plans/entry-fail-closed-c3-spec.md, with the owner's 2026-11-27 amendments).

Yahoo serves a past earnings date for weeks after a report (NVDA 11-17 and TGT 11-18
were still on file on 11-27) and a wrong one now and then (CRWD showed 11-23 against
the real 12-01). Every reader used to take whatever the bundle's `earnings` said, so a
past date read as "no risk ahead". `resolve` is called ONCE, in the producer
(`bundle_loader.load_bundle`), from Yahoo's dates plus the FMP calendar cross-check
(`earnings_xcheck`), and every reader sees the same answer.

States of `resolve(...)["state"]`:
  not_applicable        a fund (earnings don't apply).
  date                  a confirmed-or-plausible NEXT date (today or later): the EARLIEST
                        of Yahoo's and FMP's future dates (a wrong early date costs a few
                        extra held days, never a missed report).
  reported              FMP shows a report WITH an actual EPS in the last
                        EARNINGS_REPORTED_CLEAR_DAYS days and no future date: it just
                        reported; the next date isn't on any calendar yet. Passes.
  reported_unconfirmed  Yahoo's date is older than EARNINGS_PAST_DATE_HOLD_DAYS and no
                        future date exists anywhere: it reported long ago and the next date
                        isn't announced. Passes (`date` is None; `last_reported` is the
                        Yahoo date).
  no_date               Yahoo answered and nothing is announced (a fresh listing). Passes.
  failed                a read that didn't confirm the date: reason `read_failed`,
                        `past_unconfirmed` (Yahoo's date is within the hold window and
                        FMP shows nothing), `past_unchecked` (same, but the FMP calendar
                        couldn't be read) or `stale_copy` (a saved last-good copy).

FMP's free plan covers only a fixed sample of about 70 large names, so for any other
symbol "no FMP row" is not evidence of anything; the 14-day hold is what protects those.

`date` is the resolved NEXT date as an ISO string (None for every other state), never in
the past. `display` and `held_unconfirmed_line` turn a resolved dict into text. Pure: no
clock (today is passed in), no streamlit, no db, nothing raises, inputs are never mutated.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS, EARNINGS_PAST_DATE_HOLD_DAYS, EARNINGS_REPORTED_CLEAR_DAYS,
    EARNINGS_URGENCY_SOON_DAYS, EARNINGS_XCHECK_HORIZON_DAYS,
)

STATE_NOT_APPLICABLE = "not_applicable"
STATE_DATE = "date"
STATE_REPORTED = "reported"
STATE_REPORTED_UNCONFIRMED = "reported_unconfirmed"
STATE_NO_DATE = "no_date"
STATE_FAILED = "failed"

REASON_READ_FAILED = "read_failed"
REASON_PAST_UNCONFIRMED = "past_unconfirmed"
REASON_PAST_UNCHECKED = "past_unchecked"
REASON_STALE_COPY = "stale_copy"

# The cross-check stand-in for "no usable index" (a failed / missing index).
_XC_FAILED = {"state": "failed", "upcoming": [], "reported": None}


def _parse_iso(raw) -> "date | None":
    """A date for 'YYYY-MM-DD...' (a timestamp keeps its first 10 characters), else
    None. Never raises."""
    try:
        s = str(raw)[:10]
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _short(d: date) -> str:
    """'Mon Nov 23' (no zero-padded day)."""
    return f"{d:%a %b} {d.day}"


def _result(state, *, date_=None, source=None, reason=None, yahoo_date=None,
            last_reported=None, disagree=None) -> dict:
    return {"state": state, "date": date_, "source": source, "reason": reason,
            "yahoo_date": yahoo_date, "last_reported": last_reported,
            "disagree": disagree}


def yahoo_dates(raw) -> "list[str]":
    """Sorted unique ISO dates from Yahoo's raw earnings value: a list / tuple / set of
    date-likes, a single string / date, or None. Items that don't parse are dropped.
    Never raises."""
    try:
        if raw is None:
            return []
        if isinstance(raw, (str, bytes, date)):
            items = [raw]
        elif isinstance(raw, (list, tuple, set, frozenset)):
            items = list(raw)
        else:
            return []
        out = set()
        for it in items:
            d = _parse_iso(it)
            if d is not None:
                out.add(d.isoformat())
        return sorted(out)
    except Exception:
        return []


def xcheck_for(index, ticker) -> dict:
    """The FMP cross-check slice for one ticker: {"state": "ok" | "failed", "upcoming":
    [iso], "reported": iso | None}. A missing / failed index reads failed (never an
    empty "ok"); an ok index with no row for the ticker reads ok with nothing. Looks the
    symbol up upper-cased, then with '.' and '-' swapped (BRK-B / BRK.B). Never raises."""
    try:
        if not isinstance(index, dict) or index.get("state") != "ok":
            return dict(_XC_FAILED, upcoming=[])
        by = index.get("by_ticker")
        if not isinstance(by, dict):
            return dict(_XC_FAILED, upcoming=[])
        t = str(ticker or "").strip().upper()
        row = by.get(t)
        if row is None and t:
            for alt in (t.replace(".", "-"), t.replace("-", ".")):
                if alt != t and by.get(alt) is not None:
                    row = by.get(alt)
                    break
        if not isinstance(row, dict):
            return {"state": "ok", "upcoming": [], "reported": None}
        return {"state": "ok",
                "upcoming": yahoo_dates(row.get("upcoming")),
                "reported": (_parse_iso(row.get("reported")).isoformat()
                             if _parse_iso(row.get("reported")) else None)}
    except Exception:
        return dict(_XC_FAILED, upcoming=[])


def resolve(*, is_fund, stale_copy, yahoo_dates, yahoo_status, xcheck, today) -> dict:
    """Resolve the next earnings date (see the module docstring for the states).

    Rules, in order: (1) a fund -> not_applicable; (2) a stale saved copy -> failed
    (stale_copy); (3) Yahoo dates >= today and FMP upcoming dates in [today, today +
    EARNINGS_XCHECK_HORIZON_DAYS] (FMP only when its cross-check read ok): either present
    -> `date` = the earliest; (4) FMP ok + a report with an actual EPS in [today -
    EARNINGS_REPORTED_CLEAR_DAYS, today - 1] -> reported; (5) a Yahoo date in the past:
    within EARNINGS_PAST_DATE_HOLD_DAYS -> failed (past_unconfirmed when the cross-check
    read ok, else past_unchecked), older -> reported_unconfirmed; (6) Yahoo status
    "no_date" -> no_date; (7) anything else -> failed (read_failed).

    `today` is an ET date (the caller's `today_et()`); `yahoo_dates` is the output of the
    module's `yahoo_dates()` (a list of ISO strings); `xcheck` the output of `xcheck_for`.
    Never raises: any bad input reads failed / read_failed."""
    try:
        if is_fund:
            return _result(STATE_NOT_APPLICABLE)
        ydates = sorted({d for d in (_parse_iso(x) for x in (yahoo_dates or []))
                         if d is not None})
        if stale_copy:
            return _result(STATE_FAILED, reason=REASON_STALE_COPY)
        if not isinstance(today, date) or isinstance(today, datetime):
            return _result(STATE_FAILED, reason=REASON_READ_FAILED)
        xc = xcheck if isinstance(xcheck, dict) else _XC_FAILED
        xc_ok = xc.get("state") == "ok"
        horizon = today + timedelta(days=EARNINGS_XCHECK_HORIZON_DAYS)

        y_future = [d for d in ydates if d >= today]
        f_future = []
        if xc_ok:
            up = xc.get("upcoming")
            f_future = sorted(
                d for d in (_parse_iso(x) for x in (up if isinstance(up, (list, tuple)) else []))
                if d is not None and today <= d <= horizon)
        y_past = [d for d in ydates if d < today]
        y_last_past = y_past[-1] if y_past else None

        # 3. a future date from either source: the EARLIEST wins.
        if y_future or f_future:
            y_first = y_future[0] if y_future else None
            f_first = f_future[0] if f_future else None
            chosen = min(d for d in (y_first, f_first) if d is not None)
            if y_first is not None and f_first is not None:
                source = "both" if y_first == f_first else (
                    "yahoo" if chosen == y_first else "fmp")
                disagree = ({"yahoo": y_first.isoformat(), "fmp": f_first.isoformat()}
                            if y_first != f_first else None)
            else:
                source = "yahoo" if y_first is not None else "fmp"
                disagree = None
            return _result(STATE_DATE, date_=chosen.isoformat(), source=source,
                           yahoo_date=(y_first or y_last_past).isoformat()
                           if (y_first or y_last_past) else None,
                           disagree=disagree)

        # 4. FMP shows a report with an actual EPS recently, no future date anywhere.
        if xc_ok:
            rep = _parse_iso(xc.get("reported"))
            if rep is not None and (today - timedelta(days=EARNINGS_REPORTED_CLEAR_DAYS)
                                    <= rep <= today - timedelta(days=1)):
                return _result(STATE_REPORTED, source="fmp",
                               yahoo_date=y_last_past.isoformat() if y_last_past else None,
                               last_reported=rep.isoformat())

        # 5. Yahoo still shows a past date and nothing confirms a next one.
        if y_last_past is not None:
            if y_last_past >= today - timedelta(days=EARNINGS_PAST_DATE_HOLD_DAYS):
                return _result(
                    STATE_FAILED,
                    reason=REASON_PAST_UNCONFIRMED if xc_ok else REASON_PAST_UNCHECKED,
                    yahoo_date=y_last_past.isoformat())
            return _result(STATE_REPORTED_UNCONFIRMED, source="yahoo",
                           yahoo_date=y_last_past.isoformat(),
                           last_reported=y_last_past.isoformat())

        # 6. Yahoo answered and announced nothing.
        if yahoo_status == STATE_NO_DATE:
            return _result(STATE_NO_DATE)

        # 7. couldn't tell.
        return _result(STATE_FAILED, reason=REASON_READ_FAILED)
    except Exception:
        return _result(STATE_FAILED, reason=REASON_READ_FAILED)


def _days_between(iso, today) -> "int | None":
    d = _parse_iso(iso)
    if d is None or not isinstance(today, date):
        return None
    t = today.date() if isinstance(today, datetime) else today
    return (d - t).days


def display(resolved, today) -> dict:
    """{"date_text", "days", "urgency", "note"} for one resolved dict.

    urgency: imminent (0 to EARNINGS_IMMINENT_DAYS days), soon (to
    EARNINGS_URGENCY_SOON_DAYS), ahead (beyond), reported (reported / reported_unconfirmed),
    unconfirmed (failed, or a `date` that has passed since the data loaded), none
    (no_date, or nothing resolved), n/a (a fund). `date_text` is the ISO date when there
    is a next date, else "-"; `days` is whole days to the next date (None otherwise);
    `note` is a plain-text line for the reported / unconfirmed / no-date cases.
    Never raises; no `$` and no `**` in any text."""
    blank = {"date_text": "—", "days": None, "urgency": "none", "note": None}
    try:
        if not isinstance(resolved, dict):
            return dict(blank)
        state = resolved.get("state")
        if state == STATE_NOT_APPLICABLE:
            return {"date_text": "n/a", "days": None, "urgency": "n/a", "note": None}
        if state == STATE_DATE:
            days = _days_between(resolved.get("date"), today)
            d = _parse_iso(resolved.get("date"))
            if days is None or d is None:
                return {"date_text": "—", "days": None, "urgency": "unconfirmed",
                        "note": "the earnings date couldn't be placed on the calendar"}
            if days < 0:
                return {"date_text": d.isoformat(), "days": days, "urgency": "unconfirmed",
                        "note": f"the earnings date on file ({_short(d)}) has passed since "
                                "the data loaded"}
            urgency = ("imminent" if days <= EARNINGS_IMMINENT_DAYS
                       else "soon" if days <= EARNINGS_URGENCY_SOON_DAYS else "ahead")
            note = None
            dis = resolved.get("disagree")
            if isinstance(dis, dict):
                y, f = _parse_iso(dis.get("yahoo")), _parse_iso(dis.get("fmp"))
                if y is not None and f is not None:
                    note = (f"Yahoo shows {_short(y)}, the FMP calendar {_short(f)}; "
                            "the earlier date is used.")
            return {"date_text": d.isoformat(), "days": days, "urgency": urgency,
                    "note": note}
        if state in (STATE_REPORTED, STATE_REPORTED_UNCONFIRMED):
            rep = _parse_iso(resolved.get("last_reported"))
            when = f"reported on {_short(rep)}" if rep is not None else "reported recently"
            tail = ("the next date isn't on the calendar yet" if state == STATE_REPORTED
                    else "the next date isn't announced yet")
            return {"date_text": "—", "days": None, "urgency": "reported",
                    "note": f"{when}; {tail}"}
        if state == STATE_NO_DATE:
            return {"date_text": "—", "days": None, "urgency": "none",
                    "note": "no earnings date announced yet"}
        if state == STATE_FAILED:
            reason = resolved.get("reason")
            y = _parse_iso(resolved.get("yahoo_date"))
            if reason == REASON_PAST_UNCONFIRMED and y is not None:
                note = (f"the earnings date on file ({_short(y)}) has passed and the next "
                        "one couldn't be confirmed")
            elif reason == REASON_PAST_UNCHECKED and y is not None:
                note = (f"the earnings date on file ({_short(y)}) has passed and the "
                        "earnings calendar couldn't be read to confirm the next one")
            elif reason == REASON_STALE_COPY:
                note = "a saved copy of the data can't show the earnings date"
            else:
                note = "the next earnings date couldn't be read"
            return {"date_text": "—", "days": None, "urgency": "unconfirmed",
                    "note": note}
        return dict(blank)
    except Exception:
        return dict(blank)


def held_unconfirmed_line(ticker, resolved, weight, floor_pct) -> "str | None":
    """The one-line Review caption for a HELD name whose earnings date is unconfirmed
    (spec Q6; wired in C3b): None unless `resolved` is a failed read and `weight` /
    `floor_pct` are finite numbers. `floor_pct` is the earnings-week line the caller's
    rule uses (never chosen here). Plain text: no `$`, no `**`. Never raises."""
    try:
        if not isinstance(resolved, dict) or resolved.get("state") != STATE_FAILED:
            return None
        w, fl = float(weight), float(floor_pct)
        if not (math.isfinite(w) and math.isfinite(fl)) or isinstance(weight, bool):
            return None
        t = str(ticker or "").strip().upper()
        if not t:
            return None
        y = _parse_iso(resolved.get("yahoo_date"))
        reason = resolved.get("reason")
        if reason in (REASON_PAST_UNCONFIRMED, REASON_PAST_UNCHECKED) and y is not None:
            why = (f"the date on file ({_short(y)}) has passed and no next date could be "
                   "confirmed")
        elif reason == REASON_STALE_COPY:
            why = "a saved copy of the data can't show the date"
        else:
            why = "the next date couldn't be read"
        return (f"Earnings date unconfirmed for {t}: {why}. If it reports this week, its "
                f"{w:.1f}% is above your {fl:.1f}% earnings-week line; confirm the date "
                "on the company's investor-relations page.")
    except Exception:
        return None
