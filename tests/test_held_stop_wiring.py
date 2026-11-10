"""Tests for stock_analyzer.held_stop_wiring / held_stop_view and the (a)-chunk
fetch plumbing. Synthetic frames, no network, no DB."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import data as data_mod
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_view as hv
from stock_analyzer import held_stop_wiring as hw
from stock_analyzer import portfolio as pf

AS_OF = date(2026, 10, 30)          # Friday; prev trading day 10-29


def walk(n=260, seed=1, end="2026-10-29", drift=0.0005, vol=0.015):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close}, index=idx)


def journal(ticker, shares, price, when):
    return pd.DataFrame([{"id": 1, "ticker": ticker, "action": "BUY", "shares": shares,
                          "price": price, "traded_at": f"{when}T14:00:00+00:00"}])


def setup(n=260, seed=1, back=120, shares=10.0):
    df = walk(n=n, seed=seed)
    e = df.index[-back].date()
    px = float(df["Close"].iloc[-back])
    h = [{"Ticker": "AAA", "Shares": int(shares), "Shares Exact": shares, "Avg Cost ($)": px}]
    return df, e, px, h, journal("AAA", shares, px, e)


@pytest.fixture(autouse=True)
def early_trail(monkeypatch):
    # trailing / ratchet from the start of the synthetic history
    monkeypatch.setattr(C, "HELD_TRAIL_START", "2025-01-01")


def build(h, held, trades, fetch, as_of=AS_OF):
    return hw.build_held_stops(h, held, trades, as_of, fetch_history=fetch)["AAA"]


def _boom(t, p):
    raise AssertionError("fetch must not be called")


def _fetch_down(t, p):
    raise RuntimeError("unadjusted history unavailable")   # the bundle frame is used


# ── constants / plumbing ─────────────────────────────────────────────────────
def test_constants_values_owner_ruled(monkeypatch):
    monkeypatch.undo()
    assert (C.HELD_TRAIL_ATR_MULT, C.HELD_STOP_WARMUP_BARS, C.HELD_STOP_MIN_BARS,
            C.HELD_STOP_FILL_TOL_PCT, C.HELD_STOP_SHARES_TOL,
            C.HELD_STOP_NOTICE_SESSIONS) == (3.0, 60, 15, 2.0, 1e-4, 5)
    assert date.fromisoformat(C.HELD_TRAIL_START) == date(2026, 10, 30)


def test_yfinance_pin_tripwire():
    from pathlib import Path
    reqs = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text().splitlines()
    assert "yfinance==1.3.0" in [r.strip() for r in reqs], (
        "auto_adjust=False semantics were verified on yfinance 1.3.0; re-verify "
        "price_history_unadjusted before moving the pin")


def test_network_guard_is_active():
    from yfinance import data as yfd
    with pytest.raises(pytest.fail.Exception):
        yfd.YfData._make_request(object(), "https://example.invalid", "get")


def test_provider_price_history_unadjusted_uses_auto_adjust_false(monkeypatch):
    from stock_analyzer.providers import yfinance_provider as yp
    seen = {}
    frame = walk(n=5)

    class FakeTicker:
        def __init__(self, t):
            seen["t"] = t

        def history(self, **kw):
            seen["kw"] = kw
            f = frame.copy()
            f.loc[f.index[-1], "Close"] = np.nan
            return f
    monkeypatch.setattr(yp.yf, "Ticker", FakeTicker)
    out = yp.YFinanceProvider().price_history_unadjusted("ZZZ", "2y")
    assert seen["kw"] == {"period": "2y", "auto_adjust": False}
    assert len(out) == 4 and out["Close"].notna().all()


def test_fetch_unadjusted_history_never_raises(monkeypatch):
    prim = data_mod._PRIMARY

    def _raise(t, p):
        raise RuntimeError("boom")
    monkeypatch.setattr(prim, "price_history_unadjusted", _raise)
    assert data_mod.fetch_unadjusted_history("X", "1y") is None
    monkeypatch.setattr(prim, "price_history_unadjusted", lambda t, p: pd.DataFrame())
    assert data_mod.fetch_unadjusted_history("X", "1y") is None
    monkeypatch.setattr(prim, "price_history_unadjusted", lambda t, p: None)
    assert data_mod.fetch_unadjusted_history("X", "1y") is None
    f = walk(n=5)
    monkeypatch.setattr(prim, "price_history_unadjusted", lambda t, p: f)
    assert data_mod.fetch_unadjusted_history("X", "1y") is f


def test_fetch_or_raise(monkeypatch):
    monkeypatch.setattr(data_mod, "fetch_unadjusted_history", lambda t, p: None)
    with pytest.raises(RuntimeError):
        hw.fetch_or_raise("X", "1y")
    f = walk(n=5)
    monkeypatch.setattr(data_mod, "fetch_unadjusted_history", lambda t, p: f)
    assert hw.fetch_or_raise("X", "1y") is f


def test_c1_result_has_atr_last():
    df, e, px, h, tr = setup()
    r = build(h, {}, tr, lambda t, p: df)
    assert r["atr_last"] and r["atr_last"] > 0
    assert r["basis"] == "full" and r["stop"] is not None


# ── fallback chain ───────────────────────────────────────────────────────────
def test_full_basis_uses_long_frame_sized_by_period():
    df, e, px, h, tr = setup()
    periods = []

    def fetch(t, p):
        periods.append(p)
        return df
    r = build(h, {"AAA": {"df": df.tail(126)}}, tr, fetch)
    assert r["basis"] == "full" and not r["degraded"] and r["note"] == ""
    assert periods and periods[0] in ("1y", "2y", "5y", "max")


@pytest.mark.parametrize("mode", ["none", "raise", "empty"])
def test_fetch_failure_falls_back_to_partial_bundle(mode):
    df, e, px, h, tr = setup()

    def fetch(t, p):
        if mode == "raise":
            raise RuntimeError("down")
        return None if mode == "none" else pd.DataFrame()
    r = build(h, {"AAA": {"df": df.tail(100)}}, tr, fetch)
    assert r["basis"] == "partial" and r["degraded"] and r["fell_back"]
    assert r["note"]
    assert "only moves up" not in r["note"].lower()
    assert "lower bound" in r["note"]
    assert r["stop"] is not None


def test_stale_long_frame_falls_back_to_bundle():
    df, e, px, h, tr = setup()
    stale = df.iloc[:-10]
    r = build(h, {"AAA": {"df": df.tail(100)}}, tr, lambda t, p: stale)
    assert r["basis"] == "partial" and "out of date" in r["note"]


def test_bundle_that_covers_everything_is_still_partial_when_used_as_fallback():
    df, e, px, h, tr = setup(n=260, back=40)
    r = build(h, {"AAA": {"df": df}}, tr, lambda t, p: None)
    assert r["basis"] == "partial"


def test_no_bundle_and_no_fetch_is_unavailable():
    df, e, px, h, tr = setup()
    r = build(h, {}, tr, lambda t, p: None)
    assert r["basis"] == "unavailable" and r["stop"] is None
    assert r["label"] == "Stop Unavailable" and r["degraded"] and r["note"]


def test_journal_unreadable_is_prior_session_with_note():
    df, e, px, h, tr = setup()
    r = build(h, {"AAA": {"df": df}}, None, _boom)
    assert r["basis"] == "prior_session" and r["label"] == "Prior-close ATR"
    assert "trade journal could not be read" in r["note"]
    assert "only moves up" not in r["note"].lower() and "does not trail" in r["note"]


def test_journal_mismatch_and_empty_are_prior_session():
    df, e, px, h, tr = setup()
    r = build(h, {"AAA": {"df": df}}, journal("AAA", 3.0, px, e), _boom)
    assert r["basis"] == "prior_session" and "journal nets" in r["note"]
    r = build(h, {"AAA": {"df": df}}, pd.DataFrame(), _boom)
    assert r["basis"] == "prior_session"


def test_prior_session_without_bundle_uses_fetch():
    df, e, px, h, tr = setup()
    r = build(h, {}, None, lambda t, p: df)
    assert r["basis"] == "prior_session"


def test_fractional_shares_ok_and_int_shares_would_mismatch():
    df, e, px, h, tr = setup(shares=2.5)
    r = build(h, {}, tr, lambda t, p: df)
    assert r["basis"] == "full"
    h_int = [{"Ticker": "AAA", "Shares": 2, "Avg Cost ($)": px}]
    assert build(h_int, {}, tr, lambda t, p: df)["basis"] == "prior_session"
    h_low = [{"ticker": "AAA", "shares": 2.5, "avg_cost": px}]
    assert build(h_low, {}, tr, lambda t, p: df)["basis"] == "full"


def test_never_raises(monkeypatch):
    df, e, px, h, tr = setup()
    junk = [None, 5, {"Ticker": ""}, {"Ticker": "BAD", "Shares": "x", "Avg Cost ($)": 1},
            {"Ticker": "AAA", "Shares Exact": 10.0, "Avg Cost ($)": px}]
    out = hw.build_held_stops(junk, {"AAA": 5}, tr, AS_OF, fetch_history=lambda t, p: 1 / 0)
    assert set(out) <= {"AAA"}
    assert hw.build_held_stops(None, None, None, AS_OF, fetch_history=None) == {}
    monkeypatch.setattr(hs, "compute_held_stop", lambda *a, **k: 1 / 0)
    r = build(h, {"AAA": {"df": df}}, tr, lambda t, p: df)
    assert r["basis"] == "unavailable" and r["stop"] is None and "ZeroDivisionError" in r["note"]
    monkeypatch.setattr(C, "HELD_TRAIL_START", "not-a-date")
    assert hw.build_held_stops(h, {}, tr, AS_OF, fetch_history=lambda t, p: df)["AAA"]["stop"] is None


# ── rule properties through the driver ───────────────────────────────────────
def _next_bar_date(df, i):
    return df.index[i + 1].date()


def test_monotone_over_40_sessions_on_full_basis():
    df, e, px, h, tr = setup(seed=4)
    stops = []
    for i in range(len(df) - 41, len(df) - 1):
        r = build(h, {}, tr, lambda t, p: df, as_of=_next_bar_date(df, i))
        assert r["basis"] == "full", (i, r["note"])
        stops.append(r["stop"])
    assert all(b >= a for a, b in zip(stops, stops[1:]))


def test_partial_bar_dated_as_of_never_moves_the_stop():
    df, e, px, h, tr = setup()
    as_of = df.index[-3].date()
    base = build(h, {}, tr, lambda t, p: df, as_of=as_of)["stop"]
    crash = df.copy()
    crash.loc[crash.index[-3]] = [1.0, 1e6, 1e-6, 1e-3]
    assert build(h, {}, tr, lambda t, p: crash, as_of=as_of)["stop"] == base


def test_premarket_equivalence_close_vs_stop_d_equals_close_vs_stop_d_minus_1():
    # premarket on D compares close(D-1) with stop(D); the in-session rule used
    # stop(D-1). Same breach verdict at every session.
    df, e, px, h, tr = setup(seed=7)
    for i in range(len(df) - 80, len(df) - 1):
        c = float(df["Close"].iloc[i])
        s_prev = build(h, {}, tr, lambda t, p: df, as_of=df.index[i].date())["stop"]
        s_next = build(h, {}, tr, lambda t, p: df, as_of=df.index[i + 1].date())["stop"]
        assert (c <= s_next) == (c <= s_prev), (i, c, s_prev, s_next)


def test_a_session_cannot_breach_a_stop_it_just_raised():
    # whatever session s adds to the stop (its own trailing candidate or a new
    # ratchet tier) sits strictly below Close_s, so s cannot breach its own raise
    df, e, px, h, tr = setup(seed=9)
    raised = 0
    for i in range(len(df) - 80, len(df) - 1):
        before = build(h, {}, tr, lambda t, p: df, as_of=df.index[i].date())["stop"]
        after = build(h, {}, tr, lambda t, p: df, as_of=df.index[i + 1].date())["stop"]
        if after > before:
            raised += 1
            assert float(df["Close"].iloc[i]) > after
    assert raised > 0


@pytest.mark.parametrize("as_of,end", [
    (date(2026, 10, 31), "2026-10-30"),   # Saturday
    (date(2026, 11, 1), "2026-10-30"),    # Sunday
    (date(2026, 11, 26), "2026-11-25"),   # Thanksgiving
    (date(2026, 11, 27), "2026-11-25"),   # day after the holiday
])
def test_weekend_and_holiday_as_of_are_full(as_of, end):
    df = walk(end=end)
    e = df.index[-120].date()
    px = float(df["Close"].iloc[-120])
    h = [{"Ticker": "AAA", "Shares Exact": 10.0, "Avg Cost ($)": px}]
    r = build(h, {}, journal("AAA", 10.0, px, e), lambda t, p: df, as_of=as_of)
    assert r["basis"] == "full", r["note"]


def test_trail_start_boundary(monkeypatch):
    df, e, px, h, tr = setup(seed=5)
    k = len(df) - 30
    ts = df.index[k].date()
    monkeypatch.setattr(C, "HELD_TRAIL_START", ts.isoformat())
    at = build(h, {}, tr, lambda t, p: df, as_of=ts)
    assert at["components"]["trailing"] is None and at["components"]["ratchet"] is None
    nxt = build(h, {}, tr, lambda t, p: df, as_of=df.index[k + 1].date())
    pe = int(df.index.searchsorted(pd.Timestamp(e)))
    bars = df.iloc[pe - 60:k + 1]
    atr = hs._atr_series(bars["High"], bars["Low"], bars["Close"], 14)
    want = float(bars["Close"].iloc[-1]) - C.HELD_TRAIL_ATR_MULT * float(atr.iloc[-1])
    assert nxt["components"]["trailing"] == pytest.approx(want)


# ── labels ───────────────────────────────────────────────────────────────────
def test_labels():
    assert hw.label_for("full", "entry") == "Entry Stop"
    assert hw.label_for("full", "trailing") == "Trailing Stop"
    assert hw.label_for("full", "ratchet", "Protect 10% gain") == "Protect 10% gain"
    assert hw.label_for("partial", "trailing") == "Trailing Stop"
    assert hw.label_for("prior_session", "prior_session") == "Prior-close ATR"
    assert hw.label_for("unavailable", None) == "Stop Unavailable"


def test_every_result_label_is_allowed_and_never_doubles_stop():
    allowed = {"Entry Stop", "Trailing Stop", "Prior-close ATR", "Fallback Stop", "Stop Unavailable"} | {
        lab for _, _, lab in C.STOP_RATCHET_LEVELS}
    df, e, px, h, tr = setup(seed=11)
    for i in range(len(df) - 30, len(df) - 1):
        r = build(h, {}, tr, lambda t, p: df, as_of=_next_bar_date(df, i))
        assert r["label"] in allowed
    for lab in allowed:
        assert not lab.lower().endswith("stop stop")


# ── brief_breached_set / held_sig / degraded ────────────────────────────────
def _pdf(rows):
    return pd.DataFrame(rows, columns=["Ticker", "Price", "Stop", "Gap to Stop (%)"])


def test_brief_breached_set_uses_the_apps_rule():
    rows = [("A", 100.0, 101.0, -1.0), ("B", 100.0, 100.0, 0.0),
            ("C", 100.0, 100.04, -0.04),     # gap -0.04% rounds to -0.0 -> breached
            ("D", 100.0, 99.0, 1.0), ("E", 100.0, float("nan"), float("nan")),
            ("F", 100.0, 120.0, float("nan"))]          # NaN gap = data issue
    assert hw.brief_breached_set(_pdf(rows)) == {"A", "B", "C"}
    # a +0.04% gap rounds to 0.0 (GAP_TO_STOP_ROUND_DECIMALS = 1): breached too
    assert hw.brief_breached_set(_pdf([("G", 100.0, 99.96, 0.04)])) == {"G"}
    assert hw.brief_breached_set(None) == set()
    assert hw.brief_breached_set(pd.DataFrame()) == set()
    assert hw.brief_breached_set(pd.DataFrame({"Ticker": ["A"]})) == set()


def test_held_sig_changes_with_stop_or_basis_only():
    a = pd.DataFrame({"Ticker": ["A", "B"], "Stop": [10.004, 20.0],
                      "Stop Basis": ["full", "partial"]})
    assert hw.held_sig(a) == hw.held_sig(a.copy())
    b = a.copy()
    b.loc[0, "Stop"] = 10.2
    assert hw.held_sig(a) != hw.held_sig(b)
    c = a.copy()
    c.loc[1, "Stop Basis"] = "full"
    assert hw.held_sig(a) != hw.held_sig(c)
    assert hw.held_sig(None) == frozenset() and hw.held_sig(pd.DataFrame()) == frozenset()
    hash(hw.held_sig(a))
    assert hw.held_sig(pd.DataFrame({"Ticker": ["A"]})) == frozenset({("A", None, None)})


def test_degraded_and_notes():
    assert hw.degraded({"basis": "partial"}) and hw.degraded({"basis": "unavailable"})
    assert hw.degraded({"basis": "prior_session"}) and hw.degraded(None)
    assert not hw.degraded({"basis": "full"})
    rows = hw.degraded_notes({"B": {"basis": "partial", "label": "Trailing Stop", "note": "n"},
                              "A": {"basis": "full"}})
    assert rows == [("B", "Trailing Stop", "n")]
    assert hw.degraded_notes(None) == []


# ── manual stop rule ─────────────────────────────────────────────────────────
def test_manual_applies_over_unavailable_auto_stop():
    assert hs.compose_with_manual(None, 50.0) == (50.0, True)
    assert hw.analysis_manual_override(None, {"stop_price": 50.126}) == (50.13, True)
    assert hw.analysis_manual_override(None, None) == (None, False)
    assert hw.analysis_manual_override(None, {"stop_price": 0}) == (None, False)
    # unchanged when an auto stop exists
    assert hw.analysis_manual_override(100.0, {"stop_price": 99.0}) == (100.0, False)
    assert hw.analysis_manual_override(100.0, 101.0) == (101.0, True)


@pytest.mark.parametrize("auto,manual", [(100.0, 99.0), (100.0, 100.0), (100.0, 101.5),
                                         (None, 40.0), (None, None), (100.0, None)])
def test_analysis_override_parity_with_compose(auto, manual):
    assert hw.analysis_manual_override(auto, {"stop_price": manual}) == \
        hs.compose_with_manual(auto, manual)


# ── view ─────────────────────────────────────────────────────────────────────
def test_what_if_never_below_current_auto_stop():
    df, e, px, h, tr = setup(seed=3)
    r = build(h, {}, tr, lambda t, p: df)
    last = float(df["Close"].iloc[-1])
    for mult in (0.5, 0.8, 0.95, 1.0, 1.05, 1.2, 1.6, 2.5):
        w = hv.what_if(r, last * mult, px)
        assert w is not None and w["stop"] >= r["stop"]
        assert w["raised"] == (w["stop"] > r["stop"])
    assert hv.what_if(r, last * 3, px)["raised"] is True


def test_what_if_none_when_unusable():
    assert hv.what_if(None, 10, 10) is None
    assert hv.what_if({"stop": 5.0, "atr_last": 1.0, "basis": "prior_session"}, 10, 10) is None
    assert hv.what_if({"stop": None, "atr_last": 1.0, "basis": "full"}, 10, 10) is None
    assert hv.what_if({"stop": 5.0, "atr_last": 1.0, "basis": "full", "components": {}}, -1, 10) is None


def test_component_rows_plain_text():
    df, e, px, h, tr = setup(seed=3)
    r = build(h, {}, tr, lambda t, p: df)
    rows = hv.component_rows(r)
    assert rows and rows == sorted(rows, key=lambda x: -x["value"])
    assert max(x["value"] for x in rows) == r["stop"]
    assert all("Stop stop" not in x["text"] for x in rows)


# ── buy day (owner decision 2026-10-30, second): fill - ATR_STOP_MULT x ATR ───
def _atr_before(df, as_of, wb=60):
    """Independent reference: ATR at the last completed bar, seeded wb bars back."""
    bars = hs.completed_bars(df, as_of)
    sub = bars.iloc[len(bars) - wb:]
    return float(hs._atr_series(sub["High"], sub["Low"], sub["Close"], 14).iloc[-1])


def _buy_today(fill=None, held_stop=50.0, seed=2, price=None):
    df = walk(n=200, seed=seed)
    fill = float(df["Close"].iloc[-1]) if fill is None else fill
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    held = {"AAA": {"df": df, "stop": held_stop}}
    return df, trades, h, held, fill


def test_buy_day_stop_is_fill_minus_entry_mult_times_last_completed_atr():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _fetch_down)
    want = round(fill - C.ATR_STOP_MULT * _atr_before(df, AS_OF), 2)
    assert r["buy_day"] and r["basis"] == "buy_day" and r["label"] == "Entry Stop"
    assert r["stop"] == want and r["stop"] < fill
    assert r["atr_last"] == pytest.approx(_atr_before(df, AS_OF))


def test_buy_day_stop_equals_the_next_sessions_entry_component():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _fetch_down)
    # the D bar completes inside the fill's range; next session = the following business day
    d = pd.Timestamp(AS_OF)
    full = pd.concat([df, pd.DataFrame({"Open": fill, "High": fill * 1.01, "Low": fill * 0.99,
                                        "Close": fill}, index=[d])])
    nxt = date(2026, 11, 2)
    r2 = build(h, {}, trades, lambda t, p: full, as_of=nxt)
    assert r2["basis"] == "full" and not r2["buy_day"]
    assert r2["components"]["entry"] is not None
    assert round(r2["components"]["entry"], 2) == r["stop"]


def test_buy_day_gap_down_with_no_today_bar_is_not_breached():
    # the owner rule: a genuine gap-down fill must not produce a same-day sell-all even
    # when the (day-cached) history has no bar for today, so the fill can't be range-checked
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 0.80
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    r = build(h, {"AAA": {"df": df}}, trades, _fetch_down)
    port = pf.build_portfolio_df(h, {"AAA": {"current_price": fill, "stop": 1.0, "sector": "Technology",
                                             "rec": {"icon": "🟢", "label": "Buy"}, "total": 70}},
                                 held_stops={"AAA": r})
    assert r["stop"] < fill and hw.brief_breached_set(port) == set()
    assert r["basis"] == "prior_session" and r["degraded"]
    assert r["label"] == "Entry Stop" and r["binding"] == "entry"
    assert "couldn't be checked against today's trading range yet" in r["note"]
    assert "fill-based level applies" in r["note"] and "does not trail" in r["note"]


def test_buy_day_gap_down_with_a_today_bar_is_not_breached():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 0.80                                  # gapped down 20% and bought there
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    # today's (partial) bar exists and covers the gapped-down fill, as it does live
    r = build(h, {"AAA": {"df": _with_today(df, fill * 0.99, fill * 1.01)}}, trades, _fetch_down)
    port = pf.build_portfolio_df(h, {"AAA": {"current_price": fill, "stop": 1.0, "sector": "Technology",
                                             "rec": {"icon": "🟢", "label": "Buy"}, "total": 70}},
                                 held_stops={"AAA": r})
    assert r["stop"] < fill and hw.brief_breached_set(port) == set()


def test_buy_day_same_day_drop_beyond_the_entry_distance_is_breached():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _fetch_down)
    atr = _atr_before(df, AS_OF)
    price = fill - (C.ATR_STOP_MULT + 0.5) * atr        # fell more than ATR_STOP_MULT x ATR below the fill
    port = pf.build_portfolio_df(h, {"AAA": {"current_price": price, "stop": 1.0, "sector": "Technology",
                                             "rec": {"icon": "🟢", "label": "Buy"}, "total": 70}},
                                 held_stops={"AAA": r})
    assert hw.brief_breached_set(port) == {"AAA"}


def test_buy_day_ignores_the_bundle_stop_entirely():
    df, trades, h, held, fill = _buy_today(held_stop=50.0)
    a = build(h, held, trades, _fetch_down)
    b = build(h, {"AAA": {"df": df, "stop": fill * 0.999}}, trades, _fetch_down)
    c = build(h, {"AAA": {"df": df}}, trades, _fetch_down)
    assert a["stop"] == b["stop"] == c["stop"]


def test_buy_day_missing_fill_falls_back_to_a_degraded_prior_session():
    df = walk(n=200, seed=2)
    trades = journal("AAA", 10.0, 0.0, AS_OF)           # no usable fill price
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": 100.0}]
    r = build(h, {"AAA": {"df": df}}, trades, _fetch_down)
    assert r["basis"] == "prior_session" and not r["buy_day"] and r["degraded"]
    assert r["label"] == "Prior-close ATR" and r["note"]
    assert hw.degraded_notes({"AAA": r})


def test_buy_day_with_too_little_history_falls_back_too():
    df = walk(n=40, seed=2)                             # fewer than the warm-up bars
    fill = float(df["Close"].iloc[-1])
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    r = build(h, {"AAA": {"df": df}}, trades, _fetch_down)
    assert not r["buy_day"] and r["degraded"]


def test_buy_day_is_not_degraded_and_has_the_calm_note():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _fetch_down)
    assert r["degraded"] is False and hw.degraded(r) is False
    assert hw.degraded_notes({"AAA": r}) == []
    bs = chr(92)
    assert r["note"] == (f"Bought today: the stop is your fill price {bs}${fill:.2f} minus "
                         f"{C.ATR_STOP_MULT:g} × ATR (from the last completed session). From the "
                         "next session it is rebuilt from completed sessions: the trailing and "
                         "profit-lock parts can raise it, or it can come out lower if a later session "
                         "has to use a fallback basis.")
    assert "only moves up" not in r["note"].lower()
    row = {"Auto Stop": r["stop"], "Stop Basis": "buy_day", "Stop Note": r["note"],
           "Stop Type": "Entry Stop", "Stop Type Auto": "Entry Stop"}
    assert hv.basis_caption(row).startswith("ℹ️ Bought today")
    assert hv.what_if(r, fill * 1.1, fill) is None


def test_entry_anchor_atr_matches_c1s_own_entry_anchor():
    # the helper must reproduce the ATR C1 uses for the entry component of an
    # episode that started on a bar of the frame
    df = walk(n=200, seed=4)
    E = df.index[150].date()
    fill = float(df["Close"].iloc[150])
    ep = {"status": "ok", "start": E, "first_buy_price": fill, "split_ratio": 1.0,
          "reason": "", "last_buy": E}
    pol = dict(initial_mult=C.ATR_STOP_MULT, trail_mult=C.HELD_TRAIL_ATR_MULT, trail_start=None,
               ratchet_levels=C.STOP_RATCHET_LEVELS, warmup_bars=60, min_bars=15, fill_tol_pct=50.0)
    res = hs.compute_held_stop(df, ep, fill, df.index[160].date(), **pol)
    atr = hs.entry_anchor_atr(hs.completed_bars(df, df.index[160].date()), E, 60)
    assert res["components"]["entry"] == pytest.approx(fill - C.ATR_STOP_MULT * atr)
    assert hs.entry_anchor_atr(hs.completed_bars(df.iloc[:30], df.index[40].date()), E, 60) is None


def test_buy_day_not_a_lane_failure_input():
    # an all-buy-day book is not judged "every name prior_session"
    assert hw.degraded({"basis": "buy_day"}) is False


def test_unavailable_result_is_public_and_degraded():
    u = hw.unavailable_result("ZZZ", "Stop could not be computed (X).")
    assert u["basis"] == "unavailable" and u["label"] == "Stop Unavailable" and u["degraded"]
    assert hw.degraded_notes({"ZZZ": u}) == [("ZZZ", "Stop Unavailable", "Stop could not be computed (X).")]


def test_basis_caption_manual_over_unavailable_automatic_stop():
    from stock_analyzer import held_stop_view as v
    row = {"Auto Stop": float("nan"), "Stop Basis": "unavailable", "Stop Note": "No stop could be built",
           "Stop Type": "Manual"}
    assert v.basis_caption(row) == "⚠️ No automatic stop could be built; your manual stop applies."


def test_neg_cache_active_window():
    from stock_analyzer import held_stop_view as v
    assert v.neg_cache_active(1000.0, 1000.0 + C.HELD_STOP_UNADJ_NEG_CACHE_SEC - 1)
    assert not v.neg_cache_active(1000.0, 1000.0 + C.HELD_STOP_UNADJ_NEG_CACHE_SEC)
    assert not v.neg_cache_active(None, 5.0) and C.HELD_STOP_UNADJ_NEG_CACHE_SEC == 300


# ── buy-day fill sanity check + unadjusted-frame preference (2026-11-01) ──────
def _with_today(df, lo, hi, as_of=AS_OF):
    px = (lo + hi) / 2
    row = pd.DataFrame({"Open": px, "High": hi, "Low": lo, "Close": px},
                       index=[pd.Timestamp(as_of)])
    return pd.concat([df, row])


def _buy_run(fill, long_df, bundle_df, seed=2):
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    held = {"AAA": {"df": bundle_df}} if bundle_df is not None else {}

    def fetch(t, p):
        if long_df is None:
            raise RuntimeError("down")
        return long_df
    return build(h, held, trades, fetch)


def test_buy_day_fill_inside_todays_range_is_accepted_unchanged_value():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 1.12                        # far from prev close, inside today's range
    full = _with_today(df, prev * 1.10, prev * 1.14)
    r = _buy_run(fill, full, df)
    assert r["basis"] == "buy_day" and not r["degraded"]
    assert r["stop"] == round(fill - C.ATR_STOP_MULT * _atr_before(df, AS_OF), 2)


def test_buy_day_unrecorded_split_fill_is_rejected_and_falls_back():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    full = _with_today(df, prev * 0.99, prev * 1.01)
    r = _buy_run(prev * 0.5, full, df)        # a 2:1 split not recorded: fill half the price
    assert r["basis"] == "prior_session" and r["degraded"] and not r["buy_day"]
    assert ("your recorded fill price is outside today's trading range and the prior "
            "close (a mis-keyed fill or an unrecorded split)") in r["note"]
    assert "$" not in r["note"].replace(chr(92) + "$", "")   # every dollar sign is escaped


def test_buy_day_range_only_in_the_bundle_frame_widens_the_band():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 1.12
    stale_long = df                            # cached fetch: no as_of row yet
    bundle = _with_today(df, prev * 1.10, prev * 1.14)
    r = _buy_run(fill, stale_long, bundle)
    assert r["basis"] == "buy_day"


def test_buy_day_no_session_row_anywhere_uses_prev_close_band():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    tol = C.HELD_STOP_FILL_TOL_PCT
    ok = _buy_run(prev * (1 + tol / 100.0 * 0.5), df, df)
    assert ok["basis"] == "buy_day"
    bad = _buy_run(prev * (1 + tol / 100.0 * 1.5) + 0.01, df, df)
    assert bad["basis"] == "prior_session" and bad["degraded"]


def test_buy_day_atr_comes_from_the_unadjusted_frame_not_the_bundle():
    bundle = walk(n=200, seed=2)
    wide = bundle.copy()
    wide["High"] = wide["Close"] * 1.03
    wide["Low"] = wide["Close"] * 0.97
    fill = float(bundle["Close"].iloc[-1])
    r = _buy_run(fill, wide, bundle)
    assert r["basis"] == "buy_day"
    assert r["atr_last"] == pytest.approx(_atr_before(wide, AS_OF))
    assert r["atr_last"] != pytest.approx(_atr_before(bundle, AS_OF))


def test_buy_day_unadjusted_fetch_failure_uses_the_bundle():
    bundle = walk(n=200, seed=2)
    fill = float(bundle["Close"].iloc[-1])
    r = _buy_run(fill, None, bundle)
    assert r["basis"] == "buy_day"
    assert r["atr_last"] == pytest.approx(_atr_before(bundle, AS_OF))


def test_buy_day_accepted_note_names_the_lower_possibility():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _fetch_down)
    assert "or it can come out lower if a later session has to use a fallback basis" in r["note"]
    assert not r["note"].endswith("can raise it.")


def _breached_at(h, r, price):
    port = pf.build_portfolio_df(h, {"AAA": {"current_price": price, "stop": 1.0, "sector": "Technology",
                                             "rec": {"icon": "🟢", "label": "Buy"}, "total": 70}},
                                 held_stops={"AAA": r})
    return hw.brief_breached_set(port)


@pytest.mark.parametrize("with_today", [False, True])
def test_buy_day_fill_keyed_10x_too_high_is_never_a_breach_near_prior_close(with_today):
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 10.0
    held_df = _with_today(df, prev * 0.99, prev * 1.01) if with_today else df
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    r = build(h, {"AAA": {"df": held_df}}, trades, _fetch_down)
    # a 10x fill is rejected; the stop is the prior-close leg, so a price near the prior close holds
    assert r["basis"] == "prior_session" and r["degraded"]
    assert r["label"] == ("Fallback Stop" if with_today else "Prior-close ATR")
    assert r["stop"] < prev
    assert _breached_at(h, r, prev) == set()
    assert ("outside today's trading range" in r["note"]) is with_today
    assert ("couldn't be checked against today's trading range yet" in r["note"]) is (not with_today)
    assert ("range-based level applies" in r["note"]) is with_today
    assert ("prior-close level applies" in r["note"]) is (not with_today)


def test_buy_day_binding_labels_follow_the_lower_leg():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    low = _buy_run(prev * 0.80, df, df)          # fill leg below the prior-close leg
    assert low["label"] == "Entry Stop" and "fill-based level applies" in low["note"]
    high = _buy_run(prev * 10.0, df, df)         # prior-close leg below the fill leg
    assert high["label"] == "Prior-close ATR" and "prior-close level applies" in high["note"]


def test_buy_day_stale_long_frame_is_degraded_with_the_history_end_date():
    df = walk(n=200, seed=2, end="2026-10-27")     # ends two sessions early: stale
    fill = float(df["Close"].iloc[-1])
    r = _buy_run(fill, df, None)
    assert r["basis"] == "partial" and r["degraded"] and r["fell_back"] and not r["buy_day"]
    assert r["stop"] == round(fill - C.ATR_STOP_MULT * _atr_before(df, AS_OF), 2)
    assert r["label"] == "Entry Stop" and r["components"]["entry"] == r["stop"]
    assert "The price history ends on 2026-10-27" in r["note"] and "old data" in r["note"]
    assert hw.degraded_notes({"AAA": r})


def test_buy_day_accepted_note_has_no_next_session_check_claim():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _fetch_down)
    assert "or it can come out lower if a later session has to use a fallback basis" in r["note"]
    assert "rejects your recorded fill" not in r["note"]


def _prices(df, lo, hi):
    return _with_today(df, lo, hi)


def test_buy_day_third_leg_residual_today_bar_low_and_fill_keyed_10x_high():
    # reviewer's residual: today's bar at 0.85-0.87 x prior close, real fill 0.86 keyed 10x
    # too high, price at the real fill. Without the range leg the prior-close stop (above
    # the price) would breach.
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    real = prev * 0.86
    held_df = _prices(df, prev * 0.85, prev * 0.87)
    trades = journal("AAA", 10.0, real * 10.0, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": real * 10.0}]
    r = build(h, {"AAA": {"df": held_df}}, trades, _fetch_down)
    atr = _atr_before(df, AS_OF)
    want = round(prev * 0.85 * (1 - C.HELD_STOP_FILL_TOL_PCT / 100.0) - C.ATR_STOP_MULT * atr, 2)
    assert r["basis"] == "prior_session" and r["degraded"]
    assert r["binding"] == "range" and r["label"] == "Fallback Stop"
    assert r["stop"] == want and r["stop"] < real
    assert _breached_at(h, r, real) == set()
    assert "range-based level applies" in r["note"]
    assert "the lowest of" in r["note"] and "fallback level" in r["note"]
    assert "cautious" not in r["note"]


def test_buy_day_range_leg_on_a_gap_up_is_built_from_the_prior_close():
    # gap up: today's low is ABOVE the prior close, so the range leg comes from the
    # prior close, and the note must say "the lower of today's low and the prior
    # close" (not "the bottom of today's range"), with that leg's value
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    r = _buy_run(prev * 10.0, _prices(df, prev * 1.05, prev * 1.07), df)
    atr = _atr_before(df, AS_OF)
    want = round(prev * (1 - C.HELD_STOP_FILL_TOL_PCT / 100.0) - C.ATR_STOP_MULT * atr, 2)
    assert r["degraded"]
    assert "the lower of today's low and the prior close" in r["note"]
    assert f"(\\${want:.2f})" in r["note"]
    assert "bottom of today's range" not in r["note"]


def test_buy_day_range_leg_never_applies_without_a_today_bar():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    r = _buy_run(prev * 10.0, df, df)
    assert r["binding"] == "prior_session" and r["label"] == "Prior-close ATR"
    assert "today's low and the prior close" not in r["note"]


def test_buy_day_in_band_fill_is_unchanged_by_the_range_leg():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 0.86
    r = _buy_run(fill, _prices(df, prev * 0.85, prev * 0.87), df)
    assert r["basis"] == "buy_day" and not r["degraded"]
    assert r["stop"] == round(fill - C.ATR_STOP_MULT * _atr_before(df, AS_OF), 2)


def test_stop_noun_names_the_fallback_label_truthfully():
    from stock_analyzer.stop_breach_text import stop_noun
    assert stop_noun("Fallback Stop") == "fallback stop"


def test_plain_text_note_drops_the_markdown_escape_and_the_email_footer_has_none():
    from stock_analyzer import notify
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    r = _buy_run(prev * 0.5, _prices(df, prev * 0.99, prev * 1.01), df)
    assert chr(92) + "$" in r["note"]                      # the app needs the escape
    line = f"AAA ({r['label']}): {r['note']}"
    plain = hw.plain_text_note(line)
    assert chr(92) + "$" not in plain and "$" in plain
    _, body = notify.render_alert_email([{"ticker": "AAA", "kind": "stop_breach",
                                          "message": "x"}], "2026-11-02 08:00:00",
                                        footer_lines=[plain])
    assert chr(92) + "$" not in body


def test_cron_runner_builds_email_footer_notes_through_plain_text_note():
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "cron_runner.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "plain_text_note"]
    assert calls
    assert "list(payload.get(\"held_stop_notes\", []))" not in src


# ── fetch_range: today's High / Low when no history frame has a today row ─────
def build_r(h, held, trades, fetch, fetch_range, as_of=AS_OF):
    return hw.build_held_stops(h, held, trades, as_of, fetch_history=fetch,
                               fetch_range=fetch_range)["AAA"]


def _range_case():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 1.12                         # far from the prior close, inside today's range
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    return df, prev, fill, trades, h


def test_fetch_range_confirms_a_fill_when_no_frame_has_a_today_row():
    df, prev, fill, trades, h = _range_case()
    calls = []

    def rng(t, d):
        calls.append((t, d))
        return {"low": prev * 1.10, "high": prev * 1.14}
    r = build_r(h, {"AAA": {"df": df}}, trades, _fetch_down, rng)
    ref = build_r(h, {"AAA": {"df": _with_today(df, prev * 1.10, prev * 1.14)}}, trades,
                  _fetch_down, None)
    assert calls == [("AAA", AS_OF)]
    assert r["basis"] == "buy_day" and not r["degraded"] and r["binding"] == "entry"
    assert r["stop"] == round(fill - C.ATR_STOP_MULT * _atr_before(df, AS_OF), 2)
    assert (r["stop"], r["basis"], r["binding"], r["note"]) == \
        (ref["stop"], ref["basis"], ref["binding"], ref["note"])


def test_fetch_range_excluding_the_fill_is_rejected_with_a_range_leg():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 10.0
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    r = build_r(h, {"AAA": {"df": df}}, trades, _fetch_down,
                lambda t, d: {"low": prev * 0.99, "high": prev * 1.01})
    ref = build_r(h, {"AAA": {"df": _with_today(df, prev * 0.99, prev * 1.01)}}, trades,
                  _fetch_down, None)
    assert r["basis"] == "prior_session" and r["degraded"] and not r["buy_day"]
    assert "outside today's trading range" in r["note"]
    assert "couldn't be checked against today's trading range yet" not in r["note"]
    assert "today's low and the prior close" in r["note"]
    assert r["label"] == "Fallback Stop" and r["binding"] == "range"
    assert (r["stop"], r["binding"], r["note"]) == (ref["stop"], ref["binding"], ref["note"])


def _raises(t, d):
    raise RuntimeError("range down")


@pytest.mark.parametrize("bad", [
    _raises,
    lambda t, d: None,
    lambda t, d: {"low": float("nan"), "high": 1},
    lambda t, d: {"low": 0, "high": 5},
    lambda t, d: {"low": 10, "high": 5},
    lambda t, d: {"low": float("inf"), "high": float("inf")},
    lambda t, d: {"low": "x", "high": 5},
    lambda t, d: {"high": 5},
    lambda t, d: [1, 2],
])
def test_fetch_range_unusable_result_equals_no_fetch_range(bad):
    df, prev, fill, trades, h = _range_case()
    got = build_r(h, {"AAA": {"df": df}}, trades, _fetch_down, bad)
    base = build_r(h, {"AAA": {"df": df}}, trades, _fetch_down, None)
    assert (got["stop"], got["basis"], got["binding"], got["note"]) == \
        (base["stop"], base["basis"], base["binding"], base["note"])
    assert got["basis"] == "prior_session" and got["degraded"]


def _must_not_call(t, d):
    raise AssertionError("fetch_range must not be called")


def test_fetch_range_not_called_when_a_frame_has_a_today_row():
    df, prev, fill, trades, h = _range_case()
    held = {"AAA": {"df": _with_today(df, prev * 1.10, prev * 1.14)}}
    r = build_r(h, held, trades, _fetch_down, _must_not_call)
    assert r["basis"] == "buy_day" and not r["degraded"]


def test_fetch_range_not_called_when_it_is_not_a_buy_day():
    df, e, px, h, tr = setup()
    r = build_r(h, {"AAA": {"df": df}}, tr, lambda t, p: df, _must_not_call)
    assert r["basis"] == "full"


def test_fetch_range_not_called_when_the_fill_is_unusable():
    df = walk(n=200, seed=2)
    trades = journal("AAA", 10.0, 0.0, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": 100.0}]
    r = build_r(h, {"AAA": {"df": df}}, trades, _fetch_down, _must_not_call)
    assert r["basis"] == "prior_session" and not r["buy_day"]


# ── session_range_unadjusted / fetch_session_range ───────────────────────────
def _fake_history(monkeypatch, frame, seen=None):
    from stock_analyzer.providers import yfinance_provider as yp

    class FakeTicker:
        def __init__(self, t):
            if seen is not None:
                seen["t"] = t

        def history(self, **kw):
            if seen is not None:
                seen["kw"] = kw
            return frame.copy()
    monkeypatch.setattr(yp.yf, "Ticker", FakeTicker)
    return yp.YFinanceProvider()


def _et_frame(rows):
    """rows: [(ET timestamp str, low, high, close)] on a tz-aware ET index."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows]).tz_localize("America/New_York")
    return pd.DataFrame({"Open": 1.0, "High": [r[2] for r in rows], "Low": [r[1] for r in rows],
                         "Close": [r[3] for r in rows]}, index=idx)


def test_session_range_returns_low_high_of_a_nan_close_row(monkeypatch):
    seen = {}
    frame = _et_frame([("2026-11-06 00:00", 168.0, 172.0, 170.0),
                       ("2026-11-09 00:00", 170.24, 179.91, float("nan"))])
    prov = _fake_history(monkeypatch, frame, seen)
    out = prov.session_range_unadjusted("NVDA", date(2026, 11, 9))
    assert out == {"low": 170.24, "high": 179.91}
    assert seen["t"] == "NVDA" and seen["kw"] == {"period": "5d", "auto_adjust": False}


def test_session_range_none_when_no_row_for_the_date(monkeypatch):
    frame = _et_frame([("2026-11-06 00:00", 168.0, 172.0, 170.0)])
    prov = _fake_history(monkeypatch, frame)
    assert prov.session_range_unadjusted("NVDA", date(2026, 11, 9)) is None
    prov = _fake_history(monkeypatch, pd.DataFrame())
    assert prov.session_range_unadjusted("NVDA", date(2026, 11, 9)) is None


@pytest.mark.parametrize("lo,hi", [(float("nan"), 5.0), (1.0, float("nan")), (0.0, 5.0),
                                   (-1.0, 5.0), (9.0, 5.0)])
def test_session_range_none_for_unusable_low_high(monkeypatch, lo, hi):
    frame = _et_frame([("2026-11-09 00:00", lo, hi, float("nan"))])
    prov = _fake_history(monkeypatch, frame)
    assert prov.session_range_unadjusted("NVDA", date(2026, 11, 9)) is None


def test_session_range_matches_on_the_et_date_not_the_utc_date(monkeypatch):
    # a bar stamped 2026-11-09 20:00 ET is 2026-11-10 01:00 UTC: it is the 11-09 session
    idx = pd.DatetimeIndex(["2026-11-10 01:00"]).tz_localize("UTC")
    frame = pd.DataFrame({"Open": 1.0, "High": 12.0, "Low": 10.0, "Close": float("nan")}, index=idx)
    prov = _fake_history(monkeypatch, frame)
    assert prov.session_range_unadjusted("X", date(2026, 11, 9)) == {"low": 10.0, "high": 12.0}
    assert prov.session_range_unadjusted("X", date(2026, 11, 10)) is None
    # and the mirror case: a bar at 2026-11-09 23:00 ET stays on 11-09
    frame2 = _et_frame([("2026-11-09 23:00", 10.0, 12.0, float("nan"))])
    prov = _fake_history(monkeypatch, frame2)
    assert prov.session_range_unadjusted("X", date(2026, 11, 9)) == {"low": 10.0, "high": 12.0}
    assert prov.session_range_unadjusted("X", date(2026, 11, 10)) is None


def test_session_range_tz_naive_index_is_taken_as_exchange_local(monkeypatch):
    idx = pd.DatetimeIndex(["2026-11-09 00:00"])
    frame = pd.DataFrame({"Open": 1.0, "High": 12.0, "Low": 10.0, "Close": float("nan")}, index=idx)
    prov = _fake_history(monkeypatch, frame)
    assert prov.session_range_unadjusted("X", date(2026, 11, 9)) == {"low": 10.0, "high": 12.0}


def test_session_range_several_rows_for_one_date_use_min_low_max_high(monkeypatch):
    frame = _et_frame([("2026-11-09 09:30", 11.0, 12.0, float("nan")),
                       ("2026-11-09 15:00", 10.0, 11.5, float("nan"))])
    prov = _fake_history(monkeypatch, frame)
    assert prov.session_range_unadjusted("X", date(2026, 11, 9)) == {"low": 10.0, "high": 12.0}


def test_session_range_raises_on_transport_failure(monkeypatch):
    from stock_analyzer.providers import yfinance_provider as yp

    class Boom:
        def __init__(self, t):
            pass

        def history(self, **kw):
            raise RuntimeError("down")
    monkeypatch.setattr(yp.yf, "Ticker", Boom)
    with pytest.raises(RuntimeError):
        yp.YFinanceProvider().session_range_unadjusted("X", date(2026, 11, 9))


def test_fetch_session_range_never_raises(monkeypatch):
    prim = data_mod._PRIMARY

    def _raise(t, d):
        raise RuntimeError("boom")
    monkeypatch.setattr(prim, "session_range_unadjusted", _raise)
    assert data_mod.fetch_session_range("X", date(2026, 11, 9)) is None
    monkeypatch.setattr(prim, "session_range_unadjusted", lambda t, d: {"low": 1.0, "high": 2.0})
    assert data_mod.fetch_session_range("X", date(2026, 11, 9)) == {"low": 1.0, "high": 2.0}
    monkeypatch.setattr(prim, "session_range_unadjusted", lambda t, d: None)
    assert data_mod.fetch_session_range("X", date(2026, 11, 9)) is None
