# Plan: AI Intelligence Layer — Thesis, Earnings Transcripts, Portfolio Debrief, Intelligence Report

**Status:** F-1 **shipped** 2026-06-27 · F-3 **shipped** 2026-06-27 · F-2 deferred (pending transcript API budget) · F-4 **proposed** 2026-06-27  
**Date:** 2026-06-27  
**Scope:** Four new capabilities (build sequentially in order listed)  
**Philosophy:** LLM narrates and synthesizes; rule-based engine continues to decide and gate. No LLM output issues a buy/sell recommendation — that remains the composite score + gate system.

---

## Why this, why now

The app makes strong forward decisions (score, gate, recommend). What it cannot do is look backward with language: explain *why* the portfolio behaved the way it did, check whether the user's original conviction still holds, or read what management said after a quarter ends. These three capabilities close that loop.

The three opportunities are:

| # | Capability | What closes | Status |
|---|---|---|---|
| F-1 | Thesis Tracking | The gap between "why I bought" and "does that reason still hold" | **Shipped 2026-06-27** |
| F-2 | Earnings Call Intelligence | The gap between knowing *when* earnings is and knowing *what management said* | **Deferred — transcript API budget pending** |
| F-3 | Portfolio Debrief (weekly) | The gap between the app's forward decisions and retrospective pattern recognition | **Shipped 2026-06-27** |
| F-4 | Portfolio Intelligence Report (monthly) | The gap between *one week* of retrospection and the slower questions — is the engine picking well, do I act on what it surfaces, am I repeating a bias | **Proposed 2026-06-27** |

None existed before this layer. F-1 and F-2 are position-level. F-3 and F-4 are portfolio-level (F-3 weekly, F-4 monthly behavioural). All are awareness surfaces — they do not move a gate or issue a recommendation.

---

## Operating constraints (non-negotiable, apply to all three)

1. **LLM narrates; gates decide.** No LLM output changes a composite score, moves a stop, or issues an entry/exit recommendation. If a user's thesis is BROKEN, the app surfaces that fact — the user decides what to do. The rule-based exit ladder still fires independently.

2. **Fail loud.** If an LLM call fails (timeout, API error, no transcript available), the feature surfaces an explicit offline state. No fabricated or cached synthesis passes as fresh.

3. **Staleness labels.** Every LLM-generated output is stamped with when it was generated and what inputs it saw. The user always knows if they're reading a 3-day-old debrief.

4. **No thresholds in LLM output.** The LLM describes; it does not quantify gates. "Management guidance tone softened" is valid. "This stock has a 67% chance of hitting a TRIM signal" is not — that's the engine's domain.

5. **All LLM API calls go through the existing provider abstraction.** Claude (Anthropic) is the default model. Model selection is a config, not hardcoded.

6. **The core app has zero runtime dependency on LLM connectivity.** This is the key architectural trade-off: AI features are strictly additive. If the LLM is offline, rate-limited, or the subscription lapses, every existing page and feature operates exactly as it does today. The AI layer goes dark cleanly; nothing else degrades. This principle governs UI placement (see §UI Placement below) and must be preserved as new AI surfaces are added.

---

## UI placement — AI Insights page

All LLM-generated outputs are consolidated into a single dedicated **AI Insights** nav page. Core pages (Holdings, Evening Debrief, Catalyst Watch, etc.) carry no embedded AI output — they function completely without LLM connectivity.

```
Existing nav (unchanged, zero dependency on LLM):
  Home · Grow Today · Watchlist · Holdings · Risk Advisor
  Catalyst Watch · Rebalancer · Tax · Evening Debrief · ...

New nav page:
  ── AI Insights ──────────────────────────────────
  │  Thesis Reviews      (F-1 output)             │
  │  Weekly Debrief      (F-3 output)             │
  │  Earnings Transcripts (F-2, when built)       │
  ─────────────────────────────────────────────────
```

**Boundary rules:**

| Concern | Rule |
|---|---|
| Thesis capture (text field at BUY entry) | Stays in Trade Journal — it is plain text storage, no LLM involved. Always works. |
| Thesis review output (INTACT / WEAKENING / BROKEN) | Lives on AI Insights only. No chip or status embedded on the Holdings page row. |
| Combined elevated card (BROKEN thesis + engine TRIM/EXIT) | Engine TRIM/EXIT surfaces on Holdings as normal (no change). AI Insights shows the BROKEN thesis context. A lightweight "AI note →" link on the Holdings TRIM card may point to AI Insights, but the TRIM card is complete and actionable without it. |
| Evening Debrief WEAKENING/BROKEN callout (originally in design) | Moved to AI Insights. Evening Debrief page is unaffected by LLM status. |
| Weekly debrief email | If LLM offline that Sunday, the email is skipped. Retries next week. No impact on app. |

**Degradation behaviour:**

| Surface | LLM offline |
|---|---|
| All existing pages | No change — zero awareness of LLM state |
| AI Insights page | Banner: "AI layer offline — showing last generated [date]" or "Not yet generated" |
| Thesis capture at trade entry | Works — just stores text, no LLM call |
| Weekly debrief email | Skipped that week; retries next Sunday |

---

## F-1 · Thesis Tracking

### Problem

When a user buys a position, they have a reason — a conviction about the company's future. That reason either stays true or it doesn't. Today, when the app is deciding whether to recommend HOLD vs. TRIM, it has only the current technical/fundamental score. It has no memory of *why the user entered* and no way to check whether that original conviction has been contradicted by events since.

The result: users hold through drawdowns because they emotionally remember their conviction, but have no structured way to ask "is my thesis still valid?" The app scores the stock but cannot engage with the investment thesis.

### What exists today

- `recommendations.thesis` — auto-generated technical description ("price above 50d MA, momentum +12%"). This is the engine's technical rationale, not the user's investment conviction.
- `trades.notes` — free-text optional field at trade entry. Unstructured, never revisited, never prompted.

### Design

#### 1.1 Thesis capture (at entry)

Add a **Thesis** field to the Trade Journal BUY entry form. Positioned after the core trade fields (ticker, shares, price, date), before Submit.

- **Label:** "Investment thesis (optional but recommended)"
- **Hint text:** "Why do you believe in this position? What would need to change for you to reconsider?"
- **Input type:** Multi-line text, 500 char max
- **Storage:** New column `user_thesis` in `trades` table (nullable; backfills NULL for existing rows — backward-compatible per convention)
- **Behaviour on add-to-winner:** Pre-populate with the existing thesis for that ticker so the user can update rather than re-write from scratch

The second question ("what would need to change") is the most important part. It makes the thesis falsifiable — which is what the review step needs to evaluate.

#### 1.2 Thesis review (periodic + on-demand)

A background check runs **weekly** (Sunday, before market open) for all open positions that have a non-null `user_thesis`.

**Inputs per position:**
- `user_thesis` (user's written conviction)
- Last 30 days of news headlines (from existing news pipeline)
- Current fundamental scores (revenue growth, margin trend — from existing FMP/yfinance data)
- Technical trend summary: is the price above/below SMA50, momentum direction, RSI zone
- Last earnings result if available (beat/miss, guidance direction — from existing `earnings_advisor.py` data)

**LLM task:** Given the original thesis and current evidence, classify the thesis status and explain briefly why.

**Output (three states):**

| Status | Meaning | Display |
|---|---|---|
| `INTACT` | Evidence is consistent with the original conviction | Green chip: "Thesis intact" |
| `WEAKENING` | Some evidence contradicts; not yet decisive | Amber chip: "Thesis weakening — review" |
| `BROKEN` | Evidence materially contradicts the conviction or the key condition has reversed | Red chip: "Thesis challenged — re-examine" |

**Important:** BROKEN does not issue an exit signal. The rule-based deterioration ladder fires independently. This is an awareness card that says "your reason for being in this has changed." The user decides.

**Coordination:** If a position already has a TRIM or EXIT signal from the deterioration engine, the thesis card is surfaced *alongside* it (adds colour to the exit signal, does not suppress it or duplicate it).

#### 1.3 Surface

- **Holdings page** — each position row gets a thesis chip (colour-coded) if a thesis exists. Expanding the row shows the full original thesis text and the LLM review paragraph.
- **Evening Debrief** — WEAKENING/BROKEN positions surfaced in the "Positions to Watch" section with a one-line reason.

#### 1.4 Data model

```
trades table — new column:
  user_thesis   TEXT   NULL   -- user's investment conviction at entry time

thesis_reviews table (new):
  id            UUID   PK
  ticker        TEXT   NOT NULL
  trade_date    DATE   NOT NULL   -- links to the originating buy
  reviewed_at   TIMESTAMPTZ NOT NULL
  status        TEXT   NOT NULL   -- INTACT | WEAKENING | BROKEN
  summary       TEXT   NOT NULL   -- LLM paragraph (~100 words)
  inputs_hash   TEXT              -- hash of inputs used (for staleness detection)
```

#### 1.5 Risks and mitigations

| Risk | Mitigation |
|---|---|
| User writes a vague thesis ("liked the chart") | Hint text nudges toward falsifiable language; a thin thesis produces a thin review (explicitly labelled as low-confidence) |
| LLM hallucinates a news event that didn't happen | LLM is fed only the actual headlines from the app's news pipeline — it cannot invent events, only interpret what it's given |
| BROKEN status on a position that later recovers | Thesis review is advisory, not gating. Historical reviews are preserved so the user can see the trajectory |
| API cost on weekly runs | One LLM call per held position per week; typical portfolio = 10–15 positions = 10–15 calls/week. Negligible at Claude API pricing |

#### 1.6 Open questions (resolve before build)

1. Should thesis be required or optional at BUY entry? (Recommended: optional with a nudge — mandatory thesis blocks quick trade logging)
2. Should the review also run on-demand (user clicks "Re-evaluate thesis" on a position)? (Recommended: yes, single-position on-demand + weekly batch)
3. Should BROKEN positions that also have an active TRIM/EXIT signal from the engine get an elevated visual treatment? (e.g., combined card with both signals)

---

## F-2 · Earnings Call Intelligence

### Problem

Catalyst Watch tells you *when* earnings is and what the analyst consensus estimate is. What it cannot tell you is what management actually said. The earnings call transcript — guidance language, tone shifts, what institutional analysts questioned, whether management hedged more than last quarter — is the information that professional analysts process and retail investors almost never read.

A single-sentence guidance change ("we expect margin pressure to persist longer than previously anticipated") can materially alter the investment thesis for a multi-quarter holding. The app has no access to that signal today.

### What exists today

- `catalyst_watch.py` — knows the earnings date; shows analyst EPS estimate and beat/miss history
- `earnings_advisor.py` — evaluates pre-earnings positioning (analyst revisions, historical beats); does NOT parse transcript content
- No transcript fetching, no call content, zero grep matches for "transcript" across the codebase

### Design

#### 2.1 Transcript sourcing

Transcripts are available from several APIs. Selection criteria: free or low-cost tier adequate for 10–15 held names per quarter; reliable availability within 4–6 hours of call end.

**Primary candidate:** Alpha Vantage Earnings Call Transcripts endpoint (available on paid plan; ~$50/month). Returns structured JSON with speaker turns, Q&A segmentation, and metadata.

**Fallback candidate:** Seeking Alpha (scraping) — unreliable, rate-limited, fragile. Not recommended as a primary.

**Decision needed:** Select and budget the transcript API before build starts. This is the only net-new paid dependency this feature requires. (All other data already flows through FMP/yfinance/Finnhub.)

#### 2.2 Fetch trigger

Transcripts are fetched **post-earnings**, not pre-earnings. The trigger:

- Catalyst Watch already knows the earnings date for held positions
- A background check (runs as part of the existing GitHub Actions cron or a new daily check) detects when `earnings_date` has passed and `transcript_fetched = false` for that position+quarter
- Fetches the transcript; runs LLM analysis; stores result
- Re-fetch if the transcript wasn't available yet (transcripts post 2–8 hours after call end; retry once after 24 hours)

#### 2.3 LLM analysis

**Inputs:** Full transcript text (speaker-segmented; CEO + CFO remarks + analyst Q&A)

**LLM task:** Extract and summarize five specific dimensions. The prompt instructs the LLM to extract only what was said — not to infer stock direction or recommend action.

| Dimension | What to extract |
|---|---|
| Guidance tone | Raised / maintained / lowered / hedged — with the specific language used |
| Demand signals | What management said about demand, pipeline, backlog, customer behaviour |
| Cost and margin language | Any shift in language around expenses, margins, profitability timeline |
| Analyst pressure points | What themes did institutional analysts press on? (Not management answers — the questions themselves) |
| Red flags | Non-answers, CEO/CFO language shifts vs last quarter, sudden hedging, litigation/regulatory mentions |

**Output format (structured JSON → rendered as card):**

```json
{
  "quarter": "Q2 FY2026",
  "call_date": "2026-07-24",
  "guidance_tone": "LOWERED",
  "guidance_quote": "\"We now expect full-year margins in the 18–20% range, down from our prior 22–24% guidance.\"",
  "demand_summary": "Management cited softening enterprise demand in EMEA; domestic pipeline described as 'healthy but elongated sales cycles'.",
  "analyst_themes": ["margin compression timeline", "EMEA exposure", "competitive pricing pressure from new entrant"],
  "red_flags": ["CFO used 'prudent' 7× vs 2× in prior call", "No specific Q3 guidance issued; prior calls gave quarterly guidance"],
  "overall_tone": "CAUTIOUS",
  "generated_at": "2026-07-24T22:14:00Z"
}
```

#### 2.4 Thesis linkage

If the position has an F-1 user thesis, the LLM analysis also evaluates whether the transcript content supports or contradicts that thesis (one additional paragraph). This is the integration point between F-1 and F-2 — earnings calls are often the first moment a thesis breaks.

#### 2.5 Surface

**Catalyst Watch page — held positions tier:**

- Each held position with a completed transcript analysis shows a "Transcript" card beneath the earnings date row
- Card header: guidance tone chip (RAISED / MAINTAINED / LOWERED / HEDGED) + call date + "generated N hours ago"
- Expanding the card shows all five dimensions
- If thesis integration is available (F-1 is live): shows a "Thesis check" chip (SUPPORTS / NEUTRAL / CONTRADICTS) at the bottom of the card

**No surface on Grow Today or Buy Candidates.** This is awareness for held positions only — not a forward recommendation signal.

#### 2.6 Data model

```
earnings_transcripts table (new):
  id               UUID   PK
  ticker           TEXT   NOT NULL
  quarter          TEXT   NOT NULL   -- e.g. "Q2 FY2026"
  call_date        DATE   NOT NULL
  guidance_tone    TEXT   NOT NULL   -- RAISED | MAINTAINED | LOWERED | HEDGED
  guidance_quote   TEXT
  demand_summary   TEXT
  analyst_themes   JSONB             -- list of strings
  red_flags        JSONB             -- list of strings
  overall_tone     TEXT              -- BULLISH | NEUTRAL | CAUTIOUS | BEARISH
  thesis_check     TEXT   NULL       -- SUPPORTS | NEUTRAL | CONTRADICTS (only if F-1 live)
  thesis_note      TEXT   NULL       -- LLM paragraph on thesis linkage
  source_api       TEXT              -- which transcript API was used
  generated_at     TIMESTAMPTZ NOT NULL
  fetched_at       TIMESTAMPTZ NOT NULL
```

#### 2.7 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Transcript API unavailable / transcript not yet posted | Retry once after 24 hours; surface "Transcript pending" placeholder in Catalyst Watch rather than erroring |
| LLM reads too much into neutral language | Prompt instructs: extract, don't infer; guidance_tone must map to explicit guidance language, not tone impression |
| Stock already moved on the guidance print | Clearly label the card as post-earnings awareness, not a timing signal. The value is medium-term thesis review, not same-night trade |
| API cost | One transcript per held position per quarter = ~40–60 LLM calls/year at current portfolio size. Negligible |

#### 2.8 Open questions (resolve before build)

1. **Which transcript API?** Alpha Vantage paid tier is the recommended choice — confirm budget before build.
2. **Should this apply to watchlist names too, or held-only?** (Recommended: held-only for v1; watchlist is awareness-only and transcript analysis without a position adds complexity)
3. **Should the overall_tone feed into any gate?** For example: BEARISH transcript → suppress "add-to-winner" on that name? (Recommended: no for v1 — keep it awareness-only; revisit after seeing real output quality)

---

## F-3 · Portfolio Debrief (Weekly Retrospective)

### Problem

The Evening Debrief is a daily checklist — it reconciles today's plan vs. today's reality and previews tomorrow. It is forward-looking and mechanical. It cannot answer: "Why did my portfolio perform the way it did this week? What signals did I follow or ignore — and what did following or ignoring them cost? What patterns am I repeating?"

This is the reflection layer the app has never had. Professional investors do this in investment committee. Individual investors almost never do it — not because they don't want to, but because assembling the data manually is prohibitive.

### What exists today

- `recommendations` table — every recommendation surfaced, with `verdict`, `composite_score`, `thesis`, `price_at_surface`, `surfaced_at`
- `trades` table — every trade with date, price, action
- `action_log` table — manual overrides (stops, thesis notes)
- `daily_snapshots` table — daily portfolio value baseline (DDL exists; needs activation in Supabase)
- **Evening Debrief** — daily checklist (plan vs. reality; next-day preview). NOT a retrospective narrative

### Design

#### 3.1 Cadence and trigger

- **Weekly:** Sunday evening, delivered as an email (same Resend API + GitHub Actions cron pattern as existing protective alerts)
- **On-demand:** User can trigger from a new "Weekly Debrief" section in the app at any time; generates for the trailing 7 days

#### 3.2 Inputs

The LLM receives a structured data package — not raw tables. A Python builder assembles the package before the LLM call.

**Package contents:**
- Portfolio value change for the week (start vs. end, from `daily_snapshots`)
- SPY return for the same period (benchmark comparison)
- Per-position P&L for the week (from `daily_snapshots`)
- Top 3 contributors and top 3 detractors (by dollar impact)
- Recommendations surfaced during the week: ticker, verdict, composite score, whether the user traded within 2 trading days (proxy for "acted on signal")
- Signals ignored: recommendations surfaced but not acted on — and what happened to those names over the week
- Gate firings during the week: any TRIM/EXIT/WATCH that fired, and whether the user traded on them
- Macro context: market tone for each day of the week, VIX range, any HIGH-impact macro events

#### 3.3 LLM task

Generate a structured weekly debrief in four sections. Target length: 400–500 words total. Written in second person ("Your portfolio..."), plain language, no jargon.

**Section 1 — What happened (2–3 sentences)**
Performance vs. benchmark. Top contributors and detractors named. No recommendations — just facts.

**Section 2 — Decisions you made (bullet list)**
For each recommendation surfaced: did you act or not, and what was the outcome? Keep it factual. Example: "NVDA: TRIM signal surfaced Monday. You held. Name fell 6.8% by Friday."

**Section 3 — Patterns this week (1–3 bullets)**
What behavioural pattern, if any, showed up? Drawn only from this week's data — no fabricated multi-week trend unless multi-week data is included in the package. Examples:
- "You acted on 4 of 5 BUY signals but 0 of 2 TRIM signals."
- "Both positions you added to this week were already above your single-name ceiling."

**Section 4 — One thing to watch next week**
One forward-looking observation grounded in current data: a thesis that's WEAKENING (F-1), an earnings transcript that flagged a red flag (F-2), a macro event, or a position approaching a gate.

#### 3.4 Behavioural pattern library (prompt guidance, not rigid rules)

The LLM prompt includes a named-pattern library so it uses consistent language across weeks. The patterns are grounded in the app's existing signal taxonomy:

| Pattern name | Condition |
|---|---|
| Signal follower | Acted on ≥80% of Act Today signals this week |
| Selective actor | Acted on BUY signals but not TRIM/EXIT signals (or vice versa) |
| Early exiter | Exited positions that later recovered >10% within 7 days |
| Concentration creep | Added to a position already above single-name or sector ceiling |
| Earnings chaser | Bought within earnings window on ≥2 occasions this week |
| Calm week | No signals fired or signals were acted on; nothing to flag |

#### 3.5 Surface

**Email (primary):** Delivered Sunday evening via Resend API. Same formatting as existing alert emails. Subject: "DRISHTA Weekly Debrief — week of [date]".

**App (secondary):** New section at the bottom of the Evening Debrief page: "Last Weekly Debrief" — shows the most recent generated debrief with a timestamp and a "Generate Now" button for on-demand.

No new nav page required for v1. The Evening Debrief page is the natural home.

#### 3.6 Data model

```
weekly_debriefs table (new):
  id              UUID   PK
  week_ending     DATE   NOT NULL   -- Sunday of the covered week
  generated_at    TIMESTAMPTZ NOT NULL
  performance_pct NUMERIC            -- portfolio % return for the week
  spy_pct         NUMERIC            -- SPY % return for the week
  alpha_pct       NUMERIC            -- performance_pct - spy_pct
  section_facts   TEXT   NOT NULL   -- Section 1 text
  section_decisions TEXT NOT NULL   -- Section 2 text
  section_patterns  TEXT NOT NULL   -- Section 3 text
  section_watchnext TEXT NOT NULL   -- Section 4 text
  email_sent      BOOLEAN DEFAULT FALSE
  email_sent_at   TIMESTAMPTZ NULL
```

#### 3.7 Dependency: daily_snapshots activation

F-3 requires `daily_snapshots` to be live to compute per-week and per-position P&L. This table's DDL exists but needs a one-time activation step in Supabase (documented in the account-baseline plan). F-3 cannot be built until daily_snapshots has at least 7 days of data.

**Sequencing implication:** Activate `daily_snapshots` → let it accumulate ≥7 days → then build F-3.

#### 3.8 Risks and mitigations

| Risk | Mitigation |
|---|---|
| LLM invents a trade or signal that didn't happen | The data package is pre-assembled by a Python builder from real tables; LLM only narrates what the package contains — it cannot add facts |
| Pattern names used inconsistently week to week | Named-pattern library in the prompt ensures consistent labelling |
| Debrief reads as accusatory ("you ignored 3 signals") | Prompt instructs neutral, factual tone — observation not judgement |
| `daily_snapshots` not yet live | F-3 blocked until activated; app shows "Debrief unavailable until daily snapshot data is collected" |
| Sample size too small for patterns (first few weeks) | Section 3 is suppressed until ≥4 weeks of data exist; replaced with "Not enough history yet for pattern analysis" |

#### 3.9 Open questions (resolve before build)

1. Should the debrief email be opt-in or always-on once built? (Recommended: always-on; same pattern as protective alerts)
2. Should F-3 attempt multi-week pattern analysis from week 5 onward (e.g., "for the third consecutive week, you ignored TRIM signals"), or stay week-scoped for v1? (Recommended: week-scoped for v1; accumulate data first)
3. When `daily_snapshots` is not yet live, should the debrief still run but skip the P&L sections? (Recommended: no — without P&L the debrief loses its anchor; better to wait and label clearly)

---

## F-4 · Portfolio Intelligence Report (Monthly Retrospective)

### Problem

F-3 (Weekly Debrief) looks back **one week** at *what happened*. It cannot answer the slower questions that only surface across weeks — the questions an investment committee asks at month/quarter end:

- Is the **entry engine itself** picking well? (Of everything that cleared the gates and surfaced as a high-conviction buy, did it actually beat the market?)
- Do I **act** on the signals it surfaces — and does acting help or hurt?
- Are my **thesis calls** calibrated — did WEAKENING/BROKEN actually precede deterioration?
- Am I repeating a **systematic bias** (trim winners early, hold losers long, chase a sector)?

These require pattern recognition across the accumulated history, not a single week. F-4 is the monthly reflection layer over the data F-1/F-3 and the rule-based scorecard have been accumulating.

### Why "question 0" comes first

Every position in the book traces back to one origin: a ticker cleared **all gates**, scored **Composite ≥ `COMPOSITE_BUY` (65)**, and surfaced under **"📈 High-Conviction Entries Only → 🆕 New Positions to Initiate"** (`app.py`). Everything downstream — whether the user acted, the thesis they wrote, the later hold/trim/exit — is *conditioned on that entry decision*. Judging the user's behaviour without first judging the engine's picks would mislead: perfect discipline on bad picks still loses money.

So the report leads with the engine's own pick quality (**question 0**), then the user's response to it (**question 1**), folding in thesis discipline (**2**) and cross-cutting behavioural patterns (**3**) as the data matures.

| # | Question | Judges | Data maturity needed |
|---|---|---|---|
| **0** | Entry quality | Does the *engine* pick well? (composite band → alpha vs SPY) | Highest — entries must age |
| **1** | Signal discipline | Does the user *act* on surfaced signals, and at what cost/benefit? | Lowest — measurable from month 1 |
| **2** | Thesis discipline | Are WEAKENING/BROKEN calls calibrated against actual outcomes? | Medium |
| **3** | Behavioural patterns | Systematic biases across 0–2 | Highest — needs ≥2 months of reports |

**v1 ships 0 + 1** — the closed loop: engine picks → user action → outcome. 2 and 3 fold in as the history deepens.

### What exists today (rule-based — do NOT rebuild)

The scorecard math already exists and is mature in [`stock_analyzer/recommendations_history.py`](../../stock_analyzer/recommendations_history.py):

- `match_recs_to_trades()` — joins surfaced recs to actual trades (**acted vs. missed**)
- `compute_outcomes()` — `outcome_pct`, `spy_return_pct`, `alpha_pct` (= outcome − SPY, the **regime-adjusted** read); flags recs younger than `REC_SCORE_MIN_DAYS` (5 calendar days) as `outcome_maturing` and excludes them from graded aggregates
- `summary_stats()` — acted-vs-missed rollup with alpha
- `by_verdict()` — action-rate + outcome + alpha by verdict bucket
- `by_composite_band()` — Strong Buy (≥75) / Buy (65–74) / Hold-zone (44–64) / Sell-zone (<44) / Unscored

The first engine-health review (2026-06-18) judged the engine **HEALTHY** on these aggregates (memory `project_rec_engine_evaluation`).

**F-4's job is NOT to recompute this.** A Python builder calls these existing functions; the LLM reads the resulting aggregates plus the matured matched recs and writes the *narrative the numbers can't* — e.g. "your 65–74 composite band underperformed your ≥75 band by X this period; the weakness clustered in [sector]."

### Design

#### 4.1 Cadence and trigger

- **Monthly:** first Sunday of the month, delivered as email (Resend + GitHub Actions cron — same pattern as the F-140s). Runs in the **existing Sunday cron lane** alongside thesis (F-1) and debrief (F-3); gated to fire the report only on the first Sunday of the month.
- **On-demand:** "Generate Monthly Report" button on the AI Insights page, for the trailing ~4 weeks.

#### 4.2 Inputs (Python builder assembles; LLM only narrates)

The builder runs the existing pipeline over the trailing ~4–8 weeks:
`match_recs_to_trades` → `compute_outcomes(min_days=REC_SCORE_MIN_DAYS)` → `summary_stats` / `by_verdict` / `by_composite_band`, plus:

- `weekly_debriefs` rows for the period (already-computed weekly performance + alpha — the monthly view stitches these, it does not re-derive them)
- `thesis_reviews` verdict history (for question 2, once it matures)
- macro/regime context per the existing macro tag

**Only matured recs** (`days_since ≥ REC_SCORE_MIN_DAYS`) feed the graded aggregates — consistent with the on-page scorecard. Younger recs are excluded, never fabricated into a trend.

#### 4.3 LLM task — sections

| Section | Question | Content |
|---|---|---|
| 1 — Entry quality | Q0 | How did the engine's high-conviction picks perform on **alpha vs. SPY**? Which composite band converted and performed best; where weakness clustered (sector/regime). Facts only. |
| 2 — Signal discipline | Q1 | Acted vs. ignored, and what each **cost or saved**. Closes the loop Q0 opens. |
| 3 — Thesis discipline *(when data matures)* | Q2 | Did WEAKENING/BROKEN precede real deterioration, or was it noise? |
| 4 — Pattern + one focus | Q3 | One systematic pattern (named-pattern library, reused from F-3 §3.4) + one thing to focus on next month. |

Target length ~500–700 words. Second person, neutral/factual tone (observation, not judgement) — same voice as F-3.

#### 4.4 Hard boundary — AI surfaces patterns, never tunes gates

This is the non-negotiable line for question 0. The report **MAY** say: *"the 65–74 composite band underperformed the ≥75 band by X this period — you may want to review the entry threshold."* It **MAY NOT** change `COMPOSITE_BUY`, move any gate, or auto-issue a buy/sell. Threshold changes remain an **investment-policy decision** ([CLAUDE.md](../../CLAUDE.md) hard rule #1; [`constants.py`](../../stock_analyzer/constants.py)) made in conversation with the user — never automated. The report is read-only awareness over the engine's *own* behaviour.

#### 4.5 Data-maturity guards

| Question | Guard | If unmet |
|---|---|---|
| Q0 — entry quality | ≥ N matured graded entries (alpha computable) | "Not enough matured entries yet to grade pick quality" |
| Q1 — signal discipline | available from month 1 (acted/missed needs no maturity) | — |
| Q2 — thesis discipline | ≥ M thesis reviews with an outcome window | section suppressed |
| Q3 — patterns | ≥ 2 monthly reports of history | section suppressed |

`N` and `M` are **measurement floors, not gates** (same philosophy as `REC_SCORE_MIN_DAYS`) — safe to tune from observation; they never affect what the engine recommends. Proposed floors: `N ≥ 5`, `M ≥ 3` (confirm at build).

#### 4.6 Data model

```
monthly_reports table (new):
  id                       UUID   PK
  period_start             DATE   NOT NULL
  period_end               DATE   NOT NULL   -- first-Sunday boundary (unique key)
  generated_at             TIMESTAMPTZ NOT NULL
  engine_alpha_pct         NUMERIC NULL      -- avg alpha of matured high-conviction acted entries (Q0 headline)
  acted_count              INTEGER NULL      -- # surfaced recs acted on in period
  missed_count             INTEGER NULL      -- # surfaced recs not acted on
  section_entry_quality    TEXT   NOT NULL   -- Section 1 (Q0)
  section_signal_discipline TEXT  NOT NULL   -- Section 2 (Q1)
  section_thesis           TEXT   NULL       -- Section 3 (Q2; null until matured)
  section_patterns         TEXT   NOT NULL   -- Section 4 (Q3 / pattern + focus)
  email_sent               BOOLEAN DEFAULT FALSE
  email_sent_at            TIMESTAMPTZ NULL
```

Unique on `period_end` → **upsert-safe** (same convention as `weekly_debriefs`). RLS: `FOR ALL TO service_role` policy required (CLAUDE.md hard rule #2). Ships **inert** until the DDL is run in Supabase.

#### 4.7 Surface

- **AI Insights page:** new "Monthly Portfolio Intelligence" section **below** the Weekly Debrief — most recent report + "Generate Monthly Report" button. Same offline/staleness banner pattern as the rest of the page.
- **Email:** first Sunday of the month. Subject: "DRISHTA Monthly Intelligence — [month YYYY]". Reuses the light-mode-first `render_debrief_email` template pattern (the F-3 redesign), extended for the report's section set.

#### 4.8 Risks and mitigations

| Risk | Mitigation |
|---|---|
| LLM invents an outcome or trade | Builder pre-computes every number from `recommendations_history` against real tables; the LLM narrates only the package — it cannot add facts |
| LLM prescribes a threshold value as if actionable | System prompt forbids quantifying or changing gates; "you may want to review" framing only (§4.4). No LLM output touches `constants.py` |
| Small sample in early months | Maturity guards (§4.5) suppress a section rather than fabricate a trend |
| Double-counts the weekly debrief | Monthly reads **matured** outcomes + already-saved weekly alpha; framed as the longer-horizon behavioural view, not a re-summary of the weeks |
| API cost | One LLM call per month (+ on-demand). Negligible at Claude API pricing |

#### 4.9 Open questions (resolve before build)

1. **v1 scope:** ship Q0 (entry quality) + Q1 (signal discipline) only, fold Q2/Q3 in as data matures? (Recommended: **yes**)
2. **Maturity floors:** `N` (graded entries for Q0) and `M` (thesis reviews for Q2)? (Recommended: `N ≥ 5`, `M ≥ 3`)
3. **Cadence:** first Sunday of month, reusing the existing Sunday cron lane with a first-Sunday gate? (Recommended: **yes**)
4. **Model:** Sonnet, consistent with F-1/F-3 batch jobs? (Recommended: **yes**)
5. **Confirm the boundary:** the report surfaces gate-quality *patterns* but never tunes a threshold (CLAUDE.md hard rule #1). (Recommended: **locked, non-negotiable**)

---

## Build sequence

Build one feature at a time. Each is independent at the code level. F-2 optionally integrates with F-1 (thesis linkage in transcript card) — wire that integration only after both are live.

```
F-1 Thesis Tracking
  → new trades.user_thesis column (backward-compatible)
  → new thesis_reviews table
  → Trade Journal BUY form: thesis field
  → weekly review job (cron or on-demand)
  → Holdings page: thesis chip + expanded view

F-2 Earnings Call Intelligence
  → transcript API selection and account (pre-build decision)
  → new earnings_transcripts table
  → post-earnings fetch job (integrated into existing cron)
  → Catalyst Watch page: transcript card
  → (optional, after F-1) thesis linkage chip

F-3 Portfolio Debrief                                    [shipped 2026-06-27]
  → prerequisite: activate daily_snapshots (one-time DDL in Supabase)
  → prerequisite: ≥5 trading days of snapshots accumulated (build-time guard)
  → new weekly_debriefs table (unique on week_ending → upsert-safe)
  → Sunday cron lane: thesis → debrief chained; data package builder + LLM + email
  → AI Insights page: "Weekly Debrief" section + "Generate Now" button
  → light-mode-first email template (render_debrief_email; **bold** → HTML)

F-4 Portfolio Intelligence Report (monthly)              [proposed 2026-06-27]
  → reuses recommendations_history scorecard (match → compute_outcomes → rollups)
  → new monthly_reports table (unique on period_end → upsert-safe; RLS service_role)
  → first-Sunday-of-month gate in the existing Sunday cron lane
  → AI Insights page: "Monthly Portfolio Intelligence" section + Generate button
  → v1 = Q0 entry quality + Q1 signal discipline; Q2/Q3 fold in as data matures
  → BOUNDARY: surfaces gate-quality patterns, never tunes a threshold
```

---

## What this is NOT

To guard against scope creep as build progresses:

- **Not a recommendation engine.** No LLM output changes a composite score or issues an entry/exit signal.
- **Not a gate tuner.** F-4 may *surface* that a composite band underperforms, but it never changes `COMPOSITE_BUY` or any threshold — that stays an investment-policy decision with the user (CLAUDE.md hard rule #1).
- **Not a real-time feature.** These run weekly (F-1, F-3), monthly (F-4), or post-earnings (F-2). None require intraday LLM calls.
- **Not a chatbot.** Portfolio Q&A (O4 from the strategy plan) is a future phase — not part of this plan.
- **Not a replacement for the rule-based gates.** The exit ladder, concentration caps, and composite thresholds are the decision authority. These features add language and context around those decisions.

---

## Approval checklist

**F-1 — Approved 2026-06-27. Decisions locked:**
- [x] Thesis field: **optional with nudge** at BUY entry (keeps trade journal frictionless)
- [x] On-demand "Re-evaluate thesis" button per position: **yes, in v1**
- [x] BROKEN thesis + active TRIM/EXIT from engine: **combined elevated card** (highest-conviction exit signal; deserves distinct treatment)

**F-2 — Deferred. Decisions pending until budget approved:**
- [ ] Transcript API selected and budget approved?
- [ ] Held-only or include watchlist in v1?
- [ ] Does overall_tone feed any gate in v1?

**F-3 — Approved 2026-06-27. Shipped. Decisions locked:**
- [x] Delivery: **weekly email (primary) + in-app on-demand** ("Weekly Debrief" section on the **AI Insights** page + "Generate Now" button — shipped here, not Evening Debrief, to keep core pages LLM-free per the UI Placement boundary)
- [x] Multi-week pattern analysis: **deferred — week-scoped only in v1** (F-4 takes up the multi-week behavioural view)
- [x] Prerequisite: **`daily_snapshots` activated; build-time guard is ≥5 trading days of snapshots**

**F-4 — Proposed 2026-06-27. Decisions pending:**
- [ ] v1 = **Q0 (entry quality) + Q1 (signal discipline)**; Q2 (thesis discipline) + Q3 (patterns) deferred until data matures?
- [ ] Maturity floors: `N` (matured graded entries for Q0) and `M` (thesis reviews for Q2)? (recommended `N ≥ 5`, `M ≥ 3`)
- [ ] Cadence: **first Sunday of month**, reusing the existing Sunday cron lane with a first-Sunday gate?
- [ ] **Boundary locked:** report surfaces gate-quality patterns but **never tunes a threshold** (CLAUDE.md hard rule #1)?

**All features:**
- [x] LLM provider: **Claude (Anthropic)**. Model: **Sonnet** (cost-efficient for weekly/monthly batch jobs; Opus reserved for review/planning tasks per existing cost-routing policy)
