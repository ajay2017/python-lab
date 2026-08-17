"""
Discovery universe — the broad net for the Movers feature.

This is intentionally LARGER than scanner.SECTOR_UNIVERSE (the ~70 hand-picked
names Grow Today scans every day). The Movers pipeline scans this wider list
for big 1-day gainers so a genuine breakout in a name the user isn't tracking
(the next SMCI) can surface — instead of being invisible because it wasn't on
the short list.

Curated extended set (~200 liquid large/mid-caps across every sector). This is
DATA, not logic — refresh it occasionally (quarterly is plenty) as index
membership and market leadership shift. Overlap with SECTOR_UNIVERSE is fine:
the Movers pipeline excludes already-tracked / held / watchlist tickers at
runtime, so a name appearing in both lists is simply deduped.

Why hardcoded rather than scraped: a live S&P 500 scrape (Wikipedia / index
provider) adds a runtime dependency and a new failure mode for a list that
barely changes week to week. A static list has zero runtime risk; the cost is
a manual refresh a few times a year.
"""

# Grouped by sector purely for human readability — the Movers scan flattens
# this into one ticker list. Keep names liquid (avoid thin micro-caps where a
# 10% move is noise, not signal).
# Shelf life: registered in stock_analyzer/reference_shelf.py — update its as_of date when you refresh this list.
DISCOVERY_UNIVERSE: dict[str, list[str]] = {
    "Mega-cap Tech": [
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AVGO",
    ],
    "Semiconductors": [
        "AMD", "INTC", "QCOM", "MU", "AMAT", "ASML", "TXN", "ADI", "LRCX",
        "KLAC", "MRVL", "NXPI", "MCHP", "ON", "TER", "SWKS", "MPWR", "ARM",
        "SMCI", "WOLF",
    ],
    "Software & Cloud": [
        "CRM", "ORCL", "ADBE", "NOW", "INTU", "SAP", "WDAY", "TEAM", "DDOG",
        "SNOW", "MDB", "NET", "ZS", "CRWD", "PANW", "FTNT", "OKTA", "PLTR",
        # CFLT removed 2026-08-16 — delisted; Yahoo returns 404 "Quote not found"
        # and zero rows at period="max" across every provider.
        "HUBS", "ZM", "DOCU", "TWLO", "DBX", "GTLB", "S", "ESTC",
        "PATH", "AI", "U", "BILL", "APP",
    ],
    "Internet & Media": [
        "NFLX", "DIS", "CMCSA", "SHOP", "UBER", "ABNB", "DASH", "SPOT", "PINS",
        "SNAP", "RBLX", "ROKU", "PYPL", "XYZ", "COIN", "HOOD", "SE", "MELI",
        "BABA", "PDD", "JD",
    ],
    "Consumer & Retail": [
        "WMT", "COST", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD", "CMG", "LULU",
        "TJX", "ROST", "DG", "DLTR", "ULTA", "BKNG", "MAR", "F", "GM", "RIVN",
        "LCID", "DKNG",
    ],
    "Healthcare & Biotech": [
        "LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "TMO", "ABT", "DHR", "AMGN",
        "ISRG", "GILD", "VRTX", "REGN", "MRNA", "BIIB", "CVS", "BMY", "HCA",
        "MDT", "SYK", "BSX", "NVO", "HIMS",
    ],
    "Financials": [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP", "V", "MA",
        "SPGI", "CME", "ICE", "PNC", "USB", "COF", "BX", "KKR", "APO",
    ],
    "Industrials & Defense": [
        "CAT", "DE", "BA", "GE", "HON", "RTX", "LMT", "NOC", "GD", "UNP", "UPS",
        "FDX", "EMR", "ETN", "PH", "ITW", "MMM", "GEV", "PWR",
    ],
    "Energy & Materials": [
        "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "MPC", "PSX", "VLO", "WMB",
        "KMI", "LIN", "FCX", "NEM", "NUE", "DOW", "ALB", "CCJ",
    ],
    "Clean Energy & Utilities": [
        "NEE", "DUK", "SO", "ENPH", "FSLR", "SEDG", "RUN", "PLUG", "BE", "D",
        "AEP", "EXC", "VST", "CEG",
    ],
    "Communications & Telecom": [
        "T", "VZ", "TMUS",
    ],
}


def discovery_tickers(exclude: set[str] | None = None) -> list[str]:
    """Flatten the discovery universe into a deduped ticker list.

    exclude: tickers to drop (already-tracked SECTOR_UNIVERSE names, held
    positions, watchlist) — these are already scanned elsewhere, so the
    Movers pipeline shouldn't re-surface them. Comparison is case-insensitive.
    """
    excl = {str(t).upper().strip() for t in (exclude or set())}
    seen: set[str] = set()
    out: list[str] = []
    for names in DISCOVERY_UNIVERSE.values():
        for t in names:
            tu = t.upper().strip()
            if tu and tu not in excl and tu not in seen:
                out.append(tu)
                seen.add(tu)
    return out
