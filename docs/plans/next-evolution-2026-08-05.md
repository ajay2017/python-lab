# DRISHTA — Next Evolution: Brainstorm Pass #2

**Date:** 2026-08-05
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 4.8 (1M context)
**Status:** CLOSED 2026-08-06 — all 4 concrete recommendations shipped. Lens 1 (datapoint-coherence audit) ran as the alpha-coherence audit, 3 real bugs found and fixed same day (memory `project_alpha_coherence_audit`). Lens 2's "the app's own track record" pick shipped as **F-229 Engine Track Record** (both Offense + Defense facets, 2026-08-05). Lens 2's "Lessons Learned → recurrence" open question was verified NOT a gap, not built (memory `project_lessons_learned_recurrence_audit`). Lens 2's "Behavioral Fingerprint → live decision moment" shipped as **F-231** (2026-08-06, see `docs/plans/behavioral-fingerprint-decision-moment.md`). Lens 3's "State of the Portfolio standing thesis" shipped as **F-232** (2026-08-06, reframed as a stability ledger — see `docs/plans/state-of-portfolio-standing-thesis.md`). The picked-up-alongside item, Pass #1's deferred D2 (portfolio-level correlation under historical stress), shipped as **F-230 Correlation Under Stress** (2026-08-05). Lens 3's other two items — pre-trade impact preview, portfolio-level scenario engine (Pass #1's E1) — remain genuinely open; not yet added to CLAUDE.md's queue as of this closure (a deliberate choice, not a drift — they were surfaced as "what's left" options in conversation, not committed to). This document's own body below (the original brainstorm text) is left unedited as a historical record — read this status line as authoritative over the body for "is this still open."

> **Relationship to [next-evolution-strategy.md](next-evolution-strategy.md) (2026-07-17):** That document was Pass #1 — a fully-reviewed product strategy whose Concepts A–F (Behavioral Fingerprint, Portfolio-as-One, Pre-Mortem, Regime-Conditional Targets, Decision Reconstruction capture, Tax-Aware Lens) have **all shipped**. Its Experimental Track (E1 Forward Portfolio Simulator, E2 Personal Alpha Attribution, E3 Tail-Drawdown Probability) and deferred D1–D4 remain open. This Pass #2 is a fresh look ~3 weeks and ~a dozen features later (The Judge, Portfolio Q&A v2, Outcome Range Simulator, Personalized Discovery, Entry Timing). Where an idea here overlaps Pass #1's open items, it's noted.

> **Scope discipline (carried from Pass #1 §5, still binding):** every idea below must be judged on *"does this decrease total decision load?"* not just *"does it add value?"* The named product risk is unchanged: an app that becomes **unignorable-but-ignored**. And the §5.8 invariant holds — no feature may present a point-estimate expected return for an individual stock.

---

## The meta-observation

At ~3 months / ~70 shipped features, DRISHTA is no longer feature-starved. For a dense analytical app serving a single user, the highest-leverage frontier has probably **shifted away from "feature #71"** toward three things:

1. **Coherence** — with ~40 `st.session_state` cache keys and a single ticker appearing on 10+ surfaces, the dominant risk is no longer "missing capability," it's "the same fact framed two subtly-different ways." The `_dpnl_cache` fix (Home vs Summary Today's-P&L) and The Judge's coherence check already treat this as real.
2. **Closed loops** — the app issues hundreds of calls; the open question is whether it *systematically knows whether it was right*, and whether it *learns* in a way the user sees. Pass #1's "Investor Intelligence Loop" (§3) identified steps 7–10 (Record → Evaluate → Learn → Improve) as the flywheel that doesn't close. Much of the *capture* now exists; the *feedback* mostly doesn't.
3. **Trust / calibration** — the entire posture is "the app decides." The natural next demand on a deciding system is: **prove your track record, visibly and always** — not only inside a periodic audit.

The ideas below are organized by the user's three requested lenses: datapoint mismatches, disconnected dots, and boundary-pushing innovation.

---

## Lens 1 — Datapoint mismatches (same number, two surfaces)

Worth an explicit hunt because in a *decision* app a visible inconsistency erodes trust in the engine itself.

- **Today's P&L** — already fixed once (`_dpnl_cache`, Home vs Summary). That fix addressed one *instance*; the *class* is "quantity independently recomputed on 2+ surfaces." A deliberate sweep of every such quantity is the actual deliverable, not another one-off.
- **Candidate quantities to audit** (⚠️ **all unverified — confirm against code before treating as gaps**):
  - **Composite score** — does every surface read one cached composite, or do some recompute it?
  - **Beta-adjusted alpha** — appears in My Edge KPI; does the weekly-debrief email / Summary cite a return computed the same way?
  - **Portfolio value / concentration %** — `_acct_gate_cache` basis is *equity*; do all "% of portfolio" surfaces use equity, or does any use market value?
- **Two sector taxonomies by design** (curated map vs GICS) — this is *correct* (F-222/F-223 shipped them intentionally), but the open question is whether it's *visibly labeled everywhere*. A user seeing "Tech 35%" on one page and "Tech 41%" on another with no explanation experiences a correct design as a bug.

**Shape of the work:** a one-time "single source of truth" coherence audit — not new features. Output = a table of every cross-surface number and its authoritative source. Low risk, high trust payoff.

## Lens 2 — Disconnected dots (ingredients exist, wire not run)

The richest vein. These are places the app already *has* the data but doesn't *close the loop*.

- **Behavioral Fingerprint → the live decision moment.** The app diagnoses the user's failure modes but (open question ⚠️) may not surface the relevant one *at the instant* the user logs a matching trade. Pass #1 §3 step 10 set a hard invariant here: bias-aware framing may amplify salience but must **NEVER** re-order recommendations, change the composite, or gate — the engine stays sole ranker. A *passive, non-gating mirror annotation at trade-write time* ("this SELL matches your 'exit winners early' pattern; here's the base rate") would live inside that invariant. This is the "we diagnosed it but didn't apply it at the point of pain" gap.
- **Lessons Learned → recurrence.** ✅ **VERIFIED 2026-08-05 — NOT write-only.** F-195's Pattern Library already closes this loop: a chip row of past exit `lesson_category` values for the ticker (or the last 5 across all exits, if none for that ticker) is unconditionally rendered on the Pre-Mortem Protocol for a prospective BUY (`app.py:20839-20870`), and also feeds the LLM's counterargument prompt (`premortem_advisor.py`). Documented as intentional in F-187 (`docs/requirements.md`). **Real remaining gap, not a bug:** matching is ticker-identity only (plus a blunt cross-ticker recency fallback) — there's no sector- or category-based cross-ticker recurrence match (e.g. a "Held too long" lesson from selling ticker A wouldn't surface when buying unrelated ticker B in the same sector), because a lesson row carries no stored sector/composite-score field. This is a well-scoped POSSIBLE future enhancement, not a defect — see memory `project_lessons_learned_recurrence_audit`.
- **Decision-context capture (Concept E Phase 1) is "inert until DDL" / capture-only.** This is a *dangling* closed loop by design — Pass #1 shipped the passive snapshot but deferred the Phase 3 viewer until 6+ months of history accrued. Worth tracking the calendar gate; every month it stays viewer-less is a month the captured context earns nothing.
- **The app's own track record as a standing surface.** The Judge checks *coherence*; rec-engine evaluation happens at *checkpoints* (06-18, 07-26). ⚠️ **Open question: is there a persistent, always-visible calibration meter** — "engine BUY calls: +X% vs SPY over 60d, hit-rate Y%"? Pass #1's Research Scorecard Phase 3 (engine-vs-analyst calibration) is deferred pending ≥20 `composite_score_at_save` rows. A deciding app arguably earns the right to decide by showing this *constantly*, not only in an audit.

## Lens 3 — Boundary-pushing innovation

For genuine swings (each must still pass the "decreases decision load?" test and the §5.8 no-point-forecast invariant):

- **Pre-trade impact preview.** Before a Buy is logged, show the portfolio *after*: Δ concentration, Δ sector tilt, Δ beta, Δ correlation cluster, Δ fragility. Every one of those is already computed somewhere; this composes them into one "here's what this trade does to *you*" forward view. Turns the app from reactive to pre-emptive. (Partially anticipated by concentration/sizing discipline, which closes entry-time asymmetry — the new part is the *composed multi-dimensional preview*.)
- **Portfolio-level scenario engine** — Pass #1's Experimental Track **E1 (Forward Portfolio Simulator)**, still open. "What happens to *me* if rates +50bp / semis −15% / my top holding halves?" Outcome Range Simulator (F-224) does per-name block-bootstrap Monte Carlo; the boundary is *portfolio-level stress under a narrative shock, after the app's own stops/gates/trims mechanically fire*. Pass #1 mandated prototyping a single scenario first.
- **A single "State of the Portfolio" standing thesis.** Not another card — a synthesized, dated, one-paragraph standing view the app *commits to* and then grades itself against next week. Forces the app to hold *one* opinion instead of 70 fragmented surfaces. Directly serves the coherence + calibration frontiers at once.

---

## Recommended first zoom-in (author's pick, not a decision)

The **closed-loop calibration meter** (Lens 2/3). Rationale:
- It most reinforces the entire "the app decides" thesis — a deciding system's credibility rests on a visible track record.
- It's blocked mostly on *plumbing already half-built* (Concept E capture, Research Scorecard Phase 3), not net-new infrastructure.
- Every feature shipped after it inherits more trust by association.

Second choice: the **datapoint-coherence audit** (Lens 1) — lower ceiling but near-zero risk and immediately trust-positive.

---

## Before any of this becomes a plan

1. **Verify every ⚠️ open question against code at HEAD** — do NOT carry these into a plan as facts. Several are hypotheses about whether a wire exists; confirm or kill each.
2. Anything touching a gate / threshold / scoring / recommendation formula → `planner` (Opus) design pass, then `reviewer` (Opus) before ship, per CLAUDE.md hard rule #4. Most Lens-2/3 ideas here are decision-path-adjacent (bias-at-decision, calibration surface, pre-trade preview) even when they *look* like awareness-only UI — classify by impact, not by how the diff looks.
3. Re-read Pass #1 §5 ("What Not to Build") and §5.8 (no stock-level point forecast) before scoping any innovation item.

*Fact vs. assumption declaration: shipped-feature references are drawn from CLAUDE.md, MEMORY.md, and next-evolution-strategy.md (facts as of those documents' last sync). All items marked ⚠️ are unverified hypotheses about the current code and must be checked against HEAD before use. No performance or capability claim here should be treated as factual without validation.*
