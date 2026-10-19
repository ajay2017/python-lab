# Entry checks read the cached AI news scores (raise-only)

**Status:** DESIGNED 2026-10-19 (planner, Opus 5.5) — verdict **PROCEED WITH CHANGES: Option B (raise-only), gated on
chunk 0's measurement.** Chunk 0 partly run 2026-10-19 (results below): the cron has the key; the sample shows low
decision impact and reopens A vs B. **PARKED pending a full-export replay.** Owner decisions D-1..D-7 OPEN. Nothing built. If chunk 0 shows the cron services have no
Anthropic key and the owner won't add one → RECONSIDER (stay on C), because the morning email would then run VADER
while Grow Today runs AI — the F-301 app-vs-email disagreement class.

## Chunk 0 results (2026-10-19, owner-run SQL + lead replay)
- **(a) The cron services HAVE the key.** `sentiment_llm_cache.created_at` hours (score_date ≥ 10-05): 225 rows at
  12 UTC (08:00 ET premarket), 914 at 13 UTC (09:45 scan), 0-1 UTC = the new UTC day's evening loads. → **D-6 moot**;
  the email and Grow Today already read the same cached AI scores.
- **(b)/(c) 100-row sample (AAPL, ABBV, AFRM, AI; 10-17..10-19), VADER recomputed per title** (scratchpad `replay.py`):
  - The AI scored **12 of the 14 VADER-negative headlines ≥ 0** — e.g. "Forget High-Yield Traps: AbbVie Is the Dividend
    King to Buy and Hold…" VADER −0.23 / AI +0.20. Option B fixes these.
  - **VADER also MISSES real negatives** that only option A would catch: VADER 0.0 vs AI −0.20…−0.35 on "Affirm Falls
    10% as Inflation Print Lifts Yields", "AFRM Stock Dropped", "C3.ai Dips More Than Broader Market", "AFRM Stock
    Looks Stretched" (VADER's lexicon lacks falls/drops/dips). The AI reads looked correct.
  - **0 of 10 ticker-days crossed −0.15 under VADER, A or B** — averaging 8-10 headlines dilutes single misreads, so
    the measured decision impact is small.
  - Caveats: sample only (Supabase UI caps at 100 rows), averages over all cached rows of the day (the check uses the
    first 8), and pre-F-309 unrelated headlines (Amgen/SOUN/PLTR) are in the cache rows the live check would filter.
- **Lead recommendation:** don't build yet. The design's main argument against A (app vs email disagreement) is
  weakened by (a). Next: a FULL export (Export → Download CSV) to count real crossings; ~0 → park; real → owner
  chooses A vs B (data leans A).

## Why
The Anthropic key has been live in Railway since 2026-07-14 (`067e7f9`). The composite's sentiment pillar is AI-
adjusted and Home's Act Today / Review cards pass a suppress-only AI filter (F-308), but every **entry** check —
Grow Today's new-pick "❌ Skip — Negative News" (G-30), the adds check, Watchlist ENTER_NOW (app + email), Analysis —
reads raw VADER. On 10-17 ~5 of 12 Review cards were VADER misreading bullish titles ("on pace for a record high",
"Forget X: Buy Y"); the same misreads can Skip a pick. Those false Skips land on bullish names, which would bias G-30
toward "the skipped names did fine" and argue for dropping the news check for the wrong reason. G-30 has **zero
rows** (10-19), so switching now keeps it to one regime.

**Design constraint:** keep the LIVE headline set and only swap in an AI score where a title matches — never use
the cached set whole. A cache row is frozen at the first load of the UTC day (`news_feed.llm_cache_usable`,
news_feed.py:131-138 only checks non-empty), so a negative headline published later would otherwise never reach the
Skip (fail open).

## 1. Data flow (planner-verified)
- **Producer `bundle_loader.load_bundle`:** 189 `_news_kept` (F-309 survivors, ≤ `NEWS_KEEP_PER_TICKER` 10,
  news_feed.py:33); 196 `analyze_news` VADER per headline `{headline, score, label, url}`; 205 day key
  `_dt.date.today()` = UTC; 206-215 cache hit re-filtered by F-309 (`filter_scored`); 216-218 usable hit replaces
  scores; 229-237 miss + key → `rescore_headlines_llm` (Haiku `claude-haiku-4-5-20251001`, temp 0, 8 s, bidirectional,
  ±`SENTIMENT_LLM_MAX_SWING` 0.5, constants.py:830), saved only if non-empty. **No flag says AI vs VADER.**
- Every consumer already holds the bundle (app `load_all` 4042; cron headless 557) → **zero new API calls / DB reads.**
- **Consumers (all raw VADER):** `data.curate_news_items` (data.py:465; first `_NEWS_PER_TICKER` 8, compound rounded
  to 2) — so entry checks average 8, the composite up to 10.
  - Grow Today new-pick Skip / G-30: `data.curate_brief_news` 514-566 (candidate part 556); app 6141/6557, cron
    headless 569 → `_cross_reference` layer 3 (daily_briefing 529-543) → `_ticker_news_avg` 436/645 →
    `reconcile_signals` (signal_reconciliation 119/146) → G-30 row daily_briefing 1550-1560.
  - Adds (chunk 7): `_entry_verdict` 975/995-1003 reads the Brief's `news_items` — **app = F-308 suppress-only LLM
    output (app 5000-5025), cron = raw VADER (F-308 D-H) → app and cron already disagree on adds.** G-30 add rows 2407.
  - Watchlist ENTER_NOW: app 28197, cron/email headless 1090 → `news_ctx_from_items` (entry_eligibility 365) →
    `check_news` 249-292 → `is_negative_news` 163-165 (`NEWS_SENTIMENT_NEGATIVE` −0.15, constants.py:792). No ledger.
  - Analysis: app 24753 → `analysis_entry_decision` (entry_eligibility 687); its News tab shows AI scores while the
    check uses VADER (known limit, shared-entry-check.md:291).

## 2. Options
- **(A) AI both ways** — matches the composite but can CREATE Skips (prompt asks for ≥0.25 magnitude; a 0.0 can go
  to −0.3); on a cache mismatch the dangerous direction appears (email BUY, app Skip). Not recommended.
- **(B) Raise-only, recommended** — score = max(VADER, AI) per matched title. Invariant holds(B) ⊆ holds(VADER). The
  composite stays bidirectional (a negative AI read is already priced at 10% weight); the veto fires only when both
  see negative. Fallback (no key / no row / LLM failure) = identical to today. A genuine negative can be lifted by
  ≤ 0.5 — accepted risk.
- **(C) Leave as is** — keeps the misreads and the G-30 bias.

## 3. Owner decisions (OPEN)
- **D-1** Option B (alternatives A / C).
- **D-2 Scope:** every ENTRY surface (new-pick Skip incl. layer 3, adds, Watchlist ENTER_NOW app + email, Analysis).
  NOT Act Today / Review (protective, owned by F-308 / `held_news_check`). NOT the composite.
- **D-3 Adds:** rebase on bundle VADER + B overlay (drop the F-308 items for this check only) so app and cron agree.
  Alternative: max(F-308, cache, VADER).
- **D-4 Disclosure:** always a short scorer tag on the news line / hold reason / email ("AI-adjusted 6/8, raises
  only" or "raw sentiment — AI scores not available today").
- **D-5 G-30 provenance (DoD #8):** tag in the existing `reason` text (no DDL; an extra row key makes PostgREST
  reject the upsert) + a dated footnote; dated notes on F-301, F-307, shared-entry-check:291. No version bump
  (composite / `COMPOSITE_WEIGHTS_VERSION` untouched).
- **D-6 Cron key:** if chunk 0 shows none, add `ANTHROPIC_API_KEY` to the cron services or stop.
- **D-7 constants.py:** no change, no new constant (−0.15 and 0.5 stay). Rollback = revert.

## 4. Build plan
0. **Read-only measurement (lead + owner SQL):** (a) `sentiment_llm_cache.created_at` hour distribution vs the
   premarket/scan cron windows — does the cron have the key? (b) replay ~10 days: per ticker, first-8 average
   crossing −0.15 under VADER vs A vs B; (c) the cached AI scores for the 10-17 misread titles — did the AI lift them?
1. **bundle_loader** (gate file, Opus): `sentiment_source` = "llm" only on a cache hit (216) or a saved fresh rescore
   (235), else "vader". Tests for hit/save/no key/None/exception/filtered-empty hit.
2. **entry_eligibility** (Opus): pure `ai_adjusted_news_items(items, bundle)` — only when source is "llm", match by
   exact stripped title, `compound = max(VADER, AI)`, keep `vader_compound`, per-item `scorer`; ignore non-finite /
   bool / out-of-range; never add, drop or reorder. `news_ctx_from_items` gains `n_ai`; `check_news` carries the tag.
3. **Wiring** (Opus; daily_briefing, headless_alert_engine): `curate_brief_news` candidate part (data.py:556), adds
   (daily_briefing 995, per D-3), Watchlist app 28197 / cron 1090, Analysis 24753, G-30 reason tag 1550 / 2407.
   Push 1-3 together.
4. **Docs (lead):** G-30 footnote dated, new F-ID + F-301/F-307 notes, shared-entry-check:291, headline-relevance's
   polarity-lever line, architecture, User Guide, this Status.

## 5. Tests
Bullish misread (assert VADER ≤ −0.15 precondition) + AI +0.2 → passes, tagged; genuine negative AI −0.5 → still
held at the VADER value; VADER 0.0 + AI −0.4 → no hold, plus property test holds(B) ⊆ holds(VADER); fallback
golden-master byte-identical; stale row (cached titles absent) → no change and a new live negative still holds;
F-309 — no cached row added (length/titles unchanged); boundary avg exactly −0.15 holds; app/cron agreement on the
same bundles (identical candidate items and `news_skipped`), AST-pin BOTH the app and headless Watchlist call sites;
G-30 tag in `reason`, key set unchanged; mutation checks (max → AI fails the invariant; deleting the overlay in
`curate_brief_news` fails the misread test) with an assertion that each mutation landed.

## Risks
App/email can still diverge if one side's LLM call fails or misses the cache — under B only an omitted pick, and the
tag discloses it. The UTC day key rolls over at 20:00 ET (EDT) / 19:00 ET (EST), so an evening rebuild can re-score
and rewrite that ET day's app G-30 rows — pre-existing, also affects the composite; queue separately. Upsert race
(last write wins) minor at temp 0. A retired hardcoded Haiku ID silently falls back to VADER — the tag shows it.
Act Today / Review stay on F-308, so an add can be held while Home's Review card was suppressed (two LLM calls) —
visible via the add's "Also in Review" note.

**Screenshot-only:** Grow Today Skip card + "Screened but Filtered Out" tag; Watchlist "📰 News checked" tag;
Analysis News tile vs news tab; Watchlist email reason/footer; no literal `**`, no `$`-LaTeX.
