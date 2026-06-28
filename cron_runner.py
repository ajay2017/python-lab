#!/usr/bin/env python3
"""
Headless alert cron entry point (exit-discipline Phase 3 + pullback Phase 2 +
Today's-P&L EOD snapshot). Run by GitHub Actions (.github/workflows/alerts.yml).

THREE modes (one per ET trading day each):
  • premarket (~08:00 ET) — recompute PROTECTIVE signals (stop / EXIT / risk-off)
    and email only when the action set changed. (exit-discipline Phase 3)
  • scan (~10:00 ET, post-open) — run the sector scanner headlessly and persist
    the result to scanner_cache so the Home buy-candidate / Grow-Today new-pick
    lists populate on a COLD load without the user running the ~20s scanner.
  • eod (~16:30 ET, post-close) — (a) write today's daily_snapshot so the
    Today's-P&L baseline is deterministic even on unviewed days; (b) a REACTIVE
    pullback email if the market actually fell ≥ threshold today. (pullback P2 +
    Today's-P&L EOD)

Mode = $ALERT_RUN_MODE if set (workflow_dispatch input, OR the scan schedule slot
maps its cron expression to mode=scan), else derived from the ET hour (≥12:00 ET
⇒ eod, else premarket). All output → stdout (the Actions log). Ships INERT: no
RESEND_API_KEY ⇒ compute + log, send nothing. Always exits 0.

Env: SUPABASE_URL/SUPABASE_KEY (service-role) · FINNHUB_API_KEY/FMP_API_KEY/
FRED_API_KEY (optional providers) · RESEND_API_KEY/ALERT_EMAIL_TO/ALERT_EMAIL_FROM
· ALERT_RUN_MODE (premarket|eod) · ALERT_FORCE=1 (bypass guards) · ALERT_TEST_EMAIL=1
(synthetic delivery test) · ALERT_PROTECTIVE_ROW=1 / EOD lane uses row 2 in alert_state.
  • thesis (~18:00 ET Sunday) — AI thesis reviews for all open positions that
    have a user_thesis written at BUY entry. One LLM call per position, saves to
    thesis_reviews table. Inert without ANTHROPIC_API_KEY.

"""

import hashlib
import os
import sys
from datetime import datetime

import pytz

from stock_analyzer import db
from stock_analyzer.constants import ALERT_EMAIL_HOUR_ET, ALERT_EOD_HOUR_ET
from stock_analyzer.data import is_trading_day
from stock_analyzer.headless_alert_engine import (
    compute_protective_alerts, compute_eod, compute_morning_picks,
)
from stock_analyzer.notify import (
    render_alert_email, render_test_email, render_pullback_email,
    render_buy_picks_email, send_email_resend,
)

_ET = pytz.timezone("America/New_York")
_PROTECTIVE_ROW = 1   # alert_state lane: pre-market protective dedup
_EOD_ROW = 2          # alert_state lane: EOD pullback dedup
_BUY_ROW = 3          # alert_state lane: morning buy-list dedup


def _log(msg: str) -> None:
    print(f"[alerts-cron] {msg}", flush=True)


def _fingerprint(alerts: list[dict]) -> str:
    """Stable hash of the protective SET — by (kind, ticker), not wording, so a
    re-phrased directive doesn't re-trigger an email. Empty set → 'none'."""
    if not alerts:
        return "none"
    keys = sorted(f"{a.get('kind')}:{a.get('ticker')}" for a in alerts)
    return hashlib.sha1("|".join(keys).encode("utf-8")).hexdigest()[:16]


def _send_email(label: str, subject: str, html: str) -> bool:
    """Send via Resend; log the outcome (never the key). Returns sent bool.
    Inert (logs only) when RESEND_API_KEY is absent."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    to      = os.environ.get("ALERT_EMAIL_TO", "")
    sender  = os.environ.get("ALERT_EMAIL_FROM", "")
    if not api_key:
        _log(f"INERT: would send {label} email '{subject}' (no RESEND_API_KEY) — skip.")
        return False
    ok, detail = send_email_resend(api_key=api_key, sender=sender, to=to,
                                   subject=subject, html=html)
    _log(f"{label} email send {'OK' if ok else 'FAILED'} → {to or '(no ALERT_EMAIL_TO)'}"
         + (f" · {detail}" if detail else ""))
    return ok


def _run_test_email(now_et) -> int:
    """Synthetic delivery test (any mode) — proves Resend → inbox, leaves dedup
    state untouched so a later real alert still sends."""
    payload = compute_protective_alerts(today=now_et.date())
    n = len(payload.get("alerts", []))
    subject, html = render_test_email(n, payload.get("built_at", now_et.date().isoformat()))
    _send_email("TEST", subject, html)
    _log("test mode — dedup state untouched; done.")
    return 0


def _run_premarket(now_et, force: bool) -> int:
    today_str = now_et.date().isoformat()
    if not force:
        if not is_trading_day(now_et.date()):
            _log("premarket: not an ET trading day — skip.")
            return 0
        if now_et.hour < ALERT_EMAIL_HOUR_ET:
            _log(f"premarket: too early (ET hour {now_et.hour} < {ALERT_EMAIL_HOUR_ET}) — skip.")
            return 0
    state = db.load_alert_state(_PROTECTIVE_ROW) or {}

    payload = compute_protective_alerts(today=now_et.date())
    alerts = payload.get("alerts", [])
    for e in payload.get("errors", []):
        _log(f"engine note: {e}")
    _log(f"computed {len(alerts)} protective alert(s): "
         + (", ".join(f"{a.get('kind')}/{a.get('ticker')}" for a in alerts) or "(none)"))

    fp = _fingerprint(alerts)
    sent = False
    if not alerts:
        _log("nothing to act on — no email.")
    elif fp == state.get("last_fingerprint") and not force:
        _log(f"unchanged since last send (fp={fp}) — no email (anti-spam).")
    else:
        subject, html = render_alert_email(alerts, payload.get("built_at", today_str))
        sent = _send_email("protective", subject, html)

    if sent or not alerts:
        # Save dedup state ONLY on a real send or a legitimately empty run — so a
        # transient Resend failure is retried by the later DST slot rather than
        # silently suppressed (matches buy-lane dedup contract).
        if db.save_alert_state(today_str, fp, _PROTECTIVE_ROW):
            _log(f"state saved (row={_PROTECTIVE_ROW}, date={today_str}, fp={fp}).")
        else:
            _log("state NOT saved (DB offline / table missing) — dedup degrades to always-send.")
    else:
        _log("email not sent — state NOT saved so later slot can retry.")
    _log(f"premarket done · sent={sent}")
    return 0


def _run_eod(now_et, force: bool) -> int:
    today_str = now_et.date().isoformat()
    if not force:
        if not is_trading_day(now_et.date()):
            _log("eod: not an ET trading day — skip.")
            return 0
        if now_et.hour < ALERT_EOD_HOUR_ET:
            _log(f"eod: too early (ET hour {now_et.hour} < {ALERT_EOD_HOUR_ET}) — wait for post-close slot.")
            return 0

    payload = compute_eod(today=now_et.date())
    for e in payload.get("errors", []):
        _log(f"engine note: {e}")

    # 1. Today's-P&L baseline: write today's snapshot (idempotent upsert). Makes
    # the baseline deterministic even on days the app isn't opened post-close.
    rows = payload.get("snapshot_rows", [])
    if rows and db.save_daily_snapshot(now_et.date(), rows):
        _log(f"daily_snapshot written ({len(rows)} positions, date={today_str}).")
    else:
        _log(f"daily_snapshot NOT written ({len(rows)} rows; DB offline / table missing / empty).")

    # 2. Reactive pullback email — once per qualifying down-day (row 2 dedup).
    pb = payload.get("pullback")
    sent = False
    if not pb:
        _log("no qualifying pullback today.")
    else:
        _log(f"pullback: S&P {pb.get('index_pct')}% · book~{pb.get('book_implied_pct')}% "
             f"({pb.get('severity')}).")
        state = db.load_alert_state(_EOD_ROW) or {}
        if state.get("last_emailed_date") == today_str and not force:
            _log("pullback already emailed today — skip.")
        else:
            subject, html = render_pullback_email(pb, payload.get("built_at", today_str))
            sent = _send_email("pullback", subject, html)
            if db.save_alert_state(today_str, "pullback", _EOD_ROW):
                _log(f"state saved (row={_EOD_ROW}, date={today_str}).")
    _log(f"eod done · snapshot={bool(rows)} · pullback_sent={sent}")
    return 0


def _buy_fingerprint(picks: list[dict]) -> str:
    """Stable hash of the buy-list SET by ticker — re-send only when the set of
    tickers changes (not on re-ordering / wording). Empty set → 'none'."""
    if not picks:
        return "none"
    keys = sorted(str(p.get("ticker") or "").upper() for p in picks if p.get("ticker"))
    return hashlib.sha1("|".join(keys).encode("utf-8")).hexdigest()[:16]


def _run_scan(now_et, force: bool) -> int:
    """Mid-morning headless run (~9:45 ET): (1) sector scan → persist to
    scanner_cache so the Home buy-candidate / Grow-Today lists populate on a COLD
    load without the user running the ~20s scanner; (2) email the high-conviction
    "New Positions to Initiate" (Go — composite confirms) so the user can act from
    mobile. Post-open gated (today's price action must be real). Persist is inert
    until the scanner_cache table exists; the email is inert without RESEND_API_KEY.
    Always exits 0."""
    today_str = now_et.date().isoformat()
    if not force:
        if not is_trading_day(now_et.date()):
            _log("scan: not an ET trading day — skip.")
            return 0
        # Post-open only — scanning pre-open scores on a stale/forming bar and the
        # buy list must reflect today's action. (DST: the earlier UTC slot lands
        # pre-open in winter and is skipped here; the later slot runs post-open.)
        if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30):
            _log(f"scan: too early (ET {now_et.strftime('%H:%M')} — pre-open) — wait for post-open slot.")
            return 0
    from stock_analyzer.scanner import scan_sectors, SECTOR_UNIVERSE
    try:
        results_df = scan_sectors(list(SECTOR_UNIVERSE.keys()), period="6mo")
    except Exception as e:
        _log(f"scan: scan_sectors error — {str(e)[:120]}")
        return 0
    n = 0 if results_df is None or results_df.empty else len(results_df)
    if n == 0:
        _log("scan: no results (provider miss from datacenter IP?) — nothing persisted.")
        return 0
    if db.save_scanner_cache(results_df, now_et.date(), source="cron"):
        _log(f"scan: persisted {n} results (date={today_str}).")
    else:
        _log(f"scan: NOT persisted ({n} results; DB offline / table missing / read-only).")

    # ── Morning buy-list email — high-conviction New Positions to Initiate ──────
    sent = False
    payload = compute_morning_picks(today=now_et.date(), scanner_results=results_df)
    for e in payload.get("errors", []):
        _log(f"engine note: {e}")
    picks = payload.get("picks", [])
    # High-conviction = the green "✅ Go — Composite Confirms" set. Key on the SAME
    # field the Home badge reads — the reconciliation engine's verdict
    # (xref.verdict_reconciled.verdict == "go", signal_reconciliation.py) — so the
    # email can't silently drift from the badge if either detector changes.
    # Composite must be present. Excludes unverified / movers-only / conflicted noise.
    hi = [p for p in picks
          if ((p.get("xref") or {}).get("verdict_reconciled") or {}).get("verdict") == "go"
          and p.get("composite_score") is not None]
    _log(f"morning picks: {len(picks)} new pick(s), {len(hi)} high-conviction (Go): "
         + (", ".join(str(p.get("ticker")) for p in hi) or "(none)"))
    if not hi:
        # Self-explaining 0: a 0-pick run almost always means the tone gate, not a
        # fault. Flat tape raises the new-pick bar to 78 (bull = 65); bear suppresses
        # new entries outright. So "0 picks" next to a Home page showing morning
        # picks (scored 65–77) is the bar moving, not a broken scan.
        d = payload.get("diag") or {}
        _tone = d.get("tone", "?")
        _sp = d.get("sp500_pct")
        _spr = f"{_sp:+.2f}%" if isinstance(_sp, (int, float)) else "n/a"
        if _tone == "bear":
            _why = f"tone=bear (S&P {_spr}) — new entries suppressed on a risk-off tape"
        elif d.get("bar") is not None:
            _why = (f"tone={_tone} (S&P {_spr}) · new-pick bar={d['bar']}"
                    + (" (flat-day — bull-day bar=65)" if _tone == "flat" else ""))
            _drop = []
            if d.get("sector_blocked"):    _drop.append(f"{d['sector_blocked']} sector-capped")
            if d.get("macro_blocked"):     _drop.append(f"{d['macro_blocked']} macro-gated")
            if d.get("composite_short"):   _drop.append(f"{d['composite_short']} below Buy(65)")
            if d.get("composite_unavail"): _drop.append(f"{d['composite_unavail']} no composite")
            if _drop:
                _why += " · dropped: " + ", ".join(_drop)
        else:
            _why = f"tone={_tone} (S&P {_spr})"
        _log(f"no high-conviction buy setups — no email · {_why}.")
    else:
        fp = _buy_fingerprint(hi)
        state = db.load_alert_state(_BUY_ROW) or {}
        if (state.get("last_emailed_date") == today_str
                and state.get("last_fingerprint") == fp and not force):
            _log(f"buy-list unchanged since last send (fp={fp}) — no email.")
        else:
            subject, html = render_buy_picks_email(hi, payload.get("built_at", today_str))
            sent = _send_email("buy-setups", subject, html)
            # Save dedup state ONLY on a real send — so a transient Resend failure
            # (key present, send errored) is retried by the later DST slot rather
            # than silently suppressed. (Inert/no-key also won't save → harmless.)
            if sent and db.save_alert_state(today_str, fp, _BUY_ROW):
                _log(f"buy state saved (row={_BUY_ROW}, date={today_str}, fp={fp}).")
            elif not sent:
                _log("buy email not sent (inert/failed) — state NOT saved (later slot may retry).")
    _log(f"scan done · persisted={n} · buy_sent={sent}")
    return 0


def _run_thesis(now_et, force: bool) -> int:
    """Sunday evening: AI thesis review for all open positions with a user thesis.
    One LLM call per position. Inert without ANTHROPIC_API_KEY."""
    if not force and now_et.weekday() != 6:   # 6 = Sunday
        _log("thesis: not Sunday — skip.")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        _log("thesis: INERT — no ANTHROPIC_API_KEY set. Add it to GitHub secrets to activate.")
        return 0

    from stock_analyzer import thesis_advisor as _ta
    from stock_analyzer.headless_alert_engine import _build_context

    # Load open positions via the shared headless data-prep path
    today = now_et.date()
    try:
        ctx = _build_context(today)
    except Exception as e:
        _log(f"thesis: _build_context failed — {str(e)[:120]}")
        return 0

    if not ctx.get("ok"):
        _log(f"thesis: context load failed — {'; '.join(ctx.get('errors', []))}")
        return 0

    held_data   = ctx.get("held_data", {})
    open_tickers = set(held_data.keys())

    # Load trades and find BUYs with a thesis for open positions
    trades_df = db.load_trades()
    if trades_df.empty or "user_thesis" not in trades_df.columns:
        _log("thesis: no trades with user_thesis found — add theses via Trade Journal.")
        return 0

    buys_with_thesis = (
        trades_df[
            (trades_df["action"] == "BUY") &
            (trades_df["user_thesis"].notna()) &
            (trades_df["user_thesis"].astype(str).str.strip() != "") &
            (trades_df["ticker"].astype(str).str.upper().isin(open_tickers))
        ]
        .sort_values("traded_at", ascending=False)
        .drop_duplicates(subset="ticker")
    )

    if buys_with_thesis.empty:
        _log("thesis: no open positions have a user thesis yet — skip.")
        return 0

    _log(f"thesis: reviewing {len(buys_with_thesis)} position(s): "
         + ", ".join(buys_with_thesis["ticker"].astype(str).str.upper()))

    # Build positions list for batch review
    positions = []
    for _, row in buys_with_thesis.iterrows():
        ticker = str(row["ticker"]).upper()
        hd     = held_data.get(ticker, {})
        ind    = hd.get("indicators", {})
        tech   = {
            "above_sma50":     bool(ind.get("above_sma50", False)),
            "rsi":             ind.get("rsi"),
            "momentum_1m_pct": ind.get("momentum_1m_pct"),
        }
        fund = {
            "revenue_growth": hd.get("revenue_growth"),
            "profit_margin":  hd.get("profit_margin"),
        }
        raw_news = hd.get("news") or []
        headlines = [
            n.get("headline", n.get("title", "")) for n in raw_news
            if n.get("headline") or n.get("title")
        ][:15]
        positions.append({
            "ticker":      ticker,
            "trade_date":  str(row.get("traded_at", ""))[:10],
            "user_thesis": str(row["user_thesis"]),
            "inputs":      _ta.build_review_inputs(
                technical=tech, fundamentals=fund, news_headlines=headlines,
            ),
        })

    results = _ta.run_batch_review(positions, api_key=api_key)
    _log(f"thesis: LLM returned {len(results)} review(s).")

    saved = 0
    for rec in results:
        if db.save_thesis_review(rec):
            saved += 1
            _log(f"  {rec['ticker']}: {rec['status']} — saved.")
        else:
            _log(f"  {rec['ticker']}: {rec['status']} — save FAILED (DB offline?).")

    _log(f"thesis done · reviewed={len(results)} · saved={saved}")
    return 0


def _run_debrief(now_et, force: bool) -> int:
    """Sunday evening: generate weekly portfolio debrief via LLM and email it.
    Runs after thesis reviews in the same Sunday cron slot. Requires
    daily_snapshots to have >= 5 trading days of data."""
    if not force and now_et.weekday() != 6:   # 6 = Sunday
        _log("debrief: not Sunday — skip.")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        _log("debrief: INERT — no ANTHROPIC_API_KEY set.")
        return 0

    from stock_analyzer import debrief_advisor as _da
    from stock_analyzer import notify as _notify

    week_ending = now_et.date()
    week_start  = week_ending - __import__("datetime").timedelta(days=6)

    # Load snapshot data for the week
    snapshots_df = db.load_daily_snapshots(start_date=week_start, end_date=week_ending)
    days_available = len(snapshots_df["snapshot_date"].unique()) if not snapshots_df.empty else 0

    if days_available < 5:
        _log(f"debrief: only {days_available} snapshot day(s) available — need 5. "
             f"Earliest full debrief after {week_start + __import__('datetime').timedelta(days=5 - days_available)}.")
        return 0

    # Load recommendations and trades for the week
    recs_df   = db.load_recommendations(start_date=week_start, end_date=week_ending)
    trades_df = db.load_trades()

    # Fetch SPY return for the week
    spy_week_pct = None
    try:
        import yfinance as yf
        spy = yf.download("SPY", start=str(week_start), end=str(week_ending), progress=False, auto_adjust=True)
        if not spy.empty and len(spy) >= 2:
            spy_pct = float((spy["Close"].iloc[-1] - spy["Close"].iloc[0]) / spy["Close"].iloc[0] * 100)
            spy_week_pct = round(spy_pct, 2)
    except Exception:
        pass

    # Collect BROKEN theses from thesis_reviews
    broken_theses: list[str] = []
    try:
        reviews_df = db.load_thesis_reviews()
        if not reviews_df.empty:
            broken_theses = (
                reviews_df[reviews_df["status"] == "BROKEN"]["ticker"]
                .astype(str).str.upper().unique().tolist()
            )
    except Exception:
        pass

    # Build data package and call LLM
    package = _da.build_debrief_package(
        week_ending   = week_ending,
        snapshots_df  = snapshots_df,
        recs_df       = recs_df,
        trades_df     = trades_df,
        spy_week_pct  = spy_week_pct,
        broken_theses = broken_theses,
    )
    _log(f"debrief: {days_available} snapshot day(s) · "
         f"{len(recs_df) if not recs_df.empty else 0} rec(s) · "
         f"SPY {spy_week_pct:+.1f}%" if spy_week_pct is not None else
         f"debrief: {days_available} snapshot day(s) · "
         f"{len(recs_df) if not recs_df.empty else 0} rec(s) · SPY N/A")

    result = _da.generate_debrief(package, api_key)
    if result is None:
        _log("debrief: LLM call failed or insufficient snapshot data — skip.")
        return 0

    saved = db.save_weekly_debrief(result)
    _log(f"debrief: saved={saved} · week_ending={result['week_ending']} · "
         f"perf={result.get('performance_pct')} · alpha={result.get('alpha_pct')}")

    # Email
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    email_to   = os.environ.get("ALERT_EMAIL_TO", "").strip()
    email_from = os.environ.get("ALERT_EMAIL_FROM", "").strip()
    if resend_key and email_to and email_from:
        html    = _notify.render_debrief_email(result)
        subject = f"DRISHTA Weekly Debrief — week of {result['week_ending']}"
        ok, detail = _notify.send_email_resend(
            api_key=resend_key, sender=email_from, to=email_to,
            subject=subject, html=html,
        )
        _log(f"debrief: email {'sent' if ok else 'FAILED'}" + (f" — {detail}" if detail else ""))
        if ok:
            try:
                db._client().table("weekly_debriefs").update(
                    {"email_sent": True, "email_sent_at": __import__("datetime").datetime.utcnow().isoformat()}
                ).eq("week_ending", str(result["week_ending"])).execute()
            except Exception:
                pass
    else:
        _log("debrief: no Resend credentials — debrief saved but not emailed.")

    return 0


def _run_monthly_report(now_et, force: bool) -> int:
    """First Sunday of the month: generate the monthly Portfolio Intelligence
    Report (F-4) via LLM and email it. Runs after the weekly debrief in the same
    Sunday cron lane. v1 grades engine entry quality (Q0) + signal discipline (Q1).
    Inert without ANTHROPIC_API_KEY."""
    import datetime as _dt

    # First-Sunday-of-month gate (Sunday AND day-of-month ≤ 7). force bypasses.
    if not force and not (now_et.weekday() == 6 and now_et.day <= 7):
        _log("monthly: not the first Sunday of the month — skip.")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        _log("monthly: INERT — no ANTHROPIC_API_KEY set.")
        return 0

    from stock_analyzer import intelligence_report as _ir
    from stock_analyzer import notify as _notify
    from stock_analyzer.constants import MONTHLY_REPORT_MIN_GRADED

    period_end   = now_et.date()
    period_start = period_end - _dt.timedelta(days=28)   # trailing ~4 weeks

    recs_df   = db.load_recommendations(start_date=period_start, end_date=period_end)
    trades_df = db.load_trades()

    if recs_df is None or recs_df.empty:
        _log(f"monthly: no recommendations in {period_start}→{period_end} — nothing to report yet.")
        return 0

    # Current prices for the rec tickers (marks open BUYs + missed-rec would-have-gained).
    prices: dict = {}
    try:
        from stock_analyzer.data import fetch_live_prices
        tickers = sorted({
            str(t).strip().upper() for t in recs_df["ticker"].dropna().tolist() if str(t).strip()
        })
        if tickers:
            px = fetch_live_prices(tickers) or {}
            prices = {t: float(d.get("price", 0)) for t, d in px.items() if d and d.get("price")}
    except Exception as e:
        _log(f"monthly: live-price fetch failed ({str(e)[:80]}) — graded outcomes degrade gracefully.")

    # SPY close-by-date series for the regime/alpha benchmark.
    spy_by_date: dict = {}
    try:
        import yfinance as yf
        spy = yf.download("SPY", start=str(period_start - _dt.timedelta(days=5)),
                          end=str(period_end + _dt.timedelta(days=1)),
                          progress=False, auto_adjust=True)
        if spy is not None and not spy.empty and "Close" in spy.columns:
            for idx, row in spy.iterrows():
                d = idx.date() if hasattr(idx, "date") else None
                try:
                    c = float(row["Close"])
                except (TypeError, ValueError):
                    c = None
                if d is not None and c and c > 0:
                    spy_by_date[d] = c
    except Exception:
        pass

    # Recent weekly debriefs for the trajectory line.
    weekly_rows: list = []
    try:
        wdf = db.load_weekly_debriefs(limit=5)
        if wdf is not None and not wdf.empty:
            weekly_rows = wdf.to_dict("records")
    except Exception:
        pass

    package = _ir.build_report_package(
        period_start=period_start, period_end=period_end,
        recs_df=recs_df, trades_df=trades_df,
        current_prices=prices, spy_close_by_date=spy_by_date,
        weekly_rows=weekly_rows, min_graded=MONTHLY_REPORT_MIN_GRADED,
    )
    _log(f"monthly: {package['n_total']} rec(s) · acted={package['n_acted']} · "
         f"graded={package['n_graded']} · q0_ready={package['q0_ready']} · "
         f"engine_alpha={package.get('engine_alpha_pct')}")

    result = _ir.generate_report(package, api_key)
    if result is None:
        _log("monthly: LLM call failed or no data — skip.")
        return 0

    saved = db.save_monthly_report(result)
    _log(f"monthly: saved={saved} · period={result['period_start']}→{result['period_end']} · "
         f"engine_alpha={result.get('engine_alpha_pct')}")

    # Email
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    email_to   = os.environ.get("ALERT_EMAIL_TO", "").strip()
    email_from = os.environ.get("ALERT_EMAIL_FROM", "").strip()
    if resend_key and email_to and email_from:
        html    = _notify.render_intelligence_email(result)
        subject = f"DRISHTA Monthly Intelligence — {result['period_end']}"
        ok, detail = _notify.send_email_resend(
            api_key=resend_key, sender=email_from, to=email_to, subject=subject, html=html,
        )
        _log(f"monthly: email {'sent' if ok else 'FAILED'}" + (f" — {detail}" if detail else ""))
        if ok:
            try:
                db._client().table("monthly_reports").update(
                    {"email_sent": True, "email_sent_at": _dt.datetime.utcnow().isoformat()}
                ).eq("period_end", str(result["period_end"])).execute()
            except Exception:
                pass
    else:
        _log("monthly: no Resend credentials — report saved but not emailed.")

    return 0


def main() -> int:
    force = os.environ.get("ALERT_FORCE", "") == "1"
    test_email = os.environ.get("ALERT_TEST_EMAIL", "") == "1"
    now_et = datetime.now(_ET)
    # Derive mode from ET hour; named overrides (scan, thesis, debrief, monthly) bypass time-inference.
    _mode_override = os.environ.get("ALERT_RUN_MODE", "").strip().lower()
    mode = _mode_override if _mode_override in ("scan", "thesis", "debrief", "monthly") else (
        "eod" if now_et.hour >= 12 else "premarket"
    )
    _log(f"start · {now_et.isoformat()} ET · mode={mode} · force={force} · test_email={test_email}")

    if test_email:
        return _run_test_email(now_et)
    if mode == "scan":
        return _run_scan(now_et, force)
    if mode == "thesis":
        # Sunday lane: thesis review → weekly debrief → (first Sunday only) monthly report.
        _run_thesis(now_et, force)
        _run_debrief(now_et, force)
        return _run_monthly_report(now_et, force)
    if mode == "debrief":
        return _run_debrief(now_et, force)
    if mode == "monthly":
        return _run_monthly_report(now_et, force)
    if mode == "eod":
        return _run_eod(now_et, force)
    return _run_premarket(now_et, force)


if __name__ == "__main__":
    sys.exit(main())
