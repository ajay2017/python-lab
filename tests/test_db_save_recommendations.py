"""Regression test for stock_analyzer/db.py::save_recommendations() — a live
2026-08-07 production run failed to log 3 new-pick recommendations with a
real PostgREST PGRST204 error: "Could not find the 'bq_score' column of
'recommendations' in the schema cache". _col_missing() only recognized
"does not exist" / "unknown column" wording, so the strip-and-retry cascade
never engaged and the whole upsert failed outright (saved=0) instead of
degrading gracefully by dropping the not-yet-DDL'd pillar-score columns —
exactly the failure mode this cascade exists to prevent (see the matching,
already-correct "Could not find the" handling in save_trade(), which is
where this exact PostgREST wording was already proven, just not carried
over to this second, separately-implemented _col_missing()).
"""
from stock_analyzer import db


class _FakeUpsertBuilder:
    def __init__(self, table, calls, raise_exc, raise_on_call):
        self._table = table
        self._calls = calls
        self._raise_exc = raise_exc
        self._raise_on_call = raise_on_call

    def upsert(self, rows, on_conflict=None, ignore_duplicates=None):
        self._calls.append(list(rows))
        return self

    def execute(self):
        call_n = len(self._calls)
        if self._raise_exc is not None and call_n == self._raise_on_call:
            raise self._raise_exc
        return None


class _FakeClient:
    def __init__(self, raise_exc=None, raise_on_call=1):
        self.calls: list[list[dict]] = []
        self._raise_exc = raise_exc
        self._raise_on_call = raise_on_call

    def table(self, name):
        return _FakeUpsertBuilder(name, self.calls, self._raise_exc, self._raise_on_call)


def _row(ticker="CRM"):
    return {
        "ticker": ticker, "rec_date": "2026-08-07", "rec_type": "new_pick",
        "price_at_surface": 250.0, "composite_score": 70, "momentum_score": 60,
        "sector": "Technology", "conviction": "Go", "verdict": "Buy",
        "thesis": "test", "s_score": 50, "avg_sent": 0.1,
        "t_score": 65, "bq_score": 55, "val_score": 45,
    }


def test_save_recommendations_pgrst204_schema_cache_error_degrades_and_retries(monkeypatch):
    """The exact real-world error shape: message + code, stringified as a
    dict (which is what str(APIError) actually produces), not the older
    plain 'column X does not exist' phrasing."""
    exc = Exception(
        "{'message': \"Could not find the 'bq_score' column of "
        "'recommendations' in the schema cache\", 'code': 'PGRST204', "
        "'hint': None, 'details': None}"
    )
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        result = db.save_recommendations([_row()])
    finally:
        _db_mod._CLIENT = None
    assert result["saved"] == 1
    assert result["error"] is None
    # First attempt has bq_score; the retry must have dropped it (and the
    # other pillar-score cols) rather than failing outright.
    assert "bq_score" in fake.calls[0][0]
    assert "bq_score" not in fake.calls[1][0]


def test_save_recommendations_unrelated_error_not_swallowed(monkeypatch):
    """A real failure (not a missing-column shape) must still surface as
    saved=0 with the error preserved, not be mistaken for a degrade case."""
    exc = Exception("connection refused")
    fake = _FakeClient(raise_exc=exc, raise_on_call=1)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        result = db.save_recommendations([_row()])
    finally:
        _db_mod._CLIENT = None
    assert result["saved"] == 0
    assert "connection refused" in result["error"]
    assert len(fake.calls) == 1  # no retry attempted for a non-column error
