"""stock_analyzer/rr_pullback.py: the pullback price at which entry R:R hits the minimum."""
import pytest

from stock_analyzer import entry_rr
from stock_analyzer.rr_pullback import pullback_to_rr

pytestmark = pytest.mark.fast


def test_worked_example_and_consistency():
    r = pullback_to_rr(100, 92, 114, "analyst", rr_min=2.0)   # rr 1.75
    assert r["state"] == "ok"
    assert r["price"] == pytest.approx(98.0)
    assert r["pct"] == pytest.approx(-2.0)
    # stop trails by the same distance (8): stop 90 at price 98
    assert entry_rr.measure(98, 90, 114)["rr"] == pytest.approx(2.0)


def test_already_clears():
    r = pullback_to_rr(100, 92, 120, "analyst", rr_min=2.0)   # rr 2.5
    assert r == {"state": "already_clears", "price": None, "pct": None}
    assert pullback_to_rr(100, 92, 116, "analyst", rr_min=2.0)["state"] == "already_clears"  # exactly 2.0


def test_beyond_stop():
    r = pullback_to_rr(100, 92, 106, "analyst", rr_min=2.0)   # rr 0.75 -> p* = 90 <= 92
    assert r == {"state": "beyond_stop", "price": None, "pct": None}


def test_boundary_rr_exactly_min_minus_one_is_beyond_stop():
    # target 108 -> rr 1.0 -> p* = 108 - 16 = 92 = stop
    assert pullback_to_rr(100, 92, 108, "analyst", rr_min=2.0)["state"] == "beyond_stop"


def test_just_inside_boundary_is_ok():
    r = pullback_to_rr(100, 92, 108.5, "analyst", rr_min=2.0)  # p* = 92.5 > stop
    assert r["state"] == "ok"
    assert r["price"] == pytest.approx(92.5)


@pytest.mark.parametrize("args", [
    (None, 92, 114), (100, None, 114), (100, 92, None),
    (100, 92, 100), (100, 92, 90), (100, 100, 114), (100, 105, 114),
    (float("nan"), 92, 114), ("x", 92, 114),
])
def test_unknown(args):
    assert pullback_to_rr(*args, "analyst", rr_min=2.0) == {"state": "unknown", "price": None, "pct": None}


def test_default_rr_min_is_constant():
    from stock_analyzer.constants import RR_ENTRY_MIN
    assert pullback_to_rr(100, 92, 114, "analyst") == pullback_to_rr(100, 92, 114, "analyst", rr_min=RR_ENTRY_MIN)


@pytest.mark.parametrize("src", ["resistance", "momentum", "flat", None, ""])
def test_non_analyst_source_is_target_moves(src):
    assert pullback_to_rr(100, 92, 114, src, rr_min=2.0) == {"state": "target_moves", "price": None, "pct": None}


def test_non_analyst_source_unknown_and_clears_take_precedence():
    assert pullback_to_rr(100, 92, 120, "flat", rr_min=2.0)["state"] == "already_clears"
    assert pullback_to_rr(None, 92, 114, "flat", rr_min=2.0)["state"] == "unknown"
