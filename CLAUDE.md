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
  - Since 2026-10-30 the hook's pytest uses ¾ of the cores (`DRISHTA_HOOK_PYTEST_WORKERS` overrides) at below-normal priority, so the machine stays usable; a green snapshot run records its tree in `<git-common-dir>/drishta-tested-trees`.
- **`pre-push`:** skipped (since 2026-10-30) when every pushed commit is covered: its exact tree passed the full suite at pre-commit, or it touches no tested path and no suite-input doc (`docs/requirements.md`, `docs/architecture.md`, `docs/sql/`, `.gitmessage.txt`) on top of a covered or already-pushed parent. Otherwise a full run against the **working tree** rather than the commits being pushed; it warns on a mismatch but doesn't block, so it is not a real backstop. CI (`tests.yml`) re-runs the suite only for pushes matching its path filters and never gates the deploy.
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
- **Prices per million tokens, input / output:** Opus 5.5 $4/$20; Sonnet 5.5 $2/$10; Haiku 5.5 $0.10/$0.50. Limit (owner 2026-11-07): the $190/month enterprise plan's usage meter, which resets about every 5 hours; only usage above 100% of it costs extra. `scripts/session_cost.py`'s API-equivalent $ is a yardstick, not a budget (memory `project_weekly_token_budget`).

**When the `reviewer` is required:** `constants.py`, any gate, a scoring/recommendation formula, cross-feature coordination, DB-write / data-integrity, or a new user-facing decision surface.
- **Mechanically enforced through `_GATE_FILES` in `.claude/hooks/pre_tool_checks.py`, which is the authoritative list** (83 files on 2026-11-09, after `explained_gap_repair.py` joined). Print it:
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
- **Quality first, then efficiency (owner, 2026-11-01): a fix must not add a bug.** Three rules, each from a bug class found more than once:
  - **Time-context matrix.** Any date / day / "today" logic is tested on a weekend or holiday visit, a DST change day, after hours, and a trade logged or imported a day late (in both directions, buy and sell). Calendar day ≠ trading day (N1, N2, #29, #32).
  - **One freshness per number.** Never combine two sources that update at different times (broker cash vs journal holdings, live vs snapshot) into one figure without reconciling them or saying which side is stale (N1).
  - **Fix the class.** Before building, list every sibling site of the same pattern (file:line) and either fix it or name it in the commit body / [docs/bug-register.md](docs/bug-register.md). A finding that ships alone gets rediscovered.
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
| Every known open bug, one row each, with the agreed fix order | [docs/bug-register.md](docs/bug-register.md) |
| Review / hook / model-pin history, `_GATE_FILES` reasons | [docs/review-policy.md](docs/review-policy.md) |
| Coordination cache register | [docs/coordination-caches.md](docs/coordination-caches.md) |
| What's automated vs. manual testing | [docs/testing-strategy.md](docs/testing-strategy.md) |
| Latest pytest counts + coverage | [docs/test-results.md](docs/test-results.md) |
| Shipped-feature changelog | [docs/shipped-log.md](docs/shipped-log.md) |
| Auto-memory index (feedback, rationale, session handoffs) | `MEMORY.md` (Claude auto-memory, outside repo) |

---

## What's queued

**Last reconciled 2026-11-04.** One line per item; the detail is in [docs/queue.md](docs/queue.md) (snapshots of 2026-10-22 and 2026-11-04), [docs/bug-register.md](docs/bug-register.md) and the named plan. This index was reconciled against git on 2026-11-04. If it and a doc disagree, `git log` decides; fix the stale one. **Before acting on any item, `git log` it.** Items have more than once been listed as open after they shipped.

### Open now

*Tidied 2026-11-04: shipped and verified history moved verbatim to [docs/queue.md](docs/queue.md) ("2026-11-04 (verbatim, before the tidy)"); open bugs live one row each in [docs/bug-register.md](docs/bug-register.md). This list keeps only what is still open. `#N` / `U8` IDs match the bug register and the commits.*

**Owner-agreed order (2026-11-03; MacroGap step done 2026-11-04):**
1. ~~MacroGap + MacroAff + Macro~~ **SHIPPED 2026-11-04** (F-317: `fdf3635` `e9f44c6` `212f2c5` `b6113ff`; plan `docs/plans/macro-sector-gate.md`). Brief leg verified live after NFP 2026-11-06; Macro row closed. Watchlist "sector unknown" leg still open (before CPI 11-12).
2. **Write-safety batch:** ~~WLWipe~~ (fixed 2026-11-06, `72f35ff`), ~~RbPartial~~ (fixed 2026-11-06, F-81d), ~~TrPage~~ ~~DelIdx~~ (fixed 2026-11-06), ~~TJDup~~ (D0 shipped 2026-11-06, F-81e), ~~TJUtc~~ (C3 shipped 2026-11-06; C3b open), RbAsset (re-rated latent 2026-11-06: no reader of the column) (bug register; DB-write path → reviewer).
3. **TJDate Phase 1** (`docs/plans/trade-entry-guard.md`): confirming pass, P0 probe, C3 and D0 done 2026-11-06; C4 done 2026-11-07 (broker-sync ET date; live check: the next broker sync log reads "closed N linked pending", no duplicate Pending Imports); NEXT C1, then C2, C3b, C5, U9, U8.
4. **U7 + #21:** DESIGNED 2026-11-07 (`docs/plans/net-capital-broker-frame.md`, Opus planner PROCEED WITH CHANGES: net capital = the lower of journal and Robinhood's positions snapshot, tightening-only, shadow first); #21 traced = wording only (C5). **Owner to answer U1–U10 before C1.**
5. **#34c probe:** read-only check of SnapTrade's own last-sync time before designing.
- **Broker fill record** (owner asked 2026-11-09: automate the Robinhood → journal sync): DESIGNED (`docs/plans/broker-fill-record.md`, Opus planner PROCEED WITH CHANGES: Phase 1 one-click record of broker fill facts + a "notes owed" queue + same-day cron auto-link; Phase 2 cron auto-record gated). **Owner to answer U1–U16 and run probe P (`docs/sql/readonly-broker-fill-lag-probe.sql`) before any chunk;** it reorders TJDate (U14) and needs TJDate C5 + a UTC-date reader sweep (C0) first.
- **Dated:** N1 C8 before the November report (`docs/plans/account-snapshot-repair.md`: ~~C6b~~ and ~~C7~~ PUSHED 2026-11-09; C7's 4 older September dates (09-16 / 09-18 / 09-24 / 09-25) need the owner's `scripts/daily_snapshot_row_repair.py --apply` after the 11-09 EOD run, whose nightly reconcile covers 10-06 → 11-05 and 10-19; C8 docs); ~~**N1-LogTime**~~ repairs APPLIED and verified 2026-11-09 (fill times fixed 11-07; 10-19 via the backfill; 09-22 / 09-29 / 09-30 via the new `--explain` step, `db67c82`; Reports October now −8.47% / −$642 on $7,582, was +6.49%); prevention = TJDate C2 or the broker fill record plan; #26 C3c persisted stop floor by 2026-12-07 (`docs/plans/held-trailing-stop.md`).
- **LockBreach:** owner decided 2026-11-03 to RETIRE Lock Setup (option A); queued, don't start unless asked (closes #20 f/u).
- **Owner calls open:** keep the 6.0 Financials earnings fallback for uncurated financial names or restore 7.0 (C3 `212f2c5`); EW2; Fund; #25; Zone (planned-level zone parked until the ENTER_NOW base-rate readout).

**Features in flight (each plan's Status line has the detail):**
- **G-32 Grow Today R:R gate** (`docs/plans/grow-today-rr-gate.md`): C2 shadow measuring (`g32_shadow`); ~4–6 weeks of rows (~early Dec), then an outcome readout, then the owner rules on U1–U13.
- **S-D one sizing path** (F-310, `docs/plans/one-sizing-path.md`): only optional C7 left (read-only dry run on `SECTOR_CEILING` binding post-trade, D5).
- **App trust verification** (`docs/plans/app-trust-verification.md`): items 1, 3 (System Trust ⑧, planner first) and 5 open; the 18 baselined `$`-pair sites burn down when touched.
- **Period report F-276c** (`docs/plans/period-report.md`): NEXT C5 (section 2), then C6 (sections 4 + 5).
- **Headline / feed-text HTML escaping** Phase 2: the remaining baselined dynamic-HTML sites (`docs/plans/headline-html-escaping.md`).
- **LLM model routing** (`docs/plans/llm-model-routing.md` §15–17): every role is switchable in ⚙️ App Settings (F-313, F-316); `score` and `extract_trigger` run Haiku 5.5, `filter` stays on Haiku 4.5 (failed evals #15/#16). Open: the read-only dump of the headlines the candidates loosened (start on Haiku 4.5's retirement notice or an owner ask; never a looser limit); the reviewed `CODE_DEFAULTS` catch-up (see the `reset_role` TODO); Phase 5 (Investigator eval); `extract_strong` → Opus 5.5 after the D2-1 preview check; pin the settings snapshot per cron process; ai_provider's refusal wording; owner to-do: a 30-day console usage export. The SDK stays pinned `<1.0` until no role runs a legacy model (API `2026-10-21` rejects `temperature`).
- **Data providers:** FMP = earnings calendar + profile only (`DATA_EARNINGS_CALENDAR_ORDER`, `DATA_INFO_BACKFILL_ORDER`; per-symbol endpoints in `FMP_SKIP_ENDPOINTS`); Finnhub `news-sentiment` off. Reopen triggers: Finnhub `stock/metric` backup if Data Health shows Yahoo fundamentals failing on real holdings; paid FMP Starter only on a recurring Yahoo news outage, Yahoo price/fundamentals failures on real holdings more than a couple of times a month, or wanted analyst-grade / earnings backfill (then monthly billing, re-run the endpoint test on CRWD + VOO first).
- **#33 macro calendar automation** (FRED release dates → DB table, `_STATIC` fallback, moved dates shown on 🩺 System Trust; FOMC stays manual); open before design: FRED's 2027 coverage + release IDs, GDP advance vs later estimates, owner call on auto-applying a moved date; fold in the System Trust ⑤ caption fix. Not urgent (2027 rows are in).
- **#34a:** Haiku 4.5 is past its deprecation date; retirement comes with ≥ 60 days' notice to the owner's email; closing it = `filter` off Haiku 4.5 plus the `CODE_DEFAULTS` catch-up (the code default for `score` / `filter` / `extract_trigger` is still Haiku 4.5).

**Saturday data-quality email 2026-11-07 (queued, not yet ordered; owner will rank it against the register):** DQ-C7 (impossible-states C7 16 vs baseline 3; likely the N1 repairs leaving per-ticker `daily_snapshots` stale; fix = N1 C7, never raise the baseline), DQ-TSLA (duplicate thesis review, flag-only), DQ-Orphan (INTL / DELL cache rows, never served; Phase 2 targets). First step for all three: the owner runs `docs/sql/data-quality-triage-2026-11-07.sql` (read-only). Rows in `docs/bug-register.md`.

**🏠 Home top zone (owner asked 2026-11-06; display only):** AlertsGhost (four faded "⚠️ Alerts" bars on every page; the key experiment `0b0137c` was ruled out; next lead: full runs ending in `st.rerun()` / `st.stop()` before Streamlit clears stale elements) and HomeLayout (merge the Act Today pointer, "Needs a look" and the Alerts expander into one zone; options A / B / C, mockup first). **HomeLayout ON HOLD (owner 2026-11-07):** fix AlertsGhost alone; mockup kept at `docs/mockups/home-top-zone.html`, don't build without a fresh ask. AlertsGhost next step: the owner runs a read-only DevTools console snippet (expander opacity / `data-stale` / container chain) plus a Ctrl+F5 check, which separates Streamlit's fragment stale-cleanup race (PR #17718, unreleased) from our own run ending early. Rows in `docs/bug-register.md`.

**Smaller open items (full text in the docs/queue.md snapshot):**
- #20 f/u (Watchlist / Analysis banners ignore Lock Setup; moot if Lock Setup is retired), #20 (a) Signals live-tone fallback when Home's tone read failed (`entry_posture.py`, owner/planner call).
- #22 follow-ups: the read-only retro study (U5) not started; owner sets the re-enable margin before the readout.
- #29 f/u (ET buy date on lots; `peak_since_date` coercion; tests), #30 (parked until it recurs: pull the web log BEFORE pushing), #31 f/u (gate-file `~` sites).
- Earnings-week trims: EW (Home 🔧 Tune-up and the Rebalancer still size to 15%); record earnings trims as `rec_events` (own design + review); `macro_playbook` "a 0-share position"; `daily_briefing` `is_critical = gap <= 3.0` bare literal; int-`Shares` readers (`forward_sim.py` ~301 / ~473, `portfolio_qa.py` ~760 / ~838, app.py Q&A price map) and the int baseline's float-noise truncation (planner + reviewer); owner option: a sub-share macro trim falls through to the next affected name.
- R:R / Watchlist: captions format `RR_ENTRY_MIN` as `:.0f`; an unsized trim still hides "✅ Portfolio is well-balanced" on Signals; readiness point weights and the 70 / 40 bar colours are bare (display, low); owner option: "Underweight: {sector}" wording; Watchlist Remove/Wait "No size yet …" line.
- Rebalancer: the trim expander title still branches in app.py; `daily_briefing.py` ~971 bare `+ 5.0` (= `TOLERANCE_WATCH`, gate file); the drift-chart legend hardcodes "2–5pp" / ">5pp" / "±2pp". (The two-`$` news headline was fixed in `4964c1f`, 2026-11-03.)
- ETF negative-news Skip never fires: PAUSED (owner 2026-10-28), don't start unless re-asked.
- N-C: no macro (G-07) check on Grow Today adds (planner + reviewer to reopen).
- F-304 follow-ups (3)–(7); `self_track_record` UTC dating; the report's "Book side" label; SPCX item B (measure before designing); Market Scanner's score breakdown hardcodes stock weights (gate on `asset_type`); owner observation: Valuation consensus vs Smart Money can disagree on one screen.
- Hooks: re-measure the pytest time on 9-worker runs (raise the cap or the workers if a quiet run passes ~600 s or a loaded one ~780 s); trim the slowest tests before a run nears ~600 s; never run two full suites at once (MemoryError, 2026-11-04).
- **Deferred, revisit only on ask:** F-266 buying power / margin rate / Capital Trend line (Options A/B); Investor Maturity C1/C2; G-26 override and the F-230 calm-leg correlation blind spot; Grow Today's momentum-sorted list; Materials / Utilities / Real Estate scan seeds; analyst-weight test item (2); capital-basis % on every emailed pick; whether the owner's real stops live in broker stop orders (R-Multiple).

### Live checks pending (app.py has no tests, so a screenshot is the only check)

*Only legs not yet seen; what each should read is in the docs/queue.md snapshot. For every check: no literal `**`, `$` as text.*

- **F-317 (2026-11-04):** Brief macro card leg VERIFIED 2026-11-06 (after NFP). Still open: an uncurated Watchlist name with a "Technology"-type label reads "Held: sector unknown (macro check)" before NFP / CPI 11-12; a translated name reads "Sector taken from the provider label (… → …)".
- **N1 C6b (2026-11-07, after the Monday push):** a Reports period containing a left-out day: "Left out of this period's account figures:" with dates grouped by reason; "3 · Leverage & margin" reads "N day(s) left out …" (or "No usable leverage readings in this period." when every day was left out), not "No account snapshots recorded"; a left-out start day adds "change since D not shown". Expect nothing on a normal week. Only rows written since 2026-11-02 can be left out (`account_row_trust` never masks a legacy row), so no September or October period shows it. Pending Imports' "Possible match … (N trading days apart)" caption (TJDate C4) is also still unseen: the 11-09 CSCO row had no exact shares + price match and showed the "already reconciles" hint instead.
- **Composite / R:R display:** a 64.5–64.99 composite reads "64.6" (Brief, Signals, Playbook, Portfolio Overview, Analysis, Watchlist), never "65"; a redeploy R:R of 2.45–2.49 reads "2.46:1"; a 1.95–1.99 R:R reads "below 2:1" (parked with the fractional checks).
- **11-03 batches:** a flat Today's P&L with no arrow; the "≈$" no-baseline alert; "1 share" / "1 setup" / "1 downgrade"; movers after 20:00 ET; "Trims would move" 0.0% when every trim is under one share; Watchlist "is below the entry zone"; App Settings pending line naming the effort.
- **Signals headline (Bal):** the resolved, attention and clear legs.
- **Stops and breaches:** ~~the Batch C at-the-stop leg~~ (seen 2026-11-06, SAP $233.12 vs stop $233.02, "gap +0.0%" + the 0.5% recovery-margin caption); the ⛔ Exit stop card on a one-card-per-name Act item; #23 "just inside" / "beyond" legs; no degraded-basis warning on a normal load.
- **Macro / release:** #13 post-release wording on AI Snapshot, Summary and the Pre-Event Playbook (Brief leg seen 2026-11-06); Batch D's macro card dropping a risk-off trim name; #20 Lock Setup legs (moot if Lock Setup is retired).
- **Watchlist:** Batch B's 55–64 "Constructive, But Earnings in Nd" leg; Batch D's "R:R Can't Be Computed" card with a target; the S-D C4 🎯 Add detail.
- **Signals / Catalyst:** #22's Signals ⚑ variant and a remaining REDUCE's "Sell N share(s)"; a ⬆️ revision icon; the driver box absent on Add / Trim; the earnings-week Signals trim "→ ~10%"; the S-D C6 "Undersized" card; S-D C4 🔔 Catalyst HOLD / HOLD_OR_ADD text and the 📈 Analysis not-held Sell caption.
- **Grow Today:** "Markets down — …" empty state on a bear day; the sector hard-cap card's score label; the S-D C3 risk footer and the S-D C5 "⏸ Not adding" warning (need sized picks / a bull day); F-305 overflow and flat-day boxes; F-306 budget lines; F-307 Protect Mode / "Not an entry today"; chunk 7 "Adds held"; F-310 "Capped by what you already hold"; the F-304 withheld verdict on a recent IPO.
- **Other:** #14 Home Rebalancer leg; a held Buy's earnings note inside "Already held"; the Brief Review sub-share Watch; the 09:45 email footer for a stock pick and an ETF pick, plus G-28/G-29 rows with `source='cron'`; F-308 card wording and captions; the cross-check note clearing; F-311 Scanner Range caption; F-311 flicker across 65; the FSLR earnings box; Trade Journal Sell skip; F-204a Act Today ordering; ETF first firing items 1/2/3/5.
- **G-32 shadow:** the first scan day with picks logs "g32 shadow: saved=N/N"; System Trust ② hides which conditional store is unconfirmed (`system_health.py`, gate file → reviewer).
- **Rare, expect nothing on a normal day:** N1 C6 "Left out of this period's account figures"; Summary "Thesis reviews couldn't be loaded"; the Brief news-outage banner; F-290's partial-coverage caption; the Home offline (crashed Brief) path.
- **Parked (owner holds no fractional or 1-share position):** exact-shares displays and the 1-share "Bearish Signal on Profitable Position" card.

### Waiting for a date or data (don't build early)

- **Gate Suppression Ledger:** next look ~2026-10-26. G-20 counterfactual still unobserved. Retirement review ~2027-08-27 (retiring is the success condition).
- **~11-10:** Anthropic console usage, 2 weeks after the Phase 3 switch vs the 30 days before; `SP500_SECTOR_WEIGHTS` shelf-life tripwire. **~11-23:** data-maintenance Phase 2's 8-week window ends (then the owner-invoked F1 delete with confirmation; DQ-Orphan's INTL / DELL rows are its first targets).
- **Earnings rating-change shadow readout (finding #22):** at 20 flagged + 20 unflagged matured earnings events in `earnings_revision_shadow`, or ~May 2027, whichever first (pre-registered in `docs/plans/earnings-revision-shadow.md`). Before it: the owner sets the re-enable margin, and the read-only retro study (U5) runs. The flag sizes nothing until then, and re-enabling needs planner + reviewer + fresh approval, tightening-only.
- **#26 C3c persisted stop floor: build by 2026-12-07** or on the first day a data-caused degraded-basis banner appears (plan `docs/plans/held-trailing-stop.md`; C3 live since 2026-10-31, `d4ecd73`).
- **Watchlist ENTER_NOW measured base rate,** ~Nov 2026. A single headline, never per composite band. **No confidence/probability figure on any surface without a new measurement that overturns A2.**
- **R-Multiple chunk 6:** gated on captured declared-stop rows, not a date.
- **Score-history decay readout:** Dec 2026–Feb 2027. It must confront the ~7-day real holding period.
- **Recommendation Outcomes:** 3-month checkpoint ~2026-12-16, 12-month review ~2027-09-17.
- **E2 alpha attribution:** at 180 captured days, ~Mar 2027.
- **G-25 Phase 2 (trim suggestion):** an observation checklist, not a date.
- **Data Foundation Phase 5:** no data yet.
- **NYSE 2029 calendar:** when NYSE publishes. **Macro calendar backbone:** 2027 rows for CPI / jobs / PPI / GDP advance / retail sales added 2026-10-31 (`macro_calendar._STATIC`, every date read from the BLS / BEA / Census schedule); System Trust ⑤ goes amber from 2027-07-31 (runway under 90 days to the last GDP row, 2027-10-28) and red 2027-10-29, unless #33 lands first.
- **Finnhub `company-news` backup headline source (news chunk 4): DEFERRED 2026-10-27** (owner, planner RECONSIDER). Reopen on R1: the cron "news feed: 0 headlines" line fires on ≥ 2 ET days in 30 or an outage runs ≥ 2 trading days; R2: Yahoo changes `/v1/finance/search`; R3: a measurement shows held names often read `none`. Needs planner (re-run against HEAD) + reviewer. Detail: `docs/plans/news-feed-restore.md` top note.
- **Correlation listwise → pairwise:** only if the coverage caption ever shows a short sample. Needs planner + reviewer.
- **Deterioration-card hysteresis:** only if a card is seen flickering.
- **Composite-Sell track record:** "REVIEW — Signal: Sell" cards (composite < `COMPOSITE_HOLD`) are graded nowhere (the 🛡️ Defense facet covers deterioration TRIM/EXIT + risk-off only). Build once `score_history` shows ≥ 8 distinct Sell episodes (2026-10-27: 2, SPCX 10-15→10-16 and TSLA 10-23→); grade retroactively from `score_history` + prices (no new capture needed). Needs planner. Trigger query: `select ticker, min(score_date), max(score_date), count(*) from score_history where composite < 44 group by ticker`.
- **F-305 (d), the overflow-vs-picks readout:** build only once ≥ `GATE_LEDGER_MIN_CALLS` matured paired days exist (pre-registered in `docs/plans/grow-today-owns-buys.md`).
- **F-234 Phase 3:** wire ONE validated predictive signal into ONE protective gate, **tightening-only**, only if its skill score > 0 across a real window including ≥ 1 stress episode. Needs planner + reviewer and fresh explicit approval.
- **F-308 revisit trigger D-G:** ≥ 5 Review news WATCH cards on 3 of the first 5 trading days.

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
