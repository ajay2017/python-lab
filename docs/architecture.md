# Architecture Document
## DRISHTA — Beyond Noise
*Personal Portfolio Intelligence App*

**Version:** 2.0  
**Date:** June 2026  
**Status:** Active Development  
**Operating Posture:** Decides, not informs (see §4.0)

---

## 1. Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Runtime | Python | 3.12 | Application language |
| UI Framework | Streamlit | 1.57.0 | Web app rendering and state management |
| UI Framework (serialization) | pyarrow | 24.0.0 | Arrow serialization for Streamlit's `st.dataframe` / `st.data_editor` rendering — pinned (2026-07-12) after an unbounded transitive pyarrow version caused a production SIGSEGV (streamlit's own dependency spec leaves pyarrow unbounded by design) |
| Market Data (primary, history/bundle) | yfinance | 1.3.0 | OHLCV history, company info, news, analyst data |
| Market Data (real-time quotes) | Finnhub (REST, free tier) | — | Real-time US live prices — **primary for the live-price field**; price cross-check |
| Market Data (failover) | FMP / Financial Modeling Prep (REST, free tier) | `/stable/` | Failover for live prices, history, and the full analysis bundle |
| HTTP client | requests | ≥2.28.0 | Keyed REST calls to Finnhub / FMP / FRED |
| Data Processing | pandas | ≥2.0.0 | DataFrames, time series, portfolio calculations |
| Charting | Plotly | ≥5.20.0 | Interactive charts (candlestick, bar, pie) |
| Sentiment | vaderSentiment | 3.3.2 | News headline sentiment scoring |
| Database | Supabase (PostgreSQL) | client 2.29.0 | Holdings, watchlist, and trade persistence |
| AI / LLM | Anthropic / OpenAI / Google | Latest | AI Snapshot generation (Home section) + AI Insights (Anthropic-only) |
| Timezone | pytz | ≥2024.1 | All time comparisons use America/New_York (ET) |
| Deployment | Railway Hobby (primary, since 2026-08-15) + Streamlit Community Cloud (dormant fallback) | — | Hosting; both auto-deploy from `main` against the same Supabase DB (§9) |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User (Browser)                            │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────────┐
│   Railway Hobby (primary)  /  Streamlit Cloud (fallback)      │
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
├── cron_runner.py                  Headless cron entry point (GitHub Actions) — dispatch modes: auto / scan / thesis / debrief / monthly / eod
├── requirements.txt                Python dependencies
├── runtime.txt                     Python version (3.12)
├── docs/
│   ├── requirements.md             Functional and non-functional requirements
│   └── architecture.md             This document
└── stock_analyzer/                 Domain logic package
    ├── __init__.py
    ├── constants.py                Single source of truth for all decision thresholds (Phase 2)
    ├── data.py                     Public market-data API (fetch_* + crosscheck_*); routes through the
    │                               provider layer when DATA_MULTISOURCE_ENABLED, else single-source yfinance
    ├── providers/                  Multi-source market-data layer (failover + price cross-check)
    │   ├── base.py                 DataProvider abstraction, capability flags, canonical schemas
    │   ├── yfinance_provider.py    yfinance adapter (history/bundle/indices/risk-free; live-price failover)
    │   ├── finnhub_provider.py     Finnhub adapter — real-time live prices (live-price PRIMARY) + fetch_news_sentiment (F-74 news-sentiment read)
    │   ├── fmp_provider.py         FMP adapter — live prices + history + full bundle (failover)
    │   ├── orchestrator.py         Failover chains (per data type) + price cross-check; PROVIDER_REGISTRY consumer
    │   ├── _util.py                Secret reader (st.secrets→env, section-nesting tolerant) + http helper
    │   └── selftest.py             Offline provider smoke-test (env-var keys)
    ├── indicators.py               Pure technical indicator calculations
    ├── technicals.py               Technical scoring from indicator output
    ├── fundamentals.py             Fundamental scoring — sector-relative benchmarks
    ├── catalyst_watch.py           Catalyst Watch — forward earnings awareness (pure logic)
    ├── sentiment.py                VADER-based news sentiment scoring
    ├── news_sentiment.py           Finnhub news-sentiment awareness — bullish%/buzz/vs-sector → label + held-position shift alert (pure wrapper over FinnhubProvider.fetch_news_sentiment; F-74)
    ├── scoring.py                  Composite score weights and recommendation tiers
    ├── signal_reconciliation.py    Central authority resolving scanner vs. composite vs. context into one buy/skip verdict (reconcile_signals) — every recommendation surface calls it. Also exposes classify_composite_direction() (F-202, D3): a public wrapper over the private _composite_class(), for callers on held positions where Score/Signal IS the composite (no separate momentum score to reconcile against)
    ├── portfolio.py                Portfolio DataFrame construction; stop integrity gate
    ├── account.py                  Account-level pure calc (net contributed capital, growth, money-weighted/Modified-Dietz return); signed net cash nets margin
    ├── daily_pnl.py                Positions-scope day-over-day P&L (Tier B): broker-style equity-delta vs persisted daily_snapshots baseline + the day's trades
    ├── risk.py                     ATR stop loss, position sizing, risk metrics
    ├── targets.py                  Price targets, support/resistance, entry zones
    ├── ranking.py                  Cross-portfolio stock ranking (composite score sort)
    ├── comparison.py               2-ticker side-by-side comparison engine + one-line verdict (Compare page)
    ├── scanner.py                  Market scanner (curated ~73-ticker universe + Watchlist extension); scan_movers() 1-day-gainer pass
    ├── discovery_universe.py       Broad ~200-name discovery universe (by sector) for movers; discovery_tickers() flatten/dedup
    ├── daily_briefing.py           Daily briefing engine (Act Today / Grow Today + Movers / Buy Candidates / Review); structured directives + per-ticker consolidation
    ├── decision_bucket.py          Brief defensive-item bucketing: Act Today vs Monitoring/Awareness split (calm-advisor, pure)
    ├── position_lifecycle.py       Held-position state (settling → established → winning; at_risk/exit override) — calm-advisor nudge-cadence gate
    ├── signal_hysteresis.py        Calm-advisor "steady vs yesterday" damper — annotate-only continuity marker (Tier 2C)
    ├── evening_debrief.py          Evening Debrief: PM companion to Today's Brief (plan-vs-reality, today's trades, tomorrow's setup)
    ├── premarket.py                Pre-market intelligence (futures, global markets, movers)
    ├── premarket_stance.py         Pre-Market Stance: AI narrative + Defensive/Neutral/Constructive verdict for the open (cached per day)
    ├── quick_research.py           Ad-hoc ticker research with entry timing + portfolio-fit verdict
    ├── news_intelligence.py        News aggregation and attention flagging
    ├── sentiment_velocity.py       Sentiment trend tracking over time
    ├── macro.py                    Macro indicator fetching
    ├── macro_playbook.py           Macro scenario playbook
    ├── macro_calendar.py           Economic calendar events; affected_sectors() helper
    ├── earnings_advisor.py         Earnings risk and playbook
    ├── perf_advisor.py             Performance attribution and recommendations
    ├── risk_advisor.py             Risk flags and advisor recommendations (exact beta impact)
    ├── exit_advisor.py             Exit-discipline + market-risk: deterioration WATCH/TRIM/EXIT ladder, risk-off de-risk overlay, Market-Risk Posture dial (classify_deterioration_tier · risk_off_regime · assess_risk_off_derisk · market_risk_posture — pure logic); also compute_relative_strength() (20-session RS vs SPY, shared by Thesis Red Team + Multi-Agent Debate)
    ├── debate_agent.py             Multi-Agent Debate Agent (F-197): build_entry_corpus (Phase 1) + build_exit_corpus (Phase 2, D2) + run_debate — 5-Haiku sequential Bull/Bear/Judge debate; entry mode on Grow Today candidates, exit mode ("Challenge This Exit") on deterioration TRIM/EXIT cards; round prompts branch on debate_type; debate_cache table; awareness-only, never gates
    ├── structural_scanner.py       Structural Vulnerability Scanner Phase 1 (F-198): blast_radius() (single-factor beta cascade estimate, reuses portfolio_intelligence.py's risk_budget/correlation_clusters as inputs) + generate_structural_narrative() (1 Haiku call/day); structural_scan_cache table; awareness-only, never gates
    ├── thesis_cluster.py           Hidden Same-Bet Detector (F-199, D1): build_thesis_corpus() (held tickers' saved BUY theses) + generate_thesis_clusters() (1 Haiku call/day, two-layer fabrication guard — ticker + evidence-quote verification) + classify_clusters() (pure Python, zero LLM, vs portfolio_intelligence.correlation_clusters() — unverified/possible/confirmed); thesis_cluster_cache table; awareness-only, never gates
    ├── missed_opportunity.py       Missed-Opportunity Pattern (F-201, O1): build_missed_opportunity_corpus() (recommendations_history.distinct_missed() enriched with sector/price_band/composite_band) + generate_missed_opportunity_patterns() (1 Haiku call/day, two-layer guard — ticker + predicate verification against closed categorical fields) + pattern_outcome_mix() (pure Python, win/loss/flat mix per pattern); missed_opportunity_cache table; descriptive not causal, awareness-only, never gates
    ├── regime_stress.py            Regime-Aware Adversarial Stress Testing Phase 1 (F-200): build_regime_scenario_inputs + generate_regime_scenario — composes structural_scanner.blast_radius() + macro_calendar's FRED regime detector + cross_asset's USD signal into 1 Haiku call/day naming the compound scenario most likely to hurt this portfolio; regime_scenario_cache table; zero new quant modeling; awareness-only, never gates
    ├── concentration.py            Concentration & sizing discipline: single-name ceiling enforcement + high-beta cluster awareness (pure logic)
    ├── cross_asset.py              Cross-Asset Pulse — 5-signal macro stress (credit/VIX-term/dollar/copper/3m10y → 0–5 stress score; awareness-only, Risk tab + Brief one-liner; F-09c)
    ├── watchlist_advisor.py        Watchlist analysis with ENTER_NOW portfolio-risk gate
    ├── trade_analytics.py          Trade history analytics
    ├── trade_review.py             Trade Review: behavioural retrospective (app-followed vs deviated trades, panic-day reactivity, per-trade outcome vs SPY)
    ├── trades.py                   Trade-record helpers (realised PnL, performance stats)
    ├── tax_advisor.py              Tax-lot analysis; HARVEST subordinated to investment view
    ├── rebalancer.py               Portfolio rebalancing; ADD cross-checks news + risk trim
    ├── stress_test.py              Macro stress scenario modelling
    ├── attribution_readiness.py    E2 alpha-attribution data-readiness audit (F-247): distinct-snapshot-date coverage vs NYSE sessions, gaps, concentration, turnover — measurement only, no thresholds
    ├── forward_sim.py              Forward Portfolio Simulator (F-245): replays the app's OWN rules (ratcheted stop, deterioration ladder, risk-off overlay) against a shocked book — read-only diagnostic
    ├── split_detector.py           Stock split detection and adjustment
    ├── decision_journal.py         Signal-vs-override pattern analysis
    ├── broker_import.py            Robinhood statement import (F-87): parse_robinhood_csv (pure — normalises Buy/Sell, surfaces invalid rows) + classify_against_existing (content-match dedup: exact same-day + date-agnostic, so hand-logged trades don't double-count); UI in Trade Journal reuses save_trade/recalculate_from_trades
    ├── recommendations_history.py  Retrospective scorecard (rule-based, no LLM): acted/missed outcomes graded on alpha, by-band/by-verdict rollups, distinct-ticker signal_flow + report_viz_snapshot (drives 📜 Recommendations History + the F-4 monthly visuals)
    ├── thesis_advisor.py           AI Intelligence F-1 review (per-holding thesis → INTACT/WEAKENING/BROKEN, thesis_reviews table; F-154a Phase 2: also ingests saved analyst_coverage as citable CONTEXT — never upgrades a verdict) + F-5 authoring (draft_thesis: editable candidate thesis at BUY → trades.user_thesis / thesis_source)
    ├── debrief_advisor.py          AI Intelligence F-3: weekly portfolio debrief — 4-section narrative + Sunday email (weekly_debriefs table)
    ├── intelligence_report.py      AI Intelligence F-4: monthly retrospective — Q0 entry-quality + Q1 signal-discipline; build_report_package + frozen viz_json snapshot (monthly_reports table)
    ├── analyst_intel.py            Analyst Coverage F-6/F-154: extract_report (paste → list[dict], one record PER covered stock — multi-stock roundups never merge; Sonnet, offline→None) + derive_consensus (pure-Python avg/high/low PT + consensus label); awareness-only, analyst_coverage table
    ├── ticker_history.py           Prior Trades F-237: per-ticker round-trip reconstruction from the trade journal — build_ticker_history (0→held→0 episodes, weighted-avg cost mirroring db.recalculate_from_trades incl. SPLIT-overwrites-state — with one deliberate divergence: a SPLIT with NO open episode is skipped rather than seeding state, since there's no round trip to attribute it to, so a later SELL becomes an orphan_sell here where db computes a basis. A SPLIT INSIDE an open episode rescales that episode's legs onto the post-split basis (shares×f, price÷f) or realized_pct is wrong by the split factor; each SELL's dollar P&L is frozen at append time so a recomputed leg isn't rescaled with the per-share figures. Realized P&L summed from the STORED SELL realized_pnl so it can't disagree with Portfolio; orphan-SELL / split-in-window / oversell warnings; None on offline vs [] on never-traded — but note db.load_trades returns an empty DataFrame on read failure, so the None sentinel is unreachable through that producer and the consumer must treat an empty journal as indeterminate) + build_pnl_series (per-episode P&L% vs running basis + "had you held" ghost after the last exit); pure, awareness-only, never gates
    ├── bundle_loader.py            Shared market-data bundle loader (load_all) — the app AND the headless cron load through the SAME path
    ├── headless_alert_engine.py    Headless alert computation for the cron: protective signals (stops / EXIT / risk-off), reactive pullback, EOD snapshot
    ├── notify.py                   Email rendering (weekly debrief / monthly intelligence / pullback / protective) + Resend delivery
    ├── db.py                       Supabase ops (holdings/watchlist/trades/manual_stops); trade-replay; fractional shares
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
| `SHARPE_HIGH_RISK_MAX` | 0.4 | Risk Advisor: portfolio Sharpe below this → HIGH priority ("risk not rewarded") |
| `SHARPE_MEDIUM_RISK_MAX` | 0.8 | Risk Advisor: portfolio Sharpe below this → an action recommendation fires (HIGH or MEDIUM per `SHARPE_HIGH_RISK_MAX`) |
| `SHARPE_STRONG_MIN` | 1.0 | Risk Advisor: portfolio Sharpe at/above this → OK card ("strong risk-adjusted returns"). `[SHARPE_MEDIUM_RISK_MAX, SHARPE_STRONG_MIN)` is a deliberate dead zone — no card either way. Extracted 2026-07-28 from previously-inline 0.4/0.8/1.0 literals in `risk_advisor.py` (a hard-rule-#1 gap flagged during the `risk.py` Sharpe/Sortino Opus review). |
| `SHARPE_DRAG_RELATIVE_MAX` | 0.7 | Risk Advisor: selection-only (doesn't gate whether the Sharpe rec fires) — a ticker is named a "Sharpe drag" contributor in `root_cause` when its own Sharpe is below `portfolio_sharpe × this`. Extracted 2026-07-28, same hard-rule-#1 sweep as the Sharpe ladder above. |
| `SHARPE_DRAG_MIN_WEIGHT_PCT` | 3.0 | Risk Advisor: minimum position weight (%) to be named a Sharpe-drag contributor — too small a position isn't worth naming. Extracted 2026-07-28, same sweep. |
| `PORTFOLIO_VOL_HIGH_PCT` | 30.0 | Risk Advisor: portfolio annualised volatility above this → HIGH priority. No "OK" sub-type for volatility — below `PORTFOLIO_VOL_MEDIUM_PCT` is just silence. Extracted 2026-07-28, same sweep. |
| `PORTFOLIO_VOL_MEDIUM_PCT` | 25.0 | Risk Advisor: portfolio annualised volatility above this → MEDIUM priority. Extracted 2026-07-28, same sweep. |
| `PORTFOLIO_DRAWDOWN_ACTION_MAX` | -20.0 | Risk Advisor: portfolio max drawdown below (more negative than) this → an action fires. Extracted 2026-07-28, same sweep. |
| `PORTFOLIO_DRAWDOWN_HIGH_MAX` | -30.0 | Risk Advisor: portfolio max drawdown below this → HIGH priority (else MEDIUM). Extracted 2026-07-28, same sweep. |
| `PORTFOLIO_DRAWDOWN_OK_MIN` | -10.0 | Risk Advisor: portfolio max drawdown above this → OK card. `(PORTFOLIO_DRAWDOWN_HIGH_MAX, PORTFOLIO_DRAWDOWN_OK_MIN)` — i.e. -20% to -10% — is a deliberate dead zone. Extracted 2026-07-28, same sweep. |
| `DRAWDOWN_CONTRIB_MAX` | -15.0 | Risk Advisor: selection-only — a per-ticker max drawdown below this is named a drawdown contributor in `root_cause`; doesn't gate whether the portfolio-level rec fires. Extracted 2026-07-28, same sweep. |
| `TAIL_RATIO_ACTION_MIN` | 1.7 | Risk Advisor: CVaR/VaR tail ratio above this → an action fires. No "OK" sub-type for tail risk. Extracted 2026-07-28, same sweep. |
| `TAIL_RATIO_HIGH_MIN` | 2.2 | Risk Advisor: CVaR/VaR tail ratio above this → HIGH priority (else MEDIUM). Extracted 2026-07-28, same sweep. |
| `DEFENSIVE_DIVERSIFIER_MIN_PCT` | 8.0 | Risk Advisor: display-only — lower bound of the suggested defensive-sector (Healthcare/Staples/Utilities) allocation range quoted in both the beta rec and the volatility rec. Added 2026-08-04 UX audit (I11) so the two recs can't quote different ranges for the same underlying action. |
| `DEFENSIVE_DIVERSIFIER_MAX_PCT` | 10.0 | Risk Advisor: display-only — upper bound of the same range. Set to 10 (not the volatility rec's prior 12) to match that rec's own follow-up math, which already assumes a 10% allocation. |
| `REGIME_BETA_CEILING` | dict: rate_cut=1.25, neutral=1.10, inflation_fight=1.00, recession_fear=0.90, stagflation_risk=0.85 | **Concept D (Wave 3).** Regime-conditional beta target shown on the 🔗 Risk Analysis "Regime Fit" diagnostic. Keyed by the 5 real regime ids from `macro_calendar.detect_macro_regime`. Anchored to (tighter/looser than) the regime-agnostic `PORTFOLIO_BETA_*` baseline above. Diagnostic only — never gates/resizes. |
| `REGIME_CASH_FLOOR_PCT` | dict: rate_cut=5.0, neutral=5.0, inflation_fight=10.0, recession_fear=15.0, stagflation_risk=20.0 | **Concept D (Wave 3).** Regime-conditional cash-cushion floor shown alongside the beta read on the same diagnostic. Same regime keys as `REGIME_BETA_CEILING`. Diagnostic only. |
| `REGIME_CONFIDENCE_MIN_DISPLAY` | 40 | Below this regime-detection confidence, the Regime Fit diagnostic flags the read as low-confidence/estimated. Display-only — never gates. |
| `FACTOR_ETF_TICKERS` | dict: Momentum=MTUM, Value=VLUE, Quality=QUAL, Low Volatility=USMV, Growth=VUG | **Concept B panel 3 (Phase 2 sub-wave 3).** Factor-proxy ETFs for the 🧩 Portfolio Intelligence "📐 Factor Tilt" heatmap — held positions are Pearson-correlated against each of these over a trailing window. Returns-based style analysis (NOT FMP `.info` style tags, which the plan explicitly warns produces garbage). Diagnostic only — never gates. |
| `FACTOR_TILT_WINDOW_DAYS` | 126 (~6 trading months) | Trailing window fed to the Factor Tilt correlation calc — `_fetch_factor_etf_returns()` (`app.py`) trims each factor ETF's return series to this many trailing days before `portfolio_intelligence.factor_tilt()` runs, so this is the single source of truth for the negotiated 6-month lookback (chosen with the user over a 3-month alternative for statistical stability). |
| `FRAGILITY_PULLBACK_PCT` | -10.0 | Routine-correction yardstick (~1–2× per year) for the Fragility gauge; mirrors the stress-test 'Mild Correction' scenario. The gauge's severity bands reuse the existing `PORTFOLIO_BETA_ELEVATED` (1.3) / `PORTFOLIO_BETA_CEILING` (1.4) constants rather than new thresholds. |
| `TICKER_BETA_HIGH` | 1.5 | Soft warn when added to elevated portfolio |
| `TICKER_BETA_CRITICAL` | 1.8 | Hard breach when added to breached portfolio |
| `SECTOR_CEILING` | 35.0 | Hard cap — no entries when sector at this weight |
| `SECTOR_ELEVATED` | 25.0 | Soft warn — consider half-size |
| `SINGLE_NAME_CEILING` | 15.0 | Hard cap — no add-to-winner above this weight |
| `DIVERSIFY_REDUCE_HIGH_URGENCY_PCT` | 30.0 | `portfolio.diversification_recommendations()`: sector REDUCE rec above this pct = "high" urgency (else "medium") |
| `DIVERSIFY_ADD_SKIP_PCT` / `DIVERSIFY_ADD_TARGET_PCT` | 8.0 / 10.0 | `diversification_recommendations()`: a diversifying sector already at/above SKIP is not flagged underweight; ADD recs size their gap toward TARGET |
| `CONCENTRATION_HIGHBETA_SHARE_WARN` | 60.0 | High-beta cluster line warn color; display-only, not a decision gate |
| `RISK_OFF_TREND_MA` | 200 | Risk-off trend leg — SPY below this SMA = de-risk (Faber 200-DMA) |
| `RISK_OFF_VIX_LEVEL` | 25.0 | Risk-off vol leg — VIX ≥ this = high-vol regime |
| `RISK_OFF_NAME_MIN_BETA` | 1.2 | Risk-off de-risk only trims names with β ≥ this |
| `RISK_OFF_TRIM_TOP_N` | 3 | Risk-off de-risk acts on the top-N beta contributors |
| `MACRO_LEGACY_TLT_RET_PCT` / `MACRO_LEGACY_SPY_RET_PCT` | 3.0 / 5.0 | `macro.detect_macro_regime_legacy()`'s ETF-proxy thresholds (|TLT 3mo return| / |SPY 3mo return| beyond these = rate/trend signals). Also reuses `RISK_OFF_VIX_LEVEL`/`RISK_ON_VIX_LEVEL` above for its VIX leg |
| `RISK_OFF_TRIM_PCT` | 25.0 | Risk-off de-risk suggested trim % (or tighten the stop instead) |
| `PULLBACK_ALERT_INDEX_PCT` | -3.0 | EOD reactive pullback email fires when SPY closes ≤ this %; operational alert knob, not a gate |
| `PULLBACK_ENTRY_DIP_PCT` | 1.5 | Intraday drop from open (%) that triggers the intraday pullback entry email in the new intraday cron lane. Email-presentation only; never gates recommendations. |
| `PULLBACK_SPY_MAX_DOWN` | 1.0 | SPY intraday drop ceiling; all intraday entry signals are suppressed when SPY drops more than this (market rout guard). Headless-only (email alert); never gates in-app recommendations. |
| `ALERT_EOD_HOUR_ET` | 16 | EOD cron run gates on ET hour ≥ this (post-close); operational |
| `CROSS_ASSET_HYG_TREND_DAYS` / `_COPPER_TREND_DAYS` / `_DXY_TREND_DAYS` | 20 / 20 / 20 | Cross-Asset Pulse (F-09c) linear-trend lookback windows (days) for the HYG / copper / DXY legs. Awareness-only — these signals never gate a recommendation |
| `CROSS_ASSET_DXY_ROC_DAYS` / `_DXY_ROC_THRESHOLD` | 5 / 1.5 | Dollar-stress leg: 5-day rate-of-change; stressed when the DXY trend is rising AND ROC > 1.5% |
| `CROSS_ASSET_VIX_TERM_RATIO` | 1.0 | VIX/VIX3M ratio above which the term structure is "inverted" (a stress signal) |
| `CROSS_ASSET_CURVE_STRESS_BP` | -50 | 3m10y spread in bp (^TNX − ^IRX) below which the curve is "deeply inverted" |
| `CROSS_ASSET_STRESS_BRIEF_SCORE` | 2 | Aggregate stress score (count of stressed signals among those with data) at/above which Today's Brief shows the cross-asset one-liner |
| `NEWS_SENTIMENT_BULLISH_THRESHOLD` / `_BEARISH_THRESHOLD` | 0.60 / 0.40 | Finnhub news-sentiment (F-74) label cutoffs: bullish_pct ≥ 0.60 → 🟢 Bullish, < 0.40 → 🔴 Bearish, between → 🟡 Neutral. Awareness-only |
| `NEWS_SENTIMENT_SHIFT_ALERT_BULLISH` / `_SHIFT_BUZZ_MIN` | 0.40 / 1.0 | Brief held-position shift card fires when bullish_pct < 0.40 AND buzz_score > 1.0 (both required — low-buzz bearishness is thin/stale, not alerted) |
| `NEWS_SENTIMENT_CRITICAL` | −0.25 | VADER compound threshold for a qualifying "critical news" headline — compound ≤ this AND tier ≤ 2 is required per headline before it counts toward the Critical News Act Today card. Hard gate |
| `NEWS_CRITICAL_MIN_HEADLINES` | 2 | Min qualifying headlines per held ticker (compound ≤ `NEWS_SENTIMENT_CRITICAL`, tier ≤ `NEWS_CRITICAL_MAX_TIER`) before the Critical News Act Today card fires. Prevents a single borderline VADER score from triggering a stop-tighten directive. Hard gate. |
| `NEWS_CRITICAL_MAX_TIER` | 2 | Max news-source tier (1 = highest quality) that counts toward "critical." Single source shared by `daily_briefing`'s Critical News Act Today card and `news_intelligence.build_news_intelligence`'s per-headline alert-level classifier — was a bare `<= 2` literal duplicated in both (2026-08-04 audit finding) |
| `NEWS_CRITICAL_MIN_WEIGHT_PCT` | 8.0 | Min held-position weight (%) for a critical-compound headline to be classified "critical" (vs "warning") in `news_intelligence`'s per-headline alert level. Was a bare `8.0` literal with no constant at all (2026-08-04 audit finding) |
| `ANALYST_COVERAGE_FRESH_DAYS` | 30 | Analyst Coverage (F-154) — a saved report stays in the "recent" Ideas Inbox view this many days. Awareness-layer knob, not a gate |
| `ANALYST_MIN_UPSIDE_PCT` | 15 | Reserved for the Phase-2 Brief chip (avg-PT upside to surface a held-name analyst nudge); UNUSED in Phase 1 |
| `ANALYST_CONSENSUS_STRONG_BUY_FRAC` / `_BUY_FRAC` / `_SELL_FRAC` | 0.80 / 0.50 / 0.50 | Consensus **label** boundaries (fractions of rated firms) that classify the firm rating distribution into Strong Buy / Buy / Sell / Hold / Mixed. **Display-only classifications — NOT decision thresholds; never gate or score** |
| `RANK_TIER_TOP_DECILE_PCTL` / `RANK_TIER_TOP_QUARTILE_PCTL` / `RANK_TIER_ABOVE_MEDIAN_PCTL` / `RANK_TIER_BELOW_MEDIAN_PCTL` / `RANK_TIER_BOTTOM_QUARTILE_PCTL` | 90 / 75 / 50 / 25 / 10 | `ranking.tier_label()`'s percentile bands classifying a holding's rank vs the scanned universe. Display classification only — never gates or scores |
| `ANALYST_EXTRACT_MAX_TOKENS` | 8000 | Max LLM **output** tokens for one Ideas-Inbox extraction (`analyst_intel.extract_report`). Sized so a CNBC "biggest analyst calls" roundup of 20-30 separate calls fits without the JSON array truncating mid-record (which failed as a silent "extraction failed"). Plumbing knob — billed per token generated, so free for small pastes |
| `ANALYST_EXTRACT_TIMEOUT_SEC` | 90 | Per-call timeout for one Ideas-Inbox extraction — overrides the shared 30s `LLM_REQUEST_TIMEOUT_SEC`. A big roundup makes the model generate several thousand tokens, which runs past 30s → the request times out and looks identical to a parse failure (the actual cause of the roundup bug, once truncation was ruled out by the token bump). On any failure `analyst_intel.LAST_EXTRACT_ERROR` records the real exception and the Ideas Inbox surfaces it as a "Details:" caption instead of a blind error |
| `ANALYST_ACCURACY_DIRECTION_DAYS` | 30 | Research Scorecard (F-154c): measurement window (days after article_date) for Buy/Sell directional accuracy classification. Display-only — never gates or scores |
| `ANALYST_ACCURACY_PT_HIT_PCT` | 0.75 | Research Scorecard: fraction of avg_pt that the window's intra-period **HIGH** must reach to count as a PT "hit" (not the endpoint close). Accounts for the short 30-day window; a genuine 75% touch is a real event, whereas a lucky endpoint price is noise-sensitive. Display-only |
| `ANALYST_ACCURACY_LEADERBOARD_MIN_CALLS` | 2 | Research Scorecard: minimum calls per firm to appear on the Firm Leaderboard. Suppresses single-call noise |
| `ANALYST_ACCURACY_HIGHLIGHTS_MIN_EVALUABLE` | 5 | Research Scorecard: minimum evaluable calls (status ∈ {hit, miss}) before showing the Best & Worst Calls highlight cards. Defers display until enough signal exists |
| `COMPOSITE_BUY` | 65 | Buy boundary — used for entry AND add-to-winner (aligned) |
| `COMPOSITE_STRONG_BUY` | 75 | Strong Buy boundary |
| `COMPOSITE_HOLD` | 44 | Hold floor; below this = "Sell zone" |
| `COMPOSITE_SELL` | 30 | Sell floor; below this = "Strong Sell zone". Used by exit-urgency routing in `portfolio.py` (TRIM urgency = high when score < COMPOSITE_SELL). |
| `COMPOSITE_FIRMNESS_MARGIN` | 3 | Firmness-badge band on 🆕 New Positions to Initiate (F-236): a `new_pick` whose composite is within this many points of its tier floor (`COMPOSITE_STRONG_BUY`=75 or `COMPOSITE_BUY`=65) is badged "at the line" (a normal intraday reprice could flip it), else "firm". **Display-only — does NOT gate, suppress, or reorder any recommendation.** Same non-gating status as the analyst-consensus / correlation-diversifier labels. |
| `SCAN_TOP_PICK_MIN_COMPOSITE` | 70 | Morning scan lane (cron email) — min composite score for the #1 pick to feature with full "Act on" action framing. Below this it still appears but carries a "moderate" label rather than high-conviction directive. Email-presentation only; never gates in-app recommendations. |
| `PERF_ALPHA_BAND_PCT` | 5.0 | ± alpha band (pp vs benchmark) classifying a position as Outperforming / In Line / Underperforming on the Performance + Relative-Strength views (Portfolio Overview). **Display/awareness classification — never a gate or score** (QW8, 2026-07-15) |
| `FUNDAMENTALS_GATE_MIN_METRICS` | 1 | Min core fundamental metrics required to trust the verdict; below it the verdict is withheld (not scored on a fabricated neutral 50) |
| `FUNDAMENTALS_CACHE_MAX_AGE_DAYS` | 7 | Max age the persistent last-known-good fundamentals fallback (Supabase `fundamentals_cache`) stays valid; beyond it the verdict withholds again rather than serving stale data |
| `STOP_TIGHTEN_MIN_GAIN_PCT` | 8.0 | Min P&L before a still-has-room position (gap 3–8% to stop) is nudged to tighten — flat/new positions aren't micromanaged (anti-churn / §2B persona) |
| `POSITION_SETTLING_DAYS` | 10 | Held < this = "settling" lifecycle state → routine stop-tighten nudges suppressed (settling grace); exits/critical never suppressed |
| `POSITION_AT_RISK_GAP_PCT` / `POSITION_WINNING_PNL_PCT` | 3.0 / 8.0 | Lifecycle thresholds: gap ≤3% = at_risk; P&L ≥8% (and healthy) = winning |
| `ALERT_PNL_PROFIT_TAKE_PCT` / `ALERT_PNL_STOP_LOSS_PCT` | 15.0 / -8.0 | `portfolio.alerts()`: bearish signal + P&L above/below these = "consider partial profits" warning / danger-level alert. Named per 2026-07-29 audit H3 (were bare literals) |
| `REBALANCE_TRIM_PNL_PCT` | 20.0 | `portfolio.rebalance_actions()`: oversized position (`SINGLE_NAME_TRIM_TRIGGER`) + gain above this = trim candidate. Named per audit H3 |
| `REBALANCE_ADD_MIN_SCORE` / `REBALANCE_ADD_UNDERSIZED_PCT` / `REBALANCE_ADD_TARGET_WEIGHT_PCT` | 70 / 5.0 / 8.0 | `portfolio.rebalance_actions()`: Strong Buy + undersized (< `REBALANCE_ADD_UNDERSIZED_PCT` weight) + composite above `REBALANCE_ADD_MIN_SCORE` = add candidate, sized to reach `REBALANCE_ADD_TARGET_WEIGHT_PCT`. Named per audit H3 — `REBALANCE_ADD_MIN_SCORE` may be redundant given the adjacent "Strong Buy" check already implies composite ≥ `COMPOSITE_STRONG_BUY` (75); kept as-is pending a deliberate policy review. `REBALANCE_ADD_UNDERSIZED_PCT` itself was still a bare `w < 5` literal until the 2026-08-04 audit — the docs already described it as extracted, the code hadn't caught up |
| `REBALANCE_REVIEW_GAP_PCT` | 5.0 | `portfolio.rebalance_actions()`: bearish signal + profitable + gap below this (or unknown) = high urgency. Named per audit H3 |
| `BUCKET_CRITICAL_NEWS_IS_ACT` / `BUCKET_TIGHTEN_ONLY_IS_ACT` | True / False | Act-vs-Awareness borderline routing (calm advisor 2B): critical news → Act; stop-raise nudges → Awareness. Flip a flag to move the item between buckets with no code change |
| `HYSTERESIS_COMPOSITE_DELTA` | 4.0 | Calm advisor 2C: `\|today − yesterday\|` composite ≤ this (and verdict unchanged) → a Grow-Today pick gets a "↔ Steady vs yesterday" chip. Annotate-only — never suppresses a pick |
| `UNCLASSIFIED_SECTOR` | "Other" | The catch-all bucket a holding lands in with no curated `TICKER_SECTORS` mapping, no provider `.info` sector, AND no cached sector (`sector_cache`, §6.16 — the fallback that keeps a name classified through a thin-`.info` day). NOT a real correlated sector — concentration gates exclude it (a "Hard Cap Breach on Other" is a classification artifact, not a risk) |
| `MACRO_BROAD_EXPOSURE_PCT` | 60.0 | Affected-sector exposure at/above which a HIGH-impact macro event is treated as portfolio-wide (NFP/CPI/Fed). The pre-event trim downgrades to an awareness WATCH ("hold through") instead of a token single-name trim — the sized trim is reserved for sector-concentrated events |
| `ADD_WINNER_COOLDOWN_DAYS` | 10 | After the user adds shares to a position (a buy lot within this window), add-to-winner nudges for that name are suppressed — "don't grow a position you just changed." Aligned with `POSITION_SETTLING_DAYS`. None days-since-last-buy (no journal) → no cooldown |
| `RR_ENTRY_MIN` | 2.0 | Min reward:risk for a favourable entry. Hard-gates Watchlist ENTER_NOW (G-13); on Analysis it drives a caveat, not a block |
| `TARGETS_ENTRY_ZONE_LOW_ATR_FRAC` / `TARGETS_ENTRY_ZONE_HIGH_ATR_FRAC` | 0.25 / 0.10 | `targets.entry_zone()`: ideal entry band = price ∓ these × ATR. One level upstream of `RR_ENTRY_MIN` above (which is the actual gate), not a gate boundary itself |
| `TARGETS_52W_HIGH_FALLBACK_MULT` / `TARGETS_52W_LOW_FALLBACK_MULT` | 1.3 / 0.7 | `targets.compute_price_targets()`: fallback 52-week high/low (× current price) when financials data is missing |
| `TARGETS_SUPPORT_FALLBACK_MULT` | 0.88 | `compute_price_targets()`: fallback nearest-support (× current price) when no local low is found |
| `TARGETS_MODEST_UPSIDE_MULT` | 1.10 | `compute_price_targets()`: generic "modest 10% upside" placeholder, shared by the base-target candidate list and the bull-target fallback default |
| `TARGETS_BASE_FALLBACK_MULT` | 1.08 | `compute_price_targets()`: base-target fallback (× current price) when no candidate qualifies |
| `TARGETS_BULL_ANALYST_MULT` / `TARGETS_BULL_52W_HIGH_MULT` / `TARGETS_BULL_FLAT_MULT` | 1.20 / 1.12 / 1.25 | `compute_price_targets()`'s three bull-target candidates: extended analyst target, 52w-high breakout, flat upside from current price |
| `TARGETS_BEAR_ATR_MULT` | 6.0 | `compute_price_targets()`: bear floor = price − this × ATR (~1.5 monthly adverse moves) |
| `TARGETS_BEAR_SUPPORT_CUSHION_MULT` / `TARGETS_BEAR_52W_LOW_CUSHION_MULT` | 0.98 / 1.03 | `compute_price_targets()`'s bear-floor candidates: nearest support / 52w-low, each with a small cushion |
| `WATCHLIST_STALE_DAYS` | 30 | Watchlist Resurrection (F-203, O4): a watchlist ticker added at least this many days ago that is now ENTER_NOW/NEAR_ENTRY and not already held is flagged as plausibly forgotten — a memory-jog caption, never a gate |
| `CATALYST_WATCH_WINDOW_DAYS` | 7 | Forward window for the Catalyst Watch earnings-awareness panel |
| `REC_SCORE_MIN_DAYS` | 5 | Min calendar days a rec must be live before its OUTCOME is scored on the Recommendations History page and included in the aggregate metrics (avg outcome / alpha / best / worst). Younger recs display with ⏳ label but are excluded from the scorecard — one session of price wiggle isn't a meaningful outcome. **Measurement-only; never affects what the engine recommends**, only how long the scorecard waits before grading. Safe to tune from observation. |
| `ENGINE_TRACK_MIN_CALLS` | 8 | **Display-only** band threshold for the 🎯 Engine Track Record pointer card (🧾 Summary page, F-229). Below this count of matured acted new_pick calls the card shows "Building history" and withholds any verdict — too few data points for a meaningful track record. NOT an investment gate; never feeds into the recommendation pipeline or composite score. Safe to tune from observation. |
| `ENGINE_TRACK_FIRM_CALLS` | 15 | **Display-only** band threshold. At/above this count the card shows a "firm" verdict (WORKING or LAGGING); between `ENGINE_TRACK_MIN_CALLS` and this value it shows an "EARLY READ" softened verdict. Same caveats as `ENGINE_TRACK_MIN_CALLS`. |
| `PROTECT_TRACK_MIN_CALLS` | 8 | **Display-only** band threshold for the 🛡️ Defense facet (protective EXIT/TRIM calls) of the same 🎯 Engine Track Record pointer card (F-229 Phase 2). Below this count of distinct mature+priced flagged tickers the card shows "Building protective track record" and withholds any verdict. NOT an investment gate; never feeds into any alert, the exit advisor, or the composite score. Safe to tune from observation. |
| `PROTECT_TRACK_FIRM_CALLS` | 15 | **Display-only** band threshold. At/above this count the Defense facet shows a "firm" verdict (VALIDATED or RAN EARLY); between `PROTECT_TRACK_MIN_CALLS` and this value it shows an "EARLY READ" softened verdict. Same caveats as `PROTECT_TRACK_MIN_CALLS`. |
| `SELF_TRACK_MATCH_LOOKBACK_DAYS` | 3 | My Edge → 🧭 Self vs Engine — a BUY counts as `app_aligned` only if a matching `new_pick`/`buy_candidate` recommendation exists within this many days before (inclusive) the trade date; a rec further back is a distinct, later decision, and a rec dated after the trade never counts (no lookahead). Measurement-only — never gates, sizes, or suppresses a recommendation. User chose the tighter 3-day option over looser 5/10-day alternatives, 2026-08-06. |
| `SELF_TRACK_RELIABLE_LOG_START` | 2026-08-06 | My Edge → 🧭 Self vs Engine — ship date of the cron-side recommendation-logging fix (`cron_runner.py._run_scan` now persists today's `new_pick` rows even on days with no interactive session). Before this date, an in-scope ticker (universe or watchlist) bought with no matching rec on file is bucketed `coverage_limited` (disclosed, never graded either way) since the gap could be missing coverage rather than a genuine self-initiated call; on/after this date the same shape is graded `self_in_scope`. Boundary inclusive (`>=`). |
| `PREDICTIVE_MIN_BAND_N` | 5 | Minimum mature outcomes in a composite-score band before Signal Calibration (F-178) renders that band's bar. Below this the band is still drawn but coloured grey — labelled "Too few" to prevent misleading averages on 1–2 data points. Measurement floor only; **not a decision gate**. |
| `PREDICTIVE_SCORE_BAND_SIZE` | 5 | Width of each composite-score interval in the Signal Calibration chart (F-178). 5-point bands (65–69, 70–74, …) give enough granularity to show where personal edge starts without producing too many empty bands on a small history. |
| `ENTRY_TIMING_DEDUP_WINDOW_DAYS` | 5 | Entry Timing tab (F-220, Predictive Analytics Tab 6): calendar days a same-ticker `new_pick` re-firing must fall within a prior kept firing to be collapsed into one opportunity rather than counted as N independent data points (e.g. AMD firing 5x in 2 weeks). **Provisional** — fit to that one anecdote, not yet validated against the real cluster-length distribution. Measurement-only; never a gate. |
| `ENTRY_TIMING_DIVERGENCE_ALIGNED_MAX` | 15 | Entry Timing tab: upper bound (inclusive) of the "Aligned" divergence band, where divergence = `momentum_score − composite_score` at the moment a `new_pick` fires. **Provisional**, same caveat as above. Diagnostic only — never feeds back into the composite score or the 5-gate new-position pipeline. |
| `ENTRY_TIMING_DIVERGENCE_DIVERGING_MAX` | 25 | Entry Timing tab: upper bound (inclusive) of the "Diverging" band; above this is "Extreme" (momentum running far ahead of a barely-qualifying composite — the AMD pattern this tab surfaces). **Provisional**, same caveat as above. Diagnostic only. |
| `RISK_PCT_PER_TRADE` | 0.015 | 1.5% portfolio risk per trade (Moderate) |
| `EARNINGS_IMMINENT_DAYS` | 7 | Trades within this window flagged caution |
| `EARNINGS_CRITICAL_DAYS` | 3 | Tighter "danger" sub-window inside `EARNINGS_IMMINENT_DAYS` — decide position size before the report, vs. the wider window's "review ahead of report." Single source for `portfolio.alerts()`'s danger/warning split and `daily_briefing`'s earnings-overweight priority bump; was a duplicated bare `3` in each (2026-08-04 audit finding) |
| `EARNINGS_BEAT_RATE_REDUCE_THRESHOLD` | 60.0 | Pre-Earnings Playbook (F-174) — CNBC-sourced historical beat rate below this, combined with composite < `COMPOSITE_BUY`, adds a REDUCE condition |
| `EARNINGS_BEAT_RATE_STRONG_THRESHOLD` | 75.0 | Pre-Earnings Playbook (F-174) — beat rate at/above this strengthens the HOLD_OR_ADD narrative (annotation only, does not change the verdict) |
| `EARNINGS_BEARISH_REACTION_COMPOSITE_GATE` | 75 | Pre-Earnings Playbook (F-174) — a bearish CNBC-sourced post-earnings reaction history, combined with composite below this gate, adds a REDUCE condition |
| `EARNINGS_MIN_BEAT_RATE_ENTRY` | 70.0 | Catalyst Scanner (F-37b, Earnings Playbook Phase 3) — min historical beat rate for a watchlist name to surface as an Entry Candidate on 🔔 Catalyst Watch. Awareness-only filter, combined with composite ≥ `COMPOSITE_BUY` + reaction ≠ bearish; never a Buy rec |
| `MACRO_IMMINENT_DAYS` | 3 | Hard suppress new picks in sectors with HIGH-impact macro within this window |
| `MACRO_HEADWIND_WARN_PCT` | 30.0 | 🌐 Macro page: warn when this much of the portfolio (by weight) sits in sectors facing a headwind under the current ETF-proxy regime read. Was a bare `> 30` literal (2026-08-04 audit finding) |
| `REGIME_CPI_CONTROLLED_MAX` | 2.5 | CPI YoY ≤ this = controlled inflation (rate-cut supportive); ALSO the hard gate ceiling above which the "Rate-Cut Optimism" regime cannot be selected |
| `REGIME_CPI_ELEVATED_MIN` / `_HOT_MIN` | 3.0 / 4.0 | Regime-classifier CPI ladder: ≥ELEVATED = mild inflation-fight pressure; >HOT = strong inflation-fight / stagflation signal |
| `REGIME_FEDFUNDS_TREND_PP` | 0.05 | `macro_calendar.detect_macro_regime()`: 3mo Fed Funds change beyond ± this = cutting/hiking, else "holding". Named per 2026-07-29 audit H2 (pure extraction, no value change; were bare literals) |
| `REGIME_2S10S_INVERTED_PP` / `REGIME_2S10S_FLAT_PP` / `REGIME_2S10S_STEEP_PP` | -0.25 / 0.0 / 0.75 | Regime-classifier 2s10s spread ladder: below INVERTED = strong recession-fear; below FLAT (and above INVERTED) = mild recession-fear; above STEEP = rate-cut supportive. Named per audit H2 |
| `REGIME_UNEMP_DELTA_UP_PP` / `REGIME_UNEMP_DELTA_DOWN_PP` | 0.3 / -0.2 | Regime-classifier 3mo unemployment-delta ladder: rise above UP = recession-fear signal; fall below DOWN = rate-cut supportive. Named per audit H2 |
| `REGIME_HY_SPREAD_STRESS_BP` / `REGIME_HY_SPREAD_ELEVATED_BP` / `REGIME_HY_SPREAD_CALM_BP` | 600 / 450 / 300 | Regime-classifier HY credit spread (bps) ladder: above STRESS = strong recession-fear; above ELEVATED (below STRESS) = mild recession-fear; below CALM = rate-cut supportive. Named per audit H2 |
| `REGIME_SPY_20D_BULL_PCT` / `REGIME_SPY_20D_BEAR_PCT` | 5.0 / -5.0 | Regime-classifier SPY 20-trading-day return ladder: above BULL = rate-cut supportive; below BEAR = recession-fear signal. Named per audit H2 |
| `REGIME_VIX_STRESS` / `REGIME_VIX_ELEVATED` / `REGIME_VIX_CALM` | 30 / 20 / 15 | Regime-classifier VIX ladder: above STRESS = strong recession-fear; above ELEVATED (below STRESS) = mild recession-fear; below CALM = rate-cut supportive. Named per audit H2 |
| `REGIME_WINNING_SCORE_MIN` | 1 | Regime-classifier fallback: the winning regime's score must exceed this, else the regime falls back to "neutral". Named per audit H2 |
| `DIVERSIFY_SCAN_CAP` | 10 | Max discovery-universe names composite-scored per underweight sector on the Diversification ADD card (bounds cached-load_all work) |
| `DIVERSIFY_DISPLAY_TOP` | 3 | Ranked diversification candidates shown per sector (best-first by composite) |
| `REDEPLOY_CORR_DIVERSIFIER_MAX` / `_CORRELATED_MIN` | 0.40 / 0.70 | Correlation-to-your-book label boundaries, shared by the Hard-Cap-Breach rebalance plan (F-22c) AND the Diversification Advisor ADD card (F-13a, wired in 2026-08-21) via the common `portfolio.classify_book_corr()`: < MAX → 🟢 genuine diversifier, ≥ MIN → 🔴 limited benefit, between → 🟡 partial. **Display classification, NOT a gate** — never suppresses or reorders a candidate (composite + `COMPOSITE_BUY` remain the sole ranker/gate). Same status as the analyst-consensus labels |
| `GROW_MAX_PICKS_BULL` / `_DEFAULT` | 3 / 1 | Grow Today new-position cap per day (bull / flat-bear). Investment-policy values |
| `GROW_CANDIDATE_OVERFETCH` | 4 | Over-fetch multiplier — composite-score this many × the pick cap so enough candidates survive the gates. Coverage/perf knob, not a policy threshold |
| `GROW_CANDIDATE_POOL` | 12 (derived) | `GROW_MAX_PICKS_BULL × GROW_CANDIDATE_OVERFETCH` — the bull-day max candidate window; app.py pre-fetches composites for this many top non-held picks. Single source of truth (replaced a hardcoded `.head(12)` in two app.py sites) |
| `DATA_FMP_INFO_CACHE_TTL_SEC` | 3600 | TTL for per-ticker FMP `.info` backfill cache (only non-sparse responses cached) |
| `STOP_PROFIT_LOCK_PNL_PCT` / `_TRIM_PCT` | 25 / 25 | Review profit-lock: trigger P&L and trim size |
| `ATR_STOP_MULT` | 2.0 | Initial / trailing stop width = price − this × ATR. Single source consumed by `risk.atr_stop_loss`, `bundle_loader` and the Analysis stop-ladder explainer (`portfolio.stop_ladder`) so the number can't drift between engine and UI |
| `STOP_RATCHET_LEVELS` | `((75,0.40),(50,0.25),(25,0.10),(10,0.02))` | Profit-lock ratchet ladder — as gain grows, floor the protective stop at `avg_cost × (1 + floor_pct)`. Single source consumed by `portfolio.protective_stop`/`stop_ladder` and the Analysis ladder explainer/simulator. Moved here from a `portfolio.py` module-local list (2026-08-04 audit finding — was invisible to `check_constants_documented.py`) |
| `MARKET_TONE_BULL_PCT` / `MARKET_TONE_BEAR_PCT` | 0.5 / -0.5 | S&P 500 daily % move that classifies the day's market tone (bull/bear/flat), which selects `COMPOSITE_BUY` vs `COMPOSITE_BUY_FLAT_DAY` vs no new entries. Single source shared by Home's `_market_context` assembly and `headless_alert_engine.py`'s morning-email tone — previously duplicated as a bare `±0.5` literal in both, risking the interactive app and the cron silently screening a day's picks against different gates (2026-08-04 audit finding) |
| `GAP_TO_STOP_ROUND_DECIMALS` | 1 | Decimal places the Gap-to-Stop % is rounded to before the breach test (`gap ≤ 0`). Single source shared by `build_portfolio_df`, the Daily Brief breach loop and the Analysis breach gate so all three fire at the exact same price. Not a stop-width policy value — controls only where rounding tips a near-zero gap to ≤ 0 |
| `STOP_TIGHTEN_ATR_MULT` | 1.5 | Review stop-tighten multiple (vs 2.0× initial) |
| `EARNINGS_OVERWEIGHT_TRIM_PCT` / `EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT` / `EARNINGS_OVERWEIGHT_TOLERANCE_PP` / `EARNINGS_OVERWEIGHT_TRIM_TO_PCT` | 12 / 22 / 5 / 10 | Review earnings-overweight: trigger is now position-count-aware (`daily_briefing._dynamic_overweight_floor`) — `clamp(100/N + EARNINGS_OVERWEIGHT_TOLERANCE_PP, EARNINGS_OVERWEIGHT_TRIM_PCT, EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT)`, N = held position count. `EARNINGS_OVERWEIGHT_TRIM_PCT` (12) is the min clamp (floor-of-the-floor, preserves legacy behavior for diversified portfolios); `EARNINGS_OVERWEIGHT_TRIM_CEILING_PCT` (22, new 2026-07-28) caps binary-event risk even for a deliberately concentrated book; `EARNINGS_OVERWEIGHT_TOLERANCE_PP` (5, new 2026-07-28) is its own dedicated buffer constant — deliberately NOT imported from `rebalancer.TOLERANCE_WATCH` (same numeric value) so this binary-event gate can be tuned independently of the rebalancer's general drift-monitor band (Opus review flag). Target weight (`EARNINGS_OVERWEIGHT_TRIM_TO_PCT`, 10) stays flat regardless of N. Recalibrated after a flat 12% trigger fired on 5 of 7 holdings in a real concentrated portfolio despite none exceeding equal-weight+tolerance |
| `WEAK_LARGE_TRIM_TO_PCT` | 8 | Review weak-large: trim-to target weight |
| `MACRO_AFFECTED_TRIM_THRESHOLD_PCT` / `_REDUCTION_PP` | 30 / 5pp | Review macro-affected: sector trigger / reduction |
| `MOVER_MIN_DAY_GAIN_PCT` | 5 | Min 1-day gain to qualify as a discovery mover |
| `MOVER_SHORTLIST_SIZE` / `MOVER_MAX_PICKS` | 12 / 3 | Movers composite-gated shortlist / surfaced cap (own allowance) |
| `DATA_MULTISOURCE_ENABLED` | True | Master switch — False reverts to single-source yfinance (instant rollback) |
| `DATA_PROVIDER_ORDER` | `[yahoo_finance, finnhub, fmp]` | Failover order for history/bundle/indices/risk-free |
| `DATA_LIVE_PRICE_ORDER` | `[finnhub, yahoo_finance, fmp]` | Failover order for live prices — Finnhub real-time PRIMARY |
| `DATA_XCHECK_PREVCLOSE_TOL_PCT` | 0.5 | Cross-check: settled prev_close strict tolerance (integrity faults) |
| `DATA_XCHECK_LIVE_TOL_PCT` | 3.0 | Cross-check: live-price loose tolerance (latency-tolerant) |
| `DATA_XCHECK_FIELDS` | `{price}` | Which fields are cross-checked (price only; rest failover-only) |
| `BRIEF_AUTO_REFRESH_MINUTES` | 30 | Today's Brief freshness-chip cadence (F-177) — the "Built at" chip's countdown AND the watchdog fragment that auto-rebuilds the Brief past this age share this single value, so they can never drift apart. Operational cadence knob, NOT an investment threshold. |
| `PROVIDER_RL_COOLDOWN_SEC` | 120 | Provider circuit-breaker (rate-limit-resilience Phase 2): once a data provider trips "red" in `api_health` (rate_limits ≥ 3 or consecutive_errors ≥ 5), the orchestrator skips it for this many seconds rather than re-calling it on every ticker, which exhausts free-tier quotas (FMP 250/day). Auto-recovers after the window; if ALL capable providers are cooled, falls through to the full list (never a permanent hard-block). Operational infra knob — reversible, tune from observation. |
| `FMP_DAILY_CALL_CAP` | 250 | FMP free-plan hard limit (calls/day). Reference constant — the operational pause is enforced at `FMP_DAILY_SOFT_CAP` below. |
| `FMP_DAILY_SOFT_CAP` | 220 | Orchestrator pauses FMP at this daily call count (30-call buffer before the hard limit). When `api_health.get_fmp_daily_quota()` returns ≥ this, `orchestrator._providers_for()` drops FMP from the capable list; falls through to the full list if all providers are suppressed — never a permanent hard-block. DDL applied in Supabase (2026-07-10) — active. |
| `DATA_LOAD_MAX_WORKERS` | 2 | Cold-load fan-out concurrency for `_parallel_load_all` (was 4). Yahoo (the history/bundle primary) throttles bursty parallel requests; a wide synchronized fan-out trips it and cascades to 'Could not load' across every name. Operational tuning, not an investment gate. |
| `DATA_LOAD_STAGGER_SEC` | 0.1 | Gap between thread submits in `_parallel_load_all` so request starts aren't synchronized (de-bursts Yahoo). Operational tuning. |
| `REFERENCE_SHELF_LIFE_DAYS` | `{sector_universe: 90, discovery_universe: 90, sp500_sector_weights: 90, sector_candidates: 90}` | How many days a hand-maintained static reference table stays trustworthy before 🩺 System Trust check ⑤ flags it for a human refresh. Keyed to the registry in `stock_analyzer/reference_shelf.py` (a test asserts both key sets match in both directions). All four are 90d — three were tightened from 180 on 2026-08-15 (user call: leadership turns over fast in the current tech cycle). Nag fatigue, the usual argument against a tight interval, barely applies **by design** — check ⑤ is pull-only and off the Home chip, so a tighter cadence costs attention it never spends. Revisit if ⑤ ever gains a push surface. **Observability only** — never gates a recommendation, suppresses a pick, or changes a score. |
| `DB_OUTAGE_SAFE_PAGES` | `("🩺 System Trust", "📖 User Guide")` | The only pages that still render when the initial Supabase load has failed; every other page is hard-stopped behind an outage banner rather than shown with an empty portfolio, which would read as "you hold nothing". A **hard-suppression boundary**, which is why it lives in `constants.py` rather than as a tuple in `app.py`: `tests/` cannot import `app.py`, so this is the only place the "don't strand the user without a diagnostic" invariant can be mechanically pinned. Both entries render **no portfolio state**, so neither can misrepresent the book while the DB is unreadable; 🩺 System Trust in particular is the page that diagnoses this exact outage, and stopping before it is reachable would make the fix hide its own diagnostic. **Do not add a page here that reads holdings/trades/watchlist.** Companion `DB_RELOAD_RETRY_SEC` (30s auto-retry cooldown) is allowlisted rather than tabled — it is operational plumbing that gates an outage re-probe, never a pick. |
| `TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT` | `90.0` | Minimum share of the reference rosters (`SECTOR_UNIVERSE` ∪ `DISCOVERY_UNIVERSE` ∪ `_SECTOR_CANDIDATES`, ~230 unique) that must resolve in the weekly liveness sweep before its dead-ticker verdict is trusted. Below it the sweep reports `"inconclusive"` and says so in the email rather than reporting a false clean bill of health. A **batch-health floor** rather than confirmation across weeks, because the false positive being defended against (provider rate-limited/down) hits the whole batch at once and is therefore measurable inside one run — confirming across runs would need persistence, coupling a roster-rot check to the very DB whose outage F-239 addressed, and would delay a true finding by a week. 90% tolerates ~23 simultaneous misses; observed normal jitter is one (2026-08-16 = 99.6%). **Observability knob, NOT an investment threshold** — it gates whether a chore email is sent, never whether a pick is made or a gate fires. |
| `REFERENCE_HORIZON_MIN_DAYS` | `{macro_event_calendar: 90, nyse_calendar: 365}` | Minimum remaining runway (days) on a *forward-dated* table before check ⑤ flags it. Converts an expiry cliff into advance notice — the pre-existing `MARKET_CALENDAR_LAST_YEAR` mechanism only warns *after* the calendar has run out. The macro horizon is derived **per event series** (earliest series expiry), never the global max, so one freshly-extended series can't mask five expiring ones. **Observability only.** |
| `BUNDLE_CACHE_MAX_AGE_DAYS` | 5 | Max age of a last-known-good bundle that `load_all` will serve when all history/bundle providers are down (`bundle_cache` table). Beyond this, fail loud rather than show very stale signals. Mild policy flavour. |
| `SPLIT_DETECT_LOOKBACK_DAYS` / `SPLIT_DETECT_MIN_DISTORTION` / `SPLIT_DETECT_MAX_ADJ_DISTANCE` | 730 / 0.35 / 0.60 | `split_detector.py` — data-integrity tuning, not an investment threshold. Only investigate a ticker if `\|cost vs price\|` gap exceeds `MIN_DISTORTION`; fetch `LOOKBACK_DAYS` of yfinance split history; confirm a detected split only if the adjusted cost lands within `MAX_ADJ_DISTANCE` of current price. Were module-local literals (2026-08-04 audit finding) |
| `GROW_TODAY_MAX_FUND_AGE_DAYS` | 2 | Max fundamentals age (calendar days) before a new-position pick is routed to `composite_unavailable` ("Pending Verification") in `_grow_today`. More conservative than `FUNDAMENTALS_CACHE_MAX_AGE_DAYS` (7), which governs held-position display verdict — new-position recommendations carry higher trust expectations than display of already-held positions |
| `NYSE_HOLIDAYS` | frozenset (ISO dates, 2026–2028) | NYSE full-day closures by observed date (e.g., "2026-06-19" Juneteenth). Calendar facts, not decision gates. Consulted by `is_market_holiday()` and `market_status()`. |
| `NYSE_EARLY_CLOSES` | dict (ISO date → hour ET) | Half-day early closures 2026–2028 (ISO date keys map to 13.0 = 1:00 PM ET). Calendar facts. Consulted by `_early_close_hour()`. |
| `MARKET_CALENDAR_LAST_YEAR` | 2028 | Last hardcoded year in NYSE_HOLIDAYS/NYSE_EARLY_CLOSES. When system year exceeds this, `market_status()` sets `calendar_stale=True` so the UI warns to extend the calendar before 2029. Calendar-maintenance constant; must be extended with fresh holidays/early-closes before each year-end. |
| `ACCOUNT_CASH_STALE_DAYS` | 7 | Max age (calendar days) of a cached cash balance on the 💰 Account page before it is shown as stale. Display-only staleness indicator; never gates a recommendation. |
| `REDEPLOY_CORR_CORRELATED_MIN` | 0.70 | Already documented inline with `REDEPLOY_CORR_DIVERSIFIER_MAX` above (row 282). Repeated here for checker coverage. |
| `CROSS_ASSET_COPPER_TREND_DAYS` / `CROSS_ASSET_DXY_TREND_DAYS` | 20 / 20 | Already documented inline with `CROSS_ASSET_HYG_TREND_DAYS` above (all three share the same value and purpose). Repeated here for checker coverage. |
| `CROSS_ASSET_DXY_ROC_THRESHOLD` | 1.5 | Already documented inline with `CROSS_ASSET_DXY_ROC_DAYS` above. Repeated here for checker coverage. |
| `NEWS_SENTIMENT_BEARISH_THRESHOLD` | 0.40 | Already documented inline with `NEWS_SENTIMENT_BULLISH_THRESHOLD` above (both are label cutoffs for the Finnhub sentiment card, awareness-only). Repeated here for checker coverage. |
| `NEWS_SENTIMENT_SHIFT_BUZZ_MIN` | 1.0 | Already documented inline with `NEWS_SENTIMENT_SHIFT_ALERT_BULLISH` above (both are required for the held-position shift card). Repeated here for checker coverage. |
| `ANALYST_CONSENSUS_BUY_FRAC` / `ANALYST_CONSENSUS_SELL_FRAC` | 0.50 / 0.50 | Already documented inline with `ANALYST_CONSENSUS_STRONG_BUY_FRAC` above (all three are display-only consensus label boundaries). Repeated here for checker coverage. |
| `NEWS_OPPORTUNITY_COMPOUND_MIN` | 0.10 | Minimum VADER compound score for a headline to qualify as a news-opportunity signal on the watchlist. Awareness-only filter; never gates a recommendation. |
| `NEWS_OPPORTUNITY_SCORE_MIN` | 55 | Minimum composite score required for a watchlist ticker to surface as a news-driven opportunity. Awareness display filter; operates below the Buy threshold (`COMPOSITE_BUY` = 65) so it is informational only and never drives an entry recommendation. |
| `EARNINGS_MANAGEABLE_DAYS` | 21 | Earnings within this many days (and beyond `EARNINGS_URGENCY_SOON_DAYS`) are flagged as "manageable" on the Catalyst Watch holdings playbook — the print is approaching but not imminent. Display classification, not a gate. |
| `EARNINGS_URGENCY_SOON_DAYS` | 14 | Earnings within this many days are flagged as "soon" (higher urgency than "manageable"). Display classification on the Catalyst Watch playbook; does not suppress or alter any recommendation. |
| `CATALYST_STRESS_WINDOW_DAYS` | 14 | Catalyst-Specific Stress (F-207, D4) — how far ahead a HIGH-impact macro event or a held-ticker earnings date must fall to count as "upcoming" for the structural-overlap ranking. Deliberately a separate constant from `EARNINGS_URGENCY_SOON_DAYS` (which drives a different feature, the Catalyst Watch playbook tier) so tuning one never silently affects the other. Display/ranking window only, never a gate. |
| `MONTHLY_REPORT_MIN_GRADED` | 5 | Minimum number of graded recommendations (outcomes available) required before the F-4 Monthly Intelligence Report will include a "signal discipline" score. Below this count the score section is omitted and a data-thin note is shown instead. Quality gate on the report narrative; never affects investment recommendations. |
| `DECISION_QUALITY_GRADE_A` / `DECISION_QUALITY_GRADE_B` / `DECISION_QUALITY_GRADE_C` / `DECISION_QUALITY_GRADE_D` | 80 / 65 / 50 / 35 | 🎯 My Edge (F-183/F-185) — composite score thresholds for the Decision Quality monthly grade: A ≥ 80 (Elite), B ≥ 65 (Disciplined), C ≥ 50 (Learning), D ≥ 35 (Struggling), F < 35. Measurement-policy constants for the retrospective grade; never gate or alter any investment recommendation. |
| `DECISION_QUALITY_WIN_RATE_FLOOR_PCT` / `DECISION_QUALITY_WIN_RATE_CEILING_PCT` | 30.0 / 70.0 | Win-rate subscore linear map (floor%→0, ceiling%→100) feeding the Decision Quality composite. Was a bare `30.0`/`40.0`-range literal (2026-08-04 audit finding) |
| `DECISION_QUALITY_PF_FLOOR` / `DECISION_QUALITY_PF_CEILING` | 0.5 / 2.0 | Profit-factor subscore linear map (floor→0, ceiling→100) feeding the Decision Quality composite. Was a bare literal (2026-08-04 audit finding) |
| `DECISION_QUALITY_OVERTRADE_SEVERE_MULT` / `DECISION_QUALITY_OVERTRADE_MODERATE_MULT` / `DECISION_QUALITY_OVERTRADE_SEVERE_PENALTY` / `DECISION_QUALITY_OVERTRADE_MODERATE_PENALTY` | 2.0 / 1.5 / 25.0 / 10.0 | Overtrading penalty on the Decision Quality composite — a month's trade count vs. its rolling 12-month prior average, at 2 severity tiers. Was a bare literal (2026-08-04 audit finding) |
| `DECISION_QUALITY_MIN_TRADES` | 2 | My Edge (F-185) — minimum closed trades required in a period before a Decision Quality grade is computed. Periods with fewer trades display "n/a" rather than an unreliable grade. |
| `DECISION_QUALITY_ALPHA_SCALE` | 5.0 | My Edge (F-185) — scaling factor that converts realised alpha vs SPY (in percentage points) into a 0–100 sub-score contribution. At this scale, +5pp alpha → +100 points; −5pp → 0 points. Calibrated so a consistent 5pp outperformer scores A-range from alpha alone. Measurement-policy constant. |
| `WORKFLOW_ANALYST_LOOKBACK_DAYS` | 90 | My Edge (F-184) — look-back window (calendar days, before-only) for matching a saved analyst-coverage record to a BUY trade when classifying its prep tier. A report saved more than 90 days before the trade date is not counted as prep. Awareness-only classification; never gates entry. |
| `WORKFLOW_EARNINGS_WINDOW_DAYS` | 30 | My Edge (F-184) — look-back window (calendar days, before-only) for matching a saved earnings-context record to a BUY trade for prep-tier classification. A record saved after the trade date or more than 30 days before it is excluded (prevents forward-looking data leakage). Awareness-only. |
| `WORKFLOW_MIN_THESIS_LENGTH` | 10 | My Edge (F-184) — minimum character length of `user_thesis` for a trade to count as having a thesis (guards against a single-word or placeholder entry inflating the prep tier). Awareness-only. |
| `BEHAVIORAL_MIN_SAMPLE_N` | 8 | My Edge → Behavioral Fingerprint (F-193, Concept A v1) — minimum sample size required in EACH compared bucket before a pattern card renders a directional finding; below this, the card shows "insufficient data" instead. The single most important invariant for this feature per the plan's own "premature pattern labeling" risk warning. Display-gate only — never touches a gate, score, or recommendation. |
| `BEHAVIORAL_OPENING_WINDOW_MIN` | 30 | My Edge → Behavioral Fingerprint (F-193) — minutes after the 9:30 ET market open considered "the opening window" for the opening-window entry-timing pattern. Direct citation of Concept A's own illustrative example (`docs/plans/next-evolution-strategy.md`: "first 30 minutes of market open"). |
| `BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP` | 5.0 | My Edge → Behavioral Fingerprint (F-193) — display-copy threshold only: decides whether the momentum-chasing and conviction-tier patterns render a directional sentence ("chases"/"fades"/inverted) vs. "little/no difference." Never suppresses a card — that's `BEHAVIORAL_MIN_SAMPLE_N`'s job. |
| `BEHAVIORAL_MEANINGFUL_ALPHA_DELTA_PP` | 1.0 | My Edge → Behavioral Fingerprint (F-193) — same role as the constant above, scoped to the opening-window pattern's SPY-adjusted alpha comparison (a different unit/scale than action-rate percentage points, hence a separate constant). |
| `EXIT_SIGNAL_ACT_WINDOW_DAYS` | 7 | My Edge → Behavioral Fingerprint exit-side (Concept A v2) — calendar days after a WATCH/TRIM/EXIT/RISK_OFF signal date within which a SELL trade on the same ticker counts as "acted on." Defines what "responded to the signal" means; investment-policy constant. Used by `signal_response_rate_pattern` and `signal_lag_pattern` in `behavioral_fingerprint.py`. `escalation_ignored_pattern` uses the inter-signal gap as its window (not this constant), consistent with the plan. Awareness-only — never gates or scores. |
| `EXIT_VELOCITY_LOOKBACK_DAYS` | 5 | Rolling window (in days) for computing composite score velocity on WATCH-tier held positions. Used by the premarket cron to detect when a WATCH is accelerating downward before the TRIM threshold is crossed. Never gates in-app recommendations — headless-only (email alert). |
| `EXIT_VELOCITY_DROP_THRESHOLD` | 8 | Composite-point drop threshold over the `EXIT_VELOCITY_LOOKBACK_DAYS` window that triggers a velocity early-warning section in the premarket email. A WATCH ticker that drops 8+ composite points over 5 days fires the alert. Investment-policy decision for how sensitive early warnings are — tune to control email noise. Headless-only. |
| `TAX_RATE_SHORT_TERM` / `TAX_RATE_LONG_TERM` | 0.37 / 0.20 | Concept F — US high-bracket STCG/LTCG **estimate** rates used by `tax_advisor` and the exit-card tax lens. Display-only: framed as directional estimates (actual tax depends on full-year income, state tax, other realized losses, lot method); never gates, sizes, or reorders a recommendation. |
| `TAX_STCG_THRESHOLD_DAYS` | 366 | Concept F — IRS long-term threshold; a lot held ≥ 366 days is long-term. Single-sourced here (was a bare literal in `tax_advisor`). Display/classification only. |
| `TAX_HARVEST_MIN_LOSS` | 500 | Concept F — minimum unrealized loss ($) before the Tax Advisor page surfaces a HARVEST action. Below this the position shows MONITOR. Display threshold on the awareness-only Tax Advisor surface; the G-08 HARVEST-vs-conviction interaction is unchanged. |
| `TAX_LTCG_WAIT_WINDOW_DAYS` | 60 | Concept F — a short-term gain within this many days of long-term eligibility is labelled WAIT (vs HOLD_FOR_LTCG) on the Tax Advisor page. Display classification only. |
| `TAX_LONGTERM_WINDOW_DAYS` | 30 | Concept F — an EXIT/TRIM signal on a position within this many days of long-term-gains eligibility gains an amber holding-period note ("waiting can cut tax drag"). **Awareness-only** annotation layered on the unchanged exit signal — never suppresses or delays the recommendation. |
| `TAX_WASH_SALE_DAYS` | 30 | Concept F — IRS wash-sale window (fixed by law). A SELL at a loss within this many days of a same-ticker BUY shows a wash-sale awareness note on the SELL confirmation card. Awareness-only; never blocks the sale. |
| `INVESTOR_MIRROR_MIN_CLOSED_LOTS` | 10 | Investor Mirror (F-194) — minimum number of matched sell-lots required in **each** comparison group (winners AND losers) for the Disposition Effect card to render a finding; also used as the total-lot floor for the Breakeven Anchoring and Win/Loss Closure Ratio cards. Display-gate only — same role as `BEHAVIORAL_MIN_SAMPLE_N`. |
| `INVESTOR_MIRROR_MIN_POSITIONS` | 5 | Investor Mirror (F-194) — minimum held positions with a valid composite score required for the Conviction Alignment Spearman ρ computation. Below this the card shows "insufficient data." Display-gate only. |
| `CONVICTION_ALIGNMENT_LOW` | 0.30 | Investor Mirror (F-194) — Spearman ρ below this threshold maps to the "Random" alignment label on the Conviction Alignment card. Display-copy only; never suppresses or gates. |
| `CONVICTION_ALIGNMENT_HIGH` | 0.60 | Investor Mirror (F-194) — Spearman ρ at or above this threshold maps to the "Disciplined" alignment label; between LOW and HIGH = "Partial." Display-copy only. |
| `DISPOSITION_CONCERN_RATIO` | 1.5 | Investor Mirror (F-194) — if the weighted-average loser hold-time ÷ winner hold-time is at or above this value, the Disposition Effect card renders an amber concern note. Consistent with the published retail-investor benchmark range (1.5–2.0×). Display-copy only. |
| `WINLOSS_CONCERN_RATIO` | 2.0 | Investor Mirror (F-194) — if gain-realising sell-transactions ÷ loss-realising sell-transactions is at or above this value, the Win/Loss Closure Ratio card renders a loss-aversion note. Display-copy only. |
| `CONVICTION_WEAK_SCORE` | 50 | Investor Mirror (F-194) — composite score below this threshold classifies a position as "Accidental Overexposure" when it is also above median portfolio weight. Mirrors the composite-tier vocabulary (75/65/44) without duplicating an existing tier boundary. Display-copy only; never a gate. |
| `CONVICTION_FADED_SCORE` | 60 | Investor Mirror (F-194) — composite score below this threshold classifies a top-N position as "Legacy Overhang." Sits between `COMPOSITE_HOLD` (44) and `COMPOSITE_BUY` (65) — names positions that have drifted out of Buy territory without yet reaching the Hold floor. Display-copy only. |
| `CONVICTION_LEGACY_TOP_N` | 3 | Investor Mirror (F-194) — number of largest-weight positions inspected for Legacy Overhang. Display-classification only. |
| `BREAKEVEN_ANCHOR_DWELL_RATIO` | 1.3 | Investor Mirror (F-194) — `breakeven_anchoring()` flags an anchoring signal when the −2 to 0% bracket's weighted-average hold-time is ≥ this multiple of the mean of the adjacent loss brackets (−5 to −2% and −10 to −5%). A ratio of 1.3 means 30% longer dwell than adjacent brackets — deliberately conservative to avoid false positives on thin data. Display-copy only. |
| `PREMATURE_EXIT_RATIO` | 0.5 | Premature-Exit Cost (F-206, O6) — a winning closed lot held less than this fraction of the user's own average winner-hold time counts as a "quick exit" for the quick-vs-patient realized-gain comparison. Display-copy only, never gates. |
| `PREMATURE_EXIT_MIN_LOTS` | 5 | Premature-Exit Cost (F-206, O6) — minimum winning lots required in EACH of the quick-exit/patient buckets. Feature-specific, deliberately not `INVESTOR_MIRROR_MIN_CLOSED_LOTS` — that floor is sized for a larger, non-winners-only population; reusing it here would likely leave the card permanently dark on a personal-portfolio trade history. |
| `THESIS_EROSION_HAIKU_MIN` | 30 | Thesis Red Team Agent (Phase 2) — minimum erosion score (0–100) required to trigger a Haiku counter-evidence call for the position. Below this the erosion score is shown with no LLM narrative. Bounds the Phase 2 Haiku API cost to positions with material signal. **Phase 1 is inert** (no Haiku). |
| `THESIS_EROSION_BRIEF_MIN` | 50 | Thesis Red Team Agent (Phase 3, F-196) — erosion score threshold for a Daily Brief "Thesis Under Pressure" annotation. Fires when today's score meets or exceeds this floor AND the baseline row (most recent prior scored day, not literal calendar-yesterday) was below it, or — when no baseline row exists within `THESIS_EROSION_BASELINE_LOOKBACK_DAYS` (a first-ever observation) — when today's score alone meets this floor. Awareness annotation only — never a gate; deduped against tickers already shown as a deterioration/Act card in the same Brief. |
| `THESIS_EROSION_BRIEF_JUMP` | 15 | Thesis Red Team Agent (Phase 3, F-196) — a same-day score increase of this many points vs. the baseline row triggers the Daily Brief annotation regardless of whether the absolute score crosses `THESIS_EROSION_BRIEF_MIN`. Catches sudden deterioration before the absolute floor is reached. Awareness annotation only. |
| `THESIS_EROSION_BASELINE_LOOKBACK_DAYS` | 10 | Thesis Red Team Agent (Phase 3, F-196) — calendar days the Daily Brief annotation walks back to find the most recent trading day with a scored `thesis_erosion_cache` row (rows only exist on days the user visited AI Insights → Red Team, so literal calendar-yesterday is frequently blank). No row found within this window ⇒ treated as a first-ever observation, gated by `THESIS_EROSION_BRIEF_MIN` alone. |
| `DAY_SHOCK_PCT` | 5.0 | Day Shock banner (Home) — a held position moving ≥ this % (up or down) same-day triggers a "Day Shock" banner, independent of `classify_deterioration_tier`'s trend-break condition. Exists because a single-day move can occur while the position is still above its 50-day MA, where the deterioration tier stays silent. Awareness only — never alters WATCH/TRIM/EXIT tier or any recommendation. |
| `PORTFOLIO_THESIS_BASELINE_LOOKBACK_DAYS` | 14 | State of the Portfolio standing thesis (F-232, 🧾 Summary) — calendar days `app.py`'s Summary render walks back (via `db.load_portfolio_thesis()`) to find the most recent prior `portfolio_thesis` row before `portfolio_thesis.grade_prior()` grades this week's 5 claims as a HELD/SHIFTED stability ledger (mirrors `THESIS_EROSION_BASELINE_LOOKBACK_DAYS`'s precedent). Sized to cover exactly one missed week of app visits at the feature's weekly cadence. No prior row found within this window ⇒ "first standing view of the record — nothing to compare yet," never a fabricated grade. Never a gate; the ledger is a coherence/consistency read, never a right/wrong predictive score (§5.8-safe by design). |
| `PT_TARGET_LOOKBACK_DAYS` | 5 | F-169 Phase 2 (`stock_analyzer/analyst_targets.py::detect_pt_cut()`) — trading-day window (row-count, not calendar-date, to stay weekend/holiday-agnostic) the PT-cut comparison looks back over. Fewer than `LOOKBACK_DAYS + 1` distinct `analyst_target_snapshots` rows for a ticker ⇒ withheld, never a fabricated flat/neutral read. |
| `PT_TARGET_CUT_WARN_PCT` | -7.0 | F-169 Phase 2 — a consensus `target_mean` drop of this magnitude (or more) over the `PT_TARGET_LOOKBACK_DAYS` window fires a warning-level 🎯 alert in the "📉 Analyst Revisions" category even with no accompanying rating downgrade (suppressed if a rating-based revision already fired for the same ticker — see `portfolio.py::alerts()`). Opus-reviewed policy value; do not retune without a fresh review. |
| `PT_TARGET_CUT_DANGER_PCT` | -15.0 | F-169 Phase 2 — same comparison as `PT_TARGET_CUT_WARN_PCT`, danger tier. Opus-reviewed policy value; do not retune without a fresh review. |
| `MC_HISTORY_PERIOD` | `"5y"` | 🎲 Outcome Range simulator (Risk Analysis, `stock_analyzer/monte_carlo.py`) — the `data.fetch_price_history` period string used for the long-history fetch feeding the bootstrap. Nothing else in the app fetches beyond the 6-month `bundle_loader` default; this is a dedicated, separately-cached fetch path. |
| `MC_MIN_HISTORY_DAYS` | 252 | Outcome Range — minimum trading days (~1yr) of usable Close data a ticker needs to join the correlated bootstrap. Tickers below this (recent IPOs, fetch failures) are excluded and reported in the UI, never silently dropped; their weight is renormalized among the remaining tickers. |
| `MC_TRIALS` | 2000 | Outcome Range — number of block-bootstrap trials run per simulation. |
| `MC_BLOCK_DAYS` | 20 | Outcome Range — contiguous trading-day block length (~1 month) resampled together across ALL held tickers in a given trial, so a historically correlated move (e.g. a broad tech selloff day) hits correlated names together in the resample instead of being destroyed by independent per-ticker resampling. |
| `MC_HORIZON_OPTIONS_DAYS` | `[21, 63, 252]` | Outcome Range — selectable simulation horizons in trading days (~1mo/1qtr/1yr), rendered as a radio control. |
| `MC_HORIZON_DEFAULT_DAYS` | 63 | Outcome Range — default horizon selection (~1 quarter). |

Simulation-method parameters, not investment-policy thresholds — the Outcome Range tab is diagnostic/awareness-only (same class as Stress Testing and Regime Fit) and never gates a recommendation. It deliberately does NOT attach a probability to any macro regime — it resamples real historical daily returns instead, sidestepping the same thin-data problem (`daily_regime` has only ~3 days of history) that made F-200 drop a regime-probability framing.

| `QA_REC_OUTCOME_DEFAULT_HORIZON_DAYS` | 5 | 💬 Ask (Portfolio Q&A, `stock_analyzer/portfolio_qa.py`) — trading days after a recommendation's surfacing to check the price outcome, used when the parsed question doesn't specify one. |
| `QA_MAX_RANGE_DAYS` | 365 | Portfolio Q&A — widest date range a "trades in range" question may query, so an open-ended range can't fan out into an unbounded price-history fetch. |
| `QA_REC_OUTCOME_WIDE_FETCH_DAYS` | 330 | Portfolio Q&A — rec age (calendar days) past which the `rec_outcome` price-history fetch widens from `"1y"` to `"2y"`, so a recommendation old enough that a 1-year fetch wouldn't cover `rec_date + horizon_days` gets an honest outcome instead of misreporting "not enough forward history" when the real cause was a too-short fetch window. |
| `QA_HISTORY_TURNS` | 3 | Portfolio Q&A — most-recent Q&A exchanges fed back into `parse_question()` as conversation context (multi-turn follow-ups, 2026-08-02), bounding prompt size/cost. Only the parser sees this history; `narrate_answer()` still answers from ONLY the current query's facts. |
| `QA_PREMORTEM_TRADE_MATCH_WINDOW_DAYS` | 3 | Portfolio Q&A — window (calendar days on/after a recommendation's surface date) to search for the BUY trade it was acted on by, for the `rec_outcome` Pre-Mortem cross-reference (2026-08-02): narrates the recorded Pre-Mortem risk case/exit commitment against what actually happened, retrospectively only — never a new recommendation. |

Query-scoping parameters, not investment-policy thresholds — Portfolio Q&A is a retrospective narration layer over trade/recommendation history (see F-225 in requirements.md); it never gates or issues a recommendation.

**Session key:** `_qa_history` (Ask tab, `app.py`) — the tab's own conversational turn list (`{"question","answer","intent","facts",...}` per round), rendered via `st.chat_message`. This is a UI-local session_state key scoped to the Ask tab's own chat display — not a cross-feature publish/consume cache, so it isn't in CLAUDE.md's coordination-pattern cache-key list.

| `PERSONALIZED_DISCOVERY_MIN_MATCH_TRAITS` | 2 | Personalized Discovery (`stock_analyzer/personalized_discovery.py`, F-226) — of the 3 traits (composite band / momentum band / top sector), how many must match a candidate before the "matches your winning profile" caption renders on a 🏠 Home Grow Today pick. |
| `PERSONALIZED_DISCOVERY_PROFILE_PCTL_LOW` | 25 | Personalized Discovery — lower percentile of the user's own realized winning entries' composite/momentum scores defining the "typical winner" band. |
| `PERSONALIZED_DISCOVERY_PROFILE_PCTL_HIGH` | 75 | Personalized Discovery — upper percentile of the same band. |

New policy/method thresholds proposed alongside the feature, not investment-policy thresholds carried over from elsewhere — reuses the existing `BEHAVIORAL_MIN_SAMPLE_N` (above) for the min-sample withhold floor rather than a parallel constant. Diagnostic/awareness only — never changes which tickers clear the 5-gate `_grow_today()` pipeline, never re-scores or re-ranks a pick.

| `JUDGMENT_EXIT_SIGNAL_MAP` | dict: WATCH=-0.3, TRIM=-0.6, EXIT=-0.9, RISK_OFF=-0.9 | "🧑‍⚖️ The Judge" (Phase 0/1, `docs/plans/judgment-layer.md`) — maps an `exit_advisor` deterioration tier to a normalized `-1..+1` `position_health` opinion signal. |
| `JUDGMENT_FRAGILITY_SIGNAL_MAP` | dict: calm=0.3, caution=-0.3, fragile=-0.8 | The Judge — maps `fragility_gauge` severity to a normalized `structural_risk` opinion signal. |
| `JUDGMENT_VERDICT_SIGNAL_MAP` | dict: go=0.8, verify=0.0, caution=-0.4, skip=-0.9 | The Judge — maps a `signal_reconciliation` verdict tier to a normalized `quality` opinion signal. |
| `JUDGMENT_CONCENTRATION_BREACH_SIGNAL` | -0.8 | The Judge — `concentration` opinion signal when the largest name/sector is at or above `SINGLE_NAME_CEILING`/`SECTOR_CEILING`. |
| `JUDGMENT_CONCENTRATION_NEAR_BREACH_SIGNAL` | -0.3 | The Judge — `concentration` opinion signal when at/above `JUDGMENT_CONCENTRATION_NEAR_BREACH_RATIO` of a ceiling but not yet breached. |
| `JUDGMENT_CONCENTRATION_NEAR_BREACH_RATIO` | 0.8 | The Judge — "near" a hard ceiling is defined as 80% of `SINGLE_NAME_CEILING`/`SECTOR_CEILING`. |
| `JUDGMENT_CONCENTRATION_CLEAR_SIGNAL` | 0.3 | The Judge — `concentration` opinion signal below the near-breach ratio on both ceilings. |
| `JUDGMENT_VETO_PROTECTIVE_THRESHOLD` | -0.4 | The Judge — a protective-dimension opinion (`position_health`/`concentration`/`structural_risk`/`leverage`) at or below this hard-suppresses EVERY same-ticker positive acquisitive-dimension (`quality`/`momentum`) opinion outright, never blended (Q1's protective-veto routing class, added after the 2026-08-03 Opus design review found the original weighting-vs-contradiction dichotomy was a false split). The most severe (lowest-signal) protective opinion wins when more than one qualifies — fixed in the Phase 1 code review after an initial "first found" implementation was flagged as order-dependent. |
| `JUDGMENT_CONTRADICTION_MIN_MAGNITUDE` | 0.3 | The Judge — minimum `\|signal\|` for a same-dimension, opposite-sign opinion pair from two different sources to be flagged as a contradiction; avoids flagging near-neutral noise as a real conflict. |
| `JUDGMENT_SCORE_MIDPOINT` | 50.0 | The Judge — 0-100 score-scale midpoint used to normalize `composite_score`/`scanner_momentum` opinions to the `-1..+1` signal range via `(score - midpoint) / midpoint`. |
| `JUDGMENT_HORIZON_MOMENTUM_DAYS` | 5 | The Judge Phase 2 — trading days from `signal_date` before a `momentum` opinion is graded against realized forward alpha; mirrors the Entry Timing tab's Day+5 precedent for a short-term technical read. |
| `JUDGMENT_HORIZON_QUALITY_DAYS` | 20 | The Judge Phase 2 — grading horizon for `quality` opinions (composite_score/verdict_reconciliation); mirrors Entry Timing's Day+20 for a longer fundamental thesis. |
| `JUDGMENT_HORIZON_POSITION_HEALTH_DAYS` | 10 | The Judge Phase 2 — grading horizon for `position_health` opinions (exit_advisor WATCH/TRIM/EXIT/RISK_OFF); a near-term protective read, shorter than `quality`. |
| `JUDGMENT_HORIZON_CONCENTRATION_DAYS` | 20 | The Judge Phase 2 — grading horizon for portfolio-wide `concentration` opinions; paired to the same horizon as `quality` since overweight-position risk plays out on a similar timescale to a fundamental thesis, not a single-day shock. Proposed 2026-08-03, not yet observed against real data — flag if it feels wrong once grades accumulate. |
| `JUDGMENT_HORIZON_STRUCTURAL_RISK_DAYS` | 10 | The Judge Phase 2 — grading horizon for portfolio-wide `structural_risk` opinions (fragility_gauge); paired to the same horizon as `position_health` as a near-term protective read. Same caveat as `JUDGMENT_HORIZON_CONCENTRATION_DAYS`. |
| `JUDGMENT_TRACK_RECORD_NEUTRAL_ACCURACY` | 0.5 | The Judge Phase 3 — the accuracy (coin-flip) that yields a neutral 1.0x blend-weight multiplier; a witness's `track_record_summary()` accuracy is divided by this to derive its raw multiplier before clamping. |
| `JUDGMENT_TRACK_RECORD_WEIGHT_FLOOR` | 0.25 | The Judge Phase 3 — minimum blend-weight multiplier a witness's track record can apply, once its `(source, dimension)` pair clears `BEHAVIORAL_MIN_SAMPLE_N`; a poor track record can drop a witness to a quarter-weight but never fully silence it. User-confirmed "moderate" band, 2026-08-03. |
| `JUDGMENT_TRACK_RECORD_WEIGHT_CEILING` | 2.0 | The Judge Phase 3 — maximum blend-weight multiplier a witness's track record can apply; a strong track record can up to double a witness's say but never let it dominate the blend alone. Applies only inside the confidence-weighted blend — the protective veto and contradiction audit are never weighted. User-confirmed "moderate" band, 2026-08-03. |
| `COMPARE_TIE_GAP` / `COMPARE_FCF_YIELD_GAP_PCT` / `COMPARE_BETA_GAP` / `COMPARE_SHARPE_GAP` | 3 / 0.5 / 0.15 / 0.2 | ⚖️ Compare page 2-ticker verdict (`comparison.py`) — a composite gap below `COMPARE_TIE_GAP` defers to sub-factor tie-breakers, cited only when the FCF-yield/beta/Sharpe gap between the two tickers clears its own threshold. Were bare literals (2026-08-04 audit finding) |
| `SENTIMENT_VELOCITY_THRESHOLD` / `SENTIMENT_DIVERGENCE_PRICE_PCT` / `SENTIMENT_VELOCITY_MIN_ARTICLES` | 0.10 / 3.0 / 4 | `sentiment_velocity.py` — compound-score shift considered meaningful (Improving/Deteriorating label), 7-day price move % needed to flag a price-sentiment divergence, and min articles required to compute a velocity read at all. Were bare literals (2026-08-04 audit finding) |
| `PREMARKET_FUTURES_TONE_PCT` / `PREMARKET_MOVER_MIN_PCT` | 0.4 / 0.5 | `premarket.py` — ES=F % change cutoff for `futures_tone()`'s bull/bear/flat read, and the min \|% change\| for a held/watchlist ticker to qualify as a pre-market mover. Were bare literals (2026-08-04 audit finding) |
| `QUICK_RESEARCH_RSI_SEVERE_OVERBOUGHT` / `QUICK_RESEARCH_MOVE_1D_EXTREME_PCT` / `QUICK_RESEARCH_MOVE_5D_EXTREME_PCT` / `QUICK_RESEARCH_RSI_ELEVATED` / `QUICK_RESEARCH_MOVE_1D_ELEVATED_PCT` / `QUICK_RESEARCH_MOVE_5D_ELEVATED_PCT` / `QUICK_RESEARCH_RSI_OVERSOLD` | 80 / 15 / 25 / 68 / 5 / 12 / 35 | `quick_research._entry_timing()` — RSI/1-day-move/5-day-move cutoffs driving the directly-actionable entry-timing verdict ("High Risk — Avoid Chasing" / "Wait for Pullback" / "Oversold — Potential Entry" / "Normal Entry Conditions"). Were bare literals (2026-08-04 audit finding) |
| `TRADE_PRICE_SANITY_FLOOR` / `TRADE_PRICE_SANITY_RATIO_LOW` / `TRADE_PRICE_SANITY_RATIO_HIGH` / `TRADE_DUP_SUBMIT_WINDOW_SEC` | 0.10 / 0.5 / 2.0 / 15 | Trade Journal (app.py) anti-fat-finger guards — entered price below the floor, or too far (ratio) from the live market price, blocks submission as a probable typo (overridable); the dedup window rejects an identical (ticker, action, shares) resubmit within N seconds. Data-integrity, not an investment gate. Were bare literals despite the same form correctly importing `RR_ENTRY_MIN`/`COMPOSITE_BUY` from here (2026-08-04 audit finding) |
| `ALPHA_ATTRIBUTION_MIN_SNAPSHOT_DAYS` | 180 | AI Insights → Alpha Attribution panel (app.py) — daily-snapshot history needed before the factor-attribution decomposition activates. **As of F-247 (2026-08-21) the measured quantity is DISTINCT CAPTURED snapshot dates; it was previously the calendar span between the first and last snapshot.** The literal is unchanged but its unit is not, so the effective bar is materially stricter (~180 sessions is ~8.6 months of unbroken capture, and more with any gap) — a gapped history could previously clear a 180-day span while holding very little data. Display/activation-gate only, feature not yet active. Was 8 bare literal copies of "180" (2026-08-04 audit finding) |

These are the "-1..+1 signal cutpoints" and veto/contradiction boundaries the design
review explicitly required to live here rather than as inline literals in `app.py` —
same investment-policy-adjacent reasoning as any other threshold in this file, even
though Phase 1 has no authority to act on them yet (read-only, no gating). Confirm
before tuning. See §6.29 (`judgment_opinions` table) and
`docs/plans/judgment-layer.md` for the full design.

| `VOL_FORECAST_HORIZON_DAYS` | 20 | Predictive Modeling Shadow Layer, Phase 1 (F-234, MEASUREMENT-ONLY) — forecast target: N-trading-day forward realized volatility (annualized), per held ticker + the portfolio aggregate. Matches the maturation cron's `made_at + horizon_days` window and the backfill script's target-window length. **Model parameter, NOT a decision gate** — see §6.31 (`model_predictions`) and `docs/plans/predictive-modeling-shadow-layer.md`. |
| `VOL_FORECAST_EWMA_LAMBDA` | 0.94 | Predictive Modeling Shadow Layer — RiskMetrics' fixed EWMA decay factor for the v1 volatility forecaster (`vol_forecast.forecast_vol_ewma`). NOT fitted to this app's data — a classical constant, so v1 carries no backtest-leakage risk the way a fitted model (GARCH-MLE, gradient-boosted trees) would if it were ever backfilled. Model parameter, not a gate. |
| `PREDICTION_MIN_MATURED_N` | 20 | Predictive Modeling Shadow Layer — minimum matured (`realized_value` populated) `model_predictions` rows before `prediction_scoring.score_predictions()` reports a real `skill_score` number; below this, skill is withheld (`None`), same "not yet meaningful" discipline as `ENGINE_TRACK_MIN_CALLS`/`BEHAVIORAL_MIN_SAMPLE_N` elsewhere. Also reused (deliberately, not a parallel constant) as the floor for `skill_score_live_only`, so a headline skill number can't be inflated by a handful of live rows behind a mostly-backfilled sample. Measurement floor only — never a decision gate. |
| `PREDICTION_BACKFILL_PERIOD` | "5y" | Predictive Modeling Shadow Layer — depth of `scripts/backfill_vol_predictions.py`'s price-history fetch, per-ticker scope only (PORTFOLIO-scope backfill needs actual historical weights, bounded to known `trades` history, not 5 years — deliberately not built). Matches the existing `MC_HISTORY_PERIOD` constant/fetch-path precedent (Outcome Range simulator) rather than inventing a new one. |
| `IMPORTED_TRADE_ANCHOR_ET_HOUR` | 16 | Assumed ET fill time for an **imported** trade that carries a date but no time (broker sync / CSV / RH-text). Those writers sent a bare date, Postgres cast it to **midnight UTC** in the `timestamptz` column, and midnight UTC is the **prior evening in ET** — so every `tz_convert("America/New_York")` reader dated the trade a day early. Two live defects came from this: Today's P&L dropped a trade imported the same day (its cash leg never entered the delta), and `risk_advisor`'s buy/trim whiplash suppression **failed open**. `market_time.et_anchor_iso` stops new rows being written that way; `trade_time.normalize_traded_at` repairs existing ones at load. **The value is CONSTRAINED, not free: must be ≥ 0 and < 19.** At 19:00+ ET the anchor rolls into the next **UTC** day, which would silently re-date every UTC-date reader — including `tax_advisor`'s lot dates (a wash-sale or LTCG boundary can turn on one day) and `broker_sync`'s dedup key (re-importing every trade). 16:00 = market close: honest for an unknown fill time, and it sorts imports *after* the regular session, which also fixes a latent FIFO-replay bug. `tests/test_trade_time.py` pins the safe band so an edit past it fails loudly. Data-integrity constant, not an investment threshold. |
| `BROKER_DRIFT_SHARE_TOL` | 0.001 | Share-count tolerance below which a held ticker counts as matching rather than a real quantity mismatch, absorbing fractional-share rounding noise. Data-integrity tuning, not an investment threshold. **TWO consumers share this one number** — deliberately, because it is a single question ("is this share difference real or float noise") rather than two policies: **(1)** SnapTrade broker integration (`stock_analyzer/broker_sync.py`, `docs/plans/snaptrade-broker-integration.md`) — `diff_positions()` comparing the live broker feed against the app's `trades`-derived shares; **(2)** F-250 day-P&L integrity (`stock_analyzer/daily_pnl.py`) — `reconcile_baseline()` comparing the prior-close snapshot baseline against current holdings. Retuning this for the broker feed also moves the day-P&L guard; if the two ever need to diverge, split them rather than compromising on one value. |
| `SNAPTRADE_BALANCE_STALE_HOURS` | 25 | SnapTrade broker integration — max age of the last successful SnapTrade balance sync before the 💰 Account page shows a stale-data banner rather than trusting an old `account_cash` row. 25h (not 24h) mirrors the existing daily-cron-lane staleness convention elsewhere, absorbing normal cron-fire jitter past a strict 24h cycle. Display-only staleness gate. |
| `SNAPTRADE_SYNC_MAX_TXN_LOOKBACK_DAYS` | 90 | SnapTrade broker integration — bounds the `broker` cron lane's transaction-history fetch window (days back from now). Prevents an unbounded historical pull on first connect or after a long SnapTrade/cron outage; anything older is expected to already be in `trades` via manual/CSV entry. Data-integrity/operational bound, not an investment threshold. |
| `SNAPTRADE_REQUEST_TIMEOUT_SEC` | 15 | SnapTrade broker integration — per-call wall-clock timeout for `stock_analyzer/snaptrade_client.py`. Same operational-cap convention as `DATA_YF_REQUEST_TIMEOUT_SEC` — bounds a single hung SnapTrade call so the `broker` cron lane fails loud instead of blocking the job budget. |

### 4.0.2 Cross-feature coordination caches

Features publish to `st.session_state` when they own a piece of decision state; downstream features read it. When the producer fails, the consumer treats the absence as an "offline" state — not as "no constraint."

| Cache key | Producer | Consumers | Purpose |
|---|---|---|---|
| `_port_risk_cache` | Portfolio page (`compute_portfolio_risk_metrics`) | Stock Analysis Trade Plan, Watchlist `_portfolio_risk_gate` | Beta envelope checks across pages |
| `_risk_high_alerts_cache` | Portfolio page (after `build_risk_advisor_recommendations`) | Watchlist | ENTER_NOW gates against active HIGH risk alerts |
| `_grow_today_sectors_cache` | After `build_daily_briefing` | Watchlist `_portfolio_risk_gate` | Sector-overlap warning when both features pick the same sector |
| `_grow_composites` | Portfolio page (top-5 scanner pre-fetch + mover bundles) | `_grow_today` composite gate; Diversification Advisor ADD candidates (`annotate_add_candidates`) | Validates new picks AND movers against composite score, not just momentum; also cross-validates rebalance ADD candidates so the sector-tilt suggestion and the quality engine give one read |
| `_grow_composites_coverage` | Portfolio page | Grow Today UI | "Composite scores unavailable" banner when pre-fetch failed |
| `_movers_candidates` | Portfolio page (`_cached_scan_movers` → composite-gate) | `_grow_today` via `movers=` arg | Discovery breakouts fed into the unified New Positions list |
| `_daily_brief_offline` | Portfolio page (on `build_daily_briefing` exception) | Watchlist | Surfaces explicit offline state instead of silently disabling gates |
| `_reduce_calls` | After `build_daily_briefing` (`decision_bucket.reduce_call_items`) | Stock Analysis Trade Plan | Held names under a Reduce/Exit call → suppress add-on sizing + "not a place to add" banner, so Analysis can't say "add" while the Brief says "reduce" |
| `_acct_gate_cache` | Portfolio page (concentration-gate wiring, ~app.py:2710) | Grow Today 15%/35% suppression, entry nudge, watchlist/quick-research/comparison entry-fit | One number all gate consumers read for the concentration basis + denom. **Now `basis="equity"`, `denom=invested equity`** (2026-07-09 — reqs G-19); the net-capital path is retired |
| `_leverage_cache` | Portfolio page (same wiring point) | 🔗 Risk Analysis leverage read (+ 💰 Account ⚖️ note) | Margin/leverage AWARENESS (`{levered, margin_debit, net_capital, equity, ratio, stale}`) — read-only, **never gates** (F-09d) |
| `_day_shock_cache` | Home page (after `_price_strip`) | Home Day Shock banner (currently the only reader) | Held tickers moving ≥`DAY_SHOCK_PCT` same-day, `{ticker: {price, prev_close, chg_pct}}` — AWARENESS, **never gates** |

### 4.0.3 Coordination gates currently enforced

| From → to | Gate | Behaviour when fired |
|---|---|---|
| Risk Advisor TRIM → Grow Today add-to-winner | Suppress add on trim-targeted ticker | Amber banner: "Add-to-Winner Suppressed — Risk Advisor Conflict" |
| Risk Advisor TRIM → Rebalancer ADD | Suppress add on trim-targeted ticker | Amber banner: "Rebalance ADD Suppressed — Risk Advisor Conflict" |
| News Intelligence alert → Rebalancer ADD | Attach news_warning; critical drops urgency | Banner inside the add card; critical labelled "Defer Add" |
| Brief Reduce/Exit call → Overview Opportunity Signals | Drop the name from the "add on a pullback" lane (reads the published `_reduce_calls`, built by `reduce_call_items` — same `_is_reduce`/`_ticker` canon as the Act-bucket reconciler) | Amber "⚠️ NOT SHOWN AS ADDS" note lists the names; full headline stays under "All News for Your Holdings" |
| Brief Reduce/Exit call → Analysis Trade Plan (held name) | Suppress the add-on Position Sizing block (`reduce_call_items` → `_reduce_calls`; sibling to the stop-breach suppression) | Amber "⚠️ Under a Reduce/Exit call — not a place to add" banner; composite Buy score kept (rates the stock; the exit protects the position) |
| Brief Reduce/Exit call → My Edge Orphan Conviction | Exclude the ticker from the "size up" sizing-opportunity list (`decision_bucket.suppress_orphans_under_reduce_call`, reads `_reduce_calls`); filtered before publishing `_mirror_orphans`, so Analysis's 🪞 Investor Mirror banner (which reads that cache) can't ALSO contradict its own "Under a Reduce/Exit call" banner for the same ticker | Amber "⛔ excluded" note on the My Edge Conviction Alignment card naming the active reduce call (2026-07-30 — the BKNG incident: buying on this cue pushed weight over the earnings-overweight-trim threshold the same week) |
| Brief Act/Review (any card) → Grow Today add-to-winner (`add_positions`) | Exclude the ticker from an add-on-existing-position pick (`decision_bucket.all_flagged_tickers(act_today, review_list)`, broader than `_reduce_calls` by design — a WATCH card also blocks, per the 2026-07-29 audit H6 precedent) | No card surfaced for that ticker; previously only `act_today` was checked, so a review-origin trim (earnings-overweight, weak-large) never suppressed this pick |
| Brief Act/Review (any card) → Buy Candidates add-to-winner (`add_winner`) | Same `all_flagged_tickers` exclusion, independently applied — `_buy_candidates()` has its own separate add-to-winner block from `_grow_today`'s, with its own previously act_today-only `_act_blocked` set | No card surfaced for that ticker (2026-07-30 fix, found while closing the Orphan Conviction gap above — same root cause, a third instance) |
| Rebalancer drift-trim → Grow Today add-to-winner | Suppress add on drift-overweight ticker | Concentration-blocked banner |
| Single-name ceiling (15%) → Grow Today add-to-winner | Suppress add | Concentration-blocked banner |
| Deterioration WATCH → Grow Today add-to-winner | Suppress add on a held name under an active early-deterioration WATCH (entry-price-agnostic; chosen over a profit/P&L gate) | "🟡 Add Suppressed — Early Deterioration Watch" banner; ticker also joins the More-Buy-Candidates dedup so it can't reappear as "ADD — Winning Position" (changed 2026-07-21 from annotate-only — the FSLR case) |
| Sector ceiling (35%) → Watchlist ENTER_NOW | Downgrade to NEAR_ENTRY | "Portfolio Fit Blocks Entry" card |
| Imminent macro event → Grow Today new picks | Suppress picks in affected sector | "Picks Suppressed — Imminent HIGH-Impact Macro Event" banner |
| Held position composite Buy → Tax Advisor HARVEST | Suppress; action becomes `HOLD_FOR_SIGNAL` | "Harvest Suppressed — Investment View Holds" banner |
| Grow Today sectors → Watchlist ENTER_NOW | Soft warn on same-sector overlap | Caution text in ENTER_NOW card |

### 4.0.4 Multi-source market-data layer (failover + price cross-check)

The app was historically 100% dependent on yfinance (unofficial, no SLA) for all market data. The `providers/` package + `data.py` orchestrator remove that single point of failure. `data.py` keeps the same public functions (`fetch_ticker_bundle`, `fetch_live_prices`, `fetch_price_history`, `fetch_market_indices`, `fetch_risk_free_rate`) so **nothing above `data.py` changed**; it routes through the orchestrator when `DATA_MULTISOURCE_ENABLED` is True, else calls yfinance directly (byte-for-byte the pre-provider path — instant rollback).

**Providers & capabilities** (a provider serves only what it advertises; the orchestrator builds a per-data-type chain from whichever configured providers — key present — advertise the matching capability):

| Provider | `name` | Capabilities | Role |
|---|---|---|---|
| Finnhub | `finnhub` | live_price | **Live-price primary** (real-time US quotes); cross-check source |
| yfinance | `yahoo_finance` | live_price, history, bundle, indices, risk_free | History/bundle/indices primary; live-price failover |
| FMP | `fmp` | live_price, history, bundle | Failover for live price, history, and the full analysis bundle |

**Failover chains** (tried in order, first configured + capable + non-empty wins):
- **Live price** → `DATA_LIVE_PRICE_ORDER` = Finnhub → yfinance → FMP. *Gap-fill*: the primary fills most tickers; later providers fill only those still missing. Finnhub is primary because its free tier serves real-time quotes (yfinance is ~15-min delayed); a Finnhub outage silently degrades to yfinance, never worse.
- **History / bundle / indices / risk-free** → `DATA_PROVIDER_ORDER` = yfinance → Finnhub → FMP. yfinance stays primary (free, unquota'd, broad coverage); FMP is the safety net when a yfinance call hard-fails (e.g. rate-limited). The broad scanner/movers scans deliberately stay on yfinance (Finnhub's per-symbol quote would blow its 60/min limit on ~200 names). A narrower capability, `historical_close()` (arbitrary historical-date close lookup, used by the Research Scorecard's anchor-price fetch), also chains yfinance→FMP for tickers with no Yahoo data.

**Price cross-check** (`orchestrator.crosscheck_batch` / `crosscheck_price`, surfaced on the Portfolio page, cached 5 min):
- Validates the live-price primary against an INDEPENDENT source. **`prev_close` is checked strictly** (`DATA_XCHECK_PREVCLOSE_TOL_PCT` 0.5%) — a settled value that must match across sources, so a breach is a real integrity fault (missed split, wrong-symbol mapping, poisoned feed). **Live price is checked loosely** (`DATA_XCHECK_LIVE_TOL_PCT` 3.0%) because a delayed validator legitimately differs from a real-time primary intraday. A breach renders a fail-loud red banner ("Price unverified — sources disagree").
- **Widening history (F-126, Information Asymmetry Detector Phase 1, shipped 2026-07-24):** the cross-check result is additionally persisted to `price_xcheck_history` (§6.27), one row per (ticker, ET trading day), written from the interactive Home page path only (day-deduped via `st.session_state`, never from cron — see §6.27 for why). When a held ticker is currently failing cross-check, its banner bullet is annotated per failing leg with "widened from X% to Y% since `<date>`" when the most recent prior row (21-day lookback) shows a smaller gap. `data.divergence_widened()` is the pure diff+threshold helper. Display-only — never changes whether the banner fires.

**Secrets:** `FINNHUB_API_KEY`, `FMP_API_KEY` (Streamlit Cloud secrets). `_util.get_secret` reads top-level first, then tolerates a key mis-nested under a `[section]` (a common TOML mistake), then falls back to an env var (offline `selftest`). A missing key → provider reports unconfigured and is skipped (no error).

**Source transparency:** every live-price record carries a `source` tag; the price-strip caption shows the actual source(s) ("Finnhub (real-time)" / "Yahoo Finance (15-min delayed)" / "FMP"), and the Data Health sidebar tracks per-provider call/error/rate-limit counts via `api_health`.

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
        scoring.combined_score(t_score, bq_score, val_score, s_score) → total (0–100)
        scoring.recommendation(total) → {label, color, icon, rationale}
                │
                ▼
        held_data[ticker] = {df, t_score, bq_score, val_score, s_score, total, rec,
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
    held_data, scanner_results, portfolio_value, market_context,
    movers=_movers_candidates           ← discovery breakouts (composite-gated)
)
        │
        ├── Act Today     ← stop_breach / sell_signal / critical_news / macro / risk
        │       each item: {kind, directive, why, trigger, [risk_flags]}
        │       _consolidate_act_today(): mechanical exit (stop_breach/sell_signal)
        │         suppresses risk-trim on the same ticker; multiple risk flags
        │         on one ticker merge into one card; back-compat `reason` synthesised
        ├── Buy Candidates ← scanner picks, each cross-referenced via _cross_reference()
        │       (de-duped in app.py against everything already shown in Grow Today)
        ├── Grow Today    ← market-tone-aware new picks + add-to-winners
        │       ├── curated_rows: momentum-gated, truncated to max_picks*4, ranked
        │       │     ├── Bull day: score ≥ 65, up to 3 picks
        │       │     └── Flat day: score ≥ 78, max 1 pick, confirmed before unverified
        │       └── mover_rows:   composite-gated (≥65), ranked by composite,
        │             OWN MOVER_MAX_PICKS allowance, NOT momentum-gated, NO
        │             1-per-sector rule, EXEMPT from flat-day suppression;
        │             bear day → early return (movers never processed)
        └── Review Before Close ← approaching stops, earnings, weak large positions
                each item: {headline, action{type}, why, trigger}
                action.type ∈ WATCH / TIGHTEN_ONLY / TRIM_AND_TIGHTEN /
                              TRIM_TO_TARGET / PROTECTIVE_TRIM

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
composite_score = (technical_score      × 0.25)
                + (business_quality_score × 0.35)
                + (valuation_score       × 0.30)
                + (sentiment_score       × 0.10)
```

All component scores are on a 0–100 scale.

Analyst consensus (avg_pt + rating label aggregated from the `analyst_coverage` table) feeds the Valuation pillar score. Individual Ideas Inbox records remain display/awareness context only; only aggregated consensus metrics enter scoring.

### 5.2 Recommendation Tiers

| Score | Signal | Action |
|-------|--------|--------|
| ≥ 75 | ⬆⬆ Strong Buy | All four dimensions aligned bullish |
| 65–74 | ⬆ Buy | Most signals positive; favourable entry |
| 44–64 | ➡ Hold | Mixed signals; maintain position, no new entry |
| 30–43 | ⬇ Sell | Weakening; consider reducing |
| < 30 | ⬇⬇ Strong Sell | Multiple bearish signals; elevated downside risk |

### 5.3 Business Quality Score — Sector-Relative Benchmarks

Business Quality covers revenue growth, earnings growth, profit margins, and debt/equity. Revenue growth, profit margin, and P/E norms are sector-relative so high-growth companies are not structurally penalised vs value sectors. P/E and FCF Yield have moved to the Valuation pillar (`valuation.py`).

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

Earnings growth and debt/equity retain universal thresholds. The sector P/E norms table is also consumed by the Valuation pillar for its Forward P/E metric.

### 5.3a Valuation Score Components (0–100)

| Component | Max Pts | Notes |
|-----------|---------|-------|
| Forward P/E (sector-relative) | 25 | Uses same `_SECTOR_NORMS` as BQ pillar |
| FCF Yield | 20 | ≥5%=20pts; ≥3%=15; ≥1%=8; ≥0%=3; <0%=0 |
| PT Upside to consensus avg target | 25 | ≥30%=25; ≥15%=20; ≥5%=12; ≥0%=6; ≥−5%=2; <−5%=0 |
| Analyst consensus rating | 30 | Strong Buy=30; Buy=24; Hold=15; Mixed=9; Sell=0 |

### 5.4 Upgrade/Downgrade Trigger Computation

For each pillar p with score Sp and weight Wp, the score needed to reach the next verdict threshold T is:
  S_p_needed = (T − Σ(other pillars Sk × Wk)) / Wp
Rendered in the "📈 What would change this signal?" expander (app.py ~12148).
Pillar triggers sorted by ascending gain needed (easiest lever first); top 2 shown.
Downgrade buffer = composite − current_threshold; most-impactful pillar = max(Wp).
Constants: COMPOSITE_STRONG_BUY=75, COMPOSITE_BUY=65, COMPOSITE_HOLD=44, COMPOSITE_SELL=30; COMPOSITE_WEIGHTS keys technical/business_quality/valuation/sentiment.

### 5.5 Gate Checklist Display

Five pass/fail checks shown in Trade Plan tab (Buy/Strong Buy branch only). Data sources:
- Data Quality: bundle bq_available (FUNDAMENTALS_GATE_MIN_METRICS=1 of 4 BQ keys)
- R/R: rr_val computed from risk_reward(); gate = RR_ENTRY_MIN=2.0
- Concentration: equity Gate Weight % from _port_df_enriched; gate = SINGLE_NAME_CEILING=15.0%
- Sector cap: sector weight from _port_df_enriched; gate = SECTOR_CEILING=35.0%
- Earnings: days from r["earnings"] to today_et(); gate = EARNINGS_IMMINENT_DAYS=7d
Shows "—" (grey) when account or catalyst data not loaded in session. Display-only — does not modify any gate decision.

### 5.6 Technical Score Components (0–100)

| Component | Max Pts | Key Thresholds |
|-----------|---------|----------------|
| RSI (14-period) | 20 | <30 oversold=18; <45=14; <55=10; <70=6; overbought=2 |
| MACD histogram | 20 | Positive & rising=20; positive=14; improving=8; falling=2 |
| Price vs SMA 20/50 | 20 | Price>SMA20>SMA50=20; Price>SMA20=14; Price>SMA50=8 |
| Bollinger Band position | 20 | Below lower=18; lower-mid=14; mid-upper=8; above upper=2 |
| OBV trend | 20 | Rising=20; neutral=10; falling=2 |

### 5.7 Scanner Score Components (0–100)

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
    lesson           TEXT,                       -- free-text exit notes (post-trade)
    lesson_category  TEXT,                       -- F-195: structured exit-pattern label from fixed taxonomy
    user_thesis      TEXT,                       -- F-1: investor's conviction at entry (reviewed weekly by AI Insights)
    thesis_source    TEXT,                       -- F-5: 'manual' | 'ai_draft' | 'ai_edited' (thesis-draft provenance)
    decision_context JSONB,                      -- Concept E Phase 1: frozen state-of-the-world snapshot at interactive write (schema-versioned)
    premortem_case_against JSONB,                 -- F-187: 3 LLM-generated counterarguments against a BUY, or null if the call failed/was skipped
    premortem_commitment   TEXT,                  -- F-187: investor's required "what would make me wrong" answer (never null on a BUY once recorded)
    premortem_trigger_price     NUMERIC,          -- F-228: raw (pre-split) price extracted from premortem_commitment, or null if not yet extracted / not checkable
    premortem_trigger_direction TEXT,             -- F-228: 'below' | 'above' | 'not_checkable' | null (null = not yet extracted, distinct from 'not_checkable')
    traded_at        TIMESTAMPTZ DEFAULT now(),
    broker_txn_id    TEXT                          -- F-244: SnapTrade transaction id, Tier-1 dedup key (null for manual/CSV rows)
);

CREATE UNIQUE INDEX IF NOT EXISTS trades_broker_txn_id_unique
    ON trades (broker_txn_id) NULLS DISTINCT;
```

The `signal_seen`, `followed_signal`, `deviation_reason`, `lesson`, `lesson_category` (F-195), `user_thesis` (F-1), `thesis_source` (F-5), `decision_context` (Concept E), `premortem_case_against`/`premortem_commitment` (F-187), `premortem_trigger_price`/`premortem_trigger_direction` (F-228), and `broker_txn_id` (F-244) columns were added after initial deployment. `db.load_trades()` backfills `None` for these columns in older rows to maintain backward compatibility. `save_trade` additionally retries the insert without whichever of `thesis_source`/`decision_context`/`premortem_case_against`/`premortem_commitment`/`premortem_trigger_price`/`premortem_trigger_direction`/`lesson_category`/`broker_txn_id` the DB error names, so trade logging never breaks before the one-time additive `ALTER TABLE trades ADD COLUMN ...` DDL is applied — **except** a `broker_txn_id` UNIQUE-violation, which is checked BEFORE that generic retry and treated as an idempotent no-op (the same transaction was already logged), so the bare-substring match on the column-degradation path can never strip the dedup key and silently insert a duplicate row (a 2026-08-17 review finding).

**Pre-Commitment Enforcement (F-228 — docs/plans/premortem-enforcement.md).** Actively monitors the F-187 `premortem_commitment` free text against live price data instead of only ever quoting it back as passive narrative context (Thesis Red Team, Portfolio Q&A). `stock_analyzer/premortem_monitor.py::extract_trigger()` (Haiku, ONE-SHOT at BUY-submission time — never on every rerun, never retried) parses the commitment into a structured, checkable fact: `premortem_trigger_direction` is only ever `'below'`/`'above'` when an explicit numeric price was stated; a qualitative commitment ("if guidance disappoints") is marked `'not_checkable'` — a genuine, permanent state, not "not yet attempted" (that's the `NULL`/`NULL` state, distinguished so a transient extraction failure is never confused with "nothing to check"). `detect_premortem_triggers()` (pure Python, zero LLM cost, called once per Daily Brief build alongside `deterioration_signals()`) then compares each ticker's *currently open lot* (via the split-ratio-aware `tax_advisor._build_open_lots()`, so a sell-then-rebuy never resurfaces an unrelated closed lot's trigger, and a stock split never leaves a stale pre-split price) against its price history since that lot's `buy_date`. A trigger only fires while the **most recent daily close** is still beyond the level (self-resolving — a recovered position stops firing on its own, no acknowledge/snooze mechanism) — never "crossed at any point since BUY," which would nag forever after a recovered dip. Surfaces as a first-class `kind="premortem_triggered"` Act Today card, deliberately shown ALONGSIDE any existing WATCH/TRIM/EXIT card on the same ticker rather than merged (`_consolidate_act_today()` exempts this kind from its ticker-consolidation entirely) — the investor's own stated condition firing is a genuinely different reason from the algorithm's deterioration read. Never suppresses, gates, or modifies any recommendation.

**Pre-Mortem Protocol (F-187 — entry friction, never a gate).** Before a prospective LIVE Buy is recorded, an OUTSIDE-the-form "🔍 Run Pre-Mortem" section (mirrors the F-5 thesis-draft button's placement) lets the investor generate an app-side case AGAINST the buy: `stock_analyzer/premortem_advisor.py::generate_case_against()` calls Haiku (`claude-haiku-4-5-20251001`) with the composite pillar driving the score + its specific signals (`driving_pillar_from_bundle()`), the portfolio's current sector concentration, the macro regime (best-effort session-state read), and earnings-context evidence (beat rate / recent reaction / CNBC watch-items via `db.load_earnings_context`) — the prompt requires exactly 3 counterarguments (pillar / portfolio / macro angles), each citing specific evidence, rejecting anything generic; a strict JSON-array parser (`_parse_case_against`) discards a malformed or short response, returning `None`. Generated ONCE per explicit button click (not on every rerun, so typing in the commitment box can't re-trigger the LLM). The **hard requirement is independent of the LLM**: a `st.text_area` "What would make me wrong about this?" is enforced as one more validation gate in the same elif chain as the existing ticker/shares/price checks — a missing/failed case-against never blocks the trade. A case-against generated for a since-changed ticker is discarded at submit time (never attached to the wrong trade). Scoped to this one interactive form: broker CSV/screenshot imports, split rows, and the `recalculate_from_trades` replay assemble their own record dicts elsewhere and never see this gate; SELL is untouched (exit friction is bad; entry friction is the point).

**`decision_context` (Concept E, Phase 1 — passive capture).** A schema-versioned, JSON-safe snapshot frozen at each *interactive* Trade Journal write (live Buy, or Sell on confirm), built by the pure `stock_analyzer/decision_context.py::build_snapshot()`. Captures the composite verdict seen, macro regime, portfolio beta / high-beta share / top-sector concentration / position count, and active-recommendation load — none of which can be reconstructed after the fact. None-safe and I/O-free (reads only session state). Scoped to interactive writes ONLY: broker/screenshot/split imports and the `recalculate_from_trades` replay assemble their own record dicts and never carry a snapshot (a retroactive/batch write has no live decision context). The retrospective "View context" viewer is deferred to Phase 3, once history has accrued.

### 6.4 `manual_stops` table

```sql
CREATE TABLE manual_stops (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker        TEXT    NOT NULL UNIQUE,
    stop_price    NUMERIC NOT NULL CHECK (stop_price > 0),
    set_at        TIMESTAMPTZ DEFAULT now(),
    note          TEXT,
    source_action TEXT       -- e.g. 'review_tighten_only' / 'review_trim_and_tighten' / 'manual'
);
```

Backs the **Action Log** (recommend→act→log loop). When the user acts on a "raise stop" recommendation, the chosen level persists here and overrides the computed ATR/ratchet stop in `portfolio.build_portfolio_df(holdings, loaded_data, manual_stops=...)`. Helpers: `db.load_manual_stops()`, `db.save_manual_stop(ticker, stop_price, note, source_action)`, `db.clear_manual_stop(ticker)`. Override is **one-directional** — honoured only when ≥ the current computed stop (tighten-only). `db.save_holdings` symmetric-sweeps this table so a stop override is auto-cleared (orphan cleanup) when a ticker's shares drop to 0; the sweep is wrapped in its own try so a missing table never blocks a holdings save.

### 6.5 `daily_snapshots` table

```sql
CREATE TABLE daily_snapshots (
    snapshot_date DATE    NOT NULL,
    ticker        TEXT    NOT NULL,
    shares        NUMERIC NOT NULL CHECK (shares > 0),
    close_price   NUMERIC NOT NULL CHECK (close_price > 0),
    created_at    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (snapshot_date, ticker)
);
```

**Tier B day-P&L prior-close baseline.** Captures the end-of-day close for each held position once per trading day (weekday ET ≥ 16:00, post-close). Used by `daily_pnl.compute_positions_day_pnl()` to compute broker-style equity-delta for the day (current marked value − baseline value + today's trades cash). **Optional** — the table is created lazily; if absent, the app degrades to the held-only "Today's P&L (held)" mark (see Known-Behaviours row "Today's P&L — held mark vs Tier B day P&L"). RLS: `FOR ALL TO service_role` like other tables. Written via `db.save_daily_snapshot(snapshot_date, rows)` (read-only-viewer no-op; a missing table is silent no-op, fully backward-compatible).

### 6.6 `bundle_cache` table

```sql
CREATE TABLE bundle_cache (
    ticker     TEXT    PRIMARY KEY,
    history_json TEXT   NOT NULL,
    info       JSONB   NOT NULL,
    fetched_at TIMESTAMPTZ DEFAULT now()
);
```

**Last-known-good resilience cache.** `db.save_bundle_cache` write-throughs raw history+info on every successful `load_all`; `db.load_bundle_cache` serves it (age-gated by `BUNDLE_CACHE_MAX_AGE_DAYS`) when all providers fail. **Optional** — until created, `load_all` keeps its honest 'Could not load' failure. When the cache exists and all live providers are down, a bundle ≤ `BUNDLE_CACHE_MAX_AGE_DAYS` old is served with a `stale_as_of` tag + Home staleness banner; news/earnings degrade to empty in that mode; cache I/O is wrapped so it can never break the success path; the stale result is TTL-cached (~30 min) to avoid hammering the disk. RLS: `FOR ALL TO service_role` like all tables; `save_bundle_cache` is read-only-viewer no-op.

### 6.7 `account_cash` table

```sql
CREATE TABLE account_cash (
    id           INTEGER PRIMARY KEY,   -- always 1 (single row, single user)
    cash_balance NUMERIC NOT NULL DEFAULT 0,
    note         TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Account-baseline NET cash (single row).** Holds the user-entered uninvested cash. The value is **signed: NEGATIVE = a margin debit** (account-baseline v4) — so `Total Account Value = Σ Market Value + cash` nets out any margin loan, and everything derived from it (true concentration, growth, return) nets it too. `db.load_account_cash()` / `save_account_cash()` (writer is read-only-viewer no-op — USER data). **Optional** — until created, load returns None and the app behaves as today (invested-equity only, with a nudge to set cash). The same field the Robinhood MCP sync would later auto-populate. RLS: `FOR ALL TO service_role`.

Since the concentration gates are **equity-basis** (2026-07-09 — reqs G-19), `_acct_gate_cache.basis` is always `"equity"`: the **Sector Exposure** chart and **Composition Sankey** show the equity view and flag concentration (35% sector / 15% single-name) against invested equity — the net-capital overlay/flag branches remain conditioned on `basis ∈ {account, over-levered}` and are therefore inert (no net-capital claim renders). Leverage/margin risk is instead an **awareness-only** read on the **🔗 Risk Analysis** tab (`_leverage_cache`; F-09d) + the 💰 Account ⚖️ note — never a gate. (These conditional branches are retained as the seam so the basis stays a one-line policy choice; do not re-enable net-capital gating without a policy discussion.)

### 6.8 `account_flows` table

```sql
CREATE TABLE account_flows (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    flow_date  DATE    NOT NULL,
    flow_type  TEXT    NOT NULL,        -- 'baseline' | 'deposit' | 'withdrawal'
    amount     NUMERIC NOT NULL,        -- always POSITIVE; type carries the sign
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**External cash-flow ledger (account-baseline v2/v3).** Separates contributions from performance: `baseline` = the opening contributed-capital anchor, `deposit`/`withdrawal` = external cash in/out. Net Contributed Capital = baseline + Σ deposits − Σ withdrawals; **Growth $** = total account value − NCC; **money-weighted (Modified Dietz) return** + annualized (period ≥ 30d) over the tracked window. Pure calc in `stock_analyzer/account.py`. `db.load_account_flows()` / `add_account_flow()` / `delete_account_flow()` (writers are read-only-viewer no-ops). **Optional** — until created, load returns `[]` and the growth view stays hidden. **Display-only — feeds no gate.** RLS: `FOR ALL TO service_role`.

### 6.9 `thesis_reviews` table

```sql
CREATE TABLE thesis_reviews (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker      TEXT        NOT NULL,
    trade_date  DATE,                          -- the BUY lot whose thesis is reviewed
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT        NOT NULL,           -- 'INTACT' | 'WEAKENING' | 'BROKEN'
    summary     TEXT,                           -- ~100-word LLM read
    inputs_hash TEXT,                           -- staleness key (skip re-review when inputs unchanged)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**AI Intelligence F-1 thesis reviews (append-only history).** For each held position carrying a `trades.user_thesis`, an LLM grades the original conviction against current evidence and stores INTACT/WEAKENING/BROKEN + a short summary. **Append-only** (`save_thesis_review` inserts, never upserts) so review history accumulates; `inputs_hash` lets the app skip a re-review when nothing material changed. `db.load_thesis_reviews()` / `save_thesis_review()` (writer is read-only-viewer no-op). **Surfaced on 🧠 AI Insights only — no chip on core pages, and BROKEN does NOT issue an exit** (the rule-based deterioration ladder fires independently). Optional — inert until the DDL is applied. RLS: `FOR ALL TO service_role`.

### 6.10 `weekly_debriefs` table

```sql
CREATE TABLE weekly_debriefs (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    week_ending       DATE        NOT NULL UNIQUE,
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    performance_pct   NUMERIC,                  -- portfolio % for the week
    spy_pct           NUMERIC,                  -- SPY % for the week
    alpha_pct         NUMERIC,                  -- performance_pct − spy_pct
    section_facts     TEXT,                     -- "What happened"
    section_decisions TEXT,                     -- "Decisions you made"
    section_patterns  TEXT,                     -- "Patterns this week"
    section_watchnext TEXT,                     -- "One thing to watch"
    email_sent        BOOLEAN     NOT NULL DEFAULT false,
    email_sent_at     TIMESTAMPTZ
);
```

**AI Intelligence F-3 weekly debrief (one row per week).** A 4-section narrative the LLM writes from a Python-assembled package (portfolio-vs-SPY + alpha, contributors/detractors, recs surfaced vs. acted). **Unique on `week_ending`** → upsert-safe. Emailed Sunday (Resend) via the thesis cron lane + on-demand. `db.load_weekly_debriefs(limit)` / `save_weekly_debrief()` (writer is read-only-viewer no-op). The in-app view adds a weekly alpha-trajectory bar once ≥ 2 weeks exist. Optional — inert until the DDL is applied. RLS: `FOR ALL TO service_role`.

### 6.11 `monthly_reports` table

```sql
CREATE TABLE monthly_reports (
    id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_start              DATE        NOT NULL,
    period_end                DATE        NOT NULL UNIQUE,
    generated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    engine_alpha_pct          NUMERIC,                 -- mean alpha across acted picks (Q0 headline)
    acted_count               INTEGER,                 -- DISTINCT acted New-Position tickers
    missed_count              INTEGER,                 -- DISTINCT not-acted New-Position tickers
    section_entry_quality     TEXT,                    -- Q0 narrative
    section_signal_discipline TEXT,                    -- Q1 narrative
    section_thesis            TEXT,                    -- Q2 (deferred — NULL in v1)
    section_patterns          TEXT,                    -- pattern + focus
    viz_json                  JSONB,                   -- FROZEN visual snapshot (flow + bands + missed)
    email_sent                BOOLEAN     NOT NULL DEFAULT false,
    email_sent_at             TIMESTAMPTZ
);
```

**AI Intelligence F-4 monthly intelligence report (one row per period).** A monthly retrospective: Q0 entry-quality (does the engine pick well, by composite band) + Q1 signal-discipline (acted vs. missed, on alpha). Narrative built by `intelligence_report.build_report_package` → `generate_report` from the `recommendations_history` scorecard — the LLM narrates the aggregates, it never recomputes them. **`viz_json`** stores the frozen `recommendations_history.report_viz_snapshot` (decision-flow Sankey + alpha-by-band bar + ranked missed bar) so the report is an **immutable dated artifact** — re-rendered verbatim rather than drifting on a live recompute; display falls back to a live recompute only for rows saved before freezing. **Unique on `period_end`** → upsert-safe. First-Sunday-of-month cron + on-demand. Headline acted/missed counts are **distinct tickers** (`signal_flow`), not surfacings. `db.load_monthly_reports(limit)` / `save_monthly_report()` (writer is read-only-viewer no-op). Optional — inert until the DDL is applied (the freeze adds the column to an existing table: `alter table monthly_reports add column if not exists viz_json jsonb;`). RLS: `FOR ALL TO service_role`.

### 6.12 `recommendations` table

```sql
CREATE TABLE recommendations (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker           TEXT NOT NULL,
    rec_date         DATE NOT NULL,
    rec_type         TEXT NOT NULL,            -- 'new_pick' | 'add_winner' | 'buy_candidate'
    price_at_surface NUMERIC,                  -- first-seen price (NULL when ≤ 0 / unknown, or — for buy_candidate rows before 2026-07-26 commit 51b2441 — never captured; see §10 Known Behaviours)
    composite_score  NUMERIC,
    momentum_score   NUMERIC,
    sector           TEXT,
    conviction       TEXT,
    verdict          TEXT,                     -- cross-check verdict (Confirmed / Conflicted / …)
    thesis           TEXT,                     -- short rationale snapshot (≤ 600 chars)
    surfaced_at      TIMESTAMPTZ NOT NULL DEFAULT now(),   -- server-authoritative first-seen
    UNIQUE (ticker, rec_date, rec_type)
);
```

**The recommendation audit log (scorecard substrate).** Every pick Today's Brief surfaces is logged once per `(ticker, rec_date, rec_type)` — `save_recommendations` upserts with `ignore_duplicates`, so the first-seen row (and its `surfaced_at` + `price_at_surface`) is authoritative and never overwritten. `rec_type` separates the **actionable** `new_pick` / `add_winner` from the awareness-only `buy_candidate` feed. Read by `recommendations_history.py` (the scorecard) and the F-4 monthly report. `db.save_recommendations()` (read-only-viewer no-op) / `load_recommendations(start_date, end_date)`. Optional — inert until the DDL is applied. RLS: `FOR ALL TO service_role`.

**Optional columns beyond the original `CREATE TABLE` above** (each additive, each dropped-and-retried by `save_recommendations` until its DDL is applied — see `db.py`'s header docstring for the exact `ALTER TABLE` statements): `s_score`, `avg_sent` (sentiment, feeds the composite); added 2026-08-01 for F-225 Portfolio Q&A, `t_score`, `bq_score`, `val_score` (the 4-pillar breakdown, forward-only — rows saved before this date have these `NULL`); and added 2026-08-23 for **F-249 Phase 2 sizing capture**, `rec_shares`, `rec_stop`, `rec_portfolio_value` (all `NUMERIC`) plus `rec_sizing_version` (`INTEGER`).

**Sizing-capture semantics (F-249 Phase 2) — three distinguishable states, and the distinction is the point.** The suggested share count was previously computed at render time and discarded, so Phase 3's take-rate metric (actual shares bought ÷ suggested shares) had no substrate. These columns create it, forward-only:

| `rec_sizing_version` | `rec_shares` | Means |
|---|---|---|
| `2` | non-NULL | A size was suggested. `rec_portfolio_value` is the book value it was **actually computed from**, not a re-read. |
| `2` | `NULL` | Captured, and the app **deliberately suggested no size** — one share exceeded `SINGLE_NAME_CEILING`, or price was at/below the ATR stop. |
| `NULL` | `NULL` | Three different things, so **do not read this as "before the cutoff"**: pre-capture (before the DDL); a `rec_type` that never carries a size (**`buy_candidate` rows never do** — only `new_pick` and `add_winner`); **or** a required sizing *input* was unavailable so the engine produced no sizing dict at all (no bundle stop for a held name, or no portfolio value). The third case is reachable **post-DDL**, so Phase 3 must filter on a non-null `rec_shares`, never on `rec_date`. |

**First writer of the day wins — and MEASURED 2026-08-23, that is usually the INTERACTIVE session, not the cron.** The upsert is `ignore_duplicates=True` on `(ticker, rec_date, rec_type)`. This row originally claimed the cron usually wins, reasoning from the code alone; reading real `surfaced_at` values disproved it. On 2026-08-21 the write batches were **09:26, 09:31, 09:32, 09:49 and 11:37 ET**, all interactive, while the `scan` lane's own `scanner_cache` row is stamped **10:46 ET** (the `45 14` UTC slot, not `45 13`). The owner opens the app well before the cron fires, so the captured size is normally **the one that was on screen when they first opened the app that day** — which is closer to the decision moment and therefore a *better* take-rate denominator than a later recompute. Two consequences a Phase 3 author must not miss: **(1)** the captured size is whatever the first session of the day saw, so it is not reproducible from end-of-day prices; **(2)** the interactive rec-log write has **no post-open gate**, unlike the `scan` lane which explicitly refuses to run pre-open on "a stale/forming bar" — the 09:26 ET batch above is pre-open, so a captured size can be computed off the prior close. Neither is a defect; both change what the denominator *means*. Corollary: a cron row written with a degraded input (e.g. `headless_alert_engine`'s `portfolio_value = 0.0` fallback → no sizing dict → four NULLs) will **block** that day's interactive capture for the same ticker.

**`rec_stop` has two bases depending on `rec_type`.** `new_pick` re-derives the ATR stop against the **live** price; `add_winner` deliberately keeps the bundle's **last-close** ATR stop (a recorded policy decision — the ratcheted stop would sit higher, suggest more shares, and over-buy into strength; see memory `project_stop_ladder_and_display`). Phase 3 will pool both `rec_type`s to compute revealed risk-per-trade, so the split matters there.

`rec_sizing_version` versions the **sizing formula**, not the schema (`daily_briefing.SIZING_FORMULA_VERSION`, currently `2`; `1` denotes the retired uncapped `_suggest_size` era and was never persisted). It exists so a future formula change cannot be silently compared across the boundary — the reason retroactive backfill was ruled out, since the old formula's trend bucket was never recorded and F-249 changed the formula anyway. **The value travels inside the sizing dict** the engine produces, so a writer cannot pair a size with the wrong version or the wrong portfolio basis.

**The drop-and-retry cascade is error-TARGETED, not positional.** `save_recommendations` reads the column name PostgREST reports (`PGRST204`) and strips only the generation containing it, repeating if a retry reveals a different missing generation. Peeling generations blind in newest-first order would strip the sizing columns on an unrelated `bq_score`-missing error — discarding data that works because something else is absent, which is exactly the loss this cascade exists to prevent (and it would report `saved=N, error=None` while doing it). `_COL_GENERATIONS` in `db.py` is authoritative; add a new generation to the front, never extend an existing `frozenset`.

### 6.13 `scanner_cache` table

```sql
CREATE TABLE scanner_cache (
    id           INTEGER PRIMARY KEY,          -- always 1 (single row)
    results_json TEXT,                          -- scanner DataFrame (pandas to_json, orient='split')
    scan_date    DATE,
    source       TEXT,                          -- which run wrote it (cron 'scan' vs. manual full scan)
    scanned_at   TIMESTAMPTZ
);
```

**Cross-session scanner persistence (single row).** Lets a cold app load — or the cron — hydrate `scanner_results` without a manual ~20-second scan: written by the cron `scan` mode (~10:00 ET) or a manual full-universe Home scan, read once per session into `st.session_state["scanner_results"]` (freshness via `_scanner_results_meta`; see §7). `db.save_scanner_cache()` / `load_scanner_cache()`. Optional — inert until the DDL is applied. RLS: `FOR ALL TO service_role`.

### 6.14 `alert_state` table

```sql
CREATE TABLE alert_state (
    id                INTEGER PRIMARY KEY,      -- cron lane: 1 = pre-market protective, 2 = EOD pullback
    last_emailed_date TEXT,                      -- 'YYYY-MM-DD' of the last send (once-per-ET-day gate)
    last_fingerprint  TEXT,                      -- hash of the protective set (skip when unchanged)
    updated_at        TIMESTAMPTZ
);
```

**Headless-cron dedup state (system state, not user data).** Used ONLY by the email-alerts cron to (a) fire at most once per ET trading day and (b) skip an email whose protective set is unchanged since the last send (`last_fingerprint`). One row per cron lane (`id` 1 = pre-market protective, 2 = EOD pullback, 3 = morning buy-list, 4 = intraday pullback entry, **5 = DB-unreachable notice**, added 2026-08-16 for F-239) — independent dedup, no extra DDL; rows self-create on first upsert because `save_alert_state` supplies `id` explicitly with `on_conflict="id"`. Row 5's dedup is deliberately **fail-open**: you cannot dedup a database-outage alert *in the database*, so it suppresses a repeat only when the DB is well enough to answer (a partial outage → one email/day/lane) and sends regardless when it isn't (a total outage → one per lane invocation). More email means a worse outage, which is self-explaining. **Not `_READONLY`-gated** (the cron runs outside the app). Degrades to "always send" when the table is absent — the alerts work before the DDL, just without dedup. `db.load_alert_state(row_id)` / `save_alert_state(...)`. RLS: `FOR ALL TO service_role`.

### 6.15 `analyst_coverage` table

```sql
CREATE TABLE analyst_coverage (
    id               BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    ticker           TEXT NOT NULL,
    company          TEXT,
    article_date     DATE NOT NULL,
    report_type      TEXT,                          -- initiation / upgrade / downgrade / reiteration / pt_change / other
    analysts         JSONB NOT NULL DEFAULT '[]',   -- [{firm, analyst, rating, price_target, upside_pct}, ...] — atomic per-firm facts
    consensus_rating TEXT,                           -- derived label, e.g. "Strong Buy (5 Buy / 0 Hold / 0 Sell)"
    avg_pt           NUMERIC,                         -- derived in Python (never by the LLM)
    high_pt          NUMERIC,
    low_pt           NUMERIC,
    thesis           JSONB DEFAULT '[]',
    catalysts        JSONB DEFAULT '[]',
    risks            JSONB DEFAULT '[]',
    raw_text         TEXT,                            -- original pasted article, for re-processing
    source           TEXT DEFAULT 'cnbc_pro',
    price_at_article_date NUMERIC,                   -- close price on article_date (next trading day); for Research Scorecard accuracy classification (Phase 1, 2026-07-22)
    composite_score_at_save NUMERIC,                 -- engine composite score at save time (NULL if ticker not in portfolio); for future Phase 3 Engine vs Analyst calibration
    created_at       TIMESTAMPTZ DEFAULT now()
);
```

**Analyst Coverage / Ideas Inbox (F-154, append-only).** Structured analyst research captured by pasting article text; the LLM (`analyst_intel.extract_report`, Sonnet, returns `list[dict]`) extracts only **atomic per-firm facts** as **one record per covered stock** (a multi-stock "top picks" roundup yields N records — each analyst attaches only to the stock they discuss, never merged; list-only mentions skipped) and the app computes all aggregates (`avg_pt`/`high_pt`/`low_pt`/`consensus_rating`) in pure Python (`derive_consensus`) so no number is hallucinated. The editable preview shows one card per extracted stock (include/exclude), saving each as its own row. **Append-only** (`save_analyst_coverage` inserts). `db.load_analyst_coverage(ticker=, days=, limit=)` (backfills NULL for legacy columns) / `save_analyst_coverage()` / `delete_analyst_coverage(id)` — writers are read-only-viewer no-ops. **Awareness-only — feeds no gate, score, or verdict** (the "Wall Street vs. your engine" tension). **Phase 2 (F-154a)** reads this table per-ticker into the 📈 Analysis "🏦 Analyst Coverage" tab (reconciled against the `targetMeanPrice` provider consensus) and injects the newest row as **CONTEXT** into the F-1 thesis reviewer (citable, never a verdict override). **Phase 3 (F-154b)** annotates Grow Today "New Positions to Initiate" cards with a display-only awareness caption when the surfaced pick also has recent saved coverage (`_cached_analyst_coverage_recent` hourly snapshot; render-only, never reorders/gates picks). Optional — inert until the DDL is applied (load returns empty). RLS: `FOR ALL TO service_role`.

### 6.15a Research Scorecard — Analyst Call Accuracy Tracking

**Analyst Research Accountability (F-154c, awareness-only).** Display-only accuracy classification for saved analyst calls — tracks directional and price-target hit rates without ever affecting a gate, score, or verdict. Computed at render time from `analyst_coverage` rows where `price_at_article_date IS NOT NULL` via `classify_call()` in `stock_analyzer/analyst_intel.py` and fetched OHLC windows (cached `@st.cache_data` per ticker/date-range). Renders as four blocks on its own 📊 Scorecard tab within 🧠 AI Insights:
- **Block A — Summary KPI row:** Directional Accuracy % (Buy/Sell calls correct on direction), PT Hit Rate % (calls reaching ≥75% of avg_pt via intra-window HIGH, not endpoint close), and Evaluable Calls count (status ∈ {hit, miss}). Pending calls (< 30 days since article date), no-anchor rows, and rows with no consensus rating excluded with a caption.
- **Block B — Per-call accuracy table:** Sortable columns (Ticker, Article Date, Consensus, Avg PT, Price @ Article, Exit Price, Return %, PT Proximity %, Status, Window). Color-coded rows (green hit, red miss, grey pending/no-anchor). Default sort: article_date DESC.
- **Block C — Firm Leaderboard:** Aggregates per firm (not just consensus — the per-analyst `analysts` JSONB) for evaluable rows only, showing Calls, Directional Accuracy %, PT Hit Rate %, Avg Return %. Minimum 2 calls per firm to appear (suppresses single-call noise).
- **Block D — Best & Worst Calls:** Top/bottom 3 calls by return % (gated on ≥5 evaluable calls); cards with ticker, return %, article date, and measurement window.
The two new columns are: `price_at_article_date` (populated at save-time via `_resolve_price_at_save()` in app.py, or backfilled by `scripts/backfill_analyst_prices.py` from yfinance) and `composite_score_at_save` (engine score at save, NULL for non-held tickers; reserved for Phase 3 engine-vs-analyst calibration). Both added 2026-07-22. Rows with `no_anchor` status (missing price_at_article_date) now show a per-row "🔄 Fetch" button on the Research Scorecard tab (calls `analyst_intel.fetch_anchor_price()` live with yfinance→FMP failover, then saves via `db.update_analyst_coverage_price()`) instead of only being fixable via the periodic batch backfill.

### 6.16 `sector_cache` table

```sql
CREATE TABLE sector_cache (
    ticker     TEXT PRIMARY KEY,
    sector     TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Last-known sector fallback (data-resilience).** A held position's sector resolves as curated `TICKER_SECTORS` → provider `.info` sector → `UNCLASSIFIED_SECTOR` ("Other") (`portfolio.resolve_sector`), and `.info` is the ONLY live sector source. On Yahoo's recurring sparse-`.info` days the sector drops to `""`, so every **unmapped** holding collapses to "Other" — which the 35% sector gate **excludes** (`daily_briefing._breached_sectors`), silently exempting those positions from concentration gating. Since sector is near-static, `bundle_loader` now **write-throughs** a good live `.info` sector (`db.save_sector_cache`) and **falls back** to the last-known cached value (`db.load_sector_cache`) when the live fetch is empty, before the "Other" catch-all — so classification (and the gate) survive a thin-`.info` day. `save_sector_cache` is **system data → NOT read-only-viewer-gated** (mirrors `save_fundamentals_cache`). **Scoring is deliberately NOT switched to the cached sector** (`_sector_for_scoring` stays on raw `.info`) so a resilience fix can't silently move composite/fundamental scores. Optional — inert until the DDL is applied (load/save no-op; behaviour degrades to the prior "Other" fallback). RLS: `FOR ALL TO service_role`.

---

### 6.17 `api_quota_log` table

```sql
CREATE TABLE api_quota_log (
    provider   TEXT    NOT NULL,
    log_date   DATE    NOT NULL DEFAULT CURRENT_DATE,
    call_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, log_date)
);
ALTER TABLE api_quota_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_api_quota_log" ON api_quota_log FOR ALL TO service_role USING (true);

CREATE OR REPLACE FUNCTION public.increment_api_quota(p_provider TEXT)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.api_quota_log (provider, log_date, call_count)
        VALUES (p_provider, CURRENT_DATE, 1)
    ON CONFLICT (provider, log_date)
    DO UPDATE SET call_count = public.api_quota_log.call_count + 1;
END;
$$;
```

**FMP daily call counter (rate-limit-resilience Phase 3).** `db.increment_daily_quota("fmp")` fires after each successful FMP request (via `fmp_provider`). `db.get_daily_quota("fmp")` returns today's count; `api_health.get_fmp_daily_quota()` caches the read (5-min TTL) so the orchestrator hot-path never issues a per-call DB query. When count ≥ `FMP_DAILY_SOFT_CAP` (220), `orchestrator._providers_for()` drops FMP from the capable list; it falls through to the full list if all providers are suppressed — never a hard-block. The Data Health chip shows **"today: N/250"** with a ⚠️ prefix at or above the soft-cap. **DDL applied in Supabase (2026-07-10) — feature is active.** RLS: `FOR ALL TO service_role`.

### 6.18 `earnings_context` table

```sql
CREATE TABLE earnings_context (
    ticker                     TEXT NOT NULL,
    article_date               DATE NOT NULL,
    company                    TEXT,
    earnings_date              TEXT,                    -- YYYY-MM-DD string (day-of-week refs resolved by the extractor)
    earnings_time              TEXT,                     -- pre_market / post_market / intraday / unknown
    beat_rate_pct              NUMERIC,                  -- Bespoke-style historical beat rate, explicit phrasing only
    recent_reaction_direction  TEXT,                     -- bullish / bearish / mixed / unknown
    recent_reaction_summary    TEXT,
    consensus_growth_pct       NUMERIC,
    what_to_watch_cnbc         TEXT,
    article_source             TEXT DEFAULT 'cnbc_pro',
    created_at                 TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, article_date)
);
```

**Pre-Earnings Playbook CNBC context (F-174).** Column list verified against `db.save_earnings_context`/`load_earnings_context` and the `app.py` write payload — not transcribed from a migration file (none is checked into the repo; DDL is applied ad hoc in Supabase, same as the project's other optional tables). Bulk-upserted via `db.save_earnings_context()` on conflict `(ticker, article_date)`; read by `db.load_earnings_context()` / `load_earnings_context_batch()` (most recent row per ticker within `max_age_days`, default 30). Feeds `earnings_advisor.build_earnings_playbook()`'s optional `earnings_context` param, which threads `beat_rate_pct`/`recent_reaction_direction` into `_recommend()` (F-174). DDL applied 2026-07-13 — active. RLS: `FOR ALL TO service_role` (CLAUDE.md hard rule #2).

### 6.19 `earnings_results` table

```sql
CREATE TABLE earnings_results (
    ticker              TEXT NOT NULL,
    report_date         TEXT NOT NULL,                  -- YYYY-MM-DD, user-set (UI defaults to today)
    actual_eps          NUMERIC,
    estimated_eps       NUMERIC,
    eps_beat            BOOLEAN,
    eps_surprise_pct    NUMERIC,
    actual_revenue      NUMERIC,                          -- read defensively by the F-1 checkpoint; not populated by the current Finnhub UI path
    estimated_revenue   NUMERIC,                          -- ditto
    rev_beat            BOOLEAN,                           -- ditto — Finnhub's free tier has no revenue data
    guidance_direction  TEXT,                              -- raised / maintained / lowered / withdrawn (user-set manually)
    key_narrative       TEXT,
    article_source      TEXT,                              -- 'finnhub_auto' (current UI path) or an LLM-paste source
    created_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, report_date)
);
```

**Post-earnings results (F-175) / F-1 checkpoint input (F-176).** Column list verified against `db.save_earnings_results`/`load_earnings_result`, the `app.py` write payload, and `thesis_advisor.generate_earnings_thesis_update`'s reads (`r.get("rev_beat")` etc.) — not transcribed from a migration file (none checked into the repo). Bulk-upserted via `db.save_earnings_results()` on conflict `(ticker, report_date)`; read by `db.load_earnings_result()` (most recent row per ticker within `lookback_days` — 90 from the capture UI, 14 from the F-1 checkpoint gate). The live UI path (`earnings_intel.fetch_recent_results()`, Finnhub, F-175) never populates `actual_revenue`/`estimated_revenue`/`rev_beat` — those exist in the row schema for the dormant LLM paste extractor (`extract_results()`), which is not wired to any UI, so today this table only ever receives EPS-level data. DDL applied 2026-07-13 — active. RLS: `FOR ALL TO service_role`.

### 6.20 `sentiment_history` table

```sql
CREATE TABLE IF NOT EXISTS sentiment_history (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker         TEXT    NOT NULL,
    snap_date      DATE    NOT NULL,
    vader_compound FLOAT,
    vader_score    FLOAT,
    headline_count INT,
    bullish_pct    FLOAT,
    bearish_pct    FLOAT,
    buzz_score     FLOAT,
    company_score  FLOAT,
    vs_sector_pp   FLOAT,
    source         TEXT DEFAULT 'cron',
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT sentiment_history_uniq UNIQUE(ticker, snap_date)
);
CREATE INDEX IF NOT EXISTS sentiment_history_ticker_date_idx
    ON sentiment_history (ticker, snap_date DESC);

ALTER TABLE sentiment_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_sentiment_history" ON sentiment_history
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Daily sentiment time-series for Tier 3 sentiment-vs-price-move analysis (F-179).** One row per (ticker, trading day). `vader_compound`/`vader_score` come from Pipeline A (VADER on yfinance/FMP headlines, same source as the composite score's 10% sentiment weight); `bullish_pct`/`bearish_pct`/`buzz_score`/`company_score`/`vs_sector_pp` come from Pipeline B (Finnhub `/stock/news-sentiment`, pre-aggregated ratios). Written by the EOD cron (`cron_runner._run_eod`) immediately after `save_daily_snapshot` — VADER values from `held_data` bundle + one Finnhub call per held ticker. Upserts on `(ticker, snap_date)`; last writer wins intraday. At least one of `vader_compound`/`bullish_pct` must be non-None (validated in `db.save_sentiment_snapshot`). Covers held tickers only (the cron's `_build_context` universe). **Ships inert until the DDL is applied** — `db.save_sentiment_snapshot` degrades silently (returns False). RLS: `FOR ALL TO service_role` — **gap found + closed 2026-07-21**: the original DDL applied to Supabase only ran the `CREATE TABLE`/`CREATE INDEX` statements above (the RLS lines weren't yet inline in this fence, unlike §6.17's `api_quota_log`), so the table sat with RLS disabled in production until Supabase's security advisor flagged it; the fence above now carries the RLS statements inline so a future copy-paste can't skip them again.

**New columns added to `recommendations` (§6.12) as part of F-179:**

```sql
ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS s_score  FLOAT,
    ADD COLUMN IF NOT EXISTS avg_sent FLOAT;
```

`s_score` = the VADER sentiment score (0–100) at the time the rec was surfaced (what the composite's 10% sentiment pillar was built from). `avg_sent` = the raw VADER compound score (−1 to 1). Both are nullable — old rows have NULL; the upsert `ignore_duplicates=True` means a same-day re-surface leaves the first-seen row (and its stored `s_score`) untouched. Written by `db.save_recommendations` from the `app.py` rec-log path; sourced from `_grow_composites` (scanner bundles) and `_last_held_data` (held position bundles) session caches via `_s_score_for`/`_avg_sent_for` helpers. **Ships inert until the DDL is applied** — the extra dict keys pass through to `null` on the existing upsert, but the DB will reject them until the `ALTER TABLE` is run. RLS: `FOR ALL TO service_role`.

### 6.21 `exit_signals` table

```sql
CREATE TABLE IF NOT EXISTS exit_signals (
    id               BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    ticker           TEXT NOT NULL,
    signal_date      DATE NOT NULL,
    signal_type      TEXT NOT NULL,
    composite_score  NUMERIC,
    price_at_signal  NUMERIC,
    dd_from_peak_pct NUMERIC,
    pnl_pct          NUMERIC,
    below_ma_count   INT,
    rel_strength     NUMERIC,
    surfaced_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT exit_signals_unique UNIQUE (ticker, signal_date, signal_type)
);

ALTER TABLE exit_signals ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_exit_signals" ON exit_signals
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Forward capture of every WATCH/TRIM/EXIT/RISK_OFF signal per (ticker, day)** — the prerequisite for the exit-side Behavioral Fingerprint (Concept A v2, `docs/plans/exit-signal-capture.md`; Phase 1 shipped 2026-07-18, commit `f86147d`). `signal_type` ∈ `WATCH`/`TRIM`/`EXIT`/`RISK_OFF`, sourced from `exit_advisor.classify_deterioration_tier()` and `exit_advisor.assess_risk_off_derisk()`. `composite_score` is enriched from `port_df["Score"]` at the capture site (not present on the assess_holding/risk_off dicts themselves) — null for RISK_OFF where a field doesn't apply (e.g. `dd_from_peak_pct`/`below_ma_count`/`rel_strength` on a risk_off row). Written from **two** independent paths as of 2026-07-21: (1) `app.py`'s Home MISS-path build (`app.py:4137-4199`, the original Phase 1 capture — interactive sessions only), and (2) `cron_runner._run_premarket()` (added 2026-07-21, closing Phase 1's own deferred "cron capture" scope item) via `headless_alert_engine.compute_protective_alerts()`'s additive `all_deterioration_signals`/`risk_off_signals` return keys — so signal history is now captured daily regardless of whether the app is opened. Idempotent upsert on `(ticker, signal_date, signal_type)` via `db.save_exit_signals_batch()` — safe for row-dedup, but a plain upsert alone is NOT safe for value-completeness: a same-day write missing a nullable column (e.g. `price_at_signal`) would silently NULL out a value the other path had already captured. **Fixed 2026-08-05:** `save_exit_signals_batch()` now does a coalesce-on-write merge before upserting — it reads any existing row for the incoming batch's keys and fills an incoming `None` from the existing non-null value per nullable column (`price_at_signal`, `dd_from_peak_pct`, `pnl_pct`, `below_ma_count`, `rel_strength`, `composite_score`); a genuine non-null new value still overwrites (last-non-null-wins). This closed a real bug where the interactive app's capture path (`app.py`) had never carried `price`/`dd_from_peak_pct`/`below_ma_count`/`rel_strength` forward from `exit_advisor.py`'s deterioration dicts into the `act_today`/`review_list` item shape (so every app-path EXIT/TRIM/WATCH write had `price_at_signal = NULL`), which could then clobber a good price the cron path had captured earlier the same day. Also fixed: `assess_risk_off_derisk()` never exposed its already-computed per-contributor price at all (RISK_OFF rows were NULL on both paths). **The fix is forward-only — existing historical NULL rows are not backfilled**, matching the same permanent-gap precedent as the `buy_candidate`/`price_at_surface` history (see F-160 note above). Read via `db.load_exit_signals(days_back=365)`. RLS: `FOR ALL TO service_role`.

### 6.22 `daily_regime` table

```sql
CREATE TABLE IF NOT EXISTS daily_regime (
    regime_date  DATE PRIMARY KEY,
    regime       TEXT NOT NULL,
    label        TEXT,
    confidence   INT,
    fed_trend    TEXT,
    cpi_yoy      NUMERIC,
    source       TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE daily_regime ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_daily_regime" ON daily_regime
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Daily persistence of the detected macro regime** (added 2026-07-21) — one row per calendar day, portfolio-independent. `regime`/`label`/`confidence`/`fed_trend`/`cpi_yoy`/`source` mirror the return shape of `macro_calendar.detect_macro_regime()` (regime id ∈ `rate_cut`/`inflation_fight`/`recession_fear`/`stagflation_risk`/`neutral`). Closes the gap Concept D's regime-conditional targets (F-188) explicitly deferred — "no Home 'regime changed' annotation (would need day-over-day regime persistence that doesn't exist)" — though **no consuming UI exists yet**; this is the persistence prerequisite only, same pattern as `exit_signals` Phase 1 vs Phase 2. Written by `cron_runner._run_eod()` once/day (alongside `daily_snapshots`/`sentiment_history`), calling `detect_macro_regime(os.environ.get("FRED_API_KEY") or None)` — degrades to the neutral fallback (`source="fallback"`) when no FRED key is configured, never fabricates a regime. Upserts on `regime_date` via `db.save_daily_regime()`; read via `db.load_daily_regime(days_back=90)`. **Ships inert until the DDL is applied** — degrades silently (`save_daily_regime` returns False). RLS: `FOR ALL TO service_role`. Not read from `st.session_state`'s ephemeral per-browser-session regime cache (`_macro_regime_{date}_{bool(fred_key)}`, used by Risk Analysis/Economic Calendar/Pre-Market Stance) — a separate, independent write path.

### 6.23 `analyst_target_snapshots` table

```sql
CREATE TABLE IF NOT EXISTS analyst_target_snapshots (
    id             BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    ticker         TEXT NOT NULL,
    snapshot_date  DATE NOT NULL,
    target_mean    NUMERIC,
    num_analysts   INT,
    info_source    TEXT,
    captured_at    TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT analyst_target_snapshots_unique UNIQUE (ticker, snapshot_date)
);

ALTER TABLE analyst_target_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_analyst_target_snapshots" ON analyst_target_snapshots
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Log-only daily snapshot of each held ticker's analyst consensus price target** (added 2026-07-21) — one row per (ticker, day), prerequisite for a future day-over-day "consensus target dropped X%" comparison. Motivated by a real gap found in a 2026-07-21 retrospective: the existing `"revisions"` alert (`portfolio.py:472-492`, fed by yfinance's `upgrades_downgrades`) only counts rating-ACTION changes (`up`/`down`/`init`/`main`/`reit`) — a firm that keeps its rating and just cuts the price target logs as `main`/`reit`, never as a `down`, so it's structurally invisible to that alert regardless of magnitude. `target_mean`/`num_analysts` mirror `financials["analyst_target"]`/`financials["num_analyst_opinions"]` (`data.py:227,233` — already fetched on every load, zero extra API cost). `info_source` records which provider supplied `.info` when the primary was backfilled (`orchestrator.py:187`, `None` implies yfinance was sufficient) — future comparison logic should treat a delta that coincides with a source change as suspect (yfinance and FMP compute consensus differently). Rows are **skipped entirely** (not written with a stale flag) when the bundle's `stale_as_of` is set, so persisted history never mixes in a stale `bundle_cache` fallback value. Written by `cron_runner._run_premarket()` (via `headless_alert_engine.compute_protective_alerts()`'s additive `analyst_target_snapshots` return key, reusing the bundles already loaded for the protective-alert checks — no new API cost) once/day, cron-only (deliberately not also written from the interactive app path — simpler single-path model, no historical gap to retrofit the way `exit_signals` had). Upserts on `(ticker, snapshot_date)` via `db.save_analyst_target_snapshots_batch()`; read via `db.load_analyst_target_snapshots(days_back=365)`. **Ships inert until the DDL is applied** — degrades silently. RLS: `FOR ALL TO service_role`. **Phase 1 is log-only: no alert, no new `constants.py` threshold, no gate** — the % consensus-target drop worth flagging is left uncalibrated until real snapshots accumulate; wiring an actual alert into the `"revisions"` category is a deferred Phase 2 that will need its own policy discussion + Opus review (touches `constants.py` + alert logic, unlike this pure-plumbing phase).

**Phase 2 SHIPPED 2026-07-31** — `stock_analyzer/analyst_targets.py::detect_pt_cut()` is a new pure-logic detector (no I/O), comparing the newest snapshot against the one `PT_TARGET_LOOKBACK_DAYS` (5) trading days back. A drop of `PT_TARGET_CUT_WARN_PCT` (-7%) or more fires a warning-level "revisions" alert in `portfolio.py::alerts()`; `PT_TARGET_CUT_DANGER_PCT` (-15%) or more fires danger — both independent of any rating action, closing the gap described above. **Suppressed when a rating-based revision alert already fired for the same ticker** (Opus review, 2026-07-31) — both are bearish "revisions" reads on the same name, so the PT-cut branch only surfaces when it's the ONLY signal catching the deterioration, avoiding two stacked alerts in one category for one ticker. Fewer than 6 distinct snapshot dates for a ticker ⇒ withheld (`direction=None`), never a fabricated flat/neutral read. **`info_source` suppression rule:** both rows' `info_source` `None` → trusted; both non-`None` and equal → trusted; both non-`None` and differ → suppressed (`source_switch_suppressed=True`, withheld); one `None`/one non-`None` → **trusted, not suppressed** (a deliberate judgment call — `info_source` is only ever `None` or `"fmp"` today, so a lone FMP-backfilled day shouldn't disqualify the comparison; FMP sources the same metric, it doesn't redefine it — revisit if a third provider makes this comparison genuinely ambiguous). The same detector also replaces the hardcoded `_rt_pt_pts = 7.0` inert placeholder referenced in §6.24 below, via `thesis_red_team.py::pt_points_from_signal()`. The Haiku counter-evidence prompt (`build_counter_evidence_inputs()`, §6.24) still deliberately excludes this signal — that exclusion's original justification ("it's still a placeholder") no longer strictly applies now that the value is real, but folding it into that already-hardened prompt is left as a separate follow-on decision, not done in this pass.

### 6.24 `thesis_erosion_cache` table

```sql
CREATE TABLE IF NOT EXISTS thesis_erosion_cache (
    ticker           TEXT        NOT NULL,
    score_date       TEXT        NOT NULL,
    erosion_score    NUMERIC     NOT NULL,
    erosion_label    TEXT        NOT NULL,
    counter_evidence JSONB,
    signals_snapshot JSONB       NOT NULL,
    created_at       TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, score_date)
);

ALTER TABLE thesis_erosion_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_thesis_erosion_cache" ON thesis_erosion_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Daily adversarial erosion score per held ticker** (Thesis Red Team Agent, Phase 1 — shipped 2026-07-23). One row per `(ticker, score_date)` where `score_date` is the America/New_York ISO date (`_today_et()`). `erosion_score` (0–100) aggregates four signals: deterioration tier weight (from `exit_signals`, today's rows only), 20-session RS vs SPY (`exit_advisor.compute_relative_strength()`), 5-session composite delta (self-referential: `signals_snapshot["composite_today"]` read from the 5-sessions-back row of this same table — inert for the first 5 trading days), and analyst PT revision direction (`analyst_target_snapshots`, real as of F-169 Phase 2 — see §6.23 — via `thesis_red_team.py::pt_points_from_signal()`; still falls back to the inert 7.0=flat reading for any ticker without enough snapshot history yet). `erosion_label` ∈ {`Intact`, `Softening`, `Eroding`, `Breaking`}. `signals_snapshot` MUST include `composite_today` (the day's live composite score) so future rows can look back for the 5-session delta.

**`counter_evidence` — Phase 2, shipped 2026-07-27.** Populated with `[{claim, severity, signal_basis}]` (0–3 items) whenever `erosion_score >= THESIS_EROSION_HAIKU_MIN` (30) **and** a `user_thesis` is on record for that ticker, via `stock_analyzer/thesis_red_team.py::generate_counter_evidence()`. **UI-labelled "Counter-evidence," deliberately not "Bear case"** — that term is reserved for §6.25's `debate_cache.bear_case_score` below, a distinct mechanism (a numeric debate-strength score, not a claims list), and the two surfaces sit near each other on Act Today cards. An empty list `[]` is a valid, cacheable "no grounded counter-evidence found today" result — **distinct from `null`**, which means either the trigger never fired (score below threshold, or no thesis) or the Haiku call itself failed; callers must test `is not None`, never truthiness. The prompt (`build_counter_evidence_inputs()`) deliberately excludes the erosion score/label and the PT-revision component — originally because both could carry synthetic weight from the then-hardcoded PT placeholder that would otherwise be cited as if real; the PT signal is now a real read (F-169 Phase 2, §6.23) but the exclusion is left in place as a separate, not-yet-made decision rather than folded in silently. Only primitive, individually-real signals (tier, RS vs SPY, composite delta, price/entry/age) reach the prompt, each coerced through a `math.isfinite()` check first (a `None`/`NaN`/`inf`-producing upstream failure must never surface as a grounded-looking citation). Also feeds the **Pre-Mortem loop**: `trades.premortem_commitment` (F-187), read from the same most-recent-BUY-with-a-thesis row as `user_thesis`, is passed as additional context and the model is instructed to quote it back when current evidence supports it. Second surface: a read-only expander on 🏠 Home's Act Today deterioration cards (`_render_act_card`) — a pure cache read, triggers no compute. 6 Opus design-review rounds (`docs/plans/thesis-red-team-phase2.md`).

Written by `db.save_thesis_erosion_cache()` (upsert, best-effort); read by `db.load_thesis_erosion_cache(ticker, score_date)`. System cache — not `_READONLY`-gated (same classification as `sentiment_llm_cache`). Compute is gated on `is_trading_day()` to prevent weekend rows from corrupting the cross-day delta. RLS: `FOR ALL TO service_role`.

### 6.25 `debate_cache` table

```sql
CREATE TABLE IF NOT EXISTS debate_cache (
    ticker          TEXT        NOT NULL,
    debate_type     TEXT        NOT NULL,
    debate_date     TEXT        NOT NULL,
    verdict         TEXT,
    key_dispute     TEXT,
    bull_case_score NUMERIC,
    bear_case_score NUMERIC,
    grounded        BOOLEAN,
    transcript      JSONB       NOT NULL,
    corpus_snapshot JSONB       NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, debate_type, debate_date)
);

ALTER TABLE debate_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_debate_cache" ON debate_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Structured Bull vs Bear debate result per candidate** (Multi-Agent Debate Agent, Phase 1 — shipped 2026-07-23). One row per `(ticker, debate_type, debate_date)` where `debate_type` ∈ {`entry` (Phase 1, triggered from 📈 Grow Today), `exit` (Phase 2, "⚔️ Challenge This Exit" on deterioration cards — shipped 2026-07-24)} and `debate_date` is the America/New_York ISO date (`_today_et()`). `verdict` ∈ {`bull_wins`, `bear_wins`, `contested`} — `bull_wins`/`bear_wins` require the Judge's `bull_case_score`/`bear_case_score` gap to reach `DEBATE_WIN_MARGIN` (20, module-level constant in `stock_analyzer/debate_agent.py` — a display classifier, not a `constants.py` policy threshold, since no gate or score is affected regardless of its value). `key_dispute` is the Judge's one-sentence summary of the specific claim Bull and Bear most disagree on (`null` if the two sides converged). `grounded` is `false` when the Judge assessed either side as arguing generically instead of citing the supplied evidence corpus. `transcript` is the ordered `[{round, agent, text}]` list from all 4 debate rounds (Bull open → Bear response → Bull rebuttal → Bear close); `corpus_snapshot` is the exact evidence dict both agents debated from (`build_entry_corpus()`/`build_exit_corpus()`), kept for auditability. Row is only written when `transcript` is non-empty — a failed run (no API key, a mid-debate Haiku failure) is never cached, so a transient failure can be retried immediately rather than showing a false "Contested" verdict for the rest of the day. Written by `db.save_debate_cache()` (upsert, best-effort); read by `db.load_debate_cache(ticker, debate_type, debate_date)` (single row, exact key) and `db.load_debate_verdicts(tickers)` (verdict-only, filtered to a ticker list — feeds D3 Signal Coherence). **`db.load_all_debates(limit=200)`** (Phase 3 — shipped 2026-07-27) returns every stored row across all tickers/types, most recent first (`debate_date` then `created_at` as a same-day tiebreak), excluding `corpus_snapshot`, for the 🧠 AI Insights "⚔️ Debate Log" browsable history tab. System cache — not `_READONLY`-gated (same classification as `thesis_erosion_cache`/`sentiment_llm_cache`). RLS: `FOR ALL TO service_role`.

### 6.26 `structural_scan_cache` table

```sql
CREATE TABLE IF NOT EXISTS structural_scan_cache (
    scan_date            TEXT        NOT NULL,
    narrative            TEXT,
    blast_radius         JSONB       NOT NULL,
    cluster_snapshot     JSONB       NOT NULL,
    risk_budget_snapshot JSONB       NOT NULL,
    created_at           TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (scan_date)
);

ALTER TABLE structural_scan_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_structural_scan_cache" ON structural_scan_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Daily portfolio-level structural narrative** (Structural Vulnerability Scanner, Phase 1 — shipped 2026-07-24). One row per `scan_date` (America/New_York ISO date via `_today_et()`) — this is a **portfolio-wide** synthesis, not per-ticker, unlike `thesis_erosion_cache`/`debate_cache`. `narrative` is the Haiku-generated 2-4 sentence structural explanation; `null` only if the Haiku call failed, but a failed/empty narrative is never written in the first place (the caller only calls `save_structural_scan_cache()` when the narrative call succeeded), so a `null` narrative should never actually appear in a saved row. `blast_radius` is `stock_analyzer.structural_scanner.blast_radius()`'s output at scan time (one dict per shocked ticker, the top-3 risk-budget contributors); `cluster_snapshot` is `portfolio_intelligence.correlation_clusters()`'s output; `risk_budget_snapshot` is the top-3 `risk_budget()` positions — all three kept for audit alongside the narrative they produced. Written by `db.save_structural_scan_cache()` (upsert, best-effort); read by `db.load_structural_scan_cache(scan_date)`. **Button-gated write, not auto-computed** — the tab's Blast Radius Map (pure Python) recomputes live every render, but the narrative only calls Haiku inside an explicit "🧬 Generate structural narrative" button click, because Streamlit executes every tab body on every page rerun regardless of which tab is selected — an auto-compute design would fire the LLM call far more than once/day. System cache — not `_READONLY`-gated (same classification as `debate_cache`/`thesis_erosion_cache`). RLS: `FOR ALL TO service_role`.

### 6.27 `price_xcheck_history` table

```sql
CREATE TABLE IF NOT EXISTS price_xcheck_history (
    ticker           TEXT        NOT NULL,
    check_date       TEXT        NOT NULL,
    primary_source   TEXT,
    validator_source TEXT,
    prev_gap_pct     NUMERIC,
    live_gap_pct     NUMERIC,
    ok               BOOLEAN     NOT NULL,
    created_at       TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, check_date)
);

ALTER TABLE price_xcheck_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_price_xcheck_history" ON price_xcheck_history
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Daily history of the already-shipped price cross-check** (Information Asymmetry
Detector, Phase 1 — shipped 2026-07-24; F-126). One row per `(ticker, check_date)`
where `check_date` is the America/New_York ISO date (`_today_et()`) — closes the one
gap in the pre-existing F-123 price cross-check (§4.0.4), which had zero history
before this table (a 5-minute `st.cache_data` TTL only). `primary_source`/
`validator_source`/`prev_gap_pct`/`live_gap_pct`/`ok` mirror
`orchestrator.crosscheck_batch()`'s per-ticker result fields exactly — no
recomputation, purely a persistence of an already-computed value. **Written from the
interactive Home page path only, day-deduped via `st.session_state`, NEVER from
`cron_runner.py`** — the premarket cron path does not call
`crosscheck_price`/`crosscheck_batch` today, so logging from cron would mean a
genuinely new per-ticker second-provider fetch every cron run; the interactive path
already pays for this computation every 5 minutes via `_cached_price_xcheck`. Enables
the F-126 "widened since `<date>`" banner annotation: `db.load_price_xcheck_history(
ticker, before_date, days_back=21)` returns the most recent row strictly before
`before_date` within a 21-day lookback, or `None` if no prior row exists (never
fabricates a baseline). `data.divergence_widened(today_gap_pct, prior_gap_pct,
min_widen_pp=1.0)` is the pure diff+threshold helper deciding whether to append the
annotation — `min_widen_pp` is a display-annotation default, not a `constants.py`
policy value, since it only decides whether a sentence is appended to an already-firing
banner. Written by `db.save_price_xcheck_history_batch()` (upsert on
`(ticker, check_date)`, best-effort, `_READONLY`-gated since this technically writes
on every distinct trading day the app is opened); read by
`db.load_price_xcheck_history()`. RLS: `FOR ALL TO service_role`.

### 6.28 `regime_scenario_cache` table

```sql
CREATE TABLE IF NOT EXISTS regime_scenario_cache (
    scan_date             TEXT        NOT NULL,
    scenario_narrative    TEXT,
    indicator_watchlist   JSONB,
    blast_radius_snapshot JSONB       NOT NULL,
    regime_snapshot       JSONB       NOT NULL,
    cross_asset_snapshot  JSONB       NOT NULL,
    created_at            TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (scan_date)
);

ALTER TABLE regime_scenario_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_regime_scenario_cache" ON regime_scenario_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Daily portfolio-level regime-aware adversarial scenario narrative** (Regime-Aware
Adversarial Stress Testing, Phase 1 — shipped 2026-07-24; F-200). One row per
`scan_date` (America/New_York ISO date via `_today_et()`) — portfolio-wide, not
per-ticker, same grain as `structural_scan_cache`. `scenario_narrative` is the
Haiku-generated 2-4 sentence compound-scenario explanation; a failed/empty narrative is
never written (the caller only calls `save_regime_scenario_cache()` when the call
succeeded). `indicator_watchlist` is a validated jsonb list of canonical signal
labels selected by the LLM from the regime detector's real `signals` list —
normalized-matched (`.strip().casefold()`), always the stored canonical label, never
the LLM's echoed text. `blast_radius_snapshot`/`regime_snapshot`/`cross_asset_snapshot`
are the exact evidence dicts used (`structural_scanner.blast_radius()`'s output,
`macro_calendar.detect_macro_regime()`'s return dict, `cross_asset.compute_cross_asset_signals()`'s
return dict), kept for audit — all three `NOT NULL` (a deliberate, safe divergence from
`structural_scan_cache`'s equivalent columns, since all three are always populated at
the single guarded call site). **Button-gated write, not auto-computed** — same
Streamlit execution-model reasoning as `structural_scan_cache`: the expander's body
runs every rerun regardless of visual state, so the Haiku call fires only inside an
explicit "🎯 Generate regime-aware scenario" button click. Written by
`db.save_regime_scenario_cache()` (upsert, best-effort); read by
`db.load_regime_scenario_cache(scan_date)`. **Deliberately NOT `_READONLY`-gated** —
same classification as `structural_scan_cache`/`debate_cache`/`thesis_erosion_cache`:
a recomputable analytical narrative, not user-authored data (contrast with
`price_xcheck_history`, which IS gated as an accumulating per-ticker ledger of the
owner's holdings' data-quality state — the two patterns are reconciled, not
inconsistent). RLS: `FOR ALL TO service_role`.

### 6.29 `judgment_opinions` table

```sql
CREATE TABLE IF NOT EXISTS judgment_opinions (
    id           BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    source       TEXT NOT NULL,
    dimension    TEXT NOT NULL,
    ticker       TEXT NOT NULL,   -- '_PORTFOLIO' sentinel for portfolio-wide opinions
    signal_date  DATE NOT NULL,
    signal       NUMERIC NOT NULL,
    label        TEXT,
    confidence   NUMERIC NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,
    is_live      BOOLEAN NOT NULL DEFAULT TRUE,
    evidence     TEXT,
    advisory     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT judgment_opinions_unique UNIQUE (source, dimension, ticker, signal_date)
);

ALTER TABLE judgment_opinions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_judgment_opinions" ON judgment_opinions
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Phase 0 (log-only instrumentation) of "The Judge"** — see
`docs/plans/judgment-layer.md` for the full design. This is the first table in a
multi-phase, Opus-design-reviewed plan (2026-08-03, Opus 4.8: FIX-FIRST → all
findings incorporated) for a future portfolio-level judgment layer that reconciles
every existing feature into one accountable daily posture. **Nothing reads this table
yet** — it exists purely so Phase 2's grading harness has witness history to read
once it's built. Ships inert until the DDL above is applied — degrades silently, same
convention as `analyst_target_snapshots`. **RLS gap found + closed 2026-08-04**: same
failure mode as `sentiment_history`'s 2026-07-21 incident below — the original DDL
applied to Supabase only ran the `CREATE TABLE` statement above (RLS wasn't yet inline
in this fence), so the table sat with RLS disabled in production until Supabase's
security advisor flagged it (`public.judgment_opinions` "RLS Disabled in Public"). The
fence now carries the RLS statements inline; the recurrence across 8 other table
sections triggered a repo-wide sweep the same day (§6.21–6.29) to close the class,
since fixing only this one instance a second time would leave the doc template broken
for the next table.

One row per `(source, dimension, ticker, signal_date)` — upserts on that key, so
repeated same-day Home renders are no-ops. `ticker` uses the sentinel `'_PORTFOLIO'`
(not `NULL`) for portfolio-wide opinions (`concentration`, `structural_risk`) so the
unique constraint dedupes reliably (Postgres `NULL` never equals `NULL`). `signal` is
normalized `-1..+1`; `dimension` is one of the ~10-dimension taxonomy in the plan doc
(`quality`, `momentum`, `thesis_integrity`, `position_health`, `concentration`,
`structural_risk`, `macro_regime`, `behavioral_fit`, `sentiment`, `leverage`, plus
advisory-only `tax`/`catalyst`). `is_live` preserves the codebase's `None`
(offline)-vs-`[]` (checked-clean) distinction at opinion grain — an opinion built from
stale/offline source data must carry `is_live=False`, not be silently omitted, so a
future Judge can degrade its confidence rather than reading absence as "all clear."

**Phase 0 witnesses wired** (5 core-set sources, chosen because they already emit
something close to an opinion — see the plan doc's Phase-0 core-set): `exit_advisor`
(`position_health`, from the existing exit-signal capture block), `composite_score` +
`scanner_momentum` + `verdict_reconciliation` (`quality`/`momentum`, from Grow Today's
`new_picks`), `fragility_gauge` (`structural_risk`, logged only at the fresh-compute
site, not the `_home_synth_cache`-hit re-publish), and `concentration_gate`
(`concentration` — a **real** worst single-name/sector breach check against
`SINGLE_NAME_CEILING`/`SECTOR_CEILING`, not a placeholder, since no persisted
concentration history existed before this table). Built by
`stock_analyzer.judgment_opinion.build_opinion()` (pure, no I/O) at each site; written
by `db.save_judgment_opinions_batch()` (best-effort, never raises — a capture failure
must never affect Home rendering); read by `db.load_judgment_opinions(days_back=365)`.
**`_READONLY`-gated** (`if _READONLY: return`) — consistent with
`exit_signals`/`analyst_target_snapshots` (interactive-app-path writers computed
during a real user's Home render, not a system cron), not the
`structural_scan_cache`/`debate_cache` exemption class. RLS: `FOR ALL TO service_role`.

**Phase 1 SHIPPED** (2026-08-03): the routing logic (protective-veto vs weighting vs
contradiction-audit, `stock_analyzer/judgment_synthesis.py`) and the read-only
"🧑‍⚖️ The Judge" nav page. **Phase 2 SHIPPED** (2026-08-03): the grading harness
(`stock_analyzer/judgment_grading.py`) — see §6.30 `judgment_grades`. **Not yet
built:** evidence-based weighting (Phase 3) and any override/gating authority
(Phase 4) — both gated behind their own Opus review per hard rule #4. This table
only logs; it decides nothing.

### 6.30 `judgment_grades` table

```sql
CREATE TABLE IF NOT EXISTS judgment_grades (
    id             BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    source         TEXT NOT NULL,
    dimension      TEXT NOT NULL,
    ticker         TEXT NOT NULL,   -- '_PORTFOLIO' sentinel, matches judgment_opinions
    signal_date    DATE NOT NULL,
    horizon_days   INT NOT NULL,
    opinion_signal NUMERIC NOT NULL,
    realized_pct   NUMERIC,
    correct        BOOLEAN,
    graded_at      TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT judgment_grades_unique UNIQUE (source, dimension, ticker, signal_date)
);

ALTER TABLE judgment_grades ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_judgment_grades" ON judgment_grades
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Phase 2 (grading harness) of "The Judge"** — see `docs/plans/judgment-layer.md`.
One row per graded `judgment_opinions` row (same natural key). Grades individual
WITNESSES against realized outcomes — never the Judge's aggregate posture, which
is deliberately never itself graded (Q2: "posture-correctness is a derived,
secondary read"). Two grading classes, dispatched by `stock_analyzer.
judgment_grading` on whether the opinion's `ticker` is `None` (portfolio-wide) or
a real ticker — not a hardcoded per-dimension set, so a future witness on either
grain grades correctly without touching the dispatcher:

- **`grade_ticker_opinion()`** — `quality`/`momentum`/`position_health`: forward
  alpha vs SPY, reusing `predictive_analytics.forward_alpha_at_horizon()` exactly
  (the same mechanism the Entry Timing tab already uses) — not a fresh
  reimplementation.
- **`grade_portfolio_opinion()`** — `concentration`/`structural_risk`:
  portfolio-wide forward alpha vs SPY, from a `daily_snapshots`-derived value
  series (`portfolio_value_series_from_snapshots()`, new aggregation — nothing
  else in the app turns per-ticker `daily_snapshots` rows into a portfolio-value
  time series today).

Both graders check the horizon's target date against `today` **before** any price
fetch, so "not matured yet" (returns `None` from the grader — no row written) is
never confused with "matured but the fetch failed" (`realized_pct`/`correct` are
`NULL` in a written row — a real data gap, not silently dropped). `correct` is a
simple sign-match between `realized_pct` and `opinion_signal` — magnitude-weighted
correctness is deferred to Phase 3. Per-dimension horizons and the shared
min-sample gate (`BEHAVIORAL_MIN_SAMPLE_N`, reused rather than a parallel
constant) are listed in §4.0.1's constants table under "Judgment layer."

**Trigger: manual "▶ Run grading" button on the Judge page** — not automatic on
every page load (would re-fetch price history on every visit) and not a cron job
(grading is cheap, on-demand price lookups, not an LLM call). Written by
`db.save_judgment_grades_batch()` (upsert on the same natural key as
`judgment_opinions`, best-effort, never raises); read by
`db.load_judgment_grades(days_back=365)`, rolled up by
`judgment_grading.track_record_summary()` into a per-(source, dimension) accuracy
display, always visible on the Judge page (not gated behind the button — reads
whatever's already graded). `_READONLY`-gated, same class as `judgment_opinions`.
RLS: `FOR ALL TO service_role`. **Nothing reads this table's history for
weighting yet** — Phase 3 is what would.

### 6.31 `model_predictions` table

```sql
CREATE TABLE IF NOT EXISTS model_predictions (
    id                 BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    model_name         TEXT NOT NULL,
    model_version      TEXT NOT NULL,
    scope              TEXT NOT NULL,           -- 'ticker' | 'portfolio'
    ticker             TEXT,                    -- 'PORTFOLIO' sentinel for scope='portfolio'
    made_at            TIMESTAMPTZ NOT NULL,
    horizon_days       INT NOT NULL,            -- trading days
    target_metric      TEXT NOT NULL,           -- e.g. 'realized_vol_20d_annualized'
    predicted_value    NUMERIC NOT NULL,
    predicted_low       NUMERIC,
    predicted_high      NUMERIC,
    baseline_value     NUMERIC NOT NULL,        -- naive-persistence baseline, logged at make-time
    regime_at_make     TEXT,
    features_snapshot  JSONB,
    realized_value     NUMERIC,                 -- written at maturation
    scored_at          TIMESTAMPTZ,
    abs_error          NUMERIC,
    baseline_abs_error NUMERIC,
    source             TEXT NOT NULL DEFAULT 'live',  -- 'live' | 'backfill'
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT model_predictions_unique UNIQUE (model_name, model_version, scope, ticker, made_at)
);

ALTER TABLE model_predictions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_model_predictions" ON model_predictions
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**Predictive Modeling Shadow Layer — Phase 1 (F-234), MEASUREMENT-ONLY.** See
`docs/plans/predictive-modeling-shadow-layer.md` for the full design and
leakage-guard rationale. This is a quarantined prediction ledger + scoring
harness — **nothing that reads or writes this table is consumed by any
existing gate, recommendation function, or the composite score.** The `id`
IDENTITY column is required so `mature_model_predictions_batch()`
(`stock_analyzer/db.py`) can target individual rows for update at maturation;
the natural key for idempotent writes is the `UNIQUE` constraint above, not
`id`. `ticker` uses the literal string `'PORTFOLIO'` (not NULL) for
`scope='portfolio'` rows so the UNIQUE constraint's NULL-handling ambiguity
(Postgres treats each NULL as distinct, which would silently defeat the
upsert's idempotency for portfolio-scope rows) never applies.

Ships **inert until this DDL is applied manually** via the Supabase
dashboard — same "ships inert" convention as every other table added this
way (`judgment_opinions` §6.29, `analyst_target_snapshots` §6.23,
`portfolio_thesis`). `stock_analyzer/db.py`'s loaders return `None` (the
offline sentinel, distinct from a genuinely-empty result) on a pre-DDL
"relation does not exist" error, identically to any other failure — the
🔬 Model Lab page renders its "producer offline" state in that case.

**Writers:** the daily cron (`cron_runner.py`, EOD lane) writes `source=
'live'` rows for every currently-held ticker + the `'PORTFOLIO'` aggregate,
reusing the SAME 6-month bars that lane's other steps already fetched (no new
fetch); the one-off, rerunnable `scripts/backfill_vol_predictions.py` writes
`source='backfill'` rows for currently-held tickers only, using
`PREDICTION_BACKFILL_PERIOD` ("5y") of history via the same
`data.fetch_price_history` path Outcome Range (`monte_carlo.py`) uses.
**PORTFOLIO-scope backfill is deliberately NOT built** — it would need
historical portfolio weights, which only exist as far back as the logged
`trades` history (~3 months), not 5 years of market data; portfolio-scope
rows accumulate only going forward via the live cron. There is no
interactive/user-write path to this table at all — `is_readonly()` guards on
the writers are precautionary defense-in-depth, not load-bearing.

**Readers:** `stock_analyzer/prediction_scoring.py::score_predictions()`
(pure logic — MAE/skill-score vs the logged baseline, live-only skill,
regime-stratified breakdown, a stride-based effective-n note) is the only
consumer, and the 🔬 Model Lab page (owner-only, hidden from a read-only
viewer — the first nav entry ever fully hidden rather than merely
write-disabled) is the only renderer. Model v1 (`vol_forecast_ewma`) is a
fixed-λ RiskMetrics EWMA forecaster (`stock_analyzer/vol_forecast.py`) with
no fitted parameters, so backfilled rows carry no in-sample/backtest-leakage
risk the way a fitted model would.

### 6.32 `cron_heartbeat` table

```sql
CREATE TABLE IF NOT EXISTS cron_heartbeat (
    lane        TEXT PRIMARY KEY,               -- 'premarket'|'scan'|'intraday'|'eod'|'thesis'|'maintenance'|'broker'
    last_run_at TIMESTAMPTZ NOT NULL,           -- ET-aware ISO timestamp of the last invocation
    status      TEXT NOT NULL DEFAULT 'ok',     -- 'ok' | 'failed'
    detail      TEXT,                           -- failure detail (truncated), NULL on success
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cron_heartbeat ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_cron_heartbeat" ON cron_heartbeat
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**System Proprioception — Phase 1.** See `docs/plans/system-proprioception.md`.
One row per cron lane, upserted (`on_conflict="lane"`) at the END of every lane
invocation by `cron_runner.main()` — including trading-day-skip no-ops, because a
skip is still proof the Railway scheduler fired the service (the whole point of
the GitHub Actions → Railway migration was execution certainty). **OBSERVABILITY
ONLY — nothing reads this for a gate, recommendation, composite, or threshold.**

Ships **inert until this DDL is applied manually** via the Supabase dashboard —
same convention as `model_predictions` §6.31 / `daily_regime` §6.22.
`save_cron_heartbeat()` swallows the pre-DDL "relation does not exist" error and
returns `False` (the lane logs "heartbeat NOT saved" and continues — a heartbeat
write can never affect a lane's real work or exit code); `load_cron_heartbeats()`
returns `None` (offline sentinel) on any failure. **Reader:** the owner-only
🩺 System Trust page via `stock_analyzer/system_health.py::check_cron_liveness()`
— which reads a lane's heartbeat as "unknown" (⚪, not degraded) when no row
exists yet, so a freshly-applied table or a lane's first run is never a false
alarm; only a stale-but-present timestamp (or a `status='failed'` row) degrades.
**Tightened 2026-08-21 (closes a gap found live: a total DB outage prevents the
heartbeat WRITE too, so a stale-but-still-"ok" row can otherwise read healthy
for up to 30h/8d after a real failure).** Each `_Lane` in `_LANES` now carries
`fire_hours_et`/`fire_weekday` — ET-native, already-margined expected-fire
times duplicated from the Railway dashboard's real cron schedule (dashboard is
still the source of truth; these must be updated by hand if a schedule
changes) — and an otherwise-"ok" row that predates its lane's expected fire
downgrades to "warn" (never "down"/"unknown"). The email alert already covers
the same failure through a DB-independent channel; this closes the dashboard
side of the same gap.

### 6.33 `snaptrade_config` table

```sql
CREATE TABLE IF NOT EXISTS snaptrade_config (
    id                         INTEGER PRIMARY KEY,   -- always 1 (single user)
    brokerage_authorization_id TEXT,
    status                     TEXT NOT NULL DEFAULT 'disconnected',  -- 'disconnected' | 'pending_env_vars' | 'connected' | 'error'
    connected_at               TIMESTAMPTZ,
    last_full_sync_at          TIMESTAMPTZ,
    CHECK (id = 1)
);

ALTER TABLE snaptrade_config ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_snaptrade_config" ON snaptrade_config
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**F-244 SnapTrade broker sync** (`docs/plans/snaptrade-broker-integration.md`).
Single-row connection-state bookkeeping — **never the `USER_SECRET` itself**,
which SnapTrade issues once at registration and the app deliberately never
persists (a Railway environment variable instead; see the plan's "Credential
storage" section). `status='pending_env_vars'` is an app-set intermediate
value (not in the schema's original 3-value comment) covering the window
between the one-time "Register with SnapTrade" click and the `broker` cron's
first successful sync, which flips it to `'connected'`. Ships inert until this
DDL is applied — `load_snaptrade_config()` returns `None`, `has_snaptrade()`
(env-credential check) is unaffected.

### 6.34 `snaptrade_pending_imports` table

```sql
CREATE TABLE IF NOT EXISTS snaptrade_pending_imports (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snaptrade_txn_id TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    action           TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    shares           NUMERIC NOT NULL CHECK (shares > 0),
    price            NUMERIC NOT NULL CHECK (price > 0),
    trade_date       DATE NOT NULL,
    raw_json         JSONB,
    status           TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'logged' | 'dismissed'
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snaptrade_txn_id)
);

ALTER TABLE snaptrade_pending_imports ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_snaptrade_pending_imports" ON snaptrade_pending_imports
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**F-244 — a notification/reminder queue, NEVER a source of truth.** A row here
becomes a real `trades` row only when the user completes the "Log This Trade"
form (Option A flow); the `broker` cron never writes `trades` directly.
`save_snaptrade_pending_imports()` deliberately does NOT merge-upsert — it
inserts with `ignore_duplicates=True` (INSERT ... ON CONFLICT DO NOTHING) so a
re-fetch of an overlapping sync window is a true no-op. An earlier version
used a merge-upsert with `status` hardcoded to `'pending'`, which would have
silently flipped an already-`'logged'` or `'dismissed'` row back to `'pending'`
on every re-sync — caught and fixed in the 2026-08-17 review before ship.

### 6.35 `snaptrade_income_events` table

```sql
CREATE TABLE IF NOT EXISTS snaptrade_income_events (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN ('dividend', 'interest', 'fee')),
    ticker     TEXT,             -- null for account-level interest/fees
    amount     NUMERIC NOT NULL,
    event_date DATE NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE snaptrade_income_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_snaptrade_income_events" ON snaptrade_income_events
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

**F-244 — display/trend ONLY.** Feeds exclusively the 💰 Account page's Cash
Activity monthly trend chart. **Deliberately never read by
`stock_analyzer/account.py`'s Modified Dietz return math** — a dividend or
interest credit is performance, not a contribution, and routing it through
`account_flows`'s `net_contributed_capital` would silently inflate NCC and
suppress reported growth%. Only `CONTRIBUTION`/`WITHDRAWAL` SnapTrade activity
types ever reach `account_flows`; see `stock_analyzer/broker_sync.py`'s
`classify_transactions()`.

### 6.36 `broker_position_snapshot` table

**Single-row (id=1) capture of what the BROKER reported holding**, so 🏠 Home can
warn that Portfolio Value disagrees with the broker without putting a SnapTrade
call on its render path. Written by the `broker` cron lane, read by Home.
**Manual DDL** — the statements live in `db.py`'s schema block; the feature ships
inert until they are applied, and 🩺 System Trust shows the store red until then.

| column | type | meaning |
|---|---|---|
| `id` | integer PK | always 1 (`check (id = 1)`) |
| `positions` | jsonb | `{"DELL": 20.0, ...}` — ticker → shares, equity/ETF/ADR only |
| `account_ids` | jsonb | the accounts that responded at capture time |
| `all_accounts_ok` | boolean | every linked account responded |
| `captured_at` | timestamptz | set EXPLICITLY on upsert, because `default now()` fires only on INSERT and a refreshed row would otherwise keep its original age |

**Why only the broker side is stored.** The book side is diffed LIVE on every
render (`broker_sync.diff_position_map` against raw `holdings_df`). That
asymmetry is the design: the side the user actively edits is the live one, so
correcting a mis-logged trade clears the warning immediately instead of nagging
until tomorrow's cron. Storing a precomputed *verdict* instead would invert
that and is why it was rejected.

**Why one JSONB row rather than a row per ticker.** A per-ticker table admits a
PARTIAL write, and a ticker missing from it reads as "the broker doesn't hold
it" ⇒ a **fabricated** drift on a correct book. Delete-then-insert is worse — a
crash mid-way empties the table and every holding reads `app_only`. A single-row
upsert is atomic. Same never-fabricate reasoning as F-243's outage gate.

**Zero additional SnapTrade calls.** The `broker` lane's account-selection loop
already fetched every account's positions purely to count them and discarded
the payloads; this persists what was already paid for. Home doing it live would
be 1 + N-accounts reads at `SNAPTRADE_REQUEST_TIMEOUT_SEC = 15` each — a ~90s
worst-case stall on the most-rerun page.

**The write invariant, and it is strict.** A snapshot is written ONLY when
EVERY linked account responded — not merely when one did. The account topology
is one heavy brokerage account plus several empty auxiliaries; the heavy one is
the slowest read and likeliest to time out, and if only it fails the empty
auxiliaries still satisfy the lane's selection guard. The aggregate would be
`{}`, and upserting that says the broker holds nothing ⇒ every real holding
renders as fabricated drift, with the known-good snapshot destroyed. Skipping is
the safe direction: the prior row ages past `SNAPTRADE_BALANCE_STALE_HOURS` into
Home's dated "no mismatch as of ‹date› — not re-checked since". Pinned by
`tests/test_broker_position_snapshot.py`.
### `stock_analyzer/system_health.py`

Pure-ish diagnostic module for the owner-only 🩺 System Trust page (System
Proprioception Phase 1). **INFORMS ONLY — every function is read-only and feeds
no gate, recommendation, composite, or threshold.** Pull-based / render-time:
nothing depends on its own background job. FIVE never-raising checks — ① cron
liveness (`cron_heartbeat`), ② data-store existence + freshness (the "DDL-catcher":
a provably-missing table reads red/"down"; the inventory maps each cron lane to
the stores it writes and whether the write is unconditional-daily or conditional),
③ provider health (`api_health`), ④ in-session `session_state` producer caches —
rolled up by `compute_health()` into a `chip_severity` (worst of ①②③ only; ④ is
excluded so a cold Home run, before Home populates its caches, can't false-positive
the chip). `get_health()` memoizes in `st.session_state["_system_health_cache"]`
(~5-min TTL). The recency windows (`_DAILY_LANE_OK_HOURS` etc.) are **observability
thresholds, not investment policy** — deliberately local to this module, not in
`constants.py`. `check_providers()` re-grades a "down" `api_health` read to "warn"
when the provider's most recent call actually succeeded (`consec_err == 0`) — fixed
2026-08-10 after a live premarket session showed Finnhub stuck red from an earlier
rate-limit burst (`rate_limits >= 3` never decays within a session) minutes after
it had already recovered via the Yahoo Finance failover; see memory
`project_system_proprioception`.

### `stock_analyzer/reference_shelf.py`

Shelf-life registry for the hand-maintained STATIC reference tables (F-238),
rendered as 🩺 System Trust **check ⑤**. **AWARENESS ONLY — nothing here gates a
recommendation, suppresses a pick, or changes a score.** Never raises: a
proprioception layer that can crash the page it reports on is worse than none.
`shelf_status()` is pure (no I/O, no DB) and returns a **list, never `None`** —
this is not a provider that can be "offline", so the offline-sentinel contract
applies PER ROW instead: a row whose source can't be read is graded `"unknown"`
and still rendered, never dropped and never silently `"ok"`.

Two staleness mechanics, because the tables fail two different ways.
**`KIND_AS_OF`** — a membership table with a last-refresh date; stale when age
exceeds `REFERENCE_SHELF_LIFE_DAYS`. Fails as SILENT ABSENCE (a stale universe
stops surfacing names) or a mildly wrong number. Can only reach amber, never
red — it's a chore, not an outage. **`KIND_HORIZON`** — a forward-dated table
that RUNS OUT; stale when runway drops below `REFERENCE_HORIZON_MIN_DAYS`, red
once actually expired. Horizons are always **derived from the table itself**,
never hand-written, so extending the table clears the warning with nothing
second to remember. The macro horizon is the **earliest per-event-series**
expiry, not the global max, so one freshly-extended series can't mask five
expiring ones (`_MACRO_MIN_SERIES_ROWS` = 4 stops a one-off entry becoming a
phantom series that pins the row to a false red).

**The `as_of` rule, clarified 2026-08-16:** `as_of` = the last *deliberate
curation*. Mechanical ticker renames don't count. But a documented review
concluding **"no change needed" DOES earn a new date** — the date records that a
human reconsidered the membership, not that it changed, and any other reading
makes CHURNING a roster the only way to clear the warning, which would degrade
the very tables the check protects. The bar is evidence, not outcome.

### `stock_analyzer/ticker_liveness.py`

Weekly liveness sweep over the three curated rosters (`SECTOR_UNIVERSE` ∪
`DISCOVERY_UNIVERSE` ∪ `_SECTOR_CANDIDATES`, ~230 unique), run as sub-job ⓪ of
the Saturday `maintenance` cron lane (F-241). **AWARENESS / CHORE ONLY — never
gates a recommendation, suppresses a pick, or changes a score.** Exists because
`reference_shelf.py` measures a roster's AGE, which structurally cannot detect
ticker ROT: on 2026-08-16 `CFLT` was found delisted inside a table the age clock
rated **green** at 79d.

**Two-stage by design.** One batched fetch screens the whole roster; if fewer
than `TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT` of names resolve it returns
`"inconclusive"` and reports **no** dead-ticker verdict — the false positive
being defended against (a rate-limited or down provider) hits the entire batch
at once and is therefore measurable inside a single run. Above the floor, only
the handful of suspects escalate individually through the multi-source
Finnhub→yfinance→FMP layer; a miss across **every** provider is the semantic
"unknown symbol". Yahoo's literal 404 is deliberately NOT parsed (yfinance
swallows it, and reaching it directly means depending on an unofficial
crumb-gated endpoint). Confirmation is in **space** (across providers, one run)
rather than in **time** (across weeks) — the latter would need persistence,
coupling a roster-rot check to the very DB whose outage F-239 addressed, and
would delay a true finding by a week.

**Offline contract:** returns `None` — the sentinel — ONLY when the sweep could
not run at all (the batch raised). `"inconclusive"` is a RESULT, not an absence;
callers branch on `is None` separately. The batch is bounded by a module-local
wall-clock cap (`_SWEEP_WALL_CLOCK_CAP_SEC`, an operational knob on the
`system_health.py` precedent, not investment policy) reusing the provider
layer's `_call_with_timeout`, which ABANDONS a hung worker — a plain
`with ThreadPoolExecutor(...)` would block on it at `__exit__` and defeat the
timeout entirely.

**Reporting invariants (cron_runner):** sub-job ⓪ runs BEFORE the DB sub-jobs,
because ① can return early on a Supabase outage and a check needing no DB must
not be starved by one. **A dead ticker is a CURATION CHORE, never a lane
failure** — it never touches `failures`/`rc`/`_notify_failure`, because a red
maintenance heartbeat must keep meaning "the lane died". Only an exception *in
the check itself* fails the lane. Emails **only on a finding** — never a weekly
all-clear (the heartbeat already proves the lane ran) — but `"inconclusive"` and
the offline sentinel DO email, stating plainly that there is no verdict and why:
silence-because-degraded is a wrong state, not health. `shelf_status()` rides
along with a severity split: an *expired* (`down`) table emails on its own;
merely-aging (`warn`) rows are appended only when an email is already going out.

### `stock_analyzer/snaptrade_client.py`

Thin wrapper over the official `snaptrade-python-sdk` (F-244, SnapTrade broker
sync — `docs/plans/snaptrade-broker-integration.md`). Every public function
returns `None` on any failure (missing credentials, timeout, SDK/API error) —
never raises into caller code, matching the multi-source provider convention
(`project_second_data_source`) rather than a bespoke error contract.
Credentials are env-first then `st.secrets` (mirrors `db._supabase_creds()`),
so the module works identically in the headless Railway `broker` cron and the
Streamlit app. `SNAPTRADE_CLIENT_ID`/`SNAPTRADE_CONSUMER_KEY` are the **only**
two credentials — a **Personal** SnapTrade API key (corrected 2026-08-18: the
original build wrongly assumed a Commercial key with a second `userId`/
`userSecret` pair; SnapTrade's own Dashboard states Personal accounts never
register a user and never send `userId`/`userSecret` on any call — confirmed
by omitting them and getting the same clientId-level auth error as supplying
them). `SnapTradeAuth.personal_api_key(...)` is used, not `commercial_api_key`.
Every SDK call is wrapped in `stock_analyzer.providers.yfinance_provider.
_call_with_timeout` (a worker-thread wall-clock bound) rather than a
`timeout=` kwarg — the installed SDK's convenience methods don't expose one
(confirmed via `inspect.signature()` against all six methods this module
calls; a live `TypeError` caught the original mistake). `_record_error()`
special-cases `snaptrade_client.ApiException` to record `f"{e.status}
{e.body}"` rather than `str(e)` — the exception's default string form leads
with HTTP headers, not the actual JSON error body, which `api_health`'s
120-char truncation was cutting off before ever reaching the useful part.
`api_health.record("snaptrade", ...)` feeds 🩺 System Trust's provider-health
check the same way Finnhub/FMP/Yahoo do (added to `system_health._PROVIDERS`
after a live incident where the recorded source had no display surface at all).

### `stock_analyzer/broker_sync.py`

Pure transform/decision logic (no I/O) sitting between `snaptrade_client.py`
and the `broker` cron lane / `app.py` (F-244). Three functions:

**`diff_positions(rh_positions, port_df)`** — three-bucket drift (rh_only /
app_only / qty_mismatch, tolerance `BROKER_DRIFT_SHARE_TOL`) between live
SnapTrade positions and the app's holdings. Returns `None` (the offline
sentinel) when `rh_positions is None`, never collapsing "couldn't read the
broker" into "checked, no drift." Callers MUST pass **raw, untruncated**
shares (`st.session_state.holdings_df`, not the display-enriched `port_df`,
whose `"Shares"` column is `int()`-cast for the UI — a 2026-08-17 review
finding: Robinhood's routine fractional-share holdings otherwise produce a
permanent phantom `qty_mismatch`). Awareness only — never gates, never
auto-corrects `trades`.

**`map_balances_to_cash(rh_balance)`** — maps a raw SnapTrade balance payload
to `{"cash_balance": float, "note": str}`, matching `account_cash`'s signed
convention (negative = margin debit, the existing account-baseline v4 rule).
`None` in → `None` out; an empty list or a missing `cash` field also returns
`None` rather than fabricating a zero balance.

**`classify_transactions(rh_txns, existing_trades)`** — classifies raw
SnapTrade activities into `new_pending` (BUY/SELL candidates for
`snaptrade_pending_imports`), `backfill_broker_txn_id` (a content-matched
existing `trades` row — e.g. previously CSV-imported — that should have this
transaction's id attached instead of becoming a duplicate pending import),
`income_events` (dividend/interest/fee — **display/trend only**), `flows`
(CONTRIBUTION/WITHDRAWAL only — the sole category allowed to reach
`account_flows`), and `ignored` (a `{type: count}` transparency dict for
everything else, e.g. TRANSFER/OPTIONEXPIRATION). **The Modified-Dietz
invariant this function exists to protect:** `account.py`'s
`net_contributed_capital` reads `account_flows`, and a dividend/interest
credit is performance, not a contribution — routing it there would silently
inflate NCC and suppress reported growth%. `_FLOW_TYPES` and `_INCOME_TYPES`
are disjoint by construction; an unrecognized type falls to `ignored`, never
`flows` (the safe direction). Dedup is two-tier: Tier 1 exact match on
`trades.broker_txn_id`; Tier 2 the same `(date, ticker, action,
round(shares,4), round(price,2))` content-match key F-87's CSV importer
already uses, restricted to `trades` rows that don't yet carry a
`broker_txn_id` (so an already-linked row can never be re-matched by Tier 2).

### `stock_analyzer/portfolio_health.py`

Pure-logic module for the 🏆 Health page. No I/O, no Streamlit imports.

**`compute_health_score(port_df, div_score_val, avg_corr, hb_share, fragility, port_risk)`**  
Returns `{overall, grade, grade_label, sub_scores, improvements, n_available, dimension_labels, dimension_icons}`. Computes five sub-scores (each 0–100) and averages to an overall score. Grade bands: A ≥ 80 / B ≥ 65 / C ≥ 50 / D ≥ 35 / F < 35. `improvements` = top 1–2 lowest-scoring dimensions with plain-English action text and a specific named callout (ticker/sector at worst concentration, avg correlation figure, etc.).

**`compute_portfolio_dynamics(port_df, trades_df)`**  
Returns per-position tenure, return efficiency, cohort aggregates, engine alignment counts, and vitality %. Reads first BUY date per ticker from `trades_df` to compute months held; annualised return = `P&L% × (12 / months_held)` (clamped to ≥ 0.5 months). Verdict tiers derived from `COMPOSITE_BUY` / `COMPOSITE_HOLD` / `COMPOSITE_SELL`. Zero API calls.

**Session state consumed (read-only):** `_port_df_enriched`, `_div_score_cache`, `_avg_corr_cache`, `_highbeta_share`, `_fragility_cache`, `_port_risk_cache`, `trades_df`.

### `stock_analyzer/portfolio_intelligence.py`

Pure-logic module for the 🧩 Intelligence page (Concept B, "Portfolio-as-One Positioning Intelligence" — next-evolution roadmap Phase 2). No I/O, no Streamlit imports. Grows one panel per sub-wave; only one panel exists as of this writing.

**`correlation_clusters(corr_df, weights=None, threshold=CORR_HIGH_PAIRS_THRESHOLD, danger_threshold=CORR_DANGER_PAIRS_THRESHOLD)`**  
Groups tickers into transitive correlation clusters via plain-Python connected-components over the existing pairwise correlation matrix (`portfolio.correlation_matrix()`, published to `_corr_df_cache`) — no scipy/sklearn/networkx dependency, no new constants (reuses the two existing thresholds). A-B and B-C both flagged (≥ `threshold`) implies A, B, C are one cluster even when A-C isn't itself flagged. Singletons excluded — only clusters of 2+ members returned. Each cluster: `tickers`, `size`, `avg_internal_corr` (mean of every pair INSIDE the cluster, not just the edges that formed it), `combined_weight_pct`, `tier` (`"danger"` if any internal pair ≥ `danger_threshold`, else `"warning"`). Sorted by combined weight descending (or size descending when `weights` is `None`). Never raises.

**`risk_budget(held_data, weights, trading_days=252)`**  
Euler / marginal-contribution-to-risk decomposition of REALIZED portfolio volatility. Rebuilds its own jointly-aligned returns frame from `held_data` price history (does NOT accept the cached `_corr_df_cache` — the volatility calc and the correlation calc must come from the exact same date alignment, or the covariance model would be internally inconsistent). `vol = returns.std() * sqrt(trading_days)` (annualized) per ticker; `Sigma = np.outer(vol, vol) * corr`; portfolio variance = `w @ Sigma @ w`; each position's risk contribution = `w_i * (Sigma @ w)_i / portfolio_vol`, so `Σ risk_pct ≈ 100` by construction. Returns `{"positions": [{"ticker", "weight_pct" (original capital weight, not renormalized), "risk_pct", "vol_annualized_pct", "risk_to_weight_ratio"}], "portfolio_vol_annualized_pct", "n_included"}`, sorted by `risk_pct` descending. No new constant — deliberately no "outsize risk" flagging threshold, just the plain numbers. Never raises.

**`factor_tilt(held_data, weights, factor_returns, min_overlap_days=20)`**  
Directional style-factor exposure via returns-based correlation (NOT a regression/beta model, per the plan's explicit instruction to reuse "the existing correlation infrastructure"). Pearson-correlates each held position's daily returns against each factor-proxy ETF's returns (`factor_returns`, already fetched by `app.py::_fetch_factor_etf_returns()` — this module stays pure/no-I/O, the network fetch happens in `app.py`). A (ticker, factor) cell requires ≥ `min_overlap_days` (20, mirrors the existing empirical-correlation minimum in `risk.py`'s rate-sensitivity feature) overlapping trading days or it's `None` — never a correlation fabricated off too little data. Returns `{"positions": [{"ticker", "weight_pct", "correlations": {factor: float|None}, "dominant_factor", "dominant_corr"}], "portfolio_tilt": {factor: float|None}, "n_included"}`; `portfolio_tilt` renormalizes weights among only the tickers with a valid correlation for that specific factor (a ticker missing one factor's column doesn't zero-drag it). Tickers with zero usable correlations across all factors are excluded from `positions` entirely (not just from `n_included`) — a row that's blank everywhere isn't a position "included" in this panel. Directional/noisy at small portfolio sizes — realized/historical only, not a forecast.

**Session state consumed (read-only):** `_corr_df_cache` (clusters only), `_last_held_data`, `_port_df_enriched` (for the ticker→weight map). Factor Tilt additionally caches its own fetch+calc result in `_pi_factor_tilt_cache` (button-gated — the only Intelligence panel that costs a new API call, so it doesn't auto-fire on page load).

### `stock_analyzer/behavioral_fingerprint.py`

Pure-logic module for the 🧬 Behavioral Fingerprint tab on 🎯 My Edge (Concept A v1, next-evolution roadmap — Buy-side only; exit-side TRIM/EXIT patterns are out of scope, see the F-192 audit below). No I/O, no Streamlit imports. Every function returns `None` when either compared bucket is below `BEHAVIORAL_MIN_SAMPLE_N` — never presents a directional finding at small N. Reuses `recommendations_history.match_recs_to_trades()` as the sole matching mechanism (not a parallel logger), scoped by the caller (`app.py`) to actionable rec_types only (`new_pick`/`add_winner` — `buy_candidate` excluded, same scoping as the F-192 audit).

**`momentum_recency_pattern(matched, min_n, meaningful_delta_pp=5.0)`**  
Median-splits actionable recs by `momentum_score` (already known at signal time — zero new price fetches) and compares action_rate between the high-momentum half and low-momentum half. `meaningful_delta_pp` (`BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP`, 5.0) is display-copy only — decides whether the returned `direction` reads `"chases"`/`"fades"`/`"flat"`, never whether the card renders at all. Returns `{"high", "low", "delta_pp", "direction"}` or `None`.

**`conviction_tier_pattern(matched, strong_buy_floor, min_n)`**  
Compares action_rate between Strong Buy (`composite_score >= strong_buy_floor`, caller passes the existing `COMPOSITE_STRONG_BUY`) and plain Buy actionable recs — tests whether the investor actually acts more on higher-conviction calls, as rationally expected. Returns `{"strong_buy", "buy", "delta_pp"}` or `None`.

**`opening_window_pattern(enriched, opening_window_min, min_n)`**  
Compares average SPY-adjusted `alpha_pct` between acted+graded recs whose matched trade's `traded_at` (caller pre-converts to US/Eastern) falls within `opening_window_min` minutes of the 9:30 ET open vs. later in the day. Does no timezone math itself — the caller (`app.py`) parses `traded_at` with `pd.to_datetime(..., utc=True).tz_convert("America/New_York")` per the established mixed-offset-parsing convention, wrapped per-row in try/except. Opportunistically reuses `_pac_enriched` (Predictive Analytics' session cache) rather than triggering its own live-price/SPY fetch — shows a "visit Predictive Analytics first" prompt when that cache is absent. Returns `{"opening", "later", "delta_pp"}` or `None`.

**Session state consumed (read-only):** none directly (patterns 1/2 load `recommendations`/`trades` fresh via `db.load_recommendations()`/`st.session_state.get("trades_df")`); pattern 3 opportunistically reads `_pac_enriched` if present.

### `stock_analyzer/portfolio.py` — correlation sample-size diagnostic (F-246)

**`correlation_matrix(held_data)`** builds the daily-return correlation matrix every correlation surface reads (published to `_corr_df_cache`). It uses **listwise deletion** — `pd.DataFrame(series).dropna()` across every ticker at once — so the matrix is computed on the **intersection of all histories**. One short or degraded fetch therefore thins the sample for *every pair in the book*, and a thin sample pushes correlations toward zero, which renders as diversification the user does not have. This is load-bearing: the same matrix feeds the diversification score and classification, the 🧩 Intelligence correlation clusters, F-230 Correlation Under Stress, the Diversification Advisor's ADD recommendations and F-245's stop-out clustering.

**`correlation_coverage(held_data)`** (new, F-246) makes that sample size visible. Returns `{n_tickers, n_obs, shortest_ticker, shortest_len, longest_len, lengths}` — `n_obs` being the daily returns shared by *all* holdings (`len(intersection) − 1` for `pct_change`), and `shortest_ticker` naming the holding with the shortest history (usually, but not provably, the pin — with disjoint indexes every history can be equally long while the intersection is still empty). Returns `None` on the primary empty-matrix condition (fewer than 2 usable histories) — not the only one: two histories with disjoint date indexes give a coverage dict with `n_obs == 0` but an empty matrix, so empty-matrix is a strict superset. Computes no correlation and gates nothing.

Both read **`_close_series_map(held_data)`**, deliberately shared: a coverage figure computed off a different input set would be worse than no figure at all. Do not duplicate that extraction.

**`CORR_MIN_OBS_TRUSTED = 20`** — a display-only measurement floor, deliberately **not** in `constants.py` (same precedent as `stress_test._MIN_STRESS_WINDOW_DAYS` / `account._ANNUALIZE_CAVEAT_MAX_DAYS`): it decides only whether a data-quality *warning* renders, never whether a recommendation fires. The value reuses the app's existing "≥20 overlapping trading days" empirical-correlation minimum (`portfolio_intelligence.factor_tilt`'s `min_overlap_days`, mirroring `risk.py`'s rate-sensitivity floor) rather than introducing a new number.

**Wiring — the coverage figure is PRODUCED beside the matrix, never at render time.** `correlation_coverage(held_data)` is called in `app.py` inside the same `try` as `correlation_matrix(held_data)`, from the same `held_data`, and published to **`_corr_coverage_cache`**; the `except` sets it to `None` (offline sentinel, never a fabricated count). It also travels in the `_home_synth_cache` bundle as `"corr_coverage"` immediately beside `"corr_df"`, restored with `.get()` and republished on the memo-hit path — so both keys are always written together and the coverage figure always describes the matrix actually on screen. `_SYNTH_SCHEMA_VER` was bumped **6 → 7** for that bundle key. **This wiring is the point, not an implementation detail:** computing coverage at render time reads *live* `held_data` while `corr_df` may be a memoized matrix built on an earlier run's histories (the memo signature keys on holdings/date, not on price histories), so a thin matrix whose fetches had since recovered would render as "sample is fine" — the exact inverse of the figure's purpose. Same producer-threaded shape F-230's `n_window_days`/`too_short` uses. Consumers must read the cache, never recompute.

**Session state published:** `_corr_coverage_cache`. **Consumed by:** 🔗 Risk Analysis → 📊 Dashboard (the caption / sub-floor `st.error` beneath the diversification classification) and F-245's 🧯 After My Rules stop-out clustering line, which uses it to withhold any conclusion from a thin-sample correlation.

**`forward_sim.max_pairwise_corr(tickers, corr_df)`** (added with F-246) is the sibling of `mean_pairwise_corr`; both share `forward_sim._pairwise_values()`. It exists because `CORR_HIGH_PAIRS_THRESHOLD` is a **per-pair** constant and a mean cannot support a flat negative: a subset holding one duplicated pair (0.77) among several near-zero pairs averages far below the threshold, so denying duplication on the mean under-alarms on a protective surface. Use the mean to *describe* a set, the max to *deny* that duplication exists. F-245's clustering verdict gates its "correlation is not the driver" line on the max, and gates the opposite branch on a non-empty `risk_pairs` intersection rather than on `max >= threshold` (the max is 2dp-rounded off an already-3dp-rounded matrix while `diversification_score` tests unrounded, so a 0.647 pair could otherwise trip a claim the Dashboard contradicts).

**Known-open (deliberate):** the listwise strategy itself is unchanged. Moving to pairwise-complete correlation would change a shared decision input across all the surfaces above — a behaviour change needing its own design + review pass, not a cleanup. F-246 ships the measurement so that question can be settled with data. Also unchanged: the Dashboard's "weighted avg pairwise **6-month** return correlation" caption, so a mid-range sample (say 40 observations) renders a clean coverage caption directly beneath a line claiming a 6-month window. Nothing is fabricated — the count is always printed immediately below — but a window-aware caption, or a second softer tier at the requested-window level, is a real follow-up.

### `stock_analyzer/attribution_readiness.py`

Pure-logic data-readiness audit for **E2 alpha attribution** (F-247), rendered in the 📊 Alpha Attribution expander on 📊 Predictive Analytics. No Streamlit, no DB, no network. **Introduces no thresholds** — it reports numbers and lets the reader judge. The `ALPHA_ATTRIBUTION_MIN_SNAPSHOT_DAYS = 180` **literal** is untouched, but the quantity compared against it changed from calendar span to distinct captured dates, so the effective bar moved materially stricter (~180 sessions ≈ 8.6 months of unbroken capture) — see the constants-table row. Retuning the number itself is a separate decision and was not taken.

**Why it exists.** The panel previously measured coverage as `(latest − earliest).days + 1` — a **calendar span**. Only ~69% of calendar days are NYSE sessions, and more seriously a cron gap was invisible: snapshots for 5 sessions in March plus 1 in August reported 168 days of "coverage" backed by 6 real dates (the case pinned in the tests). `daily_snapshots` is cron-written and gaps are demonstrated here (F-239's 2026-08-16 lane outage read green until 08-21). Same class as F-246.

**`snapshot_coverage(snaps_df)`** — `None` when no usable dates exist (distinct from "some history, badly gapped", which returns a dict the caller must read). Counts **distinct snapshot dates** (`daily_snapshots`' PK is `(snapshot_date, ticker)`, so a row count would multiply by book size) and measures them against `_expected_sessions()`, which walks `data.is_trading_day` — the codebase's stated single source of truth for "is the market supposed to be open", so **holidays are excluded from the denominator, not just weekends**. Returns `n_dates`, `earliest`/`latest`, `span_days` (the old figure, kept so the honest one renders *beside* it rather than silently replacing it), `expected_sessions`, `missing_sessions`, `largest_gap_sessions`, `completeness_pct`, and `calendar_stale`/`calendar_last_year` (true once the span runs past `MARKET_CALENDAR_LAST_YEAR`, beyond which `is_trading_day` counts untabled holidays as sessions — the denominator is then slightly high and completeness slightly low, which is the safe direction for an audit but must not fail silently; drives its own caption). `largest_gap_sessions` is the load-bearing one: one long outage damages an attribution far more than the same number of scattered misses. `non_session_dates` counts weekend/holiday writes — not a defect, recorded because it lets `n_dates` exceed `expected_sessions`.

**`concentration(snaps_df)`** — Herfindahl and **effective** position count (`1/H`) on the latest snapshot, plus `top_weight_pct`. Reported because Pass #1's warning about E2 was specifically about a *concentrated* book: 18 equally-weighted names is 18 effective bets, 18 names where three carry half the book is far fewer. `None` on a worthless or unusable book, never 0.

**`turnover(trades_df, snaps_df, *, lookback_days=180)`** — two-way turnover, defined in the docstring because turnover has several definitions: traded notional (`shares × price`) ÷ mean daily portfolio market value over the window. BUY and SELL legs are returned separately as well as summed, since a pure accumulation phase is not churn (the conventional `min(buys, sells)` answers that by stripping net build-out). Parses `traded_at` with `utc=True` per the mixed-offset convention (several write paths produce different offsets; without it pandas coerces to `NaT`). Returns `None` rather than a fabricated 0% when a leg is unavailable — 0% reads as "no churn", the opposite of "unknown".

**The legs are also returned as percentages** (`buy_turnover_pct` / `sell_turnover_pct`, same denominator as `window_turnover_pct`, so they sum to it up to independent rounding) so the caller can show the split without handling dollar figures. **Added 2026-08-21 because the shipped panel rendered only the summed figure and dropped both legs** — precisely the ambiguity the function's own docstring claimed to resolve. It surfaced on live data as an uninterpretable **1564% over 73d**: a book *built* inside its own measurement window has both a buy-heavy numerator and a small mean-value denominator, so it reads as extreme churn by construction, and the summed number cannot distinguish that from genuine round-tripping. Pinned by `test_turnover_accumulation_and_churn_differ_in_the_legs_not_the_total`, which asserts two opposite books produce an identical total.

**`window_days` is an INCLUSIVE day count**, matching `snapshot_coverage`'s `span_days` — before 2026-08-21 the two rendered on the same panel as 74 vs 73 days for the same interval, a pure inclusive/exclusive artifact that reads as a bug on a panel whose whole purpose is trustworthy counting. Accepted side effect: the `>= lookback_days` annualisation gate opens one day earlier and the `365/window` multiplier is a hair smaller (the conservative direction). Neither matters on an audit-only panel that gates nothing.

**Two things in `turnover` that a review caught and that must not be undone.** (1) **`action` is REQUIRED and SPLIT rows are filtered.** The Apply-Split handler on 🥧 Portfolio (`app.py`) writes a synthetic `db.save_trade` row with `action='SPLIT'`, `shares` = adjusted TOTAL shares and `price` = adjusted avg cost (special-cased in `db.recalculate_from_trades` to overwrite rather than accumulate the holding), so `shares × price` on a SPLIT row is the position's whole cost basis — counting one injects a full-position-sized fake notional. `trades.py`, `portfolio_qa.py` and `evening_debrief.py` all filter this already; memory `project_split_recalc_deferred` records it as a recurring class. A missing `action` column returns `None`, because then splits are indistinguishable from trades and that is "unknown", not "assume all trades". (2) **`annualised_turnover_pct` is withheld (`None`) until `window_days >= lookback_days`.** Annualising a 10-day history multiplies by ~36 and would print a spectacular meaningless number off a 10-observation mean book value — scaling a short, possibly gapped window up to a year is the same measurement sin this module exists to fix. `window_turnover_pct` is always returned and is the honest figure; `n_snapshot_dates_in_window` states how much data the mean book value rests on.

**Substrate finding worth keeping:** `daily_snapshots` is `(snapshot_date, ticker, shares, close_price)`, written by the cron EOD lane and also opportunistically by a post-close Home view (`app.py`) — a genuine **per-position** weight history. So E2 does not need to *reconstruct* historical weights from `trades`, which is the reconstruction half of the objection F-234 recorded. **It does not extend REACH:** snapshot history may well be SHORTER than trades history, which is precisely what this panel measures — so do not read this as "the data goes back as far as trades". **Known constraint any sector decomposition must state:** `resolve_sector`/`TICKER_SECTORS` is an as-of-*now* curated map, so a historical sector attribution applies today's taxonomy to past holdings — live, not hypothetical, since the rosters were refreshed 2026-08-16/17 (F-240/F-242).

### `stock_analyzer/forward_sim.py`

Pure-logic module for the **🧯 After My Rules** tab on 🔗 Risk Analysis (F-245 — Experimental Track E1 from `docs/plans/next-evolution-strategy.md`). No I/O, no Streamlit imports, no DB writes. Answers the one question `stress_test.py` (which contains no stop or gate logic at all) and F-224 Outcome Range both leave open: *after the app's own mechanical rules fire on a shocked book, what is left?* — i.e. it tests the **interaction** of rules that were each set as isolated policy decisions.

**Faithful by construction — the load-bearing property.** Tier decisions come from `exit_advisor.classify_deterioration_tier` (the same pure scalar core `assess_holding`/the Brief use), the regime read from `exit_advisor.risk_off_regime`, the overlay from `exit_advisor.assess_risk_off_derisk`, and the per-position shock from `stress_test.run_scenario`. This module only re-extracts the deterioration *scalars* at a substituted price — it never reimplements a rule. That extraction necessarily duplicates `assess_holding`'s peak-window / trend-MA / below-MA math, which is the module's single biggest risk (a drifted replay would report a book the app would never produce, and do it authoritatively), so `tests/test_forward_sim.py::test_zero_shock_matches_assess_holding` pins tier + 7 scalars against `assess_holding` at zero shock. Do not "optimise" the extraction without that test staying green.

**`replay_position(*, ticker, df, price_now, move_pct, spy_move, atr, avg_cost, shares, stop, age_days=None, peak_window_days=None)`**
One position's outcome at the shocked price. Returns `None` — never a fabricated tier — when the Close series, the `SMA_<DETERIORATION_TREND_MA>` column, or a positive peak is unavailable (the caller reports these as `uncovered`, not as safe). `atr_pct` is taken against the *shocked* price so the ATR-scaled TRIM/EXIT floors widen as the price falls, as they would live. `rel_strength` is **additive**: the engine's real trailing RS (via `exit_advisor._pct_return` over `REL_STRENGTH_LOOKBACK_DAYS`) **plus** the scenario differential (`move_pct − spy_move`). It must never *replace* the real reading — a first cut did, and it was a live defect: in the 6 of 9 sector-targeted scenarios a holding outside `_SECTOR_SHOCKS` gets `est_move = 0.0`, so the differential alone read `0 − (−10) = +10`, a fabricated positive strength that switched `trim_active` off on a name the Brief was calling TRIM/EXIT the same session. Additive is also what makes the zero-shock identity test bind on RS at all. `_live_rel_strength` returns **`None`** (not the engine's 0.0) when either leg is missing, and `replay_position` records `rel_strength_live: False` — because the engine's 0.0 is a fail-safe meaning "an unknown RS must never open an action tier", and **that guarantee does not survive composition here** (0.0 plus a negative scenario differential is still negative, so a degraded benchmark fetch could open a TRIM the engine would have withheld). Those tickers are collected into `rel_strength_degraded` and named in the UI, so this unknown is surfaced like every other one in the module rather than blending in. `stop` MUST be `port_df["Stop"]` (the ratcheted `max(ATR stop, ratchet floor)` or tighter manual), never the raw ATR bundle stop; a missing stop yields `stop_breached = None`, never `False`. Breach mirrors the Brief exactly: `round((price − stop)/price × 100, GAP_TO_STOP_ROUND_DECIMALS) <= 0`. Emits both tiers — `TIER_DAY1` (real pre-shock `below_ma_count`) and `TIER_CONFIRMED` (`below_ma_count = DETERIORATION_CONFIRM_DAYS`).

**`mean_pairwise_corr(tickers, corr_df)`**
Mean pairwise correlation across a ticker set (used on the stop-out set — the headline finding, since highly-correlated simultaneous stop-outs mean N positions were really a handful of bets). Reads the existing `_corr_df_cache` matrix. Returns `None`, never `0.0`, when fewer than two pairs resolve — a missing correlation read must not render as "uncorrelated". Uses the established guard (membership in BOTH index and columns, plus rejecting a non-scalar `.loc` result, since duplicate labels make `corr_df.loc[a, b]` return a frame).

**`shock_spy_frame(spy_trend_df, spy_move)`** / **`shock_port_df(port_df, moves)`**
Copies (never mutate the caller's frame) that feed the risk-off functions verbatim. The SPY frame moves only its **final** bar. That bar *is* inside `close.tail(200).mean()`, so the mean drops by ~1/200th of the move while the last close drops by the full move — the gap widens, so the trend break can never be *under*-stated (a real multi-week decline would drag the mean down further, making the break marginally harder to trip). Directionally protective, and stated rather than hidden. `shock_port_df` reprices and renormalises weights because `assess_risk_off_derisk`'s β × weight ranking is weight-sensitive; rows absent from `moves` (which `run_scenario` skipped for non-positive market value) are deliberately left untouched rather than given an invented move.

**`simulate(scenario, port_df, held_data, *, spy_trend_df, vix_level, fragility, portfolio_beta, custom_spy_move)`**
Orchestrator. Reads `held_data[t]`'s `df`/`atr`/`position_age_days`/`material_add_age_days`/`risk_metrics.beta` — the same inputs `daily_briefing.deterioration_signals` already passes to `assess_holding`. Risk-off is **derived, not assumed**: `risk_off_regime`'s two legs are OR'd, so the shocked-SPY trend leg alone arms it and no VIX value is required; `fragility` (the outer AND-gate) is taken live because fragility is a property of the book, not of the shock. **`exclude_tickers` covers every name carrying ANY flag in either column, not just TRIM/EXIT** — the live path passes `decision_bucket.all_flagged_tickers()`, which deliberately includes WATCH because the 2026-07-29 audit (H6) found a narrower TRIM-only filter let a WATCH card and a same-render risk-off "trim now" card coexist for one ticker; a narrower filter here would reproduce that contradiction on one screen. Returns the bracket counts, `stop_outs`, `stop_unavailable`, the risk-off payload, per-tier `survivors`, `uncovered` (no usable history) and `no_value` (no live market value — its own bucket, since a holding the sim cannot see is a gap, not a non-holding).

**Three separate offline states on the risk-off payload, never collapsed.** `available` (is there a SPY frame at all), `fragility_available` (is `_fragility_cache` a real severity), and `armed`. `fragility` is a publish/consume cache that is `None` **when its producer failed**, not when the book is calm — treating those as the same thing would render an offline read as the benign "the overlay would not arm", which is exactly the sentinel-collapse class `check_antipatterns.py` exists to catch. The UI branches on all three and says "unknown" for the first two.

**`_survivors(positions, which, held_data)`**
An exit = a breached stop OR an EXIT tier. A **TRIM is not liquidated** — its size is a per-card directive, so a fixed haircut would invent a number; TRIMs are counted and reported instead. Surviving beta is value-weighted and `None` when no survivor carries a beta (never a fabricated 1.0). Reports `proceeds_pct` against post-shock equity but deliberately does **not** compare it to `REGIME_CASH_FLOOR_PCT` — that comparison implies a redeployment directive, which this diagnostic does not issue.

**No new constants.** Reuses `DETERIORATION_TREND_MA`, `DETERIORATION_CONFIRM_DAYS`, `DETERIORATION_PEAK_FALLBACK_BARS`, `GAP_TO_STOP_ROUND_DECIMALS`. **Session state consumed (read-only):** `_fragility_cache`, `_corr_df_cache` (both via `app.py`, which passes them in — the module stays pure). Publishes nothing; nothing downstream consumes it.

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
| `_port_df_enriched` | My Portfolio, **plus Trade Journal and trade-write operations (via `_refresh_portfolio_cache_after_trade`)** | Stock Analysis, Today's Brief |
| `_live_prices` | Price strip fragment | Portfolio P&L table |
| `_last_port_df` | My Portfolio, **plus Trade Journal and trade-write operations (via `_refresh_portfolio_cache_after_trade`)** | Trade Journal decision context |
| `_signals_computed_at` | My Portfolio (after port_df build) | Portfolio table caption, Trade Journal signal pre-fill help |
| `_portfolio_value` | My Portfolio, **plus Trade Journal and trade-write operations (via `_refresh_portfolio_cache_after_trade`)** | Sidebar display |
| `scanner_results` | Market Scanner, **or hydrated once/session from the `scanner_cache` Supabase table** (written by the cron `scan` mode ~10:00 ET, or a manual full-universe Home scan) | Today's Brief buy candidates + Grow Today new picks — now populated on a cold load without a manual scan (`_scanner_results_meta` carries the freshness stamp; hydrate bumps `_scanner_ver`) |
| `_sidebar_news` | My Portfolio / Stock Analysis | Sidebar news slot |
| `_qr_result` | Today's Brief quick research | Today's Brief (persists result) |
| `_tj_last_submit_sig` / `_tj_last_submit_ts` | Trade Journal (on submit) | Trade Journal double-submit dedupe (next submit) |

**Decision-coordination caches (see §4.0.2 for the gates that consume each):**

| Key | Set by | Read by |
|-----|--------|---------|
| `_port_risk_cache` | My Portfolio | Stock Analysis Trade Plan, Watchlist |
| `_risk_high_alerts_cache` | My Portfolio | Watchlist |
| `_grow_today_sectors_cache` | After `build_daily_briefing` | Watchlist |
| `_grow_composites` | My Portfolio (top-5 scanner pre-fetch + mover bundles) | Daily Briefing `_grow_today` |
| `_grow_composites_coverage` | My Portfolio (post-fetch) | Grow Today banner |
| `_movers_candidates` | My Portfolio (`_cached_scan_movers` + composite gate) | Daily Briefing `_grow_today` (movers= arg) |
| `_daily_brief_offline` | My Portfolio (on briefing exception) | Watchlist offline banner |
| `_tj_prefill` | Watchlist "Log Planned Trade" | Trade Journal form prefill |
| `_home_synth_cache` | My Portfolio (after full synthesis) | My Portfolio (on next rerun) — memoization cache for the synthesis bundle. As of 2026-07-15, the HIT path also runs a composite-freshness check (F-181) that re-fetches stale bundles and re-runs build_daily_briefing() without invalidating the signature. |
| `_scanner_ver` | Market Scanner (on Refresh Signals) + My Portfolio (after Market Scanner page scan) | Synthesis signature — bumped when scanner_results is replaced |
| `_brief_refresh_nonce` | My Portfolio (Refresh Macro, Lock, Unlock buttons) | Synthesis signature — bumped by user refresh/lock/unlock clicks |

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

@st.cache_data(ttl=1800)   # 30 minutes
def _cached_scan_movers(exclude_key, min_gain):
    # scanner.scan_movers() over the ~200-name discovery_universe
    # Ranks today's 1-day gainers >= min_gain; excludes curated/held/watchlist
    # Result shortlist is then composite-gated at the call site

@st.cache_data(ttl=3600)   # 60 minutes
def _fetch_sector_returns():
    # Downloads all sector ETFs (batch)
    # Computes 1W/1M/3M/6M returns

@st.cache_data(ttl=86400)  # 24 hours
def _get_rfr():
    # Fetches 13-week T-bill rate (^IRX) as annual decimal
    # Fallback: 0.045 (4.5%) if Yahoo Finance unavailable
    # Used for Sharpe and Sortino calculations across all risk functions

@st.cache_data(ttl=300)    # 5 minutes
def _cached_price_xcheck(tickers_key):
    # Held-position price cross-check (Finnhub vs yfinance).
    # 5-min TTL: a periodic integrity guardrail, not a live feed — must not
    # re-run every rerun or burn the keyed quota.

# Not cached (always fresh):
fetch_market_indices()      # Called on Daily Briefing load
fetch_live_prices()         # Called by 60s auto-refresh fragment (Finnhub real-time primary)
```

---

## 9. Deployment

**Railway Hobby is the primary deploy** (`drishta.up.railway.app`) as of the 2026-08-15
cutover, after a 3-week parallel pilot that ran clean from 2026-07-24 — full phase log
and rationale in [docs/plans/railway-migration.md](plans/railway-migration.md). The same
Railway project also hosts all 6 cron lanes (§12.6).

Streamlit Community Cloud is **retained as a dormant cold fallback**: it still auto-deploys
from the same `main` branch against the same Supabase DB, but it is not the surface changes
are verified against, and its free-tier throttling notices are expected. The two don't
conflict; either can be reloaded independently. It is kept rather than deleted because
deletion is effectively irreversible (full re-setup + re-entering every secret) and it
costs nothing to leave in place.

| Attribute | Railway Hobby (primary) | Streamlit Community Cloud (fallback) |
|-----------|--------------------------|--------------------------------------|
| Repository / Branch | GitHub, `main` | GitHub, `main` |
| Entry point | `app.py` via `railway_start.sh` (see 9.1b) | `app.py` |
| Python version | 3.12 (`runtime.txt`, nixpacks) | 3.12 (`runtime.txt`) |
| Dependencies | `requirements.txt` | `requirements.txt` |
| Config file | `railway.toml` (nixpacks build, healthcheck `/_stcore/health`, `replicas = 1`) | — |
| Secrets management | Variables tab (flat env vars only — no file-based secrets UI found) | Secrets dashboard, native `.streamlit/secrets.toml` format |
| Sleep behaviour | "Serverless" toggle enabled (Settings → Deploy) — sleeps after 10 min idle, wakes on next request from a cached build image | Sleeps after inactivity (~15–30s cold-start wake) |

### 9.1 Required Secrets

**Railway (primary)** — set as flat Service Variables. `railway_start.sh` materializes
these into `.streamlit/secrets.toml` at container boot (see 9.1b):

| Variable | Required | Purpose |
|---|---|---|
| `SUPABASE_URL` | yes | Supabase project URL |
| `SUPABASE_KEY` | yes | **service-role / secret** key — not the publishable/anon key |
| `ANTHROPIC_API_KEY` | yes | AI Snapshot, AI Insights, broker-import ticker fallback |
| `APP_PASSWORD` | yes | owner login for the password gate |
| `APP_READONLY_PASSWORD` | no | read-only viewer account |
| `FRED_API_KEY` | no | macro calendar released values |
| `FINNHUB_API_KEY` | no | primary live-price provider |
| `FMP_API_KEY` | no | tertiary live-price provider |

The 6 Cron Job services inherit these via Railway **Shared Variables** and add their own
`ALERT_RUN_MODE` plus the email trio (`RESEND_API_KEY`, `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`),
which the web service does not need.

Optional alternate LLM providers (`OPENAI_API_KEY`, `GOOGLE_API_KEY`) are read env-first by
the AI provider selector in `app.py`, so they work as plain Railway variables without a
`railway_start.sh` line.

All secrets are accessed via `st.secrets["KEY_NAME"]` (or `st.secrets["section"]["key"]`) in the
application code. They are never committed to the repository.

**Streamlit Cloud (fallback)** — the same values in its Secrets dashboard, in native TOML
form. Only needs refreshing if the fallback is ever actually activated.

#### 9.1b Why Railway needs `railway_start.sh`

~50 call sites across `app.py` and `stock_analyzer/` call `st.secrets.get(...)` directly (not the
safer `providers/_util.get_secret()` dual-source helper). Railway's Variables tab only injects
flat env vars — there is no `.streamlit/secrets.toml` file on disk. When that file doesn't exist
**at all**, Streamlit's lazy secrets loader raises `StreamlitSecretNotFoundError` on the *first*
access anywhere in the app (not a graceful per-key miss), which crashed Home on first deploy.
`railway_start.sh` writes a real `.streamlit/secrets.toml` from the env vars before launching
Streamlit, so every `st.secrets` call site works unmodified — Railway's Variables tab stays the
single source of truth, and the script re-runs (re-materializing the file) on every container
start, including wake-from-sleep. `app.py::_check_password()` additionally has a direct
`APP_PASSWORD`/`APP_READONLY_PASSWORD` env-var fallback (belt-and-suspenders, since the
materialized file already covers it) plus a brute-force lockout (3+ fails → 2s delay, 10 fails →
5-min lockout via `_login_fails`/`_login_locked_until` session_state keys) — Railway has no
Streamlit-native "Private app" OAuth layer, so the password gate needed its own rate limiting.

### 9.2 Deployment Process

1. Push changes to `main` branch on GitHub
2. Railway detects the push and auto-redeploys; Streamlit Cloud independently redeploys the dormant fallback off the same push
3. Typical redeploy time: 1–3 minutes (both platforms)
4. Verify against `drishta.up.railway.app` with a hard refresh (Ctrl+F5) — not the Streamlit URL
5. GitHub Actions runs `tests.yml` (pytest over `stock_analyzer/` pure logic, 185 tests as of 2026-07-27) and `docs-check.yml` (constants-doc coverage) on push, but **neither gates the deploy** — Railway and Streamlit Cloud don't consult GitHub Actions, so a red ❌ shows up on the commit, not a blocked deploy. Manual testing (post-deploy smoke check + feature click-through) remains the quality gate for anything outside `stock_analyzer/`'s pure logic — see `docs/testing-strategy.md` for the full split.

---

## 10. Known Behaviours and Design Decisions

| Area | Behaviour | Rationale |
|------|-----------|-----------|
| Recommendations History — buy_candidate price snapshot (F-160) | `_buy_candidates()` (`daily_briefing.py`) now captures `"price"` from its source row (`scanner_results` or `port_df`) on both append points; `app.py`'s buy_candidate write passes it through to `_price_for()`, matching the existing `new_pick` pattern. Before commit `51b2441` (2026-07-26), `buy_candidate` rows never carried this field, so `price_at_surface` fell through to the held-position-only fallback and came back `None` for any not-yet-held candidate ticker — structurally, not just slowly, since the fallback can never resolve a name that was never held. | Found during a manual re-run of the 2026-06-18 engine-quality review: the by-verdict and by-composite-band rollups showed blank missed-alpha for Conflicted/Hold-zone/Sell-zone (421+ historical rows) — the app couldn't verify its own "skipping conflicted names saves money" claim. **Not retroactive** — pre-fix rows stay blank permanently (no historical price-at-date to backfill without a separate OHLC job). Pure analytics fix; no live gate reads `price_at_surface`. Opus-reviewed SHIP, 0 blocking. |
| Held-position deterioration exit (WATCH/TRIM/EXIT) | `exit_advisor.assess_holding` issues a 3-tier exit signal per holding: **WATCH** (Review/awareness lane) when down ≥ `DETERIORATION_WATCH_DD_PCT` (6%) from its peak-since-entry AND below SMA50; **TRIM** (Act Today) when down past `max(DETERIORATION_TRIM_DD_PCT, ATR_MULT_TRIM·ATR%)` capped at `DETERIORATION_TRIM_DD_CEILING`, below SMA50 for 2 of 3 sessions, AND lagging SPY over `REL_STRENGTH_LOOKBACK_DAYS`; **EXIT** (Act Today, reduce aggressively) when TRIM holds AND (underwater vs cost OR unrealized $ loss ≥ `DETERIORATION_EXIT_DOLLAR_LOSS` OR a deep drawdown). A DEEP drawdown fires EXIT **without** the 2-of-3 confirmation (depth IS confirmation) and is **never silenced by settling grace**; routine WATCH/TRIM ARE silenced inside `POSITION_SETTLING_DAYS`. `daily_briefing.deterioration_signals` produces these; TRIM/EXIT enter `_act_today` (deduped — a `stop_breach`/`sell_signal` on the same ticker wins), WATCH enters `_review_list`. Act-Today order: stop > Sell > EXIT > TRIM > other, then dollar risk (`_KIND_RANK`). | A trade-log review (2026-06-22) found ~$1,465 of realized loss in held positions the app never flagged — the composite sat inside Hold (44–64) while the name bled and the user exited manually on trend. This fills the gap between "Hold" and a score-collapse "Sell (<30)". RS-vs-SPY scopes Phase 1 to *idiosyncratic* weakness; market-wide risk-off (the Nasdaq-pulldown bucket) is deferred to Phase 2. Thresholds are investment-policy (constants.py). **Phase 1.1 (commit 88f0355): material-add peak re-anchor** — `exit_advisor.material_add_window_days` clips the peak window to "since a ≥25% add" (`MATERIAL_ADD_RESET_THRESHOLD`) so averaging down can't false-EXIT off a stale pre-add high; cost basis stays blended (re-anchoring it would only loosen the exit). Card hysteresis remains declared-but-deferred. (Commit 753c851; plan docs/plans/exit-discipline.md.) |
| Risk-off protective de-risk (exit-discipline Phase 2) | `exit_advisor.assess_risk_off_derisk` issues a `risk_off_derisk` Act-Today TRIM card on the top beta contributors, but ONLY under two AND-gates: (1) the book is fragile — `_fragility` severity ∈ {caution, fragile}; AND (2) the market is in a risk-off **regime** via `risk_off_regime` — EITHER leg trips it: SPY's latest close below its `RISK_OFF_TREND_MA` (200)-day SMA (Faber) OR VIX ≥ `RISK_OFF_VIX_LEVEL` (25). A regime, NOT a single down day (won't sell the dip); each leg degrades to "not tripped" on missing/short data. Selection = risk budgeting: rank holdings by β×weight, keep β ≥ `RISK_OFF_NAME_MIN_BETA` (1.2), take the top `RISK_OFF_TRIM_TOP_N` (3), and **exclude any ticker already carrying a higher-priority reduce** (single-surface — never double-reduce). Action = suggest a ~`RISK_OFF_TRIM_PCT` (25%) trim OR tighten the stop to `STOP_TIGHTEN_ATR_MULT`×ATR ("don't sell into weakness"), directives only. Computed in `build_daily_briefing` AFTER act+review are built (so the exclusion set is complete) and appended to `act` as the lowest-priority reduce; `risk_off_derisk` is in `decision_bucket._ACT_KINDS` (stays in the Act lane) and `_REDUCE_ACT_KINDS` (folds a contradictory same-ticker hold/news card via `_reconcile_act`). VIX via `data.fetch_vix` + `app._cached_vix` (None → trend leg only); 200-DMA needs `_cached_spy("1y")`. Renders via the existing generic Act card. | Phase 1's relative-strength gate deliberately skips market-wide down days (idiosyncratic-only), so the −$396 Nasdaq-pulldown day (2026-06-09) fell through. This promotes the standing Fragility gauge from awareness into a concrete trim on the names actually driving the risk — a LIGHT, industry-grounded overlay (Faber trend + VIX regime + risk budgeting), not a market-timer (aggressive tactical de-risking underperforms after whipsaw/taxes; most risk stays managed at entry — sizing + concentration caps). Constants are investment-policy. Opus-reviewed SHIP. (Commit 1c5c56d; plan docs/plans/exit-discipline.md §Phase 2.) |
| Protective email-alert cron (exit-discipline Phase 3) | A GitHub Actions cron (`.github/workflows/alerts.yml` → `cron_runner.py`) runs headlessly (NO Streamlit) once per ET trading day pre-market (~08:00 ET) and emails the user when the protective action set changes. `headless_alert_engine.compute_protective_alerts` reuses the SAME pure logic as the Brief — loads holdings/trades/stops from Supabase, builds `held_data` via the shared `bundle_loader.load_bundle` (the extracted `load_all` body; app.py's `load_all` is now a thin `@st.cache_data` wrapper over it, so app + cron never drift), then `build_portfolio_df` → `assess_fragility` → PROTECTIVE signals only: stop breaches + deterioration **EXIT** + `risk_off_derisk`, with the same single-surface dedup (a `reduced` set). `notify.py` renders the HTML + sends via the Resend HTTP API. Credentials resolve env-first (`SUPABASE_URL`/`SUPABASE_KEY`, provider keys) then `st.secrets` (`db._supabase_creds`). Dedup state (once-per-day + changed-set fingerprint by kind:ticker) lives in the `alert_state` Supabase table (RLS `FOR ALL TO service_role`; degrades to always-send if absent). Two UTC cron slots (12:00 + 13:00) straddle EST/EDT; guards (trading-day + ET-hour ≥ `ALERT_EMAIL_HOUR_ET` + once-per-day flag) fire exactly one send/day. Ships INERT — no `RESEND_API_KEY` ⇒ computes + logs, sends nothing. Pre-market scope uses last close (no live-price merge) — matches the "closed below stop" rule. **Dedup contract:** `alert_state` row 1 is written only on a real send OR a legitimately-empty run (no alerts); a transient Resend failure leaves state unsaved so the later DST slot can retry — matching the buy-lane dedup contract (row 3). Mode is derived from ET hour (< 12 ⇒ premarket, ≥ 12 ⇒ eod) so YAML schedule changes can't accidentally fire the wrong lane; only `ALERT_RUN_MODE=scan` is still respected as an override. (C1/C2/L9, 2026-06-26 audit.) | Phase 1+2 protective signals only reached the user inside the app; this delivers the genuine "reduce today" calls without a visit. Narrow by design (no grow/buy/review — not "reach me now"). The headless engine + workflow are the reusable foundation for the queued pullback-awareness Phase 2 alert and the Today's-P&L EOD job (add jobs, not a new pipeline). Opus-reviewed SHIP. (Commit 9add28f; plan docs/plans/email-alerts-cron.md.) |
| Brief tone-staleness reconciliation | Pre-market, the market-tone banner (`_db_tone`, computed from the last scanner run) reflects the LAST close while the Pre-Market Intel panel shows LIVE futures — a red "Protect Mode" above green futures is misleading. When the tone is stale (`not is_open AND _db_last_close != today`) AND live futures materially contradict it (tone bear + futures bull, or tone bull + futures bear — reuses `futures_tone`'s own bull/bear classification, no new threshold), an amber reconciliation note is appended to the banner ("Reflects <day> close — live futures currently higher (ES +x%); refresh after the open"). The live futures direction is captured into `_pm_futures_tone`/`_pm_es_pct` inside the pre-market block (init to None before it → NameError-safe; the banner always renders). **Annotate only — futures NEVER change the tone / gates / recommendations** (futures ≠ the open). | A stale bearish tone sitting above live-bullish futures could push a user to defensively defer when futures point higher. Make the contradiction explicit instead of letting the two banners silently fight — without whipsawing the tone on a transient pre-open signal. (Commit 307cac6.) |
| Action Log Phase B — in-context "log this trim" | Review cards with a trim action (`TRIM_TO_TARGET` / `TRIM_AND_TIGHTEN` / `PROTECTIVE_TRIM`) render a "📒 Log this trim" button (`_render_trade_button`) that pre-fills the Trade Journal SELL form (trim ticker + suggested shares + decision context, `followed_intent="yes"`) and navigates there — so the executed trim is recorded without leaving the brief, and once logged holdings recompute and the trim recommendation stops re-firing (closes the recommend→act→log loop, extending Phase A's stop-override pattern). `PROTECTIVE_TRIM` trims `action.trim_ticker` (the weakest sector holding; the card's headline `ticker` is None), so the trim subject is computed BEFORE the headline-ticker gate (`if _db_ticker or _has_trim:`) and the Analyze button is conditional on `_db_ticker`. Button keys use a per-render card index (`_card_idx`, threaded through `_render_defensive_card`→`_render_review_card`; act loop `enumerate`, awareness loop `1000+i`) → collision-proof when two macro cards trim the same name. | Logging affordance only — no change to what's recommended. Phase A closed the loop for stop-raises (manual_stops); this does it for trims so the same trim doesn't re-nag after you've acted. (Commit 307cac6.) |
| EOD cron — pullback alert + Today's-P&L snapshot | The headless cron (`cron_runner.py`) has a second **end-of-day mode** (alongside the pre-market protective run). `headless_alert_engine.compute_eod` reuses the SAME `_build_context` prep, then: (1) **Today's-P&L snapshot** — builds `{ticker, shares, close_price}` rows from `port_df` (post-close → last close is final) and writes them via `db.save_daily_snapshot`, so the next-day Today's-P&L baseline exists deterministically even on days the app isn't opened (closes the write-on-view gaps); (2) **reactive pullback alert** (pullback-awareness Phase 2) — `_assess_pullback` fires only when the broad market (SPY) actually closed down ≥ `PULLBACK_ALERT_INDEX_PCT` (-3.0%) that day; emails an AWARENESS ping (book-implied move = fragility ×-market multiplier × index move + top exposed names) — explicitly NOT a directive (the actionable de-risk is the pre-market `risk_off_derisk`). Mode is derived from ET hour (≥12 ⇒ eod) or set via `ALERT_RUN_MODE` / the workflow `mode` input; the EOD run guards on trading-day + ET hour ≥ `ALERT_EOD_HOUR_ET` (16). Dedup lanes are independent: `alert_state` row 1 (protective) vs row 2 (pullback) — same table, no new DDL. Workflow has two EOD UTC slots (20:30 + 21:30) straddling EST/EDT. | Completes the loss-protection delivery: the reactive "the pullback is here, here's your exposure" leg (the −$396 / 2026-06-09 motivating case) and a deterministic EOD snapshot so Today's-P&L isn't blank on unviewed days. Reuses the shipped engine/workflow (add modes, not a new pipeline). Both new constants are operational alert knobs. Opus-reviewed SHIP. (Commit cb37862; plan docs/plans/email-alerts-cron.md.) |
| Thesis evidence — single extractor (F-1 review + F-5 authoring) | `thesis_advisor.bundle_evidence(bundle)` is the ONE place that turns a `load_bundle` result into the thesis evidence package: `technical` from the `df` DataFrame (above_sma50 / rsi / momentum_1m_pct), `fundamentals` from the nested `financials` dict (yfinance FRACTIONS ×100 → revenue_growth / profit_margin / earnings_trend), and `news_headlines` from `headlines`. All three consumers route through it — the F-1 on-demand "Re-evaluate", the weekly thesis cron (`cron_runner`), and F-5 authoring (`build_authoring_inputs`). Pure (duck-typed df access, no pandas import); every field degrades to None/empty so a thin bundle yields a thin-but-honest package. | Both F-1 review call sites had read bundle keys that don't exist (`indicators` / `revenue_growth` / `news` — the bundle actually has `df` / `financials` / `headlines`), so the LLM graded each thesis on its own text with empty technicals/fundamentals/news (seen in prod: "the evidence provided does not include any current data"). Centralizing the extraction fixes the class at the boundary and stops review and authoring from drifting on key names. (Commits a7f22da F-5 / 955b071 fix.) |
| Analysis stop display + explainer (Hold & Buy-add) | For a **held** position the Analysis Trade/Position-Monitor tab shows the **ratcheted / manual protective stop** (`_sa_holding["Stop"]` = `max(ATR stop, profit-ratchet floor)`, or a tighter manual override) as the Stop Loss metric, the Hold narrative and the Price-Scenarios line — NOT the raw ATR entry stop `r["stop"]`, which under-reports it once a ratchet tier engages (non-held Hold keeps `r["stop"]`). A read-only opt-in expander (`app._render_stop_ladder`, fed by the pure `portfolio.stop_ladder`) explains the stack: a **ladder chart** (active stop = ✓-star, losing candidate dimmed, "tightest wins" arrow, red/green stop-out-vs-holding zones, colour-coded role legend), a **profit-lock staircase** (stepped floor-vs-price from `stop_ladder`'s `ratchet_rungs`, with a dotted actual-stop line), a **3-layer walk-through**, and a **what-if price simulator** (downside vs today's stop; upside = the stop the app would then recommend raising to). It also renders on the Buy/Strong-Buy tab of a held name (`buy_add=True`), distinguishing the **sizing stop** (`r["stop"]` — ATR fresh-entry or manual, what the add's shares & R:R are sized off) from the **protective stop** (ratcheted). `ATR_STOP_MULT` (2.0) single-sources the initial-stop width across `risk.atr_stop_loss`, `bundle_loader` and `stop_ladder`. | AAPL's stop was labelled "Breakeven guard" (the +10% tier reached) while the number came from the ATR volatility stop — the tier-label-vs-binding-number gap was unclear; and the metric under-reported the protective stop vs the Brief (a latent contradiction). Read-only / educational — **no gate, threshold, or recommendation changes**; add-sizing deliberately stays on the ATR fresh-entry stop (sizing off the ratchet is always ≥ the ATR stop → smaller risk/share → over-buys into strength, pro-cyclical). Honesty invariant: `protective_stop` is stateless, so the ratchet floor is a **per-run recommendation the user places**, not an automatic non-retreating guarantee. **Manual-gate unified (2026-07-07):** the Analysis manual-override now gates on the same ratcheted `protective_stop` (= `max(ATR, floor)`; avg_cost from the frozen synthesis frame `_port_df_enriched["Avg Cost"]`) as `build_portfolio_df` — via the shared `portfolio.manual_stop_wins` predicate — so a manual stop in the `[ATR, floor)` gap is rejected on both and Analysis agrees with the Brief. **Breach-gate also unified (2026-07-07):** the Analysis `_stop_breached` gate now reads the ratcheted/manual `_sa_holding["Stop"]` and uses the Brief's exact `round(gap,1) ≤ 0` test, so the red breach banner fires exactly when the Brief flags a breach (`ps`/R:R still on `r["stop"]` = ATR fresh-entry — only the gate + banner label moved). **Price-basis unified (2026-07-07):** the Analysis page now merges the same `_live_prices` snapshot into `results` current_price before the stop gates (mirrors the Home merge into `held_data` before `build_portfolio_df`; reuses the strip snapshot, zero extra API cost, held tickers only, shallow-copy so the cached bundle isn't mutated), so the breach/manual gates + Price/P&L/sizing/R:R all run on the live price the Brief used → the mirror is now **price-exact**. All three stop divergences (manual-gate + breach-gate + price-basis) closed; Analysis fully mirrors the Brief on stops. Only inherent floor (not a defect): `_live_prices` refreshes every 60s while the synthesis snapshot is frozen, so Analysis can read a slightly newer price than `build_portfolio_df` used (same skew as Home's live strip vs its frozen `port_df`; self-heals). **Review-cluster hardening (2026-07-07 — P1 `fdcd908` / P2 `8402390` / P3 `3016226`):** an incremental `/code-review` of the three unifies surfaced 8 findings (`docs/reviews/2026-07-07-review.md`) — 7 fixed, 1 accepted. **(F1/F6)** the manual-gate avg-cost map is now sourced from `_port_df_enriched["Avg Cost"]` with **per-row** coercion, closing a bare-`except` that wrapped the whole loop and could wipe the entire map on one unparseable cell → every `_avg=0` → the gate silently dropped to the raw ATR stop for the whole book (re-opening the split-brain). **(F3)** the breach gate mirrors the Brief's Stop-Unavailable skip via `pd.isna("Gap to Stop (%)")` (True for None **and** float64-NaN), so no ⛔ banner fires on a name the Brief is deliberately silent about. **(F8)** `GAP_TO_STOP_ROUND_DECIMALS` single-sources the breach-boundary precision across build/Brief/Analysis. **(F4)** the breach banner labels the stop naturally per type ("your ATR stop" / "your manual stop" / "your profit-ratchet stop (Protect 25% gain)") — the old `removesuffix(" Stop")` was a no-op on ratchet-tier labels. **(F7)** the manual-override policy is single-sourced in `portfolio.manual_stop_wins` across all three sites (build_portfolio_df, the Analysis merge, and `stop_ladder`). **(F5, accepted)** the sub-0.05% rounding false-positive is intentional (mirrors the Brief so both agree). Opus-reviewed ×8 SHIP. (Commits 5a553e2→859c708 + the 2026-07-07 unifies 2f0b358/4b4f9a6/8bc4ad5 + review-cluster fdcd908/8402390/3016226; reqs F-47/F-47a; memory project_stop_ladder_and_display.) |
| Act Today — hold-vs-reduce reconciliation | The Act lane is `decision_bucket.split_defensive(act_today, review_list)` — two streams. `_consolidate_act_today` only dedupes within `act_today`, so a ticker could show BOTH an act-origin `critical_news` "hold/tighten" card AND a review-origin trim (e.g. weak-large `TRIM_TO_TARGET`) — contradictory. `_reconcile_act()` (run at the merge) drops a ticker's `critical_news` card when it also has any REDUCE card in the Act bucket (stop/sell/risk/deterioration_* or trim variants), folding a one-line note into the reduce card's `why`. `_ticker()` falls back to `action.trim_ticker` (macro PROTECTIVE_TRIM) and is isinstance-guarded (act cards carry a string `action`). News-only tickers and genuinely-distinct compatible cards are preserved. | 2026-06-23: SPCX showed "hold for now" next to "Trim 23%→8%". Producer-side `_act_tickers` exclusion only covers producers that opt in; a single reconciliation at the shared merge closes the class. (Commit f0b5de1.) |
| Holiday-aware market status | `market_status()` consults hardcoded NYSE calendar (`NYSE_HOLIDAYS`, `NYSE_EARLY_CLOSES`, both 2026–2028 ISO dates). Returns "Market Closed (Holiday)" / "Market Closed (Early Close)" and an additive `calendar_stale` key (True when system year > `MARKET_CALENDAR_LAST_YEAR`). Tier-B daily-snapshot write and prior-trading-day baseline loop both use `is_trading_day()` (weekday<5 AND not a holiday) so no holiday-dated snapshot is written and baseline skips holidays not just weekends. Sidebar shows "Holiday calendar is out of date — update NYSE_HOLIDAYS" warning when `calendar_stale=True`. | 2026-06-19 (Juneteenth) was a market holiday but appeared as "Market Open" — the app was holiday-blind. Shared `is_trading_day()` is the single source of truth for "is the market supposed to be open" so all date-skipping logic uses it consistently. Calendar must be extended (with fresh holidays/early-closes) before 2029 or the app silently treats future holidays as trading days again. |
| Signal staleness | Portfolio table shows caption with signal load time (HH:MM). Signals do not update between page refreshes even though live prices update every 60s. | Recomputing all signals on every price tick would hit Yahoo Finance rate limits and degrade performance. |
| Home-page synthesis memoization | The Home page's expensive synthesis block (portfolio risk, alerts, correlation, diversification, fragility, risk advisor, grow-composites, movers, daily briefing, recommendation log write) is memoized behind a session-state signature cache (`_home_synth_cache`; key `"sig"` holds a tuple of holdings ticker/stop, trading day, scanner run version, manual refresh nonce, and auto-refresh nonce). Recomputes ONLY when the signature changes — holdings add/remove/shares, new trading day, fresh scanner run (`_scanner_ver`), a manual Refresh/Lock/Unlock click (`_brief_refresh_nonce`), or the 30-min auto-refresh watchdog firing (`_brief_auto_refresh_nonce` — see the next row). Between refreshes, the Brief AND Risk/Diversification analysis are FROZEN at prices from the last rebuild (not live-tick). The top live-metric row (Portfolio Value, Unrealized P&L, Today's P&L, Best/Worst) is computed OUTSIDE the cache and stays live every rerun. `save_recommendations` fires only on a real rebuild (idempotent). | The Brief is a stable once-per-AM read (§2B, medium-term advisor). Re-running the full synthesis on every tab click / button press burned API quota and kept the app "watching" rather than "advising." The Lock feature already freezes the brief on demand; this makes it frozen by default between intentional refreshes. Coordination caches (`_port_risk_cache`, `_grow_today_sectors_cache`, etc.) are re-published on cache hit so cross-page gates still fire. |
| Today's Brief auto-refresh timer (F-177) | The "Built at" freshness chip promised "auto-refreshes in N min" but nothing actually triggered a rebuild — Streamlit only reruns on user interaction, and the memoization signature above had no time component, so the Brief sat stale indefinitely until a manual click. A `@st.fragment(run_every=60)` watchdog (same pattern as the live-price-strip fragment, `app.py:2572`) now ticks every 60s and, once the Brief is past `BRIEF_AUTO_REFRESH_MINUTES` (30) AND the market is in premarket or regular trading hours (`is_premarket()` / `market_status()["is_open"]`) AND "Today's Setup" isn't Locked AND the shared `_refresh_gate("data")` cooldown isn't armed, bumps `_brief_auto_refresh_nonce` and calls `st.rerun(scope="app")` to force the full page (not just the fragment) to rebuild. It never calls `st.cache_data.clear()` or `scan_sectors()` — those stay manual/cron-only — so the auto-tick's incremental provider-call cost is bounded to whatever the pre-existing 30-minute `@st.cache_data(ttl=1800)` provider cache (`load_all`, `_cached_spy`, `_cached_vix`, etc.) would refetch anyway once its TTL naturally expires; it just makes that recompute actually happen on schedule instead of waiting forever for a click. `st.cache_data` is process-wide (not per-session), so multiple open browser tabs each ticking independently still only pay for one real fetch per ticker per TTL window. | User-reported: the chip read "stale · click Refresh Signals" after sitting untouched for 2+ hours with no click ever having fired. The label was decorative. Cost containment was the binding constraint on the fix — the full "Refresh Signals" path is expensive specifically because it clears the whole cache and re-scans the ~200-ticker discovery universe, which would blow through FMP's 220-250/day quota if auto-fired every 30 min across a trading day; re-running the existing memoized synthesis WITHOUT those two calls costs nothing beyond what the TTL would already spend. (Commit 6aff705.) |
| Pre-Earnings save → watchlist auto-add (F-174) | Saving a CNBC pre-earnings preview (Ideas Inbox → 📅 Pre-Earnings → "💾 Save earnings preview") now also adds every saved ticker to the watchlist (`st.session_state.watchlist` + `db.save_watchlist`, skipping tickers already present), mirroring F-154's Stock Research mode, which already treats Ideas Inbox saves as watchlist candidates. | A ticker can score a Buy on the Analysis page and pass every gate, yet never surface under Grow Today, because `_grow_today`'s candidate pool is sourced entirely from `scanner_results` (`scan_sectors()` over the curated `SECTOR_UNIVERSE` + watchlist `extra_tickers`) — a name absent from both is never scanned, so it can never reach the composite gate regardless of score. Saving `earnings_context` alone (the Phase 1 CNBC enrichment) never touched the watchlist, so a user who pasted a pre-earnings article believing "this is now on my radar" found the ticker enriched on Catalyst Watch but structurally invisible to Grow Today (the SPOT case). (Commit 218296c.) |
| Predictive Analytics — negative alpha in a bull market (F-178) | All composite-score bands on the Signal Calibration tab may show negative alpha vs SPY simultaneously, and the "personal threshold" callout reads "not yet determinable." This is expected in a persistent bull-market regime: alpha = outcome% − SPY%, so a rising SPY sets a high bar even when individual picks are profitable. The synthesis directive explains this explicitly ("watch for the first band to flip green as conditions shift") rather than hiding it. Bands with n < `PREDICTIVE_MIN_BAND_N` (5) are rendered in grey and excluded from the threshold computation — insufficient data is stated, not hidden. `synthesize_directives` always emits a context directive with the graded-outcome count so confidence is explicit. | The page follows the "decides, not informs" posture (§2A): charts alone left the user asking "so what?" The rule-based synthesis layer closes that gap without LLM dependency — directives are generated from model outputs at render time, so they stay current as data grows. Negative-alpha-in-bull-tape is regime information (not a bug), and surfacing it honestly with a "watch for" directive is more useful than suppressing it. |
| Pre-market previous close | `fetch_premarket_movers()` prefers the known close from `held_data` history for the baseline. When `held_data` is empty (cached call), it falls back to `fast_info.previous_close`. | The cached pre-market fetch cannot accept non-hashable `held_data` as a parameter, so it uses fast_info as fallback. |
| Pre-market mover cross-check | `fetch_premarket_movers()` keeps `yf.Ticker(sym).fast_info` as the primary read for every mover (deliberately NOT routed through the multi-source orchestrator like the rest of the app — the orchestrator's yfinance leg reads `yf.download(period="2d")` daily bars, which returns yesterday's completed close pre-open, not a live premarket tick). Instead, each mover clearing the |chg|≥0.5% filter is passed through `data.crosscheck_against("finnhub", ticker, price, prev)` — naming Finnhub explicitly rather than the generic `data.crosscheck_price()`, which auto-picks "whichever provider isn't the configured live-price chain's primary"; since Finnhub **is** that primary (`DATA_LIVE_PRICE_ORDER`), the generic call would skip Finnhub and validate Yahoo fast_info against Yahoo's own daily-bar path — a same-vendor near-no-op caught in the Opus review of this fix. The mover carries `xcheck_ok` (True/False/None) but is never dropped on disagreement. `premarket_stance.format_user_prompt()` appends "(⚠ unverified)" for `xcheck_ok is False` and the system prompt instructs the model not to assert an unverified mover's direction as fact; Today's Brief's Pre-Market Intel panel shows a per-row "⚠ unverified" badge + a summary caption. **Caveat (unverified against a live key):** whether Finnhub's free-tier `/quote` reflects real pre/post-market ticks vs. yesterday's regular close during those windows hasn't been confirmed live in this environment — if it doesn't, the live-price leg degrades from a directional check to a magnitude-only flag (a correct big mover could still tag "unverified"), which is safe (only adds caution, never drops/reverses a mover) but is a known open question, not a settled fact. The prev_close leg is unaffected either way (both sides read the same settled prior session regardless of Finnhub's premarket coverage). | 2026-07-30 incident: the Pre-Market Stance narrative asserted "MSFT -8.11% / META +7.95%" — the opposite of the real overnight move (MSFT +9% / META -9% on earnings, per CNBC) — because `fast_info` served a stale/wrong premarket print with nothing to catch it. Swapping the primary source risked going premarket-blind entirely (worse than the bug); cross-checking without replacing the source catches the divergence and stops the AI narrative from asserting a wrong number as settled fact, while preserving premarket coverage. |
| RSI in strong uptrends | When avg_loss EWM = 0 (no losing periods in window), RSI is set to 100.0 (if any gains) or 50.0 (flat). | Standard division by zero would produce NaN, which downstream signal logic treats as neutral — incorrectly suppressing strong Buy signals. |
| Sortino in strong uptrends | When no negative excess-return days exist, Sortino returns 99.0 (not 0.0). | An empty downside series has std = NaN; treating that as 0.0 was showing worst-case Sortino for the best-performing stocks. |
| Fractional shares | `db.load_holdings()` converts the `shares` column to `float` (not `int`). | Brokers increasingly support fractional shares; `astype(int)` was silently truncating e.g. 12.5 → 12. |
| Two coexisting sector taxonomies (F-223) | The app now has TWO independent sector classification systems that will never sum the same way for the same portfolio: (1) `TICKER_SECTORS` — 13 curated **thematic** labels (Consumer Tech, AI & Cloud, etc.), used by Sector Exposure (F-07), Sector Gaps (F-222), and the Diversification Advisor gates; (2) the real provider-reported sector (Technology, Financials, Health Care, etc., normalized via `_normalize_provider_sector`/`_PROVIDER_SECTOR_ALIASES`), used ONLY by the "Portfolio vs. S&P 500" benchmark-tilt view (F-223, 📈 Analytics tab) via `real_sector_exposure()`/`sector_benchmark_tilt()`. Both live in `stock_analyzer/portfolio.py`. | Real GICS sectors don't map 1:1 onto the app's thematic labels (e.g. "Consumer Tech" spans 3 different real sectors), so a genuine "vs. the actual S&P 500" comparison needed the real per-ticker sector, not a lossy remap of the thematic buckets. The real sector was already being fetched and silently discarded (`resolve_sector()` prefers the thematic label) — reused for free rather than adding a new fetch. The F-223 UI explicitly captions this so it reads as a deliberate design choice, not a bug, the next time someone notices the two sector breakdowns don't match. `SP500_SECTOR_WEIGHTS` is a static reference table (Wikipedia "S&P 500" GICS weighting, as of 2026-07-01) needing periodic manual refresh — see CLAUDE.md's "What's queued." |
| Earnings + conflict verdict | The earnings priority check runs before composite/sentiment checks. A near-earnings stock with any other conflicting signal escalates to "Conflicted" (red), not just "Caution" (amber). | Holding through earnings with mixed signals is higher risk than either condition alone. |
| Entry zone (Grow Today) | **Superseded by F-249 (2026-08-23).** Previously `_suggest_size()` derived `entry_lo`/`entry_hi` from a heuristic (40% of stop-distance below / 15% above price). That function is **deleted**; Grow Today now takes `entry_lo`/`entry_hi` straight from the bundle's ATR-derived `entry_zone(price, atr_val)` (`bundle_loader`), the same zone Analysis shows. | A single "@ ~$X" price point implied precision that doesn't exist; a zone is more honest and practical. The heuristic version additionally disagreed with the ATR zone on the same ticker across two pages. |
| Position sizing single-name cap | `risk.position_sizing(...)` takes an optional `max_position_pct`; when passed (`SINGLE_NAME_CEILING`) it caps the suggested shares so the dollar position can't exceed the ceiling, and returns `ceiling_capped` + `uncapped_shares`/`uncapped_pct`. **As of F-249 (2026-08-23) this is the ONLY sizing engine** — Grow Today new-pick and add-to-winner cards and the three cron emails were migrated off a second, uncapped `daily_briefing._suggest_size()` (now deleted) that suggested a price-independent 30.0%/21.4%/18.75% of book. Every surface that shows a share count discloses the cap when it binds. | Pure risk-budget sizing balloons the dollar position when the stop is tight (observed: GD 3.4% stop → 26 sh = 42.9% of book), suggesting a concentration the rest of the app hard-blocks. The sizer must respect the same ceiling. **Known gap (unchanged):** for a held add it caps at a flat ceiling, not (ceiling − current weight) — strict improvement over uncapped, add-aware refinement is a follow-up. |
| Sizing suppression has TWO causes, and they must never be conflated (F-249, 2026-08-23) | `risk.sizing_unavailable_reason(portfolio_value, entry, stop, max_position_pct)` is the **single** predicate for both no-size conditions, returning `"stop"` (degenerate stop — a data problem the user can inspect), `"ceiling"` (the cap can't afford one whole share — an account-size constraint no stop change fixes), or `None`. `position_sizing` delegates **both** of its guards to it, the Grow Today adapter reads it instead of re-deriving the ceiling test, and the Analysis/Watchlist fallback captions branch on it. Degenerate stop takes precedence, so a user is never told their account is too small while their stop is also broken. | Found by Opus review as blocking. `position_sizing` had gained a second `None` cause while the Analysis and Watchlist captions still said *"stop price too close to entry or not set"* — so a high-priced name on a modest book blamed the stop while a healthy 2×ATR stop rendered directly above it, on the surface whose whole job is explaining the suppression. One gate, one implementation: a second copy of either predicate drifts the moment `position_sizing`'s guards change. |
| Grow Today new-pick stop AND entry zone are derived against the LIVE price (F-249, 2026-08-23) | The new-pick sizing stop is `price − ATR_STOP_MULT × _comp_data["atr"]`, where `price` is the scanner row's quote — **not** `_comp_data["stop"]`, which sits ATR_STOP_MULT×ATR below the *bundle's* last close. `entry_lo`/`entry_hi` are likewise re-derived via `targets.entry_zone(price, atr)` on that same live basis rather than taken from the bundle, so the zone always brackets the price the size was computed from. Falls back on the **result** (a non-positive derived stop → the bundle stop), not on the input, so a name whose ATR is ≥ half its price cannot slip through the missing-input guard and render silently. The **add-winner lane deliberately stays on the bundle basis** — safe because that branch is gated on `gap >= ADD_WINNER_MIN_GAP_PCT` measured from the *ratcheted* stop, so `price > stop` holds by construction; commented at the call site. Keeps `risk_per_share` exactly `ATR_STOP_MULT × ATR` and `stop_pct` honest. | Found by Opus review as blocking. For movers the two price bases diverge intraday. Above the stale stop the mismatch only over-states `risk_per_share` and under-sizes (safe); **below** it `risk_per_share` went negative, `position_sizing` returned `None`, and the card rendered a **BUY with no size and no caption** — a silent filter on the primary buy surface. Impossible before, because the deleted `_suggest_size` derived its stop from the same `price`. A `stop_infeasible` marker now also covers the residual case so it can never render blank. **The entry-zone half was a regression introduced by this same change and caught in round-2 review:** leaving the zone on the bundle basis while the size moved to the live price meant a mover's zone could sit entirely below the current quote, and the cron emails' "only act if price is still inside $X–$Y" guard would then contradict their own GO/BUY headline on one card. The deleted `_suggest_size` derived the zone from the price it sized against, so the property existed before and had to be restored. |
| Position sizing — ceiling-infeasible ⇒ no size (F-249, 2026-08-23) | When the ceiling cannot afford **one whole share** (`price > portfolio_value × SINGLE_NAME_CEILING%`), `position_sizing()` returns **`None`** rather than forcing a share. All four callers already treat a falsy result as "no sizing block", so the suppression reaches Analysis and Watchlist too. Grow Today's adapter returns a marker `{ceiling_infeasible, one_share_pct}` carrying **no `shares` key** — every renderer gates its size text on `shares`, so a marker can never print "0 shares" — and the card/email state why no size is shown. | The prior `ceiling_shares = max(1, int(...))` floor emitted one share anyway **and left `ceiling_capped` False**, because `risk_based_shares` was also 1 so the `shares > ceiling_shares` comparison never fired. A $4,500 name against a $10,000 book rendered **1 share = 45% of portfolio with no disclosure at all** — a silent breach of the ceiling the rest of the app hard-blocks. Reachable via `scan_movers` (names outside the curated universe) and any ticker typed into Analysis. Fail closed: there is no honest size at this cap, so suggest none and say why. |
| Reach line (Grow Today) | `_render_grow_today` shows a "🔭 Screened N tracked [+ N watchlist] [+ N discovery] names → N reached full composite scoring" caption (after any scan has run; suppressed before first Refresh and on bear days). The discovery term appears only when the movers pass ran this session (`_movers_candidates` present), even if it surfaced nothing. Read-only — reflects what was screened, changes no gate. | The brief draws from `SECTOR_UNIVERSE` (~70) + watchlist + the ~200-name `DISCOVERY_UNIVERSE` (movers), but the UI previously only showed the 12 composite finalists, so the engine *looked* blind to anything beyond the curated list. Surfacing the funnel makes the reach verifiable. Discovery-sourced picks that clear also carry the existing "🔥 +X% today" mover badge (provenance tooltip). |
| Position Monitor re-check | When signal is Hold for a held position, the info box shows a specific 7-day re-check date computed from `date.today() + 7`. Two triggers are given: add-on if score ≥ `COMPOSITE_BUY`; exit if price closes below stop. | "Mixed signals — check back later" gives no actionable timeline. Specific dates and conditions prevent analysis paralysis. |
| Rankings sort order | `ranking.py` sorts by Composite Score descending, Universe Rank as tiebreaker. | Sorting by Universe Rank ascending promoted lower-scoring stocks that happened to have a low ordinal rank. |
| Beta recommendation | `risk_advisor.py` names the specific highest-beta ticker and computes the exact new portfolio beta using `(beta - w*b*f) / (1 - w*f)` where f = 50% sell fraction. Explicit `if/else` guards against `w*f → 1` (Phase 1 H2). | A generic "consider trimming high-beta names" gives no concrete action. Users need to know which ticker and what the outcome will be. |
| Stop data integrity | `portfolio.py` returns `Stop=None`, `Stop Type="Stop Unavailable"`, `Gap to Stop=None` when the upstream stop is missing or zero. Downstream consumers (Act Today SELL trigger, earnings advisor, alert builder, drill-down metrics, dataframe styler) all guard for None and surface "—" or a "stop unavailable" caption instead of fabricating a fallback. | Phase 1 C2. Silently substituting a fabricated 8% buffer let mechanical SELL rules fire on a number nobody chose. Fail loudly. |
| Earnings risk for new picks | `_cross_reference` reads earnings from a UNION of `held_data + grow_composites` via `earnings_lookup`. Both held positions and new scanner picks are screened. | Phase 1 C1. Previously the earnings check ran only for held tickers, so a brand-new pick with earnings tomorrow could be marked "Confirmed." |
| Composite gate | Grow Today new picks AND add-to-winner both require composite ≥ `COMPOSITE_BUY` (65). When composite pre-fetch failed for any of the top picks, an amber "Composite Scores Unavailable" banner is rendered above Grow Today so the user knows the gate didn't run for those tickers. | Phase 1 H3 + Phase 2. Asymmetric bars (65 new vs 68 add) were backwards from "press your winners." Silent gate bypass on fetch failure was a real risk. |
| ENTER_NOW R:R requirement | Watchlist `ENTER_NOW` requires `rr is not None and rr >= RR_ENTRY_MIN` (2.0). Tickers without a validated R:R fall through to `NEAR_ENTRY`. | Phase 1 H4. "Unknown R:R" is incomplete homework, not a green light. |
| Watchlist display order (F-219) | The 📋 Watchlist page's default card order is `watchlist_advisor.sort_key_for_action()`: `ENTER_NOW → NEAR_ENTRY → REMOVE → HOLD_OFF_EARNINGS → WAIT_ENTRY → WAIT_CATALYST`. This is a page-level presentation ranking, separate from `_ACTION_PRIORITY` (`REMOVE`=HIGH, `HOLD_OFF_EARNINGS`=MEDIUM, `ENTER_NOW`=OK) which only labels priority severity for the inner detail banner color, not display order. Does not touch F-203 (Watchlist Resurrection) — that feature's stated invariant is that it never itself reorders cards; this ranking is independent, page-level logic in `app.py`, not in `build_watchlist_recommendation()`. | 2026-07-27 restructure (F-219). The prior order ranked `REMOVE`/`HOLD_OFF_EARNINGS` first, which buried the page's actionable `ENTER_NOW`/`NEAR_ENTRY` names under a wall of earnings-hold cards on watchlists with many names on hold. |
| R:R entry caveat (Analysis) | Composite scores the STOCK (quality); R:R scores the ENTRY (asymmetry at this price) — independent, so a Strong Buy can have poor R:R (target near, stop far). When the Analysis verdict is Buy/Strong Buy but R:R < `RR_ENTRY_MIN`, the Trade Plan shows an amber "strong stock, weak entry here" caveat (per-share risk/reward + suggested pullback level + "KEEP, not an add"). The verdict is NOT downgraded — Analysis is a research surface that informs and defers to the user; the hard R:R block lives on Watchlist ENTER_NOW (G-13). | ESTC: Strong Buy 75.5 but R:R 0.7:1 (risking $8.40/sh to make $5.59/sh). Conflating quality with entry timing would lose the quality signal; suppressing nothing would mislead a "decides" app. (Commit 0edfc4c.) |
| Catalyst Watch (awareness, not a gate) | Its OWN nav page (🔔 Catalyst Watch, before Economic Calendar — the single home for earnings; the Home "Earnings" tab was removed and absorbed here). TWO tiers: (1) **Your Holdings — Earnings** = full per-position detail + Pre-Earnings Playbook via `_render_holdings_earnings(port_df, held_data)` (the extracted Home-tab render), fed from the canonical `_last_port_df`/`_last_held_data` the Home brief stashes; (2) **On Your Radar** = `build_catalyst_watch` (pure) for NON-held tracked names within `CATALYST_WATCH_WINDOW_DAYS`, sector + 🔥 + chip. Radar calendar: FMP market-wide (`fetch_earnings_calendar`, one call) THEN per-name yfinance fallback (`fetch_next_earnings`, threaded) so coverage doesn't depend on FMP's tier. Both tiers carry a ticker→Analysis "Analyze" control. 24h-cached. | The app intentionally won't RECOMMEND buying into earnings (binary risk) but was BLIND to a tracked name reporting (PANW). FMP's free calendar returned held-only → yfinance fallback added. Then consolidated the rich Home Earnings tab in (Home was getting heavy) so all earnings — deep for holdings, broad for the rest — live in one place. (Commits dd8aea5 / eead6b0 / 85bd14c.) |
| Confirmed verdict guard | `_cross_reference` will NOT issue "Confirmed — All Signals Aligned" for a held position whose composite Signal is empty. `composite_available` becomes False; verdict routes to "🔍 Verify — Composite Signal Missing" (amber). | Phase 1 H5. Previously an empty signal silently fell through to the agreed list, producing a green light on missing data. |
| Single-name ceiling | Grow Today and Buy Candidates suppress add-to-winner when the position is at or above `SINGLE_NAME_CEILING` (15%). A concentration banner explains the suppression. | Phase 2. Institutional standard. Concentration risk overrides signal strength. |
| Concentration / position-sizing discipline | Closes the enforcement asymmetry where concentration ceilings gated the app's *recommendations* but NOT the user's *manual* Trade Journal entries (how SPCX grew to ~23% of book). Pure logic in `stock_analyzer/concentration.py` (`assess_add_concentration`, `high_beta_share` — no Streamlit/IO/pandas; unit-tested). **Part 1 (entry-time nudge):** the Log-a-Trade BUY branch calls `assess_add_concentration` (resulting single-name + sector weight post-fill + exact trim-back shares to `SINGLE_NAME_CEILING`) and renders a non-blocking `st.warning`. NEVER blocks (the journal records already-executed trades) and is fully exception-safe. **Part 2a (recommendation):** `risk_advisor` appends a MEDIUM `single_name_concentration` rec when `weight >= SINGLE_NAME_CEILING (15) AND score >= WEAK_CONVICTION_SCORE (55)` — overweight + STRONG conviction, the exact gap the existing weak-large rec (`score < 55`) misses; the `>= 55` / `< 55` boundary is a clean split so no name double-fires. Added to `_TUNEUP_RISK_TYPES` so it reaches the Portfolio Tune-up lane and (being MEDIUM) never reaches Act Today. **Part 2b (standing line):** under the fragility gauge, Home renders "🔗 N% of measured exposure is in high-beta (β ≥ `PORTFOLIO_BETA_ELEVATED`=1.3) names". `high_beta_share` computes the share over KNOWN-beta names only (unknown-beta excluded from num+denom), published to `st.session_state["_highbeta_share"]` (set to `None` on failure; display-only). Warn color keyed off `CONCENTRATION_HIGHBETA_SHARE_WARN` (60.0 — display threshold, not a decision gate). | Closes the entry/manual enforcement asymmetry: manually-journaled buys bypassed the recommendation-side ceilings, so SPCX reached ~23% with no friction. Risk is managed more at ENTRY (sizing) than reactively. Part 2a flags high-conviction overweights the conviction-gated weak-large rec ignores (the 15% cap is a SIZE limit, not a conviction call). Part 2b surfaces correlated-cluster risk that per-name diversification hides. Opus review caught a NameError-class blocker (`_f` used in app.py but defined only in the stock_analyzer modules — invisible to py_compile, would have shipped the surfaces silently dead); fixed with a module-level `_f`. (Commit ebdf255.) |
| Sector hard-cap gate + classification fallback | `build_portfolio_df` classifies a ticker's sector as the curated bucket → else its yfinance `.info` sector → else `"Other"` (no longer dumping every unmapped name into "Other"). `_grow_today` computes sectors ≥ `SECTOR_CEILING` (35%) and suppresses BOTH new picks (`sector_blocked_picks`) and add-to-winners (`sector_blocked_adds`) in them, with a "Suppressed — Sector Hard Cap" banner. `risk_advisor` reads the same `port_df["Sector"]`, so the Act Today breach card and this gate stay consistent. | ESTC (Tech/Software) fell into "Other", which ballooned to 44% — a spurious breach — and surfaced as a Strong-Buy add WHILE Act Today flagged the sector for a trim. Classification makes the breach real; the gate makes deploy-capital defer to protect-capital. A Strong Buy in an over-cap sector is a KEEP, not an add. (Commit a28e89d.) |
| Concentration-gate basis: EQUITY (current, 2026-07-09 — reverses the 2026-06-26 net-capital basis) | The single-name (15%) and sector (35%) hard gates, the `risk_advisor` `single_name_concentration` / `sector_concentration` trim recs, the Trade-Journal entry nudge, and the watchlist / quick-research / comparison entry-fit checks all gate on **plain equity weight** = MV ÷ invested equity. app.py sets it at ONE point (~2710): `Gate Weight (%)` := equity `Weight (%)`, `_acct_gate_cache.basis` := `"equity"`, and `gate_denom` (→ `risk_advisor`) := equity so `_acct_f` = 1. `daily_briefing` reads `_gate_wt`/`_gate_wt_col` (now == equity weight). `concentration.gating_denominator` is retired from the gate path (the fn remains, unused by gates). All the margin-framing UI (suppression-banner "⚖️ Tightened by margin", the Analysis "% of net capital (gated)" overlay, the Composition-Sankey net-capital flags, the entry-nudge note) is conditioned on `_acct_gate_cache.basis ∈ {account, over-levered}`, so it uniformly turns OFF under the equity dial — no false net-capital claims render. Leverage/margin risk moves to an **awareness-only** signal (`_leverage_cache` → 🔗 Risk Analysis leverage read + 💰 Account note; F-09d), never a gate. | The 2026-06-26 net-capital "tighter-of-both" basis made a **transient** margin balance lurch a hard gate — a sector read 166.9% and the app demanded ~85% liquidation off a temporary financing state. That fights §2B calm/anti-churn and conflates position-sizing (structural, judged on equity) with leverage (a distinct risk, better monitored than gated). Equity-basis is stable across transient margin and still fires on a genuinely over-concentrated book; leverage risk stays visible as advice. User decision 2026-07-09 (reverses the 2026-06-26 call). Do NOT re-base gates to net-capital without an explicit policy discussion. |
| Tax HARVEST subordination | `tax_advisor.py` returns `HOLD_FOR_SIGNAL` (not `HARVEST`) when the position is rated Buy or Strong Buy. The UI renders a "Harvest Suppressed — Investment View Holds" banner with the conflicting positions. | Phase 2. Tax tail does not wag investment dog. Exiting a Buy-rated position to capture a tax loss trades known savings for unknown opportunity cost. |
| Diversification ADD candidates: universe-sourced + cross-validated | The Diversification Advisor decides "which sector is underweight," not "is this name a good entry." (1) **Candidate sourcing** — `portfolio.diversifying_candidate_pool` draws the pool from the broad discovery universe bucket (≈20 names) unioned roster-FIRST (so curated names are never dropped by the cap), capped at `DIVERSIFY_SCAN_CAP` (10); a sector with no discovery bucket falls back to its roster. So a better entry the old 4-name roster omitted (AXP, SPGI, MS…) can now surface. (2) **Quality cross-validation** — the Alerts & Actions ADD card joins each candidate to the SAME composite/signal/R:R the Analysis page produces (`annotate_add_candidates`, fed from cached `_grow_composites` bundles; load_all fallback for un-scored names, R:R via `risk_reward(price, stop, targets["base"])`), gates against `COMPOSITE_BUY` (65), ranks best-first, shows the top `DIVERSIFY_DISPLAY_TOP` (3) with a "scanned N · showing best M" caption, and renders a 🎯 banner naming the best gate-passer (with R:R) or a 🚦 "tilt sound but no name clears the gate" banner. Failing names stay visible-but-demoted ("Below Buy gate") — never silently filtered. Each candidate carries an "▶ Analyze {ticker}" button (sets `_pending_page`/`_analysis_ticker`, same control as the New-Position cards) — the bridge from "good candidate" to the full Analysis scorecard and trade-from-there; the preselect handles untracked names (C/BAC/WFC) via the name_to_ticker merge. | Visa surfaced as a rebalance ADD-to-Financials candidate but never as a new-position pick; the card hid that V scores Buy 65.7 with a 5.9:1 R:R, AND a fixed 4-name roster froze the opportunity set while the market moves. Universe sourcing + quality cross-validation makes the sector-tilt engine and the quality engine give one read, over a live-relevant candidate set (still a curated universe — fully-dynamic any-ticker selection needs a screener API, Phase 5). |
| Persistent last-known-good fundamentals | `load_all` write-throughs good fundamentals to Supabase `fundamentals_cache` (per-ticker jsonb + `fetched_at`); on a live double-miss (sparse yfinance `.info` AND no FMP backfill) it serves that copy when within `FUNDAMENTALS_CACHE_MAX_AGE_DAYS` (7), exposing `fund_source="cache"` + `fund_cache_age_days`. The verdict then renders with an amber "📦 as of N days ago" note instead of withholding; withhold only fires when there's no fresh cache. Decision logic is pure (`fundamentals.resolve_fundamentals` / `count_core_metrics`); I/O is `db.load/save_fundamentals_cache` (degrade to no-ops if the table is absent → live-only, fully back-compatible). | yfinance `.info` is the fragile leg and FMP backfill is quota-capped (250/day); a transient simultaneous miss blacked out a verdict the app had perfectly good fundamentals for minutes earlier. Stale-but-real (bounded + stamped) beats a blackout, and is NOT fabrication (the neutral-50 problem) — it's aged real data with a hard freshness limit. Also cuts FMP quota pressure. (V withheld 2026-06-03.) |
| Act vs Awareness split + "you're done" (calm advisor 2B) | The Brief's defensive (right) column is split by URGENCY, not origin: `decision_bucket.split_defensive(act_today, review_list)` → `{act, aware}` (each item shallow-copied with `_source` so it renders with its origin card template). **🔴 Act Today (decisions only)** = stop_breach / sell_signal / risk / critical_news + TRIM_AND_TIGHTEN / TRIM_TO_TARGET / PROTECTIVE_TRIM. **👁️ Monitoring / Awareness (FYI)** = macro / WATCH / TIGHTEN_ONLY + anything unrecognised (fail-to-calm). Borderlines are flag-governed: `BUCKET_CRITICAL_NEWS_IS_ACT=True`, `BUCKET_TIGHTEN_ONLY_IS_ACT=False` (user choices). Empty Act bucket → a green "✅ Nothing to act on — you're set for today · monitoring N items" banner (derived; no new persistence). Card bodies (incl. Analyze + Mark-Done) are unchanged closures dispatched by `_source`; each item lands in exactly one bucket so button keys can't collide. | A stream of individually-correct prompts, all in one "Act Today" list, created false urgency / screen-watching (counter to §2B). Isolating genuine same-day decisions from FYI — and saying "you're set" when there's nothing to do — is the core calm-advisor change. |
| Read-only viewer mode | Owner-whitelist, fail-safe: `st.secrets["owner_emails"]` get full access; any other authenticated viewer (private Streamlit deployment) is read-only. Defense-in-depth: (1) hard backstop — `db.set_readonly()` sets a module flag; the 8 user/owner-data write functions in `db.py` no-op when set (`save_fundamentals_cache` stays writable — system cache). (2) UX — 9 mutating controls get `disabled=st.session_state["_readonly"]` + a "👁️ Read-only viewer" banner. The auto `save_recommendations` on Home load is additionally gated by the per-session `st.session_state["_readonly"]` (race-immune; the db process-global alone is concurrency-prone across sessions). Identity via `st.user`/`st.experimental_user`; fails OPEN to full access only when unconfigured or email-undeterminable (bounded by the private allowlist). | Owner wants to give someone read-only access to the *real* portfolio to learn the app. Streamlit's viewer allowlist controls who can OPEN the app, not what they can do inside it — so read-only must be enforced in-app. db.py is the sole write chokepoint (audited), so gating it there is complete; UI disabling is teachable UX on top. |
| Centralized sector resolution (`resolve_sector`) | `portfolio.resolve_sector(ticker, fallback)` is the single source of truth for a ticker's sector bucket: curated `TICKER_SECTORS` first → provider/scanner fallback → `UNCLASSIFIED_SECTOR`. Used by both `build_portfolio_df` (held) and `_grow_today`'s pick loop, so a mapped name (e.g. SNAP→Consumer Tech) classifies identically as a holding AND as a pick — previously the pick path took the raw scanner sector and showed mapped names as "Other." Fallback is None-safe (a `None`/blank provider sector falls through to "Other", never the literal "None"). `TICKER_SECTORS` expanded with unambiguous large-caps (Financials: BX/BAC/WFC/C/MS/SCHW/BLK; Healthcare: UNH/JNJ/PFE/MRK/TMO/ABT/AMGN/BMY/MDT/DHR). | Two sector maps disagreed (portfolio's `TICKER_SECTORS` vs the scanner's own), so SNAP showed "Other" as a pick despite being mapped — feeding the phantom "Other ≥ cap" suppression. Centralizing also sharpens real concentration detection (fewer holdings hidden in the catch-all). |
| "Other" excluded from sector concentration gate | `risk_advisor.build_risk_advisor_recommendations` computes the top-sector breach over `real_sector_weights` (all sectors except `UNCLASSIFIED_SECTOR`), and excludes "Other" from the redeploy-target list. **`_grow_today`'s own sector-cap gate (`_breached_sectors`) likewise excludes `UNCLASSIFIED_SECTOR`** so new-pick AND add-to-winner suppression never fire a phantom "Other ≥ 35% hard cap" on the catch-all bucket (the SNAP/BX/UNH/DHR case). When `Other ≥ SECTOR_ELEVATED`, it instead emits a LOW-priority `unclassified_holdings` data-hygiene note (names the tickers; never reaches Act Today — only HIGH risk recs do). `TICKER_SECTORS` was also expanded (ESTC/GTLB→AI & Data; PINS/SPOT/DASH/DIS/SNAP→Consumer Tech) to shrink the bucket — originally also CFLT, removed 2026-08-16 when the liveness sweep confirmed it delisted. | ESTC (a software name absent from the curated map) + other unmapped holdings piled into "Other", inflating it to 44.4% and tripping a HIGH "Hard Cap Breach — trim Other / redeploy" Act card. "Other" is a grab-bag, not a correlated sector — the trim/redeploy advice was incoherent. Classification artifacts must not drive trade decisions. |
| Macro pre-event trim defers to existing Act decisions + share/$ reconciliation | The macro `PROTECTIVE_TRIM` (`_review_list`, HIGH-impact event 1–3 days out, affected-sector exposure > `MACRO_AFFECTED_TRIM_THRESHOLD_PCT`) now picks the weakest affected holding NOT already in `act_today` (`_act_tickers`). If every affected holding already carries its own Act decision, the event downgrades to WATCH instead of a contradictory trim. The displayed trim $ and pp are recomputed from the ROUNDED whole-share count (`$ = shares × price`) so "trim N shares (~$X)" is internally consistent. | AVGO surfaced TWICE in Act Today: a critical-news "hold & tighten" card AND an NFP "trim AVGO" card — opposing asks on one name (the double-surface §2B kills). The news engine owns a name with the more specific same-day signal; the macro trim defers. Also fixed "trim 1 share (~$571)" false precision (dollar target vs rounded shares). Mirrors the `_buy_candidates`/`_grow_today` Act-dedup pattern. **Follow-up:** a final-pass in `_review_list` drops a negative-news WATCH (`watch_kind=="news"`) for any ticker actioned anywhere — including a macro `PROTECTIVE_TRIM` whose target is in `action.trim_ticker` (item `ticker` is None) and runs after the news block. Fixes MSFT showing as an NFP trim (Act) AND a news WATCH (Awareness) once the AVGO fix redirected the trim onto MSFT. Earnings/scheduled WATCHes (distinct catalysts) are preserved. **Broad-event downgrade:** when affected-sector exposure ≥ `MACRO_BROAD_EXPOSURE_PCT` (60%), the event is portfolio-wide (NFP/CPI/Fed hit ~everything) — a bounded single-name trim is immaterial and reads as pre-event churn, so it downgrades to an awareness WATCH ("hold through, mind your stops"). The sized `PROTECTIVE_TRIM` fires only for sector-concentrated events (30% < exposure < 60%) where culling one name actually cuts the exposure. §2B calm posture. |
| Portfolio Tune-up lane (risk-metric trims out of Act Today) | Slow-moving Risk-Advisor rec types (`_TUNEUP_RISK_TYPES` = sharpe / beta / volatility / drawdown / tail_risk) are NOT promoted to Act Today by `_act_today` — they're built into a separate `portfolio_tuneup` list (`_portfolio_tuneup`, HIGH+MEDIUM) and rendered in the Brief's right column under "🔧 Portfolio Tune-up · standing quality — not time-sensitive", below Act/Awareness. `sector_concentration` (a structural breach) STAYS in Act. | A Sharpe/beta/vol drag is a 6-month statistical metric, not a same-day decision — it would sit unchanged in "Act Today (decisions only)" for weeks (the lone PINS Sharpe item). §2B: Act Today must mean act *today*; standing quality improvements belong in their own lane you address when rebalancing, not on the clock. |
| Fragility gauge | A standing one-line banner on Today's Brief (a promoted full-width section on Home, not a tab) rendered directly under the market-tone banner. Answers: "If a routine −10% market pullback hits, how far does MY book fall?" Pre-emptive *exposure* read, explicitly NOT a forecast. Shows implied book move (e.g. "≈ −26%"), an "~N× the market's move" multiplier (derived from `implied_move ÷ pullback`, so the two numbers tie out), and top-2 most-exposed positions. Severity is calm / caution / fragile, keyed off regression portfolio beta against `PORTFOLIO_BETA_ELEVATED` (1.3) / `_CEILING` (1.4) bands. Pure `stock_analyzer/stress_test.assess_fragility(...)` reusing the `mild_correction` scenario via `run_scenario(..., custom_spy_move=FRAGILITY_PULLBACK_PCT)` and cached portfolio beta from `_port_risk_cache`. Published to `st.session_state["_fragility_cache"]` (set to `None` on failure per coordination pattern §4.0.2, not an empty dict). Withholds *visibly* (muted "exposure read unavailable" note) when beta can't be computed but holdings exist; renders nothing when there are no holdings. | Investors conflate "is my portfolio volatile today" with "will I survive a correction I can't predict." The gauge uncouples the two: beta isolation lets a user see "this is my book's sensitivity to systematic moves" as standing context, independent of today's tone, letting them build conviction whether their sizing is right for market conditions. (Pullback-awareness Phase 1; Phase 2 = reactive email alert, Phase 3 = market-risk dial — both queued.) |
| Health Score — Diversification sub-score uses equity-range rescaling | The Diversification sub-score in `portfolio_health.py` computes `(1 − avg_corr) × 100` directly, not the `(1 − avg_corr) / 2 × 100` formula used by `diversification_score()` in `portfolio.py`. The ÷2 formula is calibrated for the full −1 to +1 correlation range; equity pairwise correlations rarely go negative, so that formula compresses all realistic equity scores into 0–50. The Health Score rescales to the 0–1 equity range so avg_corr 0.0 → 100, 0.5 → 50, 1.0 → 0. Risk Analysis is unaffected — it uses `portfolio.diversification_score()` directly. | Two different score ranges on two portfolio-assessment surfaces would confuse when the same 0.5 correlation produced "50" on Health (clearly midrange) and "25" on Risk Analysis (implies problem). The Health Score's use-case (construction quality feedback) prioritizes a 0–100 scale matching the other four sub-scores; Risk Analysis's use-case is a singular diversification metric (0–50 is fine for that surface). Single-sourcing within each surface preserves both. |
| Add-to-winner post-act cooldown | Both add-to-winner generators (`_grow_today` add_positions + `_buy_candidates`) suppress an "ADD — Winning Position" nudge for any name the user added shares to within `ADD_WINNER_COOLDOWN_DAYS` (10). `days_since_last_buy` = age of the NEWEST still-held lot (via `_build_open_lots`, attached to `held_data[ticker]` in app.py alongside `position_age_days`); None (no journal) → no cooldown (calm, not blind). Grow Today surfaces a "🌱 Add Paused — Recently Added (settling)" note (never silent); buy_candidates suppresses inline like its other gates. Legitimate pyramiding resumes after the window. | PATH kept showing "ADD — Winning Position" after the user had already executed the add (held 150 shares) — re-recommending an add the user just made is the screen-watching churn §2B kills. Mirrors the settling-grace lifecycle rule: don't grow (or micromanage) a position you just changed. |
| Signal hysteresis — "steady vs yesterday" (calm advisor 2C) | `signal_hysteresis.apply_hysteresis(today_picks, prior_snapshot)` marks a Grow-Today pick (new_pick / add_winner) whose composite moved ≤ `HYSTERESIS_COMPOSITE_DELTA` (4.0) since its most-recent prior surface AND whose verdict didn't flip → `pick["_hysteresis"]={"stable":True,...}`, rendered as a calm grey "↔ Steady vs yesterday" chip. `prior_snapshot` is built in app.py from `db.load_recommendations` over a 4-day look-back (surfaced_at-desc → first row per ticker = most-recent prior day; handles weekends/holidays). **ANNOTATE-ONLY** — never adds / removes / re-orders / suppresses a pick, so it can't fight the buy gates. Skipped under the AM lock (`_brief_use_lock`) and when `not db.has_db()`; any error is swallowed (cosmetic). | A persistent pick re-surfacing daily reads like a fresh call to re-litigate, nudging the user toward daily re-evaluation (counter to §2B). The chip says "same conviction holding" so continuity looks like continuity. Verdict guard prevents marking a name "steady" when its call actually flipped within the composite noise band. |
| Position lifecycle + settling grace (calm advisor 2A) | `position_lifecycle.classify_position_state(age_days, pnl_pct, gap_to_stop_pct, has_exit_signal)` → exit / at_risk / settling / winning / established (strict precedence — danger beats age; `age_days=None` never yields settling). Position age = oldest still-held lot via `tax_advisor._build_open_lots` (FIFO, split-aware), attached to `held_data[ticker]["position_age_days"]` in app.py. `_review_list` suppresses the approaching-stop tighten when state is "settling" and not critical (complements the Tier-1 profit gate); a lifecycle badge (🌱 Settling / 📈 Winning / ⚠️ At Risk) renders on Review cards. | A freshly-opened position sits 3–8% above its own ATR stop by construction, so routine tighten nudges fired immediately (MSFT). Lifecycle state lets the app respect WHERE a position is before nudging — calm-advisor / §2B. Exits and ≤3%-gap items are never silenced by age (calm, not blind). |
| Profit-aware stop-tightening (anti-churn) | The "Approaching Stop" review (`_review_list`, gap 0–8%) only nudges a still-has-room position (gap 3–8%) to tighten its stop once it has a real gain to protect (P&L ≥ `STOP_TIGHTEN_MIN_GAIN_PCT`, 8%). A freshly-opened/flat position sits 3–8% above its own ATR stop by construction, so it used to trigger an immediate "raise stop to ~break-even" nudge — premature micromanagement that reads as day-trading. CRITICAL-gap (≤3%) positions still surface regardless of P&L. | MSFT: initiated, then same-session "Review Before Close → raise stop to $411.77" on a P&L −0.0% position. Tier-1 of the "make it a calm advisor, not a screen-watching feed" work — enforces the §2B medium-term persona at the signal-cadence level. (Tier-2 backlog: position-lifecycle states, Act-vs-Awareness split, "you're done for today", signal hysteresis.) |
| Stop-breach overrides "add" on Analysis | The Analysis Trade Plan, for a HELD position whose stop is breached (`price ≤ stop` — the same Gap-to-Stop ≤ 0 condition Act Today's SELL uses), suppresses the add-on sizing and renders a red "⛔ Stop breached — exit signal, not an add" banner mirroring the Brief. The Buy composite still renders (it rates the stock); the stop protects the position. (G-18) | ADBE: Act Today showed "SELL — Stop Breached" while Analysis framed the same held position as an "add" with full sizing — protect-capital must override deploy-capital, and the two surfaces must read the same detector so they can't contradict. |
| Macro gate on new picks | `_grow_today` accepts `macro_events` and hard-suppresses new picks in any sector with a HIGH-impact macro event within `MACRO_IMMINENT_DAYS` (3 days). `macro_calendar.affected_sectors(category)` resolves which sectors are in scope. | Phase 2. Opening fresh positions into a known binary catalyst (FOMC, CPI) is the institutional anti-pattern this gate prevents. |
| Macro gate same-day lift | when `_days_until == 0`, `_grow_today` checks if the HIGH event has resolved (ET clock ≥ `ev["time_et"]` OR FRED `released` flag) before suppressing. FOMC is never lifted same-day. Any exception keeps the gate on. | (`daily_briefing._grow_today`, commit `a691c57`) |
| Daily Briefing offline state | When `build_daily_briefing()` raises, the Portfolio page sets `_grow_today_sectors_cache = None` and `_daily_brief_offline = True`. The Watchlist page detects this and shows an explicit warning: "Daily Briefing offline — sector-overlap and active-risk-alert gates cannot run." | Phase 2. Silent gate disable on producer failure was a real risk. |
| Stock Analysis without Portfolio context | The Trade Plan beta-envelope warning depends on `_port_risk_cache`. When the cache is empty (user landed on Stock Analysis without first visiting Portfolio), a prominent "Portfolio context unavailable" info note renders above the Trade Plan. | Phase 2. Don't pretend the gate is active when it isn't. |
| Entry-timing thresholds | `quick_research.py` boundaries use `>=` for upper bounds and `<=` for lower bounds (e.g. `move_1d >= 15` triggers "Avoid Chasing"). Previously strict `>` produced unintuitive cliffs where exactly-15% one-day moves slipped past the gate. | Phase 1 H6. Standard TA convention. |
| Decision constants | All threshold values used to gate, suppress, or downgrade a recommendation live in `stock_analyzer/constants.py`. Features import from this module rather than hardcoding values. | Phase 2. Single source of truth; changes here are policy decisions, not code tuning. |
| SELL integrity guard reads the replay source | The Trade Journal SELL guard validates `shares_val` against `db.recalculate_from_trades()` — the same trade-replay the drift detector uses — NOT the `holdings_df` cache. A SELL exceeding accountable shares is blocked unless overridden. | A guard that read a different source than the detector silently disagreed: after a rebaseline, `holdings_df` had enough COIN shares so two 5-share SELLs both passed, while the replay had only one covering BUY → unmatched SELL → drift. An input guard must read the same book as the detector that flags the violation. (Commit e95ab2d.) |
| Double-submit dedupe (price-excluded signature) | An identical `(ticker, action, shares)` submit within 15 s is rejected. Price is deliberately excluded from the signature. | On a slow page a double-click recorded two trades; the live-prefilled price ticked between reruns, disguising the dup as two "different" trades. Excluding price catches the genuine double-click. (Commit e95ab2d.) |
| Portfolio cache refresh after trade write | `_refresh_portfolio_cache_after_trade(h_df)` (app.py:1788) refreshes `_port_df_enriched`, `_last_port_df`, `_portfolio_value` and related caches after every trade-write operation (Trade Journal BUY/SELL/confirm, delete-trade rebuild, drift-fix button, CSV/paste imports). Uses delta-only load: reuses cached tickers, parallel-loads only new holdings. Wrapped in try/except; silently no-ops on failure, preserving stale-snapshot warning as fallback. | Trades logged without a Home revisit previously showed missing or partial holdings on non-Home pages (brand-new positions missing entirely, same-day multi-buy showing only first entry). Cache now stays in sync across all trade sources. (Commit c1e46aa.) |
| Manual stop override is one-directional | `build_portfolio_df` honours a `manual_stops` entry only when it is ≥ the computed ATR/ratchet stop (tighten, never loosen below the mechanical floor). Overridden rows show 📌 and Stop Type="Manual". Auto-cleared when shares → 0 via `save_holdings` symmetric sweep. | Closes the recommend→act→log loop without letting a user weaken the mechanical safety net. An orphaned stop must never outlive its position. (Action Log Phase A, commits a4ed74b / a4380d4.) |
| Movers flat-day exemption | Discovery movers feed the SAME `_grow_today` New Positions list as curated picks but get their own `MOVER_MAX_PICKS` allowance and are exempt from the flat-day high-conviction suppression and the curated momentum / 1-per-sector rules. They still respect bear-day risk-off, the composite gate, the macro gate, and act-today conflicts. | A composite-Buy stock up ≥5% today IS the clearer direction the flat-day caution waits for — suppressing it defeats the discovery purpose. Deliberate asymmetry; do not "fix" by applying uniform tone-gating. (Commits 67b0dab / e4793ff.) **Label accuracy (2026-07-14):** mover header chips display "Breakout today" as the context phrase rather than the scanner momentum score, which did not gate the mover (entry via day-change breakout bypasses the momentum bar) and does not appear in Analysis Deep Dive. `reconcile_signals()` gains `is_mover: bool = False`; when `True`, `mom_str = "Breakout today"`. |
| Act Today per-ticker consolidation | `_consolidate_act_today` suppresses a risk-trim card when a mechanical exit (stop_breach / sell_signal) exists for the same ticker, and merges multiple risk flags on one ticker into a single card. | You don't trim what you're already exiting, and the same ticker appearing in multiple urgent cards (MU appeared twice) reads as noise, not signal. (Commit 0fd66db.) |
| One risk → one surface (dimension-scoped dedup) | Each risk surfaces ONCE on its highest-priority surface. `_review_list` receives `act_today` and suppresses its negative-news WATCH for any ticker already carrying an ACTIONABLE card — in Act Today, OR a stop/trim ACT in the Review blocks above it (a news WATCH whose only escalation is "tighten stop" is redundant when the stop is already being raised). Dedup is by risk DIMENSION, not by ticker: AVGO still shows two Review cards (earnings WATCH + stop-raise ACT) — earnings is `type:"WATCH"` so it neither suppresses nor is suppressed; genuinely different actions coexist. | DELL appeared as a Critical-News ACT in Act Today AND a "no action" WATCH in Review; NVDA had a raise-stop ACT AND a news WATCH in Review — same risk, redundant/contradictory directives. The Brief is a quick-glance action list, not a scavenger hunt; but blindly deduping by ticker would hide genuinely distinct actions, so the line is "same risk, or different one?" (Commits 02ed2da / next.) |
| Two-column offense/defense Brief | The Brief renders left = Grow Today + More Buy Candidates (deploy capital), right = Act Today + Review Before Close (protect capital). Streamlit reusable column containers (`with col:`) are re-entered to append each section into its column regardless of execution order. | A single-column stack buried the protective items below a long offense list; the offense/defense split mirrors how a PM actually reads the morning. Section chips must stay within their column. (Commits fb7b56c / aec735a.) |
| Finnhub-primary live prices (per data type) | Live prices use `DATA_LIVE_PRICE_ORDER` (Finnhub→yfinance→FMP); history/bundle/indices use `DATA_PROVIDER_ORDER` (yfinance→FMP). "Primary" is per-data-type, configurable, not global. | yfinance is free/unquota'd and the only free source for history/bundle, so it stays primary there; Finnhub's free tier serves real-time quotes, so it's primary for the live-price field. yfinance's weakness is availability — failover + real-time primary mitigate it. (Phase 5; commit 319edad.) |
| Price cross-check: prev_close strict / live loose | The cross-check compares the live-price primary against an independent source — `prev_close` within 0.5% (strict) and live price within 3% (loose). A breach → red "Price unverified" banner. | A delayed validator (yfinance ~15-min) legitimately differs from a real-time primary (Finnhub) intraday, so the live check must be loose; but settled `prev_close` must match across sources, so that check is strict and catches splits/wrong-symbol/poison without false positives. (Phase 5b-ii; commit 9ad0ab6.) |
| Bundle failover keeps the analysis alive | When a yfinance `bundle()` hard-fails (rate-limited), the orchestrator fails over to FMP's bundle (validated fundamentals; news/earnings degrade to neutral/None). | Observed live: yfinance went rate-limited while the app stayed functional. Composite scoring no longer goes dark when yfinance throttles. (Phase 3b; commit cc5076b.) |
| Sparse-`.info` fundamentals backfill | When the yfinance bundle is non-empty (history+news present) but `.info` is sparse, `orchestrator.get_bundle` keeps yfinance history+news and backfills fundamentals (+earnings/revisions via light per-field accessors) from FMP. Backfill source surfaced as an "ℹ️ Fundamentals via FMP" caption on Analysis. FMP `.info` is cached per-ticker (`DATA_FMP_INFO_CACHE_TTL_SEC`, 1h) to protect the 250/day free tier. | Plain bundle-failover didn't catch this — the bundle wasn't empty, only `.info` was — so the fundamental score silently collapsed to a neutral 50 and flipped Buy↔Hold on a data hiccup. (Commits a592159 / d90d6ba.) |
| Verdict withheld when fundamentals unavailable | When fundamentals are absent from ALL sources (< `FUNDAMENTALS_GATE_MIN_METRICS` core metrics present after backfill), `load_all` sets `fundamentals_available=False`. The Analysis page then **suppresses the Buy/Hold verdict** and renders a red "Verdict withheld — fundamentals unavailable" note (composite number not shown); `daily_briefing` holds the ticker OUT of `new_picks` (routes to `composite_unavailable`). | `fundamental_score` returns a fabricated neutral 50 when no metrics are scoreable (`max_points==0`), so the composite would emit a confident verdict on data we don't have. This produced the PINS/HUBS contradiction: surfaced as a Brief "new position" (composite snapshot when data was present) but Hold on Analysis (recomputed when `.info` had vanished). Per the "recommend nothing rather than wrongly" posture, withhold instead of guess. (Commit 3a154f4.) |
| Secret reading tolerates TOML mis-nesting | `_util.get_secret` reads a key top-level, then scans one level of `[section]` tables, then env var. A flat key written after a `[section]` header (TOML nests it inside that table) still resolves. | A `FINNHUB_API_KEY` placed below `[fred]` is parsed as `fred.FINNHUB_API_KEY`; the top-level lookup missed it and Finnhub silently reported unconfigured — exactly the silent degradation the app refuses. (Commit 2d3870c.) |
| Multi-source master switch | `DATA_MULTISOURCE_ENABLED` gates the whole layer. False → `data.py` calls yfinance directly, byte-for-byte the pre-provider path. | A one-line, instant rollback to single-source if the layer ever misbehaves, with no other code changes. (Phase 1–5.) |
| Home cold-load: instant live-price snapshot | The per-ticker `_parallel_load_all` blocks ~8–15s on a cold open. Before it, the Home page paints a cheap snapshot (portfolio value + today's held P&L + open P&L + a positions table) from `holdings × fetch_live_prices` — NO history bundle — into an `st.empty()` placeholder; Streamlit streams it to the browser before the blocking load, then it's cleared once the full Command Center is ready. Gated to the first cold load per session (`_home_loaded_once`) so warm reruns (which hit the `load_all` cache) don't flash it. Render-only — no score/gate change; fully exception-guarded. | A cold open showed a blank "Loading…" spinner for 8–15s; the user now sees their book in ~2s while analysis loads below. Chose this (perceived-speed, scores stay LIVE) over serving the cron-warmed `bundle_cache` as fresh — that cache is lossy (no news/earnings), so it would have neutralized the sentiment leg + blinded earnings gates, making scores silently "as of the morning cron". (Part 1 / 1b.) |
| Morning buy-list email (offense alert) | The cron `scan` run (~9:45 ET), after persisting the scan, emails the high-conviction "New Positions to Initiate". `headless_alert_engine.compute_morning_picks` reuses `_build_context` and assembles the SAME inputs the app feeds `build_daily_briefing` (tone via `fetch_market_indices`, per-pick composites via a `load_bundle` loop, news via `curate_news_items`, macro via `build_macro_calendar`), then calls the SAME `build_daily_briefing` — so no gate is looser than Grow Today. The cron keeps only `xref.verdict_reconciled.verdict == "go"` (the exact field behind the green "✅ Go — Composite Confirms" badge) with a composite present; `notify.render_buy_picks_email` renders mobile-actionable cards (entry zone · shares/$ · stop + "act only if still in zone" guard). Dedup `alert_state` row 3 (per-day ticker-set fingerprint), saved only on a successful send; two DST UTC slots (`45 13`/`45 14`) with a post-open gate (ET ≥ 9:30) so the pre-open winter slot is skipped and the redundant summer slot dedups. Silent when nothing clears; inert without `RESEND_API_KEY`. | The offense counterpart to the protective ("defense") email — lets the user act on the day's gated setups from mobile during a meeting. Reuses the same Resend pipeline + headless engine. The macro calendar is passed (not `[]`) specifically so the email can't surface a buy the app would suppress on a binary-catalyst day (Opus review caught the `macro_events=[]` gap). |
| Today's P&L — held mark vs Tier B day P&L | The header "Today's P&L" metric has two modes. **DEFAULT (held mark):** `Σ(live − prev_close) × shares` over currently-held, priced positions — a mark-to-market of held names vs prior close, labelled "Today's P&L (held)"; excludes realized P&L from today's trades and marks same-day buys from prior close, so diverges from broker on active-trading days (fail-loud "covers N of M positions" caption when any held name doesn't price). **TIER B (true positions day-P&L)** activates when a prior-close snapshot baseline exists AND every held name prices: pure `stock_analyzer/daily_pnl.compute_positions_day_pnl` computes broker-style equity-delta `Σ(current×shares) − Σ(baseline_close×baseline_shares) + (today's sell proceeds − buy cost)`. Because baseline is PRIOR CLOSE (not cost basis), each term measures day-move only — a name sold today contributes (sell−prior_close)×qty (realized DAY portion, not full holding-period `realized_pnl`), a same-day buy contributes (current−fill)×qty. Labelled "Today's P&L" when baseline is prior trading day, else "P&L since {date}". Positions scope: excludes cash & external deposits/withdrawals — never claims broker parity. Falls back to held mark when no baseline / any held name unpriced. Flags "orphan" baseline names (no current holding + no recorded trade today = journal gap). The baseline snapshot (`daily_snapshots` table: `snapshot_date, ticker, shares, close_price`) is written opportunistically once per session in post-close window (weekday ET ≥ 16:00) so stored `close_price` is final; Phase-2 cron will make deterministic. | Reconciles the app's day-P&L with the broker's account "Today" for the equity sleeve. The held-only mark diverged badly on active-trading days (e.g. app −0.57% vs Robinhood ~−5% when the day had two buys and a trim). Tier B is the equity-sleeve rung of the P&L-truth ladder; cash/flows + broker-statement reconciliation deferred. |
| Data-outage resilience (3 layers) | When the history/bundle providers (Yahoo→FMP; Finnhub serves live-price ONLY) all fail, the heavy `load_all` bundle fails for every held name while the live price strip stays fine — historically this blanked Home with 'Could not load ×N'. Three layers now: (1) HONEST EMPTY-STATE — Home distinguishes 'no holdings' from 'holdings exist but all bundles failed' and shows a fail-loud error naming the holdings + an inline cooldown-gated Retry, never 'enter your holdings'. (2) BURST-TAMING — `_parallel_load_all` runs at `DATA_LOAD_MAX_WORKERS`=2 with a `DATA_LOAD_STAGGER_SEC` stagger so a cold-load fan-out doesn't trip Yahoo's burst throttle (refreshing re-bursts and re-trips it — refreshing is the cause, not the cure). (3) LAST-KNOWN-GOOD CACHE — `load_all` write-throughs each successful bundle to `bundle_cache` and, on total provider failure, serves the aged copy (≤ `BUNDLE_CACHE_MAX_AGE_DAYS`) with a `stale_as_of` tag + a Home staleness banner; news/earnings degrade to empty in that mode; cache I/O is wrapped so it can never break the success path; the stale result is intentionally TTL-cached (~30 min). | The 2026-06-10 pre-open incident: Yahoo throttled a 4-worker cold-load fan-out of 10 holdings, FMP couldn't cover, all bundles failed → blank portfolio, while Finnhub quotes still showed. Layers (1)-(3) make a recurrence honest, less frequent, and non-fatal (portfolio still renders on aged data). |
| Recommendations History — SPY-relative alpha measurement | Each surfaced rec (Buy/Hold/Sell) carries `alpha_pct = outcome_pct − spy_return_pct`, measuring the rec's performance relative to the S&P 500 over the rec_date → today window. Alpha is None for acted SELLs (realized P&L spans an unknown holding period that can't be fairly benchmarked to a single calendar window). Regime-adjusted read: a down-tape loss that still beat SPY shows positive alpha. SPY series sourced from cached `fetch_spy("6mo")`. | Outcome scoring conflates directional regime noise with signal quality — a down year where a rec loses 5% while SPY loses 15% is a win. Alpha isolates signal from regime by asking "did we beat the benchmark?" (Commit eeb2740.) |
| Recommendations History — maturity window and outcome aggregation | Recs younger than `REC_SCORE_MIN_DAYS` (5 calendar days) are flagged `outcome_maturing` and shown in the detail table with a ⏳ label but EXCLUDED from outcome aggregates (avg outcome / alpha / best / worst). Action rate counts all recs (acting is known the day the rec surfaces). Rationale: one session of price wiggle is not a meaningful outcome. Scoring begins on day 5. | Phase-5 measurement hardening — recs must age before grading them. (Commit eeb2740.) |
| Recommendations History — by-verdict rollup | `by_verdict()` segments the Recommendations History scorecard into Confirmed / Conflicted / Caution / Mixed / Unverified / Other, presenting recs in verdict-bucket order (best→worst signal quality). This is the engine-quality view: judges the app's actual recommendations (Confirmed) apart from the awareness feed it deliberately surfaces but steers the user away from (Conflicted / Caution). Aggregates action rate, outcome metrics, and alpha per bucket so quality gradation is visible. | A single "overall" scorecard obscures the difference between an engine in the top tier and an awareness feed — both contribute to "action rate" but belong to different confidence levels. Segmentation shows which features are working. (Commit eeb2740.) |
| Recommendations History default rec-type filter | The 📊 Recommendations History page loads with a default multiselect of `new_pick` + `add_winner` only — excluding `buy_candidate` (the More-Buy-Candidates awareness feed). `buy_candidate` names did not clear the `COMPOSITE_BUY` gate and were never a recommendation to act on; including them in the default view inflated "Missed" counts with names the engine correctly flagged to skip. `buy_candidate` remains available in the multiselect for traceability when the user explicitly selects it. | Analytical integrity: the default view now matches the F-161 missed-opportunity scope (`new_pick` only). Consistent with `distinct_missed`'s default `rec_types=("new_pick",)` — the scorecard and the detail table agree on what counts as a miss. (Commit 587fe3d.) |
| NaN-Close bar hygiene (live path) | `stock_analyzer/technicals.py` `compute_indicators` strips `df = df[df["Close"].notna()]` before computing any indicators, removing bars with missing Close prices from the technical analysis frame. `app.py` `load_all` derives `price` from the last non-NaN close (defense in depth); a stray NaN becomes `None`, routed to the existing "N/A" / withheld render path instead of "$nan". The filter mirrors the notna() guard already on the cached path (`db.load_bundle_cache`), so live and cached history enforce the identical invariant: "no Close → not a bar." No-op on healthy data (raw OHLC has no NaN-Close rows) → zero verdict/score drift on the everyday path. | 2026-06-19 (live history on market holiday): Yahoo degraded, trailing bar carried NaN Close. NaN is truthy in Python, so `price = float(df["Close"].iloc[-1])` (a NaN) sailed past every `if price:` guard and sprayed "$nan" through Analysis Trade Plan (Price, Entry Zone, Stop Loss, R:R, Price-Scenario bars). The same NaN bar NaN-poisoned RSI/SMA/MACD/Bollinger indicators, distorting the Technical leg of the verdict. Closed at the data boundary so the whole class is fixed, not one surface; mirrors the "honest N/A over fabricated output" posture. No decision threshold or gate constant changed — pure data hygiene. (Commit 09fcc3d.) |
| Bundle cache — NaN safeguards | `db.load_bundle_cache` drops any bar with a NaN Close before returning the history DataFrame. A trailing partial bar serializes to null in JSON and round-trips back as NaN, so the cached last close (→ `current_price`) becomes NaN. `targets.compute_price_targets` guards against NaN `current_price` via `max(..., default=current_price*1.10)`, so an empty/NaN-price candidate set never crashes. Both guards isolate the resilience cache from the latent NaN bug (unfolds for any stock trading above all projected targets). | 2026-06-10 & 2026-06-19: During Juneteenth (market holiday, blind cache), all 10 holdings showed "Could not load" due to a cached history's trailing placeholder bar. Live prices worked fine. Dual guards (drop NaN bars at load; default= fallback at use) prevent recurrence on cached + live paths; cache I/O is wrapped to never block success. (Commits f4f6ead / 176d36d.) |
| Bundle cache — API health instrumentation | `db.save_bundle_cache` and `db.load_bundle_cache` record events to a new **"bundle_cache"** `api_health` source: `success` = served last-known-good, `empty` = queried but nothing usable (no row / too stale / parse error), `error` = has_db/RLS/parse failure. The Data Health sidebar panel now shows "Bundle cache (last-known-good)" line + an "empty" count. Previously bundle-cache writes were silent (wrapped in `except: pass`), a full blind spot during outages. | Long latency to detect whether the cache is seeding, stale, or broken — no visibility except inference from "Could not load" spam. Instrumented health metrics tie outage symptoms to their root cause and show recovery. (Commits 176d36d / af854a8.) |
| Parallel load errors captured for visibility | `_parallel_load_all` in `app.py` captures each ticker's swallowed exception into `st.session_state["_load_all_errs"]` and the "Could not load {ticker}" warning appends the real reason (e.g. "HTTPError: 429 Too Many Requests"). Previously the error was silently suppressed, leaving users with a blank portfolio and no idea why. Thread exceptions in `ThreadPoolExecutor` are swallowed by the executor; explicit try/except + dict capture makes them visible. | Yahoo throttle-cascade (burst fan-out of requests) blanked Home with no explanation of the actual failure. Visible error messages turn a mystery into actionable context ("check back in 5 min" vs "API key missing"). (Commit af854a8.) |
| Provider circuit-breaker | `orchestrator._providers_for(capability)` returns only capable providers NOT currently in cooldown (`api_health.in_cooldown(source, PROVIDER_RL_COOLDOWN_SEC)`). A provider enters cooldown when the cooldown gate trips (auth_errors ≥ 3, rate_limits ≥ 3, or consecutive_errors ≥ 5) AND its last error is within the window; it auto-recovers when the window elapses. The orchestrator skips cooled providers on `get_bundle`, `_failover_single` (history/bundle/indices/risk-free) so a 429-tripped provider isn't re-called ticker-after-ticker (the FMP quota-exhaustion amplifier). If ALL capable providers are cooled, the orchestrator falls through to the full list and attempts anyway, then the caller's cache fallback catches the failure — never a permanent hard-block. **The live-price path (`_live_price_providers` / `get_live_prices`) is deliberately NOT gated**, so live quotes stay flowing during an outage. `api_health` records per-provider rate-limit and error counts; the Data Health sidebar shows the health status. | Phase 2 (rate-limit-resilience hardening). 2026-06-10 incident: a burst cold-load fan-out (4 workers on 10 holdings) tripped Yahoo, then cold retries exhausted FMP's 250/day quota before the app could fall back to the aged bundle cache — leaving a blank portfolio. The circuit-breaker decouples "I'm actively failing" (skip it, give it time to recover) from "I'm down" (try anyway, cache fallback), preventing quota exhaustion mid-cascade. (Commit d400e7a.) |
| API key redaction in provider errors | `providers/_util.py`'s `http_get_json` catches `requests.HTTPError` and rewrites the exception message through `_redact_url()` (strips `apikey`, `api_key`, `token`, `key` query-string params → `***`) before re-raising. Provider-level exceptions that bubble up to the Streamlit UI or the GitHub Actions log never carry the raw API key, regardless of which surface catches them. | Implements NF-25. FMP REST calls embed `?apikey=...` in the URL; a raw `HTTPError` would expose the key in Streamlit's exception display or the Actions run log. Redacting at the HTTP helper means no call site needs a try/except for key leakage — the protection is structural. (L7, 2026-06-26 audit.) |
| Fine-grained error taxonomy | `providers/_util.py`'s `classify_error()` routes provider exceptions to one of five event types: `auth` (4xx credential or plan-restricted errors), `quota` (429 containing quota-exceeded wording), `rate_limit` (generic transient 429/throttling), `parse` (JSONDecodeError or HTTP 2xx + empty response body), `error` (everything else). The isinstance check on JSONDecodeError runs FIRST so its column-offset message strings don't collide with the numeric HTTP-status checks below it. All three keyed providers (Finnhub, FMP, yfinance) now call `classify_error()` instead of the previous boolean `is_rate_limit()`. `api_health` gains per-event counters: `auth_errors`, `parse_errors`, `quotas`; RED trips on `auth_errors ≥ 1`; YELLOW trips on `quotas ≥ 1`, `parse_errors ≥ 3`, or error-rate > 20%; the cooldown circuit-breaker requires `auth_errors ≥ 3` to trip (matches the `rate_limits` threshold — a single plan-restricted 403 shouldn't silence a provider for 120 s). The Data Health chip shows `quota`/`auth`/`parse` badge counts alongside existing `err`/`RL` counts. | A quota hit (plan-restricted endpoint), a parse failure, and a credential error have distinct recovery paths that the previous bool couldn't distinguish. Finnhub's silent HTTP 200+empty-body rate-limit shape is now correctly routed to `rate_limit` (the empty-body guard in `http_get_json` raises `ValueError("HTTP <status> — empty response body")` before `resp.json()` is called, so the numeric check on the error string matches). (Commit 2b8a778.) |
| FMP daily quota counter | `db.increment_daily_quota("fmp")` fires after each successful FMP request. `db.get_daily_quota("fmp")` reads today's count from `api_quota_log` (§6.17); `api_health.get_fmp_daily_quota()` caches this read (5-min TTL) so the orchestrator never hits Supabase per-call. When daily count ≥ `FMP_DAILY_SOFT_CAP` (220), `orchestrator._providers_for()` drops FMP from the capable list; falls through to the full list if all providers are suppressed — never a hard-block. The Data Health chip shows **"today: N/250"** with a ⚠️ prefix at or above the soft-cap. **Ships inert until the `api_quota_log` DDL + `increment_api_quota` function are applied** — `get_daily_quota` returns None, the chip field is hidden, and the gate stays open. | Rate-limit-resilience Phase 3: actively count FMP calls so the orchestrator can pause before the 250/day hard limit rather than relying solely on the circuit-breaker to detect a burst exhaustion. The 30-call buffer accommodates requests already in flight when the gate trips. Phase 2 reacts to error signals; Phase 3 reacts to measured usage — they complement rather than replace each other. (Commit 2b8a778.) |
| Finnhub endpoint tagging in errors | `finnhub_provider.py` prefixes every recorded error with the endpoint name and ticker — e.g. `"quote/AAPL: ..."` vs `"news-sentiment/AAPL: ..."` — so the Data Health chip's last-error snippet identifies which Finnhub surface is misbehaving. The empty-response guard in `http_get_json` raises `ValueError("HTTP <status> — empty response body")` before calling `resp.json()`, routing Finnhub's silent HTTP 200+empty rate-limit shape to the `rate_limit` branch of `classify_error()` instead of an opaque JSONDecodeError. | `/quote` (free-tier) and `/stock/news-sentiment` (likely premium-gated on the free plan) have very different recovery paths; a shared generic error message made them indistinguishable. Endpoint tagging converts "Finnhub is red" into "news-sentiment is plan-restricted, quote is fine." (Commit bde02c5.) |
| SPY-fetch dedup | `@st.cache_data(ttl=1800) _cached_spy(period)` wraps `fetch_spy()` (keyed by period: "3mo" / "6mo"). A cold-load of 10 held names calling `compute_all_risk(..., spy_df)` across the portfolio page (risk metrics), Trade Review page (benchmarks), Perf Advisor (6-mo attribution), and Recommendations History (alpha scoring) each independently fetched SPY ~10× per session. The wrapper caches at the app layer (same pattern as `_get_rfr`), so the first caller fetches and the rest hit the cache — reducing the per-session SPY provider load by ~10×. | 2026-06-10 incident context: the cold-load burst was amplified by naive SPY re-fetching on every downstream computation. Dedup prevents the redundant tier-2 surge that cascaded a primary provider's initial 429 into a full quota wipe before the fallback cache could seed. (Commit d400e7a.) |
| SELL two-step confirmation (Trade Journal) | When the Log-a-Trade form is submitted with action=SELL and all validation gates pass, the verified `record` dict is stashed to `st.session_state["_tj_pending_sell"]` and the page reruns — `db.save_trade` is NOT called yet. A confirmation card renders above the Log-a-Trade expander showing ticker, shares, price, and realized P&L, with [✅ Confirm SELL] / [✗ Cancel] buttons. Confirm runs the identical save + holdings-sync path as the original SELL path; Cancel restores `_tj_prefill` so the form reopens populated. BUY submissions skip the intercept entirely. The stash survives tab navigation within the session but is lost on app reboot — the trade was never committed so no data is orphaned. (Commit 7793be2.) | A fat-finger or misread ticker on a SELL creates a false realized-P&L record that distorts the Recommendations History audit trail and requires a manual correction to undo. SELL is the highest-stakes single action in the app; BUY cost is zero extra clicks. The two-step pattern follows the `_pending_page` indirection principle already in the nav layer: buffer in session_state, consume on next run, never mutate the DB in the same render that accepted the input. |
| Stress test — historical scenario expansion | `stress_test.SCENARIOS` now has 9 named scenarios (was 7). Two new scenarios use per-sector overrides via `_SECTOR_SHOCKS`: **2008 GFC** (`id="gfc_2008"`, `spy_move=−57.0%`) — Financials −78%, EV & Auto −75%, Energy −50%, Healthcare −22%; and **Stagflation** (`id="stagflation"`, `spy_move=−18.0%`) — Energy +30%, Defense +12%, AI & Data −30%, Clean Energy −12%. The sector-override path means each holding's projected loss uses the sector-specific drawdown rather than a pure beta-scaled SPY move — preserving the design of the existing 2022 Rate Shock and COVID Crash scenarios. Awareness only; never issues a recommendation or gates an entry. (Commit 3cbd6a6; reqs F-14a.) | The original 7 scenarios lacked any GFC-depth event (the worst credit event in modern history) and had no stagflation (inflation + stagnation) scenario. A portfolio with heavy Financials or long-duration tech looks fine under a −30% bear scenario but catastrophic under GFC or stagflation conditions — the sector-specific overrides expose that asymmetry. |
| Behavioral overtrading detection | `trade_analytics._build_overtrading_stats()` computes the current calendar-month BUY+SELL trade count (excluding SPLIT rows) and compares it to the rolling 12-month average. Returns `{current_month, current_month_count, rolling_avg, multiplier, is_elevated}`. `build_behavioral_insights()` raises a **HIGH** card at multiplier ≥ 2.0× and a **MEDIUM** card at ≥ 1.5×. `build_full_analytics()` now also returns `win_rate` in its dict (it was computed but not included in the returned value in the prior version) and `overtrading_stats`. A colour-coded KPI row (Win Rate / Profit Factor / Trades This Month / Behavioral Alert count) renders inside the 🩺 Positions tab on AI Insights (before the Thesis Reviews section), pointing to Trade Journal. HIGH priority insights are surfaced as `st.warning()` banners; the row only renders when ≥ 5 trades exist. **Relocated 2026-07-22 (commit `6e150a4`)** from a page-level row above the cadence tabs (its original placement at ship time) into the Positions tab — no logic change, indentation only. (Original: commit 3cbd6a6; relocated: commit 6e150a4; reqs F-166/F-167.) | Overtrading is the most common behavioural drag on retail portfolios — it inflates transaction costs and often reflects reactive/emotional decision-making. A rolling average baseline distinguishes "I'm active because the market is moving" from "I'm trading more than my own historical norm." The 2.0×/1.5× thresholds are hardcoded in the logic (not policy constants) because they are calibrated statistical triggers, not investment-policy gates. |
| Rate sensitivity per ticker (TLT-based) | `risk.rate_sensitivity_per_ticker()` builds a per-holding rate sensitivity table for the 🔗 Risk Analysis page, combining two complementary reads: (1) the structural sector score from `macro.RATE_SENSITIVITY` (imported lazily to avoid circular import at module level) and (2) an empirical Pearson correlation of each holding's daily returns vs TLT (`risk.pearson_corr_vs_benchmark()`), requiring ≥ 20 overlapping trading days. TLT falls when long rates rise; a negative TLT correlation = holding drops when rates rise; positive = rate beneficiary. Sorted most-sensitive first (TLT Corr ascending; sector score as fallback). A sector with **no** `RATE_SENSITIVITY` key yields `Sector Score = None` (rendered `—`), never a 0.0 stand-in that would read as a real "structurally rate-neutral" finding; a row with neither a correlation nor a label reports an **Unknown** implication and sorts **last** — unknown is not neutral, so it must not sit mid-table. Reachable for a held ticker with no `TICKER_SECTORS` entry (falls back to the raw provider GICS string) and for the curated labels `RATE_SENSITIVITY` does not yet cover (`Industrials`, `Communications`, `Consumer Staples & Retail`). Fixed 2026-08-16 after a live screenshot showed held NEM/MRVL rendering a fabricated `+0.00`. A weighted-portfolio TLT correlation summary line closes the table. TLT is fetched via `data.fetch_tlt("3mo")` wrapped in `_cached_tlt()` (30-min TTL, same pattern as `_cached_spy()`). **Awareness only — never gates, suppresses, or scores.** (Commit f0b946c; reqs F-87a.) | Duration risk is invisible in a single-ticker view but can dominate a concentrated tech-heavy portfolio during a rate-rising cycle. Combining the structural sector label with empirical correlation gives two complementary reads: the sector score generalizes (it is always available, even for holdings with thin history) while TLT Pearson is data-driven and ticker-specific. A holding in "Semiconductors" might correlate differently to rates than its peer if it has a different cash-flow profile. |
| Engine Trust by Band (Trade Journal) | `recommendations_history.engine_trust_by_band()` groups all enriched recommendation outcomes by composite band: sub-threshold (<65) / BUY (65–74) / Strong BUY (≥75). Excludes `outcome_maturing` rows. For each band: `n_recs`, `n_acted`, `action_rate`, `avg_alpha_acted`, `avg_alpha_passed`, and a plain-English `edge_comment`. Rendered as a collapsed `st.expander` in Trade Journal (lazy compute — no DB query on page load). Wrapped in `try/except` for graceful degradation when history is sparse. Answers: "did you act more often at higher conviction, and did higher conviction deliver higher alpha?" (Commit f0b946c; reqs F-165.) | The engine's conviction is expressed in composite score bands, but the only way to know if that conviction was well-calibrated is to track whether the user acted at the right bands AND whether those bands actually delivered. A flat action rate across all bands signals the engine's gradation is being ignored; a band where acted alpha < passed alpha suggests the user is acting on the wrong signal. |
| Opportunity Cost expander (Trade Journal) | A collapsed `st.expander` ("💸 Opportunity Cost — what you passed on") in Trade Journal computes lazily on open: 90-day window of `new_pick` recs, using `recommendations_history.distinct_missed()` (scope = `rec_types=("new_pick",)`) + `missed_split()` + `compute_outcomes(min_days=5)`. Shows a 4-KPI strip (recs passed / would've won / dodged losers / avg missed return) and a top-5 named ticker list with per-ticker alpha vs SPY. Scope excludes `buy_candidate` (passing on gate-clearing recs is a miss; passing on awareness-feed names is not). No new DB table — reuses `db.load_recommendations()` + existing helpers. Pointer to 📊 Recommendations History for the full chart. (Commit f0b946c; reqs F-164.) | Tracking only the trades you made misses half the picture — what the engine said and you ignored. An opportunity cost view provides accountability for inaction: it distinguishes "I dodged a loser" (good pass) from "I missed a winner" (costly pass), closing the perception gap between signal quality and personal action discipline. The 90-day window keeps the view actionable (recent decisions) without being too narrow to be statistically meaningful. |
| Stress test — historical scenario replay | `stress_test.HISTORICAL_WINDOWS` is a module-level dict mapping scenario IDs to `(start_date, end_date)` string tuples for three real market events: `covid_crash` (2020-02-19 → 2020-03-23), `rate_shock_2022` (2022-01-03 → 2022-10-13), `gfc_2008` (2007-10-09 → 2009-03-09). `fetch_historical_drawdowns(scenario_id, tickers)` fetches per-ticker OHLCV via `yfinance.download(multi_level_index=False)` with a lazy import and computes peak-to-trough as `(min_close − first_close) / first_close × 100`; tickers with fewer than 5 trading days or a non-positive first-day close return `None`. In `app.py`, an "📅 How did your holdings actually perform?" `st.expander` renders for these three scenarios only (checked via `HISTORICAL_WINDOWS.get(scenario_id)`). Loading is button-gated; results cached per-scenario in `st.session_state[f"_hist_stress_{scenario_id}"]`. The comparison table shows **Model Est. (%) / Actual (%) / Δ (Actual−Model)** with green rows (Δ > 5) and red rows (Δ < −5). Custom Scenario and scenarios without a `HISTORICAL_WINDOWS` entry show no expander. Awareness only; never gates or scores. (Commit 0e8dc9f; reqs F-168.) | Model estimates are beta/sector-scaled projections that systematically over- or under-estimate depending on idiosyncratic factors (e.g., a high-beta name that held up during COVID because it benefited from stimulus). Comparing model to actual grounds the user in calibration reality — the model is a risk-exposure tool, not a precise forecast. The three windows cover the most significant portfolio-stress events since 2007 where yfinance has full OHLCV history. |
| Broker history text import (paste-based) | `stock_analyzer/broker_screenshot.py` (pure — no Streamlit or DB imports). The initial Vision-based approach (97590fb) was replaced by a pure-Python regex parser (`parse_robinhood_text()`) in 4e4f46c because Robinhood History screenshots don't include full dates, making year inference unreliable. The paste-based path is more reliable. Parser anchors on `"Individual · [Month Day]"` lines (each Robinhood order is a predictable 4-line block); skips "Canceled" orders; reads company name, order type, shares, and price. Ticker resolution: local `_TICKER_MAP` lookup table (~60 company names) → optional Claude text API fallback for unknowns (lazy `anthropic` import; no API call needed for common names). `_infer_year()` resolves partial dates ("Jul 9" → full date) with prior-year fallback. `find_app_only_in_range()` compares by content key (ticker/action/shares/price — not date-exact). `last_screenshot_sync_date()` reads `trades.notes` for both `"RH screenshot"` and `"RH text import"` tags (backward-compat). Write path unchanged: `st.data_editor` preview → `db.save_trade` + `recalculate_from_trades`. **No auto-deletes ever.** (Commits 97590fb + 4e4f46c; reqs F-87b.) | Robinhood screenshots omit the year from date strings ("Jul 9" not "Jul 9, 2025"), making Vision-based year inference a coin-flip for trades near a year boundary. The History text paste gives the same structured data as a screenshot but in a format the regex can parse deterministically. The local ticker map handles the common case with zero API cost; the Claude text fallback only fires for unknown company names. |
| Home page tab consolidation (11 tabs → 5 + promoted section) | The Home page previously rendered 11 `st.tabs()`: Today's Brief, Evening Debrief, Overview, Performance, P&L Attribution, Alerts & Actions, Risk Analysis, Relative Strength, Sector Rotation, Rankings, AI Brief. Commit 9c1de1f (2026-07-12) promotes Today's Brief to a full-width section rendered before `st.tabs()` — always visible, no longer a tab. Its action badge (`_db_icon`, a 🔴 suffix when Act Today items exist) moved from tab-label to `st.subheader(f"📋 Today's Brief{_db_icon}")`. Five tabs remain: 🌙 Evening Debrief (unchanged, standalone), 📊 Portfolio (= old Overview + Performance + P&L Attribution concatenated), ⚠️ Risk & Alerts (= old Alerts & Actions + Risk Analysis; preserves `n_danger`/`n_warning` badge expression), 📈 Analytics (= old Relative Strength + Sector Rotation + Rankings), 🤖 AI Brief (unchanged, standalone). Pure structure — all tab bodies still execute on every rerun (Streamlit `st.tabs()` execution model unchanged); no recommendation logic, gate, threshold, or scoring changed. | Per 2026-07-12 UX review (finding I1, docs/reviews/2026-07-12-UX-review.md), 11 parallel tabs competed for attention before the user made any decision, at odds with the app's "decides, not informs" operating posture (§2A). Mirrors the 🧠 AI Insights page restructure (commit b8bc336, vertical scroll → status-strip + cadence-tabs). Built via single-source line-slice/reassemble script with pre-verified boundaries, then independent post-hoc content-preservation diff (old vs new, blank/comment-stripped, whitespace-normalized: exactly 10 diff hunks, every one an intentional tab-declaration line — zero unexpected differences across ~17,785 lines). Opus-reviewed SHIP, 0 blocking findings. Reqs F-32/NF-43 updated in the same session to drop stale "tab" framing now that Today's Brief isn't a tab. |
| Nav follow-up — Portfolio Overview extraction + AI Snapshot rename | Live-review of the Home tab consolidation (prior row) surfaced two issues. (1) The Home tab named "📊 Portfolio" collided with the pre-existing PORTFOLIO sidebar nav group (`_NAV_GROUPS`, containing Trade Journal/Trade Review/Recommendations History). Fixed (commit 2a61398) by extracting that tab's content — plus the "📈 Analytics" tab (Relative Strength + Sector Rotation + Rankings) — into a new standalone page `elif page == "🥧 Portfolio Overview":`, added to the PORTFOLIO nav group positioned first. (Page icon later changed from 📊 to 🥧 in the 2026-07-13 UX review to eliminate the icon collision with "📊 Predictive Analytics".) The new page has 2 internal tabs (same names as the old Home tabs) and the same "haven't visited Home this session" empty-state gate Catalyst Watch/Account already use (`_render_portfolio_not_loaded()` + `st.stop()` — the `st.stop()` pattern means the moved content needed zero indentation change). One new cross-page cache was added to Home's preamble, `_risk_advisor_recs_cache` (full recommendation list), and one existing preamble local (`held_tickers`) was newly published as `_last_held_tickers` so the new page rebinds Home's authoritative full holdings list (updated in `CLAUDE.md`'s cache-key list). Home's tab bar shrank from 5 to 3: Evening Debrief, Risk & Alerts, AI Snapshot. (2) The remaining Home tab "🤖 AI Brief" was renamed to "🤖 AI Snapshot" (commit bb508c7) — investigation found zero functional overlap with the standalone "🧠 AI Insights" page (AI Brief: on-demand, session-only, multi-provider Claude/OpenAI/Gemini point-in-time narrative; AI Insights: persisted, Anthropic-only, cadence-driven thesis/debrief/monthly-report/analyst-coverage suite) but a real naming/discoverability risk. Renamed rather than merged (merging would have broken AI Insights' clean single-provider/persisted-artifact design). Session-state cache key `_ai_brief__{provider}__{model}` renamed to `_ai_snapshot__{provider}__{model}`, and mutual cross-reference captions were added on both surfaces. User Guide's "pages at a glance" gained entries for both AI Snapshot and Portfolio Overview. | Both fixes originated from reviewing the live app after prior deploy — information-architecture principle: a Home tab's name/content must not silently collide or overlap with an existing standalone nav destination. Both investigated first (technical dependency analysis via `symtable`-based free-variable tracing, confirming which values were cross-page cached vs Home-preamble-only) before executing. First (structural move) Opus-reviewed; second (label + doc-only rename) judged low-risk. Phase A + B of a 3-phase nav cleanup; **Phase C (splitting "⚠️ Risk & Alerts" into two pages, "🔗 Risk Analysis" and "⚠️ Alerts & Actions", under PORTFOLIO) queued, not yet built.** |
| Nav follow-up Phase C — Risk & Alerts split + Home fully de-tabbed | Commit `a810588` (2026-07-13). Home's "⚠️ Risk & Alerts" tab (itself a Phase-3 merge of two previously-separate tabs) split back into two standalone pages under the PORTFOLIO nav group: "🔗 Risk Analysis" (active alerts, custom price alerts, rebalancing recommendations, diversification advisor moved to "⚠️ Alerts & Actions"; leverage awareness, portfolio risk dashboard, market-risk posture, correlation heatmap, rate sensitivity, risk action plan, stress testing stay in "🔗 Risk Analysis"). Both pages use the same `_render_portfolio_not_loaded()` + `st.stop()` empty-state gate as the Phase-A Portfolio Allocation page. Per user choice, Home's remaining 2 tabs (Evening Debrief, AI Snapshot) were ALSO converted from `st.tabs()` to plain sequential full-width sections — Home no longer calls `st.tabs()` at all, matching how "Today's Brief" already rendered as a promoted section (the reasoning: with only 2 tabs left, the click-to-hide tradeoff tabs exist for barely applied). 8 new session-state cache keys added to Home's preamble: `_alert_list_cache`, `_actions_cache`, `_div_recs_cache`, `_corr_df_cache`, `_div_score_cache`, `_avg_corr_cache`, `_risk_pairs_cache`, `_div_label_cache` — these were dependencies without an existing cross-page cache (`_risk_advisor_recs_cache`, needed by Risk Analysis, already existed from Phase A and was reused). Two more keys, `_n_danger_cache`/`_n_warning_cache`, were added after Opus review flagged that the old merged tab's live danger/warning badge had no replacement once the tab became a static nav entry — these now badge the "⚠️ Alerts & Actions" sidebar nav item the same way the existing "🔔 Catalyst Watch" nav badge works (a pre-existing pattern, reused not invented). One dead-code removal: Rate Sensitivity's "No holdings recorded" empty-state message was deleted after confirming (by reading `stock_analyzer/risk.py`'s `rate_sensitivity_per_ticker()`) that it emits exactly one row per `port_df` row, so the row list can only be empty when `port_df` itself is empty — a condition the new page's own load-gate already precludes by construction, making the branch genuinely unreachable dead code. The `if _rs_rows:` guard itself was left in place (now unconditionally true) rather than de-indenting ~50 lines purely to remove it. `CLAUDE.md`'s own session-state cache-key documentation list was also fully refreshed in this commit — it had drifted stale even before this phase (several real, active cache keys were missing from the documented list, unrelated to this specific change). | Same information-architecture principle driving Phases A/B (a Home tab's content/name should not silently collide, overlap, or lose fidelity relative to standalone nav destinations) — extended here to the "too few tabs left to justify the widget" case. Built via a one-shot slice/reassemble script with pre-verified line-range assertions, dedenting 4 separate content chunks (a first for this specific extraction pattern — Phases A used a different technique, `st.stop()` after the gate, specifically to avoid needing any dedent at all). One dedent-function bug was hit and fixed during the build: a blanket "every non-blank line must start with 4 spaces" check failed on lines inside a multi-line markdown string (table content with no leading whitespace, since Python's indentation rules don't apply to string-literal content) — fixed by leaving such lines untouched rather than raising, since a real Python statement at that nesting depth always has ≥4 spaces (so a 0-indent non-blank line can only be string content). Verified via a targeted byte-for-byte comparison of each moved chunk against a pre-extraction backup, rather than a generic line diff — a generic diff had produced confusing, hard-to-read false-positive-looking output on the Phase-3 extraction due to `difflib`'s `SequenceMatcher` getting confused by large block reordering. Opus-reviewed: SHIP, 0 blocking — the reviewer independently verified free-variable resolution via three separate methods, confirmed indentation uniformity across all 4 moved chunks, confirmed all cache keys publish on both the cache-hit and cache-miss branches (including exception-path fallback values), and independently re-read `risk.py` to confirm the dead-code claim rather than trusting the stated reasoning. |
| UX review remediation — 2026-07-13 (3 passes + terminology fix, commits `0b7417c` / `5f2a62b` / `76e61d5` / `e1d7b2a`) | Structured audit (`docs/reviews/2026-07-13-UX-review.md`). **Pass 1 — critical issues + quick wins:** (C1) Portfolio Overview page icon changed from 📊 to 🥧 everywhere (nav, dispatch `elif page == "🥧 Portfolio Overview":`, internal tab, User Guide) to eliminate icon collision with "📊 Predictive Analytics". (C1/I5) Predictive Analytics moved from PORTFOLIO nav group to RESEARCH group — it is a backward-looking calibration tool, not a portfolio-action surface. (C2) Rate Sensitivity table caption: `macro.RATE_SENSITIVITY` Python attribute reference removed and replaced with plain-language description. (C3) Analysis verdict formula: abbreviated pillar names expanded — "BQ" → "Business Quality", "Val" → "Valuation" (Business Quality carries 35%; abbreviation risk too high on a primary decision surface). (C4) Alpha Attribution removed from `st.tabs()` (it was a "coming soon" dead-end equal-weight alongside 4 live tabs); moved to a collapsed `st.expander` below the 4 live tabs. Page caption and spinners updated. Bear-mode Grow Today empty state: exit-condition hint added pointing to 🌐 Macro. Analysis Scorecard column header: "Score" → "Composite Score". Today's Actions chip: "to tune up" → "to maintain". Beta expander: backtick-formatted constant names replaced with plain-English labels. **Pass 2 — improvements + consistency:** (I1) Alerts & Actions Custom Price Alerts: store initialization and trigger-check moved outside the expander; config form (input fields, save button) wrapped in a collapsed `st.expander` — it is a setup surface not an action surface, and was blocking access to Rebalancing Recommendations. Fired alerts render above the expander. (I6) Analysis page: for single-ticker analysis, a verdict banner (label + score + rationale) now renders above the Summary Scorecard. "Suggested Action" → "Recommended Action" in rebalancing cards. "Recommendation" → "Recommended Action" in Risk Analysis advisor cards. "awareness-only" → "awareness only" (no hyphen) in 4 user-facing copy locations. **Pass 3 — heading style + score labels:** Risk Analysis four first-level `st.markdown("### ...")` headings promoted to `st.subheader()` (Portfolio Risk Dashboard, Rate Sensitivity, Risk Action Plan, Stress Testing). Alerts & Actions Diversification Advisor sub-section headings same. Add-to-winner cards: "Score" → "Momentum" (it is `port_df["Score"]`, the single-factor scanner score, not the composite). **Terminology fix (commit `e1d7b2a`):** 4 remaining user-facing occurrences of "Daily Brief" / "Daily Briefing" replaced with "Today's Brief" — the Watchlist offline banner, Risk Analysis posture warning, news-suppression explanation, and trim-log notes field. Code identifiers (`daily_briefing.py`, `_daily_brief_offline`, etc.) unchanged. The remaining open consistency items (alert severity vocabulary, signal/recommendation/pick, position/holding) were audited on 2026-07-14 and closed as intentional: each term pair describes genuinely distinct concepts in different contexts, not random synonym variation. | UX audit sourced from `docs/reviews/2026-07-13-UX-review.md` (a structured daily-retail-investor perspective). Principles applied: (1) icon recognition is a primary navigation mode on dense dashboards — duplicate icons eliminate that affordance; (2) a backward-looking calibration tool belongs in a research group, not alongside portfolio-action surfaces; (3) a "coming soon" tab occupying equal visual weight as 4 live tabs misleads the user at page load; (4) config surfaces should not block action surfaces on scroll; (5) heading style consistency reduces cognitive friction when navigating between pages; (6) label vocabulary should match the data: "Momentum" for the scanner score, "Business Quality" for the fundamental pillar, "Composite Score" for the 4-pillar aggregate. |
| Earnings Playbook Phases 1+2 — CNBC enrichment, Finnhub auto-fetch, F-1 checkpoint | Commits `7d09857`→`cf6cc19` (2026-07-13). **Phase 1 (F-174):** `earnings_intel.extract_playbook()` (Sonnet 4.6) extracts CNBC-sourced `beat_rate_pct`/`recent_reaction_direction`/`consensus_growth_pct`/`what_to_watch_cnbc` per stock from a pasted preview article, saved to `earnings_context` (§6.18). `earnings_advisor._recommend()` gains two REDUCE conditions gated on this context (poor beat history + weak composite; bearish reaction + composite below `EARNINGS_BEARISH_REACTION_COMPOSITE_GATE`) and a HOLD_OR_ADD narrative annotation — all evaluated strictly after the function's pre-existing EXIT check, so CNBC data can never override an EXIT. **Phase 2a (F-175):** the original paste-based post-earnings design was replaced same-day with a Finnhub-native auto-fetch (`earnings_intel.fetch_recent_results()`, no LLM) because CNBC does not publish consolidated post-earnings roundups; results save to `earnings_results` (§6.19). **Phase 2b (F-176):** `thesis_advisor.generate_earnings_thesis_update()` offers a suggestion-only INTACT/WEAKENING/BROKEN thesis-status update when a saved thesis meets a recent earnings result, gated to 🧠 AI Insights → Positions; never auto-saves. All three ship inert until their respective DDLs are applied. | `earnings_advisor.py` already had EXIT/REDUCE/MONITOR/HOLD/HOLD_OR_ADD playbook logic (F-11) but no knowledge of historical beat rates or post-earnings reaction patterns, which only exist in curated sources like CNBC Pro. Phase 1 enriches the existing playbook rather than rebuilding it; Phase 2 closes the F-1 thesis-tracking loop so an earnings result can prompt a thesis re-grade without waiting for the weekly cron. The mid-build pivot from paste to Finnhub-fetch for Phase 2a reflects a real UX constraint discovered while building, not a design change of the underlying thesis-checkpoint mechanism. |
| Earnings Playbook Phase 3 — Catalyst Scanner (F-37b) | Commit `0cac9ee` (2026-07-13, same day as Phases 1+2). `earnings_advisor.build_earnings_catalyst_candidates(watchlist_tickers, held_tickers, composites, earnings_context, today, lookahead_days=30)` (pure) returns NON-held watchlist names reporting within `lookahead_days` that pass beat_rate ≥ `EARNINGS_MIN_BEAT_RATE_ENTRY` (70.0) + composite ≥ `COMPOSITE_BUY` (65) + reaction ≠ bearish, ranked beat-rate × composite × reaction. Rendered as the awareness-only "🎯 Entry Candidates" tab on 🔔 Catalyst Watch (its own tab since the I11 restructure, 2026-07-16), reading `db.load_earnings_context_batch()` for the CNBC-sourced beat-rate/reaction; empty state prompts the user to paste via Ideas Inbox → Pre-Earnings. Each candidate has an Analyze bridge. Never a Buy rec. | Documented retroactively on 2026-07-16 — the feature shipped 2026-07-13 but was omitted from requirements/architecture at ship time and mislabeled PARKED in the CLAUDE.md queue + `project_earnings_playbook` memory until a doc-sync audit caught it (the exact drift class `feedback_doc_integrity_zero_hallucination` warns about). No code change in the backfill. |
| New-position data freshness gate (`GROW_TODAY_MAX_FUND_AGE_DAYS`, F-180) | `_grow_today()` in `daily_briefing.py` runs two freshness checks before the existing `fundamentals_available` gate: (1) `stale_as_of is not None` — bundle was served from the Supabase `bundle_cache` fallback (potentially up to `BUNDLE_CACHE_MAX_AGE_DAYS` (5) days old) → routes to `composite_unavailable`; (2) `fund_cache_age_days > GROW_TODAY_MAX_FUND_AGE_DAYS` (2) → same path. Both surface the "Pending Verification" banner with a Refresh button. New constant `GROW_TODAY_MAX_FUND_AGE_DAYS = 2` in `constants.py` (inserted after `BUNDLE_CACHE_MAX_AGE_DAYS`). `_SYNTH_SCHEMA_VER` bumped 1 → 2 to flush warm session caches. The two checks share the existing `composite_unavailable` bucket — no new code path, only new gates that feed the existing suppression. | New-position recommendations carry higher trust expectations than held-position display. INTC incident (2026-07-14): `load_all()` fell back to the Supabase `bundle_cache` (data up to 5 days old, `stale_as_of ≠ None`), producing composite ≥ 65 (Buy). After the 30-min `@st.cache_data` TTL expired, fresh Analysis showed composite 32.1 (Sell) — the app had recommended based on data that was 5 days old. `BUNDLE_CACHE_MAX_AGE_DAYS = 5` is the correct tolerance for HELD-position display (the only thing that fires on those is a display verdict, not a "new position to initiate" call); the new-position gate tightens to 2 days because adding a new position is a capital-deployment decision, not a passive display update. |
| Composite freshness check on _home_synth_cache HIT path (F-181) | On every Home page render where _home_synth_cache is a HIT, the app iterates _grow_composites and re-calls load_all(ticker) for any bundle whose fetched_at is older than 1 800 s. If any were refreshed, build_daily_briefing() is re-run with the fresh composites, and _grow_today_sectors_cache is updated. The snapshot is patched in-place. Skipped when Setup is Locked. Falls back to snapshot bundle on load_all() failure. Prior to this fix, the two-TTL mismatch (synthesis: no sub-day TTL; load_all: 30-min @st.cache_data) caused Grow Today cards to show stale composites while Analysis showed fresh data. | Financial risk: stale composites can flip a Sell-signal ticker to a Buy recommendation. Approach keeps the fast HIT path for fresh data (load_all() returns from @st.cache_data at no API cost) while auto-correcting when the cache has expired. |
| Nav rename — Alerts & Actions → Signals & Advice (2-tab split, F-13/F-13a) | Commit `291b0a9` (2026-07-22) renamed the PORTFOLIO nav item `⚠️ Alerts & Actions` → `📡 Signals & Advice` and split the flat page into two `st.tabs()`: **📡 Active Signals** (Active Alerts by category — F-169, Custom Price Alerts — F-170, Rebalancer cards — F-13) and **🧩 Diversification** (Diversification Advisor — F-13a). Same-day polish, all `app.py`-only: `be26c64` replaced full-width alert banners on Active Signals with a 3-column category-card grid; `a89e0c6` removed a duplicate severity icon and fixed bold-ticker markdown rendering; `6b88949` moved the "portfolio well-balanced, no rebalancing needed" success chip to the top of the tab. The rename commit's own message states it updated nav badge logic, the Home "Alerts" metric help text, the rebalance caption, and the User Guide page description + daily-workflow step — verified via `grep -c "Alerts & Actions" app.py` = 0 / `grep -c "Signals & Advice" app.py` = 9 consistent hits. No constants, gates, or scoring logic touched in any of the 4 commits (verified `--stat`, `app.py` only). | The old name collided conceptually with the separate ALERTS sidebar nav group below it (same UX risk class as the Home-tab-name collisions documented in the two "Nav follow-up" rows above). Splitting the flat page into Active Signals / Diversification tabs also gives the "which sector is underweight" question its own focused surface, separate from the reactive alert feed. |
| Behavioral Fingerprint audit relabel — "Data Readiness Audit" → "Signal History Coverage" (F-192) | Commit `b8939eb` (2026-07-22), copy/label only — zero logic change (verified via diff: all edits are string literals — expander title, caption, column headers, a `rec_type`→friendly-label map, warning/success/info prose). The 📊 Predictive Analytics expander (F-192, shipped 2026-07-17) was renamed from "🧬 Behavioral Fingerprint — Data Readiness Audit" to "🧬 Behavioral Fingerprint — Signal History Coverage"; the "one-time audit — not a live feature" caption framing and internal-QA-tooling references (raw `rec_type` tokens, `recommendations` table name, `decision_context.build_snapshot()`, "Concept E") were stripped in favor of plain-English copy and friendly labels ("New Position" / "Add to Winner" / "Buy Candidate"). Same data source (`recommendations_history.summary_stats`/`by_rec_type`, `_pac_enriched`), same computation, same page/location. | Part of the same 2026-07-22 UX audit (`docs/reviews/2026-07-22-UX-review.md`, finding I6) that flagged internal-tooling-style copy leaking into permanent user-facing surfaces — a feature that ships as a one-time audit finding can still end up rendering indefinitely on a live page, so its copy shouldn't read as disposable QA output. |
| Nav reorg — ALERTS renamed SIGNALS; Signals & Advice moved into it (Phase 1 of the Option-A visual refresh) | (2026-07-26) `_NAV_GROUPS`/`_NAV_ACCENT`/`_NAV_ICON` in `app.py` (~lines 1732–1779): the `"ALERTS"` group key/label was renamed to `"SIGNALS"` (all three dict/list keys updated together, since `_grp_key = _grp_label.split()[0]` derives the accent-color/icon lookup key from the group label's first word), and the `📡 Signals & Advice` nav item was moved out of the PORTFOLIO group into the renamed SIGNALS group, ordered first ahead of 🔔 Catalyst Watch and 📅 Economic Calendar. PORTFOLIO drops from 9 items to 8; SIGNALS grows from 2 to 3. Confirmed no other code keys off the literal `"ALERTS"` string, and the page dispatch (`elif page == "📡 Signals & Advice":`) and its nav-badge logic both match on the page-destination string rather than group membership, so neither was affected by the move. Display/nav only — no gate, threshold, or recommendation logic touched. | User-driven IA cleanup: the PORTFOLIO nav group had grown lopsided (9 items vs. 4–5 in every other group), and Signals & Advice's actual job — active portfolio alerts + rebalancing advice — is a closer conceptual fit with Catalyst Watch (forward earnings awareness) and Economic Calendar (macro event awareness) than with the holdings/analytics-heavy rest of PORTFOLIO. "ALERTS" was renamed to "SIGNALS" because it undersold the group once Signals & Advice's mix of live signals + advice joined it, not just calendar-style alerts. |
| Home Holdings table + Act/Review card color unification (Phase 2 of the Option-A visual refresh, F-01) | (2026-07-26) `app.py`, inside `if page == "🏠 Home":`. **(1)** New "💼 Holdings" table rendered after the Act Today/Monitoring/Tune-up cards: iterates `port_df` (Ticker/Shares/Price/Market Value/P&L (%)/Weight (%) — verified columns, `stock_analyzer/portfolio.py:327-350`) and joins each ticker against `st.session_state["_live_prices"]` (the same cache `fetch_live_prices()` already populates for the price strip above) for a per-position Day Δ%; a ticker absent from that cache renders "—", never a fabricated 0.00%. Custom HTML table (not `st.dataframe`) for full control of tabular-nums + per-cell gain/loss color, matching how the rest of Home already renders. Shares and Market Value are masked under privacy mode via the existing `_m()` helper; Price/Day Δ%/Total Δ%/Weight are not (percentages and per-share price were already unmasked elsewhere on Home). **(2)** Introduced five module-scope-in-closure constants (`_HOME_URGENT`/`_HOME_ELEVATED`/`_HOME_CALM`/`_HOME_GAIN`/`_HOME_LOSS`) and repointed every inline hex literal in `_fmt_action()`, `_render_act_card()`, and `_render_review_card()` to them — a mechanical, zero-visual-change refactor except one deliberate fix: `_fmt_action()`'s `TRIM_AND_TIGHTEN` branch previously returned green (`#22c55e`, the app's "gain" color) for its "→ ACT:" label, inconsistent with its sibling `TRIM_TO_TARGET`/`PROTECTIVE_TRIM` (both amber) — recolored to match. Two near-duplicate greys (`#78716c` stone vs `#94a3b8` slate, used in different cards for the same "calm/no action" meaning) were also unified to the slate value already used more widely across Home. No gate, threshold, or recommendation logic touched — border/label color and one new read-only table only. | Closes a real F-01 spec gap surfaced while reviewing Option A mockups: Home never had a persistent table of every position (only the live price-strip cards, which show ticker/price/day-change but no shares/value/weight, and a transient cold-load snapshot that disappears once the page finishes loading) — "no full holdings view on Home" was a genuine, not cosmetic, gap. The color unification was opportunistic — found while tracing the three card renderers to give them a shared palette — rather than a full app-wide WATCH/TRIM/EXIT color system (the other four independent color dicts elsewhere in the app — verdict colors, playbook colors, post-event colors, `lifecycle_badge()` — are untouched; that remains a separate, larger, explicitly out-of-scope effort). |
| New Summary page (MAIN nav group) + shared-helper hoist to true module level (F-204) | (2026-07-26) A first attempt to split Home into "Home" + "Daily Brief" (moving content out of Home) was researched but abandoned once it surfaced real entanglement — Act Today's data and its render functions are entangled with content that would have moved (Grow Today/Monitoring), and Home's preamble (the only place `build_portfolio_df()` runs and populates the session-state caches every other page depends on, confirmed via Explore agent: no other page/startup hook populates `_port_df_enriched`/`_last_held_data`/etc.) would have needed care. Pivoted to a strictly additive design instead: a new `elif page == "🧾 Summary":` page (added to `_NAV_GROUPS`'s MAIN group, `app.py` ~line 1902, right after Home) that reads what Home's preamble already publishes — `_port_df_enriched`, `_live_prices`, `_portfolio_value`, and `_home_synth_cache["bundle"]` (which already includes the raw `_daily_brief` dict, `n_danger`/`n_warning`/`div_score`/`_div_label`/`_rag_label`/`_rag_color`) — rather than duplicating or moving any of Home's own computation. Renders an 8-metric KPI row (Portfolio Value/Alerts/Diversification/RAG from the published cache; Unrealized P&L/Avg Score/Best/Worst cheaply recomputed from `port_df`; Today's P&L uses the simpler held-mark calc, not Home's fuller Tier-B, to avoid a second `daily_snapshots` round-trip), Act Today (same `_daily_brief` + the identical pure `split_defensive()` call Home uses, so recommendations can never diverge between the two pages — rendered via a new, deliberately leaner `_render_simple_action_card()`, not Home's full-featured card with its Analyze button/Exit Red-Team debate/journal context/tax notes), and the Holdings table. Enabled by hoisting `_HOME_URGENT`/`_HOME_ELEVATED`/`_HOME_CALM`/`_HOME_GAIN`/`_HOME_LOSS`, `_fmt_action()`, and the Holdings-table builder (now `_render_holdings_table()`) from Home-local closures (added in the prior row) to true module-level definitions near `_m()` — necessary because Streamlit re-executes the whole script per rerun and only the matching `if`/`elif page ==` branch runs, so a `def` textually inside Home's block simply never executes (name undefined) on any other page's run, regardless of Python's closure/scoping rules. Verified zero behavior change on Home itself: `_render_act_card()`/`_render_review_card()` still resolve the hoisted names via normal module-scope lookup, unchanged otherwise; the Holdings table's inline block was replaced with a call to the extracted function with byte-identical output. | User explicitly asked to avoid touching Home's existing cache/data-load mechanism ("since we have cache mechanism/data loads for the app to function") — this shaped the whole design toward pure addition rather than extraction/split, once the split option's real complexity (shared closures, entangled computation) was surfaced via research rather than assumed away. A static HTML mockup (matching the real sidebar structure, not an illustrative one) was reviewed and approved before any `app.py` change, per the user's request to "analyze & review before we lock down the design." |
| Holdings table removed from Home, same day (F-01 correction) | (2026-07-26, immediately after the prior row) Live review on Home showed the Holdings table now duplicated across two pages once 🧾 Summary shipped. Removed the `_render_holdings_table(port_df)` call and its spacer div from `app.py`'s Home block (right after the Act Today section) — the shared `_render_holdings_table()` function itself is untouched and still used from Summary; Home now shows no persistent holdings table again, same as before this whole Option-A pass started, deliberately. `docs/requirements.md` F-01 rewritten to point to §3.1h (F-204) rather than describe functionality that no longer renders on Home. | Two pages showing the identical table was redundant once Summary existed specifically to be the "at a glance" surface — the Holdings table belongs on exactly one page, and Summary is the more natural home for it (Home's job is the deep Today's Brief content; Summary's job is the quick reference). |
| Portfolio Snapshot RAG banner dropped from Summary (F-204 follow-up) | (2026-07-26, same day) Live review flagged the "Action Required"/"Monitor"/"All Clear" chip (Summary's copy of Home's Command Center bar, `_rag_label`/`_rag_color` read from `_home_synth_cache`) sitting directly above an "Act Today — you're set" card — a red banner and a green "nothing to do" card on the same short page reads as a flat contradiction, even though both are individually correct (the RAG label is driven by `n_danger` — danger-level Active Alerts, F-169 — a different signal from the `split_defensive()`-derived Act Today bucket, F-22, and the two can legitimately disagree). Rather than caption around the discrepancy, removed the whole banner block from `app.py`'s Summary page (the `_sm_rag_label`/`_sm_rag_color` reads and their `st.markdown` block); the page's top caption now appends the date (`Data as of {date}`) in place of the banner's position-count/date line, since the Holdings section already shows the count via its own "💼 Holdings (N)" subheader. | User's own read, confirmed on review: the RAG banner duplicated Home's identical bar with no new information, and removing it (rather than explaining the gap) was the cleaner fix for a page whose whole purpose is an at-a-glance, non-contradictory read. Matches the originally-approved Option-A mockup, which never had this banner either — only 4 clean KPI tiles. |
| Summary's Act Today cards corrected to match the approved mockup (F-204 follow-up) | (2026-07-26, same day) `_render_simple_action_card()` (`app.py:976-1008`) had drifted from the approved mockup during the initial build: it rendered as a full-width stacked list with the tier label as inline colored text on the same line as the ticker, not the rounded pill badge + ticker-on-its-own-bold-line + 3-column grid the mockup actually showed. Live review with a real mockup screenshot caught the gap. Fixed the card markup to a pill badge (icon + label, tinted background via `{_color}22` 8-digit hex alpha, matching the mockup's `rgba(...,0.14)` tint) with the ticker in `JetBrains Mono` on its own line below, and changed the Summary page's Act Today loop from a plain `for` stacking one card per row to `st.columns(3)` reused via `_sm_idx % 3` (Streamlit allows writing to the same column object more than once per rerun, so this wraps cleanly past 3 items without extra logic). | The mockup was the actually-approved design artifact; the initial build session simplified further than agreed without flagging it as a deviation. Caught only because the user attached the original mockup screenshot again to ask "would real items look like this" — a reminder to diff new UI against the approved reference image, not just against a verbal description of it. |
| Signals & Advice "Rebalancing Recommendations" renamed "Signal-Driven Actions" (F-13 UI disambiguation) | (2026-07-26) User noticed the section reads as if it duplicates Portfolio Overview's "⚖️ Rebalancing Advisor" (weight-vs-target drift). Confirmed they're unrelated features that only share a name: F-13's Rebalancer (`app.py:8477`, subheader) fires off a ticker's score/signal deterioration (evidence panel + decision checklist per card); Portfolio Overview's Rebalancing tab fires off allocation-% drift from a target weight. Renamed the on-screen subheader "💡 Rebalancing Recommendations" → "💡 Signal-Driven Actions" and the empty-state success chip's wording ("no rebalancing actions needed" → "no signal-driven actions needed", `app.py:8309`); updated the in-app User Guide's Signals & Advice bullet to name both features and point to where each lives. F-13's internal/gate-table name ("Rebalancer" — G-02/G-03/G-09, F-12, OP-05 in `docs/requirements.md`) intentionally left unchanged — only the user-facing copy moved. Text-only change, no logic/gate/threshold touched. | Two features sharing the word "Rebalancing" was the actual source of the user's confusion, not a misplaced feature — moving either feature onto the other's page would have stacked two differently-behaved "Rebalancing" sections on one page, a harder confusion than the original naming collision. A rename was the targeted fix. |
| Home unified alert stack (2026-08-04 UX audit I1) | Home's 4 independently-coded warning banners (Day Shock, Price cross-check disagreement, Stock Split Detected, Structural correlation-cluster alert) rendered at 4 different points in the script with no visual grouping — Structural alert's compute sits ~1700 lines after the other three because it depends on `corr_df` from a synthesis block that only converges later (a pre-existing comment warns that moving the compute earlier would read a stale value). Fixed by reserving 4 `st.empty()` placeholders together, right after `_price_strip(held_tickers)`, in final visual order (`_alert_ph_dayshock`/`_alert_ph_xcheck`/`_alert_ph_split`/`_alert_ph_structural`); each banner's existing render code, unmoved at its original compute position, was wrapped in `with _alert_ph_X.container():` instead of writing directly to the page. Streamlit renders `st.empty()` content at the reserved slot, not the fill-in call site, so all 4 now visually group right after the price strip while every banner's compute timing, caching, and data-dependency ordering is unchanged — only the render target moved. Built via the same line-slice/reassemble-script + independent content-preservation-diff method as the "Home page tab consolidation" row above (5 diff hunks, all intentional insertions, zero unexpected differences across ~27,200 normalized lines). One real bug caught during manual verification and fixed before commit: the script's insert used a uniform 4-space indent for all four `with` headers, but the Price-cross-check block sits one level deeper (nested inside `if held_tickers:`, 8-space) than the other three (page-top-level, 4-space) — the wrong indent would have dedented the `with` out of its guard, risking a `NameError` on an empty portfolio. Opus-reviewed SHIP, 0 blocking (independently re-derived the fix and confirmed no other banner has the same nesting mismatch). Home's 2026-07-13 decision to de-tab Evening Debrief/AI Snapshot back into plain sections (row above) was revisited and explicitly reaffirmed, not reversed — those 2 sections are ~680 lines combined vs. Today's Brief's ~5160, so re-tabbing them wouldn't address the actual scroll-length problem. | Per the 2026-08-04 UX audit (finding I1, `docs/reviews/2026-08-04-UX-review.md`): up to 4 independently-styled banners could stack above the Command Center on one load with no unified treatment. A visual grouping fix, not a data or gate change — all 4 banners remain awareness-only. |

---

## 11. External API Dependencies

| API | Purpose | Rate Limits | Failure Handling |
|-----|---------|-------------|-----------------|
| Yahoo Finance (yfinance) | History/bundle primary (OHLCV, company info, news, analyst data, earnings); indices; futures; global indices; live-price failover | Informal; 429 responses possible | Retry with linear backoff (3 attempts, 3s base); `api_health` records events; **a hard failure now fails over to FMP** (history/bundle) |
| Finnhub (REST, free) | **Real-time live-price primary**; price cross-check source; **news-sentiment read** (`/stock/news-sentiment` → bullish%/buzz/sector-avg) for the F-74 awareness surfaces | 60 calls/min (free) | Per-symbol; rate-limit/error skips that ticker → gap-fill to yfinance; news-sentiment returns `None` on any error (awareness-only, never blocks); `api_health("finnhub")` |
| FMP / Financial Modeling Prep (REST, free, `/stable/`) | Failover for live prices, history, and full analysis bundle (profile/ratios/growth/targets/news/earnings/grades) | 250 calls/day (free) | Only invoked when higher-priority providers fail; key redacted from logged errors; `api_health("fmp")` |
| Supabase REST API | Holdings, watchlist, trades, manual_stops CRUD | Generous free tier | Connection errors surface as UI warnings |
| Anthropic / OpenAI / Google | AI Snapshot generation | Per-account | Errors surfaced in AI Snapshot tab; rest of app unaffected |
| FRED (St. Louis Fed) | Economic-calendar actuals + release-drift dates | 120 req/min (free key) | `api_health("fred")`; macro calendar degrades to static backbone without a key |
| US Treasury / Yahoo `^IRX` | 13-week T-bill rate for risk-free rate | Daily cached | Falls back to 4.5% if unavailable |

No single market-data source is now a hard dependency for prices or the analysis bundle. yfinance has no official SLA, so its calls are wrapped in `_retry()` (transient 429s) and a hard failure fails over to FMP; live prices come from Finnhub (real-time) first with gap-fill to yfinance/FMP. Keyed providers (Finnhub/FMP/FRED) are skipped silently when their key is absent. The price cross-check (§4.0.4) surfaces source disagreement loudly. Pre-market `fast_info` calls in `premarket.py` are not retried (best-effort).

---

## 12. AI Intelligence Layer

The AI layer is **strictly additive**: every other page, every gate, every protection works identically whether or not the Anthropic API is reachable. A missing key or timeout returns `None`; callers render a graceful banner and move on.

### 12.1 Architecture overview

```mermaid
flowchart LR
    subgraph ENGINE["① Rules Engine  (always runs)"]
        direction TB
        SRC[("Market data\n& portfolio")]
        SCORE["Scoring · Gates\nRecommendations"]
        SRC --> SCORE
    end

    PACK["② Evidence package\nPython pre-computes:\nP&L · alpha · signals\nnews · technicals"]

    subgraph LLM_LAYER["③ AI Intelligence Layer  (additive)"]
        direction TB
        CLAUDE(["Claude AI\nSonnet 4.6 / Haiku"])
        F1["F-1 Thesis Review  ·  INTACT / WEAKENING / BROKEN"]
        F5["F-5 Thesis Draft  ·  editable BUY-time draft"]
        F3["F-3 Weekly Debrief  ·  4-section narrative"]
        F4["F-4 Monthly Report  ·  entry quality + signal discipline"]
        PM["Pre-market Stance  ·  Defensive / Neutral / Constructive"]
        CLAUDE --> F1 & F5 & F3 & F4 & PM
    end

    SCORE -->|"pre-computed\nfacts only"| PACK
    PACK  -->|"one-shot prompt"| CLAUDE

    NOLOOP(["⚠ AI never feeds back\nto the engine.\nApp works without AI."])
    LLM_LAYER -.-> NOLOOP
```

**Key design decisions:**

- **One-shot prompts only.** No multi-turn, no RAG, no tool use. Python assembles a fully structured evidence package; the LLM narrates it.
- **Evidence first, LLM last.** All computation (P&L, alpha, composite scores, band breakdowns) happens in Python before the prompt is constructed. The LLM describes numbers; it never computes them.
- **Thresholds interpolated at call time.** The F-4 system prompt imports `COMPOSITE_BUY` from `constants.py` at invocation, so any policy change propagates to the prompt immediately — no stale hardcoded values.
- **`bundle_evidence()` is the single extractor.** Both F-1 (review) and F-5 (authoring) read evidence through the same shared function (`thesis_advisor.bundle_evidence`), preventing drift between what the reviewer and the drafter see.
- **30-second timeout on all core features** (`LLM_REQUEST_TIMEOUT_SEC` in `constants.py`). Any exception returns `None`.
- **No retries.** Fail fast, show a banner, let the user retry manually.

### 12.2 Feature table

| ID | Feature | File | Model | Trigger | Max output | Result cached |
|----|---------|------|-------|---------|------------|---------------|
| F-1 | Thesis Review | `thesis_advisor.py` | Sonnet 4.6 | On-demand button + Sunday cron | 300 tok | DB `thesis_reviews` |
| F-5 | Thesis Draft | `thesis_advisor.py` | Sonnet 4.6 | On-demand (BUY form) | 300 tok | Editable field only (not auto-saved) |
| F-3 | Weekly Debrief | `debrief_advisor.py` | Sonnet 4.6 | On-demand + Sunday cron | 700 tok | DB `weekly_debriefs` |
| F-4 | Monthly Report | `intelligence_report.py` | Sonnet 4.6 | On-demand + 1st-Sunday cron | 1000 tok | DB `monthly_reports` (frozen `viz_json`) |
| F-6 | Analyst Coverage extract | `analyst_intel.py` | Sonnet 4.6 | On-demand (paste + Extract) | 1500 tok | DB `analyst_coverage` (after editable-preview save) |
| F-174 | Pre-Earnings Playbook extract | `earnings_intel.py` | Sonnet 4.6 | On-demand (paste + Extract) | `ANALYST_EXTRACT_MAX_TOKENS` (8000 tok, shared budget) | DB `earnings_context` (after editable-preview save) |
| F-176 | F-1 Earnings Thesis Checkpoint | `thesis_advisor.py` | Sonnet 4.6 | On-demand (Positions tab CTA, gated on a recent `earnings_results` row) | 300 tok | DB `thesis_reviews` |
| F-197 | Multi-Agent Debate — entry (Phase 1) | `debate_agent.py` | Haiku (5 sequential calls/run) | On-demand (⚔️ Debate button, Grow Today candidate card) | 200 tok × 4 rounds + 300 tok judge | DB `debate_cache` (`debate_type='entry'`) |
| F-197 | Multi-Agent Debate — exit "Challenge This Exit" (Phase 2, D2) | `debate_agent.py` | Haiku (5 sequential calls/run) | On-demand (⚔️ Challenge This Exit button, deterioration TRIM/EXIT card) | 200 tok × 4 rounds + 300 tok judge | DB `debate_cache` (`debate_type='exit'`) |
| F-198 | Structural Scan narrative (Phase 1) | `structural_scanner.py` | Haiku (1 call/day) | On-demand ("🧬 Generate structural narrative" button, 🧩 Intelligence tab) | 300 tok | DB `structural_scan_cache` |
| F-199 | Hidden Same-Bet Detector (D1) | `thesis_cluster.py` | Haiku (1 call/day) | On-demand ("🧠 Check for hidden shared bets" button, 🧩 Intelligence → 🧬 Structural Scan tab) | 600 tok | DB `thesis_cluster_cache` |
| F-201 | Missed-Opportunity Pattern (O1) | `missed_opportunity.py` | Haiku (1 call/day) | On-demand ("🔍 Look for a pattern" button, 📊 Recommendations History → Summary tab) | 600 tok | DB `missed_opportunity_cache` |
| F-202 | Signal Coherence Auditor (D3) | `signal_reconciliation.py` (`classify_composite_direction`), `db.py` (`load_debate_verdicts`) | **None — zero LLM calls** | Live on every render, 🧩 Intelligence → 🧭 Signal Coherence tab | 0 tok | None — no cache table (pure Python join, computed fresh each render) |
| F-203 | Watchlist Resurrection (O4) | `db.py` (`load_watchlist_added_dates`) | **None — zero LLM calls** | Live on every render, 👁️ Watchlist page (highlight over existing cards) | 0 tok | None — no cache table (pure Python predicate, computed fresh each render) |
| F-200 | Regime-Aware Adversarial Scenario (Phase 1) | `regime_stress.py` | Haiku (1 call/day) | On-demand ("🎯 Generate regime-aware scenario" button, 🔗 Risk Analysis → 🔥 Stress Testing tab) | 400 tok | DB `regime_scenario_cache` |
| — | Pre-market Stance | `premarket_stance.py` | Haiku | Manual refresh button | 500 tok | Session state, keyed by trading date |
| — | AI Monitoring Brief | `app.py` | Sonnet or Haiku (user pick) | Manual button | 700 tok | Session state, keyed by (provider, model) |
| — | VADER rescorer (`rescore_news_items_llm`) | `news_intelligence.py` | Haiku | Home load + Analysis page (automatic, per held ticker set) | Small JSON list | Session, keyed by day + sorted-ticker-set. Suppress-only: can only raise a VADER compound score, never lower; removes false-positive negatives from financial news. temperature=0, 8s timeout, VADER fallback on any failure. Never creates a new Act Today card or flips a buy-candidate verdict. |

### 12.3 Prompt construction pattern

```
DB / session data
  → advisor's build_package() function
  → System prompt  (static role + output format rules)
  → User prompt    (pre-aggregated evidence — 300–1600 tokens)
  → client.messages.create()   [synchronous, no streaming]
  → parse response text → structured dict
  → save to DB (F-1 / F-3 / F-4) or return to UI
```

### 12.4 Token budget (approximate per call)

| Feature | Input tokens | Output tokens | Notes |
|---------|-------------|---------------|-------|
| F-1 Thesis Review | ~600 | ~150 | Technical + fundamentals + 12 headlines |
| F-5 Thesis Draft | ~750 | ~150 | Engine verdict + catalyst + regime added |
| F-3 Weekly Debrief | ~1 000 | ~300 | Portfolio-vs-SPY + contributors + entry recs surfaced (+ conviction tier) + protective WATCH/TRIM/EXIT signals (2026-08-10, `exit_signals`) |
| F-4 Monthly Report | ~1 600 | ~400 | Band/verdict breakdowns; largest prompt |
| Pre-market Stance | ~600 | ~200 | Futures + macro regime + top 5 holdings |

At the current weekly/monthly call frequency the total annual API cost is approximately **$1–2 USD** (Sonnet 4.6: $3/M input, $15/M output; Haiku: $0.80/M input, $4/M output). Prompt caching would save ≈15–30% on system-prompt tokens but is not implemented — revisit if call volume grows 10×.

### 12.5 Secrets and graceful degradation

API key resolution order: `st.secrets["anthropic"]["api_key"]` → `ANTHROPIC_API_KEY` env var → GitHub Actions secret (cron path).

| Condition | Effect |
|-----------|--------|
| No API key | "AI drafting unavailable" caption; feature buttons disabled |
| Timeout / API error | Returns `None`; caller shows banner; rest of app unaffected |
| Read-only viewer mode | Thesis-draft button disabled at UI layer |

### 12.6 Cron lanes (Railway Cron Job services)

Since 2026-08-07 the scheduled lanes run as dedicated Railway **Cron Job** services in the
same project as the web service, each running `python cron_runner.py` with its own schedule
and `ALERT_RUN_MODE` (migrated off GitHub Actions, whose `schedule` trigger is best-effort;
`.github/workflows/alerts.yml` is now `workflow_dispatch`-only). Per-service cron
expressions live in the Railway dashboard — see memory `project_cron_railway_migration`.

`cron_runner.main()` resolves the lane from `ALERT_RUN_MODE`, falling back to an ET-hour
inference (`< 12` ⇒ `premarket`, else `eod`) when that variable is unset or unrecognised.
What actually prevents a schedule change from firing the wrong lane is the explicit
`ALERT_RUN_MODE` per service plus each lane's own in-code guard (the weekday and ET-hour
checks in the table above) — the hour-based fallback is the loose path, not the safeguard.
Each lane records a `cron_heartbeat` row (§6.32), graded on 🩺 System Trust; a lane that
reports failure by returning non-zero (rather than raising) still records
`status="failed"`, so a green heartbeat can never mask a failed run.

| Lane | Service | Schedule (UTC) | In-code guard | Features invoked |
|------|---------|----------------|---------------|-----------------|
| `premarket` | `cron-premarket` | `0 12,13 * * *` | trading day + ET hour ≥ `ALERT_EMAIL_HOUR_ET` | Protective exit alerts (F-143) |
| `scan` | `cron-scan` | `45 13,14 * * *` | trading day | Morning buy-list scan |
| `intraday` | `cron-intraday` | `30 15,16 * * 1-5` | trading day | Intraday pullback entry check |
| `eod` | `cron-eod` | `30 21 * * 1-5` | trading day + ET hour ≥ `ALERT_EOD_HOUR_ET` | EOD snapshot, pullback alert, vol-prediction write + maturation |
| `thesis` | `cron-thesis` | `0 23 * * 0` | Sunday | F-1 thesis review → F-3 weekly debrief → F-4 monthly report (first Sunday) |
| `maintenance` | `cron-maintenance` ⚠️ | *not recorded here* | Saturday | Idempotent data backfills (see below) |

**The schedule column is a MIRROR of the Railway dashboard, which is the source of truth**
(these services are dashboard-managed, not repo-managed — see the ⚠️ note below). Read the
live expression before changing one; do not edit a service to match this table.

**Why the weekday lanes carry a comma list in the HOUR field.** A single cron expression
cannot hit a fixed ET time across a DST boundary, and Railway has no timezone field. Two UTC
slots cover both seasons and each lane's own ET gate discards the wrong one — `cron-premarket`
runs 08:00 ET / dedups 09:00 in EDT, and self-skips 07:00 (`7 < ALERT_EMAIL_HOUR_ET`) / runs
08:00 in EST. **Expect two container starts per lane per trading morning**, the second logging
a dedup line; this is correct, and restores the pre-migration behaviour. The 2026-08-07
GitHub-Actions migration collapsed each lane to one slot and kept the *winter* expression, so
`premarket`/`scan`/`intraday` ran an hour late for the whole EDT season until 2026-08-23. It
was silent because **every gate is lower-bound-only: the gates can tell you a run was not
early, never that it was late.** `cron-eod` was unaffected and was deliberately left on a
single slot — 21:30 UTC clears `ALERT_EOD_HOUR_ET = 16` in both offsets, because its gate
boundary sits far enough from its schedule that a one-hour shift cannot cross it. Memory
`project_cron_railway_migration`.

⚠️ Railway cron services are **dashboard-managed, not repo-managed**. `cron-maintenance`
must be created manually (`ALERT_RUN_MODE=maintenance`, Saturday schedule, inheriting the
Shared Variables) before this lane ever fires. Until then the code is inert and the lane
reads ⚪ "unknown" on 🩺 System Trust — never red, since a lane with no heartbeat row yet
is not treated as a fault.

The `thesis` / `debrief` / `monthly` AI lanes are inert until `ANTHROPIC_API_KEY` is set.

**`maintenance` lane (added at the 2026-08-15 Railway cutover).** The cutover removed the
only practical shell for one-off maintenance scripts: Railway's Console is *not* the app's
environment (minimal `PATH`, no app dependencies, unset `LD_LIBRARY_PATH` → numpy fails on
`libz.so.1`), and the Streamlit Cloud terminal the backfill scripts were written for is now
a dormant fallback. Rather than depend on a shell, the backfills became a lane:

1. `scripts/backfill_analyst_prices.run_backfill()` — fills `analyst_coverage.price_at_article_date`.
   Self-limiting: only queries NULL rows, so it costs one cheap query once caught up.
2. `scripts/backfill_vol_predictions.run_backfill(skip_existing=True)` — fills historical
   `model_predictions` rows for held tickers. `db.has_backfilled_predictions()` skips
   tickers already done, so a recurring run only works on holdings added since the last
   tick instead of re-fetching 5y of history weekly. That helper returns `None` when the
   check itself can't run (DB offline / pre-DDL), which the caller treats as
   **"unknown → back it up anyway"**: a redundant backfill is cheap-and-safe (see the
   idempotency note) and merely costs provider calls, whereas wrongly skipping leaves a
   permanent hole in the ledger.

**Idempotency, precisely.** The upsert key is `(model_name, model_version, scope, ticker,
made_at)`, and `made_at` is derived from a *rolling* `PREDICTION_BACKFILL_PERIOD` ("5y")
window. So a same-day re-fire or retry fetches an identical window → identical `made_at`
keys → a true upsert with no duplicates. A re-run weeks later against a rolled window
samples *shifted* `as_of` points, which **adds** rows rather than replacing them. The
routine `skip_existing=True` lane path never hits this; only a deliberate manual
`skip_existing=False` redo can.

**Known limitation — the "already done" check is presence-only.** `has_backfilled_predictions`
is satisfied by a single row. If a ticker's first backfill ran against a degraded provider
that returned less than the full period, the thin result still marks it done permanently,
and the lane won't self-heal. Remedy is a manual `run_backfill(skip_existing=False)` for
that ticker. Accepted for now: the failure needs a provider degradation on exactly the
first run for a newly-bought ticker, and the data is measurement-only.

Both jobs are isolated so one's failure can't suppress the other, and a failure fires the
same `_notify_failure` dead-man's-switch email as every other lane. MEASUREMENT-ONLY —
neither job feeds a gate, a recommendation, or the composite score.
