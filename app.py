import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date

import html as _html
from stock_analyzer.data import (
    DEFAULT_TICKERS, fetch_ticker_bundle, fetch_financials_from_info,
    fetch_spy, fetch_live_prices, market_status, curate_news_items,
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
    TICKER_SECTORS,
)
from stock_analyzer.scanner import SECTOR_UNIVERSE, scan_sectors
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
if not st.session_state.get("db_loaded"):
    st.session_state.holdings_df = db.load_holdings()
    st.session_state.watchlist   = db.load_watchlist()
    st.session_state.db_loaded   = True

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📊 Portfolio Manager")
    page = st.radio(
        "Navigate",
        ["🏠 My Portfolio", "🔍 Market Scanner", "📈 Stock Analysis"],
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

    st.divider()
    portfolio_value = st.number_input(
        "Portfolio Value ($)", min_value=1_000, max_value=10_000_000,
        value=50_000, step=1_000, format="%d",
    )
    st.caption(f"Risk/trade: **${portfolio_value * MODERATE_RISK_PCT:,.0f}** (1.5% · Moderate)")
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
    }

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
            if db.save_holdings(edited):
                st.session_state.holdings_df = edited
                st.session_state.db_loaded = False  # force re-load from DB on next run
                st.success("✅ Saved to Supabase — will persist across sessions.")
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

    # Summary metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Portfolio Value",   f"${total_val:,.0f}")
    m2.metric("Total P&L",         f"${total_pnl:,.0f}", f"{total_pnl_pct:+.1f}%",
              delta_color="normal")
    m3.metric("Positions",         len(port_df))
    m4.metric("Avg Conviction",    f"{avg_score:.0f}/100")
    winners = (port_df["P&L (%)"] > 0).sum()
    m5.metric("Win Rate",          f"{winners}/{len(port_df)} positions")

    st.divider()

    # Alerts
    alert_list = alerts(port_df)
    if alert_list:
        with st.expander(f"🚨 {len(alert_list)} Alert(s) — click to review", expanded=True):
            for a in alert_list:
                if a["level"] == "danger":
                    st.error(a["msg"])
                elif a["level"] == "warning":
                    st.warning(a["msg"])
                else:
                    st.info(a["msg"])

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

    # Sector exposure
    sector_df = sector_exposure(port_df)
    if not sector_df.empty:
        with st.expander("🏭 Sector Exposure", expanded=False):
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

    # Position table with protective stops
    st.subheader("Position Detail & Protective Stops")
    st.caption(
        "Stop ratchets up automatically as gains grow — "
        "breakeven guard at +10%, protects 10% at +25%, 25% at +50%, 40% at +75%.  \n"
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

    display_cols = ["Ticker", "Shares", "Avg Cost", "Price", "P&L ($)", "P&L (%)",
                    "Weight (%)", "Stop", "Stop Type", "Gap to Stop (%)", "Signal", "Score"]
    styled = (
        port_df[display_cols].style
        .map(_pnl_color, subset=["P&L ($)", "P&L (%)"])
        .map(_stop_color, subset=["Stop Type"])
        .map(_sig_color, subset=["Signal"])
        .format({
            "Avg Cost": "${:.2f}", "Price": "${:.2f}",
            "P&L ($)": "${:,.0f}", "P&L (%)": "{:+.1f}%",
            "Weight (%)": "{:.1f}%", "Stop": "${:.2f}",
            "Gap to Stop (%)": "{:.1f}%", "Score": "{:.0f}",
        })
    )
    st.dataframe(styled, use_container_width=True)

    # Rebalancing actions
    actions = rebalance_actions(port_df)
    if actions:
        st.subheader("💡 Advisor Recommendations")
        for a in actions:
            st.info(a)
    else:
        st.success("✅ Portfolio is well-balanced — no rebalancing actions needed at this time.")

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
        d2.metric("Stop Loss",   f"${ps_row['Stop']:.2f}",     ps_row['Stop Type'])
        d3.metric("Gap to Stop", f"{ps_row['Gap to Stop (%)']:.1f}%")
        d4.metric("Composite Score", f"{r['total']:.0f}/100",  r['rec']['label'])

        # Score breakdown row
        sb1, sb2, sb3 = st.columns(3)
        t_contrib  = round(r['t_score'] * 0.45, 1)
        f_contrib  = round(r['f_score'] * 0.40, 1)
        s_contrib  = round(r['s_score'] * 0.15, 1)
        sb1.metric("Technical",    f"{r['t_score']:.0f}/100",
                   f"+{t_contrib} pts (45%)",
                   help="RSI · MACD · Bollinger Bands · MA trend · Volume")
        sb2.metric("Fundamental",  f"{r['f_score']:.0f}/100",
                   f"+{f_contrib} pts (40%)",
                   help="Forward P/E · Revenue & Earnings growth · Margins · Debt/Equity")
        sb3.metric("Sentiment",    f"{r['s_score']:.0f}/100",
                   f"+{s_contrib} pts (15%)",
                   help="VADER analysis of latest news headlines from Yahoo Finance")

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
                f"(Technical {r['t_score']:.0f} × 45% + Fundamental {r['f_score']:.0f} × 40% + Sentiment {r['s_score']:.0f} × 15%)"
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
                          delta_color="inverse")
                rr_val = risk_reward(price, r["stop"], targets["base"]) if price and r["stop"] and targets else None
                c4.metric("R:R", f"{rr_val:.1f}:1" if rr_val and rr_val > 0 else "N/A")

                if ps:
                    st.markdown("#### Position Sizing")
                    p1, p2, p3, p4 = st.columns(4)
                    p1.metric("Shares", f"{ps['shares']:,}")
                    p2.metric("Investment",  f"${ps['total_cost']:,.0f}",
                              f"{ps['portfolio_pct']:.1f}% of portfolio")
                    p3.metric("Max Risk", f"${ps['actual_risk']:,.0f}",
                              f"{ps['risk_pct_actual']:.2f}%", delta_color="inverse")
                    p4.metric("Risk/Share", f"${ps['risk_per_share']:.2f}")

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
                r1.metric("Sharpe",       f"{rm['sharpe']:.2f}",
                          help=">1.0 = good risk-adjusted return")
                r2.metric("Sortino",      f"{rm['sortino']:.2f}",
                          help=">1.5 = strong downside-adjusted return")
                r3.metric("Max Drawdown", f"{rm['max_drawdown']:.1f}%",
                          help="Worst peak-to-trough in selected period")
                r4.metric("VaR (95%)",    f"{rm['var_95']:.2f}%",
                          help="Daily loss not exceeded 95% of the time")
                r5.metric("Beta vs S&P",  f"{rm['beta']:.2f}" if rm["beta"] else "N/A",
                          help="1.0 = market-aligned. >1.3 = high sensitivity")

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
                dd1, dd2, dd3 = st.columns(3)
                with dd1:
                    st.markdown(f"**Technical — {r['t_score']}/100**")
                    for k, v in r["t_signals"].items():
                        clr = "#00C851" if "bullish" in v.lower() else (
                              "#ff4444" if "bearish" in v.lower() else "#aaa")
                        st.markdown(
                            f"<small style='color:{clr}'>●</small> **{k}**: {v}",
                            unsafe_allow_html=True,
                        )
                with dd2:
                    st.markdown(f"**Fundamental — {r['f_score']}/100**")
                    for k, v in r["f_signals"].items():
                        clr = "#00C851" if any(w in v.lower() for w in
                              ["strong","excellent","good","healthy","under"]) else (
                              "#ff4444" if any(w in v.lower() for w in
                              ["declin","contract","high lev","expensive","loss"]) else "#aaa")
                        st.markdown(
                            f"<small style='color:{clr}'>●</small> **{k}**: {v}",
                            unsafe_allow_html=True,
                        )
                    fin = r["financials"]
                    st.markdown("---")
                    for label, key in [("Trailing P/E", "pe_ratio"), ("Forward P/E", "forward_pe"),
                                       ("EPS (TTM)", "eps"), ("Current Ratio", "current_ratio")]:
                        v = fin.get(key)
                        if v:
                            st.markdown(f"- **{label}**: {v:.2f}")
                with dd3:
                    st.markdown(f"**Sentiment — {r['s_score']:.0f}/100**")
                    st.caption("Source: Yahoo Finance news · VADER compound score (−1 bearish → +1 bullish)")
                    for h in r["headlines"][:6]:
                        clr = "#00b300" if h["label"] == "Positive" else (
                              "#ff4444" if h["label"] == "Negative" else "#888")
                        lbl = "▲" if h["label"] == "Positive" else (
                              "▼" if h["label"] == "Negative" else "–")
                        headline_text = h["headline"][:90] + ("…" if len(h["headline"]) > 90 else "")
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

st.caption("Data: Yahoo Finance · Algorithmic analysis · Not financial advice")
