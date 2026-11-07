# Net capital checked against Robinhood's own positions (bug register U7, #21)

**Status:** DESIGNED 2026-11-07 (Opus planner, `claude-opus-5-5`: **PROCEED WITH CHANGES**). Nothing built. **Waiting on the owner's answers to U1–U10 below** before C1. #21 is separate and small (C5, can ship first).

> Status convention: newer status lines are prepended above this one as work lands.

## The defect

**U7 (fails open).** `margin.resolve_net_capital` (stock_analyzer/margin.py:88-139) is journal gross book + `account_cash.cash_balance`. That is only true when both describe the same moment. A sell filled before a broker sync but not yet in the journal stays in the gross book while its proceeds are already in cash, so net capital is overstated and the F-255 sizing cap (`NET_CAPITAL_POSITION_CAP_PCT`) and the F-306 daily budget (`DAILY_NEW_POSITION_BUDGET_PCT`) allow more than they should. A late BUY makes them tighter (fail-safe).

**Freshness today:**
- Broker cash: `account_cash.updated_at` is the cron save time (db.py:5897-5912), not SnapTrade's own refresh time (#34c).
- Broker lane: "15 16,21 * * *" every day incl. weekends = 11:15 / 16:15 ET (EST), 12:15 / 17:15 ET (EDT) (system_health.py ~263-284).
- Positions: `broker_position_snapshot.captured_at`, saved by the same run (step ⓪) just before cash (step ①) (cron_runner.py ~3626-3714). The snapshot is skipped when any account read fails while cash still saves, so the pair can split (cash newer than positions).
- Journal holdings: live on every trade log.

**Where it fails open:**
1. U7: a fill before a sync, not logged by that sync.
2. The N1-LogTime class in the budget lag: a fill before a sync logged after it with the log time as `traded_at`; `daily_budget.trades_after` (daily_budget.py:114-135) counts the SELL twice. The 09:45 morning email uses the 16:15 cash read against the owner's 19:xx hand-logs. (A BUY logged that way double-subtracts: too tight, fail-safe.)
3. F-255 has no lag correction: after a logged BUY the gross book includes it but cash doesn't carry the debit until the next sync, so net is overstated by the buy's cost (accepted limit, docs/plans/daily-new-position-budget.md:148).
4. #34c: cached SnapTrade cash + a logged BUY → no lag applies → overstated.

**Consumers of `resolve_net_capital`** (decision paths): app.py:2893 (`_publish_new_position_budget`), app.py:6124 (Home F-255 → `build_daily_briefing` 6449/6873; budget postpass 6488/6902), app.py:25497 (Analysis F-255 25683-25698, 27183-27395; `_budget_downstream_plan` 25706), app.py:29511 (Watchlist), app.py:31214 (Trade Journal add-concentration net-capital leg), headless_alert_engine.py:671 (morning picks F-255 720; `budget_inputs` 811-817 → `run_daily_budget` cron_runner.py:2096/2488), headless_alert_engine.py:1335 (Watchlist email), `budget_basis` at budget_view.py:178/701/863 and headless_alert_engine.py:1482. **Awareness siblings:** app.py:31077, 5857 (`_leverage_cache`), 18067, 19761, 18620, 39681/39905, 48792; cron_runner.py:923 (N1 EOD row). **Same class, other gates:** concentration gates divide by journal equity (`portfolio.gate_basis`, portfolio.py:2499) — an unlogged sell loosens them too (already disclosed by the drift banner, broker_sync.py:829). Home memo `_synth_sig` (app.py:6293-6312) lacks the cash / snapshot stamps.

**Not `_broker_drift_cache`:** `split_awaiting_sync` (broker_sync.py:877) drops tickers traded since capture, so a name with an unlogged pre-sync sell and a logged post-sync buy vanishes from the diff. The reconciliation diffs the raw snapshot positions.

**Identity:** broker frame = Σ broker shares × live price + broker cash (one sync) = journal gross + cash − adjust, where adjust = Σ (journal shares − broker shares) × price over priced tickers (signed): exact when the books agree; needs no lag correction (a fill after the sync moves shares and cash equally).

## #21 (traced; not U7's root)

Both lines read one cache and one basis (app.py:9398-9406, called at 9804 and 10387):
- "New positions today: 99.6%" — `budget_view.status_line_text` (:274) → `hold_reasons.budget_status_line` (:104); pct_used = (logged + allocated + list) / basis (daily_budget.py:312).
- "Today's new buys are 84.3%" — `budget_view.over_line_text` (:288) → `hold_reasons.budget_over_line` (:140); `over_line_pct` = logged / basis (budget_view.py:242-248).
When the over line shows, left = 0 (daily_budget.py:269), so the 15.3-point gap is emailed allocations not logged as buys that day (reserved all day by design). Same pair in the email (notify.py:93-98, headless ~1576-1584). Plausible extra on 10-29 (unconfirmed): the TJDate 10-28 fills logged with a 10-29 date inflated "logged". Fix: wording (U9).

## Design

New pure module `stock_analyzer/capital_basis.py` (`_GATE_FILES` + review-policy reason; tests/test_margin.py `_GATE_MODULES`; must NOT import `margin` — callers pass `resolve_net_capital`'s output; may reuse `broker_sync.diff_position_map`):
- `broker_frame(snapshot, cash_rec, holdings_df, price_map, now, *, pair_max_minutes)` → `{state, adjust, rows, unpriced, captured_at, age_hours}`; states "ok" (|captured_at − cash updated_at| ≤ `BROKER_FRAME_PAIR_MAX_MINUTES`, tz-aware), "no_snapshot" / "unreadable" / "no_cash" / "no_holdings", "unpaired" (cash newer, or a manual cash entry), "implausible" (empty snapshot while the journal holds positions; never "called"). Unpriced broker-only tickers (an unlogged BUY) are left out of `adjust` (safe direction) and listed. Any non-"ok" state → `adjust` None, never 0.0.
- `reconcile_net_capital(net, state, frame)`: levered/called → net_eff = net − max(0, adjust), "called" if ≤ 0; unlevered / stale / unknown unchanged. Returns `{net, state, source: journal|broker, gap, checked}`.
- `reconcile_budget_basis(basis_result, gross_book, cash_balance, frame)`: basis_eff = **min**(lag-corrected journal basis, gross + cash − adjust) (not "lagged basis − adjust", which double-subtracts a post-sync logged buy); "called" if ≤ 0.
- `disclosure_text(rec, note_pct)`: when the frame binds by ≥ `NET_CAPITAL_BROKER_GAP_NOTE_PCT` of net capital, a suffix for the existing budget status line ("net capital from Robinhood's positions; your journal shows ~X more (see the broker check on Home)"); when unpaired / unchecked, a suffix saying sizes weren't cross-checked and why. No new banner; Home's drift banner gains "Sizing uses Robinhood's lower figure until this is fixed."

**Tightening-only by construction** (never above today's value). Fixes U7, the log-time SELL double count, F-255's missing lag after a logged BUY, and #34c's cached-cash case when positions and cash share a refresh. A late BUY stays fail-safe tight.

**Wiring:** app.py render helper `_effective_net_capital(gross_book)` (cash + a module-level `st.cache_data(ttl=120)` snapshot loader shared with Home's drift check + `holdings_df` + enriched price map) replacing the six app sites; `budget_inputs` carries the frame; `budget_view` calls `reconcile_budget_basis` at its three `budget_basis` sites; cron `compute_morning_picks` loads the snapshot BEFORE the F-255 resolution at headless :671 (today ~760), plus :1335 and :1482. No new session_state key (nothing new for `coord_freshness`); `_new_position_budget_cache` gains `basis_source`, `broker_gap`. `_synth_sig` joins (cash `updated_at`, `captured_at`) + `_SYNTH_SCHEMA_VER` bump. Bump `BUDGET_SIZING_VERSION` / `SIZING_FORMULA_VERSION` 5 → 6 (`budget_allocations.basis_dollars` changes meaning; Definition of Done #8). TJDate's day+1 `unlogged_sells` isn't needed for sizing (the snapshot is the same-day path).

## Owner decisions (planner's recommendation first)

- **U1. Rule:** the LOWER of journal and broker figures (recommended; never loosens; absorbs broker-side errors that read high — auxiliary-account positions without their cash, a split refresh, a withdrawal after the sync) vs the broker figure whenever paired. Cost: a late BUY under-sizes until logged (today's accepted behaviour).
- **U2. Unchecked broker** (unpaired / no snapshot / implausible): keep today's journal figure with a visible suffix (recommended) vs withhold every buy size (churn the owner would learn to ignore).
- **U3. New constant `BROKER_FRAME_PAIR_MAX_MINUTES` = 10** (inclusive). One cron run writes positions then cash within ~2 minutes worst case; syncs are ≥ 5 h apart.
- **U4. New constant `NET_CAPITAL_BROKER_GAP_NOTE_PCT` = 1.0** — only the threshold for SHOWING the note; the lower figure always applies (below 1% the budget room moves < 0.4% and the F-255 cap < 0.25% of net capital).
- **U5. Awareness siblings:** a later optional chunk (C6) for `_leverage_cache` (5857), 18067, 19761, Account held-over-cap list (39905); name-only for My Edge MWR (48792), regime cash (18620), Account Total Account Value (39681), the N1 EOD row.
- **U6. Versioning:** bump the sizing versions 5 → 6 (recommended) + a cron log line "net capital source=journal|broker gap=X", vs a `basis_source` column on `budget_allocations` (needs DDL).
- **U7. Shadow first:** ship the module + a read-only readout ~5 trading days (Account-page caption "Net capital: journal X · Robinhood positions Y" + the cron log line) before wiring (recommended) — measures how often / how much it binds and catches a standing drift such as a symbol alias (BRK.B vs BRK-B) before it binds permanently.
- **U8. F-255 change:** between logging a buy and the next sync, F-255 net capital drops by that buy's cost (truthful, tightening; closes the accepted limit at daily-new-position-budget.md:148). Recommend accept.
- **U9. #21 wording:** rename the over line to "Logged buys alone are 84.3% of net capital — above the 40% daily budget; nothing new sized today." (both lines kept; shared by app and email). Alternatives: drop the over line where the status line shows, or merge into one line.
- **U10. A buy made today but not logged** still isn't counted as used today (accepted at daily-new-position-budget.md:146): the frame lowers the basis but doesn't count the buy as used (room ~0.6 × B too loose). Recommend keep accepted, name it as bug U7b, reopen once TJDate C5 can date the fills; charging the broker-only excess as "used" would permanently cut the budget for any deliberately untracked broker holding.

## Constants (owner approval needed)

| Constant | Proposed | Rationale |
|---|---|---|
| `BROKER_FRAME_PAIR_MAX_MINUTES` | 10 | U3 |
| `NET_CAPITAL_BROKER_GAP_NOTE_PCT` | 1.0 | U4 |

No existing threshold changes (`DAILY_NEW_POSITION_BUDGET_PCT` 40.0, `NET_CAPITAL_POSITION_CAP_PCT` 25.0, `ACCOUNT_CASH_STALE_DAYS` 7, `SNAPTRADE_BALANCE_STALE_HOURS` 25 untouched).

## Chunks

- **C0 (owner, read-only):** note today's Home drift banner state (a standing drift or symbol alias would bind once wired). The #34c probe adds confidence but doesn't block.
- **C1 (reviewer):** `capital_basis.py`, the 2 constants + architecture rows, `_GATE_FILES` + review-policy reason, tests/test_capital_basis.py, tests/test_margin.py `_GATE_MODULES`. Build: implementer.
- **C2 (shadow, U7):** Account-page caption only (app.py; not wired into a decision → review can be skipped). Live check: both figures show.
- **C3 (reviewer):** app.py helper + shared snapshot loader + six sites + memo signature/schema bump + status-line suffix; budget_view.py (three `budget_basis` sites, cache fields); daily_budget.py / daily_briefing.py (version 6); hold_reasons.py (suffix). Live checks: Grow Today / Watchlist / Analysis status lines on a clean day unchanged; the drift banner clause; Trade Journal buy preview; no literal `**`, no `$` as LaTeX.
- **C4 (reviewer):** headless_alert_engine.py (snapshot before :671, plus :1335, :1482), cron_runner.py log line, notify.py email suffix. Live check: the next 09:45 email and the cron log "net capital source=".
- **C5 (#21, can ship first):** `hold_reasons.budget_over_line` + tests (non-gate wording file: review can be skipped, state why, or fold into C3). Live check: the over-budget Grow Today card and email.
- **C6 (optional, U5):** awareness sites, app.py only; screenshot each.
- **C7 (docs, lead):** requirements F-306 / F-255, User Guide, coordination-caches (new cache fields), bug register (U7, #21 closed; U7b, the concentration-denominator sibling, awareness siblings), shipped-log, this plan's and daily-new-position-budget.md's Status / accepted limits.

## Tests the build must include

Books agree incl. fractional → byte-identical to today (source "journal"); tightening-only property over a grid; U7 full and partial unlogged sell → truth; sell logged after capture → no change; buy logged after capture → F-255 tightens by its cost, budget not double-subtracted; log-time SELL → no double count; log-time BUY → journal figure (fail-safe); unlogged unpriced BUY → no change, listed; netting sell vs priced buy (buy > sell → no change); symbol alias binds with a note; pairing at exactly 10 min (paired) and 10 min + 1 s (unpaired), both directions; snapshot / positions / cash / holdings None → adjust None, unchanged; empty snapshot + journal holdings → "implausible", never "called"; stale cash, unlevered, called; frame drives ≤ 0 → "called". Time-context: DST fall-back 2026-11-01 and spring-forward 2027-03-14 (−04:00/−05:00 vs Z); a Saturday sync pair with a Friday unlogged sell; Monday 09:45 with the Sunday 16:15 pair; holiday 2026-11-26; a 16:30 sell after the 16:15 sync binds only after the next sync. Cron–app parity; AST pins both ends (every site via the helper; `_synth_sig` carries both stamps); cache fields; suffix exactly at 1.0%; version pin == 6; `capital_basis` doesn't import `margin`; #21: with allocations > 0 the two lines can't both read as "today's new buys".
