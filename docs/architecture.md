# Architecture Document
## Personal Portfolio Intelligence App

**Version:** 1.0  
**Date:** May 2026  
**Status:** Active Development

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
    ├── data.py                     Data fetching (yfinance wrapper)
    ├── indicators.py               Pure technical indicator calculations
    ├── technicals.py               Technical scoring from indicator output
    ├── fundamentals.py             Fundamental scoring from company info
    ├── sentiment.py                VADER-based news sentiment scoring
    ├── scoring.py                  Composite score weights and recommendation tiers
    ├── portfolio.py                Portfolio DataFrame construction from holdings
    ├── risk.py                     ATR stop loss, position sizing, risk metrics
    ├── targets.py                  Price targets, support/resistance, entry zones
    ├── ranking.py                  Cross-portfolio stock ranking
    ├── scanner.py                  Market scanner (73-ticker universe)
    ├── daily_briefing.py           Daily briefing engine (Act Today / Grow Today / Buy Candidates)
    ├── quick_research.py           Ad-hoc ticker research with entry timing verdict
    ├── news_intelligence.py        News aggregation and attention flagging
    ├── sentiment_velocity.py       Sentiment trend tracking over time
    ├── macro.py                    Macro indicator fetching
    ├── macro_playbook.py           Macro scenario playbook
    ├── macro_calendar.py           Economic calendar events
    ├── earnings_advisor.py         Earnings risk and playbook
    ├── perf_advisor.py             Performance attribution and recommendations
    ├── risk_advisor.py             Risk flags and advisor recommendations
    ├── watchlist_advisor.py        Watchlist analysis and recommendations
    ├── trade_analytics.py          Trade history analytics
    ├── tax_advisor.py              Tax-lot and realised gain/loss analysis
    ├── rebalancer.py               Portfolio rebalancing calculator
    ├── stress_test.py              Macro stress scenario modelling
    ├── split_detector.py           Stock split detection and adjustment
    ├── decision_journal.py         Signal-vs-override pattern analysis
    ├── db.py                       Supabase database operations
    └── api_health.py               API call health event recording
```

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

### 4.2 Daily Briefing (Today's Brief tab)

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
        └── Review Before Close ← approaching stops, earnings, weak large positions

market_context = {
    tone: "bull" | "bear" | "flat",   (S&P ≥+0.5% bull, ≤-0.5% bear)
    sp500_pct: float,
    nasdaq_pct: float,
    leading_sectors: [{sector, return_1w}, ...]
}
```

### 4.3 Signal Cross-Reference (Buy Candidates confidence verdict)

```
For each scanner pick:

Layer 1: Technical  ← scanner score, RSI, trend (always available)
Layer 2: Composite  ← port_df Signal column (held positions only)
Layer 3: News       ← VADER sentiment on recent headlines
Layer 4: Earnings   ← days until earnings date (held positions only)
Layer 5: Revisions  ← analyst upgrades minus downgrades 90d (held positions only)

→ verdict: confirmed | mixed | conflicted | caution | unverified
   (non-held positions are always "unverified" — composite signal not computed)
```

### 4.4 Quick Research Flow

```
User enters ticker → load_all(ticker) [cached 30 min]
        │
        ▼
quick_research.research_ticker(ticker, data)
        │
        ├── move_1d, move_5d, move_1m from Close series
        ├── RSI from df["RSI"] column
        ├── entry timing verdict (_entry_timing)
        └── 4 bullets: signal, momentum, entry timing, key context

Entry Timing Verdict:
  RSI > 80 or 1D > 15% or 5D > 25%  →  High Risk — Avoid Chasing
  RSI > 68 or 1D > 5%  or 5D > 12%  →  Wait for Pullback
  RSI < 35                            →  Oversold — Potential Entry
  else                                →  Normal Entry Conditions
```

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

### 5.3 Technical Score Components (0–100)

| Component | Max Pts | Key Thresholds |
|-----------|---------|----------------|
| RSI (14-period) | 20 | <30 oversold=18; <45=14; <55=10; <70=6; overbought=2 |
| MACD histogram | 20 | Positive & rising=20; positive=14; improving=8; falling=2 |
| Price vs SMA 20/50 | 20 | Price>SMA20>SMA50=20; Price>SMA20=14; Price>SMA50=8 |
| Bollinger Band position | 20 | Below lower=18; lower-mid=14; mid-upper=8; above upper=2 |
| OBV trend | 20 | Rising=20; neutral=10; falling=2 |

### 5.4 Scanner Score Components (0–100)

| Component | Max Pts | Key Thresholds |
|-----------|---------|----------------|
| RSI | 30 | 40–65=30; <40=22; <75=12; else=2 |
| Trend (SMA 20/50) | 35 | Price>SMA20>SMA50=35; Price>SMA20=20; Price>SMA50=10 |
| 1-Month Momentum | 20 | >10%=20; >5%=15; >0%=8; negative=2 |
| 3-Month Momentum | 15 | >15%=15; >5%=10; >0%=5; negative=0 |

---

## 6. Database Schema

**Database:** Supabase (hosted PostgreSQL)  
**RLS:** Disabled on all tables (single-user app)

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
| `_portfolio_value` | My Portfolio | Sidebar display |
| `scanner_results` | Market Scanner | Today's Brief buy candidates |
| `_sidebar_news` | My Portfolio / Stock Analysis | Sidebar news slot |
| `_qr_result` | Today's Brief quick research | Today's Brief (persists result) |

---

## 8. Caching Strategy

```python
@st.cache_data(ttl=1800)   # 30 minutes
def load_all(ticker, period):
    # Fetches history, info, news, earnings, revisions
    # Computes all scores, targets, risk metrics
    # Returns complete analysis dict

@st.cache_data(ttl=3600)   # 60 minutes
def _fetch_sector_returns():
    # Downloads all sector ETFs (batch)
    # Computes 1W/1M/3M/6M returns

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

## 10. External API Dependencies

| API | Purpose | Rate Limits | Failure Handling |
|-----|---------|-------------|-----------------|
| Yahoo Finance (yfinance) | OHLCV prices, company info, news, analyst data, earnings | Informal; 429 responses possible | Retry with linear backoff (3 attempts, 3s base); `api_health` records events |
| Supabase REST API | Holdings, watchlist, trades CRUD | Generous free tier | Connection errors surface as UI warnings |
| Anthropic / OpenAI / Google | AI Brief generation | Per-account | Errors surfaced in AI Brief tab; rest of app unaffected |

Yahoo Finance has no official public API SLA. All yfinance calls are wrapped in `_retry()` in `data.py` to handle transient 429 rate-limit responses.
