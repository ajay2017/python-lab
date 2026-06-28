"""
yfinance provider — the default primary source.

This is the existing yfinance fetch logic lifted verbatim out of `data.py` and
placed behind the `DataProvider` interface. Behaviour is intentionally
IDENTICAL to the pre-provider code (same retry/backoff, same api_health
recording under the "yahoo_finance" source, same canonical return shapes), so
introducing the seam is a no-op until the orchestrator wires in other
providers. See `base.py` for the canonical schemas.
"""

import time
import concurrent.futures
from datetime import datetime
import yfinance as yf
import pandas as pd
import pytz

from stock_analyzer import api_health as _ah
from stock_analyzer.constants import DATA_YF_REQUEST_TIMEOUT_SEC
from stock_analyzer.providers.base import (
    DataProvider, ProviderUnavailable,
    CAP_LIVE_PRICE, CAP_HISTORY, CAP_BUNDLE, CAP_INDICES, CAP_RISK_FREE,
)

_ET = pytz.timezone("America/New_York")

_INDICES = [
    ("^DJI",  "DOW",     "Dow Jones"),
    ("^GSPC", "S&P 500", "S&P 500"),
    ("^IXIC", "NASDAQ",  "Nasdaq Comp"),
]


def _call_with_timeout(fn, args, kwargs, timeout: float):
    """Run fn in a worker thread, bounded by a wall-clock `timeout` (seconds).

    yfinance exposes no request-level timeout, so a TCP-level hang would block
    until the OS socket timeout (minutes) or — in the headless cron — the 15-min
    job kill. Bounding each call lets the orchestrator fail over to Finnhub/FMP
    instead. On breach we ABANDON the worker (shutdown(wait=False)) rather than
    block on it — a hung socket would make wait=True re-block here; the orphaned
    thread dies with the process / OS socket timeout. Exceptions raised inside fn
    (e.g. a 429) propagate unchanged via .result(), so the retry logic below still
    sees them.
    """
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn, *args, **kwargs).result(timeout=timeout)
    finally:
        ex.shutdown(wait=False)


def _retry(fn, *args, retries: int = 3, backoff: float = 1.0, **kwargs):
    """Retry fn on Yahoo Finance 429 / rate-limit errors with linear backoff.

    Backoff is intentionally small (default 1.0s, sleep = backoff * attempt+1
    so total wait <= ~3s across all retries). Each attempt is wrapped in a
    wall-clock timeout (DATA_YF_REQUEST_TIMEOUT_SEC) since yfinance has no
    request-level timeout knob and could otherwise hang on a TCP-level failure.
    A timeout is NOT retried (it already waited the full budget — retrying just
    multiplies the stall); it is recorded and raised so the orchestrator fails
    over. Other non-rate-limit exceptions also raise immediately; only 429-style
    errors sleep and retry.
    """
    for attempt in range(retries):
        try:
            return _call_with_timeout(fn, args, kwargs, DATA_YF_REQUEST_TIMEOUT_SEC)
        except concurrent.futures.TimeoutError:
            _ah.record("yahoo_finance", "error", msg=f"timeout >{DATA_YF_REQUEST_TIMEOUT_SEC}s")
            raise
        except Exception as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("429", "too many", "rate limit", "rate-limit")):
                _ah.record("yahoo_finance", "rate_limit")
                if attempt < retries - 1:
                    time.sleep(backoff * (attempt + 1))
                    continue
            _ah.record("yahoo_finance", "error", msg=str(exc)[:120])
            raise


class YFinanceProvider(DataProvider):
    name = "yahoo_finance"
    capabilities = frozenset({
        CAP_LIVE_PRICE, CAP_HISTORY, CAP_BUNDLE, CAP_INDICES, CAP_RISK_FREE,
    })

    # yfinance needs no key — always configured.
    def is_configured(self) -> bool:
        return True

    # ── History ──────────────────────────────────────────────────────────────
    def price_history(self, ticker: str, period: str = "6mo") -> pd.DataFrame:
        def _fetch():
            df = yf.Ticker(ticker).history(period=period)
            df.index = pd.to_datetime(df.index)
            # Strip NaN-Close bars at the boundary so every consumer of this frame
            # sees the same invariant as the FMP path (fmp_provider.price_history)
            # and the indicator layer — a NaN Close is truthy and has slipped past
            # max()/.iloc[-1]/$-format guards on the live path before.
            if "Close" in df.columns:
                df = df[df["Close"].notna()]
            return df
        return _retry(_fetch)

    # ── Bundle (history + info + news + earnings + revisions) ─────────────────
    def bundle(self, ticker: str, period: str = "6mo") -> dict:
        """Single yf.Ticker session — fetches history, info, news and earnings in one go."""
        def _fetch():
            t = yf.Ticker(ticker)
            hist = t.history(period=period)
            hist.index = pd.to_datetime(hist.index)
            if "Close" in hist.columns:        # same NaN-Close boundary strip as price_history
                hist = hist[hist["Close"].notna()]
            info = {}
            try:
                info = t.info or {}
                # yfinance's .info is lazy-loaded and occasionally returns a sparse
                # dict on the first call (missing industry / marketCap / longBusinessSummary).
                # A single retry against a fresh Ticker handle resolves this for most
                # tickers without forcing a slow retry on every call.
                if info and not info.get("longBusinessSummary") and not info.get("industry"):
                    try:
                        info_retry = yf.Ticker(ticker).info or {}
                        if info_retry.get("longBusinessSummary") or info_retry.get("industry"):
                            info = info_retry
                    except Exception:
                        pass
            except Exception:
                pass
            news = []
            try:
                news = t.news or []
            except Exception:
                pass
            earnings = None
            try:
                cal = t.calendar
                if isinstance(cal, dict):
                    dates = cal.get("Earnings Date") or cal.get("earningsDate")
                    if dates:
                        earnings = str(dates[0])[:10]
            except Exception:
                pass

            revisions = {}
            try:
                upg = t.upgrades_downgrades
                if upg is not None and not upg.empty:
                    upg = upg.copy()
                    try:
                        upg.index = pd.to_datetime(upg.index, utc=True)
                        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=90)
                        recent = upg[upg.index >= cutoff]
                    except Exception:
                        recent = upg.head(20)
                    actions = recent["Action"].str.lower() if not recent.empty else pd.Series([], dtype=str)
                    ups   = int(actions.isin(["up", "init"]).sum())
                    downs = int((actions == "down").sum())
                    maint = int(actions.isin(["main", "reit"]).sum())
                    revisions = {
                        "upgrades_90d":   ups,
                        "downgrades_90d": downs,
                        "maintained_90d": maint,
                        "net":            ups - downs,
                        "latest": [
                            {
                                "firm":       str(row.get("Firm", "")),
                                "to_grade":   str(row.get("ToGrade", "")),
                                "from_grade": str(row.get("FromGrade", "")),
                                "action":     str(row.get("Action", "")),
                            }
                            for _, row in upg.head(5).iterrows()
                        ],
                    }
            except Exception:
                pass

            return {
                "history": hist, "info": info, "news": news,
                "earnings": earnings, "revisions": revisions,
            }

        result = _retry(_fetch)
        _ah.record("yahoo_finance", "success")
        return result

    def info(self, ticker: str) -> dict:
        """Lightweight .info fetch (no history) — backs data.fetch_financials."""
        return _retry(lambda: yf.Ticker(ticker).info or {})

    def next_earnings(self, ticker: str) -> str | None:
        """Light next-earnings-date fetch (yf.Ticker.calendar, no history/info) —
        'YYYY-MM-DD' or None. Lets Catalyst Watch cover universe names cheaply
        when the FMP market-wide calendar isn't available, without the full bundle."""
        def _fetch():
            cal = yf.Ticker(ticker).calendar
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date") or cal.get("earningsDate")
                if dates:
                    return str(dates[0])[:10]
            return None
        try:
            return _retry(_fetch)
        except Exception:
            return None

    # ── Market indices ────────────────────────────────────────────────────────
    def market_indices(self) -> list[dict]:
        results = []
        try:
            tickers = [t for t, _, _ in _INDICES]
            raw = _retry(
                yf.download, tickers,
                period="2d", auto_adjust=True, progress=False, threads=True,
            )
            close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
            for ticker, short, full in _INDICES:
                try:
                    if ticker not in close.columns:
                        # Don't silently fall back to the first column — that would
                        # re-label one index's prices as another (e.g. NASDAQ shown
                        # as DOW). Skip; the batch-level success/empty is recorded
                        # after the loop. No per-index "empty" record — it polluted
                        # the circuit-breaker counters (M7).
                        continue
                    col = close[ticker].dropna()
                    if len(col) < 1:
                        continue
                    price  = float(col.iloc[-1])
                    prev   = float(col.iloc[-2]) if len(col) >= 2 else price
                    change = price - prev
                    change_pct = change / prev * 100 if prev else 0.0
                    results.append({
                        "short":      short,
                        "full":       full,
                        "price":      price,
                        "change":     change,
                        "change_pct": round(change_pct, 2),
                        "fetched_at": datetime.now(_ET).strftime("%H:%M ET"),
                    })
                except Exception:
                    continue
        except Exception as _e:
            _ah.record("yahoo_finance", "error", msg=str(_e)[:80])
        if results:
            _ah.record("yahoo_finance", "success")
        else:
            _ah.record("yahoo_finance", "empty")
        return results

    # ── Live prices (batch) ─────────────────────────────────────────────────
    def live_prices(self, tickers: list[str]) -> dict[str, dict]:
        results = {}
        if not tickers:
            return results
        try:
            raw = _retry(
                yf.download, tickers,
                period="2d", auto_adjust=True, progress=False, threads=True,
            )
            close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
            for t in tickers:
                try:
                    if t not in close.columns:
                        # Same trap as market_indices: a missing ticker would
                        # silently inherit the first column's prices, writing e.g.
                        # NVDA prices under INTC. Skip the ticker. Do NOT record a
                        # per-ticker "empty" health event — a few missing names in an
                        # otherwise-good batch is a coverage gap, not a provider fault;
                        # per-ticker records polluted the circuit-breaker counters and
                        # could keep a healthy source "red" (M7). One batch-level
                        # success/empty is recorded after the loop.
                        continue
                    col = close[t].dropna()
                    if len(col) < 1:
                        continue
                    price = float(col.iloc[-1])
                    # prev_close: None when only one bar is available (can't know the
                    # prior close) rather than falling back to the live price — a
                    # fabricated prev==price disarms the cross-check's strict
                    # settled-close leg and reports a false 0.0% day-change (M2).
                    prev  = float(col.iloc[-2]) if len(col) >= 2 else None
                    results[t] = {
                        "price":      round(price, 2),
                        "prev_close": round(prev, 2) if prev is not None else None,
                        "change_pct": round((price - prev) / prev * 100, 2) if prev else None,
                        "fetched_at": datetime.now(_ET).strftime("%H:%M:%S ET"),
                        "source":     "yahoo_finance",
                    }
                except Exception:
                    continue
        except Exception as _e:
            _ah.record("yahoo_finance", "error", msg=str(_e)[:80])
        if results:
            _ah.record("yahoo_finance", "success")
        else:
            _ah.record("yahoo_finance", "empty")
        return results

    # ── Risk-free rate (13-week T-bill ^IRX) ──────────────────────────────────
    def risk_free_rate(self) -> float:
        """Annualised risk-free rate from ^IRX. Raises ProviderUnavailable on
        any error so the orchestrator can fall back to the caller's default."""
        try:
            hist = _retry(lambda: yf.Ticker("^IRX").history(period="5d"))
            if hist is not None and not hist.empty:
                rate = round(float(hist["Close"].iloc[-1]) / 100, 4)
                # Sanity-bound: a bad/zero ^IRX close would feed a 0.0 / negative
                # risk-free rate into Sharpe / valuation math. Accept only a
                # plausible value (0 < r < 25%); otherwise fall through so the
                # orchestrator uses the caller's default rather than scoring on a
                # garbage rate.
                if 0 < rate < 0.25:
                    return rate
        except Exception as exc:
            raise ProviderUnavailable(str(exc)) from exc
        raise ProviderUnavailable("^IRX returned no / implausible data")
