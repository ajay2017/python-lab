"""Tests for stock_analyzer/intraday_entry.py — intraday pullback entry
window detection (_safe_float, compute_intraday_entries). Previously zero
test coverage. Pure module, no I/O (fetch_intraday_prices does a live
yfinance network call and is explicitly out of scope, per house convention
for pure-logic passes). compute_intraday_entries carries real decision
logic (a fail-safe SPY suppression gate) and gets the most rigorous coverage.
"""
from stock_analyzer import intraday_entry as ie


# ─── _safe_float ─────────────────────────────────────────────────────────────

def test_safe_float_none_returns_default():
    assert ie._safe_float(None) is None
    assert ie._safe_float(None, default=5.0) == 5.0


def test_safe_float_nan_returns_default():
    assert ie._safe_float(float("nan")) is None
    assert ie._safe_float(float("nan"), default=-1.0) == -1.0


def test_safe_float_valid_numeric_string():
    assert ie._safe_float("192.5") == 192.5


def test_safe_float_unconvertible_string_returns_default():
    assert ie._safe_float("not-a-number") is None
    assert ie._safe_float("not-a-number", default=0.0) == 0.0


def test_safe_float_valid_number_passthrough():
    assert ie._safe_float(100) == 100.0


# ─── compute_intraday_entries — builders ────────────────────────────────────

def _pick(ticker="AAA", composite_score=70.0):
    return {"ticker": ticker, "composite_score": composite_score}


# ─── compute_intraday_entries — SPY guard branches ──────────────────────────

def test_compute_intraday_entries_spy_data_none_returns_empty():
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 95.0, "open": 100.0}},
        spy_data=None, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == []


def test_compute_intraday_entries_spy_current_unreadable_returns_empty():
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 95.0, "open": 100.0}},
        spy_data={"current": None, "open": 500.0}, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == []


def test_compute_intraday_entries_spy_open_zero_or_negative_returns_empty():
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 95.0, "open": 100.0}},
        spy_data={"current": 500.0, "open": 0.0}, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == []


def test_compute_intraday_entries_spy_down_more_than_max_returns_empty():
    # SPY down 2% > spy_max_down=1.0 -> freefall guard suppresses all entries.
    spy = {"current": 98.0, "open": 100.0}
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 90.0, "open": 100.0}},
        spy_data=spy, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == []


def test_compute_intraday_entries_spy_down_exactly_at_max_boundary_suppresses():
    # spy_drop == -spy_max_down exactly -> suppressed, per `<=`.
    spy = {"current": 99.0, "open": 100.0}  # -1.0% exactly
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 90.0, "open": 100.0}},
        spy_data=spy, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == []


def test_compute_intraday_entries_spy_down_just_inside_max_boundary_proceeds():
    # spy_drop == -0.99% -- just inside the -1.0% ceiling, does not suppress.
    spy = {"current": 99.01, "open": 100.0}
    result = ie.compute_intraday_entries(
        [_pick()], {"AAA": {"current": 90.0, "open": 100.0}},  # -10% dip, well past dip_pct
        spy_data=spy, dip_pct=1.5, spy_max_down=1.0,
    )
    assert len(result) == 1


# ─── compute_intraday_entries — pick-level filtering ────────────────────────

_CALM_SPY = {"current": 500.0, "open": 500.0}


def test_compute_intraday_entries_pick_not_in_price_data_skipped():
    result = ie.compute_intraday_entries(
        [_pick(ticker="ZZZ")], {"AAA": {"current": 90.0, "open": 100.0}},
        spy_data=_CALM_SPY, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == []


def test_compute_intraday_entries_dip_below_threshold_excluded():
    # -1.0% drop, dip_pct=1.5 -> doesn't reach the threshold.
    result = ie.compute_intraday_entries(
        [_pick(ticker="AAA")], {"AAA": {"current": 99.0, "open": 100.0}},
        spy_data=_CALM_SPY, dip_pct=1.5, spy_max_down=1.0,
    )
    assert result == []


def test_compute_intraday_entries_dip_exactly_at_threshold_included():
    # -1.5% drop exactly -> included, per `<=`.
    result = ie.compute_intraday_entries(
        [_pick(ticker="AAA")], {"AAA": {"current": 98.5, "open": 100.0}},
        spy_data=_CALM_SPY, dip_pct=1.5, spy_max_down=1.0,
    )
    assert len(result) == 1
    assert result[0]["intraday_drop_pct"] == -1.5


def test_compute_intraday_entries_enriches_pick_preserving_original_keys():
    pick = _pick(ticker="AAA", composite_score=82.0)
    pick["thesis"] = "Strong setup"
    result = ie.compute_intraday_entries(
        [pick], {"AAA": {"current": 94.567, "open": 100.0}},
        spy_data=_CALM_SPY, dip_pct=1.5, spy_max_down=1.0,
    )
    assert len(result) == 1
    enriched = result[0]
    assert enriched["ticker"] == "AAA"
    assert enriched["composite_score"] == 82.0
    assert enriched["thesis"] == "Strong setup"
    assert enriched["current_price"] == 94.57  # rounded to 2dp
    assert enriched["open_price"] == 100.0
    assert enriched["intraday_drop_pct"] == round((94.567 - 100.0) / 100.0 * 100, 2)


def test_compute_intraday_entries_sort_order_biggest_dip_first():
    picks = [_pick(ticker="AAA"), _pick(ticker="BBB")]
    price_data = {
        "AAA": {"current": 97.0, "open": 100.0},   # -3.0%
        "BBB": {"current": 90.0, "open": 100.0},   # -10.0%
    }
    result = ie.compute_intraday_entries(
        picks, price_data, spy_data=_CALM_SPY, dip_pct=1.5, spy_max_down=1.0,
    )
    tickers = [r["ticker"] for r in result]
    assert tickers == ["BBB", "AAA"]  # biggest dip (most negative) first
