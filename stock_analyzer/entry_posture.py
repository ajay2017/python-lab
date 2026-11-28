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

from stock_analyzer import buy_check_state
from stock_analyzer import entry_eligibility
from stock_analyzer.constants import (
    COMPOSITE_BUY,
    EARNINGS_IMMINENT_DAYS,
    MARKET_TONE_BEAR_PCT,
    RR_ENTRY_MIN,
    RR_FAVOURABLE_MIN,
)
from stock_analyzer.entry_eligibility import (
    classify_tone,
    data_trust_hold,
    resolve_tone_state,
    tone_source_line,
)
from stock_analyzer.rr_readout import fmt_ratio_vs
from stock_analyzer.score_text import composite_score_text
from stock_analyzer.tone_freshness import STATE_FAILED, WHY_HOME_FAILED, home_read_state

_UNAVAILABLE = {"tone": None, "sp500_pct": None, "available": False, "source": None, "as_of": None}

_TONE_TO_POSTURE = {"bull": "open", "flat": "confirmed_only", "bear": "deferred"}

# Sources meaning "this read is Home's" (its live Brief).
_HOME_SOURCES = ("home",)


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
        "why": ts.get("why") if posture != "unknown" else None,
        "home_read_at": ts.get("home_read_at") if posture != "unknown" else None,
    }


def page_home_state(home_synth, today_iso) -> str:
    """How Home's Brief stands for today's tone, from ``_home_synth_cache``:
    ``HOME_NOT_TODAY`` unless built today (dict with a dict bundle), else
    ``entry_eligibility.home_tone_state`` of its market context.  Never raises."""
    try:
        if (
            isinstance(home_synth, dict)
            and home_synth.get("date") == today_iso
            and isinstance(home_synth.get("bundle"), dict)
        ):
            return entry_eligibility.home_tone_state(
                home_synth["bundle"].get("_market_context"), True)
    except Exception:
        pass
    return entry_eligibility.HOME_NOT_TODAY


def tone_state_for_page(home_synth, today_iso, live_pct, live_ok, *,
                        now_et=None, live_read_at=None) -> dict:
    """Tone state for a page that isn't Home.

    ``now_et=None`` (the original rule): today's ``_home_synth_cache`` market
    context when it carries a readable tone; unavailable when Home was built
    today but its tone read failed (a live read must not call an entry Grow Today
    didn't make); else the live S&P read when Home isn't built today; else
    unavailable.

    With ``now_et`` (buy-check C4a, ToneNoAge): Home's read is used only while it
    is usable (``tone_freshness.home_read_state``: under
    ``TONE_HOME_READ_MAX_AGE_MINUTES`` and the same session); a Home read that
    is aged / from another session / has no readable time falls to the live read
    (``live_pct`` / ``live_ok`` / ``live_read_at``), and to unavailable when that
    also fails.  Home built today with a FAILED read stays unavailable with
    ``why == "home_failed"`` and never takes the live read.  Never raises."""
    try:
        home_state = page_home_state(home_synth, today_iso)
        if home_state == entry_eligibility.HOME_TODAY_TONE_FAILED:
            return {**_UNAVAILABLE, "why": WHY_HOME_FAILED}
        if home_state != entry_eligibility.HOME_NOT_TODAY:
            ctx = home_synth["bundle"].get("_market_context")
            if now_et is None:
                st = resolve_tone_state(
                    ctx, True, ctx.get("tone_read_at") if isinstance(ctx, dict) else None,
                    None, False)
                if st.get("available"):
                    return st
                # Built today but the tone is unusable (e.g. NaN S&P change):
                # never fall back to the live read.
                return dict(_UNAVAILABLE)
            if home_read_state(ctx, True, now_et)["state"] == STATE_FAILED:
                # Built today but the tone is unusable (e.g. NaN S&P change).
                return {**_UNAVAILABLE, "why": WHY_HOME_FAILED}
            return resolve_tone_state(
                ctx, True, ctx.get("tone_read_at") if isinstance(ctx, dict) else None,
                live_pct, live_ok, now_et=now_et, live_read_at=live_read_at)
        return resolve_tone_state(None, False, None, live_pct, live_ok,
                                  now_et=now_et, live_read_at=live_read_at)
    except Exception:
        return dict(_UNAVAILABLE)


def page_needs_live(state) -> bool:
    """True when a page should try a live S&P read: the tone state is
    unavailable for any reason except Home's own read having failed (since
    buy-check C4b Home's Grow Today then holds every entry, so a live read must
    not call an entry Home held)."""
    if not isinstance(state, dict):
        return True
    return state.get("available") is not True and state.get("why") != WHY_HOME_FAILED


def _fmt_pct(posture: dict) -> str:
    """Signed S&P change at 2 dp, widened (3 then 4 dp) when the 2-dp rounding
    would land on the other side of a tone boundary from the true value."""
    v = posture["sp500_pct"]
    for dp in (2, 3, 4):
        txt = f"{v:+.{dp}f}"
        if classify_tone(float(txt)) == classify_tone(v):
            break
    return txt


def home_grow_hold(home_synth, today_iso):
    """🏠 Home's Brief `grow_today["entry_hold"]` (buy-check C4b) from
    ``_home_synth_cache`` when it was built today, else None.  A dict means Grow
    Today held every entry; None means not held OR not known (Home not built
    today): the caller's own tone rule then decides.  Never raises."""
    try:
        if (
            isinstance(home_synth, dict)
            and home_synth.get("date") == today_iso
            and isinstance(home_synth.get("bundle"), dict)
        ):
            brief = home_synth["bundle"].get("_daily_brief")
            gt = brief.get("grow_today") if isinstance(brief, dict) else None
            hold = gt.get("entry_hold") if isinstance(gt, dict) else None
            return hold if isinstance(hold, dict) else None
    except Exception:
        pass
    return None


def _redeploy_earnings_text(bundle, today) -> "str | None":
    """Why the named ticker's EARNINGS leg keeps the redeploy headline from calling an
    entry (buy-check C3b, owner Q9), else None: "earnings today" / "earnings in Nd" for an
    imminent date (0 to EARNINGS_IMMINENT_DAYS days), or "its next earnings date couldn't
    be confirmed" when ``buy_check_state.earnings_unknown_hold`` holds it. ``today`` is
    the caller's ET date; None (or no bundle) skips the leg, so every caller that does
    not pass it reads as before. An unreadable bundle fails CLOSED. Never raises."""
    if bundle is None or today is None:
        return None
    try:
        raw = bundle.get("earnings") if isinstance(bundle, dict) else None
        d = entry_eligibility._days_until(raw, today) if raw else None
        if d is not None and 0 <= d <= EARNINGS_IMMINENT_DAYS:
            return "earnings are today" if d == 0 else f"earnings are in {d}d"
        if buy_check_state.earnings_unknown_hold(bundle, d) is not None:
            return "its next earnings date couldn't be confirmed"
        return None
    except Exception:
        return "its next earnings date couldn't be checked"


def redeploy_call(headline, posture, *, bundle=None, site: str = "home",
                  home_state=None, grow_hold=None, today=None) -> dict:
    """What the redeploy headline may say given today's entry posture.

    ``headline`` is a ``rr_readout.redeploy_headline`` dict; ``posture`` is the
    dict ``new_entry_posture`` returns (it carries the tone / source used for
    the source line); ``bundle`` is the named ticker's data bundle (optional).

    Returns ``{"state", "entry_called", "level", "icon", "text",
    "invite_own_pick", "source_line"}``.  ``text`` is None when the caller
    should keep its existing per-state wording (every non-"entry" state);
    ``entry_called`` is True only for an "entry" headline on an open posture
    with no data-trust hold on the ticker.  Never raises: a malformed posture
    reads as "unknown" (withhold).

    ``grow_hold`` (buy-check C4b, owner rule #14: the headline defers to Grow Today)
    is the hold dict from ``tone_freshness.redeploy_grow_hold``: when Grow Today
    holds every entry (market move unread / not today's session) an "entry"
    headline on an open or flat posture is NOT an entry call, and says why.  A
    risk-off posture keeps its Protect Mode text.

    ``today`` (buy-check C3b, owner Q9) is the caller's ET date: with it, an "entry"
    headline on an open posture whose ticker has earnings within EARNINGS_IMMINENT_DAYS,
    or whose next earnings date couldn't be confirmed, is NOT called as an entry and says
    so. ``None`` (the default) skips that leg."""
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
            "why": p.get("why"),
            "home_read_at": p.get("home_read_at"),
        }, home_state)
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
    earn_txt = None
    if kind == "open" and bundle is not None:
        try:
            hold = data_trust_hold(bundle)
        except Exception:
            hold_failed = True  # an unreadable bundle can't be confirmed clean
        earn_txt = _redeploy_earnings_text(bundle, today)

    try:
        t = h.get("ticker")
        sec = f" ({h['sector']})" if h.get("sector") else ""
        s = composite_score_text(h.get("score"))
        if s == "n/a":
            # An unreadable score must still fail closed (the old ":.0f" raised
            # here and the except below withheld the entry call).
            raise ValueError("score unreadable")
        rr = fmt_ratio_vs(h.get("rr"), RR_ENTRY_MIN, RR_FAVOURABLE_MIN)
        if isinstance(grow_hold, dict) and kind in ("open", "confirmed_only"):
            why = str(grow_hold.get("reason") or "the market move isn't usable").strip()
            clears = str(grow_hold.get("clears_when") or "").strip()
            out.update(level="info", icon="⏸", text=(
                f"**{t}**{sec} clears the Buy gate ({s} ≥ {COMPOSITE_BUY:.0f} · "
                f"R:R {rr}:1 ≥ {RR_ENTRY_MIN:g}:1) on its own numbers, but 🏠 Grow "
                f"Today holds new entries: {why}."
                + (f" They return {clears}." if clears else "")))
        elif kind == "open" and hold is None and not hold_failed and earn_txt:
            out.update(level="warning", icon="⚠️", text=(
                f"**{t}**{sec} clears the Buy gate ({s} ≥ {COMPOSITE_BUY:.0f} · "
                f"R:R {rr}:1 ≥ {RR_ENTRY_MIN:g}:1) on its own numbers, but {earn_txt}, "
                "so it isn't called as an entry here."))
        elif kind == "open" and hold is None and not hold_failed:
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
                + (", matching 🏠 Home" if p.get("source") in _HOME_SOURCES else "")
                + f". **{t}**{sec} clears both bars "
                f"({s} ≥ {COMPOSITE_BUY:.0f} · R:R {rr}:1) on its own numbers; it isn't "
                "an entry today."
                + (" Trimming is still sound." if site == "home" else "")))
        else:
            out.update(level="warning", icon="⚠️", text=(
                f"Market tone couldn't be read, so **{t}** ({h['sector'] + '; ' if h.get('sector') else ''}{s} ≥ "
                f"{COMPOSITE_BUY:.0f} · R:R {rr}:1) isn't confirmed as an entry here. "
                "🏠 Grow Today holds new picks until the market move can be read. "
                + ("Reload to retry." if site == "home" else "Open 🏠 Home to retry.")))
    except Exception:
        out.update(
            entry_called=False, level="warning", icon="⚠️", invite_own_pick=False,
            text="This name isn't confirmed as an entry here. Grow Today makes today's buy calls.")
    return out
