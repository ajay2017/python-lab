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

| Tier | Model | Input | Output | vs Opus |
|---|---|---|---|---|
| Plan / Review / Lead | Opus 4.8 | $15 | $75 | 1× (baseline) |
| Build | Sonnet 4.6 | $3 | $15 | **exactly 1/5** |
| Docs | Haiku 4.5 | $1 | $5 | **exactly 1/15** |

Sonnet is 1/5 of Opus on **both** input and output, so delegated build work
costs **exactly 20%** of the Opus price regardless of the input/output mix
(Haiku ≈ 7%). The **ratio is mix-independent**; only the absolute-dollar columns
below assume a mix (~85% input / 15% output, typical for read-heavy coding) and
are therefore **ballpark**.

## Conventions

- One row per delegated task (or per inline decision worth recording).
- **Tier** = the model that did the work.
- **Saved** = Opus-equivalent cost − actual cost (≈ 80% of Opus-equiv for
  Sonnet, ≈ 93% for Haiku). For inline lead work, mark `n/a — lead`.
- Record **decisions NOT to delegate** too (one-liners done inline as lead), so
  the log doesn't imply we delegate everything.

---

## Ledger

| Date | Task | Tier | Tokens | Est. cost | Opus-equiv | Saved | Notes |
|---|---|---|---|---|---|---|---|
| 2026-06-04 | Grow Today reach/funnel caption — build | Sonnet | 17,845 | ~$0.09 | ~$0.43 | ~$0.34 | Decided, mechanical UI edit; Opus designed + reviewed diff |
| 2026-06-04 | Grow Today reach caption — Known-Behaviours doc row | — | — | — | — | n/a — lead | One-row doc edit; handoff overhead > saving, done inline |
| 2026-06-04 | Lift candidate-funnel magic numbers into constants — build | Sonnet | 27,784 | ~$0.13 | ~$0.66 | ~$0.53 | Pure refactor, values unchanged; Opus designed + reviewed |
| 2026-06-04 | Constants refactor — architecture.md table rows | — | — | — | — | n/a — lead | Inline doc edit alongside the review |
| 2026-06-04 | This ledger (`docs/cost-routing.md`) — create + seed | — | — | — | — | n/a — lead | New doc; structure/framing is a design choice, not mechanical |
| 2026-06-05 | Rate-limit resilience — scope + plan doc | — | — | — | — | n/a — lead | Architecture/design decision; mapped the data layer via Explore (read-only) |
| 2026-06-05 | Rate-limit resilience Phase 1 — refresh cooldown (build) | Sonnet | 25,663 | ~$0.12 | ~$0.62 | ~$0.50 | Decided UI gating; Opus designed spec + reviewed diff |

### Running totals (delegated work only)

| | Tokens | Est. cost | Opus-equiv | Saved |
|---|---|---|---|---|
| **To date** | 71,292 | ~$0.34 | ~$1.72 | **~$1.38 (≈80% on delegated slice)** |

---

## How to read the trend

The saving **scales with mechanical volume**. A session heavy on rote
build/refactor/docs delegates a lot and saves a lot; a judgment-heavy session
(design decisions, threshold calls, diff review) delegates little and saves
little — because that work *should* stay on Opus. A low "saved" number on a
judgment-heavy day is the routing working correctly, not a miss.

Savings that never appear in tokens — and are arguably the real ROI: the Opus
review tier catching problems before they reach the live app (e.g. a `None`
sector bug, a read-only race, a leaked API key were all caught pre-commit),
plus context hygiene and faster iteration on the mechanical parts.
