"""
Investor Mirror — conviction alignment and behavioral bias analytics (F-194).

Two lenses on the same question — do your actions match your beliefs?

  1. Conviction Alignment  — does portfolio weighting track composite scores?
     Uses Spearman rank correlation; identifies Orphan Conviction,
     Accidental Overexposure, and Legacy Overhang patterns.

  2. Behavioral Biases     — disposition effect, breakeven anchoring,
     and win/loss closure ratio, derived from the closed-lot trade history.

Pure computation module. No Streamlit, no DB, no yfinance calls.
All inputs are passed as parameters; callers own caching and data loading.
"""

import pandas as pd
from typing import Optional

from stock_analyzer.constants import (
    COMPOSITE_STRONG_BUY,
    CONVICTION_WEAK_SCORE,
    CONVICTION_FADED_SCORE,
    CONVICTION_LEGACY_TOP_N,
    BREAKEVEN_ANCHOR_DWELL_RATIO,
    PREMATURE_EXIT_RATIO,
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_float(val, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


def _weighted_avg(values: pd.Series, weights: pd.Series) -> float | None:
    total_w = weights.sum()
    if total_w <= 0:
        return None
    return float((values * weights).sum() / total_w)


# ── FIFO closed-lot builder ───────────────────────────────────────────────────

def build_closed_lots(trades_df: pd.DataFrame) -> pd.DataFrame:
    """FIFO-match BUY lots to SELL transactions across the full trade journal.

    Adapts the lot-replay logic from tax_advisor._build_open_lots, extended to
    track buy_price per lot so P&L can be computed per matched fragment.

    SPLIT handling: the shares column of a SPLIT row holds the NEW total shares
    post-split (same encoding as tax_advisor). Each open lot's share count is
    scaled by (new_total / old_total); its buy_price is divided by the same
    ratio so the per-lot cost basis stays accurate.

    Returns a DataFrame with one row per matched lot fragment:
        ticker, buy_date, sell_date, shares, buy_price, sell_price,
        days_held, pnl_pct, pnl_abs, is_gain

    Returns an empty DataFrame if no completed round-trips exist or inputs
    are invalid.
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame()

    df = trades_df.copy()
    df["_ts"] = pd.to_datetime(
        df["traded_at"], errors="coerce", utc=True, format="ISO8601"
    )
    df = df.dropna(subset=["_ts"]).sort_values(["_ts", "id"], ascending=True)

    rows: list[dict] = []

    for ticker, grp in df.groupby(df["ticker"].astype(str).str.upper()):
        # Each open lot: mutable [shares_remaining, buy_date, buy_price_per_share]
        open_lots: list[list] = []

        for _, r in grp.iterrows():
            action = str(r.get("action", "")).upper()
            try:
                sh = float(r.get("shares") or 0)
            except (TypeError, ValueError):
                sh = 0.0
            if sh <= 0:
                continue

            d = r["_ts"].date()

            if "SPLIT" in action:
                old_total = sum(lot[0] for lot in open_lots)
                if old_total > 1e-6:
                    ratio = sh / old_total
                    for lot in open_lots:
                        lot[0] *= ratio
                        if ratio > 0:
                            lot[2] = lot[2] / ratio   # cost per share falls

            elif "BUY" in action:
                price = _safe_float(r.get("price"))
                if price > 0:
                    open_lots.append([sh, d, price])

            elif "SELL" in action:
                sell_price = _safe_float(r.get("price"))
                remaining  = sh

                while remaining > 1e-6 and open_lots:
                    lot_shares, buy_date, buy_price = open_lots[0]
                    matched = min(lot_shares, remaining)

                    if buy_price > 0 and sell_price > 0:
                        pnl_pct = (sell_price - buy_price) / buy_price * 100.0
                        pnl_abs = (sell_price - buy_price) * matched
                    else:
                        pnl_pct = None
                        pnl_abs = None

                    rows.append({
                        "ticker":     ticker,
                        "buy_date":   buy_date,
                        "sell_date":  d,
                        "shares":     matched,
                        "buy_price":  buy_price,
                        "sell_price": sell_price,
                        "days_held":  (d - buy_date).days,
                        "pnl_pct":    pnl_pct,
                        "pnl_abs":    pnl_abs,
                        "is_gain":    (pnl_abs or 0.0) >= 0,
                    })

                    if lot_shares <= remaining + 1e-6:
                        remaining -= lot_shares
                        open_lots.pop(0)
                    else:
                        open_lots[0][0] -= remaining
                        remaining = 0.0

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── Behavioral bias functions ─────────────────────────────────────────────────

def disposition_effect(
    closed_lots: pd.DataFrame,
    min_n: int,
) -> Optional[dict]:
    """Measure the disposition effect: are losing positions held longer than winners?

    Computes share-weighted average holding period for each group.
    Returns None when either group is below min_n (insufficient data).

    Return keys: winner_avg_days, loser_avg_days, ratio (loser/winner),
                 n_winners, n_losers.
    """
    if closed_lots is None or closed_lots.empty:
        return None

    valid = closed_lots.dropna(subset=["pnl_abs", "days_held", "shares"])
    if valid.empty:
        return None

    winners = valid[valid["pnl_abs"] >= 0]
    losers  = valid[valid["pnl_abs"] <  0]

    if len(winners) < min_n or len(losers) < min_n:
        return None

    w_avg = _weighted_avg(winners["days_held"], winners["shares"])
    l_avg = _weighted_avg(losers["days_held"],  losers["shares"])

    if w_avg is None or l_avg is None:
        return None

    ratio = (l_avg / w_avg) if w_avg > 0 else None

    return {
        "winner_avg_days": round(w_avg, 1),
        "loser_avg_days":  round(l_avg, 1),
        "ratio":           round(ratio, 2) if ratio is not None else None,
        "n_winners":       len(winners),
        "n_losers":        len(losers),
    }


def win_loss_closure_ratio(
    closed_lots: pd.DataFrame,
    min_n: int,
) -> Optional[dict]:
    """How many gain-realizing sell transactions per loss-realizing sell?

    Groups matched lots by (ticker, sell_date) to get the net outcome of each
    SELL transaction — a single sell may span multiple buy lots with mixed P&L,
    so the transaction-level net is the correct unit.

    Returns None when n_loss_tx < min_n.

    Return keys: n_gain_tx, n_loss_tx, ratio (gain/loss).
    """
    if closed_lots is None or closed_lots.empty:
        return None

    valid = closed_lots.dropna(subset=["pnl_abs"])
    if valid.empty:
        return None

    tx = (
        valid.groupby(["ticker", "sell_date"], as_index=False)["pnl_abs"]
        .sum()
    )

    n_gain = int((tx["pnl_abs"] >= 0).sum())
    n_loss = int((tx["pnl_abs"] <  0).sum())

    if n_loss < min_n:
        return None

    ratio = (n_gain / n_loss) if n_loss > 0 else None

    return {
        "n_gain_tx": n_gain,
        "n_loss_tx": n_loss,
        "ratio":     round(ratio, 2) if ratio is not None else None,
    }


def breakeven_anchoring(
    closed_lots: pd.DataFrame,
    min_n: int,
) -> Optional[dict]:
    """Detect breakeven anchoring: do positions dwell unusually long near 0%?

    Buckets closed lots by P&L % at exit into 10 fixed brackets (loss → gain).
    Computes share-weighted average holding period per bracket.

    Anchoring flag: the '-2 to 0%' bracket's avg_days ≥ 1.3× the mean of
    adjacent loss brackets ('-5 to -2%' and '-10 to -5%').

    Returns None if total valid lots < min_n.

    Return keys:
        brackets         — list of {bracket_label, avg_days, n_lots}, ordered
                           from most-negative to most-positive bracket
        anchoring_flagged — True when the breakeven zone shows abnormal dwell
    """
    if closed_lots is None or closed_lots.empty:
        return None

    valid = closed_lots.dropna(subset=["pnl_pct", "days_held", "shares"])
    if len(valid) < min_n:
        return None

    # Presentation-only histogram bins — these are display ranges, not decision
    # thresholds. Do not move to constants.py.  (label, lo_inclusive, hi_exclusive)
    # None boundaries = open-ended.
    _BRACKETS = [
        ("< -20%",       None,  -20.0),
        ("-20 to -10%",  -20.0, -10.0),
        ("-10 to -5%",   -10.0,  -5.0),
        ("-5 to -2%",     -5.0,  -2.0),
        ("-2 to 0%",      -2.0,   0.0),
        ("0 to +2%",       0.0,   2.0),
        ("+2 to +5%",      2.0,   5.0),
        ("+5 to +10%",     5.0,  10.0),
        ("+10 to +20%",   10.0,  20.0),
        ("> +20%",        20.0,  None),
    ]

    def _label(pnl: float) -> str:
        for lbl, lo, hi in _BRACKETS:
            below_hi = (hi is None) or (pnl < hi)
            above_lo = (lo is None) or (pnl >= lo)
            if above_lo and below_hi:
                return lbl
        return "> +20%"

    df = valid.copy()
    df["_bracket"] = df["pnl_pct"].apply(_label)

    result: list[dict] = []
    for lbl, _, _ in _BRACKETS:
        sub = df[df["_bracket"] == lbl]
        avg_days = _weighted_avg(sub["days_held"], sub["shares"]) if not sub.empty else None
        result.append({
            "bracket_label": lbl,
            "avg_days":      round(avg_days, 1) if avg_days is not None else None,
            "n_lots":        len(sub),
        })

    # Anchoring flag: breakeven bracket dwell vs adjacent loss brackets
    bkv = next((r for r in result if r["bracket_label"] == "-2 to 0%"),   None)
    adj = [
        r for r in result
        if r["bracket_label"] in ("-5 to -2%", "-10 to -5%")
        and r["avg_days"] is not None
    ]

    anchoring_flagged = False
    if bkv and bkv["avg_days"] and adj:
        adj_mean = sum(r["avg_days"] for r in adj) / len(adj)
        if adj_mean > 0 and bkv["avg_days"] >= BREAKEVEN_ANCHOR_DWELL_RATIO * adj_mean:
            anchoring_flagged = True

    return {
        "brackets":          result,
        "anchoring_flagged": anchoring_flagged,
    }


# ── Conviction alignment ──────────────────────────────────────────────────────

def conviction_alignment(
    port_df: pd.DataFrame,
    min_positions: int,
) -> Optional[dict]:
    """Measure how well portfolio weights align with composite conviction scores.

    Uses pandas Spearman rank correlation (no scipy dependency).

    Misalignment patterns surfaced (thresholds from F-194 constants):
      Orphan Conviction       — Score ≥ COMPOSITE_STRONG_BUY AND Weight < median
                                (believes in it, hasn't backed it)
      Accidental Overexposure — Score < CONVICTION_WEAK_SCORE AND Weight > median
                                (carrying risk on a weak conviction)
      Legacy Overhang         — top-CONVICTION_LEGACY_TOP_N by weight AND
                                Score < CONVICTION_FADED_SCORE (large position
                                whose conviction has faded or grew through price)

    Returns None if fewer than min_positions have both a valid Score and Weight.

    Return keys:
        spearman_rho             — float in [-1, 1]; higher = better aligned
        n_positions              — number of positions included
        orphan_convictions       — list of {Ticker, Score, Weight (%)}
        accidental_overexposures — list of {Ticker, Score, Weight (%)}
        legacy_overhangs         — list of {Ticker, Score, Weight (%)}
    """
    if port_df is None or port_df.empty:
        return None

    if not {"Ticker", "Score", "Weight (%)"}.issubset(port_df.columns):
        return None

    df = port_df[["Ticker", "Score", "Weight (%)"]].copy()
    df["Score"]      = pd.to_numeric(df["Score"],      errors="coerce")
    df["Weight (%)"] = pd.to_numeric(df["Weight (%)"], errors="coerce")
    df = df.dropna(subset=["Score", "Weight (%)"])

    if len(df) < min_positions:
        return None

    # Spearman = Pearson on ranks. Avoid method="spearman" — pandas delegates
    # that to scipy.stats.spearmanr which is not installed on Streamlit Cloud.
    rho = float(df["Score"].rank().corr(df["Weight (%)"].rank(), method="pearson"))

    median_w  = float(df["Weight (%)"].median())
    top3      = set(df.nlargest(CONVICTION_LEGACY_TOP_N, "Weight (%)")["Ticker"].tolist())

    orphans = df[
        (df["Score"] >= COMPOSITE_STRONG_BUY) & (df["Weight (%)"] < median_w)
    ][["Ticker", "Score", "Weight (%)"]].to_dict("records")

    overexposed = df[
        (df["Score"] < CONVICTION_WEAK_SCORE) & (df["Weight (%)"] > median_w)
    ][["Ticker", "Score", "Weight (%)"]].to_dict("records")

    overhangs = df[
        df["Ticker"].isin(top3) & (df["Score"] < CONVICTION_FADED_SCORE)
    ][["Ticker", "Score", "Weight (%)"]].to_dict("records")

    return {
        "spearman_rho":             round(rho, 3),
        "n_positions":              len(df),
        "orphan_convictions":       orphans,
        "accidental_overexposures": overexposed,
        "legacy_overhangs":         overhangs,
    }


# ── Sizing Alpha (O5, Agentic Intelligence Roadmap v2) ────────────────────────

def sizing_alpha(closed_lots: pd.DataFrame, min_n: int) -> Optional[dict]:
    """Does your own dollar-sizing track your own realized outcomes?

    "Conviction at time of buy" is not reliably recorded anywhere in this app's
    schema (verified during design: recommendations.conviction only covers
    same-day engine surfacings; decision_context captures portfolio context,
    not the traded ticker's own score) — so this uses the one thing that IS
    reliably recorded for every trade: the dollar size actually committed.

    Groups closed-lot fragments by their ORIGINATING BUY LOT
    (ticker, buy_date, buy_price) — not by sell fragment — so a single large
    position later scaled out in several pieces is sized and scored once, not
    shredded into several small-dollar fragments that would bias the result.

    Splits buy lots into dollar-size terciles via a rank-based split (robust to
    many identically-sized buys, e.g. round-dollar purchases) and returns each
    tercile's dollar-weighted average realized outcome. Descriptive only — never
    a portfolio-%-adjusted figure (no point-in-time portfolio-value history
    exists to normalize by) and never a forecast.

    Returns None if fewer than 3 distinct buy-lot dollar sizes exist, or if any
    tercile has fewer than min_n buy lots.

    Return keys:
        terciles — list of {label, n_lots, dollar_lo, dollar_hi, avg_pnl_pct},
                   ordered Small -> Medium -> Large
    """
    if closed_lots is None or closed_lots.empty:
        return None

    valid = closed_lots.dropna(subset=["shares", "buy_price", "pnl_pct"])
    valid = valid[(valid["shares"] > 0) & (valid["buy_price"] > 0)]
    if valid.empty:
        return None

    df = valid.copy()
    df["_frag_dollars"] = df["shares"] * df["buy_price"]

    lots = []
    for (ticker, buy_date, buy_price), grp in df.groupby(["ticker", "buy_date", "buy_price"]):
        total_shares = float(grp["shares"].sum())
        dollar_size = buy_price * total_shares
        outcome = _weighted_avg(grp["pnl_pct"], grp["_frag_dollars"])
        if outcome is None:
            continue
        lots.append({"dollar_size": dollar_size, "pnl_pct": outcome})

    if len(lots) < 3:
        return None

    lots_df = pd.DataFrame(lots)
    if lots_df["dollar_size"].nunique() < 3:
        return None

    ranks = lots_df["dollar_size"].rank(method="first")
    try:
        lots_df["_tercile"] = pd.qcut(ranks, 3, labels=["Small", "Medium", "Large"])
    except ValueError:
        return None

    terciles = []
    for label in ["Small", "Medium", "Large"]:
        sub = lots_df[lots_df["_tercile"] == label]
        if len(sub) < min_n:
            return None
        avg_pnl = _weighted_avg(sub["pnl_pct"], sub["dollar_size"])
        terciles.append({
            "label":      label,
            "n_lots":     len(sub),
            "dollar_lo":  round(float(sub["dollar_size"].min()), 0),
            "dollar_hi":  round(float(sub["dollar_size"].max()), 0),
            "avg_pnl_pct": round(avg_pnl, 2) if avg_pnl is not None else None,
        })

    return {"terciles": terciles}


# ── Premature-Exit Cost (O6, Agentic Intelligence Roadmap v2) ─────────────────

def premature_exit_cost(closed_lots: pd.DataFrame, min_n: int) -> Optional[dict]:
    """Do winners sold quickly (relative to your OWN average winner-hold) realize
    less gain than winners you held longer?

    Never estimates a counterfactual dollar "cost" (that would require forecasting
    a hypothetical later price — a fabrication risk this app forbids). Reports only
    a real, already-realized comparison: share-weighted average pnl_pct for a
    "quick exit" bucket (held < PREMATURE_EXIT_RATIO x your own average winner
    hold) vs. a "patient" bucket (the rest).

    Filters on is_gain == True AND a non-null pnl_pct explicitly — is_gain alone
    is insufficient, since build_closed_lots's `is_gain = (pnl_abs or 0.0) >= 0`
    evaluates True when pnl_abs is None (missing price data).

    Computes its own average-winner-hold rather than reusing disposition_effect()'s
    return, since that function requires BOTH a populated winner AND loser group
    to return anything — it would silently withhold winner_avg_days whenever losers
    are thin, even with plenty of winners.

    Returns None if either bucket has fewer than min_n lots, or if closed_lots is
    empty/has no valid winners.

    Return keys:
        avg_winner_days — the computed split-point (share-weighted mean)
        quick  — {n_lots, avg_pnl_pct}
        patient — {n_lots, avg_pnl_pct}
    """
    if closed_lots is None or closed_lots.empty:
        return None

    valid = closed_lots.dropna(subset=["pnl_pct", "days_held", "shares", "pnl_abs"])
    winners = valid[valid["is_gain"].astype(bool)]
    if winners.empty:
        return None

    avg_winner_days = _weighted_avg(winners["days_held"], winners["shares"])
    if avg_winner_days is None or avg_winner_days <= 0:
        return None

    split_point = PREMATURE_EXIT_RATIO * avg_winner_days
    quick   = winners[winners["days_held"] <  split_point]
    patient = winners[winners["days_held"] >= split_point]

    if len(quick) < min_n or len(patient) < min_n:
        return None

    quick_avg   = _weighted_avg(quick["pnl_pct"],   quick["shares"])
    patient_avg = _weighted_avg(patient["pnl_pct"], patient["shares"])
    if quick_avg is None or patient_avg is None:
        return None

    return {
        "avg_winner_days": round(avg_winner_days, 1),
        "quick":   {"n_lots": len(quick),   "avg_pnl_pct": round(quick_avg, 2)},
        "patient": {"n_lots": len(patient), "avg_pnl_pct": round(patient_avg, 2)},
    }
