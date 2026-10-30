"""Held-position stop that only moves up, built from COMPLETED sessions only.

WHY (finding #26, owner decision options 2+1, 2026-10-30)
---------------------------------------------------------
Today a held position's stop is `risk.atr_stop_loss` (last bar Close minus
ATR_STOP_MULT x ATR), rebuilt on every load from a frame that INCLUDES today's
partial bar, then `portfolio.protective_stop` maxes it with a gain-tier ratchet
floor chosen from the CURRENT gain. Two consequences: the ATR stop chases price
down (it can never be breached intraday; the cron compares a close against a
stop built from that same close), and each ratchet floor sits below its own
threshold (see `constants.STOP_RATCHET_LEVELS`) so it can never be breached
either.

THE RULE
--------
As-of date D (an ET `date`, always passed in; this module never reads the
clock). The level is fixed for the whole session D, because only sessions
dated < D enter it. Let E be the ET date the CURRENT holding episode began
(from the trade journal), and ATR_s the engine's own ATR through bar s
(`indicators.atr`, EWM span 14; same function `risk._atr_value` uses).

  entry anchor A   = first_buy_price / split_ratio - initial_mult x ATR at the
                     last completed bar before E (dropped, with a reason, if
                     the fill is outside the entry session's range widened by
                     fill_tol_pct: an unrecorded split)
  trailing T       = max over completed sessions s, max(E, trail_start) <= s < D
                     of (Close_s - trail_mult x ATR_s), only for sessions with
                     at least warmup_bars bars before them
  sticky ratchet   = avg_cost x (1 + mult) for the tier that the HIGHEST
                     completed close since max(E, trail_start, last_buy) earns,
                     where last_buy is the most recent BUY (any add) of the
                     episode. Because it keys off the high-water close, not
                     today's gain, the floor stays after price falls back, so a
                     breach of it is reachable. The window restarts at an add so a
                     pre-add peak is never paired with the post-add avg_cost
                     (which would put the floor above the price). Trailing T
                     deliberately does NOT reset on an add (owner decision open).

  auto stop        = max of the available components, rounded to cents ONLY at
                     the end. Ties on `binding`: ratchet > trailing > entry.

Warm-up: ATR is seeded at the completed bar exactly warmup_bars real trading
bars before E (the "origin"), every day, so the series is identical on a rolling
frame. If the frame doesn't reach the origin, basis is "partial".

Never a window max for an unknown episode: if the journal is unreadable, empty
for the ticker, or disagrees with the broker share count, the stop falls back
to the prior completed session's close minus max(initial_mult, trail_mult) x ATR (basis
"prior_session"); short or stale history yields basis "partial" (a lower bound).

INVARIANT: with no trade in between, the stop for D+1 >= the stop for D. It can
fall only on a full exit / new episode, an add that lowers avg_cost (the
ratchet floor), or a degraded basis (e.g. history that stops covering E), or a vendor
re-adjustment of past closes (dividend-adjusted closes are re-adjusted on every
ex-date). C3 must therefore feed SPLIT-adjusted, dividend-UNadjusted closes
(yfinance auto_adjust=False, the "Close" column).

NEVER alters entry stops (`risk.atr_stop_loss`, the fresh-entry `r["stop"]`).
UNWIRED as of 2026-10-30: nothing imports this module yet. All policy values
are explicit keyword parameters with NO defaults; no constants.py change here.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from stock_analyzer.data import is_trading_day
from stock_analyzer.indicators import atr as _atr_series
from stock_analyzer.portfolio import manual_stop_wins

_ET = "America/New_York"
# Must match the default length of risk._atr_value (a default-arg literal there;
# a test pins the equality).
_ATR_LENGTH = 14


# ── bars ─────────────────────────────────────────────────────────────────────
def _et_dates(index: pd.Index) -> pd.DatetimeIndex:
    """Index -> tz-naive midnight timestamps of each bar's ET session date.
    tz-aware converts to ET first; tz-naive is taken as exchange-local dates."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert(_ET).tz_localize(None)
    return idx.normalize()


def completed_bars(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Bars whose ET session date < as_of, NaN-Close rows dropped, indexed by
    naive ET session date. A new frame; the input is not mutated. Today's
    partial bar (or a pre-market row dated as_of) can never enter."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    out = df.copy()
    out.index = _et_dates(out.index)
    out = out[out.index < pd.Timestamp(as_of)]
    out = out[out["Close"].notna()]
    out = out.sort_index(kind="mergesort")  # stable: keep="last" = later input row
    out = out[~out.index.duplicated(keep="last")]
    return out


# ── episode ──────────────────────────────────────────────────────────────────
def _episode(status: str, reason: str, start=None, first_buy_price=None,
             split_ratio: float = 1.0, last_buy=None) -> dict:
    return {"status": status, "start": start, "first_buy_price": first_buy_price,
            "split_ratio": split_ratio, "reason": reason, "last_buy": last_buy}


def episode_from_journal(ticker: str, trades_df: pd.DataFrame | None,
                         holdings_shares: float, *, shares_tol: float) -> dict:
    """Replay the journal for one ticker and find the current holding episode.

    Columns used (db.py `_TRADE_COLS`): ticker, action (BUY / SELL / SPLIT),
    shares, price, traded_at, id. A SPLIT row's shares is the adjusted TOTAL
    (it overwrites the running total, as in db.py's rebuild and
    tax_advisor._build_open_lots). `trades_df is None` = journal unreadable,
    distinct from an empty frame = no history. Timestamps convert to ET BEFORE
    taking the date; imported trades are NOT shifted (they anchor at 16:00 ET).
    """
    if trades_df is None:
        return _episode("unreadable", "trade journal could not be read")
    if len(trades_df) == 0 or "ticker" not in trades_df.columns:
        return _episode("no_history", "no trade history for this ticker")
    rows = trades_df[trades_df["ticker"].astype(str).str.upper().str.strip()
                     == str(ticker).upper().strip()].copy()
    if rows.empty:
        return _episode("no_history", "no trade history for this ticker")

    rows["_ts"] = pd.to_datetime(rows["traded_at"], errors="coerce", utc=True,
                                 format="ISO8601")
    rows = rows.dropna(subset=["_ts"])
    sort_cols = ["_ts", "id"] if "id" in rows.columns else ["_ts"]
    rows = rows.sort_values(sort_cols, ascending=True)

    total = 0.0
    start: date | None = None
    last_buy: date | None = None
    seeded = False
    first_price: float | None = None
    ratio = 1.0
    for _, r in rows.iterrows():
        action = str(r.get("action", "")).upper()
        try:
            sh = float(r.get("shares") or 0)
            px = float(r.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if sh <= 0:
            continue
        d = r["_ts"].tz_convert(_ET).date()
        if "SPLIT" in action:
            if total > shares_tol:
                ratio *= sh / total
                total = sh
            else:  # seeded from a SPLIT row: no known fill price
                total, start, last_buy, first_price, ratio = sh, d, d, None, 1.0
                seeded = True
        elif "BUY" in action:
            if total <= shares_tol:
                start, first_price, ratio = d, (px if px > 0 else None), 1.0
                seeded = False
            last_buy = d
            total += sh
        elif "SELL" in action:
            total -= sh
            if total <= shares_tol:
                total, start, last_buy, first_price, ratio = (
                    max(total, 0.0), None, None, None, 1.0)
                seeded = False

    if abs(total - float(holdings_shares)) > shares_tol:
        return _episode(
            "mismatch",
            f"journal nets {total:g} shares but holdings show {float(holdings_shares):g}")
    if start is None:
        return _episode("no_history", "journal shows no open position")
    return _episode("ok", "seeded from a SPLIT row" if seeded else "",
                    start=start, first_buy_price=first_price,
                    split_ratio=ratio, last_buy=last_buy)


# ── history sizing ───────────────────────────────────────────────────────────
_PERIOD_DAYS = (("1y", 366), ("2y", 732), ("5y", 1830))


def history_period_needed(df_first_date, episode_start, trail_start,
                          warmup_bars: int, *, as_of: date) -> str | None:
    """None when the frame already reaches back to the episode start minus the
    warm-up (warmup_bars x 1.05 + 10 business days: BDay ignores NYSE holidays,
    hence the slack); trail_start never shrinks the need. Else the smallest of "1y" / "2y" / "5y" / "max" that covers it.

    `as_of` is a keyword the spec's four positionals lack: a fetch period is
    relative to "now", so the caller must say which day that is.
    """
    if episode_start is None:
        return None
    # Sized to the EPISODE start (not max(E, trail_start)): the entry anchor and
    # the ATR origin both sit before E regardless of trail_start. trail_start is
    # kept in the signature for callers; it can't shrink the need.
    anchor = pd.Timestamp(episode_start)
    # Holiday slack: BDay ignores NYSE holidays, so a business-day count
    # under-shoots the real session count. The caller must still trust the basis
    # that compute_held_stop returns, not this estimate.
    need = anchor - pd.offsets.BDay(int(int(warmup_bars) * 1.05) + 10)
    if df_first_date is not None and pd.Timestamp(df_first_date) <= need:
        return None
    back = (pd.Timestamp(as_of) - need).days
    for label, days in _PERIOD_DAYS:
        if back <= days:
            return label
    return "max"


# ── the stop ─────────────────────────────────────────────────────────────────
def _tier(gain_pct: float, ratchet_levels) -> tuple[float, str] | None:
    for threshold, mult, label in sorted(ratchet_levels, key=lambda t: -t[0]):
        if gain_pct >= threshold:
            return mult, label
    return None


def _result(stop, basis, binding, reason, since=None, last_session=None,
            hw_close=None, hw_session=None, components=None, ratchet_label=None):
    return {
        "stop": stop, "basis": basis, "binding": binding, "reason": reason,
        "since": since, "last_session": last_session,
        "hw_close": hw_close, "hw_session": hw_session,
        "components": components or {"entry": None, "trailing": None, "ratchet": None},
        "ratchet_label": ratchet_label,
    }


def compute_held_stop(df: pd.DataFrame, episode: dict, avg_cost: float,
                      as_of: date, *, initial_mult: float, trail_mult: float,
                      trail_start, ratchet_levels, warmup_bars: int,
                      min_bars: int, fill_tol_pct: float) -> dict:
    """See the module docstring. Returns a dict: stop, basis (full / partial /
    prior_session / unavailable), binding, reason, since, last_session,
    hw_close, hw_session, components {entry, trailing, ratchet}, ratchet_label."""
    if int(warmup_bars) < 1:
        raise ValueError(f"warmup_bars must be >= 1 (got {warmup_bars})")
    bars = completed_bars(df, as_of)
    if len(bars) < int(min_bars):
        return _result(None, "unavailable", None,
                       f"only {len(bars)} completed bars (need {int(min_bars)})")

    close_all = bars["Close"].astype(float)
    last_date = bars.index[-1].date()
    status = (episode or {}).get("status", "unreadable")

    if status != "ok":
        # Never tighter than the trailing rule: use the wider of the two mults.
        atr_all = _atr_series(bars["High"], bars["Low"], bars["Close"], _ATR_LENGTH)
        stop = float(close_all.iloc[-1]) - max(initial_mult, trail_mult) * float(atr_all.iloc[-1])
        return _result(round(stop, 2), "prior_session", "prior_session",
                       f"episode {status}: {(episode or {}).get('reason', '')}".rstrip(": "),
                       last_session=last_date)

    E = pd.Timestamp(episode["start"])
    since = E.date()
    t0 = E if trail_start is None else max(E, pd.Timestamp(trail_start))
    notes: list[str] = []
    partial_notes: list[str] = []

    # ATR origin: the completed bar exactly warmup_bars real bars before E, so
    # ATR is seeded at the same bar every day (rolling-frame stable).
    wb = int(warmup_bars)
    pe = int(bars.index.searchsorted(E))          # first bar dated >= E
    origin = pe - wb
    covered = origin >= 0
    if covered:
        bars = bars.iloc[origin:]
        pe = wb
    close = bars["Close"].astype(float)
    atr = _atr_series(bars["High"], bars["Low"], bars["Close"], _ATR_LENGTH)

    # entry anchor
    entry = None
    fb, ratio = episode.get("first_buy_price"), episode.get("split_ratio") or 1.0
    if fb is None or fb <= 0:
        notes.append("entry anchor dropped: "
                     + (episode.get("reason") or "no recorded fill price"))
    elif pe == 0:
        partial_notes.append("entry anchor dropped: no bar before the entry session")
    elif pe < wb:
        partial_notes.append("entry anchor dropped: not enough history before entry")
    elif pe >= len(bars):
        partial_notes.append("entry anchor dropped: entry session not in history")
    elif bars.index[pe] != E:  # bars exist on both sides of E: not a session
        partial_notes.append(
            f"entry anchor dropped: entry date {E.date()} is not a trading session")
    else:
        fill_adj = fb / ratio
        row = bars.iloc[pe]
        lo = float(row["Low"]) * (1 - fill_tol_pct / 100.0)
        hi = float(row["High"]) * (1 + fill_tol_pct / 100.0)
        if not (lo <= fill_adj <= hi):
            notes.append("entry anchor dropped: fill outside the entry session's "
                         "range (possible unrecorded split)")
        else:
            entry = fill_adj - initial_mult * float(atr.iloc[pe - 1])

    # trailing over completed sessions since max(E, trail_start); when the origin
    # isn't covered, only sessions with warmup_bars bars before them count.
    pos = pd.Series(range(len(bars)), index=bars.index)
    since_mask = bars.index >= t0
    elig = since_mask if covered else (since_mask & (pos >= wb))
    trailing = None
    cand = (close - trail_mult * atr)[elig]
    if len(cand):
        trailing = float(cand.max())

    # ratchet high-water: restarts at the last BUY (an add) of the episode
    lb = episode.get("last_buy")
    r0 = t0 if lb is None else max(t0, pd.Timestamp(lb))
    hw_close = hw_session = None
    rmask = bars.index >= r0
    if rmask.any():
        hw_close = float(close[rmask].max())
        hw_session = close[rmask].idxmax().date()
    ratchet, label = None, None
    if hw_close is not None and avg_cost and avg_cost > 0:
        tier = _tier((hw_close - avg_cost) / avg_cost * 100.0, ratchet_levels)
        if tier is not None:
            ratchet, label = avg_cost * (1 + tier[0]), tier[1]

    comps = {"entry": entry, "trailing": trailing, "ratchet": ratchet}
    avail = {k: v for k, v in comps.items() if v is not None}
    if not avail:
        atr_all = _atr_series(bars["High"], bars["Low"], bars["Close"], _ATR_LENGTH)
        stop = float(close.iloc[-1]) - max(initial_mult, trail_mult) * float(atr_all.iloc[-1])
        return _result(round(stop, 2), "prior_session", "prior_session",
                       "; ".join(notes + partial_notes + ["no component computable"]),
                       since=since, last_session=last_date, hw_close=hw_close,
                       hw_session=hw_session, components=comps)
    best = max(avail.values())
    binding = next(k for k in ("ratchet", "trailing", "entry")
                   if avail.get(k) == best)

    prev_td = (pd.Timestamp(as_of) - pd.Timedelta(days=1)).date()
    while not is_trading_day(prev_td):
        prev_td -= pd.Timedelta(days=1)
    basis, reason = "full", "; ".join(notes)
    if not covered or partial_notes:
        first_usable = bars.index[min(wb, len(bars) - 1)].date()
        first_usable = max(first_usable, t0.date())
        extra = list(partial_notes)
        if not covered:
            extra.append(f"trailing since {first_usable}; older history unavailable")
        basis, reason = "partial", "; ".join(notes + extra)
    elif last_date < prev_td:
        basis, reason = "partial", "; ".join(notes + [f"history ends {last_date}"])

    return _result(round(best, 2), basis, binding, reason, since=since,
                   last_session=last_date, hw_close=hw_close, hw_session=hw_session,
                   components=comps, ratchet_label=label)


# ── manual override ──────────────────────────────────────────────────────────
def compose_with_manual(auto_stop: float | None,
                        manual_price: float | None) -> tuple[float | None, bool]:
    """(stop, stop_type_is_manual). The decision is `portfolio.manual_stop_wins`
    (a manual stop wins only when >= the auto stop), rounded to cents like
    build_portfolio_df. With no auto stop there is nothing to override."""
    if auto_stop is None:  # as build_portfolio_df: manual applies only over an auto stop
        return None, False
    if manual_stop_wins(manual_price, auto_stop):
        return round(float(manual_price), 2), True
    return auto_stop, False
