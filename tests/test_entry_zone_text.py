from stock_analyzer.constants import WATCHLIST_NEAR_ZONE_PCT as N
from stock_analyzer.entry_zone_text import zone_phrase, zone_side, zone_word


def test_inside():
    assert zone_side(105, 100, True) == "inside"
    assert zone_word(105, 100, True) == "in"
    assert zone_phrase(105, 100, True, N, " ($100.00–$110.00)") == "is inside the entry zone ($100.00–$110.00)"


def test_above_near():
    assert zone_side(112, 100, False) == "above"
    assert zone_word(112, 100, False) == "near"
    assert zone_phrase(112, 100, False, N) == f"is within {N:g}% of the entry zone"


def test_below_says_below_not_within():
    p = zone_phrase(98, 100, False, N, " ($100.00–$110.00)")
    assert p == "is below the entry zone ($100.00–$110.00) (2.0% under $100.00)"
    assert "within" not in p
    assert zone_word(98, 100, False) == "below"


def test_price_equal_entry_lo_is_not_below():
    assert zone_side(100, 100, False) == "above"


def test_unreadable_inputs_do_not_raise():
    assert zone_side(None, 100, False) == "above"
    assert zone_side(98, "x", False) == "above"
