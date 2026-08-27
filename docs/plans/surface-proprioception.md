# Surface Proprioception (F-260) — audit first, then decide

**Status: CHUNK 1 IN PROGRESS 2026-08-27.** The sweep is built and committed
(`scripts/sweep_coordination_reads.py`, commit `ea7e59b`); classification is under way.
Nothing is built beyond the sweep. **Chunk 2 is deliberately un-chosen until Chunk 1's
count is known**, against thresholds pre-registered below *before* the count existed.

Design source: [docs/reviews/2026-08-26-app-review.md](../reviews/2026-08-26-app-review.md)
Part 3 Innovation #3, plus its Part 2 #2. Opus `planner` design pass 2026-08-27, verdict
**BUILD WITH CHANGES**. The change is the ordering — see §2.

---

## 1. Why

F-235 gave the app proprioception for its **pipelines**: did each cron lane fire, is each
store fresh. It has none for its **surfaces**: did this card's inputs actually exist when it
rendered. F-258 fixed four known instances by hand. The open question is whether the rest of
the class is a real risk or a theoretical one.

The house contract is that a producer sets its coordination cache to `None` on failure and to
an empty container when it checked and found nothing — so a consumer that collapses `None`
into `{}`/`[]` converts *"the check failed"* into *"nothing to report"*. That is the app's
self-declared worst failure mode, and it is the class `check_antipatterns.py`'s
`OFFLINE_SENTINEL_COLLAPSE` and `SENTINEL_BARE_TRUTHINESS` rules exist to stop growing.

---

## 2. The one substantive departure from the review

The review proposes instrumenting **frequency** — a counter of how often a surface renders on
unverified inputs, watched for a month. The `planner` pass argued, and the user accepted
2026-08-27, that **severity must be audited first**:

- **Frequency is only decision-relevant where severity > 0.** A count of "4 surfaces rendered
  blind this session" is unactionable if you cannot tell whether those 4 were a memo cache-key
  hash or a gate checklist asserting a gate passed.
- **The audit dissolves the constraint the counter negotiates with.** CLAUDE.md forbids
  "33 untested edits to `app.py`". A read-only audit landing as a tested registry module plus
  a static gate is not an edit to `app.py` at all — and it is a more direct measurement than
  the counter, which is what "measurement, not patching" actually asked for.
- **It may make the counter unnecessary.** If the claim-bearing set is small, ~N scoped guards
  close the class outright, which is strictly better than instrumenting for a month and then
  still having to fix them.

---

## 3. Two corrections to prior counts, both verified in code

Recorded because this class has now been mis-enumerated three times, and each correction
changes what the audit must cover.

1. **CLAUDE.md's "33" did not match its own breakdown**, which summed to **29**. The dropped
   page is 📅 **Economic Calendar**, which has exactly 4 unguarded `_port_df_enriched` reads.
   The total was right; the enumeration was not. Fixed in CLAUDE.md 2026-08-27.
2. **The `or {}` / `or []` / `.get(k, {})` filter excluded the highest-severity shape in the
   set.** `.get("_port_df_enriched", pd.DataFrame())` is the exact form behind the F-258
   defect, and `float(st.session_state.get("_portfolio_value", 0) or 0)` hands
   `position_size_discipline` a **$0 budget** and then grades positions against
   `SINGLE_NAME_CEILING` on it. Neither contains `or {}`. **So the inclusion rule is keyed on
   what the falsy branch RENDERS, never on which default token was typed.**

**Measured by the sweep, superseding every earlier figure:** 154 reads of the 38 documented
keys in `app.py`; **39** on producer pages (never findings); **115** on consumer pages across
21 pages. Forms include 11 `DataFrame()` defaults, 3 `0.0`, 2 `0` and 1 `False` that no prior
count could see.

**A third flaw, found while applying the guard filter and load-bearing for classification: a
hard PORTFOLIO guard does not protect a NON-portfolio key.** 58 of the 115 sit on pages that
call `_render_portfolio_not_loaded` + `st.stop()` within 22 lines of page start — but
`_port_risk_cache`, `_reduce_calls`, `_risk_high_alerts_cache` and `_grow_composites` have
*different producers* and can be `None` on a page whose portfolio loaded fine. 🔗 Risk
Analysis clears its `port_df` guard and then reads `_port_risk_cache or {}`. "The page has a
hard guard" is therefore **not** sufficient grounds to call a read safe.

---

## 4. The inclusion rule

A `(page, cache_key)` pair is a **finding** iff all three hold:

1. **Not self-produced.** The page is not that key's producer. Producers: 🏠 Home (34 keys),
   🎯 My Edge (`_mirror_*`), 🧩 Intelligence (`_pi_factor_tilt_cache`), and 📒 Trade Journal as
   a *secondary* producer via `_refresh_portfolio_cache_after_trade`. A producer legitimately
   holds its own key unset before publishing.
2. **The falsy branch makes or withholds a claim** — it (a) renders a confident or itemised
   negative; (b) omits a suppression, warning or annotation that would otherwise fire;
   (c) feeds a number the page displays as fact; (d) feeds a sizing/gate computation on that
   page; or (e) writes a persisted row with a silently-absent field.
   **Excluded:** memo/cache-key components, self-healing fallbacks, optional adornments, and
   reads whose empty result is self-evident to the user.
3. **The page does not already handle it** — no `_coord_cache_state` guard, no `is None`
   branch, no existing empty-state copy, and no hard guard *covering that specific key*.

**An empty `[]` / `{}` / empty DataFrame that the producer did publish is `ready`, never a
finding.** That is the whole point of the `None` contract, and it is the invariant that keeps
any future count from becoming noise.

Condition 2 is irreducibly a judgment call and cannot be regexed. That is precisely why it is
written down once, as a reviewable table with a one-line rationale per row, in a module tests
can import — and why an `_EXCLUDED` companion table carrying the *reason* for each exclusion
is not padding: it is what stops the next review re-finding these sites and re-litigating them.

---

## 5. PRE-REGISTERED — the Chunk 2 branch, set with the user 2026-08-27 BEFORE the count was known

Let **N** = the number of CLAIM-classified findings from Chunk 1.

| N | Action |
|---|---|
| **N ≤ 5** | **Fix them; do NOT build the counter.** One F-258-style commit adding `_coord_cache_state` guards at the N sites, each with its rationale in the diff, plus a per-arm test at the exact boundary each guard claims. Then close the queue item. |
| **6 ≤ N ≤ 15** | **Build the counter to prioritise**, then fix top-ranked findings as they arrive. Needs the persisted store (see §6) or its exit condition is unfalsifiable. |
| **N > 15** | **The class is structural — stop.** The answer is the app review's Part 2 #3 (extract render-layer gate decisions into pure modules), not instrumentation. Do not build the counter. |

Setting these afterwards would be writing a post-hoc justification for whatever number came
back. They are recorded here rather than in `constants.py` deliberately: they gate no
recommendation and move no money, so they are build-routing anchors, not investment policy —
the same treatment `docs/plans/gate-suppression-ledger.md` §5 gives its retirement criterion.

**Chunk 3 is independent of the branch and worth noting because it removes one of the
counter's two stated justifications:** extend `check_antipatterns.py` with a registry-aware
rule so a NEW qualifying read on a non-producer page that appears in neither `_SURFACES` nor
`_EXCLUDED` fails the gate. That is the regression detector the review wanted, and it needs
**no runtime code at all**.

---

## 6. If the counter is built (6 ≤ N ≤ 15) — the constraints that hold regardless

- **Session-scoped cannot discharge the exit condition.** "A month of ordinary use" is not
  answerable by a line the owner is expected to eyeball for 30 sessions — that recreates the
  "habit, not mechanism" failure this very review calls ceremony. So the counter needs a store,
  and the store needs a **denominator** (sessions observed), or "0 findings" is unfalsifiable.
- **DDL is manual and the feature ships inert** — precedent set three times
  (`model_predictions`, `broker_position_snapshot`, `gate_suppressions`). The table must be
  registered in `system_health._INVENTORY` as existence-only so an unapplied DDL renders **red**
  rather than silently making the readout `0`; and the loader must return **`None`** on failure
  with the render branching on `is None`. **A feature that measures confident false negatives
  must not be able to produce one.**
- **Top-of-run sampling makes the count a strict UPPER BOUND** — on a non-producer page a key
  can only go missing→ready mid-run, never the reverse. That is the right direction: if the
  upper bound is 0, the true count is provably 0. The copy must say "upper bound", not imply N
  confident false negatives occurred.
- **2 `app.py` edits, not 52.** Cache state is page-global — `_reduce_calls` has identical
  state at all three 📈 Analysis read sites — so per-site instrumentation buys nothing at
  observation time. Per-site granularity lives in the registry's labels, not in the code path.
- **It must not reach the chip or the "All systems nominal" banner.** Check ⑤ needed a
  dedicated `_ref_degraded` branch precisely because a page-only check that can reach `warn`
  renders amber rows under a green headline. So ⑥ emits no `warn`/`down` at all. Cosmetically
  inconsistent with ①–⑤, deliberately: it is the only structural guarantee that "changes no
  decision" holds.
- **`system_health.py` is a `_GATE_FILES` member** ⇒ mandatory Opus review citation for that
  chunk. The reviewer's specific job: chip-invariance and the `None`-on-failure branch.
- **Zero new `constants.py` entries in every branch.**

---

## 7. Exit condition — PRE-REGISTERED, and retiring is a success

**If the counter is built:** review on **2026-09-30**; if findings == 0 across **≥ 20 observed
sessions**, delete `surface_trust.py`'s observation path, the ⑥ block and the writer, and drop
the table. The registry and the Chunk 3 gate rule stay — they cost nothing at runtime and
prevent regrowth.

The session floor is load-bearing: without a denominator, "0" is indistinguishable from "the
instrumentation never ran," which is the same checked-vs-not-checked ambiguity this whole
feature exists to expose.

**Deleting the instrumentation is the SUCCESS condition of this criterion, not a failure of
the project.** Do not soften it later to save the feature.

---

## 8. Known hazard

**Registry drift.** A hand-curated table describing `app.py` is exactly the artefact this
project has been bitten by before. The bidirectional drift test is not optional: every registry
row's key must be present in that page's line range, AND no unregistered qualifying hit may
exist. Mutation-test it — deleting a registry row must make the test fail, not silently pass.

---

## 9. Docs to sync on ship (Definition of Done)

1. No `constants.py` entry in any branch — nothing to document there.
2. `docs/requirements.md` F-260, plus §3.1k's check count if ⑥ ships.
3. `docs/shipped-log.md`; rewrite CLAUDE.md's "33 collapsing reads" queue item with the
   classified result.
4. **In-app User Guide says "Five checks"** — must change if ⑥ ships.
5. Memory `project_surface_proprioception`; update `feedback_recurring_defect_gate`.
6. The dated exit checkpoint (§7) into CLAUDE.md's queue — **mandatory**, this is exactly the
   gate-lives-only-in-memory failure DoD #6 exists for.
7. This file's `**Status:**` line.

Plus `docs/architecture.md` §6.x DDL and a `system_health.py` module note if ⑥ ships.
