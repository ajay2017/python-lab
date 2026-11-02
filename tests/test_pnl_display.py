"""pnl_display.signed_display: snap a value that rounds to zero."""
import pytest

from stock_analyzer.pnl_display import signed_display

pytestmark = pytest.mark.fast


def test_small_negative_snaps_to_zero_at_0dp():
    assert signed_display(-0.4, 0) == (0.0, True)


def test_small_negative_snaps_at_2dp():
    v, z = signed_display(-0.004, 2)
    assert (v, z) == (0.0, True)
    assert str(v) == "0.0"   # not "-0.0"


def test_half_rounds_half_even_to_zero_at_0dp():
    # round(-0.5) == -0.0 (half-to-even), the same "-0" f"{-0.5:.0f}" prints.
    assert signed_display(-0.5, 0) == (0.0, True)


def test_value_that_survives_rounding_is_unchanged():
    assert signed_display(0.6, 0) == (0.6, False)
    assert signed_display(-0.006, 2) == (-0.006, False)
