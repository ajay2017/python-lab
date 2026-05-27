import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date
import pytz as _pytz

# All date comparisons use ET so the calendar never flips at midnight UTC
_TODAY_ET = datetime.now(_pytz.timezone("America/New_York")).date()

import html as _html
from stock_analyzer.data import (
    DEFAULT_TICKERS, fetch_ticker_bundle, fetch_financials_from_info,
    fetch_spy, fetch_live_prices, fetch_market_indices, market_status,
    curate_news_items, fetch_price_history, fetch_risk_free_rate,
)
from stock_analyzer.technicals import compute_indicators, technical_score
from stock_analyzer.fundamentals import fundamental_score, upside_potential
from stock_analyzer.sentiment import analyze_news, sentiment_score_0_100
from stock_analyzer.scoring import combined_score, recommendation
from stock_analyzer.risk import atr_stop_loss, position_sizing, compute_all_risk, compute_portfolio_risk_metrics
from stock_analyzer.risk_advisor import build_risk_advisor_recommendations
from stock_analyzer.perf_advisor import compute_attribution, build_perf_recommendations
from stock_analyzer.earnings_advisor import build_earnings_playbook
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation
from stock_analyzer.trade_analytics import build_full_analytics
from stock_analyzer.stress_test import SCENARIOS, run_scenario, run_all_scenarios
from stock_analyzer.rebalancer import (
    equal_weights, compute_drift, build_rebalance_plan,
    TOLERANCE_OK, TOLERANCE_WATCH,
)
from stock_analyzer.constants import (
    PORTFOLIO_BETA_CEILING,
    PORTFOLIO_BETA_ELEVATED,
    TICKER_BETA_HIGH,
    TICKER_BETA_CRITICAL,
    SECTOR_CEILING,
    SINGLE_NAME_CEILING,
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    MACRO_IMMINENT_DAYS,
)
from stock_analyzer.sentiment_velocity import build_sentiment_dashboard
from stock_analyzer.tax_advisor import build_tax_analysis
from stock_analyzer.split_detector import detect_portfolio_splits
from stock_analyzer.macro_calendar import (
    build_macro_calendar,
    detect_macro_regime as detect_macro_regime_fred,
    HIGH as MC_HIGH, MEDIUM as MC_MEDIUM, _REGIME_NOTES as _MC_REGIME_NOTES,
)
from stock_analyzer.macro_playbook import (
    build_event_playbooks, classify_scenario,
    build_post_event_analysis, get_scenario_conditions,
    _SCENARIO_THRESHOLDS as _MC_THRESHOLDS, _parse_number as _mc_parse_number,
)
from stock_analyzer.targets import (
    support_resistance, entry_zone, compute_price_targets, risk_reward,
)
from stock_analyzer.portfolio import (
    build_portfolio_df, sector_exposure, alerts, rebalance_actions,
    correlation_matrix, diversification_score, diversification_recommendations,
    holding_returns, relative_strength_table, SECTOR_ETF, TICKER_SECTORS,
)
from stock_analyzer.scanner import SECTOR_UNIVERSE, scan_sectors
from stock_analyzer.macro import (
    RATE_SENSITIVITY, REGIME_FAVORED, detect_macro_regime, portfolio_macro_exposure,
)
from stock_analyzer.ranking import rank_holdings_in_universe, sector_alternatives, tier_label
from stock_analyzer.trades import performance_stats, compute_realized_pnl
from stock_analyzer import db
from stock_analyzer import api_health as _ah
from stock_analyzer.news_intelligence import build_news_intelligence
from stock_analyzer.daily_briefing import build_daily_briefing
from stock_analyzer.evening_debrief import build_evening_debrief
from stock_analyzer.trade_review import (
    build_trade_review, build_insights, build_recommendations,
    cumulative_pnl_series, rolling_win_rate,
    position_size_discipline, sector_mix,
)
from stock_analyzer.signal_reconciliation import reconcile_signals, lookup_composite
from stock_analyzer.comparison import build_comparison
from stock_analyzer.premarket import build_premarket_brief, is_premarket
from stock_analyzer.premarket_stance import (
    assemble_inputs as pms_assemble_inputs,
    generate_stance as pms_generate_stance,
)
from stock_analyzer.quick_research import research_ticker as _qr_research
from stock_analyzer.decision_journal import compute_patterns

# Brand: DRISHTA (Sanskrit for "vision/insight") — "Beyond Noise".
# page_icon falls back to an emoji if the logo file isn't deployed yet so
# the app continues to render in either state.
_BRAND_LOGO_PATH = "assets/drishta_logo.png"
_BRAND_PAGE_ICON = _BRAND_LOGO_PATH if os.path.exists(_BRAND_LOGO_PATH) else "👁"
st.set_page_config(page_title="DRISHTA · Beyond Noise", page_icon=_BRAND_PAGE_ICON, layout="wide")


@st.cache_data(show_spinner=False)
def _brand_logo_data_uri_cached(mtime: float) -> str | None:
    """Cached by file mtime so replacing the logo PNG auto-invalidates."""
    if not os.path.exists(_BRAND_LOGO_PATH):
        return None
    try:
        import base64 as _b64
        with open(_BRAND_LOGO_PATH, "rb") as _f:
            return "data:image/png;base64," + _b64.b64encode(_f.read()).decode()
    except Exception:
        return None


def _brand_logo_data_uri() -> str | None:
    if not os.path.exists(_BRAND_LOGO_PATH):
        return None
    return _brand_logo_data_uri_cached(os.path.getmtime(_BRAND_LOGO_PATH))


def _render_brand(large: bool = False) -> None:
    """Render the centered DRISHTA brand image.
    The logo PNG is a single combined asset (mark + wordmark + tagline) so this
    function just renders that one image — no separate HTML text. Falls back
    to a text block if the file is missing."""
    _uri     = _brand_logo_data_uri()
    _img_w   = 300 if large else 220
    _padding = "20px 0 16px 0" if large else "10px 0 12px 0"

    if _uri:
        st.markdown(
            f"<div style='text-align:center; padding:{_padding}'>"
            f"<img src='{_uri}' width='{_img_w}' "
            f"style='display:block; margin:0 auto; max-width:100%; height:auto'>"
            f"</div>",
            unsafe_allow_html=True,
        )
        return

    # Fallback: combined-image not deployed yet → text-only brand block
    _name_size = "2.0em" if large else "1.35em"
    _tag_size  = "0.82em" if large else "0.66em"
    st.markdown(
        f"<div style='text-align:center; padding:{_padding}'>"
        f"<div style='font-size:3em; line-height:1'>👁</div>"
        f"<div style='font-size:{_name_size}; font-weight:700; "
        f"letter-spacing:0.20em; color:#f9fafb; margin:10px 0 3px 0'>DRISHTA</div>"
        f"<div style='font-size:{_tag_size}; color:#9ca3af; "
        f"letter-spacing:0.28em; text-transform:uppercase; font-weight:500'>"
        f"Beyond Noise</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


# Re-exported from constants module so existing callers continue to work; new
# code should import RISK_PCT_PER_TRADE directly from stock_analyzer.constants.
from stock_analyzer.constants import RISK_PCT_PER_TRADE as MODERATE_RISK_PCT


def _time_ago(ts: int) -> str:
    if not ts:
        return ""
    try:
        delta = int((datetime.now() - datetime.fromtimestamp(ts)).total_seconds())
    except Exception:
        return ""
    if delta < 3600:
        return f"{max(delta // 60, 1)}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"


# ── Glossary tooltips ─────────────────────────────────────────────────────────
_TIPS = {
    "P/E Ratio": (
        "Price ÷ trailing 12-month earnings per share.\n\n"
        "• < 15 → Value territory\n• 15–25 → Fair value\n• > 40 → Growth premium\n\n"
        "⚠️ Can be distorted by one-time items. Always pair with FCF Yield.\n\n"
        "Learn more: investopedia.com/terms/p/price-earningsratio.asp"
    ),
    "Forward P/E": (
        "Price ÷ next-year estimated earnings. More useful than trailing P/E "
        "for growth stocks because it reflects where the business is going, "
        "not where it's been.\n\n"
        "A falling Forward P/E (earnings growing faster than price) is bullish.\n\n"
        "Learn more: investopedia.com/terms/f/forwardpe.asp"
    ),
    "FCF Yield": (
        "Free Cash Flow ÷ Market Cap × 100.\n\n"
        "The primary valuation metric used by Institutional and most top-tier "
        "analysts — harder to manipulate than P/E because cash in the bank "
        "is real.\n\n"
        "• > 5% → Excellent: strong real-cash generation\n"
        "• 3–5% → Good value\n"
        "• 1–3% → Modest / fairly priced\n"
        "• < 1% → Expensive or cash-light model\n"
        "• Negative → Company is burning cash\n\n"
        "Learn more: investopedia.com/terms/f/freecashflowyield.asp"
    ),
    "EPS": (
        "Earnings Per Share — net profit divided by shares outstanding.\n\n"
        "Watch the *trend*: rising EPS quarter over quarter is more important "
        "than the absolute number. Negative EPS = the company is losing money.\n\n"
        "Learn more: investopedia.com/terms/e/eps.asp"
    ),
    "Revenue Growth": (
        "Year-over-year change in total revenue.\n\n"
        "• > 20% → High growth\n• 10–20% → Healthy\n"
        "• 0–10% → Slow\n• Negative → Declining business\n\n"
        "Growth stocks command premium P/E multiples only if revenue "
        "growth is accelerating, not decelerating.\n\n"
        "Learn more: investopedia.com/terms/r/revenue.asp"
    ),
    "Earnings Growth": (
        "Year-over-year change in net income / EPS.\n\n"
        "One of the strongest single alpha factors in institutional investing: "
        "stocks with upward earnings revisions consistently outperform.\n\n"
        "• Accelerating growth → multiple expansion\n"
        "• Decelerating growth → multiple compression, even if still positive\n\n"
        "Learn more: investopedia.com/terms/e/earningsgrowth.asp"
    ),
    "Profit Margin": (
        "Net income ÷ revenue. Shows how much of each dollar of sales "
        "becomes profit.\n\n"
        "• > 25% → Exceptional (software, luxury goods)\n"
        "• 15–25% → Strong\n• 5–15% → Normal for most industries\n"
        "• < 5% → Thin — vulnerable to cost shocks\n\n"
        "Margins expanding quarter-over-quarter = operating leverage kicking in.\n\n"
        "Learn more: investopedia.com/terms/p/profitmargin.asp"
    ),
    "Debt/Equity": (
        "Total debt ÷ shareholder equity (expressed as %).\n\n"
        "Measures financial risk / leverage.\n\n"
        "• < 30% → Conservative, very safe\n"
        "• 30–80% → Manageable\n"
        "• 80–150% → Elevated — watch interest coverage\n"
        "• > 150% → High leverage — vulnerable to rate rises\n\n"
        "Context matters: utilities and banks run high D/E by design; "
        "tech companies should have low D/E.\n\n"
        "Learn more: investopedia.com/terms/d/debtequityratio.asp"
    ),
    "ROE": (
        "Return on Equity — net income ÷ shareholders' equity.\n\n"
        "Warren Buffett's favourite metric. Shows how efficiently management "
        "uses investors' capital to generate profit.\n\n"
        "• > 20% → Excellent\n• 10–20% → Good\n• < 10% → Below average\n\n"
        "Learn more: investopedia.com/terms/r/returnonequity.asp"
    ),
    "Short Interest": (
        "% of the float (publicly tradeable shares) that is currently sold short "
        "by investors betting the stock will fall.\n\n"
        "• < 5% → Normal, minimal bearish pressure\n"
        "• 5–15% → Elevated — meaningful bearish conviction\n"
        "• > 15% → Very high — either a strong bear thesis OR a short-squeeze setup\n"
        "• > 25% → Extreme — GameStop/AMC territory\n\n"
        "High short interest + improving fundamentals = explosive squeeze potential.\n\n"
        "Learn more: investopedia.com/terms/s/shortinterest.asp"
    ),
    "Days to Cover": (
        "Short interest ÷ average daily volume.\n\n"
        "How many trading days it would take all short sellers to buy back "
        "their borrowed shares at current volume.\n\n"
        "• < 3 days → Easy to cover, low squeeze risk\n"
        "• 3–7 days → Moderate squeeze potential\n"
        "• > 7 days → High squeeze potential — a positive catalyst could trigger a cascade\n\n"
        "Learn more: investopedia.com/terms/d/daystocover.asp"
    ),
    "Institutional Ownership": (
        "% of shares held by hedge funds, mutual funds, pension funds, "
        "and other institutional investors.\n\n"
        "• > 70% → Heavily institutionalised — large-cap blue chip validation\n"
        "• 40–70% → Normal for mid/large cap\n"
        "• < 20% → Low institutional interest — either undiscovered early-stage "
        "OR institutions have deliberately avoided it (red flag)\n\n"
        "Rising institutional ownership = 'smart money' is accumulating.\n\n"
        "Learn more: investopedia.com/terms/i/institutionalinvestor.asp"
    ),
    "Insider Ownership": (
        "% of shares held by company executives and directors.\n\n"
        "High insider ownership aligns management incentives with shareholders. "
        "Insider *buying* in the open market is one of the strongest fundamental "
        "signals — they only buy if they believe the stock is undervalued.\n\n"
        "Insider *selling* is noise (tax, diversification). Insider *buying* is signal.\n\n"
        "Learn more: investopedia.com/terms/i/insidertrading.asp"
    ),
    "Analyst Revisions": (
        "Net count of analyst upgrades minus downgrades over the last 90 days.\n\n"
        "Earnings revision momentum is one of the most reliable alpha factors "
        "in quantitative investing: stocks where analysts are raising estimates "
        "consistently outperform those where estimates are being cut.\n\n"
        "• Positive net → Analysts getting more bullish → tailwind\n"
        "• Negative net → Analysts losing conviction → headwind\n\n"
        "Learn more: investopedia.com/terms/e/earningsrevision.asp"
    ),
    "Analyst Consensus": (
        "Average analyst recommendation on a scale of 1–5.\n\n"
        "• 1.0–1.5 → Strong Buy consensus\n"
        "• 1.5–2.5 → Buy consensus\n"
        "• 2.5–3.5 → Hold consensus\n"
        "• 3.5–4.5 → Sell consensus\n"
        "• 4.5–5.0 → Strong Sell consensus\n\n"
        "Used with caution — analysts at the firms doing banking relationships "
        "are structurally biased toward Buy ratings."
    ),
    "Sharpe Ratio": (
        "Return above the risk-free rate ÷ standard deviation of returns.\n\n"
        "Answers: 'How much return am I getting per unit of risk taken?'\n\n"
        "• > 1.5 → Excellent risk-adjusted performance\n"
        "• 1.0–1.5 → Good\n• 0.5–1.0 → Acceptable\n"
        "• < 0.5 → Poor — you're not being compensated for the risk\n"
        "• Negative → Worse than holding cash\n\n"
        "Learn more: investopedia.com/terms/s/sharperatio.asp"
    ),
    "Sortino Ratio": (
        "Like the Sharpe Ratio but only penalises *downside* volatility "
        "(upside volatility is not a risk — it's a reward).\n\n"
        "• > 2.0 → Excellent\n• 1.0–2.0 → Good\n• < 1.0 → Weak\n\n"
        "Preferred by professional risk managers over Sharpe because "
        "investors only care about losing money, not about upside swings.\n\n"
        "Learn more: investopedia.com/terms/s/sortinoratio.asp"
    ),
    "Max Drawdown": (
        "The worst peak-to-trough decline in the selected period.\n\n"
        "If a stock went from $100 → $60 → $80, the max drawdown is −40%.\n\n"
        "Professional portfolio managers use this to assess 'pain tolerance': "
        "could you hold through this without panic-selling?\n\n"
        "• > −20% → Significant drawdown, high volatility\n"
        "• −10% to −20% → Normal for growth stocks\n"
        "• < −10% → Relatively stable\n\n"
        "Learn more: investopedia.com/terms/m/maximum-drawdown-mdd.asp"
    ),
    "VaR": (
        "Value at Risk (95% confidence) — the daily loss you should NOT "
        "expect to exceed on 95% of trading days.\n\n"
        "If VaR = −2.5%, it means on a normal day you won't lose more than 2.5%. "
        "But on the worst 5% of days, losses can be larger.\n\n"
        "Limitations: VaR understates risk during market crises (fat-tail events). "
        "That's why professionals also look at CVaR (Expected Shortfall).\n\n"
        "Learn more: investopedia.com/terms/v/var.asp"
    ),
    "Beta": (
        "Measures a stock's volatility relative to the S&P 500.\n\n"
        "• Beta = 1.0 → Moves exactly with the market\n"
        "• Beta = 1.5 → 50% more volatile than the market\n"
        "• Beta = 0.5 → Half the market volatility\n"
        "• Beta < 0 → Moves opposite to the market (rare)\n\n"
        "High-beta stocks amplify both gains and losses. "
        "In bull markets, β > 1 outperforms. In bear markets, it destroys capital.\n\n"
        "Learn more: investopedia.com/terms/b/beta.asp"
    ),
    "RSI": (
        "Relative Strength Index (14-period).\n\n"
        "Momentum oscillator measuring the speed and magnitude of recent price moves.\n\n"
        "• > 70 → Overbought — potential pullback ahead\n"
        "• 50–70 → Bullish momentum territory\n"
        "• 30–50 → Neutral to mildly bearish\n"
        "• < 30 → Oversold — potential bounce\n\n"
        "RSI divergence (price making new highs but RSI declining) is a "
        "leading warning signal used by professional chartists.\n\n"
        "Learn more: investopedia.com/terms/r/rsi.asp"
    ),
    "MACD": (
        "Moving Average Convergence Divergence.\n\n"
        "Trend-following momentum indicator. The MACD line is the difference "
        "between the 12-day and 26-day exponential moving averages.\n\n"
        "• MACD above signal line → Bullish\n"
        "• MACD below signal line → Bearish\n"
        "• Histogram rising → Momentum strengthening\n"
        "• Histogram falling → Momentum weakening\n\n"
        "Learn more: investopedia.com/terms/m/macd.asp"
    ),
    "ATR Stop": (
        "Average True Range stop loss — a volatility-adjusted exit level.\n\n"
        "ATR measures the average daily price range. The stop is set at "
        "Price − (2 × ATR), placing it outside normal day-to-day noise.\n\n"
        "This means stops on volatile stocks are wider (they need room to breathe) "
        "and tighter on stable stocks. Professional traders never use arbitrary "
        "percentage stops — ATR-based stops respect the stock's actual behaviour.\n\n"
        "Learn more: investopedia.com/terms/a/atr.asp"
    ),
    "R:R Ratio": (
        "Risk-to-Reward Ratio — potential profit ÷ potential loss on the trade.\n\n"
        "• 1:1 → Break-even math. Avoid unless win rate > 60%.\n"
        "• 2:1 → Minimum professional standard\n"
        "• 3:1 → Good — you can be wrong half the time and still profit\n"
        "• > 4:1 → Excellent asymmetric setup\n\n"
        "At Institutional, conviction trades typically require R:R ≥ 2.5:1 to "
        "justify the position versus other opportunities.\n\n"
        "Learn more: investopedia.com/terms/r/riskrewardratio.asp"
    ),
    "Position Sizing": (
        "How many dollars and shares to buy based on your risk tolerance.\n\n"
        "Formula used here (professional standard):\n"
        "Shares = (Portfolio × Risk%) ÷ (Price − Stop)\n\n"
        "Risk% = 1.5% means you're willing to lose 1.5% of your total portfolio "
        "if the trade hits the stop. This is the 'moderate risk' professional standard.\n\n"
        "Never risk more than 2% per trade. A 5-trade losing streak at 2%/trade "
        "= 10% portfolio drawdown — survivable. At 5%/trade = 25% — catastrophic.\n\n"
        "Learn more: investopedia.com/terms/p/positionsizing.asp"
    ),
    "Ratchet Stop": (
        "A stop loss that automatically moves up as your gains accumulate, "
        "locking in profits while letting winners run.\n\n"
        "This app's ratchet levels:\n"
        "• +10% gain → Stop moves to breakeven (you can't lose money)\n"
        "• +25% gain → Stop floors at +10% (protect 10% gain)\n"
        "• +50% gain → Stop floors at +25% (protect 25% gain)\n"
        "• +75% gain → Stop floors at +40% (protect 40% gain)\n\n"
        "Professional traders call this 'trailing stop' or 'profit ratchet'. "
        "It solves the hardest problem in investing: letting winners run while "
        "never giving back all your gains."
    ),
    "Composite Score": (
        "Weighted composite signal combining three analytical dimensions:\n\n"
        "• Technical (45%): RSI, MACD, Moving Averages, Bollinger Bands, Volume\n"
        "• Fundamental (40%): Forward P/E, FCF Yield, Revenue & Earnings Growth, "
        "Margins, Debt/Equity\n"
        "• Sentiment (15%): VADER analysis of latest news headlines\n\n"
        "Score thresholds:\n"
        "• ≥ 72 → Strong Buy\n• 58–72 → Buy\n"
        "• 44–58 → Hold\n• 30–44 → Sell\n• < 30 → Strong Sell\n\n"
        "Note: The Scanner uses momentum-only scoring (no fundamentals). "
        "A stock can score 85 on momentum and 52 composite — both are correct."
    ),
    "Diversification Score": (
        "Score 0–100 measuring how independently your portfolio positions move.\n\n"
        "Calibrated for equity portfolios (pure-equity correlations are always positive):\n"
        "• ≥ 42 → Well Diversified (avg correlation ≤ 0.16 — positions move quite independently)\n"
        "• 30–42 → Moderate (avg correlation 0.16–0.40 — normal for thematic/sector portfolios)\n"
        "• < 30 → High Correlation Risk (avg correlation > 0.40 — positions cluster together)\n\n"
        "A portfolio of 8 semiconductor stocks will score much lower than a 3-stock "
        "portfolio spanning tech, healthcare, and energy — even though the larger one "
        "looks more diversified.\n\n"
        "Institutional risk teams target average pairwise correlation below 0.40 "
        "(score ~30) for diversified equity portfolios."
    ),
    "Portfolio Correlation": (
        "Weighted average pairwise correlation between all your holdings.\n\n"
        "Correlation = how closely two stocks move together day-to-day.\n\n"
        "• < 0.30 → Well diversified — losses in one position rarely spread to others\n"
        "• 0.30–0.60 → Moderate — typical for sector-focused or thematic portfolios\n"
        "• > 0.60 → High — limited diversification benefit; you're essentially making "
        "one concentrated bet\n\n"
        "⚠️ During market crises correlations spike toward 1.0 — assets that appear "
        "uncorrelated in normal markets often crash together. "
        "This is why professionals also hold bonds, gold, or inverse positions as hedges."
    ),
    "Portfolio Beta": (
        "How much your entire portfolio moves relative to the S&P 500.\n\n"
        "• < 0.8 → Defensive — portfolio moves less than the market\n"
        "• 0.8–1.2 → Market-like — roughly tracking the index\n"
        "• 1.2–1.5 → Aggressive — amplifies both gains and losses\n"
        "• > 1.5 → High leverage equivalent — requires active management\n\n"
        "A tech-heavy portfolio typically has Beta 1.3–1.8. "
        "Institutional risk teams target portfolio Beta ≤ 1.2 for managed accounts."
    ),
    "Portfolio Volatility": (
        "Annualized standard deviation of daily portfolio returns.\n\n"
        "• < 15% → Low — institutional-grade stability\n"
        "• 15–20% → Moderate — similar to a diversified equity fund\n"
        "• 20–30% → Elevated — tech/growth tilt creates meaningful swings\n"
        "• > 30% → High — expect large intraday and weekly moves\n\n"
        "S&P 500 long-run average volatility is ~15%. Most tech portfolios run 20–35%."
    ),
    "Portfolio VaR": (
        "Value at Risk (95% confidence) — the daily loss your portfolio "
        "should NOT exceed on 95% of trading days.\n\n"
        "Example: VaR = −1.8% on a $100,000 portfolio → you expect to lose "
        "no more than $1,800 on a normal day.\n\n"
        "On the worst 5% of days, losses will exceed this number. "
        "VaR understates tail risk in crises — see CVaR for the full picture."
    ),
    "Portfolio CVaR": (
        "Conditional VaR (Expected Shortfall) — the average loss on the "
        "worst 5% of trading days.\n\n"
        "CVaR is always worse than VaR. The gap between them shows how fat the tail is:\n\n"
        "• CVaR ≈ 1.2× VaR → losses are contained even on crash days\n"
        "• CVaR ≈ 2.0× VaR → fat-tail risk, extreme days are severe\n\n"
        "Professional risk managers use CVaR as the primary stress metric — "
        "it tells you what you actually lose when things go wrong."
    ),
    "Portfolio Max Drawdown": (
        "The worst peak-to-trough decline in portfolio value over the 6-month window.\n\n"
        "Answers: 'What's the worst I've been down at any point?'\n\n"
        "• 0% to −10% → Modest — good risk control or benign market\n"
        "• −10% to −20% → Normal for a growth-tilted equity portfolio\n"
        "• −20% to −30% → Significant — test of conviction\n"
        "• < −30% → Severe — review position sizing and stop discipline\n\n"
        "Recovery math is asymmetric: a −30% drawdown requires +43% just to break even."
    ),
}


def _tip(key: str) -> str:
    """Return tooltip text for st.metric(help=...) and st.column_config help=."""
    return _TIPS.get(key, "")


def _m(val_str: str) -> str:
    """Return masked placeholder when privacy mode is on, otherwise the value as-is."""
    return "••••••" if st.session_state.get("_privacy", True) else val_str


def _render_trade_button(
    ticker: str,
    suggested_action: str = "BUY",
    signal_context: str = "",
    price: float | None = None,
    shares: float | None = None,
    trigger: str = "RECOMMENDATION",
    followed_intent: str | None = None,
    notes: str = "",
    key_suffix: str = "",
    label: str | None = None,
    use_container_width: bool = False,
) -> None:
    """
    Render a "📒 Trade {ticker}" button that, on click, stages prefill values
    into session_state and navigates to the Trade Journal page.

    The Trade Journal form reads these keys on its next render:
      _prefill_trade   — ticker / action / shares / price / trigger / notes
      _tj_signal_seen  — the reconciled verdict at point of click (e.g.
                         "Composite Sell · 42/100"), so the Decision Context
                         block becomes passive capture instead of typing work
      _tj_followed     — "Yes / No / Discretionary" radio default

    followed_intent values: 'yes', 'no', 'discretionary' — maps to the radio
    label strings expected by the form. Leave as None to skip the auto-set
    (user will pick manually).
    """
    _label = label or f"📒 Trade {ticker}"
    _key   = f"_trade_btn_{ticker}_{key_suffix or 'default'}"
    if st.button(_label, key=_key, use_container_width=use_container_width):
        _prefill: dict = {
            "ticker":  ticker.upper(),
            "action":  suggested_action.upper(),
            "trigger": trigger,
            "notes":   notes,
        }
        if price is not None and price > 0:
            _prefill["price"]  = float(price)
        if shares is not None and shares > 0:
            _prefill["shares"] = float(shares)
        st.session_state["_prefill_trade"] = _prefill
        if signal_context:
            st.session_state["_tj_signal_seen"] = signal_context
        if followed_intent:
            _followed_map = {
                "yes":           "Yes — followed signal",
                "no":            "No — overrode signal",
                "discretionary": "No signal — discretionary",
            }
            st.session_state["_tj_followed"] = _followed_map.get(
                followed_intent, "— (skip)"
            )
        st.session_state["_pending_page"] = "📒 Trade Journal"
        st.rerun()


def _fill_news_slot(slot, items: list) -> None:
    """Render curated news items into a sidebar container slot."""
    with slot:
        if not items:
            st.caption("No news found for current holdings.")
            return
        for _ni in items[:10]:
            _clr  = "#00b300" if _ni["label"] == "Positive" else (
                    "#ff4444" if _ni["label"] == "Negative" else "#666")
            _icon = "▲" if _ni["label"] == "Positive" else (
                    "▼" if _ni["label"] == "Negative" else "–")
            _badge = "✅ " if _ni["tier"] == 1 else ""
            _pub   = _ni["publisher"][:18]
            _ago   = _time_ago(_ni["ts"])
            _raw   = _ni["title"]
            _head  = _html.escape(_raw[:72] + ("…" if len(_raw) > 72 else ""))
            _url   = _ni["url"]
            _link  = (
                f"<a href='{_url}' target='_blank' "
                f"style='color:#bbb;text-decoration:none;line-height:1.3'>{_head}</a>"
                if _url else f"<span style='color:#bbb'>{_head}</span>"
            )
            st.markdown(
                f"<div style='margin-bottom:7px;padding:5px 7px;"
                f"border-left:3px solid {_clr};"
                f"border-radius:0 4px 4px 0;background:#161616'>"
                f"<div style='font-size:0.72em;color:#666;margin-bottom:2px'>"
                f"<span style='color:{_clr};font-weight:bold'>{_icon}</span> "
                f"<b style='color:#999'>{_ni['ticker']}</b> · "
                f"{_badge}{_pub} · {_ago}</div>"
                f"<div style='font-size:0.78em'>{_link}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )




# ── Password gate ─────────────────────────────────────────────────────────────
def _check_password():
    try:
        expected = st.secrets.get("app", {}).get("password", "")
    except Exception:
        expected = ""
    if not expected or st.session_state.get("auth_ok"):
        return
    _render_brand(large=True)
    st.subheader("Sign In")
    pwd = st.text_input("Password", type="password")
    if st.button("Login", type="primary"):
        if pwd == expected:
            st.session_state.auth_ok = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

_check_password()


# ── Session state — load from DB once per session ────────────────────────────
if "scanner_results" not in st.session_state:
    st.session_state.scanner_results = None
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = datetime.now()
if "nav_page" not in st.session_state:
    st.session_state.nav_page = "🏠 Home"
# Apply any pending navigation set by mid-page buttons (must run before
# the sidebar radio widget renders so the widget picks up the new value).
if "_pending_page" in st.session_state:
    _dest = st.session_state.pop("_pending_page")
    if _dest == "📈 Analysis":
        # Remember where we came from so a Back button can return us there
        st.session_state["_nav_origin"] = st.session_state.get("nav_page", "")
    st.session_state["nav_page"] = _dest
if not st.session_state.get("db_loaded"):
    st.session_state.holdings_df = db.load_holdings()
    st.session_state.watchlist   = db.load_watchlist()
    st.session_state.trades_df   = db.load_trades()
    st.session_state.db_loaded   = True

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Portfolio value banner — always at the very top ──────────────────
    _pv = st.session_state.get("_portfolio_value", 0)
    # Privacy toggle — persists across reruns within the session
    _priv_on = st.session_state.get("_privacy", True)

    if _pv > 0:
        _risk_val = _pv * MODERATE_RISK_PCT
        _pv_label    = _m(f"${_pv:,.0f}")
        _risk_label  = _m(f"${_risk_val:,.0f}")
        _eye_icon    = "👁" if _priv_on else "🙈"
        st.markdown(
            f"<div style='background:#1565C0;border-radius:8px;padding:12px 14px 10px;"
            f"margin-bottom:4px;color:#fff'>"
            f"<div style='display:flex;align-items:center;justify-content:space-between;"
            f"font-size:0.68em;font-weight:700;letter-spacing:0.1em;"
            f"text-transform:uppercase;opacity:0.8;margin-bottom:4px'>"
            f"<span>Portfolio Value</span>"
            f"</div>"
            f"<div style='font-size:1.5em;font-weight:700;line-height:1.1'>{_pv_label}</div>"
            f"<div style='font-size:0.74em;margin-top:6px;opacity:0.9'>"
            f"Risk/trade: <b>{_risk_label}</b>&nbsp;"
            f"<span style='opacity:0.65'>· 1.5% moderate</span></div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button(f"{_eye_icon} {'Show' if _priv_on else 'Hide'} values",
                     key="_privacy_toggle", use_container_width=True,
                     help="Hide or show all dollar amounts — useful in public"):
            st.session_state["_privacy"] = not _priv_on
            st.rerun()
    else:
        st.markdown(
            "<div style='background:#1565C0;border-radius:8px;padding:12px 14px 10px;"
            "margin-bottom:12px;color:#fff'>"
            "<div style='font-size:0.68em;font-weight:700;letter-spacing:0.1em;"
            "text-transform:uppercase;opacity:0.8;margin-bottom:4px'>Portfolio Value</div>"
            "<div style='font-size:1.1em;font-weight:600;opacity:0.65'>Loading…</div>"
            "<div style='font-size:0.74em;margin-top:6px;opacity:0.5'>Open Portfolio tab</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    _render_brand(large=False)
    page = st.radio(
        "Navigate",
        ["🏠 Home", "🔍 Market Scanner", "📈 Analysis", "⚖️ Compare", "📋 Watchlist", "📒 Trade Journal", "🪞 Trade Review", "📜 Recommendations History", "📅 Economic Calendar"],
        key="nav_page",
        label_visibility="collapsed",
    )
    st.divider()

    # Market status
    mkt = market_status()
    st.markdown(
        f"<span style='color:{mkt['color']};font-weight:bold'>● {mkt['label']}</span>"
        f"<span style='color:#888'> · {mkt['time_et']}</span>",
        unsafe_allow_html=True,
    )
    if not mkt["is_open"]:
        # Compute last trading day for the closed-market note
        from datetime import timedelta as _td
        _now_et   = datetime.now(_pytz.timezone("America/New_York"))
        _weekday  = _now_et.weekday()          # 0=Mon … 6=Sun
        _hour_et  = _now_et.hour + _now_et.minute / 60
        if _weekday >= 5:                       # weekend → back to Friday
            _last_close = _TODAY_ET - _td(days=_weekday - 4)
        elif _hour_et < 9.5:                    # pre-market → previous trading day
            _prev = _TODAY_ET - _td(days=1)
            while _prev.weekday() >= 5:
                _prev -= _td(days=1)
            _last_close = _prev
        else:                                   # after-hours / overnight → today
            _last_close = _TODAY_ET
        st.caption(
            f"📊 Showing end-of-day data as of "
            f"{_last_close.strftime('%a %b %d')}. "
            f"Prices & signals update when market opens."
        )

    # Refresh button
    if st.button("🔄 Refresh All Data", use_container_width=True):
        _ah.reset()
        st.cache_data.clear()
        st.session_state.last_refresh = datetime.now()
        st.rerun()

    refresh_ago = int((datetime.now() - st.session_state.last_refresh).total_seconds())
    if refresh_ago < 60:
        st.caption(f"Last refresh: {refresh_ago}s ago")
    else:
        st.caption(f"Last refresh: {refresh_ago // 60}m {refresh_ago % 60}s ago")

    # ── Data Health widget ────────────────────────────────────────────────
    _ah_overall_lv, _ah_overall_icon = _ah.overall_level()
    _ah_auto_expand = _ah_overall_lv in ("red", "yellow")
    with st.expander(f"{_ah_overall_icon} Data Health", expanded=_ah_auto_expand):
        for _ah_src, _ah_label in [
            ("yahoo_finance", "Yahoo Finance"),
            ("fred",          "FRED (St. Louis Fed)"),
            ("supabase",      "Supabase DB"),
        ]:
            _ah_h = _ah.get_health(_ah_src)
            _ah_parts = []
            if _ah_h["calls"] > 0:
                _ah_parts.append(f"{_ah_h['calls']} calls")
            if _ah_h["errors"] > 0:
                _ah_parts.append(f"**{_ah_h['errors']} err**")
            if _ah_h["rate_limits"] > 0:
                _ah_parts.append(f"**{_ah_h['rate_limits']} RL**")
            _ah_detail = " · ".join(_ah_parts) if _ah_parts else "no calls yet"
            _ah_fresh  = _ah_h["freshness"]
            _ah_err_snippet = (
                f" · _{_ah_h['last_error'][:45]}…_"
                if _ah_h["last_error"] else ""
            )
            st.markdown(
                f"{_ah_h['icon']} **{_ah_label}** — {_ah_detail}  \n"
                f"<span style='font-size:0.74em;color:#888'>"
                f"Fresh: {_ah_fresh}{_ah_err_snippet}"
                f"</span>",
                unsafe_allow_html=True,
            )
        if st.button("Reset counters", key="_ah_reset", use_container_width=True):
            _ah.reset()
            st.rerun()

    # ── Curated news feed — filled after page data loads ─────────────────
    st.divider()
    st.markdown(
        "<span style='font-size:0.85em;font-weight:600;color:#ccc'>📰 CURATED NEWS</span>"
        "<span style='font-size:0.75em;color:#555'> · vetted sources</span>",
        unsafe_allow_html=True,
    )
    _news_slot = st.container()   # placeholder — filled by page code below

    portfolio_value = _pv if _pv > 0 else 50_000
    st.divider()
    if db.has_db():
        st.markdown("🟢 **Supabase connected** — data persists")
    else:
        st.markdown("🟡 **Local session only** — [configure DB to persist](https://supabase.com)")
    st.caption("Prices: Yahoo Finance · Not financial advice")

# ── Risk-free rate (13-week T-bill, refreshed daily) ─────────────────────────
@st.cache_data(ttl=86400)
def _get_rfr() -> float:
    return fetch_risk_free_rate()


@st.cache_data(ttl=300)
def _get_premarket_brief(held_tickers: tuple, watchlist: tuple) -> dict:
    """5-minute cached pre-market intel (futures, global markets, movers).
    Macro events are injected by the caller after this returns."""
    from stock_analyzer.premarket import (
        fetch_futures, futures_tone, fetch_global_markets, fetch_premarket_movers
    )
    import datetime as _dtt
    fut  = fetch_futures()
    glbl = fetch_global_markets()
    mvrs = fetch_premarket_movers(list(held_tickers) + list(watchlist), {})
    tone = futures_tone(fut)
    now_et = _dtt.datetime.now(__import__("pytz").timezone("America/New_York"))
    return {
        "tone":           tone,
        "futures":        fut,
        "global_markets": glbl,
        "movers":         mvrs,
        "events":         [],          # caller fills this in from _macro_events
        "as_of":          now_et.strftime("%I:%M %p ET"),
    }


# ── Shared data loader ────────────────────────────────────────────────────────
@st.cache_data(ttl=1800)
def load_all(ticker: str, period: str = "6mo") -> dict:
    # One yf.Ticker session covers history + info + news + earnings (was 4 separate calls)
    bundle = fetch_ticker_bundle(ticker, period)
    df = compute_indicators(bundle["history"])
    t_score, t_signals = technical_score(df)
    financials = fetch_financials_from_info(bundle["info"])
    _sector_for_scoring = bundle.get("info", {}).get("sector", "")
    f_score, f_signals = fundamental_score(financials, _sector_for_scoring)
    avg_sent, headlines = analyze_news(bundle["news"])
    s_score = sentiment_score_0_100(avg_sent)
    total = combined_score(t_score, f_score, s_score)
    rec = recommendation(total)
    price = float(df["Close"].iloc[-1]) if not df.empty else None
    stop, atr_val = atr_stop_loss(df, multiplier=2.0)
    entry_lo, entry_hi = entry_zone(price, atr_val) if price else (None, None)
    targets = compute_price_targets(df, financials, price) if price else None
    sr = support_resistance(df)
    _rfr = _get_rfr()
    try:
        spy_df = fetch_spy(period)
        risk_metrics = compute_all_risk(df, spy_df, _rfr)
    except Exception:
        risk_metrics = compute_all_risk(df, None, _rfr)
    upside = upside_potential(price, financials) if price else None
    _info  = bundle.get("info", {})
    name   = _info.get("shortName") or _info.get("longName") or ticker
    sector = _info.get("sector", "")
    # Company Overview fields — sourced from yfinance .info, exposed so the
    # Analysis page can render a "Company Overview" block before the Trade Plan
    # without a second network call.
    industry          = _info.get("industry", "")
    market_cap        = _info.get("marketCap")
    business_summary  = _info.get("longBusinessSummary", "")
    return {
        "df": df, "t_score": t_score, "t_signals": t_signals,
        "f_score": f_score, "f_signals": f_signals,
        "s_score": s_score, "avg_sent": avg_sent, "headlines": headlines,
        "total": total, "rec": rec, "financials": financials,
        "current_price": price, "upside": upside,
        "atr": atr_val, "stop": stop, "news_raw": bundle["news"],
        "entry_lo": entry_lo, "entry_hi": entry_hi,
        "targets": targets, "sr": sr,
        "risk_metrics": risk_metrics, "earnings": bundle["earnings"],
        "revisions": bundle.get("revisions", {}),
        "name": name, "sector": sector,
        "industry": industry, "market_cap": market_cap,
        "business_summary": business_summary,
    }

# ── Sector ETF multi-period returns (for heatmap) ────────────────────────────
@st.cache_data(ttl=3600)
def _fetch_sector_returns() -> pd.DataFrame:
    """Batch-download all sector ETFs, compute 1W/1M/3M/6M returns."""
    import yfinance as yf
    from stock_analyzer.data import _retry
    unique_etfs = list(dict.fromkeys(SECTOR_ETF.values()))
    try:
        raw = _retry(
            yf.download, unique_etfs,
            period="6mo", auto_adjust=True, progress=False, threads=True,
        )
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
        close = close.dropna(how="all")
        periods = {"1W": 5, "1M": 21, "3M": 63, "6M": 126}
        rows = []
        for etf in unique_etfs:
            try:
                col = (close[etf] if etf in close.columns else close.iloc[:, 0]).dropna()
                if len(col) < 2:
                    continue
                row = {"ETF": etf}
                for label, n in periods.items():
                    idx = min(n, len(col) - 1)
                    row[label] = round((float(col.iloc[-1]) / float(col.iloc[-idx - 1]) - 1) * 100, 2)
                rows.append(row)
            except Exception:
                continue
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


# ── Market index strip — shown on every page ─────────────────────────────────
@st.fragment(run_every=60)
def _index_strip():
    indices = fetch_market_indices()
    if not indices:
        return
    tiles = ""
    for idx in indices:
        up         = idx["change_pct"] >= 0
        bg         = "rgba(0,180,70,0.10)"   if up else "rgba(220,38,38,0.10)"
        border     = "#00a040"               if up else "#cc2222"
        lbl_clr    = "#555"
        price_clr  = "#111"
        val_clr    = "#006b2a"               if up else "#aa1111"
        arrow      = "▲" if up else "▼"
        sign       = "+" if up else ""
        price_str  = f"{idx['price']:,.2f}"
        change_str = f"{arrow} {sign}{idx['change_pct']:.2f}%  ({sign}{abs(idx['change']):.0f} pts)"
        tiles += (
            f"<div style='flex:1;background:{bg};"
            f"border:1px solid {border};border-left:3px solid {border};"
            f"border-radius:6px;padding:6px 14px 7px;text-align:center;line-height:1.35'>"
            f"<div style='font-size:0.62em;color:{lbl_clr};font-weight:700;"
            f"letter-spacing:0.09em;text-transform:uppercase;margin-bottom:1px'>{idx['short']}</div>"
            f"<div style='font-size:1.05em;font-weight:700;color:{price_clr}'>{price_str}</div>"
            f"<div style='font-size:0.72em;font-weight:600;color:{val_clr};margin-top:1px'>{change_str}</div>"
            f"</div>"
        )
    ts = indices[0]["fetched_at"] if indices else ""
    st.markdown(
        f"<div style='display:flex;gap:10px;align-items:stretch;margin-top:8px;margin-bottom:2px'>{tiles}</div>"
        f"<div style='font-size:0.62em;color:#444;text-align:right;margin-top:2px'>"
        f"📡 {ts} · auto-refreshes every 60s</div>",
        unsafe_allow_html=True,
    )

_index_strip()
st.markdown("<div style='margin-bottom:6px'></div>", unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# PAGE 1 — MY PORTFOLIO
# ═════════════════════════════════════════════════════════════════════════════
if page == "🏠 Home":
    st.title("🏠 Home")


    # Load data for all held tickers
    held_tickers = [
        str(r.get("Ticker", "")).strip().upper()
        for _, r in st.session_state.holdings_df.iterrows()
        if str(r.get("Ticker", "")).strip()
    ]
    held_data: dict = {}
    with st.spinner("Loading portfolio data…"):
        for t in held_tickers:
            try:
                held_data[t] = load_all(t)
            except Exception as e:
                st.warning(f"Could not load {t}: {e}")

    if held_data:
        _news = curate_news_items(held_data)
        st.session_state["_sidebar_news"] = _news
        _fill_news_slot(_news_slot, _news)

    # ── Live price strip — fragment auto-refreshes every 60 s ────────────
    @st.fragment(run_every=60)
    def _price_strip(tickers: list[str]):
        mkt = market_status()
        live = fetch_live_prices(tickers)
        st.session_state["_live_prices"] = live   # share with P&L table
        if not live:
            return
        price_cols = st.columns(len(live))
        for col, (t, lp) in zip(price_cols, live.items()):
            chg   = lp["change_pct"]
            arrow = "▲" if chg >= 0 else "▼"
            clr   = "#00C851" if chg >= 0 else "#ff4444"
            col.markdown(
                f"<div style='text-align:center;padding:6px 2px;"
                f"border:1px solid #333;border-radius:6px'>"
                f"<b>{t}</b><br>"
                f"<span style='font-size:1.1em'>${lp['price']:.2f}</span><br>"
                f"<span style='color:{clr};font-size:0.85em'>"
                f"{arrow} {chg:+.2f}%</span></div>",
                unsafe_allow_html=True,
            )
        refresh_note = (
            "🔄 auto-refreshes every 60s"
            if mkt["is_open"] else "market closed · showing last close"
        )
        st.caption(
            f"Prices: **Yahoo Finance** · "
            f"{list(live.values())[0]['fetched_at']} · "
            f"{mkt['label']} · {refresh_note}"
        )
        st.divider()

    _price_strip(held_tickers)

    # Merge live prices into held_data so P&L uses the freshest price
    for ticker, lp in st.session_state.get("_live_prices", {}).items():
        if ticker in held_data:
            held_data[ticker]["current_price"] = lp["price"]

    holdings = st.session_state.holdings_df.to_dict("records")
    port_df = build_portfolio_df(holdings, held_data)
    st.session_state["_last_port_df"] = port_df          # used by Trade Journal decision context
    st.session_state["_signals_computed_at"] = datetime.now().strftime("%I:%M %p")  # for staleness warning

    if port_df.empty:
        st.info("Enter your holdings above to see portfolio analytics.")
        st.stop()

    # Cache enriched port_df (with Sector) so other pages can use it
    st.session_state["_port_df_enriched"] = port_df

    # ── Stock split detection ─────────────────────────────────────────────────
    _sp_check_key = f"_split_check_{_TODAY_ET}"
    if _sp_check_key not in st.session_state:
        _dismissed_sp = st.session_state.get("_dismissed_splits", set())
        with st.spinner("Checking for unaccounted stock splits…"):
            st.session_state[_sp_check_key] = detect_portfolio_splits(
                st.session_state.holdings_df,
                st.session_state.get("_live_prices", {}),
                dismissed=_dismissed_sp,
            )
    _pending_splits = st.session_state.get(_sp_check_key, [])

    for _sp in _pending_splits:
        _sp_key = f"{_sp['ticker']}_{_sp['split_date']}"
        _sp_color = "#f59e0b"
        st.markdown(
            f"<div style='background:#1a1200;border:1px solid {_sp_color};"
            f"border-left:4px solid {_sp_color};border-radius:8px;"
            f"padding:16px 20px;margin-bottom:12px'>"
            f"<span style='color:{_sp_color};font-weight:700;font-size:1.05em'>"
            f"⚠️ Stock Split Detected — {_sp['ticker']} {_sp['ratio_str']} {_sp['split_type']} Split"
            f"</span>"
            f"<span style='color:#aaa;font-size:0.85em;margin-left:12px'>"
            f"detected on {_sp['split_date']}</span>"
            f"<div style='display:flex;gap:40px;margin-top:12px;font-size:0.9em'>"
            f"<div><span style='color:#ef4444'>❌ Before adjustment</span><br>"
            f"<b>{_sp['orig_shares']:g} shares</b> @ <b>${_sp['orig_avg_cost']:,.2f}</b><br>"
            f"<span style='color:#ef4444'>P&L: {_sp['orig_pnl_pct']:+.1f}%</span></div>"
            f"<div style='color:#aaa;font-size:1.4em;align-self:center'>→</div>"
            f"<div><span style='color:#22c55e'>✅ After adjustment</span><br>"
            f"<b>{_sp['adj_shares']:g} shares</b> @ <b>${_sp['adj_avg_cost']:,.2f}</b><br>"
            f"<span style='color:#22c55e'>P&L: {_sp['adj_pnl_pct']:+.1f}%</span></div>"
            f"<div style='color:#aaa;font-size:0.82em;align-self:center;max-width:260px'>"
            f"Current price: <b>${_sp['current_price']:,.2f}</b><br>"
            f"This adjusts your cost basis and share count to reflect the {_sp['ratio_str']} split. "
            f"Your actual investment value is unchanged.</div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
        _sp_c1, _sp_c2, _sp_c3 = st.columns([2, 2, 8])
        with _sp_c1:
            if st.button(f"✅ Apply Adjustment", key=f"_sp_apply_{_sp_key}",
                         type="primary", use_container_width=True):
                _hdf = st.session_state.holdings_df.copy()
                _mask = _hdf["Ticker"] == _sp["ticker"]
                _hdf.loc[_mask, "Shares"]      = _sp["adj_shares"]
                _hdf.loc[_mask, "Avg Cost ($)"] = _sp["adj_avg_cost"]
                st.session_state.holdings_df = _hdf
                if db.save_holdings(_hdf):
                    # Record a synthetic SPLIT row in the trades table so the
                    # Rebuild flow (recalculate_from_trades) can reproduce the
                    # post-split holdings on replay. Without this, a future
                    # Rebuild would replay the pre-split BUYs and silently
                    # overwrite the user's approved post-split state.
                    # action='SPLIT' is special-cased in recalculate_from_trades
                    # to overwrite (not accumulate) the holding.
                    try:
                        db.save_trade({
                            "ticker":       str(_sp["ticker"]).upper(),
                            "action":       "SPLIT",
                            "shares":       float(_sp["adj_shares"]),
                            "price":        float(_sp["adj_avg_cost"]),
                            "cost_basis":   None,
                            "realized_pnl": None,
                            "notes":        (
                                f"{_sp.get('ratio_str','')} {_sp.get('split_type','')} split — "
                                f"adjusted from {_sp['orig_shares']:g}sh @ ${_sp['orig_avg_cost']:,.2f} "
                                f"to {_sp['adj_shares']:g}sh @ ${_sp['adj_avg_cost']:,.2f}"
                            ),
                            "trigger_type": "REBALANCE",
                        })
                        st.session_state.trades_df = db.load_trades()
                    except Exception:
                        # Don't block the split adjustment on the synthetic-row write;
                        # the user has already approved the holdings change.
                        pass
                    # Invalidate caches so portfolio rebuilds with new values
                    for _k in list(st.session_state.keys()):
                        if _k.startswith("_split_check_") or _k.startswith("_live_prices"):
                            del st.session_state[_k]
                    # Also invalidate the Trade Journal drift-detection flag so
                    # next visit re-checks against the new synthetic SPLIT row
                    st.session_state.pop("_tj_drift_checked", None)
                    st.session_state.pop("_tj_drift_state",  None)
                    st.success(
                        f"{_sp['ticker']} adjusted: {_sp['orig_shares']:g} shares @ "
                        f"${_sp['orig_avg_cost']:,.2f} → {_sp['adj_shares']:g} shares @ "
                        f"${_sp['adj_avg_cost']:,.2f}"
                    )
                    st.rerun()
                else:
                    st.error("Failed to save — check Supabase connection.")
        with _sp_c2:
            if st.button("Dismiss", key=f"_sp_dismiss_{_sp_key}",
                         use_container_width=True):
                _dismissed = st.session_state.get("_dismissed_splits", set())
                _dismissed.add(_sp_key)
                st.session_state["_dismissed_splits"] = _dismissed
                # Remove from today's cache
                if _sp_check_key in st.session_state:
                    st.session_state[_sp_check_key] = [
                        s for s in st.session_state[_sp_check_key]
                        if f"{s['ticker']}_{s['split_date']}" != _sp_key
                    ]
                st.rerun()

    total_val   = port_df["Market Value"].sum()
    total_cost  = (port_df["Avg Cost"] * port_df["Shares"]).sum()
    total_pnl   = port_df["P&L ($)"].sum()
    total_pnl_pct = total_pnl / total_cost * 100 if total_cost else 0
    avg_score   = port_df["Score"].mean()

    # Today's P&L — (live price − prev close) × shares for each position
    _lp_map = st.session_state.get("_live_prices", {})
    _today_pnl = sum(
        (_lp_map[r["Ticker"]]["price"] - _lp_map[r["Ticker"]]["prev_close"]) * r["Shares"]
        for _, r in port_df.iterrows()
        if r["Ticker"] in _lp_map and _lp_map[r["Ticker"]].get("prev_close", 0) > 0
    )
    _today_pnl_pct = _today_pnl / total_val * 100 if total_val else 0
    _today_loaded  = bool(_lp_map)
    portfolio_value = total_val                        # drive risk calc from live holdings
    st.session_state["_portfolio_value"] = total_val  # update sidebar display

    # ── Pre-compute all analytics (before tabs so all tabs can access) ────────
    # Signal change detection (session-state baseline)
    _curr_signals = dict(zip(port_df["Ticker"], port_df["Signal"]))
    _prev_signals = st.session_state.get("_prev_signals", {})
    _signal_changes = []
    for _t, _sig in _curr_signals.items():
        _prev = _prev_signals.get(_t)
        if _prev and _prev != _sig:
            _bearish = ("Sell", "Avoid", "Weak")
            _bullish = ("Strong Buy", "Buy")
            _degraded = any(w in _sig for w in _bearish) and any(w in _prev for w in _bullish)
            _improved = any(w in _sig for w in _bullish) and any(w in _prev for w in _bearish)
            _signal_changes.append({
                "ticker": _t, "from": _prev, "to": _sig,
                "degraded": _degraded, "improved": _improved,
            })
    st.session_state["_prev_signals"] = _curr_signals

    alert_list = alerts(port_df, held_data)
    # Append signal-change alerts
    for _sc in _signal_changes:
        _icon = "📉" if _sc["degraded"] else "📈" if _sc["improved"] else "↔️"
        _lvl  = "warning" if _sc["degraded"] else "info"
        alert_list.append({
            "level": _lvl, "category": "signal_change",
            "msg": f"{_icon} **{_sc['ticker']}** signal changed: {_sc['from']} → **{_sc['to']}** since last check",
        })

    n_danger   = sum(1 for a in alert_list if a["level"] == "danger")
    n_warning  = sum(1 for a in alert_list if a["level"] == "warning")

    actions = rebalance_actions(port_df)

    try:
        corr_df      = correlation_matrix(held_data)
        _weights_map = dict(zip(port_df["Ticker"], port_df["Weight (%)"])) if not corr_df.empty else None
        div          = diversification_score(corr_df, _weights_map)
        div_score    = div["score"]
        avg_corr     = div["avg_correlation"]
        risk_pairs   = div["risk_pairs"]
        _div_label   = ("Well Diversified" if div_score >= 42
                        else "Moderate" if div_score >= 30 else "High Correlation Risk")
    except Exception:
        corr_df    = pd.DataFrame()
        div        = {"score": None, "avg_correlation": None, "risk_pairs": []}
        div_score  = avg_corr = None
        risk_pairs = []
        _div_label = "Unavailable"

    try:
        div_recs = diversification_recommendations(port_df, corr_df, div, portfolio_value)
    except Exception:
        div_recs = []

    h_rets = holding_returns(held_data)

    # Portfolio-level risk metrics (Beta, Sharpe, Sortino, VaR, CVaR, Max Drawdown)
    try:
        _spy_for_risk = fetch_spy("6mo")
        _port_risk = compute_portfolio_risk_metrics(port_df, held_data, _spy_for_risk, _get_rfr())
    except Exception:
        _port_risk = {}
    st.session_state["_port_risk_cache"] = _port_risk  # available to Analysis page

    # Risk Advisor recommendations — generated from portfolio risk metrics
    try:
        _risk_advisor_recs = build_risk_advisor_recommendations(
            port_df, held_data, _port_risk, h_rets, total_val
        )
    except Exception:
        _risk_advisor_recs = []
    # Cache HIGH-priority alert titles so other pages (e.g. Watchlist) can gate
    # ENTER_NOW recommendations against active portfolio risk state.
    st.session_state["_risk_high_alerts_cache"] = [
        r.get("title", "") for r in _risk_advisor_recs if r.get("priority") == "HIGH"
    ]

    best_row  = port_df.loc[port_df["P&L (%)"].idxmax()]
    worst_row = port_df.loc[port_df["P&L (%)"].idxmin()]
    winners   = int((port_df["P&L (%)"] > 0).sum())

    if n_danger > 0 or (div_score is not None and div_score < 30):
        _rag_label, _rag_color = "Action Required", "#ff4444"
    elif n_warning > 0 or (div_score is not None and div_score < 42):
        _rag_label, _rag_color = "Monitor", "#ffbb33"
    else:
        _rag_label, _rag_color = "All Clear", "#00C851"

    # Load macro calendar (cached per ET date — FRED key optional but recommended)
    _mc_day_key = f"_macro_cal_{_TODAY_ET}"
    if _mc_day_key not in st.session_state:
        _fred_k = (
            st.secrets.get("fred", {}).get("api_key")
            or os.environ.get("FRED_API_KEY", "")
        )
        st.session_state[_mc_day_key] = build_macro_calendar(
            port_df, fred_key=_fred_k or None, days_ahead=45, days_behind=7,
            today=_TODAY_ET,
        )
    _macro_events = st.session_state[_mc_day_key]

    # Market context — drives Daily Briefing tone (bull / bear / flat)
    try:
        _mkt_indices = fetch_market_indices()
        _sp_row      = next((i for i in _mkt_indices if i["short"] == "S&P 500"), None)
        _nq_row      = next((i for i in _mkt_indices if i["short"] == "NASDAQ"),  None)
        _sp_pct      = float(_sp_row["change_pct"]) if _sp_row else 0.0
        _nq_pct      = float(_nq_row["change_pct"]) if _nq_row else 0.0
        _mkt_tone    = "bull" if _sp_pct >= 0.5 else "bear" if _sp_pct <= -0.5 else "flat"

        # Leading / lagging sectors from 1-week returns
        _sect_df_ctx  = _fetch_sector_returns()
        _lead_sectors: list[dict] = []
        if not _sect_df_ctx.empty and "1W" in _sect_df_ctx.columns:
            _sect_sorted = _sect_df_ctx.sort_values("1W", ascending=False)
            for _, _sr in _sect_sorted.head(3).iterrows():
                _etf = str(_sr["ETF"])
                _lead_sectors.append({
                    "etf":       _etf,
                    "sector":    next((k for k, v in SECTOR_ETF.items() if v == _etf), _etf),
                    "return_1w": float(_sr["1W"]),
                })

        _market_context = {
            "tone":            _mkt_tone,
            "sp500_pct":       _sp_pct,
            "nasdaq_pct":      _nq_pct,
            "leading_sectors": _lead_sectors,
        }
    except Exception:
        _market_context = {"tone": "flat", "sp500_pct": 0.0, "nasdaq_pct": 0.0, "leading_sectors": []}

    # Auto-fetch composite scores for top scanner picks so Grow Today conviction
    # labels are accurate without requiring a manual Refresh Signals click.
    # Pool size matches _grow_today's full candidate window (max_picks × 4 = 12
    # on bull days) so every candidate the Brief evaluates has a composite —
    # otherwise picks beyond slot 5 fall back to "Verify — Run Analysis First"
    # even when the user just wants a Skip/Go verdict on the Brief itself.
    # load_all() is cached (30-min TTL) so this is instant if picks were recently
    # analyzed; only the first fetch per ticker per session takes network time.
    _sr_for_comp = st.session_state.get("scanner_results")
    if _sr_for_comp is not None and not _sr_for_comp.empty:
        _existing_comp = st.session_state.get("_grow_composites", {})
        _top_for_comp  = (
            _sr_for_comp[~_sr_for_comp["Ticker"].isin(set(held_tickers))]
            .head(12)["Ticker"].tolist()
        )
        _missing_comp  = [t for t in _top_for_comp if t not in _existing_comp]
        # Track tickers where the composite fetch failed so the Grow Today UI
        # can surface a "composite scores unavailable" banner instead of
        # silently letting picks bypass the composite-score gate.
        _comp_failures: list[str] = []
        if _missing_comp:
            for _tc in _missing_comp:
                try:
                    _existing_comp[_tc] = load_all(_tc)
                except Exception:
                    _comp_failures.append(_tc)
            st.session_state._grow_composites = _existing_comp
        # Compute coverage = fraction of intended top picks that have composite data
        _intended = set(_top_for_comp)
        _have     = _intended & set(_existing_comp.keys())
        st.session_state._grow_composites_coverage = {
            "intended": sorted(_intended),
            "have":     sorted(_have),
            "missing":  sorted(_intended - _have),
            "failures": _comp_failures,
        }

    # Build Daily Briefing (synthesises all intelligence — computed once before tabs).
    #
    # Lock semantics: when the user has clicked "Lock Today's Setup", we serve
    # the cached snapshot instead of rebuilding. This freezes the AM read so
    # mid-day data drift can't shift recommendations under the user after they
    # already decided. Lock auto-expires next trading day (different _TODAY_ET).
    _brief_locked_for = st.session_state.get("_brief_locked_for_date")
    _brief_snapshot   = st.session_state.get("_brief_locked_snapshot")
    _brief_use_lock   = (
        st.session_state.get("_brief_locked", False)
        and _brief_locked_for == _TODAY_ET
        and _brief_snapshot is not None
    )
    if _brief_use_lock:
        _daily_brief = _brief_snapshot
        # _brief_built_at was set at the time of the original build (preserved)
    else:
        # Lock from a previous trading day — clear it so the new day's Brief builds fresh
        if st.session_state.get("_brief_locked") and _brief_locked_for != _TODAY_ET:
            st.session_state["_brief_locked"] = False
            st.session_state.pop("_brief_locked_snapshot", None)
            st.session_state.pop("_brief_locked_for_date", None)
            st.session_state.pop("_brief_locked_at", None)
        try:
            _daily_brief = build_daily_briefing(
                port_df         = port_df,
                alert_list      = alert_list,
                risk_recs       = _risk_advisor_recs,
                news_items      = st.session_state.get("_sidebar_news", []),
                macro_events    = _macro_events,
                held_data       = held_data,
                scanner_results = st.session_state.get("scanner_results"),
                portfolio_value = total_val,
                today           = _TODAY_ET,
                market_context  = _market_context,
                grow_composites = st.session_state.get("_grow_composites", {}),
            )
            # Stamp the build time in ET — surfaced as "Built at HH:MM ET" on
            # the Brief header so the user can see how fresh the data is.
            st.session_state["_brief_built_at"] = datetime.now(_pytz.timezone("America/New_York"))
        except Exception:
            _daily_brief    = None
            _market_context = {"tone": "flat", "sp500_pct": 0.0, "nasdaq_pct": 0.0, "leading_sectors": []}

    # Cache sectors Grow Today is already filling so the Watchlist (a separate page)
    # can demote ENTER_NOW picks that would stack the same sector exposure.
    # When Daily Briefing failed, cache is None (not empty list) so downstream
    # features can detect "offline" vs. "computed and empty" and surface the
    # state to the user instead of silently disabling coordination gates.
    if _daily_brief is None:
        st.session_state["_grow_today_sectors_cache"] = None
        st.session_state["_daily_brief_offline"]      = True
        # Provide a minimal empty dict so downstream code doesn't crash, but
        # session_state flag tells consumers to render the offline UI state.
        _daily_brief = {"act_today": [], "buy_candidates": [], "review_list": [], "grow_today": {}}
    else:
        st.session_state["_daily_brief_offline"] = False
        _gt_today = _daily_brief.get("grow_today") or {}
        st.session_state["_grow_today_sectors_cache"] = sorted({
            str(p.get("sector", "")).strip()
            for p in (_gt_today.get("new_picks") or []) + (_gt_today.get("add_positions") or [])
            if str(p.get("sector", "")).strip()
        })

        # ── Recommendations log (persist + decorate with first-seen) ─────────
        # Capture every pick surfaced today so the user can audit the App's
        # recommendation history over time. Unique-constraint on
        # (ticker, rec_date, rec_type) means the same ticker re-surfacing
        # across Brief rebuilds within the same day is a no-op — only the
        # first surface gets a row, and that row's surfaced_at is what we
        # display on the card. Locked Briefs skip the write (the snapshot's
        # surfaced_at was captured at lock time) but still get decorated
        # on read.
        #
        # No once-per-session guard: db.save_recommendations is idempotent
        # via the unique constraint + ignore_duplicates, so writing on every
        # Brief build is cheap (single upsert round-trip) and avoids the
        # sticky-flag failure mode where an early failed save permanently
        # blocked retries for the rest of the session.
        if db.has_db() and not _brief_use_lock:
            _rec_save_result = {"attempted": 0, "saved": 0, "error": None}
            try:
                # Price snapshot resolver: each pick type stores price differently,
                # and some buy_candidates don't carry one. Fall back to held-position
                # avg_cost (via port_df) or live-prices cache before giving up. The
                # column is nullable in the DB so None is safe — but a populated
                # value makes "would-have-gained" math possible later.
                _live_px_map = st.session_state.get("_live_prices", {}) or {}
                _port_px_map: dict = {}
                if port_df is not None and not port_df.empty and "Price" in port_df.columns:
                    _port_px_map = {
                        str(_r["Ticker"]).upper(): float(_r["Price"] or 0)
                        for _, _r in port_df.iterrows()
                        if _r.get("Ticker")
                    }
                def _price_for(_tk: str, _pick_price=None):
                    if _pick_price:
                        try:
                            _v = float(_pick_price)
                            if _v > 0:
                                return _v
                        except (TypeError, ValueError):
                            pass
                    _u = str(_tk).upper()
                    _lp = _live_px_map.get(_u, {})
                    if isinstance(_lp, dict):
                        try:
                            _v = float(_lp.get("price") or 0)
                            if _v > 0:
                                return _v
                        except (TypeError, ValueError):
                            pass
                    _pv = _port_px_map.get(_u, 0)
                    return _pv if _pv > 0 else None

                _rec_rows: list[dict] = []
                for _p in (_gt_today.get("new_picks") or []):
                    _tk = str(_p.get("ticker", ""))
                    _rec_rows.append({
                        "ticker":           _tk,
                        "rec_date":         _TODAY_ET,
                        "rec_type":         "new_pick",
                        "price_at_surface": _price_for(_tk, _p.get("price")),
                        "composite_score":  _p.get("composite_score"),
                        "momentum_score":   _p.get("score"),
                        "sector":           _p.get("sector", ""),
                        "conviction":       _p.get("conviction", ""),
                        "verdict":          ((_p.get("xref") or {}).get("verdict") or ""),
                        "thesis":           _p.get("thesis", ""),
                    })
                for _p in (_gt_today.get("add_positions") or []):
                    _tk = str(_p.get("ticker", ""))
                    _rec_rows.append({
                        "ticker":           _tk,
                        "rec_date":         _TODAY_ET,
                        "rec_type":         "add_winner",
                        "price_at_surface": _price_for(_tk),  # add-to-winner doesn't carry an explicit price
                        "composite_score":  _p.get("score"),
                        "momentum_score":   _p.get("score"),
                        "sector":           _p.get("sector", ""),
                        "conviction":       "high",   # add-to-winner only fires on Strong Buy
                        "verdict":          "confirmed",
                        "thesis":           _p.get("thesis", ""),
                    })
                for _p in (_daily_brief.get("buy_candidates") or []):
                    _bx = _p.get("xref") or {}
                    _tk = str(_p.get("ticker", ""))
                    _rec_rows.append({
                        "ticker":           _tk,
                        "rec_date":         _TODAY_ET,
                        "rec_type":         "buy_candidate",
                        "price_at_surface": _price_for(_tk),
                        "composite_score":  None,
                        "momentum_score":   _p.get("score"),
                        "sector":           _p.get("sector", ""),
                        "conviction":       "",
                        "verdict":          str(_bx.get("verdict") or ""),
                        "thesis":           "",
                    })
                if _rec_rows:
                    _rec_save_result = db.save_recommendations(_rec_rows)
            except Exception as _rec_save_err:
                _rec_save_result = {"attempted": 0, "saved": 0,
                                    "error": str(_rec_save_err)[:200]}
            st.session_state["_rec_log_save_result"] = _rec_save_result

        # Decorate each pick with its first-seen surfaced_at so the card
        # render can show "Recommended at HH:MM ET" without an extra round-trip
        # per card. Single fetch for today; build (ticker, rec_type) → ts map.
        if db.has_db() and not _brief_use_lock:
            _rec_load_count = 0
            _rec_load_error: str | None = None
            try:
                _rec_today_df = db.load_recommendations(start_date=_TODAY_ET, end_date=_TODAY_ET)
                _rec_first_seen: dict[tuple[str, str], str] = {}
                if not _rec_today_df.empty:
                    for _, _r in _rec_today_df.iterrows():
                        _key = (str(_r.get("ticker", "")).upper(),
                                str(_r.get("rec_type", "")))
                        _ts = _r.get("surfaced_at")
                        if _ts is not None and _key not in _rec_first_seen:
                            _rec_first_seen[_key] = str(_ts)
                _rec_load_count = len(_rec_today_df)
                # Attach to each pick dict for downstream render to read
                for _p in (_gt_today.get("new_picks") or []):
                    _p["_first_seen_at"] = _rec_first_seen.get(
                        (str(_p.get("ticker", "")).upper(), "new_pick")
                    )
                for _p in (_gt_today.get("add_positions") or []):
                    _p["_first_seen_at"] = _rec_first_seen.get(
                        (str(_p.get("ticker", "")).upper(), "add_winner")
                    )
                for _p in (_daily_brief.get("buy_candidates") or []):
                    _p["_first_seen_at"] = _rec_first_seen.get(
                        (str(_p.get("ticker", "")).upper(), "buy_candidate")
                    )
            except Exception as _rec_load_err:
                _rec_load_error = str(_rec_load_err)[:200]
            st.session_state["_rec_log_load_state"] = {
                "rows_today": _rec_load_count,
                "error":      _rec_load_error,
            }

    # Next 3 HIGH-impact events for the Command Center strip (future only)
    _cc_catalysts = [
        e for e in _macro_events
        if e["impact"] == MC_HIGH and e["date"] >= _TODAY_ET
    ][:3]

    # Compute price alert triggers here so they surface in the Command Center
    _pa_store_cc = st.session_state.get("_price_alerts", {})
    _pa_fired_cc = []
    for _, _ccpr in port_df.iterrows():
        _cct  = _ccpr["Ticker"]
        _ccpx = float(_ccpr.get("Price") or 0)
        _ccpa = _pa_store_cc.get(_cct, {})
        _cctgt = _ccpa.get("target") or 0.0
        _ccflr = _ccpa.get("floor")  or 0.0
        if _cctgt > 0 and _ccpx >= _cctgt:
            _pa_fired_cc.append(("🎯", "#f59e0b", f"{_cct} hit take-profit ${_cctgt:.2f}"))
        if _ccflr > 0 and _ccpx <= _ccflr:
            _pa_fired_cc.append(("🚨", "#ef4444", f"{_cct} breached floor ${_ccflr:.2f}"))

    # Build optional alert row for inside the Command Center box
    _cc_alert_row = ""
    if _pa_fired_cc:
        _cc_badges = "".join(
            f"<span style='background:{c};color:#fff;padding:2px 10px;border-radius:12px;"
            f"font-size:0.72em;font-weight:700;white-space:nowrap'>{ico} {msg}</span>"
            for ico, c, msg in _pa_fired_cc
        )
        _cc_alert_row = (
            f"<div style='margin-top:10px;padding-top:10px;border-top:1px solid #374151;"
            f"display:flex;gap:8px;flex-wrap:wrap;align-items:center'>"
            f"<span style='color:#9ca3af;font-size:0.7em;font-weight:600;letter-spacing:0.05em'>"
            f"PRICE ALERTS</span>{_cc_badges}</div>"
        )

    # Build catalyst strip HTML for Command Center
    _cc_catalyst_row = ""
    if _cc_catalysts:
        _impact_colors = {MC_HIGH: "#ef4444", MC_MEDIUM: "#f59e0b"}
        _cat_icons = {
            "Fed Policy": "🏦", "Inflation": "📊", "Employment": "👷",
            "Growth": "📈", "Consumer": "🛒", "Activity": "🏭",
        }
        _cat_chips = []
        for _ce in _cc_catalysts:
            _ico   = _cat_icons.get(_ce["category"], "📅")
            _color = _impact_colors.get(_ce["impact"], "#6b7280")
            _tix   = ", ".join(_ce["affected_tickers"][:3]) if _ce["affected_tickers"] else "All"
            if len(_ce["affected_tickers"]) > 3:
                _tix += f" +{len(_ce['affected_tickers'])-3}"
            _cat_chips.append(
                f"<span style='display:inline-flex;align-items:center;gap:5px;"
                f"background:#1f2937;border:1px solid {_color};border-radius:8px;"
                f"padding:3px 10px;font-size:0.72em;white-space:nowrap'>"
                f"<span style='color:{_color};font-weight:700'>{_ico} {_ce['event']}</span>"
                f"<span style='color:#9ca3af'>·</span>"
                f"<span style='color:#e5e7eb'>{_ce['days_label']}</span>"
                f"<span style='color:#9ca3af'>·</span>"
                f"<span style='color:#6b7280'>{_tix}</span>"
                f"</span>"
            )
        _cc_catalyst_row = (
            f"<div style='margin-top:10px;padding-top:10px;border-top:1px solid #374151;"
            f"display:flex;gap:8px;flex-wrap:wrap;align-items:center'>"
            f"<span style='color:#9ca3af;font-size:0.7em;font-weight:600;"
            f"letter-spacing:0.05em;white-space:nowrap'>UPCOMING CATALYSTS</span>"
            f"{''.join(_cat_chips)}</div>"
        )

    # ── Portfolio Command Center ───────────────────────────────────────────────
    st.markdown(
        f"<div style='background:#111827;border:1px solid #1f2937;border-radius:12px;"
        f"padding:14px 20px;margin-bottom:4px'>"
        f"<div style='display:flex;align-items:center;gap:10px'>"
        f"<span style='font-size:1.2em;font-weight:700;color:#f9fafb'>Portfolio Command Center</span>"
        f"<span style='background:{_rag_color};color:#000;padding:2px 10px;"
        f"border-radius:20px;font-size:0.7em;font-weight:800;letter-spacing:0.05em'>"
        f"{_rag_label}</span>"
        f"<span style='margin-left:auto;color:#6b7280;font-size:0.75em'>"
        f"{len(port_df)} positions · {datetime.now().strftime('%b %d, %Y')}</span>"
        f"</div>{_cc_alert_row}{_cc_catalyst_row}</div>",
        unsafe_allow_html=True,
    )

    _c1, _c2, _c3, _c4, _c5, _c6, _c7, _c8 = st.columns(8)
    _c1.metric("Portfolio Value",  _m(f"${total_val:,.0f}"))
    _c2.metric("Total P&L",        _m(f"${total_pnl:,.0f}"), f"{total_pnl_pct:+.1f}%", delta_color="normal")
    if _today_loaded:
        _c3.metric(
            "Today's P&L",
            _m(f"${_today_pnl:+,.0f}"),
            f"{_today_pnl_pct:+.2f}%",
            delta_color="normal" if _today_pnl >= 0 else "inverse",
            help="Intraday gain/loss vs yesterday's close · updates every 60s",
        )
    else:
        _c3.metric("Today's P&L", "Updating…", help="Loads with the live price strip")
    _c4.metric("Alerts",           f"{n_danger}🔴 {n_warning}🟡",
               help=f"{n_danger} danger · {n_warning} warning — check Alerts & Actions tab")
    _c5.metric("Avg Conviction",   f"{avg_score:.0f}/100")
    _c6.metric("Diversification",  f"{div_score:.0f}/100" if div_score is not None else "—",
               _div_label, delta_color="off")
    # Value = $ amount (masked under privacy); delta = % with explicit sign so
    # Streamlit's arrow/color parser sees the minus (a "$-450" delta string is
    # mis-parsed as positive because the $ prefix hides the sign).
    _c7.metric(f"Best: {best_row['Ticker']}",
               _m(f"${best_row['P&L ($)']:,.0f}"), f"{best_row['P&L (%)']:+.1f}%", delta_color="normal")
    _c8.metric(f"Worst: {worst_row['Ticker']}",
               _m(f"${worst_row['P&L ($)']:,.0f}"), f"{worst_row['P&L (%)']:+.1f}%", delta_color="normal")

    st.markdown("<div style='margin-bottom:4px'></div>", unsafe_allow_html=True)

    # ── Navigation tabs ───────────────────────────────────────────────────────
    _db_act_n   = len(_daily_brief["act_today"])
    _db_buy_n   = len(_daily_brief["buy_candidates"])
    _db_icon    = " 🔴" if _db_act_n else ""
    tab_daily, tab_evening, tab_ov, tab_perf, tab_earn, tab_pnl, tab_act, tab_risk, tab_rs, tab_macro, tab_heat, tab_rank, tab_brief = st.tabs([
        f"📋 Today's Brief{_db_icon}",
        "🌙 Evening Debrief",
        "📊 Overview",
        "📈 Performance",
        "📅 Earnings",
        "💰 P&L Attribution",
        f"⚠️ Alerts & Actions{'  🔴' if n_danger else ('  🟡' if n_warning else '')}",
        "🔗 Risk Analysis",
        "📈 Relative Strength",
        "🌐 Macro",
        "🔥 Sector Rotation",
        "🏆 Rankings",
        "🤖 AI Brief",
    ])

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 0 — DAILY BRIEFING
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_daily:
        from datetime import datetime as _dt

        # ── Freshness strip — captured-at timestamp + Lock/Unlock control ─────
        # The Brief is designed for one-time AM decision-making, not a streaming
        # ticker. Surfacing the build time (and offering a freeze) closes the
        # "I acted on a card that disappeared 30 min later" trust gap.
        _b_built_at = st.session_state.get("_brief_built_at")
        _b_locked   = bool(st.session_state.get("_brief_locked", False))
        _b_locked_at = st.session_state.get("_brief_locked_at")
        _b_now_et   = datetime.now(_pytz.timezone("America/New_York"))

        def _fmt_et(_d):
            if _d is None:
                return "—"
            try:
                return _d.strftime("%-I:%M %p ET") if hasattr(_d, "strftime") else "—"
            except ValueError:
                # Windows strftime doesn't support %-I; fall back to %I and strip
                return _d.strftime("%I:%M %p ET").lstrip("0")

        def _fmt_age(_d):
            if _d is None:
                return ""
            try:
                _delta = _b_now_et - _d
                _mins = int(_delta.total_seconds() // 60)
                if _mins < 1:
                    return "just now"
                if _mins < 60:
                    return f"{_mins} min ago"
                _hrs = _mins // 60
                return f"{_hrs}h {_mins % 60}m ago"
            except Exception:
                return ""

        _fs_c1, _fs_c2 = st.columns([4, 1])
        with _fs_c1:
            if _b_locked and _b_locked_at:
                st.markdown(
                    f"<div style='background:#172554;border:1px solid #3b82f6;"
                    f"border-radius:8px;padding:8px 14px;margin-bottom:8px;color:#bfdbfe'>"
                    f"🔒 <b>Today's Setup Locked</b> at {_fmt_et(_b_locked_at)} "
                    f"<span style='color:#93c5fd;font-size:0.85em'>({_fmt_age(_b_locked_at)})</span> — "
                    f"recommendations are frozen for the day. Risk, holdings, and prices continue updating."
                    f"</div>",
                    unsafe_allow_html=True,
                )
            elif _b_built_at:
                st.markdown(
                    f"<div style='background:#0f172a;border:1px solid #334155;"
                    f"border-radius:8px;padding:8px 14px;margin-bottom:8px;color:#cbd5e1;font-size:0.92em'>"
                    f"📌 <b>Built at {_fmt_et(_b_built_at)}</b> "
                    f"<span style='color:#94a3b8'>({_fmt_age(_b_built_at)})</span> · "
                    f"recommendations refresh when scanner reruns or composite cache expires (30 min)."
                    f"</div>",
                    unsafe_allow_html=True,
                )
        with _fs_c2:
            if _b_locked:
                if st.button(
                    "🔓 Unlock",
                    key="_brief_unlock_btn",
                    help="Resume live Brief updates. Recommendations will refresh on the next render.",
                    use_container_width=True,
                ):
                    st.session_state["_brief_locked"] = False
                    st.session_state.pop("_brief_locked_snapshot", None)
                    st.session_state.pop("_brief_locked_for_date", None)
                    st.session_state.pop("_brief_locked_at", None)
                    st.rerun()
            else:
                if st.button(
                    "🔒 Lock Setup",
                    key="_brief_lock_btn",
                    help="Freeze today's recommendations. Lock expires at end of trading day.",
                    use_container_width=True,
                    disabled=_daily_brief is None,
                ):
                    st.session_state["_brief_locked"]          = True
                    st.session_state["_brief_locked_snapshot"] = _daily_brief
                    st.session_state["_brief_locked_for_date"] = _TODAY_ET
                    st.session_state["_brief_locked_at"]       = _b_now_et
                    st.rerun()

        # ── Refresh macro data ────────────────────────────────────────────────
        # Bypasses the per-day _macro_cal_{date} session cache so the user can
        # force a fresh FRED pull mid-day (e.g. right after CPI prints when the
        # static date and the actual release time disagree). Cache otherwise
        # only invalidates at midnight ET.
        _rmd_c1, _rmd_c2 = st.columns([1, 5])
        with _rmd_c1:
            if st.button(
                "🔄 Refresh macro",
                key="_refresh_macro_home",
                help="Re-fetch FRED actuals + release dates. Use mid-day when the calendar looks stale.",
                use_container_width=True,
            ):
                _mc_clear_key = f"_macro_cal_{_TODAY_ET}"
                if _mc_clear_key in st.session_state:
                    del st.session_state[_mc_clear_key]
                st.rerun()
        with _rmd_c2:
            _macro_src = (
                "FRED live" if any(e.get("source") == "static+fred" for e in (_macro_events or []))
                else "Static only (add FRED key in Economic Calendar)"
            )
            st.caption(f"Macro data source: **{_macro_src}** · cache invalidates at midnight ET.")

        # ── 🪞 Trade Review verdict strip ─────────────────────────────────────
        # Surfaces the top finding from Trade Review's Course-Correction engine
        # so behavioural patterns don't get stranded on one page. Cached in
        # session_state to avoid recomputing on every Brief render — invalidates
        # when trade data changes (add, delete, Rebuild correction) or the
        # date rolls. Key includes a hash of the realized_pnl + cost_basis
        # tuples so a Rebuild that corrects existing rows (without changing
        # the row count) still busts the cache.
        _tr_strip_td   = st.session_state.get("trades_df")
        _tr_strip_n    = len(_tr_strip_td) if _tr_strip_td is not None else 0
        _tr_strip_sig: int = 0
        if _tr_strip_n > 0:
            try:
                # Cheap stable fingerprint of the financial state of the journal.
                # pandas itertuples is far faster than building dicts; using
                # builtin hash is enough because we just need change-detection,
                # not cryptographic uniqueness.
                _tr_strip_sig = hash(tuple(
                    (int(r.id) if r.id is not None else 0,
                     round(float(r.realized_pnl or 0), 4),
                     round(float(r.cost_basis or 0),   4))
                    for r in _tr_strip_td.itertuples(index=False)
                    if hasattr(r, "id")
                ))
            except Exception:
                _tr_strip_sig = 0
        _tr_strip_key  = f"{_tr_strip_n}_{_TODAY_ET}_{_tr_strip_sig}"
        _tr_strip_cache = st.session_state.get("_tr_strip_cache") or {}

        if _tr_strip_n > 0 and _tr_strip_cache.get("key") != _tr_strip_key:
            try:
                _tr_strip_tickers = sorted({
                    str(_t).upper() for _t in _tr_strip_td["ticker"].dropna().tolist()
                })
                _tr_strip_prices: dict = {}
                if _tr_strip_tickers:
                    try:
                        _px = fetch_live_prices(_tr_strip_tickers)
                        _tr_strip_prices = {
                            _t: float(_d.get("price", 0)) for _t, _d in _px.items()
                        }
                    except Exception:
                        _tr_strip_prices = {}
                _tr_strip_spy = None
                try:
                    _tr_strip_spy = fetch_spy(period="3mo")
                except Exception:
                    _tr_strip_spy = None
                _tr_data = build_trade_review(
                    trades_df       = _tr_strip_td,
                    current_prices  = _tr_strip_prices,
                    spy_history_df  = _tr_strip_spy,
                    today           = _TODAY_ET,
                    lookback_days   = 30,
                )
                _tr_strip_recs = build_recommendations(_tr_data["trades"])
                _tr_strip_data = None
                if _tr_strip_recs:
                    _tr_strip_data = {
                        "top":        _tr_strip_recs[0],
                        "n_critical": sum(1 for r in _tr_strip_recs if r["severity"] == "critical"),
                        "n_watch":    sum(1 for r in _tr_strip_recs if r["severity"] == "watch"),
                        "n_good":     sum(1 for r in _tr_strip_recs if r["severity"] == "good"),
                        "n_total":    len(_tr_strip_recs),
                    }
                st.session_state["_tr_strip_cache"] = {
                    "key":  _tr_strip_key,
                    "data": _tr_strip_data,
                }
                _tr_strip_cache = st.session_state["_tr_strip_cache"]
            except Exception as _tr_strip_err:
                # Don't let Trade Review compute failure block the Brief, but
                # leave a fingerprint so silent failures are at least visible.
                st.session_state["_tr_strip_cache"] = {
                    "key":   _tr_strip_key,
                    "data":  None,
                    "error": str(_tr_strip_err)[:120],
                }
                _tr_strip_cache = st.session_state["_tr_strip_cache"]

        _tr_strip_summary = _tr_strip_cache.get("data")
        if _tr_strip_summary:
            _tr_top    = _tr_strip_summary["top"]
            _tr_n_crit = _tr_strip_summary["n_critical"]
            _tr_n_watch = _tr_strip_summary["n_watch"]
            _tr_n_total = _tr_strip_summary["n_total"]
            _tr_sev_styles = {
                "critical": ("🔴", "#ef4444", "#3f1d1d", "#fecaca"),
                "watch":    ("🟡", "#f59e0b", "#3b2a0a", "#fde68a"),
                "good":     ("🟢", "#22c55e", "#052e16", "#86efac"),
            }
            _tr_icon, _tr_bdr, _tr_bg, _tr_text_col = _tr_sev_styles.get(
                _tr_top["severity"], _tr_sev_styles["watch"]
            )
            # Build a count summary line — "+N other findings"
            _tr_other_bits = []
            if _tr_top["severity"] == "critical":
                _remaining_crit = max(0, _tr_n_crit - 1)
                if _remaining_crit:
                    _tr_other_bits.append(f"{_remaining_crit} more 🔴")
                if _tr_n_watch:
                    _tr_other_bits.append(f"{_tr_n_watch} 🟡")
            elif _tr_top["severity"] == "watch":
                _remaining_watch = max(0, _tr_n_watch - 1)
                if _remaining_watch:
                    _tr_other_bits.append(f"{_remaining_watch} more 🟡")
            _tr_other_text = (
                f" · <span style='color:#94a3b8;font-size:0.85em'>"
                f"+{', '.join(_tr_other_bits)}</span>" if _tr_other_bits else ""
            )

            _tr_c1, _tr_c2 = st.columns([5, 1])
            with _tr_c1:
                st.markdown(
                    f"<div style='background:{_tr_bg};border-left:3px solid {_tr_bdr};"
                    f"border-radius:6px;padding:8px 14px;margin-bottom:8px;color:#cbd5e1'>"
                    f"🪞 <b>Trade Review</b> · {_tr_icon} "
                    f"<b style='color:{_tr_text_col}'>{_tr_top['pattern']}</b>"
                    f"{_tr_other_text}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with _tr_c2:
                if st.button("▶ Open", key="_tr_strip_open_btn",
                             use_container_width=True,
                             help="Jump to Trade Review for the full diagnosis and the trades behind each finding."):
                    st.session_state["_pending_page"] = "🪞 Trade Review"
                    st.rerun()
        elif _tr_strip_cache.get("error"):
            # Trade Review failed to compute — surface a tiny caption so the
            # absence isn't silent. Previously the strip just disappeared and
            # the user had no way to know it had errored.
            st.caption(
                f"🪞 Trade Review verdict unavailable — compute failed "
                f"({_tr_strip_cache['error']}). Open the Trade Review page to retry."
            )

        # Build act_today lookup once — used by pre-market movers and the main sections below
        _act_today_map: dict = {
            str(i["ticker"]).upper(): i
            for i in _daily_brief.get("act_today", [])
            if i.get("ticker")
        }

        # ── Pre-Market Intel panel (visible 4:00–9:29 AM ET weekdays) ─────────
        if is_premarket():
            try:
                _pm = _get_premarket_brief(
                    tuple(held_tickers),
                    tuple(st.session_state.get("watchlist", [])),
                )
                # Inject today's HIGH/MEDIUM macro events (already computed above)
                _pm["events"] = [
                    e for e in _macro_events
                    if e.get("date") == _TODAY_ET and e.get("impact") in ("HIGH", "MEDIUM")
                ]

                # ── Pre-Market Stance (AI narrative + verdict) ────────────
                # Renders ABOVE the deterministic Pre-Market Intel banner so the
                # interpretive stance is the first thing the user sees pre-open.
                # Card hides silently when no Anthropic key is configured —
                # graceful degradation so the rest of Today's Brief is unaffected.
                _pms_key = (
                    st.secrets.get("anthropic", {}).get("api_key")
                    or st.secrets.get("ANTHROPIC_API_KEY")
                    or os.environ.get("ANTHROPIC_API_KEY", "")
                )
                if _pms_key:
                    _pms_model     = "claude-haiku-4-5-20251001"
                    _pms_today_key = str(_TODAY_ET)
                    _pms_cache_key = f"_pm_stance__{_pms_today_key}__{_pms_model}"
                    _pms_cached    = st.session_state.get(_pms_cache_key)

                    _pms_hc1, _pms_hc2 = st.columns([5, 1])
                    with _pms_hc1:
                        st.markdown(
                            f"<div style='font-size:1.05em;font-weight:700;"
                            f"color:#f9fafb;margin-top:4px'>"
                            f"🔭 Pre-Market Stance "
                            f"<span style='color:#9ca3af;font-size:0.72em;font-weight:400'>"
                            f"· {_pm['as_of']}</span></div>",
                            unsafe_allow_html=True,
                        )
                    with _pms_hc2:
                        _pms_refresh = st.button(
                            "🔄 Refresh",
                            key="_pms_refresh_btn",
                            use_container_width=True,
                            help="Re-runs the AI stance with the latest pre-market data.",
                        )

                    if _pms_refresh or _pms_cached is None:
                        with st.spinner("Generating stance narrative…"):
                            # Pull the current regime from the cached FRED detector
                            # if a key is available; otherwise skip — the stance
                            # still has futures / events / portfolio to work with.
                            _pms_regime = None
                            try:
                                _pms_fred_key = (
                                    st.secrets.get("fred", {}).get("api_key")
                                    or os.environ.get("FRED_API_KEY", "")
                                )
                                if _pms_fred_key:
                                    _pms_regime = detect_macro_regime_fred(_pms_fred_key)
                            except Exception:
                                _pms_regime = None

                            _pms_inputs = pms_assemble_inputs(
                                premarket_brief = _pm,
                                regime          = _pms_regime,
                                port_df         = port_df,
                                news_headlines  = [
                                    n.get("headline", "")
                                    for n in st.session_state.get("_sidebar_news", [])[:5]
                                    if n.get("headline")
                                ],
                            )
                            _pms_result = pms_generate_stance(
                                inputs  = _pms_inputs,
                                api_key = _pms_key,
                                model   = _pms_model,
                            )
                            if _pms_result:
                                st.session_state[_pms_cache_key] = _pms_result
                                _pms_cached = _pms_result

                    if _pms_cached:
                        _stance        = _pms_cached.get("stance", "neutral")
                        _stance_label  = _pms_cached.get("stance_label", "Neutral at open")
                        _narrative     = _pms_cached.get("narrative", "")
                        _stance_color  = {
                            "defensive":    "#ef4444",
                            "constructive": "#22c55e",
                        }.get(_stance, "#f59e0b")
                        _stance_icon   = {
                            "defensive":    "🛡️",
                            "constructive": "🟢",
                        }.get(_stance, "⚖️")
                        st.markdown(
                            f"<div style='background:#0f172a;border:1px solid {_stance_color};"
                            f"border-left:5px solid {_stance_color};border-radius:8px;"
                            f"padding:14px 18px;margin-bottom:12px'>"
                            f"<div style='color:#e5e7eb;font-size:0.95em;line-height:1.55'>"
                            f"{_narrative}</div>"
                            f"<div style='margin-top:10px;padding-top:10px;"
                            f"border-top:1px solid #1f2937'>"
                            f"<span style='color:{_stance_color};font-weight:800;"
                            f"letter-spacing:0.05em;text-transform:uppercase;font-size:0.85em'>"
                            f"{_stance_icon} Stance: {_stance_label}</span>"
                            f"<span style='color:#6b7280;font-size:0.75em;margin-left:10px'>"
                            f"Model: {_pms_cached.get('model','?').split('-')[1].title()}</span>"
                            f"</div></div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.warning(
                            "Pre-Market Stance unavailable — Anthropic API call failed. "
                            "Click 🔄 Refresh to retry or check your key in Streamlit secrets."
                        )

                _pm_tone  = _pm["tone"]
                _pm_color = "#14532d" if _pm_tone == "bull" else "#7f1d1d" if _pm_tone == "bear" else "#1c1917"
                _pm_bdr   = "#22c55e" if _pm_tone == "bull" else "#ef4444" if _pm_tone == "bear" else "#4b5563"
                _pm_icon  = "📈" if _pm_tone == "bull" else "📉" if _pm_tone == "bear" else "〰️"
                _pm_label = (
                    "Futures pointing HIGHER — bullish open expected"  if _pm_tone == "bull" else
                    "Futures pointing LOWER — cautious open expected"   if _pm_tone == "bear" else
                    "Futures mixed — direction unclear ahead of open"
                )
                es_row  = next((f for f in _pm["futures"] if f["symbol"] == "ES=F"), None)
                es_str  = f"ES {es_row['chg_pct']:+.2f}%" if es_row else ""

                st.markdown(
                    f"<div style='background:{_pm_color};border:1px solid {_pm_bdr};"
                    f"border-radius:12px;padding:14px 20px;margin-bottom:10px'>"
                    f"<div style='display:flex;align-items:center;justify-content:space-between;"
                    f"flex-wrap:wrap;gap:8px'>"
                    f"<span style='font-size:1.05em;font-weight:700;color:#f9fafb'>"
                    f"🌅 Pre-Market Intel</span>"
                    f"<span style='color:#9ca3af;font-size:0.78em'>{_pm['as_of']}</span>"
                    f"</div>"
                    f"<div style='color:#d1d5db;font-size:0.85em;margin-top:4px'>"
                    f"{_pm_icon} {_pm_label}"
                    + (f" · <b style='color:#f9fafb'>{es_str}</b>" if es_str else "")
                    + f"</div></div>",
                    unsafe_allow_html=True,
                )

                # Row 1: US Futures tiles
                if _pm["futures"]:
                    _pm_cols = st.columns(len(_pm["futures"]))
                    for _pmc, _fut in zip(_pm_cols, _pm["futures"]):
                        _fc = "#22c55e" if _fut["chg_pct"] >= 0 else "#ef4444"
                        _pmc.markdown(
                            f"<div style='background:#111827;border:1px solid #374151;"
                            f"border-radius:8px;padding:8px 10px;text-align:center'>"
                            f"<div style='color:#9ca3af;font-size:0.72em'>{_fut['icon']} {_fut['name']}</div>"
                            f"<div style='color:#f9fafb;font-weight:700'>{_fut['price']:,.0f}</div>"
                            f"<div style='color:{_fc};font-size:0.85em;font-weight:600'>"
                            f"{_fut['chg_pct']:+.2f}%</div></div>",
                            unsafe_allow_html=True,
                        )

                # Row 2: Global markets + movers + events
                _pm_left, _pm_right = st.columns([1, 1])

                with _pm_left:
                    if _pm["global_markets"]:
                        st.markdown(
                            "<div style='color:#6b7280;font-size:0.72em;font-weight:600;"
                            "letter-spacing:0.06em;margin-top:10px;margin-bottom:4px'>"
                            "🌍 GLOBAL MARKETS (OVERNIGHT)</div>",
                            unsafe_allow_html=True,
                        )
                        for _gm in _pm["global_markets"]:
                            _gc = "#22c55e" if _gm["chg_pct"] >= 0 else "#ef4444"
                            _garrow = "▲" if _gm["chg_pct"] >= 0 else "▼"
                            st.markdown(
                                f"<div style='display:flex;justify-content:space-between;"
                                f"padding:2px 0;font-size:0.82em'>"
                                f"<span style='color:#d1d5db'>{_gm['flag']} {_gm['name']}</span>"
                                f"<span style='color:{_gc};font-weight:600'>"
                                f"{_garrow} {_gm['chg_pct']:+.2f}%</span></div>",
                                unsafe_allow_html=True,
                            )

                    if _pm["events"]:
                        st.markdown(
                            "<div style='color:#6b7280;font-size:0.72em;font-weight:600;"
                            "letter-spacing:0.06em;margin-top:12px;margin-bottom:4px'>"
                            "📅 TODAY'S CATALYSTS</div>",
                            unsafe_allow_html=True,
                        )
                        for _ev in _pm["events"][:4]:
                            _eic = "#ef4444" if _ev.get("impact") == "HIGH" else "#f59e0b"
                            _etime = _ev.get("time_et", "")
                            _ename = _ev.get("event", _ev.get("name", ""))
                            st.markdown(
                                f"<div style='font-size:0.82em;padding:2px 0'>"
                                f"<span style='color:{_eic};font-weight:700'>●</span> "
                                f"<span style='color:#9ca3af'>{_etime} </span>"
                                f"<span style='color:#d1d5db'>{_ename}</span></div>",
                                unsafe_allow_html=True,
                            )

                with _pm_right:
                    if _pm["movers"]:
                        st.markdown(
                            "<div style='color:#6b7280;font-size:0.72em;font-weight:600;"
                            "letter-spacing:0.06em;margin-top:10px;margin-bottom:4px'>"
                            "⚡ PRE-MARKET MOVERS (YOUR STOCKS)</div>",
                            unsafe_allow_html=True,
                        )
                        for _mv in _pm["movers"][:8]:
                            _mc = "#22c55e" if _mv["chg_pct"] >= 0 else "#ef4444"
                            _marrow = "▲" if _mv["chg_pct"] >= 0 else "▼"
                            _mv_ticker = str(_mv["ticker"]).upper()
                            _mv_act    = _act_today_map.get(_mv_ticker)
                            _held_badge = (
                                "<span style='background:#1e3a5f;color:#60a5fa;"
                                "padding:0 5px;border-radius:4px;font-size:0.7em'>held</span> "
                                if _mv["is_held"] else ""
                            )
                            # Alert badge when Act Today has an action on this mover
                            _act_badge = ""
                            _act_tooltip = ""
                            if _mv_act:
                                _act_action = str(_mv_act.get("action", ""))
                                if "SELL" in _act_action or "Stop" in _act_action:
                                    _act_badge = ("<span style='background:#7f1d1d;color:#fca5a5;"
                                                  "padding:0 5px;border-radius:4px;font-size:0.7em;"
                                                  "font-weight:700'>⚠ SELL SIGNAL</span> ")
                                elif "RISK" in _act_action:
                                    _act_badge = ("<span style='background:#422006;color:#fcd34d;"
                                                  "padding:0 5px;border-radius:4px;font-size:0.7em;"
                                                  "font-weight:700'>⚠ RISK ALERT</span> ")
                                else:
                                    _act_badge = ("<span style='background:#1c1917;color:#f59e0b;"
                                                  "padding:0 5px;border-radius:4px;font-size:0.7em;"
                                                  "font-weight:700'>⚠ ACT TODAY</span> ")
                            st.markdown(
                                f"<div style='display:flex;justify-content:space-between;"
                                f"align-items:center;padding:3px 0;font-size:0.82em'>"
                                f"<span style='color:#f9fafb;font-weight:600'>{_mv['ticker']}</span>"
                                f"<span>{_held_badge}{_act_badge}"
                                f"<span style='color:#9ca3af;margin-right:6px'>"
                                f"${_mv['pre_price']:.2f}</span>"
                                f"<span style='color:{_mc};font-weight:700'>"
                                f"{_marrow} {_mv['chg_pct']:+.2f}%</span></span></div>",
                                unsafe_allow_html=True,
                            )
                    else:
                        with _pm_right:
                            st.caption("No significant pre-market moves in your holdings or watchlist yet.")

                st.divider()

            except Exception as _pm_err:
                st.caption(f"Pre-market data unavailable: {_pm_err}")

        _db_act    = _daily_brief["act_today"]
        _db_buys   = _daily_brief["buy_candidates"]
        _db_review = _daily_brief["review_list"]
        _db_grow   = _daily_brief.get("grow_today", {})
        _db_tone   = _market_context.get("tone", "flat")
        _db_sp_pct = _market_context.get("sp500_pct", 0.0)
        _db_nq_pct = _market_context.get("nasdaq_pct", 0.0)

        # ── Briefing header — tone-aware ──────────────────────────────────────
        _tone_label = (
            "📈 Growth Mode — Markets Up"   if _db_tone == "bull" else
            "🛡️ Protect Mode — Markets Down" if _db_tone == "bear" else
            "📊 Hold Steady — Mixed Market"
        )
        _tone_color = "#14532d" if _db_tone == "bull" else "#7f1d1d" if _db_tone == "bear" else "#1c1917"
        _tone_bdr   = "#22c55e" if _db_tone == "bull" else "#ef4444" if _db_tone == "bear" else "#4b5563"
        _sp_str     = f"S&P 500 {_db_sp_pct:+.2f}%"
        _nq_str     = f"Nasdaq {_db_nq_pct:+.2f}%"
        _lead_str   = (
            " · Leading: " + ", ".join(
                f"{ls['sector']} ({ls['return_1w']:+.1f}% 1W)"
                for ls in _market_context.get("leading_sectors", [])[:2]
            ) if _market_context.get("leading_sectors") else ""
        )
        # Compute last trading day for the date label when market is closed
        from datetime import timedelta as _dbtd
        _db_now_et  = _dt.now(_pytz.timezone("America/New_York"))
        _db_weekday = _db_now_et.weekday()
        _db_hour_et = _db_now_et.hour + _db_now_et.minute / 60
        if _db_weekday >= 5:
            _db_last_close = _TODAY_ET - _dbtd(days=_db_weekday - 4)
        elif _db_hour_et < 9.5:
            _db_lc = _TODAY_ET - _dbtd(days=1)
            while _db_lc.weekday() >= 5:
                _db_lc -= _dbtd(days=1)
            _db_last_close = _db_lc
        else:
            _db_last_close = _TODAY_ET
        _db_date_str = _TODAY_ET.strftime("%A, %B %d %Y")
        if not mkt["is_open"] and _db_last_close != _TODAY_ET:
            _db_date_str += f" · data as of {_db_last_close.strftime('%a %b %d')}"

        st.markdown(
            f"<div style='background:{_tone_color};border:1px solid {_tone_bdr};"
            f"border-radius:12px;padding:14px 20px;margin-bottom:12px'>"
            f"<div style='display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px'>"
            f"<span style='font-size:1.1em;font-weight:700;color:#f9fafb'>"
            f"{_tone_label}</span>"
            f"<span style='color:#9ca3af;font-size:0.8em'>"
            f"{_db_date_str}</span>"
            f"</div>"
            f"<div style='color:#d1d5db;font-size:0.82em;margin-top:6px'>"
            f"{_sp_str} · {_nq_str}{_lead_str}</div>"
            f"<div style='color:#9ca3af;font-size:0.77em;margin-top:4px'>"
            f"{len(_db_act)} urgent action{'s' if len(_db_act) != 1 else ''} · "
            f"{len(_db_grow.get('new_picks',[]))+len(_db_grow.get('add_positions',[]))} growth setup{'s' if (len(_db_grow.get('new_picks',[]))+len(_db_grow.get('add_positions',[]))) != 1 else ''} · "
            f"{len(_db_review)} to review before close</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # ── Refresh Signals ───────────────────────────────────────────────────
        _rb_col, _ri_col = st.columns([1, 4])
        with _rb_col:
            _do_refresh = st.button(
                "🔄 Refresh Signals",
                key="_db_refresh_signals",
                use_container_width=True,
                help="Re-fetches live prices and re-runs the market scanner. Best used ~15 min after market open.",
            )
        with _ri_col:
            _sig_ts = st.session_state.get("_brief_signals_ts")
            if _sig_ts:
                _sig_age = int((datetime.now() - _sig_ts).total_seconds())
                if _sig_age < 60:
                    st.caption(f"Signals refreshed {_sig_age}s ago — live prices current")
                elif _sig_age < 3600:
                    st.caption(f"Signals refreshed {_sig_age // 60}m {_sig_age % 60}s ago — click to pull latest market data")
                else:
                    st.caption(f"Signals last refreshed {_sig_age // 3600}h ago — **stale, refresh recommended**")
            else:
                st.caption(
                    "Signals reflect the last scanner run — click **Refresh Signals** ~15 min after open "
                    "to score today's price action."
                )
        if _do_refresh:
            st.cache_data.clear()
            _total_scan_tickers = sum(len(v) for v in SECTOR_UNIVERSE.values())
            with st.spinner(
                f"Fetching live prices and scanning {_total_scan_tickers} stocks — ~20 seconds…"
            ):
                _fresh_results = scan_sectors(list(SECTOR_UNIVERSE.keys()), period="6mo")
            if not _fresh_results.empty:
                st.session_state.scanner_results = _fresh_results
                # Pre-fetch full composite analysis for top 12 non-held scanner picks.
                # Pool size matches _grow_today's candidate window (max_picks × 4)
                # so every candidate the Brief considers gets a composite verdict
                # rather than falling back to "Verify — Run Analysis First."
                _top_candidates = _fresh_results[
                    ~_fresh_results["Ticker"].isin(set(held_tickers))
                ].head(12)["Ticker"].tolist()
                _grow_composites: dict = {}
                if _top_candidates:
                    with st.spinner(
                        f"Validating top {len(_top_candidates)} picks with full analysis…"
                    ):
                        for _tc in _top_candidates:
                            try:
                                _grow_composites[_tc] = load_all(_tc)
                            except Exception:
                                pass
                st.session_state._grow_composites    = _grow_composites
                st.session_state._brief_signals_ts   = datetime.now()
                st.rerun()
            else:
                st.warning("Scanner returned no results — check your connection and try again.")

        # ── Quick Research ────────────────────────────────────────────────────
        st.markdown(
            "<div style='background:#111827;border:1px solid #374151;"
            "border-radius:10px;padding:12px 16px;margin-bottom:12px'>"
            "<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px'>"
            "<span style='color:#f9fafb;font-size:0.95em;font-weight:700'>🔍 Research a Stock</span>"
            "<span style='color:#6b7280;font-size:0.8em'>Spot a news catalyst? Enter any ticker for an instant actionable summary.</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        _qr_ic1, _qr_ic2 = st.columns([5, 1])
        with _qr_ic1:
            _qr_ticker_in = st.text_input(
                "ticker", key="_qr_ticker_input",
                placeholder="e.g. RKLB, TSLA, AAPL  — any ticker you've seen in the news",
                label_visibility="collapsed",
            )
        with _qr_ic2:
            _qr_btn = st.button("Research →", key="_qr_btn", use_container_width=True)

        if _qr_btn and _qr_ticker_in.strip():
            _t = _qr_ticker_in.strip().upper()
            with st.spinner(f"Analyzing {_t}..."):
                try:
                    _qr_raw = load_all(_t)
                    # Build portfolio context so the 5th bullet is portfolio-aware
                    _qr_held_row = port_df[port_df["Ticker"] == _t]
                    _qr_is_held  = not _qr_held_row.empty
                    _qr_sector   = _qr_raw.get("sector", "")
                    _qr_sec_wt   = (
                        float(port_df[port_df["Sector"] == _qr_sector]["Weight (%)"].sum())
                        if _qr_sector else 0.0
                    )
                    # Sector-level Act Today awareness — when the user asks about
                    # a ticker in a sector where OTHER positions are flagged for
                    # action, that signals sector stress even if THIS ticker
                    # looks individually fine. Tells the user to wait for the
                    # broader sector picture to stabilise before adding.
                    _qr_sector_acts = []
                    if _qr_sector:
                        _qr_held_tickers_in_sec = set(
                            port_df[port_df["Sector"] == _qr_sector]["Ticker"].tolist()
                        )
                        _qr_sector_acts = [
                            a for a in _daily_brief.get("act_today", [])
                            if a.get("ticker") and a["ticker"] in _qr_held_tickers_in_sec
                            and a["ticker"] != _t
                        ]
                    _qr_ctx = {
                        "held":              _qr_is_held,
                        "held_shares":       float(_qr_held_row["Shares"].iloc[0])   if _qr_is_held else None,
                        "held_avg_cost":     float(_qr_held_row["Avg Cost"].iloc[0]) if _qr_is_held else None,
                        "held_pnl_pct":      float(_qr_held_row["P&L (%)"].iloc[0])  if _qr_is_held else None,
                        "held_signal":       str(_qr_held_row["Signal"].iloc[0])     if _qr_is_held else None,
                        "sector_of_ticker":  _qr_sector,
                        "sector_weight_pct": _qr_sec_wt,
                        "portfolio_beta":    _port_risk.get("beta"),
                        "ticker_beta":       (_qr_raw.get("risk_metrics") or {}).get("beta"),
                        "act_today_flags":   [a for a in _daily_brief.get("act_today", [])
                                              if a.get("ticker") == _t],
                        "sector_act_today":  _qr_sector_acts,
                    }
                    st.session_state["_qr_result"] = _qr_research(_t, _qr_raw, portfolio_ctx=_qr_ctx)
                except Exception as _qr_e:
                    st.session_state["_qr_result"] = {"error": str(_qr_e), "ticker": _t}

        _qr_res = st.session_state.get("_qr_result")
        if _qr_res:
            if "error" in _qr_res:
                st.error(f"Could not load data for {_qr_res['ticker']}: {_qr_res['error']}")
            else:
                _qr_e = _qr_res["entry"]
                # Header strip: ticker | name | sector | price | entry verdict badge
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:10px;flex-wrap:wrap;"
                    f"border-top:1px solid #374151;padding-top:10px;margin-top:4px'>"
                    f"<span style='color:#fbbf24;font-size:1em;font-weight:700'>{_qr_res['ticker']}</span>"
                    + (f"<span style='color:#d1d5db;font-size:0.85em'>{_qr_res['name']}</span>"
                       if _qr_res.get("name") and _qr_res["name"] != _qr_res["ticker"] else "")
                    + (f"<span style='color:#9ca3af;font-size:0.8em'>{_qr_res['sector']}</span>"
                       if _qr_res.get("sector") else "")
                    + (f"<span style='color:#f9fafb;font-size:0.9em'>${_qr_res['price']:.2f}</span>"
                       if _qr_res.get("price") else "")
                    + f"<span style='background:{_qr_e['color']}22;border:1px solid {_qr_e['color']};"
                    f"color:{_qr_e['color']};padding:2px 10px;border-radius:10px;"
                    f"font-size:0.78em;font-weight:700'>{_qr_e['icon']} {_qr_e['label']}</span>"
                    f"<span style='background:{_qr_res['signal_color']}22;border:1px solid {_qr_res['signal_color']};"
                    f"color:{_qr_res['signal_color']};padding:2px 10px;border-radius:10px;"
                    f"font-size:0.78em;font-weight:700'>{_qr_res['signal_icon']} {_qr_res['signal']} {_qr_res['score']:.0f}/100</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # 4-bullet actionable summary
                for _qr_b in _qr_res["bullets"]:
                    st.markdown(f"- {_qr_b}")
                # Recent headlines (top 3)
                if _qr_res.get("headlines"):
                    st.markdown(
                        "<div style='color:#6b7280;font-size:0.8em;margin-top:4px'>"
                        "<strong style='color:#9ca3af'>Recent headlines:</strong></div>",
                        unsafe_allow_html=True,
                    )
                    for _qr_h in _qr_res["headlines"][:3]:
                        _qr_dot = (
                            "🟢" if _qr_h.get("label") == "Positive" else
                            "🔴" if _qr_h.get("label") == "Negative" else "⚪"
                        )
                        _qr_url   = _qr_h.get("url", "")
                        _qr_title = _qr_h.get("headline", "")
                        if _qr_url:
                            st.markdown(
                                f"<div style='color:#9ca3af;font-size:0.78em;margin-top:2px'>"
                                f"{_qr_dot} <a href='{_qr_url}' target='_blank' "
                                f"style='color:#60a5fa'>{_qr_title}</a></div>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.markdown(
                                f"<div style='color:#9ca3af;font-size:0.78em;margin-top:2px'>"
                                f"{_qr_dot} {_qr_title}</div>",
                                unsafe_allow_html=True,
                            )
                # Action buttons
                _qr_bc1, _qr_bc2 = st.columns([3, 1])
                with _qr_bc1:
                    if st.button(f"▶ Full Analysis — {_qr_res['ticker']}", key="_qr_full_btn"):
                        st.session_state["_pending_page"]    = "📈 Analysis"
                        st.session_state["_analysis_ticker"] = _qr_res["ticker"]
                        st.session_state["_nav_origin"]      = "📋 Today's Brief"
                        st.rerun()
                with _qr_bc2:
                    if st.button("✕ Clear", key="_qr_clear_btn"):
                        del st.session_state["_qr_result"]
                        st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

        # ── Grow Today (before Act Today on bull days, after on bear/flat) ────
        def _fmt_first_seen(ts_val) -> str:
            """Render '_first_seen_at' from the recommendations log as a small
            'Recommended HH:MM ET' chip. Returns empty string when no value
            is present (e.g. logging table not yet provisioned)."""
            if not ts_val:
                return ""
            try:
                _dt = pd.to_datetime(ts_val, utc=True, errors="coerce")
                if pd.isna(_dt):
                    return ""
                _et = _dt.tz_convert("America/New_York")
                return _et.strftime("%-I:%M %p ET").lstrip("0") if hasattr(_et, "strftime") else ""
            except Exception:
                try:
                    # Windows strftime doesn't support %-I; fall back to %I and strip
                    _et = pd.to_datetime(ts_val, utc=True).tz_convert("America/New_York")
                    return _et.strftime("%I:%M %p ET").lstrip("0")
                except Exception:
                    return ""

        def _render_grow_today(grow: dict, tone: str):
            if not grow:
                return
            new_picks    = grow.get("new_picks", [])
            add_pos      = grow.get("add_positions", [])
            deploy_note  = grow.get("deploy_note")
            bear_msg     = grow.get("message")
            lead_secs_ui = grow.get("leading_sectors", [])
            risk_banner  = grow.get("risk_banner")
            blocked_adds  = grow.get("risk_blocked_adds", [])
            conc_blocked  = grow.get("concentration_blocked_adds", [])
            macro_blocked = grow.get("macro_blocked_picks", [])
            comp_skipped  = grow.get("composite_skipped", [])

            _g_label = (
                "📈 Grow Today"      if tone == "bull" else
                "🛡️ Defer New Entries" if tone == "bear" else
                "📈 High-Conviction Entries Only"
            )
            _g_bg    = "#052e16" if tone == "bull" else "#1c1917"
            _g_bdr   = "#22c55e" if tone == "bull" else "#ef4444" if tone == "bear" else "#4b5563"
            _g_count = f" ({len(new_picks) + len(add_pos)} setups)" if (new_picks or add_pos) else ""

            st.markdown(
                f"<div style='background:{_g_bg};border-left:4px solid {_g_bdr};"
                f"border-radius:8px;padding:10px 16px;margin-bottom:8px'>"
                f"<span style='font-size:1em;font-weight:700;color:#f9fafb'>{_g_label}{_g_count}</span>"
                + (f"<span style='color:#86efac;font-size:0.82em;margin-left:8px'>"
                   f"Sector leaders: {', '.join(ls['sector'] for ls in lead_secs_ui[:2])}</span>"
                   if lead_secs_ui and tone == "bull" else "")
                + f"</div>",
                unsafe_allow_html=True,
            )

            if bear_msg:
                st.caption(f"🛡️ {bear_msg}")
                return

            # Composite-fetch failure banner — shown when one or more of the
            # intended top picks couldn't get a composite score, so picks may be
            # listed without the full multi-factor signal. Surfacing this lets
            # the user know to manually run Analysis before acting.
            _comp_cov = st.session_state.get("_grow_composites_coverage") or {}
            _missing_comp_ui = _comp_cov.get("missing") or []
            if _missing_comp_ui:
                st.markdown(
                    "<div style='background:#3b2a0a;border:1px solid #f59e0b;"
                    "border-radius:8px;padding:8px 14px;margin-bottom:10px'>"
                    "<div style='color:#fbbf24;font-weight:700;font-size:0.84em;margin-bottom:4px'>"
                    "⚠️ Composite Scores Unavailable for Some Picks</div>"
                    f"<div style='color:#fcd34d;font-size:0.78em'>"
                    f"Composite data missing for: <b>{', '.join(_missing_comp_ui[:5])}</b>"
                    + (f" (+{len(_missing_comp_ui)-5} more)" if len(_missing_comp_ui) > 5 else "")
                    + "</div>"
                    "<div style='color:#fde68a;font-size:0.74em;margin-top:4px;font-style:italic'>"
                    "These picks are shown with momentum data only — the full multi-factor "
                    "composite gate did not run. Open Analysis on each before acting."
                    "</div></div>",
                    unsafe_allow_html=True,
                )

            # Risk banner: shown when Act Today has active portfolio risk flags
            if risk_banner:
                st.markdown(
                    "<div style='background:#422006;border:1px solid #f59e0b;"
                    "border-radius:8px;padding:8px 14px;margin-bottom:10px'>"
                    "<div style='color:#fbbf24;font-weight:700;font-size:0.84em;margin-bottom:4px'>"
                    "⚠️ Active Risk Alerts — resolve Act Today before deploying new capital</div>"
                    + "".join(
                        f"<div style='color:#fcd34d;font-size:0.79em'>• {flag}</div>"
                        for flag in risk_banner
                    )
                    + "</div>",
                    unsafe_allow_html=True,
                )

            if not new_picks and not add_pos:
                # No actionable setups — show the empty-state message, but
                # DON'T early-return. Filtered-out / macro-blocked candidates
                # are still rendered below so the user understands WHY there
                # are no recommendations today (composite said no, sector had
                # a macro event, etc.) rather than wondering "did anything
                # even get screened?"
                _below_count = len(macro_blocked) + len(comp_skipped)
                _below_hint  = (
                    f" {_below_count} candidate{'s' if _below_count != 1 else ''} "
                    "considered but rejected — see details below."
                ) if _below_count else ""
                st.caption(
                    ("No high-confidence setups meet today's criteria. "
                     "Run Market Scanner to refresh candidates." if tone == "bull"
                     else "Flat market — waiting for clearer direction before adding new positions.")
                    + _below_hint
                )
                if tone == "bull":
                    if st.button("🔍 Run Market Scanner", key="_db_grow_scanner"):
                        st.session_state["_pending_page"] = "🔍 Market Scanner"
                        st.rerun()

            # New picks
            if new_picks:
                st.markdown("**🆕 New Positions to Initiate**")
            for _gp in new_picks:
                _gx         = _gp.get("xref", {})
                _reconciled = _gx.get("verdict_reconciled", {}) or {}
                # Prefer the central reconciliation engine's color/label/one-liner.
                # Falls back to the legacy verdict_* fields if the engine didn't run.
                _vc         = _reconciled.get("color") or _gx.get("verdict_color", "#22c55e")
                _vl         = _reconciled.get("label") or _gx.get("verdict_label", "")
                _v_one      = _reconciled.get("one_liner") or _gx.get("verdict_one_liner", "")
                _sz         = _gp.get("sizing", {})
                _conv       = _gp.get("conviction", "unverified")
                _comp_sc    = _gp.get("composite_score")   # None if not yet fetched
                _comp_lbl   = _gp.get("composite_label", "")

                # Conviction badge: color + text driven by composite score
                _conv_cfg = {
                    "high":       ("#22c55e", "✅ High Conviction"),
                    "moderate":   ("#f59e0b", "🟡 Moderate Setup"),
                    "low":        ("#ef4444", "⚠ Low Composite"),
                    "unverified": ("#f59e0b", "🔍 Verify — Run Analysis First"),
                }
                _conv_clr, _conv_txt = _conv_cfg.get(_conv, _conv_cfg["unverified"])

                # Score line: always show momentum; show composite when available
                _score_line = f"Momentum {_gp['score']:.0f}/100"
                if _comp_sc is not None:
                    _score_line += f" · Composite {_comp_sc:.0f}/100"
                    if _comp_lbl:
                        _score_line += f" ({_comp_lbl})"

                st.markdown(
                    f"<div style='background:#111827;border-left:3px solid {_vc};"
                    f"border-radius:6px;padding:10px 14px;margin-bottom:6px'>"
                    f"<div style='display:flex;align-items:center;gap:10px;flex-wrap:wrap'>"
                    f"<span style='color:#f9fafb;font-weight:700'>{_gp['ticker']}</span>"
                    f"<span style='color:#9ca3af;font-size:0.8em'>{_score_line} · {_gp['sector']}"
                    + (f" 🔥" if _gp.get("is_leader") else "")
                    + f"</span>"
                    # Cross-reference verdict badge
                    f"<span style='background:{_vc}22;border:1px solid {_vc};color:{_vc};"
                    f"padding:1px 8px;border-radius:10px;font-size:0.74em;font-weight:700'>{_vl}</span>"
                    # Conviction badge (composite-driven)
                    f"<span style='background:{_conv_clr}22;border:1px solid {_conv_clr};color:{_conv_clr};"
                    f"padding:1px 8px;border-radius:10px;font-size:0.74em;font-weight:700'>{_conv_txt}</span>"
                    f"</div>"
                    # Resolution one-liner — explicit verdict that reconciles
                    # momentum vs composite vs news vs earnings into one sentence.
                    + (f"<div style='color:{_vc};font-size:0.85em;margin-top:6px;"
                       f"font-weight:600'>→ {_v_one}</div>" if _v_one else "")
                    + f"<div style='color:#d1d5db;font-size:0.82em;margin-top:5px'>"
                    f"💡 <em>{_gp['thesis']}</em></div>"
                    + (f"<div style='color:#6b7280;font-size:0.78em;margin-top:4px'>"
                       f"📐 Suggested: {_sz.get('shares',0)} shares · "
                       f"Entry zone ${_sz.get('entry_lo', _gp['price']):.2f}–${_sz.get('entry_hi', _gp['price']):.2f} "
                       f"= ~${_sz.get('total_cost',0):,.0f} ({_sz.get('port_pct',0):.1f}% of portfolio) · "
                       f"Stop ~${_sz.get('stop',0):.2f} ({_sz.get('stop_pct',0):.0f}% below)"
                       f"</div>" if _sz else "")
                    + (f"<div style='color:#cbd5e1;font-size:0.78em;margin-top:6px;font-weight:600;background:#0f172a;border:1px solid #334155;border-radius:999px;padding:2px 10px;display:inline-block'>"
                       f"⏱ First surfaced: {_fmt_first_seen(_gp.get('_first_seen_at'))}"
                       f"</div>" if _gp.get("_first_seen_at") else "")
                    + f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button(f"▶ Analyze {_gp['ticker']}", key=f"_db_grow_{_gp['ticker']}"):
                    st.session_state["_pending_page"]    = "📈 Analysis"
                    st.session_state["_analysis_ticker"] = _gp["ticker"]
                    st.rerun()

            # Add-to-winner
            if add_pos:
                st.markdown("**➕ Add to Winning Positions**")
            for _ga in add_pos:
                _sz = _ga.get("sizing", {})
                st.markdown(
                    f"<div style='background:#052e16;border-left:3px solid #4ade80;"
                    f"border-radius:6px;padding:10px 14px;margin-bottom:6px'>"
                    f"<div style='display:flex;align-items:center;gap:10px;flex-wrap:wrap'>"
                    f"<span style='color:#f9fafb;font-weight:700'>{_ga['ticker']}</span>"
                    f"<span style='color:#9ca3af;font-size:0.8em'>{_ga['signal']} · "
                    f"Score {_ga['score']:.0f}/100 · P&L {_ga['pnl_pct']:+.1f}%"
                    + (f" 🔥 Sector leading" if _ga.get("is_leader") else "")
                    + f"</span></div>"
                    f"<div style='color:#d1d5db;font-size:0.82em;margin-top:5px'>"
                    f"💡 <em>{_ga['thesis']}</em></div>"
                    + (f"<div style='color:#6b7280;font-size:0.78em;margin-top:4px'>"
                       f"📐 Add: {_sz.get('shares',0)} shares ≈ ${_sz.get('total_cost',0):,.0f} "
                       f"· Stop ~${_sz.get('stop',0):.2f}</div>" if _sz else "")
                    + (f"<div style='color:#cbd5e1;font-size:0.78em;margin-top:6px;font-weight:600;background:#0f172a;border:1px solid #334155;border-radius:999px;padding:2px 10px;display:inline-block'>"
                       f"⏱ First surfaced: {_fmt_first_seen(_ga.get('_first_seen_at'))}"
                       f"</div>" if _ga.get("_first_seen_at") else "")
                    + f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button(f"▶ Analyze {_ga['ticker']}", key=f"_db_grow_add_{_ga['ticker']}"):
                    st.session_state["_pending_page"]    = "📈 Analysis"
                    st.session_state["_analysis_ticker"] = _ga["ticker"]
                    st.rerun()

            # Single-name concentration ceiling suppressed an add — surface why
            # so the user understands the position is capped, not signal-weak.
            if conc_blocked:
                _cn_rows = "".join(
                    f"<div style='color:#fcd34d;font-size:0.79em'>• <b>{b['ticker']}</b> "
                    f"({b.get('weight',0):.1f}% weight · Score {b.get('score',0):.0f}) — "
                    f"{b.get('reason','position at single-name ceiling')}</div>"
                    for b in conc_blocked[:3]
                )
                st.markdown(
                    "<div style='background:#422006;border:1px solid #f59e0b;"
                    "border-radius:8px;padding:8px 14px;margin:8px 0'>"
                    "<div style='color:#fbbf24;font-weight:700;font-size:0.84em;margin-bottom:4px'>"
                    f"🔒 Add Suppressed — Single-Name Ceiling ({int(SINGLE_NAME_CEILING)}%)</div>"
                    + _cn_rows
                    + "<div style='color:#fde68a;font-size:0.76em;margin-top:6px;font-style:italic'>"
                    "These winners qualify on signal but already exceed the institutional "
                    "single-name ceiling. Adding more would concentrate idiosyncratic risk. "
                    "Trim back to target before considering further adds."
                    "</div></div>",
                    unsafe_allow_html=True,
                )

            # Risk Advisor suppressed an add-to-winner — surface the conflict so the
            # user understands why a winning held position isn't on the add list.
            if blocked_adds:
                _reason_label = {
                    "beta":   "high portfolio beta",
                    "sharpe": "poor risk-adjusted return",
                }
                _rows = "".join(
                    f"<div style='color:#fcd34d;font-size:0.79em'>• <b>{b['ticker']}</b> "
                    f"(Strong Buy, Score {b.get('score',0):.0f}, P&L {b.get('pnl_pct',0):+.1f}%) — "
                    f"Risk Advisor flagged for trim due to "
                    f"{_reason_label.get(b.get('reason',''), b.get('reason',''))}.</div>"
                    for b in blocked_adds[:3]
                )
                st.markdown(
                    "<div style='background:#422006;border:1px solid #f59e0b;"
                    "border-radius:8px;padding:8px 14px;margin:8px 0'>"
                    "<div style='color:#fbbf24;font-weight:700;font-size:0.84em;margin-bottom:4px'>"
                    "🔀 Add-to-Winner Suppressed — Risk Advisor Conflict</div>"
                    + _rows
                    + "<div style='color:#fde68a;font-size:0.76em;margin-top:6px;font-style:italic'>"
                    "These positions qualify as winners but Risk Advisor recommends trimming them. "
                    "Adding more would compound the risk metric being flagged. "
                    "Resolve in Portfolio → Risk Advisor before adding.</div>"
                    "</div>",
                    unsafe_allow_html=True,
                )

            if deploy_note:
                st.info(f"💰 {deploy_note}")

            # ─────────────────────────────────────────────────────────────────
            # BELOW THE FOLD — "Here's what we considered but didn't recommend"
            # These blocks are informational, not actionable, so they live
            # below the action items. They tell the user WHY a hot ticker
            # didn't make the cut (composite said no, macro event imminent,
            # etc.) — important context but lower priority than what to act on.
            # ─────────────────────────────────────────────────────────────────

            # Macro-blocked picks — sector has imminent HIGH-impact catalyst.
            if macro_blocked:
                _mb_rows = "".join(
                    f"<div style='color:#fcd34d;font-size:0.79em'>• <b>{b['ticker']}</b> "
                    f"({b.get('sector','—')}, Score {b.get('score',0):.0f}) — {b.get('reason','')}</div>"
                    for b in macro_blocked[:4]
                )
                st.markdown(
                    "<div style='background:#422006;border:1px solid #f59e0b;"
                    "border-radius:8px;padding:8px 14px;margin-top:12px;margin-bottom:8px'>"
                    "<div style='color:#fbbf24;font-weight:700;font-size:0.84em;margin-bottom:4px'>"
                    "🌐 Picks Suppressed — Imminent HIGH-Impact Macro Event</div>"
                    + _mb_rows
                    + "<div style='color:#fde68a;font-size:0.76em;margin-top:6px;font-style:italic'>"
                    "These sectors have a HIGH-impact macro release within "
                    f"{MACRO_IMMINENT_DAYS} days. Opening fresh positions into a known binary "
                    "catalyst is the institutional anti-pattern this gate blocks. "
                    "Revisit after the event resolves and the dust settles."
                    "</div></div>",
                    unsafe_allow_html=True,
                )

            # Composite-conflict suppression — momentum was hot but the full
            # composite (Technical + Fundamental + Sentiment) was below the
            # Buy threshold. Surfaces what was considered AND rejected so the
            # user makes the decision on the Brief itself.
            if comp_skipped:
                _cs_rows = "".join(
                    f"<div style='color:#fca5a5;font-size:0.79em;margin-bottom:2px'>"
                    f"• <b>{c['ticker']}</b> ({c.get('sector','—')}) — "
                    f"Momentum <b>{c.get('momentum_score',0):.0f}/100</b> "
                    f"but composite <b>{c.get('composite_label','Hold')} "
                    f"{c.get('composite_score',0):.1f}/100</b> "
                    "→ skip (composite contradicts momentum)."
                    "</div>"
                    for c in comp_skipped[:5]
                )
                st.markdown(
                    "<div style='background:#3f1d1d;border:1px solid #ef4444;"
                    "border-radius:8px;padding:8px 14px;margin-top:8px;margin-bottom:4px'>"
                    "<div style='color:#fca5a5;font-weight:700;font-size:0.84em;margin-bottom:4px'>"
                    f"⛔ Screened but Filtered Out ({len(comp_skipped)}) — Composite Says No</div>"
                    + _cs_rows
                    + "<div style='color:#fecaca;font-size:0.76em;margin-top:6px;font-style:italic'>"
                    "Momentum (a single-factor breakout signal) caught these names, "
                    "but the full composite — Technical + Fundamental + Sentiment — "
                    "is below the Buy threshold (65). Decision: skip until composite "
                    "recovers. Track on the Watchlist for a re-look."
                    "</div></div>",
                    unsafe_allow_html=True,
                )

        # ── Two-column layout: Grow Today (left) | Act Today (right) ────────
        _db_col_left, _db_col_right = st.columns([1, 1])

        with _db_col_left:
            _render_grow_today(_db_grow, _db_tone)

        with _db_col_right:
            _db_c1_label  = f"🔴 Act Today ({len(_db_act)})" if _db_act else "🟢 Act Today — All Clear"
            _db_c1_color  = "#7f1d1d" if _db_act else "#14532d"
            _db_c1_border = "#ef4444" if _db_act else "#22c55e"
            st.markdown(
                f"<div style='background:{_db_c1_color};border-left:4px solid {_db_c1_border};"
                f"border-radius:8px;padding:10px 16px;margin-bottom:8px'>"
                f"<span style='font-size:1em;font-weight:700;color:#f9fafb'>{_db_c1_label}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if not _db_act:
                st.caption("No urgent actions required. Portfolio is within all risk parameters.")
            else:
                for _db_item in _db_act:
                    _db_is_crit = _db_item["priority"] == "critical"
                    _db_bg      = "#450a0a" if _db_is_crit else "#1c1917"
                    _db_border  = "#ef4444" if _db_is_crit else "#f59e0b"
                    _db_ticker  = _db_item.get("ticker")
                    _db_weight_txt = (
                        f" · {_db_item['weight']:.1f}% of portfolio"
                        if _db_item.get("weight") else ""
                    )
                    _db_pnl_txt = (
                        f" · P&L {_db_item['pnl_pct']:+.1f}%"
                        if _db_item.get("pnl_pct") is not None and _db_item.get("weight") else ""
                    )
                    st.markdown(
                        f"<div style='background:{_db_bg};border-left:3px solid {_db_border};"
                        f"border-radius:6px;padding:10px 14px;margin-bottom:6px'>"
                        f"<div style='color:#f9fafb;font-weight:600;font-size:0.88em'>"
                        f"{_db_item['icon']} {_db_item['action']}"
                        + (f" — <span style='color:#fbbf24'>{_db_ticker}</span>" if _db_ticker else "")
                        + f"<span style='color:#9ca3af;font-weight:400'>{_db_weight_txt}{_db_pnl_txt}</span>"
                        f"</div>"
                        f"<div style='color:#d1d5db;font-size:0.82em;margin-top:4px'>{_db_item['reason']}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if _db_ticker:
                        if st.button(f"▶ Analyze {_db_ticker}", key=f"_db_act_{_db_ticker}_{_db_item['action'][:10]}",
                                     use_container_width=False):
                            st.session_state["_pending_page"]    = "📈 Analysis"
                            st.session_state["_analysis_ticker"] = _db_ticker
                            st.rerun()

        st.markdown("<div style='margin-bottom:4px'></div>", unsafe_allow_html=True)

        # ── Section 2: Review Before Close (full width) ──────────────────────
        _db_c3_label = f"🟡 Review Before Close ({len(_db_review)})"
        st.markdown(
            f"<div style='background:#422006;border-left:4px solid #f59e0b;"
            f"border-radius:8px;padding:10px 16px;margin-bottom:8px'>"
            f"<span style='font-size:1em;font-weight:700;color:#f9fafb'>{_db_c3_label}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if not _db_review:
            st.caption("Nothing requiring pre-close review today.")
        else:
            # Trade Journal context lookup — pulled once for all review items so the
            # weak-large-position assessment is grounded in the original entry thesis
            # and any prior lessons, not just current weight/score.
            _db_trades = st.session_state.get("trades_df")

            def _journal_context(ticker: str) -> dict | None:
                if _db_trades is None or _db_trades.empty:
                    return None
                t = str(ticker).strip().upper()
                df = _db_trades[_db_trades["ticker"].astype(str).str.upper() == t]
                if df.empty:
                    return None
                df = df.sort_values("traded_at", ascending=False)
                # Entry thesis = notes on the most recent BUY (with non-empty notes)
                thesis, thesis_date = "", ""
                buys = df[df["action"].astype(str).str.upper() == "BUY"]
                for _, _row in buys.iterrows():
                    n = str(_row.get("notes") or "").strip()
                    if n and n.lower() != "nan":
                        thesis, thesis_date = n, str(_row.get("traded_at", ""))[:10]
                        break
                # Lessons = up to 2 most recent non-empty lesson entries
                lessons = []
                for _, _row in df.iterrows():
                    l = str(_row.get("lesson") or "").strip()
                    if l and l.lower() != "nan":
                        lessons.append({"text": l, "date": str(_row.get("traded_at", ""))[:10]})
                        if len(lessons) >= 2:
                            break
                if not thesis and not lessons:
                    return None
                return {"thesis": thesis, "thesis_date": thesis_date, "lessons": lessons}

            for _db_rev in _db_review:
                _db_border  = "#f59e0b" if _db_rev.get("priority") == "medium" else "#78716c"
                _db_bg      = "#1c1917"
                _db_ticker  = _db_rev.get("ticker")
                st.markdown(
                    f"<div style='background:{_db_bg};border-left:3px solid {_db_border};"
                    f"border-radius:6px;padding:10px 14px;margin-bottom:6px'>"
                    f"<div style='color:#f9fafb;font-weight:600;font-size:0.88em'>"
                    f"{_db_rev['icon']}"
                    + (f" <span style='color:#fbbf24'>{_db_ticker}</span>" if _db_ticker else "")
                    + f"</div>"
                    f"<div style='color:#d1d5db;font-size:0.82em;margin-top:4px'>{_db_rev['reason']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # Weak-large-position items get Trade Journal context injected so the
                # user can re-evaluate the position against their original reasoning,
                # not just the current weight/score metrics.
                if _db_rev.get("icon") == "🔍" and _db_ticker:
                    _jctx = _journal_context(_db_ticker)
                    if _jctx:
                        _parts = []
                        if _jctx.get("thesis"):
                            _t_clip = _jctx["thesis"][:240] + ("…" if len(_jctx["thesis"]) > 240 else "")
                            _date_tag = f" <span style='color:#78716c'>({_jctx['thesis_date']})</span>" if _jctx.get("thesis_date") else ""
                            _parts.append(
                                f"<div style='color:#fcd34d;font-size:0.78em;margin-top:2px'>"
                                f"📒 <b>Your entry thesis</b>{_date_tag}: "
                                f"<span style='color:#e7e5e4;font-style:italic'>“{_t_clip}”</span></div>"
                            )
                        for _ln in _jctx.get("lessons", []):
                            _l_clip = _ln["text"][:200] + ("…" if len(_ln["text"]) > 200 else "")
                            _l_date = f" <span style='color:#78716c'>({_ln['date']})</span>" if _ln.get("date") else ""
                            _parts.append(
                                f"<div style='color:#fcd34d;font-size:0.78em;margin-top:2px'>"
                                f"💡 <b>Lesson</b>{_l_date}: "
                                f"<span style='color:#e7e5e4;font-style:italic'>“{_l_clip}”</span></div>"
                            )
                        if _parts:
                            st.markdown(
                                f"<div style='background:#1c1917;border-left:3px solid #fbbf24;"
                                f"border-radius:6px;padding:8px 14px;margin:-4px 0 6px 0'>"
                                + "".join(_parts)
                                + f"<div style='color:#a8a29e;font-size:0.74em;margin-top:6px'>"
                                "Re-evaluate this large position against your original reasoning before trimming."
                                f"</div></div>",
                                unsafe_allow_html=True,
                            )
                    else:
                        st.markdown(
                            f"<div style='background:#1c1917;border-left:3px solid #57534e;"
                            f"border-radius:6px;padding:8px 14px;margin:-4px 0 6px 0;"
                            f"color:#a8a29e;font-size:0.76em;font-style:italic'>"
                            f"📒 No Trade Journal entry found for {_db_ticker}. "
                            "Log your entry thesis next time so weak-position reviews can be grounded in original reasoning."
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                if _db_ticker:
                    if st.button(f"▶ Analyze {_db_ticker}", key=f"_db_rev_{_db_ticker}_{_db_rev['icon']}",
                                 use_container_width=False):
                        st.session_state["_pending_page"]    = "📈 Analysis"
                        st.session_state["_analysis_ticker"] = _db_ticker
                        st.rerun()

        st.markdown("<div style='margin-bottom:4px'></div>", unsafe_allow_html=True)

        # ── Section 3: Buy Candidates (full width) ────────────────────────────
        _db_confirmed   = sum(1 for b in _db_buys if b.get("xref", {}).get("verdict") == "confirmed")
        _db_unverified  = sum(1 for b in _db_buys if b.get("xref", {}).get("verdict") == "unverified")
        _db_conflicted  = sum(1 for b in _db_buys if b.get("xref", {}).get("verdict") in ("conflicted", "caution", "mixed"))
        _db_c2_label    = f"🟢 Buy Candidates ({len(_db_buys)})"
        _db_c2_parts    = []
        if _db_confirmed:  _db_c2_parts.append(f"✅ {_db_confirmed} confirmed")
        if _db_unverified: _db_c2_parts.append(f"🔍 {_db_unverified} need verification")
        if _db_conflicted: _db_c2_parts.append(f"⚠️ {_db_conflicted} conflicted")
        _db_c2_sub = " · ".join(_db_c2_parts)
        st.markdown(
            f"<div style='background:#14532d;border-left:4px solid #22c55e;"
            f"border-radius:8px;padding:10px 16px;margin-bottom:4px'>"
            f"<span style='font-size:1em;font-weight:700;color:#f9fafb'>{_db_c2_label}</span>"
            + (f"<span style='color:#86efac;font-size:0.82em'> · {_db_c2_sub}</span>" if _db_c2_sub else "")
            + f"</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "📊 **Scanner = technical momentum only** (RSI, trend, price momentum). "
            "🔍 **Verify** picks via Analysis before acting — composite signal adds "
            "sentiment, analyst revisions, earnings risk and fundamentals."
        )
        if not _db_buys:
            st.caption("No scanner results available. Run Market Scanner to populate buy candidates.")
            if st.button("🔍 Go to Market Scanner", key="_db_to_scanner"):
                st.session_state["_pending_page"] = "🔍 Market Scanner"
                st.rerun()
        else:
            for _db_buy in _db_buys:
                _xref       = _db_buy.get("xref", {})
                _reconciled = _xref.get("verdict_reconciled", {}) or {}
                _vcolor     = _reconciled.get("color") or _xref.get("verdict_color", "#86efac")
                _vlabel     = _reconciled.get("label") or _xref.get("verdict_label", "")
                _v_one      = _reconciled.get("one_liner") or _xref.get("verdict_one_liner", "")
                _vagreed    = _xref.get("agreed", [])
                _vconflicts = _xref.get("conflicts", [])
                _vlayers    = _xref.get("layers_checked", 0)
                _db_bg      = "#1c1917"
                st.markdown(
                    f"<div style='background:{_db_bg};border-left:3px solid {_vcolor};"
                    f"border-radius:6px;padding:10px 14px;margin-bottom:4px'>"
                    f"<div style='display:flex;align-items:center;gap:10px;flex-wrap:wrap'>"
                    f"<span style='color:#f9fafb;font-weight:700;font-size:0.9em'>"
                    f"{_db_buy['icon']} {_db_buy['action']} — "
                    f"<span style='color:#fbbf24'>{_db_buy['ticker']}</span></span>"
                    f"<span style='color:#9ca3af;font-size:0.8em'>Score {_db_buy['score']:.0f}/100"
                    + (f" · {_db_buy.get('sector','')}" if _db_buy.get('sector') else "")
                    + f"</span>"
                    f"<span style='background:{_vcolor}22;border:1px solid {_vcolor};"
                    f"color:{_vcolor};padding:2px 10px;border-radius:12px;"
                    f"font-size:0.75em;font-weight:700;white-space:nowrap'>{_vlabel}</span>"
                    f"</div>"
                    # Resolution one-liner — replaces the diffuse "Verify" guidance
                    # block with the explicit reconciled verdict for this ticker.
                    + (f"<div style='color:{_vcolor};font-size:0.85em;margin-top:5px;"
                       f"font-weight:600'>→ {_v_one}</div>" if _v_one else "")
                    + (f"<div style='color:#9ca3af;font-size:0.78em;margin-top:4px'>"
                       f"📊 {_db_buy.get('scanner_signal','')} · "
                       f"RSI {_db_buy.get('rsi',0):.0f} · "
                       f"1M {_db_buy.get('mom_1m',0):+.1f}% · "
                       f"{_db_buy.get('trend','')}"
                       f"</div>" if _db_buy.get("rsi") else "")
                    + ("".join(
                        f"<div style='color:#fca5a5;font-size:0.8em;margin-top:3px'>⚠ {c}</div>"
                        for c in _vconflicts
                    ) if _vconflicts else "")
                    + (f"<div style='color:#6b7280;font-size:0.75em;margin-top:3px'>"
                       f"✓ {' · '.join(_vagreed[:3])}"
                       + (f" +{len(_vagreed)-3} more" if len(_vagreed) > 3 else "")
                       + f"</div>" if _vagreed else "")
                    + (f"<div style='color:#cbd5e1;font-size:0.78em;margin-top:6px;font-weight:600;background:#0f172a;border:1px solid #334155;border-radius:999px;padding:2px 10px;display:inline-block'>"
                       f"⏱ First surfaced: {_fmt_first_seen(_db_buy.get('_first_seen_at'))}"
                       f"</div>" if _db_buy.get("_first_seen_at") else "")
                    + f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button(f"▶ Analyze {_db_buy['ticker']}", key=f"_db_buy_{_db_buy['ticker']}",
                             use_container_width=False):
                    st.session_state["_pending_page"]    = "📈 Analysis"
                    st.session_state["_analysis_ticker"] = _db_buy["ticker"]
                    st.rerun()

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 1 — EVENING DEBRIEF
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_evening:
        _ed_now_et = datetime.now(_pytz.timezone("America/New_York"))
        _ed_hour   = _ed_now_et.hour + _ed_now_et.minute / 60
        _ed_full   = _ed_hour >= 15.5   # 3:30 PM ET — switch from preview to full debrief

        # ── Choose AM baseline: prefer the locked snapshot ──────────────────────
        _ed_baseline_brief: dict | None
        _ed_baseline_source = "none"
        _ed_baseline_at     = None
        if st.session_state.get("_brief_locked_snapshot") and \
                st.session_state.get("_brief_locked_for_date") == _TODAY_ET:
            _ed_baseline_brief  = st.session_state.get("_brief_locked_snapshot")
            _ed_baseline_source = "locked"
            _ed_baseline_at     = st.session_state.get("_brief_locked_at")
        elif _daily_brief and not st.session_state.get("_daily_brief_offline"):
            _ed_baseline_brief  = _daily_brief
            _ed_baseline_source = "live"
            _ed_baseline_at     = st.session_state.get("_brief_built_at")
        else:
            _ed_baseline_brief  = None

        # ── Fetch intraday % for all tickers mentioned in plan + skip picks ─────
        # Cached for 5 min so repeated tab opens don't hammer yfinance.
        @st.cache_data(ttl=300, show_spinner=False)
        def _ed_fetch_intraday(_tickers_key: tuple) -> dict:
            if not _tickers_key:
                return {}
            try:
                px = fetch_live_prices(list(_tickers_key))
                return {t: float(d.get("change_pct", 0)) for t, d in px.items()}
            except Exception:
                return {}

        _ed_tickers_needed: set[str] = set()
        if _ed_baseline_brief:
            _ed_grow = _ed_baseline_brief.get("grow_today") or {}
            for p in (_ed_grow.get("new_picks") or []):
                _ed_tickers_needed.add(str(p.get("ticker", "")))
            for p in (_ed_grow.get("add_positions") or []):
                _ed_tickers_needed.add(str(p.get("ticker", "")))
            for s in (_ed_grow.get("composite_skipped") or []):
                _ed_tickers_needed.add(str(s.get("ticker", "")))
            for b in (_ed_baseline_brief.get("buy_candidates") or []):
                _ed_tickers_needed.add(str(b.get("ticker", "")))
        _ed_tickers_needed.discard("")
        _ed_intraday_pct = _ed_fetch_intraday(tuple(sorted(_ed_tickers_needed)))

        # ── Build the debrief payload ───────────────────────────────────────────
        try:
            _ed_data = build_evening_debrief(
                brief               = _ed_baseline_brief,
                trades_df           = st.session_state.get("trades_df"),
                port_df             = port_df,
                held_data           = held_data,
                macro_events        = _macro_events,
                today               = _TODAY_ET,
                intraday_pct        = _ed_intraday_pct,
                am_baseline_source  = _ed_baseline_source,
                am_baseline_at      = _ed_baseline_at,
            )
        except Exception as _ed_e:
            st.error(f"Could not build Evening Debrief: {_ed_e}")
            _ed_data = None

        # ── Header strip ────────────────────────────────────────────────────────
        _ed_mode_label  = "🌙 Full Debrief" if _ed_full else "👀 Preview Mode"
        _ed_mode_bg     = "#1e1b4b" if _ed_full else "#1c1917"
        _ed_mode_bdr    = "#8b5cf6" if _ed_full else "#6b7280"
        _ed_mode_text   = (
            f"Market closes at 4:00 PM ET. This is the day's closing review."
            if _ed_full else
            f"Preview — full debrief unlocks at 3:30 PM ET ({(15.5 - _ed_hour):.1f}h from now). "
            "Numbers below are mid-day snapshots."
        )

        _ed_source_chip = {
            "locked": "🔒 AM plan: <b>Locked snapshot</b> — your actual baseline",
            "live":   "📌 AM plan: <b>Live Brief</b> (no lock) — may have drifted from open",
            "none":   "⚠ AM plan: <b>Unavailable</b> — no Brief built today",
        }.get(_ed_baseline_source, "")

        st.markdown(
            f"<div style='background:{_ed_mode_bg};border-left:4px solid {_ed_mode_bdr};"
            f"border-radius:8px;padding:12px 18px;margin-bottom:12px'>"
            f"<div style='color:#f9fafb;font-size:1.05em;font-weight:700'>{_ed_mode_label}</div>"
            f"<div style='color:#cbd5e1;font-size:0.85em;margin-top:2px'>{_ed_mode_text}</div>"
            f"<div style='color:#a5b4fc;font-size:0.82em;margin-top:6px'>{_ed_source_chip}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if _ed_data is None or _ed_baseline_brief is None:
            st.info(
                "No AM Brief available — open Today's Brief first to seed the debrief. "
                "The Evening Debrief reconciles your AM plan against the day's outcomes."
            )
        else:
            # ── Section 1: Plan vs Reality ──────────────────────────────────────
            _ed_pvr   = _ed_data["plan_vs_reality"]
            _ed_gos   = _ed_pvr["go_picks"]
            _ed_skips = _ed_pvr["skip_picks"]

            st.markdown("### 📋 Plan vs Reality")
            st.caption(
                "⏱ These verdicts reflect the AM snapshot — the composite, momentum, "
                "and sector context as of when the Brief was built (Lock the Brief in "
                "the morning to freeze this read deterministically). "
                "**A Go-pick here doesn't mean the signal still holds now** — composites "
                "drift through the day. Always re-run Analysis on a pick before acting "
                "post-AM."
            )
            _ed_pvr_c1, _ed_pvr_c2 = st.columns(2)

            with _ed_pvr_c1:
                st.markdown("**✅ Go-verdict picks (AM)**")
                if not _ed_gos:
                    st.caption("No Go-verdict picks in the AM read.")
                else:
                    for g in _ed_gos:
                        _tp     = g.get("today_pct")
                        _tp_str = f"{_tp:+.2f}%" if _tp is not None else "—"
                        _comp   = g.get("composite")
                        _comp_s = f"{_comp:.0f}" if isinstance(_comp, (int, float)) else "—"
                        _card_bg = "#052e16" if g["action_taken"] else "#1e293b"
                        _card_bdr = "#22c55e" if g["action_taken"] else "#475569"
                        _ed_fs   = _fmt_first_seen(g.get("_first_seen_at"))
                        st.markdown(
                            f"<div style='background:{_card_bg};border-left:3px solid {_card_bdr};"
                            f"border-radius:6px;padding:8px 12px;margin-bottom:6px'>"
                            f"<div style='color:#f9fafb;font-weight:700'>{g['ticker']} "
                            f"<span style='color:#94a3b8;font-size:0.82em;font-weight:400'>"
                            f"· {g.get('sector','—')} · Momentum {g['momentum']:.0f} · Composite {_comp_s}</span></div>"
                            f"<div style='color:#cbd5e1;font-size:0.85em;margin-top:3px'>{g['outcome']}</div>"
                            f"<div style='color:#94a3b8;font-size:0.78em;margin-top:2px'>"
                            f"Today: <b>{_tp_str}</b></div>"
                            + (f"<div style='color:#cbd5e1;font-size:0.78em;margin-top:6px;font-weight:600;"
                               f"background:#0f172a;border:1px solid #334155;border-radius:999px;"
                               f"padding:2px 10px;display:inline-block'>"
                               f"⏱ First surfaced: {_ed_fs}</div>" if _ed_fs else "")
                            + f"</div>",
                            unsafe_allow_html=True,
                        )

            with _ed_pvr_c2:
                st.markdown("**⛔ Skipped / Filtered Out (AM)**")
                if not _ed_skips:
                    st.caption("Nothing was filtered out today.")
                else:
                    for s in _ed_skips:
                        _tp     = s.get("today_pct")
                        _tp_str = f"{_tp:+.2f}%" if _tp is not None else "—"
                        _v      = s.get("verdict", "unknown")
                        _card_bdr = {
                            "validated": "#22c55e",
                            "missed":    "#f59e0b",
                            "flat":      "#475569",
                            "unknown":   "#475569",
                        }.get(_v, "#475569")
                        st.markdown(
                            f"<div style='background:#1e1b4b;border-left:3px solid {_card_bdr};"
                            f"border-radius:6px;padding:8px 12px;margin-bottom:6px'>"
                            f"<div style='color:#f9fafb;font-weight:700'>{s['ticker']} "
                            f"<span style='color:#94a3b8;font-size:0.82em;font-weight:400'>"
                            f"· {s.get('sector','—')} · Mom {s['momentum']:.0f} · "
                            f"Comp {s['composite']:.0f} {s.get('composite_label','')}</span></div>"
                            f"<div style='color:#cbd5e1;font-size:0.85em;margin-top:3px'>{s['outcome']}</div>"
                            f"<div style='color:#94a3b8;font-size:0.78em;margin-top:2px'>"
                            f"Today: <b>{_tp_str}</b></div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

            st.markdown("---")

            # ── Section 2: Today's Trades ───────────────────────────────────────
            _ed_trades = _ed_data["today_trades"]
            _ed_sum    = _ed_data["today_summary"]

            st.markdown("### 💼 Today's Trades")
            if not _ed_trades:
                st.caption("No trades recorded today.")
            else:
                _ed_sm_c1, _ed_sm_c2, _ed_sm_c3, _ed_sm_c4 = st.columns(4)
                _ed_sm_c1.metric("Trades", _ed_sum["n_trades"])
                _ed_sm_c2.metric("Capital Deployed",
                                 f"${_ed_sum['deployed']:,.0f}" if _ed_sum["deployed"] else "—")
                _ed_sm_c3.metric(
                    "Realized P&L",
                    f"${_ed_sum['realized_pnl']:+,.0f}",
                    delta=None,
                )
                _ed_sm_c4.metric(
                    "Signal Compliance",
                    f"{_ed_sum['n_followed']} ✓ / {_ed_sum['n_deviated']} ✗",
                )

                # Trade rows
                for t in _ed_trades:
                    _act_color = "#22c55e" if "BUY" in t["action"].upper() else "#ef4444"
                    _comp_chip = ""
                    if t.get("followed_signal") is True:
                        _comp_chip = "<span style='color:#86efac;font-size:0.78em'>✓ followed signal</span>"
                    elif t.get("followed_signal") is False:
                        _comp_chip = "<span style='color:#fca5a5;font-size:0.78em'>✗ deviated from signal</span>"
                    _rpnl_str = (
                        f" · Realized <b>${t['realized_pnl']:+,.0f}</b>"
                        if abs(t["realized_pnl"]) > 0 else ""
                    )
                    st.markdown(
                        f"<div style='background:#0f172a;border-left:3px solid {_act_color};"
                        f"border-radius:6px;padding:8px 12px;margin-bottom:6px'>"
                        f"<div style='color:#f9fafb;font-weight:600'>{t['action']} <b>{t['ticker']}</b> "
                        f"<span style='color:#94a3b8;font-weight:400'>· {t['shares']:.0f} sh @ ${t['price']:.2f}"
                        f"{_rpnl_str}</span></div>"
                        f"<div style='margin-top:3px'>{_comp_chip}</div>"
                        + (f"<div style='color:#cbd5e1;font-size:0.82em;margin-top:3px;font-style:italic'>"
                           f"{t['notes']}</div>" if t.get('notes') else "")
                        + "</div>",
                        unsafe_allow_html=True,
                    )

            st.markdown("---")

            # ── Section 3: Tomorrow's Setup ─────────────────────────────────────
            _ed_tom    = _ed_data["tomorrow_setup"]
            _ed_macros = _ed_tom["macro_tomorrow"]
            _ed_earns  = _ed_tom["earnings_imminent"]

            st.markdown(f"### 🌅 Tomorrow's Setup — {_ed_tom['tomorrow_date']}")

            _ed_t_c1, _ed_t_c2 = st.columns(2)
            with _ed_t_c1:
                st.markdown("**Macro events tomorrow**")
                if not _ed_macros:
                    st.caption("No scheduled macro releases tomorrow.")
                else:
                    for ev in _ed_macros:
                        _imp_color = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#94a3b8"}.get(
                            ev["impact"].upper(), "#94a3b8"
                        )
                        st.markdown(
                            f"<div style='background:#1c1917;border-left:3px solid {_imp_color};"
                            f"border-radius:6px;padding:8px 12px;margin-bottom:6px'>"
                            f"<div style='color:#f9fafb;font-weight:600'>{ev['event']} "
                            f"<span style='color:#94a3b8;font-weight:400'>· {ev['time']} · "
                            f"<span style='color:{_imp_color}'>{ev['impact']}</span></span></div>"
                            + (f"<div style='color:#cbd5e1;font-size:0.82em;margin-top:3px'>"
                               f"{ev['playbook']}</div>" if ev["playbook"] else "")
                            + "</div>",
                            unsafe_allow_html=True,
                        )
            with _ed_t_c2:
                st.markdown("**Held positions with earnings ≤ 7 days**")
                if not _ed_earns:
                    st.caption("No held positions reporting in the next week.")
                else:
                    for e in _ed_earns:
                        _days_color = "#ef4444" if e["days"] <= 2 else "#f59e0b" if e["days"] <= 4 else "#94a3b8"
                        st.markdown(
                            f"<div style='background:#1c1917;border-left:3px solid {_days_color};"
                            f"border-radius:6px;padding:8px 12px;margin-bottom:6px'>"
                            f"<div style='color:#f9fafb;font-weight:600'>{e['ticker']} "
                            f"<span style='color:#94a3b8;font-weight:400'>· "
                            f"<span style='color:{_days_color}'>{e['days']}d</span> · {e['date']}</span></div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 2 — OVERVIEW
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_ov:
        # Charts row
        ch1, ch2 = st.columns([1, 1])

        with ch1:
            # Allocation pie
            pie = go.Figure(go.Pie(
                labels=port_df["Ticker"],
                values=port_df["Market Value"],
                hole=0.45,
                textinfo="label+percent",
                marker=dict(
                    colors=["#00C851","#4a9eff","#ffbb33","#ff6b35","#aa00ff","#00bcd4","#ff4081"],
                ),
            ))
            pie.update_layout(
                title="Portfolio Allocation", template="plotly_dark",
                height=320, margin=dict(l=0, r=0, t=40, b=0),
                showlegend=False,
            )
            st.plotly_chart(pie, use_container_width=True)

        with ch2:
            # P&L bar
            colors = ["#00C851" if v >= 0 else "#ff4444" for v in port_df["P&L ($)"]]
            pnl_fig = go.Figure(go.Bar(
                x=port_df["Ticker"], y=port_df["P&L ($)"],
                marker_color=colors,
                text=[(f"••••<br>{p:+.1f}%" if st.session_state.get("_privacy") else f"${v:,.0f}<br>{p:+.1f}%")
                      for v, p in zip(port_df["P&L ($)"], port_df["P&L (%)"])],
                textposition="outside",
            ))
            pnl_fig.update_layout(
                title="P&L by Position", template="plotly_dark",
                height=320, yaxis_title="P&L ($)",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(pnl_fig, use_container_width=True)

        # Sector exposure — inline (no extra expander needed inside tab)
        sector_df = sector_exposure(port_df)
        if not sector_df.empty:
            st.subheader("Sector Exposure")
            sec_fig = go.Figure(go.Bar(
                x=sector_df["Sector"], y=sector_df["Pct"],
                marker_color="#4a9eff",
                text=[f"{v:.0f}%" for v in sector_df["Pct"]],
                textposition="outside",
            ))
            sec_fig.add_hline(y=40, line_dash="dash", line_color="red",
                              annotation_text="40% concentration limit")
            sec_fig.update_layout(
                template="plotly_dark", height=260,
                yaxis_title="% of Portfolio",
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(sec_fig, use_container_width=True)

        # Position table
        st.subheader("Position Detail & Protective Stops")
        st.caption(
            "Hover the ℹ️ icon on any column header below for a plain-English explanation.  \n"
            "**Ratchet stop** moves up automatically as gains grow — locks in profits while letting winners run.  \n"
            "**Score** = Technical 45% + Fundamental 40% + Sentiment 15% (composite). "
            "Scanner uses momentum-only scoring — see drill-down for full breakdown."
        )

        def _pnl_color(val):
            if isinstance(val, (int, float)):
                return "color:#00C851;font-weight:bold" if val > 0 else "color:#ff4444"
            return ""

        def _stop_color(val):
            if isinstance(val, str):
                if "Protect" in val: return "color:#00C851"
                if "Breakeven" in val: return "color:#ffbb33"
            return ""

        def _sig_color(val):
            s = str(val)
            if "Strong Buy" in s: return "background:#00C85122;color:#00C851"
            if "Buy" in s:        return "color:#00b300"
            if "Sell" in s:       return "color:#ff4444"
            return ""

        display_cols = ["Ticker", "Shares", "Avg Cost", "Price", "Market Value",
                        "P&L ($)", "P&L (%)", "Weight (%)",
                        "Stop", "Stop Type", "Gap to Stop (%)", "Signal", "Score"]
        _tbl = port_df[display_cols].copy()
        if st.session_state.get("_privacy"):
            for _mc in ["Avg Cost", "Market Value", "P&L ($)"]:
                _tbl[_mc] = "••••••"
            styled = (
                _tbl.style
                .map(_pnl_color, subset=["P&L (%)"])
                .map(_stop_color, subset=["Stop Type"])
                .map(_sig_color,  subset=["Signal"])
                .format({
                    "Price":           "${:.2f}",
                    "P&L (%)":         "{:+.1f}%",
                    "Weight (%)":      "{:.1f}%",
                    # Stop/Gap may be None when stop data is unavailable —
                    # show "—" rather than "nan" to surface the data gap clearly.
                    "Stop":            lambda v: f"${v:.2f}"  if pd.notna(v) else "—",
                    "Gap to Stop (%)": lambda v: f"{v:.1f}%" if pd.notna(v) else "—",
                    "Score":           "{:.0f}",
                })
            )
        else:
            styled = (
                _tbl.style
                .map(_pnl_color, subset=["P&L ($)", "P&L (%)"])
                .map(_stop_color, subset=["Stop Type"])
                .map(_sig_color,  subset=["Signal"])
                .format({
                    "Avg Cost":        "${:.2f}",
                    "Price":           "${:.2f}",
                    "Market Value":    "${:,.0f}",
                    "P&L ($)":         "${:,.0f}",
                    "P&L (%)":         "{:+.1f}%",
                    "Weight (%)":      "{:.1f}%",
                    "Stop":            lambda v: f"${v:.2f}"  if pd.notna(v) else "—",
                    "Gap to Stop (%)": lambda v: f"{v:.1f}%" if pd.notna(v) else "—",
                    "Score":           "{:.0f}",
                })
            )
        st.dataframe(styled, use_container_width=True)
        _sig_ts = st.session_state.get("_signals_computed_at", "")
        st.caption(
            f"Signals & scores computed at load time"
            + (f" ({_sig_ts})" if _sig_ts else "")
            + " · live prices refresh every 60s · scores do not update between refreshes"
        )

        # Per-position drill-down
        st.subheader("Position Drill-Down")
        sel = st.selectbox("Select position to drill down", port_df["Ticker"].tolist())
        if sel and sel in held_data:
            r = held_data[sel]
            price = r["current_price"]
            targets = r["targets"]
            ps_row = port_df[port_df["Ticker"] == sel].iloc[0]

            # ── Trade actions ────────────────────────────────────────────────
            # Two buttons so the user can transact either side from this surface
            # (the user explicitly asked for sell/buy from the position table).
            # Defaults are signal-aware: composite Sell → SELL primary, composite
            # Buy → BUY primary. signal_context pre-fills the Trade Journal's
            # Decision Context block so we capture *why* the trade was made.
            _ps_sig   = str(ps_row.get("Signal", "")).strip()
            _ps_score = ps_row.get("Score")
            _ps_shares = float(ps_row.get("Shares", 0))
            _ps_signal_ctx = (
                f"{_ps_sig} · Score {_ps_score:.0f}/100"
                if _ps_sig and _ps_score is not None and pd.notna(_ps_score)
                else _ps_sig or ""
            )
            _ps_is_sell_signal = "Sell" in _ps_sig or "Avoid" in _ps_sig
            _trc1, _trc2, _trc3 = st.columns([1, 1, 2])
            with _trc1:
                _render_trade_button(
                    ticker=sel,
                    suggested_action="SELL",
                    signal_context=_ps_signal_ctx,
                    price=price,
                    shares=_ps_shares,
                    trigger="MANUAL",
                    followed_intent=("yes" if _ps_is_sell_signal else None),
                    key_suffix="drill_sell",
                    label=f"📒 Sell {sel}",
                    use_container_width=True,
                )
            with _trc2:
                _render_trade_button(
                    ticker=sel,
                    suggested_action="BUY",
                    signal_context=_ps_signal_ctx,
                    price=price,
                    trigger="MANUAL",
                    followed_intent=("yes" if "Buy" in _ps_sig else None),
                    key_suffix="drill_buy",
                    label=f"📒 Add to {sel}",
                    use_container_width=True,
                )
            with _trc3:
                st.caption(
                    f"Current signal: **{_ps_sig or '—'}**"
                    + (f" · {_ps_score:.0f}/100" if _ps_score is not None and pd.notna(_ps_score) else "")
                    + " — pre-fills the Trade Journal's Decision Context so you don't re-type it."
                )

            d1, d2, d3, d4 = st.columns(4)
            # Stop and Gap may be None when stop data is unavailable (data
                # integrity gate) — show "—" rather than crashing on .format.
            _ps_stop = ps_row.get('Stop')
            _ps_gap  = ps_row.get('Gap to Stop (%)')
            d1.metric("P&L",         _m(f"${ps_row['P&L ($)']:,.0f}"), f"{ps_row['P&L (%)']:+.1f}%")
            d2.metric("Stop Loss",
                      f"${_ps_stop:.2f}" if _ps_stop is not None else "—",
                      ps_row['Stop Type'],
                      help=_tip("ATR Stop"))
            d3.metric("Gap to Stop",
                      f"{_ps_gap:.1f}%" if _ps_gap is not None else "—",
                      help=_tip("Ratchet Stop"))
            d4.metric("Composite Score", f"{r['total']:.0f}/100",  r['rec']['label'],
                      help=_tip("Composite Score"))

            # Score breakdown row
            sb1, sb2, sb3 = st.columns(3)
            t_contrib  = round(r['t_score'] * 0.45, 1)
            f_contrib  = round(r['f_score'] * 0.40, 1)
            s_contrib  = round(r['s_score'] * 0.15, 1)
            sb1.metric("Technical",   f"{r['t_score']:.0f}/100", f"+{t_contrib} pts (45%)",
                       help="RSI · MACD · Bollinger Bands · MA trend · Volume\n\n"
                            + _tip("RSI"))
            sb2.metric("Fundamental", f"{r['f_score']:.0f}/100", f"+{f_contrib} pts (40%)",
                       help="Forward P/E · FCF Yield · Revenue & Earnings growth · Margins · Debt/Equity\n\n"
                            + _tip("FCF Yield"))
            sb3.metric("Sentiment",   f"{r['s_score']:.0f}/100", f"+{s_contrib} pts (15%)",
                       help="VADER analysis of latest news headlines from Yahoo Finance")

            # Smart Money panel
            fin = r["financials"]
            rev = r.get("revisions", {})
            _sm_items = []

            short_pct = fin.get("short_pct_float")
            if short_pct is not None:
                short_clr = "#ff4444" if short_pct > 15 else ("#ffbb33" if short_pct > 7 else "#00C851")
                _sm_items.append(("Short Interest", f"{short_pct:.1f}% of float", short_clr, _tip("Short Interest")))
            short_ratio = fin.get("short_ratio")
            if short_ratio:
                _sm_items.append(("Days to Cover", f"{short_ratio:.1f}d", "#aaa", _tip("Days to Cover")))
            inst = fin.get("held_pct_institutions")
            if inst is not None:
                inst_clr = "#00C851" if inst > 60 else ("#ffbb33" if inst > 30 else "#ff4444")
                _sm_items.append(("Institutional", f"{inst:.0f}%", inst_clr, _tip("Institutional Ownership")))
            insider = fin.get("held_pct_insiders")
            if insider is not None:
                _sm_items.append(("Insider Held", f"{insider:.1f}%", "#aaa", _tip("Insider Ownership")))
            fcf_y = fin.get("fcf_yield")
            if fcf_y is not None:
                fcf_clr = "#00C851" if fcf_y >= 4 else ("#ffbb33" if fcf_y >= 1 else "#ff4444")
                _sm_items.append(("FCF Yield", f"{fcf_y:.1f}%", fcf_clr, _tip("FCF Yield")))
            if rev:
                net = rev.get("net", 0)
                rev_lbl = f"+{net} upgrades" if net > 0 else (f"{net} downgrades" if net < 0 else "neutral")
                rev_clr = "#00C851" if net > 0 else ("#ff4444" if net < 0 else "#888")
                _sm_items.append(("Revisions 90d", rev_lbl, rev_clr, _tip("Analyst Revisions")))

            if _sm_items:
                st.markdown("**Smart Money Signals**")
                _sm_cols = st.columns(len(_sm_items))
                for _col, (_label, _val, _clr, _help) in zip(_sm_cols, _sm_items):
                    _col.metric(_label, _val, help=_help)

            # Source links
            st.markdown(
                f"**Sources:** "
                f"[📊 Yahoo Finance](https://finance.yahoo.com/quote/{sel}) · "
                f"[📈 Finviz Chart](https://finviz.com/quote.ashx?t={sel}) · "
                f"[📰 SEC Filings](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={sel}&type=10-K) · "
                f"[🔍 Latest News](https://finance.yahoo.com/quote/{sel}/news/)"
            )

            if targets:
                st.markdown(
                    f"**Scenarios** · Bear `${targets['bear']:.2f} ({targets['bear_pct']:+.1f}%)` · "
                    f"Base `${targets['base']:.2f} ({targets['base_pct']:+.1f}%)` · "
                    f"Bull `${targets['bull']:.2f} ({targets['bull_pct']:+.1f}%)`"
                )

            # Mini price chart with stop line
            df = r["df"]
            mini = go.Figure()
            mini.add_trace(go.Scatter(
                x=df.index, y=df["Close"], name="Price",
                line=dict(color="#4a9eff", width=2), fill="tozeroy",
                fillcolor="rgba(74,158,255,0.08)",
            ))
            if r["stop"]:
                mini.add_hline(y=r["stop"], line_dash="dashdot", line_color="#ff6600",
                               annotation_text=f"Stop ${r['stop']:.2f}",
                               annotation_position="right")
            if targets:
                mini.add_hline(y=targets["base"], line_dash="dash", line_color="#00C851",
                               annotation_text=f"Target ${targets['base']:.2f}",
                               annotation_position="right")
            mini.update_layout(
                height=240, template="plotly_dark", showlegend=False,
                xaxis_rangeslider_visible=False,
                margin=dict(l=0, r=80, t=10, b=0),
            )
            st.plotly_chart(mini, use_container_width=True)

        # ── News Intelligence ─────────────────────────────────────────────────
        st.divider()
        _ni_data = build_news_intelligence(
            st.session_state.get("_sidebar_news", []), port_df
        )
        _ni_sum   = _ni_data.get("summary", {})
        _ni_alts  = _ni_data.get("alerts", [])
        _ni_opps  = _ni_data.get("opportunities", [])
        _ni_sects = _ni_data.get("sector_digest", [])
        _ni_held  = _ni_data.get("held_news", [])

        # Expander title shows alert count so user sees urgency at a glance
        _ni_title_badge = (
            f"🚨 {len(_ni_alts)} alert{'s' if len(_ni_alts) != 1 else ''} · "
            if _ni_alts else ""
        )
        _ni_expander_title = (
            f"📰 News Intelligence  ·  {_ni_title_badge}"
            f"{_ni_sum.get('positive', 0)} ▲  "
            f"{_ni_sum.get('negative', 0)} ▼  "
            f"{_ni_sum.get('neutral', 0)} –  "
            f"({_ni_sum.get('held_count', 0)} for your holdings)"
        )
        with st.expander(_ni_expander_title, expanded=bool(_ni_alts)):

            # ── Summary KPI row ───────────────────────────────────────────
            _nic1, _nic2, _nic3, _nic4 = st.columns(4)
            _nic1.metric("Total stories",    _ni_sum.get("total",      0))
            _nic2.metric("For holdings",     _ni_sum.get("held_count", 0))
            _nic3.metric("🚨 Alerts",        len(_ni_alts),
                         delta="Requires attention" if _ni_alts else None,
                         delta_color="inverse" if _ni_alts else "off")
            _nic4.metric("📈 Opportunities", len(_ni_opps),
                         delta="Positive signals" if _ni_opps else None,
                         delta_color="normal" if _ni_opps else "off")

            # ── Alerts ────────────────────────────────────────────────────
            if _ni_alts:
                st.markdown("#### 🚨 Requires Attention")
                st.caption(
                    "Negative news on positions you currently hold, ranked by "
                    "position size × sentiment strength × source credibility."
                )
                for _al in _ni_alts:
                    _al_border = "#ef4444" if _al["alert_level"] == "critical" else "#f59e0b"
                    _al_bg     = "#1a0000" if _al["alert_level"] == "critical" else "#1a1000"
                    _al_tag    = "🔴 CRITICAL" if _al["alert_level"] == "critical" else "🟡 WATCH"
                    _al_url    = _al.get("url", "")
                    _al_link   = (
                        f"<a href='{_al_url}' target='_blank' "
                        f"style='color:#ddd;text-decoration:none'>{_html.escape(_al['title'][:90])}"
                        f"{'…' if len(_al['title']) > 90 else ''}</a>"
                        if _al_url else
                        f"<span style='color:#ddd'>{_html.escape(_al['title'][:90])}</span>"
                    )
                    st.markdown(
                        f"<div style='background:{_al_bg};border-left:4px solid {_al_border};"
                        f"border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:8px'>"
                        f"<div style='font-size:0.72em;color:{_al_border};font-weight:700;"
                        f"margin-bottom:4px'>{_al_tag}  ·  "
                        f"<b style='color:#bbb'>{_al['ticker']}</b>  ·  "
                        f"{_al['weight']:.1f}% weight  ·  "
                        f"P&L {_al['pnl_pct']:+.1f}%  ·  "
                        f"Score {_al['score']:.0f}/100  ·  "
                        f"{_al.get('publisher', '')[:20]}  ·  "
                        f"{_time_ago(_al.get('ts', 0))}</div>"
                        f"<div style='font-size:0.84em'>{_al_link}</div>"
                        f"<div style='font-size:0.72em;color:#666;margin-top:5px'>"
                        f"Sector: {_al['sector']}  ·  Signal: {_al['signal']}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(f"▶ Analyze {_al['ticker']}", key=f"_ni_analyze_{_al['ticker']}_{_al.get('ts',0)}"):
                        st.session_state["_pending_page"]    = "📈 Analysis"
                        st.session_state["_analysis_ticker"] = _al["ticker"]
                        st.rerun()

            # ── Opportunities ─────────────────────────────────────────────
            if _ni_opps:
                st.markdown("#### 📈 Opportunity Signals")
                st.caption(
                    "Positive news on quality positions you already hold. "
                    "These may support adding on a pullback — not a signal to chase the gap."
                )
                for _op in _ni_opps[:5]:
                    _op_url  = _op.get("url", "")
                    _op_link = (
                        f"<a href='{_op_url}' target='_blank' "
                        f"style='color:#ddd;text-decoration:none'>{_html.escape(_op['title'][:90])}"
                        f"{'…' if len(_op['title']) > 90 else ''}</a>"
                        if _op_url else
                        f"<span style='color:#ddd'>{_html.escape(_op['title'][:90])}</span>"
                    )
                    st.markdown(
                        f"<div style='background:#001a08;border-left:4px solid #00C851;"
                        f"border-radius:0 6px 6px 0;padding:10px 14px;margin-bottom:8px'>"
                        f"<div style='font-size:0.72em;color:#00C851;font-weight:700;"
                        f"margin-bottom:4px'>📈 SIGNAL  ·  "
                        f"<b style='color:#bbb'>{_op['ticker']}</b>  ·  "
                        f"{_op['weight']:.1f}% weight  ·  "
                        f"P&L {_op['pnl_pct']:+.1f}%  ·  "
                        f"Score {_op['score']:.0f}/100  ·  "
                        f"{_op.get('publisher', '')[:20]}  ·  "
                        f"{_time_ago(_op.get('ts', 0))}</div>"
                        f"<div style='font-size:0.84em'>{_op_link}</div>"
                        f"<div style='font-size:0.72em;color:#666;margin-top:5px'>"
                        f"Sector: {_op['sector']}  ·  Signal: {_op['signal']}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(f"▶ Analyze {_op['ticker']}", key=f"_ni_opp_{_op['ticker']}_{_op.get('ts',0)}"):
                        st.session_state["_pending_page"]    = "📈 Analysis"
                        st.session_state["_analysis_ticker"] = _op["ticker"]
                        st.rerun()

            # ── Sector patterns ───────────────────────────────────────────
            if _ni_sects:
                st.markdown("#### ⚠️ Sector Patterns")
                st.caption(
                    "Multiple news items pointing the same direction for a sector "
                    "often precede rotation. Use as context — not a standalone signal."
                )
                for _sd in _ni_sects:
                    _sd_clr = "#ef4444" if _sd["direction"] == "negative" else "#00C851"
                    _sd_icon = "📉" if _sd["direction"] == "negative" else "📈"
                    _sd_word = "negative" if _sd["direction"] == "negative" else "positive"
                    st.markdown(
                        f"<div style='background:#111;border-left:3px solid {_sd_clr};"
                        f"border-radius:0 4px 4px 0;padding:8px 12px;margin-bottom:6px'>"
                        f"<span style='color:{_sd_clr};font-weight:700'>{_sd_icon} {_sd['sector']}</span>"
                        f"  ·  <span style='color:#bbb'>{_sd['count']} {_sd_word} stories this session</span>"
                        f"{'  ·  ⚠️ Sector rotation risk' if _sd['direction'] == 'negative' else ''}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    for _si in _sd["items"][:3]:
                        st.caption(f"  ↳ [{_si['ticker']}] {_si['title'][:80]}{'…' if len(_si['title']) > 80 else ''}")

            # ── All portfolio news ────────────────────────────────────────
            if _ni_held:
                st.markdown("#### 📰 All News for Your Holdings")
                for _hn in _ni_held:
                    _hn_clr  = ("#00C851" if _hn["compound"] >=  0.05 else
                                 "#ef4444" if _hn["compound"] <= -0.05 else "#555")
                    _hn_icon = ("▲" if _hn["compound"] >= 0.05 else
                                "▼" if _hn["compound"] <= -0.05 else "–")
                    _hn_url  = _hn.get("url", "")
                    _hn_link = (
                        f"<a href='{_hn_url}' target='_blank' "
                        f"style='color:#bbb;text-decoration:none'>{_html.escape(_hn['title'][:85])}"
                        f"{'…' if len(_hn['title']) > 85 else ''}</a>"
                        if _hn_url else
                        f"<span style='color:#bbb'>{_html.escape(_hn['title'][:85])}</span>"
                    )
                    st.markdown(
                        f"<div style='padding:5px 0;border-bottom:1px solid #1a1a1a'>"
                        f"<span style='color:{_hn_clr};font-weight:bold'>{_hn_icon}</span>  "
                        f"<b style='color:#999;font-size:0.85em'>{_hn['ticker']}</b>  "
                        f"<span style='color:#555;font-size:0.78em'>"
                        f"{_hn['weight']:.1f}% · P&L {_hn['pnl_pct']:+.1f}% · "
                        f"{_hn.get('publisher','')[:18]} · {_time_ago(_hn.get('ts',0))}"
                        f"</span><br>"
                        f"<span style='font-size:0.82em'>{_hn_link}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            elif not _ni_alts and not _ni_opps:
                st.info(
                    "No news found for your current holdings yet. "
                    "News populates after the portfolio loads — try refreshing.",
                    icon="📭",
                )

        # ── Rebalancing Advisor ───────────────────────────────────────────────
        st.divider()
        st.markdown("### ⚖️ Rebalancing Advisor")
        st.caption(
            "Shows how far each position has drifted from its target weight. "
            "Set targets manually or use equal weight as the baseline. "
            "Tolerance: ±2% = OK  ·  2–5% = Watch  ·  >5% = Action needed."
        )

        # Target weight mode
        _rb_mode = st.radio(
            "Target weight method",
            ["Equal Weight", "Custom Targets"],
            horizontal=True, key="_rb_mode",
        )

        _n_pos = len(port_df)
        if _rb_mode == "Equal Weight":
            _target_weights = equal_weights(port_df)
        else:
            # Editable target weights table
            st.caption(
                "Edit the **Target (%)** column. Targets don't need to sum to exactly 100% — "
                "the drift calculation is per-position vs your stated target."
            )
            _eq = equal_weights(port_df)
            _saved_targets = st.session_state.get("_rb_custom_targets", {})

            # Seed number-input session state from saved targets on first load
            for _, _rrow in port_df.iterrows():
                _t = _rrow["Ticker"]
                _default = round(_saved_targets.get(_t, _eq.get(_t, round(100 / _n_pos, 1))), 1)
                if f"_rb_tgt_{_t}" not in st.session_state:
                    st.session_state[f"_rb_tgt_{_t}"] = _default

            # Header
            _rh1, _rh2, _rh3, _rh4 = st.columns([2, 2, 2, 2])
            _rh1.markdown("**Ticker**"); _rh2.markdown("**Sector**")
            _rh3.markdown("**Current (%)**"); _rh4.markdown("**Target (%)**")

            for _, _rrow in port_df.iterrows():
                _t = _rrow["Ticker"]
                _rc1, _rc2, _rc3, _rc4 = st.columns([2, 2, 2, 2])
                _rc1.markdown(f"**{_t}**")
                _rc2.markdown(str(_rrow.get("Sector", "")))
                _rc3.markdown(f"{round(float(_rrow.get('Weight (%)') or 0), 1):.1f}%")
                _rc4.number_input(
                    "target", key=f"_rb_tgt_{_t}",
                    min_value=0.0, max_value=100.0, step=0.5, format="%.1f",
                    label_visibility="collapsed",
                )

            _target_weights = {
                _t: float(st.session_state.get(f"_rb_tgt_{_t}", _eq.get(_t, round(100 / _n_pos, 1))))
                for _t in port_df["Ticker"]
            }
            st.session_state["_rb_custom_targets"] = _target_weights

        # Compute drift
        _drift_df = compute_drift(port_df, _target_weights, total_val)

        # Cross-reference active negative-news flags on held tickers so the
        # rebalancer never silently recommends adding to a position News
        # Intelligence is currently flagging. Use the most negative recent flag
        # per ticker (alerts are pre-sorted by severity in build_news_intelligence).
        _rb_news_flags: dict = {}
        for _alert in (_ni_alts or []):
            _atk = str(_alert.get("ticker", "")).strip().upper()
            if not _atk or _atk in _rb_news_flags:
                continue
            _rb_news_flags[_atk] = {
                "level":    _alert.get("alert_level", "warning"),
                "headline": _alert.get("headline", ""),
                "compound": _alert.get("compound", 0.0),
            }

        # Build Risk Advisor trim set so Rebalancer ADD can never contradict
        # an investment-level reduce-exposure recommendation.
        _rb_risk_trim_set: set = set()
        for _rrec in (_risk_advisor_recs or []):
            if _rrec.get("priority") not in ("HIGH", "MEDIUM"):
                continue
            if _rrec.get("type") not in ("beta", "sharpe"):
                continue
            for _rt in _rrec.get("root_tickers", []):
                _tk = _rt.get("ticker")
                if _tk:
                    _rb_risk_trim_set.add(str(_tk).upper())

        _rb_plan  = build_rebalance_plan(
            _drift_df, total_val,
            news_flags=_rb_news_flags,
            risk_trim_set=_rb_risk_trim_set,
        )

        # KPI summary
        _rb_k1, _rb_k2, _rb_k3, _rb_k4 = st.columns(4)
        _n_trim  = len(_rb_plan["trims"])
        _n_add   = len(_rb_plan["adds"])
        _n_ok    = len(_rb_plan["ok"]) + sum(1 for _, r in _drift_df.iterrows() if r["Status"] == "WATCH")
        _rb_k1.metric("Trim needed",    _n_trim,
                      delta="Action required" if _n_trim else None,
                      delta_color="inverse" if _n_trim else "off")
        _rb_k2.metric("Add needed",     _n_add)
        _rb_k3.metric("In tolerance",   _n_ok)
        _rb_k4.metric("Portfolio moved", f"{_rb_plan['rebalance_pct']:.1f}%",
                      help="% of portfolio value touched if all actions executed")

        # Drift bar chart
        if not _drift_df.empty:
            import plotly.graph_objects as _go_rb
            _dc = ["#ff4444" if d > TOLERANCE_WATCH else
                   ("#ffbb33" if d > TOLERANCE_OK else
                   ("#4a9eff" if d < -TOLERANCE_OK else "#888888"))
                   for d in _drift_df["Drift (pp)"]]
            _rb_fig = _go_rb.Figure(_go_rb.Bar(
                x=_drift_df["Ticker"],
                y=_drift_df["Drift (pp)"],
                marker_color=_dc,
                text=[f"{d:+.1f}pp" for d in _drift_df["Drift (pp)"]],
                textposition="outside",
                customdata=list(zip(
                    _drift_df["Current (%)"],
                    _drift_df["Target (%)"],
                    _drift_df["Drift Value ($)"],
                    _drift_df["Status"],
                )),
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "Current: %{customdata[0]:.1f}%<br>"
                    "Target: %{customdata[1]:.1f}%<br>"
                    "Drift: %{y:+.1f}pp<br>"
                    "$ Drift: $%{customdata[2]:+,.0f}<br>"
                    "Status: %{customdata[3]}"
                    "<extra></extra>"
                ),
            ))
            _rb_fig.add_hline(y=TOLERANCE_OK,    line_dash="dash", line_color="#888",
                              annotation_text=f"+{TOLERANCE_OK:.0f}pp tolerance",
                              annotation_position="right")
            _rb_fig.add_hline(y=-TOLERANCE_OK,   line_dash="dash", line_color="#888",
                              annotation_position="right")
            _rb_fig.add_hline(y=TOLERANCE_WATCH,  line_dash="dot", line_color="#ffbb33",
                              annotation_text=f"+{TOLERANCE_WATCH:.0f}pp action",
                              annotation_position="right")
            _rb_fig.add_hline(y=-TOLERANCE_WATCH, line_dash="dot", line_color="#4a9eff",
                              annotation_position="right")
            _rb_fig.update_layout(
                title="Weight Drift vs Target (pp = percentage points)",
                template="plotly_dark", height=300,
                yaxis_title="Drift (pp)",
                margin=dict(l=0, r=100, t=40, b=0),
            )
            st.plotly_chart(_rb_fig, use_container_width=True)
            st.caption(
                "🔴 Red = overweight >5pp (trim)  ·  "
                "🟡 Amber = overweight 2–5pp (watch)  ·  "
                "🔵 Blue = underweight >2pp (consider adding)  ·  "
                "⬛ Gray = within ±2pp tolerance"
            )

        # Trim recommendations
        if _rb_plan["trims"]:
            st.markdown("#### ✂️ Trim Actions (Overweight)")
            for _tr in _rb_plan["trims"]:
                _tr_pri  = "HIGH" if (_tr["urgency"] >= 40 or _tr["status"] == "TRIM") else "MEDIUM"
                _tr_icon = "🔴" if _tr_pri == "HIGH" else "🟡"
                _tr_bclr = "#ff4444" if _tr_pri == "HIGH" else "#ffbb33"
                _tr_exp  = _tr["urgency"] >= 40

                with st.expander(
                    f"{_tr_icon} **{_tr['ticker']}** — trim {_tr['drift_pp']:+.1f}pp  "
                    f"| sell ~{_tr['shares_delta']:,} shares ≈ ${_tr['drift_val']:,.0f}",
                    expanded=_tr_exp,
                ):
                    _tr_m = st.columns(4)
                    _tr_m[0].metric("Current Weight",  f"{_tr['current_pct']:.1f}%")
                    _tr_m[1].metric("Target Weight",   f"{_tr['target_pct']:.1f}%")
                    _tr_m[2].metric("Drift",           f"{_tr['drift_pp']:+.1f}pp",
                                    delta_color="inverse")
                    _tr_m[3].metric("$ to Trim",       f"${_tr['drift_val']:,.0f}")

                    st.markdown(
                        f"<div style='padding:10px 14px;background:#1a1a1a;"
                        f"border-radius:6px;border-left:4px solid {_tr_bclr};margin:8px 0'>"
                        f"<span style='color:#eee'>{_tr['rationale']}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div style='padding:10px 14px;background:#0d2137;"
                        f"border-radius:6px;border-left:4px solid #4a9eff;margin:6px 0'>"
                        f"<span style='font-size:0.72em;color:#4a9eff;font-weight:700;"
                        f"letter-spacing:0.09em;text-transform:uppercase'>Action</span><br>"
                        f"<span style='color:#eee;font-size:0.9em'>{_tr['action_detail']}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.info(f"**Institutional Lens** · {_tr['institutional_lens']}")

        # Risk Advisor TRIM suppression — show before adds so the user sees
        # the conflict before the un-suppressed adds.
        _rb_risk_blocked = _rb_plan.get("risk_blocked_adds") or []
        if _rb_risk_blocked:
            _rba_rows = "".join(
                f"<div style='color:#fcd34d;font-size:0.82em'>• <b>{a['ticker']}</b> "
                f"(currently {a.get('current_pct',0):.1f}% → target {a.get('target_pct',0):.1f}%, "
                f"drift {a.get('drift_pp',0):+.1f}pp, Score {a.get('score',0):.0f}) — "
                f"{a.get('reason','')}</div>"
                for a in _rb_risk_blocked[:5]
            )
            st.markdown(
                "<div style='background:#422006;border:1px solid #f59e0b;"
                "border-radius:8px;padding:8px 14px;margin:10px 0'>"
                "<div style='color:#fbbf24;font-weight:700;font-size:0.88em;margin-bottom:4px'>"
                "🔒 Rebalance ADD Suppressed — Risk Advisor Conflict</div>"
                + _rba_rows
                + "<div style='color:#fde68a;font-size:0.78em;margin-top:6px;font-style:italic'>"
                "Adding to a position the investment view is currently telling you to TRIM is a "
                "direct contradiction. Resolve the trim in Risk Advisor first."
                "</div></div>",
                unsafe_allow_html=True,
            )

        # Add recommendations
        if _rb_plan["adds"]:
            # Header summary — how many ADDs are competing with active news flags?
            _n_news_blocked = _rb_plan.get("news_blocked_adds", 0)
            _header = "#### ➕ Add Actions (Underweight)"
            if _n_news_blocked:
                _header += (
                    f"  ·  <span style='color:#fbbf24;font-size:0.65em;font-weight:600'>"
                    f"⚠ {_n_news_blocked} flagged by News Intelligence — review before executing</span>"
                )
                st.markdown(_header, unsafe_allow_html=True)
            else:
                st.markdown(_header)
            for _ad in _rb_plan["adds"]:
                _ad_exp  = _ad["urgency"] >= 40 or bool(_ad.get("news_warning"))
                _ad_icon = "💪" if _ad["score"] >= 65 else "👁️"
                _nw      = _ad.get("news_warning")
                # Title icon override — surface news flag in the collapsed header
                if _nw:
                    _ad_icon = "⛔" if _nw.get("level") == "critical" else "⚠️"

                with st.expander(
                    f"{_ad_icon} **{_ad['ticker']}** — add {_ad['drift_pp']:+.1f}pp  "
                    f"| buy ~{_ad['shares_delta']:,} shares ≈ ${_ad['drift_val']:,.0f}"
                    + ("  ·  ACTIVE NEWS FLAG" if _nw else ""),
                    expanded=_ad_exp,
                ):
                    _ad_m = st.columns(4)
                    _ad_m[0].metric("Current Weight",  f"{_ad['current_pct']:.1f}%")
                    _ad_m[1].metric("Target Weight",   f"{_ad['target_pct']:.1f}%")
                    _ad_m[2].metric("Drift",           f"{_ad['drift_pp']:+.1f}pp")
                    _ad_m[3].metric("$ to Add",        f"${_ad['drift_val']:,.0f}")

                    # News Intelligence cross-flag — render above rationale so the
                    # user sees the conflict before reading the buy thesis.
                    if _nw:
                        _nw_critical = _nw.get("level") == "critical"
                        _nw_bg  = "#3f1d1d" if _nw_critical else "#3b2a0a"
                        _nw_brd = "#ef4444" if _nw_critical else "#f59e0b"
                        _nw_txt = "#fca5a5" if _nw_critical else "#fcd34d"
                        _nw_lbl = (
                            "🚨 News Intelligence Conflict — Defer Add"
                            if _nw_critical else
                            "📰 News Intelligence Caution"
                        )
                        st.markdown(
                            f"<div style='padding:10px 14px;background:{_nw_bg};"
                            f"border-radius:6px;border-left:4px solid {_nw_brd};margin:8px 0'>"
                            f"<span style='font-size:0.72em;color:{_nw_brd};font-weight:700;"
                            f"letter-spacing:0.09em;text-transform:uppercase'>{_nw_lbl}</span><br>"
                            f"<span style='color:{_nw_txt};font-size:0.88em'>{_nw['message']}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    st.markdown(
                        f"<div style='padding:10px 14px;background:#1a1a1a;"
                        f"border-radius:6px;border-left:4px solid #4a9eff;margin:8px 0'>"
                        f"<span style='color:#eee'>{_ad['rationale']}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div style='padding:10px 14px;background:#0d2137;"
                        f"border-radius:6px;border-left:4px solid #4a9eff;margin:6px 0'>"
                        f"<span style='font-size:0.72em;color:#4a9eff;font-weight:700;"
                        f"letter-spacing:0.09em;text-transform:uppercase'>Action</span><br>"
                        f"<span style='color:#eee;font-size:0.9em'>{_ad['action_detail']}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.info(f"**Institutional Lens** · {_ad['institutional_lens']}")

        # In-tolerance positions
        if _rb_plan["ok"]:
            with st.expander(f"✅ {len(_rb_plan['ok'])} position(s) within tolerance", expanded=False):
                st.caption(", ".join(_rb_plan["ok"]) + " — no action needed.")

        if not _rb_plan["trims"] and not _rb_plan["adds"]:
            st.success(
                "✅ All positions are within tolerance of their target weights. "
                "No rebalancing needed."
            )

        # ── Sentiment Velocity ────────────────────────────────────────────────
        st.divider()
        st.markdown("### 📰 Sentiment Momentum")
        st.caption(
            "Rate of change in news sentiment — recent 7 days vs prior 8–30 days. "
            "A deteriorating sentiment score that diverges from a rising price is one of "
            "the earliest warning signals of a coming reversal."
        )

        try:
            _sv_data = build_sentiment_dashboard(port_df, held_data)
        except Exception as _sve:
            _sv_data = []
            st.warning(f"Sentiment velocity unavailable: {_sve}")

        if _sv_data:
            # Summary KPI strip
            _sv_divg  = [v for v in _sv_data if v["divergence"]]
            _sv_imprv = [v for v in _sv_data if v["direction"] == "Improving ↑"]
            _sv_detr  = [v for v in _sv_data if v["direction"] == "Deteriorating ↓"]
            _sv_k1, _sv_k2, _sv_k3, _sv_k4 = st.columns(4)
            _sv_k1.metric("⚠️ Divergences",    len(_sv_divg),
                          delta="Price ≠ Sentiment" if _sv_divg else None,
                          delta_color="inverse" if _sv_divg else "off")
            _sv_k2.metric("✅ Improving",       len(_sv_imprv))
            _sv_k3.metric("🔴 Deteriorating",   len(_sv_detr),
                          delta="Monitor closely" if _sv_detr else None,
                          delta_color="inverse" if _sv_detr else "off")
            _sv_k4.metric("Stable →",
                          len(_sv_data) - len(_sv_imprv) - len(_sv_detr))

            # Summary table
            _sv_rows = []
            for _sv in _sv_data:
                _sv_rows.append({
                    "Ticker":        _sv["ticker"],
                    "Recent Sent.":  _sv["recent_score"],
                    "Prior Sent.":   _sv["prior_score"],
                    "Velocity":      _sv["velocity"],
                    "Direction":     _sv["direction"],
                    "Price 7d (%)":  _sv["price_ret_7d"],
                    "Signal":        _sv["signal"],
                })
            _sv_df = pd.DataFrame(_sv_rows)

            def _sv_row_style(row):
                sig = str(row.get("Signal", ""))
                vel = row.get("Velocity")
                if "Divergence" in sig and "BEARISH" in sig or "price rising but sentiment falling" in sig:
                    return ["background-color:rgba(255,68,68,0.12)"] * len(row)
                if "Divergence" in sig:
                    return ["background-color:rgba(74,158,255,0.10)"] * len(row)
                if vel is not None and vel < -0.12:
                    return ["background-color:rgba(255,187,51,0.08)"] * len(row)
                return [""] * len(row)

            st.dataframe(
                _sv_df.style.apply(_sv_row_style, axis=1).format({
                    "Recent Sent.":  lambda v: f"{v:+.3f}" if v is not None else "—",
                    "Prior Sent.":   lambda v: f"{v:+.3f}" if v is not None else "—",
                    "Velocity":      lambda v: f"{v:+.3f}" if v is not None else "—",
                    "Price 7d (%)":  lambda v: f"{v:+.1f}%" if v is not None else "—",
                }),
                use_container_width=True, hide_index=True,
            )
            st.caption(
                "Sentiment score: VADER compound −1.0 (very negative) → +1.0 (very positive).  "
                "Velocity = recent 7-day score minus prior 8–30-day score.  "
                "🔴 Highlighted = deteriorating · Red row = bearish divergence · Blue row = bullish divergence."
            )

            # Notable signal cards
            _sv_notable = [v for v in _sv_data
                           if v["divergence"] or v["direction"] in ("Improving ↑", "Deteriorating ↓")]
            if _sv_notable:
                st.markdown("#### Notable Sentiment Signals")
                for _sv in _sv_notable:
                    _vel     = _sv["velocity"]
                    _divg    = _sv["divergence"]
                    _dtype   = _sv["divergence_type"]
                    _dir     = _sv["direction"]
                    _p7d     = _sv["price_ret_7d"]

                    if _divg and _dtype == "BEARISH":
                        _sv_icon, _sv_bclr, _sv_pri = "⚠️", "#ff4444", "HIGH"
                    elif _divg and _dtype == "BULLISH":
                        _sv_icon, _sv_bclr, _sv_pri = "🔄", "#4a9eff", "MONITOR"
                    elif _dir == "Deteriorating ↓":
                        _sv_icon, _sv_bclr, _sv_pri = "🔴", "#ffbb33", "MEDIUM"
                    else:
                        _sv_icon, _sv_bclr, _sv_pri = "✅", "#00C851", "OK"

                    _expand = _sv_pri in ("HIGH", "MEDIUM")

                    with st.expander(
                        f"{_sv_icon} **{_sv['ticker']}** — {_sv['signal']}  "
                        f"| Velocity {_vel:+.3f}" if _vel is not None else
                        f"{_sv_icon} **{_sv['ticker']}** — {_sv['signal']}",
                        expanded=_expand,
                    ):
                        _sv_m = st.columns(4)
                        _sv_m[0].metric("Recent Sentiment",
                                        f"{_sv['recent_score']:+.3f}" if _sv["recent_score"] is not None else "—",
                                        help="Avg VADER compound score, last 7 days of news")
                        _sv_m[1].metric("Prior Sentiment",
                                        f"{_sv['prior_score']:+.3f}" if _sv["prior_score"] is not None else "—",
                                        help="Avg VADER compound score, 8–30 days ago")
                        _sv_m[2].metric("Velocity",
                                        f"{_vel:+.3f}" if _vel is not None else "—",
                                        delta="Improving" if (_vel and _vel > 0.12) else
                                              ("Deteriorating" if (_vel and _vel < -0.12) else None),
                                        delta_color="normal" if (_vel and _vel > 0) else "inverse")
                        _sv_m[3].metric("Price 7d",
                                        f"{_p7d:+.1f}%" if _p7d is not None else "—")

                        # Divergence or velocity interpretation
                        if _divg and _dtype == "BEARISH":
                            st.markdown(
                                f"<div style='padding:10px 14px;background:#1a0a0a;"
                                f"border-radius:6px;border-left:4px solid #ff4444;margin:8px 0'>"
                                f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                                f"letter-spacing:0.09em;text-transform:uppercase'>⚠️ Bearish Divergence</span><br>"
                                f"<span style='color:#eee'>"
                                f"Price is up <b>{_p7d:+.1f}%</b> over 7 days but news sentiment has "
                                f"shifted <b>{_vel:+.3f}</b> points — the market is rising on momentum "
                                f"while the information environment is getting worse. "
                                f"This combination frequently precedes a sharp pullback once the "
                                f"sentiment shift reaches price. Monitor closely and tighten the stop."
                                f"</span></div>",
                                unsafe_allow_html=True,
                            )
                            st.info(
                                "**Institutional Lens** · Price-sentiment divergence is a classic early warning. "
                                "Quantitative strategists include sentiment momentum as a factor "
                                "in their reversal models — a stock rising while its news flow deteriorates "
                                "has historically underperformed the following month by 3–5% on average. "
                                "This is not a sell signal in isolation, but it warrants raising the stop "
                                "and watching the next earnings or catalyst carefully."
                            )
                        elif _divg and _dtype == "BULLISH":
                            st.markdown(
                                f"<div style='padding:10px 14px;background:#0a0d1a;"
                                f"border-radius:6px;border-left:4px solid #4a9eff;margin:8px 0'>"
                                f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                                f"letter-spacing:0.09em;text-transform:uppercase'>🔄 Bullish Divergence</span><br>"
                                f"<span style='color:#eee'>"
                                f"Price is down <b>{_p7d:+.1f}%</b> over 7 days but news sentiment has "
                                f"improved <b>{_vel:+.3f}</b> points — the market is selling on momentum "
                                f"while the information environment is improving. "
                                f"Potential recovery setup — watch for price to catch up with sentiment."
                                f"</span></div>",
                                unsafe_allow_html=True,
                            )
                            st.info(
                                "**Institutional Lens** · A stock falling while sentiment improves can signal "
                                "an over-reaction to short-term price pressure. Institutional contrarian "
                                "indicators flag this as a potential mean-reversion opportunity — "
                                "particularly if the composite score remains above 55 and the fundamental "
                                "thesis is intact. Confirm with the next news cycle before adding."
                            )
                        elif _dir == "Deteriorating ↓":
                            st.markdown(
                                f"<div style='padding:10px 14px;background:#1a1200;"
                                f"border-radius:6px;border-left:4px solid #ffbb33;margin:8px 0'>"
                                f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                                f"letter-spacing:0.09em;text-transform:uppercase'>Sentiment Shift</span><br>"
                                f"<span style='color:#eee'>"
                                f"News sentiment has shifted <b>{_vel:+.3f}</b> points recently. "
                                f"Sentiment often leads price by 1–2 weeks — watch whether this "
                                f"translates to price pressure."
                                f"</span></div>",
                                unsafe_allow_html=True,
                            )
                        elif _dir == "Improving ↑":
                            st.markdown(
                                f"<div style='padding:10px 14px;background:#0a1a0a;"
                                f"border-radius:6px;border-left:4px solid #00C851;margin:8px 0'>"
                                f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                                f"letter-spacing:0.09em;text-transform:uppercase'>Positive Momentum</span><br>"
                                f"<span style='color:#eee'>"
                                f"News sentiment has improved <b>{_vel:+.3f}</b> points recently — "
                                f"a positive information environment building ahead of price."
                                f"</span></div>",
                                unsafe_allow_html=True,
                            )

                        # Recent headlines
                        if _sv["headline_sample"]:
                            st.markdown("**Recent headlines (last 7 days)**")
                            for _hl in _sv["headline_sample"]:
                                _hl_clr = "#00C851" if _hl["score"] > 0.05 else (
                                          "#ff4444" if _hl["score"] < -0.05 else "#888")
                                st.markdown(
                                    f"<div style='font-size:0.82em;color:#bbb;margin:3px 0'>"
                                    f"<span style='color:{_hl_clr};font-weight:bold'>"
                                    f"[{_hl['score']:+.2f}]</span> {_hl['title']}"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )

        # ── Tax Efficiency Advisor ────────────────────────────────────────────
        st.divider()
        st.markdown("### 💰 Tax Efficiency Advisor")
        st.caption(
            "Estimates the tax impact of selling each position today vs waiting for "
            "long-term capital gains treatment (>365 days held). "
            "Holding periods sourced from your Trade Journal — positions not yet logged show 'Unknown'."
        )

        # Tax bracket selector
        _tx_col1, _tx_col2 = st.columns([2, 1])
        with _tx_col1:
            _tx_bracket = st.radio(
                "Your tax bracket",
                ["🟢 Low  (22% STCG / 15% LTCG)",
                 "🟡 Medium  (32% STCG / 15% LTCG)",
                 "🔴 High  (37% STCG / 20% LTCG)"],
                index=2, horizontal=True, key="_tx_bracket",
            )
        _tx_rates = {
            "🟢 Low  (22% STCG / 15% LTCG)":    (0.22, 0.15),
            "🟡 Medium  (32% STCG / 15% LTCG)":  (0.32, 0.15),
            "🔴 High  (37% STCG / 20% LTCG)":    (0.37, 0.20),
        }
        _stcg_r, _ltcg_r = _tx_rates[_tx_bracket]

        _trades_for_tax = st.session_state.get("trades_df", pd.DataFrame())
        try:
            _tax = build_tax_analysis(port_df, _trades_for_tax, _stcg_r, _ltcg_r)
        except Exception as _txe:
            _tax = {}
            st.warning(f"Tax analysis unavailable: {_txe}")

        if _tax and _tax.get("rows"):
            # Portfolio-level KPI strip
            _tx_k1, _tx_k2, _tx_k3, _tx_k4, _tx_k5 = st.columns(5)
            _tx_k1.metric("STCG Unrealized",    f"${_tax['total_stcg_gain']:,.0f}",
                          help=f"Taxed at {_stcg_r*100:.0f}% if sold today")
            _tx_k2.metric("LTCG Unrealized",    f"${_tax['total_ltcg_gain']:,.0f}",
                          help=f"Taxed at {_ltcg_r*100:.0f}% — already long-term")
            _tx_k3.metric("Harvestable Losses", f"${_tax['total_harvestable']:,.0f}",
                          help="Unrealized losses that could offset gains")
            _tx_k4.metric("Tax Bill Today",     f"${_tax['tax_today']:,.0f}",
                          help="Estimated federal tax if all positions sold now")
            _sav = _tax["tax_savings"]
            _tx_k5.metric("Savings by Waiting", f"${_sav:,.0f}",
                          delta="Wait for LTCG" if _sav > 500 else None,
                          delta_color="normal" if _sav > 500 else "off",
                          help="Extra tax saved if STCG positions wait for long-term treatment")

            # Position table
            _tx_table_rows = []
            for _tr in _tax["rows"]:
                _tx_table_rows.append({
                    "Ticker":         _tr["ticker"],
                    "Unrealized P&L": _tr["pnl"],
                    "Held (days)":    _tr["days_held"] if _tr["days_held"] is not None else "—",
                    "Type":           _tr["gain_type"],
                    "Days to LTCG":   _tr["days_to_ltcg"] if _tr["days_to_ltcg"] else "—",
                    "Tax Today ($)":  _tr["tax_if_sold_today"],
                    "Tax at LTCG ($)": _tr["tax_if_ltcg"],
                    "Savings ($)":    _tr["tax_savings"],
                    "Action":         _tr["action"],
                })
            _tx_df = pd.DataFrame(_tx_table_rows)

            def _tx_row_style(row):
                a = str(row.get("Action", ""))
                if a == "HARVEST":
                    return ["background-color:rgba(74,158,255,0.10)"] * len(row)
                if a == "WAIT":
                    return ["background-color:rgba(0,200,81,0.10)"] * len(row)
                if a == "HOLD_FOR_LTCG":
                    return ["background-color:rgba(255,187,51,0.07)"] * len(row)
                return [""] * len(row)

            def _tx_type_style(val):
                if val == "STCG": return "color:#ffbb33;font-weight:bold"
                if val == "LTCG": return "color:#00C851;font-weight:bold"
                return "color:#888"

            st.dataframe(
                _tx_df.style
                    .apply(_tx_row_style, axis=1)
                    .map(_tx_type_style, subset=["Type"])
                    .format({
                        "Unrealized P&L":  lambda v: f"${v:+,.0f}" if isinstance(v, (int,float)) else v,
                        "Tax Today ($)":   lambda v: f"${v:,.0f}"  if isinstance(v, (int,float)) and v > 0 else "—",
                        "Tax at LTCG ($)": lambda v: f"${v:,.0f}"  if isinstance(v, (int,float)) and v > 0 else "—",
                        "Savings ($)":     lambda v: f"${v:,.0f}"  if isinstance(v, (int,float)) and v > 0 else "—",
                    }),
                use_container_width=True, hide_index=True,
            )
            st.caption(
                "🟡 **STCG** = held ≤365 days — short-term rate applies  ·  "
                "🟢 **LTCG** = held >365 days — preferential rate  ·  "
                "⬛ **Unknown** = no BUY record in Trade Journal for this position  ·  "
                "Estimates are federal tax only — state taxes additional."
            )

            # Action cards for notable situations
            _harvest_rows = [r for r in _tax["rows"] if r["action"] == "HARVEST"]
            _blocked_rows = [r for r in _tax["rows"] if r["action"] == "HOLD_FOR_SIGNAL"]
            _wait_rows    = [r for r in _tax["rows"] if r["action"] == "WAIT"]
            _ltcg_rows    = [r for r in _tax["rows"] if r["action"] == "HOLD_FOR_LTCG"]

            # Suppressed harvests — surface so the user knows a tax-loss-eligible
            # position is intentionally NOT being recommended for harvest because
            # the investment view is still Buy/Strong Buy on it.
            if _blocked_rows:
                _bh_rows = "".join(
                    f"<div style='color:#fcd34d;font-size:0.79em'>• <b>{r['ticker']}</b> "
                    f"(loss ${r['pnl']:+,.0f}, signal: {r.get('signal','')}) — "
                    f"investment view subordinates the tax opportunity.</div>"
                    for r in _blocked_rows[:5]
                )
                st.markdown(
                    "<div style='background:#1c1917;border:1px solid #f59e0b;"
                    "border-radius:8px;padding:8px 14px;margin:8px 0'>"
                    "<div style='color:#fbbf24;font-weight:700;font-size:0.84em;margin-bottom:4px'>"
                    "🔒 Harvest Suppressed — Investment View Holds</div>"
                    + _bh_rows
                    + "<div style='color:#fde68a;font-size:0.76em;margin-top:6px;font-style:italic'>"
                    "These positions have a harvestable loss, but the composite signal is "
                    "still Buy or Strong Buy. The institutional principle is that tax savings "
                    "do not override the investment view — taking the harvest here exits a "
                    "high-conviction position to capture a tax benefit, trading known savings "
                    "for unknown opportunity cost. Revisit if signal degrades to Hold or worse."
                    "</div></div>",
                    unsafe_allow_html=True,
                )

            if _harvest_rows:
                st.markdown("#### 🔵 Tax Loss Harvesting Opportunities")
                for _hr in _harvest_rows:
                    with st.expander(
                        f"🔵 **{_hr['ticker']}** — harvest ${_hr['harvestable']:,.0f} loss  "
                        f"| saves ~${_hr['harvestable'] * _stcg_r:,.0f} in taxes on other gains",
                        expanded=True,
                    ):
                        _hc = st.columns(3)
                        _hc[0].metric("Unrealized Loss",   f"${_hr['pnl']:+,.0f}")
                        _hc[1].metric("Tax Offset Value",  f"~${_hr['harvestable'] * _stcg_r:,.0f}",
                                      help=f"Loss × {_stcg_r*100:.0f}% STCG rate")
                        _hc[2].metric("Held",
                                      f"{_hr['days_held']}d" if _hr["days_held"] else "Unknown")
                        st.markdown(
                            f"<div style='padding:10px 14px;background:#0a0d1a;"
                            f"border-radius:6px;border-left:4px solid #4a9eff;margin:8px 0'>"
                            f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                            f"letter-spacing:0.09em;text-transform:uppercase'>Tax Loss Harvesting</span><br>"
                            f"<span style='color:#eee'>"
                            f"Selling <b>{_hr['ticker']}</b> realises a <b>${_hr['harvestable']:,.0f}</b> loss. "
                            f"At your {_stcg_r*100:.0f}% rate, this offsets approximately "
                            f"<b>${_hr['harvestable'] * _stcg_r:,.0f}</b> in taxes on other gains. "
                            f"<b>⚠️ Wash sale rule:</b> do not repurchase {_hr['ticker']} or a "
                            f"substantially identical security within 30 days before or after the sale — "
                            f"the IRS will disallow the loss deduction."
                            f"</span></div>",
                            unsafe_allow_html=True,
                        )
                        st.info(
                            "**Institutional Lens** · Tax loss harvesting is one of the highest-certainty "
                            "alpha sources available — it doesn't require predicting the market. "
                            "Tax-aware institutional strategies harvest losses systematically throughout "
                            "the year, not just in December. The key discipline: replace the sold "
                            "position with a correlated but not identical ETF or name to maintain "
                            "market exposure while the 30-day wash sale window passes."
                        )

            if _wait_rows:
                st.markdown("#### 🟢 Wait for LTCG — Threshold Close")
                for _wr2 in _wait_rows:
                    with st.expander(
                        f"🟢 **{_wr2['ticker']}** — LTCG in {_wr2['days_to_ltcg']} days  "
                        f"| saves ${_wr2['tax_savings']:,.0f} by waiting",
                        expanded=True,
                    ):
                        _wc = st.columns(4)
                        _wc[0].metric("Unrealized Gain",  f"${_wr2['pnl']:+,.0f}")
                        _wc[1].metric("Days to LTCG",     f"{_wr2['days_to_ltcg']}d")
                        _wc[2].metric("Tax if Sold Now",  f"${_wr2['tax_if_sold_today']:,.0f}")
                        _wc[3].metric("Tax Savings",      f"${_wr2['tax_savings']:,.0f}",
                                      delta="Wait", delta_color="normal")
                        st.markdown(
                            f"<div style='padding:10px 14px;background:#0a1a0a;"
                            f"border-radius:6px;border-left:4px solid #00C851;margin:8px 0'>"
                            f"<span style='color:#eee'>"
                            f"Waiting <b>{_wr2['days_to_ltcg']} more days</b> before selling "
                            f"<b>{_wr2['ticker']}</b> saves <b>${_wr2['tax_savings']:,.0f}</b> "
                            f"in federal taxes — the gain shifts from {_stcg_r*100:.0f}% to "
                            f"{_ltcg_r*100:.0f}% treatment. "
                            f"Unless the investment thesis has broken, this is almost always "
                            f"worth the wait."
                            f"</span></div>",
                            unsafe_allow_html=True,
                        )
                        st.info(
                            "**Institutional Lens** · The LTCG threshold is one of the most valuable "
                            "and underused tools in portfolio management. Institutional PMs always flag "
                            "positions within 60 days of the 1-year mark — selling before the "
                            f"threshold costs {(_stcg_r - _ltcg_r)*100:.0f} percentage points of "
                            "extra tax with zero investment rationale. "
                            "The only reason to sell before LTCG eligibility is a broken thesis — "
                            "not a preference for cash or a desire to lock in gains."
                        )

            if _ltcg_rows:
                with st.expander(
                    f"🟡 {len(_ltcg_rows)} position(s) working toward LTCG — monitor", expanded=False
                ):
                    for _lr in _ltcg_rows:
                        st.markdown(
                            f"**{_lr['ticker']}** — {_lr['days_to_ltcg']}d to LTCG · "
                            f"gain ${_lr['pnl']:+,.0f} · "
                            f"tax saving ${_lr['tax_savings']:,.0f} by waiting"
                        )

            if not _harvest_rows and not _wait_rows and not _ltcg_rows:
                st.success(
                    "✅ No immediate tax efficiency actions. "
                    "All gains are already LTCG-eligible, or positions have no unrealized gains."
                )

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 2 — PERFORMANCE VS SPY
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_perf:
        _pc1, _pc2 = st.columns([3, 1])
        _pc1.markdown("### Portfolio Performance vs S&P 500")
        _perf_period = _pc2.radio(
            "Period", ["1M", "3M", "6M"], horizontal=True,
            index=1, key="_perf_period", label_visibility="collapsed",
        )
        _period_days = {"1M": 21, "3M": 63, "6M": 126}
        _n_days = _period_days[_perf_period]

        try:
            _spy_hist  = fetch_spy("6mo")
            _spy_close = _spy_hist["Close"]
            if _spy_close.index.tz is not None:
                _spy_close.index = _spy_close.index.tz_localize(None)

            _closes = {}
            for _, _row in port_df.iterrows():
                _t = _row["Ticker"]
                if _t in held_data and not held_data[_t]["df"].empty:
                    _c = held_data[_t]["df"]["Close"].copy()
                    if _c.index.tz is not None:
                        _c.index = _c.index.tz_localize(None)
                    _closes[_t] = _c

            if _closes:
                _common = _spy_close.index
                for _s in _closes.values():
                    _common = _common.intersection(_s.index)
                _common = sorted(_common)[-_n_days:]

                _weights_s = port_df.set_index("Ticker")["Weight (%)"] / 100
                _port_ret  = pd.Series(0.0, index=_common)
                _total_w   = 0.0
                for _t, _c in _closes.items():
                    _w = float(_weights_s.get(_t, 0))
                    if _w <= 0:
                        continue
                    _aligned = _c.reindex(_common).ffill().bfill()
                    if _aligned.empty or _aligned.iloc[0] == 0:
                        continue
                    _port_ret += (_aligned / _aligned.iloc[0] - 1) * 100 * _w
                    _total_w  += _w
                if _total_w > 0:
                    _port_ret = _port_ret / _total_w

                _spy_s   = _spy_close.reindex(_common).ffill().bfill()
                _spy_ret = (_spy_s / _spy_s.iloc[0] - 1) * 100

                _port_final = float(_port_ret.iloc[-1])
                _spy_final  = float(_spy_ret.iloc[-1])
                _alpha      = _port_final - _spy_final
                _beating    = _alpha >= 0
                _port_clr   = "#00C851" if _beating else "#ff6b6b"
                _fill_clr   = "rgba(0,200,81,0.08)" if _beating else "rgba(255,100,100,0.08)"
                _alpha_sign = "+" if _beating else ""

                # ── KPI row ──────────────────────────────────────────────────
                _km1, _km2, _km3 = st.columns(3)
                _km1.metric("Portfolio Return",  f"{_port_final:+.2f}%")
                _km2.metric("S&P 500 Return",    f"{_spy_final:+.2f}%")
                _km3.metric("Alpha vs SPY",
                            f"{_alpha_sign}{_alpha:.2f}%",
                            delta=f"{'Outperforming' if _beating else 'Underperforming'}",
                            delta_color="normal" if _beating else "inverse")

                # ── Chart ────────────────────────────────────────────────────
                _perf_fig = go.Figure()
                _perf_fig.add_trace(go.Scatter(
                    x=list(_common), y=list(_spy_ret),
                    name="S&P 500",
                    line=dict(color="#888888", width=1.5, dash="dot"),
                    hovertemplate="%{x|%b %d}: %{y:+.2f}%<extra>S&P 500</extra>",
                ))
                _perf_fig.add_trace(go.Scatter(
                    x=list(_common), y=list(_port_ret),
                    name="Portfolio",
                    line=dict(color=_port_clr, width=2.5),
                    fill="tonexty", fillcolor=_fill_clr,
                    hovertemplate="%{x|%b %d}: %{y:+.2f}%<extra>Portfolio</extra>",
                ))
                _perf_fig.update_layout(
                    template="plotly_dark",
                    height=380,
                    margin=dict(l=0, r=0, t=12, b=0),
                    legend=dict(
                        orientation="h", yanchor="bottom", y=1.0,
                        xanchor="left", x=0,
                        font=dict(size=12, color="#e8e8e8"),
                        bgcolor="rgba(13,17,23,0.85)",
                        bordercolor="#444", borderwidth=1,
                    ),
                    hovermode="x unified",
                    yaxis=dict(ticksuffix="%", gridcolor="#1f2937",
                               zeroline=True, zerolinecolor="#444"),
                    xaxis=dict(gridcolor="#1f2937"),
                    plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
                )
                st.plotly_chart(_perf_fig, use_container_width=True)
                st.caption(
                    "Uses current portfolio weights applied to historical prices. "
                    "Assumes constant allocation throughout the selected period."
                )
            else:
                st.info("No price history available to build the chart.")
        except Exception as _e:
            st.warning(f"Performance chart unavailable: {_e}")

        # ── Performance Diagnostics ───────────────────────────────────────────
        st.divider()
        st.markdown("### 📊 Performance Diagnostics")
        st.caption(
            "Breaks down portfolio performance into per-position alpha vs SPY and vs the sector ETF benchmark. "
            "Distinguishes genuine stock-picking skill from sector-driven returns — "
            "and identifies exactly which positions are generating vs destroying alpha."
        )

        try:
            # Build sector ETF returns dict from cached data
            _sect_df = _fetch_sector_returns()
            _sect_rets_dict: dict = {}
            if not _sect_df.empty:
                for _, _sr in _sect_df.iterrows():
                    _sect_rets_dict[str(_sr["ETF"])] = {
                        "1W": _sr.get("1W"), "1M": _sr.get("1M"),
                        "3M": _sr.get("3M"), "6M": _sr.get("6M"),
                    }

            _attr_df = compute_attribution(
                port_df, held_data, fetch_spy("6mo"),
                _n_days, _sect_rets_dict, _perf_period,
            )

            if _attr_df.empty:
                st.info("Not enough price history to compute attribution for the selected period.")
            else:
                _perf_recs = build_perf_recommendations(_attr_df, total_val, _perf_period)

                # ── Summary KPIs ──────────────────────────────────────────────
                _net_alpha_dollar = float(_attr_df["Dollar Alpha ($)"].sum())
                _n_generators = int((_attr_df["Category"] == "Alpha Generator").sum())
                _n_riders     = int((_attr_df["Category"] == "Sector Rider").sum())
                _n_destroyers = int((_attr_df["Category"] == "Alpha Destroyer").sum())
                _n_total      = len(_attr_df)
                _skill_pct    = int((_attr_df["Alpha vs SPY (%)"] > 0).sum() / _n_total * 100) if _n_total else 0

                _dp1, _dp2, _dp3, _dp4 = st.columns(4)
                _dp1.metric(
                    f"Net Alpha vs SPY ({_perf_period})",
                    f"${_net_alpha_dollar:+,.0f}",
                    "Outperforming" if _net_alpha_dollar >= 0 else "Underperforming",
                    delta_color="normal" if _net_alpha_dollar >= 0 else "inverse",
                    help=f"Total extra $ earned (or lost) vs holding SPY at identical weights over {_perf_period}",
                )
                _dp2.metric("Alpha Generators",  _n_generators,
                            "beating SPY ≥ 5%", delta_color="off")
                _dp3.metric("Alpha Destroyers",  _n_destroyers,
                            "lagging SPY ≥ 5%", delta_color="off")
                _dp4.metric(
                    "Skill Ratio",
                    f"{_skill_pct}%",
                    f"{int(_skill_pct / 100 * _n_total)}/{_n_total} positions beating SPY",
                    delta_color="off",
                    help="% of positions generating positive alpha vs S&P 500",
                )

                # ── Alpha attribution chart ───────────────────────────────────
                _asc = _attr_df.sort_values("Alpha vs SPY (%)", ascending=True)
                _bar_clrs = [
                    "#00C851" if v >= 5 else "#ff4444" if v <= -5 else "#888888"
                    for v in _asc["Alpha vs SPY (%)"]
                ]
                _hover = [
                    f"<b>{r['Ticker']}</b><br>"
                    f"Return: {r['Holding Ret (%)']:+.1f}%<br>"
                    f"SPY: {r['SPY Ret (%)']:+.1f}%<br>"
                    f"Alpha vs SPY: {r['Alpha vs SPY (%)']:+.1f}%<br>"
                    f"vs {r['ETF']}: "
                    + (f"{r['Alpha vs Sector (%)']:+.1f}%" if r['Alpha vs Sector (%)'] is not None else "—")
                    + f"<br>Category: {r['Category']}<br>$ Alpha: ${r['Dollar Alpha ($)']:+,.0f}"
                    for _, r in _asc.iterrows()
                ]
                _attr_fig = go.Figure(go.Bar(
                    x=list(_asc["Alpha vs SPY (%)"]),
                    y=list(_asc["Ticker"]),
                    orientation="h",
                    marker_color=_bar_clrs,
                    text=[f"{v:+.1f}%" for v in _asc["Alpha vs SPY (%)"]],
                    textposition="outside",
                    customdata=_hover,
                    hovertemplate="%{customdata}<extra></extra>",
                ))
                _attr_fig.add_vline(x=0,   line_color="#555",      line_width=1)
                _attr_fig.add_vline(x=5,   line_dash="dash",
                                    line_color="rgba(0,200,81,0.4)",  line_width=1)
                _attr_fig.add_vline(x=-5,  line_dash="dash",
                                    line_color="rgba(255,68,68,0.4)", line_width=1)
                _attr_fig.update_layout(
                    title=f"Alpha vs S&P 500 by Position — {_perf_period}",
                    template="plotly_dark",
                    height=max(220, len(_asc) * 44 + 60),
                    margin=dict(l=0, r=90, t=40, b=0),
                    xaxis=dict(ticksuffix="%", gridcolor="#1f2937",
                               zeroline=False, title="Alpha vs SPY (%)"),
                    yaxis=dict(gridcolor="#1f2937"),
                    plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
                    showlegend=False,
                )
                st.plotly_chart(_attr_fig, use_container_width=True)
                st.caption(
                    "🟢 Green = outperforming SPY ≥ 5% (alpha generator)  |  "
                    "⬛ Gray = within ±5% of SPY  |  "
                    "🔴 Red = lagging SPY ≥ 5% (alpha destroyer)  |  "
                    "Dashed lines = ±5% thresholds  |  Hover for full breakdown vs sector ETF"
                )

                # ── Position Diagnostics cards ────────────────────────────────
                if _perf_recs:
                    st.divider()
                    st.markdown("#### 🎯 Position Diagnostics & Actions")

                    for _prec in _perf_recs:
                        _pri   = _prec["priority"]
                        _ptype = _prec["type"]
                        _icon  = {"OK": "✅", "MONITOR": "⚠️",
                                  "HIGH": "🔴", "MEDIUM": "🟡"}.get(_pri, "📌")
                        _bclr  = {"HIGH": "#ff4444", "MEDIUM": "#ffbb33",
                                  "MONITOR": "#ffbb33", "OK": "#00C851"}.get(_pri, "#888")
                        _expand = _pri in ("HIGH", "MEDIUM")

                        with st.expander(
                            f"{_icon} **{_pri}** · {_prec['title']}",
                            expanded=_expand,
                        ):
                            # Metrics mini-strip
                            if _prec.get("metrics"):
                                _mc = st.columns(len(_prec["metrics"]))
                                for _mcol, (_mlbl, _mval_s) in zip(_mc, _prec["metrics"].items()):
                                    _mcol.metric(_mlbl, _mval_s)

                            # Problem banner (action cards only)
                            if _prec.get("problem"):
                                st.markdown(
                                    f"<div style='padding:10px 14px;background:#1a1a1a;"
                                    f"border-radius:6px;border-left:4px solid {_bclr};"
                                    f"margin:10px 0'>"
                                    f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                                    f"letter-spacing:0.09em;text-transform:uppercase'>"
                                    f"The Problem</span><br>"
                                    f"<span style='color:#eee'>{_prec['problem']}</span>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )

                            _col_l, _col_r = st.columns([1, 1])

                            with _col_l:
                                if _prec.get("root_cause"):
                                    st.markdown("**Thesis Status**")
                                    st.markdown(
                                        f"<div style='color:#bbb;font-size:0.88em'>"
                                        f"{_prec['root_cause']}</div>",
                                        unsafe_allow_html=True,
                                    )

                            with _col_r:
                                if _prec.get("recommendation"):
                                    st.markdown(
                                        f"<div style='padding:10px 14px;background:#0d2137;"
                                        f"border-radius:6px;border-left:4px solid #4a9eff;"
                                        f"margin-bottom:10px'>"
                                        f"<span style='font-size:0.72em;color:#4a9eff;"
                                        f"font-weight:700;letter-spacing:0.09em;"
                                        f"text-transform:uppercase'>Recommendation</span><br>"
                                        f"<span style='color:#eee;font-size:0.9em'>"
                                        f"{_prec['recommendation']}</span>"
                                        f"</div>",
                                        unsafe_allow_html=True,
                                    )
                                if _prec.get("expected_outcome"):
                                    st.markdown(
                                        f"<div style='padding:10px 14px;background:#0d1a0d;"
                                        f"border-radius:6px;border-left:4px solid #00C851'>"
                                        f"<span style='font-size:0.72em;color:#00C851;"
                                        f"font-weight:700;letter-spacing:0.09em;"
                                        f"text-transform:uppercase'>Expected Outcome</span><br>"
                                        f"<span style='color:#ccc;font-size:0.88em'>"
                                        f"{_prec['expected_outcome']}</span>"
                                        f"</div>",
                                        unsafe_allow_html=True,
                                    )

                            if _prec.get("institutional_lens"):
                                st.markdown("")
                                st.info(f"**Institutional Lens** · {_prec['institutional_lens']}")

        except Exception as _de:
            st.warning(f"Performance Diagnostics unavailable: {_de}")

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 3 — EARNINGS CALENDAR
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_earn:
        st.caption(
            "Upcoming earnings dates for all holdings. "
            "Earnings are high-volatility events — positions within 7 days warrant extra attention. "
            "Dates sourced from Yahoo Finance; confirm with the company's IR page before trading."
        )

        # Build earnings rows from already-loaded held_data
        _today = datetime.now().date()
        _earn_rows = []
        for _, _pr in port_df.iterrows():
            _t   = _pr["Ticker"]
            _d   = held_data.get(_t, {}).get("earnings")
            _inf = held_data.get(_t, {}).get("info", {}) or {}
            _name = _inf.get("shortName") or _inf.get("longName") or _t
            _fwd_eps  = _inf.get("forwardEps")
            _trail_eps = _inf.get("trailingEps")
            _rev_growth = _inf.get("revenueGrowth")
            try:
                _edate = datetime.strptime(_d, "%Y-%m-%d").date() if _d else None
            except Exception:
                _edate = None
            _days = (_edate - _today).days if _edate else None
            _earn_rows.append({
                "Ticker":       _t,
                "Company":      _name,
                "Earnings Date": _edate,
                "Days Until":   _days,
                "Fwd EPS Est":  _fwd_eps,
                "Trail EPS":    _trail_eps,
                "Rev Growth":   _rev_growth,
                "Weight (%)":   _pr["Weight (%)"],
                "P&L (%)":      _pr["P&L (%)"],
                "Signal":       _pr["Signal"],
            })

        _earn_rows.sort(key=lambda x: (
            x["Days Until"] if x["Days Until"] is not None else 9999
        ))

        # KPI strip
        _with_date  = [r for r in _earn_rows if r["Days Until"] is not None]
        _in_7d      = [r for r in _with_date  if 0 <= r["Days Until"] <= 7]
        _in_30d     = [r for r in _with_date  if 0 <= r["Days Until"] <= 30]
        _no_date    = [r for r in _earn_rows  if r["Days Until"] is None]
        _past       = [r for r in _with_date  if r["Days Until"] < 0]

        _ek1, _ek2, _ek3, _ek4 = st.columns(4)
        _ek1.metric("Within 7 days",  len(_in_7d),  help="Earnings in the next week — highest risk")
        _ek2.metric("Within 30 days", len(_in_30d), help="Earnings in the next month")
        _ek3.metric("No date found",  len(_no_date),help="yfinance returned no upcoming date")
        _ek4.metric("Recently passed",len(_past),   help="Earnings date already passed in the data")

        # Timeline chart
        _upcoming = [r for r in _earn_rows if r["Days Until"] is not None and r["Days Until"] >= 0]
        if _upcoming:
            def _earn_color(days):
                if days <= 7:  return "#ff4444"
                if days <= 14: return "#ffbb33"
                return "#00C851"

            _earn_fig = go.Figure()
            # Reference line — today (add_vline annotation_position breaks on string axes)
            _earn_fig.add_shape(
                type="line",
                x0=str(_today), x1=str(_today),
                y0=0, y1=1, yref="paper",
                line=dict(dash="dash", color="#555", width=1.5),
            )
            _earn_fig.add_annotation(
                x=str(_today), y=1.04, yref="paper",
                text="Today", showarrow=False,
                xanchor="left", font=dict(size=11, color="#888"),
            )
            for _r in _upcoming:
                _clr = _earn_color(_r["Days Until"])
                _earn_fig.add_trace(go.Scatter(
                    x=[str(_r["Earnings Date"])],
                    y=[_r["Ticker"]],
                    mode="markers+text",
                    marker=dict(size=18, color=_clr, symbol="diamond",
                                line=dict(color="#fff", width=1.5)),
                    text=[f"  {_r['Days Until']}d"],
                    textposition="middle right",
                    textfont=dict(size=11, color=_clr),
                    name=_r["Ticker"],
                    hovertemplate=(
                        f"<b>{_r['Ticker']}</b><br>"
                        f"{_r['Company']}<br>"
                        f"Date: {_r['Earnings Date']}<br>"
                        f"Days away: {_r['Days Until']}<br>"
                        f"Fwd EPS: {'${:.2f}'.format(_r['Fwd EPS Est']) if _r['Fwd EPS Est'] else 'n/a'}<br>"
                        f"Weight: {_r['Weight (%)']:.1f}%<br>"
                        f"P&L: {_r['P&L (%)']:+.1f}%"
                        "<extra></extra>"
                    ),
                    showlegend=False,
                ))
            _earn_fig.update_layout(
                template="plotly_dark", height=max(220, len(_upcoming) * 44 + 60),
                margin=dict(l=0, r=60, t=30, b=0),
                xaxis=dict(title="", gridcolor="#1f2937", tickformat="%b %d"),
                yaxis=dict(title="", gridcolor="#1f2937", autorange="reversed"),
                plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
                hovermode="closest",
            )
            st.plotly_chart(_earn_fig, use_container_width=True)
        else:
            st.info("No upcoming earnings dates found for current holdings.")

        # Detail table
        st.markdown("#### Earnings Detail")
        if _earn_rows:
            _disp_earn = []
            for _r in _earn_rows:
                _days_str = (
                    f"{_r['Days Until']}d" if _r["Days Until"] is not None and _r["Days Until"] >= 0
                    else ("Passed" if (_r["Days Until"] is not None and _r["Days Until"] < 0) else "—")
                )
                _urgency = (
                    "🔴 Imminent"  if _r["Days Until"] is not None and 0 <= _r["Days Until"] <= 7
                    else "🟡 Soon"  if _r["Days Until"] is not None and 0 <= _r["Days Until"] <= 14
                    else "🟢 Ahead" if _r["Days Until"] is not None and _r["Days Until"] > 14
                    else "⚫ Passed" if (_r["Days Until"] is not None and _r["Days Until"] < 0)
                    else "— Unknown"
                )
                _disp_earn.append({
                    "Ticker":        _r["Ticker"],
                    "Company":       _r["Company"][:28],
                    "Date":          str(_r["Earnings Date"]) if _r["Earnings Date"] else "—",
                    "Days Away":     _days_str,
                    "Urgency":       _urgency,
                    "Fwd EPS":       f"${_r['Fwd EPS Est']:.2f}" if _r["Fwd EPS Est"] else "—",
                    "Trail EPS":     f"${_r['Trail EPS']:.2f}"   if _r["Trail EPS"]   else "—",
                    "Rev Growth":    f"{_r['Rev Growth']*100:.1f}%" if _r["Rev Growth"] else "—",
                    "Weight (%)":    f"{_r['Weight (%)']:.1f}%",
                    "P&L (%)":       f"{_r['P&L (%)']:+.1f}%",
                    "Signal":        _r["Signal"],
                })
            _earn_df = pd.DataFrame(_disp_earn)

            def _earn_row_style(row):
                urgency = row.get("Urgency", "")
                if "Imminent" in urgency:
                    return ["background-color: rgba(255,68,68,0.12)"] * len(row)
                if "Soon"     in urgency:
                    return ["background-color: rgba(255,187,51,0.10)"] * len(row)
                return [""] * len(row)

            st.dataframe(
                _earn_df.style.apply(_earn_row_style, axis=1),
                use_container_width=True, hide_index=True,
            )
            st.caption(
                "🔴 Imminent = ≤7 days · 🟡 Soon = 8–14 days · 🟢 Ahead = >14 days  |  "
                "Fwd EPS = analyst consensus estimate for next quarter · "
                "Earnings dates from Yahoo Finance — verify before acting."
            )

        # ── Pre-Earnings Playbook ─────────────────────────────────────────────
        try:
            _playbook = build_earnings_playbook(port_df, held_data)
        except Exception:
            _playbook = []

        if _playbook:
            st.divider()
            st.markdown("#### 📋 Pre-Earnings Playbook")
            st.caption(
                "Structured action plan for each position with earnings in the next 30 days. "
                "Covers analyst expectations, position risk vs estimated volatility, "
                "a specific pre-earnings action, and what to monitor during the report."
            )

            # KPI summary strip
            _pb_imminent = sum(1 for p in _playbook if p["urgency"] == "IMMINENT")
            _pb_soon     = sum(1 for p in _playbook if p["urgency"] == "SOON")
            _pb_exit     = sum(1 for p in _playbook if p["action"] == "EXIT")
            _pb_reduce   = sum(1 for p in _playbook if p["action"] == "REDUCE")
            _pb_k1, _pb_k2, _pb_k3, _pb_k4 = st.columns(4)
            _pb_k1.metric("Earnings in 30d",  len(_playbook))
            _pb_k2.metric("🔴 Imminent (≤7d)", _pb_imminent)
            _pb_k3.metric("EXIT signals",      _pb_exit,
                          delta="Action required" if _pb_exit else None,
                          delta_color="inverse" if _pb_exit else "off")
            _pb_k4.metric("REDUCE signals",    _pb_reduce,
                          delta="Trim before report" if _pb_reduce else None,
                          delta_color="inverse" if _pb_reduce else "off")

            st.markdown("")

            for _pb in _playbook:
                _action   = _pb["action"]
                _priority = _pb["priority"]
                _urgency  = _pb["urgency"]
                _urg_icon = {"IMMINENT": "🔴", "SOON": "🟡", "AHEAD": "🟢"}.get(_urgency, "📅")
                _act_icon = {
                    "EXIT":       "🚨",
                    "REDUCE":     "✂️",
                    "MONITOR":    "👁️",
                    "HOLD_OR_ADD": "💪",
                    "HOLD":       "✅",
                }.get(_action, "📌")
                _bclr = {
                    "HIGH":   "#ff4444",
                    "MEDIUM": "#ffbb33",
                    "OK":     "#00C851",
                }.get(_priority, "#888")
                _expand = _priority in ("HIGH", "MEDIUM") or _urgency == "IMMINENT"

                _earn_dt_str = _pb["earnings_date"].strftime("%b %d") if _pb["earnings_date"] else "—"

                with st.expander(
                    f"{_act_icon} **{_action}** · {_pb['ticker']} — {_pb['company']}  "
                    f"| {_urg_icon} {_earn_dt_str} ({_pb['days_until']}d)  "
                    f"| Est. move ±{_pb['est_move']:.0f}%",
                    expanded=_expand,
                ):
                    # Metrics strip
                    _pb_mc = st.columns(5)
                    _pb_mc[0].metric("Weight",       f"{_pb['weight']:.1f}%")
                    _pb_mc[1].metric("Market Value",  f"${_pb['market_value']:,.0f}")
                    _pb_mc[2].metric("P&L",           f"{_pb['pnl_pct']:+.1f}%")
                    _pb_mc[3].metric("Est. Move",     f"±{_pb['est_move']:.0f}%")
                    _pb_mc[4].metric("Earnings Risk",
                        f"±${_pb['earn_risk']:,.0f}",
                        delta="Stop at risk" if _pb["stop_at_risk"] else None,
                        delta_color="inverse" if _pb["stop_at_risk"] else "off",
                    )

                    # Analyst expectations
                    st.markdown("")
                    _pb_al, _pb_ar = st.columns([1, 1])
                    with _pb_al:
                        st.markdown("**Analyst Expectations**")
                        _ae_lines = []
                        if _pb["fwd_eps"] is not None:
                            _ae_lines.append(f"- **Fwd EPS:** ${_pb['fwd_eps']:.2f}")
                        if _pb["trail_eps"] is not None:
                            _ae_lines.append(f"- **Trail EPS:** ${_pb['trail_eps']:.2f}")
                        if _pb["fwd_pe"] is not None:
                            _ae_lines.append(f"- **Fwd P/E:** {_pb['fwd_pe']:.1f}×")
                        if _pb["rev_growth"] is not None:
                            _ae_lines.append(f"- **Rev Growth:** {_pb['rev_growth']*100:.1f}%")
                        if _pb["earn_growth"] is not None:
                            _ae_lines.append(f"- **Earn Growth:** {_pb['earn_growth']*100:.1f}%")
                        if _ae_lines:
                            st.markdown("\n".join(_ae_lines))
                        else:
                            st.caption("No analyst estimate data available.")

                        # Analyst revisions
                        st.markdown("")
                        _rev_color = "#00C851" if _pb["net_rev"] > 0 else ("#ff4444" if _pb["net_rev"] < 0 else "#888")
                        st.markdown(
                            f"<div style='font-size:0.88em;color:#bbb'>"
                            f"Analyst revisions (90d): "
                            f"<span style='color:{_rev_color};font-weight:700'>{_pb['net_rev']:+d} net</span>"
                            f"  ({_pb['ups_90']} ↑ / {_pb['dns_90']} ↓)"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        if _pb["latest_rev"]:
                            for _rv in _pb["latest_rev"]:
                                _rv_dir = str(_rv.get("direction", ""))
                                _rv_firm = str(_rv.get("firm", ""))
                                _rv_icon = "⬆️" if _rv_dir == "up" else ("⬇️" if _rv_dir == "down" else "➡️")
                                st.markdown(
                                    f"<div style='font-size:0.8em;color:#999;margin-left:8px'>"
                                    f"{_rv_icon} {_rv_firm}</div>",
                                    unsafe_allow_html=True,
                                )

                    with _pb_ar:
                        # Stop vs estimated move
                        _stop_label = f"{_pb['stop_type']}: ${_pb['stop_price']:.2f}" if _pb["stop_price"] else "No stop set"
                        _gap_color  = "#ff4444" if _pb["stop_at_risk"] else "#00C851"
                        st.markdown(
                            f"<div style='padding:10px 14px;background:#1a1a1a;"
                            f"border-radius:6px;border-left:4px solid {_gap_color};margin-bottom:10px'>"
                            f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                            f"letter-spacing:0.09em;text-transform:uppercase'>Stop vs Earnings Vol</span><br>"
                            f"<span style='color:#eee;font-size:0.88em'>"
                            f"{_stop_label} · Gap: <b style='color:{_gap_color}'>"
                            + (f"{_pb['gap_to_stop']:.1f}%" if _pb.get('gap_to_stop') is not None else "—")
                            + f"</b> vs Est. move <b>±{_pb['est_move']:.0f}%</b>"
                            f"{'  ⚠️ Stop may not protect against overnight gap' if _pb['stop_at_risk'] else ''}"
                            f"</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        st.markdown(f"**Composite Score:** {_pb['score']:.0f}/100 · **Signal:** {_pb['signal']}")

                    # Action recommendation
                    st.markdown(
                        f"<div style='padding:12px 16px;background:#0d1117;"
                        f"border-radius:6px;border-left:4px solid {_bclr};margin:10px 0'>"
                        f"<span style='font-size:0.72em;color:{_bclr};font-weight:700;"
                        f"letter-spacing:0.09em;text-transform:uppercase'>"
                        f"{_act_icon} Pre-Earnings Action: {_action}</span><br>"
                        f"<span style='color:#eee;font-size:0.9em'>{_pb['detail']}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                    # What to watch during the report
                    if _pb.get("watch_for"):
                        st.markdown("**What to Watch During the Report**")
                        _wf_cols = st.columns(2)
                        for _wi, _witem in enumerate(_pb["watch_for"]):
                            _wf_cols[_wi % 2].markdown(f"- {_witem}")

                    # Institutional Lens
                    if _pb.get("institutional_lens"):
                        st.markdown("")
                        st.info(f"**Institutional Lens** · {_pb['institutional_lens']}")

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 4 — P&L ATTRIBUTION WATERFALL
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_pnl:
        st.caption(
            "Shows each position's dollar contribution to total portfolio P&L — "
            "largest winners on the left, largest losers on the right. "
            "Identifies which holdings are driving performance and which are dragging it."
        )

        # Sort by P&L $ descending (winners left, losers right)
        _pnl_df = port_df[["Ticker", "Sector", "P&L ($)", "P&L (%)", "Weight (%)", "Market Value", "Signal"]].copy()
        _pnl_df = _pnl_df.sort_values("P&L ($)", ascending=False).reset_index(drop=True)

        _total_pnl   = float(_pnl_df["P&L ($)"].sum())
        _winners     = _pnl_df[_pnl_df["P&L ($)"] > 0]
        _losers      = _pnl_df[_pnl_df["P&L ($)"] < 0]
        _win_total   = float(_winners["P&L ($)"].sum())
        _loss_total  = float(_losers["P&L ($)"].sum())
        _best        = _pnl_df.iloc[0]
        _worst       = _pnl_df.iloc[-1]

        # KPI strip
        _pk1, _pk2, _pk3, _pk4, _pk5 = st.columns(5)
        _pk1.metric("Total P&L",      _m(f"${_total_pnl:+,.0f}"),
                    delta_color="normal" if _total_pnl >= 0 else "inverse")
        _pk2.metric("Gross Gains",    _m(f"${_win_total:,.0f}"),
                    f"{len(_winners)} positions", delta_color="off")
        _pk3.metric("Gross Losses",   _m(f"${_loss_total:,.0f}"),
                    f"{len(_losers)} positions",  delta_color="off")
        _pk4.metric(f"Top: {_best['Ticker']}",
                    _m(f"${_best['P&L ($)']:+,.0f}"), f"{_best['P&L (%)']:+.1f}%",
                    delta_color="normal")
        _pk5.metric(f"Drag: {_worst['Ticker']}",
                    _m(f"${_worst['P&L ($)']:+,.0f}"), f"{_worst['P&L (%)']:+.1f}%",
                    delta_color="inverse")

        # ── Waterfall chart ───────────────────────────────────────────────────
        _tickers  = list(_pnl_df["Ticker"]) + ["Total"]
        _measures = ["relative"] * len(_pnl_df) + ["total"]
        _values   = list(_pnl_df["P&L ($)"]) + [_total_pnl]
        _bar_clrs = [
            "#00C851" if v >= 0 else "#ff4444"
            for v in list(_pnl_df["P&L ($)"]) + [_total_pnl]
        ]
        _priv = st.session_state.get("_privacy")
        _hover = [
            f"{row['Ticker']}<br>"
            + (f"P&L: •••• ({row['P&L (%)']:+.1f}%)<br>" if _priv else f"P&L: ${row['P&L ($)']:+,.0f} ({row['P&L (%)']:+.1f}%)<br>")
            + f"Weight: {row['Weight (%)']:.1f}%<br>"
            f"Sector: {row['Sector']}<br>"
            f"Signal: {row['Signal']}"
            for _, row in _pnl_df.iterrows()
        ] + [("Total P&L: ••••" if _priv else f"Total P&L: ${_total_pnl:+,.0f}")]

        _wf_fig = go.Figure(go.Waterfall(
            orientation="v",
            measure=_measures,
            x=_tickers,
            y=_values,
            text=["••••" if _priv else f"${v:+,.0f}" for v in _values],
            textposition="outside",
            textfont=dict(size=11),
            customdata=_hover,
            hovertemplate="%{customdata}<extra></extra>",
            connector=dict(line=dict(color="#333", width=1, dash="dot")),
            increasing=dict(marker=dict(color="#00C851")),
            decreasing=dict(marker=dict(color="#ff4444")),
            totals=dict(marker=dict(
                color="#00C851" if _total_pnl >= 0 else "#ff4444",
                line=dict(color="#fff", width=1.5),
            )),
        ))
        _wf_fig.update_layout(
            template="plotly_dark", height=420,
            margin=dict(l=0, r=0, t=20, b=0),
            xaxis=dict(gridcolor="#1f2937"),
            yaxis=dict(
                tickprefix="$", tickformat=",.0f",
                gridcolor="#1f2937", zeroline=True, zerolinecolor="#555",
            ),
            plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
            showlegend=False,
        )
        st.plotly_chart(_wf_fig, use_container_width=True)

        # ── Sector attribution breakdown ──────────────────────────────────────
        st.markdown("#### Sector Attribution")
        _sec_pnl = (
            _pnl_df.groupby("Sector")["P&L ($)"]
            .sum().sort_values(ascending=False).reset_index()
        )
        _sec_pnl["Share of P&L (%)"] = (
            _sec_pnl["P&L ($)"] / abs(_total_pnl) * 100
            if _total_pnl != 0 else 0.0
        )
        _sec_fig = go.Figure(go.Bar(
            x=_sec_pnl["Sector"],
            y=_sec_pnl["P&L ($)"],
            marker_color=[
                "#00C851" if v >= 0 else "#ff4444"
                for v in _sec_pnl["P&L ($)"]
            ],
            text=[("••••" if st.session_state.get("_privacy") else f"${v:+,.0f}") for v in _sec_pnl["P&L ($)"]],
            textposition="outside",
            customdata=list(zip(_sec_pnl["Sector"], _sec_pnl["Share of P&L (%)"])),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                + ("P&L: ••••<br>" if st.session_state.get("_privacy") else "P&L: $%{y:+,.0f}<br>")
                + "Share: %{customdata[1]:.1f}% of total<extra></extra>"
            ),
        ))
        _sec_fig.update_layout(
            template="plotly_dark", height=280,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis=dict(gridcolor="#1f2937"),
            yaxis=dict(
                tickprefix="$", tickformat=",.0f",
                gridcolor="#1f2937", zeroline=True, zerolinecolor="#555",
            ),
            plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
            showlegend=False,
        )
        st.plotly_chart(_sec_fig, use_container_width=True)
        st.caption(
            "Waterfall: each bar is one position's dollar P&L contribution; "
            "the final 'Total' bar is the portfolio sum.  "
            "Sector chart groups by GICS sector."
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 5 — ALERTS & ACTIONS
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_act:
        # ── Active Alerts — grouped by category ──────────────────────────────
        _danger_alerts  = [a for a in alert_list if a["level"] == "danger"]
        _warning_alerts = [a for a in alert_list if a["level"] == "warning"]
        _info_alerts    = [a for a in alert_list if a["level"] == "info"]

        if not alert_list:
            st.success("✅ No active alerts — portfolio is within normal parameters.")
        else:
            # Category labels for grouping
            _CAT_LABELS = {
                "stop":         "🛑 Stop Loss",
                "signal":       "📊 Signal",
                "concentration":"🏭 Concentration",
                "earnings":     "📅 Earnings",
                "revisions":    "📉 Analyst Revisions",
                "signal_change":"🔄 Signal Changes",
            }

            _al1, _al2, _al3 = st.columns(3)
            _al1.metric("🔴 Danger",  len(_danger_alerts),  help="Require immediate attention")
            _al2.metric("🟡 Warning", len(_warning_alerts), help="Monitor closely")
            _al3.metric("ℹ️ Info",    len(_info_alerts),    help="Noteworthy changes")
            st.markdown("")

            # Group and render by category priority
            _cat_order = ["stop", "earnings", "signal", "revisions", "concentration", "signal_change"]
            _rendered  = set()
            for _cat in _cat_order:
                _cat_items = [a for a in alert_list if a.get("category") == _cat]
                if not _cat_items:
                    continue
                st.markdown(f"**{_CAT_LABELS.get(_cat, _cat)}**")
                for a in _cat_items:
                    _rendered.add(id(a))
                    if a["level"] == "danger":
                        st.error(a["msg"])
                    elif a["level"] == "warning":
                        st.warning(a["msg"])
                    else:
                        st.info(a["msg"])
            # Fallback: any alerts without a recognised category
            for a in alert_list:
                if id(a) not in _rendered:
                    if a["level"] == "danger":   st.error(a["msg"])
                    elif a["level"] == "warning": st.warning(a["msg"])
                    else:                         st.info(a["msg"])

        # ── Custom Price Alerts ───────────────────────────────────────────────
        st.divider()
        st.subheader("🎯 Custom Price Alerts")
        st.caption(
            "Set a **take-profit target** (above current price) and/or a **floor alert** "
            "(below current price) for each holding. Alerts fire the next time you load the page."
        )

        # Initialise alerts store
        _pa_store = st.session_state.setdefault("_price_alerts", {})
        for _t in port_df["Ticker"]:
            _pa_store.setdefault(_t, {"target": 0.0, "floor": 0.0})

        # Seed number-input session state from store on first load only
        for _t in port_df["Ticker"]:
            if f"_pa_tgt_{_t}" not in st.session_state:
                st.session_state[f"_pa_tgt_{_t}"] = float(_pa_store[_t].get("target") or 0.0)
            if f"_pa_flr_{_t}" not in st.session_state:
                st.session_state[f"_pa_flr_{_t}"] = float(_pa_store[_t].get("floor") or 0.0)

        # Header row
        _hc = st.columns([2, 2, 2, 2])
        _hc[0].markdown("**Ticker**")
        _hc[1].markdown("**Current price**")
        _hc[2].markdown("**Take-Profit ($)** — alert when price ≥ this")
        _hc[3].markdown("**Floor Alert ($)** — alert when price ≤ this")

        for _, _pr in port_df.iterrows():
            _t = _pr["Ticker"]
            _c1, _c2, _c3, _c4 = st.columns([2, 2, 2, 2])
            _c1.markdown(f"**{_t}**")
            _c2.markdown(f"${_pr['Price']:.2f}")
            _c3.number_input(
                "take-profit", key=f"_pa_tgt_{_t}",
                min_value=0.0, step=1.0, format="%.2f",
                label_visibility="collapsed",
            )
            _c4.number_input(
                "floor alert", key=f"_pa_flr_{_t}",
                min_value=0.0, step=1.0, format="%.2f",
                label_visibility="collapsed",
            )

        if st.button("💾 Save price alerts", key="_pa_save"):
            for _t in port_df["Ticker"]:
                _pa_store[_t] = {
                    "target": float(st.session_state.get(f"_pa_tgt_{_t}") or 0.0),
                    "floor":  float(st.session_state.get(f"_pa_flr_{_t}") or 0.0),
                }
            st.session_state["_pa_saved_ok"] = True
            st.rerun()

        if st.session_state.pop("_pa_saved_ok", False):
            st.success("✅ Price alerts saved.")

        # Check triggers — full detail shown here, badge summary shown in Command Center above
        _pa_fired = []
        for _, _pr in port_df.iterrows():
            _t    = _pr["Ticker"]
            _px   = float(_pr.get("Price") or 0)
            _pa   = _pa_store.get(_t, {})
            _tgt  = _pa.get("target") or 0.0
            _flr  = _pa.get("floor")  or 0.0
            if _tgt > 0 and _px >= _tgt:
                _pa_fired.append(("warning", f"🎯 **{_t}** hit take-profit target **${_tgt:.2f}** (current ${_px:.2f}) — consider locking in gains"))
            if _flr > 0 and _px <= _flr:
                _pa_fired.append(("danger",  f"🚨 **{_t}** breached floor alert **${_flr:.2f}** (current ${_px:.2f}) — review position now"))
        if _pa_fired:
            st.markdown("#### 🔔 Active Price Alerts")
            for _lvl, _msg in _pa_fired:
                if _lvl == "danger":   st.error(_msg)
                else:                  st.warning(_msg)

        st.divider()

        # Rebalancing advisor cards — flat (no extra expander)
        if actions:
            st.subheader("💡 Rebalancing Recommendations")
            st.caption(
                "Each recommendation shows exactly what triggered it, the score breakdown, "
                "and a pre-evaluated decision checklist — so you decide, not an algorithm."
            )
            for act in actions:
                ticker  = act["ticker"]
                urgency = act["urgency"]
                r_data  = held_data.get(ticker, {})
                fin     = r_data.get("financials", {})
                rev     = r_data.get("revisions", {})
                earn    = r_data.get("earnings")
                t_score = r_data.get("t_score")
                f_score = r_data.get("f_score")
                s_score = r_data.get("s_score")
                t_sigs  = r_data.get("t_signals", {})

                urgency_badge = {"high": "🔴 HIGH", "medium": "🟡 MEDIUM", "low": "🟢 LOW"}.get(urgency, "")
                icon = {"review": "📉", "trim": "✂️", "add": "➕"}.get(act["type"], "💡")

                with st.expander(
                    f"{icon} **{ticker}** — {act['title']}  ·  {urgency_badge}",
                    expanded=(urgency == "high"),
                ):
                    # ── Trigger ──────────────────────────────────────────────
                    st.markdown(
                        f"<div style='padding:8px 12px;background:#1a1a1a;border-radius:6px;"
                        f"border-left:4px solid #ffbb33;margin-bottom:10px'>"
                        f"<span style='font-size:0.78em;color:#888'>WHAT TRIGGERED THIS</span><br>"
                        f"<span style='color:#eee'>{act['trigger']}</span></div>",
                        unsafe_allow_html=True,
                    )

                    ev_col, check_col = st.columns([1, 1])

                    # ── Evidence panel ────────────────────────────────────────
                    with ev_col:
                        st.markdown("**Score Breakdown — What's Driving It**")
                        if t_score is not None:
                            for dim, sc, weight, tip_key in [
                                ("Technical",    t_score, "45%", "RSI"),
                                ("Fundamental",  f_score, "40%", "FCF Yield"),
                                ("Sentiment",    s_score, "15%", ""),
                            ]:
                                clr  = "#00C851" if sc >= 60 else ("#ffbb33" if sc >= 44 else "#ff4444")
                                icon_s = "✅" if sc >= 60 else ("⚠️" if sc >= 44 else "❌")
                                bar_w = int(sc)
                                st.markdown(
                                    f"<div style='margin-bottom:5px'>"
                                    f"<span style='font-size:0.8em;color:#aaa'>{icon_s} {dim} ({weight})</span>"
                                    f"<span style='float:right;font-size:0.8em;font-weight:bold;color:{clr}'>{sc:.0f}/100</span>"
                                    f"<div style='height:5px;background:#222;border-radius:3px;margin-top:2px'>"
                                    f"<div style='width:{bar_w}%;height:5px;background:{clr};border-radius:3px'></div>"
                                    f"</div></div>",
                                    unsafe_allow_html=True,
                                )

                            # Primary driver diagnosis
                            if t_score is not None and f_score is not None:
                                gap_tf = t_score - f_score
                                if gap_tf < -15:
                                    driver = "🔴 **Fundamental deterioration** is the primary driver — this is a thesis-change signal, act with more urgency."
                                elif gap_tf > 15:
                                    driver = "🟡 **Technical weakness only** — fundamentals remain solid. Likely a timing/momentum signal; the ratchet stop may handle it."
                                else:
                                    driver = "⚠️ **Both Technical and Fundamental signals are weak** — broader caution warranted."
                                st.info(driver)

                        # Specific bearish signals from t_signals
                        bearish_sigs = {k: v for k, v in t_sigs.items() if "bearish" in v.lower()}
                        if bearish_sigs:
                            st.markdown("**Specific Bearish Technical Signals:**")
                            for k, v in bearish_sigs.items():
                                st.markdown(f"<small style='color:#ff8800'>▼ **{k}**: {v}</small>", unsafe_allow_html=True)

                    # ── Decision checklist ────────────────────────────────────
                    with check_col:
                        st.markdown("**Decision Checklist**")

                        def _check(emoji, text, color="#ccc"):
                            st.markdown(
                                f"<div style='margin-bottom:5px;font-size:0.85em'>"
                                f"{emoji} <span style='color:{color}'>{text}</span></div>",
                                unsafe_allow_html=True,
                            )

                        # Stop status
                        gap = act["gap"]
                        stop = act["stop"]
                        stop_type = act["stop_type"]
                        if gap < 4:
                            _check("🔴", f"Stop at ${stop:.2f} — only {gap:.1f}% away. Market may decide for you.", "#ff4444")
                        elif gap < 8:
                            _check("🟡", f"Stop at ${stop:.2f} ({stop_type}) — {gap:.1f}% buffer. Monitor closely.", "#ffbb33")
                        else:
                            _check("✅", f"Stop at ${stop:.2f} ({stop_type}) — {gap:.1f}% buffer. Not in immediate danger.", "#00C851")

                        # Earnings proximity
                        earn_flag = False
                        if earn:
                            try:
                                days_to_earn = (datetime.strptime(earn, "%Y-%m-%d").date() - date.today()).days
                                if 0 <= days_to_earn <= 14:
                                    _check("🔴", f"Earnings in {days_to_earn}d ({earn}) — bearish signal + near earnings = reduce now.", "#ff4444")
                                    earn_flag = True
                                elif days_to_earn <= 30:
                                    _check("🟡", f"Earnings in {days_to_earn}d ({earn}) — consider reducing before report.", "#ffbb33")
                                    earn_flag = True
                                else:
                                    _check("✅", f"Next earnings: {days_to_earn}d away — no immediate catalyst pressure.")
                            except Exception:
                                pass

                        # Analyst revisions
                        if rev:
                            net_rev = rev.get("net", 0)
                            ups = rev.get("upgrades_90d", 0)
                            dns = rev.get("downgrades_90d", 0)
                            if net_rev > 0:
                                _check("✅", f"Analysts: ↑{ups} upgrades vs ↓{dns} downgrades (90d) — institutional conviction intact.", "#00C851")
                            elif net_rev < 0:
                                _check("🔴", f"Analysts: ↑{ups} upgrades vs ↓{dns} downgrades (90d) — estimates being cut. Higher urgency.", "#ff4444")
                            else:
                                _check("🟡", f"Analyst revisions: neutral (90d).")

                        # Short interest
                        short_pct = fin.get("short_pct_float")
                        if short_pct is not None:
                            if short_pct > 15:
                                _check("⚠️", f"Short interest {short_pct:.1f}% — elevated bearish positioning. Confirms the sell signal.")
                            else:
                                _check("✅", f"Short interest {short_pct:.1f}% — not heavily shorted. Bears haven't piled in.")

                        # Institutional ownership
                        inst = fin.get("held_pct_institutions")
                        if inst is not None:
                            if inst > 60:
                                _check("✅", f"Institutional ownership {inst:.0f}% — smart money still holding.")
                            elif inst < 30:
                                _check("⚠️", f"Low institutional ownership {inst:.0f}% — limited institutional support.")

                        # FCF / fundamentals quality
                        fcf_y = fin.get("fcf_yield")
                        if fcf_y is not None:
                            if fcf_y >= 3:
                                _check("✅", f"FCF Yield {fcf_y:.1f}% — business generating real cash. Fundamentals back the hold.")
                            elif fcf_y < 0:
                                _check("🔴", f"FCF Yield {fcf_y:.1f}% — company burning cash. Adds urgency to the sell signal.", "#ff4444")

                        # Position sizing
                        if act["weight"] > 15:
                            _check("⚠️", f"Position is {act['weight']:.0f}% of portfolio — above 15% threshold. Size alone justifies trimming.")

                    # ── Evidence & Sources — verify every data point ─────────
                    st.markdown("---")
                    st.markdown(
                        "**Evidence & Sources — Double-Check Before Acting**  \n"
                        "<span style='font-size:0.8em;color:#666'>"
                        "AI can make mistakes. Every data point below links to its original source "
                        "so you can verify independently before making any decision.</span>",
                        unsafe_allow_html=True,
                    )

                    src_col1, src_col2 = st.columns(2)

                    with src_col1:
                        # Negative/neutral news headlines that drove sentiment score
                        all_headlines = r_data.get("headlines", [])
                        neg_heads = [h for h in all_headlines if h["label"] in ("Negative", "Neutral")]
                        pos_heads = [h for h in all_headlines if h["label"] == "Positive"]
                        if all_headlines:
                            st.markdown(
                                f"📰 **News Sentiment · {s_score:.0f}/100** "
                                f"<span style='font-size:0.75em;color:#666'>"
                                f"({len(neg_heads)} negative · {len(pos_heads)} positive of {len(all_headlines)} headlines)</span>",
                                unsafe_allow_html=True,
                            )
                            for h in all_headlines[:6]:
                                clr  = "#ff4444" if h["label"] == "Negative" else (
                                       "#00C851" if h["label"] == "Positive" else "#888")
                                arrow = "▼" if h["label"] == "Negative" else (
                                        "▲" if h["label"] == "Positive" else "–")
                                headline_short = h["headline"][:78] + ("…" if len(h["headline"]) > 78 else "")
                                url = h.get("url", "")
                                link_part = (
                                    f"<a href='{url}' target='_blank' "
                                    f"style='color:#ccc;text-decoration:none'>{headline_short}</a>"
                                    if url else f"<span style='color:#ccc'>{headline_short}</span>"
                                )
                                source_tag = (
                                    f" <a href='{url}' target='_blank' "
                                    f"style='color:#4a9eff;font-size:0.7em'>[source ↗]</a>"
                                    if url else
                                    " <span style='color:#555;font-size:0.7em'>[no link — verify on Yahoo Finance]</span>"
                                )
                                st.markdown(
                                    f"<div style='margin-bottom:4px;font-size:0.8em'>"
                                    f"<span style='color:{clr}'>{arrow} {h['score']:+.2f}</span> "
                                    f"{link_part}{source_tag}</div>",
                                    unsafe_allow_html=True,
                                )
                            st.markdown(
                                f"[All {ticker} news on Yahoo Finance ↗](https://finance.yahoo.com/quote/{ticker}/news/)",
                            )
                        else:
                            st.caption("No news headlines available for this ticker.")

                        # Bearish fundamental signals with values and source
                        f_sigs_all = r_data.get("f_signals", {})
                        bearish_f = {
                            k: v for k, v in f_sigs_all.items()
                            if any(w in v.lower() for w in
                                   ["declin", "expensive", "loss", "burn", "high lev", "contract", "modest", "thin", "slow"])
                        }
                        if f_sigs_all:
                            st.markdown(f"📊 **Fundamentals · {f_score:.0f}/100** — raw values from Yahoo Finance")
                            for k, v in f_sigs_all.items():
                                is_bad = any(w in v.lower() for w in
                                            ["declin", "expensive", "loss", "burn", "high lev", "contract", "modest", "thin", "slow"])
                                clr = "#ff4444" if is_bad else "#00C851"
                                icon_f = "❌" if is_bad else "✅"
                                st.markdown(
                                    f"<div style='font-size:0.8em;margin-bottom:3px'>"
                                    f"{icon_f} <span style='color:{clr}'><b>{k}</b>: {v}</span></div>",
                                    unsafe_allow_html=True,
                                )
                            st.markdown(
                                f"Verify: "
                                f"[Yahoo Financials ↗](https://finance.yahoo.com/quote/{ticker}/financials/) · "
                                f"[Yahoo Statistics ↗](https://finance.yahoo.com/quote/{ticker}/key-statistics/) · "
                                f"[SEC Filings ↗](https://www.sec.gov/cgi-bin/browse-edgar?"
                                f"action=getcompany&company={ticker}&type=10-K)"
                            )

                    with src_col2:
                        # Bearish technical signals with chart links
                        if t_sigs:
                            st.markdown(f"📈 **Technical Signals · {t_score:.0f}/100**")
                            for k, v in t_sigs.items():
                                is_bear = "bearish" in v.lower()
                                is_bull = "bullish" in v.lower()
                                clr = "#ff4444" if is_bear else ("#00C851" if is_bull else "#888")
                                icon_t = "❌" if is_bear else ("✅" if is_bull else "–")
                                st.markdown(
                                    f"<div style='font-size:0.8em;margin-bottom:3px'>"
                                    f"{icon_t} <span style='color:{clr}'><b>{k}</b>: {v}</span></div>",
                                    unsafe_allow_html=True,
                                )
                            st.markdown(
                                f"Verify chart: "
                                f"[TradingView ↗](https://www.tradingview.com/chart/?symbol={ticker}) · "
                                f"[Finviz Chart ↗](https://finviz.com/quote.ashx?t={ticker}) · "
                                f"[Yahoo Chart ↗](https://finance.yahoo.com/chart/{ticker})"
                            )

                        # Analyst actions — firm names, grade changes, with source link
                        st.markdown(f"👔 **Analyst Actions (last 90 days)**")
                        if rev and rev.get("latest"):
                            net_r = rev.get("net", 0)
                            st.markdown(
                                f"<span style='font-size:0.8em;color:#888'>"
                                f"↑ {rev.get('upgrades_90d',0)} upgrades · "
                                f"↓ {rev.get('downgrades_90d',0)} downgrades · "
                                f"→ {rev.get('maintained_90d',0)} maintained</span>",
                                unsafe_allow_html=True,
                            )
                            for analyst_act in rev["latest"]:
                                atype = analyst_act.get("action", "").lower()
                                aclr  = "#ff4444" if atype == "down" else (
                                        "#00C851" if atype in ["up", "init"] else "#888")
                                arr   = "↓" if atype == "down" else ("↑" if atype in ["up", "init"] else "→")
                                from_g = analyst_act.get("from_grade", "")
                                to_g   = analyst_act.get("to_grade", "")
                                grade_str = (
                                    f"{from_g} → {to_g}" if from_g and to_g else
                                    f"→ {to_g}" if to_g else
                                    f"({atype})"
                                )
                                firm = analyst_act.get("firm", "Unknown")
                                st.markdown(
                                    f"<div style='font-size:0.82em;margin-bottom:3px'>"
                                    f"<span style='color:{aclr};font-weight:bold'>{arr} {firm}</span>"
                                    f"<span style='color:#aaa'> · {grade_str}</span></div>",
                                    unsafe_allow_html=True,
                                )
                            st.markdown(
                                f"Verify: "
                                f"[Yahoo Analyst Ratings ↗](https://finance.yahoo.com/quote/{ticker}/analysis/) · "
                                f"[MarketBeat ↗](https://www.marketbeat.com/stocks/NASDAQ/{ticker}/analyst-ratings/)"
                            )
                        else:
                            st.caption(
                                "Analyst action history not available via Yahoo Finance for this ticker.  \n"
                                f"[Check manually on Yahoo Finance ↗](https://finance.yahoo.com/quote/{ticker}/analysis/)"
                            )

                        # Verification footer — all primary sources in one row
                        st.markdown(
                            f"<div style='margin-top:10px;padding:8px 10px;background:#111;"
                            f"border-radius:6px;font-size:0.76em;color:#555'>"
                            f"📌 <b style='color:#777'>All sources for {ticker}:</b><br>"
                            f"<a href='https://finance.yahoo.com/quote/{ticker}' target='_blank' style='color:#4a9eff'>Yahoo Finance</a> · "
                            f"<a href='https://finviz.com/quote.ashx?t={ticker}' target='_blank' style='color:#4a9eff'>Finviz</a> · "
                            f"<a href='https://www.tradingview.com/chart/?symbol={ticker}' target='_blank' style='color:#4a9eff'>TradingView</a> · "
                            f"<a href='https://finance.yahoo.com/quote/{ticker}/financials/' target='_blank' style='color:#4a9eff'>Financials</a> · "
                            f"<a href='https://finance.yahoo.com/quote/{ticker}/analysis/' target='_blank' style='color:#4a9eff'>Analyst Ratings</a> · "
                            f"<a href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=10-K' target='_blank' style='color:#4a9eff'>SEC 10-K</a>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    # ── Suggested action ──────────────────────────────────────
                    st.markdown("---")
                    st.markdown("**Suggested Action**")
                    if act["type"] == "review":
                        half = act["half_shares"]
                        half_val = half * act["price"]
                        full_val = act["shares"] * act["price"]
                        if f_score is not None and t_score is not None:
                            if f_score < 44:
                                action_text = (
                                    f"**Fundamental-driven weakness — act with urgency.**  \n"
                                    f"Sell **{half} shares** (~${half_val:,.0f}) at market now to bank the "
                                    f"{act['pnl']:.0f}% gain on half the position.  \n"
                                    f"Hold remaining {act['shares'] - half} shares with stop at "
                                    f"**${act['stop']:.2f}** ({act['stop_type']}).  \n"
                                    f"Revisit full exit if stop is breached or next earnings disappoint."
                                )
                            else:
                                action_text = (
                                    f"**Technical-only weakness — fundamentals are intact.**  \n"
                                    f"Option A (conservative): Let the ratchet stop at **${act['stop']:.2f}** "
                                    f"do the work — it already locks in a portion of your gain.  \n"
                                    f"Option B (active): Sell **{half} shares** (~${half_val:,.0f}) to reduce "
                                    f"exposure, trail remainder with the existing stop.  \n"
                                    f"Do NOT sell all {act['shares']} shares on a technical signal alone "
                                    f"when fundamentals are solid."
                                )
                                if earn_flag:
                                    action_text += f"  \n⚠️ Earnings proximity tips toward Option B — reduce before the report."
                        else:
                            action_text = (
                                f"Sell **{half} shares** (~${half_val:,.0f}) to bank gain on half the position.  \n"
                                f"Hold remainder with stop at **${act['stop']:.2f}**."
                            )
                        st.markdown(action_text)

                    elif act["type"] == "trim":
                        ts = act["trim_shares"]
                        tv = act["trim_val"]
                        action_text = (
                            f"Sell **{ts} shares** (~${tv:,.0f}) to reduce from "
                            f"{act['weight']:.0f}% → ~15% portfolio weight.  \n"
                            f"This locks in a portion of the {act['pnl']:.0f}% gain while keeping "
                            f"the core position. Reinvest the proceeds in underweighted high-conviction names."
                        )
                        st.markdown(action_text)

                    elif act["type"] == "add":
                        action_text = (
                            f"Score is **{act['score']:.0f}/100** ({act['signal']}) but weight is only "
                            f"{act['weight']:.1f}%.  \n"
                            f"Consider building toward **8–10% weight** — buy in 2–3 tranches to average in "
                            f"rather than deploying all at once.  \n"
                            f"First tranche: target 4–5% weight. Only add second tranche if price holds above "
                            f"stop at **${act['stop']:.2f}**."
                        )
                        st.markdown(action_text)

                    # ── Quick log button ──────────────────────────────────────
                    st.markdown("---")
                    log_col, note_col = st.columns([1, 2])
                    with log_col:
                        _default_shares = (
                            act.get("half_shares", act.get("trim_shares", act.get("shares", 1)))
                        )
                        _default_action = "SELL" if act["type"] in ("review", "trim") else "BUY"
                        if st.button(
                            f"📝 Log trade for {ticker}",
                            key=f"log_btn_{ticker}_{act['type']}",
                            use_container_width=True,
                        ):
                            st.session_state["_prefill_trade"] = {
                                "ticker":  ticker,
                                "action":  _default_action,
                                "shares":  _default_shares,
                                "price":   act["price"],
                                "trigger": "RECOMMENDATION",
                                "notes":   f"Based on advisor recommendation: {act['title']}",
                            }
                            st.session_state["_pending_page"] = "📒 Trade Journal"
                            st.rerun()
                    with note_col:
                        st.caption(
                            "⚠️ Algorithmic analysis — not personal financial advice. "
                            "Verify all data at the sources above before acting."
                        )
        else:
            st.success("✅ Portfolio is well-balanced — no rebalancing actions needed at this time.")

        st.divider()

        # Diversification advisor — flat
        st.subheader("📋 Diversification Advisor")
        st.caption("Data-driven recommendations based on your sector weights, pairwise correlations, and analyst signals.")
        if not div_recs:
            st.success("✅ No major diversification gaps — your portfolio is well balanced.")
        else:
            reduce_recs = [r for r in div_recs if r["type"] in ("REDUCE", "PAIR_RISK")]
            add_recs    = [r for r in div_recs if r["type"] == "ADD"]

            if reduce_recs:
                st.markdown("### 🔻 Reduce / Rebalance")
                for rec in reduce_recs:
                    urgency_color = "#ff4444" if rec["urgency"] == "high" else "#ffbb33"
                    if rec["type"] == "REDUCE":
                        with st.container(border=True):
                            rc1, rc2 = st.columns([3, 1])
                            with rc1:
                                st.markdown(
                                    f"<span style='color:{urgency_color};font-weight:bold'>"
                                    f"{'🔴' if rec['urgency']=='high' else '🟡'} "
                                    f"{rec['sector']} — {rec['current_pct']}% → {rec['target_pct']}% target"
                                    f"</span>",
                                    unsafe_allow_html=True,
                                )
                                st.caption(rec["reason"])
                            with rc2:
                                st.metric("Reduce by", f"${rec['reduce_dollars']:,.0f}",
                                          f"-{rec['reduce_pct']:.1f}%", delta_color="inverse")
                            if rec["weakest_tickers"]:
                                st.markdown("**Trim candidates** *(lowest conviction first)*:")
                                cols = st.columns(len(rec["weakest_tickers"]))
                                for col, wt in zip(cols, rec["weakest_tickers"]):
                                    sig_clean = wt["signal"].split()[-1] if wt["signal"] else "—"
                                    col.markdown(
                                        f"**{wt['ticker']}**  \n"
                                        f"Score: {wt['score']:.0f}/100  \n"
                                        f"Signal: {sig_clean}  \n"
                                        f"P&L: {wt['pnl_pct']:+.1f}%  \n"
                                        f"Weight: {wt['weight']:.1f}%"
                                    )
                    elif rec["type"] == "PAIR_RISK":
                        with st.container(border=True):
                            st.markdown(
                                f"🔴 **Correlated pair: {rec['t1']} × {rec['t2']}** "
                                f"— {rec['corr']:.2f} correlation"
                            )
                            st.caption(rec["reason"])
                            st.markdown(
                                f"Consider trimming **{rec['weaker']}** "
                                f"(score {rec['weaker_score']:.0f}/100 · "
                                f"{rec['weaker_weight']:.1f}% weight · "
                                f"P&L {rec['weaker_pnl']:+.1f}%) "
                                f"and keeping **{rec['stronger']}**."
                            )

            if add_recs:
                st.markdown("### ➕ Add for Diversification")
                for rec in add_recs:
                    with st.container(border=True):
                        ac1, ac2 = st.columns([3, 1])
                        with ac1:
                            st.markdown(
                                f"🟢 **Add {rec['sector']}** — "
                                f"currently {rec['current_pct']:.0f}% → target {rec['target_pct']:.0f}%"
                            )
                            st.caption(rec["reason"])
                            st.markdown(
                                f"Avg correlation to your existing book: "
                                f"**{rec['corr_to_tech']:.2f}** — lower = genuine diversification"
                            )
                        with ac2:
                            st.metric("Suggested add", f"${rec['add_dollars']:,.0f}",
                                      f"+{rec['gap_pct']:.1f}%", delta_color="normal")
                        st.markdown(f"**Top candidates:** {' · '.join(rec['candidates'])}")
                        btn_key = f"_div_analyze_{rec['sector'].replace(' ', '_')}"
                        if st.button(
                            f"📊 Load live scores for {', '.join(rec['candidates'][:2])}",
                            key=btn_key,
                        ):
                            st.session_state[f"_div_scores_{rec['sector']}"] = True
                        if st.session_state.get(f"_div_scores_{rec['sector']}"):
                            score_cols = st.columns(len(rec["candidates"][:2]))
                            for scol, cand in zip(score_cols, rec["candidates"][:2]):
                                try:
                                    with st.spinner(f"Loading {cand}…"):
                                        cd = load_all(cand)
                                    rev     = cd.get("revisions", {})
                                    net_rev = rev.get("net", 0)
                                    rev_label = (
                                        f"↑{net_rev} upgrades (90d)"  if net_rev > 0 else
                                        f"↓{abs(net_rev)} downgrades (90d)" if net_rev < 0 else
                                        "No recent revisions"
                                    )
                                    fin = cd.get("financials", {})
                                    pe  = fin.get("forward_pe")
                                    fcf = fin.get("fcf_yield")
                                    with scol:
                                        st.metric(cand, f"{cd['total']:.0f}/100", cd["rec"]["label"])
                                        st.caption(f"Fwd P/E: {pe:.1f}" if pe else "Fwd P/E: N/A")
                                        st.caption(f"FCF Yield: {fcf:.1f}%" if fcf else "FCF Yield: N/A")
                                        st.caption(f"Revisions: {rev_label}")
                                except Exception:
                                    scol.warning(f"Could not load {cand}")

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 3 — RISK ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_risk:
        # ── Portfolio Risk Dashboard ──────────────────────────────────────────
        if _port_risk:
            st.markdown("### Portfolio Risk Dashboard")
            _rfr_display = _get_rfr()
            st.caption(
                "All metrics derived from 6-month weighted daily portfolio returns. "
                f"Risk-free rate: {_rfr_display*100:.2f}% (live 13-week T-bill, ^IRX). "
                "Weights are current allocation — not time-weighted."
            )

            _pr   = _port_risk
            _beta = _pr.get("beta")
            _vol  = _pr.get("ann_volatility")
            _sh   = _pr.get("sharpe")
            _so   = _pr.get("sortino")
            _var  = _pr.get("var_95_pct")
            _cvar = _pr.get("cvar_95_pct")
            _mdd  = _pr.get("max_drawdown")

            _rm1, _rm2, _rm3, _rm4, _rm5, _rm6, _rm7 = st.columns(7)

            # Beta
            if _beta is not None:
                _beta_lbl = "High ↑" if _beta > 1.2 else ("Low ↓" if _beta < 0.8 else "Market-like")
                _rm1.metric("Portfolio Beta", f"{_beta:.2f}", _beta_lbl,
                            delta_color="inverse" if _beta > 1.4 else "off",
                            help=_tip("Portfolio Beta"))
            else:
                _rm1.metric("Portfolio Beta", "—", help=_tip("Portfolio Beta"))

            # Annualised Volatility
            _vol_lbl = (
                "Low"      if (_vol or 0) < 15 else
                "Moderate" if (_vol or 0) < 20 else
                "Elevated" if (_vol or 0) < 30 else "High"
            )
            _rm2.metric("Ann. Volatility", f"{_vol:.1f}%" if _vol is not None else "—",
                        _vol_lbl, delta_color="off",
                        help=_tip("Portfolio Volatility"))

            # Sharpe
            if _sh is not None:
                _sh_lbl = (
                    "Excellent" if _sh >= 1.5 else
                    "Good"      if _sh >= 1.0 else
                    "Acceptable" if _sh >= 0.5 else "Weak"
                )
                _rm3.metric("Sharpe Ratio", f"{_sh:.2f}", _sh_lbl,
                            delta_color="normal" if _sh >= 1.0 else "inverse",
                            help=_tip("Sharpe Ratio"))
            else:
                _rm3.metric("Sharpe Ratio", "—", help=_tip("Sharpe Ratio"))

            # Sortino
            if _so is not None:
                _so_lbl = (
                    "Excellent" if _so >= 2.0 else
                    "Good"      if _so >= 1.0 else "Weak"
                )
                _rm4.metric("Sortino Ratio", f"{_so:.2f}", _so_lbl,
                            delta_color="normal" if _so >= 1.0 else "inverse",
                            help=_tip("Sortino Ratio"))
            else:
                _rm4.metric("Sortino Ratio", "—", help=_tip("Sortino Ratio"))

            # VaR 95% (daily)
            if _var is not None:
                _var_dollar = abs(_var / 100 * total_val)
                _rm5.metric("Daily VaR 95%", f"{_var:.2f}%",
                            f"≈ ${_var_dollar:,.0f} / day",
                            delta_color="off", help=_tip("Portfolio VaR"))
            else:
                _rm5.metric("Daily VaR 95%", "—", help=_tip("Portfolio VaR"))

            # CVaR / Expected Shortfall
            if _cvar is not None:
                _cvar_dollar = abs(_cvar / 100 * total_val)
                _rm6.metric("CVaR (Tail Risk)", f"{_cvar:.2f}%",
                            f"≈ ${_cvar_dollar:,.0f} avg bad day",
                            delta_color="off", help=_tip("Portfolio CVaR"))
            else:
                _rm6.metric("CVaR (Tail Risk)", "—", help=_tip("Portfolio CVaR"))

            # Max Drawdown
            if _mdd is not None:
                _mdd_lbl = (
                    "Modest"      if _mdd > -10 else
                    "Normal"      if _mdd > -20 else
                    "Significant" if _mdd > -30 else "Severe"
                )
                _rm7.metric("Max Drawdown", f"{_mdd:.1f}%", _mdd_lbl,
                            delta_color="off", help=_tip("Portfolio Max Drawdown"))
            else:
                _rm7.metric("Max Drawdown", "—", help=_tip("Portfolio Max Drawdown"))

            # Interpretation banner
            _risk_flags = []
            if _beta is not None and _beta > 1.4:
                _risk_flags.append(f"Beta {_beta:.2f} — portfolio moves {_beta:.1f}× the market in both directions")
            if _vol is not None and _vol > 25:
                _risk_flags.append(f"Volatility {_vol:.0f}% annualised — expect ±{_vol/16:.1f}% daily swings on average")
            if _sh is not None and _sh < 0.5:
                _risk_flags.append(f"Sharpe {_sh:.2f} — poor risk-adjusted return; the risk taken is not being rewarded")
            if _mdd is not None and _mdd < -20:
                _risk_flags.append(f"Drawdown {_mdd:.0f}% — portfolio spent time significantly below its high-water mark")

            if _risk_flags:
                st.warning("⚠️ **Risk flags:** " + "  ·  ".join(_risk_flags))
            else:
                st.success(
                    "✅ Portfolio risk metrics are within acceptable parameters "
                    "for a growth-tilted equity portfolio."
                )

            # Drawdown chart
            _dd_series = _pr.get("drawdown_series")
            if _dd_series is not None and len(_dd_series) > 1:
                _current_dd = float(_dd_series.iloc[-1])
                _dd_fig = go.Figure()
                _dd_fig.add_trace(go.Scatter(
                    x=list(_dd_series.index),
                    y=list(_dd_series),
                    fill="tozeroy",
                    fillcolor="rgba(255,68,68,0.15)",
                    line=dict(color="#ff4444", width=1.5),
                    name="Drawdown",
                    hovertemplate="%{x|%b %d}: %{y:.2f}%<extra>Portfolio Drawdown</extra>",
                ))
                _dd_fig.add_hline(y=0,   line_color="#555", line_width=1)
                _dd_fig.add_hline(y=-10, line_dash="dash", line_color="#ffbb33", line_width=1,
                                  annotation_text="−10%", annotation_position="right")
                _dd_fig.add_hline(y=-20, line_dash="dash", line_color="#ff4444",  line_width=1,
                                  annotation_text="−20%", annotation_position="right")
                _dd_fig.update_layout(
                    title="Portfolio Drawdown — 6 Months",
                    template="plotly_dark", height=280,
                    margin=dict(l=0, r=60, t=40, b=0),
                    yaxis=dict(ticksuffix="%", gridcolor="#1f2937", zeroline=False),
                    xaxis=dict(gridcolor="#1f2937"),
                    plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
                    showlegend=False,
                )
                st.plotly_chart(_dd_fig, use_container_width=True)
                st.caption(
                    "Drawdown = portfolio decline from its most recent peak (high-water mark). "
                    "Red fill = periods below prior high. "
                    f"Current drawdown from peak: **{_current_dd:.1f}%**"
                )

            # ── Beta contribution breakdown ────────────────────────────────
            if _beta is not None and held_data:
                _bc_rows = []
                for _, _bcrow in port_df.iterrows():
                    _bct = _bcrow["Ticker"]
                    _bcb = ((held_data.get(_bct) or {})
                            .get("risk_metrics", {})
                            .get("beta"))
                    _bcw = float(_bcrow.get("Weight (%)", 0))
                    if _bcb is not None and _bcw > 0:
                        _bcb = float(_bcb)
                        _bc_rows.append({
                            "ticker":       _bct,
                            "beta":         round(_bcb, 2),
                            "weight_pct":   round(_bcw, 1),
                            "contribution": round(_bcb * _bcw / 100, 3),
                        })
                if _bc_rows:
                    _bc_rows.sort(key=lambda x: -x["contribution"])
                    _bc_tickers = [r["ticker"]       for r in _bc_rows]
                    _bc_contribs = [r["contribution"] for r in _bc_rows]
                    _bc_betas    = [r["beta"]          for r in _bc_rows]
                    _bc_weights  = [r["weight_pct"]    for r in _bc_rows]
                    _bc_colors   = [
                        "#ff4444" if c > 0.15 else
                        "#ffbb33" if c > 0.08 else
                        "#00C851"
                        for c in _bc_contribs
                    ]
                    _bc_fig = go.Figure(go.Bar(
                        x=_bc_contribs,
                        y=_bc_tickers,
                        orientation="h",
                        marker_color=_bc_colors,
                        text=[
                            f"β {b:.2f} · {w:.0f}% weight → {c:.3f} contrib"
                            for b, w, c in zip(_bc_betas, _bc_weights, _bc_contribs)
                        ],
                        textposition="outside",
                        hovertemplate=(
                            "<b>%{y}</b><br>"
                            "Beta contribution: %{x:.3f}<br>"
                            "<extra></extra>"
                        ),
                    ))
                    _bc_fig.add_vline(
                        x=_beta / len(_bc_rows),
                        line_dash="dot", line_color="#888",
                        annotation_text="Equal contrib",
                        annotation_position="top right",
                    )
                    _bc_fig.update_layout(
                        title=f"Beta Contribution by Position  (Portfolio β = {_beta:.2f})",
                        template="plotly_dark",
                        height=max(220, 36 * len(_bc_rows)),
                        margin=dict(l=0, r=160, t=40, b=0),
                        xaxis=dict(title="Weighted beta contribution", gridcolor="#1f2937"),
                        yaxis=dict(autorange="reversed", gridcolor="#1f2937"),
                        plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
                        showlegend=False,
                    )
                    st.plotly_chart(_bc_fig, use_container_width=True)
                    st.caption(
                        "Contribution = position beta × portfolio weight. "
                        "🔴 Red bars are the primary beta drivers — reducing these positions "
                        "has the highest impact on lowering portfolio beta."
                    )

            st.divider()

        # ── Diversification & Correlation ─────────────────────────────────────
        if corr_df.empty:
            st.info("Need at least 2 holdings with price history to compute correlations.")
        else:
            dc1, dc2, dc3 = st.columns(3)
            dc1.metric("Diversification Score", f"{div_score:.0f}/100",
                       help=_tip("Diversification Score"))
            dc2.metric("Avg Portfolio Correlation", f"{avg_corr:.2f}",
                       help=_tip("Portfolio Correlation"))
            dc3.metric("High-Correlation Pairs", len(risk_pairs),
                       help="Pairs with correlation ≥ 0.65")
            st.caption(f"Classification: **{_div_label}** — weighted avg pairwise 6-month return correlation")

            if risk_pairs:
                st.markdown("**Correlated pairs — reduce diversification benefit:**")
                for rp in risk_pairs:
                    msg = (f"**{rp['t1']} × {rp['t2']}** — {rp['corr']:.2f} correlation. "
                           "These positions move together.")
                    if rp["level"] == "danger":
                        st.error(f"🔴 {msg}")
                    else:
                        st.warning(f"🟡 {msg}")
            else:
                st.success("✅ No highly correlated pairs — your portfolio is well diversified.")

            tickers_list = corr_df.index.tolist()
            z_vals = corr_df.values.tolist()
            z_text = [[f"{v:.2f}" for v in row] for row in corr_df.values]
            hm = go.Figure(go.Heatmap(
                z=z_vals, x=tickers_list, y=tickers_list,
                text=z_text, texttemplate="%{text}", textfont=dict(size=11),
                colorscale=[[0.0, "#00C851"], [0.5, "#1e1e2e"], [1.0, "#ff4444"]],
                zmin=-1, zmax=1, showscale=True,
                colorbar=dict(title="Corr", tickvals=[-1, -0.5, 0, 0.5, 1],
                              ticktext=["-1.0", "-0.5", "0", "+0.5", "+1.0"], len=0.8),
            ))
            hm.update_layout(
                template="plotly_dark",
                height=max(300, 65 * len(tickers_list)),
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(side="bottom", tickangle=-30),
            )
            st.plotly_chart(hm, use_container_width=True)
            st.caption(
                "🟢 Green = low/negative correlation (genuine diversification)  |  "
                "⬛ Dark = near-zero (independent)  |  "
                "🔴 Red = high correlation (positions move together).  "
                "Diagonal is always +1.0."
            )

        # ── Risk Action Plan ──────────────────────────────────────────────────
        if _risk_advisor_recs:
            st.divider()
            st.markdown("### 📋 Risk Action Plan")
            st.caption(
                "Synthesises your 7 portfolio risk metrics into ranked, evidence-backed actions. "
                "Each card shows the problem with dollar impact, which specific tickers are driving it, "
                "an exact recommendation, and the institutional perspective behind it."
            )

            with st.expander(
                f"ℹ️ Why portfolio beta target is {PORTFOLIO_BETA_ELEVATED:.1f} (soft) / "
                f"{PORTFOLIO_BETA_CEILING:.1f} (hard)?",
                expanded=False,
            ):
                st.markdown(
                    f"""
**Two thresholds, both static** — defined in `stock_analyzer/constants.py`:

| Threshold | Value | Meaning |
|---|---|---|
| `PORTFOLIO_BETA_ELEVATED` | **{PORTFOLIO_BETA_ELEVATED:.1f}** | Soft warning — Risk Advisor recommends trimming |
| `PORTFOLIO_BETA_CEILING`  | **{PORTFOLIO_BETA_CEILING:.1f}** | Hard ceiling — trim required, do not add high-beta names |

**Why these specific numbers?**

- **{PORTFOLIO_BETA_ELEVATED:.1f}** is the point at which the asymmetric-loss math starts to materially eat into risk-adjusted returns. A 10% market correction costs you proportionally more than the corresponding rally pays — Sharpe-adjusted, you stop being compensated for the extra volatility.
- **{PORTFOLIO_BETA_CEILING:.1f}** is the conventional institutional cap for managed equity accounts. Above this, professional risk teams require active mitigation regardless of conviction in individual names. The PM's job is not to eliminate beta — it's to ensure you're being paid for it through Sharpe.

**Why static, not regime-adjusted?**

The app deliberately keeps target beta fixed across regimes. In risk-off conditions, the response is to **trim harder toward the same target**, not to lower the target itself. A moving target makes it impossible to evaluate whether trades improved the risk profile. Investment policy, not market-timing.

**To change these values**, edit `stock_analyzer/constants.py` and update `project_decision_thresholds.md` with the rationale — they are treated as policy decisions, not code tuning.
"""
                )

            _n_high = sum(1 for r in _risk_advisor_recs if r["priority"] == "HIGH")
            _n_med  = sum(1 for r in _risk_advisor_recs if r["priority"] == "MEDIUM")
            _n_ok   = sum(1 for r in _risk_advisor_recs if r["priority"] == "OK")

            _rac1, _rac2, _rac3 = st.columns(3)
            _rac1.metric("🔴 Action Required", _n_high, help="Requires attention this week")
            _rac2.metric("🟡 Monitor",          _n_med,  help="Review before next rebalance")
            _rac3.metric("✅ Well Managed",      _n_ok,   help="No action needed — reinforce the discipline")

            st.markdown("")

            # Sort: HIGH → MEDIUM → OK
            _priority_order = {"HIGH": 0, "MEDIUM": 1, "OK": 2}
            _sorted_recs = sorted(
                _risk_advisor_recs,
                key=lambda x: _priority_order.get(x["priority"], 3),
            )

            for _rec in _sorted_recs:
                _pri   = _rec["priority"]
                _rtype = _rec["type"]

                # ── OK cards — compact, collapsed ────────────────────────────
                if _pri == "OK":
                    with st.expander(f"✅  {_rec['title']}", expanded=False):
                        st.caption(_rec["institutional_lens"])
                    continue

                # ── HIGH / MEDIUM action cards ────────────────────────────────
                _icon        = "🔴" if _pri == "HIGH" else "🟡"
                _border_clr  = "#ff4444" if _pri == "HIGH" else "#ffbb33"
                _expand      = _pri == "HIGH"

                with st.expander(
                    f"{_icon} **{_pri}** · {_rec['title']}",
                    expanded=_expand,
                ):
                    # Problem banner
                    st.markdown(
                        f"<div style='padding:10px 14px;background:#1a1a1a;border-radius:6px;"
                        f"border-left:4px solid {_border_clr};margin-bottom:14px'>"
                        f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                        f"letter-spacing:0.09em;text-transform:uppercase'>The Problem</span><br>"
                        f"<span style='color:#eee'>{_rec['problem']}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                    _left, _right = st.columns([1, 1])

                    # Left — root cause + offending tickers
                    with _left:
                        st.markdown("**Root Cause**")
                        if _rec.get("root_cause"):
                            st.markdown(
                                f"<div style='color:#bbb;font-size:0.88em;margin-bottom:8px'>"
                                f"{_rec['root_cause']}</div>",
                                unsafe_allow_html=True,
                            )
                        if _rec.get("root_tickers"):
                            for _rt in _rec["root_tickers"]:
                                # Colour the value badge per metric type
                                if _rtype == "beta":
                                    _vc = "#ff4444" if _rt["value"] > 1.4 else "#ffbb33"
                                elif _rtype == "sharpe":
                                    _vc = "#ff4444" if _rt["value"] < 0.3 else "#ffbb33"
                                elif _rtype in ("volatility", "drawdown"):
                                    _vc = "#ff4444"
                                else:
                                    _vc = "#ffbb33"
                                st.markdown(
                                    f"<div style='background:#111;border-radius:4px;"
                                    f"padding:6px 10px;margin-top:4px;font-size:0.82em'>"
                                    f"<b style='color:#fff'>{_rt['ticker']}</b>&nbsp;&nbsp;"
                                    f"<span style='color:{_vc}'>{_rt['label']}</span>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )

                    # Right — recommendation + expected outcome
                    with _right:
                        st.markdown(
                            f"<div style='padding:10px 14px;background:#0d2137;border-radius:6px;"
                            f"border-left:4px solid #4a9eff;margin-bottom:10px'>"
                            f"<span style='font-size:0.72em;color:#4a9eff;font-weight:700;"
                            f"letter-spacing:0.09em;text-transform:uppercase'>Recommendation</span><br>"
                            f"<span style='color:#eee;font-size:0.9em'>{_rec['recommendation']}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        if _rec.get("expected_outcome"):
                            st.markdown(
                                f"<div style='padding:10px 14px;background:#0d1a0d;border-radius:6px;"
                                f"border-left:4px solid #00C851'>"
                                f"<span style='font-size:0.72em;color:#00C851;font-weight:700;"
                                f"letter-spacing:0.09em;text-transform:uppercase'>Expected Outcome</span><br>"
                                f"<span style='color:#ccc;font-size:0.88em'>{_rec['expected_outcome']}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                    # Institutional Lens — full width
                    if _rec.get("institutional_lens"):
                        st.markdown("")
                        st.info(f"**Institutional Lens** · {_rec['institutional_lens']}")

                    # Defensive picks — shown inside volatility and beta cards
                    if _rtype in ("volatility", "beta"):
                        st.markdown("")
                        with st.expander("🔍 Find Defensive Picks — Implement This Recommendation", expanded=False):
                            _def_sectors = {"Healthcare & Biotech", "Consumer Staples & Retail"}
                            _sr = st.session_state.get("scanner_results")
                            if _sr is None or _sr.empty:
                                st.info(
                                    "Run the **Market Scanner** first to populate defensive sector picks here. "
                                    "The scanner covers Healthcare & Biotech and Consumer Staples & Retail "
                                    "tickers — results will appear here automatically once it has run."
                                )
                            else:
                                _def_picks = (
                                    _sr[
                                        _sr["Sector"].isin(_def_sectors) &
                                        ~_sr["Ticker"].isin(set(held_tickers))
                                    ]
                                    .sort_values("Score", ascending=False)
                                    .head(5)
                                    .reset_index(drop=True)
                                )
                                if _def_picks.empty:
                                    st.info(
                                        "No unowned defensive picks found. "
                                        "You may already hold the top-rated tickers in these sectors."
                                    )
                                else:
                                    # Composite scores for defensive picks — fetched on demand
                                    _def_comp_key  = f"_def_composites_{_rtype}"
                                    _def_comps     = st.session_state.get(_def_comp_key, {})
                                    _comps_loaded  = bool(_def_comps)
                                    _def_tickers   = _def_picks["Ticker"].tolist()

                                    st.caption(
                                        "Top-ranked Healthcare & Consumer Staples picks from the scanner — "
                                        "not already in your portfolio. Momentum score shown; "
                                        "load composite scores to validate before acting."
                                    )

                                    if not _comps_loaded:
                                        if st.button(
                                            "📊 Load Composite Scores",
                                            key=f"_def_load_comp_{_rtype}",
                                            help="Fetches full Technical + Fundamental + Sentiment scores for each pick",
                                        ):
                                            with st.spinner("Fetching composite scores…"):
                                                for _tc in _def_tickers:
                                                    if _tc not in _def_comps:
                                                        try:
                                                            _def_comps[_tc] = load_all(_tc)
                                                        except Exception:
                                                            pass
                                            st.session_state[_def_comp_key] = _def_comps
                                            st.rerun()

                                    for _, _dp in _def_picks.iterrows():
                                        _dp_ticker  = str(_dp["Ticker"])
                                        _dp_sector  = str(_dp.get("Sector", ""))
                                        _dp_mom_sc  = float(_dp.get("Score", 0))
                                        _dp_rsi     = _dp.get("RSI")
                                        _dp_trend   = str(_dp.get("Trend", ""))
                                        _dp_mom     = _dp.get("Momentum")

                                        # Composite data (if loaded)
                                        _dp_comp    = _def_comps.get(_dp_ticker, {})
                                        _dp_cscore  = float(_dp_comp.get("total", 0)) if _dp_comp else None
                                        _dp_clabel  = str((_dp_comp.get("rec") or {}).get("label", "")) if _dp_comp else ""

                                        # Skip Sell-rated composites — not suitable for defensive addition
                                        if _dp_cscore is not None and _dp_cscore < 45:
                                            continue

                                        _sig_clr = (
                                            "#00C851" if "Buy" in _dp_clabel else
                                            "#ff4444" if "Sell" in _dp_clabel else "#888"
                                        ) if _dp_clabel else (
                                            "#00C851" if _dp_mom_sc >= 65 else "#888"
                                        )
                                        _sc_clr = (
                                            "#00C851" if _dp_mom_sc >= 65 else
                                            "#ffbb33" if _dp_mom_sc >= 50 else "#888"
                                        )
                                        _rsi_str = f"RSI {_dp_rsi:.0f}" if _dp_rsi is not None else ""
                                        _mom_str = (f"  ·  1M {_dp_mom:+.1f}%" if _dp_mom is not None else "")
                                        _detail  = "  ·  ".join(filter(None, [_rsi_str, _dp_trend])) + _mom_str

                                        # Score line: show composite if loaded, momentum always
                                        if _dp_cscore is not None:
                                            _comp_sc_clr = (
                                                "#00C851" if _dp_cscore >= 65 else
                                                "#ffbb33" if _dp_cscore >= 55 else "#888"
                                            )
                                            _score_line = (
                                                f"<span style='color:{_sc_clr}'>Momentum {_dp_mom_sc:.0f}/100</span>"
                                                f"<span style='color:#555;margin:0 6px'>·</span>"
                                                f"<span style='color:{_comp_sc_clr}'>Composite {_dp_cscore:.0f}/100</span>"
                                                f"<span style='font-size:0.78em;color:{_sig_clr};margin-left:8px'>{_dp_clabel}</span>"
                                            )
                                        else:
                                            _score_line = (
                                                f"<span style='color:{_sc_clr}'>Momentum {_dp_mom_sc:.0f}/100</span>"
                                                f"<span style='font-size:0.73em;color:#6b7280;margin-left:8px'>"
                                                f"composite not loaded</span>"
                                            )

                                        _dp_col1, _dp_col2 = st.columns([3, 1])
                                        with _dp_col1:
                                            st.markdown(
                                                f"<div style='background:#111827;border-radius:6px;"
                                                f"padding:10px 14px;margin-bottom:6px'>"
                                                f"<span style='font-size:1em;font-weight:700;color:#f9fafb'>"
                                                f"{_dp_ticker}</span>"
                                                f"<span style='font-size:0.75em;color:#6b7280;margin-left:8px'>"
                                                f"{_dp_sector}</span><br>"
                                                f"{_score_line}<br>"
                                                f"<span style='font-size:0.73em;color:#6b7280'>{_detail}</span>"
                                                f"</div>",
                                                unsafe_allow_html=True,
                                            )
                                        with _dp_col2:
                                            if st.button(
                                                f"Analyze {_dp_ticker}",
                                                key=f"_def_analyze_{_dp_ticker}_{_rtype}",
                                                use_container_width=True,
                                            ):
                                                st.session_state["_analysis_ticker"] = _dp_ticker
                                                st.session_state["_pending_page"]    = "📈 Analysis"
                                                st.session_state["_nav_origin"]      = "🏠 Home"
                                                st.rerun()

        # ── Stress Testing ────────────────────────────────────────────────────
        st.divider()
        st.markdown("### 🔥 Stress Testing & Scenario Analysis")
        st.caption(
            "Estimates portfolio impact under market shock scenarios using each position's "
            "individual beta vs SPY. Historical scenarios apply sector-specific drawdowns "
            "from that event — more accurate than a flat beta adjustment."
        )

        _st_beta = _port_risk.get("beta") if _port_risk else None

        # Scenario selector + custom shock slider side by side
        _st_col1, _st_col2 = st.columns([2, 1])
        with _st_col1:
            _sc_labels = [s["label"] for s in SCENARIOS] + ["Custom Scenario"]
            _sc_choice = st.selectbox("Select scenario", _sc_labels, key="_stress_scenario")
        with _st_col2:
            _custom_move = st.slider(
                "Custom SPY move (%)", min_value=-50, max_value=20,
                value=-15, step=1, key="_stress_custom",
                help="Only used when 'Custom Scenario' is selected above",
            )

        # Resolve which scenario to run
        if _sc_choice == "Custom Scenario":
            _active_sc = {
                "id": "custom", "label": f"Custom  (SPY {_custom_move:+.0f}%)",
                "description": f"User-defined scenario: SPY {_custom_move:+.0f}%. "
                               "Beta-adjusted impact per position, no sector overrides.",
                "spy_move": float(_custom_move), "sector_key": None,
            }
            _sc_result = run_scenario(_active_sc, port_df, held_data, _st_beta,
                                      custom_spy_move=float(_custom_move))
        else:
            _active_sc  = next(s for s in SCENARIOS if s["label"] == _sc_choice)
            _sc_result  = run_scenario(_active_sc, port_df, held_data, _st_beta)

        if _sc_result:
            _est_pnl   = _sc_result["estimated_port_pnl"]
            _est_move  = _sc_result["estimated_port_move"]
            _post_val  = _sc_result["post_shock_value"]
            _port_val  = _sc_result["portfolio_value"]
            _pnl_clr   = "#ff4444" if _est_pnl < 0 else "#00C851"

            # Summary banner
            st.markdown(
                f"<div style='padding:12px 18px;background:#1a1a1a;"
                f"border-radius:8px;border-left:5px solid {_pnl_clr};margin:10px 0'>"
                f"<span style='font-size:0.75em;color:#888;font-weight:700;"
                f"letter-spacing:0.08em;text-transform:uppercase'>Scenario: {_active_sc['label']}</span><br>"
                f"<span style='color:#bbb;font-size:0.88em'>{_active_sc['description']}</span><br><br>"
                f"<span style='font-size:1.35em;font-weight:700;color:{_pnl_clr}'>"
                f"Estimated Portfolio P&L: {_m(f'${_est_pnl:+,.0f}')}  ({_est_move:+.1f}%)</span><br>"
                f"<span style='color:#aaa;font-size:0.9em'>"
                f"Portfolio value: {_m(f'${_port_val:,.0f}')}  →  {_m(f'${_post_val:,.0f}')} after shock</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # KPI summary
            _s1, _s2, _s3, _s4 = st.columns(4)
            _s1.metric("SPY Shock",        f"{_sc_result['spy_move']:+.0f}%")
            _s2.metric("Est. Portfolio Δ", f"{_est_move:+.1f}%",
                       delta_color="inverse" if _est_pnl < 0 else "normal")
            _s3.metric("Est. $ Impact",    _m(f"${_est_pnl:+,.0f}"),
                       delta_color="inverse" if _est_pnl < 0 else "normal")
            _s4.metric("Post-Shock Value", _m(f"${_post_val:,.0f}"))

            # Position impact table
            if _sc_result["rows"]:
                import plotly.graph_objects as _go_st
                _st_rows = _sc_result["rows"]
                _st_tickers = [r["Ticker"] for r in _st_rows]
                _st_pnls    = [r["Est. P&L ($)"] for r in _st_rows]
                _st_moves   = [r["Est. Move (%)"] for r in _st_rows]
                _st_clrs    = ["#00C851" if v >= 0 else "#ff4444" for v in _st_pnls]

                _st_fig = _go_st.Figure(_go_st.Bar(
                    x=_st_tickers,
                    y=_st_pnls,
                    marker_color=_st_clrs,
                    text=[("••••" if st.session_state.get("_privacy") else f"${v:+,.0f}") for v in _st_pnls],
                    textposition="outside",
                    customdata=list(zip(_st_moves, [r["Weight (%)"] for r in _st_rows])),
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        + ("Est. P&L: ••••<br>" if st.session_state.get("_privacy") else "Est. P&L: $%{y:+,.0f}<br>")
                        + "Est. Move: %{customdata[0]:+.1f}%<br>"
                        "Weight: %{customdata[1]:.1f}%"
                        "<extra></extra>"
                    ),
                ))
                _st_fig.add_hline(y=0, line_color="#444", line_width=1)
                _st_fig.update_layout(
                    title="Position-Level Impact (sorted by loss)",
                    template="plotly_dark",
                    height=max(260, len(_st_rows) * 28 + 80),
                    yaxis_title="Estimated P&L ($)",
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(_st_fig, use_container_width=True)

                # Detail table
                with st.expander("📋 Full position breakdown", expanded=False):
                    _st_df = pd.DataFrame(_st_rows)
                    def _st_row_style(row):
                        v = row.get("Est. P&L ($)", 0)
                        if v < -1000:
                            return ["background-color:rgba(255,68,68,0.10)"] * len(row)
                        if v > 0:
                            return ["background-color:rgba(0,200,81,0.08)"] * len(row)
                        return [""] * len(row)
                    st.dataframe(
                        _st_df.style.apply(_st_row_style, axis=1).format({
                            "Weight (%)":       "{:.1f}%",
                            "Market Value ($)": "${:,.0f}",
                            "Est. Move (%)":    "{:+.1f}%",
                            "Est. P&L ($)":     "${:+,.0f}",
                        }),
                        use_container_width=True, hide_index=True,
                    )

            # Most exposed + any gainers
            _me_col, _ag_col = st.columns([1, 1])
            with _me_col:
                if _sc_result["most_exposed"]:
                    st.markdown("**Most Exposed Positions**")
                    for _me in _sc_result["most_exposed"]:
                        st.markdown(
                            f"<div style='padding:8px 12px;background:#1a0a0a;"
                            f"border-radius:6px;border-left:3px solid #ff4444;margin:4px 0;"
                            f"font-size:0.88em'>"
                            f"<b style='color:#ff6666'>{_me['Ticker']}</b> · {_me['Sector']}<br>"
                            f"<span style='color:#ccc'>{_me['Est. Move (%)']:+.1f}%  ·  "
                            f"${_me['Est. P&L ($)']:+,.0f}  ·  {_me['Weight (%)']:.1f}% weight</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
            with _ag_col:
                if _sc_result["any_gainers"]:
                    st.markdown("**Positions That May Benefit**")
                    for _ag in _sc_result["any_gainers"]:
                        st.markdown(
                            f"<div style='padding:8px 12px;background:#0a1a0a;"
                            f"border-radius:6px;border-left:3px solid #00C851;margin:4px 0;"
                            f"font-size:0.88em'>"
                            f"<b style='color:#00C851'>{_ag['Ticker']}</b> · {_ag['Sector']}<br>"
                            f"<span style='color:#ccc'>{_ag['Est. Move (%)']:+.1f}%  ·  "
                            f"${_ag['Est. P&L ($)']:+,.0f}  ·  {_ag['Weight (%)']:.1f}% weight</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.info("No positions estimated to benefit under this scenario — "
                            "consider defensive names (Healthcare, Energy, Defense) for hedging.")

            # Scenario comparison summary — all scenarios, one row each
            st.markdown("")
            with st.expander("📊 Compare all scenarios at a glance", expanded=False):
                _all_results = run_all_scenarios(port_df, held_data, _st_beta)
                if _all_results:
                    _cmp_rows = []
                    for _ar in _all_results:
                        _cmp_rows.append({
                            "Scenario":      _ar["label"],
                            "SPY Move":      f"{_ar['spy_move']:+.0f}%",
                            "Portfolio Δ":   f"{_ar['estimated_port_move']:+.1f}%",
                            "Est. P&L ($)":  _ar["estimated_port_pnl"],
                            "Post Value ($)": _ar["post_shock_value"],
                        })
                    _cmp_df = pd.DataFrame(_cmp_rows)

                    def _cmp_style(row):
                        v = row.get("Est. P&L ($)", 0)
                        if v < -_port_val * 0.20:
                            return ["background-color:rgba(255,68,68,0.15)"] * len(row)
                        if v < -_port_val * 0.10:
                            return ["background-color:rgba(255,187,51,0.10)"] * len(row)
                        return [""] * len(row)

                    st.dataframe(
                        _cmp_df.style.apply(_cmp_style, axis=1).format({
                            "Est. P&L ($)":   "${:+,.0f}",
                            "Post Value ($)":  "${:,.0f}",
                        }),
                        use_container_width=True, hide_index=True,
                    )
                    st.caption(
                        "🔴 Red = estimated loss > 20% of portfolio  ·  "
                        "🟡 Amber = estimated loss 10–20%  ·  "
                        "Beta-adjusted for market-wide scenarios; sector overrides for historical events."
                    )

            st.info(
                "**Methodology:** Market-wide scenarios multiply each position's individual beta × SPY shock. "
                "Named historical scenarios (2022, 2020, AI Unwind) apply sector-specific drawdowns "
                "observed during those events — more accurate for portfolios with sector concentration. "
                "Estimates assume linear beta and do not model liquidity effects or margin calls."
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 4 — RELATIVE STRENGTH
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_rs:
        if not h_rets:
            st.info("Need at least 1 holding with price history to compute relative strength.")
        else:
            st.caption(
                "Each holding's 6-month return vs its sector ETF benchmark. "
                "**Outperforming** = genuine stock-specific alpha, not just riding the sector tide. "
                "**Underperforming** = the sector rallied but this position lagged — a Institutional rotation flag."
            )

            # Holding returns bar chart (instant — uses existing price data)
            _rs_ord = port_df[port_df["Ticker"].isin(h_rets)]["Ticker"].tolist()
            _rs_vals = [h_rets[t] for t in _rs_ord]
            ret_fig = go.Figure(go.Bar(
                x=_rs_ord, y=_rs_vals,
                marker_color=["#00C851" if v >= 0 else "#ff4444" for v in _rs_vals],
                text=[f"{v:+.1f}%" for v in _rs_vals],
                textposition="outside",
            ))
            ret_fig.update_layout(
                title="6-Month Holding Returns",
                template="plotly_dark", height=280,
                yaxis_title="Return (%)", yaxis_zeroline=True,
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(ret_fig, use_container_width=True)

            # ETF benchmarks — gated behind button to avoid extra API calls on load
            if st.button("📊 Load sector ETF benchmarks", key="_rs_load_btn"):
                _unique_etfs = list({SECTOR_ETF.get(row["Sector"], "SPY")
                                     for _, row in port_df.iterrows()})
                _etf_rets = {}
                for _etf in _unique_etfs:
                    try:
                        with st.spinner(f"Loading {_etf}…"):
                            _hist = fetch_price_history(_etf, period="6mo")
                        if not _hist.empty and "Close" in _hist.columns:
                            _cl = _hist["Close"].dropna()
                            if len(_cl) >= 5 and float(_cl.iloc[0]) > 0:
                                _etf_rets[_etf] = round(float((_cl.iloc[-1] / _cl.iloc[0] - 1) * 100), 1)
                    except Exception:
                        pass
                st.session_state["_rs_etf_rets"] = _etf_rets

            if st.session_state.get("_rs_etf_rets"):
                etf_rets_cached = st.session_state["_rs_etf_rets"]
                rs_df = relative_strength_table(port_df, h_rets, etf_rets_cached)
                if not rs_df.empty and rs_df["Alpha (%)"].notna().any():
                    n_out   = int((rs_df["Alpha (%)"] >= 5).sum())
                    n_under = int((rs_df["Alpha (%)"] <= -5).sum())
                    n_line  = int(((rs_df["Alpha (%)"] > -5) & (rs_df["Alpha (%)"] < 5)).sum())

                    _rm1, _rm2, _rm3 = st.columns(3)
                    _rm1.metric("Outperforming", n_out,   help="Alpha ≥ +5% vs sector ETF")
                    _rm2.metric("In Line",        n_line,  help="Alpha between -5% and +5%")
                    _rm3.metric("Underperforming", n_under, help="Alpha ≤ -5% vs sector ETF")

                    # Alpha bar chart
                    _rs_sorted = rs_df.dropna(subset=["Alpha (%)"]).sort_values("Alpha (%)", ascending=False)
                    _alpha_colors = [
                        "#00C851" if a >= 5 else "#ff4444" if a <= -5 else "#888888"
                        for a in _rs_sorted["Alpha (%)"]
                    ]
                    alpha_fig = go.Figure(go.Bar(
                        x=_rs_sorted["Ticker"],
                        y=_rs_sorted["Alpha (%)"],
                        marker_color=_alpha_colors,
                        text=[f"{a:+.1f}%" for a in _rs_sorted["Alpha (%)"]],
                        textposition="outside",
                        customdata=list(zip(
                            _rs_sorted["ETF"],
                            _rs_sorted["6mo Return (%)"],
                            _rs_sorted["ETF Return (%)"],
                        )),
                        hovertemplate=(
                            "<b>%{x}</b><br>"
                            "Alpha: %{y:+.1f}%<br>"
                            "Holding 6mo: %{customdata[1]:+.1f}%<br>"
                            "Benchmark (%{customdata[0]}): %{customdata[2]:+.1f}%"
                            "<extra></extra>"
                        ),
                    ))
                    alpha_fig.add_hline(y=0, line_color="white", line_dash="dot", line_width=1)
                    alpha_fig.update_layout(
                        title="Alpha vs Sector ETF",
                        template="plotly_dark", height=300,
                        yaxis_title="Alpha (%)",
                        margin=dict(l=0, r=0, t=40, b=0),
                    )
                    st.plotly_chart(alpha_fig, use_container_width=True)
                    st.caption(
                        "🟢 Green = outperforming sector (genuine alpha)  |  "
                        "⬜ Gray = in line with sector  |  "
                        "🔴 Red = lagging sector (riding the tide or underperforming)"
                    )

                    # Styled table
                    def _alpha_col(val):
                        if isinstance(val, float):
                            if val >= 5:  return "color:#00C851;font-weight:bold"
                            if val <= -5: return "color:#ff4444"
                        return ""

                    def _status_col(val):
                        s = str(val)
                        if "Outperforming" in s: return "color:#00C851;font-weight:bold"
                        if "Underperforming" in s: return "color:#ff4444"
                        return "color:#888888"

                    _rs_disp = rs_df[["Ticker", "Sector", "6mo Return (%)", "ETF", "ETF Return (%)", "Alpha (%)", "Status"]]
                    _fmt = {"6mo Return (%)": "{:+.1f}%", "ETF Return (%)": "{:+.1f}%", "Alpha (%)": "{:+.1f}%"}
                    _styled_rs = (
                        _rs_disp.style
                        .map(_alpha_col,  subset=["Alpha (%)"])
                        .map(_status_col, subset=["Status"])
                        .format(_fmt, na_rep="—")
                    )
                    st.dataframe(_styled_rs, use_container_width=True)

                    # Institutional-style insight callouts
                    _valid = rs_df.dropna(subset=["Alpha (%)"])
                    if n_under > 0:
                        _worst = _valid.loc[_valid["Alpha (%)"].idxmin()]
                        st.warning(
                            f"⚠️ **{_worst['Ticker']}** is lagging its sector ETF ({_worst['ETF']}) "
                            f"by **{abs(_worst['Alpha (%)']):+.1f}%** over 6 months — "
                            f"the sector rallied but this position did not keep pace. "
                            f"Best practice would flag this for rotation review."
                        )
                    if n_out > 0:
                        _best = _valid.loc[_valid["Alpha (%)"].idxmax()]
                        st.success(
                            f"✅ **{_best['Ticker']}** is generating genuine alpha: "
                            f"**{_best['Alpha (%)']:+.1f}%** above its sector ETF ({_best['ETF']}) — "
                            f"stock-specific strength, not just a sector tailwind."
                        )

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 5 — MACRO
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_macro:
        st.caption(
            "Detects the current macro regime (rising/falling rates, risk-on/off) from live ETF proxies "
            "and shows which holdings are in the tailwind or headwind. "
            "Institutional practice uses macro regime overlays to tilt sector weights 3–5% above/below benchmark."
        )

        if st.button("📡 Load macro signals (TLT · SPY · VIX)", key="_macro_load_btn"):
            _macro_raw = {}
            for _sym, _period in [("TLT", "3mo"), ("SPY", "3mo"), ("^VIX", "5d")]:
                try:
                    with st.spinner(f"Loading {_sym}…"):
                        _h = fetch_price_history(_sym, period=_period)
                    if not _h.empty and "Close" in _h.columns:
                        _cl = _h["Close"].dropna()
                        if len(_cl) >= 2:
                            if _sym == "^VIX":
                                _macro_raw["vix"] = float(_cl.iloc[-1])
                            elif float(_cl.iloc[0]) > 0:
                                _macro_raw[_sym.lower() + "_ret"] = round(
                                    float((_cl.iloc[-1] / _cl.iloc[0] - 1) * 100), 1
                                )
                except Exception:
                    pass
            st.session_state["_macro_raw"] = _macro_raw

        if st.session_state.get("_macro_raw"):
            _mr = st.session_state["_macro_raw"]
            _tlt = _mr.get("tlt_ret", 0.0)
            _spy = _mr.get("spy_ret", 0.0)
            _vix = _mr.get("vix", 18.0)
            regime = detect_macro_regime(_tlt, _spy, _vix)

            # ── Regime banner ─────────────────────────────────────────────────
            _regime_colors = {
                "rising_rates":  "#ffbb33",
                "falling_rates": "#00C851",
                "risk_off":      "#ff4444",
                "risk_on":       "#00C851",
                "neutral":       "#888888",
            }
            _rc = _regime_colors.get(regime["combined"], "#888888")
            st.markdown(
                f"<div style='background:{_rc}22;border-left:4px solid {_rc};"
                f"padding:10px 14px;border-radius:4px;margin-bottom:8px'>"
                f"<b style='color:{_rc};font-size:1.1em'>Current Regime: {regime['label']}</b>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Signal strip
            _s1, _s2, _s3 = st.columns(3)
            _sig = regime["signals"]
            _s1.metric("Rates (TLT 3mo)", f"{_tlt:+.1f}%",
                       help=_sig.get("Rates (TLT)", ""))
            _s2.metric("Volatility (VIX)", f"{_vix:.0f}",
                       delta="risk-off" if _vix >= 25 else "risk-on" if _vix <= 15 else "neutral",
                       delta_color="inverse" if _vix >= 25 else "normal",
                       help=_sig.get("Volatility (VIX)", ""))
            _s3.metric("Market (SPY 3mo)", f"{_spy:+.1f}%",
                       help=_sig.get("Market (SPY)", ""))

            # ── Rotation playbook ─────────────────────────────────────────────
            _fav = REGIME_FAVORED[regime["combined"]]
            st.markdown("---")
            st.markdown("**Sector Rotation Playbook for this Regime**")
            st.caption(_fav["reason"])
            _pb1, _pb2 = st.columns(2)
            with _pb1:
                if _fav["overweight"]:
                    st.markdown("🟢 **Overweight**")
                    for _s in _fav["overweight"]:
                        rs = RATE_SENSITIVITY.get(_s, 0)
                        st.markdown(f"- {_s} &nbsp; *(rate sensitivity {rs:+.2f})*",
                                    unsafe_allow_html=True)
                else:
                    st.markdown("🟢 **Overweight** — none flagged")
            with _pb2:
                if _fav["underweight"]:
                    st.markdown("🔴 **Underweight / Reduce**")
                    for _s in _fav["underweight"]:
                        rs = RATE_SENSITIVITY.get(_s, 0)
                        st.markdown(f"- {_s} &nbsp; *(rate sensitivity {rs:+.2f})*",
                                    unsafe_allow_html=True)
                else:
                    st.markdown("🔴 **Underweight** — none flagged")

            # ── Portfolio exposure table ──────────────────────────────────────
            st.markdown("---")
            st.markdown("**Your Portfolio — Macro Alignment**")
            expo_df = portfolio_macro_exposure(port_df, regime)
            if not expo_df.empty:
                # Summary counts
                _nt = int((expo_df["Macro Alignment"] == "Tailwind ↑").sum())
                _nh = int((expo_df["Macro Alignment"] == "Headwind ↓").sum())
                _nn = int((expo_df["Macro Alignment"] == "Neutral ↔").sum())
                _headwind_weight = expo_df.loc[
                    expo_df["Macro Alignment"] == "Headwind ↓", "Weight (%)"
                ].sum()

                _ec1, _ec2, _ec3, _ec4 = st.columns(4)
                _ec1.metric("Tailwind positions", _nt,  help="Sector favored in current regime")
                _ec2.metric("Neutral positions",  _nn)
                _ec3.metric("Headwind positions", _nh,  help="Sector disfavored in current regime")
                _ec4.metric("% in headwind sectors", f"{_headwind_weight:.0f}%",
                            help="Combined weight of positions facing macro headwinds")

                # Rate sensitivity bar chart
                _expo_sorted = expo_df.sort_values("Rate Sensitivity")
                _bar_colors = [
                    "#00C851" if a == "Tailwind ↑" else "#ff4444" if a == "Headwind ↓" else "#888888"
                    for a in _expo_sorted["Macro Alignment"]
                ]
                _labels = [
                    f"{row['Ticker']} ({row['Weight (%)']:.0f}%)"
                    for _, row in _expo_sorted.iterrows()
                ]
                rs_fig = go.Figure(go.Bar(
                    x=_expo_sorted["Rate Sensitivity"],
                    y=_labels,
                    orientation="h",
                    marker_color=_bar_colors,
                    text=[f"{v:+.2f}" for v in _expo_sorted["Rate Sensitivity"]],
                    textposition="outside",
                ))
                rs_fig.add_vline(x=0, line_color="white", line_dash="dot", line_width=1)
                rs_fig.update_layout(
                    title="Rate Sensitivity by Position (right = rate beneficiary)",
                    template="plotly_dark", height=max(280, 35 * len(_expo_sorted)),
                    xaxis_title="Rate Sensitivity Score",
                    margin=dict(l=0, r=60, t=40, b=0),
                )
                st.plotly_chart(rs_fig, use_container_width=True)
                st.caption(
                    "🟢 Green = sector benefits from current macro regime  |  "
                    "⬜ Gray = neutral  |  "
                    "🔴 Red = sector faces headwind in current regime"
                )

                # Styled table
                def _align_col(val):
                    if "Tailwind" in str(val):  return "color:#00C851;font-weight:bold"
                    if "Headwind" in str(val):  return "color:#ff4444"
                    return "color:#888888"

                def _rate_col(val):
                    if isinstance(val, float):
                        if val >= 0.3:  return "color:#00C851"
                        if val <= -0.4: return "color:#ff4444"
                    return ""

                _disp_cols = ["Icon", "Ticker", "Sector", "Weight (%)", "Rate Sensitivity", "Macro Alignment"]
                _styled_expo = (
                    expo_df[_disp_cols].style
                    .map(_align_col, subset=["Macro Alignment"])
                    .map(_rate_col,  subset=["Rate Sensitivity"])
                    .format({"Weight (%)": "{:.1f}%", "Rate Sensitivity": "{:+.2f}"})
                )
                st.dataframe(_styled_expo, use_container_width=True)

                # Actionable callout
                if _headwind_weight > 30:
                    _heads = expo_df[expo_df["Macro Alignment"] == "Headwind ↓"]["Sector"].unique().tolist()
                    st.warning(
                        f"⚠️ **{_headwind_weight:.0f}% of your portfolio is in macro headwind sectors** "
                        f"({', '.join(_heads)}) given the *{regime['label']}* environment. "
                        f"Best practice would recommend trimming these and rotating to "
                        f"{', '.join(_fav['overweight'][:2]) if _fav['overweight'] else 'defensive sectors'}."
                    )
                elif _nt > _nh:
                    st.success(
                        f"✅ **Your portfolio is well-positioned for {regime['label']}** — "
                        f"{_nt} of {len(expo_df)} positions are in macro-favored sectors."
                    )

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 6 — SECTOR ROTATION HEATMAP
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_heat:
        st.caption(
            "Multi-period return heatmap for every sector ETF. "
            "Green = outperforming, red = underperforming. "
            "Your portfolio's sector exposure is shown in the last column — "
            "use this to spot where money is rotating and whether you're positioned correctly."
        )

        if st.button("🔥 Load Sector Heatmap", key="_heat_btn", type="primary"):
            st.session_state["_sector_rets"] = _fetch_sector_returns()

        _sr_df = st.session_state.get("_sector_rets")

        if _sr_df is not None and not _sr_df.empty:
            # Reverse map: ETF → sector name(s) — use first match
            _etf_to_sector = {}
            for sec, etf in SECTOR_ETF.items():
                if etf not in _etf_to_sector:
                    _etf_to_sector[etf] = sec

            # Portfolio sector exposure — convert DataFrame → dict {sector: weight%}
            _sec_exp_df = sector_exposure(port_df)
            _sec_exp = (
                dict(zip(_sec_exp_df["Sector"], _sec_exp_df["Pct"]))
                if not _sec_exp_df.empty else {}
            )

            # Build display table
            _periods = ["1W", "1M", "3M", "6M"]
            _heat_rows = []
            for _, row in _sr_df.iterrows():
                etf     = row["ETF"]
                sector  = _etf_to_sector.get(etf, etf)
                exp_pct = _sec_exp.get(sector, 0.0)
                _heat_rows.append({
                    "Sector":     sector,
                    "ETF":        etf,
                    "1W %":       row.get("1W"),
                    "1M %":       row.get("1M"),
                    "3M %":       row.get("3M"),
                    "6M %":       row.get("6M"),
                    "My Exposure": round(exp_pct, 1),
                })
            _heat_df = pd.DataFrame(_heat_rows).sort_values("3M %", ascending=False).reset_index(drop=True)

            # KPI strip
            _best_sec  = _heat_df.loc[_heat_df["3M %"].idxmax()]
            _worst_sec = _heat_df.loc[_heat_df["3M %"].idxmin()]
            _my_sectors = _heat_df[_heat_df["My Exposure"] > 0].sort_values("My Exposure", ascending=False)
            _top_exp    = _my_sectors.iloc[0] if not _my_sectors.empty else None

            _hk1, _hk2, _hk3, _hk4 = st.columns(4)
            _hk1.metric("Best Sector (3M)",  _best_sec["Sector"],  f"{_best_sec['3M %']:+.1f}%",  delta_color="normal")
            _hk2.metric("Worst Sector (3M)", _worst_sec["Sector"], f"{_worst_sec['3M %']:+.1f}%", delta_color="inverse")
            _hk3.metric(
                "Your Top Exposure",
                _top_exp["Sector"] if _top_exp is not None else "—",
                f"{_top_exp['My Exposure']:.1f}% weight · {_top_exp['3M %']:+.1f}% (3M)" if _top_exp is not None else "",
                delta_color="off",
            )
            _positive_3m = int((_heat_df["3M %"] > 0).sum())
            _hk4.metric("Sectors in Green (3M)", f"{_positive_3m}/{len(_heat_df)}")

            # ── Heatmap ───────────────────────────────────────────────────────
            _z      = _heat_df[["1W %", "1M %", "3M %", "6M %"]].values.tolist()
            _y_lbls = [
                f"◀ {r['Sector']}  {r['My Exposure']:.0f}%" if r["My Exposure"] > 0
                else f"   {r['Sector']}"
                for _, r in _heat_df.iterrows()
            ]
            _text   = [
                [f"{v:+.1f}%" if v is not None else "—" for v in row]
                for row in _z
            ]

            _hmap = go.Figure(go.Heatmap(
                z=_z,
                x=["1 Week", "1 Month", "3 Month", "6 Month"],
                y=_y_lbls,
                text=_text,
                texttemplate="%{text}",
                textfont=dict(size=12, color="white"),
                colorscale=[
                    [0.0,  "#8b0000"],
                    [0.3,  "#cc3333"],
                    [0.45, "#996633"],
                    [0.5,  "#444444"],
                    [0.55, "#336633"],
                    [0.7,  "#00aa44"],
                    [1.0,  "#006622"],
                ],
                zmid=0,
                colorbar=dict(
                    title="Return %",
                    ticksuffix="%",
                    thickness=14,
                    len=0.8,
                ),
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Period: %{x}<br>"
                    "Return: %{text}<extra></extra>"
                ),
            ))
            # Gold highlight band for every row where the user holds this sector
            for _hi, (_, _hr) in enumerate(_heat_df.iterrows()):
                if _hr["My Exposure"] > 0:
                    _hmap.add_shape(
                        type="rect",
                        xref="paper", x0=0, x1=1,
                        yref="y", y0=_hi - 0.5, y1=_hi + 0.5,
                        fillcolor="rgba(255,200,0,0.07)",
                        line=dict(color="rgba(255,200,0,0.45)", width=1.5),
                        layer="below",
                    )
            _hmap.update_layout(
                template="plotly_dark",
                height=max(280, len(_heat_df) * 42 + 80),
                margin=dict(l=0, r=0, t=20, b=0),
                xaxis=dict(side="top"),
                yaxis=dict(autorange="reversed"),
                plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
            )
            st.plotly_chart(_hmap, use_container_width=True)
            st.caption("◀ = sector you hold (weight % shown) · gold border = your position · sorted by 3M return")

            # ── Exposure vs momentum table ────────────────────────────────────
            st.markdown("#### Your Exposure vs Sector Momentum")
            _exp_tbl = _heat_df[_heat_df["My Exposure"] > 0].copy()
            if not _exp_tbl.empty:
                _exp_tbl["Alignment"] = _exp_tbl.apply(
                    lambda r: (
                        "✅ In momentum"   if r["My Exposure"] >= 5 and r["3M %"] > 2 else
                        "⚠️ Heavy in laggard" if r["My Exposure"] >= 10 and r["3M %"] < 0 else
                        "🟡 Monitor"       if r["3M %"] < 0 else
                        "🟢 Aligned"
                    ), axis=1,
                )
                st.dataframe(
                    _exp_tbl[["Sector", "ETF", "1W %", "1M %", "3M %", "6M %", "My Exposure", "Alignment"]],
                    use_container_width=True, hide_index=True,
                )
            else:
                st.info("None of your holdings map to a tracked sector ETF.")

            st.caption(
                "Sector ETFs: SOXX (Semis) · IGV (AI/Cloud/Tech) · XLV (Healthcare) · "
                "XLE (Energy) · XLF (Financials) · ITA (Defense) · XLY (Consumer) · "
                "CIBR (Cybersecurity) · ICLN (Clean Energy) · DRIV (EV).  "
                "Data cached 1 hour."
            )
        elif _sr_df is None:
            st.info("Click **Load Sector Heatmap** to fetch live sector ETF performance.")

    # TAB 7 — RANKINGS
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_rank:
        st.caption(
            "Scan ~80 tickers across 12 sectors and rank each holding by momentum score. "
            "Shows whether you're holding the best names in each sector or just familiar ones. "
            "Institutional uses universe-relative ranking to identify rotation candidates."
        )

        if st.button("🔍 Scan full universe & rank my holdings", key="_rank_scan_btn"):
            with st.spinner("Scanning universe (~80 tickers)…"):
                try:
                    _full_scan = scan_sectors(list(SECTOR_UNIVERSE.keys()))
                    st.session_state["_rank_scan_df"] = _full_scan
                except Exception as _e:
                    st.error(f"Scan failed: {_e}")
                    st.session_state["_rank_scan_df"] = pd.DataFrame()

        if st.session_state.get("_rank_scan_df") is not None and not st.session_state["_rank_scan_df"].empty:
            _scan_df  = st.session_state["_rank_scan_df"]
            _rank_df  = rank_holdings_in_universe(port_df, _scan_df)

            if _rank_df.empty:
                st.info("Could not match any holdings to the scanned universe.")
            else:
                _total     = int(_rank_df["of"].iloc[0])
                _in_univ   = _rank_df["Universe Rank"].notna().sum()
                _top_q     = int((_rank_df["Percentile"] >= 75).sum())
                _bot_q     = int((_rank_df["Percentile"] <= 25).sum())

                _rk1, _rk2, _rk3, _rk4 = st.columns(4)
                _rk1.metric("Universe size",    _total,    help="Tickers scanned")
                _rk2.metric("Holdings ranked",  _in_univ,  help="Holdings found in universe")
                _rk3.metric("Top quartile",     _top_q,    help="Percentile ≥ 75")
                _rk4.metric("Bottom quartile",  _bot_q,    help="Percentile ≤ 25 — rotation candidates")

                # Percentile bar chart
                _rk_valid = _rank_df.dropna(subset=["Percentile"]).sort_values("Percentile", ascending=False)
                _pct_colors = [
                    tier_label(p)[1] for p in _rk_valid["Percentile"]
                ]
                pct_fig = go.Figure(go.Bar(
                    x=_rk_valid["Ticker"],
                    y=_rk_valid["Percentile"],
                    marker_color=_pct_colors,
                    text=[f"#{int(r)}" for r in _rk_valid["Universe Rank"]],
                    textposition="outside",
                    customdata=list(zip(
                        _rk_valid["Universe Rank"],
                        _rk_valid["of"],
                        _rk_valid["Scanner Score"],
                        _rk_valid["Tier"],
                    )),
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        "Rank: #%{customdata[0]} of %{customdata[1]}<br>"
                        "Percentile: %{y:.0f}th<br>"
                        "Scanner Score: %{customdata[2]:.0f}/100<br>"
                        "Tier: %{customdata[3]}"
                        "<extra></extra>"
                    ),
                ))
                pct_fig.add_hline(y=75, line_dash="dash", line_color="#4CAF50",
                                  annotation_text="Top quartile", annotation_position="right")
                pct_fig.add_hline(y=25, line_dash="dash", line_color="#ff8800",
                                  annotation_text="Bottom quartile", annotation_position="right")
                pct_fig.update_layout(
                    title=f"Holdings — Universe Percentile Rank (out of {_total} tickers)",
                    template="plotly_dark", height=300,
                    yaxis_title="Percentile", yaxis_range=[0, 110],
                    margin=dict(l=0, r=60, t=40, b=0),
                )
                st.plotly_chart(pct_fig, use_container_width=True)

                # Styled ranking table
                def _tier_col(val):
                    s = str(val)
                    if "Top Decile"     in s: return "color:#00C851;font-weight:bold"
                    if "Top Quartile"   in s: return "color:#4CAF50"
                    if "Bottom Decile"  in s: return "color:#ff4444;font-weight:bold"
                    if "Bottom Quartile"in s: return "color:#ff8800"
                    if "Below Median"   in s: return "color:#ffbb33"
                    return "color:#aaaaaa"

                def _pct_col(val):
                    if isinstance(val, float):
                        if val >= 75: return "color:#4CAF50;font-weight:bold"
                        if val <= 25: return "color:#ff8800"
                    return ""

                _disp = _rank_df[[
                    "Ticker", "Sector", "Universe Rank", "of", "Percentile",
                    "Tier", "Scanner Score", "Composite Score", "Sector Rank",
                ]].copy()
                _styled_rank = (
                    _disp.style
                    .map(_tier_col, subset=["Tier"])
                    .map(_pct_col,  subset=["Percentile"])
                    .format({
                        "Percentile":      "{:.0f}th",
                        "Scanner Score":   "{:.0f}",
                        "Composite Score": "{:.0f}",
                    }, na_rep="—")
                )
                st.dataframe(_styled_rank, use_container_width=True)
                st.caption(
                    "**Scanner Score** = momentum-only (consistent across all 80 tickers).  "
                    "**Composite Score** = technical + fundamental + sentiment (your holdings only).  "
                    "**Sector Rank** = position within its scanner sector grouping."
                )

                # Rotation candidates with alternatives
                _bot_rows = _rank_df[_rank_df["Percentile"].notna() & (_rank_df["Percentile"] <= 25)]
                if not _bot_rows.empty:
                    st.markdown("### 🔄 Rotation Candidates")
                    st.caption(
                        "These holdings rank in the bottom quartile of the universe. "
                        "Best practice would review for rotation into higher-ranked names in the same sector."
                    )
                    for _, brow in _bot_rows.iterrows():
                        alts = sector_alternatives(
                            brow["Ticker"], str(brow["_scanner_sector"]), _scan_df, n=3
                        )
                        with st.container(border=True):
                            _bc1, _bc2 = st.columns([3, 1])
                            with _bc1:
                                st.markdown(
                                    f"🔴 **{brow['Ticker']}** — ranked "
                                    f"#{int(brow['Universe Rank'])} of {int(brow['of'])} "
                                    f"({brow['Percentile']:.0f}th percentile · {brow['Tier']})"
                                )
                                st.caption(
                                    f"Scanner score: {brow['Scanner Score']:.0f}/100 · "
                                    f"Sector rank: {brow['Sector Rank']} · "
                                    f"Composite: {brow['Composite Score']:.0f}/100"
                                )
                            with _bc2:
                                st.metric("Sector rank", brow["Sector Rank"])
                            if alts:
                                st.markdown("**Higher-ranked alternatives in same sector:**")
                                _ac = st.columns(len(alts))
                                for _col, alt in zip(_ac, alts):
                                    au = universe.get(alt["ticker"]) if (universe := {r["Ticker"]: r for _, r in _scan_df.iterrows()}) else None
                                    _alt_rank = int(au["Rank"]) if au is not None else "—"
                                    _alt_pct  = round((int(brow["of"]) - int(_alt_rank) + 1) / int(brow["of"]) * 100, 0) if isinstance(_alt_rank, int) else "—"
                                    _col.markdown(
                                        f"**{alt['ticker']}**  \n"
                                        f"Score: {alt['score']:.0f}/100  \n"
                                        f"Rank: #{_alt_rank}  \n"
                                        f"Pct: {_alt_pct}th  \n"
                                        f"{alt['signal']}"
                                    )

                # Top performer callout
                _top_rows = _rank_df[_rank_df["Percentile"].notna() & (_rank_df["Percentile"] >= 90)]
                if not _top_rows.empty:
                    _best_r = _top_rows.loc[_top_rows["Percentile"].idxmax()]
                    st.success(
                        f"✅ **{_best_r['Ticker']}** ranks #{int(_best_r['Universe Rank'])} of {int(_best_r['of'])} "
                        f"({_best_r['Percentile']:.0f}th percentile — {_best_r['Tier']}) — "
                        f"top-decile momentum score across the full universe. High-conviction hold."
                    )


    # TAB 7 — AI MONITORING BRIEF
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_brief:
        import os, re as _re

        st.markdown("### 🤖 AI Monitoring Brief")
        st.caption(
            "Generates an institutional-style morning brief from your live portfolio data. "
            "Choose any supported AI provider — key is read from Streamlit secrets or entered below."
        )

        # ── Provider / model config ───────────────────────────────────────────
        _AI_PROVIDERS = {
            "Claude (Anthropic)": {
                "models": {
                    "claude-haiku-4-5-20251001": "Haiku 4.5 — fast & cheap",
                    "claude-sonnet-4-6":         "Sonnet 4.6 — more capable",
                },
                "secrets_path": ("anthropic", "api_key"),
                "env_var":      "ANTHROPIC_API_KEY",
                "key_hint":     "sk-ant-...",
                "key_url":      "https://console.anthropic.com",
            },
            "OpenAI": {
                "models": {
                    "gpt-4o-mini": "GPT-4o mini — fast & cheap",
                    "gpt-4o":      "GPT-4o — more capable",
                },
                "secrets_path": ("openai", "api_key"),
                "env_var":      "OPENAI_API_KEY",
                "key_hint":     "sk-...",
                "key_url":      "https://platform.openai.com/api-keys",
            },
            "Gemini (Google)": {
                "models": {
                    "gemini-2.0-flash":       "Gemini 2.0 Flash — fast (free tier)",
                    "gemini-2.5-flash-preview-04-17": "Gemini 2.5 Flash — most capable",
                },
                "secrets_path": ("google", "api_key"),
                "env_var":      "GOOGLE_API_KEY",
                "key_hint":     "AIza...",
                "key_url":      "https://aistudio.google.com/app/apikey",
            },
            "Groq (Free tier)": {
                "models": {
                    "llama-3.1-8b-instant": "Llama 3.1 8B — fastest",
                    "mixtral-8x7b-32768":   "Mixtral 8x7B — smarter",
                },
                "secrets_path": ("groq", "api_key"),
                "env_var":      "GROQ_API_KEY",
                "key_hint":     "gsk_...",
                "key_url":      "https://console.groq.com/keys",
            },
        }

        # ── Provider + model selectors ────────────────────────────────────────
        _bp1, _bp2 = st.columns([3, 3])
        with _bp1:
            _sel_provider = st.selectbox(
                "AI Provider", list(_AI_PROVIDERS.keys()), key="_brief_provider"
            )
        _prov_cfg   = _AI_PROVIDERS[_sel_provider]
        _model_opts = _prov_cfg["models"]
        with _bp2:
            _sel_model = st.selectbox(
                "Model",
                list(_model_opts.keys()),
                format_func=lambda m: _model_opts[m],
                key="_brief_model",
            )

        # ── API key resolution: secrets → env → manual entry ─────────────────
        _sec_section, _sec_field = _prov_cfg["secrets_path"]
        _resolved_key = (
            st.secrets.get(_sec_section, {}).get(_sec_field)
            or os.environ.get(_prov_cfg["env_var"])
            or ""
        )
        _ss_key_store = f"_brief_key_{_sel_provider}"
        if _resolved_key:
            st.session_state[_ss_key_store] = _resolved_key
            st.caption(f"🔑 API key loaded from secrets / environment.")
        else:
            _manual_key = st.text_input(
                f"{_sel_provider} API key",
                value=st.session_state.get(_ss_key_store, ""),
                type="password",
                placeholder=_prov_cfg["key_hint"],
                help=f"Get a key at {_prov_cfg['key_url']}",
                key=f"_brief_key_input_{_sel_provider}",
            )
            if _manual_key:
                st.session_state[_ss_key_store] = _manual_key
        _active_key = st.session_state.get(_ss_key_store, "")

        # ── Unified AI call ───────────────────────────────────────────────────
        def _call_ai_brief(provider, model, api_key, sys_prompt, usr_prompt):
            if provider == "Claude (Anthropic)":
                import anthropic as _anth
                c = _anth.Anthropic(api_key=api_key)
                r = c.messages.create(
                    model=model, max_tokens=700, system=sys_prompt,
                    messages=[{"role": "user", "content": usr_prompt}],
                )
                return r.content[0].text
            elif provider == "OpenAI":
                from openai import OpenAI as _OAI
                c = _OAI(api_key=api_key)
                r = c.chat.completions.create(
                    model=model, max_tokens=700,
                    messages=[{"role": "system", "content": sys_prompt},
                               {"role": "user",   "content": usr_prompt}],
                )
                return r.choices[0].message.content
            elif provider == "Gemini (Google)":
                import google.generativeai as _genai
                _genai.configure(api_key=api_key)
                _gm = _genai.GenerativeModel(model, system_instruction=sys_prompt)
                return _gm.generate_content(usr_prompt).text
            elif provider == "Groq (Free tier)":
                from openai import OpenAI as _OAI
                c = _OAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
                r = c.chat.completions.create(
                    model=model, max_tokens=700,
                    messages=[{"role": "system", "content": sys_prompt},
                               {"role": "user",   "content": usr_prompt}],
                )
                return r.choices[0].message.content
            raise ValueError(f"Unknown provider: {provider}")

        # ── Brief cache key: invalidate when provider or model changes ────────
        _brief_cache_key = f"_ai_brief__{_sel_provider}__{_sel_model}"
        _brief_cached    = st.session_state.get(_brief_cache_key)
        _brief_ts        = st.session_state.get(f"{_brief_cache_key}__ts", "")

        # ── Generate / refresh controls ───────────────────────────────────────
        _bctl1, _bctl2 = st.columns([6, 2])
        with _bctl1:
            if _brief_ts:
                st.caption(f"Generated: {_brief_ts}  ·  {_sel_provider} / {_sel_model}")
        with _bctl2:
            _gen_btn = st.button(
                "🔄 Refresh Brief" if _brief_cached else "✨ Generate Brief",
                key="_gen_brief_btn",
                use_container_width=True,
                type="primary",
                disabled=not _active_key,
            )

        if not _active_key:
            st.info(
                f"Enter your **{_sel_provider}** API key above to generate the brief.  "
                f"Get one at {_prov_cfg['key_url']}",
                icon="🔑",
            )

        if _gen_btn and _active_key:
            # Build portfolio context (same for all providers)
            _ctx_lines = [
                f"Date: {datetime.now().strftime('%A, %B %d, %Y %H:%M ET')}",
                f"Portfolio Value: ${total_val:,.0f}  |  Total P&L: ${total_pnl:,.0f} ({total_pnl_pct:+.1f}%)",
                f"Avg Conviction: {avg_score:.0f}/100  |  Diversification: {div_score:.0f}/100 ({_div_label})",
                "", "## HOLDINGS",
            ]
            for _, _pr in port_df.iterrows():
                _gap   = _pr.get("Gap to Stop (%)")
                _gap_s = f"{_gap:.1f}%" if isinstance(_gap, (int, float)) and not pd.isna(_gap) else "—"
                _ctx_lines.append(
                    f"  {_pr['Ticker']:6s} | Weight {_pr['Weight (%)']:.1f}% | "
                    f"P&L {_pr['P&L (%)']:+.1f}% (${_pr['P&L ($)']:,.0f}) | "
                    f"Signal: {_pr['Signal']} | Score: {_pr['Score']:.0f}/100 | "
                    f"Gap to Stop: {_gap_s}"
                )
            _ctx_lines += ["", "## ACTIVE ALERTS"]
            if alert_list:
                for _al in alert_list[:12]:
                    _ctx_lines.append(f"  [{_al['level'].upper()}] {_al['msg'].replace('*','').replace('_','').replace('`','')}")
            else:
                _ctx_lines.append("  No active alerts.")
            _live_idx = fetch_market_indices()
            _ctx_lines += ["", "## MARKET INDICES"]
            for _ix in _live_idx:
                _sign = "+" if _ix["change_pct"] >= 0 else ""
                _ctx_lines.append(f"  {_ix['short']:8s} {_ix['price']:,.2f}  ({_sign}{_ix['change_pct']:.2f}%)")
            _news_ctx = st.session_state.get("_sidebar_news") or []
            if _news_ctx:
                _ctx_lines += ["", "## TOP NEWS"]
                for _ni in _news_ctx[:8]:
                    _ctx_lines.append(f"  [{_ni['ticker']}] {_ni['label']:8s} | {_ni['title'][:100]}")

            _sys_prompt = (
                "You are a senior portfolio manager at a top-tier institutional investment firm. "
                "Write a concise, professional morning monitoring brief. "
                "Be specific: name tickers, cite numbers. Short structured sections. "
                "Tone: confident, analytical, no fluff. Maximum 450 words. "
                "End with 3 concrete action items ranked by urgency."
            )
            _usr_prompt = (
                f"Generate a morning monitoring brief for this portfolio:\n\n"
                f"{chr(10).join(_ctx_lines)}\n\n"
                "Structure:\n"
                "**EXECUTIVE SUMMARY** (2-3 sentences)\n"
                "**MARKET CONTEXT** (1-2 sentences)\n"
                "**PORTFOLIO HIGHLIGHTS** (top movers, key risks)\n"
                "**RISK FLAGS** (alerts — what needs attention)\n"
                "**KEY NEWS CATALYSTS** (material news affecting holdings)\n"
                "**ACTION ITEMS** (3 prioritised, specific)\n"
            )

            with st.spinner(f"Generating brief with {_sel_provider}…"):
                try:
                    _brief_text = _call_ai_brief(
                        _sel_provider, _sel_model, _active_key, _sys_prompt, _usr_prompt
                    )
                    _ts_now = datetime.now().strftime("%b %d %Y %H:%M ET")
                    st.session_state[_brief_cache_key]           = _brief_text
                    st.session_state[f"{_brief_cache_key}__ts"]  = _ts_now
                    _brief_cached = _brief_text
                    _brief_ts     = _ts_now
                except Exception as _e:
                    st.error(f"Brief generation failed: {_e}")
                    _brief_cached = None

        if _brief_cached:
            st.markdown(
                f"<div style='background:#0d1117;border:1px solid #1f2937;border-radius:10px;"
                f"padding:20px 24px;margin-top:6px;line-height:1.65'>"
                f"{_brief_cached.replace(chr(10), '<br>')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.caption(
                f"Provider: {_sel_provider} · Model: {_sel_model} · Generated {_brief_ts} · "
                "Based on live data at generation time — refresh for latest."
            )


# ═════════════════════════════════════════════════════════════════════════════
# PAGE 2 — MARKET SCANNER
# ═════════════════════════════════════════════════════════════════════════════
elif page == "🔍 Market Scanner":
    # Show cached news from last portfolio/analysis visit
    _fill_news_slot(_news_slot, st.session_state.get("_sidebar_news", []))

    st.title("🔍 Market Scanner")
    st.caption("Scans 60+ stocks across 12 sectors to surface trending opportunities.")
    st.info(
        "**How to read this:** The scanner uses a **Momentum Score** (RSI + Trend + 1M/3M price momentum). "
        "It is a fast filter, not a buy signal. A high momentum score means the stock is moving — "
        "**not** that fundamentals or sentiment support the move.  \n"
        "For any ticker that catches your eye, run a full analysis on the **Analysis** page, "
        "which adds Fundamental (40%) and Sentiment (15%) data to form a composite score. "
        "A stock can score 85 on momentum and 52 composite — both numbers are correct; they answer different questions.",
        icon="ℹ️",
    )

    sc1, sc2 = st.columns([3, 1])
    with sc1:
        selected_sectors = st.multiselect(
            "Sectors to scan",
            options=list(SECTOR_UNIVERSE.keys()),
            default=list(SECTOR_UNIVERSE.keys()),
        )
    with sc2:
        min_score = st.slider("Min score", 0, 100, 50)

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        run_scan = st.button("🔍 Scan Now", type="primary", use_container_width=True)
    with col_info:
        total_tickers = sum(len(v) for k, v in SECTOR_UNIVERSE.items() if k in selected_sectors)
        st.caption(f"Will scan **{total_tickers} stocks** across {len(selected_sectors)} sectors (~15–30 sec)")

    if run_scan:
        with st.spinner(f"Scanning {total_tickers} stocks across {len(selected_sectors)} sectors…"):
            results_df = scan_sectors(selected_sectors, period="6mo")
        if not results_df.empty:
            st.session_state.scanner_results = results_df
            st.success(f"Scan complete — {len(results_df)} stocks analyzed.")
        else:
            st.error("Scan returned no results. Check connection.")

    if st.session_state.scanner_results is not None:
        df = st.session_state.scanner_results
        filtered = df[df["Score"] >= min_score].copy()

        # Compute cache key early so Top 5 cards can show earnings badges
        # when Signal Evidence has already been loaded
        _top10 = filtered.head(10)
        _ev_cache_key = f"_scanner_ev_{','.join(_top10['Ticker'].tolist())}"
        _ev_bundle_map = st.session_state.get(_ev_cache_key, {})

        # Top 5 picks callout
        top5 = filtered.head(5)
        # Composite cache populated by the Daily Briefing pre-fetch loop — if
        # available, lets each top pick render an explicit reconciliation badge
        # (e.g. "❌ Skip — Composite Sell contradicts Momentum 90") without
        # making the user click into Analysis first. Scanner picks are by
        # definition non-held, so we don't need port_df — composites cache is
        # the only source we consult here.
        _sc_composites = st.session_state.get("_grow_composites", {}) or {}
        if not top5.empty:
            st.subheader("🏆 Top Picks")
            cols = st.columns(min(5, len(top5)))
            for i, (_, row) in enumerate(top5.iterrows()):
                with cols[i]:
                    score_color = "#00C851" if row["Score"] >= 70 else "#ffbb33"
                    # Earnings badge — shown when Signal Evidence has been loaded
                    _t5_earn_badge = ""
                    _t5_bndl = _ev_bundle_map.get(row["Ticker"], {})
                    _t5_ed_val = None
                    if _t5_bndl.get("earnings"):
                        try:
                            _t5_ed_val = (date.fromisoformat(_t5_bndl["earnings"][:10]) - _TODAY_ET).days
                            if 0 <= _t5_ed_val <= 7:
                                _t5_earn_badge = (
                                    f"<br><small style='color:#ef4444;font-weight:700'>"
                                    f"⚠ Earnings in {_t5_ed_val}d</small>"
                                )
                            elif 0 <= _t5_ed_val <= 14:
                                _t5_earn_badge = (
                                    f"<br><small style='color:#f59e0b'>"
                                    f"📅 Earnings in {_t5_ed_val}d</small>"
                                )
                        except Exception:
                            pass

                    # Reconciliation badge — pulls composite from Daily Briefing's
                    # pre-fetch cache; renders the verdict label inline so the
                    # Top Picks view never disagrees with Analysis on the same name.
                    _sc_sig, _sc_scr = lookup_composite(row["Ticker"], None, _sc_composites)
                    _sc_recon = reconcile_signals(
                        ticker=row["Ticker"],
                        momentum_score=float(row["Score"]),
                        momentum_signal=str(row.get("Signal", "")),
                        composite_score=_sc_scr,
                        composite_signal=_sc_sig,
                        earnings_days=_t5_ed_val,
                    )
                    _sc_badge = (
                        f"<br><small style='color:{_sc_recon['color']};font-weight:700'>"
                        f"{_sc_recon['label']}</small>"
                    )

                    st.markdown(
                        f"<div style='padding:10px;border-radius:8px;"
                        f"border:1px solid {score_color};text-align:center'>"
                        f"<b style='font-size:1.1em'>{row['Ticker']}</b><br>"
                        f"<span style='color:{score_color};font-size:1.4em;font-weight:bold'>"
                        f"{row['Score']}</span>/100<br>"
                        f"<small>{row['Sector']}</small><br>"
                        f"<small>{row['Signal']}</small><br>"
                        f"<small>1M: {row['1M Momentum']:+.1f}%</small>"
                        f"{_t5_earn_badge}"
                        f"{_sc_badge}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        f"▶ Analyze",
                        key=f"_sc_top_analyze_{row['Ticker']}",
                        use_container_width=True,
                    ):
                        st.session_state["_pending_page"]    = "📈 Analysis"
                        st.session_state["_analysis_ticker"] = row["Ticker"]
                        st.rerun()

            # Imminent earnings warning — only visible once Signal Evidence is loaded
            if _ev_bundle_map:
                _imm = []
                for _, _r5 in top5.iterrows():
                    _t5b = _ev_bundle_map.get(_r5["Ticker"], {})
                    if _t5b.get("earnings"):
                        try:
                            _d5 = (date.fromisoformat(_t5b["earnings"][:10]) - _TODAY_ET).days
                            if 0 <= _d5 <= 7:
                                _imm.append(f"**{_r5['Ticker']}** (in {_d5}d)")
                        except Exception:
                            pass
                if _imm:
                    st.warning(
                        f"⚠ **Earnings risk in top picks:** {', '.join(_imm)}. "
                        "High momentum may be priced in ahead of the report. "
                        "Avoid initiating a new position within 3–5 days of earnings — "
                        "wait for post-report price confirmation instead."
                    )

        # ── Signal Evidence — on-demand for top 10 ──────────────────────────
        # _top10 and _ev_cache_key already computed above

        _ev_btn_col, _ev_hint_col = st.columns([1, 3])
        with _ev_btn_col:
            _load_ev = st.button(
                "📊 Load Signal Evidence",
                key="_load_ev_btn",
                use_container_width=True,
            )
        with _ev_hint_col:
            st.caption(
                "Fetches analyst consensus, price targets, revisions, earnings date "
                "and news sentiment for the top 10 picks. Runs once, cached until next scan."
            )

        if _load_ev:
            _ev_bundle_map: dict = {}
            with st.spinner("Loading evidence for top 10 picks…"):
                for _ev_t in _top10["Ticker"].tolist():
                    try:
                        _ev_bundle_map[_ev_t] = fetch_ticker_bundle(_ev_t, period="1mo")
                    except Exception:
                        _ev_bundle_map[_ev_t] = {}
            st.session_state[_ev_cache_key] = _ev_bundle_map

        if _ev_cache_key in st.session_state:
            _ev_bundle_map = st.session_state[_ev_cache_key]

            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer as _EV_VADER
            _ev_va = _EV_VADER()

            _ev_composites = st.session_state.get("_grow_composites", {})

            _ev_rows: list[dict] = []
            for _, _ev_srow in _top10.iterrows():
                _ev_t     = _ev_srow["Ticker"]
                _ev_score = int(_ev_srow["Score"])
                _ev_price = float(_ev_srow.get("Price") or 0)

                # ── Scanner score component breakdown ──────────────────────
                _ev_rsi    = float(_ev_srow.get("RSI")          or 0)
                _ev_m1m    = float(_ev_srow.get("1M Momentum")  or 0)
                _ev_m3m    = float(_ev_srow.get("3M Momentum")  or 0)
                _ev_trend  = str(_ev_srow.get("Trend")          or "")

                # Reproduce _quick_score() component points for transparency
                _ev_rsi_pts = (30 if 40 <= _ev_rsi <= 65 else
                               22 if _ev_rsi < 40 else
                               12 if _ev_rsi < 75 else 2)
                _ev_trend_pts = (35 if "Strong Uptrend" in _ev_trend else
                                 20 if "Uptrend" in _ev_trend else
                                 10 if "Mixed" in _ev_trend else 0)
                _ev_m1m_pts = (20 if _ev_m1m > 8 else 14 if _ev_m1m > 3 else
                               7  if _ev_m1m > 0 else 2  if _ev_m1m > -5 else 0)
                _ev_m3m_pts = (15 if _ev_m3m > 15 else 10 if _ev_m3m > 5 else
                               5  if _ev_m3m > 0  else 0)

                # Composite score from pre-fetched data (may be None)
                _ev_comp_data  = _ev_composites.get(_ev_t, {})
                _ev_comp_score = float(_ev_comp_data.get("total") or 0) if _ev_comp_data else None
                _ev_comp_label = str((_ev_comp_data.get("rec") or {}).get("label", "")) if _ev_comp_data else ""
                _ev_tech_score = float(_ev_comp_data.get("t_total") or 0) if _ev_comp_data else None
                _ev_fund_score = float(_ev_comp_data.get("f_total") or 0) if _ev_comp_data else None
                _ev_sent_score = float(_ev_comp_data.get("sentiment_score") or 0) if _ev_comp_data else None
                _bndl     = _ev_bundle_map.get(_ev_t, {})
                _ev_info  = _bndl.get("info", {})
                _ev_revs  = _bndl.get("revisions", {})
                _ev_news  = _bndl.get("news", [])
                _ev_earn  = _bndl.get("earnings")

                # Analyst consensus
                _rec_mean   = _ev_info.get("recommendationMean")
                _n_analysts = int(_ev_info.get("numberOfAnalystOpinions") or 0)
                _ev_target  = _ev_info.get("targetMeanPrice")
                _ev_t_high  = _ev_info.get("targetHighPrice")
                _ev_t_low   = _ev_info.get("targetLowPrice")
                _ev_t_med   = _ev_info.get("targetMedianPrice")
                _ev_upside  = (round((_ev_target / _ev_price - 1) * 100, 1)
                               if _ev_target and _ev_price > 0 else None)

                if _rec_mean is None:       _rec_label = "No Coverage"
                elif _rec_mean <= 1.5:      _rec_label = "Strong Buy"
                elif _rec_mean <= 2.5:      _rec_label = "Buy"
                elif _rec_mean <= 3.5:      _rec_label = "Hold"
                elif _rec_mean <= 4.5:      _rec_label = "Sell"
                else:                       _rec_label = "Strong Sell"

                # Revisions
                _ev_ups   = int(_ev_revs.get("upgrades_90d")  or 0)
                _ev_downs = int(_ev_revs.get("downgrades_90d") or 0)
                _ev_maint = int(_ev_revs.get("maintained_90d") or 0)
                _ev_net   = _ev_revs.get("net")

                # Earnings countdown
                _ev_earn_days = None
                if _ev_earn:
                    try:
                        _ev_earn_days = (date.fromisoformat(_ev_earn[:10]) - _TODAY_ET).days
                    except Exception:
                        pass

                # News sentiment (title-based VADER)
                _ev_compounds = []
                for _ni in (_ev_news or [])[:6]:
                    _nt = (
                        _ni.get("title") or
                        (_ni.get("content") or {}).get("title") or ""
                    ).strip()
                    if _nt:
                        _ev_compounds.append(_ev_va.polarity_scores(_nt)["compound"])
                _avg_sent = (round(sum(_ev_compounds) / len(_ev_compounds), 2)
                             if _ev_compounds else None)

                # Verdict: does analyst data confirm or contradict the momentum signal?
                if _rec_mean is None and not _ev_revs:
                    _verdict, _v_color = "⚪ No Coverage", "#888"
                elif (_rec_mean is not None and _rec_mean <= 2.5
                      and (_ev_net is None or _ev_net >= 0)):
                    _verdict, _v_color = "✅ Confirmed", "#00C851"
                elif (_rec_mean is not None and _rec_mean > 3.5
                      or (_ev_net is not None and _ev_net <= -2)):
                    _verdict, _v_color = "❌ Diverging", "#ff4444"
                else:
                    _verdict, _v_color = "⚠️ Mixed", "#ffbb33"

                _ev_rows.append({
                    "ticker": _ev_t,        "score":      _ev_score,
                    "price":  _ev_price,    "rec_label":  _rec_label,
                    "rec_mean": _rec_mean,  "n_analysts": _n_analysts,
                    "target": _ev_target,   "upside":     _ev_upside,
                    "t_high": _ev_t_high,   "t_low":      _ev_t_low,  "t_med": _ev_t_med,
                    "ups":    _ev_ups,      "downs":      _ev_downs,   "maint": _ev_maint,
                    "net":    _ev_net,      "latest":     _ev_revs.get("latest", []),
                    "earnings": _ev_earn,   "earn_days":  _ev_earn_days,
                    "avg_sent": _avg_sent,  "n_news":     len(_ev_compounds),
                    "verdict":  _verdict,   "v_color":    _v_color,
                    "info":     _ev_info,
                    # Scanner score breakdown
                    "rsi": _ev_rsi, "rsi_pts": _ev_rsi_pts,
                    "trend": _ev_trend, "trend_pts": _ev_trend_pts,
                    "mom_1m": _ev_m1m, "mom_1m_pts": _ev_m1m_pts,
                    "mom_3m": _ev_m3m, "mom_3m_pts": _ev_m3m_pts,
                    # Composite score (None if not yet pre-fetched)
                    "comp_score": _ev_comp_score, "comp_label": _ev_comp_label,
                    "tech_score": _ev_tech_score, "fund_score": _ev_fund_score,
                    "sent_score": _ev_sent_score,
                })

            st.subheader("📊 Signal Evidence — Top 10")

            # ── Summary evidence table ────────────────────────────────────
            _ev_tbl = pd.DataFrame([{
                "Ticker":    r["ticker"],
                "Score":     r["score"],
                "Analyst":   (f"{r['rec_label']} / {r['n_analysts']}"
                              if r["n_analysts"] else r["rec_label"]),
                "Upside":    r["upside"],
                "Revisions": (f"↑{r['ups']} ↓{r['downs']} net {r['net']:+d}"
                              if r["net"] is not None else "—"),
                "Earnings":  (f"In {r['earn_days']}d"
                              if r["earn_days"] is not None and r["earn_days"] >= 0
                              else ("Past" if r["earn_days"] is not None else "—")),
                "Verdict":   r["verdict"],
            } for r in _ev_rows])

            def _ev_verdict_style(v):
                if "Confirmed" in str(v): return "color:#00C851;font-weight:bold"
                if "Diverging" in str(v): return "color:#ff4444;font-weight:bold"
                if "Mixed"     in str(v): return "color:#ffbb33;font-weight:bold"
                return "color:#888"
            def _ev_score_style(v):
                if isinstance(v, (int, float)):
                    if v >= 70: return "color:#00C851;font-weight:bold"
                    if v >= 50: return "color:#ffbb33"
                    return "color:#ff4444"
                return ""
            def _ev_upside_style(v):
                if isinstance(v, (int, float)):
                    return ("color:#00C851" if v >= 10 else
                            "color:#ff4444" if v <= -5  else "")
                return ""
            def _ev_earn_style(v):
                if isinstance(v, str) and v.startswith("In "):
                    try:
                        days = int(v.split()[1].rstrip("d"))
                        if days <= 7:  return "color:#ef4444;font-weight:bold"
                        if days <= 14: return "color:#f59e0b;font-weight:bold"
                    except Exception:
                        pass
                return ""

            st.dataframe(
                _ev_tbl.style
                .map(_ev_verdict_style, subset=["Verdict"])
                .map(_ev_score_style,   subset=["Score"])
                .map(_ev_upside_style,  subset=["Upside"])
                .map(_ev_earn_style,    subset=["Earnings"])
                .format({
                    "Score":  "{:.0f}",
                    "Upside": lambda x: f"{x:+.1f}%" if isinstance(x, (int, float)) else "—",
                })
                .hide(axis="index"),
                use_container_width=True,
            )
            st.caption(
                "✅ Confirmed = momentum + analyst consensus + positive revisions all aligned  ·  "
                "⚠️ Mixed = analyst cautious despite strong price action  ·  "
                "❌ Diverging = analysts bearish — proceed carefully"
            )

            # ── Detailed drill-down cards ─────────────────────────────────
            st.markdown("**Drill-down** — expand any ticker for the full evidence breakdown:")
            for _evr in _ev_rows:
                _card_hdr = (
                    f"{_evr['verdict']}  ·  {_evr['ticker']}  ·  "
                    f"Score {_evr['score']}  ·  {_evr['rec_label']}"
                )
                with st.expander(_card_hdr):

                    # ── Jump to Analysis — visible at the top so the user
                    # doesn't have to scroll through the evidence to act.
                    _btn_c1, _btn_c2 = st.columns([1, 4])
                    with _btn_c1:
                        if st.button(
                            f"▶ Analyze {_evr['ticker']}",
                            key=f"_sc_ev_analyze_{_evr['ticker']}",
                            use_container_width=True,
                        ):
                            st.session_state["_pending_page"]    = "📈 Analysis"
                            st.session_state["_analysis_ticker"] = _evr["ticker"]
                            st.rerun()

                    # ── Score breakdown panel ──────────────────────────────
                    _comp_sc  = _evr.get("comp_score")
                    _comp_lbl = _evr.get("comp_label", "")
                    _t_sc     = _evr.get("tech_score")
                    _f_sc     = _evr.get("fund_score")
                    _s_sc     = _evr.get("sent_score")

                    # Build composite line if available
                    if _comp_sc is not None:
                        _comp_clr = "#22c55e" if _comp_sc >= 68 else "#f59e0b" if _comp_sc >= 60 else "#ef4444"
                        _comp_breakdown = ""
                        if _t_sc is not None and _f_sc is not None and _s_sc is not None:
                            _comp_breakdown = (
                                f"<span style='color:#6b7280;font-size:0.78em'>"
                                f" (Technical {_t_sc:.0f}×45% + "
                                f"Fundamental {_f_sc:.0f}×40% + "
                                f"Sentiment {_s_sc:.0f}×15%)</span>"
                            )
                        _comp_line = (
                            f"<div style='margin-top:4px'>"
                            f"<span style='color:#9ca3af;font-size:0.82em'>Composite: </span>"
                            f"<span style='color:{_comp_clr};font-weight:700;font-size:0.9em'>"
                            f"{_comp_sc:.1f}/100 — {_comp_lbl}</span>"
                            f"{_comp_breakdown}</div>"
                        )
                    else:
                        _comp_line = (
                            "<div style='margin-top:4px;color:#6b7280;font-size:0.8em'>"
                            "Composite: not yet fetched — click Refresh Signals to validate</div>"
                        )

                    st.markdown(
                        "<div style='background:#0f172a;border:1px solid #334155;"
                        "border-radius:8px;padding:10px 14px;margin-bottom:12px'>"
                        "<div style='color:#94a3b8;font-size:0.72em;font-weight:600;"
                        "letter-spacing:0.06em;margin-bottom:6px'>📊 HOW THE SCORE IS BUILT</div>"

                        # Scanner score row
                        f"<div style='color:#f9fafb;font-size:0.88em;font-weight:700;margin-bottom:4px'>"
                        f"Momentum Score: {_evr['score']}/100"
                        f"<span style='color:#6b7280;font-weight:400;font-size:0.85em'>"
                        f" — technical setup only (RSI + trend + price momentum)</span></div>"

                        # Component breakdown
                        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;font-size:0.8em'>"

                        f"<div><span style='color:#9ca3af'>RSI {_evr['rsi']:.1f}</span>"
                        f"<span style='color:#6b7280'> → </span>"
                        f"<span style='color:#fbbf24;font-weight:700'>{_evr['rsi_pts']}/30 pts</span>"
                        f"<span style='color:#4b5563;font-size:0.85em'>"
                        f" ({'sweet spot 40–65' if 40 <= _evr['rsi'] <= 65 else 'overbought' if _evr['rsi'] >= 75 else 'oversold'})</span></div>"

                        f"<div><span style='color:#9ca3af'>1M momentum {_evr['mom_1m']:+.1f}%</span>"
                        f"<span style='color:#6b7280'> → </span>"
                        f"<span style='color:#fbbf24;font-weight:700'>{_evr['mom_1m_pts']}/20 pts</span></div>"

                        f"<div><span style='color:#9ca3af'>Trend: {_evr['trend']}</span>"
                        f"<span style='color:#6b7280'> → </span>"
                        f"<span style='color:#fbbf24;font-weight:700'>{_evr['trend_pts']}/35 pts</span></div>"

                        f"<div><span style='color:#9ca3af'>3M momentum {_evr['mom_3m']:+.1f}%</span>"
                        f"<span style='color:#6b7280'> → </span>"
                        f"<span style='color:#fbbf24;font-weight:700'>{_evr['mom_3m_pts']}/15 pts</span></div>"

                        "</div>"

                        # Composite score (vs momentum score)
                        + _comp_line +

                        "</div>",
                        unsafe_allow_html=True,
                    )

                    _dc1, _dc2 = st.columns(2)

                    with _dc1:
                        st.markdown("**📈 Analyst Consensus**")
                        _rc = (
                            "#00C851" if _evr["rec_mean"] and _evr["rec_mean"] <= 2.5 else
                            "#ff4444" if _evr["rec_mean"] and _evr["rec_mean"] > 3.5  else
                            "#ffbb33"
                        )
                        st.markdown(
                            f"<span style='color:{_rc};font-weight:bold;font-size:1.05em'>"
                            f"{_evr['rec_label']}</span>"
                            + (f" · {_evr['n_analysts']} analysts" if _evr["n_analysts"] else ""),
                            unsafe_allow_html=True,
                        )
                        if _evr["target"] and _evr["upside"] is not None:
                            _uc = "#00C851" if _evr["upside"] >= 0 else "#ff4444"
                            st.markdown(
                                f"Mean target: **${_evr['target']:.2f}**  "
                                f"<span style='color:{_uc}'>({_evr['upside']:+.1f}%)</span>",
                                unsafe_allow_html=True,
                            )
                        if _evr["t_low"] and _evr["t_high"]:
                            st.caption(
                                f"Range ${_evr['t_low']:.2f} → ${_evr['t_high']:.2f}"
                                + (f"  ·  Median ${_evr['t_med']:.2f}" if _evr["t_med"] else "")
                            )

                        st.markdown("**🔼 Analyst Revisions (last 90 days)**")
                        if _evr["net"] is not None:
                            _nc = ("#00C851" if _evr["net"] > 0 else
                                   "#ff4444" if _evr["net"] < 0 else "#888")
                            st.markdown(
                                f"Upgrades **{_evr['ups']}** · "
                                f"Downgrades **{_evr['downs']}** · "
                                f"Maintained **{_evr['maint']}**  \n"
                                f"Net: <span style='color:{_nc};font-weight:bold'>"
                                f"{_evr['net']:+d}</span>",
                                unsafe_allow_html=True,
                            )
                            for _lt in (_evr["latest"] or [])[:3]:
                                _a = str(_lt.get("action", "")).upper()
                                _li = ("🔼" if _a in ("UP", "INIT") else
                                       "🔽" if _a == "DOWN" else "➡")
                                _frm = _lt.get("from_grade", "")
                                _tog = _lt.get("to_grade", "")
                                _sep = f" → {_tog}" if _tog and _tog != _frm else ""
                                st.caption(
                                    f"{_li} {_lt.get('firm', '')}  ·  "
                                    f"{_frm}{_sep}  ({_lt.get('action', '')})"
                                )
                        else:
                            st.caption("No revision data available")

                    with _dc2:
                        st.markdown("**📅 Earnings Catalyst**")
                        _ed = _evr["earn_days"]
                        if _evr["earnings"] and _ed is not None:
                            if 0 <= _ed <= 14:
                                st.markdown(
                                    f"⚠️ **{_evr['earnings']}** — in **{_ed} days**",
                                    help="Imminent earnings → expect elevated volatility",
                                )
                                st.caption("Imminent — consider sizing down before report.")
                            elif 0 <= _ed <= 30:
                                st.markdown(f"📅 **{_evr['earnings']}** — in {_ed} days")
                            elif _ed > 30:
                                st.markdown(f"📅 {_evr['earnings']} — in {_ed} days")
                            else:
                                st.caption(f"Last reported: {_evr['earnings']}")
                        else:
                            st.caption("Earnings date not available")

                        st.markdown("**💰 Key Fundamentals**")
                        _ei   = _evr["info"]
                        _fpe  = _ei.get("forwardPE")
                        _revg = _ei.get("revenueGrowth")
                        _marg = _ei.get("profitMargins")
                        _roe  = _ei.get("returnOnEquity")
                        _de   = _ei.get("debtToEquity")
                        _fcf  = _ei.get("freeCashflow")
                        _mktc = _ei.get("marketCap")
                        _fcfy = (round(_fcf / _mktc * 100, 1)
                                 if _fcf and _mktc and _mktc > 0 else None)
                        _flines = []
                        if _fpe:   _flines.append(f"Fwd P/E: **{_fpe:.1f}x**")
                        if _revg:  _flines.append(f"Rev growth: **{_revg * 100:+.1f}%**")
                        if _marg:  _flines.append(f"Profit margin: **{_marg * 100:.1f}%**")
                        if _roe:   _flines.append(f"ROE: **{_roe * 100:.1f}%**")
                        if _fcfy:  _flines.append(f"FCF yield: **{_fcfy:.1f}%**")
                        if _de:    _flines.append(f"D/E ratio: **{_de:.1f}x**")
                        if _flines:
                            st.markdown("  \n".join(_flines))
                        else:
                            st.caption("Fundamental data not available")

                        if _evr["avg_sent"] is not None:
                            st.markdown("**📰 News Sentiment**")
                            _sc = _evr["avg_sent"]
                            _sl = ("Positive" if _sc >= 0.05 else
                                   "Negative" if _sc <= -0.05 else "Neutral")
                            _sc_col = ("#00C851" if _sc >= 0.05 else
                                       "#ff4444" if _sc <= -0.05 else "#888")
                            st.markdown(
                                f"<span style='color:{_sc_col};font-weight:bold'>{_sl}</span>"
                                f" ({_sc:+.2f} avg · {_evr['n_news']} recent articles)",
                                unsafe_allow_html=True,
                            )

        st.divider()

        # Full results table
        st.subheader(f"All Results ({len(filtered)} stocks, score ≥ {min_score})")

        # Sector heat map
        if len(filtered) > 3:
            with st.expander("📊 Sector Score Heatmap"):
                sector_avg = (
                    filtered.groupby("Sector")["Score"]
                    .mean()
                    .round(1)
                    .sort_values(ascending=False)
                    .reset_index()
                )
                heat_fig = go.Figure(go.Bar(
                    x=sector_avg["Sector"], y=sector_avg["Score"],
                    marker_color=[
                        "#00C851" if s >= 70 else ("#ffbb33" if s >= 50 else "#ff4444")
                        for s in sector_avg["Score"]
                    ],
                    text=[f"{s:.0f}" for s in sector_avg["Score"]],
                    textposition="outside",
                ))
                heat_fig.add_hline(y=65, line_dash="dash", line_color="white",
                                   annotation_text="Buy threshold")
                heat_fig.update_layout(
                    template="plotly_dark", height=260,
                    yaxis_title="Avg Score", yaxis_range=[0, 105],
                    margin=dict(l=0, r=0, t=10, b=0),
                )
                st.plotly_chart(heat_fig, use_container_width=True)

        def _scan_sig_color(val):
            s = str(val)
            if "Strong Buy" in s: return "background:#00C85122;color:#00C851;font-weight:bold"
            if "Buy" in s:        return "color:#00b300"
            if "Avoid" in s:      return "color:#ff4444"
            if "Weak" in s:       return "color:#ff8800"
            return ""

        def _mom_color(val):
            if isinstance(val, (int, float)):
                return "color:#00C851" if val > 5 else ("color:#ff4444" if val < -5 else "")
            return ""

        def _score_color(val):
            if isinstance(val, (int, float)):
                if val >= 70: return "color:#00C851;font-weight:bold"
                if val >= 50: return "color:#ffbb33"
                return "color:#ff4444"
            return ""

        # Rename Score → Momentum Score for display clarity
        scan_display = filtered.rename(columns={"Score": "Momentum Score"}).copy()

        # Flag tickers already held in portfolio
        held_tickers_set = {
            str(r.get("Ticker", "")).strip().upper()
            for _, r in st.session_state.holdings_df.iterrows()
        }
        overlap = [t for t in filtered["Ticker"] if t in held_tickers_set]
        if overlap:
            st.warning(
                f"**{', '.join(overlap)}** appear in both your portfolio and these scan results.  \n"
                "Their momentum score here may differ from the composite score in your portfolio — "
                "check the full analysis before adding more exposure."
            )

        styled = (
            scan_display.style
            .map(_scan_sig_color, subset=["Signal"])
            .map(_mom_color, subset=["1M Momentum", "3M Momentum"])
            .map(_score_color, subset=["Momentum Score"])
            .format({
                "Price": "${:.2f}",
                "Momentum Score": "{:.0f}",
                "RSI": "{:.1f}",
                "1M Momentum": "{:+.1f}%",
                "3M Momentum": "{:+.1f}%",
                "Vol Ratio": "{:.1f}x",
            })
            .hide(axis="index")
        )
        st.dataframe(styled, use_container_width=True)

        # ── Quick Analyze — pick any ticker from filtered results ─────────────
        # Complements the per-card Analyze buttons above. Lets the user jump
        # from any row in the table (not just top 5 / top 10) straight to the
        # Analysis page without having to type the ticker manually.
        st.divider()
        _qa_c1, _qa_c2 = st.columns([3, 1])
        with _qa_c1:
            _qa_options = filtered["Ticker"].tolist()
            _qa_pick = st.selectbox(
                "Quick Analyze — pick any ticker from these results",
                options=_qa_options,
                index=0 if _qa_options else None,
                key="_sc_quick_analyze_pick",
            )
        with _qa_c2:
            st.write("")
            st.write("")
            if st.button("▶ Analyze", key="_sc_quick_analyze_go",
                         disabled=not _qa_pick, use_container_width=True):
                st.session_state["_pending_page"]    = "📈 Analysis"
                st.session_state["_analysis_ticker"] = _qa_pick
                st.rerun()

        # Add to watchlist
        st.divider()
        add_col1, add_col2 = st.columns([2, 1])
        with add_col1:
            candidates = filtered["Ticker"].tolist()
            to_add = st.multiselect(
                "Add to Analysis Watchlist",
                options=candidates,
                default=[t for t in candidates[:3] if t not in st.session_state.watchlist],
            )
        with add_col2:
            st.write("")
            st.write("")
            if st.button("➕ Add to Watchlist"):
                for t in to_add:
                    if t not in st.session_state.watchlist:
                        st.session_state.watchlist.append(t)
                db.save_watchlist(st.session_state.watchlist)
                st.success(f"Added {len(to_add)} ticker(s) — watchlist saved.")
        st.caption(f"Current watchlist: {', '.join(st.session_state.watchlist)}")


# ═════════════════════════════════════════════════════════════════════════════
# PAGE 3 — STOCK ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
elif page == "📈 Analysis":
    # ── Back button — shown when navigated here via an Analyze button ─────────
    _nav_origin = st.session_state.get("_nav_origin", "")
    if _nav_origin:
        _back_label = {
            "🏠 Home":   "← Back to Home",
            "🔍 Market Scanner": "← Back to Market Scanner",
            "📋 Watchlist":      "← Back to Watchlist",
            "📋 Today's Brief":  "← Back to Today's Brief",
        }.get(_nav_origin, f"← Back to {_nav_origin}")
        if st.button(_back_label, key="_sa_back"):
            st.session_state["_pending_page"] = _nav_origin
            del st.session_state["_nav_origin"]
            st.rerun()

    st.title("📈 Analysis")

    # Consume any ticker pre-selection set by navigation buttons (News Intelligence, etc.)
    _preselect_ticker = st.session_state.pop("_analysis_ticker", None)

    with st.sidebar:
        st.divider()
        name_to_ticker = dict(DEFAULT_TICKERS)
        # Merge watchlist tickers
        for t in st.session_state.watchlist:
            if t not in name_to_ticker.values():
                name_to_ticker[t] = t

        # If navigated here from an Analyze button, add that ticker and pre-select it
        if _preselect_ticker:
            _pt = _preselect_ticker.strip().upper()
            # Find the display-name key for this ticker (may already exist as "Company (TICK)")
            _pt_key = next((k for k, v in name_to_ticker.items() if v == _pt), None)
            if _pt_key is None:
                # Not yet in options — add with ticker as both key and value
                name_to_ticker[_pt] = _pt
                _pt_key = _pt
            _sa_default = [_pt_key]
        else:
            watchlist_names = [
                k for k, v in name_to_ticker.items() if v in st.session_state.watchlist
            ] + [t for t in st.session_state.watchlist if t not in name_to_ticker.values()]
            _sa_default = [n for n in watchlist_names if n in name_to_ticker][:4]

        selected_names = st.multiselect(
            "Companies", options=list(name_to_ticker.keys()),
            default=_sa_default,
        )
        custom = st.text_input("Add ticker", "")
        if custom:
            t = custom.strip().upper()
            name_to_ticker[t] = t
            if t not in selected_names:
                selected_names.append(t)
        period = st.selectbox("History period", ["3mo", "6mo", "1y", "2y"], index=1)
        show_volume = st.checkbox("Show volume", value=True)

    if not selected_names:
        st.info("Select tickers in the sidebar or run the Market Scanner first.")
        st.stop()

    tickers = [name_to_ticker.get(n, n) for n in selected_names]
    results: dict = {}
    with st.spinner("Fetching data…"):
        for ticker in tickers:
            try:
                results[ticker] = load_all(ticker, period)
            except Exception as e:
                st.error(f"{ticker}: {e}")

    if not results:
        st.error("No data loaded.")
        st.stop()

    _news = curate_news_items(results)
    st.session_state["_sidebar_news"] = _news
    _fill_news_slot(_news_slot, _news)

    # ── Summary scorecard ──────────────────────────────────────────────────
    st.subheader("Summary Scorecard")
    _sc_port = st.session_state.get("_port_df_enriched", pd.DataFrame())
    rows = []
    for ticker, r in results.items():
        price = r["current_price"]
        targets = r["targets"]
        ps = (
            position_sizing(portfolio_value, MODERATE_RISK_PCT, price, r["stop"])
            if price and r["stop"] and price > r["stop"] else None
        )
        rr_val = risk_reward(price, r["stop"], targets["base"]) if price and r["stop"] and targets else None
        earn = r["earnings"]
        earn_label = "—"
        if earn:
            try:
                days = (datetime.strptime(earn, "%Y-%m-%d").date() - date.today()).days
                earn_label = f"{earn} ({days}d)" if days >= 0 else earn
            except Exception:
                earn_label = earn

        # Check if this ticker is held in the portfolio
        _sc_held = None
        if not _sc_port.empty and ticker in _sc_port["Ticker"].values:
            _sc_held = _sc_port[_sc_port["Ticker"] == ticker].iloc[0].to_dict()

        _sc_is_sell = r["rec"]["label"] in ("Sell", "Strong Sell")
        _sc_is_buy  = r["rec"]["label"] in ("Buy", "Strong Buy")

        # "Position / Entry Zone" column — held: show holding; buy: show entry zone
        if _sc_held:
            _avg_cost = _sc_held.get("Avg Cost", 0)
            _sc_position = f"{int(_sc_held.get('Shares', 0))} sh @ ${_avg_cost:.2f} avg"
        else:
            _sc_position = f"${r['entry_lo']:.2f}–${r['entry_hi']:.2f}" if r["entry_lo"] else "—"

        # "Shares" column — held: actual shares; not held: suggested entry
        if _sc_held:
            _sc_shares = int(_sc_held.get("Shares", 0))
        elif ps:
            _sc_shares = ps["shares"]
        else:
            _sc_shares = "—"

        # "P&L / Entry Cost" column — held: P&L; not held: entry cost
        if _sc_held:
            _sc_cost = f"{_sc_held.get('P&L (%)', 0):+.1f}% P&L"
        elif ps:
            _sc_cost = f"${ps['total_cost']:,.0f}"
        else:
            _sc_cost = "—"

        # R:R only meaningful for buy signals
        _sc_rr = f"{rr_val:.1f}:1" if (rr_val and rr_val > 0 and _sc_is_buy) else "—"

        rows.append({
            "Ticker":           ticker,
            "Price":            f"${price:.2f}" if price else "N/A",
            "Score":            r["total"],
            "Signal":           f"{r['rec']['icon']} {r['rec']['label']}",
            "Position / Entry": _sc_position,
            "Stop":             f"${r['stop']:.2f}" if r["stop"] else "—",
            "Base Target":      f"${targets['base']:.2f} ({targets['base_pct']:+.1f}%)" if targets else "—",
            "R:R":              _sc_rr,
            "Shares":           _sc_shares,
            "P&L / Cost":       _sc_cost,
            "Earnings":         earn_label,
        })

    summary_df = pd.DataFrame(rows).set_index("Ticker")

    def _sig(v):
        s = str(v)
        if "Strong Buy" in s: return "background-color:#00C851;color:white"
        if "Buy" in s:        return "background-color:#00b300;color:white"
        if "Hold" in s:       return "background-color:#ffbb33;color:black"
        if "Strong Sell" in s: return "background-color:#CC0000;color:white"
        if "Sell" in s:       return "background-color:#ff4444;color:white"
        return ""

    def _sc(v):
        if not isinstance(v, (int, float)): return ""
        if v >= 65: return "color:#00C851;font-weight:bold"
        if v >= 50: return "color:#ffbb33"
        return "color:#ff4444"

    st.dataframe(
        summary_df.style.map(_sig, subset=["Signal"]).map(_sc, subset=["Score"])
        .format({"Score": "{:.1f}", "Shares": lambda v: str(v)}),
        use_container_width=True,
    )

    # ── Per-ticker tabs ────────────────────────────────────────────────────
    st.subheader("Detailed Analysis")
    ticker_tabs = st.tabs(list(results.keys()))

    for tab, (ticker, r) in zip(ticker_tabs, results.items()):
        with tab:
            rec = r["rec"]
            df = r["df"]
            price = r["current_price"]
            targets = r["targets"]
            ps = (
                position_sizing(portfolio_value, MODERATE_RISK_PCT, price, r["stop"])
                if price and r["stop"] and price > r["stop"] else None
            )
            sr = r["sr"]
            rm = r["risk_metrics"]

            st.markdown(
                f"<div style='padding:10px;border-radius:8px;background:{rec['color']}18;"
                f"border-left:5px solid {rec['color']};margin-bottom:10px'>"
                f"<b style='font-size:1.1em;color:{rec['color']}'>{rec['icon']} {rec['label']} "
                f"· {r['total']}/100</b>"
                f"<span style='color:#888;font-size:0.85em'> "
                f"(<abbr title='{_tip('RSI').split(chr(10))[0]}' style='cursor:help'>Technical</abbr> "
                f"{r['t_score']:.0f} × 45% + "
                f"<abbr title='{_tip('FCF Yield').split(chr(10))[0]}' style='cursor:help'>Fundamental</abbr> "
                f"{r['f_score']:.0f} × 40% + Sentiment {r['s_score']:.0f} × 15%)"
                f"</span><br>{rec['rationale']}"
                + (f"<br><small>📍 {r['upside']}</small>"
                   if r["upside"]
                   and rec["label"] not in ("Sell", "Strong Sell")
                   and "upside" in r["upside"].lower()
                   else "")
                + "</div>", unsafe_allow_html=True,
            )

            # Source links
            st.markdown(
                f"[📊 Yahoo Finance](https://finance.yahoo.com/quote/{ticker}) · "
                f"[📈 Finviz](https://finviz.com/quote.ashx?t={ticker}) · "
                f"[📰 SEC Filings](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=10-K) · "
                f"[🔍 News](https://finance.yahoo.com/quote/{ticker}/news/)"
            )

            # ── Company Overview ──────────────────────────────────────────
            # Small block right before the Trade Plan: sector / industry /
            # market cap / 1-line business summary. Sourced from yfinance .info
            # (already in the load_all() cache, no extra network call).
            _co_name     = r.get("name", "") or ticker
            _co_sector_raw   = (r.get("sector") or "").strip()
            _co_industry_raw = (r.get("industry") or "").strip()
            _co_mcap     = r.get("market_cap")
            _co_summary  = (r.get("business_summary") or "").strip()

            # Format market cap as $X.YT / $XYZB / $XYZM
            def _fmt_mcap(v):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return None
                if v >= 1e12: return f"${v/1e12:.2f}T"
                if v >= 1e9:  return f"${v/1e9:.1f}B"
                if v >= 1e6:  return f"${v/1e6:.0f}M"
                return f"${v:,.0f}"
            _co_mcap_str = _fmt_mcap(_co_mcap) if _co_mcap is not None else None

            _has_any_data = bool(
                _co_sector_raw or _co_industry_raw or _co_mcap_str or _co_summary
            )

            if not _has_any_data:
                # Sparse yfinance .info — render a clean placeholder pointing
                # the user at the Yahoo Finance link instead of dashes.
                st.markdown(
                    f"<div style='background:#111827;border:1px solid #374151;"
                    f"border-radius:8px;padding:8px 14px;margin:8px 0 12px;"
                    f"font-size:0.82em;color:#9ca3af'>"
                    f"🏢 <b style='color:#e5e7eb'>{_co_name}</b> — company info "
                    f"unavailable from Yahoo Finance right now. Try the "
                    f"<a href='https://finance.yahoo.com/quote/{ticker}/profile' "
                    f"style='color:#60a5fa' target='_blank'>Yahoo profile page</a> "
                    f"or refresh the Analysis page in a minute."
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                # Build the metadata line from whatever fields ARE populated —
                # missing values are skipped rather than rendered as dashes.
                _meta_parts = []
                if _co_sector_raw and _co_industry_raw:
                    _meta_parts.append(f"{_co_sector_raw} · {_co_industry_raw}")
                elif _co_sector_raw:
                    _meta_parts.append(_co_sector_raw)
                elif _co_industry_raw:
                    _meta_parts.append(_co_industry_raw)
                if _co_mcap_str:
                    _meta_parts.append(f"Market cap <b style='color:#e5e7eb'>{_co_mcap_str}</b>")
                _meta_line = " · ".join(_meta_parts) if _meta_parts else ""

                # Trim summary to first sentence(s) up to ~280 chars so the block
                # stays compact — full summary is one click away on Yahoo/Finviz.
                if _co_summary:
                    _sentences = _co_summary.replace("\n", " ").split(". ")
                    _co_one_liner = _sentences[0]
                    if len(_co_one_liner) < 200 and len(_sentences) > 1:
                        _co_one_liner = ". ".join(_sentences[:2])
                    if not _co_one_liner.endswith("."):
                        _co_one_liner += "."
                    _co_one_liner = _co_one_liner[:320]
                else:
                    _co_one_liner = ""

                st.markdown(
                    f"<div style='background:#111827;border:1px solid #374151;"
                    f"border-radius:8px;padding:10px 14px;margin:8px 0 12px'>"
                    f"<div style='display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;"
                    f"margin-bottom:6px'>"
                    f"<span style='color:#9ca3af;font-size:0.72em;font-weight:700;"
                    f"letter-spacing:0.08em;text-transform:uppercase'>"
                    f"🏢 Company Overview</span>"
                    f"<span style='color:#f9fafb;font-weight:700'>{_co_name}</span>"
                    + (f"<span style='color:#9ca3af;font-size:0.82em'>{_meta_line}</span>"
                       if _meta_line else "")
                    + f"</div>"
                    + (f"<div style='color:#d1d5db;font-size:0.85em;line-height:1.5'>"
                       f"{_co_one_liner}</div>" if _co_one_liner else "")
                    + f"</div>",
                    unsafe_allow_html=True,
                )

            # Signal-aware tab label and portfolio lookup
            _sa_is_sell = rec["label"] in ("Sell", "Strong Sell")
            _sa_is_hold = rec["label"] == "Hold"
            _sa_holding = None
            _sa_port = st.session_state.get("_port_df_enriched", pd.DataFrame())
            if not _sa_port.empty and ticker in _sa_port["Ticker"].values:
                _sa_holding = _sa_port[_sa_port["Ticker"] == ticker].iloc[0].to_dict()

            _plan_label = "🚪 Exit Plan" if _sa_is_sell else "📋 Trade Plan"
            plan_tab, chart_tab, risk_tab, deep_tab = st.tabs(
                [_plan_label, "📈 Chart", "⚖️ Risk", "🔬 Deep Dive"]
            )

            # ── Trade Plan / Exit Plan ─────────────────────────────────────
            with plan_tab:
                rr_val = risk_reward(price, r["stop"], targets["base"]) if price and r["stop"] and targets else None

                if _sa_is_sell:
                    # ── EXIT PLAN ──────────────────────────────────────────
                    if _sa_holding:
                        _sh = _sa_holding
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Current Price",  f"${price:.2f}" if price else "N/A")
                        c2.metric("Shares Held",    f"{int(_sh.get('Shares', 0)):,}")
                        c3.metric("Position Value", f"${_sh.get('Market Value', 0):,.0f}")
                        c4.metric(
                            "P&L if Sold Now",
                            f"${_sh.get('P&L ($)', 0):+,.0f}",
                            f"{_sh.get('P&L (%)', 0):+.1f}%",
                            delta_color="normal",
                        )
                    else:
                        _ec1, _ec2 = st.columns([1, 3])
                        _ec1.metric("Current Price", f"${price:.2f}" if price else "N/A")
                        with _ec2:
                            st.info(
                                f"**{ticker}** is not in your portfolio — "
                                "this is an informational Sell signal for awareness only."
                            )

                    # Exit recommendation banner
                    _exit_urgent = rec["label"] == "Strong Sell"
                    _exit_color  = "#ef4444" if _exit_urgent else "#f59e0b"
                    _exit_bg     = "#450a0a" if _exit_urgent else "#422006"
                    _exit_action = (
                        "Exit full position. Multiple bearish signals indicate elevated risk of continued decline."
                        if _exit_urgent else
                        "Consider reducing or exiting. At minimum, raise your stop loss to protect gains."
                    )
                    st.markdown(
                        f"<div style='background:{_exit_bg};border-left:4px solid {_exit_color};"
                        f"border-radius:8px;padding:12px 16px;margin:10px 0'>"
                        f"<div style='color:{_exit_color};font-weight:700'>🚪 Recommended Action</div>"
                        f"<div style='color:#d1d5db;font-size:0.88em;margin-top:4px'>{_exit_action}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if r["stop"] and price:
                        if r["stop"] < price:
                            st.caption(
                                f"📉 ATR downside level: **${r['stop']:.2f}** — "
                                "a break below this may accelerate the decline."
                            )
                        else:
                            st.caption(
                                f"⚠ Sell signal triggered above ATR stop (${r['stop']:.2f}) — "
                                "momentum has reversed. Exit before stop is tested."
                            )

                    # Downside scenarios (reframed for exit context)
                    if targets:
                        st.markdown("#### Downside Risk — If You Hold")
                        _sc1, _sc2 = st.columns([2, 1])
                        with _sc1:
                            _sfig = go.Figure()
                            for _case, _tgt, _pct, _clr in [
                                ("Bear", targets["bear"], targets["bear_pct"], "#ff4444"),
                                ("Base", targets["base"], targets["base_pct"], "#ffbb33"),
                                ("Bull", targets["bull"], targets["bull_pct"], "#00C851"),
                            ]:
                                _sfig.add_trace(go.Bar(
                                    x=[_case], y=[_tgt], name=_case, marker_color=_clr,
                                    text=f"${_tgt:.2f}<br>{_pct:+.1f}%", textposition="outside",
                                ))
                            if price:
                                _sfig.add_hline(y=price, line_dash="dash", line_color="white",
                                                annotation_text=f"Now ${price:.2f}",
                                                annotation_position="right")
                            _sfig.update_layout(
                                height=300, template="plotly_dark", showlegend=False,
                                margin=dict(l=0, r=80, t=30, b=0),
                            )
                            st.plotly_chart(_sfig, use_container_width=True)
                        with _sc2:
                            st.markdown("**Potential outcomes if held**")
                            _held_shares = int(_sa_holding.get("Shares", 0)) if _sa_holding else None
                            for _lbl, _tgt, _pct, _clr in [
                                ("🐂 Recovery", targets["bull"], targets["bull_pct"], "#00C851"),
                                ("➡ Base",     targets["base"], targets["base_pct"], "#ffbb33"),
                                ("🐻 Bear risk", targets["bear"], targets["bear_pct"], "#ff4444"),
                            ]:
                                _impact = ""
                                if _held_shares and price:
                                    _chg = (_tgt - price) * _held_shares
                                    _impact = f" (${_chg:+,.0f})"
                                st.markdown(
                                    f"{_lbl} `${_tgt:.2f}` "
                                    f"<span style='color:{_clr}'>{_pct:+.1f}%{_impact}</span>",
                                    unsafe_allow_html=True,
                                )

                elif _sa_is_hold:
                    # ── HOLD — POSITION MONITOR ────────────────────────────
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Price", f"${price:.2f}" if price else "N/A")
                    c2.metric("Stop Loss", f"${r['stop']:.2f}" if r["stop"] else "N/A",
                              delta=f"-{(price-r['stop'])/price*100:.1f}%" if price and r["stop"] else None,
                              delta_color="inverse", help=_tip("ATR Stop"))
                    if _sa_holding:
                        c3.metric("Shares Held", f"{int(_sa_holding.get('Shares', 0)):,}")
                        c4.metric("P&L",
                                  f"${_sa_holding.get('P&L ($)', 0):+,.0f}",
                                  f"{_sa_holding.get('P&L (%)', 0):+.1f}%")
                        from datetime import date, timedelta
                        _recheck = (date.today() + timedelta(days=7)).strftime("%b %d")
                        st.info(
                            f"**Hold** your {int(_sa_holding.get('Shares', 0))} shares with stop at "
                            f"**${r['stop']:.2f}**. Mixed signals — re-check {_recheck}. "
                            f"Add to position if score ≥ {COMPOSITE_BUY:.0f} and price holds above stop. "
                            f"Exit immediately if price closes below stop."
                        )
                    else:
                        c3.metric("Entry Zone",
                                  f"${r['entry_lo']:.2f}–${r['entry_hi']:.2f}" if r["entry_lo"] else "N/A")
                        c4.metric("R:R", f"{rr_val:.1f}:1" if rr_val and rr_val > 0 else "N/A",
                                  help=_tip("R:R Ratio"))
                        st.caption(
                            "Mixed signals — not a high-conviction entry. "
                            "Wait for clearer trend before initiating a new position."
                        )

                    if targets:
                        st.markdown("#### Price Scenarios")
                        _sc1, _sc2 = st.columns([2, 1])
                        with _sc1:
                            _sfig = go.Figure()
                            for _case, _tgt, _pct, _clr in [
                                ("Bear", targets["bear"], targets["bear_pct"], "#ff4444"),
                                ("Base", targets["base"], targets["base_pct"], "#ffbb33"),
                                ("Bull", targets["bull"], targets["bull_pct"], "#00C851"),
                            ]:
                                _sfig.add_trace(go.Bar(
                                    x=[_case], y=[_tgt], name=_case, marker_color=_clr,
                                    text=f"${_tgt:.2f}<br>{_pct:+.1f}%", textposition="outside",
                                ))
                            if price:
                                _sfig.add_hline(y=price, line_dash="dash", line_color="white",
                                                annotation_text=f"Now ${price:.2f}",
                                                annotation_position="right")
                            if r["stop"]:
                                _sfig.add_hline(y=r["stop"], line_dash="dot", line_color="#ff4444",
                                                annotation_text=f"Stop ${r['stop']:.2f}",
                                                annotation_position="right")
                            _sfig.update_layout(
                                height=300, template="plotly_dark", showlegend=False,
                                margin=dict(l=0, r=80, t=30, b=0),
                            )
                            st.plotly_chart(_sfig, use_container_width=True)
                        with _sc2:
                            st.markdown("**Scenarios**")
                            for _lbl, _tgt, _pct, _clr in [
                                ("🐂 Bull", targets["bull"], targets["bull_pct"], "#00C851"),
                                ("➡ Base",  targets["base"], targets["base_pct"], "#ffbb33"),
                                ("🐻 Bear",  targets["bear"], targets["bear_pct"], "#ff4444"),
                            ]:
                                st.markdown(
                                    f"{_lbl} `${_tgt:.2f}` <span style='color:{_clr}'>{_pct:+.1f}%</span>",
                                    unsafe_allow_html=True,
                                )

                else:
                    # ── BUY / STRONG BUY — TRADE PLAN ─────────────────────
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Price", f"${price:.2f}" if price else "N/A")
                    c2.metric("Entry Zone",
                              f"${r['entry_lo']:.2f}–${r['entry_hi']:.2f}" if r["entry_lo"] else "N/A")
                    c3.metric("Stop Loss", f"${r['stop']:.2f}" if r["stop"] else "N/A",
                              delta=f"-{(price-r['stop'])/price*100:.1f}%" if price and r["stop"] else None,
                              delta_color="inverse", help=_tip("ATR Stop"))
                    c4.metric("R:R", f"{rr_val:.1f}:1" if rr_val and rr_val > 0 else "N/A",
                              help=_tip("R:R Ratio"))

                    if _sa_holding:
                        # Pre-compute earnings proximity so we can fold it into this message
                        # rather than showing a contradictory standalone warning below.
                        _earn_str = r.get("earnings", "")
                        _earn_note = ""
                        try:
                            if _earn_str:
                                _earn_d = (datetime.strptime(_earn_str, "%Y-%m-%d").date() - date.today()).days
                                if 0 <= _earn_d <= 21:
                                    _earn_note = (
                                        f" Earnings in **{_earn_d} days** ({_earn_str}) — "
                                        "consider reduced add-on size or wait until after the report."
                                    )
                        except Exception:
                            pass
                        st.info(
                            f"**Already held:** {int(_sa_holding.get('Shares', 0))} shares · "
                            f"P&L {_sa_holding.get('P&L (%)', 0):+.1f}%. "
                            f"Sizing below is for adding to your existing position.{_earn_note}"
                        )

                    if ps:
                        st.markdown("#### Position Sizing")
                        p1, p2, p3, p4 = st.columns(4)
                        p1.metric("Shares", f"{ps['shares']:,}", help=_tip("Position Sizing"))
                        p2.metric("Investment", f"${ps['total_cost']:,.0f}",
                                  f"{ps['portfolio_pct']:.1f}% of portfolio",
                                  help=_tip("Position Sizing"))
                        p3.metric("Max Risk", f"${ps['actual_risk']:,.0f}",
                                  f"{ps['risk_pct_actual']:.2f}%", delta_color="inverse",
                                  help="Maximum dollar loss if stop is hit. Should not exceed 1.5–2% of portfolio.")
                        p4.metric("Risk/Share", f"${ps['risk_per_share']:.2f}",
                                  help="Dollar distance from entry to stop per share.")

                        # Beta envelope check — warn when adding this position would push
                        # an already-elevated portfolio beta materially higher
                        _sa_port_risk = st.session_state.get("_port_risk_cache", {})
                        if not _sa_port_risk:
                            st.info(
                                "ℹ️ **Portfolio context unavailable** — visit the Portfolio page first "
                                "to enable beta-envelope checks on this Trade Plan. The size/stop suggestions "
                                "below are based on the stock alone, not on how it fits your existing book."
                            )
                        _sa_port_beta = _sa_port_risk.get("beta")
                        _sa_tick_beta = (r.get("risk_metrics") or {}).get("beta")
                        if _sa_tick_beta and _sa_port_beta:
                            if _sa_tick_beta > TICKER_BETA_HIGH and _sa_port_beta > PORTFOLIO_BETA_ELEVATED:
                                st.warning(
                                    f"⚠ **Beta envelope:** Portfolio beta is already **{_sa_port_beta:.1f}** (elevated). "
                                    f"This stock's beta is **{_sa_tick_beta:.1f}** — a full-size position would "
                                    f"add meaningful market risk. Consider taking **50–60% of the suggested "
                                    f"{ps['shares']:,} shares** to keep portfolio beta in check."
                                )
                            elif _sa_tick_beta > TICKER_BETA_CRITICAL:
                                st.warning(
                                    f"⚠ **High-beta stock:** Beta **{_sa_tick_beta:.1f}** — volatile name. "
                                    f"Use a firm stop at **${r['stop']:.2f}** and consider a starter position "
                                    f"(50–75% of suggested size) before committing fully."
                                )

                    if targets:
                        st.markdown("#### Price Scenarios")
                        _sc1, _sc2 = st.columns([2, 1])
                        with _sc1:
                            _sfig = go.Figure()
                            for _case, _tgt, _pct, _clr in [
                                ("Bear", targets["bear"], targets["bear_pct"], "#ff4444"),
                                ("Base", targets["base"], targets["base_pct"], "#ffbb33"),
                                ("Bull", targets["bull"], targets["bull_pct"], "#00C851"),
                            ]:
                                _sfig.add_trace(go.Bar(
                                    x=[_case], y=[_tgt], name=_case, marker_color=_clr,
                                    text=f"${_tgt:.2f}<br>{_pct:+.1f}%", textposition="outside",
                                ))
                            if price:
                                _sfig.add_hline(y=price, line_dash="dash", line_color="white",
                                                annotation_text=f"Now ${price:.2f}",
                                                annotation_position="right")
                            if r["stop"]:
                                _sfig.add_hline(y=r["stop"], line_dash="dot", line_color="#ff4444",
                                                annotation_text=f"Stop ${r['stop']:.2f}",
                                                annotation_position="right")
                            _sfig.update_layout(
                                height=300, template="plotly_dark", showlegend=False,
                                margin=dict(l=0, r=80, t=30, b=0),
                            )
                            st.plotly_chart(_sfig, use_container_width=True)
                        with _sc2:
                            st.markdown("**Scenarios**")
                            for _lbl, _tgt, _pct, _clr in [
                                ("🐂 Bull", targets["bull"], targets["bull_pct"], "#00C851"),
                                ("➡ Base",  targets["base"], targets["base_pct"], "#ffbb33"),
                                ("🐻 Bear",  targets["bear"], targets["bear_pct"], "#ff4444"),
                            ]:
                                st.markdown(
                                    f"{_lbl} `${_tgt:.2f}` <span style='color:{_clr}'>{_pct:+.1f}%</span>",
                                    unsafe_allow_html=True,
                                )
                            if rr_val and rr_val > 0:
                                quality = "✅ Favourable" if rr_val >= 2.5 else ("⚠️ Marginal" if rr_val >= 1.5 else "❌ Poor")
                                st.markdown(f"**R:R** `{rr_val:.1f}:1` — {quality}")

                # ── Shared: consensus warning + earnings + S&R ─────────────
                if targets and targets.get("above_consensus"):
                    at = targets["analyst_target"]
                    st.warning(
                        f"⚠️ Trading {(price-at)/at*100:.1f}% above analyst consensus (${at:.2f}). "
                        "Targets are technically-derived. Consider smaller entry."
                    )
                earn = r["earnings"]
                if earn:
                    try:
                        days = (datetime.strptime(earn, "%Y-%m-%d").date() - date.today()).days
                        if 0 <= days <= 21:
                            # For held tickers on a Buy signal the earnings note is already
                            # merged into the "Already held" info box above — skip the duplicate.
                            if not _sa_holding:
                                st.warning(
                                    f"⚠️ Earnings in {days} days ({earn}) — "
                                    "consider reduced position size or waiting until after the report."
                                )
                        else:
                            st.info(f"📅 Next earnings: {earn} ({days}d)")
                    except Exception:
                        st.info(f"📅 Next earnings: {earn}")

                with st.expander("Key Support & Resistance"):
                    lc1, lc2 = st.columns(2)
                    with lc1:
                        st.markdown("**Resistance**")
                        for lvl in sr.get("resistances", []):
                            st.markdown(f"🔴 `${lvl:.2f}`")
                    with lc2:
                        st.markdown("**Support**")
                        for lvl in sr.get("supports", []):
                            st.markdown(f"🟢 `${lvl:.2f}`")

                # ── Trade actions ─────────────────────────────────────────
                # One-click path into the Trade Journal with the signal context
                # already filled in. Defaults reflect the composite verdict —
                # the user can still override action / shares / price in the form.
                st.markdown("---")
                _ap_signal_ctx = (
                    f"{rec['label']} · Composite {r['total']:.0f}/100"
                )
                _ap_held_shares = (
                    float(_sa_holding.get("Shares", 0)) if _sa_holding else None
                )
                _ap_followed_buy  = "yes" if "Buy"  in rec["label"] else None
                _ap_followed_sell = "yes" if "Sell" in rec["label"] else None

                _ap_c1, _ap_c2, _ap_c3 = st.columns([1, 1, 2])
                if _sa_is_sell:
                    # Sell signal — primary action is SELL (exit/trim)
                    with _ap_c1:
                        _render_trade_button(
                            ticker=ticker,
                            suggested_action="SELL",
                            signal_context=_ap_signal_ctx,
                            price=price,
                            shares=_ap_held_shares,
                            trigger="RECOMMENDATION",
                            followed_intent=_ap_followed_sell,
                            key_suffix=f"plan_sell_{ticker}",
                            label=f"📒 Sell {ticker}",
                            use_container_width=True,
                        )
                elif _sa_is_hold:
                    # Hold — surface both sides so user picks
                    with _ap_c1:
                        _render_trade_button(
                            ticker=ticker,
                            suggested_action="SELL",
                            signal_context=_ap_signal_ctx,
                            price=price,
                            shares=_ap_held_shares,
                            trigger="MANUAL",
                            key_suffix=f"plan_sell_{ticker}",
                            label=f"📒 Trim {ticker}",
                            use_container_width=True,
                        )
                    with _ap_c2:
                        _render_trade_button(
                            ticker=ticker,
                            suggested_action="BUY",
                            signal_context=_ap_signal_ctx,
                            price=price,
                            shares=(ps["shares"] if ps else None),
                            trigger="MANUAL",
                            key_suffix=f"plan_buy_{ticker}",
                            label=f"📒 Add {ticker}",
                            use_container_width=True,
                        )
                else:
                    # Buy signal — primary action is BUY (new or add)
                    with _ap_c1:
                        _render_trade_button(
                            ticker=ticker,
                            suggested_action="BUY",
                            signal_context=_ap_signal_ctx,
                            price=price,
                            shares=(ps["shares"] if ps else None),
                            trigger="RECOMMENDATION",
                            followed_intent=_ap_followed_buy,
                            key_suffix=f"plan_buy_{ticker}",
                            label=(f"📒 Add {ticker}" if _sa_holding else f"📒 Buy {ticker}"),
                            use_container_width=True,
                        )
                    if _sa_holding:
                        with _ap_c2:
                            _render_trade_button(
                                ticker=ticker,
                                suggested_action="SELL",
                                signal_context=_ap_signal_ctx,
                                price=price,
                                shares=_ap_held_shares,
                                trigger="MANUAL",
                                key_suffix=f"plan_sell_{ticker}",
                                label=f"📒 Sell {ticker}",
                                use_container_width=True,
                            )
                with _ap_c3:
                    st.caption(
                        f"Opens Trade Journal pre-filled with **{rec['label']} · "
                        f"{r['total']:.0f}/100** as the signal context."
                    )

            # ── Chart ─────────────────────────────────────────────────────
            with chart_tab:
                rows_n = 3 if show_volume else 2
                fig = make_subplots(
                    rows=rows_n, cols=1, shared_xaxes=True,
                    vertical_spacing=0.04,
                    row_heights=[0.6, 0.2, 0.2] if show_volume else [0.7, 0.3],
                    subplot_titles=(
                        ["Price + Indicators", "RSI (14)", "Volume"]
                        if show_volume else ["Price + Indicators", "RSI (14)"]
                    ),
                )
                fig.add_trace(go.Candlestick(
                    x=df.index, open=df["Open"], high=df["High"],
                    low=df["Low"], close=df["Close"],
                    increasing_line_color="#00C851", decreasing_line_color="#ff4444",
                    name="Price",
                ), row=1, col=1)
                if "BB_upper" in df.columns:
                    fig.add_trace(go.Scatter(
                        x=df.index, y=df["BB_upper"],
                        line=dict(color="rgba(120,120,255,0.4)", dash="dash", width=1),
                        showlegend=False,
                    ), row=1, col=1)
                    fig.add_trace(go.Scatter(
                        x=df.index, y=df["BB_lower"],
                        fill="tonexty", fillcolor="rgba(120,120,255,0.06)",
                        line=dict(color="rgba(120,120,255,0.4)", dash="dash", width=1),
                        showlegend=False,
                    ), row=1, col=1)
                for col_name, color, name in [
                    ("SMA_20", "orange", "SMA 20"), ("SMA_50", "cyan", "SMA 50")
                ]:
                    if col_name in df.columns:
                        fig.add_trace(go.Scatter(
                            x=df.index, y=df[col_name], name=name,
                            line=dict(color=color, width=1),
                        ), row=1, col=1)
                for lvl in sr.get("resistances", [])[:2]:
                    fig.add_hline(y=lvl, line_dash="dot", line_color="red", line_width=1,
                                  annotation_text=f"R ${lvl:.2f}", annotation_position="right",
                                  row=1, col=1)
                for lvl in sr.get("supports", [])[:2]:
                    fig.add_hline(y=lvl, line_dash="dot", line_color="green", line_width=1,
                                  annotation_text=f"S ${lvl:.2f}", annotation_position="right",
                                  row=1, col=1)
                if r["stop"]:
                    fig.add_hline(y=r["stop"], line_dash="dashdot", line_color="#ff6600",
                                  line_width=1.5, annotation_text=f"Stop ${r['stop']:.2f}",
                                  annotation_position="right", row=1, col=1)
                if "RSI" in df.columns:
                    fig.add_trace(go.Scatter(
                        x=df.index, y=df["RSI"], name="RSI",
                        line=dict(color="purple", width=1.5),
                    ), row=2, col=1)
                    fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
                    fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)
                if show_volume:
                    bar_colors = [
                        "#00C851" if c >= o else "#ff4444"
                        for c, o in zip(df["Close"], df["Open"])
                    ]
                    fig.add_trace(go.Bar(
                        x=df.index, y=df["Volume"], marker_color=bar_colors, showlegend=False,
                    ), row=3, col=1)
                fig.update_layout(
                    height=620, template="plotly_dark",
                    xaxis_rangeslider_visible=False,
                    legend=dict(orientation="h", y=1.02),
                    margin=dict(l=0, r=80, t=30, b=0),
                )
                st.plotly_chart(fig, use_container_width=True)

            # ── Risk ──────────────────────────────────────────────────────
            with risk_tab:
                r1, r2, r3, r4, r5 = st.columns(5)
                r1.metric("Sharpe",       f"{rm['sharpe']:.2f}",  help=_tip("Sharpe Ratio"))
                r2.metric("Sortino",      f"{rm['sortino']:.2f}", help=_tip("Sortino Ratio"))
                r3.metric("Max Drawdown", f"{rm['max_drawdown']:.1f}%", help=_tip("Max Drawdown"))
                r4.metric("VaR (95%)",    f"{rm['var_95']:.2f}%", help=_tip("VaR"))
                r5.metric("Beta vs S&P",  f"{rm['beta']:.2f}" if rm["beta"] else "N/A",
                          help=_tip("Beta"))

                def _norm(key, val):
                    if val is None: return 0.5
                    if key == "sharpe":  return min(max((val + 0.5) / 2.5, 0), 1)
                    if key == "sortino": return min(max((val + 0.5) / 3.5, 0), 1)
                    if key == "mdd":     return min(max((val + 50) / 50, 0), 1)
                    if key == "var":     return min(max((val + 6) / 6, 0), 1)
                    if key == "beta":    return min(max(1 - abs(val - 1.0) / 1.5, 0), 1)
                    return 0.5

                vals = [
                    _norm("sharpe", rm["sharpe"]), _norm("sortino", rm["sortino"]),
                    _norm("mdd", rm["max_drawdown"]), _norm("var", rm["var_95"]),
                    _norm("beta", rm["beta"]),
                ]
                cats = ["Sharpe", "Sortino", "DD Control", "Low VaR", "Beta Align"]
                rc1, rc2 = st.columns(2)
                with rc1:
                    with st.expander("Return Distribution"):
                        rets = df["Close"].pct_change().dropna() * 100
                        hfig = go.Figure(go.Histogram(
                            x=rets, nbinsx=40, marker_color="#4a9eff", opacity=0.75,
                        ))
                        if rm["var_95"]:
                            hfig.add_vline(x=rm["var_95"], line_dash="dash", line_color="red",
                                           annotation_text=f"VaR {rm['var_95']:.2f}%")
                        hfig.update_layout(
                            height=260, template="plotly_dark",
                            xaxis_title="Daily Return (%)",
                            margin=dict(l=0, r=0, t=10, b=0),
                        )
                        st.plotly_chart(hfig, use_container_width=True)
                with rc2:
                    vals_closed = vals + [vals[0]]
                    cats_closed = cats + [cats[0]]
                    rfig = go.Figure(go.Scatterpolar(
                        r=vals_closed, theta=cats_closed, fill="toself",
                        fillcolor="rgba(0,200,81,0.2)",
                        line=dict(color="#00C851", width=2),
                    ))
                    rfig.update_layout(
                        polar=dict(radialaxis=dict(range=[0, 1], tickvals=[0.25, 0.5, 0.75])),
                        template="plotly_dark", height=260, showlegend=False,
                        margin=dict(l=30, r=30, t=30, b=30),
                        title="Risk Radar",
                    )
                    st.plotly_chart(rfig, use_container_width=True)

            # ── Deep Dive ─────────────────────────────────────────────────
            with deep_tab:
                dd1, dd2, dd3, dd4 = st.columns(4)
                with dd1:
                    st.markdown(f"**Technical — {r['t_score']}/100**")
                    st.caption("RSI · MACD · MA · Bollinger · Volume")
                    for k, v in r["t_signals"].items():
                        clr = "#00C851" if "bullish" in v.lower() else (
                              "#ff4444" if "bearish" in v.lower() else "#aaa")
                        tip_key = {"RSI": "RSI", "MACD": "MACD"}.get(k, "")
                        label_md = (
                            f"<abbr title='{_tip(tip_key).split(chr(10))[0]}' "
                            f"style='cursor:help;border-bottom:1px dotted #666'><b>{k}</b></abbr>"
                            if tip_key else f"<b>{k}</b>"
                        )
                        st.markdown(
                            f"<small style='color:{clr}'>●</small> {label_md}: "
                            f"<span style='color:#ccc'>{v}</span>",
                            unsafe_allow_html=True,
                        )
                with dd2:
                    st.markdown(f"**Fundamental — {r['f_score']}/100**")
                    st.caption("Valuation · Growth · Quality · Cash Flow")
                    for k, v in r["f_signals"].items():
                        clr = "#00C851" if any(w in v.lower() for w in
                              ["strong","excellent","good","healthy","under"]) else (
                              "#ff4444" if any(w in v.lower() for w in
                              ["declin","contract","high lev","expensive","loss","burn"]) else "#aaa")
                        tip_map = {
                            "Forward P/E": "Forward P/E", "FCF Yield": "FCF Yield",
                            "Revenue Growth": "Revenue Growth", "Earnings Growth": "Earnings Growth",
                            "Profit Margin": "Profit Margin", "Debt/Equity": "Debt/Equity",
                        }
                        tip_key = tip_map.get(k, "")
                        label_md = (
                            f"<abbr title='{_tip(tip_key).split(chr(10))[0]}' "
                            f"style='cursor:help;border-bottom:1px dotted #666'><b>{k}</b></abbr>"
                            if tip_key else f"<b>{k}</b>"
                        )
                        st.markdown(
                            f"<small style='color:{clr}'>●</small> {label_md}: "
                            f"<span style='color:#ccc'>{v}</span>",
                            unsafe_allow_html=True,
                        )
                    fin = r["financials"]
                    st.markdown("---")
                    raw_metrics = [
                        ("Trailing P/E", "pe_ratio",    _tip("P/E Ratio")),
                        ("Forward P/E",  "forward_pe",  _tip("Forward P/E")),
                        ("FCF Yield",    "fcf_yield",   _tip("FCF Yield")),
                        ("EPS (TTM)",    "eps",         _tip("EPS")),
                        ("Current Ratio","current_ratio", "Current assets ÷ current liabilities. >1.5 = healthy liquidity."),
                        ("ROE",          "return_on_equity", _tip("ROE")),
                    ]
                    for label, key, tip_txt in raw_metrics:
                        v = fin.get(key)
                        if v is None:
                            continue
                        suffix = "%" if key == "fcf_yield" else ""
                        fmt = f"{v:.1f}{suffix}" if key == "fcf_yield" else f"{v:.2f}"
                        st.markdown(
                            f"<small><abbr title='{tip_txt.split(chr(10))[0]}' "
                            f"style='cursor:help;border-bottom:1px dotted #555'>**{label}**</abbr>: "
                            f"{fmt}</small>",
                            unsafe_allow_html=True,
                        )
                with dd3:
                    st.markdown(f"**Sentiment — {r['s_score']:.0f}/100**")
                    st.caption("VADER · Yahoo Finance news · −1 bearish → +1 bullish")
                    for h in r["headlines"][:6]:
                        clr = "#00b300" if h["label"] == "Positive" else (
                              "#ff4444" if h["label"] == "Negative" else "#888")
                        lbl = "▲" if h["label"] == "Positive" else (
                              "▼" if h["label"] == "Negative" else "–")
                        headline_text = h["headline"][:75] + ("…" if len(h["headline"]) > 75 else "")
                        url = h.get("url", "")
                        linked = (
                            f"<a href='{url}' target='_blank' "
                            f"style='color:#ccc;text-decoration:none'>{headline_text}</a>"
                            if url else headline_text
                        )
                        st.markdown(
                            f"<small style='color:{clr}'>{lbl} {h['score']:+.2f}</small> "
                            f"<small>{linked}</small>",
                            unsafe_allow_html=True,
                        )
                with dd4:
                    st.markdown("**Smart Money Signals**")
                    st.caption("Ownership · Shorts · Analyst revisions")
                    fin = r["financials"]
                    rev = r.get("revisions", {})

                    def _sm_row(label, value, color, tip_text):
                        st.markdown(
                            f"<div style='margin-bottom:6px;padding:5px 8px;"
                            f"background:#161616;border-radius:5px;"
                            f"border-left:3px solid {color}'>"
                            f"<span style='font-size:0.72em;color:#666'>"
                            f"<abbr title='{tip_text.split(chr(10))[0]}' "
                            f"style='cursor:help;border-bottom:1px dotted #555'>{label}</abbr>"
                            f"</span><br>"
                            f"<span style='font-size:0.95em;font-weight:bold;color:{color}'>"
                            f"{value}</span></div>",
                            unsafe_allow_html=True,
                        )

                    short_pct = fin.get("short_pct_float")
                    if short_pct is not None:
                        clr = "#ff4444" if short_pct > 15 else ("#ffbb33" if short_pct > 7 else "#00C851")
                        badge = " 🔥" if short_pct > 15 else ""
                        _sm_row("Short Interest % Float", f"{short_pct:.1f}%{badge}", clr, _tip("Short Interest"))

                    short_ratio = fin.get("short_ratio")
                    if short_ratio:
                        clr = "#ff4444" if short_ratio > 7 else ("#ffbb33" if short_ratio > 3 else "#00C851")
                        _sm_row("Days to Cover", f"{short_ratio:.1f} days", clr, _tip("Days to Cover"))

                    inst = fin.get("held_pct_institutions")
                    if inst is not None:
                        clr = "#00C851" if inst > 60 else ("#ffbb33" if inst > 30 else "#aaa")
                        _sm_row("Institutional Ownership", f"{inst:.0f}%", clr, _tip("Institutional Ownership"))

                    insider = fin.get("held_pct_insiders")
                    if insider is not None:
                        clr = "#00C851" if insider > 10 else "#aaa"
                        _sm_row("Insider Ownership", f"{insider:.1f}%", clr, _tip("Insider Ownership"))

                    fcf_y = fin.get("fcf_yield")
                    if fcf_y is not None:
                        clr = "#00C851" if fcf_y >= 4 else ("#ffbb33" if fcf_y >= 1 else "#ff4444")
                        _sm_row("FCF Yield", f"{fcf_y:.1f}%", clr, _tip("FCF Yield"))

                    n_analysts = fin.get("num_analyst_opinions")
                    cons = fin.get("recommendation")
                    if cons is not None:
                        cons_label = (
                            "Strong Buy" if cons <= 1.5 else
                            "Buy"        if cons <= 2.5 else
                            "Hold"       if cons <= 3.5 else
                            "Sell"       if cons <= 4.5 else "Strong Sell"
                        )
                        cons_clr = (
                            "#00C851" if cons <= 2.0 else
                            "#00b300" if cons <= 2.5 else
                            "#ffbb33" if cons <= 3.5 else "#ff4444"
                        )
                        n_str = f" ({n_analysts} analysts)" if n_analysts else ""
                        _sm_row(f"Analyst Consensus{n_str}", f"{cons_label} ({cons:.1f})", cons_clr, _tip("Analyst Consensus"))

                    if rev:
                        net = rev.get("net", 0)
                        ups = rev.get("upgrades_90d", 0)
                        dns = rev.get("downgrades_90d", 0)
                        rev_clr = "#00C851" if net > 0 else ("#ff4444" if net < 0 else "#888")
                        rev_lbl = f"↑{ups} upgrades / ↓{dns} downgrades (90d)"
                        _sm_row("Analyst Revisions", rev_lbl, rev_clr, _tip("Analyst Revisions"))
                        if rev.get("latest"):
                            st.markdown("<small style='color:#555'>Recent actions:</small>", unsafe_allow_html=True)
                            for action in rev["latest"][:3]:
                                act_lbl = action.get("action", "").lower()
                                act_clr = ("#00C851" if act_lbl in ["up", "init"] else
                                           "#ff4444" if act_lbl == "down" else "#888")
                                from_g = action.get("from_grade", "")
                                to_g = action.get("to_grade", "")
                                arrow = f" {from_g} → {to_g}" if from_g and to_g else (f" → {to_g}" if to_g else "")
                                st.markdown(
                                    f"<small style='color:{act_clr}'>● {action.get('firm','')}"
                                    f"{arrow} ({act_lbl})</small>",
                                    unsafe_allow_html=True,
                                )
                    else:
                        st.caption("Revision data not available for this ticker.")

    # Correlation matrix
    if len(results) >= 2:
        with st.expander("📐 Correlation Matrix"):
            close_df = pd.DataFrame(
                {t: r["df"]["Close"] for t, r in results.items()}
            ).dropna()
            corr = close_df.pct_change().dropna().corr()
            hfig = go.Figure(go.Heatmap(
                z=corr.values, x=corr.columns.tolist(), y=corr.index.tolist(),
                colorscale="RdYlGn", zmin=-1, zmax=1,
                text=[[f"{v:.2f}" for v in row] for row in corr.values],
                texttemplate="%{text}",
            ))
            hfig.update_layout(
                height=350, template="plotly_dark",
                title="Return Correlation (>0.8 = concentrated risk)",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(hfig, use_container_width=True)

    # Analysis summary
    with st.expander("📋 Analysis Summary"):
        today_str = date.today().strftime("%B %d, %Y")
        lines = [f"# Investment Brief — {today_str}",
                 f"Portfolio: ${portfolio_value:,.0f} · Moderate Risk\n\n---"]
        for ticker, r in results.items():
            price = r["current_price"]
            targets = r["targets"]
            ps = (position_sizing(portfolio_value, MODERATE_RISK_PCT, price, r["stop"])
                  if price and r["stop"] and price > r["stop"] else None)
            rr_v = risk_reward(price, r["stop"], targets["base"]) if price and r["stop"] and targets else None
            rm = r["risk_metrics"]
            lines += [
                f"### {ticker} — {r['rec']['icon']} {r['rec']['label']} ({r['total']}/100)",
                f"**Price**: ${price:.2f}" if price else "",
                (f"**Trade**: Buy {ps['shares']} @ ${r['entry_lo']:.2f}–${r['entry_hi']:.2f} · "
                 f"Stop ${r['stop']:.2f} · Target ${targets['base']:.2f} · R:R {rr_v:.1f}:1"
                 if ps and rr_v and rr_v > 0 else ""),
                (f"**Scenarios**: Bull ${targets['bull']:.2f} ({targets['bull_pct']:+.1f}%) · "
                 f"Base ${targets['base']:.2f} ({targets['base_pct']:+.1f}%) · "
                 f"Bear ${targets['bear']:.2f} ({targets['bear_pct']:+.1f}%)" if targets else ""),
                (f"**Risk**: Sharpe {rm['sharpe']:.2f} · Sortino {rm['sortino']:.2f} · "
                 f"Max DD {rm['max_drawdown']:.1f}% · Beta {rm['beta']:.2f}"
                 if rm.get("beta") else
                 f"**Risk**: Sharpe {rm['sharpe']:.2f} · Max DD {rm['max_drawdown']:.1f}%"),
                f"**Rationale**: {r['rec']['rationale']}", "",
            ]
        brief = "\n".join(lines)
        st.markdown(brief)
        st.download_button(
            "⬇️ Download Brief", data=brief,
            file_name=f"brief_{date.today()}.md", mime="text/markdown",
        )

# ═════════════════════════════════════════════════════════════════════════════
# PAGE 4 — WATCHLIST ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
elif page == "📋 Watchlist":
    _fill_news_slot(_news_slot, st.session_state.get("_sidebar_news", []))
    st.title("📋 Watchlist Analysis")
    st.caption(
        "Your opportunity pipeline. Each stock on the watchlist is analysed for buy readiness — "
        "when to open, when to wait, and when the thesis has broken. "
        "A Institutional PM doesn't just track watchlist tickers; they actively manage them."
    )

    # ── Sidebar: manage watchlist ─────────────────────────────────────────────
    with st.sidebar:
        st.divider()
        st.markdown("**Manage Watchlist**")
        # Use a form so the input clears on submit (text_input outside a form
        # keeps its value across reruns, which made the previous version
        # silently no-op every keystroke after the first add — confusing UX).
        with st.form("_wl_add_form", clear_on_submit=True):
            _wl_add = st.text_input("Add ticker", "",
                                    placeholder="e.g. NVDA")
            _wl_submit = st.form_submit_button("Add")
        if _wl_submit:
            _t = (_wl_add or "").strip().upper()
            if not _t:
                st.warning("Enter a ticker symbol to add.")
            elif _t in st.session_state.watchlist:
                st.warning(f"**{_t}** is already on the watchlist.")
            else:
                # Verify the ticker resolves to a real price before adding —
                # otherwise typos sit in the watchlist forever and cause silent
                # load failures on every downstream page that iterates over it.
                _wl_verify_px = None
                try:
                    _wl_px = fetch_live_prices([_t])
                    if _t in _wl_px:
                        _wl_verify_px = float(_wl_px[_t].get("price") or 0)
                except Exception:
                    _wl_verify_px = None
                if not _wl_verify_px or _wl_verify_px <= 0:
                    st.warning(
                        f"Couldn't resolve a market price for **{_t}** — verify the "
                        "spelling. If you're sure the ticker is correct (e.g. a "
                        "newly listed name), retry in a few minutes."
                    )
                else:
                    st.session_state.watchlist.append(_t)
                    db.save_watchlist(st.session_state.watchlist)
                    st.success(f"Added **{_t}** (last price ${_wl_verify_px:,.2f})")
                    st.rerun()
        if st.session_state.watchlist:
            _wl_remove = st.multiselect(
                "Remove from watchlist",
                options=st.session_state.watchlist,
                key="_wl_remove_sel",
            )
            if _wl_remove and st.button("🗑️ Remove selected", key="_wl_remove_btn"):
                for _rt in _wl_remove:
                    if _rt in st.session_state.watchlist:
                        st.session_state.watchlist.remove(_rt)
                db.save_watchlist(st.session_state.watchlist)
                st.success(f"Removed {len(_wl_remove)} ticker(s).")
                st.rerun()

    _wl = st.session_state.watchlist
    if not _wl:
        st.info(
            "Your watchlist is empty. "
            "Add tickers using the sidebar, or use the Rankings tab on the Portfolio page "
            "to scan the universe and add candidates."
        )
        st.stop()

    # Held tickers — used to flag "already in portfolio" on ENTER_NOW cards
    _wl_held: set = set(
        st.session_state.get("holdings_df", pd.DataFrame()).get("Ticker", pd.Series()).tolist()
    )

    # ── Load data for all watchlist tickers ───────────────────────────────────
    _wl_data: dict = {}
    with st.spinner(f"Loading analysis for {len(_wl)} watchlist ticker(s)…"):
        for _wt in _wl:
            try:
                _wl_data[_wt] = load_all(_wt, "6mo")
            except Exception as _we:
                _wl_data[_wt] = None

    # ── Build portfolio-fit context (consumed by ENTER_NOW risk gate) ─────────
    _wl_port_df    = st.session_state.get("holdings_df", pd.DataFrame())
    _wl_port_risk  = st.session_state.get("_port_risk_cache", {}) or {}
    _wl_high_alerts = st.session_state.get("_risk_high_alerts_cache", []) or []
    _wl_grow_sectors_raw = st.session_state.get("_grow_today_sectors_cache")
    _wl_brief_offline = (
        st.session_state.get("_daily_brief_offline", False)
        or _wl_grow_sectors_raw is None
    )
    _wl_grow_sectors = set(_wl_grow_sectors_raw or [])
    _wl_port_beta  = _wl_port_risk.get("beta")

    if _wl_brief_offline:
        st.warning(
            "⚠ **Daily Briefing offline** — sector-overlap and active-risk-alert gates "
            "on ENTER_NOW recommendations cannot run. The Watchlist will still show "
            "stock-level signals, but coordination with Grow Today / Risk Advisor is "
            "currently disabled. Visit the Portfolio page first to rebuild the briefing."
        )

    # ── Build recommendations ─────────────────────────────────────────────────
    _wl_recs: list[dict] = []
    for _wt, _wd in _wl_data.items():
        if _wd is None:
            continue
        # Per-ticker portfolio fit: this ticker's sector weight in the book
        _wl_sector = str(_wd.get("sector", "")) if isinstance(_wd, dict) else ""
        _wl_sec_wt = 0.0
        if _wl_sector and not _wl_port_df.empty and "Sector" in _wl_port_df.columns:
            _wl_sec_wt = float(_wl_port_df[_wl_port_df["Sector"] == _wl_sector]["Weight (%)"].sum())
        _wl_pctx = {
            "sector_of_ticker":        _wl_sector,
            "sector_weight_pct":       _wl_sec_wt,
            "portfolio_beta":          _wl_port_beta,
            "active_high_risk_alerts": _wl_high_alerts,
            "grow_today_sectors":      _wl_grow_sectors,
        }
        try:
            _wl_recs.append(build_watchlist_recommendation(_wt, _wd, portfolio_ctx=_wl_pctx))
        except Exception:
            pass

    if not _wl_recs:
        st.warning("Could not generate analysis for any watchlist ticker. Check your connection.")
        st.stop()

    # Sort: REMOVE (HIGH) → HOLD_OFF_EARNINGS (MEDIUM) → ENTER_NOW → NEAR_ENTRY → WAIT_ENTRY → WAIT_CATALYST
    _wl_sort = {"REMOVE": 0, "HOLD_OFF_EARNINGS": 1, "ENTER_NOW": 2,
                "NEAR_ENTRY": 3, "WAIT_ENTRY": 4, "WAIT_CATALYST": 5}
    _wl_recs.sort(key=lambda x: _wl_sort.get(x["action"], 6))

    # ── KPI summary strip ─────────────────────────────────────────────────────
    _wl_enter  = sum(1 for r in _wl_recs if r["action"] == "ENTER_NOW")
    _wl_near   = sum(1 for r in _wl_recs if r["action"] == "NEAR_ENTRY")
    _wl_remove = sum(1 for r in _wl_recs if r["action"] == "REMOVE")
    _wl_k1, _wl_k2, _wl_k3, _wl_k4 = st.columns(4)
    _wl_k1.metric("Watchlist size", len(_wl_recs))
    _wl_k2.metric("✅ Enter Now",    _wl_enter,
                  delta="Actionable" if _wl_enter else None,
                  delta_color="normal" if _wl_enter else "off")
    _wl_k3.metric("🟡 Near Entry",   _wl_near)
    _wl_k4.metric("🔴 Remove",       _wl_remove,
                  delta="Thesis broken" if _wl_remove else None,
                  delta_color="inverse" if _wl_remove else "off")

    st.markdown("")

    # ── Per-ticker cards ──────────────────────────────────────────────────────
    for _wr in _wl_recs:
        _action   = _wr["action"]
        _priority = _wr["priority"]
        _ticker   = _wr["ticker"]

        # Override ENTER_NOW if ticker is already in the portfolio — buying again would
        # blindly add to an existing position without checking current size or signal.
        _already_held = _ticker in _wl_held
        if _action == "ENTER_NOW" and _already_held:
            st.warning(
                f"**{_ticker} — Already in Portfolio** · "
                "This watchlist recommendation says ENTER NOW, but you already hold this position. "
                "Check Portfolio → Today's Brief for the current add/hold/sell signal instead.",
                icon="⚠️",
            )
            continue

        _a_icon   = {
            "ENTER_NOW":         "✅",
            "NEAR_ENTRY":        "🎯",
            "WAIT_ENTRY":        "⏳",
            "WAIT_CATALYST":     "👁️",
            "HOLD_OFF_EARNINGS": "⚠️",
            "REMOVE":            "🔴",
        }.get(_action, "📌")
        _bclr = {
            "HIGH":    "#ff4444",
            "MEDIUM":  "#ffbb33",
            "OK":      "#00C851",
            "MONITOR": "#4a9eff",
        }.get(_priority, "#888")
        _expand = _action in ("ENTER_NOW", "REMOVE", "HOLD_OFF_EARNINGS")

        _price    = _wr["price"]
        _entry_lo = _wr["entry_lo"]
        _entry_hi = _wr["entry_hi"]
        _stop     = _wr["stop"]
        _rr       = _wr["rr"]
        _earn_d   = _wr["earn_days"]

        # Position sizing for this watchlist candidate
        _pv_now = st.session_state.get("_portfolio_value") or 50_000
        _wl_ps = None
        if _price and _stop and _price > _stop and _pv_now > 0:
            try:
                _wl_ps = position_sizing(_pv_now, MODERATE_RISK_PCT, _price, _stop)
            except Exception:
                pass

        with st.expander(
            f"{_a_icon} **{_action.replace('_', ' ')}** · {_ticker}  "
            f"| Score {_wr['score']:.0f}/100 · {_wr['signal']}  "
            f"| Readiness {_wr['readiness_pct']}%",
            expanded=_expand,
        ):
            # Metrics strip
            _wm = st.columns(5)
            _wm[0].metric("Price",      f"${_price:.2f}" if _price else "—")
            _wm[1].metric(
                "Entry Zone",
                f"${_entry_lo:.2f}–${_entry_hi:.2f}" if _entry_lo else "—",
            )
            _wm[2].metric("ATR Stop",   f"${_stop:.2f}" if _stop else "—",
                          delta=f"-{(_price - _stop) / _price * 100:.1f}% gap"
                          if _price and _stop else None,
                          delta_color="off")
            _wm[3].metric("R:R",
                          f"{_rr:.1f}:1" if _rr else "—",
                          delta="≥2:1 ✓" if _rr and _rr >= 2.0 else ("< 2:1" if _rr else None),
                          delta_color="normal" if (_rr and _rr >= 2.0) else "inverse")
            _wm[4].metric("Earnings",
                          f"{_earn_d}d" if _earn_d is not None and _earn_d >= 0 else "—",
                          delta="⚠️ Imminent" if _earn_d is not None and 0 <= _earn_d <= 7 else None,
                          delta_color="inverse" if (_earn_d is not None and 0 <= _earn_d <= 7) else "off")

            # Summary banner
            st.markdown(
                f"<div style='padding:10px 14px;background:#1a1a1a;"
                f"border-radius:6px;border-left:4px solid {_bclr};margin:10px 0'>"
                f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                f"letter-spacing:0.09em;text-transform:uppercase'>Buy Readiness Assessment</span><br>"
                f"<span style='color:#eee'>{_wr['summary']}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Portfolio fit caution — shown when ENTER_NOW (or downgraded NEAR_ENTRY)
            # was issued but portfolio-level risk state warrants caution / blocking.
            _pcaution = _wr.get("portfolio_caution")
            if _pcaution:
                _is_hard = _action == "NEAR_ENTRY" and "Portfolio Fit" in _wr.get("title", "")
                _pc_bg   = "#3f1d1d" if _is_hard else "#3b2a0a"
                _pc_brd  = "#ef4444" if _is_hard else "#f59e0b"
                _pc_lbl  = (
                    "🚫 Portfolio Fit — Hard Limit Breached"
                    if _is_hard else
                    "⚠️ Portfolio Fit — Caution"
                )
                _pc_txt_c = "#fca5a5" if _is_hard else "#fcd34d"
                st.markdown(
                    f"<div style='padding:10px 14px;background:{_pc_bg};"
                    f"border-radius:6px;border-left:4px solid {_pc_brd};margin:6px 0 10px'>"
                    f"<span style='font-size:0.72em;color:{_pc_brd};font-weight:700;"
                    f"letter-spacing:0.09em;text-transform:uppercase'>{_pc_lbl}</span><br>"
                    f"<span style='color:{_pc_txt_c};font-size:0.88em'>{_pcaution}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # Conditions + Position Sizing
            _wl_cl, _wl_cr = st.columns([1, 1])

            with _wl_cl:
                if _wr["conditions_met"]:
                    st.markdown("**✅ Conditions met**")
                    for _cm in _wr["conditions_met"]:
                        st.markdown(
                            f"<div style='font-size:0.85em;color:#00C851;margin-left:6px'>"
                            f"✓ {_cm}</div>",
                            unsafe_allow_html=True,
                        )
                if _wr["conditions_missing"]:
                    st.markdown("**⏳ Conditions pending**")
                    for _cp in _wr["conditions_missing"]:
                        st.markdown(
                            f"<div style='font-size:0.85em;color:#ffbb33;margin-left:6px'>"
                            f"○ {_cp}</div>",
                            unsafe_allow_html=True,
                        )

            with _wl_cr:
                # Position sizing panel
                if _wl_ps:
                    st.markdown(
                        f"<div style='padding:10px 14px;background:#0d2137;"
                        f"border-radius:6px;border-left:4px solid #4a9eff'>"
                        f"<span style='font-size:0.72em;color:#4a9eff;font-weight:700;"
                        f"letter-spacing:0.09em;text-transform:uppercase'>Position Sizing</span><br>"
                        f"<span style='color:#eee;font-size:0.88em'>"
                        f"<b>{_wl_ps['shares']:,} shares</b> @ ${_price:.2f} = "
                        f"<b>${_wl_ps['total_cost']:,.0f}</b> "
                        f"({_wl_ps['portfolio_pct']:.1f}% of portfolio)<br>"
                        f"Risk: ${_wl_ps['actual_risk']:,.0f} "
                        f"({_wl_ps['risk_pct_actual']:.2f}% of portfolio) "
                        f"at stop ${_stop:.2f}"
                        f"</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                elif _price:
                    st.caption("Position sizing unavailable — stop price too close to entry or not set.")

            # Detail recommendation
            st.markdown(
                f"<div style='padding:12px 16px;background:#0d1117;"
                f"border-radius:6px;border-left:4px solid {_bclr};margin:10px 0'>"
                f"<span style='font-size:0.72em;color:{_bclr};font-weight:700;"
                f"letter-spacing:0.09em;text-transform:uppercase'>"
                f"{_a_icon} Action: {_action.replace('_', ' ')}</span><br>"
                f"<span style='color:#eee;font-size:0.9em'>{_wr['detail']}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Institutional Lens
            if _wr.get("institutional_lens"):
                st.markdown("")
                st.info(f"**Institutional Lens** · {_wr['institutional_lens']}")

            # Quick action: add to Trade Journal as planned trade
            st.markdown("")
            _qa_col1, _qa_col2 = st.columns([1, 3])
            with _qa_col1:
                if _action == "ENTER_NOW":
                    # Use the shared trade-button helper so the Decision Context
                    # block (signal_seen + followed_signal) is auto-populated
                    # consistent with the same flow on Position Details / Analysis.
                    _wl_signal_ctx = (
                        f"{_wr['signal']} · Score {_wr['score']:.0f}/100"
                        + (f" · Readiness {_wr['readiness_pct']}%" if _wr.get("readiness_pct") is not None else "")
                    )
                    _wl_entry_price = (
                        round(_entry_hi, 2) if _entry_hi else (round(_price, 2) if _price else None)
                    )
                    _render_trade_button(
                        ticker=_ticker,
                        suggested_action="BUY",
                        signal_context=_wl_signal_ctx,
                        price=_wl_entry_price,
                        shares=(_wl_ps["shares"] if _wl_ps else None),
                        trigger="WATCHLIST_ENTRY",
                        followed_intent="yes",
                        notes=f"Watchlist entry — score {_wr['score']:.0f}/100",
                        key_suffix=f"wl_{_ticker}",
                        label=f"📒 Log buy for {_ticker}",
                    )
            with _qa_col2:
                if _action == "REMOVE" and st.button(
                    "🗑️ Remove from watchlist", key=f"_wl_del_{_ticker}"
                ):
                    if _ticker in st.session_state.watchlist:
                        st.session_state.watchlist.remove(_ticker)
                        db.save_watchlist(st.session_state.watchlist)
                        st.success(f"{_ticker} removed.")
                        st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# PAGE — COMPARE (2-ticker side-by-side)
# ═════════════════════════════════════════════════════════════════════════════
elif page == "⚖️ Compare":
    _fill_news_slot(_news_slot, st.session_state.get("_sidebar_news", []))
    st.title("⚖️ Compare")
    st.caption(
        "Side-by-side comparison of two tickers. Useful for deciding between "
        "similar candidates (e.g. OKTA vs CRWD for cybersecurity, or two "
        "watchlist names competing for the same allocation slot)."
    )

    # ── Defaults: try watchlist first two, else session memory, else blank ──
    _cmp_wl = list(st.session_state.get("watchlist", []) or [])
    _cmp_default_a = st.session_state.get("_cmp_last_a", _cmp_wl[0] if len(_cmp_wl) >= 1 else "")
    _cmp_default_b = st.session_state.get("_cmp_last_b", _cmp_wl[1] if len(_cmp_wl) >= 2 else "")

    _cmp_c1, _cmp_c2, _cmp_c3 = st.columns([2, 2, 1])
    with _cmp_c1:
        _cmp_a_in = st.text_input(
            "Ticker A", value=_cmp_default_a, key="_cmp_a_input",
            placeholder="e.g. OKTA",
        ).strip().upper()
    with _cmp_c2:
        _cmp_b_in = st.text_input(
            "Ticker B", value=_cmp_default_b, key="_cmp_b_input",
            placeholder="e.g. CRWD",
        ).strip().upper()
    with _cmp_c3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        _cmp_go = st.button("⚖️ Compare", type="primary", use_container_width=True)

    # Quick-pick row from watchlist + holdings
    _cmp_qp_options = []
    _cmp_wl_clean   = [t for t in _cmp_wl if t]
    if len(_cmp_wl_clean) >= 2:
        _cmp_qp_options.append(("Watchlist top 2", _cmp_wl_clean[0], _cmp_wl_clean[1]))
    _cmp_hold_tickers = list(st.session_state.get("holdings_df", pd.DataFrame()).get("Ticker", []))
    if len(_cmp_hold_tickers) >= 2:
        _cmp_qp_options.append(("Portfolio top 2 by weight", _cmp_hold_tickers[0], _cmp_hold_tickers[1]))

    if _cmp_qp_options:
        _qp_cols = st.columns(len(_cmp_qp_options) + 1)
        for _qpi, (_lbl, _qpa, _qpb) in enumerate(_cmp_qp_options):
            with _qp_cols[_qpi]:
                if st.button(f"🎯 {_lbl}: {_qpa} vs {_qpb}", key=f"_cmp_qp_{_qpi}",
                             use_container_width=True):
                    st.session_state["_cmp_a_input"] = _qpa
                    st.session_state["_cmp_b_input"] = _qpb
                    st.session_state["_cmp_last_a"] = _qpa
                    st.session_state["_cmp_last_b"] = _qpb
                    st.session_state["_cmp_auto_run"] = True
                    st.rerun()

    # Auto-run when triggered by a quick-pick or by direct button press
    _cmp_should_run = _cmp_go or st.session_state.pop("_cmp_auto_run", False)

    # ── Compute (and cache) the comparison when the user requests it ────────
    # The result MUST be cached in session_state so it survives reruns from
    # downstream buttons (Deep Dive, etc.). Without this, clicking any button
    # in the rendered comparison would re-enter the page with _cmp_should_run
    # = False and the whole result block would disappear — including the
    # button the user just clicked, which silently swallows the click.
    if _cmp_should_run and _cmp_a_in and _cmp_b_in:
        if _cmp_a_in == _cmp_b_in:
            st.warning("Pick two different tickers to compare.")
            st.stop()

        st.session_state["_cmp_last_a"] = _cmp_a_in
        st.session_state["_cmp_last_b"] = _cmp_b_in

        with st.spinner(f"Loading {_cmp_a_in} and {_cmp_b_in}…"):
            _cmp_bundle_a = None
            _cmp_bundle_b = None
            try:
                _cmp_bundle_a = load_all(_cmp_a_in)
            except Exception as _ea:
                st.error(f"Could not load {_cmp_a_in}: {_ea}")
            try:
                _cmp_bundle_b = load_all(_cmp_b_in)
            except Exception as _eb:
                st.error(f"Could not load {_cmp_b_in}: {_eb}")

        if _cmp_bundle_a and _cmp_bundle_b:
            _cmp_port_df = st.session_state.get("_port_df_enriched", pd.DataFrame())
            _cmp_result = build_comparison(
                bundle_a = _cmp_bundle_a,
                bundle_b = _cmp_bundle_b,
                ticker_a = _cmp_a_in,
                ticker_b = _cmp_b_in,
                port_df  = _cmp_port_df,
            )
            st.session_state["_cmp_result_cache"] = {
                "key":  (_cmp_a_in, _cmp_b_in),
                "data": _cmp_result,
            }

    # ── Render from the cached result whenever inputs match ─────────────────
    # Survives reruns triggered by Deep Dive (or any other) buttons.
    _cmp_cached = st.session_state.get("_cmp_result_cache")
    if (
        _cmp_a_in and _cmp_b_in
        and _cmp_cached
        and _cmp_cached.get("key") == (_cmp_a_in, _cmp_b_in)
    ):
        _cmp_result = _cmp_cached["data"]
        if True:
            # ── Verdict block ──────────────────────────────────────────────
            _v = _cmp_result["verdict"]
            _v_preferred = _v["preferred"]
            _v_color = {
                "a":   "#22c55e",
                "b":   "#22c55e",
                "tie": "#f59e0b",
            }.get(_v_preferred, "#6b7280")
            _v_icon = "🏆" if _v_preferred in ("a", "b") else "⚖️"
            _v_conf_label = {"high": "High confidence", "medium": "Medium confidence",
                             "low":  "Low confidence — close call"}.get(_v["confidence"], "")

            st.markdown(
                f"<div style='background:#0f172a;border:1px solid {_v_color};"
                f"border-left:5px solid {_v_color};border-radius:8px;"
                f"padding:14px 18px;margin:14px 0'>"
                f"<div style='display:flex;align-items:baseline;gap:14px;flex-wrap:wrap'>"
                f"<span style='color:{_v_color};font-weight:800;font-size:1.1em'>"
                f"{_v_icon} Verdict</span>"
                f"<span style='color:#9ca3af;font-size:0.78em'>{_v_conf_label}</span>"
                f"</div>"
                f"<div style='color:#e5e7eb;font-size:0.95em;margin-top:6px;line-height:1.5'>"
                f"{_v['reason']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # ── Portfolio fit (if any concerns) ────────────────────────────
            _pf = _cmp_result.get("portfolio_fit", {})
            if _pf.get("a") or _pf.get("b"):
                _pf_c1, _pf_c2 = st.columns(2)
                with _pf_c1:
                    if _pf.get("a"):
                        st.warning(f"**{_cmp_a_in}** · {_pf['a']}", icon="📋")
                with _pf_c2:
                    if _pf.get("b"):
                        st.warning(f"**{_cmp_b_in}** · {_pf['b']}", icon="📋")

            # ── Sectioned comparison tables ─────────────────────────────────
            # Each section uses a custom 3-col HTML table so we can highlight
            # the winner cell with a green background — st.dataframe doesn't
            # support per-cell conditional styling cleanly for mixed types.
            for _sec in _cmp_result["sections"]:
                st.markdown(f"#### {_sec['name']}")
                _table_html_rows = []
                for _r in _sec["rows"]:
                    _w   = _r["winner"]
                    _bga = "background:#052e16" if _w == "a" else "background:transparent"
                    _bgb = "background:#052e16" if _w == "b" else "background:transparent"
                    _ca  = "#86efac" if _w == "a" else "#e5e7eb"
                    _cb  = "#86efac" if _w == "b" else "#e5e7eb"
                    _table_html_rows.append(
                        f"<tr>"
                        f"<td style='padding:6px 10px;color:#9ca3af;font-size:0.85em'>"
                        f"{_r['label']}</td>"
                        f"<td style='padding:6px 10px;color:{_ca};font-weight:600;{_bga}'>"
                        f"{_r['value_a']}</td>"
                        f"<td style='padding:6px 10px;color:{_cb};font-weight:600;{_bgb}'>"
                        f"{_r['value_b']}</td>"
                        f"</tr>"
                    )
                st.markdown(
                    f"<table style='width:100%;border-collapse:collapse;"
                    f"background:#111827;border:1px solid #1f2937;border-radius:6px;"
                    f"margin-bottom:8px'>"
                    f"<thead><tr>"
                    f"<th style='padding:6px 10px;color:#6b7280;font-weight:700;"
                    f"text-align:left;font-size:0.72em;letter-spacing:0.05em;"
                    f"text-transform:uppercase;width:40%'>Metric</th>"
                    f"<th style='padding:6px 10px;color:#f9fafb;font-weight:700;"
                    f"text-align:left'>{_cmp_a_in}</th>"
                    f"<th style='padding:6px 10px;color:#f9fafb;font-weight:700;"
                    f"text-align:left'>{_cmp_b_in}</th>"
                    f"</tr></thead>"
                    f"<tbody>{''.join(_table_html_rows)}</tbody></table>",
                    unsafe_allow_html=True,
                )

            # ── Quick navigation to deep-dive on either ticker ──────────────
            st.markdown("---")
            _da, _db_ = st.columns(2)
            with _da:
                if st.button(f"📈 Deep dive on {_cmp_a_in}",
                             key=f"_cmp_dd_a_{_cmp_a_in}", use_container_width=True):
                    st.session_state["_pending_page"]    = "📈 Analysis"
                    st.session_state["_analysis_ticker"] = _cmp_a_in
                    st.rerun()
            with _db_:
                if st.button(f"📈 Deep dive on {_cmp_b_in}",
                             key=f"_cmp_dd_b_{_cmp_b_in}", use_container_width=True):
                    st.session_state["_pending_page"]    = "📈 Analysis"
                    st.session_state["_analysis_ticker"] = _cmp_b_in
                    st.rerun()

    elif _cmp_should_run and not (_cmp_a_in and _cmp_b_in):
        st.info("Enter both Ticker A and Ticker B to run a comparison.")
    elif not _cmp_cached:
        st.info(
            "Pick two tickers above (or use a quick-pick) and click **⚖️ Compare**. "
            "The verdict block highlights which composite is stronger, with sub-factor "
            "tie-breakers when scores are close."
        )


# ═════════════════════════════════════════════════════════════════════════════
# PAGE 5 — TRADE JOURNAL
# ═════════════════════════════════════════════════════════════════════════════
elif page == "📒 Trade Journal":
    _fill_news_slot(_news_slot, st.session_state.get("_sidebar_news", []))
    st.title("📒 Trade Journal")
    st.caption(
        "Log every buy and sell here. Realized P&L is calculated automatically. "
        "Holdings update instantly when you record a sell."
    )

    if not db.has_db():
        st.warning(
            "🟡 **No Supabase connection** — trades will only last for this session.  \n"
            "Add your Supabase credentials in `.streamlit/secrets.toml` to persist trades permanently."
        )

    # ── Defensive drift check ────────────────────────────────────────────────
    # Replay the trade history once per session to catch realized_pnl rows
    # whose stored value disagrees with the chronologically-replayed value,
    # or SELL rows with no prior BUY in history. This is the same bug class
    # behind the NFLX +$177 incident — bad holdings state silently
    # contaminated the realized_pnl on a later legitimate SELL. The check
    # itself is read-only; we surface a banner pointing the user at the
    # 🔄 Rebuild flow below, but never auto-mutate on page load.
    if (db.has_db()
            and not st.session_state.get("_tj_drift_checked")
            and not st.session_state.get("trades_df", pd.DataFrame()).empty):
        try:
            _drift_check = db.recalculate_from_trades(st.session_state["trades_df"])
            st.session_state["_tj_drift_state"] = {
                "n_pnl_corrections": len(_drift_check.get("realized_pnl_corrections", {})),
                "n_warnings":        len(_drift_check.get("warnings", [])),
                "warnings":          list(_drift_check.get("warnings", []))[:5],
            }
        except Exception:
            st.session_state["_tj_drift_state"] = {}
        st.session_state["_tj_drift_checked"] = True

    _tj_drift = st.session_state.get("_tj_drift_state") or {}
    if _tj_drift.get("n_pnl_corrections", 0) or _tj_drift.get("n_warnings", 0):
        _n_corr = _tj_drift.get("n_pnl_corrections", 0)
        _n_warn = _tj_drift.get("n_warnings", 0)
        _drift_lines = []
        if _n_corr:
            _drift_lines.append(
                f"<b>{_n_corr}</b> SELL row{'s' if _n_corr != 1 else ''} "
                "with stored realized P&amp;L that disagrees with the trade-history replay"
            )
        if _n_warn:
            _drift_lines.append(
                f"<b>{_n_warn}</b> SELL row{'s' if _n_warn != 1 else ''} "
                "with no prior BUY in history (cost basis can't be computed)"
            )
        st.markdown(
            "<div style='background:#3b2a0a;border:1px solid #f59e0b;"
            "border-left:4px solid #f59e0b;border-radius:8px;"
            "padding:12px 16px;margin:8px 0;color:#fde68a'>"
            "⚠️ <b>Drift detected between trades and holdings.</b><br>"
            "<span style='font-size:0.92em'>"
            + " · ".join(_drift_lines)
            + ".<br>Scroll down to <b>🔄 Rebuild holdings &amp; realized P&amp;L</b> "
            "to preview and apply the corrections.</span></div>",
            unsafe_allow_html=True,
        )

    # ── Pre-fill from recommendation card ────────────────────────────────────
    # IMPORTANT: use get() not pop() here. Streamlit forms reset their
    # widgets to the `value=` parameter whenever ANY widget OUTSIDE the form
    # triggers a rerun (Action radio, Decision Context expander, etc.). If
    # we pop the prefill on first render, those resets read `value=1`
    # instead of the original prefilled shares — silently blanking out the
    # pre-filled shares while the user is mid-edit. We keep the prefill
    # around until the trade is successfully recorded (popped below in the
    # submit handler), so every form reset re-applies the same values.
    prefill = st.session_state.get("_prefill_trade", {})

    # ── Log a Trade form ──────────────────────────────────────────────────────
    _prefill_ticker = prefill.get("ticker", "").strip().upper()
    _prefill_action = prefill.get("action", "BUY")

    # Sync pre-fill into session state keys when a recommendation pushes values in
    if _prefill_ticker:
        st.session_state["_tj_ticker"] = _prefill_ticker
    if prefill.get("action"):
        st.session_state["_tj_action"] = _prefill_action

    with st.expander("➕ Log a Trade", expanded=bool(prefill)):
        # ── Action + Ticker live OUTSIDE the form so changes trigger a rerun
        # and cost basis can be auto-looked-up before the form renders.
        _tx_c1, _tx_c2 = st.columns([1, 2])
        with _tx_c1:
            st.radio(
                "Action", ["BUY", "SELL"], horizontal=True,
                key="_tj_action",
                index=0 if st.session_state.get("_tj_action", _prefill_action) == "BUY" else 1,
            )
        with _tx_c2:
            st.text_input(
                "Ticker", placeholder="e.g. CRWV",
                key="_tj_ticker",
            )

        # Reactive cost-basis lookup — runs on every rerun after ticker/action change
        _live_action = st.session_state.get("_tj_action", "BUY")
        _live_ticker = (st.session_state.get("_tj_ticker") or "").strip().upper()

        # ── Live market price lookup for the sanity check ─────────────────────
        # Fetches the latest known price from (1) port_df if held, (2) scanner
        # results if in the universe, (3) yfinance as a fallback. Cached so
        # this is cheap on repeated reruns. Used both to display a "Current
        # market: $X" reference under the price input and to validate the
        # submitted price against reality.
        @st.cache_data(ttl=300, show_spinner=False)
        def _tj_market_price(_ticker: str) -> float | None:
            if not _ticker:
                return None
            try:
                px = fetch_live_prices([_ticker])
                if _ticker in px:
                    p = float(px[_ticker].get("price") or 0)
                    return p if p > 0 else None
            except Exception:
                pass
            return None

        def _market_price_for(ticker: str) -> float | None:
            """
            Resolve the latest known market price without making a network call
            when possible. Held positions are the cheapest source; the cached
            live fetch is the fallback.
            """
            if not ticker:
                return None
            _pf = st.session_state.get("_last_port_df")
            if _pf is not None and not _pf.empty and "Price" in _pf.columns:
                _row = _pf[_pf["Ticker"] == ticker]
                if not _row.empty:
                    _p = float(_row.iloc[0].get("Price") or 0)
                    if _p > 0:
                        return _p
            _sr = st.session_state.get("scanner_results")
            if _sr is not None and not _sr.empty and "Price" in _sr.columns:
                _row = _sr[_sr["Ticker"] == ticker]
                if not _row.empty:
                    _p = float(_row.iloc[0].get("Price") or 0)
                    if _p > 0:
                        return _p
            return _tj_market_price(ticker)

        _market_price = _market_price_for(_live_ticker) if _live_ticker else None

        # ── Sanity-check override (outside the form so it persists if the user
        # has to re-submit after a validation block) ─────────────────────────
        st.checkbox(
            "⚠ Allow unusual price / unrecognized ticker (skip sanity checks)",
            key="_tj_override_price_check",
            help=(
                "By default the form blocks trades where the entered price is "
                "more than ±50% off the current market price (catches typos like "
                "$0.01 vs $565), or where the ticker can't be resolved at all "
                "(catches typos like NFLZ vs NFLX). Tick this only if you're "
                "recording a backfill, split-adjusted price, delisted symbol, "
                "or other intentional unusual transaction."
            ),
        )

        # ── Decision Context — outside form so followed_signal can gate fields ──
        # Pre-fill signal_seen from current portfolio signal if ticker is held
        _dc_signal_default = ""
        if _live_ticker:
            _dc_held = st.session_state.get("holdings_df", pd.DataFrame())
            _dc_match = _dc_held[_dc_held["Ticker"] == _live_ticker] if not _dc_held.empty else pd.DataFrame()
            if not _dc_match.empty:
                # Try to get the current signal from last loaded port_df stored in session
                _dc_pf = st.session_state.get("_last_port_df")
                if _dc_pf is not None and not _dc_pf.empty and "Signal" in _dc_pf.columns:
                    _dc_row = _dc_pf[_dc_pf["Ticker"] == _live_ticker]
                    if not _dc_row.empty:
                        _dc_sig  = str(_dc_row.iloc[0].get("Signal", ""))
                        _dc_scr  = _dc_row.iloc[0].get("Score", "")
                        _dc_signal_default = f"{_dc_sig} · Score {_dc_scr:.0f}/100" if _dc_sig else ""

        with st.expander("📋 Decision Context (optional — builds your pattern library)", expanded=False):
            st.caption(
                "Capture *why* you made this trade vs the signal. "
                "Over time this builds a personal pattern library showing where signals help or hurt."
            )
            _dc_c1, _dc_c2 = st.columns([2, 1])
            with _dc_c1:
                st.selectbox(
                    "Did you follow the signal?",
                    ["— (skip)", "Yes — followed signal", "No — overrode signal", "No signal — discretionary"],
                    key="_tj_followed",
                )
            with _dc_c2:
                _tj_sig_ts = st.session_state.get("_signals_computed_at", "")
                st.text_input(
                    "Signal at time of trade",
                    value=_dc_signal_default,
                    key="_tj_signal_seen",
                    placeholder="e.g. Sell · Score 42",
                    help=(
                        f"Auto-filled from current portfolio signal if held"
                        + (f" (as of {_tj_sig_ts})" if _tj_sig_ts else "")
                        + ". Signals are computed at page load — refresh if stale."
                    ),
                )
            _tj_followed_val = st.session_state.get("_tj_followed", "— (skip)")
            if "No — overrode" in _tj_followed_val:
                st.text_input(
                    "Why did you override?",
                    key="_tj_deviation_reason",
                    placeholder="e.g. Believed earnings beat was priced in",
                )
            else:
                st.session_state["_tj_deviation_reason"] = ""
            st.text_input(
                "Lesson learned (optional)",
                key="_tj_lesson",
                placeholder="e.g. Always follow pre-earnings sell signals on small-caps",
            )
        _cost_basis_hint = 0.01
        _cost_hint_label = "Cost Basis / share ($)"
        _cb_info = ""
        if _live_ticker:
            _cb_match = st.session_state.holdings_df[
                st.session_state.holdings_df["Ticker"] == _live_ticker
            ]
            if not _cb_match.empty:
                _avg = float(_cb_match.iloc[0]["Avg Cost ($)"])
                _cost_basis_hint = _avg
                if _live_action == "SELL":
                    _cost_hint_label = f"Cost Basis / share ($)  ← auto-filled ${_avg:.2f}"
                    _cb_info = f"ℹ️ Avg cost from holdings: **${_avg:.2f}** — pre-filled below"
            elif _live_action == "BUY":
                _cb_info = "🆕 New position — cost basis will be set to your buy price"

        if _cb_info:
            st.caption(_cb_info)

        # st.form prevents double-submission on rerun (shares / price / notes only)
        with st.form("log_trade_form", clear_on_submit=True):
            f_col3, f_col4, f_col5 = st.columns(3)
            with f_col3:
                _trigger_opts = ["MANUAL", "RECOMMENDATION", "STOP_HIT", "REBALANCE", "WATCHLIST_ENTRY"]
                _prefill_trigger = prefill.get("trigger", "MANUAL")
                # Defensive: any unknown trigger value falls back to MANUAL
                # so a future caller doesn't crash this form with a ValueError.
                _trigger_idx = _trigger_opts.index(_prefill_trigger) if _prefill_trigger in _trigger_opts else 0
                trigger_type = st.selectbox(
                    "Reason",
                    _trigger_opts,
                    index=_trigger_idx,
                )
            with f_col4:
                shares_val = st.number_input(
                    "Shares", min_value=0.001,
                    value=float(prefill.get("shares", 1)),
                    step=1.0, format="%.3f",
                )
            with f_col5:
                price_val = st.number_input(
                    "Price per share ($)", min_value=0.01,
                    value=float(prefill.get("price", 0.01)),
                    step=0.01, format="%.2f",
                )
                if _market_price:
                    _mp_delta_str = ""
                    try:
                        _mp_ratio = price_val / _market_price
                        if _mp_ratio < 0.5 or _mp_ratio > 2.0:
                            _mp_delta_str = (
                                f" <span style='color:#fca5a5'>· ⚠ {(_mp_ratio-1)*100:+.0f}% off market</span>"
                            )
                    except Exception:
                        pass
                    st.markdown(
                        f"<div style='color:#94a3b8;font-size:0.78em;margin-top:-8px'>"
                        f"💵 Current market: <b style='color:#cbd5e1'>${_market_price:,.2f}</b>"
                        f"{_mp_delta_str}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            # Cost basis: shown for SELL (pre-filled); hidden for BUY (auto = buy price)
            if _live_action == "SELL":
                cost_basis_val = st.number_input(
                    _cost_hint_label,
                    min_value=0.01, value=max(0.01, _cost_basis_hint),
                    step=0.01, format="%.2f",
                    help="Auto-filled from your holdings avg cost. Edit if needed.",
                )
            else:
                cost_basis_val = None   # set to price_val after submission

            notes_val = st.text_input(
                "Notes (optional)", value=prefill.get("notes", ""),
                placeholder="e.g. Added on dip",
            )

            submitted = st.form_submit_button("✅ Record Trade", type="primary")

        # Read action + ticker from session state (set by widgets outside the form)
        action       = st.session_state.get("_tj_action", "BUY")
        ticker_input = (st.session_state.get("_tj_ticker") or "").strip().upper()
        if action == "BUY":
            cost_basis_val = price_val  # cost basis = what you paid

        # Process submission outside the form block
        if submitted:
            # Stash everything entered into prefill so the form re-renders pre-
            # filled on validation failure (otherwise clear_on_submit=True wipes
            # the user's input on every error and they'd have to retype).
            _stash_failed_entry = {
                "ticker":   ticker_input,
                "action":   action,
                "shares":   shares_val,
                "price":    price_val,
                "trigger":  trigger_type,
                "notes":    notes_val,
            }

            # ── Price sanity check ────────────────────────────────────────────
            # Catches the $0.01-vs-$565 typo class. Two-tier validation:
            #  1. Hard floor — sub-$0.10 prices are almost always a fat-finger
            #     error. Even penny stocks rarely trade that low.
            #  2. Market-relative — if the entered price is < 50% or > 200% of
            #     the latest known market price, it's most likely a typo. The
            #     user can override by ticking the "Allow unusual price"
            #     checkbox outside the form (persists across reruns).
            _override   = st.session_state.get("_tj_override_price_check", False)
            _mp_check   = _market_price_for(ticker_input) if ticker_input else None
            _price_block_reason: str | None = None
            if price_val < 0.10 and not _override:
                _price_block_reason = (
                    f"Entered price <b>${price_val:.2f}</b> is below $0.10 — almost "
                    "always a typo. Confirm the price, or tick "
                    "<b>'Allow unusual price'</b> above the form and resubmit."
                )
            elif _mp_check and _mp_check > 0 and not _override:
                _ratio = price_val / _mp_check
                if _ratio < 0.5 or _ratio > 2.0:
                    _price_block_reason = (
                        f"Entered price <b>${price_val:.2f}</b> is "
                        f"<b>{(_ratio-1)*100:+.0f}%</b> off the current market price "
                        f"<b>${_mp_check:,.2f}</b> — looks like a typo. Confirm "
                        "the price, or tick <b>'Allow unusual price'</b> above the "
                        "form (e.g. for a historical backfill) and resubmit."
                    )

            # ── Ticker existence check ───────────────────────────────────────
            # _market_price_for returns None when no source (held positions,
            # scanner results, or yfinance) can resolve a price. For a real
            # ticker that's just not in the scanner universe, yfinance still
            # provides a fallback price; persistent None almost always means
            # the symbol is a typo (e.g. "NFLZ" vs "NFLX"). Block unless the
            # user explicitly overrides — same override covers price sanity,
            # since both checks reduce to "I know what I'm doing."
            _ticker_block_reason: str | None = None
            if ticker_input and _mp_check is None and not _override:
                _ticker_block_reason = (
                    f"Couldn't resolve a market price for <b>{ticker_input}</b> — "
                    "likely a typo or a delisted symbol. Verify the spelling, or "
                    "tick <b>'Allow unusual price'</b> above the form if you're "
                    "intentionally recording a historical / unlisted trade."
                )

            # ── SELL-shares sanity check ──────────────────────────────────────
            # Block "SELL 100 NFLX" when only 5 are held — without this guard
            # the row was being accepted, holdings.Shares went negative, and the
            # recalculate_from_trades replay flagged it as "SELL with no prior
            # BUY". Caught the bad row but only after data was already in.
            #
            # A SELL with NO holding row at all is intentionally allowed (the
            # "logged as historical trade" path below) so users can backfill
            # closed trades from before they started using the app.
            _shares_block_reason: str | None = None
            if action == "SELL" and ticker_input:
                _h_match = st.session_state.holdings_df[
                    st.session_state.holdings_df["Ticker"] == ticker_input
                ]
                if not _h_match.empty:
                    _held_shares = float(_h_match.iloc[0]["Shares"])
                    if shares_val > _held_shares + 1e-6:
                        _shares_block_reason = (
                            f"You're trying to sell <b>{shares_val:g} shares</b> of "
                            f"<b>{ticker_input}</b> but only hold <b>{_held_shares:g}</b>. "
                            "Correct the share count, or trim the holding directly on the "
                            "Portfolio page if your records disagree with the app's."
                        )

            if not ticker_input:
                st.error("Enter a ticker symbol.")
                st.session_state["_prefill_trade"] = _stash_failed_entry
            elif shares_val <= 0:
                st.error("Shares must be greater than 0.")
                st.session_state["_prefill_trade"] = _stash_failed_entry
            elif price_val <= 0:
                st.error("Price must be greater than 0.")
                st.session_state["_prefill_trade"] = _stash_failed_entry
            elif _shares_block_reason:
                st.markdown(
                    f"<div style='background:#3f1d1d;border:1px solid #ef4444;"
                    f"border-radius:8px;padding:12px 16px;margin:8px 0;color:#fecaca'>"
                    f"⛔ <b>Trade not recorded — sell exceeds your current holding.</b><br>"
                    f"<span style='font-size:0.92em'>{_shares_block_reason}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.session_state["_prefill_trade"] = _stash_failed_entry
            elif _ticker_block_reason:
                st.markdown(
                    f"<div style='background:#3f1d1d;border:1px solid #ef4444;"
                    f"border-radius:8px;padding:12px 16px;margin:8px 0;color:#fecaca'>"
                    f"⛔ <b>Trade not recorded — ticker not recognized.</b><br>"
                    f"<span style='font-size:0.92em'>{_ticker_block_reason}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.session_state["_prefill_trade"] = _stash_failed_entry
            elif _price_block_reason:
                st.markdown(
                    f"<div style='background:#3f1d1d;border:1px solid #ef4444;"
                    f"border-radius:8px;padding:12px 16px;margin:8px 0;color:#fecaca'>"
                    f"⛔ <b>Trade not recorded — price sanity check failed.</b><br>"
                    f"<span style='font-size:0.92em'>{_price_block_reason}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.session_state["_prefill_trade"] = _stash_failed_entry
            else:
                realized_pnl = (
                    compute_realized_pnl(shares_val, price_val, cost_basis_val)
                    if action == "SELL" else None
                )
                # Decision context fields
                _dc_followed_raw = st.session_state.get("_tj_followed", "— (skip)")
                _dc_followed = (
                    "yes" if "Yes" in _dc_followed_raw
                    else "no" if "No — overrode" in _dc_followed_raw
                    else "discretionary" if "discretionary" in _dc_followed_raw
                    else None
                )
                record = {
                    "ticker":           ticker_input,
                    "action":           action,
                    "shares":           shares_val,
                    "price":            price_val,
                    "cost_basis":       cost_basis_val if action == "SELL" else None,
                    "realized_pnl":     realized_pnl,
                    "notes":            notes_val or None,
                    "trigger_type":     trigger_type,
                    "signal_seen":      (st.session_state.get("_tj_signal_seen") or "").strip() or None,
                    "followed_signal":  _dc_followed,
                    "deviation_reason": (st.session_state.get("_tj_deviation_reason") or "").strip() or None,
                    "lesson":           (st.session_state.get("_tj_lesson") or "").strip() or None,
                }
                saved = db.save_trade(record)
                if saved or not db.has_db():
                    if not db.has_db():
                        new_row = pd.DataFrame([{**record, "id": None, "traded_at": datetime.now().isoformat()}])
                        st.session_state.trades_df = pd.concat(
                            [new_row, st.session_state.trades_df], ignore_index=True
                        )
                    else:
                        st.session_state.trades_df = db.load_trades()

                    # ── Sync holdings with trade ─────────────────────────
                    h_df = st.session_state.holdings_df.copy()
                    mask = h_df["Ticker"] == ticker_input

                    if action == "SELL":
                        if mask.any():
                            idx = h_df[mask].index[0]
                            current_shares = float(h_df.at[idx, "Shares"])
                            new_shares = current_shares - shares_val
                            if new_shares <= 0:
                                h_df = h_df.drop(idx).reset_index(drop=True)
                                st.success(
                                    f"✅ **{ticker_input}** fully exited — position removed from portfolio."
                                )
                            else:
                                h_df.at[idx, "Shares"] = (
                                    int(new_shares) if new_shares == int(new_shares) else new_shares
                                )
                                pnl_str = f"${realized_pnl:+,.2f}" if realized_pnl is not None else "—"
                                pnl_pct = ((price_val - cost_basis_val) / cost_basis_val * 100
                                           if cost_basis_val else 0)
                                st.success(
                                    f"✅ Sold **{shares_val:.0f} shares of {ticker_input}** "
                                    f"@ ${price_val:.2f}  ·  "
                                    f"Holdings: {current_shares:.0f} → {new_shares:.0f} shares  ·  "
                                    f"Realized P&L: **{pnl_str}** ({pnl_pct:+.1f}%)"
                                )
                            db.save_holdings(h_df)
                            st.session_state.holdings_df = h_df
                        else:
                            st.success(
                                f"✅ SELL recorded for **{ticker_input}** "
                                f"(not in current holdings — logged as historical trade)."
                            )

                    else:  # BUY
                        if mask.any():
                            # Add to existing position — recalculate weighted avg cost
                            idx = h_df[mask].index[0]
                            old_shares   = float(h_df.at[idx, "Shares"])
                            old_avg_cost = float(h_df.at[idx, "Avg Cost ($)"])
                            new_shares   = old_shares + shares_val
                            new_avg_cost = round(
                                (old_shares * old_avg_cost + shares_val * price_val) / new_shares, 4
                            )
                            h_df.at[idx, "Shares"] = (
                                int(new_shares) if new_shares == int(new_shares) else new_shares
                            )
                            h_df.at[idx, "Avg Cost ($)"] = new_avg_cost
                            st.success(
                                f"✅ Added **{shares_val:.0f} shares of {ticker_input}** @ ${price_val:.2f}  ·  "
                                f"Holdings: {old_shares:.0f} → {new_shares:.0f} shares  ·  "
                                f"New avg cost: **${new_avg_cost:.2f}**"
                            )
                        else:
                            # New position — add row
                            new_row = pd.DataFrame([{
                                "Ticker":       ticker_input,
                                "Shares":       int(shares_val),
                                "Avg Cost ($)": round(price_val, 4),
                            }])
                            h_df = pd.concat([h_df, new_row], ignore_index=True)
                            st.success(
                                f"✅ New position opened: **{shares_val:.0f} × {ticker_input}** @ ${price_val:.2f}"
                            )
                        db.save_holdings(h_df)
                        st.session_state.holdings_df = h_df

                    # Consume the prefill now that the trade was recorded —
                    # any future visit to this page should start with a clean
                    # form. Paired with the get() at the top of the page.
                    st.session_state.pop("_prefill_trade", None)
                    # Clear the price-sanity override so the next trade is
                    # checked by default — overrides shouldn't be sticky.
                    st.session_state["_tj_override_price_check"] = False
                    st.rerun()

    # ── Performance Dashboard ─────────────────────────────────────────────────
    trades_df = st.session_state.get("trades_df", db.load_trades())
    stats = performance_stats(trades_df)

    if stats["total_trades"] > 0:
        st.subheader("📊 Performance Dashboard")
        pm1, pm2, pm3, pm4, pm5 = st.columns(5)
        pnl_total = stats["total_realized_pnl"]
        pm1.metric("Realized P&L",  f"${pnl_total:+,.2f}",
                   help="Total profit/loss from all closed (SELL) trades.")
        pm2.metric("Win Rate",      f"{stats['win_rate']:.0f}%",
                   f"{stats['wins']}W / {stats['losses']}L",
                   help="% of sell trades that were profitable.")
        pm3.metric("Avg Winner",    f"${stats['avg_winner']:+,.0f}",
                   help="Average profit on winning trades.")
        pm4.metric("Avg Loser",     f"${stats['avg_loser']:+,.0f}",
                   help="Average loss on losing trades.")
        pm5.metric("Trades Logged", stats["total_trades"],
                   f"{stats['sell_trades']} sells · {stats['buy_trades']} buys")

        # Expectancy — the pro metric
        if stats["wins"] + stats["losses"] > 0:
            expectancy = (
                stats["win_rate"] / 100 * stats["avg_winner"]
                + (1 - stats["win_rate"] / 100) * stats["avg_loser"]
            )
            exp_clr = "#00C851" if expectancy > 0 else "#ff4444"
            st.markdown(
                f"<div style='padding:8px 14px;background:#161616;border-radius:6px;"
                f"border-left:4px solid {exp_clr};margin:8px 0'>"
                f"<span style='font-size:0.8em;color:#888'>TRADE EXPECTANCY</span> "
                f"<span style='color:{exp_clr};font-weight:bold;font-size:1.05em'>"
                f"${expectancy:+,.2f} per trade</span>"
                f"<span style='font-size:0.78em;color:#666'> · "
                f"Positive = your strategy makes money on average across wins and losses</span></div>",
                unsafe_allow_html=True,
            )

        # P&L by ticker chart
        if stats["realized_by_ticker"]:
            import plotly.graph_objects as _go
            by_t = stats["realized_by_ticker"]
            tickers_sorted = sorted(by_t.keys(), key=lambda x: by_t[x], reverse=True)
            vals = [by_t[t] for t in tickers_sorted]
            colors = ["#00C851" if v >= 0 else "#ff4444" for v in vals]
            pnl_bar = _go.Figure(_go.Bar(
                x=tickers_sorted, y=vals,
                marker_color=colors,
                text=[f"${v:+,.0f}" for v in vals],
                textposition="outside",
            ))
            pnl_bar.update_layout(
                title="Realized P&L by Ticker",
                template="plotly_dark", height=260,
                yaxis_title="Realized P&L ($)",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(pnl_bar, use_container_width=True)

        # Best / worst trades
        if stats["best_trade"] or stats["worst_trade"]:
            bw1, bw2 = st.columns(2)
            if stats["best_trade"]:
                bt = stats["best_trade"]
                bw1.success(
                    f"🏆 **Best trade**: {bt['ticker']} — "
                    f"sold {bt['shares']:.0f} shares @ ${bt['price']:.2f} · "
                    f"**+${bt['realized_pnl']:,.2f}**"
                )
            if stats["worst_trade"]:
                wt = stats["worst_trade"]
                bw2.error(
                    f"📉 **Worst trade**: {wt['ticker']} — "
                    f"sold {wt['shares']:.0f} shares @ ${wt['price']:.2f} · "
                    f"**${wt['realized_pnl']:,.2f}**"
                )

    # ── Behavioral Analytics ──────────────────────────────────────────────────
    if stats["total_trades"] >= 3:
        try:
            _ta = build_full_analytics(trades_df)
        except Exception as _tae:
            _ta = {}
            st.warning(f"Analytics unavailable: {_tae}")

        if _ta and not _ta["ext_df"].empty:
            st.divider()
            st.subheader("🧠 Behavioral Analytics")
            st.caption(
                "Deeper analysis of your trading patterns. "
                "Institutional PMs review these metrics monthly to identify behavioral drift — "
                "the subtle habits that silently erode performance."
            )

            # Extended KPI row
            _ta_k = st.columns(5)
            _pf = _ta["profit_factor"]
            _pf_delta = "≥2.0 target" if _pf and _pf >= 2.0 else ("< 1.0 ⚠️" if _pf and _pf < 1.0 else None)
            _pf_dclr  = "normal" if (_pf and _pf >= 2.0) else ("inverse" if (_pf and _pf < 1.0) else "off")
            _ta_k[0].metric(
                "Profit Factor",
                f"{_pf:.2f}" if _pf else "—",
                delta=_pf_delta, delta_color=_pf_dclr,
                help="Gross wins / gross losses. Target ≥ 2.0",
            )
            _ta_k[1].metric(
                "Avg Win (%)",
                f"{_ta['avg_win_pct']:+.1f}%" if _ta["avg_win_pct"] else "—",
                help="Average % return on profitable closed trades",
            )
            _ta_k[2].metric(
                "Avg Loss (%)",
                f"{_ta['avg_loss_pct']:.1f}%" if _ta["avg_loss_pct"] else "—",
                help="Average % loss on unprofitable closed trades",
            )
            _hs = _ta["hold_stats"]
            _ta_k[3].metric(
                "Avg Hold (days)",
                f"{_hs['avg_hold_days']:.0f}d" if _hs.get("avg_hold_days") else "—",
                help="Estimated average hold time (days from matched BUY to SELL)",
            )
            _wl_ratio = (
                round(abs(_ta["avg_win_pct"] / _ta["avg_loss_pct"]), 2)
                if _ta["avg_win_pct"] and _ta["avg_loss_pct"] and _ta["avg_loss_pct"] != 0
                else None
            )
            _wl_dclr = "normal" if (_wl_ratio and _wl_ratio >= 2.0) else ("inverse" if (_wl_ratio and _wl_ratio < 1.0) else "off")
            _ta_k[4].metric(
                "Win/Loss Ratio",
                f"{_wl_ratio:.2f}:1" if _wl_ratio else "—",
                delta="≥2:1 target" if _wl_ratio and _wl_ratio >= 2.0 else None,
                delta_color=_wl_dclr,
                help="Avg win % / avg loss % — target ≥ 2.0",
            )

            # ── Monthly P&L trend ─────────────────────────────────────────────
            if not _ta["monthly_df"].empty and len(_ta["monthly_df"]) >= 2:
                import plotly.graph_objects as _go2
                _mon = _ta["monthly_df"]
                _mon_colors = ["#00C851" if v >= 0 else "#ff4444" for v in _mon["pnl"]]
                _mon_fig = _go2.Figure()
                _mon_fig.add_trace(_go2.Bar(
                    x=_mon["month_str"],
                    y=_mon["pnl"],
                    marker_color=_mon_colors,
                    name="Monthly P&L",
                    text=[f"${v:+,.0f}" for v in _mon["pnl"]],
                    textposition="outside",
                    customdata=list(zip(_mon["trade_count"], _mon["win_rate"])),
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        "P&L: $%{y:+,.0f}<br>"
                        "Trades: %{customdata[0]}<br>"
                        "Win rate: %{customdata[1]:.0f}%"
                        "<extra></extra>"
                    ),
                ))
                _mon_fig.update_layout(
                    title="Monthly Realized P&L Trend",
                    template="plotly_dark", height=260,
                    yaxis_title="Realized P&L ($)",
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(_mon_fig, use_container_width=True)

            # ── Trigger performance breakdown ─────────────────────────────────
            if not _ta["trigger_df"].empty:
                st.markdown("#### Performance by Trade Trigger")
                st.caption(
                    "Which reason for entering a trade generates the best outcome? "
                    "This is the single most actionable signal in the journal — "
                    "do more of what works, less of what doesn't."
                )

                _trig_df = _ta["trigger_df"].copy()

                def _trig_style(row):
                    exp = row.get("Expectancy ($)", 0)
                    if exp > 0:
                        return ["color:#00C851" if i == 0 else "" for i in range(len(row))]
                    elif exp < 0:
                        return ["color:#ff4444" if i == 0 else "" for i in range(len(row))]
                    return [""] * len(row)

                _trig_display = _trig_df.copy()
                for _tc in ["Avg Win ($)", "Avg Loss ($)", "Expectancy ($)"]:
                    if _tc in _trig_display.columns:
                        _trig_display[_tc] = _trig_display[_tc].apply(
                            lambda v: f"${v:+,.0f}" if pd.notna(v) else "—"
                        )
                for _tc in ["Win Rate (%)"]:
                    if _tc in _trig_display.columns:
                        _trig_display[_tc] = _trig_display[_tc].apply(
                            lambda v: f"{v:.0f}%" if pd.notna(v) else "—"
                        )
                for _tc in ["Avg Win (%)", "Avg Loss (%)"]:
                    if _tc in _trig_display.columns:
                        _trig_display[_tc] = _trig_display[_tc].apply(
                            lambda v: f"{v:+.1f}%" if pd.notna(v) else "—"
                        )
                for _tc in ["Profit Factor"]:
                    if _tc in _trig_display.columns:
                        _trig_display[_tc] = _trig_display[_tc].apply(
                            lambda v: f"{v:.2f}" if pd.notna(v) else "—"
                        )
                st.dataframe(_trig_display, use_container_width=True, hide_index=True)

            # ── Hold time breakdown ───────────────────────────────────────────
            _hs = _ta["hold_stats"]
            if _hs.get("avg_hold_days") and _hs.get("winners_avg_days") and _hs.get("losers_avg_days"):
                st.markdown("#### Hold Time Analysis")
                _ht1, _ht2, _ht3 = st.columns(3)
                _ht1.metric("Winners: avg hold", f"{_hs['winners_avg_days']:.0f} days")
                _ht2.metric("Losers: avg hold",  f"{_hs['losers_avg_days']:.0f} days",
                            delta=(
                                "Holding losers too long ⚠️"
                                if _hs["losers_avg_days"] > _hs["winners_avg_days"] * 1.3
                                else "Cutting losers faster ✓"
                            ),
                            delta_color=(
                                "inverse"
                                if _hs["losers_avg_days"] > _hs["winners_avg_days"] * 1.3
                                else "normal"
                            ))
                _ht3.metric("Sample size", f"{_hs['sample_size']} matched pairs",
                            help="Trades where a BUY was found before the SELL for the same ticker")

            # ── Behavioral insights cards ─────────────────────────────────────
            if _ta["insights"]:
                st.divider()
                st.markdown("#### 🎯 Behavioral Coaching")
                st.caption(
                    "Pattern-based feedback on your trading behavior — "
                    "not individual trade quality, but systematic habits that affect long-run performance."
                )

                for _ins in _ta["insights"]:
                    _ins_pri  = _ins["priority"]
                    _ins_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "OK": "✅"}.get(_ins_pri, "📌")
                    _ins_bclr = {"HIGH": "#ff4444", "MEDIUM": "#ffbb33", "OK": "#00C851"}.get(_ins_pri, "#888")
                    _ins_exp  = _ins_pri in ("HIGH", "MEDIUM")

                    with st.expander(
                        f"{_ins_icon} **{_ins_pri}** · {_ins['title']}",
                        expanded=_ins_exp,
                    ):
                        # Observation banner
                        st.markdown(
                            f"<div style='padding:10px 14px;background:#1a1a1a;"
                            f"border-radius:6px;border-left:4px solid {_ins_bclr};margin:8px 0'>"
                            f"<span style='font-size:0.72em;color:#888;font-weight:700;"
                            f"letter-spacing:0.09em;text-transform:uppercase'>What the Data Shows</span><br>"
                            f"<span style='color:#eee'>{_ins['observation']}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        _ins_cl, _ins_cr = st.columns([1, 1])
                        with _ins_cl:
                            st.markdown("**Why It Matters**")
                            st.markdown(
                                f"<div style='color:#bbb;font-size:0.88em'>"
                                f"{_ins['implication']}</div>",
                                unsafe_allow_html=True,
                            )
                        with _ins_cr:
                            st.markdown(
                                f"<div style='padding:10px 14px;background:#0d2137;"
                                f"border-radius:6px;border-left:4px solid #4a9eff'>"
                                f"<span style='font-size:0.72em;color:#4a9eff;font-weight:700;"
                                f"letter-spacing:0.09em;text-transform:uppercase'>Corrective Action</span><br>"
                                f"<span style='color:#eee;font-size:0.88em'>{_ins['action']}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                        if _ins.get("institutional_lens"):
                            st.markdown("")
                            st.info(f"**Institutional Lens** · {_ins['institutional_lens']}")

    # ── Decision Journal — My Patterns ───────────────────────────────────────
    try:
        _dj = compute_patterns(trades_df)
    except Exception:
        _dj = {"total_with_context": 0}

    if _dj.get("total_with_context", 0) > 0:
        st.divider()
        st.subheader("🧭 Decision Journal — My Patterns")
        st.caption(
            "Tracks how often you follow signals vs override them, and what the outcomes are. "
            "This is your personal accountability layer — patterns you can't see cost money silently."
        )

        # Behavioral insight banner
        if _dj.get("behavioral_insight"):
            _dj_clr = "#7f1d1d" if _dj["ignored_losses"] > _dj["ignored_wins"] else "#14532d"
            _dj_bdr = "#ef4444" if _dj["ignored_losses"] > _dj["ignored_wins"] else "#22c55e"
            st.markdown(
                f"<div style='background:{_dj_clr};border-left:4px solid {_dj_bdr};"
                f"border-radius:8px;padding:12px 16px;margin-bottom:12px'>"
                f"<span style='font-size:0.78em;color:#9ca3af;font-weight:700;"
                f"letter-spacing:0.08em;text-transform:uppercase'>Pattern Insight</span><br>"
                f"<span style='color:#f9fafb'>{_dj['behavioral_insight']}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # KPI strip
        _dj_k1, _dj_k2, _dj_k3, _dj_k4, _dj_k5 = st.columns(5)
        _dj_k1.metric("Trades with context", _dj["total_with_context"])
        _dj_k2.metric(
            "Signal accuracy",
            f"{_dj['signal_accuracy']:.0f}%" if _dj["signal_accuracy"] is not None else "—",
            f"{_dj['followed_wins']}W / {_dj['followed_losses']}L",
            help="Win rate when you followed the signal",
        )
        _dj_k3.metric(
            "Override accuracy",
            f"{_dj['override_accuracy']:.0f}%" if _dj["override_accuracy"] is not None else "—",
            f"{_dj['ignored_wins']}W / {_dj['ignored_losses']}L",
            help="Win rate when you ignored the signal",
        )
        _dj_k4.metric(
            "P&L from following",
            f"${_dj['followed_pnl']:+,.0f}",
            delta_color="normal" if _dj["followed_pnl"] >= 0 else "inverse",
            help="Total realized P&L on trades where you followed the signal",
        )
        _dj_k5.metric(
            "P&L from overrides",
            f"${_dj['ignored_pnl']:+,.0f}",
            delta_color="normal" if _dj["ignored_pnl"] >= 0 else "inverse",
            help="Total realized P&L on trades where you overrode the signal",
        )

        # Costly deviations
        if _dj["costly_deviations"]:
            st.markdown(f"##### 🚨 Costly Deviations ({len(_dj['costly_deviations'])})")
            st.caption("Trades where you ignored a signal and lost money. The most important pattern to study.")
            for _cd in _dj["costly_deviations"]:
                st.markdown(
                    f"<div style='background:#1c1917;border-left:3px solid #ef4444;"
                    f"border-radius:6px;padding:10px 14px;margin-bottom:6px'>"
                    f"<div style='color:#f9fafb;font-weight:600;font-size:0.88em'>"
                    f"📉 <span style='color:#fbbf24'>{_cd['ticker']}</span> · "
                    f"<span style='color:#ef4444'>${_cd['realized_pnl']:+,.0f}</span> · "
                    f"<span style='color:#9ca3af;font-weight:400'>{_cd['traded_at']}</span></div>"
                    + (f"<div style='color:#9ca3af;font-size:0.8em;margin-top:2px'>"
                       f"Signal: {_cd['signal_seen']}</div>" if _cd["signal_seen"] else "")
                    + (f"<div style='color:#d1d5db;font-size:0.82em;margin-top:2px'>"
                       f"Override reason: {_cd['deviation_reason']}</div>" if _cd["deviation_reason"] else "")
                    + (f"<div style='background:#292524;border-radius:4px;padding:6px 10px;margin-top:6px;"
                       f"color:#fbbf24;font-size:0.82em'>💡 {_cd['lesson']}</div>" if _cd["lesson"] else "")
                    + f"</div>",
                    unsafe_allow_html=True,
                )

        # Good overrides
        if _dj["good_overrides"]:
            with st.expander(f"✅ Good Overrides ({len(_dj['good_overrides'])}) — when ignoring the signal paid off"):
                for _go_item in _dj["good_overrides"]:
                    st.markdown(
                        f"<div style='background:#1c1917;border-left:3px solid #22c55e;"
                        f"border-radius:6px;padding:10px 14px;margin-bottom:6px'>"
                        f"<div style='color:#f9fafb;font-weight:600;font-size:0.88em'>"
                        f"✅ <span style='color:#4ade80'>{_go_item['ticker']}</span> · "
                        f"<span style='color:#22c55e'>${_go_item['realized_pnl']:+,.0f}</span> · "
                        f"<span style='color:#9ca3af;font-weight:400'>{_go_item['traded_at']}</span></div>"
                        + (f"<div style='color:#9ca3af;font-size:0.8em;margin-top:2px'>"
                           f"Signal: {_go_item['signal_seen']}</div>" if _go_item["signal_seen"] else "")
                        + (f"<div style='color:#d1d5db;font-size:0.82em;margin-top:2px'>"
                           f"Override reason: {_go_item['deviation_reason']}</div>" if _go_item["deviation_reason"] else "")
                        + f"</div>",
                        unsafe_allow_html=True,
                    )

        # Lessons library
        if _dj["lessons"]:
            with st.expander(f"📚 Lessons Library ({len(_dj['lessons'])})"):
                st.caption("Every lesson you've logged, newest first. Your personal trading rulebook.")
                for _les in _dj["lessons"]:
                    _les_clr = "#22c55e" if _les["pnl"] >= 0 else "#ef4444"
                    st.markdown(
                        f"<div style='background:#1c1917;border-left:3px solid {_les_clr};"
                        f"border-radius:6px;padding:8px 12px;margin-bottom:5px'>"
                        f"<span style='color:#fbbf24;font-weight:600'>{_les['ticker']}</span> "
                        f"<span style='color:#9ca3af;font-size:0.8em'>· {_les['date']} · "
                        f"{'followed' if _les['followed'] == 'yes' else 'overrode'} signal</span><br>"
                        f"<span style='color:#f9fafb'>💡 {_les['text']}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

    # ── Trade History table ───────────────────────────────────────────────────
    st.subheader("📋 Trade History")
    st.caption("Check the **Delete?** box on any row then click 'Delete Selected' to remove duplicates or mistakes.")
    if trades_df.empty:
        st.info("No trades recorded yet. Use the form above to log your first trade.")
    else:
        display_df = trades_df.copy()
        for col in ["shares", "price", "cost_basis", "realized_pnl"]:
            if col in display_df.columns:
                display_df[col] = pd.to_numeric(display_df[col], errors="coerce")
        if "traded_at" in display_df.columns:
            display_df["traded_at"] = pd.to_datetime(
                display_df["traded_at"], errors="coerce"
            ).dt.strftime("%Y-%m-%d %H:%M")

        # Add delete checkbox column
        display_df.insert(0, "Delete?", False)

        show_cols = ["Delete?"] + [c for c in
                     ["traded_at", "ticker", "action", "shares", "price",
                      "cost_basis", "realized_pnl", "trigger_type", "notes"]
                     if c in display_df.columns]

        # Only the Delete? column is interactive — every other column is
        # disabled so the table can't be silently edited. Streamlit's
        # data_editor returns the edited DataFrame, but code only reads the
        # Delete? column; making the rest editable used to look possible to
        # the user but the edits never persisted. Mark them read-only to
        # match actual behaviour.
        edited_trades = st.data_editor(
            display_df[show_cols],
            column_config={
                "Delete?":      st.column_config.CheckboxColumn("Delete?", default=False),
                "traded_at":    st.column_config.TextColumn("Date / Time",     disabled=True),
                "ticker":       st.column_config.TextColumn("Ticker",          disabled=True),
                "action":       st.column_config.TextColumn("Action",          disabled=True),
                "shares":       st.column_config.NumberColumn("Shares",            format="%.0f",    disabled=True),
                "price":        st.column_config.NumberColumn("Price ($)",         format="$%.2f",   disabled=True),
                "cost_basis":   st.column_config.NumberColumn("Cost Basis ($)",    format="$%.2f",   disabled=True),
                "realized_pnl": st.column_config.NumberColumn("Realized P&L ($)",  format="$%+,.2f", disabled=True),
                "trigger_type": st.column_config.TextColumn("Reason",           disabled=True),
                "notes":        st.column_config.TextColumn("Notes",            disabled=True),
            },
            hide_index=True,
            use_container_width=True,
            key="trade_history_editor",
        )

        rows_to_delete = edited_trades[edited_trades["Delete?"] == True]
        if not rows_to_delete.empty:
            n = len(rows_to_delete)
            if st.button(
                f"🗑️ Delete {n} selected trade{'s' if n > 1 else ''}",
                type="secondary",
            ):
                ids_to_delete = trades_df.iloc[rows_to_delete.index]["id"].tolist()
                failed = 0
                for tid in ids_to_delete:
                    if not db.delete_trade(tid):
                        failed += 1
                if failed == 0:
                    st.success(f"✅ Deleted {n} trade{'s' if n > 1 else ''}.")
                else:
                    st.warning(f"Deleted {n - failed} of {n}. {failed} failed — check logs.")

                # ── Auto-rebuild holdings from the remaining trade history ─────
                # When a trade is deleted, holdings_df has already absorbed its
                # incremental effect at save time but the deletion doesn't roll
                # that back. Replaying all remaining trades gives the truthful
                # state and corrects any realized_pnl that was computed from the
                # corrupted basis (the NFLX +$177-when-it-should-have-been-
                # negative class of bug).
                fresh_trades = db.load_trades()
                recalc = db.recalculate_from_trades(fresh_trades)
                db.save_holdings(recalc["holdings_df"])
                st.session_state.holdings_df = recalc["holdings_df"]
                _n_corrected = 0
                for _tid, _c in recalc["realized_pnl_corrections"].items():
                    if db.update_trade_realized_pnl(
                        _tid, _c["realized_pnl"], cost_basis=_c["cost_basis"]
                    ):
                        _n_corrected += 1
                if _n_corrected:
                    st.info(
                        f"🔄 Holdings rebuilt from {len(fresh_trades)} trades. "
                        f"Also corrected realized_pnl on **{_n_corrected}** SELL "
                        "row(s) whose stored figures were computed from a stale "
                        "cost basis."
                    )
                if recalc["warnings"]:
                    for _w in recalc["warnings"][:3]:
                        st.warning(f"⚠ {_w}")
                st.session_state.trades_df = db.load_trades()
                # Invalidate the page-load drift banner — we just resolved it.
                st.session_state.pop("_tj_drift_checked", None)
                st.session_state.pop("_tj_drift_state", None)
                st.rerun()

        # ── 🔄 Manual rebuild — fixes any pre-existing corrupted state ─────
        # Auto-rebuild on delete protects new workflows, but trades already
        # saved with wrong realized_pnl (because holdings_df was stale at
        # save-time) need a one-shot recalc against the full trade history.
        with st.expander(
            "🔄 Rebuild holdings & realized P&L from trade history",
            expanded=False,
        ):
            st.caption(
                "Replays every trade chronologically to derive truthful holdings "
                "(shares + weighted avg cost) and recompute the realized P&L on "
                "every SELL. Use this once after fixing bad trade data — or any "
                "time the holdings table looks out of sync with the trade log."
            )
            if st.button("Run rebuild now", key="_tj_rebuild_holdings_btn"):
                fresh = db.load_trades()
                if fresh.empty:
                    st.warning("No trades to replay.")
                else:
                    res = db.recalculate_from_trades(fresh)
                    # Preview the corrections so the user knows what's about to change
                    new_h     = res["holdings_df"]
                    corrs     = res["realized_pnl_corrections"]
                    warns     = res["warnings"]
                    st.markdown("**Recomputed holdings**")
                    st.dataframe(new_h, hide_index=True, use_container_width=True)
                    if corrs:
                        st.markdown(f"**Realized-P&L corrections to apply ({len(corrs)})**")
                        _corr_rows = []
                        for _tid, _c in corrs.items():
                            _match = fresh[fresh["id"] == _tid]
                            _ticker = str(_match.iloc[0]["ticker"]) if not _match.empty else "?"
                            _corr_rows.append({
                                "Trade ID":      _tid,
                                "Ticker":        _ticker,
                                "Stored P&L":    f"${(_c['stored_pnl'] or 0):+,.2f}",
                                "Corrected P&L": f"${_c['realized_pnl']:+,.2f}",
                                "Corrected basis": f"${_c['cost_basis']:.2f}",
                            })
                        st.dataframe(pd.DataFrame(_corr_rows), hide_index=True,
                                     use_container_width=True)
                    else:
                        st.caption("No realized_pnl corrections needed — all SELL rows are accurate.")
                    if warns:
                        for _w in warns[:5]:
                            st.warning(f"⚠ {_w}")
                    # Apply
                    if db.save_holdings(new_h):
                        st.session_state.holdings_df = new_h
                        _applied = 0
                        for _tid, _c in corrs.items():
                            if db.update_trade_realized_pnl(
                                _tid, _c["realized_pnl"], cost_basis=_c["cost_basis"]
                            ):
                                _applied += 1
                        st.success(
                            f"✅ Holdings saved · {_applied}/{len(corrs)} realized-P&L "
                            "corrections applied. Refresh the Evening Debrief and "
                            "Trade Review to see the corrected numbers."
                        )
                        st.session_state.trades_df = db.load_trades()
                        # Invalidate the page-load drift banner — we just resolved it.
                        st.session_state.pop("_tj_drift_checked", None)
                        st.session_state.pop("_tj_drift_state", None)

    if not db.has_db():
        st.info(
            "💡 **Supabase not connected** — trades above are session-only and will be lost on refresh.  \n"
            "Run the SQL in `stock_analyzer/db.py` to create the `trades` table, "
            "then add your credentials to `.streamlit/secrets.toml`."
        )
    else:
        st.markdown(
            "<div style='font-size:0.78em;color:#444;margin-top:6px'>"
            "📌 To create the trades table in Supabase, run the SQL in "
            "<code>stock_analyzer/db.py</code> → Supabase SQL Editor → New Query</div>",
            unsafe_allow_html=True,
        )

# ═════════════════════════════════════════════════════════════════════════════
# PAGE — TRADE REVIEW (behavioural retrospective)
# ═════════════════════════════════════════════════════════════════════════════
elif page == "🪞 Trade Review":
    _fill_news_slot(_news_slot, st.session_state.get("_sidebar_news", []))
    st.title("🪞 Trade Review")
    st.caption(
        "Behavioural retrospective on every trade you've recorded. The system infers "
        "categories from the Journal columns (signal compliance) and market context "
        "(panic-day overlay) — no manual tagging. Use this to answer: *are app-followed "
        "trades outperforming the ones I made off external info?*"
    )

    # ── Look-back toggle ──────────────────────────────────────────────────────
    _tr_options = {"Last 2 weeks": 14, "Last 30 days": 30, "Last 60 days": 60, "Last 90 days": 90, "All time": 0}
    _tr_choice  = st.radio(
        "Look-back window",
        list(_tr_options.keys()),
        horizontal=True,
        index=0,
        key="_tr_lookback",
    )
    _tr_days = _tr_options[_tr_choice]

    # ── Pull data ─────────────────────────────────────────────────────────────
    _tr_trades_df = st.session_state.get("trades_df")
    if _tr_trades_df is None:
        _tr_trades_df = db.load_trades()
        st.session_state.trades_df = _tr_trades_df

    if _tr_trades_df is None or _tr_trades_df.empty:
        st.info(
            "No trades recorded yet. Once you've logged a few trades in the Journal, "
            "this page will analyze them."
        )
    else:
        # SPY history for panic-day flag + vs-SPY benchmark. Cached at the fetch_spy
        # level (data.py @ st.cache_data) so this is cheap on repeated renders.
        _tr_spy_period = "6mo" if _tr_days >= 90 or _tr_days == 0 else "3mo"
        try:
            _tr_spy_df = fetch_spy(period=_tr_spy_period)
        except Exception:
            _tr_spy_df = None

        # Current prices for open-position MTM. Reuse the Evening Debrief cache pattern.
        @st.cache_data(ttl=300, show_spinner=False)
        def _tr_fetch_prices(_tickers_key: tuple) -> dict:
            if not _tickers_key:
                return {}
            try:
                px = fetch_live_prices(list(_tickers_key))
                return {t: float(d.get("price", 0)) for t, d in px.items()}
            except Exception:
                return {}

        _tr_tickers = sorted({str(t).upper() for t in _tr_trades_df["ticker"].dropna().tolist()})
        _tr_prices  = _tr_fetch_prices(tuple(_tr_tickers))

        # Build the review payload
        _tr_data = build_trade_review(
            trades_df       = _tr_trades_df,
            current_prices  = _tr_prices,
            spy_history_df  = _tr_spy_df,
            today           = _TODAY_ET,
            lookback_days   = _tr_days,
        )

        _tr_trades = _tr_data["trades"]
        _tr_m      = _tr_data["metrics"]

        # ── Window context strip ──────────────────────────────────────────────
        st.markdown(
            f"<div style='color:#94a3b8;font-size:0.85em;margin:8px 0 12px'>"
            f"Window: <b>{_tr_data['window_start']}</b> → <b>{_tr_data['window_end']}</b> · "
            f"{_tr_m['overall']['n_trades']} trades · {_tr_m['overall']['n_judged']} judged "
            f"({_tr_m['overall']['n_trades'] - _tr_m['overall']['n_judged']} still open and unpriced)"
            f"</div>",
            unsafe_allow_html=True,
        )

        # ── Compute Batch 2 analytics (trends, risk discipline, sector mix) ───
        # Cumulative P&L and rolling win rate over time — trend visibility
        _tr_cum_series = cumulative_pnl_series(_tr_trades)
        _tr_roll_wr    = rolling_win_rate(_tr_trades, window=5)

        # Position-size discipline vs SINGLE_NAME_CEILING using CURRENT portfolio
        # value as the budget reference. Approximation — see function docstring.
        # Pulled from session_state because the Home page is what computes it;
        # Trade Review is a different page and doesn't recompute total_val.
        _tr_port_val = float(st.session_state.get("_portfolio_value", 0) or 0)
        _tr_pos_disc = position_size_discipline(_tr_trades, _tr_port_val)

        # Sector mix from a {ticker: sector} map assembled from any source the
        # app already has handy (port_df → scanner_results → fallback "Other").
        _tr_t2s: dict[str, str] = {}
        try:
            _pf = st.session_state.get("_last_port_df")
            if _pf is not None and not _pf.empty and "Sector" in _pf.columns:
                for _, _r in _pf.iterrows():
                    _tr_t2s[str(_r["Ticker"]).upper()] = str(_r.get("Sector", "") or "Other")
        except Exception:
            pass
        try:
            _sr = st.session_state.get("scanner_results")
            if _sr is not None and not _sr.empty and "Sector" in _sr.columns:
                for _, _r in _sr.iterrows():
                    _tk = str(_r["Ticker"]).upper()
                    if _tk not in _tr_t2s:
                        _tr_t2s[_tk] = str(_r.get("Sector", "") or "Other")
        except Exception:
            pass
        _tr_sec_mix = sector_mix(_tr_trades, _tr_t2s)

        # ── 💡 What the Data Says — rule-based synthesis ──────────────────────
        # Turns the raw numbers into a verdict + concrete findings + one next
        # move. Designed to answer the user's "what should I make of this?" — no
        # interpretation required.
        _tr_insights = build_insights(
            _tr_m, _tr_trades,
            position_discipline = _tr_pos_disc,
            sector_mix_data     = _tr_sec_mix,
            rolling_wr_series   = _tr_roll_wr,
        )

        _ins_findings_html = ""
        if _tr_insights["findings"]:
            _ins_findings_html = "".join(
                f"<li style='margin:4px 0;color:#e2e8f0;font-size:0.9em'>{f}</li>"
                for f in _tr_insights["findings"]
            )
            _ins_findings_html = (
                f"<ul style='margin:8px 0 6px 18px;padding:0'>{_ins_findings_html}</ul>"
            )

        _ins_next_html = ""
        if _tr_insights["next_move"]:
            _ins_next_html = (
                f"<div style='background:#1e293b;border-radius:6px;padding:8px 12px;"
                f"margin-top:8px;color:#cbd5e1;font-size:0.88em'>"
                f"<b style='color:{_tr_insights['verdict_color']}'>➡ Next move:</b> "
                f"{_tr_insights['next_move']}"
                f"</div>"
            )

        st.markdown(
            f"<div style='background:#0f172a;border-left:4px solid {_tr_insights['verdict_color']};"
            f"border-radius:8px;padding:12px 18px;margin-bottom:14px'>"
            f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
            f"<div style='color:#f9fafb;font-size:1.0em;font-weight:700'>"
            f"💡 What the Data Says</div>"
            f"<div style='color:{_tr_insights['verdict_color']};font-weight:700;font-size:0.95em'>"
            f"{_tr_insights['verdict_label']}</div>"
            f"</div>"
            f"<div style='color:#94a3b8;font-size:0.85em;margin-top:4px;font-style:italic'>"
            f"{_tr_insights['verdict_msg']}</div>"
            + _ins_findings_html
            + _ins_next_html
            + "</div>",
            unsafe_allow_html=True,
        )

        if not _tr_data["spy_available"]:
            st.warning(
                "⚠ SPY history unavailable right now — panic-day flags and vs-SPY "
                "benchmarks will be missing. Try refreshing in a minute."
            )

        def _fmt_win_rate(m):
            if m["n_judged"] == 0:
                return "—"
            return f"{m['win_rate']:.0f}% · {m['n_wins']}/{m['n_judged']}"

        _hc1, _hc2, _hc3 = st.columns(3)
        with _hc1:
            _m = _tr_m["app_followed"]
            _color = "#22c55e" if (_m["win_rate"] or 0) >= 60 else "#f59e0b" if (_m["win_rate"] or 0) >= 40 else "#94a3b8"
            st.markdown(
                f"<div style='background:#052e16;border-left:4px solid {_color};"
                f"border-radius:8px;padding:12px 16px;margin-bottom:8px'>"
                f"<div style='color:#86efac;font-size:0.78em;font-weight:700;letter-spacing:0.06em;text-transform:uppercase'>"
                f"✓ App-Followed</div>"
                f"<div style='color:#f9fafb;font-size:1.7em;font-weight:700;margin-top:4px'>{_fmt_win_rate(_m)}</div>"
                f"<div style='color:#cbd5e1;font-size:0.82em;margin-top:4px'>"
                f"Net P&L: <b style='color:{'#86efac' if _m['net_pnl'] >= 0 else '#fca5a5'}'>"
                f"${_m['net_pnl']:+,.0f}</b> · {_m['n_trades']} total</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with _hc2:
            _m = _tr_m["deviated"]
            _color = "#22c55e" if (_m["win_rate"] or 0) >= 60 else "#f59e0b" if (_m["win_rate"] or 0) >= 40 else "#ef4444"
            st.markdown(
                f"<div style='background:#1c1917;border-left:4px solid {_color};"
                f"border-radius:8px;padding:12px 16px;margin-bottom:8px'>"
                f"<div style='color:#fca5a5;font-size:0.78em;font-weight:700;letter-spacing:0.06em;text-transform:uppercase'>"
                f"✗ Deviated (external)</div>"
                f"<div style='color:#f9fafb;font-size:1.7em;font-weight:700;margin-top:4px'>{_fmt_win_rate(_m)}</div>"
                f"<div style='color:#cbd5e1;font-size:0.82em;margin-top:4px'>"
                f"Net P&L: <b style='color:{'#86efac' if _m['net_pnl'] >= 0 else '#fca5a5'}'>"
                f"${_m['net_pnl']:+,.0f}</b> · {_m['n_trades']} total</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with _hc3:
            _m = _tr_m["panic_window"]
            if _m["n_trades"] == 0:
                st.markdown(
                    f"<div style='background:#0f172a;border-left:4px solid #334155;"
                    f"border-radius:8px;padding:12px 16px;margin-bottom:8px'>"
                    f"<div style='color:#94a3b8;font-size:0.78em;font-weight:700;letter-spacing:0.06em;text-transform:uppercase'>"
                    f"🌪 Panic-Window Trades</div>"
                    f"<div style='color:#cbd5e1;font-size:1.05em;font-weight:600;margin-top:6px'>None in window</div>"
                    f"<div style='color:#94a3b8;font-size:0.78em;margin-top:4px'>Trades made on days S&P ≤ -1.5%.</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                _color = "#22c55e" if (_m["win_rate"] or 0) >= 60 else "#f59e0b" if (_m["win_rate"] or 0) >= 40 else "#ef4444"
                st.markdown(
                    f"<div style='background:#3f1d1d;border-left:4px solid {_color};"
                    f"border-radius:8px;padding:12px 16px;margin-bottom:8px'>"
                    f"<div style='color:#fca5a5;font-size:0.78em;font-weight:700;letter-spacing:0.06em;text-transform:uppercase'>"
                    f"🌪 Panic-Window Trades</div>"
                    f"<div style='color:#f9fafb;font-size:1.7em;font-weight:700;margin-top:4px'>{_fmt_win_rate(_m)}</div>"
                    f"<div style='color:#cbd5e1;font-size:0.82em;margin-top:4px'>"
                    f"Net P&L: <b style='color:{'#86efac' if _m['net_pnl'] >= 0 else '#fca5a5'}'>"
                    f"${_m['net_pnl']:+,.0f}</b> · {_m['n_trades']} total · S&P ≤ -1.5% days</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # ── 📈 Performance Trends — visual answer to "right direction?" ──────
        # Default-expanded so the user sees the trajectory at a glance without
        # having to scroll past the per-trade list. The detailed scorecard
        # lives at the bottom of the page in a collapsed expander.
        with st.expander("📈 Performance Trends", expanded=True):
            if len(_tr_cum_series) < 2:
                st.caption(
                    "Need at least 2 judged trades in this window to plot the trend. "
                    "Charts will populate as you accumulate more history."
                )
            else:
                # Cumulative P&L line chart
                st.markdown("**Cumulative P&L over window**")
                _fig_cum = go.Figure()
                _fig_cum.add_trace(go.Scatter(
                    x = [p["date"] for p in _tr_cum_series],
                    y = [p["cumulative_pnl"] for p in _tr_cum_series],
                    mode = "lines+markers",
                    line = dict(color="#a78bfa", width=2),
                    marker = dict(size=7),
                    hovertemplate = (
                        "<b>%{customdata[0]}</b> %{customdata[1]}<br>"
                        "Trade P&L: $%{customdata[2]:+,.0f}<br>"
                        "Cumulative: $%{y:+,.0f}<extra></extra>"
                    ),
                    customdata = [
                        [p["ticker"], p["action"], p["outcome_pnl"]]
                        for p in _tr_cum_series
                    ],
                    name = "Cumulative P&L",
                ))
                _fig_cum.add_hline(y=0, line_dash="dot", line_color="#64748b")
                _fig_cum.update_layout(
                    height=260, margin=dict(l=10, r=10, t=10, b=10),
                    paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                    font=dict(color="#cbd5e1"),
                    xaxis=dict(gridcolor="#1e293b"),
                    yaxis=dict(gridcolor="#1e293b", title="Cumulative $ P&L"),
                    showlegend=False,
                )
                st.plotly_chart(_fig_cum, use_container_width=True)

                # Rolling win rate
                if _tr_roll_wr:
                    st.markdown(f"**Rolling {_tr_roll_wr[0]['window']}-trade win rate**")
                    _fig_wr = go.Figure()
                    _fig_wr.add_trace(go.Scatter(
                        x = [p["date"] for p in _tr_roll_wr],
                        y = [p["win_rate"] for p in _tr_roll_wr],
                        mode = "lines+markers",
                        line = dict(color="#22c55e", width=2),
                        marker = dict(size=7),
                        hovertemplate = "Win rate: %{y:.0f}%<extra></extra>",
                    ))
                    _fig_wr.add_hline(y=50, line_dash="dot", line_color="#64748b",
                                      annotation_text="50%", annotation_position="right")
                    _fig_wr.update_layout(
                        height=220, margin=dict(l=10, r=10, t=10, b=10),
                        paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                        font=dict(color="#cbd5e1"),
                        xaxis=dict(gridcolor="#1e293b"),
                        yaxis=dict(gridcolor="#1e293b", title="Win rate %", range=[0, 100]),
                        showlegend=False,
                    )
                    st.plotly_chart(_fig_wr, use_container_width=True)
                else:
                    st.caption(
                        "Rolling 5-trade win rate needs at least 5 judged trades — "
                        "currently below threshold."
                    )

        # ── ⚖ Risk Discipline — well-managed risk? ───────────────────────────
        with st.expander("⚖ Risk Discipline", expanded=True):
            if _tr_pos_disc["n_trades"] == 0:
                st.caption(
                    "Position-size discipline requires a portfolio value (open the "
                    "Home page first to seed it) and at least one BUY trade in the "
                    "window."
                )
            else:
                _pd_c1, _pd_c2, _pd_c3 = st.columns(3)
                _pd_c1.metric(
                    "Trades over ceiling",
                    f"{_tr_pos_disc['n_over_ceiling']} / {_tr_pos_disc['n_trades']}",
                    help=f"Trades where cost exceeded {_tr_pos_disc['ceiling_threshold']:.0f}% of current portfolio",
                )
                _pd_c2.metric("Avg position size", f"{_tr_pos_disc['avg_size_pct']:.1f}%")
                _pd_c3.metric("Max position size", f"{_tr_pos_disc['max_size_pct']:.1f}%")

                st.markdown("**Per-trade position sizes** (sorted largest first)")
                for _r in _tr_pos_disc["trades"][:10]:
                    _bar_pct = min(100.0, _r["size_pct"] / max(_tr_pos_disc["ceiling_threshold"] * 1.5, 1) * 100)
                    _bar_color = "#ef4444" if _r["over_ceiling"] else "#64748b"
                    _flag = (
                        f" <span style='color:#fca5a5;font-size:0.78em;font-weight:700'>"
                        f"⚠ OVER {_tr_pos_disc['ceiling_threshold']:.0f}% CEILING</span>"
                        if _r["over_ceiling"] else ""
                    )
                    _dt_str = _r["date"].isoformat() if hasattr(_r["date"], "isoformat") else str(_r["date"])
                    st.markdown(
                        f"<div style='margin-bottom:6px'>"
                        f"<div style='display:flex;justify-content:space-between'>"
                        f"<span style='color:#cbd5e1;font-size:0.85em'>"
                        f"<b>{_r['ticker']}</b> · {_dt_str} · ${_r['size_dollars']:,.0f}{_flag}</span>"
                        f"<span style='color:#cbd5e1;font-size:0.85em;font-weight:600'>{_r['size_pct']:.1f}%</span>"
                        f"</div>"
                        f"<div style='height:6px;background:#1e293b;border-radius:3px;margin-top:2px;overflow:hidden'>"
                        f"<div style='height:6px;width:{_bar_pct:.1f}%;background:{_bar_color}'></div>"
                        f"</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            st.markdown("---")
            st.markdown("**Sector concentration of trades**")
            if _tr_sec_mix["n_sectors"] == 0:
                st.caption("No BUY trades to analyze sector concentration.")
            elif _tr_sec_mix["n_sectors"] == 1:
                _s = _tr_sec_mix["sectors"][0]
                st.warning(
                    f"All {_s['n_trades']} BUY trades are in **{_s['sector']}** "
                    f"(100% concentration). Consider diversifying entries across sectors."
                )
            else:
                _fig_sec = go.Figure()
                _fig_sec.add_trace(go.Bar(
                    x=[s["pct_of_trades"] for s in _tr_sec_mix["sectors"]],
                    y=[s["sector"] for s in _tr_sec_mix["sectors"]],
                    orientation="h",
                    marker_color=[
                        "#ef4444" if s["over_elevated"] else "#3b82f6"
                        for s in _tr_sec_mix["sectors"]
                    ],
                    text=[
                        f"{s['n_trades']} ({s['pct_of_trades']:.0f}%)"
                        for s in _tr_sec_mix["sectors"]
                    ],
                    textposition="auto",
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "%{customdata[0]} trades · $%{customdata[1]:,.0f}<extra></extra>"
                    ),
                    customdata=[
                        [s["n_trades"], s["dollars"]]
                        for s in _tr_sec_mix["sectors"]
                    ],
                ))
                _fig_sec.add_vline(
                    x=_tr_sec_mix["elevated_threshold"],
                    line_dash="dot", line_color="#f59e0b",
                    annotation_text=f"{_tr_sec_mix['elevated_threshold']:.0f}% warn",
                    annotation_position="top",
                )
                _fig_sec.update_layout(
                    height=max(180, 35 * len(_tr_sec_mix["sectors"])),
                    margin=dict(l=10, r=10, t=20, b=10),
                    paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                    font=dict(color="#cbd5e1"),
                    xaxis=dict(gridcolor="#1e293b", title="% of trades", range=[0, 100]),
                    yaxis=dict(gridcolor="#1e293b"),
                    showlegend=False,
                )
                st.plotly_chart(_fig_sec, use_container_width=True)
                if _tr_sec_mix.get("top_sector_pct") and _tr_sec_mix["top_sector_pct"] > _tr_sec_mix["elevated_threshold"]:
                    st.caption(
                        f"⚠ **{_tr_sec_mix['top_sector']}** is above the "
                        f"{_tr_sec_mix['elevated_threshold']:.0f}% concentration warn level. "
                        "Over-fishing one sector means a single regime shift hits multiple positions."
                    )

        # ── 🎯 Course-Correction Recommendations (Lens 3) ─────────────────────
        # Each rec is a sample-size-guarded behavioural diagnostic — fires only
        # when YOUR data supports the conclusion. Severity-sorted (critical
        # first) so the most important course correction is at the top. Each
        # card has a toggle to expand the trades that triggered the finding.
        _tr_recs = build_recommendations(_tr_trades)

        # ── Evidence-renderer helpers — one per pattern_key ─────────────────
        # All render compact one-line-per-trade rows so the inline expansion
        # stays scannable. Receive the `related_trades` payload from the rec.
        def _tr_evidence_row(t: dict, *, show_vs_spy: bool = False,
                             show_hold: bool = False, show_signal: bool = False,
                             extra_chip: str = "") -> str:
            _pnl_col   = "#86efac" if t["outcome_pnl"] > 0 else "#fca5a5" if t["outcome_pnl"] < 0 else "#cbd5e1"
            _act_col   = "#22c55e" if "BUY" in t["action"].upper() else "#ef4444"
            _dt_str    = t["date"].isoformat() if hasattr(t["date"], "isoformat") else str(t["date"])
            _extras    = []
            if show_hold and t.get("hold_days") is not None:
                _extras.append(f"{t['hold_days']}d held")
            if show_vs_spy and t.get("vs_spy_pct") is not None:
                _vs_col = "#86efac" if t["vs_spy_pct"] >= 0 else "#fca5a5"
                _extras.append(f"vs SPY <b style='color:{_vs_col}'>{t['vs_spy_pct']:+.2f}%</b>")
            if show_signal and t.get("signal_seen"):
                _extras.append(f"signal seen: <i>{_html.escape(t['signal_seen'][:32])}</i>")
            if t.get("panic_window"):
                _extras.append("🌪 panic day")
            _extra_str = " · ".join(_extras)
            if _extra_str:
                _extra_str = f" · {_extra_str}"
            _chip = f" {extra_chip}" if extra_chip else ""
            return (
                f"<div style='background:#0f172a;border-radius:4px;"
                f"padding:6px 10px;margin-bottom:4px;color:#cbd5e1;font-size:0.82em'>"
                f"<span style='color:{_act_col};font-weight:600'>{t['action']}</span> "
                f"<b>{t['ticker']}</b> · "
                f"{t['shares']:.0f}sh @ ${t['price']:.2f} · "
                f"<b style='color:{_pnl_col}'>${t['outcome_pnl']:+,.0f} "
                f"({t['outcome_pct']:+.2f}%)</b>"
                f"{_extra_str}"
                f" <span style='color:#64748b'>· {_dt_str}</span>{_chip}"
                f"</div>"
            )

        def _tr_render_evidence(rec: dict):
            pk = rec.get("pattern_key", "")
            rt = rec.get("related_trades", {}) or {}

            if pk == "holding_period_imbalance":
                _wins   = rt.get("wins", [])
                _losses = rt.get("losses", [])
                _e1, _e2 = st.columns(2)
                with _e1:
                    st.markdown(
                        f"<div style='color:#86efac;font-weight:700;font-size:0.85em;"
                        f"margin-bottom:6px'>Winners — sorted by hold days (desc)</div>",
                        unsafe_allow_html=True,
                    )
                    for _t in _wins:
                        st.markdown(_tr_evidence_row(_t, show_hold=True),
                                    unsafe_allow_html=True)
                with _e2:
                    st.markdown(
                        f"<div style='color:#fca5a5;font-weight:700;font-size:0.85em;"
                        f"margin-bottom:6px'>Losers — sorted by hold days (desc)</div>",
                        unsafe_allow_html=True,
                    )
                    for _t in _losses:
                        st.markdown(_tr_evidence_row(_t, show_hold=True),
                                    unsafe_allow_html=True)

            elif pk == "signal_defying_bias":
                _def  = rt.get("defying",   [])
                _comp = rt.get("compliant", [])
                st.markdown(
                    f"<div style='color:#fca5a5;font-weight:700;font-size:0.85em;"
                    f"margin-bottom:6px'>Defying trades ({len(_def)})</div>",
                    unsafe_allow_html=True,
                )
                for _t in _def:
                    st.markdown(_tr_evidence_row(_t, show_signal=True),
                                unsafe_allow_html=True)
                if _comp:
                    st.markdown(
                        f"<div style='color:#86efac;font-weight:700;font-size:0.85em;"
                        f"margin-top:10px;margin-bottom:6px'>Compliant comparison "
                        f"({len(_comp)} top performers)</div>",
                        unsafe_allow_html=True,
                    )
                    for _t in _comp:
                        st.markdown(_tr_evidence_row(_t, show_signal=True),
                                    unsafe_allow_html=True)

            elif pk == "vs_spy_drag":
                _trades = rt.get("trades", [])
                _losers = [t for t in _trades if (t.get("vs_spy_pct") or 0) < 0]
                _beats  = [t for t in _trades if (t.get("vs_spy_pct") or 0) >= 0]
                if _losers:
                    st.markdown(
                        f"<div style='color:#fca5a5;font-weight:700;font-size:0.85em;"
                        f"margin-bottom:6px'>Underperformed SPY ({len(_losers)})</div>",
                        unsafe_allow_html=True,
                    )
                    for _t in _losers:
                        st.markdown(_tr_evidence_row(_t, show_vs_spy=True, show_hold=True),
                                    unsafe_allow_html=True)
                if _beats:
                    st.markdown(
                        f"<div style='color:#86efac;font-weight:700;font-size:0.85em;"
                        f"margin-top:10px;margin-bottom:6px'>Beat SPY ({len(_beats)})</div>",
                        unsafe_allow_html=True,
                    )
                    for _t in _beats:
                        st.markdown(_tr_evidence_row(_t, show_vs_spy=True, show_hold=True),
                                    unsafe_allow_html=True)

            elif pk == "trigger_type_effectiveness":
                _by_trig = rt.get("by_trigger", {}) or {}
                _best    = rt.get("best_trigger")
                _worst   = rt.get("worst_trigger")
                _stats   = rt.get("stats", {}) or {}
                # Order: worst first (so the problem is at the top), best last
                _order = []
                if _worst and _worst in _by_trig:
                    _order.append(_worst)
                for _tr in _by_trig:
                    if _tr not in (_worst, _best):
                        _order.append(_tr)
                if _best and _best != _worst and _best in _by_trig:
                    _order.append(_best)
                for _tr in _order:
                    _ts = _by_trig.get(_tr, [])
                    if not _ts:
                        continue
                    _s = _stats.get(_tr, {})
                    _chip_col = ("#ef4444" if _tr == _worst else
                                 "#86efac" if _tr == _best  else "#94a3b8")
                    _wr   = _s.get("win_rate", 0)
                    _n    = _s.get("n_trades", len(_ts))
                    _w    = _s.get("n_wins", 0)
                    _net  = _s.get("net_pnl", 0)
                    _net_col = "#86efac" if _net >= 0 else "#fca5a5"
                    st.markdown(
                        f"<div style='color:{_chip_col};font-weight:700;font-size:0.85em;"
                        f"margin:10px 0 4px'>{_tr} — "
                        f"<span style='font-weight:400'>{_w}/{_n} wins · "
                        f"<b>{_wr:.0f}%</b> win rate · net "
                        f"<b style='color:{_net_col}'>${_net:+,.0f}</b></span></div>",
                        unsafe_allow_html=True,
                    )
                    for _t in _ts:
                        st.markdown(_tr_evidence_row(_t), unsafe_allow_html=True)

            elif pk == "lesson_capture_rate":
                _wl  = rt.get("with_lesson",    [])
                _wol = rt.get("without_lesson", [])
                _e1, _e2 = st.columns(2)
                with _e1:
                    st.markdown(
                        f"<div style='color:#86efac;font-weight:700;font-size:0.85em;"
                        f"margin-bottom:6px'>With lesson ({len(_wl)})</div>",
                        unsafe_allow_html=True,
                    )
                    if not _wl:
                        st.caption("None in window.")
                    for _t in _wl:
                        st.markdown(_tr_evidence_row(_t), unsafe_allow_html=True)
                with _e2:
                    st.markdown(
                        f"<div style='color:#94a3b8;font-weight:700;font-size:0.85em;"
                        f"margin-bottom:6px'>Without lesson ({len(_wol)})</div>",
                        unsafe_allow_html=True,
                    )
                    if not _wol:
                        st.caption("None in window.")
                    for _t in _wol:
                        st.markdown(_tr_evidence_row(_t), unsafe_allow_html=True)

            elif pk == "day_of_week_timing":
                _by_wd = rt.get("by_weekday", {}) or {}
                _best_d  = rt.get("best_day")
                _worst_d = rt.get("worst_day")
                _stats_d = rt.get("stats", {}) or {}
                _order_d = []
                if _worst_d and _worst_d in _by_wd:
                    _order_d.append(_worst_d)
                for _day in _by_wd:
                    if _day not in (_worst_d, _best_d):
                        _order_d.append(_day)
                if _best_d and _best_d != _worst_d and _best_d in _by_wd:
                    _order_d.append(_best_d)
                for _day in _order_d:
                    _ts = _by_wd.get(_day, [])
                    if not _ts:
                        continue
                    _s = _stats_d.get(_day, {})
                    _chip_col = ("#ef4444" if _day == _worst_d else
                                 "#86efac" if _day == _best_d  else "#94a3b8")
                    _wr   = _s.get("win_rate", 0)
                    _n    = _s.get("n_trades", len(_ts))
                    _w    = _s.get("n_wins", 0)
                    _net  = _s.get("net_pnl", 0)
                    _net_col = "#86efac" if _net >= 0 else "#fca5a5"
                    st.markdown(
                        f"<div style='color:{_chip_col};font-weight:700;font-size:0.85em;"
                        f"margin:10px 0 4px'>{_day} — "
                        f"<span style='font-weight:400'>{_w}/{_n} wins · "
                        f"<b>{_wr:.0f}%</b> win rate · net "
                        f"<b style='color:{_net_col}'>${_net:+,.0f}</b></span></div>",
                        unsafe_allow_html=True,
                    )
                    for _t in _ts:
                        st.markdown(_tr_evidence_row(_t), unsafe_allow_html=True)

            elif pk == "re_entered_tickers":
                _neg = rt.get("negative_groups", [])
                _pos = rt.get("positive_groups", [])
                if _neg:
                    st.markdown(
                        f"<div style='color:#fca5a5;font-weight:700;font-size:0.85em;"
                        f"margin-bottom:6px'>Net-negative re-entries</div>",
                        unsafe_allow_html=True,
                    )
                    for _g in _neg:
                        st.markdown(
                            f"<div style='color:#f9fafb;font-weight:700;font-size:0.88em;"
                            f"margin:8px 0 4px'>{_g['ticker']} — "
                            f"<span style='color:#fca5a5'>{_g['n_trades']}× = "
                            f"${_g['net_pnl']:+,.0f}</span></div>",
                            unsafe_allow_html=True,
                        )
                        for _t in _g.get("trades", []):
                            st.markdown(_tr_evidence_row(_t), unsafe_allow_html=True)
                if _pos:
                    st.markdown(
                        f"<div style='color:#86efac;font-weight:700;font-size:0.85em;"
                        f"margin-top:10px;margin-bottom:6px'>Net-positive re-entries</div>",
                        unsafe_allow_html=True,
                    )
                    for _g in _pos[:3]:    # cap positive at 3 for brevity
                        st.markdown(
                            f"<div style='color:#f9fafb;font-weight:700;font-size:0.88em;"
                            f"margin:8px 0 4px'>{_g['ticker']} — "
                            f"<span style='color:#86efac'>{_g['n_trades']}× = "
                            f"${_g['net_pnl']:+,.0f}</span></div>",
                            unsafe_allow_html=True,
                        )
                        for _t in _g.get("trades", []):
                            st.markdown(_tr_evidence_row(_t), unsafe_allow_html=True)
            else:
                st.caption("No detail view registered for this diagnostic.")

        def _tr_evidence_count(rec: dict) -> int:
            """How many trades the 'Show trades' button surface for this rec."""
            rt = rec.get("related_trades", {}) or {}
            pk = rec.get("pattern_key", "")
            if pk == "holding_period_imbalance":
                return len(rt.get("wins", [])) + len(rt.get("losses", []))
            if pk == "signal_defying_bias":
                return len(rt.get("defying", [])) + len(rt.get("compliant", []))
            if pk == "vs_spy_drag":
                return len(rt.get("trades", []))
            if pk == "re_entered_tickers":
                return (sum(len(g.get("trades", [])) for g in rt.get("negative_groups", []))
                        + sum(len(g.get("trades", [])) for g in rt.get("positive_groups", [])))
            if pk == "trigger_type_effectiveness":
                return sum(len(ts) for ts in rt.get("by_trigger", {}).values())
            if pk == "lesson_capture_rate":
                return len(rt.get("with_lesson", [])) + len(rt.get("without_lesson", []))
            if pk == "day_of_week_timing":
                return sum(len(ts) for ts in rt.get("by_weekday", {}).values())
            return 0

        with st.expander(
            f"🎯 Course-Correction Recommendations ({len(_tr_recs)} active)",
            expanded=True,
        ):
            if not _tr_recs:
                st.caption(
                    "No behavioural patterns detected yet — each diagnostic needs a "
                    "minimum sample size before it fires (e.g. holding-period imbalance "
                    "needs ≥3 wins and ≥3 losses). Keep logging trades and these will "
                    "populate over time."
                )
            else:
                _sev_styles = {
                    "critical": ("🔴", "#ef4444", "#3f1d1d", "Fix Now"),
                    "watch":    ("🟡", "#f59e0b", "#3b2a0a", "Watch"),
                    "good":     ("🟢", "#22c55e", "#052e16", "Doing Well"),
                }
                for _i, _rec in enumerate(_tr_recs):
                    _icon, _col, _bg, _sev_label = _sev_styles.get(
                        _rec["severity"], _sev_styles["watch"]
                    )
                    _action_block = ""
                    if _rec.get("action"):
                        _action_block = (
                            f"<div style='background:#1e293b;border-radius:6px;"
                            f"padding:8px 12px;margin-top:8px;color:#cbd5e1;font-size:0.86em'>"
                            f"<b style='color:{_col}'>➡ Action:</b> {_rec['action']}"
                            f"</div>"
                        )
                    st.markdown(
                        f"<div style='background:{_bg};border-left:4px solid {_col};"
                        f"border-radius:8px;padding:12px 16px;margin-bottom:4px'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
                        f"<div style='color:#f9fafb;font-weight:700;font-size:1.0em'>"
                        f"{_icon} {_rec['pattern']}</div>"
                        f"<div style='color:{_col};font-size:0.78em;font-weight:700;"
                        f"letter-spacing:0.06em;text-transform:uppercase'>{_sev_label}</div>"
                        f"</div>"
                        f"<div style='color:#e2e8f0;font-size:0.9em;margin-top:6px'>"
                        f"{_rec['detection']}</div>"
                        + _action_block +
                        "</div>",
                        unsafe_allow_html=True,
                    )
                    # Evidence toggle — session-state-backed because nesting
                    # st.expander inside an outer expander isn't allowed.
                    _n_evidence = _tr_evidence_count(_rec)
                    if _n_evidence > 0:
                        _ev_key   = f"_tr_rec_show_{_rec['pattern_key']}_{_i}"
                        _is_open  = st.session_state.get(_ev_key, False)
                        _btn_label = (
                            f"▾ Hide trades ({_n_evidence})" if _is_open
                            else f"🔍 Show trades behind this ({_n_evidence})"
                        )
                        if st.button(_btn_label, key=_ev_key + "_btn"):
                            st.session_state[_ev_key] = not _is_open
                            st.rerun()
                        if _is_open:
                            st.markdown(
                                f"<div style='background:#020617;border-left:2px solid {_col};"
                                f"border-radius:6px;padding:10px 14px;margin:0 0 10px 14px'>",
                                unsafe_allow_html=True,
                            )
                            _tr_render_evidence(_rec)
                            st.markdown("</div>", unsafe_allow_html=True)
                    else:
                        st.markdown("<div style='margin-bottom:10px'></div>",
                                    unsafe_allow_html=True)

        # ── 📜 Per-Trade Scorecard — detail view, collapsed by default ───────
        # Lives at the bottom of the page because the page-level question
        # ("how am I doing?") is answered by the synthesis card + trends above.
        # The scorecard is for drilling into individual trades — open when you
        # need to audit a specific entry/exit.
        with st.expander(
            f"📜 Per-Trade Scorecard ({len(_tr_trades) if _tr_trades else 0} trades) — click to expand",
            expanded=False,
        ):
            if not _tr_trades:
                st.caption("No trades in this window. Widen the look-back if you traded earlier.")
            else:
                _cat_labels = {
                    "app_followed":  ("✓ App-Followed",  "#22c55e", "#052e16"),
                    "deviated":      ("✗ Deviated",     "#ef4444", "#3f1d1d"),
                    "discretionary": ("— Discretionary", "#94a3b8", "#1c1917"),
                }
                for t in _tr_trades:
                    _cat_label, _cat_col, _cat_bg = _cat_labels.get(t["category"], _cat_labels["discretionary"])
                    _action_chip_col = "#22c55e" if "BUY" in t["action"].upper() else "#ef4444"

                    _pnl     = t["outcome_pnl"]
                    _pct     = t["outcome_pct"]
                    _pnl_col = "#86efac" if _pnl > 0 else "#fca5a5" if _pnl < 0 else "#cbd5e1"

                    _hold = t["hold_days"]
                    _hold_str = f"{_hold}d held" if _hold is not None else "—"

                    _vs_spy = t["vs_spy_pct"]
                    _vs_spy_str = ""
                    if _vs_spy is not None:
                        _vs_col = "#86efac" if _vs_spy >= 0 else "#fca5a5"
                        _vs_spy_str = (
                            f" · vs SPY <b style='color:{_vs_col}'>{_vs_spy:+.2f}%</b>"
                        )

                    _panic_chip = ""
                    if t["panic_window"]:
                        _sp = t.get("spy_pct_on_date")
                        _sp_str = f" (S&P {_sp:+.2f}%)" if _sp is not None else ""
                        _panic_chip = (
                            f"<span style='background:#7f1d1d;color:#fecaca;padding:1px 8px;"
                            f"border-radius:4px;font-size:0.74em;margin-left:6px'>"
                            f"🌪 Panic day{_sp_str}</span>"
                        )

                    if t["outcome_status"] == "open":
                        _status_chip = "<span style='color:#fbbf24;font-size:0.78em;font-weight:600'>OPEN</span>"
                    elif t["outcome_status"] == "partial":
                        _ss = t.get("shares_sold", 0) or 0
                        _sr = t.get("shares_remaining", 0) or 0
                        _status_chip = (
                            f"<span style='color:#a78bfa;font-size:0.78em;font-weight:600'>"
                            f"PARTIAL · {_ss:.0f}/{_ss + _sr:.0f} sold</span>"
                        )
                    else:
                        _status_chip = "<span style='color:#94a3b8;font-size:0.78em'>CLOSED</span>"

                    _win_chip = ""
                    if t["is_win"] is True:
                        _win_chip = "<span style='color:#86efac;font-weight:700'>✓ WIN</span>"
                    elif t["is_win"] is False:
                        _win_chip = "<span style='color:#fca5a5;font-weight:700'>✗ LOSS</span>"

                    _exit_str = ""
                    if t["outcome_status"] == "open" and t["exit_price"]:
                        _exit_str = f" · MTM ${t['exit_price']:.2f}"
                    elif t["outcome_status"] == "closed" and t["exit_price"]:
                        _exit_str = f" · Exit avg ${t['exit_price']:.2f}"
                    elif t["outcome_status"] == "partial" and t["exit_price"]:
                        _exit_str = f" · last sold ${t['exit_price']:.2f}"

                    # For partial closes, break out realized vs unrealized so the user can
                    # see why the total P&L is what it is (audit trail for the new math).
                    _split_note = ""
                    if t["outcome_status"] == "partial":
                        _real = t.get("realized_pnl", 0.0)
                        _unr  = t["outcome_pnl"] - _real
                        _real_col = "#86efac" if _real >= 0 else "#fca5a5"
                        _unr_col  = "#86efac" if _unr  >= 0 else "#fca5a5"
                        _split_note = (
                            f"<div style='color:#a78bfa;font-size:0.78em;margin-top:3px'>"
                            f"📦 Realized <b style='color:{_real_col}'>${_real:+,.0f}</b> "
                            f"on {t['shares_sold']:.0f} sh "
                            f"+ Unrealized <b style='color:{_unr_col}'>${_unr:+,.0f}</b> "
                            f"on {t['shares_remaining']:.0f} sh held"
                            f"</div>"
                        )

                    _dev_note = ""
                    if t["category"] == "deviated" and t.get("deviation_reason"):
                        _dev_note = (
                            f"<div style='color:#fca5a5;font-size:0.78em;margin-top:4px;font-style:italic'>"
                            f"Why deviated: {_html.escape(t['deviation_reason'][:140])}"
                            f"</div>"
                        )
                    _lesson_note = ""
                    if t.get("lesson"):
                        _lesson_note = (
                            f"<div style='color:#fde68a;font-size:0.78em;margin-top:4px;font-style:italic'>"
                            f"💡 Lesson: {_html.escape(t['lesson'][:140])}"
                            f"</div>"
                        )

                    st.markdown(
                        f"<div style='background:{_cat_bg};border-left:3px solid {_cat_col};"
                        f"border-radius:6px;padding:10px 14px;margin-bottom:8px'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
                        f"<div style='color:#f9fafb;font-weight:700;font-size:1.0em'>"
                        f"<span style='color:{_action_chip_col}'>{t['action']}</span> "
                        f"<b>{t['ticker']}</b> "
                        f"<span style='color:#94a3b8;font-weight:400;font-size:0.86em'>"
                        f"· {t['shares']:.0f} sh @ ${t['price']:.2f}{_exit_str}</span>"
                        f"{_panic_chip}"
                        f"</div>"
                        f"<div style='font-size:0.84em'>{_status_chip}</div>"
                        f"</div>"
                        f"<div style='color:#cbd5e1;font-size:0.85em;margin-top:6px'>"
                        f"P&L: <b style='color:{_pnl_col}'>${_pnl:+,.0f} ({_pct:+.2f}%)</b> · "
                        f"{_hold_str}{_vs_spy_str} · "
                        f"<span style='color:{_cat_col}'>{_cat_label}</span> · {_win_chip}"
                        f"</div>"
                        + _split_note
                        + _dev_note
                        + _lesson_note
                        + f"<div style='color:#64748b;font-size:0.74em;margin-top:4px'>"
                        f"Traded {t['_trade_date'].isoformat() if t.get('_trade_date') else '—'}"
                        + (f" · Signal seen: {_html.escape(t['signal_seen'])}" if t.get("signal_seen") else "")
                        + "</div>"
                        + "</div>",
                        unsafe_allow_html=True,
                    )

        st.markdown("---")
        st.caption(
            "**Categories:** *App-Followed* = `followed_signal=True` · *Deviated* = `followed_signal=False` "
            "(external info / discretionary call) · *Discretionary* = neither recorded. "
            "**Panic-window** = trade made on a day S&P 500 closed ≤ -1.5%. "
            "**vs-SPY** = trade's % return minus SPY's % over the same holding period (closed trades only). "
            "Open positions are marked-to-market against current price. "
            "**Position size** measured against current portfolio value as a proxy (journal doesn't store historical portfolio snapshots)."
        )

# ═════════════════════════════════════════════════════════════════════════════
# PAGE — RECOMMENDATIONS HISTORY
# ═════════════════════════════════════════════════════════════════════════════
elif page == "📜 Recommendations History":
    _fill_news_slot(_news_slot, st.session_state.get("_sidebar_news", []))
    st.title("📜 Recommendations History")
    st.caption(
        "Every pick Today's Brief has surfaced — captured at first sight. "
        "Cross-referenced with the Trade Journal to show what got acted on, "
        "what was missed, and how each played out. The substrate for spotting "
        "trends in your action discipline and the App's hit rate over time."
    )

    if not db.has_db():
        st.warning(
            "🟡 Supabase not connected — recommendations history requires the "
            "`recommendations` table. Run the SQL migration in `stock_analyzer/db.py` "
            "and set credentials in `.streamlit/secrets.toml`."
        )
        st.stop()

    from stock_analyzer.recommendations_history import (
        match_recs_to_trades,
        compute_outcomes,
        summary_stats,
        by_rec_type,
        by_composite_band,
        daily_volume,
    )
    import pandas as _rh_pd
    from datetime import date as _rh_date, timedelta as _rh_td

    # ── Filter bar ──────────────────────────────────────────────────────────
    _rh_c1, _rh_c2, _rh_c3 = st.columns([1.4, 1.2, 1.2])
    with _rh_c1:
        _rh_range_label = st.selectbox(
            "Date range",
            ["Last 7 days", "Last 30 days", "Last 90 days", "All time", "Custom"],
            index=1,
            key="_rh_range",
        )
    _rh_end   = _TODAY_ET
    if _rh_range_label == "Last 7 days":
        _rh_start = _rh_end - _rh_td(days=7)
    elif _rh_range_label == "Last 30 days":
        _rh_start = _rh_end - _rh_td(days=30)
    elif _rh_range_label == "Last 90 days":
        _rh_start = _rh_end - _rh_td(days=90)
    elif _rh_range_label == "All time":
        _rh_start = _rh_date(2020, 1, 1)
    else:   # Custom
        with _rh_c1:
            _rh_custom_range = st.date_input(
                "Custom range",
                value=(_rh_end - _rh_td(days=30), _rh_end),
                key="_rh_custom_range",
            )
        if isinstance(_rh_custom_range, (tuple, list)) and len(_rh_custom_range) == 2:
            _rh_start, _rh_end = _rh_custom_range[0], _rh_custom_range[1]
        else:
            _rh_start = _rh_end - _rh_td(days=30)
    with _rh_c2:
        _rh_type_filter = st.multiselect(
            "Rec type",
            ["new_pick", "add_winner", "buy_candidate"],
            default=["new_pick", "add_winner", "buy_candidate"],
            key="_rh_type_filter",
        )
    with _rh_c3:
        _rh_status_filter = st.selectbox(
            "Status",
            ["All", "Acted only", "Missed only"],
            index=0,
            key="_rh_status_filter",
        )

    # ── Load data ───────────────────────────────────────────────────────────
    with st.spinner("Loading recommendations history…"):
        _rh_recs_df   = db.load_recommendations(start_date=_rh_start, end_date=_rh_end)
        _rh_trades_df = st.session_state.get("trades_df")
        if _rh_trades_df is None:
            _rh_trades_df = db.load_trades()

    if _rh_recs_df is None or _rh_recs_df.empty:
        st.info(
            "No recommendations recorded for this range yet. As Today's Brief "
            "surfaces picks day after day, this page will fill in. "
            "(If you only set up the `recommendations` table recently, you'll "
            "have a few days of history at most.)"
        )
        st.stop()

    # Apply type filter at the DataFrame level for efficiency
    if _rh_type_filter:
        _rh_recs_df = _rh_recs_df[_rh_recs_df["rec_type"].isin(_rh_type_filter)]

    # ── Match against trades + compute outcomes ─────────────────────────────
    # Fetch live prices for every ticker in the filtered set so missed-rec
    # would-have-gained math has data to work with.
    _rh_tickers = sorted({
        str(t).strip().upper()
        for t in _rh_recs_df["ticker"].dropna().tolist()
        if str(t).strip()
    })
    _rh_prices: dict = {}
    if _rh_tickers:
        try:
            _px = fetch_live_prices(_rh_tickers)
            _rh_prices = {
                t: float(d.get("price", 0))
                for t, d in (_px or {}).items()
                if d and d.get("price")
            }
        except Exception:
            _rh_prices = {}

    _rh_matched   = match_recs_to_trades(_rh_recs_df, _rh_trades_df)
    _rh_enriched  = compute_outcomes(_rh_matched, _rh_prices, today=_TODAY_ET)

    # Status filter (after enrichment so the dropdown shows the right counts)
    if _rh_status_filter == "Acted only":
        _rh_enriched = [r for r in _rh_enriched if r["acted_on"]]
    elif _rh_status_filter == "Missed only":
        _rh_enriched = [r for r in _rh_enriched if not r["acted_on"]]

    if not _rh_enriched:
        st.info("No recommendations match the current filters.")
        st.stop()

    # ── Headline metrics ────────────────────────────────────────────────────
    _rh_stats = summary_stats(_rh_enriched)
    _rh_m1, _rh_m2, _rh_m3, _rh_m4 = st.columns(4)
    _rh_m1.metric(
        "Total recs",
        f"{_rh_stats['n_total']:,}",
        help="Distinct picks surfaced in the selected range.",
    )
    _rh_m2.metric(
        "Action rate",
        f"{_rh_stats['action_rate']:.0f}%" if _rh_stats['action_rate'] is not None else "—",
        f"{_rh_stats['n_acted']:,} acted",
        help="Acted = same-day trade with trigger_type='RECOMMENDATION'.",
    )
    _rh_m3.metric(
        "Avg acted outcome",
        f"{_rh_stats['avg_acted_pct']:+.1f}%" if _rh_stats['avg_acted_pct'] is not None else "—",
        help="Mean outcome_pct across acted recs with priced outcomes (BUY MTM, SELL realized).",
    )
    _rh_m4.metric(
        "Missed alpha",
        f"{_rh_stats['missed_alpha']:+.1f}pp" if _rh_stats['missed_alpha'] is not None else "—",
        help=(
            "Avg missed-rec would-have-gained minus avg acted-rec outcome. "
            "Positive = you'd have done better acting more often. "
            "Negative = your discretion to skip was earning its keep."
        ),
    )

    # Best / Worst banner
    _best  = _rh_stats.get("best")
    _worst = _rh_stats.get("worst")
    if _best and _worst:
        st.markdown(
            f"<div style='background:#0f172a;border:1px solid #334155;border-radius:8px;"
            f"padding:10px 14px;margin:8px 0;color:#cbd5e1;font-size:0.92em'>"
            f"🏆 <b>Best:</b> {_best['ticker']} on {_best['rec_date']} → "
            f"<span style='color:#86efac'>{_best['outcome_pct']:+.2f}%</span> "
            f"({'acted' if _best['acted_on'] else 'missed'})"
            f"  ·  💔 <b>Worst:</b> {_worst['ticker']} on {_worst['rec_date']} → "
            f"<span style='color:#fca5a5'>{_worst['outcome_pct']:+.2f}%</span> "
            f"({'acted' if _worst['acted_on'] else 'missed'})"
            f"</div>",
            unsafe_allow_html=True,
        )

    # ── Trends ──────────────────────────────────────────────────────────────
    with st.expander("📈 Trends — recs over time & action discipline", expanded=True):
        _rh_daily = daily_volume(_rh_enriched)
        if _rh_daily:
            _rh_dv_df = _rh_pd.DataFrame(_rh_daily)
            _rh_dv_df["date"] = _rh_pd.to_datetime(_rh_dv_df["date"])
            _rh_dv_df = _rh_dv_df.set_index("date")[["acted", "missed"]]
            st.bar_chart(_rh_dv_df, height=240)
            st.caption(
                "Daily recs surfaced — green slice = same-day acted, "
                "grey slice = missed. Use this to spot stretches where action "
                "discipline slipped (lots of grey) or the App got chatty (tall bars)."
            )
        else:
            st.caption("Not enough data to draw the daily-volume chart yet.")

    # ── By composite band ───────────────────────────────────────────────────
    with st.expander("🎯 Action rate & outcome by composite band", expanded=False):
        _rh_band = by_composite_band(_rh_enriched)
        if _rh_band:
            _rh_band_df = _rh_pd.DataFrame([
                {
                    "Composite band":  b["band"],
                    "Total":           b["n_total"],
                    "Acted":           b["n_acted"],
                    "Action rate":     f"{b['action_rate']:.0f}%" if b['action_rate'] is not None else "—",
                    "Avg acted":       f"{b['avg_acted_pct']:+.1f}%" if b['avg_acted_pct'] is not None else "—",
                    "Avg missed":      f"{b['avg_missed_pct']:+.1f}%" if b['avg_missed_pct'] is not None else "—",
                }
                for b in _rh_band
            ])
            st.dataframe(_rh_band_df, hide_index=True, use_container_width=True)
            st.caption(
                "Higher-composite bands should ideally have higher action rate "
                "*and* better outcomes — that confirms the composite gate is "
                "doing its job. Inverted ordering (e.g. Hold-zone outperforming "
                "Buy) is a signal to recalibrate."
            )
        else:
            st.caption("No composite-banded data in this range yet.")

    # ── By rec type ─────────────────────────────────────────────────────────
    with st.expander("🧭 Breakdown by rec type", expanded=False):
        _rh_types = by_rec_type(_rh_enriched)
        if _rh_types:
            _rh_t_df = _rh_pd.DataFrame([
                {
                    "Type":         rt,
                    "Total":        st_["n_total"],
                    "Acted":        st_["n_acted"],
                    "Action rate":  f"{st_['action_rate']:.0f}%" if st_["action_rate"] is not None else "—",
                    "Avg acted":    f"{st_['avg_acted_pct']:+.1f}%" if st_['avg_acted_pct'] is not None else "—",
                    "Avg missed":   f"{st_['avg_missed_pct']:+.1f}%" if st_['avg_missed_pct'] is not None else "—",
                }
                for rt, st_ in _rh_types.items()
            ])
            st.dataframe(_rh_t_df, hide_index=True, use_container_width=True)

    # ── Detailed table ──────────────────────────────────────────────────────
    st.markdown("### 📋 All recommendations")
    _rh_rows = []
    for r in sorted(_rh_enriched, key=lambda x: (x["rec_date"] or _rh_date.min, x["ticker"]), reverse=True):
        _outcome_disp = (
            f"{r['outcome_pct']:+.2f}%" if r["outcome_pct"] is not None else "—"
        )
        _status_disp = "✅ Acted" if r["acted_on"] else "⏭ Missed"
        _label_disp  = {
            "win":  "🟢 Win",
            "loss": "🔴 Loss",
            "flat": "⚪ Flat",
            "unknown": "—",
        }.get(r["outcome_label"], "—")
        _rh_rows.append({
            "Date":      r["rec_date"].isoformat() if r["rec_date"] else "—",
            "Ticker":    r["ticker"],
            "Type":      r["rec_type"],
            "Sector":    r["sector"] or "—",
            "Composite": (
                f"{r['composite_score']:.0f}" if r["composite_score"] is not None else "—"
            ),
            "Momentum":  (
                f"{r['momentum_score']:.0f}" if r["momentum_score"] is not None else "—"
            ),
            "Verdict":   r["verdict"] or "—",
            "Status":    _status_disp,
            "Outcome":   _outcome_disp,
            "Result":    _label_disp,
        })
    if _rh_rows:
        st.dataframe(
            _rh_pd.DataFrame(_rh_rows),
            hide_index=True,
            use_container_width=True,
            height=min(560, 60 + 35 * len(_rh_rows)),
        )

    st.caption(
        "_Outcome math — Acted BUY: mark-to-market vs entry price; Acted SELL: "
        "realized P&L from the journal; Missed: % change from price-at-surface "
        "(when stored) to current. Older recs surfaced before price_at_surface "
        "was captured will show '—' for missed outcomes._"
    )


# ═════════════════════════════════════════════════════════════════════════════
# PAGE 6 — ECONOMIC CALENDAR
# ═════════════════════════════════════════════════════════════════════════════
elif page == "📅 Economic Calendar":
    _fill_news_slot(_news_slot, st.session_state.get("_sidebar_news", []))
    st.title("📅 Economic Calendar")
    st.caption(
        "High-impact macro events for the next 45 days — FOMC, CPI, NFP, GDP and more. "
        "Static backbone (Fed/BLS/BEA schedules) enriched with official released values from FRED (St. Louis Fed). "
        "Holdings at risk column maps each event to your specific positions."
    )

    # ── FRED key config ───────────────────────────────────────────────────────
    _ec_fred_key = (
        st.secrets.get("fred", {}).get("api_key")
        or os.environ.get("FRED_API_KEY", "")
        or st.session_state.get("_ec_fred_key", "")
    )
    with st.expander("⚙️ FRED API key (optional — adds previous & actual released values)", expanded=not _ec_fred_key):
        st.markdown(
            "**FRED** (Federal Reserve Economic Data) is the official US economic data source.  \n"
            "Free API key at [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html) — takes ~2 minutes, no credit card.  \n"
            "Adds **previous** and **actual** values to CPI, NFP, GDP, PPI, Retail Sales, and Fed Funds Rate events."
        )
        _ec_key_input = st.text_input(
            "FRED API key",
            value=st.session_state.get("_ec_fred_key", ""),
            type="password",
            placeholder="your-fred-api-key",
            help="Free at fred.stlouisfed.org — 120 requests/minute, no credit card required",
        )
        if _ec_key_input:
            st.session_state["_ec_fred_key"] = _ec_key_input
            _ec_fred_key = _ec_key_input
        if _ec_fred_key:
            _fred_health = _ah.get_health("fred")
            _fred_err    = _fred_health.get("last_error", "")
            _fred_calls  = _fred_health.get("calls", 0)
            _fred_ok     = _fred_health.get("successes", 0)
            if _fred_err:
                st.warning(f"⚠️ FRED API error: {_fred_err}")
                st.caption("Check your FRED key in Streamlit Secrets → [fred] api_key. "
                           "Then click 🔄 Refresh calendar below.")
            elif _fred_calls > 0 and _fred_ok == 0:
                st.warning("⚠️ FRED key accepted but returned no data. "
                           "Click 🔄 Refresh calendar to retry.")
            else:
                st.success("FRED key active — previous & actual values enabled.")
        else:
            st.info(
                "Running on static backbone only (FOMC dates, CPI, NFP, GDP, PPI, Retail Sales). "
                "A free FRED key is required to show previous and actual released values next to each event "
                "(e.g. CPI YoY: +2.4%, NFP Chg: +228K). Takes ~2 minutes to register at fred.stlouisfed.org."
            )

    # ── Load / refresh calendar ───────────────────────────────────────────────
    # During market hours (8 AM–6 PM ET) the cache refreshes each hour so that
    # actual values (e.g. NFP at 08:30 ET) appear automatically without a manual refresh.
    _ec_port_hash  = len(st.session_state.get("_port_df_enriched", pd.DataFrame()))
    _ec_now_et     = datetime.now(_pytz.timezone("America/New_York"))
    _ec_hour_slot  = str(_ec_now_et.hour) if 8 <= _ec_now_et.hour <= 18 else "off"
    _ec_cache_key  = f"_ec_cal_{_TODAY_ET}_{bool(_ec_fred_key)}_{_ec_port_hash}_{_ec_hour_slot}"
    if _ec_cache_key not in st.session_state or st.button("🔄 Refresh calendar", key="_ec_refresh"):
        with st.spinner("Loading economic calendar…"):
            _ec_events = build_macro_calendar(
                st.session_state.get("_port_df_enriched", pd.DataFrame()),
                fred_key=_ec_fred_key or None,
                days_ahead=45,
                days_behind=7,
                today=_TODAY_ET,
            )
            st.session_state[_ec_cache_key] = _ec_events
    _ec_events = st.session_state.get(_ec_cache_key, [])

    # Warn if FRED key is set but none of the events got enriched (API may have failed)
    if _ec_fred_key and _ec_events:
        _fred_enriched = sum(1 for _e in _ec_events
                             if _e.get("actual") is not None or _e.get("previous") is not None)
        if _fred_enriched == 0:
            st.warning(
                "⚠️ FRED key is set but no actual/previous values were received. "
                "The API call may have failed. Click **🔄 Refresh calendar** to retry."
            )

    if not _ec_events:
        st.info("No events found in the calendar window.")
        st.stop()

    # ── KPI strip (forward-looking counts only) ───────────────────────────────
    _ec_fwd  = [e for e in _ec_events if e["date"] >= _TODAY_ET]
    _ec_high = [e for e in _ec_fwd if e["impact"] == MC_HIGH]
    _ec_week = [e for e in _ec_fwd if (e["date"] - _TODAY_ET).days <= 7]
    _ec_next = _ec_high[0] if _ec_high else (_ec_fwd[0] if _ec_fwd else None)
    _ek1, _ek2, _ek3, _ek4 = st.columns(4)
    _ek1.metric("Events next 45d",  len(_ec_fwd))
    _ek2.metric("🔴 High impact",    len(_ec_high))
    _ek3.metric("This week",         len(_ec_week),
                delta="⚠️ Be prepared" if _ec_week else None,
                delta_color="inverse" if _ec_week else "off")
    _ek4.metric("Next major event",
                _ec_next["event"][:20] if _ec_next else "—",
                _ec_next["days_label"] if _ec_next else "")

    # ── Tabs ──────────────────────────────────────────────────────────────────
    _cal_tab, _play_tab, _post_tab = st.tabs(
        ["📅 Calendar", "📋 Pre-Event Playbook", "📊 Post-Event Results"]
    )

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1 — CALENDAR  (two-column: upcoming left, completed right)
    # ══════════════════════════════════════════════════════════════════════════
    with _cal_tab:
        _ef1, _ef2 = st.columns([3, 4])
        with _ef1:
            _ec_impact_filter = st.radio(
                "Impact", ["All", "🔴 HIGH only", "🟡 MEDIUM+"],
                horizontal=True, key="_ec_imp_filter",
            )
        with _ef2:
            _all_cats = sorted({e["category"] for e in _ec_events})
            _ec_cat_filter = st.multiselect(
                "Categories", _all_cats, default=_all_cats, key="_ec_cat_filter"
            )

        _ec_filtered = _ec_events
        if _ec_impact_filter == "🔴 HIGH only":
            _ec_filtered = [e for e in _ec_filtered if e["impact"] == MC_HIGH]
        elif _ec_impact_filter == "🟡 MEDIUM+":
            _ec_filtered = [e for e in _ec_filtered if e["impact"] in (MC_HIGH, MC_MEDIUM)]
        if _ec_cat_filter:
            _ec_filtered = [e for e in _ec_filtered if e["category"] in _ec_cat_filter]

        _impact_cfg = {
            MC_HIGH:   ("#ef4444", "🔴", "#1a0000"),
            MC_MEDIUM: ("#f59e0b", "🟡", "#1a1200"),
        }
        _cat_icons = {
            "Fed Policy": "🏦", "Inflation": "📊", "Employment": "👷",
            "Growth": "📈", "Consumer": "🛒", "Activity": "🏭", "Other": "📋",
        }

        # Release-status / drift badge helper — surfaces FRED's
        # last_updated date vs the static schedule so the user sees
        # immediately when the calendar is out of sync with FRED.
        def _release_status_html(_ev: dict) -> str:
            _badges = []
            _drift  = _ev.get("drift_days")
            _rel_at = _ev.get("released_at")
            # ⚠ Drift: static date trails FRED's last_updated by 1+ days
            if isinstance(_drift, int) and _drift >= 1:
                _rel_disp = ""
                if _rel_at:
                    try:
                        _rel_disp = date.fromisoformat(_rel_at).strftime("%b %d").replace(" 0", " ")
                    except Exception:
                        _rel_disp = str(_rel_at)
                _badges.append(
                    f"<span style='background:#422006;border:1px solid #f59e0b;"
                    f"color:#fbbf24;padding:2px 8px;border-radius:10px;"
                    f"font-size:0.72em;font-weight:700'>"
                    f"⚠ Drift {_drift}d — FRED released {_rel_disp}</span>"
                )
            # ⏳ Awaiting: past event but no actual yet — FRED hasn't received it
            if _ev["date"] <= _TODAY_ET and not _ev.get("released") and not _ev.get("actual"):
                _badges.append(
                    f"<span style='background:#1f2937;border:1px solid #6b7280;"
                    f"color:#d1d5db;padding:2px 8px;border-radius:10px;"
                    f"font-size:0.72em;font-weight:700'>"
                    f"⏳ Awaiting FRED update</span>"
                )
            # ✓ Released: past event with FRED actual populated
            elif _ev.get("released"):
                _badges.append(
                    f"<span style='background:#052e16;border:1px solid #22c55e;"
                    f"color:#86efac;padding:2px 8px;border-radius:10px;"
                    f"font-size:0.72em;font-weight:700'>"
                    f"✓ Released</span>"
                )
            return " ".join(_badges)

        # Split filtered events into upcoming and past
        _ec_upcoming = [e for e in _ec_filtered if e["date"] >= _TODAY_ET]
        _ec_past_rev = sorted(
            [e for e in _ec_filtered if e["date"] < _TODAY_ET],
            key=lambda x: x["date"], reverse=True,   # most recent at top
        )

        from itertools import groupby as _groupby

        _col_up, _col_done = st.columns([11, 9])

        # ── Left: Upcoming ────────────────────────────────────────────────────
        with _col_up:
            st.markdown(f"**🗓️ Upcoming** — {len(_ec_upcoming)} event{'s' if len(_ec_upcoming) != 1 else ''}")
            st.divider()

            for _ec_date, _ec_day_evs in _groupby(_ec_upcoming, key=lambda x: x["date"]):
                _ec_day_list = list(_ec_day_evs)
                _delta_days  = (_ec_date - _TODAY_ET).days
                _date_label  = _ec_date.strftime("%A, %B %d").replace(" 0", " ")
                if _delta_days == 0:
                    _urgency_tag = " — **TODAY**"
                elif _delta_days == 1:
                    _urgency_tag = " — **TOMORROW**"
                elif _delta_days <= 7:
                    _urgency_tag = " — *this week*"
                else:
                    _urgency_tag = ""

                st.markdown(f"##### {_date_label}{_urgency_tag}")

                for _ev in _ec_day_list:
                    _imp_color, _imp_icon, _imp_bg = _impact_cfg.get(
                        _ev["impact"], ("#6b7280", "⚪", "#111")
                    )
                    _icon    = _cat_icons.get(_ev["category"], "📋")
                    _tix     = _ev["affected_tickers"]
                    _tix_str = ", ".join(f"**{t}**" for t in _tix[:5])
                    if len(_tix) > 5:
                        _tix_str += f" +{len(_tix)-5} more"
                    if not _tix_str:
                        _tix_str = "*No direct holdings*"

                    _data_parts = []
                    if _ev.get("previous") is not None:
                        _data_parts.append(f"Prev: **{_ev['previous']}**")
                    if _ev.get("estimate") is not None:
                        _data_parts.append(f"Est: **{_ev['estimate']}**")
                    _data_row = "  ·  ".join(_data_parts) if _data_parts else ""

                    with st.expander(
                        f"{_imp_icon} {_icon} **{_ev['event']}**  ·  "
                        f"{_ev.get('time_et', _ev.get('time',''))} ET  ·  {_ev['days_label']}",
                        expanded=(_delta_days <= 2 and _ev["impact"] == MC_HIGH),
                    ):
                        if _ev.get("description"):
                            st.caption(_ev["description"])
                        if _data_row:
                            st.markdown(_data_row)
                        _rel_badges = _release_status_html(_ev)
                        if _rel_badges:
                            st.markdown(
                                f"<div style='margin:4px 0 6px'>{_rel_badges}</div>",
                                unsafe_allow_html=True,
                            )
                        st.markdown(
                            f"**Holdings at risk:** {_tix_str} "
                            f"<span style='float:right;background:{_imp_bg};"
                            f"border:1px solid {_imp_color};border-radius:4px;"
                            f"padding:2px 8px;font-size:0.72em;color:{_imp_color};"
                            f"font-weight:700;letter-spacing:0.06em'>"
                            f"{_ev['impact']} · {_ev['category']}</span>",
                            unsafe_allow_html=True,
                        )
                        if _ev.get("context"):
                            st.info(f"**Institutional Lens** · {_ev['context']}")

                st.markdown("")

            if not _ec_upcoming:
                st.info("No upcoming events match the current filters.")

        # ── Right: Completed ──────────────────────────────────────────────────
        with _col_done:
            st.markdown(f"**✅ Completed** — past 7 days")
            st.divider()

            for _ec_date, _ec_day_evs in _groupby(_ec_past_rev, key=lambda x: x["date"]):
                _ec_day_list = list(_ec_day_evs)
                _delta_days  = (_ec_date - _TODAY_ET).days   # negative
                _date_label  = _ec_date.strftime("%A, %B %d").replace(" 0", " ")
                _ago_tag = " — *yesterday*" if _delta_days == -1 else f" — *{abs(_delta_days)}d ago*"

                st.markdown(f"##### {_date_label}{_ago_tag}")

                for _ev in _ec_day_list:
                    _imp_color, _imp_icon, _imp_bg = _impact_cfg.get(
                        _ev["impact"], ("#6b7280", "⚪", "#111")
                    )
                    _icon    = _cat_icons.get(_ev["category"], "📋")
                    _tix     = _ev["affected_tickers"]
                    _tix_str = ", ".join(f"**{t}**" for t in _tix[:5])
                    if len(_tix) > 5:
                        _tix_str += f" +{len(_tix)-5} more"
                    if not _tix_str:
                        _tix_str = "*No direct holdings*"

                    _data_parts = []
                    if _ev.get("previous") is not None:
                        _data_parts.append(f"Prev: **{_ev['previous']}**")
                    if _ev.get("actual") is not None:
                        _data_parts.append(f"Actual: **{_ev['actual']}**")
                    _data_row = "  ·  ".join(_data_parts) if _data_parts else ""

                    with st.expander(
                        f"{_imp_icon} {_icon} **{_ev['event']}**  ·  "
                        f"{_ev.get('time_et', _ev.get('time',''))} ET",
                        expanded=False,
                    ):
                        if _ev.get("description"):
                            st.caption(_ev["description"])
                        if _data_row:
                            st.markdown(_data_row)
                        _rel_badges = _release_status_html(_ev)
                        if _rel_badges:
                            st.markdown(
                                f"<div style='margin:4px 0 6px'>{_rel_badges}</div>",
                                unsafe_allow_html=True,
                            )
                        st.markdown(
                            f"**Holdings at risk:** {_tix_str} "
                            f"<span style='float:right;background:{_imp_bg};"
                            f"border:1px solid {_imp_color};border-radius:4px;"
                            f"padding:2px 8px;font-size:0.72em;color:{_imp_color};"
                            f"font-weight:700;letter-spacing:0.06em'>"
                            f"{_ev['impact']} · {_ev['category']}</span>",
                            unsafe_allow_html=True,
                        )
                        if _ev.get("context"):
                            st.info(f"**Institutional Lens** · {_ev['context']}")

                st.markdown("")

            if not _ec_past_rev:
                st.info("No completed events in the lookback window.")

        st.caption(
            f"Static backbone: FOMC · CPI/PPI/NFP/GDP/Retail Sales (Fed/BLS/BEA).  "
            f"{'FRED live layer active — previous & actual values included.' if _ec_fred_key else 'Add a free FRED key above for previous & actual released values.'}"
        )

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2 — PRE-EVENT PLAYBOOK
    # ══════════════════════════════════════════════════════════════════════════
    with _play_tab:
        _pb_port  = st.session_state.get("_port_df_enriched", pd.DataFrame())
        _pb_total = float(_pb_port["Market Value"].sum()) if not _pb_port.empty else 0.0

        if _pb_port.empty:
            st.info(
                "Visit **Portfolio** first so the playbook can map events to your specific holdings.",
                icon="ℹ️",
            )
        else:
            _playbooks = build_event_playbooks(_ec_fwd, _pb_port, _pb_total)

            if not _playbooks:
                st.info("No upcoming HIGH-impact events with playbook data in the next 45 days.")
            else:
                st.markdown(
                    "For each upcoming HIGH-impact event: scenario analysis, position-level pre-event "
                    "actions, and post-event decision rules tailored to your holdings."
                )
                st.divider()

                _action_colors = {
                    "PROTECT":     ("#ef4444", "#2d0000"),
                    "WATCH":       ("#f59e0b", "#1a1000"),
                    "OPPORTUNITY": ("#22c55e", "#001a08"),
                    "HOLD":        ("#6b7280", "#111"),
                }
                _action_icons = {
                    "PROTECT": "🛡️", "WATCH": "👁️",
                    "OPPORTUNITY": "🎯", "HOLD": "✋",
                }

                for _pb in _playbooks:
                    _pb_days   = _pb["days_until"]
                    _pb_urgent = _pb_days <= 7
                    _pb_icon   = "🔴" if _pb_urgent else "🟡"
                    _pb_date_str = _pb["date"].strftime("%A, %B %d").replace(" 0", " ")

                    st.markdown(
                        f"### {_pb_icon} {_pb['event']}  —  {_pb_date_str}  ·  *{_pb['days_label']}*"
                    )
                    if _pb.get("description"):
                        st.caption(_pb["description"])

                    # ── Portfolio exposure KPIs ────────────────────────────
                    _pk1, _pk2, _pk3, _pk4, _pk5 = st.columns(5)
                    _pk1.metric("Portfolio Exposure",
                                f"{_pb['exposure_pct']:.0f}%",
                                delta=_pb["exposure_level"],
                                delta_color="inverse" if _pb["exposure_level"] in ("HIGH","CRITICAL") else "off")
                    _sign_bear = "-" if _pb["total_bear_impact"] < 0 else "+"
                    _sign_bull = "+" if _pb["total_bull_impact"] > 0 else ""
                    _pk2.metric("🐻 Bear Scenario",  f"${_pb['total_bear_impact']:+,.0f}")
                    _pk3.metric("🐂 Bull Scenario",  f"${_pb['total_bull_impact']:+,.0f}")
                    _pk4.metric("🛡️ PROTECT",        _pb["protect_count"],
                                delta="action needed" if _pb["protect_count"] > 0 else None,
                                delta_color="inverse" if _pb["protect_count"] > 0 else "off")
                    _pk5.metric("🎯 Opportunities",  _pb["opp_count"])

                    # ── Scenario cards ─────────────────────────────────────
                    _sc_bull = _pb["scenarios"]["bull"]
                    _sc_base = _pb["scenarios"]["base"]
                    _sc_bear = _pb["scenarios"]["bear"]

                    # Compute portfolio-level $ for each scenario from positions
                    _port_bull = sum(p["bull_impact"] for p in _pb["positions"])
                    _port_base = sum(p["base_impact"] for p in _pb["positions"])
                    _port_bear = sum(p["bear_impact"] for p in _pb["positions"])

                    _sc1, _sc2, _sc3 = st.columns(3)
                    for _sc_col, _sc_data, _port_impact, _clr, _bg in [
                        (_sc1, _sc_bull, _port_bull, "#22c55e", "#001a08"),
                        (_sc2, _sc_base, _port_base, "#6b7280", "#111118"),
                        (_sc3, _sc_bear, _port_bear, "#ef4444", "#1a0000"),
                    ]:
                        _impact_sign = "+" if _port_impact >= 0 else ""
                        _sc_col.markdown(
                            f"<div style='background:{_bg};border:1px solid {_clr}33;"
                            f"border-top:3px solid {_clr};border-radius:8px;"
                            f"padding:14px 16px;height:100%'>"
                            f"<div style='font-size:1.4em'>{_sc_data['icon']}</div>"
                            f"<div style='font-weight:700;color:{_clr};font-size:0.9em;"
                            f"margin:4px 0'>{_sc_data['label']}</div>"
                            f"<div style='font-size:1.6em;font-weight:800;color:{_clr};"
                            f"margin:6px 0'>{_impact_sign}${abs(_port_impact):,.0f}</div>"
                            f"<div style='font-size:0.75em;color:#aaa;margin-bottom:8px'>"
                            f"est. portfolio impact</div>"
                            f"<div style='font-size:0.78em;color:#ccc;border-top:1px solid #333;"
                            f"padding-top:8px'><b>If:</b> {_sc_data['condition']}</div>"
                            f"<div style='font-size:0.75em;color:#999;margin-top:6px'>"
                            f"{_sc_data['notes']}</div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    st.markdown("")

                    # ── Pre-event actions ──────────────────────────────────
                    _actions_needed = _pb["protect_count"] + _pb["watch_count"]
                    with st.expander(
                        f"⚡ Pre-Event Actions — "
                        f"{_pb['protect_count']} PROTECT  ·  {_pb['watch_count']} WATCH  ·  "
                        f"{_pb['opp_count']} OPPORTUNITY  ·  "
                        f"{len(_pb['positions']) - _pb['protect_count'] - _pb['watch_count'] - _pb['opp_count']} HOLD",
                        expanded=(_pb_urgent and _actions_needed > 0),
                    ):
                        for _pos in _pb["positions"]:
                            _ac     = _pos["action"]
                            _ac_clr, _ac_bg = _action_colors.get(_ac, ("#6b7280", "#111"))
                            _ac_ico = _action_icons.get(_ac, "")

                            _bear_str = f"${_pos['bear_impact']:+,.0f}" if _pos["bear_impact"] != 0 else "—"
                            _bull_str = f"${_pos['bull_impact']:+,.0f}" if _pos["bull_impact"] != 0 else "—"

                            # Action badge row
                            _pa1, _pa2, _pa3, _pa4 = st.columns([1.5, 1, 1, 1])
                            _pa1.markdown(
                                f"<span style='background:{_ac_bg};border:1px solid {_ac_clr};"
                                f"color:{_ac_clr};padding:3px 10px;border-radius:4px;"
                                f"font-size:0.8em;font-weight:700'>{_ac_ico} {_ac}</span>"
                                f"&nbsp;&nbsp;<b>{_pos['ticker']}</b> "
                                f"<span style='color:#aaa;font-size:0.85em'>({_pos['sector']})</span>",
                                unsafe_allow_html=True,
                            )
                            _pa2.markdown(f"**{_pos['weight']:.1f}%** weight")
                            _pa3.markdown(f"Bear: **{_bear_str}**")
                            _pa4.markdown(f"Bull: **{_bull_str}**")

                            # Rationale + action detail
                            st.markdown(
                                f"<div style='background:#0d1117;border-left:3px solid {_ac_clr};"
                                f"border-radius:0 6px 6px 0;padding:10px 14px;margin:4px 0 12px 0;"
                                f"font-size:0.85em;color:#ccc'>"
                                f"{_pos['rationale']}<br>"
                                f"<span style='color:{_ac_clr};font-weight:600;margin-top:6px;"
                                f"display:block'>{_pos['detail']}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                    # ── What to watch ──────────────────────────────────────
                    if _pb.get("watch_for"):
                        with st.expander("👁️ What to Watch in the Release", expanded=_pb_urgent):
                            for _wf in _pb["watch_for"]:
                                st.markdown(f"• {_wf}")

                    # ── Institutional Lens ─────────────────────────────────
                    if _pb.get("context"):
                        with st.expander("🏛️ Institutional Lens", expanded=False):
                            st.info(_pb["context"])

                    # ── Post-event decision rules ──────────────────────────
                    _post_positions = [p for p in _pb["positions"] if p["action"] in ("PROTECT", "WATCH")]
                    if _post_positions:
                        with st.expander("🎯 Post-Event Decision Rules", expanded=False):
                            st.caption(
                                "These rules apply the morning the number drops (08:30 ET). "
                                "Have a plan before the release — not after."
                            )
                            for _pp in _post_positions:
                                _ac_clr, _ = _action_colors.get(_pp["action"], ("#6b7280", "#111"))
                                st.markdown(
                                    f"<div style='padding:8px 0;border-bottom:1px solid #222'>"
                                    f"<b style='color:{_ac_clr}'>{_pp['ticker']}</b> — "
                                    f"{_pp['post_event']}</div>",
                                    unsafe_allow_html=True,
                                )

                    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 3 — POST-EVENT RESULTS
    # ══════════════════════════════════════════════════════════════════════════
    with _post_tab:
        _pb_port  = st.session_state.get("_port_df_enriched", pd.DataFrame())
        _pb_total = float(_pb_port["Market Value"].sum()) if not _pb_port.empty else 0.0

        if _pb_port.empty:
            st.info(
                "Visit **Portfolio** first so the post-event analysis can map "
                "outcomes to your specific holdings.",
                icon="ℹ️",
            )
        else:
            from stock_analyzer.macro_playbook import _SCENARIOS as _PB_SCEN_DEF

            # Events where date ≤ today, HIGH-impact, and have a scenario definition
            _past_events = sorted(
                [
                    e for e in _ec_events
                    if e["date"] <= _TODAY_ET
                    and e["impact"] == MC_HIGH
                    and e["event"] in _PB_SCEN_DEF
                ],
                key=lambda x: x["date"],
                reverse=True,
            )

            if not _past_events:
                st.info(
                    "No recently-released HIGH-impact events with scenario data in the calendar window. "
                    "Check back after the next NFP, CPI, FOMC, GDP, PPI or Retail Sales release.",
                    icon="📭",
                )
            else:
                # ── Auto-detect macro regime ──────────────────────────────────
                _regime_cache_key = f"_macro_regime_{_TODAY_ET}_{bool(_ec_fred_key)}"
                if _regime_cache_key not in st.session_state:
                    with st.spinner("Detecting macro regime…"):
                        try:
                            _det_regime = detect_macro_regime_fred(
                                str(_ec_fred_key).strip() if _ec_fred_key else None
                            )
                        except Exception as _regime_err:
                            _det_regime = {
                                "regime": "neutral", "label": "Data-Dependent",
                                "icon": "📊", "color": "#6b7280", "bg": "#111827",
                                "fed_trend": "unknown", "cpi_yoy": None,
                                "rationale": "Regime detection unavailable — using textbook scenario interpretation.",
                                "source": "fallback",
                                "confidence": 0, "scores": {}, "signals": [],
                            }
                        st.session_state[_regime_cache_key] = _det_regime
                _regime = st.session_state[_regime_cache_key]

                # Regime banner — pre-compute dynamic parts to avoid nested f-string issues
                _regime_src_tag  = (
                    f"  ·  {_regime.get('confidence', 0)}% confidence"
                    f"  ·  {'FRED+Market' if _regime.get('source') == 'fred+market' else 'ESTIMATED'}"
                )
                _regime_fed_tag  = ("  ·  Fed " + _regime.get("fed_trend", "unknown").title()) if _regime.get("fed_trend", "unknown") != "unknown" else ""
                _regime_cpi_tag  = ("  ·  CPI " + f"{_regime['cpi_yoy']:.1f}%") if _regime.get("cpi_yoy") is not None else ""

                # Build signal pills HTML
                _PILL_STYLES = {
                    "rate_cut":        ("background:#0a1628;border:1px solid #3b82f6;color:#93c5fd",),
                    "recession_fear":  ("background:#1a0000;border:1px solid #ef4444;color:#fca5a5",),
                    "inflation_fight": ("background:#1a1200;border:1px solid #f59e0b;color:#fcd34d",),
                    "stagflation_risk":("background:#1a0a2e;border:1px solid #8b5cf6;color:#c4b5fd",),
                }
                _DEFAULT_PILL_STYLE = "background:#1f2937;border:1px solid #4b5563;color:#9ca3af"
                _pill_html_parts = []
                for _sig_label, _sig_reading, _sig_icon, _sig_hint in _regime.get("signals", []):
                    _pstyle = _PILL_STYLES.get(_sig_hint, (_DEFAULT_PILL_STYLE,))[0] if _sig_hint else _DEFAULT_PILL_STYLE
                    _pill_html_parts.append(
                        f"<span style='{_pstyle};border-radius:12px;padding:3px 10px;"
                        f"font-size:0.75em;font-weight:600;white-space:nowrap;display:inline-block;margin:2px'>"
                        f"{_sig_icon} {_sig_label}: {_sig_reading}</span>"
                    )
                _pills_html = "".join(_pill_html_parts)
                _pills_row = (
                    f"<div style='margin-top:8px;display:flex;flex-wrap:wrap;gap:4px'>{_pills_html}</div>"
                    if _pills_html else ""
                )

                st.markdown(
                    f"<div style='background:{_regime['bg']};border-left:4px solid "
                    f"{_regime['color']};border-radius:8px;padding:12px 16px;margin-bottom:16px'>"
                    f"<div style='font-size:0.72em;color:{_regime['color']};font-weight:700;"
                    f"letter-spacing:0.08em'>CURRENT MACRO REGIME — AUTO-DETECTED{_regime_src_tag}</div>"
                    f"<div style='font-size:1.05em;font-weight:700;color:#eee;margin-top:4px'>"
                    f"{_regime['icon']}  {_regime['label']}{_regime_fed_tag}{_regime_cpi_tag}</div>"
                    f"{_pills_row}"
                    f"<div style='font-size:0.82em;color:#bbb;margin-top:6px'>"
                    f"{_regime.get('rationale', '')}</div></div>",
                    unsafe_allow_html=True,
                )

                st.markdown(
                    "For each recently-released HIGH-impact event: select (or auto-detect) which "
                    "scenario played out and see the immediate impact on your holdings."
                )
                st.divider()

                _post_action_colors = {
                    "ADD":    "#00C851", "HOLD":   "#3b82f6",
                    "WATCH":  "#f59e0b", "REDUCE": "#ef4444",
                }
                _post_action_icons = {
                    "ADD": "➕", "HOLD": "✋", "WATCH": "👁️", "REDUCE": "📉",
                }

                for _pe in _past_events:
                    _pe_name    = _pe["event"]
                    _pe_date    = _pe["date"]
                    _pe_actual  = _pe.get("actual")
                    _pe_est     = _pe.get("estimate")
                    _pe_prev    = _pe.get("previous")
                    _is_today   = (_pe_date == _TODAY_ET)
                    _conds      = get_scenario_conditions(_pe_name)

                    _date_lbl = (
                        _pe_date.strftime("%A, %B %d").replace(" 0", " ")
                        if hasattr(_pe_date, "strftime") else str(_pe_date)
                    )
                    _today_badge = "  ·  **TODAY**" if _is_today else ""
                    st.subheader(f"🔴 {_pe_name}  ·  {_date_lbl}{_today_badge}")

                    # ── Consensus estimate input ──────────────────────────
                    _thresh_cfg   = _MC_THRESHOLDS.get(_pe_name, {})
                    _implied_base = _thresh_cfg.get("implied_base")
                    _est_meta = {
                        "Non-Farm Payrolls":    ("K",     1.0,  "%.0f"),
                        "CPI Inflation":        ("% YoY", 0.1,  "%.2f"),
                        "GDP Advance Estimate": ("% QoQ", 0.1,  "%.1f"),
                        "PPI Producer Prices":  ("% MoM", 0.1,  "%.2f"),
                        "Retail Sales":         ("% MoM", 0.1,  "%.2f"),
                        "FOMC Rate Decision":   ("%",     0.25, "%.2f"),
                    }
                    _eu_lbl, _eu_step, _eu_fmt = _est_meta.get(_pe_name, ("", 1.0, "%.2f"))
                    _est_sess_key = f"_est_v_{_pe_name}_{_pe_date}"
                    # Start value: FRED estimate if provided, else model implied_base
                    _est_start = (
                        _mc_parse_number(_pe_est) if _pe_est is not None
                        else (float(_implied_base) if _implied_base is not None else None)
                    )
                    _is_baseline = (_pe_est is None and _implied_base is not None)

                    # Render estimate input in right column first so its value
                    # is available when we build the data row in the left column
                    _dc1, _dc2 = st.columns([3, 2])
                    with _dc2:
                        _est_val = st.number_input(
                            f"Consensus est. ({_eu_lbl})" if _eu_lbl else "Consensus est.",
                            value=_est_start,
                            step=_eu_step,
                            format=_eu_fmt,
                            key=_est_sess_key,
                            help=(
                                "Pre-filled with model baseline. "
                                "Enter the real Wall St. consensus (Bloomberg / Reuters) "
                                "to get an accurate Beat / Missed verdict."
                            ) if _is_baseline else (
                                "From FRED data." if _pe_est is not None
                                else "Enter the consensus estimate from Bloomberg / Reuters."
                            ),
                        )
                        if _is_baseline:
                            st.caption("📌 Model baseline — update with real consensus if known")

                    # Use entered estimate for classification
                    _auto_sc = classify_scenario(_pe_name, _pe_actual, _est_val)

                    # ── Previous · Est · Actual data row ─────────────────
                    with _dc1:
                        _pe_parts = []
                        if _pe_prev is not None:
                            _pe_parts.append(f"Previous: **{_pe_prev}**")
                        if _est_val is not None:
                            _est_disp = (
                                f"~{_est_val:,.0f}K"    if _eu_lbl == "K"
                                else f"~{_est_val:.2f}%" if _eu_lbl
                                else f"~{_est_val}"
                            )
                            _pe_parts.append(f"Est: **{_est_disp}**")
                        if _pe_actual is not None:
                            # Suppress the "In-Line" badge when no real consensus was
                            # supplied — calling a print "in-line" against a synthetic
                            # baseline is misleading (the actual headline beat or
                            # missed by an amount we cannot verify without consensus).
                            if _auto_sc == "bull":
                                _beat_badge = " 🟢 Beat"
                            elif _auto_sc == "bear":
                                _beat_badge = " 🔴 Missed"
                            elif _auto_sc == "base" and not _is_baseline:
                                _beat_badge = " ⬜ In-Line"
                            elif _auto_sc == "base" and _is_baseline:
                                _beat_badge = " ⬜ vs baseline (no consensus)"
                            else:
                                _beat_badge = ""
                            _pe_parts.append(f"Actual: **{_pe_actual}**{_beat_badge}")
                        if _pe_parts:
                            st.markdown("  ·  ".join(_pe_parts))

                    # Scenario selector
                    _sc_key  = f"_post_sc_{_pe_name}_{_pe_date}"
                    _sc_opts = ["🐂 Bull — " + _PB_SCEN_DEF[_pe_name]["bull"]["label"],
                                "📊 Base — " + _PB_SCEN_DEF[_pe_name]["base"]["label"],
                                "🐻 Bear — " + _PB_SCEN_DEF[_pe_name]["bear"]["label"]]
                    _sc_map  = {o: k for o, k in zip(_sc_opts, ["bull", "base", "bear"])}

                    # ── Regime-adjusted note ─────────────────────────────────
                    # Only show a regime note when scenario classification is real —
                    # i.e. either actual+consensus produced an explicit bull/base/bear,
                    # or we have a baseline-derived classification but it's not the
                    # base case (beat/missed against baseline is still informative,
                    # but auto-claiming "in-line" against a synthetic baseline isn't).
                    _show_regime_note = bool(_auto_sc) and not (
                        _auto_sc == "base" and _is_baseline
                    )
                    _regime_note = (
                        _MC_REGIME_NOTES
                        .get(_regime["regime"], {})
                        .get(_pe_name, {})
                        .get(_auto_sc)
                    ) if _show_regime_note else None
                    if _regime_note and _regime["regime"] != "neutral":
                        st.markdown(
                            f"<div style='background:{_regime['bg']};border-left:3px solid "
                            f"{_regime['color']};border-radius:6px;padding:8px 12px;"
                            f"margin:8px 0;font-size:0.83em;color:#ddd'>"
                            f"<span style='color:{_regime['color']};font-weight:700'>"
                            f"{_regime['icon']} {_regime['label']} · Regime Note</span><br>"
                            f"{_regime_note}</div>",
                            unsafe_allow_html=True,
                        )

                    if _auto_sc:
                        _default_idx = ["bull", "base", "bear"].index(_auto_sc)
                        _det_note = (
                            "vs. your consensus estimate" if _pe_est is None and _est_val is not None and not _is_baseline
                            else "vs. model baseline — update consensus above for a more accurate read" if _is_baseline
                            else "from FRED data"
                        )
                        st.success(
                            f"Scenario auto-detected ({_det_note}): **{_sc_opts[_default_idx]}**",
                            icon="✅",
                        )
                        _sc_sel = st.selectbox(
                            "Override if needed:",
                            _sc_opts, index=_default_idx,
                            key=f"_sc_sel_{_sc_key}",
                        )
                    else:
                        if _pe_actual is None:
                            if _is_today:
                                st.warning(
                                    f"Actual data not yet populated for today's **{_pe_name}**.  \n"
                                    "Click **🔄 Refresh calendar** above to pull the latest FRED data, "
                                    "or select the scenario manually based on news reports.",
                                    icon="⚠️",
                                )
                            else:
                                st.info(
                                    "Actual value not yet available from FRED — data populates automatically after the official release.  \n"
                                    "Select the scenario manually based on the reported result.",
                                    icon="ℹ️",
                                )
                        _sc_col1, _sc_col2 = st.columns([2, 3])
                        with _sc_col1:
                            _sc_sel = st.selectbox(
                                "What was the outcome?",
                                ["— select —"] + _sc_opts,
                                key=f"_sc_sel_{_sc_key}",
                            )
                        with _sc_col2:
                            if _sc_sel != "— select —" and _sc_sel in _sc_map:
                                _sk = _sc_map[_sc_sel]
                                st.caption(f"Condition: {_conds[_sk]}")

                    _selected_sc = _sc_map.get(_sc_sel) if _sc_sel != "— select —" else None

                    # ── Post-event analysis ───────────────────────────────
                    if _selected_sc:
                        _pea = build_post_event_analysis(_pe, _pb_port, _pb_total, _selected_sc)
                        if _pea:
                            _sc_colors = {"bull": "#00C851", "base": "#3b82f6", "bear": "#ef4444"}
                            _sc_bgs    = {"bull": "#001a08", "base": "#0a1628", "bear": "#1a0000"}
                            _sc_clr    = _sc_colors[_selected_sc]
                            _sc_bg     = _sc_bgs[_selected_sc]

                            # Scenario banner
                            st.markdown(
                                f"<div style='background:{_sc_bg};border-left:4px solid {_sc_clr};"
                                f"border-radius:8px;padding:14px 16px;margin:12px 0'>"
                                f"<div style='font-size:1.25em;font-weight:700;color:{_sc_clr}'>"
                                f"{_pea['scenario_icon']} {_pea['scenario_label']}</div>"
                                f"<div style='font-size:0.85em;color:#bbb;margin-top:6px'>"
                                f"{_pea['scenario_notes']}</div>"
                                f"<div style='font-size:0.82em;color:#888;margin-top:8px'>"
                                f"Expected broad market move: "
                                f"<span style='color:{_sc_clr};font-weight:bold'>"
                                f"{_pea['market_pct']:+.1f}%</span></div></div>",
                                unsafe_allow_html=True,
                            )

                            # Portfolio impact KPI
                            _imp_sign = "+" if _pea["total_impact"] >= 0 else ""
                            _imp_pct  = (
                                f"{_pea['total_impact'] / _pb_total * 100:+.1f}% of portfolio"
                                if _pb_total > 0 else None
                            )
                            st.metric(
                                "Estimated portfolio impact",
                                f"${_imp_sign}{_pea['total_impact']:,.0f}",
                                delta=_imp_pct,
                                delta_color="normal" if _pea["total_impact"] >= 0 else "inverse",
                            )

                            # Position-level impact cards
                            if _pea["positions"]:
                                st.markdown(
                                    "**Position-level impact & recommended actions** "
                                    "— expand any row for detail:"
                                )
                                for _pp in _pea["positions"]:
                                    _act   = _pp["action"]
                                    _aclr  = _post_action_colors.get(_act, "#888")
                                    _aicon = _post_action_icons.get(_act, "—")
                                    _imp   = _pp["dollar_impact"]
                                    _mc    = "#00C851" if _imp >= 0 else "#ef4444"
                                    _smc   = "#00C851" if _pp["sector_move"] >= 0 else "#ef4444"

                                    with st.expander(
                                        f"{_aicon} **{_pp['ticker']}**  ·  "
                                        f"Sector {_pp['sector_move']:+.1f}%  ·  "
                                        f"${_imp:+,.0f}  ·  "
                                        f"**{_act}**"
                                    ):
                                        _ppc1, _ppc2 = st.columns(2)
                                        with _ppc1:
                                            st.markdown(
                                                f"**Sector:** {_pp['sector']}  \n"
                                                f"**Scenario sector move:** "
                                                f"<span style='color:{_smc};font-weight:bold'>"
                                                f"{_pp['sector_move']:+.1f}%</span>  \n"
                                                f"**Dollar impact:** "
                                                f"<span style='color:{_mc};font-weight:bold'>"
                                                f"${_imp:+,.0f}</span>  \n"
                                                f"**Weight:** {_pp['weight']:.1f}%  ·  "
                                                f"**Score:** {_pp['score']:.0f}/100  ·  "
                                                f"**P&L:** {_pp['pnl_pct']:+.1f}%",
                                                unsafe_allow_html=True,
                                            )
                                        with _ppc2:
                                            st.markdown(
                                                f"<span style='color:{_aclr};"
                                                f"font-weight:bold;font-size:1.05em'>"
                                                f"{_aicon} {_act}</span>",
                                                unsafe_allow_html=True,
                                            )
                                            st.markdown(_pp["action_detail"])
                            else:
                                st.info(
                                    "No holdings have direct sector exposure to this event. "
                                    "No action required."
                                )

                    st.divider()

st.caption("Data: Yahoo Finance · Algorithmic analysis · Not financial advice")
