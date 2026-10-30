"""Finding #23 (2026-10-30): the Brief and the Playbook share one stop-vs-move decision."""
import pytest

from stock_analyzer.constants import EARNINGS_STOP_COVER_FRAC
from stock_analyzer import earnings_advisor
from stock_analyzer.earnings_stop_cover import (
    brief_earnings_trigger,
    estimate_earnings_move,
    stop_cover_state,
)
from stock_analyzer.daily_briefing import _review_list, _dynamic_overweight_floor
from tests.conftest import find_item, make_port_df
from tests.test_daily_briefing import _TODAY, _earn_rows


def test_stop_cover_state_boundaries():
    est = 8.0
    edge = est * EARNINGS_STOP_COVER_FRAC
    assert stop_cover_state(edge - 0.01, est) == "at_risk"
    assert stop_cover_state(edge, est) == "covered"
    assert stop_cover_state(edge + 5, est) == "covered"


@pytest.mark.parametrize("gap,est", [(None, 6.0), (3.0, None), (3.0, 0), (3.0, -1.0)])
def test_stop_cover_state_unknown(gap, est):
    assert stop_cover_state(gap, est) == "unknown"


@pytest.mark.parametrize("rm,sector,expected", [
    ({"var_95": -2.0}, "Tech", 6.0),
    ({"var_95": 2.0}, "Tech", 6.0),
    ({"var_95": -1.0}, "Semiconductors", 10.0),
    ({"var_95": -1.03}, "Energy", 6.0),
    ({"var_95": -1.04}, "Energy", 3.1),
    ({"var_95": -9}, "Tech", 25.0),
    ({"var_95": -3.3}, "Tech", 9.9),
    ({}, "Utilities", 3.0),
    ({"var_95": None}, "Other", 7.0),
    ({"var_95": "x"}, "Defense", 5.0),
])
def test_estimate_matches_old_function(rm, sector, expected):
    assert estimate_earnings_move(rm, sector) == expected
    assert earnings_advisor._estimate_move(rm, sector) == expected


def test_all_trigger_variants_plain_text_and_honest():
    for trimmed in (True, False):
        for state, gap, est in (("covered", 10.0, 6.0), ("at_risk", 3.5, 6.0), ("unknown", None, 6.0)):
            t = brief_earnings_trigger(state, gap, est, trimmed=trimmed)
            assert "$" not in t and "**" not in t
            assert "protects" not in t
            assert "covers" not in t.replace("can't say whether it covers", "")
    assert "only 3.5% below" in brief_earnings_trigger("at_risk", 3.5, 6.0, trimmed=False)


def _brief_trigger(gap, risk_metrics, weight=5.0, sector="Tech"):
    rows = _earn_rows("AAA", weight, 8)
    rows[0]["gap_to_stop"] = gap
    rows[0]["sector"] = sector
    bundle = {"earnings": "2026-07-29"}
    if risk_metrics is not None:
        bundle["risk_metrics"] = risk_metrics
    items = _review_list(make_port_df(rows), [], [], {"AAA": bundle}, _TODAY, portfolio_value=100_000.0)
    return find_item(items, "AAA")["trigger"]


def test_brief_trigger_at_risk_aapl_like():
    t = _brief_trigger(3.5, {"var_95": -2.0})  # est 6.0
    assert "only 3.5% below" in t and "protects" not in t


def test_brief_trigger_covered_beyond_move():
    t = _brief_trigger(10.0, {"var_95": -2.0})
    assert "beyond" in t and "10.0% below" in t and "±6.0%" in t
    assert "covers" not in t and "protects" not in t


def test_brief_trigger_covered_close_to_move():
    # est 6.0; 0.85*6 = 5.1 <= 5.5 < 6.0
    t = _brief_trigger(5.5, {"var_95": -2.0})
    assert "just inside" in t and "usual size could reach it" in t and "(85% of the move)" in t
    assert "covers" not in t and "protects" not in t and "beyond" not in t


def test_trigger_prints_est_one_decimal():
    for trimmed in (True, False):
        for st, gap in (("covered", 9.0), ("covered", 5.0), ("at_risk", 3.0)):
            t = brief_earnings_trigger(st, gap, 5.5, trimmed=trimmed)
            assert "±5.5%" in t and "±6%" not in t


def test_trimmed_unknown_lowercase_stop_after_semicolon():
    t = brief_earnings_trigger("unknown", None, 6.0, trimmed=True)
    assert "holds; stop data unavailable" in t


def test_brief_trigger_stop_missing():
    t = _brief_trigger(None, {"var_95": -2.0})
    assert "Stop data unavailable" in t


def test_brief_trigger_trimmed_branch_uses_stop():
    floor = _dynamic_overweight_floor(8)
    t = _brief_trigger(3.5, {"var_95": -2.0}, weight=floor + 3.0)
    assert t.startswith("Beat") and "only 3.5% below" in t


def test_playbook_and_brief_agree_on_stop_at_risk():
    from stock_analyzer.earnings_advisor import build_earnings_playbook
    est = 6.0  # var_95 -2.0
    edge = est * EARNINGS_STOP_COVER_FRAC
    for gap, at_risk in ((None, False), (edge - 0.01, True), (edge, False), (20.0, False)):
        rows = _earn_rows("AAA", 5.0, 8)
        rows[0]["gap_to_stop"] = gap
        port_df = make_port_df(rows)
        held = {"AAA": {"earnings": "2026-07-29", "risk_metrics": {"var_95": -2.0}}}
        pb = build_earnings_playbook(port_df, held, today=_TODAY)
        pb_item = next(p for p in pb if p["ticker"] == "AAA")
        assert pb_item["stop_at_risk"] is at_risk
        trig = find_item(_review_list(port_df, [], [], held, _TODAY, portfolio_value=100_000.0), "AAA")["trigger"]
        if gap is None:
            assert "Stop data unavailable" in trig
        elif at_risk:
            assert "only" in trig and "go through it" in trig and "just inside" not in trig
        else:
            assert "go through it" not in trig and ("just inside" in trig or "beyond" in trig)
            assert "only" not in trig
