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
- **Claude-authored commits** end with the trailer `Co-Authored-By: Ajay with Claude Opus 4.8 <ajay.x.ku@accenture.com>`, written via `.git/COMMIT_MSG.txt` + `git commit -F` (dodges PowerShell here-string mangling).

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

Phase 4 cleanup items (macro-calendar drift detection, FRED `actual` placeholder, CPI SA→NSA series swap, etc.) live in the todo list at the start of each session if loaded. Check before starting unrelated work.
