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


# ─── discovery_tickers — App Settings (Commit 2): explicit `universe` ───────
# param, not the module-level default, must be what the real importer path
# actually reads.

def test_discovery_tickers_uses_explicit_universe_param_not_module_default():
    fake_universe = {"FakeSector": ["ZZZFAKE"]}
    result = du.discovery_tickers(universe=fake_universe)
    assert result == ["ZZZFAKE"]


def test_discovery_tickers_empty_universe_param_returns_empty_no_fallback():
    # An explicit {} (the real caller's contract on a resolve_universe
    # failure) must never fall back to the real DISCOVERY_UNIVERSE dict.
    result = du.discovery_tickers(universe={})
    assert result == []


# ─── 2026-09-01 roster refresh — pins the exact approved diff ──────────────
# Removed: AI (C3.ai, $1.7B), LCID ($1.9B) — both sub-scale under this file's
# own liquidity rule. Added: IBM/CSCO (Software & Cloud), EBAY/WBD (Internet
# & Media), KR/ORLY (Consumer & Retail). Asserts the specific, deliberate
# diff — not a full-roster snapshot — so nothing silently rides along or
# regresses on the next refresh.

def test_discovery_2026_09_01_refresh_removals_and_additions():
    software = set(du.DISCOVERY_UNIVERSE["Software & Cloud"])
    internet = set(du.DISCOVERY_UNIVERSE["Internet & Media"])
    consumer = set(du.DISCOVERY_UNIVERSE["Consumer & Retail"])

    assert "AI" not in software, "C3.ai ($1.7B) should be removed as sub-scale"
    assert "LCID" not in consumer, "Lucid ($1.9B) should be removed as sub-scale"

    for added in ("IBM", "CSCO"):
        assert added in software, f"{added} should be a Software & Cloud candidate"
    for added in ("EBAY", "WBD"):
        assert added in internet, f"{added} should be an Internet & Media candidate"
    for added in ("KR", "ORLY"):
        assert added in consumer, f"{added} should be a Consumer & Retail candidate"
