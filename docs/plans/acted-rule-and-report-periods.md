# Acted rule + report periods (MonthlyRollingRow + MRVLActed + DebriefEnterNow)

**Status:** DESIGNED 2026-11-17 (Opus planner, verdict PROCEED WITH CHANGES).
- **Owner answered 2026-11-17, all as recommended:**
  - U12 calendar months
  - U14 preview + keep the emailed version
  - U4 N = 2 trading days
  - U8 all three types in both reports, `already_held` Enter Now excluded
- **Owner confirmed U1, U3, U5–U7, U9–U11, U13 and U15 as recommended (2026-11-17).** Probes P1–P3 are optional. **C1 BUILT 2026-11-17** (implementer; Opus FIX-FIRST with 2 blocking (the `as_of` outcome span; the weekly trajectory not period-filtered) → fixed → confirming round SHIP, 0 blocking). Uncommitted until the hook's suite fits under its time limit (tests-only speed-up in flight). **C4 carry-forwards from the C1 confirming round** (wording, non-blocking):
  - (a) the `as_of` prompt line says "(the run date)" even when a caller omits `as_of`; drop it or show the line only when `as_of != period_end` (golden regen);
  - (b) the `preview_unchecked` banner should name its period;
  - (c) on a replaced row the emailed-preview banner should read "an earlier version was emailed; the saved version is kept";
  - (d) the legacy live-fallback "Outcomes measured to" caption vs the stored `engine_alpha_pct` (logged as MonthlyFallbackCost).
- **C1 SHIPPED 2026-11-17 (`68ca690`). C2 BUILT 2026-11-18** (uncommitted until the Opus review; planner refresh at HEAD `a06aa28`: PROCEED WITH CHANGES). New pure `stock_analyzer/entry_call_action.py` (`_GATE_FILES` 97), public `predictive_analytics.advance_trading_days` (old name kept as an alias), `ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS = 2`, `tests/test_entry_call_action.py`. Three changes to the plan's C2 text: `apply_to_matched(matched, recs_df, trades_df, *, cutoff)` also takes `recs_df` (`match_recs_to_trades` doesn't carry `already_held`); the cut-off is named `cutoff`, not `as_of` (C1's `as_of` means the run date); it returns None when trades or recs are unreadable. **Owner decisions 2026-11-18 (accepted the planner's four recommendations):** (a) a legacy weekend / holiday `rec_date` counts sessions from the stored date; (b) the A4 same-day cross-type dedup still applies to `acted_on` / `acted_trade`, but the losing row keeps `entry_call_status = "acted"` and gets `credit_deduped = True` (C4 must leave it out of both arms); (c) when several BUYs qualify the earliest grades the call; (d) a blank `already_held` is judged as not held, and an unreadable same-ticker BUY date reads "unknown", never "not acted". NVDA's 11-06 `enter_now` has `already_held = false` (owner SQL 2026-11-18), so the NVDA live case stands.
- **C2 Opus review: SHIP, 0 blocking** (4 non-blocking). Carried forward: C4 must split `apply_to_matched` output on `entry_call_status` (acted with `credit_deduped` False = acted, not_acted = passed, all else out), never on `acted_on` / `rec_type`, with a test for an `add_winner` plus an already-held `enter_now` on one ticker and day plus one same-day RECOMMENDATION BUY (the excluded row keeps the old rule's credit); C3 / C4 pass only calls dated on or before the cut-off; the `already_held` id lookup raises on a `pd.NA` id (nullable Int64 dtype; guard with `pd.isna` when C3 / C4 touch it).
- **C2 COMMITTED 2026-11-18 (`50d3731`).** **U15 DDL APPLIED by the owner 2026-11-18** (`docs/sql/weekly-debriefs-acted-rule-version.sql`; verify: `acted_rule_version` smallint nullable; RLS true, policy `service_role_all` cmd `*` for {service_role}), so C3 may send the column.
- **C3 SPEC 2026-11-18** (`docs/plans/acted-rule-c3-spec.md`, Opus planner PROCEED WITH CHANGES; owner accepted UD-1 to UD-5: carried-over names count in the share that resolves them, entry signals only, 80% stays prompt text, no auto-revisit of an emailed not-acted call, strip AI entry bullets). Split C3a (new pure `debrief_entry_calls.py`, gate 98) → C3b (wiring + docs) → C3c (weekly view offline read).
- **C3a Opus SHIP 2026-11-18** (two FIX-FIRST rounds: carry-over by name; a carried not-judged name gets a neutral tail). **C3b BUILT 2026-11-18** (debrief, cron, app wiring, older-rule caption, weekly view strict read (C3c folded in), golden regen; F-152b; Opus SHIP 0 blocking, then post-SHIP fixes confirmed SHIP: a module crash turns the cron lane red, the prompt shows plain reason words, the double-fault note). Live check: the Sun 11-22 debrief email is the first version-2 one: Python-written entry bullets, "Carried over from last week" for week-ending-11-15 calls that were still open, no AI-written entry bullet, no literal `**`; the stored 11-15 and older rows show the older-rule caption in 📄 Reports.
- **C3a + C3b PUSHED 2026-11-18** (`78791be` + `6c992ff`, with C2 `50d3731`).
- **C4 SPEC 2026-11-18** (`docs/plans/acted-rule-c4-spec.md`, Opus planner at `6c992ff`, PROCEED WITH CHANGES: new pure gate module `monthly_entry_calls.py` instead of `n_open` in the shared `signal_flow`; every `("new_pick",)` default stays, the monthly passes `ENTRY_CALL_REC_TYPES`). Owner took UD-1 to UD-5 as recommended (engine figures at the call-day price, the owner's at the fill; still-open calls listed, counted nowhere). Split C4a (pure) → C4b + C4c (wiring + docs). Must land before the Sun 2026-12-06 monthly email. **C4a PUSHED 2026-11-18 (`a09856a`); C4b + C4c BUILT 2026-11-18** (status in the C4 spec). NEXT after C4: C5 (unmatched-trade + sold-against-reading bullets, weekly), then C6 Phase B (ActedRuleSiblings).

Bug-register rows: MonthlyRollingRow, MRVLActed, DebriefEnterNow (`docs/bug-register.md`).

## 1. What the investigation found (lead + planner, at HEAD `46630b8`)

**MRVL, from the owner's SQL (2026-11-17)**
- Calls (`new_pick`, confirmed): 10-06 (67.4), 10-07 (72.8), 11-06 (81.5).
- Trades:
  - #424 BUY 2, MANUAL, 10-13
  - #419 BUY 3, DIVERSIFY_ADD, 10-14
  - #495 BUY 2, DIVERSIFY_ADD, 10-15
  - #435 SELL 7, RECOMMENDATION, 10-20
  - #454 BUY 8, MANUAL, 10-30
  - #464 SELL 8, MANUAL, 11-02
  - #479 BUY 5, RECOMMENDATION, 11-06
  - #480 BUY 1, MANUAL, 11-06
  - #492 SELL 6, 11-13
  - #494 BUY 5, 11-13
- **Monthly:** October's frozen "not acted" is correct under the same-day rule. The 11-10 "acted" came from the rolling 10-11 → 11-08 window, which holds only the 11-06 call, and #479 matched it. So the monthly flip is MonthlyRollingRow.
- **Weekly 11-02 → 11-08:** the code marks MRVL acted in both runs. #464, a SELL before the call, is enough on its own. The Sunday email's "did not act" contradicts the package, so the AI most likely wrote it. It can't be confirmed: the regenerate overwrote the row (probe P1).

**Five different "acted" rules exist**
1. **Weekly debrief** (`debrief_advisor.py` ~566-569): any trade, BUY or SELL, any trigger, with an ET date ≥ `week_start` and **no upper bound**. The callers pass the full journal.
2. **Debrief behavioural block** (~679-690): the ticker was ever bought.
3. **Monthly / Recs History** (`recommendations_history.match_recs_to_trades` ~242): the same **UTC** date and `trigger_type == RECOMMENDATION`.
4. **rec_events** (`rec_events_readout.match_attribution` ~164, owner-ratified 2026-09-15): the right direction, an ET date within `REC_OUTCOME_ACTION_WINDOW_TRADING_DAYS` (10) trading days, trigger as confirmation only.
5. **My Edge self-track** (`self_track_record.classify_buys`): a BUY within `SELF_TRACK_MATCH_LOOKBACK_DAYS` (3) calendar days; `enter_now` with `already_held` is excluded.

**Other findings**
- **Monthly window gap.** The cron sets `period_end = most_recent_sunday(first Sunday)` and `period_start = end − 28 days` (`cron_runner.py` ~3140). First Sundays are 4 or 5 weeks apart, so 11-02 → 11-07 is in **no** monthly report: the 12-06 report covers 11-08 → 12-06. That gap holds the MRVL and NVDA 11-06 calls. Months 4 weeks apart overlap by a day.
- **The Generate button and the cron's `force` path** both anchor to `most_recent_sunday(today)`. `tests/test_cron_debrief_monthly_date_anchor.py` pins this as intended; it must be rewritten on purpose.
- **Regenerate overwrites the emailed version.** `save_weekly_debrief` / `save_monthly_report` upsert on the period column.
- **The monthly breaks the offline contract.** The cron uses the lenient `db.load_trades()`, and the app button and live fallback use `st.session_state.get("trades_df", pd.DataFrame())`. A failed journal read reads every name as "missed". The live fallback also uses naive `date.today()`.
- **The monthly counts `new_pick` only** (`build_report_package` default `rec_types=("new_pick",)`). `predictive_analytics.ACTIONABLE_REC_TYPES` lists `new_pick`, `add_winner`, `enter_now`.
- **`match_recs_to_trades` has about 20 callers.** `compute_outcomes` measures an acted row from the fill price, so widening it globally would feed the owner's timing into engine alpha and break personalized_discovery's `rec_date == buy_date` join. **So don't change it**; add a new module.
- **Sibling two-type filters:**
  - `debrief_advisor` :564 and :679: omissions, to change.
  - app.py ~7696 (Grow Today steady note): deliberate, keep.
  - app.py ~32115 / ~38142 / ~50813: omissions, Phase B.
  - app.py ~50953 and `personalized_discovery` ~75: analysis inputs, keep same-day, log a follow-up.

## 2. Owner decisions (planner's recommended defaults)

- **U1. Surfaces.** Phase A before 12-06: weekly debrief + monthly report + its live fallback. Phase B after: Recs History, the My Edge fingerprint, Engine Trust by Band. Never change `match_recs_to_trades`. Until Phase B, the monthly shows a visible note that Recs History still counts same-day tagged buys.
- **U2. Direction.** Only a BUY acts on an entry call.
- **U3. trigger_type.** Confirmation only, never required.
- **U4. Lag window.** New constant `ENTRY_CALL_ACTION_WINDOW_TRADING_DAYS`: the call day plus N NYSE sessions. Recommend N = 2. Options: 0 / 1 / 2 / 3 / 5.
  - At N ≤ 3, October's MRVL stays not acted. At N ≥ 4, #424 on 10-13 flips it.
  - At N ≥ 1, NVDA (Fri call, Mon buy) is acted.
- **U5. Date.** The ET date of the re-anchored `traded_at` (from `db.load_trades_or_none`), compared with `rec_date`.
- **U6. As-of cut-off.** Each report judges trades only up to its own period end. A call whose window runs past that reads "still open (decision window ends D)", never "missed".
- **U7. Straddling call.** It belongs to the week it was surfaced and reads "still open" there. Next week's debrief resolves it under "Carried over from last week". Still-open calls are left out of the ≥80% follower share.
- **U8. Types.** Weekly: `new_pick` + `add_winner` + `enter_now`. Monthly: the same, one shared `ENTRY_CALL_REC_TYPES`. Exclude `enter_now` rows with `already_held`. The quoted action rates move.
- **U9. Deterministic bullets.** Python writes the entry-signal bullets (acted / not acted / still open); the AI writes the rest and is told not to restate them. Regenerate the golden test.
- **U10. Self-track.** "Bought without a matching call" uses the shared rule. Sells reuse `self_track_record.classify_sells`. Log SelfTrackWindow (whether My Edge's 3-calendar-day window should move to the shared rule) as a follow-up.
- **U11. Sold against the app's reading.** A SELL classified `self_initiated`, plus the latest `score_history` composite on or before the sell date. When that is ≥ `COMPOSITE_BUY`, the bullet adds "while the app read …". If it's unreadable, the reading is left out and disclosed.
- **U12. Monthly window.** Calendar months (the 1st to the last day of the previous month), generated on the first Sunday; 12-06 = November 1–30. Options: contiguous first-Sunday windows, or keep 28 days and document the gap.
- **U13. Generate target.** The last completed period by default; an archived month selected rebuilds that row's own stored window.
- **U14. Emailed reports.** An emailed row gives an inline preview only, plus an explicit "Replace the emailed version" button (never for the viewer). Applies to weekly and monthly. Option: a history table (DDL).
- **U15. Versioning.** `ACTED_RULE_VERSION = 2` in the new module.
  - Monthly: in `viz_json`, no DDL.
  - Weekly: a nullable `weekly_debriefs.acted_rule_version` (DDL the owner applies before C3 deploys).
  - Don't bump `METRICS_VERSION`. Never rewrite stored rows.

**Probes (read-only)**
- P1: the 11-08 email's MRVL bullet.
- P2: `select * from exit_signals where ticker='MRVL' and signal_date between '2026-11-02' and '2026-11-08'`.
- P3: `select id, created_at from trades where id in (419,424,495)`.

## 3. Chunks (each: implementer → Opus reviewer)

- **C0.** Owner answers U1–U15, runs P1–P3, and applies the U15 DDL if chosen.
- **C1. Periods + regenerate guard + offline trades.** Needs U12–U14 only; ships first.
  - New pure `stock_analyzer/report_periods.py`: `monthly_period`, `monthly_period_for_row`, `regenerate_action`, `monthly_archive_options`.
  - `cron_runner._run_monthly_report`: also covers `force`; use `db.load_trades_or_none()`.
  - The app.py monthly branch and live fallback: load trades or refuse; `today_et`; preview / replace.
  - Weekly Generate: the same guard.
  - Tests: new `tests/test_report_periods.py`; rewrite the monthly part of `tests/test_cron_debrief_monthly_date_anchor.py`.
- **C2. The shared rule.**
  - New pure `stock_analyzer/entry_call_action.py`: `ENTRY_CALL_REC_TYPES`, `ACTED_RULE_VERSION`, `classify_call`, `classify_calls`, `per_name`, `apply_to_matched`. `trades_df is None` returns `unknown`.
  - Make the trading-day helper public (from `predictive_analytics._advance_trading_days`, behaviour unchanged).
  - The constant goes in `constants.py`, plus a row in the architecture.md constants table.
  - Test: `tests/test_entry_call_action.py`.
- **C3. Weekly debrief wiring** (gate file): bounded window, C2 statuses with `as_of = week_ending`, `carried_over`, the behavioural block on C2, Python-written bullets, an older-rule caption, golden regeneration.
- **C4. Monthly wiring:**
  - `apply_to_matched` before `compute_outcomes`.
  - `n_open` in `signal_flow` / `report_viz_snapshot`.
  - The prompt sentence plus the divergence note.
  - Email + app captions.
  - Golden regeneration.
  - Due by ~12-01.
- **C5. Unmatched-trade + sold-against-reading bullets** (weekly; may slip past 12-06).
- **C6. Phase B:** apply the shared rule to the action-rate siblings; then drop the U1 note.
- **C7. Docs:** F-3 / F-4, the constants table, the User Guide; close the three rows; add MonthlyWindowGap, MonthlyTradesOffline, SelfTrackWindow, ActedRuleSiblings.

## 4. Risks

- Two acted figures are on screen until Phase B; the visible note is required.
- At N = 2, engine alpha on acted rows picks up up to two sessions of the owner's timing. It's small; disclose it.
- Per-surfacing action rates rise (headline counts are distinct-by-ticker, so they're unaffected).
- Unreadable trades must give unknown / a refused report, never "all missed".
- Pushing C3 before the U15 DDL fails the Sunday save.
- No session_state caches are touched.
- C1 removes baselined antipattern sites; re-key the baseline, never `--init`.

## 5. Tests the build must include

- Window edges at N (from the constant): Friday → Monday, a holiday (Wed 11-25 → Mon 11-30), across DST (11-01), a legacy weekend `rec_date`.
- A 20:30 ET fill; an imported midnight-UTC row.
- A SELL or an earlier BUY never credits.
- `as_of` cut-off and still-open; identical results on a regenerate.
- `trades_df is None` gives unknown.
- `already_held` is excluded.
- Live cases: MRVL October not acted at N = 2; MRVL week of 11-08 acted via #479, not #464; NVDA still open in the 11-08 debrief and carried over as acted in the 11-15 one.
- Periods over 24 months with no gap and no overlap.
- Regenerate guard; the archive label and content come from the same row.
- Both golden tests regenerated.
