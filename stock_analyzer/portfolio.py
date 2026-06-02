import math
import pandas as pd
import numpy as np


def _safe_float(val, default: float = 0.0) -> float:
    """Convert val to float, returning default for None/NaN/empty-string."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


TICKER_SECTORS = {
    "AVGO": "Semiconductors", "NVDA": "Semiconductors", "AMD": "Semiconductors",
    "MU": "Semiconductors", "QCOM": "Semiconductors", "INTC": "Semiconductors",
    "AMAT": "Semiconductors", "ASML": "Semiconductors", "TXN": "Semiconductors",
    "AAPL": "Consumer Tech", "AMZN": "Consumer Tech", "NFLX": "Consumer Tech",
    "SHOP": "Consumer Tech", "UBER": "Consumer Tech", "ABNB": "Consumer Tech",
    "TSLA": "EV & Auto", "ENPH": "Clean Energy", "FSLR": "Clean Energy",
    "NEE": "Clean Energy", "BEP": "Clean Energy",
    "CRWD": "Cybersecurity", "NET": "Cybersecurity", "PANW": "Cybersecurity",
    "ZS": "Cybersecurity", "FTNT": "Cybersecurity", "OKTA": "Cybersecurity", "S": "Cybersecurity",
    "DELL": "Enterprise Tech", "ORCL": "Enterprise Tech", "IBM": "Enterprise Tech",
    "HPE": "Enterprise Tech", "SAP": "Enterprise Tech",
    "PLTR": "AI & Data", "AI": "AI & Data", "MDB": "AI & Data", "SNOW": "AI & Data",
    "PATH": "AI & Data", "IONQ": "AI & Data",
    "MSFT": "AI & Cloud", "GOOGL": "AI & Cloud", "META": "AI & Cloud",
    "CRM": "AI & Cloud", "NOW": "AI & Cloud", "DDOG": "AI & Cloud",
    "LLY": "Healthcare", "NVO": "Healthcare", "ABBV": "Healthcare",
    "ISRG": "Healthcare", "MRNA": "Healthcare", "REGN": "Healthcare",
    "JPM": "Financials", "V": "Financials", "MA": "Financials",
    "GS": "Financials", "SQ": "Financials", "COIN": "Financials",
    "LMT": "Defense", "RTX": "Defense", "NOC": "Defense", "GD": "Defense",
    "XOM": "Energy", "CVX": "Energy", "OXY": "Energy", "COP": "Energy",
}

# Ratchet stop levels: as gains grow, floor the stop to protect accumulated profit
_RATCHET_LEVELS = [
    (75, 0.40, "Protect 40% gain"),
    (50, 0.25, "Protect 25% gain"),
    (25, 0.10, "Protect 10% gain"),
    (10, 0.02, "Breakeven guard"),
]


def protective_stop(
    current_price: float, avg_cost: float, atr_stop: float
) -> tuple[float, str]:
    """
    Ratchet stop upward as gains accumulate so profits are never fully surrendered.
    Returns (stop_price, label).
    """
    if avg_cost <= 0:
        return atr_stop, "ATR Stop"
    gain_pct = (current_price - avg_cost) / avg_cost * 100
    for threshold, multiplier, label in _RATCHET_LEVELS:
        if gain_pct >= threshold:
            floor = avg_cost * (1 + multiplier)
            return round(max(atr_stop, floor), 2), label
    return round(atr_stop, 2), "ATR Stop"


def build_portfolio_df(
    holdings: list[dict], loaded_data: dict,
    manual_stops: dict | None = None,
) -> pd.DataFrame:
    """
    holdings: [{"ticker": "AVGO", "shares": 10, "avg_cost": 200.0}, ...]
    loaded_data: dict of ticker -> load_all() result
    manual_stops: optional {ticker: {"stop_price", "set_at", ...}} — when set
        for a ticker, the user's stop overrides the ATR-derived stop. All
        downstream consumers (Brief, Analysis, Scorecard, risk advisor) read
        the merged value via the returned "Stop" column. The "Stop Source"
        column records "manual" vs "ATR" / ratchet label so the UI can
        render a badge distinguishing user overrides from computed defaults.
    """
    manual_stops = manual_stops or {}
    rows = []
    for h in holdings:
        ticker = str(h.get("Ticker", h.get("ticker", "")) or "").strip().upper()
        shares = _safe_float(h.get("Shares", h.get("shares")))
        avg_cost = _safe_float(h.get("Avg Cost ($)", h.get("avg_cost")))
        if not ticker or shares <= 0 or avg_cost <= 0:
            continue
        r = loaded_data.get(ticker)
        if not r or not r.get("current_price"):
            continue

        price = r["current_price"]
        market_val = round(price * shares, 2)
        cost_basis = round(avg_cost * shares, 2)
        pnl_dollar = round(market_val - cost_basis, 2)
        pnl_pct = round((price - avg_cost) / avg_cost * 100, 1)

        # Stop data integrity: missing stop is a data issue, not a position issue.
        # Never silently substitute a fabricated stop — that would let mechanical
        # SELL rules fire on a number nobody chose. Surface None downstream so
        # consumers can treat it as "manual review required."
        _raw_stop = r.get("stop")
        if _raw_stop is None or _raw_stop <= 0:
            stop, stop_label, gap_to_stop = None, "Stop Unavailable", None
        else:
            stop, stop_label = protective_stop(price, avg_cost, _raw_stop)
            gap_to_stop = round((price - stop) / price * 100, 1)

        # Manual-stop override: user actioned a Brief "raise stop" recommendation
        # and recorded the new level. Persisted in Supabase manual_stops table
        # and merged here so every downstream consumer sees the user's stop,
        # not the ATR-derived default. Two semantic guards:
        #   1. Only override if the manual stop is TIGHTER (closer to price) —
        #      a stale manual stop below a fresh ratchet floor would erode
        #      profit protection; the ratchet should win in that case.
        #   2. Stop Type column flips to "Manual" so the UI badges it; the
        #      original ATR/Ratchet label is preserved in Stop Type Auto so
        #      Trade Plan can show "your manual stop overrides ATR Stop $X".
        _ms = manual_stops.get(ticker) if manual_stops else None
        stop_type_auto = stop_label
        if _ms and stop is not None:
            _ms_price = float(_ms.get("stop_price") or 0)
            if _ms_price > 0 and _ms_price >= stop:
                stop = round(_ms_price, 2)
                stop_label = "Manual"
                gap_to_stop = round((price - stop) / price * 100, 1)

        rows.append({
            "Ticker": ticker,
            # Sector: prefer the curated granular bucket (Semiconductors, AI &
            # Data, …); fall back to the ticker's actual yfinance .info sector
            # (already fetched by load_all) before the "Other" catch-all. Without
            # this, every unmapped name piled into "Other", inflating it past the
            # hard cap (ESTC, a Tech/Software name, landed in a 44% "Other"
            # bucket) — a classification artifact, not a real concentration.
            "Sector": TICKER_SECTORS.get(ticker) or (r.get("sector") or "").strip() or "Other",
            "Shares": int(shares),
            "Avg Cost": avg_cost,
            "Price": price,
            "Market Value": market_val,
            "P&L ($)": pnl_dollar,
            "P&L (%)": pnl_pct,
            "Weight (%)": 0.0,
            "Stop": stop,
            "Stop Type": stop_label,
            "Stop Type Auto": stop_type_auto if stop is not None else None,
            "Manual Stop Set At": (_ms or {}).get("set_at") if _ms else None,
            "Gap to Stop (%)": gap_to_stop,
            "Signal": f"{r['rec']['icon']} {r['rec']['label']}",
            "Score": r["total"],
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        total_val = float(df["Market Value"].sum())
        if total_val > 0:
            df["Weight (%)"] = (df["Market Value"] / total_val * 100).round(1)
        else:
            # Every row has a 0 market value — cold cache or every yfinance call
            # failed. Leave Weight at its 0.0 default rather than letting
            # inf/NaN propagate into rebalancer / risk_advisor / brief gates.
            df["Weight (%)"] = 0.0
    return df


def sector_exposure(portfolio_df: pd.DataFrame) -> pd.DataFrame:
    if portfolio_df.empty:
        return pd.DataFrame()
    return (
        portfolio_df.groupby("Sector")["Market Value"]
        .sum()
        .reset_index()
        .rename(columns={"Market Value": "Value"})
        .assign(Pct=lambda d: (d["Value"] / d["Value"].sum() * 100).round(1))
        .sort_values("Pct", ascending=False)
    )


def alerts(portfolio_df: pd.DataFrame, held_data: dict | None = None) -> list[dict]:
    """
    Returns list of alert dicts with keys: level, msg, category.
    Levels: danger, warning, info.
    Categories: stop, signal, concentration, earnings, revisions.
    """
    from datetime import date as _date, datetime as _datetime

    result = []
    if portfolio_df.empty:
        return result

    for _, row in portfolio_df.iterrows():
        w      = row["Weight (%)"]
        gap    = row["Gap to Stop (%)"]
        pnl    = row["P&L (%)"]
        signal = row["Signal"]
        ticker = row["Ticker"]

        # Stop proximity — skip when stop data is unavailable (gap is None),
        # otherwise the comparison crashes. Stop-unavailable is surfaced
        # separately via the Stop Type column.
        if gap is None:
            result.append({
                "level": "warning", "category": "stop",
                "msg": f"🟡 **{ticker}** — stop data unavailable; set a manual stop in your broker",
            })
        elif gap < 3:
            result.append({
                "level": "danger", "category": "stop",
                "msg": f"🔴 **{ticker}** is within {gap:.1f}% of stop ${row['Stop']:.2f} — review immediately",
            })
        elif gap < 7:
            result.append({
                "level": "warning", "category": "stop",
                "msg": f"🟡 **{ticker}** is {gap:.1f}% above stop ${row['Stop']:.2f} — monitor closely",
            })

        # Concentration
        if w > 20:
            result.append({
                "level": "warning", "category": "concentration",
                "msg": f"⚠️ **{ticker}** is {w:.1f}% of portfolio — above 20% concentration threshold",
            })

        # Bearish signal on profitable or losing position
        if "Sell" in signal and pnl > 15:
            result.append({
                "level": "warning", "category": "signal",
                "msg": f"📉 **{ticker}** signal turned bearish with {pnl:.1f}% gain — consider taking partial profits",
            })
        if "Sell" in signal and pnl < -8:
            result.append({
                "level": "danger", "category": "signal",
                "msg": f"⛔ **{ticker}** bearish signal with {pnl:.1f}% loss — stop at ${row['Stop']:.2f}",
            })

    # Sector concentration
    sector_exp = sector_exposure(portfolio_df)
    for _, row in sector_exp.iterrows():
        if row["Pct"] > 40:
            result.append({
                "level": "warning", "category": "concentration",
                "msg": f"🏭 **{row['Sector']}** represents {row['Pct']:.0f}% of portfolio — high sector concentration",
            })

    # ── Data-driven alerts (require held_data) ────────────────────────────────
    if held_data:
        today = _date.today()
        for _, row in portfolio_df.iterrows():
            ticker = row["Ticker"]
            r      = held_data.get(ticker, {})

            # Earnings proximity
            earn = r.get("earnings")
            if earn:
                try:
                    days = (_datetime.strptime(earn, "%Y-%m-%d").date() - today).days
                    if 0 <= days <= 3:
                        result.append({
                            "level": "danger", "category": "earnings",
                            "msg": (
                                f"📅 **{ticker}** earnings in **{days} day{'s' if days != 1 else ''}** ({earn}) "
                                f"— decide your position size before the report"
                            ),
                        })
                    elif 4 <= days <= 7:
                        result.append({
                            "level": "warning", "category": "earnings",
                            "msg": f"📅 **{ticker}** reports earnings in {days} days ({earn}) — review ahead of report",
                        })
                except Exception:
                    pass

            # Analyst revision spike
            rev = r.get("revisions", {})
            dns = rev.get("downgrades_90d", 0)
            ups = rev.get("upgrades_90d", 0)
            net = rev.get("net", 0)
            if dns >= 3 and net <= -2:
                result.append({
                    "level": "danger", "category": "revisions",
                    "msg": (
                        f"📉 **{ticker}** has {dns} analyst downgrades vs {ups} upgrades in 90 days "
                        f"(net {net}) — institutional conviction fading"
                    ),
                })
            elif dns >= 2 and net < 0:
                result.append({
                    "level": "warning", "category": "revisions",
                    "msg": (
                        f"⚠️ **{ticker}** has {dns} downgrades vs {ups} upgrades in 90 days "
                        f"— monitor for further deterioration"
                    ),
                })

    return result


def rebalance_actions(portfolio_df: pd.DataFrame) -> list[dict]:
    """
    Returns structured recommendation dicts instead of plain strings.
    Each dict carries the trigger condition and all data needed for the
    evidence panel in app.py (which also injects score breakdowns from held_data).
    """
    actions = []
    if portfolio_df.empty:
        return actions
    for _, row in portfolio_df.iterrows():
        w      = row["Weight (%)"]
        pnl    = row["P&L (%)"]
        ticker = row["Ticker"]
        price  = row["Price"]
        shares = row["Shares"]
        signal = row["Signal"]
        score  = row["Score"]
        stop   = row["Stop"]
        gap    = row["Gap to Stop (%)"]
        mval   = row["Market Value"]
        avg_cost = row["Avg Cost"]

        if w > 18 and pnl > 20:
            trim_val    = mval * (w - 15) / 100
            trim_shares = max(1, int(trim_val / price))
            actions.append({
                "type":    "trim",
                "urgency": "medium",
                "ticker":  ticker,
                "title":   "Oversized Position with Strong Gain",
                "trigger": f"Weight {w:.0f}% exceeds 18% threshold with +{pnl:.0f}% profit",
                "trim_shares": trim_shares,
                "trim_val":    trim_val,
                "weight":  w,
                "pnl":     pnl,
                "price":   price,
                "shares":  shares,
                "stop":    stop,
                "stop_type": row["Stop Type"],
                "gap":     gap,
                "score":   score,
                "signal":  signal,
                "mval":    mval,
                "avg_cost": avg_cost,
            })

        if "Strong Buy" in signal and w < 5 and score > 70:
            add_val = mval * (8 - w) / 100  # rough cost to reach 8% weight
            actions.append({
                "type":    "add",
                "urgency": "low",
                "ticker":  ticker,
                "title":   "High-Conviction Position Undersized",
                "trigger": f"Strong Buy signal ({score:.0f}/100) but only {w:.1f}% of portfolio",
                "weight":  w,
                "pnl":     pnl,
                "price":   price,
                "shares":  shares,
                "stop":    stop,
                "stop_type": row["Stop Type"],
                "gap":     gap,
                "score":   score,
                "signal":  signal,
                "mval":    mval,
                "avg_cost": avg_cost,
            })

        if "Sell" in signal and pnl > 0:
            # Treat unknown gap as elevated urgency — without a stop in place,
            # a profitable Sell signal needs manual review now, not later.
            _gap_close = (gap is None) or (gap < 5)
            urgency = "high" if (score < 30 or _gap_close) else "medium"
            half_shares = max(1, shares // 2)
            actions.append({
                "type":       "review",
                "urgency":    urgency,
                "ticker":     ticker,
                "title":      "Bearish Signal on Profitable Position",
                "trigger":    f"Composite score {score:.0f}/100 ({signal.split()[-1]}) while position is +{pnl:.1f}% profitable",
                "half_shares": half_shares,
                "weight":     w,
                "pnl":        pnl,
                "price":      price,
                "shares":     shares,
                "stop":       stop,
                "stop_type":  row["Stop Type"],
                "gap":        gap,
                "score":      score,
                "signal":     signal,
                "mval":       mval,
                "avg_cost":   avg_cost,
            })
    return actions


# ── Relative Strength vs Sector ───────────────────────────────────────────────

# Maps each sector to the most widely used sector ETF benchmark
SECTOR_ETF = {
    "Semiconductors":  "SOXX",
    "Consumer Tech":   "XLY",
    "Healthcare":      "XLV",
    "Energy":          "XLE",
    "Defense":         "ITA",
    "Financials":      "XLF",
    "Clean Energy":    "ICLN",
    "Cybersecurity":   "CIBR",
    "AI & Cloud":      "IGV",
    "AI & Data":       "IGV",
    "EV & Auto":       "DRIV",
    "Enterprise Tech": "IGV",
    "Other":           "SPY",
}


def holding_returns(held_data: dict) -> dict[str, float]:
    """6-month total return (%) for each ticker, derived from existing price history."""
    result = {}
    for ticker, data in held_data.items():
        hist = data.get("df") if data.get("df") is not None else data.get("history")
        if hist is None or hist.empty or "Close" not in hist.columns:
            continue
        closes = hist["Close"].dropna()
        if len(closes) < 5:
            continue
        # Guard against a zero opening close — rare but possible with stale
        # yfinance data for delisted / pre-IPO tickers. Returning 0 quietly
        # is preferable to a ZeroDivisionError that takes down the page.
        if float(closes.iloc[0]) <= 0:
            continue
        ret = (closes.iloc[-1] / closes.iloc[0] - 1) * 100
        result[ticker] = round(float(ret), 1)
    return result


def relative_strength_table(
    port_df: pd.DataFrame,
    h_rets: dict[str, float],
    etf_rets: dict[str, float],
) -> pd.DataFrame:
    """
    Build per-holding relative strength vs sector ETF.
    Returns DataFrame with Ticker, Sector, ETF, holding/ETF returns, alpha, and status.
    """
    rows = []
    for _, row in port_df.iterrows():
        ticker = row["Ticker"]
        sector = row["Sector"]
        etf    = SECTOR_ETF.get(sector, "SPY")
        h_ret  = h_rets.get(ticker)
        e_ret  = etf_rets.get(etf)
        if h_ret is None:
            continue
        alpha = round(h_ret - e_ret, 1) if e_ret is not None else None
        if alpha is None:
            status = "—"
        elif alpha >= 5:
            status = "Outperforming ↑"
        elif alpha <= -5:
            status = "Underperforming ↓"
        else:
            status = "In Line ↔"
        rows.append({
            "Ticker":        ticker,
            "Sector":        sector,
            "ETF":           etf,
            "6mo Return (%)": h_ret,
            "ETF Return (%)": e_ret,
            "Alpha (%)":     alpha,
            "Status":        status,
        })
    return pd.DataFrame(rows)


# ── Correlation & Diversification ─────────────────────────────────────────────

def correlation_matrix(held_data: dict) -> pd.DataFrame:
    """Build a daily-return correlation matrix from held_data price histories."""
    series = {}
    for ticker, data in held_data.items():
        hist = data.get("df") if data.get("df") is not None else data.get("history")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            series[ticker] = hist["Close"]
    if len(series) < 2:
        return pd.DataFrame()
    prices = pd.DataFrame(series).dropna()
    returns = prices.pct_change().dropna()
    return returns.corr().round(3)


def diversification_score(corr_df: pd.DataFrame, weights: dict | None = None) -> dict:
    """
    Score 0–100: 100 = fully uncorrelated, 0 = all positions move in lockstep.
    weights: {ticker: weight_pct} — equal-weight assumed when None.
    Returns score, avg_correlation, and a list of risk pair dicts.
    """
    empty = {"score": None, "avg_correlation": None, "risk_pairs": []}
    if corr_df.empty or len(corr_df) < 2:
        return empty

    tickers = corr_df.index.tolist()
    w = {t: float((weights or {}).get(t, 1.0)) for t in tickers}
    total_w = sum(w.values()) or 1.0

    weighted_sum = 0.0
    weight_sum = 0.0
    risk_pairs = []

    for i, t1 in enumerate(tickers):
        for j, t2 in enumerate(tickers):
            if i >= j:
                continue
            c = float(corr_df.loc[t1, t2])
            if np.isnan(c):
                continue
            pair_w = (w[t1] / total_w) * (w[t2] / total_w)
            weighted_sum += c * pair_w
            weight_sum += pair_w
            if c >= 0.80:
                risk_pairs.append({"t1": t1, "t2": t2, "corr": round(c, 2), "level": "danger"})
            elif c >= 0.65:
                risk_pairs.append({"t1": t1, "t2": t2, "corr": round(c, 2), "level": "warning"})

    avg_corr = weighted_sum / weight_sum if weight_sum else 0.0
    score = round((1 - avg_corr) / 2 * 100, 1)

    return {
        "score": score,
        "avg_correlation": round(avg_corr, 3),
        "risk_pairs": sorted(risk_pairs, key=lambda x: -x["corr"]),
    }


# ── Diversification Advisor ────────────────────────────────────────────────────

# Candidate tickers per sector for ADD recommendations
_SECTOR_CANDIDATES = {
    "Healthcare":    ["LLY", "NVO", "ABBV", "ISRG", "REGN"],
    "Energy":        ["XOM", "CVX", "COP", "OXY"],
    "Defense":       ["LMT", "RTX", "NOC", "GD"],
    "Financials":    ["JPM", "V", "MA", "GS"],
    "Clean Energy":  ["NEE", "ENPH", "FSLR", "BEP"],
    "Consumer Tech": ["AAPL", "AMZN", "NFLX", "SHOP"],
    "AI & Cloud":    ["MSFT", "GOOGL", "META", "CRM"],
    "AI & Data":     ["PLTR", "SNOW", "MDB", "IONQ"],
    "Cybersecurity": ["CRWD", "PANW", "NET", "ZS", "FTNT"],
    "Semiconductors":["NVDA", "AVGO", "AMD", "MU", "QCOM"],
}

# How correlated each sector is to a typical tech-heavy portfolio (lower = better diversifier)
_SECTOR_PROFILES = {
    "Healthcare":    {"corr": 0.15, "why": "counter-cyclical, FDA/drug-cycle driven — moves independently of tech"},
    "Energy":        {"corr": 0.10, "why": "oil-price and geopolitics driven — near-zero correlation to semiconductors"},
    "Defense":       {"corr": 0.12, "why": "government budget driven — orthogonal to rate-sensitive tech growth stocks"},
    "Financials":    {"corr": 0.35, "why": "benefits when rates rise — inverse to your growth-tech book"},
    "Clean Energy":  {"corr": 0.28, "why": "policy/subsidy driven — moderate diversification from pure tech"},
    "Consumer Tech": {"corr": 0.58, "why": "still tech but consumer-facing — partial diversification"},
    "AI & Cloud":    {"corr": 0.72, "why": "highly correlated to existing tech — limited diversification benefit"},
    "AI & Data":     {"corr": 0.68, "why": "correlated to AI/semiconductor cycle — limited benefit if already tech-heavy"},
}

# Sectors that genuinely diversify a tech-heavy portfolio, in priority order
_DIVERSIFYING_SECTORS = ["Healthcare", "Energy", "Defense", "Financials", "Clean Energy"]


def diversification_recommendations(
    port_df: pd.DataFrame,
    corr_df: pd.DataFrame,
    div_result: dict,
    portfolio_value: float = 50_000.0,
) -> list[dict]:
    """
    Returns structured REDUCE, PAIR_RISK, and ADD recommendation dicts.
    Each dict carries all data needed to render an advisor card in app.py.
    """
    recs = []
    if port_df.empty:
        return recs

    held_tickers = set(port_df["Ticker"].tolist())
    sec_exp = sector_exposure(port_df)
    sector_pcts = dict(zip(sec_exp["Sector"], sec_exp["Pct"]))

    # ── REDUCE: overweight sectors ────────────────────────────────────────────
    for sector, pct in sector_pcts.items():
        if pct > 20:
            target_pct = 15.0
            reduce_pct = round(pct - target_pct, 1)
            sector_rows = port_df[port_df["Sector"] == sector].sort_values("Score")
            weakest = [
                {
                    "ticker":  row["Ticker"],
                    "score":   round(row["Score"], 0),
                    "signal":  row["Signal"],
                    "pnl_pct": row["P&L (%)"],
                    "weight":  row["Weight (%)"],
                }
                for _, row in sector_rows.head(2).iterrows()
            ]
            recs.append({
                "type":            "REDUCE",
                "urgency":         "high" if pct > 30 else "medium",
                "sector":          sector,
                "current_pct":     round(pct, 1),
                "target_pct":      target_pct,
                "reduce_pct":      reduce_pct,
                "reduce_dollars":  round(portfolio_value * reduce_pct / 100),
                "weakest_tickers": weakest,
                "reason": (
                    f"**{sector}** is {pct:.0f}% of your portfolio — above the 20% sector cap. "
                    f"Intra-sector correlation means these names move together on the same macro catalyst."
                ),
            })

    # ── PAIR_RISK: highly correlated pairs ────────────────────────────────────
    for rp in div_result.get("risk_pairs", []):
        if rp["level"] != "danger":
            continue
        t1, t2 = rp["t1"], rp["t2"]
        r1 = port_df[port_df["Ticker"] == t1]
        r2 = port_df[port_df["Ticker"] == t2]
        if r1.empty or r2.empty:
            continue
        s1, s2 = float(r1["Score"].iloc[0]), float(r2["Score"].iloc[0])
        weaker   = t1 if s1 <= s2 else t2
        stronger = t2 if s1 <= s2 else t1
        wr = port_df[port_df["Ticker"] == weaker].iloc[0]
        recs.append({
            "type":          "PAIR_RISK",
            "urgency":       "high",
            "t1": t1, "t2": t2,
            "corr":          rp["corr"],
            "weaker":        weaker,
            "stronger":      stronger,
            "weaker_score":  round(min(s1, s2), 0),
            "weaker_weight": round(wr["Weight (%)"], 1),
            "weaker_pnl":    round(wr["P&L (%)"], 1),
            "reason": (
                f"**{t1}** and **{t2}** have {rp['corr']:.2f} correlation — "
                f"they move almost in lockstep. Holding both gives the risk of two positions "
                f"but the diversification of one."
            ),
        })

    # ── ADD: underweight diversifying sectors ────────────────────────────────
    for sector in _DIVERSIFYING_SECTORS:
        current_pct = sector_pcts.get(sector, 0.0)
        if current_pct >= 8.0:
            continue
        candidates = [t for t in _SECTOR_CANDIDATES.get(sector, []) if t not in held_tickers][:3]
        if not candidates:
            continue
        profile    = _SECTOR_PROFILES.get(sector, {"corr": 0.30, "why": ""})
        target_pct = 10.0
        gap_pct    = round(target_pct - current_pct, 1)
        recs.append({
            "type":         "ADD",
            "urgency":      "medium" if current_pct > 0 else "low",
            "sector":       sector,
            "current_pct":  round(current_pct, 1),
            "target_pct":   target_pct,
            "gap_pct":      gap_pct,
            "add_dollars":  round(portfolio_value * gap_pct / 100),
            "corr_to_tech": profile["corr"],
            "why":          profile["why"],
            "candidates":   candidates,
            "reason": (
                f"**{sector}** exposure is only {current_pct:.0f}% — "
                f"this sector is {profile['why']}."
            ),
        })

    return recs
