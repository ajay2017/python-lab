"""
Positions-scope day-over-day P&L (Tier B).

Computes a TRUE single-day P&L for the tracked stock positions using the
broker-style equity-delta identity, anchored to a persisted prior-close
snapshot baseline (db.daily_snapshots) plus the day's trades:

    day_pnl = Σ(current_price × current_shares)              # today's marked held value
            − Σ(baseline_close × baseline_shares)            # prior-close snapshot value
            + (Σ today's sell proceeds − Σ today's buy cost) # cash from today's trades

Because the baseline is the PRIOR CLOSE (not cost basis), every term measures
only the DAY's move — so this is what the broker's account "Today" reflects for
the equity sleeve:
  • a name SOLD today contributes (sell − prior_close) × qty   (realized day-move only —
    NOT the full holding-period gain that trades.realized_pnl would wrongly add)
  • a name BOUGHT today contributes (current − fill) × qty     (same-day fill basis)
  • a name HELD through contributes (current − prior_close) × qty

Exact for tracked positions, IGNORING fees / dividends / corporate actions and
EXCLUDING cash + external deposits/withdrawals (positions scope). The caller
labels it accordingly and never claims penny-parity with the broker. Pure /
UI-free / no I/O.
"""

from __future__ import annotations


def _num(v, default: float = 0.0) -> float:
    """Float coercion that maps None / NaN / junk to `default` (never raises)."""
    try:
        f = float(v)
        return default if f != f else f  # NaN check
    except (TypeError, ValueError):
        return default


def today_trade_cash_delta(today_trades: list[dict]) -> float:
    """
    Net cash from today's trades = Σ sell proceeds − Σ buy cost.

    Only BUY / SELL move cash; SPLIT (and any other synthetic action) is ignored
    — a split changes share count, not cash, and must never enter the delta
    (stays SPLIT-aware per the action-field contract).
    """
    delta = 0.0
    for t in today_trades:
        action = str(t.get("action", "")).strip().upper()
        cash = _num(t.get("price")) * _num(t.get("shares"))
        if action == "SELL":
            delta += cash
        elif action == "BUY":
            delta -= cash
    return delta


def compute_positions_day_pnl(
    held: list[dict],
    baseline: dict,
    today_trades: list[dict],
    total_value: float,
) -> dict | None:
    """
    held         : [{"ticker","shares","price"}]  CURRENTLY-held positions + current price
    baseline     : {ticker: {"shares","close"}}   prior-close snapshot (yesterday's EOD)
    today_trades : [{"action","shares","price"}]  today's trades (BUY/SELL used)
    total_value  : current positions value — the % denominator

    Returns the day-P&L dict, or None when no baseline exists (caller falls back
    to the held-only mark). The caller is responsible for only invoking this when
    every currently-held position is priced — a missing live price would make the
    equity delta unreliable, so the caller withholds Tier-B and shows the
    fail-loud held mark instead.
    """
    if not baseline:
        return None

    current_val  = sum(_num(h.get("price")) * _num(h.get("shares")) for h in held)
    baseline_val = sum(_num(b.get("close")) * _num(b.get("shares")) for b in baseline.values())
    cash_delta   = today_trade_cash_delta(today_trades)

    day_pnl     = current_val - baseline_val + cash_delta
    day_pnl_pct = (day_pnl / total_value * 100.0) if total_value else 0.0

    # A baseline ticker that is neither still held nor sold in today's trades
    # "vanished" without a recorded exit — a journal gap that would silently
    # distort the delta (it subtracts the baseline value with nothing offsetting).
    # Surface it so the caller can flag rather than present a quietly-wrong number.
    _held_tickers  = {str(h.get("ticker", "")).upper() for h in held}
    _traded_tickers = {str(t.get("ticker", "")).upper() for t in today_trades}
    orphans = sorted(
        tk for tk in baseline
        if tk.upper() not in _held_tickers and tk.upper() not in _traded_tickers
    )

    return {
        "day_pnl":          round(day_pnl, 2),
        "day_pnl_pct":      round(day_pnl_pct, 2),
        "trade_cash_delta": round(cash_delta, 2),
        "current_value":    round(current_val, 2),
        "baseline_value":   round(baseline_val, 2),
        "n_baseline":       len(baseline),
        "orphans":          orphans,   # baseline names with no current holding and no recorded exit today
    }
