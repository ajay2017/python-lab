"""
FMP (Financial Modeling Prep) provider — failover for prices and history.

Role in the chain (memory project_second_data_source): FMP's free tier (250
calls/day) has the broadest coverage of the keyed candidates — quotes,
historical prices, fundamentals, analyst targets, news. This adapter implements
the price + history capabilities now; the full `bundle()` (mapping FMP's
profile/ratios/estimates into the yfinance-shaped `info` dict + news + earnings
+ revisions) is the larger, live-validation-dependent piece and is built in a
follow-up step. Until then FMP advertises CAP_LIVE_PRICE + CAP_HISTORY only.

Endpoints (v3):
  Quote:      GET /api/v3/quote/AAPL,MSFT?apikey=KEY
              -> [{"symbol","price","previousClose","changesPercentage",...}, ...]
  Historical: GET /api/v3/historical-price-full/AAPL?apikey=KEY&timeseries=N
              -> {"symbol","historical":[{"date","open","high","low","close",
                  "adjClose","volume"}, ...]}  (newest-first)
"""

from datetime import datetime
import pytz
import pandas as pd

from stock_analyzer import api_health as _ah
from stock_analyzer.providers.base import (
    DataProvider, ProviderUnavailable, CAP_LIVE_PRICE, CAP_HISTORY,
)
from stock_analyzer.providers._util import get_secret, http_get_json, is_rate_limit

_ET = pytz.timezone("America/New_York")
_BASE = "https://financialmodelingprep.com/api/v3"


def _period_to_days(period: str) -> int:
    """Map our period strings (yfinance-style) to a historical-day count."""
    p = (period or "").strip().lower()
    table = {
        "1d": 3, "2d": 5, "5d": 10, "1mo": 31, "3mo": 95,
        "6mo": 190, "1y": 370, "2y": 740, "5y": 1830,
    }
    return table.get(p, 190)


def _fmp_error(payload) -> str | None:
    """FMP signals problems as a 200 with {'Error Message': ...} — detect it."""
    if isinstance(payload, dict) and ("Error Message" in payload or "error" in payload):
        return str(payload.get("Error Message") or payload.get("error"))[:120]
    return None


class FMPProvider(DataProvider):
    name = "fmp"
    capabilities = frozenset({CAP_LIVE_PRICE, CAP_HISTORY})

    def __init__(self):
        self._key = get_secret("FMP_API_KEY")

    def is_configured(self) -> bool:
        return bool(self._key)

    # ── Live prices (batch) ───────────────────────────────────────────────────
    def live_prices(self, tickers: list[str]) -> dict[str, dict]:
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        if not tickers:
            return {}
        syms = ",".join(tickers)
        try:
            payload = http_get_json(f"{_BASE}/quote/{syms}", params={"apikey": self._key})
        except Exception as exc:
            if is_rate_limit(exc):
                _ah.record("fmp", "rate_limit")
            else:
                _ah.record("fmp", "error", msg=str(exc)[:120])
            raise ProviderUnavailable(str(exc)) from exc

        err = _fmp_error(payload)
        if err:
            _ah.record("fmp", "error", msg=err)
            raise ProviderUnavailable(err)

        results: dict[str, dict] = {}
        for row in (payload or []):
            try:
                sym = str(row.get("symbol", "")).upper()
                price = row.get("price")
                if not sym or not price or float(price) <= 0:
                    continue
                price = float(price)
                prev = row.get("previousClose")
                prev = float(prev) if prev else price
                chg_pct = row.get("changesPercentage")
                results[sym] = {
                    "price":      round(price, 2),
                    "prev_close": round(prev, 2),
                    "change_pct": round(float(chg_pct), 2) if chg_pct is not None else (
                        round((price - prev) / prev * 100, 2) if prev else 0.0),
                    "fetched_at": datetime.now(_ET).strftime("%H:%M:%S ET"),
                }
            except Exception:
                continue

        if results:
            _ah.record("fmp", "success")
        else:
            _ah.record("fmp", "empty")
        return results

    # ── History ────────────────────────────────────────────────────────────────
    def price_history(self, ticker: str, period: str = "6mo") -> pd.DataFrame:
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        days = _period_to_days(period)
        try:
            payload = http_get_json(
                f"{_BASE}/historical-price-full/{ticker}",
                params={"apikey": self._key, "timeseries": days},
            )
        except Exception as exc:
            if is_rate_limit(exc):
                _ah.record("fmp", "rate_limit")
            else:
                _ah.record("fmp", "error", msg=str(exc)[:120])
            raise ProviderUnavailable(str(exc)) from exc

        err = _fmp_error(payload)
        if err:
            _ah.record("fmp", "error", msg=err)
            raise ProviderUnavailable(err)

        rows = (payload or {}).get("historical") if isinstance(payload, dict) else None
        if not rows:
            _ah.record("fmp", "empty")
            raise ProviderUnavailable(f"fmp no history for {ticker}")

        # Build an OHLCV frame matching the yfinance shape: DatetimeIndex
        # ascending, columns Open/High/Low/Close/Volume.
        df = pd.DataFrame(rows)
        try:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").set_index("date")
            out = pd.DataFrame({
                "Open":   df.get("open"),
                "High":   df.get("high"),
                "Low":    df.get("low"),
                "Close":  df.get("close"),
                "Volume": df.get("volume"),
            })
            out = out.dropna(subset=["Close"])
        except Exception as exc:
            _ah.record("fmp", "error", msg=f"history parse {ticker}: {str(exc)[:80]}")
            raise ProviderUnavailable(str(exc)) from exc

        if out.empty:
            _ah.record("fmp", "empty")
            raise ProviderUnavailable(f"fmp empty history for {ticker}")
        _ah.record("fmp", "success")
        return out
