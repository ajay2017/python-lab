# Plan: Cross-Asset Regime Signals + News Sentiment

**Status:** design approved 2026-06-29. Two additive intelligence features; no existing
gate or threshold is modified. Build in two independent phases (Phase 1 = cross-asset,
Phase 2 = news sentiment) so each ships and can be reviewed before the next starts.

---

## Why these two

Both sit in the highest-ROI tier for a retail portfolio intelligence app:

- **Cross-asset regime**: equity signals are lagging; credit, volatility term structure,
  and commodities often price risk *before* equities move. All the data is free via
  yfinance tickers already in the stack.
- **News sentiment (Finnhub)**: narrative shifts move prices before fundamentals confirm
  them. Finnhub returns a pre-computed sentiment score — no ML to build, Finnhub key
  already in the app.

Neither modifies an existing gate or score in Phase 1. Both are awareness/display layers.

---

## Phase 1 — Cross-Asset Regime Signals

### What it is

Five signals drawn from other asset classes that together describe whether the macro
environment is risk-on or risk-off *beyond* what SPY price + VIX alone capture. Surfaced
as a new "Cross-Asset Pulse" card on the 🔗 Risk Analysis tab and as a one-line note in
Today's Brief when stress is elevated.

### Why NOT expanding `market_risk_posture()`

`market_risk_posture()` carries an explicit design invariant in its docstring:
*"introducing NO new threshold."* That function composes already-computed fragility and
regime inputs; it is not the right place to absorb new data sources. The cross-asset
signals live alongside it as a parallel, independent card — the user sees both, each
clearly labelled, with no silent coupling.

### The five signals

| Signal | Ticker | Computation | Stress condition |
|--------|--------|-------------|-----------------|
| Credit spreads | `HYG` | 20-day linear trend of closing price | Trend negative (HY bonds selling off) |
| VIX term structure | `^VIX` vs `^VIX3M` | Ratio VIX / VIX3M | Ratio > 1.0 (near-term fear > long-term = backwardation) |
| Dollar strength | `DX-Y.NYB` | 20-day linear trend | Trend positive AND 5-day rate-of-change > threshold |
| Copper (growth proxy) | `HG=F` | 20-day linear trend | Trend negative (industrial demand slowing) |
| Yield curve | `^IRX` + `^TNX` | Spread = 10yr yield − 2yr yield (basis points) | Spread < `CROSS_ASSET_CURVE_STRESS_BP` (deeply inverted) |

Score = count of stressed signals (0–5). Displayed as a traffic-light grid; score mapped
to a summary label:

| Score | Label | Brief behaviour |
|-------|-------|----------------|
| 0–1 | Calm | No mention in brief |
| 2 | Caution | Brief: one-liner note |
| 3–4 | Stress | Brief: prominent note |
| 5 | Alarm | Brief: prominent note + links to Risk tab |

### New module: `stock_analyzer/cross_asset.py`

```
fetch_cross_asset_data() → dict[str, pd.DataFrame]
    Batch-fetches the 5 tickers via yfinance.download(period="45d").
    Returns {ticker: df} — empty df on failure (signal degrades to "unknown", not stressed).
    Called once per session; result passed to compute_cross_asset_signals().

compute_cross_asset_signals(data: dict) → dict
    Pure function. Returns:
    {
      "credit":   {"stressed": bool, "label": str, "detail": str},
      "vix_term": {"stressed": bool, "label": str, "detail": str},
      "dollar":   {"stressed": bool, "label": str, "detail": str},
      "copper":   {"stressed": bool, "label": str, "detail": str},
      "curve":    {"stressed": bool, "label": str, "detail": str},
      "score":    int,          # 0–5
      "summary":  str,          # one sentence for brief
      "label":    str,          # "Calm" | "Caution" | "Stress" | "Alarm"
    }
    Degrades gracefully: a signal with missing data is excluded from the score
    (score denominator shrinks) and marked "data unavailable" in its label.
```

### New constants (all in `constants.py`)

```python
CROSS_ASSET_HYG_TREND_DAYS      = 20    # lookback for HYG trend regression
CROSS_ASSET_COPPER_TREND_DAYS   = 20    # lookback for copper trend
CROSS_ASSET_DXY_TREND_DAYS      = 20    # lookback for DXY trend
CROSS_ASSET_DXY_ROC_DAYS        = 5     # short-window rate-of-change for dollar signal
CROSS_ASSET_DXY_ROC_THRESHOLD   = 1.5  # % 5-day change to confirm dollar-rising stress
CROSS_ASSET_VIX_TERM_RATIO      = 1.0  # VIX / VIX3M > this = term-structure inverted
CROSS_ASSET_CURVE_STRESS_BP     = -50  # 10yr−2yr spread in bp; < this = curve stressed
CROSS_ASSET_STRESS_BRIEF_SCORE  = 2    # score >= this → mention in Today's Brief
```

### Session state

Cache key: `_cross_asset_cache`  
Shape: `{"signals": dict, "fetched_at": datetime, "trading_day": date}`  
TTL: refresh when `trading_day` changes (once per calendar day). Stale cache returned
on fetch failure — never blocks app load.

### UI — Risk Analysis tab

New card rendered **below** the existing Market Risk Posture dial (same tab, no
re-ordering of existing surfaces):

```
Cross-Asset Pulse                           [last updated HH:MM ET]

  Credit spreads    ✅  HYG trend flat / rising — no stress
  VIX term struct   ⚠️  VIX above VIX3M — near-term fear elevated
  Dollar (DXY)      ✅  Dollar trend neutral
  Copper            ⚠️  Copper in 20-day downtrend — growth signal weak
  Yield curve       ✅  Spread −28 bp — not deeply inverted

  Overall: Caution (2/5 signals stressed)
```

Traffic-light emoji: ✅ calm, ⚠️ caution, 🔴 stress. Unknown/missing data: `—`.

### Brief integration

Reads `_cross_asset_cache` session key. If `score >= CROSS_ASSET_STRESS_BRIEF_SCORE`:

```
📡 Cross-asset: 2 of 5 signals showing stress (VIX structure inverted, copper weakening).
Check the Risk tab for detail.
```

One line only; never duplicates the Market Risk Posture sentence already in the brief.

### Files touched

| File | Change |
|------|--------|
| `stock_analyzer/cross_asset.py` | **New** — `fetch_cross_asset_data`, `compute_cross_asset_signals` |
| `stock_analyzer/constants.py` | 8 new `CROSS_ASSET_*` constants |
| `app.py` | Fetch at session load → `_cross_asset_cache`; render card on Risk tab; brief integration |

### Out of scope (Phase 1)

- Modifying `market_risk_posture()` or its score
- Using cross-asset score as a gate input
- Historical trend chart of cross-asset score over time
- Email alert on cross-asset stress (deferred to Phase 3 if warranted)

---

## Phase 2 — News Sentiment via Finnhub

### What it is

Per-ticker sentiment score from Finnhub's `/stock/news-sentiment` endpoint. Surfaces as
a row in the Analysis page scorecard, a brief awareness note for held positions showing
a negative shift, and (deferred) a potential small composite-score input.

### Finnhub endpoint

```
GET https://finnhub.io/api/v1/stock/news-sentiment?symbol=AAPL&token=KEY

Response:
{
  "buzz": {
    "articlesInLastWeek": 18,
    "weeklyAverage": 6.2,
    "buzzScore": 0.73          ← this week's articles / 52-week weekly average
  },
  "companyNewsScore": 0.63,    ← 0–1 overall sentiment
  "sectorAverageBullishPercent": 0.48,
  "sectorAverageNewsScore": 0.50,
  "sentiment": {
    "bearishPercent": 0.23,
    "bullishPercent": 0.77     ← PRIMARY signal
  },
  "symbol": "AAPL"
}
```

Key fields used: `sentiment.bullishPercent`, `buzz.buzzScore`, `companyNewsScore`,
`sectorAverageNewsScore` (for vs-sector context).

### New function in `finnhub_provider.py`

```python
def fetch_news_sentiment(ticker: str) -> dict | None:
    """
    Calls /stock/news-sentiment. Returns normalized dict or None on any failure.
    NOT routed through the DataProvider capability chain (this is not a price/bundle
    operation). Called directly by callers that hold the provider instance or key.

    Returns:
    {
      "bullish_pct":   float,   # 0–1  (primary signal)
      "bearish_pct":   float,
      "buzz_score":    float,   # this-week articles / 52-week weekly avg; >1 = elevated
      "company_score": float,   # 0–1  overall company news score
      "sector_score":  float,   # 0–1  sector average (comparison baseline)
      "vs_sector_pp":  float,   # bullish_pct − sector_avg_bullish, in percentage points
      "symbol":        str,
    }
    """
```

### Quota management

Finnhub free tier: 60 calls/minute. One call per ticker per session (held + watchlist).
With ≤ 30 positions + watchlist names: well within budget.

**Fetch scope:**
- **Held positions**: fetched at daily bundle load alongside existing data
- **Watchlist**: fetched at watchlist tab open
- **Analysis page (any ticker)**: fetched lazily when the Analysis scorecard renders;
  cached in session state for the session

**NOT fetched:**
- Scanner results (too many tickers, noisy signal for short-hold candidates)
- Cron (headless path has no display surface for sentiment; skip)

### Session cache

Cache key per ticker: `_news_sentiment_{ticker}_{trading_day}`  
Shape: the normalized dict above + `fetched_at` timestamp.  
TTL: once per trading day. Stale/missing returns `None` → UI degrades gracefully.

### New standalone module: `stock_analyzer/news_sentiment.py`

```
fetch_sentiment_for_tickers(tickers: list[str], finnhub_key: str) → dict[str, dict]
    Loops over tickers, calls FinnhubProvider.fetch_news_sentiment, collects results.
    Skips on failure (partial results fine).
    Returns {ticker: sentiment_dict}.

sentiment_label(bullish_pct: float) → tuple[str, str]
    Returns (label, emoji):
    bullish_pct >= 0.60 → ("Bullish",  "🟢")
    0.40 <= bullish_pct < 0.60 → ("Neutral", "🟡")
    bullish_pct < 0.40 → ("Bearish",  "🔴")

is_sentiment_shift(sentiment_dict: dict) -> bool
    True when the held position shows a bearish shift worth surfacing:
    bullish_pct < NEWS_SENTIMENT_SHIFT_ALERT_BULLISH AND buzz_score > NEWS_SENTIMENT_SHIFT_BUZZ_MIN
    (Active coverage that has turned bearish. Low-buzz bearishness = stale/thin data, skip.)
```

### New constants

```python
NEWS_SENTIMENT_SHIFT_ALERT_BULLISH  = 0.40  # bullish_pct below this = potential shift
NEWS_SENTIMENT_SHIFT_BUZZ_MIN       = 1.0   # buzz_score must exceed this to alert
                                             # (coverage must be above-average to be signal-worthy)
NEWS_SENTIMENT_BULLISH_THRESHOLD    = 0.60  # green label
NEWS_SENTIMENT_BEARISH_THRESHOLD    = 0.40  # red label
```

### UI — Analysis page

New row in the scorecard section, rendered after the technical score block:

```
News Sentiment    🟢 Bullish 72%  |  Buzz 1.4× avg  |  +8 pp vs sector
```

- Shows only when Finnhub key is configured and call succeeded
- Shows `—` (not an error) when data unavailable or ticker unsupported by Finnhub
- Clicking the row (or a small ℹ️) shows a tooltip: what the three numbers mean

### UI — Today's Brief (held positions only)

For each held position where `is_sentiment_shift()` is True, render a compact awareness
card alongside existing deterioration/risk cards:

```
📰 NVDA  News sentiment shifted bearish (38% bullish vs 52% sector avg, 1.8× normal coverage).
         Narrative may be changing — check headlines.
```

Label: "Sentiment Shift" (awareness tone, not action).  
Visible banner, never silent.  
Does NOT trigger a sell or tighten a stop — awareness only.

### Composite score — deferred to Phase 2b (policy decision)

Adding news sentiment to the composite score is an investment-policy decision (changes
what the engine recommends). Deferred. When ready to discuss:

- Weight candidate: `NEWS_SENTIMENT_WEIGHT = 0.05` (5%)
- Application: only when `buzz_score > NEWS_SENTIMENT_SHIFT_BUZZ_MIN` (thin coverage
  shouldn't penalise a score)
- When unavailable: remaining weights re-normalise (no phantom 50-neutral fill)
- Requires: Opus review of the full composite formula impact before shipping

### Files touched

| File | Change |
|------|--------|
| `stock_analyzer/providers/finnhub_provider.py` | Add `fetch_news_sentiment(ticker)` method |
| `stock_analyzer/news_sentiment.py` | **New** — helpers: `fetch_sentiment_for_tickers`, `sentiment_label`, `is_sentiment_shift` |
| `stock_analyzer/constants.py` | 4 new `NEWS_SENTIMENT_*` constants |
| `app.py` | Session-load fetch for held+watchlist; scorecard row on Analysis; brief awareness card |

### Out of scope (Phase 2 Phase 1)

- Baking sentiment into composite score (Phase 2b, policy decision)
- Sentiment history / trend chart over time
- Multi-source sentiment (Alpha Vantage, FMP news scoring)
- Cron email for sentiment shifts (consider Phase 3 if shift alerts prove valuable)

---

## Sequencing

```
Phase 1 — Cross-Asset Regime Signals
  Step 1  constants.py — 8 new CROSS_ASSET_* constants
  Step 2  cross_asset.py — fetch + compute functions (pure, testable in isolation)
  Step 3  app.py — session load cache + Risk tab card
  Step 4  app.py — brief integration

Phase 2 — News Sentiment
  Step 1  constants.py — 4 new NEWS_SENTIMENT_* constants
  Step 2  finnhub_provider.py — fetch_news_sentiment() method
  Step 3  news_sentiment.py — helpers module
  Step 4  app.py — session load fetch + Analysis scorecard row + brief awareness card

Phase 2b (later, policy discussion required)
  — Composite score integration (NEWS_SENTIMENT_WEIGHT)
  — Requires user approval of weight + Opus review of scoring impact
```

Each step is independently reviewable. Phase 1 ships before Phase 2 starts.

---

## Shared invariants (both features)

1. **Strictly additive.** Missing data, missing key, API failure → `None` / degraded
   label. No other page or gate is affected.
2. **No new gates.** Neither feature gates a recommendation or issues a buy/sell.
   Both surface awareness; the engine's rules stay unchanged.
3. **All thresholds in `constants.py`.** No magic numbers in module code.
4. **Single-surface rule.** Cross-asset summary goes on the Risk tab; brief gets one
   line. Sentiment shift goes in the brief card; Analysis gets the score row. No
   duplication across surfaces.
5. **Degradation is visible, not silent.** When a signal is unavailable, the UI shows
   `—` or `data unavailable` — never hides the row or renders a fabricated neutral.
