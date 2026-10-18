"""Held-news coverage helpers (docs/plans/held-news-coverage.md, chunk 1).

Pure: never reads the clock. The recency window for the Daily Brief's news
checks and the selection of which held headlines the LLM noise filter sees.
"""

import math
from datetime import date, datetime, time, timedelta

from stock_analyzer.constants import (
    NEWS_BRIEF_LOOKBACK_TRADING_DAYS,
    NEWS_CRITICAL_MAX_TIER,
    NEWS_CRITICAL_MIN_HEADLINES,
    NEWS_SENTIMENT_CRITICAL,
    NEWS_SENTIMENT_WARN,
)
from stock_analyzer.market_time import ET

_MAX_WALK_DAYS = 30


def news_window_start(
    today: date,
    lookback_trading_days: int = NEWS_BRIEF_LOOKBACK_TRADING_DAYS,
) -> datetime:
    """00:00 ET of the Nth previous NYSE trading day before `today` (tz-aware)."""
    from stock_analyzer.data import is_trading_day

    found = 0
    d = today
    for _ in range(_MAX_WALK_DAYS):
        d = d - timedelta(days=1)
        if is_trading_day(d):
            found += 1
            if found >= lookback_trading_days:
                return ET.localize(datetime.combine(d, time(0, 0)))
    raise ValueError(
        f"no {lookback_trading_days} trading day(s) found within "
        f"{_MAX_WALK_DAYS} days before {today}"
    )


def item_ts(item) -> float:
    """The item's `ts` as a float; 0.0 when missing/invalid/non-positive."""
    try:
        v = item.get("ts")
    except AttributeError:
        return 0.0
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0
    v = float(v)
    if math.isnan(v) or v <= 0:
        return 0.0
    return v


def in_window(item, window_start_ts: float) -> bool:
    """True when the item is dated and published at or after the window start."""
    ts = item_ts(item)
    return ts > 0 and ts >= window_start_ts


def rescore_selection(
    items: list,
    window_start_ts: float,
    *,
    keep_first: int | None = None,
    max_items: int,
) -> tuple[list[int], list[int]]:
    """(send_indices, over_bound_indices) for the LLM suppress-only rescore.

    Candidates: the head, then every
    other in-window item with compound <= NEWS_SENTIMENT_WARN, most-negative
    first (ties by original index). Beyond `max_items` the least-negative
    extras go to over_bound; the head is never over-bound.

    The head is the first `keep_first` TITLE-UNIQUE items (the headlines the
    sidebar shows): the list is per-ticker, so a headline naming several
    holdings repeats and an index-based head would stop short of the sidebar.
    Later repeats are ordinary candidates (sent when in-window and negative).
    """
    if keep_first is None:   # default = the shared display count (lazy import)
        from stock_analyzer.data import HELD_NEWS_DISPLAY_COUNT
        keep_first = HELD_NEWS_DISPLAY_COUNT
    from stock_analyzer.data import title_key
    head: list[int] = []
    seen_titles: set[str] = set()
    for i, it in enumerate(items):
        if len(head) >= keep_first:
            break
        k = title_key(it.get("title") if hasattr(it, "get") else None)
        if k in seen_titles:
            continue
        seen_titles.add(k)
        head.append(i)
    in_head = set(head)
    extras = []
    for i in range(len(items)):
        if i in in_head:
            continue
        it = items[i]
        if not in_window(it, window_start_ts):
            continue
        c = it.get("compound")
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            continue
        if math.isnan(float(c)) or float(c) > NEWS_SENTIMENT_WARN:
            continue
        extras.append((float(c), i))
    extras.sort()
    ordered = head + [i for _, i in extras]
    return ordered[:max_items], ordered[max_items:]


def _compound(item) -> float | None:
    """The item's numeric compound, or None when missing/invalid/NaN."""
    try:
        c = item.get("compound")
    except AttributeError:
        return None
    if c is None or isinstance(c, bool) or not isinstance(c, (int, float)):
        return None
    c = float(c)
    return None if math.isnan(c) else c


def _tier(item) -> float:
    """The item's tier; a missing/invalid tier reads as 3 (lowest trust)."""
    try:
        t = item.get("tier", 3)
    except AttributeError:
        return 3
    if t is None or isinstance(t, bool) or not isinstance(t, (int, float)):
        return 3
    if math.isnan(float(t)):
        return 3
    return t


def critical_news_by_ticker(news_items, held_tickers, window_start_ts) -> dict:
    """{ticker: [items worst-first]} for held tickers with enough in-window
    tier-1/2 headlines at or below NEWS_SENTIMENT_CRITICAL.

    Undated or out-of-window items never count. Dict order = first appearance
    of the ticker's qualifying item in `news_items`.
    """
    by_t: dict = {}
    for item in news_items or []:
        try:
            t = str(item.get("ticker", "")).upper()
        except AttributeError:
            continue
        if t not in held_tickers or not in_window(item, window_start_ts):
            continue
        c = _compound(item)
        if c is None or c > NEWS_SENTIMENT_CRITICAL:
            continue
        if _tier(item) > NEWS_CRITICAL_MAX_TIER:
            continue
        by_t.setdefault(t, []).append(item)
    out: dict = {}
    for t, lst in by_t.items():
        if len(lst) >= NEWS_CRITICAL_MIN_HEADLINES:
            out[t] = sorted(lst, key=_compound)   # stable, worst first
    return out


def warning_news_by_ticker(news_items, held_tickers, window_start_ts, *, exclude) -> dict:
    """{ticker: {"item", "n_negative", "n_act_qualifying"}} for held tickers
    not in `exclude` with >= 1 in-window headline at or below
    NEWS_SENTIMENT_WARN (any tier). `item` is the worst (lowest compound);
    `n_act_qualifying` counts in-window tier-1/2 items at or below
    NEWS_SENTIMENT_CRITICAL (what an Act Today card would need)."""
    agg: dict = {}
    for item in news_items or []:
        try:
            t = str(item.get("ticker", "")).upper()
        except AttributeError:
            continue
        if t not in held_tickers or t in exclude or not in_window(item, window_start_ts):
            continue
        c = _compound(item)
        if c is None or c > NEWS_SENTIMENT_WARN:
            continue
        e = agg.setdefault(t, {"item": item, "_c": c, "n_negative": 0, "n_act_qualifying": 0})
        e["n_negative"] += 1
        if c < e["_c"]:
            e["item"], e["_c"] = item, c
        if c <= NEWS_SENTIMENT_CRITICAL and _tier(item) <= NEWS_CRITICAL_MAX_TIER:
            e["n_act_qualifying"] += 1
    return {
        t: {"item": e["item"], "n_negative": e["n_negative"],
            "n_act_qualifying": e["n_act_qualifying"]}
        for t, e in agg.items()
    }


def window_label(window_start: datetime) -> str:
    """e.g. "Fri Oct 16" (no leading zero on the day, platform-independent)."""
    return f"{window_start.strftime('%a %b')} {window_start.day}"


def brief_news_flags(act_today, review_list) -> dict:
    """{TICKER: {"level": "critical"|"warning", "headline", "compound"}} from the
    Brief's OWN news cards (Act critical_news -> critical; Review news WATCH ->
    warning). Critical wins if a ticker somehow has both. Pure; None -> {}.
    Callers publish None (not this {}) when the Brief is offline."""
    out: dict = {}
    for it in review_list or []:
        if not isinstance(it, dict) or it.get("watch_kind") != "news":
            continue
        t = str(it.get("ticker") or "").upper()
        if t:
            out[t] = {"level": "warning",
                      "headline": str(it.get("news_headline") or ""),
                      "compound": float(it.get("news_compound") or 0.0)}
    for it in act_today or []:
        if not isinstance(it, dict) or str(it.get("kind", "")) != "critical_news":
            continue
        t = str(it.get("ticker") or "").upper()
        if t:
            out[t] = {"level": "critical",
                      "headline": str(it.get("news_headline") or ""),
                      "compound": float(it.get("news_compound") or 0.0)}
    return out


def news_flags_from_detectors(critical, warning) -> dict:
    """{TICKER: {"level", "headline", "compound"}} straight from the two news
    detectors (`critical_news_by_ticker` / `warning_news_by_ticker`), so it covers
    EVERY held name with an in-window negative headline -- including names whose
    news CARD was de-duplicated away by another card. Critical wins. Pure."""
    def _row(level, item):
        c = _compound(item)
        return {"level": level,
                "headline": str(item.get("title") or item.get("headline") or "news")[:140],
                "compound": 0.0 if c is None else c}
    out: dict = {}
    for t, e in (warning or {}).items():
        if t:
            out[str(t).upper()] = _row("warning", e["item"])
    for t, lst in (critical or {}).items():
        if t and lst:
            out[str(t).upper()] = _row("critical", lst[0])
    return out


def published_news_flags(brief, today_iso: str) -> dict:
    """The `_brief_news_flags_cache` payload for a built Brief: the Brief's own
    detector-derived `news_flags` when present (covers names whose card was
    de-duplicated), else derived from its cards (a brief dict without the key)."""
    b = brief or {}
    if "news_flags" in b and isinstance(b["news_flags"], dict):
        flags = b["news_flags"]
    else:
        flags = brief_news_flags(b.get("act_today"), b.get("review_list"))
    return {"date": today_iso, "flags": flags}


def usable_news_flags(cache, today_iso: str):
    """The flags dict from a published cache when it is today's, else None
    (None / malformed / another day's = 'not checked', never 'no news')."""
    if not isinstance(cache, dict) or cache.get("date") != today_iso:
        return None
    flags = cache.get("flags")
    return flags if isinstance(flags, dict) else None
