"""
Portfolio Construction Health Score — pure computation, no I/O, no Streamlit.

Five sub-scores (0–100 each) → weighted average → grade A–F.

Sub-scores:
  concentration   – single-name + sector weight vs hard caps
  sector_balance  – Shannon entropy of sector weights
  diversification – pre-computed avg-correlation-based diversity score
  factor_exposure – portfolio beta / fragility severity + high-beta share
  signal_integrity – % of book weight currently at or above the Buy composite threshold
"""
from __future__ import annotations

import math

import pandas as pd

from stock_analyzer.constants import (
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    PORTFOLIO_BETA_CEILING,
    PORTFOLIO_BETA_ELEVATED,
    SECTOR_CEILING,
    SECTOR_ELEVATED,
    SINGLE_NAME_CEILING,
)

# ── Grade bands ───────────────────────────────────────────────────────────────

_GRADE_BANDS: list[tuple[float, str, str]] = [
    (80, "A", "Well-constructed"),
    (65, "B", "Solid foundation"),
    (50, "C", "Needs attention"),
    (35, "D", "Structural concerns"),
    (0,  "F", "Action required"),
]

_GRADE_COLORS: dict[str, str] = {
    "A": ("#15803d", "#4ade80"),   # bg, border
    "B": ("#1d4ed8", "#60a5fa"),
    "C": "#b45309",                 # amber — single string when bg==border
    "D": ("#c2410c", "#fb923c"),
    "F": ("#b91c1c", "#f87171"),
    "?": ("#374151", "#9ca3af"),
}


def grade_colors(letter: str) -> tuple[str, str]:
    v = _GRADE_COLORS.get(letter, _GRADE_COLORS["?"])
    return (v, v) if isinstance(v, str) else v


def _grade(score: float) -> tuple[str, str]:
    for threshold, letter, label in _GRADE_BANDS:
        if score >= threshold:
            return letter, label
    return "F", "Action required"


def score_color(s: float | None) -> str:
    """CSS colour for a 0–100 sub-score."""
    if s is None:
        return "#6b7280"
    if s >= 80:
        return "#16a34a"
    if s >= 65:
        return "#2563eb"
    if s >= 50:
        return "#d97706"
    if s >= 35:
        return "#ea580c"
    return "#dc2626"


# ── Sub-score helpers ─────────────────────────────────────────────────────────

def _concentration_score(port_df: pd.DataFrame) -> dict:
    if port_df.empty:
        return {"score": None, "detail": {}}

    wt_col = "Gate Weight (%)" if "Gate Weight (%)" in port_df.columns else "Weight (%)"

    max_name_wt = float(port_df[wt_col].max())
    sector_wts = (
        port_df.groupby("Sector")[wt_col].sum()
        if "Sector" in port_df.columns
        else pd.Series(dtype=float)
    )
    max_sector_wt = float(sector_wts.max()) if not sector_wts.empty else 0.0
    worst_sector = sector_wts.idxmax() if not sector_wts.empty else None
    worst_name = (
        port_df.loc[port_df[wt_col].idxmax(), "Ticker"]
        if not port_df.empty and "Ticker" in port_df.columns
        else None
    )

    # Linear: score = 100 at 0 weight, 0 at the hard ceiling — binding constraint wins
    name_score = max(0.0, 100.0 * (1.0 - max_name_wt / SINGLE_NAME_CEILING))
    sector_score = max(0.0, 100.0 * (1.0 - max_sector_wt / SECTOR_CEILING))
    score = round(min(name_score, sector_score), 1)

    return {
        "score": score,
        "detail": {
            "max_name_wt": round(max_name_wt, 1),
            "worst_name": worst_name,
            "max_sector_wt": round(max_sector_wt, 1),
            "worst_sector": worst_sector,
            "name_ceiling": SINGLE_NAME_CEILING,
            "sector_ceiling": SECTOR_CEILING,
            "sector_elevated": SECTOR_ELEVATED,
        },
    }


def _sector_balance_score(port_df: pd.DataFrame) -> dict:
    if port_df.empty or "Sector" not in port_df.columns:
        return {"score": None, "detail": {}}

    wt_col = "Weight (%)" if "Weight (%)" in port_df.columns else "Gate Weight (%)"
    sector_wts = port_df.groupby("Sector")[wt_col].sum()
    n_sectors = int(len(sector_wts))

    if n_sectors <= 1:
        return {
            "score": 10.0,
            "detail": {"n_sectors": n_sectors, "normalized_entropy": 0.0},
        }

    props = sector_wts / sector_wts.sum()
    entropy = -sum(p * math.log2(p) for p in props if p > 0)
    max_entropy = math.log2(n_sectors)
    normalized = entropy / max_entropy if max_entropy > 0 else 0.0
    raw = normalized * 100

    # Cap score for portfolios with very few sectors regardless of balance
    caps = {2: 55}
    score = round(min(raw, caps.get(n_sectors, raw)), 1)

    return {
        "score": score,
        "detail": {
            "n_sectors": n_sectors,
            "normalized_entropy": round(normalized, 3),
            "sector_weights": sector_wts.round(1).to_dict(),
        },
    }


def _diversification_score_sub(div_score_val: float | None, avg_corr: float | None) -> dict:
    if div_score_val is None:
        return {"score": None, "detail": {}}
    return {
        "score": round(float(div_score_val), 1),
        "detail": {
            "avg_corr": round(avg_corr, 3) if avg_corr is not None else None,
        },
    }


def _factor_exposure_score(
    fragility: dict | None,
    hb_share: float | None,
    port_beta: float | None,
) -> dict:
    if fragility is None and port_beta is None:
        return {"score": None, "detail": {}}

    severity = (fragility or {}).get("severity")
    base = {"calm": 85, "caution": 55, "fragile": 20}.get(severity, 65)

    hb_penalty = 0
    if hb_share is not None:
        if hb_share >= 60:
            hb_penalty = 25
        elif hb_share >= 40:
            hb_penalty = 12

    score = round(max(0.0, min(100.0, float(base - hb_penalty))), 1)

    return {
        "score": score,
        "detail": {
            "severity": severity,
            "port_beta": round(float(port_beta), 2) if port_beta is not None else None,
            "hb_share": round(float(hb_share), 1) if hb_share is not None else None,
            "beta_elevated": PORTFOLIO_BETA_ELEVATED,
            "beta_ceiling": PORTFOLIO_BETA_CEILING,
        },
    }


def _signal_integrity_score(port_df: pd.DataFrame) -> dict:
    if port_df.empty or "Score" not in port_df.columns:
        return {"score": None, "detail": {}}

    wt_col = "Weight (%)" if "Weight (%)" in port_df.columns else "Gate Weight (%)"
    total_wt = float(port_df[wt_col].sum())
    if total_wt <= 0:
        return {"score": None, "detail": {}}

    buy_wt = float(port_df.loc[port_df["Score"] >= COMPOSITE_BUY, wt_col].sum())
    pct_buy = buy_wt / total_wt * 100

    valid = port_df.dropna(subset=["Score"])
    weighted_avg = (
        float((valid["Score"] * valid[wt_col]).sum() / valid[wt_col].sum())
        if not valid.empty
        else None
    )
    n_below_hold = int((port_df["Score"] < COMPOSITE_HOLD).sum())

    return {
        "score": round(pct_buy, 1),
        "detail": {
            "pct_buy_weight": round(pct_buy, 1),
            "weighted_avg_composite": round(weighted_avg, 1) if weighted_avg is not None else None,
            "n_below_hold": n_below_hold,
            "buy_threshold": COMPOSITE_BUY,
            "hold_floor": COMPOSITE_HOLD,
        },
    }


# ── Improvement action copy ───────────────────────────────────────────────────

_IMPROVEMENT: dict[str, dict[str, str]] = {
    "concentration": {
        "low": (
            "A position or sector is at or near its hard cap — trim before adding new "
            "exposure. Check the concentration section on Portfolio Allocation."
        ),
        "mid": (
            "Concentration is elevated. Review the largest name and sector before "
            "your next entry to avoid a hard-cap block."
        ),
    },
    "sector_balance": {
        "low": (
            "The portfolio spans too few sectors. Prioritise names from "
            "underrepresented sectors on your next Grow Today entry."
        ),
        "mid": (
            "Sector spread could be wider. When selecting from Grow Today candidates, "
            "favour names in sectors currently below 15 % weight."
        ),
    },
    "diversification": {
        "low": (
            "Correlations between holdings are high — many positions move together. "
            "Review the High-Correlation Pairs on Risk Analysis and consider names "
            "with lower pairwise correlation."
        ),
        "mid": (
            "Average correlation is moderate. Before adding the next position, "
            "check its correlation to your existing book on Risk Analysis."
        ),
    },
    "factor_exposure": {
        "low": (
            "The portfolio is fragile: high beta amplifies drawdowns in a routine "
            "pullback. Reduce high-beta exposure before the next entry, or review "
            "the exit advisor for trim candidates."
        ),
        "mid": (
            "Beta is elevated. Avoid adding high-beta names until the book "
            "settles — check the Market-Risk Posture dial on Risk Analysis."
        ),
    },
    "signal_integrity": {
        "low": (
            "Most book weight is below the Buy composite threshold — the engine no "
            "longer endorses many holdings as fresh entries. Review the exit advisor "
            "for active deterioration signals."
        ),
        "mid": (
            "A meaningful portion of the book is below the Buy threshold. Check the "
            "exit advisor for Watch or Trim candidates."
        ),
    },
}

_DIMENSION_LABELS: dict[str, str] = {
    "concentration":   "Concentration",
    "sector_balance":  "Sector Balance",
    "diversification": "Diversification",
    "factor_exposure": "Beta / Fragility",
    "signal_integrity": "Signal Integrity",
}

_DIMENSION_ICONS: dict[str, str] = {
    "concentration":   "⚖️",
    "sector_balance":  "🗂️",
    "diversification": "🔗",
    "factor_exposure": "📡",
    "signal_integrity": "🎯",
}


def _build_specific(key: str, detail: dict) -> str | None:
    """One named, specific callout line for the improvement card (HTML-safe)."""
    if not detail:
        return None
    if key == "concentration":
        max_name   = detail.get("max_name_wt", 0)
        max_sector = detail.get("max_sector_wt", 0)
        cap_name   = detail.get("name_ceiling", 15)
        cap_sector = detail.get("sector_ceiling", 35)
        name_ratio   = max_name   / cap_name   if cap_name   else 0
        sector_ratio = max_sector / cap_sector if cap_sector else 0
        parts = []
        if name_ratio >= sector_ratio and detail.get("worst_name"):
            parts.append(
                f"<strong>{detail['worst_name']}</strong> at {max_name}% "
                f"(single-name cap: {cap_name}%)"
            )
        if sector_ratio >= name_ratio and detail.get("worst_sector"):
            parts.append(
                f"<strong>{detail['worst_sector']}</strong> sector at {max_sector}% "
                f"(sector cap: {cap_sector}%)"
            )
        # Show both when both are meaningfully elevated (ratio > 0.6)
        if name_ratio > 0.6 and sector_ratio > 0.6 and len(parts) == 1:
            if detail.get("worst_name") and "worst_name" not in parts[0]:
                parts.append(
                    f"<strong>{detail['worst_name']}</strong> at {max_name}%"
                )
            elif detail.get("worst_sector") and "worst_sector" not in parts[0]:
                parts.append(
                    f"<strong>{detail['worst_sector']}</strong> sector at {max_sector}%"
                )
        return " &nbsp;·&nbsp; ".join(parts) or None
    if key == "sector_balance":
        n = detail.get("n_sectors", "?")
        weights = detail.get("sector_weights") or {}
        if weights:
            top = sorted(weights.items(), key=lambda x: -x[1])[:1]
            top_str = ", ".join(f"<strong>{s}</strong> {w:.0f}%" for s, w in top)
            return f"{n} sectors — largest: {top_str}"
        return f"{n} sector(s) represented"
    if key == "diversification":
        ac = detail.get("avg_corr")
        return f"Average pairwise correlation: <strong>{ac:.2f}</strong>" if ac is not None else None
    if key == "factor_exposure":
        sev  = detail.get("severity", "unknown").capitalize()
        beta = detail.get("port_beta")
        hbs  = detail.get("hb_share")
        parts = [f"Fragility: <strong>{sev}</strong>"]
        if beta is not None:
            parts.append(f"portfolio β <strong>{beta:.2f}</strong>")
        if hbs is not None:
            parts.append(f"<strong>{hbs:.0f}%</strong> high-β share")
        return " &nbsp;·&nbsp; ".join(parts)
    if key == "signal_integrity":
        pct     = detail.get("pct_buy_weight")
        n_below = detail.get("n_below_hold", 0)
        parts = []
        if pct is not None:
            parts.append(f"<strong>{pct:.0f}%</strong> of book weight in Buy zone (≥65)")
        if n_below > 0:
            parts.append(f"<strong>{n_below}</strong> position(s) below Hold floor (&lt;44)")
        return " &nbsp;·&nbsp; ".join(parts) or None
    return None


def _build_improvements(scored: list[tuple[str, float]], sub_scores: dict) -> list[dict]:
    actions = []
    for key, score in scored[:2]:
        bucket = "low" if score < 40 else "mid"
        text = _IMPROVEMENT.get(key, {}).get(bucket, "")
        if text:
            actions.append({
                "dimension": key,
                "label": _DIMENSION_LABELS.get(key, key),
                "score": score,
                "action": text,
                "specific": _build_specific(key, sub_scores[key].get("detail") or {}),
            })
    return actions


# A–F scale definition (exported so app.py can render the scale bar)
GRADE_SCALE: list[tuple[str, str, str, str]] = [
    ("A", "80–100", "#15803d", "#4ade80"),
    ("B", "65–79",  "#1d4ed8", "#60a5fa"),
    ("C", "50–64",  "#b45309", "#fcd34d"),
    ("D", "35–49",  "#c2410c", "#fb923c"),
    ("F", "0–34",   "#b91c1c", "#f87171"),
]


# ── Public API ────────────────────────────────────────────────────────────────

def compute_health_score(
    port_df: pd.DataFrame,
    div_score_val: float | None,
    avg_corr: float | None,
    hb_share: float | None,
    fragility: dict | None,
    port_risk: dict | None,
) -> dict:
    """
    Compute five sub-scores + overall portfolio construction grade.

    Returns:
        overall        float | None   — average of available sub-scores (0–100)
        grade          str            — "A" … "F" or "?"
        grade_label    str            — human verdict
        sub_scores     dict           — keyed by dimension name
        improvements   list[dict]     — top 1–2 priority actions
        n_available    int            — how many sub-scores had data
    """
    port_beta = (port_risk or {}).get("beta")

    sub_scores = {
        "concentration":   _concentration_score(port_df),
        "sector_balance":  _sector_balance_score(port_df),
        "diversification": _diversification_score_sub(div_score_val, avg_corr),
        "factor_exposure": _factor_exposure_score(fragility, hb_share, port_beta),
        "signal_integrity": _signal_integrity_score(port_df),
    }

    available = [(k, v["score"]) for k, v in sub_scores.items() if v["score"] is not None]
    overall = round(sum(s for _, s in available) / len(available), 1) if available else None
    grade, label = _grade(overall) if overall is not None else ("?", "Insufficient data")

    scored_asc = sorted(available, key=lambda x: x[1])
    improvements = _build_improvements(scored_asc, sub_scores)

    return {
        "overall": overall,
        "grade": grade,
        "grade_label": label,
        "sub_scores": sub_scores,
        "improvements": improvements,
        "n_available": len(available),
        "dimension_labels": _DIMENSION_LABELS,
        "dimension_icons":  _DIMENSION_ICONS,
    }
