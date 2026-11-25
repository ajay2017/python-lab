"""EWSiblings (1): earnings-date parses must tolerate a timestamped provider string.

A provider can hand back "2026-12-02 16:00:00"; a bare
``strptime(X, "%Y-%m-%d")`` raises on it and the earnings info silently drops
out of that surface. The parse is ``str(X)[:10]``, matching
``trim_sizing.days_to_earnings``.
"""
import re
from datetime import date
from pathlib import Path

import pytest

from stock_analyzer import watchlist_advisor

_APP_PY = Path(__file__).resolve().parents[1] / "app.py"

# The earnings variables parsed at the app.py sites fixed for EWSiblings (1).
_EARN_VARS = ("earn", "_earn_str", "_gc_earn_str", "_sm_ed", "_d")


def test_earn_days_until_accepts_timestamp(monkeypatch):
    monkeypatch.setattr(watchlist_advisor, "_today_et", lambda: date(2026, 11, 24))
    plain = watchlist_advisor._earn_days_until("2026-12-02")
    assert plain == 8
    assert watchlist_advisor._earn_days_until("2026-12-02 16:00:00") == plain
    assert watchlist_advisor._earn_days_until("2026-12-02T16:00:00") == plain


@pytest.mark.parametrize("bad", [None, "", "garbage", "12/02/2026"])
def test_earn_days_until_none_for_missing_or_garbage(monkeypatch, bad):
    monkeypatch.setattr(watchlist_advisor, "_today_et", lambda: date(2026, 11, 24))
    assert watchlist_advisor._earn_days_until(bad) is None


def test_app_earnings_strptime_sites_take_first_ten_chars():
    src = _APP_PY.read_text(encoding="utf-8-sig")
    bare = re.compile(
        r"strptime\(\s*(" + "|".join(re.escape(v) for v in _EARN_VARS) + r")\s*,"
    )
    offenders = [
        f"app.py:{src.count(chr(10), 0, m.start()) + 1}: strptime({m.group(1)}, ...)"
        for m in bare.finditer(src)
    ]
    assert not offenders, (
        "earnings date parsed without [:10] (a timestamped string would raise): "
        + "; ".join(offenders)
    )
    # And the fixed sites are really there (guards against the test going vacuous).
    fixed = re.findall(
        r"strptime\(str\((" + "|".join(re.escape(v) for v in _EARN_VARS) + r")\)\[:10\]",
        src,
    )
    assert len(fixed) >= 7
