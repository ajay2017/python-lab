# Held-position trailing stop (finding #26)

**Status:** C1 SHIPPED 2026-10-30 (`ea8719a`, `stock_analyzer/held_stop.py`, unwired, in `_GATE_FILES`). NEXT: C2 dry-run script → owner runs it → owner rules U1–U7 → C3a + C3b wiring (with finding #24) in one push after a close.

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

## Owner decisions pending (after the C2 dry run)

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
