"""Tests for stock_analyzer/ranking.py — universe-relative score ranking
(tier_label, rank_holdings_in_universe, sector_alternatives). Previously zero
test coverage despite tier_label's thresholds having recently been promoted
from hardcoded literals to named constants (an audit finding) -- boundary
precision matters here. Pure pandas, no I/O.
"""
import pandas as pd

from stock_analyzer import ranking


# ─── tier_label — boundary pairs at 90 / 75 / 50 / 25 / 10 ─────────────────

def test_tier_label_at_90_is_top_decile():
    label, _ = ranking.tier_label(90.0)
    assert label == "Top Decile 🏆"


def test_tier_label_just_below_90_is_top_quartile():
    label, _ = ranking.tier_label(89.99)
    assert label == "Top Quartile"


def test_tier_label_at_75_is_top_quartile():
    label, _ = ranking.tier_label(75.0)
    assert label == "Top Quartile"


def test_tier_label_just_below_75_is_above_median():
    label, _ = ranking.tier_label(74.99)
    assert label == "Above Median"


def test_tier_label_at_50_is_above_median():
    label, _ = ranking.tier_label(50.0)
    assert label == "Above Median"


def test_tier_label_just_below_50_is_below_median():
    label, _ = ranking.tier_label(49.99)
    assert label == "Below Median"


def test_tier_label_at_25_is_below_median():
    label, _ = ranking.tier_label(25.0)
    assert label == "Below Median"


def test_tier_label_just_below_25_is_bottom_quartile():
    label, _ = ranking.tier_label(24.99)
    assert label == "Bottom Quartile"


def test_tier_label_at_10_is_bottom_quartile():
    label, _ = ranking.tier_label(10.0)
    assert label == "Bottom Quartile"


def test_tier_label_just_below_10_is_bottom_decile():
    label, _ = ranking.tier_label(9.99)
    assert label == "Bottom Decile ⚠️"


def test_tier_label_colors_present():
    _, color = ranking.tier_label(95.0)
    assert color == "#00C851"


# ─── rank_holdings_in_universe — empty-df guards ────────────────────────────

def test_rank_holdings_empty_port_df_returns_empty():
    scan_df = pd.DataFrame([{"Ticker": "AAA", "Rank": 1, "Score": 80.0, "Sector": "Tech"}])
    result = ranking.rank_holdings_in_universe(pd.DataFrame(), scan_df)
    assert result.empty


def test_rank_holdings_empty_scan_df_returns_empty():
    port_df = pd.DataFrame([{"Ticker": "AAA", "Sector": "Tech", "Score": 80.0}])
    result = ranking.rank_holdings_in_universe(port_df, pd.DataFrame())
    assert result.empty


# ─── rank_holdings_in_universe — present-vs-absent-in-universe ──────────────

def _scan_row(ticker, rank, score, sector="Tech"):
    return {"Ticker": ticker, "Rank": rank, "Score": score, "Sector": sector}


def test_rank_holdings_ticker_present_in_universe_computes_fields():
    scan_df = pd.DataFrame([
        _scan_row("AAA", 1, 90.0),
        _scan_row("BBB", 2, 80.0),
        _scan_row("CCC", 3, 70.0),
        _scan_row("DDD", 4, 60.0),
    ])
    port_df = pd.DataFrame([{"Ticker": "BBB", "Sector": "Tech", "Score": 82.0}])
    result = ranking.rank_holdings_in_universe(port_df, scan_df)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["Universe Rank"] == 2
    assert row["of"] == 4
    # percentile = (total - u_rank + 1) / total * 100 = (4-2+1)/4*100 = 75.0
    assert row["Percentile"] == 75.0
    assert row["Tier"] == "Top Quartile"
    assert row["Scanner Score"] == 80.0
    assert row["Composite Score"] == 82.0
    # sector_rank: BBB is #2 of 4 Tech-sector rows sorted by Score desc
    assert row["Sector Rank"] == "2/4"


def test_rank_holdings_ticker_absent_from_universe_gives_none_fields():
    scan_df = pd.DataFrame([_scan_row("AAA", 1, 90.0)])
    port_df = pd.DataFrame([{"Ticker": "ZZZ", "Sector": "Tech", "Score": 50.0}])
    result = ranking.rank_holdings_in_universe(port_df, scan_df)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["Universe Rank"] is None
    assert row["Percentile"] is None
    assert row["Scanner Score"] is None
    assert row["Sector Rank"] == "—"
    assert row["Tier"] == "—"


# ─── rank_holdings_in_universe — sort order ─────────────────────────────────

def test_rank_holdings_sort_order_composite_desc_then_universe_rank_asc_nan_last():
    scan_df = pd.DataFrame([
        _scan_row("AAA", 1, 90.0),
        _scan_row("BBB", 2, 80.0),
    ])
    port_df = pd.DataFrame([
        {"Ticker": "AAA", "Sector": "Tech", "Score": 70.0},   # in universe, lower composite
        {"Ticker": "BBB", "Sector": "Tech", "Score": 90.0},   # in universe, higher composite
        {"Ticker": "ZZZ", "Sector": "Tech", "Score": 90.0},   # NOT in universe, tied composite w/ BBB
    ])
    result = ranking.rank_holdings_in_universe(port_df, scan_df)
    tickers_in_order = result["Ticker"].tolist()
    # Composite Score desc first: BBB/ZZZ (90) before AAA (70). Within the
    # 90-composite tie, Universe Rank asc with NaN last -> BBB (rank 2)
    # before ZZZ (no universe rank).
    assert tickers_in_order == ["BBB", "ZZZ", "AAA"]


# ─── sector_alternatives ─────────────────────────────────────────────────────

def _alt_row(ticker, score, signal="BUY"):
    return {"Ticker": ticker, "Score": score, "Sector": "Tech", "Signal": signal}


def test_sector_alternatives_empty_scan_df_returns_empty_list():
    assert ranking.sector_alternatives("AAA", "Tech", pd.DataFrame()) == []


def test_sector_alternatives_no_scanner_sector_returns_empty_list():
    scan_df = pd.DataFrame([_alt_row("AAA", 90.0)])
    assert ranking.sector_alternatives("AAA", "", scan_df) == []
    assert ranking.sector_alternatives("AAA", None, scan_df) == []


def test_sector_alternatives_excludes_given_ticker():
    scan_df = pd.DataFrame([
        _alt_row("AAA", 90.0),
        _alt_row("BBB", 80.0),
        _alt_row("CCC", 70.0),
    ])
    result = ranking.sector_alternatives("AAA", "Tech", scan_df, n=3)
    tickers = [r["ticker"] for r in result]
    assert "AAA" not in tickers
    assert tickers == ["BBB", "CCC"]


def test_sector_alternatives_caps_at_n_and_sorts_by_score_desc():
    scan_df = pd.DataFrame([
        _alt_row("AAA", 50.0),
        _alt_row("BBB", 90.0),
        _alt_row("CCC", 80.0),
        _alt_row("DDD", 70.0),
        _alt_row("EEE", 60.0),
    ])
    result = ranking.sector_alternatives("ZZZ", "Tech", scan_df, n=2)
    assert len(result) == 2
    assert [r["ticker"] for r in result] == ["BBB", "CCC"]
