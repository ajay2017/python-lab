"""Tests for stock_analyzer.sector_fit — the single source of truth for which
sector a gate (Watchlist ENTER_NOW, Grow Today, Compare, Analysis) classifies
a ticker into, and how much of the book it is measured against.

sector_gate_spec.md (2026-09-30, Opus planner, owner decisions final).
"""
import pandas as pd
import pytest

from stock_analyzer.constants import SECTOR_CEILING, UNCLASSIFIED_SECTOR
from stock_analyzer.portfolio import TICKER_SECTORS
from stock_analyzer.sector_fit import gate_sector, sector_gate_context

pytestmark = pytest.mark.fast

# NVDA is a real, stable TICKER_SECTORS -> "Semiconductors" mapping.
MAPPED_TICKER = "NVDA"
MAPPED_SECTOR = "Semiconductors"
UNMAPPED_TICKER = "ZZZQF"  # not a real ticker; must never be in the curated map


def test_unmapped_fixture_really_is_unmapped():
    assert UNMAPPED_TICKER not in TICKER_SECTORS


def test_mapped_fixture_really_is_mapped():
    assert TICKER_SECTORS.get(MAPPED_TICKER) == MAPPED_SECTOR


def _port_df(sector: str, weight: float, gate_col: str = "Gate Weight (%)"):
    return pd.DataFrame([{"Ticker": "XYZ", "Sector": sector, gate_col: weight}])


# ── gate_sector precedence ───────────────────────────────────────────────────

class TestGateSectorPrecedence:
    def test_ticker_sectors_beats_both_row_label_and_provider(self):
        assert gate_sector(MAPPED_TICKER, row_label="Watchlist",
                            provider_sector="Energy") == MAPPED_SECTOR

    def test_real_row_label_beats_provider(self):
        assert gate_sector(UNMAPPED_TICKER, row_label="Industrials",
                            provider_sector="Energy") == "Industrials"

    def test_placeholder_row_label_falls_through_to_provider(self):
        assert gate_sector(UNMAPPED_TICKER, row_label="Watchlist",
                            provider_sector="Energy") == "Energy"

    def test_both_placeholder_is_unclassified(self):
        assert gate_sector(UNMAPPED_TICKER, row_label="Watchlist",
                            provider_sector="nan") == UNCLASSIFIED_SECTOR

    def test_no_args_is_unclassified(self):
        assert gate_sector(UNMAPPED_TICKER) == UNCLASSIFIED_SECTOR

    @pytest.mark.parametrize("placeholder", ["", None, "Watchlist", "nan", "None", "Unknown"])
    def test_every_placeholder_provider_is_unclassified(self, placeholder):
        assert gate_sector(UNMAPPED_TICKER, provider_sector=placeholder) == UNCLASSIFIED_SECTOR


# ── sector_gate_context ───────────────────────────────────────────────────────

class TestSectorGateContext:
    def test_mapped_semis_ticker_curated_weight_exact_ceiling(self):
        port_df = _port_df(MAPPED_SECTOR, SECTOR_CEILING)
        ctx = sector_gate_context(MAPPED_TICKER, "Technology", port_df)
        assert ctx["sector"] == MAPPED_SECTOR
        assert ctx["classified"] is True
        assert ctx["label_source"] == "curated"
        assert ctx["weight_pct"] == SECTOR_CEILING

    def test_unmapped_ticker_provider_label_matched_against_held_rows(self):
        port_df = _port_df("Xyz Sector", 12.5)
        ctx = sector_gate_context(UNMAPPED_TICKER, "Xyz Sector", port_df)
        assert ctx["sector"] == "Xyz Sector"
        assert ctx["classified"] is True
        assert ctx["label_source"] == "provider"
        assert ctx["weight_pct"] == 12.5

    @pytest.mark.parametrize("provider", [None, "", "Watchlist", "nan"])
    def test_blank_provider_sector_is_unclassified_phantom_guard(self, provider):
        # port_df "Other" at/above ceiling must NEVER leak into weight_pct
        # for an unclassified ticker — the phantom-"Other"-breach guard.
        port_df = _port_df(UNCLASSIFIED_SECTOR, SECTOR_CEILING + 10)
        ctx = sector_gate_context(UNMAPPED_TICKER, provider, port_df)
        assert ctx["classified"] is False
        assert ctx["weight_pct"] == 0.0
        assert ctx["label_source"] == "none"

    def test_gate_weight_column_used_when_present(self):
        port_df = pd.DataFrame([
            {"Ticker": "A", "Sector": MAPPED_SECTOR, "Weight (%)": 40.0, "Gate Weight (%)": 10.0},
        ])
        ctx = sector_gate_context(MAPPED_TICKER, None, port_df)
        assert ctx["weight_pct"] == 10.0

    def test_falls_back_to_weight_column_when_gate_weight_absent(self):
        port_df = pd.DataFrame([
            {"Ticker": "A", "Sector": MAPPED_SECTOR, "Weight (%)": 17.0},
        ])
        ctx = sector_gate_context(MAPPED_TICKER, None, port_df)
        assert ctx["weight_pct"] == 17.0

    def test_port_df_none_gives_zero_weight(self):
        ctx = sector_gate_context(MAPPED_TICKER, None, None)
        assert ctx["classified"] is True
        assert ctx["weight_pct"] == 0.0

    def test_port_df_empty_gives_zero_weight(self):
        ctx = sector_gate_context(MAPPED_TICKER, None, pd.DataFrame())
        assert ctx["weight_pct"] == 0.0

    def test_port_df_without_sector_column_gives_zero_weight(self):
        port_df = pd.DataFrame([{"Ticker": "A", "Weight (%)": 40.0}])
        ctx = sector_gate_context(MAPPED_TICKER, None, port_df)
        assert ctx["weight_pct"] == 0.0
