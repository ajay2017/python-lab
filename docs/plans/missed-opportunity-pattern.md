# O1 — Missed-Opportunity Pattern — Design Plan

**Date:** 2026-07-24
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** SHIP (revised after Opus design review — FIX-FIRST round resolved). Ready for
implementation.

> **Opus design review (round 1): FIX-FIRST** — 2 blocking findings, both fixed in this
> revision. **(1)** The claim that "no verification layer is needed" (because the LLM
> works from structured fields, not free prose) was backwards — every field supplied
> (sector, price, composite score, verdict, outcome label) is a **closed, checkable
> category**, so the LLM's claimed commonality can and must be mechanically verified,
> which is actually *easier* than D1's fuzzy quote-matching, not harder. Fixed: the
> prompt now requires a structured `shared_dimension`/`shared_value` claim per pattern
> (drawn from a fixed set: sector, price-band, composite-band, verdict, outcome-label),
> verified in pure Python against each cited ticker's real pre-computed value; a
> non-conforming member is dropped, not the whole pattern. **(2)** The plan didn't
> specify which `enriched` snapshot feeds the corpus builder, and the obvious copy (the
> adjacent flat missed-list's `_rh_enriched`) is status-filtered — under "Missed only"
> it would defeat `distinct_missed()`'s "acted via ANY surfacing" safeguard; under "Acted
> only" it would silently empty the corpus. Fixed: the plan now specifies
> `_rh_enriched_all` explicitly, matching `signal_flow`'s own documented rationale.
> Also folded in: include ALL outcomes (not winners-only, a posture safeguard against
> becoming a FOMO amplifier); render each pattern's win/dodge/flat mix, not just tickers;
> reframed the prompt from causal ("why you skipped them") to descriptive ("what they
> have in common"); added an explicit non-goal against ever cross-referencing a pattern
> to a live/current candidate (the one design choice that would turn this into a
> disguised forward-looking buy signal); None/empty-safety for `alpha_pct`/
> `composite_score`/`sector`; date serialization for the cache snapshot.

> **One-line spec:** A new "🔍 Missed-Opportunity Pattern" section on the 📜
> Recommendations History page, right after the existing flat missed-opportunities
> list, that has Haiku find a **qualitative** pattern across engine "new_pick" buy
> recommendations the user never acted on — grounded entirely in real, already-computed
> outcome data (real ticker, sector, price, alpha vs SPY) — rather than a rigid
> statistical bucket-and-gate, because the missed-rec pool is too thin for buckets to
> ever clear a sample-size floor.

> **Roadmap context:** Priority 2 of [agentic-intelligence-roadmap-v2.md](agentic-intelligence-roadmap-v2.md)
> (paired with D1 as "one flagship per side"). Deliberately the "P6 done right" idea —
> P6 (Autonomous Pattern Discovery) was shelved because its only untapped dimension
> (`decision_context`) had 17 rows; this points the same unsupervised-discovery
> instinct at a real, already-accumulating dataset instead.

---

## Why this design, not a naive statistical rebuild

**Verified against HEAD (2026-07-24):** `stock_analyzer/recommendations_history.py`
already has `distinct_missed(enriched, rec_types=("new_pick",))` (`:415`) — one row per
distinct ticker the user never acted on, with real outcome data (`outcome_pct`,
`alpha_pct`, `outcome_label`) already gated by `REC_SCORE_MIN_DAYS=5` maturity
(`constants.py:761`) — and `missed_split()` (`:479`) splitting those into
winners/dodged/flats. **No sector/price-band grouping exists anywhere** — that would be
the naive first instinct for "genuinely new."

**A real data-volume check ruled that instinct out.** New-pick surfacing is capped at
`GROW_MAX_PICKS_BULL=3` / `GROW_MAX_PICKS_DEFAULT=1` per day (`constants.py:130-131`).
Account query (2026-07-24): 116 total `new_pick` surfacings, **44 distinct tickers**
ever surfaced. The missed-only subset is smaller still. Splitting that further across
~8-11 GICS sectors (or price bands) would leave most buckets at 0-1 tickers — the exact
statistical-validity wall that forced D1 away from a naive design and, before that,
sank the original P6. `predictive_analytics.py`'s `by_sector_alpha`/`by_conviction`/
`by_rec_type_stats` (`:654,591,621`) already do sector/conviction/rec-type bucketing
with a `min_n=3` floor — but mixing acted+missed together, and a missed-only rebuild
of that same mechanism would show "not enough data" for nearly every bucket for months.

**No setup/trigger-type field exists on recommendations either** — only `sector` and
`price_at_surface` are real groupable dimensions; the 4-pillar composite breakdown
(momentum/valuation/fundamentals/sentiment) is not persisted per-pillar on rec rows, so
a "momentum-driven vs. valuation-driven miss" grouping isn't derivable without new
instrumentation (the same shape of gap that killed P6's `decision_context` idea).

**User decision (2026-07-24, confirmed via question):** rather than force a statistical
grouping the data can't support, apply D1's proven solution to this substrate — Haiku
finds a *qualitative* pattern across the real missed-ticker records, citing which
specific tickers exhibit it, with the real supporting data (sector, price, alpha)
displayed alongside so the pattern's grounding is checkable by eye, not by a rigid
bucket count.

---

## What already exists (reused, not rebuilt)

| Piece | Where | Status |
|---|---|---|
| Missed-ticker determination + outcome grading | `recommendations_history.distinct_missed()` (`:415`) — a ticker counts as missed only if NONE of its surfacings were acted on; outcome/alpha come from the earliest priced+mature actionable surfacing | Shipped. **Reused verbatim** — this plan does not re-derive "missed," it consumes `distinct_missed()`'s output directly. |
| Win/loss/dodge split | `recommendations_history.missed_split()` (`:479`) | Shipped, unchanged — already rendered on the page; this plan adds a sibling section, doesn't touch it. |
| Sector / price-at-surface fields | `sector`, `price_at_surface` on the `matched`/`enriched` rows (`recommendations_history.py:126,129`) | Shipped — real, but NOT guaranteed non-empty (`sector` can be `""`, mapped to `"Other"`; `composite_score` can be `None`, mapped to the existing `"Unscored"` band). Bucketed into closed categories (design principle 5), not left as raw free values. |
| Render location | 📜 Recommendations History page (`app.py:20521`), flat missed list already at `app.py:20789/20791` | Shipped. This plan adds a new section immediately after it. |

## What's genuinely new

1. **`build_missed_opportunity_corpus(enriched_all, rec_types=("new_pick",))`** (pure
   Python) — **must be called with `_rh_enriched_all`, the UNFILTERED snapshot, never
   the page's status-filtered `_rh_enriched`** (see Resolution below; this is the
   fixed blocking finding #2). Calls `distinct_missed()` to get the real missed-ticker
   rows (ticker, first_rec_date, n_surfaced, verdict, outcome_pct, alpha_pct,
   outcome_dollars, outcome_label — reused verbatim, no new "missed" logic), then
   enriches each with three **pre-computed categorical fields**, closing the guard gap
   (fixed blocking finding #1):
   - `sector` — real value, empty string mapped to `"Other"` (empty is possible per
     review finding; matches the existing sector-fallback convention elsewhere in the
     codebase).
   - `price_band` — one of a **fixed, closed set** of module-level bands (e.g. "under
     $50" / "$50-150" / "$150-300" / "over $300") computed from `price_at_surface`.
   - `composite_band` — reuses `recommendations_history.by_composite_band()`'s own
     existing band labels/thresholds (`COMPOSITE_BUY`/`COMPOSITE_STRONG_BUY`/
     `COMPOSITE_HOLD` from `constants.py`) rather than inventing new breakpoints;
     `None` composite_score maps to the existing `"Unscored"` label.
   These three, plus the already-present `verdict` and `outcome_label`, form a
   **closed vocabulary of 5 groupable dimensions** — every one mechanically checkable.
   Field lookup uses `(ticker, rec_date == first_rec_date, rec_type in rec_types)` —
   the `rec_type` filter (not just date) narrows to the exact pool `distinct_missed()`
   drew its representative row from, closing the "could match a different same-day
   surfacing" ambiguity the review flagged as low-risk but fixable.
2. **One Haiku call/day** (button-gated, day-cached): given the full corpus (ticker +
   5 categorical dimensions + outcome_pct/alpha_pct for display), identify 1+
   **descriptive** patterns in what gets systematically skipped — reframed from a
   causal "why you skipped them" to a descriptive "what these skipped names have in
   common" (posture fix; see Resolution). Each pattern cites the **specific tickers**
   plus a **structured `shared_dimension`/`shared_value` claim** drawn from the closed
   set above. **Never forces a pattern** — "no coherent pattern found" is a valid,
   expected, cacheable answer, mirroring D1/P5's honest-null handling.
3. **Two-layer fabrication guard** (pure Python, mirrors D1's structure exactly,
   adapted to structured data):
   1. **Ticker validation** — every cited ticker must normalized-match a ticker in the
      supplied corpus; unknown tickers dropped.
   2. **Predicate verification** (the fixed blocking finding #1) — the claimed
      `shared_dimension`/`shared_value` must be `None`/unrecognized-safe, and every
      remaining cited ticker's real pre-computed value for that dimension must
      normalized-match `shared_value`; a ticker that doesn't match is **dropped from
      the pattern**, not the whole pattern discarded (same "drop the member" policy as
      D1). A pattern falling below `_MIN_PATTERN_TICKERS` (2) after both layers is
      discarded entirely.
4. **New cache table** `missed_opportunity_cache` (portfolio-wide, one row per
   `scan_date` — mirrors `thesis_cluster_cache`'s exact pattern, RLS included, never
   caches a failed/empty result; `first_rec_date` serialized to ISO string before
   caching — jsonb can't hold a raw `date` object).
5. **A render section** on 📜 Recommendations History, sibling to the existing flat
   missed list — not nested inside any of that list's own conditionals. Each pattern
   card shows the win/dodge/flat mix among its cited tickers (posture safeguard — see
   Resolution), not just a ticker list, so a pattern can never read as "these were all
   winners, buy the next one like it."

**Explicitly NOT built:** no sector/price-band statistical bucketing (ruled out above),
no new "setup type" instrumentation on recommendation rows, no change to
`distinct_missed()`/`missed_split()` themselves, no scoring/gate of any kind, no
forced pattern when the data is genuinely unrelated, **no cross-reference of a found
pattern against current candidates, the watchlist, or held positions** (see Non-goals —
this is the one design choice that would turn a retrospective pattern into a disguised
forward-looking buy signal).

---

## Design principles (non-negotiable)

1. **Strictly additive.** Nothing here touches a gate, the composite score, or
   `distinct_missed()`/`missed_split()`'s own output. Pure retrospective diagnostic.
2. **Graceful degradation on thin coverage.** Fewer than `_MIN_MISSED_TICKERS` (3)
   graded missed tickers → plain message, no button, no LLM call. Given 44 distinct
   surfaced tickers today, this floor is likely already clear, but the account will
   start thin and must degrade honestly regardless.
3. **Never fabricates a pattern.** The prompt must explicitly permit "no coherent
   pattern" as an answer — two coincidentally-similar misses should not be forced into
   a manufactured commonality.
4. **Ticker validation, canonical labels.** Any ticker the LLM returns must be
   normalized-matched against the supplied corpus and rendered using the canonical
   stored ticker — never the LLM's echoed text. A cited ticker not in the corpus is
   dropped.
5. **Predicate verification — the claimed commonality must actually hold.** Per Opus
   review: every field this feature groups by is a closed, checkable category, so
   there is no excuse not to verify. A cited ticker whose real `sector`/`price_band`/
   `composite_band`/`verdict`/`outcome_label` doesn't match the pattern's claimed
   `shared_value` is dropped from that pattern (not the whole pattern discarded,
   mirroring D1's "drop the member" policy). This is stricter grounding than D1's own
   quote-matching, not a weaker analog of it.
6. **Button-gated, day-cached.** Same `st.tabs()`/rerun-executes-everything discipline
   as P3/P5/D1 — even on a non-tabbed page, any interaction anywhere on 📜
   Recommendations History reruns the whole script, so this must not auto-compute.
7. **Show the real data, not just the claim.** Every cited ticker's real sector,
   price-at-surface, and alpha outcome render alongside the pattern narrative — visible
   confirmation of what the predicate-verification step (principle 5) already checked
   mechanically, not the primary grounding mechanism itself.
8. **Include all outcomes — a posture safeguard, not just a completeness nicety.**
   Winners-only would make this a FOMO amplifier ("here are the money-makers you
   skipped — buy the next one"). Dodged losers and flats are included precisely so a
   pattern's tickers can never all be winners; each pattern card renders its win/dodge/
   flat mix for exactly this reason.
9. **Descriptive, never causal or forward-looking.** Per Opus review, the prompt asks
   "what do these skipped names have in common" — not "why did you skip them" (a causal
   framing invites an unverifiable psychology claim that reads as "so stop doing that /
   buy the next one like it"). This is retrospective awareness only; see the explicit
   Non-goal against ever cross-referencing a pattern to a live candidate.

---

## Spec — Haiku pattern call

**Input corpus** (per qualifying missed ticker): `ticker`, `sector` (empty→"Other"),
`price_band` (fixed closed set), `composite_band` (reuses `by_composite_band`'s
existing labels), `verdict`, `outcome_label`, plus `outcome_pct`/`alpha_pct` for
display only (not a groupable dimension — continuous, not categorical). All real,
already-computed by `distinct_missed()` (called on `_rh_enriched_all`) + this plan's
enrichment step — zero new fetch. `alpha_pct` may be `None` (SPY window coverage gap)
and must render as "n/a", never coerced to 0.

**Prompt task:** "Given these N buy recommendations you never acted on, identify any
group of 2 or more that share a real common trait — even if their sectors differ.
Describe **what these skipped names have in common**, not why you might have skipped
them. For each group, name the shared trait using ONLY one of these dimensions:
sector, price_band, composite_band, verdict, or outcome_label — and state the exact
value (e.g. dimension='price_band', value='under $50'). Cite the specific tickers. If
no group shares a genuine common trait, say so plainly — do not force a connection
between misses that merely happen to sit near each other in the list."

**Output validation (two layers):**
1. Each returned ticker must normalized-match a ticker in the supplied corpus (drop
   any that don't).
2. The claimed `shared_dimension` must be one of the 5 recognized values; for each
   remaining ticker, its real pre-computed value for that dimension must
   normalized-match the claimed `shared_value` — a ticker that doesn't match is
   dropped from the pattern. An unrecognized `shared_dimension` discards the whole
   pattern (nothing to verify against).
A pattern with fewer than `_MIN_PATTERN_TICKERS` (2) valid, verified tickers after both
layers is discarded.

## Spec — cache table (mirrors `thesis_cluster_cache`)

```sql
create table if not exists public.missed_opportunity_cache (
    scan_date       text        NOT NULL,
    patterns        jsonb       NOT NULL,  -- [{tickers, shared_dimension, shared_value, pattern_label}, ...]
    missed_snapshot jsonb       NOT NULL,  -- corpus used (dates ISO-serialized), for auditability
    created_at      timestamptz DEFAULT now(),
    PRIMARY KEY (scan_date)
);
alter table public.missed_opportunity_cache enable row level security;
drop policy if exists "Allow all (service role)" on public.missed_opportunity_cache;
create policy "Allow all (service role)" on public.missed_opportunity_cache
    for all to service_role using (true) with check (true);
```

Never cache a failed/empty LLM call. "No coherent pattern found" is a valid, cacheable
result (distinct from a failure). `first_rec_date` (a `date` object) must be
`.isoformat()`-serialized before being written into `missed_snapshot` jsonb.

## Spec — render

New section on 📜 Recommendations History, sibling to (not nested inside) the existing
flat missed-opportunities list:
- Section header "🔍 Missed-Opportunity Pattern" + one-line explainer.
- If <3 graded missed tickers: plain caption, no button.
- Else: button "🔍 Look for a pattern" (or cached result if same-day row exists).
- Each pattern: the tickers involved, the plain-English `pattern_label`, the verified
  `shared_dimension`/`shared_value` chip, the **win/dodge/flat mix** among its cited
  tickers (design principle 8 — never lets a pattern read as all-winners), and each
  ticker's real sector/price-at-surface/alpha% shown inline or in a collapsed expander.
- If Haiku found no pattern: plain "No systematic pattern found in what you've
  skipped" message — legitimate, calm, not an error.

---

## Resolved design questions (Opus rulings)

1. **Include ALL outcomes — required as a posture safeguard, not just an information
   call.** Winners-only risks becoming a FOMO amplifier; dodged losers are the
   structural defense that keeps this retrospective. See design principle 8.
2. **Not sufficient as originally proposed — fixed.** Every field here is a closed,
   checkable category, so the predicate-verification guard (design principle 5) is
   both possible and required — stricter grounding than D1's own quote-matching, not
   a weaker analog.
3. **Corpus floor `_MIN_MISSED_TICKERS=3`: confirmed**, matching
   `predictive_analytics.py`'s `min_n=3` convention. **Per-pattern floor
   `_MIN_PATTERN_TICKERS=2`: acceptable now that the predicate-verification guard
   (principle 5) exists** — Opus's own conditional ruling: 2 unverified tickers would
   be the exact D1 failure mode, but 2 *verified* tickers (both checked against the
   real closed-category value) is sound.

## Non-goals

- Does not modify `distinct_missed()`, `missed_split()`, or any existing
  Recommendations History content.
- Does not gate, score, or suppress anything.
- Does not attempt sector/price-band statistical bucketing (ruled out above).
- Does not recommend acting on names resembling the pattern — retrospective awareness
  only, never forward-looking advice.
- **Never cross-references a found pattern against current Grow Today candidates, the
  watchlist, or held positions.** Per Opus review's posture stress-test: the moment a
  retrospective pattern is joined against a *live* name, it functions as a disguised
  forward-looking buy signal, regardless of how the feature is framed elsewhere. Any
  future proposal to add such a join is a new decision-adjacent feature requiring its
  own Opus review, not an "enhancement" to this one.
