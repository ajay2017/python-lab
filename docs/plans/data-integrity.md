# DRISHTA — Data Accuracy & Integrity: ranked findings register

**Date:** 2026-09-13
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 5 (findings verified at HEAD; design pass by the `planner` agent, Opus 5)
**Status (2026-09-13, Band A — 3 of 6 done):** **D19 half-closed** (`a4e2e41`). New pure
`util.pillar_tile()` withholds a pillar's score outright when its availability flag is
false, following `quick_research.py:222-232`'s "Verdict withheld" precedent, and stops the
caption asserting inputs that never contributed. **Business Quality and Valuation are
done; Technical and Sentiment remain blocked on D3** — they have no `t_available` /
`s_available` to read, which is now the concrete, user-visible thing the spine unblocks.
10 tests; full suite **5403 passed**. **The withheld branch is test-covered but UNSEEN** —
it needs a holding with neither `forward_pe` nor `fcf_yield`, and all 16 current holdings
have at least one (Q1c), so it cannot be screenshot-verified today. Track it unverified,
as F-204a tracks its Act Today rows. **Band A remaining: D8, D23, the `exit_signals`
backfill, and D19's D3-blocked half.**

**Status (2026-09-13, Band A started):** **D20 and D17 FIXED.** D20 shipped as `81c45d4` —
new pure `util.numeric_or()` (deliberate sibling of `get_or_offline`, same falsy-collapse
class) replacing `or 50` at `app.py`'s "What would change this signal?" block, with 13 tests
including a regression that pins the corrupted upgrade-trigger arithmetic rather than just
describing it. D17 corrected `docs/architecture.md` §6.38 against the authoritative DDL at
`db.py:283-287`. Full suite **5393 passed**; antipattern + constants-doc gates green. No
reviewer citation on either — no `_GATE_FILES` member touched, no constant, gate, or
scoring change. **Band A remaining: D19 (2 of 4 tiles fixable today), D8, D23, and the
`exit_signals` price backfill.**

**Status (2026-09-13, later same day):** **STEP 0 COMPLETE — ~18 read-only queries run
against production.** Headline: **the stored data is in good shape.** Seven findings closed
or retracted by measurement (D3, D7, D9, D10, D14, D15, D21); three demoted to latent
(D5, D6, D13). **Five confirmed live defects remain — none of them in the data** (D8, D17,
D19, D20, D23); they live in render paths, a writer, and the docs. Phase B of the Analyst
Weight Audit shipped mid-audit (`722bed8`, `7146468`) and closed the valuation-
renormalisation exposure outright. One backfill opportunity identified (32 unpriced
`exit_signals` rows). Still no code written by this plan.

**The thesis changed, and in the owner's favour.** The opening worry was "bad data may be
moving a decision." Measurement says it largely isn't. The real gap is that **establishing
that took ~18 hand-written production queries** — there is no surface that answers it. That
makes System Trust check ⑦ a *routine confirmation* instrument rather than an alarm, which
is a stronger and more sustainable justification than the one this plan started with.

**Status:** PRE-EXECUTION — register agreed, nothing built. 16 findings (D1–D16) ranked
P0–P3. Step 0 (the SQL verification pack) is the agreed first action and is expected to
re-rank six `MEASURE-FIRST` items. No code written, no constant changed, no DB row touched.

> Status convention: newer status lines are prepended ABOVE this one as work lands
> (`feedback_living_doc_status_prepended`). Read the topmost line first.

---

## Origin

The owner asked whether the app has any way to know its own data is accurate — and whether
bad data could be quietly moving a decision or producing a wrong action. Prompted by the
observation that DRISHTA has accumulated many features and capabilities without a
corresponding way to trust their inputs.

DRISHTA already has unusual self-monitoring: 🩺 System Trust (6 checks), the outage gate,
`coord_freshness`, the price cross-check, `reference_shelf`, `ticker_liveness`,
`broker_sync.decide_drift_banner`, and the `check_antipatterns.py` recurring-defect gate.
A read of all of it produces one conclusion, which is the thesis of this document:

> **Every existing check grades the PLUMBING — is the cron alive, is the provider
> answering, does the table exist, is the cache present. Nothing grades the PAYLOAD —
> whether the numbers the engine actually decided on today were real.**

This is distinct from, and not a re-run of, the 2026-08-30 data-integrity audit
(`project_data_integrity_audit_2026_08`), which verified that *rows are being written*.
This register asks whether the *values in them* are real.

### Owner decisions taken up-front (2026-09-13)

| Decision | Choice |
|---|---|
| DB access for verification | Claude writes read-only SQL; **owner runs it and pastes results back**. No credentials to Claude — same boundary as the 2026-08-30 audit. |
| Risk focus | Live market/fundamental data · captured history · cross-surface consistency. **The stored trade ledger is deprioritised** (see "Explicitly not doing"). |
| Gate policy when provenance is bad | **Disclose everywhere; withhold the BUY side only.** Protective EXIT/TRIM calls must never be silenced by degraded data. |
| Outcome shape | Both — a verified findings list with a cleanup queue, AND a standing in-app surface. |
| Sequencing | **SQL pack first.** |

---

## How this is ranked

The repo's own criterion, inherited from the 2026-08-30 audit: *would a silent failure here
produce a wrong DECISION, versus just a missing awareness-only data point?* Applied on two
axes.

**Priority — what is at stake**

| | |
|---|---|
| **P0** | Can produce a wrong **action** — a trade the owner would take, or an alert they would act on — and either fires systematically or has unbounded blast radius. |
| **P1** | Produces a wrong **number on a decision surface**. Bounded magnitude, or reachability not yet proven. |
| **P2** | Corrupts **self-assessment** — the engine grades itself on bad substrate. No immediate wrong action, but it corrupts the evidence behind bigger calls. |
| **P3** | **Latent or hygiene** — structurally wrong but not currently reachable, or display-only. |

**Urgency — when to act**

`NOW` · `MEASURE-FIRST` (the SQL pack sets its real rank) · `NEXT` (after the P0 batch) ·
`WHEN-TOUCHED` (fold into the next commit touching that file)

**Honest caveat on the ranking.** Six items are `MEASURE-FIRST` because their true priority
depends on a frequency nobody has measured. D3's rank in particular swings on one number —
how often the stale-bundle path actually fires. If weekly, it is the top item here; if
twice a year, it is hygiene. **Step 0 exists to re-rank this register, and is expected to
move things.** Do not treat the order below as settled before Step 0 runs.

---

## Step 0 — The SQL verification pack (always first)

Deliverable: `docs/sql/data-integrity-audit.sql` — numbered read-only queries the owner runs
in Supabase and pastes back. **Every column name transcribed from `docs/architecture.md`
§6.x at write time, none from memory**, per this repo's zero-hallucination standard.

| Q | Measures | Re-ranks |
|---|---|---|
| 1 | `bundle_cache.fetched_at` age distribution vs `BUNDLE_CACHE_MAX_AGE_DAYS = 5`; `fundamentals_cache` age vs `GROW_TODAY_MAX_FUND_AGE_DAYS = 2` | **D3** — the single most important number in this document |
| 2 | `recommendations` rows with NULL `composite_score`/`t_score`/`bq_score`/`val_score`; `surfaced_at` time-of-day distribution | **D8** |
| 3 | `analyst_coverage` duplicate groups (ticker + article_date + publisher) | **D9** |
| 4 | `exit_signals` per-ticker runs of identical nullable values across consecutive `signal_date`s | **D7** |
| 5 | `price_xcheck_history` frequency and ticker concentration of failed prev-close checks | **D11** |
| 6 | `account_flows` duplicates and the resulting NCC overstatement | **D10** |
| 7 | Orphans — tickers in `manual_stops`/`recommendations`/`exit_signals`/`analyst_coverage` present in neither `holdings` nor `trades` | **D14** |
| 8 | `trades` rows with NULL `price`; `holdings` tickers absent from `trades` and vice versa | **D15, D5** |
| 9 | `daily_snapshots` coverage vs expected NYSE sessions (reuse `attribution_readiness.snapshot_coverage()`) | context |

**Nothing is written to the DB without the owner approving the exact statement.**

---

## Step 0 results (2026-09-13, COMPLETE)

Run read-only against production by the owner; interpreted here. **The measurement changed
the register substantially**, which is what it was for.

### Scoreboard

| Outcome | Findings |
|---|---|
| **Closed / retracted by measurement** | D3, D7, D9, D10, D14, D15, D21 |
| **Demoted to latent** (real in code, never fired) | D5, D6, D13 |
| **Confirmed live** | D8, D17, D19, D20, D23 |
| **Not testable by SQL** (code-level, still open) | D1, D2, D4, D12, D16 |
| **Informational** | D11, D18 |

### What the data said

| Check | Result |
|---|---|
| Held bundles + fundamentals (Q1a-2, Q1b) | All 16 holdings fresh, loaded 09:30 ET at market open. Zero fallbacks. |
| Fundamentals depth (Q1c) | Worst case 3 of 4 BQ metrics. `⚠ Data Quality` never fires. 15 of 16 have both `forward_pe` and `fcf_yield`. |
| `analyst_coverage` dupes (Q3, Q3b) | **Not duplicates** — different firms per row. D9 retracted. |
| `exit_signals` coalesce (Q4) | Behaving. `distinct_value_sets` tracks `rows`. D7 closed. |
| `exit_signals` prices | 32 rows unpriced, all 2026-07-18 → 08-04, fixed by `bd48079` (2026-08-05). |
| Price cross-check (Q5) | 7 failures in 783 checks over 51 days. 2 genuine prev-close breaches. |
| `account_flows` (Q6) | No duplicates. D10 closed. |
| Orphans (Q7) | `manual_stops` 0, `exit_signals` 0. D14 closed. |
| Trades hygiene (Q8a) | 256 trades, zero NULL/non-positive. D15 closed. |
| **Ledger agreement (Q8c)** | **`holdings` matches the `trades` replay exactly, all 70 tickers, zero drift.** No SPLIT rows exist. |
| `account_cash` | `-13533.79`, present and correctly signed. D6 latent. |
| `daily_snapshots` | 63 days, 2026-06-09 → 09-11 (the last trading day). ~95% coverage. |
| Cron heartbeats | All 7 lanes healthy and on schedule. |

### PROCESS NOTE — three self-inflicted false positives, same root cause

Recorded because this audit's own subject is "don't trust unverified inputs," and the
auditor broke that rule three times:

1. **`md5(COALESCE(raw_text,''))`** — two NULLs both hash to `md5('')`, so the test reported
   "identical" for "both absent." Briefly confirmed D9 on it. `COALESCE(x,'')` destroys the
   null/identical distinction; never use it to prove equality.
2. **An "era 2 = since 07-25" boundary** split one bounded 07-28→08-04 gap across two
   buckets, making a closed defect look ongoing. Declared D21 "confirmed live"; it wasn't.
3. **Assumed 2026-09-12 was a Friday** (it was a Saturday) and declared a missing EOD
   session, nearly escalating a healthy cron fleet as a live incident.

Common cause: **derived facts treated as verified facts.** Constants and schema were
verified rigorously from source; dates, weekday arithmetic and self-chosen bucket
boundaries were not. Both deserve the same discipline — and #3 in particular is why
`market_time` exists and should have been consulted instead of mental arithmetic.

### D3 — measured CLEAN on the live book. Demoted.

| Query | Result |
|---|---|
| Q1a (all 305 cached tickers) | Too coarse to answer — `bundle_cache` holds every ticker ever loaded, so an old `fetched_at` conflates "fetch failed" with "nobody asked". Superseded by Q1a-2. |
| Q1a-2 (held only) | **All 16 holdings fresh**, age 0.01d, loaded 13:30:37–13:30:49 UTC (09:30 ET market-open refresh). Zero fallbacks. Sorted worst-first, so this holds even if rows were truncated. |
| Q1b (held fundamentals) | **All 16 fresh**, same timestamps. |
| Q1c (depth, not recency) | **BQ healthy** — worst case 3 of 4 core metrics (BE, CRWD, DELL, SPCX); twelve names have all four. The `⚠ Data Quality` signal (fires at ≥3 missing) never triggers on this book, so `FUNDAMENTALS_GATE_MIN_METRICS = 1` is **latent, not active**. Valuation renormalisation essentially absent — 15 of 16 have both `forward_pe` and `fcf_yield`; ORCL's −10.59 `fcf_yield` is correctly *present* and scored 0, not excluded. |

**Conclusion:** the fabricated-pillar path is not firing. D3 moves from "P0, possibly
systematic" to **P1, build for observability**. The argument for the spine survives and
changes character: it took hand-written SQL against production to establish this, and there
is no surface that tells the owner it. That is now a *positive* case for check ⑦ — confirm
health in five seconds instead of an audit.

**Structural limit, stated so it is not over-read:** `bundle_cache` records only the LAST
success. A failure leaves no trace. So this proves "not firing now"; it cannot establish
historical frequency, and no table in the schema records it. D3's frequency remains
genuinely unmeasured.

### D9 — RETRACTED. Not a defect.

Q3 found 6 "excess" rows in 653. Q3b appeared to confirm them as true duplicates via
`md5(COALESCE(raw_text,''))` — **that test was broken**: two NULL `raw_text` values both
hash to `md5('')` and report as identical. Re-run with `COUNT(raw_text)` and
`COUNT(DISTINCT analysts::text)` showed `raw_text` genuinely present and identical on both
rows, but **different analyst payloads** — CRCL id 309 is TD Cowen/Buy/$82, id 324 is
Morgan Stanley/Underweight/$38. Different firms, not duplicates.

Mechanism: `extract_report()` returns `list[dict]`, one record per stock
(`analyst_intel.py:83-94`), and `save_analyst_coverage` writes one row each. The 6 cases are
one extraction emitting two records for the same stock, split by firm, written <1s apart.
Scoring is unaffected — `bundle_loader.py:129-141` pools `analysts` across all fresh rows
before `derive_consensus`, so the firms correctly recombine.

**`analyst_coverage` having no dedup key is therefore defensible.** D9 is closed.

**Process note worth keeping:** this is the `feedback_confident_negative_from_weak_check`
class, committed inside an integrity audit. A test that cannot distinguish the thing it is
testing for must not be read as confirmation. The `COALESCE(x,'')` idiom specifically
destroys the null/identical distinction — do not use it to prove equality.

### Phase B reconciliation (`722bed8`, `7146468`, shipped mid-audit)

The Analyst Weight Audit's Phase B landed while this audit was running and **closed the
valuation-renormalisation exposure this plan had flagged**. `val_available` now requires at
least one OBJECTIVE metric (Forward P/E or FCF Yield) via a new `objective_max_points`
accumulator, so an analyst-only ticker is WITHHELD rather than renormalised to 100%
sell-side opinion. `VALUATION_CONSENSUS_PTS` also compressed (Strong Buy 30→15 etc.), and
the consensus leg's `max_points` now reads `max(VALUATION_CONSENSUS_PTS.values())` instead
of a hardcoded `+= 30`.

Consequences for this register:
- The "analyst weight silently doubles to 30%" concern is **dead** — fixed independently.
- Full-data pillar max is now 85, so the consensus leg is ~5.3% of composite (was ~9.0%).
- SPCX (has `forward_pe`, lacks `fcf_yield`) sits at 40/65 ≈ 18.5% analyst-derived — still
  elevated over the ~14% baseline, but it retains its verdict correctly.
- **D18 is explained and accepted**, not open — Phase B's decision D4 independently verified
  the single-firm mechanism and the owner chose to leave it.
- **Phase B's reviewer handed over a live finding** which becomes D19 below, and it is the
  same defect class this plan's D3 exists to close.

---

## The register

| ID | Finding | P | Urgency | Effort |
|---|---|---|---|---|
Ordered by **evidence**, not by theory. `FIRING` = observed in production this session.
`latent` = real in code, measured not to be occurring. That distinction is the whole
product of Step 0 and should drive execution order.

### Band A — FIRING NOW. Fix these.

**Shipped 2026-09-13** — `81c45d4` D20 · `47c4a54` D17 · `a4e2e41` D19 (2 of 4 tiles) ·
`f1ecc85` D8 (`enter_now`, both writers) · `5943a1c` D24 (all 4 rec_type writers) · D23
(pending commit, this pass). Full suite 5431 passing throughout; D23's addition brings it
to 5432.

| ID | Finding | P | Effort | State |
|---|---|---|---|---|
| ~~**D20**~~ | `or 50` inverted a legitimate 0 pillar score to neutral | P1 | S | **DONE** `81c45d4` |
| ~~**D17**~~ | §6.38 misdocumented `fundamentals_cache` | P2 | XS | **DONE** `47c4a54` |
| **D19** | 4 pillar tiles render possibly-fabricated `N/100` under an asserting caption | P1 | S | **HALF** `a4e2e41` — BQ + Valuation done; Technical + Sentiment **blocked on D3** |
| **D8** | Pillar columns on `recommendations` rows | P2 | M | **HALF** `f1ecc85` — `enter_now` done; `new_pick` 25% **reclassified, needs `planner`** |
| ~~**D24**~~ | A fabricated neutral pillar persists into `recommendations` as if measured | P2 | S | **DONE** `5943a1c` — all 4 rec_type writers, one commit |
| ~~**D23**~~ | Live-leg cross-check fires outside regular trading hours → alarm fatigue | P2 | S | **DONE** — both consumer sites, one commit |
| ~~—~~ | **Backfill**: 32 `exit_signals` rows unpriced 07-18→08-04 | P2 | S | **DONE** — 21 of 32 priced (13 via `daily_snapshots` join, 8 via a one-time historical fetch); 11 remain, none blocking |
| **D25** | `exit_signals.signal_date` recorded a Saturday/Sunday for 3 tickers on capture's first two days | P3 | — | **NEW, unfixed** — needs its own look at the capture site |

### Band B — real in code, measured NOT firing. Fix on consequence, not urgency.

| ID | Finding | P | Why it still matters |
|---|---|---|---|
| **D1** | Split detection absent from the protective-alert cron lane | P0 | Only finding that can email an EXIT on a healthy position. **Zero SPLIT rows exist**, so the path is entirely unexercised — neither exposed nor validated. Needs a `planner` pass. |
| **D2** | Holdings save reports success before the write, return value discarded | P0 | Ledger measured perfectly consistent (Q8c), so it has not fired — but absence of evidence is weak here, since it is only visible when a write fails. |
| **D4** | Silently-dropped holding inflates every remaining weight | P0 | All 16 holdings priced today. Fires only on degraded-data days — correlated with D3, which is also currently quiet. |
| **D3** | Composite carries no provenance; Technical + Sentiment pillars unflagged | P1 | Measured clean. Build for **observability**, and because D19's Technical/Sentiment halves are blocked on it. |
| **D12** | Price cross-check has zero test coverage | P2 | The integrity check is itself unverified. Cheap. |
| **D16** | Nothing checks two surfaces rendering the same fact agree | P3 | Still a manual screenshot check (F-204a). |
| **D5** | Fractional first buy truncates to 0 | P3 ↓ | No evidence of ever firing; no fractional first position exists. |
| **D6** | NULL `cash_balance` reads as "no margin debit" | P3 ↓ | Value present and correctly signed (`-13533.79`). |
| **D13** | Missing `Score` defaults to `50.0` vs `0` in two modules | P3 | Not reachable from `port_df`. Hygiene. |

### D8 · outcome — `enter_now` fixed; `new_pick` reclassified
**`enter_now` (was 100% NULL) — FIXED.** `build_enter_now_rows` gained an optional
`bundles_by_ticker` and now emits the same five pillar columns the `new_pick` writer has
since 2026-08-01, with identical lookup semantics so the two rec_types cannot drift.

**The load-bearing part was the SECOND caller.** `headless_alert_engine.py` (the F-265
scan lane) was missed on the first pass, and `db.save_recommendations` upserts
`ON CONFLICT DO NOTHING` with **no UPDATE path anywhere in `db.py`** — so the FIRST writer
of a `(ticker, rec_date, 'enter_now')` key wins permanently. That lane fires unattended
daily while the app path needs a 📋 Watchlist visit, so fixing only the app side would have
been a near no-op in production while reporting success. Caught by Opus review, not by the
author or by the 5 pure-function tests.

**`new_pick` (25%) is NOT a writer bug — reclassified, not fixed.** `_t_score_for`
(`app.py` ~5790) looks the ticker up in `_grow_comp_cache`, then `_held_data_cache`, and
returns `None` when it is in neither. So the composite comes from a lightweight cached path
while the pillars need the full bundle in hand. The sharper question is therefore not "why
is the log thin" but **"what was the pick based on"** — those picks may have been *made*
with less loaded than the ones carrying pillars. Needs a `planner` pass, and it is
D3-adjacent: provenance is exactly what would make it visible. **Do not "fix" it by
back-filling a value the engine never had.**

### D24 · A fabricated neutral pillar persists into `recommendations` as if measured
Surfaced by the Opus reviewer during D8 (2026-09-13), deliberately NOT fixed there.

When `val_available` is false, the bundle still carries `valuation.py`'s fabricated
neutral **50**, and `avg_sent` is **0.0** on zero headlines (`sentiment.py:36`). Both
writers — `new_pick` (`app.py` ~5849-5853) and now `enter_now` — persist those values with
no marker, so F-225 Portfolio Q&A's rec-outcome "why" answers will state "valuation 50" for
names where valuation was never measurable.

**Why it was not fixed inside D8:** the `enter_now` writer faithfully mirrors the
pre-existing `new_pick` writer, and correcting only one would *create* the cross-rec_type
drift that D8 exists to remove. Both must change in one commit.

**The rule already exists in this repo** — CLAUDE.md's queued score-history-capture design
states it outright: persist `val_score`/`bq_score` as *"NULL, never the fabricated neutral
50 the pillar returns when unavailable, or the history is silently poisoned with readings
that never happened."* This is that same rule, applied one table over.

`bq_available` / `val_available` travel in the same bundle, so the fix is mechanical. It is
squarely D3's class: the composite's provenance is not carried, so the persisted row cannot
record that it was built on a fabrication. **P2** — corrupts self-assessment, no wrong
action.

**FIXED 2026-09-13.** Three new pure functions in `stock_analyzer/util.py` —
`bq_score_or_none`, `val_score_or_none`, `sentiment_value_or_none` — each return the value
unchanged if the bundle's own flag says it was measured, else `None`. Applied at all four
rec_type writers, not two: `new_pick`/`add_winner`/`buy_candidate` turned out to share the
same four lookup closures in app.py, so one edit closed three rec_types; `enter_now`'s two
callers (app-side and the F-265 cron lane) both needed the fix, since the cron path narrows
its bundle to six pillar keys (D8) and had to be widened to also carry `bq_available` /
`val_available` / a precomputed `sentiment_available` bool. **`t_score` is deliberately
untouched everywhere** — no `t_available` flag exists yet (D3), and inferring fabrication
from the *value* rather than a flag is exactly the pattern this whole effort avoids.

**A real design question surfaced mid-implementation, not just a test fixture bug.**
`sentiment_value_or_none`'s first version failed *closed* when a bundle carried neither
`sentiment_available` nor `headlines` at all — inconsistent with its two siblings, which
fail *open* on a missing flag (this codebase's established convention for a legacy bundle
shape). Two pre-existing D8 tests caught it. Fixed to a three-tier priority: an explicit
`sentiment_available` wins if present, else derive from `headlines` if present, else fail
open. Opus review confirmed the fail-open tier cannot fire on live data — every real bundle
source traces to `bundle_loader.load_bundle`, whose return literal unconditionally carries
`headlines` (even as `[]`), so the ambiguous branch only reaches a hand-built or legacy
partial bundle.

Opus review: **SHIP, 0 blocking.** Also confirmed no fifth un-sanitized writer exists —
`cron_runner.py`'s `_build_new_pick_rows` already omits all pillar columns, so it was never
a gap. Full suite 5431 passed throughout; both deterministic gates green. **Known residual,
flagged rather than hidden:** the app.py wiring (that `_src` correctly tracks which bundle
answered) has no automated test — app.py has none by design. The sanitization decision
itself is fully unit-tested; the reviewer traced the wiring manually rather than the tests
proving it.

### Band C — closed, retracted, or informational

| ID | Outcome |
|---|---|
| ~~**D9**~~ | **RETRACTED** — rows are different firms, not duplicates. No dedup key needed. |
| ~~**D7**~~ | **CLOSED** — the coalesce *is* the fix (`bd48079`), not the bug. Measured behaving. |
| ~~**D21**~~ | **CLOSED** — unpriced `exit_signals` fixed 2026-08-05. Residual = the backfill in Band A. |
| ~~**D10**~~ | **CLOSED** — no `account_flows` duplicates. Annotate the stale manual-follow-up note at `db.py:827-836`. |
| ~~**D14**~~ | **CLOSED** — zero orphans in `manual_stops` and `exit_signals`. No FKs, but no drift. |
| ~~**D15**~~ | **CLOSED** — DDL forbids it; 256 trades, zero violations. |
| **D11** | **Informational** — 7 failures / 783 checks / 51 days; 2 genuine prev-close breaches. Gate-wiring is marginal at that rate. **D23 (below) fixed the display-side symptom this reading exposed**; wiring the check into an actual gate remains a separate, larger, undecided step. |
| **D18** | **Informational** — per-row `consensus_rating` is sometimes single-firm. Scoring pools correctly. Phase B decision D4 accepted it. |

### D23 · Live-leg cross-check fired outside regular trading hours — FIXED
**Anchor:** `app.py`'s two cross-check consumer sites (🏠 Home's `_alert_ph_xcheck` block;
📈 Analysis's per-analysed-ticker block), both grouping ANY `not ok` result — prev-close
*or* live-leg — into the same loud `st.error`/`st.warning` "sources disagree" banner.

Of 5 live-leg-only breaches recorded in the first 51 days (Step 0, Q5b), **4 fired outside
09:30-16:00 ET** — three at ~08:41 ET premarket, one at ~17:35 ET after-hours. Outside
regular hours two independent price sources can legitimately quote different things (a
real-time pre/post-market tick from one venue vs a delayed or stale last-regular-session
read from the other), so a live-leg-only gap there is an artifact of comparing
non-comparable reads, not a genuine fault — but it triggered the identical severe banner as
a settled prev-close disagreement, which *is* always a real fault. Net effect: alarm
fatigue on the one banner in the app that exists to say "don't trust this price."

**FIXED 2026-09-13.** New pure `stock_analyzer/util.py::xcheck_is_alarm_worthy(result,
market_is_open)`: a prev-close breach always alarms, any hour; a live-leg-only breach
alarms only when `market_is_open`, else renders as a quiet disclosure instead of being
dropped. `market_is_open` comes from the **existing** `data.market_status()["is_open"]` —
the app's one NYSE-hours boundary, holiday/early-close/weekend-aware — so this introduces
**no new threshold or constant**. Applied at both consumer sites, which turned out to share
near-identical logic; the Analysis page's positive "✓ agree within tolerance" caption was
also corrected to gate on *both* the alarm and quiet buckets being empty — previously a
ticker with a real quiet-only gap was told it agreed within tolerance, an accidental
false-clean the restructuring closes as a side effect.

**Explicitly unchanged:** the write to `price_xcheck_history` (`ok`) is untouched — this is
a display-only decision, so the audit trail Q5/Q5b read from stays interpretable exactly as
measured. `_xc_bad_prev_tickers` (Day Shock exclusion + 8 other display sites) was already
scoped to `prev_ok is False` only, so it was never affected by this class of false alarm.

Opus review: **SHIP, 0 blocking.** Confirmed the one direction that must never happen is
structurally impossible — a prev-close fault can't be quieted (unconditional first branch),
and a live fault during regular hours can't be quieted either; only the exact artifact class
this finding targets is softened, and even that is disclosed, never dropped. Also confirmed
`market_status()`'s calendar-staleness edge (past `MARKET_CALENDAR_LAST_YEAR`) fails toward
`is_open=True` — the safe, over-alarming direction. 15 new tests.

### exit_signals backfill — outcome (`docs/sql/exit-signals-backfill.sql`)
Of 32 rows with NULL `price_at_signal`, **21 are now priced, 11 correctly remain NULL.**

- **13 rows** — `daily_snapshots` already had the exact `(ticker, signal_date)` close;
  a straight `UPDATE ... FROM daily_snapshots` join, no external fetch.
- **8 rows** — a genuine gap: 5 tickers (AMD, FSLR, ISRG, NOW, TEAM) had confirmed **zero**
  priced EXIT/TRIM row anywhere in the table, verified before writing anything (a
  per-ticker check, not an assumption). Prices fetched via this repo's own
  `providers.orchestrator.get_historical_close` (the same yfinance→FMP chain
  `scripts/backfill_analyst_prices.py` uses), each date-to-close mapping visually
  confirmed against a wider dated window before writing — `yf.download`'s `end` is
  exclusive, so an initial same-day window silently returned nothing; caught before any
  number was used, not after.
- **11 remain NULL, and all 11 are accounted for:** 6 are genuinely un-fixable (D25,
  below); 5 (LLY ×2, MU, TSLA, PLTR) were already confirmed not to matter — LLY/MU/TSLA
  each have a *later* priced EXIT/TRIM for the same ticker elsewhere in the table, so
  they were never blocking; PLTR's remaining row is WATCH-type, which
  `protective_track_record.py` drops entirely regardless of price.

### D1 · split detection absent from the protective-alert cron lane — Commit 1 SHIPPED
**Anchor:** `stock_analyzer/exit_advisor.classify_deterioration_tier`'s `escalate` leg
(`price < avg_cost`). Both the interactive Home render and the headless cron lane call the
exact same `daily_briefing.deterioration_signals()`, which was entirely split-blind.

**Design pass:** an Opus `planner` pass, given the mechanism and every relevant file:line,
returned verdict **PROCEED** with option (a) — withhold-and-disclose, never auto-write a
correction unattended. Two load-bearing facts the planner verified before recommending
anything:

- **The blast radius is narrower than first framed.** `dd_from_peak_pct`/`trend_broken_now`
  (feeding `deep_exit`) come purely from the price-history series, which yfinance
  auto-adjusts for splits (confirmed against the installed yfinance 1.3.0's actual
  default). So a split can **never fabricate a signal from nothing** — it can only escalate
  an *already-real, price-history-legitimate* TRIM into an unwarranted EXIT, and only on a
  forward split (a reverse split makes the escalation conditions false, not true).
- **No new constant needed.** `detect_split_adjustment`'s existing 35%-distortion pre-filter
  plus its 60%-validation check are already a high-confidence gate; "detected" doesn't need
  a second "how sure" threshold.

**Owner decision (confirmed via AskUserQuestion, 2026-09-13):** withhold **every** tier for
a flagged ticker, not only the EXIT escalation — every dollar figure a directive would
render (P&L, dollar-risk) is built on the same uncorrected `avg_cost`, so even a
split-safe WATCH/TRIM would still print wrong numbers. This means a genuinely real signal
on that one ticker can go silent until the split is corrected on Home — accepted,
self-healing, and disclosed rather than hidden.

**Commit 1 (shipped) — the cron path:**
- New pure `split_detector.split_withheld_message(tickers)` — disclosure text, names the
  ticker(s), points at Home's "Apply Adjustment" as the recovery path.
- `daily_briefing.deterioration_signals(..., *, split_flagged: set[str] | None = None)` —
  a flagged ticker's payload is withheld entirely, before `exit_advisor.assess_holding` is
  ever called for it. Default `None` reproduces old behaviour exactly.
- `headless_alert_engine.py`: per-ticker `detect_split_adjustment` off `port_df`'s own
  columns (`Ticker`/`Shares`/`Avg Cost`/`Price`) — **deliberately not**
  `detect_portfolio_splits()`, which expects the raw `holdings_df` shape
  (`"Avg Cost ($)"`, a different frame). Returns a new `"split_withheld"` key.

**A real gap in the planner's own design brief, found and closed during implementation,
not left for review to catch:** `notify.render_alert_email` requires `alerts` or
`velocity_alerts` non-empty to be called at all, and the original design routed the
disclosure only into a log-only `errors` list. A split-withheld-only day would have hit
`cron_runner.py`'s existing "nothing to act on — no email" gate and produced **complete
silence** — no email, no disclosure — on exactly the day this finding exists to guarantee
one. Fixed by extending `render_alert_email` with a third, neutrally-styled
(gray, not alarming — a data-integrity disclosure, not a protective action) section and
subject-tier, and extending `cron_runner.py`'s send-gate, fingerprint, and dedup-save
condition to a three-way `alerts / velocity_alerts / split_withheld` check, consistently.

Opus review: **SHIP, 0 blocking**, with three non-blocking findings, two closed before
commit:
- **A real correction to my own scope, not the reviewer's invention.** I had described the
  interactive-path follow-up as "patch `app.py:5349` and `app.py:9065`" — but
  `daily_briefing.build_daily_briefing` (`daily_briefing.py:2891`) is a **third**, unguarded
  caller of `deterioration_signals`, and it's the *primary* source of interactive Home's
  own Act Today surface (reached via `app.py:5308` and `app.py:5637` — more central than the
  two direct calls I'd named). **The still-open follow-up must thread `split_flagged`
  through `build_daily_briefing` itself**, not just the two smaller call sites, or Home's
  main Act Today keeps surfacing false EXITs while the follow-up reads as done.
- **Closed before commit:** no test exercised the actual column names inside
  `headless_alert_engine.py`'s new loop — every existing test mocked `deterioration_signals`
  wholesale, so a `"Avg Cost ($)"`-style typo would have silently defeated the entire fix
  with zero failing tests. Closed with 4 new integration tests that mock
  `split_detector.detect_split_adjustment` directly instead, and verified by *actually
  injecting that exact typo* and confirming 3 of the 4 fail — not just reasoning that they
  would.
- **Closed before commit:** the yfinance-outage fail-open direction (a real split
  coinciding with a provider outage passes unguarded) is accepted and consistent with this
  app's existing provider-outage posture elsewhere — now stated explicitly in a code
  comment rather than left as unstated tribal knowledge.

Sub-35%-distortion forward splits remain undetected (coextensive with the pre-existing
`detect_split_adjustment` primitive, not made worse here) — awareness only, not acted on.

19 new tests across `tests/test_split_detector.py`, `tests/test_deterioration_signals.py`
(new file — includes the load-bearing proof that the false EXIT is real without the guard,
and that it's absent with it, both verified against hand-computed `trim_floor`/`exit_floor`
arithmetic, not just green tests), `tests/test_notify.py`, `tests/test_cron_split_withheld.py`
(new file), and `tests/test_headless_alert_engine.py`.

**Still open, correctly scoped now:** thread `split_flagged` through `build_daily_briefing`
(not just its two direct callers) for the interactive Home path — its own commit, its own
review, since it changes what an existing decision surface outputs on a split day.

### D2 · holdings save reported success before the write — FIXED, with a real regression caught mid-fix
**Anchor:** 📒 Trade Journal's BUY-confirm and SELL-confirm handlers in `app.py`. Both called
`st.success(...)` unconditionally before `db.save_holdings(...)`, then discarded that
call's boolean return entirely.

**Sharper than the original one-line description.** `db.save_holdings` (`db.py:1346`)
already renders its own `st.error` on a genuine exception, so a hard DB failure was never
*fully* silent — the real defect was narrower and, in one respect, worse: regardless of
success or failure, `st.session_state.holdings_df` was overwritten with the new value. On
a failed write, every gate/stop/sizing computation for the **rest of that session** ran
against a book the DB never received — a silent divergence that only a future reload would
quietly revert, with nothing in between ever telling the user.

**Fix:** new pure `stock_analyzer/util.py::holdings_write_failed_message(ticker)`. Both
confirm flows now gate the success message, the `session_state.holdings_df` write, and (in
BUY's case) a ~90-line downstream concentration-caution display plus the cross-page cache
refresh — all of which assess or propagate the *new* position — behind
`db.save_holdings(...)`'s actual return value. On failure: a specific error naming the
ticker and pointing at "Rebuild from trades" (the trade itself is already logged
successfully by this point via a separately-checked `db.save_trade` call, so this is a
recoverable aggregate-cache miss, not data loss — never conflated with genuine loss in the
message).

**A real regression was caught mid-fix, by review, not by the author.** `db.save_holdings`
returns `False` for two different reasons: a genuine write failure, **and** the
app's intentional no-DB / local-session-only mode (`db.py:1356-1357`). My first version's
`if db.save_holdings(...):` gate couldn't distinguish them — in no-DB mode, an entirely
supported and exercised mode, every single trade would have shown a **false** "write
failed" error and silently stopped updating `holdings_df` at all, breaking that mode
outright. Fixed by mirroring the exact entry gate the surrounding handler already uses
(`_ps_saved or not db.has_db()` / `_pb_saved or not db.has_db()`), so
`db.save_holdings(...) or not db.has_db()` reproduces the old, correct no-DB behaviour
while still catching a genuine DB-present failure.

Opus review: **FIX-FIRST → fixed → SHIP** (the no-DB regression above was the sole blocking
finding). Two non-blocking notes, deliberately left as-is rather than expanded scope:
(1) the new error, like the `st.success` it replaces, sits immediately before an
unconditional `st.rerun()` — unverifiable locally (this repo never runs the app locally),
and not a regression since it occupies the exact position the pre-existing success message
always held; **track as unverified, check on the next live BUY/SELL after deploy**; (2)
`db.save_holdings`'s own internal `st.error` and the new caller-side message can both
render on a genuine failure — minor duplication, the caller's message is the more
actionable of the two, not worth touching `db.py`'s exception path for.

**Known residual, stated rather than hidden:** this is the largest structural edit to
`app.py` this session (a ~90-line re-indent), and app.py has zero automated coverage of
this control flow by design — the reviewer traced the diff line-by-line rather than
relying on `py_compile` clean as sufficient. The pure `holdings_write_failed_message` is
fully unit-tested; the surrounding wiring rests on that manual trace, the same residual
class D8/D24 already carry for this file.

### D4 · a silently-dropped holding inflated every remaining weight — FIXED
**Anchor:** `portfolio.build_portfolio_df` (`stock_analyzer/portfolio.py`) — the row-build
loop's second `continue` (a held ticker with valid shares/cost but no `current_price`,
because every provider failed to price it) dropped the row with **no record anywhere**,
unlike the case immediately above it (invalid shares/cost), which was already recorded.

Two consequences of the gap: the existing `dropped_holdings` banner in `app.py` never
fired for this case, so the user had no visibility that a real held position vanished from
the view; and `Weight (%)` (computed a few lines later as `Market Value / total_val * 100`,
summed only over surviving rows) silently shrank its own denominator — every remaining
holding's weight read as inflated, with nothing disclosing why. A direct breach of the
house "never silently filter" rule.

**Fix:** the no-price branch now appends to `dropped` too, tagged `"reason":
"no_price_data"` — accurate, not fabricated, since this ticker's shares/cost were already
confirmed valid by the branch above it. A new shared pure
`stock_analyzer/util.py::dropped_holdings_banner_text(dropped)` replaces two `app.py` call
sites that had **independently duplicated the identical hardcoded string** — "invalid
shares or cost basis, check the entry" — which would have been actively misleading for the
new case: an entry that's perfectly fine sent the user looking for a data-entry bug that
doesn't exist. The two reasons now get genuinely different wording, grouped correctly when
both occur in the same render; a row from an older cache with no `reason` key defaults to
`invalid_shares_or_cost`, the only reason a drop could have meant before this fix.

**Confirmed disclosure-only, not a behavior change.** This was the one thing worth being
certain of on a `_GATE_FILES` commit: `total_val`/`Weight (%)` sums only the surviving
`rows` list, never `dropped`, so the fix adds visibility without touching any actual
weight, gate, or score. A repo-wide grep confirmed exactly one producer and two readers —
no third consumer anywhere that could read the added `reason` key unexpectedly or
disagree with the new shared message.

Opus review: **SHIP, 0 blocking.** Confirmed `"no_price_data"` is an accurate label for
both sub-cases it covers (bundle missing entirely, and bundle present but with no
current_price) by tracing `bundle_loader.py`'s own contract — `current_price` is `None`
specifically to mean "no price," never a fabricated zero, so there's no real-$0 case this
label could be misapplied to. One non-blocking note: the labels map's fallback would print
a raw snake_case key if a third drop reason is ever added without updating it — harmless
today, worth a generic fallback label if that day comes. 11 new tests across
`tests/test_portfolio.py` (including one pinning that `Weight (%)` still computes
correctly with the drop now disclosed) and `tests/test_util.py`.

### D25 · `exit_signals.signal_date` can record a weekend — mechanism CONFIRMED, still live
**New finding, surfaced while preparing the backfill above — not fixed.**

FSLR, ISRG and NOW each carry a row dated **2026-07-18 (Saturday)** and **2026-07-19
(Sunday)**. The market was not open either day, so there is no real close to backfill —
any price written would be a *different* day's close mislabeled as this weekend's
`price_at_signal`, the exact fabrication this whole effort exists to prevent. Left NULL
rather than guessed at.

**First hypothesis (a UTC/ET midnight-boundary bug, the `trade_time.normalize_traded_at`
class) was checked and was WRONG — corrected rather than left standing.** The commit
that first shipped this capture (`f86147d`) was itself made on **Saturday 2026-07-18
12:09 ET**, and its code already used `_today_et()` correctly — properly ET-anchored,
no naive UTC date math, from day one.

**The real mechanism, confirmed by reading the write path:** `signal_date` records the
*calendar day the interactive session ran*, not *the trading day the underlying price
data reflects*. The owner evidently opened the app and rebuilt the Daily Brief over that
first weekend — a reasonable thing to do testing a brand-new feature — and the
deterioration signals were computed off Friday's last available close, but stamped with
the actual Saturday/Sunday date. **There is no weekday guard on the interactive
exit_signals write path today** (`app.py`, confirmed by grep) — so this is not a
one-time historical artifact confined to week one. Any weekend visit that rebuilds the
Daily Brief would reproduce the identical defect right now.

**P3, not P1/P0** — despite being live and reachable, the consequence is narrow and
non-decision-bearing: it corrupts one historical-analysis column
(`protective_track_record.py`'s alpha grading, and now this backfill), never a live
gate, recommendation, or dollar figure. Worth a small guard (skip the interactive write
when `not is_trading_day(_today_et())`, mirroring `data.is_trading_day`) next time
`app.py`'s exit_signals capture is touched — not urgent enough to justify its own
standalone commit today.

### D19 · All four pillar tiles render fabricated scores as measured
**Anchors:** `app.py:22659` (Technical), `:22677` (Business Quality), `:22701` (Valuation),
`:22747` (Sentiment).

Surfaced by the Phase B reviewer as a non-blocking note against the Valuation tile only.
Checking the class rather than the symptom found **all four** have the same shape: each
renders `**<Pillar> — N/100**` followed by a caption *asserting* the inputs, and none
consults an availability flag.

```
_dd2v_val = r.get("val_score", 50)
st.markdown(f"**Valuation — {_dd2v_val:.0f}/100**")
st.caption("P/E · FCF Yield · PT Upside · Consensus")
```

Two defects per tile: the fabricated neutral 50 renders indistinguishably from a measured
50, **and** the caption names four inputs that contributed nothing in the withhold case.
The `.get(..., 50)` default is itself `50`, so a missing key also renders as neutral.

**Phase B made this MORE reachable**, not less — `val_available=False` is now newly
reachable for analyst-only names, and this tile does not consult it.

**Split by what is fixable today:**
- **Business Quality and Valuation — fixable NOW.** `bq_available` / `val_available` already
  exist and are simply not read.
- **Technical and Sentiment — blocked on D3.** No `t_available` / `s_available` exists to
  read. This is the concrete, user-visible consumer that justifies the spine.

### D20 · `or 50` inverts a maximally-bearish pillar to neutral
**Anchor:** `app.py:21504-21508`, the Analysis page's Upgrade/Downgrade trigger block.

```
_t_sc   = r.get("t_score",  50) or 50
_bq_sc  = r.get("bq_score", r.get("f_score", 50)) or 50
_val_sc = r.get("val_score", 50) or 50
_s_sc   = r.get("s_score",  50) or 50
```

`0.0 or 50` evaluates to `50`. A genuine zero — the worst possible pillar reading — is
rewritten to the middle of the range.

**Reachable, and specifically on the names it matters most for.** `valuation_score` awards
0 pts for negative FCF yield, 0 for price above the analyst target, and 0 for a Sell
consensus; a ticker hitting all three scores exactly `0.0` and is then displayed as a
neutral `50`. Same family as `news_intelligence.py:78`'s `float(row.get("Score", 50) or 50)`.

Marked "Pure display" in its own comment, so it gates nothing — but the user reasons from
what it says about what would move the verdict, which is this register's P1 definition.
Fix is to distinguish absent from zero (`x if x is not None else 50`), not to widen the
default.

### D17 · `docs/architecture.md` §6.38 misdocuments `fundamentals_cache`
Documented with an `updated_at TIMESTAMPTZ` column that **does not exist in production**,
`fetched_at` typed `TEXT` when it is `TIMESTAMPTZ`, and `financials` nullable when it is
`NOT NULL`. Authoritative DDL is `db.py:283-287`.

Found the hard way: Q1b was transcribed from §6.38 exactly as the house rule requires and
failed with `42703 column "updated_at" does not exist`. §6 is what every future session
copies schema from, so a wrong entry silently corrupts whatever trusts it — the same
principle CLAUDE.md already codifies for constants. Docs-only, no gate, no reviewer.

### D18 · Stored `consensus_rating` is a per-row, sometimes single-firm artifact
Informational, retained so it is not rediscovered. A single article can be split across
rows by firm (6 of 653), so a row's `consensus_rating` may describe one firm rather than a
consensus — row 309 reads "Strong Buy (1 Buy / 0 Hold / 0 Sell)" and row 324 "Sell (0 Buy /
0 Hold / 1 Sell)" for the same article.

Scoring is unaffected (pooling). Phase B's decision D4 independently verified the mechanism
and deliberately left it. Recorded because it explains the documented `n=0` for the "Buy"
tier — `derive_consensus` cannot produce Buy or Mixed at `n_rated=1` — and because the
ladder-performance evidence measures these per-row labels.

---

## P0 — act first

### D1 · Split detection never runs in the protective-alert lane
**Anchor:** `split_detector` has exactly one call site, `app.py:4691-4699` (🏠 Home render).
`cron_runner.py` and `headless_alert_engine.py` never call it.

An unaccounted forward split leaves stored `avg_cost` at a multiple of live price, so
`pnl_pct` (`portfolio.py:423`) reads ≈ −90%, `gap_to_stop` and the ratchet floor mis-fire,
and `exit_advisor.classify_deterioration_tier` classifies a healthy position as EXIT.

**Why it ranks first on consequence:** this is the only finding where bad data produces an
*outbound action with no human in the loop* — the cron **emails an EXIT call** on a healthy
position. Every other finding here is a wrong value on a screen the owner is already
looking at. Rare trigger; blast radius is a sell that would not otherwise have happened.

**Why it is not in the first code batch:** the fix is a genuine design question — should the
headless lane *detect*, *suppress*, or merely *disclose* an unaccounted split? Needs its own
`planner` pass. Do not bundle it into a cleanup commit.

### D2 · Holdings save reports success before the write
**Anchor:** `app.py:24886-24890` and `24898-24901` — `st.success("… Holdings: 100 → 80
shares")` renders *before* `db.save_holdings()` is called, and the return value is discarded.

A failed write reports success, updates session state, and the next session silently reloads
the old book — after which every gate, stop and sizing cap is computed on a book the owner
believes they changed. Foundational: if this fires, nothing else in this document matters.

**Fix:** move the success message after the write and branch on the return. Extract the
outcome decision into a pure tested function (`portfolio.refresh_outcome` is the precedent)
per the "extract the DECISION" convention, since `app.py` has zero test coverage.

### D3 · The composite carries no provenance
**Anchors:** `bundle_loader.py:73-82`, `db.py:1745-1747`, `sentiment.py:36,42`,
`technicals.py:45,55,139`, `bundle_loader.py:185-186,219-220`, `portfolio.py:477`.

When all providers fail, an aged bundle is served (up to `BUNDLE_CACHE_MAX_AGE_DAYS = 5`
days) with `news: []`. Empty news → `analyze_news` returns avg `0.0` →
`sentiment_score_0_100(0.0)` = **exactly 50.0** → straight into `combined_score`. So **every
cache-served bundle silently injects a fabricated neutral sentiment pillar** — on precisely
the days when providers have failed and data is least trustworthy.

`technical_score` likewise returns `50.0` on an empty frame (`:55`) or when `max_pts == 0`
(`:139`), and there is **no `t_available` symbol anywhere in the repo**. Only
`bq_available`/`val_available` exist — so **2 of the 4 pillars are fabricable with nothing
able to detect it.**

Then the chokepoint: `build_portfolio_df` writes `"Score": r["total"]` and drops
`stale_as_of`, `fund_cache_age_days` and both availability flags. That one line is why 25+
modules — `exit_advisor`, `risk_advisor`, `rebalancer`, `watchlist_advisor`, `ranking`,
`headless_alert_engine`, `notify`, `portfolio_health`, `macro_playbook` — are
**structurally unable** to know whether the number they gate on is real.

Weights are technical `.25` / sentiment `.10` (`COMPOSITE_WEIGHTS`, `constants.py:753`)
against a hard `COMPOSITE_BUY = 65` (`constants.py:250`), so **up to 35% of a composite can
be placeholder** — worth ~7.5 and ~2.5 composite points across a gate that sizes real trades.

**This is the systematic finding.** D1 is rarer and worse per event; D3 fires on every
degraded-data day. Its final rank is set by Step 0 Q1.

### D4 · A silently-dropped holding inflates every weight
**Anchor:** `portfolio.py:415-417` — a held ticker with no `current_price` is `continue`d
**without** being appended to `dropped`, unlike the bad-shares case immediately above it
(`:411-414`) which *is* recorded.

It therefore never reaches the `dropped_holdings` banner (`:494`), and because the
`Market Value` denominator at `:482-484` shrinks, **every surviving position's `Weight (%)`
rises.** Concentration ceilings (`SINGLE_NAME_CEILING = 15.0`, sector caps) then measure
against a smaller book than is actually held — the fail-open direction. Also a direct breach
of the house "never silently filter" rule.

Fires precisely on degraded-data days, i.e. correlated with D3.

---

## P1 — wrong number on a decision surface

- **D5 · Fractional first buy truncates.** `app.py:24894` writes `int(_pb_shares)` for a
  *new* position while `:24882-24884` preserves fractions for an *existing* one; the form
  allows `min_value=0.001`. A first buy under 1 share becomes `0`, fails `db.py:1369`'s
  `shares > 0` filter, is swept away by `db.py:1381`, and leaves a `trades` row with no
  `holdings` row — invisible to every gate and stop, and surfacing as permanent broker drift.
- **D15 · NULL-price trade silently skipped from the replay.** `db.py:2430-2434` — unlike
  the over-sell (`:2490-2496`) and no-prior-BUY (`:2501-2504`) cases, which emit warnings,
  this one just `continue`s. Cost basis is silently wrong after any rebuild, with no signal.
- **D6 · NULL cash reads as no margin debit.** `db.py:4269` coerces a NULL `cash_balance`
  to `0.0`; cash is signed and **negative = margin debit** (architecture §6.7). "Unknown"
  therefore reads as "no debit", loosening the net-capital sizing cap. Narrow trigger (row
  present, column NULL), but the wrong direction.
- **D7 · `exit_signals` coalesce-on-write.** `db.py:3011`, `3058-3076` backfills an incoming
  `None` from the stored non-null across `_EXIT_SIGNAL_NULLABLE_COLS`. A value that is
  *legitimately* null now retains and re-persists the stale prior value as if current.
  Deliberate by design; the hazard is the legitimately-null case.

---

## P2 — corrupts self-assessment

- **D8 · Recommendations last-resort floor.** `save_recommendations`' final fallback strips
  all optional columns and retries, returning `saved=N, error=None` (`db.py:2796-2798`).
  Scores and sizing can be silently absent from rows the grader later reads as evidence.
- **D9 · `analyst_coverage` has no dedup key.** Append-only insert at `db.py:2113`; a re-run
  double-counts. This feeds the Research Scorecard whose n=442 / n=653 figures are being
  used to decide on a ~$299/yr CNBC Pro renewal (`project_analyst_coverage`) — so a wrong n
  here has a real external cost.
- **D12 · The cross-check is untested.** `orchestrator.crosscheck_price` / `crosscheck_batch`
  / `_compare` / `divergence_widened` have no dedicated test file. The one genuine integrity
  check in the data layer is itself unverified. Cheap; close it in the first batch.
- **D11 · The cross-check is display-only.** No gate, score or recommendation reads its
  verdict, and `crosscheck_batch` returns `{}` entirely when the validator is RED
  (`orchestrator.py:425-426`) — i.e. it switches off exactly when data is most suspect.
  Note the primitive *works*: it correctly suppresses Day Shock and 8 display sites via
  `_xc_bad_prev_tickers`. It simply is not wired to any decision.
- **D10 · `account_flows` duplicates.** NULL `snaptrade_txn_id` rows are not matched by the
  partial unique index; `db.py:827-836` records that `net_contributed_capital` **stays
  inflated** until manually deleted. Scope-checked: architecture §6.8 states this table is
  **display-only and feeds no gate** — a wrong number that is read, not a wrong decision.

---

## P3 — latent / hygiene

- **D14 · No foreign keys anywhere.** Every cross-table link is a bare ticker string.
  `manual_stops` is the only table with orphan cleanup (`db.py:1392-1398`), and that sweep
  sits inside a bare `except: pass`.
- **D13 · Contradictory defaults.** A missing `Score` defaults to `50.0` in
  `macro_playbook.py:318,354` and `0` in `portfolio_health.py:538`. **Not confirmed
  reachable** — `portfolio.py:477` always populates `Score` — so this is latent
  inconsistency, not a proven live bug. Do not over-claim it when fixing.
- **D16 · No cross-surface value agreement.** Nothing checks that two surfaces rendering the
  same fact agree; CLAUDE.md's own F-204a item tracks this as a manual screenshot check
  ("a ticker in both Act Today and Top Positions must show the SAME composite"). A pure
  comparator over the few doubly-rendered facts (composite, day P&L, position count) turns
  it into a test. Scope after D3, which may make part of it moot.

---

## Execution order (reprioritized 2026-09-13, after Band A closed)

The original numbered plan below this line predates Step 0 and is stale — item 2's own
"Batch A" (D2, D4, D5, D12) was **never actually built**; the session's effort instead went
to the items Step 0 *promoted* to Band A (D20, D17, D19, D8, D24, D23, the backfill), which
are now done. Superseded by the ranked list immediately below. Left the old numbered list
underneath, struck through in spirit, so the reasoning that produced it isn't lost.

**Ranked by urgency × impact, not by P-band alone — a P0 with zero live trigger and a P2
with a common one can trade places.**

1. ~~**D2 — holdings save reports success before the write.**~~ **FIXED.** See its own
   section below the table — a real regression was caught mid-fix by Opus review, not by
   the author.
2. ~~**D4 — a silently-dropped holding inflates every remaining weight.**~~ **FIXED.** See
   its own section below the table.
3. ~~**D1 — split detection absent from the protective-alert cron lane.**~~ **Commit 1
   (cron path) SHIPPED.** See its own section below the table for the full design,
   mechanism confirmation, and — importantly — a corrected scope for what's still open.
4. **D3 — the provenance spine.** Not urgent (Step 0 measured it clean on the live book —
   no evidence of the fabricated-pillar path firing), but the highest strategic value left:
   it's what unblocks D19's Technical/Sentiment tiles, gives real substance to D8's
   `new_pick` question, and is the actual answer to "can the app tell me when it's making
   things up." Six commits, full design already validated by an Opus `planner` pass
   (verdict: PROCEED) — see the retained plan below for the exact shape. Do this once D1's
   design conversation is in motion, not blocking on it.
5. **D8's `new_pick` reclassification.** A `planner`-scoped research question ("what was a
   thinly-loaded pick actually based on"), not a bug fix — natural to fold into the same
   sitting as D3's design work, since D3's provenance columns are what would make this
   answerable going forward rather than just diagnosable retroactively.
6. **D25 — `signal_date` weekend guard.** One line (`skip when not is_trading_day`),
   confirmed still live and reachable, but low consequence (one historical-analysis column,
   no gate). Pick up whenever `app.py`'s exit_signals capture is next touched for any
   other reason — not worth its own standalone session.
7. **D12 — price cross-check test coverage.** Tests-only, no live risk, but it's the one
   active data-integrity check in the app and it's currently unverified. Cheap; do
   whenever there's a quiet moment.
8. **D16 — cross-surface value agreement.** Real gap (still a manual screenshot check per
   F-204a), medium effort (a comparator across the few doubly-rendered facts), no proven
   current defect. Lowest priority of the "real" items — do after D3, which may make part
   of it moot per the original scoping note.
9. **D5, D6, D13 — pure hygiene.** All three are real in code, all three measured with zero
   evidence of ever firing (Step 0). Fix opportunistically, next time each file is touched
   for another reason — never worth a dedicated pass.

---

## Original execution order (stale, predates Step 0 — kept for its reasoning, not its sequence)

## Verification

- Full `pytest` via the commit hook, plus `check_antipatterns.py` and
  `check_constants_documented.py`.
- Deploy check: push to `main`, wait ~2 min for the Railway redeploy, hard-refresh
  `drishta.up.railway.app`. Never run locally.
- **Screenshot required** for every banner added — per `feedback_streamlit_renderer_mismatch`
  the renderer-mismatch class is invisible to both tests and review.
- **D4 cannot be force-verified:** the `dropped_holdings` banner naming a ticker needs a real
  no-price day. Track it as unverified until observed, the way F-204a's Act Today rows are.

## Review burden

`portfolio.py`, `technicals.py`, `bundle_loader.py`, `constants.py`, `db.py`,
`cron_runner.py` and `risk.py` are `_GATE_FILES` ⇒ mandatory Opus `reviewer` citation per
Hard Rule #4. Standing caveat from `project_app_review_2026_09_09`: **the commit hook is not
firing in the VSCode-extension runtime**, so trailers and citations are manual discipline —
do not rely on the gate to catch a missing one.

Docs to sync in the same session (Definition of Done): `docs/architecture.md` (constants
table + a module section for `score_provenance.py`), `docs/requirements.md` (F-rows for
check ⑦ and the withholding gate), `docs/shipped-log.md`, this plan's own Status line, and a
memory file for the plumbing-vs-payload distinction.

## Explicitly not doing

- **Not** re-running the 2026-08-30 data-integrity audit — closed. Only its item 2 (7 tables
  with no System Trust check) remains, and check ⑦ supersedes it.
- **Not** touching `holdings`-vs-`trades` reconciliation. `holdings` is mutated
  incrementally and only reconciled against the `trades` replay when 📒 Trade Journal is
  opened (`app.py:24618-24630`) — a real structural gap, but the owner deprioritised the
  ledger and it deserves its own plan.
- **Not** mass-editing the ~20 sentinel-collapsing call sites. Considered and rejected once
  already (`coord_freshness.py:18-25`), for reasons that still hold.
- **Not** changing any threshold value unless the owner sets it.

### Four traps flagged by the design pass — recorded so they are not re-proposed

- **Do not make the fabricated pillars return `None`.** `t_score`/`s_score` flow into
  `combined_score` (`scoring.py:23-27`), which would `TypeError`, and every
  `bundle["t_score"]` consumer would need patching in the same commit. **Keep the `50.0`;
  the flag travels beside it.**
- **Do not set `port_df["Score"] = None` for a degraded row.** `macro_playbook.py:318` reads
  `_f(row.get("Score"), 50.0)`, so a `None` lands straight back on a fabricated neutral 50 —
  reintroducing the exact defect by a second route.
- **Do not re-normalise `combined_score` over available pillars to "fix" the 50.0.** That is
  what `valuation.py:111` does internally, and it is the mechanism that silently lifts
  sell-side weight from 16.5% to 30% (the open Analyst Weight Audit Phase B item).
  Re-weighting the engine is a scoring-formula change dressed as a data fix.
- **Do not change what `bundle["news"] == []` means** in the same pass as the flags — every
  existing `news_raw` consumer reads `[]` as "no news". Recovering the failed-vs-empty
  distinction is a provider-layer sentinel change touching `db.py`, and is its own reviewed
  commit.
