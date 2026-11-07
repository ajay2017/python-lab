# Account snapshot repair (bug register N1)

**Status (2026-11-07):** C7 now also closes the weekly data-quality alert. The Saturday 2026-11-07 run showed impossible-states C7 (account `gross_book` vs `SUM(daily_snapshots shares × close)`, > $5) at 16 rows against a baseline of 3, most likely because C3 and C5 repair the account row but not that day's per-ticker rows (bug register DQ-C7). Confirm with Q1 + Q2 of `docs/sql/data-quality-triage-2026-11-07.sql` before building; build C7 inside the C5 auto-repair; leave the C7 baseline at 3.

**Status:** designed 2026-11-01 (planner, Opus 5.5: PROCEED WITH CHANGES). **Owner accepted every recommendation 2026-11-01:**
- **U1:** yes, journal repair plus fail-closed readers.
- **U2:** 35 days.
- **U3:** exclude the row with a visible line, and measure to the nearest valid row.
- **U4:** yes, repair `daily_snapshots` too (C7).
- **U5:**
  - (a) apply missing-ticker repairs automatically; show partial-quantity ones for review;
  - (b) regenerate with Generate Now (no email);
  - (c) older affected debriefs stay frozen and are listed in the dry run.
- **U6:** later, after one live trade day.
- **U7:** queued as its own item (planner + reviewer).
- **U8:** move the broker lane's evening slot to 21:15 UTC (owner, Railway dashboard).

Build started 2026-11-01. **C1 + C2 SHIPPED** `e389d76`. **C3 SHIPPED 2026-11-01** (`scripts/account_snapshot_backfill.py` + `plan_legacy_repairs`; Opus FIX-FIRST (2 blocking: stale gross read as clean; wrong removal on a pre-EOD log) → SHIP): reconciles each legacy row's stored gross to the cent against the journal book × that day's closes, refuses rows whose cash wasn't read post-close or whose difference is trades the EOD book already held, auto-applies only reconciled `missing_ticker` repairs. Owner applied the backfill and regenerated the 11-01 debrief + monthly report 2026-11-01 (verified live). **C4 BUILT 2026-11-02** (`compute_eod` reads the max trade id before holdings, records `book_positions` from exact shares x close and builds `gross_book` as the same sum; the cron flips the heartbeat on an unreadable id, a missing book or dropped provenance columns; Opus SHIP, 0 blocking). **C5 BUILT 2026-11-02** (`stock_analyzer/account_snapshot_autorepair.py`: the EOD lane, after the `daily_snapshot` write and regardless of a price outage, and the Sunday debrief, before `load_engine_inputs`, repair stale rows dated within `ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS` (35) before today, never today's; a problem flips the heartbeat, never blocks; Opus SHIP, 0 blocking). The 10-28 row was repaired 2026-11-02 after the owner corrected its journal dates (`docs/sql/journal-fix-2026-10-28-fills.sql`). NEXT: C6-C8.

## Defect

`account_daily_snapshots.net_equity = gross_book + cash_balance` (`stock_analyzer/account.py` ~100-115). `cash_balance` comes from the broker lane's balance sync (`cash_as_of` ~21:00 UTC; ~21:15 UTC after U8), which already includes every fill. `gross_book` is the journal book at EOD. A trade that reaches the journal after the row is written leaves the row describing two different books.

Owner SQL, 2026-11-01:

| date | gross_book | cash_balance | net_equity | leverage |
|---|---|---|---|---|
| 2026-10-30 | 15272.55 | -12762.22 | 2510.33 | 6.08 |
| 2026-10-29 | 19805.70 | -14247.57 | 5558.13 | 3.56 |
| 2026-10-28 | 15242.86 | -11637.31 | 3605.55 | 4.23 |
| 2026-10-27 | 15317.51 | -7761.29 | 7556.22 | 2.03 |
| 2026-10-23 | 20951.66 | -13286.14 | 7665.52 | 2.73 |

- **10-30:** CSCO, MRVL and MU (~$3,863) were bought Fri 10-30 and reached the journal only Sat.
- **The debrief:** the Sunday 11-01 weekly debrief saved and emailed account −67.25% (= 2510.33 / 7665.52 − 1).
- **10-28:** shows the same shape.

## How trades reach the journal (planner findings)

- **The broker lane never writes trades.**
  - It writes `snaptrade_pending_imports` (`cron_runner.py` ~3582; `broker_sync.py` ~533-541, trade date only).
  - The owner then confirms each one with "Log This Trade →".
  - `traded_at` = `et_anchor_iso(trade_date)` = 16:00 ET (`IMPORTED_TRADE_ANCHOR_ET_HOUR`).
- **Manual backdating:** also anchors at 16:00 ET.
- **A trade logged today without a backdate:** takes the DB `now()`.
- **Trades have no `created_at`:** but `id` is an identity column, so a higher id means it was inserted later.

## Corrections to the first diagnosis

- **The cron monthly report** (`_run_monthly_report`) never reads `account_daily_snapshots`. It inherited the −67% only through its trajectory line, which reads the latest weekly debriefs.
- **📄 Reports' October monthly return is also wrong:** its end anchor is the 10-30 row (`performance_review.monthly_account_returns`).
- **Scope:** account rows start 2026-09-10, not 08-18.
- **Per-ticker `daily_snapshots`:** the report engine takes only closes from them, so a late buy leaves that ticker unpriced and the result `partial`. That is honest, not a wrong number. Other readers of its share counts are wrong on 10-30:
  - Capital Trend line
  - Summary sparkline
  - Predictive Analytics
  - `judgment_grading`
  - Capital vs Margin price fallback

## Design: journal-sourced repair plus fail-closed readers

**Rejected for now: valuing the book from the broker's own positions.** `broker_position_snapshot` has several problems for this use:
- It sums positions across all linked accounts, while the cash is the selected account only.
- It is one overwritten row, so it can't repair past dates.
- It can save on a run where the positions write failed.
- It has no EOD prices for broker-only names.
- Its freshness is unverified (#34c).

**The after-hours rule.** "Late" is judged against the row's own `cash_as_of` timestamp, not the calendar day. A fill at 18:00 ET logged the same evening must not be added to that day's row, whose cash was read at 17:00.

### Chunks

1. **C1 — DDL and DB functions** (`docs/sql/account-snapshot-provenance.sql`, `db.py`; reviewer).
   - Add four columns to `account_daily_snapshots`: `journal_max_trade_id bigint`, `book_positions jsonb`, `original_gross_book numeric`, `repaired_at timestamptz`.
   - The save falls back if the DDL isn't applied yet.
   - New `load_max_trade_id_or_none()`.
2. **C2 — Pure logic** in a new `stock_analyzer/account_snapshot_repair.py` plus the shared money-field math pulled out of `account.py` (reviewer; new `_GATE_FILES` member).
   - `late_trades`: `id > journal_max_trade_id` and `traded_at <= cash_as_of`.
   - `stale_rows`: rows with legacy NULL provenance are never flagged; an unreadable trades read returns None.
   - `plan_repairs`: repairs only when the book plus the late trades equals the replayed book.
   - It refuses on a split, an oversell or a missing close, and never carries a close forward.
3. **C3 — Backfill script** `scripts/account_snapshot_backfill.py` (dry run by default, `--apply`; reviewer).
   - It finds affected legacy rows since 2026-09-10 from evidence: the replayed book × that day's closes against `gross_book`, the `daily_snapshots` share mismatch, and the cash read's timing (post-close or not; there is no cash-amount cross-check).
   - It lists the weekly debriefs whose endpoints hit an affected date.
   - **Then regenerate by Sat 11-07**, in this order: repair the rows → the debrief → the monthly report. Generate Now does it without an email; `ALERT_FORCE=1` cron runs send one.
   - **Why 11-07:** `resolve_period("last_week")` keeps week_ending = 11-01 through then.
4. **C4 — Record provenance at EOD** (`headless_alert_engine.compute_eod`, `cron_runner.py`; reviewer).
   - The cron reads the max trade id BEFORE reading holdings.
   - `book_positions` comes from `Shares Exact` × `Price`.
   - A failed id read writes NULL provenance and flips the heartbeat.
5. **C5 — Automatic repair** in EOD (before step 1b) and at the top of `_run_debrief`, bounded by the new `ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS` (U2). A failure flips the heartbeat and never blocks the day's write or the debrief.
6. **C6 — Fail-closed readers** (`period_inputs.compute_period_core`, `debrief_advisor`, `notify` debrief email, Reports and Debrief captions; reviewer + screenshots).
   - Stale rows get NULL money fields in memory, which reuses the invalid-row path every reader already handles.
   - A visible line names the excluded date and why.
7. **C7 — Repair the per-ticker `daily_snapshots` rows** (only if U4): upsert a late buy with `int(shares)`; delete the row for a late full-exit sell; never touch today's date.
8. **C8 — Docs:**
   - `architecture.md`: schema and constants row.
   - `requirements.md`: F-266, Reports and Debrief.
   - In-app User Guide, `review-policy.md`, shipped-log.

Order: C1 → C2 → C3 dry run → owner approves → apply → regenerate (by 11-07) → C4 → C5 → C6 → C7 → C8.

## Owner decisions

- **U1:** approve journal-sourced repair plus fail-closed readers, with no broker-sourced book for now.
- **U2:** new constant `ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS`, suggested 35 calendar days so a monthly window is always covered. It bounds only the automatic repair; the stale-row check has no lookback.
- **U3:** how a stale end row is reported.
  - **Recommended:** treat it like an invalid-cash row. The account return searches back to an earlier valid row, the debrief labels its dates and withholds the dollar change, and one visible line names the excluded date.
  - **Alternative:** withhold the percentage entirely.
- **U4:** also repair the per-ticker `daily_snapshots` rows (C7, incl. deleting a row after a late full exit)?
- **U5:** backfill and regeneration.
  - (a) Apply missing-ticker repairs automatically and show partial-quantity ones for review?
  - (b) Regenerate the debrief and monthly report with Generate Now (no email) or a forced cron (sends email)?
  - (c) Leave older affected debriefs frozen, or note them?
- **U6 (phase 2):** a journal-vs-broker check at write time, to catch a trade never logged before a Sunday debrief. Only after one live trade day confirms SnapTrade positions reflect same-day fills. Needs a materiality constant.
- **U7 (sibling, separate item, planner + reviewer):** a sell not yet in the journal makes the net-capital sizing cap and the daily new-buy budget looser than they should be (`margin.resolve_net_capital` + journal book). A late buy makes them tighter, which is fail-safe.
- **U8 (sibling):** from 11-02 (EST) the broker lane's evening slot fires at exactly 16:00 ET. Consider moving it to 21:15 UTC, still before EOD at 21:30 UTC.

## Test matrix (each chunk covers what applies)

- **Weekend / holiday:**
  - A Saturday-logged Friday trade is repaired by the Sunday debrief pre-step.
  - A trade dated on a holiday applies to the next trading-day row.
  - No weekend row is ever written.
- **DST:** a 2026-11-02T20:30-05:00 trade is ET 11-02. The anchor at 16:00:00 EST counts as at or before `cash_as_of`.
- **After hours:**
  - An 18:00 ET same-evening fill is not added to that day.
  - A trade logged between the broker run and EOD → refused.
- **Late buy and late sell:** a buy is added to d; d−1 is untouched; d+1 is unchanged. A sell lowers gross; an oversell is refused.
- **Boundaries:**
  - `id == journal_max_trade_id` → not late.
  - ET date == d → counts; d+1 → doesn't.
- **SnapTrade offline / stale cash:** `cash_as_of` is kept, so a stale-cash row stays invalid after repair.
- **Pricing and fractions:**
  - A missing close → no write and a reported failure.
  - A late 0.5-share buy repairs gross exactly.
- **First day after go-live:**
  - Legacy rows are never flagged.
  - The pre-DDL fallback writes without the new keys.
- **Invariants:**
  - Recomputing a row with its own gross reproduces it exactly.
  - A second repair run plans zero writes.
  - `original_gross_book` is set only once.
- **The 11-01 regression fixture:** the five rows in the table above (10-26 left out) plus the three 10-30 buys.
- **AST pins on both ends:** the EOD writes the provenance keys, and `compute_period_core` applies `stale_rows`.

## Sibling sweep (same classes, listed so they aren't rediscovered)

- **Broker cash + journal book:**
  - Home live leverage (awareness).
  - The net-capital cap and daily budget (U7).
  - The `portfolio_risk_snapshots` book fields (awareness, listed).
- **Calendar or UTC day instead of trading or ET day:**
  - A forced EOD on a weekend writes a weekend-dated row (`cron_runner.py` ~700).
  - Broker import matching uses the UTC date of `traded_at` (`broker_sync.py` ~475, ~733, ~747), so a hand-logged trade after 20:00 ET never auto-links.
  - `_activity_date` takes the UTC date (verify SnapTrade's dates).
  - The lot `buy_date` UTC date (#29 follow-up).
  - Day-scoped session caches keep pre-repair rows until the ET date changes.
- **Residuals:**
  - Editing or deleting an old trade isn't detected.
  - A never-logged trade is caught only by U6.

## Contracts and residuals from the C1/C2 review (2026-11-01, Opus FIX-FIRST round 1)

- **C3 `--apply`** must first confirm the four provenance columns exist. `save_account_daily_snapshot` drops them on an unknown-column error and still returns True, so a backfill without the DDL would lose `original_gross_book` for good. Add a provenance-columns probe.
- **C4:**
  - Treat a save that dropped provenance as a degraded heartbeat.
  - Build `gross_book` as the same Σ shares × close over `book_positions` that `plan_repairs` re-sums. Otherwise switch repairs to old gross + the delta on stale tickers.
  - Never send `original_gross_book` / `repaired_at` on the EOD upsert, because PostgREST's merge would wipe the set-once value on an `ALERT_FORCE` re-run.
- **Caller contracts (C3/C5):**
  - Trades must come from `db.load_trades_or_none` (normalised `traded_at`).
  - Injected closes must be unadjusted, or the stored `daily_snapshots.close_price`.
- **Residual, imported trades:** they anchor at 16:00 ET, so an after-hours fill confirmed later always counts as at or before `cash_as_of` and is added to day d, although that day's cash read didn't include it. The 18:00 ET protection covers only hand-logged trades with a real time.
- **Residual, logged after EOD:** a fill logged without a backdate after EOD falls past the cutoff and is never flagged. U8 (broker lane at 21:15 UTC) narrows both. There is no cash-amount cross-check in C3 or C5 (a real one needs a tolerance constant: planner + owner).
- **Residual, logged between the broker run and EOD:** such a trade is refused only when its ticker also has a late trade. Comparing the whole book to the replay would false-flag the common case of a regular-hours fill logged after the close (owner-visible residual; pinned by a test).

## Do not change

- `_valid_snapshot_row`'s cash timing rule.
- The partial-outage skip.
- `cash_balance` / `cash_as_of` on a repaired row.
- The broker lane's account selection.
- The `51dd19f` render-time baseline fill.
- `IMPORTED_TRADE_ANCHOR_ET_HOUR`.
- Also: no new table, no new session cache, and no SnapTrade call in the EOD lane.

## Residuals from the C4 review (2026-11-02, Opus SHIP)

- **Insert-then-holdings race.** Every app path inserts the trade and then saves holdings as two calls (app.py ~30984→31048 BUY, ~30821→30870 SELL, ~33536→33548 / ~33856→33868 broker imports). A trade inserted just before the EOD max-id read whose holdings write lands after the holdings read, or fails, has id <= max yet is absent from the book: undetectable, the row stays wrong the N1 way. Window ~1 s (only an owner confirm near 16:30 ET). The chosen order is still the right one. Optional follow-up (owner call): an EOD check of the book against a replay of trades with id <= max (`trades_strict` is already loaded in `_build_context`); it would also fire on existing holdings drift (bug register FracNew), so it could fail normal days.
- **Two late trades on one ticker, the first already in the book:** `plan_repairs` counts the first twice and refuses. Rare, fails closed (C6 excludes the row).
- **C5/C6:** a forced same-day EOD re-run after a repair rewrites book and gross consistently and keeps `original_gross_book` and `repaired_at` (nothing reads them outside the repair code today). C6 must not label a row "repaired" from `repaired_at` alone.
- **A forced re-run hours after the close** bakes an after-hours fill hand-logged after `cash_as_of` into the book with id <= max (undetectable). The partial-outage text (cron_runner.py ~787-790) tells the owner to re-run "the same evening": the earlier, the safer.

## Residuals and follow-ups from the C5 review (2026-11-02, Opus SHIP)

- **After-hours fill imported late, now written automatically.** An imported fill anchors at 16:00 ET, so it counts as at or before `cash_as_of` even when it filled after the cash read. Before C5 such a row could at most be flagged; C5 now adds the fill to gross, overstating net equity by the fill's value (log line only, with old -> new gross). Rare: needs an after-hours fill that reaches the journal through an import after that day's EOD. Closing it needs a cash-amount cross-check with a tolerance constant (planner + owner). **Owner 2026-11-02: never trades extended-hours or on the 24-hour market**, so this needs no cross-check now; reopen if that changes.
- **Split between the row date and the repair, not yet journaled.** A late buy of a NEW ticker is priced from Yahoo's split-adjusted close times pre-split shares and written; the raised max id means the row is never revisited. Rare.
- **Refusal alarm (do at C6).** A refused C4/C5-era row flips the EOD heartbeat (failure email, healthchecks fail ping, Home degraded line) every trading day until fixed or older than the lookback, and the backfill script only handles legacy rows, so the only remedy is hand SQL. Keep it until C6 ships (until then the daily alarm is the only guard against a refused row feeding Reports and the debrief). At C6: keep flipping on errors, failed writes and transient refusals (a missing close retries nightly); for a structural refusal (split, oversell, unexplained book, no book) flip and email only the first night a (date, reason) appears (the `_notify_broker_failure` alert_state dedup pattern), then show it persistently (System Trust or C6's excluded-row line).

