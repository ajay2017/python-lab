"""stock_analyzer.sold_today.tickers_sold_on -- the one shared "sold today"
rule read by the Daily Brief, the Watchlist page and the cron's Ready-to-
Enter email (2026-10-11 audit follow-up to B-H1). Three-state contract:
None = couldn't check, set() = checked and nothing sold, else the tickers.
"""
from datetime import date

import pandas as pd

from stock_analyzer.sold_today import tickers_sold_on

DAY = date(2026, 9, 10)


def _trades(rows):
    return pd.DataFrame(rows, columns=["ticker", "action", "traded_at"])


def test_none_trade_log_is_unknown_not_empty():
    assert tickers_sold_on(None, DAY) is None


def test_empty_trade_log_is_checked_and_clean():
    assert tickers_sold_on(pd.DataFrame(columns=["ticker", "action", "traded_at"]), DAY) == set()


def test_missing_required_column_is_unknown():
    df = pd.DataFrame([{"ticker": "AAPL", "action": "SELL"}])
    assert tickers_sold_on(df, DAY) is None


def test_only_sells_on_that_et_date_count():
    df = _trades([
        ("aapl", "sell", "2026-09-10T14:30:00+00:00"),   # 10:30 ET today, lower-case
        ("NVDA", "BUY",  "2026-09-10T15:00:00+00:00"),   # a buy, not a sell
        ("MSFT", "SELL", "2026-09-09T15:00:00+00:00"),   # yesterday
    ])
    assert tickers_sold_on(df, DAY) == {"AAPL"}


def test_date_is_taken_in_new_york_not_utc():
    """02:00 UTC on the 11th is 22:00 ET on the 10th (the containers run
    UTC; CLAUDE.md: date comparisons use America/New_York)."""
    df = _trades([
        ("LATE", "SELL", "2026-09-11T02:00:00+00:00"),
        ("NEXT", "SELL", "2026-09-11T14:00:00+00:00"),
    ])
    assert tickers_sold_on(df, DAY) == {"LATE"}


def test_unparseable_timestamps_and_blank_tickers_are_skipped_not_fatal():
    df = _trades([
        ("BAD", "SELL", "not-a-date"),
        (None,  "SELL", "2026-09-10T14:30:00+00:00"),
        ("  ",  "SELL", "2026-09-10T14:30:00+00:00"),
        ("OK",  "SELL", "2026-09-10T14:30:00+00:00"),
    ])
    assert tickers_sold_on(df, DAY) == {"OK"}


def test_et_trade_dates_helper_shared_with_budget():
    from stock_analyzer.sold_today import _et_trade_dates
    df = _trades([("A", "SELL", "2026-09-10T03:59:00+00:00")])  # 23:59 ET the 9th
    _, dates = _et_trade_dates(df)
    assert list(dates) == [date(2026, 9, 9)]
