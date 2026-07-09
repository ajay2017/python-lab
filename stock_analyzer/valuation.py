"""
Valuation pillar scoring.

Covers Forward P/E (sector-relative), FCF Yield, analyst PT upside, and analyst
consensus rating.  These metrics were previously folded into fundamentals.py; the
split gives valuation its own explicit weight in the 4-pillar composite.

Pure computation — no I/O.  Graceful degradation: absent metrics contribute 0 to
numerator AND denominator, so the score reflects only the data that is present.
"""
from __future__ import annotations
from stock_analyzer.constants import (
    VALUATION_PT_UPSIDE_STRONG, VALUATION_PT_UPSIDE_GOOD,
    VALUATION_PT_UPSIDE_MODEST, VALUATION_PT_UPSIDE_NEUTRAL,
    VALUATION_PT_UPSIDE_NEAR, VALUATION_CONSENSUS_PTS,
)
from stock_analyzer.fundamentals import _SECTOR_NORMS, _FUND_BANDS


def valuation_score(
    financials: dict,
    analyst_data: dict,
    current_price: float | None,
    sector: str = "",
) -> tuple[float, dict]:
    """
    Returns (score 0-100, signals dict). Pure computation — no I/O.

    analyst_data keys: avg_pt (float|None), consensus_label (str|None), has_coverage (bool).
    Graceful degradation: absent metrics contribute 0 to numerator AND denominator.
    """
    points = 0
    max_points = 0
    signals: dict[str, str] = {}

    norms = _SECTOR_NORMS.get(sector, _SECTOR_NORMS["_default"])

    # ── Forward P/E (sector-relative, 25 pts) ──────────────────────────────────
    pe = financials.get("forward_pe")
    if pe is not None and pe > 0:
        max_points += 25
        pe_cheap  = norms["pe_cheap"]
        pe_fair   = norms["pe_fair_hi"]
        pe_exp    = norms["pe_exp"]
        if pe < pe_cheap:
            pts, label = 25, f"Cheap vs {sector or 'peers'} (P/E {pe:.1f})"
        elif pe <= pe_fair:
            pts, label = 19, f"Fair value for {sector or 'sector'} (P/E {pe:.1f})"
        elif pe <= pe_exp:
            pts, label = 10, f"Moderately expensive for {sector or 'sector'} (P/E {pe:.1f})"
        else:
            pts, label = 2,  f"Expensive vs {sector or 'peers'} (P/E {pe:.1f})"
        points += pts
        signals["Forward P/E"] = label

    # ── FCF Yield (value signal, 20 pts) ─────────────────────────────────────
    # fcf_yield is stored as a percentage (e.g. 5.0 = 5%); use _FUND_BANDS for
    # consistent thresholds with the rest of the scoring stack.
    fcf = financials.get("fcf_yield")
    if fcf is not None:
        max_points += 20
        if fcf >= _FUND_BANDS["fcf_excel"]:
            pts, label = 20, f"Excellent — cheap per $1 FCF ({fcf:.1f}%)"
        elif fcf >= _FUND_BANDS["fcf_good"]:
            pts, label = 15, f"Good FCF yield ({fcf:.1f}%)"
        elif fcf >= _FUND_BANDS["fcf_modest"]:
            pts, label = 8,  f"Modest FCF yield ({fcf:.1f}%)"
        elif fcf >= 0:
            pts, label = 3,  f"Low FCF yield ({fcf:.1f}%)"
        else:
            pts, label = 0,  f"Negative FCF yield ({fcf:.1f}%)"
        points += pts
        signals["FCF Yield"] = label

    # ── PT Upside to consensus avg target (25 pts) ───────────────────────────
    avg_pt = analyst_data.get("avg_pt")
    # Fall back to yfinance single analyst target if no DB coverage
    if avg_pt is None:
        avg_pt = financials.get("analyst_target")
    if avg_pt is not None and current_price and current_price > 0:
        max_points += 25
        upside_pct = (avg_pt - current_price) / current_price * 100
        if upside_pct >= VALUATION_PT_UPSIDE_STRONG:
            pts, label = 25, f"Strong upside to consensus target (+{upside_pct:.0f}%)"
        elif upside_pct >= VALUATION_PT_UPSIDE_GOOD:
            pts, label = 20, f"Good upside to consensus (+{upside_pct:.0f}%)"
        elif upside_pct >= VALUATION_PT_UPSIDE_MODEST:
            pts, label = 12, f"Modest upside to consensus (+{upside_pct:.0f}%)"
        elif upside_pct >= VALUATION_PT_UPSIDE_NEUTRAL:
            pts, label = 6,  f"Near consensus target (+{upside_pct:.0f}%)"
        elif upside_pct >= VALUATION_PT_UPSIDE_NEAR:
            pts, label = 2,  f"Slightly above consensus target ({upside_pct:.0f}%)"
        else:
            pts, label = 0,  f"Above analyst target — overvalued on consensus ({upside_pct:.0f}%)"
        points += pts
        signals["PT Upside"] = label

    # ── Analyst consensus rating (30 pts) ────────────────────────────────────
    label_raw = analyst_data.get("consensus_label")
    if label_raw and analyst_data.get("has_coverage"):
        max_points += 30
        pts = VALUATION_CONSENSUS_PTS.get(label_raw, 0)
        points += pts
        signals["Analyst Consensus"] = f"{label_raw} (analyst consensus)"

    score = round((points / max_points) * 100, 1) if max_points > 0 else 50.0
    return score, signals
