"""An unreadable trade journal holds new buys (buy-check C5 chunk B, SoldTodayLeak;
docs/plans/entry-fail-closed-c5-spec.md section 4; owner QB1-QB4 2026-11-27).

"Sold today" (``sold_today.tickers_sold_on``) is how a just-exited name is kept from
coming straight back as a buy.  When the trade journal could not be read, "nothing
sold today" is not a fact: Grow Today holds the whole day (``trades_unread``) and the
Watchlist / email / Analysis hold the name.  ``None`` (couldn't read) and ``set()``
(read, nothing sold) are never collapsed.  A known sell keeps the existing per-name
path.

Mirrors ``macro_read``.  Pure: no clock (every date comes from the caller or the
journal), no streamlit, no db.  It must NOT import ``entry_eligibility`` (that module
imports this one).  Never raises.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from stock_analyzer import sold_today as _sold
from stock_analyzer import tone_freshness as _tf

CHECK_UNREAD = "trades_unread"
CHECK_SOLD_TODAY = "sold_today"

_GROW_REASON = "your trade journal couldn't be read"


def unread_reason() -> str:
    """The per-name hold reason when the journal couldn't be read."""
    return ("your trade journal couldn't be read, so the app can't tell whether "
            "you sold this name today")


def entry_hold() -> dict:
    """The per-name hold ``check_journal`` returns when the journal was unread (an
    abstention: no ledger gate)."""
    return {
        "kind": "hold", "check": CHECK_UNREAD, "gate_id": None,
        "reason": unread_reason(),
        "clears_when": "the trade journal can be read — re-checked every time you open this page",
        "gate_value": None, "gate_threshold": None,
    }


def grow_hold(tone_hold=None) -> dict:
    """Grow Today's whole-day hold dict (strings only: JSON-safe).  A
    ``tone_not_today`` ``tone_hold`` is merged: the banner names both conditions
    and the session is copied."""
    clears = "when the trade journal can be read again"
    session = None
    if isinstance(tone_hold, dict) and tone_hold.get("check") == _tf.CHECK_NOT_TODAY:
        t_clears = tone_hold.get("clears_when")
        if t_clears:
            clears = f"{clears}, and {t_clears}"
        session = tone_hold.get("session")
    return {
        "check": CHECK_UNREAD,
        "reason": _GROW_REASON,
        "clears_when": clears,
        "banner": (f"⏸ New picks, ETF picks and adds are held: {_GROW_REASON}, so Grow "
                   f"Today can't tell which names you sold today (it never suggests "
                   f"buying one back the same day). They return {clears}."),
        "session": session,
    }


def retry_caption(hold) -> "str | None":
    """The retry hint under Grow Today's banner for a journal hold, else None."""
    if isinstance(hold, dict) and hold.get("check") == CHECK_UNREAD:
        return ("🏠 Home re-reads the journal each time it reloads; the picks return "
                "on the first reload that can read it.")
    return None


def failure_line(payload=None, entry_run=None) -> "str | None":
    """The cron failure line when this run's journal was unread, else None.

    Fires when ``payload["diag"]["entry_hold"]`` is ``trades_unread`` (Grow Today held
    the day), ``payload["diag"]["trades_read"]`` is False (the run's strict read; a bear
    day returns before the journal hold, so entry_hold alone misses it) or
    ``entry_run["trades_available"]`` is False (the Watchlist run's own strict read).
    Never raises."""
    try:
        unread = False
        diag = payload.get("diag") if isinstance(payload, dict) else None
        if isinstance(diag, dict) and (diag.get("entry_hold") == CHECK_UNREAD
                                       or diag.get("trades_read") is False):
            unread = True
        if not unread and isinstance(entry_run, dict) \
                and entry_run.get("trades_available") is False:
            unread = True
        if unread:
            return ("trades_unread: the trade journal couldn't be read — no buy-list, "
                    "intraday pick or Watchlist Enter Now this run")
        return None
    except Exception:
        return None


def analysis_sold_today(decision, ticker) -> "dict | None":
    """A not-held Buy the owner sold today: a copy of ``decision`` with a
    ``sold_today`` hold prepended and no size.  Only a decision whose entry check ran
    (``entry_checked is True``) is touched; anything else comes back as given.  Never
    raises (a malformed decision comes back as given)."""
    try:
        if not isinstance(decision, dict) or decision.get("entry_checked") is not True:
            return decision
        tk = str(ticker or "").strip().upper() or "this name"
        reason = (f"you sold {tk} today — the app doesn't suggest buying a name back "
                  f"the same day")
        hold = {"check": CHECK_SOLD_TODAY, "gate_id": None, "reason": reason,
                "clears_when": "the next ET trading day",
                "gate_value": None, "gate_threshold": None}
        holds = decision.get("holds")
        out = dict(decision)
        out["offer_size"] = False
        out["hold"] = hold
        out["holds"] = [hold] + ([] if holds is None else list(holds))
        out["reason_if_no_size"] = reason
        return out
    except Exception:
        return decision


def _day_text(d) -> str:
    return f"{d.strftime('%a %b')} {d.day}"


def _et_date(value):
    """The America/New_York calendar date of a buy's ``traded_at`` (a string, a
    datetime / Timestamp, or a date), else None.  An aware value is converted to ET; a
    naive one is read as ET wall clock."""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        if ts.tzinfo is not None:
            ts = ts.tz_convert("America/New_York")
        return ts.date()
    except Exception:
        return None


def same_day_sell_note(ticker, trades_or_none, buy_traded_at, today=None) -> "str | None":
    """Trade Journal BUY-confirm awareness (never blocks): a note when the owner also
    SOLD this ticker on the buy's own ET date, "couldn't be checked" when the journal
    was unread, else None.

    The date is the buy's ``traded_at`` in ET; when it is missing the caller's ``today``
    (an ET date) stands in, and with neither the journal can't be compared (an unread
    journal still says so, on "that day").  Never raises."""
    try:
        tk = str(ticker or "").strip().upper()
        if not tk:
            return None
        day = _et_date(buy_traded_at)
        if day is None and isinstance(today, date):
            day = today
        when = _day_text(day) if day is not None else "that day"
        unread_note = (f"Note: whether you sold {tk} on {when} couldn't be checked "
                       f"(the trade journal couldn't be read).")
        if trades_or_none is None:
            return unread_note
        if day is None:
            return None
        sold = _sold.tickers_sold_on(trades_or_none, day)
        if sold is None:
            return unread_note
        if tk in sold:
            return (f"Note: you also sold {tk} on {when}. The app doesn't suggest buying "
                    f"a name back the same day you sold it; this note doesn't stop you "
                    f"logging a trade you made.")
        return None
    except Exception:
        return None
