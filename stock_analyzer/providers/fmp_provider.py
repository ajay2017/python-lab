"""
FMP (Financial Modeling Prep) provider — failover for prices and history.

Role in the chain (memory project_second_data_source): FMP's free tier (250
calls/day) has the broadest coverage of the keyed candidates — quotes,
historical prices, fundamentals, analyst targets, news. This adapter implements
the price + history capabilities now; the full `bundle()` (mapping FMP's
profile/ratios/estimates into the yfinance-shaped `info` dict + news + earnings
+ revisions) is the larger, live-validation-dependent piece and is built in a
follow-up step. Until then FMP advertises CAP_LIVE_PRICE + CAP_HISTORY only.

Endpoints — FMP's CURRENT "stable" API (the legacy /api/v3/ paths 403 on the
free plan after FMP's 2024 revamp):
  Quote:      GET /stable/quote?symbol=AAPL&apikey=KEY
              -> [{"symbol","price","previousClose","changePercentage",...}]
  Historical: GET /stable/historical-price-eod/full?symbol=AAPL&from=&to=&apikey=KEY
              -> [{"symbol","date","open","high","low","close","volume",...}]  (flat list)
Parsing is defensive about field names + shape (flat list vs legacy
{"historical":[...]}) so a re-revamp doesn't silently break it.
"""

from datetime import datetime, date, timedelta
import pytz
import pandas as pd

from stock_analyzer import api_health as _ah
from stock_analyzer.providers.base import (
    DataProvider, ProviderUnavailable, CAP_LIVE_PRICE, CAP_HISTORY,
)
from stock_analyzer.providers._util import get_secret, http_get_json, is_rate_limit

_ET = pytz.timezone("America/New_York")
_BASE = "https://financialmodelingprep.com/stable"


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


def _num(row: dict, *keys):
    """First non-None value among `keys`, coerced to float, else None."""
    for k in keys:
        v = row.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


class FMPProvider(DataProvider):
    name = "fmp"
    capabilities = frozenset({CAP_LIVE_PRICE, CAP_HISTORY})

    def __init__(self):
        self._key = get_secret("FMP_API_KEY")

    def is_configured(self) -> bool:
        return bool(self._key)

    def _safe(self, msg: str) -> str:
        """Redact the API key from any message before it's logged/surfaced —
        requests embeds the full URL (incl. ?apikey=...) in its error text."""
        s = str(msg)
        if self._key:
            s = s.replace(self._key, "***")
        return s[:120]

    # ── Live prices (per-symbol; stable quote is single-symbol) ───────────────
    def live_prices(self, tickers: list[str]) -> dict[str, dict]:
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        if not tickers:
            return {}

        results: dict[str, dict] = {}
        had_error = False
        for t in tickers:
            try:
                payload = http_get_json(f"{_BASE}/quote",
                                        params={"symbol": t, "apikey": self._key})
            except Exception as exc:
                had_error = True
                if is_rate_limit(exc):
                    _ah.record("fmp", "rate_limit")
                else:
                    _ah.record("fmp", "error", msg=self._safe(exc))
                continue

            err = _fmp_error(payload)
            if err:
                had_error = True
                _ah.record("fmp", "error", msg=self._safe(err))
                continue

            row = payload[0] if isinstance(payload, list) and payload else (
                payload if isinstance(payload, dict) else None)
            if not row:
                continue
            price = _num(row, "price")
            if not price or price <= 0:
                continue
            prev = _num(row, "previousClose", "previous_close") or price
            chg = _num(row, "changePercentage", "changesPercentage")
            results[t] = {
                "price":      round(price, 2),
                "prev_close": round(prev, 2),
                "change_pct": round(chg, 2) if chg is not None else (
                    round((price - prev) / prev * 100, 2) if prev else 0.0),
                "fetched_at": datetime.now(_ET).strftime("%H:%M:%S ET"),
                "source":     "fmp",
            }

        if results:
            _ah.record("fmp", "success")
        elif not had_error:
            _ah.record("fmp", "empty")
        return results

    # ── History ────────────────────────────────────────────────────────────────
    def price_history(self, ticker: str, period: str = "6mo") -> pd.DataFrame:
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        days = _period_to_days(period)
        params = {
            "symbol": ticker,
            "from":   (date.today() - timedelta(days=days)).isoformat(),
            "to":     date.today().isoformat(),
            "apikey": self._key,
        }
        try:
            payload = http_get_json(f"{_BASE}/historical-price-eod/full", params=params)
        except Exception as exc:
            if is_rate_limit(exc):
                _ah.record("fmp", "rate_limit")
            else:
                _ah.record("fmp", "error", msg=self._safe(exc))
            raise ProviderUnavailable(self._safe(exc)) from exc

        err = _fmp_error(payload)
        if err:
            _ah.record("fmp", "error", msg=self._safe(err))
            raise ProviderUnavailable(err)

        # Stable returns a flat list; legacy returned {"historical":[...]}. Accept both.
        rows = payload.get("historical") if isinstance(payload, dict) else payload
        if not rows:
            _ah.record("fmp", "empty")
            raise ProviderUnavailable(f"fmp no history for {ticker}")

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
            }).dropna(subset=["Close"])
        except Exception as exc:
            _ah.record("fmp", "error", msg=f"history parse {ticker}: {self._safe(exc)}")
            raise ProviderUnavailable(self._safe(exc)) from exc

        if out.empty:
            _ah.record("fmp", "empty")
            raise ProviderUnavailable(f"fmp empty history for {ticker}")
        _ah.record("fmp", "success")
        return out
