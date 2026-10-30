"""Pure-logic tests for stock_analyzer.held_stop (synthetic frames, no network/DB)."""
from datetime import date

import inspect

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import held_stop as hs
from stock_analyzer import risk
from stock_analyzer.constants import STOP_RATCHET_LEVELS
from stock_analyzer.portfolio import manual_stop_wins

P = dict(initial_mult=2.0, trail_mult=3.0, trail_start=None,
         ratchet_levels=STOP_RATCHET_LEVELS, warmup_bars=60, min_bars=15,
         fill_tol_pct=2.0)


def walk(n=220, seed=1, start="2025-01-02", drift=0.0005, vol=0.015, tz=None):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    idx = pd.bdate_range(start, periods=n)
    df = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                       "Close": close}, index=idx)
    return _retz(df, tz)


def _retz(df, tz):
    df = df.copy()
    if tz == "ET":
        df.index = df.index.tz_localize("America/New_York")
    elif tz == "UTC":  # 04:00 UTC == 00:00 ET during EDT; use summer-only data
        df.index = (df.index + pd.Timedelta(hours=4)).tz_localize("UTC")
    return df


def ep(start, price, ratio=1.0):
    return {"status": "ok", "start": start, "first_buy_price": price,
            "split_ratio": ratio, "reason": ""}


def run(df, as_of, episode, avg_cost, **over):
    return hs.compute_held_stop(df, episode, avg_cost, as_of, **{**P, **over})


def d(df, i):
    return df.index[i].date()


# ── completed_bars / no today leakage ────────────────────────────────────────
@pytest.mark.parametrize("tz", [None, "ET", "UTC"])
def test_no_today_bar_leakage(tz):
    df = walk(n=120, seed=3, start="2025-06-02", tz=tz)
    as_of = d(walk(n=120, seed=3, start="2025-06-02"), 100)
    e = ep(d(walk(n=120, seed=3, start="2025-06-02"), 20), 100.0)
    base = run(df, as_of, e, 100.0)
    mutated = df.copy()
    mutated.iloc[100:, :] = [1.0, 1e6, 1e-6, 1e-3]  # crash/spike today and later
    extra = mutated.iloc[[100]].copy()
    extra.index = extra.index + pd.Timedelta(hours=4)  # pre-market row dated D
    mutated = pd.concat([mutated, extra]).sort_index()
    assert run(mutated, as_of, e, 100.0) == base


def test_completed_bars_drops_nan_and_does_not_mutate():
    df = walk(n=30, seed=2)
    df.iloc[5, df.columns.get_loc("Close")] = np.nan
    before = df.copy()
    out = hs.completed_bars(df, d(df, 20))
    assert len(out) == 19 and out.index.max() < pd.Timestamp(d(df, 20))
    pd.testing.assert_frame_equal(df, before)


# ── monotonic / breach / persistence / lemma ─────────────────────────────────
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_stop_never_falls_without_trades(seed):
    df = walk(n=260, seed=seed, drift=0.002)
    e = ep(d(df, 70), float(df["Close"].iloc[70]))
    avg = float(df["Close"].iloc[70])
    prev = None
    for i in range(140, 259):
        s = run(df, d(df, i), e, avg)["stop"]
        if prev is not None:
            assert s >= prev - 1e-9
        prev = s


def test_gap_down_breach_is_reachable_and_persists():
    df = walk(n=150, seed=7, vol=0.002, drift=0.0)
    e = ep(d(df, 70), float(df["Close"].iloc[70]))
    avg = float(df["Close"].iloc[70])
    D = d(df, 120)
    stop = run(df.iloc[:120], D, e, avg)["stop"]
    gap = float(df["Close"].iloc[119]) * 0.7
    assert gap <= stop
    # next session: that gap bar is now complete; still below the (unfallen) stop
    nxt = df.iloc[:120].copy()
    nxt.loc[pd.Timestamp(D)] = [gap, gap, gap, gap]
    stop2 = run(nxt, d(df, 121), e, avg)["stop"]
    assert stop2 >= stop and gap <= stop2


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_lemma_breach_today_iff_breach_tomorrow(seed):
    df = walk(n=240, seed=seed, drift=0.001, vol=0.02)
    e = ep(d(df, 70), float(df["Close"].iloc[70]))
    avg = float(df["Close"].iloc[70])
    for i in range(130, 238):
        c = float(df["Close"].iloc[i])
        s_p = run(df, d(df, i), e, avg)["stop"]
        s_n = run(df, d(df, i + 1), e, avg)["stop"]
        assert (c <= s_p) == (c <= s_n)


# ── episode ──────────────────────────────────────────────────────────────────
def trades(rows):
    return pd.DataFrame([
        {"id": i + 1, "ticker": t, "action": a, "shares": s, "price": p, "traded_at": ts}
        for i, (t, a, s, p, ts) in enumerate(rows)])


def test_episode_reentry_resets_partial_and_add_do_not():
    t = trades([
        ("X", "BUY", 10, 10.0, "2025-01-06T15:00:00+00:00"),
        ("X", "BUY", 5, 12.0, "2025-02-03T15:00:00+00:00"),   # add
        ("X", "SELL", 5, 13.0, "2025-02-10T15:00:00+00:00"),  # partial
        ("X", "SELL", 10, 14.0, "2025-03-03T15:00:00+00:00"),  # full exit
        ("X", "BUY", 7, 20.0, "2025-04-07T15:00:00+00:00"),   # re-entry
        ("Y", "BUY", 99, 1.0, "2025-04-08T15:00:00+00:00"),
    ])
    e = hs.episode_from_journal("x", t, 7, shares_tol=1e-4)
    assert e["status"] == "ok" and e["start"] == date(2025, 4, 7)
    assert e["first_buy_price"] == 20.0
    t2 = t.iloc[:3]
    e2 = hs.episode_from_journal("X", t2, 10, shares_tol=1e-4)
    assert e2["start"] == date(2025, 1, 6) and e2["first_buy_price"] == 10.0


def test_episode_dates_in_et_and_split_ratio():
    t = trades([
        ("X", "BUY", 10, 50.0, "2025-03-04T00:30:00+00:00"),  # = Mar 3, 19:30 ET
        ("X", "SPLIT", 20, 25.0, "2025-03-20T15:00:00+00:00"),
    ])
    e = hs.episode_from_journal("X", t, 20, shares_tol=1e-4)
    assert e["start"] == date(2025, 3, 3) and e["split_ratio"] == pytest.approx(2.0)


def test_episode_statuses_unreadable_empty_mismatch():
    none = hs.episode_from_journal("X", None, 5, shares_tol=1e-4)
    empty = hs.episode_from_journal("X", trades([]).reindex(columns=[]), 5, shares_tol=1e-4)
    t = trades([("X", "BUY", 3, 10.0, "2025-01-06T15:00:00+00:00")])
    mism = hs.episode_from_journal("X", t, 5, shares_tol=1e-4)
    assert (none["status"], empty["status"], mism["status"]) == (
        "unreadable", "no_history", "mismatch")
    df = walk(n=100, seed=4)
    reasons = set()
    for e in (none, empty, mism):
        r = run(df, d(df, 90), e, 10.0)
        assert r["basis"] == "prior_session" and r["binding"] == "prior_session"
        reasons.add(r["reason"])
    assert len(reasons) == 3


def test_pre_entry_high_never_enters_max():
    df = walk(n=160, seed=5, vol=0.001, drift=0.0)
    df.iloc[20:25] = df.iloc[20:25] * 2.0  # a peak before we bought
    E = d(df, 90)
    avg = float(df["Close"].iloc[90])
    r = run(df, d(df, 150), ep(E, avg), avg)
    post_max = float(df["Close"].iloc[90:150].max())
    assert r["hw_close"] == pytest.approx(post_max)
    assert r["stop"] < post_max


# ── fallbacks ────────────────────────────────────────────────────────────────
def test_too_few_bars_unavailable():
    df = walk(n=40, seed=6)
    r = run(df, d(df, 10), ep(d(df, 2), 100.0), 100.0)
    assert r["basis"] == "unavailable" and r["stop"] is None


def test_short_history_is_partial_lower_bound():
    df = walk(n=220, seed=8, drift=0.001)
    e = ep(d(df, 70), float(df["Close"].iloc[70]))
    avg = float(df["Close"].iloc[70])
    as_of = d(df, 200)
    full = run(df, as_of, e, avg)
    short = run(df.iloc[40:], as_of, e, avg)
    assert full["basis"] == "full" and short["basis"] == "partial"
    assert "older history unavailable" in short["reason"]
    assert short["stop"] <= full["stop"] + 1e-9


def test_stale_history_is_partial():
    df = walk(n=220, seed=8)
    e = ep(d(df, 70), float(df["Close"].iloc[70]))
    r = run(df.iloc[:190], d(df, 200), e, float(df["Close"].iloc[70]))
    assert r["basis"] == "partial" and r["reason"].startswith("history ends")


def test_split_guard_drops_anchor():
    df = walk(n=160, seed=9, vol=0.001)
    c = float(df["Close"].iloc[90])
    E = d(df, 90)
    bad = run(df, d(df, 150), ep(E, c * 2.0), c)  # fill = 2x: unrecorded split
    assert bad["components"]["entry"] is None and "unrecorded split" in bad["reason"]
    ok = run(df, d(df, 150), ep(E, c), c)
    assert ok["components"]["entry"] is not None
    adj = run(df, d(df, 150), ep(E, c * 2.0, ratio=2.0), c)  # recorded split
    assert adj["components"]["entry"] is not None


# ── ratchet ──────────────────────────────────────────────────────────────────
def test_ratchet_is_sticky_and_breachable():
    n = 160
    idx = pd.bdate_range("2025-01-02", periods=n)
    close = np.full(n, 100.0)
    close[80:100] = np.linspace(100, 140, 20)  # +40%: tier 25% -> floor +10%
    close[100:] = 105.0                        # falls back below tier threshold
    df = pd.DataFrame({"Open": close, "High": close * 1.002, "Low": close * 0.998,
                       "Close": close}, index=idx)
    e = ep(d(df, 60), 100.0)
    r = run(df, d(df, 150), e, 100.0, trail_mult=20.0)  # trailing out of the way
    assert r["binding"] == "ratchet" and r["stop"] == pytest.approx(110.0)
    assert r["ratchet_label"] == "Protect 10% gain"
    assert 105.0 <= r["stop"]  # the floor sits above today's price: breachable


# ── compose / history period / entry untouched ───────────────────────────────
@pytest.mark.parametrize("auto,manual", [
    (100.0, 99.0), (100.0, 100.0), (100.0, 101.0), (100.0, None), (100.0, 0.0),
    (100.0, -5.0)])
def test_compose_matches_manual_stop_wins(auto, manual):
    stop, is_manual = hs.compose_with_manual(auto, manual)
    assert is_manual == manual_stop_wins(manual, auto)
    assert stop == (round(manual, 2) if is_manual else auto)


def test_compose_manual_between_old_and_new_auto_loses():
    assert hs.compose_with_manual(110.0, 105.0) == (110.0, False)
    # no auto stop: a valid manual stop still applies, nothing otherwise
    assert hs.compose_with_manual(None, 90.0) == (90.0, True)
    assert hs.compose_with_manual(None, 90.126) == (90.13, True)
    assert hs.compose_with_manual(None, 0.0) == (None, False)
    assert hs.compose_with_manual(None, -5.0) == (None, False)
    assert hs.compose_with_manual(None, float("nan")) == (None, False)
    assert hs.compose_with_manual(100.0, 101.234) == (101.23, True)
    assert hs.compose_with_manual(None, None) == (None, False)


def test_history_period_needed():
    asof = date(2026, 10, 30)
    E = date(2026, 6, 1)
    assert hs.history_period_needed(date(2025, 1, 2), E, None, 60, as_of=asof) is None
    assert hs.history_period_needed(date(2026, 5, 1), E, None, 60, as_of=asof) == "1y"
    E2 = date(2025, 3, 3)
    assert hs.history_period_needed(date(2026, 1, 5), E2, None, 60, as_of=asof) == "2y"
    E3 = date(2022, 3, 3)
    assert hs.history_period_needed(date(2025, 1, 5), E3, None, 60, as_of=asof) == "5y"
    E4 = date(2015, 3, 3)
    assert hs.history_period_needed(date(2025, 1, 5), E4, None, 60, as_of=asof) == "max"
    # trail_start later than the episode start does NOT shrink the need
    assert hs.history_period_needed(date(2026, 5, 1), E2, date(2026, 6, 1), 60,
                                    as_of=asof) == "2y"


def test_history_period_needed_accepts_any_first_date_type():
    E = date(2026, 6, 1)
    asof = date(2026, 6, 10)
    for naive, want in ((date(2026, 5, 1), "1y"), (date(2025, 1, 2), None)):
        ts = pd.Timestamp(naive)
        variants = [naive, ts, ts.tz_localize("America/New_York"),
                    (ts + pd.Timedelta(hours=4)).tz_localize("UTC")]
        got = [hs.history_period_needed(v, E, None, 60, as_of=asof) for v in variants]
        assert got == [want] * 4


def _gap_frame():
    """Prior close 324.89; entry-session range 280.36-288.33 (GOOGL-like)."""
    idx = pd.bdate_range("2026-04-01", periods=130)
    close = np.full(len(idx), 320.0)
    df = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                       "Close": close}, index=idx)
    k = 120
    df.iloc[k - 1, df.columns.get_loc("Close")] = 324.89
    df.iloc[k, df.columns.get_loc("Open")] = 286.88
    df.iloc[k, df.columns.get_loc("High")] = 288.33
    df.iloc[k, df.columns.get_loc("Low")] = 280.36
    df.iloc[k, df.columns.get_loc("Close")] = 282.0
    return df, k


def test_extended_hours_fill_between_prev_close_and_session_keeps_anchor():
    df, k = _gap_frame()
    r = run(df, d(df, k + 3), ep(d(df, k), 310.60), 310.60)
    assert r["components"]["entry"] is not None
    assert "unrecorded split" not in r["reason"]


def test_googl_like_breach_reads_breached():
    df, k = _gap_frame()
    r = run(df, d(df, k + 3), ep(d(df, k), 310.60), 310.60)
    entry = r["components"]["entry"]
    # fill 310.60 minus 2 x the ATR before entry (flat 320 bars: ATR ~ 6.4 here)
    assert entry == pytest.approx(310.60 - 2.0 * hs._atr_series(
        df["High"], df["Low"], df["Close"], 14).iloc[k - 1])
    assert r["stop"] is not None and r["stop"] >= entry - 1e-9
    assert 280.49 < r["stop"]  # a price of 280.49 is below the stop: breached


def test_unrecorded_split_and_just_outside_widened_band_rejected():
    df, k = _gap_frame()
    bad = run(df, d(df, k + 3), ep(d(df, k), 310.60 * 2.0), 310.60)
    assert bad["components"]["entry"] is None and "unrecorded split" in bad["reason"]
    hi = 324.89 * 1.02  # prev_close is the band top, tol 2%
    over = run(df, d(df, k + 3), ep(d(df, k), hi * 1.001), 310.60)
    assert over["components"]["entry"] is None
    inside = run(df, d(df, k + 3), ep(d(df, k), hi * 0.999), 310.60)
    assert inside["components"]["entry"] is not None


def test_prev_close_inside_session_range_keeps_old_band():
    df = walk(n=160, seed=9, vol=0.001)
    c = float(df["Close"].iloc[90])
    hi = float(df["High"].iloc[90]) * 1.02
    E = d(df, 90)
    assert run(df, d(df, 150), ep(E, hi * 1.01), c)["components"]["entry"] is None
    assert run(df, d(df, 150), ep(E, hi * 0.99), c)["components"]["entry"] is not None


def test_atr_length_matches_risk_default():
    assert hs._ATR_LENGTH == inspect.signature(risk._atr_value).parameters["length"].default


# ── review fixes ─────────────────────────────────────────────────────────────
def test_unwarmed_entry_anchor_dropped_and_partial():
    df = walk(n=120, seed=3, vol=0.03)
    E = d(df, 1)
    c = float(df["Close"].iloc[1])
    r = run(df.iloc[:60], d(df, 40), ep(E, c), c)
    assert r["components"]["entry"] is None
    assert "not enough history before entry" in r["reason"] and r["basis"] == "partial"


def test_ratchet_window_restarts_at_add_trailing_unchanged():
    n = 200
    close = np.full(n, 50.0)
    close[70:100] = np.linspace(50, 200, 30)
    close[100:] = 150.0
    df = pd.DataFrame({"Open": close, "High": close * 1.03, "Low": close * 0.97,
                       "Close": close}, index=pd.bdate_range("2025-01-02", periods=n))
    base = ep(d(df, 65), 50.0)
    add = {**base, "last_buy": d(df, 101)}
    no_trail = dict(trail_mult=20.0)
    old = run(df, d(df, 150), base, 149.0, **no_trail)
    new = run(df, d(df, 150), add, 149.0, **no_trail)
    assert old["components"]["ratchet"] > 150.0   # the false-tight floor
    assert new["components"]["ratchet"] is None or new["components"]["ratchet"] < 150.0
    assert run(df, d(df, 150), add, 149.0)["components"]["trailing"] ==         run(df, d(df, 150), base, 149.0)["components"]["trailing"]


def test_episode_last_buy_tracks_adds():
    t = trades([("X", "BUY", 10, 10.0, "2025-01-06T15:00:00+00:00"),
                ("X", "BUY", 5, 12.0, "2025-02-03T15:00:00+00:00"),
                ("X", "SELL", 5, 13.0, "2025-02-10T15:00:00+00:00")])
    e = hs.episode_from_journal("X", t, 10, shares_tol=1e-4)
    assert e["start"] == date(2025, 1, 6) and e["last_buy"] == date(2025, 2, 3)


def test_rolling_frame_full_basis_is_monotonic():
    holidays = pd.to_datetime(["2025-02-17", "2025-04-18", "2025-05-26", "2025-07-04",
                               "2025-09-01", "2025-11-27"])
    nfull = 0
    for seed in range(8):
        df = walk(n=330, seed=seed, drift=-0.0005, vol=0.02)
        df = df[~df.index.isin(holidays)]
        Ei = 150
        avg = float(df["Close"].iloc[Ei])
        e = ep(d(df, Ei), avg)
        prev = None
        for i in range(Ei + 2, 260):
            frame = df.iloc[max(0, i - 141):i + 1]
            r = run(frame, d(df, i), e, avg)
            if prev is not None and prev[0] == "full" and r["basis"] == "full":
                assert r["stop"] >= prev[1] - 1e-9
            prev = (r["basis"], r["stop"])
            nfull += r["basis"] == "full"
    assert nfull > 100


def test_trail_start_after_entry_keeps_anchor_when_history_covers():
    df = walk(n=300, seed=5)
    Ei = 100
    c = float(df["Close"].iloc[Ei])
    e = ep(d(df, Ei), c)
    r = run(df, d(df, 250), e, c, trail_start=d(df, 200))
    assert r["components"]["entry"] is not None and r["basis"] == "full"
    r2 = run(df.iloc[Ei + 5:], d(df, 250), e, c, trail_start=d(df, 200))
    assert r2["basis"] == "partial" and r2["components"]["entry"] is None


def test_prior_session_fallback_uses_wider_mult():
    df = walk(n=100, seed=4)
    t = hs.episode_from_journal("X", None, 5, shares_tol=1e-4)
    a = run(df, d(df, 90), t, 10.0, initial_mult=2.0, trail_mult=3.0)["stop"]
    b = run(df, d(df, 90), t, 10.0, initial_mult=2.0, trail_mult=2.0)["stop"]
    assert a < b


def test_day_after_holiday_is_not_stale():
    idx = pd.bdate_range("2026-01-02", periods=160)
    idx = idx[idx != pd.Timestamp("2026-05-25")]  # Memorial Day 2026
    c = np.linspace(100, 120, len(idx))
    df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c}, index=idx)
    Ei = 70
    e = ep(idx[Ei].date(), float(c[Ei]))
    r = run(df[df.index < "2026-05-26"], date(2026, 5, 26), e, float(c[Ei]))
    assert r["basis"] == "full"
    r2 = run(df[df.index < "2026-05-26"], date(2026, 5, 27), e, float(c[Ei]))
    assert r2["basis"] == "partial" and r2["reason"].startswith("history ends")


def test_seeded_split_episode_reason():
    t = trades([("X", "SPLIT", 20, 25.0, "2025-03-20T15:00:00+00:00")])
    e = hs.episode_from_journal("X", t, 20, shares_tol=1e-4)
    assert e["status"] == "ok" and e["first_buy_price"] is None
    assert e["reason"] == "seeded from a SPLIT row"
    df = walk(n=160, seed=9)
    r = run(df, d(df, 150), {**e, "start": d(df, 90)}, 25.0)
    assert "seeded from a SPLIT row" in r["reason"]


def test_completed_bars_sorts_before_dedupe():
    df = walk(n=20, seed=1)
    a = df.iloc[[5]].copy(); a["Close"] = 111.0
    b = df.iloc[[5]].copy(); b["Close"] = 222.0
    shuffled = pd.concat([df.iloc[10:], b, df.iloc[:5], a, df.iloc[6:10]])
    out = hs.completed_bars(shuffled, d(df, 19))
    assert out.index.is_monotonic_increasing and not out.index.has_duplicates
    assert out.loc[df.index[5], "Close"] == 111.0  # a (111) comes after b (222) in input, so it survives


def test_ratchet_tier_exactly_at_threshold():
    n = 160
    close = np.full(n, 100.0)
    close[80:] = 125.0  # gain == 25.0 exactly
    df = pd.DataFrame({"Open": close, "High": close * 1.001, "Low": close * 0.999,
                       "Close": close}, index=pd.bdate_range("2025-01-02", periods=n))
    r = run(df, d(df, 150), ep(d(df, 60), 100.0), 100.0, trail_mult=50.0)
    assert r["ratchet_label"] == "Protect 10% gain"
    assert r["components"]["ratchet"] == pytest.approx(110.0)


def test_empty_frame_with_real_columns_is_no_history():
    from stock_analyzer import db
    empty = pd.DataFrame(columns=db._TRADE_COLS)
    assert hs.episode_from_journal("X", empty, 5, shares_tol=1e-4)["status"] == "no_history"


def test_entry_on_as_of_day_has_no_components():
    df = walk(n=120, seed=6)
    E = d(df, 100)
    r = run(df, E, ep(E, float(df["Close"].iloc[100])), float(df["Close"].iloc[100]))
    assert r["basis"] == "prior_session" and "no component computable" in r["reason"]


def test_atr_origin_invariance_across_frame_starts():
    df = walk(n=300, seed=21)
    Ei = 150
    c = float(df["Close"].iloc[Ei])
    e = ep(d(df, Ei), c)
    a = run(df.iloc[60:250], d(df, 249), e, c)       # origin at bar 90
    b = run(df.iloc[10:250], d(df, 249), e, c)
    assert a["basis"] == b["basis"] == "full"
    assert a["stop"] == b["stop"]
    for k in ("entry", "trailing", "ratchet"):
        assert a["components"][k] == pytest.approx(b["components"][k], abs=1e-12)


def test_warmup_bars_must_be_positive():
    df = walk(n=60, seed=1)
    with pytest.raises(ValueError, match="warmup_bars"):
        run(df, d(df, 50), ep(d(df, 20), 100.0), 100.0, warmup_bars=0)


def test_entry_on_non_trading_day_reason():
    df = walk(n=160, seed=9)
    sat = d(df, 90) + pd.Timedelta(days=(5 - pd.Timestamp(d(df, 90)).weekday()) % 7 or 7)
    r = run(df, d(df, 150), ep(sat, float(df["Close"].iloc[90])), float(df["Close"].iloc[90]))
    assert f"entry date {sat} is not a trading session" in r["reason"]
    assert r["components"]["entry"] is None and r["basis"] == "partial"
