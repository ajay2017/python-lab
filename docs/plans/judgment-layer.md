# The Judge — Portfolio Judgment Layer — Design Plan

**Date:** 2026-08-03
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 4.8 (brainstorm/design stage)
**Opus review (design):** Round 1 (2026-08-03) — Claude Opus 4.8 (1M context) (`claude-opus-4-8[1m]`) — **FIX-FIRST**: North Star, three pillars, and Q3 phase order sound; Phase 0 (log-only) safe to start; Q1 contract had 1 structural hole + 3 lesser gaps. **All 4 blocking findings + 3 non-blocking incorporated** (protective-veto routing class, offline-protective confidence degradation, post-reconcile consumption, honest Q2 grading-class scoping, constants enumeration, Phase-1 caveats). Phase 1 *code* will still need its own Opus review at build time per hard rule #4.
**Status:** Phase 0 + Phase 1 SHIPPED 2026-08-03 (DDL applied, cache-hit/miss capture bug found via live screenshot and fixed same session). **Phase 2 (grading harness) SHIPPED 2026-08-03** — built, Opus-reviewed (FIX-FIRST, 2 blocking, both fixed), and recovered from a concurrent-session commit race that briefly broke `main` (see status log). Phase 3 (evidence-based weighting) not started.

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

- **Protective dimension vs acquisitive dimension, same subject → VETO (hard gate,
  NOT weighting).** *(Added post-review — this was the original routing's structural
  hole.)* A protective/gating dimension (`position_health`, `concentration`,
  `structural_risk`, `leverage`) emitting TRIM/REDUCE/over-limit **suppresses** an
  acquisitive one (`momentum`, `quality`) emitting ADD/size-up — it does **not**
  average with it. This preserves the app's existing hard suppressions
  (`decision_bucket.suppress_orphans_under_reduce_call`, the `_reduce_calls`
  ADD-suppression consumers, add-winner-on-deterioration): *"gates are hard
  suppressions with visible banners, not soft warnings."* A strong momentum ADD
  (+0.8) must **never** out-vote a live protective signal (−0.4) into a net-positive
  posture. The `−1…+1` blend is valid **only within the non-protective set**.
- **Two non-protective dimensions, same subject → WEIGHTING (jobs 1 & 4).** e.g.
  `momentum` vs `sentiment` — the normal multi-factor picture; the Judge weighs them
  (by track record) into one call.
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

**Offline protective witnesses degrade posture confidence (added post-review, finding
#2).** An offline (`None`) *protective* witness (`concentration`, `structural_risk`,
`leverage`, `position_health`) must NOT be silently excluded from synthesis — exclusion
reads as "no risk present" and tilts the posture bullish precisely because the risk
sensor went dark (the `None`-vs-`[]` trap at portfolio scale). Rule: when a protective
witness is offline, the Judge's overall confidence **degrades** and the posture
surfaces "reduced visibility" — it may **never** render a clean bullish posture with a
dark protective sensor.

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

**Witnesses consume POST-reconcile outputs, never producer-raw streams (added
post-review, finding #3).** The Brief already de-contradicts itself — `decision_bucket
._reconcile_act()` folds a "hold/monitor" critical-news card into a same-ticker reduce
card, and `feedback_single_surface_priority` dedupes by *dimension*. If witnesses read
the **raw** `act_today`/`review_list` they will see both halves of a contradiction the
Brief already collapsed and double-count it as a fresh conflict. Witnesses MUST read
the post-`split_defensive`/`_reconcile_act` (published-cache) outputs, resolving
tickers via the canonical `decision_bucket._ticker()` (which already handles the
`ticker=None` → `action.trim_ticker` macro-card shape).

## Posture correctness measurement (Q2 — RESOLVED 2026-08-03)

**Reframe that dissolves the fuzzy-truth problem: don't grade the aggregate posture —
grade the witnesses.** "Was today's DEFENSIVE stance correct?" is genuinely fuzzy (a
correctly-cautious call about a risk that didn't fire this time looks wrong; one day
is mostly noise). But pillar #4 needs **witness-level** accuracy (`source × dimension`),
not aggregate-posture accuracy — and a witness's opinion is far closer to a testable
prediction. Posture-correctness becomes a *derived, secondary* read: the posture is as
good as its track-record-weighted witnesses. **Scope-honest framing (corrected
post-review, finding #4): this reuses the `rec_engine_evaluation` harness for ONE of
the three grading classes (ticker-forward-return); the cluster-drawdown and
regime-match graders are NET-NEW.** The `recommendations` table today persists only
`composite_score` + `momentum_score` — `quality`/`sentiment`/`thesis_integrity` are not
logged there, so grading them as separate witnesses needs net-new Phase-0 logging.
Plan Phase 2 for roughly 2× the "just extend the harness" framing.

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
   `position_health`, `sentiment` → forward alpha vs SPY, **reuses**
   `rec_engine_evaluation` / `recommendations_history.compute_outcomes`);
   *cluster/portfolio-drawdown* (`concentration`, `structural_risk`, `leverage` → did
   the flagged cluster draw down? — **net-new grader**; `structural_scan_cache`
   persists a dated `cluster_snapshot` so `structural_risk` is feasible, but
   `concentration` is session-only today and needs net-new persisted substrate too);
   *regime-match* (`macro_regime` → did the call match the market? — substrate solid
   via `daily_regime`, but **net-new grader**). Without this, portfolio-level
   witnesses go silently ungraded and never earn weight.
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
Behavioral, sentiment, macro, leverage witnesses deferred to a second wave. **Review
caveat (finding #3/#4):** 3 of these (verdict `signal_reconciliation.py`, composite,
`exit_signals`) already emit near-opinions with persisted history; **`concentration`
has NO persisted history today** (`_acct_gate_cache` is session-only) — Phase 0 must
add its logging substrate, not just tag an existing stream.

**New decision thresholds that MUST live in `constants.py` (hard rule #1, enumerated
post-review):** the min-sample gate N (per dimension), the per-dimension horizons, the
neutral-prior weight, the confidence-degradation factor for offline/stale witnesses,
the `−1…+1` signal cutpoints, and the contradiction-audit "opposite signal" boundary.
These are investment-policy decisions — set with the user, never inline literals.

**Phase 1 scope caveats (post-review):** (a) the Judge duplicates a risk surface
already shown elsewhere *intentionally* (a deliberate, temporary exception to
single-surface-per-dimension) purely as a validation overlay — resolved when Phase 4
replaces the brief. (b) Phase 1 equal-weight running validates the **schema, the
contradiction-audit, and the decomposition** — it does **not** validate
posture-correctness, which is gated behind Phase 3 weights; don't over-sell it. (c)
Slow dimensions (`quality`, `thesis_integrity`) at n≥20 on a ~5–7-name book take many
months to earn weight, so the Judge runs equal-weight for a long time — expected, not a
bug.

**Phase 1 placement — REVISED 2026-08-03 after mockup review.** Original decision was
"beside the existing Home brief" (embedded on Home, next to Grow Today). Building a
static HTML mockup first (`docs/plans/judgment-layer-phase1-mockup.html`, per
`feedback_mockup_first_ux`) and viewing it rendered surfaced a real problem the text
description missed: full per-ticker decomposition (every witness + evidence, per the
decomposability redline) across 5+ picks plus a portfolio-wide section makes Home
substantially longer, right after a separate pass to make Grow Today more scannable.
**Revised decision: a standalone nav page, "🧑‍⚖️ The Judge," in the `MAIN` nav group
between Home and Summary** (not embedded in Home). Rationale: (1) fits Phase 1's actual
role — a page you visit to cross-check, not a wall of text competing with the
actionable Brief; (2) sets up a cleaner Phase 4 story — *promoting* a page that has
earned trust into Home's primary surface is a clearer graduation moment than something
quietly embedded the whole time; (3) gives Phase 2/3 (grading-harness output,
track-record stats) room to grow without ever crowding Home. Grow Today's pick cards
stay untouched (no teaser link) to keep this phase's scope tight — decided alongside
the placement pivot.

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
- **2026-08-03** — Opus 4.8 design review (via `reviewer` subagent): **FIX-FIRST**.
  Confirmed the North Star, pillars, Q3 order, and Phase-0 safety. Caught 1 structural
  hole — Q1's routing dichotomy omitted the **protective-veto class** (protective dims
  hard-suppress acquisitive ones; my rule would have softened an existing hard gate
  into a weighted vote, re-surfacing vetoed ADDs at Phase 4) — plus offline-protective
  posture inversion (#2), raw-vs-reconciled double-count (#3), and an over-claimed
  "extension not invention" on Q2 grading (#4, only 1 of 3 classes truly reuses the
  harness; `concentration` has no persisted history). **All 4 blocking + 3 non-blocking
  incorporated into the doc above this session.** Phase 0 now cleared to start on user
  go-ahead; Phase 1 code needs its own Opus review at build time.
- **2026-08-03** — **Phase 0 SHIPPED.** New pure module
  `stock_analyzer/judgment_opinion.py` (opinion schema, `build_opinion()`,
  `PROTECTIVE_DIMENSIONS`/`ACQUISITIVE_DIMENSIONS`/`ADVISORY_DIMENSIONS` constants);
  new table `judgment_opinions` (`docs/architecture.md` §6.29, inert until DDL
  applied) + `db.save_judgment_opinions_batch()`/`load_judgment_opinions()`. All 5
  core-set witnesses wired: `exit_advisor`/position_health (app.py, exit-signal
  capture block), `composite_score`+`scanner_momentum`+`verdict_reconciliation`/
  quality+momentum (app.py, Grow Today new_picks), `fragility_gauge`/structural_risk
  (app.py, fresh-compute site only), `concentration_gate`/concentration (app.py — a
  **real** single-name/sector ceiling breach check against
  `SINGLE_NAME_CEILING`/`SECTOR_CEILING`, not a placeholder, since this table IS the
  new persisted substrate the design review said concentration lacked). Log-only:
  nothing reads this table yet, no decision logic changed, no UI changed. Verified:
  py_compile clean, `check_constants_documented.py` passes (no new constants — Q2's
  thresholds are correctly deferred to Phase 2/3), 69 targeted tests pass. No Opus
  code-review required for Phase 0 itself (no constants/gate/scoring-formula touched
  per hard rule #4) — the design was already Opus-reviewed above. Next: the table DDL
  needs manual application in Supabase before any data lands (ships inert until then,
  same as `analyst_target_snapshots`). Phase 1 (read-only Judge) is next and DOES
  require its own Opus review before shipping.
- **2026-08-03** — DDL applied by user in Supabase; `judgment_opinions` now live.
- **2026-08-03** — **Phase 1 built**: mockup-first (`docs/plans/judgment-layer-phase1-mockup.html`,
  per `feedback_mockup_first_ux`) surfaced that embedding full per-ticker decomposition
  beside Grow Today would make Home too long — placement REVISED to a standalone
  "🧑‍⚖️ The Judge" nav page (see the placement section above). Built
  `stock_analyzer/judgment_synthesis.py` (the veto/contradiction/blend routing engine)
  + 9 new `JUDGMENT_*` constants + the new page. **Opus 4.8 code review: FIX-FIRST, 2
  blocking.** (1) `verdict_reconciliation` (a meta-witness already synthesizing
  momentum+composite+news+earnings) was emitted as a non-advisory peer under `quality`
  alongside `composite_score` — this double-counted composite in the blend AND caused
  the contradiction audit to flag their by-design divergence as a peer conflict on
  every verdict_divergence-class ticker (the app's own known incident class). Fixed:
  `verdict_reconciliation` now built with `advisory=True` — still shown in the
  decomposition as context, excluded from blend/contradiction. (2) The veto only
  tagged the FIRST positive acquisitive opinion as suppressed; sibling positive
  opinions on the same vetoed ticker rendered as live green votes directly beneath the
  veto banner — an incoherent double-surface. Fixed: `veto["suppressed"]` is now the
  full list of every positive acquisitive opinion on that ticker, and the UI marks all
  of them. Also fixed while in there (non-blocking, cheap): the veto now picks the
  MOST SEVERE (lowest-signal) protective opinion when more than one qualifies, not
  simply the first found; added `JUDGMENT_SCORE_MIDPOINT` constant to remove the last
  inline `-1..+1` normalization literal. Two non-blocking findings deliberately left
  as documented technical debt for Phase 3/4 (posture_signal on veto still only uses
  the winning protective opinion's own value, ignoring other same-ticker opinions —
  harmless today since posture_signal isn't rendered anywhere yet; protective "clear/
  calm" signals are positive, which could inject bullishness into a future blend —
  both flagged in code comments for revisit before any authority is granted). All
  fixes verified: py_compile clean, `check_constants_documented.py` passes, 3076/3076
  tests pass, 4 manual synthesis scenarios re-verified after the fix (advisory
  exclusion, multi-opinion suppression, most-severe-wins, regression). Review = Opus
  reviewer (Opus 4.8, claude-opus-4-8[1m]): FIX-FIRST, 2 blocking — both fixed same
  session, cited in commit body per hard rule #4.
- **2026-08-03** — **Real production bug found via live screenshot** (same session,
  after the reviewed Phase 1 shipped): user opened the new Judge page and saw
  `structural_risk` listed as "no live witness this run," but Home's own fragility
  gauge was showing a live "1.1x, calm" read at the same moment. Traced the cause: the
  entire fragility + composite/momentum/verdict/exit_advisor opinion-capture logic
  had been written inside the `_home_synth_cache` cache-**MISS** branch only (a
  ~850-line `else:` block, lines 4139-4993). On any cache-**HIT** render — which is
  the common case for the rest of a session after the first Home load of the day —
  none of that capture code ran at all. Only the concentration-gate capture (placed
  earlier in the file, before the hit/miss split) reliably fired every render. This
  meant the Judge would show almost nothing beyond concentration for most of a user's
  session, even though Home's own gauges/picks were rendering live data from the
  cached bundle the whole time. **Fix:** removed the opinion-capture code from its two
  in-branch locations and moved it to a single consolidated block placed at the
  hit/miss reconvergence point (right after `_load_slot.empty()`, where the codebase's
  own "Structural alert banner" section already established the pattern of "placed
  HERE because the hit/miss synthesis converges here"). The new block reads
  `st.session_state["_fragility_cache"]` (reliably republished by BOTH branches) and
  `_daily_brief` (also valid on both branches) instead of the miss-branch-local
  `_fragility`/`_gt_today` variables, and independently re-derives the WATCH/TRIM/
  EXIT/RISK_OFF ticker list from `_daily_brief.get("act_today"/"review_list")` rather
  than reusing the pre-existing Behavioral Fingerprint exit-signal capture's own list
  (`_exit_signals_to_save`, which is a different feature's table and was deliberately
  left untouched — it may have the same miss-only limitation, which is a separate,
  pre-existing condition outside this session's scope, not introduced by the Judge
  work, and not silently "fixed" as a side effect here). Verified: py_compile clean,
  3076/3076 tests pass, constants-doc check passes. This is exactly the class of bug
  `feedback_mockup_first_ux`'s "verify shipped vs image" principle exists to catch —
  found because the user actually looked at the live page rather than trusting the
  design/code review alone. **Opus 4.8 scoped review of the fix: SHIP, 0 blocking**
  (1 non-blocking defense-in-depth note — the `act_today`/`review_list` re-derivation
  loops read `_daily_brief.get(...)` directly rather than `(_daily_brief or {}).get(...)`
  like the Grow Today read two lines later; unreachable today since `_daily_brief` is
  guaranteed a dict by that point on both branches, but fixed for consistency anyway).
  Verified equivalence of the re-derived WATCH/TRIM/EXIT/RISK_OFF logic against the
  removed code, confirmed the upsert key keeps every-render calls idempotent, and
  confirmed no dangling references to the removed in-branch variables.
- **2026-08-03 — Phase 2 (grading harness) built and shipped**, same session, after
  confirming three scope forks with the user: (1) per-dimension horizons —
  momentum=5, quality=20, position_health=10 trading days, with concentration=20 /
  structural_risk=10 proposed by pairing to the closest precedent (flagged for
  reconsideration once real data accumulates); (2) reuse `BEHAVIORAL_MIN_SAMPLE_N`
  as the shared min-sample gate rather than a new parallel constant; (3) grading
  triggered by a manual "▶ Run grading" button on the Judge page, not automatic on
  every load. Reused `predictive_analytics.forward_alpha_at_horizon()` (the Entry
  Timing tab's own mechanism) for ticker-class grading rather than reinventing it;
  built a net-new `portfolio_value_series_from_snapshots()` aggregation over
  `daily_snapshots` for the portfolio-drawdown class. New table `judgment_grades`
  (§6.30) + `stock_analyzer/judgment_grading.py`.

  **Opus 4.8 code review: FIX-FIRST, 2 blocking**, both fixed before commit: (1)
  protective witnesses (`position_health`/`concentration`/`structural_risk`) were
  graded by naive sign-match against forward alpha — scoring a TRIM/caution
  "correct" only when the flagged name/portfolio subsequently underperformed, i.e.
  marking the witness WRONG for every risk that correctly didn't fire. This is
  exactly the anti-caution posture inversion Q2's Gap B was designed to prevent,
  caught here before any biased grade was persisted (only ~1-2 days of opinion
  history existed at review time). Fixed by making both graders WITHHOLD (return
  `None`) for any protective dimension until a real counterfactual grader exists —
  the button handler now counts these separately ("N protective (withheld)") rather
  than silently conflating them with "pending maturity." (2) `_sign_match()` scored
  an exactly-flat realized outcome or a zero opinion signal as `correct=False`
  despite its own docstring saying "nothing to grade" — since both graders round
  alpha to 2 decimals, `0.0` is realistically reachable and would have biased
  accuracy downward on pure noise; fixed to return `None` (excluded from
  `track_record_summary`'s N and accuracy) instead of `False`.

  **A concurrent-session commit race broke `main` mid-build.** A different session
  (Sonnet 4.6, working the unrelated Trade Journal BUY-confirmation-card feature)
  staged and pushed this session's in-progress `app.py` Judge edits under its own
  commit (`a7c2542`, titled `feat(trade-journal): add BUY confirmation card before
  DB write`) — bundling unrelated work together, without the supporting files
  (`judgment_grading.py`, the `constants.py`/`db.py` additions) Phase 2's app.py
  code depends on, and without an Opus review citation for the Judge-related
  portion (hard rule #4 applies to it same as any other Judge commit). From that
  push until this fix, opening "🧑‍⚖️ The Judge" page raised
  `ModuleNotFoundError` and crashed — confirmed live via `git log`/`git show`
  before acting, not assumed from the reviewer's claim alone. Recovered via a
  **forward-fix commit** (not a history rewrite — `a7c2542`'s legitimate
  trade-journal work was left untouched) that added the missing files together
  with both review fixes, restored the working import chain (verified before
  push), and carried the review citation the mislabeled commit was missing. Same
  incident class `feedback_concurrent_session_git_races` already tracks — worth
  re-reading that memory before any future multi-file build that spans more than
  one tool-call round-trip, since the working tree is shared with any other active
  session.

  Verified after both the code fixes and the recovery commit: py_compile clean,
  3076/3076 tests pass, constants-doc check passes, 5 manual scenarios (2 protective
  withholds, normal ticker grading, flat-outcome None-not-False, track-record
  exclusion) all pass. Deferred as documented non-blocking follow-ups: a
  matured-but-fetch-failed opinion is never automatically retried on a later run;
  `grade_portfolio_opinion`'s portfolio-alpha-vs-SPY is a weaker proxy for
  idiosyncratic cluster/concentration risk than a true cluster-specific measure
  would be — both acceptable for this pass, revisit in a Phase 2b.
