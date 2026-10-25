"""Provider throttling + diagnosable errors (2026-10-25 provider investigation,
docs/plans/provider-investigation.md): P1 Finnhub stops at the first 429, P2 the
live-price paths honour the cooldown / a red primary, P3 bulk historical lookups
skip the real-time source, P5 saves are not serves, P6 per-endpoint error keys."""
import re
from pathlib import Path

import pytest
import requests

from stock_analyzer import api_health as ah
from stock_analyzer.providers import orchestrator as orch
from stock_analyzer.providers import finnhub_provider as fhp
from stock_analyzer.providers import fmp_provider as fmpp
from stock_analyzer.providers.base import DataProvider, ProviderUnavailable, CAP_LIVE_PRICE

_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT.joinpath("app.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_health():
    ah.reset()
    yield
    ah.reset()


def _finnhub():
    p = fhp.FinnhubProvider.__new__(fhp.FinnhubProvider)
    p._key = "FHKEY"
    return p


def _quote(price=10.0):
    return {"c": price, "pc": price - 1, "dp": 1.0}


# -- P1 ---------------------------------------------------------------------
def test_finnhub_stops_at_first_429(monkeypatch):
    calls = []

    def fake(url, params=None, **k):
        calls.append(params["symbol"])
        if len(calls) == 2:
            raise requests.HTTPError("429 Client Error: Too Many Requests")
        return _quote()

    monkeypatch.setattr(fhp, "http_get_json", fake)
    out = _finnhub().live_prices(["A", "B", "C", "D", "E"])
    assert calls == ["A", "B"]
    assert list(out) == ["A"]
    h = ah.get_health("finnhub")
    assert h["rate_limits"] == 1
    assert dict(h["by_key"]) == {"quote rate_limit": 1}


def test_finnhub_non_429_error_keeps_going(monkeypatch):
    calls = []

    def fake(url, params=None, **k):
        calls.append(params["symbol"])
        if len(calls) == 2:
            raise requests.HTTPError("500 Server Error: boom")
        return _quote()

    monkeypatch.setattr(fhp, "http_get_json", fake)
    out = _finnhub().live_prices(["A", "B", "C", "D", "E"])
    assert len(calls) == 5
    assert set(out) == {"A", "C", "D", "E"}


def test_realtime_flags():
    assert fhp.FinnhubProvider.realtime is True
    assert DataProvider.realtime is False


# -- P2 / P3 ----------------------------------------------------------------
class _Fake(DataProvider):
    capabilities = frozenset({CAP_LIVE_PRICE})

    def __init__(self, name, realtime=False):
        self.name = name
        self.realtime = realtime
        self.calls = []

    def live_prices(self, tickers):
        self.calls.append(list(tickers))
        return {t: {"price": 10.0, "prev_close": 9.0, "change_pct": 1.0,
                    "fetched_at": "x", "source": self.name} for t in tickers}


def _chain(monkeypatch, *provs):
    monkeypatch.setattr(orch, "_live_price_providers", lambda: list(provs))


def _make_red(name):
    for _ in range(3):
        ah.record(name, "rate_limit")


def test_get_live_prices_skips_cooling_provider(monkeypatch):
    fh, yf = _Fake("finnhub", True), _Fake("yahoo_finance")
    _chain(monkeypatch, fh, yf)
    monkeypatch.setattr(ah, "in_cooldown", lambda name, sec: name == "finnhub")
    out = orch.get_live_prices(["A", "B"])
    assert fh.calls == []
    assert set(out) == {"A", "B"}


def test_get_live_prices_realtime_false_skips_realtime_provider(monkeypatch):
    fh, yf = _Fake("finnhub", True), _Fake("yahoo_finance")
    _chain(monkeypatch, fh, yf)
    out = orch.get_live_prices(["A"], realtime=False)
    assert fh.calls == [] and yf.calls == [["A"]]
    assert set(out) == {"A"}
    orch.get_live_prices(["A"])  # default still calls the real-time source
    assert fh.calls == [["A"]]


def test_fetch_live_prices_realtime_flag_reaches_orchestrator(monkeypatch):
    from stock_analyzer import data
    seen = []
    monkeypatch.setattr(data._C, "DATA_MULTISOURCE_ENABLED", True)
    monkeypatch.setattr(data._orch, "get_live_prices",
                        lambda t, **kw: seen.append(kw) or {})
    data.fetch_live_prices(["A"])
    data.fetch_live_prices(["A"], realtime=False)
    assert seen == [{}, {"realtime": False}]


def test_crosscheck_batch_returns_empty_when_primary_red(monkeypatch):
    fh, yf = _Fake("finnhub", True), _Fake("yahoo_finance")
    _chain(monkeypatch, fh, yf)
    _make_red("finnhub")
    assert orch.crosscheck_batch(["A"]) == {}
    assert fh.calls == [] and yf.calls == []


def test_crosscheck_batch_returns_empty_when_primary_cooling(monkeypatch):
    fh, yf = _Fake("finnhub", True), _Fake("yahoo_finance")
    _chain(monkeypatch, fh, yf)
    monkeypatch.setattr(ah, "in_cooldown", lambda name, sec: name == "finnhub")
    assert orch.crosscheck_batch(["A"]) == {}
    assert fh.calls == [] and yf.calls == []


def test_crosscheck_batch_healthy_still_compares(monkeypatch):
    fh, yf = _Fake("finnhub", True), _Fake("yahoo_finance")
    _chain(monkeypatch, fh, yf)
    out = orch.crosscheck_batch(["A"])
    assert out["A"]["primary_source"] == "finnhub" and out["A"]["validator"] == "yahoo_finance"


def test_crosscheck_price_skips_cooling_validator(monkeypatch):
    fh, yf = _Fake("finnhub", True), _Fake("yahoo_finance")
    _chain(monkeypatch, fh, yf)
    monkeypatch.setattr(ah, "in_cooldown", lambda name, sec: name == "yahoo_finance")
    assert orch.crosscheck_price("A", 10.0, 9.0) is None
    assert yf.calls == []


def test_crosscheck_against_skips_cooling_source(monkeypatch):
    yf = _Fake("yahoo_finance")
    _chain(monkeypatch, _Fake("finnhub", True), yf)
    monkeypatch.setattr(ah, "in_cooldown", lambda name, sec: name == "yahoo_finance")
    assert orch.crosscheck_against("yahoo_finance", "A", 10.0, 9.0) is None
    assert yf.calls == []


def test_validator_degraded_names_primary_when_red(monkeypatch):
    _chain(monkeypatch, _Fake("finnhub", True), _Fake("yahoo_finance"))
    _make_red("finnhub")
    assert orch.live_price_validator_degraded() == "finnhub"


def test_validator_degraded_names_validator_when_it_is_red(monkeypatch):
    _chain(monkeypatch, _Fake("finnhub", True), _Fake("yahoo_finance"))
    _make_red("yahoo_finance")
    assert orch.live_price_validator_degraded() == "yahoo_finance"


def test_validator_degraded_none_when_healthy(monkeypatch):
    _chain(monkeypatch, _Fake("finnhub", True), _Fake("yahoo_finance"))
    assert orch.live_price_validator_degraded() is None


_BULK_SITES = ["_etr_all_tickers", "_etr_prot_missing", "_rh_all_tickers", "_pac_tickers",
               "_mr_tickers", "_mr_tk2", "_stv_tickers", "_sts_tickers"]


@pytest.mark.parametrize("var", _BULK_SITES)
def test_bulk_historical_call_sites_are_not_realtime(var):
    calls = re.findall(r"fetch_live_prices\(" + re.escape(var) + r"[^)]*\)", _APP)
    assert len(calls) == 1
    assert "realtime=False" in calls[0]


def test_home_strip_stays_realtime():
    start = _APP.index("def _cached_live_prices")
    body = _APP[start:_APP.index("\ndef ", start + 10)]
    assert "realtime=False" not in body


# -- P5 ---------------------------------------------------------------------
def test_save_event_is_not_a_call():
    ah.record("bundle_cache", "save")
    h = ah.get_health("bundle_cache")
    assert (h["calls"], h["successes"], h["saves"]) == (0, 0, 1)
    ah.record("bundle_cache", "success")
    h = ah.get_health("bundle_cache")
    assert (h["calls"], h["successes"], h["saves"]) == (1, 1, 1)


def test_footnote_condition_reads_successes():
    assert 'if _ah_src == "bundle_cache" and _ah_h["successes"] > 0:' in _APP
    assert 'if _ah_src == "bundle_cache" and _ah_h["calls"] > 0:' not in _APP


def test_save_bundle_cache_records_save_not_success():
    src = _ROOT.joinpath("stock_analyzer", "db.py").read_text(encoding="utf-8")
    start = src.index("def save_bundle_cache")
    body = src[start:src.index("\ndef ", start + 10)]
    assert '_ah.record("bundle_cache", "save")' in body
    assert '_ah.record("bundle_cache", "success")' not in body
    load = src[src.index("def load_bundle_cache"):]
    assert '_ah.record("bundle_cache", "success")' in load


# -- P6 ---------------------------------------------------------------------
def test_by_key_counts_non_success_events_only():
    ah.record("fmp", "quota", msg="x", key="quote")
    ah.record("fmp", "quota", msg="x", key="quote")
    ah.record("fmp", "error", msg="x", key="profile")
    ah.record("fmp", "success", key="quote")
    ah.record("fmp", "save", key="quote")
    h = ah.get_health("fmp")
    assert h["by_key"] == [("profile error", 1), ("quote quota", 2)]


def test_by_key_absent_without_key_and_unknown_source_default():
    ah.record("fmp", "error", msg="x")
    assert ah.get_health("fmp")["by_key"] == []
    assert ah.get_health("nope")["by_key"] == []


def test_last_error_msg_still_120_chars():
    ah.record("fmp", "error", msg="z" * 500, key="quote")
    assert len(ah.get_health("fmp")["last_error"]) == 120


def _fmp():
    p = fmpp.FMPProvider.__new__(fmpp.FMPProvider)
    p._key = "SECRETKEY123"
    return p


def _http402(url, params=None, **k):
    resp = requests.Response()
    resp.status_code = 402
    raise requests.HTTPError(
        f"402 Client Error: Payment Required for url: {url}?apikey=SECRETKEY123",
        response=resp)


def test_fmp_error_msg_names_endpoint_and_symbol_never_the_key(monkeypatch):
    monkeypatch.setattr(fmpp, "http_get_json", _http402)
    monkeypatch.setattr(fmpp._db, "increment_daily_quota", lambda *a, **k: None)
    assert _fmp().live_prices(["AAPL"]) == {}
    h = ah.get_health("fmp")
    assert "quote AAPL" in h["last_error"] and "402" in h["last_error"]
    assert "SECRETKEY123" not in h["last_error"]
    assert dict(h["by_key"]) == {"quote quota": 1}


def test_fmp_get_json_error_is_keyed_by_path(monkeypatch):
    monkeypatch.setattr(fmpp, "http_get_json", _http402)
    monkeypatch.setattr(fmpp._db, "increment_daily_quota", lambda *a, **k: None)
    with pytest.raises(ProviderUnavailable):
        _fmp()._get_json("profile", {"symbol": "MSFT"})
    h = ah.get_health("fmp")
    assert "profile MSFT" in h["last_error"] and "SECRETKEY123" not in h["last_error"]
    assert dict(h["by_key"]) == {"profile quota": 1}


def test_data_health_error_details_uses_no_raw_html():
    start = _APP.index('with st.expander("Error details"')
    block = _APP[start:_APP.index("# ── Curated news feed", start)]
    assert "unsafe_allow_html" not in block


def test_get_live_prices_all_cooling_falls_back_to_full_list(monkeypatch):
    # Lead fix (2026-10-25): matches _providers_for's documented "never a
    # permanent hard-block" rule -- if every live-price provider is cooling,
    # they are all tried rather than returning nothing.
    from stock_analyzer.providers import orchestrator as orch
    from stock_analyzer import api_health as ah

    calls = []

    class _P:
        def __init__(self, name, rt):
            self.name, self.realtime = name, rt
        def supports(self, cap):
            return True
        def live_prices(self, tickers):
            calls.append(self.name)
            return {t: {"price": 1.0, "source": self.name} for t in tickers}

    provs = [_P("finnhub", True), _P("yahoo_finance", False)]
    monkeypatch.setattr(orch, "_live_price_providers", lambda: provs)
    monkeypatch.setattr(ah, "in_cooldown", lambda name, sec: True)
    out = orch.get_live_prices(["AAA"])
    assert out["AAA"]["source"] == "finnhub" and calls == ["finnhub"]

    calls.clear()
    out = orch.get_live_prices(["AAA"], realtime=False)
    assert out["AAA"]["source"] == "yahoo_finance" and calls == ["yahoo_finance"]


# -- review round 1 (Opus FIX-FIRST, 2026-10-25) ------------------------------

def test_red_but_recovered_primary_still_crosschecks(monkeypatch):
    # api_health "red" is cumulative (rate_limits >= 3 never decays). A primary
    # that is red but NOT cooling and has no live error streak (a success reset
    # consec_err) must still be cross-checked, or the check would stay paused
    # until a process restart.
    fh, yf = _Fake("finnhub", True), _Fake("yahoo_finance")
    _chain(monkeypatch, fh, yf)
    _make_red("finnhub")
    ah.record("finnhub", "success")          # recovered: consec_err -> 0
    monkeypatch.setattr(ah, "in_cooldown", lambda name, sec: False)
    assert ah.get_health("finnhub")["level"] == "red"
    assert orch.live_price_validator_degraded() is None
    orch.crosscheck_batch(["A"])
    assert fh.calls, "a recovered primary must be called again"


def test_red_with_live_error_streak_still_pauses(monkeypatch):
    fh, yf = _Fake("finnhub", True), _Fake("yahoo_finance")
    _chain(monkeypatch, fh, yf)
    _make_red("finnhub")                      # consec_err = 3, no success since
    monkeypatch.setattr(ah, "in_cooldown", lambda name, sec: False)
    assert orch.crosscheck_batch(["A"]) == {}
    assert fh.calls == []
    assert orch.live_price_validator_degraded() == "finnhub"


@pytest.mark.parametrize("err,expected_calls", [
    ("429 Client Error: Too Many Requests for url: x", ["A"]),       # stop
    ("401 Client Error: Unauthorized for url: x", ["A"]),            # stop
    ("402 Client Error: Payment Required for url: x", ["A", "B", "C"]),  # per-symbol: keep going
])
def test_fmp_live_prices_stops_only_on_limit_or_auth(monkeypatch, err, expected_calls):
    import requests
    from stock_analyzer.providers import fmp_provider as fp
    calls = []

    def _fake_get(url, params=None, timeout=None):
        calls.append(params.get("symbol"))
        raise requests.HTTPError(err)

    monkeypatch.setattr(fp, "http_get_json", _fake_get)
    monkeypatch.setattr(fp._db, "increment_daily_quota", lambda *_a, **_k: None)
    p = fp.FMPProvider()
    p._key = "k"
    assert p.live_prices(["A", "B", "C"]) == {}
    assert calls == expected_calls


def test_paused_crosscheck_is_not_cached_in_app():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert "if held_tickers and not _xc_validator_down else {})" in src
    assert "if not _an_validator_down else {})" in src
