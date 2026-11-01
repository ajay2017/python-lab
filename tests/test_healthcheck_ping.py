"""healthcheck_ping + its cron_runner._record_heartbeat wiring (no network)."""
import pytest

from stock_analyzer import healthcheck_ping as hp

KEY = "SECRETKEY123"


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _Now:
    def isoformat(self):
        return "2026-11-01T10:00:00-04:00"


@pytest.fixture
def post(monkeypatch):
    calls = []

    def fake(url, data=None, timeout=None):
        calls.append({"url": url, "data": data, "timeout": timeout})
        return _Resp(200)
    monkeypatch.setattr(hp.requests, "post", fake)
    monkeypatch.setenv(hp.ENV_KEY, KEY)
    return calls


def test_ok_url():
    assert hp.ping_url(KEY, "scan", "ok") == f"https://hc-ping.com/{KEY}/drishta-scan?create=1"


def test_failed_url():
    assert hp.ping_url(KEY, "scan", "failed") == f"https://hc-ping.com/{KEY}/drishta-scan/log?create=1"


@pytest.mark.parametrize("status", ["weird", None, "", "OK"])
def test_unknown_status_is_never_success(status):
    assert hp.ping_url(KEY, "eod", status).endswith("/drishta-eod/log?create=1")


@pytest.mark.parametrize("key", [None, "", "   "])
def test_blank_key(key, monkeypatch, post):
    assert hp.ping_url(key, "scan", "ok") is None
    if key is None:
        monkeypatch.delenv(hp.ENV_KEY, raising=False)
    else:
        monkeypatch.setenv(hp.ENV_KEY, key)
    assert hp.ping("scan", "ok") == "off"
    assert post == []


def test_key_whitespace_stripped():
    assert hp.ping_url("  abc \n", "scan", "ok") == "https://hc-ping.com/abc/drishta-scan?create=1"


@pytest.mark.parametrize("lane", ["Scan Lane", "../x", "", "   ", None])
def test_bad_lane(lane, post):
    assert hp.ping_url(KEY, lane, "ok") is None
    assert hp.ping(lane, "ok") == "off"
    assert post == []


def test_lane_normalised():
    assert hp.ping_url(KEY, " Scan ", "ok") == f"https://hc-ping.com/{KEY}/drishta-scan?create=1"


@pytest.mark.parametrize("lane", sorted(hp.SCHEDULED_LANES))
def test_scheduled_lanes_auto_create(lane):
    assert hp.ping_url(KEY, lane, "ok").endswith(f"/drishta-{lane}?create=1")
    assert hp.ping_url(KEY, lane, "failed").endswith(f"/drishta-{lane}/log?create=1")


@pytest.mark.parametrize("lane", ["debrief", "monthly", "test"])
def test_unscheduled_lane_never_creates_a_check(lane):
    assert hp.ping_url(KEY, lane, "ok") == f"https://hc-ping.com/{KEY}/drishta-{lane}"
    assert hp.ping_url(KEY, lane, "failed") == f"https://hc-ping.com/{KEY}/drishta-{lane}/log"


def test_scheduled_lanes_match_cron_dispatch():
    import cron_runner as cr
    import inspect
    src = inspect.getsource(cr.main)
    for lane in hp.SCHEDULED_LANES:
        assert f'"{lane}"' in src, lane


def test_body_redacts_key_and_url_secrets(post):
    hp.ping("scan", "failed", f"boom https://x/{KEY}/y?apikey=abc&token=def")
    body = post[0]["data"].decode("utf-8")
    assert KEY not in body and "abc" not in body and "def" not in body
    assert "[redacted]" in body


def test_post_args_and_truncation(post):
    detail = "é" * (hp.BODY_MAX_CHARS + 50)
    assert hp.ping("scan", "failed", detail, timeout=2.5) == "sent"
    c = post[0]
    assert c["timeout"] == 2.5
    assert c["data"] == ("é" * hp.BODY_MAX_CHARS).encode("utf-8")
    assert c["url"].endswith("/log?create=1")


def test_default_timeout_and_empty_body(post):
    hp.ping("scan", "ok")
    assert post[0]["timeout"] == hp.DEFAULT_TIMEOUT_S
    assert post[0]["data"] == b""


@pytest.mark.parametrize("code,want", [(200, "sent"), (204, "sent"), (299, "sent"),
                                       (300, "failed"), (500, "failed")])
def test_status_mapping(code, want, monkeypatch):
    monkeypatch.setenv(hp.ENV_KEY, KEY)
    monkeypatch.setattr(hp.requests, "post", lambda *a, **k: _Resp(code))
    assert hp.ping("scan", "ok") == want


def test_post_raising_is_failed(monkeypatch):
    monkeypatch.setenv(hp.ENV_KEY, KEY)

    def boom(*a, **k):
        raise RuntimeError(f"connect error {KEY}")
    monkeypatch.setattr(hp.requests, "post", boom)
    assert hp.ping("scan", "ok") == "failed"


def test_key_never_in_output(monkeypatch, capsys):
    monkeypatch.setenv(hp.ENV_KEY, KEY)
    results = []
    monkeypatch.setattr(hp.requests, "post", lambda *a, **k: _Resp(200))
    results.append(hp.ping("scan", "ok"))
    monkeypatch.setattr(hp.requests, "post", lambda *a, **k: _Resp(500))
    results.append(hp.ping("scan", "failed", "d"))

    def boom(*a, **k):
        raise RuntimeError(KEY)
    monkeypatch.setattr(hp.requests, "post", boom)
    results.append(hp.ping("scan", "ok"))
    results.append(hp.ping("bad lane", "ok"))
    out = capsys.readouterr()
    assert KEY not in out.out + out.err
    assert all(KEY not in r for r in results)


# ---- cron_runner wiring ----

def test_heartbeat_pings_before_db(monkeypatch, capsys):
    import cron_runner as cr
    order = []
    monkeypatch.setenv(hp.ENV_KEY, KEY)
    monkeypatch.setattr(
        cr.healthcheck_ping, "ping",
        lambda lane, status, detail=None, **k: order.append(("ping", lane, status, detail)) or "sent")
    monkeypatch.setattr(cr.db, "save_cron_heartbeat",
                        lambda *a, **k: order.append(("db",)) or True)
    cr._record_heartbeat("scan", _Now(), status="failed", detail="boom")
    assert order == [("ping", "scan", "failed", "boom"), ("db",)]
    assert "healthcheck scan=failed ping sent" in capsys.readouterr().out


def test_heartbeat_ping_raise_does_not_block_db(monkeypatch, capsys):
    import cron_runner as cr
    saved = []

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(cr.healthcheck_ping, "ping", boom)
    monkeypatch.setattr(cr.db, "save_cron_heartbeat", lambda *a, **k: saved.append(1) or True)
    cr._record_heartbeat("scan", _Now())
    assert saved == [1]
    assert "healthcheck scan: UNCAUGHT" in capsys.readouterr().out


def test_heartbeat_env_unset_no_healthcheck_log(monkeypatch, capsys):
    import cron_runner as cr
    monkeypatch.delenv(hp.ENV_KEY, raising=False)
    monkeypatch.setattr(cr.db, "save_cron_heartbeat", lambda *a, **k: True)
    cr._record_heartbeat("scan", _Now())
    assert "healthcheck" not in capsys.readouterr().out
