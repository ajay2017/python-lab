"""Tests for stock_analyzer.capital_vs_margin — backward account-value
reconstruction + margin cost/benefit verdicts (pure logic, no I/O).
"""
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import capital_vs_margin as cvm
from stock_analyzer import margin as _margin

pytestmark = pytest.mark.fast


# ── 1. Interest sign-isolation ──────────────────────────────────────────────

def test_interest_partition_never_nets_mixed_rows():
    events = [
        {"event_type": "interest", "amount": -5.0, "event_date": "2026-08-19"},
        {"event_type": "interest", "amount": 2.0, "event_date": "2026-08-20"},
        {"event_type": "interest", "amount": -3.0, "event_date": "2026-08-21"},
        {"event_type": "dividend", "amount": 10.0, "event_date": "2026-08-20"},  # ignored
        {"event_type": "interest", "amount": 0.0, "event_date": "2026-08-22"},   # skipped
    ]
    part = cvm.interest_partition(events)
    assert part == {
        "sum_neg_magnitude": 8.0,
        "sum_pos_magnitude": 2.0,
        "n_neg": 2,
        "n_pos": 1,
    }
    # Never netted: neg+pos != a single net figure being returned anywhere.
    assert "net" not in part


def test_resolve_interest_charged_unconfirmed_vs_confirmed():
    part = {"sum_neg_magnitude": 8.0, "sum_pos_magnitude": 2.0}

    unconfirmed = cvm.resolve_interest_charged(part, None)
    assert unconfirmed["confirmed"] is False
    assert unconfirmed["charged"] == 8.0
    assert unconfirmed["earned"] == 2.0

    neg = cvm.resolve_interest_charged(part, "negative")
    assert neg == {"charged": 8.0, "earned": 2.0, "confirmed": True}

    pos = cvm.resolve_interest_charged(part, "positive")
    assert pos == {"charged": 2.0, "earned": 8.0, "confirmed": True}


# ── 2. Anchor + backward reconstruction ─────────────────────────────────────

def _trades_df(rows):
    return pd.DataFrame(rows)


def test_reconstruct_roundtrip_synthetic_ledger_split_and_baseline_are_phantom_free():
    """Hand-computed synthetic ledger. SPLIT and 'baseline' flow rows must
    contribute EXACTLY zero cash delta — the "phantom cash" trap."""
    trades = _trades_df([
        {"traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY", "shares": 10,
         "price": 100, "broker_txn_id": "t1"},
        {"traded_at": "2026-08-19T16:00:00-04:00", "action": "SELL", "shares": 5,
         "price": 110, "broker_txn_id": "t2"},
        # SPLIT: shares is the POST-SPLIT TOTAL, not a delta -- must be a no-op.
        {"traded_at": "2026-08-20T16:00:00-04:00", "action": "SPLIT", "shares": 20,
         "price": 50, "broker_txn_id": "t3"},
    ])
    flows = [
        # 'baseline' is a bookkeeping anchor, never a cash movement.
        {"flow_date": "2026-08-18", "flow_type": "baseline", "amount": 5000.0},
        {"flow_date": "2026-08-19", "flow_type": "deposit", "amount": 200.0},
    ]
    income = [
        {"event_type": "interest", "amount": -3.5, "event_date": "2026-08-19"},
        {"event_type": "dividend", "amount": 2.0, "event_date": "2026-08-20"},
    ]
    anchor = {"cash": 1000.0, "date": date(2026, 8, 20), "src": "live"}
    golive = date(2026, 8, 18)

    series = cvm.reconstruct_daily_cash(anchor, golive, trades, flows, income)
    by_date = {r["date"]: r["cash_balance"] for r in series}

    # Hand-computed (see module docstring's cash-delta conventions):
    #   8/20 delta = SPLIT(0) + no flow + dividend(+2.0) = 2.0  -> 8/19 = 1000-2.0 = 998.0
    #   8/19 delta = SELL(+550) + deposit(+200) + interest(-3.5) = 746.5 -> 8/18 = 998-746.5 = 251.5
    assert by_date[date(2026, 8, 20)] == pytest.approx(1000.0)
    assert by_date[date(2026, 8, 19)] == pytest.approx(998.0)
    assert by_date[date(2026, 8, 18)] == pytest.approx(251.5)
    assert len(series) == 3
    assert series[0]["date"] == golive  # oldest-first


def test_reconstruct_daily_cash_golive_after_anchor_returns_empty():
    anchor = {"cash": 100.0, "date": date(2026, 8, 18), "src": "live"}
    golive = date(2026, 8, 20)  # after anchor -- nothing to reconstruct
    assert cvm.reconstruct_daily_cash(anchor, golive, None, [], []) == []


def test_interest_sign_agnostic_rollback_shifts_by_exactly_2x():
    """Feeding interest as -X vs +X on the same date shifts every date BEFORE
    that event by exactly 2X -- both are valid rollbacks (sign-agnostic
    reconstruction), only the separately-disclosed total differs."""
    anchor = {"cash": 1000.0, "date": date(2026, 8, 20), "src": "live"}
    golive = date(2026, 8, 18)
    X = 10.0
    neg_events = [{"event_type": "interest", "amount": -X, "event_date": "2026-08-19"}]
    pos_events = [{"event_type": "interest", "amount": X, "event_date": "2026-08-19"}]

    s_neg = cvm.reconstruct_daily_cash(anchor, golive, None, [], neg_events)
    s_pos = cvm.reconstruct_daily_cash(anchor, golive, None, [], pos_events)
    by_neg = {r["date"]: r["cash_balance"] for r in s_neg}
    by_pos = {r["date"]: r["cash_balance"] for r in s_pos}

    # On/after the event date, unaffected.
    assert by_neg[date(2026, 8, 20)] == by_pos[date(2026, 8, 20)] == pytest.approx(1000.0)
    assert by_neg[date(2026, 8, 19)] == by_pos[date(2026, 8, 19)] == pytest.approx(1000.0)
    # Before the event date, differs by exactly 2X (a -X charge means MORE
    # cash had to exist before it to roll forward to the same anchor, so the
    # negative-amount rollback lands HIGHER, not lower).
    assert by_neg[date(2026, 8, 18)] - by_pos[date(2026, 8, 18)] == pytest.approx(2 * X)


def test_resolve_anchor_prefers_live_when_fresh():
    now = pd.Timestamp("2026-09-10", tz="UTC")
    account_cash_rec = {"cash_balance": -500.0, "updated_at": "2026-09-09T12:00:00+00:00"}
    recorded_df = pd.DataFrame([
        {"snapshot_date": "2026-09-08", "cash_balance": -400.0},
    ])
    anchor = cvm.resolve_anchor(account_cash_rec, recorded_df, stale_days_limit=7, now=now)
    assert anchor is not None
    assert anchor["src"] == "live"
    assert anchor["cash"] == -500.0


def test_resolve_anchor_falls_back_to_recorded_when_live_stale():
    now = pd.Timestamp("2026-09-10", tz="UTC")
    account_cash_rec = {"cash_balance": -500.0, "updated_at": "2026-08-01T12:00:00+00:00"}  # stale
    recorded_df = pd.DataFrame([
        {"snapshot_date": "2026-09-08", "cash_balance": -400.0},
        {"snapshot_date": "2026-09-09", "cash_balance": -410.0},
    ])
    anchor = cvm.resolve_anchor(account_cash_rec, recorded_df, stale_days_limit=7, now=now)
    assert anchor is not None
    assert anchor["src"] == "recorded"
    assert anchor["date"] == date(2026, 9, 9)
    assert anchor["cash"] == -410.0


def test_resolve_anchor_none_when_stale_and_no_recorded_row():
    now = pd.Timestamp("2026-09-10", tz="UTC")
    account_cash_rec = {"cash_balance": -500.0, "updated_at": "2026-08-01T12:00:00+00:00"}
    assert cvm.resolve_anchor(account_cash_rec, None, stale_days_limit=7, now=now) is None
    assert cvm.resolve_anchor(None, pd.DataFrame(), stale_days_limit=7, now=now) is None


def test_golive_floor_none_when_no_broker_synced_rows():
    trades = _trades_df([
        {"traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY", "shares": 10,
         "price": 100, "broker_txn_id": None},  # manual, not broker-synced
    ])
    snaps = pd.DataFrame([{"snapshot_date": "2026-08-18", "ticker": "AAPL",
                            "shares": 10, "close_price": 100.0}])
    assert cvm.golive_floor(trades, [], [], snaps) is None


def test_golive_floor_uses_later_of_ledger_and_snapshot_floor():
    trades = _trades_df([
        {"traded_at": "2026-08-15T16:00:00-04:00", "action": "BUY", "shares": 10,
         "price": 100, "broker_txn_id": "abc"},
    ])
    income = [{"event_type": "interest", "amount": -1.0, "event_date": "2026-08-16"}]
    # daily_snapshots only reaches back to 8/18 -- that's the binding constraint.
    snaps = pd.DataFrame([
        {"snapshot_date": "2026-08-18", "ticker": "AAPL", "shares": 10, "close_price": 100.0},
        {"snapshot_date": "2026-08-19", "ticker": "AAPL", "shares": 10, "close_price": 101.0},
    ])
    floor = cvm.golive_floor(trades, [], income, snaps)
    assert floor == date(2026, 8, 18)


def test_golive_floor_none_when_no_daily_snapshots_coverage():
    trades = _trades_df([
        {"traded_at": "2026-08-15T16:00:00-04:00", "action": "BUY", "shares": 10,
         "price": 100, "broker_txn_id": "abc"},
    ])
    assert cvm.golive_floor(trades, [], [], None) is None
    assert cvm.golive_floor(trades, [], [], pd.DataFrame()) is None


# ── 2b. Late-trade anchor adjustment (F-267 3rd fix, 2026-09-11) ────────────

def test_adjust_anchor_for_late_trades_real_example_roundtrip_zero_drift():
    """The exact real bug: BUY 15 ON @ $74.00 = $1,110.00 filed at
    2026-09-11 17:07 ET, AFTER the last account_cash sync that day. Before
    this fix, reconstruct_daily_cash's backward step un-does that trade
    against an anchor that never actually counted it, producing a
    self-check drift of EXACTLY $1,110.00 (confirmed live). This proves the
    round-trip closes to exactly zero, not merely that the adjustment
    function returns a plausible number."""
    d_anchor = date(2026, 9, 11)
    golive = date(2026, 9, 10)
    original_live_cash = -12424.0  # the raw (pre-bug) live-sync figure

    trades = _trades_df([
        {"traded_at": "2026-09-11T17:07:00-04:00", "action": "BUY", "shares": 15,
         "price": 74.0, "broker_txn_id": "t1", "traded_at_time_known": True},
    ])
    account_cash_rec = {"cash_balance": original_live_cash,
                         "updated_at": "2026-09-11T14:00:00-04:00"}
    anchor = {"cash": original_live_cash, "date": d_anchor, "src": "live"}

    adjusted = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, trades)
    assert adjusted["cash"] == pytest.approx(original_live_cash + (15 * -74.0))
    assert adjusted["cash"] == pytest.approx(-13534.0)
    assert adjusted["late_trade_adjustment"] == pytest.approx(-1110.0)
    assert adjusted["late_trade_count"] == 1

    series = cvm.reconstruct_daily_cash(adjusted, golive, trades, [], [])
    by_date = {r["date"]: r["cash_balance"] for r in series}
    # The PRIOR day's reconstructed cash must land EXACTLY on the original
    # (pre-adjustment) live-sync figure -- zero drift.
    assert by_date[date(2026, 9, 10)] == pytest.approx(original_live_cash, abs=1e-9)


def test_adjust_anchor_for_late_trades_strict_after_boundary():
    """A trade timestamped EXACTLY at the sync time does not qualify (tied
    -- treated as already-reflected, the conservative choice against
    double-counting); one second later does."""
    d_anchor = date(2026, 9, 11)
    account_cash_rec = {"cash_balance": -1000.0, "updated_at": "2026-09-11T14:00:00-04:00"}
    anchor = {"cash": -1000.0, "date": d_anchor, "src": "live"}

    tie_trades = _trades_df([
        {"traded_at": "2026-09-11T14:00:00-04:00", "action": "BUY", "shares": 1,
         "price": 50.0, "broker_txn_id": "t1", "traded_at_time_known": True},
    ])
    result_tie = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, tie_trades)
    assert result_tie == anchor
    assert "late_trade_adjustment" not in result_tie

    after_trades = _trades_df([
        {"traded_at": "2026-09-11T14:00:01-04:00", "action": "BUY", "shares": 1,
         "price": 50.0, "broker_txn_id": "t2", "traded_at_time_known": True},
    ])
    result_after = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, after_trades)
    assert result_after["cash"] == pytest.approx(-1050.0)
    assert result_after["late_trade_count"] == 1


def test_adjust_anchor_for_late_trades_before_sync_same_day_no_adjustment():
    anchor = {"cash": -1000.0, "date": date(2026, 9, 11), "src": "live"}
    account_cash_rec = {"cash_balance": -1000.0, "updated_at": "2026-09-11T14:00:00-04:00"}
    trades = _trades_df([
        {"traded_at": "2026-09-11T09:00:00-04:00", "action": "BUY", "shares": 1,
         "price": 50.0, "broker_txn_id": "t1", "traded_at_time_known": True},   # before the sync -- already reflected
    ])
    result = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, trades)
    assert result == anchor


def test_adjust_anchor_for_late_trades_different_day_no_adjustment():
    """Later in absolute time, but a DIFFERENT ET calendar date than the
    anchor's own date -- outside the backward-reconstruction window this
    anchor governs."""
    anchor = {"cash": -1000.0, "date": date(2026, 9, 11), "src": "live"}
    account_cash_rec = {"cash_balance": -1000.0, "updated_at": "2026-09-11T14:00:00-04:00"}
    trades = _trades_df([
        {"traded_at": "2026-09-12T09:00:00-04:00", "action": "BUY", "shares": 1,
         "price": 50.0, "broker_txn_id": "t1", "traded_at_time_known": True},
    ])
    result = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, trades)
    assert result == anchor


def test_adjust_anchor_for_late_trades_recorded_src_returned_unchanged():
    """A 'recorded' anchor is a complete historical EOD snapshot -- no
    intraday-sync concept applies, even with a same-day-after-sync-looking
    trade in the ledger."""
    anchor = {"cash": -1000.0, "date": date(2026, 9, 11), "src": "recorded"}
    account_cash_rec = {"cash_balance": -1000.0, "updated_at": "2026-09-11T14:00:00-04:00"}
    trades = _trades_df([
        {"traded_at": "2026-09-11T17:07:00-04:00", "action": "BUY", "shares": 15,
         "price": 74.0, "broker_txn_id": "t1", "traded_at_time_known": True},
    ])
    result = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, trades)
    assert result == anchor
    assert "late_trade_adjustment" not in result


def test_adjust_anchor_for_late_trades_none_anchor_returns_none():
    assert cvm.adjust_anchor_for_late_trades(None, {"updated_at": "2026-09-11T14:00:00-04:00"}, None) is None


def test_adjust_anchor_for_late_trades_missing_or_bad_updated_at_fails_safe():
    anchor = {"cash": -1000.0, "date": date(2026, 9, 11), "src": "live"}
    trades = _trades_df([
        {"traded_at": "2026-09-11T17:07:00-04:00", "action": "BUY", "shares": 15,
         "price": 74.0, "broker_txn_id": "t1", "traded_at_time_known": True},
    ])
    assert cvm.adjust_anchor_for_late_trades(anchor, None, trades) == anchor
    assert cvm.adjust_anchor_for_late_trades(anchor, {}, trades) == anchor
    assert cvm.adjust_anchor_for_late_trades(
        anchor, {"updated_at": "not-a-timestamp"}, trades
    ) == anchor


def test_adjust_anchor_for_late_trades_sell_split_and_multiple_summed():
    """A SELL late trade increases cash; a SPLIT row on the same day after
    the sync contributes exactly zero (already handled inside
    `daily_pnl.today_trade_cash_delta`); multiple qualifying late trades on
    the same day are summed correctly."""
    anchor = {"cash": -1000.0, "date": date(2026, 9, 11), "src": "live"}
    account_cash_rec = {"cash_balance": -1000.0, "updated_at": "2026-09-11T14:00:00-04:00"}
    trades = _trades_df([
        {"traded_at": "2026-09-11T15:00:00-04:00", "action": "SELL", "shares": 10,
         "price": 20.0, "broker_txn_id": "t1", "traded_at_time_known": True},      # +200
        {"traded_at": "2026-09-11T16:00:00-04:00", "action": "SPLIT", "shares": 40,
         "price": 10.0, "broker_txn_id": "t2", "traded_at_time_known": True},      # contributes 0
        {"traded_at": "2026-09-11T17:00:00-04:00", "action": "BUY", "shares": 5,
         "price": 50.0, "broker_txn_id": "t3", "traded_at_time_known": True},      # -250
    ])
    result = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, trades)
    # +200 (SELL) + 0 (SPLIT) - 250 (BUY) = -50
    assert result["cash"] == pytest.approx(-1050.0)
    assert result["late_trade_adjustment"] == pytest.approx(-50.0)
    assert result["late_trade_count"] == 3


def test_adjust_anchor_for_late_trades_fallback_integrity_still_flags_genuine_problem():
    """LOAD-BEARING: after correctly adjusting for a genuine late trade
    (which eliminates that SPECIFIC false-positive drift), a SEPARATE,
    unrelated, genuine data problem on a different date must still be
    caught by validate_reconstruction/render_gate -- this fix must not
    weaken the safety net for real problems."""
    d_anchor = date(2026, 9, 11)
    golive = date(2026, 9, 8)

    trades = _trades_df([
        {"traded_at": "2026-09-08T16:00:00-04:00", "action": "BUY", "shares": 10,
         "price": 100.0, "broker_txn_id": "t1", "traded_at_time_known": True},
        {"traded_at": "2026-09-11T17:07:00-04:00", "action": "BUY", "shares": 15,
         "price": 74.0, "broker_txn_id": "t2", "traded_at_time_known": True},   # the late trade
    ])
    account_cash_rec = {"cash_balance": -12424.0, "updated_at": "2026-09-11T14:00:00-04:00"}
    anchor = {"cash": -12424.0, "date": d_anchor, "src": "live"}

    adjusted = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, trades)
    assert adjusted["cash"] == pytest.approx(-13534.0)

    # A SEPARATE genuine problem: a real $500 dividend on 9/10 that this
    # reconstruction's income_events list is (deliberately, for this test)
    # missing -- simulating a dropped ledger row, an unrelated data gap.
    series = cvm.reconstruct_daily_cash(adjusted, golive, trades, [], [])
    recorded_df = pd.DataFrame([
        {"snapshot_date": "2026-09-09", "cash_balance": -12924.0},
    ])
    validation = cvm.validate_reconstruction(series, recorded_df)
    assert validation["ok"] is False
    assert len(validation["mismatches"]) == 1
    gate = cvm.render_gate(validation)
    assert gate["show_spanning_verdicts"] is False


def test_adjust_anchor_for_late_trades_broker_synced_stamp_not_adjusted():
    """THE BUG THIS TEST GUARDS AGAINST: a broker-synced trade carries NO
    real fill time -- `trade_time.normalize_traded_at` re-anchors it to a
    FABRICATED 16:00 ET stamp and flags it `traded_at_time_known=False`
    for exactly this reason (so a consumer needing a real time can exclude
    it). Even though that fabricated 16:00 stamp falls AFTER this test's
    sync time -- exactly the shape that would previously have been
    wrongly adjusted -- it must NOT be treated as a genuine late trade:
    comparing an invented time against a real sync time is a coin flip,
    not evidence. Confirms the fix."""
    anchor = {"cash": -1000.0, "date": date(2026, 9, 11), "src": "live"}
    account_cash_rec = {"cash_balance": -1000.0, "updated_at": "2026-09-11T14:00:00-04:00"}
    trades = _trades_df([
        {"traded_at": "2026-09-11T16:00:00-04:00", "action": "BUY", "shares": 15,
         "price": 74.0, "broker_txn_id": "t1", "traded_at_time_known": False},
    ])
    result = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, trades)
    assert result == anchor
    assert "late_trade_adjustment" not in result


def test_adjust_anchor_for_late_trades_manually_logged_confirmed_time_still_adjusted():
    """Confirms the fix does NOT overcorrect: a manually-logged trade with
    `traded_at_time_known=True` (a confirmed real fill time) still gets
    adjusted -- this is the actual reported scenario, BUY 15 ON @ $74.00
    filed at 2026-09-11 17:07 ET, AFTER the last sync."""
    anchor = {"cash": -12424.0, "date": date(2026, 9, 11), "src": "live"}
    account_cash_rec = {"cash_balance": -12424.0, "updated_at": "2026-09-11T14:00:00-04:00"}
    trades = _trades_df([
        {"traded_at": "2026-09-11T17:07:00-04:00", "action": "BUY", "shares": 15,
         "price": 74.0, "broker_txn_id": "t1", "traded_at_time_known": True},
    ])
    result = cvm.adjust_anchor_for_late_trades(anchor, account_cash_rec, trades)
    assert result["cash"] == pytest.approx(-13534.0)
    assert result["late_trade_adjustment"] == pytest.approx(-1110.0)
    assert result["late_trade_count"] == 1


# ── 3. Book price-return + gross-book series ────────────────────────────────

def _snap_row(d, ticker, shares, close):
    return {"snapshot_date": d, "ticker": ticker, "shares": shares, "close_price": close}


def test_gross_book_by_date_sums_and_never_zero_fills_unresolvable_price():
    """`gross_book_by_date` now takes (holdings_map, price_lookup,
    low_conf_tickers) — see the F-267 gross-book-recon fix. A date is a
    genuine GAP only when a held ticker's price can't be resolved at all,
    never when the date is simply "not in the snapshots" (that concept no
    longer applies now that holdings come from the trade ledger)."""
    d18, d19, d20 = date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)
    holdings_map = {
        d18: {"AAPL": 10.0, "MSFT": 5.0},
        d19: {"AAPL": 10.0},
        d20: {"AAPL": 10.0},   # AAPL price unresolvable this date -> gap
    }
    price_lookup = {
        ("AAPL", d18): (100.0, "exact"),
        ("MSFT", d18): (200.0, "exact"),
        ("AAPL", d19): (110.0, "exact"),
        # deliberately no ("AAPL", d20) entry
    }
    out = cvm.gross_book_by_date(holdings_map, price_lookup)
    assert out[d18] == pytest.approx(1000.0 + 1000.0)
    assert out[d19] == pytest.approx(1100.0)
    assert d20 not in out  # unresolvable price -> a gap, never zero


def test_gross_book_by_date_empty_holdings_is_a_genuine_zero():
    """Unlike the old snapshot-only function, a date with a CONFIRMED-EMPTY
    holdings map (the trade ledger says nothing is held) is a real 0.0, not
    a gap -- the trade ledger is authoritative on what's held."""
    d = date(2026, 8, 18)
    out = cvm.gross_book_by_date({d: {}}, {})
    assert out[d] == 0.0


def test_book_daily_returns_fixed_weight_and_gap_never_zero():
    snaps = pd.DataFrame([
        _snap_row("2026-08-18", "AAPL", 10, 100.0),
        _snap_row("2026-08-19", "AAPL", 10, 110.0),   # +10% on AAPL, held both days
        # 8/20 is a GAP day (no snapshot at all)
        _snap_row("2026-08-21", "AAPL", 10, 121.0),   # return vs nearest PRIOR data (8/19): +10%
    ])
    out = cvm.book_daily_returns(snaps)
    assert date(2026, 8, 18) not in out  # first date has no D_prev
    assert out[date(2026, 8, 19)] == pytest.approx(0.10)
    assert out[date(2026, 8, 21)] == pytest.approx(0.10)  # bridges the 8/20 gap, not zero-filled


def test_book_daily_returns_no_overlap_is_a_gap_not_zero():
    snaps = pd.DataFrame([
        _snap_row("2026-08-18", "AAPL", 10, 100.0),
        _snap_row("2026-08-19", "MSFT", 5, 200.0),   # total holdings turnover -- no common ticker
    ])
    out = cvm.book_daily_returns(snaps)
    assert date(2026, 8, 19) not in out


# ── 4/5. Series assembly + self-validation ──────────────────────────────────

def test_build_account_series_recorded_vs_reconstructed_and_gross_gap():
    daily_cash = [
        {"date": date(2026, 8, 18), "cash_balance": -200.0},
        {"date": date(2026, 8, 19), "cash_balance": -200.0},   # no gross_book -> a gap day
        {"date": date(2026, 9, 10), "cash_balance": -100.0},
    ]
    gross_by_date = {
        date(2026, 8, 18): 1000.0,
        date(2026, 9, 10): 2000.0,
    }
    recorded_df = pd.DataFrame([{"snapshot_date": "2026-09-10", "cash_balance": -100.0}])
    series = cvm.build_account_series(daily_cash, gross_by_date, recorded_df, rate=0.25)
    by_date = {p["date"]: p for p in series}

    assert by_date[date(2026, 8, 18)]["source"] == "reconstructed"
    assert by_date[date(2026, 8, 18)]["net_equity"] == pytest.approx(800.0)
    assert by_date[date(2026, 8, 19)]["gross_book"] is None
    assert by_date[date(2026, 8, 19)]["net_equity"] is None
    assert by_date[date(2026, 9, 10)]["source"] == "recorded"
    assert by_date[date(2026, 9, 10)]["net_equity"] == pytest.approx(1900.0)


def test_validate_reconstruction_tolerance_boundary():
    recorded_df = pd.DataFrame([{"snapshot_date": "2026-08-18", "cash_balance": -1000.0}])
    tol = max(cvm._RECON_ABS_TOL, cvm._RECON_REL_TOL * 1000.0)  # = 5.0 here

    # Exactly at tolerance -> passes.
    series_ok = [{"date": date(2026, 8, 18), "cash_balance": -1000.0 + tol}]
    result_ok = cvm.validate_reconstruction(series_ok, recorded_df)
    assert result_ok["ok"] is True
    assert result_ok["overlap_days"] == 1
    assert result_ok["mismatches"] == []

    # One cent beyond tolerance -> fails.
    series_bad = [{"date": date(2026, 8, 18), "cash_balance": -1000.0 + tol + 0.01}]
    result_bad = cvm.validate_reconstruction(series_bad, recorded_df)
    assert result_bad["ok"] is False
    assert result_bad["overlap_days"] == 1
    assert len(result_bad["mismatches"]) == 1
    assert result_bad["mismatches"][0]["date"] == date(2026, 8, 18)


def test_validate_reconstruction_skips_null_recorded_cash():
    recorded_df = pd.DataFrame([{"snapshot_date": "2026-08-18", "cash_balance": None}])
    series = [{"date": date(2026, 8, 18), "cash_balance": -99999.0}]  # wildly different
    result = cvm.validate_reconstruction(series, recorded_df)
    assert result["overlap_days"] == 0
    assert result["ok"] is True  # nothing to contradict


def test_render_gate_withholds_spanning_verdicts_on_validation_failure():
    """Render-layer fail-safe DECISION (extracted so it's actually testable --
    app.py itself is not test-covered)."""
    ok_validation = {"ok": True, "mismatches": []}
    gate_ok = cvm.render_gate(ok_validation)
    assert gate_ok["show_spanning_verdicts"] is True
    assert gate_ok["worst_mismatch"] is None

    bad_validation = {
        "ok": False,
        "mismatches": [
            {"date": date(2026, 8, 19), "reconstructed": 100.0, "recorded": 95.0, "drift": 5.0},
            {"date": date(2026, 8, 20), "reconstructed": 300.0, "recorded": 250.0, "drift": 50.0},
        ],
    }
    gate_bad = cvm.render_gate(bad_validation)
    assert gate_bad["show_spanning_verdicts"] is False
    assert gate_bad["worst_mismatch"]["date"] == date(2026, 8, 20)  # largest |drift|


# ── 6. v1 outputs ────────────────────────────────────────────────────────────

def _synthetic_series_3day():
    """A small, hand-consistent 3-day series (no interim trades/flows/interest)
    used to cross-check margin_contribution against equity_curves independently.
    """
    d0, d1, d2 = date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)
    series = [
        {"date": d0, "gross_book": 1000.0, "cash_balance": -200.0, "net_equity": 800.0,
         "margin_debit": 200.0, "source": "reconstructed"},
        {"date": d1, "gross_book": 1050.0, "cash_balance": -200.0, "net_equity": 850.0,
         "margin_debit": 200.0, "source": "reconstructed"},
        {"date": d2, "gross_book": 1029.0, "cash_balance": -200.0, "net_equity": 829.0,
         "margin_debit": 200.0, "source": "reconstructed"},
    ]
    book_returns = {d1: 0.05, d2: -0.02}
    return series, book_returns


def test_equity_curves_anchor_at_same_dollar_point():
    series, book_returns = _synthetic_series_3day()
    curves = cvm.equity_curves(series, book_returns)
    assert curves["levered"][0] == curves["unlevered"][0] == 800.0
    assert curves["levered"][-1] == pytest.approx(829.0)
    # unlevered = 800 * 1.05 * 0.98
    assert curves["unlevered"][-1] == pytest.approx(800.0 * 1.05 * 0.98)


def test_equity_curves_empty_when_no_anchor_net_equity():
    series = [{"date": date(2026, 8, 18), "gross_book": None, "net_equity": None, "source": "reconstructed"}]
    curves = cvm.equity_curves(series, {})
    assert curves == {"dates": [], "levered": [], "unlevered": [], "source": []}


def test_margin_contribution_net_value_approx_curve_gap():
    series, book_returns = _synthetic_series_3day()
    mc = cvm.margin_contribution(series, book_returns, interest_charged=0.0)
    assert mc["curve_gap"] is not None
    # Independent cross-check computed two different ways -- should be close,
    # not necessarily bit-identical (second-order compounding effects).
    assert abs(mc["net_value"] - mc["curve_gap"]) < 1.0


def test_margin_contribution_all_unlevered_window():
    d0, d1 = date(2026, 8, 18), date(2026, 8, 19)
    series = [
        {"date": d0, "gross_book": 1000.0, "cash_balance": 500.0, "net_equity": 1500.0,
         "margin_debit": 0.0, "source": "reconstructed"},
        {"date": d1, "gross_book": 1050.0, "cash_balance": 500.0, "net_equity": 1550.0,
         "margin_debit": 0.0, "source": "reconstructed"},
    ]
    book_returns = {d1: 0.05}
    mc = cvm.margin_contribution(series, book_returns, interest_charged=0.0)
    assert mc["extra_exposure_pnl"] == 0.0
    assert mc["net_value"] == 0.0

    mc_with_interest = cvm.margin_contribution(series, book_returns, interest_charged=12.34)
    assert mc_with_interest["extra_exposure_pnl"] == 0.0
    assert mc_with_interest["net_value"] == pytest.approx(-12.34)


def test_break_even_rate_edge_cases():
    assert cvm.break_even_rate(100.0, avg_debit=0.0, days=30) is None
    assert cvm.break_even_rate(100.0, avg_debit=-50.0, days=30) is None
    assert cvm.break_even_rate(100.0, avg_debit=1000.0, days=0) is None
    assert cvm.break_even_rate(100.0, avg_debit=1000.0, days=-5) is None
    # 100 / 1000 * 365/30 = 1.2166...
    assert cvm.break_even_rate(100.0, avg_debit=1000.0, days=30) == pytest.approx(100.0 / 1000.0 * 365.0 / 30.0)


def test_worst_drawdown_window_finds_largest_peak_to_trough():
    series = [
        {"date": date(2026, 8, 18), "net_equity": 1000.0},
        {"date": date(2026, 8, 19), "net_equity": 1100.0},  # new peak
        {"date": date(2026, 8, 20), "net_equity": 900.0},   # trough: -200 from peak
        {"date": date(2026, 8, 21), "net_equity": 950.0},
        {"date": date(2026, 8, 22), "net_equity": 1050.0},
        {"date": date(2026, 8, 23), "net_equity": 1000.0},  # smaller drawdown, -50
    ]
    result = cvm.worst_drawdown_window(series)
    assert result == (date(2026, 8, 19), date(2026, 8, 20), 1100.0, 900.0)


def test_worst_drawdown_window_none_when_insufficient_points():
    assert cvm.worst_drawdown_window([]) is None
    assert cvm.worst_drawdown_window([{"date": date(2026, 8, 18), "net_equity": 1000.0}]) is None
    assert cvm.worst_drawdown_window([
        {"date": date(2026, 8, 18), "net_equity": None},
        {"date": date(2026, 8, 19), "net_equity": None},
    ]) is None


def test_drawdown_decomposition_basic():
    series, book_returns = _synthetic_series_3day()
    d0, d2 = series[0]["date"], series[2]["date"]
    result = cvm.drawdown_decomposition(series, book_returns, d0, d2)
    assert result["actual_change"] == pytest.approx(29.0)  # 829 - 800
    assert result["unlevered_change"] == pytest.approx(800.0 * 1.05 * 0.98 - 800.0)
    assert result["amplification_portion"] == pytest.approx(
        result["actual_change"] - result["unlevered_change"]
    )
    assert result["interest_in_episode"] == 0.0  # no income_events passed


def test_drawdown_decomposition_with_interest_in_episode():
    series, book_returns = _synthetic_series_3day()
    d0, d2 = series[0]["date"], series[2]["date"]
    income_events = [
        {"event_type": "interest", "amount": -4.0, "event_date": "2026-08-19"},  # inside (d0, d2]
        {"event_type": "interest", "amount": -100.0, "event_date": "2026-08-01"},  # outside window
    ]
    result = cvm.drawdown_decomposition(series, book_returns, d0, d2, income_events=income_events)
    assert result["interest_in_episode"] == pytest.approx(4.0)


def test_drawdown_decomposition_none_safe_when_endpoints_missing():
    series, book_returns = _synthetic_series_3day()
    result = cvm.drawdown_decomposition(series, book_returns, date(2020, 1, 1), date(2020, 1, 2))
    assert result == {
        "actual_change": None, "unlevered_change": None,
        "amplification_portion": None, "interest_in_episode": 0.0,
    }


def test_projected_annual_interest():
    assert cvm.projected_annual_interest(10000.0, 0.12) == pytest.approx(1200.0)
    assert cvm.projected_annual_interest(0.0, 0.12) == 0.0


def test_deleverage_scenario_call_after_matches_direct_call_distance():
    gross, debit, rate, eff, shock = 24503.0, 16701.0, 0.25, 0.12, -10.0
    result = cvm.deleverage_scenario(gross, debit, 5000.0, rate, eff, shock)
    direct = _margin.call_distance(
        result["new_gross"], result["new_gross"] - result["new_debit"], result["new_debit"], rate,
    )
    assert result["call_after"] == direct
    assert result["new_debit"] == pytest.approx(11701.0)
    assert result["new_gross"] == pytest.approx(19503.0)
    assert result["interest_saved"] == pytest.approx(5000.0 * eff)


def test_deleverage_scenario_full_repay_sets_new_debit_zero_and_call_after_none():
    gross, debit, rate, eff, shock = 24503.0, 16701.0, 0.25, 0.12, -10.0
    result = cvm.deleverage_scenario(gross, debit, paydown=debit * 2, rate=rate,
                                      eff_rate=eff, shock_pct=shock)
    assert result["new_debit"] == 0.0
    assert result["call_after"] is None
    assert result["shock_after"] is None
    assert result["interest_after"] == 0.0


def test_deleverage_scenario_zero_debit_never_crashes():
    result = cvm.deleverage_scenario(gross_book=10000.0, debit=0.0, paydown=0.0,
                                      rate=0.25, eff_rate=0.12, shock_pct=-10.0)
    assert result["call_now"] is None
    assert result["shock_now"] is None
    assert result["new_debit"] == 0.0


def test_regime_split_buckets_by_week_sign():
    d0 = date(2026, 8, 17)   # Monday, ISO week A
    d1 = date(2026, 8, 24)   # Monday, ISO week B
    week_a = d0.isocalendar()[:2]
    week_b = d1.isocalendar()[:2]

    series = [
        {"date": d0, "margin_debit": 100.0},
        {"date": d1, "margin_debit": 200.0},
    ]
    weekly_book_returns = {week_a: 0.03, week_b: -0.02}
    weekly_interest = {week_a: 1.0, week_b: 2.0}

    result = cvm.regime_split(series, weekly_book_returns, weekly_interest)
    assert result["up"]["weeks"] == 1
    assert result["up"]["extra_exposure_pnl"] == pytest.approx(100.0 * 0.03)
    assert result["up"]["interest"] == pytest.approx(1.0)
    assert result["down"]["weeks"] == 1
    assert result["down"]["extra_exposure_pnl"] == pytest.approx(200.0 * -0.02)
    assert result["down"]["interest"] == pytest.approx(2.0)


def test_regime_split_week_with_no_debit_data_defaults_to_zero_exposure():
    week = date(2026, 8, 17).isocalendar()[:2]
    result = cvm.regime_split([], {week: 0.01}, {})
    assert result["up"]["weeks"] == 1
    assert result["up"]["extra_exposure_pnl"] == 0.0
    assert result["up"]["interest"] == 0.0


# ── Composition helpers (weekly wiring) ─────────────────────────────────────

def test_weekly_compounded_returns_compounds_within_week():
    d0 = date(2026, 8, 17)  # Monday
    d1 = date(2026, 8, 18)  # Tuesday, same ISO week
    d2 = date(2026, 8, 24)  # next Monday, different week
    daily = {d0: 0.02, d1: 0.01, d2: -0.03}
    out = cvm.weekly_compounded_returns(daily)
    week_a = d0.isocalendar()[:2]
    week_b = d2.isocalendar()[:2]
    assert out[week_a] == pytest.approx(1.02 * 1.01 - 1.0)
    assert out[week_b] == pytest.approx(-0.03)


def test_weekly_interest_charged_uses_same_unconfirmed_convention():
    events = [
        {"event_type": "interest", "amount": -5.0, "event_date": "2026-08-17"},
        {"event_type": "interest", "amount": 2.0, "event_date": "2026-08-18"},  # same week
        {"event_type": "interest", "amount": -1.0, "event_date": "2026-08-24"},  # next week
    ]
    out = cvm.weekly_interest_charged(events)
    week_a = date(2026, 8, 17).isocalendar()[:2]
    week_b = date(2026, 8, 24).isocalendar()[:2]
    assert out[week_a] == pytest.approx(5.0)  # unconfirmed convention -> negative leg
    assert out[week_b] == pytest.approx(1.0)


# ── window_income_events (F-267) ────────────────────────────────────────────

def test_window_income_events_boundaries_inclusive_both_ends():
    golive = date(2026, 6, 14)
    end = date(2026, 9, 10)
    events = [
        {"event_type": "interest", "amount": -1.0, "event_date": "2026-06-14"},  # == golive, kept
        {"event_type": "interest", "amount": -2.0, "event_date": "2026-09-10"},  # == end, kept
        {"event_type": "interest", "amount": -3.0, "event_date": "2026-06-13"},  # golive - 1, dropped
        {"event_type": "interest", "amount": -4.0, "event_date": "2026-09-11"},  # end + 1, dropped
    ]
    out = cvm.window_income_events(events, golive, end)
    dates_kept = {e["event_date"] for e in out}
    assert dates_kept == {"2026-06-14", "2026-09-10"}


def test_window_income_events_golive_none_returns_unchanged():
    events = [
        {"event_type": "interest", "amount": -1.0, "event_date": "2026-01-15"},
        {"event_type": "interest", "amount": -2.0, "event_date": "2026-09-10"},
    ]
    original = [dict(e) for e in events]
    out = cvm.window_income_events(events, None, date(2026, 9, 10))
    assert out == original
    assert events == original  # never mutated


def test_window_income_events_end_none_applies_only_lower_bound():
    golive = date(2026, 6, 14)
    events = [
        {"event_type": "interest", "amount": -1.0, "event_date": "2026-06-13"},  # dropped
        {"event_type": "interest", "amount": -2.0, "event_date": "2026-06-14"},  # kept
        {"event_type": "interest", "amount": -3.0, "event_date": "2030-01-01"},  # far future, still kept
    ]
    out = cvm.window_income_events(events, golive, None)
    dates_kept = {e["event_date"] for e in out}
    assert dates_kept == {"2026-06-14", "2030-01-01"}


def test_window_income_events_drops_unparseable_date():
    golive = date(2026, 6, 14)
    end = date(2026, 9, 10)
    events = [
        {"event_type": "interest", "amount": -1.0, "event_date": "not-a-date"},
        {"event_type": "interest", "amount": -2.0, "event_date": None},
        {"event_type": "interest", "amount": -3.0, "event_date": "2026-07-01"},
    ]
    out = cvm.window_income_events(events, golive, end)
    assert len(out) == 1
    assert out[0]["amount"] == -3.0


def test_window_income_events_fixes_the_actual_bug_january_mint_excluded():
    """Regression test for F-267: a January MINT margin-interest charge
    dated before go-live must not inflate the 'Interest since go-live'
    total once the shared income list is windowed."""
    golive = date(2026, 6, 14)
    end = date(2026, 9, 10)
    events = [
        {"event_type": "interest", "amount": -171.0, "event_date": "2026-01-15"},  # pre-golive MINT charge
        {"event_type": "interest", "amount": -50.0, "event_date": "2026-07-01"},   # in-window
        {"event_type": "interest", "amount": -56.0, "event_date": "2026-08-15"},   # in-window
    ]
    windowed = cvm.window_income_events(events, golive, end)
    charged_windowed = cvm.interest_partition(windowed)["sum_neg_magnitude"]
    charged_unwindowed = cvm.interest_partition(events)["sum_neg_magnitude"]

    assert charged_windowed == pytest.approx(106.0)  # only the two in-window charges
    assert charged_windowed < charged_unwindowed
    assert charged_unwindowed == pytest.approx(277.0)


def test_window_income_events_reconstruct_daily_cash_unaffected():
    """`reconstruct_daily_cash` never references an event outside
    [golive, anchor_date] anyway (its backward walk only looks up deltas by
    date within that range) -- confirm receiving the pre-filtered list
    produces an IDENTICAL result to receiving the raw list, on a fixture
    with events on both sides of both boundaries."""
    trades = _trades_df([
        {"traded_at": "2026-06-14T16:00:00-04:00", "action": "BUY", "shares": 10,
         "price": 100, "broker_txn_id": "t1"},
    ])
    flows = [{"flow_date": "2026-06-14", "flow_type": "baseline", "amount": 5000.0}]
    income = [
        {"event_type": "interest", "amount": -171.0, "event_date": "2026-01-15"},  # before golive
        {"event_type": "interest", "amount": -3.0, "event_date": "2026-06-15"},    # in window
        {"event_type": "interest", "amount": -9.0, "event_date": "2026-09-15"},    # after anchor date
    ]
    anchor = {"cash": 1000.0, "date": date(2026, 6, 16), "src": "live"}
    golive = date(2026, 6, 14)

    windowed = cvm.window_income_events(income, golive, anchor["date"])
    series_raw = cvm.reconstruct_daily_cash(anchor, golive, trades, flows, income)
    series_windowed = cvm.reconstruct_daily_cash(anchor, golive, trades, flows, windowed)
    assert series_raw == series_windowed


def test_window_income_events_coherence_with_margin_contribution_net_value():
    """Both terms of net_value = extra_exposure_pnl - interest_charged must
    now share the SAME [golive, end] window: extra_exposure_pnl is
    structurally confined to `series`'s own date range, so windowing
    income_events to that identical range before computing interest_charged
    means neither term can see data outside it."""
    series, book_returns = _synthetic_series_3day()
    golive, end = series[0]["date"], series[-1]["date"]
    events = [
        {"event_type": "interest", "amount": -100.0, "event_date": "2026-08-01"},  # before golive
        {"event_type": "interest", "amount": -4.0, "event_date": "2026-08-19"},    # inside window
        {"event_type": "interest", "amount": -9.0, "event_date": "2026-09-01"},    # after end
    ]
    windowed = cvm.window_income_events(events, golive, end)
    charged = cvm.resolve_interest_charged(cvm.interest_partition(windowed), "negative")["charged"]
    assert charged == pytest.approx(4.0)  # only the in-window charge

    mc = cvm.margin_contribution(series, book_returns, interest_charged=charged)
    assert mc["net_value"] == pytest.approx(mc["extra_exposure_pnl"] - 4.0)

    # Without windowing, pre-golive/post-end noise would have leaked into
    # the same figure -- proving the fix actually changes the number.
    charged_unwindowed = cvm.resolve_interest_charged(cvm.interest_partition(events), "negative")["charged"]
    assert charged_unwindowed == pytest.approx(113.0)
    assert charged_unwindowed != charged
