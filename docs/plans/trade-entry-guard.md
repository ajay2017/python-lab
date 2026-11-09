# Trade entry guard + journal vs Robinhood reconciliation (bug register TJDate)

**Status:** C1 BUILT 2026-11-09 (new pure `stock_analyzer/trade_entry_checks.py`, `_GATE_FILES`; the four U4 constants `TRADE_LOG_PRICE_BAND_TOL_PCT` 1.0, `TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS` 35, `NYSE_REGULAR_OPEN_ET` (9, 30), `NYSE_REGULAR_CLOSE_ET` (16, 0); not wired into app.py; Opus FIX-FIRST (1 blocking: the other-date text read SnapTrade's order-placement date as the fill date) → SHIP, 0 blocking). NEXT: C2, see "C1 review deferrals for C2" below. Earlier: C4 BUILT 2026-11-07 (broker-sync ET date on both sides of the Tier-2 key, `JOURNAL_MATCH_WINDOW_SESSIONS` = 3 trading sessions for the Pending Imports hint, strict cron trades read, still-pending imports already linked closed as logged (HoldDup source), the same ET date in `broker_import` / `broker_screenshot`, `broker_import.py` added to `_GATE_FILES`; Opus SHIP, 0 blocking). NEXT: C1. CONFIRMING PASS DONE 2026-11-06 (Opus planner: PROCEED WITH CHANGES; see "Confirming pass 2026-11-06" below). C3 BUILT 2026-11-06 (`0e7fb78`, Trade History in ET). P0 RUN 2026-11-06 (results below). D0 BUILT 2026-11-06 (F-81e, duplicate-entry warning). New Phase 1 order: ~~C3 (TJUtc)~~ → D0 (TJDup) → P0 (owner, in parallel) → C4 → C1 → C2 → C3b → C5 → U9 → U8 → docs. HoldWipe (`6b3d930`) and RbPartial (`2ca2f29`) have shipped. Earlier: DESIGNED 2026-11-02 (Opus planner: PROCEED WITH CHANGES), owner decisions 2026-11-02 below. Nothing built yet.

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

## Confirming pass 2026-11-06 (Opus planner, PROCEED WITH CHANGES)

Re-checked against HEAD `b1dfcf7`. Nothing in Phase 1 rebuilds holdings, so RbPartial (`journal_rebuild`) is untouched; restate that if a chunk ever adds a rebuild.

**Owner decision 2026-11-06 (TJDup):** a likely duplicate trade entry WARNS, not blocks: a visually prominent coloured panel naming the matching row, and one explicit tick ("this is a separate fill") to record it.

**New order:** C3 → D0 → P0 (owner, in parallel) → C4 → C1 → C2 → C3b → C5 → U9 → U8 → docs.

- **C3, Trade History in ET (bug TJUtc), mechanical, no review.** Goes first because D0's warning needs the ET formatter. `trade_time.et_display(value, time_known)` and `trade_time.et_trade_date(value)`; the placeholder test is "exactly 16:00:00.000000 ET wall clock, or `traded_at_time_known is False`" (never a fixed UTC hour: DST). Column "Date / Time (ET)", date with "—" for a placeholder time, exact shares. `display_df` keeps `trades_df`'s index (DelIdx). Sibling sites: already correct app.py ~5971, ~10307, ~41852, ~47034, ~39669; UTC `[:10]` date slice, fix here (display): ~11099→11108/11115, ~30176→30183, ~20670→20676; ~50037 counts 16:00 anchors as real fill times in the opening-window pattern (use the placeholder helper). Named only: `decision_journal.py` ~126/140/150, `portfolio_qa.py` ~455.
- **C3b (DB write → reviewer):** app.py ~44839→44845 feeds `thesis_reviews.trade_date` (writes ~45277 / ~45351) and the Red Team thesis age (~47533) with the UTC `[:10]` slice; it must change together with cron_runner.py ~2628 (gate file, same slice).
- **D0, TJDup entry guard (decision-bearing → reviewer + screenshots).** New `stock_analyzer/trade_confirm.py` (→ `_GATE_FILES`, reason in `docs/review-policy.md`).
  - Paths: every Log Trade submit (incl. the recommendation-card prefill) and the broker-prefill path. On the broker path a staged `broker_txn_id` already on a trades row is a HARD block (exact identity, points to "Already logged"); otherwise only unlinked rows are compared. Not the CSV / paste imports: they already pre-untick content matches (app.py ~33715-33736).
  - Match: same ticker (upper/strip) and action; shares within `BROKER_DRIFT_SHARE_TOL` (rounded to 9 dp); each row's ET date via `trade_time.normalize_traded_at` (idempotent); `sessions_apart` = trading days in (earlier, later]. `sessions_apart == 0`: always a match ("same price" within `JOURNAL_MATCH_PRICE_TOL_USD`, else "same size, different price"); `1 ≤ sessions_apart ≤ TRADE_LOG_DUP_WINDOW_SESSIONS`: a match only within the price tolerance.
  - One strict read at staging (`db.load_trades_or_none()`; session `trades_df` in no-DB mode): `None` → a visible "couldn't check for a duplicate" line, no tick; `[]` → clean.
  - One confirm card, not stacked tick boxes: `confirm_gate(findings)` → `{hard, needs_tick, tick_label, blocks}`; red-bordered panel per matching row ("#id · Wed Nov 4, 19:14 ET · BUY 3 @ $102.00 · Recommendation", "linked to Robinhood" when it is); ONE checkbox keyed `_tj_ack_{idempotency_key}`; Confirm disabled until ticked, always on a hard finding; C2 later adds its findings to the same list. `safe_html`; `$` as `&#36;`.
  - Constants: `JOURNAL_MATCH_PRICE_TOL_USD` = 0.01 (U4, agreed; lands here instead of C1); `TRADE_LOG_DUP_WINDOW_SESSIONS` (new, owner call: 1 recommended or 0). Both into the architecture constants table. `TRADE_DUP_SUBMIT_WINDOW_SEC` and the idempotency key unchanged.
  - Needs `data.previous_trading_day` / `trading_sessions_between` (only `data.is_trading_day` exists, data.py ~165).
- **P0:** owner runs `docs/sql/readonly-tjdate-p0-probe.sql` (Q1–Q7; Q6 counts HoldDup triggers, Q7 calibrates D0 and surfaces any other live duplicate).
- **C4 additions:** `broker_import.classify_against_existing` uses the UTC `edt.date()` (broker_import.py ~300-302; fails safe, wrong label); cron_runner.py ~3685 feeds `classify_transactions` from the lenient `db.load_trades()` (switch to `load_trades_or_none()`, skip with `failures.append` / rc=1 on None); stale "pending" rows already linked (HoldDup). Recommend `broker_import.py` → `_GATE_FILES`.
- **C1 corrections:** `NYSE_EARLY_CLOSES` holds float hours (constants.py ~1570); fills are regular-hours when 09:30 ≤ t ≤ close, close inclusive (closing-auction prints at 16:00:00); `data.market_status`'s `< 16.0` is a different question, leave it.
- **C2 corrections:** line numbers moved (omit-key → now() ~32445-32493; SELL card ~30831-30974, BUY card ~30976-31244); the "bare 0.5 / 2.0" hint at ~31907 and the submit gate ~32051-32067 move into a pure `price_sanity()` in the C1 module (retire baseline keys 164/165/189). New gap: the replay orders by `traded_at`, `id` (db.py ~3028), so a same-day SELL logged with a 14:00 time lands before a past-dated BUY on the 16:00 placeholder and reads "SELL with no prior BUY"; the existing guard (~32131) checks total shares only. Add an as-of-time replay hard block ("delete the BUY in Trade History and re-log it with its time"). `db.load_snaptrade_unlinked_fills_or_none` pages via `_read_all_pages` and drops pending rows whose txn id is already on a trade.
- **C5:** add a CSCO 11-04 fixture (two journal rows, one fill → one LINK + one JOURNAL_ONLY). **U9:** Pending Imports now ~41252-41386 ("Already logged" ~41377). **U8:** register any published matcher key in `coord_freshness.py`, three-state.
- **Review-required:** D0 (`constants.py`, `trade_confirm.py`, `pre_tool_checks.py`, `data.py`, app.py wiring); C3b; C4 (`broker_sync.py`, `cron_runner.py`, `broker_import.py`); C1 (`trade_entry_checks.py`); C2; C5 (`journal_broker_match.py`); U8/U9 (`coord_freshness.py`). C3 none.
- **Named, not in scope:** the split-apply `save_trade` (app.py ~5713); FracNew `int(_pb_shares)` (~31089); Trade History Delete is invisible to N1, so the account rows for Nov 4–6 may still count 16 CSCO after the duplicate was deleted (check in N1 C7); the SELL-without-holding block names the wrong override checkbox (~32105 / ~32115) and the help hardcodes "±50%" (~31408) (wording).

## C1 review deferrals for C2 (Opus review 2026-11-09)

C2 must handle these; none is a C1 defect.

- **Freshness (must land with C2).** `pending_fill_findings` returning `[]` only means no unlinked fill conflicts. Most hand logs happen the same day, before the broker sync, so an entry dated after the last sync was never checked. Add a `synced_through` input (None / a distinct "not synced yet" state past it), or never render `[]` as "matches Robinhood" (one freshness per number).
- **Split in the window.** `data.fetch_unadjusted_history` is split-adjusted and dividend-unadjusted, so pass `bar=None` (unchecked) when a split falls between the trade date and today.
- **Price differs, same day (owner / planner call).** A same-day fill with the same shares and a different price returns nothing (OKTA 11-02: entry 171.88 vs fill 189.14), so only the price band catches it, and it goes unchecked without a Yahoo bar. A PRICE_DIFFERS tick would follow the D0 precedent (about one tick a month).
- **Typed 16:00 on an early-close day.** On 2026-11-27 (13:00 close) an owner-typed 16:00 equals the anchor, so it never gets OUTSIDE_RTH. Pass "the time was supplied", or name it with `trade_time.is_placeholder_time`'s 16:00 caveat.
- **`so_far` after the close.** It is True for any entry dated today, even after 16:00 when the bar is final. Let C2 word it with `now_et`.
- **U2, the pre-market default (owner call, asked 2026-11-09).** The owner trades extended hours, so a pre-market fill logged before 09:30 defaults to the previous session with an optional time: the 10-28 wrong-date class, silent until the sync. Options: (a) keep U2; (b) default today from midnight; (c) no default before 09:30, with the date in words and a required time (lead's recommendation).
- **Small:** reword MATCH_SAME_DAY to "Robinhood's record of this order is dated …" like the other kinds; link `txn_ids[0]` only.

## P0 probe results (owner ran `docs/sql/readonly-tjdate-p0-probe.sql`, 2026-11-06)

- **Q1:** all 191 `snaptrade_pending_imports.raw_json` rows carry the same 17 keys: amount, currency, currency_universal_symbol, description, external_reference_id, fee, fx_rate, id, institution, option_symbol, option_type, price, settlement_date, symbol, trade_date, type, units.
- **Q2:** 191 / 191 `trade_date` values carry a time, 0 are midnight UTC, 0 have an ET date different from the stored date. Q2b: times vary to the second, `settlement_date` is midnight UTC on T+1. **CORRECTED 2026-11-06 after the owner's Robinhood check:** the times are NOT fill times. CSCO 3 @ 102 (Nov 4) reads `2026-11-04T16:39:32Z` = 11:39 EST in SnapTrade, but Robinhood shows "Filled Nov 4, 2026 at 12:16 PM EST" (37 minutes later); most likely SnapTrade's time is the order's placement time. Consequences: (1) C2's fill-time capture can't take the time from SnapTrade — the owner's entry (Robinhood's "Filled" line) is the source, or the field stays optional; (2) the ET date is right for orders placed and filled the same day; (3) RESOLVED 2026-11-06 (owner checked GOOGL 1 @ 300.99, Oct 28): Robinhood "Filled Oct 28, 2026 at 4:06 PM EDT" (a $306.81 limit that filled at once, in extended hours) equals SnapTrade's `20:06:22Z` exactly. Both checks fit "SnapTrade's time = order placement time": equal to the fill for a marketable order, earlier for a resting limit (CSCO). The after-hours order filled the same day, so its date is right; a next-session fill of an after-hours placement would still carry the placement date, but none has been seen, so the ±1-session allowance (`JOURNAL_MATCH_BROKER_LAG_SESSIONS`) stays a safety net, not a known fix. The owner trades in extended hours, so C1's outside-09:30–close tick (U3) will fire on legitimate fills like this one. `_activity_date`'s UTC date is right on every stored row; the UTC-date bug is latent.
- **Q3:** `raw_price` is exact and equals amount / units on every row; the stored `price` is rounded to 2 dp (SPCX 136.425 → 136.43, OKTA 224.405 → 224.41, WAL 75.985 → 75.98, FSLR 179.3037 → 179.30; at most half a cent per share). Fee 0.0 on every row. C2 / C5 compare against `raw_price`; `JOURNAL_MATCH_PRICE_TOL_USD` 0.01 absorbs the rounding.
- **Q4:** `external_reference_id` is null on every row: no order-grouping id. C5 groups by ticker + action + ET date (sum + VWAP), as decided 2026-11-02. Split fills are routine (≥ 30 two- or three-fill days); CSCO 11-04 = 2 fills, 8 shares (5 @ 103 + 3 @ 102).
- **Q5:** dismissed 172 (12 linked, 160 not), logged 18 (12 linked, 6 not), pending 1 (not linked). The 6 logged-unlinked: CSCO 13902 (3 @ 102, 11-04; most likely the Pending-Imports-logged row deleted as the TJDup duplicate on 11-06, the kept RECOMMENDATION row represents the fill unlinked) and five from the mid-August rebaseline era (SPCX 1563 / 1564 08-24, DELL 812 08-20, DELL 588 08-18, SHOP 587 08-18). No new integrity problem.
- **Q6:** not run; Q5 shows no pending row linked to a trade, so HoldDup has no live trigger. The one pending row (CSCO 13903, 5 @ 103, 11-04) is already in the journal as a MANUAL row: owner to dismiss it (logging it would double-count).
- **Q7 (D0 calibration, 21 same ticker / action / shares pairs within 3 days):** same day + same price 0; adjacent session + same price 0 (smallest price gap 0.38); same day + same size + different price 5 (HOOD 10-14, COF SELL 09-10, DELL 09-01, WDAY 08-27, PATH 06-03), all genuine separate fills. No exact duplicate remains in the journal.
- **Owner decisions 2026-11-06 (on the Q7 numbers):** `TRADE_LOG_DUP_WINDOW_SESSIONS` = **1**; KEEP the same-day, same-size, different-price leg (about one tick a month on genuine split fills, in exchange for catching a wrong-price re-log like OKTA 11-02).
- **Known gap D0 does not close:** a combined journal row (e.g. 8 @ 196.99) plus a later log of one of its fills (5) differ in shares, so D0 doesn't warn; C5 detects it after sync.

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
