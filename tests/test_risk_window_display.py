"""RiskListwiseYoung C3: risk_window.display_note (the one decision for what a
screen shows) and the pins that keep the User Guide sentence tied to the
constants it quotes. Spec: docs/plans/risk-young-holding-c2-spec.md (C3, A / F).
"""
from pathlib import Path

import pytest

from stock_analyzer.constants import (
    BETA_MIN_RETURNS,
    PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
)
from stock_analyzer.risk_window import coverage_note, display_note

pytestmark = pytest.mark.fast

_LEFT = [{"ticker": "YNG", "reason": "short_history", "n_returns": 12, "weight_pct": 3.6}]
_APP = Path(__file__).resolve().parent.parent / "app.py"


def _cov(state, left_out=None, **extra):
    d = {"state": state, "left_out": _LEFT if left_out is None else left_out,
         "min_returns": BETA_MIN_RETURNS,
         "max_unmeasured_pct": PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
         "unmeasured_pct": 3.6, "late_start": []}
    d.update(extra)
    return d


def test_full_reads_none():
    assert display_note(_cov("full", left_out=[])) is None


def test_partial_reads_the_coverage_note():
    cov = _cov("partial")
    note = display_note(cov)
    assert note is not None and "YNG" in note
    assert note == coverage_note(cov)


def test_unmeasured_reads_the_coverage_note():
    cov = _cov("unmeasured", unmeasured_pct=15.0)
    note = display_note(cov)
    assert note is not None and "can't be measured" in note
    assert note == coverage_note(cov)


@pytest.mark.parametrize("bad", [None, {}, {"beta": 1}, "partial", [], 0])
def test_non_dict_or_stateless_reads_none(bad):
    assert display_note(bad) is None


def test_unknown_state_reads_none():
    assert display_note({"state": "something_else"}) is None


def test_user_guide_sentence_quotes_the_live_constants():
    text = _APP.read_text(encoding="utf-8")
    marker = "A holding with fewer than "
    assert text.count(marker) == 1
    sentence = text[text.index(marker): text.index("Awareness only", text.index(marker))]
    assert f"fewer than {BETA_MIN_RETURNS} trading days" in sentence
    assert f"more than {PORTFOLIO_RISK_UNMEASURED_MAX_PCT:g}% of your book" in sentence
