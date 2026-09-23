**Status, updated 2026-09-23: Phase 0 CLOSED, Phase 1 SHIPPED + VISUALLY CONFIRMED, Phase 2
FULLY SHIPPED (all 8 tables).** Owner ran all four Phase 0 queries live (§9) — no truncation
found anywhere except `recommendations` itself (1473 rows, confirmed over the 1000-row cap) and,
discovered while scoping Phase 2, `analyst_coverage` (678 rows against a caller-facing
`limit=100` default — 85% silently dropped, worse than `recommendations`). Both known
duplicate-row cleanups (`snaptrade_income_events`, `account_flows`) confirmed already clean.
Persistence multipliers measured live: `recommendations` new_pick 293 rows/78 tickers (3.8x),
`gate_suppressions` 264/80 (3.3x), `judgment_grades` non-portfolio 230/48 (4.8x) — with the
`judgment_grades` count exactly matching that table's total, confirming zero `_PORTFOLIO`-keyed
rows exist yet (Phase 5 has nothing to act on today). `recommendations`/`exit_signals`
pre-2026-07-09 fractions: 395/1473 (27%) and 0/98 (0%) — sizes Phase 4's real urgency to
`recommendations` only.

**Phase 1 shipped as commit `6c60656`** — Opus reviewer SHIP, 0 blocking, full suite 6116 passed.
**Visually confirmed the same day** against real production data on all three surfaces it
touched: the 🎯 Engine Track Record card (real before/after screenshot, Offense 21→30 matured /
+6.3pp→+8.3pp, Defense correctly unchanged as a negative control), 🧑‍⚖️ The Judge (direct SQL
match — on-screen `n=45`/`n=38` matched live `distinct_tickers` counts exactly, not just
plausible), and 🛑 the Gate Suppression Ledger (clean negative control, every gate still
"building" exactly as expected, nothing broke). **Nothing left to validate on Phase 1.**

**Phase 2 fully shipped, in two commits.** The `recommendations` slice shipped first as commit
`eea121d` (pulled forward because it was a confirmed prerequisite for trusting Phase 1's own
re-derived numbers) — Opus reviewer SHIP, 0 blocking. The remaining 7 tables (9 function bodies)
plus the differently-shaped `analyst_coverage` fix shipped as commit `0f583fe` — **this one
needed two review rounds**, worth remembering as a real example of the mandatory-review gate
catching something the deterministic tests couldn't: the first Opus pass returned FIX-FIRST,
finding that 3 of the 8 paginated queries ordered by a non-unique date column alone
(`daily_snapshots` by `snapshot_date`, `rec_events` by `fired_date`, `analyst_coverage`'s
unbounded path by `article_date`) — `.range()`-based pagination re-executes the query fresh per
page, and Postgres doesn't guarantee a stable tie order across separate executions without a
unique `ORDER BY`. For `daily_snapshots` specifically, ties aren't an edge case — its own PK is
`(snapshot_date, ticker)`, so every multi-ticker day ties. A silently wrong pagination fix would
have been WORSE than the truncation bug it exists to close (a silently duplicated or skipped row
in Capital Trend / Alpha Attribution / F-250 day-P&L, with no error and no offline-sentinel
trip, vs. an honest large-but-incomplete read). Fixed by adding each table's own unique key as a
secondary sort; the other 5 functions already ordered by a genuine unique `id` column and needed
no change. Second Opus pass: SHIP, 0 blocking. Full suite 6140 passed both gates green.

**Phase 3 fully closed, all five items.** A4 shipped inside Phase 1. A5 (commit `8dc11fa`) fixed
`debrief_advisor.py`'s date-filter bug — turned out to be 4 real instances, not the 2 originally
scoped. A8 (income-event mislabeling) was investigated by a `planner` pass and found to be a
non-issue once every real consumer was traced — closed with 5 invariant-locking tests, zero
`broker_sync.py` change. C2 confirmed clean. C3 (commit `06cc05b`) closed a 29-ticker
sector-taxonomy gap, the same class already fixed 6 times before.

**Phase 4 SHIPPED 2026-09-23 (commit `6448265`), design-first as the owner explicitly
requested.** A `planner` pass re-verified `COMPOSITE_WEIGHTS`'s single-boundary premise back to
the very first scoring commit (2026-05-05) and found only `recommendations` is actually
ambiguous. Owner decided: proceed with a `weights_version` column (the backfill is uniquely
lossless), scoped to `recommendations` only. `COMPOSITE_WEIGHTS_VERSION` constant + the column,
stamped unconditionally at the one shared write boundary; a new, permanent Definition-of-Done
item (#8) is the actual recurrence-prevention, not the column itself. Opus reviewer SHIP, 0
blocking on the first pass. **DDL applied and verified live the same day:** 395 rows at
version 1 (matches the pre-backfill count exactly), 1118 at version 2 (up from 1078 the day
before — normal cron growth, not a discrepancy), zero `NULL` rows.

**Phases 0-4 are now ALL SHIPPED, RESOLVED, OR CONFIRMED — code, tests, review, and live
database state.** Only Phase 5 remains, correctly deferred since it has zero data to act on.
**One small new item found while building Phase
2, flagged but deliberately not fixed (out of scope for that change):** two standalone
diagnostic scripts (`scripts/exit_early_cost_analysis.py:379`, `scripts/holding_period_analysis.py:164`)
each define their own raw-REST `load_exit_signals()`, bypassing `db.py` entirely, with the same
unpaginated exposure this whole Phase 2 effort exists to close. Owner-run diagnostics, not named
in the original task — worth having them adopt `db.load_exit_signals` eventually, not urgent.

**A second small item, found and CLOSED the same day (commit `a614173`):** Phase 2's own commit
message deliberately left 3 `analyst_coverage` call sites on the bare `limit=100` default,
untested for whether their own `days=`/`ticker=` scoping ever exceeds 100 rows in practice. A
live query answered it same-day: **204 real articles fall within the 30-day
`ANALYST_COVERAGE_FRESH_DAYS` window** — two `app.py` call sites using that window (the Ideas
Inbox header count, and the per-ticker "newest analyst read" annotation dict) were **already**
silently truncating in production, not a future risk. Fixed by adding `limit=None` to both —
pure reuse of the unbounded mode Phase 2 already built and reviewed, no new read logic. The
third bare-default site (`bundle_loader.py`, per-ticker + 90-day window) was checked and
confirmed safe (worst case 26 articles for one ticker in 90 days) — left untouched, correctly.
Mechanical, non-gate, awareness-only fix — voluntary reviewer skipped per the review-economy
rule (full suite 6140 passed, both gates green).

This is a planning/design deliverable that has since had two of its phases executed under
review, per explicit sign-off at each step — not a unilateral build. Every finding below is
tagged with how it was verified — code-cited (file:line, checked directly this session, not
recalled from memory), or flagged as needing a live Supabase query this session cannot run
(§9's queries have since been run; results are folded in above and are no longer hypothetical).

**Revision note (same day).** The first draft's A1 mechanism description was wrong in a way
that mattered, and its Phase 0→1 ordering was backwards for the item that most needs to ship
soon. A `planner` (Opus) pass reviewed the draft specifically to catch this kind of
self-authored error before it reached the owner — its corrections were verified directly
against the code (not taken on faith) before being folded in below. Corrected: A1's mechanism,
A2's overstated maturity claim, A3's mixed per-ticker/portfolio shape, D1's "clock vs. live
number" framing, the Phase 0/1/4 ordering, and control #1's scope.

---

## 0. Why this exists

DRISHTA has spent most of its life shipping capability. Several trend/behavioral/historical
intelligence features (Engine Track Record, Gate Suppression Ledger, The Judge, Score History,
Behavioral Fingerprint) are now live, but their signal is reportedly weak, incomplete, or not
yet evaluable. The working hypothesis going in was "the early app didn't capture what the
current analytics need." That hypothesis is **partially right, but not the dominant story** —
the research below found the biggest problems are current, ongoing **aggregation-logic bugs**
in code that's shipping today, not missing history. Both classes matter; they need different
fixes, and conflating them would send effort at the wrong problem first.

**Headline finding:** three of the app's `n_mature`/`avg_alpha` trend headlines — Engine Track
Record (Offense facet), the Gate Suppression Ledger, and part of The Judge's witness track
record — currently compute over a **row population that counts one real signal episode
multiple times** (once per day it persisted), while two sibling features that face the exact
same data shape (`protective_track_record`, `rec_events_readout`) already fixed this. This is
not a missing-data problem. It is fixable today, against data that already exists, with no
backfill and no new capture. **The highest-value instance of this (Engine Track Record) is not
symmetric** — verified precisely in §2 A1 below, it distorts the "missed" side of the ledger
specifically, and fixing it will move a number already displayed on screen, not just clean up
an internal computation.

---

## 1. Method

Three parallel code-verified research passes (schema/backfill audit, analytics-feature/maturity
audit, entity-resolution/mapping audit) plus direct verification of the two highest-stakes
claims by the lead before writing this up (grepped `recommendations_history.py` /
`gate_ledger_readout.py` / `judgment_grading.py` directly for a collapse function — confirmed
absent in all three; walked the full `git log -p` history of `constants.py` to confirm
`COMPOSITE_WEIGHTS`'s dict body changed exactly once, not the four times a naive grep suggested;
read `trade_time.py` in full to precisely scope the debrief-advisor date-parsing finding rather
than repeat the research pass's broader framing). Every claim below is either **[CODE-VERIFIED]**
(cited to file:line, checked this session) or **[NEEDS LIVE QUERY]** (a real row-count/date/DB
fact this environment has no Supabase credentials to obtain — consistent with this project's
established precedent that live data checks are run by the owner via direct SQL, not by a
coding session).

---

## 2. Section A — Aggregation-logic bugs inflating or distorting signal TODAY (fix the code, not the data)

These are the highest-value fixes: no backfill needed, no new capture needed, pure
computation-over-existing-data corrections. Ranked by blast radius.

### A1. [CODE-VERIFIED] Engine Track Record / Recommendations History has no per-episode collapse — and it distorts the MISSED side specifically, not evenly

`recommendations_history.py`'s `summary_stats()` (line 559) and `by_rec_type()` (643) operate
directly over `enriched`, the raw row list from `recommendations` — and the premarket cron
writes **one `new_pick` row per ticker, every single day it still qualifies**
(`cron_runner.py:156-167`, upsert key `ticker, rec_date, rec_type`). A ticker that stays a
top pick for 10 consecutive days contributes 10 independent rows.

**Precise mechanism, corrected after direct re-verification of `match_recs_to_trades`
(185-255):** `acted_on` is set per-row to `trade is not None`, keyed on `(ticker, rec_date)`
against a real same-day trade (219, 237-238). The owner buys a name **once**, on one real day —
so of a 10-day streak, at most one or two rows land `acted_on=True`; the other eight-plus are
`acted_on=False`, i.e. **missed**. This means:
- `avg_acted_alpha` is barely affected — roughly one row per genuinely-acted ticker, not ten.
- **`avg_missed_alpha` is the one actually distorted** — persistence-weighted (a name that
  stayed a pick for three weeks contributes ~15 missed rows, one that briefly touched the bar
  for two days contributes 2), which is the opposite of "evenly inflated."
- **A second, distinct defect compounds it:** `summary_stats` splits purely on each row's own
  `acted_on` flag with no "was this ticker acted on via ANY of its surfacing days" exclusion —
  unlike `distinct_missed()`'s own anti-inflation guard for the BUY side. So a ticker the owner
  DID eventually buy still contributes its earlier not-yet-acted days to the missed pool,
  polluting `avg_missed_alpha` with rows that turned into a real buy, just not on that specific
  day.
- `action_rate = n_acted/n_total` is **deflated, not inflated** by persistence — a name bought
  once but surfaced ten times reads as a 10% action rate for that name, understating how
  responsive the owner actually was.

This is the metric behind the 🎯 Engine Track Record pointer card, the 📜 Recommendations
History page, and (transitively) Behavioral Fingerprint's momentum-recency pattern (which
reads the same unaggregated `matched` list — `behavioral_fingerprint.py:38-58`).

**Contrast:** two sibling features hit the identical table shape and already fixed it.
`protective_track_record.py:22-28` calls this "THE DEDUP INVARIANT" in its own docstring and
implements `collapse_by_ticker()`. `rec_events_readout.py:135-159`'s `collapse_by_rec_ticker()`
does the same, and its DDL comment in `db.py:930-933` explicitly names this as "same shape as
gate_suppressions/exit_signals" — i.e. this project has already recognized and named the bug
class twice. It simply wasn't applied to the Offense facet.

**Why this matters for the original brainstorm, and what it will actually change on screen.**
This is a serious, independent candidate explanation for A2's own finding
(`docs/plans/investor-maturity-roadmap.md` §4 A2 — "composite score does not rank forward alpha
within 65-79, reads as candidate generator not ranker"). A2's 239-row population was never
collapsed by ticker either, and a persistence-weighted missed pool could distort a band-average
comparison independent of whether the composite genuinely fails to rank. **Be explicit that
this is not a background bookkeeping fix: the Engine Track Record card's live "+14.4pp"
headline (`acted_alpha − missed_alpha`) will visibly move once `missed_alpha` is recomputed
correctly** — direction is data-dependent (if the persistently-missed names were losers, the
gap shrinks; if winners, it widens), so don't predict the sign in advance, but do expect the
owner-facing number to change, not just a maturity date to shift. **Recommend re-running A2's
own analysis with the collapse (and the acted-via-any-day exclusion) applied before treating
its "not monotonic" verdict as final** — the script already exists
(`scripts/offense_attribution.py`).

### A2. [CODE-VERIFIED, revised] Gate Suppression Ledger has no per-episode collapse — but a distinct-ticker floor already guards the worst outcome

`gate_ledger_readout.py`'s `enrich_and_grade()`/`grade_by_gate()` iterate every `gate_suppressions`
row directly with no collapse function anywhere in the module (confirmed by direct grep — zero
hits for "collapse"/"dedup" in this file). Same row shape as A1: a name suppressed by the same
gate for a multi-day streak contributes one row per day.

**Correction after re-checking `grade_by_gate` directly (220-296):** the module already carries
a `min_tickers` **distinct-ticker floor** (§5's "K") alongside `min_calls` — a gate cannot leave
"building" on `min_calls` matured rows alone; it separately needs `min_tickers` DIFFERENT
tickers evaluable (276). This is exactly the guard against "the same handful of tickers
suppressed on consecutive days" that the first draft of this document claimed was missing. It
isn't — **drop the "matures too early" framing.** What persistence still does distort is
`mean_alpha_pct` itself (a persisted streak still gets averaged in once per day, not once per
episode) — a correctness fix worth making, but not an urgency fix, since per CLAUDE.md every
gate currently reads "building" anyway, so no number a reader currently trusts is wrong today.

### A3. [CODE-VERIFIED, revised] The Judge's witness track record is a MIXED shape — part collapse problem, part pure pseudo-replication

`judgment_grading.py` has two distinct grading paths, confirmed by direct read: `grade_ticker_opinion`
(101-147) grades a real per-ticker dimension and stamps a real `ticker` (147); `grade_portfolio_opinion`
(157-210) grades a portfolio-wide dimension and hardcodes `"ticker": "_PORTFOLIO"` (203) — there
is no ticker to collapse by for that half. `track_record_summary()` (213-239) rolls both up by
`(source, dimension)` with zero collapse either way.

So this is not one bug, it's two, and they belong on two different tracks:
- **The per-ticker dimensions** (e.g. momentum) have the same episode-duplication shape as A1 —
  a witness opining daily on the same held ticker contributes one row per day for one real
  standing opinion. This belongs with A1's fix.
- **The portfolio-wide dimensions** (position_health, concentration, structural_risk — all keyed
  `_PORTFOLIO`, no ticker at all) have no episode to collapse; their problem is pure
  overlapping-forward-window pseudo-replication — the same class `model_predictions` already
  identified and partially mitigated via stride-sampling
  (`docs/plans/predictive-modeling-shadow-layer.md:134`, an explicit "effective_n" discount).
  Daily opinions on the same portfolio-wide dimension at a 10-20 day horizon share almost all of
  their input window, so consecutive rows are not independent trials. Unlike `model_predictions`,
  nothing here discloses or discounts this. This half belongs with D3/Phase 5, not a
  collapse-by-ticker fix — a collapse function has nothing to key on here.

### A4. [CODE-VERIFIED] Recommendation-to-trade matching can double-credit one real trade

`recommendations_history.py::match_recs_to_trades()` (185-255) matches purely on
`(ticker, UTC-calendar-date)`. The trades side is deduped (first trade per ticker/day wins,
219-231) but the **recs side is not** (234-254): if two different recommendation rows exist for
the same ticker on the same day (e.g. a `new_pick` and a same-day `enter_now` for the same
name — plausible, since these are captured by different code paths), **both** match to, and
both get credited with, the same single trade. One real action inflates whichever
take-rate/hit-rate metric aggregates `acted_on` across rec_types.

### A5. [CODE-VERIFIED, precisely scoped] `debrief_advisor.py` uses a raw string-slice date filter instead of this project's own hardened parsing idiom

`debrief_advisor.py:211-216, 244-249` filters `trades_df`/`recs_df` by
`trades_df["traded_at"].astype(str).str[:10] >= start_date`, instead of the
`pd.to_datetime(..., utc=True, format="ISO8601")` idiom every other consumer of `traded_at`
uses (15+ modules, per direct verification — `tax_advisor.py`, `capital_vs_margin.py`,
`behavioral_fingerprint.py`, `investor_mirror.py`, etc.). **Precise scope, verified directly
against `trade_time.py`:** this bites only **real (non-imported), extended-hours fills that
land close to the ET midnight boundary** — an imported trade's `traded_at` is re-anchored to a
wall-clock ET-offset string by `normalize_traded_at()` before this ever runs, so the embedded
date IS the correct ET date for those rows; the disagreement only exists for a genuine live fill
stored with a UTC offset that crosses the ET-midnight boundary (roughly a fill in the last few
hours before midnight ET, i.e. after-hours trading). Narrow, but real, and it feeds the weekly
State-of-Portfolio Thesis narrative's "did you trade this week" / "which positions closed this
week" framing (F-232) — a wrong week-bucket assignment would misattribute a trade to the wrong
week's behavioral narrative. Not previously flagged anywhere in CLAUDE.md or memory.

### A6. [CODE-VERIFIED] `COMPOSITE_WEIGHTS` changed exactly once (2026-07-09), and no stored row says which regime produced it

Walked the full history myself rather than trust a grep hit-count: `git log -p` on
`constants.py` shows four other diff hunks near `COMPOSITE_WEIGHTS` in the file, but each is an
**unrelated constant added immediately after the dict closes** (`EARNINGS_CRITICAL_DAYS`,
`CATALYST_WATCH_WINDOW_DAYS`, macro-playbook thresholds) — git's diff-context heuristic just
labels the hunk with the nearest preceding block. **Only commit `6dc1197` (2026-07-09) actually
changed the weight values** — the 3-pillar `{technical .45, fundamental .40, sentiment .15}`
became the 4-pillar `{technical .25, business_quality .35, valuation .30, sentiment .10}`
(Valuation split out of Fundamentals). This is a genuine methodology change, not a re-tuning.

Contrast with the project's own good pattern: `SIZING_FORMULA_VERSION` is stored per-row via
`recommendations.rec_sizing_version` specifically "so a future formula change can never be
silently compared against sizes produced under the old one" (`daily_briefing.py:113`,
`db.py:226`). **No equivalent field exists for the scoring formula.** Every `recommendations`/
`exit_signals` row's `composite_score` — across the entire history, including everything A2's
band analysis (65-69/70-74/75-79) was computed over — silently mixes two different scoring
methodologies with no way to tell them apart from the stored data alone. `score_history` (F-269,
started 2026-09-14) postdates the boundary and is internally consistent, but every other
consumer of `composite_score` spans it silently.

**Because there was only ONE boundary, at one known date, this is retroactively fixable by
inference** — see §4 below.

### A7. [RESOLVED — SHIPPED 2026-09-23 via Phase 2, commits `eea121d`/`0f583fe`] Silent-truncation risk on unbounded reads — already confirmed to have bitten this project once

This project's own `db.py` comment (3330-3332) states it plainly: PostgREST's default 1000-row
page cap has already silently truncated a real read once, confirmed live — `model_predictions`
undercounted 1524 real rows as ~1000 (2026-09-04), fixed there via real cursor pagination
(`db.py:3762-3776`). **That fix was not extended to its siblings.** The following loaders still
have no `.limit()`/pagination at all, or a hard ceiling that is a stopgap, not a scalable fix:

- `load_gate_suppressions` (3001-3017) — no date filter, no limit at all. Highest risk.
- `load_recommendations`/`_or_none` (3020-3075) — no limit; only bounded if the caller supplies
  a date range, and not every caller does.
- `load_rec_events` (4652-4664) — no date filter, no limit.
- `load_exit_signals`/`_or_none` (3169-3225), `load_analyst_target_snapshots` (3262-3282),
  `load_judgment_opinions`/`load_judgment_grades` (3410-3432, 3482-3504) — date-filtered
  (365-day default) but no `.limit()`; plausibly exceeds 1000 rows within a year on a
  ~20-holding book with multiple signal types per ticker per day.
- `load_daily_snapshots`/`_or_none` (1523-1569) — both date bounds default to `None` (full table
  scan), no `.limit()` at all.
- `load_analyst_coverage`/`_or_none` (2197-2274) — has a `limit` param, but its **default is
  100**, a silent cap for any caller that doesn't override it.
- `score_history`'s reader passes an explicit `limit=5000` (3325) — better than nothing, but the
  code's own comment frames it as a stopgap, not a scalable design.

Every table this touches is exactly the ticker-cardinality-times-trading-days shape that scales
fastest: `gate_suppressions`, `recommendations`, `rec_events`, `exit_signals`, `score_history`,
`judgment_opinions`/`grades`, `analyst_target_snapshots`, `daily_snapshots`. **A silently
truncated read is strictly worse than every other failure mode this codebase has hardened
against** — it returns a real subset with no error and no offline-sentinel trip, which is
exactly the shape of "weak or incomplete signal with no visible cause" the original brainstorm
described. **This needs a live row count, not more code reading, to know whether it has already
happened on any of these tables** — but one instance of it is not merely "highest priority to
rule in or out," it's a **prerequisite for trusting A1's own fix**: `load_recommendations` has
no `.limit()` unless the caller supplies a date range, and the all-time re-run A1 recommends
(`scripts/offense_attribution.py` against the full history) is exactly the shape that already
truncated silently once elsewhere (`model_predictions`, confirmed live). At roughly 10 picks/day
since mid-2026, `recommendations` plausibly already exceeds the 1000-row page cap. **Run
`SELECT COUNT(*) FROM recommendations` (and the same for `exit_signals`) before re-deriving
A1's collapsed headline** — if either is already truncated, the collapsed re-run would still be
computed over an incomplete population and its answer would still be wrong, just differently.

**Confirmed live and closed.** `recommendations` was indeed already over the cap (1473 rows),
`analyst_coverage` turned out to be a SECOND, worse instance (678 rows against a 100-row
default — 85% dropped) discovered while scoping the fix. All 8 loaders now paginate for real;
3 of them needed a non-unique `ORDER BY` tie-breaker fix caught by a mandatory Opus review
before shipping (see the Phase 2 status note at the top of this document). Nothing in this
section remains open.

### A8. [RESOLVED 2026-09-23 — the original finding didn't hold once the full consumer chain was traced]

Original claim: CSV `MINT` (confirmed real margin interest) gets its own `margin_interest`
subtype, while this account's live SnapTrade connector reports the same real-world charge under
the generic `type="FEE"` — so `income_event_subtype()` returns two different labels for the same
real event depending on ingestion path, and "any interest-cost trend built by grouping on
subtype silently splits one real cost line into two."

**A `planner` design pass, dispatched to fix this, traced every real consumer instead of
building the fix and found the premise doesn't survive contact with the actual pipeline:**
1. **No consumer groups by `subtype`.** `income_event_subtype()` has exactly one caller
   (`_income_dedup_bucket_key`, used only for dedup bucketing) — grepped the whole repo. Every
   real display (the Cash Activity chart, `capital_vs_margin.interest_partition()`, the Summary
   "of which interest" caption) groups on the coarser `event_type` column instead. The
   interest-cost chart the original finding assumed exists does not.
2. **The confirmed margin-interest charges are already counted correctly today.** Both read
   consumers call `dedupe_income_events()` before summing; it matches a live FEE row against its
   CSV MINT twin via the existing `_DEDUP_RAW_CODE_BUCKET_OVERRIDE` (exact amount+date match),
   and the CSV row — which already carries `event_type="interest"` — wins the tie-break. The
   live FEE twin is dropped, so it neither inflates the fee total nor goes missing from
   interest. Traced by hand against all 3 originally-confirmed events (06-26, 07-27, 08-25);
   each nets to interest exactly once.

**Directly confirmed with the owner why a blanket relabel would have been wrong, not just
unnecessary:** `FEE` is a genuinely mixed real-world bucket on this account — margin interest is
one thing that lands there, but so is the Robinhood Gold membership fee and other unrelated
recurring charges. The only evidence-based way to tell them apart is the exact cross-path
amount+date match dedup already performs; there is no safe heuristic (a fixed-recurring-amount
pattern, etc.) the redline permits building without further confirmed evidence. **Real, open
policy fact, not a software gap:** margin interest is only ever counted correctly for periods
the owner imports a CSV statement for — a live-sync-only period with no CSV import has no
evidence either way and correctly stays labeled generic "fee." This is the owner's existing
practice already, not a new requirement.

**Closed by locking in the current-correct behavior with tests, not by changing
`broker_sync.py`** (touching that file would trip its mandatory-review gate for a change with
zero display effect). 5 new tests added to `tests/test_broker_sync.py`: a confirmed CSV/live
pair nets to interest exactly once; a Gold-membership-shaped FEE row with no CSV twin is never
touched; the result doesn't depend on load order or on the CSV twin merely being absent from a
given batch; the write-side suppression still routes a cross-matched live FEE to `ignored`
before persistence. Tests-only — no source file changed, no Opus review needed.

---

## 3. Section B — Genuine historical limitations (cannot be reconstructed, don't try)

State these plainly rather than treat them as bugs to chase — the honest, disclosed limitation
this project's own docs standard requires:

- **`account_daily_snapshots`** (leverage/margin-cushion history, F-266) — forward-only from
  2026-09-10 by explicit design (`db.py:416-418`: "the daily cash/margin figures were never
  recorded before now"). No pre-existing daily cash/margin state exists anywhere to backfill from.
- **`score_history`** (F-269) — forward-only from 2026-09-14. Historical composite scores exist
  in `recommendations`/`exit_signals`, but only as one-off point captures at surface/signal time,
  not a daily time series for held tickers — there is no daily series to backfill.
- **`portfolio_risk_snapshots`** (Recommendation Outcomes Measurement Phase 1a) — forward-only
  from ship date, explicit in `db.py:901-902`.
- **`rec_events`** (Phase 1b/2) — forward-only from DDL-apply 2026-09-17.
- **`holdings` point-in-time history** — `holdings` is a current-state-only table
  (`db.py:49-62`); the only historical reconstruction path is replaying `trades` via
  `recalculate_from_trades` (`db.py:2451`). This works *only as far back as the `trades` journal
  itself goes* — any position held before the owner started logging trades in this app has no
  reconstructable history.
- **`account_cash`/`scanner_cache`/`snaptrade_config`/`broker_position_snapshot`/`alert_state`**
  — all single-row-overwrite by design; no day-by-day history exists for any of them before
  whatever forward-only capture superseded them (`account_daily_snapshots` for cash, going
  forward only).
- **Pre-2026-08-06 self/engine attribution** — `self_track_record.py`'s own constants
  (`SELF_TRACK_RELIABLE_LOG_START = 2026-08-06`, `SELF_TRACK_SELL_RELIABLE_LOG_START =
  2026-07-21`) already disclose that classification before these dates is "coverage-limited," a
  deliberate design choice to disclose rather than backfill an unreliable period. Keep this
  posture — it's the right call, already made.

**Framing for the roadmap:** don't spend effort trying to reconstruct any of the above. The
right response to a genuine historical gap is to disclose it (this project already does this
well — see the coverage-limited pattern) and let the forward-only clock run.

---

## 4. Section C — Data that CAN be repaired/enriched from what already exists (no new capture needed)

Distinct from Section B: these look like historical gaps but are actually fixable by
computation over data the app already has.

### C1. `COMPOSITE_WEIGHTS` regime — backfillable by date, not by a stored field

Because the weight change happened exactly once, on a known date (2026-07-09), any analysis
reading historical `composite_score`/pillar values can be segmented into "pre-4-pillar" vs
"post-4-pillar" purely by `rec_date`/`signal_date`, with zero new data collection. This is
enrichment via a computed epoch boundary, not a data-recovery problem. **Split across two
phases, not bundled as one item (revised — the first draft incorrectly deferred both halves to
Phase 4):**
  - **Cheap, no schema change, belongs in Phase 1 as a prerequisite:** A1's recommended
    re-run of `investor-maturity-roadmap`'s own A2 offense-attribution analysis (composite
    band vs. forward alpha) reads historical `composite_score` across the full history — if
    that history straddles the 2026-07-09 boundary without segmenting by it, the re-run is
    confounded by mixing two scoring methodologies in one band axis, independent of whatever
    the episode-collapse fix does. Segment by date before trusting the re-derived verdict.
  - **More durable, genuinely Phase 4 (schema + policy):** add a `weights_version` column to
    `recommendations`, backfilled by date for every existing row, so future readers get the
    boundary for free instead of re-deriving it each time. This is a `constants.py`-adjacent,
    DB-write change — see §6 for the review-gate implication.

  **[RESOLVED — SHIPPED 2026-09-23, commit `6448265`, see §7 Phase 4.]** Both halves done: the
  cheap segment-by-date discipline shipped as part of Phase 1's own review scope, and the schema
  change shipped as Phase 4 — scoped to `recommendations` only, per a `planner` blast-radius
  census that found `exit_signals` never actually needed it (100% single-regime already). DDL
  applied and verified live: 395 rows at version 1, 1118 at version 2, zero `NULL`.

### C2. [RESOLVED — confirmed clean, nothing to run] Duplicate-row cleanups already identified

Two known, already-diagnosed duplicate-row issues had documented one-time SQL fixes that may
or may not have been run:
  - `snaptrade_income_events` — 12 duplicate rows (3 MINT margin-interest, 9 CDIV/MDIV dividend)
    found 2026-09-11, with a documented manual cleanup query (`db.py:883-887`).
  - `account_flows` — pre-fix duplicates from before the `snaptrade_txn_id` unique index was
    added (`db.py:818-841`); "net_contributed_capital stays inflated until they are manually
    reviewed/deleted" (`db.py:830-832`).

**Checked live via `COUNT(*)`/`GROUP BY` on both tables — both already clean.** No duplicates
found on either table; nothing needed to run.

### C3. [SHIPPED 2026-09-23, commit `06cc05b`] Sector-taxonomy reconciliation gap — swept and closed

`reference_data.validate_payload`'s presence-only check for `discovery_universe` tickers
against `TICKER_SECTORS` is explicitly scoped to *newly changed* tickers only
(`reference_data.py:229-239`), grandfathering an unknown-but-real subset of pre-existing
tickers that may still disagree with `TICKER_SECTORS`'s own classification.

**Swept via a live query** (`SELECT payload FROM reference_tables WHERE name =
'discovery_universe'`) cross-referenced against the in-code `TICKER_SECTORS` dict: **29 tickers
had zero entry**, not the "unknown-but-real subset" this section originally hedged on —
`GOOG` (Mega-cap Tech); `ALB/CCJ/KMI/MPC/NEM/PSX/SLB/VLO/WMB` (Energy & Materials); `CVS/GILD/
HCA/HIMS/SYK/VRTX` (Healthcare & Biotech); `DE/EMR/ETN/FDX/HON/ITW/MMM/PH/PWR/UNP/UPS`
(Industrials & Defense); `CEG/VST` (Clean Energy & Utilities). Closed the same way the
Financials/Semiconductors/Software & Cloud/Internet & Media/Consumer & Retail/Materials-
Utilities-Real-Estate fixes already closed 6 prior instances of this exact class: added each
ticker to `TICKER_SECTORS` under the closest-matching existing peer's sector. Two genuinely
ambiguous classifications (`CCJ` — Materials not Energy, matching FCX/NEM's extraction-business
precedent over the oil & gas peers; `HIMS` — Healthcare not Consumer Tech, classified by what it
delivers rather than its subscription UX) confirmed directly with the owner before building,
not decided solo. Opus reviewer SHIP, 0 blocking — independently verified all 6 sector labels
are recognized by `_SECTOR_IMPACT`/`SECTOR_ETF`/`_SECTOR_PROFILES` (not just `RATE_SENSITIVITY`,
which has a separate, pre-existing, already-documented gap for Materials/Industrials — unrelated
to this fix, degrades honestly to "Unknown" rather than a fabricated value). `TICKER_SECTORS`
now 217 entries. Full suite 6151 passed. **Phase 3 is now fully closed — A4/A5/A8/C2/C3 all
shipped or resolved.**

---

## 5. Section D — Non-data-quality explanations for weak trend signal (per the explicit ask: don't assume every issue is a cleanup problem)

Four distinct, real explanations found or confirmed this session, none of which "more/cleaner
data" fixes:

### D1. Genuine youth — several features are simply too new, and that's fine

`score_history` (8 days old at last check), `rec_events` (5 days), the Gate Suppression Ledger
(~1 month, CLAUDE.md's own "expect building for ~2 months" is correct and already tracked). No
action needed beyond letting the clock run — this project already has good discipline here
(explicit `MIN_CALLS`/`FIRM_CALLS` floors, "building" bands rather than a premature verdict).
**Caveat, stated precisely after revision: this is not only a maturity-clock effect.** For the
Gate Suppression Ledger (A2), whose distinct-ticker floor already guards premature maturity,
collapsing mainly makes `mean_alpha_pct` more honest without moving a number anyone currently
trusts. But for the Engine Track Record (A1), the fix will visibly move the **live,
currently-displayed** "+14.4pp" headline the owner already reads as evidence — expect a real,
immediate change to that number once `missed_alpha` is recomputed correctly, not a quiet
slowdown in how fast a "building" badge clears.

### D2. Scale mismatch between a feature's assumed horizon and the real behavior it measures — a documented precedent, worth checking elsewhere

The single clearest instance already exists in this project: A4
(`docs/plans/investor-maturity-roadmap.md` §4) measured a real ~7-calendar-day median holding
period against `exit_advisor`'s weeks-scale deterioration-ladder confirmation window — the
ladder is asking a question on a timescale this account mostly doesn't hold into. That is a
genuine design-assumption mismatch, not a data defect, and this project correctly identified it
as such rather than chasing more data.

**Recommend applying the same check to every other feature with a stated horizon constant**
before assuming any of them just need more time: `JUDGMENT_HORIZON_MOMENTUM_DAYS=5` /
`_QUALITY_DAYS=20` / `_POSITION_HEALTH_DAYS=10` (The Judge), `GATE_LEDGER_HORIZON_TRADING_DAYS
=30`, `REC_OUTCOME_HORIZON_TRADING_DAYS=30`. None of these were found contradicted by a real
measured behavior duration this session — but none were found EXPLICITLY checked against one
either. This is a cheap, high-value sanity pass: compare each stated horizon against the real
median holding period (already measured at ~7 days) before trusting any of these track records'
eventual verdict.

### D3. Pseudo-replication — overlapping observation windows inflate apparent sample size

Distinct from A1-A3's duplicate-*row* problem (same episode written many times) is a duplicate-
*information* problem: even with a clean row-per-day, consecutive daily forecasts/opinions on
the same ticker share most of their input window, so they are not independent trials. This
project already identified and partially solved this once — `model_predictions`' stride-sampling
discount (`docs/plans/predictive-modeling-shadow-layer.md:134`). The Judge's portfolio-wide
dimensions (A3 above — the `"_PORTFOLIO"`-keyed grades with no ticker to collapse by at all)
have the identical shape and none of the mitigation. **Recommend treating "effective N, not raw
N" as a named, reusable convention** — see §6 controls.

### D4. Regime/idiosyncratic concentration — sometimes the data is fine and the "weak trend" is a real, narrow phenomenon

This project has already correctly distinguished this once: A1's regime-confound check
(`project_investor_maturity_roadmap` memory) found the Defense facet's negative headline
alpha was concentrated in a 5-ticker AI/growth/cyber cluster, not a broad miscalibration — and
resisted the temptation to retune a threshold on a concentrated finding. **Keep this discipline.**
Before treating any newly-collapsed, newly-versioned, newly-paginated metric's remaining
weakness as a data problem, run the same cross-tab-by-sector/cluster check the roadmap's own
A1 regime-confound analysis already pioneered — the tooling (`scripts/exit_ladder_replay.py`,
`scripts/exit_early_cost_analysis.py`) already exists and generalizes.

---

## 6. Section E — Controls to adopt going forward

Four controls, each extending a pattern this project has already proven rather than inventing a
new one — matching the explicit ask to improve quality "without unnecessarily redesigning
functionality that already exists":

1. **[FOLLOWED — Phase 1] Reuse the proven earliest-anchor collapse PATTERN — not a mandatory
   shared module (revised).** The first draft of this control oversold how identical the three cases are.
   They aren't: `collapse_by_ticker` keys on ticker alone and carries severity-escalation logic
   specific to protective calls; `collapse_by_rec_ticker` keys on `(rec_type, ticker)` with no
   escalation; The Judge's per-ticker dimensions would need a third key shape, and its
   portfolio-wide dimensions have no ticker to key on at all (A3). A single parameterized helper
   trying to serve all of them would itself be exactly the premature generality CLAUDE.md warns
   against ("don't design for hypothetical future requirements"). `rec_events_readout` already
   showed the right way to reuse this: it extended `protective_track_record`'s PATTERN
   (earliest-priced-anchor, worst-severity-label) as its own small, independently-testable
   function, not a shared abstraction. A1's and A3's per-ticker fixes should do the same — two
   or three small functions, not one shared module.

2. **[DONE — Phase 2] Extend real pagination (the `model_predictions` pattern) to every
   unbounded loader named in A7**, rather than patch each with its own ad-hoc `.limit()`
   ceiling. Shipped across two commits (`eea121d`, `0f583fe`), all 8 loaders, plus a
   non-unique-`ORDER BY` correctness fix a mandatory review caught before ship.

3. **[DONE — Phase 4] Version-stamp any constant that produces a value persisted long-term.**
   `SIZING_FORMULA_VERSION` was the good model; `COMPOSITE_WEIGHTS` was the gap. Shipped as
   `COMPOSITE_WEIGHTS_VERSION` + `recommendations.weights_version`, plus the recommended
   Definition-of-Done line added as CLAUDE.md's new item **#8** (the DoD is now 8 steps, not 7)
   — *"if a changed constant feeds a value written to a history/track-record table, does a
   reader need to know which regime produced an old row? If yes, version it."*

4. **Adopt "effective N" as a named discipline for any daily-repeating opinion/forecast table**,
   generalizing `model_predictions`' stride-discount to `judgment_grades` and any future
   feature with the same shape — a companion to the episode-collapse convention in (1), for the
   case where rows are genuinely distinct calendar days but their information overlaps.

**Deliberately NOT recommended:** a new antipattern-gate rule for the string-slice date bug
(A5). It's real, but it's one file with a narrow, already-scoped blast radius — mechanically
gating it project-wide risks the same "exemption-rationale drift" this project's own
`feedback_exemption_rationale_drift` memory already warns against for over-broad gates. A
targeted fix in `debrief_advisor.py` is enough; a new CI rule is not proportionate to one file.

---

## 7. Section F — Prioritized roadmap

Phased so each phase is independently shippable and separately reviewable — not one large
change. Model/review tiering follows CLAUDE.md's own economy rules: design of anything
touching a decision-adjacent track record or `constants.py` routes through `planner`
(Opus) before code, mechanical/bounded work goes to `implementer`, and anything landing in
`db.py` or a scoring-adjacent module requires the mandatory Opus `reviewer` pass per Hard
Rule #4 — flagged per phase below so a future execution session doesn't skip the gate.

**Phase 0 is NOT a gate in front of Phase 1 (revised — the first draft implied it was, and
that's backwards for the item that most needs to ship soon).** The collapse fix is correct
regardless of current row counts; nothing about A1/A2/A3 needs an owner-run query to be worth
building. Phase 0's live-query items genuinely gate only **Phase 2** (pagination — you need a
row count to know which loaders are actually at risk) and **Phase 3's C2 item** (the duplicate-
cleanup status). One live-query item is a real prerequisite specifically for A1's own
correctness, though, and is broken out below rather than bundled into general Phase 0: the
`recommendations`/`exit_signals` row counts, because the all-time re-run A1 recommends could
itself already be silently truncated (A7's own finding) — a collapsed-but-truncated re-run
would still hand the owner a wrong number, just wrong in a different way.

**Phase 0 — [CLOSED] background live-data verification.** Row counts for the tables named in A7
(has silent truncation already happened anywhere besides `model_predictions`?); whether the two
C2 duplicate-cleanup queries were run; current maturity (row/distinct-ticker counts) for
`exit_signals`, `gate_suppressions`, `rec_events`, `judgment_grades` against their own
`MIN_CALLS`/`FIRM_CALLS` floors. All run by the owner the same day; results folded into the
status header at the top of this document and into §9 below.

**Phase 1 — the episode-collapse fix, proceeds now, does not wait on Phase 0.** Scope, revised
per A1-A3's corrected mechanisms: (a) fix `recommendations_history.py`'s `missed_alpha`/
`action_rate` distortion (A1) — collapse by ticker AND exclude any row for a ticker that was
acted on via a different day's surfacing, closing the second defect A1 identified; (b) fix
`gate_ledger_readout.py`'s `mean_alpha_pct` persistence-weighting (A2) — lower urgency than (a)
since the distinct-ticker floor already prevents a premature "mature" verdict, but the same
collapse pattern applies; (c) fix ONLY the per-ticker half of `judgment_grading.py` (A3) — the
portfolio-wide `_PORTFOLIO`-keyed dimensions have no ticker to collapse by and belong in Phase 5
instead. **One specific prerequisite, not a general Phase-0 gate:** run
`SELECT COUNT(*) FROM recommendations` and the same for `exit_signals` before trusting a
re-derived Engine Track Record headline — if either is silently truncated (a live risk per A7),
fix that table's pagination first (pull the relevant slice of Phase 2 forward), or the
"corrected" number will still be wrong. Also apply C1's cheap half here: segment any re-run of
the investor-maturity-roadmap's A2 offense-attribution script by the 2026-07-09
`COMPOSITE_WEIGHTS` boundary before drawing a conclusion from it. **Review gate:** these modules
feed live-rendered track-record headlines the owner reads as evidence — route through `planner`
for each collapse function's design, `implementer` for the mechanical application, and the Opus
`reviewer` voluntarily even though none of these three files is literally named in `_GATE_FILES`
today, on the same "spend where a wrong call moves judgment" logic CLAUDE.md already applies to
F-272/F-255. **Tell the owner explicitly, before this ships, that the live Engine Track Record
"+14.4pp" figure will change** (direction unknown until re-derived) — this is a correction to
a number already on screen, not an invisible internal fix.

**Phase 2 — pagination hardening (A7), mechanical, `db.py`-scoped.**
Extend `model_predictions`' real `.range()` pagination pattern to the 8 other unbounded/
under-bounded loaders named in A7. Pure mechanical extension of an already-reviewed pattern.
**Review gate:** `db.py` is explicitly named in CLAUDE.md's `_GATE_FILES` DB-write list — the
commit hook will require an Opus reviewer citation regardless; budget for it.

**Phase 3 — FULLY CLOSED (A4/A5/A8/C2/C3 all shipped or resolved).** A4 shipped inside Phase 1 (Chunk
2, `_dedup_acted_credit()` in `match_recs_to_trades()`). **A5 SHIPPED 2026-09-23 as commit
`8dc11fa`** — `debrief_advisor.py`'s raw string-slice date filter replaced with the hardened
`pd.to_datetime(..., utc=True, format="ISO8601")` idiom, via a new `_traded_at_et_dates()`
helper. Turned out bigger than scoped: a full-file grep surfaced **4 real instances of the bug,
not the 2** originally cited (the recs-surfaced acted-tickers filter and the protective-signals
sold flag were the same bug, just not in the doc's original line citations). A 5th lookalike
site (the recs week-filter's `rec_date`/`surfaced_at` fallback) was deliberately left untouched
and documented in-code — `rec_date` is a plain date column in the common path, and applying the
same idiom there would introduce a NEW off-by-one-day bug to fix a rarer, production-unreachable
fallback. Opus reviewer independently verified this reasoning (hand-computed the off-by-one) and
confirmed all 5 new regression tests fail against the pre-fix code for the predicted reason, not
coincidentally: SHIP, 0 blocking. Full suite 6145 passed. **A8 RESOLVED 2026-09-23** — turned
out to be a non-issue once the full consumer chain was traced (no display groups by the field
in question; the confirmed margin-interest charges are already counted correctly via existing
dedup); closed with 4 invariant-locking tests, no source change, no review needed (see §2 A8
for the full trace). C2 (CLOSED, both tables confirmed clean via Phase 0's live query). **C3
SHIPPED 2026-09-23 (commit `06cc05b`)** — closed a 29-ticker `TICKER_SECTORS` gap in
`portfolio.py`. Nothing remains in this phase.

**Phase 4 — [SHIPPED 2026-09-23, commit `6448265`] `COMPOSITE_WEIGHTS` durable versioning.**
A `planner` (Opus) design pass re-verified the single-boundary premise independently (walked
history back to the first-ever scoring commit, 2026-05-05 — confirmed exactly two regimes ever
existed, no hidden third) and ran a blast-radius census: only `recommendations` (27%
pre-boundary) is actually ambiguous — `exit_signals`/`gate_suppressions`/`analyst_coverage`/
`score_history` all began capturing `composite_score` after the 2026-07-09 boundary and are
already 100% single-regime. Owner decided both open questions: proceed with the schema change
(the backfill is uniquely lossless — the boundary date is exact and deterministic, unlike
almost every other historical gap this initiative found), scoped to `recommendations` only (not
`exit_signals`, despite it storing the same field — zero current ambiguity there, touching it
now would be premature future-proofing).

Shipped: `COMPOSITE_WEIGHTS_VERSION = 2` constant next to the dict it versions;
`recommendations.weights_version` stamped unconditionally at the single shared write boundary
inside `save_recommendations` (never read from a caller's dict, so none of the 4 real call
sites needed a change and a stale caller value can never override the current constant); the
usual inert-until-DDL compat pattern; owner-run DDL + backfill SQL documented in both `db.py`'s
own docstring and `docs/architecture.md`. **The real recurrence-prevention is a new, permanent
Definition-of-Done item (#8)** — a stored column doesn't stop an analyst from forgetting to
check it, any more than forgetting to check `rec_date` did; the DoD rule is what actually closes
the gap for the NEXT formula change. Opus reviewer SHIP, 0 blocking, independently traced the
write-boundary invariant in the actual code (not the tests) and the read path's pre-DDL safety;
agreed with the implementer's own flagged scope call (no shared `regime_for_date()` helper yet —
correctly deferred until a real reader-side consumer exists, not built speculatively ahead of
one). Full suite 6156 passed.

**Phase 5 — the "effective N" discipline (D3/control #4), scoped to The Judge's
`_PORTFOLIO`-keyed dimensions only** (position_health, concentration, structural_risk — A3's
portfolio-wide half, which has no ticker to collapse by and so was never fixable in Phase 1).
Lowest urgency of the five; The Judge is young enough that this can wait until its raw `n`
first approaches its own floor, at which point overstated confidence would first become
consequential.

---

## 8. What this document deliberately does not recommend

- **No blanket "add more collapse/pagination everywhere" sweep.** Each fix above is scoped to a
  table/module with a confirmed, specific instance of the bug shape — not a speculative
  hardening pass across code that hasn't shown the symptom. This matches the project's own
  stated precedent against "blind bulk-patching an untested render file"
  (`feedback_recurring_defect_gate`).
- **No recommendation to retune any threshold, gate, or scoring weight.** Every finding here is
  about how existing data is aggregated or retained, never about what the thresholds themselves
  should be. That stays a separate, later, evidence-gated conversation — consistent with this
  project's repeated posture (A1's regime-confound, the 2026-09-20 dollar-cost run) of refusing
  to retune from a single pass of new evidence.
- **No new dashboard/UI surface.** This is a data-integrity assessment, not a new feature. Any
  of the above could ship as a quiet correctness fix with no new user-facing surface at all.

---

## 9. Open items needing a live Supabase query — status as of 2026-09-23

1. **[RESOLVED]** Row counts for all named tables — run live. Only `recommendations` and
   (discovered while scoping Phase 2) `analyst_coverage` were actually over their respective
   caps; both fixed. See the status header at the top of this document for the exact counts.
2. **[RESOLVED]** Both C2 duplicate-row cleanups checked — both tables confirmed already clean,
   nothing needed to run.
3. **[PARTIALLY RESOLVED]** Raw counts were obtained for all four tables (see the status
   header). The "both raw and collapsed" comparison was only done in full for The Judge
   (confirmed via direct SQL match against the live screenshot — see memory
   `project_data_foundation_strategy`'s Phase 1 visual-confirmation entry) — `gate_suppressions`
   was checked only as a negative control (still all "building," unchanged, as predicted; no
   gate has crossed its floor yet so there was nothing to compare pre/post collapse). A full
   pre/post comparison for `exit_signals`/`rec_events` was never explicitly run — low priority,
   since neither table's headline is currently displayed anywhere the way Engine Track Record's
   is, but worth knowing this specific comparison was never completed if it's ever needed.
4. **[STILL OPEN — never checked this session, no urgency tied to it]** A live re-run of
   `correlation_coverage()`'s `n_obs` (`portfolio.py`) — last measured sound at 125 observations
   on 2026-08-21, over a month old as of this update. That was always framed as "a point-in-time
   fact, not a standing guarantee," and nothing in Phases 0-4 touched correlation computation —
   this is a genuinely separate, still-unanswered question, not resolved by anything shipped
   here. Worth a periodic re-check, not urgent.
5. **[RESOLVED, and then some]** What fraction of `recommendations`/`exit_signals` rows predate
   2026-07-09? Answered as part of Phase 4's own `planner` design pass, which went further than
   this item asked — it walked the full history back to the very first scoring commit
   (2026-05-05) to confirm there was never a hidden third regime, not just the one boundary this
   item named.
