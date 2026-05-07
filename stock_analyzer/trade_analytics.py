"""
Trade Journal Behavioral Analytics.

Turns raw trade history into PM-level self-awareness:
- Trigger performance breakdown (MANUAL vs RECOMMENDATION vs STOP_HIT vs REBALANCE)
- Profit factor, win %, P&L % per trade
- Monthly P&L trend (is your edge improving or decaying?)
- Hold time estimation (matched BUY→SELL pairs)
- Loss discipline score (are you cutting losses fast or holding losers?)
- Behavioral insights with Institutional-style coaching cards
"""

import pandas as pd
import numpy as np
from datetime import datetime as _dt


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def _parse_dt(val):
    if pd.isna(val):
        return None
    try:
        return pd.to_datetime(val)
    except Exception:
        return None


def _pnl_pct(realized_pnl, cost_basis, shares):
    """Return % gain/loss on the invested capital."""
    invested = _safe_float(cost_basis) * _safe_float(shares)
    if invested <= 0:
        return None
    pnl = _safe_float(realized_pnl)
    if pnl == 0:
        return None
    return round(pnl / invested * 100, 2)


def compute_extended_stats(trades_df: pd.DataFrame) -> dict:
    """
    Returns extended per-trade stats DataFrame with extra columns:
    pnl_pct, month_str, hold_days (estimated from matched BUY records).
    Only includes SELL rows with realized_pnl.
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame()

    sells = trades_df[trades_df["action"] == "SELL"].copy()
    buys  = trades_df[trades_df["action"] == "BUY"].copy()

    if sells.empty:
        return pd.DataFrame()

    sells = sells.dropna(subset=["realized_pnl"]).copy()
    sells["realized_pnl"] = pd.to_numeric(sells["realized_pnl"], errors="coerce")
    sells["cost_basis"]   = pd.to_numeric(sells["cost_basis"],   errors="coerce")
    sells["shares"]       = pd.to_numeric(sells["shares"],       errors="coerce")
    sells["price"]        = pd.to_numeric(sells["price"],        errors="coerce")
    sells = sells.dropna(subset=["realized_pnl"])

    # P&L %
    sells["pnl_pct"] = sells.apply(
        lambda r: _pnl_pct(r["realized_pnl"], r["cost_basis"], r["shares"]), axis=1
    )

    # Month label for trending
    sells["_dt"] = sells["traded_at"].apply(_parse_dt)
    sells["month_str"] = sells["_dt"].apply(
        lambda d: d.strftime("%Y-%m") if d is not None else "Unknown"
    )

    # Approximate hold time: find the nearest preceding BUY for the same ticker
    if not buys.empty:
        buys = buys.copy()
        buys["_dt"] = buys["traded_at"].apply(_parse_dt)
        buys = buys.dropna(subset=["_dt"]).sort_values("_dt")

        def _hold_days(sell_row):
            t = sell_row["ticker"]
            sd = sell_row["_dt"]
            if sd is None:
                return None
            matching = buys[(buys["ticker"] == t) & (buys["_dt"] <= sd)]
            if matching.empty:
                return None
            nearest_buy = matching.iloc[-1]["_dt"]
            delta = (sd - nearest_buy).days
            return delta if delta >= 0 else None

        sells["hold_days"] = sells.apply(_hold_days, axis=1)
    else:
        sells["hold_days"] = None

    return sells.reset_index(drop=True)


def build_trigger_breakdown(ext_df: pd.DataFrame) -> pd.DataFrame:
    """
    Per trigger_type: count, win_rate, avg_win_pct, avg_loss_pct,
    profit_factor, expectancy_dollars.
    Returns sorted DataFrame.
    """
    if ext_df.empty or "trigger_type" not in ext_df.columns:
        return pd.DataFrame()

    rows = []
    for ttype, grp in ext_df.groupby("trigger_type"):
        winners = grp[grp["realized_pnl"] > 0]
        losers  = grp[grp["realized_pnl"] < 0]
        n       = len(grp)
        wr      = len(winners) / n * 100 if n else 0.0
        avg_w   = float(winners["realized_pnl"].mean()) if not winners.empty else 0.0
        avg_l   = float(losers["realized_pnl"].mean())  if not losers.empty else 0.0
        pf_denom = abs(float(losers["realized_pnl"].sum())) if not losers.empty else 0.0
        pf      = (float(winners["realized_pnl"].sum()) / pf_denom) if pf_denom > 0 else None
        exp_d   = wr / 100 * avg_w + (1 - wr / 100) * avg_l if n else 0.0
        avg_w_pct = float(winners["pnl_pct"].mean()) if not winners.empty and winners["pnl_pct"].notna().any() else None
        avg_l_pct = float(losers["pnl_pct"].mean())  if not losers.empty  and losers["pnl_pct"].notna().any()  else None

        rows.append({
            "Trigger":        ttype,
            "Trades":         n,
            "Win Rate (%)":   round(wr, 1),
            "Avg Win ($)":    round(avg_w, 0),
            "Avg Loss ($)":   round(avg_l, 0),
            "Avg Win (%)":    round(avg_w_pct, 1) if avg_w_pct is not None else None,
            "Avg Loss (%)":   round(avg_l_pct, 1) if avg_l_pct is not None else None,
            "Profit Factor":  round(pf, 2) if pf is not None else None,
            "Expectancy ($)": round(exp_d, 0),
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values("Expectancy ($)", ascending=False).reset_index(drop=True)


def build_monthly_trend(ext_df: pd.DataFrame) -> pd.DataFrame:
    """
    Monthly realized P&L and win rate.
    Returns DataFrame with month_str, pnl, win_rate, trade_count.
    """
    if ext_df.empty:
        return pd.DataFrame()

    monthly = (
        ext_df.groupby("month_str")
        .agg(
            pnl=("realized_pnl", "sum"),
            trade_count=("realized_pnl", "count"),
            wins=("realized_pnl", lambda x: (x > 0).sum()),
        )
        .reset_index()
    )
    monthly["win_rate"] = (monthly["wins"] / monthly["trade_count"] * 100).round(1)
    monthly["pnl"] = monthly["pnl"].round(2)
    monthly = monthly.sort_values("month_str")
    return monthly


def build_hold_time_stats(ext_df: pd.DataFrame) -> dict:
    """
    Hold time statistics from matched BUY→SELL pairs.
    Returns dict with avg, median, winners_avg, losers_avg.
    """
    hd = ext_df.dropna(subset=["hold_days"]) if not ext_df.empty else pd.DataFrame()
    if hd.empty:
        return {}

    hd_win = hd[hd["realized_pnl"] > 0]["hold_days"]
    hd_los = hd[hd["realized_pnl"] < 0]["hold_days"]

    return {
        "avg_hold_days":     round(float(hd["hold_days"].mean()), 1),
        "median_hold_days":  round(float(hd["hold_days"].median()), 1),
        "winners_avg_days":  round(float(hd_win.mean()), 1) if not hd_win.empty else None,
        "losers_avg_days":   round(float(hd_los.mean()), 1) if not hd_los.empty else None,
        "sample_size":       len(hd),
    }


def build_behavioral_insights(
    ext_df: pd.DataFrame,
    trigger_df: pd.DataFrame,
    monthly_df: pd.DataFrame,
    hold_stats: dict,
    win_rate: float,
    profit_factor: float | None,
    avg_win_pct: float | None,
    avg_loss_pct: float | None,
) -> list[dict]:
    """
    Returns a list of behavioral insight cards, each with:
    priority (HIGH/MEDIUM/OK), title, observation, implication, action, institutional_lens
    """
    insights = []

    # ── LOSS DISCIPLINE ────────────────────────────────────────────────────────
    if avg_win_pct is not None and avg_loss_pct is not None:
        win_loss_ratio = abs(avg_win_pct / avg_loss_pct) if avg_loss_pct != 0 else None
        if win_loss_ratio is not None and win_loss_ratio < 1.5 and win_rate < 55:
            insights.append({
                "priority": "HIGH",
                "title":    "Loss/Win Ratio Too Tight — Strategy Needs Edge",
                "observation": (
                    f"Average win: **{avg_win_pct:+.1f}%** · Average loss: **{avg_loss_pct:.1f}%**  \n"
                    f"Win/loss size ratio: **{win_loss_ratio:.2f}:1** with **{win_rate:.0f}% win rate**.  \n"
                    "At these levels, the strategy has negative expected value over time."
                ),
                "implication": (
                    "A strategy needs EITHER a win rate above 60% OR wins that are meaningfully larger "
                    "than losses (ratio ≥ 2:1). Currently you have neither. "
                    "This is the combination that erodes capital slowly but persistently."
                ),
                "action": (
                    "Focus on two improvements simultaneously: (1) cut losses faster — "
                    "if a trade is down more than your avg loss, it's already past the historical stop point; "
                    "(2) hold winners longer — if a trade is up, raise the stop but don't exit just because "
                    "it's profitable. Let the trend work."
                ),
                "institutional_lens": (
                    "The Kelly Criterion shows that positive expectancy requires either high win rate "
                    "OR high win/loss ratio. Institutional PMs target a minimum 2:1 win/loss ratio — "
                    "meaning they systematically let winners run and cut losers at a fixed risk point. "
                    "The math is unforgiving: a 50% win rate with 1:1 ratio produces zero edge "
                    "before transaction costs. The discipline is asymmetry — not being right more often, "
                    "but being right bigger."
                ),
            })
        elif win_loss_ratio is not None and win_loss_ratio >= 2.5 and win_rate >= 55:
            insights.append({
                "priority": "OK",
                "title":    "Strong Win/Loss Asymmetry — Strategy Has Positive Edge",
                "observation": (
                    f"Average win: **{avg_win_pct:+.1f}%** · Average loss: **{avg_loss_pct:.1f}%**  \n"
                    f"Win/loss size ratio: **{win_loss_ratio:.2f}:1** at **{win_rate:.0f}% win rate**.  \n"
                    "This is a genuinely profitable asymmetry."
                ),
                "implication": (
                    "Your wins are meaningfully larger than your losses, and your win rate is above 50%. "
                    "This is the combination that compounds capital. The key discipline: "
                    "don't change the approach when you hit a losing streak — "
                    "the edge is statistical, not guaranteed on any single trade."
                ),
                "action": (
                    "Maintain the stop discipline that's producing the small losses. "
                    "The temptation when the strategy is working is to take profits earlier — resist it. "
                    "The large wins are what makes the math work."
                ),
                "institutional_lens": (
                    "A win/loss ratio above 2.5 with a win rate above 55% is exceptional. "
                    "Quantitative research desks show that most institutional strategies cluster around 50% win rate "
                    "with a 2:1 ratio — achieving both simultaneously is what separates top quartile PMs. "
                    "Document what you're doing right now. Don't optimise it to death."
                ),
            })

    # ── HOLD TIME PATTERN ─────────────────────────────────────────────────────
    if hold_stats.get("winners_avg_days") and hold_stats.get("losers_avg_days"):
        w_days = hold_stats["winners_avg_days"]
        l_days = hold_stats["losers_avg_days"]
        if l_days > w_days * 1.3:
            insights.append({
                "priority": "MEDIUM",
                "title":    "Holding Losers Longer Than Winners — Classic Disposition Effect",
                "observation": (
                    f"Winners held avg **{w_days:.0f} days** · Losers held avg **{l_days:.0f} days**  \n"
                    f"You're holding losers **{l_days/w_days:.1f}× longer** than winners."
                ),
                "implication": (
                    "This is the 'disposition effect' — the tendency to sell winners too quickly "
                    "(to lock in the good feeling) and hold losers too long (to avoid realising the loss). "
                    "It's the most documented behavioral bias in investing. "
                    "The result: you cut your winners short and let your losers compound."
                ),
                "action": (
                    "Set a rule: if a position is down more than 8–10% and not recovering within "
                    "your planned hold window, exit. Do not wait for break-even. "
                    "Simultaneously, when a winner hits +15%, raise the trailing stop to lock in "
                    "50% of the gain — but don't sell. Let the stop do the work."
                ),
                "institutional_lens": (
                    "Kahneman and Tversky identified this in 1979 — Behavioral finance research "
                    "has spent decades trying to engineer it out of their PMs. "
                    "The solution is systematic, not motivational: pre-commit to a maximum hold time for losers "
                    "BEFORE you enter the trade. Write it in the trade notes. "
                    "When the timer expires, exit — no negotiation with yourself. "
                    "The discipline is in the pre-commitment, not the willpower."
                ),
            })
        elif w_days > l_days * 1.3:
            insights.append({
                "priority": "OK",
                "title":    "Winners Held Longer Than Losers — Healthy Discipline",
                "observation": (
                    f"Winners held avg **{w_days:.0f} days** · Losers held avg **{l_days:.0f} days**  \n"
                    f"You're cutting losers faster than you exit winners — the correct asymmetry."
                ),
                "implication": (
                    "You're exhibiting the opposite of the disposition effect. "
                    "Cutting losses quickly and letting winners run is the structural behavior of "
                    "consistently profitable investors."
                ),
                "action": (
                    "Maintain this discipline. When the market gets volatile and a winner pulls back, "
                    "resist the urge to exit early. Use trailing stops instead of emotional exits."
                ),
                "institutional_lens": (
                    "Institutional PM coaching focuses almost entirely on eliminating the disposition effect. "
                    "The fact that you're cutting losses faster than you exit winners means "
                    "you've already solved the hardest behavioral problem in investing. "
                    "Protect this edge — it's rarer than most investors think."
                ),
            })

    # ── TRIGGER TYPE INSIGHT ──────────────────────────────────────────────────
    if not trigger_df.empty and len(trigger_df) >= 2:
        best_row  = trigger_df.iloc[0]
        worst_row = trigger_df.iloc[-1]
        if best_row["Expectancy ($)"] > 0 and worst_row["Expectancy ($)"] < 0:
            insights.append({
                "priority": "MEDIUM",
                "title":    f"'{best_row['Trigger']}' Trades Outperform '{worst_row['Trigger']}' Significantly",
                "observation": (
                    f"**{best_row['Trigger']}** trades: "
                    f"{best_row['Win Rate (%)']:.0f}% win rate · "
                    f"${best_row['Expectancy ($)']:+,.0f} expectancy per trade  \n"
                    f"**{worst_row['Trigger']}** trades: "
                    f"{worst_row['Win Rate (%)']:.0f}% win rate · "
                    f"${worst_row['Expectancy ($)']:+,.0f} expectancy per trade"
                ),
                "implication": (
                    f"Not all reasons for entering a trade are equal. "
                    f"'{best_row['Trigger']}' trades are generating positive expectancy; "
                    f"'{worst_row['Trigger']}' trades are negative. "
                    "This is critical signal about which part of your process is working."
                ),
                "action": (
                    f"Increase allocation to '{best_row['Trigger']}' triggers — "
                    "take more of the trades your process is validating. "
                    f"Investigate '{worst_row['Trigger']}' trades specifically: "
                    "are they overtrading, poor timing, or a broken signal? "
                    "Consider pausing that trigger type until you understand the pattern."
                ),
                "institutional_lens": (
                    "Institutional PM performance review process explicitly breaks down returns by decision type. "
                    "A systematic strategy can produce positive overall returns while hiding a broken "
                    "sub-strategy that's slowly eroding performance. "
                    "The discipline is to measure each trigger independently — then lean harder into "
                    "what's working and ruthlessly cut what isn't. "
                    "This is process alpha, not just trade alpha."
                ),
            })
        elif not trigger_df.empty and all(trigger_df["Expectancy ($)"] > 0):
            insights.append({
                "priority": "OK",
                "title":    "All Trigger Types Showing Positive Expectancy",
                "observation": (
                    "Every trade trigger type has produced positive expected value. "
                    + "  \n".join(
                        f"**{r['Trigger']}**: {r['Win Rate (%)']:.0f}% win rate · "
                        f"${r['Expectancy ($)']:+,.0f} expectancy"
                        for _, r in trigger_df.iterrows()
                    )
                ),
                "implication": (
                    "Your process is working across all the reasons you trade. "
                    "This is evidence of a robust, not narrowly optimised, strategy."
                ),
                "action": (
                    "Scale up confidence in the process. "
                    "When you have a valid setup, trust the signal rather than second-guessing."
                ),
                "institutional_lens": (
                    "A strategy that produces positive expectancy across multiple entry triggers "
                    "is rare and valuable. Quantitative research teams call this 'robustness' — "
                    "the returns don't depend on a single parameter or condition. "
                    "Protect it by not over-fitting: resist the urge to optimise triggers further. "
                    "The diversification of edge sources is part of what makes it work."
                ),
            })

    # ── MONTHLY MOMENTUM ──────────────────────────────────────────────────────
    if not monthly_df.empty and len(monthly_df) >= 3:
        recent_3 = monthly_df.tail(3)["pnl"].tolist()
        prior_3  = monthly_df.head(max(1, len(monthly_df) - 3))["pnl"].tolist() if len(monthly_df) > 3 else []

        recent_avg = sum(recent_3) / len(recent_3)
        prior_avg  = sum(prior_3) / len(prior_3) if prior_3 else None

        if prior_avg is not None and recent_avg < prior_avg * 0.5 and recent_avg < 0:
            insights.append({
                "priority": "MEDIUM",
                "title":    "Significant Recent Performance Deterioration",
                "observation": (
                    f"Last 3 months avg P&L: **${recent_avg:+,.0f}/month**  \n"
                    f"Prior period avg P&L: **${prior_avg:+,.0f}/month**  \n"
                    "Recent performance is significantly below historical average."
                ),
                "implication": (
                    "Performance deterioration over multiple months is usually a signal — "
                    "either market conditions have changed and your strategy isn't adapting, "
                    "or behavioral drift has crept in (overtrading, ignoring stops, chasing)."
                ),
                "action": (
                    "Do a trade-by-trade review of the last 3 months. "
                    "Identify: are losses larger than historical? Is win rate falling? "
                    "Are you taking more trades? One of those three will be the cause. "
                    "Consider reducing position sizes by 30–40% until you identify and fix the pattern."
                ),
                "institutional_lens": (
                    "Institutional PM review process uses a 'drawdown trigger' — if rolling 3-month "
                    "performance is below the prior-period average by more than 40%, the PM enters "
                    "a mandatory risk review. This is not a punishment; it's a systematic response "
                    "to evidence that something has changed. The worst thing to do in a drawdown is "
                    "to increase size to 'make it back.' The discipline is to reduce size, identify "
                    "the cause, and rebuild confidence gradually."
                ),
            })
        elif prior_avg is not None and recent_avg > prior_avg * 1.5 and recent_avg > 0:
            insights.append({
                "priority": "OK",
                "title":    "Strong Recent Performance Momentum",
                "observation": (
                    f"Last 3 months avg P&L: **${recent_avg:+,.0f}/month**  \n"
                    f"Prior period avg P&L: **${prior_avg:+,.0f}/month**  \n"
                    "Recent performance is meaningfully above historical average."
                ),
                "implication": (
                    "Your strategy is working well in current market conditions. "
                    "This could be genuine skill improvement, favorable conditions, or both. "
                    "The risk is overconfidence — a strong run can lead to larger positions "
                    "and more risk-taking just before conditions revert."
                ),
                "action": (
                    "Maintain current position sizes and process discipline — do not scale up "
                    "aggressively just because recent results are strong. "
                    "Set a mental benchmark: 'If performance reverts to the prior average, "
                    "that's normal — not a failure.'"
                ),
                "institutional_lens": (
                    "Institutional PM behavioral research shows that overconfidence peaks exactly "
                    "at the top of a performance run — PMs increase size and risk just as "
                    "conditions start to revert. The antidote is systematic, not motivational: "
                    "pre-commit to keeping position sizes constant for at least 2 months after "
                    "a strong run before considering scaling up. Let the process prove it's repeatable."
                ),
            })

    # ── PROFIT FACTOR ─────────────────────────────────────────────────────────
    if profit_factor is not None:
        if profit_factor < 1.0:
            insights.append({
                "priority": "HIGH",
                "title":    f"Profit Factor Below 1.0 — Strategy Is Net Negative ({profit_factor:.2f})",
                "observation": (
                    f"Profit factor: **{profit_factor:.2f}** "
                    "(total gross profit / total gross loss = ratio below 1.0 means losses exceed wins)"
                ),
                "implication": (
                    "A profit factor below 1.0 means, in aggregate, you've lost more money on "
                    "losing trades than you've made on winning trades. "
                    "Over time this destroys capital regardless of win rate."
                ),
                "action": (
                    "The immediate fix is not to win more — it's to lose less per loss. "
                    "Set hard stops on every new trade before entry. "
                    "If a position hits the stop, exit immediately — no waiting for recovery."
                ),
                "institutional_lens": (
                    "A profit factor below 1.0 is the quantitative definition of a broken strategy. "
                    "Institutional risk committee would flag any strategy with profit factor under 1.2 "
                    "for mandatory review — under 1.0 would trigger an immediate size reduction. "
                    "The path back is not through bigger wins; it's through smaller, more disciplined losses."
                ),
            })
        elif profit_factor >= 2.0:
            insights.append({
                "priority": "OK",
                "title":    f"Healthy Profit Factor ({profit_factor:.2f}) — Strategy Is Structurally Sound",
                "observation": (
                    f"Profit factor: **{profit_factor:.2f}** — your gross profits are "
                    f"{profit_factor:.1f}× your gross losses across all closed trades."
                ),
                "implication": (
                    "A profit factor above 2.0 means the strategy has robust positive expectancy. "
                    "Even in adverse conditions, you have meaningful buffer before breakeven."
                ),
                "action": (
                    "Protect this metric. The most common way to degrade a healthy profit factor "
                    "is to start holding losers longer (hoping for recovery) or cutting winners "
                    "too early (fear of giving back gains)."
                ),
                "institutional_lens": (
                    "Quantitative research teams target a minimum profit factor of 1.5 for any live strategy. "
                    "At 2.0+, the strategy has enough cushion to survive adverse market regimes. "
                    "The discipline is keeping it there: one emotional override of a stop "
                    "can degrade a month of good discipline."
                ),
            })

    # Sort: HIGH first, then MEDIUM, then OK
    order = {"HIGH": 0, "MEDIUM": 1, "OK": 2}
    insights.sort(key=lambda x: order.get(x["priority"], 3))
    return insights


def build_full_analytics(trades_df: pd.DataFrame) -> dict:
    """
    Main entry point. Returns complete analytics dict for the Trade Journal page.
    Keys: ext_df, trigger_df, monthly_df, hold_stats, profit_factor,
          avg_win_pct, avg_loss_pct, insights
    """
    empty = {
        "ext_df":       pd.DataFrame(),
        "trigger_df":   pd.DataFrame(),
        "monthly_df":   pd.DataFrame(),
        "hold_stats":   {},
        "profit_factor": None,
        "avg_win_pct":  None,
        "avg_loss_pct": None,
        "insights":     [],
    }

    if trades_df is None or trades_df.empty:
        return empty

    ext_df = compute_extended_stats(trades_df)
    if ext_df.empty:
        return empty

    winners = ext_df[ext_df["realized_pnl"] > 0]
    losers  = ext_df[ext_df["realized_pnl"] < 0]
    gross_profit = float(winners["realized_pnl"].sum()) if not winners.empty else 0.0
    gross_loss   = abs(float(losers["realized_pnl"].sum())) if not losers.empty else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

    avg_win_pct  = float(winners["pnl_pct"].mean()) if not winners.empty and winners["pnl_pct"].notna().any() else None
    avg_loss_pct = float(losers["pnl_pct"].mean())  if not losers.empty  and losers["pnl_pct"].notna().any()  else None

    win_rate = len(winners) / len(ext_df) * 100 if len(ext_df) else 0.0

    trigger_df = build_trigger_breakdown(ext_df)
    monthly_df = build_monthly_trend(ext_df)
    hold_stats = build_hold_time_stats(ext_df)

    insights = build_behavioral_insights(
        ext_df, trigger_df, monthly_df, hold_stats,
        win_rate, profit_factor, avg_win_pct, avg_loss_pct,
    )

    return {
        "ext_df":        ext_df,
        "trigger_df":    trigger_df,
        "monthly_df":    monthly_df,
        "hold_stats":    hold_stats,
        "profit_factor": profit_factor,
        "avg_win_pct":   avg_win_pct,
        "avg_loss_pct":  avg_loss_pct,
        "insights":      insights,
    }
