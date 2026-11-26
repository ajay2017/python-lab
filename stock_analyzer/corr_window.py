"""The correlation matrix and its coverage, with young holdings left out and named.

Why this exists (CorrYoung, docs/plans/corr-young-holding.md): the old builder
(portfolio.correlation_matrix) cut the price frame listwise, so ONE holding with
four closes measured all 55 pairs of an 11-name book on four shared days, and a
chance 0.99 read as "these positions move together". The decision here:

  * A holding is COUNTED when it has >= min_returns real daily returns
    (risk_window.classify_holdings, the same rule as the book return series; the
    value is CORR_MIN_OBS_TRUSTED, equal to BETA_MIN_RETURNS today). A younger
    holding ("short_history") and a held name with no usable prices
    ("no_prices") are LEFT OUT: they are not matrix columns, so
    portfolio.correlation_unchecked already names them.
  * Each pair of counted holdings is measured on ITS OWN shared session dates
    (no listwise cut across the book), so a counted name with 25 returns pairs
    on 25 while the other pairs keep their long window. An aligned book (every
    counted holding on the same dates) gives exactly the old listwise matrix.
  * n_obs of the coverage = the FEWEST shared returns behind any measured pair of
    counted holdings (the number the sample-size gates compare to the floor).

Pure: pandas / numpy / constants only. No clock (it counts the rows in the frames
it is given), no streamlit, no db. Never raises: bad input reads as the offline
contract (empty matrix, coverage None).
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd

from stock_analyzer.constants import CORR_MIN_OBS_TRUSTED
from stock_analyzer.risk_window import _weight_pct, classify_holdings


def _split(held_data, min_returns):
    """(counted closes, left_out, usable lengths) for a held_data dict.
    `usable` = every holding with some usable Close (counted + short_history),
    in held_data order: the inputs the old builder worked from."""
    if not isinstance(held_data, dict):
        return {}, [], {}
    counted, left_out = classify_holdings(held_data, list(held_data), min_returns=min_returns)
    short = {x["ticker"]: x["n_returns"] for x in left_out if x["reason"] == "short_history"}
    lengths = {}
    for t in held_data:
        if t in counted:
            lengths[t] = len(counted[t])
        elif t in short:
            lengths[t] = short[t] + 1   # n_returns = len(closes) - 1
    return counted, left_out, lengths


def _aligned(counted: dict) -> bool:
    idxs = list(counted.values())
    first = idxs[0].index
    return all(s.index.equals(first) for s in idxs[1:])


def _pair_returns(a: pd.Series, b: pd.Series):
    """Returns of two close series on THEIR shared session dates: (ra, rb)."""
    shared = a.index.intersection(b.index)
    if len(shared) < 2:
        return None
    pa, pb = a.loc[shared], b.loc[shared]
    return pa.pct_change().iloc[1:], pb.pct_change().iloc[1:]


def _corr(x: pd.Series, y: pd.Series) -> float:
    with np.errstate(all="ignore"):
        v = x.corr(y)
    return float(v) if v is not None and math.isfinite(float(v)) else float("nan")


def _matrix(counted: dict) -> pd.DataFrame:
    names = list(counted)
    if _aligned(counted):
        # Every counted holding on the same dates: the old listwise builder
        # exactly (same calls), so an aligned book changes by not a digit.
        prices = pd.DataFrame(counted)
        returns = prices.pct_change().dropna()
        return returns.corr().round(3)
    out = pd.DataFrame(float("nan"), index=names, columns=names)
    own = {t: counted[t].pct_change().iloc[1:] for t in names}
    for t in names:
        out.loc[t, t] = _corr(own[t], own[t])   # NaN = zero variance (flat), as before
    for a, b in itertools.combinations(names, 2):
        pr = _pair_returns(counted[a], counted[b])
        v = float("nan") if pr is None else _corr(pr[0], pr[1])
        out.loc[a, b] = v
        out.loc[b, a] = v
    return out.round(3)


def corr_matrix(held_data, *, min_returns: int = CORR_MIN_OBS_TRUSTED) -> pd.DataFrame:
    """Daily-return correlation matrix of the COUNTED holdings (see module doc).
    Empty DataFrame when fewer than 2 holdings have usable prices, or fewer than
    2 are counted (nothing to correlate). Never raises."""
    try:
        counted, _left_out, lengths = _split(held_data, min_returns)
        if len(lengths) < 2 or len(counted) < 2:
            return pd.DataFrame()
        return _matrix(counted)
    except Exception:
        return pd.DataFrame()


def corr_coverage(
    held_data,
    *,
    min_returns: int = CORR_MIN_OBS_TRUSTED,
    weights=None,
) -> dict | None:
    """How much data `corr_matrix` had to work with.

    None when fewer than 2 holdings have usable prices (the offline contract). Else:
      n_tickers, shortest_ticker, shortest_len, longest_len, lengths
          -- the INPUTS (every holding with usable prices, counted or not), as before;
      n_obs      -- the FEWEST shared daily returns behind any measured pair of
                    counted holdings (0 when fewer than 2 are counted, or two
                    counted names share no sessions); equals the old listwise
                    count on an aligned book with no young holding;
      n_obs_max  -- the MOST shared returns behind any such pair (0 likewise);
      min_returns -- the per-holding floor used;
      left_out   -- [{"ticker", "reason": "short_history" | "no_prices",
                      "n_returns"}] (+ "weight_pct", None when unknown, only when
                      `weights` is a {ticker: Weight %} mapping).
    Never raises (an exception reads as None)."""
    try:
        counted, left_out, lengths = _split(held_data, min_returns)
        if len(lengths) < 2:
            return None
        if weights is not None:
            w = weights if isinstance(weights, dict) else {}
            left_out = [dict(x, weight_pct=_weight_pct(w.get(x["ticker"]))) for x in left_out]
        counts = []
        for a, b in itertools.combinations(list(counted), 2):
            shared = counted[a].index.intersection(counted[b].index)
            counts.append(max(0, len(shared) - 1))   # pct_change drops the first bar
        shortest = min(lengths, key=lambda t: lengths[t])
        return {
            "n_tickers": len(lengths),
            "n_obs": min(counts) if counts else 0,
            "n_obs_max": max(counts) if counts else 0,
            "shortest_ticker": shortest,
            "shortest_len": lengths[shortest],
            "longest_len": max(lengths.values()),
            "lengths": lengths,
            "min_returns": min_returns,
            "left_out": left_out,
        }
    except Exception:
        return None
