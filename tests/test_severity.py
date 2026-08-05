"""Regression tests for stock_analyzer/severity.py -- the canonical
severity DISPLAY palette introduced to close CA1 of the 2026-08-04 UX audit.
This module is deliberately display-only (see its own docstring); these
tests pin its shape (4 fixed tiers, a strict rank order, a fail-loud lookup)
so a future edit can't silently drop a tier or swallow a typo into a
default value.
"""
import pytest

from stock_analyzer.severity import (
    ACT_NOW,
    ELEVATED,
    STEADY,
    WATCH,
    SEVERITY_RANK,
    SEVERITY_STYLE,
    style,
)


def test_severity_style_has_exactly_four_tiers():
    assert set(SEVERITY_STYLE.keys()) == {ACT_NOW, ELEVATED, WATCH, STEADY}


def test_severity_style_entries_are_fully_populated():
    for tier, entry in SEVERITY_STYLE.items():
        assert entry.get("icon"), f"{tier} missing icon"
        assert entry.get("color"), f"{tier} missing color"
        assert entry.get("label"), f"{tier} missing label"


def test_severity_rank_keys_match_severity_style_keys():
    assert set(SEVERITY_RANK.keys()) == set(SEVERITY_STYLE.keys())


def test_severity_rank_is_a_strict_total_order():
    assert SEVERITY_RANK[ACT_NOW] < SEVERITY_RANK[ELEVATED] < SEVERITY_RANK[WATCH] < SEVERITY_RANK[STEADY]


def test_style_returns_the_matching_entry():
    assert style(ACT_NOW) == SEVERITY_STYLE[ACT_NOW]


def test_style_raises_on_unknown_tier():
    with pytest.raises(KeyError):
        style("NOT_A_TIER")
