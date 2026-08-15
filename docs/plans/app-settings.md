# App Settings — UI-managed reference data

**Status: DESIGN ONLY — 2026-08-15. No code written. Explicitly "design it first,
build later" per the user.**

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

`SECTOR_UNIVERSE` (~70 tickers, scanned daily by Grow Today) and `DISCOVERY_UNIVERSE`
(~200, the Movers net) become editable in the app.

This is the tier that solves the stated problem: you curate the list *in the app* when
check ⑤ says it's due, and `as_of` stamps itself truthfully as a consequence. No code
edit, and no way for the date to drift from reality.

**The cost, which the original author deliberately avoided.** From
[`discovery_universe.py`](../../stock_analyzer/discovery_universe.py):

> *"Why hardcoded rather than scraped: a live scrape adds a runtime dependency and a new
> failure mode for a list that barely changes week to week. A static list has zero runtime
> risk; the cost is a manual refresh a few times a year."*

DB-backing reintroduces exactly that failure mode. **Resolution — hybrid, code as the floor:**

```
resolve_universe(name) -> (payload, as_of, source):
    override = db.load_reference_table(name)   # None on ANY failure (offline sentinel)
    if override is None:      -> (code_list, CODE_AS_OF, "code")   # DB offline / pre-DDL
    if override is empty:     -> (code_list, CODE_AS_OF, "code")   # never scan nothing
    else:                     -> (override.payload, override.as_of, "db")
```

**One resolver returns the payload AND its date together — this is not a style
preference.** A first draft resolved them on two independent paths: the payload fell back
to code when the override was *empty*, while the registry fell back to the code date only
when there was *no row*. An empty-but-present row satisfies those differently, so the scan
would run on the **stale hardcoded roster** while check ⑤ reported it **freshly refreshed** —
silent absence wearing a green badge, i.e. precisely the failure class F-238 exists to
catch, reintroduced by two individually-sensible fallbacks. (Caught in review, 2026-08-15.)

**Invariant: whichever payload wins must carry its own date.** The code list always travels
with the code-recorded date; the DB payload always travels with the DB date. Check ⑤ grades
whatever actually won, and the row should name the `source` so "which list am I even
looking at?" is never a guess.

The hardcoded list stays in the repo permanently as the fallback. Supabase down ⇒ the
scan behaves exactly as it does today. This preserves zero-runtime-risk while enabling UI
editing — and it means the feature can ship inert (no DDL applied ⇒ nothing changes),
matching the house "ships inert until its DDL is applied" convention.

- **Risk:** moderate. Touches what Grow Today scans, i.e. the buy-candidate funnel. It
  changes *which names are considered*, never *how they are scored* — but that is still a
  decision-adjacent surface and needs `planner` + `reviewer`.
- **Value:** high. Removes the code edit from the recurring workflow entirely.
- **Effort:** roughly a day including tests and docs.

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
    name        text primary key,        -- 'sector_universe' | 'discovery_universe'
    payload     jsonb not null,          -- the bucket -> [tickers] mapping
    as_of       date not null,           -- STAMPED BY THE WRITE, never user-supplied
    updated_by  text,
    created_at  timestamptz default now()
);
```

`as_of` is written server-side from `market_time.today_et()` on save. **There is no UI
control that sets it.** That is the whole design in one line.

**Registry change.** `reference_shelf._REFERENCE_TABLES` currently hardcodes `as_of`
dates. For a DB-backed table it would read `as_of` from the DB, falling back to the
code-recorded date when the DB has no row — so an un-migrated table keeps working and keeps
telling the truth.

**Open questions for the `planner` pass** (do not guess these at build time):

1. **Editing UX for ~200 tickers.** A `st.data_editor`? A textarea of comma-separated
   symbols per bucket? Add/remove one at a time? This is the bulk of the real work and the
   thing most likely to be unpleasant if designed badly.
2. **Validation.** A typo'd ticker silently narrows the scan — the exact silent-absence
   failure F-238 exists to catch. Validate symbols resolve against the provider layer
   before save, per memory `feedback_data_sanity_validation`. What happens to a symbol that
   fails validation — block the save, or save with a warning badge?
3. **Bucket-label coupling.** `portfolio._DIVERSIFY_TO_DISCOVERY` is keyed on **both**
   universes' bucket labels. Renaming a bucket in the UI would silently degrade
   diversification suggestions. `tests/test_reference_shelf.py::test_diversify_map_keys_and_values_resolve`
   catches this in CI, but a UI edit bypasses CI entirely — so the UI itself must block a
   rename that orphans the map.
4. **History — this is a REQUIREMENT, not an open question.** (Upgraded in review.) The
   redline above justifies keeping Tier 3 in code on the grounds that otherwise there is
   *"no way to reconstruct what the engine believed when it made a past call."* That
   applies verbatim to a mutable scan universe: without append-only history, Tier 2 breaks
   the exact property the redline invokes to protect Tier 3. An `as_of`-keyed history
   table, not a single mutable row. The only open part is retention.
5. **Does `SP500_SECTOR_WEIGHTS` belong here too?** It's numeric reference data, not a
   ticker list, so it needs a different editor. Possibly a later phase.
6. **The `_STATIC` macro calendar.** Tempting to include (it needs periodic hand-entry, and
   BLS/BEA 2027 dates are outstanding as of 2026-08-15) — but it feeds
   `daily_briefing._act_today` and the cron alert lane directly, making it materially more
   decision-bearing than a scan universe. Recommend **excluding from v1** and revisiting.
7. **The cron lane reads the universe DIRECTLY — decide this before any code.** (Added in
   review.) `cron_runner.py` imports `SECTOR_UNIVERSE` and passes
   `list(SECTOR_UNIVERSE.keys())` to `scan_sectors`. If the app reads a DB override and the
   cron does not, the 9:45 ET morning-picks email and in-app Grow Today silently scan
   **different universes**, with no banner saying so — on a surface that has already had a
   dead-email incident (memory `project_morning_picks_cron_bug`). Needs: does the cron read
   the override, and what does it do if Supabase is unreachable mid-lane? The `resolve_*`
   helper must be the single entry point for BOTH, or this diverges by construction.
8. **Replace vs merge semantics.** The "empty payload" floor doesn't cover a *semantically
   truncated* one: a save with 3 of 12 buckets populated is non-empty, clears the floor, and
   wholesale-replaces the roster. Decide explicitly whether a save replaces or merges, and
   whether a large drop in bucket/ticker count needs a confirmation step.

---

## What NOT to do

- **No date-setting control anywhere.** See "the trap" above.
- **No decision thresholds.** See the redline.
- **Don't delete the hardcoded lists** when the DB layer lands — they are the fallback, and
  deleting them turns a Supabase outage into a broken Grow Today.
- **Don't let a UI edit skip validation** because "the user typed it deliberately." A typo
  is not a decision.

---

## Routing when this is built

- 🔴 **planner (Opus)** — resolve the six open questions above before any code. This
  touches what the buy-candidate funnel considers.
- 🔵 **implementer (Sonnet)** — the page and DB functions, once the spec is settled.
- 🔴 **reviewer (Opus)** — required: DB-write + a new user-facing surface + decision-adjacent
  data. Cite in the commit per Hard Rule #4.
- 🟢 **doc-writer (Haiku)** — architecture DDL row + requirements F-row, after facts pinned.

## Trigger to build

User's explicit go-ahead. Nothing here is urgent: check ⑤ already tells you when a table is
due, and editing a Python list four times a year is a small cost against a settings
subsystem's ongoing one. **Build this when the code-edit friction actually bites** — i.e.
after a couple of real refresh cycles — not before.
