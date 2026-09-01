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
    # AI (C3.ai, $1.7B) removed 2026-09-01 — this file's own stated liquidity
    # rule ("keep names liquid — avoid thin micro-caps where a 10% move is
    # noise, not signal"), same rule already applied to SEDG/RUN/PLUG above.
    # IBM and CSCO added 2026-09-01 — both already resolve via the existing
    # portfolio.TICKER_SECTORS "Enterprise Tech" key alongside ORCL/SAP, so
    # this is zero new taxonomy work.
    "Software & Cloud": [
        "CRM", "ORCL", "ADBE", "NOW", "INTU", "SAP", "WDAY", "TEAM", "DDOG",
        "SNOW", "MDB", "NET", "ZS", "CRWD", "PANW", "FTNT", "OKTA", "PLTR",
        # CFLT removed 2026-08-16 — delisted; Yahoo returns 404 "Quote not found"
        # and zero rows at period="max" across every provider.
        "HUBS", "ZM", "DOCU", "TWLO", "DBX", "GTLB", "S", "ESTC",
        "PATH", "U", "BILL", "APP", "IBM", "CSCO",
    ],
    # EBAY ($46.1B) and WBD ($71.5B) added 2026-09-01 — EBAY is the only major
    # US online-marketplace name missing (SE/MELI/BABA/PDD/JD here are all
    # non-US); WBD is a traditional media conglomerate, distinct from the
    # DIS/CMCSA/NFLX streaming mix already in this bucket. Neither has a
    # portfolio.TICKER_SECTORS entry yet (their raw provider GICS sector
    # doesn't match any macro-gate-known key) — but this bucket already
    # carries the same unmapped-sector gap on several existing members at
    # comparable or larger scale, so this isn't a new class of debt; it's
    # tracked separately (see the CLAUDE.md "What's queued" item on the
    # raw-GICS-vs-curated-sector shadow defect), not fixed in this commit.
    "Internet & Media": [
        "NFLX", "DIS", "CMCSA", "SHOP", "UBER", "ABNB", "DASH", "SPOT", "PINS",
        "SNAP", "RBLX", "ROKU", "PYPL", "XYZ", "COIN", "HOOD", "SE", "MELI",
        "BABA", "PDD", "JD", "EBAY", "WBD",
    ],
    # LCID ($1.9B) removed 2026-09-01 — same sub-scale liquidity rule as AI
    # above. KR ($35.3B) and ORLY ($71.8B) added 2026-09-01 — KR is grocery
    # retail, a genuine gap next to the warehouse/big-box names already here;
    # ORLY is auto-parts retail, distinct from the auto manufacturers F/GM/
    # RIVN already in this bucket. Neither KR nor ORLY has a
    # portfolio.TICKER_SECTORS entry yet (same raw-GICS-vs-curated gap as
    # EBAY/WBD above) — this bucket already carries that same gap on several
    # existing members at comparable or larger scale, so it isn't a new class
    # of debt; tracked separately, not fixed in this commit.
    "Consumer & Retail": [
        "WMT", "COST", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD", "CMG", "LULU",
        "TJX", "ROST", "DG", "DLTR", "ULTA", "BKNG", "MAR", "F", "GM", "RIVN",
        "DKNG", "KR", "ORLY",
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
    # SEDG ($2.0B) / RUN ($2.4B) / PLUG ($3.2B) removed 2026-08-17 — this
    # file's OWN stated rule applied ("keep names liquid — avoid thin micro-caps
    # where a 10% move is noise, not signal"), not a new policy. They were
    # reaching portfolio.diversifying_candidate_pool, i.e. being scored as
    # concentration RELIEF, which is the opposite of what a $2B name provides.
    # BE ($67.7B) is not sub-scale and stays; ENPH/FSLR stay too — they are
    # legitimate Movers candidates, a different question from whether they
    # should be ASSERTED as the sector's representatives.
    #
    # ORDER IS DELIBERATELY UNCHANGED. A "sort large-first" pass was tried and
    # reverted: this bucket is unioned after the roster and truncated at
    # DIVERSIFY_SCAN_CAP, so reordering promoted CEG/VST — AI-datacenter power
    # plays carrying exactly the tech correlation this sector's 0.28 diversifier
    # claim denies — from cut-by-the-cap into the scored pool. Reordering this
    # list changes which names get scored; treat it as behaviour, not cosmetics.
    "Clean Energy & Utilities": [
        "NEE", "DUK", "SO", "ENPH", "FSLR", "BE", "D",
        "AEP", "EXC", "VST", "CEG",
    ],
    "Communications & Telecom": [
        "T", "VZ", "TMUS",
    ],
}


def discovery_tickers(
    exclude: set[str] | None = None,
    universe: "dict[str, list[str]] | None" = None,
) -> list[str]:
    """Flatten the discovery universe into a deduped ticker list.

    exclude: tickers to drop (already-tracked SECTOR_UNIVERSE names, held
    positions, watchlist) — these are already scanned elsewhere, so the
    Movers pipeline shouldn't re-surface them. Comparison is case-insensitive.

    `universe` (App Settings, docs/plans/app-settings.md Commit 2): the
    resolved `discovery_universe` payload, threaded in by the caller (via
    `stock_analyzer.reference_data.resolve_universe`) so this function stays
    pure/testable. `None` is a unit-test convenience default ONLY (falls
    back to the module-level `DISCOVERY_UNIVERSE`), never an
    offline-sentinel value — the REAL caller must pass an explicit `{}`, not
    a bare `None`, when the table is unavailable (see `scanner.scan_sectors`'s
    identical `universe` param for the full reasoning).
    """
    if universe is None:
        universe = DISCOVERY_UNIVERSE
    excl = {str(t).upper().strip() for t in (exclude or set())}
    seen: set[str] = set()
    out: list[str] = []
    for names in universe.values():
        for t in names:
            tu = t.upper().strip()
            if tu and tu not in excl and tu not in seen:
                out.append(tu)
                seen.add(tu)
    return out
