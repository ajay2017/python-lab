"""C1 of docs/plans/macro-sector-gate.md: the one sector vocabulary
(`stock_analyzer/sector_vocab.py`) and its single-table link to
`sector_labels.canonical_sector`."""
import pytest

from stock_analyzer import sector_labels
from stock_analyzer import sector_vocab as sv
from stock_analyzer.constants import UNCLASSIFIED_SECTOR
from stock_analyzer.macro_calendar import _SECTOR_IMPACT
from stock_analyzer.portfolio import TICKER_SECTORS

pytestmark = pytest.mark.fast


def test_curated_sectors_equal_the_ticker_sector_values():
    assert sv.CURATED_SECTORS == set(TICKER_SECTORS.values())
    assert len(sv.CURATED_SECTORS) == 18


def test_every_curated_label_is_keyed_in_each_keyed_impact_category():
    keyed = {c: m for c, m in _SECTOR_IMPACT.items() if "__ALL__" not in m}
    assert keyed, "expected the keyed categories (Inflation / Employment / ...)"
    for cat, m in keyed.items():
        missing = sv.CURATED_SECTORS - set(m)
        assert not missing, f"{cat} has no severity for {sorted(missing)}"


def test_every_mapping_value_is_curated_and_keys_are_normalised():
    for k, v in sv.PROVIDER_TO_CURATED.items():
        assert v in sv.CURATED_SECTORS
        assert k == k.strip().lower()


@pytest.mark.parametrize("label,want", [
    ("Financial Services", "Financials"),
    ("  financial services  ", "Financials"),
    ("FINANCIAL SERVICES", "Financials"),
    ("Health Care", "Healthcare"),
    ("Basic Materials", "Materials"),
    ("Consumer Defensive", "Consumer Staples & Retail"),
    ("Consumer Staples", "Consumer Staples & Retail"),
    ("Financials & Fintech", "Financials"),
    ("Healthcare & Biotech", "Healthcare"),
    ("Defense & Aerospace", "Defense"),
    # identity on every curated label, any case / padding
    ("Financials", "Financials"),
    (" energy ", "Energy"),
    ("AI & CLOUD", "AI & Cloud"),
])
def test_to_curated_maps(label, want):
    assert sv.to_curated(label) == want


@pytest.mark.parametrize("label", [
    "Technology", "Information Technology", "Consumer Cyclical",
    "Consumer Discretionary", "Communication Services", "AI & Data Platforms",
    "EV & Clean Energy", "Something Else",
])
def test_unmapped_provider_labels_are_none(label):
    assert sv.to_curated(label) is None


@pytest.mark.parametrize("label", [None, "", "   ", "Other", "other", UNCLASSIFIED_SECTOR,
                                   "Watchlist", "nan", "None", "Unknown", float("nan")])
def test_placeholders_and_blank_are_none(label):
    assert sv.to_curated(label) is None


def test_communication_services_stays_unmapped_because_it_would_loosen_nfp():
    """Mapping it to Communications would loosen the Non-Farm Payrolls gate:
    Communications Employment severity 1 (not blocked) vs Consumer Tech 3 /
    AI & Cloud 2 (blocked). Owner ruling U1, 2026-11-04."""
    assert "communication services" not in sv.PROVIDER_TO_CURATED, (
        "Communication Services must stay unmapped: Communications has Employment "
        "severity 1 vs Consumer Tech 3 / AI & Cloud 2, so mapping it would loosen NFP")
    emp = _SECTOR_IMPACT["Employment"]
    assert emp["Communications"] < 2 <= emp["Consumer Tech"]


def test_placeholder_vocabulary_matches_sector_labels():
    for v in ("", "watchlist", "none", "nan", "unknown", "Watchlist", " NaN "):
        assert sector_labels.is_placeholder_sector(v) == sv.is_not_a_sector(v)


def test_sector_labels_reads_the_same_table():
    for k, v in sector_labels.SECTOR_LABEL_ALIASES.items():
        assert sv.PROVIDER_TO_CURATED[k] == v
    # the display path never aliased "consumer staples"; its outputs are unchanged
    assert set(sv.PROVIDER_TO_CURATED) - set(sector_labels.SECTOR_LABEL_ALIASES) == {"consumer staples"}


@pytest.mark.parametrize("stored,want", [
    ("Financial Services", "Financials"),
    ("financials & fintech", "Financials"),
    ("Health Care", "Healthcare"),
    ("healthcare & biotech", "Healthcare"),
    ("Basic Materials", "Materials"),
    ("Consumer Defensive", "Consumer Staples & Retail"),
    ("Defense & Aerospace", "Defense"),
    ("Technology", "Technology"),                 # unknown labels pass through raw
    ("  Consumer Cyclical ", "Consumer Cyclical"),
    ("AI & Data Platforms", "AI & Data Platforms"),
    ("Consumer Staples", "Consumer Staples"),     # gate-only row: display unchanged
    ("Watchlist", UNCLASSIFIED_SECTOR),
    (None, UNCLASSIFIED_SECTOR),
    ("", UNCLASSIFIED_SECTOR),
])
def test_canonical_sector_outputs_unchanged(stored, want):
    assert sector_labels.canonical_sector("ZZZZ", stored) == want


def test_canonical_sector_curated_ticker_wins():
    assert sector_labels.canonical_sector("CME", "Technology") == TICKER_SECTORS["CME"]
