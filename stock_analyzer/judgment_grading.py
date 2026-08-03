"""
Judgment-layer grading harness — Phase 2 ("The Judge").

Grades individual WITNESSES (not the aggregate posture) against realized
outcomes, per docs/plans/judgment-layer.md's Q2 design: "grade witnesses,
let posture-correctness be a derived, secondary read." This module changes
NOTHING about any recommendation — it only produces a track record that a
future Phase 3 would read to weight witnesses by accuracy. Nothing reads
judgment_grades yet except the Judge page's own display.

Two grading classes are implemented for ACQUISITIVE dimensions (the third,
regime-match, has no wired witness yet so there's nothing to grade):
  - grade_ticker_opinion() — quality/momentum: per-ticker forward alpha vs
    SPY, reusing predictive_analytics.forward_alpha_at_horizon exactly (the
    same mechanism the Entry Timing tab already uses).
  - grade_portfolio_opinion() — dispatched for concentration/structural_risk
    but currently WITHHOLDS (returns None) for every protective dimension —
    see the note below.

**Protective dimensions (position_health, concentration, structural_risk,
leverage) are deliberately WITHHELD from grading in this pass, not graded
naively.** A first implementation sign-matched a protective opinion's signal
against forward alpha exactly like an acquisitive one — which scores a
caution/TRIM as "correct" only when the name/portfolio subsequently
underperforms, i.e. it marks the witness WRONG for every risk that correctly
didn't fire. That is the anti-caution posture inversion Q2's Gap B exists to
prevent (the app's core philosophy is "when in doubt, recommend nothing" —
grading would have systematically punished exactly that). A proper fix needs
counterfactual grading (what would the flagged exposure have done if NOT
trimmed/avoided, per-$1k) which needs data this module doesn't have access to
yet (what the user actually did in response). Caught in the Phase 2 code
review (Opus 4.8, 2026-08-03) before any biased grade was persisted — see
docs/plans/judgment-layer.md's status log. Until a counterfactual grader
exists, both grading functions return None for any protective dimension,
which callers should treat as "not yet gradeable," distinct from "not yet
matured."

Dispatch between the two grading functions is by whether the opinion's
`ticker` field is None (portfolio-wide) or a real ticker — not a hardcoded
per-dimension set — so a future witness on either grain is graded correctly
without touching this module. Callers must filter out advisory opinions
before grading (an advisory opinion, e.g. verdict_reconciliation, is never
weighted, so grading it would be wasted API cost) — that's an orchestration
concern, not this module's.

All external I/O (price/SPY history) is dependency-injected via callables the
caller supplies, mirroring forward_alpha_at_horizon's own pattern — this
module has no module-load-time provider/streamlit dependency.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable

from stock_analyzer.constants import (
    JUDGMENT_HORIZON_MOMENTUM_DAYS,
    JUDGMENT_HORIZON_QUALITY_DAYS,
    JUDGMENT_HORIZON_POSITION_HEALTH_DAYS,
    JUDGMENT_HORIZON_CONCENTRATION_DAYS,
    JUDGMENT_HORIZON_STRUCTURAL_RISK_DAYS,
)
from stock_analyzer.judgment_opinion import is_protective

HORIZON_BY_DIMENSION = {
    "momentum":        JUDGMENT_HORIZON_MOMENTUM_DAYS,
    "quality":         JUDGMENT_HORIZON_QUALITY_DAYS,
    "position_health": JUDGMENT_HORIZON_POSITION_HEALTH_DAYS,
    "concentration":   JUDGMENT_HORIZON_CONCENTRATION_DAYS,
    "structural_risk": JUDGMENT_HORIZON_STRUCTURAL_RISK_DAYS,
}


def _sign_match(realized_pct: float | None, opinion_signal: float) -> bool | None:
    """True/False when both sides have a directional claim; None when there's
    nothing to grade — a missing realized outcome (fetch failed), an exactly
    flat realized move, or a signal of exactly 0. None is never counted as
    "incorrect": track_record_summary excludes it from both N and accuracy, so
    a flat/missing outcome never silently biases a witness's score downward."""
    if realized_pct is None or opinion_signal == 0 or realized_pct == 0:
        return None
    return (realized_pct > 0) == (opinion_signal > 0)


def portfolio_value_series_from_snapshots(snapshots_df) -> dict:
    """Aggregate daily_snapshots (per-ticker shares+close_price rows) into a
    {date: total_portfolio_value} series. Pure — takes the already-loaded
    DataFrame (db.load_daily_snapshots()), does no I/O of its own."""
    if snapshots_df is None or snapshots_df.empty:
        return {}
    df = snapshots_df.copy()
    df["value"] = df["shares"].astype(float) * df["close_price"].astype(float)
    grouped = df.groupby("snapshot_date")["value"].sum()
    result = {}
    for d, v in grouped.items():
        d_parsed = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        result[d_parsed] = float(v)
    return result


def grade_ticker_opinion(
    opinion: dict,
    historical_close_fn: Callable[[str, date, date], float | None],
    spy_close_by_date: dict,
    today: date,
) -> dict | None:
    """Grade a single per-ticker opinion (currently: quality/momentum only —
    position_health is protective and WITHHELD, see module docstring).

    Returns None when: the opinion is portfolio-wide (ticker is None — wrong
    grader), its dimension is protective (no counterfactual grader exists
    yet), its dimension has no configured horizon (a future witness not yet
    wired for grading), or the horizon's target date is still in the future
    (checked BEFORE any fetch, so "not matured yet" is never confused with a
    fetch failure below). Otherwise returns a graded-row dict — realized_pct/
    correct may still be None if the horizon has passed but the forward or
    entry close couldn't be fetched (delisted/no data/transport failure);
    that's real information (a data gap), not silently dropped.
    """
    dimension = opinion["dimension"]
    horizon_days = HORIZON_BY_DIMENSION.get(dimension)
    ticker = opinion.get("ticker")
    if horizon_days is None or ticker is None or is_protective(dimension):
        return None

    signal_date = date.fromisoformat(opinion["as_of"][:10])
    from stock_analyzer.predictive_analytics import (
        _advance_trading_days, forward_alpha_at_horizon,
    )
    target_date = _advance_trading_days(signal_date, horizon_days)
    if target_date > today:
        return None

    try:
        price_at_entry = historical_close_fn(ticker, signal_date, signal_date + timedelta(days=7))
    except Exception:
        price_at_entry = None

    realized_pct = forward_alpha_at_horizon(
        ticker, signal_date, price_at_entry, horizon_days,
        spy_close_by_date, historical_close_fn,
    )

    return {
        "source": opinion["source"],
        "dimension": dimension,
        "ticker": ticker,
        "signal_date": signal_date.isoformat(),
        "horizon_days": horizon_days,
        "opinion_signal": opinion["signal"],
        "realized_pct": realized_pct,
        "correct": _sign_match(realized_pct, opinion["signal"]),
        "graded_at": datetime.now(timezone.utc).isoformat(),
    }


def grade_portfolio_opinion(
    opinion: dict,
    portfolio_value_by_date: dict,
    spy_close_by_date: dict,
    today: date,
) -> dict | None:
    """Grade a single portfolio-wide opinion.

    Currently a NO-OP for both dimensions this grain handles (concentration,
    structural_risk) — both are protective and WITHHELD pending a real
    counterfactual grader (see module docstring). Kept as a real function
    rather than deleted so the horizon/dispatch plumbing is ready once a
    counterfactual grader replaces the withhold check below; same
    maturity/failure-distinction contract as grade_ticker_opinion() otherwise.
    """
    dimension = opinion["dimension"]
    horizon_days = HORIZON_BY_DIMENSION.get(dimension)
    if horizon_days is None or opinion.get("ticker") is not None or is_protective(dimension):
        return None

    signal_date = date.fromisoformat(opinion["as_of"][:10])
    from stock_analyzer.predictive_analytics import _advance_trading_days
    target_date = _advance_trading_days(signal_date, horizon_days)
    if target_date > today:
        return None

    def _value_on_or_before(d: date):
        keys = [k for k in portfolio_value_by_date if k <= d]
        return portfolio_value_by_date[max(keys)] if keys else None

    v0 = _value_on_or_before(signal_date)
    v1 = _value_on_or_before(target_date)
    portfolio_ret_pct = (v1 - v0) / v0 * 100.0 if (v0 and v1 and v0 > 0) else None

    from stock_analyzer.recommendations_history import _spy_return_pct
    spy_ret_pct = _spy_return_pct(spy_close_by_date, signal_date, target_date)

    realized_pct = (
        round(portfolio_ret_pct - spy_ret_pct, 2)
        if portfolio_ret_pct is not None and spy_ret_pct is not None
        else None
    )

    return {
        "source": opinion["source"],
        "dimension": dimension,
        "ticker": "_PORTFOLIO",
        "signal_date": signal_date.isoformat(),
        "horizon_days": horizon_days,
        "opinion_signal": opinion["signal"],
        "realized_pct": realized_pct,
        "correct": _sign_match(realized_pct, opinion["signal"]),
        "graded_at": datetime.now(timezone.utc).isoformat(),
    }


def track_record_summary(grades_df, min_sample_n: int) -> list[dict]:
    """Roll up graded rows into a per-(source, dimension) track record.

    Rows with correct=NULL (matured but the fetch failed) are excluded from
    both N and accuracy — a data gap is neither evidence of correctness nor
    incorrectness. `sufficient_sample` marks whether N has cleared the shared
    min-sample gate (BEHAVIORAL_MIN_SAMPLE_N) — display only; Phase 3 is what
    would actually act on this by assigning weight.
    """
    if grades_df is None or grades_df.empty:
        return []
    df = grades_df[grades_df["correct"].notna()].copy()
    if df.empty:
        return []
    out = []
    for (source, dimension), g in df.groupby(["source", "dimension"]):
        n = len(g)
        n_correct = int(g["correct"].sum())
        out.append({
            "source": source,
            "dimension": dimension,
            "n": n,
            "n_correct": n_correct,
            "accuracy": n_correct / n if n else None,
            "sufficient_sample": n >= min_sample_n,
        })
    return sorted(out, key=lambda r: (r["dimension"], r["source"]))
