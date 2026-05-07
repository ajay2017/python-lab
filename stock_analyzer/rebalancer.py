"""
Portfolio Rebalancing Advisor.

Computes drift between current and target weights, ranks positions by
rebalancing urgency, and generates specific trim/add actions with dollar
and share amounts, priority ordering, and Institutional-style context on
when and how to rebalance.
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


# Tolerance bands
TOLERANCE_OK      = 2.0   # ±2% — no action needed
TOLERANCE_WATCH   = 5.0   # 2–5% — monitor
# > 5% = action needed


def equal_weights(port_df: pd.DataFrame) -> dict:
    """Return equal target weight for each ticker."""
    n = len(port_df)
    if n == 0:
        return {}
    w = round(100.0 / n, 2)
    return {row["Ticker"]: w for _, row in port_df.iterrows()}


def compute_drift(
    port_df: pd.DataFrame,
    target_weights: dict,
    total_val: float,
) -> pd.DataFrame:
    """
    Returns DataFrame with per-position drift analysis.
    Columns: Ticker, Sector, Current (%), Target (%), Drift (pp),
             Current Value ($), Target Value ($), Drift Value ($),
             Price ($), Shares, Score, Signal, Status
    """
    rows = []
    for _, row in port_df.iterrows():
        ticker  = row["Ticker"]
        current = _f(row.get("Weight (%)"))
        target  = _f(target_weights.get(ticker, current))
        drift   = round(current - target, 2)  # positive = overweight
        mval    = _f(row.get("Market Value"))
        price   = _f(row.get("Price"))
        shares  = _f(row.get("Shares"))
        score   = _f(row.get("Score"))
        signal  = str(row.get("Signal", ""))
        sector  = str(row.get("Sector", "Other"))

        target_val = target / 100 * total_val
        drift_val  = round(mval - target_val, 0)  # positive = trim, negative = add

        if abs(drift) <= TOLERANCE_OK:
            status = "OK"
        elif abs(drift) <= TOLERANCE_WATCH:
            status = "WATCH"
        else:
            status = "TRIM" if drift > 0 else "ADD"

        rows.append({
            "Ticker":           ticker,
            "Sector":           sector,
            "Current (%)":      round(current, 1),
            "Target (%)":       round(target, 1),
            "Drift (pp)":       drift,
            "Current Value ($)": round(mval, 0),
            "Target Value ($)": round(target_val, 0),
            "Drift Value ($)":  round(drift_val, 0),
            "Price ($)":        round(price, 2),
            "Shares":           int(shares),
            "Score":            score,
            "Signal":           signal,
            "Status":           status,
        })

    return pd.DataFrame(rows).sort_values("Drift (pp)", ascending=False).reset_index(drop=True)


def build_rebalance_plan(
    drift_df: pd.DataFrame,
    total_val: float,
) -> dict:
    """
    Generates trim and add action lists with specific share counts,
    urgency ordering, and Institutional-style rationale.

    Returns dict with:
      trims: list of action dicts (overweight positions)
      adds:  list of action dicts (underweight positions)
      ok:    list of ticker strings in tolerance
      total_trim_value: sum of $ to trim
      total_add_value:  sum of $ to add
      rebalance_pct:    % of portfolio being touched
    """
    if drift_df.empty:
        return {"trims": [], "adds": [], "ok": [], "total_trim_value": 0,
                "total_add_value": 0, "rebalance_pct": 0}

    trims, adds, ok_list = [], [], []

    for _, row in drift_df.iterrows():
        ticker    = row["Ticker"]
        status    = row["Status"]
        drift_pp  = row["Drift (pp)"]
        drift_val = row["Drift Value ($)"]
        price     = row["Price ($)"]
        shares    = row["Shares"]
        score     = _f(row["Score"])
        signal    = str(row["Signal"])
        current   = row["Current (%)"]
        target    = row["Target (%)"]
        sector    = row["Sector"]

        if status == "OK":
            ok_list.append(ticker)
            continue

        shares_delta = abs(drift_val) / price if price > 0 else 0
        shares_delta = max(1, int(shares_delta))

        # Trim urgency scoring
        if drift_pp > 0:
            urgency = 0
            if "Sell" in signal or "Strong Sell" in signal:
                urgency += 40   # broken thesis + overweight = highest priority
            if score < 50:
                urgency += 20
            if abs(drift_pp) > TOLERANCE_WATCH:
                urgency += 30
            elif abs(drift_pp) > TOLERANCE_OK:
                urgency += 10
            if status == "WATCH":
                urgency = max(urgency, 5)

            # Rationale
            if "Sell" in signal:
                rationale = (
                    f"**{ticker}** is {drift_pp:+.1f}pp overweight AND has a Sell signal "
                    f"(score {score:.0f}/100). Carrying oversized exposure in a position "
                    f"your own signals are flagging bearish is a double risk."
                )
                action_detail = (
                    f"Sell **{shares_delta:,} shares** (≈${abs(drift_val):,.0f}) to bring "
                    f"weight from {current:.1f}% → {target:.1f}%. "
                    "Priority: do this before considering any other trim."
                )
            elif score < 50:
                rationale = (
                    f"**{ticker}** is {drift_pp:+.1f}pp overweight with a weak composite score "
                    f"({score:.0f}/100). You've drifted into a large position in a name where "
                    f"conviction is fading."
                )
                action_detail = (
                    f"Sell **{shares_delta:,} shares** (≈${abs(drift_val):,.0f}) to reduce "
                    f"from {current:.1f}% → {target:.1f}%. "
                    "Recycle proceeds into higher-conviction names."
                )
            else:
                rationale = (
                    f"**{ticker}** has drifted {drift_pp:+.1f}pp above target weight — "
                    f"a winner running. Score {score:.0f}/100 supports the thesis, "
                    f"but concentration risk increases as the position grows."
                )
                action_detail = (
                    f"Sell **{shares_delta:,} shares** (≈${abs(drift_val):,.0f}) to trim "
                    f"from {current:.1f}% → {target:.1f}%. "
                    "Keep the core position — just cap the binary event risk from concentration."
                )

            inst_lens = (
                "Institutional position sizing rule: no single name above 15% of portfolio,"
                "regardless of conviction — and even high-conviction names should be "
                "trimmed back to target when they outrun it by 5pp or more. "
                "The reason is not that you're wrong; it's that a concentrated winner "
                "creates asymmetric downside if conditions change. "
                "Trim the drift, not the thesis."
            )

            trims.append({
                "ticker":        ticker,
                "sector":        sector,
                "signal":        signal,
                "score":         score,
                "current_pct":   current,
                "target_pct":    target,
                "drift_pp":      drift_pp,
                "drift_val":     abs(drift_val),
                "shares_delta":  shares_delta,
                "price":         price,
                "urgency":       urgency,
                "status":        status,
                "rationale":     rationale,
                "action_detail": action_detail,
                "institutional_lens":       inst_lens,
            })

        else:  # underweight → ADD
            # Add urgency: high-conviction underweight names first
            urgency = 0
            if score >= 65:
                urgency += 30
            if "Buy" in signal or "Strong Buy" in signal:
                urgency += 20
            if abs(drift_pp) > TOLERANCE_WATCH:
                urgency += 30
            elif abs(drift_pp) > TOLERANCE_OK:
                urgency += 10

            if score >= 65 and ("Buy" in signal):
                rationale = (
                    f"**{ticker}** is {abs(drift_pp):.1f}pp underweight vs target — "
                    f"score {score:.0f}/100 with a {signal} signal. "
                    "You're underexposed to a high-conviction name."
                )
                action_detail = (
                    f"Buy **{shares_delta:,} shares** (≈${abs(drift_val):,.0f}) to build "
                    f"from {current:.1f}% → {target:.1f}%. "
                    "Deploy in 1–2 tranches to average in, not all at once."
                )
            else:
                rationale = (
                    f"**{ticker}** is {abs(drift_pp):.1f}pp underweight. "
                    f"Score {score:.0f}/100 · {signal}. "
                    "Position has lagged target — reassess conviction before adding."
                )
                action_detail = (
                    f"Consider buying **{shares_delta:,} shares** (≈${abs(drift_val):,.0f}) "
                    f"to move from {current:.1f}% → {target:.1f}%. "
                    "Only add if the fundamental thesis remains intact."
                )

            inst_lens = (
                "Underweight rebalancing in high-conviction names is one of the highest-return"
                "uses of portfolio cash. Research shows that systematic rebalancing "
                "— buying underweight winners and trimming overweight laggards — adds "
                "approximately 0.5–1.0% annually versus a drift portfolio, purely from "
                "the discipline of maintaining target allocations. "
                "Use proceeds from trims to fund adds — keeping total exposure stable."
            )

            adds.append({
                "ticker":        ticker,
                "sector":        sector,
                "signal":        signal,
                "score":         score,
                "current_pct":   current,
                "target_pct":    target,
                "drift_pp":      drift_pp,
                "drift_val":     abs(drift_val),
                "shares_delta":  shares_delta,
                "price":         price,
                "urgency":       urgency,
                "status":        status,
                "rationale":     rationale,
                "action_detail": action_detail,
                "institutional_lens":       inst_lens,
            })

    trims.sort(key=lambda x: x["urgency"], reverse=True)
    adds.sort(key=lambda x: x["urgency"], reverse=True)

    total_trim = sum(t["drift_val"] for t in trims)
    total_add  = sum(a["drift_val"] for a in adds)
    touched_val = total_trim + total_add
    rebalance_pct = round(touched_val / total_val * 100, 1) if total_val > 0 else 0.0

    return {
        "trims":              trims,
        "adds":               adds,
        "ok":                 ok_list,
        "total_trim_value":   round(total_trim, 0),
        "total_add_value":    round(total_add, 0),
        "rebalance_pct":      rebalance_pct,
    }
