# Market and sector trend (display only)

**Status:** DESIGNED 2026-11-21. Opus planner (claude-opus-5-5): PROCEED WITH CHANGES. Owner decisions Q1–Q10 taken 2026-11-21 (all as suggested; option 1 only). Owner took U1–U9 as recommended 2026-11-21 (U3 = 5, U6 = 252, U8 keep "Steady", U9 retire). **C1 COMMITTED 2026-11-21** (`ee5b6a2`, Opus SHIP). **C2 BUILT 2026-11-21** (🌐 Macro section, F-319; Opus SHIP, 0 blocking; review notes N1–N3, N5, N6, N8 applied after review in app.py / tests; N4 logged as SectorRetsEmptyCached in `docs/bug-register.md`; N7 = check on the screenshot that held rows' "Your weight" shows bold). C1 + C2 PUSHED Sat 2026-11-21 17:09 ET; C2 live check 2026-11-21 (owner screenshot): date caption, vs-S&P arithmetic, index Falling + correction, no word on sector rows, bold held weights (N7 settled), "15% no sector fund" all VERIFIED; the sector table's inner scroll hid the Falling rows. **C2b (owner request 2026-11-21):** 🌐 Macro becomes two tabs, "📈 Market and sector trend" (default) and "📡 Macro signals" (the old page content, unchanged); a one-line summary atop the trend tab; full-height tables (no inner scroll). Display only, app.py not a gate file → no review. C2b `638293e` PUSHED 18:06 ET and VERIFIED by the owner the same evening (tabs, summary, full-height table). **C3 BUILT 2026-11-21** (dock: tile trend lines, chips, dialog; Opus FIX-FIRST (chips dropped "you N%" silently before Home built) → fixed → SHIP). C3 live checks: lines under S&P / NASDAQ only, footer dated, chips + "you N%" after Home, the caption before Home, the dialog opens / closes / Open 🌐 Macro navigates, the 170 px pane scrolls to the chips and button (phone too), tiles still refresh each minute, no first-page-of-the-hour stall, Data Health Yahoo errors. Known (review N2, accepted): opening the dialog is a full rerun, so the dock returns to ⚠️ Notices behind it and Home rebuilds. C3 `aacd496` PUSHED Sun 2026-11-22 08:16 ET; VERIFIED live 08:21 ET (owner screenshots): tile lines on S&P / NASDAQ only, dated footer, badges render, the "you N%" disclosure before Home then the tags after (held first, weights sum to 100% with the 15% no-fund), same reading as 🌐 Macro. Dialog VERIFIED 2026-11-22 (owner screenshot: summary bold, both tables full, Open 🌐 Macro); open: phone. **C4 BUILT 2026-11-22:** the 🔥 Sector Heatmap tab is a pointer to 🌐 Macro (F-172 retired); the table caption no longer says "the regime reads further down" (the regime is on the 📡 Macro signals tab) and the first column is wider; owner asked the same day that 📡 Macro signals load by itself (it was button-gated and looked empty): `_cached_macro_raw` runs the old button's loop unchanged, complete read kept 30 min, incomplete 5 min, an all-failed read shows the warning. Display only, no gate file → no review. C4 `b46c40b` PUSHED Sun 2026-11-22 08:56 ET (live check pending). **📡 Macro signals auto-load VERIFIED 2026-11-22 (owner screenshot); phone checks dropped (owner uses a laptop only); PlaybookVsTrend logged (bug register §B). C5 DONE 2026-11-22** (docs: CLAUDE.md queue + live checks, `docs/shipped-log.md`, TrendVsLeading row in `docs/bug-register.md`; F-319 / F-172 / F-211, the User Guide and `docs/user-manual.md` were synced chunk by chunk). **Feature SHIPPED.** Follow-ups outside this plan: SectorRetsEmptyCached (next), TrendVsLeading (owner call). Mockup: `docs/mockups/2026-11-21-market-sector-trend-mockup.html`.

## Why

2026-11-21 the owner saw the market sliding toward bear territory and AI / tech stocks falling for 3–4 weeks while the app said nothing. Detection existed but was buried or binary:

- `exit_advisor.risk_off_regime` (SPY below its `RISK_OFF_TREND_MA`-day average, or VIX ≥ `RISK_OFF_VIX_LEVEL`) shows only as the 🧭 Market-Risk Posture gauge on 🧾 Summary and 🔗 Risk Analysis; `assess_risk_off_derisk` trim cards also need book fragility caution / fragile.
- Market tone (`MARKET_TONE_BEAR_PCT` / `MARKET_TONE_BULL_PCT`) is one day's S&P 500 move.
- The Brief's "leading sectors" are the top 3 sector funds by 1-week return, used only for a +5 rank bonus; falling sectors are never named.
- The sector heatmap (1W / 1M / 3M / 6M) sat in 🥧 Portfolio Overview → 📈 Analytics → 🔥 Sector Heatmap behind a load button.

## Owner decisions (2026-11-21)

- **Option 1 only:** a trend reading for the S&P 500, the Nasdaq and every `portfolio.SECTOR_ETF` fund. Trend lines on the S&P 500 and NASDAQ tiles in the 📈 Markets dock tab, sector chips grouped Falling / Weakening / Rising with a "you N%" tag on held sectors, and a "Full trend table ↗" button.
- **Option 2 dropped:** no ⚠️ Notices line about the book's share in falling sectors (owner was told nothing then pushes the reading to them). No email line (Q7). No gate, size or recommendation change.
- **Q1 rule** (cut-offs in `constants.py`): Falling = below its 50-day average AND 4-week return trails the S&P 500's by more than 3 points, for 3 sessions in a row; Weakening = one leg; Rising = above a rising 50-day average; else Steady. "From high" = drop from the 52-week high; *correction* (−10%) and *bear market* (−20%) on the index rows only.
- **Q4** trend lines on S&P 500 and Nasdaq only (Dow tile unchanged). **Q5** one "Software (IGV)" row for AI & Cloud / AI & Data / Enterprise Tech. **Q6** the full table opens as an `@st.dialog`. **Q8** the table's home is 🌐 Macro (top of the page); the 🔥 Sector Heatmap sub-tab becomes a pointer; one render function for Macro and the dialog. **Q9** keep the 1-week / 3-month / 6-month columns. **Q10** the table shows on 🌐 Macro before 🏠 Home has loaded the book; only "Your weight" waits.

## Design (planner, 2026-11-21)

### 1. Label rule

Per session `d`, on closes dated ≤ `d` only (no look-ahead):

- `SMA50(d)` = mean of the last `TREND_MA_SESSIONS` valid closes ending at `d`. `below50` / `above50` strict; equal is neither.
- `rising50(d)` = `SMA50(d)` > `SMA50` `TREND_MA_SLOPE_SESSIONS` valid closes earlier, strict (C1 build; the same as sessions when the fund has no missing bar).
- `ret4w(d)` = close(d) / close at the NYSE session `TREND_RETURN_SESSIONS` before `d` − 1; base anchored by date (`series.asof`).
- `vs_spx(d)` = r1(r1(fund ret4w) − r1(S&P ret4w)), r1 = round half away from zero to 1 dp (Decimal). The decision uses the displayed figure. `trails(d)` = `vs_spx(d)` < −`TREND_LAG_SPX_PTS`, strict. Benchmark ^GSPC.

**Sector rows** (precedence Falling > Weakening > Rising > Steady):
- Falling: below50 ∧ trails on each of the last `TREND_CONFIRM_SESSIONS` NYSE sessions ending at the as-of session.
- Weakening: below50 ∨ trails at the as-of session, Falling not confirmed ("Weakening · both signs, day k of 3" when both legs hold but not yet N sessions).
- Rising: above50 ∧ rising50 and no weakness leg.
- Steady: otherwise.

**Index rows (S&P 500, Nasdaq):** the relative leg is replaced by the 200-day leg, computed with `risk_off_regime`'s exact arithmetic (`close.tail(RISK_OFF_TREND_MA).mean()`, strict `<`). Falling = below50 ∧ below200 for 3 sessions; Weakening = either leg; Rising = above50 ∧ rising50 ∧ not below200. An index below its 200-day can never read Rising or Steady, so the S&P tile can't contradict 🧭 Market-Risk Posture's trend leg on the same series. Nasdaq still shows "vs S&P 500" in the table (display only).

**Confirmation:** recomputed from price history for the last N sessions (pure, no DB state). Falling ends the first session a leg fails. Weakening and Rising are instant.

**Drawdown words:** from-high = r1((close / max of the last `TREND_HIGH_LOOKBACK_SESSIONS` closes − 1) × 100), closing prices. ≤ `TREND_CORRECTION_PCT` → "correction", ≤ `TREND_BEAR_PCT` → "bear market", inclusive, index rows only.

**Short history:** sector rows need 57 sessions for the SMA and slope legs and 23 for the returns; index rows 202; from-high 252 (else "n/a"). Below that the label is None with "not enough price history (N sessions)".

**New constants:**

| Name | Value | Basis | Whose |
|---|---|---|---|
| `TREND_MA_SESSIONS` | 50 | "50-day average" | owner |
| `TREND_RETURN_SESSIONS` | 20 | "4-week" = 4 × 5 NYSE sessions | owner (session count = planner's translation) |
| `TREND_LAG_SPX_PTS` | 3.0 | "trails by more than 3 points" | owner |
| `TREND_CONFIRM_SESSIONS` | 3 | "3 sessions in a row" | owner |
| `TREND_MA_SLOPE_SESSIONS` | 5 | one trading week smooths the 50-day average's day-to-day sign flips | **owner call U3** |
| `TREND_HIGH_LOOKBACK_SESSIONS` | 252 | standard trading-year session count | **owner call U6** |
| `TREND_CORRECTION_PCT` | −10.0 | owner | owner |
| `TREND_BEAR_PCT` | −20.0 | owner | owner |

The index 200-day leg reuses `RISK_OFF_TREND_MA` (no new constant). Display windows 1-week = 5, 3-month = 63, 6-month = 126 sessions stay module-level (not decisions).

### 2. Time context

- As-of session = `data.previous_trading_day(today_et)`: the last completed NYSE session before today, all day (U5). After 16:00 the trend is one session behind the tiles; the UI names the session.
- Fetch `end = as_of + 1 day` (exclusive), and the module truncates to dates ≤ as_of.
- Sat / Sun / Mon → Fri; Thanksgiving Thu 2026-11-26 and Fri 11-27 → Wed 11-25; Sat 11-28 → Fri 11-27; DST Sundays → the Friday before; 23:30 ET stays the ET date.
- A date past `MARKET_CALENDAR_LAST_YEAR` with no ^GSPC bar → the whole read is None (fails closed).
- Wording: dock footer "📡 09:42 ET · prices live, every 60s · trend at Fri Nov 20 close"; table caption "As of the Fri Nov 20 close."; weight caption "share of your holdings' value at 🏠 Home's last build". Never "yesterday's close".
- Missing bars: fund without the as-of bar → None, "not checked: no close for <date>", named in the dock; a gap inside the confirmation window → Weakening "couldn't confirm"; ^GSPC without the as-of bar → whole result None; all-NaN column → not checked.

### 3. Data

- **`_fetch_sector_returns` stays byte-identical**: it feeds the Brief's +5 leading-sector bonus (a decision input) and Performance Diagnostics. Only its header comment loses "(for heatmap)".
- New app.py fetch, two cache layers:
  - `_trend_result_ok(as_of_iso)`, `st.cache_data(ttl=3600)`: one `_retry(yf.download, [SECTOR_ETF funds deduped incl. SPY, "^GSPC", "^IXIC"], start=as_of−400d, end=as_of+1d, auto_adjust=True, …)` then `market_trend.compute_trend`; raises on empty / None so a failure is never cached.
  - `_cached_market_trend(as_of_iso)`, `st.cache_data(ttl=300)`: catches and returns None, so a Yahoo hang is retried at most every 5 minutes.
- The 60-second `_index_strip` fragment only reads the cache.
- Accepted: on an hourly cache miss the first page view waits one batch download (the dock runs before page content). Measure in the C3 live check; if noticeable, warm the cache from Home's build.
- Known bias: funds are dividend-adjusted, ^GSPC is a price index → about 0.1–0.3 pp in the fund's favour on the relative leg. Documented, accepted.

### 4. Module `stock_analyzer/market_trend.py` (new, pure)

- `as_of_session(now_et)`, `history_start(as_of)`.
- `compute_trend(closes, *, as_of)` → None (couldn't check) or `{"as_of", "indices", "sectors", "not_checked"}`. Rows carry symbol, name, app_sectors, label, reason, confirm_k, ret_4w, vs_spx_pts, ma50_side, ma50_run (+ capped flag), ma200_side and drawdown_word (indices), from_high_pct, ret_1w, ret_3m, ret_6m. One row per fund (SPY not a sector row); IGV display name "Software".
- `held_weights(port_df)` → None (book not loaded, or no positive Market Value to weigh: the app can't tell an empty book from an unloaded one) / `{fund: {pct, tickers, sectors}, "_no_fund": …}`. (C1 build: `{}` is never returned; `chip_groups` / `table_rows` still treat `{}` as "–".) Index rows never show "open 🏠 Home first"; only sector rows do. Sector → `SECTOR_ETF`; missing / "Other" / SPY → `_no_fund`. Fixes the heatmap's IGV-to-"AI & Cloud"-only credit.
- `chip_groups`, `table_rows` (order Falling, Weakening, Steady, Rising, not checked; "<1%" for 0 < pct < 0.5; `_no_fund` shown on the S&P 500 row as "N% no sector fund", U7), `tile_line`.
- No new `st.session_state` key (market data lives in `st.cache_data`; weights join at render time from `get_or_offline(st.session_state, "_port_df_enriched")`), so nothing to register in `coord_freshness.py`. If weights are ever stored in session_state, register that key in the same commit.
- Add `market_trend.py` to `_GATE_FILES` with a `docs/review-policy.md` reason line (it decides a label shown to the owner).

### 5. Wording rules

1. A label is always printed beside a named fund or index, never as a verdict on "the market".
2. Never use regime, risk-off / on, Protect, Growth Mode, Defensive, Watchful on the trend surface; "correction" / "bear market" only beside their % on index rows.
3. Every trend surface carries its date; caption "Labels describe closes up to <date>. Not a forecast, and they change no pick, size or stop."
4. Posture reads SPY (dividend-adjusted, possibly intraday); the tile reads ^GSPC (price only) at the as-of close. **C1 review correction:** SPY's adjusted 200-day average sits roughly half a percent lower, so the gap is a standing band, not a one-session timing effect: while the S&P is about 0–0.5% under its 200-day the tile can read "below 200-day · Weakening" with Posture clear, for days. That direction is the conservative one (the tile is more bearish); "Rising while Posture is risk-off" still needs the intraday timing difference. The C5 User Guide wording must say this, not "one session".
5. User Guide: tone = today, Leading = best 1-week, trend = the last ~10 weeks. **Queue row "TrendVsLeading":** should a Falling sector keep the Brief's +5 leading-sector bonus? Owner call; planner + reviewer; shadow first.
6. One line atop the Macro section: "Each index's and sector fund's own trend; the regime reads further down use other inputs."
7. "Steady" also names Posture level 0; trend "Steady" appears only in the table (U8).

### 6. Heatmap retirement

`_sector_rets` (app.py ~23610 write, ~23612 read) has no other reader (verified 2026-11-21). The whole `with _an_t2:` body goes; it becomes `st.info` "The sector table moved to 🌐 Macro → 📈 Market and sector trend …" plus an Open 🌐 Macro button (`_pending_page` + `st.rerun()`, never `nav_page`). Keep `_fetch_sector_returns`, `sector_exposure` and Relative Strength's own fetch. Retired (U9): the 1M column (replaced by 4-week), the KPI strip, the plotly heatmap, and the "Alignment" column (its bare 5 / 2 / 10 / 0 literals go with it).

### 7. Chunks

1. **C1** constants + `market_trend.py` + `tests/test_market_trend.py` + architecture.md constants rows + `_GATE_FILES` / review-policy.md. Implementer builds; Opus reviewer required. No UI.
2. **C2** 🌐 Macro section (C1 review carry-over: `as_of_session` should convert a tz-aware `now_et` to America/New_York before `.date()`, with a UTC-input test, and the app must pass `market_time.now_et()`; the empty-book wording "open 🏠 Home first" should not mislead if the book is ever truly empty): fetch layers, one `_render_trend_table(result, weights)` (`st.dataframe` + Styler, no `unsafe_allow_html`), placed after the page caption and before the `port_df.empty` gate. Reviewer required. Live check: fresh session before Home (weights "open 🏠 Home first"), after Home (weights fill, IGV lists 3 sectors), Monday caption names Friday, correction / bear on index rows only, column order, no literal `**` / `$`.
3. **C3** dock: tile trend lines inside `_index_strip` (via `util.safe_html`), chips + dated footer + "Full trend table ↗" outside the fragment; module-level `@st.dialog` with Open 🌐 Macro. Reviewer required. Live check: laptop + phone on Home / Signals / Macro, pane scrolls, dialog opens / closes / navigates, tiles still refresh, no first-page-of-the-hour stall, Data Health Yahoo errors.
4. **C4** heatmap pointer and deletion, after C2 is verified live. Display only, not a gate file: skip review. Live check: pointer, button lands on Macro, Home's "Leading:" and Performance Diagnostics still render.
5. **C5** docs (Opus lead): F-319 in `docs/requirements.md` (F-172 marked retired → F-319), `docs/user-manual.md`, in-app User Guide (Macro, Portfolio Overview / Analytics, Markets dock, the §5 notes), CLAUDE.md queue + TrendVsLeading row, `docs/shipped-log.md`, this Status line, memory.

### Tests the build must include

Legs (equal-to-SMA, −3.04 / −3.05 trail boundary, flat slope), confirmation (2 vs 3 sessions, a failing leg, a gap, single legs, above a rising 50-day while trailing, no look-ahead), index rows (below 200 only, never Rising below 200, invariant vs `risk_off_regime` over generated series), drawdown (−9.94 / −9.95 / −19.95, sector gets no word, 252 window, short history), `as_of_session` (Sat, Sun, Mon 08:00 / 17:00, 2026-11-26, 2026-11-27 14:00, 2026-11-28, DST Sundays, 23:30 ET), bars (post-as-of ignored, missing as-of bar, ^GSPC missing, gap in window, all-NaN, None / empty), weights (None, {}, IGV sum, `_no_fund`, "<1%", NaN, total ≤ 0), rows (each fund once, no SPY row, chip grouping and order, `ma50_run` cap), AST pins (Macro and dialog call the same renderer, `_sector_rets` / `_heat_btn` gone, `_fetch_sector_returns` still called at both the Home and Performance Diagnostics sites, `_pending_page` never `nav_page`, the Macro section before the `port_df.empty` gate), `check_antipatterns.py` and `check_constants_documented.py` green.

## Owner calls open (U1–U9)

- **U1** Index rows use 50-day + 200-day legs (200 = shared `RISK_OFF_TREND_MA`), Nasdaq included. Recommended.
- **U2** Both legs for fewer than 3 sessions → Weakening; "above a rising 50-day but trailing by more than 3" → Weakening. Recommended.
- **U3** New `TREND_MA_SLOPE_SESSIONS` = 5.
- **U4** Only Falling is confirmed; Weakening and Rising are instant; Falling exits instantly. Recommended.
- **U5** As-of = last completed session before today ET, all day. Recommended.
- **U6** New `TREND_HIGH_LOOKBACK_SESSIONS` = 252 on closing prices; correction / bear decided on the displayed 1-dp value, inclusive.
- **U7** Weight in "Other" / unmapped sectors shown on the S&P 500 row as "N% no sector fund".
- **U8** (optional) Keep "Steady" or rename it (e.g. "No clear trend") to avoid Posture's "Steady".
- **U9** Confirm the heatmap's 1M column, KPI strip, plotly chart and "Alignment" column retire.
