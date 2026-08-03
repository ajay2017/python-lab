# The Judge — Portfolio Judgment Layer — Design Plan

**Date:** 2026-08-03
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 4.8 (brainstorm/design stage)
**Opus review:** NOT YET — this is a planning doc, not code. Per CLAUDE.md hard rule #4, the first build that touches decision logic / the Daily Brief requires an explicit Opus design review before it ships.
**Status:** DESIGN COMPLETE (all three load-bearing questions resolved 2026-08-03). **Not green-lit to build.** Phase 1 (first Judge output, touches the Daily Brief) requires an **Opus design review before shipping** per hard rule #4; Phase 0 (log-only instrumentation, no decision logic) could precede it. Awaiting user go/no-go on starting Phase 0.

> **One-line spec:** A tier *above* the app's 60+ features — a single accountable
> judgment layer ("the Judge") that reconciles every subsystem into ONE
> whole-portfolio daily posture, weighted by each subsystem's own track record,
> surfacing disagreement instead of hiding it, and always decomposable down to the
> evidence. The features stop being destinations and become a council; the app
> becomes the chair.

---

## Why this, and why not feature #61

The app reached 60+ features built incrementally — each one filled a gap or extended
the last. That worked, but it produced a quiet drift: with 15+ intelligence surfaces
(Debate, Red Team, Pre-Mortem, Investor Mirror, Portfolio Q&A, Entry Timing,
Predictive Analytics…), the **user became the integrator again** — opening surfaces
and reconciling them by hand. That is the exact posture the app was built to
eliminate ("the app decides, it does not inform"). By accretion, breadth was pulling
the app from *decides* back toward *informs*.

The Judge is the correction. It is not another witness; it is the judge that was
missing. It is a **ceiling to build toward**, not a floor to keep adding to — the
intent is that it makes feature #61 feel like a *demotion* of the app rather than
progress.

## The reframe

The 60+ features are **witnesses**, not destinations. Today the *user* is the judge.
The Judge moves that reconciliation into the app. Its four jobs:

1. **Reconcile to ONE posture** — a single daily whole-portfolio call, not 15 signals
   to weigh.
2. **Surface disagreement, don't bury it** — when subsystems contradict each other,
   that contradiction *is* the signal. Agreement is cheap; the Judge earns its keep
   on the conflicts.
3. **Audit for cross-feature contradiction systematically** — turns the reactive
   whack-a-mole of coordination incidents (BKNG orphan-conviction collision, SHOP
   buy/sell whiplash, verdict divergence, macro-affected dedup) into a standing
   guarantee. This is the CLAUDE.md coordination pattern promoted from a
   hand-maintained convention into an enforced feature.
4. **Weight each witness by its historical accuracy** — to reconcile subsystems you
   must decide whose vote counts more, and the honest way is *by how right each has
   been*. **This makes synthesis and self-calibration the same project** (the monthly
   intelligence report already proved Strong Buy = +7.3% alpha vs Buy band = −0.5%;
   see `docs/` rec-engine evaluation and memory `project_rec_engine_evaluation`).

## The three locked pillars

1. **Scope = whole-portfolio daily posture first, and DECOMPOSABLE.** Leads with one
   posture; shows the per-ticker / per-dimension votes underneath. Rationale: the
   portfolio substrate *already exists* — `_home_synth_cache`, the Daily Brief tone
   engine, and the concentration / fragility / risk-posture / leverage / factor-tilt
   caches — so promoting Home synthesis into "the Judge" is a natural extension, not
   greenfield. Decomposability is non-negotiable because a portfolio posture is a
   *fuzzier truth* than a per-ticker call: you must see the votes underneath to know
   when it's wrong and to calibrate it.

2. **Authority = full override.** The Judge may suppress a subsystem's signal
   ("momentum says GO, I'm overriding"). This *is* "decides, not informs." The
   cross-reference engine (`verdict_reconciled`) already does a mild two-signal
   version; the Judge generalizes it to all subsystems at portfolio scope.

3. **Presentation = action-first, reasoning-underneath — NEVER decide+hide.** The
   governing distinction: *inform* (bad: data dump you interpret) vs *decide+explain*
   (the target) vs *decide+hide* (bad: black box). "Decides, not informs" ≠ "hides
   its reasoning." The posture leads with the call; the basis is always one click
   down; the dissent is always shown. Enforced by the existing redline
   `feedback_recommendation_transparency` ("recs must be explainable"). A pure-action
   black box would be a *regression* — it would delete the user's only way to catch
   the app being wrong, kill the trust that makes the user actually execute a call,
   and throw away the disagreement-surfacing that is the Judge's highest-value output.

## Architecture (as understood — to be firmed once open questions close)

Builds on the existing spine, not a rewrite:

- **`_home_synth_cache`** is already a proto-synthesizer (assembles the brief). The
  Judge promotes it from "assembles and displays" to "adjudicates and renders a
  verdict." New Home inputs already MUST join its memoization signature or ship stale
  (memory `project_home_synth_memoization`) — the Judge tightens that contract.
- **The publish/consume `session_state` bus** is already the data bus a judge would
  read from. Producers already emit (`_port_risk_cache`, `_fragility_cache`,
  `_reduce_calls`, `_structural_alert_cache`, `_pi_factor_tilt_cache`, etc.); nothing
  consumes them *holistically* yet. The Judge is the first holistic consumer.
- **Verdict reconciliation** (`verdict_reconciled`) is the seed pattern — generalize
  from "momentum vs composite vs news vs earnings for one ticker" to "all subsystems
  at portfolio scope."

## Redlines to preserve (any build must honor all)

- Always **decomposable** — one posture on top, votes underneath.
- Always **show the dissent** — never hide subsystem disagreement.
- Subsystem weighting must be **evidence-based** (track record), never a hand-tuned
  black box.
- **Never a pure-action black box.**
- Thresholds/weights that gate a recommendation are **investment-policy decisions** —
  set with the user, live in `constants.py` (CLAUDE.md hard rule #1).
- First build touching decision logic / the Daily Brief gets an **Opus design
  review** before shipping (hard rule #4).

## The synthesis contract (Q1 — RESOLVED 2026-08-03)

Every feature hands the Judge an *opinion* in one common shape. The Judge does two
different things with the opinion set, and the `dimension` field is what **routes** an
opinion-pair to the right machine:

- **Different dimensions, same subject → WEIGHTING (jobs 1 & 4).** Momentum says GO
  while position-health says TRIM is *not* a conflict — it's the normal multi-factor
  picture. The Judge weighs them (by track record) into one call; override resolves.
- **Same dimension, same subject, opposite signals → CONTRADICTION AUDIT (job 3).**
  Two features answering the *same* question disagree (the `verdict_divergence`
  class). That is the bug/split-read to surface.

The taxonomy is therefore cut so that features answering the same question **share** a
dimension (so the audit can fire) and features answering different questions get
**different** dimensions (so weighting doesn't false-flag). Too coarse → everything
looks like a conflict; too fine → nothing shares a dimension and you're back to 60
silos.

**Opinion fields (finalized):**

| Field | Req? | Purpose |
|---|---|---|
| `source` | required | Which feature emitted it. Needed for contradiction *attribution* (name the two clashing features) AND track-record weighting (the lookup key is `source × dimension`, because multiple sources share a dimension by design). |
| `dimension` | required | Which question this answers — the routing key (see above). |
| `signal` | required | Directional call, **normalized −1…+1** for cross-dimension arithmetic, plus an optional human label (GO/CAUTION/STOP). |
| `confidence` | required | How sure this witness is, so a hedged vote weights below a strong one. |
| `as_of` + validity | required | When it was computed and whether its producer was live. Preserves the codebase's `None` (offline) vs `[]` (checked-clean) distinction so the Judge never weights a stale/dark subsystem as a fresh vote, and never reads "we don't know" as "we checked and it's fine." A posture's own confidence degrades when its witnesses are stale. |
| `evidence` | required | Human-readable "why" — keeps the posture decomposable (redline). |
| `ticker` | optional | Present for per-name opinions; absent for portfolio-wide ones (concentration, regime). |
| `advisory` | flag | `true` = shown in the narrative but **excluded from weighting** (e.g. `tax`, `catalyst` — they never gate, per existing redlines). |

**Dimension taxonomy (first cut, ~10 questions):** `quality` · `momentum` ·
`thesis_integrity` · `position_health` · `concentration` · `structural_risk` ·
`macro_regime` · `behavioral_fit` · `sentiment` · `leverage`. (`tax`, `catalyst` exist
as advisory-only.) Mapping of each to the emitting features is in the conversation;
to be transcribed here when the build starts.

**Emission model = pragmatic hybrid (not purist).** Features keep emitting what they
already emit (Grow Today still says ADD, reduce_calls still says REDUCE), but every
emission is **tagged** with the fields above. The Judge does cross-dimension weighting
and within-dimension contradiction-audit on top. This avoids rewriting ~60 features to
stop deciding, and respects pillar 2 (features you already trust). Individual features
can migrate to purer raw-opinion form later if one warrants it.

## Posture correctness measurement (Q2 — RESOLVED 2026-08-03)

**Reframe that dissolves the fuzzy-truth problem: don't grade the aggregate posture —
grade the witnesses.** "Was today's DEFENSIVE stance correct?" is genuinely fuzzy (a
correctly-cautious call about a risk that didn't fire this time looks wrong; one day
is mostly noise). But pillar #4 needs **witness-level** accuracy (`source × dimension`),
not aggregate-posture accuracy — and a witness's opinion is far closer to a testable
prediction. Posture-correctness becomes a *derived, secondary* read: the posture is as
good as its track-record-weighted witnesses. **This generalizes the existing
`rec_engine_evaluation` harness (already grades the composite/recommendation witness
on forward alpha) to all witnesses** — extension, not new invention.

**Decisions:**

1. **Per-dimension horizons.** Outcome window varies by dimension — `momentum` short
   (days), `quality`/`thesis_integrity` long (weeks+), `macro_regime` medium. No
   single global window.
2. **Per-dimension minimum-sample gate.** Until a `source × dimension` has enough
   graded samples, it carries a **neutral prior weight**, not its thin observed
   accuracy (one call is noise). Per-dimension, not global — a short-horizon witness
   accumulates samples in weeks while a slow one takes months; a global N would
   starve the slow ones or trust the fast ones too late. (Same n≥20 discipline the
   app already uses — Research Scorecard Phase 3, "building history" captions.)
3. **Grading target is per-dimension-class (Gap A).** Three classes:
   *ticker-forward-return* (`quality`, `momentum`, `thesis_integrity`,
   `position_health`, `sentiment` → forward alpha vs SPY, reuse
   `rec_engine_evaluation`); *cluster/portfolio-drawdown* (`concentration`,
   `structural_risk`, `leverage` → did the flagged cluster draw down?);
   *regime-match* (`macro_regime` → did the call match what the market did?). Without
   this, portfolio-level witnesses go silently ungraded and never earn weight.
4. **Protective witnesses graded on the counterfactual, not naive direction (Gap B —
   the important one).** A STOP/TRIM/DEFENSIVE witness makes a *risk-avoidance*
   prediction; grading it on "did the thing go down" punishes it for every risk that
   didn't fire, so the weighting would **systematically under-weight caution** — a
   posture inversion hiding inside a calibration formula (the app's core posture is
   "when in doubt, recommend nothing"). Grade on the **per-$1k counterfactual** (what
   the avoided/trimmed exposure would have done), per the existing
   `feedback_analytics_integrity` redline. Also solves the acted-position problem
   (once the user acts on a TRIM there's no clean forward return — the counterfactual
   is the only gradeable thing anyway).

## Build sequencing (Q3 — RESOLVED 2026-08-03)

**The insight that dictates the whole order (chicken-and-egg): the calibration half
can't run until track record accumulates.** The Judge weights witnesses by their
history, but that history only exists after witnesses have emitted *and been graded*
for weeks/months. So **weighting and authority are necessarily LAST**, not first;
until then the Judge runs on equal/neutral weights. Forward-only, one phase per
deploy, pause for live review (`feedback_phased_ux_rollout_cadence`).

| Phase | What ships | Authority | Risk / gate |
|---|---|---|---|
| **0 — Contract + instrumentation** | Opinion schema + a *core set* of witnesses tagged and **logged** to a collector. No Judge output. | none | Low — forward-only logging, like `decision_context_capture` / `analyst_target_snapshots` (log-only, inert). No decision logic → likely no heavy review. |
| **1 — Read-only Judge** | Reads logged opinions, synthesizes a posture with **equal weights**, runs the contradiction audit, renders it **with dissent + decomposable votes** — *beside* the current Home brief. | **none** (no override, no gating) | Touches the Daily Brief surface → **first Opus design review here**. The "validate against real days" phase. |
| **2 — Grading harness** | Per-dimension-class graders + counterfactual + min-sample gates run in background. Track records accumulate and become visible. | none | Medium — Q2's machinery. |
| **3 — Evidence-based weighting** | Once min-sample gates clear, synthesis switches from equal weights to **track-record weights**. Posture reflects who's been right. | none (still advisory) | Medium. |
| **4 — Grant authority** | Judge gets **override + gating**; its posture becomes the primary decision surface (action-first, reasoning-underneath), demoting the old brief. | **full** | Highest — biggest decision-logic change → **heaviest Opus review**. |

Load-bearing principles: **authority is last** (after the read-only version earns
trust against real days), and **weighting is time-gated** (physically can't precede
accumulated track record).

**Phase 0 core set (decided):** the ~5–6 witnesses that already emit something close,
covering the highest-value dimensions — **verdict reconciliation** + **composite**
(`quality`/`momentum`), **exit_signals/reduce_calls** (`position_health`),
**concentration gate** (`concentration`), **fragility/structural** (`structural_risk`).
Behavioral, sentiment, macro, leverage witnesses deferred to a second wave.

**Phase 1 placement (decided):** the read-only Judge sits **beside** the existing Home
brief first (observation panel), so its posture can be compared against the user's own
read and the current brief at zero risk. It **replaces** the brief only at Phase 4,
once it has authority and a track record.

## OPEN QUESTIONS — ✅ all resolved 2026-08-03

These are the design inputs the build depends on. To be worked next, in the
conversation, before any code.

1. **The synthesis contract.** — ✅ **RESOLVED 2026-08-03.** See "The synthesis
   contract" section above (fields, routing insight, dimension taxonomy, hybrid
   emission model).

2. **How is "posture correctness" measured?** — ✅ **RESOLVED 2026-08-03.** See
   "Posture correctness measurement" section above (grade witnesses not the aggregate,
   per-dimension horizons + min-sample gates, three grading classes, counterfactual
   grading for protective witnesses).

3. **Build sequencing / phases.** — ✅ **RESOLVED 2026-08-03.** See "Build
   sequencing" section above (5-phase 0→4 plan, authority last, weighting time-gated,
   Phase 0 core set + Phase 1 placement decided).

## Status log

- **2026-08-03** — Brainstormed with the user (Opus 4.8 session). North Star agreed;
  three pillars locked; scope/authority/presentation decided. Plan doc created.
  Open questions #1–#3 identified as blocking. Memory:
  `project_judgment_layer_northstar`. **Not green-lit to build** — user still in
  ideation.
- **2026-08-03** — Q1 (synthesis contract) RESOLVED. Routing insight (dimension
  splits weighting vs contradiction-audit), 8-field opinion shape finalized (added
  `source` and `as_of`+validity beyond the first strawman), ~10-dimension taxonomy,
  pragmatic-hybrid emission model. Moving to Q2 (posture correctness measurement).
- **2026-08-03** — Q2 (posture correctness) RESOLVED. Grade witnesses not the
  aggregate posture (generalizes `rec_engine_evaluation`); per-dimension horizons +
  min-sample gates; three grading classes (Gap A); counterfactual grading for
  protective witnesses so caution isn't structurally penalized (Gap B). Moving to Q3
  (build sequencing).
- **2026-08-03** — Q3 (build sequencing) RESOLVED. 5-phase forward-only plan (0
  instrumentation → 1 read-only Judge → 2 grading harness → 3 evidence weighting → 4
  authority); authority last, weighting time-gated; Phase 0 core set and Phase 1
  beside-not-replace decided. **All three load-bearing design questions now closed;
  design is complete.** Next real gate: Opus design review before Phase 1 code (Phase
  0 log-only could precede). Awaiting user go/no-go on Phase 0.
