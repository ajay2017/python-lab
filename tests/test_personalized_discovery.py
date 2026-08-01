"""
Tests for stock_analyzer/personalized_discovery.py — the "winner profile"
builder (Behavioral Fingerprint's backward-looking analysis run FORWARD) and
the candidate-match scorer it feeds. Zero coverage before this batch.
"""
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import personalized_discovery as pd_mod


def _lot(ticker, buy_date, sell_date=None, is_gain=True, pnl_pct=10.0, shares=10.0):
    return {
        "ticker": ticker, "buy_date": buy_date,
        "sell_date": sell_date or buy_date, "is_gain": is_gain,
        "pnl_pct": pnl_pct, "shares": shares,
    }


def _rec(ticker, rec_date, rec_type="new_pick", acted_on=True,
         composite_score=70.0, momentum_score=80.0, sector="Technology"):
    return {
        "ticker": ticker, "rec_date": rec_date, "rec_type": rec_type,
        "acted_on": acted_on, "composite_score": composite_score,
        "momentum_score": momentum_score, "sector": sector,
    }


# ─── build_winner_profile ────────────────────────────────────────────────────

def test_build_winner_profile_none_on_empty_closed_lots():
    assert pd_mod.build_winner_profile(pd.DataFrame(), [], min_n=1) is None
    assert pd_mod.build_winner_profile(None, [], min_n=1) is None


def test_build_winner_profile_none_below_min_n():
    closed_lots = pd.DataFrame([_lot("AAA", date(2026, 1, 5))])
    matched = [_rec("AAA", date(2026, 1, 5))]
    assert pd_mod.build_winner_profile(closed_lots, matched, min_n=2) is None


def test_build_winner_profile_withholds_at_zero_matches():
    # A winning lot with NO matching rec (e.g. a manually-logged trade) can't
    # contribute to the profile.
    closed_lots = pd.DataFrame([_lot("AAA", date(2026, 1, 5))])
    assert pd_mod.build_winner_profile(closed_lots, [], min_n=1) is None


def test_build_winner_profile_excludes_losing_lots():
    closed_lots = pd.DataFrame([
        _lot("AAA", date(2026, 1, 5), is_gain=True, pnl_pct=15.0),
        _lot("BBB", date(2026, 1, 6), is_gain=False, pnl_pct=-8.0),
    ])
    matched = [
        _rec("AAA", date(2026, 1, 5)),
        _rec("BBB", date(2026, 1, 6)),
    ]
    profile = pd_mod.build_winner_profile(closed_lots, matched, min_n=1)
    assert profile["n"] == 1


def test_build_winner_profile_null_pnl_pct_is_not_a_winner():
    # Regression: build_closed_lots's `is_gain = (pnl_abs or 0.0) >= 0`
    # evaluates True even when pnl_abs/pnl_pct is None (missing price data)
    # -- is_gain ALONE must never be trusted; a null pnl_pct must be excluded
    # even though is_gain reads True.
    closed_lots = pd.DataFrame([_lot("AAA", date(2026, 1, 5), is_gain=True, pnl_pct=None)])
    matched = [_rec("AAA", date(2026, 1, 5))]
    assert pd_mod.build_winner_profile(closed_lots, matched, min_n=1) is None


def test_build_winner_profile_scaled_out_entry_counts_once():
    # Regression: build_closed_lots emits one row per FIFO-matched SELL
    # fragment -- a single entry scaled out across 3 profitable sells must
    # still count as ONE winning entry (one sample), not three.
    closed_lots = pd.DataFrame([
        _lot("AAA", date(2026, 1, 5), sell_date=date(2026, 1, 10), pnl_pct=5.0, shares=3.0),
        _lot("AAA", date(2026, 1, 5), sell_date=date(2026, 1, 15), pnl_pct=8.0, shares=3.0),
        _lot("AAA", date(2026, 1, 5), sell_date=date(2026, 1, 20), pnl_pct=12.0, shares=4.0),
    ])
    matched = [_rec("AAA", date(2026, 1, 5), composite_score=70.0, sector="Technology")]
    profile = pd_mod.build_winner_profile(closed_lots, matched, min_n=1)
    assert profile["n"] == 1
    # A single entry's score must not be triple-weighted into the band or
    # the top_sectors >=2 threshold.
    assert profile["composite_low"] == pytest.approx(70.0)
    assert profile["composite_high"] == pytest.approx(70.0)
    assert profile["top_sectors"] == {"Technology"}


def test_build_winner_profile_excludes_non_actionable_rec_type():
    closed_lots = pd.DataFrame([_lot("AAA", date(2026, 1, 5))])
    matched = [_rec("AAA", date(2026, 1, 5), rec_type="more_buy_candidates")]
    assert pd_mod.build_winner_profile(closed_lots, matched, min_n=1) is None


def test_build_winner_profile_excludes_not_acted_on():
    closed_lots = pd.DataFrame([_lot("AAA", date(2026, 1, 5))])
    matched = [_rec("AAA", date(2026, 1, 5), acted_on=False)]
    assert pd_mod.build_winner_profile(closed_lots, matched, min_n=1) is None


def test_build_winner_profile_join_requires_exact_ticker_and_date():
    closed_lots = pd.DataFrame([_lot("AAA", date(2026, 1, 5))])
    # Wrong date -> no join
    matched_wrong_date = [_rec("AAA", date(2026, 1, 6))]
    assert pd_mod.build_winner_profile(closed_lots, matched_wrong_date, min_n=1) is None
    # Wrong ticker -> no join
    matched_wrong_ticker = [_rec("BBB", date(2026, 1, 5))]
    assert pd_mod.build_winner_profile(closed_lots, matched_wrong_ticker, min_n=1) is None


def test_build_winner_profile_percentile_bands():
    closed_lots = pd.DataFrame([
        _lot("AAA", date(2026, 1, 1)),
        _lot("BBB", date(2026, 1, 2)),
        _lot("CCC", date(2026, 1, 3)),
        _lot("DDD", date(2026, 1, 4)),
    ])
    matched = [
        _rec("AAA", date(2026, 1, 1), composite_score=60, momentum_score=70),
        _rec("BBB", date(2026, 1, 2), composite_score=70, momentum_score=80),
        _rec("CCC", date(2026, 1, 3), composite_score=80, momentum_score=90),
        _rec("DDD", date(2026, 1, 4), composite_score=90, momentum_score=100),
    ]
    profile = pd_mod.build_winner_profile(closed_lots, matched, min_n=4, pctl_low=25, pctl_high=75)
    assert profile["n"] == 4
    # pandas .quantile() default linear interpolation over [60,70,80,90]:
    # 25th pct -> 67.5, 75th pct -> 82.5 (and the momentum series is the same
    # shape shifted +10, so its bands shift +10 too).
    assert profile["composite_low"] == pytest.approx(67.5)
    assert profile["composite_high"] == pytest.approx(82.5)
    assert profile["momentum_low"] == pytest.approx(77.5)
    assert profile["momentum_high"] == pytest.approx(92.5)


def test_build_winner_profile_top_sectors_repeated_wins():
    closed_lots = pd.DataFrame([
        _lot("AAA", date(2026, 1, 1)),
        _lot("BBB", date(2026, 1, 2)),
        _lot("CCC", date(2026, 1, 3)),
    ])
    matched = [
        _rec("AAA", date(2026, 1, 1), sector="Technology"),
        _rec("BBB", date(2026, 1, 2), sector="Technology"),
        _rec("CCC", date(2026, 1, 3), sector="Healthcare"),
    ]
    profile = pd_mod.build_winner_profile(closed_lots, matched, min_n=3)
    assert profile["top_sectors"] == {"Technology"}


def test_build_winner_profile_top_sectors_falls_back_to_most_common_when_none_repeat():
    closed_lots = pd.DataFrame([
        _lot("AAA", date(2026, 1, 1)),
        _lot("BBB", date(2026, 1, 2)),
    ])
    matched = [
        _rec("AAA", date(2026, 1, 1), sector="Technology"),
        _rec("BBB", date(2026, 1, 2), sector="Healthcare"),
    ]
    profile = pd_mod.build_winner_profile(closed_lots, matched, min_n=2)
    assert len(profile["top_sectors"]) == 1
    assert profile["top_sectors"].issubset({"Technology", "Healthcare"})


def test_build_winner_profile_missing_scores_leave_band_none():
    closed_lots = pd.DataFrame([_lot("AAA", date(2026, 1, 1))])
    matched = [_rec("AAA", date(2026, 1, 1), composite_score=None, momentum_score=None, sector=None)]
    profile = pd_mod.build_winner_profile(closed_lots, matched, min_n=1)
    assert profile["composite_low"] is None
    assert profile["momentum_low"] is None
    assert profile["top_sectors"] == set()
    assert profile["n"] == 1


# ─── score_candidate_match ───────────────────────────────────────────────────

def test_score_candidate_match_none_profile_returns_empty():
    result = pd_mod.score_candidate_match(70, 80, "Technology", None)
    assert result == {"matched_traits": [], "n_matched": 0}


def test_score_candidate_match_all_traits_match():
    profile = {
        "composite_low": 60, "composite_high": 80,
        "momentum_low": 70, "momentum_high": 90,
        "top_sectors": {"Technology"},
    }
    result = pd_mod.score_candidate_match(70, 80, "Technology", profile)
    assert set(result["matched_traits"]) == {"composite tier", "momentum", "sector"}
    assert result["n_matched"] == 3


def test_score_candidate_match_composite_out_of_band():
    profile = {
        "composite_low": 60, "composite_high": 80,
        "momentum_low": None, "momentum_high": None,
        "top_sectors": set(),
    }
    result = pd_mod.score_candidate_match(50, None, None, profile)
    assert result["matched_traits"] == []
    assert result["n_matched"] == 0


def test_score_candidate_match_sector_not_in_top_sectors():
    profile = {
        "composite_low": None, "composite_high": None,
        "momentum_low": None, "momentum_high": None,
        "top_sectors": {"Healthcare"},
    }
    result = pd_mod.score_candidate_match(None, None, "Technology", profile)
    assert result["matched_traits"] == []


def test_score_candidate_match_missing_candidate_fields_are_safe():
    profile = {
        "composite_low": 60, "composite_high": 80,
        "momentum_low": 70, "momentum_high": 90,
        "top_sectors": {"Technology"},
    }
    result = pd_mod.score_candidate_match(None, None, None, profile)
    assert result == {"matched_traits": [], "n_matched": 0}


def test_score_candidate_match_boundary_values_are_inclusive():
    profile = {
        "composite_low": 60, "composite_high": 80,
        "momentum_low": 70, "momentum_high": 90,
        "top_sectors": set(),
    }
    result = pd_mod.score_candidate_match(60, 90, None, profile)
    assert set(result["matched_traits"]) == {"composite tier", "momentum"}
