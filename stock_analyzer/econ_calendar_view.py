"""Pure decisions behind the 📅 Economic Calendar page.

Everything the page used to decide inline lives here so ``app.py`` is render-only
and the time-context cases (same-day release, weekend visit, week boundary,
DST) are unit-tested instead of verified by screenshot:

  * ``is_done``       : is an event finished (so it belongs under Completed)?
  * ``split_events``  : Upcoming / Completed lists, using ``is_done``.
  * ``kpis``          : the KPI strip, from NOT-done events only.
  * ``upcoming_day_tag`` / ``completed_day_tag`` / ``tag_markdown``: day tags.
  * ``card_value_lines`` / ``card_value_row``: the Prev / Est / Actual text.
  * ``fred_enriched_count``: how many events FRED enriched.

Pure: no I/O, no clock reads. ``now_et`` / ``today`` are passed in; an aware
``now_et`` (ET or UTC) is converted to ET, a naive one is taken as ET (the
``macro_release`` convention). Calendar day != trading day: "this week" is the
Mon-Fri trading week, compared by Monday dates, never ISO week numbers.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from stock_analyzer.macro_calendar import HIGH as _HIGH
from stock_analyzer.macro_release import release_passed
from stock_analyzer.market_time import ET

_SEP = "  ·  "          # joins the value parts into one row, as app.py did


def _et_now(now_et):
    """``now_et`` as a naive-or-ET datetime in Eastern time (aware input is
    converted, so a UTC-aware value near midnight resolves to the right day)."""
    if getattr(now_et, "tzinfo", None) is not None:
        return now_et.astimezone(ET)
    return now_et


def _today(now_et) -> date:
    n = _et_now(now_et)
    return n.date() if isinstance(n, datetime) else n


def _is_date(d) -> bool:
    return isinstance(d, date) and not isinstance(d, datetime)


def is_done(ev, now_et) -> bool:
    """True when the event is finished: dated before today (ET), or dated today
    and either FRED already confirmed it (``released`` True) or its release time
    has passed (``release_passed(...) is True``, ``>=`` the scheduled minute).
    A same-day event with an unknown release time and no FRED confirmation is
    NOT done. A future event is never done. Never raises."""
    try:
        d = ev.get("date")
        if not _is_date(d):
            return False
        today = _today(now_et)
        if d < today:
            return True
        if d > today:
            return False
        if ev.get("released") is True:
            return True
        return release_passed(d, ev.get("time_et"), _et_now(now_et)) is True
    except Exception:
        return False


def split_events(events, now_et):
    """``(upcoming, completed)``. Upcoming keeps the input order (the calendar
    is built date/time ascending); completed is most recent date first (a stable
    sort, so a day's events keep their time order). Done = ``is_done``."""
    upcoming, completed = [], []
    for e in (events or []):
        (completed if is_done(e, now_et) else upcoming).append(e)
    completed.sort(key=lambda x: x["date"], reverse=True)
    return upcoming, completed


def _week_ahead(ev_date, today):
    """Whole trading weeks from the current one to ``ev_date``'s week: 0 = this
    week, 1 = next week. On a Sat / Sun visit "this week" is the coming Mon-Fri.
    Compares Monday dates (never ISO week numbers, which break at year end)."""
    ref_monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:                      # Sat / Sun -> the coming week
        ref_monday += timedelta(days=7)
    ev_monday = ev_date - timedelta(days=ev_date.weekday())
    return (ev_monday - ref_monday).days // 7


def upcoming_day_tag(ev_date, today) -> str:
    """Tag for an upcoming day header: ``"TODAY"``, ``"TOMORROW"``,
    ``"this week"`` (current Mon-Fri week; the coming week on a Sat / Sun
    visit), ``"next week"``, else ``""`` (also for a past or unreadable date)."""
    if not (_is_date(ev_date) and _is_date(today)):
        return ""
    delta = (ev_date - today).days
    if delta < 0:
        return ""
    if delta == 0:
        return "TODAY"
    if delta == 1:
        return "TOMORROW"
    wk = _week_ahead(ev_date, today)
    if wk <= 0:
        return "this week"
    if wk == 1:
        return "next week"
    return ""


def completed_day_tag(ev_date, today) -> str:
    """Tag for a Completed day header: ``"today"`` (never "0d ago"),
    ``"yesterday"``, else ``"Nd ago"``; ``""`` for a future / unreadable date."""
    if not (_is_date(ev_date) and _is_date(today)):
        return ""
    delta = (today - ev_date).days
    if delta < 0:
        return ""
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return f"{delta}d ago"


def tag_markdown(tag) -> str:
    """The header suffix app.py appends: `` — **TODAY**`` / `` — *this week*`` /
    ``""`` for an empty tag (bold for TODAY and TOMORROW, italic otherwise)."""
    if not tag:
        return ""
    return f" — **{tag}**" if tag in ("TODAY", "TOMORROW") else f" — *{tag}*"


def kpis(events, now_et) -> dict:
    """The KPI strip, computed from NOT-done events only (a released-today CPI
    counts nowhere and is never "Next major event").

    Keys: ``upcoming_count``, ``high_count``, ``week_count`` (not-done events in
    the current trading week, the same bucket as the "this week" tag),
    ``next_event`` (the first HIGH-impact not-done event, else the first
    not-done event, else None), ``next_name`` / ``next_days_label`` (None / ""
    when there is none)."""
    today = _today(now_et)
    fwd = [e for e in (events or []) if not is_done(e, now_et)]
    high = [e for e in fwd if e.get("impact") == _HIGH]
    week = [e for e in fwd if _is_date(e.get("date")) and e["date"] >= today
            and _week_ahead(e["date"], today) <= 0]
    nxt = high[0] if high else (fwd[0] if fwd else None)
    return {
        "upcoming_count": len(fwd),
        "high_count": len(high),
        "week_count": len(week),
        "next_event": nxt,
        "next_name": nxt.get("event") if nxt else None,
        "next_days_label": (nxt.get("days_label") or "") if nxt else "",
    }


def _esc(s) -> str:
    """Escape ``$`` so Streamlit markdown never reads a pair as LaTeX."""
    return str(s).replace("$", "\\$")


def _label_of(ev, *values):
    """The series label ("CPI YoY") for a value that can't be computed: take it
    from the sibling value string ("CPI YoY: +3.74%"), else the FRED map, else
    the event name."""
    for v in values:
        if isinstance(v, str) and ": " in v:
            return v.split(": ", 1)[0]
    try:
        from stock_analyzer.macro_calendar import _FRED_MAP
        cfg = _FRED_MAP.get(ev.get("event"))
        lab = cfg.get("label") if cfg is not None else None
        if lab:
            return lab
    except Exception:
        pass
    return ev.get("event") or "Value"


def _value_line(prefix, ev, value, period, note, sibling):
    """One ``Prev:`` / ``Actual:`` line, or None when there is nothing to show.
    The month goes AFTER the value (``**CPI YoY: +3.74%** (Sep 2026)``) so the
    first number in the bold text is the value, never the year. A value that
    can't be computed carries its month inside the label and its note after."""
    if value is not None:
        tail = f" ({_esc(period)})" if period else ""
        return f"{prefix}: **{_esc(value)}**{tail}"
    if not note:
        return None
    label = _label_of(ev, sibling)
    head, _, rest = str(note).partition(": ")
    where = f" ({period})" if period else ""
    line = f"{prefix}: **{_esc(label)}{_esc(where)}: {_esc(head)}**"
    return line + (f": {_esc(rest)}" if rest else "")


def card_value_lines(ev, done=None) -> list:
    """Prev / Est / Actual markdown parts for a calendar card, in that order
    (``card_value_row`` joins them with the page's ``  ·  `` separator).

    * Prev: whenever the event has a previous value or a not-computable note.
    * Est: only while the event is not done (an upcoming card).
    * Actual: whenever the event is done and has an actual or a note, so a
      released same-day card shows it (CalLabels (1)).
    ``done`` is the caller's ``is_done`` result; when omitted it falls back to
    the event's ``released`` flag. FRED values are escaped for ``$``."""
    if done is None:
        done = bool(ev.get("released"))
    out = []
    prev = _value_line("Prev", ev, ev.get("previous"), ev.get("previous_period"),
                       ev.get("previous_note"), ev.get("actual"))
    if prev:
        out.append(prev)
    if not done and ev.get("estimate") is not None:
        out.append(f"Est: **{_esc(ev['estimate'])}**")
    if done:
        act = _value_line("Actual", ev, ev.get("actual"), ev.get("actual_period"),
                          ev.get("actual_note"), ev.get("previous"))
        if act:
            out.append(act)
    return out


def card_value_row(ev, done=None) -> str:
    """``card_value_lines`` joined into the single row the cards show ("" if none)."""
    return _SEP.join(card_value_lines(ev, done))


def fred_enriched_count(events) -> int:
    """Events FRED enriched: an actual or a previous value, or a note explaining
    why one can't be computed (the FRED call worked, the figure just doesn't exist)."""
    n = 0
    for e in (events or []):
        if any(e.get(k) is not None for k in
               ("actual", "previous", "actual_note", "previous_note")):
            n += 1
    return n
