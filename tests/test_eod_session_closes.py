"""stock_analyzer.eod_session_closes (SlidingStop C3): the closes the EOD carry writer
may use. Boundary: the ALERT_EOD_HOUR_ET cutoff, in ET, naive and tz-aware."""
from datetime import datetime

import pytest
import pytz

from stock_analyzer import constants as C
from stock_analyzer import eod_session_closes as esc

_ET = pytz.timezone("America/New_York")
CLOSES = {"AAA": 12.5}


def _at(hour, minute=0):
    return _ET.localize(datetime(2026, 11, 4, hour, minute))


def test_before_the_cutoff_hour_the_map_is_empty_and_not_after_close():
    h = int(C.ALERT_EOD_HOUR_ET)
    assert esc.session_closes_for_write(CLOSES, _at(h - 1, 59)) == ({}, False)
    assert esc.session_closes_for_write(CLOSES, _at(0)) == ({}, False)


def test_at_and_after_the_cutoff_hour_the_closes_pass_through():
    h = int(C.ALERT_EOD_HOUR_ET)
    assert esc.session_closes_for_write(CLOSES, _at(h, 0)) == (CLOSES, True)
    assert esc.session_closes_for_write(CLOSES, _at(h, 30)) == (CLOSES, True)
    assert esc.session_closes_for_write(CLOSES, _at(23, 59)) == (CLOSES, True)


def test_the_cutoff_reads_the_et_hour_not_the_utc_hour():
    h = int(C.ALERT_EOD_HOUR_ET)
    late_et = _at(h, 5)                       # 21:05 UTC in winter: ET hour is what counts
    assert late_et.astimezone(pytz.utc).hour != late_et.hour
    assert esc.session_closes_for_write(CLOSES, late_et)[1] is True
    early_et = _at(h - 1, 5)                  # its UTC hour is already >= the cutoff
    assert early_et.astimezone(pytz.utc).hour >= h
    assert esc.session_closes_for_write(CLOSES, early_et) == ({}, False)


def test_the_cutoff_follows_the_constant(monkeypatch):
    monkeypatch.setattr(esc, "ALERT_EOD_HOUR_ET", 10)
    assert esc.session_closes_for_write(CLOSES, _at(10)) == (CLOSES, True)
    assert esc.session_closes_for_write(CLOSES, _at(9, 59)) == ({}, False)


@pytest.mark.parametrize("closes", [None, [], "AAA", 12.5])
def test_a_non_dict_close_map_after_the_close_reads_as_no_closes(closes):
    assert esc.session_closes_for_write(closes, _at(17)) == ({}, True)


@pytest.mark.parametrize("bad", [None, "16:30", 16, object()])
def test_an_unusable_clock_counts_as_before_the_close_and_never_raises(bad):
    assert esc.session_closes_for_write(CLOSES, bad) == ({}, False)
