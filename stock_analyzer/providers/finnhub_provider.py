"""
Finnhub provider — the real-time price cross-check source.

Role in the chain (memory project_second_data_source): Finnhub's free tier
offers the only real-time US quote among the keyed candidates (60 calls/min),
which makes it the best source to cross-check yfinance's price field. Stock
candles (history) and full fundamentals are premium-gated on the free tier, so
this adapter advertises ONLY CAP_LIVE_PRICE — it is a price source, not a
bundle source. FMP covers history/fundamentals failover.

Quote endpoint: GET /quote?symbol=AAPL&token=KEY
  -> {"c": current, "d": change, "dp": pct, "h","l","o", "pc": prev_close, "t"}
  Unknown symbols come back with c == 0, which we skip.
"""

from datetime import datetime
import pytz

from stock_analyzer import api_health as _ah
from stock_analyzer.providers.base import (
    DataProvider, ProviderUnavailable, CAP_LIVE_PRICE,
)
from stock_analyzer.providers._util import get_secret, http_get_json, is_rate_limit

_ET = pytz.timezone("America/New_York")
_BASE = "https://finnhub.io/api/v1"


class FinnhubProvider(DataProvider):
    name = "finnhub"
    capabilities = frozenset({CAP_LIVE_PRICE})

    def __init__(self):
        self._key = get_secret("FINNHUB_API_KEY")

    def is_configured(self) -> bool:
        return bool(self._key)

    def live_prices(self, tickers: list[str]) -> dict[str, dict]:
        """Per-symbol /quote loop (Finnhub quote is single-symbol). For the
        cross-check we only ever pass the handful of displayed/held tickers, so
        the 60/min budget is ample. Returns the canonical live-price shape;
        partial results are fine (orchestrator decides). Raises
        ProviderUnavailable only when unconfigured."""
        if not self._key:
            raise ProviderUnavailable("FINNHUB_API_KEY not set")
        if not tickers:
            return {}

        results: dict[str, dict] = {}
        had_error = False
        for t in tickers:
            try:
                q = http_get_json(f"{_BASE}/quote",
                                  params={"symbol": t, "token": self._key})
                price = q.get("c")
                if not price or float(price) <= 0:
                    # c == 0 → unknown/unsupported symbol on Finnhub; skip it.
                    continue
                price = float(price)
                # prev_close: leave None when the source omits it (Finnhub pc=0/null)
                # rather than falling back to the live price — a fabricated prev==price
                # disarms the cross-check's strict settled-close leg (it would compare
                # live-vs-live and pass) and reports a false 0.0% day-change for a real
                # mover. Consumers treat None as "prev unknown" (M2).
                prev = q.get("pc")
                prev = float(prev) if prev else None
                dp = q.get("dp")
                results[t] = {
                    "price":      round(price, 2),
                    "prev_close": round(prev, 2) if prev is not None else None,
                    "change_pct": round(float(dp), 2) if dp is not None else (
                        round((price - prev) / prev * 100, 2) if prev else None),
                    "fetched_at": datetime.now(_ET).strftime("%H:%M:%S ET"),
                    "source":     "finnhub",
                }
            except Exception as exc:
                had_error = True
                if is_rate_limit(exc):
                    _ah.record("finnhub", "rate_limit")
                else:
                    _ah.record("finnhub", "error", msg=str(exc)[:120])
                continue

        if results:
            _ah.record("finnhub", "success")
        elif not had_error:
            _ah.record("finnhub", "empty")
        return results

    def fetch_news_sentiment(self, ticker: str) -> dict | None:
        """Call /stock/news-sentiment for one ticker. Returns a normalised dict or None.

        NOT part of the DataProvider capability chain — called directly by
        news_sentiment.py. Fails silently (returns None) on any error so the
        rest of the app is unaffected when sentiment data is unavailable.
        """
        if not self._key:
            return None
        try:
            data = http_get_json(
                f"{_BASE}/stock/news-sentiment",
                params={"symbol": ticker, "token": self._key},
            )
            sentiment = data.get("sentiment")
            if sentiment is None:
                return None
            bullish = sentiment.get("bullishPercent")
            if bullish is None:
                return None
            buzz = data.get("buzz") or {}
            sector_bullish = data.get("sectorAverageBullishPercent")
            vs_sector = (
                (float(bullish) - float(sector_bullish)) * 100
                if sector_bullish is not None
                else None
            )
            return {
                "bullish_pct":   float(bullish),
                "bearish_pct":   float(sentiment.get("bearishPercent") or 0),
                "buzz_score":    float(buzz.get("buzzScore") or 0),
                "company_score": float(data.get("companyNewsScore") or 0),
                "sector_score":  float(data.get("sectorAverageNewsScore") or 0),
                "vs_sector_pp":  float(vs_sector) if vs_sector is not None else None,
                "symbol":        str(ticker),
            }
        except Exception as exc:
            if is_rate_limit(exc):
                _ah.record("finnhub", "rate_limit")
            else:
                _ah.record("finnhub", "error", msg=str(exc)[:120])
            return None
