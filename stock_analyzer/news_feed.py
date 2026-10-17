"""
News-feed helpers — pure logic for the per-ticker headline feed.

Why this exists (docs/plans/news-feed-restore.md): yfinance's `Ticker.news` hit an
endpoint Yahoo retired (HTTP 404 `{"message":"Not Found"}`), which the library parsed
as valid JSON and returned as `[]` — so every consumer read "no news" for weeks. The
feed now comes from `yf.Search` (a text query that returns unrelated headlines unless
filtered on `relatedTickers`), and every fetch carries a `news_status` so "the feed
failed" can never again read as "nothing happened".

`news_status` values (per ticker, carried on the bundle and the loaded result):
  ok      -- at least one headline survived the filter
  none    -- the response had a `news` key but nothing survived: the source returned no headlines tagged to this ticker (not proof the name is quiet -- Yahoo can return none at times)
  error   -- an exception, OR the response had NO `news` key (the silent-404 shape)
  cached  -- the bundle came from `bundle_cache` (news is never cached there)

Pure: no network, no Streamlit. `feed_state` is the cross-ticker tripwire.
"""

from __future__ import annotations

from stock_analyzer.constants import NEWS_FEED_OUTAGE_MIN_TICKERS, NEWS_MAX_AGE_DAYS
from stock_analyzer.market_time import now_et

NEWS_STATUS_OK = "ok"
NEWS_STATUS_NONE = "none"
NEWS_STATUS_ERROR = "error"
NEWS_STATUS_CACHED = "cached"

# Headlines kept per ticker (plumbing, not policy -- the old ncp feed returned ~10).
# NOTE: sentiment.analyze_news also reads 10, so lowering this below 10 would change
# the sentiment pillar -- at that point it belongs in constants.py as a policy value.
NEWS_KEEP_PER_TICKER = 10

# Share-class siblings: Yahoo tags a company's news with ONE class (Alphabet news is
# tagged GOOG; GOOGL kept 1 of 26), so an item tagged with a sibling counts for the
# ticker. Exact matching otherwise.
_SIBLINGS = {
    "GOOGL": "GOOG", "GOOG": "GOOGL",
    "BRK-A": "BRK-B", "BRK-B": "BRK-A",
    "FOX": "FOXA", "FOXA": "FOX",
    "NWS": "NWSA", "NWSA": "NWS",
}


def _related(item: dict) -> list[str]:
    rel = item.get("relatedTickers")
    if not isinstance(rel, (list, tuple)):
        return []
    return [str(r).strip().upper() for r in rel if r]


def _ts(item: dict) -> float:
    try:
        return float(item.get("providerPublishTime") or 0)
    except (TypeError, ValueError):
        return 0.0


def filter_search_news(items, ticker: str, now_ts: float | None = None) -> list[dict]:
    """Keep only Search-news items that are really about `ticker`.

    * `relatedTickers` must contain `ticker` (case-insensitive, exact) or a share-class sibling (`_SIBLINGS`); an item with
      no `relatedTickers` is dropped (a text search returns unrelated headlines).
    * Items older than NEWS_MAX_AGE_DAYS are dropped (exactly at the boundary is
      kept); an item with no publish time cannot be aged and is dropped.
    * Newest first, capped at NEWS_KEEP_PER_TICKER.
    `now_ts` (unix seconds) is injectable for tests; default is now in ET.
    """
    want = str(ticker or "").strip().upper()
    wanted = {want, _SIBLINGS.get(want, want)}
    if not want or not isinstance(items, (list, tuple)):
        return []
    now = float(now_ts) if now_ts is not None else now_et().timestamp()
    cutoff = now - NEWS_MAX_AGE_DAYS * 86400
    kept = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if not wanted.intersection(_related(it)):
            continue
        ts = _ts(it)
        if ts <= 0 or ts < cutoff:
            continue
        kept.append(it)
    kept.sort(key=_ts, reverse=True)
    return kept[:NEWS_KEEP_PER_TICKER]


def search_news_status(response, kept: list) -> str:
    """Status for a Search response: no dict / no `news` key -> error (the silent
    404 shape); `news` present but nothing kept -> none; else ok."""
    if not isinstance(response, dict) or "news" not in response:
        return NEWS_STATUS_ERROR
    return NEWS_STATUS_OK if kept else NEWS_STATUS_NONE


def should_save_llm_rescore(rescored) -> bool:
    """Never persist an empty rescore: a `headlines: []` day-cache row would keep
    serving "no news" for the rest of the UTC day even after the feed recovers."""
    return isinstance(rescored, list) and len(rescored) > 0


def llm_cache_usable(cached, live_headlines) -> bool:
    """A sentiment_llm_cache row is a HIT unless it holds `headlines: []` while live
    headlines are present (a poisoned empty row -> treat as a miss and rescore)."""
    if cached is None:
        return False
    if not cached.get("headlines") and live_headlines:
        return False
    return True


_LIVE_STATUSES = (NEWS_STATUS_OK, NEWS_STATUS_NONE, NEWS_STATUS_ERROR)


def feed_state(bundles) -> dict | None:
    """Cross-ticker news-feed verdict for one run.

    `bundles` is an iterable of loaded results, or a `{ticker: result}` mapping (a
    failed load may be `None`). Each result's status is read with
    `.get("news_status")` -- entries cached before deploy lack it.

    Returns None when given no bundles (nothing was checked), else
    {"state": "ok" | "outage" | "thin",
     "checked": live fetches (status ok/none/error),
     "with_news": how many of those were `ok`,
     "unavailable": tickers whose status is error / cached / missing,
     "none": tickers whose status is `none`,
     "partial": True when state is `ok` but at least NEWS_FEED_OUTAGE_MIN_TICKERS
                bundles were unavailable (e.g. 17 error + 1 ok) -- NOT a red state,
                a flag so a mostly-dead feed is not read as a healthy one}.
      ok      -- at least one `ok`
      outage  -- no `ok` and at least NEWS_FEED_OUTAGE_MIN_TICKERS live bundles
      thin    -- no `ok` and fewer live bundles than that (too few to call it)
    `cached` and missing statuses are not live fetches: listed as unavailable,
    never counted in `checked`.

    CAVEAT for consumers: `unavailable` is NOT the complete list of unchecked
    tickers. A bundle that failed to load before reaching `held_data` /
    `grow_composites` is simply absent from the protective and morning-picks
    payloads' input; only the watchlist payload lists failed loads (as None).
    Do not treat `unavailable` as "every ticker whose news was not checked".
    """
    if bundles is None:
        return None
    if hasattr(bundles, "items"):
        pairs = [(str(k), v) for k, v in bundles.items()]
    else:
        pairs = []
        for i, b in enumerate(bundles):
            label = b.get("ticker") if isinstance(b, dict) else None
            pairs.append((str(label) if label else f"#{i}", b))
    if not pairs:
        return None
    live = ok_n = 0
    unavailable: list[str] = []
    none_list: list[str] = []
    for label, b in pairs:
        status = b.get("news_status") if isinstance(b, dict) else None
        if status in _LIVE_STATUSES:
            live += 1
            if status == NEWS_STATUS_OK:
                ok_n += 1
            elif status == NEWS_STATUS_NONE:
                none_list.append(label)
            else:
                unavailable.append(label)
        else:
            unavailable.append(label)
    if ok_n > 0:
        state = "ok"
    elif live >= NEWS_FEED_OUTAGE_MIN_TICKERS:
        state = "outage"
    else:
        state = "thin"
    return {"state": state, "checked": live, "with_news": ok_n,
            "unavailable": sorted(unavailable), "none": sorted(none_list),
            "partial": state == "ok" and len(unavailable) >= NEWS_FEED_OUTAGE_MIN_TICKERS}


def outage_failure(*feeds) -> str | None:
    """One cron failure line if ANY of the given `feed_state` results is an outage
    (the lane still does its work and sends its emails -- this only reddens the
    heartbeat). None otherwise, including for None / thin / ok inputs. Never more
    than one line however many payloads report an outage."""
    worst = None
    for f in feeds:
        if isinstance(f, dict) and f.get("state") == "outage":
            if worst is None or (f.get("checked") or 0) > (worst.get("checked") or 0):
                worst = f
    if worst is None:
        return None
    return (f"news feed: 0 headlines across {worst.get('checked')} tickers -- "
            f"news sentiment / critical-news / negative-news checks did not run")
