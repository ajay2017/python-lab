# Claude Project Guidance — DRISHTA · Beyond Noise

A directive distillation for any Claude session working in this repo. Read first; obey throughout. Detail lives in [DEVELOPMENT.md](DEVELOPMENT.md) and [docs/](docs/).

---

## Project orientation

Personal portfolio intelligence app for a single user. Streamlit Community Cloud deploy; auto-deploys from `main`. Never run locally — the secrets architecture assumes Streamlit Cloud (see [DEVELOPMENT.md](DEVELOPMENT.md)).

## Operating posture

**The app decides, it does not inform.** Recommendations are issued as actionable calls; gates are hard suppressions with visible banners, not soft warnings. When in doubt, recommend nothing rather than recommend wrongly. See [docs/requirements.md §2A](docs/requirements.md).

---

## Hard rules

1. **Never hardcode decision thresholds.** Every gate / threshold / boundary value lives in [`stock_analyzer/constants.py`](stock_analyzer/constants.py). Import from there. Changing a value there is an investment-policy decision — discuss with the user before changing.

2. **Never disable RLS.** Supabase tables are protected by `FOR ALL TO service_role` policies. The Streamlit secret `[supabase] key` must be the service-role / secret key (not publishable). If you see "row-level security blocking" errors, the fix is to swap secrets and reboot the app via Streamlit Cloud → Manage app → Reboot — not to disable RLS.

3. **Never run the app locally to test changes.** Push to `main`, wait ~2 min for Streamlit Cloud auto-redeploy, hard-refresh the browser (Ctrl+F5).

---

## Coordination pattern

Features that own state publish to `st.session_state`; downstream features read and gate. When a producer fails, set the cache to `None` (not an empty container) so consumers can detect "offline" rather than silently disabling gates. Existing cache keys: `_port_risk_cache`, `_risk_high_alerts_cache`, `_grow_today_sectors_cache`, `_grow_composites`, `_grow_composites_coverage`, `_daily_brief_offline`.

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
- **Claude-authored commits** end with the trailer `Co-Authored-By: Ajay with Claude Opus 4.8 <ajay.x.ku@accenture.com>`, written via `.git/COMMIT_MSG.txt` + `git commit -F` (dodges PowerShell here-string mangling).

## Documentation integrity (zero-hallucination)

This is a decision-making app: a **wrong value in a doc erodes trust in the app itself**, so docs are held to the same bar as code.

- **Transcribe every threshold / constant / file:line from the source, never from memory.** Before writing a number into any doc, open `stock_analyzer/constants.py` (or the actual code) and copy it. Recalled values in `MEMORY.md`/summaries reflect what was true *when written* — verify against HEAD.
- **Never invent** function names, constant names, session keys, UI text, or dates. If you can't confirm it in code, don't write it.
- **Doc edits that carry policy values stay on the Opus lead** — do not delegate constants-table / gate / requirements writes to the Haiku doc-writer (it has hallucinated exactly these: invented function names, a wrong constant value, a fabricated date). The doc-writer is for prose/comments after the facts are pinned.

---

## Pointers

| Need | Where |
|---|---|
| Full dev context after time away | [DEVELOPMENT.md](DEVELOPMENT.md) |
| All decision thresholds | [stock_analyzer/constants.py](stock_analyzer/constants.py) |
| Functional requirements + operating policy | [docs/requirements.md](docs/requirements.md) |
| Architecture, data flow, scoring model, db schema, known behaviours | [docs/architecture.md](docs/architecture.md) |
| Auto-memory index (durable feedback, threshold rationale, etc.) | `MEMORY.md` (Claude auto-memory, outside repo) |

---

## What's queued

Last reconciled 2026-06-28 (audited against code, not memory). The macro/regime Phase-4 cluster is **done** (CPI NSA swap, drift detection, FRED `actual` — all shipped); don't re-chase it.

**Genuinely not yet done** (verify against code before starting — statuses live in the named plan/memory):
- **Rate-limit resilience Phase 3** (FMP daily soft-cap) — **DEFERRED as a safety-net (decided 2026-06-24, measured):** FMP usage = 88/250 today after Phase 2 cut it ~6× from the pre-fix ~650/day runaway; free plan confirmed adequate, no build/buy. Revisit only if a weekday creeps toward ~200. Plan: [docs/plans/rate-limit-resilience.md](docs/plans/rate-limit-resilience.md) §Phase 3.
- **Today's-P&L cash/flows + broker reconciliation** — **PARKED 2026-06-24 (Tier B is sufficient).** Robinhood Agentic Trading / MCP path analyzed (read access to all accounts via `agent.robinhood.com/mcp/trading`; Claude is a supported agent; auth is interactive/desktop-only so the app/cron can't be the MCP client → Model C = Claude bridges RH→Supabase snapshot→app renders). **Decision: HOLD until beta matures** (user chose). Full analysis + revisit triggers in memory `project_today_pnl_scope`. Don't re-propose unless asked.
- **NYSE calendar** — extend `NYSE_HOLIDAYS`/`NYSE_EARLY_CLOSES` before 2029 (hardcoded, last year = 2028; not urgent mid-2026).
- **Deferred (low priority):** pullback-awareness Phase 3 market-risk dial · deterioration-card hysteresis.

**Recently shipped (do not re-chase):**
- **AI Intelligence Layer (own 🧠 AI Insights page; LLM narrates, never gates; strictly additive / zero runtime dependency; plan docs/plans/ai-intelligence-layer.md; reqs §3.12 F-150–F-153):** **F-1 Thesis Tracking** (INTACT/WEAKENING/BROKEN per held position w/ `user_thesis`; `thesis_advisor.py`/`thesis_reviews`), **F-3 Weekly Debrief** (4-section Sunday email + on-demand; `debrief_advisor.py`/`weekly_debriefs`; light-mode email), **F-4 Monthly Intelligence Report** (Q0 entry-quality + Q1 signal-discipline + a pattern; `intelligence_report.py`/`monthly_reports`; first-Sunday cron + on-demand) — all SHIPPED 2026-06-27. **F-2 earnings-call intelligence DEFERRED** (transcript-API budget). **AI surfaces patterns but NEVER tunes a gate**; prompts interpolate thresholds from `constants.py` at call time (never hand-transcribed).
- **F-4 count-integrity + freeze hardening (2026-06-28, 5a23206→f2f5d21):** headline acted/missed are **distinct tickers** via `signal_flow` (not per-surfacing); narration **count-discipline** (no fabricated "graded-outcomes" tally, no count > distinct total); report **frozen as an immutable artifact** (`recommendations_history.report_viz_snapshot` → `monthly_reports.viz_json` jsonb; needed a one-time additive DDL, applied; old rows live-recompute); **period picker** (latest-per-calendar-month); `build_report_package` computes on the **full** enriched first (cross-rec_type acted safeguard) then scopes means to new_pick; per-band prose alpha = all-graded `avg_alpha` (matches the band chart). reqs §3.13 retrospective scorecard helpers (`distinct_missed`/`missed_split`/`signal_flow`/`report_viz_snapshot`) also drive the 📊 Recommendations History page + Overview/Grow Today Sankeys.
- Headless GitHub Actions alert cron (first non-Streamlit runtime; `cron_runner.py` / `headless_alert_engine.py` / `.github/workflows/alerts.yml`) delivers ALL three out-of-app jobs — exit Phase 3 protective alerts (premarket), pullback Phase 2 reactive drawdown email + Today's-P&L EOD snapshot (eod). LIVE 2026-06-24 (9add28f→cb37862; plan docs/plans/email-alerts-cron.md). The Sunday thesis lane now also chains F-3 debrief + F-4 monthly (`monthly` dispatch mode).
- Brief tone-staleness annotation + Action Log Phase B "log this trim" (307cac6).
- SDLC docs backfill + zero-hallucination doc-integrity rule (b3d444f, a4d9db4).
- Cross-check validator-health gate (220ab44) — no false "sources disagree" banner during a Yahoo outage; also closes the 401/crumb item (absorbed, no fragile retry). Bundle-load deferred queue now empty.
- Catalyst Watch holdings-earnings fix (19ba98a + f49ace0 + ca12cce) — decoupled FMP earnings backfill from `.info`-sparse (re-arms earnings gates), gave the holdings tier the SAME FMP-calendar→per-name-yfinance fallback as Radar (coverage parity), and ET-corrected the FMP earnings date. Confirmed working. Earnings-window literals → named constants (5b8306b). Future lever: Finnhub as a 3rd earnings source if yfinance per-name also fails from the datacenter IP.
- **Account-baseline foundation (own 💰 Account page; `stock_analyzer/account.py` + `account_cash`/`account_flows` tables; plan docs/plans/account-baseline.md):** v1 total value + cash% + true concentration (467b431/069b199), v2 flows ledger + growth-vs-contributed-capital (15b87b1), v3 money-weighted (Modified Dietz) return + annualized (0488cdc), v4 signed net cash = margin/liability awareness (f0abdf7). Reconciles to the user's Robinhood. **Display-only — gates still equity-weight.** Reuses the same tables the Robinhood MCP sync would auto-fill. **Deferred policy decision (user hasn't decided):** move the 15%/35% concentration gates to account-basis? (true-concentration view now shows the real tradeoff). User Guide + architecture §6.7/6.8 documented.
