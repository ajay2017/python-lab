"""Tests for stock_analyzer/discovery_universe.py — the broad Movers-scan
ticker net (`discovery_tickers`) and the static `DISCOVERY_UNIVERSE` data
structure it flattens. Pure list/set logic, no I/O. Previously zero test
coverage.
"""
from stock_analyzer import discovery_universe as du


# ─── DISCOVERY_UNIVERSE — well-formed data sanity check ─────────────────────

def test_discovery_universe_is_a_non_empty_dict_of_str_to_list_of_str():
    assert isinstance(du.DISCOVERY_UNIVERSE, dict)
    assert len(du.DISCOVERY_UNIVERSE) > 0
    for sector, tickers in du.DISCOVERY_UNIVERSE.items():
        assert isinstance(sector, str) and sector
        assert isinstance(tickers, list) and len(tickers) > 0
        assert all(isinstance(t, str) and t for t in tickers)


# ─── discovery_tickers — no exclude: full deduped flattened list ────────────

def test_discovery_tickers_no_exclude_returns_full_deduped_list():
    all_flat = [t.upper().strip() for lst in du.DISCOVERY_UNIVERSE.values() for t in lst]
    unique_count = len(set(all_flat))
    result = du.discovery_tickers()
    assert len(result) == unique_count
    assert len(result) == len(set(result))  # no duplicates in the output itself
    assert set(result) == set(all_flat)


def test_discovery_tickers_none_exclude_same_as_no_exclude():
    assert du.discovery_tickers(exclude=None) == du.discovery_tickers()


def test_discovery_tickers_empty_exclude_set_same_as_no_exclude():
    assert du.discovery_tickers(exclude=set()) == du.discovery_tickers()


# ─── discovery_tickers — exclude removes matches, case-insensitively ───────

def test_discovery_tickers_exclude_removes_exact_match():
    full = du.discovery_tickers()
    ticker = full[0]
    result = du.discovery_tickers(exclude={ticker})
    assert ticker not in result
    assert len(result) == len(full) - 1


def test_discovery_tickers_exclude_is_case_insensitive():
    full = du.discovery_tickers()
    ticker = full[0]
    result = du.discovery_tickers(exclude={ticker.lower()})
    assert ticker not in result
    assert len(result) == len(full) - 1


def test_discovery_tickers_exclude_mixed_case_still_matches():
    full = du.discovery_tickers()
    ticker = full[0]
    mixed = ticker[0].lower() + ticker[1:].upper()
    result = du.discovery_tickers(exclude={mixed})
    assert ticker not in result


def test_discovery_tickers_exclude_unrelated_ticker_changes_nothing():
    full = du.discovery_tickers()
    result = du.discovery_tickers(exclude={"ZZZZZ_NOT_A_REAL_TICKER"})
    assert result == full
