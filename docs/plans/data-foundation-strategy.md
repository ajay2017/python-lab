**Status, updated same day: Phase 0 CLOSED, Phase 1 SHIPPED, the `recommendations`-table slice
of Phase 2 SHIPPED ahead of schedule.** Owner ran all four Phase 0 queries live (§9) — no
truncation found anywhere except `recommendations` itself (1473 rows, confirmed over the
1000-row cap), both known duplicate-row cleanups (`snaptrade_income_events`, `account_flows`)
confirmed already clean, and the persistence multipliers measured live: `recommendations`
new_pick 293 rows/78 tickers (3.8x), `gate_suppressions` 264/80 (3.3x), `judgment_grades`
non-portfolio 230/48 (4.8x) — with the `judgment_grades` count exactly matching that table's
total, confirming zero `_PORTFOLIO`-keyed rows exist yet (Phase 5 has nothing to act on today).
`recommendations`/`exit_signals` pre-2026-07-09 fractions: 395/1473 (27%) and 0/98 (0%) — sizes
Phase 4's real urgency to `recommendations` only.

**Phase 1 shipped as commit `6c60656`** (`recommendations_history.py`, `gate_ledger_readout.py`,
`judgment_grading.py`, the Engine Track Record card in `app.py`) — Opus reviewer SHIP, 0
blocking, full suite 6116 passed. **The `recommendations` pagination slice of Phase 2 shipped as
commit `eea121d`**, pulled forward ahead of schedule because it was a confirmed (not
theoretical) prerequisite for trusting Phase 1's own re-derived numbers — Opus reviewer SHIP, 0
blocking (one non-blocking suggestion applied before commit). **The live Engine Track Record
"+14.4pp" headline has moved as a result — this is expected and disclosed, not a regression;
the new number has not yet been independently reconfirmed against a fresh screenshot.**

**Remaining, not yet started:** the rest of Phase 2 (pagination for the other 7 tables named in
§2 A7), Phase 3 (A4/A5/A8/C3 — the small bounded fixes; C2 is now closed, needs nothing further),
Phase 4 (`COMPOSITE_WEIGHTS` durable versioning — sized but not designed), Phase 5 (deferred,
correctly, since it has zero data to act on).

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

### A7. [CODE-VERIFIED] Silent-truncation risk on unbounded reads — already confirmed to have bitten this project once

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

### A8. [CODE-VERIFIED] Income-event vocabulary genuinely splits one real cost category by ingestion path

`broker_sync.py::income_event_subtype()` (975-1013) mostly unifies the CSV and live-SnapTrade
vocabularies, but one real, currently-live gap remains: CSV `MINT` (confirmed real margin
interest) is kept as its own `margin_interest` subtype, while this account's live SnapTrade
connector reports the same real-world charge type under the generic `type="FEE"`
(`broker_sync.py:961-970`, confirmed by the module's own comment against the owner's actual
statement). A dedup-matching override (`_DEDUP_RAW_CODE_BUCKET_OVERRIDE`, 1035-1037) lets
duplicate-detection see through this, but `income_event_subtype()` itself — the function any
interest-cost trend chart groups by — still returns two different labels for the same real
event depending on which path captured it. **Any interest-cost trend built by grouping on
subtype silently splits one real cost line into two**, with the split boundary being
"which ingestion path happened to capture this particular charge."

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
    `recommendations` (and `exit_signals` if it stores a composite), backfilled by date for
    every existing row
    gets this for free instead of re-deriving the boundary each time. This is a `constants.py`
    -adjacent, DB-write change — see §6 for the review-gate implication.

### C2. Duplicate-row cleanups already identified, status unconfirmed

Two known, already-diagnosed duplicate-row issues have documented one-time SQL fixes that may
or may not have been run:
  - `snaptrade_income_events` — 12 duplicate rows (3 MINT margin-interest, 9 CDIV/MDIV dividend)
    found 2026-09-11, with a documented manual cleanup query (`db.py:883-887`).
  - `account_flows` — pre-fix duplicates from before the `snaptrade_txn_id` unique index was
    added (`db.py:818-841`); "net_contributed_capital stays inflated until they are manually
    reviewed/deleted" (`db.py:830-832`).

**[NEEDS LIVE QUERY]** — a simple `COUNT(*)`/`GROUP BY` check on both tables would confirm
whether these were run. If not, this is a bounded, one-time, low-risk cleanup — not a design
question.

### C3. Sector-taxonomy reconciliation gap — bounded, sweepable

`reference_data.validate_payload`'s presence-only check for `discovery_universe` tickers
against `TICKER_SECTORS` is explicitly scoped to *newly changed* tickers only
(`reference_data.py:229-239`), grandfathering an unknown-but-real subset of pre-existing
tickers that may still disagree with `TICKER_SECTORS`'s own classification. This is finite and
sweepable: a one-time pass comparing every `discovery_universe` ticker against `TICKER_SECTORS`
would surface the full disagreement list in one query, closeable the same way the
Industrials/Defense/Utilities dedup work already closed three prior instances of this exact
class.

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

1. **Reuse the proven earliest-anchor collapse PATTERN — not a mandatory shared module
   (revised).** The first draft of this control oversold how identical the three cases are.
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

2. **Extend real pagination (the `model_predictions` pattern) to every unbounded loader named in
   A7**, rather than patch each with its own ad-hoc `.limit()` ceiling. This is mechanical,
   `db.py`-scoped, and directly closes the single highest-uncertainty risk in this whole
   assessment.

3. **Version-stamp any constant that produces a value persisted long-term.** `SIZING_FORMULA_VERSION`
   is the good model; `COMPOSITE_WEIGHTS` is the gap. Add one line to the Definition-of-Done
   checklist (CLAUDE.md already has a 7-step DoD): *"if a changed constant feeds a value written
   to a history/track-record table, does a reader need to know which regime produced an old row?
   If yes, version it."* This is a documentation/discipline addition, not a new mechanism.

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

**Phase 0 — background live-data verification (no code, needs the owner's Supabase access, does
NOT block Phase 1).** Row counts for the tables named in A7 (has silent truncation already
happened anywhere besides `model_predictions`?); whether the two C2 duplicate-cleanup queries
were run; current maturity (row/distinct-ticker counts) for `exit_signals`, `gate_suppressions`,
`rec_events`, `judgment_grades` against their own `MIN_CALLS`/`FIRM_CALLS` floors. Costs nothing
but a handful of `SELECT COUNT(*)` queries; run it in parallel with Phase 1, not before it.

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

**Phase 3 — the small, bounded fixes.** A4 (rec-side dedup in `match_recs_to_trades`), A5
(`debrief_advisor.py`'s date filter), A8 (income-event subtype unification's remaining gap), C2
(duplicate-row cleanup, contingent on Phase 0's answer), C3 (sector-taxonomy sweep). Each is
independent, small, and low-risk — bundle or sequence at the owner's discretion. A4 and A5 touch
a decision-adjacent history table; a voluntary `reviewer` pass is proportionate, same logic as
Phase 1.

**Phase 4 — `COMPOSITE_WEIGHTS` durable versioning (the schema half of C1 only), a genuine
policy decision.** The cheap segment-by-date discipline already moved to Phase 1 as a
prerequisite. What's left for Phase 4 is only the durable question: whether to add a
`weights_version` column, backfilled by date, so future readers get the boundary for free
instead of re-deriving it each time. Real `constants.py`/`db.py` implications — **squarely a
`planner`-then-`reviewer` item per Hard Rule #4**, and its priority should be set by Phase 0's
missing query (see §9): if `recommendations`/`exit_signals` turn out to hold few or no
pre-2026-07-09 rows, this phase's urgency drops to near zero.

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

## 9. Open items needing a live Supabase query before Phase 0 can close

1. Current row counts for `gate_suppressions`, `recommendations`, `rec_events`, `exit_signals`,
   `score_history`, `judgment_opinions`/`judgment_grades`, `analyst_target_snapshots`,
   `daily_snapshots` — has silent truncation already happened on any of these besides the
   confirmed `model_predictions` incident?
2. Whether the two documented C2 duplicate-row cleanups (`snaptrade_income_events`,
   `account_flows`) were actually run.
3. Current distinct-ticker and total-row counts for `exit_signals`/`gate_suppressions`/
   `rec_events`/`judgment_grades` against their own maturity floors, both raw and (once Phase 1
   ships) collapsed — to see how much the collapse actually moves each feature's maturity date.
4. A live re-run of `correlation_coverage()`'s `n_obs` (portfolio.py) — last measured sound at
   125 observations on 2026-08-21; that's a point-in-time fact, not a standing guarantee.
5. **[Added on review]** What fraction of `recommendations`/`exit_signals` rows predate
   2026-07-09? A single `SELECT COUNT(*) FILTER (WHERE rec_date < '2026-07-09'), COUNT(*) FROM
   recommendations` (and the `exit_signals` equivalent on `signal_date`) directly sizes Phase 4:
   if the pre-boundary population is a small minority (or empty — if meaningful capture only
   began after that date), the `weights_version` schema question becomes low-stakes rather than
   something needing a `planner` design pass soon.
