# Gate Suppression Ledger — capture half + readout half

**Status: BOTH ORIGINAL HALVES SHIPPED. Capture half LIVE 2026-08-27** — built, Opus-reviewed (SHIP, 0 blocking), shipped, and its §4 DDL applied the same day, so it is ACTIVE and accruing. Design was the Opus `planner` pass 2026-08-26; the build overturned two of this document's own premises — see §5a. **Readout half SHIPPED 2026-08-30** ("🛑 The Road Not Taken", commit `ddd7671`) — a fresh Opus `planner` PROCEED verdict, built by `implementer`, 2 Opus reviewer rounds (first FIX-FIRST/1 blocking — the import-isolation test missed `risk.py`, the live sizing engine; fixed, then SHIP/0 blocking). Full detail: `docs/requirements.md` F-259b; module doc: `docs/architecture.md`'s `stock_analyzer/gate_ledger_readout.py` section. **Expect "building" on every gate for ~2 months** — the ledger is only ~3 days old and the N=8/K=5 floors are far beyond current data; this is the designed primary state, not a bug to chase.

**2026-09-13 — scope-expansion planner pass complete (roadmap item B2), verdict PROCEED, 2 owner decisions pending. See §8.** Not yet built. Widens capture from "the nine sites inside `_grow_today`" to 5 more gates (G-02, G-05, G-06, G-13, G-18) that fire in production today but were never recorded — this was a deliberate original scoping boundary (§1), not an oversight, now being closed as part of `docs/plans/investor-maturity-roadmap.md`.

Design source: [docs/reviews/2026-08-26-app-review.md](../reviews/2026-08-26-app-review.md) Part 2 #1.

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

**DDL applied: 2026-08-27** (same day as the capture half shipped). The table is live, so
the feature is ACTIVE — rows accrue from the next Home brief build (`source='app'`) and the
next ~09:45 ET scan (`source='cron'`). **The §5 retirement clock starts at the first row**,
i.e. review on/after **2027-08-27** once the first row's date is confirmed.

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

---

## 7. Live data — validation from 2026-08-30 audit

**`source='cron'` writes: CONFIRMED in production.** A SQL query of `gate_suppressions` (`group by gate_id, source, counterfactual`) found one `G-20`/`source='cron'`/`counterfactual=false` row, dated 2026-08-28 (1 ticker). A single row on a single date — the pre-pass fire path is confirmed working, but this is not yet a broad sample; do not read "confirmed" as "well-observed."

**`G-20`'s `counterfactual=true` A2 in-place upgrade: STILL not observed.** The same query shows all `G-20` rows to date (`app` and `cron` sources combined) carry `counterfactual=false`. The highest-priority unvalidated scenario (a held WATCH name under an active protective call, composite ≥ 65, gap ≥ 8.0%, on a bull day) has not yet appeared in live data. Remains unproven in production despite unit test coverage.

**`G-01`'s `counterfactual=true` row, RESOLVED 2026-08-30 (was flagged as unexplained).** A `G-01` / `source='app'` / `counterfactual=true` row appeared once, 2026-08-28 (1 ticker). Checked against the actual call site (`daily_briefing.py`, the bull-mode add-to-winner loop, `"gate_id": "G-01"`) rather than guessed: G-01 has exactly ONE call site, unlike G-20's pre-pass/in-loop pair, and that site hardcodes `"counterfactual": True` unconditionally — there is no pre-pass variant and no code path that can ever write a `G-01` row with `counterfactual=false`. It is only reached after a ticker has already cleared the G-24 cooldown check, the G-16 sector-concentration check, and the loop's own Strong-Buy + `composite_score >= COMPOSITE_BUY` + `gap >= ADD_WINNER_MIN_GAP_PCT` qualification — i.e. the code has already determined this ticker WOULD be recommended as an add-to-winner — and Risk Advisor is independently recommending a TRIM on the same ticker (`_trim_set.get(ticker)` truthy), so the add is suppressed to avoid contradicting Risk Advisor. Given the established meaning of `counterfactual` (True = binding/real, the code reached the point where an action would have fired; False = a pre-registered background fact, not tied to an actual near-miss — see §5a), this row is exactly what every `G-01` row will always look like. It is not evidence toward or against the still-unproven `G-20` A2 upgrade case (item above) — the two gate_ids are structurally unrelated, `G-01` having no non-binding form to compare against.

---

## 8. Scope expansion (roadmap B2) — Opus `planner` pass, 2026-09-13

**Verdict: PROCEED.** Strictly additive capture, no DDL, no new constants, no gate/branch/
control-flow change at any of the 5 sites. Two owner decisions below, otherwise
implementation-ready.

### 8a. A framing correction, found before anything else

**G-05 fires at the hard 35% ceiling, not 25%.** The roadmap doc's own B2 item text is fine
("sector → Watchlist downgrade" without stating a number), but the working assumption
carried into this design pass said "25% (soft)" — wrong. `SECTOR_ELEVATED = 25.0` (soft) only
adds a caution banner and KEEPS `ENTER_NOW` (`watchlist_advisor.py:140`); the downgrade to
`NEAR_ENTRY` fires only at the hard breach `sector_wt >= SECTOR_CEILING` (`= 35.0`,
`watchlist_advisor.py:114`, the shared hard-breach return at `:373-409`). `docs/
requirements.md` §2A.3's own G-05 row ("Sector ≥ 35%") was already correct — nothing there
needs fixing, this was a working-assumption error in scoping this pass, caught before it
reached code.

**G-05 and G-06 are literally the same return statement** (`:373-409`), discriminated only
by `gate["kind"]` (`"sector"` → G-05, `"beta"` → G-06).

**G-13 is specifically the in-zone R:R branch at `:492`** (`score >= COMPOSITE_BUY and
in_zone and (rr is None or rr < RR_ENTRY_MIN)`) — **not** the separate "approaching zone"
NEAR_ENTRY branch at `:530` (`pct_above <= 8`, no R:R involved). Both branches emit
`action="NEAR_ENTRY"`, but only `:492` is a G-13 downgrade. **This distinction is
load-bearing and must be a test, not an assertion** — a card from `:530` must never produce
a ledger row.

### 8b. The architectural fact that reshapes the build

`gate_ledger.build_suppression_rows` reads **only** the `grow_today` dict. **None of the 5
new gates flow through `grow_today`** — G-05/G-06/G-13 live in `watchlist_advisor.py`
(rendered on 📋 Watchlist), G-02 in `rebalancer.py` (🔁 Rebalancer), G-18 inline in `app.py`'s
📈 Analysis page. `build_suppression_rows` structurally cannot see any of them.

**So B2 is new pure builders + new interactive write sites, not an extension of the
existing one.** The existing `build_suppression_rows` is untouched. Good news found in the
same pass: `db.save_gate_suppressions` already accepts arbitrary `lane` values and reads a
fixed 14-key dict; `db.load_gate_suppressions()` is an unfiltered `select("*")`. **`db.py`
needs zero changes** — new rows reach the readout automatically once written.

### 8c. Central design questions, resolved

**Q1 — does `counterfactual` mean the same thing for a downgrade as a suppression?**
`True`, unconditionally, at all 5 new sites — each is only reached after the name has
genuinely qualified up to that gate (mirrors G-01's own precedent in §7 above: one call
site, hardcoded `True`, no non-binding form exists). **But the alpha reading it feeds is NOT
the same claim**, which is exactly why lanes matter (Q2): G-02/G-18 are true suppressions
(the add vanishes, alpha = "cost of not adding"); G-05/G-06/G-13 are downgrades (the name
still renders, just weaker — alpha does not mean "cost of not showing it"). G-13 carries a
further nuance worth remembering: its binding constraint is a *missing input* (no validated
R:R), not an override of a fully-qualified green light — weaker than G-05/G-06's override of
a complete ENTER_NOW, still `True`, but the readout must not over-read it.

**Q2 — distinct `lane` value?** Yes, two: **`"downgrade"`** (G-05, G-06, G-13) and
**`"add_suppressed"`** (G-02, G-18). `grade_by_gate` already groups strictly by `gate_id` so
it never blends different gates regardless — the value of the distinct labels is semantic
honesty (keeping `"add_winner"` meaning exactly grow_today sites 3-9, per its own docstring)
and future-proofing a later readout pass that wants to render "downgraded (still shown)"
differently from "suppressed."

**Q3 — does the readout need lane-awareness now?** No. `enrich_and_grade` only branches on
`lane == "new_pick"`; every other lane (existing or new) flows straight through to maturity +
`forward_alpha_at_horizon` with no assertion, no KeyError. `grade_by_gate` never reads
`lane` at all. Zero readout code changes in this pass — the new lanes exist so a *future*
readout pass has a clean key, not because this one needs it.

### 8d. Owner decisions — DECIDED 2026-09-13, both per the planner's recommendation

1. **Readout auto-surface — ACCEPT.** The readout reads `gate_registry.GATE_IDS` directly
   (`app.py:31291`) and does an unfiltered `load_gate_suppressions()` — the moment the 5 ids
   are registered, "🛑 The Road Not Taken" gains 5 new cards (correctly "building" for
   ~2 months). The existing per-card copy ("blocked names lagged SPY = restraint paid off")
   is imprecise for the 3 downgrade gates (the name was shown, not blocked) — **accepted as
   a known, benign gap**, to be refined in a later, separate pass once there's real data to
   refine the copy against. The `"downgrade"` lane (§8c Q2) is what makes that future fix
   keyable. The alternative (decouple the readout from the registry) was rejected — it
   would touch the readout itself and create a registry↔readout divergence the anti-rot
   test exists to prevent.
2. **G-06's compound-gate `gate_value` — TICKER-BETA LEG.** G-06 is `port_beta > 1.4 AND
   ticker_beta > 1.8` — two thresholds, one scalar column pair. **Decided:** store the
   per-instrument leg, `gate_value = ticker_beta`, `gate_threshold = TICKER_BETA_CRITICAL
   (1.8)`; the portfolio-beta leg stays narrated in the `reason` text only.

**Both decisions clear the way — this section is no longer a blocker. Ready for
`implementer` + mandatory Opus `reviewer`.**

### 8e. Build plan (one reviewed commit)

1. **`gate_registry.py`** — append `G-02`, `G-05`, `G-06`, `G-13`, `G-18` (append-only).
2. **`gate_ledger.py`** — three new pure builders, `build_suppression_rows` untouched:
   `build_watchlist_suppression_rows(recs, *, rec_date, source, sector_by_ticker)` (G-05/06/13
   via a `_WATCHLIST_KIND` map keyed on `card["suppression_kind"]`, lane `"downgrade"`);
   `build_rebalance_suppression_rows(risk_blocked_adds, *, rec_date, source)` (G-02, lane
   `"add_suppressed"`, `gate_value=None` — a set-membership gate like G-01); `build_analysis_
   stop_suppression_row(*, ticker, price, composite_score, stop, gap_pct, sector, rec_date,
   source) -> dict | None` (G-18, `gate_value=price`, `gate_threshold=stop`). All 5 gate_id
   literals centralized here (keeps `tests/test_gate_registry.py`'s existing literal-scan
   covering them with zero test-file changes). Update the module docstring's lane-semantics
   block with the two new lanes.
3. **`watchlist_advisor.py`** *(⇒ mandatory Opus review — confirmed `_GATE_FILES` member,
   `.claude/hooks/pre_tool_checks.py:295`)* — additive keys only: `gate_value`/
   `gate_threshold` on the hard-breach return dicts; `card["suppression_kind"] =
   gate["kind"]` at the `:373` hard-breach card; `card["suppression_kind"] = "rr"` +
   `gate_value=rr`/`gate_threshold=RR_ENTRY_MIN` at the `:498` R:R card only (never `:530`).
   All new `_card(...)` fields default `None` so ordinary cards are unaffected.
4. **`rebalancer.py`** — additive keys on the existing `risk_blocked_adds_list.append({...})`
   dict: `price`, `composite_score`, `sector` (all already in loop scope). No control-flow
   change.
5. **`app.py`** — three new interactive write blocks, each copying the existing capture
   pattern (triple guard `has_db() and not <lock> and not _readonly`, own try/except, result
   in `st.session_state`): after the Watchlist ENTER_NOW capture; after the Rebalancer plan
   is built; inside the existing Analysis stop-breach block. No new `constants.py`
   comparison anywhere → `POLICY_DECISION_IN_RENDER` stays green.
6. **Docs (DoD):** this doc's own scope note beyond "the nine sites"; `docs/plans/
   investor-maturity-roadmap.md` B2 status; `docs/shipped-log.md`; `docs/architecture.md`'s
   `gate_ledger.py` module section (new builders + two new lanes); optionally one sentence
   per §2A.3 row noting ledger capture + lane (policy-doc edit — Opus lead, not doc-writer);
   CLAUDE.md queue; memory. **No `constants.py` change** → step 1 / `check_constants_
   documented.py` not applicable.

### 8f. Risks / coordination

- **Source is `"app"` only for all 5** — none of these three surfaces runs headless in a
  cron lane, so (unlike the original 9 sites) do not expect a `source='cron'` row for any of
  these gate ids; that is correct, not a gap.
- Each new write site's own try/except must never disturb its host page's render or its
  existing save-result session-state key (`_rec_log_save_result` / `_wl_rec_save_result`
  etc.) — same defensive posture as the original 9 sites.
- **No new decision thresholds** — Hard Rule #1 is satisfied by construction; lane strings
  and gate ids are identifiers, not policy values.

### 8g. Tests the build must include

The load-bearing invariant (a `:530` NEAR_ENTRY card produces NO row — only `:492` and
`:373` do); sector/beta threshold boundaries (exactly `SECTOR_CEILING`/`SECTOR_ELEVATED`,
each beta leg); `counterfactual is True` on every row from all 3 builders explicitly;
`price_at_suppress`/`composite_score` non-null on a normal input, `price_at_suppress` NULL
on `price <= 0`, for each builder (the exact class F-259's own first review round caught on
the original 9 sites); each builder returns `[]`/`None` cleanly on empty/offline input;
lane routing (G-05/06/13 → `"downgrade"`, G-02/G-18 → `"add_suppressed"`); a readout-side
test that a `"downgrade"`-lane row bypasses the new_pick composite filter in
`enrich_and_grade` and that `grade_by_gate` emits one independent headline per new gate_id;
`tests/test_gate_registry.py` green.
