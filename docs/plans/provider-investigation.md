# Data provider investigation: Finnhub 429s, FMP 402s, Data Health footnote

**Status (2026-10-25): READ-ONLY INVESTIGATION DONE (lead). Fix design proposed (section 4); owner decisions D-1–D-3
open. Nothing built.**

Trigger: Data Health on 2026-10-23 showed Finnhub with 15 calls OK, **112 rate-limited (429)** and 8 parse errors,
and FMP returning **402 Payment Required on every call**. Queue item 6 in CLAUDE.md.

## 1. Finnhub 429s: what the app does

Finnhub's free tier allows 60 calls/min. `FinnhubProvider.live_prices` (`providers/finnhub_provider.py:39-86`)
makes **one HTTP call per ticker**. `DATA_LIVE_PRICE_ORDER = ["finnhub", "yahoo_finance", "fmp"]`, so every
`data.fetch_live_prices(tickers)` call tries Finnhub first, for **every** ticker.

**Root cause 1: nothing stops the loop once Finnhub says 429.**
- **The per-ticker loop:** after a 429 it records the event and moves to the next ticker (`finnhub_provider.py:77-80`).
  A 100-ticker request that hits the limit at call 60 makes 40 more calls, and all 40 fail.
- **No cooldown on the live-price path:** the circuit breaker (`api_health.in_cooldown`,
  `PROVIDER_RL_COOLDOWN_SEC` = 120) is only consulted by `orchestrator._failover_single` (`orchestrator.py:69-80`).
  `get_live_prices` (`:280-298`), `crosscheck_batch` (`:430-478`), `crosscheck_price` and `crosscheck_against` call
  `prov.live_prices` directly, so a tripped Finnhub keeps being called.
- **`crosscheck_batch` checks the validator's health, never the primary's** (`:453`).

**Root cause 2: bulk, non-real-time lookups go through the real-time source.** Of the ~20 `fetch_live_prices` call
sites in `app.py`, these price whole ticker universes, once per session (`session_state`, not TTL-cached):

| Site | Page | Tickers |
|---|---|---|
| `app.py` ~14304 / ~14374 | 🧾 Summary (engine track record) | the **full rec-ticker universe**, every rec_type |
| ~34441 | 📜 Recommendations History | the same universe (shared cache key, first writer wins) |
| ~35101 | 📊 Predictive Analytics | rec tickers |
| ~44154 / ~44306 / ~46670 | 🧠 AI Insights | rec tickers, held tickers |
| ~49271 / ~49518 | (session-cached track-record views) | rec tickers |

These value historical recommendations, which doesn't need a real-time quote, but each one can fire dozens to 100+
Finnhub calls in a second.

**Steady-state load on top of that:**
- **The Home price strip:** an `st.fragment(run_every=60)` over `_cached_live_prices` (TTL 45 s, `app.py` ~3835 /
  ~5139) costs one call per held ticker per minute while Home is open, about 15/min today.
- **The held-position cross-check** (`_cached` TTL 300 s, ~3948) costs another one call per held ticker every 5 min.
- **So:** one bulk page visit on top of an open Home tab is enough to exceed 60/min, and root cause 1 then turns every
  remaining ticker into a 429.

**Effect today:**
- Yahoo gap-fills every ticker Finnhub misses (`get_live_prices` falls through), so prices still appear, but
  **delayed instead of real-time**.
- While Finnhub is red, `crosscheck_*` uses a degraded primary, and the Home strip quietly runs on Yahoo.
- Nothing is wrong on screen, but the "real-time primary" is mostly not real-time.

**Reading the Data Health numbers:**
- `api_health._stats` is **module-level, per worker process**: it accumulates across every session and rerun since the
  process started or since "Reset counters". "In one session" means "since the last reset or deploy".
- A `rate_limit` event does **not** increment `calls` (`api_health.py:85-89`), so "15 calls" counts non-429 attempts
  only.

**Parse errors (8):** `classify_error` labels any `ValueError` as `parse` (`_util.py:84-86`). That includes
`http_get_json`'s own `ValueError("HTTP <code> — empty response body")` (`_util.py:73-76`) and a JSON decode
failure. Finnhub is also called for `/stock/news-sentiment` (`fetch_news_sentiment`, `finnhub_provider.py:88-126`)
by `news_sentiment.fetch_sentiment_for_tickers`: from `app.py` ~3846 (cached) and from `cron_runner.py` ~925 (once
per held ticker per run). **Which call produced the parse errors can't be told from code.**
- **Next step:** the owner reads Finnhub's "last error" text in 🩺 Data Health (the line under Finnhub, after
  "Fresh:"). If it names `news-sentiment`, check whether that endpoint is premium on the free plan.

## 2. FMP 402 on every call

**What the app calls:**
- FMP is used through the `stable` API (`providers/fmp_provider.py`, `_BASE = …/stable`): quote, EOD history, and
  profile / ratios / estimates / news / earnings for `bundle()` and `info()`.
- `DATA_PROVIDER_ORDER = ["yahoo_finance", "finnhub", "fmp"]`. Finnhub has no bundle capability, so **FMP is the only
  failover for history and fundamentals.**
- It's also the third live-price source (`DATA_LIVE_PRICE_ORDER`) and the backfill for a sparse Yahoo `info`
  (`orchestrator._info_sparse` → `FMP.info`).

**What the 402 means:** FMP says the current plan doesn't cover the request. Whether that is endpoint-level (the
endpoint is premium) or symbol-level (the free plan covers a limited symbol set) **isn't decidable from code**.
- **Next step:** the owner reads FMP's "last error" text in Data Health. Every FMP call also counts against the 250/day
  quota (`db.increment_daily_quota("fmp")`, `fmp_provider.py:271-274`), failed ones included.

**Effect today:**
- **The fundamentals failover is dead.** If Yahoo fails for a ticker, there is no second source, and the
  fundamentals gate withholds the verdict (memory `project_fundamentals_gate`).
- Calls that hit FMP: unknown watchlist symbols that neither Finnhub nor Yahoo price (SPACEX, `6082-HK`, CSSO, UBR;
  owner to-do), sparse-info backfills and Yahoo failures. Each is a wasted call and a false "Data Health: FMP red".

## 3. Data Health "served last saved snapshot" footnote is false

- **`db.save_bundle_cache`** records `"success"` on every write-through **save** (`db.py` ~2109).
- **`db.load_bundle_cache`** records the same `"success"` when it **serves** a last-known-good bundle (`db.py`
  ~2160).
- **The footnote** (`app.py` ~3641-3646) prints "↳ live providers failed for ≥1 ticker; app served last saved
  snapshot instead" whenever `bundle_cache` calls > 0. That is true on every normal session, because saves alone
  count.
- **The fix:** record saves and serves under separate events or sources, and show the footnote only for serves.
  `db.py` is a gate file, so this needs a review.

## 4. Proposed fixes (not built)

| # | Fix | Where | Effect | Review |
|---|---|---|---|---|
| P1 | Finnhub `live_prices` **stops at the first 429** (records one event, returns what it has); the next provider fills the rest | `finnhub_provider.py` | A 429 burst becomes 1 event instead of N | Light |
| P2 | `get_live_prices` and `crosscheck_*` **skip a provider in cooldown**, the same as `_failover_single`; `crosscheck_batch` also skips when the **primary** is red | `orchestrator.py` | No calls into a tripped Finnhub for 120 s | Required (data-integrity path) |
| P3 | **Bulk lookups skip Finnhub**: `fetch_live_prices(tickers, realtime=False)` uses Yahoo's batched download first, for the historical-valuation sites in section 1. The Home strip, Analysis, the Trade Journal and the held-position cross-check stay real-time | `data.py`, `orchestrator.py`, ~8 `app.py` sites | Removes the 100+-call bursts | Required |
| P4 | A process-wide **Finnhub budget** (a token bucket under 60/min, a named plumbing constant): when it's empty Finnhub is skipped, not called | `finnhub_provider.py` + `constants.py` | Never exceeds the free tier even with several tabs open | Required (`constants.py`) |
| P5 | Data Health: separate saves from serves; the footnote shows only on a serve; show Finnhub "attempts" (calls + 429s) so the ratio reads true | `db.py`, `app.py` | Removes a false claim | Required (`db.py`) |
| P6 | FMP: D-1 below | `constants.py` (`DATA_PROVIDER_ORDER` / `DATA_LIVE_PRICE_ORDER`) | | Required |

P1 + P2 are small and remove most of the 429 noise. P3 removes the cause. P4 is a backstop. P5 is independent.

## 5. Owner decisions

- **D-1 FMP:** (a) drop FMP from both provider orders: an honest "no failover", no wasted calls or false red; or (b)
  upgrade the FMP plan to restore a real fundamentals failover; or (c) keep it, if the 402 turns out to be
  symbol-level only. Needs the 402 text first.
- **D-2 scope:** build P1 + P2 + P3 (+ P4) now, or P1 + P2 only.
- **D-3:** whether the `news-sentiment` calls stay, once the parse-error text is known.
