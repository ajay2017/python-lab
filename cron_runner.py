#!/usr/bin/env python3
"""
Headless alert cron entry point (exit-discipline Phase 3 + pullback Phase 2 +
Today's-P&L EOD snapshot). Scheduled via 7 dedicated Railway native Cron Job
services (project `endearing-magic`: cron-premarket/cron-scan/cron-intraday/
cron-eod/cron-thesis/cron-maintenance/cron-broker), migrated off GitHub
Actions on 2026-08-07 after a platform-wide GitHub Actions incident
(2026-08-06) exposed the `schedule` trigger's documented best-effort delivery
(delayed/dropped runs under load). .github/workflows/alerts.yml is now
workflow_dispatch-only — manual re-runs / ad hoc testing only, no longer the
scheduled entry point. See memory `project_cron_railway_migration` for the
full history and per-service config.

SEVEN modes (one per ET trading day/week each):
  • premarket (~08:00 ET) — recompute PROTECTIVE signals (stop / EXIT / risk-off)
    and email only when the action set changed. (exit-discipline Phase 3)
  • scan (~09:45 ET, post-open) — run the sector scanner headlessly and persist
    the result to scanner_cache so the Home buy-candidate / Grow-Today new-pick
    lists populate on a COLD load without the user running the ~20s scanner.
  • intraday (~11:30 ET) — mid-morning pullback entry-window check.
  • eod (~16:30 ET, post-close) — (a) write today's daily_snapshot so the
    Today's-P&L baseline is deterministic even on unviewed days; (b) a REACTIVE
    pullback email if the market actually fell ≥ threshold today. (pullback P2 +
    Today's-P&L EOD)
  • thesis (~18:00 ET Sunday) — AI thesis reviews for all open positions that
    have a user_thesis written at BUY entry (one LLM call per position, saves
    to thesis_reviews table; inert without ANTHROPIC_API_KEY), followed by the
    weekly debrief and (first Sunday of the month only) the monthly report.
  • maintenance (Saturday) — idempotent reference-data backfills (analyst
    anchor prices, vol-forecast history) plus the ticker-liveness/reference-
    shelf sweep. Never touches a gate or the composite score.
  • broker (any cadence the Railway service is set to) — SnapTrade (Robinhood)
    balance sync + transaction import; dormant until the user completes the
    one-time connect flow. Position drift is computed live in the app, not
    here. (docs/plans/snaptrade-broker-integration.md)

Mode = $ALERT_RUN_MODE if it's one of scan|intraday|thesis|debrief|monthly|
maintenance|broker — set directly as a fixed per-service variable on those
Railway services (or via the workflow_dispatch mode input on a manual GitHub
run) — else derived from the ET hour (≥12:00 ET ⇒ eod, else premarket);
premarket/eod are never taken as a direct override value, only ever
hour-derived, so those two Railway services set no ALERT_RUN_MODE at all.
All output → stdout (the Railway/Actions deploy log). Ships INERT: no
RESEND_API_KEY ⇒ compute + log, send nothing.

EXIT CODES (updated 2026-08-16). Returns 1, not 0, when:
  • a Sunday thesis-lane sub-job raises (thesis/debrief/monthly), or
  • ANY lane finds Supabase unreadable — see _handle_db_unavailable.
The second case is why every lane can now exit non-zero. Previously a DB
outage made every lane log one line, return 0 and record a HEALTHY
heartbeat, so an outage was indistinguishable from "nothing to report" —
including on the pre-market protective lane, whose whole job is to tell
you about stop breaches and EXIT signals. A non-zero exit makes Railway's
own run list say so too, and feeds main()'s status="failed" heartbeat.

Env: SUPABASE_URL/SUPABASE_KEY (service-role) · FINNHUB_API_KEY/FMP_API_KEY/
FRED_API_KEY (optional providers) · RESEND_API_KEY/ALERT_EMAIL_TO/ALERT_EMAIL_FROM
· ALERT_RUN_MODE (scan|intraday|thesis|debrief|monthly|maintenance|broker) ·
ALERT_FORCE=1 (bypass guards) · ALERT_TEST_EMAIL=1 (synthetic delivery test) ·
ALERT_PROTECTIVE_ROW=1 / EOD lane uses row 2 in alert_state. The broker lane
additionally needs SNAPTRADE_CLIENT_ID/SNAPTRADE_CONSUMER_KEY — a Personal
SnapTrade API key (not Commercial), which has no separate per-user
credential — see stock_analyzer/snaptrade_client.py.
"""

import hashlib
import os
import sys
from datetime import datetime, timedelta

import pytz

from stock_analyzer import broker_sync
from stock_analyzer import db
from stock_analyzer import snaptrade_client
from stock_analyzer.constants import (
    ALERT_EMAIL_HOUR_ET, ALERT_EOD_HOUR_ET, SNAPTRADE_SYNC_MAX_TXN_LOOKBACK_DAYS,
)
from stock_analyzer.data import is_trading_day
from stock_analyzer.headless_alert_engine import (
    compute_protective_alerts, compute_eod, compute_morning_picks,
)
from stock_analyzer.notify import (
    render_alert_email, render_test_email, render_pullback_email,
    render_daily_action_email, render_intraday_entry_email, send_email_resend,
    render_db_outage_email, render_liveness_email,
)

_ET = pytz.timezone("America/New_York")
_PROTECTIVE_ROW = 1   # alert_state lane: pre-market protective dedup
_EOD_ROW = 2          # alert_state lane: EOD pullback dedup
_BUY_ROW = 3          # alert_state lane: morning buy-list dedup
_INTRADAY_ROW = 4     # alert_state lane: intraday pullback entry dedup
_DB_OUTAGE_ROW = 5    # alert_state lane: DB-unreachable notice dedup (self-creates on upsert)
_BROKER_FAILURE_ROW = 6  # alert_state lane: broker-lane failure-email dedup (self-creates on upsert)

# Plain-language lane names + what a DB outage actually cost, for the outage
# email. Kept here rather than in each lane so the wording can't drift.
_LANE_OUTAGE_TEXT: dict[str, tuple[str, str]] = {
    "premarket":   ("pre-market protective scan",
                    "Stop breaches, deterioration EXIT signals and risk-off trims were NOT evaluated today."),
    "eod":         ("end-of-day snapshot",
                    "Today's closing snapshot was NOT saved, and the pullback check did NOT run."),
    "scan":        ("morning market scan",
                    "The buy-candidate scan did NOT run — no picks were evaluated."),
    "intraday":    ("intraday pullback check",
                    "The intraday entry check did NOT run."),
    "thesis":      ("weekly thesis review",
                    "The weekly thesis review did NOT run."),
    "debrief":     ("weekly debrief",
                    "The weekly debrief was NOT produced."),
    "monthly":     ("monthly intelligence report",
                    "The monthly report was NOT produced."),
    "maintenance": ("weekly data backfills",
                    "The weekly reference-data backfills did NOT run."),
    "broker":      ("SnapTrade broker sync",
                    "The Robinhood balance/transaction sync did NOT run."),
}


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
        _row = {
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
        }
        # Sizing capture (F-249 Phase 2) — unlike the pillar scores above, this
        # IS available on the cron path: compute_morning_picks runs the same
        # _grow_today, so each pick already carries the sizing dict the cards
        # and emails render from. Absent dict => columns stay NULL. Built into
        # the row before appending rather than mutating rows[-1], so a future
        # `continue` landing between the two cannot silently drop the capture.
        # NOTE: this lane does NOT usually win the day. Measured 2026-08-23:
        # the owner opens the app around 09:26-09:32 ET while this lane's own
        # scanner_cache row is stamped 10:46 ET, and the upsert ignores
        # duplicates — so the interactive session is normally the first writer
        # and these rows are the FALLBACK for tickers nobody looked at first.
        _s = p.get("sizing")
        if isinstance(_s, dict) and _s:
            _row.update({
                "rec_shares":          _s.get("shares"),
                "rec_stop":            _s.get("stop"),
                "rec_portfolio_value": _s.get("portfolio_value"),
                "rec_sizing_version":  _s.get("sizing_version"),
            })
        rows.append(_row)
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


def _notify_failure(mode: str, detail: str) -> None:
    """In-script dead-man's-switch — GitHub Actions had a workflow-level
    `if: failure()` step for this; Railway's native Cron Jobs have no
    equivalent, so a lane that raises or returns non-zero would otherwise
    fail completely silently (see memory project_cron_railway_migration).
    Reuses the same Resend path as every other email here — INERT without
    RESEND_API_KEY, and this function itself must NEVER raise, since it's
    always called from inside an except block right before the original
    failure is re-raised; a second exception here would clobber that
    traceback instead of just failing to notify."""
    try:
        subject = f"⚠️ DRISHTA cron FAILED — {mode}"
        html = (
            f"<p>The <b>{mode}</b> cron lane failed.</p>"
            f"<p><b>Error:</b> {detail or '(no detail captured)'}</p>"
            f"<p>Check the <code>cron-{mode}</code> Railway service's Deploy Logs "
            f"for the full traceback.</p>"
        )
        _send_email("cron-failure", subject, html)
    except Exception as exc:
        _log(f"cron-failure notify: UNCAUGHT — {str(exc)[:160]} (original failure still propagates)")


def _notify_broker_failure(now_et, detail: str) -> None:
    """Dedup wrapper around `_notify_failure("broker", ...)` — at most one
    failure email per day, even if the `broker` lane is scheduled to run
    more than once daily. Added 2026-08-21 as the prerequisite this lane's
    own comments called out before increasing cron frequency past daily:
    without it, an ongoing SnapTrade outage would send one email per
    scheduled run instead of one per day. Mirrors `_handle_db_unavailable`'s
    dedup shape but keyed to its own alert_state row (`_BROKER_FAILURE_ROW`)
    since this covers SnapTrade-side failures, not DB outages (those already
    route through `_handle_db_unavailable`/`_DB_OUTAGE_ROW`). Fails OPEN on
    any dedup-read/write error — a duplicate email is far cheaper than a
    silently swallowed failure. Never raises."""
    today_str = now_et.date().isoformat()
    try:
        state = db.load_alert_state(_BROKER_FAILURE_ROW)
        # NB: deliberately NOT `... or {}` — an offline None must mean "no
        # dedup available, send", not "nothing sent today".
        if state is not None and state.get("last_emailed_date") == today_str:
            _log("broker: failure email already sent today — skip (dedup)")
            return
    except Exception as exc:
        _log(f"broker: failure dedup read failed ({str(exc)[:80]}) — sending anyway")

    try:
        _notify_failure("broker", detail)
    except Exception as exc:
        # _notify_failure's own contract is "never raises" (it wraps its body
        # in try/except) — this is belt-and-suspenders so a future change to
        # it can't turn this dedup wrapper into a new way for the broker lane
        # to blow up.
        _log(f"broker: failure notify UNCAUGHT — {str(exc)[:120]}")

    try:
        db.save_alert_state(
            emailed_date=today_str, fingerprint="broker", row_id=_BROKER_FAILURE_ROW,
        )
    except Exception:
        pass   # dedup is best-effort; the email already went out


def _record_heartbeat(lane: str, now_et, status: str = "ok", detail: str | None = None) -> None:
    """Write one cron_heartbeat row (System Proprioception Phase 1) so the
    owner-only 🩺 System Trust page can prove each Railway lane is firing.
    OBSERVABILITY ONLY — nothing reads this for a decision. Called at the END
    of every lane invocation (including trading-day-skip no-ops — a skip is
    still proof the scheduler fired the service). Like _notify_failure, this
    must NEVER raise: a heartbeat write failure can't be allowed to affect the
    lane's real work or its exit code. Inert (returns False) until the
    cron_heartbeat table's one-time DDL is applied — see stock_analyzer/db.py."""
    try:
        ok = db.save_cron_heartbeat(lane, status=status, detail=detail, ran_at=now_et.isoformat())
        _log(f"heartbeat {lane}={status}"
             + ("" if ok else " (NOT saved — DB offline / table not created)"))
    except Exception as exc:
        _log(f"heartbeat {lane}: UNCAUGHT — {str(exc)[:120]} (ignored — lane unaffected)")


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
    # A DB outage must NOT read as "no protective actions today" — that is the
    # silent success this branch exists to remove. Checked BEFORE the alert
    # count is logged, so the log can't claim a clean scan either.
    if payload.get("reason") == "db_unavailable":
        return _handle_db_unavailable(
            "premarket", now_et,
            "; ".join(payload.get("errors", [])) or "holdings unreadable")
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
    if payload.get("reason") == "db_unavailable":
        return _handle_db_unavailable(
            "eod", now_et,
            "; ".join(payload.get("errors", [])) or "holdings unreadable")
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
    Exits 0 except when Supabase is unreadable (see _handle_db_unavailable)."""
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
    # A DB outage returns an empty picks list — the tone-gate explainer below would
    # attribute that to market conditions, which is confidently wrong under an outage.
    # Checked before the error-log loop so no misleading engine notes print either.
    if payload.get("reason") == "db_unavailable":
        return _handle_db_unavailable(
            "scan", now_et,
            "; ".join(payload.get("errors", [])) or "holdings unreadable")
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
        # load_scanner_cache() returns None for BOTH "DB offline" and "no scan run yet" —
        # probe before logging so we don't misattribute an outage to a missing scan.
        _detail = _db_unavailable_detail()
        if _detail:
            return _handle_db_unavailable("intraday", now_et, _detail)
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
        # _build_context sets reason="db_unavailable" when holdings can't be read —
        # treat that as an outage (email owner) rather than a silent skip.
        if ctx.get("reason") == "db_unavailable":
            return _handle_db_unavailable(
                "thesis", now_et,
                "; ".join(ctx.get("errors", [])) or "holdings unreadable")
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
        # days_available == 0 is indistinguishable from a DB outage without probing —
        # a genuine early-run has at least some snapshots; zero means nothing came back.
        # A partial shortfall (1–4 days) is a data-accumulation issue, not an outage.
        if days_available == 0:
            _detail = _db_unavailable_detail()
            if _detail:
                return _handle_db_unavailable("debrief", now_et, _detail)
        _log(f"debrief: only {days_available} snapshot day(s) available — need 5. "
             f"Earliest full debrief after {week_start + __import__('datetime').timedelta(days=5 - days_available)}.")
        return 0

    # Load recommendations and trades for the week
    recs_df   = db.load_recommendations(start_date=week_start, end_date=week_ending)
    trades_df = db.load_trades()
    # Full recommendation history for behavioral fingerprint patterns (all-time).
    all_recs_df = db.load_recommendations()
    # Protective (WATCH/TRIM/EXIT) signals for the week — the symmetric
    # counterpart to recs_df above. days_back=10 comfortably covers the
    # 7-day window; build_debrief_package does the exact date filtering.
    exit_signals_df = db.load_exit_signals(days_back=10)

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
        exit_signals_df = exit_signals_df,
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

    # Prior week's debrief, for the email's week-over-week alpha trend — loaded
    # BEFORE this week's save below, so it can't accidentally read back the row
    # this run is about to write. Deterministic delta computed in notify.py, not
    # narrated by the LLM (arithmetic belongs in code, not in a text generation).
    prior_debrief = None
    try:
        _prior_df = db.load_weekly_debriefs(limit=1)
        if _prior_df is not None and not _prior_df.empty:
            _prior_row = _prior_df.iloc[0]
            if str(_prior_row.get("week_ending")) != str(result["week_ending"]):
                prior_debrief = _prior_row.to_dict()
    except Exception as _pe:
        _log(f"debrief: prior-week lookup failed — {str(_pe)[:80]} — trend line omitted.")

    saved = db.save_weekly_debrief(result)
    _log(f"debrief: saved={saved} · week_ending={result['week_ending']} · "
         f"perf={result.get('performance_pct')} · alpha={result.get('alpha_pct')}")

    # Email
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    email_to   = os.environ.get("ALERT_EMAIL_TO", "").strip()
    email_from = os.environ.get("ALERT_EMAIL_FROM", "").strip()
    if resend_key and email_to and email_from:
        html    = _notify.render_debrief_email(
            result, week_had_trades=package.get("week_had_trades", False), prior=prior_debrief,
        )
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
        # recs_df is None on a DB read failure as well as on genuinely zero recs —
        # probe so an outage doesn't silently masquerade as "nothing to report yet".
        _detail = _db_unavailable_detail()
        if _detail:
            return _handle_db_unavailable("monthly", now_et, _detail)
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


# Set by a lane that handles its own sub-job failures and signals them via a
# non-zero RETURN rather than by raising (currently only _run_maintenance).
# main() reads it so the heartbeat carries a reason, not a bare "failed".
# Lanes that raise are unaffected — main()'s except-branch records their detail
# directly from the exception.
_LAST_LANE_FAILURE_DETAIL: str | None = None


def _db_unavailable_detail() -> str | None:
    """Human-readable detail when Supabase can't be read AT ALL, else None.

    Body moved to `db.unavailable_detail()` on 2026-08-17 so the outage EMAIL
    (this lane) and the in-app outage BANNER explain the same fault in the same
    words — two independent wordings for one condition is how they drift apart.
    The probe rationale (re-read HOLDINGS, not a synthetic `select 1`) lives
    with the implementation there.

    Kept as a thin alias rather than inlined at the 8 call sites: those sites
    read `cr.db.*` under test patching, and the indirection keeps this lane's
    call graph legible. Called ONLY after a lane's DB-derived input already came
    back empty/None, so it adds no round trip to the healthy path. Never raises.
    """
    return db.unavailable_detail()


def _handle_db_unavailable(lane: str, now_et, detail: str) -> int:
    """Email the owner that `lane` could not reach the database, then fail the run.

    Exists because the alternative is the silent success this whole change
    removes: a lane that can't read the book logs one line, returns 0, and the
    heartbeat reports healthy — indistinguishable from "nothing to report".

    Dedup is honest about its own limits: you cannot dedup a DB-outage alert IN
    the DB. So it dedups exactly when the DB is well enough to dedup (a partial
    outage → one email per day) and FAILS OPEN when it isn't (a total outage →
    one email per lane invocation, max ~4 on a weekday). More email means a
    worse outage, which is self-explaining. A dedup read returning None means
    SEND — never skip.

    Never raises: a failure to notify must not mask the original fault.
    """
    global _LAST_LANE_FAILURE_DETAIL
    label, what_did_not_run = _LANE_OUTAGE_TEXT.get(
        lane, (lane, f"The {lane} lane did NOT run."))
    _LAST_LANE_FAILURE_DETAIL = f"db_unavailable: {detail}"[:300]
    _log(f"{lane}: DB UNAVAILABLE — {detail}")

    today_str = now_et.date().isoformat()
    already: set[str] = set()
    try:
        state = db.load_alert_state(_DB_OUTAGE_ROW)
        # NB: deliberately NOT `... or {}` — an offline None must mean "no dedup
        # available, send", not "nothing sent today".
        if state is not None and state.get("last_emailed_date") == today_str:
            already = {p for p in str(state.get("last_fingerprint") or "").split("|") if p}
            if lane in already:
                _log(f"{lane}: outage email already sent today — skip (dedup)")
                return 1
    except Exception as exc:
        _log(f"{lane}: outage dedup read failed ({str(exc)[:80]}) — sending anyway")

    try:
        subject, html = render_db_outage_email(
            lane=lane, lane_label=label, what_did_not_run=what_did_not_run,
            detail=detail, built_at=now_et.isoformat(),
        )
        _send_email(f"db-outage/{lane}", subject, html)
    except Exception as exc:
        _log(f"{lane}: outage email FAILED to send — {str(exc)[:120]}")

    try:
        db.save_alert_state(
            emailed_date=today_str,
            fingerprint="|".join(sorted(already | {lane})),
            row_id=_DB_OUTAGE_ROW,
        )
    except Exception:
        pass   # dedup is best-effort; the email already went out
    return 1


def _run_maintenance(now_et, force: bool) -> int:
    """Saturday housekeeping: idempotent data backfills that keep the ledgers
    complete without needing a human at a shell.

    Exists because the 2026-08-15 Railway cutover removed the only practical
    place to run one-off maintenance scripts. Railway's Console is NOT the
    app's environment (minimal PATH, no app deps, unset LD_LIBRARY_PATH →
    numpy fails on libz.so.1), and the Streamlit Cloud terminal these scripts
    were originally written for is now a dormant fallback. Rather than chase
    the console's quirks, the backfills became a lane the existing cron
    infrastructure picks up — the takeaway recorded in memory
    `project_railway_migration`.

    Runs Saturday to stay clear of the Sunday thesis lane's LLM work. Both
    jobs are isolated so one's failure can't suppress the other, matching the
    thesis lane's discipline. MEASUREMENT-ONLY: neither job feeds a gate, a
    recommendation, or the composite score — they fill in historical anchor
    values on rows that already exist or should exist.
    """
    global _LAST_LANE_FAILURE_DETAIL
    _LAST_LANE_FAILURE_DETAIL = None

    if not force and now_et.weekday() != 5:   # 5 = Saturday
        _log("maintenance: not Saturday — skip.")
        return 0

    rc = 0
    failures: list[str] = []

    # ⓪ ticker-liveness sweep — MUST remain before sub-jobs ① and ②.
    #
    # Ordering is load-bearing: ① can `return _handle_db_unavailable(...)` early
    # (see lines below at "if summary.get('offline')" and the DB-probe fallback).
    # Placing the sweep after those paths would let a Supabase outage silently
    # starve the roster-rot check — exactly the silent-failure class F-239 fixed.
    # This sweep needs no DB: it probes provider data sources directly.
    try:
        # Imported inside the function for the same patchability reason as ① — the
        # name resolves from the module attribute at call time, letting tests patch
        # `stock_analyzer.ticker_liveness.sweep` and
        # `stock_analyzer.reference_shelf.shelf_status` without binding at import.
        from stock_analyzer import ticker_liveness as _tl
        from stock_analyzer import reference_shelf as _rs

        _sweep = _tl.sweep()
        _shelf = _rs.shelf_status(today=now_et.date())

        _shelf_down = [r for r in _shelf if r.get("severity") == "down"]
        _shelf_warn = [r for r in _shelf
                       if r.get("severity") in ("warn", "unknown")]

        # Email only on a finding.  Dead ticker is a chore, not a lane failure;
        # do NOT append to `failures` — that sets rc=1 → _LAST_LANE_FAILURE_DETAIL
        # → the 🩺 System Trust heartbeat reads "failed", training the user to
        # ignore a red heartbeat.  The informational email is the correct signal.
        _send_liveness = (
            _sweep is None                                      # batch raised
            or (_sweep is not None
                and _sweep.get("status") == "inconclusive")    # provider degraded
            or (_sweep is not None
                and bool(_sweep.get("dead")))                  # confirmed dead name(s)
            or bool(_shelf_down)                               # expired table
        )

        if _send_liveness:
            # Shelf-warn rows (approaching-expiry) go in ONLY when we're already
            # emailing for another reason — not as a standalone weekly nag.
            _subj, _html = render_liveness_email(
                sweep=_sweep,
                shelf_down=_shelf_down,
                shelf_warn=_shelf_warn,
                built_at=now_et.isoformat(),
            )
            _send_email("liveness", _subj, _html)
            _log(
                f"maintenance: liveness email sent — "
                f"sweep={'None' if _sweep is None else _sweep.get('status')} "
                f"dead={len(_sweep.get('dead', [])) if _sweep else '?'} "
                f"shelf_down={len(_shelf_down)}"
            )
        else:
            _hp = _sweep.get("health_pct", 0.0) if _sweep else 0.0
            _log(
                f"maintenance: liveness clean — health={_hp:.1f}%, dead=0, "
                f"no shelf issues"
            )
    except Exception as exc:
        # An exception HERE means the check itself broke — that IS a lane failure.
        # It is distinct from "the check found something": a dead ticker is never
        # an exception, it is a result inside a healthy _sweep dict.
        _log(f"maintenance/liveness: UNCAUGHT — {str(exc)[:160]}")
        failures.append(f"liveness: {str(exc)[:160]}")
        rc = 1

    # ① analyst_coverage anchor prices — self-limiting (only NULL rows), so
    #    this costs one cheap query once the table is caught up.
    try:
        # Imported INSIDE the function on purpose: the name resolves from the
        # module attribute at call time, which is what lets the tests patch
        # `scripts.backfill_analyst_prices.run_backfill`. Hoisting this to the
        # top of the file would bind the function object at import time and
        # silently gut those tests — they would still pass, against unstubbed
        # code. Don't "tidy" it upward.
        from scripts.backfill_analyst_prices import run_backfill as _analyst_backfill
        summary = _analyst_backfill(log=lambda m: _log(f"maintenance/analyst: {m}"))
        # Both backfills hit the same Supabase DB — if the analyst backfill reports
        # offline, the vol backfill would fail identically; email and exit rather than
        # logging silently and blundering into the second sub-job with no DB.
        if summary.get("offline"):
            return _handle_db_unavailable(
                "maintenance", now_et, "no Supabase credentials — backfills skipped")
        # The `offline` flag alone is NOT enough: run_backfill sets it only when
        # has_db() is False, and has_db() checks that credentials EXIST, not
        # that Supabase is reachable. The dominant outage class (client raises /
        # RLS blocks / table unreadable) degrades load_analyst_coverage to an
        # empty frame, so it arrives here as a cheerful "nothing to backfill".
        # Probe when there was genuinely nothing to do — a healthy caught-up
        # table returns None from the probe, so this cannot false-positive.
        if not summary.get("updated") and not summary.get("pending"):
            _detail = _db_unavailable_detail()
            if _detail:
                return _handle_db_unavailable("maintenance", now_et, _detail)
        _log(f"maintenance: analyst anchor prices — {summary['updated']} updated, "
             f"{summary['skipped_count']} skipped")
    except Exception as exc:
        _log(f"maintenance/analyst: UNCAUGHT — {str(exc)[:160]}")
        failures.append(f"analyst_prices: {str(exc)[:160]}")
        rc = 1

    # ② model_predictions historical backfill — skip_existing=True so a
    #    recurring run only does real work for holdings added since the last
    #    one, instead of re-fetching 5y of history for every ticker weekly.
    try:
        # Imported inside the function for the same patchability reason as ①.
        from scripts.backfill_vol_predictions import run_backfill as _vol_backfill
        summary = _vol_backfill(skip_existing=True,
                                log=lambda m: _log(f"maintenance/vol: {m}"))
        _log(f"maintenance: vol backfill — {summary['rows']} row(s) written across "
             f"{summary['tickers']} held ticker(s), "
             f"{len(summary['already_done'])} already done")
    except Exception as exc:
        _log(f"maintenance/vol: UNCAUGHT — {str(exc)[:160]}")
        failures.append(f"vol_predictions: {str(exc)[:160]}")
        rc = 1

    if failures:
        # Surfaced two ways, deliberately: the email is the immediate
        # dead-man's-switch, and _LAST_LANE_FAILURE_DETAIL is what makes the
        # heartbeat read "failed" on 🩺 System Trust instead of a false "ok".
        # This lane returns non-zero rather than raising (so one sub-job's
        # failure can't suppress the other), which means main()'s except-branch
        # never sees it — without this the success path would record status="ok"
        # over a run where both backfills blew up.
        _LAST_LANE_FAILURE_DETAIL = "; ".join(failures)
        _notify_failure("maintenance", _LAST_LANE_FAILURE_DETAIL)
    return rc


def _run_broker(now_et, force: bool) -> int:
    """SnapTrade broker sync (Robinhood) — the 7th cron lane
    (docs/plans/snaptrade-broker-integration.md). Balance sync + transaction
    import only — position drift (capability 1 of the plan) is computed
    LIVE at app-render time from a fresh SnapTrade read, not cached here, so
    there is nothing for this lane to write for it.

    Dormant (returns 0, no email, no heartbeat failure) until the user
    completes the one-time SnapTrade connect flow — an unconfigured
    integration is a normal not-yet-set-up state, the same posture as "no
    RESEND_API_KEY" elsewhere in this file, not a lane failure.

    Isolated per sub-job like the maintenance lane so one's failure can't
    suppress the other. A genuine Supabase outage (as opposed to SnapTrade
    itself being unreachable) still routes through _handle_db_unavailable so
    it gets the same outage email/dedup as every other lane, rather than a
    generic "cron lane failed" message that would obscure which system is
    actually down.
    """
    global _LAST_LANE_FAILURE_DETAIL
    _LAST_LANE_FAILURE_DETAIL = None

    if not snaptrade_client.has_snaptrade():
        _log("broker: SnapTrade not configured (credentials not set) — skip.")
        return 0

    accounts = snaptrade_client.list_accounts()
    if accounts is None:
        # Failure email is deduped to 1/day via _notify_broker_failure — see
        # its docstring; safe even if this lane is scheduled more than once
        # daily (2026-08-21, closes the gap the 2026-08-17 review flagged).
        _detail = "SnapTrade unreachable — could not list connected accounts"
        _LAST_LANE_FAILURE_DETAIL = _detail
        _log(f"broker: {_detail}")
        _notify_broker_failure(now_et, _detail)
        return 1
    if not accounts:
        _log("broker: SnapTrade connected but no brokerage accounts linked — nothing to sync.")
        return 0

    # Single-user app connecting one Robinhood account — sync the first;
    # log (don't silently ignore) if SnapTrade ever reports more than one.
    # Find the main brokerage account — the one with the most confirmed equity
    # positions. SnapTrade may link auxiliary accounts (credit card, crypto,
    # IRA, managed) alongside the main trading account; confirmed 2026-08-18
    # diagnostic: 5 accounts linked — Credit Card (0 pos), Crypto (0 pos),
    # IRA (0 pos), Managed (0 pos), Individual (15 pos). accounts[0] was the
    # credit card, so the original `accounts[0]` sync wrote credit-card cash
    # and ignored 84 credit-card POSDEBIT transactions instead of stock trades.
    #
    # Selection invariants (Opus reviewer, 2026-08-18):
    # - None (read failed / timeout) is NEVER treated as 0 (confirmed empty);
    #   collapsing them would silently select the wrong account on a transient
    #   timeout, corrupting account_cash (feeds the concentration gate).
    # - If every read fails: refuse to sync — notify + return 1.
    # - If all valid reads return 0 positions (user sold everything): proceed
    #   with the first account that returned a valid response (deterministic by
    #   SnapTrade list order; a legitimately all-cash account should still sync
    #   its balance). This is the deliberate policy: "all-zero but valid" is
    #   not the same failure mode as "all reads timed out".
    _best_count: int = -1   # -1 = no valid response seen yet
    _best_id: str | None = None
    for _a in accounts:
        _aid = _a.get("id")
        if not _aid:
            continue
        _aname = _a.get("name") or _a.get("institution_name") or "?"
        _apos = snaptrade_client.get_account_positions(_aid)
        if _apos is None:
            _log(f"broker: account {_aname!r} ({_aid}) — positions read failed; skipping for selection")
            continue  # unknown — do not treat as 0
        _pcount = len(_apos)
        _log(f"broker: account {_aname!r} ({_aid}) — {_pcount} positions")
        if _pcount > _best_count:
            _best_count = _pcount
            _best_id = _aid

    if _best_id is None:
        _detail = (
            "positions read failed for every linked account — cannot safely "
            "select which account to sync; skipping to avoid writing wrong "
            "account's balance"
        )
        _LAST_LANE_FAILURE_DETAIL = _detail
        _log(f"broker: {_detail}")
        _notify_broker_failure(now_et, _detail)
        return 1

    account_id = _best_id
    _log(f"broker: selected account {account_id!r} ({_best_count} positions) for sync")

    rc = 0
    failures: list[str] = []

    # ① balance sync — writes account_cash.
    try:
        raw_balance = snaptrade_client.get_account_balance(account_id)
        mapped = broker_sync.map_balances_to_cash(raw_balance)
        if mapped is None:
            failures.append("balance: SnapTrade balance unavailable/unparseable")
            rc = 1
        else:
            if db.save_account_cash(mapped["cash_balance"], note=mapped["note"]):
                _log(f"broker: balance synced — cash_balance={mapped['cash_balance']}")
            else:
                _detail = _db_unavailable_detail()
                if _detail:
                    return _handle_db_unavailable("broker", now_et, _detail)
                failures.append("balance: save_account_cash failed")
                rc = 1
    except Exception as exc:
        _log(f"broker/balance: UNCAUGHT — {str(exc)[:160]}")
        failures.append(f"balance: {str(exc)[:160]}")
        rc = 1

    # ② transaction import — pending imports / income events / flows / dedup
    #    backfill. Order matters: read trades BEFORE writing anything this
    #    pass, so Tier-2 content-match sees only rows from prior syncs.
    #    NOTE (2026-08-17 review): this sub-job has no explicit DB-outage
    #    probe of its own — it relies on sub-job ① above as the outage
    #    sentinel, same as _run_maintenance's ① (analyst backfill) covers ②
    #    (vol backfill). Every write here is idempotent/self-healing on the
    #    next fire (ignore_duplicates / re-synced bounded window / re-run
    #    Tier-2 match), so a mid-run outage beginning strictly AFTER ①
    #    succeeds silently logs "0 synced" rather than emailing — acceptable
    #    because nothing here feeds a gate or the composite score. Don't
    #    reorder ① after ② without re-adding an equivalent probe.
    try:
        raw_txns = snaptrade_client.get_account_activities(
            account_id, SNAPTRADE_SYNC_MAX_TXN_LOOKBACK_DAYS
        )
        existing_trades = db.load_trades()
        classified = broker_sync.classify_transactions(raw_txns, existing_trades)
        if classified is None:
            failures.append("transactions: SnapTrade activities unavailable")
            rc = 1
        else:
            n_pending = db.save_snaptrade_pending_imports(classified["new_pending"])
            n_income = db.save_snaptrade_income_events(classified["income_events"])
            for bf in classified["backfill_broker_txn_id"]:
                db.backfill_trade_broker_txn_id(bf["trade_id"], bf["broker_txn_id"])
            for flow in classified["flows"]:
                db.add_account_flow(
                    flow["flow_date"], flow["flow_type"], flow["amount"],
                    note="Synced via SnapTrade (Robinhood)",
                )
            _log(
                f"broker: transactions synced — {n_pending} pending, "
                f"{n_income} income events, {len(classified['backfill_broker_txn_id'])} "
                f"backfilled, {len(classified['flows'])} flows, "
                f"ignored={classified['ignored']}"
            )
    except Exception as exc:
        _log(f"broker/transactions: UNCAUGHT — {str(exc)[:160]}")
        failures.append(f"transactions: {str(exc)[:160]}")
        rc = 1

    # ③ bookkeeping only — never contributes to `failures`/rc; a missed
    #    last_full_sync_at stamp doesn't lose user data, it only makes the
    #    next SNAPTRADE_BALANCE_STALE_HOURS staleness check slightly less
    #    precise.
    try:
        db.save_snaptrade_config(status="connected", last_full_sync_at=now_et.isoformat())
    except Exception as exc:
        _log(f"broker/config: UNCAUGHT — {str(exc)[:160]} (ignored — bookkeeping only)")

    if failures:
        _LAST_LANE_FAILURE_DETAIL = "; ".join(failures)
        _notify_broker_failure(now_et, _LAST_LANE_FAILURE_DETAIL)
    return rc


def main() -> int:
    force = os.environ.get("ALERT_FORCE", "") == "1"
    test_email = os.environ.get("ALERT_TEST_EMAIL", "") == "1"
    now_et = datetime.now(_ET)
    # Derive mode from ET hour; named overrides bypass time-inference.
    _mode_override = os.environ.get("ALERT_RUN_MODE", "").strip().lower()
    mode = _mode_override if _mode_override in ("scan", "intraday", "thesis", "debrief",
                                                "monthly", "maintenance", "broker") else (
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
        _failures: list[str] = []
        # Sub-job return codes are CAPTURED, not discarded. Each of the three can
        # now return 1 from _handle_db_unavailable, and dropping that would send
        # 1-3 outage emails and then record status="ok" — the exact silent
        # success this whole change removes, on the one lane where three
        # detectors fire at once. Kept SEPARATE from `_failures` so a DB outage
        # doesn't also trigger _notify_failure's "the lane crashed, read the
        # traceback" email on top of the outage email: two different messages
        # about the same fault would be noise, and the outage email is the
        # accurate one.
        _outages: list[str] = []
        for _job, _fn in (("thesis", _run_thesis), ("debrief", _run_debrief),
                          ("monthly", _run_monthly_report)):
            try:
                if _fn(now_et, force):
                    rc = 1
                    _outages.append(_job)
            except Exception as exc:
                _log(f"{_job}: UNCAUGHT — {str(exc)[:160]}")
                rc = 1
                _failures.append(f"{_job}: {str(exc)[:160]}")
        if _failures:
            _notify_failure(mode, "; ".join(_failures))
        _detail = "; ".join(_failures) or (
            f"{_LAST_LANE_FAILURE_DETAIL} (sub-jobs: {', '.join(_outages)})"
            if _outages else None)
        _record_heartbeat("thesis", now_et,
                          status="failed" if rc else "ok",
                          detail=_detail)
        return rc

    # Every other mode dispatches exactly one job per invocation. Wrap it in
    # the same log-then-fail discipline as the thesis lane above (2026-08-04
    # audit finding: these used to call their _run_X function unguarded — a
    # crash still fails the GitHub Actions run either way, but bypassed this
    # module's own _log() so the failure reason never made it into the
    # run log / dedup state, only a raw traceback in Actions' own output).
    _job_name, _job_fn = {
        "scan":        ("scan",        _run_scan),
        "intraday":    ("intraday",    _run_intraday),
        "debrief":     ("debrief",     _run_debrief),
        "monthly":     ("monthly",     _run_monthly_report),
        "eod":         ("eod",         _run_eod),
        "maintenance": ("maintenance", _run_maintenance),
        "broker":      ("broker",      _run_broker),
    }.get(mode, ("premarket", _run_premarket))
    try:
        rc = _job_fn(now_et, force)
    except Exception as exc:
        _log(f"{_job_name}: UNCAUGHT — {str(exc)[:160]}")
        # Fire the human alert BEFORE the heartbeat: _record_heartbeat does a
        # network upsert, so if the DB is the thing that's down, doing it first
        # would delay the dead-man's-switch email behind a connection timeout.
        _notify_failure(_job_name, str(exc)[:160])
        _record_heartbeat(_job_name, now_et, status="failed", detail=str(exc)[:160])
        raise
    # Status must track the return code, not merely "we got here without an
    # exception". Two things now report by RETURNING non-zero rather than
    # raising — a DB outage in any lane (_handle_db_unavailable) and the
    # maintenance lane's isolated sub-job failures — and recording either as
    # "ok" would show a green heartbeat for a run that actually failed,
    # defeating the dead-man's-switch the heartbeat exists to provide.
    # (Before 2026-08-16 every dispatched lane returned 0 unconditionally, so
    # this branch was unreachable; it is now the normal outage path.)
    if rc:
        _record_heartbeat(_job_name, now_et, status="failed",
                          detail=_LAST_LANE_FAILURE_DETAIL)
    else:
        _record_heartbeat(_job_name, now_et, status="ok")
    return rc


if __name__ == "__main__":
    sys.exit(main())
