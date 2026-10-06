# 🏠 Home page redesign — reduce buried Act Today + banner stacking

**Status: P0 SHIPPED 2026-10-06 — re-scoped by a fresh `planner` pass before any code was touched. The original tray/pointer design below is STALE; read the 2026-10-06 section first.**

**2026-10-06 — the premise had moved on.** Asked to design the "narrower version" this doc's own Trigger-to-revisit named, a `planner` pass found the "Needs your attention" tray described below had **already shipped** (2026-09-17/24/25, as the collapsed "⚠️ Alerts" expander on Home) — there was no tray left to design. Deeper investigation of the actual 5 banner-backing caches found **none of them is a safe 2-state read** (every one collapses a real "not checked" state into something readable as clean) — so even the narrower tray-reading-caches idea as originally framed would have reproduced the exact overloaded-producer-state bug class that got the original proposal declined. Two real, independent bugs were found instead: (1) a crashed Daily Brief build fell back to a minimal empty dict that both Home and 🧾 Summary rendered as a false "nothing to act on" all-clear — Act Today was never actually checked that run; (2) Home and Summary already counted "Act Today" differently (Home demotes a recovered stop-breach, Summary didn't, despite Summary's own comment claiming parity).

**P0 (built and shipped 2026-10-06, Opus reviewer SHIP/0-blocking after one FIX-FIRST/4 round):** new `stock_analyzer/act_today_view.py` (added to `_GATE_FILES`) is now the single source of truth every render surface reads — Home's badge/chip/section header/Monitoring section, Summary's pill/chips. Returns an explicit `"offline"` state (`n_active=None`, never `0`) instead of a false all-clear; unifies the count on Home's stricter post-demotion definition (owner decision — Summary's count can now drop on a day a breach recovers, and Summary discloses this via a dedicated caption rather than silently dropping it). First review round found the diff was staged incompletely (only the two new files, not the app.py/hook wiring), a second false-all-clear site the task spec missed (Home's Monitoring/Awareness section), and Summary's new post-demotion count visibly contradicting the (correctly unchanged) Active Vetoes banner with no disclosure — all fixed and re-verified before ship. Full detail: `docs/requirements.md` F-204; memory `project_home_redesign`.

**P1 (the narrow one-line notices summary above the existing Alerts expander) and P2 (the Act Today top pointer) are deliberately NOT built this pass** — owner decision: see P0 live first, pick these up separately if still wanted. Not blocked on anything; just sequenced after.

## 2026-10-06 (later same day) — P1 + P2 designed, re-verified against post-G-25 code

Picked up once P0 was production-verified (see above). A fresh `planner` pass re-derived both specs against CURRENT code rather than trusting the original 2026-08-29 concept, which was already stale in two ways: G-25 (shipped the same day, `docs/plans/cluster-add-gate.md`) added two new elements inside the exact "⚠️ Alerts" expander P1 targets, and P2's pointer must read `act_today_view()` (P0), not the older `split_defensive()`-based counting the original concept assumed.

**P1: SHIPPED 2026-10-06.** No reviewer required (confirmed — pure additive instrumentation, no `_GATE_FILES` touch, no new constant). Full suite 7193 passed, antipattern/constants-doc gates green. **P2: not yet built** — next up, with its mandatory Opus reviewer pass.

**Verdict: PROCEED on both, as two separate commits/deploys — P1 first, a live look, then P2.** No `constants.py`/gate/scoring touch either way; neither new module name collides with `_GATE_FILES`'s exact-path matching. **P2 gets a mandatory Opus `reviewer` pass; P1 doesn't** (optional, cheap to fold in) — P2 is a fourth Home surface displaying a gated decision's count, and P0's own review round already caught a false-all-clear site the build spec had missed at exactly this kind of surface.

**A finding worth noting directly: P2 is worth less than the original framing assumed.** Act Today is reached behind far fewer sections than the stale "13 sections / 2,547 lines" figure claimed (that number and `project_home_redesign` memory's "still buried" line are both now corrected) — the existing "Today's Actions" chip in the Brief's chip row already shows "N Act Today" today. P2's real payoff is specifically during pre-market hours (4:00–9:29 ET weekdays), when the Pre-Market Intel panel pushes that chip below the fold.

**8 owner decisions, 4 confirmed explicitly, 4 taken at the planner's recommended default (all owner-approved via the pattern already established on this feature):**
- **D1 (P2 clear state):** a calm one-liner ("✅ Nothing to act on today"), not blank — reports one of three distinct states on every render.
- **D2 (P2 resolved-breach note):** yes, "· N stop breach(es) resolved — monitoring below" — mirrors Summary's own P0 disclosure for the identical situation.
- **D3 (P2 contents):** chip counts only (EXIT/TRIM/WATCH), matching Summary's approved pill style — no ticker names at the top.
- **D4 (P1 quiet-day line):** yes, a muted `st.caption` like "· 1 note" even when only low-severity notes fired (the leverage caption fires almost daily) — otherwise several real "couldn't check" disclosures go back to being invisible, the exact problem this feature exists to fix.
- **D5 (P2 jump link, planner default):** try a static in-page anchor link; fall back to plain text ("see 📋 Today's Brief → Act Today below") if a live click doesn't actually scroll — no existing in-page anchor precedent in this codebase, so this needs a live check either way.
- **D6 (P1 wording, planner default):** the summary says "notices," never "alerts" — Home already has three different things called "Alerts" (this expander, the Command Center's `n_danger`/`n_warning` metric, and "PRICE ALERTS"); a fourth collision is avoidable.
- **D7 (top-of-page order, planner default):** P2's Act Today pointer renders first, then P1's notices summary directly above the Alerts expander it summarizes.
- **D8 (P1 coverage, planner default):** every one of the 16 alert-expander fill sites gets a `_home_notice()` hook, no exceptions (including the held-ticker-load-failure site, which is low-stakes but keeps the "one hook per site" test invariant exact — the thing that will catch the *next* G-25-style addition automatically).

### Build spec

**P1 — new `stock_analyzer/home_notices.py`** (pure, no Streamlit/DB/network):
- `SOURCE_ORDER` (16 ids, declaration order): `dayshock, xcheck, split, structural, drift, thesis, systrust, heldload, stale, dropped, scorewithheld, outage, leverage, pnldq, crossasset, debrief`. `SOURCE_LABELS` maps each to a short display name. `TIERS = ("error", "warning", "info", "caption")` — a display enum, not a policy value, not in `constants.py`.
- `summarize_notices(entries) -> dict | None` — `None` for empty/`None` input, **never** renders all-clear wording (several underlying producers have lossy "clean" states, so "nothing to report" must mean "nothing fired," never "nothing checked"). Merges by source keeping the highest tier; an unknown tier counts as `"warning"` (never dropped); returns `{tier, headline, named, n_notes}` — `named` lists error/warning sources (errors first, then `SOURCE_ORDER`), `n_notes` counts distinct info/caption sources.
- **Critical design property: P1 never reads a cache.** It only records what each of the 16 fill sites *actually rendered*, from inside the exact branch that rendered it — so `_structural_alert_cache`'s three-state shape (already correct post-G-25) flows through automatically with zero new sentinel-handling logic, and the lossy "clean" producers (Day Shock, price cross-check, stock split) can never be misread, because P1 simply never asks them a question they'd answer wrong.
- `app.py` wiring: `_home_notices = []` (a local, not `session_state`) + `_notices_summary_ph = st.empty()` + a `_home_notice(source, tier)` closure, defined before its first call (`feedback_module_def_order`), wrapped in its own try/except so a bug in the summary itself can never take down a fill site that isn't in a try block. A hook call goes inside each of the 16 sites' own branch (not unconditionally in the block body — 5 of the 16 blocks run on every render even when nothing fires, so an unguarded hook would manufacture a false daily note). The broker-drift site needs `_render_broker_drift()`'s return type widened to `str | None` (its one caller already captures the return).
- No logic/text/condition of any existing banner changes — this is pure additive instrumentation.

**P2 — new `stock_analyzer/act_today_pointer.py`** (pure):
- `act_pointer(view) -> dict` returning `{state, n, chips, n_resolved}`. `view` that's `None`, not a dict, or has an unrecognized `state` → `"offline"`, `n=None` (fails loud, never calm). A non-empty `active` list always means `"act"` regardless of what `view["state"]` itself says (belt-and-suspenders against a future inconsistency). `chips` comes from `decision_bucket.bucket_act_by_type()`, EXIT→TRIM→WATCH order, zero-count buckets omitted. **Must never call `split_defensive()` directly** — reuses `act_today_view()`'s own already-computed `_act_view`, enforced by the existing `test_act_today_view_module_is_the_sole_caller_of_split_defensive` test.
- `app.py` wiring: `_act_ptr_ph = st.empty()` declared first in Home (before `_notices_summary_ph`, per D7), filled immediately after `_act_view = act_today_view(...)` is computed — one `act_today_view()` call total, pinned by an AST test, so this can't reopen `feedback_brief_act_count_source`'s drift class. The outage branch fills the pointer with the existing `_OFFLINE_ACT_MSG` *before* its `st.stop()` call, so the stop path never leaves the pointer blank (a blank pointer could otherwise be misread as "clear" under the D1 calm-one-liner design).

### Tests required (both P1 and P2)

Full list is in the planner's design transcript — condensed: `summarize_notices`'s empty/merge/unknown-tier/ordering behavior plus a simulated 16-source busy day (the logic-level stand-in for a multi-banner day that can't be forced live); `act_pointer`'s offline/clear/act boundary cases built on real `act_today_view()` fixtures (including a recovered stop-breach feeding `n_resolved`); and AST-based wiring tests on `app.py` mirroring `test_act_today_view.py`'s pattern — every declared placeholder has exactly one matching hook call, no hook call sits unconditionally in a block body that runs every render, the outage branch fills the pointer before `st.stop()`, and exactly one `act_today_view(` call exists inside Home.

### Known limitation, disclosed not fixed

The notices summary covers only the "⚠️ Alerts" expander's 16 sites — fail-loud errors drawn elsewhere on Home (e.g. the reference-table error, the global outage gate) are NOT included, and the summary's own wording must not imply total page coverage.

### Memory corrections needed at build time

`project_home_redesign`'s own "Act Today is still buried" line (written 2026-09-25) is now stale per this pass's re-verification — needs correcting, not just appending to. `feedback_brief_act_count_source` still names `split_defensive` as the drift-prone source; P0 replaced that with `act_today_view()`, so the memory's own guidance is one feature out of date.

**Common-case PRODUCTION-VERIFIED 2026-10-06**, same day as ship, via live screenshots of a real TRIM item (SPCX, concentration-driven, not a stop_breach): Home's "Act Today (1)" and Summary's "1 item needs attention [1 TRIM]" showed identical counts and the same composite score (50) — confirms the count-unification holds on a normal render, no regression from the prior per-page computation. No resolved-breach caption rendered on Summary, correctly, since a concentration TRIM isn't subject to the stop-recovery demotion logic. **Still unverified, opportunistic only:** the offline/false-all-clear path needs the Daily Brief build to actually crash on a real run — hasn't happened since ship, can't be forced. Keep this open until seen once live.

---

**Original status, superseded above: DECLINED 2026-08-29 — mockup was built and reviewed, but the user chose not to proceed once the risk analysis below (§ Risks surfaced before build) was laid out. No `app.py` code was touched. The tray/pointer design immediately below is the STALE pre-2026-10-06 proposal, kept for reference only — see the correction above before reading further.**

**Origin:** a 2026-08-29 walkthrough of the live Home page (full section-by-section inventory, ~6,660 lines / 24-26 top-level blocks) surfaced two concrete, code-confirmed problems, not opinions:

1. **Act Today is buried.** `docs/user-manual.md` says Home is *"Look here first, every day"*, but ~2,547 lines / 13 sections of preamble (System Trust chip, live-price strip, Day Shock, price cross-check, stock split cards, structural alert, broker drift, the Portfolio Command Center KPI strip, a leverage caption, up to 5 data-quality captions) render **before** "Today's Brief" — where Act Today lives — even begins at `app.py:6627`.
2. **No cross-type banner priority.** The data-quality captions already collapse into one warning + expander once ≥2 fire — but Day Shock, price cross-check, stock split, structural alert, and broker drift are five *independently* coded sections that can all fire the same morning, each rendered as its own separate call-out in a fixed sequence, with no consolidation or severity ranking across them.

**Explicitly out of scope:** no scoring, gating, threshold, or recommendation-logic change of any kind. This is a rendering-order and information-architecture change only — every number and every card still comes from the exact same computation it does today.

---

## The proposed change

**Add one new "priority zone" directly below the System Trust chip, before anything else renders. Nothing currently on the page is deleted — the existing preamble sections (price strip, Command Center KPIs, market tone, fragility gauge, Quick Research) simply render *below* this new zone instead of interleaved with the banners, in the same relative order they do today.**

The priority zone has two parts:

1. **A one-line Act Today pointer**, styled like the pill already shipped on 🧾 Summary (`docs/mockups/summary-page-restructure.html`'s `.act` component) but pointing *down the same page* ("↓ Jump to details") rather than to another page, since Home already has the full detail. **Must read the exact same post-split bucket the Act Today section itself renders from** — not a separately computed count (see `feedback_brief_act_count_source.md`: an independently-derived Act Today count has drifted from the real bucket before). Zero items → the existing calm green "Nothing to act on today" one-liner, promoted to the top instead of appearing only once you've scrolled past 13 sections to reach it.
2. **A consolidated "Needs your attention" tray** replacing the five independent banner sections (Day Shock, price cross-check, stock split, structural alert, broker drift) with one bordered card containing a compact chip-row per firing condition — generalizing the collapse-when-≥2 pattern the data-quality captions already use. **Zero conditions firing → the tray doesn't render at all**, same as today's individual conditionals, just grouped instead of stacked.

Everything else — the full two-column Grow Today / Act Today / Monitoring section, Buy Candidates, Thesis Under Pressure, Evening Debrief, AI Snapshot — is **unchanged**, both in content and in relative order.

## A real build constraint (not a design choice)

Several of the banners being consolidated are placed where they are *because of a compute-order dependency*, not arbitrarily — e.g. the structural alert (F-218) is documented as rendering "after the hit/miss synthesis converges… `corr_df` isn't freshly published until that synthesis block completes." **The new tray must render its chip for a condition only once that condition's underlying compute has actually run** — this means the tray's *render* moves to the top of the page, but the *compute* for slower-to-resolve conditions (structural alert, broker drift) cannot be dragged earlier than it runs today without separately verifying nothing downstream depends on their current timing. Whoever builds this needs to check each of the 5 conditions' compute-then-render distance individually rather than assuming a pure copy-paste move is safe.

## Two things this surfaced that are related but NOT part of this redesign

- **`docs/user-manual.md` and `docs/architecture.md`'s Home descriptions are well behind the actual page** (the manual names ~5 things; the page has 24-26 blocks; the architecture doc's coordination-cache table still says producer "My Portfolio" for several Home-produced caches, an old page name). Worth a doc-sync pass, but that's a documentation fix, not a layout change — tracked separately, not blocking this mockup's review.
- **The AI Snapshot section's fit with CLAUDE.md's "the app decides, it does not inform" posture** is a policy question (an LLM narrating "key risks and suggested actions" resembles the "current status" Ask-tab idea that was explicitly proposed-and-declined elsewhere for exactly this reason). That's a product decision for the user to make deliberately, not something a layout mockup should quietly resolve — flagged, not acted on here.

## Risks surfaced before build (why this was declined)

A mockup was built and reviewed (toggleable Quiet day / Busy day states), matched this repo's dark palette and the `.act` pointer component already shipped on 🧾 Summary. Before any `app.py` edit, the user asked for an honest risk read. Two of the risks below aren't hypothetical — they're the same bug class already found and fixed elsewhere in this app days earlier:

1. **The tray creates a second consumer of caches whose state vocabulary was fitted to their first consumer only.** `broker_sync.decide_drift_banner` returns `state="none"` for two different facts — "no broker configured" and "a clean, fresh check passed" — and this exact collapse broke 🧾 Summary's Book Safety cell on 2026-08-27 when a *different* new consumer read it at face value (`feedback_overloaded_producer_state`). The proposed tray is structurally the same shape of change (a new consumer reading an existing single-consumer producer) for the same cache, among others.
2. **Several of the five caches use `None` to mean "not checked yet," and a naive tray would silently collapse that into "all clear."** `_structural_alert_cache`, `_broker_drift_cache`, and siblings follow a 3-state contract (`None`=offline, `[]`=checked-clean, populated=firing) that this repo has a dedicated automated gate to police, because a truthiness/presence check has swallowed this distinction more than once before (`feedback_sentinel_is_present`).
3. **Compute-order dependency** (see the build-constraint section above) — moving render without moving compute risks the top tray showing stale-from-last-session data on first load while the real banner further down is correct.
4. **The new Act Today pointer is a second source of truth for a count that has already drifted once** (`feedback_brief_act_count_source` — an independently-derived Act Today count diverged from the real bucket before, on this same app). Building it correctly on day one doesn't remove the ongoing cost: every future change to Act Today's bucketing now has two render sites to keep in sync, forever.
5. **The live price strip is a `@st.fragment(run_every=60)`** — if the new zone sits outside that fragment's refresh scope, the top summary and the strip below it can disagree for up to a minute after a price move, a new class of visible inconsistency that doesn't exist today.
6. **Consolidation trades banner fatigue for under-emphasis** — a full-width dedicated Day Shock banner today becomes one chip among four in the tray; a genuinely serious condition could read as visually equal to a minor one.
7. **Zero automated test coverage for any of this** (`app.py` has no tests), combined with the fact that multiple banners firing simultaneously is a comparatively rare event in practice — a bug in the tray's state handling could ship and go unnoticed for weeks, the same pattern already observed elsewhere on this page (F-204a's Act Today row layout went unverified in production for the same reason).

None of this made the redesign's *goal* wrong — Act Today being buried behind 13 sections is real and code-confirmed. It made the *cost of building it correctly* higher than a first read of the mockup suggested: each of the 5 consolidated conditions would need its producer function opened and its real state vocabulary enumerated (not assumed), and a simulated multi-condition busy day tested deliberately rather than waiting for a rare real one. The user chose not to spend that right now.

## Phasing (if ever resumed)

- **Phase 0:** static HTML mockup — **done**, reviewed, not approved for build (declined on risk grounds above, not on visual grounds).
- **Phase 1 (not started, not scheduled):** would build the priority zone + consolidated tray in `app.py`, but must open each of the 5 producer functions' real return contracts first (not infer from the banner code that reads them today), verify the per-condition compute-order constraint, and deliberately test a simulated busy day before considering it verified — waiting for a real one is how the risks above go unnoticed.
- **Phase 2 (separate, always was gated on explicit user ask):** the doc-sync pass and the AI Snapshot policy question — these are independent of whether Phase 1 ever happens and can be picked up on their own.

**Trigger to revisit:** an explicit user re-ask, ideally paired with either (a) willingness to invest the producer-auditing work up front, or (b) a narrower version of the idea that avoids creating new consumers of overloaded producer state (e.g., only consolidating the subset of the 5 banners whose caches are already known to be 2-state, not 3-state).

## Governance

This does not touch `stock_analyzer/constants.py`, any file in `_GATE_FILES`, or any scoring/gating/DB-write path — it is a rendering-order change confined to `app.py`'s Home block. **No mandatory Opus `reviewer` pass is triggered** under Hard Rule #4's criteria. The one thing worth a careful pass at build time is the compute-order constraint above — a functional-correctness check, not a policy review.
