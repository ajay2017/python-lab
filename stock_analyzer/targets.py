import pandas as pd


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
    low = round(current_price - 0.25 * atr_val, 2)
    high = round(current_price + 0.10 * atr_val, 2)
    return low, high


def compute_price_targets(
    df: pd.DataFrame, financials: dict, current_price: float
) -> dict:
    analyst_target = financials.get("analyst_target")
    week52_high = financials.get("52_week_high") or current_price * 1.3
    week52_low = financials.get("52_week_low") or current_price * 0.7

    sr = support_resistance(df)
    nearest_support = sr["nearest_support"] or current_price * 0.88
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
        candidates = [t for t in [nearest_resistance, momentum_target, current_price * 1.10] if t and t > current_price]
        base = round(min(candidates) if candidates else current_price * 1.08, 2)

    # Bull: highest credible upside — extended analyst or 52w high breakout
    bull_candidates = [
        analyst_target * 1.20 if analyst_target else 0,
        week52_high * 1.12,
        current_price * 1.25,
    ]
    bull = round(max(c for c in bull_candidates if c > current_price), 2)

    # Bear: strongest support floor below current price
    bear = round(max(nearest_support * 0.98, week52_low * 1.03, current_price * 0.78), 2)

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
