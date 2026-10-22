# LLM model routing: move to current models, and change models without code changes

**Status (2026-10-22): Phase 1 IN PROGRESS. P1-a to P1-d built and reviewed; P1-e next (see section 12).** *(Earlier status: READY TO BUILD (Phase 1), 2026-10-22.)* Decisions ratified; planner (Opus 5.5) review: PROCEED WITH CHANGES (section 10). **Owner accepted every section-10 change the same day, including narrowing D-1:** the `filter` and `extract_trigger` roles are pinned in reviewed code alongside `score`, and App Settings chooses models for `fast`, `deep` and `extract` only. Analysis by the
lead from an AST inventory of every Anthropic call (2026-10-22) and the Claude API migration guide bundled with Claude
Code (cached 2026-10-06). **Owner agreed with every lead recommendation in section 7 (2026-10-22):** D-1 App Settings
with the score role pinned in code; D-2 switch the score model, but last, after the Phase 4 dry run; D-3 Sonnet 5.5
for the deep role; D-4 thinking off for score / filter / extract, adaptive `low` for fast / deep, confirmed from the
Phase 3 live checks; D-5 Opus 5.5 for broker ticker resolution; D-6 other providers unchanged.

## 1. Summary

- The app has **26 Anthropic call sites in 19 modules plus `app.py`**. Each one names its model in code:
  **Haiku 4.5** at 15, **Sonnet 4.6** at 8 (one, `earnings_intel.extract_results`, has no caller), **Opus 4.8** at 1,
  and two user-picked menus (Investigator, AI Snapshot). None of these
  models has an announced retirement date, so nothing forces a deadline.
- The current models are **2–10× cheaper per token**: Haiku 5.5 is $0.10/$0.50 per million tokens against $1/$5 for
  Haiku 4.5, Sonnet 5.5 is $2/$10 against $3/$15, and Opus 5.5 is $4/$20 against $5/$25.
- **Changing the model string is not enough.** On the 5.5 models: a non-default `temperature` returns an error (14
  call sites set one); thinking is on by default, so a reply can start with a thinking block (25 call sites read only
  the first block); and thinking counts against `max_tokens`, while Haiku 5.5's tokenizer also uses about 30% more
  tokens for the same text (many limits are 64–600). Every one of these fails quietly today: the call returns
  nothing and the feature goes blank. For news sentiment, that silently switches the composite score to VADER.
- **Proposal:** call sites ask for a **role** ("fast narration", "deep narration", "extraction", "score"), not a model.
  One helper builds each request to suit the model, using a small profile table, so the code knows each model's rules.
  The model for each role is chosen in **⚙️ App Settings**: stored in the database, keeps a change history, takes
  effect without a redeploy, and is read by both the app and the cron jobs. The exception is the **score** role, which
  stays pinned in reviewed code.

## 2. What runs today

Collected 2026-10-22 by parsing every `messages.create` call (AST) and tracing callers. "t" is `temperature`; "—"
means not set.

| Role (proposed) | Function (file:line) | Model today | max_tokens | t | Runs in | What the output feeds |
|---|---|---|---|---|---|---|
| **score** | `news_intelligence.rescore_headlines_llm` (`:544`), called from `bundle_loader` `:318` | Haiku 4.5 | 512 | 0 | app + every cron lane | **The sentiment pillar (10% of the composite)**, cached per ticker per UTC day in `sentiment_llm_cache` |
| filter | `news_intelligence.llm_score_batch` (`:326`) via `rescore_brief_news` (`app.py` ~5064) | Haiku 4.5 | 64 + 32×n | 0 | Home | Suppress-only filter on Act Today / Review news cards (F-308) |
| filter | `news_intelligence.rescore_news_items_llm` (`:237`, `app.py` ~24729) | Haiku 4.5 | 512 | 0 | app | Suppress-only rescore of the sidebar news list |
| extract | `analyst_intel.extract_report` (`:105`) | Sonnet 4.6 | 8000 (`ANALYST_EXTRACT_MAX_TOKENS`) | — | app | Analyst research → editable preview → `analyst_coverage` (since F-311, the Valuation price-target fallback) |
| extract | `earnings_intel.extract_playbook` (`:81`); `extract_results` (`:148`, no caller) | Sonnet 4.6 | 8000 | — | app | Earnings Playbook data |
| extract | `premortem_monitor.extract_trigger` (`:137`) | Haiku 4.5 | 200 | 0 | app | The Pre-Mortem price trigger, which is monitored and is also the R-Multiple "declared stop" |
| extract | `broker_screenshot._resolve_unknown_tickers` (`:266`), model passed from `app.py` ~32936 | **Opus 4.8** | 512 | — | app | Company name → ticker during trade import |
| fast | `premarket_stance.generate_stance` (`:203`), model hardcoded at `app.py` ~8232 | Haiku 4.5 | 500 | — | Home | Pre-market stance card |
| fast | `premortem_advisor.generate_case_against` (`:302`) | Haiku 4.5 | 700 | 0 | app | Pre-Mortem counter-case |
| fast | `thesis_red_team.generate_counter_evidence` (`:258`) | Haiku 4.5 | 600 | 0 | app | Red Team |
| fast | `debate_agent._call_haiku` (`:51`), 5 calls per debate | Haiku 4.5 | 200 / 300 | 0 | app | Multi-agent debate + Judge |
| fast | `regime_stress` (`:196`), `catalyst_stress` (`:290`), `structural_scanner` (`:256`) | Haiku 4.5 | 400 / 350 / 300 | 0.3 | app | Stress and structural narratives |
| fast | `thesis_cluster` (`:162`), `missed_opportunity` (`:195`) | Haiku 4.5 | 600 / 600 | 0.2 | app | Pattern narratives |
| fast | `portfolio_qa.parse_question` (`:342`), `narrate_answer` (`:1105`) | Haiku 4.5 (`_MODEL`) | 300 / 400 | 0 | app | 💬 Ask: parse the question, narrate an answer computed by code |
| deep | `thesis_advisor.review_thesis` (`:221`), `draft_thesis` (`:485`), `generate_earnings_thesis_update` (`:896`) | Sonnet 4.6 | 300 | — | app + weekly cron | Thesis review (persisted with its `model`), drafting at BUY |
| deep | `debrief_advisor.generate_debrief` (`:856`), `intelligence_report.generate_report` (`:411`) | Sonnet 4.6 | 1500 / 1000 | — | app + weekly / monthly cron | Weekly debrief, monthly report |
| user-picked | `ai_provider.call_llm` (`:145`) — 🔎 Investigator | Sonnet 4.6 / Opus 5 (`investigator.EVAL_PASSED_MODELS`) | 500 / 4000 | — | app | Investigator plan + report |
| user-picked | `_call_ai_brief` (`app.py` ~12601) — AI Snapshot | Haiku 4.5 / Sonnet 4.6 (its own inline menu) | 700 | — | app | AI Snapshot |

**Structural problems, separate from the model choice:**
1. **Model IDs in about 30 places**, plus **two provider menus** that disagree: `ai_provider.AI_PROVIDERS` and the copy
   inside AI Snapshot (`app.py` ~12483).
2. **25 call sites build their own Anthropic client** and read only `content[0].text` (24 in `stock_analyzer/`,
   plus AI Snapshot in `app.py`). Only `ai_provider.call_llm` reads by block type and reports a reply cut off by
   `max_tokens`.
3. **The key is read in 20 places, in 5 different orders** (secrets only; secrets → flat key → env; env only in the
   cron; env → secrets in `bundle_loader`; secrets → env in `ai_provider.resolve_key`), plus a key typed into AI
   Snapshot's page.
4. **The model that feeds the composite has no review gate and no record.** `news_intelligence.py` is not in
   `_GATE_FILES`, so changing the sentiment model needs no review. `sentiment_llm_cache` doesn't record which model
   wrote a row, so a later reader can't tell a Haiku 4.5 score from a Haiku 5.5 one (Definition of Done #8).

## 3. What changes on the 5.5 models

From the migration guide (`shared/model-migration.md`, cached 2026-10-06):

| Change | Haiku 5.5 | Sonnet 5.5 | Opus 5.5 | DRISHTA code affected |
|---|---|---|---|---|
| `temperature` other than the default | **error** (`temperature` must be 1 or omitted) | **error** | **error** | 14 call sites (0, 0.2, 0.3) |
| Thinking | on by default; `{"type": "disabled"}` allowed at effort low / medium / high | on by default; `disabled` is an **error**; `{"type": "between_tools"}` turns it off at effort ≤ high | **always on**; effort is the only control | 25 sites read only `content[0]` |
| Thinking counts toward `max_tokens` | yes | yes | yes | limits of 64–700 tokens |
| Tokenizer | ~30% more tokens than Haiku 4.5 | re-measure | same as Opus 4.8 | the same limits, plus cost estimates |
| Default effort | `medium` | `high` | `medium` | none set today |
| Assistant prefill | error | error | error | none found (checked 2026-10-22) |
| Refusal (`stop_reason: "refusal"`) | possible; no server-side fallback | possible | possible | never checked today |

Losing `temperature=0` matters most for the score role: the same headlines could get a slightly different score on a
second call. Two things limit that. The per-ticker, per-UTC-day cache (`sentiment_llm_cache`) already makes the app
and every cron lane use one score per ticker per day. Structured outputs (`output_config.format`) can fix the JSON
shape. The remaining run-to-run variation must be **measured** before switching (Phase 3).

## 4. Target models and cost

| Role | Today | Proposed | Per-token change | Request settings |
|---|---|---|---|---|
| score, filter | Haiku 4.5 | **Haiku 5.5** | input $1 → $0.10, output $5 → $0.50 | thinking `disabled`, effort `low`, structured JSON |
| fast | Haiku 4.5 | **Haiku 5.5** | same | effort `low`, thinking adaptive or disabled (decision D-4) |
| deep | Sonnet 4.6 | **Sonnet 5.5** | $3/$15 → $2/$10 | effort `low`–`medium` |
| extract | Sonnet 4.6 / Haiku 4.5 / Opus 4.8 | **Sonnet 5.5** (trigger extraction: Haiku 5.5) | as above | structured JSON |
| broker ticker resolution | Opus 4.8 | **Opus 5.5** (or Sonnet 5.5, decision D-5) | $5/$25 → $4/$20 | effort `low` |
| Investigator | Sonnet 4.6 / Opus 5 | add Sonnet 5.5 / Opus 5.5 **only after** `scripts/investigator_eval.py` passes for each | | unchanged gate |

**The real saving is not the per-token ratio.** Haiku 5.5's tokenizer uses about 30% more tokens, and thinking adds
output tokens unless it's turned off. Haiku is the large majority of calls (news sentiment runs once per ticker per
day across every scanned name), so most of the bill should fall by several times, but that has to be **measured**.
Before Phase 2: export the last 30 days of usage by model from the Anthropic console as the baseline; repeat 2 weeks
after each switch.

## 5. Design

### 5.1 Roles, not model IDs

Each call site names a role from section 2: `score`, `filter`, `extract`, `extract_strong` (broker ticker
resolution), `fast`, `deep`. The two user-picked menus keep their pickers, but both read one registry
(`ai_provider.AI_PROVIDERS`); AI Snapshot's inline copy is deleted.

### 5.2 One call helper

A new pure module (working name `stock_analyzer/llm.py`) with one entry point, roughly
`complete(role, system, user, max_tokens, json_schema=None) -> text | None`, plus `LAST_ERROR` (the same fail-open
convention as `ai_provider.call_llm`). It:
1. looks up the key once (`resolve_anthropic_key()`: env → `st.secrets[anthropic][api_key]` → flat secret, one order
   everywhere; the cron and the app share it);
2. resolves the role to a model (5.4);
3. builds the request from the model's **profile** (5.3): sends `temperature` only where the profile accepts it,
   sets thinking and effort, adds headroom to `max_tokens` for thinking and the tokenizer;
4. reads the reply **by block type**; reports `max_tokens` and `refusal` stop reasons as a visible error, not a
   blank;
5. strips the key from any error text.

All 25 of those call sites move onto it. The app's "works without a key" rule is unchanged: no key → `None` → the
existing fallback (VADER, "INERT" cron lanes, unavailable pages).

### 5.3 Model profiles

Code needs a small table because the API's rules differ by model, and the Models API doesn't report everything
(it reports thinking types, effort, vision and structured outputs, but **not** whether `temperature` is accepted, and
not prices):

| Profile | Models | temperature | Thinking when "off" is requested | Effort |
|---|---|---|---|---|
| `legacy-sampling` | `claude-haiku-4-5*`, `claude-sonnet-4-6` | accepted | omit `thinking` (off by default) | not sent |
| `opus-4x` | `claude-opus-4-8` | not sent | omit (off by default) | supported |
| `modern` (**the default for any model not in the table**) | 5.x, including Haiku / Sonnet / Opus 5.5 | **never sent** | Haiku 5.5: `disabled`; Sonnet 5.5: `between_tools`; Opus 5.5: none, lower effort instead | always sent |

**A model released next year should need no code change:** an unknown ID gets the `modern` profile, which follows the
direction every 2026 model has taken. Code changes only when a new model breaks that pattern. Optional: read the
Models API `capabilities` at start-up to confirm a model supports adaptive thinking or a given effort level.

### 5.4 Where the model choice lives

The model for each role is resolved in this order, first match wins:

1. **Environment variable** `DRISHTA_LLM_MODEL_<ROLE>` (e.g. `DRISHTA_LLM_MODEL_FAST`). An emergency override, for
   when the database setting is wrong and the app can't be reached to fix it. Needs a redeploy.
2. **⚙️ App Settings → "AI models"**, stored as one `reference_tables` row (`name = 'llm_models'`), payload
   `{role: {"model": "...", "effort": "..."}}`. Owner-only, no redeploy: the app picks it up within a few minutes and
   each cron run reads it fresh at start. `reference_table_history` already records who changed what and when, which
   gives every model change a dated record.
3. **Code default** in the registry module: the reviewed, known-good choice, used when nothing else is set or the
   setting can't be read.

**The score role is not editable in App Settings.** It changes the composite, so per Hard Rule #1's intent it stays
in reviewed code: a change is a commit with an Opus review and a dated regime note (Definition of Done #8). The new
registry module joins `_GATE_FILES`. The App Settings screen shows the score model read-only, with the reason.

**Two existing-code facts this design must handle:**
- `db.save_reference_table` always runs `reference_data.canonicalize`, which **upper-cases every value and expects
  lists of tickers**. Saving model IDs through it as-is would corrupt them. The save path needs a per-table
  canonicalizer (and `validate_payload` a branch for `llm_models`). `db.py` is a gate file, so this needs a review.
- App Settings' existing tables deliberately have **no code fallback** (a stale ticker list caused the 2026-07-14
  INTC incident). Here a code fallback is correct: the LLM is optional by principle, and the fallback is a reviewed,
  known-good model, not stale data.

**Validation on save:** a model ID is accepted only if (a) it is in the profile table or the owner confirms using the
`modern` profile, and (b) the Models API shows it as available to the current key. A mistyped ID is rejected when
saved, not discovered later as blank pages.

### 5.5 Record and observability

- **`sentiment_llm_cache.model`**: a new nullable column (owner-applied DDL), written by the score path, so every
  stored score names the model that produced it. Old rows stay `NULL` = before this change (Haiku 4.5).
- **🩺 System Trust**: a new check listing each role → model, whether it is available to the key (Models API), and
  each role's last error. "Not checked" is shown as unknown, never as green.
- Thesis reviews and Pre-Mortem records already store `model`; they keep doing so.

## 6. Phases

| Phase | What | Changes behaviour? | Review |
|---|---|---|---|
| **1** | `llm.py` helper, profiles, single key lookup; move all 26 call sites onto it **with today's models**. AI Snapshot reads `ai_provider`. A ratchet test fails on any `claude-` ID or `anthropic.Anthropic(` outside the helper. | No: for each call site, a test checks the request matches today's (model, max_tokens, temperature) | **Required** (touches `bundle_loader`, `cron_runner`; adds a gate file) |
| **2** | `llm_models` settings row, App Settings screen (read-only score row), save-path canonicalizer, System Trust check | No (defaults = today's models) | **Required** (`db.py`) |
| **3** | Switch `fast`, `deep`, `extract` and `filter` to the 5.5 models from App Settings; owner-run live check per role (like the key checks); watch errors in System Trust for a week; compare console usage | Narrative wording only; filter is suppress-only | Light (config change, no code) |
| **4** | Score role → Haiku 5.5: offline dry run on real headlines (old vs new model, run-to-run spread, how many composites cross `COMPOSITE_BUY` / `COMPOSITE_STRONG_BUY`), then a reviewed commit, the DDL column, a dated regime note | **Yes, the composite** | **Required** + planner |
| **5** | Investigator: run the eval for Sonnet 5.5 / Opus 5.5; add the ones that pass to `EVAL_PASSED_MODELS` | Adds menu choices | Light |

Phases 1–2 can be written and tested while Railway is paused. They can't be checked on the live site until it's back
(Hard Rule #3).

## 7. Decisions for the owner

- **D-1 — Where model choices live.** Lead recommendation: App Settings (database) with the score role pinned in
  code (5.4). Alternative: Railway environment variables only, which is simpler (no `db.py` change) but needs a
  redeploy of 8 services for each change and keeps no change history.
- **D-2 — Score role: switch at all?** Lead recommendation: yes, but last, after Phase 4's dry run. The saving on
  this one call is real, but it is the only change here that moves the composite.
- **D-3 — Deep role target.** Sonnet 5.5 (recommended, cheaper and newer) or Opus 5.5 ($4/$20, about 2× Sonnet) for
  the weekly thesis review and debrief.
- **D-4 — Thinking on the fast role.** Off (cheapest, closest to today) or adaptive at `low` effort (may improve
  narratives, costs more output tokens). Lead recommendation: off for `score` / `filter` / `extract`; adaptive `low`
  for `fast` / `deep`, decided from the Phase 3 live checks.
- **D-5 — Broker ticker resolution.** Opus 5.5 (closest to today) or Sonnet 5.5 (half the price). It affects which
  ticker a trade is imported under, so lead recommendation: Opus 5.5.
- **D-6 — Other providers.** OpenAI / Gemini / Groq stay as options in the Investigator and AI Snapshot only.
  Recommendation: leave them as they are; this plan is about the Anthropic calls.

## 8. Risks

| Risk | How the design handles it |
|---|---|
| A new model returns a blank on some route (thinking ate `max_tokens`, refusal) | The helper reads by block type, adds headroom and reports the stop reason; the System Trust check shows the last error per role |
| A mistyped model in App Settings | Validation on save against the Models API; code default if the row can't be read |
| The score changes without anyone noticing | Score role pinned in a gated file; DDL `model` column; dated regime note |
| Phase 1 "behaviour-neutral" quietly isn't | Per-call-site request equality tests against today's values |
| Cost goes up, not down (thinking tokens) | Effort set explicitly per role; console usage compared before and after each phase |
| A model is retired | It shows as unavailable in System Trust; change it in App Settings, no deploy |

## 9. Out of scope

Prompt rewrites; streaming; the Batch API and prompt caching (later cost options, worth a separate look once the
usage baseline exists); the non-Anthropic providers' own model lists; Claude Code's `.claude/agents` model pins
(they control the development agents, not the app).

## 10. Planner review (Opus 5.5, 2026-10-22): PROCEED WITH CHANGES

The reviewer checked section 2 against the code and confirmed the 26 call sites (none missing). It corrected two
counts, now fixed above: 14 `temperature` sites, not 10 (the lead's count came from a search whose output was capped
at 10 lines), and 5 key-lookup orders, not 4. The lead re-verified both and the Brief news flow below.

**Two roles were misclassified. Both decide what the user sees, so both should be pinned in reviewed code like
`score`. This narrows D-1 (App Settings stays, for fewer roles) and needs owner OK:**
- **filter.** `rescore_brief_news` output → `_brief_held_news` (`app.py` ~5092) → `build_daily_briefing(news_items=…)`,
  which feeds Act Today, Review, Grow Today's add checks and G-30 rows, and the Rebalancer's news flags. Suppress-only
  means a too-generous model *removes* protective cards and *releases* held adds: the gate-loosening direction.
  `rescore_news_items_llm`'s `_sidebar_news` also feeds 📡 News Intelligence and the Rebalancer fallback. Switch it in
  Phase 4 with the score role, with a dry run counting how many cards and G-30 rows change.
- **Pre-Mortem trigger extraction** gets its own pinned role (`extract_trigger`). It runs inside the BUY handler
  (`app.py` ~31515) and is saved to `trades` without the owner confirming it. It drives the Act Today Pre-Mortem cards
  and the permanent R-Multiple declared stop, and it is never re-extracted.

**Other required changes (blocking):**
1. Pinned roles **ignore the `DRISHTA_LLM_MODEL_<ROLE>` override** too, or one Railway variable gives a process an
   unreviewed score model.
2. The helper returns a **result object** (text, stop_reason, error, model, usage, truncated), not text or `None` plus a
   module global (a global is shared across threads and sessions, and can't see the cron). Rules: `refusal` → never any
   text; `max_tokens` → no result for score / filter / extract roles; narrative roles keep today's behaviour of using
   cut-off text, flagged `truncated` (debate rounds rely on this today); any unknown stop reason → error for JSON roles.
3. **Phase 1 equality tests cover every argument**, of both `anthropic.Anthropic(...)` and `messages.create`, per site:
   timeout and where it's set (the news sites use a client-level 8-second timeout with `max_retries=0`;
   premarket_stance and AI Snapshot set none), retries, system, messages, whether `temperature` is present, and that
   no thinking / effort / `output_config` / `max_tokens` headroom is sent on legacy profiles. AI Snapshot keeps its own
   model menu in Phase 1 (pointing it at `ai_provider` would add Opus 5 to its menu, a visible change).
4. **System Trust error reporting is honest:** either the cron saves per-role errors and counts (e.g. in its
   heartbeat), or roles that run only in the cron show "unknown from here". Never green because this process saw no
   error.
5. **The score model switches by `score_date`, not by deploy:** a dated schedule in reviewed code (e.g.
   `[(effective_date, model)]`), so every process switches at the same UTC-day boundary and the regime note has an
   exact date. Apply and confirm the `model` DDL **before** any code reads or writes that column, since PostgREST
   rejects the whole upsert or select otherwise. On read, a cache row whose model doesn't match the schedule is a miss.
6. **Key resolver:** accepts an explicit `api_key` (AI Snapshot's typed key, the Investigator's chosen key); has no
   Streamlit import (a secrets getter is passed in, like `ai_provider`); survives the cron, where there is no
   `secrets.toml` and `st.secrets` raises.
7. **Request shapes come from the Models API, stored at save time.** "Unknown model → modern profile" is not one
   behaviour ("off" is `disabled` on Haiku 5.5, `between_tools` on Sonnet 5.5, effort-only on Opus 5.5). So at save time
   the request shape is read from the Models API `capabilities` and stored with the setting. If the API can't be
   reached, the save is refused unless the model is in the code table. The stored row is re-validated at runtime, since
   rows have been edited directly in SQL before, with a per-role fallback to the code default and a System Trust
   warning. No guessed default for pinned roles.

**Suggestions (non-blocking), adopted unless the owner objects:** raise the SDK floor (`requirements.txt`
`anthropic>=0.40.0`; the venv has 0.121.0); split a pure registry and request builder (the gate file) from a thin
I/O layer; in the Phase 4 dry run also measure the 8-second / no-retry failure rate, incomplete batch arrays (missing
indexes keep VADER but the row is saved as an LLM row, `news_intelligence.py` ~558-587) and `True` accepted as 1.0
(~564); check that structured outputs accept a top-level array; record `model` on the persisted debate,
structural-scan, regime, catalyst and `analyst_coverage` rows; add an owner-only "Test role" button in App Settings
that shows the stop reason and usage before saving; record usage per role; make the ratchet test match string
literals only, allowlisting `investigator.EVAL_PASSED_MODELS`; coordinate the System Trust check number with
`docs/plans/app-trust-verification.md` (which proposes ⑧); same-commit docs (CLAUDE.md `_GATE_FILES` list,
requirements.md F-ID, architecture.md constants, User Guide). Pre-existing, noted for later:
`broker_screenshot._resolve_unknown_tickers` marks every non-empty LLM ticker "high" confidence (~280-285).

**Revised phases:** Phase 1 and 2 unchanged in scope, with the changes above. Phase 3 switches `fast`, `deep` and
`extract` (not `extract_trigger`) from App Settings. Phase 4 switches `score`, `filter` and `extract_trigger`, each
with a dry run and a reviewed commit.

## 11. Phase 1 build spec (lead, 2026-10-22)

Phase 1 changes **no** model and no request. It moves the calls onto one helper, proven by recorded requests. Five
commits:

| Chunk | What | Review |
|---|---|---|
| **P1-a** | `tests/test_llm_request_golden.py` + `tests/fixtures/llm_requests_golden.json`: records every `messages.create` in `stock_analyzer/` today (client arguments; model, `max_tokens`, `temperature` present or absent, timeout, system, messages), plus an AST guard so a new call without a snapshot fails. AI Snapshot (`app.py`) can't be imported and is not covered. | Not needed (tests only) |
| **P1-b** | `stock_analyzer/llm.py`, described below. Not called by anything yet. Added to `_GATE_FILES` (and to CLAUDE.md's list in the same commit). | **Required** |
| **P1-c** | Narrative and extraction modules call `llm.complete`; golden tests unchanged | Recommended |
| **P1-d** | `news_intelligence` (score, filter); key lookup in `bundle_loader` and `cron_runner` | **Required** |
| **P1-e** | `ai_provider`'s Claude branch and AI Snapshot use the helper (AI Snapshot keeps its own menu); ratchet test (string literals only, allowlisting `investigator.EVAL_PASSED_MODELS`) | Required if a gate file is touched |

### `stock_analyzer/llm.py` (P1-b)

Pure apart from the SDK call; no Streamlit import; no module-level mutable state.

- **Roles:** `fast`, `deep`, `extract`, `extract_strong`, `extract_trigger`, `filter`, `score`. `PINNED_ROLES =
  {score, filter, extract_trigger}`. `JSON_ROLES` = every role except `fast` and `deep`.
- **Code defaults** (equal to today's models, so Phase 1 changes nothing): fast, filter, score and extract_trigger →
  `claude-haiku-4-5-20251001`; deep and extract → `claude-sonnet-4-6`; extract_strong → `claude-opus-4-8`. Phase 1
  resolves role → code default only. An explicit `model=` argument wins (Investigator, AI Snapshot, tests). The env
  override and App Settings arrive in Phase 2, and pinned roles ignore both.
- **Profiles:** `legacy-sampling` (Haiku 4.5, Sonnet 4.6), `opus-4x` (Opus 4.8), `modern` (everything else).
  `build_request(profile, role, ...)` is a pure function:
  - Legacy profiles forward exactly what the caller passed. `temperature` only if given, and never thinking, effort,
    `output_config` or `max_tokens` headroom. This is the Phase 1 path.
  - `modern` drops `temperature` and sets effort `low`. Thinking is off for JSON roles: Haiku 5.5 `{"type":
    "disabled"}`; Sonnet 5.5 `{"type": "between_tools"}`; Opus 5.5 omits `thinking`. Narrative roles use adaptive
    thinking (omit `thinking`) with a named thinking-headroom constant added to `max_tokens`. Built and unit-tested
    now, used from Phase 3.
- **`complete(role, *, api_key, system, messages, max_tokens, model=None, temperature=None, timeout=None,
  client_kwargs=None) -> LLMResult`.** It constructs `anthropic.Anthropic(api_key=api_key, **client_kwargs)` and
  forwards `timeout` per request exactly as the caller passes it, so each site keeps its own timeout and retry
  settings. It never raises.
- **`LLMResult`** (frozen dataclass): `text`, `stop_reason`, `error`, `model`, `usage`, `truncated`. Rules:
  - `text` is the first `type == "text"` block's text. Any thinking block that comes first is skipped.
  - `refusal` → `text = None`, with an error naming the category.
  - `max_tokens` → narrative roles keep the text, with `truncated = True` (today's behaviour). JSON roles get
    `text = None`. This one change is deliberate: today a cut-off JSON reply reaches the parser, which almost always
    fails on it.
  - Any other unknown stop reason is an error for JSON roles.
  - Exceptions become `error`, with the key stripped from the text.
- **`resolve_anthropic_key(secrets_getter=None, environ=os.environ) -> str`:** env `ANTHROPIC_API_KEY`, then
  `[anthropic] api_key`, then a flat `ANTHROPIC_API_KEY` secret. The getter is passed in and any exception from it
  (no `secrets.toml` in the cron) counts as "not set". Used by callers from P1-d onward.
- **Tests:** builder per profile and role (no temperature on `modern`; the right "off" form per family; Haiku 5.5
  `disabled` only with effort low / medium / high); text found after a leading thinking block; refusal with text →
  `None`; `max_tokens` behaviour per role kind; key resolver with a raising getter; an explicit `model=` beats the
  role default; no module-level state (two concurrent results don't interfere).

## 12. Phase 1 progress

- **P1-a** (`8e12011`): golden snapshot of every `messages.create` request in `stock_analyzer/`.
- **P1-b** (`74234dc`): `stock_analyzer/llm.py`, in `_GATE_FILES`. Opus review FIX-FIRST/2, then SHIP.
- **P1-c** (2026-10-22): 21 call sites in 17 modules call `llm.complete`; golden fixture unchanged. Built by
  `implementer` (Sonnet 5.5); Opus review SHIP, 0 blocking. Roles: `extract` (analyst and earnings extraction, the
  debate judge), `extract_strong` (broker ticker lookup), `extract_trigger` (Pre-Mortem trigger), `deep` (thesis x3,
  weekly debrief, monthly report), `fast` (the rest, including the four debate rounds). Every site still passes its own
  `model=`, so the role decides only how the reply is read. Deliberate changes, all on unusual replies: a JSON-role
  reply cut off at `max_tokens` fails before parsing; a refusal fails everywhere; a reply with **no text block** now
  returns the failure value. Before, the thesis review and earnings update parsed `""` into the parser's default
  WEAKENING verdict, which the weekly cron saved, and the monthly report saved empty sections.

- **P1-d** (2026-10-22): `news_intelligence`'s three calls use `llm.complete` (`score` for `rescore_headlines_llm`,
  `filter` for the other two; pinned, so no `model=`; the client keeps `max_retries=0` and the 8-second
  `anthropic.Timeout` via `client_kwargs`). Golden fixture unchanged. Built by `implementer` (Sonnet 5.5); Opus review
  SHIP, 0 blocking. Key lookups:
  - **The three cron lanes** call `llm.resolve_anthropic_key()`: env only, the same as before.
  - **`bundle_loader`** calls it with `_anthropic_section_secret`. **Deliberately narrower than section 11's resolver
    order:** env, then `[anthropic] api_key`, and **never the flat `ANTHROPIC_API_KEY` secret.** `bundle_loader` never
    read the flat secret, and reading it would switch on LLM news scoring (which moves the composite) on a deploy
    whose secrets carry only the flat key, e.g. the Streamlit Cloud fallback, whose secret shape is unknown. A test pins
    the call site to this getter. Widening it is a separate, reviewed decision.
  - **Railway check (owner, 2026-10-22):** one `ANTHROPIC_API_KEY` variable on the web service; `railway_start.sh`
    writes `[anthropic] api_key` from it at every boot, so env and secrets always match there.
  - **Cut-off-reply counting on the score role is deferred to Phase 2** (section 10 item 4). The reviewer confirmed
    that every path into `rescore_headlines_llm` is capped at 10 headlines (`sentiment.py` `news_items[:10]` and
    `NEWS_KEEP_PER_TICKER`), about 150 tokens against a 512 cap, and a cut-off reply already fell back to VADER.

**Carried forward from the P1-c review:**
- **P1-e:** pin each site's role in a test. The golden recorder should also record the role, or an AST test should map
  site → role. Changing the Pre-Mortem trigger, analyst extraction or broker lookup to `fast` currently fails no test,
  and from Phase 3 the role picks the model.
- **P1-e (or earlier, in `llm.py`):** an **empty-string or whitespace-only** text block still counts as a reply
  (`LLMResult.ok`), so the thesis review would still save a WEAKENING verdict with an empty summary for it (unchanged
  from before P1-c). Decide whether `parse_response` adopts ai_provider's stricter "empty or whitespace = no reply"
  rule for every role. `llm.py` is a gate file, so this needs review.
- **Phase 3:** these sites ask for JSON but are mapped to `fast` and would get narrative treatment (adaptive thinking,
  extra headroom, cut-off text kept): `portfolio_qa.parse_question`, `premortem_advisor.generate_case_against`,
  `thesis_red_team`, `missed_opportunity`, `thesis_cluster`, `catalyst_stress`, `regime_stress`. Decide before
  switching `fast` to a 5.5 model.
- **Pre-existing, noted:** the monthly report cron lane logs "skip" and stays green when `generate_report` returns
  `None` (`cron_runner.py` ~2819). More failure kinds now reach that path. The weekly debrief lane already goes red in
  the same case.

**Carried forward from the P1-d review:**
- **P1-e:** `app.py` ~24724, the sidebar's filter-role call, still reads `st.secrets` directly, and its 512-token cap is
  reachable with about 30+ items (display-only, suppress-only, pre-existing).
- **Cosmetic:** `bundle_loader.py` ~345 comment says "env first (Railway cron lanes)"; the Railway web service sets
  the variable too. Fix in the next reviewed `bundle_loader` commit.
