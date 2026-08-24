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

    def __call__(self, positions, account_ids=None, all_accounts_ok=False):
        self.calls.append({"positions": positions, "account_ids": account_ids,
                           "all_accounts_ok": all_accounts_ok})
        return True


def _pos(ticker, units):
    return {"instrument": {"kind": "stock", "symbol": ticker}, "units": units}


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
    # Must not raise out of the lane.
    cr._run_broker(cr.datetime.now(cr._ET), force=True)
