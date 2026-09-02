"""
Tests for stock_analyzer/ticker_history.py — per-ticker round-trip episode
reconstruction for the 🧾 Prior Trades tab (F-237).

Covers: episode boundaries, weighted-avg-cost basis, partial sells, re-entry,
open positions, orphan SELLs, SPLIT rows, offline sentinel, missing ticker,
SPY unavailability, mixed-precision timestamps, legacy rows, null stored
realized_pnl, and build_pnl_series (basis shift, ghost placement, empty
price history).  Pure logic — no I/O, no Streamlit.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from stock_analyzer.ticker_history import (
    build_ticker_history, build_pnl_series, trades_fingerprint, chart_start_gap,
)


# ─── builders ────────────────────────────────────────────────────────────────

def _trade_row(
    ticker="AAA", action="BUY", shares=10.0, price=100.0,
    traded_at="2026-01-05T09:30:00Z",
    id_=1, realized_pnl=None, trigger_type="MANUAL",
    followed_signal=None, user_thesis=None, thesis_source=None,
    premortem_case_against=None, premortem_commitment=None,
    premortem_trigger_price=None, premortem_trigger_direction=None,
    notes=None, lesson=None, lesson_category=None,
    deviation_reason=None, decision_context=None,
    situational_category=None,
):
    return {
        "id":                        id_,
        "ticker":                    ticker,
        "action":                    action,
        "shares":                    shares,
        "price":                     price,
        "traded_at":                 traded_at,
        "realized_pnl":              realized_pnl,
        "trigger_type":              trigger_type,
        "followed_signal":           followed_signal,
        "user_thesis":               user_thesis,
        "thesis_source":             thesis_source,
        "premortem_case_against":    premortem_case_against,
        "premortem_commitment":      premortem_commitment,
        "premortem_trigger_price":   premortem_trigger_price,
        "premortem_trigger_direction": premortem_trigger_direction,
        "notes":                     notes,
        "lesson":                    lesson,
        "lesson_category":           lesson_category,
        "deviation_reason":          deviation_reason,
        "decision_context":          decision_context,
        "situational_category":      situational_category,
    }


def _df(rows):
    """Build a trades DataFrame from a list of row dicts."""
    return pd.DataFrame(rows)


def _price_df(pairs):
    """Build a price-history DataFrame from [(date_str, close), ...]."""
    dates, closes = zip(*pairs)
    idx = pd.to_datetime(list(dates))
    return pd.DataFrame({"Close": list(closes)}, index=idx)


def _spy_df(pairs):
    """Build a SPY history DataFrame from [(date_str, close), ...]."""
    dates, closes = zip(*pairs)
    idx = pd.to_datetime(list(dates))
    return pd.DataFrame({"Close": list(closes)}, index=idx)


# ─── 1. Single clean round trip ───────────────────────────────────────────────

def test_single_round_trip_entry_exit_hold_days():
    """Entry avg, exit avg, hold_days, and stored realized_pnl are correct."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    assert result is not None
    assert len(result["episodes"]) == 1

    ep = result["episodes"][0]
    assert ep["status"]       == "closed"
    assert ep["entry_avg"]    == pytest.approx(100.0)
    assert ep["exit_avg"]     == pytest.approx(110.0)
    assert ep["hold_days"]    == 41      # Jan 5 → Feb 15 = 41 days
    assert ep["realized_pnl"] == pytest.approx(100.0)   # stored value used
    assert ep["realized_estimated"] is False


def test_single_round_trip_totals():
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    t = result["totals"]
    assert t["n_round_trips"]  == 1
    assert t["n_open"]         == 0
    assert t["net_realized"]   == pytest.approx(100.0)
    assert t["wins"]           == 1
    assert t["losses"]         == 0


# ─── 2. Multi-buy averaged entry ─────────────────────────────────────────────

def test_multi_buy_averaged_entry_avg():
    """Two buys at different prices — entry_avg is capital-weighted."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="BUY",  shares=5.0,  price=120.0,
                   traded_at="2026-01-20T09:30:00Z"),
        _trade_row(id_=3, action="SELL", shares=15.0, price=130.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=350.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    ep = result["episodes"][0]
    # entry_avg = (10*100 + 5*120) / 15 = 1600/15 ≈ 106.67
    assert ep["entry_avg"] == pytest.approx(1600 / 15)
    assert ep["n_buys"]    == 2
    assert ep["n_sells"]   == 1
    assert ep["realized_pnl"] == pytest.approx(350.0)


# ─── 3. Partial sells that eventually close ───────────────────────────────────

def test_partial_sells_eventually_close():
    """Two SELL rows progressively reduce shares until the episode closes."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=20.0, price=50.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=60.0,
                   traded_at="2026-02-01T15:00:00Z", realized_pnl=100.0),
        _trade_row(id_=3, action="SELL", shares=10.0, price=70.0,
                   traded_at="2026-03-01T15:00:00Z", realized_pnl=200.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 4, 1))
    assert len(result["episodes"]) == 1
    ep = result["episodes"][0]
    assert ep["status"]       == "closed"
    assert ep["n_sells"]      == 2
    assert ep["shares_sold"]  == pytest.approx(20.0)
    assert ep["realized_pnl"] == pytest.approx(300.0)   # 100 + 200
    assert ep["exit_date"]    == date(2026, 3, 1)


# ─── 4. Re-entry after full exit → 2 episodes, newest first ──────────────────

def test_re_entry_two_episodes_newest_first():
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-01T15:00:00Z", realized_pnl=100.0),
        _trade_row(id_=3, action="BUY",  shares=5.0,  price=115.0,
                   traded_at="2026-03-01T09:30:00Z"),
        _trade_row(id_=4, action="SELL", shares=5.0,  price=120.0,
                   traded_at="2026-04-01T15:00:00Z", realized_pnl=25.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 5, 1))

    assert len(result["episodes"]) == 2
    # Newest first
    assert result["episodes"][0]["entry_date"] == date(2026, 3, 1)
    assert result["episodes"][1]["entry_date"] == date(2026, 1, 5)
    assert result["totals"]["n_round_trips"]   == 2
    assert result["totals"]["net_realized"]    == pytest.approx(125.0)


# ─── 5. Currently-open position ───────────────────────────────────────────────

def test_open_position_unrealized_and_no_vs_spy():
    rows = [
        _trade_row(id_=1, action="BUY", shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  current_price=115.0,
                                  today=date(2026, 3, 1))
    ep = result["episodes"][0]
    assert ep["status"]        == "open"
    assert ep["vs_spy_pct"]    is None
    assert ep["unrealized_pnl"] == pytest.approx(150.0)  # (115-100)*10
    assert ep["unrealized_pct"] == pytest.approx(15.0)
    assert result["totals"]["n_open"]        == 1
    assert result["totals"]["n_round_trips"] == 0


def test_open_position_no_current_price_unrealized_none():
    rows = [
        _trade_row(id_=1, action="BUY", shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  current_price=None,
                                  today=date(2026, 3, 1))
    ep = result["episodes"][0]
    assert ep["unrealized_pnl"] is None
    assert ep["unrealized_pct"] is None


# ─── 6. Orphan SELL → warning, no phantom episode ────────────────────────────

def test_orphan_sell_warning_no_phantom_episode():
    """A SELL with no prior BUY emits an orphan_sell warning and is skipped."""
    rows = [
        # Orphan sell first (chronologically earliest)
        _trade_row(id_=1, action="SELL", shares=5.0, price=110.0,
                   traded_at="2026-01-10T15:00:00Z", realized_pnl=50.0),
        # Legitimate round trip
        _trade_row(id_=2, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-02-01T09:30:00Z"),
        _trade_row(id_=3, action="SELL", shares=10.0, price=120.0,
                   traded_at="2026-03-01T15:00:00Z", realized_pnl=200.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 4, 1))

    orphan_warnings = [w for w in result["warnings"] if w["kind"] == "orphan_sell"]
    assert len(orphan_warnings) == 1

    # Only the legitimate episode should be present
    assert len(result["episodes"]) == 1
    assert result["episodes"][0]["entry_date"] == date(2026, 2, 1)
    assert result["episodes"][0]["realized_pnl"] == pytest.approx(200.0)


def test_orphan_sell_stats_scoped_to_legitimate_trades():
    """Totals reflect only the legitimate episode."""
    rows = [
        _trade_row(id_=1, action="SELL", shares=5.0, price=110.0,
                   traded_at="2026-01-10T15:00:00Z", realized_pnl=50.0),
        _trade_row(id_=2, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-02-01T09:30:00Z"),
        _trade_row(id_=3, action="SELL", shares=10.0, price=120.0,
                   traded_at="2026-03-01T15:00:00Z", realized_pnl=200.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 4, 1))
    t = result["totals"]
    assert t["n_round_trips"] == 1
    assert t["net_realized"]  == pytest.approx(200.0)   # orphan excluded


# ─── 7. SPLIT inside an open episode ─────────────────────────────────────────

def test_split_in_window_warning_episode_stays_open():
    """SPLIT overwrites running state and emits a warning; episode is NOT closed."""
    rows = [
        _trade_row(id_=1, action="BUY",   shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SPLIT",  shares=20.0, price=50.0,
                   traded_at="2026-02-01T09:30:00Z"),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))

    split_warnings = [w for w in result["warnings"] if w["kind"] == "split_in_window"]
    assert len(split_warnings) == 1

    assert len(result["episodes"]) == 1
    assert result["episodes"][0]["status"] == "open"


def test_split_rescales_episode_so_realized_pct_is_correct():
    """REGRESSION (Opus review, 2026-08-14): a SPLIT mid-round-trip left
    entry_avg on the PRE-split basis while shares_sold went POST-split, so
    `entry_avg * shares_sold` was a mismatched product and realized_pct came
    out wrong by exactly the split factor.

    BUY 10 @ $100 → SPLIT(20, $50) → SELL 20 @ $60.
    $1,000 in, $1,200 out = +20%. The bug reported +10%.
    """
    rows = [
        _trade_row(id_=1, action="BUY",   shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SPLIT", shares=20.0, price=50.0,
                   traded_at="2026-02-05T09:30:00Z"),
        _trade_row(id_=3, action="SELL",  shares=20.0, price=60.0,
                   traded_at="2026-03-05T09:30:00Z", realized_pnl=200.0),
    ]
    result = build_ticker_history(_df(rows), "AAA", today=date(2026, 4, 1))
    ep = result["episodes"][0]

    # Legs are rescaled onto the post-split basis...
    assert ep["entry_avg"]    == pytest.approx(50.0)
    assert ep["shares_total"] == pytest.approx(20.0)
    assert ep["shares_sold"]  == pytest.approx(20.0)
    # ...dollars are untouched (shares × price is split-invariant)...
    assert ep["realized_pnl"] == pytest.approx(200.0)
    # ...so the percentage is finally right.
    assert ep["realized_pct"] == pytest.approx(20.0)
    assert result["totals"]["net_realized_pct"] == pytest.approx(20.0)
    # Chart markers must match the split-adjusted price series behind them
    # (yfinance auto_adjust=True), not the as-recorded confirmations.
    assert [f["price"] for f in ep["fills"]] == pytest.approx([50.0, 60.0])


def test_split_does_not_rescale_a_recomputed_dollar_pnl():
    """A null stored realized_pnl is recomputed from the replayed basis. That
    dollar figure must be frozen BEFORE the split rescale, or it gets divided
    by the split factor along with the per-share numbers."""
    rows = [
        _trade_row(id_=1, action="BUY",   shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SPLIT", shares=20.0, price=50.0,
                   traded_at="2026-02-05T09:30:00Z"),
        _trade_row(id_=3, action="SELL",  shares=20.0, price=60.0,
                   traded_at="2026-03-05T09:30:00Z", realized_pnl=None),
    ]
    ep = build_ticker_history(_df(rows), "AAA",
                              today=date(2026, 4, 1))["episodes"][0]
    assert ep["realized_pnl"]       == pytest.approx(200.0)
    assert ep["realized_estimated"] is True


def test_vs_spy_is_none_when_spy_history_starts_after_entry():
    """Phase-1 carry-over #1: _spy_return_between anchors on the first close AT
    OR AFTER start_d, so a SPY frame beginning after the entry would return a
    TRUNCATED-window figure — wrong rather than absent. Coverage is checked per
    episode, so an uncovered trip reports None while a covered one still gets a
    real number."""
    rows = [
        # Old trip — outside the SPY frame below.
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-01-25T09:30:00Z", realized_pnl=100.0),
        # Recent trip — inside it.
        _trade_row(id_=3, action="BUY",  shares=10.0, price=120.0,
                   traded_at="2026-03-05T09:30:00Z"),
        _trade_row(id_=4, action="SELL", shares=10.0, price=132.0,
                   traded_at="2026-03-25T09:30:00Z", realized_pnl=120.0),
    ]
    spy_idx = pd.date_range("2026-03-01", "2026-04-01", freq="D")
    spy = pd.DataFrame({"Close": [400.0 + i for i in range(len(spy_idx))]},
                       index=spy_idx)
    eps = build_ticker_history(_df(rows), "AAA", spy_history_df=spy,
                               today=date(2026, 4, 1))["episodes"]
    recent, old = eps[0], eps[1]           # newest first
    assert old["entry_date"]  == date(2026, 1, 5)
    assert old["vs_spy_pct"]  is None      # NOT a truncated-window number
    assert recent["entry_date"] == date(2026, 3, 5)
    assert recent["vs_spy_pct"] is not None


def test_vs_spy_covered_when_spy_starts_exactly_on_entry_date():
    """Boundary: coverage is `first <= start_d`, so a SPY frame beginning on the
    entry date itself counts as covered and must still produce a figure."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-03-02T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-03-20T09:30:00Z", realized_pnl=100.0),
    ]
    spy_idx = pd.date_range("2026-03-02", "2026-04-01", freq="D")   # starts ON entry
    spy = pd.DataFrame({"Close": [400.0 + i for i in range(len(spy_idx))]},
                       index=spy_idx)
    ep = build_ticker_history(_df(rows), "AAA", spy_history_df=spy,
                              today=date(2026, 4, 1))["episodes"][0]
    assert ep["vs_spy_pct"] is not None


def test_sell_before_split_keeps_dollars_and_rescales_shares():
    """REGRESSION (Opus re-review, 2026-08-14): a sell that happens BEFORE the
    split is the ordering that proves freezing each leg's dollar P&L at append
    time was mandatory, not stylistic.

    BUY 10 @$100 → SELL 4 @$120 ($80) → SPLIT(12, $50) → SELL 12 @$70 ($240).
    $1,000 in, $1,320 out = +32%.

    The split rescale hits the ALREADY-APPENDED first sell (4 sh @$120 → 8 sh
    @$60). If the fallback P&L were still derived inside _build_episode, that
    leg would compute (60 − 100) × 8 = −$320 against its un-rescaled basis.
    """
    rows = [
        _trade_row(id_=1, action="BUY",   shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL",  shares=4.0,  price=120.0,
                   traded_at="2026-01-20T09:30:00Z", realized_pnl=80.0),
        _trade_row(id_=3, action="SPLIT", shares=12.0, price=50.0,
                   traded_at="2026-02-05T09:30:00Z"),
        _trade_row(id_=4, action="SELL",  shares=12.0, price=70.0,
                   traded_at="2026-03-05T09:30:00Z", realized_pnl=240.0),
    ]
    ep = build_ticker_history(_df(rows), "AAA",
                              today=date(2026, 4, 1))["episodes"][0]
    assert ep["status"]        == "closed"
    assert ep["entry_avg"]     == pytest.approx(50.0)
    assert ep["shares_total"]  == pytest.approx(20.0)
    assert ep["shares_sold"]   == pytest.approx(20.0)
    assert ep["exit_avg"]      == pytest.approx(66.0)
    assert ep["realized_pnl"]  == pytest.approx(320.0)
    assert ep["realized_pct"]  == pytest.approx(32.0)


def test_open_episode_exposes_shares_open_after_partial_trim():
    """REGRESSION (Opus review): the open card rendered shares_total as
    "sh held", contradicting the unrealized P&L on the same row, which is
    computed off shares still held."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=100.0, price=10.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=40.0,  price=12.0,
                   traded_at="2026-02-05T09:30:00Z", realized_pnl=80.0),
    ]
    ep = build_ticker_history(_df(rows), "AAA", current_price=15.0,
                              today=date(2026, 4, 1))["episodes"][0]
    assert ep["status"]         == "open"
    assert ep["shares_total"]   == pytest.approx(100.0)
    assert ep["shares_sold"]    == pytest.approx(40.0)
    assert ep["shares_open"]    == pytest.approx(60.0)
    # Unrealized is computed off shares_open, so the two agree on the card.
    assert ep["unrealized_pnl"] == pytest.approx((15.0 - 10.0) * 60.0)


def test_oversell_emits_warning():
    """Selling more than the journal shows held is a journal inconsistency;
    recalculate_from_trades warns on it, so this must too rather than
    silently clamping and understating realized_pct."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=10.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=25.0, price=12.0,
                   traded_at="2026-02-05T09:30:00Z", realized_pnl=50.0),
    ]
    result = build_ticker_history(_df(rows), "AAA", today=date(2026, 4, 1))
    assert [w["kind"] for w in result["warnings"]] == ["oversell"]


def test_ghost_series_suppressed_when_a_position_is_open():
    """The ghost answers "what if I hadn't sold?" — meaningless (and visually
    contradictory) when a later re-entry means you DID hold through that
    window."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-05T09:30:00Z", realized_pnl=100.0),
        _trade_row(id_=3, action="BUY",  shares=10.0, price=105.0,
                   traded_at="2026-03-05T09:30:00Z"),
    ]
    result = build_ticker_history(_df(rows), "AAA", current_price=120.0,
                                  today=date(2026, 4, 1))
    idx = pd.date_range("2026-01-01", "2026-04-01", freq="D")
    px  = pd.DataFrame({"Close": [100.0 + i * 0.2 for i in range(len(idx))]},
                       index=idx)
    series = build_pnl_series(result["episodes"], px)
    assert all(s["ghost_dates"] == [] for s in series)


def test_split_before_episode_opens_no_warning():
    """A SPLIT when no episode is open is silent (no split_in_window warning)."""
    rows = [
        _trade_row(id_=1, action="SPLIT", shares=20.0, price=50.0,
                   traded_at="2026-01-01T09:30:00Z"),
        _trade_row(id_=2, action="BUY",   shares=10.0, price=55.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=3, action="SELL",  shares=10.0, price=60.0,
                   traded_at="2026-02-01T15:00:00Z", realized_pnl=50.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))

    split_warnings = [w for w in result["warnings"] if w["kind"] == "split_in_window"]
    assert len(split_warnings) == 0
    assert len(result["episodes"]) == 1
    assert result["episodes"][0]["status"] == "closed"


# ─── 8. trades_df=None → None; empty df → dict with episodes=[] ──────────────

def test_none_trades_df_returns_none():
    """None trades_df is the offline sentinel — must return None, not {}."""
    result = build_ticker_history(None, "AAA")
    assert result is None


def test_empty_df_returns_dict_not_none():
    """An empty (but real) DataFrame → dict with episodes=[], NOT None."""
    df = pd.DataFrame(columns=[
        "ticker", "action", "shares", "price", "traded_at", "id", "realized_pnl",
    ])
    result = build_ticker_history(df, "AAA")
    assert result is not None
    assert isinstance(result, dict)
    assert result["episodes"] == []


def test_none_and_empty_df_distinct():
    """None (offline) and empty DataFrame (checked) are different return values."""
    df = pd.DataFrame(columns=[
        "ticker", "action", "shares", "price", "traded_at", "id", "realized_pnl",
    ])
    assert build_ticker_history(None, "AAA") is None
    assert build_ticker_history(df, "AAA") is not None


# ─── 9. Ticker with no rows ───────────────────────────────────────────────────

def test_ticker_with_no_rows_returns_empty_episodes():
    rows = [_trade_row(ticker="BBB", id_=1, action="BUY")]
    result = build_ticker_history(_df(rows), "AAA")
    assert result is not None
    assert result["episodes"] == []
    assert result["ticker"]   == "AAA"


def test_ticker_with_no_rows_totals_zeroed():
    rows = [_trade_row(ticker="BBB", id_=1, action="BUY")]
    result = build_ticker_history(_df(rows), "AAA")
    t = result["totals"]
    assert t["n_round_trips"]  == 0
    assert t["n_open"]         == 0
    assert t["net_realized"]   == 0.0
    assert t["wins"]           == 0
    assert t["losses"]         == 0
    assert t["avg_hold_days"]  is None


# ─── 10. SPY unavailable ──────────────────────────────────────────────────────

def test_spy_none_vs_spy_is_none_not_zero():
    """vs_spy_pct must be None when SPY history is absent — NEVER 0 as filler."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  spy_history_df=None,
                                  today=date(2026, 3, 1))
    assert result["spy_available"] is False
    ep = result["episodes"][0]
    assert ep["vs_spy_pct"] is None
    assert ep["vs_spy_pct"] != 0.0     # not zero as a stand-in
    assert result["totals"]["vs_spy_pct"]    is None
    assert result["totals"]["spy_return_pct"] is None


def test_spy_available_false_on_empty_spy_df():
    """Empty SPY DataFrame → spy_available=False."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  spy_history_df=pd.DataFrame(),
                                  today=date(2026, 3, 1))
    assert result["spy_available"] is False


# ─── 11. Mixed-precision traded_at strings ────────────────────────────────────

def test_mixed_precision_traded_at_all_rows_parse():
    """
    ISO8601 format with utc=True must parse both bare-second and microsecond
    timestamps in the same column without silently NaT-ing either.
    This is the live bug class documented in feedback_pandas_mixed_tz_parsing.
    """
    rows = [
        # Raw-SQL style — no microseconds
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        # Python-SDK style — microsecond precision
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00.123456Z",
                   realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    assert result is not None
    assert len(result["episodes"]) == 1
    ep = result["episodes"][0]
    assert ep["n_buys"]  == 1
    assert ep["n_sells"] == 1
    assert ep["status"]  == "closed"


def test_mixed_precision_ordering_preserved():
    """NaT-induced reordering would put the SELL before the BUY — must not happen."""
    rows = [
        # Microsecond-precision BUY
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00.000000Z"),
        # Bare-second SELL (would NaT if pandas infers format from row 0)
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    # If SELL appeared before BUY it would be an orphan — no episode
    assert len(result["episodes"]) == 1
    assert result["episodes"][0]["status"] == "closed"
    # No orphan_sell warning means the order was correct
    orphans = [w for w in result["warnings"] if w["kind"] == "orphan_sell"]
    assert len(orphans) == 0


# ─── 12. Legacy rows missing newer optional columns ───────────────────────────

def test_legacy_row_missing_journal_columns_no_keyerror():
    """
    Rows that pre-date journalling columns are backfilled to None in
    db.load_trades. The module must not KeyError; all journal fields should
    degrade gracefully to None.
    """
    # Minimal rows — no journalling columns at all
    rows = [
        {"id": 1, "ticker": "AAA", "action": "BUY",  "shares": 10.0,
         "price": 100.0, "traded_at": "2026-01-05T09:30:00Z"},
        {"id": 2, "ticker": "AAA", "action": "SELL", "shares": 10.0,
         "price": 110.0, "traded_at": "2026-02-15T15:00:00Z",
         "realized_pnl": 100.0},
    ]
    result = build_ticker_history(pd.DataFrame(rows), "AAA",
                                  today=date(2026, 3, 1))
    assert result is not None
    ep = result["episodes"][0]
    # Journal fields must be None, not raise KeyError
    assert ep["journal"]["user_thesis"]           is None
    assert ep["journal"]["premortem_case_against"] is None
    assert ep["journal"]["lesson"]                is None
    assert ep["journal"]["lesson_category"]       is None
    assert ep["journal"]["situational_category"]  is None
    assert ep["context"]        is None
    assert ep["followed_signal"] is None


def test_situational_category_read_from_the_opening_buy_not_the_closing_sell():
    """
    situational_category (F-257) is a BUY-only, entry-side tag — it must be
    read off the opening_row, and a value on the closing SELL row (which
    should never be written there) must not leak through.
    """
    rows = [
        _trade_row(id_=1, action="BUY", shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z",
                   situational_category="Earnings Catalyst"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0,
                   situational_category="Technical Read"),
    ]
    result = build_ticker_history(_df(rows), "AAA", today=date(2026, 3, 1))
    ep = result["episodes"][0]
    assert ep["journal"]["situational_category"] == "Earnings Catalyst"


def test_legacy_row_missing_trigger_type_fill_has_none():
    """trigger_type=None in fill when column is absent from the row."""
    rows = [
        {"id": 1, "ticker": "AAA", "action": "BUY",  "shares": 10.0,
         "price": 100.0, "traded_at": "2026-01-05T09:30:00Z"},
        {"id": 2, "ticker": "AAA", "action": "SELL", "shares": 10.0,
         "price": 110.0, "traded_at": "2026-02-15T15:00:00Z",
         "realized_pnl": 100.0},
    ]
    result = build_ticker_history(pd.DataFrame(rows), "AAA",
                                  today=date(2026, 3, 1))
    ep = result["episodes"][0]
    for fill in ep["fills"]:
        assert "trigger_type" in fill
        assert fill["trigger_type"] is None


# ─── 13. Stored realized_pnl null → recomputed leg + realized_estimated=True ──

def test_null_stored_pnl_recomputed_and_estimated_flag():
    """
    When realized_pnl on a SELL row is null, the module recomputes the leg as
    (sell_price - running_avg_cost) * shares and sets realized_estimated=True.
    """
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=None),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    ep = result["episodes"][0]
    assert ep["realized_estimated"] is True
    # Recomputed: (110 - 100) * 10 = 100
    assert ep["realized_pnl"] == pytest.approx(100.0)


def test_stored_pnl_present_estimated_is_false():
    """When all SELL rows have stored pnl, realized_estimated must be False."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    assert result["episodes"][0]["realized_estimated"] is False


def test_mixed_null_and_stored_pnl_partial_estimated():
    """
    Two SELLs: one with stored pnl, one without.  Episode is estimated because
    at least one leg was recomputed; total is the sum of both legs.
    """
    rows = [
        _trade_row(id_=1, action="BUY",  shares=20.0, price=50.0,
                   traded_at="2026-01-05T09:30:00Z"),
        # First SELL: stored pnl = 100
        _trade_row(id_=2, action="SELL", shares=10.0, price=60.0,
                   traded_at="2026-02-01T15:00:00Z", realized_pnl=100.0),
        # Second SELL: no stored pnl → recomputed as (70-50)*10 = 200
        _trade_row(id_=3, action="SELL", shares=10.0, price=70.0,
                   traded_at="2026-03-01T15:00:00Z", realized_pnl=None),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 4, 1))
    ep = result["episodes"][0]
    assert ep["realized_estimated"] is True
    assert ep["realized_pnl"] == pytest.approx(300.0)   # 100 + 200


# ─── 14. build_pnl_series ────────────────────────────────────────────────────

def test_pnl_series_basis_shift_on_add():
    """
    The basis on a bar between two BUY fills is the avg cost of only the
    earlier buy (not the one dated AFTER the bar).  After the second buy
    the basis shifts to the weighted average.
    """
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="BUY",  shares=10.0, price=120.0,
                   traded_at="2026-01-20T09:30:00Z"),
        _trade_row(id_=3, action="SELL", shares=20.0, price=130.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=500.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    episodes = result["episodes"]

    price_df = _price_df([
        ("2026-01-05", 100.0),  # entry bar
        ("2026-01-10", 105.0),  # between the two buys: basis = 100
        ("2026-01-20", 110.0),  # same day as second buy: basis = 110
        ("2026-01-25", 115.0),  # after second buy: basis = 110
        ("2026-02-15", 130.0),  # exit bar
    ])

    series_list = build_pnl_series(episodes, price_df)
    assert len(series_list) == 1
    s = series_list[0]

    # Jan 10: basis = 100 (only first buy); pct = (105/100-1)*100 = 5.0
    idx_10 = s["dates"].index(date(2026, 1, 10))
    assert s["pct"][idx_10] == pytest.approx(5.0)

    # Jan 25: basis = (10*100 + 10*120)/20 = 110; pct = (115/110-1)*100
    idx_25 = s["dates"].index(date(2026, 1, 25))
    assert s["pct"][idx_25] == pytest.approx((115 / 110 - 1) * 100)

    # Basis on Jan 20 (same day as the add) includes the second buy
    idx_20 = s["dates"].index(date(2026, 1, 20))
    assert s["pct"][idx_20] == pytest.approx((110 / 110 - 1) * 100)   # == 0


def test_pnl_series_ghost_only_on_most_recent_closed():
    """
    Ghost series (faded continuation after exit) is emitted for the most
    recent closed episode only.  The older closed episode gets empty ghost lists.
    """
    rows = [
        # Older episode: Jan → Feb
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-01T15:00:00Z", realized_pnl=100.0),
        # Newer episode: Feb → Mar (most recent closed)
        _trade_row(id_=3, action="BUY",  shares=10.0, price=115.0,
                   traded_at="2026-02-15T09:30:00Z"),
        _trade_row(id_=4, action="SELL", shares=10.0, price=120.0,
                   traded_at="2026-03-01T15:00:00Z", realized_pnl=50.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 4, 1))
    episodes = result["episodes"]
    # Verify newest-first ordering
    assert episodes[0]["entry_date"] == date(2026, 2, 15)
    assert episodes[1]["entry_date"] == date(2026, 1, 5)

    price_df = _price_df([
        ("2026-01-05", 100.0),
        ("2026-02-01", 110.0),
        ("2026-02-15", 115.0),
        ("2026-03-01", 120.0),
        ("2026-03-15", 125.0),  # post-exit: ghost for most-recent-closed
        ("2026-04-01", 130.0),  # post-exit: ghost for most-recent-closed
    ])

    series_list = build_pnl_series(episodes, price_df)
    assert len(series_list) == 2

    # episodes[0] = newest closed (most recent) → ghost populated
    newest_s = series_list[0]
    assert newest_s["episode_idx"] == 0
    assert len(newest_s["ghost_dates"]) > 0
    assert len(newest_s["ghost_pct"])   > 0

    # episodes[1] = older closed → no ghost
    older_s = series_list[1]
    assert older_s["episode_idx"] == 1
    assert older_s["ghost_dates"] == []
    assert older_s["ghost_pct"]   == []


def test_pnl_series_ghost_uses_final_basis():
    """Ghost series prices the continuation against the episode's final basis."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-01T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))

    price_df = _price_df([
        ("2026-01-05", 100.0),
        ("2026-02-01", 110.0),
        ("2026-02-15", 120.0),  # ghost: (120/100-1)*100 = 20%
    ])

    series_list = build_pnl_series(result["episodes"], price_df)
    s = series_list[0]
    assert date(2026, 2, 15) in s["ghost_dates"]
    idx = s["ghost_dates"].index(date(2026, 2, 15))
    assert s["ghost_pct"][idx] == pytest.approx(20.0)  # (120/100-1)*100


def test_pnl_series_empty_on_none_price_history():
    """Returns [] when price_history_df is None."""
    assert build_pnl_series([], None) == []


def test_pnl_series_empty_on_empty_price_df():
    """Returns [] when price_history_df has no rows."""
    assert build_pnl_series([], pd.DataFrame({"Close": []})) == []


def test_pnl_series_empty_on_missing_close_column():
    """Returns [] when price_history_df lacks the Close column."""
    df = pd.DataFrame({"Open": [100.0]}, index=pd.to_datetime(["2026-01-05"]))
    assert build_pnl_series([], df) == []


def test_pnl_series_no_ghost_when_ghost_from_last_exit_false():
    """Ghost series is suppressed when ghost_from_last_exit=False."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-01T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    price_df = _price_df([
        ("2026-01-05", 100.0),
        ("2026-02-01", 110.0),
        ("2026-02-15", 120.0),  # post-exit
    ])
    series_list = build_pnl_series(result["episodes"], price_df,
                                   ghost_from_last_exit=False)
    assert series_list[0]["ghost_dates"] == []
    assert series_list[0]["ghost_pct"]   == []


# ─── Additional correctness tests ────────────────────────────────────────────

def test_ticker_is_case_insensitive():
    rows = [
        _trade_row(ticker="aaA", id_=1, action="BUY",  shares=5.0,
                   price=100.0, traded_at="2026-01-05T09:30:00Z"),
        _trade_row(ticker="AAA", id_=2, action="SELL", shares=5.0,
                   price=110.0, traded_at="2026-02-01T15:00:00Z",
                   realized_pnl=50.0),
    ]
    result = build_ticker_history(_df(rows), "aaa",
                                  today=date(2026, 3, 1))
    assert len(result["episodes"]) == 1
    assert result["ticker"] == "AAA"


def test_fills_include_trigger_type():
    """Each fill dict carries trigger_type from the trade row."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z", trigger_type="ENGINE"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0,
                   trigger_type="STOP"),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    ep = result["episodes"][0]
    fill_map = {f["action"]: f for f in ep["fills"]}
    assert fill_map["BUY"]["trigger_type"]  == "ENGINE"
    assert fill_map["SELL"]["trigger_type"] == "STOP"


def test_context_parsed_from_json_string():
    """decision_context stored as a JSON string is parsed to a dict."""
    ctx_str = '{"macro": "expansion", "portfolio_value": 50000}'
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z",
                   decision_context=ctx_str),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    ctx = result["episodes"][0]["context"]
    assert isinstance(ctx, dict)
    assert ctx["macro"] == "expansion"


def test_context_passed_as_dict():
    """decision_context stored as a dict is returned as-is."""
    ctx = {"regime": "bull", "cash": 1234.56}
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z",
                   decision_context=ctx),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    assert result["episodes"][0]["context"] == ctx


def test_vs_spy_pct_computes_correctly_with_spy():
    """vs_spy_pct = realized_pct - SPY return over the same window."""
    rows = [
        _trade_row(id_=1, action="BUY",  shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
        _trade_row(id_=2, action="SELL", shares=10.0, price=110.0,
                   traded_at="2026-02-15T15:00:00Z", realized_pnl=100.0),
    ]
    # SPY: 400 → 408 over the window = +2%
    spy = _spy_df([
        ("2026-01-05", 400.0),
        ("2026-02-15", 408.0),
    ])
    result = build_ticker_history(_df(rows), "AAA",
                                  spy_history_df=spy,
                                  today=date(2026, 3, 1))
    ep = result["episodes"][0]
    assert result["spy_available"] is True
    # realized_pct = (110-100)/100*10 / (100*10) * 100 = 10%
    # spy_return ≈ (408-400)/400*100 = 2%
    # vs_spy_pct ≈ 10 - 2 = 8%
    assert ep["vs_spy_pct"] == pytest.approx(8.0)


def test_realized_pct_none_when_no_sells():
    """An open position with no sells has realized_pct=None."""
    rows = [
        _trade_row(id_=1, action="BUY", shares=10.0, price=100.0,
                   traded_at="2026-01-05T09:30:00Z"),
    ]
    result = build_ticker_history(_df(rows), "AAA",
                                  today=date(2026, 3, 1))
    assert result["episodes"][0]["realized_pct"] is None


# ─── trades_fingerprint (2026-09-02 Prior Trades recompute-cost follow-up) ────

def test_trades_fingerprint_none_or_empty_df_returns_empty_tuple():
    assert trades_fingerprint(None, "AAA") == ()
    assert trades_fingerprint(_df([]), "AAA") == ()


def test_trades_fingerprint_no_rows_for_ticker_returns_empty_tuple():
    rows = [_trade_row(ticker="BBB", id_=1)]
    assert trades_fingerprint(_df(rows), "AAA") == ()


def test_trades_fingerprint_is_case_insensitive_on_ticker():
    rows = [_trade_row(ticker="aaa", id_=1)]
    assert trades_fingerprint(_df(rows), "AAA") != ()
    assert trades_fingerprint(_df(rows), "AAA") == trades_fingerprint(_df(rows), "aaa")


def test_trades_fingerprint_changes_when_a_row_is_added():
    base = [_trade_row(ticker="AAA", id_=1, action="BUY", shares=10.0, price=100.0)]
    added = base + [_trade_row(ticker="AAA", id_=2, action="SELL", shares=10.0, price=110.0)]
    assert trades_fingerprint(_df(base), "AAA") != trades_fingerprint(_df(added), "AAA")


def test_trades_fingerprint_changes_when_shares_or_price_is_edited():
    original = [_trade_row(ticker="AAA", id_=1, shares=10.0, price=100.0)]
    edited   = [_trade_row(ticker="AAA", id_=1, shares=15.0, price=100.0)]
    assert trades_fingerprint(_df(original), "AAA") != trades_fingerprint(_df(edited), "AAA")


def test_trades_fingerprint_unchanged_when_only_free_text_fields_differ():
    """Notes/lesson/thesis don't feed the PnL/chart math, so editing them
    must NOT invalidate a cache keyed on this fingerprint."""
    original = [_trade_row(ticker="AAA", id_=1, notes="first take", lesson=None)]
    edited   = [_trade_row(ticker="AAA", id_=1, notes="revised note", lesson="learned something")]
    assert trades_fingerprint(_df(original), "AAA") == trades_fingerprint(_df(edited), "AAA")


def test_trades_fingerprint_unaffected_by_other_tickers():
    rows_a = [_trade_row(ticker="AAA", id_=1)]
    rows_b = rows_a + [_trade_row(ticker="BBB", id_=2, shares=999.0)]
    assert trades_fingerprint(_df(rows_a), "AAA") == trades_fingerprint(_df(rows_b), "AAA")


def test_trades_fingerprint_missing_ticker_column_returns_empty_tuple():
    assert trades_fingerprint(pd.DataFrame({"foo": [1, 2]}), "AAA") == ()


# ─── chart_start_gap (2026-09-02 Prior Trades cropped-chart follow-up) ────────

def test_chart_start_gap_true_when_px_starts_after_first_entry():
    assert chart_start_gap(date(2026, 1, 1), date(2026, 3, 1)) is True


def test_chart_start_gap_false_when_px_reaches_first_entry_or_earlier():
    assert chart_start_gap(date(2026, 1, 1), date(2026, 1, 1)) is False
    assert chart_start_gap(date(2026, 3, 1), date(2026, 1, 1)) is False


def test_chart_start_gap_false_when_either_boundary_is_unknown():
    assert chart_start_gap(None, date(2026, 1, 1)) is False
    assert chart_start_gap(date(2026, 1, 1), None) is False
    assert chart_start_gap(None, None) is False
