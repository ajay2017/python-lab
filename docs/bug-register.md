# Bug register

**Status:** started 2026-11-01. One row per known open bug, so open defects live in one place instead of scattered through the CLAUDE.md queue. Each `#N` / `U8` ID refers to the matching item in CLAUDE.md "What's queued", which keeps the full detail. When a bug ships, set its Status to the commit hash and date, add the shipped-log entry, and remove the row in the next tidy-up.

**Order agreed 2026-11-01:** N1 → U8 → #9 follow-ups → #32 → #21, then the rest by severity.

**Every row was checked against `git log` on 2026-11-01:** none had a fix commit since it was found.

## A. Wrong numbers or wrong advice

| ID | Bug | Where | Status |
|---|---|---|---|
| N1 | `account_daily_snapshots.net_equity = gross_book + cash_balance` mixes the broker's cash (balance sync, already includes every fill) with the journal holdings at EOD (a trade imported or logged a day late is missing). The owner's SQL on 2026-11-01 showed 10-30 net equity $2,510.33 (lev 6.08) against 10-23's $7,665.52, which is exactly the −67.25% in the Sunday 11-01 weekly debrief (saved and emailed). 10-28 has the same shape. Also feeds the monthly report, 📄 Reports, 💰 Capital Trend and the leverage history. | `stock_analyzer/account.py`, EOD cron, broker lane | In progress. C1 + C2 `e389d76`, C3 `61c9727` (Opus SHIP each). **Owner applied the backfill 2026-11-01:** 11 rows repaired (09-16 … 10-30, each reconciled to the cent; 10-30 = Home's live $19,702 / 2.84×), 5 refused (trade logged before EOD; rows likely correct), 10-23 off by $0.02 (left). **Weekly debrief 11-01 regenerated: −9.5% (was −67.25%), verified live; monthly report regenerated, trajectory verified.** **C4 built 2026-11-02** (EOD records `journal_max_trade_id` + `book_positions`; Opus SHIP). **10-28 cause found 2026-11-02:** three hand-logged 10-29 BUY rows merged fills from 10-28 and 10-29 at averaged prices (Robinhood's fills reconcile to the broker cash to the cent: 10-28 $3,876.02, 10-29 $2,610.26). Journal corrected 2026-11-02 by the owner with `docs/sql/journal-fix-2026-10-28-fills.sql` (Opus SHIP after 2 FIX-FIRST rounds), verified: 7 rows. Undo record (pre-fix): #444 GOOGL 5 @ 310.60, #445 MSFT 3 @ 526.00, #446 OKTA 7 @ 221.72, traded_at 2026-10-29 14:56:05 / 15:09:25 / 15:14:44 UTC (log times). Now #444 GOOGL 3 @ 323.00, #445 MSFT 2 @ 527.00, #446 OKTA 5 @ 224.406 (10-28), new #458 GOOGL 1 @ 300.99, #459 OKTA 2 @ 215.00 (10-28), #460 MSFT 1 @ 524.00, #461 GOOGL 1 @ 283.00 (10-29). **10-28 repaired 2026-11-02** (owner backfill `--apply`, `missing_ticker`: GOOGL 0→4, MSFT 0→2, OKTA 0→7; gross 15,242.86 → 19,163.06, net 3,605.55 → 7,525.75, lev 4.23x → 2.55x, residual 0.00; re-plan: 0 to repair). No weekly debrief uses 10-28 as an endpoint; October monthly report to regenerate. Remaining legacy rows: 5 refused (trades already in the EOD book) and 10-23 (−$0.02), both left as is; the per-stock breakdown lacks late-logged names until C7; C5-C8. |
| N2 | Home Today's P&L counted a late-logged buy's full value as a gain (+22.48% on Sun 11-01; CSCO / MRVL / MU bought Fri 10-30, logged Sat; then DASH / OKTA adds). | `daily_pnl.py`, app.py | **Fixed** `51dd19f` → `e41d87c` → `8771f4b` 2026-11-01; **verified live** ($0 on Sunday) |
| N3 | A SELL dated between the snapshot date and today (for example, logged with its log-time date) is not repaired by `fill_late_logged_baseline`; its orphan / qty_drift warning still shows (understated). | `daily_pnl.py` | Open (Opus review 2026-11-01); N1 C7 fixes the snapshot rows themselves |
| U7 | A SELL not yet in the journal makes the net-capital sizing cap and the daily new-buy budget LOOSER than they should be (journal book + broker cash, `margin.resolve_net_capital`); a late BUY makes them tighter (fail-safe). | sizing cap, F-306 budget | Open; planner + reviewer (N1 sibling sweep) |
| U8 | 📡 Signals Analyst Revisions alerts are red DANGER ("institutional conviction fading") at bare literals (`portfolio.py`, `get_alerts`), counted in the nav badge and Home's 🔴 count, contradicting #22's watch-only rating-change flag. | `portfolio.alerts` (gate file) | **Fixed 2026-11-01** (U8 commit; Opus FIX-FIRST → SHIP); screenshot pending: a rating-change name on 📡 Signals shows a 🟡 ⚑ flag, not red |
| #25 | 🔔 Playbook on a report day (0d) still reads pre-report for before-open reporters. | Catalyst Watch playbook (gate path) | Open, owner wording call |
| #21 | Grow Today shows two different "today's new buys" figures on one card (99.6% vs 84.3% of net capital). | F-306 budget | Open, inputs not yet traced |
| #34c | The broker-drift check dates the snapshot by `captured_at`, not SnapTrade's last sync, so a dropped connection serving cached holdings could read "no mismatch". | broker drift | Open; read-only SnapTrade probe first |
| #29 f/u | A lot's `buy_date` is the UTC date of `traded_at`, so a buy logged after 20:00 EDT anchors the drawdown peak one day late. | `tax_advisor.py` lots | Open |
| EW | In earnings week, Home 🔧 Tune-up (`risk_advisor.py`) and the Rebalancer still size the trim to 15%, while the Brief, Playbook and Signals use the earnings-week line. | `risk_advisor.py`, rebalancer | Open |
| EW2 | For a book of ≤ 7 names the earnings-week line is above `SINGLE_NAME_TRIM_TRIGGER`, so the Brief and Playbook say Watch while Signals trims the same name. | earnings-week band | Open, owner call |
| Fund | `fundamentals.resolve_fundamentals`: a live set clearing `FUNDAMENTALS_GATE_MIN_METRICS` wins over a fresh, fuller cache row and overwrites it. | fundamentals cache | Open, owner policy call |
| FracNew | A NEW position's holdings row stores `int(_pb_shares)` (app.py ~31026-31030) although the buy form accepts 0.001-share steps (~31829-31833), so a 0.5-share new buy is dropped by `save_holdings`' `shares > 0` filter: holdings (and from N1 C4 `book_positions` / `gross_book`) no longer match the journal; a later late trade on that ticker makes `plan_repairs` refuse the row (safe). Check whether a recalc / Rebuild resyncs holdings before choosing the fix. | app.py Trade Journal buy, `db.save_holdings` | Open (N1 C4 Opus review 2026-11-02) |
| Deb | The weekly debrief's `exit_signals_df` read failure only logs, so a saved debrief can silently miss its protective-signal section. | `cron_runner._run_debrief` | Open |

## B. Wording that disagrees with another screen

| ID | Bug | Status |
|---|---|---|
| #9 f/u | Scores still rounded across a band edge: the 📋 Watchlist row header `Score {:.0f}/100` can show 65 above a card saying 64.6; WAIT_CATALYST says "score 65/100 — needs to reach 65+"; `daily_briefing` "Composite score {:.0f}/100 in the Sell zone" can read 44 at 43.5–43.99; the Diversification redeploy headline prints "R:R 2.5:1" while its card reads 2.46. | Open |
| #20 f/u | The 📋 Watchlist banner and the 📈 Analysis hold banner read the current tone and don't apply Lock Setup, while Grow Today shows the locked tone. | Open (`entry_posture.py`, gate file) |
| #22 f/u | Rating-change overclaims remain in `macro_playbook.py` ("strongest alpha setup") and `watchlist_advisor.py`. | Open |
| Reb | "Trims would move" (`trim_pct`) counts trims sizing zero shares. An unsized trim hides "✅ Portfolio is well-balanced" on Signals. | Open |
| ST② | 🩺 System Trust ② "23/24 confirmed fresh" doesn't name the unconfirmed store, and only the unconditional stores are age-checked. | Open (`system_health.py`, gate file) |
| Zone | A price below `entry_lo` reads "within 3% of the entry zone" on ENTER_NOW / ENTRY_HELD (pre-existing, mostly unreachable). | Open |

## C. Time and weekend context

| ID | Bug | Status |
|---|---|---|
| #32 | 🩺 System Trust grades daily lanes on a flat 30 h, so the weekday-only intraday lane reads 🟡 late every weekend and Home adds "pipeline check degraded". Home Day Shock says "moved ≥5% today" on a Saturday. | **Fixed** `59ce1cf` 2026-11-01 (Opus SHIP); **verified live** Sun 11-01 (no degraded line, Day Shock "in the last session (Fri Oct 30)", Today's P&L "$0 / 0.00%") |
| Macro | After a release, a memoized Brief macro card can still say "Hold through the print". | Open |
| Month | 🧠 AI Insights → 📅 Debriefs, Monthly Portfolio Intelligence: the report for period 2026-10-04 → 2026-11-01 is listed as "Report month: November 2026" (seemingly named after its end date's month), and "generated 2026-11-02" is a UTC date (it was 11-01 ET). Seen live 2026-11-01. Display only. | Open |
| #30 | Yahoo `yf.download` returned empty for the indices on 2026-10-31. The page is guarded (`7bbbe11`); the cause is parked until it recurs (pull the web log BEFORE pushing). | Parked |

## D. Display only

| ID | Bug |
|---|---|
| Movers | 🧾 Summary's "Today / Last session's movers" (`summary_view.movers_window`) and Home's Day Shock (`session_label.price_move_session`) disagree 20:00–midnight ET on a trading day; route Summary through `session_label`. Grow Today's "🔥 +X% today" / "Breakout X% today" (app.py ~9971 / ~10917) have the same weekend wording. |
| Zero | A flat Today's P&L now shows "0.00%" with `delta_color="off"`, but Streamlit still draws a grey up-arrow; use `delta_arrow="off"` if the pinned Streamlit supports it (screenshot check). |
| #31 | Home ⚠️ Alerts "(~$523), MRVL (~" renders as strikethrough (Streamlit pairs the two `~`). |
| AIset | ⚙️ App Settings pending-switch line ("Pending: claude-haiku-5-5 applies from …") omits the effort. |
| Text | "1 shares"; "-0 downgrades" on Signals; "$+80" / "$-198" sign placement; "Grow Today (1 setups)"; Catalyst company names cut at ~28 chars; AAPL Analysis resistance levels listed below price; the Sentiment tile subtitle says "VADER" though LLM rescoring is live; Earn Growth (e.g. ABBV 290%) has no context; a Rebalancer news headline with two `$` can render as LaTeX. |

## E. Breaks on a dependency upgrade (held off by the 2026-11-01 freeze)

| ID | Bug |
|---|---|
| Up1 | `cron_runner.py:2916` `float(row["Close"])` on a one-element Series: pandas FutureWarning, seen in the 2026-11-01 thesis-lane log; use `.iloc[0]`. |
| Up2 | Streamlit `use_container_width` (removal announced after 2025-12-31) and pandas `Styler.applymap` (app.py ~36320). |

## Not a bug

- Home's red "Action Required" pill beside "✅ Nothing to act on today": the pill counts danger-level Active Alerts (and low diversification), Act Today counts same-day trade decisions (User Guide, "Reading Today's Brief"). U8 will remove the rating-change alerts from that count.
