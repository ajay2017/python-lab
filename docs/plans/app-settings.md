# App Settings — UI-managed reference data

**Status: DESIGN FULLY RESOLVED, still no code written, as of 2026-09-01. User explicitly
chose to HOLD rather than build now — see "Trigger to build" at the bottom.**

**Design state:** the architecture is settled (DB as single source of truth, fail loud on
unavailable, no code fallback — see the DELETE/fail-loud resolution below, which replaces
an earlier self-contradiction in this doc), the redline is agreed (never decision values),
the visual is mocked and reviewed (`docs/mockups/app-settings-mockup.html`), and **all 8
open questions are now resolved** (a `planner` pass, 2026-09-01, re-verified every one
against HEAD rather than trusting this doc's 2026-08-17 wording — see "Open questions —
ALL RESOLVED" below). Nothing here is blocked on anything except a decision to start.
Feature is verified **100% unbuilt** as of 2026-09-01 (`resolve_universe`,
`load_reference_table`, `save_reference_table`, `ReferenceDataUnavailable`,
`reference_tables` — zero hits anywhere in the repo).

**Why this got revisited 2026-09-01:** the doc's own trigger ("build this when the
code-edit friction actually bites — after a couple of real refresh cycles") arguably fired.
The same session did two live examples of exactly the workflow this feature would replace:
a `discovery_universe.py` roster refresh (commit `242d4a7`, 90-day shelf life lapsed) and a
`TICKER_SECTORS` gate-hole fix (commit `6a7ef61`, 11 tickers) — each a full
`planner`→evidence→`implementer`→`reviewer`→commit cycle for what is, in substance, "add a
few tickers to a list." The user chose to fully resolve this design so it's ready, but not
to start building yet.

**Origin:** after F-238 (reference-data shelf life, `docs/plans/` sibling work) shipped,
the user asked whether the hardcoded values it monitors could be managed from the UI
instead of by editing Python — *"an 'App Settings' section under which provide option for
user to change or pick the settings or dates whenever its due."*

**Related:** `stock_analyzer/reference_shelf.py`, `stock_analyzer/system_health.py`
(check ⑤), `docs/requirements.md` F-238, memory `project_reference_shelf_life`.

---

## The redline, agreed up front

**App Settings may own operational and data values. It must NEVER own decision values.**

The user explicitly confirmed this (2026-08-15): investment thresholds stay in code, *"as
its the foundation of the app and needs to maintain its integrity."*

The distinguishing test is **not** "is it a number in `constants.py`". A first draft used
"does changing it move a buy/sell call?", which is under-inclusive: removing NVDA from
`SECTOR_UNIVERSE` unambiguously moves a buy call (it removes the possibility of one), so
Tier 2 would fail its own test. The sharper, two-pronged version:

1. **Does it change the DECISION RULE** — a threshold, weight, or gate? → **never in the
   UI.** These are policy, and git is their audit trail.
2. **Does it change the INPUT SET the rule is applied to** — which names get considered? →
   **permitted**, but only with an audit trail and validation, because the rule itself is
   untouched and still applies uniformly to whatever is in front of it.

| Never in the UI | Why |
|---|---|
| `COMPOSITE_BUY` and every gate | Hard Rule #1: a threshold change is an investment-policy decision |
| Scoring weights, pillar formulas | Hard Rule #4: these require an Opus review, cited in a commit |
| Concentration / risk limits | Same |

A slider on any of these would erase the project's whole safety model: no git history, no
diff, no review, no commit message recording *why* the number changed — and no way to
reconstruct what the engine believed when it made a past call. Version control **is** the
audit trail here.

---

## The trap this design exists to avoid

**A "mark as refreshed" button is a snooze button.**

If an `as_of` date can be reset without the underlying table actually changing, the amber
becomes dismissible, and the date stops being *evidence* and becomes a *claim*. That is
strictly worse than today: right now the date cannot lie, because the only way to move it
is to edit the file.

**User clarified (2026-08-15) that this was never the intent** — by "refresh" they meant
the UI should surface *when a table is due*, not offer a way to dismiss it, and agreed
"everything needs to be tied back to the date."

**Design principle, therefore:**

> The `as_of` date is a **side effect of the data actually CHANGING**, never an independent
> input. There is no control anywhere in the UI that sets a date by itself.

**The rule is about the data changing, not about a write happening — and that distinction
is load-bearing.** A first draft of this doc said "`as_of` is stamped server-side on save,"
which sounds equivalent and is not: open the editor, touch nothing, click Save, and you get
`as_of = today` with a byte-identical roster. That is a snooze button reached through the
sanctioned path, and it satisfies "stamped by the write, never user-supplied" perfectly.
(Caught in review, 2026-08-15.)

**Mechanism that actually enforces it:** stamp `as_of` only on a real payload delta —
compare a content hash of the normalized payload against the stored one.

```
save_reference_table(name, payload):
    normalized = canonicalize(payload)          # sorted buckets, sorted tickers, upper-cased
    if sha256(normalized) == stored_hash(name):
        return NO_CHANGE                        # as_of untouched; UI says "no changes to save"
    write(payload=normalized, as_of=today_et(), payload_hash=...)
```

A save with an unchanged payload is a no-op that leaves `as_of` alone, and the UI says so
plainly rather than silently appearing to succeed. Canonicalization matters: reordering
tickers is not a refresh.

This single rule is what makes the whole feature safe, and it is the thing to check any
future change against.

---

## Three tiers, by risk and value

### Tier 1 — the shelf-life intervals. Safe, low value.

`REFERENCE_SHELF_LIFE_DAYS` / `REFERENCE_HORIZON_MIN_DAYS` as editable numbers.

- **Risk:** none. Pure display policy; changing one only alters when an amber appears.
- **Value:** low. Touched roughly once a year. (All four were set to 90 on 2026-08-15 and
  will likely sit there.)
- **Verdict:** not worth a page on its own. Include only if Tier 2 is built anyway.

### Tier 2 — the reference tables themselves. **This is the actual feature.**

**v1 scope, confirmed with the user 2026-09-01: THREE tables, not two.**
`SECTOR_UNIVERSE` (~88 tickers, scanned daily by Grow Today), `DISCOVERY_UNIVERSE`
(~200, the Movers net — refreshed this same session, commit `242d4a7`), and
**`_SECTOR_CANDIDATES`** (the diversification candidate roster — added after a `planner`
pass found it's "half of today's actual friction," already `reference_shelf`-tracked as
`sector_candidates`, same sector→[tickers] shape) all become editable in the app.
**Bucket/sector STRUCTURE (the label set) is locked in v1 — only ticker membership is
editable.** See Q3 below for why.

This is the tier that solves the stated problem: you curate the list *in the app* when
check ⑤ says it's due, and `as_of` stamps itself truthfully as a consequence. No code
edit, and no way for the date to drift from reality.

**Four candidates were evaluated for this tier and three were rejected — record this so a
future session doesn't re-propose them without new evidence, the same discipline this
project uses for the parked Utilities-sector decision.**

| Candidate | Verdict | Why |
|---|---|---|
| `_SECTOR_CANDIDATES` | **INCLUDE** | Same shape, already shelf-tracked, is literally the table F-242 had to hand-edit. One extra rule: the validator must block adding any ticker not *already* classified in `TICKER_SECTORS` — this is what lets the roster be UI-editable without dragging that dangerous dict along with it (`portfolio.py:1259` already asserts every member here has a matching `TICKER_SECTORS` entry). |
| `NYSE_HOLIDAYS` / `MARKET_CALENDAR_LAST_YEAR` | **EXCLUDE** | Looked like the safest candidate (no tickers, no scoring) — it isn't. `yfinance_provider.py:326` uses it inside a staleness determination (`if _is_trading_day(...) and col.index[-1].date() < today`); a mistyped holiday date can silently mask or manufacture a "data is stale" verdict — the same failure shape as the 2026-07-14 INTC incident this whole fail-loud design exists to prevent. Also a different shape (`KIND_HORIZON`, extend-by-adding-dates) than the ticker-roster editor. If ever built, it's its own phase with its own date-entry editor. |
| `SECTOR_ETF` | **EXCLUDE** | Looked benchmark-only from 2 consumers (`perf_advisor.py:95`, `portfolio.py:772`) — an incomplete check. It also feeds `app.py`'s `leading_sectors` → `daily_briefing._sector_bonus`, which **adds to the buy-candidate ranking score** (additive-only, never a gate, but not purely cosmetic either). Not `reference_shelf`-tracked at all today. Not a v1 candidate; weak even for a later phase. |
| `TICKER_SECTORS` | **EXCLUDE PERMANENTLY** | Not input-set data — it's the classification the macro-suppression gate and concentration/breach math route *through* (`resolve_sector` → `risk.py`'s breach gate; `macro_calendar.py` requires its value set to cover every `TICKER_SECTORS` value). Its failure mode is exactly the silent-omission class behind three separate production bugs this project has now fixed (F-240's BA gap, F-242's F/GM/LCID gap, today's 11-name Semiconductors gap, commit `6a7ef61`). Safely validating a UI edit here means re-implementing the macro-coverage invariant at save time — getting it slightly wrong ships the exact bug the feature exists to prevent. Stays a reviewed code edit, permanently — this is not a "for now" exclusion. |

The parked analyst-email sender allowlist (from the parked email-ingestion plan) is a
reasonable Phase-2-someday tenant for this page — pure input-set data, no policy — but it
can't be scoped until that feature is unparked. One-line future note only, not designed
here.

**A hybrid code-fallback was designed first and then REJECTED. Read this before
re-proposing one.** The original tables are hardcoded on a deliberate rationale — from
[`discovery_universe.py`](../../stock_analyzer/discovery_universe.py): *"a live scrape adds
a runtime dependency and a new failure mode… A static list has zero runtime risk."* The
first draft of this plan honoured that with a hybrid: DB override, hardcoded list as the
floor.

**That was wrong, for three reasons that only became clear on challenge (user, 2026-08-15):**

1. **It is the INTC failure mode.** On 2026-07-14 `load_all` silently fell back to
   `bundle_cache` with 5-day-old data, scored a name ≥ `COMPOSITE_BUY`, and **the app
   recommended a buy on stale inputs** — fresh data scored the same name 32.1, a Sell.
   That incident produced `GROW_TODAY_MAX_FUND_AGE_DAYS`. A silent fallback to a frozen
   scan universe repeats it exactly, on the same buy-candidate surface.
2. **The fallback rots.** Once DB edits are the norm, nobody updates the code list. The
   "safety net" decays into a snapshot that could silently activate years later and scan a
   dead universe with no signal.
3. **The premise doesn't hold.** Supabase is already a hard dependency — no DB means no
   holdings, so no portfolio and nothing to decide. A missing scan universe changes nothing
   that isn't already broken.

**Resolution — the DB is the single source of truth, and unavailability FAILS LOUD.**
This is not a new pattern; it is the one the repo already uses and has already validated
in production:

- **HONEST EMPTY-STATE** (architecture.md §10) — Home *"distinguishes 'no holdings' from
  'holdings exist but all bundles failed' and shows a fail-loud error… never 'enter your
  holdings'."*
- **During the 2026-07-24 Railway migration**, a wrong `SUPABASE_KEY` (publishable instead
  of service-role) produced exactly the desired behaviour: the app showed **no data plus a
  message naming the cause and the fix** (`db.py`'s RLS banner). The user cited this
  unprompted as the safety net they want. It is already proven.

**The invariant is therefore NOT "keep code as truth". It is:**

> **Never silently substitute different data.** Fail loud, or serve stale *and say so*
> (the `bundle_cache` / `stale_as_of` pattern).

When the reference tables can't be read, Grow Today shows **"scan universe unavailable"** —
it does not quietly scan a frozen list. Strictly safer than the hybrid, because it cannot
produce a confident recommendation from stale inputs.

**Consequences of dropping the hybrid — all simplifications:**
- No `from code` / `from DB` badge; there is only one source.
- The dual-resolution bug (payload falling back on *empty*, `as_of` on *no row*) **cannot
  exist** — there is nothing to fall back to. It is designed out, not guarded against.
- **A one-time seed migration** writes the current code lists into the DB, after which the
  hardcoded copies are **deleted, not retained**. Keeping them is what creates the rot.

~~Superseded hybrid resolver, kept only so the rejection is legible:~~

```
resolve_universe(name) -> (payload, as_of, source):
    override = db.load_reference_table(name)   # None on ANY failure (offline sentinel)
    if override is None:      -> (code_list, CODE_AS_OF, "code")   # DB offline / pre-DDL
    if override is empty:     -> (code_list, CODE_AS_OF, "code")   # never scan nothing
    else:                     -> (override.payload, override.as_of, "db")
```

**The actual resolver, post-rejection — one source, no fallback:**

```
resolve_universe(name) -> (payload, as_of)   |   raises ReferenceDataUnavailable
    row = db.load_reference_table(name)      # None on ANY failure (offline sentinel)
    if row is None:        -> UNAVAILABLE    # DB down / pre-DDL / RLS misconfigured
    if row.payload empty:  -> UNAVAILABLE    # never scan nothing and call it a scan
    else:                  -> (row.payload, row.as_of)   # always travel together
```

Callers surface UNAVAILABLE the way Home already surfaces a failed bundle load: a
fail-loud message naming the cause, and **no recommendations** — never a partial or
silent-empty scan that looks like "no opportunities today."

Note the empty-payload case still maps to UNAVAILABLE rather than "an empty universe."
An empty roster is indistinguishable from a broken one, and "scanned nothing, found
nothing" is the single most dangerous output this feature could produce: it looks
identical to a clean bill of health.

**Payload and `as_of` are returned together, never resolved separately.** This is what
designs out the two-independent-paths bug that an earlier draft had — with no fallback
tier, there is no second path for them to disagree on.

**RESOLVED 2026-09-01 — DELETE is the real design; a `planner` re-audit found this doc had
contradicted itself and fixed it.** An earlier version of this section said the hardcoded
list "stays in the repo permanently as the fallback... Supabase down ⇒ the scan behaves
exactly as it does today" — directly contradicting the "deleted, not retained" resolution
two paragraphs above. That "keep" wording was leftover residue from comparing against the
*rejected* hybrid, never cleaned up after the rewrite. Four pieces of evidence resolve it
in favor of DELETE: (1) the entire "consequences of dropping the hybrid" argument above is
one sustained case *for* fail-loud; (2) the actual resolver code block has no fallback
path — it raises; (3) the reviewed mockup (`app-settings-mockup.html:97-99`) says
explicitly "the hardcoded copies are deleted — keeping them as a 'fallback' is what lets a
frozen list silently activate years later"; (4) "preserves zero-runtime-risk" is a verbatim
echo of the exact `discovery_universe.py` rationale this section explicitly rejects one
paragraph earlier ("the premise doesn't hold").

**One real wrinkle DELETE creates, which needs a staged cutover, not a single commit.**
Deleting the code lists in the same commit that ships the new resolver means an unseeded
DB makes Grow Today dead on first deploy — that is not "ships inert." The reconciliation:
the *permanent* design has no fallback, but the *migration* is staged —
1. Ship the resolver + DB tables. Code lists **still exist** and are still what
   `resolve_universe` reads until the DB row is seeded (a transitional read-either path,
   time-boxed, not a permanent hybrid).
2. Seed the DB from the current code lists (a script, one-time).
3. **Verify in production** that the app is reading from the DB (check `as_of`/`updated_by`
   reflect a real DB row, not the code-recorded date).
4. Only then, in a **separate later commit**, delete the code lists and flip
   `resolve_universe` to DB-only, no fallback. This is what "ships inert until its DDL is
   applied" actually means here — inert refers to the DDL/table not existing yet, not to
   the code lists persisting forever as a silent safety net.

- **Risk:** moderate. Touches what Grow Today scans and what the Diversification Advisor
  suggests, i.e. two buy/ADD-candidate funnels. It changes *which names are considered*,
  never *how they are scored* — but that is still a decision-adjacent surface and needs
  `planner` + `reviewer`.
- **Value:** high. Removes the code edit from the recurring workflow entirely.
- **Effort — CORRECTED 2026-09-01, the original "roughly a day" was too low.** A `planner`
  re-audit found the real rewiring surface is far larger than "two call sites": the roster
  is read directly by `scanner.py:181` (`scan_sectors` re-resolves it internally, not just
  at the cron's top-level call), `portfolio.py:1343` (`diversifying_candidate_pool`),
  `ticker_liveness.py:137/143`, `cron_runner.py:857`, and ~6 sites in `app.py`. Realistic
  estimate: **~1.5-2 implementer sessions across 3 reviewed commits** (data layer +
  resolver + seed; rewire every importer + the settings page; the staged cutover) — see
  the phased build plan below.

### Tier 3 — investment thresholds. **Never.** See the redline above.

---

## Sketch (NOT a spec — deliberately incomplete pending a `planner` pass)

**New page** `⚙️ App Settings`, owner-only via the existing `_OWNER_ONLY_PAGES` filter in
`app.py` (same treatment as `🔬 Model Lab` and `🩺 System Trust`) — a read-only viewer must
never see or edit it. Every write goes through the `db.is_readonly()` guard.

**New Supabase table** (one-time DDL, applied by hand, ships inert — same convention as
`model_predictions` / `analyst_target_snapshots`):

```sql
create table if not exists reference_tables (
    name        text primary key,        -- 'sector_universe' | 'discovery_universe' | 'sector_candidates'
    payload     jsonb not null,          -- the bucket -> [tickers] mapping
    as_of       date not null,           -- STAMPED BY THE WRITE, never user-supplied
    updated_by  text,
    created_at  timestamptz default now()
);
```

`as_of` is written server-side from `market_time.today_et()` on save. **There is no UI
control that sets it.** That is the whole design in one line.

**Registry change.** `reference_shelf._REFERENCE_TABLES` currently hardcodes `as_of`
dates, and — corrected 2026-09-01 — **it now tracks SIX tables, not the three this doc's
Tier framing implied**: `sector_universe`, `discovery_universe`, `sp500_sector_weights`,
`sector_candidates`, `macro_event_calendar`, `nyse_calendar`. For a DB-backed table it
would read `as_of` from the DB, falling back to the code-recorded date when the DB has no
row — so an un-migrated table keeps working and keeps telling the truth.

**Open questions for the `planner` pass — ALL RESOLVED 2026-09-01.** A `planner` pass
re-derived every answer against HEAD rather than trusting this doc's 2026-08-17 wording.
Do not re-open these without new evidence.

1. **Editing UX — RESOLVED: `st.data_editor`, one row per bucket, tickers as a
   comma-separated string cell, plus a read-only ticker-count column.** Rejected: a single
   200-symbol textarea (destroys the bucket structure Q3 depends on) and add/remove-one-at-
   a-time (too slow for a refresh that touches 6+ names at once, as `242d4a7` did). A chip
   UI was also considered and rejected — it needs interpolated HTML, which
   `check_antipatterns.py`'s `UNSAFE_HTML_DYNAMIC` rule blocks (confirmed against
   `app-settings-mockup.html:260-263`'s own reasoning).
2. **Validation — RESOLVED: BLOCK the save, never warn-and-save.** A typo'd ticker silently
   narrows the scan — the exact silent-absence failure F-238 exists to catch (memory
   `feedback_data_sanity_validation`). Validate each symbol against the **same provider
   path the scanner actually reads** (Finnhub→yfinance→FMP resolution), not a full bundle
   fetch — too slow for ~200 symbols (memory `feedback_validation_reads_detector_source`).
   **The non-obvious half:** the validator is itself a network call that can fail. On
   validator-offline you cannot distinguish "bad ticker" from "provider down," so the save
   must be **BLOCKED with a fail-loud "couldn't validate, try again," never saved
   anyway** — the offline contract applied to the validator itself, not just the resolver.
3. **Bucket-label coupling — RESOLVED: lock structure in v1, edit membership only.**
   Confirmed live: `portfolio._DIVERSIFY_TO_DISCOVERY` (`portfolio.py:1312`) keys off
   `DISCOVERY_UNIVERSE` bucket labels and `_SECTOR_CANDIDATES` sector keys, consumed at
   `portfolio.py:1343`; `tests/test_reference_shelf.py::test_diversify_map_keys_and_values_resolve`
   guards it in CI but a UI edit bypasses CI. Rather than build a live orphan-guard against
   renames, **the bucket/sector set and labels are read-only in v1 — only ticker membership
   is editable.** You cannot orphan a map key you cannot rename or delete. Structural
   changes (new/renamed/removed buckets) stay a code edit + review. This also resolves half
   of Q8.
4. **History — CONFIRMED REQUIREMENT, with a concrete sketch.** The redline's justification
   for keeping Tier 3 in code ("no way to reconstruct what the engine believed when it made
   a past call") applies verbatim to a mutable scan universe. Design: an **append-only**
   table, `reference_table_history` — every accepted delta inserts a row; "current" = the
   latest row per `name`. Columns: `name`, `payload jsonb`, `as_of date`, `payload_hash
   text`, `updated_by text`, `created_at timestamptz default now()`. **Retention: keep
   all** — a JSON roster is a few KB, edits happen ~4×/year/table, pruning buys nothing for
   years. A "restore a prior payload" UI button is a nice-to-have, deferred; the *read*
   (who changed what, when) is the actual requirement.
5. **`SP500_SECTOR_WEIGHTS` — RESOLVED: later phase, not v1.** Confirmed a genuinely
   different shape (`portfolio.py:817` — `{GICS-sector-name: float}`, must sum ≈ 100),
   feeding `portfolio_vs_sp500` (`portfolio.py:888-898`, display/awareness, not a gate). Its
   stale failure mode is a **wrong number**, not silent omission — a higher validation bar
   (sum-to-100 check) and its own numeric editor, not the ticker-roster grid. It IS already
   `reference_shelf`-tracked, so it's a legitimate Phase-2 candidate — just not v1.
6. **The `_STATIC` macro calendar — RESOLVED: exclude, and more firmly than originally
   thought.** Re-verified as materially more decision-bearing than a scan universe:
   `daily_briefing._grow_today` imports `macro_calendar` directly (`daily_briefing.py:805`)
   and builds `_macro_blocked_sectors` from HIGH-impact events, which **suppresses new
   picks** (the G-07 gate); the cron alert lane surfaces those macro-gated picks
   (`cron_runner.py:962`). Editing an event's date/severity moves *whether a suppression
   gate fires* — that trips the redline's prong 1 (changes the decision rule), not merely
   the input set. **Stays a reviewed code edit, permanently** — at most a read-only "what's
   expiring soon" display on this page, never an editor.
7. **The cron lane reads the universe DIRECTLY — RESOLVED 2026-08-16 by F-239, RE-VERIFIED
   2026-09-01, still accurate but the wiring is deeper than originally scoped.**
   `cron_runner.py:855-857` still does `from stock_analyzer.scanner import scan_sectors,
   SECTOR_UNIVERSE` → `scan_sectors(list(SECTOR_UNIVERSE.keys()), period="6mo")`, and
   F-239's `_handle_db_unavailable` is present in that lane (`cron_runner.py:877`) —
   verified live 2026-08-16. **What's new:** `scan_sectors` itself re-reads `SECTOR_UNIVERSE`
   *internally* to resolve tickers (`scanner.py:181`), so `resolve_universe` must thread a
   payload dict into `scan_sectors`, not just replace the cron's top-level argument — the
   real importer list is `scanner.py:181`, `portfolio.py:1343`, `ticker_liveness.py:137/143`,
   `cron_runner.py:857`, plus ~6 `app.py` sites, not "two call sites."
8. **Replace vs merge semantics — RESOLVED: wholesale replace, with a confirmation step.**
   Merge can't express a deletion, so replace is correct — the payload IS the roster.
   Because Q3 locks the bucket set, the doc's original fear ("3 of 12 buckets populated
   clears the empty-payload floor and wholesale-replaces") can't happen the same way — you
   can only *empty* a bucket, and emptying one to zero should itself require confirmation
   (that sector stops being scanned/considered). **The confirmation threshold is an
   operational/observability knob, not a decision value** — it gates a UI confirmation
   step, never a pick — so it gets a named `constants.py` entry documented the same way
   `TICKER_LIVENESS`'s floor is ("OBSERVABILITY KNOB — NOT an investment threshold"), value
   set with the user at build time.

---

## What NOT to do

- **No date-setting control anywhere.** See "the trap" above.
- **No decision thresholds.** See the redline.
- **Don't delete the hardcoded lists in the SAME commit that ships the resolver** —
  **corrected 2026-09-01, this bullet previously said the opposite** ("don't delete the
  hardcoded lists... they are the fallback"), which was the self-contradiction resolved
  above. The lists DO get deleted eventually — that's the permanent design — but only in a
  separate, later commit, after the DB seed is verified in production. Deleting them in the
  same commit as the resolver turns an unseeded DB into a broken Grow Today on first deploy.
- **Don't let a UI edit skip validation** because "the user typed it deliberately." A typo
  is not a decision.
- **Don't include `TICKER_SECTORS`, `SECTOR_ETF`, or `NYSE_HOLIDAYS` in this feature** — all
  three were evaluated and rejected 2026-09-01 (see the candidate table under Tier 2). Don't
  re-propose without new evidence, the same discipline as the parked Utilities-sector
  decision.

---

## Build plan — phased, per the 2026-09-01 planner pass

Three reviewed commits, each independently shippable:

1. **Data layer + resolver + seed.** DDL for `reference_tables` (current row per name) +
   `reference_table_history` (append-only, Q4). `db.load_reference_table` /
   `save_reference_table` (`None` on any failure — the offline sentinel). `resolve_universe(name)
   -> (payload, as_of) | raises ReferenceDataUnavailable`, with the empty-payload→UNAVAILABLE
   rule. Content-hash `as_of` stamping (canonicalize: sort buckets, sort+upper-case
   tickers) — the snooze-button invariant from "the trap" above. `reference_shelf`'s
   registry reads `as_of` from the DB with a code-date fallback for un-migrated rows. A
   one-time seed script. **Code lists still exist and are still what's read** during this
   phase — this is the transitional step in the staged cutover, not the final state. New
   module `stock_analyzer/reference_data.py` (a NEW module, not grown inside an existing
   `_GATE_FILES` one, per the extract-into-new-module convention already used for
   `outage_gate.py`/`coord_freshness.py`).
2. **Rewire every importer + build the `⚙️ App Settings` page.** Thread the resolver into
   `scanner.py:181`, `portfolio.py:1343`, `ticker_liveness.py:137/143`,
   `cron_runner.py:857` (UNAVAILABLE → the existing `_handle_db_unavailable`), and the ~6
   `app.py` sites. Owner-only page via `_OWNER_ONLY_PAGES`, every write behind
   `db.is_readonly()`; the `st.data_editor` grid (membership-only, structure locked);
   provider-resolve validation with the offline-block branch; content-hash save states;
   history readout; large-drop/empty-bucket confirmation.
3. **Staged cutover: flip to DB-only + delete the hardcoded rosters.** Only after step 1's
   seed is verified in production (real `as_of`/`updated_by` on the DB row, not the code
   date). Deletes `SECTOR_UNIVERSE`'s/`DISCOVERY_UNIVERSE`'s/`_SECTOR_CANDIDATES`' code
   definitions from `scanner.py`/`discovery_universe.py`/`portfolio.py` — this is "deleted,
   not retained" made real, safely.

**Tests each commit must include** (invariants, not just reasoning — see the full list the
planner pass produced): `resolve_universe` raises on `None` AND on empty payload;
payload+`as_of` always travel together; a byte-identical-after-canonicalization save is a
no-op (`as_of` unmoved) — the single load-bearing boundary test; a real 1-ticker delta
moves `as_of`; an unresolvable symbol blocks the save; a validator-offline blocks the save
(not saved-anyway); a structure-changing save (bucket rename/add/remove) is rejected; a
`_SECTOR_CANDIDATES` addition whose sector doesn't resolve in `TICKER_SECTORS` is rejected;
cron UNAVAILABLE routes through `_handle_db_unavailable`; an import-isolation test proving
no decision-path module reads the rosters directly after the cutover (the F-259b
"risk.py missed from the file list" mistake, applied here); history appends on every
accepted delta; the large-drop confirmation fires exactly at its constant's boundary.

## Routing when this is built

- 🔴 **planner (Opus)** — DONE, 2026-09-01. All 8 open questions resolved, the
  self-contradiction fixed, v1 scope confirmed at 3 tables. Nothing left to scope before
  code.
- 🔵 **implementer (Sonnet)** — the 3-commit build above, once a session is allocated.
- 🔴 **reviewer (Opus)** — required on EVERY commit above: each touches `db.py` and/or a
  `_GATE_FILES` module (`scanner.py`, `portfolio.py`, `cron_runner.py`) and/or a new
  decision-adjacent user-facing surface. Cite per Hard Rule #4.
- 🟢 **doc-writer (Haiku)** — architecture DDL row + requirements F-row, after facts pinned
  by the build (not before — don't let doc-writer invent DDL ahead of the real schema).

## Trigger to build

**Design is fully resolved and ready. User explicitly chose to HOLD rather than start
building, 2026-09-01** — the friction trigger (real refresh cycles: `242d4a7`, `6a7ef61`)
has technically fired, but there's no urgency to act on it immediately. Revisit whenever a
session is allocated to it; nothing further needs to be decided first. Check ⑤ continues
to tell you when a table is due in the meantime, so the manual-edit workflow this feature
would replace remains fully functional while this sits parked.
