# Plan: Thesis Authoring & Entry Analyst Desk (F-5)

**Status:** **Phase 1 BUILT + Opus-reviewed 2026-06-28** (`py_compile` OK; 2 should-fix items fixed). Phases 2–3 deferred. **Pending:** the one-time `ALTER TABLE trades ADD COLUMN thesis_source TEXT;` DDL in Supabase + push-to-deploy. The code **ships safe before the DDL** (`save_trade` retries without the column).
**Sibling plan:** [ai-intelligence-layer.md](ai-intelligence-layer.md) (F-1…F-4). This is the **generative complement to F-1**: F-1 *reviews* a thesis; F-5 *authors* one and red-teams the entry.
**Decision authority (locked by user 2026-06-28):** **Analyst desk — advisory only.** The LLM authors and red-teams; the deterministic 5-gate / `COMPOSITE_BUY` engine remains the *sole* decision authority. No LLM output moves a gate, score, or stop. The "LLM narrates, engine gates" invariant is **preserved unchanged** — not amended.
**Scope (recommended sequencing):** **Entries first** (Phase 1–2), exits as a clean follow-on (Phase 3).

---

## Why this, why now

The app already makes strong forward decisions and already *reviews* a thesis once written (F-1, [`thesis_advisor.py`](../../stock_analyzer/thesis_advisor.py)). But the entire AI Intelligence Layer feeds on one field — `trades.user_thesis` — and that field is **optional-with-nudge**. In practice it is blank or thin. A thin thesis yields a thin review (F-1's own risk table admits this), and F-3/F-4 inherit the same starvation.

> The bottleneck on the whole intelligence layer is not a smarter reviewer or a new decision-maker. It is **getting a real, falsifiable thesis written at the buy moment.**

The user's own MU thesis is the target shape:

> *"MU is a leader in memory, which is in high demand due to a large shortage of memory chips. MU is one of the big players, has a pile of orders, and the latest earnings highlighted the shortage only normalizes in 2027."*

That is a **durable fundamental claim with a falsifiable condition baked in** ("normalizes in 2027" is the testable clause). It is the opposite of the engine's auto-thesis (`recommendations.thesis` = "price above 50d MA, momentum +12%"), which is entry *timing*, not conviction — and useless to F-1 because technicals always drift.

F-5 lowers the friction of writing an MU-quality thesis to one click, at the exact moment the data is richest (a pick surfacing in **New Positions to Initiate**), then hands it to the existing F-1 loop. **It builds the data foundation the rest of the layer has been missing.**

---

## Operating constraints (reaffirmed from the AI layer, non-negotiable)

All six constraints from [ai-intelligence-layer.md §Operating constraints](ai-intelligence-layer.md) apply verbatim. The load-bearing ones for F-5:

1. **LLM narrates; gates decide.** No F-5 output changes a composite score, moves a stop, or issues an entry/exit recommendation. The engine surfaces the pick; the LLM only explains and red-teams it.
2. **Zero runtime dependency.** If the LLM is offline / no API key, the BUY form's thesis field is a plain manual text box exactly as today — identical to how the F-1 "Re-evaluate" button is disabled at [app.py:17085](../../app.py#L17085). Nothing else degrades.
3. **Fail loud.** A failed authoring call surfaces an explicit "couldn't draft — write it yourself" state. No silent fallback that looks like a real draft.
4. **No thresholds in LLM output.** The draft describes the company; it never quantifies a gate, price target, or probability.
5. **Provider abstraction.** Reuse the existing `st.secrets["anthropic"]["api_key"]` / `ANTHROPIC_API_KEY` plumbing and `claude-sonnet-4-6` default ([app.py:16957](../../app.py#L16957), [cron_runner.py:290](../../cron_runner.py#L290)).

### Two new invariants this feature introduces (the critiques, locked)

6. **The user is always the author of record.** An AI draft is **never** persisted as `user_thesis` without an explicit user accept/edit step. The draft lands in the editable field; the user owns the final text. The app must never claim an un-accepted AI draft as the user's conviction.
7. **Author and reviewer read different evidence windows.** Authoring (F-5) is grounded in **entry-time** evidence. F-1 review weights **post-entry** evidence (news/earnings *since* the buy). This keeps F-1 a genuine test rather than the LLM grading its own homework.

---

## What exists today (do NOT rebuild)

| Piece | Where | Role for F-5 |
|---|---|---|
| BUY-form thesis field (`user_thesis_val` text_area, falsifiable-structure placeholder, add-to-winner prefill) | [app.py:12905](../../app.py#L12905) | F-5 pre-fills this field; save path unchanged ([app.py:13145](../../app.py#L13145)) |
| `trades.user_thesis` column (nullable, backward-compatible) | [db.py:647](../../stock_analyzer/db.py#L647) | Storage — unchanged |
| `db.update_user_thesis()` | [db.py:777](../../stock_analyzer/db.py#L777) | Edit path from AI Insights — unchanged |
| F-1 reviewer (`review_thesis` / `run_batch_review`) | [thesis_advisor.py:155](../../stock_analyzer/thesis_advisor.py#L155) | Consumes the authored thesis on the existing weekly + on-demand loop |
| Engine evidence (composite band, gates cleared, fundamentals, catalyst, news) | `scoring` / `catalyst_watch` / `fundamentals` / news pipeline | Inputs to the authoring prompt |
| LLM plumbing + provider abstraction | [app.py:9410](../../app.py#L9410) `_call_ai_brief`; secrets at [app.py:16957](../../app.py#L16957) | Reused — no new dependency |
| Composite boundaries: `COMPOSITE_STRONG_BUY=75`, `COMPOSITE_BUY=65` | [constants.py:87-88](../../stock_analyzer/constants.py#L87-L88) | Read-only context fed to the prompt (never tuned) |

---

## Architecture at a glance

```
                ENGINE (unchanged, sole decision authority)
   5 gates + Composite ≥ 65  ──►  "New Positions to Initiate"  ──►  user BUYs
                                                                       │
   ┌───────────────────────────────────────────────────────────────  │  ────┐
   │  ANALYST DESK (advisory, additive, zero-dependency)               ▼      │
   │                                                                          │
   │  Phase 1  Thesis Authoring   ── LLM drafts editable thesis ──► user      │
   │           (entry)               accepts/edits ──► trades.user_thesis     │
   │                                                                          │
   │  Phase 2  Entry pre-mortem   ── bull / bear / disconfirming card         │
   │           (entry)               (surfaced on AI Insights; never gates)   │
   │                                                                          │
   │  Phase 3  Exit narration     ── why-to-sell + "what keeps you in"        │
   │           (TRIM/EXIT)           (pairs with the existing combined card)  │
   └──────────────────────────────────────────────────────────────────────────┘
                                     │
              authored thesis ──►  F-1 weekly review (INTACT/WEAKENING/BROKEN)
                                     │   reads POST-ENTRY evidence
                                     ▼
                            F-3 weekly / F-4 monthly  (now fed by real theses)
```

The LLM sits **entirely to the side** of the decision spine. Remove it and every gate, pick, and exit still fires.

### When each piece fires (the lifecycle — read this if the phases blur together)

```
PICK SURFACES ──► [Phase 2] pre-mortem (bull/bear, BEFORE you buy)
YOU BUY       ──► [Phase 1] INITIAL thesis goes in (at the buy moment)   ◄── the core ask
   … time …   ──► [F-1, ALREADY BUILT] weekly review: does it STILL hold? (INTACT/WEAKENING/BROKEN)
TRIM/EXIT     ──► [Phase 3] exit narration (why-to-sell; at SELL time)
```

**Key clarification:** the *"does the thesis still hold over time?"* check is **F-1, already live** — it is NOT Phase 2 or 3. Phase 1's whole job is to feed F-1 a real, falsifiable thesis so that ongoing review stops being starved. **Phase 1 + F-1 together = the complete thesis lifecycle** (author it → test it over time). Phases 2 and 3 are advisory colour at the buy and sell moments — optional, and a fair place to stop after Phase 1.

---

## Phase 1 · Thesis Authoring (the core ask)

### 1.1 Trigger & surface

A **"✨ Draft thesis from the engine's analysis"** affordance sits next to the existing thesis text_area in the BUY form ([app.py:12905](../../app.py#L12905)).

- **On click →** one LLM call → the draft pre-fills the (still editable) text_area.
- **Disabled when** no API key or LLM offline — with a caption ("AI drafting unavailable — write your thesis below"). The field works as a plain text box. (Same disable pattern as the F-1 Re-evaluate button.)
- **Availability:** enabled for any ticker the app has loaded fundamentals/technicals for. When the ticker is a **current engine pick**, the draft also folds in the pick rationale (composite band, gates cleared, catalyst) — richest output. For a manual/off-engine buy it still drafts from fundamentals + news, just thinner. (See Open Question 1.)

The user **must** accept or edit before Submit — the draft is never auto-saved (invariant 6). Save path is unchanged.

### 1.2 Inputs (Python builder assembles — same discipline as F-1's `build_review_inputs`)

A new `thesis_advisor.build_authoring_inputs(...)` assembles a structured package *before* the LLM call:

- **Identity:** ticker, company name, sector.
- **Engine read (context only, never echoed as a recommendation):** composite score + band (Strong Buy ≥75 / Buy ≥65), which of the 5 gates it cleared, conviction tier.
- **Durable fundamentals:** revenue growth, margin trend, earnings trend (FMP/yfinance) — the load-bearing evidence.
- **Catalyst:** next earnings date / known catalysts (`catalyst_watch`).
- **News:** last ~30d headlines (the same pipeline F-1 uses — the LLM can only interpret given headlines, never invent).
- **Technical context:** included but **explicitly labelled "entry timing, not thesis"** so the model does not anchor the thesis on it.
- **Regime:** macro/sector tag.

### 1.3 The authoring prompt (this is the crux)

New `thesis_advisor.draft_thesis(...)`, mirroring `review_thesis`'s shape (returns `None` on failure → fail-loud). The system prompt requires the draft to contain **three parts** and forbids the failure modes:

| Required part | What it must contain |
|---|---|
| **The durable claim** | *Why this company wins* — competitive position, demand, catalyst. NOT "price above the 50-day MA." |
| **The supporting evidence** | Grounded only in the provided package. Cannot invent a number, order book, or analyst view. |
| **The falsifiable condition** | *"This thesis breaks if…"* — the testable clause F-1 needs. This is the crown jewel; the prompt weights it heaviest. |

**Hard prompt rules:** no price targets, no buy/sell/hold language, no probabilities or gate values, ≤ 500 chars (matches the field), prose only, and an explicit **"DRAFT — edit to match your actual conviction"** preamble in the UI (not stored). Output is plain text that drops straight into the text_area.

> Worked example (what good looks like, MU-shaped):
> *"MU is a top-tier memory supplier into a structural DRAM/HBM shortage; AI datacenter demand is pulling HBM forward and the order book is filling into 2027. Holds while the shortage persists and pricing stays firm. **Breaks if** memory pricing rolls over, hyperscaler capex guidance cuts, or a competitor closes the HBM gap."*

### 1.4 Closing the F-1 loop without grading its own homework (invariant 7)

- The draft is grounded in **entry-time** evidence; F-1 already reviews against **current** evidence. To make the separation explicit, pass the **entry date** into F-1 so its prompt weights *post-entry* developments. (Small additive change to `build_review_inputs` / `_format_prompt`.)
- Net effect: the authored thesis states the premise; F-1 later tests whether reality since the buy still supports it. Genuine test, not a tautology.

### 1.5 Data model (additive, backward-compatible)

```
trades table — new column:
  thesis_source  TEXT  NULL   -- 'manual' | 'ai_draft' | 'ai_edited'
                              -- lets F-4/analytics distinguish authored vs. handwritten
                              -- theses, and enforces invariant 6 (never claim an
                              -- un-accepted draft as the user's conviction)
```

`db.load_trades()` backfills `None` for legacy rows (per the backward-compat convention). No change to `recommendations`, gates, or scoring.

---

## Phase 2 · Entry pre-mortem (advisory red-team)

Once a pick surfaces (or at the buy moment), the analyst desk can produce a structured **"before you buy"** card — the second opinion that improves decision quality without touching the gate:

- **Bull case** — the engine's implied thesis, stated plainly.
- **Bear case / what could go wrong** — the disconfirming view the gate doesn't articulate.
- **The single most important thing to watch** — the one disconfirming signal.

**Surface:** AI Insights page (primary), per the existing UI-placement boundary in the sibling plan — core pages stay LLM-free. An optional non-gating "analyst note →" link may point from the pick card to AI Insights (the pick card is complete and actionable without it). Never gates, never suppresses a pick. (See Open Question 3.)

**Data model:** optional `entry_analyses` table (`id, ticker, surfaced_at, bull, bear, watch_signal, generated_at, model`) — additive, ships inert until DDL. Only built if Phase 2 is approved.

---

## Phase 3 · Exit-side narration (advisory)

When the deterioration / risk-off ladder fires **TRIM/EXIT** ([`exit_advisor.py`](../../stock_analyzer/exit_advisor.py)), the analyst desk narrates:

- **Why the exit fired** — in plain language, reading the same evidence the rule used.
- **"What would keep you in"** — the inverse falsifiable condition, so the user can weigh holding deliberately rather than emotionally.

Pairs naturally with the **existing combined card** (BROKEN thesis + engine EXIT, locked in the F-1 approval). Advisory only — the rule-based exit still fires independently and remains the decision.

---

## Boundary — what F-5 is NOT

- **Not a decision-maker.** It never green-lights a buy the engine didn't surface, never vetoes one it did, never moves a gate/score/stop.
- **Not a gate input.** Even the pre-mortem's "bear case" is read-only colour; it does not feed the composite or a 6th gate. (That was the "scoring input" / "autonomous" fork — explicitly **not** chosen.)
- **Not an auto-author.** No thesis is saved without the user accepting it.
- **Not a real-time feature.** One on-demand call per buy; Phases 2–3 piggyback the existing cron lanes.

---

## Cost

One Sonnet call per buy (user-triggered, on-demand) + the existing weekly F-1 batch. Phases 2–3 are one call per surfaced pick / fired exit. Negligible at Claude API pricing (consistent with the F-1/F-3/F-4 cost profile).

---

## Build sequence

```
Phase 1 — Thesis Authoring  [the core ask; build first]
  → thesis_advisor.build_authoring_inputs()  (evidence package builder)
  → thesis_advisor.draft_thesis()            (LLM call, returns None on failure)
  → BUY form: "✨ Draft from analysis" affordance → pre-fill text_area (editable)
  → trades.thesis_source column (backward-compatible)  [one-time additive DDL]
  → F-1: pass entry date so review weights post-entry evidence (invariant 7)

Phase 2 — Entry pre-mortem  [advisory red-team]
  → entry_analyses table (additive, inert until DDL)
  → bull / bear / watch-signal builder + LLM call
  → AI Insights surface (+ optional non-gating "analyst note →" link)

Phase 3 — Exit narration  [symmetric close of the lifecycle]
  → exit rationale + "what keeps you in" on TRIM/EXIT
  → pairs with the existing BROKEN-thesis + EXIT combined card
```

---

## Phase 1 — Concrete build spec (for final review before code)

**Routing:** PLAN (this) → BUILD on Sonnet (`implementer`) for the mechanical wiring → REVIEW on Opus (`reviewer`; touches the BUY form + a new column) → COMMIT. The system prompt and the "author of record" logic stay on the Opus lead (decision-adjacent).

### A. New code — `stock_analyzer/thesis_advisor.py` (additive; mirrors the existing reviewer)

```
_DRAFT_SYSTEM_PROMPT        — three-part contract + hard rules (§B)
_format_authoring_prompt(ticker, inputs) -> str
build_authoring_inputs(identity, engine, fundamentals, catalyst,
                       news_headlines, technical, regime) -> dict
draft_thesis(ticker, inputs, api_key,
             model="claude-sonnet-4-6", max_tokens=300) -> dict | None
    → {"draft": str, "model": str, "generated_at": iso}  or  None (fail-loud)
```

`draft_thesis` reuses the exact `anthropic.Anthropic(api_key=...).messages.create(...)` shape as `review_thesis` ([thesis_advisor.py:177-196](../../stock_analyzer/thesis_advisor.py#L177-L196)); returns `None` on any exception.

### B. The system prompt (the crux — stays on Opus)

The model MUST produce three parts: **durable claim** (why the company wins — competitive position / demand / catalyst, never "price above the 50-day MA"), **evidence** (only from the provided package — inventing a number/order-book/analyst view is forbidden), and a **falsifiable clause** (ends with "Breaks if …"). Hard rules: no price targets, no buy/sell/hold words, no probabilities or gate/threshold values, ≤ 500 chars, prose only. Framed as: *"You are drafting a CANDIDATE thesis the investor will edit and own; price/technical levels are entry timing, not the thesis."*

### C. UI wiring — BUY form ([app.py:12886-12921](../../app.py#L12886))

**The one non-trivial detail (Streamlit form constraint):** the thesis `text_area` is *inside* `st.form`, and a form may contain only `st.form_submit_button`. So the **"✨ Draft from analysis" button must sit OUTSIDE the form** — the same pattern the ticker/action widgets already use ("set by widgets outside the form", [app.py:12923](../../app.py#L12923)).

```
[outside form]  "✨ Draft from analysis" button (disabled if no api_key)
      └─ on click: build_authoring_inputs(_tj_ticker) → draft_thesis()
                   → st.session_state["_thesis_draft"] = result["draft"]  → st.rerun()
[inside form]   text_area value = _thesis_draft (if present) else _thesis_prefill
                   → user edits freely → Submit
```

Degradation: no key / offline → button disabled with caption; `draft_thesis` returns `None` → `st.warning("Couldn't draft — write your thesis below")`; the text_area is always usable (zero-dependency invariant 2 + fail-loud invariant 3). Evidence source: reuse already-loaded session data (scored universe / fundamentals / news) where present; for an off-engine ticker, one targeted fundamentals+news fetch. **No new data feed.**

### D. `thesis_source` capture (invariant 6)

At submit ([app.py:13145](../../app.py#L13145)), set the column by comparing the saved text to the stored draft:
- no draft generated → `'manual'`
- final text == the draft verbatim → `'ai_draft'`
- final text != draft (a draft existed) → `'ai_edited'`

### E. Data + DB

- **DDL (one-time, additive):** `ALTER TABLE trades ADD COLUMN thesis_source TEXT;` (nullable; RLS already covers `trades`).
- **`db.load_trades()`** backfills `None` for legacy rows — add `thesis_source` alongside `user_thesis` in the backfill list ([db.py:647](../../stock_analyzer/db.py#L647)).
- Insert path adds `thesis_source` to the trade dict (BUY only; `None` otherwise).

### F. F-1 evidence-window tweak (invariant 7)

Pass the entry date into `build_review_inputs` / `_format_prompt` so the weekly review explicitly weights **post-entry** news/earnings — keeps author ≠ reviewer honest. Small, additive.

### G. Doc sync (feature-commit rule)

- `docs/requirements.md` — new F-row in the §3.12 AI sequence (next number, ~F-154): "Thesis Authoring — LLM drafts an editable, falsifiable thesis at BUY; user owns the final text; feeds F-1."
- `docs/architecture.md` — `thesis_advisor` gains authoring entry points; `trades.thesis_source` added to the schema table.

Ships **inert** until the DDL runs and the Anthropic key is set (already true for F-1/F-3/F-4).

---

## Decisions (resolved before build)

1. **Authoring availability** → **any loaded ticker** (incl. manual journal buys; richest for engine picks). *(resolved 2026-06-28)*
2. **`thesis_source` column** → **yes** — protects analytics integrity and enforces invariant 6. *(resolved 2026-06-28)*
3. **Scope** → **Phase 1 only is the locked build target**; Phases 2 (pre-mortem) & 3 (exit narration) deferred, revisited after real theses accumulate. *(resolved 2026-06-28)*
4. **Model** → **Sonnet** (`claude-sonnet-4-6`), consistent with F-1/F-3/F-4 batch jobs. *(resolved 2026-06-28)*
5. **Pre-mortem surface** — N/A until Phase 2 is taken up.

---

## Approval checklist

- [x] Decision authority = **Analyst desk (advisory)** — engine stays sole authority; "never gates" preserved unchanged. *(locked by user 2026-06-28)*
- [x] Scope = **Phase 1 only** (authoring at BUY); Phases 2–3 deferred. *(locked by user 2026-06-28)*
- [x] Invariant 6 — **user is author of record** (no auto-save of AI drafts).
- [x] Invariant 7 — **author vs. reviewer read different evidence windows** (entry-time vs. post-entry).
- [x] Decisions 1–4 resolved.
- [x] **Built** (`thesis_advisor.draft_thesis`/`build_authoring_inputs`; BUY-form wiring; `trades.thesis_source` + resilient `save_trade`) — `py_compile` OK.
- [x] **Opus-reviewed** — no blockers; 2 should-fix items fixed (thesis survives a validation-failure resubmit; regime reads the real `_market_tone_cache` key). 1 pre-existing F-1 bug flagged (empty technicals/fundamentals fed to the on-demand reviewer — out of scope).
- [x] **Docs synced** — requirements F-150a, architecture module line + `trades` schema (`user_thesis` + `thesis_source`).
- [ ] **Run DDL + deploy** ← the boxes left: `ALTER TABLE trades ADD COLUMN thesis_source TEXT;` in Supabase, then commit + push to `main` (auto-deploys).
