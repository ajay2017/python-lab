# DRISHTA — Database Maintenance & Data-Quality Framework

**Date:** 2026-09-28
**Author:** Ajay Kumar
**Design pass:** Opus `planner` (Opus 4.8, 1M context), against a three-agent parallel research pass over the live codebase (schema/write-patterns, existing maintenance/health-check infra, orphan/duplicate/growth risk).

**Status (2026-09-28, Phase 0 COMPLETE).** All 4 queries run against production by the owner, results pasted back:
- **Q1 — F3 CLOSED, confirmed safe.** All 6 tables (`catalyst_stress_cache`, `debate_cache`, `missed_opportunity_cache`, `regime_scenario_cache`, `structural_scan_cache`, `thesis_cluster_cache`) have a real `PRIMARY KEY` exactly matching the natural key their `save_*` function treats it as (`scan_date` for 5 of them; `ticker, debate_type, debate_date` for `debate_cache`). Postgres' `.upsert()` defaults to the table's PK when no explicit `on_conflict` is given, so these bare upserts were never actually at risk. **No fix needed — remove F3 from Phase 1's scope entirely.**
- **Q2 — F5 confirmed, not a surprise.** Zero foreign-key constraints exist anywhere in the `public` schema. Confirms (rather than assumes) that referential-integrity checking can only ever be advisory/detective — there is no DB-level relationship to lean on, by design of this schema, not a gap to close.
- **Q3 — `account_flows` uniqueness confirmed exactly as `db.py`'s own comment claimed.** Two indexes: `account_flows_pkey` (`UNIQUE (id)`, the surrogate key only) and `account_flows_txn_id_unique` (`UNIQUE (snaptrade_txn_id) WHERE (snaptrade_txn_id IS NOT NULL)` — a **partial** index). A manually-entered flow always has `snaptrade_txn_id = NULL`, so it is structurally invisible to the only real uniqueness guard on this table. Nothing else stands in for it. **F2a confirmed as a genuine, currently-unguarded gap** — Decision 1's write-time dedup guard is free to add a new key without colliding with either existing index.
- **Q4 — Decision 1's blast radius is trivial today.** 1 manual row, 0 broker-synced rows (the SnapTrade flow sync hasn't populated this table in this account yet), 0 existing duplicate groups. **No backfill/cleanup pass is needed alongside the write-time guard** — it's pure prevention going forward, not prevention-plus-remediation.

**Net effect on Phase 1's scope:** F3 drops out entirely (closed, confirmed safe). F5 stays exactly as scoped (advisory-only forever). F2a (the `account_flows` gap) is now the single most concretely-confirmed finding in the whole document, and its fix (Decision 1, resolved below) carries zero cleanup burden.

**Status (2026-09-28, all four owner decisions RESOLVED — Phase 1 is now fully scoped):**
1. **`account_flows` dedup key** — exact match on `(flow_date, flow_type, amount, note)`, scoped to inserts within a short time window of an existing identical row (new constant `ACCOUNT_FLOW_DEDUP_WINDOW_SEC = 10`). Chosen specifically because the real risk is a double-click submitting the same form twice (near-simultaneous, identical in every field) — an unconditional-forever exact-match key would incorrectly block a genuinely repeated flow (e.g. two real $500 deposits made days apart), which this time-window scoping avoids.
2. **Orphan-cache grace period** — `DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS = 90`, matching the existing reference-shelf-life convention.
3. **Detection-only observation window** — 8 weekly Saturday `_run_maintenance` runs (~2 months) of clean detection-only operation before F1's orphan-cache auto-remediation may ever be enabled. Tracked at build time via a dated trigger (e.g. a named constant holding the earliest-eligible date, set once Phase 1 actually ships and its first clean run is confirmed) rather than a runtime counter — simpler and matches how this repo's other gated-phase triggers are recorded (dated notes in `docs/plans/*.md`, not application state).
4. **Trend-tracking table** — **no**, not in Phase 1. Findings ride the existing cron log + a new System Trust check. Revisit only as an optional, separately-approved Phase 3 if longitudinal trend data later turns out to matter.

**Nothing built yet — these are the resolved inputs Phase 1's implementation will use.** New constants (`ACCOUNT_FLOW_DEDUP_WINDOW_SEC`, `DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS`) still need to be walked through with the owner at actual write time per Hard Rule #1 (already effectively done here, but the values must be transcribed into `constants.py` + `docs/architecture.md`'s constants table at build time, not assumed to already exist).

**Status (2026-09-28, Phase 0 started):** SQL verification pack written — `docs/sql/data-maintenance-phase0.sql`, 4 read-only queries (Q1: PK/unique constraints on the 6 bare-`.upsert()` tables named in F3; Q2: confirm zero FK constraints exist anywhere, scoping F5; Q3: the real `account_flows` index situation; Q4: manual-row count + existing-duplicate check on `account_flows`). Owner runs these in the Supabase SQL editor and pastes results back — a coding session has no DB credentials, same boundary as every other SQL pack in this repo (`docs/plans/data-integrity.md`'s Step 0). Nothing else started.

**Status:** DESIGN COMPLETE, NOTHING BUILT. Verdict: **PROCEED WITH CHANGES** — scoped far tighter than the original request. Four owner decisions are open (below) before Phase 0 can start. No code written, no constant changed, no DB row touched.

> Status convention: newer status lines are prepended ABOVE this one as work lands (`feedback_living_doc_status_prepended`). Read the topmost line first.

---

## Origin

The owner asked to evaluate a reusable database/data-maintenance process (on-demand or cron), covering: nulls/malformed/duplicate values, referential integrity, stale/orphaned/abandoned records, values that stop refreshing, invalid state transitions, historical data safe to archive, and DB growth/performance.

This is explicitly **not** a re-run of either existing DB-focused effort in this repo, and the distinction matters enough to state up front:

- The 2026-08-30 audit (`project_data_integrity_audit_2026_08`) verified rows are being written at all — a **plumbing** question.
- `docs/plans/data-integrity.md` (2026-09-13, "Data Accuracy & Integrity") verified whether the *values* the engine decided on were real, not fabricated-neutral placeholders — a **payload** question.
- **This document is a third, distinct angle: is the stored row-SET structurally clean over time** — orphaned rows, duplicate rows, stuck state transitions, unbounded accumulation. Not "is the cron alive" (system_health.py already owns that), not "is this composite fabricated" (data-integrity.md already owns that) — "does the data quietly rot at the edges as tickers churn and buttons get double-clicked."

## What already exists (do not rebuild any of this)

A parallel research pass confirmed the following infrastructure is already live and must be extended/complemented, never duplicated:

1. **`cron_runner.py::_run_maintenance`** (Saturday-only Railway cron lane). Three isolated sub-jobs (ticker-liveness/shelf-life sweep, `analyst_coverage` anchor-price backfill, `model_predictions` history backfill), each in its own try/except. **Nature: read-then-write backfill of missing/NULL rows only — never deletes, never archives, never dedupes.** No dry-run flag because NULL-only targeting *is* its safety mechanism.
2. **`stock_analyzer/system_health.py`** (🩺 System Trust page, owner-only, 6 checks, purely read-and-report). ② `check_data_stores()` already grades table freshness for a 17-store registry — **a new framework must not re-grade table freshness**, only row-set cleanliness.
3. **`stock_analyzer/reference_shelf.py`** — a narrow, registry-based staleness mechanism for exactly 6 hand-curated reference tables. A plausible structural template, not a generic engine.
4. **`stock_analyzer/reference_data.py::validate_payload`** — the app's only existing write-time validator, scoped narrowly to 3 App Settings roster payloads. No equivalent exists for any other table family.
5. **`stock_analyzer/broker_sync.py`'s income-event dedup subsystem** — the best existing precedent for "detect confidently, remediate conservatively, escalate ambiguous cases to a human" (write-time consume-on-match dedup + a read-side backstop + an advisory-only near-duplicate flagger that never auto-merges).
6. **`scripts/` diagnostic convention** — read-only, evidence-not-recommendation, several requiring the owner's own Supabase credentials to run manually. **Nothing in the codebase today deletes/archives/purges any DB row, anywhere.** A new framework proposing deletion is genuinely novel territory here, not a duplicate — but should inherit this same conservative posture.

---

## Findings, by the owner's own 7 requested categories

Of the 7 categories requested, only ~2.5 have a real, evidenced finding. Stating this plainly matters more than a complete-looking checklist:

| Category | Verdict |
|---|---|
| Nulls/malformed/**duplicates** | **Real finding** (below) |
| Referential integrity | Thin — mostly subsumed by orphan-ticker detection; no FK enforcement is visible from `db.py` alone, so this can only ever be advisory/detective |
| **Stale/orphaned/abandoned records** | **Real finding** (below) |
| Values that stop refreshing | **Already covered** — `system_health.check_data_stores()` + `reference_shelf.shelf_status()`. Do not duplicate. |
| Invalid state transitions | Thin — one real gap, low stakes |
| **Historical/temp data safe to archive** | **NO — actively dangerous.** See "The hard constraint" below. |
| DB growth/performance | **No finding.** Every growing table is book- or event-scoped (~250–8,000 rows/year), 2–3 orders of magnitude below where `select("*")` becomes a real concern for 1–2 years. (Read-side query/caching issues were already the subject of a separate, completed performance review — `MEMORY.md` → `project_performance_review_2026_09`.) |

### The hard constraint (governs the entire design)

`recommendations`, `exit_signals`, `gate_suppressions`, `rec_events`, `score_history`, `model_predictions`, `analyst_target_snapshots`, `judgment_opinions`/`judgment_grades`, `analyst_coverage` are **intentionally permanent** — each one's own module docstring confirms it exists specifically to grade a past call (Engine Track Record, Research Scorecard, Gate Suppression Ledger, Recommendation Outcomes Measurement). **A maintenance job that archives or prunes any of these would destroy the evidence base those features are built on.** This is the single biggest constraint on the design: the framework must be architecturally *incapable* of targeting these tables for deletion, not merely instructed not to.

### Real findings

**F1 — Orphan cache rows.** 8 tables upsert keyed on `ticker` (or ticker+date) with no removal sweep when the ticker leaves `holdings`/`watchlist`/the discovery universe — unlike `holdings`/`watchlist`/`manual_stops`, which already correctly sweep (`db.save_holdings` db.py:1524-1546, including a symmetric `manual_stops` sweep with the comment "a stop override for a ticker no longer held is an orphan"): `bundle_cache`, `fundamentals_cache`, `sector_cache`, `sentiment_llm_cache`, `thesis_erosion_cache`, `debate_cache`, `etf_lookthrough_cache`, `price_xcheck_history`. Low individual stakes — `max_age_days`-gated reads (e.g. `load_bundle_cache(ticker, max_age_days)`, db.py:1885) already prevent a stale row from ever being *served* — so this is an accumulation-hygiene gap, not a correctness or performance one. **This is the only finding that is ever a plausible auto-remediation candidate**, precisely because these rows are regenerable on next research and feed no decision once expired.

**F2 — Duplicate-insert risk, ranked by real stakes.**
- **F2a · `account_flows`** (`add_account_flow`, db.py:5117) — a manual "Add Flow" button with **no dedup key**, while the *sibling* broker-sourced writer to the same table (`save_account_flows`, db.py:5138) was explicitly hardened on 2026-08-24 specifically because an undeduped insert "would silently re-inflate net_contributed_capital." A double-click today reproduces the exact bug already fixed once, on the other path. **Highest-stakes finding in this document.**
- **F2b · `analyst_coverage`** (db.py:2302) — "each article is a distinct row" is deliberate for genuinely distinct articles (confirmed by `data-integrity.md`'s own D9 investigation — six apparent "duplicates" turned out to be different analyst firms on the same article, not real dupes), but pasting the *same* article twice is not rejected and would double-count in the Research Scorecard/calibration matrix. Flag-for-review only — a human must distinguish "same article pasted twice" from "two firms, one article."
- **F2c · `thesis_reviews`** (db.py:2152) — no unique constraint; a double-click before `st.rerun()` fires could insert two rows. Low blast radius (display-only AI review log).

~~**F3 — Design-risk flag, not a confirmed bug.**~~ **CLOSED by Phase 0 (2026-09-28) — confirmed safe, not a bug.** 6 tables (`debate_cache`, `structural_scan_cache`, `regime_scenario_cache`, `catalyst_stress_cache`, `thesis_cluster_cache`, `missed_opportunity_cache`) call bare `.upsert()` with no explicit `on_conflict` in the Python — correctness depends entirely on the actual Postgres PK. Phase 0's Q1 confirmed all 6 have a real `PRIMARY KEY` matching the natural key each `save_*` function treats it as, so Postgres' default-to-PK upsert behavior makes these correct as written. No code change needed; removed from Phase 1's scope entirely.

**F4 — One state-transition gap.** `snaptrade_pending_imports.status` (pending→logged/dismissed): `mark_snaptrade_pending_import_logged` (db.py:5331) and `dismiss_snaptrade_pending_import` (db.py:5347) both `.update(...).eq("id", ...)` with no precondition on the *current* status — nothing DB-level enforces the transition is one-way. Mitigated in practice (the UI always reloads `status="pending"` fresh each render), so low real risk. Report-only.

---

## Four owner decisions — RESOLVED 2026-09-28

1. ~~**`account_flows` (F2a) — fix at write-time, or detect after the fact?**~~ **RESOLVED: both**, with the dedup key set to **exact match on `(flow_date, flow_type, amount, note)`, scoped to a short time window** (`ACCOUNT_FLOW_DEDUP_WINDOW_SEC = 10`) of an existing identical row — not an unconditional-forever exact match, so a genuinely repeated flow entered days apart is never blocked. Phase 0 confirmed the blast radius is trivial (1 manual row, 0 existing duplicates), so no backfill/cleanup pass is needed alongside the guard.
2. ~~**Orphan-cache grace period (F1).**~~ **RESOLVED: 90 days**, new constant `DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS = 90` — matches the existing reference-shelf-life convention.
3. ~~**Detection-only observation window.**~~ **RESOLVED: 8 weekly Saturday runs (~2 months)** of clean detection-only operation before F1's auto-remediation may be enabled — tracked via a dated trigger recorded once Phase 1 ships and its first clean run is confirmed, not a runtime counter.
4. ~~**Longitudinal trend tracking — worth a new table?**~~ **RESOLVED: no**, not in Phase 1. Findings ride the existing cron log + a new System Trust check. Revisit only as an optional, separately-approved Phase 3 if trend data later turns out to matter.

**Phase 1 is now fully scoped and unblocked.**

---

## Execution model

**Answering the owner's 5 explicit questions:**

**(1) What checks are needed:** exactly F1, F2a/b/c, F3 (report-only pending Phase 0), F4 — nothing more. See findings above for exactly which of the ~46 tables each targets and why.

**(2) Automatic vs manual:**
- **Detection: automatic.** Wired as a new isolated sub-job inside the existing Saturday `_run_maintenance` lane (email-on-finding only, never fails the cron lane on a mere finding — mirrors how the existing ticker-liveness/shelf-life sub-job already behaves), *plus* a new 7th 🩺 System Trust check (`check_data_quality()`) for on-demand inspection any time.
- **Remediation: manual/owner-invoked, and almost never.** F2b/F2c/F3/F4 are flag-or-report only, forever — no automated remediation ever, matching the `find_unreconciled_near_duplicates` precedent (surfaces candidates, never auto-merges). The single exception, F1 orphan-cache deletion, starts owner-invoked-with-confirm (shows exactly what it would delete before doing anything), and is only promoted to automatic in a later gated phase, after the detection-only window has run clean.

**(3) Frequency:** cadence should track how fast the underlying risk can *change*, not how often the table is written — nothing here needs to be more frequent than weekly. F1 and F2: weekly detection (free-riding the existing Saturday lane). F3: effectively a one-time static-structure check (re-run only when a new bare-`.upsert()` table is added — arguably belongs as a `check_antipatterns.py`-style one-off, not a recurring job). F4: monthly or on-demand only.

**(4) One framework or several jobs — one framework.** A new `stock_analyzer/data_maintenance.py` module holding a **registry of small, independent, pure check functions**, each returning a structured finding, each runnable in isolation — mirroring the two patterns this codebase already uses successfully (`system_health.py`'s check-registry shape; `_run_maintenance`'s "isolated sub-jobs, each in its own try/except" discipline). One framework means one place to enforce the protected-table allowlist, one place to enforce the offline-sentinel discipline, one dry-run/threshold convention — scattering into several small jobs would repeat the exact fragmentation that let the `account_flows` fix land on one write path (`save_account_flows`) and miss its sibling (`add_account_flow`).

**(5) Metrics/logs to retain — no new table in Phase 1.** Findings ride the existing cron `_log(...)` output, the existing email-on-finding channel, and the new System Trust check surfacing the latest run's result on screen. Each finding record: check id, target table, finding type, affected row count, a small sample of offending keys (never full rows — respect the existing `_m()` privacy posture for dollar figures), classification tier (report/flag/auto-fixable), and whether the underlying read succeeded vs was skipped-as-unreadable. A dedicated trend-tracking table is explicitly deferred to an optional Phase 3 (Decision 4).

---

## Safety design

- **Dry-run is the default, and in Phase 1 the *only* mode.** The framework computes and reports; it changes nothing. A remediation path exists only for F1, only behind an explicit `apply=True`/owner-confirmed flag, only after the detection-only window.
- **Offline-sentinel discipline is mandatory and non-negotiable.** Every check reads through the existing `db.py` loaders and branches on `is None` explicitly — never `or []`/`or {}`. A `None` (DB down / table missing / exception) yields a `status="skipped/unreadable"` finding, **never** a "clean, safe to act on" result. This is the previously-fixed bug class this repo already knows about (`feedback_sentinel_is_present`, `feedback_none_sentinel_meets_pandas`) — a maintenance tool that deletes rows on a misread outage would be the worst possible instance of it.
- **A hard-coded PROTECTED-TABLE allowlist**, checked first by any destructive code path, listing every intentionally-permanent historical/event-log table named under "The hard constraint" above. No check may ever target these for deletion or archival.
- **Threshold tiers**, every number a named `constants.py` value: **AUTO-FIXABLE** (F1 only, later phase, gated on the observation window) / **FLAG-FOR-REVIEW** (F2a backstop, F2b) / **REPORT-ONLY** (F2c, F3, F4).
- **Rollback/recovery:** nothing to roll back for detection. For F1's eventual remediation: log the exact deleted `(table, ticker, key, age)` tuples to the cron log before deletion, so a row can be regenerated by simply re-researching the ticker — the natural recovery path, since the cache self-heals. No row that feeds a decision or a track record is ever in scope (enforced by the protected-table allowlist), so there is no "undo a lost recommendation" scenario to design for at all.
- **Coordination guardrail:** this framework is pure back-office hygiene. It must never publish anything to `st.session_state` that a decision consumer reads, and must never surface on a decision surface (Act Today, Watchlist, etc.) — its only surfaces are the owner-only System Trust page and the maintenance email.

## Tests the eventual build must include (invariants, exercised — not merely argued)

- Offline-sentinel: a check fed a `None`-returning loader must return `unreadable/skipped`, never a clean/empty result — and the remediation path must refuse to act on an unreadable read.
- Protected-table guard: actually pass a protected table name to the destructive path and assert it refuses.
- Orphan detection boundary: a ticker in any roster is never flagged even with old cache rows; a ticker in none of them past the grace period is flagged; exercise the grace boundary exactly (at, one below, one above the constant).
- Duplicate detection: F2a flags identical manual `account_flows` rows but not rows carrying distinct `snaptrade_txn_id`s; F2b flags identical `(ticker, article_date, firm)` but not genuinely distinct articles/firms.
- Dry-run guarantee: default invocation performs zero writes/deletes (mock the client, assert no delete/update/insert call fires).
- Isolation: one check raising must not prevent the others from producing results, and must not fail the cron lane (mirrors `_run_maintenance`'s existing try/except discipline).

---

## Phased next steps (nothing built yet — this is the queue to pick from)

- ~~**Phase 0 — owner-run, no app code.**~~ **DONE 2026-09-28.** Confirmed the actual Postgres PK/FK constraints on the 6 bare-`.upsert()` tables (closed F3, no fix needed) and the real `account_flows` uniqueness situation (confirmed F2a's gap is real, with trivial current blast radius — 1 manual row, 0 duplicates). See the top status line for full results.
- **Phase 1 — the detection-only framework.** New `stock_analyzer/data_maintenance.py` (check registry: F1, F2a/b/c, F4; F3 report-only if Phase 0 warrants), the protected-table allowlist, offline-sentinel discipline, full test coverage per above. Wired into (a) an isolated sub-job in `_run_maintenance`, (b) `system_health.check_data_quality()`. No remediation, no new table. New constants named + constants-doc-synced. `db.py`/`cron_runner.py`/`system_health.py` are `_GATE_FILES` → mandatory Opus `reviewer` citation.
- **Phase 1b (parallel, small, high-value)** — the `add_account_flow` write-time dedup guard, key resolved (Decision 1: `(flow_date, flow_type, amount, note)` within `ACCOUNT_FLOW_DEDUP_WINDOW_SEC`). `db.py` change → Opus reviewer required.
- **Phase 2 — gated.** Enable F1's auto-remediation only after Phase 1 has run clean for the approved observation window (Decision 3). Owner-invoked-with-confirm first; auto-on-Saturday only if the owner then separately approves it. `db.py` delete path → Opus reviewer required.
- **Phase 3 — optional, only if Decision 4 = yes.** A `data_quality_findings` trend-tracking table. Own DDL, own `db.py` write path, own mandatory Opus review. Not built speculatively.

## Explicitly not doing

- Not building any archival/purge/partition mechanism motivated by growth or performance — no evidence supports this being a real problem for 1–2 years.
- Not touching (archiving, pruning, or otherwise altering) any of the intentionally-permanent historical/event-log tables under any circumstance.
- Not building a generic, table-agnostic freshness/staleness checker — `system_health.py`/`reference_shelf.py` already own that question.
- Not adding a trend-tracking table speculatively (Decision 4 governs this).
- Not enforcing referential integrity beyond ticker-orphan detection — no FK reality is confirmed, so anything beyond that is guesswork until Phase 0.
