# Broker fill record: Robinhood fills into the journal with less manual work

**Status (2026-11-09): DESIGNED, not started.**
- **Asked by:** the owner on 2026-11-09, "automate the sync between App and Robinhood so we avoid some of the manual review steps".
- **Planner:** Opus 5.5 (`claude-opus-5-5`), verdict PROCEED WITH CHANGES.
- **Before anything:** the owner answers U1–U16 and runs probe P (read-only SQL).
- **No code yet.** The design below is the planner's report, kept close to verbatim. File:line references are as of HEAD `92a2bbe`; re-check them before building.

## Recommended shape

- **Phase 1:** split each Robinhood fill into FACTS and DOCUMENTATION.
  - The owner records the facts with one click, using the broker's own timestamp and price.
  - The cron auto-links only exact same-day matches.
  - The documentation becomes a visible "notes owed" queue.
- **Phase 2:** the cron records fills on its own. This is a separate decision with its own gate and shadow run.
- **Rejected:** full auto-write, and using Robinhood's positions as the book.

### Why the cron doesn't write the book now

The main reason is not data quality. The app assumes `holdings` has a single writer.

- Holdings load once per session (`app.py` ~3177-3205).
- Every trade-confirm site then saves a full replace of the session copy.
- `save_holdings` upserts, then deletes every ticker not in the frame, plus those tickers' `manual_stops` (`db.py` ~1647-1729).
- So an open tab that logs any trade after a cron write would silently delete a cron-recorded new position and its stop.

### Corrections to the design brief

- **No `source` column on `trades`.** Provenance today is `broker_txn_id` plus note markers (`db.py` ~2278-2284; `trade_time.py` ~82-96).
- **F-80a is the trade-date picker.** The pre-mortem hard gate is the submit check at `app.py` ~32286 ("Trade not recorded — Pre-Mortem required before this Buy").

## The prior decision (snaptrade-broker-integration.md ~55-71, Option A: imports never auto-write `trades`)

**Recommendation: split it.** A Robinhood fill is a fact that has already happened, and refusing to record it only makes the book wrong.

**What a wrong book has already caused:**
- N1 (the −67% debrief);
- U7 (sizing fails open);
- the CSCO / NVDA review work.

**Two existing rules point the same way:**
- **OP-03** (requirements.md §2A.1) says integrity failures must fail loud. An unrecorded fill fails quietly.
- **The CSV / paste import is a precedent.** It already writes fact-only rows (`trigger_type: "MANUAL"`, no thesis, no pre-mortem) after one owner click (`app.py` ~33886-33896).

**Consequences to accept:**
- **The pre-mortem moves.** It stops being a forcing function on recording the trade and becomes one on marking the notes done.
- **Thesis monitoring waits for notes.** None of these runs on an undocumented position until notes exist:
  - the weekly thesis lane (BUY rows with `user_thesis`, `cron_runner.py` ~2583-2587);
  - pre-mortem trigger monitoring;
  - Red Team;
  - R-Multiple (a retrospective BUY gets no declared stop, `app.py` ~32395-32403).

  This gap already exists between a fill and its hand-log, and forever for CSV rows. The owed queue makes it visible.
- **Analytics that count rows change:**
  - `decision_quality.classify_trade_prep` grades thesis per BUY row (~338-339);
  - `trade_analytics` groups by `trigger_type` and silently drops NULL (~118-122);
  - per-row win rates and trade counts.

## Options

### O1. Faster cadence plus a live "Check Robinhood now"

**Changes:**
- extra Railway slots (owner, in the dashboard);
- `system_health._LANES` broker `fire_hours_et` (~284);
- optionally, a live activities read on 💰 Account. That needs the account selection (`cron_runner.py` ~3561-3603) pulled out into a pure `broker_sync` function, or the app and the cron could pick different accounts.

**Removes:** no manual step, only waiting time.

**New risk:** wasted calls and noise if SnapTrade refreshes Robinhood only once a day (#34c).

**Closes:** nothing on its own.

**Verdict:** defer until probe P.

### O2. The cron auto-links more (never writes `trades`)

**Changes:**
- In `_run_broker` step ② (`cron_runner.py` ~3737-3796), run TJDate's C5 matcher.
- Auto-apply only:
  - same-ET-date unique LINKs;
  - same-day many-to-one "reconciled" groups: sum within `BROKER_DRIFT_SHARE_TOL`, VWAP within `JOURNAL_MATCH_PRICE_TOL_USD` (TJDate owner decision 2).
- DDL: `snaptrade_pending_imports.linked_trade_id bigint` and `link_kind text`. A single `broker_txn_id` column can't link 2 fills to 1 row.
- Fix `backfill_trade_broker_txn_id` returning True on 0 rows (`db.py` ~6728-6746; TJDate C4 f/u (1)).

**Removes:** most "Already logged" decisions, such as the CSCO split-sell case.

**New risk:** a wrong link closes an unlogged fill for good. Guards:
- same ET date only;
- unique match only;
- a shadow week first;
- C5's UNLOGGED check treats a `linked_trade_id` that points at a deleted trade as unlogged again.

**Closes:** duplicates (mostly) and HoldDup's stale-pending source. Not N1 or N1-LogTime.

### O3. One-click "Record from Robinhood" plus a notes-owed queue (RECOMMENDED, with O2)

**Changes:**
- DDL: `trades.doc_status text` ('owed' | 'done' | 'waived'; NULL = legacy, not tracked) and `trades.documented_at timestamptz`.
- New pure `stock_analyzer/broker_record.py` (`_GATE_FILES`).
- New db functions:
  - an insert with three results (inserted / exists / None);
  - per-ticker holdings upsert and delete, sweeping only that ticker's stop;
  - a strict pending reader;
  - `update_trade_documentation`, limited to a whitelist of documentation columns.
- Pending Imports rebuilt (absorbs TJDate U9).
- The D0 guard extended.
- The owed queue on 📒 Trade Journal.

**Removes:**
- filling in the form for each fill;
- choosing the date, time and price;
- remembering to backdate;
- future fill-time SQL fixes.

**New risks:**
- weaker documentation discipline;
- a journal kept fill by fill;
- evening fill times expose readers that use the UTC date (C0 is a prerequisite);
- HoldDup at insert. `save_trade` returns True on a `broker_txn_id` unique violation (`db.py` ~2331-2335), so a double-click or a second tab would apply holdings twice. The three-result insert is needed.

**Closes:**
- **N1-LogTime** for recorded rows.
- **U7 failure mode 2** for recorded rows (a log-time SELL counted twice in `daily_budget.trades_after`, ~114-135).
- **The duplicate class.**
- **N1 heals itself, but isn't fixed at the source.** A fill recorded after EOD has the correct `traded_at ≤ cash_as_of`, so the nightly C5 repair fixes day d at d+1's EOD.

### O4. The cron records facts (Phase 2 only)

**Changes:**
- O3, plus an atomic DB function: trade insert, holdings row and pending link in one transaction, idempotent on `broker_txn_id`.
- Fix the stale-session class at all 7 `save_holdings` call sites (`app.py` 5706, 30986, 31176, 33476, 33674, 33939, 34295) with a book-version check.
- An explicit DB-outage probe in step ②. The comment at `cron_runner.py` ~3728-3736 accepts silent partial writes "because nothing here feeds a gate", and that stops being true.
- A per-run circuit breaker.
- Record a fill only when the book then equals the same run's snapshot for that ticker.

**Removes:** the record click.

**New risk:** the cron writes the book that every gate reads, with no human looking. A SnapTrade misreport, a wrong account or a symbol alias then moves gates. (Activities come from one account, `cron_runner.py` ~3602 and ~3738; positions sum across all accounts, ~3655-3658.)

**Closes:** N1 at the source, but only for fills SnapTrade shows by the evening run. That depends on #34c.

### O5. Full auto-write with no owed queue, or broker positions as the book: REJECT

- **Full auto-write:** undocumented positions pile up silently, which contradicts §2A.
- **Broker positions as the book:** already rejected in account-snapshot-repair.md ~64-69, for four reasons:
  - positions sum across accounts while cash is one account;
  - it is a single overwritten row;
  - it has no EOD prices for broker-only names;
  - #34c is unresolved.

  It would also split realized P&L, tax lots and R-multiple, which come from the journal, from the holdings.

## Phasing

### Prerequisites

**P (owner, read-only SQL): how late do Robinhood fills reach SnapTrade?** This is the practical #34c for the activities feed.
- **Method:** across `snaptrade_pending_imports`, all statuses, compare `fetched_at` (first seen, because of `ignore_duplicates`) with `raw_json->>'trade_date'`.
- **Report:**
  - the share of day-d fills first seen by day d's evening run versus the next day;
  - the most fills in one ET day (this calibrates the Phase 2 breaker);
  - whether today's NVDA 5 fill has a pending row yet.
- **Caveats:**
  - The sample leans toward fills that were never logged, which is fine for measuring lag.
  - The positions-side last-sync probe (#34c as queued) stays a Phase 2 prerequisite.
  - Phase 1 doesn't depend on freshness, because the owner records what is there.

**TJDate C5** (pure matcher, reviewer): already planned. Phase 1 uses it rather than a second matcher, so each fill/row pair gets one verdict.

**C0: the UTC-date reader sweep** (fixes the class; reviewer for the gate and DB sites).
- **Sites:**
  - TJDate C3b (`app.py` ~44839 → thesis_reviews; `cron_runner.py` ~2628);
  - #29 f/u (`tax_advisor` lot `buy_date`);
  - `decision_journal.py` ~126/140/150;
  - `portfolio_qa.py` ~455.
- **Why it's needed:** broker times after 19:00 EST / 20:00 EDT fall on the next UTC day. The 11-04 hand logs at 19:12 ET already hit this.

### Phase 1 chunks

1. **DDL plus db layer** (reviewer).
   - Files: `docs/sql/broker-fill-record.sql` (owner applies) and `db.py`.
   - The four columns above.
   - `doc_status` / `documented_at` added to `save_trade`'s `_optional` list, with `load_trades` backfilling None.
   - **`load_snaptrade_pending_imports_or_none`.** Today's reader returns [] on failure (`db.py` ~6570-6588), so 💰 Account says "✓ No pending imports" during an outage (`app.py` ~41332-41334).
   - The three-result insert.
   - Per-ticker holdings writes.
   - The documentation-only update.
   - The backfill `res.data` fix.
2. **`broker_record.py`** (reviewer; new `_GATE_FILES` member plus its reason in review-policy.md).
   - **`plan_record(pending, journal, holdings, snapshot, kinds, today, now)`.**
     - Each fill is recordable, or refused with a reason: oversell, older than `BROKER_RECORD_LOOKBACK_DAYS`, already linked, explained by a journal row (link it instead), malformed.
     - Non-trading ET dates are flagged. Fills are ordered by broker time.
     - **Each record carries:**
       - `traded_at` = the SnapTrade ISO timestamp;
       - the pending row's 2-dp price, so every existing content key still matches (classify Tier 2, CSV import, D0, `find_pending_match_candidates`);
       - `broker_txn_id`;
       - `asset_type` via `resolve_trade_asset_type`;
       - `trigger_type` None;
       - `doc_status` 'owed'.
     - **It also returns:** average-cost BUY deltas, SELL realized P&L via `trades.compute_realized_pnl` (~57), and a per-ticker "after recording, book vs Robinhood" against the latest snapshot.
   - **`owed_groups(journal)`,** keyed by (ticker, action, ET date).
3. **Cron auto-link** (reviewer). Files: `cron_runner.py`, plus `broker_sync.py` or the C5 module.
   - Shadow week first, logging "would auto-link N (exact X, group Y)".
   - Then apply, limited to the U6 scope.
4. **Pending Imports rebuilt** (reviewer plus screenshots). File: `app.py`.
   - Show C5's verdict on each row (TJDate U9).
   - "Record" per row, and "Record all N" with a preview: ET time, holdings before → after, realized P&L, and the verdict against Robinhood.
   - **Write path:**
     - strict pending, trades and holdings reads immediately before the write, never the session copy;
     - one ticker at a time: insert, then that ticker's holdings row;
     - on any failure, stop and say what was recorded.
   - Then pop `_budget_trades_cache`, call `_refresh_portfolio_cache_after_trade`, and reload strictly.
5. **D0 extension** (reviewer). Files: `trade_confirm.py` and its app.py wiring.
   - A hand entry whose ticker / action / ET-date group is already covered by recorded broker fills shows: "Already recorded from Robinhood: #a 5 @ 103, #b 3 @ 102. Add your notes to them instead."
6. **Notes-owed queue** (reviewer plus screenshots). Files: app.py, `broker_record.py` or a new `trade_documentation.py`, and `trade_analytics.py`.
   - "📝 Notes owed (N)" on 📒 Trade Journal, with the facts locked.
   - **BUY fields:** thesis (F-5 draft optional), Retrospective pre-mortem, trigger chip, situational category.
   - **SELL fields:** exit reason and lesson.
   - Pre-mortem trigger extraction runs at the notes click. It is an LLM call: optional, fails open, never in the record path.
   - Each item ends as 'done' or 'waived'.
   - `trade_analytics` gets an explicit "Not yet documented" bucket.
7. **Sunday debrief line** (optional; screenshot). "N trades still need notes (oldest D)" once past `TRADE_DOC_OWED_NOTE_SESSIONS`.
8. **Docs** (on the lead, since they carry policy values).
   - requirements.md: F-244 and a new F-ID.
   - architecture.md: schema, constants table, the new module.
   - The decision record in the snaptrade plan's §3.
   - The User Guide, the review-policy reason and the shipped-log.
   - The account-snapshot-repair residual text (imported fills no longer anchor at 16:00).
   - The TJDate Status line.

### Phase 2 (O4)

**Gated on all of:**
- the #34c positions probe;
- probe P showing most day-d fills arrive by the evening run;
- 4 weeks of Phase 1 live;
- the 7-site stale-book fix and the atomic DB function shipped.

Then about 10 trading days of shadow ("would record" against what the owner recorded), then fresh explicit owner approval. Every Phase 2 chunk needs the reviewer.

## Constants (proposed; owner approval needed)

| Constant | Proposed | Rationale |
|---|---|---|
| `BROKER_RECORD_LOOKBACK_DAYS` | 35 | Must stay ≤ `ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS` (35, test-pinned), so every one-click record falls inside the auto-repair window. Older fills go to the backfill script. |
| `TRADE_DOC_OWED_NOTE_SESSIONS` | 3 | Trading sessions before an owed item is mentioned beyond Trade Journal. |
| `BROKER_AUTO_RECORD_MODE` (Phase 2) | "off" | Then "shadow", then "on". |
| `BROKER_AUTO_RECORD_MAX_FILLS_PER_RUN` (Phase 2) | from probe P | Circuit breaker. |

Phase 2 would also reuse U7's `BROKER_FRAME_PAIR_MAX_MINUTES` (10) to pair the snapshot with the activities.

**Reused unchanged:**
- `BROKER_DRIFT_SHARE_TOL` 0.001
- `JOURNAL_MATCH_PRICE_TOL_USD` 0.01
- `JOURNAL_MATCH_WINDOW_SESSIONS` 3
- `IMPORTED_TRADE_ANCHOR_ET_HOUR` 16 (stays for CSV and no-time imports)
- `SNAPTRADE_SYNC_MAX_TXN_LOOKBACK_DAYS` 90

TJDate U4's `JOURNAL_MATCH_LOOKBACK_DAYS`, `_BROKER_LAG_SESSIONS`, `_MAX_FILLS` and `_MAX_CANDIDATES` land with C5.

## Owner decisions (planner's recommendation first)

- **U1.** Amend Option A: a broker fill may be recorded as a fact by one owner click, without notes, and its notes are tracked as owed. Yes.
- **U2.** Where the pre-mortem gate applies.
  - Hand-typed BUYs: the gate (~32286) stays at submit.
  - Recorded BUYs: it moves to the notes step. "Done" requires a pre-mortem; "Skip" is allowed as 'waived', with a visible count.
  - Alternative: no waiving.
- **U3.** `traded_at` = SnapTrade's timestamp. Recommended, knowing it is the order placement time: equal to the fill for marketable orders, earlier for a resting limit (CSCO was 37 min early, trade-entry-guard.md ~112). Alternative: keep the 16:00 anchor, which keeps the after-hours residual.
- **U4.** One journal row per fill (recommended). Alternative: same-day consolidation, which needs a link table and loses per-fill identity.
- **U5.** Allow "Record all" with a preview (recommended). This relaxes "every row a deliberate individual action".
- **U6.** The cron auto-links only same-ET-date unique LINKs and same-day reconciled groups. Cross-day matches stay a hint: a cross-day "match" is often a wrong-dated journal row that N1 needs corrected, not linked.
- **U7.** A shadow week for auto-link. Yes.
- **U8.** Hand entries covered by recorded fills.
  - Fully covered: a hard block, pointing to "add notes".
  - Partly covered: asks for a tick.
  - This is consistent with D0's broker hard block.
- **U9.** Where owed notes surface.
  - Trade Journal and the Sunday debrief only. Never Act Today, and no new Home banner.
  - If Home at all, join TJDate U8's single journal-vs-broker line.
- **U10.** Approve the two Phase 1 constants.
- **U11.** SELL notes are owed too, but light. `STOP_HIT` drives self_track_record's bucket (~699-701).
- **U12.** `decision_context` is captured at the record click and flagged retrospective, not at notes time. Readers to check: `investor_mirror.py`, `signals_headline.py`, `ticker_history.py`.
- **U13.** Resolve a contradiction in the docs. account-snapshot-repair.md ~198 says the owner "never trades extended-hours", but trade-entry-guard.md ~112 shows GOOGL filled at 4:06 PM EDT, in extended hours. This decides whether the C5 after-hours residual still matters. C0 is needed either way.
- **U14.** TJDate order becomes C1 → C5 → C0 / C3b → Phase 1 here → C2 (re-scoped to fills SnapTrade doesn't show) → U8.
- **U15.** No cadence change until probe P.
- **U16.** Phase 2 constants and gate are decided at the gate, not now.

## Interactions with in-flight plans

- **TJDate:**
  - C5 is a hard dependency, and U9 folds into chunk 4.
  - C1 stays, because it guards hand entry. The record path skips it, as the broker prefill does.
  - C2's strict `load_snaptrade_unlinked_fills_or_none` must also drop rows with `linked_trade_id`.
- **U7 net-capital broker frame:** independent and still needed.
  - It covers the gap between a sync and the record click.
  - Recording with broker time removes U7 failure mode 2 at the source.
  - U7's shadow readout is the evidence for the Phase 2 gate.
- **account-snapshot-repair:**
  - C5's `late_trades` rule becomes accurate for recorded fills.
  - The C8 docs must describe the new time source.
  - The 09-22/29/30 explained-gap repair is untouched.
- **#34c:** a prerequisite for Phase 2 and for any cadence change, not for Phase 1.

## Risks

- **StaleBook.** Two tabs can already overwrite each other's holdings today. Register it in the bug register, naming the 7 sites. Phase 1 avoids it with fresh reads and per-ticker writes; Phase 2 needs it fixed.
- **HoldDup.** The three-result insert is mandatory.
- **The pending reader returns [].** It must become a strict read that can report "offline" (`None` = couldn't read).
- **Coordination.** If Home gets a line, publish one three-state key, register it in `coord_freshness.py`, and share it with TJDate U8.
- **Account-selection drift.** In Phase 1, the preview shows the gap. In Phase 2, the cron refuses to record.
- **Dividend reinvestment.** "REI" activities count as income (`broker_sync.py` ~83) while the share count changes. Named only.
- **Row-count analytics distortion.** Named, and partly handled in chunk 6.

## Tests the build must include

- **Re-sync dedup:**
  - a second pass plans nothing;
  - a double-click or two tabs apply holdings exactly once;
  - a re-fetched txn id never re-queues.
- **Partial fills:** BUY 8 arriving as 5 at the 16:15 sync and 3 the next day gives two rows, holdings of 8 and one owed group.
- **Sale split across fills:** SELL 5 + 3 gives summed realized P&L equal to one 8-share average-cost sell, and the average cost is unchanged.
- **Oversell:** refused with a reason, and holdings never go negative.
- **Hand-logged AND imported:**
  - hand row first: a same-day LINK;
  - the CSCO 11-04 fixture: 2 rows and 1 fill give 1 LINK plus 1 JOURNAL_ONLY;
  - fills recorded first: U8's block or tick;
  - a cross-day match is never auto-linked;
  - a linked trade that is later deleted reads UNLOGGED again.
- **SnapTrade outage:**
  - a pending read that returns `None` shows "couldn't read";
  - activities `None` writes nothing;
  - a failed auto-link write gives rc=1 and the failure email.
- **Wrong account:** a fill missing from the selected account leaves drift as `rh_only`.
- **After hours:**
  - a 16:30 ET fill after a 16:15 cash read is not added to day d;
  - a 19:30 EST fill gets the ET date at every C0 site.
- **DST:** fall-back 2026-11-01 and spring-forward 2027-03-14, with `Z` against `-04:00` / `-05:00` timestamps.
- **Weekend / holiday:**
  - a Saturday record of Friday fills is repaired by the Sunday debrief pre-step;
  - a non-trading ET date is flagged;
  - Thanksgiving, 2026-11-26.
- **Late, both directions:**
  - a BUY and a SELL recorded 1 and 3 sessions late get the right C5 repair;
  - the lookback boundary: exactly at `BROKER_RECORD_LOOKBACK_DAYS` is recordable, one day past it is not;
  - the ≤ `ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS` pin.
- **Invariants:**
  - the documentation update can never change the trade's facts (whitelist test);
  - no LLM import in `broker_record.py` (AST pin);
  - the record path never calls `save_holdings` full-replace (AST pin);
  - the recorded price equals the pending row's 2-dp price;
  - `trade_analytics` counts NULL-trigger rows as "Not yet documented".
- **Phase 2:**
  - a fill is recorded only when the book then equals the snapshot within `BROKER_DRIFT_SHARE_TOL`;
  - the breaker: at the limit the run records, and one fill over it refuses all;
  - a skipped snapshot means nothing is recorded.
