import numpy as np
import pandas as pd
from stock_analyzer.indicators import atr as _atr_series
from stock_analyzer.constants import ATR_STOP_MULT

# A flat/no-volatility excess-return series (returns - a risk-free daily
# rate derived from a non-terminating binary fraction, e.g. 0.045/252)
# should have std() == exactly 0.0, but summing/averaging that repeating
# fraction across many identical rows can leave ~1e-19-scale floating-point
# noise instead. An exact `== 0` check misses that noise and then divides
# by it, blowing the ratio up to +/-quadrillions instead of the intended
# "no volatility -> no signal" 0.0 fallback. Any REAL volatility signal is
# many orders of magnitude above this floor (daily vol ~1e-3 to 1e-1), so
# this tolerance can never suppress a genuine result.
_ZERO_VOL_EPS = 1e-9


def _atr_value(df: pd.DataFrame, length: int = 14) -> float:
    s = _atr_series(df["High"], df["Low"], df["Close"], length).dropna()
    if s.empty:
        return float((df["High"] - df["Low"]).tail(length).mean())
    return float(s.iloc[-1])


def atr_stop_loss(df: pd.DataFrame, multiplier: float = ATR_STOP_MULT) -> tuple[float, float]:
    """Returns (stop_loss_price, atr_value)."""
    atr_val = _atr_value(df)
    current_price = float(df["Close"].iloc[-1])
    stop = round(current_price - multiplier * atr_val, 2)
    return stop, round(atr_val, 2)


def sizing_unavailable_reason(
    portfolio_value: float, entry: float, stop: float,
    max_position_pct: float | None = None,
) -> str | None:
    """Why `position_sizing` would decline to size, or None if it would size.

    `position_sizing` returns None for two STRUCTURALLY DIFFERENT reasons, and a
    caller that conflates them tells the user to fix the wrong thing:

      "stop"    — degenerate stop (entry <= stop, or stop <= 0). A data problem;
                  the user can inspect or reset the stop.
      "ceiling" — the single-name cap cannot afford one whole share
                  (`price > portfolio_value * max_position_pct%`). An ACCOUNT-SIZE
                  constraint; no change to the stop will ever fix it. Before this
                  helper existed, the Analysis and Watchlist fallback captions
                  said "stop price too close to entry or not set" for this case,
                  while rendering a perfectly healthy 2xATR stop directly above.

    Kept as the single predicate for both conditions so `position_sizing`, the
    Grow Today adapter and the fallback captions cannot drift apart. Do NOT
    re-derive either test at a call site.
    """
    # Public helper called from render branches that are reached BECAUSE values
    # are missing, so coerce rather than trusting the caller: a None entry would
    # raise TypeError and a NaN would blow up int() further down. Both are
    # "no usable stop" answers, not crashes.
    def _num(x) -> float:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return 0.0
        return v if v == v else 0.0  # v != v filters NaN

    portfolio_value, entry, stop = _num(portfolio_value), _num(entry), _num(stop)
    if entry <= 0 or stop <= 0 or entry <= stop:
        return "stop"
    if (max_position_pct is not None and portfolio_value > 0
            and int((portfolio_value * (_num(max_position_pct) / 100.0)) / entry) < 1):
        return "ceiling"
    return None


def position_sizing(
    portfolio_value: float, risk_pct: float, entry: float, stop: float,
    max_position_pct: float | None = None,
) -> dict | None:
    # Both no-size conditions live in one place — see sizing_unavailable_reason.
    if sizing_unavailable_reason(portfolio_value, entry, stop, max_position_pct):
        return None
    risk_dollars   = portfolio_value * risk_pct
    risk_per_share = entry - stop
    risk_based_shares = max(1, int(risk_dollars / risk_per_share))

    # Single-name concentration cap. With a tight stop the risk-budget math can
    # balloon the DOLLAR position well past the single-name ceiling (e.g. a 3%
    # stop on a high-priced name → 40%+ of the book). Never SUGGEST a size that
    # breaches the ceiling the rest of the app enforces; cap it and flag, so the
    # UI can show the capped figure plus what the uncapped size would have been.
    shares = risk_based_shares
    ceiling_capped = False
    if max_position_pct is not None and portfolio_value > 0 and entry > 0:
        # Guaranteed >= 1 by the "ceiling" guard above, so no max(1, ...) floor
        # is needed here. That floor used to force one share even when it
        # breached the cap AND left ceiling_capped False (risk_based_shares was
        # also 1, so `shares > ceiling_shares` never fired) — a $4,500 name on a
        # $10,000 book printed 1 share = 45% of portfolio, silently.
        ceiling_shares = int((portfolio_value * (max_position_pct / 100.0)) / entry)
        if shares > ceiling_shares:
            shares = ceiling_shares
            ceiling_capped = True

    total_cost  = round(shares * entry, 2)
    actual_risk = round(shares * risk_per_share, 2)
    out = {
        "shares": shares,
        "risk_budget": round(risk_dollars, 2),
        "actual_risk": actual_risk,
        "risk_per_share": round(risk_per_share, 2),
        "total_cost": total_cost,
        "portfolio_pct": round(total_cost / portfolio_value * 100, 1) if portfolio_value else 0.0,
        "risk_pct_actual": round(actual_risk / portfolio_value * 100, 2) if portfolio_value else 0.0,
    }
    if max_position_pct is not None:
        out["ceiling_pct"]     = max_position_pct
        out["ceiling_capped"]  = ceiling_capped
        out["uncapped_shares"] = risk_based_shares
        out["uncapped_pct"]    = round(risk_based_shares * entry / portfolio_value * 100, 1) if portfolio_value else 0.0
    return out


def sharpe_ratio(df: pd.DataFrame, risk_free_annual: float = 0.045) -> float:
    returns = df["Close"].pct_change().dropna()
    rf_daily = risk_free_annual / 252
    excess = returns - rf_daily
    std = excess.std()
    if abs(std) < _ZERO_VOL_EPS or np.isnan(std):
        return 0.0
    return round(float((excess.mean() / std) * np.sqrt(252)), 2)


def sortino_ratio(df: pd.DataFrame, risk_free_annual: float = 0.045) -> float:
    returns = df["Close"].pct_change().dropna()
    rf_daily = risk_free_annual / 252
    excess = returns - rf_daily
    downside = excess[excess < 0]
    if downside.empty:
        # No negative excess-return days: strong uptrend — Sortino is excellent, not zero.
        return 99.0 if excess.mean() > 0 else 0.0
    downside_std = downside.std()
    if abs(downside_std) < _ZERO_VOL_EPS or np.isnan(downside_std):
        return 0.0
    return round(float((excess.mean() / downside_std) * np.sqrt(252)), 2)


def max_drawdown_pct(df: pd.DataFrame) -> float:
    prices = df["Close"]
    rolling_max = prices.cummax()
    dd = (prices - rolling_max) / rolling_max
    return round(float(dd.min() * 100), 1)


def var_95_daily(df: pd.DataFrame) -> float:
    """One-day 95% VaR as % of position value (negative = loss)."""
    returns = df["Close"].pct_change().dropna()
    return round(float(np.percentile(returns, 5) * 100), 2)


def beta_vs_market(df: pd.DataFrame, market_df: pd.DataFrame) -> float | None:
    stock_ret = df["Close"].pct_change().dropna()
    mkt_ret = market_df["Close"].pct_change().dropna()
    combined = pd.concat([stock_ret, mkt_ret], axis=1, keys=["s", "m"]).dropna()
    if len(combined) < 20:
        return None
    cov = combined.cov().loc["s", "m"]
    mkt_var = combined["m"].var()
    if mkt_var == 0:
        return None
    return round(float(cov / mkt_var), 2)


def pearson_corr_vs_benchmark(df: pd.DataFrame, benchmark_df: pd.DataFrame) -> float | None:
    """Pearson correlation of daily returns vs a benchmark (e.g. TLT).

    Distinct from beta_vs_market: beta is the regression slope (sensitivity in $),
    correlation is the −1..+1 co-movement coefficient (direction + strength).
    Both require ≥20 overlapping trading days; returns None when unavailable.
    """
    stock_ret = df["Close"].pct_change().dropna()
    bench_ret = benchmark_df["Close"].pct_change().dropna()
    combined = pd.concat([stock_ret, bench_ret], axis=1, keys=["s", "b"]).dropna()
    if len(combined) < 20:
        return None
    corr = combined["s"].corr(combined["b"])
    if corr != corr:   # NaN guard
        return None
    return round(float(corr), 3)


def rate_sensitivity_per_ticker(
    port_df: pd.DataFrame,
    held_data: dict,
    tlt_df: pd.DataFrame | None,
) -> list[dict]:
    """
    Per-holding rate sensitivity table combining:
      - Sector-level score from macro.RATE_SENSITIVITY (−1..+1 label)
      - Computed Pearson correlation vs TLT daily returns (−1..+1, live data)

    TLT falls when long rates rise; a negative correlation with TLT means the
    holding tends to DROP when rates rise (rate-sensitive / long-duration).
    A positive correlation means it tends to RISE with rates (rate beneficiary).

    Returns list[dict] sorted by tlt_corr ascending (most rate-sensitive first).
    Rows with no TLT data still appear using the sector score only.

    Third case, added 2026-08-16: a row with NEITHER a correlation NOR a
    structural label reports `Sector Score = None` and an "Unknown" implication,
    and sorts LAST. It is never given a 0.0 stand-in — that rendered as a
    confident "+0.00" / "Roughly rate-neutral" indistinguishable from a real
    finding. Unknown is not neutral, so it must not sort mid-table either.
    """
    from stock_analyzer.macro import RATE_SENSITIVITY  # avoid circular import at module level

    rows = []
    for _, pos in port_df.iterrows():
        ticker = str(pos.get("Ticker", ""))
        # `or ""` not a default of "Other": UNCLASSIFIED_SECTOR ("Other") IS a
        # real RATE_SENSITIVITY key worth 0.00, so defaulting a MISSING or blank
        # column to it would silently inherit that deliberate policy value for a
        # position whose sector simply wasn't supplied. Blank resolves to no key
        # → None → "Unknown", which is what it actually is.
        sector = str(pos.get("Sector") or "")
        weight = float(pos.get("Weight (%)", 0) or 0)
        # None, NOT 0.0, when the sector has no structural label. A default of
        # 0.0 renders as a confident "+0.00" — indistinguishable from a real
        # "structurally rate-neutral" reading — when the truth is "we have no
        # label for this sector". Same fabricated-neutral class the fundamentals
        # gate rejects. Reachable whenever a HELD ticker has no TICKER_SECTORS
        # entry and falls back to the raw provider GICS string ("Basic
        # Materials", "Technology"), and for the curated labels RATE_SENSITIVITY
        # does not yet cover (Industrials, Communications,
        # Consumer Staples & Retail).
        sector_score = RATE_SENSITIVITY.get(sector)

        tlt_corr = None
        if tlt_df is not None and not tlt_df.empty:
            data = (held_data.get(ticker) or {})
            df   = data.get("df")
            if df is not None and not df.empty and "Close" in df.columns:
                tlt_corr = pearson_corr_vs_benchmark(df, tlt_df)

        # Human-readable implication driven by the sector score (structural) and
        # TLT correlation (empirical).  Use TLT corr when available; fall back to sector score.
        primary = tlt_corr if tlt_corr is not None else sector_score
        if primary is None:
            # Neither an empirical correlation nor a structural label — say so
            # rather than printing "Roughly rate-neutral", which would be a
            # confident claim derived from no data whatsoever.
            implication = "Unknown — no rate data and no sector label"
        elif primary < -0.4:
            implication = "Rate-sensitive — hurt when rates rise"
        elif primary < -0.1:
            implication = "Mild rate headwind"
        elif primary < 0.1:
            implication = "Roughly rate-neutral"
        elif primary < 0.4:
            implication = "Mild rate tailwind"
        else:
            implication = "Rate beneficiary — helped when rates rise"

        rows.append({
            "Ticker":         ticker,
            "Sector":         sector,
            "Weight (%)":     round(weight, 1),
            "Sector Score":   sector_score,
            "TLT Corr":       tlt_corr,
            "Implication":    implication,
        })

    # Most rate-sensitive first. A row with neither an empirical correlation nor
    # a structural label sorts LAST rather than raising: the pre-2026-08-16 key
    # would compare None against a float once Sector Score stopped defaulting to
    # 0.0. Unknown is not "neutral", so it must not land mid-table where it would
    # read as one.
    def _sort_key(r: dict) -> tuple[int, float]:
        primary = r["TLT Corr"] if r["TLT Corr"] is not None else r["Sector Score"]
        return (1, 0.0) if primary is None else (0, float(primary))

    rows.sort(key=_sort_key)
    return rows


def compute_all_risk(
    df: pd.DataFrame,
    spy_df: pd.DataFrame | None = None,
    risk_free_rate: float = 0.045,
) -> dict:
    return {
        "sharpe": sharpe_ratio(df, risk_free_rate),
        "sortino": sortino_ratio(df, risk_free_rate),
        "max_drawdown": max_drawdown_pct(df),
        "var_95": var_95_daily(df),
        "beta": beta_vs_market(df, spy_df) if spy_df is not None else None,
    }


def compute_portfolio_risk_metrics(
    port_df: pd.DataFrame,
    held_data: dict,
    spy_df: pd.DataFrame | None = None,
    risk_free_annual: float = 0.045,
) -> dict | None:
    """
    Portfolio-level risk metrics from weighted daily returns.
    Returns Beta, Ann. Volatility, Sharpe, Sortino, VaR 95%, CVaR, Max Drawdown,
    plus drawdown_series and cum_returns Series for charting.
    Returns None if insufficient data — an offline sentinel, not {}, so
    callers can distinguish "couldn't compute" from "computed, zero risk"
    (2026-08-04 audit finding: a bare {} here was silently read downstream
    as "no risk," suppressing risk alerts instead of flagging them offline).
    """
    series: dict[str, pd.Series] = {}
    for ticker, data in held_data.items():
        hist = data.get("df") if data.get("df") is not None else data.get("history")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].dropna().copy()
            if closes.index.tz is not None:
                closes.index = closes.index.tz_localize(None)
            if len(closes) >= 10:
                series[ticker] = closes

    if not series:
        return None

    prices = pd.DataFrame(series).ffill().dropna()
    if len(prices) < 10:
        return None

    daily_returns = prices.pct_change().dropna()

    weights: dict[str, float] = {}
    for _, row in port_df.iterrows():
        t = row["Ticker"]
        if t in daily_returns.columns:
            weights[t] = float(row["Weight (%)"]) / 100.0

    if not weights:
        return None

    total_w = sum(weights.values())
    if total_w == 0:
        return None
    weights = {t: w / total_w for t, w in weights.items()}

    port_returns = pd.Series(0.0, index=daily_returns.index)
    for t, w in weights.items():
        port_returns += daily_returns[t] * w

    std_ret = port_returns.std()
    rf_daily = risk_free_annual / 252
    excess = port_returns - rf_daily

    sharpe = round(float((excess.mean() / std_ret) * np.sqrt(252)), 2) if std_ret > 0 else 0.0

    downside_std = excess[excess < 0].std()  # downside deviation on excess returns (standard Sortino)
    sortino = (
        round(float((excess.mean() / downside_std) * np.sqrt(252)), 2)
        if (downside_std and not np.isnan(downside_std) and downside_std > _ZERO_VOL_EPS)
        else 0.0
    )

    ann_vol = round(float(std_ret * np.sqrt(252) * 100), 1)

    var_pct = round(float(np.percentile(port_returns, 5) * 100), 2)
    threshold = np.percentile(port_returns, 5)
    bad_days = port_returns[port_returns <= threshold]
    cvar_pct = round(float(bad_days.mean() * 100), 2) if len(bad_days) > 0 else var_pct

    cum_ret = (1 + port_returns).cumprod()
    rolling_max = cum_ret.cummax()
    drawdown_series = (cum_ret - rolling_max) / rolling_max * 100
    max_dd = round(float(drawdown_series.min()), 1)

    beta = None
    if spy_df is not None and not spy_df.empty and "Close" in spy_df.columns:
        spy_ret = spy_df["Close"].pct_change().dropna().copy()
        if spy_ret.index.tz is not None:
            spy_ret.index = spy_ret.index.tz_localize(None)
        combined = pd.concat([port_returns, spy_ret], axis=1, keys=["port", "spy"]).dropna()
        if len(combined) >= 20:
            cov_val = combined.cov().loc["port", "spy"]
            mkt_var = combined["spy"].var()
            if mkt_var > 0:
                beta = round(float(cov_val / mkt_var), 2)

    return {
        "beta":            beta,
        "ann_volatility":  ann_vol,
        "sharpe":          sharpe,
        "sortino":         sortino,
        "var_95_pct":      var_pct,
        "cvar_95_pct":     cvar_pct,
        "max_drawdown":    max_dd,
        "drawdown_series": drawdown_series,
        "cum_returns":     cum_ret,
    }
