"""Read-only provider endpoint probe: classification, no-key, quota hook, ET dates,
key redaction. All HTTP is an injected fake `get`."""
from datetime import date

import json

import pytest

from stock_analyzer.providers import endpoint_probe as ep

pytestmark = pytest.mark.fast

KEY = "SUPERSECRETKEY"


class _Resp:
    def __init__(self, status=200, text="[]"):
        self.status_code = status
        self.text = text


def _cls(provider, endpoint, status, body):
    return ep.classify_response(provider, endpoint, status, body, KEY)


def test_works_list_and_dict():
    assert _cls("fmp", "profile", 200, '[{"a":1},{"a":2}]') == (ep.OUT_WORKS, "2 items")
    assert _cls("finnhub", "stock/metric", 200, '{"a":1,"b":2}') == (ep.OUT_WORKS, "2 fields")


def test_no_data_cases():
    assert _cls("fmp", "profile", 200, "[]")[0] == ep.OUT_NO_DATA
    assert _cls("fmp", "profile", 200, "{}")[0] == ep.OUT_NO_DATA
    assert _cls("finnhub", "quote", 200, '{"c":0,"d":null}')[0] == ep.OUT_NO_DATA
    assert _cls("finnhub", "quote", 200, '{"c":190.5}')[0] == ep.OUT_WORKS


def test_not_on_plan_cases():
    assert _cls("fmp", "earnings", 402, "Payment Required")[0] == ep.OUT_NOT_ON_PLAN
    assert _cls("finnhub", "stock/metric", 403,
                '{"error":"You don\'t have access to this resource."}')[0] == ep.OUT_NOT_ON_PLAN
    assert _cls("fmp", "x", 401, "Premium subscription needed")[0] == ep.OUT_NOT_ON_PLAN
    assert _cls("fmp", "x", 200,
                '{"Error Message":"Special Endpoint : requires a premium plan"}'
                )[0] == ep.OUT_NOT_ON_PLAN
    assert _cls("fmp", "x", 200,
                '{"Error Message":"Legacy Endpoint, no longer supported"}'
                )[0] == ep.OUT_NOT_ON_PLAN


def test_auth_rate_nonjson_error():
    assert _cls("finnhub", "quote", 401, "Invalid API key")[0] == ep.OUT_AUTH
    assert _cls("fmp", "quote", 403, "")[0] == ep.OUT_AUTH
    assert _cls("finnhub", "quote", 429, "limit")[0] == ep.OUT_RATE
    assert _cls("finnhub", "stock/news-sentiment", 200, "")[0] == ep.OUT_NON_JSON
    out, det = _cls("finnhub", "stock/news-sentiment", 200, "<html>\n  nope   page " + "x" * 200)
    assert out == ep.OUT_NON_JSON and det.startswith("<html> nope page") and len(det) <= 60
    assert _cls("fmp", "x", 200, '{"Error Message":"Invalid symbol"}')[0] == ep.OUT_ERROR
    assert _cls("fmp", "x", 500, "boom")[0] == ep.OUT_ERROR


def test_details_never_contain_the_key():
    for status, body in ((402, f"pay apikey={KEY}"), (500, f"x {KEY} y"),
                         (200, f"<b>{KEY}</b>"),
                         (200, '{"Error Message":"premium %s"}' % KEY),
                         (403, f"access denied token={KEY}")):
        assert KEY not in _cls("fmp", "x", status, body)[1]


def test_exception_message_is_redacted():
    def _get(url, params=None, timeout=None):
        raise RuntimeError(f"conn failed for {url}?apikey={KEY} and {KEY}")
    rows = ep.run_probe({"finnhub": KEY, "fmp": KEY}, today=date(2026, 10, 27), get=_get)
    assert all(r["outcome"] == ep.OUT_ERROR for r in rows)
    assert all(KEY not in r["detail"] and r["detail"].startswith("RuntimeError") for r in rows)


def test_missing_key_makes_no_request():
    calls = []

    def _get(url, params=None, timeout=None):
        calls.append(url)
        return _Resp(200, "[1]")
    rows = ep.run_probe({"finnhub": None, "fmp": ""}, today=date(2026, 10, 27), get=_get)
    assert calls == []
    assert len(rows) == 18 and all(r["outcome"] == ep.OUT_NO_KEY for r in rows)


def test_on_fmp_call_counts_fmp_requests_only_and_one_request_each():
    calls, n = [], []

    def _get(url, params=None, timeout=None):
        calls.append((url, dict(params), timeout))
        return _Resp(200, '[{"a":1}]')
    rows = ep.run_probe({"finnhub": "FK", "fmp": "MK"}, today=date(2026, 10, 27),
                        get=_get, on_fmp_call=lambda: n.append(1))
    fmp_calls = [c for c in calls if "financialmodelingprep" in c[0]]
    assert len(calls) == 18 and len(fmp_calls) == 11 == len(n)
    assert all(c[2] for c in calls)                     # short timeout set
    assert all(c[1].get("apikey") == "MK" for c in fmp_calls)
    assert all(c[1].get("token") == "FK" for c in calls if "finnhub" in c[0])
    assert {r["provider"] for r in rows} == {"finnhub", "fmp"}


def test_dates_are_based_on_the_passed_today():
    seen = {}

    def _get(url, params=None, timeout=None):
        seen[url.rsplit("/", 1)[-1] if "news" not in url else url] = dict(params)
        return _Resp(200, "[1]")
    ep.run_probe({"finnhub": "FK", "fmp": None}, today=date(2026, 10, 27), get=_get)
    news = next(v for k, v in seen.items() if k.endswith("company-news"))
    assert news["from"] == "2026-10-20" and news["to"] == "2026-10-27"
    cal = seen["earnings"]
    assert cal["from"] == "2026-10-27" and cal["to"] == "2026-11-10"


def test_probe_counts_match_a_real_run():
    from datetime import date
    rows = ep.run_probe({"finnhub": "", "fmp": ""}, today=date(2026, 10, 27))
    counts = ep.probe_counts()
    assert counts["finnhub"] == sum(r["provider"] == "finnhub" for r in rows)
    assert counts["fmp"] == sum(r["provider"] == "fmp" for r in rows)


def test_key_straddling_the_truncation_point_never_leaks_a_prefix():
    body = "p" * 50 + KEY + " and more trailing text " * 5
    for status in (200, 500, 402, 403, 429):
        det = _cls("fmp", "x", status, body)[1]
        for n in range(4, len(KEY) + 1):
            assert KEY[:n] not in det
    det = _cls("fmp", "x", 200, '{"Error Message":"%s%s tail"}' % ("p" * 115, KEY))[1]
    assert all(KEY[:n] not in det for n in range(4, len(KEY) + 1))


def test_limit_reach_is_rate_limited_before_plan_words():
    txt = "Limit Reach . Please upgrade your plan to continue"
    assert _cls("fmp", "x", 200, '{"Error Message":"%s"}' % txt)[0] == ep.OUT_RATE
    assert _cls("fmp", "x", 402, txt)[0] == ep.OUT_RATE
    assert _cls("fmp", "x", 403, txt)[0] == ep.OUT_RATE


def test_finnhub_error_payload_is_not_works():
    assert _cls("finnhub", "stock/metric", 200,
                '{"error":"You do not have access to this resource."}')[0] == ep.OUT_NOT_ON_PLAN
    out, det = _cls("finnhub", "stock/metric", 200, '{"error":"Invalid symbol"}')
    assert out == ep.OUT_ERROR and "Invalid symbol" in det


def test_fmp_probe_allowed_boundaries():
    cap = ep.C.FMP_DAILY_SOFT_CAP
    assert ep.fmp_probe_allowed(None, 11)
    assert ep.fmp_probe_allowed(cap - 11, 11)
    assert not ep.fmp_probe_allowed(cap - 10, 11)


def test_run_probe_skips_fmp_over_cap_and_sends_nothing_to_fmp():
    calls, n = [], []

    def _get(url, params=None, timeout=None):
        calls.append(url)
        return _Resp(200, "[1]")
    rows = ep.run_probe({"finnhub": "FK", "fmp": "MK"}, today=date(2026, 10, 27),
                        get=_get, on_fmp_call=lambda: n.append(1),
                        fmp_used_today=ep.C.FMP_DAILY_SOFT_CAP)
    fmp = [r for r in rows if r["provider"] == "fmp"]
    assert len(fmp) == 11 and all(r["outcome"] == ep.OUT_SKIPPED for r in fmp)
    assert all("soft cap" in r["detail"] for r in fmp)
    assert n == [] and not any("financialmodelingprep" in u for u in calls)


def test_every_row_has_a_role_and_finnhub_covers_the_apps_endpoints():
    rows = ep.run_probe({"finnhub": "", "fmp": ""}, today=date(2026, 10, 27))
    assert all(r["role"] in (ep.ROLE_IN_USE, ep.ROLE_CANDIDATE) for r in rows)
    fh_in_use = {r["endpoint"] for r in rows
                 if r["provider"] == "finnhub" and r["role"] == ep.ROLE_IN_USE}
    assert fh_in_use == {"quote", "stock/news-sentiment", "stock/earnings"}
    assert all(r["role"] == ep.ROLE_IN_USE for r in rows if r["provider"] == "fmp")


def test_fmp_news_probe_uses_the_apps_window():
    seen = {}

    def _get(url, params=None, timeout=None):
        seen[url] = dict(params)
        return _Resp(200, "[1]")
    ep.run_probe({"finnhub": None, "fmp": "MK"}, today=date(2026, 10, 27), get=_get)
    p = next(v for k, v in seen.items() if k.endswith("news/stock"))
    assert p["to"] == "2026-10-27" and p["limit"] == 3
    assert p["from"] == "2026-10-06"   # 21 days (NEWS_MAX_AGE_DAYS)


def test_limit_phrase_in_a_working_2xx_body_is_not_rate_limited():
    body = json.dumps([{"title": "Congress nears a debt limit reached deal"}])
    assert ep.classify_response("fmp", "news/stock", 200, body)[0] == ep.OUT_WORKS
    assert ep.classify_response("finnhub", "company-news", 200, body)[0] == ep.OUT_WORKS


@pytest.mark.parametrize("raw,want", [
    ("AAPL", "AAPL"), ("  aapl ", "AAPL"), ("brk-b", "BRK-B"), ("BRK.B", "BRK.B"),
    ("A", "A"), ("ABCDEFGHIJ", "ABCDEFGHIJ"),
])
def test_normalize_symbol_accepts(raw, want):
    assert ep.normalize_symbol(raw) == want


@pytest.mark.parametrize("raw", [
    "", "   ", "AA PL", "$AAPL", "ABCDEFGHIJK", "AAPL!", "AA/PL", None, 123,
])
def test_normalize_symbol_rejects(raw):
    assert ep.normalize_symbol(raw) is None
