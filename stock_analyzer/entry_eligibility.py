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


def resolve_tone_state(home_ctx, home_is_today, home_built_at, live_pct, live_ok) -> dict:
    """Today's Home market context first, else the live read, else unavailable."""
    if (
        home_is_today
        and isinstance(home_ctx, dict)
        and home_ctx.get("tone_available") is True
        and home_ctx.get("tone") is not None
    ):
        return {
            "tone": home_ctx.get("tone"),
            "sp500_pct": home_ctx.get("sp500_pct"),
            "available": True,
            "source": "home",
            "as_of": home_built_at,
        }
    if live_ok and live_pct is not None:
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
    # The hold is decided on SET MEMBERSHIP; the reason string only words it.
    sectors = bm["sectors"]
    reasons = bm["reasons"]
    sector = ctx.get("sector")
    if "__ALL__" in sectors:
        r = reasons.get("__ALL__")
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
