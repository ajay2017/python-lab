# Measuring whether Rebalancer / Diversification-ADD / Tax-Harvest calls actually worked

**Status: ALL 4 OWNER DECISIONS RATIFIED, 2026-09-15 — READY FOR BUILD, no code written
yet.** §10 is the Opus `planner` design (Opus 4.8); §11 records the ratified decisions.
Phase 1a (a new `portfolio_risk_snapshots` daily EOD capture, three non-colliding
`trigger_type` values, and a non-banded tax-harvest running total) is fully unblocked.
Phase 1b/2 (the rec-attribution ledger + banded readout) is also unblocked, gated only on
Phase 1a's capture actually running long enough to accumulate data. See §10 for the full
plan, risks, and required tests; §11 for exactly what was decided and why.

Source research: `docs/reviews/2026-09-09-app-review.md` Part 2 #4; memory
`project_app_review_2026_09_09`'s 2026-09-14 section (exact file:line citations for the three
generators, confirmed zero persistence); a follow-up Explore pass 2026-09-15 (this doc) for
the reusable-pattern audit in §5.

---

## 1. Why this needs a scenario pass before `planner`, not just a research brief

The three things this measures — a beta/concentration TRIM, a diversification ADD, a
tax-loss HARVEST — are not three instances of one problem. Each has a genuinely different
definition of "worked," a different confound, and a different (mostly missing) attribution
path. A `planner` pass that starts from "measure recommendation outcomes" in the abstract
will re-derive these distinctions from scratch and likely lock in the first plausible
definition of "outcome" it finds for each — probably the ticker-vs-SPY alpha shape, because
that's the one existing pattern (`recommendations_history.py`/`protective_track_record.py`)
already does well. That shape is WRONG or incomplete for two of the three types (§3). This
doc exists so that mismatch is visible before design starts, not discovered mid-build.

---

## 2. The three types compared

| | Rebalancer trim (beta/concentration) | Diversification ADD | Tax-Harvest |
|---|---|---|---|
| Generator | `portfolio.py::rebalance_actions()` (`:704`), `risk_advisor.py` `type="beta"` (`:249`)/`"sector_concentration"` (`:721`)/`"single_name_concentration"` (`:789`) | `portfolio.py::diversification_recommendations()` (`:1519`), ADD dict `:1607-1636` | `tax_advisor.py::build_tax_analysis()` (`:152`), `harvestable`/`harvest_blocked` |
| The bet | Selling X reduces portfolio-level risk (beta/concentration) without giving up too much expected return | Buying Y reduces correlation/concentration and is itself a reasonable position | Realizing a loss now saves tax dollars without meaningfully hurting future return |
| Naive "worked" | Ticker Y beat SPY after the ADD (porting the BUY-side shape) — **wrong primary metric**, see §3b | Did portfolio beta/concentration actually fall, and was giving up the trimmed position's subsequent return worth it | Tax dollars saved − opportunity cost of being out of the position − any wash-sale penalty incurred |
| Denominator of success | The **portfolio metric** (beta, sector %, single-name %), not the traded ticker's own return | The **portfolio metric** (correlation/diversification score) AND the candidate's own return (two legs) | A **dollar** figure (tax saved) that is real and computable almost immediately, separate from a return-based leg |
| When is "outcome" knowable | Mechanical leg (did beta fall) — same day/next snapshot. Was-it-worth-it leg — needs weeks/months (§3a) | Mechanical leg — same day. Return leg — weeks/months, same horizon problem as BUY-side | Tax-benefit leg — knowable immediately (tax bracket × loss realized). Wash-sale compliance — knowable at day 31. Opportunity-cost leg — weeks/months |
| Persisted today | Nothing (confirmed) | Nothing (confirmed) | Nothing (confirmed) |
| Act-on-detection today | **Broken even for a real design**: Rebalancer's own quick-log button hardcodes `trigger_type="RECOMMENDATION"` for trim/add/review alike (`app.py:13709,13721`) — a Rebalancer trim is INDISTINGUISHABLE from any other rec-sourced trade by `trigger_type` today | **No quick-log button exists at all** (confirmed: `_tj_prefill` set at only 3 app-wide sites, none diversification-sourced) | **No quick-log button exists at all** — same gap |

---

## 3. Per-type scenario catalog

### 3a. Rebalancer trim / risk_advisor concentration calls

**Candidate outcome definitions (pick one, or capture enough to support several):**
- **Mechanical correctness**: did the portfolio metric (beta / sector % / single-name %) that
  the call was computed against actually decrease after the trim executed? This is
  answerable almost immediately (needs a portfolio-level metric snapshot at call time and
  another N days later — neither exists today, §4).
- **Risk-adjusted opportunity cost**: the trimmed ticker's subsequent return matters too — a
  trim that correctly lowered beta right before that ticker rallied 20% is mechanically
  "correct" but may have been a bad trade in hindsight. Needs the trimmed ticker's own
  forward return AND a market-direction control (a trim that avoids a drawdown is good; a
  trim that avoids nothing because the market rose is a pure cost).
- **Compound / did-it-matter**: did NOT trimming (the counterfactual) coincide with a
  drawdown the trim would have avoided, weighted by how much beta was actually reduced?
  This is the real question behind the owner's original prompt ("beta is elevated — is
  trimming the only lever") but needs a shocked/counterfactual portfolio simulation
  (`forward_portfolio_simulator.py` already exists for a DIFFERENT purpose — Rate-Spike
  stress testing — and might be partially reusable, needs checking, not assumed here).

**Confounds specific to this type:**
- Portfolio beta drifts on its own every day from price moves and OTHER trades — isolating
  the effect of ONE trim requires either (a) a controlled "beta with vs without this trim"
  counterfactual computed AT REC TIME (compare `expected_beta_after_trim` — does this
  function exist yet the way `expected_beta_after_add` does? **Not confirmed — check before
  build**), or (b) accepting a noisier "did beta trend down over the following N days"
  proxy that other trades can pollute.
- Multiple trims can fire on the same day across different tickers for the same underlying
  beta call — is the "outcome" attributed per-ticker or per-rec-event (all trims issued
  together on one day, as one bundle)?
- A Rebalancer trim and an Exit Advisor EXIT/TRIM/WATCH call can fire on the SAME ticker at
  the same time for DIFFERENT reasons (one risk-driven, one deterioration-driven) — if the
  owner sells, which system gets credit? Needs an explicit attribution rule (e.g. "if both
  fired within N days, log both, let the readout show the overlap rather than picking one").
- Partial fills: owner may trim fewer/more shares than recommended. Does "acted on" require
  an exact match, a directional match (any SELL on that ticker within N days), or a
  percentage-of-recommended-shares threshold?

### 3b. Diversification ADD

**Candidate outcome definitions:**
- **Mechanical correctness (portfolio leg)**: did adding the position actually reduce
  portfolio correlation/sector concentration by roughly the claimed amount?
  `expected_beta_after_add()` (`portfolio.py:1293`, shipped 2026-09-14) already computes a
  live "expected" figure for beta specifically — but it is NOT persisted anywhere (confirmed:
  no session_state write, sole caller only renders a caption, `app.py:13985-13999`) and it
  covers beta only, not the correlation/sector-gap metric the ADD card is actually built
  around (`corr_to_tech`, `gap_pct`, `:1649-1664`). A real outcome measurement needs the
  ANALOGOUS "expected correlation after add" figure captured at rec time, which doesn't
  exist yet even as a live display.
- **Candidate-return leg**: did the added ticker itself perform reasonably (avoid a
  disaster), independent of the portfolio-level diversification effect? This is the ONE leg
  that ports the existing BUY-side alpha-vs-SPY shape cleanly — but it is only HALF the
  story, and building only this half (because it's the cheap, familiar pattern) would
  silently drop the metric the recommendation is actually about.
- **Never-acted counterfactual**: for an ADD candidate shown but never bought, is there any
  value in tracking "would it have helped" the same way the Gate Suppression Ledger tracks
  counterfactual suppressions? This doubles the scope (needs the same rec-time correlation
  snapshot for EVERY candidate surfaced, not just ones bought) — a genuine phase-2-or-never
  candidate, not phase 1.

**Confounds specific to this type:** correlation is measured off a LISTWISE-deletion matrix
across the whole book (`project_correlation_sample_size` — currently sound, but a known
latent fragility); a correlation-outcome metric captured today and re-measured later must
account for the sample composition possibly having shifted (tickers added/dropped from the
book) independent of the one ADD being evaluated.

### 3c. Tax-Harvest

**This is NOT a return-based bet like the other two — treat it as a different problem, not
a smaller version of the same one.**

**Candidate outcome definitions:**
- **Tax dollars realized**: loss amount × an assumed marginal rate. Knowable IMMEDIATELY at
  the sell (no waiting period at all) — the one leg of this entire feature that doesn't need
  a maturation window. Needs a tax-bracket assumption/input that doesn't exist anywhere in
  the app today (check: is there ANY tax-rate constant/setting currently? If not, this is a
  new user input, itself a scoped mini-decision).
- **Wash-sale compliance**: `wash_sale_risk()` (`tax_advisor.py:408-449`) is a SEPARATE
  function from `harvestable`/`harvest_blocked`, called independently and only from the
  Position Sizer/SELL-confirm flow (`app.py:25141`) — it is NEVER currently joined to the
  harvest recommendation's own output row. An outcome measurement must decide whether it
  cares about actual wash-sale VIOLATIONS following a harvest sale (did the owner or the
  app's own later BUY recommendations re-enter the same/substantially-identical position
  within the 30-day window and trigger a real violation) — this is a genuinely new
  cross-check, not present in either function today.
- **Opportunity cost**: did the harvested ticker rally in the 31+ days before a legitimate
  rebuy would have been allowed, and if the owner never rebought, is that a foregone-gain
  cost against the realized tax benefit?
- **Seasonality**: tax-loss harvesting is naturally clustered near year-end. A `min_calls`
  floor modeled on the other two ledgers (which fire near-daily) may take YEARS to mature
  for this type if harvest events are rare — this type likely needs either a much longer
  maturity horizon or a fundamentally different "is this worth tracking as a graded ledger
  at all, vs. a simple running total" framing (see §7).

---

## 4. Cross-cutting scenarios (apply to more than one type)

- **Streak/re-issue collapsing.** All three recommendation types recompute LIVE on every
  render — the same trim/ADD/harvest candidate can appear on 10 consecutive daily sessions
  until acted on or the condition resolves. Every existing ledger in this codebase solves
  this with a collapse/dedup step (`protective_track_record.py::collapse_by_ticker`,
  `gate_ledger_readout.py`'s per-row tagging) — a new capture mechanism needs an equivalent,
  decided per type (dedupe by ticker+rec-type+N-day window is the obvious default, but the
  right window differs: a beta call might persist for weeks while conditions hold, a
  tax-harvest window is bounded by when the loss exists at all).
- **Acted vs skipped, and what skipped means.** The Engine Track Record's own A2 finding
  (see `project_investor_maturity_roadmap`) is that acted-vs-skipped alpha differs sharply —
  the same split matters here. But "skipped" for a portfolio-level call (beta/diversification)
  is fuzzier than for a single-ticker BUY: the owner might act PARTIALLY (trim half the
  suggested shares), or address the SAME underlying condition through a DIFFERENT ticker
  than the one recommended (sell a different high-beta name instead of the suggested one) —
  does that count as "acted on the call" or "skipped"? Needs an explicit rule, not an
  implicit one that quietly picks the easy case.
- **Attribution when systems overlap.** Named in §3a for Rebalancer-vs-Exit-Advisor; the
  same shape recurs between Diversification-ADD and Watchlist ENTER_NOW (an ADD candidate
  might also independently qualify as a Watchlist entry) and between Tax-Harvest and
  Exit Advisor (a harvestable loser might also be under an active EXIT call for unrelated
  deterioration reasons) — an owner action on that ticker could be "credited" to more than
  one system's call simultaneously. Decide once, generally, rather than per-pair.
- **`trigger_type` collision risk.** `REBALANCE` already exists as a `trades.trigger_type`
  value but is used TODAY only for synthetic stock-split adjustment rows (`app.py:4914`,
  `action="SPLIT"`) — reusing it for real rebalancer-motivated trims without checking every
  existing reader of `trigger_type=='REBALANCE'` would silently conflate split-adjustment
  bookkeeping rows with real trade attribution rows. If new trigger_type values are the
  chosen mechanism (vs. a separate attribution table), they need genuinely new names
  (`REBALANCE_TRIM`? `DIVERSIFY_ADD`? `TAX_HARVEST`?), not a reuse of `REBALANCE`.
- **`decision_context` partial reuse opportunity.** `decision_context.build_snapshot()`
  (`decision_context.py:48-131`) ALREADY captures `portfolio.beta` (`:120-121`) and a single
  `top_sector`+weight (`:72-84`, NOT a full sector vector, NOT correlation state) on every
  INTERACTIVE trade log — meaning any Rebalancer trim logged through the app's own trade
  journal (not a broker-sync import) already has a beta-at-trade-time data point sitting
  unused in `trades.decision_context` jsonb, IF it can be identified as a rebalancer-motivated
  trade in the first place (which today it cannot — see the `trigger_type` gap above). This
  is a real, already-flowing data source worth deciding whether to mine, rather than
  necessarily building a parallel capture path from scratch — but it only covers beta, not
  correlation/sector-vector/diversification-score, and only forward from whenever attribution
  starts working, never retroactively (no way to know from history which past trades were
  rebalancer-motivated).
- **Mechanical-correctness vs dollar-outcome are two different questions, for all three
  types.** "Did the metric move the way the recommendation predicted" (proves the app's
  math/logic works) and "did it make the owner better or worse off in dollars" (proves the
  app's JUDGMENT is valuable) are both worth knowing and can disagree — capture enough to
  report both, don't collapse to one number prematurely the way the Engine Track Record's
  own A2 finding suggests may already be happening for the BUY-side headline.

---

## 5. Reusable-pattern audit (what already exists to copy, not reinvent)

| Pattern piece | Existing implementation | Reusable as-is? |
|---|---|---|
| Capture-only, forward-only, cron-written, availability-flagged rows | `score_history` (`db.py:3209-3246`, `save_score_history_batch`) — NULL means "not measurable," never a fabricated neutral; idempotent `ON CONFLICT` upsert key; **zero readout built on it 4+ months after capture started** — a live cautionary example that capture without a scoped readout plan can sit dormant | Shape yes, but see §7 — don't repeat "capture with no readout deadline" |
| Match rec → later trade | `recommendations_history.match_recs_to_trades()` (`:185-255`) — same-day ticker match on `trigger_type=='RECOMMENDATION'` | No — needs new/expanded `trigger_type` values or a separate attribution table (§4) since none of the 3 types are taggable today |
| Collapse duplicate/re-issued recs | `protective_track_record.collapse_by_ticker()` (`:136-199`) — one row/ticker, earliest priced signal_date, max-severity | Partially — collapse KEY differs per type (§4) |
| Outcome vs SPY, maturity gate | `compute_outcomes()`/`compute_protective_outcomes()`, `ENGINE_TRACK_MIN_CALLS=8`/`FIRM_CALLS=15`, `PROTECT_TRACK_MIN_CALLS=8`/`FIRM_CALLS=15` (`constants.py:1244-1254`, deliberately duplicated per-ledger, not shared) | Only for the candidate-return leg (§3b) — wrong primary metric for §3a/3c |
| N-floor + K-distinct-tickers floor + pre-registered retirement date | `gate_ledger_readout.py`, `GATE_LEDGER_MIN_CALLS=8`/`FIRM_CALLS=15`/`MIN_TICKERS=5`/`HORIZON_TRADING_DAYS=30` (`constants.py:1271-1278`); 12-month retirement pre-registered BEFORE any data existed (`docs/plans/gate-suppression-ledger.md:138-141`) | Yes for the "building/early/firm" verdict shape; floor VALUES likely need per-type tuning (tax-harvest is rare/seasonal, §3c) |
| Portfolio-level (not per-ticker) metric history | **Does not exist anywhere** — `account_daily_snapshots` (F-266) is margin/leverage only, no beta/sector/correlation column; `score_history` is per-ticker | New capture required regardless of every other design choice |

---

## 6. Architecture options (genuinely open, not a recommendation)

- **A. One unified table** (`rec_type` discriminator: `rebalance_trim` / `diversify_add` /
  `tax_harvest`) vs **B. three purpose-built tables**, mirroring how the codebase already
  keeps `ENGINE_TRACK_*` and `PROTECT_TRACK_*` constants deliberately separate even though
  the shape is identical. Given how different the outcome legs are (§2 table), B may fit
  this codebase's own stated preference better than A — but the shared collapse/banding
  CODE (not necessarily the table) should still be one module if the logic converges.
- **Attribution mechanism**: new `trigger_type` values (needs a UI selectbox change +
  migration awareness, but reuses existing `match_recs_to_trades`-style matching) vs. a
  dedicated `rec_attributions` linking table keyed on rec-id + trade-id (more flexible for
  the overlap/multi-credit cases in §4, more new plumbing).
- **Rec-time snapshot mechanism**: extend `decision_context` (already fires on every
  interactive trade, already has a beta field) vs. a new standalone snapshot captured at the
  moment the recommendation itself is COMPUTED (not at trade time) — the latter is necessary
  regardless for the "shown but never acted on" counterfactual case (§3b), since
  `decision_context` only fires on an actual trade.
- **Where portfolio-level beta/correlation/sector-vector history lives**: a new table
  (parallel to `account_daily_snapshots` but for risk/diversification metrics, capturing
  DAILY regardless of whether any rec fired that day) vs. capturing ONLY at rec-fire moments
  (cheaper, but can't compute a clean "what would beta have been without this one trim"
  counterfactual later without the surrounding days' context).

---

## 7. Sequencing / phasing options

- **Capture-first, readout-much-later** (the `score_history`/F-269 shape) — lowest risk,
  but §5's table already flags that `score_history` itself has sat capture-only with zero
  readout for 4+ months; repeating that pattern without a scoped readout commitment risks a
  second dormant table.
- **Capture + a HARD readout deadline or trigger condition set at design time** (borrowing
  the Gate Ledger's pre-registered-before-data-exists discipline, §5) — avoids the above.
- **Ship the cheapest, fastest-maturing leg first**: of the three, tax-harvest's dollar-value
  leg is knowable IMMEDIATELY (no maturation window at all) — a scoped "tax dollars
  harvested to date" running total could ship far faster than any beta/correlation
  mechanical-correctness leg, which needs weeks of post-capture data regardless of when
  capture starts. Worth deciding whether phase 1 is "the fastest real number" rather than
  "the most complete design."
- **Retroactive partial bootstrap**: NOT possible for beta/correlation history (confirmed,
  §"No historical table" in prior research) or for act-on-detection (confirmed, no
  `trigger_type` distinguishes past rebalancer trims). The ONLY retroactive angle is mining
  `decision_context.beta` on already-logged INTERACTIVE trades if a heuristic can identify
  which were rebalancer-motivated after the fact (e.g., cross-referencing trade date against
  a day `rebalance_actions()` would have suggested that ticker — reconstructable since the
  function is pure/live, GIVEN the historical portfolio state to feed it, which itself may
  not be reconstructable exactly). Flag as a possible cheap win, not a plan to rely on.

---

## 8. Explicit policy-decision checklist (for `planner` + owner, not to be silently decided)

1. Which outcome leg(s) does phase 1 actually measure per type — mechanical correctness,
   dollar/return outcome, or both (§2, §3)?
2. Is a tax-bracket/marginal-rate assumption introduced as a new setting, and where does it
   live (a new `constants.py` entry needs Hard Rule #1 discussion; a user-editable setting
   is a different, lighter-weight path)?
3. New `trigger_type` values, or a separate attribution table (§6)? If new trigger_type
   values: exact names (must NOT collide with `REBALANCE`'s existing SPLIT-only usage, §4).
4. Unified table vs three tables (§6)?
5. How is "acted on" defined when the owner trims a DIFFERENT ticker than recommended, or a
   partial share count (§4)?
6. How is cross-system attribution handled when a Rebalancer/Diversification/Tax-Harvest
   call overlaps an Exit Advisor or Watchlist call on the same ticker (§4)?
7. Per-type maturity floors (`min_calls`/`firm_calls`/`min_tickers`) — copy the existing
   8/15/5 convention, or does tax-harvest's rarity/seasonality need a different shape
   entirely, e.g. a running total with no "building/firm" banding at all (§3c, §7)?
8. Is a retirement/re-scope criterion pre-registered NOW, before any data exists, per the
   Gate Ledger's own discipline (§7)?
9. Does phase 1 also capture the NEVER-ACTED counterfactual case (§3b), or is that
   explicitly deferred to a phase 2 (doubles capture scope if included now)?
10. Where does this render — a new dedicated page, or folded into an existing one (My Edge,
    Engine Track Record, or a Gate-Ledger-style new page)? Not scoped in this doc at all.
11. Could a mature verdict here ever tighten a live gate/threshold in some future phase
    (mirroring Predictive Shadow Modeling's Phase 3 "tightening-only, never loosens, needs
    fresh explicit approval" gate — `project_predictive_shadow_modeling`), or is this
    permanently awareness-only like the Gate Suppression Ledger? Doesn't need an answer now,
    but the capture schema should not foreclose the option if the answer is later "yes."

---

## 9. Explicitly out of scope for this doc

No code, no schema, no constants have been written or decided. This is a scenario/options
catalog only — the next step is a `planner` (Opus) pass that reads this doc plus the prior
research in `project_app_review_2026_09_09`, resolves as many of §8's checklist items as it
can on its own judgment, and returns the remainder as explicit questions for the owner
(matching the Analyst-Email-Ingestion and Sizing-Calibration precedents, where planner
resolved most implementation gaps and only escalated genuine policy calls).

---

## 10. `planner` design pass — 2026-09-15, Opus 4.8, verdict PROCEED WITH QUESTIONS

Corrections to this doc's own open uncertainties, confirmed against HEAD by the planner
pass (don't re-check these, they're settled): `expected_beta_after_add` exists
(`portfolio.py:1293`) but **`expected_beta_after_trim` does NOT** — `risk_advisor.py:238`
already computes a live predicted post-trim beta (`_new_beta`) inline, unpersisted.
**`TAX_RATE_SHORT_TERM=0.37` / `TAX_RATE_LONG_TERM=0.20` already exist**
(`constants.py:1420-1421`) — §8 Q2 (tax-rate setting) is resolved, no new constant needed for
the dollar leg. `REBALANCE` trigger_type is doubly polluted — SPLIT synthetic rows
(`app.py:4914`) AND a live user-selectable journal option (`app.py:26076`) — confirming §4's
collision-risk flag; new names are mandatory, not merely preferred.

**Redline the plan will not cross:** do not port the BUY-side alpha-vs-SPY shape onto all
three types — it is the wrong primary metric for the trim and only half the story for the
ADD (§3b), and the planner independently confirmed this from the code, not just this doc's
framing.

### Plan

**Phase 1a — fastest real numbers + stand up all capture, no maturation wait:**
1. New table `portfolio_risk_snapshots`, daily EOD-cron write: `date, portfolio_beta,
   top_sector, top_sector_pct, max_single_name_pct, avg_pairwise_corr,
   diversification_score, corr_coverage_n`. NULL-preserving on producer failure (same
   contract as `account_daily_snapshots`/`score_history`) — this alone answers the owner's
   original live question ("is beta actually coming down") as a chart, with zero attribution
   confound. Touches `db.py`/`cron_runner.py`/`system_health.py` (mandatory Opus review) +
   new pure `stock_analyzer/risk_metric_history.py`.
2. Three non-colliding `trigger_type` values — `REBAL_TRIM`, `DIVERSIFY_ADD`,
   `TAX_HARVEST` — added to the journal selectbox (`app.py:26076-26077`) and the Rebalancer
   quick-log (`app.py:13721`, currently hardcoded to `RECOMMENDATION`). Every existing
   `trigger_type` reader must be audited first (P&L-by-trigger groupby `app.py:26847`,
   buy-from-alerts `app.py:31123`, the SPLIT-row writer `app.py:4914`).
3. Harvest dollar running-total — pure function reading `TAX_HARVEST`-tagged loss SELLs,
   realized loss × `TAX_RATE_*`, reported as "$X harvested across N events since [date]."
   **Non-banded, no maturity floor** (tax-harvest is rare/seasonal — §3c/§7's concern
   resolved by NOT forcing it into the 8/15/5 shape). Plus a wash-sale COMPLIANCE check
   extending `wash_sale_risk()` to the AFTER-side (a rebuy within 30 days of a harvest sale),
   which today only checks before a sale.

**Phase 1b/2 — the maturing legs, pre-registered trigger:**
4. `rec_events` capture table (one collapsed row per rec-episode, dedup via the
   `protective_track_record.collapse_by_ticker` pattern): `rec_type, ticker_or_sector,
   fired_date, metric_before, metric_predicted_after (nullable), rec_dollars, candidates
   (jsonb, ADD only), corr_coverage_n (ADD)`. Captures `risk_advisor`'s own `_new_beta` as
   `metric_predicted_after` so the later readout compares predicted-vs-realized honestly.
5. Attribution + banded readout matches `rec_events` to trades by ticker/sector + window +
   direction (new trigger_type as a confirming, not sole, signal). Trim readout =
   predicted-vs-realized metric delta, captioned that other trades/price moves also move
   beta. ADD readout = candidate-return-vs-SPY leg labeled precisely as "the added name's
   own performance," never "diversification worked," alongside the correlation snapshot for
   the mechanical leg. New, deliberately-duplicated `constants.py` floors (8/15/5 + 30-day
   horizon), per this codebase's own convention of not sharing ledger constants.
6. **Deferred, not dropped:** the never-acted ADD counterfactual (§3b) and the trim/harvest
   opportunity-cost return legs — phase 3-or-never.

### 4 owner decisions outstanding (not the planner's to pick)

1. **Render home.** Recommend: the Phase-1a metric-trend chart folds into 💰 Account beside
   the F-266 Leverage/Margin and Capital Trend charts (same Weekly/Monthly/All toggle); the
   harvest running-total folds into the existing Tax lens. The Phase-2 attribution readout
   (Gate-Ledger-style owner-only page vs. a My Edge tab) is a product-shape call, not urgent
   until Phase 2.
2. **Harvest dollar figure basis.** Uses the existing top-bracket defaults
   (`TAX_RATE_SHORT_TERM`/`TAX_RATE_LONG_TERM`). Recommend: display-only caption ("estimated
   at top federal bracket, not your realized tax") for Phase 1, vs. a personal effective-rate
   input (a scoped later add). Personal tax data — owner's call, not the planner's.
3. **Attribution generosity.** If elevated beta is addressed by selling a DIFFERENT high-beta
   name than the one the app named, does that credit the rebalancer call, or only move the
   portfolio-metric chart? Recommend: do NOT credit the specific rec (honest — the metric
   chart still reflects the improvement either way), but how generous attribution should be
   in general is the owner's call.
4. **Ratify the pre-registered Phase-2 trigger + retirement date** (below) before any capture
   data exists — per the Gate-Ledger precedent, the calendar date itself should be
   owner-ratified, not planner-set.

**Proposed pre-registration (for ratification, §8 Q8):** the trim/ADD attribution readouts
render a first verdict only once a type reaches its MIN_CALLS floor at horizon; if no type
produces an evaluable verdict distinguishable from zero within **12 months of first
`rec_events` capture**, that type's readout is retired (retiring is the success condition,
per the Gate-Ledger discipline — not to be softened later to save the feature). The harvest
running-total and the metric-trend chart are exempt (immediate, non-banded, nothing to
retire).

### Risks / coordination (from the planner pass)

- **Cross-system overlap — one general rule, not per-pair:** when a trade matches more than
  one ledger (e.g. a SELL that's both a rebalancer trim AND an Exit Advisor EXIT), **credit
  both and have the readout disclose the overlap — never pick a single winner.** Mirrors the
  `_reduce_calls` cross-feature disclosure posture, applied retrospectively.
- **Offline contract:** both new tables must write NULL on producer failure, never a
  fabricated 0/neutral — the exact `feedback_sentinel_is_present`/
  `feedback_overloaded_producer_state` class this project has already been bitten by twice.
- **Correlation sample-composition confound:** `corr_coverage_n` must be persisted at rec
  time so a later re-measure can tell whether the listwise sample shifted independent of the
  one ADD being evaluated (`project_correlation_sample_size`).
- **score_history is the cautionary tale, not a template to repeat as-is:** Phase 1a MUST
  ship visible numbers, not capture-only — `score_history`/F-269 has sat capture-only with
  zero readout for 4+ months. This is why Phase 1a includes a chart and a running total, not
  just a table.
- **Calm-advisor posture:** none of this is Act Today material — retrospective awareness
  only (Account trend chart, owner-only readout). No new banner stacking (respects the
  declined `project_home_redesign` posture).
- **Awareness-only redline:** no rec/scoring/sizing engine may read these tables in Phase
  1-2 — keep the schema decision-neutral so a future tightening-only phase (mirroring
  Predictive Shadow Modeling's Phase 3 gate) isn't foreclosed, without building toward it now.
- **Review scope:** `db.py`/`cron_runner.py`/`system_health.py`/`tax_advisor.py`/
  `constants.py`/`risk_advisor.py` are all `_GATE_FILES` or DB-write paths — every capture
  chunk carries a mandatory Opus review. If `expected_beta_after_trim` is extracted as a
  reusable function, it belongs in `portfolio.py`/`risk_advisor.py` (review-required), not a
  duplicate of the inline `_new_beta` math.

### Tests the build must include

Harvest dollar math (realized-loss × rate, excludes gains, NULL-preserving) · wash-sale
boundary (day 30 flags, day 31 does NOT — the exact never-fires-at-the-window-edge
invariant) · `portfolio_risk_snapshots` NULL-on-producer-failure · collapse (N consecutive
re-fires → one `rec_events` row) · attribution invariants (named-ticker-in-window matches;
different-ticker does not credit the specific rec; a SELL matching both the trim and the
exit ledger credits BOTH) · `trigger_type` non-collision (a `REBALANCE`/SPLIT row is never
counted as a `REBAL_TRIM`) · band-boundary tests for the Phase-2 readouts.

Full planner citations: `stock_analyzer/portfolio.py` (`rebalance_actions` ~704,
`expected_beta_after_add` 1293), `stock_analyzer/risk_advisor.py` (`_new_beta` ~238),
`stock_analyzer/tax_advisor.py` (`build_tax_analysis` 152, `wash_sale_risk` 408),
`stock_analyzer/constants.py` (`TAX_RATE_*` 1420, ledger floors 1244-1278),
`stock_analyzer/decision_context.py` (`build_snapshot` 48),
`stock_analyzer/protective_track_record.py` (`collapse_by_ticker` 136), `app.py` (SPLIT
REBALANCE row 4914, Rebalancer quick-log 13716-13721, trigger_type selectbox 26076).

---

## 11. Owner decisions — ratified 2026-09-15

1. **Render home: fold into existing pages.** Phase 1a's metric-trend chart renders on
   💰 Account beside the existing F-266 Leverage/Margin and Capital Trend charts (same
   Weekly/Monthly/All toggle); the harvest running-total renders in the existing Tax lens.
   No new page for Phase 1a. (Phase 2's attribution-readout page — Gate-Ledger-style new
   page vs. a My Edge tab — remains an open, non-blocking call for when Phase 2 is built.)
2. **Tax-rate basis: top-bracket caption only.** Uses the existing `TAX_RATE_SHORT_TERM`/
   `TAX_RATE_LONG_TERM` constants (`constants.py:1420-1421`), captioned "estimated at top
   federal bracket, not your realized tax." No new setting, no new constant.
3. **Attribution rule: do not credit a different ticker.** Selling a DIFFERENT high-beta
   name than the one the app named does NOT credit the rebalancer call as "acted on" —
   only an action on the actual named ticker counts as acting on THAT call. The
   portfolio-metric chart (Phase 1a) still shows the improvement either way, independent of
   attribution.
4. **Retirement + sanity-check schedule — amended from the planner's straight 12-month
   proposal.** The owner's "why not 3 months?" surfaced a real distinction the planner's
   original single-date proposal conflated: an EARLY VISIBILITY check is not the same thing
   as a RETIREMENT decision, and 3 months is too short for the latter (at `MIN_CALLS=8` with
   a ~30-trading-day-per-event maturation window, reaching 8 matured events in 3 months
   would require the underlying condition — elevated beta, a diversification gap — to fire
   roughly twice a week, far more often than these calls plausibly do; for comparison the
   near-daily-triggered Gate Suppression Ledger itself is still mostly "building" after ~3
   weeks live). Ratified as TWO separate dates:
   - **3 months after first `rec_events` capture: a SANITY-CHECK checkpoint only** — no
     retire/keep decision. Just confirms rec_events are actually accumulating (catches a
     broken capture pipeline early; zero rows at 3 months is a bug signal, not a retirement
     signal).
   - **12 months after first `rec_events` capture: the real retirement decision**, unchanged
     from the planner's proposal — if no rec_type has produced an evaluable verdict
     distinguishable from zero by then, that type's readout is retired (a success condition
     of the criterion, not a failure — matches the Gate Suppression Ledger's own precedent,
     not to be softened later to save the feature).
   - The harvest running-total and the Phase-1a metric-trend chart remain exempt from both
     dates (immediate, non-banded, nothing to retire).

**All 4 decisions ratified — nothing left blocking a build.** Next step, if picked up: hand
this doc (now complete with the ratified §11) to `implementer` for Phase 1a, remembering
every touched file among `db.py`/`cron_runner.py`/`system_health.py`/`app.py`'s
`trigger_type` sites/`tax_advisor.py` needs its own Opus `reviewer` pass before commit (all
are `_GATE_FILES` or DB-write paths per Hard Rule #4), and the DDL for `portfolio_risk_snapshots`
will need the owner to apply it by hand in Supabase before the EOD cron can write to it
(same pattern as F-266's `account_daily_snapshots`).
