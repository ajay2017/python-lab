"""PerfWindowCollapse: the vs-SPY backtest window is SPY's own sessions; a holding
with too little history is left out and named, never allowed to shrink the window."""
from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analyzer.data import is_trading_day
from stock_analyzer.perf_backtest import backtest_vs_spy
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast


def _sessions(n: int, end: date = date(2026, 11, 20)) -> list[pd.Timestamp]:
    out: list[pd.Timestamp] = []
    d = end
    while len(out) < n:
        if is_trading_day(d):
            out.append(pd.Timestamp(d))
        d -= timedelta(days=1)
    return sorted(out)


def _ser(idx, start_val=100.0, step=1.0) -> pd.Series:
    return pd.Series([start_val + step * i for i in range(len(idx))], index=pd.DatetimeIndex(idx))


N = 30
DAYS = _sessions(80)
SPY = _ser(DAYS, 400.0, 1.0)
WIN = DAYS[-N:]


def test_holding_starting_exactly_on_window_start_is_included():
    r = backtest_vs_spy({"A": _ser(WIN)}, {"A": 1.0}, SPY, N)
    assert r["ok"] and r["included"] == ["A"] and r["left_out"] == []
    assert r["dates"] == WIN


def test_holding_starting_three_sessions_after_window_start_is_included_and_trims():
    a = _ser(WIN[3:], 100.0, 1.0)
    r = backtest_vs_spy({"A": _ser(DAYS), "B": a}, {"A": 0.5, "B": 0.5}, SPY, N)
    assert r["ok"] and r["included"] == ["A", "B"] and r["left_out"] == []
    assert r["start_trimmed_sessions"] == 3
    assert len(r["dates"]) == N - 3 and r["dates"][0] == WIN[3]
    assert r["window_short"] is False and r["spy_window_sessions"] == N
    assert r["port_ret"].iloc[0] == pytest.approx(0.0)
    assert r["spy_ret"].iloc[0] == pytest.approx(0.0)


def test_holding_starting_six_sessions_after_window_start_is_left_out():
    r = backtest_vs_spy(
        {"A": _ser(DAYS), "B": _ser(WIN[6:])}, {"A": 0.5, "B": 0.5}, SPY, N
    )
    assert r["included"] == ["A"] and r["start_trimmed_sessions"] == 0
    lo = r["left_out"][0]
    assert lo["ticker"] == "B" and lo["sessions"] == N - 6
    assert lo["first_date"] == WIN[6].date()
    assert lo["weight_pct"] == pytest.approx(50.0)
    assert r["left_out_weight_pct"] == pytest.approx(50.0)
    assert len(r["dates"]) == N


def test_holding_starting_five_sessions_after_is_the_last_one_included():
    r = backtest_vs_spy({"A": _ser(WIN[5:])}, {"A": 1.0}, SPY, N)
    assert r["ok"] and r["start_trimmed_sessions"] == 5


def test_trim_leaving_under_two_dates_drops_the_latest_starting_holding():
    spy = SPY.iloc[-4:]  # window of 4: slack index is the last date
    a, b = _ser(DAYS), _ser(DAYS[-1:])  # B starts on the last window date only
    r = backtest_vs_spy({"A": a, "B": b}, {"A": 0.5, "B": 0.5}, spy, 4)
    assert r["included"] == ["A"] and [x["ticker"] for x in r["left_out"]] == ["B"]
    assert len(r["dates"]) == 4 and r["start_trimmed_sessions"] == 0
    only = backtest_vs_spy({"B": b}, {"B": 1.0}, spy, 4)
    assert only["ok"] is False and only["reason"] == "none_included"


def test_two_session_holding_does_not_collapse_the_window():
    ant = _ser(DAYS[-2:])
    r = backtest_vs_spy(
        {"A": _ser(DAYS), "ANT": ant}, {"A": 0.9, "ANT": 0.1}, SPY, N
    )
    assert r["ok"]
    assert len(r["dates"]) == N and r["dates"] == WIN
    assert [x["ticker"] for x in r["left_out"]] == ["ANT"]
    assert r["left_out"][0]["sessions"] == 2


def test_weights_rescale_over_the_included():
    a = _ser(DAYS, 50.0, 0.5)
    r = backtest_vs_spy(
        {"A": a, "B": _ser(DAYS[-3:])}, {"A": 0.6, "B": 0.4}, SPY, N
    )
    expected = (a.loc[WIN[-1]] / a.loc[WIN[0]] - 1) * 100
    assert r["port_final"] == pytest.approx(expected)
    assert r["port_ret"].iloc[0] == pytest.approx(0.0)


def test_zero_and_negative_weight_ignored_and_not_listed():
    r = backtest_vs_spy(
        {"A": _ser(DAYS), "Z": _ser(DAYS[-2:]), "N": _ser(DAYS[-2:])},
        {"A": 1.0, "Z": 0.0, "N": -0.2}, SPY, N,
    )
    assert r["included"] == ["A"] and r["left_out"] == []


def test_holding_with_no_data_is_listed_with_zero_sessions():
    r = backtest_vs_spy({"A": _ser(DAYS)}, {"A": 0.7, "X": 0.3}, SPY, N)
    assert r["included"] == ["A"]
    assert r["left_out"] == [
        {"ticker": "X", "sessions": 0, "first_date": None, "weight_pct": pytest.approx(30.0)}
    ]


def test_all_left_out_is_none_included():
    r = backtest_vs_spy({"ANT": _ser(DAYS[-2:])}, {"ANT": 1.0}, SPY, N)
    assert r["ok"] is False and r["reason"] == "none_included"
    assert [x["ticker"] for x in r["left_out"]] == ["ANT"]


def test_spy_shorter_than_n_days_sets_window_short():
    short = SPY.iloc[-10:]
    r = backtest_vs_spy({"A": _ser(DAYS)}, {"A": 1.0}, short, N)
    assert r["ok"] and r["window_short"] is True and len(r["dates"]) == 10
    full = backtest_vs_spy({"A": _ser(DAYS)}, {"A": 1.0}, SPY, N)
    assert full["window_short"] is False


def test_spy_with_fewer_than_two_dates_is_no_spy():
    for spy in (SPY.iloc[-1:], SPY.iloc[:0], pd.Series([np.nan, np.nan], index=DAYS[-2:])):
        r = backtest_vs_spy({"A": _ser(DAYS)}, {"A": 1.0}, spy, N)
        assert r == {"ok": False, "reason": "no_spy"}


def test_missing_interior_bar_is_forward_filled_not_back_filled():
    a = _ser(DAYS, 100.0, 1.0)
    gap = WIN[5]
    a = a.drop(gap)
    r = backtest_vs_spy({"A": a}, {"A": 1.0}, SPY, N)
    base = a.loc[WIN[0]]
    prev_close = a.loc[WIN[4]]
    got = r["port_ret"].loc[gap]
    assert got == pytest.approx((prev_close / base - 1) * 100)


def test_start_value_is_the_asof_close_when_the_first_bar_precedes_the_window():
    a = _ser(DAYS)  # starts well before the window
    r = backtest_vs_spy({"A": a}, {"A": 1.0}, SPY, N)
    assert r["port_ret"].iloc[0] == pytest.approx(0.0)
    assert r["port_final"] == pytest.approx((a.loc[WIN[-1]] / a.loc[WIN[0]] - 1) * 100)


def test_returns_are_percent_and_alpha_is_port_minus_spy():
    a = _ser(DAYS, 100.0, 2.0)
    r = backtest_vs_spy({"A": a}, {"A": 1.0}, SPY, N)
    spy_final = (SPY.loc[WIN[-1]] / SPY.loc[WIN[0]] - 1) * 100
    assert r["spy_final"] == pytest.approx(spy_final)
    assert r["spy_ret"].iloc[0] == pytest.approx(0.0)
    assert r["alpha"] == pytest.approx(r["port_final"] - r["spy_final"])
    assert 1.0 < abs(r["port_final"]) < 100.0  # percent, not a fraction


def test_tz_aware_inputs_are_normalised():
    a = _ser(DAYS)
    a.index = a.index.tz_localize("America/New_York")
    r = backtest_vs_spy({"A": a}, {"A": 1.0}, SPY, N)
    assert r["ok"] and r["included"] == ["A"]


# ── wiring pin (no test imports app.py) ──────────────────────────────────────

_APP = Path(__file__).parent.parent / "app.py"
_TREE = parse_file_cached(_APP)


def _page_branch(page: str) -> ast.If:
    for top in _TREE.body:
        node = top
        while isinstance(node, ast.If):
            t = node.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "page" and len(t.comparators) == 1
                    and isinstance(t.comparators[0], ast.Constant)
                    and t.comparators[0].value == page):
                return node
            node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
    raise AssertionError(f"no page branch for {page!r}")


def test_portfolio_overview_uses_backtest_vs_spy_and_no_intersection_call():
    branch = _page_branch("🥧 Portfolio Overview")
    called, intersections = set(), 0
    for stmt in branch.body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
                    if f.attr == "intersection":
                        intersections += 1
    assert "backtest_vs_spy" in called
    assert intersections == 0
