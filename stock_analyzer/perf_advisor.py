"""
Performance attribution and diagnostics advisor.

Computes per-position alpha vs SPY and sector ETF for a selected period,
categorises each holding, then generates prioritised action recommendations
with dollar impact, thesis status, and institutional context.
"""

import pandas as pd


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def compute_attribution(
    port_df: pd.DataFrame,
    held_data: dict,
    spy_df: pd.DataFrame,
    n_days: int,
    sector_etf_rets: dict | None = None,
    period_label: str = "3M",
) -> pd.DataFrame:
    """
    Returns a DataFrame with per-position performance attribution.
    Columns: Ticker, Sector, ETF, Weight(%), Market Value, Holding Ret(%),
             SPY Ret(%), Sector Ret(%), Alpha vs SPY(%), Alpha vs Sector(%),
             Dollar Alpha($), Category, Score, Signal
    """
    if port_df.empty or spy_df is None or spy_df.empty:
        return pd.DataFrame()

    from stock_analyzer.portfolio import SECTOR_ETF

    spy_close = spy_df["Close"].dropna().copy()
    if spy_close.index.tz is not None:
        spy_close.index = spy_close.index.tz_localize(None)

    n = min(n_days, len(spy_close) - 1)
    if n < 2:
        return pd.DataFrame()

    spy_ret = (float(spy_close.iloc[-1]) / float(spy_close.iloc[-(n + 1)]) - 1) * 100

    rows = []
    for _, row in port_df.iterrows():
        ticker = row["Ticker"]
        weight = _f(row.get("Weight (%)"))
        mval   = _f(row.get("Market Value"))
        sector = str(row.get("Sector", "Other"))
        score  = _f(row.get("Score"))
        signal = str(row.get("Signal", ""))

        data = held_data.get(ticker)
        if not data:
            continue
        hist = data.get("df")
        if hist is None or hist.empty or "Close" not in hist.columns:
            continue

        closes = hist["Close"].dropna().copy()
        if closes.index.tz is not None:
            closes.index = closes.index.tz_localize(None)

        nh = min(n_days, len(closes) - 1)
        if nh < 2:
            continue

        holding_ret = (float(closes.iloc[-1]) / float(closes.iloc[-(nh + 1)]) - 1) * 100

        etf = SECTOR_ETF.get(sector, "SPY")
        sector_ret = None
        if sector_etf_rets:
            etf_data = sector_etf_rets.get(etf) or {}
            raw = etf_data.get(period_label)
            if raw is not None:
                sector_ret = _f(raw)

        alpha_spy = holding_ret - spy_ret
        alpha_sec = (holding_ret - sector_ret) if sector_ret is not None else None

        # Dollar impact: extra $ vs holding SPY at same portfolio weight
        dollar_alpha = alpha_spy / 100 * mval

        if alpha_spy >= 5:
            category = "Alpha Generator" if (alpha_sec is not None and alpha_sec >= 3) else "Sector Rider"
        elif alpha_spy <= -5:
            category = "Alpha Destroyer"
        else:
            category = "In Line"

        rows.append({
            "Ticker":               ticker,
            "Sector":               sector,
            "ETF":                  etf,
            "Weight (%)":           round(weight, 1),
            "Market Value":         round(mval, 0),
            "Holding Ret (%)":      round(holding_ret, 1),
            "SPY Ret (%)":          round(spy_ret, 1),
            "Sector Ret (%)":       round(sector_ret, 1) if sector_ret is not None else None,
            "Alpha vs SPY (%)":     round(alpha_spy, 1),
            "Alpha vs Sector (%)":  round(alpha_sec, 1) if alpha_sec is not None else None,
            "Dollar Alpha ($)":     round(dollar_alpha, 0),
            "Category":             category,
            "Score":                score,
            "Signal":               signal,
        })

    if not rows:
        return pd.DataFrame()

    return (
        pd.DataFrame(rows)
        .sort_values("Dollar Alpha ($)", ascending=False)
        .reset_index(drop=True)
    )


def build_perf_recommendations(
    attr_df: pd.DataFrame,
    portfolio_value: float,
    period_label: str = "3M",
) -> list[dict]:
    """
    Returns ranked recommendation dicts from the attribution DataFrame.
    Each dict: priority, type, ticker, title, metrics, problem,
               root_cause, recommendation, expected_outcome, institutional_lens
    """
    if attr_df.empty:
        return []

    pv = portfolio_value if portfolio_value > 0 else 50_000.0
    recs: list[dict] = []

    for _, row in attr_df.iterrows():
        ticker    = row["Ticker"]
        category  = row["Category"]
        h_ret     = _f(row["Holding Ret (%)"])
        spy_ret   = _f(row["SPY Ret (%)"])
        alpha_spy = _f(row["Alpha vs SPY (%)"])
        alpha_sec = row["Alpha vs Sector (%)"]
        alpha_sec_v = _f(alpha_sec) if alpha_sec is not None else None
        sect_ret  = row["Sector Ret (%)"]
        sect_ret_v = _f(sect_ret) if sect_ret is not None else None
        dollar_alpha = _f(row["Dollar Alpha ($)"])
        mval      = _f(row["Market Value"])
        score     = _f(row["Score"])
        signal    = str(row["Signal"])
        sector    = row["Sector"]
        etf       = row["ETF"]

        # ── ALPHA GENERATORS ─────────────────────────────────────────────────
        if category == "Alpha Generator":
            recs.append({
                "priority": "OK",
                "type":     "alpha_generator",
                "ticker":   ticker,
                "title":    f"{ticker} — Genuine Alpha Generator ({alpha_spy:+.1f}% vs SPY)",
                "metrics": {
                    "Holding Return":  f"{h_ret:+.1f}%",
                    "vs S&P 500":      f"{alpha_spy:+.1f}%",
                    f"vs {etf}":       f"{alpha_sec_v:+.1f}%" if alpha_sec_v is not None else "—",
                    f"$ Added ({period_label})": f"+${abs(dollar_alpha):,.0f}",
                },
                "problem": "",
                "root_cause": (
                    f"Score {score:.0f}/100 · Signal: {signal}. "
                    f"Outperforming both SPY ({alpha_spy:+.1f}%) and sector ETF {etf}"
                    + (f" ({alpha_sec_v:+.1f}%)" if alpha_sec_v is not None else "")
                    + f" — this is stock-specific strength, not just sector momentum."
                ),
                "recommendation": (
                    f"Hold confidently. If composite score remains above 60, this is a candidate "
                    "to add on any meaningful pullback — not to chase the current price. "
                    "Ensure the ratchet stop is updated to protect the accumulated gains."
                ),
                "expected_outcome": (
                    f"${abs(dollar_alpha):,.0f} of outperformance generated vs holding SPY at this weight over {period_label}. "
                    "Holding genuine alpha generators through their run is the primary driver of long-term outperformance."
                ),
                "institutional_lens": (
                    "Idiosyncratic alpha — outperformance above the sector ETF — is the rarest result in a portfolio. "
                    "Factor research teams distinguish three return layers: market beta (free, get it from SPY), "
                    "sector beta (cheap, get it from an ETF), and stock-specific alpha (what you research and earn). "
                    f"{ticker} is delivering on all three. "
                    "The discipline is in recognising this and letting it run — not taking profits too early out of anxiety."
                ),
            })

        # ── SECTOR RIDERS ─────────────────────────────────────────────────────
        elif category == "Sector Rider":
            recs.append({
                "priority": "MONITOR",
                "type":     "sector_rider",
                "ticker":   ticker,
                "title":    f"{ticker} — Sector Rider, Not Alpha ({alpha_spy:+.1f}% vs SPY, {alpha_sec_v:+.1f}% vs {etf})" if alpha_sec_v is not None else f"{ticker} — Sector Tailwind Driving Return",
                "metrics": {
                    "Holding Return": f"{h_ret:+.1f}%",
                    "vs S&P 500":     f"{alpha_spy:+.1f}%",
                    f"vs {etf}":      f"{alpha_sec_v:+.1f}%" if alpha_sec_v is not None else "—",
                    f"$ vs SPY ({period_label})": f"+${abs(dollar_alpha):,.0f}",
                },
                "problem": (
                    f"**{ticker}** is up {h_ret:+.1f}% vs SPY's {spy_ret:+.1f}% — looks like outperformance. "
                    + (f"But vs its sector ETF ({etf}: {sect_ret_v:+.1f}%), it's only {alpha_sec_v:+.1f}%. " if alpha_sec_v is not None and sect_ret_v is not None else "")
                    + "This gain is primarily sector momentum, not stock-specific skill."
                ),
                "root_cause": (
                    f"Score {score:.0f}/100 · Signal: {signal}. "
                    f"The {sector} sector is outperforming the market — {ticker} is largely riding that wave."
                ),
                "recommendation": (
                    "No immediate action — the position is working. But be aware: "
                    f"your edge here is sector positioning, not stock picking. "
                    f"If {sector} rotates out of favor, this gain may reverse faster than the sector ETF. "
                    f"Consider whether {etf} directly would give cleaner sector exposure "
                    "with less single-stock earnings risk."
                ),
                "expected_outcome": (
                    f"Monitor {etf} momentum. If the sector ETF starts lagging SPY, "
                    "this position will typically be the first to feel the rotation."
                ),
                "institutional_lens": (
                    "Confusing beta return with alpha is the most common performance attribution mistake. "
                    f"A stock that gained {h_ret:.0f}% when its sector gained "
                    + (f"{sect_ret_v:.0f}% " if sect_ret_v is not None else "similarly ")
                    + "generated minimal idiosyncratic alpha. "
                    "Institutional PMs are ruthless about this distinction: sector returns are 'free' — "
                    "you can get them cheaper from an ETF. Stock-specific alpha is what you're paid to generate. "
                    "If you can't articulate why this stock outperformed its sector, "
                    "the honest answer is that it didn't."
                ),
            })

        # ── ALPHA DESTROYERS ──────────────────────────────────────────────────
        elif category == "Alpha Destroyer":
            priority = "HIGH" if alpha_spy <= -15 else "MEDIUM"

            opp_cost = abs(alpha_spy / 100 * mval)  # $ lost vs holding SPY
            etf_opp_cost = abs(alpha_sec_v / 100 * mval) if alpha_sec_v is not None else None

            # Thesis assessment from composite score
            if score >= 60:
                thesis = (
                    f"Score {score:.0f}/100 — fundamentals still solid. "
                    "This may be temporary technical weakness or a positioning headwind, not thesis failure."
                )
                action = (
                    f"Hold position but set a 30-day review trigger. "
                    "If composite score drops below 55 OR the alpha gap widens further, reduce immediately. "
                    "The fundamentals justify patience; the performance does not justify complacency."
                )
            elif score >= 44:
                thesis = (
                    f"Score {score:.0f}/100 — borderline. Fundamental conviction is fading "
                    "and price action confirms the weakness."
                )
                action = (
                    f"Trim 40–50% of the position immediately. "
                    f"Redeploy into {etf} (maintains {sector} exposure without single-stock drag) "
                    "or the highest-scoring name in the same sector. "
                    "Keep a residual position only if you have a specific near-term catalyst."
                )
            else:
                thesis = (
                    f"Score {score:.0f}/100 — both performance and fundamentals are weak. "
                    "This is a broken thesis, not a temporary setback."
                )
                action = (
                    f"Exit or reduce to a minimum position. "
                    f"Rotating into {etf} maintains {sector} exposure while stopping the alpha bleed. "
                    "Institutional rotation rule: when a position underperforms its sector by more than 15% "
                    "AND has a score below 44, the thesis is statistically broken."
                )

            recs.append({
                "priority": priority,
                "type":     "alpha_destroyer",
                "ticker":   ticker,
                "title":    f"{ticker} — Alpha Destroyer ({alpha_spy:.1f}% vs SPY over {period_label})",
                "metrics": {
                    "Holding Return":  f"{h_ret:+.1f}%",
                    "vs S&P 500":      f"{alpha_spy:+.1f}%",
                    f"vs {etf}":       f"{alpha_sec_v:+.1f}%" if alpha_sec_v is not None else "—",
                    "Opportunity Cost": f"-${opp_cost:,.0f}",
                },
                "problem": (
                    f"**{ticker}** returned {h_ret:+.1f}% vs SPY's {spy_ret:+.1f}% over {period_label}. "
                    f"That's **{alpha_spy:.1f}% of underperformance** — a direct opportunity cost of "
                    f"**${opp_cost:,.0f}** vs holding SPY at the same weight."
                    + (f" Vs sector ETF {etf}, the gap is {alpha_sec_v:.1f}% (≈${etf_opp_cost:,.0f})." if alpha_sec_v is not None and etf_opp_cost is not None else "")
                ),
                "root_cause": thesis,
                "recommendation": action,
                "expected_outcome": (
                    f"Stopping the alpha bleed now avoids compounding a ${opp_cost:,.0f} gap further. "
                    f"Rotating into {etf} would have generated approximately ${opp_cost:,.0f} more over {period_label} "
                    "while maintaining the same sector exposure — with lower single-stock risk."
                ),
                "institutional_lens": (
                    "The opportunity cost question is the most powerful forcing function in portfolio management: "
                    f"'What would I have made holding {etf} instead of {ticker}?' "
                    f"The answer is ${opp_cost:,.0f} more, with less work and less single-stock risk. "
                    "Institutional PM review process explicitly quantifies opportunity cost every quarter, "
                    "not to trigger automatic selling, but to force a conscious decision: "
                    "'I am choosing to hold this underperformer. Here is my specific thesis for recovery.' "
                    "Without that explicit thesis, holding becomes hope. Hope is not a strategy."
                ),
            })

    # Sort: HIGH → MEDIUM → MONITOR → OK
    _order = {"HIGH": 0, "MEDIUM": 1, "MONITOR": 2, "OK": 3}
    recs.sort(key=lambda x: _order.get(x["priority"], 4))
    return recs
