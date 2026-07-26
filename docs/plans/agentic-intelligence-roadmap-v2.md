# DRISHTA — Agentic Intelligence Roadmap v2 (360° Completion)

**Date:** 2026-07-24
**Author:** Ajay Kumar
**Analysis model:** Claude Opus 4.8
**Status:** Phase 1 (D2), Phase 2 (D1 + O1), and Phase 3 (D3 + O4) ALL SHIPPED as of
2026-07-26. Phase 4 (O5/O6/O2/D4) not yet started. See the status table below for the
live per-item state.

> **Supersedes:** [agentic-intelligence-roadmap.md](agentic-intelligence-roadmap.md)
> (v1), which shipped P1-P5 and closed P6 (Autonomous Pattern Discovery) as
> evaluated-and-shelved. v1 is complete and remains the historical record for the
> shipped features; this v2 is the living roadmap.

---

## Why a v2

v1 was a **red team** — six adversarial ideas that attack your own positions before the
market does. Five shipped (P1 Thesis Red Team, P2 Multi-Agent Debate, P3 Structural
Scanner, P4 Info Asymmetry, P5 Regime Stress); P6 was correctly shelved. But mapped
against the user's stated goal — *360° visibility around signals, opportunity, and
risk* — v1 has a lopsided shape:

- **Risk: deep.** Three of five shipped ideas, atop an already-strong quantitative risk
  layer.
- **Signals: medium.** Well covered on data-trust (P4), thin on signal *coherence*.
- **Opportunity: shallow, and reactive.** Only P2 (debate) touches it, and only on
  candidates the engine already surfaced.

**Five of six v1 ideas were defensive.** That isn't an accident — defensive features are
safe to build (a risk warning can't make you reckless), while offensive/opportunity
features risk becoming a FOMO/churn machine that fights the app's core calm-advisor
posture. v2 deliberately closes **both** the remaining defensive whitespace *and* the
offensive gap, in a priority order that respects two principles below.

---

## Two governing principles

**1. Defense-first is correct on merit, not just preference (the asymmetry rule).**
A hole that lets a *loss* through costs more than an equal-sized *missed gain* — drawdowns
compound against you; missed gains don't. So completing the defensive perimeter generally
outranks optimization-grade offense. The exception: one or two offensive/defensive items
are so high-leverage they jump their same-side queue (see phasing).

**2. Offense must attack YOUR inaction, never the market's movement (the calm rule).**
Everything on the offensive side must be grounded in the user's *own* history and *own*
absence — retrospective and positional, never predictive or urgent. *"You have
historically made money doing X and you're structurally not doing X right now"* is calm
and true. *"NVDA is ripping, get in"* is the exact churn we spent months suppressing
(§2B, `feedback_calm_advisor_not_daytrading`). Awareness-only, never a gate, never framed
as "act now."

The offensive side audits three forms of **absence**: **Names** (setups you never take),
**Size** (conviction right but position too small), **Exposure** (what a regime rewards
that you're absent from).

---

## The full board — 10 candidates

Every "reuses / partially built" note below was **verified against HEAD 2026-07-24**
(the P6 near-duplicate taught us not to trust memory here).

| ID | Candidate | Side | What it closes | Verified reuse / build state |
|---|---|---|---|---|
| **D1** | Hidden Same-Bet Detector | 🛡️ | Positions that *look* diversified but share one thesis/assumption — hidden concentration a price-correlation scan (P3) can't see | **Genuinely new.** No code reads thesis text across multiple positions; `thesis_advisor.run_batch_review()` loops positions *independently*. Reuses P3's 🧩 Intelligence page + `user_thesis` column (`db.py:999-1004`). |
| **D2** | Exit Red-Team ("Challenge This Exit") | 🛡️ | We red-team reasons to *own* continuously but never reasons to *sell* | **Mostly scaffolded.** `build_exit_corpus()` stub exists (`debate_agent.py:247`); `run_debate()` already type-agnostic; `_format_corpus()` already branches on `debate_type`. Scope pre-specified in `multi-agent-debate.md` Phase 2. Needs: stub body + button wiring at TRIM/EXIT cards (`app.py:6717`, key off `trim_ticker`). |
| **D3** | Signal Coherence Auditor | 🛡️ | When our own surfaces (composite / debate / erosion / regime-fit) *disagree* on a name, you reconcile it in your head today | **New join.** `signal_reconciliation.reconcile_signals()` exists but only reconciles momentum-vs-composite (+earnings/news) — does NOT touch debate verdict, thesis-erosion, or regime-fit. New part = the per-ticker join + contradiction surfacing. |
| **D4** | Catalyst-Specific Stress | 🛡️ | "Which upcoming *event* most threatens THIS book" — event twin of P5's regime stress | Reuses P5 synthesis pattern + Catalyst Watch calendar. (Phase 4 — not deeply audited yet.) |
| **O1** | Missed-Opportunity Pattern | ⚔️ Names | The setups you *systematically* pass on — P6's instinct, pointed at missed-recs (real data) not decision_context (17 rows) | **Substrate exists, pattern is new.** `distinct_missed()`/`missed_split()` (`recommendations_history.py:415/479`) return per-ticker missed names *with* alpha vs SPY — but NO grouping by sector/price-band/setup. New part = the category pattern + surfacing. |
| **O4** | Watchlist Resurrection | ⚔️ Names | Dead watchlist names now at the setup you originally wanted | Reuses watchlist + current signals. Cheapest item on the board. |
| **O5** | Sizing Alpha | ⚔️ Size | Flat sizing across conviction tiers = alpha left on the table | Reuses trades + conviction + outcomes. Genuinely new. |
| **O6** | Premature-Exit Cost | ⚔️ Size | Winners sold early vs. your own avg winner-hold | Reuses `build_closed_lots()` (`investor_mirror.py:50`). |
| **O2** | Conviction Under-Confidence | ⚔️ Size | Where your rating is systematically too low on names that *worked* | **Extension, new slice.** `conviction_alignment()` (`:316`) gives spearman + `orphan_convictions` by *current* score — does NOT isolate under-confidence vs *realized* winners (no outcome join). New part = the realized-outcome directional slice. |
| **O3** | Regime Exposure Gap | ⚔️ Exposure | What this regime rewards that you're absent from | **Data-gated / parked** — needs `daily_regime` history for an honest base rate (~3 days old; same problem that narrowed P5). |

---

## Agreed priority — phased

**Phase 1 — the asymmetric cheap win**
1. **D2 Exit Red-Team.** #1 overall. Half-scaffolded already, reuses `debate_agent.py`,
   and closes the most dangerous asymmetry in the app: **panic/premature selling
   destroys more books than bad buys, and it's the one decision we never adversarially
   challenge.** Highest value-per-effort on either side — and it's defense.

**Phase 2 — one flagship structural gap per side**
2. **D1 Hidden Same-Bet Detector.** The deepest genuine *risk* gap — three names all long
   "AI capex" or "rates fall" is a single point of failure in a diversification costume.
   Extends P3's page.
3. **O1 Missed-Opportunity Pattern.** The flagship offensive — rebalances the layer to a
   real 360° and has the best offensive data story. High enough value to jump ahead of
   the remaining defensive items.

**Phase 3 — meta-layer + quick win**
4. **D3 Coherence Auditor.** Deliberately *after* Phase 2 — the auditor is only as useful
   as the number of surfaces it can reconcile, so it's worth more once D1/O1 exist. Also
   the item most at risk of becoming noise; needs the calm-posture discipline applied
   hardest.
5. **O4 Watchlist Resurrection.** Cheapest item — slot in parallel whenever there's slack.

**Phase 4 — optimization tier (nice-to-have, not perimeter-critical)**
6. O5 Sizing Alpha + O6 Premature-Exit Cost (the sizing/exit pair).
7. O2 Conviction Under-Confidence (only if the pair leaves a gap).
8. D4 Catalyst-Specific Stress (a P5 variant, not a new axis).

**Parked:** O3 Regime Exposure Gap — data-gated on `daily_regime` accumulation.

### Why this order and not "pure defense then offense"
D2 and D1 genuinely are the top two on merit (asymmetric+cheap, then deepest-risk), so
defense-first and value-first agree there. But forcing D3/D4 ahead of O1 would put a
noise-prone meta-layer and a P5-variant above the single feature that completes the 360°
— ideology over value. This keeps defense weighted up (3 of the top 5 are shields)
without starving the one offensive move that matters.

---

## Process (carried from v1, non-negotiable)

Each item, in priority order, before it ships:
1. A dedicated plan doc (linked from the status table below once written).
2. **Opus design review** of that plan.
3. Implementation (Sonnet implementer).
4. **Opus pre-ship review** (every one of these touches an LLM and/or a
   recommendation-adjacent surface).
5. Docs sync in the same session (requirements.md + architecture.md + this roadmap's
   status + shipped-log + memory + User Guide).

**Design constraints (from v1, apply to ALL):** strictly additive (never gates a
threshold/score/recommendation), graceful degradation, day-cached at minimum, never
fabricates data, user is author of record. Plus v2's **calm rule** for every offensive
item (above).

**The HEAD-audit rule:** before writing each dedicated plan, re-verify the "reuses /
partially built" facts against HEAD — the table above is dated 2026-07-24 and code moves.

---

## Status table

| ID | Candidate | Plan doc | Status |
|---|---|---|---|
| D2 | Exit Red-Team | [docs/plans/exit-red-team.md](exit-red-team.md) | **SHIPPED 2026-07-24** (commit `fb3676e`; F-197 Phase 2) |
| D1 | Hidden Same-Bet Detector | [docs/plans/hidden-same-bet-detector.md](hidden-same-bet-detector.md) | **SHIPPED 2026-07-24** (commit `5e8479f`; F-199) |
| O1 | Missed-Opportunity Pattern | [docs/plans/missed-opportunity-pattern.md](missed-opportunity-pattern.md) | **SHIPPED 2026-07-24** (commit `b11e34a`; F-201) |
| D3 | Signal Coherence Auditor | [docs/plans/signal-coherence-auditor.md](signal-coherence-auditor.md) | **SHIPPED 2026-07-26** (commit `df36c6d`; F-202) |
| O4 | Watchlist Resurrection | [docs/plans/watchlist-resurrection.md](watchlist-resurrection.md) | **SHIPPED 2026-07-26** (commit `631e6ba`; F-203) |
| O5 | Sizing Alpha | *(to write)* | Phase 4 |
| O6 | Premature-Exit Cost | *(to write)* | Phase 4 |
| O2 | Conviction Under-Confidence | *(to write)* | Phase 4 |
| D4 | Catalyst-Specific Stress | *(to write)* | Phase 4 |
| O3 | Regime Exposure Gap | — | Parked (data-gated) |
