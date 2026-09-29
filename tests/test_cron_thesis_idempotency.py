"""cron_runner._run_thesis idempotency guard (2026-09-29 fix).

Real production finding: the weekly thesis cron lane (Sunday-only, gated
purely on day-of-week with NO hour filter, unlike premarket/scan/intraday)
can fire more than once on the same Sunday with nothing to stop it. Live
data showed 24 tickers with a near-duplicate `thesis_reviews` row roughly
an hour apart, across 5 separate weeks — every extra firing silently paid
for a second Claude API call and wrote a second near-identical row per
open position with a thesis.

The fix: before building the batch-review list, `_run_thesis` now reads
`db.load_thesis_reviews()` once and drops any ticker already reviewed
today (`thesis_advisor.already_reviewed_today`, tested on its own pure
merits in test_thesis_advisor.py) — a duplicate lane firing degrades to
a no-op for whichever tickers were already handled by the first firing,
rather than reviewing (and paying for) them again.
"""
import pandas as pd
import pytest

pytestmark = pytest.mark.fast


def _trades_df(tickers):
    return pd.DataFrame([
        {
            "ticker": t, "action": "BUY", "user_thesis": f"{t} thesis text",
            "traded_at": "2026-09-01T10:00:00+00:00",
        }
        for t in tickers
    ])


def _held_data(tickers):
    return {t: {"history": pd.DataFrame(), "info": {}, "headlines": []} for t in tickers}


def _setup(monkeypatch, cr, tickers, already_reviewed_tickers, now_et):
    import stock_analyzer.headless_alert_engine as hae

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(
        hae, "_build_context",
        lambda today: {"ok": True, "held_data": _held_data(tickers), "errors": []},
    )
    monkeypatch.setattr(cr.db, "load_trades", lambda: _trades_df(tickers))
    monkeypatch.setattr(
        cr.db, "load_thesis_reviews",
        lambda: pd.DataFrame([
            {"ticker": t, "reviewed_at": now_et.isoformat()}
            for t in already_reviewed_tickers
        ]) if already_reviewed_tickers else pd.DataFrame(columns=["ticker", "reviewed_at"]),
    )
    monkeypatch.setattr(cr.db, "load_analyst_coverage", lambda **_kw: pd.DataFrame())


def test_already_reviewed_ticker_is_excluded_from_the_batch(monkeypatch):
    """The core fix: a ticker already reviewed today must never reach
    run_batch_review (and therefore never trigger a second LLM call)."""
    import cron_runner as cr
    from stock_analyzer import thesis_advisor as ta

    now_et = cr.datetime.now(cr._ET).replace(hour=12, minute=0, second=0, microsecond=0)
    _setup(monkeypatch, cr, tickers=["AAPL"], already_reviewed_tickers=["AAPL"], now_et=now_et)

    called = []
    monkeypatch.setattr(ta, "run_batch_review", lambda positions, **_kw: called.append(positions) or [])

    rc = cr._run_thesis(now_et, force=True)

    assert rc == 0
    assert called == [], "run_batch_review must never be called when every candidate was already reviewed today"


def test_a_not_yet_reviewed_ticker_still_gets_reviewed(monkeypatch):
    """Regression guard: the fix must not accidentally skip a genuinely
    new/unreviewed ticker."""
    import cron_runner as cr
    from stock_analyzer import thesis_advisor as ta

    now_et = cr.datetime.now(cr._ET).replace(hour=12, minute=0, second=0, microsecond=0)
    _setup(monkeypatch, cr, tickers=["MSFT"], already_reviewed_tickers=[], now_et=now_et)

    called = []
    monkeypatch.setattr(ta, "run_batch_review", lambda positions, **_kw: called.append(positions) or [])
    monkeypatch.setattr(cr.db, "save_thesis_review", lambda rec: True)

    cr._run_thesis(now_et, force=True)

    assert len(called) == 1
    assert [p["ticker"] for p in called[0]] == ["MSFT"]


def test_a_mixed_batch_only_skips_the_already_reviewed_ticker(monkeypatch):
    """One ticker already reviewed today, one not -- only the genuinely new
    one should reach run_batch_review."""
    import cron_runner as cr
    from stock_analyzer import thesis_advisor as ta

    now_et = cr.datetime.now(cr._ET).replace(hour=12, minute=0, second=0, microsecond=0)
    _setup(monkeypatch, cr, tickers=["AAPL", "MSFT"],
           already_reviewed_tickers=["AAPL"], now_et=now_et)

    called = []
    monkeypatch.setattr(ta, "run_batch_review", lambda positions, **_kw: called.append(positions) or [])
    monkeypatch.setattr(cr.db, "save_thesis_review", lambda rec: True)

    cr._run_thesis(now_et, force=True)

    assert len(called) == 1
    assert [p["ticker"] for p in called[0]] == ["MSFT"]


def test_a_duplicate_lane_firing_is_a_complete_no_op(monkeypatch):
    """End-to-end simulation of the real bug: call _run_thesis twice in a
    row (as two duplicate cron firings would) with the SAME held/trades
    state, but let the second call see the first call's real write via a
    shared in-memory reviews store. The second call must not re-review
    anything the first call already handled."""
    import cron_runner as cr
    import stock_analyzer.headless_alert_engine as hae
    from stock_analyzer import thesis_advisor as ta

    now_et = cr.datetime.now(cr._ET).replace(hour=12, minute=0, second=0, microsecond=0)
    tickers = ["AAPL"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(
        hae, "_build_context",
        lambda today: {"ok": True, "held_data": _held_data(tickers), "errors": []},
    )
    monkeypatch.setattr(cr.db, "load_trades", lambda: _trades_df(tickers))
    monkeypatch.setattr(cr.db, "load_analyst_coverage", lambda **_kw: pd.DataFrame())

    written_reviews: list[dict] = []
    monkeypatch.setattr(cr.db, "load_thesis_reviews",
                        lambda: pd.DataFrame(written_reviews) if written_reviews
                        else pd.DataFrame(columns=["ticker", "reviewed_at"]))

    def _fake_save(rec):
        written_reviews.append({"ticker": rec["ticker"], "reviewed_at": rec["reviewed_at"]})
        return True

    monkeypatch.setattr(cr.db, "save_thesis_review", _fake_save)

    review_calls = []

    def _fake_run_batch_review(positions, **_kw):
        review_calls.append(len(positions))
        return [
            {"ticker": p["ticker"], "trade_date": "2026-09-01", "status": "INTACT",
             "summary": "ok", "reviewed_at": now_et.isoformat(), "inputs_hash": "abc"}
            for p in positions
        ]

    monkeypatch.setattr(ta, "run_batch_review", _fake_run_batch_review)

    rc1 = cr._run_thesis(now_et, force=True)
    rc2 = cr._run_thesis(now_et, force=True)  # simulated duplicate firing

    assert rc1 == 0 and rc2 == 0
    assert review_calls == [1], (
        "the first firing should review AAPL once; the second (duplicate) "
        "firing must see it already reviewed today and never call "
        "run_batch_review again"
    )
    assert len(written_reviews) == 1, "only one thesis_reviews row should ever be written"
