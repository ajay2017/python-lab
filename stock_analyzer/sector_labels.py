"""Canonical sector label for a recommendation row.

Recommendation writers stored sectors from three different vocabularies:
- the curated `TICKER_SECTORS` buckets ("Financials", "Healthcare", ...);
- raw provider/GICS names on Watchlist Enter Now rows ("Financial Services",
  "Technology");
- the scanner's discovery-bucket names ("Financials & Fintech", "Healthcare &
  Biotech"), plus the placeholder "Watchlist", which the scanner gives every
  watchlist ticker (`scanner.py`) and which was then persisted as if it were a
  sector.

One sector therefore showed up as several labels on 📊 Predictive Analytics →
🌐 Sector Alpha, and each slice was thin (found 2026-09-30 by
scripts/predictive_analytics_audit.py). `canonical_sector` collapses them.

Order:
  1. the curated map by ticker (the single source of truth, as in
     `portfolio.resolve_sector`);
  2. an alias table for labels with an unambiguous curated equivalent;
  3. "Watchlist" or blank → UNCLASSIFIED_SECTOR;
  4. anything else is returned as-is.

Owner decision 2026-09-30: "Technology", "Consumer Cyclical" and
"AI & Data Platforms" have no clean curated equivalent (the curated map splits
tech six ways), so they are deliberately NOT aliased. Classify those tickers
into TICKER_SECTORS instead. The audit script lists them.

Display/analytics labelling only. This is not wired into any gate; the
sector-ceiling gate classifies via `portfolio.resolve_sector` and is untouched.
"""
from __future__ import annotations

from stock_analyzer.constants import UNCLASSIFIED_SECTOR

# Keys are lower-cased and stripped. Only labels whose curated target is
# unambiguous belong here.
SECTOR_LABEL_ALIASES: dict[str, str] = {
    "financial services":     "Financials",
    "financials & fintech":   "Financials",
    "health care":            "Healthcare",
    "healthcare & biotech":   "Healthcare",
    "basic materials":        "Materials",
    "consumer defensive":     "Consumer Staples & Retail",
    "defense & aerospace":    "Defense",
}

# Placeholders that are not a sector at all.
_NOT_A_SECTOR = {"", "watchlist", "none", "nan", "unknown"}


def is_placeholder_sector(label: str | None) -> bool:
    """True when `label` is not a real sector at all (blank, "Watchlist",
    "nan", "None", "Unknown" — case/whitespace-insensitive), vs. a genuine
    (even if uncurated) sector name. Shared by `sector_fit.gate_sector` so a
    gate's fallback chain (row label -> provider label -> unclassified) uses
    the exact same placeholder vocabulary this module's own `canonical_sector`
    already uses for display (sector_gate_spec.md, 2026-09-30)."""
    return str(label or "").strip().lower() in _NOT_A_SECTOR


def canonical_sector(ticker: str | None, stored: str | None) -> str:
    """Curated label for `ticker`, else the aliased `stored` label, else
    UNCLASSIFIED_SECTOR for a placeholder, else `stored` stripped."""
    from stock_analyzer.portfolio import TICKER_SECTORS  # lazy: portfolio is heavy

    tk = str(ticker or "").strip().upper()
    if tk and tk in TICKER_SECTORS:
        return TICKER_SECTORS[tk]
    raw = "" if stored is None else str(stored).strip()
    key = raw.lower()
    if key in _NOT_A_SECTOR:
        return UNCLASSIFIED_SECTOR
    return SECTOR_LABEL_ALIASES.get(key, raw)
