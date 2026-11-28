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
from zoneinfo import ZoneInfo

from stock_analyzer import buy_check_state as _bcs
from stock_analyzer import journal_read as _jr
from stock_analyzer import macro_read as _mr
from stock_analyzer import macro_sector_gate as _msg
from stock_analyzer import tone_freshness as _tf
from stock_analyzer.asset_type import ASSET_TYPE_ETF
from stock_analyzer.news_feed import (
    NEWS_STATUS_CACHED, NEWS_STATUS_ERROR, NEWS_STATUS_NONE,
)

from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_PAST_DATE_HOLD_DAYS,
    GROW_TODAY_MAX_FUND_AGE_DAYS,
    MACRO_IMMINENT_DAYS,
    MARKET_TONE_BEAR_PCT,
    MARKET_TONE_BULL_PCT,
    NEWS_SENTIMENT_NEGATIVE,
    TONE_HOME_READ_MAX_AGE_MINUTES,
)


_ET_ZONE = ZoneInfo("America/New_York")


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


def resolve_tone_state(home_ctx, home_is_today, home_built_at, live_pct, live_ok,
                      *, now_et=None, live_read_at=None) -> dict:
    """Today's Home market context first, else the live read, else unavailable.

    A NaN / inf S&P change reads as unavailable on both legs (never classified).

    ``now_et=None`` (cron, tests) keeps the original rule: any same-day Home
    context with a readable tone is used.  With ``now_et`` the Home leg also
    needs ``tone_freshness.home_read_state(...)["state"] == "usable"`` (under
    ``TONE_HOME_READ_MAX_AGE_MINUTES`` old and the same session; buy-check C4a).

    The result adds ``why`` (None when Home's read was used; else why it wasn't:
    ``home_not_today`` / ``home_aged`` / ``home_other_session`` / ``home_failed`` /
    ``home_unreadable``, or ``live_failed`` when nothing was readable and Home
    can't be blamed) and ``home_read_at`` (Home's aware read time when known).
    ``as_of`` is Home's read time on the Home leg and ``live_read_at`` on the
    live leg."""
    why = None
    home_read_at = None
    if now_et is None:
        home_ok = bool(home_is_today)
    else:
        hs = _tf.home_read_state(home_ctx, home_is_today, now_et)
        home_ok = hs["state"] == _tf.STATE_USABLE
        home_read_at = hs["read_at"]
        why = _tf.why_for_state(hs["state"])
    if (
        home_ok
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
            "why": None,
            "home_read_at": home_read_at,
        }
    if live_ok and _finite(live_pct):
        return {
            "tone": classify_tone(live_pct),
            "sp500_pct": live_pct,
            "available": True,
            "source": "live",
            "as_of": live_read_at,
            "why": why,
            "home_read_at": home_read_at,
        }
    # Nothing readable: blame Home only when it had a read that couldn't be used.
    if why in (None, _tf.WHY_HOME_NOT_TODAY):
        why = _tf.WHY_LIVE_FAILED
    return {"tone": None, "sp500_pct": None, "available": False, "source": None,
            "as_of": None, "why": why, "home_read_at": home_read_at}


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
    """Reason string if ``sector`` is held by the macro map, else None.

    Delegates to ``macro_sector_gate.decide``: only a "blocked" state returns a
    reason (never an empty one: membership decides, the text only words it).
    An unknown sector is NOT a hold here; Grow Today abstains on it separately."""
    if not block_map:
        return None
    d = _msg.decide(sector, block_map)
    if d["state"] != "blocked":
        return None
    return d["event_reason"] or "imminent HIGH-impact macro event"


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
# kind "hold" (a hold row), "unchecked" (a {check, why} note) or "passed"
# (a {check, detail} note: the check ran and cleared, shown so a pass is not
# invisible).
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
    if not isinstance(ts, dict) or not ts.get("available"):
        # Buy-check C4a (owner D1 of 2026-10-16 reversed 2026-11-27): a market
        # that couldn't be read HOLDS a new entry; it is never read as flat.
        # No gate_id and no _HOLD_SUPPRESSION_KIND entry: a data abstention, not
        # a ledgered policy gate.
        return {
            "kind": "hold", "check": "tone_unread", "gate_id": None,
            "reason": _tf.unread_reason(ts),
            "clears_when": ("the S&P 500 read succeeds — re-checked every time "
                            "you open this page"),
            "gate_value": None, "gate_threshold": None,
        }
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
    bundle = ctx.get("bundle")
    is_etf = isinstance(bundle, dict) and bundle.get("asset_type") == ASSET_TYPE_ETF
    d = _msg.decide(ctx.get("sector"), ctx.get("macro_map"), is_etf=is_etf)
    if d["state"] == "unchecked":
        # buy-check C4m: an unreadable calendar HOLDS the name (never "no event").
        return {**_mr.entry_hold()}
    if d["state"] == "blocked":
        return {
            "kind": "hold", "check": "macro", "gate_id": _gid(ctx, "macro"),
            "reason": f"macro event · {d['event_reason']}",
            "clears_when": "the release is out",
            "gate_value": None, "gate_threshold": MACRO_IMMINENT_DAYS,
        }
    if d["state"] == "unknown":
        # No gate_id and no _HOLD_SUPPRESSION_KIND entry: a data abstention, not
        # a ledgered policy gate (macro-sector-gate plan C2).
        return {
            "kind": "hold", "check": "macro_unknown_sector", "gate_id": None,
            "reason": d["reason"],
            "clears_when": _msg.unknown_clears_when(is_etf=is_etf),
            "gate_value": None, "gate_threshold": MACRO_IMMINENT_DAYS,
        }
    return None


def check_news(ctx):
    news = ctx.get("news")
    if news and not isinstance(news, dict):
        # Fail CLOSED on a malformed input, as before 3a (the caller's
        # except turns this into "entry check couldn't run").
        raise TypeError(f"check_news: news must be a dict or None, got {type(news).__name__}")
    t = ctx.get("ticker")
    who = str(t).upper() if t else "this ticker"
    # Headlines the relevance filter dropped (named another company). None/absent =
    # not checked; only a real positive int is disclosed.
    _bd = ctx.get("bundle")
    unrel = _bd.get("news_unrelated_n") if isinstance(_bd, dict) else None
    unrel = unrel if isinstance(unrel, int) and not isinstance(unrel, bool) and unrel > 0 else 0
    if isinstance(news, dict) and news.get("compound") is not None and news.get("n"):
        # Real headlines are evaluated whatever the feed status says.
        c = news["compound"]
        n = news["n"]
        if is_negative_news(c):
            return {
                "kind": "hold", "check": "news", "gate_id": None,
                "reason": f"negative news (avg {c:+.2f} across {n} headlines)",
                "clears_when": f"the news average rises above {NEWS_SENTIMENT_NEGATIVE:g}",
                "gate_value": c, "gate_threshold": NEWS_SENTIMENT_NEGATIVE,
            }
        return {"kind": "passed", "check": "news", "n": n, "compound": c,
                "detail": (f"{n} headline{'s' if n != 1 else ''}, avg {c:+.2f}"
                           f" — above the negative-news bar ({NEWS_SENTIMENT_NEGATIVE:+.2f})"
                           + (f" · {unrel} that didn't name {who} not counted"
                              if unrel else ""))}
    bundle = ctx.get("bundle")
    status = bundle.get("news_status") if isinstance(bundle, dict) else None
    if (ctx.get("news_read_failed") is True
            or status in (NEWS_STATUS_ERROR, NEWS_STATUS_CACHED)):
        return {"kind": "unchecked", "check": "news",
                "why": f"headlines for {who} couldn't be read — the news check didn't run"}
    if status == NEWS_STATUS_NONE:
        return {"kind": "unchecked", "check": "news",
                "why": f"no headlines tagged to {who}"}
    if unrel:
        return {"kind": "unchecked", "check": "news",
                "why": (f"no headlines naming {who} — {unrel} headline{'s' if unrel != 1 else ''}"
                        f" tagged to {who} didn't name it in the title and "
                        f"{'weren' if unrel != 1 else 'wasn'}'t counted")}
    return {"kind": "unchecked", "check": "news", "why": "no recent headlines to check"}


def check_earnings(ctx):
    d = ctx.get("earnings_days")
    if d is not None and 0 <= d <= EARNINGS_IMMINENT_DAYS:
        return {
            "kind": "hold", "check": "earnings", "gate_id": None,
            "reason": "earnings today" if d == 0 else f"earnings in {d}d",
            "clears_when": "the report is out",
            "gate_value": d, "gate_threshold": EARNINGS_IMMINENT_DAYS,
        }
    # Buy-check C3b: the imminent policy hold above binds first. Then a date that
    # couldn't be confirmed HOLDS the name (`earnings_unknown`, an abstention: no gate
    # id, no ledger row). A bundle with no `earnings_resolved` stamp (legacy / test, or
    # no bundle at all) is unchanged.
    b = ctx.get("bundle")
    h = _bcs.earnings_unknown_hold(b, d)
    if h is not None:
        return h
    note = _bcs.earnings_pass_note(b)
    if note:
        return {"kind": "passed", "check": "earnings", "detail": note}
    return None


def check_journal(ctx):
    """Buy-check C5 chunk B (SoldTodayLeak): an unreadable trade journal HOLDS the
    name -- the app can't tell whether it was sold today. Only an explicit False
    (the surface tried and failed) holds; None / absent / True do not. No gate_id
    and no ledger row: a data abstention."""
    if ctx.get("trades_read") is False:
        return {**_jr.entry_hold()}
    return None


# stale -> tone -> macro -> news -> earnings -> journal. A future check is one
# function above plus one entry here.
DEFAULT_CHECKS = (check_stale, check_tone, check_macro, check_news, check_earnings,
                  check_journal)

DEFAULT_GATE_IDS = {"tone": None, "macro": None}

# Holds that are a check that couldn't run (a data abstention), not a policy
# gate. When a policy hold fires on the same name it binds instead, so the
# ledgered gate row (G-28 etc.) is never lost behind an abstention.
_ABSTENTION_CHECKS = frozenset({"tone_unread", _mr.CHECK_UNREAD, _jr.CHECK_UNREAD,
                                _bcs.CHECK_EARNINGS_UNKNOWN})


def _binding_hold(holds):
    """The first hold whose ``check`` is not an abstention, else the first hold
    (None when there are none). ``holds`` order itself is unchanged."""
    for h in holds:
        if not (isinstance(h, dict) and h.get("check") in _ABSTENTION_CHECKS):
            return h
    return holds[0] if holds else None


def entry_check(*, bundle=None, tone_state=None, sector=None, macro_map=None,
                news=None, earnings_days=None, gate_ids=None, checks=None,
                ticker=None, news_read_failed=False, trades_read=None) -> dict:
    """Run every check; never returns None.

    Returns ``eligible``/``holds``/``binding`` plus ``unchecked`` (checks that
    couldn't run) and ``passed`` (checks that ran and cleared, a
    ``{check, detail}`` note each; a pass never blocks).  ``ticker`` only words
    the news note; ``news_read_failed`` True = the headline read itself raised.

    ``news`` is ``{"compound", "n", "headline"}`` or None. ``gate_ids`` maps
    "tone"/"macro" to the calling surface's ledger gate id (stale/news/earnings
    never carry one). ``macro_map`` is ``macro_block_map``'s result (None = the
    calendar couldn't be read: a HOLD, ``macro_unread``). ``trades_read`` is False
    when the surface's strict read of the trade journal failed (a HOLD,
    ``trades_unread``: the app can't tell whether the name was sold today); None
    (not wired) and True hold nothing.
    """
    ctx = {
        "bundle": bundle, "tone_state": tone_state, "sector": sector,
        "macro_map": macro_map, "news": news, "earnings_days": earnings_days,
        "gate_ids": gate_ids, "ticker": ticker, "news_read_failed": news_read_failed,
        "trades_read": trades_read,
    }
    holds: list = []
    unchecked: list = []
    passed: list = []
    for fn in (checks if checks is not None else DEFAULT_CHECKS):
        r = fn(ctx)
        if not r:
            continue
        r = dict(r)
        kind = r.pop("kind", "hold")
        if kind == "unchecked":
            unchecked.append({"check": r.get("check"), "why": r.get("why")})
        elif kind == "passed":
            note = {"check": r.get("check"), "detail": r.get("detail")}
            for k in ("n", "compound"):
                if k in r:
                    note[k] = r[k]
            passed.append(note)
        else:
            holds.append(r)
    return {
        "eligible": not holds,
        "holds": holds,
        "binding": _binding_hold(holds),
        "unchecked": unchecked,
        "passed": passed,
    }


# ── Watchlist wiring helpers (chunk 4) ───────────────────────────────────────
# Pure: app.py reads the caches / makes the live fetches and passes the RESULTS
# in; every decision (which tone source, which hold reads how) is here.
def news_ctx_from_items(items):
    """``{"compound", "n", "headline"}`` from curated headline items (the
    ``data.curate_news_items`` shape: ``title`` + raw-VADER ``compound``), or
    None when there are none (-> check_news reads the bundle's ``news_status`` to say WHY there are
    none: headlines couldn't be read vs no headline tagged to the ticker)."""
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


def watchlist_run_ctx(tone_state, macro_events, today: date, now_et,
                      trades_read=None) -> dict:
    """The per-run part of an ``entry_ctx``: the tone state (from
    ``resolve_tone_state``), the macro block map (None when the events
    couldn't be loaded -> check_macro holds every name, ``macro_unread``) and
    ``trades_read`` (False when the surface's strict trade-journal read failed ->
    check_journal holds every name, ``trades_unread``; None = not wired)."""
    return {"tone_state": tone_state,
            "macro_map": macro_block_map(macro_events, today, now_et),
            "trades_read": trades_read}


def watchlist_ticker_ctx(run_ctx, *, sector, classified, news_items) -> dict:
    """The full per-ticker ``entry_ctx``. ``sector`` is the curated
    ``sector_gate_context`` sector and is passed ONLY when ``classified`` (else
    None -> check_macro holds it as "sector unknown" while a sector-keyed event
    is imminent, and passes it when none is). ``news_items`` None means the
    headline read raised (``news_read_failed`` True -> "headlines couldn't be read");
    an empty list is a completed read with nothing usable, which check_news words
    from the bundle's ``news_status`` (``curate_news_items`` never returns None)."""
    return {
        "tone_state": run_ctx.get("tone_state"),
        "macro_map": run_ctx.get("macro_map"),
        "trades_read": run_ctx.get("trades_read"),
        "news": news_ctx_from_items(news_items),
        "news_read_failed": news_items is None,
        "sector": sector if classified else None,
    }


def _fmt_clock(dt) -> "str | None":
    """12-hour clock text in ET: an aware datetime is converted to
    America/New_York first (a UTC-stamped read must not print UTC hours); a
    naive one is read as ET wall clock, as before."""
    try:
        if getattr(dt, "tzinfo", None) is not None and dt.tzinfo.utcoffset(dt) is not None:
            dt = dt.astimezone(_ET_ZONE)
        hour = dt.hour % 12 or 12
        return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
    except Exception:
        return None


HOME_TODAY_OK = "home_today_ok"
HOME_TODAY_TONE_FAILED = "home_today_tone_failed"
HOME_NOT_TODAY = "home_not_today"


def home_tone_state(home_ctx, home_is_today) -> str:
    """How Home's Brief stands for TODAY's tone: built today with a real read,
    built today but its index read failed (Home then holds its new picks), or
    not built today. Drives the Protect banner's wording only."""
    if not home_is_today:
        return HOME_NOT_TODAY
    if isinstance(home_ctx, dict) and home_ctx.get("tone_available") is True:
        return HOME_TODAY_OK
    return HOME_TODAY_TONE_FAILED


def _fmt_read_time(as_of) -> "str | None":
    clock = _fmt_clock(as_of)
    return None if clock is None else clock


def _session_tail(tone_state) -> str:
    """" This is the move in the last session (Wed Nov 25), not today's." when the
    read's S&P move is an earlier session's; else ""."""
    phrase = _tf.session_phrase(tone_state)
    return f" This is the move {phrase}, not today's." if phrase else ""


def tone_source_line(tone_state, home_state=None) -> "str | None":
    """Where the tone came from, worded so it is true on every path.

    home  -> "Market read from Home's Brief, 9:42 AM ET." (time omitted when
             the context carries none);
    live  -> says why Home's read wasn't used from the state's ``why`` (else
             from ``home_state``);
    unavailable -> None (the caller says the check couldn't run instead).
    A read whose S&P move belongs to an earlier session says so (pre-open,
    weekend, holiday)."""
    if not isinstance(tone_state, dict) or not tone_state.get("available"):
        return None
    tail = _session_tail(tone_state)
    if tone_state.get("source") == "home":
        clock = _fmt_read_time(tone_state.get("as_of"))
        return ("Market read from Home's Brief" + (f", {clock} ET" if clock else "") + "." + tail)
    clock = _fmt_read_time(tone_state.get("as_of"))
    at = f" at {clock} ET" if clock else ""
    why = tone_state.get("why")
    home_clock = _fmt_read_time(tone_state.get("home_read_at"))
    home_read = f"Home's read from {home_clock} ET" if home_clock else "Home's read"
    n = TONE_HOME_READ_MAX_AGE_MINUTES
    if why == _tf.WHY_HOME_AGED:
        return f"Market read live{at} ({home_read} is over {n:g} minutes old)." + tail
    if why == _tf.WHY_HOME_OTHER_SESSION:
        return f"Market read live{at} ({home_read} is from an earlier session)." + tail
    if why == _tf.WHY_HOME_UNREADABLE:
        return f"Market read live{at} ({home_read} has no readable time)." + tail
    if why == _tf.WHY_HOME_FAILED or home_state == HOME_TODAY_TONE_FAILED:
        return (f"Market read live{at} — 🏠 Home's Brief couldn't read the market "
                "this run; its new picks are held until it can." + tail)
    if why == _tf.WHY_HOME_NOT_TODAY or home_state == HOME_NOT_TODAY:
        return f"Market read live{at} (🏠 Home's Brief isn't built today)." + tail
    return f"Market read live{at}." + tail


def tone_line(tone_state, home_state=None) -> "str | None":
    """One caption for a bull / flat day: the tone, the S&P change and where /
    when it was read (None on a bear day, where the Protect box says it, and
    when the tone is unavailable, where the unread box says it)."""
    ts = tone_state if isinstance(tone_state, dict) else {}
    if not ts.get("available") or ts.get("tone") not in ("bull", "flat"):
        return None
    pct = ts.get("sp500_pct")
    if not _finite(pct):
        return None
    src = tone_source_line(ts, home_state)
    return f"Market tone: {ts.get('tone')} (S&P {pct:+.1f}%)." + (f" {src}" if src else "")


def _tickers_held_only_by(recs, check, exclude) -> list:
    """Tickers of ENTRY_HELD cards whose every hold is ``check`` (one check name or
    a set / tuple of them), minus ``exclude`` (an Already-in-Portfolio /
    Sold-Today override replaces the held card)."""
    _checks = {check} if isinstance(check, str) else set(check)
    skip = {str(x).upper() for x in (exclude or ())}
    tickers = []
    for r in (recs if recs is not None else []):
        if not (isinstance(r, dict) and r.get("action") == "ENTRY_HELD"):
            continue
        if str(r.get("ticker", "")).upper() in skip:
            continue
        holds = r.get("holds")
        holds = [] if holds is None else holds
        if holds and all(isinstance(h, dict) and h.get("check") in _checks for h in holds):
            tickers.append(str(r.get("ticker")))
    return tickers


def watchlist_banner(tone_state, macro_map, recs, home_state=None, exclude=(),
                     trades_read=None) -> dict:
    """What the top of the Actionable view says.

    ``{"protect": None | {"title", "body", "source"},
    "unread": None | {"title", "body"}, "macro_unread": None | {"title", "body"},
    "trades_unread": None | {"title", "body"},
    "earnings_unknown": None | {"title", "body"},
    "tone_line": str | None, "notes": [str, ...]}``.
    ``protect`` is set on a bear tone; "matching 🏠 Home" is said ONLY when the
    tone really came from Home's Brief. ``unread`` is set when the market move
    couldn't be read (new entries are HELD, never read as flat). The "would
    otherwise be Ready" lists are names held ONLY by that one tone check and not
    replaced by an Already-in-Portfolio / Sold-Today override (``exclude``).
    ``tone_line`` is one caption on every bull / flat day (the time and source
    of the read). ``macro_unread`` is set when the macro calendar couldn't be read
    (buy-check C4m: new entries are HELD; its list is the names held only by that
    check, or none when the tone is also unread -- the tone ``unread`` block then
    lists every name held only by either check, one list, never two).
    ``earnings_unknown`` (buy-check C3b) is set when at least one name is held ONLY by
    the unconfirmed-earnings-date check: it lists those names (minus ``exclude``) and is
    None when there are none (the hold is per name, not run-level).
    ``trades_unread`` is set when ``trades_read`` is False (buy-check C5 chunk B:
    the trade journal couldn't be read, so the app can't tell which names were sold
    today; new entries are HELD). ONE list across the unread blocks: the first
    active block in the order tone -> macro -> journal lists the names held only by
    the union of the unread checks; the later blocks carry no list. ``notes`` is
    kept for future run-level disclosures (none today)."""
    notes: list = []
    macro_unread = None
    trades_unread = None
    protect = None
    unread = None
    _jr_unread = trades_read is False
    ts = tone_state if isinstance(tone_state, dict) else {}
    if not ts.get("available"):
        _macro_too = _mr.map_unread(macro_map)
        _unread_checks = {"tone_unread"}
        if _macro_too:
            _unread_checks.add(_mr.CHECK_UNREAD)
        if _jr_unread:
            _unread_checks.add(_jr.CHECK_UNREAD)
        tickers = _tickers_held_only_by(recs, _unread_checks, exclude)
        n = len(tickers)
        unread = {
            "title": "Market move couldn't be read — new entries held",
            "body": ((f"{n} name{'s' if n != 1 else ''} would otherwise be "
                      f"Ready: {', '.join(tickers)}. " if n else "")
                     + _tf.unread_detail(ts)
                     + " Clears when the S&P 500 read succeeds — re-checked "
                       "every time you open this page."),
        }
    elif ts.get("tone") == "bear":
        pct = ts.get("sp500_pct")
        pct_txt = f"{pct:+.1f}%" if _finite(pct) else "down"
        tickers = _tickers_held_only_by(recs, "tone", exclude)
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
    if _mr.map_unread(macro_map):
        _both = unread is not None
        _m_checks = {_mr.CHECK_UNREAD}
        if _jr_unread:
            _m_checks.add(_jr.CHECK_UNREAD)
        _mtickers = [] if _both else _tickers_held_only_by(recs, _m_checks, exclude)
        _mn = len(_mtickers)
        macro_unread = {
            "title": "Macro calendar couldn't be read — new entries held",
            "body": ((f"{_mn} name{'s' if _mn != 1 else ''} would otherwise be "
                      f"Ready: {', '.join(_mtickers)}. " if _mn else "")
                     + "The app can't tell whether a CPI, jobs or Fed release is due, "
                       "so it holds new entries rather than assume none. Clears when "
                       "the macro calendar can be read — re-checked every time you "
                       "open this page."),
        }
    if _jr_unread:
        _jtickers = ([] if (unread is not None or macro_unread is not None)
                     else _tickers_held_only_by(recs, {_jr.CHECK_UNREAD}, exclude))
        _jn = len(_jtickers)
        trades_unread = {
            "title": "Trade journal couldn't be read — new entries held",
            "body": ((f"{_jn} name{'s' if _jn != 1 else ''} would otherwise be "
                      f"Ready: {', '.join(_jtickers)}. " if _jn else "")
                     + "The app never suggests buying back a name the same day you "
                       "sold it, and without the journal it can't tell which names "
                       "you sold today. Clears when the trade journal can be read — "
                       "re-checked every time you open this page."),
        }
    earnings_unknown = None
    _etickers = _tickers_held_only_by(recs, _bcs.CHECK_EARNINGS_UNKNOWN, exclude)
    if _etickers:
        _en = len(_etickers)
        earnings_unknown = {
            "title": "Earnings date unconfirmed — new entries held",
            "body": (f"{_en} name{'s' if _en != 1 else ''} would otherwise be Ready: "
                     f"{', '.join(_etickers)}. "
                     f"{'Their' if _en != 1 else 'Its'} next earnings date couldn't be "
                     "confirmed, and a buy just before a report is what this check stops. "
                     "Clears when the date is confirmed (re-checked each time the data "
                     "reloads); a date on file that passed more than "
                     f"{EARNINGS_PAST_DATE_HOLD_DAYS} days ago stops holding."),
        }
    return {"protect": protect, "unread": unread, "macro_unread": macro_unread,
            "trades_unread": trades_unread, "earnings_unknown": earnings_unknown,
            "tone_line": None if protect is not None else tone_line(ts, home_state),
            "notes": notes}


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
    word = "deferred" if card.get("hold_kind") == "tone" else "held"
    return f"Emailed {when}{sh_txt} — now {word}: {reason}"


HELD_KIND_LABELS = {
    "macro": "macro event",
    "macro_unknown_sector": "sector unknown (macro check)",
    "news": "negative news",
    "stale": "stale data",
    "earnings": "earnings",
    "check_failed": "entry check couldn't run",
    # buy-check C4a: the S&P 500 read couldn't run (never read as a flat day)
    "tone_unread": "market move couldn't be read",
    # buy-check C4m: the macro-event calendar couldn't be read
    "macro_unread": "macro calendar couldn't be read",
    # buy-check C5 chunk B: the trade journal couldn't be read (sold today unknown)
    "trades_unread": "trade journal couldn't be read",
    # entry-fail-closed C2a: a portfolio-fit / beta check that couldn't run
    "fit_unknown": "portfolio fit couldn't be checked",
    "beta_unknown": "portfolio beta couldn't be checked",
    # buy-check C3b: the next earnings date couldn't be confirmed
    "earnings_unknown": "earnings date unconfirmed",
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
    that couldn't run for a single name (no headlines) is that card's
    ``entry_unchecked`` note (an unknown sector is a hold, not a note), which the email prints under the name.  The caller escapes before building HTML.
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
        lines.append("Market tone couldn't be read — no name is listed as Ready to Enter this run.")
    if entry_run.get("macro_available") is not True:
        lines.append("Macro calendar couldn't be read — no name is listed as Ready to Enter this run.")
    if entry_run.get("trades_available") is False:
        # buy-check C5 chunk B: only an explicit False (the strict journal read
        # failed); an old payload without the key says nothing.
        lines.append("Trade journal couldn't be read — no name is listed as Ready to Enter this run.")
    return lines


# ── Analysis wiring (chunk 6) ────────────────────────────────────────────────
# One decision for "may the Analysis page offer a share count for this name?"
# (docs/plans/shared-entry-check.md D7 / S-A).  The Watchlist runs the same
# ``entry_check`` over the same per-run context, so the two surfaces cannot
# disagree on a not-held Buy.  Held-name ADD sizing is deliberately unchanged
# here (the known D5 / S-D gap).
_ANALYSIS_BUY_LABELS = ("Buy", "Strong Buy")


def analysis_entry_hold(*, basis, label, held, entry=None, check_crashed=False) -> dict:
    """``{offer_size, hold, holds, unchecked, passed, reason_if_no_size,
    entry_checked, crashed}``.

    * withheld / unknown verdict basis -> no size.
    * any non-Buy verdict (held or not) -> no size (S-A); no entry check needed.
    * held Buy (an add) -> size offered, unchanged (D5 gap); no check applied.
    * not-held Buy -> ``entry`` (an ``entry_check`` result) decides; any hold,
      a crashed check or a missing result -> no size (fail CLOSED).
    """
    out = {"offer_size": False, "hold": None, "holds": [], "unchecked": [], "passed": [],
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
    passed = entry.get("passed")
    out["passed"] = [] if passed is None else list(passed)
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
                            classified, news_items, earnings, today: date,
                            ticker=None) -> dict:
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
                ticker=ticker, news_read_failed=tctx["news_read_failed"],
                trades_read=tctx["trades_read"],
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
    passed = decision.get("passed")
    passed = [] if passed is None else passed
    crashed = decision.get("crashed") is True
    spec = (("tone", "Market tone", "Market not risk-off"),
            ("macro", "Macro", "No imminent macro event"),
            ("news", "News", "No negative news"),
            ("stale", "Data fresh", "Data is current"))
    # The Macro tile reads the G-28 hold, the unknown-sector hold and the unread hold.
    # The Market tone tile reads both the bear-day hold and the unread hold.
    _checks_for = {"macro": ("macro", "macro_unknown_sector", "macro_unread"),
                   "tone": ("tone", "tone_unread")}
    tiles = []
    for chk, label, ok in spec:
        _names = _checks_for.get(chk, (chk,))
        hold = next((h for h in holds if isinstance(h, dict) and h.get("check") in _names), None)
        if crashed:
            tiles.append({"label": label, "state": None, "detail": "not checked"})
        elif hold is not None:
            tiles.append({"label": label, "state": False,
                          "detail": str(hold.get("reason") or "held")})
        elif any(isinstance(u, dict) and u.get("check") == chk for u in unchecked):
            u = next(u for u in unchecked
                     if isinstance(u, dict) and u.get("check") == chk)
            tiles.append({"label": label, "state": None,
                          "detail": (str(u.get("why") or "not checked")
                                     if chk == "news" else "not checked")})
        else:
            detail = ok
            p = next((p for p in passed
                      if isinstance(p, dict) and p.get("check") == chk), None)
            if chk == "news" and p is not None:
                n, c = p.get("n"), p.get("compound")
                if n is not None and c is not None:
                    detail = (f"Above the negative-news bar ({n} headline"
                              f"{'s' if n != 1 else ''}, avg {c:+.2f})")
            tiles.append({"label": label, "state": True, "detail": detail})
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
    _tone_check = binding.get("check") in ("tone", "tone_unread")
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
        "source": (_tf.unread_detail(ts) if binding.get("check") == "tone_unread"
                   else tone_source_line(ts, home_state) if _tone_check else None),
    }


def analysis_notes(tone_state, macro_map, home_state=None) -> "list[str]":
    """Disclosures for the Analysis page (worded for sizes, not for the
    Watchlist's entries): the tone line on a bull / flat day (an unread market
    is a HOLD on the card, not a note; so is an unread macro calendar)."""
    notes = []
    ts = tone_state if isinstance(tone_state, dict) else {}
    line = tone_line(ts, home_state)
    if line:
        notes.append(line)
    return notes


def analysis_shares_label(decision) -> str:
    """Scorecard Shares text for a held-back not-held Buy: "— (deferred)" when
    the binding hold is the tone, "— (held)" otherwise (incl. a crashed check)."""
    hold = decision.get("hold") if isinstance(decision, dict) else None
    if isinstance(hold, dict) and hold.get("check") == "tone":
        return "— (deferred)"
    return "— (held)"
