# Cost-Routing Ledger

A running log of **how work was routed across model tiers** (the agent roster in
[`.claude/agents/`](../.claude/agents/)) and the **savings on delegated work**.

This is a *routing-decision + delegated-savings* log — **not** a total-cost
dashboard. Read the scope notes before trusting a number.

---

## What this does and does not measure

- ✅ **Captures delegated work** (Sonnet/Haiku subagents) — that's where a real
  token count is reported back at completion.
- ❌ **Does NOT capture Opus orchestration** (planning, design, diff review,
  commits). Those run on the lead model by design and aren't precisely
  instrumented from inside the session. So a row's "saved" figure is the saving
  **on that delegated slice**, not the session's total cost.
- ❌ **Not auto-instrumented.** Rows are appended by hand at commit time. A gap
  means "nobody logged it," not "nothing happened."
- 📊 **Authoritative total cost** lives in the Anthropic Console usage dashboard
  (per-token API) or your Claude subscription usage view. This ledger is the
  *why-we-routed-it-this-way* companion to that, not a replacement.

## Price ladder (list prices, per 1M tokens)

> **Corrected 2026-06-05.** An earlier version of this ladder assumed Opus at
> $15 / $75. Opus 4.8 list price is **$5 / $25**. That collapses the Opus↔Sonnet
> gap: Sonnet is now **0.6×** Opus (not 0.2×), so delegated build saves ~40%, not
> ~80%. Haiku is **0.2×** (saves ~80%). All figures below have been recomputed.

| Tier | Model | Input | Output | vs Opus 4.8 |
|---|---|---|---|---|
| Plan / Review / Lead | Opus 4.8 | $5 | $25 | 1× (baseline) |
| Build | Sonnet 4.6 | $3 | $15 | **0.6× (60%)** |
| Docs | Haiku 4.5 | $1 | $5 | **0.2× (20%)** |

Sonnet is **0.6×** of Opus 4.8 on **both** input ($3 vs $5) and output ($15 vs
$25), so delegated build work costs **exactly 60%** of the Opus price regardless
of the input/output mix — i.e. it **saves 40%**. Haiku is **0.2×** (saves 80%).
The **ratio is mix-independent**; only the absolute-dollar columns below assume a
mix (~85% input / 15% output, typical for read-heavy coding) and are therefore
**ballpark**.

## Conventions

- One row per delegated task (or per inline decision worth recording).
- **Tier** = the model that did the work.
- **Saved** = Opus-equivalent cost − actual cost (≈ 40% of Opus-equiv for
  Sonnet, ≈ 80% for Haiku). For inline lead work, mark `n/a — lead`.
- Record **decisions NOT to delegate** too (one-liners done inline as lead), so
  the log doesn't imply we delegate everything.

---

## Ledger

| Date | Task | Tier | Tokens | Est. cost | Opus-equiv | Saved | Notes |
|---|---|---|---|---|---|---|---|
| 2026-06-04 | Grow Today reach/funnel caption — build | Sonnet | 17,845 | ~$0.09 | ~$0.14 | ~$0.06 | Decided, mechanical UI edit; Opus designed + reviewed diff |
| 2026-06-04 | Grow Today reach caption — Known-Behaviours doc row | — | — | — | — | n/a — lead | One-row doc edit; handoff overhead > saving, done inline |
| 2026-06-04 | Lift candidate-funnel magic numbers into constants — build | Sonnet | 27,784 | ~$0.13 | ~$0.22 | ~$0.09 | Pure refactor, values unchanged; Opus designed + reviewed |
| 2026-06-04 | Constants refactor — architecture.md table rows | — | — | — | — | n/a — lead | Inline doc edit alongside the review |
| 2026-06-04 | This ledger (`docs/cost-routing.md`) — create + seed | — | — | — | — | n/a — lead | New doc; structure/framing is a design choice, not mechanical |
| 2026-06-05 | Rate-limit resilience — scope + plan doc | — | — | — | — | n/a — lead | Architecture/design decision; mapped the data layer via Explore (read-only) |
| 2026-06-05 | Rate-limit resilience Phase 1 — refresh cooldown (build) | Sonnet | 25,663 | ~$0.12 | ~$0.21 | ~$0.08 | Decided UI gating; Opus designed spec + reviewed diff |
| 2026-06-05 | User Guide — "how candidates are found" explainer | — | — | — | — | n/a — lead | Content + engine-accuracy = judgment, not a mechanical doc row; a Haiku handoff would've needed a spec as detailed as the content, so done inline |
| 2026-06-05 | User Guide — "first run / data-population order" section | — | — | — | — | n/a — lead | Correctness-critical onboarding content; grounded in real UI affordances (Trade Journal, Rebuild flow); done inline as lead |
| 2026-06-08 | Grow Today funnel-caption fix (macro-blocked mislabel + "16 of 12" arithmetic) | — | — | — | — | n/a — lead | Small, fully-designed display-logic fix; per corrected pricing the ~40% Sonnet margin isn't worth the handoff on a one-spot edit |
| 2026-06-08 | Grow Today: macro block count/overflow (16-vs-4) + macro-aware empty-state CTA (hide futile re-scan) | — | — | — | — | n/a — lead | Two small display-only fixes from the same review thread; done inline as lead |
| 2026-06-08 | Position sizing — single-name ceiling cap + warning (risk.py + 4 call sites + Trade Plan/Watchlist UI) | Sonnet | 25,065 | ~$0.12 | ~$0.20 | ~$0.08 | Risk-discipline fix touching sizing logic; Opus scoped the spec + reviewed the diff before commit |
| 2026-06-09 | Fragility gauge (Phase 1) — pure `assess_fragility` + constant + Home render | — | — | — | — | n/a — lead | Judgment-heavy: severity bands, risk-display copy, the call to reuse existing beta bands rather than add thresholds. Thin Sonnet margin not worth the handoff on a design-laden spec — done inline as lead |
| 2026-06-09 | Fragility gauge — pre-commit review (risk-severity display + new constant) | — | — | — | — | n/a — lead | Opus reviewer tier by design; verdict SHIP, 3 non-blocking findings (2 fixed: beta-consistency multiplier + visible-withhold note) |
| 2026-06-09 | Fragility gauge — architecture.md constants + Known-Behaviours rows | Haiku | 39,118 | ~$0.06 | ~$0.27 | ~$0.21 | Mechanical doc rows; the strong-saving lane (~80%). Token count includes reading the large architecture.md to match house style |
| 2026-06-09 | Today's Brief header declutter — pair tone+fragility (2 cols) + 1-row Quick Research | — | — | — | — | n/a — lead | Pure layout (st.columns wrap, no logic/thresholds/gates); user-chosen option. No decision logic to review → inline as lead |

### Running totals (delegated work only)

| | Tokens | Est. cost | Opus-equiv | Saved |
|---|---|---|---|---|
| **To date** | 135,475 | ~$0.52 | ~$1.04 | **~$0.52 (≈50% blended — Sonnet builds ~40%, Haiku docs ~80%)** |

---

## How to read the trend

The saving **scales with mechanical volume**. A session heavy on rote
build/refactor/docs delegates a lot and saves a lot; a judgment-heavy session
(design decisions, threshold calls, diff review) delegates little and saves
little — because that work *should* stay on Opus. A low "saved" number on a
judgment-heavy day is the routing working correctly, not a miss.

Savings that never appear in tokens — and are arguably the real ROI: the Opus
review tier catching problems before they reach the live app (e.g. a `None`
sector bug, a read-only race, a leaked API key were all caught pre-commit, and a
def-order NameError that *slipped* the gate on 2026-06-05 sharpened the review
checklist), plus context hygiene and faster iteration on the mechanical parts.

**Implication of the corrected pricing (2026-06-05):** with Opus 4.8 at $5/$25,
Build→Sonnet now saves only **~40%** (was ~80% under the wrong $15/$75
assumption). That thinner margin means handoff overhead (a subagent re-reads
context cold) eats a bigger fraction of the saving — so the bar for delegating a
*small* build to Sonnet is higher than before; tiny edits are often cheaper done
inline as lead. The strong-saving lane is now **Docs→Haiku (~80%)**, and the
durable case for keeping Opus on plan/review (catching the costly mistakes) is
unchanged. Net: route for *quality and context hygiene* first, dollars second —
the dollar gap is no longer the headline it looked like.
