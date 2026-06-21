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
| 2026-06-09 | Today's Brief — group Refresh macro + Refresh Signals into one button row (moved button + scan handler up; def-before-use deps verified) | — | — | — | — | n/a — lead | Pure layout move; verified held_tickers/_parallel_load_all defined above the new location; grep-confirmed single widget key + single handler. Inline as lead |
| 2026-06-09 | Today's P&L honesty (Fix A) — relabel "(held)" + scope tooltip + fail-loud "N of M priced" caption | — | — | — | — | n/a — lead | Correctness/honesty fix; computed number byte-for-byte unchanged (no decision logic/threshold). Diagnosed -0.57% vs broker -5% as held-only-mark scope (excludes today's realized trades). Inline as lead |
| 2026-06-09 | Tier B — TRUE positions day-P&L (new daily_pnl.py equity-delta + daily_snapshots table I/O + app.py wiring) | — | — | — | — | n/a — lead | Correctness-critical P&L + new DB writer; built inline as lead (judgment-heavy: equity-delta design, baseline/tz logic, snapshot-write timing). Unit-tested the pure math |
| 2026-06-09 | Tier B — Opus review (2 rounds) | — | — | — | — | n/a — lead | Mandatory review (P&L + DB writer + RLS). Round 1 caught a BLOCKER: `not is_open` write gate fired in pre-market → persisted stale prior-close as the baseline + blocked the real post-close write. Round 2 verified the post-close-window fix → SHIP |
| 2026-06-09 | Adopt Conventional Commits standard — .gitmessage template + DEVELOPMENT.md spec + CLAUDE.md pointer | — | — | — | — | n/a — lead | Process/docs; defining the convention is judgment, not a mechanical doc row. Template+docs only (no hook), per user choice |
| 2026-06-10 | Data-outage triage + #1 honest empty-state (holdings-exist-but-load-failed → fail-loud + retry) | — | — | — | — | n/a — lead | Live incident triage (diagnosed Yahoo bundle-throttle vs Finnhub quotes) + display/fail-loud fix; judgment-heavy, inline |
| 2026-06-10 | Resilience part (a) — burst-taming (DATA_LOAD_MAX_WORKERS 4→2 + stagger) | — | — | — | — | n/a — lead | Operational tuning to stop tripping Yahoo's throttle; small, inline |
| 2026-06-10 | Resilience part (b) — last-known-good bundle cache (daily_pnl-style: db table + load_all write-through/fallback + staleness banner) | — | — | — | — | n/a — lead | Correctness-critical (load_all hot path) + new DB writer/RLS; built inline as lead, serialization unit-tested |
| 2026-06-10 | Resilience part (b) — Opus review | — | — | — | — | n/a — lead | Mandatory review (hot path + DB writer + RLS). Traced hot-path safety, ran tz round-trip + _json_safe live → SHIP, no blockers |
| 2026-06-18 | Home perf — memoize pre-tab synthesis (signature-gated session cache + explicit-only recompute triggers) | — | — | — | — | n/a — lead | Correctness-critical (Daily Brief + recommendations DB write + cross-page coordination caches); judgment-heavy boundary placement + NameError-risk bundle. Built inline as lead; +4-indent of the 525-line region done via a one-shot script, py_compile-verified |
| 2026-06-18 | Home perf — Opus review (bundle completeness, DB-write gating, lock, signature) | — | — | — | — | n/a — lead | Mandatory review (Brief + DB writer). Grepped all post-region reads vs the restore set → no escaped local; verified holdings/manual_stops signature keys + no threshold change → SHIP |
| 2026-06-18 | Home perf — architecture.md Known-Behaviours + coordination-cache rows | Haiku | 42,606 | ~$0.07 | ~$0.29 | ~$0.22 | Mechanical doc rows (the strong-saving lane). Handed the facts; doc-writer matched house style |
| 2026-06-18 | Rec-engine evaluation — diagnose Recommendations History (action rate, acted/missed outcome, missed-alpha, composite-band + rec-type breakdowns) | — | — | — | — | n/a — lead | Judgment-heavy data interpretation: read the metric defs + engine verdict logic, separated signal from measurement artifact (verdict-layer working; numbers regime/sample-confounded). Verdict: don't retune the engine — fix the scorecard |
| 2026-06-18 | Rec-history scorecard hardening — SPY-relative alpha + maturity window + by_verdict (module + page) | — | — | — | — | n/a — lead | Correctness-critical analytics the user trusts to judge the engine; built inline as lead, unit-tested the alpha/maturity/verdict math |
| 2026-06-18 | Rec-history hardening — Opus review (alpha math, maturity exclusion, rollup propagation, no-gate-change) | — | — | — | — | n/a — lead | Mandatory review of trust-bearing metrics + a new constant. Traced alpha direction + SELL=None + SPY-build degradation → SHIP, no blockers |
| 2026-06-18 | Rec-history hardening — architecture.md Known-Behaviours + constant row | Haiku | 49,651 | ~$0.08 | ~$0.34 | ~$0.26 | Mechanical doc rows (strong-saving lane); handed the facts |
| 2026-06-19 | Market-holiday awareness — diagnose ("Market Open" on Juneteenth vs the independent "Could not load" rate-limit burst; confirmed independent) + plan | — | — | — | — | n/a — lead | Live-incident triage; mapped market_status blast radius, separated the display bug from the cold-load burst; user-chosen hardcoded-calendar approach |
| 2026-06-19 | Market-holiday awareness — build (NYSE calendar constants + is_market_holiday/is_trading_day/market_status + Tier-B guard swaps) | — | — | — | — | n/a — lead | Correctness-critical (Tier-B snapshot/baseline timing); dates cross-checked against the official NYSE/ICE calendar via WebSearch/WebFetch; unit-tested helpers + full date set |
| 2026-06-19 | Market-holiday awareness — Opus review (date set + Tier-B semantics + return-shape compat) | — | — | — | — | n/a — lead | Mandatory review; verified 28 holidays no weekend/observance errors, half-day branch ordering, additive calendar_stale key, no gate change → SHIP |
| 2026-06-19 | Market-holiday awareness — architecture.md Known-Behaviours + constants rows | Haiku | 45,826 | ~$0.07 | ~$0.32 | ~$0.25 | Mechanical doc rows (strong-saving lane); handed the facts |
| 2026-06-19 | "Could not load" incident — multi-round diagnosis (reboot triage → bundle-cache observability → swallowed-exception surfacing → root cause) + fix (targets max() default + drop NaN-close cached bars) | — | — | — | — | n/a — lead | Live correctness incident; iterative deploy-to-diagnose converged on a latent crash (max() on a NaN cached close), NOT the assumed cache-empty/rate-limit. Built inline as lead; guard logic unit-tested |
| 2026-06-19 | "Could not load" incident — architecture.md rows (NaN safeguards + bundle_cache health + load-error surfacing) | Haiku | 46,252 | ~$0.07 | ~$0.32 | ~$0.25 | Mechanical doc rows (strong-saving lane); handed the facts |
| 2026-06-19 | Cold-load burst reduction (rate-limit P2) — provider circuit-breaker (api_health.in_cooldown + orchestrator _providers_for) + SPY-fetch dedup + constant | — | — | — | — | n/a — lead | Failover hot path, correctness-sensitive; found + reused the already-approved Phase-2 design; in_cooldown logic unit-tested |
| 2026-06-19 | Burst reduction P2 — Opus review (failover, live-price-path unaffected, all-cooled fallthrough, import cycle) | — | — | — | — | n/a — lead | Mandatory review (touches failover); verified breaker scope + no hard-block + no decision change → SHIP, 0 blocking |
| 2026-06-19 | Burst reduction P2 — architecture.md Known-Behaviours rows + constant | Haiku | 52,370 | ~$0.08 | ~$0.36 | ~$0.28 | Mechanical doc rows (strong-saving lane); handed the facts |
| 2026-06-21 | Analysis "$nan" — diagnose (up/downstream impact sweep: price chokepoint vs the latent `technical_score` reduced-signal-set distortion) + boundary fix (`compute_indicators` NaN-Close strip + `load_all` last-non-NaN price) | — | — | — | — | n/a — lead | Same NaN-is-truthy class as af854a8 but on the LIVE path; fixed at the data boundary, not the one surface (user directive: trace impact across, fix the class). Built inline as lead; py_compile-verified |
| 2026-06-21 | NaN-Close hygiene — Opus review (healthy no-op, index alignment, None-path consumers, drop-all vs drop-trailing) | — | — | — | — | n/a — lead | Mandatory review (scoring/verdict input path); verified zero healthy-path drift + all `current_price` None consumers handled → SHIP, 0 blocking. First attempt died on a transient agent-API 401, re-run cleanly |
| 2026-06-21 | NaN-Close hygiene — architecture.md Known-Behaviours row | Haiku | 29,669 | ~$0.05 | ~$0.20 | ~$0.15 | Mechanical doc row (strong-saving lane); handed the facts. Lead trimmed one borrowed-neighbour sentence post-hoc |

### Running totals (delegated work only)

| | Tokens | Est. cost | Opus-equiv | Saved |
|---|---|---|---|---|
| **To date** | 401,849 | ~$0.94 | ~$2.87 | **~$1.93 (≈67% blended — Sonnet builds ~40%, Haiku docs ~80%)** |

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
