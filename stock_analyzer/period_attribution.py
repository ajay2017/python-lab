"""
Period report engine (C2): where the money went over a window -- S1 (period
result + reconciliation to net equity), S3 (leverage & margin), period
presets and the data-trust strip core.  Plan: docs/plans/period-report.md.

Pure: no streamlit, no db, no network, no clock (callers pass `today`).
Display-only: feeds no gate, threshold or recommendation, and has
no dependence on risk / exit_advisor / risk_advisor / scoring / ranking (pinned by
tests/test_period_attribution.py).  Every threshold-like input (the "days
above" leverage level) is passed in by the caller from constants.

RECONCILIATION IDENTITY (d0 = the NYSE session before the period, d1 = the
latest valid account row on or before the period end; window = (d0, d1]):

    dE = PosPnL + F + I + R

PosPnL is built per ticker per session interval [p -> d] from trade prices on
the buy day and the sell day (a close-to-close mark misses both), VWAP per
side: q1 = min(q_sell, s_p), q2 = min(q_sell - q1, q_buy)
    held_through = (s_p - q1) * (c_d - c_p)
    sold_leg     = q1 * (f_sell - c_p)
    round_trip   = q2 * (f_sell - f_buy)
    bought_leg   = (q_buy - q2) * (c_d - f_buy)
For a ticker with a SPLIT row inside an interval the interval's whole P&L is
computed from values (s_d*c_d - s_p*c_p + sells - buys) and booked to
held_through -- exact, but not separable into the four legs.

R (residual, "unexplained") = R_cash + R_book, R_cash = dC - [(S-B) + F + I];
R_book = R - R_cash (equals dG - dV, journal-replay book vs recorded book, in
the clean case; an oversell clamp or an unpriced ticker lands here too, which
is the point of showing it).  The book gap G - V is reported at both ends.

Signs: income events keep their STORED sign in I (margin interest negative,
dividends positive).  The fee sign is not settled until the C0 owner SQL
(income events by type x month x sign) has been read -- fees are summed as
stored and never flipped here.

Sentinels: a missing day/record/close is "unknown", never zero.  E(d0) not a
valid account row -> status "pre_coverage" (holdings-only, no residual);
flows or income None -> that line None and the residual None; a held interval
with no close -> listed in `unpriced`, its P&L excluded.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from stock_analyzer.broker_sync import canonical_income_events
from stock_analyzer.capital_vs_margin import (
    holdings_by_date,
    interest_partition,
    resolve_interest_charged,
    split_window_tickers,
    window_income_events,
)
from stock_analyzer.market_time import ET
from stock_analyzer.performance_review import (
    is_trading_day,
    last_trading_day_on_or_before,
    prior_trading_day,
    valid_account_snapshot_row,
)

_EPS = 1e-9
_SHARE_TOL = 1e-6  # float noise when comparing replayed vs traded share counts
_CENT_TOL = 0.005  # rounding/display tolerance (half a cent), not a policy threshold

PRESETS = (
    "last_week", "last_month", "this_quarter", "last_quarter",
    "this_tax_year", "since_tracking", "custom",
)


# --- coercion helpers -------------------------------------------------------

def _f(v) -> "float | None":
    """Safe float: None / NaN / junk -> None."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _to_date(v) -> "date | None":
    if v is None:
        return None
    try:
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        if hasattr(v, "date") and not isinstance(v, str):
            return v.date()
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _et_date(ts) -> "date | None":
    """America/New_York calendar date of a timestamptz value (a 21:30 ET trade
    is that ET day; naive values are treated as UTC, same as the rest of the
    app)."""
    try:
        t = pd.to_datetime(ts, utc=True, errors="coerce", format="ISO8601")
    except Exception:
        return None
    if t is None or pd.isna(t):
        return None
    return t.tz_convert(ET).date()


def _dict(x) -> dict:
    return x if x is not None else {}


def _list(x) -> list:
    return x if x is not None else []


def _records(df) -> "list[dict]":
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict("records")


# --- periods ---------------------------------------------------------------

def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def resolve_period(preset, today: date, custom_start: "date | None" = None,
                   custom_end: "date | None" = None, tracking_start: "date | None" = None):
    """(start, end) ET calendar dates for a preset, or None for an invalid range.

    last_week = the Mon-Sun calendar week ending on the most recent Sunday ON
    OR BEFORE `today` (so on a Sunday it is the week that just ended, which is
    what a Sunday digest wants; Mon-Sat give the week before the current one).
    last_month / last_quarter are full previous calendar periods;
    this_quarter / this_tax_year (calendar year) / since_tracking end at
    `today`.  `tracking_start` = performance_review.first_measurable_period_start
    (the caller supplies it; None -> since_tracking is None).  custom needs
    start <= end <= today."""
    if not isinstance(today, date):
        return None
    if preset == "last_week":
        end = today - timedelta(days=(today.weekday() + 1) % 7)  # most recent Sunday <= today
        return end - timedelta(days=6), end
    if preset == "last_month":
        first_this = today.replace(day=1)
        end = first_this - timedelta(days=1)
        return end.replace(day=1), end
    if preset == "this_quarter":
        return _quarter_start(today), today
    if preset == "last_quarter":
        cur = _quarter_start(today)
        end = cur - timedelta(days=1)
        return _quarter_start(end), end
    if preset == "this_tax_year":
        return date(today.year, 1, 1), today
    if preset == "since_tracking":
        ts = _to_date(tracking_start)
        if ts is None or ts > today:
            return None
        return ts, today
    if preset == "custom":
        s, e = _to_date(custom_start), _to_date(custom_end)
        if s is None or e is None or s > e or e > today:
            return None
        return s, e
    return None


# --- S1: reconciliation ----------------------------------------------------

def _empty_recon(status: str, period_start, period_end) -> dict:
    return {
        "status": status, "pre_coverage_reason": None,
        "period_start": period_start, "period_end": period_end,
        "d0": None, "d1": None, "d1_short_of_period_end": False,
        "equity_start": None, "equity_end": None, "equity_change": None, "equity_pct": None,
        "pos_pnl": None, "leverage_split": None, "flows": None, "income": None,
        "residual": None, "book_gap_start": None, "book_gap_end": None,
        "unpriced": [], "journal_inconsistent": [], "coverage": None, "legacy_close_to_close": None,
        "per_ticker": {},
    }


def _sessions(d0: date, d1: date) -> "list[date]":
    out, d = [], d0
    while d <= d1:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _vwap_side(rows) -> "tuple[float, float]":
    q = sum(r[0] for r in rows)
    usd = sum(r[0] * r[1] for r in rows)
    return q, usd


def reconcile_period(*, period_start, period_end, trades_df, account_snapshots_df,
                     daily_snapshots_df, flows, income_events,
                     historical_closes=None, split_tickers=None) -> dict:
    """S1 dict -- see the module docstring for the identity and sentinels.

    `daily_snapshots_df` supplies CLOSES only (its `shares` is int-truncated);
    `historical_closes` {ticker: {date: close}} is the caller-fetched fallback,
    refused for a ticker in `split_tickers` (default: derived from the trades).
    Inputs are never mutated."""
    if trades_df is None or account_snapshots_df is None or daily_snapshots_df is None:
        return _empty_recon("offline", period_start, period_end)

    d0 = prior_trading_day(period_start)
    d1_target = last_trading_day_on_or_before(period_end)
    if d1_target <= d0:
        out = _empty_recon("pre_coverage", period_start, period_end)
        out["d0"] = d0
        out["pre_coverage_reason"] = "no_sessions"
        return out

    # valid account rows by (trading) date.  A holiday/weekend-dated valid row
    # is ignored here (unlike performance_review._valid_snapshots).
    valid: "dict[date, dict]" = {}
    for row in _records(account_snapshots_df):
        d = _to_date(row.get("snapshot_date"))
        if d is not None and is_trading_day(d) and valid_account_snapshot_row(row):
            valid[d] = row

    d1c = max((d for d in valid if d <= d1_target), default=None)
    ok = (d0 in valid) and d1c is not None and d1c > d0
    d1 = d1c if ok else d1_target
    status = "ok" if ok else "pre_coverage"
    reason = None
    if not ok:
        if d0 not in valid:
            reason = "no_start_record"
        else:
            # d0 is valid but nothing valid follows it: records may exist and
            # have failed the after-close cash check, or simply not landed yet
            raw_days = {_to_date(r.get("snapshot_date")) for r in _records(account_snapshots_df)}
            has_raw = any(d is not None and d0 < d <= d1_target for d in raw_days)
            reason = "no_valid_end_record" if has_raw else "no_end_record_yet"
    sess = _sessions(d0, d1)
    window_sessions = sess[1:]

    # shares (raw, split-aware) and trades
    hmap = holdings_by_date(trades_df, d0, d1)
    if split_tickers is None:
        split_tickers = split_window_tickers(trades_df, d0 + timedelta(days=1), d1)
    split_tickers = {str(t).upper() for t in split_tickers}

    trades: "list[tuple]" = []          # (date, ticker, 'B'|'S', shares, price)
    split_dates: "dict[str, set]" = {}
    for row in _records(trades_df):
        d = _et_date(row.get("traded_at"))
        if d is None or not (d0 < d <= d1):
            continue
        t = str(row.get("ticker") or "").upper().strip()
        action = str(row.get("action") or "").upper()
        sh, px = _f(row.get("shares")), _f(row.get("price"))
        if not t or sh is None or px is None or sh <= 0 or px <= 0:
            continue
        if "SPLIT" in action:
            split_dates.setdefault(t, set()).add(d)
        elif "BUY" in action:
            trades.append((d, t, "B", sh, px))
        elif "SELL" in action:
            trades.append((d, t, "S", sh, px))

    # closes
    daily_close: "dict[tuple, float]" = {}
    snap_days = set()
    for row in _records(daily_snapshots_df):
        d = _to_date(row.get("snapshot_date"))
        c = _f(row.get("close_price"))
        t = str(row.get("ticker") or "").upper().strip()
        if d is None or not t or c is None or c <= 0:
            continue
        snap_days.add(d)
        daily_close[(t, d)] = c
    hist = {}
    for t, m in (historical_closes or {}).items():
        hist[str(t).upper()] = {_to_date(k): _f(v) for k, v in (m or {}).items()}

    def close(t, d):
        c = daily_close.get((t, d))
        if c is not None:
            return c
        if t in split_tickers:
            return None
        c = (_dict(hist.get(t))).get(d)
        return c if (c is not None and c > 0) else None

    unpriced: "set[tuple]" = set()
    journal_bad: "set[tuple]" = set()
    bad_names: "set[str]" = set()
    per_ticker: "dict[str, dict]" = {}

    def pt(t):
        return per_ticker.setdefault(t, {
            "held_through": 0.0, "sold_leg": 0.0, "bought_leg": 0.0, "round_trip": 0.0,
            "total": 0.0, "bought_usd": 0.0, "sold_usd": 0.0,
        })

    tot = {"held_through": 0.0, "sold_leg": 0.0, "bought_leg": 0.0, "round_trip": 0.0}
    borrowed = own = unsplit = 0.0
    days_split = days_unsplit = 0
    lev_vals: "list[float]" = []

    for i in range(1, len(sess)):
        p, d = sess[i - 1], sess[i]
        by_t: "dict[str, dict]" = {}
        for (td, t, side, sh, px) in trades:
            if p < td <= d:
                e = by_t.setdefault(t, {"B": [], "S": []})
                e[side].append((sh, px))
        tickers = set(by_t) | set(hmap.get(p, {})) | set(hmap.get(d, {}))
        X = 0.0  # held_through + sold_leg this interval (leverage-split base)
        interval_unsplit = 0.0
        for t in sorted(tickers):
            e = by_t.get(t, {"B": [], "S": []})
            q_buy, b_usd = _vwap_side(e["B"])
            q_sell, s_usd = _vwap_side(e["S"])
            if q_buy or q_sell:
                rec = pt(t)
                rec["bought_usd"] += b_usd
                rec["sold_usd"] += s_usd
            s_p = hmap.get(p, {}).get(t, 0.0)
            s_d = hmap.get(d, {}).get(t, 0.0)
            is_split = any(p < sd <= d for sd in split_dates.get(t, ()))
            if t in bad_names:
                # phantom shares carried from an earlier flagged interval
                journal_bad.add((t, d))
                continue
            if not is_split and abs(s_d - (s_p - q_sell + q_buy)) > _SHARE_TOL:
                # journal replay disagrees with its own trades (oversell clamp,
                # same-timestamp SELL-before-BUY, ...): flag it, exclude the
                # interval, never mislabel it as unpriced.
                journal_bad.add((t, d))
                bad_names.add(t)
                continue
            if is_split:
                need_p, need_d = s_p > _EPS, s_d > _EPS
            else:
                q1 = min(q_sell, s_p)
                q2 = min(q_sell - q1, q_buy)
                need_p = s_p > _EPS
                need_d = (s_p - q1) > _EPS or (q_buy - q2) > _EPS or s_d > _EPS
            c_p = close(t, p) if need_p else None
            c_d = close(t, d) if need_d else None
            miss = False
            if need_p and c_p is None:
                unpriced.add((t, p)); miss = True
            if need_d and c_d is None:
                unpriced.add((t, d)); miss = True
            if miss:
                continue
            v_p = s_p * c_p if need_p else 0.0
            v_d = s_d * c_d if need_d else 0.0
            if is_split:
                held = v_d - v_p + s_usd - b_usd
                sold = bought = rt = 0.0
            else:
                f_sell = s_usd / q_sell if q_sell > _EPS else 0.0
                f_buy = b_usd / q_buy if q_buy > _EPS else 0.0
                held = (s_p - q1) * (c_d - c_p) if (s_p - q1) > _EPS else 0.0
                sold = q1 * (f_sell - c_p) if q1 > _EPS else 0.0
                rt = q2 * (f_sell - f_buy) if q2 > _EPS else 0.0
                bought = (q_buy - q2) * (c_d - f_buy) if (q_buy - q2) > _EPS else 0.0
            if held or sold or bought or rt or q_buy or q_sell or s_p > _EPS:
                rec = pt(t)
                rec["held_through"] += held
                rec["sold_leg"] += sold
                rec["bought_leg"] += bought
                rec["round_trip"] += rt
                rec["total"] += held + sold + bought + rt
            tot["held_through"] += held
            tot["sold_leg"] += sold
            tot["bought_leg"] += bought
            tot["round_trip"] += rt
            X += held + sold
            interval_unsplit += bought + rt
        unsplit += interval_unsplit
        # leverage split for this interval
        row_p = valid.get(p)
        g_p = _f(row_p.get("gross_book")) if row_p else None
        c_p_cash = _f(row_p.get("cash_balance")) if row_p else None
        if c_p_cash is None and row_p:
            e_p = _f(row_p.get("net_equity"))
            c_p_cash = (e_p - g_p) if (e_p is not None and g_p is not None) else None
        e_p0 = (g_p + c_p_cash) if (g_p is not None and c_p_cash is not None) else None
        if row_p and g_p is not None and g_p > 0 and c_p_cash is not None and e_p0 is not None and e_p0 > 0:
            b = X * (max(0.0, -c_p_cash) / g_p)
            borrowed += b
            own += X - b
            days_split += 1
            lv = _f(row_p.get("leverage"))
            if lv is None:
                e_p = _f(row_p.get("net_equity"))
                lv = g_p / e_p if (e_p and e_p > 0) else None
            if lv is not None:
                lev_vals.append(lv)
        else:
            unsplit += X
            days_unsplit += 1

    # Additive per-ticker endpoints (Weekly Debrief shape + price %): shares at
    # d0/d1 and the d0/d1 closes.  A split-window name gets no closes -- a
    # close-to-close % across a split is a fabricated move.
    for t, rec in per_ticker.items():
        rec["shares_start"] = hmap.get(d0, {}).get(t, 0.0)
        rec["shares_end"] = hmap.get(d1, {}).get(t, 0.0)
        is_split_name = t in split_tickers or t in split_dates
        rec["close_start"] = None if is_split_name else close(t, d0)
        rec["close_end"] = None if is_split_name else close(t, d1)

    pos_total = sum(tot.values())
    pos_pnl = dict(tot, total=pos_total)
    leverage_split = {
        "borrowed": borrowed, "own": own, "unsplit": unsplit,
        "days_split": days_split, "days_unsplit": days_unsplit,
        "avg_leverage": (sum(lev_vals) / len(lev_vals)) if lev_vals else None,
    }

    # book gap + legacy close-to-close (names held at both ends)
    def book_v(day):
        v = 0.0
        for t, sh in hmap.get(day, {}).items():
            if sh > _EPS:
                c = close(t, day)
                if c is None:
                    if t not in bad_names:  # journal-flagged names are reported there, not here
                        unpriced.add((t, day))
                else:
                    v += sh * c
        return v

    V0, V1 = book_v(d0), book_v(d1)
    legacy = 0.0
    for t, s0 in hmap.get(d0, {}).items():
        s1 = hmap.get(d1, {}).get(t, 0.0)
        if s0 > _EPS and s1 > _EPS:
            c0, c1 = close(t, d0), close(t, d1)
            if c0 is not None and c1 is not None:
                legacy += min(s0, s1) * (c1 - c0)

    # flows / income over (d0, d1]
    lo = d0 + timedelta(days=1)
    flows_out = None
    F = 0.0
    if flows is not None:
        n = 0
        for f in flows:
            ft = str(f.get("flow_type") or "").strip().lower()
            fd = _to_date(f.get("flow_date"))
            amt = _f(f.get("amount"))
            if ft not in ("deposit", "withdrawal") or fd is None or amt is None or not (lo <= fd <= d1):
                continue
            F += amt if ft == "deposit" else -amt
            n += 1
        flows_out = {"net": F, "n": n}
    income_out = None
    I = 0.0
    if income_events is not None:
        evs = window_income_events(canonical_income_events(list(income_events)), lo, d1)
        div = fees = 0.0
        n = 0
        n_recl = 0
        last = None
        typed = []
        for e in evs:
            et = str(e.get("event_type") or "").strip().lower()
            amt = _f(e.get("amount"))
            if et not in ("dividend", "interest", "fee") or amt is None:
                continue
            n += 1
            if e.get("reclassified_from") == "fee":
                n_recl += 1
            ed = _to_date(e.get("event_date"))
            last = ed if (last is None or (ed is not None and ed > last)) else last
            if et == "dividend":
                div += amt
            elif et == "fee":
                fees += amt
            typed.append(e)
        ri = resolve_interest_charged(interest_partition(typed), "negative")
        I = div + (ri["earned"] - ri["charged"]) + fees
        income_out = {"dividends": div, "interest_charged": ri["charged"],
                      "interest_earned": ri["earned"], "fees": fees, "n": n,
                      "last_event_date": last, "n_reclassified_from_fee": n_recl,
                      "last_interest_charged_month": None,
                      "interest_coverage_gap_months": []}
        # Capture-gap detection (calendar logic, no threshold).  Robinhood posts
        # margin interest once a month near month end, so a month is flagged
        # only when (a) it ENDED on or before the period end, (b) the period
        # touches it with a levered valid account row (cash < 0), and (c) the
        # canonical list (a live FEE counts) holds NO margin-interest charge in
        # that month -- per-month presence, so a missing middle month shows.
        rec_months = set()
        for e in canonical_income_events(list(income_events)):
            if str(e.get("event_type") or "").strip().lower() != "interest":
                continue
            a, ed = _f(e.get("amount")), _to_date(e.get("event_date"))
            if a is not None and a < 0 and ed is not None:
                rec_months.add(ed.strftime("%Y-%m"))
        income_out["last_interest_charged_month"] = max(rec_months) if rec_months else None
        gap = set()
        for dd, row in valid.items():
            if not (lo <= dd <= d1):
                continue
            cash = _f(row.get("cash_balance"))
            if cash is None:
                ne, gb = _f(row.get("net_equity")), _f(row.get("gross_book"))
                cash = (ne - gb) if (ne is not None and gb is not None) else None
            m = dd.strftime("%Y-%m")
            month_end = (dd.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            if cash is not None and cash < 0 and month_end <= period_end and m not in rec_months:
                gap.add(m)
        income_out["interest_coverage_gap_months"] = sorted(gap)

    # account endpoints + residual
    e0 = e1 = g0 = g1 = c0 = c1 = None
    out = _empty_recon(status, period_start, period_end)
    if ok:
        r0, r1 = valid[d0], valid[d1]
        e0, e1 = _f(r0.get("net_equity")), _f(r1.get("net_equity"))
        g0, g1 = _f(r0.get("gross_book")), _f(r1.get("gross_book"))
        c0 = _f(r0.get("cash_balance")); c1 = _f(r1.get("cash_balance"))
        if c0 is None and e0 is not None and g0 is not None:
            c0 = e0 - g0
        if c1 is None and e1 is not None and g1 is not None:
            c1 = e1 - g1
        out["equity_start"], out["equity_end"] = e0, e1
        out["equity_change"] = e1 - e0
        out["equity_pct"] = ((e1 - e0) / e0 * 100.0) if e0 and e0 > 0 else None
        out["book_gap_start"] = (g0 - V0) if g0 is not None else None
        out["book_gap_end"] = (g1 - V1) if g1 is not None else None
        if flows_out is not None and income_out is not None and c0 is not None and c1 is not None:
            s_minus_b = sum(tr[3] * tr[4] for tr in trades if tr[2] == "S") - \
                sum(tr[3] * tr[4] for tr in trades if tr[2] == "B")
            r_total = (e1 - e0) - pos_total - F - I
            r_cash = (c1 - c0) - (s_minus_b + F + I)
            out["residual"] = {
                "total": r_total, "cash": r_cash, "book": r_total - r_cash,
                "pct_of_start": (r_total / e0 * 100.0) if e0 and e0 > 0 else None,
            }
    partial = ok and (out["residual"] is None or bool(unpriced) or bool(journal_bad))
    out.update({
        "status": "partial" if partial else status,
        "pre_coverage_reason": reason,
        "d0": d0, "d1": d1,
        "d1_short_of_period_end": bool(ok and d1 < d1_target),
        "pos_pnl": pos_pnl, "leverage_split": leverage_split,
        "flows": flows_out, "income": income_out,
        "unpriced": sorted(unpriced),
        "journal_inconsistent": sorted(journal_bad),
        "coverage": {
            "trading_days": len(window_sessions),
            "account_days_valid": sum(1 for d in window_sessions if d in valid),
            "snapshot_days": sum(1 for d in window_sessions if d in snap_days),
        },
        "legacy_close_to_close": legacy,
        "per_ticker": per_ticker,
    })
    return out


# --- S3: leverage & margin -------------------------------------------------

def leverage_section(account_snapshots_df, period_start, period_end, reference_leverage) -> dict:
    """Leverage range, closest margin call, days above `reference_leverage`
    (strictly greater) over account_daily_snapshots rows in
    [period_start, period_end].  NULL-leverage rows are excluded and counted
    (`n_null_rows`); rows with leverage but a NULL call distance are counted in
    `n_null_call_rows`.  call_distance_pct < 0 is the % drop that would trigger a
    call; >= 0 means already in call (margin.call_distance), so the CLOSEST
    call is the row with the MAXIMUM call_distance_pct.  maintenance_rate is
    the recorded estimate, never a broker figure."""
    keys = ("start", "end", "min", "max", "mean", "n_days_recorded", "n_trading_days",
            "days_above_reference", "closest_call", "days_in_call", "maintenance_rate",
            "n_null_rows", "n_null_call_rows")
    base = {k: None for k in keys}
    if account_snapshots_df is None:
        return dict(base, status="offline")
    rows = []
    null_rows = 0
    for r in _records(account_snapshots_df):
        d = _to_date(r.get("snapshot_date"))
        if d is None or not (period_start <= d <= period_end):
            continue
        lv = _f(r.get("leverage"))
        if lv is None:
            null_rows += 1
            continue
        rows.append((d, lv, r))
    n_td = len([1 for d in _sessions(period_start, period_end)])
    if not rows:
        return dict(base, status="empty", n_days_recorded=0, n_trading_days=n_td,
                    days_above_reference=0, days_in_call=0, n_null_rows=null_rows,
                    n_null_call_rows=0)
    rows.sort(key=lambda x: x[0])
    levs = [lv for _, lv, _ in rows]
    mn = min(rows, key=lambda x: x[1]); mx = max(rows, key=lambda x: x[1])
    cd_rows = [(d, _f(r.get("call_distance_pct")), r) for d, _, r in rows
               if _f(r.get("call_distance_pct")) is not None]
    closest = None
    if cd_rows:
        d, cd, r = max(cd_rows, key=lambda x: x[1])
        closest = {"date": d, "call_distance_pct": cd, "cushion": _f(r.get("cushion")),
                   "leverage": _f(r.get("leverage"))}
    mr = None
    for _, _, r in reversed(rows):
        mr = _f(r.get("maintenance_rate"))
        if mr is not None:
            break
    return {
        "status": "ok",
        "start": {"date": rows[0][0], "leverage": rows[0][1]},
        "end": {"date": rows[-1][0], "leverage": rows[-1][1]},
        "min": {"date": mn[0], "leverage": mn[1]},
        "max": {"date": mx[0], "leverage": mx[1]},
        "mean": sum(levs) / len(levs),
        "n_days_recorded": len(rows),
        "n_trading_days": n_td,
        "days_above_reference": sum(1 for v in levs if v > reference_leverage),
        "closest_call": closest,
        "days_in_call": sum(1 for _, cd, _ in cd_rows if cd >= 0),
        "maintenance_rate": mr,
        "n_null_rows": null_rows,
        "n_null_call_rows": len(rows) - len(cd_rows),
    }


# --- trust strip -----------------------------------------------------------

def _chip(key, label, state):
    return {"key": key, "label": label, "state": state}


def _usd(x) -> str:
    """Display dollars; `$` is backslash-escaped so Streamlit/Markdown never
    reads a pair as LaTeX."""
    if x is None:
        return "unknown"
    if round(abs(x)) == 0:
        return "\\$0"   # never "-\$0" for a sub-dollar rounding residue
    sign = "-" if x < 0 else ""
    return f"{sign}\\${abs(x):,.0f}"


def trust_strip(recon, lev, *, trades_truncated=None, flows_last_date=None,
                interest_last_posted=None, health_now=None) -> "list[dict]":
    """Chips {key, label, state: ok|warn|unknown}.  Never "ok" for something
    that could not be checked, and never a claim about outages DURING the
    period (feed/cron health is not stored per day) -- `health_now` is today's
    System Trust state and the chip says so."""
    chips: "list[dict]" = []
    rec = recon or {}
    st = rec.get("status")
    cov = rec.get("coverage")
    if st in (None, "offline") or not cov:
        chips.append(_chip("account_records", "Account records: unknown", "unknown"))
        chips.append(_chip("price_marks", "Price marks: unknown", "unknown"))
    else:
        n, a, s = cov["trading_days"], cov["account_days_valid"], cov["snapshot_days"]
        chips.append(_chip("account_records", f"Account records {a}/{n}", "ok" if a == n else "warn"))
        chips.append(_chip("price_marks", f"Price marks {s}/{n}", "ok" if s == n else "warn"))
    if st in (None, "offline"):
        chips.append(_chip("unpriced", "Unpriced: unknown", "unknown"))
    else:
        u = len(_list(rec.get("unpriced")))
        chips.append(_chip("unpriced", f"Unpriced {u}", "ok" if u == 0 else "warn"))
    if st == "pre_coverage":
        why = rec.get("pre_coverage_reason")
        txt = {
            "no_start_record": "No valid account record before the period: holdings-only",
            "no_end_record_yet": f"No valid account record after {rec.get('d0')} in this period -- the end-of-day "
                                 "record may not have landed yet",
            "no_valid_end_record": f"Account records exist after {rec.get('d0')} but none passed the after-close "
                                   "cash check",
            "no_sessions": "No trading sessions in this range",
        }.get(why, "Account records not available for this period: holdings-only")
        chips.append(_chip("coverage", txt, "warn"))
    if _list(rec.get("journal_inconsistent")):
        chips.append(_chip("journal", f"Trade journal order inconsistent for {len(rec['journal_inconsistent'])} "
                           "position(s) -- excluded", "warn"))
    if rec.get("d1_short_of_period_end"):
        chips.append(_chip("d1_short", "Latest account record is before the period end", "warn"))
    resid = rec.get("residual")
    if resid is None:
        chips.append(_chip("unexplained", "Unexplained: unknown", "unknown"))
    else:
        pct = resid.get("pct_of_start")
        pct_s = f" ({abs(pct):.1f}%)" if pct is not None else ""
        incl = " incl. unpriced/journal-excluded" if (_list(rec.get("unpriced")) or _list(rec.get("journal_inconsistent"))) else ""
        chips.append(_chip("unexplained", f"Unexplained {_usd(resid['total'])}{pct_s}{incl}",
                           "ok" if abs(resid["total"]) < _CENT_TOL else "warn"))
    if rec.get("flows") is None:
        chips.append(_chip("flows", "Deposits/withdrawals ledger: unknown", "unknown"))
    elif rec["flows"]["n"] > 0:
        chips.append(_chip("flows", f"Deposits/withdrawals ledger: {rec['flows']['n']} entries in period", "ok"))
    else:
        # none recorded cannot be told from none made -- never "ok"
        chips.append(_chip("flows", f"Deposits/withdrawals ledger: no entries since {flows_last_date or 'ever'}", "warn"))
    if rec.get("income") is None:
        chips.append(_chip("interest", "Margin interest: unknown", "unknown"))
    elif rec["income"].get("interest_coverage_gap_months"):
        lm = rec["income"].get("last_interest_charged_month") or "ever"
        ms = ", ".join(rec["income"]["interest_coverage_gap_months"])
        chips.append(_chip("interest", f"Margin interest not recorded for {ms} (last recorded {lm}) -- import "
                           "the statement on Account > Cash Activity", "warn"))
    elif interest_last_posted is None:
        chips.append(_chip("interest", "Margin interest posts monthly (last posting unknown)", "unknown"))
    else:
        chips.append(_chip("interest", f"Margin interest posts monthly (last {interest_last_posted})", "ok"))
    if trades_truncated is None:
        chips.append(_chip("trades", "Trade journal completeness: unknown", "unknown"))
    elif trades_truncated:
        chips.append(_chip("trades", "Trade journal may be truncated", "warn"))
    else:
        chips.append(_chip("trades", "Trade journal read in full", "ok"))
    if lev is None or lev.get("status") == "offline":
        chips.append(_chip("leverage_records", "Leverage records: unknown", "unknown"))
    elif lev.get("n_trading_days") is None:
        chips.append(_chip("leverage_records", "Leverage records: unknown", "unknown"))
    else:
        a, n = lev.get("n_days_recorded") or 0, lev["n_trading_days"]
        chips.append(_chip("leverage_records", f"Leverage records {a}/{n}", "ok" if a == n else "warn"))
    if health_now is None:
        chips.append(_chip("health_now", "System Trust now: unknown", "unknown"))
    else:
        h = str(health_now).strip()
        chips.append(_chip("health_now", f"System Trust now: {h.upper() if h.lower() == 'ok' else h}",
                           "ok" if h.lower() == "ok" else "warn"))
    return chips


# --- exports ---------------------------------------------------------------

def _fx(v, spec="{:.2f}x") -> str:
    return "unknown" if v is None else spec.format(v)


def _rows(report: dict) -> "list[tuple]":
    """(section, item, value, note) rows shared by the CSV and Markdown."""
    rows: "list[tuple]" = []
    rec = _dict((report or {}).get("recon"))
    lev = _dict((report or {}).get("leverage"))
    pp, ls = rec.get("pos_pnl"), rec.get("leverage_split")
    rows.append(("period", "status", rec.get("status"), ""))
    rows.append(("period", "account change measured", f"{rec.get('d0')} to {rec.get('d1')}", ""))
    rows.append(("s1", "equity_start", rec.get("equity_start"), ""))
    rows.append(("s1", "equity_end", rec.get("equity_end"), ""))
    rows.append(("s1", "equity_change", rec.get("equity_change"), ""))
    rows.append(("s1", "equity_pct", rec.get("equity_pct"), "% of start"))
    if pp:
        for k in ("held_through", "sold_leg", "bought_leg", "round_trip", "total"):
            rows.append(("s1", f"pos_pnl_{k}", pp[k], ""))
    if ls:
        for k in ("borrowed", "own", "unsplit"):
            rows.append(("s1", f"leverage_split_{k}", ls[k], ""))
        rows.append(("s1", "avg_leverage", ls.get("avg_leverage"), ""))
    fl, inc, res = rec.get("flows"), rec.get("income"), rec.get("residual")
    rows.append(("s1", "flows_net", fl["net"] if fl else None, "" if fl else "unknown"))
    for k in ("dividends", "interest_charged", "interest_earned", "fees"):
        rows.append(("s1", k, inc[k] if inc else None, "" if inc else "unknown"))
    rows.append(("s1", "interest_coverage_gap_months",
                 ",".join(inc["interest_coverage_gap_months"]) if inc else None, "unrecorded months" if inc else "unknown"))
    rows.append(("s1", "unexplained_total", res["total"] if res else None, "" if res else "unknown"))
    if res:
        rows.append(("s1", "unexplained_cash", res["cash"], ""))
        rows.append(("s1", "unexplained_book", res["book"], ""))
    rows.append(("s1", "legacy_close_to_close", rec.get("legacy_close_to_close"), "held-at-both-ends close to close"))
    rows.append(("s1", "unpriced", len(_list(rec.get("unpriced"))), ""))
    if lev.get("status") == "ok":
        rows.append(("s3", "leverage_min", lev["min"]["leverage"], str(lev["min"]["date"])))
        rows.append(("s3", "leverage_max", lev["max"]["leverage"], str(lev["max"]["date"])))
        rows.append(("s3", "leverage_mean", lev["mean"], ""))
        rows.append(("s3", "days_above_reference", lev["days_above_reference"], ""))
        cc = lev.get("closest_call")
        rows.append(("s3", "closest_call_pct", cc["call_distance_pct"] if cc else None,
                     str(cc["date"]) if cc else "unknown"))
        rows.append(("s3", "days_in_call", lev["days_in_call"], ""))
        rows.append(("s3", "maintenance_rate_estimate", lev.get("maintenance_rate"), "estimate"))
    else:
        rows.append(("s3", "status", lev.get("status", "unknown"), ""))
    return rows


def format_period_csv(report: dict) -> pd.DataFrame:
    """Flat section/item/value/note rows for S1 + S3 (unknown stays blank)."""
    return pd.DataFrame(_rows(report), columns=["section", "item", "value", "note"])


def format_period_markdown(report: dict) -> str:
    """Readable Markdown of S1 + S3.  Every `$` is escaped (`\\$`) so no pair
    can render as LaTeX; `**` appears only as intended bold."""
    report = report or {}
    rec = _dict(report.get("recon"))
    lev = _dict(report.get("leverage"))
    lines = [
        f"# Period report -- {report.get('period_start')} to {report.get('period_end')}",
        "",
        "**Informational only -- not investment advice.** A look-back; changes no gate, "
        "no recommendation.",
        "",
        "## 1. Period result",
    ]
    st = rec.get("status")
    if st == "offline":
        lines.append("_Offline -- trades, account or price history could not be loaded._")
    else:
        if st == "pre_coverage":
            lines.append("_" + {
                "no_start_record": "No valid account record before the period: holdings-only, no net-equity reconciliation.",
                "no_end_record_yet": f"No valid account record after {rec.get('d0')} in this period (the end-of-day record may not have landed yet): holdings-only.",
                "no_valid_end_record": f"Account records exist after {rec.get('d0')} but none passed the after-close cash check: holdings-only.",
                "no_sessions": "No trading sessions in this range.",
            }.get(rec.get("pre_coverage_reason"), "Account records not available: holdings-only.") + "_")
        else:
            pct = rec.get("equity_pct")
            lines.append(f"- Net equity change ({rec.get('d0')} to {rec.get('d1')}): "
                         f"{_usd(rec.get('equity_change'))}"
                         + (f" ({pct:+.1f}% of start)" if pct is not None else ""))
        pp = rec.get("pos_pnl")
        if pp:
            ls = _dict(rec.get("leverage_split"))
            lines += [
                f"- Price moves on positions held through: {_usd(pp['held_through'])}",
                f"  - of which on borrowed money: {_usd(ls.get('borrowed'))}; own money: {_usd(ls.get('own'))}",
                f"- Last-day moves on positions sold: {_usd(pp['sold_leg'])}",
                f"- First-day moves on positions bought: {_usd(pp['bought_leg'])}",
                f"- Same-day round trips: {_usd(pp['round_trip'])}",
            ]
        fl, inc, res = rec.get("flows"), rec.get("income"), rec.get("residual")
        lines.append(f"- Deposits / withdrawals: {_usd(fl['net']) if fl else 'unknown'}")
        if inc:
            if inc.get("interest_coverage_gap_months"):
                lines.append("- WARNING: margin interest is not recorded for "
                             + ", ".join(inc["interest_coverage_gap_months"])
                             + " (levered, no margin-interest row in that month; last recorded "
                             + str(inc.get("last_interest_charged_month") or "ever")
                             + "); the line below may understate it and the unexplained row absorbs it.")
            if inc.get("n_reclassified_from_fee"):
                lines.append(f"- Note: {inc['n_reclassified_from_fee']} broker FEE charge(s) are counted as margin "
                             "interest (owner rule: an unmatched live FEE is margin interest; the Gold fee stays a fee).")
            lines += [f"- Dividends: {_usd(inc['dividends'])}",
                      f"- Margin interest posted: {_usd(-inc['interest_charged'] + inc['interest_earned'])}",
                      f"- Fees: {_usd(inc['fees'])}"]
        else:
            lines.append("- Dividends / interest / fees: unknown")
        incl = (" (includes the P&L of unpriced / journal-inconsistent positions)"
                if (rec.get("unpriced") or rec.get("journal_inconsistent")) else "")
        lines.append(f"- Unexplained{incl}: {_usd(res['total']) if res else 'unknown'}")
        if rec.get("journal_inconsistent"):
            lines.append(f"- Journal order inconsistent for {len(rec['journal_inconsistent'])} position(s); excluded from the legs above.")
        if rec.get("unpriced"):
            lines.append(f"- Unpriced (excluded, not zero): {len(rec['unpriced'])}")
        lines.append(f"- Close-to-close on names held at both ends: {_usd(rec.get('legacy_close_to_close'))}")
    lines += ["", "## 3. Leverage & margin"]
    if lev.get("status") != "ok":
        lines.append("_No leverage records for this period._")
    else:
        lines.append(f"- Leverage range: {lev['min']['leverage']:.2f}x to {lev['max']['leverage']:.2f}x "
                     f"(start {lev['start']['leverage']:.2f}x, end {lev['end']['leverage']:.2f}x)")
        cc = lev.get("closest_call")
        if cc:
            lines.append(f"- Closest to a margin call: {cc['call_distance_pct']:.1f}% on {cc['date']}"
                         + (f", cushion {_usd(cc['cushion'])}" if cc.get("cushion") is not None else ""))
        lines.append(f"- Days in call: {lev['days_in_call']}; days above reference leverage: "
                     f"{lev['days_above_reference']} of {lev['n_days_recorded']} recorded")
        lines.append("- The maintenance rate is an estimate; a real call is likely closer than shown.")
    chips = _list(report.get("trust"))
    if chips:
        lines += ["", "## Data trust"] + [f"- {c['label']} ({c['state']})" for c in chips]
    return "\n".join(lines) + "\n"


# --- render helpers (C4): pure formatting, no I/O ---------------------------

def usd_plain(x, *, plus: bool = False) -> str:
    """Dollars for st.metric / dataframe cells (NOT markdown): sign BEFORE the
    `$` ("-$1,314"), "unknown" for None, "$0" inside half a cent.  `plus`
    prefixes gains with "+"."""
    if x is None:
        return "unknown"
    if abs(x) < _CENT_TOL or round(abs(x)) == 0:
        return "$0"   # incl. a sub-dollar residue that would print "-$0"
    sign = "-" if x < 0 else ("+" if plus else "")
    return f"{sign}${abs(x):,.0f}"


def interest_posted(recon) -> "float | None":
    """Margin interest posted in the window as a signed P&L figure (charged is
    negative, earned positive); None when income could not be read."""
    inc = _dict(recon).get("income")
    if inc is None:
        return None
    return -inc["interest_charged"] + inc["interest_earned"]


def pre_coverage_reason_text(recon) -> str:
    """Why the account-level reconciliation is unavailable (status pre_coverage)."""
    rec = _dict(recon)
    return {
        "no_start_record": "There is no valid account record for the session before this period, so the "
                           "change in your account can't be measured.",
        "no_end_record_yet": f"There is no valid account record after {rec.get('d0')} in this period (the "
                             "end-of-day record may not have landed yet).",
        "no_valid_end_record": f"Account records exist after {rec.get('d0')} but none passed the after-close "
                               "cash check.",
        "no_sessions": "There are no trading sessions in this range.",
    }.get(rec.get("pre_coverage_reason"), "Account records are not available for this period.")


def book_side_extra(recon) -> str:
    """Suffix for the "Book side" row of What's in Unexplained.  R_book equals
    the book-gap change only in the clean case; the P&L of an unpriced or
    journal-inconsistent position is left out of the waterfall and lands in
    R_book (module docstring), so without this note a non-zero book side next
    to two $0 gaps reads as a contradiction.  "" when no position was left out."""
    rec = _dict(recon)
    names = {r[0] for r in _list(rec.get("unpriced")) + _list(rec.get("journal_inconsistent"))
             if isinstance(r, (tuple, list)) and r}
    if not names:
        return ""
    n = len(names)
    return (f"; includes the move on {n} position{'' if n == 1 else 's'} left out above "
            "because it couldn't be priced or replayed")


def split_moves(recon) -> "dict | None":
    """Borrowed / own split of the moves on positions held at the PRIOR close
    (held-through + last-day-sold legs: the engine splits exactly these two).
    {held_sold, borrowed, own, unsplit}; borrowed/own are None ("unknown", never
    $0) when no day in the window had a valid account row to split on
    (days_split == 0); then the whole amount is `unsplit`.  None without pos_pnl."""
    rec = _dict(recon)
    pp = rec.get("pos_pnl")
    if not pp:
        return None
    hs = pp["held_through"] + pp["sold_leg"]
    ls = _dict(rec.get("leverage_split"))
    if not ls.get("days_split"):
        return {"held_sold": hs, "borrowed": None, "own": None, "unsplit": hs}
    b, o = ls["borrowed"], ls["own"]
    return {"held_sold": hs, "borrowed": b, "own": o, "unsplit": hs - b - o}


def waterfall_rows(recon) -> "list[dict]":
    """Rows {label, value (float|None), indent, total, breakdown, note} for the
    S1 table.  A value that is None means "unknown" (never $0).  `breakdown`
    rows (the held-at-prior-close subtotal) restate lines already above and are
    NOT added again into the net; their indented children sum to them.
    pre_coverage / offline show only the holdings price-move lines (no flows,
    no residual, no total)."""
    rec = _dict(recon)
    pp = rec.get("pos_pnl")
    ls = _dict(rec.get("leverage_split"))
    rows: "list[dict]" = []

    def add(label, value, indent=False, total=False, note="", breakdown=False):
        rows.append({"label": label, "value": value, "indent": indent, "total": total,
                     "breakdown": breakdown, "note": note})

    if pp:
        add("Price moves on positions held through", pp["held_through"])
        add("Last-day moves on positions you sold", pp["sold_leg"])
        sm = split_moves(rec)
        add("Moves on positions held at the prior close (the two lines above)", sm["held_sold"],
            breakdown=True)
        avg = ls.get("avg_leverage")
        add("on borrowed money" + (f" (avg {avg:.1f}x leverage)" if avg is not None and sm["borrowed"] is not None else ""),
            sm["borrowed"], indent=True)
        add("on your own money", sm["own"], indent=True)
        if sm["unsplit"] is not None and abs(sm["unsplit"]) >= _CENT_TOL:
            add("not split (no valid account record that day)", sm["unsplit"], indent=True)
        add("First-day moves on positions you bought", pp["bought_leg"])
        add("Same-day round trips", pp["round_trip"])
    if rec.get("status") in ("ok", "partial"):
        fl, inc, res = rec.get("flows"), rec.get("income"), rec.get("residual")
        add("Deposits / withdrawals", fl["net"] if fl is not None else None,
            note="none recorded" if (fl is not None and fl["n"] == 0) else "")
        add("Dividends", inc["dividends"] if inc is not None else None)
        gap = inc["interest_coverage_gap_months"] if inc is not None else []
        add("Margin interest posted", interest_posted(rec),
            note=("not recorded for " + ", ".join(gap)) if gap else "posts monthly")
        add("Fees", inc["fees"] if inc is not None else None)
        add("Unexplained", res["total"] if res is not None else None)
        add("Net equity change", rec.get("equity_change"), total=True)
    return rows


def leverage_tiles(lev) -> dict:
    """Strings for the three S3 tiles.  `cushion` is returned as a float so the
    caller can mask it.  A call distance >= 0 means ALREADY in a margin call."""
    lev = _dict(lev)
    out = {"range_value": "unknown", "range_delta": None, "call_value": "unknown", "call_when": None,
           "cushion": None, "days_value": "unknown", "days_delta": None}
    if lev.get("status") != "ok":
        return out
    out["range_value"] = f"{lev['min']['leverage']:.2f}x - {lev['max']['leverage']:.2f}x"
    out["range_delta"] = (f"start {lev['start']['leverage']:.2f}x ({lev['start']['date']}) -> "
                          f"end {lev['end']['leverage']:.2f}x ({lev['end']['date']})")
    cc = lev.get("closest_call")
    if cc is not None:
        d = cc["call_distance_pct"]
        if d >= 0:
            out["call_value"] = "In a margin call"
            out["call_when"] = f"on {cc['date']}"
        else:
            out["call_value"] = f"{d:.1f}%"
            out["call_when"] = f"a {abs(d):.1f}% drop away on {cc['date']}"
        out["cushion"] = cc.get("cushion")
    out["days_value"] = f"{lev['days_above_reference']} of {lev['n_days_recorded']}"
    out["days_delta"] = "recorded days"
    return out


def mask_usd(text: str, mask: str = "••••••") -> str:
    """Replace every backslash-escaped dollar amount in a chip/caption label
    (a minus, a backslash, a dollar sign, digits) with `mask` -- privacy mode
    must show no dollar figure."""
    import re
    return re.sub(r"-?\\\$[\d,]+(?:\.\d+)?", mask, str(text))
