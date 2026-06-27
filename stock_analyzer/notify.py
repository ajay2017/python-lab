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


def render_buy_picks_email(picks: list[dict], built_at: str) -> tuple[str, str]:
    """Return (subject, html_body) for the morning high-conviction buy-list email.

    `picks` are `new_pick` dicts from build_daily_briefing's Grow Today — the
    gated "New Positions to Initiate" (caller filters to the Go / composite-
    confirms set and guarantees non-empty). Built to be ACTED ON from a phone:
    ticker · momentum + composite · entry zone · suggested shares/$ · stop, with
    an entry-zone guard since the user may act later than the scan."""
    n = len(picks)
    tickers = ", ".join(dict.fromkeys(str(p.get("ticker") or "") for p in picks if p.get("ticker")))
    subject = f"DRISHTA · {n} buy setup{'s' if n != 1 else ''} today — {tickers}"

    cards = []
    for p in picks:
        ticker = _html.escape(str(p.get("ticker") or ""))
        sector = _html.escape(str(p.get("sector") or ""))
        score  = p.get("score")
        comp   = p.get("composite_score")
        comp_label = _html.escape(str(p.get("composite_label") or ""))
        conv   = _html.escape(str(p.get("conviction") or ""))
        day    = p.get("day_change")
        thesis = _html.escape(str(p.get("thesis") or ""))[:240]
        sz     = p.get("sizing") or {}
        _lo, _hi = sz.get("entry_lo"), sz.get("entry_hi")
        _sh, _tc = sz.get("shares"), sz.get("total_cost")
        _stop, _pp = sz.get("stop"), sz.get("port_pct")

        score_bits = []
        if score is not None: score_bits.append(f"Momentum {float(score):.0f}")
        if comp  is not None: score_bits.append(f"Composite {float(comp):.0f}" + (f" ({comp_label})" if comp_label else ""))
        if day   is not None and float(day) >= 4: score_bits.append(f"<b style='color:#22c55e'>+{float(day):.1f}% today</b>")
        score_str = "  ·  ".join(score_bits)

        # The actionable line — only render numbers we actually have.
        act_bits = []
        if _lo is not None and _hi is not None:
            _buy = f"Buy in <b style='color:#e5e7eb'>${float(_lo):.2f}–${float(_hi):.2f}</b>"
            if _sh: _buy += f", ~<b style='color:#e5e7eb'>{int(_sh)} shares</b>"
            if _tc: _buy += f" (~${float(_tc):,.0f}" + (f", {float(_pp):.1f}% of book" if _pp is not None else "") + ")"
            act_bits.append(_buy)
        if _stop is not None:
            act_bits.append(f"stop <b style='color:#e5e7eb'>${float(_stop):.2f}</b>")
        act_str = "  ·  ".join(act_bits)
        guard = (f"Only act if price is still inside ${float(_lo):.2f}–${float(_hi):.2f}."
                 if _lo is not None and _hi is not None else "")

        cards.append(f"""
        <div style="border-left:4px solid #22c55e;background:#1c1917;border-radius:0 6px 6px 0;
                    padding:12px 16px;margin:0 0 10px 0;font-family:Arial,Helvetica,sans-serif">
          <div style="color:#22c55e;font-weight:700;font-size:13px;letter-spacing:.3px">
            🟢 GO — COMPOSITE CONFIRMS &nbsp;·&nbsp; <span style="color:#e5e7eb">{ticker}</span>
            {f'<span style="color:#9ca3af;font-weight:400">&nbsp;·&nbsp;{sector}</span>' if sector else ''}
            {f'<span style="color:#9ca3af;font-weight:400">&nbsp;·&nbsp;{conv} conviction</span>' if conv else ''}
          </div>
          {f'<div style="color:#cbd5e1;font-size:12px;margin-top:5px">{score_str}</div>' if score_str else ''}
          {f'<div style="color:#f1f5f9;font-size:14px;margin-top:6px"><b style="color:#22c55e">→ BUY:</b> {act_str}</div>' if act_str else ''}
          {f'<div style="color:#fbbf24;font-size:12px;margin-top:3px">⚠️ {guard}</div>' if guard else ''}
          {f'<div style="color:#a8a29e;font-size:12px;margin-top:4px">{thesis}</div>' if thesis else ''}
        </div>""")

    body = f"""<!DOCTYPE html><html><body style="background:#0c0a09;padding:20px;margin:0">
      <div style="max-width:640px;margin:0 auto">
        <div style="font-family:Arial,Helvetica,sans-serif;color:#f9fafb;font-size:18px;font-weight:700;margin-bottom:4px">
          DRISHTA · New Positions to Initiate
        </div>
        <div style="font-family:Arial,Helvetica,sans-serif;color:#9ca3af;font-size:12px;margin-bottom:16px">
          {n} high-conviction setup{'s' if n != 1 else ''} · scores as of the {_html.escape(str(built_at))[:19]} ET morning scan
        </div>
        {''.join(cards)}
        <div style="font-family:Arial,Helvetica,sans-serif;color:#6b7280;font-size:11px;margin-top:18px;
                    border-top:1px solid #292524;padding-top:10px">
          High-conviction new-position setups that cleared the gates as of the morning scan — momentum + full
          composite agree, conflicts and over-cap sectors excluded. <b>Verify price is still in the entry zone
          before acting</b> (intraday moves can leave it). Advisory — you decide and place the trade; nothing is
          auto-traded. Check the Economic Calendar for any imminent macro event. You receive this only when the
          set of setups changes; silence means nothing cleared the bar.
        </div>
      </div>
    </body></html>"""
    return subject, body


def render_debrief_email(debrief: dict) -> str:
    """Render the weekly portfolio debrief as a professional HTML email (light-mode-first)."""
    import re

    week_ending  = debrief.get("week_ending", "—")
    generated_at = str(debrief.get("generated_at", ""))[:10]
    perf  = debrief.get("performance_pct")
    spy   = debrief.get("spy_pct")
    alpha = debrief.get("alpha_pct")

    def _md_inline(text: str) -> str:
        """Convert **bold** and *italic* markdown to HTML inline."""
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\*([^*\s][^*]*?)\*",  r"<em>\1</em>",      text)
        return text

    def _pct_cell(label: str, v: float | None, bold: bool = False) -> str:
        if v is None:
            val_html = "<span style='color:#6b7280'>N/A</span>"
        else:
            colour   = "#16a34a" if v >= 0 else "#dc2626"
            weight   = "700" if bold else "600"
            val_html = f"<span style='color:{colour};font-weight:{weight}'>{v:+.1f}%</span>"
        lw = "700" if bold else "400"
        return (
            f"<td style='padding:14px 20px;text-align:center;border-right:1px solid #e5e7eb'>"
            f"<div style='font-size:0.75em;color:#6b7280;text-transform:uppercase;"
            f"letter-spacing:0.05em;font-weight:600;margin-bottom:4px'>{label}</div>"
            f"<div style='font-size:1.4em;font-weight:{lw}'>{val_html}</div>"
            f"</td>"
        )

    perf_block = ""
    if perf is not None:
        perf_block = (
            "<table width='100%' style='border-collapse:collapse;background:#f8fafc;"
            "border:1px solid #e5e7eb;border-radius:8px;margin:20px 0;overflow:hidden'>"
            "<tr>"
            + _pct_cell("Portfolio", perf, bold=True)
            + _pct_cell("S&P 500", spy)
            + _pct_cell("Alpha", alpha, bold=True).replace("border-right:1px solid #e5e7eb", "border-right:none")
            + "</tr></table>"
        )

    # Section accent colours (left-border strip)
    _SECTION_COLOURS = {
        "What happened":       "#3b82f6",   # blue
        "Decisions you made":  "#8b5cf6",   # purple
        "Patterns this week":  "#f59e0b",   # amber
        "One thing to watch":  "#10b981",   # green
    }

    def _section(title: str, content: str) -> str:
        if not content:
            return ""
        accent = _SECTION_COLOURS.get(title, "#6b7280")
        lines   = content.split("\n")
        parts: list[str] = []
        in_list = False
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith(("• ", "- ", "* ")):
                item = _md_inline(stripped[2:])
                if not in_list:
                    parts.append(
                        "<ul style='margin:8px 0 8px 0;padding-left:20px;color:#374151'>"
                    )
                    in_list = True
                parts.append(
                    f"<li style='margin:6px 0;line-height:1.55;color:#374151'>{item}</li>"
                )
            else:
                if in_list:
                    parts.append("</ul>")
                    in_list = False
                if stripped:
                    parts.append(
                        f"<p style='margin:6px 0;color:#374151;line-height:1.6'>"
                        f"{_md_inline(stripped)}</p>"
                    )
        if in_list:
            parts.append("</ul>")
        body = "\n".join(parts)
        return (
            f"<div style='margin:20px 0;background:#ffffff;border-radius:8px;"
            f"border:1px solid #e5e7eb;border-left:4px solid {accent};overflow:hidden'>"
            f"<div style='padding:10px 16px 8px;background:#f9fafb;"
            f"border-bottom:1px solid #e5e7eb'>"
            f"<span style='font-weight:700;font-size:0.85em;color:{accent};"
            f"text-transform:uppercase;letter-spacing:0.06em'>{title}</span></div>"
            f"<div style='padding:14px 16px;font-size:0.9em'>{body}</div>"
            f"</div>"
        )

    sections_html = (
        _section("What happened",       debrief.get("section_facts", ""))
        + _section("Decisions you made",  debrief.get("section_decisions", ""))
        + _section("Patterns this week",  debrief.get("section_patterns", ""))
        + _section("One thing to watch",  debrief.get("section_watchnext", ""))
    )

    return (
        "<!DOCTYPE html><html lang='en'>"
        "<head><meta charset='UTF-8'><meta name='viewport' content='width=device-width'></head>"
        "<body style='background:#f3f4f6;color:#111827;font-family:-apple-system,BlinkMacSystemFont,"
        "\"Segoe UI\",Roboto,sans-serif;margin:0;padding:32px 16px'>"

        # Outer card
        "<div style='max-width:600px;margin:0 auto;background:#ffffff;"
        "border-radius:12px;box-shadow:0 1px 8px rgba(0,0,0,0.08);overflow:hidden'>"

        # Header band
        "<div style='background:linear-gradient(135deg,#1e3a5f 0%,#1e40af 100%);"
        "padding:28px 28px 22px'>"
        "<div style='font-size:1.35em;font-weight:800;color:#ffffff;letter-spacing:-0.01em'>"
        "DRISHTA Weekly Debrief</div>"
        f"<div style='color:#93c5fd;font-size:0.82em;margin-top:4px'>"
        f"Week ending {week_ending} &nbsp;·&nbsp; Generated {generated_at}</div>"
        "</div>"

        # Body
        f"<div style='padding:24px 24px 20px'>"
        f"{perf_block}"
        f"{sections_html}"
        "</div>"

        # Footer
        "<div style='background:#f9fafb;border-top:1px solid #e5e7eb;"
        "padding:12px 24px;font-size:0.75em;color:#9ca3af;text-align:center'>"
        "DRISHTA &nbsp;·&nbsp; AI-generated retrospective &nbsp;·&nbsp; Not financial advice"
        "</div>"

        "</div>"  # close card
        "</body></html>"
    )


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
