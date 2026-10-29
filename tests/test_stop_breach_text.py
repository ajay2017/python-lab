"""Stop-breach "why" wording (stock_analyzer/stop_breach_text.py)."""
from stock_analyzer.stop_breach_text import breach_why


def test_price_below_stop_keeps_todays_exact_text():
    assert breach_why(230.0, 233.02, "auto", -1.3) == (
        "Price $230.00 closed below the auto stop $233.02 (gap -1.3%)."
    )


def test_price_above_stop_with_zero_gap_says_closed_at_not_below():
    s = breach_why(233.03, 233.02, "auto", 0.0)
    assert "closed at the" in s
    assert "counts as a breach" in s
    assert "below" not in s
    assert "(gap +0.0%)" in s


def test_price_equal_to_stop_says_closed_at():
    assert "closed at the" in breach_why(100.0, 100.0, "manual", 0.0)


def test_missing_or_nan_price_falls_back_without_raising():
    for bad in (None, float("nan")):
        s = breach_why(bad, 100.0, "auto", -0.5)
        assert "closed below the" in s
        s2 = breach_why(100.0, bad, "auto", -0.5)
        assert "closed below the" in s2
