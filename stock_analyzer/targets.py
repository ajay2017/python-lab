import pandas as pd
from stock_analyzer.indicators import atr as _atr_series
from stock_analyzer.constants import (
    TARGETS_ENTRY_ZONE_LOW_ATR_FRAC,
    TARGETS_ENTRY_ZONE_HIGH_ATR_FRAC,
    TARGETS_52W_HIGH_FALLBACK_MULT,
    TARGETS_52W_LOW_FALLBACK_MULT,
    TARGETS_SUPPORT_FALLBACK_MULT,
    TARGETS_MODEST_UPSIDE_MULT,
    TARGETS_BASE_FALLBACK_MULT,
    TARGETS_BULL_ANALYST_MULT,
    TARGETS_BULL_52W_HIGH_MULT,
    TARGETS_BULL_FLAT_MULT,
    TARGETS_BEAR_ATR_MULT,
    TARGETS_BEAR_SUPPORT_CUSHION_MULT,
    TARGETS_BEAR_52W_LOW_CUSHION_MULT,
)


def _atr_val(df: pd.DataFrame, length: int = 14) -> float:
    """14-period ATR, falling back to mean daily range if insufficient data."""
    s = _atr_series(df["High"], df["Low"], df["Close"], length).dropna()
    if not s.empty:
        return float(s.iloc[-1])
    return float((df["High"] - df["Low"]).tail(length).mean())


def support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict:
    recent = df.tail(lookback)
    high = recent["High"]
    low = recent["Low"]

    window = 5
    local_highs = high[high == high.rolling(window, center=True).max()].dropna()
    local_lows = low[low == low.rolling(window, center=True).min()].dropna()

    resistances = sorted(local_highs.tolist(), reverse=True)[:3]
    supports = sorted(local_lows.tolist())[:3]

    return {
        "resistances": [round(r, 2) for r in resistances],
        "supports": [round(s, 2) for s in supports],
        "nearest_resistance": round(resistances[0], 2) if resistances else None,
        "nearest_support": round(supports[0], 2) if supports else None,
    }


def entry_zone(current_price: float, atr_val: float) -> tuple[float, float]:
    """Ideal entry band: current price ± fraction of ATR."""
    low = round(current_price - TARGETS_ENTRY_ZONE_LOW_ATR_FRAC * atr_val, 2)
    high = round(current_price + TARGETS_ENTRY_ZONE_HIGH_ATR_FRAC * atr_val, 2)
    return low, high


def compute_price_targets(
    df: pd.DataFrame, financials: dict, current_price: float
) -> dict:
    analyst_target = financials.get("analyst_target")
    _w52h = financials.get("52_week_high")
    week52_high = _w52h if _w52h is not None else current_price * TARGETS_52W_HIGH_FALLBACK_MULT
    _w52l = financials.get("52_week_low")
    week52_low = _w52l if _w52l is not None else current_price * TARGETS_52W_LOW_FALLBACK_MULT

    sr = support_resistance(df)
    _sup = sr["nearest_support"]
    nearest_support = _sup if _sup is not None else current_price * TARGETS_SUPPORT_FALLBACK_MULT
    nearest_resistance = sr["nearest_resistance"]

    # Momentum-based upside: 6-month price trend extrapolated
    returns_6m = df["Close"].pct_change().dropna()
    monthly_drift = float(returns_6m.mean()) * 21  # ~1 month forward
    momentum_target = round(current_price * (1 + monthly_drift * 3), 2)  # 3-month projection

    # Base: analyst consensus if above current price; else use momentum/resistance
    if analyst_target and analyst_target > current_price:
        base = round(analyst_target, 2)
    else:
        # Stock has surpassed consensus — use nearest resistance or momentum
        candidates = [t for t in [nearest_resistance, momentum_target, current_price * TARGETS_MODEST_UPSIDE_MULT] if t and t > current_price]
        base = round(min(candidates) if candidates else current_price * TARGETS_BASE_FALLBACK_MULT, 2)

    # Bull: highest credible upside — extended analyst or 52w high breakout
    bull_candidates = [
        analyst_target * TARGETS_BULL_ANALYST_MULT if analyst_target else 0,
        week52_high * TARGETS_BULL_52W_HIGH_MULT,
        current_price * TARGETS_BULL_FLAT_MULT,
    ]
    # default= guards the empty case: when NO candidate exceeds current_price
    # (price at/above every projected ceiling, OR a NaN/degraded current_price),
    # max() of the empty generator would raise ValueError and crash the whole
    # load_all → "Could not load". Fall back to a modest 10% upside.
    bull = round(max((c for c in bull_candidates if c > current_price),
                     default=current_price * TARGETS_MODEST_UPSIDE_MULT), 2)

    # Bear: strongest support floor below current price.
    # ATR-based floor replaces the old flat 0.78× multiplier so that volatile
    # stocks get a deeper bear scenario and stable stocks a shallower one.
    # 6× ATR ≈ 1.5 monthly adverse moves — a meaningful but not extreme bear case.
    atr = _atr_val(df)
    atr_bear = current_price - TARGETS_BEAR_ATR_MULT * atr
    bear = round(max(nearest_support * TARGETS_BEAR_SUPPORT_CUSHION_MULT,
                      week52_low * TARGETS_BEAR_52W_LOW_CUSHION_MULT, atr_bear), 2)

    def pct(t: float) -> float:
        return round((t - current_price) / current_price * 100, 1)

    above_consensus = bool(analyst_target and analyst_target < current_price)

    return {
        "bull": bull,
        "base": base,
        "bear": bear,
        "bull_pct": pct(bull),
        "base_pct": pct(base),
        "bear_pct": pct(bear),
        "analyst_target": analyst_target,
        "above_consensus": above_consensus,
    }


def risk_reward(entry: float, stop: float, target: float) -> float:
    risk = entry - stop
    reward = target - entry
    if risk <= 0:
        return 0.0
    return round(reward / risk, 1)
