"""S-D C4: Econ Calendar OPPORTUNITY inside the macro window is HOLD, the card
carries no share count, and the window literals are named constants."""
import ast
import inspect
from datetime import timedelta

import pandas as pd
import pytest

from stock_analyzer import macro_playbook as _mp
from stock_analyzer.constants import (
    MACRO_IMMINENT_DAYS,
    MACRO_OPP_WINDOW_DAYS,
    MACRO_PROTECT_PNL_WINDOW_DAYS,
    MACRO_WATCH_MED_WEIGHT,
)
from stock_analyzer.macro_playbook import (
    _build_rationale,
    _pre_event_action,
    build_event_playbooks,
)
from stock_analyzer.market_time import today_et

pytestmark = pytest.mark.fast

_NFP = "Non-Farm Payrolls"   # Financials: bear -2.0, bull +2.5 (both real _SCENARIOS values)


def _row(**overrides):
    row = {
        "Ticker": "NVDA", "Sector": "Financials", "Weight (%)": 5.0,
        "Score": 75.0, "Signal": "Buy", "P&L (%)": 0.0,
        "Shares": 100, "Price": 50.0, "Market Value": 5000.0,
    }
    row.update(overrides)
    return row


def _event(days_ahead):
    return {
        "event": _NFP, "date": today_et() + timedelta(days=days_ahead),
        "impact": "HIGH", "days_label": f"{days_ahead}d", "category": "Labor",
        "description": "", "estimate": None, "previous": None, "context": "",
        "watch_for": [],
    }


def test_opportunity_inside_imminent_window_is_hold_flagged_downgraded():
    assert _pre_event_action(_row(), _NFP, MACRO_IMMINENT_DAYS) == ("HOLD", "OK", True)


def test_opportunity_just_outside_imminent_window_stays_opportunity():
    assert _pre_event_action(_row(), _NFP, MACRO_IMMINENT_DAYS + 1) == ("OPPORTUNITY", "OK", False)


def test_opportunity_window_boundary_uses_constant():
    inside = _pre_event_action(_row(), _NFP, MACRO_OPP_WINDOW_DAYS)[0]
    outside = _pre_event_action(_row(), _NFP, MACRO_OPP_WINDOW_DAYS + 1)[0]
    assert inside == "OPPORTUNITY"
    assert outside != "OPPORTUNITY"


def test_protect_medium_window_boundary_unchanged():
    row = _row(**{"P&L (%)": -20.0, "Signal": "Hold", "Score": 60.0})
    assert _pre_event_action(row, _NFP, MACRO_PROTECT_PNL_WINDOW_DAYS)[:2] == ("PROTECT", "MEDIUM")
    assert _pre_event_action(row, _NFP, MACRO_PROTECT_PNL_WINDOW_DAYS + 1)[0] != "PROTECT"


def test_downgraded_rationale_keeps_setup_text_and_says_dont_add():
    opp = _build_rationale(_row(), _NFP, "OPPORTUNITY")
    text = _build_rationale(_row(), _NFP, "HOLD", True)
    assert text.startswith(opp)
    assert text.endswith(
        f" With the release within {MACRO_IMMINENT_DAYS} days this isn't shown as an add "
        "here; any add is Grow Today's call (📈 Analysis).")


def test_plain_hold_rationale_has_no_dont_add_text():
    text = _build_rationale(_row(Ticker="ABC", Sector="Defense"), _NFP, "HOLD")
    assert "isn't shown as an add" not in text


def test_playbook_inside_imminent_window_not_counted_as_add():
    port_df = pd.DataFrame([_row()])
    pb = build_event_playbooks([_event(MACRO_IMMINENT_DAYS)], port_df, 100_000.0)[0]
    pos = pb["positions"][0]
    assert pb["opp_count"] == 0
    assert pos["action"] == "HOLD" and pos["priority"] == "OK"
    assert "isn't shown as an add" in pos["rationale"]
    assert pos["detail"].startswith("Hold current position")


def test_playbook_outside_imminent_window_is_add_with_no_size():
    port_df = pd.DataFrame([_row()])
    pb = build_event_playbooks([_event(MACRO_IMMINENT_DAYS + 1)], port_df, 100_000.0)[0]
    pos = pb["positions"][0]
    assert pb["opp_count"] == 1
    assert pos["action"] == "OPPORTUNITY"
    assert "shares" not in pos["detail"] and "$" not in pos["detail"]


def test_no_bare_window_literals_and_no_0_10_in_module():
    tree = ast.parse(inspect.getsource(_mp))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_pre_event_action")
    bad = [c.value
           for node in ast.walk(fn) if isinstance(node, ast.Compare)
           for c in node.comparators
           if isinstance(c, ast.Constant) and c.value in (7, 14)]
    assert bad == []
    # The old add-size multiplier was written `0.10` (the `< 0.1` exposure
    # filters are a different literal and stay).
    assert "0.10" not in inspect.getsource(_mp)


def test_in_window_opportunity_with_watch_inputs_falls_through_to_watch():
    row = _row(**{"Weight (%)": MACRO_WATCH_MED_WEIGHT})
    assert _pre_event_action(row, _NFP, MACRO_IMMINENT_DAYS) == ("WATCH", "MEDIUM", False)
    pb = build_event_playbooks([_event(MACRO_IMMINENT_DAYS)], pd.DataFrame([row]), 100_000.0)[0]
    assert pb["watch_count"] == 1 and pb["opp_count"] == 0
    assert "isn't shown as an add" not in pb["positions"][0]["rationale"]


def test_in_window_opportunity_without_watch_inputs_is_hold_with_new_sentence():
    action, priority, down = _pre_event_action(_row(), _NFP, MACRO_IMMINENT_DAYS)
    assert (action, priority, down) == ("HOLD", "OK", True)
    assert "any add is Grow Today's call (📈 Analysis)" in _build_rationale(_row(), _NFP, action, down)


def test_same_watch_inputs_outside_window_still_opportunity():
    row = _row(**{"Weight (%)": MACRO_WATCH_MED_WEIGHT})
    assert _pre_event_action(row, _NFP, MACRO_IMMINENT_DAYS + 1) == ("OPPORTUNITY", "OK", False)
