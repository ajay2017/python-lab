"""
Tests for stock_analyzer/macro_calendar.py — the FRED-based economic calendar
and regime detector. Zero coverage before this batch, and more decision-
critical than most: `detect_macro_regime()` feeds the Home page's Macro
Signals banner and several downstream advisors, so every boundary in the
7-signal weighted scorer (Fed Funds trend, CPI YoY, 2s10s spread, unemployment
delta, HY credit spread, SPY 20d return, VIX level) is tested at-boundary vs
just-past using the real `constants.py` values, plus the "rate_cut hard gate"
that reassigns the winner when CPI is not actually controlled, and the
winning-score-too-weak fallback to "neutral". `_fred_obs`/`_fred_series_last_
updated` locally import `requests` inside their functions — the real
installed module is monkeypatched directly (its local import resolves to the
same cached module object), mirroring test_stress_test.py's yfinance pattern
(also used here for the SPY/VIX market-data signals).
"""
from datetime import date

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
    }
    assert mc.affected_sectors("Inflation", min_severity=2) == expected


def test_affected_sectors_inflation_at_min_severity_3_is_smaller_subset():
    expected_3 = {"AI & Data", "AI & Cloud", "Semiconductors", "Clean Energy"}
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


def test_fetch_fred_unrecognized_event_skipped_entirely(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("_fred_obs should not be called for an unmapped event")
    monkeypatch.setattr(mc, "_fred_obs", _boom)
    ev = _mk_event("Some Unmapped Event Name", date(2026, 7, 1))
    before = dict(ev)
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev == before


def test_fetch_fred_same_series_fetched_once_for_multiple_events(monkeypatch):
    calls = []
    def fake_fred_obs(series_id, limit, api_key):
        calls.append(series_id)
        return [114.0 - i for i in range(14)]
    monkeypatch.setattr(mc, "_fred_obs", fake_fred_obs)
    monkeypatch.setattr(mc, "_fred_series_last_updated", lambda *a, **k: None)

    ev1 = _mk_event("CPI Inflation", date(2026, 6, 1))
    ev2 = _mk_event("CPI Inflation", date(2026, 5, 1))
    mc._fetch_fred("key", [ev1, ev2], date(2026, 7, 15))
    assert calls == ["CPIAUCNS"]


def test_fetch_fred_future_event_actual_none_previous_from_current_obs(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs", lambda *a, **k: [3.5])
    monkeypatch.setattr(mc, "_fred_series_last_updated", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 10, 30))  # future vs. today below
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["actual"] is None
    assert ev["previous"] == "GDP QoQ Ann.: 3.50%"
    assert ev["released_at"] is None
    assert ev["drift_days"] is None
    assert ev["released"] is False


def test_fetch_fred_past_event_actual_from_current_previous_from_shifted(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs", lambda *a, **k: [4.0, 3.0])
    monkeypatch.setattr(mc, "_fred_series_last_updated", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1))  # past vs. today below
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["actual"] == "GDP QoQ Ann.: 4.00%"
    assert ev["previous"] == "GDP QoQ Ann.: 3.00%"
    assert ev["released"] is True


def test_fetch_fred_prepopulated_actual_and_previous_not_overwritten(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs", lambda *a, **k: [4.0, 3.0])
    monkeypatch.setattr(mc, "_fred_series_last_updated", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1),
                    actual="Preset Actual", previous="Preset Previous")
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["actual"] == "Preset Actual"
    assert ev["previous"] == "Preset Previous"


def test_fetch_fred_source_stamped_only_when_originally_static(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs", lambda *a, **k: [4.0, 3.0])
    monkeypatch.setattr(mc, "_fred_series_last_updated", lambda *a, **k: None)
    ev_static = _mk_event("GDP Advance Estimate", date(2026, 5, 1), source="static")
    ev_other = _mk_event("GDP Advance Estimate", date(2026, 5, 1), source="custom")
    mc._fetch_fred("key", [ev_static, ev_other], date(2026, 7, 15))
    assert ev_static["source"] == "static+fred"
    assert ev_other["source"] == "custom"


def test_fetch_fred_drift_days_computed_only_for_past_event_with_known_last_updated(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs", lambda *a, **k: [4.0, 3.0])
    monkeypatch.setattr(mc, "_fred_series_last_updated", lambda *a, **k: date(2026, 4, 29))
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1))
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["released_at"] == "2026-04-29"
    assert ev["drift_days"] == 2  # (2026-05-01 - 2026-04-29).days
    assert ev["released"] is True


def test_fetch_fred_released_false_and_drift_none_for_future_event_even_with_known_last_updated(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs", lambda *a, **k: [4.0])
    monkeypatch.setattr(mc, "_fred_series_last_updated", lambda *a, **k: date(2026, 7, 1))
    ev = _mk_event("GDP Advance Estimate", date(2026, 10, 30))
    mc._fetch_fred("key", [ev], date(2026, 7, 15))
    assert ev["released_at"] == "2026-07-01"  # populated regardless of past/future
    assert ev["drift_days"] is None            # only set when event is past/today
    assert ev["released"] is False


def test_fetch_fred_mutates_events_list_in_place(monkeypatch):
    monkeypatch.setattr(mc, "_fred_obs", lambda *a, **k: [4.0, 3.0])
    monkeypatch.setattr(mc, "_fred_series_last_updated", lambda *a, **k: None)
    ev = _mk_event("GDP Advance Estimate", date(2026, 5, 1))
    events = [ev]
    mc._fetch_fred("key", events, date(2026, 7, 15))
    assert events[0] is ev
    assert ev["actual"] is not None


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
    }
    assert set(row.keys()) == expected_keys
    assert row["released"] is False
    assert row["released_at"] is None
    assert row["drift_days"] is None
    assert row["source"] == "static"


# ─── detect_macro_regime ──────────────────────────────────────────────────────

def _fred_obs_dispatch(fred_map):
    def _f(series_id, limit, api_key):
        return fred_map.get(series_id, [])
    return _f


def _regime_setup(monkeypatch, fred_map, yf_download=None):
    monkeypatch.setattr(mc, "_fred_obs", _fred_obs_dispatch(fred_map))
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
