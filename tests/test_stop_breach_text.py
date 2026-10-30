"""Stop-breach "why" wording (stock_analyzer/stop_breach_text.py)."""
from stock_analyzer.stop_breach_text import breach_why, live_breach_caption


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


def test_live_caption_below_stop_keeps_legacy_text():
    assert live_breach_caption(230.0, 233.02) == (
        "⚠️ Breach confirmed at live price $230.00 — still below stop $233.02."
    )


def test_live_caption_at_or_above_stop_names_recovery_margin():
    from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT
    for px in (233.02, 233.5):
        s = live_breach_caption(px, 233.02)
        assert "at or just above" in s
        assert f"{STOP_RECOVERY_MARGIN_PCT:g}%" in s
        assert "still below" not in s


def test_live_caption_none_for_bad_inputs():
    nan = float("nan")
    assert live_breach_caption(None, 100.0) is None
    assert live_breach_caption(nan, 100.0) is None
    assert live_breach_caption(0, 100.0) is None
    assert live_breach_caption(100.0, 0) is None
    assert live_breach_caption(100.0, None) is None
