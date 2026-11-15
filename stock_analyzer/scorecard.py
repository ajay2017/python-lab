"""
Account scorecard: "is this account earning extra?" (docs/plans/account-scorecard.md,
section 8, which overrides section 4).

PURE: no streamlit, no db import, no network, no clock read (callers pass `today`,
an America/New_York date, and the already-loaded inputs).

Three measurements, each a plain dict whose `state` says whether it can be trusted,
plus the display strings the Summary card (app.py) and the Sunday email (notify)
only render:

  since_start      the account's dollar result since the `account_flows` baseline row
                   versus the same money (same dates) in SPY, both on ONE close: the
                   latest trusted end-of-day `account_daily_snapshots` row and SPY's
                   close on that same date. An older SPY close is never reused for the
                   end (state `spy_pending`).
  leverage_verdict did the borrowed money pay for its interest, over complete calendar
                   months with interest already posted. "Paid" / "cost" only when the
                   sign survives spreading the unsplit dollars at the window's own
                   borrowed share (no new constant; otherwise "too_close").
  tax_triggered    an estimate of the tax the year's realized sales have triggered
                   (ST and LT netted first; rates are passed in, never hardcoded here).

`None` always means "couldn't check" and never collapses to 0 or [] (a published None
is not "clean"). Money is rendered as plain text with a raw `$`: a caller that puts
these strings through `st.markdown` must escape `$` (it would otherwise pair into
LaTeX).
"""
from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta

from stock_analyzer import (
    account,
    account_row_trust,
    performance_review,
    period_attribution,
    period_inputs,
    tax_report,
)

_MINUS = "−"   # the true minus sign, never a hyphen
_TITLE = "\U0001f9ed Is this account earning extra?"

_WARN = "⚠️ "
_INFO = "ℹ️ "

_OFFLINE_TEXT = ("Couldn't read your deposits ledger or account records just now, so nothing "
                 "is shown. This is not the same as zero; try again shortly.")
_NO_BASELINE_TEXT = "Starting point not set — set a baseline on \U0001f4b0 Account."
_AMBIGUOUS_TEXT = ("More than one starting point is recorded on \U0001f4b0 Account; fix the "
                   "deposits ledger before this can be computed.")
_NO_ROW_TEXT = ("No end-of-day account record after the starting point is usable yet (its cash "
                "must be read after the close and match your journal), so nothing is shown.")
# period_attribution's pre_coverage_reason values that are NOT about the opening record
# ("no_start_record" is the only start-record one).
_END_RECORD_REASONS = ("no_valid_end_record", "no_end_record_yet", "no_sessions")
_NO_READING_WHY = ("it has no usable end-of-day reading: no net equity, or its cash wasn't read "
                   "after that day's close")


# --------------------------------------------------------------------------- helpers

def _f(v) -> "float | None":
    """float, or None for None / NaN / inf / non-numeric. Never truthiness."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _num0(v) -> float:
    """A ledger sum (always numeric by construction): missing reads as 0.0 here only."""
    x = _f(v)
    return 0.0 if x is None else x


def _d(v) -> "date | None":
    """date from a date / datetime / Timestamp / 'YYYY-MM-DD...' string; None otherwise
    (NaT included)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        if v != v:   # NaT
            return None
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _flow_type(row) -> str:
    return str(row.get("flow_type") or "").strip().lower() if isinstance(row, dict) else ""


def _spy_map(spy_closes) -> "dict | None":
    """{date: float > 0} from spy_closes (date or ISO-string keys), or None when
    nothing usable."""
    if not isinstance(spy_closes, dict) or not spy_closes:
        return None
    out: dict = {}
    for k, v in spy_closes.items():
        d, c = _d(k), _f(v)
        if d is not None and c is not None and c > 0:
            out[d] = c
    return out or None


# How far back the start / flow dates may look for a SPY close (a weekend or holiday).
# Module-local data-integrity plumbing, not an investment-policy threshold (same
# precedent as account._ANNUALIZE_CAVEAT_MAX_DAYS); mirrors
# benchmark_mirror.price_on_or_before, so a hole in the SPY history reads as
# unavailable instead of being bridged with an old close.
_SPY_LOOKBACK_DAYS = 5


def _on_or_before(closes: dict, d: date) -> "float | None":
    """Close on d or the nearest earlier date, at most `_SPY_LOOKBACK_DAYS` calendar
    days back. Look-back is allowed for the start and flow dates only, never the end."""
    for delta in range(_SPY_LOOKBACK_DAYS + 1):
        p = closes.get(d - timedelta(days=delta))
        if p is not None:
            return p
    return None


def _empty_since(state: str) -> dict:
    return {
        "state": state, "start_date": None, "start_value": None, "end_date": None,
        "end_value": None, "net_flows": None, "n_flows": None, "account_gain_usd": None,
        "account_return_pct": None, "spy_gain_usd": None, "spy_return_pct": None,
        "gap_usd": None, "skipped_dates": None, "skipped_reasons": None, "excluded": None,
    }


def _empty_lev(state: str) -> dict:
    return {
        "state": state, "period_start": None, "period_end": None, "borrowed_usd": None,
        "own_usd": None, "interest_usd": None, "net_usd": None, "unsplit_usd": None,
        "avg_leverage": None, "verdict": None, "gap_months": None,
        "n_unpriced": None, "n_journal_inconsistent": None, "ended_early": None,
        "start_uncovered": None, "pre_coverage_reason": None,
    }


# --------------------------------------------------------------------------- 1. since start

def since_start(flows, account_snapshots_df, trades_df, spy_closes, *, today) -> dict:
    """The account's result since the baseline row versus the same money in SPY.

    State precedence: `offline` (flows, snapshots or the trade journal None, or an
    unreadable ledger row; `account_row_trust.exclude_stale_rows` masks every
    journal-stamped row when the journal is None, so the end row would silently fall
    back weeks)
    > `no_baseline` > `ambiguous` (more than one baseline row) > `no_account_row`
    > `spy_unavailable` / `spy_pending` (account figures still returned) > `ok`."""
    if flows is None or account_snapshots_df is None or trades_df is None:
        return _empty_since("offline")

    baselines = [r for r in flows if _flow_type(r) == "baseline"]
    if not baselines:
        return _empty_since("no_baseline")
    if len(baselines) > 1:
        return _empty_since("ambiguous")
    start_date = _d(baselines[0].get("flow_date"))
    start_value = _f(baselines[0].get("amount"))
    if start_date is None or start_value is None:
        return _empty_since("offline")   # a baseline row we can't read is not "no baseline"

    out = _empty_since("no_account_row")
    out["start_date"], out["start_value"] = start_date, start_value

    masked, excluded = account_row_trust.exclude_stale_rows(account_snapshots_df, trades_df)
    out["excluded"] = excluded

    end_date = end_value = None
    if masked is not None and "snapshot_date" in masked.columns:
        for _, row in masked.iterrows():
            d = _d(row.get("snapshot_date"))
            if d is None or not performance_review.is_trading_day(d):
                continue
            if not (start_date < d <= today):
                continue
            if not performance_review.valid_account_snapshot_row(row):
                continue
            if end_date is None or d >= end_date:
                ev = _f(row.get("net_equity"))
                if ev is not None:
                    end_date, end_value = d, ev
    if end_date is None:
        return out

    # Why each later session was passed over: the reason `exclude_stale_rows` carries for
    # a masked date, else the generic "no usable end-of-day reading" (a row that failed
    # `valid_account_snapshot_row`); never a cause that may be wrong.
    masked_why = {}
    for x in excluded:
        xd = _d(x.get("snapshot_date"))
        if xd is not None and isinstance(x.get("reason"), str):
            masked_why[xd] = x["reason"]
    skipped = {}
    if "snapshot_date" in account_snapshots_df.columns:
        for v in account_snapshots_df["snapshot_date"]:
            d = _d(v)
            if d is not None and end_date < d <= today and performance_review.is_trading_day(d):
                skipped[d] = masked_why[d] if d in masked_why else _NO_READING_WHY
    out["skipped_dates"] = sorted(skipped)
    out["skipped_reasons"] = dict(skipped)
    out["end_date"], out["end_value"] = end_date, end_value

    # Flows in (start, end] only: a row ON start_date is already inside the baseline
    # amount, a row before it is out of scope.
    window: list = []
    for r in flows:
        t = _flow_type(r)
        if t not in ("deposit", "withdrawal"):
            continue
        fd = _d(r.get("flow_date"))
        if fd is None:
            return _empty_since("offline")
        if not (start_date < fd <= end_date):
            continue
        amt = _f(r.get("amount"))
        if amt is None:
            return _empty_since("offline")
        window.append((fd, amt if t == "deposit" else -amt, r))
    net_flows = sum(s for _, s, _ in window)
    flow_rows = [r for _, _, r in window]
    out["net_flows"] = round(net_flows, 2)
    out["n_flows"] = len(window)
    out["account_gain_usd"] = round(end_value - start_value - net_flows, 2)
    mwr = account.money_weighted_return(start_value, start_date, end_value, end_date, flow_rows)
    out["account_return_pct"] = None if mwr is None else mwr.get("period_return_pct")

    closes = _spy_map(spy_closes)
    if closes is None:
        out["state"] = "spy_unavailable"
        return out
    if end_date not in closes:
        out["state"] = "spy_pending"   # never reuse an older close for the end
        return out
    p_start = _on_or_before(closes, start_date)
    if p_start is None:
        out["state"] = "spy_unavailable"
        return out
    units = start_value / p_start
    for fd, signed, _ in window:
        p = _on_or_before(closes, fd)
        if p is None:
            out["state"] = "spy_unavailable"
            return out
        units += signed / p
    shadow_value = units * closes[end_date]
    out["spy_gain_usd"] = round(shadow_value - start_value - net_flows, 2)
    smwr = account.money_weighted_return(start_value, start_date, shadow_value, end_date, flow_rows)
    out["spy_return_pct"] = None if smwr is None else smwr.get("period_return_pct")
    out["gap_usd"] = round(end_value - shadow_value, 2)
    out["state"] = "ok"
    return out


# --------------------------------------------------------------------------- 2. leverage

def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _first_complete_month(first_start: date) -> tuple:
    """(year, month) of the first calendar month a period can start on: the month of
    `first_start` when it is the 1st or that month's first trading day, else the next."""
    y, m = first_start.year, first_start.month
    first_td = date(y, m, 1)
    while not performance_review.is_trading_day(first_td):
        first_td = date.fromordinal(first_td.toordinal() + 1)
    if first_start.day == 1 or first_start <= first_td:
        return (y, m)
    return (y + 1, 1) if m == 12 else (y, m + 1)


def _label(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def _months_between(lo: tuple, hi: tuple) -> list:
    out, cur = [], lo
    while cur <= hi:
        out.append(_label(*cur))
        cur = (cur[0] + 1, 1) if cur[1] == 12 else (cur[0], cur[1] + 1)
    return out


def leverage_window(account_snapshots_df, today) -> "tuple[date, date] | None":
    """`(start, end)` of the complete-calendar-month window the leverage line covers,
    BEFORE `leverage_verdict` trims its end to the last month with posted interest; None
    when there is none (no valid account row, or no month has ended since the first
    measurable one).

    First month = the first calendar month a period can start on (the month of
    `performance_review.first_measurable_period_start` when that day is the 1st or the
    month's first session, else the next month). Last month = the month before
    `today`'s. `leverage_verdict` calls this, so the two cannot diverge: a caller that
    loads the engine inputs for it must build them with
    `period_inputs.load_engine_inputs(db, start, end)` over at least this window (the
    daily_snapshots read is scoped to [start - 14d, end], and a missing close drops a
    position-day from the borrowed figure)."""
    first_start = performance_review.first_measurable_period_start(account_snapshots_df)
    if first_start is None:
        return None
    first = _first_complete_month(first_start)
    # the last month that ended before today is always the previous calendar month
    last = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    if first > last:
        return None
    return date(first[0], first[1], 1), _month_end(*last)


def leverage_verdict(inputs, spy_closes, *, today, reference_leverage) -> dict:
    """Did borrowed money pay for its interest, over complete months with interest posted.

    `inputs` is `period_inputs.load_engine_inputs(db, start, end)` or None, built over at
    least `leverage_window(account_snapshots_df, today)` (see its docstring). States:
    `offline`, `no_complete_month`, `interest_gap` (figures returned, net / verdict
    None), `unknown_split`, `incomplete` (figures returned, verdict None: the engine
    dropped position-days it couldn't price or match to the journal, or the window
    ended early; `n_unpriced`, `n_journal_inconsistent`, `ended_early` say which),
    `ok`. A verdict (`paid` / `cost` / `too_close`) only in `ok`."""
    if not isinstance(inputs, dict):
        return _empty_lev("offline")
    acct = inputs.get("account_snapshots_df")
    if acct is None:
        return _empty_lev("offline")
    win = leverage_window(acct, today)
    if win is None:
        return _empty_lev("no_complete_month")
    start, end = win
    first, last = (start.year, start.month), (end.year, end.month)

    def _core(period_end):
        try:
            return period_inputs.compute_period_core(
                inputs, start, period_end, spy_prices_by_date=spy_closes,
                reference_leverage=reference_leverage)
        except Exception:  # noqa: BLE001 -- an engine failure is "couldn't check", not a crash
            return None

    core = _core(end)
    recon = core.get("recon") if isinstance(core, dict) else None
    income = recon.get("income") if isinstance(recon, dict) else None
    if income is None:
        return _empty_lev("offline")
    last_charged = income.get("last_interest_charged_month")
    if last_charged is None:
        return _empty_lev("no_complete_month")
    try:
        ly, lm = int(str(last_charged)[:4]), int(str(last_charged)[5:7])
    except ValueError:
        return _empty_lev("offline")
    if (ly, lm) < last:
        if (ly, lm) < first:
            return _empty_lev("no_complete_month")
        last, end = (ly, lm), _month_end(ly, lm)
        core = _core(end)
        recon = core.get("recon") if isinstance(core, dict) else None
        income = recon.get("income") if isinstance(recon, dict) else None
        if income is None:
            return _empty_lev("offline")

    out = _empty_lev("ok")
    out["period_start"], out["period_end"] = start, end
    window_months = set(_months_between(first, last))
    gap_raw = income.get("interest_coverage_gap_months")
    gap = sorted(m for m in (gap_raw if isinstance(gap_raw, list) else []) if m in window_months)
    out["gap_months"] = gap

    # Position-days the engine left out (unpriced, or contradicted by the journal) are
    # silently absent from `borrowed`; a window that ended early is a shorter window.
    unpriced, bad = recon.get("unpriced"), recon.get("journal_inconsistent")
    early = recon.get("d1_short_of_period_end")
    out["n_unpriced"] = len(unpriced) if isinstance(unpriced, list) else None
    out["n_journal_inconsistent"] = len(bad) if isinstance(bad, list) else None
    out["ended_early"] = early if isinstance(early, bool) else None
    # A window whose opening record isn't a valid anchor ("pre_coverage") has no
    # trustworthy start, so it can't carry a verdict either (lead, review round 1).
    start_uncovered = recon.get("status") not in ("ok", "partial")
    out["start_uncovered"] = start_uncovered
    pcr = recon.get("pre_coverage_reason")
    out["pre_coverage_reason"] = pcr if isinstance(pcr, str) else None   # which record is missing
    incomplete = (not isinstance(unpriced, list) or not isinstance(bad, list)
                  or not isinstance(early, bool) or bool(unpriced) or bool(bad) or early
                  or start_uncovered)

    sm = period_attribution.split_moves(recon)
    interest = _f(period_attribution.interest_posted(recon))
    ls = recon.get("leverage_split")
    avg_lev = _f(ls.get("avg_leverage")) if isinstance(ls, dict) else None
    out["interest_usd"] = interest
    out["avg_leverage"] = avg_lev
    borrowed = own = unsplit = None
    if isinstance(sm, dict):
        borrowed, own = _f(sm.get("borrowed")), _f(sm.get("own"))
        pp = recon.get("pos_pnl")
        parts = (_f(sm.get("unsplit")),
                 _f(pp.get("bought_leg")) if isinstance(pp, dict) else None,
                 _f(pp.get("round_trip")) if isinstance(pp, dict) else None)
        unsplit = None if any(p is None for p in parts) else sum(parts)
    out["borrowed_usd"], out["own_usd"], out["unsplit_usd"] = borrowed, own, unsplit

    if gap:
        out["state"] = "interest_gap"
        return out
    if borrowed is None:
        out["state"] = "unknown_split"
        return out
    if interest is None:
        return _empty_lev("offline")
    net = borrowed + interest
    out["net_usd"] = net
    if incomplete:
        out["state"] = "incomplete"
        return out

    # A net that rounds to $0 is "too close", the same call as the card's "Level with SPY".
    if _whole(net) == "0" or avg_lev is None or avg_lev <= 1 or unsplit is None:
        out["verdict"] = "too_close"
        return out
    f = min(max(1.0 - 1.0 / avg_lev, 0.0), 1.0)
    net_spread = net + unsplit * f
    if net > 0 and net_spread > 0:
        out["verdict"] = "paid"
    elif net < 0 and net_spread < 0:
        out["verdict"] = "cost"
    else:
        out["verdict"] = "too_close"
    return out


# --------------------------------------------------------------------------- 3. tax

def tax_triggered(trades_df, *, tax_year, st_rate, lt_rate, today) -> dict:
    """Estimated tax the `tax_year` realized sales have triggered. ST (with unknown-term
    gains, counted short-term) and LT are netted before the rates apply. `st_rate` /
    `lt_rate` are echoed back so the display strings can name them."""
    out = {
        "state": "offline", "tax_year": tax_year, "st_net": None, "lt_net": None,
        "unknown_gain": None, "n_unknown_counted": None, "n_unknown_left_out": None,
        "tax_usd": None, "net_loss": None,
        "n_wash": None, "reconciles": None, "st_rate": st_rate, "lt_rate": lt_rate,
    }
    ledger = tax_report.build_realized_lot_ledger(trades_df, tax_year, today)
    if ledger is None:
        return out
    unknown, st_g, lt = (_num0(ledger.get(k)) for k in ("unknown_gain", "st_gain", "lt_gain"))
    st = st_g + unknown
    if st >= 0 and lt >= 0:
        tax = st * st_rate + lt * lt_rate
    elif st <= 0 and lt <= 0:
        tax = 0.0
    elif st > 0 > lt:
        tax = max(st + lt, 0.0) * st_rate
    else:   # lt > 0 > st
        tax = max(st + lt, 0.0) * lt_rate
    rows = ledger.get("rows")
    rows = rows if isinstance(rows, list) else []
    out.update({
        "state": "ok",
        "st_net": round(st, 2),
        "lt_net": round(lt, 2),
        "unknown_gain": round(unknown, 2),
        # unknown-term rows the ledger could value (summed, counted short-term) vs rows it
        # couldn't value at all (gain None: left out of every figure)
        "n_unknown_counted": sum(1 for r in rows if r.get("term") == "Unknown"
                                 and _f(r.get("gain")) is not None),
        "n_unknown_left_out": sum(1 for r in rows if r.get("term") == "Unknown"
                                  and _f(r.get("gain")) is None),
        "tax_usd": round(tax, 2),
        "net_loss": (st + lt) < 0,
        "n_wash": sum(1 for r in rows
                      if isinstance(r.get("wash_sale_status"), dict)
                      and r["wash_sale_status"].get("status") == "violation"),
        "reconciles": ledger.get("reconciles"),
    })
    return out


# --------------------------------------------------------------------------- formatting

def _whole(x: float) -> str:
    return f"{abs(x):,.0f}"


def _usd0(x: float) -> str:
    """Unsigned whole dollars: $7,153."""
    return f"${_whole(x)}"


def _usd2(x: float) -> str:
    """Whole cents (the starting point only): $7,152.99."""
    return ("" if x >= 0 else _MINUS) + f"${abs(x):,.2f}"


def _signed_usd(x: float) -> str:
    n = _whole(x)
    if n == "0":
        return "$0"
    return ("+" if x > 0 else _MINUS) + "$" + n


def _signed_pct(x: float) -> str:
    n = f"{abs(x):.1f}"
    if n == "0.0":
        return "0.0%"
    return ("+" if x > 0 else _MINUS) + n + "%"


def _dstr(d: date, *, weekday: bool = True, year: bool = False) -> str:
    s = f"{d:%b} {d.day}"
    if weekday:
        s = f"{d:%a} " + s
    return s + (f", {d.year}" if year else "")


def _and_join(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _month_name(y: int, m: int) -> str:
    return date(y, m, 1).strftime("%B")


def _month_range_label(start: date, end: date) -> str:
    """'October 2026', 'October–November 2026' or 'December 2026–January 2027'."""
    a, b = (start.year, start.month), (end.year, end.month)
    if a == b:
        return f"{_month_name(*a)} {a[0]}"
    if a[0] == b[0]:
        return f"{_month_name(*a)}–{_month_name(*b)} {a[0]}"
    return f"{_month_name(*a)} {a[0]}–{_month_name(*b)} {b[0]}"


def _rate_text(rate) -> "str | None":
    r = _f(rate)
    return None if r is None else f"{r * 100:.0f}%"


def _state(d) -> str:
    return d.get("state") if isinstance(d, dict) else "offline"


# --------------------------------------------------------------------------- card model

def _asof(since: dict, *, email: bool = False) -> "str | None":
    sd, sv, ed = since.get("start_date"), since.get("start_value"), since.get("end_date")
    if sd is None or sv is None:
        return None
    year = ed is not None and sd.year != ed.year
    start_part = f"starting point {_usd2(sv)} on {_dstr(sd, weekday=False, year=year)}"
    tail = "" if email else ", when tracking began"
    if ed is None:
        return (start_part if email else start_part[0].upper() + start_part[1:]) + tail
    head = f"{'as of' if email else 'As of'} {_dstr(ed)} close"
    return f"{head} · {start_part}{tail}"


def _since_parts(since: dict) -> dict:
    """{headline, tone, subline, warning, info} for a since_start dict."""
    res = {"headline": None, "tone": "neutral", "subline": None, "warning": None, "info": None}
    st = _state(since)
    if st == "offline":
        res["warning"] = _WARN + _OFFLINE_TEXT
        return res
    if st == "no_baseline":
        res["warning"] = _WARN + _NO_BASELINE_TEXT
        return res
    if st == "ambiguous":
        res["warning"] = _WARN + _AMBIGUOUS_TEXT
        return res
    if st == "no_account_row":
        res["warning"] = _WARN + _NO_ROW_TEXT
        return res

    ed = since.get("end_date")
    skipped = since.get("skipped_dates")
    if isinstance(skipped, list) and skipped and ed is not None:
        why = since.get("skipped_reasons")
        why = why if isinstance(why, dict) else {}
        items = [(_dstr(d), why[d] if isinstance(why.get(d), str) else _NO_READING_WHY)
                 for d in skipped]
        # The reason is never wrapped in parentheses: some carry their own.
        if len(items) == 1:
            res["info"] = (f"{_INFO}{items[0][0]}'s account record was left out: {items[0][1]}. "
                           f"This uses {_dstr(ed)}.")
        else:
            res["info"] = (f"{_INFO}These account records were left out: "
                           + "; ".join(f"{n}: {w}" for n, w in items)
                           + f". This uses {_dstr(ed)}.")

    gain, pct = _f(since.get("account_gain_usd")), _f(since.get("account_return_pct"))
    acct = None
    if gain is not None:
        acct = "Account " + _signed_usd(gain) + (f" ({_signed_pct(pct)})" if pct is not None else "")
    if st == "spy_pending":
        res["subline"] = acct
        res["warning"] = (f"{_WARN}SPY's {_dstr(ed, weekday=False)} close isn't available yet, so the "
                          "comparison isn't shown. An older close is never reused.")
        return res
    if st == "spy_unavailable":
        res["subline"] = acct
        res["warning"] = f"{_WARN}SPY's prices couldn't be read, so the comparison isn't shown."
        return res

    sgain, spct = _f(since.get("spy_gain_usd")), _f(since.get("spy_return_pct"))
    gap = _f(since.get("gap_usd"))
    if acct is not None and sgain is not None:
        res["subline"] = (acct + " · the same money in SPY " + _signed_usd(sgain)
                          + (f" ({_signed_pct(spct)})" if spct is not None else ""))
    else:
        res["subline"] = acct
    if gap is not None:
        n = _whole(gap)
        if n == "0":
            res["headline"] = "Level with SPY"
        elif gap > 0:
            res["headline"], res["tone"] = f"${n} ahead of SPY", "pos"
        else:
            res["headline"], res["tone"] = f"${n} behind SPY", "neg"
    return res


def _lev_row(lev: dict) -> dict:
    row = {"heading": "Borrowed money", "text": None, "caption": None,
           "chip_label": None, "chip_kind": None}
    st = _state(lev)
    if st == "offline":
        row["caption"] = "Couldn't read the records this needs just now; try again shortly."
        return row
    if st == "no_complete_month":
        row["caption"] = ("Needs a complete month with interest posted. The first one in this "
                          "window is shown once its charge posts.")
        return row

    ps, pe = lev.get("period_start"), lev.get("period_end")
    label = _month_range_label(ps, pe)
    row["heading"] = f"Borrowed money, {label}"
    borrowed, interest = _f(lev.get("borrowed_usd")), _f(lev.get("interest_usd"))
    avg, net = _f(lev.get("avg_leverage")), _f(lev.get("net_usd"))
    unsplit = _f(lev.get("unsplit_usd"))
    nm = pe.month + 1 if pe.month < 12 else 1
    waits = (f"Only complete months with interest already posted count; Robinhood charges for a "
             f"cycle ending around the 24th, so {_month_name(pe.year, nm)} joins after its "
             "charge posts.")

    if st == "unknown_split":
        row["caption"] = ("Moves couldn't be split between borrowed and own money (no valid "
                          "account record to split on), so no figure is shown. " + waits)
        return row

    made = None
    if borrowed is not None:
        made = (f"Made {_signed_usd(borrowed)} on borrowed money"
                + (f" (avg {avg:.1f}× leverage)" if avg is not None else ""))
    if st == "interest_gap":
        row["text"] = made
        gm = lev.get("gap_months")
        months = _and_join([f"{_month_name(int(m[:4]), int(m[5:7]))} {int(m[:4])}"
                            for m in (gm if isinstance(gm, list) else [])])
        row["caption"] = (f"Margin interest isn't recorded for {months}, so there's no net result "
                          "or verdict yet. " + waits)
        return row

    if made is not None and interest is not None and net is not None:
        row["text"] = f"{made} · margin interest posted {_signed_usd(interest)} → net {_signed_usd(net)}"
    if st == "incomplete":
        n_p, n_j, early = lev.get("n_unpriced"), lev.get("n_journal_inconsistent"), lev.get("ended_early")
        n_days = (n_p if isinstance(n_p, int) else 0) + (n_j if isinstance(n_j, int) else 0)
        why = []
        if n_days:
            why.append(f"{n_days} position-day{'' if n_days == 1 else 's'} couldn't be priced or "
                       "matched to your journal")
        elif not (isinstance(n_p, int) and isinstance(n_j, int)):
            why.append("some position-days couldn't be checked against your journal")
        if lev.get("start_uncovered") is True:
            # pre_coverage_reason can name an END-record problem; only the start-record
            # reasons (and an unknown / absent one) keep the start wording.
            if lev.get("pre_coverage_reason") in _END_RECORD_REASONS:
                why.append("there's no usable account record inside the window")
            else:
                why.append("the record at the start of the window isn't usable")
        if early is True:
            why.append("the last complete record is before the month's end")
        elif not isinstance(early, bool):
            why.append("the window's end couldn't be confirmed")
        why = why or ["some inputs couldn't be confirmed"]
        lead = _and_join(why)
        row["caption"] = (lead[0].upper() + lead[1:] + ", so these figures are incomplete and "
                          "there's no verdict. 📄 Reports lists them. " + waits)
        return row
    cap = []
    material = unsplit is not None and _whole(unsplit) != "0"
    if material:
        cap.append(f"{_usd0(unsplit)} of moves couldn't be split between borrowed and own money "
                   "(first-day buys and same-day round trips).")
    verdict = lev.get("verdict")
    if verdict == "paid":
        row["chip_label"], row["chip_kind"] = "Leverage paid", "pay"
        if material:
            cap.append(f"Even if those moves are split the same way as the rest of {label}, "
                       "leverage still paid.")
    elif verdict == "cost":
        row["chip_label"], row["chip_kind"] = "Leverage cost you", "nopay"
        if material:
            cap.append(f"Even if those moves are split the same way as the rest of {label}, "
                       "leverage still cost money.")
    elif verdict == "too_close":
        row["chip_label"], row["chip_kind"] = "Too close to call", "close"
        if net is not None and _whole(net) == "0":
            cap.append("The net is too close to zero to call.")
        elif avg is None:
            cap.append("Average leverage couldn't be read, so no verdict.")
        elif avg <= 1:
            cap.append("Average leverage was 1× or less, so no verdict.")
        elif material:
            cap.append(f"Split the same way as the rest of {label}, those moves could flip the "
                       "result, so there's no verdict.")
        else:
            cap.append("The net is too close to zero to call.")
    cap.append(waits)
    row["caption"] = " ".join(cap)
    return row


def _tax_row(tax: dict) -> dict:
    year = tax.get("tax_year") if isinstance(tax, dict) else None
    row = {"heading": f"Tax already triggered by {year} sales (estimate)",
           "text": None, "caption": None}
    if _state(tax) != "ok":
        row["text"] = "Couldn't read your trade journal just now, so no figure is shown."
        return row
    st, lt = _f(tax.get("st_net")), _f(tax.get("lt_net"))
    usd = _f(tax.get("tax_usd"))
    if st is None or lt is None or usd is None:
        row["text"] = "Couldn't read your trade journal just now, so no figure is shown."
        return row
    st_r = _rate_text(tax.get("st_rate"))
    lt_r = _rate_text(tax.get("lt_rate"))
    at_st = f" at {st_r}" if st_r else ""
    at_lt = f" at {lt_r}" if lt_r else ""
    net = st + lt
    if tax.get("net_loss"):
        row["text"] = f"About $0: net loss of {_usd0(net)} so far"
    elif st >= 0 and lt >= 0:
        parts = []
        if _whole(st) != "0" and _whole(lt) != "0":
            parts = [f"short-term gain of {_usd0(st)}{at_st}", f"long-term gain of {_usd0(lt)}{at_lt}"]
            body = " plus ".join(parts)
        elif _whole(st) != "0":
            body = f"net short-term gain of {_usd0(st)}{at_st}"
        elif _whole(lt) != "0":
            body = f"net long-term gain of {_usd0(lt)}{at_lt}"
        else:
            body = "no taxable gain so far"
        row["text"] = f"About {_usd0(usd)}: {body}"
    elif _whole(net) == "0" or net < 0:
        row["text"] = f"About $0: short- and long-term results net to {_usd0(net)}"
    elif st > 0 > lt:
        row["text"] = (f"About {_usd0(usd)}: short-term gain of {_usd0(st)} less a long-term loss "
                       f"of {_usd0(lt)}, net {_usd0(net)}{at_st}")
    else:
        row["text"] = (f"About {_usd0(usd)}: long-term gain of {_usd0(lt)} less a short-term loss "
                       f"of {_usd0(st)}, net {_usd0(net)}{at_lt}")

    counted, left_out = tax.get("n_unknown_counted"), tax.get("n_unknown_left_out")
    n_counted = counted if isinstance(counted, int) else 0
    has_left_out = isinstance(left_out, int) and left_out > 0
    cap = ["An estimate at the top federal bracket, not your actual tax."]
    if n_counted > 0 or has_left_out:
        cap.append("Short- and long-term results are netted first; sales with an unknown holding "
                   f"period are counted as short-term ({n_counted} this year).")
    else:
        cap.append("Short- and long-term results are netted first.")
    if has_left_out:
        cap.append("1 sale couldn't be valued (its cost is unknown), so it is left out of this figure."
                   if left_out == 1 else
                   f"{left_out} sales couldn't be valued (their cost is unknown), so they are left "
                   "out of this figure.")
    n_wash = tax.get("n_wash")
    if isinstance(n_wash, int) and n_wash > 0:
        # A disallowed (deferred) loss can only raise the year's taxable gain, never lower it.
        cap.append("1 sale was a wash sale; its deferred loss isn't reflected here, so the real "
                   "tax may be higher than this estimate." if n_wash == 1
                   else f"{n_wash} sales were wash sales; their deferred losses aren't reflected "
                   "here, so the real tax may be higher than this estimate.")
    rec = tax.get("reconciles")
    if rec is True:
        cap.append("Ledger reconciles with your journal ✓.")
    elif rec is False:
        cap.append(f"{_WARN}The ledger does not reconcile with your journal, so treat this "
                   "figure as unreliable.")
    cap.append("The same money left in SPY would owe nothing yet. Shown on its own line and "
               "never subtracted from the gap above.")
    row["caption"] = " ".join(cap)
    return row


def card_model(since, lev, tax) -> dict:
    """Render-ready strings for the Summary card (see the approved mockup,
    docs/mockups/2026-11-15-account-scorecard-mockup.html). The tax rates printed are
    the ones `tax_triggered` echoes in `tax["st_rate"]` / `tax["lt_rate"]`, so the rate
    shown is always the rate used."""
    since = since if isinstance(since, dict) else _empty_since("offline")
    parts = _since_parts(since)
    return {
        "title": _TITLE,
        "asof": _asof(since),
        "headline": parts["headline"],
        "tone": parts["tone"],
        "subline": parts["subline"],
        "warning": parts["warning"],
        "info": parts["info"],
        "lev_row": _lev_row(lev),
        "tax_row": _tax_row(tax if isinstance(tax, dict) else {}),
    }


def email_lines(since, lev, tax) -> list:
    """The plain lines of the Sunday email block (mockup section 5): fewer when a part
    is unknown, a warning in place of the gap when SPY is pending / unavailable, and a
    "couldn't be read this week" line when the leverage or tax part is offline (never a
    silent drop)."""
    since = since if isinstance(since, dict) else _empty_since("offline")
    parts = _since_parts(since)
    asof = _asof(since, email=True)
    lines = [f"{_TITLE} {asof}" if asof else _TITLE]
    if parts["headline"] is not None and parts["subline"] is not None:
        sub = parts["subline"]
        lines.append(f"{parts['headline']}: {sub[0].lower() + sub[1:]}")
    elif parts["subline"] is not None and parts["warning"] is not None:
        lines.append(f"{parts['subline']}. {parts['warning']}")
    elif parts["warning"] is not None:
        lines.append(parts["warning"])

    st = _state(lev)
    if st in ("ok", "interest_gap", "incomplete"):
        ps, pe = lev.get("period_start"), lev.get("period_end")
        label = _month_range_label(ps, pe)
        if st == "incomplete" and _f(lev.get("net_usd")) is not None:
            lines.append(f"Borrowed money, {label}: net {_signed_usd(_f(lev.get('net_usd')))} "
                         "after interest (incomplete, no verdict)")
        elif st == "ok" and _f(lev.get("net_usd")) is not None:
            phrase = {"paid": "leverage paid", "cost": "leverage cost you",
                      "too_close": "too close to call"}.get(lev.get("verdict"))
            lines.append(f"Borrowed money, {label}: net {_signed_usd(_f(lev.get('net_usd')))} "
                         f"after interest" + (f" ({phrase})" if phrase else ""))
        elif st == "interest_gap":
            lines.append(f"Borrowed money, {label}: no net result yet (margin interest isn't "
                         "recorded for every month)")
    elif st == "offline":
        lines.append("Borrowed money: couldn't be read this week.")

    if _state(tax) == "ok" and _f(tax.get("tax_usd")) is not None:
        year = tax.get("tax_year")
        if tax.get("net_loss"):
            lines.append(f"Tax already triggered by {year} sales: about $0 (net loss so far; "
                         "estimate)")
        else:
            lines.append(f"Tax already triggered by {year} sales: about "
                         f"{_usd0(_f(tax.get('tax_usd')))} (estimate; SPY would owe nothing yet)")
    elif _state(tax) == "offline":
        lines.append("Tax already triggered: couldn't be read this week.")
    return lines
