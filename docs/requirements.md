# Requirements Document
## Personal Portfolio Intelligence App

**Version:** 1.0  
**Date:** May 2026  
**Status:** Active Development

---

## 1. Purpose and Scope

This application is a personal investment management and intelligence platform built for a retail investor managing a concentrated US equity portfolio. It replaces manual spreadsheet tracking and fragmented news/analysis tools with a single, always-current dashboard that surfaces actionable signals, manages risk, and builds a track record of decisions over time.

The app is not a brokerage or order-execution system. It is a decision-support tool — it gathers, synthesises, and presents information to help the user decide when to buy, hold, add, trim, or exit positions.

---

## 2. User Profile

- **Primary user:** Individual retail investor
- **Portfolio size:** Variable; moderate risk tolerance
- **Experience level:** Active investor, growing familiarity with technical and fundamental analysis
- **Usage pattern:** Daily briefing check before market open; ad-hoc analysis during trading hours; end-of-day journal entries
- **Access:** Web browser via Streamlit Community Cloud; no mobile-specific UI required

---

## 3. Functional Requirements

### 3.1 My Portfolio (Home)

| ID | Requirement |
|----|-------------|
| F-01 | Display all held positions with live price, shares, average cost, market value, P&L ($), P&L (%), portfolio weight (%) |
| F-02 | Auto-refresh live prices every 60 seconds during market hours via a Streamlit fragment |
| F-03 | Show real-time daily P&L (total portfolio $ and %) based on prior-close vs current price |
| F-04 | Detect and prompt for stock splits; adjust shares and average cost accordingly |
| F-05 | Allow user to add, edit, and remove holdings; persist to Supabase `holdings` table |
| F-06 | Display sector allocation pie chart and P&L bar chart |
| F-07 | Show sector exposure breakdown as a percentage of portfolio |
| F-08 | Calculate and display composite signal (Buy/Hold/Sell) for each held position |
| F-09 | Show portfolio-level risk metrics: Sharpe ratio, Sortino ratio, max drawdown, beta, correlation matrix |
| F-10 | Performance attribution: breakdown of return by position and sector |
| F-11 | Earnings calendar: upcoming earnings dates for all held positions with days-to-date |
| F-12 | Risk advisor: flag positions breaching stop-loss, concentration risk, correlation clusters |
| F-13 | Rebalancer: suggest target weights and calculate trades needed to reach them |
| F-14 | Stress test: model portfolio impact under defined macro scenarios (rate shock, recession, etc.) |
| F-15 | Display market-closed context note in sidebar showing last close date when market is shut |

### 3.2 Today's Brief (Daily Briefing)

| ID | Requirement |
|----|-------------|
| F-20 | Show market tone header (bull / bear / flat) based on S&P 500 daily change (≥+0.5% bull, ≤-0.5% bear) |
| F-21 | Display date, S&P 500 %, Nasdaq %, and top 2 leading sectors by 1-week return |
| F-22 | Act Today (right column): prioritised list of urgent actions — stop triggers, sell signals, critical news, macro events |
| F-23 | Grow Today (left column): market-tone-aware growth setups — new positions and add-to-winners on bull days; deferral message on bear days |
| F-24 | Each Grow Today pick includes: ticker, sector, scanner score, thesis one-liner, suggested position size (shares, cost, stop) |
| F-25 | Review Before Close (full width): approaching stops, near-term earnings, weakening large positions |
| F-26 | Buy Candidates (full width): scanner picks cross-referenced across 5 layers with a confidence verdict (Confirmed / Mixed / Conflicted / Caution / Unverified) |
| F-27 | Each Buy Candidate card shows: ticker, sector, score, verdict badge, technical summary, conflicts and agreed signals |
| F-28 | Quick Research panel: user enters any ticker (e.g. from external news), app returns 4-bullet actionable summary with entry timing verdict |
| F-29 | Entry timing verdicts: High Risk — Avoid Chasing (RSI>80 or 1D>15% or 5D>25%), Wait for Pullback, Oversold — Potential Entry, Normal Entry Conditions |
| F-30 | All Analyze buttons navigate to Stock Analysis with a Back button returning to Today's Brief |

### 3.3 Stock Analysis

| ID | Requirement |
|----|-------------|
| F-40 | Analyse any ticker (not limited to held positions) |
| F-41 | Summary scorecard table: price, composite score, signal, position/entry zone, stop, base target, R:R, shares, P&L/cost, earnings |
| F-42 | Scorecard adapts to context: held tickers show actual shares and P&L%; non-held show entry zone and suggested entry cost |
| F-43 | Signal banner shows composite score breakdown (technical × 45% + fundamental × 40% + sentiment × 15%) |
| F-44 | Analyst upside note shown only when it reinforces signal direction (upside on Buy; suppressed on Sell and when downside on Buy) |
| F-45 | Trade Plan tab (Buy/Strong Buy): entry zone, stop loss, R:R, position sizing (shares, cost, max risk) |
| F-46 | Exit Plan tab (Sell/Strong Sell): current price, shares held, position value, P&L if sold now; exit recommendation banner; ATR downside level |
| F-47 | Position Monitor tab (Hold): stop loss, shares held, P&L; guidance to maintain with stop |
| F-48 | Price scenarios chart (Bull / Base / Bear) in all signal modes; sell mode shows position $ impact per scenario |
| F-49 | Chart tab: candlestick with Bollinger Bands, SMA 20/50, RSI panel, optional volume |
| F-50 | Risk tab: Sharpe, Sortino, max drawdown, beta, volatility vs SPY |
| F-51 | Deep Dive tab: fundamental signals, analyst revisions, earnings proximity, news sentiment |
| F-52 | Analysis Summary expander: formatted markdown summary for all analysed tickers |
| F-53 | Source links: Yahoo Finance, Finviz, SEC filings, Yahoo News |

### 3.4 Market Scanner

| ID | Requirement |
|----|-------------|
| F-60 | Scan a universe of ~73 tickers across 12 sectors for technical momentum |
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
| F-82 | Decision Context capture on each trade: signal seen at time of trade, whether signal was followed (yes / no / discretionary), deviation reason, lesson learned |
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
| F-102 | Provide advisor recommendations for watchlist entries |

### 3.9 AI Brief

| ID | Requirement |
|----|-------------|
| F-110 | Generate a natural language portfolio brief using an LLM (Anthropic / OpenAI / Google) |
| F-111 | Brief should summarise portfolio state, key risks, and suggested actions |

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
| NF-10 | Live prices refresh every 60 seconds during market hours via Streamlit fragment |
| NF-11 | load_all() cache TTL: 30 minutes (1800 seconds) |
| NF-12 | Sector ETF returns cache TTL: 60 minutes (3600 seconds) |
| NF-13 | Market indices cache TTL: not cached — fetched on each Daily Briefing load |
| NF-14 | When market is closed, the app must display a context note indicating data reflects last close |

### 4.3 Reliability

| ID | Requirement |
|----|-------------|
| NF-20 | Yahoo Finance API failures must be caught and displayed as warnings, not crash the app |
| NF-21 | Rate-limit (HTTP 429) responses from Yahoo Finance must be retried with linear backoff (3 attempts, 3s base) |
| NF-22 | API health events (success, rate_limit, error, empty) must be recorded via api_health module |
| NF-23 | Supabase connectivity failures must display a user-facing error without exposing credentials |

### 4.4 Security

| ID | Requirement |
|----|-------------|
| NF-30 | All secrets (Supabase URL, Supabase key, LLM API keys) must be stored in Streamlit Cloud Secrets, never in code or committed files |
| NF-31 | No user authentication required (single-user personal app) |
| NF-32 | Row Level Security is disabled on Supabase tables (single-user, no multi-tenancy) |

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

---

## 5. Out of Scope

- Order execution or brokerage integration
- Multi-user / multi-portfolio support
- Mobile app
- Real-time streaming data (WebSocket feeds)
- International or non-US equity markets
- Options, ETFs, or fixed income instruments
- Automated trading or algorithmic execution
