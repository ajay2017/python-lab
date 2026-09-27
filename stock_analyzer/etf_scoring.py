"""
ETF/fund composite scoring — Phase 2 of the ETF-support initiative
(`docs/plans/etf-multi-asset-support.md` §11, F-279/F-280 predecessors).

Deliberately a NEW module, not grown inside `scoring.py`/`fundamentals.py`,
so it doesn't inherit their existing `_GATE_FILES` review history — this
file itself still needs review since it IS new decision logic, but keeping
it separate matches this repo's established pattern (`outage_gate.py`,
`coord_freshness.py`).

An ETF/fund cannot be scored on the equity composite: `business_quality` and
`valuation` both score fields that don't exist for a fund (revenue growth,
margins, forward P/E, FCF yield — see the plan doc's §2, "65% of the
composite is structurally unmeasurable for an ETF"). This module gives a
fund its own decidable composite instead: a weighted blend of technical
trend + expense-ratio cost quality, using ONLY data Phase 1 already captures
on the bundle (`etf_facts`). No look-through/constituent data exists yet and
none is used here.

Policy values (`ETF_COMPOSITE_WEIGHTS`, the expense-ratio bands,
`ETF_COST_SCORE_FLOOR`, `ETF_AUM_THIN_FLOOR_USD`) all live in
`constants.py`, owner-approved before this module was written — never
hardcode a threshold here.
"""
from __future__ import annotations

from stock_analyzer.constants import (
    ETF_COMPOSITE_WEIGHTS,
    ETF_EXPENSE_RATIO_CHEAP_PCT,
    ETF_EXPENSE_RATIO_EXPENSIVE_PCT,
    ETF_COST_SCORE_FLOOR,
    ETF_AUM_THIN_FLOOR_USD,
)
from stock_analyzer.scoring import recommendation

# ETF-appropriate rationale text, keyed by the SAME label vocabulary
# scoring.recommendation() already produces (COMPOSITE_STRONG_BUY/BUY/HOLD/
# SELL boundaries — never a new threshold here). The equity rationale text
# ("solid fundamentals", "weakening technicals or fundamentals") is false for
# a fund with no fundamentals leg at all, so it's swapped for wording that
# references cost + technical trend instead.
_ETF_RATIONALE = {
    "Strong Buy": "Strong technical trend and a cost-efficient expense ratio both support this fund.",
    "Buy":        "Favorable technical trend and a reasonable expense ratio for the position.",
    "Hold":       "Mixed signal — either the technical trend is unclear, or a relatively high expense ratio offsets an otherwise favorable trend.",
    "Sell":       "Weakening technical trend, or a high expense ratio eroding the fund's return over time.",
    "Strong Sell": "Weak technical trend combined with a high-cost expense ratio.",
}


def expense_ratio_score(net_expense_ratio_pct: float | None) -> float | None:
    """0-100 cost-quality score from a fund's expense ratio (percent units,
    e.g. 0.20 == 0.20%, matching `etf_facts["net_expense_ratio"]`).

    At/below `ETF_EXPENSE_RATIO_CHEAP_PCT` -> 100 (ceiling). At/above
    `ETF_EXPENSE_RATIO_EXPENSIVE_PCT` -> `ETF_COST_SCORE_FLOOR` (a high fee
    is a real drag, not a disqualifier — floors at 25, never 0). Linear
    interpolation between the two bands. `None` input -> `None` output —
    never fabricate a cost score for a fund with no expense-ratio data.
    """
    if net_expense_ratio_pct is None:
        return None
    r = float(net_expense_ratio_pct)
    if r <= ETF_EXPENSE_RATIO_CHEAP_PCT:
        return 100.0
    if r >= ETF_EXPENSE_RATIO_EXPENSIVE_PCT:
        return float(ETF_COST_SCORE_FLOOR)
    span = ETF_EXPENSE_RATIO_EXPENSIVE_PCT - ETF_EXPENSE_RATIO_CHEAP_PCT
    frac = (r - ETF_EXPENSE_RATIO_CHEAP_PCT) / span
    return round(100.0 - frac * (100.0 - ETF_COST_SCORE_FLOOR), 1)


def etf_available(etf_facts: dict | None) -> bool:
    """Minimum bar for an ETF to get a real verdict instead of a withhold:
    `net_expense_ratio` must be known. Without it, only technicals would
    drive a verdict — the same "manufactured buy on technicals alone" risk
    this app avoids elsewhere. `etf_facts=None` or missing
    `net_expense_ratio` -> `False`.
    """
    if not etf_facts:
        return False
    return etf_facts.get("net_expense_ratio") is not None


def etf_composite(technical_score: float, cost_score: float) -> float:
    """Weighted 0-100 composite using `ETF_COMPOSITE_WEIGHTS`. Mirrors
    `scoring.combined_score`'s rounding convention exactly (round to 1
    decimal place)."""
    w = ETF_COMPOSITE_WEIGHTS
    return round(
        technical_score * w["technical"] + cost_score * w["cost"],
        1,
    )


def etf_recommendation(score: float) -> dict:
    """Reuses `scoring.recommendation(score)` for label/color/icon/threshold
    boundaries (same `COMPOSITE_STRONG_BUY`/`BUY`/`HOLD`/`SELL` vocabulary as
    equities — never a new threshold here), but replaces the equity
    rationale text (which references "solid fundamentals" — false for a
    fund) with ETF-appropriate wording that references cost and technical
    trend instead. Returns the same dict shape `scoring.recommendation`
    returns, with `rationale` swapped."""
    rec = recommendation(score)
    rec["rationale"] = _ETF_RATIONALE.get(rec["label"], rec["rationale"])
    return rec


def etf_aum_thin(total_assets: float | None) -> bool:
    """Awareness-only flag: `True` when `total_assets` is known AND below
    `ETF_AUM_THIN_FLOOR_USD`. `total_assets=None` -> `False` — never infer
    "thin" from missing data.

    This value must NEVER feed `etf_composite` and must NEVER be read by
    `risk_advisor`/`exit_advisor` — it's a display caption only (not yet
    wired to any UI as of Phase 2), same posture as the existing
    leverage/margin awareness captions.
    """
    if total_assets is None:
        return False
    return float(total_assets) < ETF_AUM_THIN_FLOOR_USD
