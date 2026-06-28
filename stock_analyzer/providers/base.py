"""
Market-data provider abstraction.

The app was historically 100% dependent on yfinance for every market-data
call. This package introduces a thin provider seam so `data.py` can become a
failover orchestrator (try primary; fall to the next source on failure) and
cross-check the price field across sources — without anything ABOVE `data.py`
changing. See memory `project_second_data_source` for the converged design
(chain: yfinance → Finnhub → FMP, configurable; price-only cross-check).

A provider implements only the capabilities it can actually serve (declared in
`capabilities`); the orchestrator builds a per-data-type chain from whichever
configured providers advertise the matching capability. A provider signals "I
can't serve this right now" by raising `ProviderUnavailable` (no key, API
error, empty payload) — the orchestrator then tries the next provider.

Canonical return shapes (every provider must conform so consumers above
`data.py` are source-agnostic):

    live_prices(tickers) -> {ticker: {"price": float, "prev_close": float|None,
                                      "change_pct": float|None, "fetched_at": str}}
        (prev_close / change_pct are None when the source omits the prior close —
         providers must NOT fabricate prev==price; consumers handle None. See M2.)
    price_history(ticker, period) -> pandas.DataFrame indexed by datetime with
                                     at least a "Close" column (OHLCV when avail)
    bundle(ticker, period) -> {"history": DataFrame, "info": dict, "news": list,
                               "earnings": str|None, "revisions": dict}
    market_indices() -> [{"short","full","price","change","change_pct","fetched_at"}, ...]
    risk_free_rate() -> float   (annualised decimal, e.g. 0.045)
"""

import pandas as pd

# ── Capability flags — what a provider can serve ─────────────────────────────
CAP_LIVE_PRICE = "live_price"   # batch current price + prev close
CAP_HISTORY    = "history"      # OHLCV history for one ticker
CAP_BUNDLE     = "bundle"       # history + info + news + earnings + revisions
CAP_INDICES    = "indices"      # DOW / S&P / NASDAQ levels
CAP_RISK_FREE  = "risk_free"    # 13-week T-bill / risk-free rate


class ProviderUnavailable(Exception):
    """A provider could not serve a request (missing key, API error, or empty
    payload). The orchestrator treats this as 'try the next provider', NOT as a
    fatal error — distinct from a programming bug, which should still raise."""


class DataProvider:
    """Base class for a market-data source. Subclasses override only the methods
    matching the capabilities they declare; unimplemented methods raise
    NotImplementedError (a programming error — the orchestrator never calls a
    method whose capability the provider didn't advertise)."""

    #: stable identifier, also used as the api_health source key
    name: str = "base"
    #: frozenset of CAP_* this provider can serve
    capabilities: frozenset = frozenset()

    def is_configured(self) -> bool:
        """True when the provider has everything it needs to make calls (e.g. an
        API key present in st.secrets). Key-less providers (yfinance) return
        True unconditionally. The orchestrator skips unconfigured providers so a
        missing optional key is silent, not an error."""
        return True

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    # ── Capability methods (override as declared) ────────────────────────────
    def live_prices(self, tickers: list[str]) -> dict[str, dict]:
        raise NotImplementedError

    def price_history(self, ticker: str, period: str = "6mo") -> pd.DataFrame:
        raise NotImplementedError

    def bundle(self, ticker: str, period: str = "6mo") -> dict:
        raise NotImplementedError

    def market_indices(self) -> list[dict]:
        raise NotImplementedError

    def risk_free_rate(self) -> float:
        raise NotImplementedError
