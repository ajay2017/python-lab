"""cron_runner._run_thesis evidence-snapshot capture (Chunk B).

Verifies the save loop attaches a non-None evidence_snapshot to each `rec`
when a snapshot was built for that ticker, that composite/erosion feed
through correctly per-ticker, that pt_signal/regime are deliberately None
in this cron lane (documented scope decision -- Chunk B, not a gap), and
that a missing/failed erosion lookup for ONE ticker never affects another
ticker's own snapshot (independent failure, per
db.load_thesis_erosion_cache_batch's own never-raises/returns-{}-on-failure
contract).
"""
import pandas as pd
import pytest

pytestmark = pytest.mark.fast


def _trades_df(tickers):
    return pd.DataFrame([
        {"ticker": t, "action": "BUY", "user_thesis": f"{t} thesis text",
         "traded_at": "2026-09-01T10:00:00+00:00"}
        for t in tickers
    ])


def _held_data(tickers, totals=None):
    totals = totals or {}
    return {
        t: {"history": pd.DataFrame(), "info": {}, "headlines": [],
            "total": totals.get(t)}
        for t in tickers
    }


def _setup(monkeypatch, cr, tickers, totals, erosion_batch):
    import stock_analyzer.headless_alert_engine as hae

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(
        hae, "_build_context",
        lambda today: {"ok": True, "held_data": _held_data(tickers, totals), "errors": []},
    )
    monkeypatch.setattr(cr.db, "load_trades", lambda: _trades_df(tickers))
    monkeypatch.setattr(cr.db, "load_thesis_reviews",
                        lambda: pd.DataFrame(columns=["ticker", "reviewed_at"]))
    monkeypatch.setattr(cr.db, "load_analyst_coverage", lambda **_kw: pd.DataFrame())
    monkeypatch.setattr(cr.db, "load_thesis_erosion_cache_batch",
                        lambda tickers, score_date: erosion_batch)


def test_evidence_snapshot_attached_to_saved_record(monkeypatch):
    import cron_runner as cr
    from stock_analyzer import thesis_advisor as ta

    now_et = cr.datetime.now(cr._ET).replace(hour=12, minute=0, second=0, microsecond=0)
    _setup(monkeypatch, cr, tickers=["AAPL"], totals={"AAPL": 72.0},
           erosion_batch={"AAPL": {"erosion_score": 40.0, "erosion_label": "Eroding"}})

    monkeypatch.setattr(ta, "run_batch_review", lambda positions, **_kw: [
        {"ticker": "AAPL", "trade_date": "2026-09-01", "status": "INTACT",
         "summary": "ok", "reviewed_at": now_et.isoformat(), "inputs_hash": "abc"}
    ])

    saved_records = []
    monkeypatch.setattr(cr.db, "save_thesis_review", lambda rec: saved_records.append(rec) or True)

    cr._run_thesis(now_et, force=True)

    assert len(saved_records) == 1
    snap = saved_records[0]["evidence_snapshot"]
    assert snap is not None
    assert snap["schema_v"] == 1
    assert snap["composite"] == 72.0
    assert snap["erosion_score"] == 40.0
    assert snap["erosion_label"] == "Eroding"
    assert snap["pt_signal"] is None       # deliberate cron-lane scope decision (Chunk B)
    assert snap["regime"] is None          # deliberate cron-lane scope decision (Chunk B)


def test_missing_erosion_for_one_ticker_does_not_affect_another(monkeypatch):
    """AAPL has no row in the batch erosion result (a miss -- not scored
    today); MSFT does. AAPL's snapshot must fall back to None fields without
    disturbing MSFT's own populated snapshot -- independent per-ticker
    failure, not a shared/contaminated lookup."""
    import cron_runner as cr
    from stock_analyzer import thesis_advisor as ta

    now_et = cr.datetime.now(cr._ET).replace(hour=12, minute=0, second=0, microsecond=0)
    _setup(monkeypatch, cr, tickers=["AAPL", "MSFT"], totals={"AAPL": 55.0, "MSFT": 61.0},
           erosion_batch={"MSFT": {"erosion_score": 30.0, "erosion_label": "Holding"}})

    monkeypatch.setattr(ta, "run_batch_review", lambda positions, **_kw: [
        {"ticker": p["ticker"], "trade_date": "2026-09-01", "status": "INTACT",
         "summary": "ok", "reviewed_at": now_et.isoformat(), "inputs_hash": "abc"}
        for p in positions
    ])

    saved_records = []
    monkeypatch.setattr(cr.db, "save_thesis_review", lambda rec: saved_records.append(rec) or True)

    cr._run_thesis(now_et, force=True)

    by_ticker = {r["ticker"]: r["evidence_snapshot"] for r in saved_records}
    assert by_ticker["AAPL"]["erosion_score"] is None
    assert by_ticker["AAPL"]["erosion_label"] is None
    assert by_ticker["AAPL"]["composite"] == 55.0        # unaffected by MSFT's presence
    assert by_ticker["MSFT"]["erosion_score"] == 30.0
    assert by_ticker["MSFT"]["erosion_label"] == "Holding"
    assert by_ticker["MSFT"]["composite"] == 61.0


def test_erosion_batch_lookup_failure_degrades_to_empty_not_raise(monkeypatch):
    """load_thesis_erosion_cache_batch's own contract: never raises, returns
    {} on any failure (offline/failed query). Confirms _run_thesis's
    snapshot build tolerates a totally empty batch result -- every
    candidate's erosion fields fall back to None, nothing crashes, the
    lane still completes and saves."""
    import cron_runner as cr
    from stock_analyzer import thesis_advisor as ta

    now_et = cr.datetime.now(cr._ET).replace(hour=12, minute=0, second=0, microsecond=0)
    _setup(monkeypatch, cr, tickers=["AAPL"], totals={"AAPL": 72.0}, erosion_batch={})

    monkeypatch.setattr(ta, "run_batch_review", lambda positions, **_kw: [
        {"ticker": "AAPL", "trade_date": "2026-09-01", "status": "INTACT",
         "summary": "ok", "reviewed_at": now_et.isoformat(), "inputs_hash": "abc"}
    ])
    saved_records = []
    monkeypatch.setattr(cr.db, "save_thesis_review", lambda rec: saved_records.append(rec) or True)

    rc = cr._run_thesis(now_et, force=True)

    assert rc == 0
    assert saved_records[0]["evidence_snapshot"]["erosion_score"] is None
    assert saved_records[0]["evidence_snapshot"]["composite"] == 72.0
