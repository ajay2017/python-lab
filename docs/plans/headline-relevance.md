# A headline counts only when it names the company

**Status: DESIGNED 2026-10-17 (planner Opus 5.5, PROCEED WITH CHANGES, measured on live Yahoo headlines). **Owner
decisions R1-R9 ALL DECIDED 2026-10-17 — every recommendation accepted** (order: relevance → R9 de-dup → D-7 "ap" → news 3b). Chunk 1 `3440d18` (Opus SHIP after 4 rounds) + R9 `e0d166e` (SHIP) committed; D-7 + docs last; all pushed together. Requirements: F-309. **D-G's count resets from this deploy** (earlier cards were the wrong-company defect).** Triggered by the first live render of F-308
(`docs/plans/held-news-coverage.md`): most Review news WATCH cards quoted headlines about OTHER companies.

## Problem (live 2026-10-17)
Headlines come from `yf.Search` filtered on Yahoo's `relatedTickers`, which tags every ticker a story mentions in passing.
F-308 removed the top-20 cap that hid most of it. Live: ABBV ← "Is Amgen Stock Increasing Your Market Risk?", MRVL ←
"Dell Stock Closes Its Worst Week in Years", XOM ← "Oil Topped $110 as Tech Stocks Lost $1 Trillion in a Day", META ←
"BofA Warns AI Investors Could Lose…", HOOD ← "AMC's CEO warns major exchange…". Production has no LLM (owner principle,
`docs/requirements.md` ~734: the LLM may be used when present, the app must work correctly without it), so the fix is
deterministic; no LLM layer recommended (worth zero in prod; residual errors are polarity, which the existing optional
rescore already targets when a key exists).

## Measurement (planner, live read-only, 18 tickers with news, 180 served headlines)
- Rule keeps 92; precision 92% (85/92 about the company); recall ~95-98% (2 clear misses: an ABBV product press
  release, "Jamie Dimon Warns…"; 6 unclear teasers).
- Negative headlines (≤ −0.05): 19 of 31 dropped, 17 of those clearly not about the company. All five live fixtures dropped.
- **Review cards on the sample: 14 → 12** (Act Today 0 → 0). Every remaining card names the holding (before: 3 of 14).
  Of the 12, ~6 are genuine negatives and **~5 are VADER misreading bullish titles** ("on pace for a record high",
  "back on offense", "Forget X: Buy Y", "Trades Far Below Its Usual Multiple") — a separate polarity problem.
- D-G's pre-registered first option (tier ≤ 2 on Review) measured as a poor lever: keeps three misreads from tier-2
  publishers, drops real tier-3 negatives.
- Composite move (sentiment 10%): −1.03 to +1.04 per name, most within ±0.4.
- Scripts/data: scratchpad `news.json`, `proto.py`, `cards30.py` (session 2026-10-17).

## Design
- **One filter in `bundle_loader`** (gate file, single choke point for app + cron): split the fetched items into
  relevant / unrelated BEFORE the 10-headline cap; `news_raw` = relevant only (every consumer — s_score, Act/Review,
  news flags, Rebalancer, add block, Grow Today Skip, entry check, News Intelligence — gets the decision-grade set);
  new display-only `news_unrelated` (≤ 10), `news_unrelated_n` (None = not checked), `news_relevance_checked`.
  `news_status` stays FEED health (all-unrelated still reads `ok`, so the outage tripwire can't false-alarm).
- **Names:** `info` short/long/display name + the Search response's own `quotes[]` entry for the ticker/sibling +
  a small curated alias map (company/brand only). No usable name → keep all tagged (today's behaviour).
- **Matching:** pure `stock_analyzer/news_relevance.py`; normalised name terms (legal + industry suffixes stripped,
  common-word first tokens stoplisted, ≤4-char single tokens case-sensitive), word-boundary regex, possessives;
  ticker forms `$T`, `(T)`, `NASDAQ: T`; bare uppercase T only if len ≥ 3, not an acronym, title not all-caps;
  "T Stock" for len ≥ 2; multi-company titles count for each company named.
- **Disclosure:** `check_news` gains "no headlines about MRVL — N headlines tagged to MRVL were about other companies
  and not counted" (never "quiet"); the passed line gains " · N about other companies not counted"; Analysis News tab
  gets a caption + collapsed "Not counted — about other companies (N)" expander (escape `$`).
- LLM day-cache hits must be relevance-filtered too (the shared cache can hold rows from a keyed deploy).
- No new constants; no `COMPOSITE_WEIGHTS_VERSION` bump; dated note per D-5 precedent.

## Owner decisions (planner recommendation — ALL ACCEPTED 2026-10-17)
| ID | Decision | Recommendation |
|---|---|---|
| R1 | Apply once in `bundle_loader`; relevant-only `news_raw` for every consumer; dropped headlines display-only | Yes |
| R2 | Sentiment pillar reads relevant headlines only (±1 composite pt; may flip names near 65/75) | Yes, dated note, no version bump |
| R3 | Deliberate loosening: market-wide negative stories stop triggering the Grow Today Skip, the entry hold and the add pause; market risk stays with the tone check | Yes |
| R4 | Aliases: company/brand only; no CEO names, no product names in v1 | Yes |
| R5 | Title-only (a title that doesn't name the company isn't counted, even if the story is about it) | Yes |
| R6 | No name available → keep all tagged (today's behaviour) | Yes |
| R7 | Disclosure wording + Analysis expander as above | Yes |
| R8 | D-G: reset its count from this deploy; re-examine its tier option later | Yes, separately |
| R9 | Fix `curate_news_items`' cross-ticker title de-dup (a relevant multi-company headline reaches only the first holding) as chunk 2 | Yes, own review |

## Plan
1. `fix(news)`: relevance filter (spec in the 2026-10-17 planner report) — implementer + reviewer (`bundle_loader`,
   hook file, new gate module `news_relevance` → `_GATE_FILES` + CLAUDE.md).
2. `fix(news)`: per-ticker de-dup in `curate_news_items` (R9) — implementer + reviewer.
3. D-7 `_TIER1` "ap" substring fix — small reviewed commit.
4. Docs (lead): F-309, architecture module section, User Guide, notes in news-feed-restore / held-news-coverage /
   shared-entry-check.
5. After 5 trading days: re-count cards; design the VADER-polarity lever separately (e.g. finance-phrase overrides).

## Chunk-1 review (2026-10-17): FIX-FIRST, 2 blocking — fixed in build
- **SPCX (held):** Yahoo's names ("Space Exploration Technologies") never yield "SpaceX", so 24 real SpaceX headlines were dropped and a "…Cybersecurity Space" story kept via the generic token "Space". Fix: alias SPCX → SpaceX; "Space" stoplisted.
- **Short names headlines use:** Southern Company, Disney, Schwab, Lilly, Bristol Myers, Supermicro, Oxy (held), plus 3M, JD.com, On Holding (no term at all) — curated aliases (R4 allows company/brand aliases); generic first tokens (Home, International, Texas, Union, Boston, Super, Morgan, Walt, Charles, Philip, U.S.) stoplisted.
- **R7 wording changed by the lead (owner-visible):** "were about other companies" can be FALSE (the SpaceX case — a headline about the company that just doesn't use the name the rule knows). Disclosures now say the headlines "didn't name TICKER in the title", which is true in every case.
- Fail-open (no usable name) is now visible on Analysis: "Couldn't check which headlines name TICKER … all tagged headlines are counted."
- **Known residual:** a holding whose headlines use a brand the alias map doesn't know loses that news from decisions until an alias is added; the disclosure says so honestly. The universe sweep (224 `TICKER_SECTORS` names) is the check.

## Deferred (non-blocking, from the D-7 review 2026-10-17)
- `data.curate_news_items`' title-only branch still builds its key with `title.lower()[:70]` rather than `title_key()` (same result today; drift risk).
- A curly-apostrophe "Barron’s" publisher is tier 3 (was before D-7 too); Yahoo sends "Barrons.com" (tier 1). Normalise ’ → ' if it ever appears.
- "APNews" (one word) would be tier 3; never seen live (Yahoo sends "Associated Press"/"AP"). Add `apnews` to `_TIER1` if it appears.
- Block's product brands ("Cash App", "Square") are not aliases (R4); "Cash App" stories don't count for XYZ — an owner call if wanted.
