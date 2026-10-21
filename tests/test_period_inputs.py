"""Tests for stock_analyzer/period_inputs.py (C7: the one input-loading + core
helper shared by Reports, the app's Weekly Debrief button and the Sunday cron)."""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import period_attribution as pa
from stock_analyzer import period_inputs as pi
from tests.test_period_attribution import FRI, INT_EV, MON, D, week_world

PS, PE = D("2026-10-12"), D("2026-10-18")


class FakeDb:
    """Records every call; any loader named in `raising` raises."""

    def __init__(self, raising=(), values=None):
        self.calls = []
        self.raising = set(raising)
        self.values = values or {}

    def _do(self, name, *args):
        self.calls.append((name, args))
        if name in self.raising:
            raise RuntimeError(name)
        return self.values.get(name, f"<{name}>")

    def load_trades_or_none(self):
        return self._do("load_trades_or_none")

    def load_account_daily_snapshots(self, *a):
        return self._do("load_account_daily_snapshots", *a)

    def load_daily_snapshots_or_none(self, *a):
        return self._do("load_daily_snapshots_or_none", *a)

    def load_account_flows_for_dedup_check(self):
        return self._do("load_account_flows_for_dedup_check")

    def load_snaptrade_income_events_or_none(self):
        return self._do("load_snaptrade_income_events_or_none")


def _names(db):
    return [c[0] for c in db.calls]


def test_loads_every_input_by_default():
    db = FakeDb()
    out = pi.load_engine_inputs(db, PS, PE)
    assert out == {
        "trades_df": "<load_trades_or_none>",
        "account_snapshots_df": "<load_account_daily_snapshots>",
        "daily_snapshots_df": "<load_daily_snapshots_or_none>",
        "flows": "<load_account_flows_for_dedup_check>",
        "income_events": "<load_snaptrade_income_events_or_none>",
    }
    # the account history is read UNSCOPED (the session before the period must be visible)
    assert ("load_account_daily_snapshots", ()) in db.calls


def test_a_raising_loader_yields_none_for_that_key_only_and_never_raises():
    for name, key in (("load_trades_or_none", "trades_df"),
                      ("load_account_daily_snapshots", "account_snapshots_df"),
                      ("load_daily_snapshots_or_none", "daily_snapshots_df"),
                      ("load_account_flows_for_dedup_check", "flows"),
                      ("load_snaptrade_income_events_or_none", "income_events")):
        out = pi.load_engine_inputs(FakeDb(raising={name}), PS, PE)
        assert out[key] is None
        assert all(v is not None for k, v in out.items() if k != key)


def test_passed_in_values_are_used_as_is_and_not_reloaded_even_none():
    db = FakeDb()
    out = pi.load_engine_inputs(db, PS, PE, trades_df=None, account_snapshots_df="ACCT", flows=[])
    assert out["trades_df"] is None and out["account_snapshots_df"] == "ACCT" and out["flows"] == []
    assert "load_trades_or_none" not in _names(db)
    assert "load_account_daily_snapshots" not in _names(db)
    assert "load_account_flows_for_dedup_check" not in _names(db)
    # the two inputs that are always loaded here still are
    assert "load_daily_snapshots_or_none" in _names(db) and "load_snaptrade_income_events_or_none" in _names(db)


def test_load_sentinel_triggers_a_load():
    db = FakeDb()
    out = pi.load_engine_inputs(db, PS, PE, trades_df=pi.LOAD, flows=pi._LOAD)
    assert out["trades_df"] == "<load_trades_or_none>" and out["flows"] == "<load_account_flows_for_dedup_check>"


def test_daily_snapshot_window_starts_14_days_before_the_period():
    db = FakeDb()
    pi.load_engine_inputs(db, PS, PE)
    args = next(a for n, a in db.calls if n == "load_daily_snapshots_or_none")
    assert args == (PS - timedelta(days=14), PE)
    assert pi.DAILY_SNAPSHOT_LOOKBACK_DAYS == 14


# --- spy_closes_by_date --------------------------------------------------------

def _hist(closes, idx=None):
    idx = idx or pd.to_datetime(["2026-10-09", "2026-10-12", "2026-10-13"])
    return pd.DataFrame({"Close": closes}, index=idx)


def test_spy_closes_basic_keys_are_date_objects():
    out = pi.spy_closes_by_date(_hist([100.0, 101.0, 102.0]))
    assert out == {D("2026-10-09"): 100.0, D("2026-10-12"): 101.0, D("2026-10-13"): 102.0}


def test_spy_closes_drops_nan_and_non_positive():
    out = pi.spy_closes_by_date(_hist([100.0, float("nan"), -3.0]))
    assert out == {D("2026-10-09"): 100.0}


def test_spy_closes_multiindex_close_column():
    idx = pd.to_datetime(["2026-10-09", "2026-10-12"])
    df = pd.DataFrame([[100.0, 1.0], [101.0, 2.0]], index=idx,
                      columns=pd.MultiIndex.from_tuples([("Close", "SPY"), ("Volume", "SPY")]))
    assert pi.spy_closes_by_date(df) == {D("2026-10-09"): 100.0, D("2026-10-12"): 101.0}


@pytest.mark.parametrize("bad", [None, pd.DataFrame(), pd.DataFrame({"Open": [1.0]}),
                                 pd.DataFrame({"Close": [float("nan")]},
                                              index=pd.to_datetime(["2026-10-09"]))])
def test_spy_closes_unusable_input_is_none(bad):
    assert pi.spy_closes_by_date(bad) is None


# --- compute_period_core ---------------------------------------------------------

def _inputs():
    t, a, dl = week_world()
    return {"trades_df": t, "account_snapshots_df": a, "daily_snapshots_df": dl,
            "flows": [], "income_events": INT_EV}


def test_core_none_inputs_are_all_none():
    core = pi.compute_period_core(None, PS, PE, spy_prices_by_date=None, reference_leverage=2.0)
    assert core == {"period_start": PS, "period_end": PE, "recon": None, "leverage": None,
                    "account_return": None}


def test_core_matches_direct_engine_calls():
    inp = _inputs()
    spy = {FRI: 100.0, MON: 101.0}
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date=spy, reference_leverage=2.0)
    direct = pa.reconcile_period(
        period_start=PS, period_end=PE, trades_df=inp["trades_df"],
        account_snapshots_df=inp["account_snapshots_df"], daily_snapshots_df=inp["daily_snapshots_df"],
        flows=inp["flows"], income_events=inp["income_events"], historical_closes=None)
    assert core["recon"] == direct
    assert core["leverage"] == pa.leverage_section(inp["account_snapshots_df"], PS, PE, 2.0)
    assert core["account_return"]["status"] == "ok"
    assert core["account_return"]["return_pct"] == pytest.approx(-11.5)


def test_core_offline_inputs_stay_offline_not_empty():
    inp = _inputs()
    inp["trades_df"] = None
    inp["flows"] = None
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date=None, reference_leverage=2.0)
    assert core["recon"]["status"] == "offline"
    assert core["account_return"]["status"] == "offline"


def test_helper_never_raises_when_every_loader_raises():
    """The Reports page keeps its own try/except, but the helper itself must not rely on it:
    every loader raising still yields all-None inputs and an offline core, never an exception."""
    db = FakeDb(raising={"load_trades_or_none", "load_account_daily_snapshots",
                         "load_daily_snapshots_or_none", "load_account_flows_for_dedup_check",
                         "load_snaptrade_income_events_or_none"})
    inp = pi.load_engine_inputs(db, PS, PE)
    assert all(v is None for v in inp.values())
    core = pi.compute_period_core(inp, PS, PE, spy_prices_by_date=None, reference_leverage=2.0)
    assert core["recon"]["status"] == "offline"
    assert core["leverage"]["status"] == "offline"
    assert core["account_return"]["status"] == "offline"


# --- import isolation ---------------------------------------------------------------

def test_import_isolation():
    src = (Path(__file__).parent.parent / "stock_analyzer" / "period_inputs.py").read_text(encoding="utf-8")
    for mod in ("risk", "exit_advisor", "risk_advisor", "scoring", "ranking", "streamlit", "db"):
        assert not re.search(rf"^\s*(from|import)\s+(stock_analyzer\.)?{mod}\b", src, re.M), mod
        assert not re.search(rf"from stock_analyzer import[^\n]*\b{mod}\b", src), mod
    code = ("import sys; import stock_analyzer.period_inputs; "
            "bad=[m for m in ('stock_analyzer.risk','stock_analyzer.exit_advisor','stock_analyzer.risk_advisor',"
            "'stock_analyzer.scoring','stock_analyzer.ranking','stock_analyzer.db','streamlit') if m in sys.modules]; "
            "print('BAD', bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(Path(__file__).parent.parent))
    assert "BAD []" in out.stdout, out.stdout + out.stderr
