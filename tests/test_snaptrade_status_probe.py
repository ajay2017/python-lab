"""scripts/snaptrade_status_probe.py with a faked SnapTrade client (no network)."""
import importlib.util
import pathlib
from datetime import timedelta
from types import SimpleNamespace

from stock_analyzer import snaptrade_client
from stock_analyzer.market_time import now_et

_P = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "snaptrade_status_probe.py"
_spec = importlib.util.spec_from_file_location("snaptrade_status_probe", _P)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

SECRET_ID, SECRET_KEY = "FAKE-CLIENT-ID-xyz", "FAKE-CONSUMER-KEY-abc"


def _resp(body):
    return SimpleNamespace(body=body)


def _fake(monkeypatch, auths, accounts):
    def _auths():
        if isinstance(auths, Exception):
            raise auths
        return _resp(auths)

    def _accts():
        if isinstance(accounts, Exception):
            raise accounts
        return _resp(accounts)

    client = SimpleNamespace(
        connections=SimpleNamespace(list_brokerage_authorizations=_auths),
        account_information=SimpleNamespace(list_user_accounts=_accts),
    )
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", SECRET_ID)
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", SECRET_KEY)
    monkeypatch.setattr(snaptrade_client, "has_snaptrade", lambda: True)
    monkeypatch.setattr(snaptrade_client, "_client", lambda: client)
    monkeypatch.setattr(snaptrade_client, "_call_with_timeout",
                        lambda fn, a, k, t: fn())


def _acct(auth_id="auth-1234567890", sync=True):
    ts = (now_et() - timedelta(hours=30)).isoformat()
    a = {"institution_name": "Robinhood", "name": "Individual",
         "number": "RH987654321", "brokerage_authorization": auth_id}
    if sync:
        a["sync_status"] = {
            "holdings": {"initial_sync_completed": True, "last_successful_sync": ts,
                         "holdings_unavailable": False},
            "transactions": {"initial_sync_completed": True, "last_successful_sync": ts},
        }
    return a


def _auth(disabled=False):
    return {"id": "auth-1234567890", "name": "ROBINHOOD", "type": "read",
            "brokerage": {"name": "Robinhood"}, "disabled": disabled,
            "disabled_date": "2026-10-20T00:00:00Z" if disabled else None,
            "data_freshness_mode": "realtime", "created_date": "2026-01-01"}


def test_masks_and_no_credentials(monkeypatch, capsys):
    _fake(monkeypatch, [_auth()], [_acct()])
    assert probe.main() == 0
    out = capsys.readouterr().out
    print(out)
    assert "auth-1234567890" not in out and "RH987654321" not in out
    assert "****7890" in out and "****4321" in out
    assert SECRET_ID not in out and SECRET_KEY not in out
    assert "(>24h)" in out and "data_freshness_mode=realtime" in out


def test_missing_sync_status_survives(monkeypatch, capsys):
    _fake(monkeypatch, [{"id": "x"}], [_acct(sync=False), {}])
    assert probe.main() == 0
    assert "holdings last synced unknown" in capsys.readouterr().out


def test_disabled_verdict(monkeypatch, capsys):
    _fake(monkeypatch, [_auth(disabled=True)], [_acct()])
    assert probe.main() == 0
    assert "DISABLED since 2026-10-20" in capsys.readouterr().out


def test_missing_creds_returns_2(monkeypatch, capsys):
    monkeypatch.setattr(snaptrade_client, "has_snaptrade", lambda: False)
    assert probe.main() == 2
    assert "credentials missing" in capsys.readouterr().out


def test_connections_raise_continues(monkeypatch, capsys):
    class Boom(Exception):
        status = 401
        body = {"detail": "nope"}
    _fake(monkeypatch, Boom("x"), [_acct()])
    assert probe.main() == 0
    out = capsys.readouterr().out
    assert "Boom status=401" in out and "Robinhood" in out


def test_error_body_echoing_credentials_is_scrubbed(monkeypatch, capsys):
    class Boom(Exception):
        status = 400
        body = {"detail": f"Invalid clientId provided - {SECRET_ID} / {SECRET_KEY}"}
    _fake(monkeypatch, Boom("x"), Boom("y"))
    assert probe.main() == 1
    out = capsys.readouterr().out
    assert SECRET_ID not in out and SECRET_KEY not in out
    assert "[redacted]" in out


def test_both_fail_returns_1(monkeypatch, capsys):
    _fake(monkeypatch, RuntimeError("a"), RuntimeError("b"))
    assert probe.main() == 1
