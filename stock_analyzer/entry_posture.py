"""Whether a redeploy headline may call a NEW ENTRY on today's market tone.

Grow Today owns every buy call (owner D1, 2026-10-15): it makes no new entries
on a bear day and raises its bar for new picks on a flat day.  The
Diversification Advisor / Rebalancer redeploy headline
(``rr_readout.redeploy_headline``) knows only the composite Buy gate and R:R,
so on a non-bull day it could say "a genuine redeploy entry" while Grow Today
says wait.  This module is the single place that reconciles the two: a
*posture* (open / confirmed_only / deferred / unknown), never a number.

Rules (owner decisions 2026-10-29): only a readable bull tone with no
data-trust hold on the named ticker lets the headline call an entry; an
unreadable tone withholds (never defaults to bull).  The candidate list, its
ranking and its icons are untouched; only the headline sentence changes.

Pure: no clock, no app / db imports; every threshold comes from ``constants``.
"""
from __future__ import annotations

import math

from stock_analyzer.constants import COMPOSITE_BUY, MARKET_TONE_BEAR_PCT, RR_ENTRY_MIN
from stock_analyzer.entry_eligibility import (
    classify_tone,
    data_trust_hold,
    resolve_tone_state,
    tone_source_line,
)
from stock_analyzer.rr_readout import fmt_ratio_vs

_UNAVAILABLE = {"tone": None, "sp500_pct": None, "available": False, "source": None, "as_of": None}

_TONE_TO_POSTURE = {"bull": "open", "flat": "confirmed_only", "bear": "deferred"}


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def new_entry_posture(tone_state) -> dict:
    """Map a ``resolve_tone_state`` result to a posture dict:
    ``{"posture", "tone", "sp500_pct", "source", "as_of"}``.

    bull -> "open"; flat -> "confirmed_only"; bear -> "deferred"; anything
    unreadable (not a dict, ``available`` not True, unknown tone, non-finite
    S&P change) -> "unknown"."""
    ts = tone_state if isinstance(tone_state, dict) else {}
    tone = ts.get("tone")
    pct = ts.get("sp500_pct")
    posture = "unknown"
    if ts.get("available") is True and tone in _TONE_TO_POSTURE and _finite(pct):
        posture = _TONE_TO_POSTURE[tone]
    return {
        "posture": posture,
        "tone": tone if posture != "unknown" else None,
        "sp500_pct": pct if posture != "unknown" else None,
        "source": ts.get("source") if posture != "unknown" else None,
        "as_of": ts.get("as_of") if posture != "unknown" else None,
    }


def tone_state_for_page(home_synth, today_iso, live_pct, live_ok) -> dict:
    """Tone state for a page that isn't Home: today's ``_home_synth_cache``
    market context when it carries a readable tone, else the live S&P read,
    else unavailable.  Never raises."""
    try:
        if (
            isinstance(home_synth, dict)
            and home_synth.get("date") == today_iso
            and isinstance(home_synth.get("bundle"), dict)
        ):
            ctx = home_synth["bundle"].get("_market_context")
            st = resolve_tone_state(
                ctx, True, ctx.get("tone_read_at") if isinstance(ctx, dict) else None,
                None, False)
            if st.get("available"):
                return st
        return resolve_tone_state(None, False, None, live_pct, live_ok)
    except Exception:
        return dict(_UNAVAILABLE)


def _fmt_pct(posture: dict) -> str:
    """Signed S&P change at 2 dp, widened (3 then 4 dp) when the 2-dp rounding
    would land on the other side of a tone boundary from the true value."""
    v = posture["sp500_pct"]
    for dp in (2, 3, 4):
        txt = f"{v:+.{dp}f}"
        if classify_tone(float(txt)) == classify_tone(v):
            break
    return txt


def redeploy_call(headline, posture, *, bundle=None, site: str = "home") -> dict:
    """What the redeploy headline may say given today's entry posture.

    ``headline`` is a ``rr_readout.redeploy_headline`` dict; ``posture`` is the
    dict ``new_entry_posture`` returns (it carries the tone / source used for
    the source line); ``bundle`` is the named ticker's data bundle (optional).

    Returns ``{"state", "entry_called", "level", "icon", "text",
    "invite_own_pick", "source_line"}``.  ``text`` is None when the caller
    should keep its existing per-state wording (every non-"entry" state);
    ``entry_called`` is True only for an "entry" headline on an open posture
    with no data-trust hold on the ticker.  Never raises: a malformed posture
    reads as "unknown" (withhold)."""
    h = headline if isinstance(headline, dict) else {}
    state = h.get("state")
    p = posture if isinstance(posture, dict) else {}
    kind = p.get("posture")
    if kind not in ("open", "confirmed_only", "deferred"):
        kind = "unknown"

    source_line = None
    try:
        source_line = tone_source_line({
            "available": kind != "unknown",
            "source": p.get("source"),
            "as_of": p.get("as_of"),
        })
    except Exception:
        source_line = None

    out = {
        "state": state, "entry_called": False, "level": None, "icon": None,
        "text": None, "invite_own_pick": kind == "open", "source_line": source_line,
    }
    if state != "entry":
        return out

    out["invite_own_pick"] = False
    hold = None
    hold_failed = False
    if kind == "open" and bundle is not None:
        try:
            hold = data_trust_hold(bundle)
        except Exception:
            hold_failed = True  # an unreadable bundle can't be confirmed clean

    try:
        t = h.get("ticker")
        sec = f" ({h['sector']})" if h.get("sector") else ""
        s = f"{h.get('score'):.0f}"
        rr = fmt_ratio_vs(h.get("rr"), RR_ENTRY_MIN)
        if kind == "open" and hold is None and not hold_failed:
            out.update(
                entry_called=True, level="success", icon="🎯", invite_own_pick=True,
                text=(
                    f"**{t}**{sec} clears the Buy gate "
                    f"({s} ≥ {COMPOSITE_BUY:.0f} · "
                    f"R:R {rr}:1 ≥ {RR_ENTRY_MIN:.0f}:1) "
                    "— a genuine redeploy entry, not just a sector filler."
                ),
            )
        elif kind == "open" and hold_failed:
            out.update(level="warning", icon="⚠️", text=(
                f"**{t}**{sec} clears the Buy gate ({s} ≥ {COMPOSITE_BUY:.0f}) and "
                f"R:R {rr}:1 ≥ {RR_ENTRY_MIN:g}:1, but its data couldn't be checked, "
                "so it isn't confirmed as an entry here. Check 📈 Analysis."))
        elif kind == "open":
            out.update(level="warning", icon="⚠️", text=(
                f"**{t}**{sec} clears the Buy gate ({s} ≥ {COMPOSITE_BUY:.0f}) and "
                f"R:R {rr}:1 ≥ {RR_ENTRY_MIN:g}:1, but its data needs a refresh "
                f"({hold['reason']}), so it isn't confirmed as an entry here. "
                "Check 📈 Analysis after a refresh."))
        elif kind == "confirmed_only":
            out.update(level="info", icon="⏸", text=(
                f"**{t}**{sec} clears the Buy gate ({s} ≥ {COMPOSITE_BUY:.0f}) and "
                f"R:R {rr}:1 ≥ {RR_ENTRY_MIN:g}:1 on its own numbers, but it isn't "
                f"called as an entry here today. On a mixed market (S&P {_fmt_pct(p)}%), "
                "🏠 Grow Today raises its bar for new picks, and this list doesn't "
                "run those checks. Grow Today makes today's buy calls."))
        elif kind == "deferred":
            out.update(level="warning", icon="🛡️", text=(
                f"Protect Mode — S&P {_fmt_pct(p)}% (risk-off at "
                f"{MARKET_TONE_BEAR_PCT:g}% or worse): new entries are deferred today"
                + (", matching 🏠 Home" if p.get("source") == "home" else "")
                + f". **{t}**{sec} clears both bars "
                f"({s} ≥ {COMPOSITE_BUY:.0f} · R:R {rr}:1) on its own numbers; it isn't "
                "an entry today."
                + (" Trimming is still sound." if site == "home" else "")))
        else:
            out.update(level="warning", icon="⚠️", text=(
                f"Market tone couldn't be read, so **{t}** ({h['sector'] + '; ' if h.get('sector') else ''}{s} ≥ "
                f"{COMPOSITE_BUY:.0f} · R:R {rr}:1) isn't confirmed as an entry here. "
                "🏠 Grow Today treats an unread market as a flat day. Reload to retry."))
    except Exception:
        out.update(
            entry_called=False, level="warning", icon="⚠️", invite_own_pick=False,
            text="This name isn't confirmed as an entry here. Grow Today makes today's buy calls.")
    return out
