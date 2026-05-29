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

---

## Coming back to this project — checklist

When you open this folder after time away:

1. **Open the folder in VS Code.** The `.venv/` is local; VS Code auto-detects it as the Python interpreter for this workspace.
2. **Skim this file.**
3. **Open `MEMORY.md`** (the auto-memory index) to refresh on durable decisions and feedback.
4. **Check `git log --oneline -10`** to see what the last commits were.
5. **Glance at the Phase 4 todo list** (see "Outstanding Work" below) for what's still queued.
6. **Open the deployed app** and click through Home / Today's Brief / Watchlist / Stock Analysis to confirm everything still loads.

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
│   └── architecture.md            Module map, data flow, scoring model, db schema
└── stock_analyzer/
    ├── constants.py               SINGLE SOURCE OF TRUTH for decision thresholds
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

Rationale for each value: [`memory/project_decision_thresholds.md`](.claude/projects/c--Users-ajay-x-ku-python-lab-python-lab/memory/project_decision_thresholds.md) (Claude's auto-memory; same content also summarised in requirements §2A).

---

## How to run / deploy

**Don't run the app locally.** It's designed to run on Streamlit Community Cloud where the secrets live.

| Action | How |
|---|---|
| Deploy a change | `git push origin main` — Streamlit Cloud auto-redeploys in 1–3 min |
| See the new deploy | Hard-refresh the browser tab (Ctrl+F5 / Cmd+Shift+R) |
| Force a session reset (when a cached_resource is stale) | Streamlit Cloud → app → **Manage app → Reboot** |
| View deploy logs | Streamlit Cloud → app → Manage app → Logs |

---

## Secrets (set in Streamlit Cloud dashboard, never committed)

Under **App → Settings → Secrets** in `secrets.toml` TOML format:

```toml
[supabase]
url = "https://<your-project-id>.supabase.co"
key = "sb_secret_***"          # MUST be the service-role / secret key, not publishable

ANTHROPIC_API_KEY = "sk-ant-..."   # Anthropic API for AI Brief
OPENAI_API_KEY    = "sk-..."       # optional
GOOGLE_API_KEY    = "AIza..."      # optional

[fred]
api_key = "..."                     # optional, enriches macro calendar with released values
```

**Security model:** RLS is enabled on all Supabase tables with `FOR ALL TO service_role` policies. The publishable/anon key has no matching policy — defense-in-depth in case the publishable key ever leaks. If you ever swap secrets, you MUST reboot the app via Manage app (the `@st.cache_resource` client doesn't auto-refresh).

---

## Common workflows

### Making a code change
```
# In c:\Users\ajay.x.ku\python-lab\python-lab
git pull
# edit code
git add <files>
git commit -m "Short imperative summary"
git push
# wait ~2 min, hard-refresh deployed app
```

### Quick syntax check before pushing
```
python -c "import ast, io; ast.parse(io.open(r'app.py', encoding='utf-8').read()); print('OK')"
```

### Switching to another project (and coming back)
- This project's `.venv/` stays put — no cleanup needed
- Other project: separate folder + separate `.venv/`
- Open one folder per VS Code window; VS Code auto-detects the venv for each

---

## Outstanding work (queued for Phase 4)

The audit and live-market validation surfaced these items; none blocking:

**Macro calendar / regime:**
- Macro calendar drift detection (Tier 1): cross-check `_STATIC` dates against FRED `last_updated`; warn loudly if drift
- Auto-released flag via FRED last-update (Tier 2): macro gate filters on `not released AND in window`
- "⏳ awaiting FRED update" placeholder when an event is past but `actual` is None
- "Refresh macro data" button that bypasses session_state cache
- Switch CPI series CPIAUCSL (SA) → CPIAUCNS (NSA) so value matches the media headline
- Tighten "controlled inflation" threshold in regime detection (≤2.5%, not ≤4%)
- Don't auto-claim "in-line" regime note without a consensus value supplied

**Long-term (optional):**
- BLS Calendar API integration to replace `_STATIC` dates entirely (Tier 3)

**Minor cleanup:**
- Dead "Weak Hold" / "Avoid" code paths (no producer emits them)
- R:R targets dict validation (defensive)
- Trim trade journal entry text suggestion

Pick up tomorrow morning during market validation, then batch.

---

## Phase 5+ queued

**Evening Debrief (PM read companion to Today's Brief):**
- New section/tab rendered after 3:30 PM ET (or always with a "preview" mode before that)
- Reviews the day's activity against the AM read:
  - Which Go-verdict picks were actioned in Trade Journal · entry vs. close price
  - Which Skip/Filtered-Out picks would have worked anyway (learning loop)
  - Held positions that crossed stops or earnings windows during the day
  - Tomorrow's macro events + sector exposure heatmap
- Closing summary: "Today's P&L attribution," "Risk events tomorrow," "One thing to fix"
- Should consume the locked AM snapshot (if present) as the "what I planned" baseline so the debrief is a true delta vs. the morning plan

**Concept:** AM Brief = "today's playbook"; Evening Debrief = "how it went + what's tomorrow." Together they make the loop closed without needing the user to remember anything between sessions.

**Brokerage sync (eliminate manual journaling + the drift class of bugs):**
- One-way pull from brokerage API → `trades` + `holdings` tables. Brokerage state is canonical for shares and cost basis; app retains the decision-context columns (`signal_seen`, `followed_signal`, `deviation_reason`, `lesson`).
- Auto-detect drift between brokerage truth and app state; surface a side-by-side diff for user approval before applying.
- Triggered: on-demand "🔁 Sync brokerage" button + optional scheduled pull at market close.
- Removes the manual-entry path that produced the May 2026 drift mess (15 unmatched SELLs, multi-round SQL backfill, eventual full rebaseline on 2026-05-27). Drift recovery is documented in memory `feedback_trade_drift_recovery`.
- Brokers to scope: Fidelity, Schwab/TD, IBKR, Robinhood (varying API quality). Auth via OAuth + token storage in Streamlit secrets.

**Future expansion (optional) — discovery universe:** widen `discovery_universe` to full S&P 500 (~500) or Russell 1000 (~1000) — bigger net, slower scan, more yfinance flakiness. Or swap the static list for a live source (Wikipedia SP500 scrape needs lxml; paid screener API = Polygon/Twelvedata/Alpha Vantage, reliable but adds key plumbing). The curated static list was chosen first for zero new deps and zero runtime-scrape risk; cost is a manual refresh a few times a year. The mover signal is also 1-day gain only — could add sustained-momentum and most-active lenses later.

**Action Log — Phase B/C (queued):** Phase A (manual stop override) shipped. Phase B = in-context Sell/Trim button directly on Review Before Close items (act without leaving the Brief); Phase C = protective-trim variant. See memory `project_action_log_subsystem`.

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
