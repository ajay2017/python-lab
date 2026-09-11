"""Tests for the F-267 gross-book reconstruction fix (stock_analyzer/
capital_vs_margin.py): `holdings_by_date`, `split_window_tickers`,
`build_price_lookup`, the new `gross_book_by_date` signature,
`low_confidence_dates`, and `validate_reconstruction`'s advisory-only gross
fields.

THE BUG (see capital_vs_margin.py's "THE BUG THIS SECTION FIXES" comment):
`reconstruct_daily_cash`'s cash and the old snapshot-only gross book came
from different sources with different timing -- a BUY reduced cash on its
trade date but the position didn't enter gross book until `daily_snapshots`
actually captured it days later, producing an artificial multi-thousand-
dollar V-shaped dip in net_equity.

THE FIX: gross book is now reconstructed from HOLDINGS (the trades ledger
itself, via `holdings_by_date`), priced with a fallback chain that ends in
the position's own BUY fill price (Decision 1) when no snapshot exists yet
-- making net_equity exactly continuous through a purchase by construction.
A resulting gross-book/F-266 drift is advisory-only (Decision 2), never a
hard gate.
"""
import inspect
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import capital_vs_margin as cvm
from stock_analyzer import db

pytestmark = pytest.mark.fast


def _trades_df(rows):
    return pd.DataFrame(rows)


# ── Regression tests for the exact reported defect ──────────────────────────

def test_after_hours_trade_net_equity_is_continuous_across_buy_date():
    """A BUY on D with no `daily_snapshots` row until D+1 (the after-hours /
    EOD-cron-lag case) must NOT create an artificial multi-thousand-dollar
    drop in net_equity on D -- the whole point of Decision 1's BUY-fill
    fallback."""
    d0 = date(2026, 8, 17)   # golive -- day before the trade
    d1 = date(2026, 8, 18)   # trade date -- no snapshot captured yet
    d2 = date(2026, 8, 19)   # snapshot finally lands

    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 100.0, "ticker": "AAPL"},
    ])
    snaps = pd.DataFrame([
        {"snapshot_date": "2026-08-19", "ticker": "AAPL", "shares": 10, "close_price": 101.0},
    ])
    anchor = {"cash": 1000.0, "date": d2, "src": "live"}

    holdings = cvm.holdings_by_date(trades, d0, d2)
    split_tk = cvm.split_window_tickers(trades, d0, d2)
    price_lookup = cvm.build_price_lookup(holdings, snaps, trades)
    gross_by_date = cvm.gross_book_by_date(holdings, price_lookup, split_tk)
    daily_cash = cvm.reconstruct_daily_cash(anchor, d0, trades, [], [])
    series = cvm.build_account_series(daily_cash, gross_by_date, None, rate=0.25)
    by_date = {p["date"]: p for p in series}

    # No gap: the BUY-fill fallback prices the position at $100 the same
    # date cash dropped by $1000 -- net_equity is exactly flat.
    assert by_date[d0]["net_equity"] == pytest.approx(2000.0)
    assert by_date[d1]["net_equity"] == pytest.approx(2000.0)
    # Small delta across the trade date -- NOT a multi-thousand-dollar spike.
    assert abs(by_date[d1]["net_equity"] - by_date[d0]["net_equity"]) < 1.0
    # Once the real snapshot lands, the 1% price move shows up correctly.
    assert by_date[d2]["net_equity"] == pytest.approx(2010.0)


def test_backdated_import_never_snapshotted_stays_continuous():
    """A trade whose `traded_at` predates when the app learned about it
    (broker-imported late) and which is NEVER snapshotted across the whole
    reconstruction window -- the BUY-fill fallback must carry the position
    at a flat, continuous value the entire time, never a gap or a spike."""
    d0 = date(2026, 8, 17)
    d1 = date(2026, 8, 18)   # trade date
    d2 = date(2026, 8, 19)
    d3 = date(2026, 8, 20)   # anchor -- still no snapshot has ever landed

    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 100.0, "ticker": "AAPL"},
    ])
    snaps = pd.DataFrame(columns=["snapshot_date", "ticker", "shares", "close_price"])  # never captured
    anchor = {"cash": 1000.0, "date": d3, "src": "live"}

    holdings = cvm.holdings_by_date(trades, d0, d3)
    split_tk = cvm.split_window_tickers(trades, d0, d3)
    price_lookup = cvm.build_price_lookup(holdings, snaps, trades)
    gross_by_date = cvm.gross_book_by_date(holdings, price_lookup, split_tk)
    daily_cash = cvm.reconstruct_daily_cash(anchor, d0, trades, [], [])
    series = cvm.build_account_series(daily_cash, gross_by_date, None, rate=0.25)
    by_date = {p["date"]: p for p in series}

    assert by_date[d0]["net_equity"] == pytest.approx(2000.0)
    assert by_date[d1]["net_equity"] == pytest.approx(2000.0)
    assert by_date[d2]["net_equity"] == pytest.approx(2000.0)
    assert by_date[d3]["net_equity"] == pytest.approx(2000.0)
    for d in (d1, d2, d3):
        assert by_date[d]["gross_book"] is not None  # never a gap -- always priced


# ── holdings_by_date ─────────────────────────────────────────────────────────

def test_holdings_by_date_none_or_empty_trades_returns_empty_dict():
    assert cvm.holdings_by_date(None, date(2026, 8, 1), date(2026, 8, 5)) == {}
    assert cvm.holdings_by_date(pd.DataFrame(), date(2026, 8, 1), date(2026, 8, 5)) == {}


def test_holdings_by_date_buy_accumulation_same_ticker():
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 100.0, "ticker": "AAPL"},
        {"id": 2, "traded_at": "2026-08-19T16:00:00-04:00", "action": "BUY",
         "shares": 5, "price": 110.0, "ticker": "AAPL"},
    ])
    out = cvm.holdings_by_date(trades, date(2026, 8, 18), date(2026, 8, 19))
    assert out[date(2026, 8, 18)] == {"AAPL": 10.0}
    assert out[date(2026, 8, 19)] == {"AAPL": 15.0}


def test_holdings_by_date_sell_reduction_and_full_close():
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 100.0, "ticker": "AAPL"},
        {"id": 2, "traded_at": "2026-08-19T16:00:00-04:00", "action": "SELL",
         "shares": 4, "price": 110.0, "ticker": "AAPL"},
        {"id": 3, "traded_at": "2026-08-20T16:00:00-04:00", "action": "SELL",
         "shares": 6, "price": 115.0, "ticker": "AAPL"},   # closes the position
    ])
    out = cvm.holdings_by_date(trades, date(2026, 8, 18), date(2026, 8, 20))
    assert out[date(2026, 8, 18)] == {"AAPL": 10.0}
    assert out[date(2026, 8, 19)] == {"AAPL": 6.0}
    assert out[date(2026, 8, 20)] == {}   # fully closed -- dropped from the map


def test_holdings_by_date_no_trade_day_carries_forward_unchanged():
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 100.0, "ticker": "AAPL"},
    ])
    out = cvm.holdings_by_date(trades, date(2026, 8, 18), date(2026, 8, 22))
    for d in (date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 22)):
        assert out[d] == {"AAPL": 10.0}
    # Fresh dict copies per date -- mutating one must not affect another.
    out[date(2026, 8, 19)]["AAPL"] = 999.0
    assert out[date(2026, 8, 20)]["AAPL"] == 10.0


def test_holdings_by_date_replay_starts_before_the_window():
    """Trades before `start` must already be reflected in `start`'s map --
    the replay starts from the earliest trade, not from `start`."""
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-10T16:00:00-04:00", "action": "BUY",
         "shares": 20, "price": 90.0, "ticker": "MSFT"},
    ])
    out = cvm.holdings_by_date(trades, date(2026, 8, 18), date(2026, 8, 19))
    assert out[date(2026, 8, 18)] == {"MSFT": 20.0}
    assert out[date(2026, 8, 19)] == {"MSFT": 20.0}


def test_holdings_by_date_split_overwrites_not_sums():
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 100.0, "ticker": "AAPL"},
        {"id": 2, "traded_at": "2026-08-19T16:00:00-04:00", "action": "SPLIT",
         "shares": 20, "price": 50.0, "ticker": "AAPL"},   # post-split TOTAL
    ])
    out = cvm.holdings_by_date(trades, date(2026, 8, 18), date(2026, 8, 19))
    assert out[date(2026, 8, 18)] == {"AAPL": 10.0}
    assert out[date(2026, 8, 19)] == {"AAPL": 20.0}   # overwrite, never 10+20=30


# ── Parity with db.recalculate_from_trades ───────────────────────────────────

def test_holdings_by_date_matches_recalculate_from_trades_final_state():
    """Pins the new forward-replay logic against the existing, already-
    trusted `db.recalculate_from_trades` implementation -- both must agree
    on the FINAL holding state for the same trade history."""
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-10T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 100.0, "ticker": "AAPL"},
        {"id": 2, "traded_at": "2026-08-12T16:00:00-04:00", "action": "BUY",
         "shares": 20, "price": 50.0, "ticker": "MSFT"},
        {"id": 3, "traded_at": "2026-08-14T16:00:00-04:00", "action": "SELL",
         "shares": 4, "price": 110.0, "ticker": "AAPL"},
        {"id": 4, "traded_at": "2026-08-16T16:00:00-04:00", "action": "SPLIT",
         "shares": 12, "price": 45.83, "ticker": "AAPL"},
        {"id": 5, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 5, "price": 60.0, "ticker": "MSFT"},
        {"id": 6, "traded_at": "2026-08-19T16:00:00-04:00", "action": "SELL",
         "shares": 25, "price": 65.0, "ticker": "MSFT"},   # fully closes MSFT
    ])
    end = date(2026, 8, 19)
    holdings_end = cvm.holdings_by_date(trades, date(2026, 8, 10), end)[end]

    recalc = db.recalculate_from_trades(trades)
    recalc_map = {
        row["Ticker"]: row["Shares"]
        for _, row in recalc["holdings_df"].iterrows()
    }

    assert set(holdings_end.keys()) == set(recalc_map.keys())
    for ticker, shares in holdings_end.items():
        assert shares == pytest.approx(recalc_map[ticker])


# ── build_price_lookup fallback chain ────────────────────────────────────────

def test_build_price_lookup_exact_beats_carry_beats_buy_fill():
    d1, d2, d3 = date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)
    holdings = {d1: {"AAPL": 10.0}, d2: {"AAPL": 10.0}, d3: {"AAPL": 10.0}}
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 99.0, "ticker": "AAPL"},
    ])
    # Exact snapshot only on d1; d2 and d3 have no snapshot at all.
    snaps = pd.DataFrame([
        {"snapshot_date": "2026-08-18", "ticker": "AAPL", "shares": 10, "close_price": 100.0},
    ])
    lookup = cvm.build_price_lookup(holdings, snaps, trades)
    assert lookup[("AAPL", d1)] == (100.0, "exact")     # tier (a)
    assert lookup[("AAPL", d2)] == (100.0, "carry")      # tier (b): carries d1's snapshot forward
    assert lookup[("AAPL", d3)] == (100.0, "carry")


def test_build_price_lookup_falls_to_buy_fill_when_never_snapshotted():
    d1 = date(2026, 8, 18)
    holdings = {d1: {"AAPL": 10.0}}
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-18T16:00:00-04:00", "action": "BUY",
         "shares": 10, "price": 123.45, "ticker": "AAPL"},
    ])
    lookup = cvm.build_price_lookup(holdings, None, trades)
    assert lookup[("AAPL", d1)] == (123.45, "buy_fill")   # tier (c)


def test_build_price_lookup_absent_when_nothing_resolvable():
    d1 = date(2026, 8, 18)
    holdings = {d1: {"GME": 10.0}}   # never snapshotted, never bought (edge case)
    lookup = cvm.build_price_lookup(holdings, None, None)
    assert ("GME", d1) not in lookup


def test_build_price_lookup_carry_forward_uses_nearest_prior_snapshot_only():
    """A snapshot AFTER the target date must never be used -- carry-forward
    is strictly backward-looking."""
    d1, d2 = date(2026, 8, 18), date(2026, 8, 19)
    holdings = {d1: {"AAPL": 10.0}}
    snaps = pd.DataFrame([
        {"snapshot_date": "2026-08-19", "ticker": "AAPL", "shares": 10, "close_price": 200.0},
    ])
    lookup = cvm.build_price_lookup(holdings, snaps, None)
    assert ("AAPL", d1) not in lookup   # only a FUTURE snapshot exists -- unresolvable


# ── gross_book_by_date + low_confidence_dates: split boundary ───────────────

def test_split_boundary_carry_forward_date_blanked_and_low_confidence():
    d_pre, d_split = date(2026, 8, 18), date(2026, 8, 19)
    holdings = {d_pre: {"AAPL": 10.0}, d_split: {"AAPL": 20.0}}
    split_tk = {"AAPL"}
    # Only a PRE-split snapshot exists; d_split has no exact match, so its
    # price would carry-forward the PRE-split $100 close against POST-split
    # 20 shares -- exactly the double-count Decision 2 guards against.
    snaps = pd.DataFrame([
        {"snapshot_date": "2026-08-18", "ticker": "AAPL", "shares": 10, "close_price": 100.0},
    ])
    lookup = cvm.build_price_lookup(holdings, snaps, None)
    assert lookup[("AAPL", d_split)][1] == "carry"

    gross = cvm.gross_book_by_date(holdings, lookup, split_tk)
    assert gross[d_pre] == pytest.approx(1000.0)     # exact match -- priced normally
    assert d_split not in gross                       # carry-forward + split window -- blanked

    low_conf = cvm.low_confidence_dates(holdings, split_tk)
    assert d_split in low_conf
    assert d_pre in low_conf   # disclosed regardless of blanking (ticker held, in split window)


def test_split_boundary_exact_match_date_not_blanked():
    """The SAME split-flagged ticker, on a date with an EXACT snapshot
    match, must be priced normally -- only carry-forward pricing is
    untrustworthy across a split, not every date the ticker is held."""
    d = date(2026, 8, 19)
    holdings = {d: {"AAPL": 20.0}}
    split_tk = {"AAPL"}
    snaps = pd.DataFrame([
        {"snapshot_date": "2026-08-19", "ticker": "AAPL", "shares": 20, "close_price": 50.0},
    ])
    lookup = cvm.build_price_lookup(holdings, snaps, None)
    assert lookup[("AAPL", d)][1] == "exact"

    gross = cvm.gross_book_by_date(holdings, lookup, split_tk)
    assert gross[d] == pytest.approx(1000.0)   # priced normally, not blanked


def test_gross_book_by_date_low_conf_ticker_ignored_when_not_in_split_window():
    """A ticker only becomes low-confidence when it's actually passed in
    `low_conf_tickers` -- a carry-forward price for an unrelated ticker is
    priced normally."""
    d1, d2 = date(2026, 8, 18), date(2026, 8, 19)
    holdings = {d2: {"MSFT": 10.0}}
    snaps = pd.DataFrame([
        {"snapshot_date": "2026-08-18", "ticker": "MSFT", "shares": 10, "close_price": 50.0},
    ])
    lookup = cvm.build_price_lookup(holdings, snaps, None)
    assert lookup[("MSFT", d2)][1] == "carry"
    gross = cvm.gross_book_by_date(holdings, lookup, low_conf_tickers=set())  # MSFT not flagged
    assert gross[d2] == pytest.approx(500.0)


def test_split_window_tickers_scoped_to_window_and_action():
    trades = _trades_df([
        {"id": 1, "traded_at": "2026-08-19T16:00:00-04:00", "action": "SPLIT",
         "shares": 20, "price": 50.0, "ticker": "AAPL"},
        {"id": 2, "traded_at": "2026-01-01T16:00:00-04:00", "action": "SPLIT",
         "shares": 40, "price": 25.0, "ticker": "MSFT"},   # outside window
        {"id": 3, "traded_at": "2026-08-19T16:00:00-04:00", "action": "BUY",
         "shares": 5, "price": 100.0, "ticker": "GOOG"},    # not a split
    ])
    out = cvm.split_window_tickers(trades, date(2026, 8, 18), date(2026, 8, 20))
    assert out == {"AAPL"}


def test_low_confidence_dates_empty_when_no_flagged_tickers():
    holdings = {date(2026, 8, 18): {"AAPL": 10.0}}
    assert cvm.low_confidence_dates(holdings, set()) == set()
    assert cvm.low_confidence_dates(holdings, None) == set()


# ── validate_reconstruction: gross fields are advisory, never gate ─────────

def test_validate_reconstruction_gross_drift_never_flips_ok_or_gate():
    d1 = date(2026, 8, 18)
    # Cash matches exactly (no cash mismatch); gross is wildly off.
    series = [{"date": d1, "cash_balance": -100.0, "gross_book": 5000.0}]
    recorded_df = pd.DataFrame([
        {"snapshot_date": "2026-08-18", "cash_balance": -100.0, "gross_book": 1000.0},
    ])
    result = cvm.validate_reconstruction(series, recorded_df)

    # Cash side: perfect match.
    assert result["ok"] is True
    assert result["mismatches"] == []

    # Gross side: way beyond _RECON_GROSS_REL_TOL, correctly flagged...
    assert result["gross_overlap_days"] == 1
    assert len(result["gross_mismatches"]) == 1
    assert result["gross_mismatches"][0]["date"] == d1
    assert result["max_gross_drift"] == pytest.approx(4000.0)

    # ...but it must NEVER affect `ok`, and render_gate must never withhold
    # a verdict because of it.
    assert result["ok"] is True
    gate = cvm.render_gate(result)
    assert gate["show_spanning_verdicts"] is True


def test_validate_reconstruction_gross_within_tolerance_no_mismatch():
    d1 = date(2026, 8, 18)
    recorded_gross = 10000.0
    tol = max(cvm._RECON_ABS_TOL, cvm._RECON_GROSS_REL_TOL * recorded_gross)
    series = [{"date": d1, "cash_balance": -100.0, "gross_book": recorded_gross + tol}]
    recorded_df = pd.DataFrame([
        {"snapshot_date": "2026-08-18", "cash_balance": -100.0, "gross_book": recorded_gross},
    ])
    result = cvm.validate_reconstruction(series, recorded_df)
    assert result["gross_mismatches"] == []
    assert result["gross_overlap_days"] == 1


def test_validate_reconstruction_gross_skips_when_either_side_is_none():
    d1 = date(2026, 8, 18)
    series = [{"date": d1, "cash_balance": -100.0, "gross_book": None}]  # reconstruction gap
    recorded_df = pd.DataFrame([
        {"snapshot_date": "2026-08-18", "cash_balance": -100.0, "gross_book": 1000.0},
    ])
    result = cvm.validate_reconstruction(series, recorded_df)
    assert result["gross_overlap_days"] == 0
    assert result["gross_mismatches"] == []


# ── Purity: no I/O anywhere in this module ───────────────────────────────────

_FORBIDDEN_TOKENS = (
    "requests.", "yfinance", "_client(", "supabase",
    "import streamlit", "st.session_state", "open(",
)


def test_module_source_contains_no_io_calls():
    """This module's whole contract is pure computation (module docstring:
    'no I/O, no Streamlit'). A crude but effective static check that the
    new functions didn't accidentally introduce a network/DB/file call."""
    source = inspect.getsource(cvm)
    for token in _FORBIDDEN_TOKENS:
        assert token not in source, f"forbidden I/O token found: {token!r}"
