# State of the Portfolio — standing thesis

**Date:** 2026-08-06
**Design pass:** `planner` (Opus 4.8, 1M context) — verdict **PROCEED WITH REDUCED SCOPE**, two reframes locked by the user.
**Status:** DESIGN LOCKED — mock pending approval, no code written yet.

Picked up from `docs/plans/next-evolution-2026-08-05.md` Lens 3, item 3 — the third and last of the brainstorm's boundary-pushing items (after Behavioral Fingerprint's decision-moment mirror, F-231, shipped 2026-08-06).

## The reframe (locked, user-confirmed 2026-08-06)

The brainstorm's original wording — "a paragraph the app *commits to* and *grades itself against* next week" — read literally would have the app forecast its own future portfolio-level state and score itself right/wrong. That's a forward forecast, which breaches:
- **§5.8** (`docs/plans/next-evolution-strategy.md` line 495-496): "no feature in DRISHTA may present a point-estimate expected return... Portfolio-level scenario ranges are sound; point forecasts are not."
- **The "AI narrates, never originates" redline** already drawn on Portfolio Q&A (F-225) — the user explicitly declined a forward-looking LLM intent there for the same reason.

**Locked reframe: a stability/consistency ledger, not a predictive scorecard.** Grading means: of the posture claims the app stated last week, how many still hold this week (HELD), and which changed (SHIFTED, from→to) — never whether last week's read was "right." A view that whipsaws weekly is itself a real coherence signal, without forecasting anything.

**Second locked reframe: zero LLM in v1.** The paragraph is a deterministic template composing already-decided states from existing sources — never an LLM opinion. This is stricter than necessary but structurally guarantees the composer can never originate a state its source didn't already decide, avoiding the exact risk the F-225 redline was drawn to close.

## What the thesis says — 5 graded claims, each copied from an existing already-decided source

1. **Risk posture** ← `_rag_label` (bundle["_rag_label"]: All Clear / Monitor / Action Required)
2. **Concentration** ← `_acct_gate_cache` (equity basis; top single-name & sector vs `SINGLE_NAME_CEILING`/`SECTOR_CEILING`) → within / single-name-elevated / sector-elevated
3. **Correlation & structure** ← `_div_label` + `_structural_alert_cache` (new cluster formed) → diversified / elevated / concentrated-cluster
4. **Holdings composite-health** ← `_grow_composites` aggregate tier distribution (count of holdings at/above `COMPOSITE_BUY` vs below) — a portfolio-level distribution, never a per-name call
5. **Action posture** ← Daily Brief buy_candidates vs `_reduce_calls` → deploying / holding / de-risking

**Cited as context, never graded** (exogenous/continuous, not the app's own decided state): market tone (`_market_tone_cache`), fragility number, dominant sector tilt. **Cited when available, cleanly omitted when not** (never recomputed separately): Engine Track Record's `engine_trust_headline()` band/alpha as one coherence line.

## Storage

New table `portfolio_thesis` — genuinely required (this composed posture isn't reconstructable from any existing table). Schema-versioned JSON `claims` blob (matching the `decision_context`/`judgment_opinions` additive pattern, so future claim additions never need a DDL). Columns: `thesis_date`, `iso_year`, `iso_week`, `schema_version`, `claims` (JSONB), `prose`. One row per ISO week — a once-per-ISO-week write guard reusing the live `_home_synth_cache` bundle (no second recompute). **The grade/ledger is derived at read time from the two most recent rows — never stored**, so it can never go stale.

## Grading mechanism

At read time: load this week's row + the most recent prior row within `PORTFOLIO_THESIS_BASELINE_LOOKBACK_DAYS`=14 (covers exactly one missed week of app visits). Per claim: same categorical state = HELD; changed = SHIFTED(from→to); either side `"unavailable"` = `not_comparable` (never a guessed hold). One summary stability line, e.g. "4 of 5 held — posture stable this week." No prior row within the lookback → "First standing view of the record — nothing to compare yet," never a fabricated grade.

## LLM boundary

None in v1. Worked example — this week's deterministic facts: `{risk: Monitor, concentration: sector-elevated (Tech 38% vs 35% ceiling), correlation: elevated (avg 0.58), holdings-health: 6 Buy / 2 below-threshold, action: holding}`. Template composes: *"As of Aug 6, your portfolio sits in Monitor posture. Tech is elevated at 38% (above the 35% ceiling); correlation is running elevated. Of 8 holdings, 6 remain at Buy or better and 2 have slipped below the entry bar. No new deployments and no active reduce calls — a hold-and-watch week."* Contains only classified states its sources already decided — no invented number, no per-ticker call, no "will."

## Render & cadence

**🧾 Summary page** (user's choice — grouped with Engine Track Record and the other credibility/trust surfaces, not the daily Brief), weekly cadence. **Placement, decided 2026-08-06:** a full-width card **below the existing 2×2 pointer grid**, not a new tab and not squeezed into the grid as a 5th cell — Summary is a single flat "at a glance" page by design (no tabs exist there; the 2026-08-05 restructure explicitly decluttered it), and this card is denser than the grid's one-liner pointer cards (5 chips + a paragraph + a 5-row ledger). Collapsed by default to one headline line (e.g. "This week's standing view: Monitor posture · concentration elevated · 4/5 held ▸"), matching the same collapse-by-default density trick Engine Track Record already uses — expands via `st.expander` to reveal the full paragraph + 5 claim chips with current state, plus a "Since last week" ledger sub-section (per-claim HELD/SHIFTED). If it feels cramped once live with real data, a dedicated tab is an easy fallback later — not built preemptively.

## Implementation spec

1. **`stock_analyzer/portfolio_thesis.py`** (new, pure module, no `anthropic` import — enforces the no-LLM invariant by import-absence):
   - `compose_thesis(bundle, acct_gate, reduce_calls, ...) -> dict | None` — returns `{v, thesis_date, claims: {...}, prose}` or `None` if core inputs (`_home_synth_cache`) are offline. Each claim degrades independently to `"unavailable"` when its own source is `None`.
   - `grade_prior(this_week: dict, prior: dict) -> dict` — per-claim `HELD | SHIFTED(from, to) | not_comparable`. Never a single aggregate pass/fail.
2. **`stock_analyzer/constants.py`** — add `PORTFOLIO_THESIS_BASELINE_LOOKBACK_DAYS = 14` (rationale comment: mirrors `THESIS_EROSION_BASELINE_LOOKBACK_DAYS`=10's precedent, sized for one missed week). Decision-bearing — Opus review required, constants-doc table row required.
3. **`stock_analyzer/db.py`** — `save_portfolio_thesis(record)` / `load_portfolio_thesis(lookback_days)`, idempotent one-row-per-ISO-week write. New table via additive DDL.
4. **`app.py`** 🧾 Summary page — once-per-ISO-week write guard (reuse `_home_synth_cache`, no recompute); load prior row; render both states (fresh headline+expander, ledger sub-section). Offline branch when `_home_synth_cache is None`.
5. **Tests** in `tests/test_portfolio_thesis.py`:
   - §5.8 invariant: no claim carries a per-ticker point-return/price-target field; claims are portfolio-scoped enums only.
   - "Narrates, never originates" invariant: module imports no `anthropic`; each claim value is a direct copy of its source's already-decided state.
   - Same-ISO-week boundary: two renders in the same week write exactly one row; a thesis never grades against itself.
   - Lookback boundary: a prior row exactly at the 14-day edge is/isn't selected per the intended inclusive/exclusive rule.
   - Grading conservatism: a claim `"unavailable"` on either side grades `not_comparable`, never HELD/SHIFTED.
   - Per-claim independence: no single aggregate pass/fail is ever emitted.
   - Offline: `_home_synth_cache is None` → `compose_thesis` returns `None`, no row written.
6. **Docs sync same session:** F-ID in `docs/requirements.md`; constants table row in `docs/architecture.md` (stays on the lead per doc-integrity rule, not delegated); `docs/shipped-log.md`; new memory file.

## Review requirement

**Opus `reviewer` pass REQUIRED before commit** — new user-facing decision-adjacent surface, cross-feature coordination, a new `constants.py` value, and a DB write (every one of hard rule #4's triggers at once). Reviewer must specifically re-verify the §5.8 and narrates-never-originates invariants hold in the actual code, not just the design.

## Cross-feature coordination (checked, not duplicated)

- **The Judge's `audit_coherence()` (F-227)** audits per-ticker veto/reduce-call coverage across enforcement surfaces — a different dimension (per-ticker enforcement coverage vs portfolio-level posture stability). Coordination: if `audit_coherence` flags an uncovered risk, the thesis's "action posture" claim must not contradict it (can't say "holding steady" while the Judge flags an uncovered risk).
- **Weekly Debrief email** narrates backward-looking user behavior + benchmark performance ("you did/didn't act, name fell X%"). The thesis is a forward-standing posture graded on its own week-over-week consistency — distinct dimension, must not restate the debrief's "what happened."
- **Engine Track Record (F-229)** is per-call alpha; the thesis is posture. Coordination = cite its headline as one context line, never recompute a separate credibility number.
- **Home's tone banner** reads the same `_market_tone_cache`, so the two can never contradict — tone is cited context here, not a graded claim.

## Risks flagged by the design pass

- **Cadence robustness:** a week the user never opens the app writes no row — handled by the 14-day lookback walk-back, not a cron dependency (cron-side writing is an explicitly deferred robustness enhancement, not v1).
- **Offline discipline:** composer returns `None` (not a hollow row) when `_home_synth_cache` is absent; every claim and the grade degrade independently and conservatively.
