import yfinance as yf
import pandas as pd
from stock_analyzer.indicators import sma, rsi as calc_rsi
from stock_analyzer.constants import MOVER_MIN_DAY_GAIN_PCT

# Shelf life: registered in stock_analyzer/reference_shelf.py — update its as_of date when you refresh this list.
#
# BUCKET LABELS ARE LOAD-BEARING, not just display. daily_briefing.py's macro
# gate resolves a candidate's sector via portfolio.resolve_sector(ticker, <this
# bucket label>) and then tests `sector in _macro_blocked_sectors`. A label that
# macro_calendar._SECTOR_IMPACT doesn't know can NEVER be macro-suppressed — it
# fails OPEN, silently, with no banner. So when adding a bucket, either name it
# exactly as an existing _SECTOR_IMPACT key or add a row there in the same
# commit, and give each ticker a portfolio.TICKER_SECTORS entry so the pick path
# and the held path (which resolves from the provider's GICS string) agree.
# tests/test_scanner.py asserts this invariant with an explicit allowlist.
SECTOR_UNIVERSE = {
    "AI & Cloud": ["MSFT", "GOOGL", "META", "AMZN", "CRM", "NOW", "DDOG", "WDAY"],
    "Cybersecurity": ["PANW", "CRWD", "ZS", "NET", "FTNT", "OKTA", "S"],
    "Semiconductors": ["NVDA", "AMD", "AVGO", "MU", "QCOM", "AMAT", "ASML", "INTC"],
    "Consumer Tech": ["AAPL", "NFLX", "SHOP", "UBER", "ABNB"],
    "AI & Data Platforms": ["PLTR", "AI", "MDB", "SNOW", "PATH", "IONQ"],
    "EV & Clean Energy": ["TSLA", "ENPH", "FSLR", "NEE", "RIVN"],
    "Healthcare & Biotech": ["LLY", "NVO", "ABBV", "ISRG", "MRNA", "REGN", "AMGN",
                             "JNJ", "UNH", "MRK", "TMO"],
    "Financials & Fintech": ["JPM", "V", "MA", "GS", "XYZ", "COIN", "PYPL",
                             "BAC", "WFC", "MS", "SCHW"],
    "Enterprise Tech": ["DELL", "ORCL", "IBM", "HPE", "SAP"],
    "Defense & Aerospace": ["LMT", "RTX", "NOC", "GD", "BA"],
    "Industrials": ["CAT", "GE", "GEV"],
    "Communications": ["T", "VZ", "TMUS"],
    "Energy": ["XOM", "CVX", "OXY", "COP", "SLB"],
    "Consumer Staples & Retail": ["COST", "NKE", "TJX", "WMT", "TGT", "HD"],
}


# ── Score-component point buckets ────────────────────────────────────────────
# Extracted 2026-07-29 (audit Medium finding) so app.py's Market Scanner
# "Signal Evidence" transparency panel can call these directly instead of
# hand-copying the thresholds — a prior copy had already silently duplicated
# them with no shared source to keep the two in sync.

def _rsi_points(rsi: float) -> int:
    """RSI component (30 pts) — reward the sweet spot 40-65, slightly oversold is also good."""
    if 40 <= rsi <= 65:
        return 30
    elif rsi < 40:
        return 22
    elif rsi < 75:
        return 12
    return 2


def _trend_points(trend_label: str) -> int:
    """Trend-alignment component (35 pts), keyed off the trend label _quick_score()
    itself produces from the identical price/SMA brackets — the label is a
    lossless encoding of which bracket fired, so this never needs the raw
    price/SMA values to stay in sync with the scoring."""
    if "Strong Uptrend" in trend_label:
        return 35
    elif "Uptrend" in trend_label:
        return 20
    elif "Mixed" in trend_label:
        return 10
    return 0


def _momentum_1m_points(mom_1m: float) -> int:
    """1-month momentum component (20 pts)."""
    if mom_1m > 8:
        return 20
    elif mom_1m > 3:
        return 14
    elif mom_1m > 0:
        return 7
    elif mom_1m > -5:
        return 2
    return 0


def _momentum_3m_points(mom_3m: float) -> int:
    """3-month momentum component (15 pts)."""
    if mom_3m > 15:
        return 15
    elif mom_3m > 5:
        return 10
    elif mom_3m > 0:
        return 5
    return 0


def _quick_score(ticker: str, df: pd.DataFrame) -> dict | None:
    try:
        close = df["Close"].dropna()
        volume = df["Volume"].dropna()
        if len(close) < 30:
            return None

        rsi_s   = calc_rsi(close, 14)
        sma20_s = sma(close, 20)
        sma50_s = sma(close, 50)

        price = float(close.iloc[-1])
        rsi   = float(rsi_s.dropna().iloc[-1])   if not rsi_s.dropna().empty   else 50.0
        sma20 = float(sma20_s.dropna().iloc[-1]) if not sma20_s.dropna().empty else price
        sma50 = float(sma50_s.dropna().iloc[-1]) if not sma50_s.dropna().empty else price

        mom_1m = (price / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0.0
        mom_3m = (price / float(close.iloc[-63]) - 1) * 100 if len(close) > 63 else 0.0

        # Trend label (computed first — trend_pts is derived from this label,
        # a lossless encoding of the same price/SMA brackets, so there is only
        # one place these brackets are evaluated).
        if price > sma20 > sma50:
            trend = "⬆⬆ Strong Uptrend"
        elif price > sma20:
            trend = "⬆ Uptrend"
        elif price > sma50:
            trend = "↔ Mixed"
        else:
            trend = "⬇ Downtrend"

        score = (
            _rsi_points(rsi)
            + _trend_points(trend)
            + _momentum_1m_points(mom_1m)
            + _momentum_3m_points(mom_3m)
        )

        # Volume ratio (recent vs 20-day avg)
        vol_ratio = (
            float(volume.iloc[-5:].mean() / volume.iloc[-20:].mean())
            if len(volume) >= 20 and volume.iloc[-20:].mean() > 0 else 1.0
        )

        # Signal label — momentum-only scale, independent of the composite policy
        # gates in constants.py (COMPOSITE_BUY=65 coincides but is not the same
        # concept; the scanner label is awareness only, not a buy recommendation).
        if score >= 80:
            signal = "⬆⬆ Strong Buy"
        elif score >= 65:
            signal = "⬆ Buy"
        elif score >= 45:
            signal = "➡ Hold / Watch"
        elif score >= 30:
            signal = "⬇ Weak"
        else:
            signal = "⬇⬇ Avoid"

        return {
            "Ticker": ticker,
            "Price": round(price, 2),
            "Score": min(score, 100),
            "Signal": signal,
            "RSI": round(rsi, 1),
            "1M Momentum": round(mom_1m, 1),
            "3M Momentum": round(mom_3m, 1),
            "Trend": trend,
            "Vol Ratio": round(vol_ratio, 1),
        }
    except Exception:
        return None


def scan_sectors(
    selected_sectors: list[str],
    period: str = "6mo",
    extra_tickers: list[str] | None = None,
    universe: "dict[str, list[str]] | None" = None,
) -> pd.DataFrame:
    """
    Scan the requested sectors plus any extra tickers (e.g. user's watchlist).

    extra_tickers are tagged with sector="Watchlist" so they appear in scan
    results with a recognisable label. Tickers already present in a selected
    sector keep their real sector classification — dedup is by ticker symbol,
    not by sector. This lets the user widen the scan universe beyond the
    hardcoded SECTOR_UNIVERSE without needing a code change for each name.

    `universe` (App Settings, docs/plans/app-settings.md Commit 2): the
    resolved `sector_universe` payload — bucket -> [tickers] — threaded in by
    the caller (via `stock_analyzer.reference_data.resolve_universe`) so this
    function stays pure/testable rather than reaching into the DB itself.

    IMPORTANT — `None` is a unit-test convenience default ONLY (falls back to
    the module-level `SECTOR_UNIVERSE`, or whatever a test has monkeypatched
    onto this module), never an offline-sentinel value. Every REAL caller
    (app.py, cron_runner.py) must always pass this explicitly: a resolved
    payload on success, or an explicit `{}` — NOT a bare `None` — when
    `resolve_universe` raised `ReferenceDataUnavailable`. Passing `None` on
    an unavailable resolution would silently fall through to the hardcoded
    dict one layer down, exactly the silent-stale-universe fallback the
    design doc rejects (the 2026-07-14 INTC failure mode repeated on this
    surface) — the caller must render its own fail-loud banner first, then
    pass `{}` so the scan legitimately returns nothing rather than a
    confident result built on frozen code.
    """
    if universe is None:
        universe = SECTOR_UNIVERSE
    all_tickers, ticker_sector = [], {}
    for sector in selected_sectors:
        for t in universe.get(sector, []):
            if t not in ticker_sector:
                all_tickers.append(t)
                ticker_sector[t] = sector

    # Append watchlist / extra tickers AFTER the curated universe so a ticker
    # that happens to be in both keeps its real-sector classification.
    for t in (extra_tickers or []):
        t = str(t).upper().strip()
        if t and t not in ticker_sector:
            all_tickers.append(t)
            ticker_sector[t] = "Watchlist"

    if not all_tickers:
        return pd.DataFrame()

    try:
        raw = yf.download(
            all_tickers, period=period,
            auto_adjust=True, progress=False, threads=True,
        )
    except Exception:
        return pd.DataFrame()

    results = []
    for ticker in all_tickers:
        try:
            if len(all_tickers) == 1:
                df = raw
            elif isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(ticker, axis=1, level=1).dropna()
            else:
                continue
            if df.empty:
                continue
            result = _quick_score(ticker, df)
            if result:
                result["Sector"] = ticker_sector[ticker]
                results.append(result)
        except Exception:
            continue

    if not results:
        return pd.DataFrame()

    out = (
        pd.DataFrame(results)
        .sort_values("Score", ascending=False)
        .reset_index(drop=True)
    )
    out.insert(0, "Rank", range(1, len(out) + 1))
    return out


def _day_change_pct(close: pd.Series) -> float | None:
    """Last close vs prior close, in percent. None if insufficient history."""
    c = close.dropna()
    if len(c) < 2:
        return None
    prev = float(c.iloc[-2])
    if prev <= 0:
        return None
    return (float(c.iloc[-1]) / prev - 1) * 100


def scan_movers(tickers: list[str], min_day_gain_pct: float = MOVER_MIN_DAY_GAIN_PCT,
                period: str = "3mo") -> pd.DataFrame:
    """
    Scan a broad ticker list for today's biggest 1-day GAINERS.

    Distinct from scan_sectors (which ranks by composite momentum score across
    the curated universe). This is the discovery net: it ranks by today's
    single-day % change so a fresh breakout in an untracked name surfaces.

    Returns a DataFrame ranked by "Day Change %" descending, filtered to
    gainers ≥ min_day_gain_pct. Columns mirror scan_sectors output (so the
    composite-gate + sizing code can treat the rows uniformly) PLUS a
    "Day Change %" column. Sector is tagged "Mover" since these come from the
    discovery universe, not a sector bucket.

    Empty DataFrame on any failure — discovery is best-effort, never blocks
    the rest of the brief.
    """
    tickers = [str(t).upper().strip() for t in (tickers or []) if str(t).strip()]
    if not tickers:
        return pd.DataFrame()

    try:
        raw = yf.download(
            tickers, period=period,
            auto_adjust=True, progress=False, threads=True,
        )
    except Exception:
        return pd.DataFrame()

    results = []
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                df = raw
            elif isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(ticker, axis=1, level=1).dropna()
            else:
                continue
            if df.empty:
                continue
            day_chg = _day_change_pct(df["Close"])
            if day_chg is None or day_chg < min_day_gain_pct:
                continue
            scored = _quick_score(ticker, df)
            if not scored:
                continue
            scored["Sector"]       = "Mover"
            scored["Day Change %"] = round(day_chg, 1)
            results.append(scored)
        except Exception:
            continue

    if not results:
        return pd.DataFrame()

    out = (
        pd.DataFrame(results)
        .sort_values("Day Change %", ascending=False)
        .reset_index(drop=True)
    )
    out.insert(0, "Rank", range(1, len(out) + 1))
    return out
