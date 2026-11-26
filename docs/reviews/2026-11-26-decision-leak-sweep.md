# Decision-leak sweep — 2026-11-26

**Status:** findings logged in `docs/bug-register.md` (rows tagged "sweep 2026-11-26"), **not yet ranked by the owner**. The owner asked for a full register review after BriefSkipSizeBlind (Batch 2, in flight in another session) closes, to pick the next item. The order below is the lead's proposal for that review.

## Scope and method

The ask: while the Tier 0 fixes are still landing, look for other leaks, unconnected dots and unwired functionality that can weaken the calls the app gives.

- Five read-only lanes ran in parallel against HEAD `5db2af12` plus the staged BriefSkipSizeBlind tree (that work was excluded):
  - A, unwired logic
  - B, coordination caches
  - C, the buy-gate × buy-surface matrix
  - D, sell-side suppression
  - E, data inputs and freshness
- Each lane excluded every bug-register row and every CLAUDE.md queue item. Where a finding is a new site of a known class, the row is named.
- No code was changed and no test was run.
- **"Lead-verified"** means the Opus lead re-read the cited code and confirmed the mechanism. **"Agent-reported"** means it rests on the lane's reading only; check it before designing a fix.
- Line numbers are approximate (`~`). app.py moves daily.

## Proposed order (impact high → low)

Ranking rule, the same as the 2026-11-25 register order:
1. Can it lose money (a wrong buy, or a hidden or weakened sell)?
2. How often does the trigger happen?
3. Is it silent (no banner or log)?

Owner calls are marked.

| # | ID | One line | Kind | Trigger frequency | Verified |
|---|---|---|---|---|---|
| 1 | BetaGateWatchlistOnly | The G-06 beta check runs only on the 📋 Watchlist; Grow Today and the 09:45 / 11:30 emails issue sized buys the Watchlist would block | wrong buy | every bull-tone day with a high-beta pick | lead |
| 2 | ScanCacheAnyAge | The app hydrates Grow Today from the saved scan with no date check, so picks are sized on yesterday's prices | wrong buy | every visit before the 09:45 scan, and any day the scan fails | lead |
| 3 | SectorCapCardMerged | A sector Hard Cap Breach card is dropped from Act Today when the sector's largest name also has a stop breach or Sell | hidden sell | whenever the top name in an over-cap sector has a mechanical call | lead |
| 4 | ConcTrimsRiskNone | When portfolio risk is unmeasured, the sector hard-cap and single-name ceiling cards vanish with no notice | hidden sell | a young holding over 10% of the book, or a returns build failure | lead |
| 5 | RiskOffRegimeUnread | A failed 1y SPY or VIX read reads as "not risk-off"; no error, no "couldn't check" line | hidden sell | a provider failure on a risk-off day | lead |
| 6 | DetSpyFail | A failed 6-month SPY read turns off every deterioration trim and the escalated exit; email engine errors never fail the lane | hidden sell | a provider failure | lead |
| 7 | IntradayBelowStop | The 11:30 pullback email can call an entry on a pick already below its own stop | wrong buy | a large intraday drop on a morning pick | lead |
| 8 | SoldTodayLeak | A failed trades read becomes an empty journal (= "nothing sold today"); 📈 Analysis has no sold-today check at all | wrong buy | Analysis after a sell (common); the read failure (rare) | lead |
| 9 | CronStaleHeld | The pre-market / EOD lanes judge a held name on a cached price up to 5 days old, or drop an unpriced name, and stay green | hidden sell + data | a one-ticker provider failure | agent |
| 10 | SentimentFake50 | No headlines → a made-up 50 in the sentiment pillar, with no availability flag | wrong buy / sell | a news outage, or F-309 filtering every headline | lead |
| 11 | CostOnlySig | A cost-basis-only correction serves the old Brief and marks it current | stale sell | a cost-only rebuild / statement import | lead |
| 12 | RiskOffTrimShift | The risk-off trim skips a name that carries any non-selling card, so the 25% trim moves to a lower-beta name; the email disagrees | weakened sell | a risk-off day with a Watch / premortem card on the top-beta name | agent |
| 13 | ToneNoAge | The Watchlist, Analysis and the redeploy headline reuse Home's tone with no age limit | wrong buy | a session where Home isn't revisited after a morning build | agent |
| 14 | YoungUnprotected | A holding with 15–50 sessions gets no deterioration check; risk-off skips a name with unmeasured beta; no notice | hidden sell | every young holding (ANT now) | agent |
| 15 | AnalysisSizesHeld | 📈 Analysis prints a full size where the Watchlist holds (sector cap, sector unknown, beta) — **owner call** (F-43b may make it deliberate) | wrong buy | a Buy in an over-cap sector | agent |
| 16 | PlaceholderTargetRR | A flat "+10%" placeholder target can clear the 2:1 R:R gate for Enter Now — **owner call** (ties to G-32) | wrong buy | a low-ATR name with no analyst target | agent |

**Second tier, money path but lower likelihood or inform-only:**

| ID | One line |
|---|---|
| RedeployHeadlineGates | "A genuine redeploy entry" skips earnings and news, and passes when the bundle is missing |
| CatalystEarnDates | Catalyst Watch's backfilled earnings dates never reach the playbook or the G-33 trim |
| OvernightCaches | Across an ET date rollover with no Home visit, other pages read yesterday's Home caches as current. Summary's all-clear and its weekly-thesis write are among them |
| SparseInfoNorms | On a sparse-`.info` day, cached fundamentals are scored against default norms instead of the sector's |
| MacroTrimDefer | The macro pre-event trim defers to Act cards that sell nothing (the news and premortem legs) |
| BetaRepairUnwired | The F-274 swap / add levers are built but unwired; only trim-to-cash is quoted |
| SplitFlagApp | The app Brief doesn't withhold deterioration calls on split-flagged names; the email does |
| BrokerOnlyUnprotected | A Robinhood-only position is never protected, and the protective email never says so |
| PremortemJournalFail | Premortem trigger cards vanish on an unreadable journal |
| ScannerGoBadge | Market Scanner's "✅ Go" ignores tone / macro / sector / withheld verdict |
| OppSignalsNoConc | 📰 Opportunity Signals "may support adding" with no concentration check |

**Data integrity:** StaleRegimePersisted, EtfWeightsVersion, TjCategoryCarry.

**Time context:** FlowDateUtc, HolidayPremarket.

**Display / cleanup:** SidebarNewsOverwrite, NavBadgeLag, BrokenThesisDaily, ExitSignalsCachedFail, DeadTwins, SweepLiterals, NonUsdSizing, FactorEtfFallback, StressBookBeta, RegistryGaps.

### Suggested batching (for the owner's review)

- **"A sell check that couldn't run reads as clear"** (the RiskOffUnchecked class): ConcTrimsRiskNone, RiskOffRegimeUnread, DetSpyFail, CronStaleHeld, YoungUnprotected. Each needs a disclosure plus a lane failure, not a policy change.
- **Folds into the in-flight buy-check batch** (`docs/plans/entry-fail-closed.md`): SoldTodayLeak, ToneNoAge, RedeployHeadlineGates.
- **One gate, more surfaces:** BetaGateWatchlistOnly, IntradayBelowStop, ScanCacheAnyAge. These are planner work, because they add a hold to surfaces that don't have one today.
- **Brief consolidation:** SectorCapCardMerged, RiskOffTrimShift, MacroTrimDefer. These belong to the same family as BriefSkipSizeBlind, so review them after it lands.

## Detail by finding

### 1. BetaGateWatchlistOnly — lead-verified
- **Where:** `PORTFOLIO_BETA_CEILING` / `TICKER_BETA_CRITICAL` are read in `watchlist_advisor.py` (~205-222) and its cron path, but nowhere in `daily_briefing.py`. Watchlist names are also scanned as extras, so the same ticker reaches both surfaces. The Watchlist email's scanner-Go dedup can hand a beta-blocked name to the pick email.
- **Scenario:** book β 1.6, ticker β 2.0. The Watchlist reads "Portfolio Fit Blocks Entry". Grow Today, the 09:45 pick email and the 11:30 email issue it sized. This is the "one high-beta pocket" loss pattern (memory `loss_attribution_2026_10_19`). Related to EnterNowInProtect.

### 2. ScanCacheAnyAge — lead-verified
- **Where:** app.py ~3482-3498. The session hydrates `scanner_results` once from `db.load_scanner_cache()` (db.py ~3209: the single row id=1, no date filter). `_scanner_cache_checked` blocks a re-hydrate for the session. `daily_briefing.py` ~1477 takes `row["Price"]`, and ~1858-1873 builds the ATR stop, entry zone and share count from it.
- **Contrast:** the cron intraday lane refuses a cache whose `scan_date` isn't today (cron_runner.py ~2738-2743). The app shows the scan date only in the Refresh Signals tooltip (~9662-9666).
- **Consequences:** the names are also logged as `new_pick` rows and graded. architecture.md calls this price "the LIVE price".

### 3. SectorCapCardMerged — lead-verified
- **Where:** the sector card's ticker is `rt[0]["ticker"]`, the largest name in the sector (`daily_briefing.py` ~3255; risk_advisor.py ~759). In `_consolidate_act_today` (~3324-3336), "mechanical wins": a `stop_breach` / `sell_signal` on that name keeps only the mechanical item, so the risk flags carrying the sector trim plan are dropped.
- **Repro (the lane ran it):** a `sell_signal` plus a `sector_concentration` risk card on one ticker gives `[('sell_signal', None)]`.
- **What the owner sees:** 📡 Signals' sector REDUCE card hides its own size behind "Today's Brief has an Act Today item for this sector" (sector_trigger_text.py ~77-90). There is no banner.
- **Duration:** a breach heals via BriefBreachRestore once the name recovers; a Sell signal lasts as long as the composite reads Sell.
- **Class:** the risk-attribution class (memory `card_ticker_semantics`, MacroAff f/u). `_review_list` already handles it (~3456); consolidation doesn't.

### 4. ConcTrimsRiskNone — lead-verified
- **Where:** `home_risk_synthesis.py` ~292-299 publishes `_risk_advisor_recs = None` when `_port_risk` is None (correct for the cache). But `daily_briefing.py` ~3224 and ~458 iterate `risk_recs or []`, and 🔧 Tune-up renders only for a non-empty list (app.py ~13835).
- **What's lost:** the sector Hard Cap Breach Act Today card and the single-name ceiling Tune-up card, although neither needs beta or returns. Only 🔗 Risk Analysis says the data is offline (~19419). The RiskOffUnchecked text fires only on a risk-off day.
- **Trigger:** a young holding above `PORTFOLIO_RISK_UNMEASURED_MAX_PCT` (10%; ANT is at 7.1%), or a returns-series build failure.
- **Class:** RiskListwiseYoung / RiskOffUnchecked.

### 5. RiskOffRegimeUnread — lead-verified
- **Where:**
  - `headless_alert_engine.py` ~164-168: a failed `fetch_spy("1y")` sets None with no `errors.append` (the 6mo failure does get one), and `_vix_level()` may return None.
  - `exit_advisor.risk_off_regime` (~487-509) degrades each missing leg to "not tripped", by its docstring.
  - Neither `daily_briefing` (~4525) nor the email calls `risk_off_state` (only Summary / Risk Analysis do, F-271).
- **Scenario:** VIX above `RISK_OFF_VIX_LEVEL` with SPY still above its 200-day average, and the ^VIX read fails (Yahoo-only; Finnhub 403s it). No de-risk trims, no line, no log. In the app `_cached_vix` keeps the None for 30 min (that caching leg is already in ScanMoversEmptyCached f/u).

### 6. DetSpyFail — lead-verified
- **Mechanism:** `exit_advisor.py` ~365-369 sets relative strength to 0.0 when the SPY or the name's return is missing. `classify_deterioration_tier` (~209-226) needs `rel_strength < 0` for `trim_active`, so every TRIM and the "trim + underwater" EXIT are lost; only `deep_exit` remains.
- **Lane:** `headless_alert_engine.py` ~160-164 catches the `fetch_spy("6mo")` failure into `errors`, and `cron_runner.py` ~624-625 only logs it as an `engine note:`.
- **More of the same:** if the deterioration or risk-off engine raises (~436-440, ~536-543), the result is `[]` plus an `errors` line that never fails the lane: "nothing to act on — no email", dedup state saved, lane green.
- **App:** the same SPY failure crashes the build loudly instead.

### 7. IntradayBelowStop — lead-verified
- **Where:** `intraday_entry.py` ~78-99 tests only `drop <= -dip_pct` (no lower bound) and sorts biggest drop first. There is no current-price-vs-stop or zone check.
- **Sizing:** the stop, zone and size come from the morning scan price (`compute_morning_picks`, cron_runner.py ~2745). notify.py ~1026-1106 prints "now $X · stop $Y" unguarded.
- **Scenario:** a pick falls 9% on a guidance cut (more than 2×ATR) and is emailed first as an "entry window".

### 8. SoldTodayLeak — lead-verified
- **Read failure:** app.py ~3557 sets `trades_df = _t if _t is not None else pd.DataFrame(columns=db._TRADE_COLS)`, which undoes `load_trades_or_none`'s None. `sold_today.tickers_sold_on` (~40-46) reads an empty frame as "genuinely no sells".
  - The app records `_db_load_failure` (~3558-3561). Whether the banner it drives is only a soft "warn" was reported by the lane, not checked.
  - The cron fails closed on the same input (`headless_alert_engine.py` ~1405-1411, "suppressing all entries").
  - The same empty frame plausibly also hits the F-306 budget lag correction, the G-24 add cooldown, premortem triggers and held-stop buy anchors (agent-reported).
- **Analysis:** 📈 Analysis has no sold-today input at all. `analysis_entry_decision` (entry_eligibility.py ~688) takes none, and the not-held Buy branch sizes via `position_sizing` (~27319) and shows "→ N suggested" (~27486). The Watchlist (~30700) and the Brief (~4483, ~1017) do check.
- **Trade Journal:** the BUY confirm's `journal_hold_note` (~32886) reads only `_wl_held_cache`.
- **Class:** RecsNone / EntryFailOpen.

### 9. CronStaleHeld — agent-reported
- **Mechanism:** `bundle_loader.py` ~148-163 serves `db.load_bundle_cache(..., BUNDLE_CACHE_MAX_AGE_DAYS=5)` on a fetch exception and sets `stale_as_of`.
- **Where `stale_as_of` is not read:**
  - `compute_eod` snapshot rows (`headless_alert_engine.py` ~1891-1897) and `book_positions` / `gross_book` (~1910-1934);
  - the partial-outage guard (cron_runner.py ~1141-1165, any `Price > 0` counts as priced);
  - the pre-market stop-breach `Gap to Stop` (~382), the deterioration EXIT and fragility.
- **Dropped names:** a bundle that raises is skipped as an "engine note"; a priceless bundle goes to `attrs["dropped_holdings"]`, which the cron never reads (portfolio.py ~539-560); a row with avg cost 0 is dropped the same way.
- **Footer:** notes ("no stop yet") go out only when another alert triggers an email.
- **Contrast:** the app says "📦 Showing cached data" (app.py ~6350). Different trigger from YahooNaN.

### 10. SentimentFake50 — lead-verified
- **Mechanism:** `sentiment.analyze_news([])` returns 0.0, `sentiment_score_0_100(0.0)` = 50.0, and it goes into `combined_score` (`bundle_loader.py` ~382-383, weight 0.10).
- **Already noticed:** `util.sentiment_value_or_none`'s docstring (~143) calls the missing flag "a separate, larger finding — D3"; no register row existed.
- **Impact:** a real-80 name loses about 3 points and a real-20 name gains about 3, enough to cross `COMPOSITE_BUY` / `COMPOSITE_HOLD`. `entry_eligibility`'s news check is "unchecked", but the composite still carries the 50, and the cron's `score_history` records it as real.

### 11. CostOnlySig — lead-verified
- **Where:** `_synth_sig` (app.py ~7495-7518) carries ticker and Shares, but no Avg Cost. A rebuild (~35633), statement import (~35906) or screenshot rebaseline (~36265) can change only the cost and still bump the epoch (`_refresh_portfolio_cache_after_trade`, ~3239); the comment at ~8893-8901 admits this case.
- **On the memo hit:** `_synth_cache["epoch"] = _coord_epoch()` (~7535) and `_stamp_coord` (~8908) re-stamp the Home caches as fresh.
- **Stale calls:** the P&L-gated trims (`REBALANCE_TRIM_PNL_PCT`, the "Sell & pnl>0" half-trims portfolio.py ~1001 / ~1070, profit-lock, exit_advisor's gain tiers) and `_reduce_calls` stay stale until the next miss.

### 12. RiskOffTrimShift — agent-reported
- **Where:** `daily_briefing.py` ~4516-4520 passes every non-news Review card's ticker as the risk-off exclusion set (decision_bucket.py ~320-348; exit_advisor.py ~641-646). Excluded names are dropped before ranking, so the trim slides to a lower-beta name with no line saying why.
- **Email mismatch:** the email excludes only stop breaches and deterioration exits (`headless_alert_engine.py` ~537-540), so it can say "TRIM — Risk-Off NVDA" while Home trims others.
- **Class:** DeferSizeBlind, at build time.

### 13. ToneNoAge — agent-reported
- **Where:** `resolve_tone_state` (entry_eligibility.py ~48-75) prefers any same-day Home build and never reads the live tone once one exists (app.py ~30855-30890, ~27146-27180; entry_posture.py ~92-110). The 30-minute watchdog runs only while Home is open (~9603-9622). On a bull or flat day no read time is shown.
- **Class:** time-context sibling of EnterNowInProtect / GrowTodayStaleToday.

### 14. YoungUnprotected — agent-reported
- **Deterioration:** a holding with fewer than 50 closes has NaN `SMA_50`, so `assess_holding` returns None (`exit_advisor.py` ~339-341). F-320 covers only the missing stop (fewer than 15 bars).
- **Risk-off:** the pool drops a name with fewer than `BETA_MIN_RETURNS` (20) returns (~647-658), even if it is the largest beta-times-weight driver.
- **Disclosure:** neither is disclosed per name. ANT qualifies until about mid-December.

### 15. AnalysisSizesHeld — agent-reported, owner call
- **Where:** `entry_eligibility.analysis_entry_decision` (~688-711) runs stale, tone, macro, news and earnings only. The sector cap renders as a red ✗ tile (app.py ~28814) while the size still prints (~28901). A G-06 breach is a "consider 50–60%" warning (~28968-28990). An unclassified sector gets a size where the Watchlist holds `sector_unknown`.
- **Owner question:** may be deliberate under F-43b / one-sizing-path D5.

### 16. PlaceholderTargetRR — agent-reported, owner call
- **Where:** `targets.py` ~107-122 always offers `base_source="flat"` (`TARGETS_MODEST_UPSIDE_MULT` 1.10). `watchlist_advisor.py` ~554 / ~664 gate on that R:R with no `base_source` check.
- **Scenario:** with ATR at or below about 2.5% (stop = 2×ATR), R:R = 10/5 = 2.0 passes. The card labels it a placeholder. Nothing decides whether a placeholder may open an entry (G-32 U1–U13).

### Second tier (all agent-reported)
- **RedeployHeadlineGates:** `entry_posture.redeploy_call` (~170-230) checks tone and `data_trust_hold` only; with `bundle is None` the hold stays None and the entry is called (~205-213). Call sites are app.py ~13242 (Home Rebalance) and ~18506 (📡 Signals). The macro leg is MacroGap f/u.
- **CatalystEarnDates:** the 🔔 table backfills from FMP / yfinance via `_cached_held_earnings_dates` (app.py ~5640-5645). `build_earnings_playbook` (earnings_advisor.py ~619) and `trim_sizing.earnings_days_by_ticker` read the bundle `earnings` only. Sibling of StaleEarningsDate.
- **OvernightCaches:** freshness is book-only (`_coord_epoch`, `coord_freshness.classify`; `_render_portfolio_stale_banner` ~2668 has no date leg).
  - Readers that use the previous day's values: Signals `_actions_cache` (~17329), Analysis `_under_reduce` (~28140), the Rebalancer `_rb_reduce_set` (~23409), Watchlist `_grow_today_sectors_cache` / `_risk_high_alerts_cache` (~30780), Catalyst `_leading_sectors_cache`, Summary `book_safety` (~15203).
  - Summary's Act Today renders "🟢 Nothing to act on today" without checking the Brief's build date (~15705-15769).
  - Summary persists the weekly State-of-Portfolio thesis from `_home_synth_cache.bundle` (~15446, ~16866-16921) with no date or epoch check, also after a trade.
- **SparseInfoNorms:** `bundle_loader.py` ~194-210 keeps `_sector_for_scoring` on raw live `.info`. On cache-served fundamentals days, `fundamentals._SECTOR_NORMS["_default"]` replaces sector norms (e.g. Technology P/E 20/45/65 becomes 15/28/45).
- **MacroTrimDefer:** the deferral set is every Act ticker (`daily_briefing.py` ~3452, ~3951-3954), including critical-news "Hold for now" and premortem cards. MacroAff f/u names only the risk-attribution leg.
- **BetaRepairUnwired:** `beta_repair.expected_beta_after_swap` (~121), `dollars_to_target_add` (~152) and `dollars_to_target_swap` (~182) have no production callers; risk_advisor.py ~289 wires only `dollars_to_target_trim` (plan `docs/plans/beta-repair.md` §1, F-274).
- **SplitFlagApp:** `daily_briefing.py` ~4466 passes no `split_flagged`; the email does (`headless_alert_engine.py` ~437).
- **BrokerOnlyUnprotected:** the drift banner is buy-email only (notify.py ~253-278); `compute_protective_alerts` has no drift input.
- **PremortemJournalFail:** `premortem_monitor.py` ~228-229 returns [] on the lenient read's empty frame.
- **ScannerGoBadge:** app.py ~26233-26281 calls `reconcile_signals` with no tone / macro / sector / news / stale input; `lookup_composite` (signal_reconciliation.py ~336-346) returns a withheld or stale bundle's `total`. This widens the entry-fail-closed plan §1 Scanner row.
- **OppSignalsNoConc:** app.py ~23174-23230 cross-checks Reduce / Exit only.

### Data integrity (agent-reported)
- **StaleRegimePersisted:** the trade `decision_context` (app.py ~34284-34288) and the premortem prompt (~2155) take the first `_macro_regime_*` key in session_state. Those keys are date-keyed (~20178, ~20635, ~46664) and never pruned, so in a long session this returns the oldest day. `_market_tone_cache` is undated, defaults to "flat", and is persisted as `regime` in the thesis-review `evidence_snapshot` (~48434) and the thesis draft (~33611). Neighbour of RegimeCacheSameDay.
- **EtfWeightsVersion:** `ETF_COMPOSITE_WEIGHTS_VERSION` (constants.py ~1133) has no reader. `db.save_recommendations` (~3355) stamps `COMPOSITE_WEIGHTS_VERSION` on ETF `new_pick` rows too. `build_enter_now_rows` (recommendations_history.py ~153-183) writes no `asset_type`. Latent: Predictive Analytics excludes ETFs.
- **TjCategoryCarry:** `_reset_tj_decision_context` (app.py ~1955-1971) doesn't clear `_tj_situational_category` (widget ~33905, saved ~34361).

### Time context (agent-reported)
- **FlowDateUtc:** the 💰 Account deposit / withdrawal date (`_acct_fdate` ~42162) and the baseline date (`_acct_bdate` ~42070) default to `date.today()` on a UTC container. These are baselined NAIVE_DATE_TODAY sites. A flow logged after 8 PM ET (EDT) defaults to tomorrow.
- **HolidayPremarket:** `premarket.is_premarket()` (~53-60) is True on an NYSE holiday, so Home builds a Pre-Market Stance for a closed session (today, Thanksgiving). `evening_debrief._next_trading_day` (~270-284) skips weekends only.

### Display / cleanup (agent-reported)
- **SidebarNewsOverwrite:** 📈 Analysis overwrites `_sidebar_news` (~27047, ~27060). 🥧 Portfolio Overview's News Intelligence (~23098) and every page's sidebar news slot then show the analysed ticker's headlines as held news.
- **NavBadgeLag:** the sidebar reads `_n_danger_cache` / `_n_warning_cache` / `_risk_high_alerts_cache` / `_earnings_posture_alerts_cache` (~3649-3668) before Home writes them, so the badges are one run behind. Sibling of Bal f/u.
- **BrokenThesisDaily:** `_broken_thesis_tickers` is date-keyed (~12564-12572); a BROKEN review saved mid-day doesn't invalidate it.
- **ExitSignalsCachedFail:** `_exit_signals_cache` loads once per session (~33475, ~52603) and keeps a failed read.
- **DeadTwins:** superseded helpers still in gate modules, each with a docstring pointing the wrong way:
  - `portfolio.stop_ladder` (~362; the real explainer reads `held_stop_view`);
  - `held_trim_owner.brief_sell_names_ticker` / `brief_sell_tickers` / `view_sell_tickers` (`act_sell_mention.py` ~29 still names the last one);
  - `trim_sizing.earnings_week_trim` (~150);
  - `entry_eligibility.macro_hold` (~129);
  - `act_sell_mention.verified_sells` (~120).
- **SweepLiterals:** bare ±0.05 sentiment cut-offs in `news_intelligence.py` (~106-107, ~119, ~163-165, ~290-291, ~593-594); `earnings_stop_cover.estimate_earnings_move` 3.0 / 25.0 / 3.1 / 7.0 (~51-54); no reader for `DEFAULT_PORTFOLIO_VALUE` (constants.py ~495).
- **NonUsdSizing:** there is no currency handling. 000660.KS (KRW) gets dollar stops and zones; sizing returns 0 shares, but the name takes a Grow Today slot. Sibling of NonUsSessionShift.
- **FactorEtfFallback:** `_fetch_factor_etf_returns` (app.py ~5134) has the `close.iloc[:, 0]` fallback. Sibling of SectorRetsPartial.
- **StressBookBeta:** `stress_test.py` ~316-319 gives an unmeasured holding the book beta (or 1.0) and can list it in `most_exposed`. Near ThinStressLines / RiskBudgetYoung.
- **RegistryGaps:**
  - `_news_feed_cache` (~8882) is written and never read.
  - Keys missing from `coord_freshness`: `_n_danger_cache`, `_n_warning_cache`, `_earnings_posture_alerts_cache`, `_judgment_opinions_today` (🧑‍⚖️ The Judge, no stale banner).
  - `_home_synth_cache` itself is unregistered, yet Summary reads it directly.

## Checked and found sound (so the next sweep can skip them)

**Sell side:**
- `act_today_view`: offline gives None, never 0.
- Home memo `_new_breach` rebuild.
- `act_sell_cover` / `_verified_act_today_sell_cover`: None never defers; build date and freshness are checked.
- `held_stop_wiring.build_held_stops` never raises.
- These pre-market failures do fail the lane: a DB outage, a total price outage, unreadable manual stops, `held_stops_failed`, an unreadable floor or carry, a journal-fault stop basis, `risk_off_unchecked`.
- The EOD session-close guard.
- `_review_list` excludes risk-attribution tickers.

**Buy side:**
- The 09:45 / intraday / Watchlist budget crashes withhold sizes.
- An Analysis held-add uses Grow Today's verdict.
- The ETF lane's sector blindness is by design.
- The intraday lane checks the cache date, fails safe on a missing SPY, and labels its zone "original zone".
- Headless tone `tone_available=False` fails closed.

**Caches:**
- `_reduce_calls` readers pair `_coord_cache_state == "ready"` with `_daily_brief_offline`.
- Correctly three-state: `_div_recs_cache`, `_risk_high_alerts_cache`, `_risk_advisor_recs_cache`, `_alert_list_cache` / `_actions_cache`, `_corr_unchecked_cache` / `_corr_coverage_cache`, `_leading_sectors_cache`, `_brief_news_flags_cache`, `_journal_match_cache`, `_movers_checked`, `_manual_stops`.
- Date- and epoch-aware: `_new_position_budget_cache`, `_wl_budget_plan_cache`, `_home_dock_digest`.
- No `k in st.session_state` liveness test outside `_coord_cache_state`.

**Inputs:**
- `pillar_availability.verdict_basis`.
- `entry_eligibility` / `buy_check_state` honour `stale_as_of`.
- These raise rather than cache a failure: `_cached_held_stop_floors` / `_cached_held_stop_carry` / `_cached_spy` / `_scorecard_spy_closes` / `_sector_returns_ok` / `_trend_result_ok`.
- `_failover_single` rejects empty / all-NaN.
- `sold_today` / `daily_budget` use ET dates.
- `trading_sessions_between` / `is_trading_day` honour NYSE holidays.
- Beta joins on dates.
- `assess_risk_off_derisk` uses measured betas only.

**Wiring:**
- The provider circuit-breaker.
- The `DATA_*_ORDER` / `DATA_XCHECK_*` / `LLM_EVAL_*` constants.
- G-25 / G-26 maps.
- `macro_sector_gate.decide`.
- `usable_news_flags`.
- R-Multiple capture.
- Held-stop carry / floor / young-holding notes in the cron lanes.
- `risk_off_check` in the Brief and the cron.
- Watchlist sizing caps.
- `act_sell_mention` on its four surfaces.
