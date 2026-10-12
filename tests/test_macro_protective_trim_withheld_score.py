"""2026-10-11 audit B-M6: the macro pre-event PROTECTIVE_TRIM picked its
"weakest" holding via sort_values("Score"). A withheld score is NaN; with
EVERY affected holding withheld it picked an arbitrary row and `_f(NaN)`
rendered a measured-looking "score 0". Owner decision 2026-10-12: still
trim (protection must not go quiet mid-outage), on the LARGEST affected
position, and say conviction is unmeasured.
"""
import datetime
import math

import pandas as pd

import stock_analyzer.daily_briefing as db_mod
from stock_analyzer.constants import (
    MACRO_AFFECTED_TRIM_THRESHOLD_PCT, MACRO_BROAD_EXPOSURE_PCT,
)
from stock_analyzer.macro_calendar import HIGH as MC_HIGH

TODAY = datetime.date(2026, 10, 12)


def _port(rows):
    df = pd.DataFrame(rows)
    df["Shares"] = 10.0
    df["Price"] = 100.0
    return df


def _trim(monkeypatch, rows):
    monkeypatch.setattr("stock_analyzer.macro_calendar.affected_sectors", lambda _cat: ["Tech"])
    ev = {"date": (TODAY + datetime.timedelta(days=1)).isoformat(),
          "impact": MC_HIGH, "category": "Inflation", "event": "CPI"}
    items = db_mod._review_list(_port(rows), [], [ev], {}, TODAY,
                                portfolio_value=10_000.0, act_today=[])
    trims = [i for i in items if (i.get("action") or {}).get("type") == "PROTECTIVE_TRIM"]
    assert len(trims) == 1, [i.get("action") for i in items]
    return trims[0]


def test_fixture_exposure_sits_in_the_sized_trim_band():
    """Every fixture below puts Tech at 40% of the book: over the trim
    threshold, under the broad-print cutoff. If a constant moves out of that
    band, this names the cause instead of the trim tests failing opaquely."""
    assert MACRO_AFFECTED_TRIM_THRESHOLD_PCT < 40.0 < MACRO_BROAD_EXPOSURE_PCT


def test_all_withheld_trims_largest_and_says_unmeasured(monkeypatch):
    item = _trim(monkeypatch, [
        {"Ticker": "AAA", "Sector": "Tech", "Weight (%)": 15.0, "Score": math.nan, "Score Available": False},
        {"Ticker": "BBB", "Sector": "Tech", "Weight (%)": 25.0, "Score": math.nan, "Score Available": False},
        {"Ticker": "CCC", "Sector": "Energy", "Weight (%)": 60.0, "Score": 70.0, "Score Available": True},
    ])
    a = item["action"]
    assert a["trim_ticker"] == "BBB"          # largest affected, not row order
    assert a["weakest_score"] is None         # never a fabricated 0.0
    assert a["conviction_unmeasured"] is True
    assert "unmeasured" in item["why"]
    # "Protection still fires" means a SIZED trim, not just a named ticker.
    assert a["trim_shares"] > 0 and a["trim_dollars"] > 0


def test_largest_is_chosen_regardless_of_row_order(monkeypatch):
    item = _trim(monkeypatch, [
        {"Ticker": "BBB", "Sector": "Tech", "Weight (%)": 25.0, "Score": math.nan, "Score Available": False},
        {"Ticker": "AAA", "Sector": "Tech", "Weight (%)": 15.0, "Score": math.nan, "Score Available": False},
        {"Ticker": "CCC", "Sector": "Energy", "Weight (%)": 60.0, "Score": 70.0, "Score Available": True},
    ])
    assert item["action"]["trim_ticker"] == "BBB"


def test_mixed_sector_trims_weakest_measured_name(monkeypatch):
    """A withheld name must never be picked as 'weakest' over a measured one,
    even when the withheld row is the larger position."""
    item = _trim(monkeypatch, [
        {"Ticker": "AAA", "Sector": "Tech", "Weight (%)": 25.0, "Score": math.nan, "Score Available": False},
        {"Ticker": "BBB", "Sector": "Tech", "Weight (%)": 10.0, "Score": 72.0, "Score Available": True},
        {"Ticker": "DDD", "Sector": "Tech", "Weight (%)": 5.0, "Score": 55.0, "Score Available": True},
        {"Ticker": "CCC", "Sector": "Energy", "Weight (%)": 60.0, "Score": 70.0, "Score Available": True},
    ])
    a = item["action"]
    assert a["trim_ticker"] == "DDD"
    assert a["weakest_score"] == 55.0
    assert a["conviction_unmeasured"] is False
    assert "unmeasured" not in item["why"]


def test_score_available_false_with_a_numeric_score_is_still_unmeasured(monkeypatch):
    """The flag, not merely NaN-ness, is the contract (portfolio.py:2111
    filters the sibling sector path the same way)."""
    item = _trim(monkeypatch, [
        {"Ticker": "AAA", "Sector": "Tech", "Weight (%)": 25.0, "Score": 10.0, "Score Available": False},
        {"Ticker": "BBB", "Sector": "Tech", "Weight (%)": 15.0, "Score": 60.0, "Score Available": True},
        {"Ticker": "CCC", "Sector": "Energy", "Weight (%)": 60.0, "Score": 70.0, "Score Available": True},
    ])
    assert item["action"]["trim_ticker"] == "BBB"


def test_missing_score_available_column_falls_back_to_nan_check(monkeypatch):
    """Without the flag column, NaN-ness alone decides (fail-open default,
    same as daily_briefing's weak-large block and portfolio.py:2111)."""
    item = _trim(monkeypatch, [
        {"Ticker": "AAA", "Sector": "Tech", "Weight (%)": 25.0, "Score": math.nan},
        {"Ticker": "BBB", "Sector": "Tech", "Weight (%)": 15.0, "Score": 60.0},
        {"Ticker": "CCC", "Sector": "Energy", "Weight (%)": 60.0, "Score": 70.0},
    ])
    assert item["action"]["trim_ticker"] == "BBB"
    assert item["action"]["conviction_unmeasured"] is False


def test_weight_tie_breaks_on_ticker_deterministically(monkeypatch):
    item = _trim(monkeypatch, [
        {"Ticker": "ZZZ", "Sector": "Tech", "Weight (%)": 20.0, "Score": math.nan, "Score Available": False},
        {"Ticker": "AAA", "Sector": "Tech", "Weight (%)": 20.0, "Score": math.nan, "Score Available": False},
        {"Ticker": "CCC", "Sector": "Energy", "Weight (%)": 60.0, "Score": 70.0, "Score Available": True},
    ])
    assert item["action"]["trim_ticker"] == "AAA"
