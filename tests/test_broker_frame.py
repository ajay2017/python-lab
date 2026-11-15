"""stock_analyzer.broker_frame -- net capital checked against Robinhood's own
positions (U7; docs/plans/net-capital-broker-frame.md, C1: the pure module only).

Scenario used throughout: journal holds A 100 sh and B 50 sh, both priced $100
(gross book 15,000); the broker-synced cash record is -5,000, so the journal
reads net capital 10,000. Robinhood's truth is sum(broker shares x price) + cash.
"""
import ast
import itertools
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import broker_frame as bf
from stock_analyzer import broker_sync
from stock_analyzer.constants import (
    BROKER_FRAME_PAIR_MAX_MINUTES,
    NET_CAPITAL_BROKER_GAP_NOTE_PCT,
)

pytestmark = pytest.mark.fast

PAIR = BROKER_FRAME_PAIR_MAX_MINUTES
NOTE_PCT = NET_CAPITAL_BROKER_GAP_NOTE_PCT
NOW = pd.Timestamp("2026-11-12T20:00:00Z")
CAP = "2026-11-12T11:15:00-05:00"          # 16:15Z
CASH_AT = "2026-11-12T16:16:00+00:00"      # one minute later: the same sync
PX = {"A": 100.0, "B": 100.0}
GROSS = 15000.0
CASH = -5000.0


def _snap(positions, captured_at=CAP):
    return {"positions": dict(positions), "account_ids": ["x"],
            "all_accounts_ok": True, "captured_at": captured_at, "kinds": {}}


def _cash(updated_at=CASH_AT, note=bf.BROKER_CASH_NOTE, balance=CASH):
    return {"cash_balance": balance, "note": note, "updated_at": updated_at}


def _hold(rows):
    return pd.DataFrame([{"Ticker": t, "Shares": s} for t, s in rows],
                        columns=["Ticker", "Shares"])


JOURNAL = _hold([("A", 100), ("B", 50)])


def _frame(positions, *, hold=None, cash=None, snap=None, px=None, now=NOW):
    return bf.broker_frame(
        snap if snap is not None else _snap(positions),
        cash if cash is not None else _cash(),
        JOURNAL if hold is None else hold,
        PX if px is None else px, now, pair_max_minutes=PAIR)


# -- books agree -------------------------------------------------------------

def test_books_agree_unchanged_source_journal():
    f = _frame({"A": 100, "B": 50})
    assert f["state"] == "ok" and f["adjust"] == 0.0 and f["rows"] == []
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == 10000.0 and r["state"] == "levered"
    assert r["source"] == "journal" and r["gap"] == 0.0 and r["checked"] is True
    b = {"state": "ok", "basis": 10000.0, "reason": None, "lag_adjustment": 0.0,
         "lag_uncertain": 0.0, "lag_uncertain_tickers": []}
    rb = bf.reconcile_budget_basis(b, GROSS, CASH, f)
    assert rb["basis"] == 10000.0 and rb["source"] == "journal" and rb["checked"] is True
    for k, v in b.items():
        assert rb[k] == v        # every input key preserved


def test_books_agree_fractional_shares_unchanged():
    hold = _hold([("A", 10.123456), ("B", 0.333333)])
    f = _frame({"A": 10.123456, "B": 0.333333}, hold=hold)
    assert f["state"] == "ok" and f["adjust"] == 0.0
    r = bf.reconcile_net_capital(1234.5, "levered", f)
    assert r["net"] == 1234.5 and r["source"] == "journal"


def test_float_noise_inside_share_tolerance_is_not_a_difference():
    f = _frame({"A": 100.0004, "B": 50})
    assert f["adjust"] == 0.0 and f["rows"] == []


# -- U7: unlogged sells ------------------------------------------------------

def test_full_unlogged_sell_lowers_net_to_broker_truth():
    f = _frame({"A": 100})                       # B sold at the broker, not logged
    assert f["adjust"] == pytest.approx(5000.0)
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == pytest.approx(5000.0)     # 100 x 100 + (-5000)
    assert r["source"] == "broker" and r["gap"] == pytest.approx(5000.0)
    assert r["state"] == "levered" and r["journal_net"] == 10000.0


def test_partial_unlogged_sell_lowers_net_to_broker_truth():
    f = _frame({"A": 100, "B": 20})
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == pytest.approx(7000.0)     # (100 + 20) x 100 - 5000
    assert f["rows"][0]["ticker"] == "B" and f["rows"][0]["diff"] == 30


def test_budget_basis_unlogged_sell_lowers_to_broker_truth():
    f = _frame({"A": 100})
    b = {"state": "ok", "basis": 10000.0, "reason": None, "lag_adjustment": 0.0}
    rb = bf.reconcile_budget_basis(b, GROSS, CASH, f)
    assert rb["basis"] == pytest.approx(5000.0) and rb["source"] == "broker"
    assert rb["gap"] == pytest.approx(5000.0) and rb["journal_net"] == 10000.0


def test_sell_logged_after_capture_changes_nothing():
    # Journal now holds LESS than the snapshot (B sold after the sync, logged):
    # the sell's proceeds are not in cash yet either, so the net is unchanged.
    hold = _hold([("A", 100)])
    f = _frame({"A": 100, "B": 50}, hold=hold)
    assert f["adjust"] == pytest.approx(-5000.0)
    r = bf.reconcile_net_capital(5000.0, "levered", f)    # gross 10,000 + cash -5,000
    assert r["net"] == 5000.0 and r["source"] == "journal" and r["gap"] == 0.0
    # budget: the lag-corrected journal basis already added the sell back (5,000 + 5,000).
    b = {"state": "ok", "basis": 10000.0, "reason": None, "lag_adjustment": 5000.0}
    rb = bf.reconcile_budget_basis(b, 10000.0, CASH, f)
    assert rb["basis"] == 10000.0 and rb["source"] == "journal" and rb["gap"] == 0.0


# -- buys logged / unlogged --------------------------------------------------

def test_buy_logged_after_capture_tightens_net_by_cost_not_budget_twice():
    # C bought 10 sh @ $50 after the sync and logged: journal has it, broker doesn't,
    # cash record doesn't carry the debit yet.
    hold = _hold([("A", 100), ("B", 50), ("C", 10)])
    px = dict(PX, C=50.0)
    f = _frame({"A": 100, "B": 50}, hold=hold, px=px)
    assert f["adjust"] == pytest.approx(500.0)
    gross = GROSS + 500.0
    r = bf.reconcile_net_capital(gross + CASH, "levered", f)
    assert r["net"] == pytest.approx(10000.0) and r["source"] == "broker"   # 10,500 - cost
    # budget_basis already subtracted that buy (lag): 10,500 - 500 = 10,000.
    b = {"state": "ok", "basis": 10000.0, "reason": None, "lag_adjustment": -500.0}
    rb = bf.reconcile_budget_basis(b, gross, CASH, f)
    assert rb["basis"] == pytest.approx(10000.0)        # not 9,500: no double subtraction
    assert rb["gap"] == 0.0 and rb["source"] == "journal"


def test_unlogged_unpriced_buy_changes_nothing_and_is_listed():
    f = _frame({"A": 100, "B": 50, "ZZZ": 5})           # broker-only, no price
    assert f["state"] == "ok" and f["adjust"] == 0.0 and f["unpriced"] == ["ZZZ"]
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == 10000.0 and r["source"] == "journal"


def test_nonpositive_or_nan_price_counts_as_unpriced():
    f = _frame({"A": 100}, px={"A": 100.0, "B": float("nan")})
    assert f["unpriced"] == ["B"] and f["adjust"] == 0.0
    f = _frame({"A": 100}, px={"A": 100.0, "B": 0.0})
    assert f["unpriced"] == ["B"]


def test_netting_sell_vs_priced_buy_buy_larger_no_change():
    # B sold (-50 sh = +5,000 adjust) but a priced C buy of 80 sh @ 100 is at the
    # broker too (-8,000): net adjust -3,000 -> never loosens.
    px = dict(PX, C=100.0)
    f = _frame({"A": 100, "C": 80}, px=px)
    assert f["adjust"] == pytest.approx(-3000.0)
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == 10000.0 and r["source"] == "journal"


def test_netting_sell_larger_than_priced_buy_tightens_by_net():
    px = dict(PX, C=100.0)
    f = _frame({"A": 100, "C": 20}, px=px)              # +5,000 - 2,000
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == pytest.approx(7000.0)


# -- symbol alias ------------------------------------------------------------

def test_symbol_alias_is_not_merged_it_binds_and_lists():
    # A shared ticker (A) keeps the snapshot plausible; the alias pair differs.
    hold = _hold([("A", 100), ("BRK-B", 10)])
    f = bf.broker_frame(_snap({"A": 100, "BRK.B": 10}), _cash(), hold,
                        {"A": 100.0, "BRK-B": 400.0}, NOW, pair_max_minutes=PAIR)
    assert f["state"] == "ok"
    assert [r["ticker"] for r in f["rows"]] == ["BRK-B"]
    assert f["adjust"] == pytest.approx(4000.0)         # journal side unmatched: binds
    assert f["unpriced"] == ["BRK.B"]                   # broker side listed, not merged
    assert bf.reconcile_net_capital(19000.0, "levered", f)["net"] == pytest.approx(15000.0)


def test_a_lone_alias_holding_reads_implausible_never_bound():
    # Only holding is BRK-B, the snapshot says BRK.B: shares no held ticker.
    f = bf.broker_frame(_snap({"BRK.B": 10}), _cash(), _hold([("BRK-B", 10)]),
                        {"BRK-B": 400.0}, NOW, pair_max_minutes=PAIR)
    assert f["state"] == "implausible" and f["adjust"] is None


def test_ticker_case_and_whitespace_are_normalised():
    f = bf.broker_frame(_snap({"a": 100, " b ": 50}), _cash(), JOURNAL, {"a": 100.0, "B": 100.0},
                        NOW, pair_max_minutes=PAIR)
    assert f["state"] == "ok" and f["adjust"] == 0.0


# -- pairing -----------------------------------------------------------------

@pytest.mark.parametrize("cash_at, state", [
    ("2026-11-12T16:25:00+00:00", "ok"),          # exactly +10 min
    ("2026-11-12T16:25:01+00:00", "unpaired"),    # +10 min 1 s
    ("2026-11-12T16:05:00+00:00", "ok"),          # exactly -10 min (cash older)
    ("2026-11-12T16:04:59+00:00", "unpaired"),    # -10 min 1 s
    ("2026-11-12T16:15:00+00:00", "ok"),          # same instant
])
def test_pairing_window_boundary_both_directions(cash_at, state):
    f = _frame({"A": 100}, cash=_cash(updated_at=cash_at))
    assert f["state"] == state
    assert (f["adjust"] is None) == (state != "ok")


def test_unpaired_leaves_net_and_basis_unchanged_and_unchecked():
    f = _frame({"A": 100}, cash=_cash(updated_at="2026-11-12T20:00:00+00:00"))
    assert f["state"] == "unpaired" and f["adjust"] is None
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == 10000.0 and r["checked"] is False and r["gap"] is None
    assert r["source"] == "journal" and r["frame_state"] == "unpaired"
    b = {"state": "ok", "basis": 9000.0, "reason": None}
    rb = bf.reconcile_budget_basis(b, GROSS, CASH, f)
    assert rb["basis"] == 9000.0 and rb["checked"] is False


@pytest.mark.parametrize("note", [None, "", "my own note", "synced via snaptrade"])
def test_manual_cash_entry_is_unpaired_even_inside_the_window(note):
    f = _frame({"A": 100}, cash=_cash(note=note))
    assert f["state"] == "unpaired" and f["adjust"] is None


def test_pair_max_minutes_unusable_is_never_ok():
    for bad in (None, -1, float("nan"), "x"):
        f = bf.broker_frame(_snap({"A": 100, "B": 50}), _cash(), JOURNAL, PX, NOW,
                            pair_max_minutes=bad)
        assert f["state"] == "unpaired" and f["adjust"] is None


def test_broker_cash_note_matches_the_real_sync_mapper():
    mapped = broker_sync.map_balances_to_cash({"cash": -5000.0, "currency": {"code": "USD"}})
    assert mapped["note"] == bf.BROKER_CASH_NOTE


# -- missing inputs ----------------------------------------------------------

def test_none_inputs_give_adjust_none_and_unchanged():
    cases = {
        "no_snapshot": bf.broker_frame(None, _cash(), JOURNAL, PX, NOW, pair_max_minutes=PAIR),
        "unreadable": bf.broker_frame(
            {"positions": None, "captured_at": CAP}, _cash(), JOURNAL, PX, NOW,
            pair_max_minutes=PAIR),
        "no_cash": bf.broker_frame(_snap({"A": 100}), None, JOURNAL, PX, NOW,
                                   pair_max_minutes=PAIR),
        "no_holdings": bf.broker_frame(_snap({"A": 100}), _cash(), None, PX, NOW,
                                       pair_max_minutes=PAIR),
    }
    for want, f in cases.items():
        assert f["state"] == want, want
        assert f["adjust"] is None, want        # never 0.0
        r = bf.reconcile_net_capital(10000.0, "levered", f)
        assert r["net"] == 10000.0 and r["checked"] is False and r["source"] == "journal"
        rb = bf.reconcile_budget_basis(
            {"state": "ok", "basis": 10000.0, "reason": None}, GROSS, CASH, f)
        assert rb["basis"] == 10000.0 and rb["checked"] is False


def test_unreadable_variants():
    assert _frame({}, snap=_snap({"A": "x"}))["state"] == "unreadable"
    assert _frame({}, snap=_snap({"A": 1}, captured_at="not a time"))["state"] == "unreadable"
    assert _frame({}, snap=_snap({"A": 1}, captured_at=None))["state"] == "unreadable"
    assert _frame({"A": 1}, cash=_cash(updated_at="garbage"))["state"] == "unreadable"


def test_cash_record_missing_pieces_is_no_cash():
    assert _frame({"A": 1}, cash={"cash_balance": None, "updated_at": CASH_AT,
                                  "note": bf.BROKER_CASH_NOTE})["state"] == "no_cash"
    assert _frame({"A": 1}, cash={"cash_balance": -1.0, "updated_at": None,
                                  "note": bf.BROKER_CASH_NOTE})["state"] == "no_cash"


def test_holdings_unreadable_shapes():
    bad = pd.DataFrame({"Ticker": ["A"], "Shares": [float("nan")]})
    assert _frame({"A": 1}, hold=bad)["state"] == "no_holdings"
    assert _frame({"A": 1}, hold=pd.DataFrame({"X": [1]}))["state"] == "no_holdings"


def test_empty_journal_with_broker_positions_is_ok_and_never_tightens():
    f = _frame({"A": 100}, hold=_hold([]))
    assert f["state"] == "ok" and f["adjust"] == pytest.approx(-10000.0)
    assert bf.reconcile_net_capital(500.0, "levered", f)["net"] == 500.0


def test_empty_snapshot_with_empty_journal_is_a_real_ok():
    f = _frame({}, hold=_hold([]))
    assert f["state"] == "ok" and f["adjust"] == 0.0


def test_empty_snapshot_with_journal_holdings_is_implausible_never_called():
    f = _frame({})
    assert f["state"] == "implausible" and f["adjust"] is None
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == 10000.0 and r["state"] == "levered" and r["checked"] is False
    rb = bf.reconcile_budget_basis({"state": "ok", "basis": 10000.0, "reason": None},
                                   GROSS, CASH, f)
    assert rb["state"] == "ok" and rb["basis"] == 10000.0


# -- states other than levered ----------------------------------------------

def test_stale_unlevered_unknown_are_unchanged():
    f = _frame({"A": 100})                              # a binding frame
    for st in ("stale", "unknown"):
        r = bf.reconcile_net_capital(None, st, f)
        assert r["net"] is None and r["state"] == st and r["checked"] is False
    r = bf.reconcile_net_capital(None, "unlevered", f)
    assert r["net"] is None and r["state"] == "unlevered" and r["source"] == "journal"
    rb = bf.reconcile_budget_basis(
        {"state": "unknown", "basis": None, "reason": "the cash record is out of date"},
        GROSS, CASH, f)
    assert rb["state"] == "unknown" and rb["basis"] is None and rb["checked"] is False


def test_called_input_stays_called_and_never_rises():
    f = _frame({"A": 100})
    r = bf.reconcile_net_capital(-300.0, "called", f)
    assert r["state"] == "called" and r["net"] == pytest.approx(-5300.0)
    assert r["net"] <= -300.0
    rb = bf.reconcile_budget_basis(
        {"state": "called", "basis": -300.0, "reason": "x"}, GROSS, CASH, f)
    assert rb["state"] == "called" and rb["basis"] == -300.0


def test_frame_driving_net_to_zero_or_below_is_called():
    hold = _hold([("A", 100), ("B", 50)])
    f = _frame({"A": 100}, hold=hold)                   # adjust 5,000
    r = bf.reconcile_net_capital(5000.0, "levered", f)  # exactly zero
    assert r["net"] == 0.0 and r["state"] == "called" and r["source"] == "broker"
    r = bf.reconcile_net_capital(4000.0, "levered", f)
    assert r["net"] == pytest.approx(-1000.0) and r["state"] == "called"
    b = {"state": "ok", "basis": 5000.0, "reason": None, "lag_adjustment": 0.0}
    rb = bf.reconcile_budget_basis(b, GROSS, CASH, f)   # frame basis = 5,000
    assert rb["state"] == "ok" and rb["basis"] == pytest.approx(5000.0)
    b2 = {"state": "ok", "basis": 5000.0, "reason": None}
    rb2 = bf.reconcile_budget_basis(b2, GROSS, -10000.0, f)   # frame basis = 0
    assert rb2["state"] == "called" and rb2["basis"] == pytest.approx(0.0)
    assert rb2["reason"] == bf.CALLED_REASON
    # a lag-corrected basis > 0 is lowered to <= 0 -> called
    b3 = {"state": "ok", "basis": 100.0, "reason": None}
    rb3 = bf.reconcile_budget_basis(b3, GROSS, -12000.0, f)   # frame basis = -2,000
    assert rb3["state"] == "called" and rb3["basis"] == pytest.approx(-2000.0)


# -- budget basis keeps the real daily_budget shape --------------------------

def test_budget_basis_preserves_lag_uncertain_fields_and_only_lowers_basis():
    f = _frame({"A": 100})
    b = {"state": "ok", "basis": 10000.0, "reason": None, "lag_adjustment": -250.0,
         "lag_uncertain": 300.0, "lag_uncertain_tickers": ["XYZ"]}
    rb = bf.reconcile_budget_basis(b, GROSS, CASH, f)
    for k in ("lag_adjustment", "lag_uncertain", "lag_uncertain_tickers", "reason"):
        assert rb[k] == b[k]
    assert rb["basis"] <= b["basis"]
    assert b["basis"] == 10000.0                        # the input dict is not mutated
    assert set(rb) - set(b) == {"source", "gap", "checked", "journal_net", "frame_state",
                                "frame_adjust"}


def test_budget_basis_unusable_inputs_unchecked():
    f = _frame({"A": 100})
    b = {"state": "ok", "basis": 10000.0, "reason": None}
    for gb, cs in ((None, CASH), (GROSS, None), (float("nan"), CASH)):
        rb = bf.reconcile_budget_basis(b, gb, cs, f)
        assert rb["basis"] == 10000.0 and rb["checked"] is False
    assert bf.reconcile_budget_basis(None, GROSS, CASH, f)["checked"] is False


# -- tightening-only property -------------------------------------------------

def test_tightening_only_over_a_grid():
    shares = (0, 0.5, 10, 50, 100, 150)
    nets = (-500.0, 0.0, 250.0, 10000.0, 1_000_000.0)
    checked = 0
    for ja, jb, ba, bb, net, st in itertools.product(
            shares, shares, shares, shares, nets, ("levered", "called", "unlevered")):
        hold = _hold([("A", ja), ("B", jb)]) if (ja or jb) else _hold([])
        f = _frame({k: v for k, v in (("A", ba), ("B", bb)) if v}, hold=hold)
        r = bf.reconcile_net_capital(net, st, f)
        assert r["net"] <= net
        if r["source"] == "broker":
            assert r["net"] < net
        else:
            assert r["net"] == net
        b = {"state": "ok", "basis": net, "reason": None}
        rb = bf.reconcile_budget_basis(b, GROSS, CASH, f)
        assert rb["basis"] <= net
        checked += 1
    assert checked == len(shares) ** 4 * len(nets) * 3


# -- time context ------------------------------------------------------------

def test_dst_fall_back_2026_11_01_pairs_by_instant_not_wall_clock():
    # 01:55 EDT (-04:00) = 05:55Z; cash 06:03Z = 01:03 EST. Wall clock reads 52 min
    # apart; the real gap is 8 minutes.
    f = _frame({"A": 100, "B": 50},
               snap=_snap({"A": 100, "B": 50}, captured_at="2026-11-01T01:55:00-04:00"),
               cash=_cash(updated_at="2026-11-01T06:03:00+00:00"))
    assert f["state"] == "ok"
    # same wall-clock hour, the other side of the fall-back: 65 real minutes apart.
    f = _frame({"A": 100, "B": 50},
               snap=_snap({"A": 100, "B": 50}, captured_at="2026-11-01T01:30:00-04:00"),
               cash=_cash(updated_at="2026-11-01T01:35:00-05:00"))
    assert f["state"] == "unpaired"


def test_dst_spring_forward_2027_03_14_pairs_by_instant():
    # 01:58 EST (-05:00) = 06:58Z; cash 07:04Z = 03:04 EDT: 6 real minutes apart
    # (wall clock says 66).
    f = _frame({"A": 100, "B": 50},
               snap=_snap({"A": 100, "B": 50}, captured_at="2027-03-14T01:58:00-05:00"),
               cash=_cash(updated_at="2027-03-14T07:04:00Z"))
    assert f["state"] == "ok"
    f = _frame({"A": 100, "B": 50},
               snap=_snap({"A": 100, "B": 50}, captured_at="2027-03-14T01:58:00-05:00"),
               cash=_cash(updated_at="2027-03-14T03:04:00-04:00"))
    assert f["state"] == "ok"                            # the same instant in EDT


def test_saturday_sync_pair_with_friday_unlogged_sell_binds():
    # 2026-11-14 is a Saturday; the broker lane runs every day. B was sold Friday,
    # never logged.
    f = _frame({"A": 100},
               snap=_snap({"A": 100}, captured_at="2026-11-14T11:15:00-05:00"),
               cash=_cash(updated_at="2026-11-14T16:16:30+00:00"),
               now=pd.Timestamp("2026-11-14T18:00:00Z"))
    assert pd.Timestamp("2026-11-14").day_name() == "Saturday"
    assert f["state"] == "ok" and f["adjust"] == pytest.approx(5000.0)
    assert f["age_hours"] == pytest.approx(1.75)
    assert bf.reconcile_net_capital(10000.0, "levered", f)["net"] == pytest.approx(5000.0)


def test_age_hours_and_captured_at():
    f = _frame({"A": 100, "B": 50})
    assert f["captured_at"] == pd.Timestamp("2026-11-12T16:15:00Z")
    assert f["age_hours"] == pytest.approx(3.75)
    f = _frame({"A": 100, "B": 50}, now=None)
    assert f["age_hours"] is None and f["state"] == "ok"
    assert bf.broker_frame(None, None, None, {}, NOW, pair_max_minutes=PAIR)["captured_at"] is None


# -- disclosure --------------------------------------------------------------

def _rec_with_gap(gap, jnet=10000.0):
    return {"state": "levered", "checked": True, "gap": gap, "journal_net": jnet,
            "net": jnet - gap, "source": "broker", "frame_state": "ok"}


def test_disclosure_shows_exactly_at_the_note_threshold():
    one_pct = 10000.0 * NOTE_PCT / 100.0
    txt = bf.disclosure_text(_rec_with_gap(one_pct), NOTE_PCT)
    assert "net capital taken from Robinhood's positions" in txt
    assert f"~${one_pct:,.0f} below your journal's figure" in txt
    assert "💰 Account → Net capital check" in txt


def test_disclosure_hidden_just_below_the_threshold():
    one_pct = 10000.0 * NOTE_PCT / 100.0
    assert bf.disclosure_text(_rec_with_gap(one_pct - 0.01), NOTE_PCT) == ""
    assert bf.disclosure_text(_rec_with_gap(0.0), NOTE_PCT) == ""


def test_disclosure_end_to_end_from_reconcile_at_one_percent():
    # adjust 100 on a journal net of 10,000 is exactly 1.0%.
    hold = _hold([("A", 100), ("B", 51)])
    f = bf.broker_frame(_snap({"A": 100, "B": 50}), _cash(), hold, {"A": 100.0, "B": 100.0},
                        NOW, pair_max_minutes=PAIR)
    assert f["adjust"] == pytest.approx(100.0)
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert bf.disclosure_text(r, NOTE_PCT) != ""
    f2 = bf.broker_frame(_snap({"A": 100, "B": 50.01}), _cash(), hold,
                         {"A": 100.0, "B": 100.0}, NOW, pair_max_minutes=PAIR)
    assert bf.disclosure_text(bf.reconcile_net_capital(10000.0, "levered", f2),
                              NOTE_PCT) == ""


def test_disclosure_budget_rec_uses_the_basis_as_the_denominator():
    f = _frame({"A": 100})                              # basis 10,000 -> 5,000: 50%
    rb = bf.reconcile_budget_basis(
        {"state": "ok", "basis": 10000.0, "reason": None}, GROSS, CASH, f)
    assert "~$5,000 below your journal's figure" in bf.disclosure_text(rb, NOTE_PCT)


def test_disclosure_unchecked_says_not_cross_checked_and_why():
    for st, why in (("unpaired", "weren't read together"),
                    ("no_snapshot", "no Robinhood positions snapshot"),
                    ("implausible", "none of the positions your journal holds"),
                    ("no_prices", "no prices for your holdings"),
                    ("no_cash", "no cash record"),
                    ("no_holdings", "holdings couldn't be read"),
                    ("unreadable", "unreadable")):
        f = {"state": st, "adjust": None, "rows": [], "unpriced": [],
             "captured_at": None, "age_hours": None}
        r = bf.reconcile_net_capital(10000.0, "levered", f)
        txt = bf.disclosure_text(r, NOTE_PCT)
        assert "not cross-checked" in txt and why in txt, st
    assert "no Robinhood check was available" in bf.disclosure_text(
        bf.reconcile_net_capital(10000.0, "levered", None), NOTE_PCT)


def test_disclosure_silent_when_there_is_no_status_line_state():
    for st in ("unlevered", "stale", "unknown"):
        assert bf.disclosure_text(bf.reconcile_net_capital(None, st, None), NOTE_PCT) == ""
    assert bf.disclosure_text(None, NOTE_PCT) == ""
    assert bf.disclosure_text({"state": "unknown", "checked": False}, NOTE_PCT) == ""


def test_disclosure_dollars_are_maskable_like_budget_view():
    from stock_analyzer.budget_view import _MASK, _USD_RE
    txt = bf.disclosure_text(_rec_with_gap(1234.0), NOTE_PCT)
    assert "$1,234" in txt
    assert "$" not in _USD_RE.sub(_MASK, txt)


# -- U7 C3: implausible / no_prices / split guard ------------------------------

def test_disjoint_snapshot_is_implausible_even_when_not_empty():
    f = _frame({"ZZZ": 5})                              # journal holds A, B; snapshot only ZZZ
    assert f["state"] == "implausible" and f["adjust"] is None
    r = bf.reconcile_net_capital(10000.0, "levered", f)
    assert r["net"] == 10000.0 and r["checked"] is False and r["source"] == "journal"


def test_one_shared_ticker_is_enough_to_be_plausible():
    f = _frame({"A": 100})                              # B gone, A shared
    assert f["state"] == "ok"


def test_dust_journal_holding_is_not_held():
    # Journal holds only dust (<= tolerance) of B plus real A; the snapshot holds A.
    f = _frame({"A": 100}, hold=_hold([("A", 100), ("B", 0.0004)]))
    assert f["state"] == "ok"
    # Journal that is ALL dust is "holds nothing": never implausible.
    f = _frame({"A": 100}, hold=_hold([("B", 0.0004)]))
    assert f["state"] == "ok"


def test_broker_ticker_at_zero_is_not_held():
    # Snapshot lists A and B at 0: it shares nothing held with the journal.
    f = _frame({"A": 0, "B": 0}, hold=_hold([("A", 100), ("B", 50)]))
    assert f["state"] == "implausible"


def test_single_holding_fully_sold_unlogged_is_an_accepted_detection_loss():
    f = _frame({}, hold=_hold([("A", 100)]))
    assert f["state"] == "implausible" and f["adjust"] is None
    assert "not cross-checked" in bf.disclosure_text(
        bf.reconcile_net_capital(5000.0, "levered", f), NOTE_PCT)


def test_no_prices_for_the_held_names_is_not_a_clean_ok():
    f = _frame({"A": 100, "B": 50}, px={})
    assert f["state"] == "no_prices" and f["adjust"] is None
    f = _frame({"A": 100, "B": 50}, px={"A": float("nan"), "B": 0.0})
    assert f["state"] == "no_prices"
    # one priced held name is enough to be a real check
    f = _frame({"A": 100, "B": 50}, px={"A": 100.0})
    assert f["state"] == "ok"
    r = bf.reconcile_net_capital(10000.0, "levered", _frame({"A": 100}, px={}))
    assert r["net"] == 10000.0 and r["checked"] is False
    txt = bf.disclosure_text(r, NOTE_PCT)
    assert "not cross-checked" in txt and "no prices for your holdings" in txt


def test_no_prices_never_fires_for_an_empty_journal():
    f = _frame({"A": 100}, hold=_hold([]), px={})
    assert f["state"] == "ok"


def test_every_state_constant_has_a_disclosure_reason():
    states = {v for k, v in vars(bf).items() if k.startswith("STATE_")}
    assert states - {bf.STATE_OK} == set(bf._WHY)


def test_exclude_tickers_leaves_a_differing_ticker_out_and_lists_it():
    f = bf.broker_frame(_snap({"A": 100, "B": 50}), _cash(),
                        _hold([("A", 100), ("B", 200)]), PX, NOW,
                        pair_max_minutes=PAIR, exclude_tickers=("b",))
    assert f["state"] == "ok" and f["adjust"] == 0.0 and f["rows"] == []
    assert f["excluded"] == [{"ticker": "B", "journal_shares": 200.0,
                              "broker_shares": 50.0, "diff": 150.0}]
    # a ticker the books AGREE on is not listed even when excluded
    f = bf.broker_frame(_snap({"A": 100, "B": 50}), _cash(), JOURNAL, PX, NOW,
                        pair_max_minutes=PAIR, exclude_tickers=["B"])
    assert f["excluded"] == []
    # without the exclusion it binds
    f = bf.broker_frame(_snap({"A": 100, "B": 50}), _cash(),
                        _hold([("A", 100), ("B", 200)]), PX, NOW, pair_max_minutes=PAIR)
    assert f["adjust"] == pytest.approx(15000.0) and f["excluded"] == []


def test_every_result_carries_excluded_key():
    for f in (_frame({"A": 100}), _frame({}), bf.broker_frame(
            None, None, None, {}, NOW, pair_max_minutes=PAIR)):
        assert f["excluded"] == []


# -- U7 C3: checked only on a usable basis -------------------------------------

def test_ok_frame_with_unusable_basis_is_unchecked_with_no_gap():
    f = _frame({"A": 100, "B": 50})
    for bad in (None, float("nan"), float("inf")):
        rb = bf.reconcile_budget_basis(
            {"state": "ok", "basis": bad, "reason": None}, GROSS, CASH, f)
        assert rb["checked"] is False and rb["gap"] is None
        assert rb["frame_adjust"] is None
    assert bf.disclosure_text(rb, NOTE_PCT).startswith(" · sizes not cross-checked")


def test_called_with_a_numeric_basis_is_checked_and_carries_frame_adjust():
    f = _frame({"A": 100})
    rb = bf.reconcile_budget_basis(
        {"state": "called", "basis": -300.0, "reason": "x"}, GROSS, CASH, f)
    assert rb["checked"] is True and rb["gap"] == 0.0
    assert rb["frame_adjust"] == pytest.approx(5000.0)
    assert rb["state"] == "called" and rb["basis"] == -300.0


def test_frame_adjust_is_the_signed_adjust_when_checked():
    f = _frame({"A": 100, "B": 50, "C": 5}, px=dict(PX, C=100.0))   # broker has an extra C
    rb = bf.reconcile_budget_basis(
        {"state": "ok", "basis": 10000.0, "reason": None}, GROSS, CASH, f)
    assert rb["frame_adjust"] == pytest.approx(-500.0)


# -- U7 C3: disclosure wording S1 / S2 -----------------------------------------

def _budget_rec(gap, adjust, jnet=10000.0):
    return {"state": "ok", "checked": True, "gap": gap, "journal_net": jnet,
            "source": "broker", "frame_state": "ok", "frame_adjust": adjust}


@pytest.mark.parametrize("gap, adjust, s1", [
    (1000.0, 1000.0, True),         # the books themselves differ
    (1000.0, 999.0, True),          # exactly gap - 1.0: still S1
    (1000.0, 998.99, False),        # gap - 1.01: the lag part dominates -> S2
    (1000.0, 0.0, False),           # nothing at the broker, all lag
    (1000.0, -50.0, False),         # a negative adjust counts as 0
])
def test_disclosure_s1_vs_s2_boundary(gap, adjust, s1):
    txt = bf.disclosure_text(_budget_rec(gap, adjust), NOTE_PCT)
    assert "~$1,000 below your journal's figure" in txt
    if s1:
        assert txt.endswith("(see 💰 Account → Net capital check)")
        assert "because" not in txt
    else:
        assert ("because a sell logged as after Robinhood's last sync is already in it "
                "(check that trade's fill time)") in txt
        assert "Net capital check" not in txt


def test_disclosure_without_frame_adjust_reads_s1():
    txt = bf.disclosure_text(_rec_with_gap(1234.0), NOTE_PCT)
    assert txt.endswith("(see 💰 Account → Net capital check)")


def test_disclosure_budget_one_percent_boundary_with_s_wording():
    one_pct = 10000.0 * NOTE_PCT / 100.0
    assert bf.disclosure_text(_budget_rec(one_pct, one_pct), NOTE_PCT) != ""
    assert bf.disclosure_text(_budget_rec(one_pct - 0.99, one_pct), NOTE_PCT) == ""


def test_disclosure_masked_has_no_digit_after_the_tilde():
    import re
    from stock_analyzer.budget_view import _MASK, _USD_RE
    for adj in (1234.0, 0.0):
        masked = _USD_RE.sub(_MASK, bf.disclosure_text(_budget_rec(1234.0, adj), NOTE_PCT))
        assert re.search(r"~\d", masked) is None and "$" not in masked and _MASK in masked


# -- module hygiene ----------------------------------------------------------

def test_broker_frame_does_not_import_margin_or_touch_io():
    src = Path(bf.__file__).read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    banned = ("stock_analyzer.margin", "streamlit", "stock_analyzer.db")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod not in banned, mod
            if mod == "stock_analyzer":
                assert all(a.name not in ("margin", "db") for a in node.names)
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name not in banned, a.name
    assert "datetime.now" not in src and "now_et" not in src and "utcnow" not in src
