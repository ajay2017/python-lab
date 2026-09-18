# 🔎 Portfolio Investigator — Design Plan

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
| `forward_alpha_at_horizon` | `predictive_analytics.py` | Fixed-horizon forward alpha for any ticker/date |
| `TICKER_SECTORS` (static lookup, not a fetch) | `portfolio.py` | Sector-level grouping, no network call needed |
| `fetch_live_prices` / `fetch_spy` | `data.py` | Live price + SPY history inputs the above functions need |

Deliberately excludes anything not already exercised against real data — the toolbox grows
the same way Predictive Shadow Modeling's scope grew: one deliberate, reviewed addition at a
time, never speculatively.

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

## Open questions still genuinely unresolved (to be settled via mockup iteration, then a
formal `planner` pass before any code)

- Exact plan-step prompt design and its failure mode when no toolbox function fits — the
  mockup's second example (the ATR-stop-simulation question) shows the desired REFUSAL shape;
  the actual prompt/logic that reliably produces that refusal instead of an improvised guess
  is still unbuilt.
- Cost/latency: multi-step tool orchestration is a materially bigger LLM-usage and latency
  profile than Ask's single classify-then-lookup call — needs an honest budget conversation
  before scoping v1's toolbox size.
- Whether investigations should be resumable/multi-turn (like Ask's follow-up support) or
  always start fresh per question.

## Files (once a build is actually approved — not yet)

Design doc: this file. Mockup: `docs/mockups/portfolio-investigator-mockup.html`. No
`stock_analyzer/*.py` or `app.py` changes have been made — this is a planning-only commit.
