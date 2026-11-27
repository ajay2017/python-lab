"""Tests for stock_analyzer/regime_read.py (per-leg risk-off regime read).

Plan: docs/plans/sell-check-unread-3a-3b.md §2 / §3 C1.
"""
from __future__ import annotations

import itertools
import math
import pathlib

import pandas as pd
import pytest

from stock_analyzer import exit_advisor, regime_read
from stock_analyzer.constants import RISK_OFF_TREND_MA, RISK_OFF_VIX_LEVEL
from stock_analyzer.regime_read import (
    CLEAR, TRIPPED, UNREAD, display_line, log_text, posture_regime_state,
    read_regime, signature, unread_phrase,
)

N = RISK_OFF_TREND_MA
T = RISK_OFF_VIX_LEVEL
_REPO = pathlib.Path(__file__).resolve().parent.parent


def _frame(closes):
    return pd.DataFrame({"Close": list(closes)})


def _rising(n):
    return _frame([100.0 + i for i in range(n)])


def _falling(n):
    return _frame([200.0 - i * 0.1 for i in range(n)])


def _flat(n):
    return _frame([100.0] * n)


SPY_CASES = {
    "none": None,
    "no_close": pd.DataFrame({"Other": [1.0, 2.0]}),
    "short": _falling(N - 1),
    "above": _rising(N),
    "below": _falling(N),
    "equal": _flat(N),
}
VIX_CASES = {
    "none": None,
    "text": "x",
    "nan": float("nan"),
    "below": T - 0.01,
    "at": T,
    "inf": float("inf"),
    "high": T + 5,
}


def _read(spy=None, vix=None, **kw):
    return read_regime(spy, vix, **kw)


# ── invariant grid ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("spy_key,vix_key", list(itertools.product(SPY_CASES, VIX_CASES)))
def test_grid_arming_and_reasons_match_exit_advisor(spy_key, vix_key):
    spy, vix = SPY_CASES[spy_key], VIX_CASES[vix_key]
    armed, reasons = exit_advisor.risk_off_regime(
        spy, vix, trend_ma=N, vix_threshold=T)
    read = read_regime(spy, vix)
    assert (read["state"] == "risk_off") == armed
    assert read["reasons"] == reasons


@pytest.mark.parametrize("spy_key,vix_key", list(itertools.product(SPY_CASES, VIX_CASES)))
def test_grid_pins_risk_off_state_unchanged(spy_key, vix_key):
    spy, vix = SPY_CASES[spy_key], VIX_CASES[vix_key]
    spy_close = exit_advisor._series_close(spy)
    spy_usable = spy_close is not None and len(spy_close) >= N
    try:
        float(vix) if vix is not None else None
        vix_usable = vix is not None
    except (TypeError, ValueError):
        vix_usable = False
    if spy_usable or vix_usable:
        expected = "measured"
    elif spy_close is None and vix is None:
        expected = "not_measured"
    else:
        expected = "unusable"
    assert exit_advisor.risk_off_state(spy, vix, trend_ma=N) == expected


def test_risk_off_state_still_has_old_outputs():
    assert exit_advisor.risk_off_state(_rising(N), None, trend_ma=N) == "measured"
    assert exit_advisor.risk_off_state(None, 18.0, trend_ma=N) == "measured"
    assert exit_advisor.risk_off_state(_rising(N - 1), "x", trend_ma=N) == "unusable"
    assert exit_advisor.risk_off_state(None, None, trend_ma=N) == "not_measured"


def test_enter_now_readout_still_reads_risk_off_state():
    text = (_REPO / "stock_analyzer" / "enter_now_readout.py").read_text(encoding="utf-8")
    assert "risk_off_state(" in text


# ── legs ─────────────────────────────────────────────────────────────────────

def test_exactly_trend_ma_closes_is_readable():
    read = _read(_rising(N), None)
    assert read["trend"] == CLEAR
    assert read["trend_unread_why"] is None


def test_one_short_is_unread_short_history():
    read = _read(_rising(N - 1), None)
    assert read["trend"] == UNREAD
    assert read["trend_unread_why"] == "short_history"


@pytest.mark.parametrize("spy", [None, pd.DataFrame(), pd.DataFrame({"Open": [1.0]}),
                                 _frame([float("nan")] * 5)])
def test_no_data_trend_is_unread_no_data(spy):
    read = _read(spy, None)
    assert read["trend"] == UNREAD
    assert read["trend_unread_why"] == "no_data"


def test_trend_below_average_is_tripped():
    read = _read(_falling(N), None)
    assert read["trend"] == TRIPPED
    assert read["state"] == "risk_off"


def test_last_close_equal_to_average_is_clear():
    assert _read(_flat(N), None)["trend"] == CLEAR


def test_nan_vix_is_unread():
    read = _read(None, float("nan"))
    assert read["vix"] == UNREAD
    assert read["vix_value"] is None


@pytest.mark.parametrize("vix", [None, "x", [], object()])
def test_non_numeric_vix_is_unread(vix):
    assert _read(None, vix)["vix"] == UNREAD


def test_vix_boundaries():
    assert _read(None, T - 0.01)["vix"] == CLEAR
    assert _read(None, T)["vix"] == TRIPPED
    assert _read(None, float("inf"))["vix"] == TRIPPED


def test_vix_value_only_when_read():
    assert _read(None, 18.4)["vix_value"] == 18.4
    assert _read(None, None)["vix_value"] is None
    assert _read(None, "x")["vix_value"] is None


def test_unread_list_order_and_state():
    both = _read(None, None)
    assert both["unread"] == ["trend", "vix"] and both["state"] == "unread"
    one = _read(_rising(N), None)
    assert one["unread"] == ["vix"] and one["state"] == "partial"
    one = _read(None, 18.0)
    assert one["unread"] == ["trend"] and one["state"] == "partial"
    none = _read(_rising(N), 18.0)
    assert none["unread"] == [] and none["state"] == "calm"


def test_tripped_leg_with_other_unread_stays_risk_off():
    read = _read(_falling(N), None)
    assert read["state"] == "risk_off"
    assert read["unread"] == ["vix"]
    assert read["reasons"]


# ── posture_regime_state / signature / log_text / unread_phrase ──────────────

def test_posture_regime_state_mapping():
    assert posture_regime_state(_read(_rising(N), 18.0)) == "measured"
    assert posture_regime_state(_read(_rising(N), None)) == "partial"
    assert posture_regime_state(_read(None, None)) == "not_measured"
    assert posture_regime_state(_read(_falling(N), None)) == "partial"
    assert posture_regime_state(None) == "not_measured"
    assert posture_regime_state("garbage") == "not_measured"


def test_signature():
    assert signature(_read(_rising(N), None)) == (CLEAR, UNREAD)
    assert signature(_read(_falling(N), T)) == (TRIPPED, TRIPPED)
    assert signature(None) == (UNREAD, UNREAD)
    assert signature([1, 2]) == (UNREAD, UNREAD)


def test_log_text_format():
    assert log_text(_read(_rising(N), None)) == "risk-off regime: state=partial trend=clear vix=unread"
    assert log_text(_read(None, None)) == "risk-off regime: state=unread trend=unread vix=unread"
    assert log_text(_read(_falling(N), T)) == "risk-off regime: state=risk_off trend=tripped vix=tripped"
    assert log_text(None) == "risk-off regime: state=unread trend=unread vix=unread"


def test_unread_phrase():
    assert unread_phrase(_read(_rising(N), 18.0)) is None
    assert unread_phrase(_read(_rising(N), None)) == "VIX"
    assert unread_phrase(_read(None, 18.0)) == "the S&P 500's 1-year history"
    assert unread_phrase(_read(None, None)) == "neither VIX nor the S&P 500's 1-year history"


# ── display_line ─────────────────────────────────────────────────────────────

def test_display_line_none_when_nothing_unread():
    assert display_line(_read(_rising(N), 18.0)) is None


def test_display_line_none_when_risk_off_even_with_unread_leg():
    read = _read(_falling(N), None)  # trend tripped, VIX unread
    assert read["state"] == "risk_off"
    assert display_line(read) is None
    read = _read(None, T + 5)  # VIX tripped, trend unread
    assert read["state"] == "risk_off"
    assert display_line(read) is None


def test_display_line_vix_unread_caption():
    line = display_line(_read(_rising(N), None))
    assert line.startswith("⚠ VIX couldn't be read this session")
    assert f"not below its {N}-day average" in line
    assert f"VIX reading of {T:g} or more would not show here" in line
    assert line.endswith("Treat the regime as unknown, not calm.")


def test_display_line_trend_unread_caption_no_data():
    line = display_line(_read(None, 18.4))
    assert line.startswith("⚠ The S&P 500's 1-year history couldn't be read this session")
    assert f"VIX is 18.4 (below {T:g})" in line
    assert f"a close below the {N}-day average would not show here" in line


def test_display_line_trend_unread_caption_short_history():
    line = display_line(_read(_rising(N - 1), 18.4))
    assert line.startswith(
        f"⚠ The S&P 500's history was too short for the {N}-day check this session")
    assert "couldn't be read" not in line.split(", so the market")[0]


def test_display_line_both_unread_caption():
    line = display_line(_read(None, None))
    assert line.startswith("⚠ Neither VIX nor the S&P 500's 1-year history could be read")
    assert "the market regime is unknown" in line


def test_display_line_uses_parameters_not_literals():
    spy = _rising(150)
    read = read_regime(spy, None, trend_ma=150, vix_threshold=30)
    line = display_line(read, trend_ma=150, vix_threshold=30)
    assert "150-day" in line
    assert "VIX reading of 30 or more" in line
    read = read_regime(spy, 18.4, trend_ma=150, vix_threshold=30)
    assert display_line(read, trend_ma=150, vix_threshold=30) is None
    read = read_regime(None, 18.4, trend_ma=150, vix_threshold=30)
    line = display_line(read, trend_ma=150, vix_threshold=30)
    assert "150-day" in line and "(below 30)" in line


@pytest.mark.parametrize("vix", [T - 0.04, T - 0.001])
def test_below_threshold_vix_never_prints_as_threshold(vix):
    read = _read(None, vix)
    assert read["vix"] == CLEAR
    line = display_line(read)
    expected = f"{math.floor(vix * 10) / 10:.1f}"
    assert f"VIX is {expected} (below" in line
    assert f"VIX is {T:.1f} (" not in line


def test_display_line_24_96_and_24_999_print_24_9():
    for vix in (24.96, 24.999):
        read = read_regime(None, vix, trend_ma=N, vix_threshold=25)
        assert "VIX is 24.9 (below 25)" in display_line(
            read, trend_ma=N, vix_threshold=25)


def test_display_line_plain_text_only():
    cases = [_read(_rising(N), None), _read(None, 18.4), _read(_rising(N - 1), 18.4),
             _read(None, None)]
    for read in cases:
        line = display_line(read)
        assert "$" not in line and "**" not in line and "<" not in line


# ── never raises ─────────────────────────────────────────────────────────────

GARBAGE = [
    None, "abc", ["x"], {"Close": [1, 2]}, 5, object(), float("nan"),
    pd.DataFrame({"Close": [float("nan")] * 250}),
    pd.DataFrame({"Close": ["a", "b"]}),
]


@pytest.mark.parametrize("spy", GARBAGE)
@pytest.mark.parametrize("vix", GARBAGE)
def test_read_regime_never_raises_and_garbage_legs_are_unread(spy, vix):
    read = read_regime(spy, vix)
    assert read["trend"] in (CLEAR, TRIPPED, UNREAD)
    assert read["vix"] in (CLEAR, TRIPPED, UNREAD)
    display_line(read)
    unread_phrase(read)
    log_text(read)
    signature(read)
    posture_regime_state(read)


@pytest.mark.parametrize("read", [None, "x", 5, [], {}, {"trend": object(), "vix": []},
                                  {"trend": "clear"}, {"state": 3}])
def test_helpers_never_raise_on_garbage_reads(read):
    display_line(read)
    unread_phrase(read)
    assert isinstance(log_text(read), str)
    assert len(signature(read)) == 2
    assert posture_regime_state(read) in ("measured", "partial", "not_measured")


def test_nan_closes_frame_is_no_data():
    read = read_regime(pd.DataFrame({"Close": [float("nan")] * (N + 5)}), None)
    assert read["trend"] == UNREAD
    assert read["trend_unread_why"] == "no_data"


def test_module_has_no_threshold_literals():
    src = (_REPO / "stock_analyzer" / "regime_read.py").read_text(encoding="utf-8")
    assert "200" not in src and "25" not in src
    assert regime_read.RISK_OFF_TREND_MA == RISK_OFF_TREND_MA
