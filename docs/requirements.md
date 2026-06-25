# Requirements Document
## DRISHTA — Beyond Noise
*Personal Portfolio Intelligence App*

**Version:** 2.0  
**Date:** June 2026  
**Status:** Active Development  
**Operating Posture:** Decides, not informs (see §2A)

---

## 1. Purpose and Scope

This application is a personal investment management and intelligence platform built for a retail investor managing a concentrated US equity portfolio. It replaces manual spreadsheet tracking and fragmented news/analysis tools with a single, always-current dashboard that surfaces actionable signals, manages risk, and builds a track record of decisions over time.

The app is configured to **make decisions, not merely inform**. Recommendations are presented as the call; suppressions are hard rather than soft. The app does not execute trades — the user retains all execution authority — but its recommendations are designed to be trusted and acted upon directly. See §2A for the operating-posture commitments that shape every requirement below.

The app is not a brokerage or order-execution system.

---

## 2. User Profile

- **Primary user:** Individual retail investor
- **Portfolio size:** Variable; moderate risk tolerance
- **Experience level:** Active investor, growing familiarity with technical and fundamental analysis
- **Usage pattern:** Daily briefing check before market open; ad-hoc analysis during trading hours; end-of-day journal entries
- **Access:** Web browser via Streamlit Community Cloud; no mobile-specific UI required

---

## 2A. Operating Posture and Decision Policy

This is an explicit operating-mode commitment that drives functional design. It is the most important policy section in this document because every gate, threshold, and suppression below derives from it.

### 2A.1 Operating principles

| ID | Principle |
|---|---|
| OP-01 | The app makes decisions. Recommendations are issued only when conditions warrant action. The app would rather recommend nothing than recommend wrongly. |
| OP-02 | Gates are hard suppressions, not soft warnings. When a gate fires, the contradicting recommendation is removed from the UI with an explicit explanation, not appended as a caution badge. |
| OP-03 | Data integrity failures fail loud. When a required input is missing (stop price, composite score, daily briefing, portfolio risk metrics) the dependent feature surfaces an explicit "offline" or "unavailable" state. Fabricated fallbacks are never used. |
| OP-04 | Cross-feature coordination is mandatory. Two features that make overlapping decisions never silently contradict each other. The pattern is: one publishes state; downstream features read and gate. |
| OP-05 | Secondary objectives subordinate to investment view. Tax outcomes do not override the investment thesis (HARVEST suppressed on Buy/Strong Buy). Drift-rebalancing does not override active risk reduction (Rebalancer ADD suppressed when Risk Advisor TRIMs the same ticker). |
| OP-06 | All decision thresholds live in `stock_analyzer/constants.py`. Changes to any value are investment-policy decisions, not code tuning. |

### 2A.2 Decision thresholds (policy)

Single source of truth: `stock_analyzer/constants.py`.

| Threshold | Value | Type |
|---|---|---|
| Portfolio beta — target | 1.0 | Baseline |
| Portfolio beta — elevated (soft warn) | 1.3 | Soft |
| Portfolio beta — ceiling (hard breach) | 1.4 | **Hard** |
| Ticker beta — high (combines with elevated portfolio) | 1.5 | Soft |
| Ticker beta — critical (combines with breached portfolio) | 1.8 | **Hard** |
| Sector — ceiling | 35% | **Hard** |
| Sector — elevated | 25% | Soft |
| Single-name — ceiling | 15% | **Hard** |
| Composite — Buy boundary (entry AND add-to-winner) | 65 | Gate |
| Composite — Strong Buy boundary | 75 | Tier label |
| Composite — Hold floor (below = Sell zone) | 44 | Tier label |
| Fundamentals gate — min core metrics to trust the verdict | 1 | **Hard** (withhold verdict below it) |
| Entry reward:risk minimum (`RR_ENTRY_MIN`) | 2.0 | **Hard** on Watchlist ENTER_NOW (G-13); caveat on Analysis |
| Risk per trade | 1.5% of portfolio | Sizing |
| Earnings imminence window (`EARNINGS_IMMINENT_DAYS`) | 7 days | Caution (binary-event conflict) |
| Earnings "manageable window" — Brief verdict agreed signal (`EARNINGS_MANAGEABLE_DAYS`) | 8–21 days | Tier label |
| Earnings urgency "SOON" tier — Catalyst Watch playbook (`EARNINGS_URGENCY_SOON_DAYS`) | 8–14 days | Display tier |
| Macro imminence window (HIGH-impact event in pick's sector) | 3 days | **Hard** |
| Catalyst Watch forward window (`CATALYST_WATCH_WINDOW_DAYS`) | 7 days | Awareness |
| Review profit-lock — P&L trigger / trim size | 25% / 25% of position | Action target |
| Review stop-tighten ATR multiple (vs 2.0× initial) | 1.5× | Action target |
| Review earnings-overweight — trim trigger / target weight | 12% / 10% | Action target |
| Review weak-large — trim-to target weight | 8% | Action target |
| Review macro-affected — sector trigger / reduction | 30% / 5pp | Action target |
| Exit deterioration — WATCH / TRIM / EXIT drawdown-from-peak floors (`DETERIORATION_*_DD_PCT`) | 6% / 8% / 12% | Action target (exit ladder) |
| Exit deterioration — ATR-scaled TRIM / EXIT floor (capped) | 2.5× / 3.5× ATR; capped 14% / 20% | Action target |
| Exit deterioration — EXIT escalation on unrealized $ loss | $250 | Action target |
| Exit deterioration — trend reference / confirmation | close < SMA50; 2 of last 3 sessions (EXIT skips confirm) | Confirmation |
| Risk-off de-risk — SPY trend trigger (`RISK_OFF_TREND_MA`) | below 200-DMA | Regime (Faber) |
| Risk-off de-risk — VIX trigger (`RISK_OFF_VIX_LEVEL`) | ≥ 25 | Regime |
| Risk-off de-risk — min name β / top-N / trim size | β ≥ 1.2 / top 3 by β×weight / 25% | Action target |
| Concentration — high-beta cluster share (soft warn, `CONCENTRATION_HIGHBETA_SHARE_WARN`) | 60% | Soft |
| Pullback fragility — routine-correction yardstick (`FRAGILITY_PULLBACK_PCT`) | −10% | Awareness |
| Protective alert email — premarket / EOD ET hour (`ALERT_EMAIL_HOUR_ET` / `ALERT_EOD_HOUR_ET`) | 08:00 / ≥ 16:00 ET | Operational |
| Reactive pullback alert — index drawdown trigger (`PULLBACK_ALERT_INDEX_PCT`) | −3% | Awareness |
| Mover — min 1-day gain to qualify | 5% | Discovery gate |
| Mover — shortlist size composite-gated / max picks surfaced | 12 / 3 | Discovery sizing |
| Price cross-check — prev_close tolerance (settled) | 0.5% | **Integrity** (strict) |
| Price cross-check — live-price tolerance (latency) | 3.0% | Soft (loose) |
| Data live-price provider order | Finnhub → yfinance → FMP | Sourcing policy |
| Data general provider order (history/bundle) | yfinance → Finnhub → FMP | Sourcing policy |

The "Review action target" rows translate a *trigger* (when an item lands in Review Before Close) into a *concrete directive* (trim N shares, raise stop to $Y). See §3.2 F-25 and architecture §4.0.1.

### 2A.3 Hard gates currently enforced

| ID | Gate | Behaviour |
|---|---|---|
| G-01 | Risk Advisor TRIM → Grow Today add-to-winner | Suppress; banner explains |
| G-02 | Risk Advisor TRIM → Rebalancer ADD | Suppress; banner explains |
| G-03 | News Intelligence alert → Rebalancer ADD | Attach `news_warning`; critical drops urgency to bottom |
| G-04 | Single-name ≥ 15% → Grow Today add-to-winner | Suppress; concentration banner |
| G-05 | Sector ≥ 35% → Watchlist ENTER_NOW | Downgrade to NEAR_ENTRY with portfolio-fit card |
| G-06 | Portfolio β > 1.4 AND ticker β > 1.8 → Watchlist ENTER_NOW | Downgrade to NEAR_ENTRY |
| G-07 | Imminent HIGH macro (3d) → Grow Today new picks in affected sector | Suppress; macro banner |
| G-08 | Composite Buy → Tax HARVEST | Suppress; `HOLD_FOR_SIGNAL` action with banner |
| G-09 | Rebalancer drift-overweight → Grow Today add-to-winner | Suppress; banner |
| G-10 | Earnings within 7 days → Buy Candidates verdict | Escalate to "Caution" or "Conflicted" depending on other signals |
| G-11 | Stop data missing → Act Today SELL trigger | Skip mechanical SELL; show "stop unavailable" review item instead |
| G-12 | Composite Signal empty on held position → Confirmed verdict | Route to "🔍 Verify — Composite Signal Missing" |
| G-13 | ENTER_NOW without validated R:R (`rr is None`) | Downgrade to NEAR_ENTRY; R:R must be `≥ 2.0` |
| G-14 | `build_daily_briefing` failure → coordination caches | Set to `None`; dependent features show explicit offline banner |
| G-15 | Fundamentals absent from live sources (< `FUNDAMENTALS_GATE_MIN_METRICS`) AND no fresh cache → Analysis verdict AND Brief new-pick | **First, fall back to last-known-good:** `load_all` serves the persistent `fundamentals_cache` copy when the live leg is sparse and the cache is within `FUNDAMENTALS_CACHE_MAX_AGE_DAYS` — the verdict then renders normally with an amber "📦 Fundamentals as of N days ago" note (`fund_source = "cache"`). **Only when there's no fresh cache** is the verdict withheld: on Analysis, BOTH the Summary Scorecard row (Score → "—", Signal → "🚫 Withheld") and the Detailed Analysis block (red "verdict withheld" note) suppress the fabricated-neutral-50 verdict; price-derived columns (Stop, Target, R:R) remain. `daily_briefing` holds the ticker out of `new_picks` (→ `composite_unavailable`). |
| G-16 | Sector at/above `SECTOR_CEILING` (35%) → Grow Today new picks AND add-to-winner in that sector | Suppress; render "Suppressed — Sector Hard Cap" banner (`sector_blocked_picks` / `sector_blocked_adds`). A Strong Buy in an over-cap sector is a KEEP, not an add — deploy-capital defers to protect-capital |
| G-17 | CPI YoY > `REGIME_CPI_CONTROLLED_MAX` (2.5%) → macro "Rate-Cut Optimism" regime | Block: the regime cannot be selected even if risk-on signals (low VIX, strong SPY) win the score, because its rationale claims "controlled inflation." Reassigns to the next-best regime. Prevents a label that contradicts itself (e.g. 3.95% CPI in a "controlled inflation" regime). |
| G-18 | Held position with breached stop (price ≤ stop, i.e. Act Today's Gap-to-Stop ≤ 0) → Analysis "add to position" sizing | Suppress the add: the Analysis Trade Plan replaces the "Sizing below is for adding" note with a red "⛔ Stop breached — exit signal, not an add" banner (mirrors Act Today's SELL) and hides the position-sizing block. The Buy composite still shows (it rates the STOCK); the stop protects the POSITION — protect-capital overrides deploy-capital. Same detector condition as the Brief, so the two surfaces agree instead of contradicting (ADBE: Brief SELL vs Analysis "add"). |

### 2A.4 Soft warnings (kept in addition to gates)

| ID | Warning | When |
|---|---|---|
| W-01 | Sector ≥ 25% concentration on a new entry | Soft caution in card |
| W-02 | Portfolio β > 1.3 AND ticker β > 1.5 | Soft caution; sizing recommendation |
| W-03 | Active HIGH risk alerts in portfolio | Resolve-first prompt in Watchlist |
| W-04 | Grow Today and Watchlist same-sector overlap | Inform the user one sector trade is enough today |
| W-05 | Trade Journal entry thesis missing on a weak-large-position review | Prompt user to log thesis on future entries |

---

## 2B. Intended Investor Persona and Time Horizon

This app is built for the **quality-first, medium-term investor with long-term tax discipline** — and a defensive bias. It is explicitly **not** a day-trading tool. This framing is a design constraint: features and gates are evaluated against it, and a request that only serves intraday trading is out of scope (see §5) unless the persona itself is revisited.

### 2B.1 Persona fit by horizon

| Persona | Horizon | Fit | Rationale |
|---|---|---|---|
| Day trader | seconds–hours | **Not supported** | The engine actively works against it (see 2B.2). |
| Swing trader | days–weeks | **Partial** | Technical signals, entry zones, ATR stops, R:R, Catalyst Watch and Movers serve this — but quality gates and the no-trade-into-earnings posture temper pure momentum. |
| Medium-term investor | weeks–months | **Primary (sweet spot)** | Composite scoring, sector rotation, macro-regime, rebalancing, diversification advisor all operate on this horizon. |
| Long-term investor | months–years | **Strong** | 40% fundamental weight, valuation/FCF, tax-loss harvesting, 1-yr cap-gains & wash-sale awareness, behavioural discipline. Fit strengthens with horizon. |

### 2B.2 Why the design is investor-oriented, not trader-oriented

| ID | Design choice | Implication |
|---|---|---|
| PH-01 | Composite = Technical 45% + **Fundamental 40%** + Sentiment 15%; verdict is WITHHELD when fundamentals are unavailable. | A 40% fundamental weight is central to an investor and irrelevant to an intraday trader. |
| PH-02 | Composite signals are computed on page load and held; only prices refresh (~60s). Signals do **not** recompute every tick. | Deliberate anti-overtrading choice (also a rate-limit guard). Intraday signal churn is intentionally absent. |
| PH-03 | Proximity gates suppress initiating into earnings; Catalyst Watch is awareness, **not** a buy rec. | The app declines binary-event volatility that a trader often seeks. |
| PH-04 | Stops are ATR-based, R:R is multi-day, sizing is risk-%-of-portfolio. | Swing-to-position constructs (holding across days/weeks), not intraday levels. |
| PH-05 | Macro-regime lens reads CPI / Fed / rates (months horizon); concentration caps, beta/Sharpe/Sortino/VaR, rebalancing, tax/wash-sale all operate at portfolio/horizon scale. | Portfolio-construction and protection, not per-trade edge capture. |
| PH-06 | Even Movers (1-day breakouts) are funnelled through the **same composite quality gate**, never traded on momentum alone. | The most opportunistic surface is still quality-first — the philosophy in miniature. |

### 2B.3 What day-trading support would require (fork, not extension)

Supporting day traders would mean intraday/real-time data feeds, Level 2 / order-flow, tick charts, removal of the fundamental weighting, and removal of the earnings/macro gates that make this app safe. That is a different product with an opposite risk philosophy — a fork, not an increment. Documented here so the boundary stays explicit.

---

## 3. Functional Requirements

### 3.1 My Portfolio (Home)

| ID | Requirement |
|----|-------------|
| F-01 | Display all held positions with live price, shares, average cost, market value, P&L ($), P&L (%), portfolio weight (%) |
| F-02 | Auto-refresh live prices every 60 seconds during market hours via a Streamlit fragment. Prices come from the multi-source layer (§3.10) — real-time from Finnhub during market hours, with gap-fill to yfinance/FMP. The price-strip caption shows the actual source(s); a fail-loud banner appears if held-position prices fail the cross-check. |
| F-03 | Show real-time daily P&L (total portfolio $ and %) based on prior-close vs current price |
| F-03a | **Today's P&L — Tier B (true positions day-P&L).** Beyond the held-only prior-close mark (F-03), compute the day's P&L as an equity delta against a baseline persisted in the Supabase `daily_snapshots` table (per-ticker shares + close written by the EOD cron, F-141). This captures positions opened/closed intraday that a held-only mark misses. The label fails loud about its scope; Tier B is inert until the one-time `daily_snapshots` DDL is applied. **Still out of scope:** cash/flows reconciliation and full broker reconciliation. |
| F-03b | **Account-level view — account-baseline v1 (cash + total account value).** The app otherwise reasons only about invested equity; setting a user-entered uninvested **cash balance** (single-row Supabase `account_cash`, manually entered now — the same table the Robinhood MCP sync would later auto-fill) unlocks a dedicated **💰 Account** nav page (between Home and Market Scanner) showing **Total Account Value** (equity + cash), **Invested Equity**, **Cash %**, and **true concentration** (each holding as % of the *whole account* alongside its equity weight). **Display-only:** the 15%/35% concentration GATES still fire on equity weight — moving them to account-basis is a deferred investment-policy decision. Inert until the one-time `account_cash` DDL is applied (load returns None → equity-only behaviour + a nudge to set cash). Cash entry is data-sanity validated (non-negative; soft-warns implausible) and read-only-viewer-aware. Out of v1: growth/return (needs a flows ledger, v2) and TWR/IRR (v3). Plan: `docs/plans/account-baseline.md`. |
| F-03c | **Growth vs contributions — account-baseline v2 (flows ledger).** On the 💰 Account page, a cash-flow ledger (Supabase `account_flows`: a `baseline` anchor + `deposit`/`withdrawal` rows; manual now, broker-fillable later) separates money **deposited** from money the **market made you**. **Net Contributed Capital** = baseline + deposits − withdrawals; **Growth $** = total account value − NCC; **Growth %** = growth ÷ NCC (rendered "—" when NCC ≤ 0 or the portfolio/cash isn't loaded — never a bogus number). The baseline defaults to the current total account value (growth-from-today) or accepts lifetime net deposits (all-time gain). A deposit raises total value and NCC equally, so it can never masquerade as growth. Pure calc in `stock_analyzer/account.py`; **display-only — feeds no gate.** Inert until the `account_flows` DDL is applied. Out of v2 → v3: time-weighted return / IRR (uses the `daily_snapshots` series). Plan: `docs/plans/account-baseline.md`. |
| F-04 | Detect and prompt for stock splits; adjust shares and average cost accordingly |
| F-05 | Allow user to add, edit, and remove holdings; persist to Supabase `holdings` table |
| F-06 | Display sector allocation pie chart and P&L bar chart |
| F-07 | Show sector exposure breakdown as a percentage of portfolio |
| F-08 | Calculate and display composite signal (Buy/Hold/Sell) for each held position |
| F-09 | Show portfolio-level risk metrics: Sharpe ratio, Sortino ratio, max drawdown, beta, correlation matrix |
| F-09a | **Pullback fragility gauge (pullback-awareness Phase 1).** Surface a portfolio "fragility" read — how hard a routine market pullback (`FRAGILITY_PULLBACK_PCT`, −10%) would hit the book given its beta/concentration — framed as: timing isn't predictable, but exposure and reaction are. Awareness only; it does not issue a buy/sell. Consumed downstream by the risk-off de-risk overlay (F-25e) and the reactive pullback email (F-142). |
| F-10 | Performance attribution: breakdown of return by position and sector |
| F-11 | Earnings calendar for held positions (KPIs, timeline, detail table with Fwd EPS/weight/P&L/signal, Pre-Earnings Playbook) lives on the 🔔 Catalyst Watch page ("Your Holdings — Earnings" tier), NOT a Home tab — consolidated there with the watchlist/universe awareness tier so all earnings detail is in one place. |
| F-12 | Risk advisor: flag positions breaching stop-loss, concentration risk, correlation clusters. Beta and Sharpe recommendations name specific trim targets, which downstream features (Grow Today add-to-winner, Rebalancer ADD) cross-check and suppress to avoid contradicting the active reduce-exposure recommendation. |
| F-12a | **Concentration / sizing discipline (entry-time enforcement).** Closes the asymmetry where manual journal buys bypassed the position-sizing ceilings the recommendation paths already respected (a single name had reached ~23%). Three coordinated surfaces, all from the pure `concentration.py`: (a) an **entry nudge** at trade entry when an add would push a single name past `SINGLE_NAME_CEILING` (15%); (b) a `single_name_concentration` MEDIUM recommendation on standing positions over the ceiling; (c) a **high-beta cluster** line when the high-β share of the book exceeds `CONCENTRATION_HIGHBETA_SHARE_WARN` (60%). Concentration breaches are structural, so they stay in **Act Today** (not the slow Tune-up lane). |
| F-13 | Rebalancer: suggest target weights and trim/add trades. ADD actions cross-check News Intelligence alerts (suppressed for critical news; warned for negative) and Risk Advisor TRIM recommendations (ADD suppressed on tickers being trimmed). Suppressions render as explicit banners. |
| F-13a | Diversification Advisor ADD card: candidates are sourced from the broad discovery universe (≈20 names per sector, curated roster unioned first so known names are never dropped; capped by `DIVERSIFY_SCAN_CAP`), not a fixed roster — so the best diversifier surfaces, not a frozen list. Each candidate is cross-validated against the quality engine (composite/signal/R:R, the SAME numbers as Analysis, via the `_grow_composites` cache + load_all fallback), gated at `COMPOSITE_BUY`, ranked best-first, and shows the top `DIVERSIFY_DISPLAY_TOP` with a 🎯 best-passer / 🚦 none-clears banner. Failing names stay visible-but-demoted (never silently filtered). Each carries an "▶ Analyze {ticker}" button to the full Analysis scorecard (the bridge from candidate to decision/trade). |
| F-14 | Stress test: model portfolio impact under defined macro scenarios (rate shock, recession, etc.) |
| F-15 | Display market-closed context note in sidebar showing last close date when market is shut |
| F-16 | Tax Advisor: per-position tax-lot analysis with HARVEST recommendations on harvestable losses. HARVEST is **suppressed** on positions rated Buy or Strong Buy (action becomes `HOLD_FOR_SIGNAL`) so the tax tail does not wag the investment dog. The UI explicitly surfaces every suppressed harvest with the position's current signal so the user can revisit if conviction degrades. |
| F-17 | Stop data integrity: when a position's stop value is missing or zero, the portfolio table displays "—" for Stop and Gap, marks Stop Type as "Stop Unavailable", and the mechanical SELL trigger in Act Today is skipped (the user is prompted to set a manual stop). No fabricated fallback is substituted. |
| F-18 | **Action Log — manual stop override (closes the recommend→act→log loop):** when Review Before Close advises raising a stop, the user can record the new stop level. It persists to the Supabase `manual_stops` table and overrides the computed ATR/ratchet stop in `build_portfolio_df`. Override-wins is **one-directional**: a manual stop is honoured only when it is ≥ the current computed stop (you can tighten, not loosen below the mechanical floor). Overridden positions show a 📌 badge and Stop Type="Manual" wherever the stop is rendered (portfolio table, Trade Plan, Summary Scorecard). |
| F-19 | A manual stop is auto-cleared (orphan cleanup) when the ticker's share count drops to 0 — `save_holdings` symmetric-sweeps `manual_stops` so a stop override never outlives the position it protects. |

### 3.2 Today's Brief (Daily Briefing)

| ID | Requirement |
|----|-------------|
| F-20 | Show market tone header (bull / bear / flat) based on S&P 500 daily change (≥+0.5% bull, ≤-0.5% bear). Header date uses ET timezone; when market is closed, appends "data as of [last trading day]". |
| F-21 | Display date, S&P 500 %, Nasdaq %, and top 2 leading sectors by 1-week return |
| F-22 | Act Today (defense / right column): prioritised list of urgent actions — stop breaches, sell signals, critical news, macro events, risk-advisor trims. Each item carries a structured directive (what to do), why, and trigger — not a one-line label. (See F-22a/F-22b.) |
| F-22a | Act Today items are **consolidated per ticker**: a mechanical-exit signal (stop breach or sell signal) on a ticker suppresses any risk-trim card for the same ticker (you don't trim what you're exiting); multiple risk flags on one ticker merge into a single card. Consolidation logic lives in `daily_briefing._consolidate_act_today`. |
| F-22b | Each Act Today item exposes `kind` (stop_breach / sell_signal / critical_news / macro / risk), a `directive` (concrete action), `why`, and `trigger`; risk items additionally carry a `risk_flags` list. A back-compatible `reason` field is synthesised for legacy consumers. |
| F-23 | Grow Today (offense / left column): market-tone-aware growth setups — new positions and add-to-winners on bull/flat days; deferral message on bear days |
| F-24 | Each Grow Today pick includes: ticker, sector, scanner score, thesis one-liner, suggested entry zone (lo–hi range), position size (shares, cost, stop) |
| F-24a | **Movers** (discovery picks from outside the curated universe — see F-60a) surface in the SAME "New Positions to Initiate" list as curated picks, not a separate developer-facing section; the user sees one action list regardless of how the ticker was sourced. Movers face the composite gate (≥65), macro gate, and act-today block like curated picks, but get their OWN allowance (`MOVER_MAX_PICKS`=3) separate from the curated cap, skip the curated momentum bar and the 1-per-sector diversity rule, and are **exempt from the flat-day high-conviction suppression** (a composite-Buy stock up ≥5% today is itself the clearer direction the flat-day caution waits for). They still respect bear-day risk-off (no Grow Today processing at all on bear days). |
| F-25 | Review Before Close (defense / right column, under Act Today): approaching stops, near-term earnings, weakening large positions. Each item issues a **quantitative directive** — not prose. Item types: WATCH, TIGHTEN_ONLY (raise stop to current − 1.5×ATR), TRIM_AND_TIGHTEN (profit-lock 25% of position + tighten stop when P&L ≥ 25%), TRIM_TO_TARGET (earnings-overweight → trim to 10%; weak-large → trim to 8%), PROTECTIVE_TRIM (macro-affected sector → reduce lowest-conviction holding by 5pp). Each carries `headline`, `action`, `why`, `trigger`. |
| F-25a | **Calm-advisor split (§2B persona enforcement).** The defensive column is split by URGENCY into **🔴 Act Today (decisions only)** — stop_breach / sell_signal / risk / critical_news + active trims (TRIM_AND_TIGHTEN / TRIM_TO_TARGET / PROTECTIVE_TRIM) — and **👁️ Monitoring / Awareness (FYI)** — macro / WATCH / TIGHTEN_ONLY + anything unrecognised (fail-to-calm). Borderlines are policy flags (`BUCKET_CRITICAL_NEWS_IS_ACT`=True, `BUCKET_TIGHTEN_ONLY_IS_ACT`=False). When the Act bucket is empty, a prominent **"✅ Nothing to act on — you're set for today · monitoring N items"** banner replaces it (derived from empty-Act; no new persistence). Logic: `decision_bucket.split_defensive`. **Risk-metric refinement:** slow-moving Risk-Advisor drags (Sharpe / beta / volatility / drawdown / tail risk — `_TUNEUP_RISK_TYPES`) are NOT same-day decisions, so they route to a separate **🔧 Portfolio Tune-up** lane (`portfolio_tuneup`) rendered below Act/Awareness, not into Act Today. Concentration breaches stay in Act (structural). |
| F-25b | **Position lifecycle + settling grace.** Each held position has a lifecycle state (`position_lifecycle.classify_position_state`: exit / at_risk / settling / winning / established) from age (oldest still-held lot via `tax_advisor._build_open_lots`), P&L, and gap-to-stop. A "settling" position (held < `POSITION_SETTLING_DAYS`) is exempted from the routine approaching-stop tighten nudge (it sits at its normal entry-to-stop distance by construction); exits and ≤3%-gap items are NEVER suppressed (danger beats age; no-journal age=None never settles). A 🌱/📈/⚠️ lifecycle badge renders on Review cards. |
| F-25c | **Signal hysteresis ("steady vs yesterday").** A Grow-Today pick whose composite moved ≤ `HYSTERESIS_COMPOSITE_DELTA` since its most-recent prior surface AND whose verdict didn't flip is marked with a calm grey "↔ Steady vs yesterday" chip (`signal_hysteresis.apply_hysteresis`, fed by a 4-day look-back over `db.load_recommendations`). Continuity reads as continuity, damping the urge to re-litigate persistent picks daily (§2B). **Annotate-only — never adds, removes, re-orders, or suppresses a pick** (cannot fight the buy gates); skipped under the AM lock and when offline. |
| F-25d | **Exit-discipline deterioration ladder (`exit_advisor.py`).** Idiosyncratic (name-specific) deterioration is graded into WATCH / TRIM / EXIT off **drawdown-from-peak** (peak re-anchored on material adds — Phase 1.1), gated by a broken trend (close < SMA50) and confirmation (2 of last 3 sessions; a deep EXIT skips confirmation). Floors are the larger of a fixed % (6 / 8 / 12) and an ATR-scaled level (capped 14 / 20), so volatility cannot quietly disable the stop; a TRIM escalates to EXIT on an unrealized loss past `DETERIORATION_EXIT_DOLLAR_LOSS` ($250). Renders as a Review item with the lifecycle badge. Motivated by a trade-log loss review. |
| F-25e | **Risk-off market-wide de-risk overlay (exit-discipline Phase 2).** When the **book is fragile** (F-09a) AND the regime is risk-off (SPY below its 200-DMA OR VIX ≥ 25 — Faber trend rule + high-vol cut), surface a single `risk_off_derisk` action that trims the **top-3 beta contributors** (β × weight, only genuinely high-β names with β ≥ 1.2) by a modest 25% (or tighten their stops instead) — it does not touch the whole book or defensives. Computed AFTER the act/review buckets and **single-surfaced**: tickers already flagged by an idiosyncratic exit (F-25d) or other reduce action are excluded so a name is never double-asked. Ranked highest-urgency in the defensive column. |
| F-25f | **Brief tone-staleness reconciliation (annotate-only).** Pre-market, when the tone banner is stale (computed from the last scanner run, e.g. Friday's close — `not is_open AND last_close != today`) AND live pre-market futures materially contradict it (tone bear + futures bull, or tone bull + futures bear; reuses the existing futures bull/bear direction, no new threshold), append an amber reconciliation note to the tone banner, e.g. "Reflects Fri Jun 20 close — live futures currently higher (ES +0.6%); refresh after the open." **Annotate only — pre-market futures NEVER flip the tone, gates, or recommendations.** Resolves a red "Protect Mode" banner silently sitting above green live futures. |
| F-25g | **Action Log — Phase B "Log this trim" (closes the recommend→act→log loop for trims).** Each Review trim card (`TRIM_TO_TARGET` / `TRIM_AND_TIGHTEN` / `PROTECTIVE_TRIM`) carries a one-click "📒 Log this trim" button that pre-fills the Trade Journal SELL form (trim ticker + suggested shares + decision context, `followed_intent="yes"`) so an executed trim is logged without leaving the Brief; once logged, holdings recompute and the recommendation stops re-firing. The macro `PROTECTIVE_TRIM` case correctly targets `action.trim_ticker` (the weakest sector holding; the card's own `ticker` is `None`) — the trim subject is resolved before the button gate. Button keys use a per-render card index so two macro cards trimming the same name can't collide. (Extends F-18's manual-stop loop from stops to trims.) |
| F-26 | Buy Candidates (offense / left column, under Grow Today; titled "More Buy Candidates"): scanner picks cross-referenced across 5 layers (Technical, Composite, News, Earnings, Revisions) with a confidence verdict (Confirmed / Mixed / Conflicted / Caution / Unverified). Earnings risk is checked against a unified `earnings_lookup` derived from held positions AND pre-fetched composites, so non-held new picks are also screened. |
| F-26a | Buy Candidates must be **de-duplicated against everything already shown in Grow Today** (new picks, add-to-winners, composite-skipped, macro-blocked, composite-unavailable) so the same ticker never appears in both lists on the same screen. |
| F-26b | The Brief renders as a two-column **offense / defense** layout: left column = Grow Today + More Buy Candidates (where to deploy capital); right column = Act Today + Review Before Close (what to protect). Section chips must stay within their column and not bleed across the page when the opposite column is empty. |
| F-27 | Each Buy Candidate card shows: ticker, sector, score, verdict badge (amber for Verify, not blue), technical summary, conflicts and agreed signals |
| F-28 | Quick Research panel: user enters any ticker (e.g. from external news), app returns up to 5-bullet actionable summary — signal, momentum, entry timing, key context, and (when portfolio_ctx is supplied) portfolio-fit including sector-level Act Today awareness |
| F-29 | Entry timing verdicts (boundaries inclusive): High Risk — Avoid Chasing (RSI≥80 or 1D≥15% or 5D≥25%), Wait for Pullback (RSI≥68 or 1D≥5% or 5D≥12%), Oversold — Potential Entry (RSI≤35), Normal Entry Conditions otherwise |
| F-30 | All Analyze buttons navigate to Stock Analysis with a Back button returning to Today's Brief |
| F-31 | On flat market days, Grow Today must output confirmed-verdict picks before unverified-verdict picks so highest-conviction ideas lead |
| F-32 | Pre-Market Intel panel: visible 4:00–9:29 AM ET weekdays only; appears at top of Today's Brief tab |
| F-33 | Pre-Market Intel shows US futures (S&P 500, Nasdaq 100, Dow, Russell 2000) with price and % change vs prior close |
| F-34 | Pre-Market Intel shows overnight % change for major global indices (Nikkei, Hang Seng, DAX, FTSE, CAC 40) |
| F-35 | Pre-Market Intel shows pre-market movers (≥0.5% change) for all held positions and watchlist tickers; held positions are visually distinguished |
| F-36 | Pre-Market Intel shows today's HIGH and MEDIUM impact economic events as "catalysts" |
| F-37 | Pre-market expected open tone (bull/bear/flat) is derived from ES=F futures % change (≥+0.4% bull, ≤-0.4% bear) |
| F-37a | **Catalyst Watch** — its OWN nav page (🔔 Catalyst Watch, before Economic Calendar; viewable any time, not gated to pre/post-market). TWO tiers: (1) **Your Holdings — Earnings** = the full per-position detail + Pre-Earnings Playbook (rendered from the canonical port_df/held_data the Home brief built, via `_render_holdings_earnings`). The held-name earnings DATE comes from the bundle (`held_data["earnings"]`); when that is missing (a yfinance hiccup — the date is a different yfinance endpoint than `.info`, so it can be blank even when fundamentals are present), it is backfilled for DISPLAY ONLY via `_cached_held_earnings_dates`, which uses the SAME two-source path as the Radar tier: the FMP market-wide calendar first, then a per-name yfinance fallback (`fetch_next_earnings`) for names the (sparse, on free tier) FMP calendar didn't cover — so held names get equal earnings coverage to watchlist/universe names. This is awareness-only and never feeds a gate. The underlying bundle date itself is also repaired at the data boundary: `orchestrator.get_bundle` backfills the earnings date from FMP's light per-ticker accessor independently of whether `.info` was sparse, so the earnings-proximity gates (which read the bundle date) stay armed; (2) **On Your Radar — Watchlist & Universe** = upcoming earnings within `CATALYST_WATCH_WINDOW_DAYS` (7) for NON-held tracked names, grouped Today/Tomorrow/Next-7d, each row ticker · sector (🔥 when leading) · date · before-open/after-close · watchlist/universe chip. Both tiers have a ticker→Analysis "Analyze" control. **Awareness only** — must NOT recommend initiating into earnings (proximity gates still suppress that); removes the blind spot of a tracked name reporting unannounced (the PANW case). Radar dates: FMP market-wide calendar (one call) then a per-name yfinance fallback for names FMP didn't cover, so universe coverage doesn't depend on FMP's tier. 24h-cached. |
| F-38 | When `build_daily_briefing()` fails, the page must render an explicit "offline" state on dependent features (notably the Watchlist) rather than silently disabling coordination gates. The user must see that gates are inactive. (Implements G-14.) |
| F-39 | Grow Today must surface a "Composite Scores Unavailable" banner when the composite pre-fetch failed for any of the intended top picks, so the user knows the multi-factor gate did not run for those tickers. |
| F-39a | Grow Today must hard-suppress new picks in any sector with a HIGH-impact macro event scheduled within `MACRO_IMMINENT_DAYS` (3 days), and surface a "Picks Suppressed — Imminent HIGH-Impact Macro Event" banner with the affected sectors and event date. (Implements G-07.) |
| F-39b | Grow Today and Buy Candidates add-to-winner blocks must suppress: (a) tickers Risk Advisor recommends trimming, (b) positions at or above the single-name ceiling (15%), and (c) positions drift-overweight beyond equal-weight + tolerance. Each suppression class renders a distinct banner with the conflict reason. (Implements G-01, G-04, G-09.) |
| F-39c | Review Before Close items flagged as weak-large-positions must pull the entry thesis (most recent BUY notes) and up to two recent lessons from the Trade Journal for that ticker, rendered as an amber-bordered block below the mechanical assessment. When no journal entry exists, prompt the user to log thesis on future entries. |
| F-39d | Grow Today must hold a candidate OUT of new picks when its composite was computed without real fundamental data (`fundamentals_available == False`, i.e. < `FUNDAMENTALS_GATE_MIN_METRICS` core metrics present). Such a ticker is routed to the `composite_unavailable` bucket, never surfaced as a "new position to initiate". (Implements G-15.) |
| F-39e | Grow Today must suppress BOTH new picks and add-to-winners whose sector is at/above `SECTOR_CEILING` (35%), surfacing a "Suppressed — Sector Hard Cap" banner. This keeps the deploy-capital signal from contradicting the Risk Advisor's concurrent sector-trim recommendation (the ESTC case). (Implements G-16.) |
| F-39f | Portfolio sector classification must fall back to the ticker's yfinance `.info` sector before the `"Other"` catch-all, so unmapped tickers carry a real sector and the "Other" bucket cannot accumulate a spurious hard-cap breach. `risk_advisor` and the F-39e gate both read this same `port_df["Sector"]`, so the Act Today breach card and the Grow Today gate agree. |

### 3.3 Stock Analysis

| ID | Requirement |
|----|-------------|
| F-40 | Analyse any ticker (not limited to held positions) |
| F-41 | Summary scorecard table: price, composite score, signal, position/entry zone, stop, base target, R:R, shares, P&L/cost, earnings |
| F-42 | Scorecard adapts to context: held tickers show actual shares and P&L%; non-held show entry zone and suggested entry cost |
| F-43 | Signal banner shows composite score breakdown (technical × 45% + fundamental × 40% + sentiment × 15%) |
| F-43a | When fundamentals are unavailable from all sources (`fundamentals_available == False`), the Analysis page must WITHHOLD the Buy/Hold verdict — suppressing the signal banner and composite number — and render a red "Verdict withheld — fundamentals unavailable" note. The note must explain the data gap and the possible Brief↔Analysis mismatch (a ticker that appeared as a Brief new-pick when data was present), and advise re-checking / broker verification. The verdict must not be computed on the fabricated neutral-50 fundamental. (Implements G-15.) |
| F-43b | When the Analysis verdict is Buy/Strong Buy but the entry R:R is below `RR_ENTRY_MIN` (2.0), the Trade Plan must surface a caveat distinguishing stock quality (composite) from entry timing (R:R): per-share risk/reward, a suggested pullback level, and "KEEP, not an add here — user's judgment." The verdict must NOT be downgraded (Analysis is a research/judgement surface, not a hard gate; the hard R:R block is G-13 on Watchlist ENTER_NOW). |
| F-44 | Analyst upside note shown only when it reinforces signal direction (upside on Buy; suppressed on Sell and when downside on Buy) |
| F-45 | Trade Plan tab (Buy/Strong Buy): entry zone, stop loss, R:R, position sizing (shares, cost, max risk) |
| F-46 | Exit Plan tab (Sell/Strong Sell): current price, shares held, position value, P&L if sold now; exit recommendation banner; ATR downside level |
| F-47 | Position Monitor tab (Hold): stop loss, shares held, P&L; guidance to maintain with stop; specific 7-day re-check date and two concrete action triggers (add if score ≥58; exit if price closes below stop) |
| F-48 | Price scenarios chart (Bull / Base / Bear) in all signal modes; sell mode shows position $ impact per scenario |
| F-49 | Chart tab: candlestick with Bollinger Bands, SMA 20/50, RSI panel, optional volume |
| F-50 | Risk tab: Sharpe, Sortino, max drawdown, beta, volatility vs SPY |
| F-51 | Deep Dive tab: fundamental signals, analyst revisions, earnings proximity, news sentiment |
| F-52 | Analysis Summary expander: formatted markdown summary for all analysed tickers |
| F-53 | Source links: Yahoo Finance, Finviz, SEC filings, Yahoo News |

### 3.4 Market Scanner

| ID | Requirement |
|----|-------------|
| F-60 | Scan a curated universe of ~73 tickers across 12 sectors for technical momentum, **extended at runtime with the user's Watchlist tickers** (tagged sector="Watchlist") so watched names are scored alongside the curated set |
| F-60a | **Movers discovery (close the discovery gap):** a separate `scan_movers()` pass scans the broad ~200-name `discovery_universe` for today's 1-day gainers ≥ `MOVER_MIN_DAY_GAIN_PCT` (5%). The top `MOVER_SHORTLIST_SIZE` (12) gainers are composite-gated; those clearing `COMPOSITE_BUY` (65) feed the unified New Positions list (see F-24a). The composite gate is the noise filter — a 1-day pop without fundamentals/sentiment behind it is rejected. Cached 30 min via `_cached_scan_movers`. **Two distinct sources:** (1) the candidate *list* is a **static, hardcoded curated set** (`DISCOVERY_UNIVERSE` in `discovery_universe.py`, ~200 liquid large/mid-caps grouped by sector, deduped against curated/held/watchlist at runtime) — NOT scraped from an index provider and NOT pulled from a screener API; it is treated as DATA and refreshed manually (~quarterly). (2) the price *data* used to compute each name's 1-day change comes from **yfinance** (a single batched `yf.download(..., period="3mo")`) — the same market-data source as the rest of the app; there is no paid/third-party movers feed. Consequently the only discovery limits are (a) a breakout must be one of the ~200 listed names, and (b) yfinance must return its data. Widening the net (full S&P 500 / live screener API) is a documented future-expansion path, not current behaviour. |
| F-61 | Score each ticker 0–100 using RSI, trend alignment (SMA 20/50), and price momentum |
| F-62 | Surface top picks ranked by score; display sector, signal, RSI, trend, momentum |
| F-63 | Scanner results persist in session state and feed the Daily Briefing buy candidates list |

### 3.5 News Intelligence

| ID | Requirement |
|----|-------------|
| F-70 | Aggregate news headlines for all held positions from Yahoo Finance |
| F-71 | Score each headline using VADER sentiment (Positive / Neutral / Negative) |
| F-72 | Flag tickers with predominantly negative news as "Requires Attention" |
| F-73 | Provide Analyze button navigating to Stock Analysis for flagged tickers |

### 3.6 Trade Journal

| ID | Requirement |
|----|-------------|
| F-80 | Log all buy and sell trades with ticker, action, shares, price, notes, and trigger type |
| F-81 | Persist trades to Supabase `trades` table |
| F-81a | **SELL integrity guard:** a SELL whose shares exceed what the logged BUYs can cover is hard-blocked at form submit. The guard validates against `db.recalculate_from_trades()` — the SAME trade-replay source the drift detector reads — so the guard and the drift detector can never disagree (validating against the `holdings_df` cache instead let an unmatched SELL slip through after a rebaseline; that was the May 2026 COIN double-SELL bug). An explicit override is required to record a SELL beyond accountable shares. |
| F-81b | **Double-submit dedupe:** an identical `(ticker, action, shares)` submission within 15 seconds is rejected with a "duplicate ignored" warning. The live-prefilled price is deliberately EXCLUDED from the dedupe signature — it ticks between reruns and otherwise disguises a double-click as two different trades (the mechanism behind the COIN double-SELL). |
| F-82 | Decision Context capture on each trade: signal seen at time of trade (auto-filled from current portfolio signal; help text shows load-time timestamp so pre-fill is clearly dated), whether signal was followed (yes / no / discretionary), deviation reason, lesson learned |
| F-83 | My Patterns section: analyse historical trades to compute signal accuracy vs override accuracy |
| F-84 | Surface costly deviations (ignored signal, position lost money) and good overrides |
| F-85 | Behavioural insight: compare signal-follow win rate vs override win rate; flag if ≥2 costly deviations |
| F-86 | Lessons library: aggregated lessons from all logged trades |

### 3.7 Economic Calendar

| ID | Requirement |
|----|-------------|
| F-90 | Display upcoming macro events (Non-Farm Payrolls, CPI, FOMC, etc.) with dates |
| F-91 | Show relevance of each event to the current portfolio holdings |

### 3.8 Watchlist

| ID | Requirement |
|----|-------------|
| F-100 | Allow user to add tickers to a watchlist; persist to Supabase `watchlist` table |
| F-101 | Show composite signal and key metrics for watched tickers |
| F-102 | Provide advisor recommendations for watchlist entries: REMOVE / HOLD_OFF_EARNINGS / ENTER_NOW / NEAR_ENTRY / WAIT_ENTRY / WAIT_CATALYST |
| F-103 | ENTER_NOW must require composite score ≥ `COMPOSITE_BUY` (65), price in/near entry zone, AND validated `R:R ≥ 2.0` (rr=None is NOT a green light). Tickers without R:R fall through to NEAR_ENTRY. (Implements G-13.) |
| F-104 | ENTER_NOW must apply the portfolio-risk gate using `_port_risk_cache`, `_risk_high_alerts_cache`, and `_grow_today_sectors_cache`. Hard breaches (sector ≥ 35% OR portfolio β > 1.4 + ticker β > 1.8) downgrade to a "Setup Ready, But Portfolio Fit Blocks Entry" NEAR_ENTRY card. Soft concerns keep ENTER_NOW with an amber caution banner. |
| F-105 | When `_daily_brief_offline` is True, the Watchlist must surface an explicit warning that coordination gates are disabled and instruct the user to revisit the Portfolio page. |
| F-106 | "Log Planned Trade" button on an ENTER_NOW card must prefill the Trade Journal form with ticker, price, stop, and trigger=`WATCHLIST_ENTRY`, and route via `_pending_page` (never assign `nav_page` directly — that key is widget-bound and raises `StreamlitAPIException`). |

### 3.9 AI Brief

| ID | Requirement |
|----|-------------|
| F-110 | Generate a natural language portfolio brief using an LLM (Anthropic / OpenAI / Google) |
| F-111 | Brief should summarise portfolio state, key risks, and suggested actions |

### 3.10 Market Data Layer (multi-source: failover + cross-check)

The app must not depend on a single market-data source. A provider abstraction (`stock_analyzer/providers/`) sits behind `data.py`'s public functions so the rest of the app is source-agnostic. See architecture §4.0.4.

| ID | Requirement |
|----|-------------|
| F-120 | Market data must be served through a provider chain with automatic failover. A `DATA_MULTISOURCE_ENABLED` master switch toggles the layer; when off, `data.py` behaves exactly as the single-source yfinance code (instant rollback). |
| F-121 | **Live prices** use `DATA_LIVE_PRICE_ORDER` (Finnhub → yfinance → FMP). Finnhub (real-time US quotes) is primary; the chain gap-fills — later providers supply only tickers the primary couldn't. A Finnhub outage must degrade to yfinance (delayed), never fail outright. |
| F-122 | **History, the analysis bundle, indices, and risk-free rate** use `DATA_PROVIDER_ORDER` (yfinance → Finnhub → FMP). yfinance stays primary; when a yfinance call hard-fails (e.g. rate-limited), the request fails over to FMP so composite scoring does not go dark. The broad scanner/movers scans stay on yfinance only. |
| F-123 | **Price cross-check guardrail:** held-position prices are validated against an independent source (cached 5 min). `prev_close` is compared strictly (`DATA_XCHECK_PREVCLOSE_TOL_PCT` 0.5%) — a settled value whose mismatch signals a real integrity fault (missed split, wrong-symbol, poisoned feed). Live price is compared loosely (`DATA_XCHECK_LIVE_TOL_PCT` 3.0%) to tolerate delayed-vs-real-time latency. A breach renders a fail-loud red "Price unverified — sources disagree" banner naming the tickers and gaps. (Implements OP-03 at the data boundary.) |
| F-123a | **Validator-health gate (no false alarms during a validator outage).** The cross-check is **skipped** when its independent validator (the second live-price provider, yfinance / `"yahoo_finance"`) is RED in `api_health` (sustained degradation — `rate_limits ≥ 3` OR `consecutive_errors ≥ 5`, e.g. Yahoo 401 Invalid Crumb / 429 from a datacenter IP). A "disagreement" against a degraded validator is the validator's own fault, not a real integrity fault, so surfacing the red F-123 banner then would be a false alarm. In that state both surfaces (Home, Analysis) show a grey "Price cross-check paused — validator degraded" caption (transparent, never silent) and the check auto-resumes when the validator recovers. **Red-only by design:** a genuine integrity fault with a HEALTHY validator still fires the F-123 red banner — the gate must not mask a real fault. Single chokepoint in `orchestrator.crosscheck_batch`; the gate and the UI caption share one threshold (`api_health` red). |
| F-124 | **Source transparency:** every live-price record is tagged with its provider; the price-strip caption shows the actual source(s) ("Finnhub (real-time)" / "Yahoo Finance (15-min delayed)" / "FMP"), and the Data Health sidebar shows per-provider call / error / rate-limit counts. The user can always see where a price came from. |
| F-125 | **Keyed-provider configuration:** Finnhub/FMP API keys live in Streamlit secrets (`FINNHUB_API_KEY`, `FMP_API_KEY`). A missing key makes that provider report unconfigured and be skipped silently (no error). The secret reader tolerates a key mis-nested under a TOML `[section]` and falls back to an env var for offline self-test. |

### 3.11 Headless Automation & Email Alerts (out-of-app cron)

The app must deliver its protective signals even when the user never opens it. A **headless runtime** (GitHub Actions, no Streamlit) runs the same decision logic and emails the user. This is the app's **second runtime**: `cron_runner.py` (dispatch) → `headless_alert_engine.py` (compute) → `notify.py` (Resend HTTP email), sharing the app's data path via the extracted `bundle_loader.py`. Credentials resolve **env-first** (GitHub repo secrets) then `st.secrets`, and the Supabase client is a module-global singleton (the cron has no `st.cache_resource`). See architecture §4.0.x and memory `project_email_alerts_cron`.

| ID | Requirement |
|----|-------------|
| F-140 | **Pre-market protective alert (exit-discipline Phase 3).** ~08:00 ET on trading days, recompute protective signals headlessly and email the user when (and only when) the actionable set is non-empty: breached/triggered stops, deterioration EXIT (F-25d), and the risk-off de-risk overlay (F-25e), single-surfaced via a shared `reduced` set so a ticker is never listed twice. |
| F-141 | **EOD snapshot + reactive pullback (post-close).** After `ALERT_EOD_HOUR_ET` (≥ 16:00 ET) on trading days, write the day's `daily_snapshots` baseline (per-ticker shares + close — the Tier B Today's-P&L baseline, F-03a) and evaluate the reactive pullback condition. |
| F-142 | **Reactive pullback email (pullback-awareness Phase 2).** When the index draws down past `PULLBACK_ALERT_INDEX_PCT` (−3%) AND the book is fragile (F-09a), email a reactive pullback notice — framed as exposure/reaction, never a market-timing call. |
| F-143 | **Once-per-ET-day idempotency + DST safety.** GitHub cron is UTC with no DST, so each job is scheduled on TWO UTC slots straddling EST/EDT; `cron_runner` guards on trading-day + ET-hour + a per-day Supabase `alert_state` flag (row 1 = premarket/protective lane, row 2 = EOD pullback lane) so exactly one run fires per ET day per lane — never a double-send. The mode is derived from ET hour (< 12 ⇒ premarket, else eod) unless overridden. |
| F-144 | **Email delivery + dedup.** Email is sent via the Resend HTTP API (no SMTP); failures surface the HTTP status/reason in the run log (the API key is never logged). The premarket lane also dedups on a content fingerprint so an unchanged action set does not re-email the same day. A `workflow_dispatch` manual trigger exposes `mode` / `force` (bypass guards) / `test_email` (prove Resend→inbox without touching dedup state). |
| F-145 | **Fail-safe posture.** The cron always exits 0 (a failed run never red-flags the workflow into auto-disable) and is **inert without `RESEND_API_KEY`** — absent secrets mean it computes nothing and emails nothing rather than erroring. Secrets live in GitHub repo secrets (same service-role Supabase key class as Streamlit; RLS stays on). |

---

## 4. Non-Functional Requirements

### 4.1 Performance

| ID | Requirement |
|----|-------------|
| NF-01 | Portfolio data (held tickers) must load within 15 seconds on app open |
| NF-02 | Individual stock analysis (load_all) must complete within 10 seconds for a single ticker |
| NF-03 | Market Scanner full run must complete within 30 seconds for the 73-ticker universe |
| NF-04 | Cached data (load_all, sector returns) must be served from cache within 1 second on repeat calls |

### 4.2 Data Freshness

| ID | Requirement |
|----|-------------|
| NF-10 | Live prices refresh every 60 seconds during market hours via Streamlit fragment. During market hours the live-price primary (Finnhub) returns real-time US quotes (vs yfinance's ~15-min delay); when the market is closed all sources report the last regular-session close. |
| NF-10a | The held-position price cross-check is cached 5 minutes (`_cached_price_xcheck`) — a periodic integrity check, not a per-rerun live call — to bound keyed-provider quota usage. |
| NF-11 | load_all() cache TTL: 30 minutes (1800 seconds) |
| NF-12 | Sector ETF returns cache TTL: 60 minutes (3600 seconds) |
| NF-13 | Market indices cache TTL: not cached — fetched on each Daily Briefing load |
| NF-14 | When market is closed, the app must display a context note indicating data reflects last close |
| NF-15 | Pre-market intel cache TTL: 5 minutes (300 seconds); keyed on held tickers + watchlist to ensure correct data after holdings change |
| NF-16 | Risk-free rate (^IRX) cache TTL: 24 hours (86400 seconds); fallback to 4.5% if Yahoo Finance unavailable |
| NF-17 | All date comparisons must use America/New_York (ET) timezone via pytz to prevent midnight UTC rollover producing wrong calendar dates on Streamlit Cloud |

### 4.3 Reliability

| ID | Requirement |
|----|-------------|
| NF-20 | Yahoo Finance API failures must be caught and displayed as warnings, not crash the app |
| NF-21 | Rate-limit (HTTP 429) responses from Yahoo Finance must be retried with linear backoff (3 attempts, 3s base) |
| NF-22 | API health events (success, rate_limit, error, empty) must be recorded via api_health module, per provider (`yahoo_finance`, `finnhub`, `fmp`, `fred`, `supabase`) |
| NF-23 | Supabase connectivity failures must display a user-facing error without exposing credentials |
| NF-24 | No single market-data source is a hard dependency for live prices or the analysis bundle. A provider hard-failure (error, rate-limit after retries, or empty) must fail over to the next configured provider in the chain; the request fails only if every capable provider fails. |
| NF-25 | Keyed-provider errors must never leak the API key — error text surfaced or logged must have the key redacted (e.g. FMP's `?apikey=` in request URLs). |
| NF-26 | The headless alert cron (§3.11) must be **fail-safe and non-blocking**: it always exits 0 (a failure never auto-disables the workflow), is inert when `RESEND_API_KEY` is absent (computes/emails nothing rather than erroring), and uses per-ET-day idempotency guards (Supabase `alert_state`) so a job firing on both DST-straddling UTC slots delivers at most one email per lane per day. |
| NF-27 | The cron and the app must share one data + credential path: `bundle_loader.load_bundle` (extracted from the app's `load_all`) and env-first credential resolution (`SUPABASE_URL`/`SUPABASE_KEY` → `st.secrets`). The cron's Supabase client is a module-global singleton (`db._CLIENT`), not `st.cache_resource`, so it runs without a Streamlit context. |

### 4.4 Security

| ID | Requirement |
|----|-------------|
| NF-30 | All secrets (Supabase URL/key, LLM API keys, market-data keys `FINNHUB_API_KEY` / `FMP_API_KEY`, `[fred] api_key`) must be stored in Streamlit Cloud Secrets, never in code or committed files. A missing market-data key degrades gracefully (provider skipped), never crashes. The headless cron (§3.11) reads the same secrets from **GitHub repo secrets** (incl. `RESEND_API_KEY` / `ALERT_EMAIL_TO` / `ALERT_EMAIL_FROM`); its Supabase key is the same service-role key class as Streamlit (RLS stays on per NF-32). |
| NF-31 | No user authentication required (single-user personal app) |
| NF-32 | Row Level Security is **enabled** on all public-schema tables with `FOR ALL TO service_role` policies. The Streamlit secret `[supabase] key` must be the service-role / secret key (bypasses RLS); the publishable/anon key has no matching policy and is denied. This is defense-in-depth: a leaked publishable key cannot access portfolio data. |

### 4.5 Usability

| ID | Requirement |
|----|-------------|
| NF-40 | All navigation between pages must use session-state-based routing (nav_page) without page reload |
| NF-41 | Analyze buttons must navigate to Stock Analysis and set a Back button to return to the origin page |
| NF-42 | The app must be usable on a standard desktop browser; no mobile-specific layout required |
| NF-43 | Urgent action count must be visible on the Today's Brief tab badge without opening the tab |

### 4.6 Maintainability

| ID | Requirement |
|----|-------------|
| NF-50 | All domain logic (scoring, signals, risk, briefing) must live in the `stock_analyzer/` package, not in `app.py` |
| NF-51 | `app.py` is responsible for UI rendering and orchestration only |
| NF-52 | New database columns must be backward-compatible: `load_trades()` must backfill None for missing columns in older rows |
| NF-53 | Every decision threshold (beta tiers, sector limits, single-name ceiling, composite boundaries, risk-per-trade, imminence windows) must be defined in `stock_analyzer/constants.py`. Features import from this module — they never hardcode threshold values. Changes are policy decisions, not code tuning. |
| NF-54 | Cross-feature coordination must use `st.session_state` caches. One feature publishes; downstream features read and gate. When a producer fails, the cache is set to `None` (not an empty container) so consumers can render an explicit "offline" state rather than silently disabling the gate. |
| NF-55 | Data integrity failures (missing stop, missing composite, missing daily briefing) must surface visibly to the user rather than being masked with fabricated fallback values. |

---

## 5. Out of Scope

- Order execution or brokerage integration
- Multi-user / multi-portfolio support
- Mobile app
- Real-time streaming data (WebSocket feeds)
- International or non-US equity markets
- Options, ETFs, or fixed income instruments
- Automated trading or algorithmic execution
- Day-trading / intraday support (Level 2, order flow, tick charts, momentum-only signals) — counter to the intended persona; see §2B
