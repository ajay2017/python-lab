import pandas as pd
from stock_analyzer.indicators import sma, ema, rsi, macd, bbands, obv


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Drop bars with no Close before computing anything. A degraded/partial
    # trailing bar (a market-holiday or mid-outage row) carries a NaN Close;
    # left in, it does two kinds of silent damage: (a) df["Close"].iloc[-1]
    # becomes NaN — which is *truthy* in Python, so it sails past every
    # `if price:` guard downstream and sprays "$nan" through the Trade Plan;
    # and (b) it NaN-poisons that row's RSI/SMA/MACD/Bollinger, so
    # technical_score (which reads df.iloc[-1] behind pd.notna gates) silently
    # computes on a reduced signal set and distorts the verdict. A bar without
    # a Close is not a tradeable bar. Mirrors the same notna() filter applied on
    # the cached path in db.load_bundle_cache (single source of truth for the
    # rule: no Close → not a bar).
    if "Close" in df.columns:
        df = df[df["Close"].notna()]
    close  = df["Close"]
    volume = df["Volume"]
    high   = df["High"]
    low    = df["Low"]

    df["SMA_20"] = sma(close, 20)
    df["SMA_50"] = sma(close, 50)
    df["EMA_20"] = ema(close, 20)
    df["RSI"]    = rsi(close, 14)

    macd_line, signal_line, histogram = macd(close, 12, 26, 9)
    df["MACD"]        = macd_line
    df["MACD_signal"] = signal_line
    df["MACD_hist"]   = histogram

    bb_upper, bb_mid, bb_lower = bbands(close, 20, 2.0)
    df["BB_upper"] = bb_upper
    df["BB_mid"]   = bb_mid
    df["BB_lower"] = bb_lower

    df["OBV"] = obv(close, volume)

    return df


def technical_score(df: pd.DataFrame) -> tuple[float, dict]:
    """Returns score 0–100 and a dict of signal details. Higher = more bullish."""
    signals = {}
    points  = 0
    max_pts = 0

    latest = df.iloc[-1]
    prev   = df.iloc[-2] if len(df) > 1 else latest

    # RSI
    rsi_val = latest.get("RSI")
    if pd.notna(rsi_val):
        max_pts += 20
        if rsi_val < 30:
            points += 18; signals["RSI"] = f"{rsi_val:.1f} — Oversold (bullish)"
        elif rsi_val < 45:
            points += 14; signals["RSI"] = f"{rsi_val:.1f} — Below midrange (mildly bullish)"
        elif rsi_val < 55:
            points += 10; signals["RSI"] = f"{rsi_val:.1f} — Neutral"
        elif rsi_val < 70:
            points +=  6; signals["RSI"] = f"{rsi_val:.1f} — Above midrange (mildly bearish)"
        else:
            points +=  2; signals["RSI"] = f"{rsi_val:.1f} — Overbought (bearish)"

    # MACD histogram
    hist     = latest.get("MACD_hist")
    prev_hist = prev.get("MACD_hist")
    if pd.notna(hist) and pd.notna(prev_hist):
        max_pts += 20
        if hist > 0 and hist > prev_hist:
            points += 20; signals["MACD"] = "Positive and rising (bullish)"
        elif hist > 0:
            points += 14; signals["MACD"] = "Positive but declining (neutral)"
        elif hist < 0 and hist > prev_hist:
            points +=  8; signals["MACD"] = "Negative but improving (cautious)"
        else:
            points +=  2; signals["MACD"] = "Negative and falling (bearish)"

    # Price vs moving averages
    sma20 = latest.get("SMA_20")
    sma50 = latest.get("SMA_50")
    close = latest["Close"]
    if pd.notna(sma20) and pd.notna(sma50):
        max_pts += 20
        if close > sma20 > sma50:
            points += 20; signals["MA Trend"] = "Price > SMA20 > SMA50 (strong uptrend)"
        elif close > sma20:
            points += 14; signals["MA Trend"] = "Price > SMA20 (short-term bullish)"
        elif close > sma50:
            points +=  8; signals["MA Trend"] = "Price > SMA50 only (weakening)"
        else:
            points +=  2; signals["MA Trend"] = "Price below both MAs (downtrend)"

    # Bollinger Band position
    bb_upper = latest.get("BB_upper")
    bb_lower = latest.get("BB_lower")
    if pd.notna(bb_upper) and pd.notna(bb_lower):
        band_range = bb_upper - bb_lower
        max_pts += 20
        if band_range > 0:
            pos = (close - bb_lower) / band_range
            if pos < 0.2:
                points += 18; signals["Bollinger"] = f"Near lower band ({pos:.0%}) — potential bounce"
            elif pos < 0.4:
                points += 14; signals["Bollinger"] = f"Lower half ({pos:.0%}) — mildly bullish"
            elif pos < 0.6:
                points += 10; signals["Bollinger"] = f"Mid band ({pos:.0%}) — neutral"
            elif pos < 0.8:
                points +=  6; signals["Bollinger"] = f"Upper half ({pos:.0%}) — mildly bearish"
            else:
                points +=  2; signals["Bollinger"] = f"Near upper band ({pos:.0%}) — potential reversal"

    # Volume trend
    if "Volume" in df.columns:
        max_pts += 20
        vol_recent = df["Volume"].iloc[-5:].mean()
        vol_avg    = df["Volume"].iloc[-20:].mean()
        if vol_avg > 0:
            ratio = vol_recent / vol_avg
            if ratio > 1.5:
                points += 20; signals["Volume"] = f"{ratio:.1f}x avg — strong interest"
            elif ratio > 1.1:
                points += 14; signals["Volume"] = f"{ratio:.1f}x avg — above average"
            elif ratio > 0.9:
                points += 10; signals["Volume"] = f"{ratio:.1f}x avg — normal"
            else:
                points +=  5; signals["Volume"] = f"{ratio:.1f}x avg — low interest"

    score = (points / max_pts * 100) if max_pts > 0 else 50
    return round(score, 1), signals
