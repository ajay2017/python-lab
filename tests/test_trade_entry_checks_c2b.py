"""TJDate C2b: the price and broker halves of `stock_analyzer.trade_entry_checks`.

`price_sanity`, `bar_for_price_check`, `price_check_needs_bar`, `price_band_finding`
(`now_et` / `unchecked_reason`), `price_band_entry_finding`, PRICE_DIFFERS and the
reworded MATCH_SAME_DAY in `pending_fill_findings`, `unlinked_fills`, `fills_since`,
`broker_check_coverage` and their finding builders.

Time-context matrix (CLAUDE.md): a weekend visit, the 16:00 close and the
2026-11-27 13:00 early close, an after-hours (21:18 ET) log, DST change days, a
trade logged a day late and a day early (BUY and SELL), a Thanksgiving window.
Fixtures from the real incidents: OKTA 11-02 (171.88 vs 189.14), MRVL 10-30
(5 @ 200.00 + 3 @ 191.96), GOOGL 10-28, CSCO 11-04.
"""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import trade_entry_checks as tec
from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast

FLOOR = C.TRADE_PRICE_SANITY_FLOOR
LOW = C.TRADE_PRICE_SANITY_RATIO_LOW
HIGH = C.TRADE_PRICE_SANITY_RATIO_HIGH

BAR = {"low": 100.0, "high": 200.0}
NO_BAR = "no usable price bar for that day"
SPLIT_UNKNOWN = "couldn't check for a stock split since that day"
SPLIT_SINCE = "a stock split since that day puts the day's range on a different share basis"


def et(y, mo, d, h=0, mi=0, s=0):
    return ET.localize(datetime(y, mo, d, h, mi, s))


def kinds(findings):
    return [f["kind"] for f in findings]


def fill(txn, tk, act, sh, px, d):
    return {"snaptrade_txn_id": txn, "ticker": tk, "action": act, "shares": sh,
            "price": px, "trade_date": d}


def entry(tk="GOOGL", act="BUY", sh=1, px=300.99, d=date(2026, 10, 29)):
    return {"ticker": tk, "action": act, "shares": sh, "price": px, "trade_date": d}


def _journal(*rows):
    return pd.DataFrame(list(rows))


def _jr(id_, ticker, action, shares, traded_at, price=100.0):
    return {"id": id_, "ticker": ticker, "action": action, "shares": shares,
            "price": price, "traded_at": traded_at}


# ── price_sanity ──────────────────────────────────────────────────────────────

def test_price_sanity_floor_is_strict():
    assert tec.price_sanity(FLOOR, None) == {"block": False, "kind": None, "ratio": None, "hint": False}
    below = tec.price_sanity(round(FLOOR - 0.01, 2), None)
    assert below["block"] is True and below["kind"] == "floor"


def test_price_sanity_ratio_edges_are_strict_both_ways():
    mp = 100.0
    assert tec.price_sanity(mp * LOW, mp)["block"] is False           # exactly LOW passes
    assert tec.price_sanity(mp * HIGH, mp)["block"] is False          # exactly HIGH passes
    assert tec.price_sanity(mp * LOW, mp)["hint"] is False
    assert tec.price_sanity(mp * HIGH, mp)["hint"] is False
    lo = tec.price_sanity(mp * LOW - 0.01, mp)
    hi = tec.price_sanity(mp * HIGH + 0.01, mp)
    assert (lo["block"], lo["kind"], lo["hint"]) == (True, "ratio", True)
    assert (hi["block"], hi["kind"], hi["hint"]) == (True, "ratio", True)
    assert tec.price_sanity(150.0, mp)["ratio"] == pytest.approx(1.5)


def test_price_sanity_floor_wins_kind_but_the_hint_still_reports_the_ratio():
    r = tec.price_sanity(0.05, 100.0)
    assert r["kind"] == "floor" and r["block"] is True
    assert r["hint"] is True and r["ratio"] == pytest.approx(0.0005)


@pytest.mark.parametrize("mp", [None, 0, -3.0, float("nan"), float("inf"), "x", True])
def test_price_sanity_without_a_usable_market_price_has_no_ratio(mp):
    assert tec.price_sanity(150.0, mp) == {"block": False, "kind": None, "ratio": None, "hint": False}


def test_price_sanity_unusable_price_fails_closed():
    for bad in (None, float("nan"), "abc"):
        r = tec.price_sanity(bad, 100.0)
        assert r["block"] is True and r["kind"] == "floor" and r["ratio"] is None


def test_price_sanity_matches_the_old_app_formula_on_a_grid():
    # the two blocks that lived in app.py: floor `price < FLOOR`, then
    # `ratio < LOW or ratio > HIGH` when a market price exists
    for mp in (None, 1.0, 20.0, 565.0):
        for px in (0.01, 0.09, 0.10, 0.11, 0.5, 1.0, 9.99, 10.0, 10.01, 20.0, 40.0, 41.0,
                   565.0, 1130.0, 1131.0):
            old_floor = px < FLOOR
            old_ratio = (mp is not None and mp > 0
                         and (px / mp < LOW or px / mp > HIGH))
            new = tec.price_sanity(px, mp)
            assert new["block"] == (old_floor or old_ratio), (px, mp)
            assert (new["kind"] == "floor") == old_floor, (px, mp)
            assert (new["kind"] == "ratio") == (old_ratio and not old_floor), (px, mp)
            assert new["hint"] == bool(old_ratio), (px, mp)


def test_price_sanity_uses_the_constants_not_literals(monkeypatch):
    monkeypatch.setattr(tec, "TRADE_PRICE_SANITY_FLOOR", 5.0)
    monkeypatch.setattr(tec, "TRADE_PRICE_SANITY_RATIO_LOW", 0.9)
    monkeypatch.setattr(tec, "TRADE_PRICE_SANITY_RATIO_HIGH", 1.1)
    assert tec.price_sanity(4.99, None)["kind"] == "floor"
    assert tec.price_sanity(88.0, 100.0)["kind"] == "ratio"
    assert tec.price_sanity(95.0, 100.0)["block"] is False


def test_override_label_is_built_from_the_constants(monkeypatch):
    assert tec.price_sanity_override_label() == (
        "Allow unusual price (under 50% or over 200% of the market price — "
        "historical/split-adjusted/delisted)")
    assert "±50%" not in tec.price_sanity_override_label()
    monkeypatch.setattr(tec, "TRADE_PRICE_SANITY_RATIO_LOW", 0.4)
    monkeypatch.setattr(tec, "TRADE_PRICE_SANITY_RATIO_HIGH", 3.0)
    lab = tec.price_sanity_override_label()
    assert "under 40%" in lab and "over 300%" in lab


# ── price_check_needs_bar ─────────────────────────────────────────────────────

def test_price_check_needs_bar_boundaries():
    today = date(2026, 11, 9)                                         # Monday
    limit = C.TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS
    assert tec.price_check_needs_bar(today, today) is True
    assert tec.price_check_needs_bar(date(2026, 11, 10), today) is False           # future
    assert tec.price_check_needs_bar(today - timedelta(days=limit + 1), today) is False
    edge = today - timedelta(days=limit)
    assert tec.price_check_needs_bar(edge, today) is tec._is_trading_day(edge)
    assert tec.price_check_needs_bar(date(2026, 11, 7), today) is False            # Saturday
    assert tec.price_check_needs_bar(date(2026, 11, 26), date(2026, 11, 30)) is False   # Thanksgiving
    assert tec.price_check_needs_bar("2026-11-06", "2026-11-09") is True
    with pytest.raises(ValueError):
        tec.price_check_needs_bar(None, today)


def test_price_check_needs_bar_false_means_the_band_is_unchecked_with_a_reason():
    today = date(2026, 11, 9)
    for d in (date(2026, 11, 7), date(2026, 10, 1), date(2026, 11, 10)):
        assert tec.price_check_needs_bar(d, today) is False
        f = tec.price_band_finding(150.0, d, None, today)
        assert f["state"] == "unchecked" and f["reason"]


# ── bar_for_price_check ───────────────────────────────────────────────────────

def _hist(rows, tz="America/New_York", splits=True):
    """rows = [(iso_date, low, high, split)]; a daily frame like Yahoo's."""
    idx = pd.DatetimeIndex(pd.to_datetime([r[0] for r in rows]))
    if tz:
        idx = idx.tz_localize(tz)
    data = {"Low": [r[1] for r in rows], "High": [r[2] for r in rows]}
    if splits:
        data["Stock Splits"] = [r[3] for r in rows]
    return pd.DataFrame(data, index=idx)


H = _hist([("2026-11-02", 186.1, 191.4, 0.0), ("2026-11-03", 187.0, 192.0, 0.0),
           ("2026-11-04", 188.0, 193.0, 0.0), ("2026-11-05", 189.0, 194.0, 0.0),
           ("2026-11-06", 190.0, 195.0, 0.0), ("2026-11-09", 191.0, 196.0, 0.0)])
SR = {"low": 100.0, "high": 110.0}


def test_bar_today_prefers_the_session_range_and_needs_no_history():
    assert tec.bar_for_price_check(date(2026, 11, 9), date(2026, 11, 9), None, SR) == (SR, None)
    assert tec.bar_for_price_check(date(2026, 11, 9), date(2026, 11, 9), H, SR) == (SR, None)


def test_bar_today_falls_back_to_the_history_row_then_to_no_bar():
    assert tec.bar_for_price_check(date(2026, 11, 9), date(2026, 11, 9), H, None) == \
        ({"low": 191.0, "high": 196.0}, None)
    assert tec.bar_for_price_check(date(2026, 11, 9), date(2026, 11, 9), None, None) == (None, NO_BAR)
    gone = _hist([("2026-11-06", 190.0, 195.0, 0.0)])
    assert tec.bar_for_price_check(date(2026, 11, 9), date(2026, 11, 9), gone,
                                   {"low": 5, "high": 1}) == (None, NO_BAR)


def test_bar_today_does_not_check_for_a_split():
    h = _hist([("2026-11-06", 190.0, 195.0, 0.0), ("2026-11-09", 95.0, 98.0, 2.0)])
    assert tec.bar_for_price_check(date(2026, 11, 9), date(2026, 11, 9), h, None) == \
        ({"low": 95.0, "high": 98.0}, None)


def test_bar_past_day_needs_the_history_for_the_split_check():
    for h in (None, "not a frame", H.iloc[0:0], H.drop(columns=["Stock Splits"])):
        assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 9), h, SR) == \
            (None, SPLIT_UNKNOWN)


def test_bar_past_day_uses_session_range_first_then_the_history_row():
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 9), H, SR) == (SR, None)
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 9), H, None) == \
        ({"low": 187.0, "high": 192.0}, None)
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 9), H,
                                   {"low": 0, "high": 1}) == ({"low": 187.0, "high": 192.0}, None)


def test_bar_split_after_the_trade_date_is_excluded_but_on_the_trade_date_is_allowed():
    after = _hist([("2026-11-02", 186.1, 191.4, 0.0), ("2026-11-03", 187.0, 192.0, 0.0),
                   ("2026-11-04", 94.0, 96.0, 2.0), ("2026-11-05", 95.0, 97.0, 0.0)])
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 5), after, SR) == (None, SPLIT_SINCE)
    # the split's ex-date is "after" a trade dated the day before, even when it is today
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 4), after, None) == (None, SPLIT_SINCE)
    # ON the trade date the day's own bar is already post-split: allowed
    assert tec.bar_for_price_check(date(2026, 11, 4), date(2026, 11, 5), after, None) == \
        ({"low": 94.0, "high": 96.0}, None)
    # a split BEFORE the trade date is irrelevant
    assert tec.bar_for_price_check(date(2026, 11, 5), date(2026, 11, 5), after, None) == \
        ({"low": 95.0, "high": 97.0}, None)


def test_bar_a_reverse_split_counts_too():
    rev = _hist([("2026-11-03", 10.0, 11.0, 0.0), ("2026-11-04", 40.0, 44.0, 0.1)])
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 4), rev, SR) == (None, SPLIT_SINCE)


def test_bar_tz_naive_index_is_exchange_local():
    naive = _hist([("2026-11-03", 187.0, 192.0, 0.0), ("2026-11-04", 188.0, 193.0, 0.0)], tz=None)
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 4), naive, None) == \
        ({"low": 187.0, "high": 192.0}, None)
    splitn = _hist([("2026-11-03", 187.0, 192.0, 0.0), ("2026-11-04", 94.0, 96.0, 2.0)], tz=None)
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 4), splitn, None) == \
        (None, SPLIT_SINCE)


def test_bar_tz_aware_index_in_another_zone_is_read_in_et():
    # Nov 4 03:00 UTC is 22:00 ET on Nov 3
    idx = pd.DatetimeIndex(pd.to_datetime(["2026-11-03 03:00", "2026-11-04 03:00"])).tz_localize("UTC")
    h = pd.DataFrame({"Low": [1.0, 2.0], "High": [2.0, 3.0], "Stock Splits": [0.0, 0.0]}, index=idx)
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 5), h, None) == \
        ({"low": 2.0, "high": 3.0}, None)


def test_bar_nan_and_unusable_rows_are_no_bar_and_never_raise():
    nan = _hist([("2026-11-03", float("nan"), 192.0, 0.0), ("2026-11-04", 188.0, 193.0, 0.0)])
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 4), nan, None) == (None, NO_BAR)
    inverted = _hist([("2026-11-03", 200.0, 192.0, 0.0), ("2026-11-04", 188.0, 193.0, 0.0)])
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 4), inverted, None) == (None, NO_BAR)
    # a NaN "Stock Splits" cell is yfinance's blank, not a split
    nan_split = _hist([("2026-11-03", 187.0, 192.0, 0.0), ("2026-11-04", 188.0, 193.0, float("nan"))])
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 4), nan_split, None)[0] == \
        {"low": 187.0, "high": 192.0}
    # a frame that isn't date-indexed: unknown, no exception
    junk = pd.DataFrame({"Low": [1.0], "High": [2.0], "Stock Splits": [0.0]}, index=["abc"])
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 4), junk, None) == \
        (None, SPLIT_UNKNOWN)
    nolow = H.drop(columns=["Low"])
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 9), nolow, None) == (None, NO_BAR)
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 9), H, "bar") == \
        ({"low": 187.0, "high": 192.0}, None)


def test_bar_history_that_starts_after_the_trade_date_cannot_rule_out_a_split():
    h = _hist([("2026-11-05", 189.0, 194.0, 0.0), ("2026-11-06", 190.0, 195.0, 0.0)])
    assert tec.bar_for_price_check(date(2026, 11, 3), date(2026, 11, 6), h, SR) == (None, SPLIT_UNKNOWN)


def test_bar_non_trading_date_reads_no_bar_never_a_split_reason():
    for d, today in ((date(2026, 11, 7), date(2026, 11, 9)),        # Saturday
                     (date(2026, 11, 8), date(2026, 11, 9)),        # Sunday
                     (date(2026, 11, 26), date(2026, 11, 30)),      # Thanksgiving
                     (date(2026, 11, 7), date(2026, 11, 7))):       # a weekend visit, today
        for hist in (H, None, "x", H.drop(columns=["Stock Splits"])):
            assert tec.bar_for_price_check(d, today, hist, None) == (None, NO_BAR), (d, hist is None)
        assert tec.bar_for_price_check(d, today, H, SR) == (None, NO_BAR)


def test_bar_future_date_and_bad_dates():
    assert tec.bar_for_price_check(date(2026, 11, 10), date(2026, 11, 9), H, SR)[0] is None
    with pytest.raises(ValueError):
        tec.bar_for_price_check(None, date(2026, 11, 9), H, SR)


def test_okta_1102_end_to_end_through_the_bar_and_the_band():
    h = _hist([("2026-11-02", 186.50, 192.30, 0.0), ("2026-11-03", 187.0, 192.0, 0.0),
               ("2026-11-09", 191.0, 196.0, 0.0)])
    today = date(2026, 11, 9)
    bar, why = tec.bar_for_price_check(date(2026, 11, 2), today, h, None)
    band = tec.price_band_finding(171.88, date(2026, 11, 2), bar, today,
                                  now_et=et(2026, 11, 9, 11, 0), unchecked_reason=why)
    f = tec.price_band_entry_finding(band, 171.88, date(2026, 11, 2))
    assert f["kind"] == "PRICE_OUTSIDE_RANGE" and f["severity"] == "tick" and f["group"] == "price"
    assert f["text"] == ("$171.88 is outside Mon Nov 2's trading range "
                         "($186.50 – $192.30, with 1% leeway)")
    ok = tec.price_band_finding(189.14, date(2026, 11, 2), bar, today)
    assert tec.price_band_entry_finding(ok, 189.14, date(2026, 11, 2)) is None


# ── price_band_finding: so_far with now_et, unchecked_reason ─────────────────

def test_so_far_is_false_once_the_regular_close_has_passed():
    d = date(2026, 11, 9)                                             # Monday, 16:00 close
    sf = lambda now: tec.price_band_finding(150.0, d, BAR, d, now_et=now)["so_far"]
    assert sf(et(2026, 11, 9, 9, 30)) is True
    assert sf(et(2026, 11, 9, 15, 59, 59)) is True
    assert sf(et(2026, 11, 9, 16, 0, 0)) is False                     # at the close the bar is final
    assert sf(et(2026, 11, 9, 16, 0, 1)) is False
    assert sf(et(2026, 11, 9, 21, 18)) is False                       # the 21:18 ET evening log
    assert sf(et(2026, 11, 9, 7, 0)) is True                          # pre-open: nothing final yet


def test_so_far_on_the_early_close_day_uses_the_early_close():
    d = date(2026, 11, 27)
    sf = lambda now: tec.price_band_finding(150.0, d, BAR, d, now_et=now)["so_far"]
    assert sf(et(2026, 11, 27, 12, 59, 59)) is True
    assert sf(et(2026, 11, 27, 13, 0, 0)) is False
    assert sf(et(2026, 11, 27, 15, 0)) is False


def test_so_far_weekend_today_is_false_with_now_and_unchanged_without():
    sat = date(2026, 11, 7)
    assert tec.price_band_finding(150.0, sat, None, sat, now_et=et(2026, 11, 7, 12, 0))["so_far"] is False
    assert tec.price_band_finding(150.0, sat, None, sat)["so_far"] is True


def test_so_far_never_true_for_another_day_and_needs_an_aware_now():
    assert tec.price_band_finding(150.0, date(2026, 11, 6), BAR, date(2026, 11, 9),
                                  now_et=et(2026, 11, 9, 12, 0))["so_far"] is False
    with pytest.raises(ValueError):
        tec.price_band_finding(150.0, date(2026, 11, 9), BAR, date(2026, 11, 9),
                               now_et=datetime(2026, 11, 9, 12, 0))


def test_unchecked_reason_replaces_only_the_no_bar_reason():
    today, d = date(2026, 11, 9), date(2026, 11, 2)
    assert tec.price_band_finding(150.0, d, None, today)["reason"] == NO_BAR
    f = tec.price_band_finding(150.0, d, None, today, unchecked_reason=SPLIT_SINCE)
    assert f["state"] == "unchecked" and f["reason"] == SPLIT_SINCE
    assert "future" in tec.price_band_finding(150.0, date(2026, 11, 10), None, today,
                                               unchecked_reason=SPLIT_SINCE)["reason"]
    assert "not a positive" in tec.price_band_finding(0, d, None, today,
                                                       unchecked_reason=SPLIT_SINCE)["reason"]
    assert str(C.TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS) in tec.price_band_finding(
        150.0, date(2026, 9, 1), None, today, unchecked_reason=SPLIT_SINCE)["reason"]
    assert tec.price_band_finding(150.0, d, BAR, today, unchecked_reason="x")["state"] == "in_band"


# ── price_band_entry_finding ──────────────────────────────────────────────────

def _band(price, bar, d=date(2026, 11, 2), today=date(2026, 11, 9), now=None):
    return tec.price_band_finding(price, d, bar, today, now_et=now)


def test_entry_finding_outside_in_band_and_unchecked():
    bar = {"low": 186.10, "high": 191.40}
    out = tec.price_band_entry_finding(_band(171.88, bar), 171.88, date(2026, 11, 2))
    assert out == {"kind": "PRICE_OUTSIDE_RANGE", "severity": "tick", "group": "price",
                   "text": "$171.88 is outside Mon Nov 2's trading range "
                           "($186.10 – $191.40, with 1% leeway)"}
    assert tec.price_band_entry_finding(_band(189.0, bar), 189.0, date(2026, 11, 2)) is None
    un = tec.price_band_entry_finding(_band(189.0, None), 189.0, date(2026, 11, 2))
    assert un == {"kind": "PRICE_UNCHECKED", "severity": "info", "group": "price",
                  "text": f"Price not checked against the day's range: {NO_BAR}."}


def test_entry_finding_says_so_far_for_a_forming_bar_and_not_after_the_close():
    bar = {"low": 186.10, "high": 191.40}
    d = date(2026, 11, 9)
    live = tec.price_band_finding(171.88, d, bar, d, now_et=et(2026, 11, 9, 11, 0))
    assert "trading range so far" in tec.price_band_entry_finding(live, 171.88, d)["text"]
    final = tec.price_band_finding(171.88, d, bar, d, now_et=et(2026, 11, 9, 21, 0))
    txt = tec.price_band_entry_finding(final, 171.88, d)["text"]
    assert "so far" not in txt and "trading range (" in txt


def test_entry_finding_without_a_date_and_with_other_leeway(monkeypatch):
    monkeypatch.setattr(tec, "TRADE_LOG_PRICE_BAND_TOL_PCT", 2.5)
    f = tec.price_band_entry_finding(_band(50.0, {"low": 100.0, "high": 110.0}), 50.0)
    assert f["text"].startswith("$50.00 is outside that day's trading range")
    assert "with 2.5% leeway" in f["text"]


def test_entry_finding_unreadable_band_is_unchecked_never_clean():
    for band in (None, "x", {}, {"state": "weird"}):
        f = tec.price_band_entry_finding(band, 100.0, date(2026, 11, 2))
        assert f["kind"] == "PRICE_UNCHECKED" and f["severity"] == "info"
    assert tec.price_band_entry_finding(
        {"state": "outside", "low": None, "high": None}, 100.0)["kind"] == "PRICE_UNCHECKED"
    assert tec.price_check_failed_finding()["text"] == \
        "Price not checked against the day's range: the check failed."


def test_split_unchecked_reason_reaches_the_card():
    today, d = date(2026, 11, 9), date(2026, 11, 2)
    f = tec.price_band_entry_finding(
        tec.price_band_finding(150.0, d, None, today, unchecked_reason=SPLIT_SINCE), 150.0, d)
    assert f["text"] == f"Price not checked against the day's range: {SPLIT_SINCE}."


# ── group keys ────────────────────────────────────────────────────────────────

def test_time_and_replay_findings_carry_the_time_group():
    now = et(2026, 11, 9, 11, 0)
    for v in ("2026-11-07T15:00:00Z", "2026-11-02T22:08:00Z", "2026-11-14T15:00:00Z"):
        for f in tec.time_findings(v, now):
            assert f["group"] == "time", f
    j = _journal(_jr(1, "OKTA", "BUY", 5, "2026-11-05T16:00:00-05:00"))
    assert tec.sell_replay_finding(j, "OKTA", 5, "2026-11-05T14:00:00-05:00")["group"] == "time"
    assert tec.sell_replay_finding(None, "OKTA", 5, "2026-11-05T14:00:00-05:00")["group"] == "time"


def test_broker_findings_carry_the_broker_group():
    e = entry("CSCO", "BUY", 5, 103.00, date(2026, 11, 4))
    cases = [
        [fill("A", "CSCO", "BUY", 5, 103.00, date(2026, 11, 4))],                       # MATCH
        [fill("A", "CSCO", "BUY", 5, 150.00, date(2026, 11, 4))],                       # PRICE_DIFFERS
        [fill("A", "CSCO", "BUY", 5, 103.00, date(2026, 11, 3))],                       # OTHER_DATE
        [fill("A", "CSCO", "BUY", 3, 103.00, date(2026, 11, 3)),
         fill("B", "CSCO", "BUY", 2, 103.00, date(2026, 11, 3))],                       # OTHER_DATE_SPLIT
        [fill("A", "CSCO", "BUY", 9, 103.00, date(2026, 11, 4))],                       # SHARES_SHORT
    ]
    seen = set()
    for fills in cases:
        for f in tec.pending_fill_findings(e, fills):
            assert f["group"] == "broker"
            seen.add(f["kind"])
    assert seen == {"MATCH_SAME_DAY", "PRICE_DIFFERS", "OTHER_DATE", "OTHER_DATE_SPLIT", "SHARES_SHORT"}


# ── PRICE_DIFFERS and the reworded MATCH_SAME_DAY ────────────────────────────

def test_okta_1102_same_shares_different_price_is_price_differs():
    e = entry("OKTA", "BUY", 3, 171.88, date(2026, 11, 2))
    f = tec.pending_fill_findings(e, [fill("T1", "OKTA", "BUY", 3, 189.14, date(2026, 11, 2))])
    assert kinds(f) == ["PRICE_DIFFERS"]
    assert f[0]["severity"] == "tick" and f[0]["txn_ids"] == ["T1"]
    assert f[0]["fill_date"] == date(2026, 11, 2)
    assert f[0]["text"] == ("Robinhood's record of BUY 3 OKTA on Mon Nov 2 is at 189.14, "
                            "not 171.88. Check the price in Robinhood.")
    assert "$" not in f[0]["text"]


def test_price_differs_one_finding_per_same_size_fill():
    e = entry("OKTA", "BUY", 3, 171.88, date(2026, 11, 2))
    f = tec.pending_fill_findings(e, [fill("B", "OKTA", "BUY", 3, 190.00, date(2026, 11, 2)),
                                      fill("A", "OKTA", "BUY", 3, 189.14, date(2026, 11, 2))])
    # two same-size fills also add up to more than the entry's shares
    assert kinds(f) == ["PRICE_DIFFERS", "PRICE_DIFFERS", "SHARES_SHORT"]
    assert [x["txn_ids"] for x in f[:2]] == [["A"], ["B"]]


def test_price_differs_needs_the_same_shares_and_the_same_day():
    e = entry("OKTA", "BUY", 3, 171.88, date(2026, 11, 2))
    assert tec.pending_fill_findings(e, [fill("A", "OKTA", "BUY", 2, 189.14, date(2026, 11, 2))]) == []
    assert tec.pending_fill_findings(e, [fill("A", "OKTA", "BUY", 3, 189.14, date(2026, 11, 3))]) == []
    # a half-cent rounding gap is the same price, so a MATCH rather than a difference
    assert kinds(tec.pending_fill_findings(
        e, [fill("A", "OKTA", "BUY", 3, 171.89, date(2026, 11, 2))])) == ["MATCH_SAME_DAY"]


def test_price_differs_vwap_group_for_a_combined_row():
    e = entry("MRVL", "BUY", 8, 150.00, date(2026, 10, 30))
    fills = [fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
             fill("S2", "MRVL", "BUY", 3, 190.00, date(2026, 10, 30))]
    f = tec.pending_fill_findings(e, fills)
    assert kinds(f) == ["PRICE_DIFFERS"]
    assert f[0]["txn_ids"] == ["S1", "S2"] and f[0]["severity"] == "tick"
    assert f[0]["text"] == ("Robinhood's 2 fills of BUY 8 MRVL on Fri Oct 30 average 196.25, "
                            "not 150.00. Check the price in Robinhood.")


def test_mrvl_1030_reconcile_stays_clean_vwap_within_a_cent():
    e = entry("MRVL", "BUY", 8, 196.99, date(2026, 10, 30))
    fills = [fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
             fill("S2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 30))]
    assert tec.pending_fill_findings(e, fills) == []                  # VWAP 196.985
    e9 = entry("MRVL", "BUY", 9, 150.00, date(2026, 10, 30))
    assert tec.pending_fill_findings(e9, fills) == []                 # 8 shares: no PRICE_DIFFERS group


def test_price_differs_is_absent_when_a_match_exists():
    e = entry("MRVL", "BUY", 5, 200.00, date(2026, 10, 30))
    fills = [fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
             fill("S2", "MRVL", "BUY", 5, 150.00, date(2026, 10, 30))]
    assert kinds(tec.pending_fill_findings(e, fills)) == ["MATCH_SAME_DAY"]


def test_broker_finding_order_is_match_pricediffers_otherdate_split_short():
    e = entry("CSCO", "BUY", 5, 103.00, date(2026, 11, 4))
    fills = [fill("A", "CSCO", "BUY", 5, 103.50, date(2026, 11, 4)),
             fill("B", "CSCO", "BUY", 3, 102.00, date(2026, 11, 4)),
             fill("C", "CSCO", "BUY", 5, 103.00, date(2026, 11, 3))]
    want = ["PRICE_DIFFERS", "OTHER_DATE", "SHARES_SHORT"]
    assert kinds(tec.pending_fill_findings(e, fills)) == want
    assert kinds(tec.pending_fill_findings(e, list(reversed(fills)))) == want


def test_match_same_day_text_names_the_record_and_the_link():
    e = entry("GOOGL", "BUY", 1, 300.99, date(2026, 10, 28))
    f = tec.pending_fill_findings(e, [fill("T9", "GOOGL", "BUY", 1, 300.99, date(2026, 10, 28))])
    assert f[0]["text"] == ("Robinhood's record of this order is dated Wed Oct 28: BUY 1 GOOGL at "
                            "300.99. This entry will be linked to it.")


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_logged_a_day_late_or_early_is_other_date_for_buy_and_sell(action):
    fills = [fill("T", "GOOGL", action, 1, 300.99, date(2026, 10, 28))]
    late = tec.pending_fill_findings(entry("GOOGL", action, 1, 300.99, date(2026, 10, 29)), fills)
    early = tec.pending_fill_findings(entry("GOOGL", action, 1, 300.99, date(2026, 10, 27)), fills)
    assert kinds(late) == ["OTHER_DATE"] and kinds(early) == ["OTHER_DATE"]


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_weekend_and_monday_entries_see_fridays_fill(action):
    fills = [fill("T", "GOOGL", action, 1, 300.99, date(2026, 11, 6))]
    for d in (date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9)):
        f = tec.pending_fill_findings(entry("GOOGL", action, 1, 300.99, d), fills)
        assert kinds(f) == ["OTHER_DATE"], d


# ── unlinked_fills ────────────────────────────────────────────────────────────

def _row(id_, txn, tk="OKTA", act="BUY", sh=3, px=189.14, td="2026-11-02", raw=None):
    return {"id": id_, "snaptrade_txn_id": txn, "ticker": tk, "action": act, "shares": sh,
            "price": px, "trade_date": td, "raw_json": raw}


def _trades(*txns):
    return pd.DataFrame({"ticker": ["X"] * len(txns), "broker_txn_id": list(txns)})


def test_unlinked_fills_drops_rows_whose_txn_is_on_a_journal_row():
    rows = [_row(1, "T1"), _row(2, "T2"), _row(3, "T3")]
    out = tec.unlinked_fills(rows, _trades("T1", None, float("nan"), " T3 "))
    assert [f["snaptrade_txn_id"] for f in out] == ["T2"]
    assert out[0]["pending_id"] == 2


def test_unlinked_fills_maps_the_fill_shape():
    out = tec.unlinked_fills(
        [_row(7, "T1", tk=" okta ", act="buy", sh=3, px=189.14, td="2026-11-02")], _trades())
    assert out == [{"snaptrade_txn_id": "T1", "pending_id": 7, "ticker": "OKTA", "action": "BUY",
                    "shares": 3.0, "price": 189.14, "trade_date": date(2026, 11, 2)}]


def test_unlinked_fills_prefers_the_exact_raw_json_price():
    d = tec.unlinked_fills([_row(1, "T1", px=224.41, raw={"price": 224.405, "units": 1})], _trades())
    assert d[0]["price"] == 224.405
    j = tec.unlinked_fills([_row(1, "T1", px=224.41, raw='{"price": 224.405}')], _trades())
    assert j[0]["price"] == 224.405
    for bad in ({"price": 0}, {"price": "x"}, {"price": None}, {}, "not json", 5, None,
                {"price": float("nan")}):
        assert tec.unlinked_fills([_row(1, "T1", px=224.41, raw=bad)], _trades())[0]["price"] == 224.41


def test_unlinked_fills_none_propagation_and_unreadable_inputs():
    assert tec.unlinked_fills(None, _trades()) is None
    assert tec.unlinked_fills([_row(1, "T1")], None) is None
    assert tec.unlinked_fills([_row(1, "T1")], "nope") is None
    assert tec.unlinked_fills([_row(1, "T1"), "garbage"], _trades()) is None
    assert tec.unlinked_fills([], _trades()) == []
    # an empty journal (even without the column) links nothing; a non-empty one without it can't say
    assert len(tec.unlinked_fills([_row(1, "T1")], pd.DataFrame())) == 1
    assert tec.unlinked_fills([_row(1, "T1")], pd.DataFrame({"ticker": ["X"]})) is None


def test_unlinked_fills_trade_date_is_the_et_date():
    for stored, want in (("2026-11-04", date(2026, 11, 4)),
                         ("2026-11-05T00:30:00Z", date(2026, 11, 4)),    # 19:30 ET the evening before
                         (date(2026, 11, 4), date(2026, 11, 4))):
        assert tec.unlinked_fills([_row(1, "T", td=stored)], _trades())[0]["trade_date"] == want


def test_unlinked_malformed_row_makes_that_name_unchecked_not_clean():
    rows = [_row(1, "T1", sh=None), _row(2, "T2", tk="CSCO", sh=5, px=103.0, td="2026-11-04")]
    fills = tec.unlinked_fills(rows, _trades())
    assert fills[0]["shares"] is None
    e_okta = entry("OKTA", "BUY", 3, 171.88, date(2026, 11, 2))
    assert tec.pending_fill_findings(e_okta, fills) is None
    e_csco = entry("CSCO", "BUY", 5, 103.0, date(2026, 11, 4))               # another ticker is unaffected
    assert kinds(tec.pending_fill_findings(e_csco, fills)) == ["MATCH_SAME_DAY"]
    for bad_row in (_row(3, "T3", px=None), _row(4, "T4", td="garbage"), _row(5, "T5", sh=0)):
        assert tec.pending_fill_findings(e_okta, tec.unlinked_fills([bad_row], _trades())) is None


def test_unlinked_fills_feed_okta_1102_into_price_differs_using_the_raw_price():
    rows = [_row(1, "T1", sh=3, px=189.14, raw={"price": 189.14})]
    e = entry("OKTA", "BUY", 3, 171.88, date(2026, 11, 2))
    assert kinds(tec.pending_fill_findings(e, tec.unlinked_fills(rows, _trades()))) == ["PRICE_DIFFERS"]
    # the same fill already on a journal row is no longer a candidate
    assert tec.pending_fill_findings(e, tec.unlinked_fills(rows, _trades("T1"))) == []


def test_pending_id_for_txn():
    fills = tec.unlinked_fills([_row(11, "T1"), _row(12, "T2")], _trades())
    assert tec.pending_id_for_txn(fills, "T2") == 12
    assert tec.pending_id_for_txn(fills, " T1 ") == 11
    assert tec.pending_id_for_txn(fills, "nope") is None
    assert tec.pending_id_for_txn(fills, None) is None
    assert tec.pending_id_for_txn(None, "T1") is None


# ── fills_since ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("d,want", [
    (date(2026, 11, 6), date(2026, 11, 3)),        # Friday: Thu, Wed, Tue
    (date(2026, 11, 9), date(2026, 11, 4)),        # Monday: Fri, Thu, Wed
    (date(2026, 11, 7), date(2026, 11, 3)),        # Saturday: the Friday window
    (date(2026, 11, 8), date(2026, 11, 3)),        # Sunday
    (date(2026, 11, 30), date(2026, 11, 24)),      # Monday after Thanksgiving: 27, 25, 24
    (date(2026, 11, 26), date(2026, 11, 20)),      # Thanksgiving itself: Wednesday's window
    (date(2026, 11, 27), date(2026, 11, 23)),      # the early-close day
])
def test_fills_since_steps_back_the_window_in_sessions(d, want):
    assert tec.fills_since(d) == want


def test_fills_since_is_exactly_the_edge_of_the_pending_fill_findings_window():
    w = C.JOURNAL_MATCH_WINDOW_SESSIONS
    d = date(2026, 11, 14)
    for _ in range(45):                                               # weekends, Thanksgiving, DST
        lo = tec.fills_since(d)
        assert tec._is_trading_day(lo)
        assert tec._sessions_between(lo, d) == w, d
        assert tec._sessions_between(tec._previous_trading_day(lo), d) == w + 1, d
        d += timedelta(days=1)


def test_fills_since_reads_the_same_edge_as_the_window_test_in_the_findings():
    # the Monday window test in test_trade_entry_checks: Wed 11-04 is in, Tue 11-03 is out
    lo = tec.fills_since(date(2026, 11, 9))
    e = entry(d=date(2026, 11, 9))
    assert kinds(tec.pending_fill_findings(
        e, [fill("A", "GOOGL", "BUY", 1, 300.99, lo)])) == ["OTHER_DATE"]
    assert tec.pending_fill_findings(
        e, [fill("A", "GOOGL", "BUY", 1, 300.99, lo - timedelta(days=1))]) == []


def test_fills_since_accepts_iso_strings_and_rejects_garbage():
    assert tec.fills_since("2026-11-06") == date(2026, 11, 3)
    with pytest.raises(ValueError):
        tec.fills_since(None)


# ── broker_check_coverage ─────────────────────────────────────────────────────

def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_coverage_unknown_when_no_completed_sync():
    assert tec.broker_check_coverage(date(2026, 11, 10), None) == {
        "state": "unknown",
        "text": "Not compared with Robinhood's fills: the time of the last "
                "completed broker sync couldn't be read."}


def test_coverage_a_same_day_sync_is_not_enough_a_next_day_sync_is():
    d = date(2026, 11, 10)
    assert tec.broker_check_coverage(d, _utc(2026, 11, 10, 17, 0)) == {      # 12:00 ET
        "state": "not_synced",
        "text": "Not compared with Robinhood's fills for Tue Nov 10 yet: "
                "the last completed sync was Tue Nov 10, 12:00 PM ET."}
    assert tec.broker_check_coverage(d, _utc(2026, 11, 11, 17, 0)) == {
        "state": "covered",
        "text": "Compared with Robinhood fills synced Wed Nov 11, 12:00 PM ET."}


def test_coverage_uses_the_et_date_not_the_utc_date():
    d = date(2026, 11, 10)
    assert tec.broker_check_coverage(d, _utc(2026, 11, 10, 23, 30))["state"] == "not_synced"   # 18:30 ET
    assert tec.broker_check_coverage(d, _utc(2026, 11, 11, 3, 30))["state"] == "not_synced"    # 22:30 ET Nov 10
    assert tec.broker_check_coverage(d, _utc(2026, 11, 11, 5, 30))["state"] == "covered"       # 00:30 ET Nov 11


def test_coverage_a_sync_before_the_trade_date_and_weekend_dates():
    c = tec.broker_check_coverage(date(2026, 11, 10), _utc(2026, 11, 8, 17, 0))
    assert c["state"] == "not_synced" and "Sun Nov 8, 12:00 PM ET" in c["text"]
    sat = date(2026, 11, 7)
    assert tec.broker_check_coverage(sat, _utc(2026, 11, 7, 17, 0))["state"] == "not_synced"
    assert tec.broker_check_coverage(sat, _utc(2026, 11, 8, 17, 0))["state"] == "covered"


def test_coverage_dst_change_day_is_read_in_et():
    d = date(2026, 10, 31)                                            # Saturday before the change
    assert tec.broker_check_coverage(d, _utc(2026, 11, 1, 3, 30))["state"] == "not_synced"  # 23:30 EDT
    assert tec.broker_check_coverage(d, _utc(2026, 11, 1, 5, 30))["state"] == "covered"     # 01:30 EDT


def test_coverage_accepts_iso_strings_and_naive_utc_and_survives_garbage():
    d = date(2026, 11, 10)
    assert tec.broker_check_coverage(d, "2026-11-11T17:00:00+00:00")["state"] == "covered"
    assert tec.broker_check_coverage(d, datetime(2026, 11, 11, 17, 0))["state"] == "covered"
    for bad in ("garbage", "2026-11-11", 12345):
        assert tec.broker_check_coverage(d, bad)["state"] == "unknown"
    with pytest.raises(ValueError):
        tec.broker_check_coverage(None, None)


def test_coverage_finding_is_info_for_unknown_and_not_synced_only():
    d = date(2026, 11, 10)
    for sync, want in ((None, True), (_utc(2026, 11, 10, 17), True), (_utc(2026, 11, 11, 17), False)):
        f = tec.broker_coverage_finding(tec.broker_check_coverage(d, sync))
        if want:
            assert f["kind"] == "BROKER_NOT_SYNCED" and f["severity"] == "info"
            assert f["group"] == "broker" and f["text"].startswith("Not compared with Robinhood's fills")
        else:
            assert f is None
    assert tec.broker_coverage_finding(None) is None
    assert tec.broker_coverage_finding({"state": "weird"}) is None


def test_broker_unreadable_finding_and_the_never_matches_wording_rule():
    f = tec.broker_unchecked_finding()
    assert f == {"kind": "BROKER_UNCHECKED", "severity": "info", "group": "broker",
                 "text": "Not compared with Robinhood's fills: they couldn't be read."}
    assert tec.BROKER_UNREADABLE_TEXT == f["text"]
    for sync in (None, _utc(2026, 11, 10, 17), _utc(2026, 11, 11, 17)):
        assert "matches" not in tec.broker_check_coverage(date(2026, 11, 10), sync)["text"].lower()


def test_new_kind_constants_and_docstring():
    assert tec.KIND_PRICE_DIFFERS == "PRICE_DIFFERS"
    assert tec.KIND_PRICE_OUTSIDE_RANGE == "PRICE_OUTSIDE_RANGE"
    assert tec.KIND_PRICE_UNCHECKED == "PRICE_UNCHECKED"
    assert tec.KIND_BROKER_NOT_SYNCED == "BROKER_NOT_SYNCED"
    assert tec.KIND_BROKER_UNCHECKED == "BROKER_UNCHECKED"
    assert "not wired yet" not in tec.__doc__
