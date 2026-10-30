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
    allowed = {"Entry Stop", "Trailing Stop", "Prior-close ATR", "Stop Unavailable"} | {
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
    r = build(h, held, trades, _boom)
    want = round(fill - C.ATR_STOP_MULT * _atr_before(df, AS_OF), 2)
    assert r["buy_day"] and r["basis"] == "buy_day" and r["label"] == "Entry Stop"
    assert r["stop"] == want and r["stop"] < fill
    assert r["atr_last"] == pytest.approx(_atr_before(df, AS_OF))


def test_buy_day_stop_equals_the_next_sessions_entry_component():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _boom)
    # the D bar completes inside the fill's range; next session = the following business day
    d = pd.Timestamp(AS_OF)
    full = pd.concat([df, pd.DataFrame({"Open": fill, "High": fill * 1.01, "Low": fill * 0.99,
                                        "Close": fill}, index=[d])])
    nxt = date(2026, 11, 2)
    r2 = build(h, {}, trades, lambda t, p: full, as_of=nxt)
    assert r2["basis"] == "full" and not r2["buy_day"]
    assert r2["components"]["entry"] is not None
    assert round(r2["components"]["entry"], 2) == r["stop"]


def test_buy_day_gap_down_before_the_fill_is_not_breached():
    df = walk(n=200, seed=2)
    prev = float(df["Close"].iloc[-1])
    fill = prev * 0.80                                  # gapped down 20% and bought there
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    r = build(h, {"AAA": {"df": df}}, trades, _boom)
    port = pf.build_portfolio_df(h, {"AAA": {"current_price": fill, "stop": 1.0, "sector": "Technology",
                                             "rec": {"icon": "🟢", "label": "Buy"}, "total": 70}},
                                 held_stops={"AAA": r})
    assert r["stop"] < fill and hw.brief_breached_set(port) == set()


def test_buy_day_same_day_drop_beyond_the_entry_distance_is_breached():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _boom)
    atr = _atr_before(df, AS_OF)
    price = fill - (C.ATR_STOP_MULT + 0.5) * atr        # fell more than ATR_STOP_MULT x ATR below the fill
    port = pf.build_portfolio_df(h, {"AAA": {"current_price": price, "stop": 1.0, "sector": "Technology",
                                             "rec": {"icon": "🟢", "label": "Buy"}, "total": 70}},
                                 held_stops={"AAA": r})
    assert hw.brief_breached_set(port) == {"AAA"}


def test_buy_day_ignores_the_bundle_stop_entirely():
    df, trades, h, held, fill = _buy_today(held_stop=50.0)
    a = build(h, held, trades, _boom)
    b = build(h, {"AAA": {"df": df, "stop": fill * 0.999}}, trades, _boom)
    c = build(h, {"AAA": {"df": df}}, trades, _boom)
    assert a["stop"] == b["stop"] == c["stop"]


def test_buy_day_missing_fill_falls_back_to_a_degraded_prior_session():
    df = walk(n=200, seed=2)
    trades = journal("AAA", 10.0, 0.0, AS_OF)           # no usable fill price
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": 100.0}]
    r = build(h, {"AAA": {"df": df}}, trades, _boom)
    assert r["basis"] == "prior_session" and not r["buy_day"] and r["degraded"]
    assert r["label"] == "Prior-close ATR" and r["note"]
    assert hw.degraded_notes({"AAA": r})


def test_buy_day_with_too_little_history_falls_back_too():
    df = walk(n=40, seed=2)                             # fewer than the warm-up bars
    fill = float(df["Close"].iloc[-1])
    trades = journal("AAA", 10.0, fill, AS_OF)
    h = [{"Ticker": "AAA", "Shares": 10, "Shares Exact": 10.0, "Avg Cost ($)": fill}]
    r = build(h, {"AAA": {"df": df}}, trades, _boom)
    assert not r["buy_day"] and r["degraded"]


def test_buy_day_is_not_degraded_and_has_the_calm_note():
    df, trades, h, held, fill = _buy_today()
    r = build(h, held, trades, _boom)
    assert r["degraded"] is False and hw.degraded(r) is False
    assert hw.degraded_notes({"AAA": r}) == []
    bs = chr(92)
    assert r["note"] == (f"Bought today: the stop is your fill price {bs}${fill:.2f} minus "
                         f"{C.ATR_STOP_MULT:g} × ATR (from the last completed session). From the "
                         "next session the trailing and profit-lock parts can raise it.")
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
