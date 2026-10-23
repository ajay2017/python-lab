"""Pure G-32 entry R:R measure (stock_analyzer/entry_rr.py)."""
import numpy as np
import pytest

from stock_analyzer import entry_rr
from stock_analyzer.constants import RR_ENTRY_MIN
from stock_analyzer.targets import risk_reward

pytestmark = pytest.mark.fast


def test_float_noise_true_2_to_1_clears():
    assert (390.33 - 384.25) / (384.25 - 381.21) < 2.0   # raw ratio is 1.9999999999999813
    m = entry_rr.measure(384.25, 381.21, 390.33)
    assert m["state"] == "ok" and m["rr"] == 2.0
    assert entry_rr.clears(m)


def test_exactly_two_clears_at_rr_entry_min():
    m = entry_rr.measure(100, 95, 100 + 5 * RR_ENTRY_MIN)
    assert m["rr"] == RR_ENTRY_MIN and entry_rr.clears(m)


def test_just_below_does_not_clear():
    m = entry_rr.measure(100, 95, 109.999995)   # 1.999999
    assert m["rr"] == 1.999999
    assert not entry_rr.clears(m)
    assert entry_rr.classify(m) == "below"


def test_unrounded_196_fails_while_display_rr_says_two():
    m = entry_rr.measure(100, 95, 109.8)        # 1.96
    assert m["rr"] == 1.96
    assert risk_reward(100, 95, 109.8) >= RR_ENTRY_MIN
    assert not entry_rr.clears(m)


@pytest.mark.parametrize("target", [100, 90])
def test_no_upside(target):
    m = entry_rr.measure(100, 95, target)
    assert m["state"] == "no_upside" and m["why"] == "no_upside"
    assert m["rr"] <= 0
    assert not entry_rr.clears(m)
    assert entry_rr.classify(m) == "no_upside"


@pytest.mark.parametrize("stop", [100, 101])
def test_stop_not_below_entry_unknown(stop):
    m = entry_rr.measure(100, stop, 110)
    assert m == {"rr": None, "state": "unknown", "why": "stop_not_below_entry"}


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0, -5, True, "100"])
@pytest.mark.parametrize("slot", [0, 1, 2])
def test_bad_inputs_unknown(bad, slot):
    args = [100, 95, 110]
    args[slot] = bad
    m = entry_rr.measure(*args)
    assert m["state"] == "unknown" and m["rr"] is None and m["why"] == "missing_input"
    assert not entry_rr.clears(m)
    assert entry_rr.classify(m) == "unknown"


def test_numpy_inputs():
    m = entry_rr.measure(np.float64(100), np.float32(95), np.int64(110))
    assert m["state"] == "ok" and m["rr"] == 2.0
    assert entry_rr.measure(np.bool_(True), 95, 110)["state"] == "unknown"


def test_from_bundle():
    b = {"current_price": 100, "stop": 95,
         "targets": {"base": 110, "base_source": "analyst"}}
    m = entry_rr.from_bundle(b)
    assert m["rr"] == 2.0 and m["base_source"] == "analyst" and entry_rr.clears(m)


@pytest.mark.parametrize("b", [
    {"current_price": 100, "stop": 95},
    {"current_price": 100, "stop": 95, "targets": None},
    {"current_price": 100, "stop": 95, "targets": {"base": None}},
])
def test_from_bundle_missing_targets(b):
    m = entry_rr.from_bundle(b)
    assert m["state"] == "unknown" and m["why"] == "missing_input"
    assert "base_source" in m


@pytest.mark.parametrize("b", [None, [], "x"])
def test_from_bundle_no_bundle(b):
    m = entry_rr.from_bundle(b)
    assert m["state"] == "unknown" and m["why"] == "no_bundle" and m["base_source"] is None


def test_classify_all_four():
    assert entry_rr.classify(entry_rr.measure(100, 95, 110)) == "pass"
    assert entry_rr.classify(entry_rr.measure(100, 95, 105)) == "below"
    assert entry_rr.classify(entry_rr.measure(100, 95, 99)) == "no_upside"
    assert entry_rr.classify(entry_rr.measure(None, 95, 99)) == "unknown"


def test_custom_rr_min():
    m = entry_rr.measure(100, 95, 105)   # 1.0
    assert not entry_rr.clears(m)
    assert entry_rr.clears(m, rr_min=1.0)
    assert entry_rr.classify(m, rr_min=1.0) == "pass"
