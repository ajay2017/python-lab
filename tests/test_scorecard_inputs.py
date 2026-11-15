"""Tests for stock_analyzer/scorecard_inputs.py (account scorecard C3): the one loader
the Summary card and the Sunday weekly-debrief email share, plus the email block's
wiring in cron_runner / notify.  Plan: docs/plans/account-scorecard.md sections 8 / 9."""
from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import notify, period_inputs, scorecard, scorecard_inputs
from stock_analyzer.constants import (
    LEVERAGE_REFERENCE_TARGET, TAX_RATE_LONG_TERM, TAX_RATE_SHORT_TERM,
)
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 16)


def _compute_fn():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "scorecard_inputs.py")
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "compute")


class _RaisingDb:
    """Every read raises, as a down database would."""

    def __getattr__(self, name):
        def _f(*_a, **_k):
            raise RuntimeError("db down")
        return _f


class _FakeDb:
    def __init__(self, flows=(), acct=None, trades=None):
        self.flows, self.acct, self.trades = list(flows), acct, trades

    def load_account_flows_for_dedup_check(self):
        return self.flows

    def load_account_daily_snapshots(self):
        return self.acct

    def load_trades_or_none(self):
        return self.trades


# --- the loaders ---------------------------------------------------------------

def test_reads_only_none_on_failure_loaders():
    attrs = {n.attr for n in ast.walk(_compute_fn()) if isinstance(n, ast.Attribute)}
    assert {"load_account_flows_for_dedup_check", "load_account_daily_snapshots",
            "load_trades_or_none"} <= attrs
    assert not attrs & {"load_account_flows", "load_trades", "load_daily_snapshots",
                        "load_snaptrade_income_events"}


def test_engine_inputs_are_loaded_over_the_leverage_window_with_the_shared_frames(monkeypatch):
    acct, trades, flows = pd.DataFrame({"a": [1]}), pd.DataFrame({"t": [1]}), [{"f": 1}]
    win = (date(2026, 10, 1), date(2026, 10, 31))
    seen = {}
    monkeypatch.setattr(scorecard, "leverage_window", lambda a, t: win if a is acct else None)

    def _load(db, start, end, **kw):
        seen.update(start=start, end=end, **kw)
        return {"marker": 1}

    monkeypatch.setattr(period_inputs, "load_engine_inputs", _load)
    got = {}
    monkeypatch.setattr(scorecard, "leverage_verdict",
                        lambda inputs, spy, **kw: got.update(inputs=inputs, kw=kw) or {"state": "ok"})
    scorecard_inputs.compute(_FakeDb(flows, acct, trades), TODAY, {"spy": 1})
    assert (seen["start"], seen["end"]) == win
    assert seen["trades_df"] is trades and seen["account_snapshots_df"] is acct
    assert seen["flows"] == flows
    assert got["inputs"] == {"marker": 1}
    assert got["kw"] == {"today": TODAY, "reference_leverage": LEVERAGE_REFERENCE_TARGET}


def test_no_window_hands_the_account_frame_in_a_dict(monkeypatch):
    acct = pd.DataFrame({"a": [1]})
    monkeypatch.setattr(scorecard, "leverage_window", lambda a, t: None)
    monkeypatch.setattr(period_inputs, "load_engine_inputs",
                        lambda *a, **k: pytest.fail("no window, no engine load"))
    got = {}
    monkeypatch.setattr(scorecard, "leverage_verdict",
                        lambda inputs, spy, **kw: got.update(inputs=inputs) or {"state": "no_complete_month"})
    scorecard_inputs.compute(_FakeDb([], acct, pd.DataFrame()), TODAY, None)
    assert got["inputs"] == {"account_snapshots_df": acct} and got["inputs"]["account_snapshots_df"] is acct


def test_tax_and_since_get_the_constants_and_today(monkeypatch):
    got = {}
    monkeypatch.setattr(scorecard, "tax_triggered", lambda trades, **kw: got.update(tax=kw) or {"state": "ok"})
    monkeypatch.setattr(scorecard, "since_start",
                        lambda *a, **kw: got.update(since=kw) or {"state": "ok"})
    scorecard_inputs.compute(_FakeDb([], pd.DataFrame(), pd.DataFrame()), TODAY, None)
    assert got["tax"] == {"tax_year": 2026, "st_rate": TAX_RATE_SHORT_TERM,
                          "lt_rate": TAX_RATE_LONG_TERM, "today": TODAY}
    assert got["since"] == {"today": TODAY}


def test_a_db_that_raises_gives_offline_states_and_never_raises():
    since, lev, tax = scorecard_inputs.compute(_RaisingDb(), TODAY, None)
    assert (since["state"], lev["state"], tax["state"]) == ("offline", "offline", "offline")


def test_empty_reads_are_not_offline():
    since, lev, tax = scorecard_inputs.compute(
        _FakeDb([], pd.DataFrame(), pd.DataFrame()), TODAY, None)
    assert since["state"] == "no_baseline"
    assert lev["state"] == "no_complete_month"
    assert tax["state"] == "ok"


# --- the Sunday email wiring ---------------------------------------------------

def _run_debrief_node():
    tree = parse_file_cached(ROOT / "cron_runner.py")
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_debrief")


def test_cron_debrief_computes_the_block_from_the_shared_loader_and_never_blocks():
    fn = _run_debrief_node()
    comp = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and c.func.attr == "compute" and isinstance(c.func.value, ast.Name)
            and c.func.value.id == "_scorecard_inputs"]
    assert len(comp) == 1
    # ET date, never a UTC one; and the lane's own 2y SPY closes dict
    assert ast.unparse(comp[0].args[1]) == "now_et.date()"
    assert ast.unparse(comp[0].args[2]) == "spy_by_date"
    # inside a try that catches Exception
    for n in ast.walk(fn):
        for c in ast.iter_child_nodes(n):
            c._parent = n
    p = comp[0]
    while p is not None and not isinstance(p, ast.Try):
        p = getattr(p, "_parent", None)
    assert p is not None
    assert any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in p.handlers)
    # handed to the renderer as `scorecard_lines`, never to the LLM package
    render = next(c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                  and c.func.attr == "render_debrief_email")
    assert "scorecard_lines" in {k.arg for k in render.keywords}
    build = next(c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and c.func.attr == "build_debrief_package")
    assert "scorecard_lines" not in {k.arg for k in build.keywords}


# --- the email block ------------------------------------------------------------

_DEB = {"metrics_version": 2, "performance_pct": 1.0, "spy_pct": 0.5, "alpha_pct": 0.5,
        "week_ending": "2026-11-15"}


def test_no_block_without_lines():
    base = notify.render_debrief_email(_DEB)
    assert notify.render_debrief_email(_DEB, scorecard_lines=None) == base
    assert notify.render_debrief_email(_DEB, scorecard_lines=[]) == base


def test_block_first_line_bold_rest_plain_and_escaped():
    html = notify.render_debrief_email(
        _DEB, scorecard_lines=["Title <x> as of Fri", "$50 ahead & more", "Tax: $555"])
    assert "<b>Title &lt;x&gt; as of Fri</b><br>$50 ahead &amp; more<br>Tax: $555" in html
    assert "<x>" not in html


def test_block_sits_between_the_kpi_tiles_and_the_ai_sections():
    deb = dict(_DEB, section_facts="AI-FACTS-TEXT")
    html = notify.render_debrief_email(deb, scorecard_lines=["Title", "line"])
    assert html.index("vs SPY") < html.index("<b>Title</b>") < html.index("AI-FACTS-TEXT")
