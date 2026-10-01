"""canonical_sector: one sector vocabulary for recommendation rows (2026-09-30)."""
import pytest

from stock_analyzer.constants import UNCLASSIFIED_SECTOR
from stock_analyzer.portfolio import TICKER_SECTORS
from stock_analyzer.sector_labels import SECTOR_LABEL_ALIASES, canonical_sector

UNMAPPED = "ZZZQX"  # not a real ticker; must never be in the curated map


def test_unmapped_fixture_really_is_unmapped():
    assert UNMAPPED not in TICKER_SECTORS


def test_curated_map_wins_over_any_stored_label():
    tk, curated = next(iter(TICKER_SECTORS.items()))
    assert canonical_sector(tk, "Watchlist") == curated
    assert canonical_sector(tk.lower(), "Financial Services") == curated


@pytest.mark.parametrize("stored,expected", [
    ("Financial Services", "Financials"),
    ("Financials & Fintech", "Financials"),
    ("Health Care", "Healthcare"),
    ("Healthcare & Biotech", "Healthcare"),
    ("Basic Materials", "Materials"),
    ("Consumer Defensive", "Consumer Staples & Retail"),
    ("Defense & Aerospace", "Defense"),
    ("  financial services ", "Financials"),
])
def test_unambiguous_aliases(stored, expected):
    assert canonical_sector(UNMAPPED, stored) == expected


@pytest.mark.parametrize("stored", ["Watchlist", "", None, "  ", "nan", "None", "Unknown"])
def test_placeholders_become_unclassified(stored):
    assert canonical_sector(UNMAPPED, stored) == UNCLASSIFIED_SECTOR


@pytest.mark.parametrize("stored", ["Technology", "Consumer Cyclical", "AI & Data Platforms"])
def test_ambiguous_labels_are_left_alone(stored):
    # Owner decision 2026-09-30: no clean curated equivalent, so they are not aliased.
    assert canonical_sector(UNMAPPED, stored) == stored


def test_every_alias_target_is_a_real_curated_bucket():
    assert set(SECTOR_LABEL_ALIASES.values()) <= set(TICKER_SECTORS.values())


def test_prepare_population_canonicalises_sector_without_mutating_input():
    from stock_analyzer.constants import COMPOSITE_WEIGHTS_VERSION
    from stock_analyzer.predictive_analytics import prepare_population
    rows = [{
        "ticker": UNMAPPED, "rec_type": "new_pick", "rec_date": "2026-09-01",
        "weights_version": COMPOSITE_WEIGHTS_VERSION, "asset_type": None,
        "sector": "Financial Services", "composite_score": 70,
        "outcome_maturing": False, "outcome_pct": 1.0, "alpha_pct": 0.5, "acted_on": False,
    }]
    pop = prepare_population(rows)
    assert rows[0]["sector"] == "Financial Services"          # input untouched
    rep = pop["reps"][0]
    assert rep["sector"] == "Financials"
    assert rep["sector_raw"] == "Financial Services"


def test_match_recs_to_trades_canonicalises_and_keeps_raw():
    import pandas as pd
    from stock_analyzer.recommendations_history import match_recs_to_trades
    recs = pd.DataFrame([
        {"ticker": UNMAPPED, "rec_date": "2026-09-01", "rec_type": "enter_now", "sector": "Financial Services"},
        {"ticker": "ZZZQY", "rec_date": "2026-09-01", "rec_type": "buy_candidate", "sector": "Watchlist"},
    ])
    out = {r["ticker"]: r for r in match_recs_to_trades(recs, pd.DataFrame())}
    assert out[UNMAPPED]["sector"] == "Financials"
    assert out[UNMAPPED]["sector_raw"] == "Financial Services"
    assert out["ZZZQY"]["sector"] == UNCLASSIFIED_SECTOR
    assert out["ZZZQY"]["sector_raw"] == "Watchlist"


def test_communication_services_is_not_aliased():
    # Review 2026-09-30: curated "Communications" is telecom only; GICS
    # Communication Services also spans media/gaming/ad-tech. Not a clean map.
    assert canonical_sector(UNMAPPED, "Communication Services") == "Communication Services"


def test_personalized_discovery_matches_across_vocabularies_and_never_on_other():
    from stock_analyzer.personalized_discovery import score_candidate_match
    prof = {"top_sectors": {"Financials"}}
    hit = score_candidate_match(None, None, "Financials & Fintech", prof)
    assert "sector" in hit["matched_traits"]
    prof_other = {"top_sectors": {UNCLASSIFIED_SECTOR}}
    miss = score_candidate_match(None, None, "Watchlist", prof_other)
    assert "sector" not in miss["matched_traits"]


def test_sector_advice_never_names_an_unclassified_bucket():
    from stock_analyzer.predictive_analytics import synthesize_directives
    sec = [
        {"sector": UNCLASSIFIED_SECTOR, "avg_alpha": 9.0, "n": 20},
        {"sector": "Energy", "avg_alpha": 2.0, "n": 6},
        {"sector": "Financials", "avg_alpha": -5.0, "n": 6},
        {"sector": "Unknown", "avg_alpha": -9.0, "n": 8},
    ]
    ds = synthesize_directives([], None, {}, [], [], sec, 40, min_n=5)
    texts = " ".join(d["text"] for d in ds if d["source_tab"] == "🌐 Sector Alpha")
    assert "Energy has done better" in texts
    assert "Signals in Financials" in texts
    assert "Other" not in texts and "Unknown" not in texts
