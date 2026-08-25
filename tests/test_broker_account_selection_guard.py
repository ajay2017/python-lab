"""The `broker` cron's account-selection guard for balance/transaction sync.

2026-08-24 review finding: the existing position-snapshot write already
refuses to persist when any linked account's read failed (see
test_broker_position_snapshot.py's `test_partial_read_failure_writes_NOTHING`)
— but balance sync and transaction-history sync did NOT share that guard.
Account selection picks whichever account has the most CONFIRMED positions
among those that responded (`_best_id`/`_best_count`); if the real, heavy
brokerage account times out while an empty auxiliary (credit card / crypto /
IRA) responds with 0 positions, `_best_id` becomes that empty auxiliary and
BOTH the balance sync (`db.save_account_cash`) and the transaction sync
(`snaptrade_client.get_account_activities`) would run against the WRONG
account — silently overwriting `account_cash` (feeds `_leverage_cache` / the
margin-awareness surface) with the auxiliary's balance.

The new guard: skip balance/transaction sync (and notify) whenever every
account that responded shows 0 positions AND at least one account's read
failed — the exact ambiguous case above. It must NOT fire when a real
account's positions were confirmed (the legitimate main-account-selected
case) or when every account genuinely responded with 0 positions (a real
all-cash book, which should still sync).
"""
import cron_runner as cr


def _pos(ticker, units):
    return {"instrument": {"kind": "stock", "symbol": ticker}, "units": units}


_ACCOUNTS = [{"id": "cc", "name": "Credit Card"},
             {"id": "crypto", "name": "Crypto"},
             {"id": "ira", "name": "IRA"},
             {"id": "individual", "name": "Individual"}]


def _run_lane(monkeypatch, positions_by_id):
    """Drive `_run_broker` far enough to exercise the balance/transaction
    sync guard. Returns a dict of call recorders."""
    calls = {"balance": [], "activities": []}
    monkeypatch.setattr(cr.snaptrade_client, "has_snaptrade", lambda: True)
    monkeypatch.setattr(cr.snaptrade_client, "list_accounts", lambda: _ACCOUNTS)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_positions",
                        lambda aid: positions_by_id.get(aid))
    monkeypatch.setattr(cr.db, "save_broker_position_snapshot", lambda *_a, **_k: True)

    def _get_balance(aid):
        calls["balance"].append(aid)
        return {"cash": 1000.0}

    def _get_activities(aid, lookback_days):
        calls["activities"].append(aid)
        return []

    monkeypatch.setattr(cr.snaptrade_client, "get_account_balance", _get_balance)
    monkeypatch.setattr(cr.snaptrade_client, "get_account_activities", _get_activities)
    monkeypatch.setattr(cr.broker_sync, "map_balances_to_cash",
                        lambda raw: {"cash_balance": 1000.0, "note": "test"})
    monkeypatch.setattr(cr.db, "save_account_cash", lambda *_a, **_k: True)
    monkeypatch.setattr(cr.db, "load_trades", lambda: None)
    monkeypatch.setattr(cr.db, "save_account_flows", lambda *_a, **_k: 0)
    monkeypatch.setattr(cr.db, "save_snaptrade_pending_imports", lambda *_a, **_k: 0)
    monkeypatch.setattr(cr.db, "save_snaptrade_income_events", lambda *_a, **_k: 0)
    monkeypatch.setattr(cr, "_db_unavailable_detail", lambda: None)
    notified = []
    monkeypatch.setattr(cr, "_notify_broker_failure",
                        lambda *_a, **_k: notified.append(_a))
    cr._run_broker(cr.datetime.now(cr._ET), force=True)
    calls["notified"] = notified
    return calls


def test_heavy_account_timeout_with_empty_auxiliaries_skips_balance_and_transaction_sync(monkeypatch):
    """THE dangerous case this guard exists for. Every account that
    responded shows 0 positions; the one account that could have real
    holdings timed out. Syncing against the empty auxiliary would silently
    overwrite account_cash with the wrong balance."""
    calls = _run_lane(monkeypatch, {
        "cc": [], "crypto": [], "ira": [],
        "individual": None,  # timed out — the heavy account
    })
    assert calls["balance"] == [], "must not sync balance against an ambiguous selection"
    assert calls["activities"] == [], "must not sync transactions against an ambiguous selection"
    assert len(calls["notified"]) == 1


def test_real_positions_confirmed_still_syncs_even_if_another_account_timed_out(monkeypatch):
    """The legitimate case: the main account WAS read successfully and has
    real positions, even though an unrelated auxiliary account failed. This
    must not be treated as ambiguous."""
    calls = _run_lane(monkeypatch, {
        "cc": None,  # some other account failed — irrelevant, real positions found
        "crypto": [], "ira": [],
        "individual": [_pos("DELL", 20.0)],
    })
    assert calls["balance"] == ["individual"]
    assert calls["activities"] == ["individual"]
    assert calls["notified"] == []


def test_legitimate_all_cash_account_still_syncs(monkeypatch):
    """Every account responded successfully and all show 0 positions — a
    real all-cash book. This is NOT the ambiguous case (no read failed) and
    must still sync."""
    calls = _run_lane(monkeypatch, {
        "cc": [], "crypto": [], "ira": [], "individual": [],
    })
    assert calls["balance"] == ["cc"]
    assert calls["activities"] == ["cc"]
    assert calls["notified"] == []
