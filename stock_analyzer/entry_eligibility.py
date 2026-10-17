"""Shared new-entry eligibility (docs/plans/shared-entry-check.md, chunk 2).

One pure place for the checks that decide whether a NEW position may be
called today: stale data, market tone, macro calendar (G-07), negative news,
earnings.  Grow Today calls the lifted pieces in place (zero behaviour
change); the Watchlist / email / Analysis surfaces call ``entry_check`` from
later chunks.

Pure: no clock reads (``now_et`` / ``today`` are always passed in), no app or
``stock_analyzer.db`` imports, every threshold imported from ``constants``.
"""
from __future__ import annotations

import math
from datetime import date, time as _time

from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS,
    GROW_TODAY_MAX_FUND_AGE_DAYS,
    MACRO_IMMINENT_DAYS,
    MARKET_TONE_BEAR_PCT,
    MARKET_TONE_BULL_PCT,
    NEWS_SENTIMENT_NEGATIVE,
)


# ── Tone ─────────────────────────────────────────────────────────────────────
def classify_tone(sp500_pct) -> str:
    """bull / bear / flat from the S&P 500 daily % change."""
    return (
        "bull" if sp500_pct >= MARKET_TONE_BULL_PCT
        else "bear" if sp500_pct <= MARKET_TONE_BEAR_PCT
        else "flat"
    )


def _finite(x) -> bool:
    """A real, finite number (NaN / inf / bool / None / str are not)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def resolve_tone_state(home_ctx, home_is_today, home_built_at, live_pct, live_ok) -> dict:
    """Today's Home market context first, else the live read, else unavailable.

    A NaN / inf S&P change reads as unavailable on both legs (never classified)."""
    if (
        home_is_today
        and isinstance(home_ctx, dict)
        and home_ctx.get("tone_available") is True
        and home_ctx.get("tone") is not None
        and _finite(home_ctx.get("sp500_pct"))
    ):
        return {
            "tone": home_ctx.get("tone"),
            "sp500_pct": home_ctx.get("sp500_pct"),
            "available": True,
            "source": "home",
            "as_of": home_built_at,
        }
    if live_ok and _finite(live_pct):
        return {
            "tone": classify_tone(live_pct),
            "sp500_pct": live_pct,
            "available": True,
            "source": "live",
            "as_of": None,
        }
    return {"tone": None, "sp500_pct": None, "available": False, "source": None, "as_of": None}


# ── Macro (G-07) ─────────────────────────────────────────────────────────────
def _days_until(date_str, today: date):
    try:
        d = date.fromisoformat(str(date_str)[:10])
        return (d - today).days
    except Exception:
        return None


def macro_block_map(macro_events, today: date, now_et):
    """Sector -> reason map of imminent HIGH macro events.

    Returns ``None`` when ``macro_events is None`` (unchecked); a map with empty
    sets when checked and nothing applies.  ``now_et`` is a datetime (or time)
    in ET, or None (a same-day release then cannot be proven resolved, so the
    gate stays on -- fail safe, as before).
    """
    if macro_events is None:
        return None
    from stock_analyzer.macro_calendar import HIGH as _MC_HIGH, affected_sectors as _aff_sectors
    blocked: set = set()
    reasons: dict = {}
    for _ev in macro_events:
        if _ev.get("impact") != _MC_HIGH:
            continue
        _d = _days_until(_ev.get("date"), today)
        if _d is None or _d < 0 or _d > MACRO_IMMINENT_DAYS:
            continue
        # Today's events: FOMC is never lifted same-day; other HIGH events lift
        # when FRED confirms the actual (released) or the wall clock has passed
        # the scheduled release time. Any exception keeps the gate on.
        if _d == 0 and _ev.get("category") != "Fed Policy":
            _resolved = bool(_ev.get("released"))
            if not _resolved and _ev.get("time_et"):
                try:
                    _now_t = now_et.time() if hasattr(now_et, "time") else now_et
                    _resolved = _time.fromisoformat(_ev["time_et"]) <= _now_t
                except Exception:
                    pass
            if _resolved:
                continue
        _affected = _aff_sectors(_ev.get("category", ""))
        if "__ALL__" in _affected:
            blocked = {"__ALL__"}
            reasons["__ALL__"] = f"{_ev.get('event','')} ({_ev.get('date')}) — {_d}d away"
            break
        for _s in _affected:
            blocked.add(_s)
            reasons.setdefault(_s, f"{_ev.get('event','')} ({_ev.get('date')}) — {_d}d away")
    return {"sectors": blocked, "reasons": reasons}


def macro_hold(sector, block_map):
    """Reason string if ``sector`` is held by the macro map, else None."""
    if not block_map:
        return None
    sectors = block_map["sectors"]
    reasons = block_map["reasons"]
    if "__ALL__" in sectors:
        return reasons.get("__ALL__")
    if sector in sectors:
        return reasons.get(sector)
    return None


# ── Data trust ───────────────────────────────────────────────────────────────
def data_trust_hold(bundle):
    """None (no hold) or a hold dict: stale cache, then fundamentals age."""
    if bundle.get("stale_as_of") is not None:
        return {
            "check": "stale", "gate_id": None, "kind": "stale_as_of",
            "reason": f"score uses cached data from {bundle.get('stale_as_of')}",
            "clears_when": "a refresh brings fresh data — reload the page",
            "gate_value": None, "gate_threshold": None,
        }
    _fund_age = bundle.get("fund_cache_age_days")
    if _fund_age is not None and _fund_age > GROW_TODAY_MAX_FUND_AGE_DAYS:
        return {
            "check": "stale", "gate_id": None, "kind": "fund_age",
            "reason": (f"fundamentals {_fund_age} days old — the score uses fundamentals "
                       f"older than {GROW_TODAY_MAX_FUND_AGE_DAYS} days"),
            "clears_when": "a refresh brings fresh data — reload the page",
            "gate_value": _fund_age, "gate_threshold": GROW_TODAY_MAX_FUND_AGE_DAYS,
        }
    return None


# ── News ─────────────────────────────────────────────────────────────────────
def is_negative_news(avg_compound) -> bool:
    """Same predicate as ``reconcile_signals``' negative-news skip."""
    return avg_compound is not None and avg_compound <= NEWS_SENTIMENT_NEGATIVE


def news_compound(items):
    """Mean ``compound`` over the headlines, or None when there are none."""
    items = list(items or [])
    if not items:
        return None
    return sum(n.get("compound", 0) for n in items) / len(items)


# ── Ordered check functions (A4) ─────────────────────────────────────────────
# Each takes the context dict and returns None (pass), or a dict with
# kind "hold" (a hold row) or "unchecked" (a {check, why} note).
def _gid(ctx, name):
    gids = ctx.get("gate_ids")
    return None if gids is None else gids.get(name)


def check_stale(ctx):
    b = ctx.get("bundle")
    if b is None:
        return {"kind": "unchecked", "check": "stale",
                "why": "no data bundle — the stale-data check couldn't run"}
    h = data_trust_hold(b)
    return None if h is None else {"kind": "hold", **h}


def check_tone(ctx):
    ts = ctx.get("tone_state")
    if not ts or not ts.get("available"):
        return {"kind": "unchecked", "check": "tone",
                "why": "market tone unavailable — the bear-day check couldn't run"}
    if ts.get("tone") == "bear":
        pct = ts.get("sp500_pct")
        return {
            "kind": "hold", "check": "tone", "gate_id": _gid(ctx, "tone"),
            "reason": ("market risk-off"
                       + (f" (S&P {pct:+.1f}%)" if isinstance(pct, (int, float)) else "")),
            "clears_when": (f"a session where the S&P isn't down {abs(MARKET_TONE_BEAR_PCT):g}% "
                            "or more — re-checked every time you open this page"),
            "gate_value": pct, "gate_threshold": MARKET_TONE_BEAR_PCT,
        }
    return None


def check_macro(ctx):
    bm = ctx.get("macro_map")
    if bm is None:
        return {"kind": "unchecked", "check": "macro",
                "why": "macro calendar unavailable — the macro-event check couldn't run"}
    if not isinstance(bm, dict) or bm.get("sectors") is None:
        # A map without its blocked-sector set is malformed, not "nothing
        # blocked": say the check couldn't run rather than pass silently.
        return {"kind": "unchecked", "check": "macro",
                "why": "macro calendar unreadable — the macro-event check couldn't run"}
    # The hold is decided on SET MEMBERSHIP; the reason string only words it.
    # Safe defaults (explicit None handling) so `{}` is a pass, not a crash.
    sectors = bm.get("sectors")
    reasons = bm.get("reasons")
    reasons = {} if reasons is None else reasons
    sector = ctx.get("sector")
    if "__ALL__" in sectors:
        r = reasons.get("__ALL__")
    elif not sector:
        # A falsy sector can't be matched against a blocked-sector set: say so
        # rather than pass silently (chunk-3 review item 3).
        return {"kind": "unchecked", "check": "macro",
                "why": "sector unknown — the macro-event check couldn't run"}
    elif sector in sectors:
        r = reasons.get(sector)
    else:
        return None
    r = r or "imminent HIGH-impact macro event"
    return {
        "kind": "hold", "check": "macro", "gate_id": _gid(ctx, "macro"),
        "reason": f"macro event · {r}",
        "clears_when": "the release is out",
        "gate_value": None, "gate_threshold": MACRO_IMMINENT_DAYS,
    }


def check_news(ctx):
    news = ctx.get("news")
    if not news or news.get("compound") is None or not news.get("n"):
        return {"kind": "unchecked", "check": "news", "why": "no recent headlines to check"}
    c = news["compound"]
    if is_negative_news(c):
        return {
            "kind": "hold", "check": "news", "gate_id": None,
            "reason": f"negative news (avg {c:+.2f} across {news['n']} headlines)",
            "clears_when": f"the news average rises above {NEWS_SENTIMENT_NEGATIVE:g}",
            "gate_value": c, "gate_threshold": NEWS_SENTIMENT_NEGATIVE,
        }
    return None


def check_earnings(ctx):
    d = ctx.get("earnings_days")
    if d is not None and 0 <= d <= EARNINGS_IMMINENT_DAYS:
        return {
            "kind": "hold", "check": "earnings", "gate_id": None,
            "reason": "earnings today" if d == 0 else f"earnings in {d}d",
            "clears_when": "the report is out",
            "gate_value": d, "gate_threshold": EARNINGS_IMMINENT_DAYS,
        }
    return None


# stale -> tone -> macro -> news -> earnings. A future check is one function
# above plus one entry here.
DEFAULT_CHECKS = (check_stale, check_tone, check_macro, check_news, check_earnings)

DEFAULT_GATE_IDS = {"tone": None, "macro": None}


def entry_check(*, bundle=None, tone_state=None, sector=None, macro_map=None,
                news=None, earnings_days=None, gate_ids=None, checks=None) -> dict:
    """Run every check; never returns None.

    ``news`` is ``{"compound", "n", "headline"}`` or None. ``gate_ids`` maps
    "tone"/"macro" to the calling surface's ledger gate id (stale/news/earnings
    never carry one). ``macro_map`` is ``macro_block_map``'s result (None =
    unchecked).
    """
    ctx = {
        "bundle": bundle, "tone_state": tone_state, "sector": sector,
        "macro_map": macro_map, "news": news, "earnings_days": earnings_days,
        "gate_ids": gate_ids,
    }
    holds: list = []
    unchecked: list = []
    for fn in (checks if checks is not None else DEFAULT_CHECKS):
        r = fn(ctx)
        if not r:
            continue
        r = dict(r)
        kind = r.pop("kind", "hold")
        if kind == "unchecked":
            unchecked.append({"check": r.get("check"), "why": r.get("why")})
        else:
            holds.append(r)
    return {
        "eligible": not holds,
        "holds": holds,
        "binding": holds[0] if holds else None,
        "unchecked": unchecked,
    }


# ── Watchlist wiring helpers (chunk 4) ───────────────────────────────────────
# Pure: app.py reads the caches / makes the live fetches and passes the RESULTS
# in; every decision (which tone source, which hold reads how) is here.
def news_ctx_from_items(items):
    """``{"compound", "n", "headline"}`` from curated headline items (the
    ``data.curate_news_items`` shape: ``title`` + raw-VADER ``compound``), or
    None when there are none (-> check_news's "no recent headlines")."""
    items = [n for n in (items or []) if isinstance(n, dict)]
    if not items:
        return None
    worst = min(items, key=lambda n: n.get("compound", 0))
    return {"compound": news_compound(items), "n": len(items),
            "headline": worst.get("title")}


def pick_macro_events(home_events, home_is_today, live_events):
    """Today's Home brief's macro events first, else the live calendar read.
    None (= unchecked) only when neither is available; ``[]`` is a real
    "checked, none"."""
    if home_is_today and home_events is not None:
        return home_events
    return live_events


def watchlist_run_ctx(tone_state, macro_events, today: date, now_et) -> dict:
    """The per-run part of an ``entry_ctx``: the tone state (from
    ``resolve_tone_state``) and the macro block map (None when the events
    couldn't be loaded -> check_macro's "unchecked")."""
    return {"tone_state": tone_state,
            "macro_map": macro_block_map(macro_events, today, now_et)}


def watchlist_ticker_ctx(run_ctx, *, sector, classified, news_items) -> dict:
    """The full per-ticker ``entry_ctx``. ``sector`` is the curated
    ``sector_gate_context`` sector and is passed ONLY when ``classified`` (else
    None -> check_macro says "sector unknown"). ``news_items`` None / empty =
    headlines couldn't be read or none exist (-> "no recent headlines to
    check")."""
    return {
        "tone_state": run_ctx.get("tone_state"),
        "macro_map": run_ctx.get("macro_map"),
        "news": news_ctx_from_items(news_items),
        "sector": sector if classified else None,
    }


def _fmt_clock(dt) -> "str | None":
    try:
        hour = dt.hour % 12 or 12
        return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
    except Exception:
        return None


HOME_TODAY_OK = "home_today_ok"
HOME_TODAY_TONE_FAILED = "home_today_tone_failed"
HOME_NOT_TODAY = "home_not_today"


def home_tone_state(home_ctx, home_is_today) -> str:
    """How Home's Brief stands for TODAY's tone: built today with a real read,
    built today but its index read failed (Home then runs the day as flat), or
    not built today. Drives the Protect banner's wording only."""
    if not home_is_today:
        return HOME_NOT_TODAY
    if isinstance(home_ctx, dict) and home_ctx.get("tone_available") is True:
        return HOME_TODAY_OK
    return HOME_TODAY_TONE_FAILED


def _fmt_read_time(as_of) -> "str | None":
    clock = _fmt_clock(as_of)
    return None if clock is None else clock


def tone_source_line(tone_state, home_state=None) -> "str | None":
    """Where the tone came from, worded so it is true on every path.

    home  -> "Market read from Home's Brief, 9:42 AM ET." (time omitted when
             the context carries none);
    live  -> says why Home's read wasn't used when ``home_state`` tells us;
    unavailable -> None (the caller says the check couldn't run instead)."""
    if not isinstance(tone_state, dict) or not tone_state.get("available"):
        return None
    if tone_state.get("source") == "home":
        clock = _fmt_read_time(tone_state.get("as_of"))
        return ("Market read from Home's Brief" + (f", {clock} ET" if clock else "") + ".")
    if home_state == HOME_TODAY_TONE_FAILED:
        return ("Market read live — 🏠 Home's Brief couldn't read the market this run "
                "and is treating today as flat (its picks were not deferred).")
    if home_state == HOME_NOT_TODAY:
        return "Market read live (🏠 Home's Brief isn't built today)."
    return "Market read live."


def watchlist_banner(tone_state, macro_map, recs, home_state=None, exclude=()) -> dict:
    """What the top of the Actionable view says.

    ``{"protect": None | {"title", "body", "source"}, "notes": [str, ...]}``.
    ``protect`` is set on a bear tone; "matching 🏠 Home" is said ONLY when the
    tone really came from Home's Brief. The "would otherwise be Ready" list is
    names held ONLY by the tone check and not replaced by an Already-in-
    Portfolio / Sold-Today override (``exclude``). ``notes`` carries the
    fail-open "couldn't run" disclosures."""
    notes: list = []
    protect = None
    ts = tone_state if isinstance(tone_state, dict) else {}
    if not ts.get("available"):
        notes.append("⚠️ The bear-day check couldn't run (market tone unavailable) — "
                     "entries are shown, not deferred.")
    elif ts.get("tone") == "bear":
        pct = ts.get("sp500_pct")
        pct_txt = f"{pct:+.1f}%" if _finite(pct) else "down"
        skip = {str(x).upper() for x in (exclude or ())}
        tickers = []
        for r in (recs if recs is not None else []):
            if not (isinstance(r, dict) and r.get("action") == "ENTRY_HELD"):
                continue
            if str(r.get("ticker", "")).upper() in skip:
                continue
            holds = r.get("holds")
            holds = [] if holds is None else holds
            if holds and all(isinstance(h, dict) and h.get("check") == "tone" for h in holds):
                tickers.append(str(r.get("ticker")))
        n = len(tickers)
        matching = " matching 🏠 Home." if ts.get("source") == "home" else ""
        protect = {
            "title": (f"🛡️ Protect Mode — S&P {pct_txt} "
                      f"(risk-off at {MARKET_TONE_BEAR_PCT:g}% or worse)."),
            "body": ("New entries deferred," + matching if matching else "New entries deferred.")
                    + (f" {n} name{'s' if n != 1 else ''} would otherwise be "
                       f"Ready: {', '.join(tickers)}." if n else ""),
            "source": tone_source_line(ts, home_state),
        }
    if macro_map is None:
        notes.append("⚠️ The macro-event check couldn't run (macro calendar unavailable) — "
                     "entries are shown, not held for it.")
    return {"protect": protect, "notes": notes}


def held_budget_footer(em_line, allocs) -> str:
    """The sentence under a held card. The "not counted against today's budget"
    claim is made ONLY when the emailed-allocation record was actually read
    (``allocs`` not None) and holds nothing for the name (no emailed line)."""
    base = "No size and no trade button while held."
    if em_line:
        return base
    if allocs is None:
        return base
    return base + " Not counted against today's new-position budget."


def watchlist_held_cache(recs, day_iso) -> "dict | None":
    """``{"day", "held": {TICKER: hold_reason}}`` over the ENTRY_HELD cards, or
    None when there are no recs to read (the page couldn't build them -- never
    ``{}`` for failure; an empty ``held`` means checked, nothing held)."""
    if recs is None:
        return None
    held = {}
    for r in recs:
        if isinstance(r, dict) and r.get("action") == "ENTRY_HELD":
            held[str(r.get("ticker", "")).upper()] = (
                r.get("hold_reason") or "held by the shared entry check")
    return {"day": day_iso, "held": held}


def journal_hold_note(ticker, held_cache, today_iso) -> "str | None":
    """Trade Journal BUY-confirm awareness: 'Note: X is held today — <reason>.'
    None when the cache is unreadable / not today's / the ticker isn't held --
    it never claims "not held" and never blocks."""
    if not isinstance(held_cache, dict) or held_cache.get("day") != today_iso:
        return None
    held = held_cache.get("held")
    if not isinstance(held, dict):
        return None
    tk = str(ticker or "").upper()
    if tk not in held:
        return None
    return f"Note: {tk} is held today — {held[tk]}."


def emailed_then_held_line(card, allocs, alloc_times) -> "str | None":
    """'Emailed at 9:45 AM with 6 sh — now deferred: <reason>' for an
    ENTRY_HELD card whose name was already sized in an email today; else None.
    The allocation is only READ -- it stays counted against today's budget."""
    if not isinstance(card, dict) or card.get("action") != "ENTRY_HELD":
        return None
    tk = str(card.get("ticker", "")).upper()
    alloc = allocs.get(tk) if isinstance(allocs, dict) else None
    if not isinstance(alloc, dict):
        return None
    sh = alloc.get("shares")
    try:
        sh_txt = f" with {float(sh):g} sh" if sh is not None and float(sh) > 0 else ""
    except (TypeError, ValueError):
        sh_txt = ""
    clock = None
    raw = alloc_times.get(tk) if isinstance(alloc_times, dict) else None
    if raw is not None:
        try:
            import pytz
            from datetime import datetime as _dt
            d = raw if isinstance(raw, _dt) else _dt.fromisoformat(str(raw).replace("Z", "+00:00"))
            if d.tzinfo is not None:
                d = d.astimezone(pytz.timezone("America/New_York"))
            clock = _fmt_clock(d)
        except Exception:
            clock = None
    when = f"at {clock}" if clock else "today"
    reason = card.get("hold_reason") or "held by the shared entry check"
    return f"Emailed {when}{sh_txt} — now deferred: {reason}"


HELD_KIND_LABELS = {
    "macro": "macro event",
    "news": "negative news",
    "stale": "stale data",
    "earnings": "earnings",
    "check_failed": "entry check couldn't run",
}


def held_label(card) -> str:
    """Badge text of an ENTRY_HELD card: "Deferred — market risk-off" on a bear
    tone, "Held: <kind>" otherwise ("Held today" if the kind is unknown)."""
    kind = card.get("hold_kind") if isinstance(card, dict) else None
    if kind == "tone":
        return "Deferred — market risk-off"
    if kind in HELD_KIND_LABELS:
        return f"Held: {HELD_KIND_LABELS[kind]}"
    return "Held today"


# ── Watchlist email footer (chunk 5) ─────────────────────────────────────────
def email_footer_lines(entry_run) -> "list[str]":
    """Plain-text footer lines for the "Ready to Enter" email.

    ``entry_run`` is ``{"tone_state": <resolve_tone_state result>,
    "macro_available": bool}`` (what ``compute_watchlist_entries`` returns), or
    None / malformed -> no lines (an old caller says nothing rather than
    guessing). RUN-level lines only: one for the tone the run used -- or that it
    couldn't be read -- and one if the macro calendar was unavailable. A check
    that couldn't run for a single name (no headlines, unknown sector) is that
    card's ``entry_unchecked`` note, which the email prints under the name.  The caller escapes before building HTML.
    """
    if not isinstance(entry_run, dict):
        return []
    lines: list = []
    ts = entry_run.get("tone_state")
    if isinstance(ts, dict) and ts.get("available") and _finite(ts.get("sp500_pct")):
        clock = _fmt_clock(ts.get("as_of"))
        at = f" at {clock} ET" if clock else ""
        lines.append(
            f"Market tone{at}: {ts.get('tone')} (S&P {float(ts['sp500_pct']):+.1f}%). "
            "The app re-checks when you open it and defers if the market turns risk-off."
        )
    else:
        lines.append("Market tone couldn't be read — the bear-day check didn't run.")
    if entry_run.get("macro_available") is not True:
        lines.append("Macro calendar unavailable — the macro-event check didn't run.")
    return lines


# ── Analysis wiring (chunk 6) ────────────────────────────────────────────────
# One decision for "may the Analysis page offer a share count for this name?"
# (docs/plans/shared-entry-check.md D7 / S-A).  The Watchlist runs the same
# ``entry_check`` over the same per-run context, so the two surfaces cannot
# disagree on a not-held Buy.  Held-name ADD sizing is deliberately unchanged
# here (the known D5 / S-D gap).
_ANALYSIS_BUY_LABELS = ("Buy", "Strong Buy")


def analysis_entry_hold(*, basis, label, held, entry=None, check_crashed=False) -> dict:
    """``{offer_size, hold, holds, unchecked, reason_if_no_size, entry_checked}``.

    * withheld / unknown verdict basis -> no size.
    * any non-Buy verdict (held or not) -> no size (S-A); no entry check needed.
    * held Buy (an add) -> size offered, unchanged (D5 gap); no check applied.
    * not-held Buy -> ``entry`` (an ``entry_check`` result) decides; any hold,
      a crashed check or a missing result -> no size (fail CLOSED).
    """
    out = {"offer_size": False, "hold": None, "holds": [], "unchecked": [],
           "reason_if_no_size": None, "entry_checked": False, "crashed": False}
    if basis not in ("equity", "etf"):
        out["reason_if_no_size"] = "verdict withheld"
        return out
    if label not in _ANALYSIS_BUY_LABELS:
        out["reason_if_no_size"] = (f"no size on a {label} verdict" if label
                                    else "no size without a verdict")
        return out
    if held:
        out["offer_size"] = True
        return out
    out["entry_checked"] = True
    if check_crashed or not isinstance(entry, dict):
        out["crashed"] = True
        out["reason_if_no_size"] = "entry check couldn't run"
        return out
    holds = entry.get("holds")
    holds = [] if holds is None else list(holds)
    unchecked = entry.get("unchecked")
    out["unchecked"] = [] if unchecked is None else list(unchecked)
    out["holds"] = holds
    if holds:
        binding = entry.get("binding")
        out["hold"] = binding if binding is not None else holds[0]
        reason = out["hold"].get("reason") if isinstance(out["hold"], dict) else None
        out["reason_if_no_size"] = reason or "held by the entry check"
        return out
    out["offer_size"] = True
    return out


def analysis_needs_check(basis, label, held) -> bool:
    """True only for a not-held Buy / Strong Buy with a real verdict."""
    return bool(basis in ("equity", "etf") and label in _ANALYSIS_BUY_LABELS
                and not held)


def analysis_entry_decision(*, bundle, basis, label, held, run_ctx, sector,
                            classified, news_items, earnings, today: date) -> dict:
    """Build the per-ticker context with the Watchlist's own helpers, run
    ``entry_check`` and decide.  A crash anywhere in the check fails CLOSED
    (``reason_if_no_size`` = "entry check couldn't run"); a held name or a
    non-Buy verdict skips the check entirely."""
    needs_check = analysis_needs_check(basis, label, held)
    entry = None
    crashed = False
    if needs_check:
        try:
            tctx = watchlist_ticker_ctx(run_ctx, sector=sector, classified=classified,
                                        news_items=news_items)
            entry = entry_check(
                bundle=bundle, tone_state=tctx["tone_state"], sector=tctx["sector"],
                macro_map=tctx["macro_map"], news=tctx["news"],
                earnings_days=_days_until(earnings, today) if earnings else None,
            )
        except Exception:
            entry, crashed = None, True
    return analysis_entry_hold(basis=basis, label=label, held=held,
                               entry=entry, check_crashed=crashed)


def analysis_gate_tiles(decision) -> "list[dict]":
    """The four extra Gate-checks tiles (Market tone / Macro / News / Data
    fresh): ``{label, state, detail}`` with state True (passed), False (held)
    or None (not checked).  Empty when the entry check did not run."""
    if not isinstance(decision, dict) or not decision.get("entry_checked"):
        return []
    holds = decision.get("holds")
    holds = [] if holds is None else holds
    unchecked = decision.get("unchecked")
    unchecked = [] if unchecked is None else unchecked
    crashed = decision.get("crashed") is True
    spec = (("tone", "Market tone", "Market not risk-off"),
            ("macro", "Macro", "No imminent macro event"),
            ("news", "News", "No negative news"),
            ("stale", "Data fresh", "Data is current"))
    tiles = []
    for chk, label, ok in spec:
        hold = next((h for h in holds if isinstance(h, dict) and h.get("check") == chk), None)
        if crashed:
            tiles.append({"label": label, "state": None, "detail": "not checked"})
        elif hold is not None:
            tiles.append({"label": label, "state": False,
                          "detail": str(hold.get("reason") or "held")})
        elif any(isinstance(u, dict) and u.get("check") == chk for u in unchecked):
            tiles.append({"label": label, "state": None, "detail": "not checked"})
        else:
            tiles.append({"label": label, "state": True, "detail": ok})
    return tiles


def analysis_hold_banner(decision, tone_state=None, home_state=None) -> "dict | None":
    """The banner above the Trade Plan of a held-back not-held Buy:
    ``{title, body, source}`` or None when nothing was held back.  "Same call
    as 🏠 Home" is said only when the tone really came from Home's Brief."""
    if (not isinstance(decision, dict) or not decision.get("entry_checked")
            or decision.get("offer_size")):
        return None
    binding = decision.get("hold")
    if not isinstance(binding, dict):
        return {
            "title": "Not an entry today — the entry check couldn't run",
            "body": ("The verdict stands on its own numbers; no size is offered "
                     "until the check can run. Reload the page to retry."),
            "source": None,
        }
    reason = binding.get("reason") or "held by the entry check"
    lead = "deferred: " if binding.get("check") == "tone" else "held: "
    ts = tone_state if isinstance(tone_state, dict) else {}
    # 🏠 Home is named only when the binding hold IS the tone and the tone
    # really came from Home's Brief; no claim about what the Watchlist shows
    # for this ticker (it may not be listed there at all).
    same = ("📋 Watchlist and 🏠 Home apply the same check."
            if (binding.get("check") == "tone" and ts.get("available")
                and ts.get("source") == "home")
            else "📋 Watchlist applies the same check.")
    all_holds = decision.get("holds")
    all_holds = [] if all_holds is None else all_holds
    others = [str(h.get("reason")) for h in all_holds
              if isinstance(h, dict) and h is not binding and h.get("reason")]
    body = ("The verdict stands on its own numbers; no size is offered today. " + same)
    if others:
        body += " Also: " + "; ".join(others) + "."
    clears = binding.get("clears_when")
    if clears:
        body += f" Clears when: {clears}."
    return {
        "title": f"Not an entry today — {lead}{reason}",
        "body": body,
        "source": (tone_source_line(ts, home_state) if binding.get("check") == "tone" else None),
    }


def analysis_notes(tone_state, macro_map) -> "list[str]":
    """Fail-open disclosures for the Analysis page (worded for sizes, not for
    the Watchlist's entries)."""
    notes = []
    ts = tone_state if isinstance(tone_state, dict) else {}
    if not ts.get("available"):
        notes.append("⚠️ The bear-day check couldn't run (market tone unavailable) — "
                     "sizes are shown, not deferred.")
    if macro_map is None:
        notes.append("⚠️ The macro-event check couldn't run (macro calendar unavailable) — "
                     "sizes are shown, not held for it.")
    return notes


def analysis_shares_label(decision) -> str:
    """Scorecard Shares text for a held-back not-held Buy: "— (deferred)" when
    the binding hold is the tone, "— (held)" otherwise (incl. a crashed check)."""
    hold = decision.get("hold") if isinstance(decision, dict) else None
    if isinstance(hold, dict) and hold.get("check") == "tone":
        return "— (deferred)"
    return "— (held)"
