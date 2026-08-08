# DRISHTA — System Proprioception (Pipeline Trust Layer)

**Date:** 2026-08-07
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 4.8 (1M context)
**Status:** Phase 1 SHIPPED 2026-08-07 (F-235) — owner-only `🩺 System Trust` page + cron heartbeat + top-of-Home degraded-only chip. `stock_analyzer/system_health.py`, `cron_heartbeat` table (§6.32, needs one-time manual DDL), `db.save_cron_heartbeat`/`load_cron_heartbeats`, `cron_runner.main()` heartbeat wiring, `tests/test_system_health.py` (16 tests). Opus review (Opus 4.8): FIX-FIRST, 1 blocking (a DST-blind freshness `expected_hour_et` that would false-amber the Home chip daily in EDT) → fixed same session → SHIP. Full suite green (3479); antipattern + constants-doc gates green. **Phase 2 (the suppression meta-gate) remains BRAINSTORM**, gated behind a future `planner` (Opus) design pass on its suppression rule + the provable-input list. See `docs/shipped-log.md` and memory `project_system_proprioception`.
**Mockup:** [`docs/mockups/system-proprioception-phase1-mockup.html`](../mockups/system-proprioception-phase1-mockup.html) — 4 frames: Home chip (degraded + healthy), full page (degraded + healthy). Saved into the repo per `feedback_mockup_first_ux` (don't leave a mock to live only in a conversation a `/clear` can erase).
**Reserved F-ID:** assign at build, not before — nothing user-facing has shipped.

---

## Origin

Surfaced during the 2026-08-07 "what's next / how do we push the boundary" brainstorm, triggered by the cron migration (GitHub Actions → Railway, 2026-08-07, all 5 lanes) — specifically by *how* a real bug was found en route: a **DDL that was never applied**, so a table the app writes to didn't exist and writes were failing silently. It was found by accident, during unrelated surgery, not by any system that was watching for it.

That accident is the whole thesis of this doc.

## The idea (and the reframe that makes it a boundary-push, not another surface)

DRISHTA has spent ~two months building an increasingly deep **decision brain** — gates, composites, the Judge, track records, the predictive shadow layer. The DDL bug was not a decision-logic bug. It was a **proprioception** bug: a limb went numb and the brain couldn't feel it. We only tripped over it.

The real question the incident raises is not "what new intelligence do we build" but **"how many other numb limbs are there that we simply haven't tripped over yet?"** Structurally there are several candidates for silent failure:

- Manual-DDL tables (`analyst_target_snapshots`, `daily_regime`, `model_predictions`) — every one is a silent failure waiting for the next feature to forget the manual step.
- 5 cron lanes that must all fire. Railway is a *better bet* than GitHub's best-effort `schedule`, not a guarantee.
- The 3-source data failover (Finnhub → yfinance → FMP) can degrade silently by design.
- ~30 `session_state` cache keys on a `None`-on-failure contract that's been violated often enough to need an AST gate (`check_antipatterns.py`).

**Reframe:** this is NOT greenfield. We've already grown scattered proprioceptive *organs*. What's missing is a **spinal cord** connecting them, plus two or three nerves we never wired. The restraint — one reflex, precisely bounded — *is* the feature.

### What we already sense vs. what is genuinely numb

| Failure class | Currently sensed? | Cheap to detect? |
|---|---|---|
| Cron lane didn't fire | Partial — in-script dead-man's-switch, per-lane (commit `25e1fec`) | yes |
| **Table doesn't exist / DDL never applied** | **No — this is the wound** | yes (freshness/existence) |
| **Ran the whole day on the backup data source** | No — failover is silent by design | yes |
| **A gate was disabled because its input was offline** | Cache goes `None`, but the *user* isn't told | yes |
| Stale / cross-source disagreement | Partial — price cross-check, Brief tone staleness banner | yes |

Key property of the DDL class: it's detectable **without knowing the cause.** Dead cron, unapplied DDL, or a crash all present identically as *"an expected write did not happen for today."* A pure freshness/existence check catches all of them — and it's the exact check that would have tripped on the bug that started this.

---

## Relationship to existing invariants

**Consistent with the founding posture, by construction:**

- **"The app decides, it does not inform."** Proprioception points the same lens — *make hidden risk visible with a hard banner* — back at the app itself. Phase 1 only *informs* (owner diagnostic). Phase 2's one reflex *strengthens* the deciding posture: it can add a scoped suppression when an input is provably gone; it can never manufacture a call or loosen a gate.
- **Deterministic gates are the free safety net; paid agents are for judgment only.** Every proprioception check is an existence / freshness / timestamp read. **No LLM, near-zero cost.** This belongs in the always-on deterministic tier — it is `check_antipatterns.py` energy aimed at the *running pipeline* instead of the source tree, not a paid-judgment surface.
- **Offline contract (`None`, never an empty container).** This layer is the natural *consumer and reporter* of that contract — it reads caches via `util.get_or_offline` and reports which came back offline, rather than adding new sentinels.

---

## The hard part: the meta-gate (where the real design risk lives)

The diagnostic side is easy. The dangerous, interesting part is: **should degraded pipeline state ever change what the app decides?**

A meta-gate is *categorically different* from a normal gate:
- A normal gate says *"this position fails concentration."* — scoped, specific.
- A meta-gate says *"the app itself might be blind — don't trust today's calls."* — sweeping.

If that over-fires, we've built the opposite of trust: an app that frequently refuses to help, which teaches the user to override it — classic **alarm fatigue**, and now it's noise. Designing *against* that failure mode is the point.

### The tiered discipline (bias to disclose over suppress)

- **Green — invisible.** All nerves healthy → the app behaves *exactly* as today, zero new UI. Non-negotiable: proprioception visible when nothing is wrong is just clutter. It must be the dog that doesn't bark.
- **Amber — disclose, don't suppress.** Degraded but decisions still have their inputs → one confidence line, reusing the existing banner pattern: *"Running on backup data source (FMP)"* / *"Sentiment offline — composite excludes it today."* The call still ships; its confidence is visibly dented.
- **Red — suppress, but only scoped and only provable.** The meta-gate may suppress a decision **only when it can prove that decision's specific required input is absent** — e.g. *"Protective EXIT calls suppressed: the scan lane didn't run today."* Never a vague "things seem off, blocking everything."

**The single guardrail that keeps this from becoming a tyrant:** suppression is allowed *only* for a decision whose **named input can be proven missing** — never a blanket block on a hunch. This is the invariant the Phase 2 design pass must protect above all.

---

## Two design constraints that are easy to miss

1. **Who watches the watcher?** If the health surface itself depends on a cron or a table, it fails silently the same way. So the robust form is **pull-based, computed at render time** from cheap live checks (*does this table have a today-row? does this source answer?*) — never something that relies on its own background job having run. Proprioception you have to trust a job for isn't proprioception.
2. **Invisible when healthy** (restated because it's the most common way this class of feature rots): if it shows yellow on a normal day, it gets tuned out and the one day it matters is missed.

---

## Phasing (two deliverables, wildly different risk — never ship as one)

### Phase 1 — the diagnostic surface (safe, pure upside)

An **owner-only** "System Trust" panel answering one question: *"Can I trust what the app told me today?"* Pure *informing*, changes no decision, owner-gated (same owner-whitelist fail-safe as other owner-only surfaces).

Proposed checks (all pull-based, render-time, no LLM):
- **Cron liveness** — last successful fire per lane (premarket / scan / intraday / eod / weekly thesis), against expected cadence.
- **Table freshness / existence** — for each table that should have a fresh row for today/this-period, does it? *This single check closes the DDL blind spot* — a missing or empty table lights up regardless of cause.
- **Data source served** — which of Finnhub / yfinance / FMP actually served today's marks; flag if the whole day ran on a backup.
- **Cache integrity** — which `session_state` caches populated vs. came back `None` this run.

Why first: it's almost free, it changes nothing, and it directly cauterizes the wound that prompted this whole thread. It gives us eyes immediately, and lets us *watch* which degradations are common enough to be worth gating on before we design Phase 2.

**Placement — LOCKED 2026-08-07 (with user):**
- **A new owner-only `🩺 System Trust` nav page**, in the **RESEARCH group, directly after `🔬 Model Lab`.** Hidden from read-only viewers via the *same* `db.is_readonly()` group-filter that already hides Model Lab ([app.py:2175](../../app.py#L2175)) — the two are natural neighbours (both owner-only operational/experimental, not shared portfolio views). New page dispatch follows the existing `elif page == "🔬 Model Lab":` pattern ([app.py:26271](../../app.py#L26271)).
- **Phase 1 DOES ship the top-of-Home chip** (mock Frame A): a single degraded-only line at the top of Home that renders *only* when a check is amber/red, linking to the System Trust page. Invisible when healthy (Frame B). **Informs only — suppresses nothing** (that's Phase 2). It must join the `_home_synth_cache` signature if it reads any Home-built input, per CLAUDE.md (new Home inputs MUST join it or ship stale).

**Copy language — LOCKED 2026-08-07 (with user):** every row reads in **plain business language as the headline** (e.g. "Protective exit scan", "Volatility forecast", "Portfolio holdings"), with the **internal identifier shown only as a small muted mono hint** beneath it (`exit_signals`, `model_predictions`, `_port_df_enriched`). Rationale: this is an owner-only *diagnostic* — when a store goes red the technical name is what you'd go fix (apply the DDL to *that* table), so it stays visible for debuggability, but it never leads. No raw cache-key / table-name as a primary label.

**The one genuinely-open Phase 1 task (load-bearing):** the **table → lane → cadence inventory** — which cron lane is responsible for writing which table, and on what cadence, so the freshness check knows what "should exist today" means. This is the map that makes the DDL detector real; without it, check ② is guesswork. Build this inventory from `cron_runner.py` + the DB schema *before* writing the check. (Do NOT hardcode "today" — freshness/"now" comes from `market_time.now_et`/`today_et`, per the recurring-defect gate; a naive `date.today()` would fail the antipattern check anyway.)

### Phase 2 — the meta-gate (behavior-changing, real design risk)

The amber/red banners that dent confidence or scope-suppress a call. This **touches decision behavior** → by Hard Rule #4 it needs a **`planner` (Opus) design pass + `reviewer` (Opus) before ship**, and it needs at least one **new policy constant** (the freshness threshold at which an input counts as "dead" — a policy decision to set *with the user*, in `constants.py`).

Deliberately second, deliberately slower. The prerequisite is having watched the Phase-1 panel long enough to know:
1. Which degradations actually occur, and how often (so Red doesn't fire on noise).
2. **Which existing decisions have a single, provable required input** whose absence would justify a scoped Red suppression. *This list is what tells us whether Phase 2 is worth building at all* — if almost no decision has a cleanly-provable single input, the honest answer is "stay at Phase 1 / amber-only."

**Phase 2 is NOT approved by this doc.** It is scoped here so the discipline is on record; it earns a build only after Phase 1 has run and the provable-input list is real.

---

## Coordination note

Phase 1 is terminal display — it consumes existing caches / DB state and feeds nothing downstream. Phase 2's Red suppressions, if ever built, must dedupe against surfaces that already suppress on the same input (per `feedback_single_surface_priority` — dedupe by dimension), and must publish/consume via the standard `session_state` pattern rather than each surface re-deriving pipeline state.

## Definition-of-Done reminders (for whoever builds this)

- New owner-only surface → F-ID in `docs/requirements.md`; in-app User Guide entry.
- Any new constant (Phase 2 freshness threshold) → `docs/architecture.md` constants table + `check_constants_documented.py` green.
- Bump *this doc's own `**Status:**` line* when a phase ships (the status-drift lesson — plan docs lag fastest after a same-session ship).
- Memory: capture the provable-input list from the Phase-2 design pass — it's the non-obvious decision.
