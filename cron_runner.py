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

Mode = $ALERT_RUN_MODE if it's one of scan|intraday|thesis|debrief|monthly
(workflow_dispatch input selects one of these directly), else derived from the
ET hour (≥12:00 ET ⇒ eod, else premarket) — premarket/eod are never taken as a
direct override value, only ever hour-derived. All output → stdout (the
Actions log). Ships INERT: no RESEND_API_KEY ⇒ compute + log, send nothing.
Exits 0 on every mode except a Sunday thesis-lane sub-job failure (thesis/
debrief/monthly), which deliberately returns 1 so GitHub Actions marks the
run failed — the dead-man's-switch failure notification depends on this.

Env: SUPABASE_URL/SUPABASE_KEY (service-role) · FINNHUB_API_KEY/FMP_API_KEY/
FRED_API_KEY (optional providers) · RESEND_API_KEY/ALERT_EMAIL_TO/ALERT_EMAIL_FROM
· ALERT_RUN_MODE (scan|intraday|thesis|debrief|monthly) · ALERT_FORCE=1 (bypass guards) · ALERT_TEST_EMAIL=1
(synthetic delivery test) · ALERT_PROTECTIVE_ROW=1 / EOD lane uses row 2 in alert_state.
  • thesis (~18:00 ET Sunday) — AI thesis reviews for all open positions that
    have a user_thesis written at BUY entry. One LLM call per position, saves to
    thesis_reviews table. Inert without ANTHROPIC_API_KEY.

"""

import hashlib
import os
import sys
from datetime import datetime, timedelta

import pytz

from stock_analyzer import db
from stock_analyzer.constants import ALERT_EMAIL_HOUR_ET, ALERT_EOD_HOUR_ET
from stock_analyzer.data import is_trading_day
from stock_analyzer.headless_alert_engine import (
    compute_protective_alerts, compute_eod, compute_morning_picks,
)
from stock_analyzer.notify import (
    render_alert_email, render_test_email, render_pullback_email,
    render_daily_action_email, render_intraday_entry_email, send_email_resend,
)

_ET = pytz.timezone("America/New_York")
_PROTECTIVE_ROW = 1   # alert_state lane: pre-market protective dedup
_EOD_ROW = 2          # alert_state lane: EOD pullback dedup
_BUY_ROW = 3          # alert_state lane: morning buy-list dedup
_INTRADAY_ROW = 4     # alert_state lane: intraday pullback entry dedup


def _build_new_pick_rows(picks: list[dict], rec_date) -> list[dict]:
    """Pure row-shaping helper (Self Track Record — cron coverage fix): turn
    today's `new_picks` list from compute_morning_picks into the same row
    shape app.py's interactive path builds for save_recommendations (app.py
    ~line 4763), minus the pillar-score columns (s_score/avg_sent/t_score/
    bq_score/val_score) — those live in session_state caches
    (_grow_composites / _last_held_data) that only exist in a live
    interactive session; they're optional/nullable columns on save, so
    leaving them unset here is safe, not a data loss. Skips any pick with no
    ticker. No local dedup — db.save_recommendations' own upsert
    (on_conflict="ticker,rec_date,rec_type", ignore_duplicates=True) is the
    idempotency guarantee, so calling this twice for the same day and
    feeding both outputs to save_recommendations is safe by construction."""
    rows: list[dict] = []
    for p in picks:
        tk = p.get("ticker")
        if not tk:
            continue
        # Explicit is-None/type check rather than `p.get("xref") or {}` — the
        # antipattern gate (check_antipatterns.py OFFLINE_SENTINEL_COLLAPSE)
        # flags that idiom regardless of context; here "xref" simply may not
        # be a dict, not a producer-cache offline signal, but the fix is the
        # same either way per CLAUDE.md's recurring-defect-gate guidance.
        _xref = p.get("xref")
        _verdict = _xref.get("verdict") if isinstance(_xref, dict) else None
        rows.append({
            "ticker":           str(tk),
            "rec_date":         rec_date,
            "rec_type":         "new_pick",
            "price_at_surface": p.get("price"),
            "composite_score":  p.get("composite_score"),
            "momentum_score":   p.get("score"),
            "sector":           p.get("sector", ""),
            "conviction":       p.get("conviction", ""),
            "verdict":          _verdict or "",
            "thesis":           p.get("thesis", ""),
        })
    return rows


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

    # exit_signals capture — closes the gap where WATCH/TRIM/EXIT/RISK_OFF signal
    # history only existed for days the app was opened (app.py's own MISS-path
    # capture). Idempotent upsert on (ticker, signal_date, signal_type), so this
    # is safe even if an interactive session also captures the same day.
    exit_signal_rows = []
    for d in payload.get("all_deterioration_signals", []):
        tier = d.get("tier")
        if tier not in ("WATCH", "TRIM", "EXIT"):
            continue
        exit_signal_rows.append({
            "ticker": d.get("ticker"), "signal_date": today_str, "signal_type": tier,
            "composite_score": d.get("composite_score"),
            "price_at_signal": d.get("price"),
            "dd_from_peak_pct": d.get("dd_from_peak_pct"),
            "pnl_pct": d.get("pnl_pct"),
            "below_ma_count": d.get("below_ma_count"),
            "rel_strength": d.get("rel_strength"),
        })
    for c in payload.get("risk_off_signals", []):
        exit_signal_rows.append({
            "ticker": c.get("ticker"), "signal_date": today_str, "signal_type": "RISK_OFF",
            "composite_score": c.get("composite_score"),
            "price_at_signal": c.get("price"),
            "dd_from_peak_pct": c.get("dd_from_peak_pct"),
            "pnl_pct": c.get("pnl_pct"),
            "below_ma_count": c.get("below_ma_count"),
            "rel_strength": c.get("rel_strength"),
        })
    if exit_signal_rows:
        db.save_exit_signals_batch(exit_signal_rows)
        _log(f"exit_signals captured ({len(exit_signal_rows)} rows, date={today_str}).")

    # Analyst-target consensus snapshot — log-only Phase 1, no alert wired yet.
    # Reuses the bundles already loaded for the checks above (zero extra API cost).
    target_rows = payload.get("analyst_target_snapshots", [])
    if target_rows:
        db.save_analyst_target_snapshots_batch(target_rows)
        _log(f"analyst_target_snapshots captured ({len(target_rows)} rows, date={today_str}).")

    # Velocity check — detect WATCH tickers whose composite score is accelerating
    # toward TRIM. Silently skips when exit_signals has < 2 days of WATCH history
    # for a ticker; fills in naturally as data accumulates post-2026-07-21 launch.
    velocity_alerts: list[dict] = []
    try:
        from stock_analyzer.exit_velocity import find_accelerating_watches
        from stock_analyzer.constants import (
            EXIT_VELOCITY_LOOKBACK_DAYS, EXIT_VELOCITY_DROP_THRESHOLD,
        )
        _signals_hist = db.load_exit_signals(days_back=EXIT_VELOCITY_LOOKBACK_DAYS)
        _watch_tickers = [
            d.get("ticker") for d in payload.get("all_deterioration_signals", [])
            if d.get("tier") == "WATCH" and d.get("ticker")
        ]
        if _watch_tickers and _signals_hist is not None and not _signals_hist.empty:
            velocity_alerts = find_accelerating_watches(
                _signals_hist, _watch_tickers,
                EXIT_VELOCITY_LOOKBACK_DAYS, EXIT_VELOCITY_DROP_THRESHOLD,
            )
            if velocity_alerts:
                _log("velocity alerts: " + ", ".join(
                    f"{v['ticker']} ({v['delta']:+.1f}pt over {v['n_days']}d)"
                    for v in velocity_alerts
                ))
            else:
                _log(f"velocity check: {len(_watch_tickers)} WATCH ticker(s), none accelerating.")
        else:
            _log("velocity check: no WATCH tickers or insufficient history — skip.")
    except Exception as _ve:
        _log(f"velocity check failed: {str(_ve)[:80]} — continuing without.")

    # Combined fingerprint includes velocity tickers so a new acceleration
    # triggers a re-send even when the hard-alert set is unchanged.
    _fp_input = alerts + [{"kind": "velocity", "ticker": v["ticker"]} for v in velocity_alerts]
    fp = _fingerprint(_fp_input)
    sent = False
    if not alerts and not velocity_alerts:
        _log("nothing to act on — no email.")
    elif fp == state.get("last_fingerprint") and not force:
        _log(f"unchanged since last send (fp={fp}) — no email (anti-spam).")
    else:
        subject, html = render_alert_email(
            alerts, payload.get("built_at", today_str), velocity_alerts=velocity_alerts,
        )
        sent = _send_email("protective", subject, html)

    if sent or (not alerts and not velocity_alerts):
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

    # 2. Sentiment snapshot: persist VADER + Finnhub readings for all held tickers
    # so Tier 3 sentiment-vs-price-move analysis has a growing daily series.
    _held_bundles = payload.get("held_data") or {}
    _snap_sentiment_rows: list = []
    if _held_bundles:
        _snap_sentiment_rows = []
        for _stk, _sb in _held_bundles.items():
            if not isinstance(_sb, dict):
                continue
            _finnhub_sent: dict = {}
            try:
                from stock_analyzer.news_sentiment import fetch_sentiment_for_tickers as _fsft
                _finnhub_sent = _fsft([_stk]).get(_stk, {})
            except Exception:
                pass
            _snap_sentiment_rows.append({
                "ticker":         _stk,
                "vader_compound": _sb.get("avg_sent"),
                "vader_score":    _sb.get("s_score"),
                "headline_count": len(_sb.get("headlines") or []),
                "bullish_pct":    _finnhub_sent.get("bullish_pct"),
                "bearish_pct":    _finnhub_sent.get("bearish_pct"),
                "buzz_score":     _finnhub_sent.get("buzz_score"),
                "company_score":  _finnhub_sent.get("company_score"),
                "vs_sector_pp":   _finnhub_sent.get("vs_sector_pp"),
                "source":         "cron",
            })
        if db.save_sentiment_snapshot(now_et.date(), _snap_sentiment_rows):
            _log(f"sentiment_snapshot written ({len(_snap_sentiment_rows)} tickers, date={today_str}).")
        else:
            _log("sentiment_snapshot NOT written (DB offline / table missing / no rows).")
    else:
        _log("sentiment_snapshot skipped — no held_data in payload.")

    # 3. Daily regime persistence — one row/day, portfolio-independent, so a
    # future "regime changed since yesterday" annotation has a real day-over-day
    # series instead of only ever the current session's ephemeral cache.
    _regime_tag = None
    try:
        from stock_analyzer.macro_calendar import detect_macro_regime
        regime = detect_macro_regime(os.environ.get("FRED_API_KEY") or None)
        _regime_tag = regime.get("regime")
        if db.save_daily_regime(now_et.date(), regime):
            _log(f"daily_regime written (regime={regime.get('regime')}, date={today_str}).")
        else:
            _log("daily_regime NOT written (DB offline / table missing).")
    except Exception as e:
        _log(f"daily_regime detection failed: {str(e)[:120]}")

    # 4. Reactive pullback email — once per qualifying down-day (row 2 dedup).
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
            # Save dedup state ONLY on a real send — so a transient Resend failure
            # (key present, send errored) is retried by the later DST slot rather
            # than silently suppressed for the day (matches the premarket/buy-lane
            # contract above). Inert/no-key also won't save → harmless.
            if sent and db.save_alert_state(today_str, "pullback", _EOD_ROW):
                _log(f"state saved (row={_EOD_ROW}, date={today_str}).")
            elif not sent:
                _log("pullback email not sent (inert/failed) — state NOT saved so later slot can retry.")

    # 5. Predictive Modeling Shadow Layer (Phase 1, F-234) — LIVE vol forecast.
    # MEASUREMENT-ONLY: writes one quarantined model_predictions row per held
    # ticker + the portfolio aggregate. Feeds NO gate/recommendation/composite —
    # see docs/plans/predictive-modeling-shadow-layer.md. Reuses the SAME 6mo
    # bars this run's _build_context already fetched (payload["held_data"][t]
    # ["df"]["Close"]) — no new fetch. Skips (writes nothing) for any ticker
    # whose bars are too thin/unavailable, rather than logging a guessed
    # forecast from a known-offline bundle (design doc §1.6b, mockup state 3).
    try:
        _n_pred = _write_live_vol_predictions(now_et, payload, _regime_tag)
        _log(f"model_predictions (live): {_n_pred} row(s) written.")
    except Exception as e:
        _log(f"model_predictions (live) FAILED — {str(e)[:120]} — continuing.")

    # 6. Predictive Modeling Shadow Layer — maturation. Independent of step 5
    # (a PRIOR day's live/backfill prediction can mature today even if today's
    # live forecast above failed). A maturing ticker may no longer be held, so
    # this step fetches its own targeted price history rather than reusing
    # payload["held_data"] (which only covers CURRENTLY held tickers).
    try:
        _n_mat = _mature_vol_predictions(now_et)
        _log(f"model_predictions (maturation): {_n_mat} row(s) matured.")
    except Exception as e:
        _log(f"model_predictions (maturation) FAILED — {str(e)[:120]} — continuing.")

    _log(f"eod done · snapshot={bool(rows)} · sentiment={bool(_snap_sentiment_rows)} · pullback_sent={sent}")
    return 0


def _write_live_vol_predictions(now_et, payload: dict, regime_tag: str | None) -> int:
    """Compute + persist one 'live' model_predictions row per held ticker +
    the portfolio aggregate, from the bars already fetched by this EOD run's
    _build_context (payload['held_data'][t]['df']) — NO new fetch. Returns the
    count of rows written (0 on any structural miss — insufficient bars,
    nothing held, etc — never raises; the caller already wraps this call in
    its own try/except for defense in depth)."""
    import pandas as pd

    from stock_analyzer.constants import VOL_FORECAST_EWMA_LAMBDA, VOL_FORECAST_HORIZON_DAYS
    from stock_analyzer.vol_forecast import forecast_vol_ewma, realized_vol

    held_data = payload.get("held_data", {})
    if not held_data:
        return 0

    # Normalized to the ET *date* at midnight, NOT now_et.isoformat() (wall-
    # clock time) — the EOD lane is demonstrably re-entrant within a day (any
    # post-close slot, or a `force` re-run; the daily_snapshot step a few
    # lines above this one upserts keyed by date for exactly this reason). A
    # wall-clock made_at would defeat the (model_name, model_version, scope,
    # ticker, made_at) upsert key on a same-day re-run, INSERTING a second,
    # 0-day-stride duplicate row per ticker instead of upserting the same
    # one — inflating n_matured with fully-redundant observations.
    made_at = datetime.combine(now_et.date(), datetime.min.time(), tzinfo=now_et.tzinfo).isoformat()
    rows: list[dict] = []
    returns_by_ticker: dict[str, "pd.Series"] = {}

    for t, bundle in held_data.items():
        if not isinstance(bundle, dict):
            continue
        df = bundle.get("df")
        if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
            continue
        closes = df["Close"].dropna()
        rets = closes.pct_change().dropna()
        if rets.empty:
            continue
        returns_by_ticker[t] = rets
        forecast = forecast_vol_ewma(rets, lam=VOL_FORECAST_EWMA_LAMBDA)
        baseline = realized_vol(rets.tail(VOL_FORECAST_HORIZON_DAYS))
        if forecast is None or baseline is None:
            continue
        rows.append({
            "model_name":      "vol_forecast_ewma",
            "model_version":   "v1",
            "scope":           "ticker",
            "ticker":          t,
            "made_at":         made_at,
            "horizon_days":    VOL_FORECAST_HORIZON_DAYS,
            "target_metric":   "realized_vol_20d_annualized",
            "predicted_value": forecast,
            "baseline_value":  baseline,
            "regime_at_make":  regime_tag,
            "source":          "live",
        })

    # Portfolio aggregate — a weighted return series built from the SAME bars
    # above, weights from this run's own snapshot_rows (shares x close_price at
    # this EOD's build), never a re-fetch or a re-derivation of holdings.
    snapshot_rows = payload.get("snapshot_rows", [])
    market_values: dict[str, float] = {}
    for r in snapshot_rows:
        t = str(r.get("ticker") or "").upper()
        mv = (r.get("shares") or 0) * (r.get("close_price") or 0)
        if t and mv and mv > 0:
            market_values[t] = mv
    total_mv = sum(market_values.values())
    usable = [t for t in market_values if t in returns_by_ticker]
    if total_mv > 0 and usable:
        aligned = pd.DataFrame({t: returns_by_ticker[t] for t in usable}).dropna()
        if not aligned.empty:
            weights = pd.Series({t: market_values[t] for t in aligned.columns}, dtype=float)
            weights = weights / weights.sum()
            port_rets = (aligned * weights).sum(axis=1)
            p_forecast = forecast_vol_ewma(port_rets, lam=VOL_FORECAST_EWMA_LAMBDA)
            p_baseline = realized_vol(port_rets.tail(VOL_FORECAST_HORIZON_DAYS))
            if p_forecast is not None and p_baseline is not None:
                rows.append({
                    "model_name":      "vol_forecast_ewma",
                    "model_version":   "v1",
                    "scope":           "portfolio",
                    "ticker":          "PORTFOLIO",
                    "made_at":         made_at,
                    "horizon_days":    VOL_FORECAST_HORIZON_DAYS,
                    "target_metric":   "realized_vol_20d_annualized",
                    "predicted_value": p_forecast,
                    "baseline_value":  p_baseline,
                    "regime_at_make":  regime_tag,
                    "source":          "live",
                })

    if not rows:
        return 0
    return len(rows) if db.save_model_predictions_batch(rows) else 0


def _trading_days_elapsed(start_date, end_date) -> int:
    """Count ET trading sessions strictly AFTER `start_date` up to and
    including `end_date` — used to decide whether a model_predictions row's
    `horizon_days` (trading days) has fully elapsed. A simple day-by-day walk
    is deliberate here (never a large range in practice — this table gains
    one row per held ticker + portfolio per day, so a pending row is at most
    ~horizon_days+few trading days old before this catches it)."""
    n = 0
    d = start_date
    while d < end_date:
        d = d + timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


def _mature_vol_predictions(now_et) -> int:
    """Find `model_predictions` rows whose horizon has fully elapsed
    (trading-day aware) and write their realized outcome. Fetches a fresh,
    targeted price history per maturing ticker — deliberately NOT this run's
    held-tickers bar context, since a maturing prediction's ticker may no
    longer be held. PORTFOLIO-scope rows are left unmatured for now (this
    cron does not reconstruct historical portfolio weights; same scoping
    limit as the backfill script — design doc §1.6b). Never raises; the
    caller wraps this call in its own try/except for defense in depth."""
    import pandas as pd

    from stock_analyzer import data as _data
    from stock_analyzer.vol_forecast import realized_vol

    pending = db.load_unmatured_model_predictions(model_name="vol_forecast_ewma")
    if pending is None or pending.empty:
        return 0

    today = now_et.date()
    updates: list[dict] = []

    for _, row in pending.iterrows():
        ticker = str(row.get("ticker") or "").upper()
        if row.get("scope") == "portfolio" or ticker == "PORTFOLIO":
            continue
        try:
            made_at_raw = str(row.get("made_at"))
            made_date = datetime.fromisoformat(made_at_raw.replace("Z", "+00:00")).date()
        except Exception:
            continue
        horizon = row.get("horizon_days")
        if horizon is None:
            continue
        try:
            horizon_int = int(horizon)
        except (TypeError, ValueError):
            continue
        if _trading_days_elapsed(made_date, today) < horizon_int:
            continue  # not due yet

        try:
            hist = _data.fetch_price_history(ticker, period="3mo")
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            closes = hist["Close"].dropna()
            # pct_change() on the FULL series first, then filter to the
            # forward window — filtering closes to (made_date, today] before
            # computing pct_change would drop the made_date -> made_date+1
            # return (no predecessor left inside the filtered slice), silently
            # scoring one fewer trading day than the stated horizon. Mirrors
            # the same full-series-then-slice order _write_live_vol_predictions
            # already uses for its baseline.
            rets_full = closes.pct_change().dropna()
            idx_dates = pd.to_datetime(rets_full.index).date
            # Cap the upper edge to exactly `horizon_int` forward returns —
            # NOT to `today`. If a cron slot gets skipped (this project has
            # a documented multi-week cron-outage precedent), maturation
            # runs late and an uncapped (made_date, today] window would
            # silently score realized vol over MORE than the stated horizon,
            # making late-matured live rows non-comparable to the backfill
            # script's always-exactly-`horizon`-day rows. `rets_full` is
            # chronological-ascending, so `.head(horizon_int)` on the
            # forward-filtered slice = the earliest `horizon_int` returns
            # after made_date — identical to the old behaviour when
            # maturation runs on time, protective only when it's late.
            forward = rets_full[idx_dates > made_date].head(horizon_int)
            if len(forward) < horizon_int:
                continue  # window not fully realized yet (or fetch too short) — retry later
            realized = realized_vol(forward)
        except Exception:
            realized = None
        if realized is None:
            continue

        predicted = row.get("predicted_value")
        baseline = row.get("baseline_value")
        abs_error = abs(float(predicted) - realized) if predicted is not None else None
        baseline_abs_error = abs(float(baseline) - realized) if baseline is not None else None
        updates.append({
            "id":                 row.get("id"),
            "realized_value":     realized,
            "scored_at":          now_et.isoformat(),
            "abs_error":          abs_error,
            "baseline_abs_error": baseline_abs_error,
        })

    if not updates:
        return 0
    return len(updates) if db.mature_model_predictions_batch(updates) else 0


def _daily_action_fingerprint(top_pick: dict, exit_alerts: list[dict]) -> str:
    """Stable hash of the morning action brief: top ticker+score + any EXIT/TRIM
    set. Re-fires when the top pick changes or exit signals change; silent when
    only secondary picks shuffle."""
    top_t = str(top_pick.get("ticker") or "").upper()
    top_c = round(float(top_pick.get("composite_score") or 0), 1)
    exits = sorted(
        f"{str(a.get('ticker') or '').upper()}:{a.get('signal_type', '')}"
        for a in exit_alerts if a.get("ticker")
    )
    key = f"{top_t}:{top_c}|" + "|".join(exits)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


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

    # ── Recommendation logging (Self Track Record — cron coverage fix) ─────────
    # The interactive app only logs a "new_pick" row when a real Streamlit
    # session visits Home and builds Grow Today (app.py ~line 4763). On a day
    # nobody opens the app, this headless scan is the ONLY place a New
    # Positions to Initiate pick gets surfaced at all — so without this,
    # `recommendations` silently under-covers those days and every BUY that
    # matches one of today's picks would wrongly read as "no rec on file"
    # (see SELF_TRACK_RELIABLE_LOG_START). Only "new_pick" is logged — the
    # cron has no held-portfolio context to compute add_winner rows, and no
    # buy_candidate list at all. save_recommendations is an idempotent upsert
    # (on_conflict="ticker,rec_date,rec_type", ignore_duplicates=True), so a
    # same-day interactive re-write later can't double-count. Wrapped so a DB
    # hiccup here can NEVER abort the scan or block the higher-priority
    # buy-list email built below.
    try:
        _rec_rows = _build_new_pick_rows(picks, now_et.date())
        if _rec_rows:
            _rec_save_result = db.save_recommendations(_rec_rows)
            _log(f"rec log: saved={_rec_save_result.get('saved', 0)}/"
                 f"{_rec_save_result.get('attempted', 0)} new_pick row(s)"
                 + (f" — {_rec_save_result.get('error')}" if _rec_save_result.get("error") else ""))
        else:
            _log("rec log: no new_pick rows to save today.")
    except Exception as _rec_log_err:
        _log(f"rec log: FAILED — {str(_rec_log_err)[:120]} — continuing (email unaffected).")

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
        # Sort by composite score so the highest-conviction pick leads.
        hi.sort(key=lambda p: float(p.get("composite_score") or 0), reverse=True)
        top_pick   = hi[0]
        other_picks = hi[1:]

        # Pull today's EXIT/TRIM signals from the premarket run so the email can
        # warn the user to handle exits before entering any new position.
        exit_alerts: list[dict] = []
        try:
            signals_df = db.load_exit_signals(days_back=1)
            if signals_df is not None and not signals_df.empty and "signal_date" in signals_df.columns:
                _today_rows = signals_df[
                    (signals_df["signal_date"].astype(str) == today_str) &
                    (signals_df["signal_type"].isin(["EXIT", "TRIM"]))
                ]
                exit_alerts = _today_rows.to_dict("records")
        except Exception as _e:
            _log(f"exit_signals load for scan email failed: {str(_e)[:80]} — skipping exit section.")

        if exit_alerts:
            _log(f"exit alerts in email: " + ", ".join(
                f"{a.get('ticker')}/{a.get('signal_type')}" for a in exit_alerts
            ))

        fp = _daily_action_fingerprint(top_pick, exit_alerts)
        state = db.load_alert_state(_BUY_ROW) or {}
        if (state.get("last_emailed_date") == today_str
                and state.get("last_fingerprint") == fp and not force):
            _log(f"morning action unchanged since last send (fp={fp}) — no email.")
        else:
            subject, html = render_daily_action_email(
                top_pick=top_pick,
                exit_alerts=exit_alerts,
                other_picks=other_picks,
                built_at=payload.get("built_at", today_str),
            )
            sent = _send_email("morning-action", subject, html)
            # Save dedup state ONLY on a real send — so a transient Resend failure
            # (key present, send errored) is retried by the later DST slot rather
            # than silently suppressed. (Inert/no-key also won't save → harmless.)
            if sent and db.save_alert_state(today_str, fp, _BUY_ROW):
                _log(f"action state saved (row={_BUY_ROW}, date={today_str}, fp={fp}).")
            elif not sent:
                _log("action email not sent (inert/failed) — state NOT saved (later slot may retry).")
    _log(f"scan done · persisted={n} · buy_sent={sent}")
    return 0


def _run_intraday(now_et, force: bool) -> int:
    """Mid-morning intraday pullback window (~11:30 ET): fire when a morning
    go-verdict pick has pulled back from its open by PULLBACK_ENTRY_DIP_PCT
    while SPY is NOT in freefall (SPY intraday drop ≤ PULLBACK_SPY_MAX_DOWN).

    Requires scanner_cache to have been written by the earlier _run_scan() step.
    Deduplicates via _INTRADAY_ROW (row 4) so each qualifying name fires at most
    once per day. Inert without RESEND_API_KEY."""
    today_str = now_et.date().isoformat()
    if not force:
        if not is_trading_day(now_et.date()):
            _log("intraday: not an ET trading day — skip.")
            return 0
        # Only run after opening volatility has settled (≥ 10:00 ET).
        if now_et.hour < 10:
            _log(f"intraday: too early (ET hour {now_et.hour} < 10) — skip.")
            return 0

    # Load the scanner_cache written by this morning's _run_scan step.
    cache = db.load_scanner_cache()
    if cache is None:
        _log("intraday: no scanner_cache available — run scan first.")
        return 0
    scanner_df = cache.get("df")
    scan_date  = cache.get("scan_date")
    if scanner_df is None or (hasattr(scanner_df, "empty") and scanner_df.empty):
        _log("intraday: scanner_cache is empty — skip.")
        return 0
    # Sanity: only use today's cache (stale data from yesterday → wrong picks).
    # None scan_date means the row was written by an older code path with no date —
    # treat as stale rather than assume it's fresh.
    if (scan_date is None or scan_date != today_str) and not force:
        _log(f"intraday: scanner_cache is from {scan_date!r}, not today ({today_str}) — skip.")
        return 0

    # Re-derive go-verdict picks from the cached scan (mirrors what _run_scan does).
    payload = compute_morning_picks(today=now_et.date(), scanner_results=scanner_df)
    for e in payload.get("errors", []):
        _log(f"engine note: {e}")
    picks = payload.get("picks", [])
    go_picks = [
        p for p in picks
        if ((p.get("xref") or {}).get("verdict_reconciled") or {}).get("verdict") == "go"
        and p.get("composite_score") is not None
        and str(p.get("ticker") or "").strip()
    ]
    if not go_picks:
        _log("intraday: no go-verdict picks in today's scan — skip.")
        return 0
    _log(f"intraday: {len(go_picks)} go-verdict pick(s) to check: "
         + ", ".join(str(p.get("ticker")) for p in go_picks))

    # Fetch live intraday prices for the qualifying tickers + SPY.
    from stock_analyzer.intraday_entry import fetch_intraday_prices, compute_intraday_entries
    from stock_analyzer.constants import PULLBACK_ENTRY_DIP_PCT, PULLBACK_SPY_MAX_DOWN

    pick_tickers = [str(p.get("ticker")).upper() for p in go_picks]
    all_tickers  = pick_tickers + ["SPY"]
    price_data   = fetch_intraday_prices(all_tickers)
    spy_data     = price_data.pop("SPY", None)
    _log(f"intraday: prices fetched for {len(price_data)}/{len(pick_tickers)} pick(s)"
         + (f" · SPY: cur={spy_data.get('current'):.2f} open={spy_data.get('open'):.2f}"
            if spy_data else " · SPY: unavailable"))

    # Check qualifying pullback entries.
    entries = compute_intraday_entries(go_picks, price_data, spy_data,
                                       PULLBACK_ENTRY_DIP_PCT, PULLBACK_SPY_MAX_DOWN)
    sent = False
    if not entries:
        _log("intraday: no tickers qualify (none pulled back enough, or SPY in freefall).")
    else:
        _log("intraday pullback entries: "
             + ", ".join(f"{e.get('ticker')} ({e.get('intraday_drop_pct'):+.1f}%)" for e in entries))
        # Dedup: fingerprint on (ticker, date) pairs so the same entry set doesn't
        # re-fire when the cron re-runs (DST dual-slot or FORCE re-test).
        _fp_keys = sorted(f"{str(e.get('ticker')).upper()}:{today_str}" for e in entries)
        fp = hashlib.sha1("|".join(_fp_keys).encode("utf-8")).hexdigest()[:16]
        state = db.load_alert_state(_INTRADAY_ROW) or {}
        if (state.get("last_emailed_date") == today_str
                and state.get("last_fingerprint") == fp and not force):
            _log(f"intraday: same entries already sent today (fp={fp}) — skip.")
        else:
            spy_drop_pct = None
            if spy_data:
                _sc = spy_data.get("current")
                _so = spy_data.get("open")
                if _sc and _so and _so > 0:
                    spy_drop_pct = round((_sc - _so) / _so * 100, 2)
            subject, html = render_intraday_entry_email(
                entries=entries,
                spy_drop=spy_drop_pct,
                built_at=today_str,
            )
            sent = _send_email("intraday-entry", subject, html)
            if sent and db.save_alert_state(today_str, fp, _INTRADAY_ROW):
                _log(f"intraday state saved (row={_INTRADAY_ROW}, date={today_str}, fp={fp}).")
            elif not sent:
                _log("intraday email not sent (inert/failed) — state NOT saved (later slot may retry).")
    _log(f"intraday done · entries={len(entries)} · sent={sent}")
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
        # Bundle evidence via the shared extractor (same path as the app's
        # on-demand review) — reads financials/df/headlines, not indicators/
        # revenue_growth/news, so the weekly review actually sees current data.
        ev = _ta.bundle_evidence(held_data.get(ticker, {}))
        # Enrich with newest saved analyst coverage (if any)
        _cov_cron = db.load_analyst_coverage(ticker=ticker, limit=1)
        _ac_cons_cron: dict = {}
        if not _cov_cron.empty:
            _acr_cron = _cov_cron.iloc[0]
            def _pj_cron(v):    # defensive jsonb parse
                import json as _cj
                if isinstance(v, str):
                    try:
                        return _cj.loads(v)
                    except Exception:
                        return []
                return v or []
            _ac_cons_cron = {
                "consensus_rating": _acr_cron.get("consensus_rating"),
                "avg_pt":           _acr_cron.get("avg_pt"),
                "n_firms":          len(_pj_cron(_acr_cron.get("analysts"))),
                "as_of":            str(_acr_cron.get("article_date")),
                "thesis":           _pj_cron(_acr_cron.get("thesis")),
            }
        positions.append({
            "ticker":      ticker,
            "trade_date":  str(row.get("traded_at", ""))[:10],
            "user_thesis": str(row["user_thesis"]),
            "inputs":      _ta.build_review_inputs(
                technical=ev["technical"],
                fundamentals=ev["fundamentals"],
                news_headlines=ev["news_headlines"],
                analyst_consensus=_ac_cons_cron,
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
    # Full recommendation history for behavioral fingerprint patterns (all-time).
    all_recs_df = db.load_recommendations()

    # Fetch SPY return for the week
    spy_week_pct = None
    try:
        import yfinance as yf
        spy = yf.download("SPY", start=str(week_start), end=str(week_ending), progress=False, auto_adjust=True)
        if not spy.empty and len(spy) >= 2:
            _close = spy["Close"]
            if hasattr(_close, "columns"):   # multi-ticker download yields a DataFrame
                _close = _close.iloc[:, 0]
            _c0 = float(_close.iloc[0])
            _c1 = float(_close.iloc[-1])
            spy_week_pct = round((_c1 - _c0) / _c0 * 100, 2)
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
        all_recs_df   = all_recs_df,
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
        html    = _notify.render_debrief_email(result, week_had_trades=package.get("week_had_trades", False))
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
    # Derive mode from ET hour; named overrides bypass time-inference.
    _mode_override = os.environ.get("ALERT_RUN_MODE", "").strip().lower()
    mode = _mode_override if _mode_override in ("scan", "intraday", "thesis", "debrief", "monthly") else (
        "eod" if now_et.hour >= 12 else "premarket"
    )
    _log(f"start · {now_et.isoformat()} ET · mode={mode} · force={force} · test_email={test_email}")

    if test_email:
        return _run_test_email(now_et)
    if mode == "thesis":
        # Sunday lane: thesis review → weekly debrief → (first Sunday only) monthly
        # report. Isolate each lane so one's uncaught exception can't take down the
        # others (the monthly report, last in line, is the most valuable of the three).
        # A non-zero aggregate exit lets Actions mark the run failed → feeds the
        # failure notification (dead-man's-switch).
        rc = 0
        for _job, _fn in (("thesis", _run_thesis), ("debrief", _run_debrief),
                          ("monthly", _run_monthly_report)):
            try:
                _fn(now_et, force)
            except Exception as exc:
                _log(f"{_job}: UNCAUGHT — {str(exc)[:160]}")
                rc = 1
        return rc

    # Every other mode dispatches exactly one job per invocation. Wrap it in
    # the same log-then-fail discipline as the thesis lane above (2026-08-04
    # audit finding: these used to call their _run_X function unguarded — a
    # crash still fails the GitHub Actions run either way, but bypassed this
    # module's own _log() so the failure reason never made it into the
    # run log / dedup state, only a raw traceback in Actions' own output).
    _job_name, _job_fn = {
        "scan":     ("scan",     _run_scan),
        "intraday": ("intraday", _run_intraday),
        "debrief":  ("debrief",  _run_debrief),
        "monthly":  ("monthly",  _run_monthly_report),
        "eod":      ("eod",      _run_eod),
    }.get(mode, ("premarket", _run_premarket))
    try:
        return _job_fn(now_et, force)
    except Exception as exc:
        _log(f"{_job_name}: UNCAUGHT — {str(exc)[:160]}")
        raise


if __name__ == "__main__":
    sys.exit(main())
