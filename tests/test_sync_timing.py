"""Tests for stock_analyzer/sync_timing.py (RecTime C1,
`docs/plans/fill-time-typed.md` S3): was a journal trade stored AFTER a broker
sync, so the sync can't contain it?

`after_sync` is three-valued and the whole point is that None ("can't tell") is
never collapsed into True or False. The #494 replay is the motivating case:
a SELL typed at 09:56, a BUY filled 10:21 but recorded 14:30:50 (fill time left
blank), a broker sync completed 11:19:14 ET on 2026-11-13. Pure frame math.
"""
import pandas as pd
import pytest

from stock_analyzer import sync_timing as stm
from stock_analyzer.sync_timing import after_sync, classify_vs_sync
from stock_analyzer.trade_time import normalize_traded_at

pytestmark = pytest.mark.fast

SYNC = "2026-11-13T11:19:14-05:00"          # the 2026-11-13 broker sync, ET


# ─── after_sync: "fill" ─────────────────────────────────────────────────────

def test_fill_strictly_after_is_true():
    assert after_sync("2026-11-13T11:19:15-05:00", "fill", SYNC) is True


def test_fill_tie_is_not_after():
    """A tie is "already reflected", the existing convention."""
    assert after_sync(SYNC, "fill", SYNC) is False


def test_fill_before_is_false():
    assert after_sync("2026-11-13T09:56:00-05:00", "fill", SYNC) is False


def test_fill_one_microsecond_after_is_true():
    assert after_sync("2026-11-13T11:19:14.000001-05:00", "fill", SYNC) is True


def test_fill_compares_the_instant_not_the_offset():
    """Same instant written in UTC and in ET is a tie, not an ordering."""
    assert after_sync("2026-11-13T16:19:14+00:00", "fill", SYNC) is False


# ─── after_sync: "recorded" ─────────────────────────────────────────────────

def test_recorded_at_or_before_sync_is_false():
    assert after_sync("2026-11-13T11:19:14-05:00", "recorded", SYNC) is False
    assert after_sync("2026-11-13T10:30:00-05:00", "recorded", SYNC) is False


def test_recorded_after_sync_same_et_date_is_unknown():
    """#494: recorded 14:30:50 after an 11:19:14 sync; the fill may be either side."""
    assert after_sync("2026-11-13T14:30:50.554958-05:00", "recorded", SYNC) is None


def test_recorded_on_a_later_et_date_is_after():
    assert after_sync("2026-11-16T10:05:00-05:00", "recorded", SYNC) is True


def test_recorded_late_evening_et_that_is_next_day_utc_stays_same_et_date():
    """23:30 ET on the sync's day is the NEXT UTC date; the ET date decides."""
    assert after_sync("2026-11-14T04:30:00+00:00", "recorded", SYNC) is None


# ─── after_sync: "placeholder" ──────────────────────────────────────────────

def test_placeholder_same_et_date_is_unknown():
    assert after_sync("2026-11-13T16:00:00-05:00", "placeholder", SYNC) is None


def test_placeholder_earlier_date_is_false():
    assert after_sync("2026-11-12T16:00:00-05:00", "placeholder", SYNC) is False


def test_placeholder_later_date_is_true():
    assert after_sync("2026-11-16T16:00:00-05:00", "placeholder", SYNC) is True


def test_placeholder_earlier_date_even_when_its_clock_is_after_the_sync_clock():
    """Only the DATE of a placeholder is real; 16:00 on the 12th is not 'after'
    an 11:19 sync on the 13th just because 16:00 > 11:19."""
    assert after_sync("2026-11-12T16:00:00-05:00", "placeholder",
                      "2026-11-13T09:31:00-05:00") is False


# ─── after_sync: kind handling and unreadable inputs ────────────────────────

@pytest.mark.parametrize("kind", [None, float("nan"), pd.NA])
def test_missing_kind_reads_as_fill(kind):
    assert after_sync("2026-11-13T14:00:00-05:00", kind, SYNC) is True
    assert after_sync("2026-11-13T10:00:00-05:00", kind, SYNC) is False


def test_unrecognised_kind_is_unknown_not_a_guess():
    assert after_sync("2026-11-13T14:00:00-05:00", "garbage", SYNC) is None


@pytest.mark.parametrize("bad", [None, "", "not-a-time", float("nan"), pd.NaT])
def test_unreadable_trade_time_is_unknown(bad):
    assert after_sync(bad, "fill", SYNC) is None
    assert after_sync(bad, "recorded", SYNC) is None
    assert after_sync(bad, "placeholder", SYNC) is None


@pytest.mark.parametrize("bad", [None, "", "not-a-time", pd.NaT])
def test_unreadable_sync_time_is_unknown(bad):
    assert after_sync("2026-11-13T14:00:00-05:00", "fill", bad) is None


def test_accepts_timestamp_and_datetime_inputs():
    import datetime as dt
    ts = pd.Timestamp("2026-11-13 14:00:00", tz="America/New_York")
    assert after_sync(ts, "fill", SYNC) is True
    assert after_sync(dt.datetime(2026, 11, 13, 14, 0,
                                  tzinfo=dt.timezone.utc), "fill", SYNC) is False


def test_naive_values_are_read_as_utc():
    """Same reading as trade_time / the timestamptz column."""
    assert after_sync("2026-11-13T16:19:14", "fill", SYNC) is False   # tie
    assert after_sync("2026-11-13T16:19:15", "fill", SYNC) is True


# ─── time-context matrix ────────────────────────────────────────────────────

def test_weekend_visit_after_a_friday_blank_time_row():
    """Friday blank-time row (recorded 14:30) and a sync that ran Saturday: the
    sync is later, so the row is certainly before it."""
    assert after_sync("2026-11-13T14:30:50.5-05:00", "recorded",
                      "2026-11-14T02:00:00-05:00") is False
    # ... and a sync still on Friday 11:19 leaves it unknown, not 'after'
    assert after_sync("2026-11-13T14:30:50.5-05:00", "recorded", SYNC) is None


def test_friday_placeholder_vs_monday_sync_is_before():
    assert after_sync("2026-11-13T16:00:00-05:00", "placeholder",
                      "2026-11-16T09:40:00-05:00") is False


def test_weekend_dated_placeholder_vs_friday_sync_is_after():
    assert after_sync("2026-11-14T16:00:00-05:00", "placeholder", SYNC) is True


def test_nyse_holiday_thanksgiving_dated_rows():
    """2026-11-26 is a market holiday; the comparison is by ET date only, so
    a holiday-dated placeholder is after a Wednesday sync and a Wednesday one
    is unknown."""
    wed = "2026-11-25T11:19:14-05:00"
    assert after_sync("2026-11-26T16:00:00-05:00", "placeholder", wed) is True
    assert after_sync("2026-11-25T16:00:00-05:00", "placeholder", wed) is None
    assert after_sync("2026-11-27T10:00:00-05:00", "recorded", wed) is True


def test_dst_spring_forward_2026_03_08():
    """2026-03-08 02:00 ET does not exist; 16:00 EDT is 20:00Z (not 21:00Z)."""
    sync = "2026-03-08T11:19:14-04:00"
    assert after_sync("2026-03-08T16:00:00-04:00", "placeholder", sync) is None
    assert after_sync("2026-03-07T16:00:00-05:00", "placeholder", sync) is False
    assert after_sync("2026-03-08T14:30:00-04:00", "recorded", sync) is None
    # 21:00Z on 03-08 is 17:00 EDT, a real fill after the 11:19 sync
    assert after_sync("2026-03-08T21:00:00+00:00", "fill", sync) is True


def test_dst_fall_back_2026_11_01_repeated_hour():
    """01:30 ET happens twice on 2026-11-01 (EDT then EST). Instants, not wall
    clocks, decide a 'fill'; the ET date stays 11-01 for both."""
    sync = "2026-11-01T01:45:00-04:00"                 # first 01:45, EDT = 05:45Z
    assert after_sync("2026-11-01T01:30:00-04:00", "fill", sync) is False
    assert after_sync("2026-11-01T01:30:00-05:00", "fill", sync) is True   # 06:30Z
    assert after_sync("2026-11-01T01:30:00-05:00", "recorded", sync) is None


def test_dst_fall_back_late_evening_is_next_utc_day_but_same_et_day():
    sync = "2026-11-01T09:00:00-05:00"
    # 23:30 EST on 11-01 is 04:30Z on 11-02
    assert after_sync("2026-11-02T04:30:00+00:00", "placeholder", sync) is None
    assert after_sync("2026-11-02T04:30:00+00:00", "recorded", sync) is None


def test_after_hours_fill_vs_afternoon_sync():
    assert after_sync("2026-11-13T17:45:00-05:00", "fill", SYNC) is True
    assert after_sync("2026-11-13T17:45:00-05:00", "recorded", SYNC) is None


def test_trade_dated_a_day_early_or_late():
    """A hand log back-dated a day, or forward-dated a day, against the sync."""
    assert after_sync("2026-11-12T16:00:00-05:00", "placeholder", SYNC) is False
    assert after_sync("2026-11-14T16:00:00-05:00", "placeholder", SYNC) is True
    assert after_sync("2026-11-12T10:00:00-05:00", "fill", SYNC) is False
    assert after_sync("2026-11-14T10:00:00-05:00", "fill", SYNC) is True


# ─── classify_vs_sync ───────────────────────────────────────────────────────

def _rows_494():
    """The 2026-11-13 journal tail, as db.load_trades_or_none would normalise it."""
    return pd.DataFrame([
        {"id": 493, "ticker": "MRVL", "action": "SELL", "shares": 3.0,
         "price": 160.0, "traded_at": "2026-11-13T09:56:00-05:00",
         "notes": None, "broker_txn_id": None, "fill_time_typed": True},
        {"id": 494, "ticker": "MRVL", "action": "BUY", "shares": 5.0,
         "price": 150.0, "traded_at": "2026-11-13T14:30:50.554958-05:00",
         "notes": None, "broker_txn_id": None, "fill_time_typed": False},
    ])


def test_494_replay_sell_before_buy_unknown():
    df = normalize_traded_at(_rows_494())
    out = classify_vs_sync(df, SYNC)
    assert out["before"] == [0]
    assert out["unknown"] == [1]
    assert out["after"] == []
    assert out["unknown_by_ticker"] == {
        "MRVL": {"net_shares": 5.0, "cash_delta": -750.0, "rows": 1}}
    assert out["unknown_cash_delta"] == pytest.approx(-750.0)
    assert out["after_by_ticker"] == {} and out["after_cash_delta"] == 0.0


def test_494_replay_the_same_buy_typed_is_a_certain_after():
    rows = _rows_494()
    rows.loc[1, "fill_time_typed"] = True
    rows.loc[1, "traded_at"] = "2026-11-13T14:30:00-05:00"
    out = classify_vs_sync(normalize_traded_at(rows), SYNC)
    assert out["after"] == [1] and out["unknown"] == []
    assert out["after_by_ticker"]["MRVL"]["net_shares"] == 5.0
    assert out["after_cash_delta"] == pytest.approx(-750.0)


def test_same_day_16_00_prefill_row_is_unknown_not_after():
    """A Pending-Imports row logged same day sits at the 16:00 anchor, after an
    11:19 sync by the clock. By date it is unknown."""
    rows = pd.DataFrame([
        {"id": 1, "ticker": "CSCO", "action": "BUY", "shares": 10.0,
         "price": 60.0, "traded_at": "2026-11-13T16:00:00-05:00",
         "notes": "broker import", "broker_txn_id": "abc", "fill_time_typed": False},
    ])
    out = classify_vs_sync(normalize_traded_at(rows), SYNC)
    assert out["unknown"] == [0] and out["after"] == []
    assert out["unknown_by_ticker"]["CSCO"]["net_shares"] == 10.0


def test_missing_kind_column_reads_every_row_as_fill():
    df = pd.DataFrame([
        {"ticker": "AAA", "action": "BUY", "shares": 1.0, "price": 10.0,
         "traded_at": "2026-11-13T14:00:00-05:00"},
        {"ticker": "BBB", "action": "SELL", "shares": 2.0, "price": 20.0,
         "traded_at": "2026-11-13T10:00:00-05:00"},
    ])
    assert "traded_at_time_kind" not in df.columns
    out = classify_vs_sync(df, SYNC)
    assert out["after"] == [0] and out["before"] == [1] and out["unknown"] == []


def test_returns_none_for_an_unreadable_frame_or_sync():
    good = normalize_traded_at(_rows_494())
    assert classify_vs_sync(None, SYNC) is None
    assert classify_vs_sync("not a frame", SYNC) is None
    assert classify_vs_sync(good, None) is None
    assert classify_vs_sync(good, "garbage") is None
    assert classify_vs_sync(pd.DataFrame([{"ticker": "A"}]), SYNC) is None


def test_empty_frame_is_a_real_empty_answer_not_none():
    out = classify_vs_sync(pd.DataFrame(columns=["ticker", "traded_at"]), SYNC)
    assert out is not None
    assert out["after"] == out["before"] == out["unknown"] == out["unreadable"] == []
    assert out["after_by_ticker"] == {} and out["unknown_by_ticker"] == {}
    assert out["after_cash_delta"] == 0.0 and out["unknown_cash_delta"] == 0.0


def test_unparseable_row_time_lands_in_its_own_unreadable_bucket():
    """No date, so never 'unknown' (it would sit there forever) and never in a
    ticker / cash summary: an unreadable row must not excuse a drift gap."""
    df = pd.DataFrame([
        {"ticker": "AAA", "action": "BUY", "shares": 1.0, "price": 10.0,
         "traded_at": "garbage", "traded_at_time_kind": "fill"},
        {"ticker": "AAA", "action": "BUY", "shares": 2.0, "price": 10.0,
         "traded_at": None, "traded_at_time_kind": "placeholder"},
        {"ticker": "BBB", "action": "BUY", "shares": 3.0, "price": 10.0,
         "traded_at": "2026-11-13T14:00:00-05:00", "traded_at_time_kind": "fill"},
    ])
    out = classify_vs_sync(df, SYNC)
    assert out["unreadable"] == [0, 1]
    assert out["unknown"] == [] and out["before"] == [] and out["after"] == [2]
    assert out["unknown_by_ticker"] == {} and out["unknown_cash_delta"] == 0.0
    assert list(out["after_by_ticker"]) == ["BBB"]


def test_per_ticker_nets_cash_and_split_handling():
    df = pd.DataFrame([
        {"ticker": "AAA", "action": "BUY", "shares": 5.0, "price": 10.0,
         "traded_at": "2026-11-13T14:00:00-05:00", "traded_at_time_kind": "fill"},
        {"ticker": "AAA", "action": "SELL", "shares": 2.0, "price": 12.0,
         "traded_at": "2026-11-13T15:00:00-05:00", "traded_at_time_kind": "fill"},
        {"ticker": "zzz", "action": "SPLIT", "shares": 40.0, "price": 1.0,
         "traded_at": "2026-11-13T16:00:00-05:00", "traded_at_time_kind": "fill"},
    ])
    out = classify_vs_sync(df, SYNC)
    assert out["after"] == [0, 1, 2]
    assert out["after_by_ticker"]["AAA"] == {
        "net_shares": 3.0, "cash_delta": pytest.approx(-26.0), "rows": 2}
    # a SPLIT carries the adjusted TOTAL: never summed as a delta, never cash
    assert out["after_by_ticker"]["ZZZ"] == {
        "net_shares": 0.0, "cash_delta": 0.0, "rows": 1}
    assert out["after_cash_delta"] == pytest.approx(-26.0)


def test_rows_are_reported_by_frame_index_label():
    df = pd.DataFrame(
        [{"ticker": "AAA", "action": "BUY", "shares": 1.0, "price": 1.0,
          "traded_at": "2026-11-13T14:00:00-05:00", "traded_at_time_kind": "fill"}],
        index=[77])
    assert classify_vs_sync(df, SYNC)["after"] == [77]


def test_misdated_recorded_row_on_a_later_day_is_after_buy_and_sell():
    """A 'recorded' row (blank time, microseconds) stamped on a LATER ET day
    than the sync can only be a mis-dated row (the real blank-time shape is the
    next test). S3 still says: certainly after. The same row before the sync is
    not."""
    df = pd.DataFrame([
        {"ticker": "AAA", "action": "BUY", "shares": 4.0, "price": 10.0,
         "traded_at": "2026-11-16T10:05:00.123456-05:00", "fill_time_typed": False},
        {"ticker": "BBB", "action": "SELL", "shares": 4.0, "price": 10.0,
         "traded_at": "2026-11-13T10:05:00.123456-05:00", "fill_time_typed": False},
    ])
    out = classify_vs_sync(normalize_traded_at(df.assign(notes=None)), SYNC)
    assert out["after"] == [0] and out["before"] == [1]
    assert out["after_by_ticker"]["AAA"]["net_shares"] == 4.0
    assert out["after_cash_delta"] == pytest.approx(-40.0)


@pytest.mark.parametrize("action,signed", [("BUY", 4.0), ("SELL", -4.0)])
def test_c2a_shape_past_date_blank_time_is_a_16_00_placeholder(action, signed):
    """The real C2a shape: a PAST trade date with the fill time left blank is
    stored at the 16:00 ET anchor, `fill_time_typed` False -> "placeholder".
    Against a sync on that same ET day it is unknown; against a later-day sync it
    is before."""
    from stock_analyzer.market_time import et_anchor_iso
    df = pd.DataFrame([
        {"ticker": "AAA", "action": action, "shares": 4.0, "price": 10.0,
         "traded_at": et_anchor_iso("2026-11-13"), "notes": None,
         "fill_time_typed": False},
    ])
    df = normalize_traded_at(df)
    assert df["traded_at_time_kind"].iloc[0] == "placeholder"

    same_day = classify_vs_sync(df, SYNC)
    assert same_day["unknown"] == [0] and same_day["after"] == []
    assert same_day["unknown_by_ticker"]["AAA"]["net_shares"] == signed
    assert same_day["unknown_cash_delta"] == pytest.approx(-signed * 10.0)

    later = classify_vs_sync(df, "2026-11-16T11:19:14-05:00")
    assert later["before"] == [0] and later["unknown"] == [] and later["after"] == []


def test_module_has_no_numeric_thresholds():
    """No decision threshold is hardcoded here: every comparison is an exact
    instant or an ET date. Guard against a 'within N minutes' window creeping in."""
    import inspect
    import re
    src = inspect.getsource(stm)
    assert not re.search(r"timedelta|minutes\s*=|Timedelta", src)
