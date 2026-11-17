"""
Tests for stock_analyzer/macro_calendar.py — the FRED-based economic calendar
and regime detector. Zero coverage before this batch, and more decision-
critical than most: `detect_macro_regime()` feeds the Home page's Macro
Signals banner and several downstream advisors, so every boundary in the
7-signal weighted scorer (Fed Funds trend, CPI YoY, 2s10s spread, unemployment
delta, HY credit spread, SPY 20d return, VIX level) is tested at-boundary vs
just-past using the real `constants.py` values, plus the "rate_cut hard gate"
that reassigns the winner when CPI is not actually controlled, and the
winning-score-too-weak fallback to "neutral". `_fred_obs`/`_fred_obs_dated`/
`_fred_series_last_updated` locally import `requests` inside their functions — the real
installed module is monkeypatched directly (its local import resolves to the
same cached module object), mirroring test_stress_test.py's yfinance pattern
(also used here for the SPY/VIX market-data signals).
"""
from datetime import date, datetime

import pandas as pd
import pytest
import requests
import yfinance

from stock_analyzer import macro_calendar as mc
from stock_analyzer.constants import (
    REGIME_CPI_CONTROLLED_MAX, REGIME_CPI_ELEVATED_MIN, REGIME_CPI_HOT_MIN,
    REGIME_FEDFUNDS_TREND_PP, REGIME_2S10S_INVERTED_PP, REGIME_2S10S_FLAT_PP,
    REGIME_2S10S_STEEP_PP, REGIME_UNEMP_DELTA_UP_PP, REGIME_UNEMP_DELTA_DOWN_PP,
    REGIME_HY_SPREAD_STRESS_BP, REGIME_HY_SPREAD_ELEVATED_BP, REGIME_HY_SPREAD_CALM_BP,
    REGIME_SPY_20D_BULL_PCT, REGIME_SPY_20D_BEAR_PCT, REGIME_VIX_STRESS,
    REGIME_VIX_ELEVATED, REGIME_VIX_CALM, REGIME_WINNING_SCORE_MIN,
)

pytestmark = pytest.mark.fast


# ─── _days_label ──────────────────────────────────────────────────────────────

def test_days_label_past_event():
    assert mc._days_label(date(2026, 7, 1), date(2026, 7, 5)) == "4d ago"


def test_days_label_today_boundary():
    assert mc._days_label(date(2026, 7, 5), date(2026, 7, 5)) == "Today"


def test_days_label_tomorrow_vs_general_future_boundary():
    assert mc._days_label(date(2026, 7, 6), date(2026, 7, 5)) == "Tomorrow"
    assert mc._days_label(date(2026, 7, 7), date(2026, 7, 5)) == "In 2d"


# ─── _affected_tickers ────────────────────────────────────────────────────────

def test_affected_tickers_none_or_empty_or_missing_sector_column_returns_empty():
    assert mc._affected_tickers("Fed Policy", None) == []
    assert mc._affected_tickers("Fed Policy", pd.DataFrame()) == []
    assert mc._affected_tickers("Fed Policy", pd.DataFrame({"Ticker": ["AAPL"]})) == []


def test_affected_tickers_all_sector_category_returns_every_ticker():
    port_df = pd.DataFrame({
        "Ticker": ["AAPL", "XOM", "JPM"],
        "Sector": ["AI & Cloud", "Energy", "Financials"],
    })
    result = mc._affected_tickers("Fed Policy", port_df)
    assert set(result) == {"AAPL", "XOM", "JPM"}


def test_affected_tickers_per_sector_category_filters_by_severity():
    port_df = pd.DataFrame({
        "Ticker": ["HIGH1", "HIGH2", "LOW1", "LOW2"],
        "Sector": ["AI & Data", "Clean Energy", "Healthcare", "Defense"],
    })
    result = mc._affected_tickers("Inflation", port_df)
    assert set(result) == {"HIGH1", "HIGH2"}


def test_affected_tickers_unrecognized_category_returns_empty():
    port_df = pd.DataFrame({"Ticker": ["AAPL"], "Sector": ["AI & Data"]})
    assert mc._affected_tickers("Not A Real Category", port_df) == []


# ─── is_all_sector_category ──────────────────────────────────────────────────

def test_is_all_sector_category_true_for_fed_policy_and_growth():
    assert mc.is_all_sector_category("Fed Policy") is True
    assert mc.is_all_sector_category("Growth") is True


def test_is_all_sector_category_false_for_per_sector_categories():
    for cat in ("Inflation", "Employment", "Consumer", "Activity"):
        assert mc.is_all_sector_category(cat) is False


def test_is_all_sector_category_false_for_unrecognized():
    assert mc.is_all_sector_category("Not A Real Category") is False


# ─── affected_sectors ─────────────────────────────────────────────────────────

def test_affected_sectors_fed_policy_default_severity_returns_all_sentinel():
    assert mc.affected_sectors("Fed Policy") == {"__ALL__"}


def test_affected_sectors_inflation_at_min_severity_2_matches_real_table():
    expected = {
        "AI & Data", "AI & Cloud", "Semiconductors", "Cybersecurity",
        "Clean Energy", "Consumer Tech", "EV & Auto", "Financials", "Energy",
        # Added 2026-08-16 closing the macro-gate coverage hole — these four
        # labels were reachable from resolve_sector but absent from
        # _SECTOR_IMPACT, so their names could never be macro-suppressed.
        "Industrials", "Communications", "Consumer Staples & Retail",
        "Enterprise Tech",
        # Added 2026-09-02 (F-240 follow-up) — Materials/Utilities/Real Estate,
        # all Inflation severity 3.
        "Materials", "Utilities", "Real Estate",
    }
    assert mc.affected_sectors("Inflation", min_severity=2) == expected


def test_affected_sectors_inflation_at_min_severity_3_is_smaller_subset():
    expected_3 = {
        "AI & Data", "AI & Cloud", "Semiconductors", "Clean Energy",
        # Added 2026-09-02 (F-240 follow-up) — all three land at severity 3.
        "Materials", "Utilities", "Real Estate",
    }
    result = mc.affected_sectors("Inflation", min_severity=3)
    assert result == expected_3
    assert result < mc.affected_sectors("Inflation", min_severity=2)


def test_affected_sectors_unrecognized_category_returns_empty_set():
    assert mc.affected_sectors("Not A Real Category") == set()


# ─── _fred_obs ────────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def test_fred_obs_no_api_key_returns_empty_no_request(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("requests.get should not be called without an api_key")
    monkeypatch.setattr(requests, "get", _boom)
    assert mc._fred_obs("FEDFUNDS", 4, None) == []
    assert mc._fred_obs("FEDFUNDS", 4, "") == []


def test_fred_obs_non_200_status_returns_empty(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(status_code=500, text="server error"))
    assert mc._fred_obs("FEDFUNDS", 4, "key") == []


def test_fred_obs_missing_values_skipped_not_counted_toward_limit(monkeypatch):
    obs = [{"value": "1.0"}, {"value": "."}, {"value": "2.0"}, {"value": "."}, {"value": "3.0"}]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(json_data={"observations": obs}))
    result = mc._fred_obs("FEDFUNDS", 3, "key")
    assert result == [1.0, 2.0, 3.0]


def test_fred_obs_stops_once_limit_reached_even_with_more_available(monkeypatch):
    obs = [{"value": str(float(i))} for i in range(10)]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(json_data={"observations": obs}))
    result = mc._fred_obs("FEDFUNDS", 3, "key")
    assert result == [0.0, 1.0, 2.0]


def test_fred_obs_exception_returns_empty_no_crash(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionError("network down")
    monkeypatch.setattr(requests, "get", _raise)
    assert mc._fred_obs("FEDFUNDS", 4, "key") == []


def test_fred_obs_strips_whitespace_from_api_key(monkeypatch):
    captured = {}
    def fake_get(url, params=None, timeout=None):
        captured.update(params)
        return _FakeResp(json_data={"observations": []})
    monkeypatch.setattr(requests, "get", fake_get)
    mc._fred_obs("FEDFUNDS", 4, "  mykey  ")
    assert captured["api_key"] == "mykey"


# ─── _apply_transform ─────────────────────────────────────────────────────────

def test_apply_transform_empty_vals_returns_none():
    assert mc._apply_transform([], "level", "%") is None


def test_apply_transform_level():
    assert mc._apply_transform([1.234], "level", "%") == "1.23%"


def test_apply_transform_yoy_pct_boundary_12_vs_13_obs():
    vals12 = [float(i) for i in range(12)]
    assert mc._apply_transform(vals12, "yoy_pct", "%") is None

    vals13 = [100.0] + [0.0] * 11 + [80.0]
    expected = (100.0 - 80.0) / 80.0 * 100
    assert mc._apply_transform(vals13, "yoy_pct", "%") == f"{expected:+.2f}%"


def test_apply_transform_yoy_pct_zero_division_returns_none():
    vals = [100.0] + [0.0] * 11 + [0.0]
    assert mc._apply_transform(vals, "yoy_pct", "%") is None


def test_apply_transform_mom_pct_and_mom_diff_require_2_obs():
    assert mc._apply_transform([1.0], "mom_pct", "%") is None
    assert mc._apply_transform([1.0], "mom_diff", "K") is None


def test_apply_transform_mom_diff_comma_thousands_formatting():
    result = mc._apply_transform([2500.0, 1000.0], "mom_diff", "K")
    assert result == "+1,500K"


def test_apply_transform_unrecognized_transform_returns_none():
    assert mc._apply_transform([1.0, 2.0], "bogus_transform", "%") is None


# ─── _fred_series_last_updated ────────────────────────────────────────────────

def test_fred_series_last_updated_no_api_key_returns_none():
    assert mc._fred_series_last_updated("FEDFUNDS", None) is None


def test_fred_series_last_updated_non_200_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(status_code=404))
    assert mc._fred_series_last_updated("FEDFUNDS", "key") is None


def test_fred_series_last_updated_empty_seriess_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(json_data={"seriess": []}))
    assert mc._fred_series_last_updated("FEDFUNDS", "key") is None


def test_fred_series_last_updated_blank_last_updated_returns_none(monkeypatch):
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: _FakeResp(json_data={"seriess": [{"last_updated": "   "}]}),
    )
    assert mc._fred_series_last_updated("FEDFUNDS", "key") is None


def test_fred_series_last_updated_valid_string_parsed_to_date(monkeypatch):
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: _FakeResp(json_data={"seriess": [{"last_updated": "2026-05-12 08:30:05-05"}]}),
    )
    assert mc._fred_series_last_updated("FEDFUNDS", "key") == date(2026, 5, 12)


def test_fred_series_last_updated_exception_returns_none(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(requests, "get", _raise)
    assert mc._fred_series_last_updated("FEDFUNDS", "key") is None


# ─── _fetch_fred ──────────────────────────────────────────────────────────────

def _mk_event(event_name, ev_date, **extra):
    d = {
        "date": ev_date, "time_et": "08:30", "event": event_name, "category": "Inflation",
        "impact": mc.HIGH, "days_label": "", "description": "", "context": "",
        "affected_tickers": [], "previous": None, "estimate": None, "actual": None,
        "source": "static", "released": False, "released_at": None, "drift_days": None,
    }
    d.update(extra)
    return d


def _et(y, m, d, hh=9, mm=30):
    """A tz-aware America/New_York datetime (what _fred_series_last_updated_et returns)."""
    import pytz
    return pytz.timezone("America/New_York").localize(datetime(y, m, d, hh, mm))


def _monthly(vals, newest=date(2026, 6, 1), step=1):
    """Dated rows (date, value), NEWEST FIRST, one per `step` months, no gaps."""
    from stock_analyzer import fred_periods as fp
    return [(fp.sub_months(newest, i * step), v) for i, v in enumerate(vals)]


# GDP is quarterly (step_months 3): 2026-Q2 = 4.0, 2026-Q1 = 3.0
_GDP_2Q = _monthly([4.0, 3.0], newest=date(2026, 4, 1), step=3)
_GDP_1Q = _monthly([4.0], newest=date(2026, 4, 1), step=3)
_GDP_1Q_35 = _monthly([3.5], newest=date(2026, 4, 1), step=3)


def test_fetch_fred_unrecognized_event_skipped_entirely(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("_fred_obs_dated should not be called for an unmapped event")
    monkeypatch.setattr(mc, "_fred_obs_dated", _boom)
    ev = _mk_event("Some Unmapped Event Name", date(2026, 7, 1))
    before = dict(ev)
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev == before


def test_fetch_fred_same_series_fetched_once_for_multiple_events(monkeypatch):
    calls = []
    def fake_fred_obs(series_id, limit, api_key):
        calls.append(series_id)
        return _monthly([114.0 - i for i in range(14)])
    monkeypatch.setattr(mc, "_fred_obs_dated", fake_fred_obs)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)

    ev1 = _mk_event("CPI Inflation", date(2026, 6, 1))
    ev2 = _mk_event("CPI Inflation", date(2026, 5, 1))
    mc._fetch_fred("key", [ev1, ev2], date(2026, 7, 15))
    assert calls == ["CPIAUCNS"]


def test_fetch_fred_future_event_actual_none_previous_from_current_obs(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_1Q_35)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 10, 30))  # future vs. today below
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["actual"] is None
    assert ev["previous"] == "GDP QoQ Ann.: 3.50%"
    assert ev["released_at"] is None
    assert ev["drift_days"] is None
    assert ev["released"] is False


def test_fetch_fred_past_event_actual_from_current_previous_from_shifted(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1))  # past vs. today below
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["actual"] == "GDP QoQ Ann.: 4.00%"
    assert ev["previous"] == "GDP QoQ Ann.: 3.00%"
    assert ev["released"] is True


def test_fetch_fred_prepopulated_actual_and_previous_not_overwritten(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1),
                    actual="Preset Actual", previous="Preset Previous")
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["actual"] == "Preset Actual"
    assert ev["previous"] == "Preset Previous"


def test_fetch_fred_source_stamped_only_when_originally_static(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev_static = _mk_event("GDP Advance Estimate", date(2026, 5, 1), source="static")
    ev_other = _mk_event("GDP Advance Estimate", date(2026, 5, 1), source="custom")
    mc._fetch_fred("key", [ev_static, ev_other], date(2026, 7, 15))
    assert ev_static["source"] == "static+fred"
    assert ev_other["source"] == "custom"


def test_fetch_fred_drift_days_computed_only_for_past_event_with_known_last_updated(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 4, 29))
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1))
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["released_at"] == "2026-04-29"
    assert ev["drift_days"] == 2  # (2026-05-01 - 2026-04-29).days
    assert ev["released"] is True


def test_fetch_fred_released_false_and_drift_none_for_future_event_even_with_known_last_updated(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_1Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 7, 1))
    ev = _mk_event("GDP Advance Estimate", date(2026, 10, 30))
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["released_at"] == "2026-07-01"  # populated regardless of past/future
    assert ev["drift_days"] is None            # only set when event is past/today
    assert ev["released"] is False


def test_fetch_fred_mutates_events_list_in_place(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1))
    events = [ev]
    mc._fetch_fred("key", events, date(2026, 7, 15))
    assert events[0] is ev
    assert ev["actual"] is not None


# ─── MacroSameDay: a same-day event is released only once FRED updated it at/after its time ───

_TODAY_RELEASE = date(2026, 12, 10)  # CPI day, ET date passed in (clock never read)


def test_parse_fred_stamp_et_converts_central_to_eastern_with_dst():
    p = mc._parse_fred_stamp_et
    # CST (-06): 07:00 CT = 08:00 ET (both standard time)
    assert p("2026-12-10 07:00:00-06") == _et(2026, 12, 10, 8, 0)
    # CDT (-05): 07:00 CDT = 08:00 EDT
    assert p("2026-05-12 07:00:00-05") == _et(2026, 5, 12, 8, 0)
    # CT stamp crossing midnight into the next ET day
    got = p("2026-11-09 23:30:00-06")
    assert got == _et(2026, 11, 10, 0, 30) and got.date() == date(2026, 11, 10)
    # an offset-less or garbage stamp cannot be placed on the ET clock
    assert p("2026-12-10 08:30:00") is None
    assert p("not a date") is None
    assert p("") is None and p(None) is None


def test_fred_series_last_updated_et_parses_stamp_and_none_without_key(monkeypatch):
    assert mc._fred_series_last_updated_et("CPIAUCNS", None) is None
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: _FakeResp(json_data={"seriess": [{"last_updated": "2026-12-10 07:31:00-06"}]}),
    )
    assert mc._fred_series_last_updated_et("CPIAUCNS", "key") == _et(2026, 12, 10, 8, 31)


def test_same_day_released_helper_boundaries():
    f = mc._same_day_released
    d = _TODAY_RELEASE
    assert f(_et(2026, 12, 10, 7, 43), d, "08:30") is False   # stamped before the release time
    assert f(_et(2026, 12, 10, 8, 29), d, "08:30") is False
    assert f(_et(2026, 12, 10, 8, 30), d, "08:30") is True    # at the release time
    assert f(_et(2026, 12, 10, 8, 31), d, "08:30") is True
    assert f(_et(2026, 12, 9, 15, 0), d, "08:30") is False    # earlier day
    assert f(_et(2026, 12, 11, 9, 0), d, "08:30") is False    # later day (clock skew)
    # fail safe: missing / unparseable stamp or time_et -> not released
    assert f(None, d, "08:30") is False
    assert f(_et(2026, 12, 10, 9, 0), d, None) is False
    assert f(_et(2026, 12, 10, 9, 0), d, "") is False
    assert f(_et(2026, 12, 10, 9, 0), d, "8.30am") is False


def test_same_day_released_midnight_crossing_stamp_is_still_before_release_time():
    # "2026-12-09 23:30:00-06" = 00:30 ET on the release day: right date, wrong time.
    stamp = mc._parse_fred_stamp_et("2026-12-09 23:30:00-06")
    assert stamp.date() == _TODAY_RELEASE
    assert mc._same_day_released(stamp, _TODAY_RELEASE, "08:30") is False


def test_same_day_released_dst_offsets_both_convert_correctly():
    # CST: 07:31 CT = 08:31 ET -> released; 07:29 CT = 08:29 ET -> not
    assert mc._same_day_released(mc._parse_fred_stamp_et("2026-12-10 07:31:00-06"), _TODAY_RELEASE, "08:30") is True
    assert mc._same_day_released(mc._parse_fred_stamp_et("2026-12-10 07:29:00-06"), _TODAY_RELEASE, "08:30") is False
    # CDT: same wall-clock rule on a summer release day
    summer = date(2026, 7, 14)
    assert mc._same_day_released(mc._parse_fred_stamp_et("2026-07-14 07:31:00-05"), summer, "08:30") is True
    assert mc._same_day_released(mc._parse_fred_stamp_et("2026-07-14 07:29:00-05"), summer, "08:30") is False


def test_fetch_fred_same_day_stamped_before_release_time_reads_as_future_event(monkeypatch):
    # Updated earlier the same ET day (07:43 ET) than the 08:30 release: still the prior print.
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 12, 10, 7, 43))
    ev = _mk_event("GDP Advance Estimate", _TODAY_RELEASE)
    mc._fetch_fred("key", [ev], _TODAY_RELEASE)
    assert ev["actual"] is None
    assert ev["previous"] == "GDP QoQ Ann.: 4.00%"   # newest obs IS the prior print
    assert ev["released"] is False
    assert ev["drift_days"] is None
    assert ev["released_at"] == "2026-12-10"          # "when FRED last updated", date only


def test_fetch_fred_same_day_old_last_updated_reads_as_future_event(monkeypatch):
    # FRED's newest obs (4.0) is still LAST month's print; last_updated is a month old.
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 11, 12))
    ev = _mk_event("GDP Advance Estimate", _TODAY_RELEASE)
    mc._fetch_fred("key", [ev], _TODAY_RELEASE)
    assert ev["actual"] is None
    assert ev["previous"] == "GDP QoQ Ann.: 4.00%"
    assert ev["released"] is False
    assert ev["drift_days"] is None
    assert ev["released_at"] == "2026-11-12"


def test_fetch_fred_same_day_unknown_last_updated_reads_as_not_released(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", _TODAY_RELEASE)
    mc._fetch_fred("key", [ev], _TODAY_RELEASE)
    assert ev["actual"] is None
    assert ev["previous"] == "GDP QoQ Ann.: 4.00%"
    assert ev["released"] is False
    assert ev["drift_days"] is None
    assert ev["released_at"] is None


def test_fetch_fred_same_day_missing_time_et_fails_safe_to_not_released(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 12, 10, 9, 0))
    ev = _mk_event("GDP Advance Estimate", _TODAY_RELEASE, time_et="")
    mc._fetch_fred("key", [ev], _TODAY_RELEASE)
    assert ev["actual"] is None and ev["released"] is False


def test_fetch_fred_same_day_after_release_reads_as_past_event(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 12, 10, 8, 31))
    ev = _mk_event("GDP Advance Estimate", _TODAY_RELEASE)
    mc._fetch_fred("key", [ev], _TODAY_RELEASE)
    assert ev["actual"] == "GDP QoQ Ann.: 4.00%"
    assert ev["previous"] == "GDP QoQ Ann.: 3.00%"
    assert ev["released"] is True
    assert ev["drift_days"] == 0
    assert ev["released_at"] == _TODAY_RELEASE.isoformat()


def test_fetch_fred_same_day_no_key_leaves_event_unenriched(monkeypatch):
    # No FRED key: _fred_obs_dated returns None (real function, no request made).
    def _no_request(*a, **k):
        raise AssertionError("no network call expected without a key")
    monkeypatch.setattr(requests, "get", _no_request)
    ev = _mk_event("GDP Advance Estimate", _TODAY_RELEASE)
    before = dict(ev)
    mc._fetch_fred(None, [ev], _TODAY_RELEASE)
    assert ev == before


def test_fetch_fred_past_and_future_unchanged_by_same_day_rule(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 11, 12))
    past = _mk_event("GDP Advance Estimate", date(2026, 12, 9))
    future = _mk_event("GDP Advance Estimate", date(2026, 12, 11))
    mc._fetch_fred("key", [past, future], _TODAY_RELEASE)
    assert past["actual"] == "GDP QoQ Ann.: 4.00%" and past["released"] is True
    assert past["drift_days"] == (date(2026, 12, 9) - date(2026, 11, 12)).days
    assert future["actual"] is None and future["released"] is False
    assert future["previous"] == "GDP QoQ Ann.: 4.00%" and future["drift_days"] is None


def test_macro_block_map_same_day_unreleased_blocks_before_0830_lifts_at_0830(monkeypatch):
    from stock_analyzer import entry_eligibility as ee
    # CPI series needs a 12-month-earlier row for the YoY; the values don't matter here.
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _monthly([114.0 - i for i in range(14)]))
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 11, 12))
    ev = _mk_event("CPI Inflation", _TODAY_RELEASE, category="Inflation")
    mc._fetch_fred("key", [ev], _TODAY_RELEASE)
    assert ev["released"] is False and ev["actual"] is None
    ev["date"] = _TODAY_RELEASE.isoformat()   # macro_block_map reads the ISO string
    sectors = mc.affected_sectors("Inflation") - {"__ALL__"}
    assert sectors, "Inflation must map to at least one sector for this test to mean anything"
    sec = sorted(sectors)[0]
    d = _TODAY_RELEASE
    before = datetime(d.year, d.month, d.day, 8, 29)
    at = datetime(d.year, d.month, d.day, 8, 30)
    assert ee.macro_hold(sec, ee.macro_block_map([ev], d, before))
    assert ee.macro_hold(sec, ee.macro_block_map([ev], d, at)) is None


def test_macro_block_map_fomc_same_day_never_lifts():
    from stock_analyzer import entry_eligibility as ee
    d = _TODAY_RELEASE
    ev = _mk_event("FOMC Rate Decision", d.isoformat(), category="Fed Policy", time_et="14:00")
    late = datetime(d.year, d.month, d.day, 20, 0)
    m = ee.macro_block_map([ev], d, late)
    assert m["sectors"] == {"__ALL__"}
    ev["released"] = True   # even a "released" FOMC stays held same-day
    assert ee.macro_block_map([ev], d, late)["sectors"] == {"__ALL__"}


# ─── build_macro_calendar ─────────────────────────────────────────────────────

def test_build_macro_calendar_excludes_events_outside_window(monkeypatch):
    monkeypatch.setattr(mc, "_fetch_fred", lambda *a, **k: None)
    today = date(2026, 7, 29)  # real static FOMC + GDP entries both fall on this date
    rows = mc.build_macro_calendar(None, fred_key=None, days_ahead=0, days_behind=0, today=today)
    assert {r["date"] for r in rows} == {today}
    events_today = {r["event"] for r in rows}
    assert "FOMC Rate Decision" in events_today
    assert "GDP Advance Estimate" in events_today


def test_build_macro_calendar_rows_sorted_by_date_then_time(monkeypatch):
    monkeypatch.setattr(mc, "_fetch_fred", lambda *a, **k: None)
    today = date(2026, 7, 29)
    rows = mc.build_macro_calendar(None, fred_key=None, days_ahead=0, days_behind=0, today=today)
    times = [(r["date"], r["time_et"]) for r in rows]
    assert times == sorted(times)
    assert times[0][1] == "08:30"  # GDP (08:30) sorts before FOMC (14:00) same day


def test_build_macro_calendar_row_shape_and_defaults(monkeypatch):
    monkeypatch.setattr(mc, "_fetch_fred", lambda *a, **k: None)
    today = date(2026, 7, 29)
    rows = mc.build_macro_calendar(None, fred_key=None, days_ahead=0, days_behind=0, today=today)
    row = rows[0]
    expected_keys = {
        "date", "time_et", "event", "category", "impact", "days_label",
        "description", "context", "affected_tickers", "previous", "estimate",
        "actual", "source", "released", "released_at", "drift_days",
        "actual_period", "previous_period", "actual_note", "previous_note",
    }
    assert set(row.keys()) == expected_keys
    assert row["actual_period"] is None and row["previous_period"] is None
    assert row["actual_note"] is None and row["previous_note"] is None
    assert row["released"] is False
    assert row["released_at"] is None
    assert row["drift_days"] is None
    assert row["source"] == "static"


# ─── detect_macro_regime ──────────────────────────────────────────────────────

def _fred_obs_dispatch(fred_map):
    def _f(series_id, limit, api_key):
        return fred_map.get(series_id, [])
    return _f


_DAILY_SERIES = ("DGS2", "DGS10")


def _to_dated(series_id, vals):
    """Positional test lists -> dated rows (newest first) with the SAME meaning the
    old positional code gave them: monthly series step back one month per slot
    from Oct 2026 (so obs[12] is Oct 2025), daily series one calendar day per
    slot. Already-dated rows (tuples) and None pass through."""
    from stock_analyzer import fred_periods as fp
    if vals is None or (vals and isinstance(vals[0], tuple)):
        return vals
    if series_id in _DAILY_SERIES:
        return [(date(2026, 11, 13) - pd.Timedelta(days=i).to_pytimedelta(), v)
                for i, v in enumerate(vals)]
    return [(fp.sub_months(date(2026, 10, 1), i), v) for i, v in enumerate(vals)]


def _fred_obs_dated_dispatch(fred_map):
    def _f(series_id, limit, api_key):
        # Honour `limit` like FRED does (newest-first rows), so a too-small
        # regime row count fails a real gate test instead of passing silently.
        rows = _to_dated(series_id, fred_map.get(series_id, []))
        return rows if rows is None else rows[:limit]
    return _f


def _regime_setup(monkeypatch, fred_map, yf_download=None):
    monkeypatch.setattr(mc, "_fred_obs", _fred_obs_dispatch(fred_map))
    monkeypatch.setattr(mc, "_fred_obs_dated", _fred_obs_dated_dispatch(fred_map))
    if yf_download is None:
        def _raise(*a, **k):
            raise RuntimeError("no network in tests")
        monkeypatch.setattr(yfinance, "download", _raise)
    else:
        monkeypatch.setattr(yfinance, "download", yf_download)


def _cpi_obs(yoy_target_pct, base=100.0):
    """13-obs list where (obs[0]-obs[12])/obs[12]*100 == yoy_target_pct exactly
    (bit-for-bit) at the real boundary values under test — additive
    construction (obs[0] = base + target) round-trips exactly through the
    source's /base*100, unlike a multiplicative base*(1+target/100) form,
    which was found to drift ~1e-15 off the boundary and flip a strict `<`."""
    obs = [base] * 13
    obs[0] = base + yoy_target_pct
    obs[12] = base
    return obs


def _mk_market_df(spy_prices, vix_prices):
    return pd.DataFrame(
        list(zip(spy_prices, vix_prices)),
        columns=pd.MultiIndex.from_tuples([("Close", "SPY"), ("Close", "^VIX")]),
    )


def test_detect_macro_regime_all_signals_fail_returns_neutral_fallback(monkeypatch):
    _regime_setup(monkeypatch, {})
    result = mc.detect_macro_regime("key")
    assert result == mc._NEUTRAL_REGIME


def test_detect_macro_regime_fedfunds_trend_holding_boundary(monkeypatch):
    # diff (obs[0]-obs[2]) == -REGIME_FEDFUNDS_TREND_PP exactly -> NOT strictly
    # less-than -> holding. obs[2]=0.0 so diff is a bit-exact subtraction of
    # zero, avoiding the ~1e-15 drift a "5.00 - TREND" then "-5.00" pair had.
    _regime_setup(monkeypatch, {"FEDFUNDS": [-REGIME_FEDFUNDS_TREND_PP, 0.0, 0.0]})
    result = mc.detect_macro_regime("key")
    assert result["fed_trend"] == "holding"


def test_detect_macro_regime_fedfunds_trend_cutting_just_past_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"FEDFUNDS": [-(REGIME_FEDFUNDS_TREND_PP + 0.01), 0.0, 0.0]})
    result = mc.detect_macro_regime("key")
    assert result["fed_trend"] == "cutting"
    assert result["scores"]["rate_cut"] == 3


def test_detect_macro_regime_fedfunds_trend_hiking_just_past_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"FEDFUNDS": [REGIME_FEDFUNDS_TREND_PP + 0.01, 0.0, 0.0]})
    result = mc.detect_macro_regime("key")
    assert result["fed_trend"] == "hiking"
    assert result["scores"]["inflation_fight"] == 3


def test_detect_macro_regime_cpi_hot_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"CPIAUCNS": _cpi_obs(REGIME_CPI_HOT_MIN)})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["cpi_yoy"] == pytest.approx(REGIME_CPI_HOT_MIN)
    assert at_boundary["scores"]["inflation_fight"] == 1  # not >HOT -> falls to elevated branch
    assert at_boundary["scores"]["stagflation_risk"] == 1

    _regime_setup(monkeypatch, {"CPIAUCNS": _cpi_obs(REGIME_CPI_HOT_MIN + 0.01)})
    just_above = mc.detect_macro_regime("key")
    assert just_above["scores"]["inflation_fight"] == 3
    assert just_above["scores"]["stagflation_risk"] == 2
    assert just_above["scores"]["rate_cut"] == -2


def test_detect_macro_regime_cpi_elevated_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"CPIAUCNS": _cpi_obs(REGIME_CPI_ELEVATED_MIN)})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["inflation_fight"] == 1
    assert at_boundary["scores"]["stagflation_risk"] == 1

    _regime_setup(monkeypatch, {"CPIAUCNS": _cpi_obs(REGIME_CPI_ELEVATED_MIN - 0.01)})
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["inflation_fight"] == 0
    assert just_below["scores"]["stagflation_risk"] == 0
    assert just_below["scores"]["rate_cut"] == 0


def test_detect_macro_regime_cpi_controlled_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"CPIAUCNS": _cpi_obs(REGIME_CPI_CONTROLLED_MAX)})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["rate_cut"] == 0  # not strictly < CONTROLLED_MAX -> neutral branch
    assert at_boundary["scores"]["inflation_fight"] == 0

    _regime_setup(monkeypatch, {"CPIAUCNS": _cpi_obs(REGIME_CPI_CONTROLLED_MAX - 0.01)})
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["rate_cut"] == 2
    assert just_below["scores"]["inflation_fight"] == -2


def test_detect_macro_regime_2s10s_inverted_boundary(monkeypatch):
    # spread = dgs10[0] - dgs2[0]; dgs2 pinned to 0.0 so the subtraction is
    # bit-exact against the target (avoids the fp drift a "5.0 + INV" /
    # "5.0" pair introduced for the unemployment/fedfunds cases below).
    _regime_setup(monkeypatch, {"DGS2": [0.0], "DGS10": [REGIME_2S10S_INVERTED_PP]})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["recession_fear"] == 1  # not strictly < inverted -> flat/inv branch

    _regime_setup(monkeypatch, {"DGS2": [0.0], "DGS10": [REGIME_2S10S_INVERTED_PP - 0.01]})
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["recession_fear"] == 3
    assert just_below["scores"]["rate_cut"] == -1


def test_detect_macro_regime_2s10s_flat_zero_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"DGS2": [0.0], "DGS10": [REGIME_2S10S_FLAT_PP]})
    result = mc.detect_macro_regime("key")
    assert result["scores"]["recession_fear"] == 0
    assert result["scores"]["rate_cut"] == 0


def test_detect_macro_regime_2s10s_steep_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"DGS2": [0.0], "DGS10": [REGIME_2S10S_STEEP_PP]})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["rate_cut"] == 0  # not strictly > steep -> else branch

    _regime_setup(monkeypatch, {"DGS2": [0.0], "DGS10": [REGIME_2S10S_STEEP_PP + 0.01]})
    just_above = mc.detect_macro_regime("key")
    assert just_above["scores"]["rate_cut"] == 1
    assert just_above["scores"]["recession_fear"] == -1


def test_detect_macro_regime_unemployment_up_boundary(monkeypatch):
    # delta = obs[0] - obs[3]; obs[3] pinned to 0.0 for a bit-exact subtraction.
    _regime_setup(monkeypatch, {"UNRATE": [REGIME_UNEMP_DELTA_UP_PP, 0.0, 0.0, 0.0]})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["recession_fear"] == 0  # not strictly > up -> stable branch

    _regime_setup(monkeypatch, {"UNRATE": [REGIME_UNEMP_DELTA_UP_PP + 0.01, 0.0, 0.0, 0.0]})
    just_above = mc.detect_macro_regime("key")
    assert just_above["scores"]["recession_fear"] == 2
    assert just_above["scores"]["stagflation_risk"] == 1


def test_detect_macro_regime_unemployment_down_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"UNRATE": [REGIME_UNEMP_DELTA_DOWN_PP, 0.0, 0.0, 0.0]})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["rate_cut"] == 0  # not strictly < down -> stable branch

    _regime_setup(monkeypatch, {"UNRATE": [REGIME_UNEMP_DELTA_DOWN_PP - 0.01, 0.0, 0.0, 0.0]})
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["rate_cut"] == -1
    assert just_below["scores"]["inflation_fight"] == 1
    assert just_below["scores"]["recession_fear"] == -1


def test_detect_macro_regime_hy_spread_stress_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"BAMLH0A0HYM2": [REGIME_HY_SPREAD_STRESS_BP / 100.0]})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["recession_fear"] == 1  # not strictly > stress -> elevated branch
    assert at_boundary["scores"]["stagflation_risk"] == 1

    _regime_setup(monkeypatch, {"BAMLH0A0HYM2": [(REGIME_HY_SPREAD_STRESS_BP + 1) / 100.0]})
    just_above = mc.detect_macro_regime("key")
    assert just_above["scores"]["recession_fear"] == 3
    assert just_above["scores"]["rate_cut"] == -2


def test_detect_macro_regime_hy_spread_elevated_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"BAMLH0A0HYM2": [REGIME_HY_SPREAD_ELEVATED_BP / 100.0]})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["recession_fear"] == 1  # >= elevated

    _regime_setup(monkeypatch, {"BAMLH0A0HYM2": [(REGIME_HY_SPREAD_ELEVATED_BP - 1) / 100.0]})
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["recession_fear"] == 0
    assert just_below["scores"]["rate_cut"] == 0


def test_detect_macro_regime_hy_spread_calm_boundary(monkeypatch):
    _regime_setup(monkeypatch, {"BAMLH0A0HYM2": [REGIME_HY_SPREAD_CALM_BP / 100.0]})
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["rate_cut"] == 0  # not strictly < calm -> normal branch

    _regime_setup(monkeypatch, {"BAMLH0A0HYM2": [(REGIME_HY_SPREAD_CALM_BP - 1) / 100.0]})
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["rate_cut"] == 2
    assert just_below["scores"]["recession_fear"] == -2


def test_detect_macro_regime_spy_20d_bull_boundary(monkeypatch):
    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0 * (1 + REGIME_SPY_20D_BULL_PCT / 100.0)], [17.0, 17.0]))
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["rate_cut"] == 0  # not strictly > bull -> else branch
    assert at_boundary["scores"]["recession_fear"] == 0

    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0 * (1 + (REGIME_SPY_20D_BULL_PCT + 0.1) / 100.0)], [17.0, 17.0]))
    just_above = mc.detect_macro_regime("key")
    assert just_above["scores"]["rate_cut"] == 2
    assert just_above["scores"]["recession_fear"] == -2


def test_detect_macro_regime_spy_20d_bear_boundary(monkeypatch):
    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0 * (1 + REGIME_SPY_20D_BEAR_PCT / 100.0)], [17.0, 17.0]))
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["recession_fear"] == 0  # not strictly < bear -> else branch

    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0 * (1 + (REGIME_SPY_20D_BEAR_PCT - 0.1) / 100.0)], [17.0, 17.0]))
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["recession_fear"] == 1


def test_detect_macro_regime_vix_stress_boundary(monkeypatch):
    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0], [29.0, float(REGIME_VIX_STRESS)]))
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["recession_fear"] == 1  # not strictly > stress -> elevated branch

    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0], [29.0, REGIME_VIX_STRESS + 0.1]))
    just_above = mc.detect_macro_regime("key")
    assert just_above["scores"]["recession_fear"] == 2
    assert just_above["scores"]["rate_cut"] == -1


def test_detect_macro_regime_vix_elevated_boundary(monkeypatch):
    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0], [19.0, float(REGIME_VIX_ELEVATED)]))
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["recession_fear"] == 1  # >= elevated

    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0], [19.0, REGIME_VIX_ELEVATED - 0.01]))
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["recession_fear"] == 0  # not >=elevated, not <calm -> else branch


def test_detect_macro_regime_vix_calm_boundary(monkeypatch):
    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0], [16.0, float(REGIME_VIX_CALM)]))
    at_boundary = mc.detect_macro_regime("key")
    assert at_boundary["scores"]["rate_cut"] == 0  # not strictly < calm -> else branch

    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 100.0], [16.0, REGIME_VIX_CALM - 0.01]))
    just_below = mc.detect_macro_regime("key")
    assert just_below["scores"]["rate_cut"] == 1
    assert just_below["scores"]["recession_fear"] == -1


def test_detect_macro_regime_flat_non_multiindex_columns_branch(monkeypatch):
    df = pd.DataFrame({"SPY": [100.0, 106.0], "^VIX": [17.0, 17.0]})
    _regime_setup(monkeypatch, {}, yf_download=lambda *a, **k: df)
    result = mc.detect_macro_regime("key")
    assert result["scores"]["rate_cut"] == 2  # SPY ret = +6% > bull threshold


def test_detect_macro_regime_rate_cut_hard_gate_reassigns_winner_when_cpi_hot(monkeypatch):
    fred_map = {
        "FEDFUNDS":     [4.50, 4.75, 4.80],       # diff -0.30 -> cutting, rate_cut += 3
        "CPIAUCNS":     _cpi_obs(4.5),            # hot -> inflation_fight+=3, stagflation_risk+=2, rate_cut-=2
        "DGS2":         [4.0],
        "DGS10":        [4.9],                    # spread 0.9 > steep -> rate_cut += 1, recession_fear -= 1
        "UNRATE":       [3.79, 3.9, 3.95, 4.0],    # delta -0.21 -> rate_cut -= 1, inflation_fight += 1, recession_fear -= 1
        "BAMLH0A0HYM2": [2.5],                     # 250bps < calm -> rate_cut += 2, recession_fear -= 2
    }
    _regime_setup(monkeypatch, fred_map, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 105.5], [14.0, 14.0]))  # SPY +5.5% bull (rate_cut+=2, recession_fear-=2), VIX 14<calm (rate_cut+=1, recession_fear-=1)

    result = mc.detect_macro_regime("key")
    assert result["scores"]["rate_cut"] == 6         # would naturally win on score
    assert result["scores"]["inflation_fight"] == 4
    assert result["cpi_yoy"] == pytest.approx(4.5)
    assert result["regime"] == "inflation_fight"     # reassigned by the hard gate, NOT rate_cut
    assert result["label"] == "Inflation Fight"


def test_detect_macro_regime_winning_score_exactly_at_min_falls_back_to_neutral(monkeypatch):
    _regime_setup(monkeypatch, {"CPIAUCNS": _cpi_obs(REGIME_CPI_ELEVATED_MIN)})  # inflation_fight == 1 == MIN
    result = mc.detect_macro_regime("key")
    assert result["regime"] == "neutral"
    assert result["label"] == "Data-Dependent"
    assert result["source"] == "fred+market"  # source stays real even though regime falls back


def test_detect_macro_regime_winning_score_above_min_keeps_regime(monkeypatch):
    _regime_setup(monkeypatch, {"UNRATE": [4.5, 4.2, 4.1, 4.0]})  # delta +0.5 -> recession_fear += 2 (> MIN)
    result = mc.detect_macro_regime("key")
    assert result["regime"] == "recession_fear"


def test_detect_macro_regime_clean_inflation_fight_full_shape(monkeypatch):
    _regime_setup(monkeypatch, {"CPIAUCNS": _cpi_obs(5.0)})  # hot, unambiguous winner
    result = mc.detect_macro_regime("key")
    assert result["regime"] == "inflation_fight"
    assert result["label"] == "Inflation Fight"
    assert result["icon"] == "🔥"
    assert result["color"] == "#f59e0b"
    assert result["bg"] == "#1a1200"
    assert result["rationale"] == (
        "Inflation and/or Fed hiking pressure dominant. "
        "Strong data = more hikes = growth stocks under pressure."
    )
    assert result["cpi_yoy"] == pytest.approx(5.0)
    assert result["fed_trend"] == "unknown"
    assert result["source"] == "fred+market"
    assert result["confidence"] == 60  # 3 / (3+0+0+2) * 100
    assert "scores" in result and "signals" in result


# ── Agency-verified release dates (tripwire, 2026-11-10) ─────────────────────
# Read from the agencies' own schedules on 2026-11-10 after the hand-typed
# Oct 2026 CPI row (11-12) turned out wrong (BLS released it 11-10):
#   CPI / PPI / jobs: bls.gov/schedule/news_release/{cpi,ppi,empsit}.htm
#   Retail Sales:     census.gov/retail/release_schedule.html
# A change here must cite the source page in the commit; never edit from memory.
_VERIFIED_2026_11_10 = {
    "CPI Inflation":       ["2026-11-10", "2026-12-10", "2027-01-13"],
    "PPI Producer Prices": ["2026-11-13", "2026-12-15", "2027-01-14"],
    "Non-Farm Payrolls":   ["2026-12-04", "2027-01-08"],
    "Retail Sales":        ["2026-11-17", "2026-12-16", "2027-01-15"],
}


def test_static_rows_match_agency_schedules_read_2026_11_10():
    from stock_analyzer import macro_calendar as mc
    for name, expected in _VERIFIED_2026_11_10.items():
        got = sorted(r[0] for r in mc._STATIC
                     if r[2] == name and "2026-11-10" <= r[0] <= "2027-01-31")
        assert got == expected, f"{name}: {got} != agency schedule {expected}"
        times = {r[1] for r in mc._STATIC
                 if r[2] == name and "2026-11-10" <= r[0] <= "2027-01-31"}
        assert times == {"08:30"}, f"{name}: release time {times}"

def test_verified_rows_carry_their_period_label():
    from stock_analyzer import macro_calendar as mc
    labels = {(r[0], r[2]): r[5] for r in mc._STATIC}
    assert labels[("2026-11-10", "CPI Inflation")] == "Oct 2026 CPI"
    assert labels[("2026-12-10", "CPI Inflation")] == "Nov 2026 CPI"
    assert labels[("2026-12-15", "PPI Producer Prices")] == "Nov 2026 PPI"
    assert labels[("2026-11-17", "Retail Sales")] == "Oct 2026 Retail Sales"
    assert labels[("2026-12-16", "Retail Sales")] == "Nov 2026 Retail Sales"


# ═══════════════════════════════════════════════════════════════════════════════
# CpiYoyGap C2 — date-keyed FRED changes wired into macro_calendar
# (docs/plans/cpi-yoy-gap-spec.md; pure core in tests/test_fred_periods.py)
# ═══════════════════════════════════════════════════════════════════════════════

# FRED CPIAUCNS as the lead transcribed it from fredgraph.csv on 2026-11-16
# (None = the "." row: BLS never published an October 2025 CPI).
_REAL_CPI_2024_09_TO_2026_10 = [
    ("2024-09-01", 315.301), ("2024-10-01", 315.664), ("2024-11-01", 315.493),
    ("2024-12-01", 315.605), ("2025-01-01", 317.671), ("2025-02-01", 319.082),
    ("2025-03-01", 319.799), ("2025-04-01", 320.795), ("2025-05-01", 321.465),
    ("2025-06-01", 322.561), ("2025-07-01", 323.048), ("2025-08-01", 323.976),
    ("2025-09-01", 324.800), ("2025-10-01", None),    ("2025-11-01", 324.122),
    ("2025-12-01", 324.054), ("2026-01-01", 325.252), ("2026-02-01", 326.785),
    ("2026-03-01", 330.213), ("2026-04-01", 333.020), ("2026-05-01", 335.123),
    ("2026-06-01", 333.952), ("2026-07-01", 333.918), ("2026-08-01", 334.980),
    ("2026-09-01", 336.940), ("2026-10-01", 337.511),
]


def _real_cpi(extra=()):
    rows = [(date.fromisoformat(d), v) for d, v in _REAL_CPI_2024_09_TO_2026_10]
    rows += [(date.fromisoformat(d), v) for d, v in extra]
    return sorted(rows, key=lambda t: t[0], reverse=True)


# ─── _fred_obs_dated: offline (None) is not empty ([]) ─────────────────────────

def test_fred_obs_dated_no_key_is_none_and_no_request(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("requests.get should not be called without an api_key")
    monkeypatch.setattr(requests, "get", _boom)
    assert mc._fred_obs_dated("CPIAUCNS", 26, None) is None
    assert mc._fred_obs_dated("CPIAUCNS", 26, "") is None


def test_fred_obs_dated_http_error_and_exception_are_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(status_code=500, text="boom"))
    assert mc._fred_obs_dated("CPIAUCNS", 26, "key") is None

    def _raise(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(requests, "get", _raise)
    assert mc._fred_obs_dated("CPIAUCNS", 26, "key") is None


def test_fred_obs_dated_no_rows_is_empty_list_not_none(monkeypatch):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _FakeResp(json_data={"observations": []}))
    got = mc._fred_obs_dated("CPIAUCNS", 26, "key")
    assert got == [] and got is not None


def test_fred_obs_dated_keeps_dot_rows_newest_first_and_asks_for_exactly_limit(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(params)
        # unsorted on purpose: the parser must return newest first
        return _FakeResp(json_data={"observations": [
            {"date": "2025-09-01", "value": "324.800"},
            {"date": "2025-10-01", "value": "."},
            {"date": "2025-11-01", "value": "324.122"},
        ]})
    monkeypatch.setattr(requests, "get", fake_get)
    got = mc._fred_obs_dated("CPIAUCNS", 26, "  k  ")
    assert got == [(date(2025, 11, 1), 324.122), (date(2025, 10, 1), None),
                   (date(2025, 9, 1), 324.800)]
    assert captured["limit"] == 26          # rows, "." rows included: no +4 slack
    assert captured["api_key"] == "k"


def test_fred_map_row_counts_cover_current_and_previous_by_date():
    for name, cfg in mc._FRED_MAP.items():
        want = 26 if cfg["transform"] == "yoy_pct" else 6
        assert cfg["limit"] >= want, name
    assert mc._FRED_MAP["GDP Advance Estimate"]["step_months"] == 3
    assert all("step_months" not in c for n, c in mc._FRED_MAP.items()
               if n != "GDP Advance Estimate")


# ─── _fetch_fred on the real 2025-10 CPI gap ───────────────────────────────────

def _cpi_event(ev_date):
    return _mk_event("CPI Inflation", ev_date)


def test_fetch_fred_oct_2026_cpi_actual_not_computable_but_released(monkeypatch):
    """The 11-10 CPI: Oct 2026 has no 12-month figure (Oct 2025 is a "." row). The
    old positional code printed a 13-month +3.91%; now actual is None, the period
    and the reason travel in their own fields, previous is Sep 2026's true +3.74%."""
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _real_cpi())
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 11, 10, 8, 31))
    ev = _cpi_event(date(2026, 11, 10))
    mc._fetch_fred("key", [ev], date(2026, 11, 12))
    assert ev["actual"] is None
    assert ev["actual_period"] == "Oct 2026"
    assert ev["actual_note"] == "no 12-month figure: FRED has no Oct 2025 value"
    assert ev["previous"] == "CPI YoY: +3.74%"
    assert ev["previous_period"] == "Sep 2026"
    assert ev["previous_note"] is None
    assert ev["released"] is True            # FRED posted Oct 2026: still a released event
    assert ev["drift_days"] == 0


def test_fetch_fred_next_cpi_previous_is_the_not_computable_oct_reading(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _real_cpi())
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 11, 10, 8, 31))
    ev = _cpi_event(date(2026, 12, 10))
    mc._fetch_fred("key", [ev], date(2026, 11, 12))
    assert ev["actual"] is None and ev["released"] is False
    assert ev["previous"] is None
    assert ev["previous_period"] == "Oct 2026"
    assert ev["previous_note"] == "no 12-month figure: FRED has no Oct 2025 value"


def test_fetch_fred_after_the_dec_10_print_actual_is_true_12_month_previous_still_gapped(monkeypatch):
    rows = _real_cpi(extra=[("2026-11-01", 338.000)])
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: rows)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 12, 10, 8, 31))
    ev = _cpi_event(date(2026, 12, 10))
    mc._fetch_fred("key", [ev], date(2026, 12, 10))
    want = (338.000 - 324.122) / 324.122 * 100            # Nov 2026 vs Nov 2025
    assert ev["actual"] == f"CPI YoY: {want:+.2f}%"
    assert ev["actual_period"] == "Nov 2026" and ev["actual_note"] is None
    assert ev["previous"] is None                          # Oct 2026: base Oct 2025 is the gap
    assert ev["previous_period"] == "Oct 2026"
    assert "FRED has no Oct 2025 value" in ev["previous_note"]
    assert ev["released"] is True


def test_fetch_fred_dot_row_for_current_month_does_not_borrow_an_older_base(monkeypatch):
    """Positional code skipped "." rows and silently compared against the next older
    value. A trailing "." (Nov listed without a number) leaves Oct as the newest
    VALUED reading, never a re-based Nov."""
    rows = _real_cpi(extra=[("2026-11-01", None)])
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: rows)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _cpi_event(date(2026, 11, 10))
    mc._fetch_fred("key", [ev], date(2026, 11, 12))
    assert ev["actual_period"] == "Oct 2026" and ev["actual"] is None


def test_fetch_fred_series_with_no_valued_row_leaves_event_untouched(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: [(date(2026, 4, 1), None)])
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1))
    before = dict(ev)
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev == before


def test_fetch_fred_gdp_previous_is_the_prior_quarter_with_quarter_labels(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 7, 29))
    mc._fetch_fred("key", [ev], date(2026, 8, 5))
    assert ev["actual"] == "GDP QoQ Ann.: 4.00%" and ev["actual_period"] == "Q2 2026"
    assert ev["previous"] == "GDP QoQ Ann.: 3.00%" and ev["previous_period"] == "Q1 2026"


def test_fetch_fred_gdp_missing_prior_quarter_is_not_the_quarter_before_that(monkeypatch):
    rows = _monthly([4.0, 2.0], newest=date(2026, 4, 1), step=6)   # Q2 2026, Q4 2025 (Q1 absent)
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: rows)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 7, 29))
    mc._fetch_fred("key", [ev], date(2026, 8, 5))
    assert ev["actual"] == "GDP QoQ Ann.: 4.00%"
    assert ev["previous"] is None and ev["previous_period"] == "Q1 2026"
    assert ev["previous_note"] == "no figure: FRED has no Q1 2026 value"


def test_fetch_fred_preset_actual_keeps_its_period_and_note_untouched(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _GDP_2Q)
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1), actual="Preset Actual")
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["actual"] == "Preset Actual"
    assert ev.get("actual_period") is None and ev.get("actual_note") is None
    assert ev["released"] is True


# ─── Parser guard: macro_playbook._parse_number reads the FIRST number ─────────

def _no_gap_series(cfg):
    """A gap-free dated series for the config's step with known positive values."""
    step = cfg.get("step_months", 1)
    return _monthly([200.0 - 3.0 * i + 0.125 * i * i for i in range(cfg["limit"])],
                    newest=date(2026, 4, 1) if step == 3 else date(2026, 6, 1), step=step)


def test_every_fetch_fred_string_parses_to_its_own_value_and_matches_positional_math(monkeypatch):
    from stock_analyzer.macro_playbook import _parse_number
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    for name, cfg in mc._FRED_MAP.items():
        obs = _no_gap_series(cfg)
        monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, _o=obs, **k: _o)
        ev = _mk_event(name, date(2026, 7, 1))
        mc._fetch_fred("key", [ev], date(2026, 8, 1))
        vals = [v for _, v in obs]
        # no-gap equivalence with the legacy positional transform
        want_cur = mc._apply_transform(vals, cfg["transform"], cfg["unit"])
        want_prev = mc._apply_transform(vals[1:], cfg["transform"], cfg["unit"])
        assert ev["actual"] == f"{cfg['label']}: {want_cur}", name
        assert ev["previous"] == f"{cfg['label']}: {want_prev}", name
        # the parser sees the value, never a month / year / note digit
        assert _parse_number(ev["actual"]) == pytest.approx(
            float(want_cur.replace(",", "").rstrip("%K")), abs=1e-9), name
        assert _parse_number(ev["previous"]) == pytest.approx(
            float(want_prev.replace(",", "").rstrip("%K")), abs=1e-9), name
        assert ev["actual_note"] is None and ev["previous_note"] is None
        assert ev["actual_period"] and ev["previous_period"]
        # a future event's previous carries the same exact shape
        fut = _mk_event(name, date(2026, 9, 1))
        mc._fetch_fred("key", [fut], date(2026, 8, 1))
        assert fut["previous"] == ev["actual"], name


def test_negative_and_zero_values_keep_the_value_first_in_the_string(monkeypatch):
    from stock_analyzer.macro_playbook import _parse_number
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: None)
    rows = _monthly([-1.6, 0.0], newest=date(2026, 4, 1), step=3)     # GDP QoQ -1.6, 0.0
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: rows)
    ev = _mk_event("GDP Advance Estimate", date(2026, 7, 29))
    mc._fetch_fred("key", [ev], date(2026, 8, 5))
    assert ev["actual"] == "GDP QoQ Ann.: -1.60%" and _parse_number(ev["actual"]) == -1.6
    assert ev["previous"] == "GDP QoQ Ann.: 0.00%" and _parse_number(ev["previous"]) == 0.0


def test_not_computable_actual_classifies_to_none_while_the_event_reads_released(monkeypatch):
    from stock_analyzer.macro_playbook import _SCENARIO_THRESHOLDS, _parse_number, classify_scenario
    assert "CPI Inflation" in _SCENARIO_THRESHOLDS      # else the None below proves nothing
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: _real_cpi())
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 11, 10, 8, 31))
    ev = _cpi_event(date(2026, 11, 10))
    mc._fetch_fred("key", [ev], date(2026, 11, 12))
    assert classify_scenario("CPI Inflation", ev["actual"]) is None
    assert ev["released"] is True
    # the previous string still parses to its own value, not to a year in a label
    assert _parse_number(ev["previous"]) == pytest.approx(3.74)


# ─── Risk 2: the macro hold and the Brief's release signature keep their behaviour ──

def test_not_computable_same_day_release_lifts_the_hold_as_before_and_not_earlier(monkeypatch):
    from stock_analyzer import entry_eligibility as ee
    from stock_analyzer.macro_release import release_signature
    rows = _real_cpi()
    monkeypatch.setattr(mc, "_fred_obs_dated", lambda *a, **k: rows)
    sectors = mc.affected_sectors("Inflation") - {"__ALL__"}
    assert sectors
    sec = sorted(sectors)[0]
    d = date(2026, 11, 10)

    # FRED posted the (not-computable) Oct 2026 CPI at 08:31 ET: released True lifts
    # the hold on the event day, exactly as the old 13-month "actual" did.
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 11, 10, 8, 31))
    ev = _cpi_event(d)
    mc._fetch_fred("key", [ev], d)
    assert ev["actual"] is None and ev["released"] is True
    ev["date"] = d.isoformat()
    assert ee.macro_hold(sec, ee.macro_block_map([ev], d, datetime(2026, 11, 10, 8, 29))) is None

    # the day BEFORE the release the same event is future: released False, hold on
    ev2 = _cpi_event(d)
    mc._fetch_fred("key", [ev2], date(2026, 11, 9))
    assert ev2["released"] is False
    ev2["date"] = d.isoformat()
    assert ee.macro_hold(sec, ee.macro_block_map([ev2], date(2026, 11, 9),
                                                 datetime(2026, 11, 9, 20, 0)))

    # same day, FRED not updated yet (stamp is last month's): hold stays until 08:30
    monkeypatch.setattr(mc, "_fred_series_last_updated_et", lambda *a, **k: _et(2026, 10, 14, 9, 0))
    ev3 = _cpi_event(d)
    mc._fetch_fred("key", [ev3], d)
    assert ev3["released"] is False
    ev3["date"] = d.isoformat()
    assert ee.macro_hold(sec, ee.macro_block_map([ev3], d, datetime(2026, 11, 10, 8, 29)))
    assert ee.macro_hold(sec, ee.macro_block_map([ev3], d, datetime(2026, 11, 10, 8, 30))) is None

    # the Brief's memo signature never reads actual / released / the new fields
    ev_hi = {"event": "CPI Inflation", "date": d, "time_et": "08:30", "impact": mc.HIGH}
    base = release_signature([ev_hi], d, datetime(2026, 11, 10, 9, 0))
    enriched = dict(ev_hi, actual=None, released=True, actual_period="Oct 2026",
                    actual_note="no 12-month figure: FRED has no Oct 2025 value")
    assert release_signature([enriched], d, datetime(2026, 11, 10, 9, 0)) == base


# ─── detect_macro_regime: CPI by date, bounded fallback, unknown blocks rate_cut ──

def _cpi_rows(latest=None, fallback=None, unavailable=False, newest=date(2026, 10, 1), n=26):
    """Dated CPI rows (100.0 a year back, additive so the yoy is bit-exact):
      latest      the newest month has a true 12-month change of `latest`
      fallback    the newest month's BASE is a gap; the month before has `fallback`
      unavailable the newest month AND the one before both lack a base
    """
    from stock_analyzer import fred_periods as fp
    months = [fp.sub_months(newest, i) for i in range(n)]
    val = {m: 100.0 for m in months}
    if latest is not None:
        val[newest] = 100.0 + latest
    if fallback is not None:
        val[fp.sub_months(newest, 12)] = None
        val[fp.sub_months(newest, 1)] = 100.0 + fallback
    if unavailable:
        val[fp.sub_months(newest, 12)] = None
        val[fp.sub_months(newest, 13)] = None
    return [(m, val[m]) for m in months]


def _risk_on(monkeypatch, cpi):
    """Calm VIX (rate_cut +1), SPY +5.5% (+2), calm HY 250bps (+2) = 5 before CPI."""
    fred_map = {"BAMLH0A0HYM2": [2.5]}
    if cpi != "absent":
        fred_map["CPIAUCNS"] = cpi
    _regime_setup(monkeypatch, fred_map, yf_download=lambda *a, **k: _mk_market_df(
        [100.0, 105.5], [14.0, 14.0]))
    return mc.detect_macro_regime("key")


def test_regime_cpi_latest_controlled_lets_rate_cut_win(monkeypatch):
    r = _risk_on(monkeypatch, _cpi_rows(latest=2.0))
    assert r["regime"] == "rate_cut"
    assert r["cpi_yoy"] == pytest.approx(2.0)
    assert r["cpi_yoy_basis"] == "latest" and r["cpi_yoy_month"] == date(2026, 10, 1)
    assert ("CPI YoY", "2.0%", "✅", "rate_cut") in r["signals"]


def test_regime_cpi_exactly_at_controlled_max_is_allowed(monkeypatch):
    r = _risk_on(monkeypatch, _cpi_rows(latest=REGIME_CPI_CONTROLLED_MAX))
    assert r["cpi_yoy"] == pytest.approx(REGIME_CPI_CONTROLLED_MAX)
    assert r["regime"] == "rate_cut"          # gate is strictly >


def test_regime_cpi_just_over_controlled_max_blocks_rate_cut(monkeypatch):
    r = _risk_on(monkeypatch, _cpi_rows(latest=REGIME_CPI_CONTROLLED_MAX + 0.01))
    assert r["regime"] != "rate_cut"
    assert r["regime"] == "neutral"           # best other regime scores 0 <= MIN


def test_regime_fallback_month_above_ceiling_blocks_rate_cut(monkeypatch):
    r = _risk_on(monkeypatch, _cpi_rows(fallback=3.0))
    assert r["cpi_yoy_basis"] == "fallback" and r["cpi_yoy_month"] == date(2026, 9, 1)
    assert r["cpi_yoy"] == pytest.approx(3.0)
    assert r["regime"] != "rate_cut"


def test_regime_fallback_month_controlled_allows_rate_cut_and_names_the_months(monkeypatch):
    r = _risk_on(monkeypatch, _cpi_rows(fallback=2.0))
    assert r["regime"] == "rate_cut"
    assert r["cpi_yoy_basis"] == "fallback"
    pill = [s for s in r["signals"] if s[0] == "CPI YoY"]
    assert pill == [("CPI YoY", "2.0% (Sep 2026; Oct 2026 has no 12-month figure)",
                     "✅", "rate_cut")]


def test_regime_cpi_unavailable_blocks_rate_cut_with_a_visible_pill(monkeypatch):
    r = _risk_on(monkeypatch, _cpi_rows(unavailable=True))
    assert r["regime"] != "rate_cut"
    assert r["cpi_yoy"] is None and r["cpi_yoy_month"] is None
    assert r["cpi_yoy_basis"] == "unavailable"
    assert ("CPI YoY", "unavailable (Rate-Cut Optimism can't be selected)", "⚠️", None) in r["signals"]


@pytest.mark.parametrize("cpi", [None, [], "absent"])
def test_regime_cpi_series_down_or_empty_blocks_rate_cut(monkeypatch, cpi):
    r = _risk_on(monkeypatch, cpi)
    assert r["regime"] != "rate_cut"
    assert r["cpi_yoy"] is None and r["cpi_yoy_basis"] == "unavailable"


def test_regime_no_fred_key_blocks_rate_cut_even_when_market_signals_are_risk_on(monkeypatch):
    """No key: every FRED fetch is offline (None). Market-only signals give
    rate_cut 3 (SPY +2, VIX +1); the old None-skips-the-gate code returned
    rate_cut here."""
    def _no_net(*a, **k):
        raise AssertionError("no FRED request without a key")
    monkeypatch.setattr(requests, "get", _no_net)
    monkeypatch.setattr(yfinance, "download",
                        lambda *a, **k: _mk_market_df([100.0, 105.5], [14.0, 14.0]))
    r = mc.detect_macro_regime(None)
    assert r["scores"]["rate_cut"] == 3
    assert r["regime"] != "rate_cut"
    assert r["cpi_yoy"] is None and r["cpi_yoy_basis"] == "unavailable"


@pytest.mark.parametrize("cpi,label", [
    (_cpi_rows(latest=-1.0), "latest -1.0"), (_cpi_rows(latest=2.0), "latest 2.0"),
    (_cpi_rows(latest=REGIME_CPI_CONTROLLED_MAX), "latest at max"),
    (_cpi_rows(latest=REGIME_CPI_CONTROLLED_MAX + 0.01), "latest over"),
    (_cpi_rows(latest=4.5), "latest hot"),
    (_cpi_rows(fallback=2.0), "fallback controlled"), (_cpi_rows(fallback=3.0), "fallback over"),
    (_cpi_rows(unavailable=True), "unavailable"), (None, "series None"), ([], "series empty"),
])
def test_regime_rate_cut_implies_a_numeric_cpi_at_or_under_the_ceiling(monkeypatch, cpi, label):
    r = _risk_on(monkeypatch, cpi)
    if r["regime"] == "rate_cut":
        assert r["cpi_yoy"] is not None and r["cpi_yoy"] <= REGIME_CPI_CONTROLLED_MAX, label


def test_regime_real_2025_10_gap_falls_back_to_sep_2026_true_12_month(monkeypatch):
    _regime_setup(monkeypatch, {"CPIAUCNS": _real_cpi()})
    r = mc.detect_macro_regime("key")
    assert r["cpi_yoy_basis"] == "fallback" and r["cpi_yoy_month"] == date(2026, 9, 1)
    assert r["cpi_yoy"] == pytest.approx((336.940 - 324.800) / 324.800 * 100)   # 3.74, not 3.91
    assert ("CPI YoY", "3.7% (Sep 2026; Oct 2026 has no 12-month figure)",
            "⚠️", "inflation_fight") in r["signals"]


def test_regime_cpi_fetch_asks_for_rows_derived_from_the_fallback_bound(monkeypatch):
    from stock_analyzer.constants import REGIME_CPI_FALLBACK_MAX_MONTHS
    seen = {}

    def fake(series_id, limit, api_key):
        seen[series_id] = limit
        return None

    def _raise(*a, **k):
        raise RuntimeError("no network in tests")
    monkeypatch.setattr(mc, "_fred_obs", lambda *a, **k: [])
    monkeypatch.setattr(mc, "_fred_obs_dated", fake)
    monkeypatch.setattr(yfinance, "download", _raise)
    mc.detect_macro_regime("key")
    assert seen["CPIAUCNS"] == 13 + REGIME_CPI_FALLBACK_MAX_MONTHS


# ─── the other date-keyed regime signals ──────────────────────────────────────

def test_regime_fedfunds_trend_is_by_date_not_position(monkeypatch):
    """Two months back by DATE: a gap in the middle month changes nothing, a gap
    in the base month drops the signal (the old code borrowed an older value)."""
    from stock_analyzer import fred_periods as fp
    newest = date(2026, 10, 1)
    ok = [(newest, 3.50), (fp.sub_months(newest, 1), None), (fp.sub_months(newest, 2), 3.75)]
    _regime_setup(monkeypatch, {"FEDFUNDS": ok})
    r = mc.detect_macro_regime("key")
    assert r["fed_trend"] == "cutting"

    gap_base = [(newest, 3.50), (fp.sub_months(newest, 1), 3.60),
                (fp.sub_months(newest, 2), None), (fp.sub_months(newest, 3), 3.75)]
    _regime_setup(monkeypatch, {"FEDFUNDS": gap_base})
    assert mc.detect_macro_regime("key") == mc._NEUTRAL_REGIME     # nothing else was read


def test_regime_unemployment_base_in_the_gap_drops_the_signal_with_a_pill(monkeypatch):
    from stock_analyzer import fred_periods as fp
    newest = date(2026, 1, 1)                       # base = Oct 2025: UNRATE has no value
    rows = [(newest, 4.9), (fp.sub_months(newest, 1), 4.5), (fp.sub_months(newest, 2), 4.5),
            (fp.sub_months(newest, 3), None), (fp.sub_months(newest, 4), 4.3)]
    # the positional code would have used obs[3] = 4.3 (Sep 2025): +0.6pp, "recession fear"
    assert 4.9 - 4.3 > REGIME_UNEMP_DELTA_UP_PP
    _regime_setup(monkeypatch, {"UNRATE": rows, "BAMLH0A0HYM2": [2.5]})
    r = mc.detect_macro_regime("key")
    assert ("Unemployment Δ3m", "not computable: FRED has no Oct 2025 value", "⚠️", None) in r["signals"]
    assert r["scores"]["recession_fear"] == -2      # only the calm-HY term; unemployment added nothing


def test_regime_2s10s_uses_the_newest_date_both_series_have(monkeypatch):
    d = date(2026, 11, 13)
    dgs2 = [(d, None), (date(2026, 11, 12), 4.0), (date(2026, 11, 11), 3.0)]
    dgs10 = [(d, 4.5), (date(2026, 11, 12), 4.0 + 0.9), (date(2026, 11, 11), 3.5)]
    _regime_setup(monkeypatch, {"DGS2": dgs2, "DGS10": dgs10})
    r = mc.detect_macro_regime("key")
    # 11-12 is the newest date both have: spread 0.90 (steep), NOT 4.5 - 4.0
    assert any(s[0] == "2s10s Spread" and s[1].startswith("+0.90%") for s in r["signals"])
