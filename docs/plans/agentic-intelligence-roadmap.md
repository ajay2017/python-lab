# DRISHTA — Agentic Intelligence Roadmap

**Date:** 2026-07-23
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 4.6
**Status:** IDEATION — not yet designed or built. This is a brainstorm-to-roadmap
document. Each idea requires a dedicated plan + Opus design review before any build starts.

> **Scope:** Brainstormed 2026-07-23. Core premise: shift DRISHTA from reactive/defensive
> signal-reading to proactive/offensive adversarial intelligence — actively generating
> adversarial pressure on holdings and theses before the market does. Inspired by the
> security pen-tester analogy: find the vulnerability in your own position before a bad
> actor (market) exploits it.

---

## Analogy anchor

| Cybersecurity / Red Team | DRISHTA equivalent |
|---|---|
| Pen tester attacks their own system | Agent attacks your own portfolio thesis |
| Finds vulnerability before bad actor exploits it | Finds deterioration signal before market prices it in |
| Authorized breach = the discovery IS the value | Adversarial scenario = the stress test IS the insight |
| Red team vs. Blue team | Bear agent vs. Bull agent per holding |
| Continuous vulnerability scanning (not one-time audit) | Continuous thesis erosion monitoring (not one-time pre-mortem) |

---

## Priority Order

| Priority | Idea | Complexity | Architectural risk |
|---|---|---|---|
| **P1** | #1 — Thesis Red Team Agent | Medium | Low — extends existing Pre-Mortem + exit_advisor patterns |
| **P2** | #2 — Multi-Agent Debate Architecture | High | Medium-high — async/cron design question is unresolved |
| **P3** | #3 — Structural Vulnerability Scanner | Medium | Low — extends existing correlation/risk-pair infrastructure |
| **P4** | #4 — Information Asymmetry Detector | Low-Medium | Low — multi-source stack already live |
| **P5** | #5 — Regime-Aware Adversarial Stress Testing | High | Medium — custom scenario generation is novel |
| **P6** | #6 — Autonomous Pattern Discovery | Very High | High — unsupervised discovery, statistical validity risk |

---

## Design constraints that apply to ALL ideas

These carry over from the existing AI Intelligence Layer principles (`docs/plans/ai-intelligence-layer.md`) and CLAUDE.md:

1. **Strictly additive** — no agentic idea changes a gate, threshold, or recommendation score. Every output is awareness/advisory only.
2. **Graceful degradation** — if the LLM call fails or the API is unavailable, the surface goes dark silently. No hard dependency on any of these features.
3. **Day-cached at minimum** — no per-user-interaction LLM calls without a cache layer. All expensive calls cache to Supabase.
4. **Never fabricates data** — if context is insufficient to form a bear case or vulnerability finding, the agent says "insufficient data" rather than hallucinating one.
5. **User is author of record** — agent output is a starting point for human judgment, not a final verdict. Surfaced as "consider this" not "therefore do this."
6. **Opus review required before ship** — any of these that touch recommendation logic (even additively) requires an Opus review pre-commit.

---

## Idea #1 — Thesis Red Team Agent (P1)

### What it is

A persistent LLM agent that runs adversarially against every held position's bull thesis, producing a continuous "thesis erosion score" and surfacing the specific counter-evidence it found.

**Key differentiation from Pre-Mortem (F-187):**

| Pre-Mortem (existing, F-187) | Thesis Red Team Agent (new) |
|---|---|
| User-authored bear case | LLM-generated counter-evidence |
| One-time at buy | Continuous / refreshed daily |
| What you *think* could go wrong | What the data *currently shows* going wrong |
| Stored as text in the thesis | Stored as structured erosion signal |
| Requires human insight | Requires no human trigger |

### What it produces

- **Thesis Erosion Score** (0–100): how much the original buy thesis is still supported by current data. Derived from existing signals (composite trend, momentum, fundamentals delta, analyst revision direction) + LLM qualitative interpretation.
- **Counter-Evidence Summary**: 2–3 bullet points of the strongest current bear case against the position.
- **Thesis Distance to Failure**: "At current trajectory, thesis breaks in ~N trading sessions" — directional estimate, not a prediction.

### Where it surfaces

- Exit Advisor card for WATCH/TRIM positions: adds a "Red Team" expander showing counter-evidence alongside the existing deterioration signal.
- Daily Brief: new "Thesis Under Pressure" section when erosion score deteriorates sharply in 24h.
- AI Insights: dedicated tab showing all held positions ranked by erosion score.

### Implementation sketch

- **Input**: per-ticker composite score history + momentum signals + LLM call (Haiku) with the stored thesis text and current signals as context.
- **Trigger**: daily cron run OR on-page-load with day-cache check (same pattern as LLM sentiment rescore).
- **Storage**: new `thesis_erosion_cache` table (ticker, erosion_score, counter_evidence jsonb, computed_at).
- **Cost control**: only run on held positions with a stored thesis. On-demand for watchlist items (user-triggered button).

### Open design questions (resolve before build)

1. Does "erosion score" derive purely from existing quantitative signals (no LLM cost), with LLM only generating the counter-evidence text? **Recommendation: yes** — this makes the score auditable and keeps LLM cost bounded.
2. Does the agent read the user's Pre-Mortem text as context? This would close a powerful loop: "You said X would invalidate your thesis — here's evidence that X is now happening."
3. What happens when there's no stored thesis? Degrade gracefully — show signals only, no LLM call.

---

## Idea #2 — Multi-Agent Debate Architecture (P2)

### What it is

For high-stakes decisions (new buy candidate, hold vs. trim decision), spawn two adversarial LLM agents — a Bull Agent and a Bear Agent — that exchange evidence moves (3–5 rounds) and produce a structured debate outcome the user adjudicates.

### What it produces

- **Debate transcript**: Bull opens → Bear responds → Bull rebuts → Bear closes. 4 rounds, each ~2-3 sentences.
- **Agreement/Disagreement verdict**: if Bull and Bear converge → high-confidence signal. If they diverge → surface the specific disputed claim for human judgment.
- **Confidence band**: a range rather than a point estimate. "Bull case: composite reaches 78 in 90 days. Bear case: composite falls to 52 and triggers WATCH."

### Where it surfaces

- Grow Today (entry candidates): a "Run Debate" button per candidate. On click, triggers async debate and shows spinner/progress.
- Exit Advisor (TRIM/EXIT): a "Challenge This Exit" button that runs a Bull agent defending the hold vs. the Bear agent (existing exit signals) — gives the user a structured reason-to-hold before acting.
- AI Insights: stored debate log for past decisions.

### Architectural challenge — THIS IS THE DESIGN QUESTION TO RESOLVE FIRST

Streamlit is synchronous and request-driven. A 4-round multi-LLM-call debate is:
- **High latency**: 4 Haiku calls × ~2s each = ~8–12s blocking. Feasible with a spinner.
- **Cost-intensive**: 4–6 LLM calls per debate. With 5 candidates on Grow Today → 20–30 calls on a single trigger. Need a cost ceiling.
- **Stateful**: the Bull-Bear exchange requires conversation state across turns. Manageable within a single request scope if batched.

**Two viable architectures:**

| Option A — On-demand (blocking) | Option B — Cron-prefetched |
|---|---|
| User clicks "Run Debate" → spinner → result rendered | Nightly cron runs debates for all active candidates → user sees stored result |
| Always fresh | May be stale by morning if price moved |
| ~8–12s UX latency | Near-instant read |
| Simpler implementation | Requires cron + storage + staleness handling |

**Recommendation: Option A with a per-session cost ceiling.** Cap at 1 debate per session per ticker, day-cache result. If cached result < 24h old, show it without re-running.

### Open design questions (resolve before build)

1. Haiku vs. Sonnet for debate agents? Haiku is cost-cheap but less capable of structured multi-step reasoning. Sonnet gives better debate quality but 10x cost.
2. Should the two agents have access to different information (information asymmetry test) or the same corpus?
3. How does the debate result integrate with the existing composite score? **It must not modify the score** — the debate is awareness only. The score is the score.
4. Is the "Run Debate" button always visible or only when conviction is ambiguous (e.g., composite between 55–75 where the call is genuinely uncertain)?

---

## Idea #3 — Structural Vulnerability Scanner (P3)

### What it is

An agent that scans the portfolio for non-obvious structural weaknesses — hidden factor clustering, blast-radius chains, and "open port" exposures — and surfaces the specific exploit chain.

### What it produces

- **Hidden Cluster Report**: positions that appear diversified (different sectors) but share a hidden factor (duration, China supply chain exposure, enterprise IT spend).
- **Blast Radius Map**: "If X drops 20%, the cascade through correlated positions estimates N% portfolio drawdown."
- **Weakest Link Ranking**: positions ranked by how much their deterioration would damage overall portfolio health.

### Where it surfaces

- Risk Analysis page: new "Structural Scan" tab alongside existing correlation matrix and risk pairs.
- Home: "Structural alert" banner when a new vulnerability cluster is detected.

### Why P3 (not P2)

Lower architectural risk than Idea #2. Builds directly on existing `_corr_df_cache`, `_risk_pairs_cache`, and `_avg_corr_cache` infrastructure. The novel piece is the LLM-generated narrative explanation of *why* a cluster is structurally dangerous — the quantitative detection already exists.

---

## Idea #4 — Information Asymmetry Detector (P4)

### What it is

Monitors cross-source data divergence (Finnhub vs. yfinance vs. FMP) as a first-class signal. When three sources agree on a metric, confidence is high. When they diverge, that divergence is surfaced as a "look closer" alert.

### What it produces

- **Divergence score** per ticker: low/medium/high based on spread across sources.
- **Alert when divergence widens**: "Three sources now disagree on NVDA's forward PE — this widened in the last 48h."
- **Source-level breakdown**: which source is the outlier and by how much.

### Why it matters

The current stack uses failover logic (Finnhub → yfinance → FMP) — it picks the first available value. Divergence between sources is currently invisible. But disagreement between data providers is sometimes the most actionable signal (one has stale data, one has updated data, or they model the metric differently).

### Implementation sketch

Very low LLM dependency — this is primarily a quantitative comparison layer on top of the existing multi-source stack. LLM optional for narrative explanation of *why* sources might diverge.

---

## Idea #5 — Regime-Aware Adversarial Stress Testing (P5)

### What it is

Rather than generic stress tests ("what if 2008?"), this agent builds a custom worst-case macro scenario specifically designed to damage the user's *current* portfolio composition — then monitors for early indicators of that scenario.

### What it produces

- **Custom adversarial scenario**: "Your portfolio is most vulnerable to: rate spike + tech multiple compression + USD strengthening simultaneously."
- **Scenario plausibility score**: how likely is this custom scenario in the next 90 days given current macro regime signals?
- **Early indicator watchlist**: the 2–3 leading indicators that would signal this scenario is developing.

### Why P5

Depends on Idea #3 (structural vulnerability detection) to know what to stress-test. Also requires the macro regime layer (already built) to assess scenario plausibility. Natural sequencing: P3 finds the structure, P5 stress-tests it.

---

## Idea #6 — Autonomous Pattern Discovery (P6)

### What it is

An agent that discovers new behavioral patterns in the user's trade + outcome history — specifically patterns the user has *not* recognized — by operating adversarially: finding the pattern most correlated with underperformance.

### What it produces

- **Discovered pattern**: "You underperform when you buy semiconductor names with >3 consecutive green days before entry — 4 occurrences, 1 win."
- **Blind-spot score**: how surprising this pattern is relative to your stated investing beliefs.
- **Anti-pattern recommendation**: the inverse condition that has correlated with better outcomes.

### Why P6 (last)

Requires substantial historical trade + outcome data to avoid statistical noise (minimum ~30 completed trades per pattern). Behavioral Fingerprint (F-193) already handles the known patterns. This is the unsupervised-discovery extension — higher statistical validity risk. Build after the simpler ideas have generated more outcome data.

---

## What to build first — session decision record

**Chosen priority (2026-07-23):** P1 → P2 → P3 → P4 → P5 → P6

Each idea requires:
1. A dedicated plan document (linked from here once written)
2. Opus design review of the plan
3. Implementation (Sonnet implementer)
4. Opus pre-ship review (any LLM/scoring surface)
5. Docs sync (requirements.md + architecture.md + this roadmap's status)

| Idea | Plan doc | Status |
|---|---|---|
| #1 Thesis Red Team Agent | [docs/plans/thesis-red-team-agent.md](thesis-red-team-agent.md) | Planning — Opus review pending |
| #2 Multi-Agent Debate | `docs/plans/multi-agent-debate.md` | Not yet written |
| #3 Structural Vulnerability Scanner | `docs/plans/structural-vulnerability-scanner.md` | Not yet written |
| #4 Information Asymmetry Detector | `docs/plans/information-asymmetry-detector.md` | Not yet written |
| #5 Regime-Aware Stress Testing | `docs/plans/adversarial-stress-testing.md` | Not yet written |
| #6 Autonomous Pattern Discovery | `docs/plans/autonomous-pattern-discovery.md` | Not yet written |
