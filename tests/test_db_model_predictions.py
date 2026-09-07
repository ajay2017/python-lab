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
        self._range = None

    def select(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def eq(self, *_a, **_kw):
        return self

    def is_(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def update(self, *_a, **_kw):
        return self

    def upsert(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("simulated relation does not exist / transient failure")
        if self._range is not None:
            start, end = self._range
            return _FakeExecResult(self._rows[start:end + 1])
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


def test_load_model_predictions_paginates_past_page_size(monkeypatch):
    """2026-09-04 live incident: PostgREST silently caps an unpaginated
    `.select("*")` at its server-side default row limit, so a result set
    bigger than one page must still come back whole via `.range()` looping —
    confirmed live when the 08-06 cohort's true 13 matured tickers reported
    as only 9 once the table passed 1000 rows for one model_name."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_MODEL_PREDICTIONS_PAGE_SIZE", 2)
    rows = [{"ticker": f"T{i}"} for i in range(5)]
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=rows))
    out = db.load_model_predictions()
    assert len(out) == 5
    assert list(out["ticker"]) == [f"T{i}" for i in range(5)]


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


# ── has_backfilled_predictions ────────────────────────────────────────────────
# Feeds the `maintenance` cron lane's skip decision. The tri-state matters:
# True = skip, False = do the work, None = UNKNOWN, which must NOT be collapsed
# into "already done" or a transient DB blip would leave a permanent hole in
# the ledger.

def test_has_backfilled_predictions_no_creds_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.has_backfilled_predictions("vol_forecast_ewma", "v1", "AAPL") is None


def test_has_backfilled_predictions_query_failure_returns_none(monkeypatch):
    """A pre-DDL table or a transient error is 'unknown', never False."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(raise_on_execute=True))
    assert db.has_backfilled_predictions("vol_forecast_ewma", "v1", "AAPL") is None


def test_has_backfilled_predictions_true_when_row_exists(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[{"id": 7}]))
    assert db.has_backfilled_predictions("vol_forecast_ewma", "v1", "AAPL") is True


def test_has_backfilled_predictions_false_when_no_rows(monkeypatch):
    """Genuine zero-row result is False (do the backfill) — distinct from None."""
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows=[]))
    assert db.has_backfilled_predictions("vol_forecast_ewma", "v1", "AAPL") is False


def test_has_backfilled_predictions_queries_model_predictions(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(db, "_client", lambda: fake)
    db.has_backfilled_predictions("vol_forecast_ewma", "v1", "AAPL")
    assert fake.calls == ["model_predictions"]


# ── withdraw_unmatured_model_prediction ──────────────────────────────────────
# Phase 2 (F-234, earnings-move magnitude) — deletes ONE model_predictions row
# by id, but ONLY while still unmatured (realized_value IS NULL). Used to
# withdraw a prediction whose frozen earnings event_date was rescheduled.

class _FakeDeleteQueryBuilder(_FakeQueryBuilder):
    """Extends the shared fake query builder to record the exact filter
    chain a `.delete()` call applies, so a test can confirm the
    `.eq("id", ...).is_("realized_value", "null")` shape is actually sent,
    not just that SOME delete happened."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.delete_calls: list[tuple] = []

    def delete(self, *_a, **_kw):
        self._is_delete = True
        return self

    def eq(self, col, val):
        if getattr(self, "_is_delete", False):
            self.delete_calls.append(("eq", col, val))
        return self

    def is_(self, col, val):
        if getattr(self, "_is_delete", False):
            self.delete_calls.append(("is_", col, val))
        return self


class _FakeDeleteClient:
    def __init__(self, raise_on_execute=False):
        self._raise = raise_on_execute
        self.builder = None

    def table(self, name):
        self.builder = _FakeDeleteQueryBuilder(raise_on_execute=self._raise)
        return self.builder


def test_withdraw_unmatured_model_prediction_readonly_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: True)
    monkeypatch.setattr(db, "has_db", lambda: True)
    assert db.withdraw_unmatured_model_prediction(1) is False


def test_withdraw_unmatured_model_prediction_no_db_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.withdraw_unmatured_model_prediction(1) is False


def test_withdraw_unmatured_model_prediction_none_id_noop(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    assert db.withdraw_unmatured_model_prediction(None) is False


def test_withdraw_unmatured_model_prediction_never_raises_on_failure(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeDeleteClient(raise_on_execute=True))
    assert db.withdraw_unmatured_model_prediction(1) is False  # no raise


def test_withdraw_unmatured_model_prediction_success_returns_true(monkeypatch):
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeDeleteClient()
    monkeypatch.setattr(db, "_client", lambda: fake)
    assert db.withdraw_unmatured_model_prediction(7) is True


def test_withdraw_unmatured_model_prediction_sends_the_right_filter_shape(monkeypatch):
    """Confirms the delete is actually filtered by id AND realized_value IS
    NULL -- not a bare unconditional delete -- so a race with the
    maturation cron can never erase a scored outcome."""
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    fake = _FakeDeleteClient()
    monkeypatch.setattr(db, "_client", lambda: fake)
    db.withdraw_unmatured_model_prediction(7)
    assert ("eq", "id", 7) in fake.builder.delete_calls
    assert ("is_", "realized_value", "null") in fake.builder.delete_calls
