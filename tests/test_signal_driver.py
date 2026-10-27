import pytest

from stock_analyzer import constants, signal_driver
from stock_analyzer.signal_driver import DRIVER_TEXT, bq_weak, driver_diagnosis, show_driver_box

G = constants.SIGNAL_DRIVER_GAP_PTS


def test_tsla_case_bq_is_weak():
    assert driver_diagnosis(62, 32) == "bq"


def test_technical_weak():
    assert driver_diagnosis(32, 62) == "technical"


def test_exact_boundaries_are_both():
    assert driver_diagnosis(30 + G, 30) == "both"      # weak BQ, gap == G
    assert driver_diagnosis(50 - G, 50) == "both"      # solid BQ, gap == -G


def test_just_past_boundary_picks_side():
    assert driver_diagnosis(30 + G + 0.01, 30) == "bq"
    assert driver_diagnosis(50 - G - 0.01, 50) == "technical"


def test_equal_scores_both():
    assert driver_diagnosis(40, 40) == "both"


def test_technical_low_but_bq_weak_is_both():
    # BQ below the Hold floor: Recommended Action says BQ weakness, so the
    # driver box must not say "technical only".
    assert driver_diagnosis(20, 40) == "both"


def test_big_gap_but_bq_not_weak_is_both():
    assert driver_diagnosis(65, 48) == "both"


@pytest.mark.parametrize("t,b", [
    (None, 50), (50, None), (float("nan"), 50), (50, float("nan")),
    (float("inf"), 50), (True, 50), (50, False), ("60", 50),
])
def test_invalid_inputs_none(t, b):
    assert driver_diagnosis(t, b) is None


@pytest.mark.parametrize("kind", ["add", "trim", None, ""])
def test_non_review_cards_none(kind):
    assert driver_diagnosis(62, 32, action_type=kind) is None
    assert show_driver_box(kind) is False


def test_review_card_shows_box():
    assert show_driver_box("review") is True


def test_placeholder_technical_score_none():
    assert driver_diagnosis(62, 32, t_available=False) is None


def test_bq_weak_uses_hold_floor():
    assert bq_weak(constants.COMPOSITE_HOLD - 0.01) is True
    assert bq_weak(constants.COMPOSITE_HOLD) is False


def test_driver_never_contradicts_bq_weak():
    for t in range(0, 101, 5):
        for b in range(0, 101, 3):
            k = driver_diagnosis(t, b)
            if k == "bq":
                assert bq_weak(b)
            if k == "technical":
                assert not bq_weak(b)


def test_texts():
    assert "Business quality weakness" in DRIVER_TEXT["bq"]
    assert DRIVER_TEXT["bq"].startswith("🔴")
    assert "Technical weakness only" in DRIVER_TEXT["technical"]
    assert set(DRIVER_TEXT) == {"bq", "technical", "both"}


def test_threshold_read_from_constants(monkeypatch):
    monkeypatch.setattr(signal_driver.constants, "SIGNAL_DRIVER_GAP_PTS", 5)
    assert driver_diagnosis(40, 30) == "bq"
    assert driver_diagnosis(40, 50) == "technical"
    assert driver_diagnosis(35, 30) == "both"


def test_both_text_does_not_claim_both_pillars_are_weak():
    # A small gap can mean both strong (80 vs 78), so the "both" box must not
    # say both are weak; it states the gap band instead.
    text = DRIVER_TEXT["both"]
    assert "weak" not in text.lower()
    assert "No single driver" in text
