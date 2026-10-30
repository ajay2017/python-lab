"""composite_score_text never rounds a composite across a band edge."""
import pytest

from stock_analyzer.constants import (
    COMPOSITE_SELL, COMPOSITE_HOLD, COMPOSITE_BUY, COMPOSITE_STRONG_BUY,
)
from stock_analyzer.score_text import composite_score_text as t

pytestmark = pytest.mark.fast

_EDGES = [COMPOSITE_SELL, COMPOSITE_HOLD, COMPOSITE_BUY, COMPOSITE_STRONG_BUY]


@pytest.mark.parametrize("edge", _EDGES)
def test_just_below_edge_never_reads_as_edge(edge):
    s = edge - 0.4
    assert t(s) == f"{s:.1f}" and t(s) != f"{edge:.0f}"


@pytest.mark.parametrize("edge", _EDGES)
def test_edge_and_whole_numbers_stay_whole(edge):
    assert t(float(edge)) == f"{edge:.0f}"
    assert t(edge - 1.0) == f"{edge - 1:.0f}"


@pytest.mark.parametrize("edge", _EDGES)
def test_round_down_side_is_plain(edge):
    # 0.6 below the edge rounds to the integer below it, never across the edge
    assert t(edge - 0.6) == f"{edge - 1:.0f}"
    assert t(edge + 0.4) == f"{edge:.0f}"


def test_floors_not_rounds_to_one_decimal():
    assert t(COMPOSITE_BUY - 0.04) == f"{COMPOSITE_BUY - 0.1:.1f}"


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf"), "x"])
def test_unusable_input_is_na(bad):
    assert t(bad) == "n/a"


def test_literal_examples():
    assert t(64.6) == "64.6" and t(64.4) == "64" and t(64.96) == "64.9"
    assert t(70.0) == "70" and t(65.0) == "65"
