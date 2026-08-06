"""Tests for the model_predictions db.py functions -- Predictive Modeling
Shadow Layer Phase 1 (F-234, MEASUREMENT-ONLY). Mirrors the offline-sentinel
contract test style already established in test_db_load_recommendations.py:
None on ANY failure (no creds, or a raised exception including a pre-DDL
"relation does not exist"), an empty DataFrame ONLY on a genuine zero-row
result."""
import pandas as pd

from stock_analyzer import db


class _FakeExecResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def is_(self, *_a, **_kw):
        return self

    def update(self, *_a, **_kw):
        return self

    def upsert(self, *_a, **_kw):
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated relation does not exist / transient failure")
        return _FakeExecResult(self._rows)


class _FakeClient:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows
        self._raise = raise_on_execute
        self.calls = []

    def table(self, name):
        self.calls.append(name)
        return _FakeQueryBuilder(self._rows, self._raise)


# ── load_model_predictions ────────────────────────────────────────────────────

def test_load_model_predictions_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_model_predictions() is None


def test_load_model_predictions_query_failure_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_model_predictions() is None


def test_load_model_predictions_genuine_empty_result_returns_empty_df(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_model_predictions()
    assert out is not None
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_load_model_predictions_real_rows(monkeypatch):
    rows = [{"ticker": "AAPL", "model_name": "vol_forecast_ewma", "predicted_value": 0.2}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))
    out = db.load_model_predictions()
    assert list(out["ticker"]) == ["AAPL"]


# ── load_unmatured_model_predictions ─────────────────────────────────────────

def test_load_unmatured_model_predictions_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_unmatured_model_predictions() is None


def test_load_unmatured_model_predictions_query_failure_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.load_unmatured_model_predictions() is None


def test_load_unmatured_model_predictions_genuine_empty_returns_empty_df(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.load_unmatured_model_predictions()
    assert out is not None and out.empty


# ── save_model_predictions_batch ─────────────────────────────────────────────

def test_save_model_predictions_batch_readonly_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    assert db.save_model_predictions_batch([{"ticker": "AAPL"}]) is False


def test_save_model_predictions_batch_empty_rows_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    assert db.save_model_predictions_batch([]) is False


def test_save_model_predictions_batch_no_creds_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.save_model_predictions_batch([{"ticker": "AAPL"}]) is False


def test_save_model_predictions_batch_never_raises_on_failure(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.save_model_predictions_batch([{"ticker": "AAPL"}]) is False  # no raise


def test_save_model_predictions_batch_success(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    assert db.save_model_predictions_batch([{"ticker": "AAPL"}]) is True


# ── mature_model_predictions_batch ────────────────────────────────────────────

def test_mature_model_predictions_batch_readonly_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    assert db.mature_model_predictions_batch([{"id": 1, "realized_value": 0.2}]) is False


def test_mature_model_predictions_batch_never_raises_on_failure(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    out = db.mature_model_predictions_batch([{"id": 1, "realized_value": 0.2}])
    assert out is False


def test_mature_model_predictions_batch_success(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    out = db.mature_model_predictions_batch([{"id": 1, "realized_value": 0.2}])
    assert out is True


def test_mature_model_predictions_batch_skips_rows_with_no_id(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)
    out = db.mature_model_predictions_batch([{"realized_value": 0.2}])  # no "id"
    assert out is True  # loop completes without ever calling .update on a bad row
