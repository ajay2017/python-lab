# Multi-Agent Debate Architecture — Design Plan

**Date:** 2026-07-23
**Author:** Ajay Kumar
**Analysis model:** Claude Sonnet 4.6
**Status:** PLAN — pending Opus review before build

> **One-line spec:** For high-stakes investment decisions (new entry, hold vs. trim),
> a Bull Agent and a Bear Agent run a structured 4-round debate on the same evidence
> corpus, with a Judge producing a structured verdict — giving the user a steel-manned
> case on both sides before they act.

---

## Differentiation from existing AI surfaces

This is the critical clarity question — what does a debate add that the composite score
and Red Team erosion score don't already provide?

| Existing surface | What it does | Gap it leaves |
|---|---|---|
| Composite score (5 gates + scoring) | Quantitative ranking of entry quality | Single-point verdict; no steelman of the counter-case |
| Pre-Mortem (F-187) | User writes the bear case at buy | Relies on user's own imagination; static, one-time |
| Thesis Red Team (F-196, Phase 2) | Haiku reads data and writes the current bear case | One-sided; no rebuttal from Bull side |
| **Multi-Agent Debate (new)** | Bull and Bear challenge each other's claims across 4 rounds | Structured adversarial dialectic; surfaces the *contested* claim |

**The key value add:** the debate identifies the specific claim where Bull and Bear
disagree — the "key dispute" — which is the single most useful signal for a human
adjudicator to investigate further. The transcript is secondary; the key dispute is primary.

---

## Design principles (non-negotiable)

1. **Strictly additive.** Debate output never modifies composite score, erosion score,
   gate decisions, or any recommendation. Display-only, forever.
2. **User-triggered only.** No debate runs without explicit user intent (button click).
   No cron, no background compute, no auto-run on page load.
3. **Day-cached, ET-keyed.** One debate run per `(ticker, debate_type, score_date)`.
   If cache hit < 24 hours old: show stored result, button disabled. Prevents runaway cost.
4. **Graceful degradation.** If any Haiku call fails mid-debate: log the failure,
   show the rounds that completed, mark result as partial. Never crash other surfaces.
5. **Never fabricates.** Prompts instruct: cite only from the supplied evidence corpus.
   Generic or invented arguments are a failure. Judge prompt validates grounding before issuing verdict.
6. **Per-session ceiling.** Max 3 new debates per session (button disables after 3 runs).
   Cache hits do not count toward the ceiling.
7. **Opus review required** before build (this plan) and before ship (code review).

---

## Debate protocol

### Agents

| Agent | Role | Model | System prompt stance |
|---|---|---|---|
| **Bull** | Advocate for the position | `claude-haiku-4-5-20251001` | "Construct the strongest specific case FOR based on the evidence. Cite evidence directly. 2–3 sentences. Never hedge. Do not fabricate." |
| **Bear** | Advocate against the position | `claude-haiku-4-5-20251001` | "Construct the strongest specific case AGAINST based on the evidence. Cite evidence directly. 2–3 sentences. Never fabricate." |
| **Judge** | Structural verdict | `claude-haiku-4-5-20251001` | "Assess the debate. Identify the one contested claim where Bull and Bear most disagree. Score each side 0–100. Output JSON only." |

### Round structure

```
Round 1: Bull opens     → "Here is why this is a strong entry/hold."
Round 2: Bear responds  → "Here is the strongest counter-evidence to Bull's claim."
Round 3: Bull rebuts    → "Bear's argument fails because X (cite evidence)."
Round 4: Bear closes    → "Despite Bull's rebuttal, the outstanding concern is Y."
Round 5: Judge reads all 4 rounds, issues verdict JSON.
```

**Total: 5 sequential Haiku calls per debate.** Rounds 2–4 each receive all prior
rounds as conversation context (multi-turn within a single request chain). The Judge
receives the full transcript as a single read-only prompt.

### Judge output schema

```json
{
  "verdict": "bull_wins" | "bear_wins" | "contested",
  "key_dispute": "the specific claim Bull and Bear most disagree on (1 sentence or null if converged)",
  "bull_case_score": 0-100,
  "bear_case_score": 0-100,
  "grounded": true | false
}
```

- `grounded`: the Judge's assessment of whether both agents cited specific evidence
  (vs. generic claims). If `false`, the debate is marked "Low-quality debate — agents
  failed to ground arguments in evidence" and is stored but flagged.
- `verdict = "bull_wins"` when `bull_case_score - bear_case_score >= 20`
- `verdict = "bear_wins"` when `bear_case_score - bull_case_score >= 20`
- Otherwise `"contested"` (the most common and most useful outcome)

---

## Information corpus (same for both agents)

Both agents receive the identical evidence set. Information asymmetry (where agents
intentionally receive different data) is a Phase 2 idea — too complex for Phase 1 and
risks hallucination if one agent fabricates "private" information.

**For entry debate (Grow Today candidate):**
```
ticker, current_price (from grow_bundle["history"]["Close"].iloc[-1]),
composite_score (from grow_candidate_row),
pillar_scores: t_score, bq_score, val_score, s_score (from grow_bundle),
sector (from grow_candidate_row if present, else from grow_bundle["info"].get("sector")),
momentum_5d_pct (computed: grow_bundle["history"]["Close"].pct_change(5).iloc[-1] × 100),
momentum_20d_pct (computed: grow_bundle["history"]["Close"].pct_change(20).iloc[-1] × 100),
rs_vs_spy_20d_pp (computed via compute_relative_strength() from exit_advisor, imported;
                  ticker close from grow_bundle["history"]["Close"], SPY from spy_close_series),
verdict_label (from grow_candidate_row if present, else derived from composite_score vs COMPOSITE_BUY)
```
All fields wrapped in try/except individually — missing/erroring fields excluded silently.
`grow_bundle["history"]` is a price DataFrame from `load_all()` — always present when
the candidate appeared in Grow Today (load_all succeeded).

**For exit debate (TRIM/EXIT card):**
```
ticker, current_price, entry_price, days_held,
composite_score_today, composite_delta_5session,
deterioration_tier (TRIM or EXIT),
rs_vs_spy_20d_pp,
erosion_score (from thesis_erosion_cache, if available),
user_thesis (from trades, if non-empty),
premortem_commitment (from trades, if non-empty),
stop_price (if set — from manual_stops)
```

**Corpus size discipline:** all numeric values as rounded 1-decimal strings. No raw
DataFrames passed. The prompt builder assembles a structured text block, not free-form
prose. This keeps tokens bounded and prevents prompt injection from data values.

---

## Where it surfaces

### Surface 1 — Grow Today: entry candidate debate

**Location:** `app.py`, inside the Home page's Grow Today section — specifically inside
the per-candidate loop at `app.py:5648` (`for _gp in new_picks:`). Grow Today renders
via `_render_grow_today()` called at `app.py:6018`, which is inside `if page == "🏠 Home":`.
There is no standalone `elif page == "🌱 Grow Today":` block.

**Session-state available at this render point:** `_grow_composites` (dict keyed by ticker
→ raw `load_all()` bundle with `t_score/bq_score/val_score/s_score/history/info/headlines`),
`_cached_spy("6mo")` (SPY price history function), `_last_held_data`, `_port_df_enriched`.

**Trigger:** `st.button("⚔️ Debate", key=f"debate_entry_{ticker}")` — visible on every
candidate. On click:
1. Check `debate_cache` for `(ticker, 'entry', today_et())` → if hit, skip compute.
2. Check per-session counter `_debate_runs_this_session` → if >= 3, show
   "Session debate limit reached (3/3)" and disable.
3. If run allowed: `st.spinner("Running debate — 5 Haiku calls, ~15 seconds…")`,
   call `run_debate(corpus, 'entry', api_key)`, save to `debate_cache`, `st.rerun()`.
4. On rerun: `_home_synth_cache` is not None (synthesis already ran), so Grow Today
   re-renders from the synth cache — debate result is then read from `debate_cache` and
   displayed without re-running. No synth-cache interaction issue.

**What renders after debate:**
- Verdict chip: `🟢 Bull wins`, `🔴 Bear wins`, or `⚖️ Contested`
- Key dispute (the single most actionable line)
- Confidence band: `Bull: 72 / Bear: 48`
- Expander "Full transcript" with all 4 rounds labeled by agent/round
- If `grounded: false`: amber note "One or both agents relied on generic arguments — low confidence in this debate."

**Non-goal:** debate result does NOT modify the candidate's ranking in Grow Today.
The composite score is sole ranker. Debate is user-side deliberation aid.

### Surface 2 — Exit Advisor: challenge an exit signal

**Location:** `app.py`, inside TRIM and EXIT deterioration cards only (not WATCH —
debates are for high-conviction decisions where the cost of being wrong is high).

**Trigger:** `st.button("⚔️ Challenge This Exit", key=f"debate_exit_{ticker}")` —
inside the existing expander for TRIM/EXIT cards.

**What renders after debate:**
- Same structure as Surface 1, but debate type = `'exit'` and corpus uses exit context.
- Framing note at top: "This debate challenges the exit signal — not the composite score.
  The exit signal stands; this is a second opinion before you act."
- If `user_thesis` non-empty: the Bull agent has access to the original buy thesis text
  as its first-round anchor. Bear must argue against both the thesis AND the data.

**Non-goal:** debate result does NOT suppress the TRIM/EXIT card or change the signal.
The deterioration tier stands.

### What is NOT surfaced

- Daily Brief: debate transcripts are too long and latency-sensitive for the Brief.
- AI Insights / Positions tab: phase 2 consideration; requires AI Insights to load
  all debate cache rows on tab load.
- Home portfolio overview (stale banner, portfolio table, Daily Brief section): debates
  are deliberation-mode tools. Grow Today is the deliberation section of Home — that is
  the right integration point, not the overview.
- WATCH tier cards: low-conviction; formal debate is overkill. Show when tier elevates to TRIM.

### Phase 2 (Exit Advisor) pre-work note

**Sector-rebalance TRIM cards** carry `ticker=None` with the actual subject in
`action.trim_ticker` (`app.py:6645, 6726`). `build_exit_corpus` and the button key must
key off `trim_ticker` (not `ticker`) for these cards, or the debate will target an empty
ticker. This is a must-resolve item before Phase 2 build begins — not in scope for Phase 1.

---

## Supabase table: `debate_cache`

```sql
CREATE TABLE IF NOT EXISTS debate_cache (
    ticker          text        NOT NULL,
    debate_type     text        NOT NULL,  -- 'entry' | 'exit'
    debate_date     text        NOT NULL,  -- ET ISO date via _today_et()
    verdict         text,                  -- 'bull_wins' | 'bear_wins' | 'contested'
    key_dispute     text,
    bull_case_score numeric,
    bear_case_score numeric,
    grounded        boolean,
    transcript      jsonb       NOT NULL,  -- [{round, agent, text}, ...]
    corpus_snapshot jsonb       NOT NULL,  -- the evidence dict used (for auditability)
    created_at      timestamptz DEFAULT now(),
    PRIMARY KEY (ticker, debate_type, debate_date)
);
ALTER TABLE debate_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS debate_cache_service_role ON public.debate_cache;
CREATE POLICY debate_cache_service_role ON public.debate_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**DDL delivery:** manually applied once in Supabase SQL editor (same pattern as every
other table in this app — there is no `ensure_schema()` or programmatic DDL execution).
Until the table exists, `load_debate_cache` returns `None` and `save_debate_cache`
no-ops silently — same degradation pattern as `load_thesis_erosion_cache` (`db.py:2173`).

**db.py functions:**
- `load_debate_cache(ticker: str, debate_type: str, debate_date: str) → dict | None`
- `save_debate_cache(ticker, debate_type, debate_date, verdict, key_dispute, bull_case_score, bear_case_score, grounded, transcript, corpus_snapshot) → None` (upsert, best-effort)

---

## Module structure

### New file: `stock_analyzer/debate_agent.py`

Pure logic, no Streamlit imports. Exports:

```python
build_entry_corpus(
    ticker: str,
    grow_candidate_row: dict,    # the _gp dict from the candidate loop (ticker, composite_score, sector, conviction, etc.)
    grow_bundle: dict,           # _grow_composites[ticker] raw load_all() bundle (t_score, bq_score, val_score, s_score, history, info, headlines)
    spy_close_series: "pd.Series",  # _cached_spy("6mo")["Close"] — passed from app.py
) -> dict
# Computes momentum and RS from grow_bundle["history"]["Close"] and spy_close_series.
# Returns a flat dict of str-keyed str/float/int values. Never raises (try/except all).

build_exit_corpus(
    ticker: str,
    port_df_row: dict,           # row from _port_df_enriched for this ticker
    held_data_bundle: dict,      # _last_held_data[ticker] bundle
    erosion_cache_row: dict | None,   # load_thesis_erosion_cache result (optional)
    trade_row: dict | None,      # most-recent BUY row from db.load_trades() for ticker (optional)
) -> dict
# user_thesis and premortem_commitment truncated to 600 chars each before inclusion.
# Never raises.

run_debate(
    corpus: dict,
    debate_type: str,            # 'entry' | 'exit'
    api_key: str,                # st.secrets["anthropic"]["api_key"] passed from app.py
    model: str = "claude-haiku-4-5-20251001",
) -> dict
# If not api_key: returns {"transcript": [], "verdict": None, "partial": True, "error": "no_api_key"}
# Runs 5 sequential Haiku calls (4 debate rounds + 1 judge). Returns:
# {"transcript": [...], "verdict": str | None, "key_dispute": str | None,
#  "bull_case_score": int | None, "bear_case_score": int | None,
#  "grounded": bool | None, "partial": bool}
# "partial" = True if any call failed mid-debate (completed rounds still returned).

DEBATE_ROUND_PROMPTS: list[tuple[str, str]]
# Module-level: system+user prompt templates for rounds 1–4 and judge.
```

**No constants.py additions required for this feature.** The per-session ceiling (3)
and the `DEBATE_WIN_MARGIN = 20` (bull/bear score difference that declares a winner)
are behavioral UI limits — no gate fires, no recommendation changes, no composite
score is modified at any margin value. They live as module-level constants in
`debate_agent.py`. **Conscious decision: kept in-module because these are display
classifiers, not investment-policy thresholds.** The precedent is `exit_advisor.py`'s
`_POSTURE_LABELS` and severity maps.

**Exception:** if a future iteration ties the verdict to a gate (e.g., "debate says bear_wins
→ suppress candidate"), escalate to `constants.py` immediately and require Opus review.

### `db.py` additions

DDL + `load_debate_cache()` + `save_debate_cache()`.

### `app.py` wiring — two integration points

1. **Grow Today section** — inside `_render_grow_today()` (called at `app.py:6018`
   from within `if page == "🏠 Home":`), specifically inside the per-candidate loop
   `for _gp in new_picks:` at `app.py:5648`. Add button + spinner + render after the
   existing score breakdown. Guard: only show button when `ticker in _grow_composites`
   and `_gp.get("composite_score") is not None` — hide/disable otherwise to avoid
   running a debate against an empty corpus (which would count against the session ceiling).

2. **Exit Advisor TRIM/EXIT cards** — inside the existing deterioration card expanders
   (Phase 2 only; see §Build phases).

**Session counter:** `st.session_state.setdefault("_debate_runs_this_session", 0)`
Increment on each new run. Check before running. Cache hits do not increment.

---

## Streamlit execution model — latency analysis

5 sequential Haiku calls:
- Each call: ~1–3s depending on token count (short outputs, 150 max_tokens each)
- Total wall time: ~8–15s
- Acceptable with `st.spinner()`. Streamlit blocks the UI during the spinner — no risk
  of partial render or state corruption.

**Why not parallelize rounds 1–4?** Each round requires the prior round's output as
conversation context. Rounds cannot be parallelized by construction.

**Why not cron-prefetch?** User-triggered debates are inherently reactive (user decides
when to debate). Prefetching all candidates nightly would run debates the user never
views (wasted cost). Day-cache achieves the cost goal without prefetching.

---

## Cost model

| Item | Per debate | Per session (3 debates) | Per month (20 sessions/mo) |
|---|---|---|---|
| 5 Haiku calls × ~200 input tokens + 150 output | ~$0.002 | ~$0.006 | ~$0.12 |
| Day-cache hits | $0 | — | — |

At these rates, the per-session ceiling of 3 debates is conservative. If the feature
proves high-value, the ceiling can be raised in `debate_agent.py` without Opus review
(it is a UX limit, not a policy threshold).

---

## Build phases

| Phase | Scope | Gate |
|---|---|---|
| **Phase 1** | `debate_agent.py` + `db.py` DDL/functions + Grow Today surface only | Opus plan review → implement → Opus code review → ship |
| **Phase 2** | Exit Advisor surface (TRIM/EXIT cards) | Phase 1 stable in production for ≥ 3 days |
| **Phase 3** | AI Insights tab showing all stored debates chronologically | Phase 2 stable; enough debate history to be worth showing |

**Rationale for phased delivery:** Grow Today is the higher-value surface (entry decisions
have more asymmetric upside than single exit debates). Phase 1 alone delivers the core
adversarial debate capability. Exit Advisor integration is a follow-on that can be
assessed after real-world debate quality is confirmed.

---

## What NOT to build in this plan

- **Composite score modification.** Debate output is display-only, forever.
- **Cron / background prefetch.** User-triggered only.
- **Information asymmetry between agents.** Phase 2 idea; out of scope.
- **Debate for WATCH tier.** Too low-conviction for formal debate.
- **Daily Brief integration.** Transcripts are too long and latency-sensitive.
- **Watchlist / candidates not in Grow Today.** Scope is Grow Today candidates only.
- **Historical debate chart.** Data will accumulate; charting is Phase 3+.
- **Debate as a gate.** Permanently excluded.

---

## Open design questions for Opus review

1. **Corpus completeness vs. prompt size:** the exit corpus includes `user_thesis` and
   `premortem_commitment` which could be multi-paragraph. Should these be truncated to
   N characters in the prompt to bound token count? If so, what N?

2. **Per-session ceiling value (3):** is this the right UX balance, or should it be
   lower/higher? This is a cost + UX judgment, not a policy threshold.

3. **Judge grounding check:** is a boolean `grounded` field sufficient, or should the
   judge return a per-agent grounding score? A per-agent score would let us show
   "Bull grounded: ✅ Bear grounded: ❌" — more actionable, slightly more complex.

4. **Partial debate rendering:** if Round 3 Haiku call fails, should we show Rounds 1–2
   with a "debate interrupted" note, or suppress the entire result and show only an error?
   Showing partial is more transparent; suppressing is cleaner.

5. **`debate_type` scope:** should Grow Today's debate be typed `'entry'` and Exit Advisor
   `'exit'`, or should we include the ticker's deterioration tier in the key
   (e.g., `'exit_trim'` vs. `'exit_exit'`)? The simpler 2-type scheme is proposed here.

---

## Review required before build

This plan requires an Opus review before any implementation begins. The reviewer should
assess:

- [ ] 5 Haiku calls per debate — is sequential round-based context accumulation the
      right pattern, or is there a more cost-efficient multi-turn structure?
- [ ] Corpus design — any critical signal missing from entry or exit corpus?
- [ ] Judge output schema — is `grounded: boolean` sufficient for quality control?
- [ ] `debate_cache` DDL — any schema issue?
- [ ] `debate_agent.py` module boundary — correct separation of pure logic vs. app.py?
- [ ] Per-session ceiling implementation via `st.session_state` — any state collision risk
      with existing session keys?
- [ ] Phase 1 scope (Grow Today only) — is this the right starting surface, or should
      Exit Advisor be Phase 1?

---

## Review log

| Round | Model | Verdict | Blocking findings |
|---|---|---|---|
| Round 1 | Claude Opus 4.8 | FIX-FIRST | 5 blocking (DDL delivery, RLS missing, api_key not passed, Grow Today surface anchor wrong, entry corpus fields unavailable) — all resolved in v2 |
| Round 2 | Claude Opus 4.8 | FIX-FIRST | 1 blocking (stale wrong wiring anchor in module structure section); 4 non-blocking (win-margin in-module rationale, sector fallback, guard button when bundle missing, CREATE POLICY idempotency) — all addressed in v3 |
| Round 3 | Claude Opus 4.8 | SHIP | 0 blocking — all Round 2 fixes verified correct; plan ready to implement |
