import math
import pandas as pd
import numpy as np

from stock_analyzer.constants import COMPOSITE_BUY, COMPOSITE_SELL, DIVERSIFY_SCAN_CAP, UNCLASSIFIED_SECTOR, SINGLE_NAME_CEILING, SINGLE_NAME_TRIM_TRIGGER, SECTOR_CEILING, SECTOR_REDUCE_TRIGGER, ATR_STOP_MULT, GAP_TO_STOP_ROUND_DECIMALS, CORR_HIGH_PAIRS_THRESHOLD, CORR_DANGER_PAIRS_THRESHOLD, POSITION_AT_RISK_GAP_PCT, APPROACHING_STOP_GAP_PCT, ALERT_PNL_PROFIT_TAKE_PCT, ALERT_PNL_STOP_LOSS_PCT, REBALANCE_TRIM_PNL_PCT, REBALANCE_ADD_MIN_SCORE, REBALANCE_ADD_UNDERSIZED_PCT, REBALANCE_ADD_TARGET_WEIGHT_PCT, REBALANCE_REVIEW_GAP_PCT, DIVERSIFY_REDUCE_HIGH_URGENCY_PCT, DIVERSIFY_ADD_SKIP_PCT, DIVERSIFY_ADD_TARGET_PCT, PT_TARGET_LOOKBACK_DAYS, STOP_RATCHET_LEVELS, EARNINGS_IMMINENT_DAYS, EARNINGS_CRITICAL_DAYS
from stock_analyzer.discovery_universe import DISCOVERY_UNIVERSE
from stock_analyzer.earnings_advisor import _today_et


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
    "LRCX": "Semiconductors",
    "AAPL": "Consumer Tech", "AMZN": "Consumer Tech", "NFLX": "Consumer Tech",
    "SHOP": "Consumer Tech", "UBER": "Consumer Tech", "ABNB": "Consumer Tech",
    "BKNG": "Consumer Tech",
    "TSLA": "EV & Auto", "ENPH": "Clean Energy", "FSLR": "Clean Energy",
    "NEE": "Clean Energy", "BEP": "Clean Energy",
    "CRWD": "Cybersecurity", "NET": "Cybersecurity", "PANW": "Cybersecurity",
    "ZS": "Cybersecurity", "FTNT": "Cybersecurity", "OKTA": "Cybersecurity", "S": "Cybersecurity",
    "DELL": "Enterprise Tech", "ORCL": "Enterprise Tech", "IBM": "Enterprise Tech",
    "HPE": "Enterprise Tech", "SAP": "Enterprise Tech",
    "PLTR": "AI & Data", "AI": "AI & Data", "MDB": "AI & Data", "SNOW": "AI & Data",
    "PATH": "AI & Data", "IONQ": "AI & Data",
    # Data / observability / search infra — same correlated cluster as SNOW/MDB.
    "ESTC": "AI & Data", "CFLT": "AI & Data", "GTLB": "AI & Data",
    "MSFT": "AI & Cloud", "GOOGL": "AI & Cloud", "META": "AI & Cloud",
    "CRM": "AI & Cloud", "NOW": "AI & Cloud", "DDOG": "AI & Cloud",
    # Consumer-internet / social-advertising names — map to Consumer Tech so they
    # don't fall through to the "Other" catch-all and inflate a phantom breach.
    "PINS": "Consumer Tech", "SPOT": "Consumer Tech", "DASH": "Consumer Tech",
    "DIS": "Consumer Tech", "SNAP": "Consumer Tech",
    "LLY": "Healthcare", "NVO": "Healthcare", "ABBV": "Healthcare",
    "ISRG": "Healthcare", "MRNA": "Healthcare", "REGN": "Healthcare",
    "UNH": "Healthcare", "JNJ": "Healthcare", "PFE": "Healthcare",
    "MRK": "Healthcare", "TMO": "Healthcare", "ABT": "Healthcare",
    "AMGN": "Healthcare", "BMY": "Healthcare", "MDT": "Healthcare", "DHR": "Healthcare",
    "BIIB": "Healthcare", "BSX": "Healthcare",
    "JPM": "Financials", "V": "Financials", "MA": "Financials",
    "GS": "Financials", "XYZ": "Financials", "COIN": "Financials",
    "BX": "Financials", "BAC": "Financials", "WFC": "Financials",
    "C": "Financials", "MS": "Financials", "SCHW": "Financials", "BLK": "Financials",
    "COF": "Financials", "HOOD": "Financials",
    "LMT": "Defense", "RTX": "Defense", "NOC": "Defense", "GD": "Defense",
    "XOM": "Energy", "CVX": "Energy", "OXY": "Energy", "COP": "Energy",
    "EOG": "Energy",
    "SPCX": "Communications",   # Specialty Telecom (Nasdaq classification)
}


def resolve_sector(ticker: str, fallback: str = "") -> str:
    """Single source of truth for a ticker's sector bucket: curated
    TICKER_SECTORS first, then a provider-supplied fallback (yfinance/scanner
    sector), then the UNCLASSIFIED_SECTOR catch-all. Used by both held-position
    classification and the Brief's pick path so a mapped name classifies the
    same way everywhere."""
    # None-safe fallback: check truthiness BEFORE str() so a None/blank provider
    # sector falls through to UNCLASSIFIED_SECTOR — NOT the literal "None" (which
    # would dodge the breach-gate's "Other" exclusion). Mirrors the original
    # build_portfolio_df expression `(r.get("sector") or "").strip()`.
    return (
        TICKER_SECTORS.get(str(ticker).strip().upper())
        or (str(fallback).strip() if fallback else "")
        or UNCLASSIFIED_SECTOR
    )


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
    for threshold, multiplier, label in STOP_RATCHET_LEVELS:
        if gain_pct >= threshold:
            floor = avg_cost * (1 + multiplier)
            return round(max(atr_stop, floor), 2), label
    return round(atr_stop, 2), "ATR Stop"


def manual_stop_wins(manual_price: float, protective_stop_price: float) -> bool:
    """Single source for the manual-override policy: does a user's manual stop
    beat the auto (protective) stop?

    A manual stop wins ONLY when it is at least as TIGHT (>=) as the ratcheted
    protective stop — a manual sitting BELOW a fresh ratchet floor would erode
    profit protection, so the ratchet keeps winning there. Both surfaces that
    apply a manual override (build_portfolio_df, feeding the Brief; and the
    Analysis manual-stop merge) MUST gate through this one predicate — if the
    comparison ever drifts (e.g. >= tightened to >) on only one of them, a
    manual in the [ATR stop, ratchet floor) gap is accepted on one surface and
    rejected on the other, re-opening the split-brain closed 2026-07-07.
    """
    return bool(
        manual_price and manual_price > 0
        and protective_stop_price and manual_price >= protective_stop_price
    )


def stop_ladder(
    price: float,
    avg_cost: float,
    atr_val: float,
    atr_multiplier: float = ATR_STOP_MULT,
    tighten_multiplier: float | None = None,
    manual_stop: float | None = None,
    atr_stop_override: float | None = None,
) -> dict | None:
    """Full stop-management breakdown for ONE position at a given price — the
    single source behind the "How your stop is set" explainer + its what-if
    simulator on the Analysis page.

    FAITHFUL to the live engine by construction: it reuses `protective_stop`
    (the profit ratchet) and the same `price − mult × ATR` formula as
    `risk.atr_stop_loss`, so the explanation/simulation can never drift from the
    numbers the Brief and Scorecard actually act on. The simulator just calls
    this again with a hypothetical price.

    Args:
        price:              current (or hypothetical) price.
        avg_cost:           position average cost.
        atr_val:            current ATR(14) in dollars. Held constant across a
                            what-if (labelled "at today's volatility" in the UI)
                            — ATR is a short-horizon dollar range, not a function
                            of the price level.
        atr_multiplier:     initial-stop ATR multiple (ATR_STOP_MULT = the value
                            risk.atr_stop_loss uses).
        tighten_multiplier: if given (STOP_TIGHTEN_ATR_MULT), also return the
                            profit-lock "tighter alternative" level. None = omit.
        manual_stop:        an active user override; it wins ONLY when it is at
                            least as tight as the auto stop, exactly mirroring
                            build_portfolio_df's override guard.
        atr_stop_override:  when the caller already holds the engine's exact ATR
                            stop (bundle `r["stop"]`, computed from the UNrounded
                            ATR), pass it so the ladder's ATR stop is bit-exact
                            with the "Stop Loss" metric / Brief instead of being
                            re-derived from the rounded `atr_val` (avoids a ±$0.01
                            drift). None → derive from price − mult × atr_val.

    Returns None when the inputs can't form a stop (missing price/atr, or
    avg_cost ≤ 0 so there is no gain basis).
    """
    if not price or price <= 0 or not atr_val or atr_val <= 0 or avg_cost <= 0:
        return None

    gain_pct = (price - avg_cost) / avg_cost * 100.0
    atr_stop = round(atr_stop_override, 2) if atr_stop_override else round(price - atr_multiplier * atr_val, 2)

    # Ratchet floor for the CURRENT gain tier (display-only — which number
    # actually wins is decided by protective_stop below). Mirrors STOP_RATCHET_LEVELS
    # exactly; None until the gain reaches the first rung (+10%).
    ratchet_floor = None
    ratchet_floor_label = None
    for threshold, mult, label in STOP_RATCHET_LEVELS:
        if gain_pct >= threshold:
            ratchet_floor = round(avg_cost * (1 + mult), 2)
            ratchet_floor_label = label
            break

    # Authoritative auto stop + tier label (= max(ATR floor, ratchet floor)).
    auto_stop, tier_label = protective_stop(price, avg_cost, atr_stop)
    # Which NUMBER is in force pre-manual: the ratchet floor only wins when it is
    # actually higher than the ATR stop (the AAPL nuance — the tier label can say
    # "Breakeven guard" while the ATR stop is the number that binds).
    auto_source = "ratchet" if (ratchet_floor is not None and ratchet_floor >= atr_stop) else "atr"

    active_stop = auto_stop
    active_source = auto_source
    manual_applied = False
    # Same override policy as build_portfolio_df / the Analysis merge — route
    # through the shared predicate so this DISPLAY mirror can't drift from the
    # decision the Brief acts on (auto_stop is positive here, so it's a no-op
    # today, but it keeps all three manual-override surfaces single-sourced).
    if manual_stop_wins(manual_stop, auto_stop):
        active_stop = round(manual_stop, 2)
        active_source = "manual"
        manual_applied = True

    gap_pct = round((price - active_stop) / price * 100.0, 1)

    tighten_stop = None
    if tighten_multiplier:
        tighten_stop = round(price - tighten_multiplier * atr_val, 2)

    # Next ratchet rung the price has NOT reached yet — the "keep climbing" story:
    # at +N% your stop auto-ratchets to $floor (locks +M%). None past the top rung.
    next_tier = None
    for threshold, mult, label in sorted(STOP_RATCHET_LEVELS, key=lambda x: x[0]):
        if threshold > gain_pct:
            next_tier = {
                "gain_pct":      threshold,
                "trigger_price": round(avg_cost * (1 + threshold / 100.0), 2),
                "floor":         round(avg_cost * (1 + mult), 2),
                "label":         label,
            }
            break

    # Full profit-lock staircase (all rungs, ascending) — powers the staircase
    # visual. Each rung carries the PRICE that activates the tier and the stop
    # FLOOR it locks. reached = gain already at/above it; is_current = the highest
    # reached rung (the tier in force). Same avg_cost×(1+…) math as protective_stop.
    _reached = [t for t, _, _ in STOP_RATCHET_LEVELS if gain_pct >= t]
    _current_threshold = max(_reached) if _reached else None
    ratchet_rungs = [
        {
            "gain_pct":      threshold,
            "trigger_price": round(avg_cost * (1 + threshold / 100.0), 2),
            "floor":         round(avg_cost * (1 + mult), 2),
            "floor_pct":     round(mult * 100, 1),
            "label":         label,
            "reached":       gain_pct >= threshold,
            "is_current":    threshold == _current_threshold,
        }
        for threshold, mult, label in sorted(STOP_RATCHET_LEVELS, key=lambda x: x[0])
    ]

    return {
        "price":               round(price, 2),
        "avg_cost":            round(avg_cost, 2),
        "gain_pct":            round(gain_pct, 1),
        "atr_val":             round(atr_val, 2),
        "atr_multiplier":      atr_multiplier,
        "atr_stop":            atr_stop,
        "ratchet_floor":       ratchet_floor,
        "ratchet_floor_label": ratchet_floor_label,
        "tier_label":          tier_label,          # profit tier reached (protective_stop label)
        "tier_reached":        ratchet_floor is not None,
        "auto_stop":           auto_stop,
        "auto_source":         auto_source,          # "ratchet" | "atr" (which number wins pre-manual)
        "active_stop":         active_stop,
        "active_source":       active_source,        # "manual" | "ratchet" | "atr"
        "manual_applied":      manual_applied,
        "gap_pct":             gap_pct,
        "tighten_stop":        tighten_stop,
        "next_tier":           next_tier,
        "ratchet_rungs":       ratchet_rungs,
        "stopped_out":         price <= active_stop,
    }


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
    dropped: list[dict] = []
    for h in holdings:
        ticker = str(h.get("Ticker", h.get("ticker", "")) or "").strip().upper()
        shares = _safe_float(h.get("Shares", h.get("shares")))
        avg_cost = _safe_float(h.get("Avg Cost ($)", h.get("avg_cost")))
        if not ticker or shares <= 0 or avg_cost <= 0:
            if ticker:
                dropped.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost})
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
            gap_to_stop = round((price - stop) / price * 100, GAP_TO_STOP_ROUND_DECIMALS)

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
            if manual_stop_wins(_ms_price, stop):
                stop = round(_ms_price, 2)
                stop_label = "Manual"
                gap_to_stop = round((price - stop) / price * 100, GAP_TO_STOP_ROUND_DECIMALS)

        rows.append({
            "Ticker": ticker,
            # Sector: prefer the curated granular bucket (Semiconductors, AI &
            # Data, …); fall back to the ticker's actual yfinance .info sector
            # (already fetched by load_all) before the "Other" catch-all. Without
            # this, every unmapped name piled into "Other", inflating it past the
            # hard cap (ESTC, a Tech/Software name, landed in a 44% "Other"
            # bucket) — a classification artifact, not a real concentration.
            "Sector": resolve_sector(ticker, r.get("sector")),
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
    # Never silently filter (CLAUDE.md UI-suppression rule) — pandas .attrs is
    # pure metadata (no signature/call-site change for the 3 existing callers,
    # invisible to DataFrame equality/column checks), read by app.py to render
    # a visible banner for whichever holdings got dropped above.
    df.attrs["dropped_holdings"] = dropped
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


def alerts(
    portfolio_df: pd.DataFrame,
    held_data: dict | None = None,
    deterioration: list[dict] | None = None,
    pt_cut_signals: dict[str, dict] | None = None,   # ticker -> analyst_targets.detect_pt_cut() output
) -> list[dict]:
    """
    Returns list of alert dicts with keys: level, msg, category.
    Levels: danger, warning, info.
    Categories: stop, signal, concentration, earnings, revisions.

    `deterioration` (optional): the same exit_advisor.assess_holding payload list
    daily_briefing.deterioration_signals() computes for the Daily Brief — passed in
    so bearish-signal alerts can be annotated with the ticker's current WATCH/TRIM/
    EXIT tier (if any), rather than computing a fourth, independent "should this be
    trimmed" read (2026-07-29 audit H4).

    `pt_cut_signals` (optional): ticker -> analyst_targets.detect_pt_cut() output,
    fires an independent "revisions" alert on a consensus price-target cut even
    without an accompanying rating action (F-169 Phase 2 — closes the gap the
    rating-based "Analyst revision spike" branch below cannot see).
    """
    from datetime import datetime as _datetime

    _det_tier = {d["ticker"]: d["tier"] for d in (deterioration or []) if d.get("ticker")}

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
        elif gap < POSITION_AT_RISK_GAP_PCT:
            result.append({
                "level": "danger", "category": "stop",
                "msg": f"🔴 **{ticker}** is within {gap:.1f}% of stop ${row['Stop']:.2f} — review immediately",
            })
        elif gap < APPROACHING_STOP_GAP_PCT:
            result.append({
                "level": "warning", "category": "stop",
                "msg": f"🟡 **{ticker}** is {gap:.1f}% above stop ${row['Stop']:.2f} — monitor closely",
            })

        # Concentration
        if w > SINGLE_NAME_CEILING:
            result.append({
                "level": "warning", "category": "concentration",
                "msg": f"⚠️ **{ticker}** is {w:.1f}% of portfolio — above {SINGLE_NAME_CEILING}% concentration threshold",
            })

        # Bearish signal on profitable or losing position
        _tier_note = f" (deterioration tier: {_det_tier[ticker]})" if ticker in _det_tier else ""
        if "Sell" in signal and pnl > ALERT_PNL_PROFIT_TAKE_PCT:
            result.append({
                "level": "warning", "category": "signal",
                "msg": f"📉 **{ticker}** signal turned bearish with {pnl:.1f}% gain — consider taking partial profits{_tier_note}",
            })
        if "Sell" in signal and pnl < ALERT_PNL_STOP_LOSS_PCT:
            result.append({
                "level": "danger", "category": "signal",
                "msg": f"⛔ **{ticker}** bearish signal with {pnl:.1f}% loss — stop at ${row['Stop']:.2f}{_tier_note}",
            })

    # Sector concentration
    sector_exp = sector_exposure(portfolio_df)
    for _, row in sector_exp.iterrows():
        if row["Pct"] > SECTOR_CEILING:
            result.append({
                "level": "warning", "category": "concentration",
                "msg": f"🏭 **{row['Sector']}** represents {row['Pct']:.0f}% of portfolio — high sector concentration",
            })

    # ── Data-driven alerts (require held_data) ────────────────────────────────
    if held_data:
        today = _today_et()
        for _, row in portfolio_df.iterrows():
            ticker = row["Ticker"]
            r      = held_data.get(ticker, {})

            # Earnings proximity
            earn = r.get("earnings")
            if earn:
                try:
                    days = (_datetime.strptime(earn, "%Y-%m-%d").date() - today).days
                    if 0 <= days <= EARNINGS_CRITICAL_DAYS:
                        result.append({
                            "level": "danger", "category": "earnings",
                            "msg": (
                                f"📅 **{ticker}** earnings in **{days} day{'s' if days != 1 else ''}** ({earn}) "
                                f"— decide your position size before the report"
                            ),
                        })
                    elif EARNINGS_CRITICAL_DAYS < days <= EARNINGS_IMMINENT_DAYS:
                        result.append({
                            "level": "warning", "category": "earnings",
                            "msg": f"📅 **{ticker}** reports earnings in {days} days ({earn}) — review ahead of report",
                        })
                except (ValueError, TypeError):
                    # Malformed/missing earnings-date string from the provider —
                    # isolate to this ticker, same as every other per-ticker loop
                    # in this file. Anything else (a real bug) must surface, not
                    # vanish silently.
                    pass

            # Analyst revision spike
            rev = r.get("revisions", {})
            dns = rev.get("downgrades_90d", 0)
            ups = rev.get("upgrades_90d", 0)
            net = rev.get("net", 0)
            _rating_fired = False
            if dns >= 3 and net <= -2:
                result.append({
                    "level": "danger", "category": "revisions",
                    "msg": (
                        f"📉 **{ticker}** has {dns} analyst downgrades vs {ups} upgrades in 90 days "
                        f"(net {net}) — institutional conviction fading"
                    ),
                })
                _rating_fired = True
            elif dns >= 2 and net < 0:
                result.append({
                    "level": "warning", "category": "revisions",
                    "msg": (
                        f"⚠️ **{ticker}** has {dns} downgrades vs {ups} upgrades in 90 days "
                        f"— monitor for further deterioration"
                    ),
                })
                _rating_fired = True

            # Analyst price-target cut (F-169 Phase 2 — independent of rating
            # action; see docs/architecture.md §6.23). Skipped when the rating
            # branch above already fired for this ticker — both are bearish
            # "revisions" reads on the same name, so surfacing the PT cut too
            # would stack two alerts in one category rather than add a
            # genuinely new one (Opus review, 2026-07-31).
            pt = {} if _rating_fired else ((pt_cut_signals or {}).get(ticker) or {})
            if pt.get("level") == "danger":
                result.append({
                    "level": "danger", "category": "revisions",
                    "msg": (
                        f"🎯 **{ticker}** consensus price target cut {pt['pct_change'] * 100:.1f}% "
                        f"over {PT_TARGET_LOOKBACK_DAYS} trading days "
                        f"(${pt['compare_target']:.2f} → ${pt['newest_target']:.2f}) — no rating change, "
                        f"but analysts are marking down the outlook"
                    ),
                })
            elif pt.get("level") == "warning":
                result.append({
                    "level": "warning", "category": "revisions",
                    "msg": (
                        f"🎯 **{ticker}** consensus price target down {pt['pct_change'] * 100:.1f}% "
                        f"over {PT_TARGET_LOOKBACK_DAYS} trading days — monitor"
                    ),
                })

    return result


def rebalance_actions(
    portfolio_df: pd.DataFrame,
    deterioration: list[dict] | None = None,
) -> list[dict]:
    """
    Returns structured recommendation dicts instead of plain strings.
    Each dict carries the trigger condition and all data needed for the
    evidence panel in app.py (which also injects score breakdowns from held_data).

    `deterioration` (optional): see `alerts()`'s docstring — same annotation-only
    pattern, applied to the "review" (bearish-signal) action type here.
    """
    _det_tier = {d["ticker"]: d["tier"] for d in (deterioration or []) if d.get("ticker")}
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

        if w > SINGLE_NAME_TRIM_TRIGGER and pnl > REBALANCE_TRIM_PNL_PCT:
            trim_val    = mval * (w - SINGLE_NAME_CEILING) / 100
            trim_shares = max(1, int(trim_val / price))
            actions.append({
                "type":    "trim",
                "urgency": "medium",
                "ticker":  ticker,
                "title":   "Oversized Position with Strong Gain",
                "trigger": f"Weight {w:.0f}% exceeds {SINGLE_NAME_TRIM_TRIGGER:.0f}% threshold with +{pnl:.0f}% profit",
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

        if "Strong Buy" in signal and w < REBALANCE_ADD_UNDERSIZED_PCT and score > REBALANCE_ADD_MIN_SCORE:
            add_val = mval * (REBALANCE_ADD_TARGET_WEIGHT_PCT - w) / 100  # rough cost to reach target weight
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
            _gap_close = (gap is None) or (gap < REBALANCE_REVIEW_GAP_PCT)
            urgency = "high" if (score < COMPOSITE_SELL or _gap_close) else "medium"
            half_shares = max(1, shares // 2)
            _tier = _det_tier.get(ticker)
            _trigger = f"Composite score {score:.0f}/100 ({signal.split()[-1]}) while position is +{pnl:.1f}% profitable"
            if _tier:
                _trigger += f" (deterioration tier: {_tier})"
            actions.append({
                "type":       "review",
                "urgency":    urgency,
                "ticker":     ticker,
                "title":      "Bearish Signal on Profitable Position",
                "trigger":    _trigger,
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


# ── Real-Sector Benchmark Tilt (vs. S&P 500) ──────────────────────────────────
# Separate, parallel taxonomy from TICKER_SECTORS above. TICKER_SECTORS's 13
# thematic labels (Consumer Tech, AI & Cloud, ...) don't map 1:1 onto real
# GICS sectors — e.g. "Consumer Tech" mixes AAPL (real: Technology), AMZN
# (Consumer Discretionary), NFLX (Communication Services) — so comparing
# against a real market benchmark needs each ticker's actual provider-reported
# sector, not the curated thematic bucket. That real sector is already fetched
# for every held ticker (bundle_loader.py sets loaded_data[ticker]["sector"]
# from the provider's raw .info["sector"]) but normally discarded in favor of
# TICKER_SECTORS by resolve_sector() — reused here for free, zero new fetches.

# S&P 500 GICS sector weights. Source: English Wikipedia, "S&P 500" article,
# GICS sector weighting table, as of 2026-07-01. Static reference data (no
# live benchmark-weight source exists in the provider layer) — refresh
# periodically (every 6-12 months) since it will silently drift stale
# otherwise. NOT a decision threshold (never gates), so it stays here next to
# SECTOR_ETF rather than in constants.py, matching the _SECTOR_PROFILES
# precedent above.
# Shelf life: registered in stock_analyzer/reference_shelf.py — update its as_of date when you refresh this list.
SP500_SECTOR_WEIGHTS = {
    "Information Technology":  37.4,
    "Financials":              12.0,
    "Communication Services":   9.96,
    "Consumer Discretionary":   9.41,
    "Health Care":              8.96,
    "Industrials":              8.86,
    "Consumer Staples":         4.57,
    "Energy":                   2.97,
    "Utilities":                2.17,
    "Materials":                1.84,
    "Real Estate":              1.84,
    # Materials == Real Estate is not a transcription error — independently
    # re-verified against the same source with a second, targeted fetch.
}

# Normalizes a provider's raw .info["sector"] string (Yahoo/Morningstar-style
# naming differs from GICS-11 naming in several cases) onto the GICS-11 keys
# above. Verify against this app's actual live sector_cache/_last_held_data
# values post-deploy — anything not covered here falls through to "Other"
# (visible, never silently miscounted), so an incomplete alias list degrades
# gracefully rather than producing a wrong bucket.
_PROVIDER_SECTOR_ALIASES = {
    "technology":              "Information Technology",
    "information technology":  "Information Technology",
    "financial services":      "Financials",
    "financials":              "Financials",
    "communication services":  "Communication Services",
    "consumer cyclical":       "Consumer Discretionary",
    "consumer discretionary":  "Consumer Discretionary",
    "healthcare":              "Health Care",
    "health care":             "Health Care",
    "industrials":             "Industrials",
    "consumer defensive":      "Consumer Staples",
    "consumer staples":        "Consumer Staples",
    "energy":                  "Energy",
    "utilities":               "Utilities",
    "basic materials":         "Materials",
    "materials":               "Materials",
    "real estate":             "Real Estate",
}


def _normalize_provider_sector(raw: str | None) -> str:
    """Maps a raw provider sector string onto a GICS-11 key, or 'Other' when
    unmapped/blank — mirrors the UNCLASSIFIED_SECTOR catch-all convention."""
    key = str(raw or "").strip().lower()
    return _PROVIDER_SECTOR_ALIASES.get(key, UNCLASSIFIED_SECTOR)


def real_sector_exposure(port_df: pd.DataFrame, loaded_data: dict) -> pd.DataFrame:
    """Sector exposure keyed by each ticker's REAL provider-reported sector
    (GICS-11-normalized), not the curated TICKER_SECTORS thematic label used
    by sector_exposure(). Same output shape as sector_exposure()."""
    if port_df.empty:
        return pd.DataFrame()
    df = port_df[["Ticker", "Market Value"]].copy()
    df["Sector"] = df["Ticker"].map(
        lambda t: _normalize_provider_sector((loaded_data.get(t) or {}).get("sector"))
    )
    return (
        df.groupby("Sector")["Market Value"]
        .sum()
        .reset_index()
        .rename(columns={"Market Value": "Value"})
        .assign(Pct=lambda d: (d["Value"] / d["Value"].sum() * 100).round(1))
        .sort_values("Pct", ascending=False)
    )


def sector_benchmark_tilt(real_sector_df: pd.DataFrame) -> pd.DataFrame:
    """Portfolio real-sector % vs. SP500_SECTOR_WEIGHTS, outer-joined so a
    benchmark sector held at 0% still shows a negative tilt. Tilt = portfolio
    pct − benchmark pct. Diagnostic only — never gates."""
    port_pcts = dict(zip(real_sector_df.get("Sector", []), real_sector_df.get("Pct", [])))
    sectors = sorted(set(port_pcts) | set(SP500_SECTOR_WEIGHTS))
    rows = [
        {
            "Sector":         s,
            "Portfolio Pct":  round(port_pcts.get(s, 0.0), 1),
            "Benchmark Pct":  round(SP500_SECTOR_WEIGHTS.get(s, 0.0), 2),
            "Tilt":           round(port_pcts.get(s, 0.0) - SP500_SECTOR_WEIGHTS.get(s, 0.0), 1),
        }
        for s in sectors
    ]
    return pd.DataFrame(rows).sort_values("Tilt", ascending=False)


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


def _to_tz_naive(s: pd.Series) -> pd.Series:
    """Drop timezone from a datetime-indexed Series so two series built from
    different providers (tz-aware vs tz-naive) can align on their dates."""
    out = s.dropna().copy()
    idx = out.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        out.index = idx.tz_localize(None)
    return out


def trim_allocation(ordered, target_dollar: float, denom: float) -> dict:
    """Greedy allocation of a sector-trim target across conviction-ordered names.

    Makes the "Trim first" list ADD UP to the card's headline directive: walk the
    already-conviction-ascending candidates and fully exit the weakest names
    first, partial-trimming the last one to land exactly on `target_dollar`. This
    only DISTRIBUTES a target the engine already computed (`excess_dollar`); it
    never reranks or gates.

    Parameters
    ----------
    ordered : conviction-ascending candidates, each a dict with `ticker`,
              `market_value`, `price` (may be None/0), `pnl_pct`.
    target_dollar : total $ to trim out of the sector (rec's `excess_dollar`).
    denom : the pp denominator the headline uses (`_gd`) so per-name pp sums to
            the headline `excess_pp`.

    Returns {rows, total_allocated, target, shortfall} where each row is
    {ticker, cut_dollar, cut_pp, shares, full, tax_dir}: `full` = exit the whole
    position; `shares` = None when price is missing; `tax_dir` in
    {"gain","loss","flat"} from `pnl_pct`; `shortfall` = target not covered by the
    supplied (scored) names (surface an honest "+$X across other names" note).
    Pure / no I/O.
    """
    rows: list[dict] = []
    try:
        target = float(target_dollar)
    except (TypeError, ValueError):
        target = 0.0
    try:
        d = float(denom)
    except (TypeError, ValueError):
        d = 0.0
    if target <= 0:
        return {"rows": [], "total_allocated": 0.0, "target": max(0.0, target), "shortfall": 0.0}

    remaining = target
    total = 0.0
    eps = 0.01  # sub-cent: treat as fully consumed / fully exited
    for c in (ordered or []):
        if remaining <= eps:
            break
        try:
            mv = float(c.get("market_value") or 0.0)
        except (TypeError, ValueError):
            mv = 0.0
        if mv <= 0:
            continue
        cut = mv if mv <= remaining else remaining
        try:
            price = float(c.get("price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        shares = round(cut / price, 2) if price > 0 else None
        try:
            pnl = float(c.get("pnl_pct"))
        except (TypeError, ValueError):
            pnl = 0.0
        tax_dir = "gain" if pnl > 0 else ("loss" if pnl < 0 else "flat")
        rows.append({
            "ticker":     c.get("ticker"),
            "cut_dollar": round(cut),
            "cut_pp":     round(cut / d * 100, 1) if d > 0 else None,
            "shares":     shares,
            "full":       cut >= mv - eps,
            "tax_dir":    tax_dir,
        })
        remaining -= cut
        total     += cut

    return {
        "rows":            rows,
        "total_allocated": round(total),
        "target":          round(target),
        "shortfall":       round(remaining) if remaining > eps else 0.0,
    }


def trailing_return(close, trading_days: int) -> float | None:
    """Trailing % price return over the last `trading_days` bars of a Close series.

    Used to show a held name's RECENT momentum (e.g. 1wk = 5, 1mo = 21) next to
    its composite on the rebalance-plan trim list — the composite already blends a
    technical pillar, but a short-window return is what makes "this name is
    performing better lately" legible. Returns None when history is too short.
    Pure / no I/O.
    """
    try:
        s = pd.Series(close).dropna()
        if len(s) < trading_days + 1:
            return None
        prev = float(s.iloc[-1 - trading_days])
        last = float(s.iloc[-1])
        if prev <= 0:
            return None
        return round((last / prev - 1.0) * 100.0, 1)
    except Exception:
        return None


def portfolio_return_series(port_df: pd.DataFrame, held_data: dict) -> pd.Series | None:
    """The book's weighted daily-RETURN Series (for correlation_to_portfolio).

    Mirrors the weighting in `risk.compute_portfolio_risk_metrics` (which computes
    the same `port_returns` internally but does not expose it): per-name daily
    returns from each holding's Close history, weighted by current Weight (%),
    renormalised to the names that actually have price history. Returns None when
    there is insufficient data. Pure / no I/O.
    """
    if port_df is None or port_df.empty or not held_data:
        return None
    series: dict[str, pd.Series] = {}
    for ticker, data in (held_data or {}).items():
        hist = data.get("df") if data.get("df") is not None else data.get("history")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            series[ticker] = _to_tz_naive(hist["Close"])
    if not series:
        return None
    prices = pd.DataFrame(series).ffill().dropna()
    if len(prices) < 10:
        return None
    daily_returns = prices.pct_change().dropna()
    weights: dict[str, float] = {}
    for _, row in port_df.iterrows():
        t = row["Ticker"]
        if t in daily_returns.columns:
            try:
                weights[t] = float(row["Weight (%)"]) / 100.0
            except (TypeError, ValueError):
                continue
    total_w = sum(weights.values())
    if total_w <= 0:
        return None
    weights = {t: w / total_w for t, w in weights.items()}
    port_returns = pd.Series(0.0, index=daily_returns.index)
    for t, w in weights.items():
        port_returns += daily_returns[t] * w
    return port_returns


def correlation_to_portfolio(
    candidate_close: pd.Series,
    port_returns: pd.Series,
    min_overlap: int = 20,
) -> float | None:
    """Pearson correlation of a candidate's daily returns to the BOOK's daily returns.

    This is the data-driven, book-relative diversification read — it supersedes
    the hardcoded, tech-heavy-assuming `_SECTOR_PROFILES` corr values for the
    rebalance card, which are wrong for a book concentrated in a non-tech sector.

    Parameters
    ----------
    candidate_close : the candidate's daily Close price Series (from its bundle `df`).
    port_returns    : the portfolio's weighted daily-RETURN Series (already returns,
                      not prices — built by the caller the same way
                      `compute_portfolio_risk_metrics` builds `port_returns`).
    min_overlap     : minimum overlapping trading days required for a stable read;
                      fewer → return None (surface "n/a", never a noisy estimate).

    Returns the correlation rounded to 2dp in [-1, 1], or None when the candidate
    price history is missing/short or the aligned overlap is below `min_overlap`.
    Pure / no I/O.
    """
    if candidate_close is None or port_returns is None:
        return None
    try:
        cand = _to_tz_naive(pd.Series(candidate_close))
        cand_ret = cand.pct_change().dropna()
        book_ret = _to_tz_naive(pd.Series(port_returns))
        joined = pd.concat([cand_ret, book_ret], axis=1, join="inner").dropna()
        if len(joined) < max(2, min_overlap):
            return None
        c = joined.iloc[:, 0].corr(joined.iloc[:, 1])
        if c is None or c != c:   # NaN guard (e.g. a flat/zero-variance series)
            return None
        return round(float(c), 2)
    except Exception:
        return None


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
            if c >= CORR_DANGER_PAIRS_THRESHOLD:
                risk_pairs.append({"t1": t1, "t2": t2, "corr": round(c, 2), "level": "danger"})
            elif c >= CORR_HIGH_PAIRS_THRESHOLD:
                risk_pairs.append({"t1": t1, "t2": t2, "corr": round(c, 2), "level": "warning"})

    avg_corr = weighted_sum / weight_sum if weight_sum else 0.0
    score = round((1 - avg_corr) / 2 * 100, 1)

    return {
        "score": score,
        "avg_correlation": round(avg_corr, 3),
        "risk_pairs": sorted(risk_pairs, key=lambda x: -x["corr"]),
    }


# ── Diversification Advisor ────────────────────────────────────────────────────

# Curated fallback roster per sector. Kept as the seed of the candidate pool
# (always unioned in FIRST so well-known names are never dropped by the scan
# cap), and as the sole source if the discovery-universe bucket is unavailable.
# Shelf life: registered in stock_analyzer/reference_shelf.py — update its as_of date when you refresh this list.
_SECTOR_CANDIDATES = {
    "Healthcare":      ["LLY", "NVO", "ABBV", "ISRG", "REGN"],
    "Energy":          ["XOM", "CVX", "COP", "OXY"],
    "Defense":         ["LMT", "RTX", "NOC", "GD"],
    "Financials":      ["JPM", "V", "MA", "GS"],
    "Clean Energy":    ["NEE", "ENPH", "FSLR", "BEP"],
    "Consumer Tech":   ["AAPL", "AMZN", "NFLX", "SHOP"],
    "AI & Cloud":      ["MSFT", "GOOGL", "META", "CRM"],
    "AI & Data":       ["PLTR", "SNOW", "MDB", "IONQ"],
    "Cybersecurity":   ["CRWD", "PANW", "NET", "ZS", "FTNT"],
    "Semiconductors":  ["NVDA", "AVGO", "AMD", "MU", "QCOM"],
    "Communications":  ["T", "VZ", "TMUS"],
    "EV & Auto":       ["TSLA", "RIVN", "LCID", "F", "GM"],
    "Enterprise Tech": ["DELL", "ORCL", "IBM", "HPE", "SAP"],
}

# Maps a Diversification-Advisor sector to the broad discovery-universe bucket
# (discovery_universe.py uses slightly different bucket labels). This widens the
# candidate pool from the fixed roster (~4) to the curated universe slice (~20)
# so the ADD card can surface a better entry the roster doesn't list — without
# the runtime risk of a live market scrape (the universe is curated, refreshed
# quarterly). A sector with no mapping falls back to its roster only.
_DIVERSIFY_TO_DISCOVERY = {
    "Healthcare":     "Healthcare & Biotech",
    "Energy":         "Energy & Materials",
    "Defense":        "Industrials & Defense",
    "Financials":     "Financials",
    "Clean Energy":   "Clean Energy & Utilities",
    "Semiconductors": "Semiconductors",
    "Communications": "Communications & Telecom",
    # EV & Auto and Enterprise Tech have no clean 1:1 discovery-universe bucket
    # (EV names are split across Mega-cap Tech/Consumer & Retail; Enterprise
    # Tech only partially overlaps Software & Cloud) — roster-only is fine,
    # diversifying_candidate_pool() falls back gracefully when unmapped.
}


def diversifying_candidate_pool(
    sector: str,
    held_tickers,
    cap: int = DIVERSIFY_SCAN_CAP,
) -> list[str]:
    """Candidate pool for a diversification ADD, drawn from the broad universe.

    Unions the curated roster (FIRST, so names like V/MA are never dropped by
    the cap) with the sector's discovery-universe bucket, removes already-held
    names, dedupes (case-insensitive, order-preserving), and caps the length so
    the caller's composite-scoring pass stays bounded. Falls back to the roster
    alone when the sector has no discovery bucket. Pure / no I/O — app.py scores
    the returned names and ranks them via `annotate_add_candidates`.
    """
    held = {str(t).upper().strip() for t in (held_tickers or [])}
    roster = _SECTOR_CANDIDATES.get(sector, [])
    bucket = DISCOVERY_UNIVERSE.get(_DIVERSIFY_TO_DISCOVERY.get(sector, ""), [])
    seen: set = set()
    pool: list[str] = []
    for t in [*roster, *bucket]:
        tu = str(t).upper().strip()
        if tu and tu not in held and tu not in seen:
            seen.add(tu)
            pool.append(tu)
    return pool[:max(0, cap)]

# How correlated each sector is to a typical tech-heavy portfolio (lower = better diversifier)
_SECTOR_PROFILES = {
    "Healthcare":      {"corr": 0.15, "why": "counter-cyclical, FDA/drug-cycle driven — moves independently of tech"},
    "Energy":          {"corr": 0.10, "why": "oil-price and geopolitics driven — near-zero correlation to semiconductors"},
    "Defense":         {"corr": 0.12, "why": "government budget driven — orthogonal to rate-sensitive tech growth stocks"},
    "Communications":  {"corr": 0.20, "why": "rate-sensitive, dividend-defensive telecom — closer to utilities than growth tech"},
    "Financials":      {"corr": 0.35, "why": "benefits when rates rise — inverse to your growth-tech book"},
    "EV & Auto":       {"corr": 0.40, "why": "rate- and commodity-cycle driven, consumer-discretionary — distinct from the software/AI cycle"},
    "Enterprise Tech": {"corr": 0.45, "why": "legacy enterprise IT/capex cycle — more value-oriented than AI/Cloud growth names"},
    "Clean Energy":    {"corr": 0.28, "why": "policy/subsidy driven — moderate diversification from pure tech"},
    "Consumer Tech":   {"corr": 0.58, "why": "still tech but consumer-facing — partial diversification"},
    "Semiconductors":  {"corr": 0.65, "why": "same AI/compute cycle as AI & Cloud / AI & Data — not a genuine diversifier"},
    "Cybersecurity":   {"corr": 0.65, "why": "SaaS/enterprise-software spend cycle — correlated with the AI & Cloud / AI & Data cluster"},
    "AI & Cloud":      {"corr": 0.72, "why": "highly correlated to existing tech — limited diversification benefit"},
    "AI & Data":       {"corr": 0.68, "why": "correlated to AI/semiconductor cycle — limited benefit if already tech-heavy"},
}

# Sectors that genuinely diversify a tech-heavy portfolio, in priority order.
# Semiconductors and Cybersecurity are deliberately excluded (see _SECTOR_PROFILES
# "why") despite having a profile — same treatment as Consumer Tech/AI & Cloud/AI & Data.
_DIVERSIFYING_SECTORS = [
    "Healthcare", "Energy", "Defense", "Communications",
    "Financials", "EV & Auto", "Enterprise Tech", "Clean Energy",
]


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
        if pct > SECTOR_REDUCE_TRIGGER:
            target_pct = SINGLE_NAME_CEILING
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
                "urgency":         "high" if pct > DIVERSIFY_REDUCE_HIGH_URGENCY_PCT else "medium",
                "sector":          sector,
                "current_pct":     round(pct, 1),
                "target_pct":      target_pct,
                "reduce_pct":      reduce_pct,
                "reduce_dollars":  round(portfolio_value * reduce_pct / 100),
                "weakest_tickers": weakest,
                "reason": (
                    f"**{sector}** is {pct:.0f}% of your portfolio — above the {SECTOR_REDUCE_TRIGGER:.0f}% sector cap. "
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
        if current_pct >= DIVERSIFY_ADD_SKIP_PCT:
            continue
        candidates = diversifying_candidate_pool(sector, held_tickers)
        if not candidates:
            continue
        profile    = _SECTOR_PROFILES.get(sector, {"corr": 0.30, "why": ""})
        target_pct = DIVERSIFY_ADD_TARGET_PCT
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


def annotate_add_candidates(
    candidates: list[str],
    quality: dict[str, dict],
    buy_gate: float = COMPOSITE_BUY,
) -> list[dict]:
    """Cross-validate diversification ADD candidates against the stock-quality engine.

    The Diversification Advisor proposes candidate names from a static per-sector
    roster — it answers "which SECTOR is underweight," not "is this NAME a good
    entry." This helper joins each candidate to the SAME composite/signal/R:R the
    Analysis page and Grow Today produce, so the rebalance card and the
    new-position engine give one consistent read instead of two disconnected ones.

    Parameters
    ----------
    candidates : the bare ticker list from a diversification ADD rec.
    quality    : {ticker: {"score": float|None, "signal": str|None, "rr": float|None}}
                 supplied by app.py from the cached load_all bundles (orchestration
                 layer owns the data fetch; this function stays pure/testable).
    buy_gate   : composite threshold a candidate must clear to be a genuine Buy.

    Returns a list of dicts {ticker, score, signal, rr, passes}, where:
      passes is True  -> scored at/above the Buy gate (a real, actionable entry)
      passes is False -> scored but below the Buy gate (diversifies, but weak entry)
      passes is None  -> no score available (couldn't load) — surfaced, not hidden

    Sorted best-first: gate-passers by composite desc, then scored-but-failing by
    composite desc, then unscored. Callers should render passers prominently and
    keep the rest visible-but-demoted (never silently filter).
    """
    out: list[dict] = []
    for t in candidates:
        q = quality.get(t) or {}
        score = q.get("score")
        passes = (score >= buy_gate) if isinstance(score, (int, float)) else None
        out.append({
            "ticker": t,
            "score":  score,
            "signal": q.get("signal"),
            "rr":     q.get("rr"),
            "passes": passes,
        })

    def _rank(c: dict) -> tuple:
        # passers (0) before scored-failers (1) before unscored (2); within a
        # tier, higher composite first.
        tier = 0 if c["passes"] is True else (1 if c["passes"] is False else 2)
        return (tier, -(c["score"] if isinstance(c["score"], (int, float)) else 0))

    out.sort(key=_rank)
    return out
