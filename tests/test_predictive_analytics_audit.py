"""
Tests for scripts/predictive_analytics_audit.py's pure pagination helper. The
audit exists to find truncated/mis-scoped reads, so its own read must refuse
to proceed on a row-count mismatch rather than silently analyse a partial
table. No DB/network: fake paged responses only.
"""
import pytest

import scripts.predictive_analytics_audit as audit

pytestmark = pytest.mark.fast


def _pager(total_rows, exact, page_size):
    data = [{"id": i} for i in range(total_rows)]
    calls = []

    def fetch(start, end):
        calls.append((start, end))
        return data[start:end + 1], exact

    return fetch, calls


def test_fetch_all_pages_three_pages_last_short():
    fetch, calls = _pager(total_rows=25, exact=25, page_size=10)
    rows = audit.fetch_all_pages(fetch, page_size=10)
    assert [r["id"] for r in rows] == list(range(25))
    assert calls == [(0, 9), (10, 19), (20, 29)]


def test_fetch_all_pages_exact_multiple_stops_at_count():
    fetch, calls = _pager(total_rows=20, exact=20, page_size=10)
    rows = audit.fetch_all_pages(fetch, page_size=10)
    assert len(rows) == 20
    assert calls == [(0, 9), (10, 19)]


def test_fetch_all_pages_count_mismatch_aborts():
    # Server says 30 rows exist, but the pages stop at 25 -> truncated read.
    fetch, _ = _pager(total_rows=25, exact=30, page_size=10)
    with pytest.raises(audit.PaginationError):
        audit.fetch_all_pages(fetch, page_size=10)


def test_fetch_all_pages_missing_count_aborts():
    fetch, _ = _pager(total_rows=5, exact=None, page_size=10)
    with pytest.raises(audit.PaginationError):
        audit.fetch_all_pages(fetch, page_size=10)


def test_fetch_all_pages_empty_table():
    fetch, _ = _pager(total_rows=0, exact=0, page_size=10)
    assert audit.fetch_all_pages(fetch, page_size=10) == []


def test_parse_content_range():
    assert audit._parse_content_range("0-999/1234") == 1234
    assert audit._parse_content_range("*/0") == 0
    assert audit._parse_content_range(None) is None
    assert audit._parse_content_range("0-9/*") is None
