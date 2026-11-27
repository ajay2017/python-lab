"""stock_analyzer.protective_coverage: which held names a cron protective lane did
not really check (docs/plans/sell-check-unread-3a-3b.md, chunk C3).

Dates are real, against the app calendar (data.is_trading_day / NYSE_HOLIDAYS):
Thanksgiving 2026-11-26 and the half day 2026-11-27 are closed / open as in prod.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import protective_coverage as pc
from stock_analyzer.data import is_trading_day

D = date.fromisoformat


# ------------------------------------------------------------------- builders

def _df(dates, close=100.0):
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    return pd.DataFrame({"Close": [close] * len(idx)}, index=idx)


def _bundle(dates, stale=None):
    return {"df": _df(dates), "stale_as_of": stale}


def _port(prices: dict, dropped=None):
    df = pd.DataFrame({"Ticker": list(prices), "Price": list(prices.values())})
    if dropped is not None:
        df.attrs["dropped_holdings"] = dropped
    return df


def _cov(held, data, prices=None, *, exp, dropped=None):
    prices = prices if prices is not None else {t: 10.0 for t in held}
    return pc.price_coverage(held, data, _port(prices, dropped), expected_session=D(exp))


# ------------------------------------------------------------ expected_session

def test_calendar_assumptions_used_by_these_tests():
    assert not is_trading_day(D("2026-11-26"))      # Thanksgiving
    assert is_trading_day(D("2026-11-27"))          # half day, still a session
    assert "2027-01-18" in C.NYSE_HOLIDAYS          # MLK Monday
    assert is_trading_day(D("2027-03-15"))


def test_monday_premarket_expects_friday_half_day():
    assert pc.expected_session(D("2026-11-30"), lane="premarket") == D("2026-11-27")


def test_day_after_thanksgiving_premarket_expects_wednesday():
    assert pc.expected_session(D("2026-11-27"), lane="premarket") == D("2026-11-25")


def test_day_after_monday_holiday_premarket_expects_friday():
    assert pc.expected_session(D("2027-01-19"), lane="premarket") == D("2027-01-15")


def test_forced_weekend_runs_expect_friday():
    sat, sun = D("2026-11-28"), D("2026-11-29")
    assert pc.expected_session(sat, lane="premarket") == D("2026-11-27")
    assert pc.expected_session(sun, lane="premarket") == D("2026-11-27")
    assert pc.expected_session(sat, lane="eod") == D("2026-11-27")


def test_eod_expects_today_on_a_trading_day_and_previous_otherwise():
    assert pc.expected_session(D("2026-12-01"), lane="eod") == D("2026-12-01")
    assert pc.expected_session(D("2026-11-26"), lane="eod") == D("2026-11-25")


def test_unknown_lane_follows_premarket_rule():
    assert pc.expected_session(D("2026-12-01"), lane="weird") == D("2026-11-30")


def test_expected_session_bad_input_never_raises():
    assert pc.expected_session(None, lane="premarket") is None
    assert pc.expected_session("2026-11-30", lane="eod") is None


# --------------------------------------------------------------- price_coverage

def test_monday_premarket_fresh_friday_bar_is_not_behind():
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-11-25", "2026-11-27"])}, exp="2026-11-27")
    assert cov["state"] == "full"
    assert cov["behind"] == [] and cov["cached"] == [] and cov["unpriced"] == []
    assert cov["fresh_count"] == 1 and cov["held_count"] == 1


def test_thanksgiving_cache_bar_wednesday_is_zero_behind():
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-11-25"], stale="2026-11-25")}, exp="2026-11-25")
    assert cov["state"] == "gaps"
    assert cov["cached"] == [{"ticker": "AAA", "last_bar": "2026-11-25", "sessions_behind": 0}]
    assert cov["behind"] == []


def test_thanksgiving_cache_bar_tuesday_is_one_behind():
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-11-24"], stale="2026-11-24")}, exp="2026-11-25")
    assert cov["cached"][0]["sessions_behind"] == 1


def test_old_cache_monday_premarket_counts_sessions():
    # Mon 2026-12-07, expected Fri 12-04; a Wed 12-02 cache bar is 2 sessions old.
    exp = pc.expected_session(D("2026-12-07"), lane="premarket")
    assert exp == D("2026-12-04")
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-12-02"], stale="2026-12-02")}, exp=exp.isoformat())
    assert cov["cached"][0]["sessions_behind"] == 2


def test_wed_bar_vs_thanksgiving_week_monday_is_one_session():
    # Mon 2026-11-30 expects Fri 11-27; Wed 11-25 -> only 11-27 counts (11-26 closed).
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-11-25"], stale="2026-11-25")}, exp="2026-11-27")
    assert cov["cached"][0]["sessions_behind"] == 1


def test_mlk_monday_expected_friday_and_thursday_bar_is_one_behind():
    exp = pc.expected_session(D("2027-01-19"), lane="premarket")
    cov = _cov(["AAA"], {"AAA": _bundle(["2027-01-14"])}, exp=exp.isoformat())
    assert cov["behind"] == [{"ticker": "AAA", "last_bar": "2027-01-14", "sessions_behind": 1}]
    cov2 = _cov(["AAA"], {"AAA": _bundle(["2027-01-15"])}, exp=exp.isoformat())
    assert cov2["state"] == "full"


def test_dst_monday_tz_aware_utc_bars_convert_to_et_dates():
    exp = pc.expected_session(D("2027-03-15"), lane="premarket")
    assert exp == D("2027-03-12")
    # Fri 03-12 21:00 EST = 02:00 UTC on 03-13 (a Saturday in UTC): ET date is 03-12.
    fri = pd.DataFrame({"Close": [1.0]},
                       index=pd.DatetimeIndex(["2027-03-13 02:00"], tz="UTC"))
    cov = _cov(["AAA"], {"AAA": {"df": fri, "stale_as_of": None}}, exp=exp.isoformat())
    assert cov["state"] == "full" and cov["not_dated"] == []
    # Thu 03-11 04:00 UTC = Wed 03-10 23:00 EST: ET date 03-10 -> 2 sessions behind.
    thu = pd.DataFrame({"Close": [1.0]},
                       index=pd.DatetimeIndex(["2027-03-11 04:00"], tz="UTC"))
    cov2 = _cov(["AAA"], {"AAA": {"df": thu, "stale_as_of": None}}, exp=exp.isoformat())
    assert cov2["behind"] == [{"ticker": "AAA", "last_bar": "2027-03-10", "sessions_behind": 2}]


def test_eod_cached_bar_yesterday_is_withheld():
    exp = pc.expected_session(D("2026-12-01"), lane="eod")
    cov = _cov(["AAA", "BBB"],
               {"AAA": _bundle(["2026-11-30"], stale="2026-11-30"),
                "BBB": _bundle(["2026-12-01"])}, exp=exp.isoformat())
    assert cov["cached"][0]["sessions_behind"] == 1
    assert pc.eod_withheld(cov) == ["AAA"]
    assert pc.lane_failures(cov, lane="eod", deterioration_basis="depth_only",
                            deterioration_error="x", risk_off_error="y") == []


def test_eod_fresh_read_bar_yesterday_is_behind_log_only():
    exp = pc.expected_session(D("2026-12-01"), lane="eod")
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-11-30"])}, exp=exp.isoformat())
    assert [b["ticker"] for b in cov["behind"]] == ["AAA"]
    assert pc.eod_withheld(cov) == []
    assert pc.lane_failures(cov, lane="eod", deterioration_basis="full",
                            deterioration_error=None, risk_off_error=None) == []
    assert "behind=AAA(last 2026-11-30, 1 behind)" in pc.log_lines(cov)[0]


def test_last_bar_after_expected_is_not_dated_not_behind():
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-11-27"])}, exp="2026-11-25")
    assert cov["not_dated"] == ["AAA"] and cov["behind"] == []
    assert cov["state"] == "full"


def test_tz_naive_index_at_expected_session_is_not_behind():
    df = pd.DataFrame({"Close": [1.0, 2.0]},
                      index=pd.DatetimeIndex(["2026-11-24 00:00", "2026-11-25 16:00"]))
    cov = _cov(["AAA"], {"AAA": {"df": df, "stale_as_of": None}}, exp="2026-11-25")
    assert cov["state"] == "full" and cov["behind"] == [] and cov["not_dated"] == []


def test_trailing_nan_close_bar_is_skipped_when_dating():
    df = pd.DataFrame({"Close": [1.0, np.nan]},
                      index=pd.DatetimeIndex(["2026-11-24", "2026-11-25"]))
    cov = _cov(["AAA"], {"AAA": {"df": df, "stale_as_of": None}}, exp="2026-11-25")
    assert cov["behind"] == [{"ticker": "AAA", "last_bar": "2026-11-24", "sessions_behind": 1}]


def test_foreign_symbol_behind_is_not_dated_not_behind():
    cov = _cov(["SHOP.TO", "000660.KS"],
               {"SHOP.TO": _bundle(["2026-11-20"]), "000660.KS": _bundle(["2026-11-20"])},
               exp="2026-11-25")
    assert cov["behind"] == []
    assert cov["not_dated"] == ["000660.KS", "SHOP.TO"]
    assert cov["state"] == "full"


def test_us_symbol_with_class_suffix_counts_as_behind():
    cov = _cov(["BRK-B"], {"BRK-B": _bundle(["2026-11-20"])}, exp="2026-11-25")
    assert [b["ticker"] for b in cov["behind"]] == ["BRK-B"]


def test_cached_name_is_never_also_behind():
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-11-20"], stale="2026-11-20")}, exp="2026-11-25")
    assert len(cov["cached"]) == 1 and cov["behind"] == []


def test_cached_with_missing_df_has_none_dates():
    cov = _cov(["AAA"], {"AAA": {"df": None, "stale_as_of": "2026-11-20"}}, exp="2026-11-25")
    assert cov["cached"] == [{"ticker": "AAA", "last_bar": None, "sessions_behind": None}]


def test_held_tickers_none_is_unverified():
    assert pc.price_coverage(None, {}, _port({"A": 1.0}), expected_session=D("2026-11-25")) is None
    assert pc.price_coverage("AAA", {}, _port({"A": 1.0}), expected_session=D("2026-11-25")) is None
    lines = pc.lane_failures(None, lane="premarket", deterioration_basis="full",
                             deterioration_error=None, risk_off_error=None)
    assert len(lines) == 1 and lines[0].startswith("held_coverage_unverified -- ")
    assert pc.lane_failures(None, lane="eod", deterioration_basis="full",
                            deterioration_error=None, risk_off_error=None) == []
    assert pc.log_lines(None) == ["held coverage: unverified (held-ticker list unavailable)"]
    assert pc.eod_withheld(None) == []
    assert pc.alert_note(None, "AAA") is None
    assert pc.email_lines(None, lane="premarket", deterioration_basis="full") == []


def test_absent_from_held_data_is_bundle_failed():
    cov = _cov(["AAA", "BBB"], {"BBB": _bundle(["2026-11-25"])},
               prices={"BBB": 5.0}, exp="2026-11-25")
    assert cov["unpriced"] == [{"ticker": "AAA", "reason": "bundle_failed"}]
    assert cov["state"] == "gaps" and cov["fresh_count"] == 1 and cov["held_count"] == 2


def test_dropped_holdings_reason_passes_through_and_default():
    dropped = [{"ticker": "AAA", "shares": 0, "avg_cost": 0, "reason": "invalid_shares_or_cost"}]
    cov = _cov(["AAA", "BBB", "CCC"],
               {"AAA": _bundle(["2026-11-25"]), "BBB": _bundle(["2026-11-25"]),
                "CCC": _bundle(["2026-11-25"])},
               prices={"AAA": 0.0, "BBB": float("nan"), "CCC": 5.0},
               exp="2026-11-25", dropped=dropped)
    by = {u["ticker"]: u["reason"] for u in cov["unpriced"]}
    assert by == {"AAA": "invalid_shares_or_cost", "BBB": "no_price_data"}


def test_ticker_case_and_whitespace_normalised():
    cov = _cov([" aaa "], {"AAA": _bundle(["2026-11-25"])}, prices={"AAA": 1.0}, exp="2026-11-25")
    assert cov["state"] == "full"


# ---------------------------------------------------------- deterioration_basis

def test_deterioration_basis_boundary_from_constant():
    n = C.REL_STRENGTH_LOOKBACK_DAYS
    assert pc.deterioration_basis(_df(pd.date_range("2026-01-01", periods=n))) == "depth_only"
    assert pc.deterioration_basis(_df(pd.date_range("2026-01-01", periods=n + 1))) == "full"
    assert pc.deterioration_basis(None) == "depth_only"
    assert pc.deterioration_basis(pd.DataFrame({"Open": [1.0]})) == "depth_only"
    assert pc.deterioration_basis("garbage") == "depth_only"


# ------------------------------------------------------------------------ text

def _gaps_cov():
    return {
        "state": "gaps", "expected_session": "2026-11-25",
        "held_count": 6, "fresh_count": 2,
        "unpriced": [{"ticker": "XYZ", "reason": "bundle_failed"},
                     {"ticker": "LOT", "reason": "invalid_shares_or_cost"}],
        "cached": [{"ticker": "ABC", "last_bar": "2026-11-25", "sessions_behind": 0},
                   {"ticker": "DEF", "last_bar": "2026-11-23", "sessions_behind": 2}],
        "behind": [{"ticker": "GHI", "last_bar": "2026-11-23", "sessions_behind": 1}],
        "not_dated": ["SHOP.TO"],
    }


def test_no_dollar_or_bold_in_fixed_wording():
    cov = _gaps_cov()
    texts = pc.lane_failures(cov, lane="premarket", deterioration_basis="depth_only",
                             deterioration_error=None, risk_off_error=None)
    texts += pc.email_lines(cov, lane="premarket", deterioration_basis="depth_only")
    texts += [pc.alert_note(cov, t) for t in ("ABC", "DEF", "GHI")]
    texts += pc.log_lines(cov)
    for s in texts:
        assert "$" not in s and "**" not in s and "<" not in s, s


def test_dates_render_weekday_month_day_without_zero_pad():
    cov = _gaps_cov()
    note = pc.alert_note(cov, "ABC")
    assert "Wed Nov 25" in note
    assert "Mon Nov 23" in pc.alert_note(cov, "DEF")
    cov2 = {**cov, "cached": [{"ticker": "ABC", "last_bar": "2026-12-02", "sessions_behind": 0}]}
    assert "Wed Dec 2)" in pc.alert_note(cov2, "ABC")        # no "Dec 02"


def test_session_plurals():
    cov = _gaps_cov()
    one = pc.alert_note({**cov, "cached": [{"ticker": "ABC", "last_bar": "2026-11-24",
                                            "sessions_behind": 1}]}, "ABC")
    assert "1 session old" in one and "1 sessions" not in one
    two = pc.alert_note(cov, "DEF")
    assert "2 sessions old" in two
    fl = pc.lane_failures(cov, lane="premarket", deterioration_basis="full",
                          deterioration_error=None, risk_off_error=None)
    assert any("GHI (ends Mon Nov 23, 1 session behind)" in s for s in fl)


def test_lane_failures_order_and_content():
    cov = _gaps_cov()
    fl = pc.lane_failures(cov, lane="premarket", deterioration_basis="depth_only",
                          deterioration_error="e" * 200, risk_off_error="rr")
    keys = [s.split(" -- ")[0] for s in fl]
    assert keys == ["held_unchecked", "held_cached", "held_price_behind",
                    "deterioration_depth_only", "deterioration_failed", "risk_off_failed"]
    assert "XYZ (no price from any provider)" in fl[0]
    assert "LOT (its shares or cost on record can't be used)" in fl[0]
    assert "ABC (last price Wed Nov 25)" in fl[1] and "DEF (Mon Nov 23, 2 sessions old)" in fl[1]
    assert "before the Wed Nov 25 close" in fl[2]
    assert ("e" * 80) in fl[4] and ("e" * 81) not in fl[4]
    assert "(rr)" in fl[5]


def test_lane_failures_clean_book_has_no_lines():
    cov = _cov(["AAA"], {"AAA": _bundle(["2026-11-25"])}, exp="2026-11-25")
    assert pc.lane_failures(cov, lane="premarket", deterioration_basis="full",
                            deterioration_error=None, risk_off_error=None) == []
    assert pc.email_lines(cov, lane="premarket", deterioration_basis="full") == []
    assert pc.log_lines(cov) == ["held coverage: 1 of 1 fresh"]


def test_lane_failures_unknown_lane_follows_premarket():
    fl = pc.lane_failures(None, lane="whatever", deterioration_basis="full",
                          deterioration_error=None, risk_off_error=None)
    assert fl and fl[0].startswith("held_coverage_unverified")


def test_email_lines_content_and_eod_empty():
    cov = _gaps_cov()
    lines = pc.email_lines(cov, lane="premarket", deterioration_basis="depth_only")
    joined = "\n".join(lines)
    assert "Not checked this morning: XYZ. No provider returned a price" in joined
    assert "Not checked this morning: LOT." in joined
    assert "ABC was checked against a saved copy of its Wed Nov 25 price, not a fresh read" in joined
    assert "DEF was checked against a saved copy of its Mon Nov 23 price (2 sessions old)" in joined
    assert "GHI's price history ends Mon Nov 23, 1 session before the Wed Nov 25 close" in joined
    assert "Deterioration exits were checked on drawdown depth only today" in lines[-1]
    assert pc.email_lines(cov, lane="eod", deterioration_basis="depth_only") == []


def test_email_lines_group_plural_unpriced():
    cov = {**_gaps_cov(), "unpriced": [{"ticker": "AAA", "reason": "bundle_failed"},
                                       {"ticker": "BBB", "reason": "no_price_data"}],
           "cached": [], "behind": []}
    lines = pc.email_lines(cov, lane="premarket", deterioration_basis="full")
    assert len(lines) == 1 and "AAA, BBB" in lines[0] and "their stop" in lines[0]


def test_alert_note_none_for_clean_name_and_text():
    cov = _gaps_cov()
    assert pc.alert_note(cov, "XYZ") is None
    assert pc.alert_note(cov, "nope") is None
    assert pc.alert_note(cov, "abc").startswith("Priced from a saved copy (last price Wed Nov 25)")
    assert pc.alert_note(cov, "GHI") == (
        "Its price history ends Mon Nov 23, not at the Wed Nov 25 close: "
        "check the live price before acting.")


def test_log_line_format():
    cov = _gaps_cov()
    line = pc.log_lines(cov)[0]
    assert line == (
        "held coverage: 2 of 6 fresh; "
        "cached=ABC(last 2026-11-25, 0 behind),DEF(last 2026-11-23, 2 behind); "
        "behind=GHI(last 2026-11-23, 1 behind); "
        "unpriced=XYZ(bundle_failed),LOT(invalid_shares_or_cost); "
        "not_dated=SHOP.TO")


def test_eod_withheld_sorted():
    cov = {**_gaps_cov(), "cached": [{"ticker": "ZZZ", "last_bar": None, "sessions_behind": None},
                                     {"ticker": "AAA", "last_bar": "2026-11-25",
                                      "sessions_behind": 0}]}
    assert pc.eod_withheld(cov) == ["AAA", "ZZZ"]


# --------------------------------------------------------------- never raises

@pytest.mark.parametrize("port_df", [None, "x", pd.DataFrame(), pd.DataFrame({"A": [1]})])
def test_price_coverage_garbage_port_df(port_df):
    cov = pc.price_coverage(["AAA"], {"AAA": _bundle(["2026-11-25"])}, port_df,
                            expected_session=D("2026-11-25"))
    assert cov is not None
    assert cov["unpriced"] == [{"ticker": "AAA", "reason": "no_price_data"}]


def test_price_coverage_garbage_held_data_values():
    hd = {"AAA": None, "BBB": "x", "CCC": 5, "DDD": {"df": "junk", "stale_as_of": None},
          "EEE": {"df": pd.DataFrame({"Open": [1.0]}), "stale_as_of": "2026-11-20"}}
    prices = {t: 1.0 for t in hd}
    cov = _cov(list(hd), hd, prices=prices, exp="2026-11-25")
    assert cov is not None
    assert [c["ticker"] for c in cov["cached"]] == ["EEE"]
    assert cov["cached"][0]["last_bar"] is None
    assert cov["not_dated"] == ["AAA", "BBB", "CCC", "DDD"]
    assert pc.price_coverage(["AAA"], "junk", _port({"AAA": 1.0}),
                             expected_session=D("2026-11-25"))["not_dated"] == ["AAA"]


def test_price_coverage_bad_expected_and_garbage_dropped():
    assert pc.price_coverage(["AAA"], {}, _port({"AAA": 1.0}), expected_session=None) is None
    assert pc.price_coverage(["AAA"], {}, _port({"AAA": 1.0}), expected_session="2026-11-25") is None
    cov = _cov(["AAA"], {}, prices={"AAA": 0}, exp="2026-11-25",
               dropped=["junk", {"ticker": None}, 5])
    assert cov["unpriced"] == [{"ticker": "AAA", "reason": "bundle_failed"}]


def test_text_functions_never_raise_on_garbage_coverage():
    for bad in ({}, {"cached": [{"nope": 1}]}, {"behind": [{"ticker": "A"}]}, "junk", 5):
        assert isinstance(pc.lane_failures(bad, lane="premarket", deterioration_basis="x",
                                           deterioration_error=None, risk_off_error=None), list)
        assert isinstance(pc.email_lines(bad, lane="premarket", deterioration_basis="x"), list)
        assert pc.alert_note(bad, "A") is None or isinstance(pc.alert_note(bad, "A"), str)
        assert isinstance(pc.eod_withheld(bad), list)
        assert isinstance(pc.log_lines(bad), list)


def test_empty_error_message_still_loud():
    fl = pc.lane_failures(_cov(["AAA"], {"AAA": _bundle(["2026-11-25"])}, exp="2026-11-25"),
                          lane="premarket", deterioration_basis="full",
                          deterioration_error="", risk_off_error=ValueError("a\nb"))
    assert any("deterioration_failed" in s and "unknown error" in s for s in fl)
    assert any("risk_off_failed" in s and "(a b)" in s for s in fl)


def test_empty_book_is_full():
    cov = pc.price_coverage([], {}, _port({}), expected_session=D("2026-11-25"))
    assert cov["state"] == "full" and cov["held_count"] == 0
