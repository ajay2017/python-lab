# 🏠 Home page redesign — reduce buried Act Today + banner stacking

**Status: DECLINED 2026-08-29 — mockup was built and reviewed, but the user chose not to proceed once the risk analysis below (§ Risks surfaced before build) was laid out. No `app.py` code was touched. Do not re-propose this from scratch — if revisited, start from the risks section, since that's the reasoning that closed it, not the design itself.**

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
