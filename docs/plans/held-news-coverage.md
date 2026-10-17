# Every holding's news is checked — held-news coverage fix

**Status: DESIGNED 2026-10-17 (planner Opus 5.5, PROCEED WITH CHANGES, checked against HEAD `a8be518`). Owner
decided to fix the cap (vs disclose only) 2026-10-17; **ALL decisions D-A..D-H DECIDED 2026-10-17 — every recommendation accepted** (D-A = 1 trading day); D-I (Tune-up) added + decided 2026-10-17. Chunk 1 `3ea4306` and chunk 2 `543544e` COMMITTED locally (Opus SHIP); chunk 4 + docs (chunk 3) Opus SHIP after one FIX-FIRST round (flags must come from the detectors before card de-dup); all three commits pushed together. Requirements: F-308.** Runs
before news chunk 3b (`docs/plans/news-feed-restore.md`), which then extends this module with `coverage(...)`.

## Problem (lead-verified)
Act Today's critical-news card (`daily_briefing.py` ~2644-2685) and Review's warning-news WATCH (~3163-3205) read only
the HELD part of `news_items`, which is `data.curate_news_items(held_data)` with default `max_items=20` — the 20 most
recent headlines across ALL ~18 holdings (8 read per ticker). A busy holding crowds out the rest, so a quieter
holding's negative headlines never reach either check even with a healthy feed. Paths: app Home `app.py` ~4997-5020
(top-20 → sidebar, LLM suppress-only rescore, `_brief_held_news`) → `data.curate_brief_news` → both Brief builds (app
~6117, ~6522; cron `headless_alert_engine.py` ~568, no rescore).

## Why it is not a bare cap change (planner)
1. The top-20 cap was silently a **recency filter**: neither check reads a headline's age, and items live up to
   `NEWS_MAX_AGE_DAYS` (21), so a critical pair would keep firing Act Today ("tighten your stop") for weeks.
2. `rescore_news_items_llm` has `max_tokens=512` and one JSON array; ~140 items truncate → parse fails → silent raw
   VADER for every item, sidebar included.
3. **Pre-existing bug:** the rescore session key is `_news_llm_{date.today()}_{sorted tickers}` — UTC date, and it
   ignores the headlines. With the same ticker set all day it serves the first render's list to every later Brief
   rebuild, so headlines published after the first Home render never reach Act Today/Review. Happens today whenever
   the top-20's ticker set is unchanged; full coverage makes it constant.
4. Coordination: risk-off exclusion and Rebalancer news flags start firing on many more names (D-D, D-F).

## Owner decisions (planner recommendation in bold — ALL ACCEPTED by the owner 2026-10-17)
| ID | Decision | Recommendation |
|---|---|---|
| D-A | Recency window for both checks, new constant `NEWS_BRIEF_LOOKBACK_TRADING_DAYS` | **1** — from 00:00 ET of the previous NYSE trading day (Mon → Fri, Tue-after-holiday → Fri, Sat/Sun → Fri); inclusive `ts >= start`; cards name the window ("since Fri Oct 16"). 2 roughly doubles how long a card lingers |
| D-B | Undated headlines (`ts <= 0`, only the FMP adapter makes them) | **Not counted**; 3b counts them as "couldn't date" |
| D-C | Single-critical inversion: today one tier-1 −0.60 fires NOTHING (Act needs ≥2, Review band is (−0.25, −0.05]) while a −0.10 fires a WATCH | **Review band = any in-window headline ≤ `NEWS_SENTIMENT_WARN`** on a held name with no Act card; card quotes the worst and says whether one more tier-1/2 ≤ −0.25 would escalate. No constant change |
| D-D | Risk-off de-risk excludes EVERY Act/Review card's ticker before its top-N slice, so a −0.06 headline moves the risk-off TRIM off the biggest beta contributor (pre-existing; contradicts `decision_bucket._reconcile_act`) | **News-only cards (`critical_news`, `watch_kind=="news"`) stop excluding**; drop the news WATCH where a risk-off TRIM lands. Other WATCH kinds keep the exclusion. Must ship with the coverage chunk |
| D-E | `curate_brief_news` de-dups candidate headlines against the held list, so a candidate loses a negative headline it shares with a held name (F-301 known limit) | **Remove the de-dup** (concatenate per ticker); candidate "Skip — Negative News" catches more (safe direction for buys) |
| D-F | Rebalancer `news_flags` (app ~20701) derive from the top-20 `_sidebar_news` → would disagree with the Brief | **Home publishes `_brief_news_flags_cache`** from the Brief's own cards (`None` when offline, registered in `coord_freshness`); Rebalancer reads it, falls back to today's derivation with a caption. Changes the Rebalancer's "critical" meaning from weight ≥ 8% to the Brief's ≥2 headlines |
| D-G | Review clutter (estimate: ~2-4 news WATCH/day vs ~2 today, after the window + LLM) | **Accept one card per ticker; pre-registered trigger:** ≥5 news WATCH cards on 3 of the first 5 trading days → revisit, first option tier ≤ `NEWS_CRITICAL_MAX_TIER` on Review |
| D-H | Cron Brief has no LLM rescore | **No LLM in cron** — its Act/Review are never emailed or persisted; only effect is a few more silent add-lane ACT blocks (fewer cron add-gate ledger rows on bull days) |
| D-I | (Added 2026-10-17 by the chunk-2 review) Portfolio Tune-up's beta card is silently dropped when its root ticker carries ANY Act/Review card (`_acted_final`), incl. a news WATCH from one −0.05 headline | **News-only cards don't drop it** (same rule as D-D; a news WATCH isn't a trim) — DECIDED by the owner 2026-10-17 |

## Build plan (push 1-4 together; 2 and 4 must not ship apart)
1. `fix(news)`: batched (25/batch, `max_tokens = 64 + 20n`, 3 concurrent workers), memoized (per-ET-day memo keyed
   on (TICKER, title[:70]); 600 s backoff after a failed batch), decision-scoped LLM rescore — sends the top-20 plus
   in-window items ≤ WARN, at most 75; over-bound items stay raw VADER and are disclosed. Home: full held list,
   sidebar = `full[:20]` (planner verified identical to today's 20), caption when headlines went unfiltered. New pure
   `stock_analyzer/held_news_check.py` (`news_window_start`, `item_ts`, `rescore_selection`). Cost est. ≤ ~$1.50/mo.
2. `fix(brief)`: every holding checked within the window — `critical_news_by_ticker` / `warning_news_by_ticker` in
   `held_news_check.py`; `daily_briefing` reads them; `curate_brief_news` held default uncapped + D-E; new constant;
   `_GATE_FILES` + CLAUDE.md list gain `held_news_check`; `_SYNTH_SCHEMA_VER` bump. Card wording names the window and
   the real count/tier.
3. Docs (lead): architecture constants rows, requirements F-22/F-301/F-13, User Guide Act Today, this plan, a re-plan
   note on `shared-entry-check.md` chunk 7, CLAUDE.md queue, shipped-log.
4. `fix(brief)`: D-D risk-off exclusion + D-F `brief_news_flags` / `_brief_news_flags_cache` / Rebalancer.

Full function signatures and the required test list (window boundaries incl. MLK/Thanksgiving/DST, threshold
boundaries, the non-vacuous "QUIET holding behind 19 busy ones now fires" end-to-end, sidebar parity, rescore
batching/memo/backoff, D-D/D-F, AST wiring, four named mutations) are in the 2026-10-17 planner report, transcribed
into the implementer spec at build time.

## Knock-ons
- **Shared-entry chunk 7 must be re-planned:** with D-C a single tier-1 −0.40 gets a Review card and is ACT-blocked
  before chunk 7's `news_blocked_adds`; the add lane should read this module's verdict (one owner), and chunk 7's job
  becomes making the ACT block visible.
- **D-7 (`_TIER1` "ap" substring → AppleInsider/TheWrap tier 1)** bites more often (more items pass tier ≤ 2) — more
  urgent, still separate.
- 3b's "checked" wording must count IN-WINDOW headlines and name the window; expected set from `port_df`, never
  `held_data` (F-292 lesson).
- The Brief is memoized on holdings/date, not news; fresh headlines arrive only on a rebuild (unchanged).

## Found by the chunk-2 review (not built)
- `curate_news_items` still de-dups a headline naming two holdings across held tickers, so it counts for the first one iterated only (the D-E limit, within the held set).
- No test pins `_GATE_FILES` against CLAUDE.md's enumeration (only that every entry is git-tracked).
