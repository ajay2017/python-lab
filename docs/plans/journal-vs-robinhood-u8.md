# TJDate U8 — Journal vs Robinhood (Account list + Home line)

**Status:** DESIGNED 2026-11-12 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES, read at HEAD `53a1efc`). Owner answered 2026-11-12: **UE-1, UE-2, UE-4 – UE-9 all as recommended; UE-3 = (b)**, one reviewed hand-SQL batch applied by Mon 11-16 (three SELL prices SPCX #451 / TSLA #420 / ORCL #412, MRVL #419's date, plus linking SPCX #484 / CSCO #478), same route as `99fbefd`. This reverses the owner's earlier 11-12 "no hand SQL, wait for U8" choice. The batch is `docs/sql/journal-fix-2026-11-12-sell-prices-links.sql` (Opus FIX-FIRST: no Rebuild before-picture, #484 / #478 price check tighter than the matcher's LINK rule → SHIP, 0 blocking), APPLIED by the owner 2026-11-12 evening with the app Rebuild (#412 −120.09, #420 +15.28, #451 −127.90). MRVL #419 stays FORWARD on purpose (see `journal-fix-2026-11-11-mrvl-fslr.sql`). U8a BUILT 2026-11-12 (Build = implementer Sonnet 5.5; pure `stock_analyzer/journal_match_view.py`, `_GATE_FILES` 88, plus the matcher's additive `differs_on` field; Opus FIX-FIRST → SHIP; nothing on screen yet). Opus round 1 blockers, all fixed: a linked row on the wrong ticker or action read as a date difference with "share counts agree" (matcher `holdings_effect` 0.0 on equal shares → new `differs_on`); "share counts agree" printed where agreement wasn't known; Account read "clean" with all-pending findings. U8b BUILT 2026-11-13 (F-244e; Build = implementer Sonnet 5.5; pure `decide_account_view` + `sync_status_parts` in `journal_match_view.py`, additive `twin_txn_id` on U9 rows, app.py `_journal_match_inputs` shared by Pending Imports and the new list, Broker Sync banner in ET with a separate ⚠️ line for an unfinished transaction import, plus the owner's 11-13 wording fixes (User Guide, weekend tick hint, `REASON_NO_SESSION`); Opus SHIP, 0 blocking, then a confirming round SHIP on the follow-ups). Live checks pending. U8c + U8d BUILT 2026-11-13 (Build = implementer Sonnet 5.5; the 🏠 Home line `_alert_ph_journalmatch` + `_journal_match_cache`, memoized on `memo_key`, judged with the same cached sync stamp the key holds, a failure never memoized; Opus FIX-FIRST (the matcher read a fresh txn stamp while the key / line used the cached one, and a None result was memoized) → SHIP; docs F-244e Home paragraph, §4.0.2 row, coordination-caches note, CLAUDE.md cache table, User Guide). Owner (2026-11-13): commit U8c with U8b but push it only after the Account list is seen live (keeps UE-9). **COMPLETE 2026-11-13:** U8b `d71bc66`, U8c + U8d `7a821d7` and the headline fill-count fix `44e3a7d` pushed the same evening (owner: the Account list was seen live first, so UE-9 held) and all verified live by the owner's screenshots. Parent plan: [trade-entry-guard.md](trade-entry-guard.md); matcher spec: [journal-broker-match-c5.md](journal-broker-match-c5.md); U9: [pending-imports-verdicts-u9.md](pending-imports-verdicts-u9.md) (UD-5 hands the non-pending fills to U8).

## Planner's changes to the brief

1. **U8 can't fix the four real "differs" rows, and three of them leave the comparison before U8 can ship.** The matcher drops a finding once its newest date is older than today − `JOURNAL_MATCH_LOOKBACK_DAYS` (35; `journal_broker_match._in_window`, `_Ctx.start`). Last day shown: ORCL #412 Mon 11-16, TSLA #420 Wed 11-18, MRVL #419 (fill session 10-15) Thu 11-19, SPCX #451 Fri 12-04. Phase 2 (C6/C7) isn't designed and uses the same 35-day repair cutoff (`ACCOUNT_SNAPSHOT_REPAIR_LOOKBACK_DAYS` = 35). The app has no edit-trade path (db.py: `delete_trade`, `update_trade_realized_pnl`, `backfill_trade_broker_txn_id` only). Lead verified the window and the db.py functions 2026-11-12. See UE-3.
2. `coord_freshness.py` is not a `_GATE_FILES` member. The new key is read only by Home, the page that writes it, so it goes in `_SELF_CONSUMED_BY_PRODUCER` (`tests/test_coord_freshness.py`), as `_broker_drift_cache` does, not in `PORTFOLIO_DEPENDENT_KEYS`.
3. Count findings, not "journal rows" (an UNLOGGED finding has no journal row).
4. Calm posture: price- or date-only differences are caption level, not "Needs a look".
5. Ship the Account list first (U8b) and the Home line in a later deploy (U8c).

## Owner decisions (planner's recommendation in brackets)

- **UE-1** Home count = severity "differs" minus REDATE and SPLIT (both consistent with an order placed one day and filled on the journal's date; listed on Account under "Dates differ; the journal may be right"). FORWARD and MERGED_FILLS counted. AMBIGUOUS, TOO_MANY, NOT_YET_COMPARED, UNCHECKED and info kinds not counted. [yes]
- **UE-2** Warning tier ("Needs a look") only when a counted finding changes share counts (`holdings_effect` non-zero by `_tec._same_shares`: UNLOGGED, JOURNAL_ONLY, QTY_MISMATCH, LINKED_DIFFERS on shares); price/date-only → caption. [yes; today's data gives one caption]
- **UE-3** The four real rows before they age out: (a) accept them leaving unfixed; (b) one reviewed hand-SQL batch like `99fbefd`, applied by 11-16 (SPCX #451 = two fills on one row, needs a row split); (c) wait for C6/C7 (can't arrive in time). [(b), or (a) for the three price rows if the realized-P&L error is judged immaterial; reverses the owner's 11-12 "no hand SQL" choice.] Either way the U8 list shows each row's "leaves the comparison on" date.
- **UE-4** One action in U8: "Log This Trade →" for an UNLOGGED fill that is dismissed (or logged but in no journal row), through the existing `_tj_broker_prefill` path (`db.mark_snaptrade_pending_import_logged` flips a row by id whatever its status; `trade_confirm` / D0 stay as guards). Always behind the tick "I've checked: this fill isn't in my journal (I marked it Already logged earlier)". [yes]
- **UE-5** No other one-click in U8 (LINK: `backfill_trade_broker_txn_id` overwrites with no "is null" guard and no audit row; PRICE_FIX needs price + realized P&L + link written atomically = C7). [none]
- **UE-6** Read-only viewer sees the Home line and the list; Log shows "🔒 view-only". [yes]
- **UE-7** AMBIGUOUS / TOO_MANY not on Home; listed on Account. [no]
- **UE-8** When `last_txn_sync_at` < `last_full_sync_at`, say fills after the earlier sync aren't compared (a clean run stamps both from the same `now_et`, cron_runner.py ~3843 / ~3853). [yes]
- **UE-9** Order: U8b Account list first, U8c Home line in a later deploy. [yes]

No new constant: the window text reuses `JOURNAL_MATCH_LOOKBACK_DAYS`; share tests reuse `BROKER_DRIFT_SHARE_TOL` through `_tec._same_shares`.

## Chunks

### U8a — new pure module (implementer → Opus reviewer)
Files: new `stock_analyzer/journal_match_view.py` + `tests/test_journal_match_view.py`; `.claude/hooks/pre_tool_checks.py` (`_GATE_FILES` 87 → 88); `docs/review-policy.md` reason line; CLAUDE.md count.
- Imports `journal_broker_match` as `_jbm` (KIND_*, `SEV_DIFFERS`, `match_coverage`, `_sync_et`, `_sync_when`), `trade_entry_checks` as `_tec` (`_same_shares`, `_fmt_date`, `_aware_et`, `_txn_text`), `JOURNAL_MATCH_LOOKBACK_DAYS`. No streamlit / db / data import, no clock read. Verify each name at HEAD; stop if one is missing.
- `HOME_COUNTED_KINDS` frozenset (the UE-1 answer, test-pinned).
- `changes_holdings(f) -> bool`: `holdings_effect is not None and not _tec._same_shares(holdings_effect, 0.0)`.
- `journal_signature(trades_df) -> str | None`: order-independent hash over (id, ticker, action, shares to 9 dp, price to 6 dp, `traded_at`, `broker_txn_id`); None for a non-DataFrame.
- `memo_key(today_et, journal_sig, txn_synced_at, full_synced_at, nonce) -> tuple` (the matcher reads `now_et` only for `.date()`).
- `home_line(findings, drift_verdict, txn_synced_at, full_synced_at, broker_linked) -> dict | None` → `{tier: "warning"|"caption", text, counted, deferred_tickers}`:
  - broker not linked → None;
  - `findings is None` → caption "⚠️ Journal vs Robinhood not checked: your journal or Robinhood's fills couldn't be read this run, so a missing or wrong journal row isn't flagged.";
  - dedupe by dimension: a counted share-changing finding on a ticker in `drift_verdict["diff"]` `rh_only` / `app_only` / `qty_mismatch`, only when `drift_verdict["state"] == "drift"`, is not counted; its ticker goes into "{TK}: shown in the broker-drift notice above; its journal row is listed on 💰 Account". Price/date findings are never deferred. `awaiting_sync` / `stale_clean` / `unknown` / None defer nothing. Only deferred left → caption cross-reference;
  - warning: "⚠️ Journal vs Robinhood: {n} difference(s) that change share counts ({e.g. 1 Robinhood sell of TSLA not in your journal}){; plus m in price or date} → 💰 Account → Broker Sync. Holdings, weights and sizes are computed from your journal.";
  - caption: "ℹ️ Journal vs Robinhood: {n} difference(s) in price or date ({k} price, {d} date); share counts agree → 💰 Account → Broker Sync.";
  - coverage unknown, nothing counted → caption with `match_coverage(None)["text"]`; UE-8 sentence; clean → None (Home never prints an all-clear).
- `account_list(findings, txn_synced_at, now_et, pending_visible, offline_parts) -> dict` → `{state: offline|clean|rows, offline_text, summary_text, window_text, coverage_text, in_pending_count, groups}`:
  - group order: Differs (counted) / Dates differ, journal may be right (REDATE, SPLIT) / Couldn't match automatically / Not compared yet (NOT_YET_COMPARED, UNCHECKED) / Matches, nothing to fix (LINK, RECONCILED, SKIPPED_NON_EQUITY);
  - item: `headline`, matcher `text`, `fix_text`, `status_note`, `leaves_window_on` (newest date + LOOKBACK + 1 day), `journal_ids`, `txn_ids`, `log_*` fields shaped like `pending_import_verdict` rows (UE-4, UNLOGGED with no pending fill only);
  - a finding whose fills are all pending is left out and counted into `in_pending_count` ("K more are in 📥 Pending Imports above") only when `pending_visible`; otherwise list everything;
  - `fix_text` never says the next sync will fix it, never says delete and re-log; PRICE_FIX / FORWARD / MERGED_FILLS / QTY_MISMATCH / JOURNAL_ONLY say a correction that also repairs past account figures isn't built yet; REDATE / SPLIT / FORWARD say "check the Filled date in Robinhood";
  - `window_text`: "Compared: journal rows and Robinhood fills dated {start} or later."; clean: "No differences found." + coverage text, never "matches Robinhood".
- `sync_status_text(full_synced_at, txn_synced_at) -> str` (ET via `_sync_et` / `_sync_when`): "Last sync run: {when} ET." then "Transactions imported in that run." / "Its transaction import didn't finish; fills are compared through the {txn when} sync." / "No completed transaction import on record (or it couldn't be read), so journal rows aren't compared with Robinhood's fills."; full stamp None → "Waiting for the first `broker` cron sync."; "Last sync run" uses the later stamp.
- All text plain: no `$`, no `**`, no line starting with `#`.

### U8b — 💰 Account wiring, one deploy (implementer → reviewer + screenshots)
- `_journal_match_inputs()`: strict reads, each wrapped, None on failure (`db.load_trades_or_none`, `db.load_snaptrade_fills_or_none(journal_broker_match.fills_read_since(now).isoformat())`, `db.load_snaptrade_txn_synced_at_or_none`), read once per Account render.
- Refactor `_pending_imports_decision(pending)` (app.py ~1890) to take those inputs, so Pending Imports and the new list judge from the same reads.
- New "#### 🧾 Journal vs Robinhood" after the Pending Imports coverage line (~41687), before 💵 Cash Activity; calls `match_journal_to_broker` directly (no memo); `st.warning` / `st.caption` only, last two groups in `st.expander`s; no `unsafe_allow_html`; headlines escaped as U9 (strip `*`, escape `$`).
- UE-4 button: tick → existing prefill (`snaptrade_txn_id`, `pending_id`) → `_pending_page` 📒 Trade Journal.
- U9 twin overlap (U8a Opus review, 2026-11-12): U9's `_apply_twin_rule` (`pending_import_verdict.py` ~416-444) flips a pending twin to "Not in your journal" and offers Log because a dismissed UNLOGGED twin exists; U8 offers Log on that same dismissed fill, so one missing trade gets two Log buttons. U8b suppresses U8's Log (or shows a cross-reference to the Pending Imports row) for an UNLOGGED fill that U9 used as a twin.
- Broker Sync banner (~41469-41474): `[:16]` + " UTC" → `sync_status_text(...)`.
- Bump `st.session_state["_journal_match_nonce"]` after a successful "Already logged" dismiss (~41679).
- `db.dismiss_snaptrade_pending_import` docstring (~6862-6868, "until U8 lists such fills"): update (db.py gate file, comment only, same reviewed commit).
- User Guide paragraph near the Pending Imports text (~45442 / ~45480).

### U8c — 🏠 Home line, a later deploy (implementer → reviewer + screenshots)
Files: `app.py`, `stock_analyzer/home_notices.py` + `tests/test_home_notices.py`, `scripts/check_antipatterns.py` + `tests/test_coord_freshness.py`.
- `_alert_ph_journalmatch = st.empty()` right after `_alert_ph_drift` (~5019); `"journalmatch"` after `"drift"` in `SOURCE_ORDER`, label "Journal vs Robinhood" (16 → 17 placeholders in the tests).
- Fill site right after `st.session_state["_broker_drift_cache"] = _drift_verdict` (~6108): skip when `snaptrade_client.has_snaptrade()` is False; stamps through one `@st.cache_data(ttl=120)` read; `memo_key(_today_et(), journal_signature(st.session_state.trades_df), txn, full, nonce)`; memo hit (`_journal_match_memo = {"key", "checked_at"}`) reuses `_journal_match_cache`; miss → `_journal_match_inputs()` + matcher; any None / exception → publish `_journal_match_cache = None` and drop the memo (never cache a failure); `home_line(get_or_offline(st.session_state, "_journal_match_cache"), _drift_verdict, …)`; render inside `with _alert_ph_journalmatch.container():` only when non-None; `_home_notice("journalmatch", tier)` inside that branch.
- `_journal_match_cache` → `_SENTINEL_KEYS` and `_SELF_CONSUMED_BY_PRODUCER` ("Home only; memo key holds the journal signature + sync stamps"). Not in `_home_synth_cache`'s signature, not in `_alert_list_cache`: outside the Brief, Act Today and alert synthesis.

### U8d — docs (lead; policy values)
`docs/requirements.md` new F-244e "Journal vs Robinhood (TJDate U8)" (counted kinds, tiers, dedupe, read-only scope; F-244d UD-5 note); CLAUDE.md cache table (Home row); `docs/coordination-caches.md`; `docs/architecture.md` §4.0.2; Status lines of trade-entry-guard.md, journal-broker-match-c5.md, pending-imports-verdicts-u9.md and this file; bug-register TJDate row; queue item 3; shipped-log; a memory for the UE answers and the age-out finding.

## Risks / coordination
- Overlap with the broker-drift banner on share counts only; deferred as above (precedent: F-250 `qty_drift` caption app.py ~8041-8056).
- Overlap with Pending Imports on the same page: the all-pending exclusion avoids showing a fill twice. The matcher runs twice per Account render (U9 inside `decide_pending_imports`, plus U8); accepted, same inputs → same result, avoids editing U9's gate file.
- Never in Act Today, never a gate. If U7 later reads the cache's UNLOGGED SELL `holdings_effect`, the key moves to `PORTFOLIO_DEPENDENT_KEYS` (gate tier, with a surface).
- Memo staleness: a trade logged on another device doesn't change this session's `trades_df` (app-wide; named, not fixed). Cron link changes and fill writes reach `last_full_sync_at`, so the memo key catches them.
- Wording: LINK rows (SPCX #484, CSCO #478) may or may not auto-link at the next sync (Tier-2 uses the placement date and 2-dp prices, broker_sync.py ~529); never promise it.
- Silent age-out: disclosed by `window_text` and `leaves_window_on`; a history of unresolved findings would need a table (out of scope).
- Sibling UTC display: only app.py ~41472 (grepped by the planner).

## Tests the build must include
- **Count rule:** every `_jbm._SEVERITY` kind classified explicitly (a new kind fails); REDATE / SPLIT never counted (if UE-1); FORWARD / MERGED_FILLS / PRICE_FIX counted; unresolved, unchecked, info never; the 2026-11-12 13-finding mix → counted 4, caption, "3 price, 1 date".
- **Tier:** one UNLOGGED SELL → warning; QTY_MISMATCH → warning; PRICE_FIX only → caption; `holdings_effect` 0.0005 (inside `BROKER_DRIFT_SHARE_TOL`) → caption.
- **Dedupe:** drift "drift" + TSLA in `qty_mismatch` + UNLOGGED TSLA SELL → not counted, clause names TSLA; same with `awaiting_sync` / `stale_clean` / `unknown` / None → counted; PRICE_FIX on a drift ticker → counted; only deferred → caption cross-reference.
- **Three states:** None → "not checked" caption; [] → None; populated → line; broker not linked → None in all three; Account offline names the failed part; clean carries coverage; no text contains "matches Robinhood"; `pending_visible` False → all-pending findings listed, `in_pending_count` 0.
- **UE-8:** equal stamps → no lag sentence; txn < full → sentence; txn None → unknown wording; txn > full → "last run" uses the later.
- **Time-context matrix:** Saturday 11-14 and Thanksgiving 11-26 visits; 07:00 vs 20:30 ET same date → identical `memo_key` and output; 23:30 ET 11-12 (04:30Z 11-13) vs 00:30 ET 11-13 → different `memo_key`; DST: 10-31 13:05Z and 11-02 14:05Z both "9:05 AM ET"; six-day-old sync → UNLOGGED still counted, no clean claim; a trade logged a day late, BUY and SELL → REDATE, listed under "Dates differ", not counted; back-dated BUY and SELL → FORWARD, counted; naive `now_et` → ValueError.
- **Window:** `leaves_window_on` for a 10-12 finding = 11-17; newest date = today − 35 listed, today − 36 gone; `window_text` names the start.
- **Memo:** `journal_signature` order-independent, changes on a `broker_txn_id` backfill, a price edit, a deleted row; None for a non-DataFrame; `memo_key` changes on each component.
- **UE-4:** Log enabled only for UNLOGGED with no pending fill, always needs the tick; `pending_import_verdict.log_allowed(row, False)` False.
- **Strings / purity:** no `$`, `**`, leading `#`; every text non-empty; AST purity pin; reuse pins (`jmv._jbm._sync_et is jbm._sync_et`, `_tec._same_shares`).
- **Wiring (AST / text on app.py):** `_journal_match_cache` written only at the Home fill site, read nowhere else; no other `stock_analyzer/` module names it; `act_today_view`, `daily_briefing` and synthesis never import `journal_match_view`; the `journalmatch` `_home_notice` call sits inside a conditional; `test_coord_freshness` classification passes.
- **Property (matcher's seeded generator):** `home_line["counted"]` + deferred = counted findings; Account items + `in_pending_count` cover every finding exactly once.
