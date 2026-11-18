"""Finnhub's free plan answers 403 for a symbol it does not cover (a non-US
ticker such as 000660.KS) but 401 for a bad key. A plan-boundary 403 must not
turn the Data Health chip red; 401 anywhere, or a 403 on a plain US symbol, is
still an auth fault. The skipped symbol is left to the next price source."""
import pytest
import requests

from stock_analyzer import api_health as ah
from stock_analyzer.providers import finnhub_provider as fhp

pytestmark = pytest.mark.fast


@pytest.fixture(autouse=True)
def _fresh_health():
    ah.reset()
    yield
    ah.reset()


def _finnhub():
    p = fhp.FinnhubProvider.__new__(fhp.FinnhubProvider)
    p._key = "FHKEY"
    return p


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def _http_err(code, reason):
    return requests.HTTPError(f"{code} Client Error: {reason}", response=_Resp(code))


def _fake(raises: dict, price=10.0):
    def fake(url, params=None, **k):
        t = params["symbol"]
        if t in raises:
            raise raises[t]
        return {"c": price, "pc": price - 1, "dp": 1.0}
    return fake


def test_non_us_403_is_unsupported_and_source_stays_green(monkeypatch):
    monkeypatch.setattr(fhp, "http_get_json",
                        _fake({"000660.KS": _http_err(403, "Forbidden")}))
    out = _finnhub().live_prices(["AAPL", "000660.KS"])
    assert set(out) == {"AAPL"}
    h = ah.get_health("finnhub")
    assert h["level"] == "green"
    assert h["unsupported"] == 1
    assert h["auth_errors"] == 0
    assert h["errors"] == 0
    assert h["last_error"] == ""
    assert "000660.KS" in h["last_unsupported"]
    assert dict(h["by_key"]) == {"quote unsupported": 1}


def test_403_on_plain_us_symbol_is_still_auth(monkeypatch):
    monkeypatch.setattr(fhp, "http_get_json",
                        _fake({"AAPL": _http_err(403, "Forbidden")}))
    assert _finnhub().live_prices(["AAPL"]) == {}
    h = ah.get_health("finnhub")
    assert h["auth_errors"] == 1
    assert h["level"] == "red"
    assert h["unsupported"] == 0


def test_401_on_non_us_symbol_is_still_auth(monkeypatch):
    monkeypatch.setattr(fhp, "http_get_json",
                        _fake({"000660.KS": _http_err(401, "Unauthorized")}))
    assert _finnhub().live_prices(["000660.KS"]) == {}
    h = ah.get_health("finnhub")
    assert h["auth_errors"] == 1
    assert h["level"] == "red"
    assert h["unsupported"] == 0


def test_only_unsupported_batch_does_not_record_empty(monkeypatch):
    monkeypatch.setattr(fhp, "http_get_json",
                        _fake({"000660.KS": _http_err(403, "Forbidden")}))
    assert _finnhub().live_prices(["000660.KS"]) == {}
    h = ah.get_health("finnhub")
    assert h["empty"] == 0
    assert h["unsupported"] == 1
    assert h["level"] == "gray"   # nothing served yet


def test_403_without_a_response_object_keeps_the_auth_path(monkeypatch):
    # No status_code to read -> classify_error decides, exactly as before.
    monkeypatch.setattr(fhp, "http_get_json",
                        _fake({"000660.KS": requests.HTTPError("403 Client Error: Forbidden")}))
    _finnhub().live_prices(["000660.KS"])
    h = ah.get_health("finnhub")
    assert h["auth_errors"] == 1 and h["unsupported"] == 0


@pytest.mark.parametrize("sym,expected", [
    ("AAPL", True), ("BRK-B", True), ("F", True), ("GOOGL", True),
    ("000660.KS", False), ("SHOP.TO", False), ("BP.L", False),
    ("^VIX", False), ("ES=F", False), ("BRK.B", False), ("", False),
    ("aapl", False), ("TOOLONG", False), ("BRK-BBB", False),
])
def test_is_plain_us_symbol(sym, expected):
    assert fhp._is_plain_us_symbol(sym) is expected

@pytest.mark.parametrize("order", [["000660.KS", "MSFT"], ["MSFT", "000660.KS"]])
def test_mixed_batch_red_wins_whatever_the_order(monkeypatch, order):
    monkeypatch.setattr(fhp, "http_get_json", _fake({
        "000660.KS": _http_err(403, "Forbidden"),
        "MSFT": _http_err(401, "Unauthorized"),
    }))
    assert _finnhub().live_prices(order) == {}
    h = ah.get_health("finnhub")
    assert h["level"] == "red"
    assert h["unsupported"] == 1 and h["auth_errors"] == 1


def test_unsupported_calls_do_not_dilute_the_error_rate():
    # 2 errors in 7 real calls = 28.6% > 20% -> yellow. Diluted by 10
    # plan-boundary calls it would be 2/17 = 11.8% and read green.
    for ev in ["success", "success", "success", "error", "success", "error", "success"]:
        ah.record("finnhub", ev, msg="boom" if ev == "error" else "")
    for _ in range(10):
        ah.record("finnhub", "unsupported", msg="quote/000660.KS: x", key="quote")
    assert ah.get_health("finnhub")["level"] == "yellow"


def test_get_live_prices_gap_fills_the_unsupported_symbol(monkeypatch):
    from stock_analyzer.providers import orchestrator as orch
    monkeypatch.setattr(fhp, "http_get_json",
                        _fake({"000660.KS": _http_err(403, "Forbidden")}))

    class _Yahoo:
        name = "yahoo_finance"
        realtime = False

        def live_prices(self, tickers):
            return {t: {"price": 99.0, "source": "yahoo_finance"} for t in tickers}

    monkeypatch.setattr(orch, "_live_price_providers", lambda: [_finnhub(), _Yahoo()])
    out = orch.get_live_prices(["AAPL", "000660.KS"])
    assert out["AAPL"]["source"] == "finnhub"
    assert out["000660.KS"]["source"] == "yahoo_finance"
