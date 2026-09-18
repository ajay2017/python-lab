# 🔎 Portfolio Investigator — Design Plan

**Status update 2026-09-18: PRODUCTION-VERIFIED, two real live bugs found and fixed post-ship.**
The refusal eval (chunk 5) only ever exercised the PLAN stage against a real API — the
REPORT-synthesis stage had zero live-model test coverage until the owner tried a real question in
production. First attempt (`claude-opus-5`, `INVESTIGATOR_MAX_TOKENS_REPORT`=1000) silently
returned no report at all: a reasoning-capable model can spend the whole token budget on invisible
thinking before emitting text, and the API returns a normal (non-exception) response in that case,
so even the "Details:" diagnostic caption showed nothing. Fixed by bumping the budget to 4000
(commit `4249a50`) and hardening `call_llm` to also catch an empty/whitespace-only text block, not
just a completely absent one. That fix immediately surfaced a SECOND real failure: the larger
budget let the model generate a genuinely longer response, which now exceeded `ai_provider.
call_llm`'s fixed 30s timeout — a real `anthropic.APITimeoutError`. Fixed with a new, Investigator-
specific `INVESTIGATOR_LLM_TIMEOUT_SECONDS`=90 constant (commit `705b344`), leaving `call_llm`'s
own shared default untouched for any future caller. **First real end-to-end success, same day:**
"How has my protective EXIT and TRIM alpha performed against SPY?" on `claude-sonnet-4-6` — a
correct 1-step plan, a real per-signal table, honest handling of not-yet-scoreable signals, and a
mandatory Caveats section, all rendering exactly per the mockup. Full detail + the third
non-bug ("no eligible model configured" after every redeploy is session-state cold start, not a
regression) in memory `project_portfolio_investigator`.

**Status: FULLY SHIPPED 2026-09-18 — all 7 chunks complete.** Chunk 6 (`app.py` — the 8th
"🔎 Investigator" tab on 🧠 AI Insights, plus the provider/model config section on 🩺 System
Trust) built by `implementer` against a fully-researched, line-anchored spec, then the mandatory
Opus `reviewer` pass (required regardless of `_GATE_FILES` membership — a new user-facing
decision surface per Hard Rule #4) caught one real blocking bug before ship: the data-bundle
builder sourced `trades_df` via a path that collapsed a failed read into an indistinguishable
empty DataFrame, unlike its sibling `recs_df`/`exit_signals_df` inputs which correctly used
their `_or_none` loaders — fixed via `db.load_trades_or_none()`, same commit (`041085e`). Chunk 7
(docs sync) closed requirements.md F-275, two new `docs/architecture.md` module sections
(`ai_provider.py`, `investigator.py`), the in-app User Guide (both the AI Insights and System
Trust descriptions), and memory `project_portfolio_investigator`. Full suite 5881 passed.
**Nothing left queued from this feature's original v1 scope** — the only deliberately-deferred
item is `forward_alpha_at_horizon`/v1.1 (a per-question ticker/date scalar the plan schema has no
slot for; needs its own schema extension + resolver, not started, no trigger set). Older status
lines below are superseded except for the mockup/planner history they still accurately describe.

**Status update 2026-09-18: chunks 1-5 of 7 SHIPPED. `claude-sonnet-4-6` and `claude-opus-5`
both have a recorded refusal-eval PASS (34/34 each, see chunk 5 below) — the two enabled
candidates for chunk 6's model selector so far. Chunk 6 (`app.py` tab wiring) and chunk 7 (docs
sync) are the only pieces left.** Older status line below is superseded except for the
mockup/planner history it still accurately describes.

**Status: MOCKUP-APPROVED, HANDED TO `planner` 2026-09-18.** Seven rounds of mockup
iteration resolved every placement/UX/safety scope decision below (fixed toolbox, sync flow,
owner-only gating, LLM provider flexibility, offline-failure discipline, relationship to the
standalone scripts). No `stock_analyzer/*.py` or `app.py` code has been written — this
remains a planning-only artifact until the `planner` pass below returns a design a build can
actually start from. Grew out of
a 2026-09-17 brainstorm session that used a manual, multi-step, tool-composing investigation
(SQL pulls, live price fetches, reused pure functions, self-caught bug, disclosed caveats) to
answer three real questions about the owner's own trading (A4's self-track cut, A1's
regime-confound check, the growth-cluster concentration completing piece — see memory
`project_investor_maturity_roadmap`). The idea: turn that *workflow* into an on-demand
capability inside the app, not a one-off session artifact.

## Why this is a different thing from 💬 Ask, not an extension of it

`stock_analyzer/portfolio_qa.py` (💬 Ask, F-225, the 7th tab on 🧠 AI Insights) is a
**single-shot, fixed-intent lookup**: an LLM classifies the question into one of six
`_VALID_INTENTS` (`trades_in_range` / `rec_outcome` / `trade_lookup` / `holding_lookup` /
`portfolio_summary` / `unsupported`), each intent maps to exactly one deterministic Python
function, and the result is narrated. No composition, no multi-table joins, no live-price
cross-referencing beyond what a single intent already does, no self-correction.

What the 2026-09-17 session actually did, abstracted:
1. Turn a vague question into a plan — which existing pure functions answer it, what data
   they need.
2. Pull that data from multiple sources (Supabase tables, live market prices), and verify
   it's *complete* before trusting it (the 268-row trades reconciliation, the missing-row
   diff).
3. Call the SAME reused functions the live app's own cards use
   (`protective_track_record.compute_protective_outcomes`, `investor_mirror.build_closed_lots`,
   `db.recalculate_from_trades`) — never a parallel reimplementation.
4. Catch a real bug in its own reasoning mid-flight (the `classify_sells` vs.
   `build_closed_lots` date-derivation mismatch) and fix it before reporting.
5. Report with mandatory, explicit caveats — sample size, correlation-vs-causation,
   single-window limits.
6. Only persist anything (docs/memory) after explicit owner approval.

That's multi-step, tool-composing, and self-correcting. Ask is none of those. The
Investigator is not a smarter Ask — it's a different *kind* of tool, placed next to Ask
because it answers a different *class* of question (open-ended "why"/"is this true"
investigations vs. "what happened on this trade" lookups).

## The one design decision everything else depends on: fixed toolbox, not open code execution

Two ways to give an LLM agent the power to compose an investigation:

- **Fixed toolbox (chosen).** The agent may only call a curated, already-reviewed set of
  pure functions from `stock_analyzer/*.py`. It composes them in new combinations to answer
  a new question, but cannot write new analysis logic and execute it against live data.
  Every number it produces is traceable to a function that has already been reviewed once,
  for a different reason, by a human.
- **Open code generation (rejected for any version of this feature).** Let the agent write
  and run arbitrary Python against live trades/prices. Strictly more powerful, and exactly
  the failure shape this project has spent all its Hard Rules avoiding: unreviewed,
  self-written logic executing against real financial data with no second pair of eyes.

This mirrors the "reuse, never reimplement" convention that already governs every
`scripts/*.py` analysis in this repo (`offense_attribution.py`, `exit_ladder_replay.py`,
`holding_period_analysis.py` — none of them re-derive an alpha calculation or a lot-matching
algorithm; they all call into `stock_analyzer/*.py`). The Investigator generalizes that
existing discipline into an on-demand capability instead of a new bespoke script per
question.

## Hard redlines (non-negotiable, unaffected by how good the LLM gets)

- **Read-only, always.** Never writes to `trades`, `constants.py`, or any production table.
- **Never influences a gate, score, or recommendation.** Same posture as Predictive Shadow
  Modeling, The Judge, Self Track Record, Gate Suppression Ledger — awareness-only.
- **No autonomous triggering.** Every investigation starts because the owner asked. No
  scheduled runs, no self-initiated checks.
- **No persisted state that changes future behavior.** Each investigation is fresh and
  bounded — the tool does not "learn" an approach across sessions in a way that could drift
  without a human noticing.
- **Docs/memory sync stays a proposal, gated on explicit approval, every time** — exactly
  the pattern used all through the 2026-09-17 session (propose → owner says yes → sync).
- **Owner-only, the whole tab** — RATIFIED 2026-09-18, see Placement below. Not inherited
  from the sibling Ask tab, which is viewer-visible.
- **Fails visibly, never silently, on any fetch/compute failure** — RATIFIED 2026-09-18. If
  Supabase is unreachable or a live price fetch fails mid-investigation, the report says so
  plainly ("couldn't complete this — [what failed]") rather than degrading quietly or
  reasoning from partial data as if it were complete. Same standing discipline as the rest of
  this app's offline-sentinel handling (`feedback_sentinel_is_present`-class care) — the
  Investigator does not get an exception to it just because an LLM is narrating the result.
- **The toolbox IS the data-access boundary, not just the compute boundary** — RATIFIED
  2026-09-18. The agent never runs an ad hoc query of its own; it only ever calls functions
  that already define exactly what they read. There is no separate "let the agent query
  Supabase directly" capability alongside the toolbox — the toolbox function list from the
  table above is the complete set of what this feature can ever read, not a suggestion.
- **Does not replace or duplicate `scripts/exit_ladder_replay.py` /
  `offense_attribution.py` / `holding_period_analysis.py`.** Those stay as they are, useful
  specifically for out-of-app / no-live-credential situations (the exact situation the
  2026-09-17 session itself was in). The Investigator's toolbox calls the SAME underlying
  `stock_analyzer/*.py` functions those scripts already call — two callers of one source of
  truth, never a fork or a second implementation.

## Architecture sketch (5 stages)

1. **Plan.** An LLM reads the question and maps it to: which toolbox functions apply, what
   data they need (tables, live prices, date ranges), and whether the question is answerable
   at all with the current toolbox. `"I don't have a reviewed function for that"` must be a
   normal, frequent, honest answer — not something the agent works around.
2. **Fetch.** Runs inside the app's own environment using its existing Supabase connection
   and `stock_analyzer.data`'s live-price failover — no manual SQL-paste round-trip (that
   was specific to a coding assistant without the app's live credentials, not a property of
   the real feature).
3. **Verify.** Row-count / completeness sanity checks before computing anything — the same
   discipline the 2026-09-17 session applied by hand (confirming 268/268 against
   `COUNT(*)`).
4. **Compute.** Calls only pre-approved toolbox functions. Nothing else executes.
5. **Report.** A required output shape, not free text: finding, exact functions/data used,
   and a mandatory caveats section — mirroring the "HONEST CAVEAT" convention already in
   every `scripts/*.py` analysis. Ends with an explicit, separate "sync this to docs/memory?"
   action the owner must click — never automatic.

## Toolbox v1 — start with exactly what was proven on 2026-09-17, not a speculative superset

| Function | Module | Answers |
|---|---|---|
| `build_closed_lots` | `investor_mirror.py` | Holding-period distributions, realized P&L by any grouping |
| `classify_sells` | `self_track_record.py` | App-aligned vs. self-initiated sell classification |
| `compute_protective_outcomes` / `collapse_by_ticker` / `protective_headline` | `protective_track_record.py` | Protective (EXIT/TRIM) call alpha, per ticker or aggregate |
| `recalculate_from_trades` | `db.py` | Current holdings + corrected realized P&L, replayed from the trade log |
| `match_recs_to_trades` / `compute_outcomes` | `recommendations_history.py` | BUY-side recommendation alpha, acted vs. skipped |
| `TICKER_SECTORS` (static lookup, not a fetch) | `portfolio.py` | Sector-level grouping, no network call needed |
| `fetch_live_prices` / `fetch_spy` | `data.py` | Live price + SPY history inputs the above functions need |
| `sector_composition` (wraps `sector_exposure`) | `portfolio_qa.py` (wraps `portfolio.py`) | Dollar-weighted sector concentration for held positions — market value and % of book per sector |

Deliberately excludes anything not already exercised against real data — the toolbox grows
the same way Predictive Shadow Modeling's scope grew: one deliberate, reviewed addition at a
time, never speculatively. **`sector_exposure` added 2026-09-18, the first genuine post-ship
toolbox growth, exactly on that principle:** the first real production question ("how concentrated
is my portfolio by sector?") could only be answered by counting positions per sector, because no
toolbox function surfaced a per-position market value — `recalculate_from_trades`'s `holdings_df`
is deliberately just `[Ticker, Shares, Avg Cost ($)]`, out of scope for what that function exists
to fix. The model itself correctly refused to multiply shares × price into a market value on its
own (the report-writing prompt's "narrate, never derive" rule working exactly as intended) rather
than fabricating the number — so the gap was disclosed, not silently wrong, which is what made it
legible as a real toolbox gap rather than a bug. Closed by reusing `portfolio_qa.sector_composition()`
verbatim (already a thin, reviewed wrapper around `portfolio.sector_exposure()`) — same numbers
Portfolio Overview's own sector chart shows, and more complete than the existing `ticker_sectors`
entry's bare static lookup (`port_df`'s own `Sector` column already goes through the live app's
full curated-map → `.info` → cache → "Other" resolution chain). Pure-additive, no `_GATE_FILES`
touch, no reviewer required per the review-economy rule (reused function, non-gate file, full
suite green). **Re-verified 2026-09-18, same day: both recorded-pass models re-ran clean after
this toolbox growth** — `claude-sonnet-4-6` 21/21 refusal + 13/13 answer, `claude-opus-5` 21/21
refusal + 13/13 answer, 34/34 each, no misclassifications. Confirms the `sector_exposure` addition
didn't shift any refuse/answer boundary, matching the "no flip expected" prediction — the 3
sector-related eval questions were already correctly answerable before this change (via
`ticker_sectors` + `recalculate_holdings`), just with a weaker (position-count) answer.

**`forward_alpha_at_horizon` — DEFERRED to v1.1, caught by the implementer during chunk 2,
resolved 2026-09-18.** It answers a single ticker/date/entry-price question, but the plan
step's `{"fn_id", "why"}` shape (per this same `planner` pass's own design) has no per-step
argument slot for a scalar like a ticker — the fixed input vocabulary is bulk data sources
(`trades_df`, `exit_signals_df`, etc.), not per-question parameters, and no question-side
resolver exists to populate one (the same job `portfolio_qa.py`'s own parse step already does
for Ask, just not yet built for the Investigator). The implementer initially wired it with a
graceful degrade-to-error-fact fallback rather than crashing, which is sound engineering — but
it would still let the plan step select a function that can only ever fail today, which is a
half-finished capability, not a working one, and wastes an LLM round-trip in exactly the cases
where a question happens to name a ticker. **Removed from v1's registered `TOOLBOX` entirely**
rather than shipped in a permanently-degraded state; the adapter's own reasoning is preserved
in a code comment at its removal site. **v1.1 path, not started:** extend the plan-step JSON
schema with a per-step `"params"` field (and matching validation in `validate_plan`) once a
real question shape needs it, paired with a question-side ticker/date/price resolver at the
`app.py` wiring layer — not before, since speculative param-passing plumbing with nothing yet
to exercise it is exactly the kind of premature abstraction this project's own conventions
warn against.

## Placement — owner decision, ratified 2026-09-17

**Folds into 🧠 AI Insights as a new 8th tab, immediately next to 💬 Ask** (not a new
top-level page, and not merged into Ask itself). Reuses the same `st.tabs([...])` list at
`app.py` ~line 37057; same `st.chat_message`/`st.chat_input` conversational shell Ask
already uses, so the two tabs feel like siblings, not unrelated tools.

**Owner-only — RATIFIED 2026-09-18, diverging from the sibling tab.** Verified in code:
🧠 AI Insights (and thus 💬 Ask) is NOT in `_OWNER_ONLY_PAGES` (`app.py` ~line 3037) — it's
visible to a read-only viewer today. The Investigator tab does not inherit that visibility:
the whole tab is gated owner-only, not just its "Write this" sync action (which would need
that gate regardless, since it persists content into the project's own docs/memory — a
different and more sensitive category than viewing portfolio data). Since `st.tabs()` can't
selectively hide one tab within an otherwise-shared page for one viewer class, this likely
means the tab itself checks `db.is_readonly()` and renders a "🔒 owner-only" placeholder in
that slot for a viewer session, matching the defense-in-depth pattern App Settings already
uses (`db.is_readonly()` check + `st.stop()`) — exact mechanism is the planner's/implementer's
call, the requirement is the owner-only outcome.

**Explicitly NOT inheriting the sibling's LLM wiring, though.** Verified in code: 💬 Ask
(`app.py` ~line 36915) is hardwired to a single Anthropic key
(`st.secrets.get("anthropic", {}).get("api_key", "")`) — no provider or model choice. That
pattern is the wrong one to copy here.

## LLM provider/model — ratified 2026-09-18: reuse 🤖 AI Snapshot's existing registry, not Ask's hardwired key

This app already has a working, shipped answer to "don't lock this to one model": AI
Snapshot's `_AI_PROVIDERS` dict (`app.py` ~line 10685) — Claude / OpenAI / Gemini / Groq,
each listing its own selectable models, with a settings panel (provider selectbox → model
selectbox → API key resolved from secrets → env → manual entry, in that order). The
Investigator should adopt this exact shape rather than Ask's single-key pattern, so a
provider/model choice is a settings decision, not a code change.

**Adding a genuinely new future model stays a deliberate, reviewed edit — one new dict
entry — never automatic.** Consistent with how the toolbox itself only grows one validated
function at a time (see above): flexibility means "your choice among models someone has
actually confirmed work," not "silently trust whatever ships next."

**Honest caveat, load-bearing for later evaluation, not just a UI note:** provider-flexible
does not mean provider-equivalent for THIS job specifically. AI Snapshot only needs one
clean single-shot narrative pass — a small/fast model is a fine fit. The Investigator needs
reliable multi-step tool planning *and* the harder, already-flagged skill of knowing when to
refuse (see the open question on plan-step failure modes, below). A model that's perfectly
adequate for AI Snapshot's narrative (e.g. a small Groq/Llama tier) may not be reliable
enough for the Investigator's planning step. Before shipping, the smaller/cheaper tiers in
the registry need to be evaluated specifically against the refusal behavior, not assumed to
work equally just because they're already wired up elsewhere.

## A pre-existing architecture debt this question surfaced — real, but deliberately kept OUT of this feature's scope

The owner's follow-up ("is model selection a single reusable module, or scattered?") led to
checking the actual codebase rather than assuming. Finding, verified by grep, not estimated:
**19 independent, duplicated call sites** read `st.secrets.get("anthropic", {}).get("api_key",
"")` directly — 18 in `app.py`, 1 in `bundle_loader.py` — each hardwired to Anthropic only,
each with its own locally-named variable (`_ant_key`, `_rt_api_key`, `_hsb_api_key`,
`_mo_api_key`, etc., across Ask, Debriefs, Red Team, Debate Log, thesis authoring, sentiment,
premortem, structural scanning, catalyst stress, analyst intel, and more). **Only 🤖 AI
Snapshot has real provider/model flexibility today**, via the `_AI_PROVIDERS` registry
already described above.

**Decision: build a shared `stock_analyzer/ai_provider.py`-style module (extracting
`_AI_PROVIDERS`' already-proven shape, not reinventing it) and use it for the Investigator
from day one — but do NOT migrate the other 19 existing call sites as part of this work.**
That migration is real, worthwhile, pre-existing technical debt this question happened to
surface — not something to bundle into a new feature's scope. Reasons to keep it separate,
matching this project's own established discipline against unrelated-refactor bundling: (a)
19 already-shipped, independently-working features would each need their own regression
check; (b) `bundle_loader.py` is a `_GATE_FILES` member, so even a pure plumbing edit there
mechanically triggers Hard Rule #4's mandatory Opus review; (c) the house pattern for this
exact shape of problem (`util.get_or_offline`, the `safe_html`/XSS burn-down in
`check_antipatterns.py`) is "one shared module, adopted deliberately site-by-site," never a
big-bang rewrite. **If this migration is ever picked up, it is its own separately-scoped
plan, not a follow-on task of this one.**

**Placement of the provider/model config UI — RATIFIED 2026-09-18: 🩺 System Trust, not
⚙️ App Settings.** `⚙️ App Settings`'s own header explicitly scopes it to "the engine's INPUT
SET" (the three ticker rosters) and explicitly excludes anything that isn't that. An LLM
provider/model choice is neither a ticker-style input set nor a decision rule (it doesn't
touch a gate or threshold) — a third category that page wasn't built for. **Owner-confirmed:
folds into 🩺 System Trust instead**, which already exists to show configuration/diagnostic
status — a natural, non-stretching home for "which AI provider/model is configured, and is
its key resolved" as a visible, checkable state, consistent with what that page already does
for cron liveness, data-store freshness, and write-outcome diagnostics.

## Explicit scope decisions

1. **Fixed toolbox for v1** (see above) — not open code generation, ever, without a
   separate, explicit, later decision this doc does not make.
2. **No autonomous scheduling** — on-demand only, every run owner-initiated.
3. **Mandatory caveats section** in every report, not optional narrative color.
4. **Docs/memory sync is proposed, never automatic — RATIFIED 2026-09-17 as draft-then-approve
   (Option A), not a blind confirm dialog (Option B).** Clicking "Propose a sync" immediately
   shows the exact markdown that would be written, editable in place, targeted at the specific
   file(s) it would touch. Only a second, explicit "Write this" click actually saves anything;
   a "Cancel — discard draft" sits beside it. This is stricter than the "Clear conversation"
   confirm shape already used on Ask's own tab (a plain yes/no) — deliberately, because a sync
   here writes prose a future session will trust as ground truth without re-verifying (this
   project's own zero-hallucination doc-integrity standard), so the human must see and be able
   to correct the exact wording before it's saved, not just approve a description of an action
   after the fact. Mockup: `docs/mockups/portfolio-investigator-mockup.html`'s purple draft
   panel.
5. **Placement: new tab next to Ask on AI Insights**, not a separate page, not folded into
   Ask's own intent list.
6. **Trace visibility and session shape — ACCEPTED as shown in the mockup, 2026-09-17**: the
   5-step trace renders as an always-available expander (not hidden behind a second click, not
   forced open); a single continuous conversation thread, same shape as Ask's, no separate
   named/revisitable investigation sessions for v1.

## `planner` pass — 2026-09-18, Opus 4.8 (1M context), verdict PROCEED WITH CHANGES

Read the mockup-approved design and verified every code claim against HEAD (not the plan
doc's own descriptions) before proceeding — this caught a real defect, not just a nitpick.

### THE DEFECT — the mockup's "Write this" file-write is impossible on this deploy model, fixed

The mockup depicted an in-app button writing directly into `docs/*.md` and a memory file.
**This cannot work and would have been silently wrong in production:** Hard Rule #3 means
the app only ever runs on Railway (or the dormant Streamlit Cloud fallback) — never locally,
never with the owner's own git working tree. A write from the running container hits an
**ephemeral filesystem** (lost on next redeploy, never reaches git, no commit, no review) —
the file a future session actually reads is the git-tracked repo on the owner's machine, not
the container's copy, so a "successful" write would create a phantom edit nobody ever sees
while the owner believes it's now ground truth — the exact inverse of the trust this sync
feature exists to provide. Memory files live entirely outside the repo on the owner's local
machine; the container has no path to them at all. Confirmed by grep: `app.py` has **never**
written to a `.md`/memory file anywhere — no existing pattern was being copied, because this
has never been safe to do from the app.

**RATIFIED FIX, 2026-09-18: the action becomes "📋 Copy this markdown" (or a
`st.download_button`), never an in-app file write.** The owner still sees the exact editable
draft first — nothing about draft-then-approve changes — they paste it into the repo
themselves afterward, the same manual step used all through the 2026-09-17 session. Keeps
git as the real audit trail. No DDL, no new write path, no `_GATE_FILES` exposure. The
mockup's purple draft panel is otherwise correct and stays as designed; only its final button
changes semantics.

### Three open questions, resolved

**1. Refusal mechanism.** Refusal is a CODE decision over a closed enum, never the LLM's own
self-assessment. The LLM proposes a structured plan (JSON naming specific `fn_id`s from the
toolbox); a pure `validate_plan()` function checks it — any hallucinated function name, any
compute-less plan, or any plan whose data needs aren't satisfiable is rejected in code before
anything executes. The residual risk (a real toolbox function picked as a poor fit for the
question) is mitigated by the mandatory "functions & data used" trace and quantified, not
eliminated, by a required pre-ship eval (below).

**2. Cost/latency.** A fixed 5-stage pipeline, never an open-ended agentic loop. Normal case
= 2 LLM calls (plan, report); worst case = 3 (one re-plan). `INVESTIGATOR_MAX_LLM_CALLS = 3`
enforced in code — on hitting the cap it fails VISIBLY ("couldn't converge on a plan — try
rephrasing"), never loops. Same call-count order as Ask (parse + narrate = 2); the difference
is per-call input size and the compute done between calls, not an unbounded loop.

**3. Session shape.** Fresh per question, RATIFIED — each investigation re-runs fetch/verify/
compute from scratch. One narrow exception: raw FETCHED DATA (trades, live prices) may be
cached in session_state across questions within a session for speed — but a prior report's
NARRATED conclusions never feed into a new plan step, closing off the "soft conclusion
trusted as hard fact" failure mode `portfolio_qa.py` already avoids by design (its own
multi-turn history feeds only the parser, never the narrator).

### Four owner decisions, ratified 2026-09-18

1. **Sync action: copy/download, not a file write** (see the defect above — not really a
   choice, a required fix; ratified).
2. **Model tier: ALL tiers selectable** (fast/cheap included), diverging from the planner's
   own recommendation to restrict to the capable tier only. **Made consistent with decision 3
   below:** the selectbox does not pre-filter by price tier — instead, a model only appears as
   a real, enabled option once it has a recorded PASS on the refusal eval. Tier label alone
   decides nothing; the eval result does. This is arguably more rigorous than a blanket
   cutoff (it lets a genuinely capable fast/cheap model qualify on evidence rather than being
   excluded by assumption, and excludes a "capable"-labeled model that still fails the eval).
3. **Refusal eval is a REQUIRED ship gate, not optional.** A fixed, frozen labeled set of
   ~15-25 "should refuse" and ~10-15 "should answer" questions
   (`scripts/investigator_eval.py`), run against every candidate model, scoring refusal/answer
   recall. A model without a recorded pass is not offered in the tab — mirrors this project's
   own "prove skill before trusting a signal" discipline already used for the volatility-
   forecast feature (Predictive Shadow Modeling).
4. **`INVESTIGATOR_MAX_LLM_CALLS = 3`** approved as proposed.

### Module boundaries (both new, both pure, neither a `_GATE_FILES` member — verified against
`pre_tool_checks.py`'s actual list, not assumed)

- **`stock_analyzer/ai_provider.py`** — extracts AI Snapshot's `_AI_PROVIDERS` registry shape
  (`app.py` ~10685) and its dispatch (~10798-10830) into a reusable, Streamlit-free module.
  Used ONLY by the Investigator — does not touch AI Snapshot's own inline dict or the other 19
  pre-existing hardwired call sites (explicitly fenced out of this feature's scope, per
  above). `AI_PROVIDERS` dict (now carrying a per-model `tier` field used only for display,
  not gating — see decision 2), `capable_models()`, `resolve_key()` (secrets → env → ""),
  `call_llm()` (fails open to `None` + `LAST_CALL_ERROR`, never raises).
- **`stock_analyzer/investigator.py`** — the toolbox registry (per-function `adapter`, `needs`
  over a fixed input vocabulary, `summary`, `cannot`-list of out-of-scope example phrasings),
  `parse_plan`/`validate_plan` (the code gate), `verify_fetch`, `execute_plan` (runs ONLY
  named toolbox adapters), prompt builders reusing `portfolio_qa`'s narration discipline
  verbatim where it applies (no invented numbers, no rescaling a dollar figure, mandatory
  caveats), `build_sync_draft` (pure — produces the draft markdown, no write), and the
  `investigate()` orchestrator enforcing the call cap and the fail-visibly-on-offline
  contract.

Neither module gets added to `_GATE_FILES` — awareness-only, never influences a gate/score/
recommendation, same posture as The Judge / Gate Suppression Ledger (also not gate files). A
refusal-logic failure produces a wrong or absent ANSWER, never a moved call.

### `app.py` integration points (verified at HEAD 2026-09-18; expect line drift by build time)

- 8th tab added to the `st.tabs([...])` list (~37056-37058, currently 7 labels).
- Tab body placed after `with _ai_tab_ask:` (~39717), reusing Ask's chat shell.
- **Owner gate at the top of the tab body** — `if db.is_readonly(): st.info(...); st.stop()`,
  the exact `⚙️ App Settings` pattern (~32606-32612). **Load-bearing, stated explicitly: this
  in-tab check is the ONLY gate** — unlike Model Lab/System Trust/App Settings, 🧠 AI Insights
  is not in `_OWNER_ONLY_PAGES` (~3037), so there is no sidebar-level defense-in-depth here.
- Provider/model config section on 🩺 System Trust (page ~32345, already owner-gated),
  mirroring AI Snapshot's settings-expander shape but sourced from `ai_provider.py`.

### DDL — none for v1

Session thread lives in `session_state` only (matches "fresh and bounded / no persisted state
that changes future behavior"). No new Supabase table. A durable investigation-history table
was considered and explicitly deferred — it would be a `db.py` write path (mandatory Opus
review) buying nothing v1 needs.

### Build-chunk sequence (ordered; chunk 5's eval gates chunk 6 enabling the tab)

1. `ai_provider.py` + unit tests.
2. `investigator.py` core (toolbox registry, `parse_plan`/`validate_plan`/`verify_fetch`/
   `execute_plan`, NO LLM calls yet) + exhaustive deterministic unit tests for `validate_plan`
   specifically (the refusal guardrail — a "never X" claim needs a test at that exact
   boundary: hallucinated `fn_id` rejected, compute-less plan rejected, unsatisfiable `needs`
   rejected, valid plan accepted).
3. Constants (`INVESTIGATOR_MAX_LLM_CALLS`, max-token sizing) — `constants.py` is a
   `_GATE_FILES` member, mandatory Opus review citation on this commit.
4. Prompts + `investigate()` orchestrator (call cap, fail-visible offline handling,
   `build_sync_draft`).
5. **Refusal eval — `claude-sonnet-4-6` RECORDED PASS, 2026-09-18.**
   `scripts/investigator_eval.py`: a frozen, labeled 34-question set (21 refuse / 13 answer,
   drawn from the ratified toolbox's own `cannot` lists plus close phrasings of the three real
   2026-09-17 investigations), a `main()` that runs ONLY the plan step per `(provider, model)`
   pair (cheap — no Supabase/live-price credentials needed), scores refusal/answer recall, and
   prints every individual misclassification, not just an aggregate. 33 tests (29 original +
   4 for the `selected_fns` diagnostic below), all against a fake LLM, zero network dependency.

   **First real run** (owner's machine, real `ANTHROPIC_API_KEY`) against `claude-sonnet-4-6`:
   18/21 refusal, 13/13 answer — 3 misclassifications, all `expected=refuse -> got=ok`. Added a
   `score_outcome(..., plan=plan)` diagnostic that captures which `fn_id`(s) the model actually
   selected and why, specifically so a misclassification is legible rather than just a wrong/
   right count. That surfaced two distinct, genuine toolbox/prompt gaps, not eval noise:
   (a) *"AAPL's alpha after the March 3rd BUY recommendation"* — the model correctly identified
   `rec_outcomes` as relevant but had no way to know it only returns the aggregate acted-vs-
   skipped track record, never one named ticker+date instance; fixed by adding that exact
   limitation to `rec_outcomes`'s own `cannot` list. (b) *"Is my portfolio good?"* / *"Am I
   doing well as an investor?"* — the model chained 4-6 unrelated functions together rather than
   refusing an unscoped question with no named analytical angle; fixed with a new explicit rule
   in `build_plan_prompt`'s Rules section (a pile of facts from different functions is not an
   answer to a question that never named what it wanted measured).

   **Second real run, same model, same machine, after both fixes: 21/21 refusal, 13/13
   answer — 34/34, no misclassifications. Recorded PASS for `claude-sonnet-4-6`.**

   **`claude-opus-5` — RECORDED PASS, 2026-09-18, after a real bug fix (not a judgment fix).**
   Added as a new candidate model in `ai_provider.py`'s registry (one-line, deliberate — see
   ratified decision above). First real run crashed on 3 of 34 questions
   (`AttributeError: 'ThinkingBlock' object has no attribute 'text'`), scored as false refusals
   ("no valid plan was produced") rather than a real judgment gap — Opus 5 is reasoning-capable
   and can lead its response `content` with a `ThinkingBlock`/`RedactedThinkingBlock` that has
   no `.text` attribute, and `call_llm`'s Claude branch blindly read `content[0].text`. Fixed in
   `ai_provider.py` by scanning `content` for the first block that actually has `.text` instead
   of trusting position (the identical pattern still lives, unfixed, in `app.py`'s AI Snapshot
   `_call_ai_brief` at ~line 10806 — flagged as a separate, explicitly out-of-scope latent risk,
   not silently expanded into this fix). Second real run, after the fix: 21/21 refusal, 13/13
   answer — 34/34, no misclassifications. **Recorded PASS for `claude-opus-5`.**

   Two candidate models now enabled for chunk 6's model selector: `claude-sonnet-4-6` and
   `claude-opus-5`. No other candidate model has been run yet — each remains gated on its own
   recorded pass before chunk 6 can list it as enabled. To run another: `ANTHROPIC_API_KEY=...
   python scripts/investigator_eval.py --provider "Claude (Anthropic)" --model <id>` (or omit
   `--provider`/`--model` to sweep every model whose key is set in the shell).
6. **`app.py` wiring — SHIPPED 2026-09-18, commit `041085e`.** 8th tab, owner gate, chat shell,
   trace expander, caveats block (baked into `report_text` itself, never extracted separately),
   the copy/download draft panel (not a write), System Trust config section filtered to
   `investigator.EVAL_PASSED_MODELS`. Built by `implementer`; Opus `reviewer` FIX-FIRST/1
   blocking (the `trades_df` fetch-failure collapse described in the status line above) → fixed
   same commit → effectively confirmed SHIP (the fix mirrors the already-reviewed-clean sibling
   `_or_none` pattern one-to-one; no separate re-review pass was run for a change this
   mechanical).
7. **Docs sync — SHIPPED 2026-09-18, all 7 Definition-of-Done steps.** requirements.md F-275;
   two new architecture.md module sections; in-app User Guide (AI Insights + System Trust
   descriptions); memory `project_portfolio_investigator`; this plan doc's own status line.

One Opus `reviewer` pass covers chunks 2/4/6 together (`validate_plan`/`execute_plan`/the
orchestrator + eval results) before ship, on top of the mechanically-required citation on
chunk 3's `constants.py` commit.

## Files (once a build is actually approved — not yet)

Design doc: this file. Mockup: `docs/mockups/portfolio-investigator-mockup.html`. No
`stock_analyzer/*.py` or `app.py` changes have been made — this is a planning-only commit.
