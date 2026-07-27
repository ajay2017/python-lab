# Claude Project Guidance — DRISHTA · Beyond Noise

A directive distillation for any Claude session working in this repo. Read first; obey throughout. Detail lives in [DEVELOPMENT.md](DEVELOPMENT.md) and [docs/](docs/).

---

## Project orientation

Personal portfolio intelligence app for a single user. Primary deploy is Streamlit Community Cloud, auto-deploying from `main`. A Railway Hobby pilot (`drishta.up.railway.app`) has run in parallel since 2026-07-24 against the same Supabase DB — see [docs/plans/railway-migration.md](docs/plans/railway-migration.md) for status and [DEVELOPMENT.md](DEVELOPMENT.md) for the secrets architecture. Never run locally — both deploys assume their respective hosted secrets delivery, not a local `.env`/`secrets.toml` dev loop.

## Operating posture

**The app decides, it does not inform.** Recommendations are issued as actionable calls; gates are hard suppressions with visible banners, not soft warnings. When in doubt, recommend nothing rather than recommend wrongly. See [docs/requirements.md §2A](docs/requirements.md).

---

## Hard rules

1. **Never hardcode decision thresholds.** Every gate / threshold / boundary value lives in [`stock_analyzer/constants.py`](stock_analyzer/constants.py). Import from there. Changing a value there is an investment-policy decision — discuss with the user before changing.

2. **Never disable RLS.** Supabase tables are protected by `FOR ALL TO service_role` policies. The Streamlit secret `[supabase] key` must be the service-role / secret key (not publishable). If you see "row-level security blocking" errors, the fix is to swap secrets and reboot the app via Streamlit Cloud → Manage app → Reboot — not to disable RLS.

3. **Never run the app locally to test changes.** Push to `main`, wait ~2 min for Streamlit Cloud auto-redeploy (and, during the Railway pilot, its auto-redeploy too), hard-refresh the browser (Ctrl+F5).

4. **Any commit touching `stock_analyzer/constants.py`, a gate, or a scoring/recommendation formula requires an Opus review before it ships, cited in the commit body** (`Review = Opus reviewer: SHIP/FIX-FIRST, N blocking; ...`). This applies **regardless of which model is running the main session** — invoke the `reviewer` subagent (pinned `model: opus` in [`.claude/agents/reviewer.md`](.claude/agents/reviewer.md)) explicitly; don't rely on the main session's own judgment as a substitute. A commit in this category with no review citation is itself a defect — flag it. (Two 2026-07-15 commits shipped without this citation and needed a retroactive review to close the gap — see `docs/cost-routing.md`.)

---

## Coordination pattern

Features that own state publish to `st.session_state`; downstream features read and gate. When a producer fails, set the cache to `None` (not an empty container) so consumers can detect "offline" rather than silently disabling gates. Existing cache keys (refreshed 2026-07-13 against actual code, prior list had drifted): `_last_port_df`/`_port_df_enriched` (both hold the same enriched `port_df` object), `_last_held_data`, `_last_held_tickers`, `_portfolio_value`, `_port_risk_cache`, `_fragility_cache`, `_highbeta_share`, `_risk_high_alerts_cache`, `_risk_advisor_recs_cache` (full recommendation list), `_alert_list_cache`, `_actions_cache`, `_div_recs_cache`, `_corr_df_cache`, `_div_score_cache`, `_avg_corr_cache`, `_risk_pairs_cache`, `_div_label_cache` (these 8 feed the standalone Signals & Advice / Risk Analysis pages), `_grow_today_sectors_cache`, `_grow_composites`, `_grow_composites_coverage`, `_daily_brief_offline`, `_acct_gate_cache` (concentration gate basis — now equity; see account-baseline), `_leverage_cache` (margin/leverage awareness — read-only, never gates), `_holdings_sig_at_home_build` (published at Home's `build_portfolio_df` call; a `(ticker, shares)` signature consumed by `_portfolio_snapshot_stale()`/`_render_portfolio_stale_banner()` on 7 other pages to warn — never gate — when a trade logged elsewhere this session has made their cached `_port_df_enriched` stale), `_day_shock_cache` (Home, after the price strip — held tickers moving ≥`DAY_SHOCK_PCT` same-day, awareness only, never gates), `_structural_alert_cache` (Home, after the `_home_synth_cache` hit/miss block converges — `None` when correlation data is offline this session, `[]` when checked and no new cluster formed, else the list of newly-formed correlation clusters since the last 🧬 Structural Scan; awareness only, never gates; nothing consumes it downstream yet), `_dpnl_cache` (Home, after the Tier-B `compute_positions_day_pnl` try/except — `{dpnl, baseline_date, is_current, date}`; 🧾 Summary consumes it so its "Today's P&L" tile matches Home's instead of independently recomputing the cheaper held-only mark, dated so a stale cross-day value is never reused, falls back to its own held-mark calc labeled "(held)" when absent/stale).

When adding a new advisor or recommendation feature, **always** check whether its decision overlaps with another feature's. If yes, wire coordination via the same publish/consume pattern.

---

## Navigation safety

`st.session_state.nav_page` is bound to the sidebar navigation widget. **Setting it directly raises `StreamlitAPIException`.** Use the `_pending_page` indirection (button sets `_pending_page`; consumed at top of next run; then assigned to `nav_page`). The `_pending_page` consumption logic lives near the top of `app.py`.

---

## Coding conventions

- Pure logic lives in `stock_analyzer/`; UI rendering and orchestration in `app.py`. Don't move domain logic into `app.py`.
- New database columns must be backward-compatible: `db.load_trades()` backfills `None` for legacy rows missing columns.
- Date comparisons use America/New_York timezone via `pytz` (Streamlit Cloud runs UTC).
- For UI suppressions, render a visible banner explaining what was suppressed and why — never silently filter.

---

## Commit messages

**Conventional Commits**: `type(scope): summary` — imperative, lowercase, ≤72 chars, no trailing period; a body explaining **why**; trailers in the footer. Types: `feat fix docs refactor perf test build ci chore revert`. Full spec + the one-time `git config commit.template .gitmessage.txt` setup live in [DEVELOPMENT.md](DEVELOPMENT.md); the template is [`.gitmessage.txt`](.gitmessage.txt).

- **Threshold/gate changes** (`stock_analyzer/constants.py`) are investment-policy decisions — call them out in the body and name the constant + old→new value.
- **Feature commits must sync the docs that describe behaviour:** a user-facing feature or gate touches `docs/requirements.md` (the functional spec), not just `docs/architecture.md`. requirements.md silently drifted ~3 weeks once because per-feature docs commits hit architecture but skipped requirements — don't repeat that.
- **Claude-authored commits** end with the trailer `Co-Authored-By: Ajay with <model> <ajay.x.ku@accenture.com>`, written via `.git/COMMIT_MSG.txt` + `git commit -F` (dodges PowerShell here-string mangling). Use the **actual model name from the session context** (e.g. `Claude Sonnet 4.6`, `Claude Opus 4.8`, `Claude Haiku 4.5`) — never hardcode a model name.

## Documentation integrity (zero-hallucination)

This is a decision-making app: a **wrong value in a doc erodes trust in the app itself**, so docs are held to the same bar as code.

- **Transcribe every threshold / constant / file:line from the source, never from memory.** Before writing a number into any doc, open `stock_analyzer/constants.py` (or the actual code) and copy it. Recalled values in `MEMORY.md`/summaries reflect what was true *when written* — verify against HEAD.
- **Never invent** function names, constant names, session keys, UI text, or dates. If you can't confirm it in code, don't write it.
- **Doc edits that carry policy values stay on the Opus lead** — do not delegate constants-table / gate / requirements writes to the Haiku doc-writer (it has hallucinated exactly these: invented function names, a wrong constant value, a fabricated date). The doc-writer is for prose/comments after the facts are pinned.

---

## Definition of Done — sync docs in the SAME session (don't defer to a periodic audit)

A user-facing feature or a new/changed decision value is **not done** until its docs are synced in the same work session. Three shipped features (Phase 3 Catalyst Scanner, Phase 2b sentiment, the 44/45 gate) drifted undocumented for days before the 2026-07-16 sweep caught them — the fix is a per-feature checkpoint, not a rare cleanup. Run this every time:

1. **New/changed constant in `stock_analyzer/constants.py`** → add/update its row in the `docs/architecture.md` constants table (or, if genuinely internal plumbing, add it to `scripts/constants_doc_allowlist.txt`). **Mechanically enforced:** `.github/workflows/docs-check.yml` runs `scripts/check_constants_documented.py` and fails when a constant is neither documented nor allowlisted. Run it locally before committing: `python scripts/check_constants_documented.py`.
2. **New user-facing surface** (page, tab, card, gate, section) → add/update an F-ID in `docs/requirements.md`. *(Judgment item — CI can't detect "this is user-facing"; it's on the author.)*
3. **Shipped a queued item** → move it out of "What's queued → Genuinely not yet done" and add an entry to [docs/shipped-log.md](docs/shipped-log.md), and fix any memory that still calls it parked. At the start of any doc-sync, grep the queue's named functions/features against the code (and against `docs/shipped-log.md`) to catch items that already shipped.
4. **User-visible behaviour changed** → update the in-app User Guide (`app.py`, `elif page == "📖 User Guide":`).
5. **Non-obvious decision or rationale** → capture it in a memory file.
6. **Shipped a phased feature with a future Phase 2/3 gated on a trigger date/condition** → add that gate to "What's queued" below immediately, not just the roadmap/plan memory file. Memory only surfaces when a session's retrieval happens to match it; CLAUDE.md is loaded into *every* session. A gate that lives only in memory is invisible to any session that doesn't happen to ask about that specific feature — this is exactly how three Agentic Intelligence Roadmap Phase-2/3 gates (Thesis Red Team, Structural Scanner, Multi-Agent Debate) went untracked here until a 2026-07-26 user question surfaced them.

Only #1 is mechanically guarded; #2–#6 are the author's checklist. When wrapping up a feature or doing a "sync the docs" pass, run all six.

---

## Pointers

| Need | Where |
|---|---|
| Full dev context after time away | [DEVELOPMENT.md](DEVELOPMENT.md) |
| All decision thresholds | [stock_analyzer/constants.py](stock_analyzer/constants.py) |
| Functional requirements + operating policy | [docs/requirements.md](docs/requirements.md) |
| Architecture, data flow, scoring model, db schema, known behaviours | [docs/architecture.md](docs/architecture.md) |
| What's automated (pytest) vs. what needs manual testing, and when | [docs/testing-strategy.md](docs/testing-strategy.md) |
| Latest pytest pass/fail counts + coverage, and run history | [docs/test-results.md](docs/test-results.md) |
| Auto-memory index (durable feedback, threshold rationale, etc.) | `MEMORY.md` (Claude auto-memory, outside repo) |
| Full shipped-feature changelog (history, not needed every session) | [docs/shipped-log.md](docs/shipped-log.md) |

---

## What's queued

Last reconciled 2026-07-27 (Multi-Agent Debate Phase 3 — SHIPPED same day, a browsable "⚔️ Debate Log" 6th tab on 🧠 AI Insights, built forward-only despite thin data (~4 rows) per user decision; removed from below — this closes the LAST of the three originally-tracked Agentic Intelligence Roadmap gated phases; see `docs/shipped-log.md` and memory `project_agentic_intelligence_roadmap`). Previously reconciled same day (Structural Vulnerability Scanner Phase 2 — SHIPPED same day once its ≥3-day production-observation + ≥2-day `structural_scan_cache` history gates cleared, removed from below; see `docs/shipped-log.md` and memory `project_agentic_intelligence_roadmap`). Previously reconciled same day (Buy Candidates dual-verdict divergence — FIXED same day, removed from below; see `docs/shipped-log.md` and memory `project_verdict_divergence`). Previously reconciled same day (Summary page pointer-cards plan removed — CLOSED at 3 of 4 cards shipped, Watchlist card #4 deliberately dropped on cost grounds; full writeup in `docs/plans/summary-page-pointer-cards.md` and `docs/shipped-log.md`). Previously reconciled 2026-07-26 (added the three Agentic Intelligence Roadmap gated phases below — they previously lived only in memory `project_agentic_intelligence_roadmap` and had drifted invisible to this queue; see Definition-of-Done #6 above). Earlier items last audited against code 2026-07-13 (nav follow-up Phase A/B/C all shipped — Home is now fully de-tabbed). The macro/regime Phase-4 cluster is **done** (CPI NSA swap, drift detection, FRED `actual` — all shipped); don't re-chase it. The Agentic Intelligence Roadmap v1 (P1-P6) and v2 (D1-D3/O1-O6/D4) are both **fully shipped and closed**; Structural Vulnerability Scanner Phase 2 and Multi-Agent Debate Phase 3 (the last two of the three originally-tracked gated phases) both shipped 2026-07-27 — only Thesis Red Team Phase 2 (below) remains open from that whole initiative.

**Genuinely not yet done** (verify against code before starting — statuses live in the named plan/memory):
- **Today's-P&L cash/flows + broker reconciliation** — **PARKED 2026-06-24 (Tier B is sufficient).** Robinhood Agentic Trading / MCP path analyzed (read access to all accounts via `agent.robinhood.com/mcp/trading`; Claude is a supported agent; auth is interactive/desktop-only so the app/cron can't be the MCP client → Model C = Claude bridges RH→Supabase snapshot→app renders). **Decision: HOLD until beta matures** (user chose). Full analysis + revisit triggers in memory `project_today_pnl_scope`. Don't re-propose unless asked.
- **NYSE calendar** — extend `NYSE_HOLIDAYS`/`NYSE_EARLY_CLOSES` before 2029 (hardcoded, last year = 2028; not urgent mid-2026).
- **Deterioration-card hysteresis** — **PARKED until a flicker is actually observed** (user had NOT noticed any toggling as of 2026-06-28; the existing 2-of-3 below-MA entry confirmation + settling grace already damp most noise). **NOT the "small UX polish" it was once labelled:** it changes WATCH/TRIM/EXIT *recommendation* behaviour (decision-logic → Opus-review-worthy), needs a **new policy constant** (the asymmetric clear-band buffer → `constants.py`, a policy decision to set with the user). **Data gap closed 2026-07-21:** per-ticker day-over-day tier state now exists via `exit_signals` (shipped 2026-07-18, commit `f86147d`; cron-covered as of 2026-07-21 — see docs/shipped-log.md), so this is purely a decision-logic + policy-constant item now, not a missing-data problem. Trigger to revisit: a deterioration card seen toggling on/off across days. `exit_advisor.classify_deterioration_tier`; memory project_exit_discipline.
- **Broker CSV v2 — cash events** — v1 (shipped, F-87) scopes to Buy/Sell trades only; dividends/ACH/interest/fees are counted in a skipped-rows summary but deferred to v2 (→ the `account_flows` ledger on 💰 Account). Plan `docs/plans/broker-statement-import.md`; memory `project_broker_import`.
- **Research Scorecard Phase 3 — Engine vs Analyst Calibration** — **DEFERRED, not yet started.** When saved analyst consensus was Bullish and the engine composite was below `COMPOSITE_BUY` (65) — or vice versa — a 2×2 disagreement matrix would show who was right ~60 days later (subsequent return per quadrant). **Trigger to build:** `composite_score_at_save` populated on ≥20 `analyst_coverage` rows (that column only started populating with saves made after 2026-07-22; needs ~4-6 weeks of real usage to accumulate). Full spec in `docs/plans/analyst-research-accountability.md` (Phase 3 section). Don't re-propose until the trigger condition is met.
- **Thesis Red Team Agent Phase 2** (F-196) — Haiku counter-evidence narrative + a pre-mortem loop reading back the user's saved `trades.premortem_commitment` as context. Phase 1 (0–100 erosion score, ⚠️ Red Team tab) shipped 2026-07-23. **Trigger:** ~1 week of production observation (~2026-07-30) **AND** its own Opus plan review before build starts — neither has happened yet. `thesis_red_team.py`; memory `project_agentic_intelligence_roadmap`.


**Recently shipped:** moved to [docs/shipped-log.md](docs/shipped-log.md) on 2026-07-22 — same content, verbatim, just relocated so it isn't injected into every session's context (it had grown to ~76KB / 86% of this file). Grep/Read it for the "did we already ship this?" check in Definition-of-Done step 3, or for full feature history.
