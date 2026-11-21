"""The `broker` cron's position-snapshot write, and its one hard invariant.

THE INVARIANT: a snapshot is written ONLY when EVERY linked account responded.

Why it is this strict rather than "at least one responded" — which is what an
earlier version of the code assumed, and what a reviewer caught. The real
account topology is one heavy brokerage account plus several empty auxiliaries
(credit card / crypto / IRA / managed). The heavy one is the slowest read and
so the likeliest to time out. If ONLY it fails, the empty auxiliaries still
satisfy the lane's account-selection guard, the aggregate is `{}`, and upserting
that over the last good row tells 🏠 Home the broker holds NOTHING — every real
holding renders as fabricated `app_only` drift ("overstated by ~$24,503") on a
perfectly correct book, with the known-good snapshot destroyed.

Skipping is the safe direction: the prior snapshot ages into Home's dated
"no mismatch as of <date> — not re-checked since", which is visible and never
fabricated.
"""
import cron_runner as cr


class _Recorder:
    """Captures save_broker_position_snapshot calls without touching a DB."""

    def __init__(self):
        self.calls = []

    def __call__(self, positions, account_ids=None, all_accounts_ok=False, kinds=None):
        self.calls.append({"positions": positions, "account_ids": account_ids,
                           "all_accounts_ok": all_accounts_ok, "kinds": kinds})
        return True


def _pos(ticker, units, kind="stock"):
    return {"instrument": {"kind": kind, "symbol": ticker}, "units": units}


def _run_lane(monkeypatch, accounts, positions_by_id):
    """Drive `_run_broker` far enough to exercise the snapshot block."""
    rec = _Recorder()
    monkeypatch.setattr(cr.snaptrade_client, "has_snaptrade", lambda: True)
    monkeypatch.setattr(cr.snaptrade_client, "list_accounts", lambda: accounts)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_positions",
                        lambda aid: positions_by_id.get(aid))
    # Neutralise the sub-jobs that follow; only the snapshot block is under test.
    monkeypatch.setattr(cr.snaptrade_client, "get_account_balance", lambda _a: None)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_activities",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_broker_position_snapshot", rec)
    monkeypatch.setattr(cr.db, "save_broker_connection_status", lambda *_a, **_k: False)
    monkeypatch.setattr(cr.db, "save_account_cash", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: None)
    monkeypatch.setattr(cr, "_notify_broker_failure", lambda *_a, **_k: None)
    cr._run_broker(cr.datetime.now(cr._ET), force=True)
    return rec


_ACCOUNTS = [{"id": "cc", "name": "Credit Card"},
             {"id": "crypto", "name": "Crypto"},
             {"id": "ira", "name": "IRA"},
             {"id": "individual", "name": "Individual"}]


def test_partial_read_failure_writes_NOTHING(monkeypatch):
    """THE load-bearing case. Only the heavy account fails; the empty
    auxiliaries respond. Writing `{}` here would fabricate drift across the
    entire book and destroy the last known-good snapshot."""
    rec = _run_lane(monkeypatch, _ACCOUNTS, {
        "cc": [], "crypto": [], "ira": [],
        "individual": None,          # timed out
    })
    assert rec.calls == [], "a partial read must never persist a snapshot"


def test_total_read_failure_writes_nothing(monkeypatch):
    rec = _run_lane(monkeypatch, _ACCOUNTS, {
        "cc": None, "crypto": None, "ira": None, "individual": None,
    })
    assert rec.calls == []


def test_all_accounts_responding_writes_the_aggregate(monkeypatch):
    rec = _run_lane(monkeypatch, _ACCOUNTS, {
        "cc": [], "crypto": [], "ira": [],
        "individual": [_pos("DELL", 20.0), _pos("NVDA", 5.0)],
    })
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["positions"] == {"DELL": 20.0, "NVDA": 5.0}
    assert call["all_accounts_ok"] is True
    assert call["account_ids"] == ["cc", "crypto", "individual", "ira"]


def test_a_ticker_held_in_two_accounts_is_summed_not_overwritten(monkeypatch):
    rec = _run_lane(monkeypatch, _ACCOUNTS, {
        "cc": [], "crypto": [], "ira": [_pos("AAA", 4.0)],
        "individual": [_pos("AAA", 6.0)],
    })
    assert rec.calls[0]["positions"] == {"AAA": 10.0}


def test_a_legitimately_all_cash_book_IS_representable_as_empty(monkeypatch):
    """Distinct from a failed read: every account responded and holds nothing.
    That is a real result and must be written, or Home could never learn the
    user has sold everything."""
    rec = _run_lane(monkeypatch, _ACCOUNTS, {
        "cc": [], "crypto": [], "ira": [], "individual": [],
    })
    assert len(rec.calls) == 1
    assert rec.calls[0]["positions"] == {}
    assert rec.calls[0]["all_accounts_ok"] is True


def test_non_equity_positions_are_excluded_from_the_snapshot(monkeypatch):
    rec = _run_lane(monkeypatch, _ACCOUNTS, {
        "cc": [], "crypto": [{"instrument": {"kind": "crypto", "symbol": "BTC"},
                              "units": 1.0}],
        "ira": [], "individual": [_pos("DELL", 20.0)],
    })
    assert rec.calls[0]["positions"] == {"DELL": 20.0}


def test_kinds_map_computed_alongside_positions(monkeypatch):
    """Phase 1 ETF support (F-279 §11): the broker ground-truth kind map is
    computed from the SAME payloads normalize_positions already used, at
    zero extra SnapTrade cost, and passed alongside positions."""
    rec = _run_lane(monkeypatch, _ACCOUNTS, {
        "cc": [], "crypto": [], "ira": [],
        "individual": [_pos("SPY", 10.0, kind="etf"), _pos("AAPL", 5.0, kind="stock")],
    })
    assert rec.calls[0]["kinds"] == {"SPY": "etf", "AAPL": "stock"}


def test_kinds_map_ticker_in_two_accounts_first_resolved_kind_wins(monkeypatch):
    rec = _run_lane(monkeypatch, _ACCOUNTS, {
        "cc": [], "crypto": [], "ira": [_pos("SPY", 4.0, kind="etf")],
        "individual": [_pos("SPY", 6.0, kind="etf")],
    })
    assert rec.calls[0]["kinds"] == {"SPY": "etf"}


def test_a_snapshot_write_failure_does_not_fail_the_lane(monkeypatch):
    """The snapshot is awareness-only. Failing the lane over it would suppress
    the balance and transaction sync, which are the load-bearing jobs."""
    monkeypatch.setattr(cr.snaptrade_client, "has_snaptrade", lambda: True)
    monkeypatch.setattr(cr.snaptrade_client, "list_accounts", lambda: _ACCOUNTS)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_positions",
                        lambda aid: [_pos("DELL", 20.0)])
    monkeypatch.setattr(cr.snaptrade_client, "get_account_balance", lambda _a: None)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_activities",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_account_cash", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: None)
    monkeypatch.setattr(cr, "_notify_broker_failure", lambda *_a, **_k: None)

    def _boom(*_a, **_k):
        raise RuntimeError("table does not exist")

    monkeypatch.setattr(cr.db, "save_broker_position_snapshot", _boom)
    monkeypatch.setattr(cr.db, "save_broker_connection_status", lambda *_a, **_k: False)
    # Must not raise out of the lane.
    cr._run_broker(cr.datetime.now(cr._ET), force=True)


# ── #34c C1: one broker_connection_status row per configured run ──────────────
# Capture only (docs/plans/broker-connection-alarm-34c.md). The row is bookkeeping:
# it must never change the lane's rc, raise, or be written when SnapTrade isn't set up.

_HEALTHY_ACCOUNTS = [
    {"id": "cc", "name": "Credit Card", "sync_status": {
        "holdings": {"holdings_unavailable": None,
                     "last_successful_sync": "2026-11-20T22:52:17.376190+00:00"},
        "transactions": {"last_successful_sync": "2026-11-19"}}},
    {"id": "individual", "name": "Individual", "sync_status": {
        "holdings": {"holdings_unavailable": None,
                     "last_successful_sync": "2026-11-20T22:52:17.376190+00:00"},
        "transactions": {"last_successful_sync": "2026-11-19"}}},
]
_HEALTHY_CONNECTIONS = [{"disabled": False, "data_freshness_mode": "realtime"}]
_REAL_RECORD = cr._record_broker_connection_status   # a driver call may swap it for a no-op


def _drive(monkeypatch, *, accounts=_HEALTHY_ACCOUNTS, positions_by_id=None,
           connections=_HEALTHY_CONNECTIONS, configured=True, save_result=True,
           snapshot_result=True, record=True):
    """Run `_run_broker`; returns (rc, status_rows, notified). `record=False` swaps
    the status helper for a no-op: the "no status row at all" baseline."""
    rows, notified = [], []
    positions_by_id = positions_by_id if positions_by_id is not None else {
        "cc": [], "individual": [_pos("DELL", 20.0)]}

    def _save(row):
        rows.append(row)
        if isinstance(save_result, Exception):
            raise save_result
        return save_result

    monkeypatch.setattr(cr.snaptrade_client, "has_snaptrade", lambda: configured)
    monkeypatch.setattr(cr.snaptrade_client, "list_accounts", lambda: accounts)
    monkeypatch.setattr(cr.snaptrade_client, "list_connections", lambda: connections)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_positions",
                        lambda aid: positions_by_id.get(aid))
    monkeypatch.setattr(cr.snaptrade_client, "get_account_balance", lambda _a: None)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_activities", lambda *_a, **_k: None)
    monkeypatch.setattr(cr.db, "save_broker_position_snapshot", lambda *_a, **_k: snapshot_result)
    monkeypatch.setattr(cr.db, "save_broker_connection_status", _save)
    monkeypatch.setattr(cr.db, "save_account_cash", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: None)
    monkeypatch.setattr(cr, "_notify_broker_failure", lambda *_a, **_k: notified.append(_a))
    monkeypatch.setattr(cr, "_record_broker_connection_status",
                        _REAL_RECORD if record else (lambda *_a, **_k: None))
    rc = cr._run_broker(cr.datetime.now(cr._ET), force=True)
    return rc, rows, notified


def test_status_row_saved_on_the_normal_path(monkeypatch, capsys):
    rc, rows, _ = _drive(monkeypatch)
    assert len(rows) == 1
    row = rows[0]
    assert row["accounts_ok"] is True and row["connections_ok"] is True
    assert row["n_accounts"] == 2 and row["n_connections"] == 1
    assert row["any_disabled"] is False and row["any_holdings_unavailable"] is False
    assert row["positions_read_ok"] is True and row["snapshot_saved"] is True
    assert row["positions_sig"] == cr.broker_connection.positions_sig({"DELL": 20.0})
    assert row["holdings_refreshed_oldest"] == "2026-11-20T22:52:17.376190+00:00"
    assert row["checked_at"] >= row["run_started_at"]
    assert "broker: connection status saved (" in capsys.readouterr().out


def test_status_row_on_the_account_selection_guard_path(monkeypatch):
    """NOT a plain partial read: the only account that answered is empty and the
    heavy one failed, so the lane's account-selection guard returns rc=1 (and
    emails). The row is still written, on that early exit."""
    rc, rows, notified = _drive(monkeypatch, positions_by_id={"cc": [], "individual": None})
    assert rc == 1 and len(notified) == 1
    assert len(rows) == 1
    assert rows[0]["snapshot_saved"] is False
    assert rows[0]["positions_read_ok"] is False
    assert rows[0]["positions_sig"] is None


def test_status_row_on_a_true_partial_read_leaves_the_lane_as_it_was(monkeypatch):
    """One account unreadable but the main one has positions: no snapshot is saved
    (partial-read invariant), no guard fires, and the row records that the read
    was partial without changing rc or the failure emails."""
    pos = {"cc": None, "individual": [_pos("DELL", 20.0)]}
    rc_base, _, notified_base = _drive(monkeypatch, positions_by_id=pos, record=False)
    rc, rows, notified = _drive(monkeypatch, positions_by_id=pos)
    assert rc == rc_base
    assert [n[1] for n in notified] == [n[1] for n in notified_base]
    assert len(rows) == 1
    assert rows[0]["positions_read_ok"] is False
    assert rows[0]["snapshot_saved"] is False
    assert rows[0]["positions_sig"] is None


def test_status_row_records_a_snapshot_that_failed_to_save(monkeypatch):
    rc, rows, _ = _drive(monkeypatch, snapshot_result=False)
    assert rows[0]["snapshot_saved"] is False
    assert rows[0]["positions_read_ok"] is True
    assert rows[0]["positions_sig"] is not None


def test_status_row_on_the_list_accounts_failed_path(monkeypatch):
    rc, rows, notified = _drive(monkeypatch, accounts=None)
    assert rc == 1 and len(notified) == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["accounts_ok"] is False and row["n_accounts"] is None
    assert row["any_holdings_unavailable"] is None
    assert row["connections_ok"] is True and row["any_disabled"] is False
    assert row["snapshot_saved"] is None            # never attempted, not "failed"
    assert row["positions_read_ok"] is None
    assert "could not list connected accounts" in row["error"]


def test_status_row_on_the_no_accounts_path(monkeypatch):
    rc, rows, _ = _drive(monkeypatch, accounts=[])
    assert rc == 0
    assert len(rows) == 1
    assert rows[0]["accounts_ok"] is True and rows[0]["n_accounts"] == 0
    assert rows[0]["snapshot_saved"] is None        # never attempted, not "failed"
    assert rows[0]["positions_read_ok"] is None


def test_status_row_on_the_every_read_failed_path(monkeypatch):
    rc, rows, notified = _drive(monkeypatch, positions_by_id={"cc": None, "individual": None})
    assert rc == 1 and len(notified) == 1
    assert len(rows) == 1
    assert rows[0]["positions_read_ok"] is False and rows[0]["snapshot_saved"] is False
    assert "positions read failed" in rows[0]["error"]


def test_status_row_when_connections_cannot_be_read_is_none_not_clean(monkeypatch):
    rc, rows, _ = _drive(monkeypatch, connections=None)
    assert rows[0]["connections_ok"] is False
    assert rows[0]["any_disabled"] is None


def test_a_disabled_connection_is_recorded_and_changes_nothing_else(monkeypatch):
    rc_ok, _, notified_ok = _drive(monkeypatch)
    rc, rows, notified = _drive(monkeypatch, connections=[{"disabled": True,
                                                           "disabled_date": "2026-11-10"}])
    assert rows[0]["any_disabled"] is True and rows[0]["disabled_date"] == "2026-11-10"
    # C1 records only: the lane's rc and failure emails are exactly what they were
    # (this driver's balance read is stubbed to fail, so both are the same non-clean values).
    assert rc == rc_ok
    assert [n[1] for n in notified] == [n[1] for n in notified_ok]


def test_a_failing_status_write_leaves_the_lane_rc_unchanged(monkeypatch, capsys):
    rc_ok, _, _ = _drive(monkeypatch)
    for result in (False, RuntimeError("table does not exist")):
        rc, rows, _ = _drive(monkeypatch, save_result=result)
        assert rc == rc_ok
        assert len(rows) == 1
    assert "broker: connection status NOT saved" in capsys.readouterr().out


def test_a_raising_list_connections_cannot_fail_the_lane(monkeypatch):
    def _boom():
        raise RuntimeError("sdk exploded")

    rc_ok, _, _ = _drive(monkeypatch)
    rows = []
    monkeypatch.setattr(cr.snaptrade_client, "list_connections", _boom)
    monkeypatch.setattr(cr.db, "save_broker_connection_status",
                        lambda row: rows.append(row) or True)
    rc = cr._run_broker(cr.datetime.now(cr._ET), force=True)
    assert rc == rc_ok
    assert rows == []   # nothing built, nothing written, and nothing raised


def test_not_configured_writes_no_status_row(monkeypatch):
    rc, rows, _ = _drive(monkeypatch, configured=False)
    assert rc == 0
    assert rows == []
