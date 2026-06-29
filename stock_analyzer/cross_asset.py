"""Cross-asset regime signals — macro stress pulse for the Risk tab."""

from __future__ import annotations

import concurrent.futures
import numpy as np
import pandas as pd
import yfinance as yf

from stock_analyzer.constants import (
    CROSS_ASSET_HYG_TREND_DAYS,
    CROSS_ASSET_COPPER_TREND_DAYS,
    CROSS_ASSET_DXY_TREND_DAYS,
    CROSS_ASSET_DXY_ROC_DAYS,
    CROSS_ASSET_DXY_ROC_THRESHOLD,
    CROSS_ASSET_VIX_TERM_RATIO,
    CROSS_ASSET_CURVE_STRESS_BP,
)

_TICKERS = ["HYG", "^VIX", "^VIX3M", "DX-Y.NYB", "HG=F", "^IRX", "^TNX"]

_SCORE_LABELS = {0: "Calm", 1: "Calm", 2: "Caution", 3: "Stress", 4: "Stress", 5: "Alarm"}

_SIGNAL_NAMES = {
    "credit":   "credit spreads (HYG)",
    "vix_term": "VIX structure inverted",
    "dollar":   "dollar strength",
    "copper":   "copper weakening",
    "curve":    "3m10y spread inverted",
}


def _unavailable() -> dict:
    return {"stressed": False, "label": "—", "detail": "data unavailable", "available": False}


def _slope(series: pd.Series) -> float:
    x = np.arange(len(series))
    return float(np.polyfit(x, series.to_numpy(dtype=float), 1)[0])


def _close(data: dict, ticker: str) -> pd.Series | None:
    df = data.get(ticker)
    if df is None or df.empty or "Close" not in df.columns:
        return None
    s = df["Close"].dropna()
    return s if not s.empty else None


def fetch_cross_asset_data() -> dict[str, pd.DataFrame]:
    """Download 45 days of daily history for all cross-asset tickers."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
            _fut = _ex.submit(
                yf.download,
                _TICKERS,
                period="45d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )
            raw = _fut.result(timeout=20)
    except Exception:
        return {}

    result: dict[str, pd.DataFrame] = {}
    # yfinance multi-ticker download returns a MultiIndex column (ticker, field);
    # extract each ticker's slice into its own single-level DataFrame.
    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in _TICKERS:
            try:
                df = raw[ticker].copy()
                df.columns = [c if isinstance(c, str) else c[0] for c in df.columns]
                close_col = df.get("Close") if "Close" in df.columns else None
                if close_col is None or close_col.dropna().empty:
                    result[ticker] = pd.DataFrame()
                else:
                    result[ticker] = df
            except Exception:
                result[ticker] = pd.DataFrame()
    else:
        # Single-ticker fallback — yfinance returned a flat DataFrame (only one
        # ticker responded). We cannot identify which ticker it belongs to from
        # shape alone, so treat all as unavailable rather than silently assign
        # wrong-asset data.
        for ticker in _TICKERS:
            result[ticker] = pd.DataFrame()

    return result


def compute_cross_asset_signals(data: dict[str, pd.DataFrame]) -> dict:
    """Pure function: score each cross-asset signal and return the aggregate dict."""

    signals: dict[str, dict] = {}

    # ── Credit spreads (HYG) ─────────────────────────────────────────────────
    hyg = _close(data, "HYG")
    if hyg is None or len(hyg) < CROSS_ASSET_HYG_TREND_DAYS:
        signals["credit"] = _unavailable()
    else:
        series = hyg.iloc[-CROSS_ASSET_HYG_TREND_DAYS:]
        slope = _slope(series)
        _base = float(series.iloc[0])
        pct_per_day = (slope / _base * 100) if _base != 0 else 0.0
        stressed = slope < 0
        signals["credit"] = {
            "stressed": stressed,
            "label": "HYG declining — credit stress" if stressed else "HYG trend flat/rising",
            "detail": f"{CROSS_ASSET_HYG_TREND_DAYS}-day trend: {pct_per_day:+.2f}%/day",
            "available": True,
        }

    # ── VIX term structure (^VIX / ^VIX3M) ──────────────────────────────────
    vix_s  = _close(data, "^VIX")
    vix3m_s = _close(data, "^VIX3M")
    if vix_s is None or vix3m_s is None:
        signals["vix_term"] = _unavailable()
    else:
        vix_val  = float(vix_s.iloc[-1])
        vix3m_val = float(vix3m_s.iloc[-1])
        if vix3m_val == 0:
            signals["vix_term"] = _unavailable()
        else:
            ratio = vix_val / vix3m_val
            stressed = ratio > CROSS_ASSET_VIX_TERM_RATIO
            signals["vix_term"] = {
                "stressed": stressed,
                "label": "VIX > VIX3M — term structure inverted" if stressed else "VIX term structure normal",
                "detail": f"VIX/VIX3M = {ratio:.2f}",
                "available": True,
            }

    # ── Dollar strength (DX-Y.NYB) ───────────────────────────────────────────
    dxy = _close(data, "DX-Y.NYB")
    roc_needed = CROSS_ASSET_DXY_ROC_DAYS + 1  # need close[-1] and close[-(roc_days+1)]
    if dxy is None or len(dxy) < max(CROSS_ASSET_DXY_TREND_DAYS, roc_needed):
        signals["dollar"] = _unavailable()
    else:
        trend_series = dxy.iloc[-CROSS_ASSET_DXY_TREND_DAYS:]
        slope = _slope(trend_series)
        roc = (float(dxy.iloc[-1]) / float(dxy.iloc[-(CROSS_ASSET_DXY_ROC_DAYS + 1)]) - 1) * 100
        stressed = (slope > 0) and (roc > CROSS_ASSET_DXY_ROC_THRESHOLD)
        signals["dollar"] = {
            "stressed": stressed,
            "label": "Dollar rising rapidly — stress" if stressed else "Dollar trend contained",
            "detail": f"{CROSS_ASSET_DXY_ROC_DAYS}-day ROC: {roc:+.2f}%",
            "available": True,
        }

    # ── Copper / growth proxy (HG=F) ─────────────────────────────────────────
    copper = _close(data, "HG=F")
    if copper is None or len(copper) < CROSS_ASSET_COPPER_TREND_DAYS:
        signals["copper"] = _unavailable()
    else:
        series = copper.iloc[-CROSS_ASSET_COPPER_TREND_DAYS:]
        slope = _slope(series)
        _base = float(series.iloc[0])
        pct_per_day = (slope / _base * 100) if _base != 0 else 0.0
        stressed = slope < 0
        signals["copper"] = {
            "stressed": stressed,
            "label": "Copper declining — growth concern" if stressed else "Copper trend flat/rising",
            "detail": f"{CROSS_ASSET_COPPER_TREND_DAYS}-day trend: {pct_per_day:+.2f}%/day",
            "available": True,
        }

    # ── Yield curve (^TNX − ^IRX, in basis points) ──────────────────────────
    tnx_s = _close(data, "^TNX")
    irx_s = _close(data, "^IRX")
    if tnx_s is None or irx_s is None:
        signals["curve"] = _unavailable()
    else:
        tnx = float(tnx_s.iloc[-1])
        irx = float(irx_s.iloc[-1])
        spread_bp = (tnx - irx) * 100
        stressed = spread_bp < CROSS_ASSET_CURVE_STRESS_BP
        signals["curve"] = {
            "stressed": stressed,
            "label": "3m10y deeply inverted — recession watch" if stressed else "3m10y spread normal/flat",
            "detail": f"3m10y spread {spread_bp:+.0f} bp",
            "available": True,
        }

    # ── Aggregate score ───────────────────────────────────────────────────────
    available_signals = [k for k, v in signals.items() if v["available"]]
    stressed_signals  = [k for k in available_signals if signals[k]["stressed"]]
    score = len(stressed_signals)

    # Total data outage — never fabricate a calm read when nothing is known.
    if not available_signals:
        return {
            **signals,
            "score":   0,
            "label":   "—",
            "summary": "Cross-asset signals unavailable — market data offline.",
        }

    # Clamp to the label map boundaries in case future signals expand beyond 5.
    label_key = min(score, 5)
    summary_label = _SCORE_LABELS.get(label_key, "Stress")

    if score == 0:
        summary = "All available cross-asset signals calm."
    else:
        named = ", ".join(_SIGNAL_NAMES.get(k, k) for k in stressed_signals)
        total = len(available_signals)
        summary = f"{score} of {total} cross-asset signals showing stress ({named})."

    return {
        **signals,
        "score":   score,
        "label":   summary_label,
        "summary": summary,
    }
