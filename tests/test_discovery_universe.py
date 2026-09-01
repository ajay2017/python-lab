"""Tests for stock_analyzer/discovery_universe.py — `discovery_tickers`'s
flatten/dedup/exclude logic. Pure list/set logic, no I/O.

App Settings (docs/plans/app-settings.md) Commit 3, 2026-09-01 — the
module-level `DISCOVERY_UNIVERSE` dict this file used to test directly was
deleted; the roster now lives DB-backed in Supabase `reference_tables` and is
edited through the ⚙️ App Settings UI, which has its own validation and
history trail. These tests exercise `discovery_tickers`'s OWN behaviour
(dedup, exclude, case-insensitivity) against a small local fixture universe
rather than depending on real production roster contents — a deliberate
test-hygiene improvement this cutover forces, not a loss of coverage: the
function's logic is identical regardless of which payload it's handed.
"""
from stock_analyzer import discovery_universe as du

# Minimal fixture universe: two buckets, one ticker (BBB1) duplicated across
# both so tests can exercise cross-bucket dedup, not just within-bucket dedup.
_FIXTURE_UNIVERSE = {
    "Sector A": ["AAA1", "AAA2", "BBB1"],
    "Sector B": ["BBB1", "CCC1"],
}


# ─── discovery_tickers — no exclude: full deduped flattened list ────────────

def test_discovery_tickers_no_exclude_returns_full_deduped_list():
    all_flat = [t.upper().strip() for lst in _FIXTURE_UNIVERSE.values() for t in lst]
    unique_count = len(set(all_flat))
    result = du.discovery_tickers(universe=_FIXTURE_UNIVERSE)
    assert len(result) == unique_count
    assert len(result) == len(set(result))  # no duplicates in the output itself
    assert set(result) == set(all_flat)


def test_discovery_tickers_none_exclude_same_as_no_exclude():
    assert (
        du.discovery_tickers(exclude=None, universe=_FIXTURE_UNIVERSE)
        == du.discovery_tickers(universe=_FIXTURE_UNIVERSE)
    )


def test_discovery_tickers_empty_exclude_set_same_as_no_exclude():
    assert (
        du.discovery_tickers(exclude=set(), universe=_FIXTURE_UNIVERSE)
        == du.discovery_tickers(universe=_FIXTURE_UNIVERSE)
    )


# ─── discovery_tickers — exclude removes matches, case-insensitively ───────

def test_discovery_tickers_exclude_removes_exact_match():
    full = du.discovery_tickers(universe=_FIXTURE_UNIVERSE)
    ticker = full[0]
    result = du.discovery_tickers(exclude={ticker}, universe=_FIXTURE_UNIVERSE)
    assert ticker not in result
    assert len(result) == len(full) - 1


def test_discovery_tickers_exclude_is_case_insensitive():
    full = du.discovery_tickers(universe=_FIXTURE_UNIVERSE)
    ticker = full[0]
    result = du.discovery_tickers(exclude={ticker.lower()}, universe=_FIXTURE_UNIVERSE)
    assert ticker not in result
    assert len(result) == len(full) - 1


def test_discovery_tickers_exclude_mixed_case_still_matches():
    full = du.discovery_tickers(universe=_FIXTURE_UNIVERSE)
    ticker = full[0]
    mixed = ticker[0].lower() + ticker[1:].upper()
    result = du.discovery_tickers(exclude={mixed}, universe=_FIXTURE_UNIVERSE)
    assert ticker not in result


def test_discovery_tickers_exclude_unrelated_ticker_changes_nothing():
    full = du.discovery_tickers(universe=_FIXTURE_UNIVERSE)
    result = du.discovery_tickers(
        exclude={"ZZZZZ_NOT_A_REAL_TICKER"}, universe=_FIXTURE_UNIVERSE,
    )
    assert result == full


# ─── discovery_tickers — `universe` is required, no fallback ───────────────

def test_discovery_tickers_uses_explicit_universe_param():
    fake_universe = {"FakeSector": ["ZZZFAKE"]}
    result = du.discovery_tickers(universe=fake_universe)
    assert result == ["ZZZFAKE"]


def test_discovery_tickers_empty_universe_param_returns_empty_no_fallback():
    # An explicit {} (the real caller's contract on a resolve_universe
    # failure) must return nothing — there is no module-level dict left to
    # fall back to.
    result = du.discovery_tickers(universe={})
    assert result == []
