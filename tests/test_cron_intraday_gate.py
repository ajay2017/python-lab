"""The intraday cron lane's start-hour gate — decided with the user 2026-08-25.

The lane fires via a fixed-UTC dual-slot schedule (15:30/16:30 UTC) with no
native ET/DST awareness, so this gate ALSO decides which of the two daily
firings is the real one. Before this change the floor was a bare `10`, which
both the winter slot (10:30 ET) and the summer slot (11:30 ET) cleared, so
winter's earlier firing silently won and the lane ran an hour before the
documented ~11:30 ET target every winter. Raised to
`CRON_INTRADAY_START_HOUR_ET = 11` so only the 11:30 ET firing ever clears it,
in both EST and EDT — these tests lock the boundary itself, not just the
constant's value.
"""
import pandas as pd
import pytest

from stock_analyzer.constants import CRON_INTRADAY_START_HOUR_ET


def test_cron_intraday_start_hour_is_eleven():
    """The decided value — a policy constant, not to be changed casually."""
    assert CRON_INTRADAY_START_HOUR_ET == 11


@pytest.mark.parametrize("hour", [0, 8, 9, 10])
def test_hours_below_the_floor_skip_without_touching_the_db(hour, monkeypatch):
    """Winter's earlier dual-slot firing (10:30 ET => hour=10) must now skip —
    this is the exact behavior change: hour=10 used to clear the old floor."""
    import cron_runner as cr

    now_et = pd.Timestamp(2026, 1, 15, hour, 30, tz=cr._ET)  # a real Thursday
    calls = []
    monkeypatch.setattr(cr.db, "load_scanner_cache", lambda: calls.append("hit") or None)
    rc = cr._run_intraday(now_et, force=False)
    assert rc == 0
    assert calls == [], f"hour={hour} should skip before ever reading the DB"


def test_the_floor_hour_itself_clears_the_gate(monkeypatch):
    """hour == CRON_INTRADAY_START_HOUR_ET (11, i.e. 11:30 ET) must proceed past
    the gate — proven by reaching the next DB call, not by full execution."""
    import cron_runner as cr

    now_et = pd.Timestamp(2026, 1, 15, CRON_INTRADAY_START_HOUR_ET, 30, tz=cr._ET)
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                         lambda: pd.DataFrame({"Ticker": ["AAPL"]}))
    calls = []
    monkeypatch.setattr(cr.db, "load_scanner_cache", lambda: calls.append("hit") or None)
    rc = cr._run_intraday(now_et, force=False)
    assert calls == ["hit"], "hour=11 must clear the gate and reach load_scanner_cache"
    assert rc == 0  # no scanner_cache in this test => lane logs and exits cleanly


def test_an_hour_past_the_floor_also_clears_the_gate(monkeypatch):
    """Summer's slot (11:30 ET) was already correct before this change and must
    stay correct — the fix must not have narrowed the window from the other side."""
    import cron_runner as cr

    now_et = pd.Timestamp(2026, 7, 15, CRON_INTRADAY_START_HOUR_ET + 1, 30, tz=cr._ET)
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                         lambda: pd.DataFrame({"Ticker": ["AAPL"]}))
    calls = []
    monkeypatch.setattr(cr.db, "load_scanner_cache", lambda: calls.append("hit") or None)
    cr._run_intraday(now_et, force=False)
    assert calls == ["hit"]


def test_force_bypasses_the_hour_gate_entirely(monkeypatch):
    """force=True (manual/ad hoc re-run) must skip the trading-day AND hour
    checks — unchanged pre-existing behavior, guarded here against regression."""
    import cron_runner as cr

    now_et = pd.Timestamp(2026, 1, 15, 3, 0, tz=cr._ET)  # 3 AM — well below any floor
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                         lambda: pd.DataFrame({"Ticker": ["AAPL"]}))
    calls = []
    monkeypatch.setattr(cr.db, "load_scanner_cache", lambda: calls.append("hit") or None)
    cr._run_intraday(now_et, force=True)
    assert calls == ["hit"], "force=True must reach the DB call even at 3 AM"
