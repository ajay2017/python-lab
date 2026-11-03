# Trade entry guard + journal vs Robinhood reconciliation (bug register TJDate)

**Status:** DESIGNED 2026-11-02 (Opus planner: PROCEED WITH CHANGES). Owner decisions 2026-11-02 (below), QUEUED behind HoldWipe and LockBreach. NEXT: a confirming planner pass on the revised scope, then the P0 probe. Nothing built.

**Owner decisions 2026-11-02, after the lead's review:**
1. **Two phases.**
   - **Phase 1:** P0, C4, C1, C2 (with U1–U4 and U12 as recommended), C3 (U10), and C5 as a READ-ONLY comparison. Phase 1 replaces the Pending Imports "Already logged" advice (U9) and adds the Home awareness line (U8).
   - **Phase 2:** C6–C8 one-click corrections, the `journal_corrections` table and the DB function. Built only if, after Phase 1 is live, the comparison shows 3 or more real mismatches in 4 weeks, or any that touch account rows. U5 / U6 / U7′ / U11 are decided at that gate.
2. **Same-day combined fills are reconciled, not a finding.** Fills on one ET date that sum to the journal row (`BROKER_DRIFT_SHARE_TOL`) with VWAP within `JOURNAL_MATCH_PRICE_TOL_USD` are linked silently.
   - Example: MRVL 10-30, 5 @ 200.00 + 3 @ 191.96 = 8 @ 196.99.
   - Only cross-day merges, share-count gaps and price differences are flagged.
3. **The confirm card states the trade date in words** ("Logging as Thu Oct 29"). The 10-28 error was a next-day log during market hours, which the default-date rule can't catch.

Source: Opus planner pass 2026-11-02 on the TJDate row, extended with the owner-agreed additions of 2026-11-02: price outside the day's range, time outside regular hours, Trade History in ET.

## Why

Hand entry in 📒 Trade Journal went wrong three ways in six days:
- **10-28: wrong date and merged fills** (the N1 cause), fixed by `docs/sql/journal-fix-2026-10-28-fills.sql`.
- **11-02, wrong time:** OKTA re-logged at 17:08 EST instead of the 14:43 fill.
- **11-02, wrong price:** OKTA at 171.88 instead of 189.14.

The broker-vs-journal check of 11-02 (`docs/sql/readonly-pending-imports-check.sql`) found more:
- Robinhood splits partial fills, while the owner logs one combined row.
- The Pending Imports "already logged" advice led to 172 dismissals.
- One fill was never logged (GOOGL 10-30, fixed by `docs/sql/journal-fix-2026-10-30-googl.sql`).

## Planner changes to the agreed shape

1. **Corrections are delete + insert in one atomic DB function, never in-place edits.**
   - N1 finds lateness only by `id > journal_max_trade_id` (`account_snapshot_repair._late_prepped`).
   - An in-place re-date keeps its id, so it is never "late": the account rows in between stay silently wrong.
   - An in-place split is refused as "unexplained" every night.
   - Delete + insert (new ids) repairs exactly. This holds only for back-dating with total shares unchanged. Forward moves and quantity changes stay display-only in v1.
2. **Stop the cause:** never store a log-time "now" outside regular hours; capture the fill time instead. For new rows this also closes:
   - the N1 "logged after EOD" residual;
   - the #29 lot `buy_date` UTC follow-up;
   - the broker auto-link miss on evening logs.
3. **The reconciliation card is computed from the DB on every render,** and it replaces Pending Imports' "Already logged" advice for every row it explains. There is one verdict per journal-row/fill pair.
4. **A log-time match with a pending import switches the form to the existing broker-prefill path,** which locks the date, shares and price and sets `broker_txn_id`. It doesn't merely change the date.

## Owner decisions (open)

- **U1 — Fill time (ET) input:**
  - today, market open: default now (editable);
  - today, market closed: required;
  - past date: optional (16:00 ET anchor), but required when another same-ticker row exists that ET date.
- **U2 — Default trade date:** today if it is a trading day and past 09:30 ET, else the previous trading day.
- **U3 — Tick-box gating** ("I've checked, record it anyway"):
  - price outside the session range;
  - weekend or holiday date;
  - time outside 09:30–close;
  - broker shares that date > entry.

  A future time today is a hard block.
- **U4 — New constants** (proposed values):

  | Constant | Value |
  |---|---|
  | `TRADE_LOG_PRICE_BAND_TOL_PCT` | 1.0 |
  | `TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS` | 35 |
  | `JOURNAL_MATCH_WINDOW_SESSIONS` | 3 |
  | `JOURNAL_MATCH_LOOKBACK_DAYS` | 35, must stay ≤ `ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS` (test-pinned) |
  | `JOURNAL_MATCH_PRICE_TOL_USD` | 0.01 |
  | `JOURNAL_MATCH_BROKER_LAG_SESSIONS` | 2 |
  | `JOURNAL_MATCH_MAX_FILLS` / `JOURNAL_MATCH_MAX_CANDIDATES` | 6 / 12 |
  | `NYSE_REGULAR_OPEN_ET` / `NYSE_REGULAR_CLOSE_ET` | (9, 30) / (16, 0); early closes from `NYSE_EARLY_CLOSES` |

  The partial-fill sum uses the existing `BROKER_DRIFT_SHARE_TOL`.
- **U5 — New table and function:** `journal_corrections` (audit + undo) and an atomic `journal_apply_correction(p jsonb)` (`docs/sql/journal-corrections.sql`, owner applies). With no function there is no one-click, and no non-atomic fallback.
- **U6 — v1 one-click scope:**
  - one-click: LINK, REDATE-back, SPLIT-back, PRICE_FIX;
  - UNLOGGED: the existing "Log This Trade →" prefill;
  - display-only with instructions: QTY_MISMATCH, FORWARD, AMBIGUOUS, TOO_MANY, JOURNAL_ONLY;
  - v2 (its own item): an N1 repair that understands deleted rows, which would also make the Trade History Delete visible to N1.
- **U7′ — Owner-written fields** (thesis, decision context, pre-mortem, …) go on the latest fill, exactly once. `notes` / `trigger_type` / `asset_type` are copied to every new row.
- **U8 — Home ⚠️ Alerts awareness line:** "N journal rows differ from Robinhood's fills → 💰 Account", deduped against the broker-drift line. Never in Act Today.
- **U9 — Pending Imports** shows the matcher's verdict instead of "Already logged". It keeps "Already logged" only for AMBIGUOUS / TOO_MANY.
- **U10 — 📋 Trade History:** times in ET, the column labelled "Date / Time (ET)", and "—" when the time is exactly the 16:00:00 anchor.
- **U11 — Held-stop note:** the card says when a correction moves a held position's entry date or first fill price. Text only.
- **U12 (optional) — Duplicate check at log time:** same ticker, action and shares already on that ET date.
- **On U7 (sizing):** rank "broker sold, journal didn't" first. TJDate doesn't change sizing math. Its `unlogged_sells` output is U7's day+1 input; U7 still needs a same-day path (drift).

## Chunks (order: P0 → C4 → C1 → C2 → C3 → C5 → C6 → C7 → C8 → C9)

- **P0. Read-only owner SQL probe** on `snaptrade_pending_imports.raw_json`:
  - Does `trade_date` carry a time?
  - Is `amount/units` more exact than the 2-dp `price`?
  - Is there an order-grouping id?
  - Do all `logged` rows have a matching `trades.broker_txn_id`?
- **C1. Constants + pure entry checks** (reviewer): new `stock_analyzer/trade_entry_checks.py` (`_GATE_FILES`):
  - `default_trade_date`, `session_hours`, `fill_time_requirement`, `resolve_traded_at`;
  - `time_findings`: NON_TRADING_DATE / OUTSIDE_RTH / FUTURE;
  - `price_band_finding`: Yahoo unadjusted daily bar, band ± tol, today labelled "so far", `unchecked` with a reason, never a silent in-band. It doesn't reuse `held_stop.fill_in_band`, which widens to the prior close.
  - `pending_fill_findings`: MATCH_SAME_DAY / OTHER_DATE / OTHER_DATE_SPLIT / SHARES_SHORT / couldn't check.
- **C2. Wire the log-time checks** (reviewer + screenshots):
  - the fill-time input and default date;
  - every manual entry sends an explicit `traded_at` (drop the omit-key → `now()` branch ~32433–32440);
  - findings on the confirm cards (~30779 / ~30923), with tick-boxes keyed to the staged record;
  - a new strict `db.load_snaptrade_unlinked_fills_or_none`;
  - fold in: the bare 0.5 / 2.0 at ~31854 → `TRADE_PRICE_SANITY_RATIO_*`;
  - broker-prefill / imports skip the checks.
- **C3. Trade History in ET** (display only, no review): formatter in `trade_time.py`; also show exact shares (`%.0f` hides fractions, the FracNew class).
- **C4. Broker-sync ET date** (reviewer):
  - the `broker_sync` Tier-2 key (~475) and `find_pending_match_candidates` (~729–733) use the ET date of `traded_at`, not the UTC date;
  - `window_days=3` → sessions constant, or retired if U9.
  - Independent; ship early.
- **C5. Pure matcher** `stock_analyzer/journal_broker_match.py` (reviewer, `_GATE_FILES`):
  - matching order: LINK → REDATE / PRICE_FIX → SPLIT (subset sums, one-click only if unique) → many-to-one reconciled → QTY_MISMATCH → UNLOGGED → JOURNAL_ONLY (suppressed under lag or a stale sync) → FORWARD downgrade;
  - output None / [] / findings ranked UNLOGGED SELL first.
- **C6. Pure correction planner** `stock_analyzer/journal_correction.py` (reviewer, `_GATE_FILES`):
  - one insert per fill with the broker's shares, price and `broker_txn_id`; SELL basis kept from the original;
  - validation: total shares preserved; every new date a trading day and ≤ the original; no oversell in the full replay (an anchor-placed buy after a same-day sell is withheld);
  - preview: recalculated holdings and realized P&L, held-stop episode before/after, and affected account dates tagged auto / legacy / outside_lookback / no_row with the "next repair run" text.
  - It instructs; it never triggers the repair.
- **C7. DDL + db wrappers** (reviewer, owner applies):
  - `journal_corrections` + `journal_apply_correction`, which checks the expected rows exactly, then deletes, inserts, flips pending imports and writes the audit row atomically. LINK is a kind of it.
  - `db.apply_journal_correction` returns None when the function is missing; never a fallback.
- **C8. UI** (reviewer + screenshots):
  - a "🔗 Journal vs Robinhood" section on 💰 Account → Broker Sync above Pending Imports;
  - confirm → DB function → strict reload → recompute and compare with the preview → apply holdings + realized → pop `_budget_trades_cache` → refresh;
  - the U9 hints, a Trade Journal pointer, the U8 Home line.
- **C9. Docs** (lead): requirements F-ID, architecture constants and schema, User Guide, review-policy, bug register, shipped-log, this plan's Status line.

## Key invariant test (C6)

For every one-click kind:
1. Build provenance account rows from the wrong journal.
2. Apply the planned delete + insert with ids above max.
3. Run `plan_repairs`.

Expect zero refusals, exact repaired gross, rows on or after the old date untouched, and FORWARD / QTY payloads refused. A second run plans nothing.

Full test list: time-context matrix (weekend, Thanksgiving 11-26, early close 11-27, RTH boundaries, DST, same-day after close, pre-open, same-ticker same day), price band (OKTA 11-02 regression, boundaries, unchecked reasons), pending-fill findings (10-28 fixture, GOOGL 10-30), matcher fixtures (MRVL 10-30 split, #444–446, GOOGL 10-30, OKTA price fix, LINK, AMBIGUOUS, TOO_MANY, FORWARD, reconciled, lag), planner invariants, the lookback assertion, never-without-a-click AST pins, DB wrapper mocks.

## Sibling sweep (fix or name)

- `broker_sync` UTC date (C4).
- `window_days=3` literal (C4).
- `_activity_date` UTC (P0).
- app.py ~31854 bare 0.5 / 2.0 (C2).
- Trade History `%.0f` shares (C3).
- `tax_advisor` lot `buy_date` UTC (legacy rows remain).
- Trade History Delete has no audit and is invisible to N1 (v2).
- **The delete / bulk-import / screenshot-import auto-rebuilds use the lenient `db.load_trades()`. A failed read rebuilds from an empty frame, and `save_holdings` then sweeps every holdings row AND every manual stop.** Registered as bug HoldWipe; fix it before TJDate.
- `data.market_status` 9.5 / 16.0 literals.
- `dismiss_snaptrade_pending_import` has no undo.
