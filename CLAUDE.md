# Claude Project Guidance — DRISHTA · Beyond Noise

A directive distillation for any Claude session working in this repo. Read first; obey throughout. Detail lives in [DEVELOPMENT.md](DEVELOPMENT.md) and [docs/](docs/). **This file is loaded into every request and every subagent, so keep it to rules and current state.** History and per-item detail go in [docs/queue.md](docs/queue.md), [docs/review-policy.md](docs/review-policy.md), [docs/coordination-caches.md](docs/coordination-caches.md), [docs/shipped-log.md](docs/shipped-log.md) and the plan docs. These were split out of this file on 2026-10-22, verbatim.

---

## Project orientation

Personal portfolio intelligence app for a single user.
- **Primary deploy: Railway Hobby at `drishta.up.railway.app`**, auto-deploying from `main` (cut over 2026-08-15; [docs/plans/railway-migration.md](docs/plans/railway-migration.md); secrets architecture in [DEVELOPMENT.md](DEVELOPMENT.md)).
- **Streamlit Community Cloud is a dormant cold fallback.** It still auto-deploys from `main` against the same Supabase DB. It is not the surface you verify against, and its free-tier throttling notices are expected noise. Use it only if Railway is down.
- **The same Railway project runs all 7 scheduled cron lanes** as native Cron Job services: premarket, scan, intraday, eod, weekly thesis, weekly maintenance, daily broker sync. `system_health._LANES` is authoritative. `.github/workflows/alerts.yml` is manual-dispatch only (memory `project_cron_railway_migration`).
- **Never run locally.** Both deploys assume hosted secrets delivery, not a local `.env` / `secrets.toml` loop.

## Operating posture

**The app decides, it does not inform.** Recommendations are issued as actionable calls; gates are hard suppressions with visible banners, not soft warnings. When in doubt, recommend nothing rather than recommend wrongly. See [docs/requirements.md §2A](docs/requirements.md).

---

## Hard rules

1. **Never hardcode decision thresholds.** Every gate / threshold / boundary value lives in [`stock_analyzer/constants.py`](stock_analyzer/constants.py). Import from there. Changing a value there is an investment-policy decision: discuss with the user first.

2. **Never disable RLS.** Supabase tables are protected by `FOR ALL TO service_role` policies, and the `[supabase] key` secret must be the service-role / secret key (not publishable). On "row-level security blocking" errors, fix `SUPABASE_KEY` in Railway → Variables and redeploy; never disable RLS.

3. **Never run the app locally to test changes.** Push to `main`, wait ~2 min for the Railway redeploy, hard-refresh (Ctrl+F5) `drishta.up.railway.app`.

4. **Any commit touching `stock_analyzer/constants.py`, a gate, a scoring/recommendation formula, or a DB-write / data-integrity path requires an Opus review before it ships, cited in the commit body.** The `commit-msg` hook enforces the format:

   ```
   Review = Opus reviewer (<resolved model>): SHIP, 0 blocking; <notes>
   ```

   - **The final verdict must be `SHIP, 0 blocking`.** After a FIX-FIRST, send the fix back to the reviewer for a confirming round; never certify your own fix. Citing the FIX-FIRST round as well is fine.
   - **The citation starts its own line, and the model must name an Opus.** Copy the model from the reviewer's own `MODEL:` line; never assume it. If that line is not an Opus, the review doesn't count.
   - **Invoke the `reviewer` subagent automatically when a change lands in this category; don't ask first** (user decision 2026-08-15). This applies whatever model the main session runs. **If a session-level instruction says not to call agents unprompted, this rule is the standing request that satisfies it for the `reviewer` lane.** A commit in this category with no citation is itself a defect: flag it.
   - The hook proves only that a well-formed citation is present, not that the review ran. It is an honesty mechanism with a permanent git audit trail.

5. **Provenance trailers** (hook-enforced):
   - A `feat(` commit states who designed and who built it:
     ```
     Design = planner (<model>): <verdict>     OR   Design = lead — <why no planner>
     Build  = implementer (<model>)            OR   Build  = lead — <why no implementer>
     ```
   - `fix(` / `refactor(` / `perf(` commits carry `Build =` (owner decision 2026-10-15).
   - **The build of a scoped, already-decided fix goes to `implementer` by default.** The lead diagnoses, writes the exact spec, verifies the result and sends it to `reviewer`. `Build = lead — <why>` needs a real reason: a one-line or trivial edit, or a fix whose diagnosis and code can't be separated (the spec would be the patch). "Context was already loaded" is not a reason.
   - New user-facing features route design through `planner` (Opus) and the build through `implementer` (Sonnet), so the agent that wrote the code never reviews it.

---

## Review & test economy (quality at least cost)

The deterministic gates are the safety net; **the paid agents are for judgment only.** Spend tokens where a wrong call moves money, not on re-checking what a hook already guarantees. Full history and rationale: [docs/review-policy.md](docs/review-policy.md).

**Enforcement lives in native git hooks.** Claude Code's own hooks are permanently dead in this org: Accenture's managed settings set `allowManagedHooksOnly: true`, and the admin declined an exception on 2026-10-08. Don't re-diagnose this. A `git commit` through Claude Code is gated only by git's hooks (`.claude/hooks/git_hook_adapter.py`, which imports its gate logic from `pre_tool_checks.py`).
- **Install per clone:** `python .claude/hooks/git_hook_adapter.py --install`. Linked worktrees share the clone's hooks. Check with `ls "$(git rev-parse --git-path hooks)/pre-commit"`.
- **`commit-msg`:** the text gates. A `Review =` citation when a `_GATE_FILES` member is staged, `Design =` / `Build =` on `feat(`, `Build =` on `fix(` / `refactor(` / `perf(`.
- **`pre-commit`:** the full `pytest` suite plus `check_antipatterns.py`, both run against a temporary snapshot of the staged commit.
  - pytest runs when the staged set touches `app.py`, `cron_runner.py`, `stock_analyzer/`, `tests/`, `.claude/hooks/`, `pytest.ini`, the requirements files, `railway.cron.toml` or `scripts/`. The antipattern check runs when a scanned path, or its own script or baseline, is staged.
  - pytest fails CLOSED; the antipattern gate fails open. If the snapshot can't be built, both test the working tree and print a loud warning.
- **`pre-push`:** a second full run, against the **working tree** rather than the commits being pushed. It warns on a mismatch but doesn't block, so it is not a real backstop.
- **Known gaps:**
  - cherry-pick, revert, rebase, `am` and merge commits skip `commit-msg` and `pre-commit`.
  - Nothing looks back for a `fix(` commit that slipped through without `Build =`. If that's ever wanted, add a since-this-commit cutoff; don't widen `_is_feature_commit`.
  - A message-only `--amend` runs no pytest, and an amend that drops a citation isn't detected.
  - Hard Rule #3 has no mechanical enforcement.
- **Every BLOCK is written to `git rev-parse --git-path last-hook-block.log`.** Read it in full, never grep-filtered, before re-diagnosing.
- On a timeout or a single xdist worker crash: check power and load, run the named test alone, retry once (memory `project_hook_pytest_timeout_load`). Never `--no-verify` without the owner's say.

**Agent lanes and model pins** (`.claude/agents/*.md` frontmatter; exact IDs, because aliases aren't stable here):

| Lane | Pin | Use for |
|---|---|---|
| `planner` | `claude-opus-5-5` | Design of money-moving logic: gates, thresholds, scoring, coordination, new decision surfaces |
| `reviewer` | `claude-opus-5-5` | Post-code review of the review-required set below |
| `implementer` | `claude-sonnet-5-5` | Scoped, decided edits |
| `doc-writer` | `claude-haiku-5-5` | Prose and comments after the facts are pinned (never policy values; see doc integrity) |
| `test-runner` | `claude-haiku-5-5` | Only when the git hooks aren't installed, or as a cheap pre-filter before a large Opus review. Not a per-change stage |

- **Run the lead on `claude-opus-5-5`:** `/model opus` resolves to it, and the change applies live. Don't use `opusplan`.
- **Agent definitions freeze at session start.** A pin change can only be verified in the next session, by a cold `MODEL:` probe.
- **A rejected pin falls back to the lead's model, so it costs more, not less.** A frozen definition reports the session-start pin; a rejected pin reports the lead's model.
- **The built-in `Plan` agent inherits the session model and has no pin.** Use it only for non-policy structural scaffolding, never for gate, threshold or scoring design: the `planner` / `reviewer` pins are what stop a cheap session default from downgrading policy design.
- If the lead ever reads Opus 5 or Sonnet 4.6 (the org default), re-run `/model opus`. Bump the pins by hand when a newer model ships.
- **Never pass a per-call `model:` alias to `reviewer` / `planner`;** it overrides the exact pin. Precedence: per-call → frontmatter → `CLAUDE_CODE_SUBAGENT_MODEL` → the lead's model.
- `availableModels` gates only the `/model` picker, not agent frontmatter.
- **Fable 5.1 was evaluated and rejected for every lane:** Opus 5.5 leads it at 2.5× less cost. Don't re-propose it without a new benchmark.
- **Prices per million tokens, input / output:** Opus 5.5 $4/$20; Sonnet 5.5 $2/$10; Haiku 5.5 $0.10/$0.50. Weekly budget: $250 API-equivalent (memory `project_weekly_token_budget`).

**When the `reviewer` is required:** `constants.py`, any gate, a scoring/recommendation formula, cross-feature coordination, DB-write / data-integrity, or a new user-facing decision surface.
- **Mechanically enforced through `_GATE_FILES` in `.claude/hooks/pre_tool_checks.py`, which is the authoritative list** (43 files on 2026-10-22). Print it:
  ```
  python -c "import sys; sys.path.insert(0,'.claude/hooks'); import pre_tool_checks as p; print(sorted(p._GATE_FILES))"
  ```
- For these files there is no "it's just a comment" exemption.
- **When you add a member, add its one-line reason to [docs/review-policy.md](docs/review-policy.md) in the same commit.**
- **Skip review** for docs-only, comments, tests-only, pure-additive code not yet wired into a decision path, and mechanical refactors in non-gate files, provided the suite, antipattern and constants-doc gates are green. When skipping, say so and why in one line.

**Audit cadence:** run an incremental `/audit` (diff plus blast radius) after a feature ships. Keep `/audit --full` for milestones, not every few days.

---

## Coordination pattern

Features that own state publish to `st.session_state`; downstream features read and gate on it.
- **When a producer fails, set the cache to `None`, not an empty container,** so consumers can detect "offline" rather than silently disabling gates. Many caches are three-state: `None` = couldn't check, `[]` / `{}` = checked and clean, populated = firing.
- **Read caches with `stock_analyzer.util.get_or_offline`,** never `.get(...) or []`, and never use `k in session_state` as a liveness test, because a published `None` is present.
- **Every portfolio-derived cache must be registered in `stock_analyzer/coord_freshness.py` in the same commit that creates it,** with its tier and surface. A key missing from that registry reads as permanently fresh.
- **When adding an advisor or recommendation feature, always check whether its decision overlaps another feature's,** and if so wire coordination through the same publish/consume pattern.

Current keys (per-key notes, `None` semantics and incident history: [docs/coordination-caches.md](docs/coordination-caches.md); producer/consumer table: [docs/architecture.md §4.0.2](docs/architecture.md)):

| Producer | Keys |
|---|---|
| 🏠 Home / portfolio build | `_last_port_df` / `_port_df_enriched` (same object), `_last_held_data`, `_last_held_tickers`, `_portfolio_value`, `_port_risk_cache`, `_fragility_cache`, `_highbeta_share`, `_risk_high_alerts_cache`, `_risk_advisor_recs_cache`, `_alert_list_cache`, `_actions_cache`, `_acct_gate_cache`, `_leverage_cache` (awareness only), `_holdings_sig_at_home_build`, `_day_shock_cache`, `_dpnl_cache`, `_broker_drift_cache` (`None` / `"unknown"` = not checked), `_xc_bad_prev_tickers` (always a set), `_home_synth_cache` (new Home inputs must join its signature) |
| Correlation / diversification | `_div_recs_cache`, `_corr_df_cache`, `_div_score_cache`, `_avg_corr_cache`, `_risk_pairs_cache`, `_div_label_cache`, `_corr_coverage_cache`, `_corr_unchecked_cache`, `_structural_alert_cache` (gates G-25) |
| Daily Brief / Grow Today | `_grow_today_sectors_cache`, `_grow_composites`, `_grow_composites_coverage`, `_daily_brief_offline`, `_reduce_calls` (consumed by Analysis, My Edge, Opportunity Signals, Rebalancer, 🧑‍⚖️ The Judge, 🧾 Summary), `_leading_sectors_cache`, `_market_tone_cache`, `_brief_news_flags_cache`, `_new_position_budget_cache` |
| Other pages | `_mirror_orphans` / `_mirror_overexp` / `_mirror_overhangs` (My Edge), `_pi_factor_tilt_cache` (🧩 Intelligence, button-gated), `_bm_summary_cache` (My Edge, button-gated), `_wl_budget_plan_cache` (📋 Watchlist) |
| Freshness / errors | `_coord_epoch` / `_coord_stamps` (bump only when the book changes), `_refresh_error` |
| Day- or range-scoped shared reads | `_rh_prices_cache_{start}_{end}`, `_account_daily_snapshots_full_cache_{date}`, `_portfolio_risk_snapshots_full_cache_{date}`, `_rec_events_full_cache_{date}`, `_account_flows_full_cache_{date}` (`is None` = miss; a failed read is never cached) |

---

## Navigation safety

`st.session_state.nav_page` is bound to the sidebar navigation widget. **Setting it directly raises `StreamlitAPIException`.** Use the `_pending_page` indirection: a button sets `_pending_page`, it is consumed at the top of the next run, then assigned to `nav_page`. The consumption logic lives near the top of `app.py`.

---

## Coding conventions

- **Pure logic lives in `stock_analyzer/`; UI rendering and orchestration in `app.py`.** Don't move domain logic into `app.py`.
- **Extract the DECISION, not just the helper; this is the default.** No test imports `app.py`, so any conditional there that decides what the user is told (a threshold comparison, a breach, a staleness call, a tone classification) is verifiable only by screenshot.
  - Put the decision in a pure function in `stock_analyzer/`, unit-test it at its boundaries, and leave `app.py` as render-only wiring.
  - Examples: `portfolio.refresh_outcome`, `portfolio.gate_basis`, `coord_freshness.decide_stale_banner`, `outage_gate.decide`, `util.factor_tilt_state`.
  - Extraction does not catch **renderer-mismatch** bugs: `**bold**` printing literally inside raw HTML, or `$…$` rendering as LaTeX. Only a screenshot finds those (memory `feedback_streamlit_renderer_mismatch`).
- **Mechanically ratcheted:** `check_antipatterns.py`'s `POLICY_DECISION_IN_RENDER` rule fails when `app.py` / `cron_runner.py` gains a new comparison against a `constants.py` value. Comparing inside `stock_analyzer/` is never flagged.
  - **Prefer a NEW module over growing a `_GATE_FILES` one** (the pattern: `outage_gate.py`, `coord_freshness.py`), so the right move stays the cheap one.
- **New database columns must be backward-compatible:** `db.load_trades()` backfills `None` for legacy rows.
- **Date comparisons use America/New_York.** Take "now" / "today" from `stock_analyzer.market_time.now_et` / `today_et`; the hosted containers run UTC.
- **For UI suppressions, render a visible banner explaining what was suppressed and why.** Never silently filter.
- **Escape interpolated HTML with `stock_analyzer.util.safe_html`** (`util.safe_link` for links).

---

## Commit messages

**Conventional Commits:** `type(scope): summary`, imperative, lowercase, ≤72 chars, no trailing period, a body explaining **why**, trailers in the footer. Types: `feat fix docs refactor perf test build ci chore revert`. Spec and the one-time `git config commit.template .gitmessage.txt` setup: [DEVELOPMENT.md](DEVELOPMENT.md); template: [`.gitmessage.txt`](.gitmessage.txt).

- **Threshold/gate changes** (`constants.py`) are investment-policy decisions. Call them out in the body and name the constant and its old → new value.
- **Feature commits sync the docs that describe behaviour:** a user-facing feature or gate touches `docs/requirements.md` (the functional spec), not just `docs/architecture.md`.
- **Claude-authored commits end with `Co-Authored-By: Ajay with <model>`,** with no email address. Use the actual model name and version from the session context (e.g. `Claude Opus 5.5`).
- Write the message to `.git/COMMIT_MSG.txt` and run `git commit -F` (dodges PowerShell here-string mangling).

## Documentation integrity (zero-hallucination)

This is a decision-making app: **a wrong value in a doc erodes trust in the app itself**, so docs are held to the same bar as code.
- **Transcribe every threshold / constant / file:line from the source, never from memory.** Memory and summaries reflect what was true when written; verify against HEAD.
- **Never invent** function names, constant names, session keys, UI text, dates or test counts. If you can't confirm it in code or real output, don't write it.
- **Doc edits that carry policy values stay on the Opus lead,** never the Haiku doc-writer: constants tables, gates, requirements, this file. Treat every doc-writer output as needing verification, whatever file it touches; it has invented function names, a wrong constant value, a fabricated date and a fabricated test count.

---

## Definition of Done — sync docs in the SAME session (don't defer to a periodic audit)

A user-facing feature or a new/changed decision value is **not done** until its docs are synced in the same work session:

1. **New/changed constant in `constants.py`** → add or update its row in the `docs/architecture.md` constants table, or allowlist genuinely internal plumbing in `scripts/constants_doc_allowlist.txt`. **Mechanically enforced** by `scripts/check_constants_documented.py`, both in CI and in the suite. Run it before committing.
2. **New user-facing surface** (page, tab, card, gate, section) → add or update an F-ID in `docs/requirements.md`.
3. **Shipped a queued item** → update its one-liner in this file's queue and its detail in [docs/queue.md](docs/queue.md) (or its plan), add an entry to [docs/shipped-log.md](docs/shipped-log.md), and fix any memory that still calls it open. Before a doc sync, grep the queue's named functions against the code and the shipped log.
4. **User-visible behaviour changed** → update the in-app User Guide (`app.py`, `elif page == "📖 User Guide":`).
5. **Non-obvious decision or rationale** → capture it in a memory file.
6. **Phased feature with a future phase gated on a date or condition** → add that gate to "What's queued" below immediately, not only to memory. This file is loaded into every session; memory isn't.
7. **Touched a `docs/plans/*.md` feature** → bump that plan's own `**Status:**` line.
8. **A changed constant feeds a value written to a history table?** If a reader needs to know which regime produced an old row, version it (precedent: `COMPOSITE_WEIGHTS_VERSION` / `recommendations.weights_version`).

Only #1 is mechanically guarded; #2–#8 are the author's checklist. Run all eight when wrapping up a feature.

**Second mechanical guard: the recurring-defect gate.** `scripts/check_antipatterns.py` (CI plus the `pre-commit` hook) fails on a NEW instance of a bug class past audits kept re-finding.
- **The classes:** offline-sentinel collapse (`.get(...) or []` / `or {}`), dynamic `unsafe_allow_html` (the XSS class), and naive `datetime.utcnow()` / `date.today()` date math.
- **It is baseline-gated** (`scripts/antipattern_baseline.json`). Prefer fixing at the source with `util.get_or_offline`, `util.safe_html` and `market_time.now_et` / `today_et` over growing the baseline.
- Migrating an existing call site is a live behaviour change. On a decision/gate path it needs its own review.

---

## Pointers

| Need | Where |
|---|---|
| Single portable map of the whole app (usage + mechanics) | [docs/user-manual.md](docs/user-manual.md) |
| Full dev context after time away | [DEVELOPMENT.md](DEVELOPMENT.md) |
| All decision thresholds | [stock_analyzer/constants.py](stock_analyzer/constants.py) |
| Functional requirements + operating policy | [docs/requirements.md](docs/requirements.md) |
| Architecture, data flow, scoring model, db schema, known behaviours | [docs/architecture.md](docs/architecture.md) |
| Full queue history, closed items and "don't re-propose" reasoning | [docs/queue.md](docs/queue.md) |
| Review / hook / model-pin history, `_GATE_FILES` reasons | [docs/review-policy.md](docs/review-policy.md) |
| Coordination cache register | [docs/coordination-caches.md](docs/coordination-caches.md) |
| What's automated vs. manual testing | [docs/testing-strategy.md](docs/testing-strategy.md) |
| Latest pytest counts + coverage | [docs/test-results.md](docs/test-results.md) |
| Shipped-feature changelog | [docs/shipped-log.md](docs/shipped-log.md) |
| Auto-memory index (feedback, rationale, session handoffs) | `MEMORY.md` (Claude auto-memory, outside repo) |

---

## What's queued

**Last reconciled 2026-10-22.** One line per item; the detail is in [docs/queue.md](docs/queue.md) (a 2026-10-22 snapshot) and the named plan. This index was reconciled against git on 2026-10-22. If it and a doc disagree, `git log` decides; fix the stale one. **Before acting on any item, `git log` it.** Items have more than once been listed as open after they shipped.

### Open now (owner-agreed order, 2026-10-22)

*The Railway security incident was closed 2026-10-23 (owner); see `docs/plans/credential-rotation-2026-10.md`, including the forward-only captures lost on 10-21.*

0. **Grow Today R:R gate, G-32** (`docs/plans/grow-today-rr-gate.md`; owner decision 2026-10-23: R:R ≥ `RR_ENTRY_MIN` on new picks AND adds, after a dry run). **C1 SHIPPED 2026-10-23** (`entry_rr.py` unrounded R:R + `scripts/grow_rr_dryrun.py`, not wired). **Dry run DONE 2026-10-23** (readout + outcome analysis in the plan: no 5/10-day alpha separation at 2.0 across 96 picks; the owner's own 13 lots favour passers). **Owner chose the C2 shadow first; C2 built 2026-10-23** (`g32_shadow` table written by the scan cron, measure-only). DDL applied 2026-10-23. **NEXT: ~4–6 weeks of rows (~early Dec), then an outcome readout, then the owner rules on U1–U13.** C4 (move G-13, the redeploy headline and the Analysis tile onto unrounded R:R) must ship before or with enforcement.
1. **S-D, one sizing path** (`docs/plans/one-sizing-path.md`, F-310): **C1–C6, C8 and C9 shipped (2026-10-20…23).** Only optional C7 remains: a read-only dry run on whether `SECTOR_CEILING` should also bind post-trade (D5); owner decides after seeing it. Screenshots still pending for C3 (footer, needs sized picks), C4 rows and the C6 Signals "Undersized" card.
2. **App trust verification** (`docs/plans/app-trust-verification.md`). Item 4 shipped 2026-10-23: `MARKDOWN_DOLLAR_PAIR` and `HTML_LITERAL_BOLD` in the antipattern gate. The 18 baselined `$`-pair sites burn down when touched.
   - Item 2 shipped (`db8d891`); the Q0 function was re-applied and verified 2026-10-22 (owner SQL: 32 rows, `_V` = `q0:fdbd2b2a9c28`, every check at its baseline; C1 21 vs 19 is its by-design accruing class, the regression split C9 is unchanged at 5). E3 was baselined 10-20 (11).
   - Items 1, 3 (System Trust ⑧, planner first) and 5 are open (`docs/plans/app-trust-verification.md`).
3. **Weekly/monthly report, F-276c** (`docs/plans/period-report.md`).
   - Sections 1 + 3, C5b, C7 and C8 have shipped. **NEXT: C5** (section 2), then **C6** (sections 4 + 5).
   - Verify the Sunday email when the crons run: dates, money block equal to Reports, `metrics_version = 2`.
4. **Headline / feed-text HTML escaping.** Phase 1 shipped (`2cb4ace` `util.safe_link`, `60e7625` the 12 sites). Phase 2 is the remaining baselined dynamic-HTML sites (`docs/plans/headline-html-escaping.md`).
5. **LLM model routing** (`docs/plans/llm-model-routing.md` §13–14).
   - **Phase 1 complete 2026-10-22** (`74234dc`..`32e1d8a`): every Claude call goes through `stock_analyzer/llm.py`.
   - **Phase 2 IN BUILD** (owner 2026-10-25: make future model switches a settings change): ⚙️ App Settings picks the model + effort (≤ `high`) for `fast` / `deep` / `extract`; `score` / `filter` / `extract_trigger` / `extract_strong` stay in reviewed code. Planner PROCEED WITH CHANGES, every recommended safeguard accepted. **P2-a1 + P2-a2 + P2-b + P2-d SHIPPED 2026-10-25** (per-call `expects_json` + empty-text rule; inert resolver with source, known-modern / generic profiles, refusal fallback, Models API helpers; `llm_settings` validated / hash-checked settings row + snapshot, wired into `complete`; 🩺 System Trust 🤖 AI models table; plan §15). **P2-c prerequisites (`246d927`) + P2-c SHIPPED 2026-10-26** (⚙️ App Settings 🤖 AI models, F-313: Check model → Test role → Save only after a passing test of that exact model + effort; Reset; `stock_analyzer/ai_models_settings.py`). **P2-e docs SHIPPED 2026-10-26; Phase 2 COMPLETE.** **Phase 3 switches DONE 2026-10-27** (owner, App Settings, Test role first; plan §16): `extract` → Sonnet 5.5, `fast` → Haiku 5.5, `deep` → Sonnet 5.5 (after `b13ed92` `THESIS_REVIEW_MAX_TOKENS` 300 → 1000: the first `deep` test used 363 of 390). **NEXT after the watch:** `filter` + `extract_trigger` → Haiku 5.5 (reviewed `CODE_DEFAULTS` change), then Phase 4 (`score`, dry run + planner + reviewer), Phase 5 (Investigator eval); `extract_strong` → Opus 5.5 after the D2-1 preview check. Still open: pin the settings snapshot per cron process (plan §15). No model changes until Phase 3.
   - Owner to-do: a 30-day Anthropic console usage export (Phase 3's cost baseline).
   - Small item still open: ai_provider's refusal wording (the empty-text rule moved into P2-a1).
   - The SDK stays pinned `<1.0` until no role runs a legacy model: API version `2026-10-21` (SDK 2.0's default) rejects `temperature`, which 14 legacy-model sites send.
6. **Data providers and news.** **2026-10-27 (owner, after the D-1 / D-3 readout):** Finnhub `news-sentiment` switched OFF (`FINNHUB_NEWS_SENTIMENT_ENABLED`; non-JSON on every call, ~31% of Finnhub calls) and FMP `news/stock` skipped (`FMP_SKIP_ENDPOINTS`; the first cut skipped `earnings` + `grades-consensus`, but the owner's live probe on AAPL showed both working, so their earlier 402s were per-symbol, e.g. VOO; only `news/stock` is endpoint-restricted, "Restricted Endpoint"); FMP stays in both provider orders (superseded the same evening, below). Probe on AAPL 10-27: every Finnhub candidate works (`company-news` 247 items/7d); 10 of 11 FMP endpoints work. New 🧪 Test provider endpoints on Data Health (F-314, `providers/endpoint_probe.py`) tests every in-use endpoint plus Finnhub candidates (`company-news`, `stock/metric`, `stock/recommendation`, `calendar/earnings`). **2026-10-27 evening (owner): FMP = earnings calendar only.** The Ticker-box probe showed FMP's free plan serves only sample symbols (AAPL works; CRWD / VOO get 402 "Premium Query Parameter" on quote, history, fundamentals, earnings and grades; only `profile` and `earnings-calendar` work for all). FMP left `DATA_PROVIDER_ORDER` / `DATA_LIVE_PRICE_ORDER`; new `DATA_EARNINGS_CALENDAR_ORDER = ["fmp"]` keeps Catalyst Watch's calendar, and new `DATA_INFO_BACKFILL_ORDER = ["fmp"]` keeps a one-call `profile` refill on sparse-`.info` days so holdings keep their sector norms (owner chose this after the reviewer found `profile` works for every symbol); the other per-symbol endpoints are in `FMP_SKIP_ENDPOINTS` (so the per-symbol negative-cache idea is moot). **Finnhub `stock/metric` fundamentals backup: NOT now (owner); reopen if Data Health shows Yahoo fundamentals failing on real holdings.** **Paid FMP Starter (about $22/mo billed annually, per FMP's plan guide as read 2026-10-27; verify on FMP's pricing page): stay free unless (1) a Yahoo news outage recurs (FMP news would back up headlines without competing with Finnhub's price limit), (2) Yahoo price/fundamentals failures hit real holdings more than a couple of times a month, or (3) analyst-grade/earnings backfill is wanted; if upgrading, take monthly billing and re-run the endpoint test on CRWD + VOO before putting FMP back in the orders.** The Finnhub `company-news` backup headline source is DEFERRED (owner 2026-10-27, planner RECONSIDER; triggers under "Waiting for a date or data").** Earlier: **Read-only investigation DONE 2026-10-25** (`docs/plans/provider-investigation.md`): the Finnhub 429s come from (1) no stop or cooldown on the live-price path (the per-ticker loop keeps calling after a 429; `get_live_prices` / `crosscheck_*` ignore `in_cooldown`) and (2) bulk historical-valuation pages (Summary track record, Rec History, Predictive Analytics, AI Insights) pricing the whole rec-ticker universe through real-time Finnhub. **P1 + P2 + P3 + P5 + P6 SHIPPED 2026-10-25** (owner D-2). **FMP kept as-is (D-1): after a few days of P6's Data Health "Error details" (errors by endpoint, full FMP message with endpoint + symbol), judge whether FMP calls were real failovers or bad-symbol gap-fills, then decide upgrade vs wait.** D-3 (Finnhub news-sentiment) waits on the same readout. Live check: Data Health shows "N saved" on Bundle cache, no "served last saved snapshot" on a normal visit, and far fewer Finnhub 429s after visiting Summary / Rec History. Earlier: **2026-10-23: the post-rotation 401s are FIXED** (owner re-set `FINNHUB_API_KEY` / `FMP_API_KEY` in Railway; Data Health after redeploy shows no auth errors). **Still broken, back to the 10-17 state:** Finnhub 15 calls OK vs **112 rate-limited (429) and 8 parse errors** in one session, so the app over-calls Finnhub's free tier and most live prices and cross-checks still fall back to Yahoo; **FMP returns 402 Payment Required on every call** (key accepted; the endpoints the app uses aren't in the plan): owner call to upgrade the plan or drop FMP from `DATA_PROVIDER_ORDER` / `DATA_LIVE_PRICE_ORDER`, after the investigation. NEXT: a read-only provider investigation (Finnhub call volume per page / refresh, throttle + cache options, what the parse errors are). The Data Health "↳ live providers failed for ≥1 ticker; app served last saved snapshot" footnote (app.py ~3640) is a false claim: `db.save_bundle_cache` records every write-through SAVE as a call (live 10-23: 77 cache "calls" = 77 live loads); count serves separately (db.py gate file → reviewer). Earlier: Live Data Health on 10-17 showed Finnhub (live-price primary) returning 429 on 9 of 16 calls and FMP returning 402 on 11 of 11, so the fundamentals failover is effectively dead. That needs a read-only provider investigation in its own right. On news, the owner said (10-17) to re-check the queue before more news work. The lead recommends dropping chunk 4 (backup source) and the VADER-polarity fix, with a minimal or no 3b/3c (`docs/plans/news-feed-restore.md`).
7. **Small leftovers:**
   - F-304 follow-ups (3)–(7) (docs/queue.md)
   - From S-D C4 (2026-10-22): N-C, a macro (G-07) check on Grow Today adds, is still deferred, so the 📅 Economic Calendar defers any in-window add to Grow Today rather than saying "don't add"; reopening it needs planner + reviewer. (`earnings_advisor._recommend`'s bare literals were named 2026-10-25.) Watchlist Remove/Wait rows read "No size yet … once Ready to Enter" (true, odd on Remove; owner may want a neutral line).
   - **R:R / trim truth fixes shipped 2026-10-23** (`fc9e6af`; Diversification headline VERIFIED live 2026-10-23: names CVX 68 / R:R 2.3 over OXY 74 / 1.8 and EOG 72 / 0.3; other screenshots pending). **Owner decisions 2026-10-23 (memory `project_rr_entry_policy_2026_10_23`):** `RR_MARGINAL_MIN` 1.5 → 2.0 (= `RR_ENTRY_MIN`) and the single-name card shows no trim for an under-one-share excess (both shipped in `715a175`; icon band and the 🚦 "below the 2:1 minimum the Watchlist uses" headline VERIFIED live 2026-10-23: OXY 1.8 ❌, EOG 0.4 ❌). **Entry-ready-first ranking SHIPPED 2026-10-23** (owner decision; `rr_readout.rank_add_candidates`): on the Diversification Advisor and the Rebalancer redeploy card, names clearing BOTH composite ≥ `COMPOSITE_BUY` and R:R ≥ `RR_ENTRY_MIN` rank first before the top-`DIVERSIFY_DISPLAY_TOP` cut (live case: CVX 68 / R:R 2.3 had fallen behind COP 69 / R:R N/A); screenshot pending. Review follow-up: captions/headlines format `RR_ENTRY_MIN` as `:.0f`, which would misprint a non-integer value. **Grow Today to require R:R ≥ 2:1 on picks AND adds, after a read-only dry run: planner designing (open-now item).** the Diversification Advisor and Rebalancer redeploy headlines name a "genuine entry" only when a DISPLAYED candidate also has R:R ≥ `RR_ENTRY_MIN` (`rr_readout.redeploy_headline`, now a `_GATE_FILES` member); display strings never round across a threshold (`rr_readout.fmt_ratio_vs`, e.g. Watchlist "1.97:1 below the 2:1 minimum"); Trade Plan / Diversification R:R bands named `RR_FAVOURABLE_MIN` / `RR_MARGINAL_MIN`; a zero-share Signals trim is `urgency` "low", writes no `rebal_trim` row and isn't counted in `act_today_n`; the 4-pillar label fixed. Follow-ups from its review: (1) `targets.risk_reward` rounds R:R to 1 decimal BEFORE the headline and the Analysis gate compare it, so a true 1.95–1.99 still passes there as 2.0 while the Watchlist (2-decimal R:R) rejects it: give every gate comparison one unrounded R:R (gate file, reviewed). (2) Grow Today R:R gate: owner said yes (see above). (3) An unsized trim still hides "✅ Portfolio is well-balanced" on Signals (app.py `elif not actions`). (4) Single-name sub-share trim: decided and fixed (see above). Left from before: the owner may want "➕ Add for Diversification" / "🟢 Add {sector}" reworded to "Underweight: {sector}" (S-D C6 review).
   - **Earnings-week trim unification + sub-share trims: SHIPPED 2026-10-25** (owner Option A: in earnings week the Brief, the 🔔 Catalyst Watch playbook and Signals all size the overweight trim with one rule, `stock_analyzer/trim_sizing.py`, to `EARNINGS_OVERWEIGHT_TRIM_TO_PCT`). Screenshot pending: a Signals trim card in earnings week reads "→ ~10%" plus the earnings sentence. Follow-ups:
     - **Owner call:** for a book of ≤ 7 names the earnings-week line is above `SINGLE_NAME_TRIM_TRIGGER` (18), so an 18%-to-line name gets WATCH on the Brief and HOLD on the playbook while Signals trims to 15; reviewer's tightening-only option: in the window, trigger = min(line, `SINGLE_NAME_TRIM_TRIGGER`) on all three. Pinned by `test_concentrated_book_band_pins_todays_behaviour`.
     - **Sub-share round-ups: SHIPPED 2026-10-28** (`ee5247d`; the Brief's weak-large TRIM_TO_TARGET and macro PROTECTIVE_TRIM and Signals' "review" half now floor; under one share → a Watch that says why; a macro protective trim worth < 1 share of the chosen name moves Act → Awareness, per the 10-23 rule). Screenshot pending: a Signals review card on a 1-share holding. Follow-ups from its review: (1) every trim site sizes from int-truncated `port_df` Shares (portfolio.py ~603) while weight comes from the float market value, so a fractional holding can under-size (2.95 sh at 13% → 0); size from Market Value / Price (reviewed, all `trim_sizing` callers); (2) owner option: on a sub-share macro trim, fall through to the next eligible affected name whose one share fits.
     - Other surfaces still sizing to 15% in earnings week: the Home 🔧 Tune-up `single_name_concentration` card (`risk_advisor.py` ~948, not deduped against the Brief) and the Rebalancer.
     - Measure earnings trims: record them as `rec_events` so pre-print trims can be judged against holding (needs its own design + review).
     - Cosmetic: `macro_playbook` can print "a 0-share position" for a holding under 1 share; `daily_briefing` `is_critical = gap <= 3.0` is a bare literal (Hard Rule #1).
   - Wording that contradicts the screen (seen live 2026-10-23): the deterioration-add box (VERIFIED live 2026-10-23) and the Watchlist's "1 share" (screenshot pending) shipped in `e087636`.
   - Rebalancer follow-ups from the 2026-10-22 clean-up review (non-blocking): the trim expander title branch on `shares_delta` / price still sits in app.py (return a ready title field from `rebalancer.py`); `daily_briefing.py` ~961 has a bare `+ 5.0` meaning `TOLERANCE_WATCH` (gate file, its own reviewed change); the drift-chart legend caption hardcodes "2–5pp" / ">5pp" / "±2pp" (interpolate the constants); a news headline with two `$` in the Rebalancer news box could render as LaTeX. Also: "Trims would move" (`trim_pct`) still counts a trim that sizes zero shares (live: 2.4% from META's under-one-share trim, where nothing would be sold); count only trims with `shares_delta` ≥ 1, as `trim_action_needed` does.
   - **Sparse-`.info` refill now MERGES: SHIPPED 2026-10-28** (`244ec91`, Opus SHIP after 2 FIX-FIRST rounds): Yahoo's non-empty values win, the FMP profile fills missing / blank / NaN keys, `_info_filled_keys` keeps a Yahoo price target from being labelled "FMP", and the 📈 Analysis caption reads "Company profile filled in from FMP: …" (screenshot pending, on a sparse-`.info` name). **Open owner policy call (review finding 2):** in `fundamentals.resolve_fundamentals`, a live set that clears `FUNDAMENTALS_GATE_MIN_METRICS` (1) wins over a fresh, fuller `fundamentals_cache` row and the write-through overwrites that row; this already happened when the refill failed, and now also when a sparse Yahoo `.info` kept 1–2 core metrics. Option: prefer a fresh cache with strictly more core metrics, and don't write through a thinner live set. Planner + reviewer if the owner wants it.
   - **🔔 Pre-Earnings Playbook "Analyst Expectations" always blank + title repeats the ticker (found live 2026-10-28, pre-existing, NEXT):** `earnings_advisor.py` ~514 reads `data.get("info")` (forwardEps / trailingEps / forwardPE / revenueGrowth / earningsGrowth / shortName), but `bundle_loader.load_bundle`'s result has no `"info"` key, so every row reads "No analyst estimate data available." and "ABBV — ABBV". Display-only (none of these feed `_recommend`). Fix: read `data["financials"]` (`eps`, `forward_pe`, `revenue_growth`, `earnings_growth`) and `data["name"]`, keeping `info` as a fallback; it is the only `get("info")` reader of held_data.
   - **ETF negative-news Skip never fires (review finding 2026-10-28, pre-existing):** ETF candidate bundles never reach `news_items` (`curate_brief_news(held_data, grow_composites)` at `headless_alert_engine.py` ~569 and app.py ~6317 read held + the scanner pool only), so `_cross_reference` on the ETF lane always gets no headlines, and ETF tickers are absent from `feed_state`. The morning email now says "Negative-news check isn't applied to ETF picks yet." Fix = thread ETF candidate bundles into `curate_brief_news` and `feed_state` / `home_feed_state` on cron and app (gate path: planner-light + reviewer); change that email sentence and `test_footer_etf_pick_says_negative_news_not_applied` in the same commit.
   - **Thesis-review loads still collapsing a failed read to "no reviews"** (after `ca8ba02` fixed Home + Summary): 🧠 AI Insights (app.py ~43734, `_ai_db.load_thesis_reviews()`, which both new "couldn't be loaded" notes point to; do this one first) and the Opportunity Signals contradiction audit (~20255, `_sca_reviews_df`; only drops a signal input). Review NBs: move `thesis_review_status.broken_tickers` inside Home's try; the offline caption shows under Act Today only, not Awareness.
   - **Hook pytest timeout: RAISED 450 → 900 s 2026-10-27** (`77141da`, owner chose raising the cap; Opus SHIP after FIX-FIRST). Still open: trim the slowest tests (`pytest --durations=40`) before a completed run nears ~600 s, not after the next timeout. 2026-10-28: a completed pre-commit run took 535 s (11,101 tests, with agents' test runs in parallel) and one timeout test (`test_ticker_liveness.py::test_escalation_timeout_breach_is_bounded_and_fails_safe`) failed under that load, then passed alone. A quiet-machine full run the same day took 382 s (11,138 tests), so the 535 s was load. Slowest: `test_check_antipatterns` real-repo baseline 21 s; the LLM / sizing AST ratchets (`test_llm_ratchet`, `test_llm_roles`, `test_sizing_ratchet`) 5–10 s each, ~55 s together, a candidate for a shared parse if a trim is ever needed. Run hook-bearing commits/pushes from Claude Code with `run_in_background` (the Bash tool's 600 s foreground cap is below 900).
   - `self_track_record` UTC dating
   - the report's "Book side" label
   - 2 stale `.claude/worktrees`
  - System Trust ② data-stores sentence (`5c4fea3`): screenshot pending; expect "No store found missing or stale — 6 daily stores are fresh; 17 others exist (existence-only check …); not confirmed: G-32 entry R:R shadow (measure-only): exists, no rows yet."
   - owner observations: the Sentiment tile subtitle says "VADER" though LLM rescoring is live; Valuation consensus (saved research) vs Smart Money (provider) can disagree on one screen
   - SPCX item B, the post-earnings score lag (measure before designing)
   - Market Scanner's score breakdown hardcodes stock weights; ETF Phase 2b shipped, so gate it on `asset_type` if an ETF can reach it
   - **Deferred, revisit only on ask** (detail in docs/queue.md):
     - F-266 buying power / per-security margin rate / Capital Trend history line, and its Options A/B
     - Investor Maturity C1/C2 extensions (roadmap §5)
     - G-26 override (no acknowledge path today) and the correlation blind spot left on F-230's calm leg and the diversification score
     - Grow Today's momentum-sorted list (owner call)
     - Materials / Utilities / Real Estate seeds not yet on the scan roster
     - analyst-weight test item (2)
     - capital-basis % on every emailed pick
     - whether the owner's real stops live in broker stop orders (R-Multiple)

### Live checks pending (Railway is back as of 2026-10-22; app.py has no tests, so a screenshot is the only check)

- **Railway deploys were stuck "initializing" (Railway incident, evening 2026-10-28):** `5c4fea3`, `ef59c27` and `445967b` were pushed but had not been seen live; a 14:15 ET snapshot still showed the old AI ACTION ITEMS. Confirm the active deploy is `445967b` (or later) before any check below.
- **2026-10-28 batch (not yet seen live):**
  - 🏠 Home → 🤖 AI Snapshot grounded in the Brief (2026-10-28): the card shows "WHAT THE APP IS TELLING YOU TODAY" (Act Today / Monitoring / Grow Today copied from Today's Brief, not AI-written) after EXECUTIVE SUMMARY; the AI narrative has no action items; a footer says the Brief wins; when the Brief is offline, Generate is disabled with a ⏸️ note. Check AAPL reads Watch there, no literal `**`, no `$`-LaTeX.
  - 🏠 Home → 🤖 AI Snapshot (`a07dc6f`) VERIFIED live 2026-10-28: header "App Settings fast model — claude-haiku-5-5 (default)", generated 10:45 ET, full brief (3 action items, not cut off), `$` amounts render as text.
  - The 09:45 morning action email footer (`29a7127`): a stock pick reads "…the tone, sector, concentration, macro and negative-news checks cleared."; an ETF pick adds "Negative-news check isn't applied to ETF picks yet."
  - 🧾 Summary 🩺 Thesis Integrity / Act Today "Thesis reviews couldn't be loaded" (`ca8ba02`): shows only on a DB read failure, so expect nothing on a normal day; the healthy card is unchanged.
  - 📡 Signals "Bearish Signal on Profitable Position" card on a 1-share (or sub-1-share) holding (`ee5247d`): "Half this position is under one share …", no "Sell 0 shares", no 📒 log button; bold renders, no `$`-LaTeX.
  - 🏠 Brief Review: a weak-large or macro trim worth under one share reads Watch with the reason, not "Trim N shares".
  - 🔔 Catalyst Watch → 📋 Pre-Earnings Playbook action box (`54270a0`) VERIFIED live 2026-10-28 on ABBV: bold renders (no literal `**` in "Your stop is … below current price"), and the line ends "…don't wait for the stop to fill at a price well below it."
  - 📈 Analysis → Trade Plan on a HELD Hold/Sell with earnings within 21 days (`5dc9089`) VERIFIED live 2026-10-28: ABBV (Hold 62) shows "📅 Earnings in 2 days (2026-10-30)". A held Buy should still show it once, inside "Already held" (not yet seen).
- **🏠 Brief news-outage banner (2026-10-27, news chunk 3b):** only fires on an outage, or when ≥ `NEWS_FEED_OUTAGE_MIN_TICKERS` names' news couldn't be checked (incl. a run served from cache). When it does, one `st.warning` spans both Brief columns and names Grow Today's negative-news Skip; a normal day shows nothing. Expect it rarely; watch for banner fatigue if cached Grow/mover bundles trip it often.
- **🏠 Brief one-card-per-name + stop-raise minimum (2026-10-27; Act card VERIFIED live 2026-10-27 on TSLA: Act Today (1), "At minimum, trim 10.6% → 8.0% (1 share ≈ $399)", entry thesis block, Log this trim (1 sh), no raise-stop card; Thesis-broken button correctly absent, latest review WEAKENING; a ⛔ Exit stop card not yet seen):** a held name with a composite Sell AND weak-large (TSLA 10-27) shows ONE Act Today card: "Reduce or exit … At minimum, trim X% → 8% (N share(s) ≈ $Y)", the 📒 entry-thesis block and a "📒 Log this trim" button (no second 🔍 card; Act count drops by one). Its Awareness stop card (if any) shows ⛔ Exit and "Today's Brief already says to reduce", never "still has room". No raise-stop card when the raise is < `STOP_RAISE_MIN_STEP_PCT` (1%) of price. No literal `**`, no `$`-LaTeX.
- **📡 Signals driver box (2026-10-27, `signal_driver.py`; REVIEW card VERIFIED live 2026-10-27 on TSLA T 62 / BQ 32 / V 27 / S 60: "🔴 Business quality weakness is the primary driver — this is a thesis-change signal", bold renders; no-box-on-Add/Trim not yet seen):** on a REVIEW card the box agrees with the card's Recommended Action (TSLA T 62 / BQ 32 → "🔴 Business quality weakness is the primary driver", not the old swapped "Technical weakness only"); no box on Add / Trim cards; bold renders (no literal `**`).
- **Grow Today score labels (2026-10-26; macro card VERIFIED live 2026-10-27: "S (Cybersecurity, momentum 100 · composite 52)", all 4 shown composites 52–59, below `COMPOSITE_BUY`; sector card not yet seen):** the 🌐 macro-event card and the sector hard-cap card read "momentum N" (plus "· composite N" when a trusted composite was recorded) for a new pick; a held add on the sector card still reads "Score N". No literal `**`.
- **⚙️ App Settings → 🤖 AI models (P2-c, pushed 2026-10-26):** owner screenshot 2026-10-26 VERIFIED the status line, the 3 roles on code defaults, the 4-row locked table and the literal env-var text; History → "AI model settings" VERIFIED live 2026-10-27 (3 rows: "Initial capture: extract: claude-sonnet-5-5", then fast → claude-haiku-5-5, deep → claude-sonnet-5-5, all by `app_settings_ui`); inside the `fast` expander VERIFIED live 2026-10-27: Check model shows efforts low…max + max output tokens 128000; Test role passed (end_turn, claude-haiku-5-5, 1,051 / 394 tokens, 2.9 s of 30 s, cost "≈$0.0003" with no LaTeX); Save enabled after the pass, and greyed out ("Save is off: the last test was for a different model or effort.") after switching effort default (low) → explicit low. **All P2-c checks done.** Nothing is saved by this check unless the owner chooses to.
- **🩺 System Trust → 🤖 AI models (P2-d, `b3eec89`) VERIFIED live 2026-10-25:** green no-row line, 7 rows all "code default" on the expected models, Key ✅ on all 7, no env notes, counters moving (score 29 calls, filter 1), Investigator picker intact. Cosmetic: the long "What it does" / "Editable" cells truncate at column width.
- **Provider throttle + Data Health (`8f98f7b`, pushed 2026-10-25; VERIFIED live 2026-10-27: Finnhub 35 calls, 0 rate-limited (was 112 on 10-23), all 11 errors are `news-sentiment` parse ("Expecting value: line 1 column 1", e.g. XOM); FMP 5 calls, all 402 (earnings 3, grades-consensus 2, incl. VOO); Bundle cache "74 saved", no snapshot note; Error details lists both by endpoint; F-314 probe ran live 10-27 (18 rows, FMP counter 89 → 100 = its 11 FMP requests). Only the cross-check note clearing is still unseen. 2026-10-28, after `131d335` (FMP = calendar + profile): tile reads "FMP (earnings calendar + profile) — today: 19/250" (10-27 reached ~100); 🔔 Catalyst Watch 📡 Radar still fed by FMP's calendar (139 names watched, 49 reporting in 7 days; some watchlist rows show no sector, e.g. "CCS ·", cosmetic); Error details "No errors recorded since the last reset" (no FMP 402s, no news-sentiment parse errors), Finnhub 9 calls green, Bundle cache "27 saved" with no snapshot note: VERIFIED. D-1 / D-3 readout handed to the owner 2026-10-27):**
  - **Finnhub:** after visiting 🧾 Summary / 📜 Rec History, Finnhub's rate-limited count barely moves.
  - **Bundle cache:** the tile shows "N saved", with no "served last saved snapshot" note on a normal visit.
  - **"Error details" expander:** opens under Data Health and lists FMP's 402s by endpoint (D-1 readout after a few days).
  - **Cross-check:** any "Price cross-check paused — a price source (X)…" note clears once X recovers.
- **Earnings-week trim unification (`b2f905c`, pushed 2026-10-25), validate Mon 10-26:** 🏠 Brief VERIFIED live 2026-10-28 (11 names, line 14.1%): FSLR 11.4% (1d), ABBV 9.4% (2d), AAPL 11.3% (5d) all "Watch: no action today … within tolerance", no "sell 1 share". 🔔 playbook VERIFIED live 2026-10-28: ABBV and FSLR both "Watch" (no REDUCE), the same call as the Brief. No Signals case today (no holding over 18%); the Signals leg waits for a qualifying name.
  - **📡 Signals:** a held name over `SINGLE_NAME_TRIM_TRIGGER` with a gain and earnings within 7 days reads "Sell N shares … → ~10%" plus "sized by the same earnings-week rule the 🏠 Brief and 🔔 Catalyst Watch use".
  - **The same name on 🏠 Brief → Review and on the 🔔 Catalyst Watch playbook** shows the same share count.
  - **No "sell 1 share" on an under-one-share trim.**
  - **Render:** no literal `**`, no `$`-LaTeX.
  - If no holding qualifies, FSLR earnings Thu 10-29 is the first live case.
- **G-32 C2 shadow:** DDL applied 2026-10-23 (owner). 2026-10-28: `g32_shadow` has 0 rows, consistent with Grow Today: `recommendations` since 10-23 holds only 21 `enter_now` rows (10-23) and no `new_pick` / `add_winner`, so no Grow Today name has existed to record. Pending: on the first scan day with picks, the cron log shows "g32 shadow: saved=N/N" and the table gains rows. Found the same day: System Trust ② says "23/24 confirmed fresh", but only the 6 unconditional stores are age-checked (the 18 conditional ones are "ok" if they ever had a row), and the collapsed row hides which store is unconfirmed (here `g32_shadow`, empty); the wording + naming fix touches `system_health.py` (gate file → reviewer).

- **S-D C4:** (📋 Watchlist NEAR_ENTRY "No size yet" VERIFIED live 2026-10-23 on OXY: no count, no 📒 Log buy; held WAL shows "Already in Portfolio", no size.) Still open: 📅 Economic Calendar 🎯 Add detail and the in-window HOLD sentence (its rationale sits in raw HTML and starts with `**TICKER**`, pre-existing: check for literal `**`); 🔔 Catalyst HOLD / HOLD_OR_ADD text; 📈 Analysis not-held Sell caption. (FSLR "Selling goes against today's Buy call" + "Add button hidden" verified live 2026-10-22.)
- **S-D C6 VERIFIED live 2026-10-23** on the Diversification Advisor (Energy: "Gap to 10% target ≈$1,248", ↓ "-5.8pp vs target", "if the whole ≈$1,248 gap went here"). The Signals "Undersized" card awaits a held Strong Buy under `REBALANCE_ADD_UNDERSIZED_PCT`.
- **S-D C3 risk footer** (🏠 Home → Grow Today, under the cards): "If every stop hits, the N sized setups above lose ~$X", unsized names with reasons, `$` not LaTeX.
- **F-311 C3 labels:** Scanner Range caption. (Watchlist "🎯 Base target: Yahoo Finance mean, N analysts — the R:R above is built from it" verified live 2026-10-23 on MU/OXY.) (Trade Plan "🎯 Base target: Yahoo Finance mean, 30 analysts" verified live 2026-10-22.)
- **Rebalancer clean-up + Bear fix VERIFIED live 2026-10-22** (`362b891`): KPIs 1 + 2 + 12 = 15 holdings, "Watch" on META, "≈$508 drift (under one share)", "−2pp tolerance" / "−5pp action" labels; FSLR Bear $167.38 (−2.3%). S-D C5 verified too; only its "⏸ Not adding" warning awaits a bull day with a paused add.
- **S-D C3 footer needs a day with sized Grow Today picks:** 10-22 was risk-off, so no setups and correctly no footer. The Analysis "No add call today" box in its risk-off state was verified live 2026-10-22.
- **Recent Grow Today / Watchlist / Analysis surfaces:**
  - the F-305 overflow and flat-day boxes
  - F-306 budget lines (Watchlist line + "Reserved by today's email run" on an emailed ENTER_NOW verified live 2026-10-23: 39.9% of 40%, MU still sized inside the email's allocation)
  - F-307 Protect Mode / "Not an entry today"
  - the chunk 7 "Adds held" block
  - F-310 "Capped by what you already hold"
  - the F-304 withheld verdict on a recent IPO
- **Other surfaces:**
  - the FSLR earnings box
  - Trade Journal Sell skip
  - F-204a Act Today row ordering, plus the Top-Positions composite cross-check
  - the 🏠 Home offline (crashed Brief) path
  - ETF first firing, items 1/2/3/5
- **F-307 / F-308 emails and cards:**
  - the 09:45 Watchlist email footer, plus G-28/G-29 rows with `source='cron'`
  - F-308's Act/Review card wording, the Rebalancer caption, the noise-filter caption only when it fires
- **F-290's partial-coverage caption** has never rendered; it needs a real partial price-history failure.
- **After the F-311 deploy, watch for:** names flickering across 65 as the analyst count crosses 3 or a target nears 3× price; a one-time "newly ready" Watchlist email (dry run: BZUN, GLW, MRVL, ONON).
- **For every check:** no literal `**`, no `$`-LaTeX.

### Waiting for a date or data (don't build early)

- **Gate Suppression Ledger:** next look ~2026-10-26. G-20 counterfactual still unobserved. Retirement review ~2027-08-27 (retiring is the success condition).
- **11-01:** first DST-change morning for the crons, plus the monthly statement check. **Sun 11-01 20:00 ET:** the first cron `deep` run on Sonnet 5.5 (thesis lane log `llm: … deep=claude-sonnet-5-5 (setting)`; reviews saved, not dropped). **This week:** System Trust 🤖 AI models errors / fallbacks on fast / deep / extract (fast's stance used 62% of its budget). 2026-10-28 config VERIFIED live (fast Haiku 5.5, deep + extract Sonnet 5.5 from App Settings; pinned roles on code defaults; Key available on all 7; 0 errors / 0 fallbacks), but the counters reset on every deploy (6 that day), so judge errors late in a session with no deploy since, or from the cron logs. **~11-10:** Anthropic console usage, 2 weeks after the Phase 3 switch vs the 30 days before. **~11-10:** `SP500_SECTOR_WEIGHTS` shelf-life tripwire. **~11-23:** data-maintenance Phase 2's 8-week window ends.
- **Watchlist ENTER_NOW measured base rate,** ~Nov 2026. A single headline, never per composite band. **No confidence/probability figure on any surface without a new measurement that overturns A2.**
- **R-Multiple chunk 6:** gated on captured declared-stop rows, not a date.
- **Score-history decay readout:** Dec 2026–Feb 2027. It must confront the ~7-day real holding period.
- **Recommendation Outcomes:** 3-month checkpoint ~2026-12-16, 12-month review ~2027-09-17.
- **E2 alpha attribution:** at 180 captured days, ~Mar 2027.
- **G-25 Phase 2 (trim suggestion):** an observation checklist, not a date.
- **Data Foundation Phase 5:** no data yet.
- **NYSE 2029 calendar:** when NYSE publishes. **BEA 2027 GDP dates:** externally blocked.
- **Finnhub `company-news` backup headline source (news chunk 4): DEFERRED 2026-10-27** (owner, planner RECONSIDER). Reopen on R1: the cron "news feed: 0 headlines" line fires on ≥ 2 ET days in 30 or an outage runs ≥ 2 trading days; R2: Yahoo changes `/v1/finance/search`; R3: a measurement shows held names often read `none`. Needs planner (re-run against HEAD) + reviewer. Detail: `docs/plans/news-feed-restore.md` top note.
- **Correlation listwise → pairwise:** only if the coverage caption ever shows a short sample. Needs planner + reviewer.
- **Deterioration-card hysteresis:** only if a card is seen flickering.
- **Composite-Sell track record:** "REVIEW — Signal: Sell" cards (composite < `COMPOSITE_HOLD`) are graded nowhere (the 🛡️ Defense facet covers deterioration TRIM/EXIT + risk-off only). Build once `score_history` shows ≥ 8 distinct Sell episodes (2026-10-27: 2, SPCX 10-15→10-16 and TSLA 10-23→); grade retroactively from `score_history` + prices (no new capture needed). Needs planner. Trigger query: `select ticker, min(score_date), max(score_date), count(*) from score_history where composite < 44 group by ticker`.
- **F-305 (d), the overflow-vs-picks readout:** build only once ≥ `GATE_LEDGER_MIN_CALLS` matured paired days exist (pre-registered in `docs/plans/grow-today-owns-buys.md`).
- **F-234 Phase 3:** wire ONE validated predictive signal into ONE protective gate, **tightening-only**, only if its skill score > 0 across a real window including ≥ 1 stress episode. Needs planner + reviewer and fresh explicit approval.
- **F-308 revisit trigger D-G:** ≥ 5 Review news WATCH cards on 3 of the first 5 trading days.
- **Unverified plan states; check, don't trust either version:**
  - `signal-reconciliation-momentum-available.md` and `engine-track-record-meter.md`
  - the Phase-2/3 gates untracked since 2026-07-26: `multi-agent-debate-phase3.md`, `structural-scanner-phase2.md`, `thesis-red-team-phase2.md`

### Parked or declined — don't re-propose without the stated trigger (reasons in docs/queue.md)

- **Deterioration-ladder retune:** don't. A1's negative Defense verdict is one high-beta pocket, not the ladder; any change must be a narrow beta-aware qualifier, gated on the ledger and a W6 re-run.
- **Margin option (c),** recalibrating the gate denominator to net capital: parked. Trigger: over-cap list grows instead of resolving, or a re-ask.
- **Robinhood Agentic trading:** parked until a funded Agentic account exists.
- **Analyst email ingestion:** declined; pasted research has no measured edge.
- **Portfolio Q&A `current_status` intent:** parked; needs an explicit re-ask and its own review.
- **Self Track Record decision-moment mirror:** rejected.
- **Forward Simulator Phase 2:** closed by the W6 replay; exits aren't early.
- **System Proprioception Phase 2:** closed, not warranted.
- **Today's-P&L broker reconciliation via MCP:** parked until the beta matures.
- **Investigator `forward_alpha_at_horizon` v1.1:** only when a real compound question needs it.
- **Semiconductor names absent from the F-240 scan roster are deliberate** (the 53% → 44% tech rebalance). `VST` in the Clean Energy pool is accepted; a real exclusion needs a per-sector deny-list.
- **Fable 5.1 for any agent lane:** rejected (see Review & test economy).
