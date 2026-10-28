"""Latest thesis-review status per ticker, three-state.

``None`` = the reviews couldn't be loaded (DB down / no DB); an empty result
= checked and there are no rows. Callers pass ``db.load_thesis_reviews_or_none``
output so a failed read is never mistaken for "no reviews".
"""
from __future__ import annotations

import pandas as pd

UNCHECKED_ACT_NOTE = (
    "🩺 Thesis reviews couldn't be loaded this run, so a 🔴 Thesis broken link "
    "can't show on today's Brief cards. Check 🧠 AI Insights."
)
UNCHECKED_SUMMARY_NOTE = (
    "Thesis reviews couldn't be loaded. Reload the page, or open 🧠 AI Insights."
)


def latest_per_ticker(df: pd.DataFrame) -> pd.DataFrame:
    """Most-recent review per ticker (ticker compared upper-cased)."""
    out = df.sort_values("reviewed_at", ascending=False, kind="stable").copy()
    out["ticker"] = out["ticker"].astype(str).str.upper()
    return out.drop_duplicates(subset="ticker")


def broken_tickers(df: pd.DataFrame | None) -> set[str] | None:
    """Tickers whose latest review is BROKEN; None when reviews unavailable."""
    if df is None:
        return None
    if df.empty:
        return set()
    latest = latest_per_ticker(df)
    return set(latest.loc[latest["status"] == "BROKEN", "ticker"])


def held_summary(df: pd.DataFrame | None, held_tickers) -> dict | None:
    """Counts + worst-first names for held tickers; None when unavailable."""
    if df is None:
        return None
    if df.empty:
        return {"n_reviewed": 0, "n_weakening": 0, "n_broken": 0, "bad_names": []}
    held = {str(t).upper() for t in held_tickers}
    latest = latest_per_ticker(df)
    latest = latest[latest["ticker"].isin(held)]
    bad = latest[latest["status"].isin(["BROKEN", "WEAKENING"])]
    # "BROKEN" sorts before "WEAKENING"; stable keeps most-recent-first within.
    bad = bad.sort_values("status", kind="stable")
    return {
        "n_reviewed": len(latest),
        "n_weakening": int((latest["status"] == "WEAKENING").sum()),
        "n_broken": int((latest["status"] == "BROKEN").sum()),
        "bad_names": bad["ticker"].tolist(),
    }


def act_today_note(broken: set | None, n_act_cards: int) -> str | None:
    """Caption under the Act Today cards when the broken-thesis set is offline."""
    if broken is None and n_act_cards > 0:
        return UNCHECKED_ACT_NOTE
    return None


def awareness_note(broken: set | None, n_act_cards: int, n_aware_cards: int) -> str | None:
    """Same note under Awareness, only when Act Today showed no cards (so it
    appears exactly once on the page)."""
    if broken is None and n_act_cards == 0 and n_aware_cards > 0:
        return UNCHECKED_ACT_NOTE
    return None
