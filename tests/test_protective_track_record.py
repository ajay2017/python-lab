"""
Tests for stock_analyzer/protective_track_record.py — the 🛡️ Defense facet
of the 🎯 Engine Track Record card (F-229 Phase 2).

Covers the invariants called out in the design doc
(docs/plans/engine-track-record-meter.md, Phase 2 section):
  - sign convention (protect_alpha_pct is spy − name, sign-flipped vs BUY-side)
  - never-negative-on-absent (building/no-data render neutral, never a computed number)
  - dedup/anti-inflation (one row per distinct ticker after collapse_by_ticker)
  - severity escalation (TRIM→EXIT keeps the earlier anchor, worse severity label)
  - population-parity (n_mature == the exact population protect_alpha averages)
  - scope invariant (WATCH/RISK_OFF never reach protective_headline)
  - maturity boundary (min_days - 1 excluded, min_days included)
"""

import math
from datetime import date

import pytest

from stock_analyzer import protective_track_record as ptr


# ─── builders ───────────────────────────────────────────────────────────────

def _sig_row(ticker="AAA", signal_date=date(2026, 1, 1), signal_type="EXIT",
             price_at_signal=100.0, composite_score=40.0):
    return {
        "ticker": ticker,
        "signal_date": signal_date,
        "signal_type": signal_type,
        "price_at_signal": price_at_signal,
        "composite_score": composite_score,
        "dd_from_peak_pct": None,
        "pnl_pct": None,
        "below_ma_count": None,
        "rel_strength": None,
    }


def _erow(ticker="AAA", signal_date=date(2026, 1, 1), signal_type="EXIT",
           price_at_signal=100.0, name_return_pct=0.0, spy_return_pct=0.0,
           protect_alpha_pct=0.0, days_since=30, maturing=False):
    return {
        "ticker": ticker,
        "signal_date": signal_date,
        "signal_type": signal_type,
        "price_at_signal": price_at_signal,
        "name_return_pct": name_return_pct,
        "spy_return_pct": spy_return_pct,
        "protect_alpha_pct": protect_alpha_pct,
        "days_since": days_since,
        "maturing": maturing,
    }


# ─── compute_protective_outcomes — sign convention ─────────────────────────

def test_sign_convention_name_falls_spy_rises_is_positive_alpha():
    """Flagged name falls while SPY rises → protect_alpha_pct > 0 (caution was right)."""
    signals = [_sig_row(ticker="BAD", signal_date=date(2026, 1, 1), price_at_signal=100.0)]
    current_prices = {"BAD": 80.0}   # -20%
    spy = {date(2026, 1, 1): 100.0, date(2026, 2, 1): 110.0}   # +10%
    out = ptr.compute_protective_outcomes(
        signals, current_prices, today=date(2026, 2, 1),
        spy_close_by_date=spy, min_days=5,
    )
    assert len(out) == 1
    row = out[0]
    assert row["name_return_pct"] == pytest.approx(-20.0)
    assert row["spy_return_pct"] == pytest.approx(10.0)
    assert row["protect_alpha_pct"] == pytest.approx(30.0)
    assert row["protect_alpha_pct"] > 0


def test_sign_convention_name_rises_spy_falls_is_negative_alpha():
    """Flagged name rises while SPY falls → protect_alpha_pct < 0 (honest negative,
    the call ran early — must NOT be suppressed or floored at zero)."""
    signals = [_sig_row(ticker="RECOVERED", signal_date=date(2026, 1, 1), price_at_signal=100.0)]
    current_prices = {"RECOVERED": 120.0}   # +20%
    spy = {date(2026, 1, 1): 100.0, date(2026, 2, 1): 90.0}   # -10%
    out = ptr.compute_protective_outcomes(
        signals, current_prices, today=date(2026, 2, 1),
        spy_close_by_date=spy, min_days=5,
    )
    row = out[0]
    assert row["name_return_pct"] == pytest.approx(20.0)
    assert row["spy_return_pct"] == pytest.approx(-10.0)
    assert row["protect_alpha_pct"] == pytest.approx(-30.0)
    assert row["protect_alpha_pct"] < 0


# ─── compute_protective_outcomes — never-negative-on-absent ────────────────

def test_missing_current_price_yields_none_alpha_not_negative():
    signals = [_sig_row(ticker="NOPX", price_at_signal=100.0)]
    out = ptr.compute_protective_outcomes(
        signals, current_prices={}, today=date(2026, 2, 1),
        spy_close_by_date={date(2026, 1, 1): 100.0, date(2026, 2, 1): 110.0},
        min_days=5,
    )
    assert out[0]["name_return_pct"] is None
    assert out[0]["protect_alpha_pct"] is None


def test_falsy_price_at_signal_yields_none_alpha():
    signals = [_sig_row(ticker="ZEROPX", price_at_signal=None)]
    out = ptr.compute_protective_outcomes(
        signals, current_prices={"ZEROPX": 50.0}, today=date(2026, 2, 1),
        spy_close_by_date={date(2026, 1, 1): 100.0}, min_days=5,
    )
    assert out[0]["price_at_signal"] is None
    assert out[0]["name_return_pct"] is None
    assert out[0]["protect_alpha_pct"] is None


def test_missing_spy_series_yields_none_alpha():
    signals = [_sig_row(ticker="AAA", price_at_signal=100.0)]
    out = ptr.compute_protective_outcomes(
        signals, current_prices={"AAA": 90.0}, today=date(2026, 2, 1),
        spy_close_by_date=None, min_days=5,
    )
    assert out[0]["spy_return_pct"] is None
    assert out[0]["protect_alpha_pct"] is None


# ─── NaN safety — live bug reproduction ────────────────────────────────────
# A legacy exit_signals row with a NULL price_at_signal reads back from the
# DataFrame as float('nan'), NOT None (pandas convention for missing numeric
# data). NaN is truthy in Python (only 0.0 is falsy for floats), so a bare
# `if pas else None` guard let it through as a "valid" price, poisoning the
# downstream mean and rendering the literal string "nan" on the live card.

def test_nan_price_at_signal_treated_as_missing_not_valid_price():
    """A NaN price_at_signal (legacy NULL row read back via pandas) must be
    treated exactly like None — missing data, not a valid zero-ish price."""
    signals = [_sig_row(ticker="NANPX", price_at_signal=float("nan"))]
    out = ptr.compute_protective_outcomes(
        signals, current_prices={"NANPX": 50.0}, today=date(2026, 2, 1),
        spy_close_by_date={date(2026, 1, 1): 100.0, date(2026, 2, 1): 110.0},
        min_days=5,
    )
    assert out[0]["price_at_signal"] is None
    assert out[0]["name_return_pct"] is None
    assert out[0]["protect_alpha_pct"] is None


def test_nan_current_price_treated_as_missing_not_valid_price():
    """A NaN current price (e.g. a bad live-price fetch) must be treated
    exactly like a missing entry in current_prices — the OTHER entry point
    that must be NaN-guarded, not just price_at_signal."""
    signals = [_sig_row(ticker="NANCUR", price_at_signal=100.0)]
    out = ptr.compute_protective_outcomes(
        signals, current_prices={"NANCUR": float("nan")}, today=date(2026, 2, 1),
        spy_close_by_date={date(2026, 1, 1): 100.0, date(2026, 2, 1): 110.0},
        min_days=5,
    )
    assert out[0]["name_return_pct"] is None
    assert out[0]["protect_alpha_pct"] is None


def test_nan_price_at_signal_excluded_from_headline_average_live_scenario():
    """Reproduces the exact live scenario: 9 flagged names, 1 with a NaN
    price_at_signal mixed in among otherwise-valid mature+priced rows. The
    NaN row must be excluded from n_mature/the average (same as if it were
    None), and protect_alpha must be a real float — NEVER nan."""
    signals = (
        [
            _sig_row(ticker=f"GOOD{i}", signal_date=date(2026, 1, 1),
                      price_at_signal=100.0)
            for i in range(8)
        ]
        + [_sig_row(ticker="NANONE", signal_date=date(2026, 1, 1),
                      price_at_signal=float("nan"))]
    )
    current_prices = {f"GOOD{i}": 80.0 for i in range(8)}
    current_prices["NANONE"] = 80.0
    spy = {date(2026, 1, 1): 100.0, date(2026, 2, 1): 110.0}

    enriched = ptr.compute_protective_outcomes(
        signals, current_prices, today=date(2026, 2, 1),
        spy_close_by_date=spy, min_days=5,
    )
    assert len(enriched) == 9
    collapsed = ptr.collapse_by_ticker(enriched)
    assert len(collapsed) == 9

    headline = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert headline["n_mature"] == 8   # the NaN row is excluded, not counted
    assert headline["protect_alpha"] is not None
    assert headline["protect_alpha"] == headline["protect_alpha"]   # not NaN
    assert not math.isnan(headline["protect_alpha"])
    assert headline["protect_alpha"] == pytest.approx(30.0)


def test_headline_defensive_guard_strips_nan_protect_alpha_pct():
    """Belt-and-suspenders: even if a NaN protect_alpha_pct somehow reaches
    protective_headline directly (bypassing compute_protective_outcomes'
    own guards), the population filter must still exclude it rather than
    let it poison the mean."""
    enriched = [
        _erow(ticker=f"P{i}", protect_alpha_pct=5.0, maturing=False) for i in range(8)
    ] + [
        _erow(ticker="NANDIRECT", protect_alpha_pct=float("nan"), maturing=False),
    ]
    collapsed = ptr.collapse_by_ticker(enriched)
    headline = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert headline["n_mature"] == 8
    assert headline["protect_alpha"] == pytest.approx(5.0)
    assert not math.isnan(headline["protect_alpha"])


def test_empty_input_returns_building_never_computed_negative():
    headline = ptr.protective_headline([], min_calls=8, firm_calls=15)
    assert headline["band"] == "building"
    assert headline["protect_alpha"] is None
    assert headline["n_mature"] == 0
    assert headline["since_date"] is None


def test_unpriced_ticker_and_below_sample_stays_building_not_negative():
    """A handful of unpriced/immature rows must never render as a negative verdict."""
    enriched = [
        _erow(ticker="A", protect_alpha_pct=None, maturing=False),
        _erow(ticker="B", protect_alpha_pct=None, maturing=True),
    ]
    collapsed = ptr.collapse_by_ticker(enriched)
    headline = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert headline["band"] == "building"
    assert headline["protect_alpha"] is None
    assert headline["n_mature"] == 0


# ─── collapse_by_ticker — dedup / anti-inflation ───────────────────────────

def test_collapse_dedups_15_day_episode_to_one_row():
    """A ticker with 15 daily EXIT rows in one episode collapses to exactly ONE
    row, anchored at the EARLIEST signal_date of the 15."""
    dates = [date(2026, 1, d) for d in range(1, 16)]   # Jan 1 .. Jan 15
    signals = [
        _sig_row(ticker="LONGRUN", signal_date=d, signal_type="EXIT", price_at_signal=100.0)
        for d in dates
    ]
    enriched = ptr.compute_protective_outcomes(
        signals, current_prices={"LONGRUN": 80.0}, today=date(2026, 2, 1),
        spy_close_by_date={date(2026, 1, 1): 100.0, date(2026, 2, 1): 105.0},
        min_days=5,
    )
    assert len(enriched) == 15
    collapsed = ptr.collapse_by_ticker(enriched)
    assert len(collapsed) == 1
    assert collapsed[0]["ticker"] == "LONGRUN"
    assert collapsed[0]["signal_date"] == date(2026, 1, 1)


def test_collapse_multiple_tickers_yields_one_row_each():
    enriched = [
        _erow(ticker="A", signal_date=date(2026, 1, 1)),
        _erow(ticker="A", signal_date=date(2026, 1, 2)),
        _erow(ticker="B", signal_date=date(2026, 1, 5)),
    ]
    collapsed = ptr.collapse_by_ticker(enriched)
    tickers = sorted(r["ticker"] for r in collapsed)
    assert tickers == ["A", "B"]


# ─── collapse_by_ticker — severity escalation ──────────────────────────────

def test_severity_escalation_trim_then_exit_keeps_earlier_trim_anchor():
    """Early TRIM row + later EXIT row → collapsed severity = EXIT, but the
    anchor signal_date/price_at_signal are still from the earlier TRIM row."""
    enriched = [
        _erow(ticker="ESC", signal_date=date(2026, 1, 1), signal_type="TRIM",
              price_at_signal=100.0),
        _erow(ticker="ESC", signal_date=date(2026, 1, 20), signal_type="EXIT",
              price_at_signal=85.0),
    ]
    collapsed = ptr.collapse_by_ticker(enriched)
    assert len(collapsed) == 1
    row = collapsed[0]
    assert row["severity"] == "EXIT"
    assert row["signal_date"] == date(2026, 1, 1)      # anchor stays the TRIM row
    assert row["price_at_signal"] == pytest.approx(100.0)
    assert row["signal_type"] == "TRIM"                # untouched original field


def test_severity_no_escalation_when_only_trim():
    enriched = [
        _erow(ticker="T", signal_date=date(2026, 1, 1), signal_type="TRIM"),
        _erow(ticker="T", signal_date=date(2026, 1, 2), signal_type="TRIM"),
    ]
    collapsed = ptr.collapse_by_ticker(enriched)
    assert collapsed[0]["severity"] == "TRIM"


# ─── protective_headline — population parity ──────────────────────────────

def test_population_parity_n_mature_matches_alpha_population_exactly():
    """n_mature must exactly equal the count of rows feeding the protect_alpha
    average — unpriced/immature rows mixed in must not inflate n_mature."""
    enriched = [
        # 10 mature + priced → feed the average
        _erow(ticker=f"P{i}", protect_alpha_pct=5.0, maturing=False) for i in range(10)
    ] + [
        # 4 immature (even though priced) — excluded
        _erow(ticker=f"IMM{i}", protect_alpha_pct=3.0, maturing=True) for i in range(4)
    ] + [
        # 3 mature but unpriced — excluded
        _erow(ticker=f"UNPX{i}", protect_alpha_pct=None, maturing=False) for i in range(3)
    ]
    collapsed = ptr.collapse_by_ticker(enriched)
    headline = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert headline["n_mature"] == 10
    assert headline["protect_alpha"] == pytest.approx(5.0)
    assert headline["band"] == "early"   # 10 is between min_calls=8 and firm_calls=15


# ─── scope invariant — WATCH / RISK_OFF excluded ───────────────────────────

def test_watch_and_risk_off_rows_never_reach_compute_protective_outcomes():
    signals = [
        _sig_row(ticker="W", signal_type="WATCH"),
        _sig_row(ticker="R", signal_type="RISK_OFF"),
        _sig_row(ticker="E", signal_type="EXIT"),
        _sig_row(ticker="T", signal_type="TRIM"),
    ]
    out = ptr.compute_protective_outcomes(
        signals, current_prices={"W": 100, "R": 100, "E": 90, "T": 95},
        today=date(2026, 2, 1),
        spy_close_by_date={date(2026, 1, 1): 100.0, date(2026, 2, 1): 105.0},
        min_days=5,
    )
    tickers = {r["ticker"] for r in out}
    assert tickers == {"E", "T"}


def test_watch_and_risk_off_never_inflate_headline_count_or_alpha():
    enriched_all_scoped_correctly = [
        _erow(ticker=f"P{i}", protect_alpha_pct=4.0, maturing=False) for i in range(10)
    ]
    # Simulate a caller bug that let WATCH-derived rows leak through — headline
    # itself has no signal_type awareness, so the SCOPE FILTER must happen in
    # compute_protective_outcomes (verified above); this test locks that the
    # helper chain, used correctly, produces the expected count.
    collapsed = ptr.collapse_by_ticker(enriched_all_scoped_correctly)
    headline = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert headline["n_mature"] == 10


# ─── maturity boundary ──────────────────────────────────────────────────────

def test_maturity_boundary_min_days_minus_one_excluded():
    signals = [_sig_row(ticker="EDGE", signal_date=date(2026, 1, 1), price_at_signal=100.0)]
    today = date(2026, 1, 1 + 4)   # days_since = 4, min_days = 5 → 4 < 5 → maturing
    out = ptr.compute_protective_outcomes(
        signals, current_prices={"EDGE": 90.0}, today=today,
        spy_close_by_date={date(2026, 1, 1): 100.0, today: 100.0},
        min_days=5,
    )
    assert out[0]["days_since"] == 4
    assert out[0]["maturing"] is True


def test_maturity_boundary_exactly_min_days_included():
    signals = [_sig_row(ticker="EDGE2", signal_date=date(2026, 1, 1), price_at_signal=100.0)]
    today = date(2026, 1, 1 + 5)   # days_since = 5, min_days = 5 → 5 >= 5 → mature
    out = ptr.compute_protective_outcomes(
        signals, current_prices={"EDGE2": 90.0}, today=today,
        spy_close_by_date={date(2026, 1, 1): 100.0, today: 100.0},
        min_days=5,
    )
    assert out[0]["days_since"] == 5
    assert out[0]["maturing"] is False


# ─── protective_headline — band boundaries (mirrors engine_trust_headline) ─

def test_protective_headline_building_band_below_min_calls():
    enriched = [_erow(ticker=f"T{i}", protect_alpha_pct=5.0, maturing=False) for i in range(7)]
    collapsed = ptr.collapse_by_ticker(enriched)
    out = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert out["band"] == "building"
    assert out["n_mature"] == 7


def test_protective_headline_early_band_at_min_calls_boundary():
    enriched = [_erow(ticker=f"T{i}", protect_alpha_pct=5.0, maturing=False) for i in range(8)]
    collapsed = ptr.collapse_by_ticker(enriched)
    out = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert out["band"] == "early"
    assert out["n_mature"] == 8


def test_protective_headline_firm_band_at_firm_calls_boundary():
    enriched = [_erow(ticker=f"T{i}", protect_alpha_pct=5.0, maturing=False) for i in range(15)]
    collapsed = ptr.collapse_by_ticker(enriched)
    out = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert out["band"] == "firm"
    assert out["n_mature"] == 15


def test_protective_headline_since_date_is_earliest_across_all_collapsed_rows():
    """since_date uses ALL collapsed rows, not just mature ones (mirrors
    engine_trust_headline's since_date over ALL new_picks)."""
    enriched = [
        _erow(ticker="A", signal_date=date(2026, 3, 1), maturing=False, protect_alpha_pct=1.0),
        _erow(ticker="B", signal_date=date(2026, 1, 5), maturing=True, protect_alpha_pct=None),
    ]
    collapsed = ptr.collapse_by_ticker(enriched)
    out = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert out["since_date"] == date(2026, 1, 5)


def test_protective_headline_negative_alpha_at_firm_band_is_honest_not_suppressed():
    """A real negative outcome at firm sample size must render as the actual
    negative number — never floored to zero or hidden."""
    enriched = [_erow(ticker=f"T{i}", protect_alpha_pct=-2.5, maturing=False) for i in range(15)]
    collapsed = ptr.collapse_by_ticker(enriched)
    out = ptr.protective_headline(collapsed, min_calls=8, firm_calls=15)
    assert out["band"] == "firm"
    assert out["protect_alpha"] == pytest.approx(-2.5)
