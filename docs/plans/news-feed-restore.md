# Restore the news feed — and make a dead feed loud

**Status: DESIGNED + OWNER-APPROVED 2026-10-16 (planner Opus 5.5, PROCEED WITH CHANGES; owner accepted every
recommendation D-1..D-7, and yf.Search primary + Finnhub `/company-news` secondary after a source comparison). Chunk 1
DONE 2026-10-16 (`593a26c`, Opus SHIP after 3 rounds, pushed). Chunk 2 (tripwire) DONE 2026-10-17 (Opus SHIP after
one FIX-FIRST round). Chunk 3 split into 3a/3b/3c 2026-10-17 (see Build plan); 3a in build.** Found from the live 📋 Watchlist (BKNG: "no recent headlines to check"). Blocks
`docs/plans/shared-entry-check.md` chunk 7 (a news check on adds would be inert).

## Root cause (verified read-only)
- yfinance `.news` / `get_news()` POSTs to `finance.yahoo.com/xhr/ncp?queryRef=latestNews&serviceKey=ncp_fin`; that
  endpoint now answers **HTTP 404 `{"message":"Not Found"}`**, which yfinance parses as valid JSON and returns `[]` —
  silently (`.venv/Lib/site-packages/yfinance/base.py:573-614`).
- A version bump does not help: yfinance **1.7.0** (latest) uses the same ncp endpoint. Our pin (1.3.0) has not
  changed since 2026-05-06; Yahoo retired the endpoint. News last demonstrably worked 2026-09-21.
- `yf.Search` (`/v1/finance/search`) works through the same library/session and returns the flat item shape
  `_parse_news_item` / `analyze_news` already handle. It is a text query, so it returns unrelated headlines unless
  filtered on `relatedTickers` (unfiltered: NOW 2/10 related, PAY 0/10, APP 4/9; with 30 fetched + the filter: NOW 6,
  APP 13, TEAM 6, CRM 29, PAY still 0). Items can be 21-78 days old (BRK-B); no consumer filters by age.

## Blast radius (everything currently fails OPEN on empty news)
| Consumer | Where | Effect today |
|---|---|---|
| Sentiment pillar (10% of the composite) | `bundle_loader.py` ~159-192, `scoring.combined_score` | **fabricated neutral `s_score = 50` in every composite** (≈ ±1.5 pts) |
| LLM sentiment day-cache | `bundle_loader.py` ~166-191, `news_intelligence.py` ~298 | caches `headlines: []` daily — would keep serving it for the rest of the UTC day after a fix |
| Grow Today negative-news Skip | `daily_briefing._cross_reference` → `signal_reconciliation` ~118 | never fires |
| Act Today critical news / Review warning news | `daily_briefing.py` ~2644-2685 / ~3163-3205 | never fire (so adds are never news-blocked) |
| F-307 entry check | `entry_eligibility.check_news` | "no recent headlines to check" — indistinguishable from a quiet name |
| Sidebar / Analysis news tab / Opportunity Signals | `app.py` ~4996, ~24373, ~20427 | "No news found" |
| LLM evidence (thesis, Red Team, Pre-Mortem, debate) | `thesis_advisor`, `premortem_advisor`, `debate_agent` | news silently omitted from the prompt |
| Persisted | `recommendations.composite_score`, `score_history`, `watchlist_entries`, gate-ledger composites | carry the fabricated 50; `s_score`/`avg_sent` are honestly NULL |
| Dead code | `data.fetch_curated_news` (~383-431, no callers) | — |

## Design
- **Primary source:** `yf.Search(ticker, news_count=NEWS_SEARCH_FETCH_COUNT, …)` inside `YFinanceProvider.bundle`, in
  its own try (a news error never fails or retries the bundle); keep only items whose `relatedTickers` contains the
  ticker (drop items lacking the field); newest first; keep 10. Same call count as the old ncp call; no new cache.
  Never cache news in `bundle_cache` (up to 5 days old).
- **Fallback (chunk 4):** Finnhub `/company-news` only when the primary is `none`/`error`, behind a per-process rate
  limit well under 60/min (Finnhub is also the live-price primary). Gated on a Railway selftest proving the endpoint
  works on the free key. FMP (250/day shared with fundamentals) and Yahoo RSS (no publisher → all tier 3) rejected.
- **Per-ticker status:** `news_status` ∈ `ok` / `none` (response had a `news` key, nothing kept = genuinely quiet) /
  `error` (exception, or **no `news` key** — the exact silent 404 shape) / `cached` (bundle from `bundle_cache`).
- **Tripwire:** new pure `stock_analyzer/news_feed.py` `feed_state(bundles)` → `ok` / `outage` (≥
  `NEWS_FEED_OUTAGE_MIN_TICKERS` live bundles, none `ok`) / `thin`, `None` for no input. Cron lanes append an outage
  to `failures` (red heartbeat, 🩺 check ①) while still doing their work and sending emails; app records a
  `yahoo_news` provider in `api_health` / 🩺 check ③; Home publishes `_news_feed_cache`.
- **Disclosure:** `check_news` → "news feed unavailable — the news check didn't run" vs the existing "no recent
  headlines to check"; an Act Today / Review / email line "📰 News feed unavailable this run — the critical-news and
  warning-news checks on your holdings did not run. Other checks are unaffected."; Grow Today "News check didn't run
  on N candidates (feed unavailable) — no negative-news Skip was applied."; LLM evidence "News: feed unavailable — do
  not infer quiet news."

## Owner decisions (2026-10-16 — all recommendations ACCEPTED)
| ID | Decision | Recommendation |
|---|---|---|
| D-1 | New buys during a news outage: fail open (with disclosure) or closed | **Open + disclosure + red heartbeat** (matches F-307 siblings; closed would block every buy on any Yahoo hiccup) |
| D-2 | `NEWS_FEED_OUTAGE_MIN_TICKERS` | **5** (disclosure only; changes no gate) |
| D-3 | `NEWS_MAX_AGE_DAYS` — drop items older than this at fetch (both sources; replaces FMP's bare 21 days) | **21** (14 is the tighter alternative); changes the sentiment pillar → policy |
| D-4 | A news outage turns the cron lane red | **Yes** |
| D-5 | Composites carried a fabricated 50 sentiment during the outage | **Dated footnotes, no `COMPOSITE_WEIGHTS_VERSION` bump**; expect ±1-2 pt moves on deploy (flips near 65 / 75) |
| D-6 | Footnote the window on Engine Track Record + Recommendation Outcomes | **Yes**, once the SQL dates it |
| D-7 | Pre-existing: `_TIER1` contains `"ap"` matched by substring (AppleInsider, TheWrap → tier 1) | **Separate reviewed fix**, not bundled |

## Build plan
| # | Chunk | Review |
|---|---|---|
| 1 | `fix(news)`: `yf.Search` + `relatedTickers` filter + age cap, `news_status`, LLM cache never saves an empty rescore (and treats a cached `[]` with live headlines as a miss), `cached` status, delete `fetch_curated_news`, selftest | reviewer (`bundle_loader` gate file; composites change) |
| 2 | `feat(news)`: `news_feed.py` tripwire, cron failures, `api_health` / `system_health` row, Home cache | reviewer |
| 3 | `feat(news)`: status-aware disclosure in `entry_eligibility`, `daily_briefing`, `curate_brief_news`, LLM evidence, app render, emails — **split 2026-10-17 into 3a/3b/3c** (a 10-17 site map found `_act_today` has no route to any bundle/status — it sees only the status-less curated `news_items` — and `_news_feed_cache` is published after the Brief builds and read nowhere, so the Brief half needs its own plumbing) | reviewer |
| 3a | Watchlist + Analysis: `check_news` splits feed-unavailable / "no headlines tagged to TICKER" / no recent headlines, and returns a visible PASS ("📰 News checked: N headlines, avg x — no negative news", owner-approved 2026-10-17) on Watchlist cards + the Ready email + Analysis's News tile; Analysis's "Opens Trade Journal pre-filled…" caption only beside a real trade button | reviewer |
| 3b | Brief + emails: thread news status / feed state into `daily_briefing` (Act Today critical-news, Review warning-news, Grow Today negative-news Skip) and say when those checks didn't run; morning-email footer must stop implying a news gate it doesn't list | reviewer |
| 3c | LLM evidence: "News: feed unavailable — do not infer quiet news" vs untagged vs real headlines (thesis, Pre-Mortem; debate/red-team carry no headlines today) | reviewer |
| 4 | `feat(news)`: Finnhub `/company-news` fallback with a rate budget (after a Railway selftest) | reviewer |
| 5 | Docs (new F-ID, F-307 news row, architecture, shipped-log, User Guide "Skip — Negative News", plans, memory) | lead |
| 6 | Then shared-entry **chunk 7**, built against `news_status` | per its plan |

Tests (from the design, must be in the chunks): the 404 no-`news`-key shape → `error` vs `{"news": []}` → `none`
(mutation-checked); `relatedTickers` filter + sort + cap; age boundary; item-shape + tier parity (`Barrons.com` 1,
`Zacks` 2, `MT Newswires` 3); a news exception never fails the bundle; LLM-cache empty-rescore rules; `feed_state`
boundaries at `NEWS_FEED_OUTAGE_MIN_TICKERS` and `−1`, `None` on empty; cron outage → `failed` heartbeat AND emails
still sent (non-vacuous); `check_news` strings for each status; no invented `critical_news` during an outage, and a
real critical WATCH fires with `ok` + 2 tier-1 headlines at −0.30 (the shared-entry golden master unchanged when
status is `ok`); LLM evidence line differs for unavailable vs quiet.

## Found during the 2026-10-17 site map — NOT built, each needs its own decision
Unverified beyond a read-only code read; check against HEAD before acting.
- `_sidebar_news` is overwritten by 📈 Analysis with the analysed tickers' news (`app.py` ~24394/~24407), and 🥧 Portfolio Overview's News Intelligence / Opportunity Signals (~20448) and the AI brief prompts (~8168, ~12384) read it as if it were holdings news. The comment at ~5016 blames Market Scanner, which only reads it.
- `app.py` ~8166-8170 builds the premarket-stance headlines with `n.get("headline")`, but curated items carry `title` — so that list is probably always empty.
- `debate_agent.build_entry_corpus` reads `grow_bundle["history"]` (~192/239/246/254) but `load_bundle` results carry `df`, so price/momentum/RS are probably always missing from the entry debate.
- `premortem_advisor.driving_pillar_from_bundle` treats sentiment as always available (docstring ~96-97), so a fabricated neutral 50 can be named the driving pillar (feeds 3c).
- The Act Today `critical_news` card and the Review news WATCH are never emailed — no Act Today/Review email renderer exists (feeds 3b scope).

## Owner SQL (Supabase, read-only) — dates the outage
```sql
-- Q1 (best signal): LLM day-cache, empty vs non-empty headlines per UTC day
SELECT score_date, count(*) AS tickers,
       count(*) FILTER (WHERE jsonb_array_length(COALESCE(headlines,'[]'::jsonb)) > 0) AS with_headlines,
       count(*) FILTER (WHERE jsonb_array_length(COALESCE(headlines,'[]'::jsonb)) = 0) AS empty
FROM sentiment_llm_cache WHERE score_date >= '2026-09-01'
GROUP BY score_date ORDER BY score_date;

-- Q2: EOD held-name sentiment
SELECT snap_date, count(*) AS rows, count(*) FILTER (WHERE headline_count > 0) AS with_news,
       sum(headline_count) AS headlines
FROM sentiment_history WHERE snap_date >= '2026-09-01'
GROUP BY snap_date ORDER BY snap_date;

-- Q3: interactive recommendation rows -- pillars measured but sentiment NULL
SELECT rec_date, count(*) FILTER (WHERE t_score IS NOT NULL) AS scored,
       count(*) FILTER (WHERE t_score IS NOT NULL AND avg_sent IS NULL) AS no_news
FROM recommendations WHERE rec_date >= '2026-09-01'
GROUP BY rec_date ORDER BY rec_date;
```

## Risks
- Composites move on deploy (±1-2 pts) — Buy/Hold flips near 65/75 and the first real Skips / critical-news
  WATCHes in weeks; say so in the commit and shipped-log.
- `yf.Search` is an undocumented Yahoo endpoint and can break the same way — the status + tripwire make that loud.
- `none` means "the source returned no headlines tagged to this ticker", NOT proof the name is quiet: BKNG read `ok`/10 in the morning and `none`/0 that evening because raw `yf.Search('BKNG')` itself returned `news: []`. PAY reads `none` (no item tagged PAY). Chunk 3's wording must not say "quiet"; chunk 4's Finnhub fallback on `none` covers both.
- Version coupling: chunk 1 calls the uncached `yfinance.data.YfData().get` on `yfinance.const._BASE_URL_` `/v1/finance/search` with params copied from `Search.search()` (yf.Search itself routes through a process-lifetime `lru_cache` that froze news in the long-lived app process — review round 3). A test pins the params against a real `yf.Search`, so a yfinance bump that changes them fails loudly.
