# DRISHTA — Data Accuracy & Integrity: ranked findings register

**Date:** 2026-09-13
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 5 (findings verified at HEAD; design pass by the `planner` agent, Opus 5)
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

| ID | Finding | P | Effort |
|---|---|---|---|
| **D20** | `or 50` inverts a legitimate 0 pillar score to neutral (`app.py:21504-21508`) | P1 | S |
| **D19** | All 4 pillar tiles render possibly-fabricated `N/100` with an asserting caption | P1 | S (2 of 4) + D3 |
| **D8** | Pillar columns missing on 25% of `new_pick` and **100% of `enter_now`** rows | P2 | M |
| **D23** | Live-leg cross-check fires outside regular trading hours → alarm fatigue | P2 | S |
| **D17** | `docs/architecture.md` §6.38 misdocuments `fundamentals_cache` | P2 | XS |
| — | **Backfill**: 32 `exit_signals` rows unpriced 07-18→08-04, blocking Protective Track Record | P2 | S |

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

### Band C — closed, retracted, or informational

| ID | Outcome |
|---|---|
| ~~**D9**~~ | **RETRACTED** — rows are different firms, not duplicates. No dedup key needed. |
| ~~**D7**~~ | **CLOSED** — the coalesce *is* the fix (`bd48079`), not the bug. Measured behaving. |
| ~~**D21**~~ | **CLOSED** — unpriced `exit_signals` fixed 2026-08-05. Residual = the backfill in Band A. |
| ~~**D10**~~ | **CLOSED** — no `account_flows` duplicates. Annotate the stale manual-follow-up note at `db.py:827-836`. |
| ~~**D14**~~ | **CLOSED** — zero orphans in `manual_stops` and `exit_signals`. No FKs, but no drift. |
| ~~**D15**~~ | **CLOSED** — DDL forbids it; 256 trades, zero violations. |
| **D11** | **Informational** — 7 failures / 783 checks / 51 days; 2 genuine prev-close breaches. Gate-wiring is marginal at that rate; D23 outranks it. |
| **D18** | **Informational** — per-row `consensus_rating` is sometimes single-firm. Scoring pools correctly. Phase B decision D4 accepted it. |

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

## Execution order

1. **Step 0** — SQL pack. Re-ranks D3, D7, D8, D9, D14, D15.
2. **Batch A** (no DB needed, ships immediately): D2, D4, D5, D12, plus the trivial dead
   `technical_score` import at `app.py:58`.
3. **D1 design pass** — `planner`, in parallel with Batch A.
4. **D3 — the provenance spine.** Six commits, 1–5 with zero user-visible change, each
   independently revertible. Design validated by an Opus `planner` pass (verdict: PROCEED):
   - `technical_score_detail(df) -> dict` widening that keeps the public 2-tuple signature
     (one production caller, `bundle_loader.py:84`; ~50 tests keep passing unchanged).
     `available = max_pts > 0` is structural — **no minimum-signals floor**, since a thin
     real measurement is categorically different from a fabrication.
   - `bool(headlines)` for sentiment, computed immediately before `bundle_loader.py:185` —
     after the LLM-rescore block, the one point where the headline set that produced
     `avg_sent` is final.
   - New pure `stock_analyzer/score_provenance.py` with **four** states —
     `MEASURED` / `ASSUMED` / `DEGRADED` / `UNUSABLE`. `ASSUMED` exists because today's
     default-`True` on absent flags *is* the collapse of "never checked" into "checked and
     clean".
   - `analyst_intel.trustworthy_composite` reduced to a thin wrapper. **Acceptance
     criterion: its ~20 existing tests pass with ZERO edits.**
   - Carry as **two new columns inside `build_portfolio_df`'s row literal** — not `.attrs`
     (pandas drops attrs silently through `groupby`/`merge`, and both existing `.attrs`
     reads are already baselined as `OFFLINE_SENTINEL_COLLAPSE`). A *string* state, so an
     absent column yields `None` and cannot masquerade as "measured".
   - **No new constant in this phase.**
5. **BUY-side withholding** — the owner's chosen destination, with one measurement in front
   of it: suppression needs a new policy constant (proposed `COMPOSITE_MAX_FABRICATED_WEIGHT`)
   whose value cannot be chosen responsibly before Q1 shows how often `DEGRADED` occurs.
   Entry gates only; **protective EXIT/TRIM/WATCH paths explicitly untouched.** Two further
   policy calls are the owner's here: whether Grow Today's gate
   (`daily_briefing.py:1125-1128`) extends to the new flags, and whether `ASSUMED` keeps
   counting as a pass (it does today, implicitly).
6. **P2 cleanup** — driven by the Step 0 results.
7. **🩺 System Trust check ⑦, "Decision Data Quality"** — the standing surface. Unlike ①–⑥
   it grades the payload: of N holdings scored this session, how many on measured data, how
   many on a stale bundle, how many with a fabricated pillar, named by ticker; the age of
   the oldest bundle actually used in a decision; and whether the cross-check ran. Follows
   `system_health.py`'s existing contract (owner-only, read-only, never raises,
   `unknown ≠ degraded`). **Open question for the reviewer:** whether ⑦ joins the Home chip
   rollup or is excluded like ④/⑤ — ⑤ was excluded to stop a permanent amber desensitising
   the chip, and ⑦ should not repeat that if it turns out to be chronically amber.
8. **D11, D16** last.

---

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
