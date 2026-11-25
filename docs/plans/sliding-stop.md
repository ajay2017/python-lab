# SlidingStop: a carried stop for positions the journal can't date

**Status:** DESIGNED 2026-11-25 (Opus planner `claude-opus-5-5`, PROCEED WITH CHANGES). **Owner took every §9 default 2026-11-25** (U1 carry written on full / partial / prior_session, applied on partial / prior_session only; U2 unreadable journal → carry only from the previous trading session; U3 allow a drop with a note when a fixed journal gives a lower full stop; U4 Home error + red premarket line when uncarried, amber when carried; U5 out of Act Today; U6 unadjusted 1-year frame first). Nothing built; next = the owner's C0 probe (P1 Home list, P2 logs, P3 `docs/sql/readonly-sliding-stop-probe.sql`). **P3 Q1 RESULT (owner, 2026-11-25):** all 10 holdings (AAPL, ABBV, AMZN, ANT, CRWD, REGN, SNOW, SPCX, TGT, TMUS) have journal net = holdings shares, gap 0, no `no_history`, no SPLIT rows → no journal-mismatch exposure today; ANT's young-listing window (from the 12-10 close) is the only current case. Per §2: the carry still ships before ≈ 12-10; C4 escalation is lower priority (nothing to escalate today). **Q2 / Q3 RESULT (owner, 2026-11-25):** 7 of the 10 holdings were on full basis at the 11-24 EOD; **ABBV** last on full 11-17, **REGN** never, ANT never (young listing, expected). Q3 (rows by last-written date: 11-24 7, 11-20 1, 11-19 1, 11-18 2, 11-17 2, 11-16 2) counts per (ticker, episode) last write, not per-EOD writes (PK = ticker + episode_start); the older rows are sold positions plus ABBV. **Explained, no defect (owner, same day):** ABBV and REGN were bought on 11-25, so the 11-24 EOD couldn't write them; ABBV's 11-17 row (episode 9-17, stop 275.24 trailing) belongs to the earlier, since-sold ABBV position and can't apply to the new episode (key = ticker + episode_start). Home showed no "Some stops are not on a full price history" box (no partial / prior-close stop in the app). **P1–P3 conclusion: no holding is on a `prior_session` stop today; ANT from the 12-10 close is the only exposure.** Build order stays C1 → C2 → C2b dry run → C3 before ≈ 12-10; C4 escalation after. Tier 0 item 2 of the owner's 2026-11-25 pick order. Bug-register row SlidingStop (corrected from this pass). Lead checked the two headline claims against HEAD before saving: the young-listing second trigger (held_stop.py ~421-429) and the premarket email never firing on a `prior_session` stop (no live merge, headless_alert_engine.py ~195-197, the stop is built from the same last close).

**Planner's required changes:** run the C0 probe first; the C2b dry run is a hard gate before the wiring push; the register row and F-315 must be corrected (a `prior_session` stop never reaches the premarket email).

## 1. Mechanism (checked against HEAD)

**Producer (one source for every reader):** `held_stop_wiring._one` (held_stop_wiring.py:449) replays the journal with `episode_from_journal`; any status other than ok goes to the branch at :475-486 (6-month bundle first, then `fetch_history(t,"1y")`); `compute_held_stop` then returns `prior_session` (held_stop.py:335-342) = last completed close − max(2.0, 3.0) × ATR.

**Second trigger the register row missed:** an "ok" episode with no computable component also returns `prior_session` (held_stop.py:421-429). The young-listing case: when the frame doesn't reach `HELD_STOP_WARMUP_BARS` (60) bars before the buy, the entry anchor is dropped (`pe < wb`, :371) and trailing needs `pos >= wb` (:396), so from 15 to about 60 bars there is no trailing component and no ratchet unless the high-water gain is ≥ 10%. **ANT gets this sliding "Prior-close ATR" stop from the 12-10 close until about mid-February 2027** (F-320's plan expects "its own stop" on 12-10). Its manual $75 stop still wins whenever it is ≥ the auto stop (`compose_with_manual`, :458-475).

`apply_floor` drops out for `prior_session` (held_stop_floor.py:301-303: status must be ok and basis full / partial); `rows_to_write` writes full basis only (:389); `lane_failure` ignores `prior_session` (:440-454).

**Readers:** `build_portfolio_df` → "Stop" / "Gap to Stop (%)" (portfolio.py:622-626, 693-710); the Brief's stop_breach → Act Today, Signals, Analysis, `brief_breached_set` (wiring :596), and the premarket email (headless_alert_engine.py:329-343). All read that one number.

**Corrections to the register row:**
- **Premarket email: a `prior_session` stop can never fire.** The lane runs at about 08:00 ET (`ALERT_EMAIL_HOUR_ET` = 8) with no live price merge (engine :195-197); the bundle's price is the last close (bundle_loader.py:207), C(D−1); the stop is C(D−1) − 3·ATR, so the gap is always 3·ATR / price > 0. The `POSITION_AT_RISK_GAP_PCT` (3%) band is never reached at premarket either.
- **EOD and intraday lanes:** no held-stop breach email (intraday does entries only, cron_runner.py:2539).
- **App (Home / Brief):** a breach shows only during session D and that evening, when the live price ≤ C(D−1) − 3·ATR; at midnight ET `as_of` rolls, the stop is rebuilt from C(D) and the breach disappears.

**What else catches a slow bleed:**
- **Deterioration ladder** (`exit_advisor.classify_deterioration_tier`, :182-238) still runs on a mismatched name. Its peak window comes from `_build_open_lots` (engine :178-182), so it is journal-dependent too (an unreadable journal uses the 63-bar fallback). TRIM (Brief only, 25%, silenced while settling): dd ≥ min(max(8, 2.5·ATR%), 14), 2 of 3 closes below SMA50, RS < 0. EXIT (Brief + email): deep dd ≥ min(max(12, 3.5·ATR%), 20) with a close below SMA50, or an active TRIM plus (underwater, or ≤ −$250, or deep). Against a full trailing stop (3·ATR% below the peak close), a name falling **with** the market gets its first emailed exit later:

  | ATR% | Trailing stop fires at | Ladder EXIT fires at | Extra drawdown |
  |---|---|---|---|
  | 2 | 6% | 12% | ≈ 6 pp |
  | 3 | 9% | 12% | ≈ 3 pp |
  | 5 | 15% | 17.5% | ≈ 2.5 pp |

  A name lagging the market gets a Brief TRIM at ≈ 8–12.5% and an EXIT once underwater or past −$250. The ratchet profit lock (Breakeven guard / Protect 10%) is lost entirely.
- **Manual stop:** a fixed floor whenever one is set (ANT has one).
- **Risk-off de-risk:** only when SPY < 200-day or VIX ≥ 25, top 3 names with β ≥ 1.2; incidental, not a backstop.

**Net:** a `prior_session` name has no emailed stop at all, and its in-app stop only catches a one-day crash of ≈ 3 ATR. The ladder bounds the damage at an extra ≈ 2.5–6 pp of drawdown plus the lost profit lock.

## 2. How often (owner, read-only, before any code)

- **P1 (now):** 🏠 Home → "⚠️ Some stops are not on a full price history": any row labelled **Prior-close ATR** is a SlidingStop name; its note names the cause (e.g. "journal nets N shares but holdings show M").
- **P2 (history):** Railway `cron-premarket` / `cron-eod` logs "engine note: held stop on a limited basis: X (Prior-close ATR)" (engine :218-219), one per name per run; premarket email footers since 11-02 (only on days an alert was sent).
- **P3 (new read-only SQL, `docs/sql/readonly-sliding-stop-probe.sql`, lead writes):** (a) per held ticker: journal BUY − SELL net vs `holdings.shares`, the difference, whether a SPLIT row exists (checked by hand), plus holdings with no trades (`no_history`); (b) held tickers whose newest `held_stop_floors.as_of` is older than the table's latest `as_of`, or with no row (not on full basis at the last EOD; the 11-16 / 11-17 runs wrote 12 / 11 rows, so one name was already off full at least once).
- **What changes the design:** chronic `no_history` on pre-journal positions → the fix is an opening-balance journal row, and C4 moves ahead of C3; zero mismatches with ANT the only case → the carry still ships before ≈ 12-10 (ANT's manual stop lowers urgency); several mismatched names → the escalation (C4) first.

## 3. Options and recommendation

- (a) Reuse the last good C3c floor whatever the basis: **rejected alone** (its fingerprint is journal-derived, exactly what is untrusted; it never covers names with no full day: ANT, `no_history`).
- (b) Sticky ratchet for `prior_session`: needed, but alone it drops a full stop on the day a mismatch begins.
- (c) Escalate the gap: needed (fixes the root cause), alone it leaves the gap open until the owner acts.
- Stateless window max: **rejected** (held_stop.py:54: "never a window max for an unknown episode" — pre-entry highs would fabricate a false "Sell all").
- **Recommended (d) = carry + (c):**
  - **One new table `held_stop_carry`**, primary key ticker. **Write:** every EOD (trading days only) writes the *effective auto* stop for every held name on full, partial or `prior_session` basis, including full, so yesterday's full / ratchet stop carries into a mismatch day ((a) done safely). **Apply:** only on **partial and `prior_session`** results, after the C3c floor (partial included so ANT doesn't drop at its 60-bar switch). **Never** on full (C3c and the C1 invariant govern), buy_day, unavailable, or the `_buy_day` rejected-fill result.
  - **Effective stop** = max(computed, row.stop, max over completed sessions s with row.ref_session < s < as_of of Close_s − max(mults) × ATR_s on today's frame). The window term spans only sessions the app saw the holding in the book, so a missed EOD loses nothing.
  - **Fingerprint (any mismatch lapses to today's behaviour):** holdings `avg_cost` to the cent (an add or re-entry changes it; a partial sale doesn't); the journal's (count, max id) of BUY + SPLIT rows for the ticker (any new, late-logged or deleted buy lapses it; SELLs don't); `held_stop_floor.policy_sig()`; a module `CARRY_SIG_VERSION`.
  - **Price-history check:** `verify_closes` on ref / bind sessions within `HELD_STOP_FLOOR_CLOSE_TOL_PCT` (2.0); an unrecorded split or a corrected bad tick lapses it.
  - **Sweep:** EOD deletes rows for tickers not in the holdings list (`write_guards.snapshot_sweep_keep` pattern); a None held set → no delete.
  - **Seed guard:** with no valid row, the writer never *seeds* a row whose stop is ≥ that session's close (closes the buy-day amplification below); advancing an existing valid row is always allowed (that advance is what catches a slow bleed).
  - **Failure directions:** carry rows unreadable → no carry (today's behaviour) + a degraded note; premarket red only if a partial / `prior_session` name exists (mirrors C3c U4). Carry-code exception → keep the computed stop, write nothing for that name, EOD red (like `FLOOR_CHECK_FAILED`). Journal unreadable at read time → U2. EOD with journal or carry unreadable → writes nothing (fail closed).
  - **Risks:** a same-session full exit + re-entry at an identical avg cost with an unreadable journal (residual, bounded by U2); a mismatched position bought today already gets a stop from the *pre-entry* close that can sit above the fill (pre-existing, new row **MismatchBuyDay**; the seed guard stops the carry persisting it); adjusted-bundle dividend drift on a months-old bind bar can exceed 2% for a high-yield name (lapses the carry: fails safe).
  - **(c) Escalation:** a structured episode status + share numbers ride on the result; the pure decision gives **error** when a fixable cause (mismatch / no_history / unreadable) leaves a name on an *uncarried* `prior_session` stop, **warning** when carried, nothing extra for the young-listing ok case (F-320 notices already). A Home ⚠️ Notices source "Stop basis: X …" pointing to "📒 Trade Journal → ⚠️ Fix drift between trades and holdings", plus a premarket email body line. Not Act Today (a data fault, not a trade).

## 4. Constants

No new decision threshold. `HELD_STOP_FLOOR_CLOSE_TOL_PCT` is reused (its architecture row must say it also governs the carry). `CARRY_SIG_VERSION` is module plumbing like `POLICY_SIG_VERSION`.

## 5. Edge cases

- Weekend / holiday visit (`as_of` Sat, or Thu 11-26): the reader takes Fri's (Wed's) row; the breach test is C(Fri) ≤ stop(Fri), same as Monday's premarket. The writer skips non-trading days (`split_non_trading_rows`).
- Pre-open / after hours read the D−1 row (cache per ET day); row D = stop(D) anyway. DST day: date-only logic.
- Late-logged BUY (either direction): new id → carry lapses (conservative), Notice turns error. Late-logged SELL: no lapse; a full exit removes the holding and the sweep deletes the row.
- Split: unrecorded → `verify_closes` lapses; recorded → the SPLIT row and new avg_cost lapse it. Full exit then re-entry: sweep + avg_cost + journal id. Two accounts: `holdings` has one row per ticker, nothing new.
- ANT: < 15 bars no stop and no carry; 15 to ≈ 60 bars the carry ratchets its `prior_session` stop; then partial with the carry and no drop; its manual stop still wins when higher.
- Manual stop: the carry feeds the auto stop only; `compose_with_manual` runs after; the writer never reads `port_df["Stop"]`.

## 6. Data and DB

- New DDL `docs/sql/held-stop-carry.sql` (owner applies before the wiring push). Columns: ticker PK, as_of, stop, stop_set_on, source_basis (full / partial / prior_session), binding, ratchet_label, avg_cost, journal_buy_count, journal_buy_max_id, ref_session / ref_close, bind_session / bind_close, policy_sig, carry_sig, source `cron:eod`, written_at. RLS = the service_role policy as in held-stop-floors.sql.
- A missing table reads as None (today's behaviour) and the EOD lane goes red with "apply …". `policy_sig` + `carry_sig` = the DoD #8 version marker.
- Writes via `db.save_held_stop_carry` / `delete_held_stop_carry` (no-ops for the read-only viewer); the app only reads. Register the table in `system_health._Store`.

## 7. Chunks

held_stop*.py, young_holding_stop.py, portfolio.py, db.py, headless_alert_engine.py and cron_runner.py are already gate files; home_notices.py is not.

1. **C0 (owner, read-only):** P1–P3; the lead writes the SQL.
2. **C1 pure core** [decision-bearing]: new gate module `stock_analyzer/held_stop_carry.py` (+ `_GATE_FILES` + review-policy line): `journal_buy_sig`, `apply_carry`, `rows_to_write` (with the seed guard), `sweep_tickers`, `lane_failure`, `notice_tier`; additive `journal_shares` / `holdings_shares` on `episode_from_journal`. Unwired.
3. **C2 DDL + db read / save / delete + tests** [DB-write]: reviewer.
4. **C2b dry run** (precedent C3c-2): `held_stop_dryrun.py --carry` replays the EOD writer over ≈ 20 sessions and lists every bind and every NEW breach. **Go only if no new breach is false.**
5. **C3 wiring** [decision-bearing; one push, two commits]: `build_held_stops(..., carry=)` (required) applies the carry after `floored()` in every branch of `_one`; the app's `_held_stops_for` reads the carry once per ET day before the fan-out (cache raises on None); `_build_context` publishes `held_stop_carry_readable`; new `cron_runner._eod_held_stop_carry`; the premarket rule; "Stop Carried" / "Stop Carried On" columns; notes and labels (incl. fixing `_note_for`'s "start isn't usable" wording for an ok episode).
6. **C4 escalation:** Notices source + tier, email body line. Reviewer.
7. **C5 docs (lead):** F-315 (carried-stop paragraph + the correction: the premarket email never fired on `prior_session`), F-320 (ANT after 12-10), architecture (DDL, known behaviours, the tolerance row), User Guide stop paragraph, bug register (SlidingStop; new rows MismatchBuyDay and LadderPeakJournal), review-policy line, shipped-log, plan Status, CLAUDE.md queue line.

## 8. Tests the build must include

- **Slow-bleed invariant:** a mismatched holding falling 0.5 ATR a session with EOD writes simulated day by day breaches at premarket exactly the morning after the first close ≤ the stop in force (equality counts, per `is_stop_breached` rounding), never a day earlier; without the carry it never breaches (pins the bug).
- **Never lowers:** effective stop ≥ computed on every path; a carry-code exception keeps the computed stop.
- **Lapses:** avg_cost by a cent; a new / late-dated / deleted BUY; a SPLIT row; the policy signature or `CARRY_SIG_VERSION`; a 50% close mismatch; a future-dated row; an incomplete row. SELL-only: no lapse.
- **Re-entry:** swept then re-bought at the identical avg cost → no carry; same-session round trip with a readable journal → lapse; with an unreadable journal → only a previous-session row applies.
- **Seed guard:** a mismatched buy-day stop ≥ the close is not seeded; a valid row advanced through a breach is written.
- **Time-context matrix:** Saturday visit and Thanksgiving 11-26 (no write, prior row used); the 2027-03-14 DST date; pre-open and after hours on D; an EOD missed on D−1 (window term recovers stop(D−1)).
- **Transitions:** 14 → 15 bars (no carry, then a seed); 59 → 60 bars (`prior_session` → partial, no drop); mismatch → fixed (full governs; any drop noted per U3).
- **Writer:** never writes buy_day / unavailable; skips non-trading days; nothing with an unreadable journal or unreadable carry rows; never uses manual values; sweeps only with a known held set.
- AST wiring tests for the app and headless reads and the EOD call (mirror `test_held_stop_floor_wiring.py`).

## 9. Owner decisions (defaults in brackets)

- **U1** Carry design as in §3: written on full / partial / `prior_session`, applied on partial / `prior_session` only. [yes]
- **U2** Journal unreadable at read time: apply the carry only if the row is from the previous trading session (alternatives: never, always). [previous session only]
- **U3** A fixed journal gives a full stop lower than the carried one: allow the drop with a note. [allow]
- **U4** Escalation: Home error tier + a red premarket heartbeat when a name sits on an uncarried `prior_session` stop for a fixable cause; amber / footer when carried. [yes]
- **U5** Keep the data fault out of Act Today. [yes, out]
- **U6** Build the `prior_session` stop from the unadjusted 1-year frame first, as buy-day does (one extra cached Yahoo fetch per name per day; consistent closes for the price-history check; moves the stop by cents, so reviewer). [yes]
