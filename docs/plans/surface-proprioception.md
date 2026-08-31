# Surface Proprioception (F-260) — audit first, then decide

**Status: the 7 previously-skipped pages are now audited — 4 clean, 3 more findings CLOSED 2026-08-31 (commit `7faf729`).** Both earlier rounds (10 ranked + 5 more) had explicitly assumed 📈 Analysis, 📒 Trade Journal, 🪞 Trade Review, 📅 Economic Calendar, 🎯 My Edge, 🧑‍⚖️ The Judge, and 📋 Watchlist were already covered, without independently re-verifying. A full re-audit of all 7 (every `elif page ==` block read top to bottom, not sampled) confirmed 🪞 Trade Review, 📅 Economic Calendar, 🎯 My Edge, and 🧑‍⚖️ The Judge are genuinely CLEAN, and found 3 real findings on the other 3: (1) 📋 Watchlist's portfolio-beta risk gate (both the hard ceiling downgrade and the soft caution on ENTER_NOW recs) silently disabled whenever `_port_risk_cache` went offline — confirmed LIVE, not theoretical, since that cache's producer has its own independent try/except uncoupled from `_daily_brief_offline`; (2) 📒 Trade Journal's post-BUY concentration check silently skipped with zero disclosure when the portfolio snapshot wasn't loaded; (3) 📈 Analysis's HOLD-tab stop-ladder could show "keep climbing" nudges on a position under an active Reduce/Exit call if that cache couldn't be verified. All disclosure-only, Opus review SHIP 0 blocking. **This closes the "7 unaudited pages" item — the only genuinely open F-260 work left is Phase 3** (§11/§12 item 2, lifting Home's risk/fragility/correlation producer block into a pure module), which needs its own `planner` pass and was never part of any of these audit rounds.

**Status: 5 MORE findings CLOSED 2026-08-30 (later session, commit `f707e31`), of the ~15 that were never individually itemized.** A fresh investigation (re-derived against current code, not assumed from the loose one-sentence description below) found the true remaining count in the 4 named areas was **5, not ~15** — several of the loosely-described items had already been closed by unrelated work (the `_acct_gate_cache` dead-branch fix, the coord_freshness banner rollout) without ever being checked off here. The 5: (1) 🔗 Risk Analysis's whole Portfolio Risk Dashboard vanishing with no `else` when `_port_risk_cache` is falsy; (2) the same page's leverage/margin warning never reading the producer's `cash_seen` field, so "never measured" rendered identically to "confirmed unlevered"; (3) 🥧 Portfolio Overview's News Intelligence tab and (4) its Rebalancer tab both feeding an unverified `_reduce_calls` into an existing suppression parameter without disclosing when the cross-check couldn't run; (5) 🔔 Catalyst Watch's 🔥 leading-sector flag going STALE (not absent) because the Daily-Brief-crash reset block nulled its siblings `_grow_today_sectors_cache`/`_reduce_calls` but never `_leading_sectors_cache`. All disclosure-only (no gate/threshold/recommendation change); Opus review SHIP, 0 blocking. Full per-finding detail and the reviewer's two non-blocking notes (a stale "sole writer" claim, and a separate pre-existing memo-hit-rebuild staleness gap on the same key family — not fixed, out of scope) in the commit body. **Did NOT re-audit** 📈 Analysis, 📒 Trade Journal, 🪞 Trade Review, 📅 Economic Calendar, 🎯 My Edge, 🧑‍⚖️ The Judge, or 📋 Watchlist (assumed covered by the closed top-10 + the coord_freshness rollout, per the investigation's own scope note) — a small remainder could still exist there.

**Status: all 10 individually-ranked §10 findings CLOSED 2026-08-30.** Findings
#1 and #3 were already fixed before this pass (the fabricated-$50k sizing fix and
the Analysis `_reduce_calls` guard, both pre-existing). This session closed the
remaining 8 — #2, #4, #5, #6, #7, #8, #9, #10 — across 7 commits (some findings
shared one root cause and landed together: #6/#9 turned out to be the SAME dead
"levered" branch, not two separate cache-collapse bugs — see the #6 note below).
Every fix went through Opus review before commit; one (#8) came back FIX-FIRST on
the first pass and was revised and re-reviewed SHIP. **The remaining ~15 of the
25 total CLAIM findings were never individually itemized in §10** (grouped there
as "and annotation-level omissions") — closing them, if ever picked up, needs its
own fresh pass rather than continuing this list. Full detail per finding below.

**Status: CHUNK 1 COMPLETE 2026-08-27 — N = 25 CLAIM findings, which trips the
pre-registered `N > 15` branch: THE COUNTER IS NOT BUILT.** The class is structural.
Chunk 3 (the registry-aware gate) and the ranked fixes proceed; the instrumentation
does not. See §10 for the classified result and §11 for the leverage point.

**2026-08-28 — the §10 factor-tilt finding is SHIPPED** (requirements.md F-260;
Opus review SHIP, 0 blocking). It landed as a pure, tested module
(`util.factor_tilt_evidence_line` / `factor_tilt_state`) rather than as edits to
`app.py`'s render layer, which is what §5's `N > 15` branch actually asked for.

**2026-08-28, same day — §12 item 2 (the `_refresh_portfolio_cache_after_trade`
republisher) is SHIPPED, Phases 0 and 1** (requirements.md F-260a / F-260b; Opus
`planner` design pass then Opus review SHIP, 0 blocking). **Phase 2 was descoped
by an explicit user decision**: on a stale suppressor cache the app DISCLOSES
rather than withholds, so the fail-closed `risk.sizing_unavailable_reason`
extension is not built and no new policy constant was introduced.

**§12 item 3 SHIPPED 2026-08-28** as a registry-drift guard in
`tests/test_coord_freshness.py` rather than an AST rule in
`check_antipatterns.py` — the real risk was never a mis-shaped write, it was a
portfolio-derived cache MISSING from `PORTFOLIO_DEPENDENT_KEYS`, which reads as
permanently fresh and which nothing else would surface. Every key in
`check_antipatterns._SENTINEL_KEYS` must now be classified: tracked, refreshed
by the republisher, not portfolio-derived, self-invalidating, or explicitly
listed as a known gap. Adding a cache without classifying it fails the suite.

**It immediately exposed a real gap — now CLOSED 2026-08-28, same day.** The
guard found **14 portfolio-derived caches not tracked for freshness** — `_actions_cache`,
`_alert_list_cache`, `_div_recs_cache`, `_risk_high_alerts_cache`,
`_risk_advisor_recs_cache`, `_dpnl_cache`, `_day_shock_cache`,
`_grow_today_sectors_cache`, `_grow_composites_coverage`, the three `_mirror_*`
keys, `_pi_factor_tilt_cache` and `_broker_drift_cache`. They go stale after a
trade exactly like the tracked ones. The Phase 1 registry covered the seven
siblings the §11 analysis enumerated plus their derived keys — it was never a
complete census, and the guard is what made that visible.

**Resolution: 11 tracked, 3 reclassified, and the measurement caught a bug the
naive fix would have introduced.** Mapping every write and read in `app.py`
showed **4 of the 14 are NOT produced by 🏠 Home** — the three `_mirror_*` keys
come from 🎯 My Edge and `_pi_factor_tilt_cache` from a BUTTON on 🧩
Intelligence. Home's blanket `_stamp_coord()` would have marked all four fresh
merely because Home ran, certifying pre-trade data as current: the same
freshness LAUNDER the review caught on the memo-HIT path, about to be
reintroduced by the fix meant to close the gap. So the registry now carries a
**`producer`** field and each page stamps only its own keys
(`coord_freshness.keys_for_producer`), with a test asserting Home never claims
the four it does not produce. The remaining **3** (`_day_shock_cache`,
`_grow_composites_coverage`, `_broker_drift_cache`) are read only by the page
that produces them, so there is no cross-surface staleness to report; they sit
in a named `_SELF_CONSUMED_BY_PRODUCER` bucket rather than counting as debt.
The per-surface map was **regenerated by measurement**, not hand-edited.
Registry: 14 -> 25 keys. Untracked: 14 -> 0, pinned by a test.

**🧑‍⚖️ The Judge — CLOSED 2026-08-28.** It now calls
`_render_portfolio_stale_banner(key_suffix="judge")` with
`SURFACE_KEYS["judge"] = ("_reduce_calls",)`. The scope was **re-measured, not
carried over from this paragraph**: a scan of the whole page block (app.py
10757–11090) found `_reduce_calls` at three lines and no other registry key, so
the one-key mapping is a measured claim rather than an inherited one. Because
`_reduce_calls` is GATE tier, a stale one escalates to a warning carrying the
suppression clause — correct here, since the audit's entire output is a
comparison against that reduce set. The behaviour is pinned by extending
`test_a_surface_reading_a_suppressor_still_warns` to `judge`, and that
assertion was **mutation-checked** (setting the mapping to `()` fails it), per
the "a gate whose broken state is GREEN is worse than no rule" scar.

**📋 Watchlist — CLOSED 2026-08-28, and it exposed a defect in the mechanism
itself.** The banner now sits directly after the `_wl_brief_offline` block, so
both disclosures precede the recommendations they qualify. Scope re-measured:
**three** keys, not the two this paragraph used to claim — `_port_risk_cache`
was missing, and never showed as a gap because it is mapped on other surfaces,
so only walking the page block found it. The gap was also wider than "one
gate-tier cache": Watchlist sizes real positions against `_portfolio_value` and
had **no banner call at all**, so it silently omitted a crashed post-trade
refresh, a data outage, dropped holdings and changed holdings as well.

**The Opus review then found a Phase-1 defect this change made reachable.**
`_stamp_coord` stamped on PRESENCE, but `None` is the offline sentinel for every
registry key and a published sentinel is present — so a FAILED producer was
stamped, and after the next epoch bump read as **STALE instead of ABSENT**. On
Watchlist both banners then rendered, contradicting each other: one said the
sector gate could not run, the other said it ran against an older book. Latent
on six decision-tier surfaces (quiet caption); `_grow_today_sectors_cache` was
the first GATE-tier key to reach it, which is what turned it into a warning
carrying a false suppression clause on the sizing surface.

Fixed at the source. The decision moved out of `app.py` into the pure
`coord_freshness.apply_stamps()` — refuses to stamp a `None`, and **drops any
stamp the key already had**, closing the second route (stamps persist across
runs, so a producer that succeeded then later failed kept the old stamp).
Gating the banner on `not _wl_brief_offline` was considered and rejected: it
would also have suppressed the outage and refresh-error branches, i.e. a
suppression with no banner. Nine tests, mutation-checked — reverting
`apply_stamps` to the presence rule fails five of them.

**Registering `_portfolio_value` / `_port_df_enriched` — MEASURED and DECLINED
2026-08-28.** The same review asked whether the two sizing inputs belong in the
registry, citing `_acct_gate_cache`'s own rationale (list a key even when the
republisher refreshes it, so a future regression surfaces as stale rather than
as a silent gap). Measured before deciding: **16 pages read them**, including
three — 🪞 Trade Review, 📅 Economic Calendar, 🌐 Macro — that `SURFACE_KEYS`
deliberately maps to `()` with a test asserting they stay silent. Registering
would invalidate that premise, force a re-measure of every surface, and risk
banners on pages that show none today — to guard a case the `_refresh_error`
branch already reports FIRST, since these keys can only go stale when the
republisher failed. The guard was instead placed where the risk is: a structural
test that `_refresh_portfolio_cache_after_trade` still publishes all three keys
(`tests/test_repo_hygiene.py`), mutation-checked against dropping each one.
**Revisit with those numbers, not from the key names.**

**Worth carrying forward:** the extraction is what made the fix verifiable.
While the rule lived inline in `app.py` nothing could import it, which is
exactly why a sentinel-stamping bug survived Phase 1, its own review, and a
drift guard written specifically to police this registry.

**Still unbuilt:** Phase 3 (lifting Home's risk/fragility/correlation producer block into a
pure module, which is where this plan converges with §5's `N > 15` branch) —
Phase 3 needs its own `planner` pass. The remaining ranked findings from §10
also stand.

Three corrections the build made to the design, worth not re-deriving: the
planner's own candidate option 2 (null the un-refreshed caches) was shown to be
a **net regression**, since `None` and absent render identically at the ~20
sentinel-collapsing sites; the real regression was never the omitted keys but
that the republisher **silenced `_portfolio_snapshot_stale()` on 9 pages**; and
the review caught that a single blanket freshness stamp would have **laundered**
a genuinely-stale `_reduce_calls` into a positive freshness claim on the
memo-HIT path, because `_synth_sig` carries shares but not avg cost.

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


---

## 10. CHUNK 1 RESULT — classified 2026-08-27

All 115 consumer-page reads were read and verdicted against §4's inclusion rule.

| Verdict | Count |
|---|---|
| **CLAIM** (a finding) | **25** |
| HANDLED | 74 |
| INERT | 16 |
| RAW-UNGUARDED | 0 |
| UNCLEAR | 0 |

**N = 25 ⇒ the §5 pre-registered `N > 15` branch fires: the class is structural, the counter is
NOT built.** Recorded plainly because the threshold was set before the number existed, which is
the only thing that makes this outcome trustworthy rather than a rationalisation.

**The app is in better shape than the raw count implies, and this matters for how the remaining
work is framed.** 74 of 115 sites already carry an explicit offline branch, several of them
exemplary — 🔔 Catalyst Watch renders *"⚠ Composite filter did not run — this is NOT 'nothing
qualified'"*; 🎯 My Edge's `_last_held_tickers` read carries a comment naming the false
"No missed exits" all-clear it prevents; 🔗 Risk Analysis has an "unavailable, treat the figures
above as unverified" arm for correlation coverage. **This class is an incompletely-applied good
pattern, not neglect.** Zero sites were UNCLEAR and zero were RAW-UNGUARDED — every raw read is
either `is None`-checked, wrapped in `try/except`, or passed to a documented None-safe callee.

### The 25 CLAIM findings, ranked by how badly a user could be misled

1. **`_portfolio_value` → a fabricated $50,000 book** (shared sidebar global + 📋 Watchlist ×2).
   Sizes every trade on 📈 Analysis and Watchlist and prints "Portfolio: $50,000" in the
   exportable brief, on a real book of ~$24,500. **Money-moving; fixed first, separately — see
   §12.** **CLOSED** (pre-existing fix, confirmed still in place 2026-08-30).
2. **📈 Analysis Summary Scorecard** (`_port_df_enriched`) — a name you already own renders as a
   fresh entry with a suggested share count. **CLOSED 2026-08-30** — `_coord_cache_state`
   disclosure banner mirroring the sibling Trade Plan tab guard. Opus review SHIP, 0 blocking.
3. **📈 Analysis `_reduce_calls`** — the "not a place to add" suppression vanishes, *and* the
   compensating warning provably cannot fire in that state because it keys on a different cache.
   **CLOSED** (pre-existing fix, confirmed still in place 2026-08-30).
4. **🪞 Trade Review `_last_port_df`** — *fabricates* a finding ("100% of your trades are in
   Other, above the 25% warn level") plus prescriptive advice. Invents advice rather than
   omitting it. **CLOSED 2026-08-30** — `sector_mix()` gained a `data_available` kwarg that
   abstains (n_sectors=0, `data_unavailable=True`) instead of letting every ticker fall through
   to "Other"; both the `build_insights` finding and a separate standalone chart render site
   fixed. Opus review SHIP, 0 blocking.
5. **🧾 Summary `_structural_alert_cache`** — asserts a "diversified" correlation structure when
   the cluster scan never ran, and **persists it** to `portfolio_thesis`, where it becomes next
   week's HELD/SHIFTED baseline. The only finding that poisons a durable record. **CLOSED
   2026-08-30** — `_classify_correlation` now abstains to "unavailable" whenever
   `structural_new_clusters` isn't a real list (matching this module's own sibling classifiers'
   posture), instead of falling through to a div_label-only verdict. Pre-fix persisted rows are
   unrecoverable but bounded/self-healing (awareness-only, ages out within the 14-day baseline
   lookback) — fixed forward, no remediation built. Opus review SHIP, 0 blocking.
6. **🥧 Sankey `_acct_gate_cache`** — an over-cap sector/name renders neutral blue instead of
   red. A red flag turning green. **CLOSED 2026-08-30, and the root cause was DIFFERENT from
   what this line describes.** `gate_basis()` has returned `basis="equity"` unconditionally since
   a 2026-07-09 policy reversal, so the "levered" branch this cache fed (`basis in ("account",
   "over-levered")`) had been **permanently dead code** since that date — the collapse was real
   but inert, since the branch it fed could never fire regardless of the cache's state. Removed
   the dead branch (user-chosen option, over re-wiring to the newer `margin.capital_basis_weight()`
   helper or leaving it with a disclosure) rather than patching a cache read that was never the
   actual problem. Same root cause also covered finding #9's Trade Journal site — bundled into
   one commit rather than two separate cache-collapse fixes. Opus review SHIP, 0 blocking.
7. **🏆 Health `_fragility_cache` / `_highbeta_share`** — fabricates a neutral 65 and drops a
   25-point penalty into the headline A–F grade, while `n_available == 5` **suppresses** the
   "some dimensions unavailable" banner. Insidious: the disclosure mechanism exists and is
   defeated. **CLOSED 2026-08-30** — `_factor_exposure_score`'s bail-out now triggers on
   `fragility` alone (`isinstance` guard), independent of `port_beta`'s presence, since
   `port_beta` never fed the score in the first place — only `detail` display. Replaced an
   existing test that had pinned the fabrication as if intentional; git blame confirmed it was a
   characterization test from a coverage-backfill pass, not a designed choice. Opus review SHIP,
   0 blocking.
8. **🎯 My Edge `_reduce_calls` ×2** — republishes the BKNG "size up a name flagged to trim"
   contradiction and propagates it to 📈 Analysis. **CLOSED 2026-08-30, two review passes.**
   First fix gated on `_coord_cache_state("_reduce_calls") == "ready"` alone and was returned
   FIX-FIRST: the cache's producer never writes `None` — a Daily Brief crash fail-opens it to
   `{}` (the real "did this run" signal lives in a separate `_daily_brief_offline` flag), so
   "ready" alone still misread a crashed Brief as verified-empty, leaving the false claim live in
   exactly the state (a data outage) where a real reduce call would matter most. Revised to gate
   on `_coord_cache_state(...) == "ready" AND NOT _daily_brief_offline`; re-reviewed SHIP, 0
   blocking.
9. **📒 Trade Journal `_acct_gate_cache`** — the post-buy concentration breach warning silently
   does not fire at the moment of the trade, when it is most actionable. **CLOSED 2026-08-30 —
   same dead-code root cause as #6** (see above): the `basis in ("account", "over-levered")`
   check this cache fed had been unreachable since the 2026-07-09 `gate_basis()` policy reversal,
   so the breach warning itself was never actually suppressed by a cache collapse — only the
   *"measured on your net capital"* clarifying clause (also dead) was removed. The real
   concentration check (`assess_add_concentration`, including the live F-255 net-capital cap)
   was untouched and fires correctly regardless.
10. **📅 Economic Calendar** — itemised *"No direct holdings"* on every macro event for a check
    that never ran. High false-comfort volume, low stakes each. **CLOSED 2026-08-30** — a single
    `_coord_cache_state`-gated disclosure caption at the top of the Calendar tab, covering both
    render sites. Did NOT touch `_affected_tickers()`'s own contract (would have rippled into
    `daily_briefing.py`, a `_GATE_FILES` member, whose 3 consumers use `.get(key, [])` — a default
    that doesn't protect against a stored `None`, so all 3 would raise `TypeError`) — judged
    disproportionate to this finding's own "low stakes" ranking. Opus review SHIP, 0 blocking.
11. Then: 🔗 Risk Analysis's whole risk dashboard vanishing with no `else`; 🥧 Portfolio
    Overview's two documented fail-opens; the awareness-only leverage/sector-chart surfaces
    going quiet; and annotation-level omissions.

### A doc defect the audit exposed in passing
CLAUDE.md's coordination list names **🔗 Risk Analysis** as the consumer of
`_pi_factor_tilt_cache`, but the **producer** is 🧩 Intelligence. So Risk Analysis reading it
absent is the *common* case, not an edge case — the LLM adversarial-scenario narrative is
generated and persisted to `regime_scenario_cache` with no factor-concentration evidence and no
disclosure. Fix the doc alongside the code.

**RESOLVED 2026-08-28 — both halves, and the finding was understated in two ways.**
(1) The window is wider than "never opened Intelligence": the cache is written only by that
page's "📡 Load factor exposure" BUTTON, so visiting the page without clicking still leaves both
consumers with `None`. (2) There are **two** consumers, not one — Intelligence's own 🧬 Structural
Scan tab has the identical defect, with a byte-identical `_format_evidence` block. Both now call
one shared helper. `structural_scanner`'s prompt had a partial mitigation (*"do not mention factor
exposure if it isn't supplied"*) that the fix would have silently invalidated by making the line
unconditional; it was rewritten and a test pins the old wording's removal. **Residual, needs its
own reviewed `db.py` commit:** neither persisted table can record that a narrative was built
without factor evidence (no factor-tilt snapshot column), so the disclosure is generation-time
only and a later reader of an earlier row sees no provenance.

---

## 11. The leverage point — verified in code

`_refresh_portfolio_cache_after_trade` (`app.py` ~2352, 6 call sites across 📒 Trade Journal and
the broker/screenshot imports) publishes `_last_port_df`, `_last_held_data`,
`_last_held_tickers`, `_manual_stops`, `_holdings_sig_at_home_build`, `_port_df_enriched` and
`_portfolio_value` — and **none** of `_reduce_calls`, `_port_risk_cache`, `_fragility_cache`,
`_acct_gate_cache`, `_leverage_cache`, `_grow_composites`, `_corr_df_cache`.

So after any logged trade it makes the portfolio keys `ready` while the sibling caches stay
absent, **defeating precisely the guards that key on `port_df`.** This is the single mechanism
that turns §3's "a hard portfolio guard does not protect a non-portfolio key" from a caveat into
the actual delivery vector for **~8 of the 25 findings**.

**If one thing is built rather than 25 patched, it is this function.** It is also materially
smaller than the general render-layer refactor the `N > 15` branch nominally points at, so it
should be designed (`planner`) before Part 2 #3 is opened.

---

## 12. Ordering from here

1. **The fabricated-$50k sizing fix — DONE FIRST, separately.** Money-moving; user decided
   2026-08-27 that no size may be proposed when the book is unknown. The fix lands in
   `risk.sizing_unavailable_reason` (a new `"portfolio"` reason) rather than at the three
   substitution sites, because that function is the documented single predicate and it had **no
   branch for a non-positive portfolio value** — its `"ceiling"` check is itself gated on
   `portfolio_value > 0`, so a zero book returned `None` ("I would size").
   **CORRECTED 2026-08-27 by the Opus review, and the correction matters:** the fall-through
   result was **not** a harmless 0 shares. `position_sizing` computes `risk_dollars = 0`, then
   `max(1, int(0 / risk_per_share))` floors it to **1 share** — an ACTIONABLE "buy 1 share" at
   0.0% of book. (The `max(1, ...)` floor lives on the risk-based path; only the ceiling path
   dropped its floor in the 2026-08-24 sizing overhaul.) So removing the substitution alone
   would have swapped a fabricated size for a *differently* fabricated one, not for silence —
   which is why the fix had to land in the predicate. `risk.py` + `daily_briefing.py` ⇒
   mandatory Opus review; verdict SHIP, 0 blocking.
2. **The `_refresh_portfolio_cache_after_trade` republisher** (§11) — `planner` first.
3. **Chunk 3, the registry-aware `check_antipatterns.py` rule** — stops regrowth, needs no
   runtime code, and is independent of the branch.
4. **The remaining ranked findings**, highest-severity first, each with its own rationale in the
   diff and a per-arm test at the boundary it claims. **CLOSED 2026-08-30 — all 10 individually-
   ranked §10 findings now fixed** (see the per-item CLOSED notes in §10 above). The remaining
   ~15 of the 25 total CLAIM findings were never itemized individually and remain open as an
   unranked group if ever picked up.


---

## 13. Recorded for whoever picks up flag 6a (the `_grow_today` pre-guards)

The Opus review established a prerequisite set that must land in the SAME commit as any
removal of `daily_briefing.py`'s `if price > 0 and portfolio_value > 0 else {}` pre-guards:

- **The `portfolio_unknown` marker is currently unreachable AND unhandled.** `app.py` ~7919 and
  ~8209, plus `notify._sizing_cap_note`, all key on specific marker names and fall through to
  no-caption / `""` for an unrecognised one. So removing the pre-guard *alone* converts `{}`
  into an equally silent drop — no improvement.
- **It would falsify a schema comment.** `db.py` ~232-237 documents "all four NULL = ... or no
  portfolio value"; the marker carries `sizing_version` with no `shares`, producing a
  `rec_sizing_version NOT NULL` + `rec_shares NULL` row that `db.py` ~229-231 enumerates as
  only reachable for ceiling-or-stop.

So flag 6a is three renderer arms + a schema-comment fix, not a one-line guard removal. That is
why it was correctly left out of the sizing fix rather than bundled.

**One residual, future-callers only:** with a NaN book *and* a degenerate stop, the `"stop"` arm
returns `portfolio_value: nan`, which would persist as NaN via `_rec_sizing_cols`. Unreachable
from both current call sites.

---

## 14. Five more findings CLOSED 2026-08-30 (later session) — re-derived, not assumed

§10's closing line grouped the remaining ~15 findings as one sentence: "🔗 Risk Analysis's whole
risk dashboard vanishing with no `else`; 🥧 Portfolio Overview's two documented fail-opens; the
awareness-only leverage/sector-chart surfaces going quiet; and annotation-level omissions." A
fresh investigation re-verified each clause against CURRENT code rather than trusting that
sentence — worthwhile, because two of the four turned out to be partly stale (the Portfolio
Overview Sankey/sector-bar "fail-open" was already closed by findings #6/#9's dead-branch fix;
several other candidate sites were already covered by the coord_freshness banner rollout that
shipped between 2026-08-27 and 2026-08-30). **True remaining count in the 4 named areas: 5, not
~15.**

1. **🔗 Risk Analysis — Portfolio Risk Dashboard vanishes with no `else`.** `_port_risk_cache`'s
   producer (app.py ~5240) publishes `None` on failure, never `{}` — "offline sentinel, not {},
   matches sibling cache contract" — so a falsy read always means not-computed. The consumer
   (`if _port_risk: <4 sections> ` through `st.divider()`) had no `else`, so a session that
   reached the page via the Trade Journal republisher (which does not refresh
   `_port_risk_cache`) without visiting Home saw the dashboard, the Market-Risk Posture gauge,
   Cross-Asset Pulse, and the Beta Contribution chart all silently absent. **CLOSED** — added an
   `else` using `_coord_cache_state("_port_risk_cache")` to disclose "missing" (never visited
   Home) vs. "offline" (computation failed) with a specific `st.warning`.
2. **🔗 Risk Analysis — leverage/margin warning also goes quiet.** The producer (app.py ~4754)
   always publishes a dict with a `cash_seen: bool` field added in a prior session specifically
   to distinguish "measured, no debt" from "never measured" (see the extensive in-code comments
   at app.py ~4739-4785) — but this consumer only ever checked `levered`, never `cash_seen`, so
   "never measured" and "confirmed unlevered" rendered identically (nothing). **CLOSED** —
   reused the already-tested `summary_view.book_safety()` classifier (the same one 🧾 Summary's
   Book Safety zone already calls for the identical cache) with `broker_drift=None` (deliberate:
   keeps its drift-red leg from ever firing, since this block has never considered drift), added
   as an `elif` after the existing `levered` branch — mutually exclusive by construction, per
   `book_safety`'s own logic (verified by the reviewer).
3. **🥧 Portfolio Overview — News Intelligence tab, `_reduce_calls` fail-open.**
   `_opp_reduce_tickers = set((... .get("_reduce_calls") or {}).keys())` fed
   `build_news_intelligence(reduce_tickers=...)`, whose job is to split a Reduce/Exit-flagged
   ticker's positive news out of "opportunities" into "opportunities_suppressed". An
   unverified/offline read silently produced an empty set — same shape as "genuinely nothing to
   suppress" — so a name under an active Reduce/Exit call could show as a clean add-on-pullback
   signal. **CLOSED** — mirrors the already-closed finding #8 (🎯 My Edge, same cache) exactly:
   `_coord_cache_state("_reduce_calls") == "ready" and not _daily_brief_offline` (both checks
   needed, since a crashed Brief fail-opens `_reduce_calls` to `{}` rather than `None` — the
   exact gap #8's own first review pass missed). **Note: this is a disclosure add, not a
   suppression-behavior change** — the offline/missing case already produced an empty set
   either way; what was missing was telling the user the cross-check didn't run.
4. **🥧 Portfolio Overview — Rebalancer tab, `_reduce_calls` fail-open.** Same cache, same page,
   different tab. `_rb_reduce_set` fed `build_rebalance_plan(reduce_call_set=...)`, which
   suppresses ADD actions on a Reduce/Exit-flagged ticker (checked BEFORE the risk-trim check,
   per its own docstring) — an unverified empty set meant a drift-driven ADD could directly
   contradict a same-day protective call. **CLOSED** — same verified-check pattern as #3,
   placed to mirror an EXISTING disclosure already shipped for a sibling cache
   (`_risk_advisor_recs_raw is None`) immediately above this block on the same page. Same
   "disclosure add, not a behavior change" note applies.
5. **🔔 Catalyst Watch — leading-sector 🔥 flag can go STALE, not just absent.** The
   Daily-Brief-crash reset block (app.py ~5564) explicitly nulls `_grow_today_sectors_cache`
   (to `None`) and `_reduce_calls` (to `{}`) on `_daily_brief is None`, but never touched its
   sibling `_leading_sectors_cache` — published from the exact same `_gt_today` dict a few
   lines below on the success path. So a Brief that crashed AFTER an earlier successful run in
   the SAME session left the prior run's leading-sector list in place, read by Catalyst Watch as
   current — the more severe of two possible failure modes (the other, a first-run crash, merely
   collapsed to "no sector leading," which is low-stakes since it's a per-row 🔥 badge, not a
   whole section). **CLOSED** — producer-side: added `_leading_sectors_cache = None` to the same
   reset block. Consumer-side: Catalyst Watch now reads the raw value first and distinguishes
   `None` from a genuinely-computed empty list, appending a short caption when the flag
   couldn't be checked.

**Opus review: SHIP, 0 blocking.** Two non-blocking notes, neither requiring a code change:
- Finding 5's producer-reset block is not the *sole* writer of `_leading_sectors_cache` — it is
  also written at app.py ~5101 (a memo-hit republish) and ~6029 (the bundle build). Both already
  flow `None` through correctly; the fix is coherent, just don't describe the reset block as the
  only writer if this is revisited.
- The SAME memo-hit rebuild path (app.py ~5124-5163) recomputes `_daily_brief` but does **not**
  re-derive `_leading_sectors_cache` or `_reduce_calls` — a separate, narrower, pre-existing
  staleness window on the same key family. **Not fixed here, out of scope** — flagged for a
  future pass, not a regression introduced by this commit.

All 5 fixes are disclosure-only: no `constants.py` touch, no gate/threshold/recommendation/engine
change. Full suite unchanged at 4782 (app.py has no self-coverage; the reused classifiers
`_coord_cache_state`/`book_safety` are already unit-tested). Commit `f707e31`.

**Genuinely still open at the time of writing:** 📈 Analysis, 📒 Trade Journal, 🪞 Trade Review,
📅 Economic Calendar, 🎯 My Edge, 🧑‍⚖️ The Judge, and 📋 Watchlist were NOT re-audited this
pass — see §15, which closes this out. Phase 3 (lifting Home's risk/fragility/correlation
producer block into a pure module, per §11/§12 item 2's own text — "where this plan converges
with §5's `N > 15` branch") remains genuinely unstarted and needs its own `planner` pass.

---

## 15. The 7 previously-skipped pages, audited 2026-08-31 (commit `7faf729`)

§14's own assumption ("assumed covered by the closed top-10 + the coord_freshness rollout")
was never independently verified. A full re-audit read all 7 `elif page ==` blocks top to
bottom (not sampled), cross-referenced every session-state read against
`stock_analyzer/coord_freshness.py`'s registry, and traced consumer functions where the
falsy-branch consequence wasn't obvious from `app.py` alone.

**4 pages are genuinely CLEAN:**
- **🧑‍⚖️ The Judge** — its two cross-page reads (`_judgment_opinions_today`, `_reduce_calls`
  for the coherence audit) each already have an explicit `is None`/empty disclosure banner.
- **🪞 Trade Review** — its one collapse (`_portfolio_value`) is safe because
  `position_size_discipline()` already returns `n_trades=0` on a non-positive value, and the
  page's own caption already names "requires a portfolio value" as a precondition. The
  already-fixed finding #4 (`sector_mix`'s `data_available` kwarg) is present and correctly
  wired. `coord_freshness.SURFACE_KEYS["tr"] = ()` is confirmed accurate — no other
  coordination-cache reads exist on this page.
- **📅 Economic Calendar** — the already-fixed finding #10 disclosure covers the Calendar
  tab's collapse. The Pre-Event Playbook and Post-Event Results tabs both use a plain
  `.empty` check on `_port_df_enriched` whose fallback message is accurate regardless of
  whether the cause is "never loaded" or "genuinely empty" — no false claim either way.
- **🎯 My Edge** — mostly a *producer* (of the `_mirror_*` keys, excluded from the inclusion
  rule by definition). Everywhere it *consumes* a Home-produced cache, it already discloses
  correctly: `_port_risk_cache` → an explicit "Portfolio beta not available this session"
  caption (the exact wording reused for finding 1 below); `_last_port_df` → an explicit
  basis-degradation caption; `_reduce_calls` → the already-fixed `_mi_reduce_verified` guard,
  independently and correctly implemented at BOTH of its two read sites on this page;
  `_last_held_tickers` → explicit three-state handling before `detect_missed_exits()`.

**3 real findings, fixed, ranked by how badly a user could be misled:**

1. **📋 Watchlist — portfolio-beta risk gate silently disabled.**
   `_wl_port_risk = st.session_state.get("_port_risk_cache", {}) or {}` then
   `_wl_port_beta = _wl_port_risk.get("beta")`, fed into every
   `build_watchlist_recommendation()` call's `portfolio_ctx`. Consumed by
   `stock_analyzer/watchlist_advisor.py::_portfolio_risk_gate()`: BOTH the hard breach
   (`port_beta > PORTFOLIO_BETA_CEILING and ticker_beta > TICKER_BETA_CRITICAL` → downgrade
   ENTER_NOW to NEAR_ENTRY) and the soft caution require `port_beta is not None` — a `None`
   beta silently skips both, and an ENTER_NOW card renders with no beta-related caution,
   indistinguishable from "beta checked, all clear." **Confirmed LIVE, not theoretical:**
   `_port_risk_cache`'s producer has its OWN independent try/except (computing beta/vol from
   portfolio returns) with NO coupling to `_daily_brief_offline` — unlike this same page's
   sibling G-05 sector-ceiling check, whose coupling to `_daily_brief_offline` is documented in
   the code's own comments as "incidental, not designed." So this cache can go offline on a day
   the Daily Brief itself computes fine, and the page's existing `_wl_brief_offline` banner
   never mentioned it. **CLOSED** — a standalone `if _coord_cache_state("_port_risk_cache") !=
   "ready": st.warning(...)` check, deliberately NOT folded into `_wl_brief_offline` (the two
   conditions aren't reliably coupled), reusing the exact wording 🎯 My Edge already ships for
   this same cache. Two non-blocking reviewer notes, both left as consistency-preserving rather
   than fixed: the warning gates on cache *state*, not on beta being present-but-unusable within
   a ready cache (My Edge has the same property); and it can fire on an empty Watchlist with no
   recs to gate (harmless, matches the existing banner's behavior).
2. **📒 Trade Journal — post-BUY concentration check silently skipped.** The single-name/
   sector/net-capital concentration check (`assess_add_concentration`) only ran inside
   `if _pb_cc_pdf is not None and not _pb_cc_pdf.empty and _pb_cc_pv > 0:`, nested in a broad
   `try/except: pass`. When `_pb_cc_pdf` (`_last_port_df`) was `None`, the ONE heads-up the app
   gives about a newly-created concentration breach silently didn't run, with zero disclosure.
   **CLOSED** — an `elif _pb_cc_pdf is None:` caption, deliberately scoped to only the `None`
   case: a genuinely-empty frame (first-ever trade) or a non-positive book value fall through
   to neither branch, since those are real "nothing to check yet" answers, not offline ones.
3. **📈 Analysis — HOLD-tab stop-ladder could soften an unverified active Reduce/Exit call.**
   `_hold_rc = (st.session_state.get("_reduce_calls") or {}).get(...)` then
   `_render_stop_ladder(..., under_reduce=bool(_hold_rc))`. Per that function's own docstring,
   `under_reduce` exists specifically to "suppress the 'keep climbing' nudges... so the
   explainer never softens an active exit directive" — an unverified `_reduce_calls` silently
   forced it `False`. The page's BUY-tab sibling branch already discloses a related, broader
   scenario, but the HOLD-tab branch (reached only once a ticker is confirmed held) had no
   equivalent. **CLOSED** — mirrors the exact dual-check pattern already shipped for the closed
   My Edge finding #8 and this session's Portfolio Overview findings:
   `_coord_cache_state("_reduce_calls") == "ready" and not _daily_brief_offline` (both needed,
   since a crashed Daily Brief fail-opens `_reduce_calls` to `{}` rather than `None`), plus a
   caption disclosing when the check couldn't be verified.

All 3 disclosure-only: no `constants.py`/gate/threshold/recommendation/engine change. Full suite
4835 passed (up from 4815 — the extra 20 are from an unrelated concurrent session's own commits,
not this diff). Opus review SHIP, 0 blocking. Commit `7faf729`.

**This closes the "7 unaudited pages" line item entirely.** The only genuinely open F-260 work
remaining is Phase 3 (see above) — a structural extraction, not a bug-fix pass, needing its own
`planner` design before any code.
