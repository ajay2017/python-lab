# Summary Page — Cross-Page Pointer Cards — Design Plan

**Date:** 2026-07-27
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 5
**Status:** Card #1 (Risk Posture) shipped 2026-07-27 via a parallel session's
"Summary KPI Tier 3" work, before this plan's Phase 1 build started — a plain
read-only badge (emoji + label + one-line `summary` text + "→ see 🔗 Risk Analysis"
caption) sitting in a second row under the KPI tiles (alongside an Alpha-vs-SPY
tile), reusing `exit_advisor.market_risk_posture()` + the published
`_fragility_cache` exactly as scoped below — it does NOT include this plan's
correlated-pairs flag embellishment. User decision (confirmed cross-session
2026-07-27): keep that build as-is, don't duplicate it here.

**Card #2 (Thesis Review) SHIPPED 2026-07-27.** Built as the first card in a new
"🧭 Elsewhere in DRISHTA" section (between Act Today and Holdings, as originally
scoped below) — Risk Posture stays in its own separate KPI-row home, not moved here.
**Naming correction made during the build:** this plan originally called this card
"Thesis Red Team" and pointed it at the ⚠️ Red Team tab — wrong on both counts.
`thesis_reviews` (the table explored together during the 2026-07-26 sweep:
BROKEN=2, WEAKENING=19, INTACT=51) is F-1's per-holding INTACT/WEAKENING/BROKEN
thesis review, which renders on AI Insights' **🩺 Positions** tab (confirmed via its
own identical "N need review" header count, `app.py` ~line 25151-25156). The ⚠️ Red
Team tab is a completely different feature — a quantitative 0–100 erosion score
(`thesis_erosion_cache`, Intact/Softening/Eroding/Breaking bands) built by a
different module (`thesis_red_team.py`). The shipped card correctly reads
`thesis_reviews` and points to "🧠 AI Insights" (landing on the default first tab,
🩺 Positions — the correct destination, by coincidence of tab order, not by naming
the tab directly since `_pending_page` can only select a page, not a tab within it).
See "Selected cards" section #2 below for the corrected spec as actually built.

**Build order for this plan now starts at card #3 (Catalyst Watch).**

> **One-line spec:** Add a "🧭 Elsewhere in DRISHTA" section to 🧾 Summary — a small
> row of read-only pointer cards, each surfacing one already-computed signal from
> another page with a one-line summary and a "→ Page Name" jump link. Never a second
> independent computation of anything another page already owns.

> **Origin:** grew out of a broader "is DRISHTA professional-grade" review
> (2026-07-26/27 session) that ended with a Summary-page brainstorm, then a static
> HTML mockup (per `feedback_mockup_first_ux`), approved as-is by the user. The
> mockup showed 6 candidate cards across 4 nav groups; the user then picked exactly
> one per group for Phase 1 (see "Selected cards" below). Full brainstorm reasoning
> lives in that session's transcript, not restated here — this doc is the buildable
> distillation.

---

## Why this shape, not a bigger dashboard

Summary's own design precedent (`project_summary_page` memory) already ruled that it
must be **strictly additive** — it reads what Home's preamble already publishes to
`st.session_state`, never recomputes anything Home (or any other page) already owns.
The already-shipped-then-deferred "Tier 3" idea (a Risk Posture pointer badge,
explicitly specified as "a read-only pointer... reusing `exit_advisor.market_risk_posture()`,
never a second independent computation") is the template this whole plan generalizes.
The governing rule for every card below is the same one: **read an existing value,
add a link, never stand up a parallel calculation.** This app has hit the "two
surfaces silently disagree" bug class enough times (SPCX hold-vs-reduce, the DELL
split-brain, the AAPL stop-label mismatch — all in `docs/architecture.md` §10) that
a second independent read of the same fact is the one thing to rule out for every
card, not an afterthought.

**Bounded by design:** the mockup showed 6 candidates; the user picked 4, one per nav
group (Portfolio, AI, Signals, Research), deliberately holding back two Portfolio-group
candidates (Signal Coherence, a Recommendations-engine snapshot) that would have
competed with Risk Posture in the same group. Don't add a 5th card without a
deliberate reopening of that cap — the "calm, not noisy" posture (§2B) applies to
Summary too, not just to the Brief.

**Placement (confirmed in the mockup):** the new section sits **after** Act Today,
**before** Holdings. Act Today stays the top-priority actionable content directly
under the KPI row; the pointer section is awareness-level (one step down in urgency);
Holdings remains the detailed reference table at the bottom.

**Navigation mechanism:** reuse the existing `_pending_page` indirection (button sets
`_pending_page` → consumed at top of next run → assigned to `nav_page`) already used
elsewhere for ticker→Analysis jumps (CLAUDE.md "Navigation safety"). No new nav
pattern needed.

---

## Selected cards for Phase 1 (build order, cheapest/most-ready first)

### 1. Risk Posture (Portfolio group) — SHIPPED 2026-07-27 via a parallel session, do not rebuild
- **Note:** shipped without the correlated-pairs flag described below (badge only:
  emoji + label + summary + pointer caption). If the correlated-pairs flag is still
  wanted, that's an incremental addition to the existing tile, not a fresh build.
- **Shows:** the current market risk posture read (e.g. "Neutral") plus a
  correlated-pairs flag.
- **Reuses:** `exit_advisor.market_risk_posture()` (already computed for 🔗 Risk
  Analysis's Market Risk Posture dial) and the already-published `_risk_pairs_cache`
  / `_avg_corr_cache` session-state keys (CLAUDE.md "Coordination pattern").
- **Points to:** 🔗 Risk Analysis.
- **To verify at build time:** confirm `market_risk_posture()`'s exact return shape
  and whether it's already published to session_state by Home's preamble or needs
  an explicit call from the Summary page render path (if the latter, confirm it's
  cheap/idempotent to call a second time, not a fresh fetch).
- **Why first:** this is the one candidate that was already partially scoped once
  before (the deferred Tier 3 item) — least new ground to cover.

### 2. Thesis Review (AI group) — SHIPPED 2026-07-27, renamed from "Thesis Red Team"
- **Corrected name and destination (was wrong in the original plan):** this reads
  `thesis_reviews` — F-1's per-holding INTACT/WEAKENING/BROKEN review — which renders
  on AI Insights' **🩺 Positions** tab, not the ⚠️ Red Team tab (a different feature,
  `thesis_erosion_cache`, a quantitative 0–100 score with its own Intact/Softening/
  Eroding/Breaking bands). See the plan status header above for the full correction.
- **Shows:** a count of held tickers whose most recent review is WEAKENING or BROKEN,
  e.g. "1 Broken, 2 Weakening of N reviewed" — or "All Intact" when none need
  attention (shown calmly, not hidden).
- **Reuses:** `db.load_thesis_reviews()` (columns: `id, ticker, trade_date,
  reviewed_at, status, summary, inputs_hash, created_at`), reduced to one row per
  ticker via `sort_values("reviewed_at", ascending=False).drop_duplicates("ticker")`
  — the identical pattern the AI Insights page's own "🩺 Positions" header count and
  the Act Today "Thesis broken" button's `_broken_thesis_tickers` set already use.
  Status values: `INTACT` / `WEAKENING` / `BROKEN`.
- **Points to:** 🧠 AI Insights (lands on the default first tab, 🩺 Positions, where
  this exact count already lives).
- **Why second:** directly usable now that the missed-alpha bug fix (`51b2441`) and
  the 2026-07-26 sweep already put this data in front of us; cheap DB read, no new
  modeling.

### 3. Catalyst Watch (Signals group)
- **Shows:** a count of held names reporting earnings within the week, e.g. "3
  holdings report this week."
- **Reuses:** `held_data`'s existing `earnings` field per ticker — already loaded and
  read the same way in `daily_briefing.py`'s `_review_list()` (confirmed this
  session: `earn_date = (data or {}).get("earnings")`). No new fetch.
- **Points to:** 🔔 Catalyst Watch.
- **To verify at build time:** decide the exact window (7 days, matching
  `CATALYST_WATCH_WINDOW_DAYS`? or a tighter "this week" cut) — don't invent a new
  threshold without checking what constant already governs Catalyst Watch's own
  window, to avoid a second silently-different definition of "soon."
- **Why third:** near-zero cost (data already in memory), different nav group so it
  doesn't compete with the Portfolio-group cards.

### 4. Watchlist actionable count (Research group) — build last, least certain
- **Shows:** a count of watchlist names currently `ENTER_NOW`/`NEAR_ENTRY`, e.g. "4
  names now actionable of 58 tracked."
- **Reuses:** `build_watchlist_recommendation()` (`stock_analyzer/watchlist_advisor.py`,
  confirmed real via `docs/plans/watchlist-resurrection.md`) — the same
  classification the 👁️ Watchlist page and the already-shipped Watchlist
  Resurrection feature (F-203) already compute.
- **Points to:** 📋 Watchlist.
- **To verify at build time:** `build_watchlist_recommendation()` runs per-ticker on
  the Watchlist page's own render — confirm whether Summary can cheaply reuse an
  already-published result (if the Watchlist page hasn't been visited this session,
  there may be nothing to read yet, meaning this card would need its own compute
  pass — the one candidate of the four where "reuse, don't recompute" might not be
  free). If it turns out to require a real new computation pass, that changes this
  card's cost tier and is worth flagging back to the user before building it.
- **Why last:** the one card where the "just reuse an existing value" assumption is
  least certain — see above. Build the other three first, see how the section feels
  live, then decide if this one is still worth it.

## Explicitly held back from this round (not forgotten, just not in Phase 1)

- **Signal Coherence** (🧩 Intelligence) — zero-LLM, cheap, but competes with Risk
  Posture in the same Portfolio nav group under the one-per-group cap. Candidate for
  a future round if Risk Posture doesn't cover enough ground on its own.
- **Recommendations engine snapshot** (📜 Recommendations History) — same
  Portfolio-group collision. Also newly relevant post-`51b2441` (the missed-alpha
  fix), so worth another look later even though it's not in Phase 1.

Don't re-propose either of these as "new" ideas in a future session — they were
considered and deliberately deferred, not missed.

---

## Design principles (carried into the build, non-negotiable)

1. **Read-only, additive.** No card changes what any other page computes, gates, or
   recommends. Each card is a summary + a link, nothing else.
2. **Never a second independent computation.** Every card reuses an existing
   function/cache; if build-time investigation finds a card actually needs new
   modeling (see Watchlist card above), stop and flag it rather than quietly
   building a parallel calculation.
3. **One predicate per card, no drift between the chip and its destination page.**
   If a card's count and the destination page's own count could ever disagree,
   that's the same double-surface bug class (`feedback_single_surface_priority`)
   this codebase has hit before — verify at build time that both read the same
   underlying value.
4. **Calm framing.** These are "here's what's happening elsewhere," not urgency
   language. No card should read as a call to action stronger than what its source
   page already implies.
5. **Graceful degradation.** Any card whose underlying data isn't available this
   session (page not yet visited, cache empty, table unreachable) simply doesn't
   render — no error, no placeholder implying something's wrong.
6. **Bounded set.** Four cards, one per nav group, for this phase. Don't grow this
   list without deliberately revisiting the cap.

## Non-goals

- Not a new alerting/urgency layer — nothing here should ever feed Act Today or any
  gate.
- Not a replacement for visiting the source pages — each card is intentionally a
  one-line teaser, not a mini-embed of the destination page's content.
- Not built in one shot — four separate phases, one per deploy, live-reviewed before
  the next, per this project's established rollout cadence
  (`feedback_phased_ux_rollout_cadence`).

---

## Definition of Done (CLAUDE.md, run per-phase when each card is built)

1. Any new constant (e.g. a window-size threshold for the Catalyst Watch card, if one
   is needed) → `docs/architecture.md` constants table row.
2. New user-facing surface → an F-ID in `docs/requirements.md` under Summary (F-204).
3. Shipped entry in `docs/shipped-log.md` per phase; this plan's status line updated
   as phases land.
4. In-app User Guide (`app.py`, User Guide page) note for the new section.
5. Memory update per phase noting anything discovered that changes the plan above
   (e.g. if the Watchlist card turns out to need new computation).
6. If any phase's gate is itself deferred/re-gated on something, add it to
   CLAUDE.md's "What's queued" immediately — the lesson from the 2026-07-26 audit
   that found the Agentic Roadmap's Phase-2/3 gates had drifted memory-only.
