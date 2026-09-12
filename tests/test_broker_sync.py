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
from stock_analyzer.constants import BROKER_DRIFT_SHARE_TOL, INCOME_EVENT_DEDUP_DATE_TOL_DAYS
import pytest

pytestmark = pytest.mark.fast


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


# ─── annotate_pending_reconciliation ────────────────────────────────────────
# Position Drift and Pending Imports check different things at different
# granularities (net position vs. exact-key per-transaction match), so a
# pending row can legitimately persist even when its ticker's drift is
# clean. This function narrates that already-computed drift fact next to
# the pending row — the load-bearing case is that "drift unavailable" must
# never collapse into "reconciles".

def _pending_row(ticker, **extra):
    return {"id": 1, "ticker": ticker, "action": "BUY", "shares": 1.0,
            "price": 100.0, "trade_date": "2026-08-20", **extra}


def test_drift_none_flags_every_row_as_unknown():
    out = bs.annotate_pending_reconciliation([_pending_row("AAA"), _pending_row("BBB")], None)
    assert [r["likely_reconciled"] for r in out] == [None, None]


def test_ticker_with_no_drift_bucket_membership_flags_true():
    drift = {"rh_only": [], "app_only": [], "qty_mismatch": []}
    out = bs.annotate_pending_reconciliation([_pending_row("AAA")], drift)
    assert out[0]["likely_reconciled"] is True


def test_ticker_present_in_rh_only_flags_false():
    drift = {"rh_only": [{"ticker": "AAA", "shares": 5.0}], "app_only": [], "qty_mismatch": []}
    out = bs.annotate_pending_reconciliation([_pending_row("AAA")], drift)
    assert out[0]["likely_reconciled"] is False


def test_ticker_present_in_app_only_flags_false():
    drift = {"rh_only": [], "app_only": [{"ticker": "AAA", "shares": 5.0}], "qty_mismatch": []}
    out = bs.annotate_pending_reconciliation([_pending_row("AAA")], drift)
    assert out[0]["likely_reconciled"] is False


def test_ticker_present_in_qty_mismatch_flags_false():
    drift = {"rh_only": [], "app_only": [],
             "qty_mismatch": [{"ticker": "AAA", "rh_shares": 5.0, "app_shares": 4.0, "diff": 1.0}]}
    out = bs.annotate_pending_reconciliation([_pending_row("AAA")], drift)
    assert out[0]["likely_reconciled"] is False


def test_only_the_drifted_ticker_is_flagged_false_others_stay_true():
    drift = {"rh_only": [{"ticker": "AAA", "shares": 5.0}], "app_only": [], "qty_mismatch": []}
    out = bs.annotate_pending_reconciliation([_pending_row("AAA"), _pending_row("BBB")], drift)
    flags = {r["ticker"]: r["likely_reconciled"] for r in out}
    assert flags == {"AAA": False, "BBB": True}


def test_empty_pending_returns_empty_list():
    assert bs.annotate_pending_reconciliation([], None) == []
    assert bs.annotate_pending_reconciliation([], {"rh_only": [], "app_only": [], "qty_mismatch": []}) == []


def test_does_not_mutate_input_list_or_rows():
    pending = [_pending_row("AAA")]
    original = dict(pending[0])
    bs.annotate_pending_reconciliation(pending, None)
    assert pending == [original]


# ─── find_pending_match_candidates ──────────────────────────────────────────
# A date-TOLERANT sibling of classify_transactions' exact-date Tier-2 dedup —
# read-only, display-only, never backfills anything. The load-bearing cases:
# a match outside the window is not returned, a trade already linked via
# broker_txn_id is excluded from candidacy, and no trades_df fails soft.

def _trade_row(trade_id, ticker, action, shares, price, traded_at, broker_txn_id=None):
    return {"id": trade_id, "ticker": ticker, "action": action, "shares": shares,
            "price": price, "traded_at": traded_at, "broker_txn_id": broker_txn_id}


def test_exact_day_match_is_found_with_zero_days_off():
    trades = pd.DataFrame([_trade_row(1, "AAA", "BUY", 1.0, 100.0, "2026-08-20T10:00:00Z")])
    out = bs.find_pending_match_candidates([_pending_row("AAA", trade_date="2026-08-20")], trades)
    assert out[1] == {"trade_id": 1, "traded_at": "2026-08-20", "days_off": 0}


def test_a_one_day_off_match_is_found_within_the_default_window():
    trades = pd.DataFrame([_trade_row(1, "AAA", "BUY", 1.0, 100.0, "2026-08-21T10:00:00Z")])
    out = bs.find_pending_match_candidates([_pending_row("AAA", trade_date="2026-08-20")], trades)
    assert out[1]["days_off"] == 1


def test_a_match_outside_the_window_is_not_returned():
    trades = pd.DataFrame([_trade_row(1, "AAA", "BUY", 1.0, 100.0, "2026-08-25T10:00:00Z")])
    out = bs.find_pending_match_candidates(
        [_pending_row("AAA", trade_date="2026-08-20")], trades, window_days=3
    )
    assert out == {}


def test_a_trade_already_linked_via_broker_txn_id_is_excluded():
    trades = pd.DataFrame([_trade_row(1, "AAA", "BUY", 1.0, 100.0, "2026-08-20T10:00:00Z",
                                       broker_txn_id="already-linked")])
    out = bs.find_pending_match_candidates([_pending_row("AAA", trade_date="2026-08-20")], trades)
    assert out == {}


def test_multiple_candidates_picks_the_closest_by_date():
    trades = pd.DataFrame([
        _trade_row(1, "AAA", "BUY", 1.0, 100.0, "2026-08-22T10:00:00Z"),
        _trade_row(2, "AAA", "BUY", 1.0, 100.0, "2026-08-20T10:00:00Z"),
    ])
    out = bs.find_pending_match_candidates([_pending_row("AAA", trade_date="2026-08-20")], trades)
    assert out[1] == {"trade_id": 2, "traded_at": "2026-08-20", "days_off": 0}


def test_no_trades_df_returns_empty_dict_without_raising():
    assert bs.find_pending_match_candidates([_pending_row("AAA")], None) == {}


def test_empty_trades_df_returns_empty_dict():
    assert bs.find_pending_match_candidates([_pending_row("AAA")], pd.DataFrame()) == {}


def test_empty_pending_returns_empty_dict():
    trades = pd.DataFrame([_trade_row(1, "AAA", "BUY", 1.0, 100.0, "2026-08-20T10:00:00Z")])
    assert bs.find_pending_match_candidates([], trades) == {}


def test_mismatched_shares_does_not_match():
    trades = pd.DataFrame([_trade_row(1, "AAA", "BUY", 2.0, 100.0, "2026-08-20T10:00:00Z")])
    out = bs.find_pending_match_candidates([_pending_row("AAA", trade_date="2026-08-20")], trades)
    assert out == {}


def test_missing_broker_txn_id_column_is_treated_as_backward_compatible():
    trades = pd.DataFrame([{"id": 1, "ticker": "AAA", "action": "BUY", "shares": 1.0,
                             "price": 100.0, "traded_at": "2026-08-20T10:00:00Z"}])
    out = bs.find_pending_match_candidates([_pending_row("AAA", trade_date="2026-08-20")], trades)
    assert out[1]["trade_id"] == 1


def test_one_logged_trade_is_never_suggested_to_two_pending_rows():
    """THE LOAD-BEARING CASE (2026-08-30 reviewer finding). Two real DCA buys
    at the same round price/shares, only one of which was ever logged: both
    pending rows key-match the SAME single logged trade. Suggesting it to
    both would give false confidence to dismiss a real, distinct, never-
    logged trade via the permanent 'Already logged' button. Only ONE pending
    row may claim the one available candidate."""
    trades = pd.DataFrame([_trade_row(1, "AAA", "BUY", 1.0, 100.0, "2026-08-20T10:00:00Z")])
    pending = [
        {"id": 10, "ticker": "AAA", "action": "BUY", "shares": 1.0, "price": 100.0,
         "trade_date": "2026-08-20"},
        {"id": 11, "ticker": "AAA", "action": "BUY", "shares": 1.0, "price": 100.0,
         "trade_date": "2026-08-21"},
    ]
    out = bs.find_pending_match_candidates(pending, trades)
    assert len(out) == 1
    assert set(out.keys()) <= {10, 11}


# ─── parse_robinhood_csv_income — MINT reclassification (2026-09-11) ───────
# MINT's Description on the owner's real RH statement is "Aggregated Margin
# Rate" — real margin interest charged, not a brokerage fee. It was misfiled
# as "fee" until this date, which fed a false $0 into capital_vs_margin.py's
# Interest Paid figure. GOLD/GMPC are pinned below as regression guards so
# this fix cannot accidentally touch either (GMPC is separately flagged as
# possibly also miscategorized but is NOT confirmed and is explicitly out of
# scope).

def _income_csv(code, amount_str, activity_date="01/15/2026", instrument=""):
    return (
        "Activity Date,Instrument,Trans Code,Amount\n"
        f"{activity_date},{instrument},{code},{amount_str}\n"
    )


def test_mint_maps_to_interest_not_fee():
    rows = bs.parse_robinhood_csv_income(_income_csv("MINT", "($36.59)"))
    assert len(rows) == 1
    assert rows[0]["event_type"] == "interest"
    assert rows[0]["amount"] == -36.59


def test_mint_amount_stays_negative_as_charged_on_real_statement_rows():
    """MINT is no longer in _RH_FEE_CODES, so the sign is NOT force-flipped —
    it is taken as-is, matching the real statement (already negative)."""
    rows = bs.parse_robinhood_csv_income(_income_csv("MINT", "(12.34)"))
    assert rows[0]["amount"] == -12.34


def test_mint_dedup_key_matches_the_csv_prefix_format():
    """Pins the exact snaptrade_txn_id shape the one-time production backfill
    SQL's LIKE pattern depends on: csv:{date}:{code}:{ticker}:{cents}."""
    rows = bs.parse_robinhood_csv_income(
        _income_csv("MINT", "($36.59)", activity_date="01/15/2026")
    )
    assert rows[0]["snaptrade_txn_id"] == "csv:2026-01-15:MINT::-3659"


def test_gold_still_maps_to_fee_regression_guard():
    """This fix must not touch GOLD ('Gold Subscription Fee') — confirmed
    correctly categorized already."""
    rows = bs.parse_robinhood_csv_income(_income_csv("GOLD", "5.99"))
    assert rows[0]["event_type"] == "fee"
    assert rows[0]["amount"] == -5.99  # still fee-coded, still force-negated


def test_gmpc_still_maps_to_interest_regression_guard():
    """This fix must not touch GMPC ('Gold Plan Credit') — flagged as
    possibly also miscategorized but explicitly out of scope for this fix."""
    rows = bs.parse_robinhood_csv_income(_income_csv("GMPC", "2.00"))
    assert rows[0]["event_type"] == "interest"
    assert rows[0]["amount"] == 2.00  # never fee-coded — sign untouched


def test_csv_income_rows_now_carry_raw_code():
    """F-268 follow-on: raw_code feeds income_event_subtype's cross-path
    match — every CSV row must carry the original Trans Code."""
    rows = bs.parse_robinhood_csv_income(_income_csv("CDIV", "5.00"))
    assert rows[0]["raw_code"] == "CDIV"


# ─── income_event_subtype — the cross-path canonical mapping (2026-09-11) ──
# The CSV path (Robinhood Trans Code) and the live SnapTrade path (`type`)
# use two disjoint vocabularies for describing the same real-world events.
# Matching on the raw string would never match across paths at all — these
# tests pin the merge/no-merge decisions the whole dedup depends on.

def test_cdiv_and_dividend_both_map_to_cash_dividend():
    """Proves the merge works: CSV's CDIV and SnapTrade's DIVIDEND are the
    SAME real event (a regular cash dividend) in two different vocabularies."""
    assert bs.income_event_subtype("CDIV", "dividend") == "cash_dividend"
    assert bs.income_event_subtype("DIVIDEND", "dividend") == "cash_dividend"


def test_mdiv_and_substitute_dividend_both_map_to_manufactured_dividend():
    assert bs.income_event_subtype("MDIV", "dividend") == "manufactured_dividend"
    assert bs.income_event_subtype("SUBSTITUTE_DIVIDEND", "dividend") == "manufactured_dividend"


def test_cdiv_and_mdiv_are_DIFFERENT_subtypes():
    """Proves no false merge: a regular cash dividend and a manufactured/
    substitute dividend are genuinely different real-world events and must
    never collapse into one subtype, even though both share event_type=
    "dividend" in the DB today."""
    assert bs.income_event_subtype("CDIV", "dividend") != bs.income_event_subtype("MDIV", "dividend")


def test_mint_is_its_own_subtype_not_plain_interest():
    """MINT (confirmed real margin interest) is deliberately NOT merged into
    plain "interest" — whether SnapTrade's live INTEREST type ever represents
    the same underlying charge is unconfirmed."""
    mint_subtype = bs.income_event_subtype("MINT", "interest")
    assert mint_subtype == "margin_interest"
    assert mint_subtype != bs.income_event_subtype("INT", "interest")
    assert mint_subtype != bs.income_event_subtype("INTEREST", "interest")


def test_unrecognized_raw_code_falls_back_to_event_type():
    assert bs.income_event_subtype("SOME_FUTURE_CODE", "fee") == "fee"


def test_none_raw_code_falls_back_to_event_type():
    """Legacy rows persisted before the raw_code column existed."""
    assert bs.income_event_subtype(None, "interest") == "interest"


# ─── dedupe_income_events — read-side cross-path duplicate collapse ────────

def _ev(ticker, event_type, raw_code, amount, event_date, txn_id):
    return {
        "snaptrade_txn_id": txn_id,
        "event_type": event_type,
        "raw_code": raw_code,
        "ticker": ticker,
        "amount": amount,
        "event_date": event_date,
    }


def test_empty_and_none_input_returns_empty_list():
    assert bs.dedupe_income_events([]) == []


def test_no_duplicates_returns_all_rows_unchanged():
    events = [
        _ev("AAPL", "dividend", "CDIV", 5.00, "2026-06-01", "csv:1"),
        _ev("MSFT", "dividend", "CDIV", 3.00, "2026-06-01", "csv:2"),
    ]
    out = bs.dedupe_income_events(events)
    assert len(out) == 2


def test_cross_vocabulary_duplicate_is_collapsed_csv_wins():
    """The load-bearing case: a CDIV CSV row and a DIVIDEND live-sync row for
    the SAME real event (same ticker, same signed amount, dates within
    tolerance) must collapse to ONE row, and the CSV-sourced one must survive
    per the user's own stated tie-break preference."""
    csv_row = _ev("AAPL", "dividend", "CDIV", 12.34, "2026-06-01", "csv:2026-06-01:CDIV:AAPL:1234")
    live_row = _ev("AAPL", "dividend", "DIVIDEND", 12.34, "2026-06-02", "live-uuid-1")
    out = bs.dedupe_income_events([live_row, csv_row])
    assert len(out) == 1
    assert out[0]["snaptrade_txn_id"] == "csv:2026-06-01:CDIV:AAPL:1234"


def test_opposite_sign_same_magnitude_is_NOT_collapsed():
    """A real dividend and a same-day reversal/correction share magnitude but
    opposite sign — these are two distinct real cash movements, not a
    duplicate pair."""
    charge = _ev(None, "fee", "GOLD", -5.99, "2026-06-01", "csv:a")
    reversal = _ev(None, "fee", "GOLD", 5.99, "2026-06-01", "csv:b")
    out = bs.dedupe_income_events([charge, reversal])
    assert len(out) == 2


def test_date_within_tolerance_is_collapsed():
    a = _ev("AAPL", "dividend", "CDIV", 10.00, "2026-06-01", "csv:a")
    b = _ev("AAPL", "dividend", "DIVIDEND", 10.00,
            (pd.Timestamp("2026-06-01") + pd.Timedelta(days=INCOME_EVENT_DEDUP_DATE_TOL_DAYS)).date().isoformat(),
            "live:b")
    out = bs.dedupe_income_events([a, b])
    assert len(out) == 1


def test_date_just_past_tolerance_is_NOT_collapsed():
    a = _ev("AAPL", "dividend", "CDIV", 10.00, "2026-06-01", "csv:a")
    b = _ev("AAPL", "dividend", "DIVIDEND", 10.00,
            (pd.Timestamp("2026-06-01") + pd.Timedelta(days=INCOME_EVENT_DEDUP_DATE_TOL_DAYS + 1)).date().isoformat(),
            "live:b")
    out = bs.dedupe_income_events([a, b])
    assert len(out) == 2


def test_mint_and_int_are_never_collapsed_together():
    """MINT (margin_interest) and INT (interest) are different subtypes even
    at the same ticker/amount/date — the non-merge decision must hold in the
    actual dedup pass, not just in income_event_subtype's mapping."""
    mint_row = _ev(None, "interest", "MINT", -36.59, "2026-06-01", "csv:mint")
    int_row = _ev(None, "interest", "INT", -36.59, "2026-06-01", "csv:int")
    out = bs.dedupe_income_events([mint_row, int_row])
    assert len(out) == 2


def test_legacy_rows_with_no_raw_code_still_dedup_via_event_type_fallback():
    """Two legacy rows (raw_code=None) sharing ticker/event_type/amount/date
    still collapse, using event_type as the fallback subtype."""
    a = _ev("AAPL", "dividend", None, 10.00, "2026-06-01", "csv:a")
    b = _ev("AAPL", "dividend", None, 10.00, "2026-06-01", "live:b")
    out = bs.dedupe_income_events([a, b])
    assert len(out) == 1


def test_does_not_mutate_input_income_events():
    events = [_ev("AAPL", "dividend", "CDIV", 5.00, "2026-06-01", "csv:a")]
    before = [dict(e) for e in events]
    bs.dedupe_income_events(events)
    assert events == before


# ─── classify_transactions' write-time income dedup (existing_income_events) ─

def _txn(ttype, amount, trade_date="2026-06-01", ticker=None, txn_id="live-1"):
    t = {"type": ttype, "amount": amount, "trade_date": trade_date, "id": txn_id}
    if ticker is not None:
        t["symbol"] = ticker
    return t


def test_no_existing_income_events_param_is_backward_compatible():
    """Omitting existing_income_events (the default) must not change
    behaviour for callers that haven't been updated yet."""
    out = bs.classify_transactions([_txn("DIVIDEND", 5.00, ticker="AAPL")], pd.DataFrame())
    assert len(out["income_events"]) == 1


def test_a_live_activity_matching_an_existing_csv_row_is_suppressed():
    existing = [_ev("AAPL", "dividend", "CDIV", 5.00, "2026-06-01", "csv:2026-06-01:CDIV:AAPL:500")]
    out = bs.classify_transactions(
        [_txn("DIVIDEND", 5.00, trade_date="2026-06-01", ticker="AAPL")],
        pd.DataFrame(),
        existing_income_events=existing,
    )
    assert out["income_events"] == []
    assert out["ignored"].get("DIVIDEND (cross-path duplicate)") == 1


def test_a_non_matching_live_activity_is_not_suppressed():
    existing = [_ev("AAPL", "dividend", "CDIV", 5.00, "2026-06-01", "csv:2026-06-01:CDIV:AAPL:500")]
    out = bs.classify_transactions(
        [_txn("DIVIDEND", 9.99, trade_date="2026-06-01", ticker="AAPL")],
        pd.DataFrame(),
        existing_income_events=existing,
    )
    assert len(out["income_events"]) == 1


def test_new_income_events_carry_raw_code():
    out = bs.classify_transactions([_txn("INTEREST", -1.23, ticker=None)], pd.DataFrame())
    assert out["income_events"][0]["raw_code"] == "INTEREST"


def test_one_existing_row_cannot_suppress_two_new_activities():
    """The 'consume on match' guard: one existing CSV row must not explain
    away two distinct new live activities in the same batch."""
    existing = [_ev("AAPL", "dividend", "CDIV", 5.00, "2026-06-01", "csv:2026-06-01:CDIV:AAPL:500")]
    out = bs.classify_transactions(
        [
            _txn("DIVIDEND", 5.00, trade_date="2026-06-01", ticker="AAPL", txn_id="live-a"),
            _txn("DIVIDEND", 5.00, trade_date="2026-06-01", ticker="AAPL", txn_id="live-b"),
        ],
        pd.DataFrame(),
        existing_income_events=existing,
    )
    # First one matches and is suppressed; the second (no more existing rows
    # left in that bucket) is genuinely new and must pass through.
    assert len(out["income_events"]) == 1
