"""Tests for stock_analyzer/broker_sync.py's position-drift half.

This module had ZERO test coverage until 2026-08-23, despite `diff_positions`
being the app's only app-vs-broker reconciliation and about to become a 🏠 Home
decision surface. These tests pin CURRENT behaviour first, so the split into
`normalize_positions` + `diff_position_map` can be proven behaviour-preserving
rather than asserted to be.

The load-bearing cases are the OFFLINE SENTINEL (None must never collapse to
"no drift") and the TOLERANCE BOUNDARY (the comparison is `>`, not `>=`, so a
diff of exactly BROKER_DRIFT_SHARE_TOL is NOT drift).
"""
import pandas as pd

from stock_analyzer import broker_sync as bs
from stock_analyzer.constants import BROKER_DRIFT_SHARE_TOL


def _pos(ticker, units, kind="stock"):
    """A raw SnapTrade position dict in the shape the client returns."""
    return {"instrument": {"kind": kind, "symbol": ticker}, "units": units}


def _pdf(pairs):
    return pd.DataFrame([{"Ticker": t, "Shares": s} for t, s in pairs])


# ─── the offline sentinel ───────────────────────────────────────────────────

def test_none_positions_returns_none_not_an_empty_diff():
    """THE OFFLINE CONTRACT. A failed broker read must never render as
    'checked, no drift' — that is the collapse the whole sentinel exists for."""
    assert bs.diff_positions(None, _pdf([("AAA", 10)])) is None


def test_broker_holding_nothing_is_a_REAL_result_not_an_unknown():
    """Distinct from the above: an empty list means the broker responded and
    holds nothing. Every app holding is then genuinely app_only."""
    out = bs.diff_positions([], _pdf([("AAA", 10)]))
    assert out is not None
    assert [r["ticker"] for r in out["app_only"]] == ["AAA"]
    assert out["rh_only"] == [] and out["qty_mismatch"] == []


def test_empty_buckets_mean_checked_and_clean():
    out = bs.diff_positions([_pos("AAA", 10)], _pdf([("AAA", 10)]))
    assert out == {"rh_only": [], "app_only": [], "qty_mismatch": []}


# ─── the tolerance boundary ─────────────────────────────────────────────────

def test_diff_exactly_at_the_tolerance_is_NOT_drift():
    """The comparison is `>`, not `>=`. Pinned because an off-by-one here is
    the class that produces either float-noise spam or a missed real drift."""
    out = bs.diff_positions(
        [_pos("AAA", 10.0 + BROKER_DRIFT_SHARE_TOL)], _pdf([("AAA", 10.0)])
    )
    assert out["qty_mismatch"] == []


def test_diff_just_past_the_tolerance_IS_drift():
    out = bs.diff_positions(
        [_pos("AAA", 10.0 + BROKER_DRIFT_SHARE_TOL * 1.1)], _pdf([("AAA", 10.0)])
    )
    assert len(out["qty_mismatch"]) == 1


def test_float_noise_below_tolerance_never_fires():
    out = bs.diff_positions([_pos("AAA", 10.0000001)], _pdf([("AAA", 10.0)]))
    assert out["qty_mismatch"] == []


# ─── the real 2026-08-23 DELL case ──────────────────────────────────────────

def test_the_dell_share_drift_is_detected_with_a_signed_diff():
    """The live defect: broker held 20, app thought 24.
    `diff` is rh - app, so NEGATIVE means the app OVERSTATES the book."""
    out = bs.diff_positions([_pos("DELL", 20.0)], _pdf([("DELL", 24.0)]))
    row = out["qty_mismatch"][0]
    assert row["ticker"] == "DELL"
    assert row["rh_shares"] == 20.0
    assert row["app_shares"] == 24.0
    assert row["diff"] == -4.0


# ─── position normalization ─────────────────────────────────────────────────

def test_zero_unit_positions_are_skipped():
    """A closed position is not a holding; counting it would fabricate drift."""
    out = bs.diff_positions([_pos("AAA", 0)], _pdf([]))
    assert out["rh_only"] == []


def test_options_and_crypto_are_excluded():
    out = bs.diff_positions(
        [_pos("AAA", 5, kind="option"), _pos("BBB", 5, kind="crypto")], _pdf([])
    )
    assert out["rh_only"] == []


def test_adr_is_included_as_equity():
    """SAP/ASML trade as ADRs and are ordinary holdings."""
    out = bs.diff_positions([_pos("SAP", 5, kind="adr")], _pdf([]))
    assert [r["ticker"] for r in out["rh_only"]] == ["SAP"]


def test_missing_instrument_kind_is_treated_as_equity():
    out = bs.diff_positions([{"instrument": {"symbol": "AAA"}, "units": 5}], _pdf([]))
    assert [r["ticker"] for r in out["rh_only"]] == ["AAA"]


def test_nested_symbol_dict_shape_is_handled():
    out = bs.diff_positions(
        [{"instrument": {"kind": "stock", "symbol": {"symbol": "AAA"}}, "units": 5}],
        _pdf([]),
    )
    assert [r["ticker"] for r in out["rh_only"]] == ["AAA"]


def test_none_instrument_does_not_raise():
    out = bs.diff_positions([{"instrument": None, "symbol": "AAA", "units": 5}], _pdf([]))
    assert [r["ticker"] for r in out["rh_only"]] == ["AAA"]


def test_same_ticker_across_multiple_accounts_is_summed():
    """The user has 5 linked accounts; a ticker held in two must aggregate, or
    the combined position reads as a phantom shortfall against the app."""
    out = bs.diff_positions(
        [_pos("AAA", 6), _pos("AAA", 4)], _pdf([("AAA", 10)])
    )
    assert out["qty_mismatch"] == []


def test_tickers_are_uppercased_and_trimmed_on_both_sides():
    out = bs.diff_positions([_pos(" aaa ", 10)], _pdf([("AAA", 10)]))
    assert out == {"rh_only": [], "app_only": [], "qty_mismatch": []}


# ─── the app side ───────────────────────────────────────────────────────────

def test_empty_portfolio_makes_every_broker_position_rh_only():
    """Why the Account panel refuses to run without a loaded portfolio: diffing
    against an unloaded book fabricates a full set of rh_only rows."""
    out = bs.diff_positions([_pos("AAA", 5), _pos("BBB", 5)], _pdf([]))
    assert [r["ticker"] for r in out["rh_only"]] == ["AAA", "BBB"]


def test_none_portfolio_does_not_raise():
    out = bs.diff_positions([_pos("AAA", 5)], None)
    assert [r["ticker"] for r in out["rh_only"]] == ["AAA"]


def test_results_are_sorted_by_ticker():
    out = bs.diff_positions(
        [_pos("ZZZ", 1), _pos("AAA", 1), _pos("MMM", 1)], _pdf([])
    )
    assert [r["ticker"] for r in out["rh_only"]] == ["AAA", "MMM", "ZZZ"]


def test_all_three_buckets_populate_independently():
    out = bs.diff_positions(
        [_pos("BROKERONLY", 5), _pos("BOTH", 7)],
        _pdf([("APPONLY", 3), ("BOTH", 9)]),
    )
    assert [r["ticker"] for r in out["rh_only"]] == ["BROKERONLY"]
    assert [r["ticker"] for r in out["app_only"]] == ["APPONLY"]
    assert [r["ticker"] for r in out["qty_mismatch"]] == ["BOTH"]


# ─── the split must be behaviour-preserving ─────────────────────────────────

def test_split_is_equivalent_to_the_original_for_every_fixture():
    """diff_position_map(normalize_positions(x), pdf) == diff_positions(x, pdf).
    Proves the refactor preserved behaviour instead of asserting it."""
    fixtures = [
        (None, _pdf([("AAA", 10)])),
        ([], _pdf([("AAA", 10)])),
        ([_pos("AAA", 10)], _pdf([("AAA", 10)])),
        ([_pos("DELL", 20.0)], _pdf([("DELL", 24.0)])),
        ([_pos("AAA", 6), _pos("AAA", 4)], _pdf([("AAA", 10)])),
        ([_pos("AAA", 5, kind="option")], _pdf([])),
        ([_pos("AAA", 0)], _pdf([])),
        ([_pos("ZZZ", 1), _pos("AAA", 1)], _pdf([("MMM", 2)])),
        ([_pos("AAA", 10.0 + BROKER_DRIFT_SHARE_TOL)], _pdf([("AAA", 10.0)])),
    ]
    for raw, pdf in fixtures:
        assert bs.diff_position_map(bs.normalize_positions(raw), pdf) == \
               bs.diff_positions(raw, pdf), raw


def test_normalize_positions_none_in_none_out():
    assert bs.normalize_positions(None) is None


def test_normalize_positions_empty_is_a_real_empty_map_not_none():
    assert bs.normalize_positions([]) == {}


# ─── dollar impact and its sign ─────────────────────────────────────────────

def test_app_only_overstates_the_book_by_its_full_value():
    diff = bs.diff_positions([], _pdf([("AAA", 10)]))
    out = bs.drift_dollar_impact(diff, {"AAA": 50.0})
    assert out["overstated"] == 500.0


def test_app_holding_more_than_broker_overstates():
    """The real DELL direction: app 24, broker 20 -> 4 phantom shares."""
    diff = bs.diff_positions([_pos("DELL", 20.0)], _pdf([("DELL", 24.0)]))
    out = bs.drift_dollar_impact(diff, {"DELL": 272.90})
    assert out["overstated"] == round(4 * 272.90, 2)


def test_app_holding_less_than_broker_understates():
    diff = bs.diff_positions([_pos("AAA", 14.0)], _pdf([("AAA", 10.0)]))
    out = bs.drift_dollar_impact(diff, {"AAA": 100.0})
    assert out["overstated"] == -400.0


def test_rh_only_is_reported_as_shares_never_as_zero_dollars():
    """It isn't in the book so it has no price. Printing $0 would read as
    'no impact', which is worse than saying the value is missing."""
    # Broker holds AAA (matching) plus NEW; the app knows only AAA. So the ONLY
    # discrepancy is rh_only, and it must contribute no dollars either way.
    diff = bs.diff_positions([_pos("AAA", 1), _pos("NEW", 8)], _pdf([("AAA", 1)]))
    out = bs.drift_dollar_impact(diff, {"AAA": 10.0})
    assert out["rh_only_shares"] == [{"ticker": "NEW", "shares": 8.0}]
    assert out["overstated"] == 0.0
    assert out["priced"] == [] and out["unpriced"] == []


def test_an_unpriced_drifted_ticker_is_listed_not_valued_at_zero():
    diff = bs.diff_positions([], _pdf([("AAA", 10)]))
    out = bs.drift_dollar_impact(diff, {})
    assert out["overstated"] == 0.0
    assert out["unpriced"] == [{"ticker": "AAA", "shares": 10.0}]
    assert out["priced"] == []


def test_impact_of_no_diff_is_zero():
    assert bs.drift_dollar_impact(None, {})["overstated"] == 0.0
    assert bs.drift_dollar_impact(
        {"rh_only": [], "app_only": [], "qty_mismatch": []}, {})["overstated"] == 0.0


# ─── decide_drift_banner — the fail-open invariants ─────────────────────────

_NOW = "2026-08-23T12:00:00+00:00"


def _snap(positions, captured_at=_NOW, all_ok=True):
    return {"positions": positions, "captured_at": captured_at,
            "all_accounts_ok": all_ok}


def test_no_snapshot_is_UNKNOWN_never_none():
    """The branch that would otherwise fail open into looking clean."""
    out = bs.decide_drift_banner(None, _pdf([("AAA", 10)]), _NOW, 25)
    assert out["state"] == "unknown"


def test_snapshot_with_null_positions_is_UNKNOWN():
    out = bs.decide_drift_banner(_snap(None), _pdf([("AAA", 10)]), _NOW, 25)
    assert out["state"] == "unknown"


def test_empty_holdings_renders_nothing_rather_than_fabricating_rh_only():
    """Diffing against an unloaded book would turn every broker position into a
    phantom rh_only. The Account panel refuses for the same reason."""
    out = bs.decide_drift_banner(_snap({"AAA": 10.0}), _pdf([]), _NOW, 25)
    assert out["state"] == "none"
    assert out["reason"] == "no_holdings"


def test_none_holdings_renders_nothing():
    out = bs.decide_drift_banner(_snap({"AAA": 10.0}), None, _NOW, 25)
    assert out["state"] == "none"


def test_clean_and_fresh_is_silent():
    """No green tick on every render -- that is the noise that trains a user
    past the amber one."""
    out = bs.decide_drift_banner(_snap({"AAA": 10.0}), _pdf([("AAA", 10)]), _NOW, 25)
    assert out["state"] == "none"
    assert out["reason"] == "clean"


def test_clean_but_STALE_never_reads_as_a_clean_bill_of_health():
    """The easiest branch to get wrong: 'we did not check' rendering as
    'no problem'."""
    old = "2026-08-01T12:00:00+00:00"
    out = bs.decide_drift_banner(_snap({"AAA": 10.0}, captured_at=old),
                                 _pdf([("AAA", 10)]), _NOW, 25)
    assert out["state"] == "stale_clean"
    assert out["is_stale"] is True


def test_clean_but_partial_account_coverage_is_not_a_clean_verdict():
    """A clean result cannot rule out drift in an account that never responded."""
    out = bs.decide_drift_banner(_snap({"AAA": 10.0}, all_ok=False),
                                 _pdf([("AAA", 10)]), _NOW, 25)
    assert out["state"] == "stale_clean"
    assert out["all_accounts_ok"] is False


def test_unreadable_captured_at_counts_as_stale_not_fresh():
    """An unknown age treated as fresh is the fail-open direction."""
    out = bs.decide_drift_banner(_snap({"AAA": 10.0}, captured_at="garbage"),
                                 _pdf([("AAA", 10)]), _NOW, 25)
    assert out["state"] == "stale_clean"


def test_missing_captured_at_counts_as_stale():
    out = bs.decide_drift_banner(_snap({"AAA": 10.0}, captured_at=None),
                                 _pdf([("AAA", 10)]), _NOW, 25)
    assert out["state"] == "stale_clean"


def test_real_drift_reports_state_drift_with_the_dollar_impact():
    out = bs.decide_drift_banner(_snap({"DELL": 20.0}), _pdf([("DELL", 24.0)]),
                                 _NOW, 25, price_map={"DELL": 272.90})
    assert out["state"] == "drift"
    assert out["impact"]["overstated"] == round(4 * 272.90, 2)


def test_a_STALE_positive_is_still_reported_because_it_is_still_true():
    old = "2026-08-01T12:00:00+00:00"
    out = bs.decide_drift_banner(_snap({"DELL": 20.0}, captured_at=old),
                                 _pdf([("DELL", 24.0)]), _NOW, 25)
    assert out["state"] == "drift"
    assert out["is_stale"] is True


def test_broker_holding_nothing_is_a_real_result_and_reports_drift():
    """An all-cash broker against a non-empty book is genuine, loud drift --
    not an 'unknown'."""
    out = bs.decide_drift_banner(_snap({}), _pdf([("AAA", 10)]), _NOW, 25)
    assert out["state"] == "drift"
    assert [r["ticker"] for r in out["diff"]["app_only"]] == ["AAA"]


def test_the_user_fixing_the_book_clears_the_banner_against_a_stale_snapshot():
    """The reason the BROKER side is persisted and the BOOK side is live: a fix
    must clear the warning immediately, not wait for the next cron."""
    old = "2026-08-01T12:00:00+00:00"
    snap = _snap({"DELL": 20.0}, captured_at=old)
    assert bs.decide_drift_banner(snap, _pdf([("DELL", 24.0)]), _NOW, 25)["state"] == "drift"
    # user corrects the holding to match the broker
    assert bs.decide_drift_banner(snap, _pdf([("DELL", 20.0)]), _NOW, 25)["state"] == "stale_clean"


# ─── a correctly-logged trade must never read as a missing one ──────────────
# The broker snapshot refreshes once daily; the book is diffed live. That
# asymmetry lets a user's FIX clear the banner instantly -- but its mirror is
# that logging a perfectly correct trade makes the book move ahead of the
# snapshot and look like drift. Without this split, the app's most common daily
# workflow produced "overstated by ~$5,400 -- fix a missing trade" on a morning
# the user did everything right.

def test_a_ticker_traded_since_the_snapshot_is_awaiting_sync_not_drift():
    out = bs.decide_drift_banner(
        _snap({"NVDA": 10.0}), _pdf([("NVDA", 40.0)]), _NOW, 25,
        price_map={"NVDA": 180.0}, recent_trade_tickers=["NVDA"],
    )
    assert out["state"] == "awaiting_sync"
    assert out["awaiting_sync"] == ["NVDA"]
    # And crucially: no dollar accusation.
    assert out["impact"]["overstated"] == 0.0


def test_the_same_drift_without_a_recent_trade_IS_reported():
    """The guard must not swallow genuine drift -- only drift the user has
    already explained by logging something."""
    out = bs.decide_drift_banner(
        _snap({"NVDA": 10.0}), _pdf([("NVDA", 40.0)]), _NOW, 25,
        price_map={"NVDA": 180.0},
    )
    assert out["state"] == "drift"
    assert out["impact"]["overstated"] == 30 * 180.0


def test_real_drift_still_surfaces_alongside_an_awaiting_sync_ticker():
    """A mixed book: one ticker explained, one not. The unexplained one must
    still produce the full warning, and the dollar figure must EXCLUDE the
    explained one or it would overstate the problem."""
    out = bs.decide_drift_banner(
        _snap({"NVDA": 10.0, "DELL": 20.0}),
        _pdf([("NVDA", 40.0), ("DELL", 24.0)]), _NOW, 25,
        price_map={"NVDA": 180.0, "DELL": 100.0},
        recent_trade_tickers=["NVDA"],
    )
    assert out["state"] == "drift"
    assert [r["ticker"] for r in out["diff"]["qty_mismatch"]] == ["DELL"]
    assert out["awaiting_sync"] == ["NVDA"]
    assert out["impact"]["overstated"] == 4 * 100.0     # DELL only


def test_awaiting_sync_matches_tickers_case_insensitively():
    out = bs.decide_drift_banner(
        _snap({"NVDA": 10.0}), _pdf([("NVDA", 40.0)]), _NOW, 25,
        recent_trade_tickers=["nvda"],
    )
    assert out["state"] == "awaiting_sync"


def test_no_recent_trades_leaves_the_diff_untouched():
    for empty in (None, [], ()):
        out = bs.decide_drift_banner(
            _snap({"DELL": 20.0}), _pdf([("DELL", 24.0)]), _NOW, 25,
            recent_trade_tickers=empty,
        )
        assert out["state"] == "drift", empty
        assert out["awaiting_sync"] == []


def test_split_awaiting_sync_handles_a_none_diff():
    real, awaiting = bs.split_awaiting_sync(None, ["AAA"])
    assert real == {"rh_only": [], "app_only": [], "qty_mismatch": []}
    assert awaiting == []


def test_split_awaiting_sync_covers_all_three_buckets():
    diff = {
        "rh_only":      [{"ticker": "AAA", "shares": 1.0}],
        "app_only":     [{"ticker": "BBB", "shares": 2.0}],
        "qty_mismatch": [{"ticker": "CCC", "rh_shares": 1.0, "app_shares": 3.0,
                          "diff": -2.0}],
    }
    real, awaiting = bs.split_awaiting_sync(diff, ["AAA", "BBB", "CCC"])
    assert awaiting == ["AAA", "BBB", "CCC"]
    assert real == {"rh_only": [], "app_only": [], "qty_mismatch": []}


def test_a_clean_book_with_recent_trades_stays_silent():
    """Trading today must not by itself produce a message."""
    out = bs.decide_drift_banner(
        _snap({"NVDA": 40.0}), _pdf([("NVDA", 40.0)]), _NOW, 25,
        recent_trade_tickers=["NVDA"],
    )
    assert out["state"] == "none"
