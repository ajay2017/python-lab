# #34c — SnapTrade connection / staleness alarm

**Status:** DESIGNED 2026-11-21 (Opus planner `claude-opus-5-5`: PROCEED WITH CHANGES). **Owner 2026-11-21: U1–U10 all as recommended; P1 done (below).** **C1 BUILT 2026-11-21** (implementer Sonnet 5.5; Opus review SHIP 0 blocking + 7 non-blocking, all fixed: NB1 `n_disabled` + `any_linked_disabled` (an old disabled authorization after a reconnect is not a linked disable; None when no account carries an authorization reference), NB2 ok flags derived from the payload shape, NB3 `snapshot_saved` None when no snapshot was attempted, NB4 real `list_connections` tested, NB5 true partial-read test, NB6 scrubbed log, NB7 compact date-only). Owner applies the DDL before C1 deploys. C2 keys "disabled" on `any_linked_disabled`, the partial read on `positions_read_ok is False`, and words `positions_read_ok=True` + `snapshot_saved=False` as a failed snapshot write. Confirming round SHIP 0 blocking. Carry into C2: the cron tests' `_HEALTHY_ACCOUNTS` carry no `brokerage_authorization` (their rows read `any_linked_disabled=None`); add a cron fixture with the reference so C2's decision is tested on the production shape (P1: all five accounts carry it). `n_accounts=0` is its own case (`any_linked_disabled` False there). `save_broker_connection_status` has no missing-column fallback: after any DDL change, confirm the first cron log reads "broker: connection status saved".

**P1 DONE 2026-11-21 (owner probe, Sat ~14:15 ET):** one connection `disabled=False`, realtime/realtime; all five accounts (Crypto, Traditional IRA, Managed Individual, Credit Card, Individual) `initial_sync_completed=True`, **`holdings_unavailable=None`** (not False), holdings stamp 2026-11-20 22:52 UTC = **Fri 17:52 ET** on all five (an earlier batch than the 20:22 / 22:56 seen before; no Saturday batch yet at probe time); transactions `last_successful_sync` date-only **2026-11-19** (two UTC days behind on a Saturday; the probe's "ET 2026-11-18 19:00" is the midnight-UTC artefact §3 forbids). Readings: no account needs excluding from the worst-of rule; **build rule: `holdings_unavailable` counts only when it is True — None (the healthy reading) must read as available, never as "couldn't check"** (else permanent unknown); the fills feed lags 1–2 days, which supports U4 (display only) and U8 (i).

Bug register row: `docs/bug-register.md` #34c (probe history). Lead checked two planner claims against HEAD on 2026-11-21: `broker_position_snapshot` is a single upserted row (`db.py` ~7795, `id=1`), and a partial read skips the snapshot save but keeps rc=0 (`cron_runner.py` ~3808).

## Corrections to the alarm draft
1. "~1.5 weekdays" is the wrong unit: weekday-hours false-flag the morning after an NYSE holiday (Fri 2026-11-27 11:15 with a Wed 11-25 20:22 stamp is 38.9 weekday-hours old) and depend on the cron's run time. Count trading sessions (§1).
2. (c) "our holdings call fails" is mostly covered: a `list_accounts` failure or all positions reads failing returns rc=1 (heartbeat red on System Trust ①, failure email). The invisible case is the partial read (one account unreadable, snapshot not saved, rc 0, no email). (c) is re-scoped to that.
3. A stale-broker flag must NOT become "not checked" for U7 (`effective_capital` / `broker_frame`); that would loosen sizing (§4).
4. No history exists to re-check "our calls fetch live" (the snapshot row is overwritten). Store one status row per run with a positions signature, so the evidence accumulates and K is measured before it is final.

## Owner decisions (recommended default in brackets)
- **U1** `SNAPTRADE_REFRESH_MAX_MISSED_SESSIONS` [0]. Equals "~1.5 weekdays" at our 11:15 / 16:15 runs but holiday-safe. Raise to 1 if the C1 rows show SnapTrade skipping nights.
- **U2** Email on entry into flagged for (a) disabled or (b) stale refresh, and on a reason change; no daily repeat; clears on clean [yes, (a)+(b)].
- **U3** Partial read (c): System Trust + Account warn only, no email [yes].
- **U4** Transactions date-only field: stored, shown "(UTC date)", never drives a flag [display only].
- **U5** System Trust placement: one row in ③ Data providers beside "Broker sync — SnapTrade" [③] (⑧ is reserved for App trust item 3).
- **U6** Disabled, or `holdings_unavailable` on any account: severity "down" (Home chip red). Stale refresh (b) or partial read (c): "warn" [yes].
- **U7** Storage: new append-only table, one row per broker run (~730/yr) [table], not extra `snaptrade_config` columns (no history).
- **U8** Siblings logged in the bug register, not built in v1 [log]: (i) U8c Journal-vs-Robinhood Home line and U9 Pending Imports verdicts can read "matches" on a frozen fills feed; (ii) CashAsOf: under a dead connection the cron still rewrites `account_cash` with a fresh `updated_at`, so `daily_budget.budget_basis`'s lag correction stops subtracting buys logged since the freeze (only U7's min() catches it, while the frame is "ok"); moves sizing → planner + reviewer.
- **U9** Keep every write as-is when disabled [keep]: skipping snapshot / cash writes changes figures, and the frozen snapshot is the protective side.
- **U10** "Reconnect Robinhood" button on Account [not now].
- **P1 (owner, before C2):** paste the probe's section-2 lines (masked) for all 5 accounts: `holdings_unavailable`, `initial_sync_completed`, the stamp. If an auxiliary account (Credit Card, Crypto) normally reads `holdings_unavailable=True` or a null stamp, the worst-of rule must exclude it.

## §1 Age bound
- (b) flagged when `holdings_refreshed_oldest < refresh_floor(checked_at)`. `refresh_floor`: ET date of `checked_at`, walk back K+1 sessions with `data.previous_trading_day` (strictly before), floor = ET midnight (`ET.localize`) of that session. Strict: a stamp == floor is clean. Judged at the status row's own `checked_at` ("as of the last broker sync").
- Midnight floor because SnapTrade's batch stamps that session's evening (20:22, 22:56 ET seen); a batch slipping past midnight still counts.
- K=0 examples: Mon 11:15 needs ≥ Fri 00:00 ET (Thu stamp flagged); Tue 2027-01-19 after MLK needs ≥ Fri 01-15; Fri 2026-11-27 needs ≥ Wed 11-25; Sat / Sun need ≥ Fri; an ad-hoc 21:00 ET run before that night's batch stays clean. Weekend batches (Sat 11-14 22:56 seen) only add leniency.
- Status row older than `SNAPTRADE_BALANCE_STALE_HOURS` (25, reused) → "couldn't check".
- Snapshot pairing, no window constant: status row stores `run_started_at` (lane `now_et`) and `checked_at` (end of run). Snapshot belongs to the run when `run_started_at ≤ captured_at ≤ checked_at`; `captured_at > checked_at` → couldn't check; `captured_at < run_started_at` → this run saved none = partial read (c).
- Known limit: `NYSE_HOLIDAYS` ends 2028-12-25 (existing "NYSE 2029 calendar" queue item).

## §2 Where it runs, storage
- `_run_broker` already has the `list_accounts()` payload with per-account `sync_status` (b needs no extra call). One new GET: `snaptrade_client.list_connections()` (`connections.list_brokerage_authorizations`), module contract (None on failure, api_health record, `SNAPTRADE_REQUEST_TIMEOUT_SEC`).
- Status row on every exit path once configured: `list_accounts` failed (`accounts_ok=false` + error), no accounts linked, normal path after the snapshot save. Bookkeeping: never changes rc, never fails the lane.
- DDL `docs/sql/broker-connection-status.sql` (owner applies): table `broker_connection_status` (`id bigserial pk`, `run_started_at timestamptz not null`, `checked_at timestamptz not null`, `accounts_ok bool not null`, `connections_ok bool not null`, `n_accounts int`, `n_connections int`, `any_disabled bool` (null = couldn't check), `disabled_date text`, `any_holdings_unavailable bool`, `holdings_refreshed_oldest timestamptz`, `holdings_refreshed_newest timestamptz`, `holdings_stamp_missing int`, `txn_refreshed_utc_date date`, `freshness_modes jsonb`, `positions_read_ok bool`, `snapshot_saved bool`, `positions_sig text`, `error text` (scrubbed, ≤160)); index `checked_at desc`; RLS on + `FOR ALL TO service_role`.
- db.py: `save_broker_connection_status(row) -> bool` (read-only-viewer gated, never raises); `load_latest_broker_connection_status() -> dict | None` (None = offline / table missing / no rows). Separate table keeps `load_snaptrade_config` untouched.
- Readers never call SnapTrade: Home + Account a `@st.cache_data(ttl=120)` loader beside `_cached_broker_snapshot`; `system_health` direct. No new session_state key (nothing for `coord_freshness`).
- Parsing: holdings stamp tz-aware (naive = UTC; date-only → None, never invent midnight); `disabled` only True / "true"; holdings refresh = OLDEST stamp across linked accounts (the snapshot sums all five).

## §3 Transactions field
Parse to a `date` as given ("YYYY-MM-DD", or `.astimezone(UTC).date()` if a datetime). Never ET midnight (the probe's bogus "39.1 h"). Shown "fills feed refreshed {date} (UTC date)"; drives nothing.

## §4 Consumers
- `effective_capital` / `broker_frame` (U7): no change, no import of the new module. Under a frozen connection, positions and cash freeze at T0; the journal gross includes buys since T0 that cash doesn't reflect, and the frame's `adjust = (journal − broker shares) × price` subtracts exactly those, so min() corrects. Sells since T0 make `adjust` negative, never used to loosen. A stale frame only tightens; "not checked" would remove the correction (loosen). Only change: 💰 Account "🧪 Net capital check … · same" gets " · Robinhood copy may be stale (see Broker Sync)".
- `broker_sync.decide_drift_banner` → `_broker_drift_cache`: `broker_sync.py` (gate) untouched. New pure `broker_connection.caveat_drift(verdict, conn)` before the cache store (keeps the order `test_journal_match_wiring` pins): `none`/`clean` + flagged → `stale_clean`, reason `connection_flagged`, + `connection` sub-dict; `drift` / `awaiting_sync` / `stale_clean` kept + sub-dict; `unknown` and `none`/`no_holdings` unchanged; connection "couldn't check" → identity. Monotonic.
- `summary_view.book_safety`: no change (already maps `stale_clean`).
- F-250 qty_drift deferral (app.py ~8455), U8c `_tickers_in_drift`: unaffected.
- 💰 Account live Position Drift: same connection sentence under the table when flagged, from the cron-stored verdict (one freshness per number).
- `system_health`: new `check_broker_connection()`, one row in `providers`, joins `pipeline`.

## §5 Wording (pure module produces it; app.py renders, `safe_html` in HTML; times like "Mon Nov 17, 8:22 PM ET")
- Home caveated clean, disabled: "ℹ️ No broker mismatch as of {asof}, but SnapTrade reports the Robinhood connection disabled since {date}, so it may be serving an old copy of your holdings. Not a clean bill of health for right now. Details: 💰 Account → Broker Sync."
- Home caveated clean, stale: "ℹ️ No broker mismatch as of {asof}, but SnapTrade's own Robinhood refresh last ran {refreshed} (it normally runs each evening), so that copy may be out of date. Not a clean bill of health for right now. Details: 💰 Account → Broker Sync."
- Home appended to drift / awaiting: "_SnapTrade's own Robinhood refresh last ran {refreshed}_ [or: _the connection is disabled since {date}_]_; part of this difference may be an old copy at SnapTrade, not your journal._"
- Account clean (appended to ✅ line): " SnapTrade's own Robinhood refresh: {refreshed} (checked {checked})."
- Account disabled: "⚠️ SnapTrade reports the Robinhood connection disabled since {date}. Balances, positions and fills here may be an old copy. Reconnect Robinhood in SnapTrade."
- Account stale: "⚠️ SnapTrade's own Robinhood refresh last ran {refreshed}; it normally runs each evening, so this copy may be out of date (checked {checked})."
- Account holdings unavailable: "⚠️ SnapTrade says Robinhood holdings are unavailable for {n} linked account(s)."
- Account partial read: "⚠️ The {checked} broker sync couldn't read every linked account, so the positions snapshot is still from {captured}."
- Couldn't check (caption): "Connection health not checked: {why}" — no check recorded yet (apply `docs/sql/broker-connection-status.sql`) · the last check is over 25 hours old · SnapTrade didn't answer the connection check at {checked} · the latest broker sync didn't record a check.
- System Trust ③ "Robinhood connection — SnapTrade (checked by the broker sync)": ok "connection active · SnapTrade's own refresh {refreshed} · fills feed {date} (UTC date) · checked {checked}"; warn "SnapTrade's own refresh last ran {refreshed}, before {floor} …"; down disabled / unavailable; unknown {why}.
- Email subject "⚠️ DRISHTA: Robinhood connection needs a look"; body = reason sentence + "No figure was changed; the broker checks are marked as possibly stale until this clears" + the action.
- No `**` inside HTML, no bare `$`.

## Chunks
0. Owner: DDL, P1, U1–U10.
1. **C1 Capture** (DB write → reviewer): `snaptrade_client.list_connections`; new pure `stock_analyzer/broker_connection.py` (`summarize_status`, `parse_holdings_stamp`, `parse_utc_date`, `positions_sig`; joins `_GATE_FILES`); db save / load; `cron_runner._run_broker` status row on every exit path + one log line; `system_health._INVENTORY` existence-only row; `docs/sql/broker-connection-status.sql` + `docs/sql/readonly-broker-connection-status-readout.sql`; tests. Ship before Wed 11-25 if possible (Thanksgiving + weekend evidence).
2. **C2 Decision** (constants → reviewer): `SNAPTRADE_REFRESH_MAX_MISSED_SESSIONS = 0` + architecture constants row (next to `SNAPTRADE_BALANCE_STALE_HOURS` ~667, which gains a consumer); `refresh_floor`, `decide(status, snapshot_captured_at, now)` → `{state: clean|flagged|unknown|none, severity, reasons, caveat, why, checked_at, holdings_refreshed_at, floor, txn_utc_date, disabled_date}`, `caveat_drift`, wording functions, `email_decision(verdict, prior_fingerprint)`; cron email on transition, alert_state row 10 (`_BROKER_CONN_ROW`; 1–9 taken), fail open on unreadable state, fingerprint recorded only after delivery (`_notify_broker_failure` pattern). After ~1–2 weeks of C1 rows + the readout (earlier if the owner wants).
3. **C3 Surfaces** (→ reviewer): `system_health.check_broker_connection` into `providers` / `pipeline`; app.py Home drift block (~6411: load, `decide`, `caveat_drift` before ~6443; `_render_broker_drift` appends); Account banner (~41996), Position Drift caveat, Net capital suffix; User Guide. app.py compares only `verdict["state"]` / `["caveat"]` strings (ratchet clean).
4. **C4 Docs** (lead): requirements F-244f; bug register #34c + new rows CashAsOf, U8c/U9 stale fills, 2029 calendar note; coordination-caches `_broker_drift_cache` reason `connection_flagged` + `connection` sub-dict; architecture schema + data flow; review-policy reason for `broker_connection.py` ("decides whether the broker checks are caveated as possibly stale and when the connection alarm emails; a wrong call hides a dead Robinhood connection or cries wolf"; `_GATE_FILES` → 100); shipped-log, CLAUDE.md queue, user-manual.

## Risks
- One surface decides: System Trust owns connection health; Home / Account sentences annotate existing broker checks, no new alert type.
- Expected: a caveated clean renders a caption, so the dock's "Needs a look: Broker drift" can appear (same as `stale_clean` today).
- Offline: `list_connections` / `load_latest…` None = couldn't check; `decide` never reads None as clean; couldn't check adds no Home caveat; failed status write = log only.
- DDL order: C1 before the DDL → `_INVENTORY` "missing" (red). Apply the DDL first.
- Invariant: the connection verdict never feeds `effective_capital`, `broker_frame`, `daily_budget`, `margin`.

## Tests the build must include
- `refresh_floor` K=0 and K=1: Mon 11:15 stamp Fri 20:22 clean / Thu 20:22 flagged; Tue 2027-01-19 after MLK; Fri 2026-11-27 stamp Wed clean; Mon 2027-03-29 after Good Friday stamp Thu clean; Sat / Sun runs; Mon 2026-11-02 after DST end (floor Fri 10-30 04:00 UTC); Mon 2027-03-15 after spring-forward (floor Fri 03-12 05:00 UTC); stamp == floor clean, floor − 1 s flagged; `checked_at` 01:30 UTC Tue uses ET Mon; ad-hoc 21:00 ET before the batch clean; real probe values (11-18 12:10 / 11-17 20:22; Sun 11-15 10:00 / 11-14 22:56) clean.
- `decide`: disabled → flagged/down; any `holdings_unavailable` → flagged/down; connections GET failed + fresh stamp → unknown, + stale stamp → flagged; accounts GET failed → unknown; one null stamp → unknown; status > 25 h → unknown; `captured_at > checked_at` → unknown; snapshot not saved this run → warn (c), no drift caveat from (c) alone; not configured → none; 30-day-old txn date otherwise clean → clean (pins U4); junk / NaN / naive never raise.
- `caveat_drift` monotonic over every input state, identity on unknown; integration: caveated clean through `summary_view.book_safety` → `stale_clean`.
- U7 invariance: AST / import test that `effective_capital`, `broker_frame`, `daily_budget`, `margin` don't import `broker_connection`; `effective_capital.resolve` identical with the connection flagged or clean.
- `email_decision`: entry sends; same fingerprint no resend; reason change resends; clean clears; unreadable state sends; undelivered not recorded.
- Cron (extend `tests/test_broker_position_snapshot.py` / `test_broker_account_selection_guard.py`): status row on `list_accounts`-failed, no-accounts, normal, partial-read paths; status-write failure leaves rc unchanged; partial-read row `snapshot_saved=False`.
- `parse_utc_date`: date string → same date; tz-aware datetime → its UTC date; never ET-shifted.
- `check_constants_documented.py` green.
