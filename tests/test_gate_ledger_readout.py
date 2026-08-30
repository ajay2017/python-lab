"""
Tests for stock_analyzer/gate_ledger_readout.py — Gate Suppression Ledger
READOUT half (F-259 Phase 2).

Covers the invariants this feature depends on being correct
(docs/plans/gate-suppression-ledger.md §5/§5a):
  1. Two-floor banding boundary — N_min AND K both required to leave "building"
  2. K is load-bearing even when N_min is far exceeded (one ticker, many rows)
  3. Maturity boundary — exact horizon match matures; one session short doesn't
  4. counterfactual=False excluded from every evaluable count
  5. G-01 restricted to source='app' (defends against a cron row even though
     this shouldn't occur in real data — cron always passes risk_recs=[])
  6. G-23 (__MARKET__) excluded, tagged market_wide, NEVER reaches
     forward_alpha_at_horizon
  7. New-pick-lane composite filter (>= buy evaluable, < buy excluded, None
     excluded+tallied separately) vs add-lane (no filter at all)
  8. A matured-but-unpriceable row is excluded from the mean, tallied separately
  9. load_gate_suppressions() offline contract at the db.py level
 10. Import-isolation redline — never imported by a decision/sizing module
 11. Reuse invariant — forward_alpha_at_horizon is the SOLE alpha source;
     this module writes no arithmetic of its own
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stock_analyzer import db
from stock_analyzer import gate_ledger_readout as glr
from stock_analyzer.constants import (
    COMPOSITE_BUY,
    GATE_LEDGER_MIN_CALLS,
    GATE_LEDGER_FIRM_CALLS,
    GATE_LEDGER_MIN_TICKERS,
)
from stock_analyzer.predictive_analytics import _advance_trading_days

REC_DATE = date(2026, 1, 5)
HORIZON = 30
TARGET_DATE = _advance_trading_days(REC_DATE, HORIZON)
ALMOST_MATURE_DATE = _advance_trading_days(REC_DATE, HORIZON - 1)


# ─── builders ───────────────────────────────────────────────────────────────

def _row(**over):
    base = {
        "ticker": "AAA",
        "gate_id": "G-04",
        "lane": "add_winner",
        "source": "app",
        "counterfactual": True,
        "tone": "bull",
        "rec_date": REC_DATE,
        "price_at_suppress": 100.0,
        "composite_score": 70.0,
        "momentum_score": 80.0,
        "sector": "Technology",
        "gate_value": None,
        "gate_threshold": None,
        "reason": "test",
    }
    base.update(over)
    return base


def _graded_row(ticker, gate_id="G-04", status=None, alpha=1.0, rec_date=REC_DATE):
    status = status or glr.STATUS_MATURED_EVALUABLE
    return {
        "ticker": ticker,
        "gate_id": gate_id,
        "status": status,
        "alpha_pct": alpha if status == glr.STATUS_MATURED_EVALUABLE else None,
        "rec_date": rec_date,
    }


def _n_rows(n, distinct, gate_id="G-04"):
    """n pre-graded matured_evaluable rows cycling among `distinct` tickers."""
    return [_graded_row(f"T{i % distinct}", gate_id=gate_id, alpha=float(i)) for i in range(n)]


def _spy_flat(d1, d2, price=100.0):
    return {d1: price, d2: price}


def _never_called_close_fn(*_a, **_kw):
    raise AssertionError(
        "historical_close_fn must not be called for an excluded/unmatured row "
        "-- maturity/scope must be checked BEFORE any fetch"
    )


# ─── 1/2. banding — two floors, both required; K is load-bearing ───────────

def test_band_building_when_n_below_floor_but_distinct_meets_floor():
    enriched = _n_rows(GATE_LEDGER_MIN_CALLS - 1, GATE_LEDGER_MIN_TICKERS)
    out = glr.grade_by_gate(
        enriched, gate_ids=("G-04",), min_calls=GATE_LEDGER_MIN_CALLS,
        firm_calls=GATE_LEDGER_FIRM_CALLS, min_tickers=GATE_LEDGER_MIN_TICKERS,
    )
    assert out[0]["band"] == "building"


def test_band_building_when_k_floor_bites_even_though_n_floor_cleared():
    enriched = _n_rows(GATE_LEDGER_MIN_CALLS, GATE_LEDGER_MIN_TICKERS - 1)
    out = glr.grade_by_gate(
        enriched, gate_ids=("G-04",), min_calls=GATE_LEDGER_MIN_CALLS,
        firm_calls=GATE_LEDGER_FIRM_CALLS, min_tickers=GATE_LEDGER_MIN_TICKERS,
    )
    assert out[0]["band"] == "building"


def test_band_early_at_exact_both_floors_boundary():
    enriched = _n_rows(GATE_LEDGER_MIN_CALLS, GATE_LEDGER_MIN_TICKERS)
    out = glr.grade_by_gate(
        enriched, gate_ids=("G-04",), min_calls=GATE_LEDGER_MIN_CALLS,
        firm_calls=GATE_LEDGER_FIRM_CALLS, min_tickers=GATE_LEDGER_MIN_TICKERS,
    )
    assert out[0]["band"] == "early"


def test_band_firm_at_firm_calls_boundary():
    enriched = _n_rows(GATE_LEDGER_FIRM_CALLS, GATE_LEDGER_MIN_TICKERS)
    out = glr.grade_by_gate(
        enriched, gate_ids=("G-04",), min_calls=GATE_LEDGER_MIN_CALLS,
        firm_calls=GATE_LEDGER_FIRM_CALLS, min_tickers=GATE_LEDGER_MIN_TICKERS,
    )
    assert out[0]["band"] == "firm"


def test_k_is_load_bearing_one_ticker_recorded_repeatedly_stays_building():
    """30 rows, all the same ticker -- n=30 >> min_calls, but distinct=1 <<
    min_tickers, so the gate must stay 'building', not evaluable."""
    enriched = _n_rows(30, 1)
    out = glr.grade_by_gate(
        enriched, gate_ids=("G-04",), min_calls=GATE_LEDGER_MIN_CALLS,
        firm_calls=GATE_LEDGER_FIRM_CALLS, min_tickers=GATE_LEDGER_MIN_TICKERS,
    )
    assert out[0]["n_matured_evaluable"] == 30
    assert out[0]["n_distinct_tickers_evaluable"] == 1
    assert out[0]["band"] == "building"


# ─── 3. maturity boundary ───────────────────────────────────────────────────

def test_maturity_boundary_exact_horizon_matures():
    rows = [_row(rec_date=REC_DATE, price_at_suppress=100.0)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date=_spy_flat(REC_DATE, TARGET_DATE),
        historical_close_fn=lambda t, s, e: 110.0,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_MATURED_EVALUABLE
    assert out[0]["alpha_pct"] == pytest.approx(10.0)


def test_maturity_boundary_one_session_short_is_not_matured():
    rows = [_row(rec_date=REC_DATE)]
    out = glr.enrich_and_grade(
        rows, today=ALMOST_MATURE_DATE, spy_close_by_date={},
        historical_close_fn=_never_called_close_fn,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_NOT_MATURED
    assert out[0]["alpha_pct"] is None


# ─── 4. counterfactual=False excluded ───────────────────────────────────────

def test_counterfactual_false_excluded_from_every_evaluable_count():
    rows = [_row(counterfactual=False)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date={},
        historical_close_fn=_never_called_close_fn,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_EXCLUDED_COUNTERFACTUAL_FALSE
    assert out[0]["alpha_pct"] is None

    graded = glr.grade_by_gate(
        out, gate_ids=("G-04",), min_calls=1, firm_calls=2, min_tickers=1,
    )
    assert graded[0]["n_matured_evaluable"] == 0
    assert graded[0]["n_excluded_counterfactual_false"] == 1


def test_counterfactual_none_also_excluded():
    """DB rows can carry a NULL counterfactual (unexpected legacy state) --
    only an explicit True is binding evidence."""
    rows = [_row(counterfactual=None)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date={},
        historical_close_fn=_never_called_close_fn,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_EXCLUDED_COUNTERFACTUAL_FALSE


# ─── 5. G-01 restricted to source='app' ─────────────────────────────────────

def test_g01_over_cron_source_never_enters_evaluable_tally():
    """Structurally shouldn't occur (cron always passes risk_recs=[]), but
    the readout must defend against it anyway (§5, F1)."""
    rows = [_row(gate_id="G-01", lane="add_winner", source="cron", counterfactual=True)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date={},
        historical_close_fn=_never_called_close_fn,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_EXCLUDED_SOURCE_MISMATCH

    graded = glr.grade_by_gate(
        out, gate_ids=("G-01",), min_calls=1, firm_calls=2, min_tickers=1,
    )
    assert graded[0]["n_matured_evaluable"] == 0


def test_g01_over_app_source_is_not_excluded_for_source_reasons():
    rows = [_row(gate_id="G-01", lane="add_winner", source="app", counterfactual=True)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date=_spy_flat(REC_DATE, TARGET_DATE),
        historical_close_fn=lambda t, s, e: 90.0,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_MATURED_EVALUABLE


# ─── 6. G-23 market-wide — excluded, never priced ──────────────────────────

def test_g23_market_wide_row_is_tagged_and_never_fetched():
    rows = [_row(
        ticker="__MARKET__", gate_id="G-23", lane="tone", counterfactual=True,
        composite_score=None, price_at_suppress=None,
    )]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date={},
        historical_close_fn=_never_called_close_fn,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_MARKET_WIDE

    graded = glr.grade_by_gate(
        out, gate_ids=("G-23",), min_calls=1, firm_calls=2, min_tickers=1,
    )
    assert graded[0]["market_wide"] is True
    assert "n_matured_evaluable" not in graded[0]


def test_g23_never_reaches_forward_alpha_at_horizon(monkeypatch):
    calls = []

    def _fake_forward_alpha(ticker, *a, **kw):
        calls.append(ticker)
        return 5.0

    monkeypatch.setattr(glr, "forward_alpha_at_horizon", _fake_forward_alpha)
    rows = [_row(ticker="__MARKET__", gate_id="G-23", lane="tone")]
    glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date={},
        historical_close_fn=lambda *a, **k: 100.0,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert calls == []
    assert "__MARKET__" not in calls


# ─── 7. new-pick-lane composite filter vs add-lane (no filter) ─────────────

def test_new_pick_lane_composite_at_or_above_buy_is_evaluable():
    rows = [_row(gate_id="G-07", lane="new_pick", composite_score=COMPOSITE_BUY)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date=_spy_flat(REC_DATE, TARGET_DATE),
        historical_close_fn=lambda t, s, e: 110.0,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_MATURED_EVALUABLE


def test_new_pick_lane_composite_below_buy_is_excluded():
    rows = [_row(gate_id="G-07", lane="new_pick", composite_score=COMPOSITE_BUY - 1)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date={},
        historical_close_fn=_never_called_close_fn,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_EXCLUDED_LOW_COMPOSITE


def test_new_pick_lane_null_composite_excluded_and_tallied_separately():
    rows = [_row(gate_id="G-07", lane="new_pick", composite_score=None)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date={},
        historical_close_fn=_never_called_close_fn,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_EXCLUDED_NULL_COMPOSITE

    graded = glr.grade_by_gate(
        out, gate_ids=("G-07",), min_calls=1, firm_calls=2, min_tickers=1,
    )
    assert graded[0]["n_excluded_null_composite"] == 1
    assert graded[0]["n_excluded_low_composite"] == 0


def test_add_lane_gate_with_null_composite_is_still_evaluable():
    """Add-lane gates (G-01/G-04/G-09/G-20/G-24) get NO composite filter --
    reaching an add-lane suppression site already proves the name would
    have been an add regardless of composite (§5a-1)."""
    rows = [_row(gate_id="G-04", lane="add_winner", composite_score=None)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date=_spy_flat(REC_DATE, TARGET_DATE),
        historical_close_fn=lambda t, s, e: 105.0,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_MATURED_EVALUABLE


# ─── 8. matured but unpriceable ─────────────────────────────────────────────

def test_matured_row_with_no_entry_price_is_unpriceable_not_evaluable():
    rows = [_row(price_at_suppress=None)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date=_spy_flat(REC_DATE, TARGET_DATE),
        historical_close_fn=lambda t, s, e: 110.0,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_MATURED_UNPRICEABLE
    assert out[0]["alpha_pct"] is None

    graded = glr.grade_by_gate(
        out, gate_ids=("G-04",), min_calls=1, firm_calls=2, min_tickers=1,
    )
    assert graded[0]["n_matured_unpriceable"] == 1
    assert graded[0]["n_matured_evaluable"] == 0
    assert graded[0]["mean_alpha_pct"] is None


def test_matured_row_with_missing_forward_close_is_unpriceable():
    rows = [_row(price_at_suppress=100.0)]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date=_spy_flat(REC_DATE, TARGET_DATE),
        historical_close_fn=lambda t, s, e: None,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert out[0]["status"] == glr.STATUS_MATURED_UNPRICEABLE


# ─── 9. load_gate_suppressions() offline contract ──────────────────────────

class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows if rows is not None else []
        self._raise = raise_on_execute

    def select(self, *_a, **_kw):
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated transient Supabase failure")
        return _FakeExecResult(self._rows)


class _FakeClient:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute

    def table(self, _name):
        return _FakeQueryBuilder(self._rows, self._raise)


def test_load_gate_suppressions_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_gate_suppressions() is None


def test_load_gate_suppressions_query_failure_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_gate_suppressions() is None


def test_load_gate_suppressions_genuine_empty_result_returns_empty_list_not_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_gate_suppressions()
    assert out is not None
    assert out == []


def test_load_gate_suppressions_returns_real_rows(monkeypatch):
    rows = [{"ticker": "AAPL", "gate_id": "G-04", "rec_date": "2026-01-05"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))
    out = db.load_gate_suppressions()
    assert out == rows


# ─── 10. import-isolation redline ───────────────────────────────────────────

def test_gate_ledger_readout_never_imported_by_decision_or_sizing_modules():
    """This module is a RETROSPECTIVE MEASUREMENT and must be a dead end --
    it must never feed risk_advisor, exit_advisor, daily_briefing, scoring,
    or any sizing path (constants.py's GATE_LEDGER_* redline).

    risk.py is the actual live sizing engine (position_sizing /
    sizing_unavailable_reason, F-249/F-255's net_capital/max_capital cap) --
    it is neither "risk_advisor.py" nor *sizing*.py-named, so an earlier
    version of this test's file list missed it entirely, leaving the single
    highest-stakes property this test exists to enforce unchecked (Opus
    review finding, 2026-08-30). portfolio.py/ranking.py added as
    defense-in-depth since they also feed pick/sizing decisions."""
    repo = Path(__file__).parent.parent / "stock_analyzer"
    named = [
        "risk_advisor.py", "exit_advisor.py", "daily_briefing.py", "scoring.py",
        "risk.py", "portfolio.py", "ranking.py",
    ]
    sizing_named = [p.name for p in repo.glob("*sizing*.py")]
    checked = sorted(set(named) | set(sizing_named))
    for fname in checked:
        path = repo / fname
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "gate_ledger_readout" not in text, (
            f"{fname} must never import/reference gate_ledger_readout -- this "
            "is a dead-end retrospective measurement module by design."
        )


# ─── 11. reuse invariant — no second alpha calculation ─────────────────────

def test_reuse_invariant_forward_alpha_result_flows_through_unchanged(monkeypatch):
    sentinel = -12.34
    calls = []

    def _fake_forward_alpha(*args, **kwargs):
        calls.append(args)
        return sentinel

    monkeypatch.setattr(glr, "forward_alpha_at_horizon", _fake_forward_alpha)
    rows = [_row()]
    out = glr.enrich_and_grade(
        rows, today=TARGET_DATE, spy_close_by_date={},
        historical_close_fn=lambda *a, **k: 100.0,
        horizon_trading_days=HORIZON, composite_buy=COMPOSITE_BUY,
    )
    assert len(calls) == 1
    assert out[0]["status"] == glr.STATUS_MATURED_EVALUABLE
    assert out[0]["alpha_pct"] == sentinel


# ─── readout_footnotes() ────────────────────────────────────────────────────

def test_readout_footnotes_g20_flags_bull_day_only_undercount():
    notes = glr.readout_footnotes("G-20")
    assert len(notes) == 1
    assert "bull" in notes[0].lower()


def test_readout_footnotes_g23_flags_no_instrument():
    notes = glr.readout_footnotes("G-23")
    assert len(notes) == 1
    assert "instrument" in notes[0].lower() or "market" in notes[0].lower()


def test_readout_footnotes_unknown_gate_returns_empty():
    assert glr.readout_footnotes("G-04") == []
    assert glr.readout_footnotes("G-99") == []
