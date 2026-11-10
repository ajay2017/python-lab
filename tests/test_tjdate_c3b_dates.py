"""TJDate C3b: the thesis-review trade date is the BUY's ET date.

`thesis_reviews.trade_date` (written by 📒 Thesis Review in app.py and by the
weekly thesis lane in cron_runner.py) and the Red Team position age used the
UTC `str(traded_at)[:10]` slice, which dates a BUY filled or logged after
~19:00 ET (already the next day in UTC) one day late. Both now take
`trade_time.et_trade_date`, with ET today as the fallback when the timestamp
can't be read. app.py is not importable in tests, so its wiring is pinned by
source text; the date rule itself is pinned behaviourally below.
"""
import re
from datetime import date
from pathlib import Path

from stock_analyzer import trade_time

ROOT = Path(__file__).resolve().parents[1]
# Three forms: `.get("traded_at", ""))[:10]`, `["traded_at"])[:10]`, and a
# pandas `traded_at ... .str[:10]` on the column.
_UTC_SLICE = re.compile(
    r'traded_at["\']?\s*(?:,\s*["\']{2}\s*)?\)\s*\)\s*\[\s*:\s*10\s*\]'
    r'|\[\s*["\']traded_at["\']\s*\]\s*\)\s*\[\s*:\s*10\s*\]'
    r'|traded_at["\']?\s*\]?(?:\s*\.astype\(\s*str\s*\))?\s*\.str\s*\[\s*:\s*10\s*\]'
)


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_no_utc_traded_at_date_slice_left_in_app_or_cron():
    for name in ("app.py", "cron_runner.py"):
        hits = [m.group(0) for m in _UTC_SLICE.finditer(_src(name))]
        assert hits == [], f"{name} still slices traded_at to a UTC date: {hits}"


def test_thesis_review_sites_use_the_et_date_and_et_today():
    app = _src("app.py")
    assert '_bt_d = trade_time.et_trade_date(_brow.get("traded_at"))' in app
    assert app.count("_trade_date_by_ticker.get(_ticker) or _today_et().isoformat()") == 2
    assert "_trade_date_by_ticker.get(_ticker) or str(date.today())" not in app
    cron = _src("cron_runner.py")
    assert ('"trade_date":  (trade_time.et_trade_date(row.get("traded_at")) or today).isoformat()'
            in cron)


def test_evening_et_buy_keeps_its_et_date_in_both_dst_regimes():
    # 19:14 EST on Nov 4 is 00:14 UTC on Nov 5; the UTC slice read "2026-11-05".
    assert trade_time.et_trade_date("2026-11-05T00:14:00+00:00") == date(2026, 11, 4)
    # 20:30 EDT on Jul 1 is 00:30 UTC on Jul 2.
    assert trade_time.et_trade_date("2026-07-02T00:30:00+00:00") == date(2026, 7, 1)
    # A morning fill is the same date either way.
    assert trade_time.et_trade_date("2026-11-04T15:00:00+00:00") == date(2026, 11, 4)


def test_unreadable_timestamp_is_none_so_callers_fall_back():
    assert trade_time.et_trade_date(None) is None
    assert trade_time.et_trade_date("not a time") is None


# ── cron_runner._run_thesis, end to end (harness as test_cron_thesis_idempotency) ──

import pandas as pd
import pytest


def _run_thesis_positions(monkeypatch, traded_at):
    import cron_runner as cr
    import stock_analyzer.headless_alert_engine as hae
    from stock_analyzer import thesis_advisor as ta

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(hae, "_build_context", lambda today: {
        "ok": True, "errors": [],
        "held_data": {"AAPL": {"history": pd.DataFrame(), "info": {}, "headlines": []}}})
    monkeypatch.setattr(cr.db, "load_trades", lambda: pd.DataFrame([{
        "ticker": "AAPL", "action": "BUY", "user_thesis": "AAPL thesis text",
        "traded_at": traded_at}]))
    monkeypatch.setattr(cr.db, "load_thesis_reviews",
                        lambda: pd.DataFrame(columns=["ticker", "reviewed_at"]))
    monkeypatch.setattr(cr.db, "load_analyst_coverage", lambda **_kw: pd.DataFrame())
    monkeypatch.setattr(cr.db, "save_thesis_review", lambda rec: True)
    called = []
    monkeypatch.setattr(ta, "run_batch_review",
                        lambda positions, **_kw: called.append(positions) or [])
    # Sunday 2026-11-08 20:05 EST (the lane's slot) = Monday 01:05 UTC.
    now_et = cr._ET.localize(cr.datetime(2026, 11, 8, 20, 5))   # _ET is pytz
    cr._run_thesis(now_et, force=True)
    assert len(called) == 1
    return called[0]


@pytest.mark.parametrize("traded_at, want", [
    ("2026-11-05T00:14:00+00:00", "2026-11-04"),   # 19:14 EST: the UTC slice read 11-05
    ("2026-09-01T10:00:00+00:00", "2026-09-01"),
])
def test_cron_thesis_trade_date_is_the_buys_et_date(monkeypatch, traded_at, want):
    (pos,) = _run_thesis_positions(monkeypatch, traded_at)
    assert pos["trade_date"] == want


@pytest.mark.parametrize("traded_at", [None, "not a time"])
def test_cron_thesis_unreadable_time_falls_back_to_et_today(monkeypatch, traded_at):
    # The lane runs Sunday 20:05 ET, already Monday in UTC: the fallback is Sunday.
    (pos,) = _run_thesis_positions(monkeypatch, traded_at)
    assert pos["trade_date"] == "2026-11-08"
