"""
Decision Journal module.

Analyses the decision context attached to trades to surface:
  - Signal accuracy : when you followed signals, how often were they right?
  - Override accuracy: when you ignored signals, how did outcomes compare?
  - Costly deviations: ignored signals that led to losses (most important pattern)
  - Good overrides   : times ignoring the signal paid off
  - Lessons library  : all free-text lessons logged by the user
  - Behavioral insight: one-line summary of the dominant pattern
"""

import pandas as pd


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return default if v != v else v
    except (TypeError, ValueError):
        return default


def compute_patterns(trades_df: pd.DataFrame) -> dict:
    """
    Compute decision-quality patterns from the trades DataFrame.

    Expects columns: ticker, action, realized_pnl, signal_seen,
                     followed_signal, deviation_reason, lesson, traded_at.

    Returns a dict with pattern stats, lists, and a behavioral insight string.
    """
    empty = {
        "total_with_context": 0,
        "followed_wins": 0, "followed_losses": 0, "followed_pnl": 0.0,
        "ignored_wins":  0, "ignored_losses":  0, "ignored_pnl":  0.0,
        "signal_accuracy":    None,
        "override_accuracy":  None,
        "costly_deviations":  [],
        "good_overrides":     [],
        "lessons":            [],
        "behavioral_insight": None,
    }

    if trades_df is None or trades_df.empty:
        return empty

    # Only SELL trades have realized_pnl and meaningful decision context
    df = trades_df[trades_df["action"].str.upper() == "SELL"].copy() if "action" in trades_df.columns else trades_df.copy()

    # Normalise followed_signal: 'yes' / 'no' / None
    if "followed_signal" not in df.columns:
        return empty

    df["_followed"] = df["followed_signal"].fillna("").str.lower().str.strip()
    df["_pnl"]      = df["realized_pnl"].apply(lambda x: _f(x, None))
    df["_won"]      = df["_pnl"].apply(lambda x: x is not None and x > 0)

    with_context = df[df["_followed"].isin(["yes", "no"])]
    if with_context.empty:
        return empty

    followed = with_context[with_context["_followed"] == "yes"]
    ignored  = with_context[with_context["_followed"] == "no"]

    # ── Followed-signal stats ─────────────────────────────────────────────────
    f_wins   = int(followed["_won"].sum())
    f_losses = int((followed["_pnl"].apply(lambda x: x is not None and x < 0)).sum())
    f_pnl    = float(followed["_pnl"].dropna().sum())
    f_acc    = round(f_wins / (f_wins + f_losses) * 100, 1) if (f_wins + f_losses) > 0 else None

    # ── Ignored-signal stats ──────────────────────────────────────────────────
    i_wins   = int(ignored["_won"].sum())
    i_losses = int((ignored["_pnl"].apply(lambda x: x is not None and x < 0)).sum())
    i_pnl    = float(ignored["_pnl"].dropna().sum())
    i_acc    = round(i_wins / (i_wins + i_losses) * 100, 1) if (i_wins + i_losses) > 0 else None

    # ── Costly deviations: ignored AND lost money ─────────────────────────────
    costly = ignored[ignored["_pnl"].apply(lambda x: x is not None and x < 0)].copy()
    costly_list = []
    for _, row in costly.sort_values("_pnl").iterrows():
        costly_list.append({
            "ticker":           str(row.get("ticker", "?")),
            "signal_seen":      str(row.get("signal_seen", "") or ""),
            "deviation_reason": str(row.get("deviation_reason", "") or ""),
            "realized_pnl":     _f(row.get("realized_pnl")),
            "lesson":           str(row.get("lesson", "") or ""),
            "traded_at":        str(row.get("traded_at", ""))[:10],
        })

    # ── Good overrides: ignored AND made money ────────────────────────────────
    good = ignored[ignored["_pnl"].apply(lambda x: x is not None and x > 0)].copy()
    good_list = []
    for _, row in good.sort_values("_pnl", ascending=False).iterrows():
        good_list.append({
            "ticker":           str(row.get("ticker", "?")),
            "signal_seen":      str(row.get("signal_seen", "") or ""),
            "deviation_reason": str(row.get("deviation_reason", "") or ""),
            "realized_pnl":     _f(row.get("realized_pnl")),
            "lesson":           str(row.get("lesson", "") or ""),
            "traded_at":        str(row.get("traded_at", ""))[:10],
        })

    # ── Lessons library: all non-empty lesson strings ─────────────────────────
    lessons = [
        {
            "ticker":    str(row.get("ticker", "")),
            "text":      str(row.get("lesson", "")),
            "pnl":       _f(row.get("realized_pnl")),
            "date":      str(row.get("traded_at", ""))[:10],
            "followed":  row.get("_followed", ""),
        }
        for _, row in with_context.iterrows()
        if str(row.get("lesson", "")).strip()
    ]
    lessons.sort(key=lambda x: x["date"], reverse=True)

    # ── Behavioral insight ────────────────────────────────────────────────────
    insight = None
    if len(costly_list) >= 2:
        avg_cost = sum(c["realized_pnl"] for c in costly_list) / len(costly_list)
        insight  = (
            f"You've overridden sell signals {len(costly_list)} times with an avg loss of "
            f"${avg_cost:,.0f} per trade. Following signals would have saved "
            f"${abs(i_pnl):,.0f} in this period."
        )
    elif f_acc is not None and i_acc is not None:
        if f_acc > i_acc + 10:
            insight = (
                f"Signals are working: {f_acc:.0f}% accuracy when followed vs "
                f"{i_acc:.0f}% when overridden. Trust the system more."
            )
        elif i_acc > f_acc + 10:
            insight = (
                f"Your overrides are outperforming signals ({i_acc:.0f}% vs {f_acc:.0f}%). "
                "Your discretionary edge may be real — document your reasoning."
            )
        else:
            insight = (
                f"Signal accuracy ({f_acc:.0f}%) and override accuracy ({i_acc:.0f}%) "
                "are similar. Keep logging to build a clearer picture."
            )

    return {
        "total_with_context": len(with_context),
        "followed_wins":   f_wins,
        "followed_losses": f_losses,
        "followed_pnl":    f_pnl,
        "ignored_wins":    i_wins,
        "ignored_losses":  i_losses,
        "ignored_pnl":     i_pnl,
        "signal_accuracy":   f_acc,
        "override_accuracy": i_acc,
        "costly_deviations": costly_list,
        "good_overrides":    good_list,
        "lessons":           lessons,
        "behavioral_insight": insight,
    }
