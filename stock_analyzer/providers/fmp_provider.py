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

import time
from datetime import datetime, date, timedelta
import pytz
import pandas as pd

from stock_analyzer import api_health as _ah
from stock_analyzer import constants as C
from stock_analyzer.providers.base import (
    DataProvider, ProviderUnavailable, CAP_LIVE_PRICE, CAP_HISTORY, CAP_BUNDLE,
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


def _first(payload):
    """First element of a list payload, or the dict itself, else {}."""
    if isinstance(payload, list):
        return payload[0] if payload else {}
    if isinstance(payload, dict):
        return payload
    return {}


def _pick(d: dict, *keys):
    """First present, non-None value among `keys` (tolerates FMP field renames)."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
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
    # CAP_BUNDLE enabled 2026-06-01 after live selftest validated the bundle:
    # history + comprehensive, ACCURATE fundamentals (sector/PE/margins/growth/
    # D-E/targets) + revisions all populate correctly for AAPL. Known soft gaps:
    # news may be empty (→ neutral sentiment, 15% weight) and earnings-date may
    # be None on the failover path — both degrade gracefully and only matter
    # while yfinance is actually down. Bundle failover chain is now yfinance→fmp.
    capabilities = frozenset({CAP_LIVE_PRICE, CAP_HISTORY, CAP_BUNDLE})

    def __init__(self):
        self._key = get_secret("FMP_API_KEY")
        # Process-local fundamentals cache {ticker: (fetched_at, info)} — one
        # info() fetch is ~5 calls against a 250/day free tier, so caching it
        # keeps repeated analyses of the same sparse-yfinance ticker from
        # re-spending the quota. TTL in constants; cleared on reboot.
        self._info_cache: dict[str, tuple[float, dict]] = {}

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

    # ── Bundle (failover for the full analysis when yfinance is down) ─────────
    def _get_json(self, path: str, params: dict | None = None):
        """GET /stable/<path> with apikey; raise ProviderUnavailable (key
        redacted) on transport error or FMP error payload."""
        p = dict(params or {})
        p["apikey"] = self._key
        try:
            payload = http_get_json(f"{_BASE}/{path}", params=p)
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
        return payload

    def bundle(self, ticker: str, period: str = "6mo") -> dict:
        """Compose the canonical bundle from FMP. History is REQUIRED (raises if
        unavailable); every other section is best-effort — if an endpoint fails,
        that section comes back empty rather than failing the whole bundle, so a
        partial failover bundle is still useful for scoring. Field names map onto
        the yfinance-shaped `info` dict that fundamentals/scoring read."""
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        hist = self.price_history(ticker, period)   # core — raises if unavailable
        return {
            "history":   hist,
            "info":      self._build_info(ticker),
            "news":      self._fetch_news(ticker),
            "earnings":  self._next_earnings(ticker),
            "revisions": self._fetch_revisions(ticker),
        }

    def info(self, ticker: str) -> dict:
        """yfinance-shaped `.info` (fundamentals only — no history/news), used by
        the orchestrator to backfill a sparse yfinance bundle's fundamentals.
        Cached per ticker (DATA_FMP_INFO_CACHE_TTL_SEC) to protect the free-tier
        quota. Only non-sparse results are cached, so a quota-exhausted empty
        fetch is retried next time rather than pinned for an hour."""
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        hit = self._info_cache.get(ticker)
        if hit and (time.time() - hit[0]) < float(C.DATA_FMP_INFO_CACHE_TTL_SEC):
            return hit[1]
        info = self._build_info(ticker)
        if info and any(info.get(k) is not None for k in
                        ("marketCap", "trailingPE", "profitMargins",
                         "revenueGrowth", "returnOnEquity")):
            self._info_cache[ticker] = (time.time(), info)
        return info

    def earnings(self, ticker: str) -> str | None:
        """Soonest future earnings date — light accessor (1 call) so the
        orchestrator can backfill earnings without re-fetching a full bundle."""
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        return self._next_earnings(ticker)

    def revisions(self, ticker: str) -> dict:
        """Analyst-revision consensus — light accessor (1 call), same rationale
        as earnings(): avoids paying for a whole bundle during backfill."""
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        return self._fetch_revisions(ticker)

    def earnings_calendar(self, from_date: str, to_date: str) -> list[dict]:
        """Upcoming earnings across the market for a date range, in ONE call.
        Returns [{ticker, date, when}] (when = 'bmo'/'amc'/'' if FMP provides it).
        Used by Catalyst Watch, which intersects this with the app's tracked
        universe — so one call covers held + watchlist + sector-universe names."""
        if not self._key:
            raise ProviderUnavailable("FMP_API_KEY not set")
        payload = self._get_json("earnings-calendar", {"from": from_date, "to": to_date})
        rows = payload if isinstance(payload, list) else []
        out: list[dict] = []
        for r in rows:
            sym = r.get("symbol")
            dt  = r.get("date")
            if not sym or not dt:
                continue
            out.append({
                "ticker": str(sym).upper(),
                "date":   str(dt)[:10],
                "when":   str(r.get("time") or r.get("when") or "").lower(),
            })
        return out

    def _build_info(self, ticker: str) -> dict:
        """Map FMP profile + ratios + growth + price-target into the subset of
        yfinance `.info` keys that fundamentals.fundamental_score and
        data.fetch_financials_from_info read. Each block is independent."""
        info: dict = {}
        try:                                            # company profile
            prof = _first(self._get_json("profile", {"symbol": ticker}))
            if prof:
                info["longName"]  = prof.get("companyName")
                info["shortName"] = prof.get("companyName")
                info["longBusinessSummary"] = prof.get("description")
                info["sector"]    = prof.get("sector")
                info["industry"]  = prof.get("industry")
                info["marketCap"] = _pick(prof, "marketCap", "mktCap")
                info["beta"]      = prof.get("beta")
                rng = prof.get("range") or ""
                if isinstance(rng, str) and "-" in rng:
                    try:
                        lo, hi = (x.strip() for x in rng.split("-")[:2])
                        info["fiftyTwoWeekLow"]  = float(lo)
                        info["fiftyTwoWeekHigh"] = float(hi)
                    except Exception:
                        pass
        except Exception:
            pass
        try:                                            # valuation / quality ratios (TTM)
            rat = _first(self._get_json("ratios-ttm", {"symbol": ticker}))
            if rat:
                info["trailingPE"]     = _pick(rat, "priceToEarningsRatioTTM", "peRatioTTM", "priceEarningsRatioTTM")
                info["profitMargins"]  = _pick(rat, "netProfitMarginTTM", "netProfitMargin")
                info["returnOnEquity"] = _pick(rat, "returnOnEquityTTM", "returnOnEquity")
                info["currentRatio"]   = _pick(rat, "currentRatioTTM", "currentRatio")
                de = _pick(rat, "debtToEquityRatioTTM", "debtEquityRatioTTM", "debtToEquity")
                if de is not None:
                    try:                                # yfinance reports D/E as a percent (1.5x → 150)
                        info["debtToEquity"] = float(de) * 100
                    except Exception:
                        pass
        except Exception:
            pass
        try:                                            # key metrics (TTM) — fills ROE / EPS / FCF
            km = _first(self._get_json("key-metrics-ttm", {"symbol": ticker}))
            if km:
                if info.get("returnOnEquity") is None:
                    info["returnOnEquity"] = _pick(km, "returnOnEquityTTM", "roeTTM")
                eps = _pick(km, "netIncomePerShareTTM", "epsTTM", "earningsPerShareTTM")
                if eps is not None:
                    info["trailingEps"] = eps
                # data.fetch_financials_from_info computes fcf_yield = freeCashflow /
                # marketCap; back it out from FMP's FCF yield so that derivation works.
                fcf_yield = _pick(km, "freeCashFlowYieldTTM")
                mc = info.get("marketCap")
                if fcf_yield is not None and mc:
                    try:
                        info["freeCashflow"] = float(fcf_yield) * float(mc)
                    except Exception:
                        pass
        except Exception:
            pass
        try:                                            # growth
            grow = _first(self._get_json("financial-growth", {"symbol": ticker, "limit": 1}))
            if grow:
                info["revenueGrowth"]  = _pick(grow, "revenueGrowth", "growthRevenue")
                info["earningsGrowth"] = _pick(grow, "epsgrowth", "growthEPS", "netIncomeGrowth")
        except Exception:
            pass
        try:                                            # analyst price targets
            tgt = _first(self._get_json("price-target-consensus", {"symbol": ticker}))
            if tgt:
                info["targetMeanPrice"]   = _pick(tgt, "targetConsensus", "targetMean")
                info["targetHighPrice"]   = _pick(tgt, "targetHigh")
                info["targetLowPrice"]    = _pick(tgt, "targetLow")
                info["targetMedianPrice"] = _pick(tgt, "targetMedian", "targetConsensus")
        except Exception:
            pass
        return info

    def _fetch_news(self, ticker: str, limit: int = 8) -> list:
        """Map FMP stock news into the flat shape data._parse_news_item reads
        (title / publisher / link / providerPublishTime)."""
        try:
            # Some FMP news endpoints require an explicit date window; pass one.
            payload = self._get_json("news/stock", {
                "symbols": ticker, "limit": limit,
                "from": (date.today() - timedelta(days=21)).isoformat(),
                "to":   date.today().isoformat(),
            })
        except Exception:
            return []
        rows = payload if isinstance(payload, list) else (
            payload.get("content") if isinstance(payload, dict) else [])
        out = []
        for r in (rows or [])[:limit]:
            title = r.get("title")
            if not title:
                continue
            ts = 0
            pub = r.get("publishedDate") or r.get("date") or ""
            if pub:
                try:
                    ts = int(datetime.fromisoformat(str(pub).replace("Z", "+00:00")).timestamp())
                except Exception:
                    ts = 0
            out.append({
                "title": title,
                "publisher": r.get("site") or r.get("publisher") or "FMP",
                "link": r.get("url"),
                "providerPublishTime": ts,
            })
        return out

    def _next_earnings(self, ticker: str) -> str | None:
        """Soonest future earnings date as 'YYYY-MM-DD', else None."""
        try:
            payload = self._get_json("earnings", {"symbol": ticker, "limit": 12})
        except Exception:
            return None
        rows = payload if isinstance(payload, list) else []
        # ET, not UTC: Streamlit Cloud runs UTC, and "is this earnings date still
        # in the future" flips by a day at the UTC/ET boundary — a name reporting
        # TODAY (ET) must not be dropped as past because UTC already rolled over.
        today = datetime.now(_ET).date().isoformat()
        future = sorted(str(r.get("date"))[:10] for r in rows
                        if r.get("date") and str(r.get("date"))[:10] >= today)
        return future[0] if future else None

    def _fetch_revisions(self, ticker: str) -> dict:
        """Map FMP analyst grade consensus into the yfinance-shaped revisions
        dict (net upgrades-vs-downgrades is the signal scoring uses). A consensus
        snapshot, not a 90-day delta — a reasonable failover proxy."""
        try:
            d = _first(self._get_json("grades-consensus", {"symbol": ticker}))
        except Exception:
            return {}
        if not d:
            return {}
        def _i(*keys):
            v = _pick(d, *keys)
            try:
                return int(v) if v is not None else 0
            except (TypeError, ValueError):
                return 0
        sb, b = _i("strongBuy"), _i("buy")
        h = _i("hold")
        s, ss = _i("sell"), _i("strongSell")
        ups, downs = sb + b, s + ss
        if (ups + downs + h) == 0:
            return {}
        return {
            "upgrades_90d": ups, "downgrades_90d": downs,
            "maintained_90d": h, "net": ups - downs, "latest": [],
        }
