import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date

import html as _html
from stock_analyzer.data import (
    DEFAULT_TICKERS, fetch_ticker_bundle, fetch_financials_from_info,
    fetch_spy, fetch_live_prices, fetch_market_indices, market_status,
    curate_news_items, fetch_price_history,
)
from stock_analyzer.technicals import compute_indicators, technical_score
from stock_analyzer.fundamentals import fundamental_score, upside_potential
from stock_analyzer.sentiment import analyze_news, sentiment_score_0_100
from stock_analyzer.scoring import combined_score, recommendation
from stock_analyzer.risk import atr_stop_loss, position_sizing, compute_all_risk
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

st.set_page_config(page_title="Portfolio Manager", page_icon="📊", layout="wide")


MODERATE_RISK_PCT = 0.015


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
        "The primary valuation metric used by Goldman Sachs and most top-tier "
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
        "At Goldman Sachs, conviction trades typically require R:R ≥ 2.5:1 to "
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
        "Goldman Sachs risk teams target average pairwise correlation below 0.40 "
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
}


def _tip(key: str) -> str:
    """Return tooltip text for st.metric(help=...) and st.column_config help=."""
    return _TIPS.get(key, "")


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
    st.title("📊 Portfolio Manager")
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
    st.session_state.nav_page = "🏠 My Portfolio"
if not st.session_state.get("db_loaded"):
    st.session_state.holdings_df = db.load_holdings()
    st.session_state.watchlist   = db.load_watchlist()
    st.session_state.trades_df   = db.load_trades()
    st.session_state.db_loaded   = True

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Portfolio value banner — always at the very top ──────────────────
    _pv = st.session_state.get("_portfolio_value", 0)
    if _pv > 0:
        _risk_val = _pv * MODERATE_RISK_PCT
        _pv_label    = f"${_pv:,.0f}"
        _risk_label  = f"${_risk_val:,.0f}"
        st.markdown(
            f"<div style='background:#1565C0;border-radius:8px;padding:12px 14px 10px;"
            f"margin-bottom:12px;color:#fff'>"
            f"<div style='font-size:0.68em;font-weight:700;letter-spacing:0.1em;"
            f"text-transform:uppercase;opacity:0.8;margin-bottom:4px'>Portfolio Value</div>"
            f"<div style='font-size:1.5em;font-weight:700;line-height:1.1'>{_pv_label}</div>"
            f"<div style='font-size:0.74em;margin-top:6px;opacity:0.9'>"
            f"Risk/trade: <b>{_risk_label}</b>&nbsp;"
            f"<span style='opacity:0.65'>· 1.5% moderate</span></div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='background:#1565C0;border-radius:8px;padding:12px 14px 10px;"
            "margin-bottom:12px;color:#fff'>"
            "<div style='font-size:0.68em;font-weight:700;letter-spacing:0.1em;"
            "text-transform:uppercase;opacity:0.8;margin-bottom:4px'>Portfolio Value</div>"
            "<div style='font-size:1.1em;font-weight:600;opacity:0.65'>Loading…</div>"
            "<div style='font-size:0.74em;margin-top:6px;opacity:0.5'>Open My Portfolio tab</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.header("📊 Portfolio Manager")
    page = st.radio(
        "Navigate",
        ["🏠 My Portfolio", "🔍 Market Scanner", "📈 Stock Analysis", "📒 Trade Journal"],
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

    # Refresh button
    if st.button("🔄 Refresh Prices", use_container_width=True):
        st.cache_data.clear()
        st.session_state.last_refresh = datetime.now()
        st.rerun()

    refresh_ago = int((datetime.now() - st.session_state.last_refresh).total_seconds())
    if refresh_ago < 60:
        st.caption(f"Last refresh: {refresh_ago}s ago")
    else:
        st.caption(f"Last refresh: {refresh_ago // 60}m {refresh_ago % 60}s ago")

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

# ── Shared data loader ────────────────────────────────────────────────────────
@st.cache_data(ttl=1800)
def load_all(ticker: str, period: str = "6mo") -> dict:
    # One yf.Ticker session covers history + info + news + earnings (was 4 separate calls)
    bundle = fetch_ticker_bundle(ticker, period)
    df = compute_indicators(bundle["history"])
    t_score, t_signals = technical_score(df)
    financials = fetch_financials_from_info(bundle["info"])
    f_score, f_signals = fundamental_score(financials)
    avg_sent, headlines = analyze_news(bundle["news"])
    s_score = sentiment_score_0_100(avg_sent)
    total = combined_score(t_score, f_score, s_score)
    rec = recommendation(total)
    price = float(df["Close"].iloc[-1]) if not df.empty else None
    stop, atr_val = atr_stop_loss(df, multiplier=2.0)
    entry_lo, entry_hi = entry_zone(price, atr_val) if price else (None, None)
    targets = compute_price_targets(df, financials, price) if price else None
    sr = support_resistance(df)
    try:
        spy_df = fetch_spy(period)
        risk_metrics = compute_all_risk(df, spy_df)
    except Exception:
        risk_metrics = compute_all_risk(df, None)
    upside = upside_potential(price, financials) if price else None
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
if page == "🏠 My Portfolio":
    st.title("🏠 My Portfolio")

    # Holdings editor
    with st.expander("✏️ Edit Holdings", expanded=False):
        st.caption("Update your actual shares and average cost. Add rows for new positions.")
        edited = st.data_editor(
            st.session_state.holdings_df,
            num_rows="dynamic",
            column_config={
                "Ticker":        st.column_config.TextColumn("Ticker", width="small"),
                "Shares":        st.column_config.NumberColumn("Shares", min_value=1, step=1),
                "Avg Cost ($)":  st.column_config.NumberColumn("Avg Cost ($)", min_value=0.01, format="$%.2f"),
            },
            use_container_width=True,
            key="holdings_editor",
        )
        if st.button("Save Holdings", type="primary"):
            st.session_state.holdings_df = edited  # always update session state
            if db.save_holdings(edited):
                st.success("✅ Saved to Supabase — will persist across sessions.")
            else:
                st.success("✅ Holdings updated for this session.")
            st.rerun()

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

    if port_df.empty:
        st.info("Enter your holdings above to see portfolio analytics.")
        st.stop()

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

    best_row  = port_df.loc[port_df["P&L (%)"].idxmax()]
    worst_row = port_df.loc[port_df["P&L (%)"].idxmin()]
    winners   = int((port_df["P&L (%)"] > 0).sum())

    if n_danger > 0 or (div_score is not None and div_score < 30):
        _rag_label, _rag_color = "Action Required", "#ff4444"
    elif n_warning > 0 or (div_score is not None and div_score < 42):
        _rag_label, _rag_color = "Monitor", "#ffbb33"
    else:
        _rag_label, _rag_color = "All Clear", "#00C851"

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
        f"</div></div>",
        unsafe_allow_html=True,
    )

    _c1, _c2, _c3, _c4, _c5, _c6, _c7, _c8 = st.columns(8)
    _c1.metric("Portfolio Value",  f"${total_val:,.0f}")
    _c2.metric("Total P&L",        f"${total_pnl:,.0f}", f"{total_pnl_pct:+.1f}%", delta_color="normal")
    if _today_loaded:
        _c3.metric(
            "Today's P&L",
            f"${_today_pnl:+,.0f}",
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
    _c7.metric(f"Best: {best_row['Ticker']}",
               f"{best_row['P&L (%)']:+.1f}%", f"${best_row['P&L ($)']:,.0f}", delta_color="normal")
    _c8.metric(f"Worst: {worst_row['Ticker']}",
               f"{worst_row['P&L (%)']:+.1f}%", f"${worst_row['P&L ($)']:,.0f}", delta_color="normal")

    st.markdown("<div style='margin-bottom:4px'></div>", unsafe_allow_html=True)

    # ── Navigation tabs ───────────────────────────────────────────────────────
    tab_ov, tab_perf, tab_earn, tab_pnl, tab_act, tab_risk, tab_rs, tab_macro, tab_heat, tab_rank, tab_brief = st.tabs([
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
    # TAB 1 — OVERVIEW
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
                text=[f"${v:,.0f}<br>{p:+.1f}%" for v, p in
                      zip(port_df["P&L ($)"], port_df["P&L (%)"])],
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
        styled = (
            port_df[display_cols].style
            .map(_pnl_color, subset=["P&L ($)", "P&L (%)"])
            .map(_stop_color, subset=["Stop Type"])
            .map(_sig_color, subset=["Signal"])
            .format({
                "Avg Cost":        "${:.2f}",
                "Price":           "${:.2f}",
                "Market Value":    "${:,.0f}",
                "P&L ($)":         "${:,.0f}",
                "P&L (%)":         "{:+.1f}%",
                "Weight (%)":      "{:.1f}%",
                "Stop":            "${:.2f}",
                "Gap to Stop (%)": "{:.1f}%",
                "Score":           "{:.0f}",
            })
        )
        st.dataframe(styled, use_container_width=True)

        # Per-position drill-down
        st.subheader("Position Drill-Down")
        sel = st.selectbox("Select position to drill down", port_df["Ticker"].tolist())
        if sel and sel in held_data:
            r = held_data[sel]
            price = r["current_price"]
            targets = r["targets"]
            ps_row = port_df[port_df["Ticker"] == sel].iloc[0]

            d1, d2, d3, d4 = st.columns(4)
            d1.metric("P&L",         f"${ps_row['P&L ($)']:,.0f}", f"{ps_row['P&L (%)']:+.1f}%")
            d2.metric("Stop Loss",   f"${ps_row['Stop']:.2f}",     ps_row['Stop Type'],
                      help=_tip("ATR Stop"))
            d3.metric("Gap to Stop", f"{ps_row['Gap to Stop (%)']:.1f}%",
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
                    name="My Portfolio",
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
        _pk1.metric("Total P&L",      f"${_total_pnl:+,.0f}",
                    delta_color="normal" if _total_pnl >= 0 else "inverse")
        _pk2.metric("Gross Gains",    f"${_win_total:,.0f}",
                    f"{len(_winners)} positions", delta_color="off")
        _pk3.metric("Gross Losses",   f"${_loss_total:,.0f}",
                    f"{len(_losers)} positions",  delta_color="off")
        _pk4.metric(f"Top: {_best['Ticker']}",
                    f"${_best['P&L ($)']:+,.0f}", f"{_best['P&L (%)']:+.1f}%",
                    delta_color="normal")
        _pk5.metric(f"Drag: {_worst['Ticker']}",
                    f"${_worst['P&L ($)']:+,.0f}", f"{_worst['P&L (%)']:+.1f}%",
                    delta_color="inverse")

        # ── Waterfall chart ───────────────────────────────────────────────────
        _tickers  = list(_pnl_df["Ticker"]) + ["Total"]
        _measures = ["relative"] * len(_pnl_df) + ["total"]
        _values   = list(_pnl_df["P&L ($)"]) + [_total_pnl]
        _bar_clrs = [
            "#00C851" if v >= 0 else "#ff4444"
            for v in list(_pnl_df["P&L ($)"]) + [_total_pnl]
        ]
        _hover = [
            f"{row['Ticker']}<br>"
            f"P&L: ${row['P&L ($)']:+,.0f} ({row['P&L (%)']:+.1f}%)<br>"
            f"Weight: {row['Weight (%)']:.1f}%<br>"
            f"Sector: {row['Sector']}<br>"
            f"Signal: {row['Signal']}"
            for _, row in _pnl_df.iterrows()
        ] + [f"Total P&L: ${_total_pnl:+,.0f}"]

        _wf_fig = go.Figure(go.Waterfall(
            orientation="v",
            measure=_measures,
            x=_tickers,
            y=_values,
            text=[f"${v:+,.0f}" for v in _values],
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
            text=[f"${v:+,.0f}" for v in _sec_pnl["P&L ($)"]],
            textposition="outside",
            customdata=list(zip(_sec_pnl["Sector"], _sec_pnl["Share of P&L (%)"])),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "P&L: $%{y:+,.0f}<br>"
                "Share: %{customdata[1]:.1f}% of total<extra></extra>"
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

        # Initialise or update the alerts store when holdings change
        _pa_store = st.session_state.setdefault("_price_alerts", {})
        for _t in port_df["Ticker"]:
            _pa_store.setdefault(_t, {"target": 0.0, "floor": 0.0})

        _pa_rows = []
        for _, _pr in port_df.iterrows():
            _t   = _pr["Ticker"]
            _pa  = _pa_store[_t]
            _pa_rows.append({
                "Ticker":            _t,
                "Current ($)":       round(_pr["Price"], 2),
                "Take-Profit ($)":   _pa.get("target") or 0.0,
                "Floor Alert ($)":   _pa.get("floor")  or 0.0,
            })
        _pa_df = pd.DataFrame(_pa_rows)
        _pa_edited = st.data_editor(
            _pa_df,
            column_config={
                "Ticker":          st.column_config.TextColumn("Ticker", disabled=True),
                "Current ($)":     st.column_config.NumberColumn("Current ($)", disabled=True, format="$%.2f"),
                "Take-Profit ($)": st.column_config.NumberColumn("Take-Profit ($)", min_value=0.0, format="$%.2f",
                                    help="Alert when price rises above this level"),
                "Floor Alert ($)": st.column_config.NumberColumn("Floor Alert ($)", min_value=0.0, format="$%.2f",
                                    help="Alert when price drops below this level"),
            },
            use_container_width=True,
            hide_index=True,
            key="_pa_editor",
        )
        if st.button("💾 Save price alerts", key="_pa_save"):
            for _, _row in _pa_edited.iterrows():
                _t = _row["Ticker"]
                _pa_store[_t] = {
                    "target": float(_row["Take-Profit ($)"]) or 0.0,
                    "floor":  float(_row["Floor Alert ($)"]) or 0.0,
                }
            st.success("✅ Price alerts saved — active on next page load.")

        # Check triggers and surface them
        _pa_fired = []
        for _, _pr in port_df.iterrows():
            _t    = _pr["Ticker"]
            _px   = _pr["Price"]
            _pa   = _pa_store.get(_t, {})
            _tgt  = _pa.get("target") or 0.0
            _flr  = _pa.get("floor")  or 0.0
            if _tgt > 0 and _px >= _tgt:
                _pa_fired.append(("warning", f"🎯 **{_t}** hit take-profit target **${_tgt:.2f}** (current ${_px:.2f}) — consider locking in gains"))
            if _flr > 0 and _px <= _flr:
                _pa_fired.append(("danger",  f"🚨 **{_t}** breached floor alert **${_flr:.2f}** (current ${_px:.2f}) — review position now"))
        if _pa_fired:
            st.markdown("**🔔 Price Alert Triggers:**")
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
                            st.session_state.nav_page = "📒 Trade Journal"
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
                "**Underperforming** = the sector rallied but this position lagged — a Goldman rotation flag."
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
                            if len(_cl) >= 5:
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

                    # Goldman-style insight callouts
                    _valid = rs_df.dropna(subset=["Alpha (%)"])
                    if n_under > 0:
                        _worst = _valid.loc[_valid["Alpha (%)"].idxmin()]
                        st.warning(
                            f"⚠️ **{_worst['Ticker']}** is lagging its sector ETF ({_worst['ETF']}) "
                            f"by **{abs(_worst['Alpha (%)']):+.1f}%** over 6 months — "
                            f"the sector rallied but this position did not keep pace. "
                            f"Goldman would flag this for rotation review."
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
            "Goldman Sachs uses macro regime overlays to tilt sector weights 3–5% above/below benchmark."
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
                            else:
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
                        f"Goldman would recommend trimming these and rotating to "
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
            "Goldman uses universe-relative ranking to identify rotation candidates."
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
                        "Goldman would review for rotation into higher-ranked names in the same sector."
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
        import os
        try:
            import anthropic as _anthropic
            _anthro_key = (
                st.secrets.get("anthropic", {}).get("api_key")
                or os.environ.get("ANTHROPIC_API_KEY")
                or ""
            )
        except Exception:
            _anthro_key = ""

        st.markdown("### 🤖 AI Monitoring Brief")
        st.caption(
            "Generates a concise Goldman Sachs-style morning brief using Claude AI. "
            "Synthesises your portfolio positions, live alerts, recent news, and market context "
            "into actionable insights. Cached until you refresh."
        )

        if not _anthro_key or _anthro_key.startswith("sk-ant-your"):
            st.warning(
                "**Anthropic API key not configured.**  \n"
                "Add your key to `.streamlit/secrets.toml` under `[anthropic] api_key = \"sk-ant-...\"` "
                "or set the `ANTHROPIC_API_KEY` environment variable.  \n"
                "Get a key at [console.anthropic.com](https://console.anthropic.com)",
                icon="🔑",
            )
        else:
            _brief_cached = st.session_state.get("_ai_brief")
            _brief_ts     = st.session_state.get("_ai_brief_ts", "")

            _br1, _br2 = st.columns([6, 2])
            with _br1:
                if _brief_ts:
                    st.caption(f"Generated: {_brief_ts}")
            with _br2:
                _gen_btn = st.button(
                    "🔄 Refresh Brief" if _brief_cached else "✨ Generate Brief",
                    key="_gen_brief_btn",
                    use_container_width=True,
                    type="primary",
                )

            if _gen_btn or (_brief_cached is None):
                # Build context string for Claude
                _ctx_lines = [
                    f"Date: {datetime.now().strftime('%A, %B %d, %Y %H:%M ET')}",
                    f"Portfolio Value: ${total_val:,.0f}  |  Total P&L: ${total_pnl:,.0f} ({total_pnl_pct:+.1f}%)",
                    f"Avg Conviction Score: {avg_score:.0f}/100  |  Diversification: {div_score:.0f}/100 ({_div_label})",
                    "",
                    "## HOLDINGS",
                ]
                for _, _pr in port_df.iterrows():
                    _gap  = _pr.get("Gap to Stop (%)", "—")
                    _gap_s = f"{_gap:.1f}%" if isinstance(_gap, float) else str(_gap)
                    _ctx_lines.append(
                        f"  {_pr['Ticker']:6s} | Weight {_pr['Weight (%)']:.1f}% | "
                        f"P&L {_pr['P&L (%)']:+.1f}% (${_pr['P&L ($)']:,.0f}) | "
                        f"Signal: {_pr['Signal']} | Score: {_pr['Score']:.0f}/100 | "
                        f"Gap to Stop: {_gap_s}"
                    )

                _ctx_lines += ["", "## ACTIVE ALERTS"]
                if alert_list:
                    for _al in alert_list[:12]:
                        import re as _re
                        _ctx_lines.append(f"  [{_al['level'].upper()}] {_re.sub(r'[*_`]', '', _al['msg'])}")
                else:
                    _ctx_lines.append("  No active alerts.")

                # Market indices
                _live_idx = fetch_market_indices()
                _ctx_lines += ["", "## MARKET INDICES (today)"]
                for _ix in _live_idx:
                    _sign = "+" if _ix["change_pct"] >= 0 else ""
                    _ctx_lines.append(
                        f"  {_ix['short']:8s} {_ix['price']:,.2f}  "
                        f"({_sign}{_ix['change_pct']:.2f}%)"
                    )

                # Top news
                _news_ctx = st.session_state.get("_sidebar_news") or []
                if _news_ctx:
                    _ctx_lines += ["", "## TOP NEWS (recent headlines, sentiment)"]
                    for _ni in _news_ctx[:8]:
                        _ctx_lines.append(
                            f"  [{_ni['ticker']}] {_ni['label']:8s} | {_ni['title'][:100]}"
                        )

                _full_ctx = "\n".join(_ctx_lines)

                _system_prompt = (
                    "You are a senior portfolio analyst at Goldman Sachs. "
                    "Write a concise, professional morning monitoring brief for a portfolio manager. "
                    "Be specific: name tickers, cite numbers. Use a structured format with short sections. "
                    "Tone: confident, analytical, no fluff. Maximum 450 words. "
                    "End with 3 concrete action items ranked by urgency."
                )
                _user_prompt = (
                    f"Generate a morning monitoring brief for this portfolio:\n\n{_full_ctx}\n\n"
                    "Structure your response as:\n"
                    "**EXECUTIVE SUMMARY** (2-3 sentences)\n"
                    "**MARKET CONTEXT** (1-2 sentences on indices)\n"
                    "**PORTFOLIO HIGHLIGHTS** (top movers, key risks)\n"
                    "**RISK FLAGS** (from the alerts — what needs attention)\n"
                    "**KEY NEWS CATALYSTS** (material news affecting holdings)\n"
                    "**ACTION ITEMS** (3 prioritised, specific actions)\n"
                )

                with st.spinner("Generating brief with Claude AI…"):
                    try:
                        _client = _anthropic.Anthropic(api_key=_anthro_key)
                        _resp = _client.messages.create(
                            model="claude-haiku-4-5-20251001",
                            max_tokens=700,
                            system=_system_prompt,
                            messages=[{"role": "user", "content": _user_prompt}],
                        )
                        _brief_text = _resp.content[0].text
                        st.session_state["_ai_brief"]    = _brief_text
                        st.session_state["_ai_brief_ts"] = datetime.now().strftime("%b %d %Y %H:%M ET")
                        _brief_cached = _brief_text
                        _brief_ts     = st.session_state["_ai_brief_ts"]
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
                    f"Model: claude-haiku-4-5 · Generated {_brief_ts} · "
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
        "For any ticker that catches your eye, run a full analysis on the **Stock Analysis** page, "
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

        # Top 5 picks callout
        top5 = filtered.head(5)
        if not top5.empty:
            st.subheader("🏆 Top Picks")
            cols = st.columns(min(5, len(top5)))
            for i, (_, row) in enumerate(top5.iterrows()):
                with cols[i]:
                    score_color = "#00C851" if row["Score"] >= 70 else "#ffbb33"
                    st.markdown(
                        f"<div style='padding:10px;border-radius:8px;"
                        f"border:1px solid {score_color};text-align:center'>"
                        f"<b style='font-size:1.1em'>{row['Ticker']}</b><br>"
                        f"<span style='color:{score_color};font-size:1.4em;font-weight:bold'>"
                        f"{row['Score']}</span>/100<br>"
                        f"<small>{row['Sector']}</small><br>"
                        f"<small>{row['Signal']}</small><br>"
                        f"<small>1M: {row['1M Momentum']:+.1f}%</small>"
                        f"</div>",
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
elif page == "📈 Stock Analysis":
    st.title("📈 Stock Analysis")

    with st.sidebar:
        st.divider()
        name_to_ticker = dict(DEFAULT_TICKERS)
        # Merge watchlist tickers
        for t in st.session_state.watchlist:
            if t not in name_to_ticker.values():
                name_to_ticker[t] = t

        # Pre-select watchlist by default
        watchlist_names = [
            k for k, v in name_to_ticker.items() if v in st.session_state.watchlist
        ] + [t for t in st.session_state.watchlist if t not in name_to_ticker.values()]

        selected_names = st.multiselect(
            "Companies", options=list(name_to_ticker.keys()),
            default=[n for n in watchlist_names if n in name_to_ticker][:4],
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
        rows.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}" if price else "N/A",
            "Score": r["total"],
            "Signal": f"{r['rec']['icon']} {r['rec']['label']}",
            "Entry Zone": f"${r['entry_lo']:.2f}–${r['entry_hi']:.2f}" if r["entry_lo"] else "—",
            "Stop": f"${r['stop']:.2f}" if r["stop"] else "—",
            "Base Target": f"${targets['base']:.2f} ({targets['base_pct']:+.1f}%)" if targets else "—",
            "R:R": f"{rr_val:.1f}:1" if rr_val and rr_val > 0 else "—",
            "Shares": ps["shares"] if ps else "—",
            "Cost": f"${ps['total_cost']:,.0f}" if ps else "—",
            "Earnings": earn_label,
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
        .format({"Score": "{:.1f}"}),
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
                + (f"<br><small>📍 {r['upside']}</small>" if r["upside"] else "")
                + "</div>", unsafe_allow_html=True,
            )

            # Source links
            st.markdown(
                f"[📊 Yahoo Finance](https://finance.yahoo.com/quote/{ticker}) · "
                f"[📈 Finviz](https://finviz.com/quote.ashx?t={ticker}) · "
                f"[📰 SEC Filings](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=10-K) · "
                f"[🔍 News](https://finance.yahoo.com/quote/{ticker}/news/)"
            )

            plan_tab, chart_tab, risk_tab, deep_tab = st.tabs(
                ["📋 Trade Plan", "📈 Chart", "⚖️ Risk", "🔬 Deep Dive"]
            )

            # ── Trade Plan ────────────────────────────────────────────────
            with plan_tab:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Price", f"${price:.2f}" if price else "N/A")
                c2.metric("Entry Zone",
                          f"${r['entry_lo']:.2f}–${r['entry_hi']:.2f}" if r["entry_lo"] else "N/A")
                c3.metric("Stop Loss", f"${r['stop']:.2f}" if r["stop"] else "N/A",
                          delta=f"-{(price-r['stop'])/price*100:.1f}%" if price and r["stop"] else None,
                          delta_color="inverse", help=_tip("ATR Stop"))
                rr_val = risk_reward(price, r["stop"], targets["base"]) if price and r["stop"] and targets else None
                c4.metric("R:R", f"{rr_val:.1f}:1" if rr_val and rr_val > 0 else "N/A",
                          help=_tip("R:R Ratio"))

                if ps:
                    st.markdown("#### Position Sizing")
                    p1, p2, p3, p4 = st.columns(4)
                    p1.metric("Shares", f"{ps['shares']:,}", help=_tip("Position Sizing"))
                    p2.metric("Investment",  f"${ps['total_cost']:,.0f}",
                              f"{ps['portfolio_pct']:.1f}% of portfolio",
                              help=_tip("Position Sizing"))
                    p3.metric("Max Risk", f"${ps['actual_risk']:,.0f}",
                              f"{ps['risk_pct_actual']:.2f}%", delta_color="inverse",
                              help="Maximum dollar loss if stop is hit. Should not exceed 1.5–2% of portfolio.")
                    p4.metric("Risk/Share", f"${ps['risk_per_share']:.2f}",
                              help="Dollar distance from entry to stop per share.")

                if targets:
                    st.markdown("#### Price Scenarios")
                    sc1, sc2 = st.columns([2, 1])
                    with sc1:
                        sfig = go.Figure()
                        for case, tgt, pct, color in [
                            ("Bear", targets["bear"], targets["bear_pct"], "#ff4444"),
                            ("Base", targets["base"], targets["base_pct"], "#ffbb33"),
                            ("Bull", targets["bull"], targets["bull_pct"], "#00C851"),
                        ]:
                            sfig.add_trace(go.Bar(
                                x=[case], y=[tgt], name=case, marker_color=color,
                                text=f"${tgt:.2f}<br>{pct:+.1f}%", textposition="outside",
                            ))
                        if price:
                            sfig.add_hline(y=price, line_dash="dash", line_color="white",
                                           annotation_text=f"Now ${price:.2f}", annotation_position="right")
                        if r["stop"]:
                            sfig.add_hline(y=r["stop"], line_dash="dot", line_color="#ff4444",
                                           annotation_text=f"Stop ${r['stop']:.2f}", annotation_position="right")
                        sfig.update_layout(
                            height=300, template="plotly_dark", showlegend=False,
                            margin=dict(l=0, r=80, t=30, b=0),
                        )
                        st.plotly_chart(sfig, use_container_width=True)
                    with sc2:
                        st.markdown("**Scenarios**")
                        for label, tgt, pct, clr in [
                            ("🐂 Bull", targets["bull"], targets["bull_pct"], "#00C851"),
                            ("➡ Base", targets["base"], targets["base_pct"], "#ffbb33"),
                            ("🐻 Bear", targets["bear"], targets["bear_pct"], "#ff4444"),
                        ]:
                            st.markdown(
                                f"{label} `${tgt:.2f}` <span style='color:{clr}'>{pct:+.1f}%</span>",
                                unsafe_allow_html=True,
                            )
                        if rr_val and rr_val > 0:
                            quality = "✅ Favourable" if rr_val >= 2.5 else ("⚠️ Marginal" if rr_val >= 1.5 else "❌ Poor")
                            st.markdown(f"**R:R** `{rr_val:.1f}:1` — {quality}")

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
                            st.warning(f"⚠️ Earnings in {days} days ({earn}) — consider reduced size.")
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

    # Client brief
    with st.expander("📄 Client Brief"):
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
# PAGE 4 — TRADE JOURNAL
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

    # ── Pre-fill from recommendation card ────────────────────────────────────
    prefill = st.session_state.pop("_prefill_trade", {})

    # ── Log a Trade form ──────────────────────────────────────────────────────
    # Pre-compute cost basis hint before entering the form so the default is
    # available as a widget value (form widgets don't update reactively).
    _prefill_ticker  = prefill.get("ticker", "").strip().upper()
    _prefill_action  = prefill.get("action", "SELL")
    _cost_basis_hint = 0.01
    _cost_hint_label = "Cost Basis / share ($)"
    if _prefill_action == "SELL" and _prefill_ticker:
        _cb_match = st.session_state.holdings_df[
            st.session_state.holdings_df["Ticker"] == _prefill_ticker
        ]
        if not _cb_match.empty:
            _cost_basis_hint = float(_cb_match.iloc[0]["Avg Cost ($)"])
            _cost_hint_label = f"Cost Basis / share ($) — auto-filled from holdings (${_cost_basis_hint:.2f})"

    with st.expander("➕ Log a Trade", expanded=bool(prefill)):
        # st.form guarantees exactly one submission per button click,
        # preventing the duplicate-entry glitch caused by page reruns.
        with st.form("log_trade_form", clear_on_submit=True):
            f_col1, f_col2, f_col3 = st.columns(3)
            with f_col1:
                action = st.radio(
                    "Action", ["SELL", "BUY"], horizontal=True,
                    index=0 if _prefill_action == "SELL" else 1,
                )
            with f_col2:
                ticker_input = st.text_input(
                    "Ticker", value=_prefill_ticker, placeholder="e.g. NET",
                ).strip().upper()
            with f_col3:
                trigger_type = st.selectbox(
                    "Reason",
                    ["MANUAL", "RECOMMENDATION", "STOP_HIT", "REBALANCE"],
                    index=["MANUAL", "RECOMMENDATION", "STOP_HIT", "REBALANCE"].index(
                        prefill.get("trigger", "MANUAL")
                    ),
                )

            f_col4, f_col5, f_col6 = st.columns(3)
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
            with f_col6:
                cost_basis_val = st.number_input(
                    _cost_hint_label,
                    min_value=0.01, value=max(0.01, _cost_basis_hint),
                    step=0.01, format="%.2f",
                    help="Your average purchase price per share. "
                         "Auto-filled when the ticker matches a current holding.",
                )

            notes_val = st.text_input(
                "Notes (optional)", value=prefill.get("notes", ""),
                placeholder="e.g. Partial profit on bearish signal",
            )

            submitted = st.form_submit_button("✅ Record Trade", type="primary")

        # Process submission outside the form block
        if submitted:
            if not ticker_input:
                st.error("Enter a ticker symbol.")
            elif shares_val <= 0:
                st.error("Shares must be greater than 0.")
            elif price_val <= 0:
                st.error("Price must be greater than 0.")
            else:
                realized_pnl = (
                    compute_realized_pnl(shares_val, price_val, cost_basis_val)
                    if action == "SELL" else None
                )
                record = {
                    "ticker":       ticker_input,
                    "action":       action,
                    "shares":       shares_val,
                    "price":        price_val,
                    "cost_basis":   cost_basis_val if action == "SELL" else None,
                    "realized_pnl": realized_pnl,
                    "notes":        notes_val or None,
                    "trigger_type": trigger_type,
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

        edited_trades = st.data_editor(
            display_df[show_cols],
            column_config={
                "Delete?":      st.column_config.CheckboxColumn("Delete?", default=False),
                "traded_at":    st.column_config.TextColumn("Date / Time"),
                "ticker":       st.column_config.TextColumn("Ticker"),
                "action":       st.column_config.TextColumn("Action"),
                "shares":       st.column_config.NumberColumn("Shares", format="%.0f"),
                "price":        st.column_config.NumberColumn("Price ($)", format="$%.2f"),
                "cost_basis":   st.column_config.NumberColumn("Cost Basis ($)", format="$%.2f"),
                "realized_pnl": st.column_config.NumberColumn("Realized P&L ($)", format="$%+,.2f"),
                "trigger_type": st.column_config.TextColumn("Reason"),
                "notes":        st.column_config.TextColumn("Notes"),
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
                st.session_state.trades_df = db.load_trades()
                st.rerun()

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

st.caption("Data: Yahoo Finance · Algorithmic analysis · Not financial advice")
