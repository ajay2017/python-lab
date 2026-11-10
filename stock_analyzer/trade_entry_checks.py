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
`fill_time_missing_text`). Chunk C2b wires the PRICE and BROKER halves: the
caller owns the fetching (today's / a recent day's bar low / high from
`data.fetch_session_range`, falling back to the `data.fetch_unadjusted_history`
frame, split-adjusted and dividend-unadjusted, for older dates;
`db.load_snaptrade_pending_fills_or_none` for the broker's fills and
`db.load_snaptrade_txn_synced_at_or_none` for the last completed transaction
sync) and passes it in; `bar_for_price_check`, `price_band_entry_finding`,
`unlinked_fills`, `broker_check_coverage` and friends turn it into findings.
Plain text only in every `text` field: the caller escapes it and puts `$` as
`&#36;`. Every finding carries a `group` ("time", "price" or "broker") so the
confirm card can word its tick-box for what it is checking.

  * Time: `session_hours`, `default_trade_date`, `fill_time_requirement`,
    `resolve_traded_at`, `time_findings` (NON_TRADING_DATE / OUTSIDE_RTH are
    ticks, FUTURE is a hard block), `same_ticker_same_day`, `logging_as_text`,
    `fill_time_caption`, `pick_date_caption`, `trade_date_widget_reset`,
    `fill_time_missing_text`.
  * Replay: `sell_replay_finding`, the as-of-time hard block for a SELL that
    `db.recalculate_from_trades` would replay before the BUY that covers it.
  * Price: `price_sanity` (typo guard against the market price),
    `price_band_finding`, a logged price against that session's split-adjusted,
    dividend-unadjusted low-high band, with `bar_for_price_check` choosing the
    bar (and refusing it across a split) and `price_band_entry_finding` turning
    the verdict into a card finding. Never a silent in-band: anything it can't
    judge is `unchecked` with a reason.
  * Broker: `pending_fill_findings`, the entry against Robinhood fills that are
    not yet linked to a journal row (`unlinked_fills` builds that list,
    `fills_since` bounds the read); `broker_check_coverage` says how fresh the
    comparison is. A real finding is always shown; coverage only qualifies the
    ABSENCE of one, and an empty result is never worded "matches Robinhood".

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

import json
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
    TRADE_PRICE_SANITY_FLOOR,
    TRADE_PRICE_SANITY_RATIO_HIGH,
    TRADE_PRICE_SANITY_RATIO_LOW,
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
KIND_PRICE_DIFFERS = "PRICE_DIFFERS"
KIND_BROKER_NOT_SYNCED = "BROKER_NOT_SYNCED"
KIND_BROKER_UNCHECKED = "BROKER_UNCHECKED"
# Finding kinds (price band)
KIND_PRICE_OUTSIDE_RANGE = "PRICE_OUTSIDE_RANGE"
KIND_PRICE_UNCHECKED = "PRICE_UNCHECKED"

# What a finding is about: `trade_confirm.confirm_gate` words the tick-box by it.
GROUP_TIME = "time"
GROUP_PRICE = "price"
GROUP_BROKER = "broker"

BROKER_UNREADABLE_TEXT = "Not compared with Robinhood's fills: they couldn't be read."
PRICE_CHECK_FAILED_REASON = "the check failed"

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
            "kind": KIND_FUTURE, "severity": SEV_HARD, "group": GROUP_TIME,
            "text": f"{_fmt_date(d)}, {_fmt_dt_clock(t)} ET is in the future",
        })

    hours = session_hours(d)
    if hours is None:
        out.append({
            "kind": KIND_NON_TRADING_DATE, "severity": SEV_TICK, "group": GROUP_TIME,
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
            "kind": KIND_OUTSIDE_RTH, "severity": SEV_TICK, "group": GROUP_TIME,
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
        "kind": KIND_SELL_REPLAY_UNCHECKED, "severity": SEV_INFO, "group": GROUP_TIME,
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
                "kind": KIND_SELL_BEFORE_BUY, "severity": SEV_HARD, "group": GROUP_TIME,
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
                "kind": KIND_SELL_BEFORE_BUY, "severity": SEV_HARD, "group": GROUP_TIME,
                "text": (f"This SELL leaves too few {tk} shares for a later SELL already in "
                         f"your journal ({lw}). Check the share count and dates, or tick "
                         "'Allow SELL without a matching holding' for a holding from "
                         "before the app."),
            }
    return None


# ── price ────────────────────────────────────────────────────────────────────

def price_sanity(price, market_price) -> dict:
    """The typo guard on a typed price (moved out of app.py, TJDate C2b).

    -> `{"block": bool, "kind": "floor" | "ratio" | None, "ratio": float | None,
    "hint": bool}`.
      * `floor`: `price` is below `TRADE_PRICE_SANITY_FLOOR` (strict `<`), or is
        not a usable number (never reachable from the form's number box; failing
        closed is the safe direction). Blocks.
      * `ratio`: `market_price` is finite and > 0 and `price / market_price`
        falls outside `[TRADE_PRICE_SANITY_RATIO_LOW, TRADE_PRICE_SANITY_RATIO_HIGH]`
        (strict `<` / `>`, so exactly LOW or HIGH passes). Blocks.
      * `ratio` is the price / market-price quotient whenever both are usable
        (None otherwise: no market price, nothing to compare); `hint` is True
        exactly when that ratio is outside the band, whatever the floor says, for
        the live "off market" note. The floor wins `kind` when both fire.
    The "Allow unusual price" override stays the caller's: it simply ignores
    `block`.
    """
    px = _num(price)
    mp = _num(market_price)
    ratio = px / mp if (px is not None and mp is not None and mp > 0) else None
    hint = ratio is not None and (
        ratio < TRADE_PRICE_SANITY_RATIO_LOW or ratio > TRADE_PRICE_SANITY_RATIO_HIGH)
    if px is None or px < TRADE_PRICE_SANITY_FLOOR:
        return {"block": True, "kind": "floor", "ratio": ratio, "hint": hint}
    if hint:
        return {"block": True, "kind": "ratio", "ratio": ratio, "hint": True}
    return {"block": False, "kind": None, "ratio": ratio, "hint": False}


def price_sanity_override_label() -> str:
    """The "Allow unusual price" checkbox label, with the band read from the
    constants (the old hardcoded "±50% off market" was wrong: the high side is
    +100%)."""
    lo = TRADE_PRICE_SANITY_RATIO_LOW * 100.0
    hi = TRADE_PRICE_SANITY_RATIO_HIGH * 100.0
    return (f"Allow unusual price (under {lo:g}% or over {hi:g}% of the market "
            "price — historical/split-adjusted/delisted)")


REASON_NO_BAR = "no usable price bar for that day"
REASON_SPLIT_UNKNOWN = "couldn't check for a stock split since that day"
REASON_SPLIT_SINCE = ("a stock split since that day puts the day's range on a "
                      "different share basis")


# yfinance `period` strings and the calendar days each is guaranteed to span
# (lower bounds: "1mo" is 28 days in February), shortest first.
_YF_PERIOD_DAYS = (("1mo", 28), ("3mo", 89), ("6mo", 180), ("1y", 365),
                   ("2y", 730), ("5y", 1825), ("10y", 3650))


def price_check_history_period() -> str:
    """The yfinance `period` for the history `bar_for_price_check` reads: the
    shortest one that spans more than `TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS`
    calendar days (the oldest date `price_check_needs_bar` lets through), so a
    raised constant moves the period with it instead of quietly leaving the split
    check without history ("max" past ten years)."""
    need = int(TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS) + 1
    for period, days in _YF_PERIOD_DAYS:
        if days >= need:
            return period
    return "max"


def price_check_needs_bar(trade_date, today_et) -> bool:
    """Whether a price bar can change `price_band_finding`'s verdict, so the
    caller should fetch one: the date is not in the future, not older than
    `TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS`, and a trading day (a weekend / holiday
    has no bar, and its own NON_TRADING_DATE tick already fires). False means the
    network fetch is pointless; the caller still calls `price_band_finding` (with
    `bar=None`) for the stated reason. Unreadable dates -> ValueError."""
    d = _as_date(trade_date)
    today = _as_date(today_et)
    if d is None or today is None:
        raise ValueError("price_check_needs_bar needs trade_date and today_et as dates")
    if d > today or (today - d).days > TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS:
        return False
    return _is_trading_day(d)


def _usable_bar(b) -> "dict | None":
    """`{"low", "high"}` as floats when finite, positive and ordered, else None."""
    if not isinstance(b, dict):
        return None
    lo, hi = _num(b.get("low")), _num(b.get("high"))
    if lo is None or hi is None or lo <= 0 or hi <= 0 or lo > hi:
        return None
    return {"low": lo, "high": hi}


def _index_et_dates(index) -> "list | None":
    """The ET calendar date of every row of a history index, or None when the
    index can't be read. A tz-aware index is converted to America/New_York; a
    tz-naive one is taken as exchange-local, the way
    `yfinance_provider.session_range_unadjusted` does."""
    try:
        idx = pd.DatetimeIndex(pd.to_datetime(index))
        if idx.tz is not None:
            idx = idx.tz_convert("America/New_York").tz_localize(None)
        return [None if pd.isna(t) else t.date() for t in idx]
    except Exception:
        return None


def _history_bar(history, dates, d) -> "dict | None":
    """The min Low / max High of the history rows dated `d`, or None."""
    try:
        lows, highs = [], []
        for i, rd in enumerate(dates):
            if rd != d:
                continue
            lo, hi = _num(history["Low"].iloc[i]), _num(history["High"].iloc[i])
            if lo is None or hi is None:
                continue
            lows.append(lo)
            highs.append(hi)
        if not lows:
            return None
        return _usable_bar({"low": min(lows), "high": max(highs)})
    except Exception:
        return None


def bar_for_price_check(trade_date, today_et, history, session_range) -> tuple:
    """The bar to judge a typed price against, and why there is none.

    -> `(bar, unchecked_reason)`: `({"low", "high"}, None)` or `(None, reason)`.
    `session_range` is `data.fetch_session_range`'s `{"low", "high"}` for
    `trade_date` (None when unusable); `history` is the
    `data.fetch_unadjusted_history`-style frame (Yahoo `auto_adjust=False`:
    split-adjusted, dividend-unadjusted; Low / High columns, normally "Stock
    Splits" too) or None. The session range is preferred because it keeps a bar
    Yahoo published without a Close.
      * `trade_date == today_et`: no split check (nothing has happened since);
        the session range, else the history row for that ET date, else
        `REASON_NO_BAR`.
      * an earlier date: the bar is on TODAY's share basis, the typed price on
        the trade date's. History None / empty / without a "Stock Splits" column
        / with an unreadable index or no row at or before the trade date ->
        `REASON_SPLIT_UNKNOWN`; a nonzero split on an ET date in
        `(trade_date, today_et]` -> `REASON_SPLIT_SINCE` (a split ON the trade
        date is not "since": that day's own bar is already on the new basis).
        Otherwise the session range, else the history row, else `REASON_NO_BAR`.
      * a future date: `(None, "the trade date is in the future")`; a weekend or
        NYSE holiday: `(None, REASON_NO_BAR)` before any split question (a day
        with no session has no bar to put on a share basis).
    A row's ET date is read by `_index_et_dates`. Usable = finite, > 0,
    low <= high. Never raises on a malformed frame; unreadable dates ->
    ValueError like its siblings.
    """
    d = _as_date(trade_date)
    today = _as_date(today_et)
    if d is None or today is None:
        raise ValueError("bar_for_price_check needs trade_date and today_et as dates")
    if d > today:
        return None, "the trade date is in the future"
    if not _is_trading_day(d):
        return None, REASON_NO_BAR      # no session, so no bar and no split question
    sr = _usable_bar(session_range)

    usable_hist = isinstance(history, pd.DataFrame) and len(history) > 0
    dates = _index_et_dates(history.index) if usable_hist else None
    if d == today:
        if sr is not None:
            return sr, None
        if dates is not None and "Low" in history.columns and "High" in history.columns:
            hb = _history_bar(history, dates, d)
            if hb is not None:
                return hb, None
        return None, REASON_NO_BAR

    if (not usable_hist or dates is None or "Stock Splits" not in history.columns
            or all(rd is None or rd > d for rd in dates)):
        return None, REASON_SPLIT_UNKNOWN
    try:
        for i, rd in enumerate(dates):
            if rd is None or not (d < rd <= today):
                continue
            sp = _num(history["Stock Splits"].iloc[i])
            if sp is not None and sp != 0:
                return None, REASON_SPLIT_SINCE
    except Exception:
        return None, REASON_SPLIT_UNKNOWN
    if sr is not None:
        return sr, None
    if "Low" in history.columns and "High" in history.columns:
        hb = _history_bar(history, dates, d)
        if hb is not None:
            return hb, None
    return None, REASON_NO_BAR


def price_band_finding(price, trade_date, bar, today_et, *, now_et=None,
                       unchecked_reason=None) -> dict:
    """A logged price against that session's daily range.

    `bar` is `{"low", "high"}` for `trade_date`'s split-adjusted, dividend-
    unadjusted daily bar or None; `bar_for_price_check` picks it (today's / a
    recent day's low / high from `data.fetch_session_range`, else the
    `data.fetch_unadjusted_history` frame) and returns None, with a reason, when
    a split falls between the trade date and today: the bar would then be on a
    different share basis than the price the owner typed. The band is
    `low x (1 - tol/100)` to `high x (1 + tol/100)` with
    `tol = TRADE_LOG_PRICE_BAND_TOL_PCT`, both edges inclusive (compared at 6 dp
    so float noise can't flip an edge). Not `held_stop.fill_in_band`, which
    widens to the prior close.

    Returns `{"state": "in_band" | "outside" | "unchecked", "low", "high",
    "band_low", "band_high", "so_far": bool, "reason": str | None}`.
    "unchecked" (never a silent in-band, `reason` says why) when: the price is
    non-finite or <= 0; `trade_date` is after `today_et`; `trade_date` is more
    than `TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS` calendar days before `today_et`;
    or the bar is None / non-finite / non-positive / low > high (then `reason` is
    `unchecked_reason` when the caller gives one, else "no usable price bar for
    that day"). `so_far` is True when `trade_date == today_et` AND today's bar is
    still forming, so the caller words the verdict "so far". With `now_et` (a
    tz-aware datetime) that means before that day's regular close
    (`session_hours`, early closes included; at the close or after it, the bar is
    final, so False; a day with no session is False); without `now_et` it is
    simply `trade_date == today_et`. `low`/`high`/`band_*` are None when the bar
    is unusable (and band_* None whenever unchecked).
    """
    d = _as_date(trade_date)
    today = _as_date(today_et)
    if d is None or today is None:
        raise ValueError("price_band_finding needs trade_date and today_et as dates")
    so_far = d == today
    if so_far and now_et is not None:
        hours = session_hours(d)
        so_far = hours is not None and _aware_et(now_et) < hours[1]

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
        return _out(STATE_UNCHECKED, unchecked_reason or REASON_NO_BAR)

    tol = TRADE_LOG_PRICE_BAND_TOL_PCT / 100.0
    band_lo = round(lo * (1.0 - tol), 6)
    band_hi = round(hi * (1.0 + tol), 6)
    inside = band_lo <= round(px, 6) <= band_hi
    return _out(STATE_IN_BAND if inside else STATE_OUTSIDE, None, band_lo, band_hi)


def _price_unchecked(reason: str) -> dict:
    return {
        "kind": KIND_PRICE_UNCHECKED, "severity": SEV_INFO, "group": GROUP_PRICE,
        "text": f"Price not checked against the day's range: {reason}.",
    }


def price_check_failed_finding() -> dict:
    """The info finding for "the price-band check itself raised" (never silent)."""
    return _price_unchecked(PRICE_CHECK_FAILED_REASON)


def price_band_entry_finding(band, price, trade_date=None) -> "dict | None":
    """A `price_band_finding` result as a confirm-card finding, or None.

    `{"kind", "severity", "text", "group": "price"}`:
      * outside -> PRICE_OUTSIDE_RANGE (tick): "$171.88 is outside Mon Nov 2's
        trading range ($186.10 – $191.40, with 1% leeway)", with "trading range
        so far" while the day's bar is still forming (`band["so_far"]`). The range
        shown is the bar's own low / high; the leeway names
        `TRADE_LOG_PRICE_BAND_TOL_PCT`. `trade_date` (optional) names the day,
        else "that day".
      * unchecked -> PRICE_UNCHECKED (info): "Price not checked against the day's
        range: {reason}." An unreadable `band` reads as unchecked too ("the check
        failed"), never as clean.
      * in_band -> None.
    Plain text; `$` is put on the prices (the caller escapes it).
    """
    if not isinstance(band, dict):
        return price_check_failed_finding()
    state = band.get("state")
    if state == STATE_IN_BAND:
        return None
    if state == STATE_OUTSIDE:
        px, lo, hi = _num(price), _num(band.get("low")), _num(band.get("high"))
        if px is None or lo is None or hi is None:
            return price_check_failed_finding()
        d = _as_date(trade_date) if trade_date is not None else None
        day = f"{_fmt_date(d)}'s" if d is not None else "that day's"
        rng = "trading range so far" if band.get("so_far") else "trading range"
        return {
            "kind": KIND_PRICE_OUTSIDE_RANGE, "severity": SEV_TICK, "group": GROUP_PRICE,
            "text": (f"${_fmt_price(px)} is outside {day} {rng} "
                     f"(${_fmt_price(lo)} – ${_fmt_price(hi)}, with "
                     f"{TRADE_LOG_PRICE_BAND_TOL_PCT:g}% leeway)"),
        }
    reason = str(band.get("reason") or "").strip() or PRICE_CHECK_FAILED_REASON
    return _price_unchecked(reason)


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
      * PRICE_DIFFERS (tick; owner / lead decision, the D0 precedent), only when
        there is NO MATCH_SAME_DAY: (i) a fill on the entry's ET date with the
        same shares but a different price (OKTA 11-02: entry 171.88, fill
        189.14), one finding per such fill; (ii) two or more fills on that date
        whose shares sum to the entry and whose VWAP is not the entry's price
        (the combined-row case). A same-day split that sums to the entry at its
        VWAP is reconciled, not a finding (MRVL 10-30).
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
    Deterministic order: MATCH_SAME_DAY, PRICE_DIFFERS, OTHER_DATE (by fill
    date, txn id), OTHER_DATE_SPLIT (by fill date), SHARES_SHORT. Every finding
    carries `"group": "broker"`.

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
            "kind": KIND_MATCH_SAME_DAY, "severity": SEV_INFO, "group": GROUP_BROKER,
            "text": (f"Robinhood's record of this order is dated {ed}: {desc} at "
                     f"{_fmt_price(match[0]['price'])}. This entry will be linked to it."),
            "txn_ids": _txn_ids(match), "fill_date": e_date,
        }]

    out: list = []
    e_px_txt = _fmt_price(e_px)
    for c in same_day:
        if _same_shares(c["shares"], e_sh) and not _same_price(c["price"], e_px):
            out.append({
                "kind": KIND_PRICE_DIFFERS, "severity": SEV_TICK, "group": GROUP_BROKER,
                "text": (f"Robinhood's record of {desc} on {ed} is at "
                         f"{_fmt_price(c['price'])}, not {e_px_txt}. "
                         "Check the price in Robinhood."),
                "txn_ids": _txn_ids([c]), "fill_date": e_date,
            })
    if len(same_day) >= 2:
        total = sum(c["shares"] for c in same_day)
        vwap = sum(c["shares"] * c["price"] for c in same_day) / total
        if _same_shares(total, e_sh) and not _same_price(vwap, e_px):
            out.append({
                "kind": KIND_PRICE_DIFFERS, "severity": SEV_TICK, "group": GROUP_BROKER,
                "text": (f"Robinhood's {len(same_day)} fills of {desc} on {ed} average "
                         f"{_fmt_price(vwap)}, not {e_px_txt}. "
                         "Check the price in Robinhood."),
                "txn_ids": _txn_ids(same_day), "fill_date": e_date,
            })
    near =[c for c in cand if c["date"] != e_date
            and _sessions_between(e_date, c["date"]) <= JOURNAL_MATCH_WINDOW_SESSIONS]

    for c in near:
        if _same_shares(c["shares"], e_sh) and _same_price(c["price"], e_px):
            out.append({
                "kind": KIND_OTHER_DATE, "severity": SEV_TICK, "group": GROUP_BROKER,
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
                "kind": KIND_OTHER_DATE_SPLIT, "severity": SEV_TICK, "group": GROUP_BROKER,
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
                "kind": KIND_SHARES_SHORT, "severity": SEV_TICK, "group": GROUP_BROKER,
                "text": (f"{_fmt_shares(total)} shares of {tk} {act} in Robinhood fills not yet "
                         f"matched to the journal are dated {ed}, more than the "
                         f"{_fmt_shares(e_sh)} you entered"),
                "txn_ids": _txn_ids(same_day), "fill_date": e_date,
            })
    return out


# ── broker fills: what to compare with, and how fresh the comparison is ──────

def _txn_text(v) -> "str | None":
    """A non-empty transaction id as text, else None (None / NaN / '' / 'None')."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return None if s in ("", "None", "nan", "NaN") else s


def _raw_price(raw_json) -> "float | None":
    """The exact fill price in a pending row's `raw_json` (a dict or a JSON
    string): positive and finite, else None. P0 probe Q3: `raw_json["price"]` is
    exact; the row's own `price` column is rounded to 2 dp."""
    raw = raw_json
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, dict):
        return None
    px = _num(raw.get("price"))
    return px if px is not None and px > 0 else None


def unlinked_fills(rows, journal) -> "list | None":
    """The `pending_fill_findings` input: Robinhood fills not yet on a journal row.

    `rows` = the pending-import dicts `db.load_snaptrade_pending_fills_or_none`
    returns (`id, snaptrade_txn_id, ticker, action, shares, price, trade_date,
    raw_json`), or None; `journal` = the strict trades frame
    (`db.load_trades_or_none()`), or None. None if either is None (couldn't
    check, never a clean `[]`), if a row is not a dict, or if a non-empty
    journal has no `broker_txn_id` column (nothing can be called unlinked).
    Drops rows whose `snaptrade_txn_id` is on a journal row (text compare,
    stripped; NaN / None ignored). Each remaining row becomes
    `{"snaptrade_txn_id", "pending_id", "ticker", "action", "shares", "price",
    "trade_date"}`: price is the exact `raw_json` price when it has one, else the
    row's `price`; `trade_date` is the ET date of the stored value (a Postgres
    `date`, already the ET fill date since TJDate C4). A field that can't be read
    becomes None, which makes `pending_fill_findings` return None for that
    ticker and action (its malformed-fill rule), never a clean result.
    """
    if rows is None or journal is None or not isinstance(journal, pd.DataFrame):
        return None
    linked: set = set()
    if "broker_txn_id" in journal.columns:
        for v in journal["broker_txn_id"]:
            t = _txn_text(v)
            if t is not None:
                linked.add(t)
    elif len(journal) > 0:
        return None
    out: list = []
    for r in rows:
        if not isinstance(r, dict):
            return None
        txn = _txn_text(r.get("snaptrade_txn_id"))
        if txn is not None and txn in linked:
            continue
        sh = _num(r.get("shares"))
        px = _raw_price(r.get("raw_json"))
        if px is None:
            px = _num(r.get("price"))
        out.append({
            "snaptrade_txn_id": txn,
            "pending_id": r.get("id"),
            "ticker": str(r.get("ticker") or "").strip().upper(),
            "action": str(r.get("action") or "").strip().upper(),
            "shares": sh if sh is not None and sh > 0 else None,
            "price": px if px is not None and px > 0 else None,
            "trade_date": _as_date(r.get("trade_date")),
        })
    return out


def pending_id_for_txn(fills, txn_id) -> "object | None":
    """The pending-import `pending_id` of the fill with `txn_id` in an
    `unlinked_fills` list, or None. The form stashes it so confirming a
    MATCH_SAME_DAY entry flips that pending import to 'logged'."""
    want = _txn_text(txn_id)
    if want is None:
        return None
    for f in fills or []:
        if isinstance(f, dict) and _txn_text(f.get("snaptrade_txn_id")) == want:
            return f.get("pending_id")
    return None


def has_same_day_fill(entry, fills) -> bool:
    """True when `fills` (an `unlinked_fills` list) holds a fill of the entry's
    ticker and action dated the entry's own ET date: the entry WAS compared with
    Robinhood's record of that day, whatever `pending_fill_findings` returned
    (a same-day split that reconciles to the entry returns `[]`, MRVL 10-30), so
    a "not compared yet" line would be wrong. False for None, a malformed entry,
    or when no such fill exists; unreadable fills are skipped."""
    if not isinstance(entry, dict) or not fills:
        return False
    tk = str(entry.get("ticker") or "").strip().upper()
    act = str(entry.get("action") or "").strip().upper()
    d = _as_date(entry.get("trade_date"))
    if not tk or not act or d is None:
        return False
    for f in fills:
        if (isinstance(f, dict)
                and str(f.get("ticker") or "").strip().upper() == tk
                and str(f.get("action") or "").strip().upper() == act
                and _as_date(f.get("trade_date")) == d):
            return True
    return False


def fills_since(trade_date) -> date:
    """The earliest fill date `pending_fill_findings` could still call near
    `trade_date`: the lower bound for the pending-fills read. Fills dated
    `JOURNAL_MATCH_WINDOW_SESSIONS` trading sessions back count, so a trading
    day steps back that many sessions; a weekend or holiday date has the same
    window as the session before it (sessions are counted in
    `(fill date, trade date]`), so it steps from there."""
    d = _as_date(trade_date)
    if d is None:
        raise ValueError("fills_since needs a trade date")
    if not _is_trading_day(d):
        d = _previous_trading_day(d)
    for _ in range(int(JOURNAL_MATCH_WINDOW_SESSIONS)):
        d = _previous_trading_day(d)
    return d


def broker_check_coverage(trade_date, synced_at) -> dict:
    """How fresh the broker comparison is, for ONE trade date.

    -> `{"state": "covered" | "not_synced" | "unknown", "text": str}`.
    `synced_at` = a tz-aware datetime (or ISO string) of the last COMPLETED
    transaction sync, or None. "covered" only when the sync's ET date is AFTER
    `trade_date` (conservative: a sync on the trade date itself may predate that
    day's fills, and SnapTrade dates a fill by order placement); "not_synced"
    when it is on or before it; "unknown" when no completed sync is on record
    (or the value is unreadable: the loader also returns None on a failed read or
    before the DDL, so the text says the time "couldn't be read"). A naive value is read as UTC (the `trade_time`
    convention). One freshness per number: a real finding from the fills is
    always shown whatever this says; this only qualifies the ABSENCE of one, and
    an empty result is never worded "matches Robinhood".
    """
    d = _as_date(trade_date)
    if d is None:
        raise ValueError("broker_check_coverage needs a trade date")
    st = None
    if synced_at is not None:
        try:
            st = _to_et_datetime(synced_at.isoformat() if isinstance(synced_at, datetime)
                                 else synced_at)
        except (ValueError, AttributeError):
            st = None
    if st is None:
        return {"state": "unknown",
                "text": "Not compared with Robinhood's fills: the time of the last "
                        "completed broker sync couldn't be read."}
    when = f"{_fmt_date(st.date())}, {_fmt_dt_clock(st)} ET"
    if st.date() > d:
        return {"state": "covered",
                "text": f"Compared with Robinhood fills synced {when}."}
    return {"state": "not_synced",
            "text": (f"Not compared with Robinhood's fills for {_fmt_date(d)} yet: "
                     f"the last completed sync was {when}.")}


def broker_coverage_finding(coverage) -> "dict | None":
    """An info finding (BROKER_NOT_SYNCED) for an "unknown" / "not_synced"
    coverage, None for "covered" (or anything unreadable: the caller shows the
    BROKER_UNCHECKED finding for a failed read instead)."""
    if not isinstance(coverage, dict) or coverage.get("state") not in ("unknown", "not_synced"):
        return None
    return {"kind": KIND_BROKER_NOT_SYNCED, "severity": SEV_INFO, "group": GROUP_BROKER,
            "text": str(coverage.get("text") or "")}


def broker_unchecked_finding() -> dict:
    """The info finding for "Robinhood's fills couldn't be read" (never silent)."""
    return {"kind": KIND_BROKER_UNCHECKED, "severity": SEV_INFO, "group": GROUP_BROKER,
            "text": BROKER_UNREADABLE_TEXT}
