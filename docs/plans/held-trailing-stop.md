# Held-position trailing stop (finding #26)

**Status:** **C3 BUILT 2026-10-30** (worktree branch `held-stop-c3`; commits (a)–(d) + #24 + #28; Opus FIX-FIRST then confirming round; owner decided the buy-day stop = the recorded fill − ATR_STOP_MULT × the last completed session's ATR, fixed for the day; an earlier min(prior-close stop, live sizing stop) was dropped because it moved with the live price and couldn't fire). **Owner 2026-10-30 evening: go live THIS weekend (2026-10-31/11-01) after the 10-30 EOD lane, without the buy-day fill check; `HELD_TRAIL_START` moved 2026-11-06 → 2026-10-30; no new buys until the fill check lands (Mon/Tue 11-02/03).** **After go-live (was: before the deploy) (reviewer non-blocking, final round 2026-10-30; one small reviewed chunk):** (1) buy-day fill sanity check: test the recorded fill against [min(today's partial Low, prior close), max(today's partial High, prior close)] ± `HELD_STOP_FILL_TOL_PCT` and fall back to `prior_session` with a note when it fails (a mis-keyed fill or a buy-day split could otherwise give a false same-day sell-all); (2) on buy day prefer the unadjusted `fetch_history` frame over the dividend-adjusted bundle so the stop doesn't move a few cents overnight; (3) wording: the buy-day note's "the trailing and profit-lock parts can raise it" (it can also fall if the next session's fill check drops the anchor), the User Guide fallback sentence (also missing ATR / too little history), and the `daily_briefing.py` add-lane comment should say `_position_size_for_render` (via `risk.sizing_unavailable_reason`) returns `stop_infeasible`, not `position_sizing`. C1 SHIPPED (`ea8719a`, fix `2a2e0e4`), C2 SHIPPED (`3e650d6`). Owner ruled U1–U7 and the C3 decisions D1–D5 on 2026-10-30. **C3 deploy: weekend 2026-11-07/08, after the EOD lane** (build + review during the week of 11-02; nothing pushed before). C3c (persisted floor) by 2026-12-07 or on the first data-caused degraded-basis banner. Owner to set manual / broker stops on exposed names (FSLR, GOOGL) meanwhile.

## The defect (confirmed 2026-10-30)

- `risk.atr_stop_loss` (risk.py:25-30) = `df["Close"].iloc[-1] − ATR_STOP_MULT × ATR`, rebuilt on every bundle load (`bundle_loader.py:367`). yfinance `history()` includes today's partial bar intraday (checked live 10:16 ET 2026-10-30), so in the app the ATR stop sits about 2×ATR below the live price all session: it follows the price down.
- The pre-market / EOD cron (`headless_alert_engine.py:172-176`) uses the bundle's last close as the price AND builds the stop from that same close, so a pure ATR stop can never breach in the protective email.
- `portfolio.protective_stop` picks the ratchet tier from the CURRENT gain, and every floor multiplier in `STOP_RATCHET_LEVELS` is below its own threshold, so a falling price drops the tier before it can reach the floor: no ratchet floor can be breached either. Only manual stops fire.
- `scripts/exit_ladder_replay.py:238-262` already documents the hazard and assumes the Brief fixes the stop from the prior close; the live code doesn't.
- Real case: GOOGL (avg $310.60) 10-28 close $324.89 → stop $309.56. 10-29 gap to $283.82 showed "Sell all 5 shares at next open". 10-30 the stop was rebuilt to $257.85; the breach was gone and the holdings table read ↑ Buy 69.

## Owner decision (2026-10-30)

Options 2 + 1: for HELD positions a stop that only moves up, built from completed sessions only. A breach latch (option 3) was not chosen. Entry stops (`r["stop"]`: Grow Today sizing, Watchlist / Analysis R:R, G-13 / G-32, `rr_pullback`) stay `price − ATR_STOP_MULT × ATR`.

## The rule (planner, Opus 5.5: PROCEED WITH CHANGES; implemented in C1)

For as-of session D (ET), using only bars dated before D:
1. Episode from the trade journal in ET dates: start E = the last BUY that took running shares from ~0 to >0; partial sells and adds don't reset; a full exit and re-buy starts a new episode. Statuses: ok / unreadable / no_history / mismatch.
2. Entry anchor = (first fill / journal split ratio) − initial multiple × ATR(before E). Dropped, with a reason, if there isn't `warmup_bars` of history before E, the fill is outside the entry session's range (unrecorded-split guard), or E isn't a trading session.
3. Trailing = max over sessions max(E, trail_start) ≤ s < D of (Close_s − trail multiple × ATR_s). ATR is seeded at a fixed bar (`warmup_bars` before E), so it doesn't move with the frame.
4. Sticky ratchet: the tier comes from the highest completed close since max(trail_start, the latest add) against avg cost; floor = avg cost × (1 + mult). (The window restarts at an add so a pre-add peak can't set a floor above the price.)
5. Stop = max of the three, rounded at the end; a manual stop wins only when tighter (`portfolio.manual_stop_wins`).
6. Fallbacks: episode not ok → `prior_session` = last close − max(initial, trail multiple) × ATR; short or stale history → `partial` (a lower bound, never tighter); too few bars → `unavailable` (no stop fabricated).

All policy values are explicit parameters in C1; no constant has changed.

## Owner decisions (2026-10-30, after the C2 dry run)

**Accepted the lead's recommendations:** U1 `HELD_TRAIL_ATR_MULT` = **3.0**; U2 variant **(c)** with `HELD_TRAIL_START` = the last completed session before go-live; U3 sticky ratchet yes; U4 no reset of the trailing part on an add; U5 breach on close / live price (not the day's low); U6 `prior_session` fallback when the buy date is unknown; U7 record the first breach per episode (own chunk C5). Technical values go in as proposed unless the owner objects: warm-up 60 bars, min bars 15, fill tolerance 2%, shares tolerance 1e-4.

**Dry-run evidence (2026-10-30, 12 holdings, 123 closed round trips: 68 losers / 55 winners, variant a):** 2.0× fired on 35/68 losers and 14/55 winners and pre-empted the ladder on all but 1 loser; 3.0× fired on 24/68 losers and 5/55 winners, with the ladder alone or first on 6 losers and the same session on 8; per fire, selling at the next open beat the actual exit by a median ~+1pp on losers and lost ~−5pp on winners; median fire one session before the actual exit; whipsaw (close back above the fired stop within 10 sessions) 59–86% at every multiple. Day 1 under (c): FSLR and GOOGL newly breached (both well below their entry-risk stop), OKTA / PLTR at risk, ABBV / WAL looser for one session (closed by the trail-start rule).

**Found by the dry run, fix before C3 (gate file, reviewed):** (1) `history_period_needed` must normalise a tz-aware first-bar date; (2) the split guard rejects real extended-hours fills (GOOGL 10-29 fill $310.60 vs a regular session of $280.36–$288.33): accept a fill within [min(Low, prev close), max(High, prev close)] ± tolerance.

### Original pending list (for the record)


- **U1 trail multiple:** 2.0 (= `ATR_STOP_MULT`) fires a sell-all before the deterioration ladder's TRIM on every name; planner prior ≥ 3.0 (about the ladder's EXIT distance). Decide from the replay.
- **U2 start:** recommended variant (c): entry anchor retroactive from each buy, trailing / ratchet from a fixed deploy date (`HELD_TRAIL_START`).
- **U3** sticky ratchet (recommended yes). **U4** no reset of the trailing part on an add (recommended). **U5** breach on close / live price, not the day's low (recommended). **U6** `prior_session` fallback when the buy date is unknown (recommended). **U7** record the first breach per episode for measurement (recommended, own chunk C5).
- **U8 (later):** a pre-market price merge so the morning email can catch overnight gaps; the email catches a close below the stop, not a gap.

## C3 requirements from the C1 review (must)

1. A separate long-history fetch per held name, sized by `history_period_needed`: on the 6-month bundle a position held longer than about 66 sessions goes `partial`, and its trailing stop then drops $6–15 as the window rolls (the GOOGL vanishing breach again).
2. Split-adjusted, dividend-UNadjusted closes (yfinance `auto_adjust=False`; verify on the installed version): dividend re-adjustment lowers past closes on every ex-date.
3. A visible banner whenever the basis is `partial` or `prior_session`; no "only moves up" claim on those.
4. Planner design for a persisted floor (max of the last full-basis stop per (ticker, episode start) and today's) so the invariant survives fetch failures and vendor re-adjustment. DB write: planner + reviewer.
5. Both held-stop decision sites switch together: `build_portfolio_df` and the Analysis manual-stop merge (app.py ~25226-25255); app and cron call the same entry point in one push. Pass `Shares Exact`, not the int `Shares`, to the episode check.
6. Ship finding #24 (Signals "within -X% of stop" on a breach) in the same push.

## Chunks

- C1 SHIPPED 2026-10-30: `held_stop.py` + `tests/test_held_stop.py`.
- C2: `scripts/held_stop_dryrun.py` (read-only): `--today` per holding (today's stop vs each variant × multiple ∈ {2.0, 2.5, 3.0, 3.5}, newly-breached flags, basis) and `--replay` over closed round trips (first fire vs the ladder's TRIM / EXIT and the actual exit, 5- / 10-session forward return, whipsaw). `NYSE_HOLIDAYS` starts 2026-01-01, so exclude pre-2026 post-holiday days from basis counts.
- C3a engine + cron wiring; C3b app wiring (one push); C4 wording / User Guide / requirements F-17 / F-18 + new F-ID; C4b `exit_ladder_replay.py` on `held_stop`; C5 breach measurement (if U7).


## C3 design (planner, Opus 5.5, 2026-10-30: PROCEED WITH CHANGES)

### Owner decisions on C3 (2026-10-30, all as recommended)
- **D1** Persisted floor deferred to its own chunk **C3c, by 2026-12-07** or on the first day a data-caused degraded-basis banner appears (XOM, the oldest holding, outgrows the 6-month frame around mid-December).
- **D2** `HELD_TRAIL_START` = the last trading session BEFORE the deploy date (for the 11-07/08 deploy: 2026-11-06). Re-run `scripts/held_stop_dryrun.py --today --live --trail-start 2026-11-06` on deploy day.
- **D3** Deploy outside market hours after the EOD lane, weekend 2026-11-07/08, so the owner sees the sell-all cards before Monday's protective email.
- **D4** One-time "stops recalculated" notice: Home `st.info` for `HELD_STOP_NOTICE_SESSIONS = 5` sessions from go-live, plus one line in the first protective email.
- **D5** The Brief's raise-stop card (`STOP_TIGHTEN_ATR_MULT` 1.5, opt-in via Mark Done) stays as is; revisit at the C5 readout.

### Four changes to the earlier plan
1. Persisted floor waits (D1). Interim guards: the bundle-frame fallback is `partial` with a banner, and the banner never claims "only moves up".
2. **A new breach rebuilds the Brief:** the Home memo (`_synth_sig`) gains `held_sig(port_df)`, and a memo hit is treated as a miss when `brief_breached_set(port_df) − breached_at_build` is non-empty (only NEW breaches, so the `STOP_RECOVERY_MARGIN_PCT` hysteresis is kept); same for the memoized `alert_list`. `_SYNTH_SCHEMA_VER` 20 → 21.
3. `build_portfolio_df(holdings, loaded_data, manual_stops=None, *, held_stops)`: keyword REQUIRED, no default. `held_stops is None` or a missing ticker → "Stop Unavailable" (never the old engine). The `protective_stop(price, …)` call is removed.
4. C4 wording ships in the same push (tooltips, explainer, User Guide), so no screen says "price − 2×ATR" beside a different number.

### Commit split (one push, after a close; hook commits with run_in_background)
- **(a)** Constants + unadjusted history fetch (not wired): `HELD_TRAIL_ATR_MULT = 3.0`, `HELD_TRAIL_START` (ISO string), `HELD_STOP_WARMUP_BARS = 60`, `HELD_STOP_MIN_BARS = 15`, `HELD_STOP_FILL_TOL_PCT = 2.0`, `HELD_STOP_SHARES_TOL = 1e-4`, `HELD_STOP_NOTICE_SESSIONS = 5` beside `ATR_STOP_MULT` (the initial multiple reuses it); architecture constants rows; the dry run imports them. `YFinanceProvider.price_history_unadjusted` (`auto_adjust=False`, `_retry`) and `data.fetch_unadjusted_history` (Yahoo only, no failover, never raises, None on failure).
- **(b)** New gate module `stock_analyzer/held_stop_wiring.py` (+ `_GATE_FILES` + review-policy line): `build_held_stops(holdings, held_data, trades_df, as_of, *, fetch_history)` (exact float shares; policy values read from constants inside; fallback chain long frame → bundle frame as `partial` with a note → C1 `prior_session` / `unavailable`; never raises). Labels: entry → "Entry Stop", trailing → "Trailing Stop", ratchet → its `STOP_RATCHET_LEVELS` label, prior_session → "Prior-close ATR", unavailable → "Stop Unavailable". Also `fetch_or_raise`, `analysis_manual_override`, `degraded`, `brief_breached_set`, `held_sig`; an additive `atr_last` in the C1 result; non-gate `stock_analyzer/held_stop_view.py` for the explainer (`what_if(h) ≥ current Auto Stop` by construction).
- **(c)** The switch (atomic):
  - `build_portfolio_df` new columns `Auto Stop`, `Stop Basis`, `Stop Since`, `Stop Note`, `Stop Entry`, `Stop Trailing`, `Stop Ratchet`, `Stop ATR`, `Stop HW Close` (scalars only; `loaded_data[t]["stop"]` / `["atr"]` never mutated).
  - **#24** in `portfolio.alerts` (the function is `alerts`, not `get_alerts`; stop bands ~701-715): a first branch for gap ≤ 0 via new `stop_breach_text.alert_breach_msg` ("⛔ **WDAY** is 2.5% below its stop $200.25 — breached; Today's Brief says sell at the next open"; one `$` per message). `stop_breach_text.stop_noun` for app.py ~26423-26430; `daily_briefing` ~3378 uses `_label` (no "Trailing Stop stop").
  - Cron: `_build_context` reads `db.load_trades_or_none()` once (strict for held stops, an empty frame for the old consumers) and passes `held_stops=build_held_stops(...)`. Journal unreadable → the lane fails like `price_outage`; per-name degradation → a log line + an email footer; the rule-change line in the first email.
  - App: a `_held_stops_for` wrapper using `_budget_strict_trades()` + `_today_et()`; `_cached_unadjusted_history` (`st.cache_data` ttl 1 day keyed by ET date; failures raise so they aren't cached; a 5-minute negative cache; parallel fan-out). All three `build_portfolio_df` calls (app ~2836, ~5526, headless ~176) switch. The Analysis merge (~25214-25255) → `analysis_manual_override`. One degraded-basis `st.warning` after ~5550 naming each ticker + note; an Analysis caption under Stop Loss when the basis isn't full; `_render_stop_ladder` driven by `held_stop_view`; the Exit Plan caption ~26645 for held names; drill-down help ~21006 → `_tip("Protective Stop")`; the D4 notice. U8 stays a separate push.
- **(d)** Docs: `_tip` ~937 / ~978, User Guide, requirements F-17 / F-18 + new **F-315** (held-position stop), architecture stop section + forward_sim wording (~3075), queue, shipped-log, memory.
- **Review:** one Opus pass over (a)–(c) as a combined diff before the push; a confirming round after any FIX-FIRST. Design = planner, Build = implementer on each `feat(`.

### Risks / follow-ups
- Day 1: FSLR + GOOGL sell-all cards (Brief, Signals danger, Monday email); OKTA / PLTR at-risk; FSLR / OKTA / PLTR lose add room. Trailing from D−1 can add a breach the 10-30 dry run never showed (it used trail_start = as_of).
- Never deploy intraday. No new session key (rides `_port_df_enriched`); if a `_held_stops_cache` is ever published, register it in `coord_freshness.py`.
- Manual-stops read: `db.load_manual_stops_or_none` + the `MANUAL_STOPS_UNREADABLE_BANNER` (Home, post-trade refresh, every page via `_render_portfolio_stale_banner`) and a premarket lane failure SHIPPED 2026-10-30 (pre-C3 protection). **C3 must rewrite `MANUAL_STOPS_UNREADABLE_BANNER`** (its "the automatic stop moves down with the price, so it rarely triggers on its own" becomes false once the held stop ships) and update its pinned test string.
- Follow-ups from that review: while the manual-stops read is failing, the Brief raise-stop card can suggest a level below the user's real manual stop and Mark Done → Save / Revert to ATR would overwrite or clear it (disable Save / Revert when `_manual_stops` is None; DB-write path, reviewed); optionally note the unreadable read inside the protective email itself (`render_alert_email`).
- A name whose anchor the guard drops stays `partial` permanently: word the banner calmly and watch for banner fatigue.
- Premarket compares close(D−1) with stop(D) ≡ stop(D−1) for breach purposes (pin with a property test); overnight gaps remain U8 (a pre-market price merge).

### Tests the build must include
- No network in tests: monkeypatch `fetch_unadjusted_history`, plus a conftest guard that fails on a real yfinance call.
- AST parity pinning both ends of all three `build_portfolio_df(` calls (`held_stops=` from `_held_stops_for` / `build_held_stops`; strict trades; `_today_et()`); no `protective_stop(` / `stop_ladder(` left in app.py; `_synth_sig` contains `held_sig(` and the hit test uses `brief_breached_set(`.
- Goldens: `loaded_data` stop / atr untouched; Grow Today add sizing unchanged; `bundle_loader` stop == `risk.atr_stop_loss`.
- Monotone over 40 sessions on a full basis; rolling-frame fallback → partial with no "only moves up"; a partial bar dated as_of never moves stop(as_of); a session can't breach its own trailing value; premarket equivalence property; weekend / holiday as_of; trail-start boundary (as_of = HELD_TRAIL_START → no trailing / ratchet; the next session → exactly one).
- Real cases: GOOGL (extended-hours fill accepted, $283.82 breached, "Sell all 5 shares at next open") and FSLR (episode 10-12, add 10-16).
- Fetch None / raise / stale → bundle partial; bundle missing → prior_session / unavailable; `held_stops=None` → all "Stop Unavailable"; journal None → prior_session "trade journal could not be read"; a cron with every name prior_session fails the lane; fractional shares ok.
- Manual parity between `analysis_manual_override` and `build_portfolio_df`; labels (no "Stop stop"); #24 messages (−2.5, gap 0.0 at/above, +0.1 unchanged, one `$`); explainer what-if; memo breach set; a tripwire on the `yfinance==1.3.0` pin.
