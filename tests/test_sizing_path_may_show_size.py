"""S-D D6 / C4 (a): sizing_path.may_show_size -- only ENTER_NOW on the
Watchlist hands out a share count; everything else fails closed."""
import pytest

from stock_analyzer import sizing_path as sp

pytestmark = pytest.mark.fast


def test_enter_now_on_watchlist_may_show_size():
    assert sp.may_show_size("watchlist", "ENTER_NOW") is True


@pytest.mark.parametrize("action", [
    "NEAR_ENTRY", "ENTRY_HELD", "WAIT_ENTRY", "HOLD_OFF_EARNINGS", "REMOVE", "", None,
])
def test_other_watchlist_actions_get_no_size(action):
    assert sp.may_show_size("watchlist", action) is False


def test_unknown_surface_fails_closed():
    assert sp.may_show_size("nowhere", "ENTER_NOW") is False
    assert sp.may_show_size(None, "ENTER_NOW") is False


def test_unhashable_inputs_fail_closed():
    assert sp.may_show_size(["watchlist"], ["ENTER_NOW"]) is False
    assert sp.may_show_size("watchlist", ["ENTER_NOW"]) is False


# ── watchlist_no_size_caption ─────────────────────────────────────────────────

def test_no_caption_when_a_size_may_be_shown():
    assert sp.watchlist_no_size_caption("ENTER_NOW", False, "ABC") is None
    assert sp.watchlist_no_size_caption("ENTER_NOW", True, "ABC") is None


def test_no_caption_for_entry_held():
    assert sp.watchlist_no_size_caption("ENTRY_HELD", True, "ABC") is None
    assert sp.watchlist_no_size_caption("ENTRY_HELD", False, "ABC") is None


def test_held_near_entry_says_add_is_grow_todays_call():
    assert sp.watchlist_no_size_caption("NEAR_ENTRY", True, "ABC") == (
        "📐 No size — you already hold ABC; an add is Grow Today's call (📈 Analysis).")


def test_not_held_near_entry_says_ready_to_enter():
    assert sp.watchlist_no_size_caption("NEAR_ENTRY", False, "ABC") == (
        "📐 No size yet — a share count appears once this name is Ready to Enter.")
    assert sp.watchlist_no_size_caption("", False, "ABC") is not None
