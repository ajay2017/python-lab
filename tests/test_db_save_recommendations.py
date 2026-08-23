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


# ── F-249 Phase 2: sizing capture ────────────────────────────────────────────
#
# The cascade is the whole risk here. db.py's own comment records why: a
# column-missing error must peel ONLY the generation that is actually missing,
# because stripping every optional column on the first failure silently stops
# persisting already-working data for the entire window, and reports success
# while doing it (saved=N, error=None).

_SIZING_COLS = ("rec_shares", "rec_stop", "rec_portfolio_value", "rec_sizing_version")
_PILLAR_COLS = ("t_score", "bq_score", "val_score")
_F179_COLS   = ("s_score", "avg_sent")


def _sized_row(ticker="ALB"):
    r = _row(ticker)
    r.update({"rec_shares": 26, "rec_stop": 132.17,
              "rec_portfolio_value": 25487.0, "rec_sizing_version": 2})
    return r


def _run(rows, monkeypatch, exc=None, raise_on_call=1):
    fake = _FakeClient(raise_exc=exc, raise_on_call=raise_on_call)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        return db.save_recommendations(rows), fake
    finally:
        _db_mod._CLIENT = None


def _missing(col):
    return Exception(
        "{'message': \"Could not find the '%s' column of 'recommendations' "
        "in the schema cache\", 'code': 'PGRST204'}" % col
    )


def test_sizing_columns_round_trip_when_the_ddl_is_applied(monkeypatch):
    result, fake = _run([_sized_row()], monkeypatch)
    assert result["saved"] == 1 and result["error"] is None
    sent = fake.calls[0][0]
    assert sent["rec_shares"] == 26.0
    assert sent["rec_stop"] == 132.17
    assert sent["rec_portfolio_value"] == 25487.0
    assert sent["rec_sizing_version"] == 2


def test_missing_sizing_column_strips_ONLY_that_generation(monkeypatch):
    """A rec_shares-missing error must not take the pillar scores with it.

    This is the regression the third generation exists to prevent: before the
    cascade was generalised, the first strip stage removed the pillar columns,
    so a pending sizing DDL would have discarded t_score/bq_score/val_score --
    data already working in production -- and still reported success.
    """
    result, fake = _run([_sized_row()], monkeypatch, exc=_missing("rec_shares"))
    assert result["saved"] == 1 and result["error"] is None
    assert len(fake.calls) == 2, "expected exactly one retry"
    retried = fake.calls[1][0]
    for c in _SIZING_COLS:
        assert c not in retried, f"{c} should have been stripped"
    for c in _PILLAR_COLS + _F179_COLS:
        assert c in retried, f"{c} must SURVIVE a sizing-column error"


def test_second_generation_peels_only_after_its_own_error(monkeypatch):
    """Sizing missing, then pillars missing: peel one generation per failure."""
    fake = _FakeClient()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)

    class _TwoFailures(_FakeClient):
        def table(self, name):
            n = len(self.calls)
            exc = None
            if n == 0:
                exc = _missing("rec_shares")
            elif n == 1:
                exc = _missing("t_score")
            return _FakeUpsertBuilder(name, self.calls, exc, len(self.calls) + 1)

    fake = _TwoFailures()
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        result = db.save_recommendations([_sized_row()])
    finally:
        _db_mod._CLIENT = None
    assert result["saved"] == 1 and result["error"] is None
    assert len(fake.calls) == 3
    final = fake.calls[2][0]
    for c in _SIZING_COLS + _PILLAR_COLS:
        assert c not in final
    for c in _F179_COLS:
        assert c in final, "sentiment must survive two unrelated generations failing"


def test_declined_size_keeps_version_but_nulls_shares(monkeypatch):
    """version set + shares NULL must stay distinguishable from not-captured.

    The app deliberately suggests no size when one share breaches the ceiling
    or price is at/below the stop. That is a real decision and must not read
    the same as a pre-capture row.
    """
    r = _row("NVR")
    r.update({"rec_shares": None, "rec_stop": None,
              "rec_portfolio_value": 25487.0, "rec_sizing_version": 2})
    _, fake = _run([r], monkeypatch)
    sent = fake.calls[0][0]
    assert sent["rec_sizing_version"] == 2
    assert sent["rec_shares"] is None and sent["rec_stop"] is None
    assert sent["rec_portfolio_value"] == 25487.0


def test_row_with_no_sizing_at_all_writes_four_nulls(monkeypatch):
    """buy_candidate rows never carry sizing -- all four columns NULL, no crash."""
    _, fake = _run([_row("XYZ")], monkeypatch)
    sent = fake.calls[0][0]
    for c in _SIZING_COLS:
        assert c in sent and sent[c] is None


def test_garbage_sizing_values_are_coerced_to_null(monkeypatch):
    """Non-numeric, zero and negative suggestions store NULL, not 0.

    Phase 3 divides by rec_shares; a 0 or a string would either crash the
    take-rate arithmetic or silently produce a nonsense ratio.
    """
    r = _row("BAD")
    r.update({"rec_shares": "twenty", "rec_stop": -5.0,
              "rec_portfolio_value": 0, "rec_sizing_version": "two"})
    _, fake = _run([r], monkeypatch)
    sent = fake.calls[0][0]
    for c in _SIZING_COLS:
        assert sent[c] is None, f"{c} should have coerced to None"


def test_missing_pillar_column_does_NOT_strip_sizing(monkeypatch):
    """The mirror guarantee, and the reason the cascade is error-targeted.

    An earlier draft peeled generations positionally (newest first), so a
    `bq_score`-missing error stripped the sizing columns on the way past --
    discarding data that was working because something unrelated was absent.
    PostgREST names the offending column; the cascade must use it.
    """
    result, fake = _run([_sized_row()], monkeypatch, exc=_missing("bq_score"))
    assert result["saved"] == 1 and result["error"] is None
    assert len(fake.calls) == 2, "one targeted retry, not a walk down the generations"
    retried = fake.calls[1][0]
    for c in _PILLAR_COLS:
        assert c not in retried
    for c in _SIZING_COLS + _F179_COLS:
        assert c in retried, f"{c} must survive an unrelated pillar-column error"


def test_cascade_terminates_when_the_error_repeats_the_same_generation(monkeypatch):
    """The `generation <= stripped` subset guard — the anti-infinite-loop line.

    If PostgREST keeps naming a column we have already stripped, the loop must
    bail rather than re-strip the same generation forever. Correct by reading,
    but this is the one branch whose failure mode is a hung request rather than
    a wrong number, so it gets a test.
    """
    class _AlwaysSameError(_FakeClient):
        def table(self, name):
            return _FakeUpsertBuilder(name, self.calls, _missing("rec_shares"),
                                      len(self.calls) + 1)

    fake = _AlwaysSameError()
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        result = db.save_recommendations([_sized_row()])
    finally:
        _db_mod._CLIENT = None
    # Bounded: initial + one targeted strip + the last-resort strip-all floor.
    assert len(fake.calls) <= 4, f"unbounded retry: {len(fake.calls)} calls"
    assert result["saved"] == 0
    assert result["error"], "a persistent failure must surface, not report success"


def test_cron_new_pick_rows_carry_the_sizing_columns():
    """The cron lane is the writer that usually WINS the day.

    It runs before any interactive session and the upsert ignores duplicates,
    so these are the values Phase 3 will mostly read. Untested until now.
    """
    import cron_runner
    rows = cron_runner._build_new_pick_rows([
        {"ticker": "ALB", "price": 143.25, "score": 67,
         "sizing": {"shares": 26, "stop": 132.17,
                    "portfolio_value": 25487.0, "sizing_version": 2}},
    ], "2026-08-23")
    assert len(rows) == 1
    r = rows[0]
    assert r["rec_shares"] == 26 and r["rec_stop"] == 132.17
    assert r["rec_portfolio_value"] == 25487.0 and r["rec_sizing_version"] == 2


def test_cron_declined_size_keeps_version_and_basis_only():
    """A ceiling/stop marker on the cron path: version + basis, no shares."""
    import cron_runner
    rows = cron_runner._build_new_pick_rows([
        {"ticker": "NVR", "sizing": {"ceiling_infeasible": True,
                                     "portfolio_value": 25487.0,
                                     "sizing_version": 2}},
    ], "2026-08-23")
    r = rows[0]
    assert r["rec_sizing_version"] == 2
    assert r["rec_portfolio_value"] == 25487.0
    assert r.get("rec_shares") is None and r.get("rec_stop") is None


def test_cron_row_without_sizing_omits_the_columns_entirely():
    """No sizing dict => keys absent, so db.py's _pos_num lands them NULL."""
    import cron_runner
    rows = cron_runner._build_new_pick_rows([{"ticker": "XYZ"}], "2026-08-23")
    r = rows[0]
    for c in _SIZING_COLS:
        assert c not in r
