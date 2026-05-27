# Architecture Document
## DRISHTA — Beyond Noise
*Personal Portfolio Intelligence App*

**Version:** 1.1  
**Date:** May 2026  
**Status:** Active Development  
**Operating Posture:** Decides, not informs (see §4.0)

---

## 1. Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Runtime | Python | 3.12 | Application language |
| UI Framework | Streamlit | 1.57.0 | Web app rendering and state management |
| Market Data | yfinance | 1.3.0 | OHLCV prices, company info, news, analyst data |
| Data Processing | pandas | ≥2.0.0 | DataFrames, time series, portfolio calculations |
| Charting | Plotly | ≥5.20.0 | Interactive charts (candlestick, bar, pie) |
| Sentiment | vaderSentiment | 3.3.2 | News headline sentiment scoring |
| Database | Supabase (PostgreSQL) | client 2.29.0 | Holdings, watchlist, and trade persistence |
| AI / LLM | Anthropic / OpenAI / Google | Latest | AI Brief generation |
| Timezone | pytz | ≥2024.1 | All time comparisons use America/New_York (ET) |
| Deployment | Streamlit Community Cloud | — | Hosting; secrets injected via dashboard |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User (Browser)                            │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────────┐
│              Streamlit Community Cloud                        │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                      app.py                            │  │
│  │  - Page routing (session_state nav_page)               │  │
│  │  - UI rendering for all pages and tabs                 │  │
│  │  - Orchestrates calls to stock_analyzer package        │  │
│  │  - Caches expensive calls with @st.cache_data          │  │
│  └──────────┬───────────────────────────┬─────────────────┘  │
│             │                           │                     │
│  ┌──────────▼───────────┐   ┌──────────▼───────────────┐    │
│  │  stock_analyzer/     │   │  External APIs            │    │
│  │  Python package      │   │                           │    │
│  │  (domain logic)      │   │  Yahoo Finance (yfinance) │    │
│  └──────────────────────┘   │  Supabase (PostgreSQL)    │    │
│                             │  Anthropic / OpenAI /     │    │
│                             │  Google (LLM)             │    │
│                             └───────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Module Structure

```
python-lab/
├── app.py                          Main application (UI + orchestration)
├── main.py                         Entry point alias
├── requirements.txt                Python dependencies
├── runtime.txt                     Python version (3.12)
├── docs/
│   ├── requirements.md             Functional and non-functional requirements
│   └── architecture.md             This document
└── stock_analyzer/                 Domain logic package
    ├── __init__.py
    ├── constants.py                Single source of truth for all decision thresholds (Phase 2)
    ├── data.py                     Data fetching (yfinance wrapper, risk-free rate)
    ├── indicators.py               Pure technical indicator calculations
    ├── technicals.py               Technical scoring from indicator output
    ├── fundamentals.py             Fundamental scoring — sector-relative benchmarks
    ├── sentiment.py                VADER-based news sentiment scoring
    ├── scoring.py                  Composite score weights and recommendation tiers
    ├── portfolio.py                Portfolio DataFrame construction; stop integrity gate
    ├── risk.py                     ATR stop loss, position sizing, risk metrics
    ├── targets.py                  Price targets, support/resistance, entry zones
    ├── ranking.py                  Cross-portfolio stock ranking (composite score sort)
    ├── scanner.py                  Market scanner (73-ticker universe)
    ├── daily_briefing.py           Daily briefing engine (Act Today / Grow Today / Buy Candidates / Review)
    ├── premarket.py                Pre-market intelligence (futures, global markets, movers)
    ├── quick_research.py           Ad-hoc ticker research with entry timing + portfolio-fit verdict
    ├── news_intelligence.py        News aggregation and attention flagging
    ├── sentiment_velocity.py       Sentiment trend tracking over time
    ├── macro.py                    Macro indicator fetching
    ├── macro_playbook.py           Macro scenario playbook
    ├── macro_calendar.py           Economic calendar events; affected_sectors() helper
    ├── earnings_advisor.py         Earnings risk and playbook
    ├── perf_advisor.py             Performance attribution and recommendations
    ├── risk_advisor.py             Risk flags and advisor recommendations (exact beta impact)
    ├── watchlist_advisor.py        Watchlist analysis with ENTER_NOW portfolio-risk gate
    ├── trade_analytics.py          Trade history analytics
    ├── trades.py                   Trade-record helpers (realised PnL, performance stats)
    ├── tax_advisor.py              Tax-lot analysis; HARVEST subordinated to investment view
    ├── rebalancer.py               Portfolio rebalancing; ADD cross-checks news + risk trim
    ├── stress_test.py              Macro stress scenario modelling
    ├── split_detector.py           Stock split detection and adjustment
    ├── decision_journal.py         Signal-vs-override pattern analysis
    ├── db.py                       Supabase database operations (fractional shares)
    └── api_health.py               API call health event recording
```

---

## 4.0 Operating Posture and Decision Framework

The app is configured to **make decisions, not merely inform**. This is an explicit operating choice (May 2026) that drives the rest of the architecture:

- **Higher bars to recommend.** Each gate is a hard suppression where the threshold fails, not a soft warning. The app would rather recommend nothing than recommend wrongly.
- **Data integrity fails loud.** When a required input is missing (stop price, composite score, daily briefing) the dependent feature surfaces an explicit "offline" state instead of degrading gracefully with fabricated fallbacks.
- **Coordination is mandatory.** Two features that make overlapping decisions never silently contradict each other. One publishes state; the other reads and gates.
- **Subordination of secondary objectives.** Tax outcomes do not override investment view (HARVEST is suppressed on Buy/Strong Buy). Macro events override entry recommendations in affected sectors.

### 4.0.1 Decision constants

All decision thresholds live in `stock_analyzer/constants.py`. Changes to any value are investment-policy decisions, not code tuning.

| Constant | Value | Role |
|---|---|---|
| `PORTFOLIO_BETA_TARGET` | 1.0 | Baseline equity-portfolio target |
| `PORTFOLIO_BETA_ELEVATED` | 1.3 | Soft warn above this |
| `PORTFOLIO_BETA_CEILING` | 1.4 | Hard breach — institutional managed-equity ceiling |
| `TICKER_BETA_HIGH` | 1.5 | Soft warn when added to elevated portfolio |
| `TICKER_BETA_CRITICAL` | 1.8 | Hard breach when added to breached portfolio |
| `SECTOR_CEILING` | 35.0 | Hard cap — no entries when sector at this weight |
| `SECTOR_ELEVATED` | 25.0 | Soft warn — consider half-size |
| `SINGLE_NAME_CEILING` | 15.0 | Hard cap — no add-to-winner above this weight |
| `COMPOSITE_BUY` | 65 | Buy boundary — used for entry AND add-to-winner (aligned) |
| `COMPOSITE_STRONG_BUY` | 75 | Strong Buy boundary |
| `COMPOSITE_HOLD` | 44 | Hold floor; below this = "Sell zone" |
| `RISK_PCT_PER_TRADE` | 0.015 | 1.5% portfolio risk per trade (Moderate) |
| `EARNINGS_IMMINENT_DAYS` | 7 | Trades within this window flagged caution |
| `MACRO_IMMINENT_DAYS` | 3 | Hard suppress new picks in sectors with HIGH-impact macro within this window |

### 4.0.2 Cross-feature coordination caches

Features publish to `st.session_state` when they own a piece of decision state; downstream features read it. When the producer fails, the consumer treats the absence as an "offline" state — not as "no constraint."

| Cache key | Producer | Consumers | Purpose |
|---|---|---|---|
| `_port_risk_cache` | Portfolio page (`compute_portfolio_risk_metrics`) | Stock Analysis Trade Plan, Watchlist `_portfolio_risk_gate` | Beta envelope checks across pages |
| `_risk_high_alerts_cache` | Portfolio page (after `build_risk_advisor_recommendations`) | Watchlist | ENTER_NOW gates against active HIGH risk alerts |
| `_grow_today_sectors_cache` | After `build_daily_briefing` | Watchlist `_portfolio_risk_gate` | Sector-overlap warning when both features pick the same sector |
| `_grow_composites` | Portfolio page (top-5 scanner pre-fetch) | `_grow_today` composite gate | Validates new picks against composite score, not just momentum |
| `_grow_composites_coverage` | Portfolio page | Grow Today UI | "Composite scores unavailable" banner when pre-fetch failed |
| `_daily_brief_offline` | Portfolio page (on `build_daily_briefing` exception) | Watchlist | Surfaces explicit offline state instead of silently disabling gates |

### 4.0.3 Coordination gates currently enforced

| From → to | Gate | Behaviour when fired |
|---|---|---|
| Risk Advisor TRIM → Grow Today add-to-winner | Suppress add on trim-targeted ticker | Amber banner: "Add-to-Winner Suppressed — Risk Advisor Conflict" |
| Risk Advisor TRIM → Rebalancer ADD | Suppress add on trim-targeted ticker | Amber banner: "Rebalance ADD Suppressed — Risk Advisor Conflict" |
| News Intelligence alert → Rebalancer ADD | Attach news_warning; critical drops urgency | Banner inside the add card; critical labelled "Defer Add" |
| Rebalancer drift-trim → Grow Today add-to-winner | Suppress add on drift-overweight ticker | Concentration-blocked banner |
| Single-name ceiling (15%) → Grow Today add-to-winner | Suppress add | Concentration-blocked banner |
| Sector ceiling (35%) → Watchlist ENTER_NOW | Downgrade to NEAR_ENTRY | "Portfolio Fit Blocks Entry" card |
| Imminent macro event → Grow Today new picks | Suppress picks in affected sector | "Picks Suppressed — Imminent HIGH-Impact Macro Event" banner |
| Held position composite Buy → Tax Advisor HARVEST | Suppress; action becomes `HOLD_FOR_SIGNAL` | "Harvest Suppressed — Investment View Holds" banner |
| Grow Today sectors → Watchlist ENTER_NOW | Soft warn on same-sector overlap | Caution text in ENTER_NOW card |

---

## 4. Data Flow

### 4.1 Portfolio Load (on My Portfolio page)

```
Supabase holdings table
        │
        ▼
db.load_holdings() → st.session_state.holdings_df
        │
        ▼
[for each held ticker]
data.fetch_ticker_bundle(ticker)   ← Yahoo Finance (single session per ticker)
        │
        ├── history (OHLCV)  →  technicals.compute_indicators()
        │                              │
        │                              ▼
        │                    technicals.technical_score() → t_score, t_signals
        │
        ├── info (company)  →  data.fetch_financials_from_info()
        │                              │
        │                              ▼
        │                    fundamentals.fundamental_score() → f_score, f_signals
        │
        ├── news             →  sentiment.analyze_news() → avg_sent, headlines
        │                              │
        │                              ▼
        │                    sentiment.sentiment_score_0_100() → s_score
        │
        └── earnings, revisions → stored in result dict
                │
                ▼
        scoring.combined_score(t_score, f_score, s_score) → total (0–100)
        scoring.recommendation(total) → {label, color, icon, rationale}
                │
                ▼
        held_data[ticker] = {df, t_score, f_score, s_score, total, rec,
                             financials, headlines, risk_metrics, targets,
                             entry_lo/hi, stop, earnings, revisions,
                             name, sector}
                │
                ▼
        portfolio.build_portfolio_df(holdings, held_data) → port_df
        st.session_state["_port_df_enriched"] = port_df
```

### 4.2 Pre-Market Intel (Today's Brief tab, 4:00–9:29 AM ET only)

```
premarket.is_premarket()   ← True on weekdays 4:00–9:29 AM ET
        │
        ▼  (if True)
_get_premarket_brief(held_tickers, watchlist)  [cached 5 min]
        │
        ├── fetch_futures()         ← ES=F, NQ=F, YM=F, RTY=F via yfinance fast_info
        │                              % change vs previous_close → tone (bull/bear/flat)
        │
        ├── fetch_global_markets()  ← ^N225, ^HSI, ^GDAXI, ^FTSE, ^FCHI
        │                              5-day history → 1-day overnight % change
        │
        └── fetch_premarket_movers() ← held + watchlist tickers, fast_info last_price
                                       vs previous_close; |chg| >= 0.5% threshold

+ inject today's HIGH/MEDIUM macro events from already-computed _macro_events

→ rendered as: tone banner, futures tiles, global markets, catalysts, movers
```

Tone logic: ES=F ≥ +0.4% → bull; ≤ -0.4% → bear; otherwise flat.
During pre-market the tone banner overrides the regular (market-closed) tone with a forward-looking read.

### 4.3 Daily Briefing (Today's Brief tab)

```
port_df  +  scanner_results  +  news_items  +  held_data
        │
        ▼
daily_briefing.build_daily_briefing(
    port_df, alert_list, risk_recs, news_items,
    held_data, scanner_results, portfolio_value, market_context
)
        │
        ├── Act Today     ← stop triggers, sell signals, critical news, macro events
        ├── Buy Candidates ← scanner picks, each cross-referenced via _cross_reference()
        ├── Grow Today    ← market-tone-aware new picks + add-to-winners
        │       ├── Bull day: score ≥ 65, up to 3 picks, confirmed + unverified allowed
        │       └── Flat day: score ≥ 78, max 1 pick, confirmed picks shown before unverified
        └── Review Before Close ← approaching stops, earnings, weak large positions

market_context = {
    tone: "bull" | "bear" | "flat",   (S&P ≥+0.5% bull, ≤-0.5% bear)
    sp500_pct: float,
    nasdaq_pct: float,
    leading_sectors: [{sector, return_1w}, ...]
}
```

### 4.4 Signal Cross-Reference (Buy Candidates confidence verdict)

```
For each scanner pick:

Layer 1: Technical  ← scanner score, RSI, trend (always available)
Layer 2: Composite  ← port_df Signal column. Held positions ONLY.
                     If composite Signal is empty/missing, verdict is "unverified"
                     (not "confirmed") — data integrity gate added Phase 1 H5.
Layer 3: News       ← VADER sentiment on recent headlines
Layer 4: Earnings   ← days until earnings date. Looked up from a UNION of held_data
                     + pre-fetched composites (`earnings_lookup`), so non-held new
                     picks are also screened. Phase 1 C1 fix — previously this
                     check was skipped silently for any ticker not in held_data.
Layer 5: Revisions  ← analyst upgrades minus downgrades 90d (held positions only)

→ verdict: confirmed | mixed | conflicted | caution | unverified
   (non-held positions are always "unverified" — composite signal not computed)
```

**Verdict UI:** "🔍 Verify — Run Stock Analysis First" badge renders in **amber** (`#f59e0b`) — not blue — so a tired user reads it as "action required" rather than "informational." Same colour applies to the Grow Today conviction tier when composite data is unavailable.

### 4.5 Quick Research Flow

```
User enters ticker → load_all(ticker) [cached 30 min]
        │
        ▼
quick_research.research_ticker(ticker, data, portfolio_ctx)
        │
        ├── move_1d, move_5d, move_1m from Close series
        ├── RSI from df["RSI"] column
        ├── entry timing verdict (_entry_timing)
        ├── 4 bullets: signal, momentum, entry timing, key context
        └── 5th bullet (when portfolio_ctx supplied): portfolio fit
              ├── Act Today flag on THIS ticker      (highest priority)
              ├── Act Today flags on OTHER tickers   (sector under stress)
              │   in the same sector
              ├── Already held position context
              └── New position fit: sector concentration + beta envelope

Entry Timing Verdict (boundaries inclusive — Phase 1 H6 aligned):
  RSI ≥ 80 or 1D ≥ 15% or 5D ≥ 25%  →  High Risk — Avoid Chasing
  RSI ≥ 68 or 1D ≥ 5%  or 5D ≥ 12%  →  Wait for Pullback
  RSI ≤ 35                            →  Oversold — Potential Entry
  else                                →  Normal Entry Conditions
```

`portfolio_ctx` includes `sector_of_ticker`, `sector_weight_pct`, `portfolio_beta`, `ticker_beta`, `act_today_flags`, `sector_act_today`, and the user's holding data — populated at the Daily Briefing call site from the portfolio page state.

---

## 5. Scoring Model

### 5.1 Composite Score Formula

```
composite_score = (technical_score × 0.45)
                + (fundamental_score × 0.40)
                + (sentiment_score × 0.15)
```

All component scores are on a 0–100 scale.

### 5.2 Recommendation Tiers

| Score | Signal | Action |
|-------|--------|--------|
| ≥ 72 | ⬆⬆ Strong Buy | All three dimensions aligned bullish |
| 58–71 | ⬆ Buy | Most signals positive; favourable entry |
| 44–57 | ➡ Hold | Mixed signals; maintain position, no new entry |
| 30–43 | ⬇ Sell | Weakening; consider reducing |
| < 30 | ⬇⬇ Strong Sell | Multiple bearish signals; elevated downside risk |

### 5.3 Fundamental Score — Sector-Relative Benchmarks

P/E, revenue growth, and profit margin thresholds are normalised per sector so high-multiple growth companies (Technology, Communication Services) are not structurally penalised vs value sectors (Utilities, Energy).

| Sector | P/E Cheap | P/E Fair Hi | P/E Expensive | Rev Strong | Rev Healthy | Margin Excel | Margin Good |
|--------|-----------|-------------|---------------|------------|-------------|--------------|-------------|
| Technology | <20 | <45 | ≥65 | >15% | >8% | >20% | >12% |
| Healthcare | <15 | <30 | ≥50 | >12% | >6% | >20% | >10% |
| Financial Services | <10 | <18 | ≥28 | >10% | >5% | >25% | >15% |
| Consumer Cyclical | <14 | <25 | ≥40 | >12% | >6% | >12% | >6% |
| Consumer Defensive | <15 | <24 | ≥35 | >8% | >4% | >10% | >5% |
| Industrials | <13 | <22 | ≥35 | >10% | >5% | >15% | >8% |
| Basic Materials | <10 | <20 | ≥30 | >8% | >4% | >15% | >8% |
| Energy | <10 | <18 | ≥28 | >10% | >5% | >12% | >6% |
| Utilities | <14 | <20 | ≥28 | >5% | >2% | >15% | >8% |
| Communication Services | <15 | <28 | ≥45 | >10% | >5% | >20% | >10% |
| Real Estate | <25 | <45 | ≥70 | >8% | >4% | >30% | >15% |
| Default | <15 | <28 | ≥45 | >15% | >8% | >18% | >10% |

Earnings growth and debt/equity retain universal thresholds; FCF Yield retains universal thresholds.

### 5.4 Technical Score Components (0–100)

| Component | Max Pts | Key Thresholds |
|-----------|---------|----------------|
| RSI (14-period) | 20 | <30 oversold=18; <45=14; <55=10; <70=6; overbought=2 |
| MACD histogram | 20 | Positive & rising=20; positive=14; improving=8; falling=2 |
| Price vs SMA 20/50 | 20 | Price>SMA20>SMA50=20; Price>SMA20=14; Price>SMA50=8 |
| Bollinger Band position | 20 | Below lower=18; lower-mid=14; mid-upper=8; above upper=2 |
| OBV trend | 20 | Rising=20; neutral=10; falling=2 |

### 5.5 Scanner Score Components (0–100)

| Component | Max Pts | Key Thresholds |
|-----------|---------|----------------|
| RSI | 30 | 40–65=30; <40=22; <75=12; else=2 |
| Trend (SMA 20/50) | 35 | Price>SMA20>SMA50=35; Price>SMA20=20; Price>SMA50=10 |
| 1-Month Momentum | 20 | >10%=20; >5%=15; >0%=8; negative=2 |
| 3-Month Momentum | 15 | >15%=15; >5%=10; >0%=5; negative=0 |

---

## 6. Database Schema

**Database:** Supabase (hosted PostgreSQL)  
**RLS:** **Enabled** on all public-schema tables with a single `FOR ALL TO service_role` policy per table. The Streamlit secret `[supabase] key` must be the service-role / secret key (bypasses RLS, server-side only). The anon/publishable key has no matching policy and is therefore denied — defense in depth in case the publishable key ever leaks.

### 6.1 `holdings` table

```sql
CREATE TABLE holdings (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker     TEXT    NOT NULL,
    shares     NUMERIC NOT NULL CHECK (shares > 0),
    avg_cost   NUMERIC NOT NULL CHECK (avg_cost > 0),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### 6.2 `watchlist` table

```sql
CREATE TABLE watchlist (
    id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker   TEXT NOT NULL UNIQUE,
    added_at TIMESTAMPTZ DEFAULT now()
);
```

### 6.3 `trades` table

```sql
CREATE TABLE trades (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker           TEXT    NOT NULL,
    action           TEXT    NOT NULL,           -- 'BUY' | 'SELL'
    shares           NUMERIC NOT NULL CHECK (shares > 0),
    price            NUMERIC NOT NULL CHECK (price > 0),
    cost_basis       NUMERIC,
    realized_pnl     NUMERIC,
    notes            TEXT,
    trigger_type     TEXT DEFAULT 'MANUAL',
    signal_seen      TEXT,                       -- composite signal at time of trade
    followed_signal  TEXT,                       -- 'yes' | 'no' | 'discretionary'
    deviation_reason TEXT,                       -- reason if signal not followed
    lesson           TEXT,                       -- lesson learned (post-trade)
    traded_at        TIMESTAMPTZ DEFAULT now()
);
```

The `signal_seen`, `followed_signal`, `deviation_reason`, and `lesson` columns were added after initial deployment. The `db.load_trades()` function backfills `None` for these columns in older rows to maintain backward compatibility.

---

## 7. Navigation and State Management

The app uses a single-page architecture with session-state-based routing. There is no URL routing.

```python
# Current page
st.session_state["nav_page"]  # e.g. "🏠 My Portfolio"

# Two-run navigation (prevents widget-ownership conflicts):
st.session_state["_pending_page"]   # set by buttons; consumed at top of next run
st.session_state["_nav_origin"]     # saved when navigating TO Stock Analysis
                                    # enables Back button
```

**Navigation pattern for Analyze buttons:**
1. Button sets `_pending_page = "📈 Stock Analysis"` and `_analysis_ticker = ticker`
2. Top of next run: consumes `_pending_page`, saves current page to `_nav_origin`, sets `nav_page`
3. Stock Analysis reads `_nav_origin` and renders Back button
4. Back button sets `_pending_page = _nav_origin`, clears `_nav_origin`

**Cross-page data sharing via session state:**

| Key | Set by | Read by |
|-----|--------|---------|
| `_port_df_enriched` | My Portfolio | Stock Analysis, Today's Brief |
| `_live_prices` | Price strip fragment | Portfolio P&L table |
| `_last_port_df` | My Portfolio | Trade Journal decision context |
| `_signals_computed_at` | My Portfolio (after port_df build) | Portfolio table caption, Trade Journal signal pre-fill help |
| `_portfolio_value` | My Portfolio | Sidebar display |
| `scanner_results` | Market Scanner | Today's Brief buy candidates |
| `_sidebar_news` | My Portfolio / Stock Analysis | Sidebar news slot |
| `_qr_result` | Today's Brief quick research | Today's Brief (persists result) |

**Decision-coordination caches (see §4.0.2 for the gates that consume each):**

| Key | Set by | Read by |
|-----|--------|---------|
| `_port_risk_cache` | My Portfolio | Stock Analysis Trade Plan, Watchlist |
| `_risk_high_alerts_cache` | My Portfolio | Watchlist |
| `_grow_today_sectors_cache` | After `build_daily_briefing` | Watchlist |
| `_grow_composites` | My Portfolio (top-5 scanner pre-fetch) | Daily Briefing `_grow_today` |
| `_grow_composites_coverage` | My Portfolio (post-fetch) | Grow Today banner |
| `_daily_brief_offline` | My Portfolio (on briefing exception) | Watchlist offline banner |
| `_tj_prefill` | Watchlist "Log Planned Trade" | Trade Journal form prefill |

---

## 8. Caching Strategy

```python
@st.cache_data(ttl=300)    # 5 minutes
def _get_premarket_brief(held_tickers, watchlist):
    # Fetches US futures (ES=F, NQ=F, YM=F, RTY=F) via fast_info
    # Fetches global index overnight moves (5-day history → 1-day return)
    # Fetches pre-market movers for held + watchlist tickers
    # Only called when is_premarket() is True (4:00–9:29 AM ET weekdays)

@st.cache_data(ttl=1800)   # 30 minutes
def load_all(ticker, period):
    # Fetches history, info, news, earnings, revisions
    # Computes all scores, targets, risk metrics
    # Returns complete analysis dict
    # Uses live risk-free rate from _get_rfr() for Sharpe/Sortino

@st.cache_data(ttl=3600)   # 60 minutes
def _fetch_sector_returns():
    # Downloads all sector ETFs (batch)
    # Computes 1W/1M/3M/6M returns

@st.cache_data(ttl=86400)  # 24 hours
def _get_rfr():
    # Fetches 13-week T-bill rate (^IRX) as annual decimal
    # Fallback: 0.045 (4.5%) if Yahoo Finance unavailable
    # Used for Sharpe and Sortino calculations across all risk functions

# Not cached (always fresh):
fetch_market_indices()      # Called on Daily Briefing load
fetch_live_prices()         # Called by 60s auto-refresh fragment
```

---

## 9. Deployment

| Attribute | Detail |
|-----------|--------|
| Platform | Streamlit Community Cloud |
| Repository | GitHub (public or private) |
| Branch | `main` |
| Entry point | `app.py` |
| Python version | 3.12 (declared in `runtime.txt`) |
| Dependencies | `requirements.txt` |
| Secrets management | Streamlit Cloud Secrets dashboard (`.streamlit/secrets.toml` format) |

### 9.1 Required Secrets

```toml
SUPABASE_URL    = "https://xxxxxxxxxxxx.supabase.co"
SUPABASE_KEY    = "eyJ..."          # anon/service role key
ANTHROPIC_API_KEY = "sk-ant-..."    # for AI Brief
# Optional additional LLM keys:
OPENAI_API_KEY  = "sk-..."
GOOGLE_API_KEY  = "AIza..."
```

All secrets are accessed via `st.secrets["KEY_NAME"]` in the application code. They are never committed to the repository.

### 9.2 Deployment Process

1. Push changes to `main` branch on GitHub
2. Streamlit Community Cloud detects the push and automatically redeploys
3. Typical redeploy time: 1–3 minutes
4. No CI/CD pipeline; manual testing before push is the quality gate

---

## 10. Known Behaviours and Design Decisions

| Area | Behaviour | Rationale |
|------|-----------|-----------|
| Signal staleness | Portfolio table shows caption with signal load time (HH:MM). Signals do not update between page refreshes even though live prices update every 60s. | Recomputing all signals on every price tick would hit Yahoo Finance rate limits and degrade performance. |
| Briefing date (ET) | All dates in Today's Brief use `_TODAY_ET` (America/New_York) not `datetime.now()`. When market is closed, header appends "data as of Fri May 09" to clarify the data source. | Streamlit Cloud runs on UTC servers; bare `datetime.now()` flips to the next calendar day after ~8 PM ET. |
| Pre-market previous close | `fetch_premarket_movers()` prefers the known close from `held_data` history for the baseline. When `held_data` is empty (cached call), it falls back to `fast_info.previous_close`. | The cached pre-market fetch cannot accept non-hashable `held_data` as a parameter, so it uses fast_info as fallback. |
| RSI in strong uptrends | When avg_loss EWM = 0 (no losing periods in window), RSI is set to 100.0 (if any gains) or 50.0 (flat). | Standard division by zero would produce NaN, which downstream signal logic treats as neutral — incorrectly suppressing strong Buy signals. |
| Sortino in strong uptrends | When no negative excess-return days exist, Sortino returns 99.0 (not 0.0). | An empty downside series has std = NaN; treating that as 0.0 was showing worst-case Sortino for the best-performing stocks. |
| Fractional shares | `db.load_holdings()` converts the `shares` column to `float` (not `int`). | Brokers increasingly support fractional shares; `astype(int)` was silently truncating e.g. 12.5 → 12. |
| Earnings + conflict verdict | The earnings priority check runs before composite/sentiment checks. A near-earnings stock with any other conflicting signal escalates to "Conflicted" (red), not just "Caution" (amber). | Holding through earnings with mixed signals is higher risk than either condition alone. |
| Entry zone (Grow Today) | `_suggest_size()` returns `entry_lo` (40% of stop-distance below price) and `entry_hi` (15% of stop-distance above price) as the actionable entry range. | A single "@ ~$X" price point implied precision that doesn't exist; a zone is more honest and practical. |
| Position Monitor re-check | When signal is Hold for a held position, the info box shows a specific 7-day re-check date computed from `date.today() + 7`. Two triggers are given: add-on if score ≥ `COMPOSITE_BUY`; exit if price closes below stop. | "Mixed signals — check back later" gives no actionable timeline. Specific dates and conditions prevent analysis paralysis. |
| Rankings sort order | `ranking.py` sorts by Composite Score descending, Universe Rank as tiebreaker. | Sorting by Universe Rank ascending promoted lower-scoring stocks that happened to have a low ordinal rank. |
| Beta recommendation | `risk_advisor.py` names the specific highest-beta ticker and computes the exact new portfolio beta using `(beta - w*b*f) / (1 - w*f)` where f = 50% sell fraction. Explicit `if/else` guards against `w*f → 1` (Phase 1 H2). | A generic "consider trimming high-beta names" gives no concrete action. Users need to know which ticker and what the outcome will be. |
| Stop data integrity | `portfolio.py` returns `Stop=None`, `Stop Type="Stop Unavailable"`, `Gap to Stop=None` when the upstream stop is missing or zero. Downstream consumers (Act Today SELL trigger, earnings advisor, alert builder, drill-down metrics, dataframe styler) all guard for None and surface "—" or a "stop unavailable" caption instead of fabricating a fallback. | Phase 1 C2. Silently substituting a fabricated 8% buffer let mechanical SELL rules fire on a number nobody chose. Fail loudly. |
| Earnings risk for new picks | `_cross_reference` reads earnings from a UNION of `held_data + grow_composites` via `earnings_lookup`. Both held positions and new scanner picks are screened. | Phase 1 C1. Previously the earnings check ran only for held tickers, so a brand-new pick with earnings tomorrow could be marked "Confirmed." |
| Composite gate | Grow Today new picks AND add-to-winner both require composite ≥ `COMPOSITE_BUY` (65). When composite pre-fetch failed for any of the top picks, an amber "Composite Scores Unavailable" banner is rendered above Grow Today so the user knows the gate didn't run for those tickers. | Phase 1 H3 + Phase 2. Asymmetric bars (65 new vs 68 add) were backwards from "press your winners." Silent gate bypass on fetch failure was a real risk. |
| ENTER_NOW R:R requirement | Watchlist `ENTER_NOW` requires `rr is not None and rr >= 2.0`. Tickers without a validated R:R fall through to `NEAR_ENTRY`. | Phase 1 H4. "Unknown R:R" is incomplete homework, not a green light. |
| Confirmed verdict guard | `_cross_reference` will NOT issue "Confirmed — All Signals Aligned" for a held position whose composite Signal is empty. `composite_available` becomes False; verdict routes to "🔍 Verify — Composite Signal Missing" (amber). | Phase 1 H5. Previously an empty signal silently fell through to the agreed list, producing a green light on missing data. |
| Single-name ceiling | Grow Today and Buy Candidates suppress add-to-winner when the position is at or above `SINGLE_NAME_CEILING` (15%). A concentration banner explains the suppression. | Phase 2. Institutional standard. Concentration risk overrides signal strength. |
| Tax HARVEST subordination | `tax_advisor.py` returns `HOLD_FOR_SIGNAL` (not `HARVEST`) when the position is rated Buy or Strong Buy. The UI renders a "Harvest Suppressed — Investment View Holds" banner with the conflicting positions. | Phase 2. Tax tail does not wag investment dog. Exiting a Buy-rated position to capture a tax loss trades known savings for unknown opportunity cost. |
| Macro gate on new picks | `_grow_today` accepts `macro_events` and hard-suppresses new picks in any sector with a HIGH-impact macro event within `MACRO_IMMINENT_DAYS` (3 days). `macro_calendar.affected_sectors(category)` resolves which sectors are in scope. | Phase 2. Opening fresh positions into a known binary catalyst (FOMC, CPI) is the institutional anti-pattern this gate prevents. |
| Daily Briefing offline state | When `build_daily_briefing()` raises, the Portfolio page sets `_grow_today_sectors_cache = None` and `_daily_brief_offline = True`. The Watchlist page detects this and shows an explicit warning: "Daily Briefing offline — sector-overlap and active-risk-alert gates cannot run." | Phase 2. Silent gate disable on producer failure was a real risk. |
| Stock Analysis without Portfolio context | The Trade Plan beta-envelope warning depends on `_port_risk_cache`. When the cache is empty (user landed on Stock Analysis without first visiting Portfolio), a prominent "Portfolio context unavailable" info note renders above the Trade Plan. | Phase 2. Don't pretend the gate is active when it isn't. |
| Entry-timing thresholds | `quick_research.py` boundaries use `>=` for upper bounds and `<=` for lower bounds (e.g. `move_1d >= 15` triggers "Avoid Chasing"). Previously strict `>` produced unintuitive cliffs where exactly-15% one-day moves slipped past the gate. | Phase 1 H6. Standard TA convention. |
| Decision constants | All threshold values used to gate, suppress, or downgrade a recommendation live in `stock_analyzer/constants.py`. Features import from this module rather than hardcoding values. | Phase 2. Single source of truth; changes here are policy decisions, not code tuning. |

---

## 11. External API Dependencies

| API | Purpose | Rate Limits | Failure Handling |
|-----|---------|-------------|-----------------|
| Yahoo Finance (yfinance) | OHLCV prices, company info, news, analyst data, earnings, futures, global indices | Informal; 429 responses possible | Retry with linear backoff (3 attempts, 3s base); `api_health` records events; pre-market failures caught and shown as caption |
| Supabase REST API | Holdings, watchlist, trades CRUD | Generous free tier | Connection errors surface as UI warnings |
| Anthropic / OpenAI / Google | AI Brief generation | Per-account | Errors surfaced in AI Brief tab; rest of app unaffected |
| US Treasury / Yahoo `^IRX` | 13-week T-bill rate for risk-free rate | Daily cached | Falls back to 4.5% if unavailable |

Yahoo Finance has no official public API SLA. All yfinance calls are wrapped in `_retry()` in `data.py` to handle transient 429 rate-limit responses. Pre-market `fast_info` calls in `premarket.py` are not retried (best-effort; panel silently omits unavailable tickers).
