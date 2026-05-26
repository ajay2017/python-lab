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

from stock_analyzer.constants import SINGLE_NAME_CEILING, SECTOR_ELEVATED


_PANIC_THRESHOLD_PCT = -1.5   # S&P daily return ≤ this = panic day
_ROLLING_WINDOW      = 5      # trailing N-trade rolling win-rate window
_TREND_SPREAD_PP     = 15.0   # trailing-vs-overall spread that counts as a trend


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
    Share-aware FIFO matching with partial-fill support.

    Walks the trades chronologically. Each SELL eats shares from the oldest
    open BUY of the same ticker first; if the SELL has more shares than that
    BUY's remaining lot, it spills onto the next BUY. If the SELL has fewer,
    the BUY stays partially open with `shares_remaining`.

    Returns:
      {
        "matches": {buy_id: {
            "matched":          [{"sell_id", "sell_price", "sell_date", "shares"}, ...],
            "shares_remaining": float — shares not yet sold (0 if fully closed),
            "buy_shares":       float — original purchase quantity (echoed for convenience),
        }},
        "matched_sell_ids": set of SELL ids that consumed shares from any
                             in-window BUY (used downstream to dedup aggregate
                             metrics so realized P&L isn't double-counted).
      }

    This replaces the batch-1 simplification that pretended every SELL fully
    closed the matching BUY regardless of share count — that over-attributed
    losses on partial sells (e.g. the NFLX 5-buy / 1-sell case).
    """
    by_ticker_open: dict[str, list] = defaultdict(list)
    matches: dict[int, dict] = {}
    matched_sell_ids: set = set()   # SELLs that hit an in-window BUY (any portion)

    sorted_trades = sorted(
        trades_in_window,
        key=lambda t: (t["_trade_date"] or date.min, t["id"] or 0),
    )
    for t in sorted_trades:
        if "BUY" in t["action"].upper():
            buy_shares = _f(t["shares"])
            entry = {"buy": t, "shares_remaining": buy_shares}
            by_ticker_open[t["ticker"]].append(entry)
            matches[t["id"]] = {
                "matched":          [],
                "shares_remaining": buy_shares,
                "buy_shares":       buy_shares,
            }
        elif "SELL" in t["action"].upper():
            sell_remaining = _f(t["shares"])
            while sell_remaining > 0 and by_ticker_open[t["ticker"]]:
                buy_entry = by_ticker_open[t["ticker"]][0]
                matched_shares = min(buy_entry["shares_remaining"], sell_remaining)
                if matched_shares <= 0:
                    by_ticker_open[t["ticker"]].pop(0)
                    continue
                buy_id = buy_entry["buy"]["id"]
                matches[buy_id]["matched"].append({
                    "sell_id":    t["id"],
                    "sell_price": _f(t["price"]),
                    "sell_date":  t["_trade_date"],
                    "shares":     matched_shares,
                })
                matched_sell_ids.add(t["id"])
                buy_entry["shares_remaining"]      -= matched_shares
                matches[buy_id]["shares_remaining"] = buy_entry["shares_remaining"]
                sell_remaining                     -= matched_shares
                if buy_entry["shares_remaining"] <= 1e-9:   # fully closed (epsilon for floats)
                    by_ticker_open[t["ticker"]].pop(0)
    return {"matches": matches, "matched_sell_ids": matched_sell_ids}


def _per_trade_outcome(t: dict, match_info: dict | None, current_price: float | None,
                       today: date, spy_history_df) -> dict:
    """
    Build the outcome record for a single trade row.

    For SELL rows: outcome = realized_pnl from the journal (authoritative).

    For BUY rows: outcome = realized P&L from any partial sells (share-weighted)
    PLUS mark-to-market on the unsold remainder. Three sub-states:
      - fully open       : no shares sold yet → pure MTM
      - partially closed : some shares sold → realized + MTM on remainder
      - fully closed     : all shares sold → pure realized

    Returns:
      outcome_status   : 'open' | 'partial' | 'closed'
      outcome_pnl      : float ($), realized + (MTM on remaining if open/partial)
      outcome_pct      : float (%), w.r.t. cost basis of full BUY position
      hold_days        : int — entry to latest sell (closed) or to today (open/partial)
      exit_price       : float | None — share-weighted avg sell price (closed),
                          most recent sell price (partial), MTM (open)
      exit_date        : date | None — last sell date (closed/partial only)
      vs_spy_pct       : float | None — closed trades only
      is_win           : bool | None — None when outcome can't be priced
      realized_pnl     : float — realized portion only (informational)
      shares_sold      : float — shares sold so far (0 for open)
      shares_remaining : float — shares still held (0 for closed)
    """
    entry_price = _f(t["price"])
    shares      = _f(t["shares"])
    entry_date  = t["_trade_date"]

    # ── SELL row — authoritative from journal ────────────────────────────────
    if "SELL" in t["action"].upper():
        realized = _f(t["realized_pnl"])
        cost_b   = _f(t["cost_basis"]) or (entry_price * shares)
        pct      = (realized / cost_b) * 100.0 if cost_b > 0 else 0.0
        return {
            "outcome_status":   "closed",
            "outcome_pnl":      round(realized, 2),
            "outcome_pct":      round(pct, 2),
            "hold_days":        None,
            "exit_price":       entry_price,
            "exit_date":        entry_date,
            "vs_spy_pct":       None,
            "is_win":           realized > 0,
            "realized_pnl":     round(realized, 2),
            "shares_sold":      shares,
            "shares_remaining": 0.0,
        }

    # ── BUY row — partial-fill aware ─────────────────────────────────────────
    matched          = (match_info or {}).get("matched", []) or []
    shares_remaining = (match_info or {}).get("shares_remaining", shares)
    shares_sold      = shares - shares_remaining

    # Realized P&L: sum of (sell - entry) × shares_matched across all partial sells
    realized = sum(
        (_f(m["sell_price"]) - entry_price) * _f(m["shares"])
        for m in matched
    )

    # Unrealized P&L on the unsold remainder
    unrealized = 0.0
    can_mtm    = current_price is not None and current_price > 0 and shares_remaining > 0
    if can_mtm:
        unrealized = (current_price - entry_price) * shares_remaining

    total_pnl = realized + unrealized

    # Cost basis on the FULL position (shares × entry_price) — % of capital risked
    cost_basis = entry_price * shares
    pct        = (total_pnl / cost_basis) * 100.0 if cost_basis > 0 else 0.0

    # Status
    if shares_remaining <= 1e-9:
        status = "closed"
    elif shares_sold <= 1e-9:
        status = "open"
    else:
        status = "partial"

    # Display fields keyed off status
    if status == "closed" and matched:
        total_proceeds = sum(_f(m["sell_price"]) * _f(m["shares"]) for m in matched)
        total_matched  = sum(_f(m["shares"])                       for m in matched)
        weighted_exit  = total_proceeds / total_matched if total_matched > 0 else entry_price
        last_sell_date = max((m["sell_date"] for m in matched if m.get("sell_date")), default=None)
        hold_days      = (last_sell_date - entry_date).days if (last_sell_date and entry_date) else None
        spy_ret        = _spy_return_between(spy_history_df, entry_date, last_sell_date) \
                         if (entry_date and last_sell_date) else None
        vs_spy         = round(pct - spy_ret, 2) if spy_ret is not None else None
        exit_price     = round(weighted_exit, 2)
        exit_date      = last_sell_date
    elif status == "partial":
        # Most recent sell price as the "exit reference"; vs-SPY not yet meaningful
        # because remainder is still open. Hold-days runs to today.
        last_sell      = matched[-1]
        exit_price     = round(_f(last_sell["sell_price"]), 2)
        exit_date      = last_sell.get("sell_date")
        hold_days      = (today - entry_date).days if entry_date else None
        vs_spy         = None
    else:    # open
        exit_price     = round(current_price, 2) if can_mtm else None
        exit_date      = None
        hold_days      = (today - entry_date).days if entry_date else None
        vs_spy         = None

    # is_win only meaningful when we can actually price the outcome
    if status == "open" and not can_mtm:
        is_win = None
    else:
        is_win = total_pnl > 0

    return {
        "outcome_status":   status,
        "outcome_pnl":      round(total_pnl, 2),
        "outcome_pct":      round(pct, 2),
        "hold_days":        hold_days,
        "exit_price":       exit_price,
        "exit_date":        exit_date,
        "vs_spy_pct":       vs_spy,
        "is_win":           is_win,
        "realized_pnl":     round(realized,   2),
        "shares_sold":      round(shares_sold, 4),
        "shares_remaining": round(shares_remaining, 4),
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

    SELLs whose realized portion is already counted in a matching in-window
    BUY's outcome are filtered out via the `_sell_dedup` flag — otherwise the
    same economic event would be counted twice (once on the SELL row's
    realized_pnl, once embedded in the BUY's position-level outcome).
    """
    bucket = [
        t for t in trades_with_outcome
        if bucket_filter(t) and not t.get("_sell_dedup", False)
    ]
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


def cumulative_pnl_series(trades_with_outcome: list[dict]) -> list[dict]:
    """
    Chronological cumulative P&L for the trend chart. Skips deduped SELLs so
    the realized portion of partial-fill positions isn't double-counted.

    Returns list of {date, ticker, outcome_pnl, cumulative_pnl} in trade order.
    """
    judged = [
        t for t in trades_with_outcome
        if t.get("is_win") is not None and not t.get("_sell_dedup", False)
    ]
    judged.sort(key=lambda t: (t["_trade_date"] or date.min, t.get("id") or 0))

    series: list[dict] = []
    cum = 0.0
    for t in judged:
        cum += t.get("outcome_pnl", 0.0)
        series.append({
            "date":           t["_trade_date"],
            "ticker":         t["ticker"],
            "action":         t["action"],
            "outcome_pnl":    round(t["outcome_pnl"], 2),
            "cumulative_pnl": round(cum, 2),
        })
    return series


def rolling_win_rate(trades_with_outcome: list[dict],
                     window: int = _ROLLING_WINDOW) -> list[dict]:
    """
    Trailing-N rolling win-rate series. Returns empty when fewer than `window`
    judged trades exist (no plotting a single-point line on thin data).

    Returns list of {trade_idx, date, win_rate, window, n_wins}.
    """
    judged = [
        t for t in trades_with_outcome
        if t.get("is_win") is not None and not t.get("_sell_dedup", False)
    ]
    judged.sort(key=lambda t: (t["_trade_date"] or date.min, t.get("id") or 0))

    if len(judged) < window:
        return []

    series: list[dict] = []
    for i in range(window - 1, len(judged)):
        chunk = judged[i - window + 1: i + 1]
        wins  = sum(1 for t in chunk if t["is_win"])
        series.append({
            "trade_idx": i + 1,
            "date":      judged[i]["_trade_date"],
            "win_rate":  round(wins / window * 100.0, 1),
            "window":    window,
            "n_wins":    wins,
        })
    return series


def position_size_discipline(trades_with_outcome: list[dict],
                              portfolio_value: float) -> dict:
    """
    Per-BUY position size as % of current portfolio. Flags trades whose cost
    exceeded the single-name ceiling (15%).

    Only BUYs are scored — entry decisions are what's being judged here. SELLs
    are exits and have already been counted as part of the BUY's lifecycle in
    the partial-fill outcome math.

    Note: uses *current* portfolio_value as the budget reference rather than
    portfolio_value at trade-time (the journal doesn't store historical
    portfolio snapshots). For a freshly opened account this is fine; for a
    portfolio that's grown or shrunk significantly during the window, treat
    the size_pct numbers as approximations.

    Returns:
      trades             — list of {ticker, action, date, size_dollars, size_pct, over_ceiling}
      n_trades           — count of BUYs measured
      n_over_ceiling     — count of trades that breached SINGLE_NAME_CEILING
      avg_size_pct       — mean position size %
      max_size_pct       — largest position size %
      ceiling_threshold  — SINGLE_NAME_CEILING (15.0) for display
    """
    rows: list[dict] = []
    for t in trades_with_outcome:
        if "BUY" not in t["action"].upper():
            continue
        cost = _f(t.get("price")) * _f(t.get("shares"))
        if cost <= 0 or portfolio_value <= 0:
            continue
        size_pct = cost / portfolio_value * 100.0
        rows.append({
            "ticker":       t["ticker"],
            "action":       t["action"],
            "date":         t["_trade_date"],
            "size_dollars": round(cost, 2),
            "size_pct":     round(size_pct, 2),
            "over_ceiling": size_pct > SINGLE_NAME_CEILING,
        })
    rows.sort(key=lambda r: -r["size_pct"])

    return {
        "trades":            rows,
        "n_trades":          len(rows),
        "n_over_ceiling":    sum(1 for r in rows if r["over_ceiling"]),
        "avg_size_pct":      round(sum(r["size_pct"] for r in rows) / len(rows), 2) if rows else None,
        "max_size_pct":      round(max(r["size_pct"] for r in rows),            2) if rows else None,
        "ceiling_threshold": SINGLE_NAME_CEILING,
    }


def sector_mix(trades_with_outcome: list[dict],
               ticker_to_sector: dict[str, str]) -> dict:
    """
    Distribution of BUY trades by sector. Flags sectors taking more than
    SECTOR_ELEVATED (25%) of the trade activity — same threshold used for
    portfolio sector concentration warnings elsewhere in the app.

    Returns:
      sectors            — list of {sector, n_trades, dollars, pct_of_trades, over_elevated}
      n_sectors          — distinct sector count
      top_sector         — name of the largest-share sector
      top_sector_pct     — that sector's share of trades
      elevated_threshold — SECTOR_ELEVATED (25.0) for display
    """
    sector_count:    dict[str, int]   = defaultdict(int)
    sector_dollars:  dict[str, float] = defaultdict(float)
    total_trades = 0

    for t in trades_with_outcome:
        if "BUY" not in t["action"].upper():
            continue
        sector = ticker_to_sector.get(t["ticker"], "") or "Other"
        cost   = _f(t.get("price")) * _f(t.get("shares"))
        sector_count[sector]   += 1
        sector_dollars[sector] += cost
        total_trades           += 1

    if total_trades == 0:
        return {
            "sectors":            [],
            "n_sectors":          0,
            "top_sector":         None,
            "top_sector_pct":     None,
            "elevated_threshold": SECTOR_ELEVATED,
        }

    sectors: list[dict] = []
    for s, n in sector_count.items():
        pct = n / total_trades * 100.0
        sectors.append({
            "sector":         s,
            "n_trades":       n,
            "dollars":        round(sector_dollars[s], 2),
            "pct_of_trades":  round(pct, 1),
            "over_elevated":  pct > SECTOR_ELEVATED,
        })
    sectors.sort(key=lambda x: -x["pct_of_trades"])

    return {
        "sectors":            sectors,
        "n_sectors":          len(sectors),
        "top_sector":         sectors[0]["sector"],
        "top_sector_pct":     sectors[0]["pct_of_trades"],
        "elevated_threshold": SECTOR_ELEVATED,
    }


def build_insights(metrics: dict, trades: list[dict],
                   position_discipline: dict | None = None,
                   sector_mix_data:     dict | None = None,
                   rolling_wr_series:   list | None = None) -> dict:
    """
    Derive a rule-based summary of what the trade data shows.

    Returns:
      verdict        : 'on_track' | 'mixed' | 'correct' (course-correct)
      verdict_label  : display chip text
      verdict_color  : hex for the verdict chip
      verdict_msg    : one-sentence framing
      findings       : list of bullet-point strings (auto-generated, only the
                       ones with enough underlying data — never speculative)
      next_move      : one-sentence action keyed off the strongest finding
      data_thin      : bool — True when window has too few judged trades to
                       draw conclusions; UI should soften the verdict
    """
    af = metrics.get("app_followed", {}) or {}
    dv = metrics.get("deviated", {})     or {}
    pw = metrics.get("panic_window", {}) or {}
    ov = metrics.get("overall", {})      or {}

    findings: list[str] = []
    actions:  list[str] = []   # ordered by strength; we pick the top one

    af_judged = af.get("n_judged", 0)
    dv_judged = dv.get("n_judged", 0)
    pw_n      = pw.get("n_trades", 0)
    ov_judged = ov.get("n_judged", 0)
    af_wr     = af.get("win_rate") or 0
    dv_wr     = dv.get("win_rate") or 0
    pw_wr     = pw.get("win_rate") or 0
    pw_pnl    = pw.get("net_pnl",  0.0)
    af_pnl    = af.get("net_pnl",  0.0)
    dv_pnl    = dv.get("net_pnl",  0.0)
    ov_pnl    = ov.get("net_pnl",  0.0)

    # ── Finding 1: signal-compliance comparison ──────────────────────────────
    if af_judged >= 3 and dv_judged >= 2:
        spread = af_wr - dv_wr
        if spread >= 15:
            findings.append(
                f"App-followed trades win <b>{af_wr:.0f}%</b> vs deviated "
                f"<b>{dv_wr:.0f}%</b> — system is outperforming your external "
                f"calls by <b>{spread:.0f}pp</b>."
            )
            actions.append(
                "Trust app signals on the next entry; revisit the deviation_reason "
                "before acting on external info next time."
            )
        elif spread <= -15:
            findings.append(
                f"Deviated trades win <b>{dv_wr:.0f}%</b> vs app-followed "
                f"<b>{af_wr:.0f}%</b> — your discretionary calls are doing better "
                f"({abs(spread):.0f}pp gap)."
            )
            actions.append(
                "Your judgment is adding alpha — keep documenting deviation_reason "
                "in the journal so the pattern stays reproducible."
            )
        else:
            findings.append(
                f"App-followed and deviated win rates are similar "
                f"(<b>{af_wr:.0f}%</b> vs <b>{dv_wr:.0f}%</b>) — no clear edge "
                "from either approach yet."
            )

    # ── Finding 2: panic-window cost/benefit ─────────────────────────────────
    if pw_n >= 2:
        if pw_pnl <= -50:
            findings.append(
                f"Trades on panic days (S&P ≤ -1.5%) cost "
                f"<b style='color:#fca5a5'>${abs(pw_pnl):,.0f}</b> across "
                f"<b>{pw_n}</b> trades."
            )
            actions.append(
                "Defer entries on red-tape days — wait for S&P to stabilise before "
                "opening new positions."
            )
        elif pw_pnl >= 50 and pw_wr >= 60:
            findings.append(
                f"Panic-day trades won <b>{pw_wr:.0f}%</b> for net "
                f"<b style='color:#86efac'>+${pw_pnl:,.0f}</b> — you bought "
                "weakness well."
            )
            actions.append(
                "Buying panic-day weakness is working — but size disciplined; "
                "high-volatility entries amplify both directions."
            )

    # ── Finding 3: best & worst trade highlight ──────────────────────────────
    # Dedup SELLs whose realized portion is already embedded in a matching BUY
    # so we don't surface "Worst: NFLX SELL -$91" when the BUY position-level
    # outcome (-$391) is the more honest answer.
    judged = [
        t for t in trades
        if t.get("is_win") is not None and not t.get("_sell_dedup", False)
    ]
    if judged:
        best  = max(judged, key=lambda t: t["outcome_pnl"])
        worst = min(judged, key=lambda t: t["outcome_pnl"])
        if best["outcome_pnl"] > 0 or worst["outcome_pnl"] < 0:
            findings.append(
                f"Best: <b>{best['ticker']}</b> {best['action']} "
                f"<b style='color:#86efac'>${best['outcome_pnl']:+,.0f}</b> · "
                f"Worst: <b>{worst['ticker']}</b> {worst['action']} "
                f"<b style='color:#fca5a5'>${worst['outcome_pnl']:+,.0f}</b>."
            )

    # ── Finding 4: position-size discipline (single-name ceiling 15%) ────────
    if position_discipline and position_discipline.get("n_trades", 0) >= 3:
        n_over = position_discipline.get("n_over_ceiling", 0)
        max_pct = position_discipline.get("max_size_pct") or 0
        ceil   = position_discipline.get("ceiling_threshold", SINGLE_NAME_CEILING)
        if n_over >= 1:
            findings.append(
                f"<b>{n_over}</b> trade{'s' if n_over != 1 else ''} exceeded the "
                f"<b>{ceil:.0f}%</b> single-name ceiling — largest position size "
                f"was <b>{max_pct:.1f}%</b> of portfolio."
            )
            actions.append(
                f"Trim position sizes below the {ceil:.0f}% single-name ceiling — "
                "concentration above this amplifies idiosyncratic risk."
            )

    # ── Finding 5: sector concentration of trade activity ────────────────────
    if sector_mix_data and sector_mix_data.get("n_sectors", 0) >= 1:
        top_pct = sector_mix_data.get("top_sector_pct") or 0
        top_sec = sector_mix_data.get("top_sector")
        n_secs  = sector_mix_data.get("n_sectors", 0)
        elev    = sector_mix_data.get("elevated_threshold", SECTOR_ELEVATED)
        # Need ≥4 trades total before sector-concentration finding is meaningful
        total_trades = sum(s.get("n_trades", 0) for s in sector_mix_data.get("sectors", []))
        if total_trades >= 4 and top_pct > elev and top_sec:
            findings.append(
                f"<b>{top_pct:.0f}%</b> of your trades are in <b>{top_sec}</b> "
                f"({n_secs} sector{'s' if n_secs != 1 else ''} total) — above "
                f"the <b>{elev:.0f}%</b> concentration warn level."
            )
            actions.append(
                "Broaden trade entries across sectors — over-concentration in one "
                "sector means a single regime shift hits multiple positions."
            )

    # ── Finding 6: win-rate trend direction (trailing vs. overall) ───────────
    if rolling_wr_series and len(rolling_wr_series) >= 1 and ov_judged >= 6:
        # Compare the most recent trailing-N win rate to the overall
        latest_trailing = rolling_wr_series[-1].get("win_rate", 0)
        overall_wr      = ov.get("win_rate") or 0
        spread          = latest_trailing - overall_wr
        if spread >= _TREND_SPREAD_PP:
            findings.append(
                f"Win rate trending <b style='color:#86efac'>up</b>: trailing-"
                f"{rolling_wr_series[-1]['window']} is <b>{latest_trailing:.0f}%</b> "
                f"vs overall <b>{overall_wr:.0f}%</b> ({spread:+.0f}pp)."
            )
        elif spread <= -_TREND_SPREAD_PP:
            findings.append(
                f"Win rate trending <b style='color:#fca5a5'>down</b>: trailing-"
                f"{rolling_wr_series[-1]['window']} is <b>{latest_trailing:.0f}%</b> "
                f"vs overall <b>{overall_wr:.0f}%</b> ({spread:+.0f}pp)."
            )
            actions.append(
                "Recent decision quality has slipped — pause and re-read your "
                "last few `deviation_reason`/`lesson` notes for the common thread."
            )

    # ── Verdict tier ─────────────────────────────────────────────────────────
    data_thin = ov_judged < 3

    if data_thin:
        verdict       = "thin"
        verdict_label = "⏳ Not enough data yet"
        verdict_color = "#94a3b8"
        verdict_msg   = (
            f"Only {ov_judged} judged trade{'s' if ov_judged != 1 else ''} in this "
            "window. Findings will sharpen as you accumulate more history — try a "
            "longer look-back or keep logging."
        )
    elif ov_pnl > 0 and af_wr >= 60:
        verdict       = "on_track"
        verdict_label = "🟢 On Track"
        verdict_color = "#22c55e"
        verdict_msg   = "Profitable window with strong signal compliance. Keep going."
    elif ov_pnl < 0 or (af_judged >= 3 and af_wr < 40):
        verdict       = "correct"
        verdict_label = "🔴 Course Correction"
        verdict_color = "#ef4444"
        verdict_msg   = (
            "Losing window or weak signal compliance — the findings below show "
            "where the bleed is. One concrete fix in the Next Move."
        )
    else:
        verdict       = "mixed"
        verdict_label = "🟡 Mixed Signals"
        verdict_color = "#f59e0b"
        verdict_msg   = (
            "Not catastrophic, not a clear winning streak either. Small "
            "adjustments below should tighten it up."
        )

    next_move = actions[0] if actions else None
    # Provide a generic action when no rule fired but data isn't thin
    if next_move is None and not data_thin:
        if ov_pnl < 0:
            next_move = (
                "Pause new entries for one session; review the losing trades' "
                "deviation_reason / lesson columns for the common thread."
            )
        else:
            next_move = (
                "Keep the current cadence — sample size is still building. "
                "Stay disciplined on the signal-compliance log."
            )

    return {
        "verdict":       verdict,
        "verdict_label": verdict_label,
        "verdict_color": verdict_color,
        "verdict_msg":   verdict_msg,
        "findings":      findings,
        "next_move":     next_move,
        "data_thin":     data_thin,
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
    pairing          = _pair_sells_to_buys(rows)
    match_map        = pairing["matches"]
    matched_sell_ids = pairing["matched_sell_ids"]

    # ── Build outcome + category + panic-flag per trade ──────────────────────
    spy_daily_returns = _build_spy_returns(spy_history_df)
    current_prices    = current_prices or {}

    trades_with_outcome: list[dict] = []
    for r in rows:
        outcome      = _per_trade_outcome(
            r,
            match_map.get(r["id"]) if "BUY" in r["action"].upper() else None,
            current_prices.get(r["ticker"]),
            today,
            spy_history_df,
        )
        category     = _classify(r["followed_signal"])
        sp_ret_today = spy_daily_returns.get(r["_trade_date"])
        panic_flag   = sp_ret_today is not None and sp_ret_today <= _PANIC_THRESHOLD_PCT
        # Mark SELL rows whose realized P&L is already captured in a matching BUY
        # row's outcome — those are deduped out of bucket aggregates to avoid
        # double-counting the realized portion of the same economic event.
        sell_matched_to_in_window_buy = (
            "SELL" in r["action"].upper() and r["id"] in matched_sell_ids
        )
        trades_with_outcome.append({
            **r,
            **outcome,
            "category":              category,
            "panic_window":          panic_flag,
            "spy_pct_on_date":       sp_ret_today,
            "_sell_dedup":           sell_matched_to_in_window_buy,
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
