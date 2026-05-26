"""
Trade Review — behavioural retrospective on executed trades.

Mines the Trade Journal to answer the questions that matter for portfolio
self-improvement:
  - Are app-followed trades outperforming deviated (external/CNBC-driven) trades?
  - Did reactive entries/exits on panic days (S&P ≤ -1.5%) help or hurt?
  - Per-trade outcome: realized P&L for closed positions, mark-to-market for
    open ones, plus a vs-SPY benchmark for closed trades.

Classification (each trade gets exactly one primary category):
  - app_followed     : journal recorded followed_signal=True
  - deviated         : journal recorded followed_signal=False (e.g. CNBC-driven)
  - discretionary    : neither flag recorded (older or freeform entries)

Overlay flag (independent of category):
  - panic_window     : trade date had S&P 500 daily return ≤ -1.5%

Pure logic — no Streamlit, no API calls. Caller supplies:
  - trades_df         (Trade Journal DataFrame)
  - current_prices    {ticker: latest_price}
  - spy_history_df    pandas DataFrame indexed by date with a Close column
  - today             date — for marking open positions
  - lookback_days     int — window for the review
"""

from datetime import date, timedelta
from collections import defaultdict


_PANIC_THRESHOLD_PCT = -1.5   # S&P daily return ≤ this = panic day


def _f(v, default=0.0):
    if v is None:
        return default
    try:
        x = float(v)
        return default if x != x else x
    except (TypeError, ValueError):
        return default


def _to_date(v) -> date | None:
    if v is None:
        return None
    try:
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _classify(followed_signal) -> str:
    """Map the journal's followed_signal column to a primary category."""
    if followed_signal is True:
        return "app_followed"
    if followed_signal is False:
        return "deviated"
    return "discretionary"


def _build_spy_returns(spy_history_df) -> dict[date, float]:
    """
    Convert SPY history DataFrame into {date: daily_pct_change} so per-trade
    panic flagging is one dict lookup per trade.
    """
    out: dict[date, float] = {}
    if spy_history_df is None or len(spy_history_df) == 0:
        return out
    try:
        df = spy_history_df.copy()
        # The history DataFrame is normally indexed by Timestamp; coerce to date keys
        closes = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        prev = None
        for ts, val in closes.items():
            d = ts.date() if hasattr(ts, "date") else _to_date(ts)
            if d is None:
                continue
            v = float(val)
            if prev is not None and prev > 0:
                out[d] = (v - prev) / prev * 100.0
            prev = v
    except Exception:
        return {}
    return out


def _spy_return_between(spy_history_df, start_d: date, end_d: date) -> float | None:
    """
    Compute SPY's % return between two dates (closes). Returns None when either
    end of the window is missing from the history (e.g. trade pre-dates the
    fetched window or end date is in the future).
    """
    if spy_history_df is None or len(spy_history_df) == 0 or start_d is None or end_d is None:
        return None
    try:
        closes = spy_history_df["Close"] if "Close" in spy_history_df.columns else spy_history_df.iloc[:, 0]

        # Find first close at or after start_d, and last close at or before end_d
        c_start = None
        c_end   = None
        for ts, val in closes.items():
            d = ts.date() if hasattr(ts, "date") else _to_date(ts)
            if d is None:
                continue
            if c_start is None and d >= start_d:
                c_start = float(val)
            if d <= end_d:
                c_end = float(val)
        if c_start is None or c_end is None or c_start <= 0:
            return None
        return (c_end - c_start) / c_start * 100.0
    except Exception:
        return None


def _pair_sells_to_buys(trades_in_window: list[dict]) -> dict:
    """
    Build a {buy_id: closing_sell_dict | None} map by FIFO-matching SELL rows
    to prior BUY rows for the same ticker.

    Greedy: walk chronologically; each SELL closes the oldest open BUY of the
    same ticker first. Partial closes are not modeled — a sell fully closes
    the next-in-line buy regardless of share count. This is a pragmatic
    simplification for the review surface (the actual realized_pnl column in
    the journal is authoritative for $ figures).
    """
    by_ticker_open: dict[str, list] = defaultdict(list)
    closing: dict[int, dict] = {}

    sorted_trades = sorted(trades_in_window, key=lambda t: (t["_trade_date"] or date.min, t["id"] or 0))
    for t in sorted_trades:
        if "BUY" in t["action"].upper():
            by_ticker_open[t["ticker"]].append(t)
        elif "SELL" in t["action"].upper() and by_ticker_open[t["ticker"]]:
            buy = by_ticker_open[t["ticker"]].pop(0)   # FIFO
            closing[buy["id"]] = t
    return closing


def _per_trade_outcome(t: dict, closing_sell: dict | None, current_price: float | None,
                       today: date, spy_history_df) -> dict:
    """
    Build the outcome record for a single trade row.
    Returns:
      outcome_status : 'open' | 'closed'
      outcome_pnl    : float ($), positive = gain
      outcome_pct    : float (%), w.r.t. entry cost basis
      hold_days      : int — days held (open: to today; closed: to sell date)
      exit_price     : float | None
      exit_date      : date | None
      vs_spy_pct     : float | None — trade % return − SPY % return over same period (closed only)
      is_win         : bool | None — None when outcome can't be determined
    """
    entry_price = _f(t["price"])
    shares      = _f(t["shares"])
    entry_date  = t["_trade_date"]

    if "SELL" in t["action"].upper():
        # SELL rows are inherently closed — realized_pnl is the truth
        outcome_pnl = _f(t["realized_pnl"])
        outcome_pct = (outcome_pnl / (_f(t["cost_basis"]) or (entry_price * shares))) * 100.0 \
                      if entry_price > 0 else 0.0
        return {
            "outcome_status": "closed",
            "outcome_pnl":    round(outcome_pnl, 2),
            "outcome_pct":    round(outcome_pct, 2),
            "hold_days":      None,           # not meaningful on a sell row in isolation
            "exit_price":     entry_price,
            "exit_date":      entry_date,
            "vs_spy_pct":     None,
            "is_win":         outcome_pnl > 0,
        }

    # BUY row: either still open or matched to a later SELL via FIFO
    if closing_sell:
        sell_price = _f(closing_sell["price"])
        sell_date  = closing_sell["_trade_date"]
        pnl        = (sell_price - entry_price) * shares
        pct        = (sell_price - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0
        hold_days  = (sell_date - entry_date).days if (sell_date and entry_date) else None
        spy_ret    = _spy_return_between(spy_history_df, entry_date, sell_date) if (entry_date and sell_date) else None
        vs_spy     = round(pct - spy_ret, 2) if spy_ret is not None else None
        return {
            "outcome_status": "closed",
            "outcome_pnl":    round(pnl, 2),
            "outcome_pct":    round(pct, 2),
            "hold_days":      hold_days,
            "exit_price":     round(sell_price, 2),
            "exit_date":      sell_date,
            "vs_spy_pct":     vs_spy,
            "is_win":         pnl > 0,
        }

    # Open position — mark to market
    if current_price is None or current_price <= 0 or entry_price <= 0:
        return {
            "outcome_status": "open",
            "outcome_pnl":    0.0,
            "outcome_pct":    0.0,
            "hold_days":      (today - entry_date).days if entry_date else None,
            "exit_price":     None,
            "exit_date":      None,
            "vs_spy_pct":     None,
            "is_win":         None,           # can't judge until closed or priced
        }
    pnl = (current_price - entry_price) * shares
    pct = (current_price - entry_price) / entry_price * 100.0
    return {
        "outcome_status": "open",
        "outcome_pnl":    round(pnl, 2),
        "outcome_pct":    round(pct, 2),
        "hold_days":      (today - entry_date).days if entry_date else None,
        "exit_price":     round(current_price, 2),    # current MTM
        "exit_date":      None,
        "vs_spy_pct":     None,                       # not meaningful until closed
        "is_win":         pnl > 0,
    }


def _bucket_metrics(trades_with_outcome: list[dict], bucket_filter) -> dict:
    """
    Compute headline stats for a bucket (e.g., all app_followed trades).
      n_trades  — total in bucket
      n_judged  — trades where is_win is not None
      n_wins    — judged trades where is_win = True
      win_rate  — n_wins / n_judged (0–100), None if no judged trades
      avg_gain  — mean PnL of wins
      avg_loss  — mean PnL of losses (negative)
      net_pnl   — sum of all judged PnL
    """
    bucket = [t for t in trades_with_outcome if bucket_filter(t)]
    judged = [t for t in bucket if t.get("is_win") is not None]
    wins   = [t for t in judged if t["is_win"]]
    losses = [t for t in judged if not t["is_win"]]
    return {
        "n_trades":  len(bucket),
        "n_judged":  len(judged),
        "n_wins":    len(wins),
        "n_losses":  len(losses),
        "win_rate":  round(len(wins) / len(judged) * 100.0, 1) if judged else None,
        "avg_gain":  round(sum(t["outcome_pnl"] for t in wins)   / len(wins),   2) if wins   else 0.0,
        "avg_loss":  round(sum(t["outcome_pnl"] for t in losses) / len(losses), 2) if losses else 0.0,
        "net_pnl":   round(sum(t["outcome_pnl"] for t in judged), 2),
    }


def build_trade_review(
    trades_df,
    current_prices: dict[str, float] | None,
    spy_history_df,
    today: date,
    lookback_days: int = 14,
) -> dict:
    """
    Build the Trade Review payload.

    Returns:
      trades         — chronological list of per-trade scorecard dicts
      metrics        — {app_followed, deviated, discretionary, panic_window, overall}
      window_start   — date — beginning of the lookback window
      window_end     — date — today
      lookback_days  — int (echoed)
      spy_available  — bool — whether SPY history was usable (drives UI hint)
    """
    if lookback_days <= 0:
        window_start = date.min
    else:
        window_start = today - timedelta(days=lookback_days)

    # ── Normalize journal rows in window ─────────────────────────────────────
    rows: list[dict] = []
    if trades_df is not None and len(trades_df) > 0:
        for _, r in trades_df.iterrows():
            td = _to_date(r.get("traded_at"))
            if td is None or td < window_start:
                continue
            rows.append({
                "id":               r.get("id"),
                "ticker":           str(r.get("ticker", "")).upper(),
                "action":           str(r.get("action", "")),
                "shares":           _f(r.get("shares")),
                "price":            _f(r.get("price")),
                "cost_basis":       _f(r.get("cost_basis")),
                "realized_pnl":     _f(r.get("realized_pnl")),
                "trigger_type":     str(r.get("trigger_type", "") or ""),
                "signal_seen":      str(r.get("signal_seen", "") or ""),
                "followed_signal":  r.get("followed_signal"),
                "deviation_reason": str(r.get("deviation_reason", "") or ""),
                "lesson":           str(r.get("lesson", "") or ""),
                "notes":            str(r.get("notes", "") or ""),
                "_trade_date":      td,
                "traded_at":        r.get("traded_at"),
            })

    # ── FIFO-pair sells against buys (for closed-buy outcomes) ───────────────
    closing_map = _pair_sells_to_buys(rows)

    # ── Build outcome + category + panic-flag per trade ──────────────────────
    spy_daily_returns = _build_spy_returns(spy_history_df)
    current_prices    = current_prices or {}

    trades_with_outcome: list[dict] = []
    for r in rows:
        outcome      = _per_trade_outcome(
            r,
            closing_map.get(r["id"]) if "BUY" in r["action"].upper() else None,
            current_prices.get(r["ticker"]),
            today,
            spy_history_df,
        )
        category     = _classify(r["followed_signal"])
        sp_ret_today = spy_daily_returns.get(r["_trade_date"])
        panic_flag   = sp_ret_today is not None and sp_ret_today <= _PANIC_THRESHOLD_PCT
        trades_with_outcome.append({
            **r,
            **outcome,
            "category":           category,
            "panic_window":       panic_flag,
            "spy_pct_on_date":    sp_ret_today,
        })

    # ── Sort chronologically (newest first for display) ──────────────────────
    trades_with_outcome.sort(
        key=lambda t: (t["_trade_date"] or date.min, t["id"] or 0),
        reverse=True,
    )

    # ── Headline metrics by bucket ───────────────────────────────────────────
    metrics = {
        "app_followed":  _bucket_metrics(trades_with_outcome, lambda t: t["category"] == "app_followed"),
        "deviated":      _bucket_metrics(trades_with_outcome, lambda t: t["category"] == "deviated"),
        "discretionary": _bucket_metrics(trades_with_outcome, lambda t: t["category"] == "discretionary"),
        "panic_window":  _bucket_metrics(trades_with_outcome, lambda t: t["panic_window"]),
        "overall":       _bucket_metrics(trades_with_outcome, lambda _t: True),
    }

    return {
        "trades":        trades_with_outcome,
        "metrics":       metrics,
        "window_start":  window_start.isoformat() if window_start != date.min else "all-time",
        "window_end":    today.isoformat(),
        "lookback_days": lookback_days,
        "spy_available": bool(spy_daily_returns),
    }
