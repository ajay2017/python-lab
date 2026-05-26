"""
Evening Debrief — PM companion to Today's Brief.

Closes the loop on the trading day:
  1. Plan vs. Reality — for each AM Go-verdict pick, was a trade taken? And for
     each Skip / Filtered-Out pick, what did it do today? (Learning loop.)
  2. Today's Trades — buys, sells, deployed capital, realized P&L.
  3. Tomorrow's Setup — macro events tomorrow + held positions with imminent
     earnings.

Designed to consume the locked AM snapshot (preferred — the user's actual
planning baseline) or fall back to the current live Brief.

Pure logic — no Streamlit, no API calls. Caller is responsible for fetching
intraday prices and passing them in via the intraday_pct map. Keeps this module
testable and decoupled from yfinance.
"""

from datetime import date, timedelta


def _f(v, default=0.0):
    if v is None:
        return default
    try:
        x = float(v)
        return default if x != x else x
    except (TypeError, ValueError):
        return default


def _trades_today(trades_df, today: date) -> list[dict]:
    """
    Filter trades_df to entries with traded_at date == today. Returns list of
    dicts with normalized keys for downstream rendering.
    """
    if trades_df is None or trades_df.empty:
        return []
    out: list[dict] = []
    for _, row in trades_df.iterrows():
        ta = row.get("traded_at")
        if ta is None:
            continue
        try:
            ta_date = ta.date() if hasattr(ta, "date") else date.fromisoformat(str(ta)[:10])
        except Exception:
            continue
        if ta_date != today:
            continue
        out.append({
            "id":               row.get("id"),
            "ticker":           str(row.get("ticker", "")).upper(),
            "action":           str(row.get("action", "")),
            "shares":           _f(row.get("shares")),
            "price":            _f(row.get("price")),
            "cost_basis":       _f(row.get("cost_basis")),
            "realized_pnl":     _f(row.get("realized_pnl")),
            "trigger_type":     str(row.get("trigger_type", "") or ""),
            "signal_seen":      str(row.get("signal_seen", "") or ""),
            "followed_signal":  row.get("followed_signal"),
            "notes":            str(row.get("notes", "") or ""),
            "traded_at":        ta,
        })
    return out


def _plan_vs_reality(brief: dict | None, trades_today_list: list[dict],
                     intraday_pct: dict[str, float]) -> dict:
    """
    Match AM picks against today's trades + intraday performance.

    Returns:
      go_picks    — list of {ticker, am_verdict, action_taken, today_pct, outcome}
      skip_picks  — list of {ticker, momentum, composite, today_pct, would_have_worked}
    """
    if not brief:
        return {"go_picks": [], "skip_picks": []}

    grow      = brief.get("grow_today") or {}
    new_picks = grow.get("new_picks")    or []
    adds      = grow.get("add_positions") or []
    skipped   = grow.get("composite_skipped") or []
    buys      = brief.get("buy_candidates") or []

    today_buy_tickers = {
        t["ticker"] for t in trades_today_list
        if "BUY" in t.get("action", "").upper()
    }

    # Go-verdict picks: union of new_picks (high/moderate conviction) + adds +
    # buy_candidates with confirmed verdict. Dedupe by ticker. `_first_seen_at`
    # is plumbed through from the source dict so the Evening Debrief Go-pick
    # cards can render the same "⏱ First surfaced" chip the Today's Brief cards
    # show. App.py attaches it from the recommendations table at brief-build
    # time before this function is called.
    go_set: dict[str, dict] = {}
    for p in new_picks:
        tk = str(p.get("ticker", ""))
        if not tk:
            continue
        go_set.setdefault(tk, {
            "ticker":          tk,
            "sector":          p.get("sector", ""),
            "momentum":        _f(p.get("score")),
            "composite":       p.get("composite_score"),
            "source":          "new_pick",
            "thesis":          p.get("thesis", ""),
            "_first_seen_at":  p.get("_first_seen_at"),
        })
    for p in adds:
        tk = str(p.get("ticker", ""))
        if not tk:
            continue
        go_set.setdefault(tk, {
            "ticker":          tk,
            "sector":          p.get("sector", ""),
            "momentum":        _f(p.get("score")),
            "composite":       _f(p.get("score")),
            "source":          "add_winner",
            "thesis":          p.get("thesis", ""),
            "_first_seen_at":  p.get("_first_seen_at"),
        })
    for p in buys:
        xref = p.get("xref") or {}
        if xref.get("verdict") != "confirmed":
            continue
        tk = str(p.get("ticker", ""))
        if not tk:
            continue
        go_set.setdefault(tk, {
            "ticker":          tk,
            "sector":          p.get("sector", ""),
            "momentum":        _f(p.get("score")),
            "composite":       None,
            "source":          "buy_candidate",
            "thesis":          "",
            "_first_seen_at":  p.get("_first_seen_at"),
        })

    go_picks: list[dict] = []
    for tk, info in go_set.items():
        action_taken   = tk in today_buy_tickers
        today_pct_val  = intraday_pct.get(tk)
        if action_taken:
            outcome = "✅ Acted — see today's trades for entry/PnL"
        elif today_pct_val is None:
            outcome = "—  No action taken (intraday data unavailable)"
        elif today_pct_val >= 1.0:
            outcome = f"💸 Missed — would have gained {today_pct_val:+.2f}% today"
        elif today_pct_val <= -1.0:
            outcome = f"🛡 Dodged — would have lost {today_pct_val:+.2f}% today"
        else:
            outcome = f"⚖ Flat — {today_pct_val:+.2f}% today; no meaningful move"
        go_picks.append({**info, "action_taken": action_taken,
                         "today_pct": today_pct_val, "outcome": outcome})

    skip_picks: list[dict] = []
    for s in skipped:
        tk = str(s.get("ticker", ""))
        if not tk:
            continue
        today_pct_val = intraday_pct.get(tk)
        if today_pct_val is None:
            outcome = "—  Intraday data unavailable"
            verdict = "unknown"
        elif today_pct_val >= 1.0:
            outcome = f"💭 Would have worked — {today_pct_val:+.2f}% today (composite still says wait)"
            verdict = "missed"
        elif today_pct_val <= -1.0:
            outcome = f"✅ Skip validated — {today_pct_val:+.2f}% today (composite was right)"
            verdict = "validated"
        else:
            outcome = f"⚖ Flat — {today_pct_val:+.2f}% today"
            verdict = "flat"
        skip_picks.append({
            "ticker":           tk,
            "sector":           s.get("sector", ""),
            "momentum":         _f(s.get("momentum_score")),
            "composite":        _f(s.get("composite_score")),
            "composite_label":  s.get("composite_label", "Hold"),
            "today_pct":        today_pct_val,
            "outcome":          outcome,
            "verdict":          verdict,
        })

    # Sort: acted Go picks first, then highest-magnitude movers
    go_picks.sort(key=lambda x: (
        0 if x["action_taken"] else 1,
        -abs(x.get("today_pct") or 0),
    ))
    skip_picks.sort(key=lambda x: -abs(x.get("today_pct") or 0))

    return {"go_picks": go_picks, "skip_picks": skip_picks}


def _today_summary(trades_today_list: list[dict]) -> dict:
    """Aggregate stats for today's trades."""
    n_buy   = sum(1 for t in trades_today_list if "BUY"  in t["action"].upper())
    n_sell  = sum(1 for t in trades_today_list if "SELL" in t["action"].upper())
    deployed = sum(
        t["shares"] * t["price"]
        for t in trades_today_list
        if "BUY" in t["action"].upper()
    )
    # Realized P&L is only meaningful on SELL rows. BUYs SHOULD have
    # realized_pnl = None / 0, but defensively filter — a stray non-zero on
    # a BUY (e.g. legacy row, manual DB edit) would otherwise inflate the
    # daily total without warning.
    realized = sum(
        t["realized_pnl"]
        for t in trades_today_list
        if "SELL" in t["action"].upper()
    )
    followed = [t for t in trades_today_list if t.get("followed_signal") is True]
    deviated = [t for t in trades_today_list if t.get("followed_signal") is False]
    return {
        "n_trades":      len(trades_today_list),
        "n_buys":        n_buy,
        "n_sells":       n_sell,
        "deployed":      round(deployed, 2),
        "realized_pnl":  round(realized, 2),
        "n_followed":    len(followed),
        "n_deviated":    len(deviated),
    }


def _next_trading_day(d: date) -> date:
    """
    Return the next U.S. equity-market trading day after `d`.

    Friday → Monday (skip Saturday/Sunday). Saturday → Monday.
    Sunday → Monday. Other weekdays → next day.

    Doesn't handle market holidays — those are rare enough that "Tomorrow's
    Setup" pointing at e.g. Thanksgiving will just show an empty macro list,
    which is fine (the visible "no events" rendering is honest).
    """
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:    # 5=Sat, 6=Sun
        nxt += timedelta(days=1)
    return nxt


def _tomorrow_setup(macro_events: list | None, held_data: dict | None,
                    today: date) -> dict:
    """
    Compose tomorrow's playbook: macro events on the next trading day +
    held names with earnings inside the next 1–7 days.

    Uses next-trading-day rather than literal calendar tomorrow so the
    Friday-PM debrief surfaces Monday's macro events rather than an
    empty Saturday list.
    """
    tomorrow = _next_trading_day(today)
    macro_tomorrow: list[dict] = []
    for ev in (macro_events or []):
        ev_date = ev.get("date")
        if not ev_date:
            continue
        try:
            ev_d = ev_date if isinstance(ev_date, date) else date.fromisoformat(str(ev_date)[:10])
        except Exception:
            continue
        if ev_d == tomorrow:
            macro_tomorrow.append({
                "event":    str(ev.get("event", "")),
                "impact":   str(ev.get("impact", "")),
                "time":     str(ev.get("time", "")),
                "category": str(ev.get("category", "")),
                "playbook": str(ev.get("playbook_note", "") or ""),
            })

    earnings_imminent: list[dict] = []
    for tk, data in (held_data or {}).items():
        ed = (data or {}).get("earnings")
        if not ed:
            continue
        try:
            ed_d = ed if isinstance(ed, date) else date.fromisoformat(str(ed)[:10])
        except Exception:
            continue
        days = (ed_d - today).days
        if 1 <= days <= 7:
            earnings_imminent.append({
                "ticker": str(tk).upper(),
                "date":   ed_d.isoformat(),
                "days":   days,
            })
    earnings_imminent.sort(key=lambda x: x["days"])

    return {
        "macro_tomorrow":    macro_tomorrow,
        "earnings_imminent": earnings_imminent,
        "tomorrow_date":     tomorrow.isoformat(),
    }


def build_evening_debrief(
    brief:          dict | None,
    trades_df,
    port_df,
    held_data:      dict | None,
    macro_events:   list | None,
    today:          date,
    intraday_pct:   dict[str, float] | None = None,
    am_baseline_source: str = "live",
    am_baseline_at = None,
) -> dict:
    """
    Synthesize the day's loop. Returns:
      am_baseline_source : 'locked' | 'live' | 'none'
      am_baseline_at     : datetime | None — when the AM read was captured
      plan_vs_reality    : {go_picks, skip_picks}
      today_trades       : list of normalized trade dicts
      today_summary      : aggregate stats
      tomorrow_setup     : {macro_tomorrow, earnings_imminent, tomorrow_date}
    """
    trades_today_list = _trades_today(trades_df, today)
    intraday_pct      = intraday_pct or {}
    return {
        "am_baseline_source": am_baseline_source,
        "am_baseline_at":     am_baseline_at,
        "plan_vs_reality":    _plan_vs_reality(brief, trades_today_list, intraday_pct),
        "today_trades":       trades_today_list,
        "today_summary":      _today_summary(trades_today_list),
        "tomorrow_setup":     _tomorrow_setup(macro_events, held_data, today),
    }
