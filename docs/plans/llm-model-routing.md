# LLM model routing: move to current models, and change models without code changes

**Status (2026-10-25): Phase 2 READY TO BUILD. Designed (section 13); planner review PROCEED WITH CHANGES (section 14); owner accepted every recommended safeguard (B3, B4a, B8), declined the optional 404 retry. Nothing built. NEXT: P2-a1.** *(2026-10-22: Phase 1 COMPLETE. P1-a to P1-f built and reviewed (section 12).)* *(Earlier status: READY TO BUILD (Phase 1), 2026-10-22.)* Decisions ratified; planner (Opus 5.5) review: PROCEED WITH CHANGES (section 10). **Owner accepted every section-10 change the same day, including narrowing D-1:** the `filter` and `extract_trigger` roles are pinned in reviewed code alongside `score`, and App Settings chooses models for `fast`, `deep` and `extract` only. Analysis by the
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

- **P1-e** (2026-10-22): ai_provider's Claude branch (`deep`, user-picked model, stricter empty-text rule kept) and
  AI Snapshot's (`fast`, its own menu kept) call `llm.complete`. Every `model=` default and `portfolio_qa._MODEL` is
  gone: `None` resolves to the role default (equal to the old literal); saved/returned model fields use `r.model`;
  `run_debate` resolves once so the judge stays on Haiku. `_pms_model` and the broker import resolve through roles.
  The golden fixture gained one entry (Investigator on `claude-opus-5`); every other entry is unchanged. Lead fix:
  `LLMResult.status_code`, so AI Snapshot's 529/429/401/403 hints survive. New guards: `tests/test_llm_roles.py` (role
  per site, plus the debate call sites) and `tests/test_llm_ratchet.py` (no `claude-…` ids outside `llm.py` and the
  three menus; no Anthropic client or `messages.create` outside `llm.py`; `llm` import form). Built by `implementer`
  (Sonnet 5.5); Opus review SHIP, 0 blocking.

**Carried forward from the P1-e review (and still open from earlier):**
- **Next reviewed `llm.py` change:** should `parse_response` treat empty or whitespace-only text as no reply for every
  role? Today a blank text block counts as a reply, so the thesis review could still save a WEAKENING verdict with an
  empty summary.
- **Next reviewed `ai_provider` change:** a refusal gets the "likely truncated by max_tokens" message; give it its own
  wording plus a test. Also: an empty first text block followed by a real one now returns None (the old loop took the
  first non-empty block). Rare.
- **Cosmetic:** `portfolio_qa.py` ~38 has a run of blank lines where `_MODEL` was.
- **Phase 3:** add the Investigator's plan stage (JSON, on `deep`) to the list of JSON sites on narrative roles.

- **P1-f** (2026-10-22, owner-approved): app.py's 16 Anthropic-specific key reads go through three helpers
  (`_anthropic_secret`, `_anthropic_key`, `_anthropic_key_section_only`). 13 narrative/extraction reads use the full
  order (env → `[anthropic] api_key` → flat secret). Before, 11 read only the section and 2 read section → flat → env.
  The Home brief news filter, the sidebar news filter and the Pre-Mortem trigger at BUY keep **exactly** their old read:
  `[anthropic] api_key` only, **no env**, no flat secret. Two reasons: widening the filter reads could switch the
  filter on, and the filter removes protective cards; the trigger is a pinned role saved as the declared stop. A missing
  secrets file now counts as "no key" instead of raising. Guard: `tests/test_app_anthropic_key_reads.py`. Built by
  `implementer` (Sonnet 5.5); Opus review SHIP, 0 blocking (6/6 mutations caught). The multi-provider pickers (AI
  Snapshot, the Investigator's provider config) still read `secrets_path` / `env_var` themselves, deliberately.

**Notes from the P1-f review:**
- **Not identical to `bundle_loader`:** `bundle_loader` reads env first, then the section, never the flat secret. The
  app's two filter reads read the section only, never env. On a deploy with only the env var (e.g. Streamlit Cloud,
  which exports a root-level secret as env), LLM sentiment scoring turns on while the app's news filter stays off. That
  gap predates P1-f. Don't "align" either side without a reviewed decision.
- **The Pre-Mortem trigger:** keeping it section-only is "no behaviour change". But widening it would *add* protective
  monitoring rather than remove cards. On an env-only deploy the BUY flow shows the AI case-against (full order) yet
  saves no declared stop. Revisit with the Phase 4 pinned-role switch.
- **Cosmetic:** the helpers sit inside app.py's import block (~414-438); move them after it in a later app.py edit.

## 13. Phase 2 design (lead, 2026-10-25)

**Owner's goal (2026-10-25):** when a faster or cheaper model ships, moving a role onto it is a settings change, not
a code change. The owner chose Phase 2 for that reason, ahead of the section-1 condition (the 30-day console usage
export). That export is still wanted as the cost baseline for Phase 3. Phase 2 itself changes no model: every
default stays equal to today's.

**Owner decisions (2026-10-25):**
- **D2-1:** `extract_strong` (broker ticker resolution) stays code-only. It's neither pinned nor editable in App
  Settings until it's checked that the import preview lets the owner catch a wrong ticker.
- **D2-2:** for Sonnet 5.5 and Opus 5.5 requests, turn on the API's server-side refusal fallback (beta `fallbacks`).
  Haiku 5.5 has none (a `fallbacks` list is a 400 there; `"default"` is a no-op).
- **D2-3:** usage per role is tracked in process only (System Trust). Cost comes from the console export. No new
  table.
- **D2-4:** planner review before any build.

### 13.1 Facts found while designing (2026-10-25, verified at HEAD `47faf75`)

1. **JSON is decided by role, and 8 JSON-reading sites are on narrative roles.** `llm.parse_response` and
   `llm.build_request` treat `fast` / `deep` as narrative (cut-off text kept, adaptive thinking plus headroom on
   `modern`). These sites parse JSON but are on `fast`: `portfolio_qa.parse_question`,
   `premortem_advisor.generate_case_against`, `thesis_red_team`, `missed_opportunity`, `thesis_cluster`,
   `catalyst_stress`, `regime_stress`. The Investigator plan stage is on `deep` (section 12). Making `fast` / `deep`
   editable without fixing this would let one App Settings change give those sites the narrative treatment.
   **Phase 2 has to fix this before the roles become editable.**
2. **The Models API doesn't say how to turn thinking off.** `client.models.retrieve(id).capabilities` reports
   `thinking.types.{adaptive,enabled}`, `effort.{low..max}`, `structured_outputs` and `image_input`, plus
   `max_input_tokens` / `max_tokens`. It doesn't report the "thinking off" form (`disabled` on Haiku 5.5,
   `between_tools` on Sonnet 5.5, none on Opus 5.5), whether `temperature` is accepted, or prices.
3. **API version `2026-10-21` rejects `temperature` / `top_p` / `top_k` on every model, even at default values.**
   Python SDK 2.0.0 sends it by default. `requirements.txt` pins `anthropic>=0.40.0,<1.0` (venv 0.121.0), so the app
   still sends `2023-06-01`: nothing is broken today. But the legacy profile forwards `temperature` from 14 sites, so
   **the SDK can't be upgraded past `<1.0` while any role runs on a legacy-profile model.** Phase 3 lifts this for the
   editable roles and Phase 4 for the pinned ones.
4. **No new table.** The setting is one `reference_tables` row (`name = 'llm_models'`), and `reference_table_history`
   gives the dated audit trail. But `db.save_reference_table` always runs `reference_data.canonicalize`, which
   upper-cases values and expects `{bucket: [tickers]}`. Model IDs saved through it as-is would be corrupted. It needs a
   canonicalizer per table (`db.py` is a gate file).
5. **System Trust ⑧ is reserved** for the cross-page consistency check (`docs/plans/app-trust-verification.md` item 3).
   The existing "🤖 Portfolio Investigator — AI provider" section (app.py, System Trust page) becomes "🤖 AI models".
6. **The `extract` role reaches decisions.** `analyst_intel.extract_report` feeds `analyst_coverage`, which since
   F-311 is the Valuation price-target fallback, so it reaches the composite. The owner confirms an editable preview
   before saving. It stays editable (owner decision, section 10); the review should weigh this.

### 13.2 Design

**A. JSON is a property of the call, not the role (`llm.py`).** `complete(..., expects_json: bool | None = None)`.
`None` keeps today's role-derived value (so nothing changes until a site opts in). The 8 sites in 13.1 #1 pass
`expects_json=True`. `parse_response` and `build_request` read the per-call value. A test pins `expects_json=True` at
each of the 8 sites (extend `tests/test_llm_roles.py`). In the same change, adopt ai_provider's rule that empty or
whitespace-only text is no reply, for every role (open since P1-c).

**B. Model resolution order (`llm.py`, pure).** `resolve_model(role, model=None, *, setting=None, env=None)`:
1. pinned roles (`score`, `filter`, `extract_trigger`) and `extract_strong` (D2-1) → `CODE_DEFAULTS`, ignoring env
   and the setting (an explicit `model=` that differs still raises for pinned roles, as today);
2. an explicit non-empty `model=` (Investigator, AI Snapshot, tests);
3. env `DRISHTA_LLM_MODEL_<ROLE>`;
4. the validated App Settings entry for the role;
5. `CODE_DEFAULTS`.

It returns the model **and its source** (`code` / `setting` / `env` / `explicit`) so the UI and System Trust can show
where it came from. `EDITABLE_ROLES = {fast, deep, extract}`.

**C. Request shape for a model the code doesn't know (`llm.py`, pure).** Today an unknown ID gets `modern`, which
guesses the thinking-off form from a substring (`"haiku"` / `"sonnet"`). Replace that with two profiles:
- **`known-modern`:** the code table of families whose thinking-off form is known (Haiku 5.5 `disabled`, Sonnet 5.5
  `between_tools`, Opus 5.5 none). Today's `modern` rules apply, keyed by an exact ID, not a substring.
- **`generic`** (any other ID): no sampling parameters; **no `thinking` field** (the model's own default, adaptive on
  every 5.x model); `output_config.effort` only when the stored capabilities list that level (otherwise left out);
  thinking headroom on `max_tokens` for every role, because thinking may be on even for JSON roles. It never sends a
  field that only one family accepts.

So a new model works with no code change. Adding its row to the known table is an optional saving (thinking off for
JSON roles), not a requirement. **Capabilities come from the stored setting** (E below), never a live Models API call
on the request path.

**D. Refusal fallback (D2-2).** For known Sonnet 5.5 / Opus 5.5 requests: `client.beta.messages.create(...,
betas=["server-side-fallback-2026-07-01"], fallbacks="default")`. Not for Haiku 5.5, legacy models, `generic`, or the
Batches API.
- **Limit:** on Sonnet 5.5 it retries only `cyber` / `frontier_llm` declines (onto Sonnet 5), not `general_harms`,
  `bio` or `reasoning_extraction`. A `between_tools` request that falls back runs on Sonnet 5 with thinking
  `disabled`.
- **`LLMResult.model`** records `response.model` (the model that actually served the reply). New field
  `LLMResult.fell_back` (a `fallback` content block, or a `fallback_message` entry in `usage.iterations`).
- **The record:** persisted `model` fields (thesis reviews) record the serving model.

**E. Settings row and validation (`llm_settings.py`, new; `db.py`).**
- **Payload:** `{"version": 1, "roles": {role: {"model": str, "effort": "low"|"medium"|"high"|null,
  "capabilities": {...snapshot...}, "checked_at": ISO}}}`. Only `EDITABLE_ROLES` keys are allowed.
- **`db.save_reference_table`:** dispatches canonicalize and validate by `name`. `llm_models` gets its own
  canonicalizer (sort keys, strip, never change case) and a `validate_payload` branch. Existing tables are
  byte-for-byte unchanged (a test hashes a fixture payload before and after).
- **On save (app, owner-only):** `client.models.retrieve(id)` must succeed with the current key. Its `capabilities`
  are stored in the row. The chosen effort must be one the model lists, or none. If the Models API can't be reached,
  the save is refused unless the ID is in the known table.
- **At runtime:** each read re-validates the row (shape, roles, a model string with a `claude-` prefix, effort valid
  against the stored capabilities). Rows have been edited in SQL before. A role that fails validation falls back to
  `CODE_DEFAULTS` and records a warning for System Trust.
- **Reading it:**
  - The app uses `st.cache_data(ttl=300)` around a pure loader, and an App Settings save clears the cache.
  - Each cron lane reads it once at start.
  - The read is `db.load_reference_table('llm_models')`. `None` (the database is offline) and "no row" both mean
    code defaults, which is correct here. Section 5.4 explains why this table, unlike the ticker tables, has a code
    fallback.
- **`llm_settings` has no Streamlit import.** The app wraps the loader. It passes `setting` / `env` into `complete`
  through a thin `llm_settings.complete_for(role, **kw)`, or call sites keep calling `llm.complete` and it asks a
  resolver hook. **Open for the planner:** which of the two (a hook is module state, which the "no module-level
  mutable state in `llm.py`" rule forbids).

**F. App Settings "🤖 AI models" section (app.py, render only; decisions in `llm_settings`).**
- **One row per role:** the role, what it does, the current model and its source, and the effort.
- **Editable roles:** a model picker (the Models API `list()`, the IDs this key can use, plus free text) and an effort
  picker limited to the levels the model lists.
- **"Test role":** one tiny real call through `complete` with the candidate model. It shows the stop reason, input /
  output tokens, response time and an approximate cost (from a small code price table; a model not in the table shows
  "price unknown"). Then **Save** (history row) and **Reset to code default** (deletes the role's key).
- **Pinned roles and `extract_strong`:** shown read-only with the reason.
- **The page's warning text** ("no gate, threshold, scoring weight") stays true. Its wording adds that model choice
  for the roles that move decisions is not here.

**G. System Trust "🤖 AI models" (app.py, replaces the Investigator AI-provider section).**
- **Per role:** the model, its source, whether the key can use it (one cached `models.list()` per session; "not
  checked" shows as unknown, never green), any runtime validation warning, and calls / errors / last error / tokens
  **seen by this process** (D2-3).
- **Cron-only roles** (`deep` in the weekly lanes, `score` in every lane) show "unknown from here". The cron reporting
  per-role errors in its heartbeat `detail` (200 chars) is optional (see 13.4).
- **Counters live in `st.session_state`,** never in `llm.py`.

### 13.3 Chunks

| Chunk | What | Review |
|---|---|---|
| **P2-a** | `llm.py`: `expects_json` (A) + the 8 sites + the empty-text rule; `resolve_model` order with source (B), with no setting wired yet; `known-modern` / `generic` profiles (C); fallback request + `fell_back` (D). Golden fixture unchanged (every default is still legacy). | Required |
| **P2-b** | `llm_settings.py` (payload validation, runtime loader, save-time Models API check, price table) + `db.save_reference_table` / `reference_data` per-table dispatch + tests; wired into `complete` resolution. Behaviour-neutral until a row exists. | Required (`db.py`) |
| **P2-c** | App Settings section (F) | Required (new owner surface feeding a decision input) |
| **P2-d** | System Trust section (G) | Light |
| **P2-e** | `docs/requirements.md` F-ID, `docs/architecture.md`, User Guide, `_GATE_FILES` + `docs/review-policy.md` line for `llm_settings.py` | Not needed |

After P2-e: Phase 3 is the owner switching `fast` → Haiku 5.5, `deep` / `extract` → Sonnet 5.5 in App Settings, one
role at a time, Test role first.

### 13.4 Open questions for the planner

1. How `complete` gets the setting without module state in `llm.py` (E, last bullet).
2. Should the cron report per-role errors (heartbeat `detail`, or one heartbeat `lane = 'llm'`), or is "unknown from
   here" enough for `deep` until Phase 4?
3. Should a saved `extract` change carry a regime note, since it reaches Valuation (13.1 #6)? Or is the
   `reference_table_history` row enough?
4. Is the `generic` profile's "thinking on for JSON roles + headroom" safe for the `extract` role's 8000-token limit
   (`ANALYST_EXTRACT_MAX_TOKENS`) and its non-streaming timeout? SDK guidance: non-streaming beyond ~16k `max_tokens`
   risks HTTP timeouts.
5. Anything in the Phase 1 golden or ratchet tests that the per-call `expects_json` flag or the beta-client path would
   weaken.

## 14. Planner review of section 13 (Opus 5.5, 2026-10-25): PROCEED WITH CHANGES

The lead re-checked B1 (`app.py:8258`, `debate_agent.py:462`), B8 (`thesis_advisor._parse_response` defaults to
`WEAKENING`) and B10 (the Investigator picker is in the System Trust section) against the code.

**Corrections to 13.1:**
- **#1:** 7 of the 8 JSON sites are confirmed on `fast`. The Investigator plan stage can't be reached by a setting:
  it always passes an explicit user-picked model, and its `ai_provider` site also serves the report stage. It is out
  of P2-a.
- **Two `deep` sites are missing from #1:** `thesis_advisor.review_thesis` and `generate_earnings_thesis_update`. They
  parse a trailing `Verdict:` line, so a reply cut off at `max_tokens` is parsed as `WEAKENING`, and the weekly cron
  saves it.
- **#4:** `save_reference_table` doesn't call `validate_payload` today, so the dispatch is new behaviour and must fail
  closed. `load_reference_table` returns `None` for both "offline" and "no row".
- **#6:** `extract` also feeds `earnings_intel.extract_playbook` → `earnings_context` → `earnings_advisor`. Both
  `extract` paths go through an owner-confirmed preview.

**Answers to 13.4:**
1. **How `complete` gets the setting:**
   - `llm_settings` owns a process-level immutable snapshot: lock-protected, TTL-based, with `invalidate()` called on
     save.
   - `llm.complete` gains a `setting=` seam and consults the provider (lazy import) only for `EDITABLE_ROLES` with no
     explicit `model`, so pinned roles never trigger a DB read.
   - No `complete_for` wrapper (it would escape the AST guards) and no `st.cache_data`.
   - The Models API helpers and the known-model and price tables live in `llm.py`; the ratchet allowlist is not
     widened.
2. **Cron reporting:** no new heartbeat lane. Each lane logs the resolved model and its source; an LLM failure puts
   the role, model and error into the lane's failure detail; the monthly lane goes red when `generate_report` returns
   `None` while it has data.
3. **`extract`:** no regime note. Both of its paths are preview-gated and `reference_table_history` dates the change.
   Each preview caption shows "extracted by <served model>".
4. **`generic` on `extract`:** fails visibly rather than silently. A timeout is shown and nothing is saved. Mitigate
   with effort `low` and a clamp to `capabilities.max_tokens`.
5. **The Phase 1 guards:** the AST pin is the only guard for `expects_json`. The golden fake client has no `.beta`,
   so the fallback needs its own unit test. `resolve_model` keeps returning a string.

**Blocking changes, and the owner's decisions on them (2026-10-25):**

| # | Change | Owner |
|---|---|---|
| B1 | Premarket stance (`app.py:8258`) and `debate_agent.py:462` resolve the `fast` model early and pass it as an explicit `model=`, which bypasses the setting. Add a setting-aware `llm.resolved(role)` for both. An AST ratchet limits `resolve_model(` to `llm.py` / `llm_settings.py`. | Accepted (defect fix) |
| B2 | `expects_json` may only add JSON handling: `json_mode = role in JSON_ROLES or expects_json is True`; `False` on a JSON role raises. An AST pin covers each site. | Accepted (defect fix) |
| B3 | Editable roles' effort is limited to `{low, medium, high}` ∩ the model's capabilities, because the thinking-off forms are a 400 above `high`. | **Accepted** |
| B4a | A row whose payload doesn't match its `payload_hash` (edited in SQL) is ignored: code defaults plus a System Trust warning. | **Accepted 2026-10-25** |
| B4b | The monthly report lane goes red on `None` with data. | Accepted (defect fix) |
| B5 | The snapshot state is `ok` / `no_row` / `unreadable` / `invalid`; `unreadable` is not cached for the full TTL. | Accepted (defect fix) |
| B6 | "Test role" sends the production request (`setting=`, not `model=`) with a realistically sized prompt per role, and shows latency against the site's timeout. | Accepted |
| B7 | The 13.4 #1 answer is folded into the spec. | Accepted |
| B8 | Add `review_thesis` and `generate_earnings_thesis_update` to the opt-in, so a cut-off reply fails instead of saving `WEAKENING`. | **Accepted 2026-10-25** |
| B9 | `llm_settings.py`'s `_GATE_FILES` entry and `docs/review-policy.md` line land in P2-b, the commit that creates it. | Accepted (CLAUDE.md rule) |
| B10 | Keep the Investigator picker (it writes `_inv_provider` / `_inv_model` and the key store the Investigator reads); add the AI-models table beside it. | Accepted (defect fix) |
| opt | Retry once on the code default when a setting's model returns 404. | Not selected |

**Non-blocking, adopted unless the owner objects:**
- **N3:** `LLMResult.model` is the model that served the reply, plus a `requested_model` field. An alias may come back
  as a dated ID; note that in the commit.
- **N4:** unit-test the beta path with a fake `.beta`.
- **N5:** raise the floor to `anthropic>=0.121.0` before Phase 3.
- **N6:** known-modern is matched by `^claude-(haiku|sonnet|opus)-5-5(-\d{8})?$`.
- **N7:** `generic` applies `MODERN_TOKEN_FACTOR` and is clamped to `capabilities.max_tokens`.
- **N8:** the UI shows when env is the source, and that Save won't take effect then.
- **N9:** `checked_at` is left out of the hash.
- **N10:** the `fast` role's description mentions the debate label on Act Today.
- **N11:** drop `temperature` forwarding when the SDK reaches 2.0.
- **N12:** the TTL is an internal constant.
- **N13:** an autouse test fixture clears `DRISHTA_LLM_MODEL_*` and stubs the provider.

**Revised chunks:**

| Chunk | What | Review |
|---|---|---|
| **P2-a1** | Add-only `expects_json` at the 7 `fast` sites and the 2 thesis sites (B8), and the empty-text rule | Required |
| **P2-a2** | Resolver with source, `setting=` seam, known-modern and `generic` profiles with the effort cap, beta fallback with `fell_back`, Models API helpers in `llm.py`. Inert. | Required |
| **P2-b** | `llm_settings` (validation, state, TTL, `invalidate()`), per-table dispatch in `db.py` / `reference_data.py` with existing tables byte-identical, the B1 fixes, the monthly lane fix, `_GATE_FILES` and the review-policy line | Required |
| **P2-d** | System Trust "🤖 AI models" with the Investigator picker kept: observability before the write path | Required |
| **P2-c** | App Settings section with Test role (B6) and the effort cap; `requirements.md` F-ID in this commit | Required |
| **P2-e** | The remaining docs (on the lead: they carry policy) | Not needed |

