"""
Email delivery for the protective-alert cron (exit-discipline Phase 3).

Resend HTTP API (no SMTP — works cleanly from GitHub Actions with a single API
key). Pure: takes the alert payload + creds as arguments, returns a bool; no
Streamlit, no global config. `render_alert_email` builds the subject + HTML body
from the engine's normalised alert list.
"""

from __future__ import annotations

import html as _html
import re

import requests

from stock_analyzer.constants import SINGLE_NAME_CEILING, NET_CAPITAL_POSITION_CAP_PCT

_RESEND_ENDPOINT = "https://api.resend.com/emails"


def _sizing_cap_note(sz: dict) -> str:
    """Concentration-cap disclosure row for an email card, or "" when silent.

    Five distinct cases, none of which may pass unexplained (an email that
    silently drops the size line reads as "no opinion" rather than "the cap
    bound"):
      * ceiling_capped     — a size was suggested, but trimmed to the single-
                             name book cap.
      * capital_capped     — (F-255) a size was suggested, but trimmed to the
                             SEPARATE net-capital cap. NOT mutually exclusive
                             with ceiling_capped: risk.position_sizing applies
                             the capital cap AFTER the ceiling cap, so a size
                             already capped by the book ceiling can be capped
                             FURTHER by capital — both notes render together
                             when both are true, so neither fact is dropped.
      * ceiling_infeasible — one share alone breaches the book cap, so no size
                             was suggested at all.
      * capital_infeasible — (F-255) one share alone breaches the SEPARATE
                             net-capital cap (or net_capital <= 0 / margin-
                             called), so no size was suggested at all.
      * stop_infeasible    — price is at/below the name's ATR stop, so there is
                             no room between entry and stop to size against.
    The four "infeasible"/"capped-only" terminal states are mutually exclusive
    per `_position_size_for_render`'s own branching (only one of stop/ceiling/
    capital "no size" reasons is ever set, and a terminal reason never carries
    ceiling_capped/capital_capped) — so checking them in sequence after the
    capped-notes block never double-renders. All interpolated values are
    numbers this module computed, not free text.
    """
    parts = []
    if sz.get("ceiling_capped") and sz.get("uncapped_shares") is not None:
        parts.append(
            f'<div style="color:#94a3b8;font-size:11px;margin-top:3px">'
            f'📐 Size capped at {int(SINGLE_NAME_CEILING)}% single-name limit — '
            f'risk-budget size would be {int(sz["uncapped_shares"])} shares.</div>'
        )
    if sz.get("capital_capped") and sz.get("capital_pct") is not None:
        parts.append(
            f'<div style="color:#94a3b8;font-size:11px;margin-top:3px">'
            f'📐 Size also capped at {int(NET_CAPITAL_POSITION_CAP_PCT)}% of net capital (margin-aware) — '
            f'now ~{float(sz["capital_pct"]):.0f}% of your actual capital.</div>'
        )
    if parts:
        return "".join(parts)
    if sz.get("ceiling_infeasible") and sz.get("one_share_pct") is not None:
        return (
            f'<div style="color:#94a3b8;font-size:11px;margin-top:3px">'
            f'📐 No size suggested — one share is ~{float(sz["one_share_pct"]):.0f}% of the book, '
            f'above the {int(SINGLE_NAME_CEILING)}% single-name ceiling.</div>'
        )
    if sz.get("capital_infeasible") and sz.get("one_share_capital_pct") is not None:
        return (
            f'<div style="color:#94a3b8;font-size:11px;margin-top:3px">'
            f'📐 No size suggested — one share is ~{float(sz["one_share_capital_pct"]):.0f}% of your net capital, '
            f'above the {int(NET_CAPITAL_POSITION_CAP_PCT)}% net-capital cap (margin-aware).</div>'
        )
    if sz.get("stop_infeasible") and sz.get("stop_at") is not None:
        return (
            f'<div style="color:#94a3b8;font-size:11px;margin-top:3px">'
            f'📐 No size suggested — price is at or below this name&#39;s '
            f'${float(sz["stop_at"]):,.2f} ATR stop.</div>'
        )
    return ""


def _book_drift_banner(verdict: dict | None) -> str:
    """Book-vs-broker drift disclosure for an email, or "" when silent.

    F-252 follow-up (2026-08-24): the suggested share sizes in this email are
    computed from the app's own `portfolio_value` — a book-wide sum — which
    can silently disagree with what the broker actually shows. Since
    `portfolio_value` is a single denominator shared by every pick, a drift
    on ANY held ticker corrupts the sizing for every new pick in this email,
    including names that have nothing to do with the drifted ticker — so this
    renders ONE book-wide banner, not a per-pick note.

    Renders ONLY on `verdict["state"] == "drift"` — a real, actionable
    mismatch. Silent on "unknown"/"stale_clean"/"awaiting_sync"/"none" and on
    `None` (SnapTrade not configured, no capture yet, or the drift check
    itself failed). This deliberately diverges from the in-app Home banner,
    which also renders on "unknown" (a QUERIED surface, where silence + a
    green tick would fail open into looking clean). This is a PUSH surface:
    it never implies broker verification happened, so silence here does not
    assert cleanliness — rendering "unknown" on every push email a user with
    no broker linked ever receives is exactly the amber-fatigue this
    module's own docstring principle warns against. Fire only on a positive,
    actionable finding.

    Never changes a suggested share count — annotation only.
    """
    if not isinstance(verdict, dict) or verdict.get("state") != "drift":
        return ""
    impact = verdict.get("impact")
    if not isinstance(impact, dict):
        impact = {}
    overstated = impact.get("overstated") or 0.0
    _amt = f" (~${abs(float(overstated)):,.0f} of book value in question)" if overstated else ""
    return (
        f'<div style="border-left:4px solid #f59e0b;background:#1c1710;border-radius:0 6px 6px 0;'
        f'padding:10px 16px;margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif">'
        f'<div style="color:#f59e0b;font-weight:700;font-size:12px">'
        f'⚠️ Your recorded book disagrees with your broker{_amt}</div>'
        f'<div style="color:#cbd5e1;font-size:12px;margin-top:3px">'
        f'The suggested share sizes below are computed from the app\'s portfolio value — '
        f'verify the size against your actual account before acting.</div>'
        f'</div>'
    )


def _macro_coverage_banner(expired: "list[dict] | None") -> str:
    """Macro calendar coverage disclosure for an email, or "" when silent.

    Modelled on `_book_drift_banner`: same silent-unless-positive shape, same
    HTML conventions.

    Returns "" for None (could not verify), "" for [] (nothing expired), and
    "" when nothing in the list is overdue.  Fires only when at least one
    recurring series is genuinely overdue — i.e. today is past the date the
    next release was expected based on the series' own cadence.

    Never changes a suggested share count — annotation only.
    """
    if not isinstance(expired, list) or not expired:
        return ""
    overdue = [e for e in expired if e.get("is_overdue")]
    if not overdue:
        return ""
    names = ", ".join(e.get("name", "") for e in overdue)
    return (
        f'<div style="border-left:4px solid #ef4444;background:#1c0a0a;border-radius:0 6px 6px 0;'
        f'padding:10px 16px;margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif">'
        f'<div style="color:#ef4444;font-weight:700;font-size:12px">'
        f'&#9888;&#65039; Macro calendar blind spot — {names} is overdue and not on the calendar</div>'
        f'<div style="color:#cbd5e1;font-size:12px;margin-top:3px">'
        f'Picks above cleared every other gate but were NOT screened against this release. '
        f'This is not “no macro events are near.” Update the calendar in the app '
        f'(\U0001f9fa System Trust → Reference data).</div>'
        f'</div>'
    )


# Per-kind accent + headline label for the email cards.
_KIND_STYLE = {
    "stop_breach":        ("#ef4444", "🛑 STOP BREACH"),
    "deterioration_exit": ("#f97316", "📉 DETERIORATION EXIT"),
    "risk_off_derisk":    ("#f59e0b", "🛡️ RISK-OFF TRIM"),
}


def render_alert_email(
    alerts: list[dict],
    built_at: str,
    velocity_alerts: list[dict] | None = None,
) -> tuple[str, str]:
    """Return (subject, html_body) for the protective-alert email.

    `alerts` are the hard protective signals (stop breaches, EXIT, risk-off).
    `velocity_alerts` (optional) are WATCH tickers whose composite score is
    accelerating downward — shown as a separate section below hard alerts.
    At least one of alerts / velocity_alerts must be non-empty (caller's
    responsibility).
    """
    velocity_alerts = velocity_alerts or []
    n = len(alerts)
    n_vel = len(velocity_alerts)
    tickers = ", ".join(dict.fromkeys(str(a.get("ticker") or "") for a in alerts if a.get("ticker")))
    vel_tickers = ", ".join(dict.fromkeys(str(v.get("ticker") or "") for v in velocity_alerts if v.get("ticker")))

    if alerts:
        subject = f"DRISHTA · {n} protective action{'s' if n != 1 else ''} today — {tickers}"
    else:
        subject = (f"DRISHTA · {n_vel} WATCH accelerating — {vel_tickers}"
                   if n_vel == 1 else
                   f"DRISHTA · {n_vel} WATCH signals accelerating — {vel_tickers}")

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

    # Velocity section — WATCH positions whose composite score is trending down
    vel_html = ""
    if velocity_alerts:
        vel_rows = []
        for v in velocity_alerts:
            t   = _html.escape(str(v.get("ticker") or ""))
            d   = v.get("delta")
            nd  = v.get("n_days")
            ns  = v.get("newest_score")
            os_ = v.get("oldest_score")
            d_str  = f"{float(d):+.1f} pts" if d is not None else ""
            nd_str = f"over {nd} days" if nd is not None else ""
            sc_str = (f"{float(os_):.0f} → {float(ns):.0f}"
                      if os_ is not None and ns is not None else "")
            detail = "  ·  ".join(x for x in [sc_str, d_str, nd_str] if x)
            vel_rows.append(f"""
            <div style="border-left:3px solid #f59e0b;background:#1c1400;border-radius:0 6px 6px 0;
                        padding:10px 14px;margin:0 0 8px 0;font-family:Arial,Helvetica,sans-serif">
              <div style="color:#f59e0b;font-weight:700;font-size:12px;letter-spacing:.3px">
                ⚡ WATCH ACCELERATING &nbsp;·&nbsp; <span style="color:#e5e7eb">{t}</span>
              </div>
              {f'<div style="color:#cbd5e1;font-size:12px;margin-top:4px">Score {_html.escape(detail)}</div>' if detail else ''}
              <div style="color:#a8a29e;font-size:12px;margin-top:3px">
                Not yet at TRIM — but deteriorating faster than usual. Open the app to review.
              </div>
            </div>""")
        vel_html = f"""
        <div style="margin-top:{14 if alerts else 0}px">
          {f'<div style="font-family:Arial,Helvetica,sans-serif;color:#9ca3af;font-size:11px;font-weight:600;letter-spacing:.3px;margin-bottom:8px">EARLY WARNING</div>' if alerts else ''}
          {''.join(vel_rows)}
        </div>"""

    subtitle = (f"{n} protective action{'s' if n != 1 else ''}"
                + (f" · {n_vel} WATCH accelerating" if n_vel else "")
                + f" · built {_html.escape(str(built_at))[:19]} ET")
    if not alerts:
        subtitle = f"{n_vel} early warning{'s' if n_vel != 1 else ''} · built {_html.escape(str(built_at))[:19]} ET"

    body = f"""<!DOCTYPE html><html><body style="background:#0c0a09;padding:20px;margin:0">
      <div style="max-width:640px;margin:0 auto">
        <div style="font-family:Arial,Helvetica,sans-serif;color:#f9fafb;font-size:18px;font-weight:700;margin-bottom:4px">
          DRISHTA · Protective Alerts
        </div>
        <div style="font-family:Arial,Helvetica,sans-serif;color:#9ca3af;font-size:12px;margin-bottom:16px">
          {subtitle}
        </div>
        {''.join(cards)}
        {vel_html}
        <div style="font-family:Arial,Helvetica,sans-serif;color:#6b7280;font-size:11px;margin-top:18px;
                    border-top:1px solid #292524;padding-top:10px">
          Protective signals (stop breaches · deterioration EXIT · risk-off trim) are directives —
          not auto-executed, open DRISHTA to act. Early-warning WATCH velocity is informational only
          (the position has not yet reached TRIM). You receive this only when the set changes.
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


def render_db_outage_email(lane: str, lane_label: str, what_did_not_run: str,
                           detail: str, built_at: str) -> tuple[str, str]:
    """Email (subject, html) for "this lane could not read the database".

    Deliberately NOT reused from `_notify_failure`'s renderer: that email means
    "the code crashed, go read a traceback." This one means something different
    and more useful — "the lane ran correctly and could not see your book."

    Tone is load-bearing. The tempting version says "⚠️ your positions may be
    unprotected!" — that manufactures alarm out of an ABSENCE of data, which is
    exactly what the calm-advisor persona forbids. State precisely what did not
    happen, state that we have no market opinion right now because we could not
    see anything, and stop.

    STYLING DIFFERS FROM EVERY OTHER RENDERER IN THIS MODULE, ON PURPOSE.
    The others put `background:#0c0a09` on <body> and then use near-white text.
    Yahoo, Gmail and most webmail STRIP or override <body> styling, so the dark
    background disappears and the light text is left on white — verified live on
    2026-08-16, where the title and the entire "What to check" list rendered
    nearly invisible. That is tolerable on an informational email and NOT
    tolerable here: this one is read at 8am when something is actually wrong,
    and the actionable list was the least legible part of it. So this renderer
    uses dark-on-light with the background on a wrapping <div> the clients
    respect. Don't "make it consistent" with the others without re-testing in a
    real inbox.

    Pure string building: no DB, no Streamlit, no
    imports beyond this module's existing three — the whole point is that this
    path still works when Supabase does not.
    """
    subject = f"🔴 DRISHTA: {lane_label} did NOT run — database unreachable"
    body = f"""<!DOCTYPE html><html><body style="margin:0;padding:20px;background:#f4f4f5">
      <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #d4d4d8;
                  border-radius:8px;padding:22px;font-family:Arial,Helvetica,sans-serif">
        <div style="color:#18181b;font-size:18px;font-weight:700">
          DRISHTA · scheduled run did not complete
        </div>

        <div style="color:#b91c1c;font-size:15px;margin-top:14px;font-weight:700">
          {_html.escape(what_did_not_run)}
        </div>

        <div style="color:#3f3f46;font-size:13px;margin-top:12px">
          DRISHTA could not read your holdings from Supabase, so the
          <b style="color:#18181b">{_html.escape(lane_label)}</b> lane had nothing to work from.
        </div>
        <div style="color:#3f3f46;font-size:12px;margin-top:8px;font-family:monospace;
                    background:#f4f4f5;border:1px solid #e4e4e7;padding:8px 10px;border-radius:4px">
          {_html.escape(str(detail))[:400]}
        </div>

        <div style="color:#3f3f46;font-size:13px;margin-top:14px;
                    border-left:3px solid #a1a1aa;padding-left:10px">
          <b style="color:#18181b">This is an infrastructure fault, not a market signal.</b>
          DRISHTA has no opinion on your positions right now, because it could not see them.
          Nothing here says anything about what the market did.
        </div>

        <div style="color:#3f3f46;font-size:13px;margin-top:14px">
          <b style="color:#18181b">What to check</b>
          <ol style="margin:6px 0 0 18px;padding:0;color:#3f3f46">
            <li>Supabase project status — is the instance paused or down?</li>
            <li>Is <span style="font-family:monospace;color:#18181b">SUPABASE_KEY</span> still the
                <b>service-role / secret</b> key (not the publishable/anon one)?</li>
            <li>Railway → Shared Variables — are the Supabase vars attached to this cron service?</li>
            <li>Open DRISHTA. If the app also shows an empty portfolio, the outage is real.</li>
          </ol>
        </div>

        <div style="color:#52525b;font-size:11px;margin-top:16px;border-top:1px solid #e4e4e7;padding-top:10px">
          Lane <span style="font-family:monospace">{_html.escape(lane)}</span> ·
          attempted {_html.escape(str(built_at))[:19]} ET.
          You are receiving this because a scheduled run could not reach the database —
          silence would have been indistinguishable from "nothing to report".
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
        _cap_note  = _sizing_cap_note(sz)

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
          {_cap_note}
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


def render_daily_action_email(
    top_pick: dict,
    exit_alerts: list[dict],
    other_picks: list[dict],
    built_at: str,
    book_drift: dict | None = None,
    macro_coverage_expired: "list[dict] | None" = None,
) -> tuple[str, str]:
    """Return (subject, html_body) for the single-action morning email.

    Replaces the flat buy-list format with a priority-first layout:
      Section 1 (if any EXIT/TRIM signals from premarket run) — handle exits first
      Section 1b (if book_drift shows real drift) — book-vs-broker disclosure
      Section 1c (if macro calendar is overdue) — macro coverage blind-spot
      Section 2 — #1 entry action today (top composite pick, full detail)
      Section 3 — other setups as a compact reference list

    `exit_alerts` are rows from exit_signals (EXIT or TRIM tier only).
    `top_pick` is the highest-composite go-verdict pick (caller guarantees non-empty).
    `other_picks` are the remaining go-verdict picks (may be empty).
    `book_drift` is the verdict dict from broker_sync.decide_drift_banner, or
    None — see `_book_drift_banner` for when it renders (F-252 follow-up).
    `macro_coverage_expired` is from reference_shelf.expired_macro_series or
    None — see `_macro_coverage_banner` for when it renders.
    """
    from stock_analyzer.constants import SCAN_TOP_PICK_MIN_COMPOSITE

    ticker  = _html.escape(str(top_pick.get("ticker") or ""))
    comp    = top_pick.get("composite_score")
    score   = top_pick.get("score") or top_pick.get("momentum_score")
    sector  = _html.escape(str(top_pick.get("sector") or ""))
    thesis  = _html.escape(str(top_pick.get("thesis") or ""))[:240]
    sz      = top_pick.get("sizing") or {}
    _lo, _hi_p = sz.get("entry_lo"), sz.get("entry_hi")
    _sh, _tc   = sz.get("shares"), sz.get("total_cost")
    _stop, _pp = sz.get("stop"), sz.get("port_pct")
    _cap_note  = _sizing_cap_note(sz)
    day     = top_pick.get("day_change")

    is_high_conviction = (comp or 0) >= SCAN_TOP_PICK_MIN_COMPOSITE

    # Subject — includes exit ticker(s) when exits need attention first
    exit_tickers = [_html.escape(str(a.get("ticker") or "")) for a in exit_alerts if a.get("ticker")]
    if exit_tickers and ticker:
        subject = f"DRISHTA · Exit {exit_tickers[0]} + Enter {ticker}"
    elif ticker and comp is not None:
        subject = f"DRISHTA · Act on {ticker} — {float(comp):.0f}/100"
    else:
        subject = f"DRISHTA · Morning action: {ticker}"

    # ── Section 1: exit alerts (if any) ──────────────────────────────────────
    exit_html = ""
    if exit_alerts:
        rows = []
        for a in exit_alerts:
            t    = _html.escape(str(a.get("ticker") or ""))
            tier = _html.escape(str(a.get("signal_type") or ""))
            pnl  = a.get("pnl_pct")
            dd   = a.get("dd_from_peak_pct")
            pnl_str = f"{float(pnl):+.1f}% P&L" if pnl is not None else ""
            dd_str  = f"{float(dd):.1f}% from peak" if dd is not None else ""
            detail  = "  ·  ".join(x for x in [pnl_str, dd_str] if x)
            color   = "#ef4444" if tier == "EXIT" else "#f97316"
            rows.append(
                f"<div style='margin:3px 0'>"
                f"<span style='color:{color};font-weight:700'>{tier}</span>"
                f"&nbsp;&nbsp;<span style='color:#f1f5f9;font-weight:700'>{t}</span>"
                + (f"&nbsp;&nbsp;<span style='color:#9ca3af;font-size:12px'>{_html.escape(detail)}</span>"
                   if detail else "")
                + "</div>"
            )
        exit_html = f"""
        <div style="border-left:4px solid #ef4444;background:#1c0a0a;border-radius:0 6px 6px 0;
                    padding:12px 16px;margin:0 0 16px 0;font-family:Arial,Helvetica,sans-serif">
          <div style="color:#ef4444;font-weight:700;font-size:12px;letter-spacing:.3px;margin-bottom:6px">
            ⚠️ HANDLE EXITS BEFORE ENTERING NEW POSITIONS
          </div>
          {''.join(rows)}
        </div>"""

    # ── Section 1b: book-vs-broker drift disclosure (if any) ─────────────────
    drift_html = _book_drift_banner(book_drift)

    # ── Section 1c: macro calendar blind-spot (if any) ────────────────────────
    macro_cov_html = _macro_coverage_banner(macro_coverage_expired)

    # ── Section 2: #1 action card ─────────────────────────────────────────────
    score_bits = []
    if score  is not None: score_bits.append(f"Momentum {float(score):.0f}")
    if comp   is not None: score_bits.append(f"Composite <b style='color:#e5e7eb'>{float(comp):.0f}/100</b>")
    if day    is not None and float(day) >= 4:
        score_bits.append(f"<b style='color:#22c55e'>+{float(day):.1f}% today</b>")
    score_str = "  ·  ".join(score_bits)

    act_bits = []
    if _lo is not None and _hi_p is not None:
        _buy = f"Buy ${float(_lo):.2f}–${float(_hi_p):.2f}"
        if _sh:  _buy += f", ~{int(_sh)} shares"
        if _tc:  _buy += f" (~${float(_tc):,.0f}" + (f", {float(_pp):.1f}% of book" if _pp is not None else "") + ")"
        act_bits.append(_buy)
    if _stop is not None:
        act_bits.append(f"stop ${float(_stop):.2f}")
    act_str = "  ·  ".join(act_bits)

    guard = (f"Only act if price is still inside ${float(_lo):.2f}–${float(_hi_p):.2f}."
             if _lo is not None and _hi_p is not None else "")

    conviction_badge = (
        "<span style='color:#22c55e;font-weight:700'>HIGH CONVICTION</span>"
        if is_high_conviction else
        "<span style='color:#fbbf24;font-weight:700'>MODERATE</span>"
    )

    top_card = f"""
    <div style="border-left:4px solid {'#22c55e' if is_high_conviction else '#fbbf24'};
                background:#1c1917;border-radius:0 6px 6px 0;
                padding:14px 16px;margin:0 0 6px 0;font-family:Arial,Helvetica,sans-serif">
      <div style="color:#9ca3af;font-size:11px;font-weight:600;letter-spacing:.4px;margin-bottom:4px">
        #1 ENTRY TODAY · {conviction_badge}
      </div>
      <div style="color:#f9fafb;font-size:22px;font-weight:700;margin-bottom:2px">
        {ticker}
        {f'<span style="color:#9ca3af;font-size:14px;font-weight:400"> · {sector}</span>' if sector else ''}
      </div>
      {f'<div style="color:#cbd5e1;font-size:13px;margin-bottom:6px">{score_str}</div>' if score_str else ''}
      {f'<div style="color:#f1f5f9;font-size:14px;font-weight:600;margin-bottom:3px">→ {act_str}</div>' if act_str else ''}
      {f'<div style="color:#fbbf24;font-size:12px;margin-bottom:4px">⚠️ {guard}</div>' if guard else ''}
      {f'<div style="color:#a8a29e;font-size:12px">{thesis}</div>' if thesis else ''}
      {_cap_note}
    </div>"""

    # ── Section 3: other setups (compact) ────────────────────────────────────
    other_html = ""
    if other_picks:
        rows2 = []
        for p in other_picks:
            t2   = _html.escape(str(p.get("ticker") or ""))
            c2   = p.get("composite_score")
            s2   = _html.escape(str(p.get("sector") or ""))
            rows2.append(
                f"<div style='margin:4px 0;color:#d1d5db;font-size:13px'>"
                f"<span style='color:#e5e7eb;font-weight:600'>{t2}</span>"
                + (f"&nbsp;—&nbsp;composite {float(c2):.0f}" if c2 is not None else "")
                + (f"&nbsp;·&nbsp;<span style='color:#9ca3af'>{s2}</span>" if s2 else "")
                + "</div>"
            )
        other_html = f"""
        <div style="border-top:1px solid #292524;margin-top:14px;padding-top:10px;
                    font-family:Arial,Helvetica,sans-serif">
          <div style="color:#6b7280;font-size:11px;font-weight:600;letter-spacing:.3px;margin-bottom:6px">
            OTHER SETUPS (context — not the primary action)
          </div>
          {''.join(rows2)}
        </div>"""

    body = f"""<!DOCTYPE html><html><body style="background:#0c0a09;padding:20px;margin:0">
      <div style="max-width:640px;margin:0 auto">
        <div style="font-family:Arial,Helvetica,sans-serif;color:#f9fafb;font-size:18px;
                    font-weight:700;margin-bottom:2px">DRISHTA · Morning Action Brief</div>
        <div style="font-family:Arial,Helvetica,sans-serif;color:#9ca3af;font-size:12px;
                    margin-bottom:16px">
          {_html.escape(str(built_at))[:19]} ET · one decisive call
        </div>
        {exit_html}
        {drift_html}
        {macro_cov_html}
        {top_card}
        {other_html}
        <div style="font-family:Arial,Helvetica,sans-serif;color:#6b7280;font-size:11px;
                    margin-top:18px;border-top:1px solid #292524;padding-top:10px">
          Composite + momentum both confirm the #1 pick; all gates cleared (tone, sector,
          concentration, macro). <b>Check price is still in the entry zone before acting —
          intraday moves can leave it.</b> Advisory only — you place the trade. Exit signals
          shown above are from the 8am premarket run; open the app for full context.
        </div>
      </div>
    </body></html>"""
    return subject, body


def render_intraday_entry_email(
    entries: list[dict],
    spy_drop: float | None,
    built_at: str,
    book_drift: dict | None = None,
    macro_coverage_expired: "list[dict] | None" = None,
) -> tuple[str, str]:
    """Return (subject, html_body) for the intraday pullback entry-window email.

    `entries` are qualifying picks enriched with intraday_drop_pct, current_price,
    open_price from intraday_entry.compute_intraday_entries().  Non-empty guaranteed
    by caller. `spy_drop` is SPY's intraday drop (negative float) or None.
    `book_drift` is the verdict dict from broker_sync.decide_drift_banner, or
    None — see `_book_drift_banner` for when it renders (F-252 follow-up).
    `macro_coverage_expired` is from reference_shelf.expired_macro_series or
    None — see `_macro_coverage_banner` for when it renders.
    """
    n = len(entries)
    top = entries[0]
    top_ticker = _html.escape(str(top.get("ticker") or ""))
    subject = (f"DRISHTA · Entry window: {top_ticker} down {abs(top.get('intraday_drop_pct', 0)):.1f}% from open"
               if n == 1 else
               f"DRISHTA · {n} entry windows now — {top_ticker} leads")

    cards = []
    for e in entries:
        ticker = _html.escape(str(e.get("ticker") or ""))
        drop   = e.get("intraday_drop_pct")
        cur    = e.get("current_price")
        opn    = e.get("open_price")
        comp   = e.get("composite_score")
        sector = _html.escape(str(e.get("sector") or ""))
        sz     = e.get("sizing") or {}
        _lo, _hi_p = sz.get("entry_lo"), sz.get("entry_hi")
        _stop  = sz.get("stop")
        _cap_note = _sizing_cap_note(sz)

        price_line = ""
        if cur is not None and opn is not None and drop is not None:
            _drop_f, _cur_f, _opn_f = float(drop), float(cur), float(opn)
            price_line = f"<b style='color:#f87171'>{_drop_f:.1f}%</b> from open (${_opn_f:.2f}) → now <b style='color:#e5e7eb'>${_cur_f:.2f}</b>"

        entry_bits = []
        if _lo is not None and _hi_p is not None:
            entry_bits.append(f"original zone ${float(_lo):.2f}–${float(_hi_p):.2f}")
        if _stop is not None:
            entry_bits.append(f"stop ${float(_stop):.2f}")
        entry_str = "  ·  ".join(entry_bits)

        cards.append(f"""
        <div style="border-left:4px solid #22c55e;background:#1c1917;border-radius:0 6px 6px 0;
                    padding:12px 16px;margin:0 0 10px 0;font-family:Arial,Helvetica,sans-serif">
          <div style="color:#22c55e;font-weight:700;font-size:13px;letter-spacing:.3px">
            📉 PULLBACK ENTRY WINDOW &nbsp;·&nbsp; <span style="color:#e5e7eb">{ticker}</span>
            {f'<span style="color:#9ca3af;font-weight:400">&nbsp;·&nbsp;{sector}</span>' if sector else ''}
            {f'<span style="color:#9ca3af;font-weight:400">&nbsp;·&nbsp;composite {float(comp):.0f}/100</span>' if comp is not None else ''}
          </div>
          {f'<div style="color:#f1f5f9;font-size:14px;margin-top:6px">{price_line}</div>' if price_line else ''}
          {f'<div style="color:#cbd5e1;font-size:12px;margin-top:4px">{_html.escape(entry_str)}</div>' if entry_str else ''}
          <div style="color:#fbbf24;font-size:12px;margin-top:3px">
            ⚠️ Price moves fast — verify live before acting.
          </div>
          {_cap_note}
        </div>""")

    spy_line = ""
    if spy_drop is not None:
        spy_line = f"SPY {float(spy_drop):+.1f}% intraday — broad market not in freefall."

    drift_html    = _book_drift_banner(book_drift)
    macro_cov_html = _macro_coverage_banner(macro_coverage_expired)

    body = f"""<!DOCTYPE html><html><body style="background:#0c0a09;padding:20px;margin:0">
      <div style="max-width:640px;margin:0 auto">
        <div style="font-family:Arial,Helvetica,sans-serif;color:#f9fafb;font-size:18px;
                    font-weight:700;margin-bottom:2px">DRISHTA · Intraday Entry Window</div>
        <div style="font-family:Arial,Helvetica,sans-serif;color:#9ca3af;font-size:12px;
                    margin-bottom:16px">
          {_html.escape(str(built_at))[:19]} ET
          {f'&nbsp;·&nbsp;{_html.escape(spy_line)}' if spy_line else ''}
        </div>
        {drift_html}
        {macro_cov_html}
        {''.join(cards)}
        <div style="font-family:Arial,Helvetica,sans-serif;color:#6b7280;font-size:11px;
                    margin-top:18px;border-top:1px solid #292524;padding-top:10px">
          These names cleared all gates this morning (composite + tone + sector + macro). The
          intraday dip may offer a better entry price than the morning scan's open. <b>Verify the
          price is still near the current level before acting — this email may be minutes old.</b>
          Advisory only — you place the trade. Original morning entry zone shown for reference.
        </div>
      </div>
    </body></html>"""
    return subject, body


def _email_md_inline(text: str) -> str:
    """Escape for HTML, then convert **bold** and *italic* markdown to inline
    HTML. Order is load-bearing (matches util.md_bold_to_html): escape FIRST
    so `**`/`*` markers (no HTML metacharacters) survive intact, then convert
    — converting first would let the escape mangle the tags just produced.
    2026-09-01 audit finding: this ran on raw `text` with no escaping."""
    text = _html.escape(text, quote=True)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*\s][^*]*?)\*",  r"<em>\1</em>",      text)
    return text


def _email_section(title: str, content: str, section_colours: dict) -> str:
    """Render one named section block for DRISHTA HTML emails."""
    if not content:
        return ""
    accent = section_colours.get(title, "#6b7280")
    parts: list[str] = []
    in_list = False
    for ln in content.split("\n"):
        stripped = ln.strip()
        if stripped.startswith(("• ", "- ", "* ")):
            item = _email_md_inline(stripped[2:])
            if not in_list:
                parts.append("<ul style='margin:8px 0;padding-left:20px;color:#374151'>")
                in_list = True
            parts.append(f"<li style='margin:6px 0;line-height:1.55;color:#374151'>{item}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            if stripped:
                parts.append(
                    f"<p style='margin:6px 0;color:#374151;line-height:1.6'>"
                    f"{_email_md_inline(stripped)}</p>"
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


def render_debrief_email(debrief: dict, week_had_trades: bool = False, prior: dict | None = None) -> str:
    """Render the weekly portfolio debrief as a professional HTML email (light-mode-first).

    week_had_trades: True when a BUY/SELL landed inside the joined week — the
    equity-value tiles then reflect position-size changes as well as price
    moves, not price performance alone (same class of caveat as the Summary
    page's Alpha vs SPY tile, added 2026-07-26).
    prior: last week's saved weekly_debriefs row (or None), for a week-over-week
    alpha trend annotation on the Alpha tile. The delta is computed here in code,
    not narrated by the LLM — arithmetic on two numbers has zero business being
    left to a text-generation model when Python can just do it exactly.
    """

    week_ending  = debrief.get("week_ending", "—")
    generated_at = str(debrief.get("generated_at", ""))[:10]
    perf  = debrief.get("performance_pct")
    spy   = debrief.get("spy_pct")
    alpha = debrief.get("alpha_pct")

    alpha_subtitle = "vs benchmark"
    if prior is not None:
        prior_alpha = prior.get("alpha_pct")
        if (alpha is not None and prior_alpha is not None
                and not (isinstance(alpha, float) and alpha != alpha)
                and not (isinstance(prior_alpha, float) and prior_alpha != prior_alpha)):
            _delta = float(alpha) - float(prior_alpha)
            _arrow = "▲" if _delta >= 0 else "▼"
            alpha_subtitle = f"vs benchmark &nbsp;·&nbsp; {_arrow} {_delta:+.1f}pp vs last wk"

    def _pct_cell(label: str, v: float | None, bold: bool = False, subtitle: str = "") -> str:
        if v is None or (isinstance(v, float) and v != v):   # NaN != NaN
            v = None
        if v is None:
            val_html = "<span style='color:#6b7280'>N/A</span>"
        else:
            colour   = "#16a34a" if v >= 0 else "#dc2626"
            weight   = "700" if bold else "600"
            val_html = f"<span style='color:{colour};font-weight:{weight}'>{v:+.1f}%</span>"
        lw = "700" if bold else "400"
        sub_html = (f"<div style='font-size:0.68em;color:#9ca3af;margin-top:3px'>{subtitle}</div>"
                    if subtitle else "")
        return (
            f"<td style='padding:14px 20px;text-align:center;border-right:1px solid #e5e7eb'>"
            f"<div style='font-size:0.75em;color:#6b7280;text-transform:uppercase;"
            f"letter-spacing:0.05em;font-weight:600;margin-bottom:4px'>{label}</div>"
            f"<div style='font-size:1.4em;font-weight:{lw}'>{val_html}</div>"
            f"{sub_html}"
            f"</td>"
        )

    perf_block = ""
    if perf is not None:
        # The trades caveat renders BEFORE the KPI tiles (not after) — a reader
        # sees the eye-catching % numbers first regardless of layout order, so
        # the caveat needs to land ahead of them to actually frame the read,
        # not trail behind it as a footnote nobody scrolls back up for.
        if week_had_trades:
            perf_block += (
                "<p style='font-size:0.75em;color:#b45309;background:#fffbeb;"
                "border:1px solid #fde68a;border-radius:6px;padding:8px 12px;"
                "margin:0 0 4px;text-align:center'>"
                "⚠️ Trades occurred this week — the figures below reflect position-size "
                "changes as well as price moves, not price performance alone."
                "</p>"
            )
        perf_block += (
            "<table width='100%' style='border-collapse:collapse;background:#f8fafc;"
            "border:1px solid #e5e7eb;border-radius:8px;margin:20px 0 4px;overflow:hidden'>"
            "<tr>"
            + _pct_cell("Portfolio", perf, bold=True, subtitle="this week · equity positions")
            + _pct_cell("S&P 500", spy, subtitle="this week · SPY")
            + _pct_cell("Alpha (unadjusted)", alpha, bold=True, subtitle=alpha_subtitle).replace("border-right:1px solid #e5e7eb", "border-right:none")
            + "</tr></table>"
            "<p style='font-size:0.72em;color:#9ca3af;margin:0 0 20px;text-align:center'>"
            "Weekly change in equity position value (Mon&#8202;–&#8202;Fri). "
            "Includes new positions opened this week. "
            "Different from Account page&#8202;'s all-time money-weighted return."
            "</p>"
        )

    # Section accent colours (left-border strip)
    _SECTION_COLOURS = {
        "What happened":       "#3b82f6",   # blue
        "Decisions you made":  "#8b5cf6",   # purple
        "Patterns this week":  "#f59e0b",   # amber
        "One thing to watch":  "#10b981",   # green
    }

    _section = lambda t, c: _email_section(t, c, _SECTION_COLOURS)

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


def render_intelligence_email(report: dict) -> str:
    """Render the monthly Portfolio Intelligence Report as a professional HTML
    email (light-mode-first; same template family as render_debrief_email)."""
    period_start = str(report.get("period_start", "—"))[:10]
    period_end   = str(report.get("period_end", "—"))[:10]
    generated_at = str(report.get("generated_at", ""))[:10]
    engine_alpha = report.get("engine_alpha_pct")
    acted        = report.get("acted_count")
    missed       = report.get("missed_count")

    # Headline metric strip — engine alpha (Q0) + acted/missed (Q1)
    def _alpha_html(v) -> str:
        if v is None or (isinstance(v, float) and v != v):   # NaN != NaN
            v = None
        if v is None:
            return "<span style='color:#6b7280'>N/A</span>"
        colour = "#16a34a" if v >= 0 else "#dc2626"
        return f"<span style='color:{colour};font-weight:700'>{v:+.1f}%</span>"

    def _metric_cell(label: str, inner: str, last: bool = False) -> str:
        border = "" if last else "border-right:1px solid #e5e7eb"
        return (
            f"<td style='padding:14px 20px;text-align:center;{border}'>"
            f"<div style='font-size:0.72em;color:#6b7280;text-transform:uppercase;"
            f"letter-spacing:0.05em;font-weight:600;margin-bottom:4px'>{label}</div>"
            f"<div style='font-size:1.3em;font-weight:700'>{inner}</div></td>"
        )

    perf_block = (
        "<table width='100%' style='border-collapse:collapse;background:#f8fafc;"
        "border:1px solid #e5e7eb;border-radius:8px;margin:20px 0;overflow:hidden'><tr>"
        + _metric_cell("Engine alpha (acted)", _alpha_html(engine_alpha))
        + _metric_cell("Acted on", f"{acted if acted is not None else '—'}")
        + _metric_cell("Missed", f"{missed if missed is not None else '—'}", last=True)
        + "</tr></table>"
    )

    _SECTION_COLOURS = {
        "Entry quality":     "#3b82f6",   # blue — the engine's half
        "Signal discipline": "#8b5cf6",   # purple — the user's half
        "Pattern & focus":   "#f59e0b",   # amber
    }

    _section = lambda t, c: _email_section(t, c, _SECTION_COLOURS)

    sections_html = (
        _section("Entry quality",     report.get("section_entry_quality", ""))
        + _section("Signal discipline", report.get("section_signal_discipline", ""))
        + _section("Pattern & focus",   report.get("section_patterns", ""))
    )

    return (
        "<!DOCTYPE html><html lang='en'>"
        "<head><meta charset='UTF-8'><meta name='viewport' content='width=device-width'></head>"
        "<body style='background:#f3f4f6;color:#111827;font-family:-apple-system,BlinkMacSystemFont,"
        "\"Segoe UI\",Roboto,sans-serif;margin:0;padding:32px 16px'>"
        "<div style='max-width:600px;margin:0 auto;background:#ffffff;"
        "border-radius:12px;box-shadow:0 1px 8px rgba(0,0,0,0.08);overflow:hidden'>"
        # Header band
        "<div style='background:linear-gradient(135deg,#1e3a5f 0%,#1e40af 100%);padding:28px 28px 22px'>"
        "<div style='font-size:1.35em;font-weight:800;color:#ffffff;letter-spacing:-0.01em'>"
        "DRISHTA Monthly Intelligence</div>"
        f"<div style='color:#93c5fd;font-size:0.82em;margin-top:4px'>"
        f"{period_start} → {period_end} &nbsp;·&nbsp; Generated {generated_at}</div></div>"
        # Body
        f"<div style='padding:24px 24px 20px'>{perf_block}{sections_html}</div>"
        # Footer
        "<div style='background:#f9fafb;border-top:1px solid #e5e7eb;padding:12px 24px;"
        "font-size:0.75em;color:#9ca3af;text-align:center'>"
        "DRISHTA &nbsp;·&nbsp; AI-generated retrospective &nbsp;·&nbsp; "
        "Surfaces patterns; never changes a gate &nbsp;·&nbsp; Not financial advice</div>"
        "</div></body></html>"
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


def render_liveness_email(
    sweep: "dict | None",
    shelf_down: "list[dict]",
    shelf_warn: "list[dict]",
    built_at: str,
) -> "tuple[str, str]":
    """Email (subject, html) for the weekly ticker-liveness and shelf-life check.

    Called from the Saturday maintenance cron lane when: a dead ticker was
    confirmed, the batch was inconclusive, the sweep raised (None), or a
    reference table has expired (severity == "down").

    Chore/awareness only — never gates a recommendation, never suppresses a pick.

    STYLING: dark-on-light (same as render_db_outage_email) because email clients
    strip <body> styling, leaving near-white text invisible on a white background —
    verified live on 2026-08-16.  Do NOT align this to the other dark-body renderers
    without re-testing in a real inbox.

    Pure string building: no DB, no Streamlit, no network.
    """
    from stock_analyzer.util import safe_html as _sh
    from stock_analyzer.constants import TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT

    _dead_list = sweep.get("dead") if sweep is not None else None
    n_dead = len(_dead_list) if _dead_list is not None else 0
    n_shelf_down = len(shelf_down)

    # ── Subject ───────────────────────────────────────────────────────────────
    if sweep is None:
        subject = "DRISHTA · Maintenance: roster-liveness check failed — no verdict this week"
    elif sweep.get("status") == "inconclusive":
        subject = "DRISHTA · Maintenance: roster-liveness inconclusive — provider may be degraded"
    elif n_dead and n_shelf_down:
        subject = (
            f"DRISHTA · Maintenance: {n_dead} dead ticker{'s' if n_dead != 1 else ''}"
            f" + {n_shelf_down} expired table{'s' if n_shelf_down != 1 else ''}"
            f" — curation needed"
        )
    elif n_dead:
        subject = (
            f"DRISHTA · Maintenance: {n_dead} dead ticker{'s' if n_dead != 1 else ''}"
            f" found in rosters — curation needed"
        )
    elif n_shelf_down:
        subject = (
            f"DRISHTA · Maintenance: {n_shelf_down} reference"
            f" table{'s' if n_shelf_down != 1 else ''} expired — extend now"
        )
    else:
        # Unreachable via cron_runner, which only calls this when there IS a
        # finding. Guarded anyway so a future caller can't ship the nonsense
        # subject "0 reference tables expired".
        subject = "DRISHTA · Maintenance: roster-liveness report"

    # ── Sweep section ─────────────────────────────────────────────────────────
    if sweep is None:
        sweep_html = """
        <div style="border-left:3px solid #dc2626;background:#fef2f2;border-radius:0 4px 4px 0;
                    padding:10px 14px;margin:0 0 14px 0">
          <div style="color:#991b1b;font-weight:700;font-size:13px">
            Ticker-liveness batch check could not run
          </div>
          <div style="color:#7f1d1d;font-size:12px;margin-top:4px">
            The batch download raised an exception before producing any result.
            There is no verdict this week — the rosters may or may not contain
            stale tickers.  Check the Railway maintenance cron-service logs for
            the traceback.  Silence would have been indistinguishable from a
            clean run.
          </div>
        </div>"""
    elif sweep.get("status") == "inconclusive":
        hp = sweep.get("health_pct", 0.0)
        suspects_n = sweep.get("suspects_n", 0)
        roster_n = sweep.get("roster_n", 0)
        sweep_html = f"""
        <div style="border-left:3px solid #d97706;background:#fffbeb;border-radius:0 4px 4px 0;
                    padding:10px 14px;margin:0 0 14px 0">
          <div style="color:#92400e;font-weight:700;font-size:13px">
            Ticker-liveness sweep inconclusive
          </div>
          <div style="color:#78350f;font-size:12px;margin-top:4px">
            Batch health {_sh(f'{hp:.1f}%')} — only
            {_sh(str(roster_n - suspects_n))} of {_sh(str(roster_n))} roster
            tickers returned price data ({_sh(str(suspects_n))} suspect).
            This is below the {_sh(f'{TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT:.0f}%')}
            threshold required to trust the verdict.  The data provider was likely
            rate-limited or temporarily degraded.  No dead-ticker conclusion is
            drawn; the check will re-run next Saturday.  Silence would have been
            indistinguishable from a clean run — this email states there is no verdict.
          </div>
        </div>"""
    elif sweep.get("dead"):
        rows = ""
        for d in sweep["dead"]:
            tk = _sh(str(d.get("ticker") or ""))
            _rosters = d.get("rosters")
            rosters = _sh(", ".join(sorted(_rosters if _rosters is not None else [])))
            rows += f"""
            <div style="border-bottom:1px solid #fecaca;padding:6px 0">
              <span style="font-family:monospace;color:#b91c1c;font-weight:700">{tk}</span>
              <span style="color:#3f3f46;font-size:12px;margin-left:8px">
                appears in: {rosters}
              </span>
            </div>"""
        sweep_html = f"""
        <div style="margin:0 0 14px 0">
          <div style="color:#b91c1c;font-weight:700;font-size:13px;margin-bottom:6px">
            {n_dead} dead ticker{'s' if n_dead != 1 else ''} confirmed
          </div>
          <div style="background:#fef2f2;border:1px solid #fecaca;
                      border-radius:4px;padding:10px 14px">
            {rows}
          </div>
          <div style="color:#52525b;font-size:12px;margin-top:8px">
            Each ticker returned no price from any provider (Finnhub, yfinance,
            FMP).  Remove it from the roster(s) listed above and update the
            shelf-registry date in reference_shelf.py.
          </div>
        </div>"""
    else:
        hp = sweep.get("health_pct", 0.0)
        roster_n = sweep.get("roster_n", 0)
        sweep_html = f"""
        <div style="border-left:3px solid #16a34a;background:#f0fdf4;
                    border-radius:0 4px 4px 0;padding:10px 14px;margin:0 0 14px 0">
          <div style="color:#15803d;font-weight:700;font-size:13px">
            All {_sh(str(roster_n))} roster tickers alive
            (batch health {_sh(f'{hp:.1f}%')})
          </div>
        </div>"""

    # ── Shelf-down section ────────────────────────────────────────────────────
    shelf_down_html = ""
    if shelf_down:
        rows = ""
        for r in shelf_down:
            label = _sh(str(r.get("label") or r.get("key") or ""))
            detail = _sh(str(r.get("detail") or ""))
            loc = _sh(str(r.get("location") or ""))
            consequence = _sh(str(r.get("consequence") or ""))
            rows += f"""
            <div style="border-bottom:1px solid #fecaca;padding:8px 0">
              <div style="color:#991b1b;font-weight:700;font-size:13px">{label}</div>
              <div style="color:#7f1d1d;font-size:12px;margin-top:2px">{detail}</div>
              <div style="color:#52525b;font-size:12px;margin-top:2px">
                Location: <code>{loc}</code>
              </div>
              <div style="color:#3f3f46;font-size:12px;margin-top:2px">
                Impact: {consequence}
              </div>
            </div>"""
        shelf_down_html = f"""
        <div style="margin:0 0 14px 0">
          <div style="color:#b91c1c;font-weight:700;font-size:13px;margin-bottom:6px">
            {n_shelf_down} expired reference table{'s' if n_shelf_down != 1 else ''}
          </div>
          <div style="background:#fef2f2;border:1px solid #fecaca;
                      border-radius:4px;padding:10px 14px">
            {rows}
          </div>
        </div>"""

    # ── Shelf-warn section (secondary: included only when already emailing) ───
    shelf_warn_html = ""
    if shelf_warn:
        rows = ""
        for r in shelf_warn:
            sev = _sh(str(r.get("severity") or ""))
            label = _sh(str(r.get("label") or r.get("key") or ""))
            detail = _sh(str(r.get("detail") or ""))
            color = "#d97706" if r.get("severity") == "warn" else "#6b7280"
            rows += f"""
            <div style="border-bottom:1px solid #e4e4e7;padding:6px 0">
              <span style="color:{color};font-size:12px;font-weight:600">
                {sev.upper()}
              </span>
              <span style="color:#3f3f46;font-size:12px;margin-left:6px">
                {label}: {detail}
              </span>
            </div>"""
        shelf_warn_html = f"""
        <div style="margin:0 0 14px 0;color:#3f3f46;font-size:12px">
          <div style="font-weight:600;margin-bottom:6px">
            Reference tables approaching expiry
          </div>
          <div style="background:#fafaf9;border:1px solid #e4e4e7;
                      border-radius:4px;padding:10px 14px">
            {rows}
          </div>
        </div>"""

    body = f"""<!DOCTYPE html><html><body style="margin:0;padding:20px;background:#f4f4f5">
      <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #d4d4d8;
                  border-radius:8px;padding:22px;font-family:Arial,Helvetica,sans-serif">
        <div style="color:#18181b;font-size:18px;font-weight:700">
          DRISHTA · Weekly Roster-Liveness Check
        </div>
        <div style="color:#52525b;font-size:12px;margin-top:4px;margin-bottom:16px">
          Chore report · built {_sh(str(built_at))[:19]} ET ·
          awareness only, never gates a recommendation
        </div>
        {sweep_html}
        {shelf_down_html}
        {shelf_warn_html}
        <div style="color:#71717a;font-size:11px;margin-top:16px;
                    border-top:1px solid #e4e4e7;padding-top:10px">
          Saturday maintenance lane · runs weekly.  You are receiving this
          because the liveness check found something (or could not produce a
          verdict) — silence is indistinguishable from health.
        </div>
      </div>
    </body></html>"""
    return subject, body
