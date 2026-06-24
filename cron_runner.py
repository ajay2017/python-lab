#!/usr/bin/env python3
"""
Protective-alert cron entry point (exit-discipline Phase 3).

Run by GitHub Actions (see .github/workflows/alerts.yml). Recomputes the
portfolio's protective signals headlessly and emails the user — but only once per
ET trading day, and only when the set of protective actions has CHANGED since the
last send. All output goes to stdout (the Actions log).

Ships INERT: with no RESEND_API_KEY it computes + logs but sends nothing, so the
pipeline is safe to merge before the secrets are provisioned.

Env:
  SUPABASE_URL, SUPABASE_KEY      — read by stock_analyzer.db (service-role key)
  FINNHUB_API_KEY/FMP_API_KEY/FRED_API_KEY — read by the provider layer
  RESEND_API_KEY, ALERT_EMAIL_TO, ALERT_EMAIL_FROM — email delivery
  ALERT_FORCE=1                   — bypass the once-per-day + hour guards (manual test)

Exit code is always 0 (a cron failure shouldn't fail the workflow noisily); the
log carries the detail.
"""

import hashlib
import os
import sys
from datetime import datetime

import pytz

from stock_analyzer import db
from stock_analyzer.constants import ALERT_EMAIL_HOUR_ET
from stock_analyzer.data import is_trading_day
from stock_analyzer.headless_alert_engine import compute_protective_alerts
from stock_analyzer.notify import render_alert_email, send_email_resend

_ET = pytz.timezone("America/New_York")


def _log(msg: str) -> None:
    print(f"[alerts-cron] {msg}", flush=True)


def _fingerprint(alerts: list[dict]) -> str:
    """Stable hash of the protective SET — by (kind, ticker), not wording, so a
    re-phrased directive doesn't re-trigger an email. Empty set → 'none'."""
    if not alerts:
        return "none"
    keys = sorted(f"{a.get('kind')}:{a.get('ticker')}" for a in alerts)
    return hashlib.sha1("|".join(keys).encode("utf-8")).hexdigest()[:16]


def main() -> int:
    force = os.environ.get("ALERT_FORCE", "") == "1"
    now_et = datetime.now(_ET)
    today_str = now_et.date().isoformat()
    _log(f"start · {now_et.isoformat()} ET · force={force}")

    # ── Guards (skipped under ALERT_FORCE for manual smoke-tests) ─────────────
    if not force:
        if not is_trading_day(now_et.date()):
            _log("not an ET trading day — skip.")
            return 0
        state = db.load_alert_state() or {}
        if state.get("last_emailed_date") == today_str:
            _log("already processed today — skip (second UTC slot).")
            return 0
        if now_et.hour < ALERT_EMAIL_HOUR_ET:
            _log(f"too early (ET hour {now_et.hour} < {ALERT_EMAIL_HOUR_ET}) — wait for the next UTC slot.")
            return 0
    else:
        state = db.load_alert_state() or {}

    # ── Compute ───────────────────────────────────────────────────────────────
    payload = compute_protective_alerts(today=now_et.date())
    alerts = payload.get("alerts", [])
    for e in payload.get("errors", []):
        _log(f"engine note: {e}")
    _log(f"computed {len(alerts)} protective alert(s): "
         + (", ".join(f"{a.get('kind')}/{a.get('ticker')}" for a in alerts) or "(none)"))

    fp = _fingerprint(alerts)
    last_fp = state.get("last_fingerprint")

    # ── Decide + send ───────────────────────────────────────────────────────
    sent = False
    if not alerts:
        _log("nothing to act on — no email.")
    elif fp == last_fp and not force:
        _log(f"unchanged since last send (fp={fp}) — no email (anti-spam).")
    else:
        api_key = os.environ.get("RESEND_API_KEY", "")
        to      = os.environ.get("ALERT_EMAIL_TO", "")
        sender  = os.environ.get("ALERT_EMAIL_FROM", "")
        subject, html = render_alert_email(alerts, payload.get("built_at", today_str))
        if not api_key:
            _log(f"INERT: would email '{subject}' (no RESEND_API_KEY set) — skipping send.")
        else:
            sent = send_email_resend(api_key=api_key, sender=sender, to=to,
                                     subject=subject, html=html)
            _log(f"email send {'OK' if sent else 'FAILED'} → {to or '(no ALERT_EMAIL_TO)'} · '{subject}'")

    # ── Persist dedup state (mark today processed; store the set fingerprint) ──
    # Always update so the same alert doesn't re-send, and a cleared alert today
    # lets it re-send when it reappears. Only persist a successful (or inert) run.
    if db.save_alert_state(today_str, fp):
        _log(f"state saved (date={today_str}, fp={fp}).")
    else:
        _log("state NOT saved (DB offline / alert_state table missing) — dedup degrades to always-send.")

    _log(f"done · sent={sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
