"""hold_reasons.reason_text — constants interpolated, pacing never says 'sized'."""
import pytest

from stock_analyzer import hold_reasons as hr
from stock_analyzer.constants import (
    GROW_MAX_PICKS_BULL, GROW_MAX_PICKS_DEFAULT, MOVER_MAX_PICKS,
)

pytestmark = pytest.mark.fast


def test_pick_cap_interpolates_both_caps():
    t = hr.reason_text("pick_cap")
    assert t == (f"Daily pick limit reached ({GROW_MAX_PICKS_BULL} on an up day / "
                 f"{GROW_MAX_PICKS_DEFAULT} on a flat day).")


def test_sector_slot_names_sector_and_falls_back():
    assert hr.reason_text("sector_slot", {"sector": "Energy"}) == (
        "Another Energy pick took today's one-per-sector slot.")
    assert "that sector" in hr.reason_text("sector_slot")
    assert "that sector" in hr.reason_text("sector_slot", {"sector": "  "})


def test_mover_cap_interpolates_constant():
    assert hr.reason_text("mover_cap") == f"Breakout limit reached ({MOVER_MAX_PICKS} per day)."


@pytest.mark.parametrize("code", ["pick_cap", "sector_slot", "mover_cap"])
def test_pacing_text_never_says_sized(code):
    t = hr.reason_text(code, {"sector": "Energy"})
    assert t  # firing control: the text exists
    assert "sized" not in t.lower()
    assert "sized" not in hr.FOOTER.lower()


@pytest.mark.parametrize("code", [None, "", "bogus", 3])
def test_unknown_code_is_empty_not_invented(code):
    assert hr.reason_text(code) == ""


def test_footer_text_exact():
    assert hr.FOOTER == ("Same checks as the picks above; held back only by pacing. "
                         "No share size — not a second buy list.")


def test_leads_note_always_says_no_verdict_and_interpolates_flat_bar():
    from stock_analyzer.constants import COMPOSITE_BUY_FLAT_DAY
    for tone in ("bull", "flat", "bear", None):
        assert "no verdict" in hr.leads_note(tone)
    assert f"≥ {COMPOSITE_BUY_FLAT_DAY}" in hr.leads_note("flat")
    assert "top few names" in hr.leads_note("flat")
    assert "only checks names with" not in hr.leads_note("flat")
    assert "risk-off" in hr.leads_note("bear")


def test_adds_paused_note_names_tickers_and_never_sizes():
    t = hr.adds_paused_note(["MSFT", "AAPL"])
    assert "MSFT, AAPL would qualify" in t and "no size today" in t
    assert "sized" not in t.lower()


def test_adds_paused_label_flat_vs_down():
    assert hr.adds_paused_label("bear", 2) == "Adds paused on a down day (2)"
    assert hr.adds_paused_label("flat", 1) == "Adds paused on a flat day (1)"
