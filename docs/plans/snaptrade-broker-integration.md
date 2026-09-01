# SnapTrade Broker Integration — Plan

**Follow-up SHIPPED 2026-08-31 — Account page cash-source clarity + sync-lag disclosure (F-244b).** A user-reported "why doesn't Total Account Value match Robinhood" question root-caused to the `broker` cron's once/twice-daily sync not yet reflecting same-day trades (traced to an exact $ match: two sells + a buy generated the identical net-cash delta as the gap between the app's stale synced debit and Robinhood's live figure). Two display-only fixes: (1) the page's static intro caption claiming cash was "entered manually for now" directly contradicted the "Synced via SnapTrade" caption shown for the same value — stale copy from before F-244 shipped, corrected. (2) A new caption fires when `broker_sync.tickers_traded_since()` (the same helper Home's position-drift check already uses) finds a trade logged after the cash row's own sync timestamp, naming the ticker(s) and the estimated net-cash impact via `daily_pnl.today_trade_cash_delta()`. Commits `1c1e634`/`1c5aed1`, no reviewer needed (pure-additive display logic). Full detail: `docs/requirements.md` F-244b; memory `project_snaptrade_broker_integration`.

**Follow-up SHIPPED 2026-08-30 — Pending Imports clarity (F-244a).** Users could see Position Drift read completely clean while Pending Imports still listed rows, with no explanation of why both could be true — the two checks answer different questions at different granularities (Position Drift = live net-holdings, forgiving; Pending Imports = exact per-transaction date/price match, unforgiving). Two display-only additions to `broker_sync.py`: `annotate_pending_reconciliation()` (flags a pending row "likely already logged" when its ticker's drift is clean) and `find_pending_match_candidates()` (suggests a specific already-logged trade within a 3-day window). Two commits (`b9657b7`, `688b42e`); the second went through an Opus reviewer pass by choice (not mechanically required) given the trade-record-integrity risk of a bad suggestion — first pass was FIX-FIRST (1 blocking: a matched candidate wasn't consumed, so one logged trade could be suggested to multiple pending rows), fixed and re-reviewed SHIP, 0 blocking. Full detail: `docs/requirements.md` F-244a; memory `project_snaptrade_broker_integration`. Deliberately NOT built here: loosening `classify_transactions`' actual cron-time dedup, and a date field on the manual Log Trade form (the real root cause of most date mismatches) — both are wider-blast-radius changes left for a separate initiative.

**Goal:** Fully automated Robinhood sync via SnapTrade (REST brokerage aggregator), replacing the need for manual CSV import ([broker-statement-import.md](broker-statement-import.md) stays live as fallback). Three capabilities, all automated: position drift detection, account cash/balance sync, transaction import.

**Status: SHIPPED 2026-08-18.** Chunks 1-3 shipped (constants, `snaptrade_client.py`, `broker_sync.py` — Opus 4.8 reviewed, SHIP, 0 blocking). Chunk 4 (`db.py` layer) shipped — Opus 4.8 reviewed FIX-FIRST (3 blocking: a `broker_txn_id` unique-violation was falling into the column-degradation retry and silently duplicating trades; `save_snaptrade_pending_imports`' merge-upsert could clobber a `logged`/`dismissed` row's status back to `pending` on a re-sync; three save functions were missing the `is_readonly()` viewer-guard). All three fixed (idempotent-return check mirroring `idempotency_key`'s pattern; switched to `ignore_duplicates=True` with the existing `save_recommendations` TypeError-compat fallback; added the guard to all three writers) and reverified (full suite + antipattern/constants-doc gates green). Chunk 5 (`cron_runner.py` — 7th `broker` lane, plus the one new `db.backfill_trade_broker_txn_id` helper it needed) shipped — Opus 4.8 reviewed SHIP, 0 blocking (3 non-blocking notes on outage-routing asymmetry between the balance/transaction sub-jobs and the unreachable-SnapTrade email's lack of dedup, both accepted as-is with a documenting comment added).

**Chunk 6 (`app.py` — "⚡ Broker Sync" Account-page section + Option A "Log This Trade" Trade Journal integration) shipped — Opus 4.8 reviewed FIX-FIRST (2 blocking) → fixed → re-verified SHIP.** Blocking #1: the Log Trade tab's locked fields had no escape hatch — a user who clicked "Log This Trade →" and changed their mind found the form permanently frozen (only a successful save cleared `_tj_broker_prefill`). Fixed with a standalone "✗ Not now — unlock the form" button plus clearing both keys in the existing Cancel handlers. Blocking #2: the live position-drift check diffed against `_acc_pdf`'s `int()`-truncated `"Shares"` column instead of raw holdings, so any fractional Robinhood share count produced a permanent, unfixable false `qty_mismatch` — fixed by feeding `diff_positions()` `st.session_state.holdings_df` (the true, untruncated source `build_portfolio_df` itself reads from) instead. A related non-blocking finding (an unloaded portfolio producing false "Robinhood-only" drift) was fixed in the same pass. Two cheap non-blocking suggestions also applied: a 60s `@st.cache_data` wrapper on the per-render SnapTrade calls, and a re-registration caution note in the setup copy. A narrow re-verification pass confirmed both blocking fixes hold end-to-end → **SHIP**.

**All 6 build chunks complete and Opus-reviewed.** Docs synced same session: `docs/requirements.md` (new F-244), `docs/architecture.md` (+2 module sections for `snaptrade_client.py`/`broker_sync.py`, +3 new §6.33–6.35 table sections, `trades.broker_txn_id` documented in §6.3, constants table from chunk 1), in-app User Guide (💰 Account tab + pages-at-a-glance).

**Post-ship live-debugging correction (2026-08-17/18): the credential model was wrong — fixed, re-verified against the real SnapTrade API, not yet re-reviewed by Opus.** Three real bugs surfaced only once the user actually tried to connect a live SnapTrade account (none catchable by design review or unit tests, since they required the real third-party API): (1) `timeout=` was passed to SDK convenience methods that don't accept it (`TypeError`, confirmed via `inspect.signature()` against the installed SDK — none of the six wrapper methods this module calls expose a `timeout` kwarg); fixed by wrapping every call in `stock_analyzer.providers.yfinance_provider._call_with_timeout` (the same worker-thread wall-clock helper already reused by `ticker_liveness.py` for yfinance's identical no-timeout-knob problem) instead. (2) The captured error for any SnapTrade failure showed only HTTP headers, not the actual JSON error body (`SnapTradeApiException.__str__()` renders the response line + headers before the body, and `api_health`'s 120-char truncation cut off before ever reaching it) — fixed by having `_record_error()` special-case `ApiException` and record `f"{e.status} {e.body}"` instead of `str(e)`. (3) **The credential model itself was wrong** — the whole Commercial multi-tenant design (register a `userId`, get a one-time `userSecret`, use both alongside `SNAPTRADE_USER_ID`/`SNAPTRADE_USER_SECRET`) assumed a Commercial SnapTrade API key. The user's actual SnapTrade account is a **Personal** key (free, single-account — the correct fit for a personal single-user app like this one), confirmed against a screenshot of the real SnapTrade Dashboard's Personal API Key page, which states verbatim: *"Personal accounts do not register a SnapTrade user — skip registerUser, and do not send userId or userSecret."* Verified directly against the SDK that omitting `user_id`/`user_secret` from every call produces the same clientId-level auth error as supplying them (not a "missing required field" error), confirming the docs. Fixed by rewriting `snaptrade_client.py` to use `SnapTradeAuth.personal_api_key(...)`, removing `register_user()`/`_snaptrade_user_creds()` entirely, and dropping `user_id`/`user_secret` from every call. `has_snaptrade()` now checks only `SNAPTRADE_CLIENT_ID`/`SNAPTRADE_CONSUMER_KEY` — no second credential pair exists. The Account page's setup flow correspondingly dropped the "Register with SnapTrade" → one-time-`USER_SECRET`-display step entirely, replaced with a single "Connect Robinhood" button that goes straight to the connection portal link. **Lesson for future SnapTrade-adjacent work:** this class of error (wrong account-tier assumption) was invisible to code review and to the SDK's own generic README, which is written assuming the Commercial model as the default case — it only surfaced once a human actually clicked through the live flow against their real account. Neither `curl`-verifying SDK source (which caught the earlier `timeout=` and header-vs-body mistakes) nor an Opus design review would have caught an account-*tier* mismatch, since both assumed the account type stated in the plan rather than checking the user's actual dashboard.

**LIVE 2026-08-18.** All pre-ship items completed: credential-model fixes committed and pushed; DDL applied; Railway env vars set (`SNAPTRADE_CLIENT_ID`/`SNAPTRADE_CONSUMER_KEY`). Three additional post-ship bugs found and fixed during live validation — see the post-ship section above and memory `project_snaptrade_broker_integration`.

Supersedes the Robinhood MCP path (rejected — interactive-auth only, no headless cron possible; see memory `project_today_pnl_scope`). SnapTrade is REST middleware: a **Personal API key** (Client ID + Consumer Key only — no per-user registration), then all calls (app + cron) are headless.

---

## Why SnapTrade over MCP

Robinhood has no public REST API for individuals. The MCP path (`agent.robinhood.com/mcp/trading`) requires interactive/desktop auth with no long-lived headless token — neither the Railway app nor its cron lanes could be the MCP client. SnapTrade solves this: a **Personal API key** (`CLIENT_ID` + `CONSUMER_KEY`, the correct tier for a personal single-user app — see the "credential model" correction above) authenticates every call. Personal accounts have no per-user registration step and no second credential pair, unlike a Commercial/multi-tenant SnapTrade integration.

## Credential storage

- `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY` — the **only** two credentials, Railway env vars (same tier as `SUPABASE_KEY`), sourced from the SnapTrade Dashboard's **Personal API Key** page. `snaptrade_config` (DB) stores only non-secret connection state: `brokerage_authorization_id, status, connected_at, last_full_sync_at`.
- **No `SNAPTRADE_USER_ID`/`SNAPTRADE_USER_SECRET`.** A Personal SnapTrade key has no per-user registration concept — the original design assumed the Commercial multi-tenant model and was corrected 2026-08-18 once the user's actual (Personal, free) SnapTrade account made this concretely visible via a live API rejection.

---

## Three capabilities

### 1. Position drift detection (awareness only, no DB write)
Live-compute each app render (or cached like other advisors): fetch RH positions via SnapTrade, diff against `trades`-derived `port_df` holdings. Three buckets, matching the mockup:
- **RH-only** (held at broker, not in `trades` — missing buy)
- **App-only** (in `trades`, not at broker — missing sell, or a drift bug)
- **Qty mismatch** (both have it, share count differs beyond `BROKER_DRIFT_SHARE_TOL`)

Never gates, never auto-corrects `trades` — same posture as [feedback_trade_drift_recovery](../../MEMORY.md). Cache key `_broker_drift_cache`, set to `None` (not `[]`) on SnapTrade failure — the offline-sentinel contract check_antipatterns.py enforces.

### 2. Account cash/balance sync (writes `account_cash`)
Fetch RH balance via SnapTrade → `map_balances_to_cash()` → `db.save_account_cash`. Runs from the `broker` cron lane (not on every page render) so a stale SnapTrade call can't block page load; the existing `account_cash` timestamp already lets the UI show "as of" staleness. Guarded by `SNAPTRADE_BALANCE_STALE_HOURS` — if the last successful sync is older than that, the Account page shows a stale banner rather than silently trusting an old number.

### 3. Transaction import (review queue → manual log, never auto-writes `trades`)
Cron fetches RH orders + cash events since `last_full_sync_at` (bounded by `SNAPTRADE_SYNC_MAX_TXN_LOOKBACK_DAYS`), classifies each:
- **Buy/Sell order** → dedup (below) → new rows go into `snaptrade_pending_imports` (`status='pending'`)
- **Dividend / interest / fee** → straight into `snaptrade_income_events` (display/trend only — see Modified Dietz note below)
- **ACH/wire transfer** → straight into `account_flows` (the only category allowed to touch NCC)

---

## Trade-log flow — Option A (DECIDED, mockup approved: `docs/mockups/broker-sync-trade-log-flow.html`)

RH imports never auto-write `trades` — they lack the app's required decision documentation (thesis, pre-mortem, decision context, trigger type), and CLAUDE.md's operating posture ("the app decides, it does not inform") depends on that documentation existing for every position. Auto-writing would silently create trades with no thesis behind them.

Instead, `snaptrade_pending_imports` is a **notification/reminder queue**:

1. **💰 Account page, "Pending Imports" section** — a table of un-logged RH trades, each row with a **"Log This Trade →"** button (not a bulk checkbox-import — every row is a deliberate individual action). Status column shows ⏳ Pending / ✓ Logged.
2. **Clicking it pre-fills the existing 📒 Trade Journal "Log Trade" form**, with the mechanical fields **locked (🔒 read-only)** from the RH data: ticker, action, shares, price, trade date, amount. The user cannot edit these — they're broker fact.
3. **BUY trades still require:**
   - Trigger type (chip selector — same taxonomy as manual entry)
   - Thesis (optional, same as manual entry)
   - **Pre-mortem — required**, labeled **"Retrospective"** with an honest framing banner: *"written after the trade — what would have made this wrong at the moment you decided?"* This is not a waived gate; it's relabeled to be truthful about timing, per the documentation-integrity posture in CLAUDE.md.
   - Decision context is auto-captured at **save time** (not order time) with a visible "Auto-captured" tag — there's an honest gap between when the RH order actually filled and when the user clicks "Log This Trade," and the UI must not pretend that gap doesn't exist.
4. **SELL trades get a simpler form:** locked mechanical fields + exit-reason chip selector + optional lesson note (feeds Pattern Library, F-195). No pre-mortem — that's a forward-looking BUY-only gate structurally, so waiving it here isn't a special case.
5. **On save:** row writes to `trades` via the existing `save_trade` path (adds `broker_txn_id` for dedup, see below), pending-import row flips to `status='logged'`, UI updates the row in-place to "✓ Logged".

This makes RH import a **faster data-entry path with the same integrity bar**, not a bypass of it.

---

## Dedup strategy (two-tier)

**Tier 1 — exact, via new column.** `trades.broker_txn_id text` (nullable, backward-compatible like every other optional column — see `db.load_trades()` backfill convention). Unique index with `NULLS DISTINCT` (or Postgres default, which already treats NULLs as distinct) so existing manual/CSV rows with `NULL` never collide. Once a `snaptrade_pending_imports` row is logged, its SnapTrade `transaction_id` is stored here — re-fetching the same order from SnapTrade is now a guaranteed no-op.

**Tier 2 — content fallback, for rows that predate this column** (i.e., already logged via F-87 CSV import before SnapTrade existed). Same match key as CSV import's proven approach ([broker-statement-import.md](broker-statement-import.md) line 25): `(date, ticker, action, round(shares,4), round(price,2))`. If a SnapTrade-fetched transaction content-matches an existing `trades` row that has no `broker_txn_id` yet, treat it as already-logged (backfill the `broker_txn_id` onto that row rather than creating a new pending-import) instead of creating a duplicate pending row.

---

## Modified Dietz integrity (why income events get their own table)

`account_flows` feeds `net_contributed_capital` (`stock_analyzer/account.py`). A dividend/interest credit is **performance**, not a **contribution** — writing it there as a deposit would inflate NCC and mechanically suppress the reported growth%. Only genuine ACH/wire transfers may land in `account_flows`. Dividends, interest, and fees go into the new `snaptrade_income_events` table, which feeds **only** the Account page's cash-activity trend chart (the stacked monthly bar chart in the mockup) — it is never read by `account.py`'s return math.

---

## Schema (new DDL, apply manually in Supabase per project convention)

```sql
create table if not exists snaptrade_config (
  id int primary key default 1,
  brokerage_authorization_id text,
  status text not null default 'disconnected',  -- 'disconnected' | 'connected' | 'error'
  connected_at timestamptz,
  last_full_sync_at timestamptz,
  check (id = 1)  -- single-row config, same pattern as account_cash
);

create table if not exists snaptrade_pending_imports (
  id bigint generated always as identity primary key,
  snaptrade_txn_id text not null,
  ticker text not null,
  action text not null,  -- 'BUY' | 'SELL'
  shares numeric not null check (shares > 0),
  price numeric not null check (price > 0),
  trade_date date not null,
  raw_json jsonb,
  status text not null default 'pending',  -- 'pending' | 'logged' | 'dismissed'
  fetched_at timestamptz not null default now(),
  unique (snaptrade_txn_id)
);

create table if not exists snaptrade_income_events (
  id bigint generated always as identity primary key,
  event_type text not null,  -- 'dividend' | 'interest' | 'fee'
  ticker text,               -- null for account-level interest/fees
  amount numeric not null,
  event_date date not null,
  fetched_at timestamptz not null default now()
);

alter table trades add column if not exists broker_txn_id text;
create unique index if not exists trades_broker_txn_id_uq on trades (broker_txn_id) where broker_txn_id is not null;
```

All three new tables are `FOR ALL TO service_role` RLS-protected, same as every existing table (CLAUDE.md Hard Rule #2).

---

## New constants (`stock_analyzer/constants.py`)

| Constant | Value | Purpose |
|---|---|---|
| `BROKER_DRIFT_SHARE_TOL` | `0.001` | Share-count tolerance before flagging a qty mismatch (fractional-share rounding noise) |
| `SNAPTRADE_BALANCE_STALE_HOURS` | `25` | Account page shows a stale-data banner past this age (mirrors the daily-lane 25h pattern already used elsewhere) |
| `SNAPTRADE_SYNC_MAX_TXN_LOOKBACK_DAYS` | `90` | Bounds the cron's transaction fetch window — avoids an unbounded historical pull on first sync or after a long outage |
| `SNAPTRADE_REQUEST_TIMEOUT_SEC` | `15` | Per-call timeout for the SnapTrade client, same convention as `DATA_YF_REQUEST_TIMEOUT_SEC` |

These are policy/threshold values (Hard Rule #1) — confirm with the user before any future change.

---

## Modules

- **`stock_analyzer/snaptrade_client.py`** — thin wrapper over the SnapTrade SDK/REST. Every function returns `None` on any failure (timeout, auth error, API error) — never raises into caller code, matching the existing multi-source provider convention (`project_second_data_source`). Reads credentials from env (`os.environ`, Railway-injected) — no `st.secrets` dependency since cron runs outside Streamlit.
- **`stock_analyzer/broker_sync.py`** — pure transform/decision logic, no I/O:
  - `diff_positions(rh_positions, port_df) -> dict` (the 3 drift buckets)
  - `map_balances_to_cash(rh_balance) -> dict` (shape matching `account_cash` columns, signed per the v4 margin convention)
  - `classify_transactions(rh_txns, existing_trades) -> dict` (`{"new_pending": [...], "income_events": [...], "flows": [...]}`, applying the two-tier dedup)

---

## Cron: 7th lane (`broker`)

New dedicated Railway Cron Job service (alongside premarket/scan/intraday/eod/weekly-thesis/weekly-maintenance — see memory `project_cron_railway_migration`). Own heartbeat row on 🩺 System Trust, own failure email. Kept separate so SnapTrade latency/outages can never perturb the EOD `daily_snapshot` write or any other lane's timing. `cron_runner.py` gets a new `_run_broker` mode: balance sync → transaction fetch/classify → pending-imports upsert → income-events insert → flows insert (ACH only) → update `snaptrade_config.last_full_sync_at`.

---

## Build sequence (planner-specified order)

1. Constants block — 4 new constants above
2. `stock_analyzer/snaptrade_client.py` (thin wrapper, all-`None`-on-failure)
3. `stock_analyzer/broker_sync.py` (pure transform/decision logic) — **reviewer required** (decision-adjacent: drift classification, dedup correctness)
4. `stock_analyzer/db.py` — load/save for the 3 new tables + `broker_txn_id` support in `save_trade` — **reviewer required** (DB-write/data-integrity path, Hard Rule #4)
5. `cron_runner.py` — new `_run_broker` mode / 7th lane — **reviewer required** (cross-feature coordination + data-integrity)
6. `app.py` — "⚡ Broker Sync" section on 💰 Account page (connection status, balances, drift, pending imports with Option A flow, cash-activity trend chart) — **reviewer required** (new user-facing decision-adjacent surface: the "Log This Trade" write path)
7. Apply Supabase DDL (3 tables + `broker_txn_id` column) — manual, user-run per repo convention
8. Docs sync same session (Definition of Done, CLAUDE.md): `docs/requirements.md` new F-row, `docs/architecture.md` constants table + module sections + this plan's own status line, `docs/shipped-log.md` on ship, User Guide, memory

F-87 CSV import stays as manual fallback — this doesn't replace it, it supplements it for the common case.

---

## Mockups (approved)

- `docs/mockups/broker-sync-mockup.html` — full Account page section, 3 states (connected / not connected / SnapTrade offline), balances/drift/pending-imports/cash-activity-trend layout
- `docs/mockups/broker-sync-trade-log-flow.html` — Option A flow: pending queue → pre-filled locked form → post-save state; SELL edge case

---

## Routing

🟣 planner (this doc, already passed) → 🔵 implementer (chunks 1-2, mechanical) → 🔴 reviewer (chunks 3-6, each individually — data-integrity + new decision surface) → docs sync (same session, all 8 Definition-of-Done items) → user runs DDL.
