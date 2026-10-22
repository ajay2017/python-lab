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

1. **🟢 Railway security incident: misuse checks only.**
   - Credentials rotated, Railway redeployed with the new variables (web + 7 crons), and section 5's verification table passed (owner, 2026-10-22). `drishta.up.railway.app` is the verify surface again.
   - **Pending (owner):** section 5's one-time misuse checks (Anthropic usage, Resend log, Supabase logs plus trades vs broker, SnapTrade) in `docs/plans/credential-rotation-2026-10.md`; then mark that plan DONE and remove this item.
   - Days the crons ran on the deleted keys lost their forward-only captures (EOD leverage/risk rows, rec_events, score history, ledger cron rows); 10-21 is likely gone.
2. **S-D, one sizing path** (`docs/plans/one-sizing-path.md`, F-310).
   - C1 + C2 shipped 2026-10-20; C3 (risk footer) and C4 (quick wins: Watchlist non-ready rows, Catalyst / Econ windows, FSLR caption, (0b)) shipped 2026-10-22, screenshots pending.
   - C5 (Rebalancer underweight rows: no share counts, Grow Today's verdict per name, paused names in their own block; closes D5's Rebalancer half) shipped 2026-10-22, screenshots pending.
   - **NEXT: C6**, then C7–C9:
     - Diversification $
     - the D5 dry run, the AST ratchet and the docs
3. **App trust item 4:** antipattern rules for a literal `**` in raw HTML and a bare `$` next to numbers (~1 h). Slot it between S-D chunks.
   - Item 2 shipped (`db8d891`). **Owner:** re-apply `docs/sql/impossible-states-function.sql`, then run E3's first count.
   - Items 1, 3 (System Trust ⑧, planner first) and 5 are open (`docs/plans/app-trust-verification.md`).
4. **Weekly/monthly report, F-276c** (`docs/plans/period-report.md`).
   - Sections 1 + 3, C5b, C7 and C8 have shipped. **NEXT: C5** (section 2), then **C6** (sections 4 + 5).
   - Verify the Sunday email when the crons run: dates, money block equal to Reports, `metrics_version = 2`.
5. **Headline / feed-text HTML escaping.** Phase 1 shipped (`2cb4ace` `util.safe_link`, `60e7625` the 12 sites). Phase 2 is the remaining baselined dynamic-HTML sites (`docs/plans/headline-html-escaping.md`).
6. **LLM model routing** (`docs/plans/llm-model-routing.md` §12).
   - **Phase 1 complete 2026-10-22** (`74234dc`..`32e1d8a`): every Claude call goes through `stock_analyzer/llm.py`.
   - **Phase 2** (App Settings model choice) waits on a 30-day Anthropic console usage export. If spend is small, defer it; if large, consider a reviewed `CODE_DEFAULTS` switch instead.
   - Small items: the empty-text rule in `parse_response`, and ai_provider's refusal wording.
7. **Data providers and news.** Live Data Health on 10-17 showed Finnhub (live-price primary) returning 429 on 9 of 16 calls and FMP returning 402 on 11 of 11, so the fundamentals failover is effectively dead. That needs a read-only provider investigation in its own right. On news, the owner said (10-17) to re-check the queue before more news work. The lead recommends dropping chunk 4 (backup source) and the VADER-polarity fix, with a minimal or no 3b/3c (`docs/plans/news-feed-restore.md`).
8. **Small leftovers:**
   - F-304 follow-ups (3)–(7) (docs/queue.md)
   - From S-D C4 (2026-10-22): N-C, a macro (G-07) check on Grow Today adds, is still deferred, so the 📅 Economic Calendar defers any in-window add to Grow Today rather than saying "don't add"; reopening it needs planner + reviewer. Bare literals left in `earnings_advisor._recommend` (Hard Rule #1): `net_rev <= -2`, `weight >= 5`, `0.35`, `est_move * 0.85`. Watchlist Remove/Wait rows read "No size yet … once Ready to Enter" (true, odd on Remove; owner may want a neutral line).
   - **Bear scenario can sit ABOVE the price** (seen live 2026-10-22, FSLR: Bear $173.66, +1.4% at $171.25). `targets.py` takes the max of support × `TARGETS_BEAR_SUPPORT_CUSHION_MULT`, 52w low × `TARGETS_BEAR_52W_LOW_CUSHION_MULT` (1.03, an upward cushion) and price − `TARGETS_BEAR_ATR_MULT` × ATR, with no cap at the price; within 3% of the 52w low the bear floor lands above the price. Display-only (Analysis scenario blocks + two text summaries; R:R uses the stop). The fix is a formula choice (filter to candidates below the price, or cushion downward): owner picks the order vs S-D C5; reviewer before ship.
   - `self_track_record` UTC dating
   - the report's "Book side" label
   - 2 stale `.claude/worktrees`
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

- **S-D C4:** 📋 Watchlist NEAR_ENTRY "No size yet" / held caption; 📅 Economic Calendar 🎯 Add detail and the in-window HOLD sentence (its rationale sits in raw HTML and starts with `**TICKER**`, pre-existing: check for literal `**`); 🔔 Catalyst HOLD / HOLD_OR_ADD text; 📈 Analysis not-held Sell caption. (FSLR "Selling goes against today's Buy call" + "Add button hidden" verified live 2026-10-22.)
- **S-D C3 risk footer** (🏠 Home → Grow Today, under the cards): "If every stop hits, the N sized setups above lose ~$X", unsized names with reasons, `$` not LaTeX.
- **F-311 C3 labels:** the Watchlist R:R note, Scanner Range caption. (Trade Plan "🎯 Base target: Yahoo Finance mean, 30 analysts" verified live 2026-10-22.)
- **S-D C5 Rebalancer** (🥧 Portfolio Overview → ⚖️ Rebalancing Advisor): KPIs "Underweight" / "Trims would move"; the "⏸ Not adding — Grow Today paused these today" warning; an underweight card's "Drift · today's add call" box (one `$`, no share count); a news-flagged card ("📰 News Flag", no add advice). Known, pre-existing: `**TICKER**` in the rationale prints literally in raw HTML; a headline with two `$` could render as LaTeX.
- **S-D C3 footer needs a day with sized Grow Today picks:** 10-22 was risk-off, so no setups and correctly no footer. The Analysis "No add call today" box in its risk-off state was verified live 2026-10-22.
- **Recent Grow Today / Watchlist / Analysis surfaces:**
  - the F-305 overflow and flat-day boxes
  - F-306 budget lines
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
- **11-01:** first DST-change morning for the crons, plus the monthly statement check. **~11-10:** `SP500_SECTOR_WEIGHTS` shelf-life tripwire. **~11-23:** data-maintenance Phase 2's 8-week window ends.
- **Watchlist ENTER_NOW measured base rate,** ~Nov 2026. A single headline, never per composite band. **No confidence/probability figure on any surface without a new measurement that overturns A2.**
- **R-Multiple chunk 6:** gated on captured declared-stop rows, not a date.
- **Score-history decay readout:** Dec 2026–Feb 2027. It must confront the ~7-day real holding period.
- **Recommendation Outcomes:** 3-month checkpoint ~2026-12-16, 12-month review ~2027-09-17.
- **E2 alpha attribution:** at 180 captured days, ~Mar 2027.
- **G-25 Phase 2 (trim suggestion):** an observation checklist, not a date.
- **Data Foundation Phase 5:** no data yet.
- **NYSE 2029 calendar:** when NYSE publishes. **BEA 2027 GDP dates:** externally blocked.
- **Correlation listwise → pairwise:** only if the coverage caption ever shows a short sample. Needs planner + reviewer.
- **Deterioration-card hysteresis:** only if a card is seen flickering.
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
