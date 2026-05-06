import pandas as pd
import pandas_ta as ta


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Trend
    df["SMA_20"] = ta.sma(df["Close"], length=20)
    df["SMA_50"] = ta.sma(df["Close"], length=50)
    df["EMA_20"] = ta.ema(df["Close"], length=20)

    # Momentum
    df["RSI"] = ta.rsi(df["Close"], length=14)
    macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["MACD"] = macd["MACD_12_26_9"]
        df["MACD_signal"] = macd["MACDs_12_26_9"]
        df["MACD_hist"] = macd["MACDh_12_26_9"]

    # Volatility
    bb = ta.bbands(df["Close"], length=20, std=2)
    if bb is not None:
        # pandas-ta 0.4.x uses a double-std suffix; fall back gracefully
        col_map = {c.split("_")[0]: c for c in bb.columns}
        df["BB_upper"] = bb[col_map.get("BBU", bb.columns[2])]
        df["BB_mid"]   = bb[col_map.get("BBM", bb.columns[1])]
        df["BB_lower"] = bb[col_map.get("BBL", bb.columns[0])]

    # Volume
    df["OBV"] = ta.obv(df["Close"], df["Volume"])

    return df


def technical_score(df: pd.DataFrame) -> tuple[float, dict]:
    """
    Returns a score 0–100 and a dict of signal details.
    Higher = more bullish.
    """
    signals = {}
    points = 0
    max_points = 0

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    # RSI: 30–70 neutral, <30 oversold (buy signal), >70 overbought (sell)
    rsi = latest.get("RSI")
    if pd.notna(rsi):
        max_points += 20
        if rsi < 30:
            points += 18
            signals["RSI"] = f"{rsi:.1f} — Oversold (bullish)"
        elif rsi < 45:
            points += 14
            signals["RSI"] = f"{rsi:.1f} — Below midrange (mildly bullish)"
        elif rsi < 55:
            points += 10
            signals["RSI"] = f"{rsi:.1f} — Neutral"
        elif rsi < 70:
            points += 6
            signals["RSI"] = f"{rsi:.1f} — Above midrange (mildly bearish)"
        else:
            points += 2
            signals["RSI"] = f"{rsi:.1f} — Overbought (bearish)"

    # MACD: histogram positive and growing = bullish
    macd_hist = latest.get("MACD_hist")
    prev_hist = prev.get("MACD_hist")
    if pd.notna(macd_hist) and pd.notna(prev_hist):
        max_points += 20
        if macd_hist > 0 and macd_hist > prev_hist:
            points += 20
            signals["MACD"] = "Positive and rising (bullish)"
        elif macd_hist > 0:
            points += 14
            signals["MACD"] = "Positive but declining (neutral)"
        elif macd_hist < 0 and macd_hist > prev_hist:
            points += 8
            signals["MACD"] = "Negative but improving (cautious)"
        else:
            points += 2
            signals["MACD"] = "Negative and falling (bearish)"

    # Price vs SMA 20/50 (golden/death cross territory)
    sma20 = latest.get("SMA_20")
    sma50 = latest.get("SMA_50")
    close = latest["Close"]
    if pd.notna(sma20) and pd.notna(sma50):
        max_points += 20
        if close > sma20 > sma50:
            points += 20
            signals["MA Trend"] = "Price > SMA20 > SMA50 (strong uptrend)"
        elif close > sma20:
            points += 14
            signals["MA Trend"] = "Price > SMA20 (short-term bullish)"
        elif close > sma50:
            points += 8
            signals["MA Trend"] = "Price > SMA50 only (weakening)"
        else:
            points += 2
            signals["MA Trend"] = "Price below both MAs (downtrend)"

    # Bollinger Band position
    bb_upper = latest.get("BB_upper")
    bb_lower = latest.get("BB_lower")
    bb_mid = latest.get("BB_mid")
    if pd.notna(bb_upper) and pd.notna(bb_lower) and pd.notna(bb_mid):
        max_points += 20
        band_range = bb_upper - bb_lower
        if band_range > 0:
            position = (close - bb_lower) / band_range
            if position < 0.2:
                points += 18
                signals["Bollinger"] = f"Near lower band ({position:.0%}) — potential bounce"
            elif position < 0.4:
                points += 14
                signals["Bollinger"] = f"Lower half ({position:.0%}) — mildly bullish"
            elif position < 0.6:
                points += 10
                signals["Bollinger"] = f"Mid band ({position:.0%}) — neutral"
            elif position < 0.8:
                points += 6
                signals["Bollinger"] = f"Upper half ({position:.0%}) — mildly bearish"
            else:
                points += 2
                signals["Bollinger"] = f"Near upper band ({position:.0%}) — potential reversal"

    # Volume trend: recent 5-day avg vs 20-day avg
    if "Volume" in df.columns:
        max_points += 20
        vol_recent = df["Volume"].iloc[-5:].mean()
        vol_avg = df["Volume"].iloc[-20:].mean()
        if vol_avg > 0:
            vol_ratio = vol_recent / vol_avg
            if vol_ratio > 1.5:
                points += 20
                signals["Volume"] = f"{vol_ratio:.1f}x avg — strong interest"
            elif vol_ratio > 1.1:
                points += 14
                signals["Volume"] = f"{vol_ratio:.1f}x avg — above average"
            elif vol_ratio > 0.9:
                points += 10
                signals["Volume"] = f"{vol_ratio:.1f}x avg — normal"
            else:
                points += 5
                signals["Volume"] = f"{vol_ratio:.1f}x avg — low interest"

    score = (points / max_points * 100) if max_points > 0 else 50
    return round(score, 1), signals
