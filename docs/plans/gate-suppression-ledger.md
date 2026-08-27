# Gate Suppression Ledger — capture half

**Status: CAPTURE HALF BUILT + Opus-reviewed (SHIP, 0 blocking) 2026-08-27. SHIPS INERT — the §4 DDL is NOT yet applied, so nothing accrues until it is. Readout half NOT started and must not be bundled.** Design was the Opus `planner` pass 2026-08-26; the build overturned two of this document's own premises — see §5a.

Design source: [docs/reviews/2026-08-26-app-review.md](../reviews/2026-08-26-app-review.md) Part 2 #1.
The readout is a separate, later build (that review's Innovation #1) and must not be bundled.

---

## 1. Why

OP-01 (`docs/requirements.md`) says the app would rather recommend nothing than recommend
wrongly. That is the product. The app grades its buy calls (F-229), its protective calls
(F-229 addendum), the owner's own trades (F-233/F-256) and outside analysts (F-154c).

**It has never once graded its own restraint — the thing it does most often.**

On a book run at ~3.15x leverage a systematically mis-set suppression is not a neutral
non-event. And the evidence is generated daily and discarded daily: `_grow_today` already
returns every blocked bucket, and `headless_alert_engine` counts two of them into a log line
and throws the rest away. No new computation is needed — only persistence.

Second-order effect: `judgment_grading.py` refuses to grade protective dimensions because it
lacks counterfactual data. This ledger is the missing input.

---

## 2. Three premises from the review that are WRONG against the code

The `planner` pass checked the review's assumptions rather than inheriting them. Two of these
would have poisoned the dataset in ways no later analysis could repair.

**F1 — the cron lane cannot see one of the seven gates.** `headless_alert_engine` calls
`build_daily_briefing` with `risk_recs=[]`, `alert_list=[]`, `movers=[]` and no `trades_df`.
So on cron: `_trim_targets(risk_recs)` is empty ⇒ **`risk_blocked_adds` (G-01) is structurally
always empty, forever**; and a smaller `_act_blocked` set means tickers the interactive app
would have skipped *before any gate* instead reach the macro/sector gates and get recorded as
suppressions that never happened to the user (the cron ledger **over-counts** G-07/G-16).
Without a `source` column, "G-01: 0 rows" is permanently ambiguous between *gate never fired*
and *gate was never evaluable* — the exact checked-vs-never-checked failure this app calls its
worst mode, baked into the data. `source` is therefore non-negotiable.

**F2 — bear days record zero suppressions.** `_grow_today` early-returns with every bucket
empty, and `cooldown_adds` / `deterioration_blocked_adds` are not even keys in that return. On
the days the app performs its single largest act of restraint, the ledger would record nothing.

**F3 — `deterioration_blocked_adds` is not a suppression bucket.** Its pre-pass appends *every*
held WATCH ticker, regardless of tone and regardless of whether the name could ever have
qualified as an add. Capturing the merged list measures the forward returns of names below
their trend MA *by construction* — not the value of the restraint. **This alone would produce a
strong spurious signal and make the retirement test below return a false positive.** Hence the
`counterfactual` column.

**F4 — `concentration_blocked_adds` carries two different gates** (G-04 single-name ceiling and
G-09 drift-trim conflict) with opposite meanings. A bucket→`gate_id` 1:1 map destroys the
distinction, which is why the producer must emit the id rather than the writer inferring it.

**F5 — the dedup key.** The scan lane genuinely runs twice daily (both DST slots clear its
`hour < 9:30` gate), plus once per interactive Home brief build. And
`save_recommendations` falls back to a plain `.insert()` on a `TypeError` — copying that shape
would make the unique constraint the only thing standing between this table and the
`account_flows` unbounded-reinsert bug (2026-08-24). **Upsert only; no `_insert` fallback.**

---

## 3. Decisions taken with the user (2026-08-26)

| # | Decision | Chosen |
|---|---|---|
| 1 | Bear days | **Emit one synthetic row per bear day for the tone gate.** Needs a new gate ID for something §2A.3 has no ID for. |
| 2 | `reason` free text | **Keep, quarantined at 300 chars.** Human forensics only; nothing may ever parse it. |
| 3 | Write path | **Both cron and interactive**, with `source` in the dedup key. |
| 4 | Retirement criterion | **Accept the anchors** in §5, pre-registered now. |

Decision 3 note: `source` in the key is a deliberate departure from the review's recommendation.
First-writer-wins still holds *within* a source, so the two DST cron slots still dedup to the
09:45 price (matching `recommendations`' documented semantics) — while the **complete**
interactive row is no longer suppressed by the **incomplete** cron one. Cost is at most 2× rows.

---

## 4. Schema

Reasoning for the non-obvious columns:

- **`gate_value` + `gate_threshold`** — the measured quantity and the boundary it crossed. Not
  reconstructible later (weights change daily; `risk_advisor` recomputes live). Without them you
  can only grade *whether a gate helps on average*; with them you can grade *whether
  `SINGLE_NAME_CEILING` is set at the right number* — and calibration is the real policy question.
- **`sector`** — kept despite looking reconstructible. Sector *classification* drifts (F-240/F-242
  roster refreshes, the IGV alias fix), and as-classified-at-the-time is unrecoverable after a
  taxonomy change. That class of drift has bitten this project before.
- **Deliberately NOT included:** portfolio state (`daily_snapshots` + `account_cash` already own
  it, joinable on date — a second source of truth for a number that has one), and a brief pointer
  (no brief id exists in this codebase; it would be a foreign key to nothing — `(rec_date, source)`
  already identifies the build).

```sql
create table if not exists public.gate_suppressions (
    id                bigint primary key generated always as identity,
    rec_date          date  not null,
    ticker            text  not null,
    gate_id           text  not null,          -- stable id from gate_registry.py; APPEND-ONLY
    source            text  not null,          -- 'cron' | 'app'  (F1: cron cannot see G-01)
    lane              text,                    -- 'new_pick' | 'add_winner' | 'tone'
    counterfactual    boolean,                 -- true = gate was the BINDING constraint (F3)
    tone              text,                    -- 'bull' | 'flat' | 'bear'
    price_at_suppress numeric,                 -- NULL when <= 0, per price_at_surface convention
    composite_score   numeric,
    momentum_score    numeric,
    sector            text,                    -- as CLASSIFIED that day; taxonomy drifts
    gate_value        numeric,
    gate_threshold    numeric,
    reason            text,                    -- human forensics ONLY. NOTHING MAY EVER PARSE THIS.
    suppressed_at     timestamptz default now(),
    constraint gate_suppressions_unique_per_day
        unique (ticker, rec_date, gate_id, source)
);

create index if not exists gate_suppressions_rec_date_idx
    on public.gate_suppressions (rec_date desc);
create index if not exists gate_suppressions_gate_date_idx
    on public.gate_suppressions (gate_id, rec_date desc);

alter table public.gate_suppressions enable row level security;

DROP POLICY IF EXISTS "Allow all (service role)" ON public.gate_suppressions;
CREATE POLICY "Allow all (service role)" ON public.gate_suppressions
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Ships INERT until this DDL is applied by hand** — same precedent as `model_predictions`,
`decision_context` and `premortem_trigger_price`. **Record the application date here the same
day it happens:** CLAUDE.md carried a stale "DDL pending" claim about `model_predictions` for
three weeks after it was live.

**DDL applied:** _not yet._

Volume: ceiling ≈ 30 rows/day/source ≈ 7,600/year worst case, realistically 2,000–5,000.
**Accrue indefinitely, no retention policy.**

---

## 5. Retirement criterion — PRE-REGISTERED 2026-08-26, before any data exists

The review's "after ~40 suppressions no gate shows a consistent sign" is too loose to bind: it
does not say per-gate or aggregate, does not define "consistent sign", and has no date.

- **Measure:** forward alpha vs SPY at **H = 30 trading days** from `rec_date`, computed by
  **reusing `predictive_analytics.forward_alpha_at_horizon`** — do not write a second alpha.
- **Per-gate, never aggregate.** Pooling would let a strong G-07 mask a useless G-09.
- **Two floors, both required.** A gate is EVALUABLE only at **N_min = 8** matured rows *and*
  **K = 5 distinct tickers**. The distinct-ticker floor is load-bearing and the review missed it:
  a bucket that re-records the same ticker daily can reach 30 rows on one observation.
- **Filter to `counterfactual = true`** (F3). Non-binding rows are context, not evidence.
- **Restrict G-01 to `source='app'`** (F1). A G-01 verdict over cron rows is a verdict over an
  empty set.
- **Below the floors → "building", no number, no verdict** — the same banding F-229 uses.
- **Review date: 12 months after the first row lands.** If by then *no* `gate_id` has produced an
  evaluable verdict distinguishable from zero, **the readout is retired** — the card comes out and
  the table either drops or stays as a passive log.

**Retiring is the success condition of this criterion, not a failure of the project.**

### 5a. Four constraints on §5 added 2026-08-27 by the capture-half build

Found by the Opus `reviewer` pass on the capture half. Recorded here because each one changes
how §5 must be *read*, and three of them would otherwise make the readout draw a confident
wrong conclusion from correct data.

1. **The new-pick lane's `counterfactual = true` means "first binding gate", NOT "would have
   been bought".** G-07 and G-16 fire in `_grow_today`'s pick loop *before* `_cross_reference`,
   the conflicted skip, the flat-day verdict gate, the stale-bundle gate, the composite
   `>= COMPOSITE_BUY` gate and the `max_picks` cap. So a momentum-qualifying name can be
   recorded as macro-suppressed when the composite gate would have killed it anyway. The bias
   is directional, not random: composite-failing names underperform by the app's own model, so
   G-07/G-16 read as **more** protective than they are, which biases §5 *against* retirement.
   **The readout must therefore restrict the new-pick lane to `composite_score >= COMPOSITE_BUY`,
   and must REPORT the `composite_score IS NULL` rows as a separate, labelled subset rather than
   silently folding them either way.** That NULL arm is a live judgment, not a settled formula,
   and it is the one place this note could mislead: `_grow_today` also refuses to surface names
   whose composite never loaded (they go to `composite_unavailable` and never reach `new_picks`),
   so admitting NULLs partially re-admits the very population this item exists to exclude — while
   dropping them shrinks an already-small sample and discards names that were legitimately
   unmeasured rather than measured-and-failed. Whoever builds the readout must make that call
   knowingly, with both counts on screen. The capture half stores `composite_score` at those two
   sites precisely so the choice is available at all; without it the distinction is unrecoverable.
   Sites 3-9 (the add lane) need no such filter — after G-09 the
   code goes straight to `add_positions.append` with no further gate and no cap, so reaching
   any add-lane gate genuinely proves the name would have been an add.
2. **G-20's `counterfactual = true` count is an undercount, and bull-day-only.** The in-loop
   upgrade is gated on `tone == "bull"`, and `save_gate_suppressions` is first-writer-wins, so
   an intraday flat→bull tone flip keeps the morning's `False` and discards the later binding
   `True`. Lost data, never wrong data — but **a low G-20 True count must not be read as "the
   gate rarely binds."** One caveat on the caveat: this holds only on the normal write path. If
   the deployed supabase-py rejects `ignore_duplicates`, the compat retry flips the table to
   **last**-writer-wins, under which the upgrade survives and this undercount does not occur —
   so check the `error` string before reasoning about the count either way.
3. **G-23 can never be evaluated per-ticker.** Its synthetic rows carry `ticker="__MARKET__"`,
   so `forward_alpha_at_horizon` has no instrument to price. **Exclude G-23 from the
   evaluable-gate tally** — otherwise it counts as a permanently un-evaluable gate and drags
   the 12-month "no gate produced a verdict" test toward a false retirement.
4. **Every row needs a usable `price_at_suppress` or §5 cannot run at all.** §5 mandates reusing
   `predictive_analytics.forward_alpha_at_horizon`, which returns `None` when `price_at_entry`
   is missing or `<= 0`. The capture half's first draft stored no price at any site, which would
   have made every row unevaluable for every gate, forever — and produced a *false retirement*
   at the 12-month review caused by a missing column rather than by useless gates. Fixed in the
   same build by storing `price` at all nine sites. **Do not "simplify" it away**, and do not
   backfill a `rec_date` close later: that is a different price basis from
   `recommendations.price_at_surface`, so suppressed-name and surfaced-name alpha would be
   computed on two different bases.

**Do not later "fix" `headless_alert_engine.py`'s `brief.get("grow_today") or {}`** believing it
is this feature's offline-sentinel path. It is not — the cron's `None` route is the
`build_daily_briefing`-failed early return, which omits the `grow` key entirely. Collapsing that
`or {}` is harmless here; the sentinel that matters is `build_suppression_rows`' own
`grow is None` check, which **must** stay ahead of its `tone == "bear"` branch (a pinned test
enforces this — reorder them and an offline day carrying a stale bear tone would write a
synthetic row claiming the app exercised restraint on a day the engine never ran).

Recorded in two places by design: here, and as a dated row in CLAUDE.md's "What's queued".
Definition-of-Done step 6 exists because a future-dated gate living only in memory is invisible
to any session that does not happen to ask — which is how three Agentic-Roadmap Phase-2 gates
went untracked.

---

## 6. Build order

1. `stock_analyzer/gate_registry.py` (new) — frozen, **append-only** id→gate map. NOT in
   `constants.py`: these are identifiers, not thresholds. Anti-rot mechanism is a test that
   parses the §2A.3 markdown table and asserts every registry id appears in it.
2. `stock_analyzer/daily_briefing.py` — emit `gate_id` / `counterfactual` / `gate_value` /
   `gate_threshold` on the dicts already appended at the nine suppression sites. **Strictly
   additive: add keys, never move or reorder a branch.** This is the only real decision-path
   exposure in the whole feature.
3. `stock_analyzer/gate_ledger.py` (new) — pure `build_suppression_rows(...)`. Must branch
   `is None` (offline ⇒ no rows) vs `[]` (checked, none) — a semantic collapse here records
   "the gates found nothing" on a day the gates never ran, which is the same lie the ledger
   exists to expose. `check_antipatterns.py` cannot see that; it needs a test.
4. `stock_analyzer/db.py` — `save_gate_suppressions`. Upsert only, `is_readonly()` first,
   never raises.
5. `cron_runner.py` — write in `_run_scan` after the rec-log block, own try/except, must never
   abort the buy-list email.
6. `app.py` — write beside the rec-log, under the same triple guard including `_readonly`.
7. Docs: `docs/requirements.md` (new F-ID + the new tone gate ID in §2A.3),
   `docs/architecture.md` DDL section, `docs/shipped-log.md`, CLAUDE.md queue, memory.

`daily_briefing.py`, `db.py` and `cron_runner.py` are all in `_GATE_FILES` ⇒ **Hard Rule #4
Opus review citation is mechanically required.** `gate_registry.py` and `gate_ledger.py` should
**not** be added to `_GATE_FILES` — they decide nothing.

**No new policy constants in the capture half.** The §5 anchors live in this document until the
readout build; that is what keeps this chunk low-risk under Hard Rule #1.
