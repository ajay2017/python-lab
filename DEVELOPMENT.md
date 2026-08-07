# Development Context — DRISHTA · Beyond Noise

This file is the "context capsule" for getting back into the project after working on something else. Read top-to-bottom in ~5 minutes and you should be able to pick up where you left off.

For deeper detail see [docs/requirements.md](docs/requirements.md) and [docs/architecture.md](docs/architecture.md).

---

## What this project is

A personal portfolio intelligence app for a single user (Ajay). Live deploy: **Streamlit Community Cloud**, repo: **github.com/ajay2017/python-lab**, branch: **main**.

**Operating posture:** the app *decides*, it doesn't merely inform. Recommendations are issued as actionable calls; gates are hard suppressions, not soft warnings. See [docs/requirements.md §2A](docs/requirements.md#2a-operating-posture-and-decision-policy).

---

## Recently shipped (May 2026, post-audit)

The "decides not informs" push turned several informational surfaces into concrete, consolidated, internally-consistent actions:

- **Movers discovery** — `scan_movers()` over a ~200-name `discovery_universe` surfaces 1-day breakouts outside the curated universe, composite-gated, fed into the SAME "New Positions to Initiate" list (NOT a separate dev-facing section). Flat-day exemption is deliberate — see memory `project_decision_thresholds`.
- **Review Before Close directives** — every item now carries a quantitative action (trim N shares, raise stop to $Y) instead of "consider trimming" prose. Action types: WATCH / TIGHTEN_ONLY / TRIM_AND_TIGHTEN / TRIM_TO_TARGET / PROTECTIVE_TRIM.
- **Act Today consolidation** — structured directives + per-ticker consolidation (a mechanical exit suppresses a risk-trim on the same ticker; multiple risk flags merge). Fixed MU-appears-twice.
- **Action Log (Phase A)** — `manual_stops` table closes the recommend→act→log loop; one-directional override (tighten-only), 📌 badges, auto-clear on shares→0.
- **Two-column offense/defense Brief** — left = Grow Today + More Buy Candidates; right = Act Today + Review Before Close. Buy Candidates de-duped against Grow Today.
- **SELL integrity guard + double-submit dedupe** — SELL validates against `recalculate_from_trades()` (same source as the drift detector); identical `(ticker, action, shares)` within 15 s rejected. Fixed the COIN double-SELL drift. See memory `feedback_validation_reads_detector_source`.

## Recently shipped (June 2026)

The loss-protection / discipline push — exit logic, concentration enforcement, and the first out-of-app runtime:

- **Concentration / sizing discipline** (`ebdf255`, `concentration.py`) — closes the entry-time enforcement gap where manual journal buys bypassed sizing ceilings (a name had hit ~23%). Entry nudge + `single_name_concentration` rec + high-beta cluster line. See memory `project_concentration_discipline`.
- **Exit-discipline** (`exit_advisor.py`) — Phase 1 (`753c851`) idiosyncratic deterioration WATCH/TRIM/EXIT off drawdown-from-peak; Phase 1.1 (`88f0355`) peak re-anchor on material adds; Phase 2 (`1c5c56d`) market-wide `risk_off_derisk` overlay (fragile book AND SPY<200-DMA-or-VIX≥25 → trim top-3 beta contributors, single-surface). See memory `project_exit_discipline`.
- **Headless protective-alert cron — the SECOND runtime** (`9add28f`→`cb37862`; `cron_runner.py` / `headless_alert_engine.py` / `notify.py` / `bundle_loader.py` / `.github/workflows/alerts.yml`) — GitHub Actions, no Streamlit. Delivers all three out-of-app jobs: exit Phase 3 protective email (premarket), pullback-awareness Phase 2 reactive drawdown email + Today's-P&L EOD snapshot (eod). Resend HTTP email; `alert_state` per-ET-day dedup; DST-straddling UTC slots; inert without secrets. See memory `project_email_alerts_cron`.
- **Brief tone-staleness reconciliation** (`307cac6`) — annotate-only amber note when a stale pre-market tone contradicts live futures; never flips tone/gates. See memory `project_brief_tone_staleness`.
- **Today's-P&L Tier B** (`bafcf8d`) — true positions day-P&L via equity-delta against the `daily_snapshots` baseline (needs one-time DDL; inert until then). See memory `project_today_pnl_scope`.
- **AI Intelligence layer (own 🧠 AI Insights page; LLM narrates, never gates; strictly additive / zero runtime dependency)** — F-1 thesis tracking (`thesis_advisor.py` / `thesis_reviews`), F-3 weekly debrief (`debrief_advisor.py` / `weekly_debriefs`), F-4 monthly intelligence report (`intelligence_report.py` / `monthly_reports`) — all SHIPPED 2026-06-27 (Sunday cron + on-demand). **F-5 thesis authoring** (✨ Draft thesis on the BUY form → an editable, falsifiable candidate thesis → `trades.user_thesis` + `thesis_source`) shipped 2026-06-28 (`a7f22da`), plus an F-1 evidence-sourcing fix (`955b071` — shared `thesis_advisor.bundle_evidence`, so review and authoring read the bundle through one path). F-2 earnings-call intelligence DEFERRED (transcript-API budget). Plans: `docs/plans/ai-intelligence-layer.md` + `docs/plans/thesis-authoring-analyst-desk.md`; memories `project_ai_integration_strategy` + `project_thesis_authoring`.

## Recently shipped (July 2026)

The "leverage my paid research" push — a full analyst-intelligence feature end-to-end, plus an AI-page information-architecture refresh:

- **Analyst Coverage — "Ideas Inbox"** (`analyst_intel.py` / `analyst_coverage` table; own surface on 🧠 AI Insights; reqs F-154/F-154a/F-154b; plan `docs/plans/analyst-coverage.md`; memory `project_analyst_coverage`) — paste CNBC Pro / broker research → LLM (`extract_report` → `list[dict]`) extracts **atomic per-firm facts as ONE record per covered stock** (a multi-stock "top picks" roundup never merges; `derive_consensus` computes avg/high/low PT + consensus label in pure Python so no arithmetic is hallucinated) → editable **N-card preview** → Supabase. **Awareness-only — never gates, scores, or moves a verdict.** Phase 1 Ideas Inbox capture (`19620b3`); Phase 2 (`f0cdd6b`) = 🏦 Analyst Coverage tab on 📈 Analysis (reconciles the provider `targetMeanPrice` consensus vs your saved research) + F-1 thesis reviewer ingests consensus as **CONTEXT ONLY** (never upgrades a verdict); Phase 3 (`ad4e5d7`) = Grow Today "New Positions to Initiate" card annotation (display-only); multi-stock extraction fix (`dd4023a`) + preview-hardening for legacy session state (`cacc0ab`). **Deferred by choice:** Brief awareness chip (calm-advisor persona), analyst-conviction tie-breaker (would influence recommendation order → violates awareness-only), PDF upload.
- **🧠 AI Insights page IA refresh** (`b8bc336`) — flat vertical scroll → persistent **"At a glance" status strip** + **cadence tabs** (🩺 Positions · 📅 Debriefs · 🏦 Research). Structural re-housing only (no logic change); the status-strip + cadence-tabs pattern is the template for the eventual broader UX pass (memory `feedback_ui_polish`).
- **CI Node 24** (`3295598`) — `actions/checkout@v5` + `actions/setup-python@v6` (GitHub's Node.js 20 runner deprecation); no workflow-behaviour change.
- **pyarrow production segfault fix** (2026-07-10 → 07-12; `9e5c913`→`d6dfad9`) — an initial numpy ABI pin (`751cd4b`, 07-10) didn't hold; Streamlit Cloud deploys kept segfaulting on the first `st.dataframe` render. Root-caused to `requirements.txt` never pinning pyarrow — streamlit's own dependency spec deliberately leaves it unbounded — with the resolver almost certainly pulling a ~2-day-old pyarrow 25.0.0 release. Pinned `pyarrow==24.0.0` (`d6dfad9`); also migrated the remaining 4 `st.data_editor` calls off deprecated `use_container_width`. See `docs/architecture.md` §1 (tech-stack table).
- **UX review remediation, 5 phases** (`1b33c8c`→`dfa1b77`; per `docs/reviews/2026-07-12-UX-review.md`) — dev-string/identifier leaks stripped from user copy (`1b33c8c`); 11 hardcoded display values single-sourced to `constants.py` (`699104f`); Home's original 11 tabs consolidated to a promoted Today's Brief section + 5 grouped tabs (`9c1de1f` — see `docs/architecture.md` §10 "Home page tab consolidation"); 4 differently-worded empty-state messages consolidated (`fa63ba4`); WATCH/MONITOR terminology standardized to "Watch" + remaining watchlist code leaks fixed (`dfa1b77`).
- **Nav follow-up, 3 phases** (`2a61398`, `bb508c7`, `a810588`) — a "📊 Portfolio" Home tab collided with the pre-existing PORTFOLIO sidebar nav group. Phase A (`2a61398`) extracted Portfolio Allocation + Analytics into a new standalone page; Phase B (`bb508c7`) renamed Home's "AI Brief" tab to "AI Snapshot"; Phase C (`a810588`, 2026-07-13) split the remaining "Risk & Alerts" tab into two more standalone pages ("🔗 Risk Analysis", "⚠️ Alerts & Actions") and dropped `st.tabs()` from Home entirely — Home is now Today's Brief (promoted) + Evening Debrief + AI Snapshot as three sequential full-width sections. See `docs/architecture.md` §10 ("Nav follow-up — Portfolio Allocation extraction + AI Snapshot rename" / "Nav follow-up Phase C").

---

## Coming back to this project — checklist

When you open this folder after time away:

1. **Open the folder in VS Code.** The `.venv/` is local; VS Code auto-detects it as the Python interpreter for this workspace.
2. **Skim this file.**
3. **Open `MEMORY.md`** (the auto-memory index) to refresh on durable decisions and feedback.
4. **Check `git log --oneline -10`** to see what the last commits were.
5. **Glance at the Phase 4 todo list** (see "Outstanding Work" below) for what's still queued.
6. **Open the deployed app** and click through Home / Today's Brief / Watchlist / Stock Analysis to confirm everything still loads (the standing post-deploy smoke test — see [docs/testing-strategy.md](docs/testing-strategy.md) §4.1).

You're ready to work.

---

## Project layout (the modules that matter)

```
python-lab/
├── app.py                         UI orchestration; multi-page via st.radio
├── DEVELOPMENT.md                 This file
├── MEMORY.md                      Auto-memory index (durable decisions / feedback)
├── requirements.txt               Python dependencies
├── runtime.txt                    Python 3.12
├── assets/
│   └── drishta_logo.png           Brand asset (single combined image)
├── docs/
│   ├── requirements.md            Functional + non-functional reqs + operating posture
│   ├── architecture.md            Module map, data flow, scoring model, db schema
│   ├── testing-strategy.md        Automated (pytest) vs. manual testing — what covers what, and when
│   └── test-results.md            Running log of pytest pass/fail + coverage, updated each run
├── tests/                         pytest regression suite over stock_analyzer/ pure logic (see docs/plans/test-automation.md)
└── stock_analyzer/
    ├── constants.py               SINGLE SOURCE OF TRUTH for decision thresholds + DATA_* provider config
    ├── data.py                    Public market-data API (fetch_* + crosscheck_*); routes to providers/ orchestrator
    ├── providers/                 Multi-source data layer: base + yfinance/finnhub/fmp adapters + orchestrator + selftest
    ├── daily_briefing.py          Grow Today (+Movers) / Act Today / Buy Candidates / Review — directives + consolidation
    ├── scanner.py                 Curated scan (+Watchlist) and scan_movers() 1-day-gainer pass
    ├── discovery_universe.py      Broad ~200-name universe for movers discovery
    ├── risk_advisor.py            Beta / Sharpe / volatility / drawdown recs
    ├── rebalancer.py              Trim / add actions; news + risk-trim aware
    ├── watchlist_advisor.py       ENTER_NOW gate with portfolio-fit checks
    ├── quick_research.py          5-bullet ad-hoc research with portfolio fit
    ├── tax_advisor.py             HARVEST subordinated to Buy/Strong Buy signal
    ├── news_intelligence.py       Curated news + critical/warning alerts
    ├── macro_calendar.py          Static event backbone + FRED enrichment
    ├── portfolio.py               build_portfolio_df; stop integrity gate; manual-stop override merge
    ├── db.py                      Supabase persistence (service-role only); manual_stops + trade-replay
    └── ...                        See architecture.md for full module list
```

---

## Decision constants & operating posture (CRITICAL)

Every threshold the app uses to gate, suppress, or downgrade a recommendation lives in [stock_analyzer/constants.py](stock_analyzer/constants.py). **Do not hardcode values elsewhere.** Changes here are investment-policy decisions, not code tuning.

| Constant | Value | Type |
|---|---|---|
| `PORTFOLIO_BETA_CEILING` | 1.4 | Hard breach |
| `PORTFOLIO_BETA_ELEVATED` | 1.3 | Soft warn |
| `TICKER_BETA_HIGH` / `_CRITICAL` | 1.5 / 1.8 | Beta envelope |
| `SECTOR_CEILING` / `_ELEVATED` | 35% / 25% | Concentration |
| `SINGLE_NAME_CEILING` | 15% | Hard cap |
| `COMPOSITE_BUY` / `_HOLD` | 65 / 44 | Signal boundary |
| `RISK_PCT_PER_TRADE` | 1.5% | Sizing |
| `EARNINGS_IMMINENT_DAYS` | 7 | Caution window |
| `MACRO_IMMINENT_DAYS` | 3 | Hard suppression |

Rationale for each value: memory file `project_decision_thresholds.md` (Claude's auto-memory, stored outside this repo under the user's local `.claude` profile — not a repo-relative link; same content also summarised in requirements §2A).

---

## How to run / deploy

**Don't run the app locally.** It's designed to run on Streamlit Community Cloud where the secrets live.

| Action | How |
|---|---|
| Deploy a change | `git push origin main` — Streamlit Cloud auto-redeploys in 1–3 min |
| See the new deploy | Hard-refresh the browser tab (Ctrl+F5 / Cmd+Shift+R) |
| Force a session reset (when a cached_resource is stale) | Streamlit Cloud → app → **Manage app → Reboot** |
| View deploy logs | Streamlit Cloud → app → Manage app → Logs |

**Scheduled cron jobs run on Railway, not GitHub Actions** (migrated 2026-08-07 — GitHub's `schedule` trigger is best-effort and was hit by a platform-wide incident on 2026-08-06). Five dedicated Railway Cron Job services in project `endearing-magic` (`cron-premarket`, `cron-scan`, `cron-intraday`, `cron-eod`, `cron-thesis`) each run `python cron_runner.py` with their own schedule + `ALERT_RUN_MODE`, sharing the web service's Supabase/API secrets via Railway **Shared Variables**. `.github/workflows/alerts.yml` is now `workflow_dispatch`-only — manual re-runs / ad hoc testing, no longer the scheduled entry point. Full rationale, per-service cron expressions, and setup gotchas (config-as-code precedence, variable-reference fragility on service rename) in memory `project_cron_railway_migration`.

---

## Secrets (set in Streamlit Cloud dashboard, never committed)

Under **App → Settings → Secrets** in `secrets.toml` TOML format:

```toml
[supabase]
url = "https://<your-project-id>.supabase.co"
key = "sb_secret_***"          # MUST be the service-role / secret key, not publishable

ANTHROPIC_API_KEY = "sk-ant-..."   # Anthropic API for AI Snapshot, AI Insights, and the broker-import ticker fallback
OPENAI_API_KEY    = "sk-..."       # optional
GOOGLE_API_KEY    = "AIza..."      # optional

[fred]
api_key = "..."                     # optional, enriches macro calendar with released values
```

**Security model:** RLS is enabled on all Supabase tables with `FOR ALL TO service_role` policies. The publishable/anon key has no matching policy — defense-in-depth in case the publishable key ever leaks. If you ever swap secrets, you MUST reboot the app via Manage app (the Supabase client is a process-level singleton — `db._CLIENT` — so a swap is only picked up on restart). Credentials resolve env-first (`SUPABASE_URL`/`SUPABASE_KEY`) then `st.secrets`, so the headless alert cron and the app share one path.

---

## Common workflows

### Making a code change
```
# In c:\Users\ajay.x.ku\python-lab\python-lab
git pull
# edit code
git add <files>
git commit -m "type(scope): short imperative summary"   # see Commit messages below
git push
# wait ~2 min, hard-refresh deployed app
```

### Commit messages (Conventional Commits)

Standard format so history stays readable and tooling-friendly:

```
type(scope): summary

Body — what changed and especially WHY (wrap ~72). Bullets fine.

BREAKING CHANGE: ...        # if applicable
Refs #123                   # if applicable
```

- **type**: `feat` | `fix` | `docs` | `refactor` | `perf` | `test` | `build` | `ci` | `chore` | `revert`
- **scope** (optional): the area — e.g. `pnl`, `brief`, `risk`, `db`, `constants`, `scanner`, `ui`
- **summary**: imperative mood, lowercase, ≤72 chars, no trailing period
- **Threshold/gate changes** (`stock_analyzer/constants.py`) are investment-policy decisions — say so in the body and name the constant + old→new value
- Claude-authored commits end with: `Co-Authored-By: Ajay with <model> <ajay.x.ku@accenture.com>` — use the actual model name from the session context (e.g. `Claude Sonnet 4.6`, `Claude Opus 4.8`)

One-time setup per clone (wires the editor to pre-fill the format from `.gitmessage.txt`):

```
git config commit.template .gitmessage.txt
```

Examples:

```
feat(pnl): add Tier B true day-over-day P&L
fix(brief): split macro-blocked names out of the funnel caption
docs(architecture): record the fragility gauge
```

### Quick syntax check before pushing
```
python -c "import ast, io; ast.parse(io.open(r'app.py', encoding='utf-8').read()); print('OK')"
```

### Regression tests before touching scoring/gates
`tests/` holds a `pytest` regression suite over `stock_analyzer/`'s pure-logic
modules (scoring, concentration, gates) — see `docs/plans/test-automation.md`
for scope/rationale. One-time setup: `pip install -r requirements-dev.txt`
(dev-only, not installed on Streamlit Cloud/Railway). Run before committing
any change to `stock_analyzer/constants.py`, `scoring.py`, `concentration.py`,
or similar decision-logic modules:
```
pytest tests/ -v
```
Log the outcome in [docs/test-results.md](docs/test-results.md) if it's worth
tracking (a new batch, a failure, a coverage shift) — that file is the running
history of pass/fail counts and coverage, not a one-time snapshot.

**As of 2026-07-27 this is also mechanically enforced**, not just documented:
`.claude/hooks/pre_tool_checks.py` blocks a `git commit` touching
`stock_analyzer/`/`tests/` (and always blocks `git push`) if `pytest tests/`
fails — see [docs/testing-strategy.md](docs/testing-strategy.md) §2 for the
exact behavior and its caveats (Claude Code sessions only, takes effect after
a session restart per memory `feedback_hook_enforcement`).

A GitHub Actions workflow (`.github/workflows/tests.yml`) also runs this on
push/PR touching `stock_analyzer/**` or `tests/**` — it's a pre-push safety
net (red ❌ on the commit), not a deploy gate; neither Streamlit Cloud nor
Railway consults GitHub Actions.

pytest only covers `stock_analyzer/`'s pure logic — it does NOT cover `app.py`
UI, live data providers, Supabase, cron jobs, or whether the recommendations
are actually good. See [docs/testing-strategy.md](docs/testing-strategy.md)
for the full split and what manual checking still needs to happen for each
kind of change.

### Switching to another project (and coming back)
- This project's `.venv/` stays put — no cleanup needed
- Other project: separate folder + separate `.venv/`
- Open one folder per VS Code window; VS Code auto-detects the venv for each

---

## Outstanding work (queued for Phase 4)

The audit and live-market validation surfaced these items; none blocking:

**Macro calendar / regime — ✅ CLUSTER COMPLETE (2026-06-02 audit).**
All originally-queued items are implemented and live. Verified against code:
- ✅ Macro calendar drift detection (Tier 1): `drift_days` computed in `macro_calendar._fetch_fred`; surfaced as a "⚠ Drift Nd — FRED released …" badge in `app.py` (`_release_status_html`).
- ✅ Auto-released flag (Tier 2): `released` flag set per event; the new-pick macro gate (`daily_briefing.py`) already filters `0 ≤ days ≤ MACRO_IMMINENT_DAYS` (forward = unreleased), and a "✓ Released" badge renders in the calendar.
- ✅ "⏳ Awaiting FRED update" placeholder: rendered for past events with no `actual` yet.
- ✅ "🔄 Refresh macro" button: bypasses the per-day `_macro_cal_{date}` session cache.
- ✅ CPI series CPIAUCSL (SA) → **CPIAUCNS** (NSA): matches the media-headline number (`_FRED_MAP` + `detect_macro_regime`).
- ✅ "controlled inflation" gate: rate-cut regime is hard-gated off when CPI > `REGIME_CPI_CONTROLLED_MAX` (2.5%). The CPI ladder (2.5 / 3.0 / 4.0) now lives in `constants.py` as `REGIME_CPI_CONTROLLED_MAX / _ELEVATED_MIN / _HOT_MIN` (was hardcoded inline; centralized 2026-06-02 per hard-rule #1, values unchanged).
- ✅ Don't auto-claim "in-line" without consensus: `classify_scenario` returns `None` with no reference; UI shows "⬜ vs baseline (no consensus)" instead of "In-Line" when only the model baseline is available.

_Remaining (genuinely minor, optional):_ the macro pick-gate uses the static event date for "forward", not FRED's `released` flag — so in the rare drift case where FRED releases a day before the static date, the gate could suppress for one extra day. The drift badge already surfaces this visually; wiring `released` into the gate itself is an edge-case refinement only.

**Long-term (optional):**
- BLS Calendar API integration to replace `_STATIC` dates entirely (Tier 3)

**Minor cleanup:**
- Dead "Weak Hold" / "Avoid" code paths (no producer emits them)
- R:R targets dict validation (defensive)
- Trim trade journal entry text suggestion

---

## Phase 5+ queued

**Evening Debrief — ✅ SHIPPED** (see `stock_analyzer/evening_debrief.py`; renders as a Home section, after Today's Brief). This queue item was stale — removed 2026-07-13.

**Brokerage sync (eliminate manual journaling + the drift class of bugs):**
- One-way pull from brokerage API → `trades` + `holdings` tables. Brokerage state is canonical for shares and cost basis; app retains the decision-context columns (`signal_seen`, `followed_signal`, `deviation_reason`, `lesson`).
- Auto-detect drift between brokerage truth and app state; surface a side-by-side diff for user approval before applying.
- Triggered: on-demand "🔁 Sync brokerage" button + optional scheduled pull at market close.
- Removes the manual-entry path that produced the May 2026 drift mess (15 unmatched SELLs, multi-round SQL backfill, eventual full rebaseline on 2026-05-27). Drift recovery is documented in memory `feedback_trade_drift_recovery`.
- Brokers to scope: Fidelity, Schwab/TD, IBKR, Robinhood (varying API quality). Auth via OAuth + token storage in Streamlit secrets.

**Future expansion (optional) — discovery universe:** widen `discovery_universe` to full S&P 500 (~500) or Russell 1000 (~1000) — bigger net, slower scan, more yfinance flakiness. Or swap the static list for a live source (Wikipedia SP500 scrape needs lxml; paid screener API = Polygon/Twelvedata/Alpha Vantage, reliable but adds key plumbing). The curated static list was chosen first for zero new deps and zero runtime-scrape risk; cost is a manual refresh a few times a year. The mover signal is also 1-day gain only — could add sustained-momentum and most-active lenses later.

**Action Log — Phase B/C ✅ SHIPPED (2026-06-24, `307cac6`):** Phase A (manual stop override) + Phase B "📒 Log this trim" button on Review trim cards (pre-fills the Trade Journal SELL form, then the rec stops re-firing once holdings recompute). Phase C (protective-trim variant) folded into B — `PROTECTIVE_TRIM` resolves its `trim_ticker` before the button gate, per-render card index keeps keys collision-proof. See memory `project_action_log_subsystem`.

**Multi-source market-data layer — ✅ SHIPPED & LIVE (2026-06-01):** the single-source yfinance dependency is removed. Code in `stock_analyzer/providers/` (base abstraction + yfinance/finnhub/fmp adapters + orchestrator + `_util` + `selftest`); `data.py` keeps the SAME public `fetch_*` signatures and routes through the orchestrator when `DATA_MULTISOURCE_ENABLED` (instant rollback = set False).
- **Live prices:** Finnhub real-time PRIMARY, gap-fill to yfinance(delayed)→FMP (`DATA_LIVE_PRICE_ORDER`).
- **History / bundle / indices / risk-free:** yfinance primary, failover to FMP (`DATA_PROVIDER_ORDER`) — so composite scoring survives a yfinance rate-limit (observed live).
- **Price cross-check (held positions, fail-loud banner):** prev_close strict 0.5% (`DATA_XCHECK_PREVCLOSE_TOL_PCT`) / live loose 3.0% (`DATA_XCHECK_LIVE_TOL_PCT`).
- **Source transparency:** price-strip caption + Data Health sidebar show the real provider per call.
- Keys in Streamlit secrets (`FINNHUB_API_KEY`, `FMP_API_KEY`); `_util.get_secret` tolerates TOML section-nesting + env-var fallback. Validate adapters offline with `python -m stock_analyzer.providers.selftest AAPL MSFT` (env-var keys). **Alpha Vantage rejected** (~25 req/day free tier).
- Full spec + per-data-type chain table + status in memory `project_second_data_source`; architecture §4.0.4 + §11; requirements §3.10.
- **Optional follow-ups remaining:** Analysis-page per-ticker cross-check status; harden FMP `news`/`earnings` stable endpoints (currently empty → neutral sentiment / no earnings date on the FMP failover path only).

---

## What NOT to do

- **Don't disable RLS.** The current Supabase setup is secured with RLS + service-role-only policies. If you see "row-level security blocking" errors, the Streamlit secret is on the wrong key (anon instead of service_role). The fix is to swap secrets, not disable RLS.
- **Don't hardcode threshold values.** Use `from stock_analyzer.constants import ...`.
- **Don't run the app locally.** The secrets architecture assumes Streamlit Cloud. Local runs miss Supabase, miss FRED, may behave differently.
- **Don't set `nav_page` directly.** It's bound to the sidebar widget. Use the `_pending_page` indirection pattern (see app.py:502 for how the harness consumes it).
- **Don't write speculative documentation.** The two memories `project_manual.md` and `project_sdlc_docs.md` explicitly defer user-manual and SDLC docs until the app is ~90% feature-complete.

---

## Quick architectural reminders

**Cross-feature coordination pattern:** features that own state publish to `st.session_state`; downstream features read and gate. When a producer fails, the cache is set to `None` (not an empty container) so consumers can render an explicit "offline" state.

Cache keys currently in use:
- `_port_risk_cache` (Portfolio → Stock Analysis, Watchlist)
- `_risk_high_alerts_cache` (Portfolio → Watchlist)
- `_grow_today_sectors_cache` (Daily Briefing → Watchlist)
- `_grow_composites` / `_grow_composites_coverage` (Portfolio → Grow Today UI)
- `_daily_brief_offline` (Portfolio → Watchlist offline banner)

**Navigation pattern:** `_pending_page` is set by buttons; consumed at the top of the next run; assigned to widget-bound `nav_page`. The `_nav_origin` cache is set when going TO Stock Analysis so the Back button knows where to return.

---

## Branch / commit history pointers

The recent work (May 2026) is organised into named phases. To see the relevant commits:

```
git log --oneline --grep "Phase 1"   # hygiene / audit fixes
git log --oneline --grep "Phase 2"   # operating posture + gates
git log --oneline --grep "Phase 3"   # coordination + defensive sweep
```

Notable commits:
- `9e0d95a` — Phase 1 hygiene (2 critical + 5 high bugs)
- `4194e8f` — Phase 2 enacts "decides" operating posture (16 policy changes, constants module)
- `98a79a4` — Phase 3 coordination + defensive sweep
- `e0ff999` / `1ecc8d2` — DRISHTA rebrand
- `6b5f13c` — Supabase RLS hardening
- `8a36bf2` — Review Before Close: prose → quantitative directives
- `a4ed74b` / `a4380d4` — Action Log Phase A (manual stops + orphan auto-clear)
- `0fd66db` — Act Today: structured directives + per-ticker consolidation
- `287021b` / `67b0dab` / `e4793ff` — Movers discovery, unified into New Positions, flat-day breakout-exempt
- `fb7b56c` / `aec735a` — two-column offense/defense Brief + Buy Candidates de-dupe
- `e95ab2d` — SELL integrity guard (replay-sourced) + double-submit dedupe (COIN double-SELL fix)
