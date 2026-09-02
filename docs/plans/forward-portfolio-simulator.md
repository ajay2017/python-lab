# Plan: Forward Portfolio Simulator (E1) — Phase 1 Diagnostic

**Status: Phase 1 SHIPPED 2026-08-21 as F-245 (`stock_analyzer/forward_sim.py` + the 🧯 After My Rules tab on 🔗 Risk Analysis; 64 tests). Two Opus review passes: FIX-FIRST (5 blocking) → FIX-FIRST (1 blocking) → SHIP. Phase 2 CLOSED 2026-08-29 per its own pre-registered falsifiable criterion below (this line was never updated past the original "NOT started and deliberately gated" wording — corrected 2026-09-02). `scripts/exit_ladder_replay.py` (the W6 measurement) found only 24% of the owner's real closed losing round trips got ANY protective signal before the actual sale, across three independent methodology tightenings converging within 22-24% — the criterion's own bar for closing rather than waiting. Full reasoning: CLAUDE.md's queue entry and memory `project_forward_portfolio_simulator`. Do not re-propose without new evidence.**
**Author:** Ajay Kumar
**Date:** 2026-08-21
**Design model:** Claude Opus 5 (lead)
**Origin:** Pass #1 `next-evolution-strategy.md` Experimental Track E1; re-surfaced as still-open by Pass #2 `next-evolution-2026-08-05.md` Lens 3. Chosen by the user 2026-08-21 over pre-trade impact preview and E2 alpha attribution.

---

## The gap

The app enforces ~20 hard gates, an 8-threshold deterioration ladder (`constants.py:443-450`),
ATR/ratchet stops, regime cash floors, and a risk-off de-risk trim. **Every one was set as an isolated policy decision, and
the app has never been asked what happens when they all fire at once.**

Everything in the accountability layer grades *individual calls* — The Judge (coherence), F-229
Engine Track Record (BUY/EXIT alpha), F-233 Self Track Record (own instinct). Nothing grades the
*interaction* of the rule set. Verified against HEAD:

- `stock_analyzer/stress_test.py` contains **no stop or gate logic at all** — it shocks prices per
  position (beta-adjusted + `_SECTOR_SHOCKS` overrides) and reports P&L. It stops there.
- F-224 Outcome Range is per-name block-bootstrap Monte Carlo — a distribution of outcomes, not a
  replay of the app's own mechanical responses.

So the question **"after my own rules mechanically fire, what book am I left holding?"** is
unanswerable today.

### The specific pathology this is expected to surface

`constants.py:468` sets `RISK_OFF_TREND_MA = 200` and cites Faber (SSRN 962461) — a rule that is
explicitly **two-sided**: exit below the trend MA, re-enter above it.
`exit_advisor.assess_risk_off_derisk()` implements only the exit half. Every use of "redeploy" in
the codebase means *sector rotation within an already-invested book* (the Rebalancer hard-cap-breach
card via `REDEPLOY_CORR_*`, `risk_advisor`'s trim-into-same-sector) — **never re-entry from cash.**

Compose that with the entry side. In a drawdown the entry gates tighten simultaneously: broad
below-MA readings mean many G-20 deterioration WATCHes suppressing adds, W-03's "resolve Act Today
before deploying new capital" fires, G-07 macro suppressions cluster, and the 25%-weighted
technical pillar drags composites below the `COMPOSITE_BUY` gate market-wide.

**Hypothesis: the app systematically de-risks into a drawdown, and every gate that would let you
back in is tightest at the bottom.** Each rule is individually correct; the composition is
expensive. This is a *mechanism argument from the gate definitions, not a measurement* — measuring
it on the real book is this feature's entire purpose. Do not treat it as established.

---

## Phase 1 scope — read-only, no gate touched, no new constants

One question, answered honestly: **for a given shock, which of my own rules fire, on which names,
and what is the surviving book?**

Explicitly OUT of Phase 1: any re-entry directive, any new threshold, any Home surface, any
recommendation change, multi-scenario comparison, probability weighting.

### Faithful-by-construction: reuse the engine, never reimplement it

The single most important design rule. Every tier decision must come from the *same functions the
Brief calls*, driven with a substituted price — never from a parallel reimplementation that can
drift. This is the pattern `portfolio.stop_ladder()` already established (see memory
`project_stop_ladder_and_display`: "reuses the real `protective_stop` + the ATR formula, so the
explainer can't drift from the engine").

Verified APIs that make this possible:

| Input | Source | Derived or assumed? |
|---|---|---|
| Per-position shocked price | `stress_test.run_scenario()` — existing beta-adjusted + sector-override model | **Derived** (existing shipped model) |
| Deterioration tier, day 1 | ~~`exit_advisor.assess_holding(price=shocked)`~~ → **AS SHIPPED: `classify_deterioration_tier` on locally re-extracted scalars.** See the deviation note below. | **Derived** |
| Relative strength | `real_rs + (move_pct − spy_move)` — the engine's own trailing RS (same `_pct_return` over `REL_STRENGTH_LOOKBACK_DAYS`) plus the scenario differential. **Additive, never a replacement** — see deviation #3. | **Derived** |
| Deterioration tier, trend confirmed | `exit_advisor.classify_deterioration_tier(...)` — a **pure scalar function, no pandas, no I/O** — called with `below_ma_count = DETERIORATION_CONFIRM_DAYS` | **Assumed** (the one assumption in Phase 1 — see below) |
| Stop breach | shocked price vs the **ratcheted** protective stop | **Derived** |
| Risk-off armed? | `exit_advisor.risk_off_regime()` compares SPY's last close to `close.tail(200).mean()`. Its two legs are **OR'd** — the trend leg alone arms it, so **no VIX assumption is needed.** Substitute the shocked SPY close. | **Derived** |
| Risk-off trim targets | `exit_advisor.assess_risk_off_derisk()` — ranks β × weight from real `held_data["risk_metrics"]["beta"]` | **Derived** |
| Fragility (the outer AND-gate on risk-off) | current live `_fragility_cache`. Fragility is a property of *the book*, not of the shock, so today's value is the honest input — no post-shock re-derivation | **Derived** |

**The one stated assumption.** `assess_holding` reads `below_ma_count` from *real, pre-shock*
history, so a healthy name shocked −25% has `below_ma_count = 0` → `trim_active` is False → only
the `deep_exit` shortcut fires. That is not a bug; it is the genuine day-1 answer (2-of-3 trend
confirmation truly has not happened yet). So Phase 1 renders **two columns side by side**:

- **"Fires immediately"** — the position's real pre-shock `below_ma_count`.
- **"Fires once trend confirms"** — `below_ma_count = DETERIORATION_CONFIRM_DAYS`, modelling the
  scenario's multi-week duration.

> **AS SHIPPED — deviation #3, and the most consequential one** (recorded here because the two
> rows above originally claimed `assess_holding(price=shocked)` "verbatim, bit-exact to the
> engine", which is **not what shipped** and overstated the fidelity guarantee this whole document
> exists to make).
>
> **Neither column calls `assess_holding`.** Both call the pure `classify_deterioration_tier` on
> scalars `forward_sim.replay_position` re-extracts itself. Two reasons the delegation didn't work:
> (1) `assess_holding` returns `None` whenever the tier is `None`, so a healthy name yields no
> scalars at all and the table could not be populated; and (2) — the one that actually matters —
> its `rel_strength` is computed from *pre-shock* history, so passing `price=shocked` silently
> mixes a post-shock price with an unrelated pre-shock relative strength. A first cut shipped
> internally with the scenario differential *replacing* real RS, which an Opus review caught as a
> live defect: in the 6 of 9 sector-targeted scenarios, a holding outside `_SECTOR_SHOCKS` gets
> `est_move = 0.0`, so RS read `0 − (−10) = +10` — a fabricated *positive* strength that switched
> TRIM off on a name the Brief was calling TRIM/EXIT in the same session. RS is now **additive**
> (`real_rs + (move_pct − spy_move)`), which reduces exactly to the engine's value at zero shock.
>
> The duplicated extraction is the module's standing risk, so it is fenced two ways: the peak-window
> math is **shared**, not copied (`exit_advisor._peak_window_bars`, extracted for this and called by
> `assess_holding` too), and `test_zero_shock_matches_assess_holding` pins tier + 7 scalars against
> `assess_holding` at zero shock across every peak-window branch, with a non-zero-RS benchmark and a
> NaN-Close bar.

The **gap between the two columns is itself the finding** — it quantifies how much of the book's
protection depends on confirmation lag. This is a bracket, not a point estimate, which is the
honest framing and keeps Phase 1 clear of Pass #1 §5.8 (no point-estimate forecasts; this is
portfolio-level mechanical consequence, not a return forecast).

### The stop trap — must not be got wrong

Per memory `project_stop_ladder_and_display` (45 days old — **re-verify at build time**), a held
position has two divergent stop numbers:

- `r["stop"]` = the **raw ATR entry stop**, and on the Analysis page it is *overwritten to the
  manual price* when a manual stop is active. Using it here would under-report protection.
- `_sa_holding["Stop"]` (from `build_portfolio_df`) = `max(ATR stop, ratchet floor)` or a tighter
  manual override — **the stop the Brief actually acts on. This is the one to use.**

Also required (same memory, F3): `Gap to Stop (%)` is `None` in a float64 column, so `None`
becomes `NaN` — test with `pd.isna(...)`, never `is None`. And per
`feedback_none_sentinel_meets_pandas`, assert at the render layer.

**Honesty note to carry into the UI:** `protective_stop` is *stateless* — it re-derives the floor
from the current gain tier each run and has no high-water memory. A simulated stop-out therefore
means "the stop the app would recommend today was breached," **not** "a resting broker order
filled." The output must not imply an automatic exit occurred.

### Output — the four things worth knowing

1. **Stop-out clustering.** Which names breach, and — cross-referenced against the F-189
   correlation clusters — *how concentrated in time*. The expected headline: 14 names behave like 4
   real bets. This is the finding most likely to change a policy value.
2. **Surviving book.** Positions remaining, value-weighted surviving beta, sector mix, and
   proceeds raised. **AS SHIPPED, narrowed:** the spec said "vs the current `regime_targets` cash
   floor and beta ceiling" — the cash-floor comparison was dropped, because saying "you are below
   your regime cash floor" *is* a redeployment directive in everything but grammar, and Phase 1
   commits to issuing none. The raw proceeds figure is shown; the judgement is not.
3. **Ladder load.** Count of names at WATCH / TRIM / EXIT in each of the two columns, plus the
   risk-off overlay's trim list (which `assess_risk_off_derisk` already excludes from
   double-surfacing via `exclude_tickers`).
4. **The re-entry blind spot.** **AS SHIPPED, narrowed:** the spec said "which entry gates would be
   blocking on the way out." Shipped instead as a plain prose statement (UI + User Guide) that the
   de-risk rule is the exit half of a two-sided trend rule and the re-entry half does not exist.
   Enumerating *which* gates would block requires replaying the entry pipeline (composite
   recomputation at shocked prices, macro proximity, W-03) — a materially larger build than the
   exit-side replay, and one that would need its own faithfulness proof. Deferred to Phase 2 scope
   rather than half-built.

### Placement

A 5th tab on **🔗 Risk Analysis**, beside the existing four (`app.py:12149`:
`📊 Dashboard | 📋 Action Plan | 🔥 Stress Testing | 🎲 Outcome Range`). This is the correct
adjacency — raw shock → outcome distribution → **after my rules fire** — and it adds no page to a
nav that already carries 25. Not on Home: this is a low-frequency audit instrument, and a standing
Home number would invite exactly the regime-chasing churn Concept D's UX review rejected.

Scenario picker reuses `stress_test.SCENARIOS` (9 shipped scenarios). Per Pass #1's explicit
mandate, **default to and validate one scenario first** — `bear_entry` (SPY −20%).

**AS SHIPPED, this was widened:** the picker exposes all 9 scenarios, defaulting to `bear_entry`.
Restricting the list would have been theatre, not safety — the 🔥 Stress Testing tab already
exposes the same 9 through the same `run_scenario` shock model, so a narrower picker here would
protect nothing while implying the wider set was somehow unvalidated. Recorded as a deliberate
deviation from the spec above rather than silently.

---

## Phase 2 — CLOSED 2026-08-29, per its own pre-registered falsifiable criterion (was: NOT approved, NOT scoped for build)

A re-entry / redeployment discipline closing the Faber asymmetry. This was a **new user-facing
decision surface with new policy constants**, so it would have required: a `planner` (Opus) design
pass, the threshold values set explicitly with the user, and a `reviewer` (Opus) pass — CLAUDE.md
hard rules #1 and #4. **The gate below — "do not scope it until Phase 1 shows the pathology is real
in the actual book" — is exactly what got tested, and it came back negative for the premise this
phase would have addressed:** the W6 exit-ladder replay found real exits are frequently late or
absent (only 24% of closed losing round trips got any protective signal before the actual sale),
not early — the opposite emphasis from Phase 1's original worry that the app de-risks too
aggressively and would need a re-entry counterpart. Building Phase 2 to aid re-entry after an
over-eager exit would solve a problem this measurement doesn't support. **Closed per the
criterion's own pre-registered rule, not a judgment call made after seeing the number.** Don't
re-propose without new evidence — memory `project_forward_portfolio_simulator` has full detail.

---

## Governance

- Phase 1 touches **no gate, no threshold, no scoring formula, and writes nothing to the DB.** It
  is a read-only diagnostic over existing pure functions.
- It *is* a new user-facing surface, and it reasons about gate logic — so per CLAUDE.md hard rule
  #4 it takes a **`reviewer` (Opus) pass before the commit ships**, cited in the commit body. The
  review's highest-value target is not the arithmetic: it is **whether the replay is faithful to
  the engine** (right stop, right function, assumption honestly labelled).
- `feat(` commit needs `Design =` / `Build =` trailers (hard rule #5).
- Definition-of-Done: new F-ID in `docs/requirements.md`; module section in
  `docs/architecture.md`; in-app User Guide; this plan's own Status line; `docs/shipped-log.md`.
  No new constant expected — if one appears, it needs a constants-table row and a policy
  conversation.
