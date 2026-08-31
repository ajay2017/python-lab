"""Tests for stock_analyzer.providers.yfinance_provider.

Focused on the 2026-08-31 false-alarm fix: live_prices() must null out
prev_close when its 2-day batch window hasn't rolled forward to include
today's session yet, but only on an actual trading day — a weekend/holiday
read where the last bar legitimately IS the prior session must be untouched.
"""
from datetime import date, datetime

import pandas as pd

from stock_analyzer.providers import yfinance_provider as yfp


def _raw(dates: list[str], closes: dict[str, list[float]]) -> pd.DataFrame:
    """Build a yf.download-shaped DataFrame: MultiIndex columns (Close, ticker)."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    data = {("Close", t): vals for t, vals in closes.items()}
    df = pd.DataFrame(data, index=idx)
    df.columns = pd.MultiIndex.from_tuples(list(data.keys()))
    return df


def _freeze_now(monkeypatch, fixed: datetime):
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.replace(tzinfo=tz) if tz else fixed
    monkeypatch.setattr(yfp, "datetime", _FixedDatetime)


class TestIsTradingDay:
    def test_weekday_non_holiday_is_trading_day(self):
        assert yfp._is_trading_day(date(2026, 8, 31)) is True  # Monday

    def test_saturday_is_not_trading_day(self):
        assert yfp._is_trading_day(date(2026, 8, 29)) is False

    def test_sunday_is_not_trading_day(self):
        assert yfp._is_trading_day(date(2026, 8, 30)) is False

    def test_nyse_holiday_is_not_trading_day(self):
        assert yfp._is_trading_day(date(2026, 1, 19)) is False  # MLK Day, a Monday


class TestLivePricesPrevCloseStaleness:
    def test_prev_close_kept_when_last_bar_is_today(self, monkeypatch):
        # 2026-08-31 (Mon) is a trading day; the batch's last bar IS today.
        _freeze_now(monkeypatch, datetime(2026, 8, 31, 10, 0, 0))
        df = _raw(["2026-08-28", "2026-08-31"], {"NVDA": [217.55, 220.00]})
        monkeypatch.setattr(yfp.yf, "download", lambda *a, **k: df)

        out = yfp.YFinanceProvider().live_prices(["NVDA"])

        assert out["NVDA"]["price"] == 220.00
        assert out["NVDA"]["prev_close"] == 217.55

    def test_prev_close_nulled_when_last_bar_stale_on_trading_day(self, monkeypatch):
        # Reproduces the 2026-08-31 incident: today is a trading day (Monday),
        # but yfinance's window still ends at Friday — one session behind.
        _freeze_now(monkeypatch, datetime(2026, 8, 31, 8, 0, 0))
        df = _raw(["2026-08-27", "2026-08-28"], {"NVDA": [227.98, 217.55]})
        monkeypatch.setattr(yfp.yf, "download", lambda *a, **k: df)

        out = yfp.YFinanceProvider().live_prices(["NVDA"])

        assert out["NVDA"]["price"] == 217.55       # last available bar, kept
        assert out["NVDA"]["prev_close"] is None     # stale-by-a-session, nulled
        assert out["NVDA"]["change_pct"] is None

    def test_prev_close_kept_on_non_trading_day_even_if_stale(self, monkeypatch):
        # Sunday: no "today" bar is ever expected, so the same [Thu, Fri]
        # window is legitimate — matches the real 0.0%-agreement read logged
        # on 2026-08-30.
        _freeze_now(monkeypatch, datetime(2026, 8, 30, 10, 0, 0))
        df = _raw(["2026-08-27", "2026-08-28"], {"NVDA": [227.98, 217.55]})
        monkeypatch.setattr(yfp.yf, "download", lambda *a, **k: df)

        out = yfp.YFinanceProvider().live_prices(["NVDA"])

        assert out["NVDA"]["price"] == 217.55
        assert out["NVDA"]["prev_close"] == 227.98

    def test_prev_close_still_none_with_only_one_bar(self, monkeypatch):
        _freeze_now(monkeypatch, datetime(2026, 8, 31, 10, 0, 0))
        df = _raw(["2026-08-31"], {"NVDA": [220.00]})
        monkeypatch.setattr(yfp.yf, "download", lambda *a, **k: df)

        out = yfp.YFinanceProvider().live_prices(["NVDA"])

        assert out["NVDA"]["price"] == 220.00
        assert out["NVDA"]["prev_close"] is None

    def test_multi_ticker_batch_each_evaluated_independently(self, monkeypatch):
        _freeze_now(monkeypatch, datetime(2026, 8, 31, 8, 0, 0))
        df = _raw(
            ["2026-08-27", "2026-08-28"],
            {"NVDA": [227.98, 217.55], "DELL": [472.26, 456.24]},
        )
        monkeypatch.setattr(yfp.yf, "download", lambda *a, **k: df)

        out = yfp.YFinanceProvider().live_prices(["NVDA", "DELL"])

        assert out["NVDA"]["prev_close"] is None
        assert out["DELL"]["prev_close"] is None
        assert out["DELL"]["price"] == 456.24
