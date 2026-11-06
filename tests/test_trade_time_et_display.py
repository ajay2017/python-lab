"""TJUtc (TJDate Phase 1, C3): `trade_time.et_display` / `et_trade_date` /
`is_placeholder_time`, and the app.py wiring that uses them.

The Trade History table and several date labels used the UTC value, so a
19:14 ET fill read as the next day (00:14). The helpers are pure; app.py has no
importing tests, so its wiring is pinned at the source.
"""
import datetime
import re
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import trade_time as tt

pytestmark = pytest.mark.fast

_APP = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
_DASH = "—"


# --- et_display -------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("2026-11-05T00:14:00+00:00", "2026-11-04 19:14"),       # the CSCO row
    ("2026-11-04T21:00:00+00:00", f"2026-11-04 {_DASH}"),    # 16:00 EST anchor
    ("2026-10-30T20:00:00+00:00", f"2026-10-30 {_DASH}"),    # 16:00 EDT anchor
    ("2026-10-30T21:00:00+00:00", "2026-10-30 17:00"),       # real 17:00 EDT fill
    ("2026-11-04T20:00:00+00:00", "2026-11-04 15:00"),       # 20:00 UTC in EST is 15:00
])
def test_et_display_known_values(value, expected):
    assert tt.et_display(value) == expected


def test_et_display_time_known_false_is_always_a_placeholder():
    assert tt.et_display("2026-10-30T21:00:00+00:00", False) == f"2026-10-30 {_DASH}"
    assert tt.et_display("2026-11-05T00:14:00+00:00", False) == f"2026-11-04 {_DASH}"


@pytest.mark.parametrize("k", [None, float("nan"), True])
def test_et_display_time_known_not_false_is_not_a_placeholder_by_that_rule(k):
    assert tt.et_display("2026-10-30T21:00:00+00:00", k) == "2026-10-30 17:00"
    assert tt.is_placeholder_time("2026-10-30T21:00:00+00:00", k) is False


def test_et_display_numpy_false_counts_as_false():
    import numpy as np
    assert tt.is_placeholder_time("2026-10-30T21:00:00+00:00", np.False_) is True


@pytest.mark.parametrize("value", [pd.NaT, None, "garbage", "", float("nan")])
def test_et_display_blank_for_missing_or_unparseable(value):
    assert tt.et_display(value) == ""
    assert tt.et_display(value, False) == ""
    assert tt.is_placeholder_time(value) is False


def test_et_display_accepts_timestamp_and_datetime():
    assert tt.et_display(pd.Timestamp("2026-11-05T00:14:00Z")) == "2026-11-04 19:14"
    assert tt.et_display(datetime.datetime(2026, 11, 5, 0, 14)) == "2026-11-04 19:14"


def test_placeholder_is_tested_on_et_wall_clock_not_a_fixed_utc_hour():
    # 20:00 UTC is the anchor in EDT but a real 15:00 fill in EST (and 21:00
    # UTC the reverse), so any fixed-UTC-hour test is wrong half the year.
    assert tt.is_placeholder_time("2026-07-15T20:00:00+00:00") is True
    assert tt.is_placeholder_time("2026-07-15T21:00:00+00:00") is False
    assert tt.is_placeholder_time("2026-01-15T21:00:00+00:00") is True
    assert tt.is_placeholder_time("2026-01-15T20:00:00+00:00") is False


def test_placeholder_needs_exactly_16_00_00():
    assert tt.is_placeholder_time("2026-11-04T21:00:01+00:00") is False
    assert tt.is_placeholder_time("2026-11-04T21:00:00.500000+00:00") is False
    assert tt.is_placeholder_time("2026-11-04T21:01:00+00:00") is False


def test_anchor_written_by_the_importer_is_a_placeholder():
    from stock_analyzer.market_time import et_anchor_iso
    for day in ("2026-03-07", "2026-03-09", "2026-07-15", "2026-11-04"):
        assert tt.is_placeholder_time(et_anchor_iso(day)) is True
        assert tt.et_display(et_anchor_iso(day)) == f"{day} {_DASH}"


# --- et_trade_date ----------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("2026-11-05T00:14:00+00:00", datetime.date(2026, 11, 4)),
    ("2026-03-08T06:30:00+00:00", datetime.date(2026, 3, 8)),    # DST start day
    ("2026-03-08T07:30:00+00:00", datetime.date(2026, 3, 8)),
    ("2026-11-01T05:30:00+00:00", datetime.date(2026, 11, 1)),   # DST end day
    ("2026-11-05T03:00:00", datetime.date(2026, 11, 4)),         # naive = UTC
    ("2026-11-05T12:00:00-05:00", datetime.date(2026, 11, 5)),   # ET offset kept
])
def test_et_trade_date(value, expected):
    assert tt.et_trade_date(value) == expected


def test_et_trade_date_matches_normalize_traded_at_treating_naive_as_utc():
    df = pd.DataFrame([{"ticker": "A", "traded_at": "2026-11-05T03:00:00",
                        "notes": None, "broker_txn_id": None}])
    out = tt.normalize_traded_at(df)
    assert tt.et_trade_date(out["traded_at"].iloc[0]) == datetime.date(2026, 11, 4)


@pytest.mark.parametrize("value", [None, pd.NaT, "garbage", ""])
def test_et_trade_date_none_for_missing(value):
    assert tt.et_trade_date(value) is None


# --- app.py wiring (source pins) --------------------------------------------

def _between(start: str, end: str) -> str:
    i = _APP.index(start)
    return _APP[i:_APP.index(end, i)]


def test_trade_history_block_no_longer_formats_utc():
    block = _between('display_df = trades_df.copy()', 'display_df.insert(0, "Delete?"')
    assert "trade_time.et_display(" in block
    assert "strftime" not in block
    assert not re.search(r"to_datetime\([^)]*utc=True", block, re.S)


def test_trade_history_column_label_is_et_and_index_is_kept():
    block = _between('display_df = trades_df.copy()', 'key="trade_history_editor"')
    assert re.search(r'"traded_at":\s*st\.column_config\.TextColumn\("Date / Time \(ET\)"', block)
    # DelIdx: no sort / reset / filter on the frame handed to the editor.
    assert "reset_index" not in block
    assert "sort_values" not in block
    assert "display_df[show_cols]" in block


def test_trade_history_shares_are_not_rounded_to_whole():
    block = _between('display_df = trades_df.copy()', 'key="trade_history_editor"')
    assert not re.search(r'"shares":\s*st\.column_config\.NumberColumn', block)
    assert 'format="%.0f"' not in block
    assert "fmt_shares" in block


@pytest.mark.parametrize("anchor", [
    'thesis, thesis_date = n, (_d.isoformat() if _d else "")',
    '"date": (_ld.isoformat() if _ld else "")',
    '"trade_date":                   (_tc_td.isoformat() if _tc_td else "")',
    '_sca_trade_date[_sca_bt] = _sca_td.isoformat() if _sca_td else ""',
])
def test_utc_date_slice_sites_use_et_trade_date(anchor):
    assert anchor in _APP
    assert _APP.count("trade_time.et_trade_date(") >= 4


def test_the_four_display_slices_are_gone():
    assert 'thesis_date = n, str(_row.get("traded_at", ""))[:10]' not in _APP
    assert '"date": str(_row.get("traded_at", ""))[:10]' not in _APP
    assert 'str(_tc_brow.get("traded_at", ""))[:10]' not in _APP
    assert 'str(_sca_brow.get("traded_at", ""))[:10]' not in _APP


def test_c3b_db_write_site_left_alone():
    # Deliberately out of scope (C3b: feeds thesis_reviews.trade_date).
    assert '_trade_date_by_ticker[_bt]  = str(_brow.get("traded_at", ""))[:10]' in _APP


def test_opening_window_pattern_skips_placeholder_anchors():
    block = _between('_bf_grade_rows = []', '_bf_p3 = _bf.opening_window_pattern(')
    assert "trade_time.is_placeholder_time(" in block
    assert 'traded_at_time_known").get' not in block
