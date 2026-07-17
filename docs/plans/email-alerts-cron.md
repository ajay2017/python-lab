# Plan: Email Alerts Cron — Exit-discipline Phase 3

**Status: SHIPPED 2026-06-24 (commits `9add28f`→`cb37862`)**

All code shipped: `cron_runner.py`, `headless_alert_engine.py`, `notify.py`,
`.github/workflows/alerts.yml`, `alert_state` Supabase DDL applied, Resend wired.
Sunday thesis lane chains F-3 debrief + F-4 monthly (`monthly` dispatch mode).

Planned 2026-06-23 (decisions locked with the user). Builds the out-of-app reach
for the loss-protection signals so they land *without* opening the app. Also the
foundation the pullback-awareness Phase 2 alert and the Today's-P&L EOD job reuse
(one headless engine, one workflow).

## Decisions (locked)
- **Delivery:** Resend HTTP API (single `RESEND_API_KEY` secret; no SMTP).
- **Schedule:** once daily, pre-market ~8:00 ET, trading days only.
- **Scope:** PROTECTIVE only — stop breaches + deterioration **EXIT** +
  `risk_off_derisk`. (TRIM/WATCH/grow excluded — highest signal-to-noise.)
- **Anti-spam:** email only when the material set is **new/changed** vs the last
  send (fingerprint stored in Supabase) AND there is ≥1 protective item.

## The seam (why this is bounded, not a rewrite)
Every signal we email is already PURE in `stock_analyzer/` (no Streamlit). The
only coupling is data-prep living in app.py + Streamlit caches. Two extractions
make the app and the cron share ONE code path (no drift):

1. **`stock_analyzer/db.py` — headless credentials.** Today: `st.secrets`-only +
   `@st.cache_resource`. Add an `os.environ` fallback (`SUPABASE_URL`/
   `SUPABASE_KEY`) and a client factory that works with no Streamlit runtime;
   keep the `st.secrets` path for the app. (Providers/`data.py` already do this
   dual-source via `providers/_util.get_secret` — free.) RLS stays ON; the env
   key is the same service-role key class.
2. **Extract `load_all` → `stock_analyzer/bundle_loader.py`.** Move the body
   (currently app.py ~1082–1204; a clean sequence of already-pure calls) into
   `load_bundle(ticker, period, spy_df, rfr)`. app.py's `@st.cache_data load_all`
   becomes a thin wrapper that supplies `_cached_spy(period)` + `_get_rfr()`. The
   cron calls `load_bundle` directly with its own SPY/rfr.

## New files
- **`stock_analyzer/bundle_loader.py`** — `load_bundle(...)` (pure; the extracted
  load_all body). Unit-checkable.
- **`stock_analyzer/headless_alert_engine.py`** — `compute_protective_alerts(*, today) -> dict`:
  - db: `load_holdings`, `load_trades`, `load_manual_stops`.
  - per held ticker: `load_bundle` → enrich `position_age_days` +
    `material_add_age_days` (reuse `tax_advisor._build_open_lots` +
    `exit_advisor.material_add_window_days`).
  - market: SPY 6mo + 1y, VIX (via `data.fetch_*` — env-keyed providers).
  - `build_portfolio_df` → `compute_portfolio_risk_metrics` → `assess_fragility`.
  - signals: stop breaches (`Gap to Stop (%) <= 0`) + `deterioration_signals`
    (keep EXIT only) + `assess_risk_off_derisk` (excluding stop/EXIT tickers —
    same single-surface rule as the brief).
  - returns `{alerts: [...], built_at, errors: [...]}`.
- **`stock_analyzer/notify.py`** — `send_email_resend(*, api_key, to, sender, subject, html)`
  (pure `requests` POST to Resend) + `render_alert_email(alerts, built_at) -> (subject, html)`.
- **`cron_runner.py`** (repo ROOT) — reads env, calls the engine, computes the
  alert fingerprint, compares to the last send in Supabase, emails if changed,
  writes the new fingerprint + date. All to stdout (Actions log). No-ops cleanly
  (logs only) when `RESEND_API_KEY` is absent → safe to ship inert.
- **`.github/workflows/alerts.yml`** — `schedule` (UTC cron) + `workflow_dispatch`
  (manual test button); checkout, setup-python, `pip install -r requirements.txt`,
  run `cron_runner.py` with secrets as env. **DST:** GitHub cron is UTC with no
  DST, so schedule 12:00 **and** 13:00 UTC and let the daily idempotency guard
  (Supabase `last_emailed_date`) fire only the first run each ET day where it's a
  trading day — covers both EST (07:00/08:00) and EDT without double-sending.

## Supabase (one-time DDL — I provide the SQL)
New tiny table `alert_state` (single row: `id`, `last_emailed_date`,
`last_fingerprint`, `updated_at`) with `FOR ALL TO service_role` RLS — mirrors the
`daily_snapshots` pattern. Stores the dedup state. Degrades to "always send" if
the table is absent (so the feature works before the DDL, just without dedup).

## Constants (operational — not investment-policy)
- `ALERT_EMAIL_HOUR_ET = 8` (the idempotency target hour). Labeled operational.
- Reuse all signal thresholds from the exit/risk-off constants already shipped.

## Routing
- Build: Sonnet for the mechanical extractions (load_all → bundle_loader, db env
  fallback); Opus lead for the engine + dedup + email + workflow.
- **Mandatory Opus review** (touches a recommendation surface + a new db table +
  secrets handling). Verify: load_all extraction is behavior-identical for the
  app path; db env fallback never weakens RLS; the cron no-ops without secrets;
  single-surface dedup holds; no provider-key leakage in logs.
- Verification: `workflow_dispatch` manual run → inspect Actions log + confirm a
  test email; THEN trust the schedule. (Can't validate on Streamlit Cloud; the
  Action itself is the test harness.)

## User tasks (provisioning) — gate the LIVE email, not the build
The build ships inert; these flip it on:
1. **Resend:** sign up, verify a sender (their onboarding domain is fine to
   start), copy the API key.
2. **Recipient:** confirm the destination address. NB: `accenture.com` may reject
   external senders — a personal inbox may be needed. (User decision.)
3. **GitHub → Settings → Secrets and variables → Actions:** add
   `RESEND_API_KEY`, `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`, `SUPABASE_URL`,
   `SUPABASE_KEY` (service-role), plus the provider keys the app uses
   (`FINNHUB_API_KEY`, `FMP_API_KEY`, `FRED_API_KEY`).
4. **Enable Actions** on the repo if not already.
5. **Run the `alert_state` DDL** in Supabase (SQL provided at build time).
6. **Manual-trigger** the workflow once (`workflow_dispatch`) to smoke-test.

## Risks / notes
- FMP free tier (250/day) is shared by API key; a once-daily ~10-name run is
  cheap and well within budget. Coordinate with rate-limit resilience.
- Service-role key in GitHub secrets = same key class as Streamlit; RLS stays on.
- `requirements.txt` install on Actions ~1–2 min; fine for a daily job.

## Out of scope (DEFERRED)
- Pullback-awareness Phase 2 reactive alert + Today's-P&L EOD snapshot — will
  reuse this engine/workflow once it's proven (add jobs, not a new pipeline).
- Intraday / twice-daily cadence; richer HTML templates; per-signal mute controls.
