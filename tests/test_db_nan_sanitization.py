"""Regression tests for the 2026-10-02 review C1 finding: a withheld
holding's honest-NaN composite score (portfolio.py:589-634) reaching a
Supabase write with no sanitization raises inside httpx's JSON encoder
(`json.dumps(..., allow_nan=False)`) BEFORE any network call —
`save_exit_signals_batch`'s bare `except Exception` then silently drops the
ENTIRE batch (every held ticker's signal that day, not just the withheld
one), and `save_thesis_review`'s except block doesn't match the error at all
so the review is shown to the user but silently never persisted.

Covers:
  - the raw mechanism (pins the actual failure mode, not an imagined one)
  - save_exit_signals_batch survives a NaN composite_score and writes None
  - save_thesis_review survives a NaN buried in evidence_snapshot and writes None
"""
import json
import math

import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast


# ── Mechanism pin: proves the actual failure mode, not a hypothetical one ──

def test_json_dumps_allow_nan_false_raises_on_nan():
    """Pins the exact mechanism postgrest-py/httpx hit: json.dumps with
    allow_nan=False (httpx's encode_json) raises ValueError on a NaN float,
    before any network I/O."""
    with pytest.raises(ValueError, match="not JSON compliant"):
        json.dumps([{"composite_score": float("nan")}], allow_nan=False)


def test_json_safe_coerces_nan_to_none():
    assert db._json_safe({"composite_score": float("nan")}) == {"composite_score": None}
    assert db._json_safe({"composite_score": float("inf")}) == {"composite_score": None}
    assert db._json_safe({"composite_score": 72.0}) == {"composite_score": 72.0}


def test_json_safe_coerces_nan_nested_in_dict():
    nested = {"evidence_snapshot": {"composite": float("nan"), "label": "WEAKENING"}}
    out = db._json_safe(nested)
    assert out["evidence_snapshot"]["composite"] is None
    assert out["evidence_snapshot"]["label"] == "WEAKENING"


# ── save_exit_signals_batch: a NaN composite_score must not raise and must
# not drop the batch; the record written must carry None, not NaN ─────────

class _FakeExecResult:
    def __init__(self, data=None):
        self.data = data


class _FakeSelectBuilder:
    """Returns whatever pre-existing rows the client was constructed with
    (default: none, so the pre-read merge is a no-op) — lets a test simulate
    a row a PRIOR build already saved today, to exercise the coalesce-merge
    path (Opus review, 2026-10-02 confirmation pass)."""
    def __init__(self, existing_rows):
        self._existing_rows = existing_rows

    def in_(self, col, values):
        return self

    def execute(self):
        return _FakeExecResult(self._existing_rows)


class _FakeUpsertBuilder:
    def __init__(self, store, records):
        self._store = store
        self._records = records

    def execute(self):
        # Opus review, 2026-10-02 confirmation pass: without this, a fake
        # that just stores records in a dict can't reproduce the real
        # failure mode (httpx's allow_nan=False encoder raising before any
        # network call) -- so a test could pass even if a future change
        # reintroduced a raw NaN into what actually reaches the upsert.
        # Mirrors the real encode_request path exactly (same call shape).
        json.dumps(self._records, allow_nan=False)
        for r in self._records:
            key = (r.get("ticker"), str(r.get("signal_date")), r.get("signal_type"))
            self._store[key] = dict(r)
        return _FakeExecResult([])


class _FakeExitSignalsTable:
    def __init__(self, store, existing_rows):
        self._store = store
        self._existing_rows = existing_rows

    def select(self, cols):
        return _FakeSelectBuilder(self._existing_rows)

    def upsert(self, records, on_conflict=None):
        return _FakeUpsertBuilder(self._store, records)


class _FakeExitSignalsClient:
    def __init__(self, existing_rows=None):
        self.store: dict[tuple, dict] = {}
        self._existing_rows = existing_rows or []

    def table(self, name):
        assert name == "exit_signals"
        return _FakeExitSignalsTable(self.store, self._existing_rows)


@pytest.fixture(autouse=True)
def _patch_db_flags(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    yield


def _install(fake):
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake


def _teardown():
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = None


def _sig(ticker="AAPL", signal_date="2026-10-02", signal_type="WATCH", **overrides):
    row = {
        "ticker": ticker, "signal_date": signal_date, "signal_type": signal_type,
        "composite_score": None, "price_at_signal": None, "dd_from_peak_pct": None,
        "pnl_pct": None, "below_ma_count": None, "rel_strength": None,
    }
    row.update(overrides)
    return row


def test_nan_composite_score_does_not_raise():
    fake = _FakeExitSignalsClient()
    _install(fake)
    try:
        ok = db.save_exit_signals_batch([_sig(composite_score=float("nan"))])
        assert ok is True
    finally:
        _teardown()


def test_nan_composite_score_written_as_none_not_nan():
    fake = _FakeExitSignalsClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(composite_score=float("nan"))])
        key = ("AAPL", "2026-10-02", "WATCH")
        written = fake.store[key]["composite_score"]
        # Must be a real None, not a NaN float -- `written != written` would
        # be True for NaN and False for None, so this also guards against a
        # fix that merely swallows the field rather than sanitizing it.
        assert written is None
    finally:
        _teardown()


def test_nan_in_one_signal_does_not_drop_the_whole_batch():
    """The actual reported bug: one withheld ticker's NaN composite must not
    sink every OTHER held ticker's signal in the same batch."""
    fake = _FakeExitSignalsClient()
    _install(fake)
    try:
        ok = db.save_exit_signals_batch([
            _sig(ticker="AAPL", composite_score=float("nan")),
            _sig(ticker="MSFT", composite_score=72.0),
        ])
        assert ok is True
        assert fake.store[("AAPL", "2026-10-02", "WATCH")]["composite_score"] is None
        assert fake.store[("MSFT", "2026-10-02", "WATCH")]["composite_score"] == 72.0
    finally:
        _teardown()


def test_nan_sanitized_before_coalesce_does_not_clobber_prior_non_null_value():
    """Opus review, 2026-10-02 confirmation pass (non-blocking finding on the
    C1 fix itself): the pre-read coalesce merge's 'don't overwrite a prior
    value' check is `s.get(col) is None` -- a raw NaN is NOT None, so if
    sanitize ran AFTER the merge, a NaN would skip the merge entirely and
    then get coerced straight to a writable None, silently clobbering a
    non-null value a PRIOR same-day build already saved. Sanitizing BEFORE
    the merge means the NaN already reads as None by the time the merge
    runs, so it correctly qualifies for 'fill from the existing row' like
    any other missing value -- the prior 70.0 must survive."""
    existing_row = {
        "ticker": "AAPL", "signal_date": "2026-10-02", "signal_type": "WATCH",
        "composite_score": 70.0, "price_at_signal": None,
        "dd_from_peak_pct": None, "pnl_pct": None, "rel_strength": None,
    }
    fake = _FakeExitSignalsClient(existing_rows=[existing_row])
    _install(fake)
    try:
        ok = db.save_exit_signals_batch([_sig(composite_score=float("nan"))])
        assert ok is True
        key = ("AAPL", "2026-10-02", "WATCH")
        assert fake.store[key]["composite_score"] == 70.0
    finally:
        _teardown()


def test_non_nan_composite_score_unaffected():
    fake = _FakeExitSignalsClient()
    _install(fake)
    try:
        db.save_exit_signals_batch([_sig(composite_score=65.0)])
        key = ("AAPL", "2026-10-02", "WATCH")
        assert fake.store[key]["composite_score"] == 65.0
    finally:
        _teardown()


# ── save_thesis_review: a NaN buried in evidence_snapshot must not raise and
# must not silently fail to persist ─────────────────────────────────────────

class _FakeThesisInsertBuilder:
    def __init__(self, calls):
        self._calls = calls

    def insert(self, record):
        self._calls.append(dict(record))
        return self

    def execute(self):
        return _FakeExecResult()


class _FakeThesisClient:
    def __init__(self):
        self.calls: list[dict] = []

    def table(self, name):
        return _FakeThesisInsertBuilder(self.calls)


def test_save_thesis_review_nan_in_evidence_snapshot_does_not_raise():
    fake = _FakeThesisClient()
    _install(fake)
    try:
        ok = db.save_thesis_review({
            "ticker": "AAPL", "status": "INTACT", "summary": "ok",
            "evidence_snapshot": {"composite": float("nan"), "label": "WEAKENING"},
        })
        assert ok is True
    finally:
        _teardown()


def test_save_thesis_review_nan_in_evidence_snapshot_written_as_none():
    fake = _FakeThesisClient()
    _install(fake)
    try:
        db.save_thesis_review({
            "ticker": "AAPL", "status": "INTACT", "summary": "ok",
            "evidence_snapshot": {"composite": float("nan"), "label": "WEAKENING"},
        })
        assert len(fake.calls) == 1
        assert fake.calls[0]["evidence_snapshot"]["composite"] is None
        assert fake.calls[0]["evidence_snapshot"]["label"] == "WEAKENING"
    finally:
        _teardown()


def test_save_thesis_review_non_nan_evidence_snapshot_unaffected():
    fake = _FakeThesisClient()
    _install(fake)
    try:
        db.save_thesis_review({
            "ticker": "AAPL", "status": "INTACT", "summary": "ok",
            "evidence_snapshot": {"composite": 72.0, "label": "INTACT"},
        })
        assert fake.calls[0]["evidence_snapshot"]["composite"] == 72.0
    finally:
        _teardown()
