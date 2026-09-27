"""stock_analyzer/db.py — asset_type NULL-backfill + pre-DDL write resilience
across holdings / trades / recommendations (Phase 1 ETF support, F-279 §11).

Every loader must: (1) backfill a legacy row's missing/NULL asset_type to
"stock" (never "etf"); (2) keep returning None (the offline sentinel) on a
genuine read FAILURE, never confuse the two. Every saver must degrade
gracefully via the existing "optional column, drop and retry" mechanism when
asset_type doesn't exist in the DB yet (DDL not applied).
"""
import pandas as pd
import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast


# ── load_holdings_or_none ────────────────────────────────────────────────────

class _FakeHoldingsExec:
    def __init__(self, data): self.data = data


class _FakeHoldingsBuilder:
    def __init__(self, rows=None, raise_exc=None):
        self._rows = rows or []
        self._raise_exc = raise_exc

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self

    def execute(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeHoldingsExec(self._rows)


class _FakeHoldingsClient:
    def __init__(self, rows=None, raise_exc=None):
        self._rows, self._raise_exc = rows, raise_exc

    def table(self, _name):
        return _FakeHoldingsBuilder(self._rows, self._raise_exc)


def test_load_holdings_or_none_backfills_null_asset_type(monkeypatch):
    rows = [{"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0, "asset_type": None}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeHoldingsClient(rows=rows))
    out = db.load_holdings_or_none()
    assert out is not None
    assert list(out["Asset Type"]) == ["stock"]


def test_load_holdings_or_none_backfills_missing_asset_type_column(monkeypatch):
    """Pre-DDL shape: the column isn't even in the row dict."""
    rows = [{"ticker": "AAPL", "shares": 10.0, "avg_cost": 150.0}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeHoldingsClient(rows=rows))
    out = db.load_holdings_or_none()
    assert out is not None
    assert list(out["Asset Type"]) == ["stock"]


def test_load_holdings_or_none_preserves_real_etf_value(monkeypatch):
    rows = [{"ticker": "SPY", "shares": 5.0, "avg_cost": 400.0, "asset_type": "etf"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeHoldingsClient(rows=rows))
    out = db.load_holdings_or_none()
    assert list(out["Asset Type"]) == ["etf"]


def test_load_holdings_or_none_read_failure_still_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(
        db, "_client",
        lambda: _FakeHoldingsClient(raise_exc=RuntimeError("simulated outage")),
    )
    assert db.load_holdings_or_none() is None


# ── load_trades_or_none ──────────────────────────────────────────────────────

class _FakeTradesExec:
    def __init__(self, data): self.data = data


class _FakeTradesBuilder:
    def __init__(self, rows=None, raise_exc=None):
        self._rows, self._raise_exc = rows or [], raise_exc

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self

    def execute(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeTradesExec(self._rows)


class _FakeTradesClient:
    def __init__(self, rows=None, raise_exc=None):
        self._rows, self._raise_exc = rows, raise_exc

    def table(self, _name):
        return _FakeTradesBuilder(self._rows, self._raise_exc)


def test_load_trades_or_none_backfills_null_asset_type(monkeypatch):
    rows = [{"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 100.0,
             "traded_at": "2026-01-01T12:00:00+00:00", "asset_type": None}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeTradesClient(rows=rows))
    out = db.load_trades_or_none()
    assert out is not None
    assert list(out["asset_type"]) == ["stock"]


def test_load_trades_or_none_backfills_missing_asset_type_column(monkeypatch):
    rows = [{"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 100.0,
             "traded_at": "2026-01-01T12:00:00+00:00"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeTradesClient(rows=rows))
    out = db.load_trades_or_none()
    assert out is not None
    assert list(out["asset_type"]) == ["stock"]


def test_load_trades_or_none_preserves_real_etf_value(monkeypatch):
    rows = [{"ticker": "SPY", "action": "BUY", "shares": 1.0, "price": 400.0,
             "traded_at": "2026-01-01T12:00:00+00:00", "asset_type": "etf"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeTradesClient(rows=rows))
    out = db.load_trades_or_none()
    assert list(out["asset_type"]) == ["etf"]


def test_load_trades_or_none_read_failure_still_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(
        db, "_client",
        lambda: _FakeTradesClient(raise_exc=RuntimeError("simulated outage")),
    )
    assert db.load_trades_or_none() is None


# ── load_recommendations_or_none ─────────────────────────────────────────────

class _FakeRecsQuery:
    def __init__(self, rows=None, raise_exc=None):
        self._rows, self._raise_exc = rows or [], raise_exc
        self._range = None

    def select(self, *_a, **_k): return self
    def gte(self, *_a, **_k): return self
    def lte(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._range is not None:
            start, end = self._range
            return _FakeTradesExec(self._rows[start:end + 1])
        return _FakeTradesExec(self._rows)


class _FakeRecsClient:
    def __init__(self, rows=None, raise_exc=None):
        self._rows, self._raise_exc = rows, raise_exc

    def table(self, _name):
        return _FakeRecsQuery(self._rows, self._raise_exc)


def test_load_recommendations_or_none_backfills_null_asset_type(monkeypatch):
    rows = [{"ticker": "AAPL", "rec_date": "2026-08-01", "rec_type": "new_pick",
             "asset_type": None}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeRecsClient(rows=rows))
    out = db.load_recommendations_or_none()
    assert out is not None
    assert list(out["asset_type"]) == ["stock"]


def test_load_recommendations_or_none_backfills_missing_asset_type_column(monkeypatch):
    rows = [{"ticker": "AAPL", "rec_date": "2026-08-01", "rec_type": "new_pick"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeRecsClient(rows=rows))
    out = db.load_recommendations_or_none()
    assert out is not None
    assert list(out["asset_type"]) == ["stock"]


def test_load_recommendations_or_none_preserves_real_etf_value(monkeypatch):
    rows = [{"ticker": "SPY", "rec_date": "2026-08-01", "rec_type": "new_pick",
             "asset_type": "etf"}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeRecsClient(rows=rows))
    out = db.load_recommendations_or_none()
    assert list(out["asset_type"]) == ["etf"]


def test_load_recommendations_or_none_read_failure_still_returns_none(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(
        db, "_client",
        lambda: _FakeRecsClient(raise_exc=RuntimeError("simulated outage")),
    )
    assert db.load_recommendations_or_none() is None


def test_load_recommendations_or_none_genuine_empty_result_still_has_asset_type_col(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeRecsClient(rows=[]))
    out = db.load_recommendations_or_none()
    assert out is not None
    assert out.empty
    assert "asset_type" in out.columns


# ── save_trade: asset_type optional-column drop-and-retry ──────────────────

class _FakeSaveTradeBuilder:
    def __init__(self, calls, raise_exc=None, raise_on_call=1):
        self._calls, self._raise_exc, self._raise_on_call = calls, raise_exc, raise_on_call

    def insert(self, record):
        self._calls.append(dict(record))
        return self

    def execute(self):
        if self._raise_exc is not None and len(self._calls) == self._raise_on_call:
            raise self._raise_exc
        return None


class _FakeSaveTradeClient:
    def __init__(self, raise_exc=None, raise_on_call=1):
        self.calls: list[dict] = []
        self._raise_exc, self._raise_on_call = raise_exc, raise_on_call

    def table(self, _name):
        return _FakeSaveTradeBuilder(self.calls, self._raise_exc, self._raise_on_call)


@pytest.fixture
def _patch_save_trade(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)


def test_save_trade_missing_asset_type_column_degrades_and_retries(_patch_save_trade, monkeypatch):
    exc = Exception("Could not find the 'asset_type' column of 'trades' in the schema cache")
    fake = _FakeSaveTradeClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_trade({"ticker": "SPY", "action": "BUY", "asset_type": "etf"})
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert len(fake.calls) == 2
    assert "asset_type" in fake.calls[0]
    assert "asset_type" not in fake.calls[1]


def test_save_trade_asset_type_present_on_normal_insert(_patch_save_trade, monkeypatch):
    fake = _FakeSaveTradeClient()
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        ok = db.save_trade({"ticker": "SPY", "action": "BUY", "asset_type": "etf"})
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    assert fake.calls[0]["asset_type"] == "etf"


# ── save_holdings: asset_type optional-column drop-and-retry ───────────────

class _FakeSaveHoldingsBuilder:
    def __init__(self, calls, raise_exc=None, raise_on_call=1, kind="upsert"):
        self._calls, self._raise_exc, self._raise_on_call, self._kind = (
            calls, raise_exc, raise_on_call, kind
        )

    def upsert(self, records, on_conflict=None):
        self._calls.append(("upsert", list(records)))
        return self

    def delete(self):
        return self

    @property
    def not_(self):
        return self

    def neq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def execute(self):
        call_n = sum(1 for c in self._calls if c[0] == "upsert")
        if self._raise_exc is not None and call_n == self._raise_on_call:
            raise self._raise_exc
        return None


class _FakeSaveHoldingsClient:
    def __init__(self, raise_exc=None, raise_on_call=1):
        self.calls: list = []
        self._raise_exc, self._raise_on_call = raise_exc, raise_on_call

    def table(self, name):
        if name == "holdings":
            return _FakeSaveHoldingsBuilder(self.calls, self._raise_exc, self._raise_on_call)
        # manual_stops sweep — harmless no-op builder
        class _Noop:
            def delete(self_inner): return self_inner
            @property
            def not_(self_inner): return self_inner
            def neq(self_inner, *a, **k): return self_inner
            def in_(self_inner, *a, **k): return self_inner
            def execute(self_inner): return None
        return _Noop()


@pytest.fixture
def _patch_save_holdings(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)


def test_save_holdings_missing_asset_type_column_degrades_and_retries(
    _patch_save_holdings, monkeypatch,
):
    exc = Exception("Could not find the 'asset_type' column of 'holdings' in the schema cache")
    fake = _FakeSaveHoldingsClient(raise_exc=exc, raise_on_call=1)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    df = pd.DataFrame([{"Ticker": "SPY", "Shares": 5.0, "Avg Cost ($)": 400.0,
                         "Asset Type": "etf"}])
    try:
        ok = db.save_holdings(df)
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    upsert_calls = [c for c in fake.calls if c[0] == "upsert"]
    assert len(upsert_calls) == 2
    assert "asset_type" in upsert_calls[0][1][0]
    assert "asset_type" not in upsert_calls[1][1][0]


def test_save_holdings_normalizes_asset_type_on_write(_patch_save_holdings, monkeypatch):
    fake = _FakeSaveHoldingsClient()
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    df = pd.DataFrame([{"Ticker": "AAPL", "Shares": 5.0, "Avg Cost ($)": 100.0,
                         "Asset Type": None}])
    try:
        ok = db.save_holdings(df)
    finally:
        _db_mod._CLIENT = None
    assert ok is True
    upsert_calls = [c for c in fake.calls if c[0] == "upsert"]
    assert upsert_calls[0][1][0]["asset_type"] == "stock"


# ── save_recommendations: asset_type generation in the strip cascade ───────

class _FakeSaveRecsUpsertBuilder:
    def __init__(self, calls, raise_exc, raise_on_call):
        self._calls, self._raise_exc, self._raise_on_call = calls, raise_exc, raise_on_call

    def upsert(self, rows, on_conflict=None, ignore_duplicates=None):
        self._calls.append(list(rows))
        return self

    def execute(self):
        if self._raise_exc is not None and len(self._calls) == self._raise_on_call:
            raise self._raise_exc
        return None


class _FakeSaveRecsClient:
    def __init__(self, raise_exc=None, raise_on_call=1):
        self.calls: list = []
        self._raise_exc, self._raise_on_call = raise_exc, raise_on_call

    def table(self, _name):
        return _FakeSaveRecsUpsertBuilder(self.calls, self._raise_exc, self._raise_on_call)


def _rec_row(ticker="CRM"):
    return {
        "ticker": ticker, "rec_date": "2026-09-27", "rec_type": "new_pick",
        "price_at_surface": 250.0, "asset_type": "etf",
    }


def test_save_recommendations_missing_asset_type_column_degrades_and_retries(monkeypatch):
    exc = Exception(
        "Could not find the 'asset_type' column of 'recommendations' in the schema cache"
    )
    fake = _FakeSaveRecsClient(raise_exc=exc, raise_on_call=1)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        result = db.save_recommendations([_rec_row()])
    finally:
        _db_mod._CLIENT = None
    assert result["saved"] == 1
    assert result["error"] is None
    assert "asset_type" in fake.calls[0][0]
    assert "asset_type" not in fake.calls[1][0]


def test_save_recommendations_asset_type_missing_does_not_strip_other_generations(monkeypatch):
    """Targeted strip: an asset_type-missing error must not also discard the
    already-working pillar-score/weights_version columns."""
    exc = Exception(
        "Could not find the 'asset_type' column of 'recommendations' in the schema cache"
    )
    fake = _FakeSaveRecsClient(raise_exc=exc, raise_on_call=1)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    import stock_analyzer.db as _db_mod
    _db_mod._CLIENT = fake
    try:
        db.save_recommendations([_rec_row()])
    finally:
        _db_mod._CLIENT = None
    assert "weights_version" in fake.calls[1][0]
