"""Stop-breach "why" wording (stock_analyzer/stop_breach_text.py)."""
from stock_analyzer.stop_breach_text import breach_why, live_breach_caption


def test_price_below_stop_exact_text():
    assert breach_why(230.0, 233.02, "auto", -1.3) == (
        "Price $230.00 is below the auto stop $233.02 (gap -1.3%)."
    )


def test_price_above_stop_with_zero_gap_says_at_or_just_above():
    s = breach_why(233.03, 233.02, "auto", 0.0)
    assert "is at or just above the" in s
    assert "counts as a breach" in s
    assert "is below" not in s
    assert "closed" not in s
    assert "(gap +0.0%)" in s


def test_price_equal_to_stop_says_at_or_just_above():
    assert "is at or just above the" in breach_why(100.0, 100.0, "manual", 0.0)


def test_missing_or_nan_price_falls_back_without_raising():
    for bad in (None, float("nan")):
        s = breach_why(bad, 100.0, "auto", -0.5)
        assert "is below the" in s
        s2 = breach_why(100.0, bad, "auto", -0.5)
        assert "is below the" in s2


def test_trailing_stop_word_in_label_is_trimmed():
    s = breach_why(230.0, 233.02, "ATR Stop", -1.3)
    assert "the ATR stop $" in s
    assert "Stop stop" not in s
    assert "Manual stop $" in breach_why(230.0, 233.02, "Manual", -1.3)


def test_empty_label_has_no_double_space():
    for lbl in ("", None, "Stop", "  "):
        s = breach_why(230.0, 233.02, lbl, -1.3)
        assert "is below the stop $" in s
        assert "  " not in s


def test_other_labels_kept_verbatim():
    for lbl in ("Protect 25% gain", "Breakeven guard"):
        assert f"the {lbl} stop $" in breach_why(230.0, 233.02, lbl, -1.3)


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
