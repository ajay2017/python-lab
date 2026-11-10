"""
Log-time checks for a hand-entered trade (bug register TJDate, chunk C1).

THE INCIDENTS. Hand entry in the Trade Journal went wrong three ways in six
days (docs/plans/trade-entry-guard.md): a next-day log during market hours
(10-28, wrong date), OKTA re-logged at 17:08 instead of the 14:43 fill (11-02,
wrong time), and OKTA at 171.88 instead of 189.14 (11-02, wrong price).
Nothing compared the entry with the market or with Robinhood's own fills.

WHAT THIS MODULE IS. Pure decisions only. No streamlit, no database, no network,
no clock read: every function that needs "now" takes `now_et` (a tz-aware
datetime) from its caller. The TIME half is wired into the Trade Journal form by
chunk C2a (`default_trade_date`, `fill_time_requirement`, `resolve_traded_at`,
`time_findings`, `same_ticker_same_day`, `logging_as_text`,
`sell_replay_finding`, and the form helpers `trade_date_widget_reset`,
`fill_time_missing_text`). The price and broker halves are not wired yet; chunk C2b
does that and owns the fetching (today's / a recent day's bar low / high from
`data.fetch_session_range`, falling back to the `data.fetch_unadjusted_history`
frame, split-adjusted and dividend-unadjusted, for older dates, and
`db.load_snaptrade_unlinked_fills_or_none` for the broker's fills). Plain text
only in every `text` field: the caller escapes it and puts `$` as `&#36;`.

  * Time: `session_hours`, `default_trade_date`, `fill_time_requirement`,
    `resolve_traded_at`, `time_findings` (NON_TRADING_DATE / OUTSIDE_RTH are
    ticks, FUTURE is a hard block), `same_ticker_same_day`, `logging_as_text`,
    `fill_time_caption`, `pick_date_caption`, `trade_date_widget_reset`,
    `fill_time_missing_text`.
  * Replay: `sell_replay_finding`, the as-of-time hard block for a SELL that
    `db.recalculate_from_trades` would replay before the BUY that covers it.
  * Price: `price_band_finding`, a logged price against that session's
    split-adjusted, dividend-unadjusted low-high band. Never a silent in-band:
    anything it can't judge is `unchecked` with a reason.
  * Broker: `pending_fill_findings`, the entry against Robinhood fills that are
    not yet linked to a journal row.

TRADING CALENDAR. Provider-free on purpose: `stock_analyzer.data` imports
yfinance and the provider orchestrator, and this module has to stay importable
from a pure-logic test. The three small calendar helpers below apply the SAME
rule as `data.is_trading_day` / `previous_trading_day` / `trading_sessions_between`
(weekday and not in `constants.NYSE_HOLIDAYS`), the way `snapshot_row_repair`
does; `tests/test_trade_entry_checks.py` pins them against the `data` functions
so the two can't drift.

OWNER TRADES IN EXTENDED HOURS (P0 probe, 2026-11-06), so OUTSIDE_RTH fires on
legitimate fills such as GOOGL 1 @ 300.99 at 4:06 PM EDT on Oct 28. That is why
it is a tick ("I've checked, record it anyway"), never a block.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time as _time, timedelta

import pandas as pd

from stock_analyzer import trade_time
from stock_analyzer.constants import (
    BROKER_DRIFT_SHARE_TOL,
    IMPORTED_TRADE_ANCHOR_ET_HOUR,
    JOURNAL_MATCH_PRICE_TOL_USD,
    JOURNAL_MATCH_WINDOW_SESSIONS,
    NYSE_EARLY_CLOSES,
    NYSE_HOLIDAYS,
    NYSE_REGULAR_CLOSE_ET,
    NYSE_REGULAR_OPEN_ET,
    TRADE_LOG_PRICE_BAND_TOL_PCT,
    TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS,
)
from stock_analyzer.market_time import ET, et_anchor_iso

# Finding kinds (time)
KIND_NON_TRADING_DATE = "NON_TRADING_DATE"
KIND_OUTSIDE_RTH = "OUTSIDE_RTH"
KIND_FUTURE = "FUTURE"
# Finding kinds (journal replay)
KIND_SELL_BEFORE_BUY = "SELL_BEFORE_BUY"
KIND_SELL_REPLAY_UNCHECKED = "SELL_REPLAY_UNCHECKED"

# Shown instead of staging when the form's trade date was reset for a new ET day
# (or by the 09:30 flip) in the very run the owner pressed Record: what they saw
# may not be what the server now holds.
DATE_RESET_NOTICE = ("The trade date was reset. Check the date and "
                     "fill time, then press Record again.")
# Finding kinds (broker fills)
KIND_MATCH_SAME_DAY = "MATCH_SAME_DAY"
KIND_OTHER_DATE = "OTHER_DATE"
KIND_OTHER_DATE_SPLIT = "OTHER_DATE_SPLIT"
KIND_SHARES_SHORT = "SHARES_SHORT"

SEV_HARD = "hard"   # blocks the entry
SEV_TICK = "tick"   # needs the "I've checked, record it anyway" tick
SEV_INFO = "info"   # tells the form what to do, not a warning

REQ_DEFAULT_NOW = "default_now"
REQ_REQUIRED = "required"
REQ_OPTIONAL = "optional"

STATE_IN_BAND = "in_band"
STATE_OUTSIDE = "outside"
STATE_UNCHECKED = "unchecked"


# ── trading calendar (provider-free; same rule as data.is_trading_day) ───────

def _is_trading_day(d: date) -> bool:
    """Weekday and not an NYSE full-day closure. Early closes are trading days."""
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def _previous_trading_day(d: date) -> date:
    """The last session strictly before `d`; `d` itself need not be one."""
    p = d - timedelta(days=1)
    while not _is_trading_day(p):
        p -= timedelta(days=1)
    return p


def _sessions_between(a: date, b: date) -> int:
    """NYSE sessions in the half-open span (earlier, later]; order-independent."""
    lo, hi = (a, b) if a <= b else (b, a)
    n = 0
    d = lo + timedelta(days=1)
    while d <= hi:
        if _is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


# ── small coercions ──────────────────────────────────────────────────────────

def _as_date(v) -> "date | None":
    """A calendar `date`, or None when unreadable.

    A `date` and a bare `YYYY-MM-DD` string are taken as they are. Anything
    carrying a time (an ISO string with a time part, an aware datetime, a
    `pd.Timestamp`) is converted to ET FIRST, so 2026-10-29T00:30:00Z reads as
    Oct 28, never the UTC date. A naive `datetime` has no zone to convert and
    keeps its own date.
    """
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.date()
        return v.astimezone(ET).date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            if ":" in s:
                return _to_et_datetime(s).date()
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _num(v) -> "float | None":
    """A finite float, or None for None / NaN / inf / bool / unparseable."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _aware_et(now_et) -> datetime:
    """`now_et` as an ET datetime. A naive value is a caller bug (it would be
    read in the server's zone, UTC on the hosted containers): ValueError."""
    if not isinstance(now_et, datetime) or now_et.tzinfo is None:
        raise ValueError("now_et must be a timezone-aware datetime")
    return now_et.astimezone(ET)


def _to_et_datetime(value) -> datetime:
    """A stored `traded_at` (ISO string / datetime / Timestamp) as an ET
    datetime. A naive value is read as UTC, the convention of `trade_time`.
    ValueError when unparseable, and for a date-only string (no time part: a
    midnight-UTC reading would be the previous evening in ET): a gate must not
    read garbage as "fine"."""
    if isinstance(value, str) and ":" not in value:
        raise ValueError(f"traded_at has no time part: {value!r}")
    try:
        ts = pd.to_datetime(value, utc=True, errors="coerce", format="ISO8601")
    except (TypeError, ValueError):
        ts = None
    if ts is None or pd.isna(ts):
        raise ValueError(f"unreadable traded_at: {value!r}")
    return ts.tz_convert("America/New_York").to_pydatetime()


def _fmt_date(d: date) -> str:
    """'Sat Nov 7' (no leading zero on the day)."""
    return f"{d:%a %b} {d.day}"


def _fmt_clock(h: int, m: int) -> str:
    """'4:06 PM' (12-hour, no leading zero)."""
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _fmt_dt_clock(dt: datetime) -> str:
    return _fmt_clock(dt.hour, dt.minute)


def _fmt_shares(x: float) -> str:
    """Shares without trailing zeros: 5, 0.25, 8."""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _fmt_price(x: float) -> str:
    """A price to cents, no currency symbol (the caller adds one)."""
    return f"{x:.2f}"


# ── time ─────────────────────────────────────────────────────────────────────

def session_hours(d) -> "tuple[datetime, datetime] | None":
    """The regular session of ET date `d` as (open, close), tz-aware ET.

    None on a weekend or NYSE holiday. Open is `NYSE_REGULAR_OPEN_ET`; close is
    `NYSE_REGULAR_CLOSE_ET`, or the early close from `NYSE_EARLY_CLOSES`
    (float hours, 13.0 = 1:00 PM) on a half day. Built with `localize`, so the
    offset is right on both sides of a DST change.
    """
    d = _as_date(d)
    if d is None:
        raise ValueError("session_hours needs a date")
    if not _is_trading_day(d):
        return None
    oh, om = NYSE_REGULAR_OPEN_ET
    early = NYSE_EARLY_CLOSES.get(d.isoformat())
    if early is not None:
        total_min = int(round(float(early) * 60))
        ch, cm = divmod(total_min, 60)
    else:
        ch, cm = NYSE_REGULAR_CLOSE_ET
    return (
        ET.localize(datetime(d.year, d.month, d.day, int(oh), int(om))),
        ET.localize(datetime(d.year, d.month, d.day, int(ch), int(cm))),
    )


def default_trade_date(now_et) -> "date | None":
    """The trade date the form should offer (owner decision U2, option (c),
    2026-11-09).

    * Today is a trading day and `now_et` is at or past the open: today.
    * Today is a trading day and `now_et` is before the open (pre-market):
      None. The form pre-fills NO date and the owner must pick one, because a
      pre-market log is either yesterday's after-hours fill or today's
      pre-market fill and the app can't tell which.
    * Today is a weekend or holiday: the previous trading day.
    `now_et` must be tz-aware (ValueError otherwise).
    """
    now = _aware_et(now_et)
    today = now.date()
    hours = session_hours(today)
    if hours is not None:
        return today if now >= hours[0] else None
    return _previous_trading_day(today)


def fill_time_requirement(trade_date, now_et, same_ticker_same_day: bool) -> str:
    """Whether the form must ask for the fill time (owner decision U1).

    * "default_now": the date is today and the regular session is open
      (open <= now <= close): the time defaults to now, editable.
    * "required": the date is today but the market is closed (before the open,
      after the close, a weekend or a holiday), OR the date is in the future
      (the time is then needed so `time_findings` can block it as FUTURE), OR
      the date is past and another same-ticker row already exists on that ET
      date (`same_ticker_same_day`), where a 16:00 anchor would order wrongly,
      OR (owner decision U2 option (c), 2026-11-09) today is a trading day and
      `now_et` is before that day's open: ANY trade date, because pre-open is
      when the extended-hours date and time are ambiguous.
    * "optional": any other past date; the 16:00 ET anchor is used. Logging a
      past date on a weekend or holiday stays "optional" on purpose: the
      owner's decision covers trading-day pre-open only.
    """
    now = _aware_et(now_et)
    d = _as_date(trade_date)
    if d is None:
        raise ValueError("fill_time_requirement needs a trade date")
    today = now.date()
    if d > today:
        return REQ_REQUIRED
    today_hours = session_hours(today)
    if today_hours is not None and now < today_hours[0]:
        return REQ_REQUIRED
    if d == today:
        hours = today_hours
        if hours is not None and hours[0] <= now <= hours[1]:
            return REQ_DEFAULT_NOW
        return REQ_REQUIRED
    return REQ_REQUIRED if same_ticker_same_day else REQ_OPTIONAL


def resolve_traded_at(trade_date, fill_time, now_et,
                      same_ticker_same_day: bool = False) -> str:
    """The tz-aware ISO `traded_at` to store for a hand-entered trade.

    * A given `fill_time` (a `datetime.time`) is that ET wall-clock time on
      `trade_date`; its tzinfo, if any, is ignored (the form asks in ET).
    * No `fill_time` on a past date: the 16:00 ET date-only anchor
      (`market_time.et_anchor_iso`, the implementation `trade_time.anchored_iso`
      and the importers share, hour `IMPORTED_TRADE_ANCHOR_ET_HOUR`).
    * No `fill_time` on today with the market open: `now_et`.
    ValueError for a naive `now_et`, and for no `fill_time` when
    `fill_time_requirement(trade_date, now_et, same_ticker_same_day)` says
    "required", so the whole U1 rule is enforced here (a past date with another
    same-ticker row that ET date needs its time too).
    """
    now = _aware_et(now_et)
    d = _as_date(trade_date)
    if d is None:
        raise ValueError("resolve_traded_at needs a trade date")
    if fill_time is not None:
        if not isinstance(fill_time, _time):
            raise ValueError("fill_time must be a datetime.time or None")
        wall = datetime.combine(d, fill_time.replace(tzinfo=None))
        return ET.localize(wall).isoformat()
    req = fill_time_requirement(d, now, same_ticker_same_day)
    if req == REQ_REQUIRED:
        raise ValueError(f"a fill time is required for {d.isoformat()}")
    if req == REQ_DEFAULT_NOW:
        return now.isoformat()
    return et_anchor_iso(d)


def time_findings(traded_at, now_et, time_supplied: bool = False) -> list:
    """Findings about WHEN a trade is dated: `[{"kind", "severity", "text"}]`.

    * FUTURE (hard): `traded_at` is later than `now_et`.
    * NON_TRADING_DATE (tick): its ET date is a weekend or NYSE holiday. This is
      then the only time finding besides FUTURE (no OUTSIDE_RTH on top of it).
    * OUTSIDE_RTH (tick): its ET time is before the open or after the close.
      Regular hours are open <= t <= close, CLOSE INCLUSIVE (closing-auction
      prints at 16:00:00); an early-close day closes at its early close. The
      16:00 ET date-only anchor (`trade_time.is_placeholder_time`) is an
      app-invented time and never fires this, UNLESS `time_supplied` is True:
      then the owner typed that time (a real 16:00 fill on an early-close day
      such as 2026-11-27, 13:00 close, is outside regular hours) and it is
      checked like any other time.
    `traded_at` is an ISO string / datetime (naive is read as UTC, the
    `trade_time` convention); ValueError if unreadable. `now_et` must be aware.
    Order: FUTURE, then the date or time finding.
    """
    now = _aware_et(now_et)
    t = _to_et_datetime(traded_at)
    d = t.date()
    out: list = []

    if t > now:
        out.append({
            "kind": KIND_FUTURE, "severity": SEV_HARD,
            "text": f"{_fmt_date(d)}, {_fmt_dt_clock(t)} ET is in the future",
        })

    hours = session_hours(d)
    if hours is None:
        out.append({
            "kind": KIND_NON_TRADING_DATE, "severity": SEV_TICK,
            "text": f"{_fmt_date(d)} is not a trading day",
        })
        return out

    if not time_supplied and trade_time.is_placeholder_time(traded_at):
        return out
    open_dt, close_dt = hours
    if t < open_dt or t > close_dt:
        early = d.isoformat() in NYSE_EARLY_CLOSES
        window = f"{_fmt_dt_clock(open_dt)}–{_fmt_dt_clock(close_dt)}"
        out.append({
            "kind": KIND_OUTSIDE_RTH, "severity": SEV_TICK,
            "text": (f"{_fmt_dt_clock(t)} ET is outside regular hours ({window}"
                     f"{', early close' if early else ''})"),
        })
    return out


def same_ticker_same_day(journal, ticker, trade_date) -> "bool | None":
    """Whether the journal already holds a row for `ticker` on ET date `trade_date`.

    `journal` is a trades frame (columns `ticker`, `traded_at`) or None. Three
    states: None when it can't be judged (journal None, not a frame, a frame
    missing a column, or an unreadable `trade_date`): callers treat None as
    True, i.e. fail closed and ask for the fill time. Otherwise True / False.
    A blank `ticker` matches nothing (False); the form re-checks with the real
    ticker at submit. The row's date is `trade_time.et_trade_date` (the ET date
    of the stored value, idempotent on a raw or a normalized `traded_at`); a row
    with an unreadable `traded_at` is ignored.
    """
    if journal is None or not isinstance(journal, pd.DataFrame):
        return None
    d = _as_date(trade_date)
    if d is None:
        return None
    if journal.empty:
        return False
    if "ticker" not in journal.columns or "traded_at" not in journal.columns:
        return None
    tk = str(ticker or "").strip().upper()
    if not tk:
        return False
    mask = journal["ticker"].astype(str).str.strip().str.upper() == tk
    for v in journal.loc[mask, "traded_at"]:
        if trade_time.et_trade_date(v) == d:
            return True
    return False


def logging_as_text(traded_at, today_et, time_supplied: bool) -> str:
    """The confirm card's date-in-words line (owner decision 3 of 2026-11-02).

    "Logging as Thu Nov 5, 2:43 PM ET" when `time_supplied` or the time is not
    the 16:00 ET date-only placeholder; "Logging as Thu Nov 5 (no fill time:
    placed at 4:00 PM ET)" when it is the placeholder and the owner gave no
    time. ", 2025" follows the day only when the ET year differs from
    `today_et`'s. Plain text, no `$`. ValueError if `traded_at` is unreadable.
    """
    t = _to_et_datetime(traded_at)
    today = _as_date(today_et)
    day = _fmt_date(t.date())
    if today is not None and t.year != today.year:
        day = f"{day}, {t.year}"
    if time_supplied or not trade_time.is_placeholder_time(traded_at):
        return f"Logging as {day}, {_fmt_dt_clock(t)} ET"
    anchor = _fmt_clock(int(IMPORTED_TRADE_ANCHOR_ET_HOUR), 0)
    return f"Logging as {day} (no fill time: placed at {anchor} ET)"


def fill_time_caption(requirement) -> str:
    """The one-line caption under the form's fill-time box for a
    `fill_time_requirement` result. An unknown value reads as the strictest
    ("required"), never as a blank that implies the time can be skipped."""
    if requirement == REQ_DEFAULT_NOW:
        return "Leave blank to use the time you press Record."
    if requirement == REQ_OPTIONAL:
        anchor = _fmt_clock(int(IMPORTED_TRADE_ANCHOR_ET_HOUR), 0)
        return f"Optional: blank places it at {anchor} ET."
    return "Required: enter the fill time from Robinhood (ET)."


def pick_date_caption(now_et) -> str:
    """The caption shown while the form has no trade date. Before the open on a
    trading day (when the form pre-fills nothing, owner decision U2 option (c))
    it explains the choice; at any other time it is the plain prompt."""
    now = _aware_et(now_et)
    hours = session_hours(now.date())
    if hours is not None and now < hours[0]:
        oh, om = NYSE_REGULAR_OPEN_ET
        return (f"Before {_fmt_clock(int(oh), int(om))} ET: pick the trade date "
                "(yesterday's after-hours fill or today's pre-market fill).")
    return "Pick the trade date."


def trade_date_seeded_stale(seeded_on, now_et) -> bool:
    """True when the ET date the trade-date widget was last used on
    (`seeded_on`, a date or ISO string; None = never) is not today's ET date:
    a tab left open overnight must not carry yesterday's date and fill time
    into a new session. An unreadable `seeded_on` is not stale."""
    now = _aware_et(now_et)
    d = _as_date(seeded_on)
    return d is not None and d != now.date()


def trade_date_widget_reset(stored_value, seeded_on, now_et) -> bool:
    """Whether the form must drop the stored trade date so the default applies.

    True when the stored date is from an earlier ET day (`trade_date_seeded_stale`),
    or when the stored value is None (left blank across the 09:30 open, or
    cleared after it) while `default_trade_date(now_et)` now has a date to offer.
    False when the owner's pick is from today, and when the value is None before
    the open (the form deliberately offers no date then).
    """
    if trade_date_seeded_stale(seeded_on, now_et):
        return True
    return stored_value is None and default_trade_date(now_et) is not None


def fill_time_missing_text(trade_date, now_et, journal_unreadable: bool) -> str:
    """The error when `resolve_traded_at` refuses for want of a fill time.

    The plain message, except when the journal couldn't be read and the date is
    a past one outside the pre-open window (where the requirement would be
    "optional" but for the unread same-ticker check): then it says why the time
    is required.
    """
    d = _as_date(trade_date)
    if (journal_unreadable and d is not None
            and fill_time_requirement(d, now_et, False) == REQ_OPTIONAL):
        return ("Enter the fill time (ET): the journal couldn't be read to check "
                "for other trades in this ticker that day, so the time is required.")
    return "Enter the fill time (ET) — it's required for this date."


# ── journal replay ───────────────────────────────────────────────────────────

def _replay_rows(journal, tk: str) -> "list | None":
    """The journal rows for `tk` as replay entries, or None when the frame
    can't be read. Mirrors the row filter of `db.recalculate_from_trades`:
    shares and price must be positive; the action decides SPLIT / BUY / SELL
    (`"SPLIT" in action` first, then BUY, then SELL)."""
    if journal is None or not isinstance(journal, pd.DataFrame):
        return None
    if journal.empty:
        return []
    for col in ("ticker", "action", "shares", "price", "traded_at"):
        if col not in journal.columns:
            return None
    df = journal[journal["ticker"].astype(str).str.strip().str.upper() == tk]
    if df.empty:
        return []
    ts = pd.to_datetime(df["traded_at"], errors="coerce", utc=True, format="ISO8601")
    out = []
    for (_, row), t in zip(df.iterrows(), ts):
        action = str(row.get("action", "")).upper()
        sh, px = _num(row.get("shares")), _num(row.get("price"))
        if sh is None or px is None or sh <= 0 or px <= 0:
            continue
        if "SPLIT" in action:
            kind = "SPLIT"
        elif "BUY" in action:
            kind = "BUY"
        elif "SELL" in action:
            kind = "SELL"
        else:
            continue
        rid = _num(row.get("id"))
        out.append({
            "kind": kind, "shares": sh, "raw": row.get("traded_at"), "new": False,
            "ts": None if pd.isna(t) else t,
            "id": rid if rid is not None else math.inf,
        })
    return out


def _replay_order(rows: list) -> list:
    """Replay order of `db.recalculate_from_trades`: by parsed `traded_at`
    (unparseable last), then id; the staged row has the highest id, so it sorts
    after every existing row with an equal timestamp."""
    return sorted(rows, key=lambda r: (
        r["ts"] is None, r["ts"] if r["ts"] is not None else 0, r["id"], r["new"]))


def _replay_violations(ordered: list) -> list:
    """Walk the running balance; one `{"row", "before"}` per SELL that takes it
    below `-BROKER_DRIFT_SHARE_TOL`. Like the db replay, an over-sell is clamped
    at zero and the walk goes on."""
    bal = 0.0
    bad = []
    for r in ordered:
        if r["kind"] == "SPLIT":
            bal = r["shares"]
        elif r["kind"] == "BUY":
            bal += r["shares"]
        else:
            nxt = bal - r["shares"]
            if nxt < -BROKER_DRIFT_SHARE_TOL:
                bad.append({"row": r, "before": bal})
                nxt = 0.0
            bal = max(nxt, 0.0)
    return bad


def sell_replay_unchecked_finding() -> dict:
    """The info finding for "the replay check could not run" (never silent)."""
    return {
        "kind": KIND_SELL_REPLAY_UNCHECKED, "severity": SEV_INFO,
        "text": "Couldn't check this SELL against the order of your journal's trades.",
    }


def sell_replay_finding(journal, ticker, shares, traded_at) -> "dict | None":
    """A hard block for a SELL that the journal replay would orphan.

    `db.recalculate_from_trades` replays by `traded_at` then id, so a SELL timed
    14:00 lands BEFORE a past-dated BUY on the 16:00 placeholder and becomes
    "SELL with no prior BUY". The form's older guard checks total shares only.
    This inserts the new SELL (sorted after every existing row with an equal
    timestamp) into that ordering and walks the running balance from there on.
    It mirrors the db replay's ordering, row filter, SPLIT overwrite and
    clamp-at-zero, but judges a shortfall against `BROKER_DRIFT_SHARE_TOL` (the
    db replay warns at 1e-6).

    * None when `ticker` / `shares` are unusable, or when the balance never
      drops below `-BROKER_DRIFT_SHARE_TOL` because of this row.
    * An info finding (`sell_replay_unchecked_finding`) when the journal can't
      be read (None, not a frame, or a frame missing a column): the check did not
      run, and that is said, never a silent clean. The caller's other guards
      still run; nothing blocks.
    * Otherwise `{"kind": "SELL_BEFORE_BUY", "severity": "hard", "text"}`: the
      new SELL itself is short at its place in the order, or it leaves a LATER
      existing SELL short (a shortfall that already existed without this row is
      not blamed on it; a later row's unreadable date reads "date unknown").
    `traded_at` unreadable -> ValueError.
    """
    tk = str(ticker or "").strip().upper()
    sh = _num(shares)
    if not tk or sh is None or sh <= 0:
        return None
    rows = _replay_rows(journal, tk)
    if rows is None:
        return sell_replay_unchecked_finding()
    t = _to_et_datetime(traded_at)
    new = {"kind": "SELL", "shares": sh, "raw": traded_at, "new": True,
           "ts": pd.Timestamp(t).tz_convert("UTC"), "id": math.inf}
    base_bad = {id(v["row"]) for v in _replay_violations(_replay_order(rows))}
    bad = _replay_violations(_replay_order(rows + [new]))
    when = f"{_fmt_date(t.date())}, {_fmt_dt_clock(t)} ET"
    anchor = _fmt_clock(int(IMPORTED_TRADE_ANCHOR_ET_HOUR), 0)
    for v in bad:
        if v["row"] is new:
            return {
                "kind": KIND_SELL_BEFORE_BUY, "severity": SEV_HARD,
                "text": (f"At {when} your journal holds only {_fmt_shares(max(v['before'], 0.0))} "
                         f"{tk}: the BUY that covers this SELL is stored at a later time "
                         f"(a BUY logged without a fill time is placed at {anchor} ET), "
                         "so the SELL would be replayed first and count as a sale of "
                         "shares you didn't hold. If this SELL's time is right, the BUY "
                         "covering it is stored too late: delete it in \U0001F4CB History and "
                         "re-log it with its fill time (a Robinhood-linked BUY would lose "
                         "its link, so tick 'Allow SELL without a matching holding' "
                         "instead). The same tick covers a holding from before the app."),
            }
    for v in bad:
        if id(v["row"]) not in base_bad:
            ld = trade_time.et_trade_date(v["row"]["raw"])
            lw = _fmt_date(ld) if ld is not None else "date unknown"
            return {
                "kind": KIND_SELL_BEFORE_BUY, "severity": SEV_HARD,
                "text": (f"This SELL leaves too few {tk} shares for a later SELL already in "
                         f"your journal ({lw}). Check the share count and dates, or tick "
                         "'Allow SELL without a matching holding' for a holding from "
                         "before the app."),
            }
    return None


# ── price ────────────────────────────────────────────────────────────────────

def price_band_finding(price, trade_date, bar, today_et) -> dict:
    """A logged price against that session's daily range.

    `bar` is `{"low", "high"}` for `trade_date`'s split-adjusted, dividend-
    unadjusted daily bar (the C2b caller takes today's / a recent day's low /
    high from `data.fetch_session_range` and falls back to the
    `data.fetch_unadjusted_history` frame, which still split-adjusts, for older
    dates) or None. C2b MUST pass `bar=None` (so the result is "unchecked") when a split falls between
    the trade date and today: the bar would then be on a different share basis
    than the price the owner typed. The band is
    `low x (1 - tol/100)` to `high x (1 + tol/100)` with
    `tol = TRADE_LOG_PRICE_BAND_TOL_PCT`, both edges inclusive (compared at 6 dp
    so float noise can't flip an edge). Not `held_stop.fill_in_band`, which
    widens to the prior close.

    Returns `{"state": "in_band" | "outside" | "unchecked", "low", "high",
    "band_low", "band_high", "so_far": bool, "reason": str | None}`.
    "unchecked" (never a silent in-band, `reason` says why) when: the price is
    non-finite or <= 0; `trade_date` is after `today_et`; `trade_date` is more
    than `TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS` calendar days before `today_et`;
    or the bar is None / non-finite / non-positive / low > high. `so_far` is True
    when `trade_date == today_et`: today's bar is still forming, so the caller
    words the verdict "so far". `low`/`high`/`band_*` are None when the bar is
    unusable (and band_* None whenever unchecked).
    """
    d = _as_date(trade_date)
    today = _as_date(today_et)
    if d is None or today is None:
        raise ValueError("price_band_finding needs trade_date and today_et as dates")
    so_far = d == today

    lo = hi = None
    if isinstance(bar, dict):
        lo, hi = _num(bar.get("low")), _num(bar.get("high"))
    bar_ok = lo is not None and hi is not None and lo > 0 and hi > 0 and lo <= hi

    def _out(state, reason, band_lo=None, band_hi=None):
        return {
            "state": state,
            "low": lo if bar_ok else None, "high": hi if bar_ok else None,
            "band_low": band_lo, "band_high": band_hi,
            "so_far": so_far, "reason": reason,
        }

    px = _num(price)
    if px is None or px <= 0:
        return _out(STATE_UNCHECKED, "the price is not a positive number")
    if d > today:
        return _out(STATE_UNCHECKED, "the trade date is in the future")
    if (today - d).days > TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS:
        return _out(STATE_UNCHECKED,
                    f"older than {TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS} days, "
                    "so the day's price range isn't checked")
    if not bar_ok:
        return _out(STATE_UNCHECKED, "no usable price bar for that day")

    tol = TRADE_LOG_PRICE_BAND_TOL_PCT / 100.0
    band_lo = round(lo * (1.0 - tol), 6)
    band_hi = round(hi * (1.0 + tol), 6)
    inside = band_lo <= round(px, 6) <= band_hi
    return _out(STATE_IN_BAND if inside else STATE_OUTSIDE, None, band_lo, band_hi)


# ── broker fills ─────────────────────────────────────────────────────────────

def _same_shares(a: float, b: float) -> bool:
    return round(abs(a - b), 9) <= BROKER_DRIFT_SHARE_TOL


def _same_price(a: float, b: float) -> bool:
    return round(abs(a - b), 6) <= JOURNAL_MATCH_PRICE_TOL_USD


def _txn_ids(fills) -> list:
    return [str(f["txn"]) for f in fills if f["txn"] not in (None, "")]


def pending_fill_findings(entry, fills) -> "list | None":
    """The entry against Robinhood fills not yet linked to a journal row.

    `entry` = `{ticker, action, shares, price, trade_date}`; `fills` = unlinked
    broker fills `[{snaptrade_txn_id, ticker, action, shares, price,
    trade_date}]` where `trade_date` is the fill's ET date, or None when the
    caller couldn't read them. Three states, like every cache here:
    `None` in -> `None` out ("couldn't check", never read as clean); `[]` ->
    `[]`; otherwise findings. A malformed `entry`, or a malformed fill of the
    SAME ticker and action, also returns `None` rather than a clean `[]`.
    Only fills with the same ticker (upper / stripped) and the same action are
    considered. Shares compare within `BROKER_DRIFT_SHARE_TOL`, prices within
    `JOURNAL_MATCH_PRICE_TOL_USD`, "near" is `JOURNAL_MATCH_WINDOW_SESSIONS`
    trading sessions.

    Each finding is `{"kind", "severity", "text", "txn_ids", "fill_date"}`:
      * MATCH_SAME_DAY (info): a fill on the entry's ET date with the same
        shares and price. `txn_ids` is every such fill (identical fills are
        interchangeable; one id in the normal case). Plan decision 4: the C2
        form switches to the broker-prefill path for it. It is EXCLUSIVE: the
        entry is explained, so no other finding is returned alongside it.
      * OTHER_DATE (tick): one fill with the same shares and price on a
        DIFFERENT ET date within the window (an order-date hint pointing at the
        'Filled' date in Robinhood). One finding per such fill.
      * OTHER_DATE_SPLIT (tick): the fills of ONE other ET date within the
        window whose shares sum to the entry and whose VWAP is within the price
        tolerance. Whole-date groups only (subset sums are C5's job).
      * SHARES_SHORT (tick): the fills on the entry's own ET date sum to MORE
        shares than the entry (U3 "broker shares that date > entry"). A same-day
        split that sums to the entry is reconciled, not a finding (owner
        decision 2 of 2026-11-02, MRVL 10-30: 5 @ 200.00 + 3 @ 191.96 = 8 @ 196.99).
    Deterministic order: MATCH_SAME_DAY, OTHER_DATE (by fill date, txn id),
    OTHER_DATE_SPLIT (by fill date), SHARES_SHORT.

    A fill's date is SnapTrade's ORDER PLACEMENT date (P0 probe 2026-11-06), not
    necessarily the execution date, so OTHER_DATE is a hint, not proof, and its
    text says so. For C2: `trades.broker_txn_id` is one column, so a MATCH_SAME_DAY
    links `txn_ids[0]` only; and the broker-prefill path locks the entry's date
    to that SnapTrade placement date. SHARES_SHORT counts UNLINKED fills, which
    can include a fill the owner already hand-logged, hence its wording.
    """
    if fills is None:
        return None
    if not isinstance(entry, dict):
        return None
    tk = str(entry.get("ticker") or "").strip().upper()
    act = str(entry.get("action") or "").strip().upper()
    e_sh, e_px = _num(entry.get("shares")), _num(entry.get("price"))
    e_date = _as_date(entry.get("trade_date"))
    if not tk or not act or e_sh is None or e_sh <= 0 or e_px is None or e_px <= 0 \
            or e_date is None:
        return None

    cand = []
    for f in fills:
        if not isinstance(f, dict):
            continue
        if str(f.get("ticker") or "").strip().upper() != tk \
                or str(f.get("action") or "").strip().upper() != act:
            continue
        sh, px, fd = _num(f.get("shares")), _num(f.get("price")), _as_date(f.get("trade_date"))
        if sh is None or sh <= 0 or px is None or px <= 0 or fd is None:
            return None     # an unreadable same-name fill: can't call it clean
        cand.append({"txn": f.get("snaptrade_txn_id"), "shares": sh, "price": px, "date": fd})
    cand.sort(key=lambda c: (c["date"], str(c["txn"]), c["shares"], c["price"]))

    ed = _fmt_date(e_date)
    desc = f"{act} {_fmt_shares(e_sh)} {tk}"

    same_day = [c for c in cand if c["date"] == e_date]
    match = [c for c in same_day if _same_shares(c["shares"], e_sh) and _same_price(c["price"], e_px)]
    if match:
        return [{
            "kind": KIND_MATCH_SAME_DAY, "severity": SEV_INFO,
            "text": (f"Robinhood shows this fill on {ed}: {desc} at "
                     f"{_fmt_price(match[0]['price'])}"),
            "txn_ids": _txn_ids(match), "fill_date": e_date,
        }]

    out: list = []
    near = [c for c in cand if c["date"] != e_date
            and _sessions_between(e_date, c["date"]) <= JOURNAL_MATCH_WINDOW_SESSIONS]

    for c in near:
        if _same_shares(c["shares"], e_sh) and _same_price(c["price"], e_px):
            out.append({
                "kind": KIND_OTHER_DATE, "severity": SEV_TICK,
                "text": (f"Robinhood's record of this order ({desc} at {_fmt_price(c['price'])}) "
                         f"is dated {_fmt_date(c['date'])}, not {ed}. That is the date the "
                         "order was placed, which can be earlier than the fill; check the "
                         "'Filled' date in Robinhood."),
                "txn_ids": _txn_ids([c]), "fill_date": c["date"],
            })

    by_date: dict = {}
    for c in near:
        by_date.setdefault(c["date"], []).append(c)
    for fd in sorted(by_date):
        grp = by_date[fd]
        if len(grp) < 2:
            continue
        total = sum(c["shares"] for c in grp)
        vwap = sum(c["shares"] * c["price"] for c in grp) / total
        if _same_shares(total, e_sh) and _same_price(vwap, e_px):
            out.append({
                "kind": KIND_OTHER_DATE_SPLIT, "severity": SEV_TICK,
                "text": (f"Robinhood's records of {len(grp)} fills totalling {_fmt_shares(total)} "
                         f"{tk} {act} (average {_fmt_price(vwap)}) are dated {_fmt_date(fd)}, "
                         f"not {ed}. That is the date the orders were placed, which can be "
                         "earlier than the fills; check the 'Filled' dates in Robinhood."),
                "txn_ids": _txn_ids(grp), "fill_date": fd,
            })

    if same_day:
        total = sum(c["shares"] for c in same_day)
        if round(total - e_sh, 9) > BROKER_DRIFT_SHARE_TOL:
            out.append({
                "kind": KIND_SHARES_SHORT, "severity": SEV_TICK,
                "text": (f"{_fmt_shares(total)} shares of {tk} {act} in Robinhood fills not yet "
                         f"matched to the journal are dated {ed}, more than the "
                         f"{_fmt_shares(e_sh)} you entered"),
                "txn_ids": _txn_ids(same_day), "fill_date": e_date,
            })
    return out
