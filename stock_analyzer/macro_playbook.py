"""
Pre-Event Macro Playbook.

For each upcoming high-impact macro event generates:
  - Portfolio-level exposure score and dollar range (bull/base/bear)
  - Position-level pre-event actions: PROTECT / WATCH / HOLD / OPPORTUNITY
  - Specific rationale and action detail per position
  - Post-event decision rules (what to do the morning the number drops)
"""

from datetime import date as _date
import pandas as _pd


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


# ── Scenario definitions ──────────────────────────────────────────────────────
# sector_moves: absolute % move for that sector under that scenario
# (positive = gain, negative = loss)
_SCENARIOS: dict = {

    "Non-Farm Payrolls": {
        "bull": {
            "label":      "Strong Beat",
            "icon":       "🐂",
            "condition":  "Payrolls +200K+, unemployment holds or falls, upward revisions to prior months",
            "market_pct": +1.2,
            "notes":      "Consumer confidence surges. Rate-cut timeline extends but growth momentum dominates the reaction.",
            "sector_moves": {
                "Consumer Tech": +2.5, "EV & Auto": +2.0, "Financials": +2.5,
                "Semiconductors": +1.0, "AI & Data": +0.5, "AI & Cloud": +0.5,
                "Cybersecurity": +0.5, "Healthcare": +0.5, "Energy": +1.0,
                "Clean Energy": -0.5, "Defense": +0.5,
            },
        },
        "base": {
            "label":      "In-Line (~165K)",
            "icon":       "📊",
            "condition":  "Payrolls within ±30K of consensus, unemployment stable, wage growth near estimates",
            "market_pct": +0.2,
            "notes":      "Muted reaction. Fed stays the course. Market attention shifts to next CPI print.",
            "sector_moves": {k: 0.0 for k in [
                "Consumer Tech", "EV & Auto", "Financials", "Semiconductors",
                "AI & Data", "AI & Cloud", "Cybersecurity", "Healthcare",
                "Energy", "Clean Energy", "Defense",
            ]},
        },
        "bear": {
            "label":      "Miss + Downward Revisions",
            "icon":       "🐻",
            "condition":  "Payrolls below 100K or prior months revised down sharply; unemployment rate rises",
            "market_pct": -1.8,
            "notes":      "Recession narrative takes hold. Consumer and cyclical sectors hit hardest. Defensive rotation into healthcare and defense.",
            "sector_moves": {
                "Consumer Tech": -2.5, "EV & Auto": -3.0, "Financials": -2.0,
                "Semiconductors": -1.5, "AI & Data": -1.2, "AI & Cloud": -1.0,
                "Cybersecurity": -0.8, "Healthcare": -0.5, "Energy": -1.5,
                "Clean Energy": -1.0, "Defense": -0.3,
            },
        },
    },

    "CPI Inflation": {
        "bull": {
            "label":      "Cool Print",
            "icon":       "🐂",
            "condition":  "Core CPI YoY decelerates or 0.1pp+ below consensus; shelter costs ease",
            "market_pct": +1.5,
            "notes":      "Rate-cut timeline accelerates. Long-duration growth (AI, clean energy) leads the rally. Bonds rally, dollar weakens.",
            "sector_moves": {
                "AI & Data": +2.5, "AI & Cloud": +2.0, "Semiconductors": +2.0,
                "Clean Energy": +2.5, "EV & Auto": +1.5, "Consumer Tech": +1.5,
                "Cybersecurity": +1.2, "Healthcare": +0.8, "Financials": -0.5,
                "Energy": -0.8, "Defense": +0.3,
            },
        },
        "base": {
            "label":      "In-Line",
            "icon":       "📊",
            "condition":  "Core CPI within ±0.05pp of consensus; no component surprises",
            "market_pct": +0.1,
            "notes":      "Minimal reaction. Consensus already priced in. Watch shelter and services sub-components for forward signal.",
            "sector_moves": {k: 0.0 for k in [
                "AI & Data", "AI & Cloud", "Semiconductors", "Clean Energy",
                "EV & Auto", "Consumer Tech", "Cybersecurity", "Healthcare",
                "Financials", "Energy", "Defense",
            ]},
        },
        "bear": {
            "label":      "Hot Print",
            "icon":       "🐻",
            "condition":  "Core CPI re-accelerates or 0.1pp+ above consensus; shelter or services components sticky",
            "market_pct": -1.5,
            "notes":      "Higher-for-longer narrative returns. Growth tech hit hardest. Rate-cut probability collapses. Financials may outperform.",
            "sector_moves": {
                "AI & Data": -2.5, "AI & Cloud": -2.0, "Semiconductors": -2.0,
                "Clean Energy": -3.0, "EV & Auto": -2.0, "Consumer Tech": -1.5,
                "Cybersecurity": -1.2, "Healthcare": -0.5, "Financials": +0.8,
                "Energy": +0.5, "Defense": -0.3,
            },
        },
    },

    "FOMC Rate Decision": {
        "bull": {
            "label":      "Dovish Surprise",
            "icon":       "🐂",
            "condition":  "Rate cut delivered or clear signal of imminent cut; dovish press conference; lower dot plot",
            "market_pct": +1.8,
            "notes":      "All risk assets rally. Long-duration tech and clean energy lead. Dollar weakens, bond yields fall.",
            "sector_moves": {
                "AI & Data": +2.5, "AI & Cloud": +2.0, "Semiconductors": +2.0,
                "Clean Energy": +3.0, "EV & Auto": +2.0, "Consumer Tech": +1.8,
                "Cybersecurity": +1.5, "Healthcare": +1.0, "Financials": -0.5,
                "Energy": +0.5, "Defense": +0.5,
            },
        },
        "base": {
            "label":      "Hold + Neutral Language",
            "icon":       "📊",
            "condition":  "Hold as expected; statement unchanged; dot plot in-line; non-committal press conference",
            "market_pct": +0.2,
            "notes":      "Status quo. Brief relief rally on 'no surprise' often fades within hours.",
            "sector_moves": {k: 0.0 for k in [
                "AI & Data", "AI & Cloud", "Semiconductors", "Clean Energy",
                "EV & Auto", "Consumer Tech", "Cybersecurity", "Healthcare",
                "Financials", "Energy", "Defense",
            ]},
        },
        "bear": {
            "label":      "Hawkish Hold or Hike",
            "icon":       "🐻",
            "condition":  "Hold with hawkish language; fewer cuts in dot plot; surprise hike",
            "market_pct": -2.0,
            "notes":      "Risk-off across the board. Rate-sensitive growth (AI, clean energy) sell off hardest. Dollar surges.",
            "sector_moves": {
                "AI & Data": -2.8, "AI & Cloud": -2.5, "Semiconductors": -2.2,
                "Clean Energy": -3.5, "EV & Auto": -2.2, "Consumer Tech": -2.0,
                "Cybersecurity": -1.5, "Healthcare": -0.8, "Financials": +0.5,
                "Energy": +0.3, "Defense": -0.5,
            },
        },
    },

    "GDP Advance Estimate": {
        "bull": {
            "label":      "Above-Consensus Growth",
            "icon":       "🐂",
            "condition":  "GDP beats consensus 0.3pp+ QoQ annualised; PCE price index contained",
            "market_pct": +1.0,
            "notes":      "Risk-on, cyclicals and financials lead. Tempered if PCE deflator is hot — stagflation concern dampens rally.",
            "sector_moves": {
                "Financials": +2.0, "EV & Auto": +1.5, "Consumer Tech": +1.5,
                "Semiconductors": +1.2, "AI & Data": +1.0, "AI & Cloud": +1.0,
                "Energy": +1.5, "Defense": +0.8, "Healthcare": +0.5,
                "Clean Energy": +0.5, "Cybersecurity": +0.5,
            },
        },
        "base": {
            "label":      "In-Line",
            "icon":       "📊",
            "condition":  "GDP within ±0.3pp of consensus; no major composition surprises",
            "market_pct": 0.0,
            "notes":      "Minimal reaction. Market focus returns to earnings and next CPI.",
            "sector_moves": {k: 0.0 for k in [
                "Financials", "EV & Auto", "Consumer Tech", "Semiconductors",
                "AI & Data", "AI & Cloud", "Energy", "Defense", "Healthcare",
                "Clean Energy", "Cybersecurity",
            ]},
        },
        "bear": {
            "label":      "Contraction or Sharp Miss",
            "icon":       "🐻",
            "condition":  "GDP below consensus 0.5pp+ or negative print",
            "market_pct": -1.5,
            "notes":      "Recession narrative dominates. Defensive rotation. Consumer and financials hit hardest.",
            "sector_moves": {
                "EV & Auto": -2.5, "Consumer Tech": -2.0, "Financials": -2.0,
                "Semiconductors": -1.8, "AI & Data": -1.5, "AI & Cloud": -1.5,
                "Energy": -2.0, "Clean Energy": -1.0, "Healthcare": -0.5,
                "Defense": -0.3, "Cybersecurity": -0.8,
            },
        },
    },

    "PPI Producer Prices": {
        "bull": {
            "label":      "Cool PPI",
            "icon":       "🐂",
            "condition":  "Core PPI below consensus — pipeline inflation easing; margin relief ahead",
            "market_pct": +0.6,
            "notes":      "Leads CPI lower. Moderate positive for growth stocks. Less market-moving than CPI.",
            "sector_moves": {
                "AI & Data": +1.0, "Semiconductors": +1.2, "EV & Auto": +1.0,
                "Clean Energy": +0.8, "Consumer Tech": +0.8, "AI & Cloud": +0.8,
                "Cybersecurity": +0.5, "Financials": -0.3, "Healthcare": +0.3,
                "Energy": -0.5, "Defense": +0.2,
            },
        },
        "base": {
            "label":      "In-Line",
            "icon":       "📊",
            "condition":  "PPI matches consensus — no surprise",
            "market_pct": 0.0,
            "notes":      "Minimal market impact. Look ahead to CPI the following day.",
            "sector_moves": {k: 0.0 for k in [
                "AI & Data", "Semiconductors", "EV & Auto", "Clean Energy",
                "Consumer Tech", "AI & Cloud", "Cybersecurity",
                "Financials", "Healthcare", "Energy", "Defense",
            ]},
        },
        "bear": {
            "label":      "Hot PPI",
            "icon":       "🐻",
            "condition":  "Core PPI above consensus — upstream inflation re-accelerating",
            "market_pct": -0.8,
            "notes":      "Signals future CPI risk. Margin compression for sectors that can't pass costs through.",
            "sector_moves": {
                "AI & Data": -1.0, "Semiconductors": -1.5, "EV & Auto": -1.5,
                "Clean Energy": -1.2, "Consumer Tech": -1.0, "AI & Cloud": -0.8,
                "Cybersecurity": -0.5, "Financials": +0.3, "Healthcare": -0.5,
                "Energy": +0.5, "Defense": -0.3,
            },
        },
    },

    "Retail Sales": {
        "bull": {
            "label":      "Strong Consumer",
            "icon":       "🐂",
            "condition":  "Retail sales beat consensus; control group (ex-autos/gas/food) +0.4%+ MoM",
            "market_pct": +0.8,
            "notes":      "Consumer resilience confirmed. Consumer-facing sectors outperform. Reduces near-term recession risk.",
            "sector_moves": {
                "Consumer Tech": +2.0, "EV & Auto": +2.0, "Financials": +1.0,
                "AI & Data": +0.5, "Semiconductors": +0.5, "Healthcare": +0.3,
                "Energy": +0.5, "AI & Cloud": +0.3, "Clean Energy": +0.2,
                "Cybersecurity": +0.3, "Defense": +0.2,
            },
        },
        "base": {
            "label":      "In-Line",
            "icon":       "📊",
            "condition":  "Retail sales near consensus ±0.2%; no major category surprises",
            "market_pct": 0.0,
            "notes":      "Minimal reaction. Consistent with stable but uninspiring consumer picture.",
            "sector_moves": {k: 0.0 for k in [
                "Consumer Tech", "EV & Auto", "Financials", "AI & Data",
                "Semiconductors", "Healthcare", "Energy", "AI & Cloud",
                "Clean Energy", "Cybersecurity", "Defense",
            ]},
        },
        "bear": {
            "label":      "Consumer Slowdown",
            "icon":       "🐻",
            "condition":  "Retail sales miss consensus; control group flat or negative",
            "market_pct": -0.8,
            "notes":      "Consumer stress signal. Discretionary and consumer-facing sectors hit hardest.",
            "sector_moves": {
                "Consumer Tech": -2.5, "EV & Auto": -2.5, "Financials": -1.0,
                "AI & Data": -0.5, "Semiconductors": -0.8, "Healthcare": -0.3,
                "Energy": -0.5, "AI & Cloud": -0.3, "Clean Energy": -0.2,
                "Cybersecurity": -0.3, "Defense": -0.2,
            },
        },
    },
}

# Action thresholds
_PROTECT_WEIGHT  = 18.0   # % — oversized position
_PROTECT_SCORE   = 44.0   # composite score — weak fundamentals
_PROTECT_BEAR    = 1.5    # min % sector bear-move to flag PROTECT
_WATCH_WEIGHT    = 8.0    # min weight to flag WATCH
_WATCH_BEAR      = 1.0    # min sector bear-move to flag WATCH
_OPP_SCORE       = 68.0   # min score for OPPORTUNITY
_OPP_BULL        = 1.5    # min sector bull-move for OPPORTUNITY


def _pre_event_action(row, event_name: str, days_until: int) -> tuple:
    score   = _f(row.get("Score"),    50.0)
    signal  = str(row.get("Signal",   ""))
    weight  = _f(row.get("Weight (%)"), 0.0)
    pnl_pct = _f(row.get("P&L (%)"),  0.0)
    sector  = str(row.get("Sector",   ""))

    sc = _SCENARIOS.get(event_name, {})
    bear_move = abs(_f(sc.get("bear", {}).get("sector_moves", {}).get(sector, 0)))
    bull_move =    _f(sc.get("bull", {}).get("sector_moves", {}).get(sector, 0))

    if "Sell" in signal or "Strong Sell" in signal:
        return "PROTECT", "HIGH"
    if score < _PROTECT_SCORE and bear_move >= _PROTECT_BEAR:
        return "PROTECT", "HIGH"
    if weight > _PROTECT_WEIGHT and bear_move >= _PROTECT_BEAR:
        return "PROTECT", "HIGH"
    if pnl_pct < -15.0 and bear_move >= _PROTECT_BEAR and days_until <= 7:
        return "PROTECT", "MEDIUM"

    if (score >= _OPP_SCORE and
            ("Buy" in signal or "Strong Buy" in signal) and
            bull_move >= _OPP_BULL and days_until <= 14):
        return "OPPORTUNITY", "OK"

    if bear_move >= _PROTECT_BEAR and weight >= _WATCH_WEIGHT:
        return "WATCH", "MEDIUM"
    if bear_move >= _WATCH_BEAR and (score < 55.0 or weight >= 12.0):
        return "WATCH", "LOW"

    return "HOLD", "OK"


def _build_rationale(row, event_name: str, action: str) -> str:
    ticker  = str(row.get("Ticker", ""))
    sector  = str(row.get("Sector", ""))
    weight  = _f(row.get("Weight (%)"), 0.0)
    score   = _f(row.get("Score"),      50.0)
    signal  = str(row.get("Signal",     ""))
    pnl_pct = _f(row.get("P&L (%)"),   0.0)

    sc         = _SCENARIOS.get(event_name, {})
    bear_move  = abs(_f(sc.get("bear", {}).get("sector_moves", {}).get(sector, 0)))
    bull_move  =    _f(sc.get("bull", {}).get("sector_moves", {}).get(sector, 0))
    bear_label = sc.get("bear", {}).get("label", "miss scenario")
    bull_label = sc.get("bull", {}).get("label", "beat scenario")

    if action == "PROTECT":
        if "Sell" in signal:
            return (
                f"**{ticker}** already has a Sell signal — heading into a binary event where "
                f"{sector} could move ~{bear_move:.0f}% on a miss is event risk on a broken thesis. "
                f"The worst combination in portfolio management."
            )
        if score < _PROTECT_SCORE:
            return (
                f"**{ticker}** composite score is {score:.0f}/100 — weak fundamentals compounded "
                f"by a binary event that could move {sector} ~{bear_move:.0f}% on a miss. "
                f"Each risk layer independently justifies reducing size. Both together mandate action."
            )
        if weight > _PROTECT_WEIGHT:
            return (
                f"**{ticker}** is {weight:.0f}% of your portfolio — the institutional rule: "
                f"no single position above 15-18% going into a binary event regardless of conviction. "
                f"A ~{bear_move:.0f}% sector move on a miss creates asymmetric downside at this size."
            )
        return (
            f"**{ticker}** is down {abs(pnl_pct):.0f}% and in a sector with ~{bear_move:.0f}% "
            f"downside in the bear case. An event-driven gap down compounds an existing loss — "
            f"limited upside to holding through the release."
        )

    if action == "OPPORTUNITY":
        return (
            f"**{ticker}** is high-conviction (score {score:.0f}/100, {signal}) in {sector} — "
            f"a sector that could move ~{bull_move:.0f}% on a {bull_label}. "
            f"Positive revision momentum into an event is the strongest alpha setup in institutional investing."
        )

    if action == "WATCH":
        return (
            f"**{ticker}** ({sector}) has ~{bear_move:.0f}% sector exposure in the bear scenario. "
            f"Score {score:.0f}/100, weight {weight:.1f}%. No pre-event action required — "
            f"but be at your terminal when the number drops and have a decision rule ready."
        )

    if bear_move < 0.5:
        return f"**{ticker}** has limited direct exposure to this event category. No action needed."
    return (
        f"**{ticker}** — score {score:.0f}/100, weight {weight:.1f}%. "
        f"Risk/reward of holding through the release is acceptable at current size."
    )


def _action_detail(row, event_name: str, action: str) -> str:
    ticker  = str(row.get("Ticker", ""))
    sector  = str(row.get("Sector", ""))
    weight  = _f(row.get("Weight (%)"), 0.0)
    shares  = int(_f(row.get("Shares"), 0))
    price   = _f(row.get("Price"),  0.0)
    mval    = _f(row.get("Market Value"), 0.0)

    sc        = _SCENARIOS.get(event_name, {})
    bear_move = abs(_f(sc.get("bear", {}).get("sector_moves", {}).get(sector, 0)))

    if action == "PROTECT":
        if weight > _PROTECT_WEIGHT:
            target_w   = 15.0
            trim_frac  = (weight - target_w) / weight
            trim_sh    = max(1, int(shares * trim_frac))
            trim_val   = round(trim_frac * mval)
            return (
                f"Trim to ~15% weight: **sell {trim_sh:,} shares (~${trim_val:,.0f})** before the event. "
                f"Keep the core position for the upside scenario — just cap the binary event risk."
            )
        trim_sh  = max(1, shares // 2)
        trim_val = round(trim_sh * price)
        return (
            f"Reduce 50% before the release: **sell {trim_sh:,} shares (~${trim_val:,.0f})**. "
            f"Re-enter from a smaller base if the report clears the risk."
        )

    if action == "OPPORTUNITY":
        add_sh  = max(1, int(shares * 0.10))
        add_val = round(add_sh * price)
        return (
            f"Consider a small pre-event add: **{add_sh:,} shares (~${add_val:,.0f})** — "
            f"10% of current size. Deploy in 1-2 tranches, not all at once."
        )

    return (
        f"Hold current position. Stops in place. "
        f"Be at your terminal when the release drops at 08:30 ET."
    )


def _post_event_rules(row, event_name: str) -> str:
    ticker     = str(row.get("Ticker", ""))
    sector     = str(row.get("Sector", ""))
    sc         = _SCENARIOS.get(event_name, {})
    bear_move  = _f(sc.get("bear", {}).get("sector_moves", {}).get(sector, 0))
    bull_move  = _f(sc.get("bull", {}).get("sector_moves", {}).get(sector, 0))
    bear_label = sc.get("bear", {}).get("label", "miss")
    bull_label = sc.get("bull", {}).get("label", "beat")

    rules = []
    if bear_move < -1.0:
        rules.append(
            f"**If {bear_label}:** Check {ticker} pre-market. "
            f"If down {abs(bear_move):.0f}%+, confirm thesis holds before staying long — "
            f"gaps bypass stops entirely."
        )
    if bull_move > 1.0:
        rules.append(
            f"**If {bull_label}:** Resist chasing the open gap on {ticker}. "
            f"Wait for the first 30-min candle to confirm direction before adding."
        )
    if not rules:
        rules.append(
            f"Monitor {ticker} for secondary spillover in the 24h post-release."
        )
    return "  ·  ".join(rules)


def build_event_playbooks(
    events: list,
    port_df,
    total_val: float,
) -> list:
    """
    Generate a Pre-Event Playbook for each upcoming HIGH-impact event.

    Parameters
    ----------
    events    : list of event dicts from build_macro_calendar (future events only)
    port_df   : enriched portfolio DataFrame
    total_val : total portfolio market value ($)

    Returns list of playbook dicts sorted by date.
    """
    if port_df is None or port_df.empty:
        return []

    today     = _date.today()
    playbooks = []

    for event in events:
        if event.get("impact") != "HIGH":
            continue

        ev_name    = event["event"]
        ev_date    = event["date"]
        days_until = (ev_date - today).days

        if days_until < 0:
            continue

        sc_def = _SCENARIOS.get(ev_name)
        if not sc_def:
            continue

        positions          = []
        total_bear_impact  = 0.0
        total_bull_impact  = 0.0

        for _, row in port_df.iterrows():
            sector    = str(row.get("Sector", ""))
            mval      = _f(row.get("Market Value"), 0.0)

            bull_move = _f(sc_def["bull"]["sector_moves"].get(sector, 0))
            base_move = _f(sc_def["base"]["sector_moves"].get(sector, 0))
            bear_move = _f(sc_def["bear"]["sector_moves"].get(sector, 0))

            if abs(bull_move) < 0.1 and abs(bear_move) < 0.1:
                continue

            bull_impact = round(mval * bull_move / 100, 0)
            base_impact = round(mval * base_move / 100, 0)
            bear_impact = round(mval * bear_move / 100, 0)
            total_bull_impact += bull_impact
            total_bear_impact += bear_impact

            action, priority = _pre_event_action(row, ev_name, days_until)
            rationale        = _build_rationale(row, ev_name, action)
            detail           = _action_detail(row, ev_name, action)
            post_evt         = _post_event_rules(row, ev_name)

            positions.append({
                "ticker":        str(row.get("Ticker", "")),
                "sector":        sector,
                "weight":        _f(row.get("Weight (%)"), 0.0),
                "score":         _f(row.get("Score"),      50.0),
                "signal":        str(row.get("Signal",     "")),
                "pnl_pct":       _f(row.get("P&L (%)"),   0.0),
                "market_value":  mval,
                "shares":        int(_f(row.get("Shares"), 0)),
                "price":         _f(row.get("Price"),      0.0),
                "action":        action,
                "priority":      priority,
                "rationale":     rationale,
                "detail":        detail,
                "post_event":    post_evt,
                "bull_impact":   bull_impact,
                "base_impact":   base_impact,
                "bear_impact":   bear_impact,
                "bull_move_pct": bull_move,
                "bear_move_pct": bear_move,
            })

        _order = {"PROTECT": 0, "WATCH": 1, "OPPORTUNITY": 2, "HOLD": 3}
        positions.sort(key=lambda x: (_order.get(x["action"], 4), -x["weight"]))

        # Portfolio exposure = % of portfolio in sectors with high bear sensitivity
        exposed_val  = sum(p["market_value"] for p in positions if abs(p["bear_move_pct"]) >= 1.5)
        exposure_pct = round(exposed_val / total_val * 100, 1) if total_val > 0 else 0.0

        if exposure_pct >= 55:
            exp_level, exp_color = "CRITICAL", "#ef4444"
        elif exposure_pct >= 35:
            exp_level, exp_color = "HIGH",     "#f59e0b"
        elif exposure_pct >= 15:
            exp_level, exp_color = "MEDIUM",   "#3b82f6"
        else:
            exp_level, exp_color = "LOW",      "#22c55e"

        protect_count = sum(1 for p in positions if p["action"] == "PROTECT")
        watch_count   = sum(1 for p in positions if p["action"] == "WATCH")
        opp_count     = sum(1 for p in positions if p["action"] == "OPPORTUNITY")

        playbooks.append({
            "event":             ev_name,
            "date":              ev_date,
            "days_until":        days_until,
            "days_label":        event.get("days_label", ""),
            "category":          event.get("category", ""),
            "description":       event.get("description", ""),
            "estimate":          event.get("estimate"),
            "previous":          event.get("previous"),
            "context":           event.get("context", ""),
            "watch_for":         event.get("watch_for", []),
            "exposure_pct":      exposure_pct,
            "exposure_level":    exp_level,
            "exposure_color":    exp_color,
            "total_bear_impact": round(total_bear_impact, 0),
            "total_bull_impact": round(total_bull_impact, 0),
            "protect_count":     protect_count,
            "watch_count":       watch_count,
            "opp_count":         opp_count,
            "positions":         positions,
            "scenarios":         sc_def,
        })

    playbooks.sort(key=lambda x: x["date"])
    return playbooks
