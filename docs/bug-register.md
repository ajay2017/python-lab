# Bug register

**Status:** started 2026-11-01. One row per known open bug, so open defects live in one place instead of scattered through the CLAUDE.md queue. Each `#N` / `U8` ID refers to the matching item in CLAUDE.md "What's queued", which keeps the full detail. When a bug ships, set its Status to the commit hash and date, add the shipped-log entry, and remove the row in the next tidy-up.

**Order agreed 2026-11-01:** N1 → U8 → #9 follow-ups → #32 → #21, then the rest by severity.

**Every row was checked against `git log` on 2026-11-01:** none had a fix commit since it was found.

## A. Wrong numbers or wrong advice

| ID | Bug | Where | Status |
|---|---|---|---|
| N1 | `account_daily_snapshots.net_equity = gross_book + cash_balance` mixes the broker's cash (balance sync, already includes every fill) with the journal holdings at EOD (a trade imported or logged a day late is missing). The owner's SQL on 2026-11-01 showed 10-30 net equity $2,510.33 (lev 6.08) against 10-23's $7,665.52, which is exactly the −67.25% in the Sunday 11-01 weekly debrief (saved and emailed). 10-28 has the same shape. Also feeds the monthly report, 📄 Reports, 💰 Capital Trend and the leverage history. | `stock_analyzer/account.py`, EOD cron, broker lane | Planner designing (2026-11-01); then repair the rows and regenerate the debrief and monthly report |
| N2 | Home Today's P&L counted a late-logged buy's full value as a gain (+22.48% on Sun 11-01; CSCO / MRVL / MU bought Fri 10-30, logged Sat; then DASH / OKTA adds). | `daily_pnl.py`, app.py | **Fixed** `51dd19f` → `e41d87c` → `8771f4b` 2026-11-01; **verified live** ($0 on Sunday) |
| N3 | A SELL dated between the snapshot date and today (for example, logged with its log-time date) is not repaired by `fill_late_logged_baseline`; its orphan / qty_drift warning still shows (understated). | `daily_pnl.py` | Open (Opus review 2026-11-01); N1 C7 fixes the snapshot rows themselves |
| U7 | A SELL not yet in the journal makes the net-capital sizing cap and the daily new-buy budget LOOSER than they should be (journal book + broker cash, `margin.resolve_net_capital`); a late BUY makes them tighter (fail-safe). | sizing cap, F-306 budget | Open; planner + reviewer (N1 sibling sweep) |
| U8 | 📡 Signals Analyst Revisions alerts are red DANGER ("institutional conviction fading") at bare literals (`portfolio.py`, `get_alerts`), counted in the nav badge and Home's 🔴 count, contradicting #22's watch-only rating-change flag. | `portfolio.get_alerts` (gate file) | Open → implementer + reviewer |
| #25 | 🔔 Playbook on a report day (0d) still reads pre-report for before-open reporters. | Catalyst Watch playbook (gate path) | Open, owner wording call |
| #21 | Grow Today shows two different "today's new buys" figures on one card (99.6% vs 84.3% of net capital). | F-306 budget | Open, inputs not yet traced |
| #34c | The broker-drift check dates the snapshot by `captured_at`, not SnapTrade's last sync, so a dropped connection serving cached holdings could read "no mismatch". | broker drift | Open; read-only SnapTrade probe first |
| #29 f/u | A lot's `buy_date` is the UTC date of `traded_at`, so a buy logged after 20:00 EDT anchors the drawdown peak one day late. | `tax_advisor.py` lots | Open |
| EW | In earnings week, Home 🔧 Tune-up (`risk_advisor.py`) and the Rebalancer still size the trim to 15%, while the Brief, Playbook and Signals use the earnings-week line. | `risk_advisor.py`, rebalancer | Open |
| EW2 | For a book of ≤ 7 names the earnings-week line is above `SINGLE_NAME_TRIM_TRIGGER`, so the Brief and Playbook say Watch while Signals trims the same name. | earnings-week band | Open, owner call |
| Fund | `fundamentals.resolve_fundamentals`: a live set clearing `FUNDAMENTALS_GATE_MIN_METRICS` wins over a fresh, fuller cache row and overwrites it. | fundamentals cache | Open, owner policy call |
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
