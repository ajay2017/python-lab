"""
Ticker History — reconstruct the user's own past positions in a single ticker
as chronological "round-trip episodes" (0 shares → held → back to 0).

Purpose: read-only awareness surface for the 🧾 Prior Trades tab on the
Analysis page (F-237, `docs/plans/prior-trades-tab.md`). This module
never gates, scores, or feeds a recommendation of any kind.

Cost-basis convention: episode realized P&L sums the STORED `realized_pnl`
on SELL rows — authoritative, weighted-average-cost basis written by
`db.recalculate_from_trades`. Only when a stored value is null/unparseable
is a leg recomputed from (sell_price - running_avg_cost) * shares. This
guarantees the tab can never disagree with the Portfolio page.

Public API
----------
build_ticker_history(trades_df, ticker, current_price, spy_history_df, today)
    → dict | None

build_pnl_series(episodes, price_history_df, ghost_from_last_exit)
    → list[dict]

Pure logic — no Streamlit, no DB calls, no network.  Caller supplies:
  trades_df       Full trades-table DataFrame (or None when offline).
  ticker          The ticker string to reconstruct (case-insensitive).
  current_price   Latest price for unrealized P&L on open episodes (optional).
  spy_history_df  SPY OHLC DataFrame (DatetimeIndex, Close column) for vs-SPY.
  today           Date override for hold-day math; defaults to today_et().
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd

from stock_analyzer.market_time import today_et
from stock_analyzer.trade_review import _spy_return_between


# ── Module-local display tuning ───────────────────────────────────────────────
# These are presentation / awareness constants only — not investment-policy
# thresholds.  Adjust freely without it being a constants.py / Opus-review
# concern; they never move a gate or a recommendation.
#
#  _MAX_EPISODES_DEFAULT  : first N episode cards shown before a "show all"
#                           button.  Chosen so the tab doesn't scroll immediately
#                           when a ticker has many round trips.
#  _SHARES_ZERO_THRESHOLD : floating-point fuzz below which "shares remaining"
#                           counts as 0 (position fully closed).  Mirrors the
#                           same 1e-9 tolerance used in recalculate_from_trades
#                           and portfolio.py.
#
_MAX_EPISODES_DEFAULT  = 5
_SHARES_ZERO_THRESHOLD = 1e-9


# ── Private helpers ────────────────────────────────────────────────────────────

def _f(v: Any, default: float = 0.0) -> float:
    """Safe float coercion; returns default on None / NaN / non-numeric."""
    if v is None:
        return default
    try:
        x = float(v)
        return default if x != x else x   # NaN guard (NaN != NaN)
    except (TypeError, ValueError):
        return default


def _to_date(v: Any) -> date | None:
    """Coerce a Timestamp / string / date to a plain date; None on failure."""
    if v is None:
        return None
    try:
        if hasattr(v, "date"):
            return v.date()          # pandas Timestamp; raises ValueError for NaT
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _spy_covers(spy_history_df: Any, start_d: date | None) -> bool:
    """True when the SPY frame actually reaches back to `start_d`.

    `_spy_return_between` anchors on the first close AT OR AFTER `start_d`, so
    a frame that begins after an episode's entry silently returns a return
    measured over a TRUNCATED window — a *wrong* vs-SPY number rather than an
    absent one. That is the one way this module can report a wrong figure
    instead of `None`, so the coverage is checked per episode: an old trip
    correctly reports `—` while a recent one still gets a real comparison.
    """
    if spy_history_df is None or start_d is None:
        return False
    try:
        idx = spy_history_df.index
        if len(idx) == 0:
            return False
        first = _to_date(idx[0])
        return first is not None and first <= start_d
    except Exception:
        return False


def _parse_context(raw: Any) -> dict | None:
    """Parse decision_context: accepts dict or JSON string; None on failure."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _row_get(row: Any, col: str, default: Any = None) -> Any:
    """
    Safely read a column from a pandas Series row; returns default when the
    column is absent OR when its value is None (legacy rows pre-date several
    journalling columns and have them back-filled as None by db.load_trades).
    """
    try:
        v = row.get(col)
        return v if v is not None else default
    except Exception:
        return default


def _trigger_type_from_row(row: Any) -> str | None:
    """Extract a clean trigger_type string from a row; None when absent/empty."""
    raw = _row_get(row, "trigger_type")
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s else None


def _compute_basis_at(buys: list[tuple], d: date) -> float | None:
    """
    Weighted avg cost of episode buys with date <= d.
    Returns None when no qualifying buys exist (position not yet open by d).

    buys = [(buy_date, shares, price), ...]
    """
    total_shares = 0.0
    total_cost   = 0.0
    for buy_date, shares, price in buys:
        if buy_date is not None and buy_date <= d:
            total_shares += shares
            total_cost   += shares * price
    if total_shares <= _SHARES_ZERO_THRESHOLD:
        return None
    return total_cost / total_shares


def _episode_is_win(ep: dict) -> bool | None:
    """True if realized_pnl > 0, False if < 0, None when 0 or unknown."""
    pnl = ep.get("realized_pnl")
    if pnl is None:
        return None
    if pnl > 0:
        return True
    if pnl < 0:
        return False
    return None  # exactly 0 counts as neither win nor loss


def _collect_trigger_types(entries: list[dict]) -> list[str]:
    """Sorted list of distinct non-empty trigger_type values across all fills."""
    seen: list[str] = []
    for e in entries:
        v = _trigger_type_from_row(e["row"])
        if v is not None and v not in seen:
            seen.append(v)
    return sorted(seen)


def _build_journal(opening_row: Any, closing_row: Any) -> dict:
    """
    Extract journalling fields: opening-BUY sources + closing-SELL sources.
    Every field degrades to None for columns absent on legacy rows.
    """
    def _g(row: Any, col: str) -> Any:
        if row is None:
            return None
        return _row_get(row, col)

    return {
        "user_thesis":                  _g(opening_row, "user_thesis"),
        "thesis_source":                _g(opening_row, "thesis_source"),
        "situational_category":         _g(opening_row, "situational_category"),
        "premortem_case_against":       _g(opening_row, "premortem_case_against"),
        "premortem_commitment":         _g(opening_row, "premortem_commitment"),
        "premortem_trigger_price":      _g(opening_row, "premortem_trigger_price"),
        "premortem_trigger_direction":  _g(opening_row, "premortem_trigger_direction"),
        "notes":                        _g(opening_row, "notes"),
        "lesson":                       _g(closing_row, "lesson"),
        "lesson_category":              _g(closing_row, "lesson_category"),
        "deviation_reason":             _g(closing_row, "deviation_reason"),
    }


def _build_episode(
    ep_raw:        dict,
    today_date:    date,
    current_price: float | None,
    spy_history_df: Any,
) -> dict:
    """
    Convert the accumulated episode state into the final episode dict shape.
    Pure computation — no I/O.

    ep_raw keys:
      buys        list of {"date", "shares", "price", "row"}
      sells       list of {"date", "shares", "price", "stored_pnl",
                           "pnl", "estimated", "row"} — `pnl` is the leg's
                           DOLLAR result, frozen at append time so a later
                           SPLIT rescale of shares/price can't move it
      opening_row pandas Series — the first BUY row
      closing_row pandas Series — the last SELL row (closed episodes only)
      status      "open" | "closed"
    """
    buys   = ep_raw["buys"]
    sells  = ep_raw["sells"]
    status = ep_raw.get("status", "open")

    # ── Dates ──────────────────────────────────────────────────────────────────
    entry_date = buys[0]["date"]  if buys  else None
    exit_date  = sells[-1]["date"] if (sells and status == "closed") else None

    # ── Hold days ──────────────────────────────────────────────────────────────
    if status == "closed" and entry_date is not None and exit_date is not None:
        hold_days: int | None = (exit_date - entry_date).days
    elif status == "open" and entry_date is not None:
        hold_days = (today_date - entry_date).days
    else:
        hold_days = None

    # ── Entry metrics ───────────────────────────────────────────────────────────
    total_buy_shares = sum(b["shares"] for b in buys)
    total_buy_cost   = sum(b["shares"] * b["price"] for b in buys)
    entry_avg: float | None = (
        total_buy_cost / total_buy_shares
        if total_buy_shares > _SHARES_ZERO_THRESHOLD else None
    )

    # ── Sell metrics ────────────────────────────────────────────────────────────
    shares_sold         = sum(s["shares"] for s in sells)
    total_sell_proceeds = sum(s["shares"] * s["price"] for s in sells)
    exit_avg: float | None = (
        total_sell_proceeds / shares_sold
        if shares_sold > _SHARES_ZERO_THRESHOLD else None
    )

    n_buys       = len(buys)
    n_sells      = len(sells)
    shares_total = total_buy_shares   # shares ever bought within this episode
    # Shares STILL held. Distinct from shares_total: a partially-trimmed open
    # position has bought more than it currently holds, and rendering
    # shares_total as "sh held" contradicts the unrealized P&L on the same card
    # (which is computed off this number).
    shares_open  = max(0.0, total_buy_shares - shares_sold)

    # ── Realized P&L ───────────────────────────────────────────────────────────
    # Each sell's dollar P&L was resolved at append time (stored value when the
    # journal has one — the authoritative weighted-avg-cost basis written by
    # recalculate_from_trades — else a fallback from the replayed basis).
    # Resolving it THERE rather than here is deliberate: a stock split rescales
    # a sell's `shares`/`price` onto the post-split basis, which would silently
    # divide a recomputed dollar P&L by the split factor if it were derived
    # here. Dollars are split-invariant; per-share figures are not.
    realized_pnl       = 0.0
    realized_estimated = False
    for s in sells:
        realized_pnl += s["pnl"]
        if s["estimated"]:
            realized_estimated = True

    # ── Realized % ─────────────────────────────────────────────────────────────
    denom_realized = (_f(entry_avg) * shares_sold) if entry_avg is not None else 0.0
    realized_pct: float | None = (
        (realized_pnl / denom_realized * 100) if denom_realized > 0 else None
    )

    # ── Unrealized (open episodes, when current price is available) ─────────────
    unrealized_pnl: float | None = None
    unrealized_pct: float | None = None
    if status == "open" and current_price is not None:
        open_shares = shares_open
        if open_shares > _SHARES_ZERO_THRESHOLD and entry_avg is not None:
            unrealized_pnl = (current_price - entry_avg) * open_shares
            unrealized_pct = (
                (current_price - entry_avg) / entry_avg * 100
                if entry_avg > 0 else None
            )

    # ── vs-SPY (closed only; NEVER 0 as stand-in for "unknown") ────────────────
    vs_spy_pct: float | None = None
    if status == "closed" and entry_date is not None and exit_date is not None:
        spy_ret = (_spy_return_between(spy_history_df, entry_date, exit_date)
                   if _spy_covers(spy_history_df, entry_date) else None)
        if realized_pct is not None and spy_ret is not None:
            vs_spy_pct = realized_pct - spy_ret

    # ── Trigger types + journal fields ──────────────────────────────────────────
    trigger_types   = _collect_trigger_types(buys + sells)
    opening_row     = ep_raw.get("opening_row")
    closing_row     = ep_raw.get("closing_row")
    followed_signal = _row_get(opening_row, "followed_signal") if opening_row is not None else None
    journal         = _build_journal(opening_row, closing_row)
    context         = _parse_context(
        _row_get(opening_row, "decision_context") if opening_row is not None else None
    )

    # ── Fills (chronological) — includes trigger_type per fill for chart markers
    fills: list[dict] = []
    for b in buys:
        fills.append({
            "date":         b["date"],
            "action":       "BUY",
            "shares":       b["shares"],
            "price":        b["price"],
            "trigger_type": _trigger_type_from_row(b["row"]),
        })
    for s in sells:
        fills.append({
            "date":         s["date"],
            "action":       "SELL",
            "shares":       s["shares"],
            "price":        s["price"],
            "trigger_type": _trigger_type_from_row(s["row"]),
        })
    fills.sort(key=lambda x: (x["date"] if x["date"] is not None else date.min,))

    return {
        "entry_date":         entry_date,
        "exit_date":          exit_date,
        "hold_days":          hold_days,
        "entry_avg":          entry_avg,
        "exit_avg":           exit_avg,
        "shares_total":       shares_total,
        "shares_sold":        shares_sold,
        "shares_open":        shares_open,
        "n_buys":             n_buys,
        "n_sells":            n_sells,
        "realized_pnl":       realized_pnl,
        "realized_pct":       realized_pct,
        "realized_estimated": realized_estimated,
        "unrealized_pnl":     unrealized_pnl,
        "unrealized_pct":     unrealized_pct,
        "vs_spy_pct":         vs_spy_pct,
        "status":             status,
        "trigger_types":      trigger_types,
        "followed_signal":    followed_signal,
        "journal":            journal,
        "context":            context,
        "fills":              fills,
    }


def _build_totals(episodes: list[dict]) -> dict:
    """Aggregate across all episodes; financial stats are closed-only."""
    closed   = [ep for ep in episodes if ep["status"] == "closed"]
    open_eps = [ep for ep in episodes if ep["status"] == "open"]

    n_round_trips = len(closed)
    n_open        = len(open_eps)

    net_realized = sum(_f(ep["realized_pnl"]) for ep in closed)

    # net_realized_pct = net realized / total capital deployed (closed episodes)
    total_capital = sum(
        _f(ep["entry_avg"]) * _f(ep["shares_sold"])
        for ep in closed
    )
    net_realized_pct: float | None = (
        (net_realized / total_capital * 100) if total_capital > 0 else None
    )

    wins   = sum(1 for ep in closed if _f(ep["realized_pnl"]) > 0)
    losses = sum(1 for ep in closed if _f(ep["realized_pnl"]) < 0)

    closed_hold_days = [ep["hold_days"] for ep in closed if ep["hold_days"] is not None]
    avg_hold_days: float | None = (
        sum(closed_hold_days) / len(closed_hold_days) if closed_hold_days else None
    )

    all_hold_days      = [ep["hold_days"] for ep in episodes if ep["hold_days"] is not None]
    total_days_in_name = sum(all_hold_days)

    all_entry_dates = [ep["entry_date"] for ep in episodes if ep["entry_date"] is not None]
    first_entry_date: date | None = min(all_entry_dates) if all_entry_dates else None

    # Capital-weighted vs_spy_pct and spy_return_pct over closed episodes
    # that have a non-None vs_spy_pct.
    spy_eps = [ep for ep in closed if ep.get("vs_spy_pct") is not None]
    totals_vs_spy:     float | None = None
    totals_spy_return: float | None = None

    if spy_eps:
        spy_capital = sum(
            _f(ep["entry_avg"]) * _f(ep["shares_sold"])
            for ep in spy_eps
        )
        if spy_capital > 0:
            totals_vs_spy = sum(
                ep["vs_spy_pct"] * _f(ep["entry_avg"]) * _f(ep["shares_sold"])
                for ep in spy_eps
            ) / spy_capital
            # spy_return per episode = realized_pct - vs_spy_pct (both are known)
            totals_spy_return = sum(
                (_f(ep["realized_pct"]) - ep["vs_spy_pct"])
                * _f(ep["entry_avg"]) * _f(ep["shares_sold"])
                for ep in spy_eps
            ) / spy_capital

    return {
        "n_round_trips":      n_round_trips,
        "n_open":             n_open,
        "net_realized":       net_realized,
        "net_realized_pct":   net_realized_pct,
        "wins":               wins,
        "losses":             losses,
        "avg_hold_days":      avg_hold_days,
        "total_days_in_name": total_days_in_name,
        "first_entry_date":   first_entry_date,
        "vs_spy_pct":         totals_vs_spy,
        "spy_return_pct":     totals_spy_return,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def build_ticker_history(
    trades_df,
    ticker: str,
    current_price: float | None = None,
    spy_history_df=None,
    today: date | None = None,
) -> dict | None:
    """
    Reconstruct the user's past positions in *ticker* as round-trip episodes.

    Parameters
    ----------
    trades_df     : pd.DataFrame | None
        The full trades table.  None means "trade history unavailable"
        (offline sentinel) — distinct from an empty DataFrame (no trades).
    ticker        : str
        Case-insensitive; normalised internally.
    current_price : float | None
        Latest price.  Only needed for open-episode unrealized P&L.
    spy_history_df: pd.DataFrame | None
        SPY OHLC with DatetimeIndex and Close column; None = SPY unavailable.
    today         : date | None
        Date for hold-day math; defaults to today_et() when None.

    Returns
    -------
    None
        When trades_df is None (offline).  Never substitute [] or {} here —
        the offline-sentinel contract distinguishes "couldn't load" from
        "checked and never traded."
    dict
        When trades_df is a real DataFrame (possibly empty, or with no rows
        for this ticker).  "episodes" is [] when never traded — explicitly
        NOT None.
    """
    # ── Offline sentinel ───────────────────────────────────────────────────────
    if trades_df is None:
        return None

    if today is None:
        today = today_et()

    ticker = str(ticker).upper().strip()

    # ── Filter to this ticker ──────────────────────────────────────────────────
    mask = trades_df["ticker"].astype(str).str.upper().str.strip() == ticker
    tdf  = trades_df[mask].copy()

    # ── Parse timestamps — format='ISO8601' + utc=True handles mixed-precision
    #    strings: raw-SQL inserts (no microseconds) vs. SDK inserts (microsecond
    #    precision).  Without ISO8601, pandas infers from row 0 and silently
    #    coerces non-matching rows to NaT, causing ordering bugs downstream.
    tdf["_sort_ts"] = pd.to_datetime(
        tdf["traded_at"], errors="coerce", utc=True, format="ISO8601"
    )
    sort_cols = ["_sort_ts", "id"] if "id" in tdf.columns else ["_sort_ts"]
    tdf = tdf.sort_values(sort_cols, ascending=True, na_position="last")

    # ── Chronological episode replay ───────────────────────────────────────────
    raw_episodes: list[dict] = []
    warnings:     list[dict] = []

    current_ep:       dict | None = None   # None = no open episode
    running_shares:   float       = 0.0
    running_avg_cost: float       = 0.0

    for _, row in tdf.iterrows():
        action   = str(row.get("action", "") or "").upper()
        ts_raw   = row.get("_sort_ts")
        row_date = _to_date(ts_raw)

        # Parse shares + price with the same tolerance as recalculate_from_trades
        try:
            shares = float(row.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        try:
            price = float(row.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0

        if shares <= 0 or price <= 0:
            date_str = row_date.isoformat() if row_date else "unknown"
            warnings.append({
                "kind": "bad_row",
                "date": date_str,
                "msg":  (f"Row skipped on {date_str}: "
                         f"invalid shares={shares} or price={price}"),
            })
            continue

        # ── SPLIT ─────────────────────────────────────────────────────────────
        # Overwrite running state rather than accumulate — same as
        # recalculate_from_trades (db.py:1789).  Do NOT open or close an episode.
        # Only apply to running state when an episode IS open; a SPLIT with no
        # open episode is a pre-history adjustment to a position we never tracked
        # as a round trip, so we leave running_shares at 0.
        if "SPLIT" in action:
            if current_ep is not None:
                # Rescale the OPEN episode's legs onto the post-split basis.
                #
                # Without this, entry_avg stays pre-split while shares_sold
                # becomes post-split, so `entry_avg * shares_sold` is a
                # mismatched product and realized_pct is wrong by exactly the
                # split factor (BUY 10 @ $100 → SPLIT(20, $50) → SELL 20 @ $60
                # reported +10% when the truth is +20%). vs_spy_pct and both
                # totals percentages inherited the same error.
                #
                # Rescaling is safe for dollars: shares × price is invariant,
                # and each sell's dollar P&L was already frozen at append time.
                # It also FIXES the chart — the price history behind it comes
                # from yfinance with auto_adjust=True, i.e. already split-
                # adjusted, so an as-recorded pre-split fill would plot at the
                # wrong height.
                if running_shares > _SHARES_ZERO_THRESHOLD:
                    factor = shares / running_shares
                    if factor > 0:
                        for _leg in current_ep["buys"] + current_ep["sells"]:
                            _leg["shares"] *= factor
                            _leg["price"]  /= factor
                running_shares   = shares
                running_avg_cost = price
                date_str = row_date.isoformat() if row_date else "unknown"
                warnings.append({
                    "kind": "split_in_window",
                    "date": date_str,
                    "msg":  (f"Stock split on {date_str}: this round trip's share "
                             "counts and per-share prices are shown split-adjusted "
                             "to today's basis, so they won't match your original "
                             "confirmations. Dollar P&L is unaffected."),
                })
            continue

        # ── BUY ───────────────────────────────────────────────────────────────
        elif "BUY" in action:
            if running_shares <= _SHARES_ZERO_THRESHOLD:
                # Open a fresh episode
                current_ep = {
                    "buys":        [],
                    "sells":       [],
                    "opening_row": row,
                    "status":      "open",
                }
                raw_episodes.append(current_ep)

            # Weighted-avg-cost update (mirrors recalculate_from_trades)
            new_total = running_shares + shares
            if new_total > 0:
                running_avg_cost = (
                    (running_shares * running_avg_cost + shares * price) / new_total
                )
            running_shares = new_total

            current_ep["buys"].append({  # type: ignore[index]
                "date":   row_date,
                "shares": shares,
                "price":  price,
                "row":    row,
            })

        # ── SELL ──────────────────────────────────────────────────────────────
        elif "SELL" in action:
            if current_ep is None:
                date_str = row_date.isoformat() if row_date else "unknown"
                warnings.append({
                    "kind": "orphan_sell",
                    "date": date_str,
                    "msg":  f"SELL on {date_str} has no prior BUY — skipped.",
                })
                continue

            # Stored realized_pnl is authoritative; None (or NaN — pandas
            # coerces None to NaN in a float column) triggers a recompute in
            # _build_episode.  Use the same NaN guard as _f().
            stored_pnl: float | None = None
            raw_pnl = row.get("realized_pnl")
            if raw_pnl is not None:
                try:
                    v = float(raw_pnl)
                    if v == v:     # NaN guard (NaN != NaN)
                        stored_pnl = v
                except (TypeError, ValueError):
                    stored_pnl = None

            # Resolve this leg's DOLLAR P&L now, while `price`/`shares` are
            # still on the basis that `running_avg_cost` was measured against.
            # A later SPLIT rescales shares/price onto the post-split basis;
            # dollars must not move with it.
            current_ep["sells"].append({
                "date":             row_date,
                "shares":           shares,
                "price":            price,
                "stored_pnl":       stored_pnl,
                "pnl":              (stored_pnl if stored_pnl is not None
                                     else (price - running_avg_cost) * shares),
                "estimated":        stored_pnl is None,
                # NOTE: running_avg_cost is deliberately NOT retained here.
                # It is consumed above to freeze `pnl`, and keeping it would be
                # a trap: after a SPLIT rescale it would sit on the pre-split
                # basis while `price`/`shares` are post-split, so any future
                # reader mixing them would silently reintroduce the split bug.
                "row":              row,
            })

            # Selling more than the journal says is held is a journal
            # inconsistency, not a valid trade. recalculate_from_trades raises
            # an explicit warning for the same case (db.py:1836-1841); clamping
            # silently would inflate shares_sold and understate realized_pct
            # the same way the un-rescaled SPLIT did.
            if shares > running_shares + _SHARES_ZERO_THRESHOLD:
                date_str = row_date.isoformat() if row_date else "unknown"
                warnings.append({
                    "kind": "oversell",
                    "date": date_str,
                    "msg":  (f"SELL on {date_str} is for more shares "
                             f"({shares:,.0f}) than your journal shows held "
                             f"({running_shares:,.0f}) — this round trip's "
                             "percentages may be understated."),
                })
            running_shares = max(0.0, running_shares - shares)

            if running_shares <= _SHARES_ZERO_THRESHOLD:
                current_ep["status"]      = "closed"
                current_ep["closing_row"] = row
                current_ep       = None
                running_shares   = 0.0
                running_avg_cost = 0.0

    # Any episode still open at EOF keeps status="open" (already set at creation)

    # ── Build final episode dicts; reverse so newest is first ─────────────────
    final_episodes: list[dict] = [
        _build_episode(ep_raw, today, current_price, spy_history_df)
        for ep_raw in reversed(raw_episodes)
    ]

    totals = _build_totals(final_episodes)

    spy_available = (
        spy_history_df is not None
        and hasattr(spy_history_df, "__len__")
        and len(spy_history_df) > 0
    )

    return {
        "ticker":        ticker,
        "episodes":      final_episodes,
        "totals":        totals,
        "warnings":      warnings,
        "spy_available": spy_available,
    }


def build_pnl_series(
    episodes:          list[dict],
    price_history_df,
    ghost_from_last_exit: bool = True,
) -> list[dict]:
    """
    Build per-episode P&L % series for Panel 2 of the journey chart.

    pct(d) = (close(d) / basis(d) - 1) * 100
    where basis(d) is the weighted avg cost of the episode's buys dated <= d,
    so an add mid-episode shifts the basis visibly from that date forward.

    Ghost series: only for the most recent CLOSED episode (index 0 in the
    newest-first list), when ghost_from_last_exit=True — continues from the
    exit date to the last bar, using the episode's final basis.

    Parameters
    ----------
    episodes         : list[dict] from build_ticker_history (newest first).
    price_history_df : pd.DataFrame with DatetimeIndex and Close column.
    ghost_from_last_exit : bool (default True).

    Returns
    -------
    list[dict]  — one entry per episode, same order as `episodes`.
    []          — when price_history_df is None / empty / no Close column.

    Each entry:
      {"episode_idx": int, "status": str, "is_win": bool | None,
       "dates": [date,...], "pct": [float,...],
       "ghost_dates": [date,...], "ghost_pct": [float,...]}
    """
    if price_history_df is None:
        return []
    if not hasattr(price_history_df, "columns"):
        return []
    if "Close" not in price_history_df.columns:
        return []
    if len(price_history_df) == 0:
        return []

    # Most recent closed episode = first closed one in the newest-first list.
    #
    # Suppressed entirely when ANY episode is still open: the ghost answers
    # "what would this have done had I not sold?", and if you re-entered after
    # that exit you DID hold through the window — drawing the hypothetical
    # there would overlay a second, contradictory "today" percentage on top of
    # the real open-position arc in the same panel.
    most_recent_closed_idx: int | None = None
    if not any(ep["status"] == "open" for ep in episodes):
        for i, ep in enumerate(episodes):
            if ep["status"] == "closed":
                most_recent_closed_idx = i
                break

    # Last available bar date for open episodes
    last_bar_date: date | None = None
    for ts in reversed(price_history_df.index.tolist()):
        d = _to_date(ts)
        if d is not None:
            last_bar_date = d
            break

    result: list[dict] = []

    for i, ep in enumerate(episodes):
        entry_date = ep.get("entry_date")
        exit_date  = ep.get("exit_date")
        status     = ep["status"]
        is_win     = _episode_is_win(ep)

        # Buy fills (date, shares, price) for rolling basis
        buys_for_basis: list[tuple] = [
            (f["date"], f["shares"], f["price"])
            for f in ep["fills"]
            if f["action"] == "BUY"
        ]

        if entry_date is None or not buys_for_basis:
            result.append({
                "episode_idx": i,
                "status":      status,
                "is_win":      is_win,
                "dates":       [],
                "pct":         [],
                "ghost_dates": [],
                "ghost_pct":   [],
            })
            continue

        # Series end date
        if status == "closed" and exit_date is not None:
            series_end = exit_date
        elif last_bar_date is not None:
            series_end = last_bar_date
        else:
            result.append({
                "episode_idx": i,
                "status":      status,
                "is_win":      is_win,
                "dates":       [],
                "pct":         [],
                "ghost_dates": [],
                "ghost_pct":   [],
            })
            continue

        # Main series
        dates_out: list[date]  = []
        pct_out:   list[float] = []

        for ts, close_val in price_history_df["Close"].items():
            d = _to_date(ts)
            if d is None or d < entry_date or d > series_end:
                continue
            try:
                close = float(close_val)
            except (TypeError, ValueError):
                continue
            basis = _compute_basis_at(buys_for_basis, d)
            if basis is None or basis <= 0:
                continue
            dates_out.append(d)
            pct_out.append((close / basis - 1) * 100)

        # Ghost series — only for the most recent closed episode
        ghost_dates: list[date]  = []
        ghost_pct:   list[float] = []

        if (ghost_from_last_exit
                and status == "closed"
                and i == most_recent_closed_idx
                and exit_date is not None):
            final_basis = _compute_basis_at(buys_for_basis, exit_date)
            if final_basis is not None and final_basis > 0:
                for ts, close_val in price_history_df["Close"].items():
                    d = _to_date(ts)
                    if d is None or d <= exit_date:
                        continue
                    try:
                        close = float(close_val)
                    except (TypeError, ValueError):
                        continue
                    ghost_dates.append(d)
                    ghost_pct.append((close / final_basis - 1) * 100)

        result.append({
            "episode_idx": i,
            "status":      status,
            "is_win":      is_win,
            "dates":       dates_out,
            "pct":         pct_out,
            "ghost_dates": ghost_dates,
            "ghost_pct":   ghost_pct,
        })

    return result


def trades_fingerprint(trades_df, ticker: str) -> tuple:
    """Cheap, order-stable fingerprint of one ticker's rows in the trades
    journal. Changes iff a trade affecting `ticker` was added, edited, or
    removed — lets a caller memoize `build_ticker_history`/`build_pnl_series`
    output across Streamlit reruns that didn't touch this ticker's history
    (2026-09-02 follow-up, F-237: the Prior Trades tab previously redid this
    pandas/plotly work on every rerun, including reruns triggered by an
    unrelated widget elsewhere on the page, since Streamlit still builds
    inactive tab content).

    Deliberately omits free-text fields (notes/lesson/user_thesis/etc.) —
    they render elsewhere on the page but don't feed the PnL/chart math, so
    including them would invalidate the cache on edits that can't actually
    change the result.
    """
    if trades_df is None or trades_df.empty or "ticker" not in trades_df.columns:
        return ()
    sub = trades_df[trades_df["ticker"].astype(str).str.upper() == str(ticker).upper()]
    if sub.empty:
        return ()
    cols = [c for c in ("id", "action", "shares", "price", "realized_pnl", "traded_at")
            if c in sub.columns]
    return tuple(tuple(row.get(c) for c in cols) for row in sub[cols].to_dict("records"))


def chart_start_gap(first_entry_date, px_start_date) -> bool:
    """True when the plotted price series starts AFTER the trade history's
    first entry — meaning round trips before `px_start_date` have no price
    data to plot against and are silently absent from the chart, not merely
    shown under a wider unqualified window (the normal, harmless case).

    Both arguments are plain `date` objects (or `None`, which always
    returns `False` — an unknown boundary is not evidence of a gap).
    """
    if first_entry_date is None or px_start_date is None:
        return False
    return px_start_date > first_entry_date
