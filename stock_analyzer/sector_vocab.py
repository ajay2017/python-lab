"""The app's one sector vocabulary: curated labels and the provider-label
translation table (docs/plans/macro-sector-gate.md, C1).

The macro-event block set (``macro_calendar._SECTOR_IMPACT``) holds CURATED
labels only. A name with no ``TICKER_SECTORS`` entry carries the data
provider's raw label ("Financial Services"), which is not in that set, so a
membership test passed it silently (MacroGap, 2026-11-03: ENVA vs NFP).
``to_curated`` is the one place a raw label is translated to a curated one,
or refused (None) when there is no unambiguous curated equivalent.

Pure: no streamlit, no db. ``CURATED_SECTORS`` is a literal (importing
``portfolio.TICKER_SECTORS`` here would be circular once ``resolve_sector``
translates at the source); a test pins it equal to
``set(TICKER_SECTORS.values())``.
"""
from __future__ import annotations

from stock_analyzer.constants import UNCLASSIFIED_SECTOR

CURATED_SECTORS: frozenset = frozenset({
    "AI & Cloud",
    "AI & Data",
    "Clean Energy",
    "Communications",
    "Consumer Staples & Retail",
    "Consumer Tech",
    "Cybersecurity",
    "Defense",
    "EV & Auto",
    "Energy",
    "Enterprise Tech",
    "Financials",
    "Healthcare",
    "Industrials",
    "Materials",
    "Real Estate",
    "Semiconductors",
    "Utilities",
})

# Keys are lower-cased and stripped. Only labels whose curated target is
# unambiguous belong here (owner ruling U1, 2026-11-04).
#
# Deliberately UNMAPPED (they fall through to the unknown-sector hold):
#   Technology, Information Technology - the curated map splits tech six ways;
#   Consumer Cyclical, Consumer Discretionary - no single curated equivalent;
#   Communication Services - mapping it to Communications would LOOSEN the
#     Non-Farm Payrolls gate (Communications Employment severity 1 vs
#     Consumer Tech 3 / AI & Cloud 2), so such a name must hold instead;
#   AI & Data Platforms, EV & Clean Energy - scanner buckets that straddle two
#     curated sectors.
PROVIDER_TO_CURATED: dict = {
    "financial services":     "Financials",
    "financials & fintech":   "Financials",
    "health care":            "Healthcare",
    "healthcare & biotech":   "Healthcare",
    "basic materials":        "Materials",
    "consumer defensive":     "Consumer Staples & Retail",
    "consumer staples":       "Consumer Staples & Retail",
    "defense & aerospace":    "Defense",
}

# Same vocabulary as sector_labels.is_placeholder_sector (kept as its own copy:
# sector_labels imports THIS module for its alias table, so importing back
# would be circular; a test pins the two equal).
_NOT_A_SECTOR = frozenset({"", "watchlist", "none", "nan", "unknown"})

_CURATED_BY_KEY = {c.lower(): c for c in CURATED_SECTORS}


def is_not_a_sector(label) -> bool:
    """True for None / blank / a placeholder ("Watchlist", "nan", ...) / the
    app's own "Other": no label the data provider gave."""
    key = "" if label is None else str(label).strip().lower()
    return key in _NOT_A_SECTOR or key == UNCLASSIFIED_SECTOR.lower()


def to_curated(label) -> "str | None":
    """Curated sector for ``label``, or None.

    Case/space-insensitive. A curated label maps to itself; a provider label on
    ``PROVIDER_TO_CURATED`` to its target. None for None / blank / a placeholder
    ("Watchlist", "nan", ...) / "Other" / anything unmapped."""
    if is_not_a_sector(label):
        return None
    key = str(label).strip().lower()
    if key in _CURATED_BY_KEY:
        return _CURATED_BY_KEY[key]
    return PROVIDER_TO_CURATED.get(key)
