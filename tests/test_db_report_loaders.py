"""Strict (`_or_none`) vs lenient loaders for the saved periodic reports.

`regenerate_action` must tell "no row for this period" from "couldn't read the
rows" (docs/plans/acted-rule-and-report-periods.md, U14): the strict loaders
return None on a failed read, an empty frame on a genuine zero-row read, and the
lenient ones keep degrading to an empty frame for display callers.
"""
import pytest

from stock_analyzer import db


class _Chain:
    """A supabase-style query chain that returns `rows` or raises `exc`."""
    def __init__(self, rows=None, exc=None):
        self._rows, self._exc, self.table_name, self.limit_n = rows, exc, None, None

    def table(self, name):
        self.table_name = name
        return self

    def select(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return type("R", (), {"data": self._rows})()


_CASES = [
    ("load_weekly_debriefs_or_none", "load_weekly_debriefs", "weekly_debriefs", "week_ending"),
    ("load_monthly_reports_or_none", "load_monthly_reports", "monthly_reports", "period_end"),
]


@pytest.mark.parametrize("strict,lenient,table,col", _CASES)
def test_rows_come_back_and_the_limit_is_passed(monkeypatch, strict, lenient, table, col):
    chain = _Chain(rows=[{col: "2026-11-30", "email_sent": True}])
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: chain)
    df = getattr(db, strict)(limit=24)
    assert list(df[col]) == ["2026-11-30"] and chain.table_name == table and chain.limit_n == 24
    assert list(getattr(db, lenient)(limit=3)[col]) == ["2026-11-30"]


@pytest.mark.parametrize("strict,lenient,table,col", _CASES)
def test_a_genuine_zero_row_read_is_an_empty_frame_not_none(monkeypatch, strict, lenient, table, col):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _Chain(rows=[]))
    df = getattr(db, strict)()
    assert df is not None and df.empty and col in df.columns


@pytest.mark.parametrize("strict,lenient,table,col", _CASES)
def test_a_failed_read_is_none_strict_and_empty_lenient(monkeypatch, strict, lenient, table, col):
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _Chain(exc=RuntimeError("boom")))
    assert getattr(db, strict)() is None
    lenient_df = getattr(db, lenient)()
    assert lenient_df is not None and lenient_df.empty and col in lenient_df.columns


@pytest.mark.parametrize("strict,lenient,table,col", _CASES)
def test_no_credentials_is_none_strict_and_empty_lenient(monkeypatch, strict, lenient, table, col):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert getattr(db, strict)() is None
    assert getattr(db, lenient)().empty
