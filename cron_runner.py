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
"""

import hashlib
import os
import sys
from datetime import datetime

import pytz

from stock_analyzer import db
from stock_analyzer.constants import ALERT_EMAIL_HOUR_ET, ALERT_EOD_HOUR_ET
from stock_analyzer.data import is_trading_day
from stock_analyzer.headless_alert_engine import compute_protective_alerts, compute_eod
from stock_analyzer.notify import (
    render_alert_email, render_test_email, render_pullback_email, send_email_resend,
)

_ET = pytz.timezone("America/New_York")
_PROTECTIVE_ROW = 1   # alert_state lane: pre-market protective dedup
_EOD_ROW = 2          # alert_state lane: EOD pullback dedup


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
        state = db.load_alert_state(_PROTECTIVE_ROW) or {}
        if state.get("last_emailed_date") == today_str:
            _log("premarket: already processed today — skip (second UTC slot).")
            return 0
        if now_et.hour < ALERT_EMAIL_HOUR_ET:
            _log(f"premarket: too early (ET hour {now_et.hour} < {ALERT_EMAIL_HOUR_ET}) — skip.")
            return 0
    else:
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

    if db.save_alert_state(today_str, fp, _PROTECTIVE_ROW):
        _log(f"state saved (row={_PROTECTIVE_ROW}, date={today_str}, fp={fp}).")
    else:
        _log("state NOT saved (DB offline / table missing) — dedup degrades to always-send.")
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


def _run_scan(now_et, force: bool) -> int:
    """Mid-morning headless sector scan → persist the result so the Home page's
    buy-candidate / Grow-Today new-pick lists populate on a COLD load without the
    user running the ~20s scanner. Overwrites the single-row scanner_cache (no
    dedup needed). Inert until the scanner_cache table exists. Always exits 0."""
    today_str = now_et.date().isoformat()
    if not force and not is_trading_day(now_et.date()):
        _log("scan: not an ET trading day — skip.")
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
    return 0


def main() -> int:
    force = os.environ.get("ALERT_FORCE", "") == "1"
    test_email = os.environ.get("ALERT_TEST_EMAIL", "") == "1"
    now_et = datetime.now(_ET)
    mode = (os.environ.get("ALERT_RUN_MODE", "").strip().lower()
            or ("eod" if now_et.hour >= 12 else "premarket"))
    _log(f"start · {now_et.isoformat()} ET · mode={mode} · force={force} · test_email={test_email}")

    if test_email:
        return _run_test_email(now_et)
    if mode == "scan":
        return _run_scan(now_et, force)
    if mode == "eod":
        return _run_eod(now_et, force)
    return _run_premarket(now_et, force)


if __name__ == "__main__":
    sys.exit(main())
