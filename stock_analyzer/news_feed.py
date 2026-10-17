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

Pure: no network, no Streamlit. Chunk 2 adds the cross-ticker `feed_state` here.
"""

from __future__ import annotations

from stock_analyzer.constants import NEWS_MAX_AGE_DAYS
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
