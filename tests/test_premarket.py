"""Tests for stock_analyzer/premarket.py — pre-market intelligence (US index
futures, global overnight indices, pre-market movers, today's macro events).
`is_premarket` depends on real wall-clock time so we only assert it's a
well-typed, non-crashing call (the weekday+hour window logic is simple
enough that fighting real time for an exact boundary isn't worth it here).
Everything else is pure once yfinance is mocked by replacing the module's
`yf` reference (matching test_scanner.py's approach) or, more surgically,
the module-level `_fast` helper that wraps `yf.Ticker(...).fast_info`.
"""
import pandas as pd
import pytest

from stock_analyzer import premarket


# ─── is_premarket — structural only (real wall-clock time) ──────────────────

def test_is_premarket_returns_bool_without_crashing():
    result = premarket.is_premarket()
    assert isinstance(result, bool)


# ─── _pct ────────────────────────────────────────────────────────────────────

def test_pct_falsy_price_returns_none():
    assert premarket._pct(None, 100.0) is None
    assert premarket._pct(0, 100.0) is None


def test_pct_falsy_prev_returns_none():
    assert premarket._pct(100.0, None) is None
    assert premarket._pct(100.0, 0) is None


def test_pct_non_positive_prev_returns_none():
    assert premarket._pct(100.0, -5.0) is None


def test_pct_normal_case_and_rounding():
    assert premarket._pct(110.0, 100.0) == pytest.approx(10.0)
    assert premarket._pct(100.12345, 100.0) == round((100.12345 - 100.0) / 100.0 * 100, 2)


# ─── futures_tone ────────────────────────────────────────────────────────────

def test_futures_tone_no_es_entry_is_flat():
    assert premarket.futures_tone([{"symbol": "NQ=F", "chg_pct": 5.0}]) == "flat"


def test_futures_tone_at_exactly_positive_boundary_is_bull():
    assert premarket.futures_tone([{"symbol": "ES=F", "chg_pct": 0.4}]) == "bull"


def test_futures_tone_just_below_positive_boundary_is_flat():
    assert premarket.futures_tone([{"symbol": "ES=F", "chg_pct": 0.39}]) == "flat"


def test_futures_tone_at_exactly_negative_boundary_is_bear():
    assert premarket.futures_tone([{"symbol": "ES=F", "chg_pct": -0.4}]) == "bear"


def test_futures_tone_just_above_negative_boundary_is_flat():
    assert premarket.futures_tone([{"symbol": "ES=F", "chg_pct": -0.39}]) == "flat"


# ─── fetch_futures — mocked _fast ────────────────────────────────────────────

def test_fetch_futures_drops_symbol_with_none_price(monkeypatch):
    prices = {
        "ES=F":  (5000.0, 4980.0),
        "NQ=F":  (None, None),
        "YM=F":  (40000.0, 40000.0),
        "RTY=F": (2000.0, 1900.0),
    }
    monkeypatch.setattr(premarket, "_fast", lambda sym: prices[sym])

    rows = premarket.fetch_futures()
    symbols = [r["symbol"] for r in rows]
    assert "NQ=F" not in symbols
    assert set(symbols) == {"ES=F", "YM=F", "RTY=F"}

    es_row = next(r for r in rows if r["symbol"] == "ES=F")
    assert es_row["chg_pct"] == pytest.approx(round((5000.0 - 4980.0) / 4980.0 * 100, 2))


# ─── fetch_global_markets — mocked yf.Ticker(sym).history ───────────────────

class _FakeHistTicker:
    def __init__(self, df):
        self._df = df

    def history(self, **kwargs):
        return self._df


class _FakeYFGlobal:
    def __init__(self, hist_by_symbol):
        self._hist_by_symbol = hist_by_symbol

    def Ticker(self, sym):
        return _FakeHistTicker(self._hist_by_symbol.get(sym, pd.DataFrame()))


def _hist_df(closes):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


def test_fetch_global_markets_excludes_single_row_history(monkeypatch):
    fake = _FakeYFGlobal({
        "^N225": _hist_df([30000.0]),          # only 1 row -- excluded
        "^HSI":  _hist_df([18000.0, 18200.0]),  # 2 rows -- included
    })
    monkeypatch.setattr(premarket, "yf", fake)

    rows = premarket.fetch_global_markets()
    symbols = [r["symbol"] for r in rows]
    assert "^N225" not in symbols
    assert "^HSI" in symbols


def test_fetch_global_markets_computes_chg_and_sorts_descending(monkeypatch):
    fake = _FakeYFGlobal({
        "^N225":  _hist_df([30000.0, 30300.0]),  # +1.0%
        "^HSI":   _hist_df([18000.0, 17640.0]),  # -2.0%
        "^GDAXI": _hist_df([17000.0, 17510.0]),  # +3.0%
    })
    monkeypatch.setattr(premarket, "yf", fake)

    rows = premarket.fetch_global_markets()
    chgs = [r["chg_pct"] for r in rows]
    assert chgs == sorted(chgs, reverse=True)
    n225 = next(r for r in rows if r["symbol"] == "^N225")
    assert n225["chg_pct"] == pytest.approx(1.0)


# ─── fetch_premarket_movers ──────────────────────────────────────────────────

def test_fetch_premarket_movers_prefers_held_data_close_over_fast_info(monkeypatch):
    # Regression test for a real bug found while writing this batch:
    # `hd.get("df") or hd.get("history")` called Python's `or`, which
    # evaluates `bool(df)` on the left operand whenever it's truthy-checked --
    # and pandas DataFrames ALWAYS raise on `bool()` regardless of shape or
    # emptiness. So the documented "prefer held_data's Close as the prior-
    # close baseline" behavior crashed instead of firing, for ANY non-None
    # `hd["df"]`. Fixed to an explicit `is None` check. Was unreachable in
    # production (the only real caller, app.py's `_get_premarket_brief`,
    # always passes `held_data={}`) but `build_premarket_brief` threads real
    # held_data through, so this was a live landmine for that path.
    monkeypatch.setattr(premarket, "_fast", lambda sym: (100.0, 90.0))
    held_data = {"AAPL": {"df": pd.DataFrame({"Close": [95.0, 99.0]})}}

    rows = premarket.fetch_premarket_movers(["AAPL"], held_data)

    assert len(rows) == 1
    # prev_close should come from held_data's df (99.0), not fast_info's 90.0
    assert rows[0]["prev_close"] == pytest.approx(99.0)
    assert rows[0]["chg_pct"] == pytest.approx((100.0 - 99.0) / 99.0 * 100, rel=1e-3)


def test_fetch_premarket_movers_filters_below_half_percent(monkeypatch):
    monkeypatch.setattr(premarket, "_fast", lambda sym: {"AAA": (100.0, 100.49), "BBB": (100.0, 99.5)}[sym])
    rows = premarket.fetch_premarket_movers(["AAA", "BBB"], {})
    tickers = [r["ticker"] for r in rows]
    assert "AAA" not in tickers   # |chg| just under 0.5
    assert "BBB" in tickers       # |chg| exactly at 0.5


def test_fetch_premarket_movers_cap_at_12_and_sorted_by_abs_change(monkeypatch):
    tickers = [f"T{i}" for i in range(15)]
    prices = {t: (100.0 + (i + 1), 100.0) for i, t in enumerate(tickers)}  # chg = i+1 %
    monkeypatch.setattr(premarket, "_fast", lambda sym: prices[sym])

    rows = premarket.fetch_premarket_movers(tickers, {})
    assert len(rows) == 12
    abs_chgs = [abs(r["chg_pct"]) for r in rows]
    assert abs_chgs == sorted(abs_chgs, reverse=True)


def test_fetch_premarket_movers_is_held_flag(monkeypatch):
    monkeypatch.setattr(premarket, "_fast", lambda sym: (105.0, 100.0))
    # No "df"/"history" key -- avoids the real-DataFrame `or` bug documented
    # above; `is_held` only cares about key presence in held_data, not its
    # contents, so this isolates that concern cleanly.
    held_data = {"AAPL": {}}
    rows = premarket.fetch_premarket_movers(["AAPL", "MSFT"], held_data)
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["AAPL"]["is_held"] is True
    assert by_ticker["MSFT"]["is_held"] is False


# ─── build_premarket_brief — orchestration ──────────────────────────────────

def test_build_premarket_brief_shape_and_events_filter(monkeypatch):
    monkeypatch.setattr(premarket, "fetch_futures", lambda: [{"symbol": "ES=F", "chg_pct": 0.5}])
    monkeypatch.setattr(premarket, "futures_tone", lambda futures: "bull")
    monkeypatch.setattr(premarket, "fetch_global_markets", lambda: [{"symbol": "^N225", "chg_pct": 1.0}])

    captured_tickers = {}

    def fake_movers(tickers, held_data):
        captured_tickers["tickers"] = tickers
        return [{"ticker": "AAPL", "chg_pct": 1.0}]

    monkeypatch.setattr(premarket, "fetch_premarket_movers", fake_movers)

    today = "2024-06-10"
    macro_events = [
        {"date": today, "impact": "HIGH", "event": "CPI"},
        {"date": today, "impact": "MEDIUM", "event": "Fed Speech"},
        {"date": today, "impact": "LOW", "event": "Ignored — low impact"},
        {"date": "2024-06-09", "impact": "HIGH", "event": "Ignored — wrong date"},
    ]

    result = premarket.build_premarket_brief(
        held_tickers=["AAPL", "MSFT"],
        watchlist=["MSFT", "GOOG"],
        held_data={},
        macro_events=macro_events,
        today=today,
    )

    assert result["tone"] == "bull"
    assert result["futures"] == [{"symbol": "ES=F", "chg_pct": 0.5}]
    assert result["global_markets"] == [{"symbol": "^N225", "chg_pct": 1.0}]
    assert result["movers"] == [{"ticker": "AAPL", "chg_pct": 1.0}]
    assert [e["event"] for e in result["events"]] == ["CPI", "Fed Speech"]
    assert "as_of" in result

    # held_tickers + watchlist deduped via dict.fromkeys, preserving order.
    assert captured_tickers["tickers"] == ["AAPL", "MSFT", "GOOG"]
