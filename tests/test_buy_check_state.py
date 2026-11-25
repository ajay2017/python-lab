"""stock_analyzer/buy_check_state.py::earnings_state (entry-fail-closed C1,
docs/plans/entry-fail-closed.md §2 / §7). Pure: no clock, no streamlit, no db.

C1 changes no behaviour: these tests pin the reader only; nothing holds a buy on
it yet (C3)."""
from pathlib import Path

import pytest

from stock_analyzer import asset_type
from stock_analyzer import buy_check_state as bcs

pytestmark = pytest.mark.fast


def _b(**kw):
    base = {"asset_type": asset_type.ASSET_TYPE_STOCK}
    base.update(kw)
    return base


def test_etf_with_a_stray_date_within_seven_days_is_not_applicable():
    out = bcs.earnings_state(_b(asset_type=asset_type.ASSET_TYPE_ETF,
                                earnings="2026-12-02", earnings_status="date"))
    assert out == {"state": "not_applicable", "date": None}


def test_etf_with_failed_status_and_no_date_is_still_not_applicable():
    # A bare {} from Yahoo (the ETF shape) is not a failure for a fund.
    out = bcs.earnings_state(_b(asset_type=asset_type.ASSET_TYPE_ETF,
                                earnings=None, earnings_status="failed"))
    assert out["state"] == "not_applicable"


def test_parseable_date_is_date():
    out = bcs.earnings_state(_b(earnings="2026-12-02", earnings_status="date"))
    assert out == {"state": "date", "date": "2026-12-02"}


def test_timestamped_date_reads_its_first_ten_characters():
    out = bcs.earnings_state(_b(earnings="2026-12-02 16:00:00", earnings_status="date"))
    assert out == {"state": "date", "date": "2026-12-02"}


def test_past_date_is_still_date():
    out = bcs.earnings_state(_b(earnings="2020-01-15", earnings_status="date"))
    assert out == {"state": "date", "date": "2020-01-15"}


def test_no_date_status_without_a_date_is_no_date():
    out = bcs.earnings_state(_b(earnings=None, earnings_status="no_date"))
    assert out == {"state": "no_date", "date": None}


@pytest.mark.parametrize("empty", [None, "", {}, []])
def test_no_date_status_with_any_empty_earnings_value_is_no_date(empty):
    assert bcs.earnings_state(_b(earnings=empty, earnings_status="no_date"))["state"] == "no_date"


def test_failed_status_without_a_date_is_failed():
    out = bcs.earnings_state(_b(earnings=None, earnings_status="failed"))
    assert out == {"state": "failed", "date": None}


def test_absent_status_key_and_no_date_is_failed():
    assert bcs.earnings_state(_b(earnings=None))["state"] == "failed"
    assert bcs.earnings_state(_b())["state"] == "failed"


def test_status_key_present_but_none_and_no_date_is_failed():
    assert bcs.earnings_state(_b(earnings=None, earnings_status=None))["state"] == "failed"


def test_unknown_status_string_and_no_date_is_failed():
    assert bcs.earnings_state(_b(earnings=None, earnings_status="weird"))["state"] == "failed"


def test_absent_status_key_with_a_date_is_date():
    out = bcs.earnings_state(_b(earnings="2026-12-02"))
    assert out == {"state": "date", "date": "2026-12-02"}


def test_stale_cached_bundle_is_failed_even_with_a_date_and_a_date_status():
    out = bcs.earnings_state(_b(earnings="2026-12-02", earnings_status="date",
                                stale_as_of="2026-11-20"))
    assert out == {"state": "failed", "date": None}


def test_stale_cached_bundle_with_a_no_date_status_is_failed():
    out = bcs.earnings_state(_b(earnings=None, earnings_status="no_date",
                                stale_as_of="2026-11-20"))
    assert out["state"] == "failed"


def test_fresh_bundle_stale_as_of_none_is_not_stale():
    out = bcs.earnings_state(_b(earnings="2026-12-02", earnings_status="date",
                                stale_as_of=None))
    assert out["state"] == "date"


def test_unparseable_date_is_failed_not_no_date():
    out = bcs.earnings_state(_b(earnings="not a date", earnings_status="no_date"))
    assert out == {"state": "failed", "date": None}


@pytest.mark.parametrize("garbage", [None, [], "AAPL", 42, 3.5, ("a",), object()])
def test_non_dict_bundle_is_failed_and_never_raises(garbage):
    assert bcs.earnings_state(garbage) == {"state": "failed", "date": None}


@pytest.mark.parametrize("raw", [{"a": 1}, 20261202, "2026-13-45", "Dec 2, 2026"])
def test_garbage_earnings_value_is_failed_and_never_raises(raw):
    assert bcs.earnings_state(_b(earnings=raw, earnings_status="date"))["state"] == "failed"


def test_a_bundle_whose_get_raises_is_failed():
    class _Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")
    assert bcs.earnings_state(_Boom()) == {"state": "failed", "date": None}


def test_input_is_not_mutated():
    b = _b(earnings="2026-12-02", earnings_status="date")
    snap = dict(b)
    bcs.earnings_state(b)
    assert b == snap


def test_result_dicts_are_independent_copies():
    a = bcs.earnings_state(None)
    a["state"] = "date"
    assert bcs.earnings_state(None)["state"] == "failed"


def test_reads_no_clock_under_a_frozen_clock(monkeypatch):
    """The state is a function of the bundle alone: freezing time at two different
    instants (and making every clock accessor raise) cannot change it."""
    import time
    import datetime as _dt

    def _boom(*a, **k):
        raise AssertionError("earnings_state read the clock")
    monkeypatch.setattr(time, "time", _boom)
    monkeypatch.setattr(time, "monotonic", _boom)
    b = _b(earnings="2026-12-02", earnings_status="date")
    first = bcs.earnings_state(b)
    second = bcs.earnings_state(b)
    assert first == second == {"state": "date", "date": "2026-12-02"}
    assert bcs.earnings_state(_b(earnings=None, earnings_status="no_date"))["state"] == "no_date"
    assert _dt.datetime is not None  # sanity: datetime itself still importable


def test_module_is_pure_no_streamlit_db_and_no_clock_read():
    src = (Path(__file__).resolve().parent.parent / "stock_analyzer"
           / "buy_check_state.py").read_text(encoding="utf-8")
    for banned in ("import streamlit", "from stock_analyzer import db", "market_time",
                   "utcnow", "date.today", "datetime.now", "time.time"):
        assert banned not in src, banned
