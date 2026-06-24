"""
Email delivery for the protective-alert cron (exit-discipline Phase 3).

Resend HTTP API (no SMTP — works cleanly from GitHub Actions with a single API
key). Pure: takes the alert payload + creds as arguments, returns a bool; no
Streamlit, no global config. `render_alert_email` builds the subject + HTML body
from the engine's normalised alert list.
"""

from __future__ import annotations

import html as _html

import requests

_RESEND_ENDPOINT = "https://api.resend.com/emails"

# Per-kind accent + headline label for the email cards.
_KIND_STYLE = {
    "stop_breach":        ("#ef4444", "🛑 STOP BREACH"),
    "deterioration_exit": ("#f97316", "📉 DETERIORATION EXIT"),
    "risk_off_derisk":    ("#f59e0b", "🛡️ RISK-OFF TRIM"),
}


def render_alert_email(alerts: list[dict], built_at: str) -> tuple[str, str]:
    """Return (subject, html_body) for the protective-alert email.

    Caller guarantees `alerts` is non-empty (no email is sent for an empty set).
    """
    n = len(alerts)
    tickers = ", ".join(dict.fromkeys(str(a.get("ticker") or "") for a in alerts if a.get("ticker")))
    subject = f"DRISHTA · {n} protective action{'s' if n != 1 else ''} today — {tickers}"

    cards = []
    for a in alerts:
        accent, label = _KIND_STYLE.get(a.get("kind", ""), ("#f59e0b", "ACTION"))
        ticker = _html.escape(str(a.get("ticker") or ""))
        directive = _html.escape(str(a.get("directive") or ""))
        why = _html.escape(str(a.get("why") or ""))
        trigger = _html.escape(str(a.get("trigger") or ""))
        wt = a.get("weight")
        pnl = a.get("pnl_pct")
        meta = []
        if wt is not None:
            meta.append(f"{float(wt):.1f}% of book")
        if pnl is not None:
            meta.append(f"P&amp;L {float(pnl):+.1f}%")
        meta_str = "  ·  ".join(meta)
        cards.append(f"""
        <div style="border-left:4px solid {accent};background:#1c1917;border-radius:0 6px 6px 0;
                    padding:12px 16px;margin:0 0 10px 0;font-family:Arial,Helvetica,sans-serif">
          <div style="color:{accent};font-weight:700;font-size:13px;letter-spacing:.3px">
            {label} &nbsp;·&nbsp; <span style="color:#e5e7eb">{ticker}</span>
            {f'<span style="color:#9ca3af;font-weight:400">&nbsp;·&nbsp;{meta_str}</span>' if meta_str else ''}
          </div>
          <div style="color:#f1f5f9;font-size:14px;margin-top:6px"><b style="color:{accent}">→ ACT:</b> {directive}</div>
          {f'<div style="color:#a8a29e;font-size:12px;margin-top:4px"><b>Why:</b> {why}</div>' if why else ''}
          {f'<div style="color:#a8a29e;font-size:12px;margin-top:2px"><b>Trigger:</b> {trigger}</div>' if trigger else ''}
        </div>""")

    body = f"""<!DOCTYPE html><html><body style="background:#0c0a09;padding:20px;margin:0">
      <div style="max-width:640px;margin:0 auto">
        <div style="font-family:Arial,Helvetica,sans-serif;color:#f9fafb;font-size:18px;font-weight:700;margin-bottom:4px">
          DRISHTA · Protective Alerts
        </div>
        <div style="font-family:Arial,Helvetica,sans-serif;color:#9ca3af;font-size:12px;margin-bottom:16px">
          {n} same-day reduce decision{'s' if n != 1 else ''} · built {_html.escape(str(built_at))[:19]} ET
        </div>
        {''.join(cards)}
        <div style="font-family:Arial,Helvetica,sans-serif;color:#6b7280;font-size:11px;margin-top:18px;
                    border-top:1px solid #292524;padding-top:10px">
          Protective signals only (stop breaches · deterioration EXIT · risk-off trim). These are directives,
          not auto-executed — open DRISHTA to act. You receive this only when the set changes.
        </div>
      </div>
    </body></html>"""
    return subject, body


def render_test_email(n_alerts: int, built_at: str) -> tuple[str, str]:
    """A delivery-test email (subject, html) — proves the Resend → inbox path
    without waiting for a real protective trigger. Includes today's computed count
    so it also confirms the engine ran."""
    subject = "DRISHTA · alerts pipeline test — delivery OK"
    body = f"""<!DOCTYPE html><html><body style="background:#0c0a09;padding:20px;margin:0">
      <div style="max-width:640px;margin:0 auto;font-family:Arial,Helvetica,sans-serif">
        <div style="color:#f9fafb;font-size:18px;font-weight:700">DRISHTA · Pipeline Test</div>
        <div style="color:#22c55e;font-size:14px;margin-top:10px">
          ✅ If you're reading this, the protective-alert email path works
          (Resend → your inbox).
        </div>
        <div style="color:#a8a29e;font-size:13px;margin-top:10px">
          The engine ran and found <b style="color:#e5e7eb">{n_alerts}</b> protective
          action{'s' if n_alerts != 1 else ''} today. Real alerts (stop breaches ·
          deterioration EXIT · risk-off trim) arrive here automatically, once per ET
          trading day, only when the set changes.
        </div>
        <div style="color:#6b7280;font-size:11px;margin-top:16px;border-top:1px solid #292524;padding-top:10px">
          Sent by a manual test run · built {_html.escape(str(built_at))[:19]} ET. No action needed.
        </div>
      </div>
    </body></html>"""
    return subject, body


def render_pullback_email(pb: dict, built_at: str) -> tuple[str, str]:
    """Reactive pullback-awareness email (subject, html) — the market actually fell
    today. AWARENESS, not an action: it reports exposure (reality observed), while
    the actionable de-risk, if warranted, comes in the pre-market protective email.
    Caller guarantees `pb` is a real pullback (index_pct ≤ threshold)."""
    idx = pb.get("index_pct", 0.0)
    book = pb.get("book_implied_pct")
    mult = pb.get("mult")
    sev = pb.get("severity")
    exposed = [str(x) for x in (pb.get("exposed") or [])]
    subject = f"DRISHTA · market pullback today — S&P {idx:+.1f}%"

    book_line = ""
    if book is not None and mult:
        book_line = (f"Your book carries ~<b style=\"color:#e5e7eb\">{mult:.1f}× market</b> "
                     f"exposure → roughly <b style=\"color:#f87171\">{book:+.1f}%</b> today"
                     + (f" ({_html.escape(str(sev))})." if sev else "."))
    exposed_line = ("Most exposed: " + _html.escape(", ".join(exposed))) if exposed else ""

    body = f"""<!DOCTYPE html><html><body style="background:#0c0a09;padding:20px;margin:0">
      <div style="max-width:640px;margin:0 auto;font-family:Arial,Helvetica,sans-serif">
        <div style="color:#f9fafb;font-size:18px;font-weight:700">DRISHTA · Pullback Today</div>
        <div style="border-left:4px solid #f87171;background:#1c1917;border-radius:0 6px 6px 0;
                    padding:12px 16px;margin:14px 0">
          <div style="color:#f87171;font-weight:700;font-size:14px">
            📉 S&amp;P 500 closed <b>{idx:+.1f}%</b> today.
          </div>
          {f'<div style="color:#f1f5f9;font-size:14px;margin-top:8px">{book_line}</div>' if book_line else ''}
          {f'<div style="color:#a8a29e;font-size:12px;margin-top:6px">{exposed_line}</div>' if exposed_line else ''}
        </div>
        <div style="color:#a8a29e;font-size:13px">
          This is awareness, not a directive — pullback timing isn't predictable, exposure is.
          Nobody can call the bottom; check your protective email (pre-market) for any
          stop / EXIT / risk-off actions if conditions warrant. Reacting to a single red day
          is usually how medium-term investors hurt themselves.
        </div>
        <div style="color:#6b7280;font-size:11px;margin-top:16px;border-top:1px solid #292524;padding-top:10px">
          Sent once per qualifying down-day · built {_html.escape(str(built_at))[:19]} ET.
        </div>
      </div>
    </body></html>"""
    return subject, body


def send_email_resend(*, api_key: str, sender: str, to: str, subject: str, html: str,
                      timeout: int = 20) -> tuple[bool, str]:
    """POST one email via Resend. Returns (ok, detail). `detail` carries the HTTP
    status + truncated Resend error body on failure (NOT echoing the recipient or
    key — safe to log), or "" on success. Never raises.

    `sender` must be a verified Resend sender (e.g. 'DRISHTA <alerts@yourdomain>'
    or the onboarding 'onboarding@resend.dev'). `to` is a single address. NB:
    Resend's onboarding sender is sandboxed — it can ONLY deliver to the account
    owner's own email until a custom domain is verified."""
    if not api_key:
        return False, "no api_key"
    if not sender:
        return False, "no sender (ALERT_EMAIL_FROM)"
    if not to:
        return False, "no recipient (ALERT_EMAIL_TO)"
    try:
        resp = requests.post(
            _RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": sender, "to": [to], "subject": subject, "html": html},
            timeout=timeout,
        )
        if 200 <= resp.status_code < 300:
            return True, ""
        return False, f"HTTP {resp.status_code}: {(resp.text or '')[:300]}"
    except Exception as e:
        return False, f"exception: {type(e).__name__}: {e}"
