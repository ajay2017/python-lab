"""TJDate C1: `stock_analyzer.trade_entry_checks`, the pure log-time checks for a
hand-entered trade (time, price band, unlinked broker fills).

Time-context matrix (CLAUDE.md): weekend, Thanksgiving, the 2026-11-27 early
close, the RTH boundaries to the second, DST days with `Z` and offset inputs,
pre-open / after-close / same-day-after-close, a past date with and without a
same-ticker sibling, and a trade dated for the future. Fixtures come from the
real incidents: GOOGL 10-28 (4:06 PM EDT fill), OKTA 11-02 (171.88 vs 189.14),
MRVL 10-30 (5 @ 200.00 + 3 @ 191.96).
"""
from datetime import date, datetime, time
from pathlib import Path

import pytest

from stock_analyzer import constants as C
from stock_analyzer import trade_entry_checks as tec
from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast


def et(y, mo, d, h=0, mi=0, s=0):
    return ET.localize(datetime(y, mo, d, h, mi, s))


def kinds(findings):
    return [f["kind"] for f in findings]


# ── calendar parity with data.py (the module deliberately doesn't import it) ──

def test_calendar_helpers_match_data_module():
    from stock_analyzer import data
    d = date(2026, 11, 20)
    for _ in range(40):
        assert tec._is_trading_day(d) == data.is_trading_day(d), d
        assert tec._previous_trading_day(d) == data.previous_trading_day(d), d
        assert tec._sessions_between(d, date(2026, 12, 31)) == \
            data.trading_sessions_between(d, date(2026, 12, 31)), d
        d = date.fromordinal(d.toordinal() + 1)


def test_module_does_not_import_the_provider_stack():
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; import stock_analyzer.trade_entry_checks; "
         "print('stock_analyzer.data' in sys.modules, 'yfinance' in sys.modules)"],
        capture_output=True, text=True, check=True,
        cwd=str(Path(__file__).parent.parent)).stdout.strip()
    assert out == "False False"


# ── constants ─────────────────────────────────────────────────────────────────

def test_owner_agreed_constants():
    assert C.TRADE_LOG_PRICE_BAND_TOL_PCT == 1.0
    assert C.TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS == 35
    assert C.NYSE_REGULAR_OPEN_ET == (9, 30)
    assert C.NYSE_REGULAR_CLOSE_ET == (16, 0)


# ── session_hours ─────────────────────────────────────────────────────────────

def test_session_hours_regular_day_est_and_edt():
    o, c = tec.session_hours(date(2026, 11, 2))          # EST
    assert (o.hour, o.minute, c.hour, c.minute) == (9, 30, 16, 0)
    assert o.utcoffset().total_seconds() == -5 * 3600
    o, c = tec.session_hours(date(2026, 10, 28))         # EDT
    assert o.utcoffset().total_seconds() == -4 * 3600


@pytest.mark.parametrize("d", [
    date(2026, 11, 7), date(2026, 11, 8),                # Sat, Sun
    date(2026, 11, 26),                                  # Thanksgiving
    date(2026, 11, 1), date(2027, 3, 14),                # DST Sundays
])
def test_session_hours_none_when_closed(d):
    assert tec.session_hours(d) is None


def test_session_hours_early_close_day():
    assert C.NYSE_EARLY_CLOSES["2026-11-27"] == 13.0     # pin the fixture's source
    o, c = tec.session_hours(date(2026, 11, 27))
    assert (o.hour, o.minute) == (9, 30)
    assert (c.hour, c.minute) == (13, 0)
    # a day after it is a normal close again
    assert tec.session_hours(date(2026, 11, 30))[1].hour == 16


# ── default_trade_date (U2) ───────────────────────────────────────────────────

@pytest.mark.parametrize("now,expected", [
    (et(2026, 11, 9, 8, 0), date(2026, 11, 6)),          # Monday pre-open -> Friday
    (et(2026, 11, 9, 9, 29, 59), date(2026, 11, 6)),
    (et(2026, 11, 9, 9, 30, 0), date(2026, 11, 9)),      # at the open -> today
    (et(2026, 11, 9, 21, 0), date(2026, 11, 9)),         # after close -> still today
    (et(2026, 11, 7, 12, 0), date(2026, 11, 6)),         # Saturday
    (et(2026, 11, 8, 12, 0), date(2026, 11, 6)),         # Sunday
    (et(2026, 11, 26, 12, 0), date(2026, 11, 25)),       # Thanksgiving
    (et(2026, 11, 27, 8, 0), date(2026, 11, 25)),        # day after: pre-open skips the holiday
    (et(2026, 11, 27, 15, 0), date(2026, 11, 27)),       # early-close day, after its close
])
def test_default_trade_date(now, expected):
    assert tec.default_trade_date(now) == expected


def test_default_trade_date_accepts_utc_aware_now():
    # 14:29:59Z on 2026-11-09 is 09:29:59 EST; 14:30:00Z is 09:30:00 EST
    z = datetime.fromisoformat("2026-11-09T14:29:59+00:00")
    assert tec.default_trade_date(z) == date(2026, 11, 6)
    z = datetime.fromisoformat("2026-11-09T14:30:00+00:00")
    assert tec.default_trade_date(z) == date(2026, 11, 9)


def test_naive_now_is_refused_everywhere():
    naive = datetime(2026, 11, 9, 12, 0)
    for call in (
        lambda: tec.default_trade_date(naive),
        lambda: tec.fill_time_requirement(date(2026, 11, 9), naive, False),
        lambda: tec.resolve_traded_at(date(2026, 11, 9), time(10, 0), naive),
        lambda: tec.time_findings("2026-11-09T15:00:00+00:00", naive),
    ):
        with pytest.raises(ValueError):
            call()


# ── fill_time_requirement (U1) ────────────────────────────────────────────────

@pytest.mark.parametrize("now,td,sibling,expected", [
    (et(2026, 11, 9, 11, 0), date(2026, 11, 9), False, "default_now"),
    (et(2026, 11, 9, 9, 30, 0), date(2026, 11, 9), False, "default_now"),
    (et(2026, 11, 9, 16, 0, 0), date(2026, 11, 9), False, "default_now"),    # close inclusive
    (et(2026, 11, 9, 16, 0, 1), date(2026, 11, 9), False, "required"),       # same day after close
    (et(2026, 11, 9, 9, 29, 59), date(2026, 11, 9), False, "required"),      # pre-open
    (et(2026, 11, 7, 11, 0), date(2026, 11, 7), False, "required"),          # Saturday
    (et(2026, 11, 26, 11, 0), date(2026, 11, 26), False, "required"),        # Thanksgiving
    (et(2026, 11, 27, 12, 59), date(2026, 11, 27), False, "default_now"),    # early-close day, before 1 PM
    (et(2026, 11, 27, 13, 1), date(2026, 11, 27), False, "required"),        # after the early close
    (et(2026, 11, 9, 11, 0), date(2026, 11, 6), False, "optional"),          # past date
    (et(2026, 11, 9, 11, 0), date(2026, 11, 6), True, "required"),           # past + same-ticker sibling
    (et(2026, 11, 9, 11, 0), date(2026, 11, 10), False, "required"),         # future date
])
def test_fill_time_requirement(now, td, sibling, expected):
    assert tec.fill_time_requirement(td, now, sibling) == expected


# ── resolve_traded_at ─────────────────────────────────────────────────────────

def test_resolve_given_time_est_and_edt():
    now = et(2026, 11, 9, 12, 0)
    assert tec.resolve_traded_at(date(2026, 11, 4), time(12, 16), now) == \
        "2026-11-04T12:16:00-05:00"
    assert tec.resolve_traded_at(date(2026, 10, 28), time(16, 6, 22), now) == \
        "2026-10-28T16:06:22-04:00"


def test_resolve_dst_days_use_the_right_offset():
    now = et(2027, 3, 20, 12, 0)
    assert tec.resolve_traded_at(date(2026, 11, 1), time(11, 0), now) == \
        "2026-11-01T11:00:00-05:00"        # fall back day: EST
    assert tec.resolve_traded_at(date(2027, 3, 14), time(12, 0), now) == \
        "2027-03-14T12:00:00-04:00"        # spring forward day: EDT


def test_resolve_past_date_without_time_is_the_16_et_anchor():
    now = et(2026, 11, 9, 12, 0)
    assert tec.resolve_traded_at(date(2026, 11, 4), None, now) == "2026-11-04T16:00:00-05:00"
    assert tec.resolve_traded_at(date(2026, 10, 28), None, now) == "2026-10-28T16:00:00-04:00"
    # the same wall-clock hour on a spring-forward week (not an absolute offset)
    assert tec.resolve_traded_at(date(2027, 3, 12), None, et(2027, 3, 20, 12)) == \
        "2027-03-12T16:00:00-05:00"
    assert tec.resolve_traded_at(date(2027, 3, 15), None, et(2027, 3, 20, 12)) == \
        "2027-03-15T16:00:00-04:00"


def test_resolve_anchor_agrees_with_trade_time_anchor():
    from stock_analyzer import trade_time
    now = et(2026, 11, 9, 12, 0)
    got = tec.resolve_traded_at(date(2026, 11, 4), None, now)
    assert got == trade_time.anchored_iso("2026-11-04")
    assert trade_time.is_placeholder_time(got)


def test_resolve_today_open_without_time_is_now():
    now = et(2026, 11, 9, 11, 7, 30)
    assert tec.resolve_traded_at(date(2026, 11, 9), None, now) == "2026-11-09T11:07:30-05:00"
    # a UTC-aware now is expressed in ET
    z = datetime.fromisoformat("2026-11-09T16:07:30+00:00")
    assert tec.resolve_traded_at(date(2026, 11, 9), None, z) == "2026-11-09T11:07:30-05:00"


@pytest.mark.parametrize("now,td", [
    (et(2026, 11, 9, 17, 0), date(2026, 11, 9)),         # same day after close
    (et(2026, 11, 9, 8, 0), date(2026, 11, 9)),          # pre-open
    (et(2026, 11, 7, 12, 0), date(2026, 11, 7)),         # weekend
    (et(2026, 11, 9, 11, 0), date(2026, 11, 10)),        # future date
])
def test_resolve_none_when_required_raises(now, td):
    with pytest.raises(ValueError):
        tec.resolve_traded_at(td, None, now)


def test_resolve_past_date_same_ticker_same_day_needs_the_time():
    now = et(2026, 11, 9, 12, 0)
    with pytest.raises(ValueError):
        tec.resolve_traded_at(date(2026, 11, 4), None, now, same_ticker_same_day=True)
    # default False keeps the 16:00 anchor; a given time is always accepted
    assert tec.resolve_traded_at(date(2026, 11, 4), None, now, False) == "2026-11-04T16:00:00-05:00"
    assert tec.resolve_traded_at(date(2026, 11, 4), time(12, 16), now, True) ==         "2026-11-04T12:16:00-05:00"


def test_as_date_converts_zoned_values_to_et_first():
    import pandas as pd
    assert tec._as_date("2026-10-29T00:30:00Z") == date(2026, 10, 28)
    assert tec._as_date("2026-10-29T00:30:00+00:00") == date(2026, 10, 28)
    assert tec._as_date(datetime.fromisoformat("2026-10-29T00:30:00+00:00")) == date(2026, 10, 28)
    assert tec._as_date(pd.Timestamp("2026-10-29T00:30:00Z")) == date(2026, 10, 28)
    assert tec._as_date("2026-10-29") == date(2026, 10, 29)           # bare date as-is
    assert tec._as_date(date(2026, 10, 29)) == date(2026, 10, 29)
    assert tec._as_date(datetime(2026, 10, 29, 0, 30)) == date(2026, 10, 29)   # naive: own date
    assert tec._as_date("garbage") is None and tec._as_date(None) is None


def test_fill_trade_date_with_a_time_is_read_in_et():
    # a fill carried as a UTC timestamp 00:30Z Oct 29 is Oct 28 ET: MATCH_SAME_DAY on Oct 28
    e = entry("GOOGL", "BUY", 1, 300.99, date(2026, 10, 28))
    f = tec.pending_fill_findings(
        e, [fill("T", "GOOGL", "BUY", 1, 300.99, "2026-10-29T00:30:00Z")])
    assert kinds(f) == ["MATCH_SAME_DAY"]


def test_resolve_given_time_on_a_required_day_is_accepted():
    # after close today, with the time supplied: fine (time_findings judges it)
    out = tec.resolve_traded_at(date(2026, 11, 9), time(14, 43), et(2026, 11, 9, 17, 8))
    assert out == "2026-11-09T14:43:00-05:00"


# ── time_findings ─────────────────────────────────────────────────────────────

NOW = et(2026, 11, 9, 20, 0)


def test_weekend_is_one_non_trading_finding_not_two():
    f = tec.time_findings("2026-11-07T15:00:00+00:00", NOW)       # Sat 10:00 EST
    assert kinds(f) == ["NON_TRADING_DATE"]
    assert f[0]["severity"] == "tick"
    assert f[0]["text"] == "Sat Nov 7 is not a trading day"


def test_thanksgiving_is_non_trading():
    assert kinds(tec.time_findings("2026-11-26T15:00:00+00:00", et(2026, 11, 30, 10))) ==         ["NON_TRADING_DATE"]


def test_dst_sundays_are_non_trading_with_z_or_offset_input():
    now = et(2027, 3, 20, 20, 0)
    for v in ("2026-11-01T15:00:00Z", "2026-11-01T10:00:00-05:00", "2026-11-01T11:00:00-04:00"):
        assert kinds(tec.time_findings(v, now)) == ["NON_TRADING_DATE"], v
    for v in ("2027-03-14T16:00:00Z", "2027-03-14T12:00:00-04:00"):
        assert kinds(tec.time_findings(v, now)) == ["NON_TRADING_DATE"], v


@pytest.mark.parametrize("value,expect", [
    # 2026-11-02 is EST (UTC-5): 09:30 EST = 14:30Z
    ("2026-11-02T14:29:59Z", ["OUTSIDE_RTH"]),
    ("2026-11-02T14:30:00Z", []),
    ("2026-11-02T21:00:00Z", []),                       # 16:00:00 EST: placeholder AND in range
    ("2026-11-02T21:00:01Z", ["OUTSIDE_RTH"]),         # 16:00:01
    ("2026-11-02T09:29:59-05:00", ["OUTSIDE_RTH"]),
    ("2026-11-02T09:30:00-05:00", []),
    ("2026-11-02T16:00:00-05:00", []),                 # close inclusive
    ("2026-11-02T16:00:01-05:00", ["OUTSIDE_RTH"]),
    # 2026-10-30 is EDT (UTC-4): 09:30 EDT = 13:30Z
    ("2026-10-30T13:29:59Z", ["OUTSIDE_RTH"]),
    ("2026-10-30T13:30:00Z", []),
    ("2026-10-30T20:00:00Z", []),                      # 16:00 EDT
    ("2026-10-30T20:00:01Z", ["OUTSIDE_RTH"]),
    # DST boundary weekdays: Fri before / Mon after
    ("2027-03-12T14:30:00Z", []),                      # EST still: 09:30
    ("2027-03-12T13:30:00Z", ["OUTSIDE_RTH"]),         # that is 08:30 EST
    ("2027-03-15T13:30:00Z", []),                      # EDT now: 09:30
    ("2027-03-15T14:30:00Z", []),                      # 10:30 EDT
    ("2026-11-02T12:00:00+00:00", ["OUTSIDE_RTH"]),    # 07:00 EST pre-market
])
def test_rth_boundaries_and_dst_offsets(value, expect):
    assert kinds(tec.time_findings(value, et(2027, 3, 20, 20, 0))) == expect


def test_googl_1028_extended_hours_fill_is_a_tick_not_a_block():
    f = tec.time_findings("2026-10-28T20:06:22Z", et(2026, 10, 29, 9, 0))
    assert kinds(f) == ["OUTSIDE_RTH"]
    assert f[0]["severity"] == "tick"
    assert f[0]["text"] == "4:06 PM ET is outside regular hours (9:30 AM–4:00 PM)"


def test_okta_1102_late_log_time_is_outside_rth():
    # logged at 17:08 EST instead of the 14:43 fill
    f = tec.time_findings("2026-11-02T22:08:00Z", et(2026, 11, 2, 17, 30))
    assert kinds(f) == ["OUTSIDE_RTH"]
    assert "5:08 PM ET" in f[0]["text"]


def test_sixteen_hundred_anchor_is_never_outside_rth_even_on_early_close_day():
    # 16:00 ET anchor on the half day (13:00 close) would be after close, but it
    # is the app-invented placeholder
    assert tec.time_findings("2026-11-27T16:00:00-05:00", et(2026, 11, 30, 10)) == []
    assert tec.time_findings("2026-11-04T16:00:00-05:00", NOW) == []
    # ...but a real 16:00:01 on a normal day is flagged, and the placeholder
    # rule is exact to the microsecond
    assert kinds(tec.time_findings("2026-11-04T16:00:01-05:00", NOW)) == ["OUTSIDE_RTH"]


def test_early_close_day_both_sides():
    now = et(2026, 11, 30, 10)
    assert tec.time_findings("2026-11-27T12:59:59-05:00", now) == []
    assert tec.time_findings("2026-11-27T13:00:00-05:00", now) == []            # inclusive
    assert tec.time_findings("2026-11-27T18:00:00Z", now) == []                 # 13:00:00 EST, same instant in Z
    f = tec.time_findings("2026-11-27T13:00:01-05:00", now)
    assert kinds(f) == ["OUTSIDE_RTH"]
    assert f[0]["text"] == "1:00 PM ET is outside regular hours (9:30 AM–1:00 PM, early close)"
    # the same clock time on a normal day (Mon 11-30) is fine
    assert tec.time_findings("2026-11-30T13:00:01-05:00", et(2026, 12, 1, 10)) == []


def test_future_is_hard_and_strict():
    now = et(2026, 11, 9, 11, 0, 0)
    f = tec.time_findings("2026-11-09T11:00:01-05:00", now)
    assert kinds(f) == ["FUTURE"] and f[0]["severity"] == "hard"
    assert tec.time_findings("2026-11-09T11:00:00-05:00", now) == []             # equal is not future
    assert tec.time_findings("2026-11-09T10:59:59-05:00", now) == []
    # a future Saturday also carries the non-trading tick; FUTURE first
    f = tec.time_findings("2026-11-14T15:00:00Z", now)
    assert kinds(f) == ["FUTURE", "NON_TRADING_DATE"]
    # a future after-hours time carries both too
    f = tec.time_findings("2026-11-10T22:00:00Z", now)
    assert kinds(f) == ["FUTURE", "OUTSIDE_RTH"]


def test_time_findings_accepts_datetime_and_rejects_garbage():
    assert tec.time_findings(et(2026, 11, 2, 10, 0), NOW) == []
    with pytest.raises(ValueError):
        tec.time_findings("not a time", NOW)
    with pytest.raises(ValueError):
        tec.time_findings(None, NOW)
    with pytest.raises(ValueError):
        tec.time_findings("2026-11-04", NOW)          # date-only: no time to judge


def test_no_dollar_sign_in_any_time_text():
    cases = ["2026-11-07T15:00:00Z", "2026-11-02T22:08:00Z", "2026-11-14T15:00:00Z"]
    for v in cases:
        for f in tec.time_findings(v, et(2026, 11, 9, 11, 0)):
            assert "$" not in f["text"]


# ── price_band_finding ────────────────────────────────────────────────────────

TODAY = date(2026, 11, 9)
BAR = {"low": 100.0, "high": 200.0}


def test_okta_1102_regression_logged_171_88_vs_real_189_14():
    # a bar around the real 189.14 fill; the stray 171.88 sits more than 1% under its low
    bar = {"low": 186.50, "high": 192.30}
    f = tec.price_band_finding(171.88, date(2026, 11, 2), bar, TODAY)
    assert f["state"] == "outside"
    assert f["low"] == 186.50 and f["high"] == 192.30
    assert f["so_far"] is False and f["reason"] is None
    assert tec.price_band_finding(189.14, date(2026, 11, 2), bar, TODAY)["state"] == "in_band"


def test_band_edges_are_inclusive():
    d = date(2026, 11, 2)
    f = tec.price_band_finding(99.0, d, BAR, TODAY)
    assert (f["band_low"], f["band_high"]) == (99.0, 202.0)
    assert f["state"] == "in_band"
    assert tec.price_band_finding(202.0, d, BAR, TODAY)["state"] == "in_band"
    assert tec.price_band_finding(98.99, d, BAR, TODAY)["state"] == "outside"
    assert tec.price_band_finding(202.01, d, BAR, TODAY)["state"] == "outside"
    assert tec.price_band_finding(150.0, d, BAR, TODAY)["state"] == "in_band"


def test_band_uses_the_constant_not_a_literal(monkeypatch):
    monkeypatch.setattr(tec, "TRADE_LOG_PRICE_BAND_TOL_PCT", 5.0)
    f = tec.price_band_finding(96.0, date(2026, 11, 2), BAR, TODAY)
    assert f["band_low"] == 95.0 and f["state"] == "in_band"


@pytest.mark.parametrize("bar", [
    None, {}, {"low": None, "high": 5}, {"low": float("nan"), "high": 5},
    {"low": 1, "high": float("inf")}, {"low": 300, "high": 100},      # low > high
    {"low": "x", "high": 5}, {"low": 0, "high": 5}, {"low": -1, "high": 5}, "bar",
])
def test_unusable_bar_is_unchecked_with_reason(bar):
    f = tec.price_band_finding(150.0, date(2026, 11, 2), bar, TODAY)
    assert f["state"] == "unchecked"
    assert f["reason"]
    assert f["band_low"] is None and f["band_high"] is None
    assert f["low"] is None and f["high"] is None


@pytest.mark.parametrize("price", [None, 0, -5, float("nan"), float("inf"), "abc", True])
def test_bad_price_is_unchecked(price):
    f = tec.price_band_finding(price, date(2026, 11, 2), BAR, TODAY)
    assert f["state"] == "unchecked" and f["reason"]


def test_age_limit_is_calendar_days_and_inclusive_of_the_limit():
    limit = C.TRADE_LOG_PRICE_CHECK_MAX_AGE_DAYS
    from datetime import timedelta
    ok = TODAY - timedelta(days=limit)
    old = TODAY - timedelta(days=limit + 1)
    assert tec.price_band_finding(150.0, ok, BAR, TODAY)["state"] == "in_band"
    f = tec.price_band_finding(150.0, old, BAR, TODAY)
    assert f["state"] == "unchecked"
    assert str(limit) in f["reason"]


def test_future_trade_date_is_unchecked():
    f = tec.price_band_finding(150.0, date(2026, 11, 10), BAR, TODAY)
    assert f["state"] == "unchecked" and "future" in f["reason"]


def test_today_is_labelled_so_far():
    f = tec.price_band_finding(150.0, TODAY, BAR, TODAY)
    assert f["state"] == "in_band" and f["so_far"] is True
    assert tec.price_band_finding(150.0, date(2026, 11, 6), BAR, TODAY)["so_far"] is False
    # an unchecked today is still "so far"
    assert tec.price_band_finding(150.0, TODAY, None, TODAY)["so_far"] is True


def test_price_band_accepts_iso_strings_for_dates():
    f = tec.price_band_finding(150.0, "2026-11-02", BAR, "2026-11-09")
    assert f["state"] == "in_band"


# ── pending_fill_findings ─────────────────────────────────────────────────────

def fill(txn, tk, act, sh, px, d):
    return {"snaptrade_txn_id": txn, "ticker": tk, "action": act, "shares": sh,
            "price": px, "trade_date": d}


def entry(tk="GOOGL", act="BUY", sh=1, px=300.99, d=date(2026, 10, 29)):
    return {"ticker": tk, "action": act, "shares": sh, "price": px, "trade_date": d}


def test_none_in_none_out_and_empty_is_empty():
    assert tec.pending_fill_findings(entry(), None) is None
    assert tec.pending_fill_findings(entry(), []) == []


def test_malformed_entry_or_same_name_fill_is_couldnt_check_never_clean():
    assert tec.pending_fill_findings({"ticker": "X"}, [fill("a", "X", "BUY", 1, 1, date(2026, 11, 2))]) is None
    assert tec.pending_fill_findings(None, []) is None
    bad = fill("a", "GOOGL", "BUY", None, 300.99, date(2026, 10, 28))
    assert tec.pending_fill_findings(entry(), [bad]) is None
    # a malformed fill of ANOTHER ticker is irrelevant
    assert tec.pending_fill_findings(
        entry(), [fill("a", "AAPL", "BUY", None, 1, date(2026, 10, 28))]) == []


def test_1028_fixture_entry_dated_the_wrong_day_is_other_date():
    # owner logged the 10-28 fill on 10-29; SnapTrade's fill is dated 10-28
    f = tec.pending_fill_findings(
        entry(d=date(2026, 10, 29)),
        [fill("T1", "GOOGL", "BUY", 1, 300.99, date(2026, 10, 28))])
    assert kinds(f) == ["OTHER_DATE"]
    assert f[0]["severity"] == "tick"
    assert f[0]["txn_ids"] == ["T1"] and f[0]["fill_date"] == date(2026, 10, 28)
    assert f[0]["text"] == (
        "Robinhood's record of this order (BUY 1 GOOGL at 300.99) is dated Wed Oct 28, "
        "not Thu Oct 29. That is the date the order was placed, which can be earlier than "
        "the fill; check the 'Filled' date in Robinhood.")
    assert "$" not in f[0]["text"]


def test_other_date_respects_the_session_window():
    w = C.JOURNAL_MATCH_WINDOW_SESSIONS
    assert w == 3
    e = entry(d=date(2026, 11, 9))                                    # Monday
    # 3 sessions back = Wed 11-04: inside
    assert kinds(tec.pending_fill_findings(
        e, [fill("A", "GOOGL", "BUY", 1, 300.99, date(2026, 11, 4))])) == ["OTHER_DATE"]
    # 4 sessions back = Tue 11-03: outside
    assert tec.pending_fill_findings(
        e, [fill("A", "GOOGL", "BUY", 1, 300.99, date(2026, 11, 3))]) == []
    # Thanksgiving skipped: entry Mon 11-30. Wed 11-25 is 2 sessions back
    # (11-27, 11-30), Tue 11-24 is 3 (11-25, 11-27, 11-30), Mon 11-23 is 4.
    e2 = entry(d=date(2026, 11, 30))
    for inside in (date(2026, 11, 25), date(2026, 11, 24)):
        assert kinds(tec.pending_fill_findings(
            e2, [fill("A", "GOOGL", "BUY", 1, 300.99, inside)])) == ["OTHER_DATE"], inside
    assert tec.pending_fill_findings(
        e2, [fill("A", "GOOGL", "BUY", 1, 300.99, date(2026, 11, 23))]) == []


def test_other_date_needs_matching_shares_and_price():
    e = entry(d=date(2026, 10, 29))
    assert tec.pending_fill_findings(
        e, [fill("A", "GOOGL", "BUY", 2, 300.99, date(2026, 10, 28))]) == []
    assert tec.pending_fill_findings(
        e, [fill("A", "GOOGL", "BUY", 1, 301.50, date(2026, 10, 28))]) == []
    # a half-cent rounding difference is still the same price
    assert kinds(tec.pending_fill_findings(
        e, [fill("A", "GOOGL", "BUY", 1, 301.00, date(2026, 10, 28))])) == ["OTHER_DATE"]


def test_mrvl_1030_split_on_another_date_is_other_date_split():
    e = entry("MRVL", "BUY", 8, 196.99, date(2026, 11, 2))
    fills = [fill("S2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 30)),
             fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30))]
    f = tec.pending_fill_findings(e, fills)
    assert kinds(f) == ["OTHER_DATE_SPLIT"]
    assert f[0]["severity"] == "tick"
    assert f[0]["txn_ids"] == ["S1", "S2"]
    assert f[0]["fill_date"] == date(2026, 10, 30)
    assert f[0]["text"] == (
        "Robinhood's records of 2 fills totalling 8 MRVL BUY (average 196.99) are dated "
        "Fri Oct 30, not Mon Nov 2. That is the date the orders were placed, which can be "
        "earlier than the fills; check the 'Filled' dates in Robinhood.")
    assert "$" not in f[0]["text"]


def test_mrvl_1030_split_on_the_same_day_is_not_a_finding():
    e = entry("MRVL", "BUY", 8, 196.99, date(2026, 10, 30))
    fills = [fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
             fill("S2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 30))]
    assert tec.pending_fill_findings(e, fills) == []


def test_split_with_wrong_vwap_or_wrong_total_is_no_finding():
    e = entry("MRVL", "BUY", 8, 150.00, date(2026, 11, 2))
    fills = [fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
             fill("S2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 30))]
    assert tec.pending_fill_findings(e, fills) == []
    e = entry("MRVL", "BUY", 9, 196.99, date(2026, 11, 2))
    assert tec.pending_fill_findings(e, fills) == []


def test_split_mixing_dates_does_not_sum_across_dates():
    e = entry("MRVL", "BUY", 8, 196.99, date(2026, 11, 2))
    fills = [fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
             fill("S2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 29))]
    assert tec.pending_fill_findings(e, fills) == []


def test_shares_short_when_broker_shows_more_that_day():
    e = entry("CSCO", "BUY", 5, 103.00, date(2026, 11, 4))
    fills = [fill("A", "CSCO", "BUY", 5, 103.50, date(2026, 11, 4)),    # price differs: no MATCH
             fill("B", "CSCO", "BUY", 3, 102.00, date(2026, 11, 4))]
    f = tec.pending_fill_findings(e, fills)
    assert kinds(f) == ["SHARES_SHORT"]
    assert f[0]["severity"] == "tick" and f[0]["txn_ids"] == ["A", "B"]
    assert f[0]["fill_date"] == date(2026, 11, 4)
    assert f[0]["text"] == ("8 shares of CSCO BUY in Robinhood fills not yet matched to the "
                            "journal are dated Wed Nov 4, more than the 5 you entered")


def test_shares_equal_or_fewer_that_day_is_not_short():
    e = entry("CSCO", "BUY", 8, 103.00, date(2026, 11, 4))
    assert tec.pending_fill_findings(
        e, [fill("A", "CSCO", "BUY", 5, 103.50, date(2026, 11, 4))]) == []     # fewer
    assert tec.pending_fill_findings(
        e, [fill("A", "CSCO", "BUY", 8, 150.00, date(2026, 11, 4))]) == []     # equal, price off: C5's job


def test_match_same_day_carries_the_txn_id_and_is_info():
    e = entry("GOOGL", "BUY", 1, 300.99, date(2026, 10, 28))
    f = tec.pending_fill_findings(e, [fill("T9", "GOOGL", "BUY", 1, 300.99, date(2026, 10, 28))])
    assert kinds(f) == ["MATCH_SAME_DAY"]
    assert f[0]["severity"] == "info"
    assert f[0]["txn_ids"] == ["T9"] and f[0]["fill_date"] == date(2026, 10, 28)
    assert "$" not in f[0]["text"]


def test_match_same_day_is_exclusive_of_the_sibling_findings():
    # entry 5 @ 200.00 matches one fill; the other fill (3) would add up to "more shares"
    e = entry("MRVL", "BUY", 5, 200.00, date(2026, 10, 30))
    fills = [fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
             fill("S2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 30)),
             fill("S3", "MRVL", "BUY", 5, 200.00, date(2026, 10, 29))]
    f = tec.pending_fill_findings(e, fills)
    assert kinds(f) == ["MATCH_SAME_DAY"] and f[0]["txn_ids"] == ["S1"]


def test_match_within_tolerances():
    e = entry("GOOGL", "BUY", 1.0004, 301.004, date(2026, 10, 28))     # 0.0004 sh, half a cent
    f = tec.pending_fill_findings(e, [fill("T", "GOOGL", "BUY", 1, 301.00, date(2026, 10, 28))])
    assert kinds(f) == ["MATCH_SAME_DAY"]


def test_different_action_or_ticker_never_matches():
    e = entry("GOOGL", "BUY", 1, 300.99, date(2026, 10, 28))
    assert tec.pending_fill_findings(
        e, [fill("T", "GOOGL", "SELL", 1, 300.99, date(2026, 10, 28))]) == []
    assert tec.pending_fill_findings(
        e, [fill("T", "GOOG", "BUY", 1, 300.99, date(2026, 10, 28))]) == []
    assert tec.pending_fill_findings(
        e, [fill("T", "GOOGL", "SELL", 1, 300.99, date(2026, 10, 27)),
            fill("U", "AAPL", "BUY", 1, 300.99, date(2026, 10, 27))]) == []


def test_ticker_and_action_are_case_and_whitespace_insensitive():
    e = entry(" googl ", "buy", 1, 300.99, date(2026, 10, 28))
    f = tec.pending_fill_findings(e, [fill("T", "GOOGL", " BUY", 1, 300.99, date(2026, 10, 28))])
    assert kinds(f) == ["MATCH_SAME_DAY"]


def test_dates_may_be_iso_strings():
    e = entry(d="2026-10-29")
    f = tec.pending_fill_findings(e, [fill("T", "GOOGL", "BUY", 1, 300.99, "2026-10-28")])
    assert kinds(f) == ["OTHER_DATE"]


def test_deterministic_order_regardless_of_input_order():
    e = entry("MRVL", "BUY", 8, 196.99, date(2026, 11, 2))
    base = [fill("S1", "MRVL", "BUY", 5, 200.00, date(2026, 10, 30)),
            fill("S2", "MRVL", "BUY", 3, 191.96, date(2026, 10, 30)),
            fill("Z", "MRVL", "BUY", 8, 196.99, date(2026, 10, 29)),
            fill("Y", "MRVL", "BUY", 8, 196.99, date(2026, 11, 3))]
    a = tec.pending_fill_findings(e, base)
    b = tec.pending_fill_findings(e, list(reversed(base)))
    assert a == b
    assert kinds(a) == ["OTHER_DATE", "OTHER_DATE", "OTHER_DATE_SPLIT"]
    assert [x["fill_date"] for x in a] == [date(2026, 10, 29), date(2026, 11, 3), date(2026, 10, 30)]


def test_missing_txn_ids_are_dropped_not_stringified():
    e = entry(d=date(2026, 10, 29))
    f = tec.pending_fill_findings(e, [fill(None, "GOOGL", "BUY", 1, 300.99, date(2026, 10, 28))])
    assert f[0]["txn_ids"] == []
