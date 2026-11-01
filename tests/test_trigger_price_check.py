"""stock_analyzer/trigger_price_check.py: the in-text price check for the
Pre-Mortem trigger (owner U1-U4, 2026-11-01), plus the wiring pins."""
import ast
from pathlib import Path

import pytest

from stock_analyzer import trigger_price_check as tpc
from tests._ast_cache import parse_file_cached
from stock_analyzer.constants import PREMORTEM_TRIGGER_PRICE_TOL

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent


# ── price_mentions ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("closes below $1,250.50", [1250.5]),
    ("exit at 150", [150.0]),
    ("exit at 150.00", [150.0]),
    ("below $200", [200.0]),
    ("falls 15% from here", []),
    ("falls 15 percent", []),
    ("falls 15 per cent", []),
    ("drops 15 pct", []),
    ("drops $15%", []),
    ("below the 200-day", []),
    ("below the 200 day average", []),
    ("under the 50dma", []),
    ("under the 200 sma", []),
    ("in 3 weeks", []),
    ("in 6 months", []),
    ("if Q3 guidance disappoints", []),
    ("if it falls 2x", []),
    ("S&P500 breaks down", []),
    ("closes below one fifty", []),
    ("", []),
    (None, []),
    ("140,150", [140150.0]),                    # no space: fails closed
    ("below $140, 150 is the next level", [140.0, 150.0]),
    ("by 2027 or below $150", [2027.0, 150.0]),  # a bare year stays a candidate
    ("$200 day-one price", [200.0]),            # `$` exempts the time-unit rule
])
def test_price_mentions(text, expected):
    assert tpc.price_mentions(text) == expected


def test_dollar_prefix_exempts_unit_and_letter_rules_but_not_percent():
    assert tpc.price_mentions("$200d") == [200.0]
    assert tpc.price_mentions("Q$3") == [3.0]
    assert tpc.price_mentions("$5 percent") == []


# ── price_in_text ───────────────────────────────────────────────────────────
def test_price_in_text_matches_numerals_either_way():
    assert tpc.price_in_text(150.0, "breaks 150")
    assert tpc.price_in_text(150.0, "breaks $150.00")
    assert tpc.price_in_text(150, "breaks $150")
    assert not tpc.price_in_text(150.0, "falls 150%")
    assert not tpc.price_in_text(150.0, None)
    assert not tpc.price_in_text(None, "150")
    assert not tpc.price_in_text(float("nan"), "150")


def test_tolerance_boundary_is_inclusive():
    # 0.25 is exact in binary, so the boundary is exact too
    assert tpc.price_in_text(0.75, "0.5", tol=0.25) is True
    assert tpc.price_in_text(0.75 + 1e-6, "0.5", tol=0.25) is False


def test_default_tolerance_is_the_constant():
    assert PREMORTEM_TRIGGER_PRICE_TOL == 0.01
    assert tpc.price_in_text(150.005, "150")
    assert not tpc.price_in_text(150.011, "150")


# ── guard_trigger: tightening-only property table ───────────────────────────
_BELOW = {"checkable": True, "direction": "below", "price_level": 150.0}
_NOT = {"checkable": False, "direction": None, "price_level": None}


@pytest.mark.parametrize("parsed,text,expect_trigger,expect_refused", [
    (None, "closes below $150", None, None),
    (_NOT, "closes below $150", _NOT, None),
    (_NOT, "", _NOT, None),
    (_BELOW, "closes below $150", _BELOW, None),
    (_BELOW, "closes below 150.00", _BELOW, None),
    (_BELOW, "If guidance disappoints, I'm wrong", None, 150.0),
    (_BELOW, "falls 150% from here", None, 150.0),
    (_BELOW, "", None, 150.0),
    ({"checkable": True, "direction": "above", "price_level": 15.0},
     "falls 15% from here", None, 15.0),
])
def test_guard_trigger_table(parsed, text, expect_trigger, expect_refused):
    trig, refused = tpc.guard_trigger(parsed, text)
    assert trig == expect_trigger
    assert refused == expect_refused
    # tightening-only: the result is the input or nothing, never a new value
    assert trig is None or trig is parsed


def test_guard_never_changes_price_or_direction():
    p = {"checkable": True, "direction": "above", "price_level": 200.0}
    trig, _ = tpc.guard_trigger(p, "rallies past $200")
    assert trig == {"checkable": True, "direction": "above", "price_level": 200.0}


# ── wording ─────────────────────────────────────────────────────────────────
def test_refused_note_exact_wording():
    assert tpc.refused_note(150, "below") == (
        "The AI read a below trigger of $150.00 from your commitment, but $150.00 "
        "isn't in what you wrote, so no automatic trigger is saved for this buy: "
        "it won't be monitored or used as your declared stop. To have it "
        "monitored, Cancel and write the price as a number "
        "(e.g. \"closes below $150\").")
    above = tpc.refused_note(42.5, "above")
    assert "an above trigger of $42.50" in above
    # only a "below" trigger would have been the declared stop
    assert "declared stop" not in above and "it won't be monitored. To have it" in above


def test_trigger_caption():
    assert tpc.trigger_caption(_BELOW) == "Monitored trigger: closes below $150.00"
    assert tpc.trigger_caption(
        {"checkable": True, "direction": "above", "price_level": 1250.5}
    ) == "Monitored trigger: closes above $1,250.50"
    assert tpc.trigger_caption(_NOT) is None
    assert tpc.trigger_caption(None) is None
    assert tpc.trigger_caption({"checkable": True, "direction": "sideways",
                                "price_level": 1}) is None


# ── wiring pins (AST) ───────────────────────────────────────────────────────
def _raw_subscripts(path):
    tree = parse_file_cached(path)
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                and n.slice.value == "raw"):
            out.append(n)
    return out


def test_eval_script_reads_the_raw_answer_of_the_outcome():
    subs = _raw_subscripts(ROOT / "scripts" / "llm_role_eval.py")
    assert len(subs) == 1
    call = subs[0].value
    assert isinstance(call, ast.Call) and call.func.attr == "extract_trigger_outcome"


def test_app_never_reads_raw_from_the_outcome():
    assert _raw_subscripts(ROOT / "app.py") == []
